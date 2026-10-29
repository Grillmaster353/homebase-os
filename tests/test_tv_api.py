"""Authenticated direct-TV API contract; every TV transport is a controlled fixture."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from app.server import Application, Server


class QuietMonitor:
    def overview(self):
        return {"host": {}, "network": {"history": []}, "devices": []}


class TVAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temporary.name) / "data", monitor=QuietMonitor())
        self.server = Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie, self.csrf = "", ""
        status, result = self.request("/api/auth/setup", {"username": "fixture", "password": "offline fixture password for TV tests"})
        self.assertEqual(status, 200)
        self.csrf = result["csrfToken"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temporary.cleanup()

    def request(self, path, body=None, csrf=True):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Content-Type": "application/json", "Cookie": self.cookie}
        if csrf:
            headers["X-CSRF-Token"] = self.csrf
        client.request("GET" if body is None else "POST", path, None if body is None else json.dumps(body), headers)
        response = client.getresponse()
        value = json.loads(response.read())
        cookie = response.getheader("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";", 1)[0]
        client.close()
        return response.status, value

    def save_tv(self, **overrides):
        body = {"type": "androidtv", "name": "Fixture TV", "host": "192.168.1.50", "connectionMode": "direct", "protocol": "androidtv", **overrides}
        status, result = self.request("/api/integrations", body)
        self.assertEqual(status, 200, result)
        return result["integration"], body

    def test_pairing_needs_explicit_approval_and_csrf(self):
        tv, _ = self.save_tv()
        path = f"/api/tvs/{tv['id']}/pair/start"
        with patch("app.direct_tv.start_pairing") as start:
            self.assertEqual(self.request(path, {})[0], 403)
            self.assertEqual(self.request(path, {"approved": True}, csrf=False)[0], 403)
            start.assert_not_called()
            start.return_value = {"pairingId": "fixture-session", "status": "awaiting_code", "requiresCode": True, "protocol": "androidtv", "expiresAt": 9999999999, "message": "Read the TV screen"}
            status, data = self.request(path, {"approved": True})
            self.assertEqual(status, 200, data)
            self.assertTrue(data["requiresCode"])
            self.assertTrue(start.call_args.kwargs["approved"])

    def test_pairing_credentials_stay_encrypted_and_are_not_returned_or_audited(self):
        tv, body = self.save_tv(tvCredentials={"clientKey": "browser-injected-key"})
        self.assertNotIn("tvCredentials", self.app.integration(tv["id"]))
        credentials = {"certificate": "fixture-private-certificate", "clientKey": "fixture-private-key"}
        with patch("app.direct_tv.finish_pairing", return_value={"status": "paired", "protocol": "androidtv", "message": "Paired", "credentials": credentials, "code": "ABC123"}):
            status, result = self.request(f"/api/tvs/{tv['id']}/pair/finish", {"approved": True, "pairingId": "fixture-session", "code": "ABC123"})
        self.assertEqual(status, 200, result)
        self.assertNotIn("credentials", result)
        self.assertNotIn("code", result)
        self.assertEqual(self.app.integration(tv["id"])["tvCredentials"], credentials)
        public = self.request("/api/overview")[1]
        self.assertTrue(public["integrations"][0]["hasTvCredentials"])
        serialized = json.dumps(public) + self.app.vault.path.read_bytes().hex()
        for secret in (*credentials.values(), "ABC123", "browser-injected-key"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("fixture-private-key", self.app.safe_error(ValueError("fixture-private-key failed")))
        self.request("/api/integrations", {**body, "id": tv["id"], "apiPort": 6468})
        self.assertNotIn("tvCredentials", self.app.integration(tv["id"]))
        with self.app.vault.lock:
            self.app.vault.data["integrations"][0]["tvCredentials"] = credentials
        self.request("/api/integrations", {**body, "id": tv["id"], "host": "192.168.1.51"})
        self.assertNotIn("tvCredentials", self.app.integration(tv["id"]))

    def test_command_proposal_does_not_run_transport_and_approval_runs_once(self):
        tv, _ = self.save_tv()
        with patch("app.direct_tv.execute_tv_command", return_value={"message": "fixture accepted"}) as execute:
            status, data = self.request("/api/actions", {"type": "tv.command", "target": tv["id"], "payload": {"command": "home"}})
            self.assertEqual(status, 201, data)
            execute.assert_not_called()
            path = f"/api/actions/{data['action']['id']}/approve"
            status, result = self.request(path, {})
            self.assertEqual(status, 200, result)
            self.assertEqual(result["action"]["status"], "completed")
            self.assertTrue(execute.call_args.kwargs["approved"])
            self.assertEqual(self.request(path, {})[0], 409)
            self.assertEqual(execute.call_count, 1)

    def test_bad_direct_protocol_does_not_create_integration(self):
        status, data = self.request("/api/integrations", {"type": "hisense", "name": "TV", "host": "192.168.1.50", "connectionMode": "direct", "protocol": "arbitrary-shell"})
        self.assertEqual(status, 400, data)
        self.assertEqual(self.app.integrations(), [])

    def test_inflight_pairing_cannot_restore_credentials_after_port_change(self):
        tv, _ = self.save_tv(port=6466)
        def finish_after_endpoint_changes(*args, **kwargs):
            with self.app.vault.lock:
                stored = self.app.vault.data["integrations"][0]
                stored["port"] = 6468
                stored.pop("tvCredentials", None)
            return {"status": "paired", "protocol": "androidtv", "credentials": {"clientKey": "old-port-private-key"}}
        with patch("app.direct_tv.finish_pairing", side_effect=finish_after_endpoint_changes):
            status, data = self.request(f"/api/tvs/{tv['id']}/pair/finish", {"approved": True, "pairingId": "fixture-session", "code": "ABC123"})
        self.assertEqual(status, 409, data)
        self.assertNotIn("tvCredentials", self.app.integration(tv["id"]))
        self.assertNotIn("old-port-private-key", json.dumps(data))

    def test_type_change_clears_tv_credentials_and_non_tv_cannot_pair(self):
        tv, body = self.save_tv()
        with self.app.vault.lock:
            self.app.vault.data["integrations"][0]["tvCredentials"] = {"clientKey": "first-tv-private-key"}
        status, data = self.request("/api/integrations", {"id": tv["id"], "type": "server", "name": "Changed type", "host": "192.168.1.60", "connectionMode": "direct"})
        self.assertEqual(status, 200, data)
        self.assertNotIn("tvCredentials", self.app.integration(tv["id"]))
        with patch("app.direct_tv.start_pairing") as start:
            self.assertEqual(self.request(f"/api/tvs/{tv['id']}/pair/start", {"approved": True})[0], 400)
            start.assert_not_called()
        self.request("/api/integrations", {**body, "id": tv["id"], "host": "192.168.1.60"})
        self.assertNotIn("tvCredentials", self.app.integration(tv["id"]))
