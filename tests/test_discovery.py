"""Offline discovery fixtures: no multicast or household-device traffic."""
import html
import re
import unittest
from unittest.mock import patch

from app.monitor import (_advertised_endpoint, _discover_advertisements,
    _enrich_ssdp_devices, _merge_device, _onvif_probe, _parse_onvif_response,
    _parse_ssdp_description, _parse_ssdp_response, _ssdp_metadata)


PROBE_ID = "uuid:12345678-1234-1234-1234-123456789abc"


def response(message_id=PROBE_ID, types="dn:NetworkVideoTransmitter",
             scopes="onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/Front%20Door onvif://www.onvif.org/hardware/HIKVISION-DS-2CD",
             endpoint="http://192.168.1.25/onvif/device_service"):
    return (f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        f'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        f'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
        f'<s:Header><a:RelatesTo>{html.escape(message_id)}</a:RelatesTo></s:Header>'
        f'<s:Body><d:ProbeMatches><d:ProbeMatch>'
        f'<d:Types>{html.escape(types)}</d:Types><d:Scopes>{html.escape(scopes)}</d:Scopes>'
        f'<d:XAddrs>{html.escape(endpoint)}</d:XAddrs>'
        f'</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>').encode()


def tv_description(name="Hisense VIDAA TV", manufacturer="SmartTV", model="Renderer",
                   description="#CAP#\nplatform=1\nvoice=2\nbrand=his\nvidaa_s=1"):
    return ('<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
        '<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>'
        f'<friendlyName>{html.escape(name)}</friendlyName>'
        f'<manufacturer>{html.escape(manufacturer)}</manufacturer>'
        f'<modelName>{html.escape(model)}</modelName>'
        f'<modelDescription>{html.escape(description)}</modelDescription>'
        '</device></root>').encode()


