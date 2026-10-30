import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import time
import unittest
from unittest.mock import patch

from app import vidaa


CERT = "-----BEGIN CERTIFICATE-----\nfixture-public-certificate\n-----END CERTIFICATE-----"
KEY = "-----BEGIN PRIVATE KEY-----\nfixture-private-key\n-----END PRIVATE KEY-----"
LEAF = b"fixture-approved-server-DER-certificate"
CLIENT = "fixture-client$normal"


def profile(**overrides):
    return {"clientId": CLIENT, "username": "fixture-configured-user", "password": "fixture-configured-password",
            "certificatePem": CERT, "privateKeyPem": KEY,
            "serverFingerprint": hashlib.sha256(LEAF).hexdigest(), "authMode": "modern", **overrides}


def publication(suffix, payload=b"", *, retained=False, qos=0, client=CLIENT):
    if not isinstance(payload, bytes):
        payload = json.dumps(payload).encode()
    topic = "/remoteapp/mobile/" + client + "/" + suffix
    return vidaa._packet(0x30 | (qos << 1) | int(retained), vidaa._string(topic) + (b"\x00\x09" if qos else b"") + payload)


def preamble():
    return vidaa._packet(0x20, b"\x00\x00") + vidaa._packet(0x90, b"\x00\x01" + b"\x00" * len(vidaa._topics(CLIENT)))


class FakeSocket:
    def __init__(self, incoming=b"", chunk=7):
        self.incoming = bytearray(incoming)
        self.sent = []
        self.closed = False
        self.chunk = chunk
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, value):
        self.sent.append(value)

    def recv(self, size):
        if not self.incoming:
            raise socket.timeout("fixture-sensitive-message")
        size = min(size, self.chunk, len(self.incoming))
        value = bytes(self.incoming[:size])
        del self.incoming[:size]
        return value

    def getpeercert(self, binary_form=False):
        return LEAF

    def close(self):
        self.closed = True


def sent_packets(sock):
    result = []
    for data in sock.sent:
        packet = FakeSocket(data, chunk=1)
        handle = vidaa.PairingHandle(packet, {})
        result.append(handle._receive(time.monotonic() + 1))
    return result


def sent_publications(sock):
    result = []
    for header, body in sent_packets(sock):
        if header >> 4 != 3:
            continue
        length = struct.unpack("!H", body[:2])[0]
        offset = 2 + length
        topic = body[2:offset].decode()
        if (header >> 1) & 3:
            offset += 2
        result.append((topic, body[offset:]))
    return result


