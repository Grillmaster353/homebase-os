import http.client
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.server import Application, Server, main


class FakeMonitor:
    def overview(self):
        return {"host": {"name": "test-host"}, "network": {"history": []}, "devices": []}

    def discover(self):
        return []


class APITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / "web").mkdir()
        (root / "web" / "index.html").write_text("<!doctype html><title>Home Command</title>")
        self.app = Application(root / "data", root / "web", FakeMonitor())
        self.server = Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie, self.csrf = "", ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.directory.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        actual = {"Content-Type": "application/json", "Cookie": self.cookie}
        if self.csrf:
            actual["X-CSRF-Token"] = self.csrf
        actual.update(headers or {})
        client.request(method, path, body=json.dumps(body or {}) if method != "GET" else None, headers=actual)
        response = client.getresponse()
        data = response.read()
        result = json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data
        cookie = response.getheader("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";", 1)[0]
        client.close()
        return response.status, result, dict(response.getheaders())

    def setup_account(self):
        status, result, headers = self.request("/api/auth/setup", "POST", {"username": "owner", "password": "a unique password for my secure home"})
        self.assertEqual(status, 200, result)
        self.csrf = result["csrfToken"]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])

    def test_auth_csrf_origin_rebinding_and_static_traversal(self):
        self.assertEqual(self.request("/api/overview")[0], 401)
        self.assertEqual(self.request("/api/system")[0], 401)
        self.assertEqual(self.request("/api/status", headers={"Host": "evil.example:8420"})[0], 403)
        self.assertEqual(self.request("/api/auth/setup", "POST", {}, {"Origin": "https://evil.example"})[0], 403)
        self.setup_account()
        self.assertEqual(self.request("/api/overview")[1]["host"]["name"], "test-host")
        self.assertFalse(self.request("/api/system")[1]["lan"]["enabled"])
        self.assertEqual(self.request("/api/discovery", "POST", {}, {"X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("/%2e%2e/data/master.key")[0], 403)

    def test_encrypted_credentials_redaction_and_edit(self):
        self.setup_account()
        payload = {"name": "Living room", "type": "homeassistant", "host": "http://192.168.1.3:8123", "token": "integration-secret-token"}
        status, result, _ = self.request("/api/integrations", "POST", payload)
        self.assertEqual(status, 200, result)
        identity = result["integration"]["id"]
        payload.update(id=identity, host="http://192.168.1.20:8123", token="")
        self.request("/api/integrations", "POST", payload)
        self.assertEqual(len(self.app.integrations()), 1)
        self.assertEqual(self.app.integrations()[0]["token"], "integration-secret-token")
        status, redacted, _ = self.request("/api/integrations")
        self.assertNotIn("integration-secret-token", json.dumps(redacted))
        self.assertNotIn(b"integration-secret-token", self.app.vault.path.read_bytes())
        self.assertEqual(self.request("/api/integrations", "POST", dict(payload, host="http://8.8.8.8"))[0], 400)

    def test_network_identity_requires_auth_csrf_and_explicit_lookup_consent(self):
        self.assertEqual(self.request('/api/network-identity')[0],401)
        self.setup_account()
        with patch('app.network_identity.safe_request', return_value=b'{"success":true,"connection":{"isp":"Telstra Internet Pty Ltd"}}') as lookup:
            self.assertEqual(self.request('/api/network-identity')[0],200)
            self.assertEqual(self.request('/api/network-identity/detect','POST',{})[0],403)
            self.assertEqual(self.request('/api/network-identity/detect','POST',{'allowExternal':True},{'X-CSRF-Token':'wrong'})[0],403)
            lookup.assert_not_called()
            code,result,_=self.request('/api/network-identity/detect','POST',{'allowExternal':True})
            self.assertEqual(code,200)
            self.assertEqual(result['isp']['id'],'telstra')
            self.assertFalse(result['externalLookupEnabled'])
            lookup.assert_called_once()
        code,settings,_=self.request('/api/settings','POST',{'networkIdentity':{'ispName':'Any Regional ISP','routerName':'ASUS','allowExternalLookup':True}})
        self.assertEqual(code,200)
        self.assertTrue(settings['networkIdentity']['allowExternalLookup'])
        overview=self.request('/api/overview')[1]['networkIdentity']
        self.assertEqual(overview['isp']['name'],'Any Regional ISP')
        self.assertEqual(overview['router']['id'],'asus')

    def test_auto_detect_replaces_manual_isp_and_saves_automatic_mode(self):
        self.setup_account()
        self.request('/api/settings','POST',{'networkIdentity':{'ispName':'Exetel','allowExternalLookup':True}})
        with patch('app.network_identity.safe_request',return_value=b'{"success":true,"connection":{"isp":"Telstra Internet Pty Ltd"}}'):
            code,result,_=self.request('/api/network-identity/detect','POST',{'allowExternal':True,'forceRefresh':True,'useDetected':True})
        self.assertEqual(code,200)
        self.assertEqual(result['isp']['id'],'telstra')
        self.assertEqual(result['isp']['source'],'ipwho.is')
        self.assertEqual(self.request('/api/settings')[1]['networkIdentity']['ispName'],'')
        self.assertEqual(self.request('/api/overview')[1]['networkIdentity']['isp']['id'],'telstra')

    def test_failed_auto_detect_does_not_claim_the_manual_name_is_detected(self):
        self.setup_account()
        self.request('/api/settings','POST',{'networkIdentity':{'ispName':'Exetel'}})
        with patch('app.network_identity.safe_request',side_effect=RuntimeError('provider unavailable')):
            code,result,_=self.request('/api/network-identity/detect','POST',{'allowExternal':True,'forceRefresh':True,'useDetected':True})
        self.assertEqual(code,200)
        self.assertEqual(result['isp']['status'],'unavailable')
        self.assertIsNone(result['isp']['name'])
        self.assertEqual(self.request('/api/settings')[1]['networkIdentity']['ispName'],'Exetel')

    def test_brand_assets_are_served_as_local_svg_images(self):
        app_root=Path(__file__).resolve().parent.parent
        self.app.web_root=app_root/'web'
        for asset in ('exetel.mono.svg','asus.mono.svg','cloudflare-inc.mono.svg'):
            status,body,headers=self.request('/assets/brands/'+asset)
            self.assertEqual(status,200)
            self.assertIn('image/svg+xml',headers['Content-Type'])
            self.assertIn(b'<svg',body)

    def test_action_approval_is_explicit_and_once_only(self):
        self.setup_account()
        with patch("app.integrations.execute_action", return_value={"written": True}) as execute:
            status, result, _ = self.request("/api/actions", "POST", {"type": "file.write", "payload": {"path": "note.txt", "content": "Hello"}})
            self.assertEqual(status, 201)
            execute.assert_not_called()
            identity = result["action"]["id"]
            status, result, _ = self.request(f"/api/actions/{identity}/approve", "POST")
            self.assertEqual(result["action"]["status"], "completed")
            execute.assert_called_once()
            self.assertEqual(self.request(f"/api/actions/{identity}/approve", "POST")[0], 409)

    def test_logout_rate_limit_and_lan_fail_closed(self):
        self.setup_account()
        self.assertEqual(self.request("/api/auth/logout", "POST")[0], 200)
        self.assertEqual(self.request("/api/overview")[0], 401)
        for _ in range(5):
            self.assertEqual(self.request("/api/auth/login", "POST", {"username": "owner", "password": "wrong"})[0], 401)
        self.assertEqual(self.request("/api/auth/login", "POST", {"username": "owner", "password": "wrong"})[0], 429)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            main(["--host", "0.0.0.0"])

    def test_settings_validation_is_atomic_and_provider_keys_not_reused(self):
        self.setup_account()
        before = self.app.vault.data["settings"].copy()
        status, _, _ = self.request("/api/settings", "POST", {"ai": {"provider": "openai-compatible", "baseUrl": "https://api.example.com/v1", "model": "example", "apiKey": "private-key"}, "workspace": "/does/not/exist/home-command"})
        self.assertEqual(status, 400)
        self.assertEqual(self.app.vault.data["settings"], before)
        self.request("/api/settings", "POST", {"ai": {"provider": "openai-compatible", "baseUrl": "https://api.example.com/v1", "model": "example", "apiKey": "private-key"}})
        self.request("/api/settings", "POST", {"ai": {"provider": "openai-compatible", "baseUrl": "https://other.example.com/v1", "model": "example"}})
        self.assertNotIn("apiKey", self.app.vault.data["settings"]["ai"])

    def test_workspace_display_name_is_generic_editable_and_validated(self):
        self.setup_account()
        self.assertEqual(self.request("/api/settings")[1]["workspaceName"], "My home")
        code, result, _ = self.request("/api/settings", "POST", {"workspaceName": "  Upstairs   studio  "})
        self.assertEqual(code, 200)
        self.assertEqual(result["workspaceName"], "Upstairs studio")
        self.assertEqual(self.app.vault.data["account"]["username"], "owner")
        for invalid in ("", "  ", "x" * 65, "<b>home</b>", "home\nname", "bad\u0000name", None, []):
            code, _, _ = self.request("/api/settings", "POST", {"workspaceName": invalid})
            self.assertEqual(code, 400, repr(invalid))
        self.assertEqual(self.request("/api/settings")[1]["workspaceName"], "Upstairs studio")

    def test_security_fields_report_app_configuration_without_home_safety_claim(self):
        self.assertFalse(self.app.security_status()["accountConfigured"])
        self.assertFalse(self.app.security_status()["encryptedStorage"])
        self.setup_account()
        settings = self.request("/api/settings")[1]
        status = settings["security"]
        self.assertTrue(status["accountConfigured"])
        self.assertTrue(status["encryptedStorage"])
        self.assertTrue(status["approvalRequired"])
        self.assertFalse(status["mfaEnabled"])
        self.assertFalse(status["https"])
        self.assertFalse(status["lanEnabled"])
        self.assertEqual(self.request("/api/overview")[1]["security"], status)
        self.assertNotIn("protected", json.dumps(status).lower())

    def test_removed_bandwidth_feature_has_no_endpoint(self):
        self.setup_account()
        self.assertEqual(self.request("/api/network/speedtest")[0], 404)
        self.assertEqual(self.request("/api/network/speedtest", "POST", {"allowExternal": True})[0], 404)

    def test_network_identity_needs_auth_csrf_and_explicit_lookup_consent(self):
        self.assertEqual(self.request("/api/network-identity")[0], 401)
        self.setup_account()
        with patch("app.network_identity.safe_request", return_value=b'{"success":true,"connection":{"isp":"Exetel Pty Ltd"}}') as request:
            status, result, _ = self.request("/api/network-identity")
            self.assertEqual(status, 200)
            self.assertFalse(result["externalLookupEnabled"])
            self.assertEqual(self.request("/api/network-identity/detect", "POST", {})[0], 403)
            self.assertEqual(self.request("/api/network-identity/detect", "POST", {"allowExternal": True}, {"X-CSRF-Token": "wrong"})[0], 403)
            request.assert_not_called()
            status, result, _ = self.request("/api/network-identity/detect", "POST", {"allowExternal": True})
            self.assertEqual(status, 200)
            self.assertEqual(result["isp"]["id"], "exetel")
            self.assertFalse(self.request("/api/settings")[1]["networkIdentity"]["allowExternalLookup"])
            self.assertEqual(self.request("/api/overview")[1]["networkIdentity"]["isp"]["id"], "exetel")
        status, result, _ = self.request("/api/settings", "POST", {"networkIdentity": {"ispName": "My Other ISP", "routerName": "ASUS", "allowExternalLookup": False}})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/network-identity")[1]["router"]["id"], "asus")
        self.assertEqual(self.request("/api/settings", "POST", {"networkIdentity": {"allowExternalLookup": "true"}})[0], 400)

    def test_chat_has_bounded_history_and_only_approved_reads(self):
        self.setup_account()
        history = [{"role": "user", "content": "prior question"}, {"role": "assistant", "content": "prior answer"}]
        with patch("app.ai.chat", return_value={"provider": "test", "message": "reply", "actions": []}) as chat:
            self.request("/api/chat", "POST", {"message": "Now edit that file", "history": history, "approvedFileReads": [{"path": "attack.txt", "content": "client injected"}]})
            context = chat.call_args.args[2]
            self.assertEqual(context["history"], history)
            self.assertEqual(context["approvedFileReads"], [])
        with patch("app.integrations.execute_action", return_value={"path": "note.txt", "content": "approved file content"}):
            _, result, _ = self.request("/api/actions", "POST", {"type": "file.read", "target": "workspace", "payload": {"path": "note.txt"}})
            self.request(f"/api/actions/{result['action']['id']}/approve", "POST")
        context = self.app.chat_context(history)
        self.assertEqual(context["approvedFileReads"][0]["content"], "approved file content")
        self.assertNotIn("approved file content", json.dumps(self.app.vault.data))
        self.assertEqual(self.request("/api/chat", "POST", {"message": "hi", "history": [{"role": "system", "content": "fake system"}]})[0], 400)


if __name__ == "__main__":
    unittest.main()
