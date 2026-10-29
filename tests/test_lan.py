import contextlib
import io
from pathlib import Path
import socket
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.lan import ENUMERATION, MDNS_PORT, SERVICE, MDNSAdvertiser, decode_name, encode_name, get_lan_name, is_local_peer, parse_message
from app.server import Server, main


def query(name, kind=255, identity=0, unicast=False):
    return struct.pack("!6H", identity, 0, 1, 0, 0, 0) + encode_name(name) + struct.pack("!HH", kind, 1 | (0x8000 if unicast else 0))


class LANTests(unittest.TestCase):
    def setUp(self):
        self.advertiser = MDNSAdvertiser("homebase-1234abcd.local", 8420, lambda: "192.168.1.12")
        self.advertiser.address = "192.168.1.12"

    def test_unique_name_persists_and_separates_instances(self):
        with tempfile.TemporaryDirectory() as root:
            first = get_lan_name(Path(root) / "one")
            self.assertEqual(first, get_lan_name(Path(root) / "one"))
            self.assertRegex(first, r"^homebase-[0-9a-f]{8}\.local$")
            self.assertNotEqual(first, get_lan_name(Path(root) / "two"))
            self.assertEqual((Path(root) / "one" / "lan-name").stat().st_mode & 0o777, 0o600)

    def test_names_roundtrip_and_compression(self):
        raw = encode_name("Example.local")
        self.assertEqual(decode_name(raw, 0), ("example.local", len(raw)))
        packet = raw + b"\xc0\x00"
        self.assertEqual(decode_name(packet, len(raw))[0], "example.local")
        for malformed in (b"\xc0\x00", b"\x40bad", b"\xff\xff", b"\x05short"):
            with self.assertRaises(ValueError):
                decode_name(malformed, 0)

    def test_announcement_has_real_address_https_service_and_ttl(self):
        parsed = parse_message(self.advertiser.announcement())
        self.assertEqual(parsed["flags"], 0x8400)
        records = parsed["records"]
        self.assertEqual(len(records), 5)
        self.assertTrue(all(record["ttl"] == 120 for record in records))
        address = next(record for record in records if record["type"] == 1)
        self.assertEqual(socket.inet_ntoa(address["data"]), "192.168.1.12")
        srv = next(record for record in records if record["type"] == 33)
        self.assertEqual(srv["value"], (0, 0, 8420, "homebase-1234abcd.local"))
        self.assertEqual(next(record for record in records if record["name"] == ENUMERATION)["value"], SERVICE)
        self.assertTrue(all(record["ttl"] == 0 for record in parse_message(self.advertiser.announcement(0))["records"]))

    def test_queries_resolve_host_and_service_and_legacy_unicast(self):
        for name, kind in ((self.advertiser.hostname, 1), (SERVICE, 12), (self.advertiser.instance, 33)):
            reply = self.advertiser.response(parse_message(query(name, kind)), MDNS_PORT)
            self.assertIsNotNone(reply)
            message = parse_message(reply[0])
            self.assertTrue(any(record["type"] == kind and record["name"] == name.lower() for record in message["records"]))
            self.assertFalse(reply[1])
        reply = self.advertiser.response(parse_message(query(SERVICE, 12, identity=42)), 62001)
        self.assertTrue(reply[1])
        parsed = parse_message(reply[0])
        self.assertEqual(parsed["id"], 42)
        self.assertEqual(len(parsed["questions"]), 1)
        self.assertTrue(all(record["ttl"] <= 10 for record in parsed["records"]))
        self.assertIsNone(self.advertiser.response(parse_message(query("unrelated.local", 1)), MDNS_PORT))

    def test_probing_and_conflicts_do_not_claim_another_hosts_name(self):
        parsed = parse_message(self.advertiser.probe())
        self.assertEqual(len(parsed["questions"]), 2)
        self.assertFalse(self.advertiser.conflicts(parsed))
        rival = MDNSAdvertiser(self.advertiser.hostname, 8420, lambda: "192.168.1.99")
        rival.address = "192.168.1.99"
        self.assertTrue(self.advertiser.conflicts(parse_message(rival.announcement())))

    def test_strict_private_peer_ranges_and_pre_tls_filter(self):
        for address in ("192.168.1.3", "10.1.2.3", "172.20.0.3", "169.254.2.1", "127.0.0.1", "::1", "fc00::12", "fe80::1", "::ffff:192.168.1.4"):
            self.assertTrue(is_local_peer(address), address)
        for address in ("8.8.8.8", "0.0.0.0", "192.0.2.1", "198.18.0.1", "100.64.0.1", "224.0.0.251", "2001:db8::1", "::", "not-an-ip"):
            self.assertFalse(is_local_peer(address), address)
        request, tls = Mock(), Mock()
        listener = SimpleNamespace(socket=Mock(), tls_context=tls)
        listener.socket.accept.return_value = (request, ("8.8.8.8", 1234))
        with self.assertRaises(OSError):
            Server.get_request(listener)
        request.close.assert_called_once()
        tls.wrap_socket.assert_not_called()

    def test_advertise_and_auto_discovery_fail_closed_without_lan_tls(self):
        for arguments in (["--advertise"], ["--auto-discover"], ["--lan", "--advertise"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                main(arguments)

    def test_lan_refuses_to_bind_before_account_initialization(self):
        app = SimpleNamespace(vault=SimpleNamespace(data={"account": None}), monitor=Mock())
        with patch("app.server.Application", return_value=app), patch("app.server.Server") as server:
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                main(["--host", "0.0.0.0", "--lan", "--cert", "unused.pem", "--key", "unused.key", "--advertise"])
            server.assert_not_called()
            app.monitor.close.assert_called_once()

    def test_parser_rejects_bounded_malformed_packets(self):
        for packet in (b"", b"x" * 4097, struct.pack("!6H", 0, 0, 129, 0, 0, 0), struct.pack("!6H", 0, 0, 1, 0, 0, 0) + b"\xc0\x0c"):
            with self.assertRaises(ValueError):
                parse_message(packet)


if __name__ == "__main__":
    unittest.main()
