"""Authenticated local HTTP service. Start with ``python -m app.server``."""

from __future__ import annotations

import argparse
import copy
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
import unicodedata
from urllib.parse import unquote, urlsplit

from . import __version__
from .lan import MDNSAdvertiser, get_lan_name, is_local_peer
from .network_identity import DEFAULT_SETTINGS as IDENTITY_DEFAULTS, NetworkIdentity, validate_settings as validate_identity_settings
from .security import RateLimiter, Sessions, Vault, new_totp_secret, password_hash, redact, totp_uri, verify_password, verify_totp


class APIError(Exception):
    def __init__(self, status: int, message: str, code: str = "request_failed"):
        self.status, self.message, self.code = status, message, code


class Application:
    def __init__(self, data_dir: str | Path, web_root: str | Path | None = None, monitor=None):
        if monitor is None:
            from .monitor import Monitor
            monitor = Monitor()
        self.vault = Vault(data_dir)
        self.sessions = Sessions()
        self.auth_limiter = RateLimiter(5, 300)
        self.chat_limiter = RateLimiter(25, 60)
        self.identity_limiter = RateLimiter(5, 60)
        self.pair_limiter = RateLimiter(12, 60)
        self.pair_slots = threading.BoundedSemaphore(2)
        self.tv_manual_limiter = RateLimiter(120, 60)
        self.tv_manual_slots = threading.BoundedSemaphore(2)
        self.tv_manual_locks = {}
        self.tv_manual_lock_guard = threading.Lock()
        self.chat_slots = threading.BoundedSemaphore(2)
        self.approved_reads: dict[str, dict] = {}
        self.monitor = monitor
        self.web_root = Path(web_root or Path(__file__).resolve().parent.parent / "web").resolve()
        self.secure = False
        self.lan = {"enabled": False, "hostname": None, "url": None, "addresses": [], "port": 8420, "advertising": False, "advertisementState": "disabled", "advertisementError": None, "autoDiscover": False}
        self.allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        self.port = 8420
        self.vault.data["settings"].setdefault("workspace", str(self.vault.directory.parent / "workspace"))
        self.vault.data["settings"].setdefault("workspaceName", "My home")
        self.vault.data["settings"].setdefault("networkIdentity", copy.deepcopy(IDENTITY_DEFAULTS))
        Path(self.vault.data["settings"]["workspace"]).mkdir(parents=True, exist_ok=True)
        self.network_identity = NetworkIdentity(self.identity_settings, self.monitor.overview)

    def identity_settings(self):
        with self.vault.lock:
            return copy.deepcopy(self.vault.data["settings"].get("networkIdentity", IDENTITY_DEFAULTS))

    def overview(self) -> dict:
        result = self.monitor.overview()
        with self.vault.lock:
            result.update(integrations=[self.public_integration(i) for i in self.vault.data["integrations"]], events=copy.deepcopy(self.vault.data["events"][:30]), actions=copy.deepcopy(self.vault.data["actions"][-50:]), lan=copy.deepcopy(self.lan))
        result["networkIdentity"] = self.network_identity.snapshot(result)
        result["security"] = self.security_status()
        return result

    def security_status(self) -> dict:
        with self.vault.lock:
            account = self.vault.data.get("account") or {}
            password_record = account.get("password")
            return {**self.vault.security_status(), "accountConfigured": bool(account.get("username") and isinstance(password_record, dict) and password_record.get("hash")), "approvalRequired": True, "mfaEnabled": bool(account.get("mfaSecret")), "sessionIdleMinutes": 30, "sessionMaxHours": 8, "https": self.secure, "lanEnabled": bool(self.lan["enabled"])}

    def settings(self) -> dict:
        with self.vault.lock:
            value = redact(copy.deepcopy(self.vault.data["settings"]))
            value["security"] = self.security_status()
            return value

    def integrations(self) -> list:
        with self.vault.lock:
            return copy.deepcopy(self.vault.data["integrations"])

    @staticmethod
    def public_integration(config):
        result = redact(copy.deepcopy(config))
        from . import direct_tv
        if direct_tv.is_direct(config):
            try:
                protocol = direct_tv.protocol_for(config)
                result["supportedCommands"] = list(direct_tv.SUPPORTED_COMMANDS[protocol])
            except direct_tv.TVError:
                result["supportedCommands"] = []
        return result

    def integration(self, identity: str) -> dict:
        item = next((item for item in self.integrations() if item["id"] == identity), None)
        if item is None:
            raise APIError(404, "Integration not found.")
        return item

    def propose(self, body: dict, source="user") -> dict:
        kind = body.get("type")
        allowed = {"tv.command", "ha.service", "file.write", "file.read"}
        if kind not in allowed:
            raise APIError(400, "That action type is not supported.")
        payload = body.get("payload", {})
        if not isinstance(payload, dict):
            raise APIError(400, "Action payload must be an object.")
        if len(json.dumps(payload)) > 180000:
            raise APIError(400, "Action payload is too large.")
        if kind.startswith("file.") and not isinstance(payload.get("path"), str):
            raise APIError(400, "File actions need a workspace-relative path.")
        if kind == "file.write" and not isinstance(payload.get("content"), str):
            raise APIError(400, "File writes need text content for review.")
        if kind in {"tv.command", "ha.service"}:
            self.integration(str(body.get("target", "")))
        action = {"id": secrets.token_urlsafe(12), "type": kind, "target": str(body.get("target", "workspace"))[:100], "payload": copy.deepcopy(payload), "status": "pending", "source": source, "createdAt": time.time(), "expiresAt": time.time() + 900}
        with self.vault.lock:
            self.vault.data["actions"].append(action)
            self.vault.data["actions"] = self.vault.data["actions"][-100:]
            self.vault.event("Action awaiting approval", kind, "approval")
        return copy.deepcopy(action)

    def safe_error(self, exc: Exception) -> str:
        # User-facing integration errors are scrubbed against every stored secret.
        message = str(exc)[:1000] if isinstance(exc, (ValueError, RuntimeError, TimeoutError, OSError)) else "The operation failed. Check the configured host and service, then retry."
        def collect(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key.lower() in {"password", "token", "apikey", "api_key", "secret", "clientkey", "authorization", "tvcredentials"} and isinstance(value, str) and value:
                        yield value
                    elif key.lower() in {"tvcredentials", "vidaaprofile"}:
                        def strings(item):
                            if isinstance(item, str) and item:
                                yield item
                            elif isinstance(item, dict):
                                for nested in item.values():
                                    yield from strings(nested)
                            elif isinstance(item, list):
                                for nested in item:
                                    yield from strings(nested)
                        yield from strings(value)
                    elif isinstance(value, (dict, list)):
                        yield from collect(value)
            elif isinstance(obj, list):
                for value in obj:
                    yield from collect(value)
        with self.vault.lock:
            for secret in collect(self.vault.data):
                message = message.replace(secret, "[redacted]")
        message = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", message)
        return message[:350]

    def chat_context(self, history) -> dict:
        if not isinstance(history, list):
            raise APIError(400, "Conversation history must be a list of messages.")
        messages, budget = [], 16000
        for entry in reversed(history[-8:]):
            if not isinstance(entry, dict) or entry.get("role") not in {"user", "assistant"} or not isinstance(entry.get("content"), str):
                raise APIError(400, "Conversation history may contain only user and assistant text messages.")
            content = entry["content"][:min(4000, budget)]
            budget -= len(content)
            if content:
                messages.insert(0, {"role": entry["role"], "content": content})
            if budget <= 0:
                break
        context = self.overview()
        context["history"] = messages
        reads, byte_budget = [], 24 * 1024
        with self.vault.lock:
            current = time.time()
            self.approved_reads = {path: entry for path, entry in self.approved_reads.items() if current - entry["approvedAt"] <= 1800}
            for entry in sorted(self.approved_reads.values(), key=lambda item: item["approvedAt"], reverse=True)[:3]:
                content = entry["content"]
                limited = content.encode("utf-8")[:byte_budget].decode("utf-8", "ignore")
                byte_budget -= len(limited.encode("utf-8"))
                reads.append({"path": entry["path"], "content": limited, "truncated": len(limited) < len(content), "totalCharacters": len(content)})
                if byte_budget <= 0:
                    break
        context["approvedFileReads"] = reads
        return context


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeCommand"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> Application:
        return self.server.app

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        # Avoid request lines, query strings, credentials and model output in logs.
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store")
        if self.app.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def send_bytes(self, status: int, value: bytes, kind="application/json", cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(value)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(value)

    def send_json(self, status: int, value: dict, cookie=None):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8", cookie)

    def cookie_token(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            value = cookies.get("hc_session")
            return value.value if value else ""
        except Exception:
            return ""

    def session(self):
        return self.app.sessions.get(self.cookie_token())

    def require_auth(self):
        session = self.session()
        if not session:
            raise APIError(401, "Sign in to Home Command.", "authentication_required")
        return session

    def validate_origin(self):
        raw_host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("//" + raw_host)
            hostname = parsed.hostname
            port = parsed.port or (443 if self.app.secure else 80)
        except ValueError:
            raise APIError(400, "Invalid host header.")
        if hostname not in self.app.allowed_hosts or port != self.app.port:
            raise APIError(403, "This host is not allowed. Use the local dashboard address.", "host_rejected")
        origin = self.headers.get("Origin")
        if origin:
            expected = ("https" if self.app.secure else "http") + "://" + raw_host
            if not hmac.compare_digest(origin, expected):
                raise APIError(403, "Cross-origin requests are blocked.", "origin_rejected")
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            raise APIError(403, "Cross-site requests are blocked.", "origin_rejected")

    def body(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise APIError(400, "Chunked request bodies are not accepted.")
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise APIError(415, "Send JSON using application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError(400, "Invalid request length.")
        if not 0 < length <= 262144:
            raise APIError(413, "Request body is empty or too large.")
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            raise APIError(400, "Invalid JSON request.")
        if not isinstance(value, dict):
            raise APIError(400, "The JSON request must be an object.")
        return value

    def dispatch(self):
        try:
            self.validate_origin()
            path = unquote(urlsplit(self.path).path)
            if path == "/health":
                self.send_json(200, {"status": "ok", "version": __version__})
                return
            if not path.startswith("/api/"):
                if self.command not in {"GET", "HEAD"}:
                    raise APIError(405, "Method not allowed.")
                self.static(path)
                return
            if self.command in {"GET", "HEAD"}:
                self.get_api(path)
                return
            if self.command == "DELETE" and not re.fullmatch(r"/api/integrations/[^/]+", path):
                raise APIError(405, "Use POST for this operation.")
            body = self.body()
            if path not in {"/api/auth/setup", "/api/auth/login"}:
                session = self.require_auth()
                if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
                    raise APIError(403, "Your security token expired. Refresh and sign in again.", "csrf_rejected")
            self.mutate_api(path, body)
        except APIError as exc:
            self.send_json(exc.status, {"error": exc.code, "message": exc.message})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception as exc:
            self.send_json(502, {"error": "integration_failed", "message": self.app.safe_error(exc)})

    do_GET = dispatch
    do_HEAD = dispatch
    do_POST = dispatch
    do_DELETE = dispatch

    def static(self, path):
        requested = (self.app.web_root / (path.lstrip("/") or "index.html")).resolve()
        if not requested.is_relative_to(self.app.web_root):
            raise APIError(403, "Path is outside the dashboard.")
        if not requested.is_file():
            if "." not in Path(path).name:
                requested = self.app.web_root / "index.html"
            if not requested.is_file():
                raise APIError(404, "File not found.")
        self.send_bytes(200, requested.read_bytes(), mimetypes.guess_type(requested)[0] or "application/octet-stream")

    def status(self):
        session = self.session()
        result = {"setupRequired": not bool(self.app.vault.data.get("account")), "authenticated": bool(session), "secure": self.app.secure, "version": __version__}
        if session:
            result.update(user=session["user"], csrfToken=session["csrf"])
        return result

    def get_api(self, path):
        from . import ai, integrations
        if path == "/api/status":
            self.send_json(200, self.status())
            return
        self.require_auth()
        if path == "/api/overview":
            self.send_json(200, self.app.overview())
        elif path == "/api/system":
            self.send_json(200, {"version": __version__, "lan": copy.deepcopy(self.app.lan)})
        elif path == "/api/network-identity":
            self.send_json(200, self.app.network_identity.snapshot())
        elif path == "/api/devices":
            self.send_json(200, {"devices": self.app.monitor.overview().get("devices", [])})
        elif path == "/api/integrations":
            self.send_json(200, {"integrations": [self.app.public_integration(i) for i in self.app.integrations()]})
        elif path == "/api/actions":
            with self.app.vault.lock:
                actions = copy.deepcopy(self.app.vault.data["actions"][-100:])
            self.send_json(200, {"actions": actions})
        elif path == "/api/settings":
            self.send_json(200, self.app.settings())
        elif path == "/api/entities":
            entities, errors = [], []
            for config in self.app.integrations():
                if config["type"] == "homeassistant":
                    try:
                        found = integrations.get_entities(config)
                        entities.extend([dict(item, integrationId=config["id"]) for item in found])
                    except Exception as exc:
                        errors.append({"id": config["id"], "message": self.app.safe_error(exc)})
            self.send_json(200, {"entities": entities, "errors": errors})
        elif path == "/api/servers":
            servers, errors = [], []
            for config in self.app.integrations():
                if config["type"] == "server":
                    try:
                        servers.append(dict(integrations.get_server(config), id=config["id"], name=config["name"]))
                    except Exception as exc:
                        errors.append({"id": config["id"], "name": config["name"], "message": self.app.safe_error(exc)})
            self.send_json(200, {"servers": servers, "errors": errors})
        elif path == "/api/ai/models":
            self.send_json(200, {"models": ai.get_models(copy.deepcopy(self.app.vault.data["settings"]))})
        elif re.fullmatch(r"/api/cameras/[^/]+/snapshot", path):
            config = self.app.integration(path.split("/")[3])
            if config["type"] != "hikvision":
                raise APIError(400, "This integration is not a camera.")
            self.send_bytes(200, integrations.get_snapshot(config), "image/jpeg")
        else:
            raise APIError(404, "API endpoint not found.")

    def auth_cookie(self, token: str, clear=False):
        return f"hc_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if clear else 28800}" + ("; Secure" if self.app.secure else "")

    def auth(self, path, body):
        key = self.client_address[0]
        if not self.app.auth_limiter.permit(key):
            raise APIError(429, "Too many sign-in attempts. Wait five minutes before retrying.", "rate_limited")
        username, password = body.get("username", ""), body.get("password", "")
        if path.endswith("/setup"):
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise APIError(403, "Create the first account on this host using localhost, or use the terminal setup wizard.")
            if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
                raise APIError(400, "Username must be 1–64 letters, numbers, dots, dashes or underscores.")
            try:
                record = password_hash(password)
            except ValueError as exc:
                raise APIError(400, str(exc))
            with self.app.vault.lock:
                if self.app.vault.data.get("account"):
                    raise APIError(409, "An account already exists. Sign in instead.")
                self.app.vault.data["account"] = dict(username=username, password=record, createdAt=time.time())
                self.app.vault.event("Home Command secured", "Local administrator account created.", "security")
        else:
            with self.app.vault.lock:
                account = copy.deepcopy(self.app.vault.data.get("account"))
            if not account:
                raise APIError(401, "Create your local account first.", "setup_required")
            correct_password = verify_password(password, account["password"])
            if not correct_password or not isinstance(username, str) or not hmac.compare_digest(username, account["username"]):
                raise APIError(401, "Incorrect username, password or verification code.", "invalid_credentials")
            if account.get("mfaSecret"):
                counter = verify_totp(account["mfaSecret"], str(body.get("totp", "")), account.get("mfaLastCounter", -1))
                if counter is None:
                    raise APIError(401, "Enter a fresh six-digit authenticator code.", "mfa_required")
                with self.app.vault.lock:
                    current = self.app.vault.data["account"]
                    if counter <= current.get("mfaLastCounter", -1):
                        raise APIError(401, "This authenticator code has already been used.", "mfa_required")
                    current["mfaLastCounter"] = counter
                    self.app.vault.save()
            self.app.vault.event("Administrator signed in", "Authenticated local session.", "security")
        self.app.auth_limiter.clear(key)
        token, record = self.app.sessions.create(username)
        self.send_json(200, {"setupRequired": False, "authenticated": True, "user": username, "csrfToken": record["csrf"], "secure": self.app.secure, "version": __version__}, self.auth_cookie(token))

    def mutate_api(self, path, body):
        from . import ai, integrations
        if path in {"/api/auth/setup", "/api/auth/login"}:
            if self.command != "POST":
                raise APIError(405, "Use POST for authentication.")
            self.auth(path, body)
        elif path == "/api/auth/logout":
            self.app.sessions.delete(self.cookie_token())
            self.send_json(200, {"authenticated": False}, self.auth_cookie("", clear=True))
        elif path == "/api/discovery":
            devices = self.app.monitor.discover()
            self.app.vault.event("Network discovery completed", f"{len(devices)} local devices found.", "network")
            self.send_json(200, {"devices": devices})
        elif path == "/api/network-identity/detect":
            if not self.app.identity_limiter.permit(self.client_address[0]):
                raise APIError(429, "ISP detection was requested too often. Wait a minute before retrying.", "rate_limited")
            # Explicit detection chooses the detected provider, rather than
            # silently displaying a saved manual override as the lookup result.
            choose_detected = body.get("useDetected", True) is True
            old_manual = self.app.identity_settings().get("ispName", "")
            try:
                identity = self.app.network_identity.detect(allow_external=body.get("allowExternal") is True, force_refresh=body.get("forceRefresh") is True, prefer_detected=choose_detected)
            except PermissionError as exc:
                raise APIError(403, str(exc), "consent_required")
            if choose_detected and not identity.get("lookupInProgress") and identity["isp"].get("status") == "detected":
                with self.app.vault.lock:
                    settings = self.app.vault.data["settings"]["networkIdentity"]
                    if settings.get("ispName", "") == old_manual:
                        settings["ispName"] = ""
                        self.app.vault.event("Automatic ISP identification selected", "Saved manual provider override cleared after a successful lookup.", "settings")
                    else:
                        raise APIError(409, "The provider setting changed while detection was running. Retry to apply the detected provider.")
                identity = self.app.network_identity.snapshot()
            self.send_json(200, identity)
        elif path == "/api/integrations" and self.command == "POST":
            self.save_integration(body)
        elif re.fullmatch(r"/api/integrations/[^/]+/test", path):
            config = self.app.integration(path.split("/")[3])
            try:
                result = integrations.test_integration(config)
                if not isinstance(result, dict):
                    result = {"status": "connected", "message": str(result)}
            except Exception as exc:
                result = {"status": "error", "message": self.app.safe_error(exc)}
            with self.app.vault.lock:
                stored = next(item for item in self.app.vault.data["integrations"] if item["id"] == config["id"])
                stored.update(status=result.get("status", "connected"), lastChecked=time.time(), message=result.get("message", ""))
                self.app.vault.event("Integration checked", f"{config['name']}: {stored['status']}", "integration")
            self.send_json(200, redact(result))
        elif re.fullmatch(r"/api/integrations/[^/]+", path) and self.command == "DELETE":
            config = self.app.integration(path.split("/")[3])
            with self.app.vault.lock:
                self.app.vault.data["integrations"] = [item for item in self.app.vault.data["integrations"] if item["id"] != config["id"]]
                self.app.vault.event("Integration removed", config["name"], "integration")
            self.send_json(200, {"deleted": True})
        elif re.fullmatch(r"/api/tvs/[^/]+/pair/(start|finish|cancel)", path):
            self.pair_tv(path, body)
        elif re.fullmatch(r"/api/tvs/[^/]+/command", path):
            self.manual_tv_command(path, body)
        elif path == "/api/settings":
            self.save_settings(body)
        elif path == "/api/actions":
            self.send_json(201, {"action": self.app.propose(body)})
        elif re.fullmatch(r"/api/actions/[^/]+/(approve|reject)", path):
            self.resolve_action(path)
        elif path == "/api/chat":
            message = body.get("message", "")
            if not isinstance(message, str) or not 1 <= len(message.strip()) <= 8000:
                raise APIError(400, "Write a message of 1–8000 characters.")
            context = self.app.chat_context(body.get("history", []))
            if not self.app.chat_limiter.permit(self.client_address[0]) or not self.app.chat_slots.acquire(blocking=False):
                raise APIError(429, "The assistant is busy. Please retry shortly.")
            try:
                settings = copy.deepcopy(self.app.vault.data["settings"])
                result = ai.chat(message, settings, context)
                proposed = []
                for action in result.get("actions", [])[:8]:
                    try:
                        proposed.append(self.app.propose(action, source="assistant"))
                    except APIError:
                        continue
                result["actions"] = proposed
                self.send_json(200, result)
            finally:
                self.app.chat_slots.release()
        elif path == "/api/auth/mfa/setup":
            with self.app.vault.lock:
                account = self.app.vault.data["account"]
                if account.get("mfaSecret"):
                    raise APIError(409, "Two-factor authentication is already enabled.")
                secret = new_totp_secret()
                account["mfaPending"] = secret
                account["mfaPendingUntil"] = time.time() + 600
                self.app.vault.save()
                uri = totp_uri(secret, account["username"])
            self.send_json(200, {"secret": secret, "uri": uri})
        elif path == "/api/auth/mfa/confirm":
            with self.app.vault.lock:
                account = self.app.vault.data["account"]
                if not account.get("mfaPending") or time.time() > account.get("mfaPendingUntil", 0):
                    raise APIError(400, "Authenticator setup expired. Start setup again.")
                counter = verify_totp(account["mfaPending"], str(body.get("code", "")))
                if counter is None:
                    raise APIError(400, "That verification code is invalid.")
                account["mfaSecret"] = account.pop("mfaPending")
                account.pop("mfaPendingUntil", None)
                account["mfaLastCounter"] = counter
                self.app.vault.event("Two-factor authentication enabled", "Authenticator codes are now required at sign-in.", "security")
            self.send_json(200, {"mfaEnabled": True})
        else:
            raise APIError(404, "API endpoint not found.")

    def save_integration(self, body):
        allowed = {"homeassistant", "samsung", "androidtv", "appletv", "hisense", "roku", "webos", "hikvision", "server", "ollama"}
        kind, name, host = body.get("type"), body.get("name", ""), body.get("host", "")
        if kind not in allowed or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not isinstance(host, str) or not host.strip():
            raise APIError(400, "Choose an integration type, name and local host address.")
        parsed = urlsplit(host if "://" in host else "http://" + host)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise APIError(400, "Use a local HTTP or HTTPS host without embedded credentials, query or fragment.")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            if not (address.is_private or address.is_loopback) or address.is_unspecified or address.is_multicast:
                raise APIError(400, "Device integrations must use a private home-network address.")
        except ValueError:
            # DNS names are resolved and checked again by the integration client.
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", parsed.hostname):
                raise APIError(400, "Invalid local hostname.")
        accepted = {"username", "password", "token", "port", "entityId", "remoteEntityId", "homeAssistantId", "channel", "snapshotChannel", "apiKey", "model", "protocol", "monitoringPath", "basePath", "connectionMode", "allowInsecureLocalControl", "apiPort", "pairPort"}
        if "manualControlEnabled" in body and type(body["manualControlEnabled"]) is not bool:
            raise APIError(400, "Manual TV control permission must be a JSON boolean.")
        config = {key: value for key, value in body.items() if key in accepted and isinstance(value, (str, int, float, bool))}
        for value in config.values():
            if isinstance(value, str) and len(value) > 8000:
                raise APIError(400, "Integration field is too long.")
        tv_types = {"samsung", "androidtv", "appletv", "hisense", "roku", "webos"}
        if kind in tv_types:
            mode = config.get("connectionMode", "homeassistant")
            if mode not in {"direct", "homeassistant"}:
                raise APIError(400, "Choose a direct TV connection or Home Assistant.")
            if mode == "direct":
                if "manualControlEnabled" in body:
                    config["manualControlEnabled"] = body["manualControlEnabled"]
                protocol = config.get("protocol") or ("" if kind == "hisense" else kind)
                if protocol not in {"samsung", "androidtv", "appletv", "roku", "webos", "vidaa"}:
                    raise APIError(400, "Choose the TV's actual platform: VIDAA, Android/Google TV, Roku, Samsung, Apple TV, or LG webOS.")
                config["protocol"] = protocol
                if protocol == "vidaa" and body.get("vidaaProfile"):
                    from . import vidaa
                    raw_profile = body["vidaaProfile"]
                    if not isinstance(raw_profile, str) or len(raw_profile) > 65536:
                        raise APIError(400, "The VIDAA credential profile must be a JSON object under 64 KB.")
                    try:
                        config["vidaaProfile"] = vidaa.validate_profile(json.loads(raw_profile))
                    except (ValueError, TypeError):
                        raise APIError(400, "Invalid VIDAA profile. Supply an authorized client ID, MQTT credentials, client certificate/key, and a trusted CA or approved server fingerprint. See docs/TV.md.") from None
                config["allowInsecureLocalControl"] = body.get("allowInsecureLocalControl") is True
                for key in ("homeAssistantId", "entityId", "remoteEntityId", "token"):
                    config.pop(key, None)
                for key in ("apiPort", "pairPort"):
                    if key in config:
                        try:
                            config[key] = int(config[key])
                        except (TypeError, ValueError):
                            raise APIError(400, "TV ports must be integers.")
                        if not 1 <= config[key] <= 65535:
                            raise APIError(400, "TV ports must be between 1 and 65535.")
        with self.app.vault.lock:
            identity = body.get("id")
            existing = next((item for item in self.app.vault.data["integrations"] if item["id"] == identity), None)
            if identity and existing is None:
                raise APIError(404, "Integration not found.")
            if existing:
                if existing.get("type") != kind:
                    # Never carry a TV's device identity through another
                    # integration type and later reuse it on a new endpoint.
                    existing.pop("tvCredentials", None)
                    existing.pop("vidaaProfile", None)
                    existing["manualControlEnabled"] = False
                if kind in tv_types and config.get("connectionMode") == "direct":
                    # Pairing credentials belong to one endpoint and protocol.
                    # Changing those requires pairing again; never carry a
                    # Home Assistant token into a direct TV connection.
                    changed = any(existing.get(key) != value for key, value in (
                        ("host", host.strip()), ("protocol", config.get("protocol")),
                        ("connectionMode", "direct"), ("apiPort", config.get("apiPort", existing.get("apiPort"))),
                        ("pairPort", config.get("pairPort", existing.get("pairPort"))), ("port", config.get("port", existing.get("port"))),
                        ("vidaaProfile", config.get("vidaaProfile", existing.get("vidaaProfile")))))
                    if changed:
                        existing.pop("tvCredentials", None)
                        existing["manualControlEnabled"] = False
                    for key in ("homeAssistantId", "entityId", "remoteEntityId", "token"):
                        existing.pop(key, None)
                elif config.get("connectionMode") == "homeassistant":
                    existing.pop("tvCredentials", None)
                    existing["manualControlEnabled"] = False
                if config.get("protocol") != "vidaa" or config.get("connectionMode") != "direct":
                    existing.pop("vidaaProfile", None)
                for key in {"password", "token", "apiKey"}:
                    if not config.get(key) and existing.get(key):
                        config[key] = existing[key]
                existing.update(config, type=kind, name=name.strip(), host=host.strip(), status="configured", message="Connection details updated; test when on the home network.")
                stored = existing
            else:
                if len(self.app.vault.data["integrations"]) >= 100:
                    raise APIError(400, "The maximum of 100 configured integrations has been reached.")
                stored = dict(config, id=secrets.token_urlsafe(10), type=kind, name=name.strip(), host=host.strip(), status="configured", createdAt=time.time())
                self.app.vault.data["integrations"].append(stored)
            if kind in tv_types and config.get("connectionMode") == "direct":
                stored.setdefault("manualControlEnabled", False)
            self.app.vault.event("Integration configured", stored["name"], "integration")
        self.send_json(200, {"integration": redact(stored)})

    def manual_tv_command(self, path, body):
        """Authenticated remote buttons use a saved, per-TV manual permission.

        Assistant actions still use the separate proposal/approval pipeline.
        A pairing, login, CSRF token, grant and command allowlist are all needed.
        """
        from . import direct_tv
        if self.command != "POST":
            raise APIError(405, "Use POST for a manual TV command.")
        expected = {"command","text"} if body.get("command") == "text" else {"command"}
        if set(body) != expected or not isinstance(body.get("command"), str):
            raise APIError(400, "Send exactly one supported TV command.")
        if body.get("command") == "text":
            try:
                direct_tv.validate_text(body.get("text"))
            except direct_tv.TVError as exc:
                raise APIError(400,str(exc)) from None
        identity = path.split("/")[3]
        config = self.app.integration(identity)
        if not direct_tv.is_direct(config) or config.get("manualControlEnabled") is not True or not config.get("tvCredentials"):
            raise APIError(403, "Pair this direct TV and enable immediate manual remote control in its connection settings first.", "manual_control_disabled")
        protocol = direct_tv.protocol_for(config)
        command = body["command"]
        if command not in direct_tv.SUPPORTED_COMMANDS[protocol]:
            raise APIError(400, "This command is not supported by the TV platform.")
        if not self.app.tv_manual_limiter.permit(self.client_address[0]) or not self.app.tv_manual_slots.acquire(blocking=False):
            raise APIError(429, "TV remote is busy. Try again in a moment.", "rate_limited")
        with self.app.tv_manual_lock_guard:
            # Remove obsolete locks without disturbing an in-flight request.
            if len(self.app.tv_manual_locks) >= 100:
                self.app.tv_manual_locks = {key:lock for key,lock in self.app.tv_manual_locks.items() if lock.locked()}
            lock = self.app.tv_manual_locks.setdefault(identity, threading.Lock())
            acquired = lock.acquire(blocking=False)
        try:
            if not acquired:
                raise APIError(429, "This TV is still processing another remote button.", "rate_limited")
            # Refresh after acquiring the target lock so a prior permission
            # revocation or endpoint edit cannot be replayed from an old copy.
            config = self.app.integration(identity)
            if not direct_tv.is_direct(config) or config.get("manualControlEnabled") is not True or not config.get("tvCredentials"):
                raise APIError(403, "Immediate manual control is disabled or this TV needs pairing again.", "manual_control_disabled")
            if command not in direct_tv.SUPPORTED_COMMANDS[direct_tv.protocol_for(config)]:
                raise APIError(400, "This command is not supported by the saved TV platform.")
            result = direct_tv.execute_tv_command(config, body, approved=True)
            result = {**result, "message":f"Sent {'text input' if command=='text' else command.replace('_',' ')} to {config['name']}."}
            self.app.vault.event("Manual TV command", f"{config['name']}: {command}", "device")
            self.send_json(200, redact(result))
        finally:
            if acquired:
                lock.release()
            self.app.tv_manual_slots.release()

    def pair_tv(self, path, body):
        from . import direct_tv
        identity, operation = path.split("/")[3], path.rsplit("/", 1)[1]
        config = self.app.integration(identity)
        if not direct_tv.is_direct(config):
            raise APIError(400, "Save a direct TV connection before pairing.")
        if operation == "cancel":
            self.send_json(200, direct_tv.cancel_pairing(str(body.get("pairingId", "")), config))
            return
        if body.get("approved") is not True:
            raise APIError(403, "Review the TV address and approve starting or completing pairing.", "approval_required")
        if not self.app.pair_limiter.permit(self.client_address[0]) or not self.app.pair_slots.acquire(blocking=False):
            raise APIError(429, "TV pairing is busy. Wait a minute before retrying.", "rate_limited")
        try:
            if operation == "start":
                result = direct_tv.start_pairing(config, approved=True)
                self.app.vault.event("TV pairing requested", config["name"], "approval")
            else:
                code = body.get("code", "")
                if not isinstance(code, str) or len(code) > 16:
                    raise APIError(400, "Enter the short pairing code shown on the TV.")
                result = direct_tv.finish_pairing(str(body.get("pairingId", "")), config, code, approved=True)
                credentials = result.get("credentials")
                if result.get("status") != "paired" or not isinstance(credentials, dict):
                    raise APIError(502, "The TV did not confirm pairing. Retry the connection.")
                with self.app.vault.lock:
                    stored = next((i for i in self.app.vault.data["integrations"] if i["id"] == identity), None)
                    if not stored or any(stored.get(k) != config.get(k) for k in ("type", "host", "protocol", "connectionMode", "allowInsecureLocalControl", "port", "apiPort", "pairPort", "applePairProtocol", "vidaaProfile")):
                        raise APIError(409, "The TV connection changed during pairing. Pair the saved address again.")
                    stored.update(tvCredentials=copy.deepcopy(credentials), status="paired", message=result.get("message", "TV pairing completed."), lastChecked=time.time())
                    self.app.vault.event("TV paired", config["name"], "integration")
            # Tokens, PINs, certificates, and client keys never cross this boundary.
            public = {key: result[key] for key in ("pairingId", "status", "protocol", "requiresCode", "message", "expiresAt") if key in result}
            self.send_json(200, public)
        finally:
            self.app.pair_slots.release()

    def save_settings(self, body):
        with self.app.vault.lock:
            current = copy.deepcopy(self.app.vault.data["settings"])
            if "workspaceName" in body:
                name = body["workspaceName"]
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(unicodedata.category(character) in {"Cc", "Cs"} or character in "<>" for character in name):
                    raise APIError(400, "Choose a workspace name of 1–64 plain-text characters, without control characters or markup.")
                current["workspaceName"] = " ".join(name.split())
            if "networkIdentity" in body:
                try:
                    identity = validate_identity_settings(body["networkIdentity"])
                except ValueError as exc:
                    raise APIError(400, str(exc))
                current["networkIdentity"] = {**IDENTITY_DEFAULTS, **current.get("networkIdentity", {}), **identity}
            if "ai" in body:
                candidate = body["ai"]
                if not isinstance(candidate, dict) or candidate.get("provider") not in {"ollama", "openai", "openai-compatible", "none", "local"}:
                    raise APIError(400, "Choose Ollama or an OpenAI-compatible provider.")
                url = str(candidate.get("baseUrl", ""))
                parsed = urlsplit(url)
                if candidate["provider"] not in {"none", "local"}:
                    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                        raise APIError(400, "Enter a valid provider base URL without credentials.")
                    # Compatible local servers may use HTTP. The integration
                    # client resolves/pins the destination and requires HTTPS
                    # before it can ever contact a public AI endpoint.
                updated = {"provider": candidate["provider"], "baseUrl": url[:2048], "model": str(candidate.get("model", ""))[:200]}
                if candidate.get("apiKey"):
                    updated["apiKey"] = str(candidate["apiKey"])[:8000]
                elif current.get("ai", {}).get("apiKey") and current["ai"].get("baseUrl") == url:
                    updated["apiKey"] = current["ai"]["apiKey"]
                current["ai"] = updated
            if "workspace" in body:
                path = Path(str(body["workspace"])).expanduser().resolve()
                if not path.is_absolute() or not path.is_dir():
                    raise APIError(400, "The assistant workspace must be an existing local folder.")
                protected = [self.app.vault.directory, self.app.web_root, Path(__file__).resolve().parent]
                if any(path == item or path.is_relative_to(item) or item.is_relative_to(path) for item in protected):
                    raise APIError(400, "Choose a dedicated workspace outside the application and encrypted vault.")
                current["workspace"] = str(path)
            self.app.vault.data["settings"] = current
            if "workspace" in body:
                self.app.approved_reads.clear()
            self.app.vault.event("Settings updated", "Local application configuration saved.", "settings")
        if "networkIdentity" in body:
            self.app.network_identity.settings_changed()
        self.send_json(200, self.app.settings())

    def resolve_action(self, path):
        from . import integrations
        identity, decision = path.split("/")[3:5]
        with self.app.vault.lock:
            action = next((item for item in self.app.vault.data["actions"] if item["id"] == identity), None)
            if not action:
                raise APIError(404, "Action not found.")
            if action["status"] != "pending":
                raise APIError(409, "This action has already been resolved.")
            if time.time() > action.get("expiresAt", 0):
                action["status"] = "expired"
                self.app.vault.save()
                raise APIError(409, "This approval expired. Request the action again.")
            if decision == "reject":
                action.update(status="rejected", resolvedAt=time.time())
                self.app.vault.event("Action rejected", action["type"], "approval")
                self.send_json(200, {"action": copy.deepcopy(action)})
                return
            action.update(status="executing", resolvedAt=time.time())
            self.app.vault.save()
            approved = copy.deepcopy(action)
        try:
            approved_workspace = self.app.vault.data["settings"]["workspace"]
            result = integrations.execute_action(approved, self.app.integrations(), approved_workspace)
            with self.app.vault.lock:
                stored_result = result
                if action["type"] == "file.read" and isinstance(result, dict) and isinstance(result.get("content"), str):
                    if self.app.vault.data["settings"]["workspace"] == approved_workspace:
                        self.app.approved_reads[result["path"]] = {"path": result["path"], "content": result["content"], "approvedAt": time.time()}
                    # File bodies stay in transient memory, outside persisted audit data.
                    stored_result = {key: value for key, value in result.items() if key != "content"}
                    stored_result["charactersRead"] = len(result["content"])
                    stored_result["message"] = "Approved file content is available to the assistant for 30 minutes in this server session."
                if action["type"] == "file.write":
                    self.app.approved_reads.clear()
                action.update(status="completed", result=stored_result, completedAt=time.time())
                self.app.vault.event("Approved action completed", action["type"], "success")
        except Exception as exc:
            with self.app.vault.lock:
                action.update(status="failed", error=self.app.safe_error(exc), completedAt=time.time())
                self.app.vault.event("Approved action failed", action["type"], "error")
        self.send_json(200, {"action": copy.deepcopy(action)})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 24

    def __init__(self, address, app: Application):
        self.app = app
        self.tls_context = None
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, Handler)
        app.port = self.server_port

    def get_request(self):
        request, address = self.socket.accept()
        if not is_local_peer(address[0]):
            request.close()
            raise OSError("Public client addresses are not accepted.")
        if self.tls_context:
            request.settimeout(5)
            try:
                request = self.tls_context.wrap_socket(request, server_side=True)
            except Exception:
                request.close()
                raise
        return request, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def verify_request(self, request, client_address):
        return is_local_peer(client_address[0])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Home Command local household dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8420, type=int)
    parser.add_argument("--data-dir", default=".data")
    parser.add_argument("--lan", action="store_true", help="Explicitly enable private-network access; requires HTTPS certificate and key")
    parser.add_argument("--advertise", action="store_true", help="Advertise the authenticated HTTPS dashboard over local-link mDNS; requires --lan")
    parser.add_argument("--auto-discover", action="store_true", help="Start authorized read-only discovery on the current network; requires --lan and an existing account")
    parser.add_argument("--cert", "--tls-cert", dest="cert")
    parser.add_argument("--key", "--tls-key", dest="key")
    parser.add_argument("--allow-host", action="append", default=[], help="Additional LAN DNS hostname accepted by the anti-rebinding protection")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        binding = ipaddress.ip_address(args.host)
    except ValueError:
        if args.host != "localhost":
            parser.error("Bind to localhost or an explicit private IP address.")
        binding = ipaddress.ip_address("127.0.0.1")
    if not binding.is_loopback and (not args.lan or not args.cert or not args.key):
        parser.error("Private-network binding requires --lan --cert <certificate.pem> --key <key.pem>. Default access is loopback only.")
    if args.lan and (not args.cert or not args.key):
        parser.error("LAN access requires a TLS certificate and key.")
    if args.lan and binding.is_loopback:
        parser.error("LAN mode requires --host 0.0.0.0 or an explicit private interface address. For loopback HTTPS, omit --lan.")
    if (args.advertise or args.auto_discover) and not args.lan:
        parser.error("--advertise and --auto-discover require explicit --lan HTTPS access.")
    if not (is_local_peer(str(binding)) or binding.is_unspecified):
        parser.error("Only local/private network addresses are permitted.")
    if bool(args.cert) != bool(args.key):
        parser.error("Supply both --cert and --key.")
    app = Application(args.data_dir)
    if args.lan and not app.vault.data.get("account"):
        close_monitor = getattr(app.monitor, "close", None)
        if close_monitor:
            close_monitor()
        parser.error("Create a strong local administrator account before enabling LAN access. Use the offline account setup command or initialize it over loopback first.")
    app.allowed_hosts.update(args.allow_host)
    if not binding.is_unspecified:
        app.allowed_hosts.add(str(binding))
    if args.lan:
        hostname = get_lan_name(args.data_dir)
        app.allowed_hosts.add(hostname)
        app.lan.update(enabled=True, hostname=hostname, url=f"https://{hostname}:{args.port}", port=args.port, autoDiscover=args.auto_discover, advertisementState="starting" if args.advertise else "disabled")
        try:
            for candidate in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = candidate[4][0]
                if is_local_peer(address):
                    app.allowed_hosts.add(address)
            local_address = app.monitor.overview().get("network", {}).get("localAddress")
            if local_address and is_local_peer(local_address):
                app.allowed_hosts.add(local_address)
                app.lan["addresses"] = [local_address]
        except OSError:
            pass
    server = Server((args.host, args.port), app)
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        # Filter public peers before even performing a TLS handshake.
        server.tls_context = context
        app.secure = True
    advertiser = None
    if args.advertise:
        def address_supplier():
            address = app.monitor.overview().get("network", {}).get("localAddress") if binding.is_unspecified else str(binding)
            if address and is_local_peer(address):
                with app.vault.lock:
                    app.allowed_hosts.add(address)
                    app.lan["addresses"] = [address]
                return address
            with app.vault.lock:
                app.lan["addresses"] = []
            return None

        def advertised_state(state, error, address):
            with app.vault.lock:
                app.lan.update(advertising=state == "advertising", advertisementState=state, advertisementError=error)

        advertiser = MDNSAdvertiser(app.lan["hostname"], args.port, address_supplier, advertised_state)
        advertiser.start()
    if args.auto_discover:
        def startup_discovery():
            try:
                devices = app.monitor.discover()
                app.vault.event("Automatic network discovery completed", f"{len(devices)} local devices observed on the authorized current network.", "network")
            except Exception:
                app.vault.event("Automatic discovery unavailable", "Start a manual scan when the current network is ready.", "network")
        threading.Thread(target=startup_discovery, name="startup-discovery", daemon=True).start()
    app.network_identity.start()
    print(f"Home Command running at {'https' if app.secure else 'http'}://{'localhost' if binding.is_unspecified else args.host}:{args.port}", flush=True)
    print("Private network access enabled with HTTPS." if args.lan else "Loopback only. No public or LAN listener is enabled.", flush=True)
    if args.advertise:
        print(f"Local discovery name: {app.lan['url']} (multicast availability depends on this LAN).", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.network_identity.close()
        if advertiser:
            advertiser.close()
        server.server_close()
        close = getattr(app.monitor, "close", None)
        if close:
            close()


if __name__ == "__main__":
    main()
