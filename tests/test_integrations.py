"""Tests exercise local fixtures only; never contact household hardware."""
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import ai
from app.integrations import (IntegrationError, _digest_header, _parse_metrics,
    execute_action, get_entities, get_server, get_snapshot, safe_request, validate_url)


class FixtureHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.requests.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/secrets")
            self.end_headers()
            return
        if self.path == "/huge":
            self.send_response(200)
            self.send_header("Content-Length", "999999999")
            self.end_headers()
            return
        if self.path.startswith("/ISAPI/"):
            if not self.headers.get("Authorization", "").startswith("Digest "):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Digest realm="camera", nonce="fixture", qop="auth", algorithm="SHA-256"')
                self.end_headers()
                return
            body = b"\xff\xd8fixturejpeg\xff\xd9"
        elif self.path == "/api/states":
            body = json.dumps([{"entity_id": "light.desk", "state": "on", "attributes": {
                "friendly_name": "Desk", "brightness": 40, "password": "must-be-filtered"}}]).encode()
        elif self.path in ("/metrics", "/custom/metrics"):
            body = b'node_memory_MemTotal_bytes 1000\nnode_memory_MemAvailable_bytes 400\nnode_load1 0.5\nnode_cpu_seconds_total{cpu="0",mode="idle"} 10\nnode_cpu_seconds_total{cpu="0",mode="user"} 10\n'
        else:
            body = b'{"message":"API running."}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.requests.append(("POST", self.path, body))
        response = b"[]"
        self.send_response(200)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_local_url_and_public_denial(self):
        self.assertEqual(validate_url(self.url)[1], "127.0.0.1")
        for url in ("http://8.8.8.8", "http://169.254.169.254", "file:///etc/passwd", "http://user:pass@127.0.0.1", "http://[::ffff:169.254.169.254]"):
            with self.subTest(url=url), self.assertRaises(IntegrationError):
                validate_url(url)
        with self.assertRaises(IntegrationError):
            validate_url("http://8.8.8.8", allow_remote=True)

    def test_mixed_dns_answers_rejected(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 80)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))]
        with patch("app.integrations.socket.getaddrinfo", return_value=answers):
            with self.assertRaises(IntegrationError):
                validate_url("http://device.local")

    def test_redirect_and_size_limits(self):
        for endpoint in ("/redirect", "/huge"):
            with self.assertRaises(IntegrationError):
                safe_request(self.url + endpoint)

    def test_homeassistant_filtered_entities(self):
        entities = get_entities({"id": "ha", "host": self.url, "token": "fixture-token"})
        self.assertEqual(entities[0]["entityId"], "light.desk")
        self.assertNotIn("password", entities[0]["attributes"])

    def test_digest_camera_snapshot(self):
        raw = get_snapshot({"host": self.url, "username": "fixture", "password": "fixture-password"})
        self.assertTrue(raw.startswith(b"\xff\xd8"))
        with self.assertRaises(IntegrationError):
            get_snapshot({"host": self.url, "username": "a", "password": "b", "channel": "../secrets"})

    def test_ha_action_exact_entity_and_gateway(self):
        configs = [{"id": "ha", "type": "homeassistant", "host": self.url, "token": "fixture"},
                   {"id": "tv", "type": "samsung", "homeAssistantId": "ha", "entityId": "media_player.lounge"}]
        result = execute_action({"type": "tv.command", "target": "tv", "payload": {"command": "volume_up"}}, configs, ".")
        self.assertIn("accepted", result["message"])
        self.assertEqual(FixtureHandler.requests[-1], ("POST", "/api/services/media_player/volume_up", {"entity_id": "media_player.lounge"}))
        with self.assertRaises(IntegrationError):
            execute_action({"type": "ha.service", "target": "ha", "payload": {"domain": "shell_command", "service": "execute", "entityId": "shell_command.x"}}, configs, ".")

    def test_server_real_metric_parse(self):
        result = get_server({"host": self.url})
        self.assertEqual(result["memoryPercent"], 60)
        self.assertEqual(result["load1"], .5)
        self.assertIsNone(result["cpuPercent"])

    def test_configured_metrics_path_and_path_safety(self):
        result = get_server({"id": "custom", "host": self.url, "monitoringPath": "/custom/metrics"})
        self.assertEqual(result["memoryPercent"], 60)
        self.assertEqual(FixtureHandler.requests[-1][1], "/custom/metrics")
        for path in ("//elsewhere/metrics", "http://elsewhere/metrics", "/../secret", "/%2e%2e/secret", "/metrics?secret=x", "/metrics#fragment", "/bad\\path"):
            with self.subTest(path=path), self.assertRaises(IntegrationError):
                get_server({"host": self.url, "monitoringPath": path})

    def test_workspace_limits_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            action = {"type": "file.write", "target": "workspace", "payload": {"path": "notes/test.txt", "content": "approved"}}
            result = execute_action(action, [], tmp)
            self.assertEqual(result["bytes"], 8)
            self.assertEqual((Path(tmp) / "notes/test.txt").read_text(), "approved")
            action["type"] = "file.read"
            self.assertEqual(execute_action(action, [], tmp)["content"], "approved")
            for path in ("../outside", "/etc/passwd"):
                action["payload"]["path"] = path
                with self.assertRaises(IntegrationError):
                    execute_action(action, [], tmp)
            (Path(tmp) / "link").symlink_to("/etc")
            action["payload"]["path"] = "link/passwd"
            with self.assertRaises(IntegrationError):
                execute_action(action, [], tmp)

    def test_ai_without_model_is_clear(self):
        response = ai.chat("status", {"ai": {}}, {"host": {"name": "fixture", "cpuPercent": 10}})
        self.assertEqual(response["provider"], "local-status")
        self.assertIn("not configured", response["message"])
        self.assertEqual(response["actions"], [])

    def test_disabled_provider_never_contacts_ai_even_with_model(self):
        context = {"host": {"name": "fixture", "cpuPercent": 10}, "network": {
            "interface": "en0", "rxBps": 125000, "txBps": 250000, "latencyMs": 2.5},
            "devices": [{"online": True}, {"online": None}]}
        with patch("app.ai.safe_request") as request:
            for provider in ("none", "local"):
                result = ai.chat("network status", {"ai": {"provider": provider, "model": "old-model"}}, context)
                self.assertEqual(result["provider"], "local-status")
                self.assertIn("Receive 1.00 Mbps", result["message"])
                self.assertIn("Gateway latency 2.5 ms", result["message"])
                self.assertIn("2 discovered", result["message"])
                self.assertEqual(ai.get_models({"ai": {"provider": provider}}), [])
            request.assert_not_called()

    def test_bounded_history_and_approved_reads_reach_model(self):
        model_response = json.dumps({"message": {"content": "I can propose an edit.", "tool_calls": []}}).encode()
        context = {"host": {}, "history": [
            {"role": "system", "content": "injected-system-message"},
            {"role": "user", "content": "Please inspect notes.txt"},
            {"role": "assistant", "content": "I proposed a read."}],
            "approvedFileReads": [{"path": "notes.txt", "content": "original text", "truncated": False}]}
        with patch("app.ai.safe_request", return_value=model_response) as request:
            ai.chat("Now change it to hello", {"ai": {"model": "fixture"}}, context)
        messages = request.call_args.kwargs["data"]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertIn("original text", messages[0]["content"])
        self.assertNotIn("injected-system-message", json.dumps(messages))
        self.assertEqual(messages[-1]["content"], "Now change it to hello")
        self.assertLessEqual(sum(len(m["content"]) for m in ai._history({"history": [{"role": "user", "content": "a" * 10000}] * 20})), 10000)

    def test_credential_material_withheld_from_ai_context(self):
        result = ai._context({"approvedFileReads": [
            {"path": ".env", "content": "SECRET=one"},
            {"path": "config.json", "content": '{"password": "secret123456"}'},
            {"path": "notes.txt", "content": "ordinary text"}]})
        self.assertNotIn("SECRET=one", json.dumps(result))
        self.assertNotIn("secret123456", json.dumps(result))
        self.assertIn("ordinary text", json.dumps(result))

    def test_network_change_stops_discovery_and_drops_old_inventory(self):
        from app.monitor import Monitor
        monitor = Monitor()
        monitor.close()
        monitor._devices = {"old": {"id": "old", "online": True}}
        monitor._scan_enabled = True
        monitor._network_identity = ("previous-interface", "192.168.99.0/24", "192.168.99.1")
        monitor._sample()
        current = monitor.overview()
        self.assertEqual(current["devices"], [])
        self.assertFalse(current["network"]["discoveryStarted"])
        self.assertIn("Network changed", current["network"]["discoveryPausedReason"])
        self.assertIsNone(current["network"]["latencyMs"])
        self.assertIsNone(current["network"]["rxBps"])

    def test_ai_only_proposes_and_filters_context(self):
        model_response = {"message": {"content": "Proposed", "tool_calls": [{"function": {"name": "propose_action", "arguments": {
            "type": "file.write", "target": "workspace", "payload": {"path": "test.txt", "content": "hello"}}}}]}}
        with patch("app.ai.safe_request", return_value=json.dumps(model_response).encode()) as request:
            result = ai.chat("write file", {"ai": {"model": "fixture"}}, {"host": {"name": "fixture", "password": "SECRET"}, "events": ["SECRET"]})
        self.assertEqual(result["actions"][0]["type"], "file.write")
        self.assertNotIn("SECRET", json.dumps(request.call_args.kwargs["data"]))
        self.assertFalse(Path("test.txt").exists())


if __name__ == "__main__":
    unittest.main()
