import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from app.security import RateLimiter, Sessions, Vault, password_hash, redact, totp, verify_password, verify_totp


class SecurityTests(unittest.TestCase):
    def test_vault_encrypts_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Vault(Path(root) / "data")
            vault.data["integrations"] = [{"token": "very-private-integration-token"}]
            vault.save()
            self.assertNotIn(b"very-private-integration-token", vault.path.read_bytes())
            self.assertEqual(Vault(vault.directory).data["integrations"][0]["token"], "very-private-integration-token")
            self.assertEqual(os.stat(vault.directory / "master.key").st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(vault.directory).st_mode & 0o777, 0o700)
            encrypted = bytearray(vault.path.read_bytes())
            encrypted[-1] ^= 1
            vault.path.write_bytes(encrypted)
            with self.assertRaises(RuntimeError):
                Vault(vault.directory)

    def test_password_scrypt_and_requirements(self):
        record = password_hash("a long unique password for home")
        self.assertTrue(verify_password("a long unique password for home", record))
        self.assertFalse(verify_password("bad", record))
        self.assertNotEqual(record["hash"], password_hash("a long unique password for home")["hash"])
        with self.assertRaises(ValueError):
            password_hash("too short")

    def test_totp_rfc_and_replay(self):
        # RFC 6238 SHA-1 test secret, 6-digit truncation of the 8-digit vector.
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertEqual(totp(secret, 59), "287082")
        code = totp(secret)
        counter = verify_totp(secret, code)
        self.assertIsNotNone(counter)
        self.assertIsNone(verify_totp(secret, code, counter))
        self.assertIsNone(verify_totp(secret, "abcdef"))

    def test_sessions_expire_and_store_no_raw_token(self):
        sessions = Sessions()
        token, record = sessions.create("owner")
        self.assertNotIn(token, sessions.records)
        self.assertEqual(sessions.get(token)["user"], "owner")
        next(iter(sessions.records.values()))["created"] = time.time() - 28801
        self.assertIsNone(sessions.get(token))

    def test_rate_limiter_and_nested_redaction(self):
        limiter = RateLimiter(2)
        self.assertTrue(limiter.permit("local"))
        self.assertTrue(limiter.permit("local"))
        self.assertFalse(limiter.permit("local"))
        limiter.clear("local")
        self.assertTrue(limiter.permit("local"))
        value = redact({"token": "SECRET", "nested": {"apiKey": "KEY"}, "username": "owner"})
        self.assertNotIn("SECRET", json.dumps(value))
        self.assertNotIn("KEY", json.dumps(value))
        self.assertEqual(value["nested"], {"hasApiKey": True})

    def test_security_status_reports_verified_storage_and_real_file_modes(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Vault(Path(root) / "data")
            self.assertFalse(vault.security_status()["encryptedStorage"])
            vault.save()
            status = vault.security_status()
            self.assertTrue(status["encryptedStorage"])
            self.assertEqual(status["encryptedStorageState"], "verified")
            if os.name == "posix":
                self.assertTrue(status["keyPermissionsRestricted"])
                os.chmod(vault.directory / "master.key", 0o644)
                self.assertFalse(vault.security_status()["keyPermissionsRestricted"])
                os.chmod(vault.directory / "master.key", 0o600)
            encrypted = bytearray(vault.path.read_bytes())
            encrypted[-1] ^= 1
            vault.path.write_bytes(encrypted)
            self.assertFalse(vault.security_status()["encryptedStorage"])
            self.assertEqual(vault.security_status()["encryptedStorageState"], "modified-since-verification")

    def test_security_status_detects_replaced_master_key(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Vault(Path(root) / "data")
            vault.save()
            (vault.directory / "master.key").write_bytes(b"x" * 32)
            status = vault.security_status()
            self.assertFalse(status["encryptedStorage"])
            self.assertEqual(status["encryptedStorageState"], "key-changed")


if __name__ == "__main__":
    unittest.main()