class VidaaTests(unittest.TestCase):
    def test_profile_validation_is_private_and_has_no_io(self):
        with patch("app.vidaa.socket.create_connection", side_effect=AssertionError("No network")), patch("app.vidaa.tempfile.TemporaryDirectory", side_effect=AssertionError("No files")):
            normalized = vidaa.validate_profile(profile(serverFingerprint=":".join(hashlib.sha256(LEAF).hexdigest()[i:i + 2].upper() for i in range(0, 64, 2))))
            self.assertEqual(normalized["serverFingerprint"], hashlib.sha256(LEAF).hexdigest())
            for invalid in (None, {}, profile(serverFingerprint=""), profile(serverFingerprint="wrong"), profile(clientId="bad/#"), profile(authMode=[]), profile(privateKeyPem="wrong"), profile(extra="unsupported")):
                with self.subTest(kind=type(invalid).__name__), self.assertRaises(vidaa.VidaaError) as caught:
                    vidaa.validate_profile(invalid)
                self.assertNotIn("fixture-configured-password", str(caught.exception))
                self.assertNotIn(KEY, str(caught.exception))

    def test_invalid_destination_is_refused_before_tls(self):
        with patch("app.vidaa._tls_connection") as connect:
            for address, port in (("8.8.8.8", 36669), ("tv.example", 36669), ("192.168.1.2", 0)):
                with self.assertRaises(vidaa.VidaaError):
                    vidaa.start_pairing(address, port, profile())
            connect.assert_not_called()

    def test_wrong_server_pin_closes_socket_before_mqtt_credentials(self):
        raw, secured = FakeSocket(), FakeSocket()
        class Context:
            check_hostname = True
            verify_mode = ssl.CERT_REQUIRED
            def load_cert_chain(self, cert, key, password):
                self.paths = [cert, key]
                for name in self.paths:
                    self_test.assertEqual(os.stat(name).st_mode & 0o777, 0o600)
                self_test.assertEqual(Path(key).read_text(), KEY)
            def wrap_socket(self, sock, server_hostname):
                self_test.assertTrue(all(not Path(name).exists() for name in self.paths))
                return secured
        self_test = self
        context = Context()
        with patch("app.vidaa.ssl.SSLContext", return_value=context), patch("app.vidaa.socket.create_connection", return_value=raw):
            with self.assertRaisesRegex(vidaa.VidaaError, "fingerprint"):
                vidaa.start_pairing("192.168.1.20", 36669, profile(serverFingerprint="00" * 32))
        self.assertTrue(secured.closed)
        self.assertEqual(secured.sent, [])
        self.assertTrue(all(not Path(name).exists() for name in context.paths))

    def test_ca_verification_never_falls_back_and_temporary_keys_are_removed(self):
        paths = []
        class Context:
            check_hostname = True
            verify_mode = ssl.CERT_REQUIRED
            def load_verify_locations(self, **kwargs):
                self_test.assertEqual(kwargs, {"cadata": CERT})
            def load_cert_chain(self, cert, key, password):
                paths.extend([cert, key])
                raise ssl.SSLError("fixture-private-key and fixture-configured-password")
        self_test = self
        context = Context()
        with patch("app.vidaa.ssl.SSLContext", return_value=context), patch("app.vidaa.socket.create_connection") as network:
            with self.assertRaisesRegex(vidaa.VidaaError, "could not be verified") as caught:
                vidaa.start_pairing("192.168.1.20", 36669, profile(caPem=CERT, serverFingerprint=""))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(paths)
        self.assertTrue(all(not Path(name).exists() for name in paths))
        self.assertNotIn("fixture-private-key", str(caught.exception))
        network.assert_not_called()

    def test_untrusted_ca_handshake_never_sends_mqtt_or_retries_unverified(self):
        raw = FakeSocket()
        class Context:
            check_hostname = True
            verify_mode = ssl.CERT_REQUIRED
            def load_verify_locations(self, **kwargs):
                pass
            def load_cert_chain(self, *args, **kwargs):
                pass
            def wrap_socket(self, *args, **kwargs):
                raise ssl.SSLCertVerificationError("untrusted fixture server")
        context = Context()
        with patch("app.vidaa.ssl.SSLContext", return_value=context), patch("app.vidaa.socket.create_connection", return_value=raw) as network:
            with self.assertRaisesRegex(vidaa.VidaaError, "could not be verified"):
                vidaa.start_pairing("192.168.1.20", 36669, profile(caPem=CERT, serverFingerprint=""))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        network.assert_called_once()
        self.assertEqual(raw.sent, [])
        self.assertTrue(raw.closed)

    def test_mqtt_remaining_length_and_fragmented_publication(self):
        payload = b"x" * 200
        sock = FakeSocket(publication("ui_service/data/state", payload, qos=1), chunk=1)
        handle = vidaa.PairingHandle(sock, {})
        message = handle._message(time.monotonic() + 1)
        self.assertEqual(message, (f"/remoteapp/mobile/{CLIENT}/ui_service/data/state", payload, False))
        self.assertEqual(sock.sent, [b"\x40\x02\x00\x09"])
        for invalid in (b"\x30\x80\x80\x80\x80", b"\x30\x80\x00", b"\x30\xff\xff\x7f"):
            with self.subTest(frame=invalid), self.assertRaises(vidaa.VidaaError):
                vidaa.PairingHandle(FakeSocket(invalid), {})._receive(time.monotonic() + 1)

    def test_modern_pairing_preserves_empty_pin_event_and_subscribes_first(self):
        sock = FakeSocket(preamble() + publication("ui_service/data/authentication"))
        with patch("app.vidaa._tls_connection", return_value=sock):
            handle = vidaa.start_pairing("192.168.1.20", 36669, profile())
        self.addCleanup(vidaa.close, handle)
        self.assertTrue(handle.pin_shown)
        self.assertFalse(handle.request_accepted)
        packets = sent_packets(sock)
        self.assertEqual([header for header, _ in packets], [0x10, 0x82, 0x30])
        self.assertTrue(packets[0][1].startswith(b"\x00\x04MQTT\x04\xc2"))
        topic, payload = sent_publications(sock)[0]
        self.assertTrue(topic.endswith("/actions/vidaa_app_connect"))
        self.assertEqual(json.loads(payload), {"app_version": 2, "connect_result": 0, "device_type": "Mobile App"})
        self.assertNotIn("fixture-configured", repr(handle))

    def test_request_ack_is_not_reported_as_pin_display(self):
        sock = FakeSocket(preamble() + publication("ui_service/data/vidaa_app_connect", {"connect_result": 1}))
        with patch("app.vidaa._tls_connection", return_value=sock):
            handle = vidaa.start_pairing("192.168.1.20", 36669, profile())
        self.assertTrue(handle.request_accepted)
        self.assertFalse(handle.pin_shown)
        self.assertEqual(vidaa.PAIR_TTL, 180)
        with self.assertRaisesRegex(vidaa.VidaaError, "four-digit"):
            vidaa.finish_pairing(handle, "")
        vidaa.close(handle)
        vidaa.close(handle)
        self.assertTrue(sock.closed)

    def test_denied_subscriptions_and_stale_pin_notifications_fail(self):
        denied = vidaa._packet(0x20, b"\x00\x00") + vidaa._packet(0x90, b"\x00\x01" + b"\x80" * len(vidaa._topics(CLIENT)))
        for incoming in (denied, preamble() + publication("ui_service/data/authentication", retained=True), preamble() + publication("ui_service/data/authentication", client="different-client")):
            sock = FakeSocket(incoming)
            with patch("app.vidaa._tls_connection", return_value=sock), self.assertRaises(vidaa.VidaaError):
                vidaa.start_pairing("192.168.1.20", 36669, profile())
            self.assertTrue(sock.closed)

    def test_denied_optional_state_topics_do_not_block_real_pin_trigger(self):
        # A real TV grants every authentication subscription but refuses its
        # direct/broadcast state topics to an unpaired client. Neither optional
        # permission is needed to receive and finish the PIN authentication.
        grants = bytes((0, 0, 0, 0, 0, 128, 0, 128))
        incoming = vidaa._packet(0x20, b"\x00\x00") + vidaa._packet(0x90, b"\x00\x01" + grants)
        sock = FakeSocket(incoming + publication("ui_service/data/authentication"))
        with patch("app.vidaa._tls_connection", return_value=sock):
            handle = vidaa.start_pairing("192.168.1.20", 36669, profile())
        self.addCleanup(vidaa.close, handle)
        self.assertTrue(handle.pin_shown)
        self.assertTrue(sent_publications(sock)[0][0].endswith("/actions/vidaa_app_connect"))
        for required_index in (0, 1, 2, 3, 4, 6):
            rejected = bytearray(grants)
            rejected[required_index] = 128
            denied = vidaa._packet(0x20, b"\x00\x00") + vidaa._packet(0x90, b"\x00\x01" + bytes(rejected))
            refused = FakeSocket(denied)
            with self.subTest(required_index=required_index), patch("app.vidaa._tls_connection", return_value=refused), self.assertRaisesRegex(vidaa.VidaaError, "required pairing subscriptions"):
                vidaa.start_pairing("192.168.1.20", 36669, profile())
            self.assertEqual(sent_publications(refused), [])
            self.assertTrue(refused.closed)

    def test_pin_acceptance_requires_modern_tokens_and_private_cleanup(self):
        tokens = {"accesstoken": "fixture-access", "refreshtoken": "fixture-refresh", "accesstoken_time": 1700000000, "accesstoken_duration_day": 30}
        sock = FakeSocket(publication("ui_service/data/authenticationcode", {"result": 1}) + publication("platform_service/data/tokenissuance", tokens))
        handle = vidaa.PairingHandle(sock, vidaa.validate_profile(profile()))
        result = vidaa.finish_pairing(handle, "0012")
        self.assertEqual(result["accessToken"], "fixture-access")
        self.assertEqual(result["refreshToken"], "fixture-refresh")
        self.assertEqual(result["accessTokenExpiresAt"], 1700000000 + 30 * 86400)
        self.assertNotIn("password", result)
        self.assertNotIn("privateKeyPem", result)
        self.assertTrue(handle.closed)
        self.assertEqual(handle._profile, {})
        pubs = sent_publications(sock)
        self.assertEqual(json.loads(pubs[0][1]), {"authNum": 12})
        self.assertIs(type(json.loads(pubs[0][1])["authNum"]), int)
        self.assertTrue(pubs[1][0].endswith("/data/gettoken"))
        self.assertTrue(pubs[2][0].endswith("/actions/authenticationcodeclose"))

    def test_pin_acceptance_without_tokens_is_never_modern_pairing_success(self):
        for suffix in (b"", publication("platform_service/data/tokenissuance", {"accesstoken": "fixture-access"}), publication("platform_service/data/tokenissuance", {"accesstoken": "fixture-access", "refreshtoken": "fixture-refresh"}, retained=True)):
            sock = FakeSocket(publication("ui_service/data/authenticationcode", {"result": 1}) + suffix)
            handle = vidaa.PairingHandle(sock, vidaa.validate_profile(profile()))
            with self.assertRaises(vidaa.VidaaError):
                vidaa.finish_pairing(handle, "1234")
            self.assertTrue(sock.closed)

    def test_token_before_pin_acceptance_cannot_confirm_new_pairing(self):
        sock = FakeSocket(publication("platform_service/data/tokenissuance", {"accesstoken": "old-access", "refreshtoken": "old-refresh"}) + publication("ui_service/data/authenticationcode", {"result": 1}))
        handle = vidaa.PairingHandle(sock, vidaa.validate_profile(profile()))
        with self.assertRaisesRegex(vidaa.VidaaError, "did not complete"):
            vidaa.finish_pairing(handle, "1234")
        self.assertTrue(sock.closed)

    def test_legacy_pairing_uses_read_only_state_trigger_and_can_be_tokenless(self):
        sock = FakeSocket(preamble() + publication("ui_service/data/authentication"))
        with patch("app.vidaa._tls_connection", return_value=sock):
            handle = vidaa.start_pairing("192.168.1.20", 36669, profile(authMode="legacy"))
        self.assertTrue(sent_publications(sock)[0][0].endswith("/actions/gettvstate"))
        sock.incoming.extend(publication("ui_service/data/authenticationcode", {"result": 1}))
        result = vidaa.finish_pairing(handle, "1234")
        self.assertEqual(result["authMode"], "legacy")
        self.assertTrue(result["paired"])
        self.assertNotIn("accessToken", result)
        self.assertFalse(any(topic.endswith("/data/gettoken") for topic, _ in sent_publications(sock)))

    def test_pin_validation_and_session_expiry_precede_network(self):
        sock = FakeSocket()
        handle = vidaa.PairingHandle(sock, vidaa.validate_profile(profile()))
        for value in ("123", "12345", "１２３４", "abcd", 1234):
            with self.assertRaises(vidaa.VidaaError):
                vidaa.finish_pairing(handle, value)
        self.assertEqual(sock.sent, [])
        handle._expires = time.monotonic() - 1
        with self.assertRaisesRegex(vidaa.VidaaError, "expired"):
            vidaa.finish_pairing(handle, "1234")
        self.assertTrue(sock.closed)

    def test_commands_use_saved_access_token_and_only_claim_delivery_ack(self):
        credentials = {"paired": True, "clientId": CLIENT, "username": "fixture-configured-user", "authMode": "modern", "accessToken": "fixture-saved-access"}
        sock = FakeSocket(vidaa._packet(0x20, b"\x00\x00") + vidaa._packet(0x40, b"\x00\x02"))
        with patch("app.vidaa._tls_connection", return_value=sock):
            result = vidaa.command("192.168.1.20", 36669, profile(), credentials, "back")
        self.assertTrue(result["acknowledged"])
        self.assertFalse(result["stateConfirmed"])
        self.assertIn(b"fixture-saved-access", sock.sent[0])
        self.assertNotIn(b"fixture-configured-password", sock.sent[0])
        self.assertEqual(sent_publications(sock)[0][1], b"KEY_RETURNS")
        self.assertTrue(sock.closed)
        with patch("app.vidaa._tls_connection") as network:
            for command in ("power_on", "power_off", "arbitrary"):
                with self.assertRaises(vidaa.VidaaError):
                    vidaa.command("192.168.1.20", 36669, profile(), credentials, command)
            with self.assertRaisesRegex(vidaa.VidaaError, "expired"):
                vidaa.command("192.168.1.20", 36669, profile(), {**credentials, "accessTokenExpiresAt": 1}, "up")
            network.assert_not_called()

    def test_text_input_uses_both_native_platform_routes_and_requires_both_delivery_acks(self):
        credentials={"paired":True,"clientId":CLIENT,"username":"fixture-configured-user","authMode":"modern","accessToken":"fixture-saved-access"}
        sock=FakeSocket(vidaa._packet(0x20,b"\x00\x00")+vidaa._packet(0x40,b"\x00\x02")+vidaa._packet(0x40,b"\x00\x03"))
        with patch('app.vidaa._tls_connection',return_value=sock):
            result=vidaa.command('192.168.1.20',36669,profile(),credentials,'text','search fixture')
        publications=sent_publications(sock)
        self.assertEqual([topic.rsplit('/',1)[-1] for topic,payload in publications],['txtinputdata','bwsinputdata'])
        for topic,payload in publications:
            self.assertIn('/platform_service/',topic)
            self.assertEqual(json.loads(payload),{'text':'search fixture','action':'insert'})
        self.assertTrue(result['acknowledged'])
        self.assertNotIn('search fixture',json.dumps(result))
        self.assertTrue(sock.closed)

    def test_verified_channel_and_power_toggle_values_use_actual_mqtt_key_framing(self):
        credentials = {"paired": True, "clientId": CLIENT, "username": "fixture-configured-user", "authMode": "modern", "accessToken": "fixture-saved-access"}
        expected_keys = {"channel_up": b"KEY_CHANNELUP", "channel_down": b"KEY_CHANNELDOWN", "power_toggle": b"KEY_POWER"}
        topic = f"/remoteapp/tv/remote_service/{CLIENT}/actions/sendkey".encode("utf-8")
        for name, key in expected_keys.items():
            sock = FakeSocket(vidaa._packet(0x20, b"\x00\x00") + vidaa._packet(0x40, b"\x00\x02"))
            with self.subTest(command=name), patch("app.vidaa._tls_connection", return_value=sock):
                result = vidaa.command("192.168.1.20", 36669, profile(), credentials, name)
            self.assertIn(name, vidaa.SUPPORTED_COMMANDS)
            packets = sent_packets(sock)
            self.assertEqual([header for header, _ in packets], [0x10, 0x32])
            self.assertEqual(packets[1][1], struct.pack("!H", len(topic)) + topic + b"\x00\x02" + key)
            self.assertTrue(result["acknowledged"])
            self.assertFalse(result["stateConfirmed"])
            self.assertTrue(sock.closed)
        with patch("app.vidaa._tls_connection") as network:
            for directional in ("power_on", "power_off"):
                self.assertNotIn(directional, vidaa.SUPPORTED_COMMANDS)
                with self.assertRaisesRegex(vidaa.VidaaError, "power toggle"):
                    vidaa.command("192.168.1.20", 36669, profile(), credentials, directional)
            network.assert_not_called()

    def test_saved_pairing_verification_sends_only_connect_and_closes(self):
        credentials = {"paired": True, "clientId": CLIENT, "username": "fixture-configured-user", "authMode": "modern", "accessToken": "fixture-saved-access"}
        sock = FakeSocket(vidaa._packet(0x20, b"\x00\x00"))
        with patch("app.vidaa._tls_connection", return_value=sock):
            result = vidaa.verify_connection("192.168.1.20", 36669, profile(), credentials)
        self.assertTrue(result["authenticated"])
        self.assertFalse(result["stateConfirmed"])
        self.assertEqual([header for header, body in sent_packets(sock)], [0x10])
        self.assertIn(b"fixture-saved-access", sock.sent[0])
        self.assertNotIn(b"fixture-configured-password", sock.sent[0])
        self.assertEqual(sent_publications(sock), [])
        self.assertTrue(sock.closed)
        self.assertNotIn("fixture-saved-access", json.dumps(result))

    def test_saved_pairing_verification_rejects_wrong_or_expired_identity_before_io(self):
        credentials = {"paired": True, "clientId": CLIENT, "username": "fixture-configured-user", "authMode": "modern", "accessToken": "fixture-saved-access"}
        with patch("app.vidaa._tls_connection") as network:
            for invalid in ({}, {**credentials, "clientId": "other-identity"}, {**credentials, "accessTokenExpiresAt": 1}):
                with self.subTest(fields=list(invalid)), self.assertRaises(vidaa.VidaaError):
                    vidaa.verify_connection("192.168.1.20", 36669, profile(), invalid)
            network.assert_not_called()

    def test_saved_pairing_login_rejection_closes_without_other_mqtt_packets(self):
        credentials = {"paired": True, "clientId": CLIENT, "username": "fixture-configured-user", "authMode": "modern", "accessToken": "fixture-saved-access"}
        sock = FakeSocket(vidaa._packet(0x20, b"\x00\x04"))
        with patch("app.vidaa._tls_connection", return_value=sock), self.assertRaisesRegex(vidaa.VidaaError, "rejected") as caught:
            vidaa.verify_connection("192.168.1.20", 36669, profile(), credentials)
        self.assertTrue(sock.closed)
        self.assertEqual([header for header, body in sent_packets(sock)], [0x10])
        self.assertNotIn("fixture-saved-access", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
