"""Manual TV authorization contract; loopback API fixtures, no TV transports."""
import json
import threading
import unittest
from unittest.mock import patch

from tests import test_tv_api as tv_fixtures


class ManualTVTests(unittest.TestCase):
    setUp = tv_fixtures.TVAPITests.setUp
    tearDown = tv_fixtures.TVAPITests.tearDown
    request = tv_fixtures.TVAPITests.request
    save_tv = tv_fixtures.TVAPITests.save_tv

    def paired_tv(self, **overrides):
        tv, body = self.save_tv(**overrides)
        with self.app.vault.lock:
            stored = next(item for item in self.app.vault.data["integrations"] if item["id"] == tv["id"])
            stored.update(tvCredentials={"certificate":"fixture-certificate", "clientKey":"fixture-private-key"}, status="paired")
        return tv, body

    @staticmethod
    def command_path(tv):
        return f"/api/tvs/{tv['id']}/command"

    def test_manual_control_defaults_off_and_pairing_alone_does_not_enable_it(self):
        tv, _ = self.paired_tv()
        self.assertIs(tv["manualControlEnabled"], False)
        with patch("app.direct_tv.execute_tv_command") as execute:
            status, data = self.request(self.command_path(tv), {"command":"home"})
        self.assertEqual(status, 403, data)
        self.assertEqual(data["error"], "manual_control_disabled")
        execute.assert_not_called()

    def test_manual_command_requires_authentication_and_csrf_even_with_saved_grant(self):
        tv, _ = self.paired_tv(manualControlEnabled=True)
        cookie = self.cookie
        with patch("app.direct_tv.execute_tv_command") as execute:
            self.cookie = ""
            status, data = self.request(self.command_path(tv), {"command":"home"})
            self.assertEqual(status, 401, data)
            self.cookie = cookie
            status, data = self.request(self.command_path(tv), {"command":"home"}, csrf=False)
            self.assertEqual(status, 403, data)
            execute.assert_not_called()

    def test_grant_does_not_replace_pairing_and_status_text_does_not_prove_pairing(self):
        tv, _ = self.save_tv(manualControlEnabled=True)
        with self.app.vault.lock:
            self.app.vault.data["integrations"][0]["status"] = "paired"
        with patch("app.direct_tv.execute_tv_command") as execute:
            status, data = self.request(self.command_path(tv), {"command":"home"})
        self.assertEqual(status, 403, data)
        self.assertEqual(data["error"], "manual_control_disabled")
        execute.assert_not_called()

    def test_manual_grant_only_accepts_real_json_booleans(self):
        for value in ("true", "false", 1, 0, None):
            with self.subTest(value=value):
                status, data = self.request("/api/integrations", {"type":"androidtv", "protocol":"androidtv",
                    "connectionMode":"direct", "host":"192.168.1.50", "name":"Fixture TV",
                    "manualControlEnabled":value})
                self.assertEqual(status, 400, data)
        self.assertEqual(self.app.integrations(), [])

    def test_allowed_button_sends_once_without_creating_an_approval(self):
        tv, _ = self.paired_tv(manualControlEnabled=True)
        # A read-only connection check can replace the display status while the
        # encrypted paired identity remains valid.
        with self.app.vault.lock:
            self.app.vault.data["integrations"][0]["status"] = "available"
        with patch("app.direct_tv.execute_tv_command", return_value={"message":"Command sent", "protocol":"androidtv"}) as execute:
            status, data = self.request(self.command_path(tv), {"command":"home"})
        self.assertEqual(status, 200, data)
        self.assertEqual(set(data), {"message", "protocol"})
        self.assertEqual(data["protocol"], "androidtv")
        self.assertIn("home", data["message"])
        self.assertIn("Fixture TV", data["message"])
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0]["id"], tv["id"])
        self.assertEqual(execute.call_args.args[1], {"command":"home"})
        self.assertIs(execute.call_args.kwargs["approved"], True)
        self.assertEqual(self.app.vault.data["actions"], [])
        self.assertNotIn("fixture-private-key", json.dumps(data))

    def test_unsupported_or_expanded_command_payloads_fail_before_transport(self):
        tv, _ = self.paired_tv(manualControlEnabled=True)
        payloads = ({"command":"factory_reset"}, {"command":""}, {"command":None},
            {"command":["home"]}, {"command":True}, {},
            {"command":"home", "approved":True}, {"command":"home", "host":"192.168.1.99"})
        with patch("app.direct_tv.execute_tv_command") as execute:
            for body in payloads:
                with self.subTest(body=body):
                    status, data = self.request(self.command_path(tv), body)
                    self.assertEqual(status, 400, data)
            execute.assert_not_called()

    def test_text_input_is_bounded_and_not_stored_in_activity_or_approval_records(self):
        tv,_=self.paired_tv(manualControlEnabled=True)
        with patch('app.direct_tv.execute_tv_command',return_value={'protocol':'androidtv'}) as execute:
            status,data=self.request(self.command_path(tv),{'command':'text','text':'private search fixture'})
            self.assertEqual(status,200,data)
            self.assertEqual(execute.call_args.args[1],{'command':'text','text':'private search fixture'})
            self.assertNotIn('private search fixture',json.dumps(self.app.overview()))
            self.assertEqual(self.app.vault.data['actions'],[])
        for text in ('', 'x'*513, 'line\nbreak', '\x00', '\ud800'):
            with patch('app.direct_tv.execute_tv_command') as execute:
                status,data=self.request(self.command_path(tv),{'command':'text','text':text})
                self.assertEqual(status,400,data)
                execute.assert_not_called()

    def test_non_direct_or_non_tv_integrations_cannot_use_manual_route(self):
        for changes in ({"connectionMode":"homeassistant"}, {"type":"server"}):
            with self.subTest(changes=changes):
                tv, _ = self.paired_tv(manualControlEnabled=True)
                with self.app.vault.lock:
                    stored = next(item for item in self.app.vault.data["integrations"] if item["id"] == tv["id"])
                    stored.update(changes)
                with patch("app.direct_tv.execute_tv_command") as execute:
                    status, data = self.request(self.command_path(tv), {"command":"home"})
                self.assertEqual(status, 403, data)
                self.assertEqual(data["error"], "manual_control_disabled")
                execute.assert_not_called()

    def test_endpoint_changes_clear_the_grant_and_existing_paired_identity(self):
        for changes in ({"host":"192.168.1.51"}, {"protocol":"roku"}, {"apiPort":6468},
                {"pairPort":6469}, {"port":6468}, {"type":"hisense"}, {"connectionMode":"homeassistant"}):
            with self.subTest(changes=changes):
                tv, body = self.paired_tv(manualControlEnabled=True, apiPort=6466, pairPort=6467, port=6466)
                body.pop("manualControlEnabled")
                status, data = self.request("/api/integrations", {**body, "id":tv["id"], **changes})
                self.assertEqual(status, 200, data)
                self.assertIs(data["integration"]["manualControlEnabled"], False)
                self.assertNotIn("tvCredentials", self.app.integration(tv["id"]))

    def test_explicit_grant_after_endpoint_change_still_requires_new_pairing(self):
        tv, body = self.paired_tv(manualControlEnabled=True)
        status, data = self.request("/api/integrations", {**body, "id":tv["id"], "host":"192.168.1.51", "manualControlEnabled":True})
        self.assertEqual(status, 200, data)
        self.assertIs(data["integration"]["manualControlEnabled"], True)
        with patch("app.direct_tv.execute_tv_command") as execute:
            status, data = self.request(self.command_path(tv), {"command":"home"})
        self.assertEqual(status, 403, data)
        execute.assert_not_called()

    def test_name_only_edit_preserves_grant_and_explicit_false_revokes_it(self):
        tv, body = self.paired_tv(manualControlEnabled=True)
        body.pop("manualControlEnabled")
        status, data = self.request("/api/integrations", {**body, "id":tv["id"], "name":"Renamed TV"})
        self.assertEqual(status, 200, data)
        self.assertIs(data["integration"]["manualControlEnabled"], True)
        self.assertTrue(self.app.integration(tv["id"]).get("tvCredentials"))
        status, data = self.request("/api/integrations", {**body, "id":tv["id"], "manualControlEnabled":False})
        self.assertEqual(status, 200, data)
        with patch("app.direct_tv.execute_tv_command") as execute:
            self.assertEqual(self.request(self.command_path(tv), {"command":"home"})[0], 403)
            execute.assert_not_called()

    def test_profile_change_revokes_grant_and_pairing(self):
        with patch("app.vidaa.validate_profile", side_effect=lambda value:value):
            tv, body = self.paired_tv(type="hisense", protocol="vidaa", manualControlEnabled=True,
                vidaaProfile=json.dumps({"clientId":"fixture-client", "password":"old-fixture-password"}))
            body.pop("manualControlEnabled")
            status, data = self.request("/api/integrations", {**body, "id":tv["id"],
                "vidaaProfile":json.dumps({"clientId":"fixture-client", "password":"new-fixture-password"})})
        self.assertEqual(status, 200, data)
        self.assertIs(data["integration"]["manualControlEnabled"], False)
        self.assertNotIn("tvCredentials", self.app.integration(tv["id"]))

    def test_assistant_proposals_still_wait_for_individual_approval(self):
        tv, _ = self.paired_tv(manualControlEnabled=True)
        with patch("app.direct_tv.execute_tv_command", return_value={"message":"Approved command sent"}) as execute:
            proposal = self.app.propose({"type":"tv.command", "target":tv["id"], "payload":{"command":"home"}}, source="assistant")
            self.assertEqual(proposal["status"], "pending")
            execute.assert_not_called()
            status, data = self.request(f"/api/actions/{proposal['id']}/approve", {})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["action"]["status"], "completed")
        execute.assert_called_once()
        self.assertIs(execute.call_args.kwargs["approved"], True)

    def test_manual_rate_limit_and_busy_slots_fail_without_transport(self):
        tv, _ = self.paired_tv(manualControlEnabled=True)
        with patch.object(self.app.tv_manual_limiter, "permit", return_value=False), patch("app.direct_tv.execute_tv_command") as execute:
            status, data = self.request(self.command_path(tv), {"command":"home"})
            self.assertEqual(status, 429, data)
            execute.assert_not_called()
        self.app.tv_manual_slots = threading.BoundedSemaphore(1)
        self.app.tv_manual_slots.acquire()
        try:
            with patch("app.direct_tv.execute_tv_command") as execute:
                status, data = self.request(self.command_path(tv), {"command":"home"})
                self.assertEqual(status, 429, data)
                execute.assert_not_called()
        finally:
            self.app.tv_manual_slots.release()

    def test_two_buttons_for_same_tv_do_not_overlap_and_busy_request_releases_slot(self):
        tv, _ = self.paired_tv(manualControlEnabled=True)
        entered, release = threading.Event(), threading.Event()
        responses = []
        def slow_command(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Fixture transport was not released")
            return {"message":"Sent", "protocol":"androidtv"}
        first = threading.Thread(target=lambda:responses.append(self.request(self.command_path(tv), {"command":"home"})))
        with patch("app.direct_tv.execute_tv_command", side_effect=slow_command) as execute:
            try:
                first.start()
                self.assertTrue(entered.wait(2), "First fixture command did not start")
                status, data = self.request(self.command_path(tv), {"command":"home"})
                self.assertEqual(status, 429, data)
                self.assertEqual(execute.call_count, 1)
            finally:
                release.set()
                first.join(3)
            self.assertFalse(first.is_alive())
            self.assertEqual(responses[0][0], 200, responses)
            status, data = self.request(self.command_path(tv), {"command":"home"})
            self.assertEqual(status, 200, data)
            self.assertEqual(execute.call_count, 2)

    def test_grant_revoked_during_precheck_is_rechecked_before_transport(self):
        tv, _ = self.paired_tv(manualControlEnabled=True)
        previous = self.app.integration(tv["id"])
        revoked = {**previous, "manualControlEnabled":False}
        with patch.object(self.app, "integration", side_effect=[previous, revoked]), patch("app.direct_tv.execute_tv_command") as execute:
            status, data = self.request(self.command_path(tv), {"command":"home"})
        self.assertEqual(status, 403, data)
        execute.assert_not_called()

    def test_transport_error_is_scrubbed_and_does_not_leak_manual_lock_or_slot(self):
        tv, _ = self.paired_tv(manualControlEnabled=True)
        with patch("app.direct_tv.execute_tv_command", side_effect=ValueError("fixture-private-key rejected")):
            status, data = self.request(self.command_path(tv), {"command":"home"})
        self.assertEqual(status, 502, data)
        self.assertNotIn("fixture-private-key", json.dumps(data))
        with patch("app.direct_tv.execute_tv_command", return_value={"message":"Sent", "protocol":"androidtv"}):
            status, data = self.request(self.command_path(tv), {"command":"home"})
        self.assertEqual(status, 200, data)


if __name__ == "__main__":
    unittest.main()