class DiscoveryTests(unittest.TestCase):
    def test_onvif_camera_detected_with_safe_prefill(self):
        with patch("app.monitor.socket.getaddrinfo", side_effect=AssertionError("Discovery must not resolve advertised hosts")):
            matches = _parse_onvif_response(response(), "192.168.1.25", {PROBE_ID}, "192.168.1.0/24")
        self.assertEqual(len(matches), 1)
        camera = matches[0]
        self.assertEqual(camera["type"], "camera")
        self.assertEqual(camera["name"], "Front Door")
        self.assertEqual(camera["vendor"], "Hikvision")
        self.assertEqual(camera["suggestedHost"], "http://192.168.1.25")
        self.assertEqual(camera["endpoint"], "http://192.168.1.25/onvif/device_service")
        self.assertEqual(camera["suggestedIntegration"], "hikvision")
        self.assertTrue(camera["onvif"])

    def test_generic_onvif_device_requires_video_scope(self):
        generic = response(types="tds:Device", scopes="onvif://www.onvif.org/type/access_control onvif://www.onvif.org/name/Door")
        self.assertEqual(_parse_onvif_response(generic, "192.168.1.25", {PROBE_ID}), [])
        video = response(types="tds:Device", scopes="onvif://www.onvif.org/type/video_encoder")
        self.assertEqual(_parse_onvif_response(video, "192.168.1.25", {PROBE_ID})[0]["type"], "camera")

    def test_spoofed_correlation_and_off_subnet_peers_ignored(self):
        self.assertEqual(_parse_onvif_response(response(message_id="uuid:unrelated"), "192.168.1.25", {PROBE_ID}), [])
        for peer in ("8.8.8.8", "169.254.169.254", "192.168.2.25", "192.168.1.255", "192.168.1.0"):
            with self.subTest(peer=peer):
                self.assertEqual(_parse_onvif_response(response(), peer, {PROBE_ID}, "192.168.1.0/24"), [])

    def test_advertised_urls_never_redirect_or_resolve_elsewhere(self):
        for endpoint in ("http://8.8.8.8/onvif", "http://169.254.169.254/latest/meta-data", "http://192.168.1.99/onvif",
                "http://camera.local/onvif", "file:///etc/passwd", "http://user:password@192.168.1.25/onvif",
                "http://192.168.1.25:0/onvif", "http://192.168.1.25/onvif?token=x", "http://192.168.1.25/onvif#x",
                "http://192.168.1.25/../secrets", "http://192.168.1.25\\@8.8.8.8/onvif"):
            with self.subTest(endpoint=endpoint):
                self.assertIsNone(_advertised_endpoint(endpoint, "192.168.1.25"))
                result = _parse_onvif_response(response(endpoint=endpoint), "192.168.1.25", {PROBE_ID})[0]
                self.assertIsNone(result["endpoint"])
                self.assertEqual(result["host"], "http://192.168.1.25")
        self.assertEqual(_advertised_endpoint("https://192.168.1.25:8443/onvif/device_service", "192.168.1.25"),
            "https://192.168.1.25:8443/onvif/device_service")

    def test_xml_entities_oversize_and_excessive_nesting_rejected(self):
        attacks = [b'<!DOCTYPE s:Envelope [<!ENTITY a SYSTEM "file:///etc/passwd">]>' + response(),
            b'<!DOCTYPE s:Envelope [<!ENTITY a "expanded">]>' + response(),
            b"a" * 32769, response().decode().encode("utf-16"), b"not xml",
            response().replace(b"<d:Types>", b"<x>" * 30 + b"<d:Types>").replace(b"</d:Types>", b"</d:Types>" + b"</x>" * 30)]
        for payload in attacks:
            with self.subTest(length=len(payload)):
                self.assertEqual(_parse_onvif_response(payload, "192.168.1.25", {PROBE_ID}), [])

    def test_malformed_scope_does_not_crash_discovery(self):
        result = _parse_onvif_response(response(scopes="onvif://[ onvif://www.onvif.org/type/video_encoder"), "192.168.1.25", {PROBE_ID})
        self.assertEqual(result[0]["type"], "camera")

    def test_ssdp_camera_classification_and_local_metadata(self):
        ssdp = b"HTTP/1.1 200 OK\r\nSERVER:Hikvision/1.0\r\nST:urn:schemas-upnp-org:device:DigitalSecurityCamera:1\r\nLOCATION:http://192.168.1.25/description.xml\r\n\r\n"
        result = _parse_ssdp_response(ssdp, "192.168.1.25", "192.168.1.0/24")
        self.assertEqual(result["type"], "camera")
        self.assertEqual(result["vendor"], "Hikvision")
        self.assertEqual(result["suggestedHost"], "http://192.168.1.25")
        self.assertIsNone(_parse_ssdp_response(ssdp, "8.8.8.8"))
        tv = _parse_ssdp_response(b"HTTP/1.1 200 OK\r\nSERVER:Samsung\r\nST:MediaRenderer\r\n\r\n", "192.168.1.26")
        self.assertEqual(tv["type"], "tv")

    def test_vidaa_metadata_supplies_actual_name_and_platform_without_pairing(self):
        packet = b"HTTP/1.1 200 OK\r\nST:urn:schemas-upnp-org:device:MediaRenderer:1\r\nLOCATION:http://192.168.1.7:38400/MediaServer/rendererdevicedesc.xml\r\n\r\n"
        candidate = _parse_ssdp_response(packet, "192.168.1.7", "192.168.1.0/24")
        self.assertNotIn("suggestedProtocol", candidate)
        with patch("app.integrations.safe_request", return_value=tv_description()) as request:
            found = _ssdp_metadata(candidate)
        request.assert_called_once_with("http://192.168.1.7:38400/MediaServer/rendererdevicedesc.xml", timeout=.75, max_bytes=32768)
        self.assertEqual(found["name"], "Hisense VIDAA TV")
        self.assertEqual(found["manufacturer"], "SmartTV")
        self.assertEqual(found["modelName"], "Renderer")
        self.assertEqual(found["vendor"], "Hisense")
        self.assertTrue(found["tvCandidate"])
        self.assertEqual(found["suggestedIntegration"], "hisense")
        self.assertEqual(found["suggestedProtocol"], "vidaa")
        self.assertEqual(found["suggestedHost"], "http://192.168.1.7:36669")
        self.assertNotIn("tvCredentials", found)

    def test_vidaa_advertisement_or_description_marker_is_positive_evidence(self):
        for hint in ("VIDAA/1.0", "Hisense vidaa_s=1"):
            found = _parse_ssdp_response(f"HTTP/1.1 200 OK\r\nSERVER:{hint}\r\nST:MediaRenderer\r\n\r\n".encode(), "192.168.1.7")
            self.assertEqual(found["suggestedProtocol"], "vidaa")
        candidate = _parse_ssdp_response(b"HTTP/1.1 200 OK\r\nST:MediaRenderer\r\nLOCATION:http://192.168.1.7/device.xml\r\n\r\n", "192.168.1.7")
        with patch("app.integrations.safe_request", return_value=tv_description(name="Living room", description="brand=his vidaa_s=1")):
            self.assertEqual(_ssdp_metadata(candidate)["suggestedProtocol"], "vidaa")

    def test_hisense_brand_and_renderer_filename_never_imply_android_or_vidaa(self):
        packet = b"HTTP/1.1 200 OK\r\nSERVER:Hisense\r\nST:MediaRenderer\r\nLOCATION:http://192.168.1.20:38400/MediaServer/rendererdevicedesc.xml\r\n\r\n"
        candidate = _parse_ssdp_response(packet, "192.168.1.20")
        self.assertTrue(candidate["tvCandidate"])
        self.assertEqual(candidate["suggestedIntegration"], "hisense")
        self.assertEqual(candidate["suggestedProtocol"], "")
        with patch("app.integrations.safe_request", return_value=tv_description(name="Hisense TV", manufacturer="Hisense", description="Media renderer")):
            found = _ssdp_metadata(candidate)
        self.assertEqual(found["suggestedProtocol"], "")
        self.assertEqual(found["suggestedHost"], "http://192.168.1.20")

    def test_ssdp_metadata_rejects_foreign_urls_and_does_not_follow_embedded_urls(self):
        for endpoint in ("http://192.168.1.99/device.xml", "http://tv.local/device.xml",
                "http://8.8.8.8/device.xml", "http://user:pass@192.168.1.7/device.xml"):
            candidate = {"ip":"192.168.1.7", "descriptionUrl":endpoint, "type":"tv"}
            with patch("app.integrations.safe_request") as request:
                self.assertEqual(_ssdp_metadata(candidate), candidate)
                request.assert_not_called()
        candidate = {"ip":"192.168.1.7", "descriptionUrl":"http://192.168.1.7/device.xml", "type":"tv"}
        payload = tv_description().replace(b"</device>", b"<presentationURL>http://8.8.8.8/admin</presentationURL><URLBase>http://8.8.8.8/</URLBase></device>")
        with patch("app.integrations.safe_request", return_value=payload) as request:
            self.assertEqual(_ssdp_metadata(candidate)["suggestedProtocol"], "vidaa")
            self.assertEqual(request.call_count, 1)
        with patch("app.integrations.safe_request", side_effect=ValueError("Redirect refused")):
            self.assertEqual(_ssdp_metadata(candidate), candidate)

    def test_ssdp_description_rejects_entities_size_depth_and_element_flood(self):
        attacks = [b'<!DOCTYPE root [<!ENTITY a "VIDAA">]>' + tv_description(),
            b'<!ENTITY a SYSTEM "file:///etc/passwd">' + tv_description(),
            b"a" * 32769, tv_description().decode().encode("utf-16"), b"not XML",
            tv_description().replace(b"<device>", b"<device>" + b"<x>" * 17).replace(b"</device>", b"</x>" * 17 + b"</device>"),
            tv_description().replace(b"</device>", b"<x/>" * 257 + b"</device>")]
        for payload in attacks:
            with self.subTest(length=len(payload)):
                self.assertEqual(_parse_ssdp_description(payload), {})

    def test_ssdp_metadata_requests_are_capped_and_only_known_local_urls_are_used(self):
        devices = {f"192.168.1.{i}":{"ip":f"192.168.1.{i}", "name":f"Device {i}",
            "type":"tv", "descriptionUrl":f"http://192.168.1.{i}/device.xml"} for i in range(10, 30)}
        with patch("app.monitor._ssdp_metadata", side_effect=lambda candidate: candidate) as metadata:
            _enrich_ssdp_devices(devices)
        self.assertEqual(metadata.call_count, 8)
        self.assertEqual(len(devices), 20)

    def test_android_mdns_name_and_protocol_survive_generic_hisense_metadata(self):
        android = {"id":"192.168.1.72", "ip":"192.168.1.72", "name":"Kitchen TV",
            "type":"tv", "vendor":"Google / Android TV", "tvCandidate":True,
            "source":"TV mDNS advertisement", "suggestedProtocol":"androidtv",
            "suggestedIntegration":"androidtv", "host":"http://192.168.1.72:6466",
            "suggestedHost":"http://192.168.1.72:6466"}
        generic = _parse_ssdp_response(b"HTTP/1.1 200 OK\r\nSERVER:Hisense\r\nST:MediaRenderer\r\n\r\n", "192.168.1.72")
        for old, new in ((android, generic), (generic, android)):
            found = _merge_device(old, new)
            self.assertEqual(found["name"], "Kitchen TV")
            self.assertEqual(found["suggestedProtocol"], "androidtv")
            self.assertEqual(found["suggestedHost"], "http://192.168.1.72:6466")
        enriched = {**generic, "name":"Hisense VIDAA TV", "nameSource":"SSDP description", "suggestedProtocol":"vidaa"}
        self.assertEqual(_merge_device(android, enriched)["suggestedProtocol"], "androidtv")
        self.assertEqual(_merge_device(android, enriched)["name"], "Kitchen TV")

    def test_generic_ssdp_does_not_erase_previously_identified_protocol_or_name(self):
        strong = {"ip":"192.168.1.7", "name":"Living room", "nameSource":"SSDP description", "type":"tv",
            "tvCandidate":True, "source":"SSDP response", "suggestedProtocol":"vidaa",
            "suggestedIntegration":"hisense", "suggestedHost":"http://192.168.1.7:36669", "host":"http://192.168.1.7:36669"}
        generic = _parse_ssdp_response(b"HTTP/1.1 200 OK\r\nSERVER:Hisense\r\nST:MediaRenderer\r\n\r\n", "192.168.1.7")
        found = _merge_device(strong, generic)
        self.assertEqual(found["name"], "Living room")
        self.assertEqual(found["suggestedProtocol"], "vidaa")
        self.assertEqual(found["host"], "http://192.168.1.7:36669")

    def test_camera_identity_survives_protocol_response_order(self):
        camera = _parse_onvif_response(response(), "192.168.1.25", {PROBE_ID})[0]
        neighbor = {"id": "192.168.1.25", "ip": "192.168.1.25", "mac": "11:22:33:44:55:66", "type": "device", "source": "neighbor cache", "online": None}
        result = _merge_device(neighbor, camera)
        self.assertEqual(result["mac"], neighbor["mac"])
        self.assertEqual(result["name"], "Front Door")
        generic = _parse_ssdp_response(b"HTTP/1.1 200 OK\r\nST:upnp:rootdevice\r\n\r\n", "192.168.1.25")
        result = _merge_device(result, generic)
        self.assertEqual(result["type"], "camera")
        self.assertEqual(result["source"], "ONVIF WS-Discovery")
        self.assertIn("SSDP response", result["sources"])

    def test_probe_types_and_multicast_wire_protocol_without_network(self):
        sockets = []

        class FakeUDP:
            def __init__(self, *_):
                self.sent = []
                self.options = []
                sockets.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def setsockopt(self, *args):
                self.options.append(args)

            def bind(self, address):
                self.bound = address

            def setblocking(self, flag):
                self.blocking = flag

            def sendto(self, payload, destination):
                self.sent.append((payload, destination))

            def recvfrom(self, _):
                message_id = re.search(rb"<a:MessageID>([^<]+)</a:MessageID>", self.sent[0][0]).group(1).decode()
                return response(message_id=message_id), ("192.168.1.25", 3702)

        with patch("app.monitor.socket.socket", FakeUDP), \
                patch("app.monitor.select.select", side_effect=lambda *_: ([sockets[1]], [], [])), \
                patch("app.monitor.time.monotonic", side_effect=[0, 0, 0, 3]):
            found = _discover_advertisements("192.168.1.2", "192.168.1.0/24")
        self.assertEqual(found[0]["type"], "camera")
        self.assertEqual(sockets[0].sent[0][1], ("239.255.255.250", 1900))
        self.assertEqual(len(sockets[1].sent), 2)
        self.assertTrue(all(destination == ("239.255.255.250", 3702) for _, destination in sockets[1].sent))
        self.assertEqual(sockets[1].bound, ("192.168.1.2", 0))
        self.assertIn(b"dn:NetworkVideoTransmitter", sockets[1].sent[0][0])
        self.assertIn(b"tds:Device", sockets[1].sent[1][0])
        self.assertIn(b"http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe", _onvif_probe(PROBE_ID))


if __name__ == "__main__":
    unittest.main()
