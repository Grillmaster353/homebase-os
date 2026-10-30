"""Small, auditable security primitives for the local Home Command service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import struct
import threading
import time
from urllib.parse import quote

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError as exc:
    raise SystemExit("Home Command needs the Python package 'cryptography'. Use the Docker deployment, or install requirements.txt in a project virtual environment.") from exc


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


class Vault:
    """AES-256-GCM storage with an independent, owner-readable local key."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.lock = threading.RLock()
        key_path = self.directory / "master.key"
        if not key_path.exists():
            if (self.directory / "vault.enc").exists():
                raise RuntimeError("The vault encryption key is missing; restore the key with its matching vault backup.")
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(AESGCM.generate_key(bit_length=256))
            except FileExistsError:
                pass
        os.chmod(key_path, 0o600)
        key = key_path.read_bytes()
        if len(key) != 32:
            raise RuntimeError("The vault encryption key has an invalid length.")
        self.cipher = AESGCM(key)
        self._key_digest = hashlib.sha256(key).digest()
        self.path = self.directory / "vault.enc"
        self._verified_signature = None
        self.data = self._load()

    def _file_signature(self):
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"account": None, "integrations": [], "settings": {"ai": {"provider": "ollama", "baseUrl": "http://127.0.0.1:11434", "model": "qwen3:4b"}, "workspace": str(self.directory.parent / "workspace"), "workspaceName": "My home", "networkIdentity": {"ispName": "", "routerName": "", "allowExternalLookup": False}}, "actions": [], "events": []}
        try:
            encrypted = self.path.read_bytes()
            plaintext = self.cipher.decrypt(encrypted[:12], encrypted[12:], b"home-command-v1")
            value = json.loads(plaintext)
            self._verified_signature = self._file_signature()
            return value
        except Exception as exc:
            raise RuntimeError("Cannot unlock the encrypted vault. Restore its matching master.key and vault.enc files.") from exc

    def save(self) -> None:
        with self.lock:
            nonce = secrets.token_bytes(12)
            plaintext = json.dumps(self.data, separators=(",", ":")).encode()
            atomic_write(self.path, nonce + self.cipher.encrypt(nonce, plaintext, b"home-command-v1"))
            self._verified_signature = self._file_signature()

    def security_status(self) -> dict:
        """Report only security properties this process can actually verify.

        AES-GCM integrity is verified on load/save; a stat signature detects
        later file changes without repeatedly reading a potentially large vault.
        POSIX mode/owner checks are useful on Mac/Linux but do not assess ACLs.
        """
        with self.lock:
            key_path = self.directory / "master.key"
            try:
                key_info = key_path.lstat()
                if not stat.S_ISREG(key_info.st_mode):
                    raise OSError("Encryption key is not a regular file.")
                with key_path.open("rb") as stream:
                    key_bytes = stream.read(33)
                key_unchanged = len(key_bytes) == 32 and hmac.compare_digest(hashlib.sha256(key_bytes).digest(), self._key_digest)
                signature = self._file_signature()
                verified = key_unchanged and signature is not None and signature == self._verified_signature
                state = "verified" if verified else "modified-since-verification"
                if not key_unchanged:
                    state = "key-changed"
            except FileNotFoundError:
                verified, state = False, "not-initialized" if not self.path.exists() else "key-missing"
            except OSError:
                verified, state = False, "unavailable"
            key_restricted = vault_restricted = None
            if os.name == "posix":
                def private_owner(path, directory=False):
                    try:
                        info = path.lstat()
                        expected_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
                        return expected_kind and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0
                    except OSError:
                        return False
                key_restricted = private_owner(key_path) and private_owner(self.directory, directory=True)
                vault_restricted = private_owner(self.path)
            return {"encryptedStorage": verified, "encryptedStorageState": state, "keyPermissionsRestricted": key_restricted, "vaultPermissionsRestricted": vault_restricted, "keyPermissionsCheck": "posix-owner-mode" if os.name == "posix" else "unsupported", "keyPermissionsLimitations": "Checks ownership and group/other POSIX mode bits; ACLs and administrator access are not assessed."}

    def event(self, title: str, detail: str = "", kind: str = "info") -> dict:
        with self.lock:
            event = {"id": secrets.token_hex(8), "title": title[:180], "detail": detail[:300], "kind": kind, "time": time.time(), "timestamp": time.time()}
            self.data["events"].insert(0, event)
            self.data["events"] = self.data["events"][:300]
            self.save()
            return event


def password_hash(password: str, salt: str | None = None) -> dict:
    if not isinstance(password, str) or not 14 <= len(password) <= 1024:
        raise ValueError("Use a unique password with at least 14 characters (maximum 1024).")
    raw_salt = base64.b64decode(salt) if salt else secrets.token_bytes(24)
    value = hashlib.scrypt(password.encode(), salt=raw_salt, n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return {"salt": base64.b64encode(raw_salt).decode(), "hash": base64.b64encode(value).decode(), "algorithm": "scrypt-32768-8-1"}


def verify_password(password: str, record: dict) -> bool:
    if not isinstance(password, str) or len(password) > 1024:
        return False
    try:
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(record["salt"]), n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(digest, base64.b64decode(record["hash"]))
    except (KeyError, ValueError, TypeError):
        return False


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, at: float | None = None) -> str:
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    counter = struct.pack(">Q", int((time.time() if at is None else at) // 30))
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def verify_totp(secret: str, code: str, last_counter: int = -1) -> int | None:
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return None
    current = int(time.time() // 30)
    for offset in (-1, 0, 1):
        counter = current + offset
        if counter > last_counter and hmac.compare_digest(totp(secret, counter * 30), code):
            return counter
    return None


def totp_uri(secret: str, username: str) -> str:
    return f"otpauth://totp/Home%20Command:{quote(username, safe='')}?secret={secret}&issuer=Home%20Command&algorithm=SHA1&digits=6&period=30"


class Sessions:
    def __init__(self):
        self.lock = threading.RLock()
        self.records: dict[str, dict] = {}

    def create(self, username: str) -> tuple[str, dict]:
        with self.lock:
            now = time.time()
            self.records = {key: value for key, value in self.records.items() if now - value["created"] <= 28800 and now - value["seen"] <= 1800}
            token = secrets.token_urlsafe(48)
            record = {"user": username, "csrf": secrets.token_urlsafe(32), "created": now, "seen": now}
            self.records[hashlib.sha256(token.encode()).hexdigest()] = record
            return token, record

    def get(self, token: str) -> dict | None:
        with self.lock:
            key = hashlib.sha256(token.encode()).hexdigest()
            record = self.records.get(key)
            now = time.time()
            if not record or now - record["created"] > 28800 or now - record["seen"] > 1800:
                self.records.pop(key, None)
                return None
            record["seen"] = now
            return record.copy()

    def revoke_all(self) -> None:
        with self.lock:
            self.records.clear()

    def delete(self, token: str) -> None:
        with self.lock:
            self.records.pop(hashlib.sha256(token.encode()).hexdigest(), None)


class RateLimiter:
    def __init__(self, limit: int = 5, window: int = 300):
        self.limit, self.window = limit, window
        self.entries: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def permit(self, key: str) -> bool:
        with self.lock:
            now = time.time()
            recent = [stamp for stamp in self.entries.get(key, []) if now - stamp < self.window]
            self.entries[key] = recent
            if len(recent) >= self.limit:
                return False
            recent.append(now)
            return True

    def clear(self, key: str) -> None:
        with self.lock:
            self.entries.pop(key, None)


SECRET_FIELDS = {"password", "token", "apikey", "api_key", "secret", "clientkey", "client_key", "pairingtoken", "authorization", "mfasecret", "mfapending", "tvcredentials", "vidaaprofile"}


def redact(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.lower() in SECRET_FIELDS:
                result["has" + key[0].upper() + key[1:]] = bool(item)
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
