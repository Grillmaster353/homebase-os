"""Read-only OS telemetry and authorized SSDP/ONVIF/neighbor discovery.

Traffic is interface throughput, not an internet speed-test result. Network
inventory stays empty until the owner explicitly starts discovery.
"""
from __future__ import annotations

import collections
import contextlib
import datetime as dt
import ipaddress
import os
import platform
import re
import select
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path


def _run(args, timeout=3):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              check=False, env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _private(address):
    try:
        ip = ipaddress.ip_address(address)
        return any(ip in n for n in (ipaddress.ip_network("10.0.0.0/8"),
                   ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16")))
    except ValueError:
        return False


_DISCOVERY_NAMESPACES = ("http://schemas.xmlsoap.org/ws/2005/04/discovery", "http://docs.oasis-open.org/ws-dd/ns/discovery/2009/01")
_ADDRESS_NAMESPACES = ("http://schemas.xmlsoap.org/ws/2004/08/addressing", "http://www.w3.org/2005/08/addressing")
_SOAP_NAMESPACES = ("http://www.w3.org/2003/05/soap-envelope", "http://schemas.xmlsoap.org/soap/envelope/")
_CAMERA_VENDORS = ("Hikvision", "Dahua", "Axis", "Reolink", "Uniview", "Hanwha", "Foscam", "Amcrest", "Vivotek")


def _allowed_peer(address, subnet=None):
    if not _private(address):
        return False
    if subnet:
        try:
            network = ipaddress.ip_network(subnet, strict=False)
            return ipaddress.ip_address(address) in network and address not in (str(network.network_address), str(network.broadcast_address))
        except ValueError:
            return False
    return True


def _advertised_endpoint(value, peer_address):
    """Accept literal local URLs on the responder only; never resolve/fetch them."""
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 33 for c in value) or "\\" in value:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.hostname or "%" in parsed.hostname):
            return None
        address = ipaddress.ip_address(parsed.hostname)
        if not _private(str(address)) or address != ipaddress.ip_address(peer_address):
            return None
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return None
        if ".." in urllib.parse.unquote(parsed.path).split("/"):
            return None
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    except ValueError:
        return None


def _camera_details(hint):
    decoded = urllib.parse.unquote(hint).lower()
    vendor = next((name for name in _CAMERA_VENDORS if name.lower() in decoded), None)
    camera = any(term in decoded for term in ("networkvideotransmitter", "video_encoder", "video_source",
        "/type/camera", "/type/video_storage", "digitalsecuritycamera", "surveillance", "hikvision", "onvif"))
    return camera, vendor


def _onvif_probe(message_id, device_type="dn:NetworkVideoTransmitter"):
    """ONVIF uses WS-Discovery's original 2005/04 SOAP multicast protocol."""
    if device_type not in ("dn:NetworkVideoTransmitter", "tds:Device") or not re.fullmatch(r"uuid:[a-f0-9-]{36}", message_id):
        raise ValueError("Invalid discovery probe")
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        f'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        f'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        f'xmlns:dn="http://www.onvif.org/ver10/network/wsdl" '
        f'xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
        f'<s:Header><a:Action s:mustUnderstand="1">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>'
        f'<a:MessageID>{message_id}</a:MessageID><a:ReplyTo>'
        f'<a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address></a:ReplyTo>'
        f'<a:To s:mustUnderstand="1">urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To></s:Header>'
        f'<s:Body><d:Probe><d:Types>{device_type}</d:Types></d:Probe></s:Body></s:Envelope>').encode("utf-8")


def _parse_onvif_response(payload, peer_address, message_ids, subnet=None):
    """Return camera advertisements, never HTTP-fetch or authenticate a device.

    Discovery payloads are hostile input: require bounded UTF-8 XML, forbid DTD
    and entities, match our probe IDs, and constrain advertised URLs to the peer.
    """
    if not _allowed_peer(peer_address, subnet) or not isinstance(payload, bytes) or len(payload) > 32768 or b"\x00" in payload:
        return []
    try:
        text = payload.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            return []
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError):
        return []
    if root.tag not in (f"{{{namespace}}}Envelope" for namespace in _SOAP_NAMESPACES):
        return []
    stack, count = [(root, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 24 or count > 256:
            return []
        stack.extend((child, depth + 1) for child in node)
    header = next((root.find(f"{{{namespace}}}Header") for namespace in _SOAP_NAMESPACES
                   if root.find(f"{{{namespace}}}Header") is not None), None)
    body = next((root.find(f"{{{namespace}}}Body") for namespace in _SOAP_NAMESPACES
                 if root.find(f"{{{namespace}}}Body") is not None), None)
    if header is None or body is None:
        return []
    relates = next((header.findtext(f"{{{namespace}}}RelatesTo") for namespace in _ADDRESS_NAMESPACES
                    if header.findtext(f"{{{namespace}}}RelatesTo")), None)
    if not relates or relates.strip() not in message_ids:
        return []
    matches = []
    for namespace in _DISCOVERY_NAMESPACES:
        for match in body.findall(f"{{{namespace}}}ProbeMatches/{{{namespace}}}ProbeMatch")[:32]:
            types = (match.findtext(f"{{{namespace}}}Types") or "")[:2000]
            scopes = (match.findtext(f"{{{namespace}}}Scopes") or "")[:6000]
            # A generic ONVIF Device advertisement without a camera/encoder
            # scope stays unclassified; access-control devices are not cameras.
            camera, vendor = _camera_details(types + " " + scopes)
            # The namespace on ordinary ONVIF scopes is not itself evidence of
            # video; require an actual video type/scope or a camera vendor hint.
            strong_camera = any(term in urllib.parse.unquote(types + " " + scopes).lower()
                for term in ("networkvideotransmitter", "video_encoder", "video_source", "/type/camera", "/type/video_storage", "hikvision"))
            if not camera or not strong_camera:
                continue
            advertised = (match.findtext(f"{{{namespace}}}XAddrs") or "").split()[:12]
            endpoints = [endpoint for value in advertised if (endpoint := _advertised_endpoint(value, peer_address))]
            endpoint = endpoints[0] if endpoints else None
            suggested = urllib.parse.urlsplit(endpoint) if endpoint else None
            host = f"{suggested.scheme}://{suggested.netloc}" if suggested else f"http://{peer_address}"
            names = []
            for scope in scopes.split()[:32]:
                try:
                    parsed = urllib.parse.urlsplit(scope)
                except ValueError:
                    continue
                if parsed.scheme.lower() == "onvif" and parsed.hostname == "www.onvif.org" and parsed.path.startswith("/name/"):
                    name = urllib.parse.unquote(parsed.path[6:]).strip()
                    names.append("".join(c for c in name if ord(c) >= 32)[:100])
            matches.append({"id": peer_address, "ip": peer_address,
                "name": next((name for name in names if name), f"{vendor or 'ONVIF'} camera"),
                "type": "camera", "vendor": vendor, "online": True, "lastSeen": _now(),
                "source": "ONVIF WS-Discovery", "host": host, "suggestedHost": host,
                "endpoint": endpoint, "onvif": True, "cameraCandidate": True,
                "suggestedIntegration": "hikvision" if vendor == "Hikvision" else None,
                "advertisement": types[:120],
                "note": "Camera advertisement detected; authorized credentials are required to view images."
                    if endpoint else "Camera advertisement detected; device-service URLs could not be verified. Credentials are still required."})
    return matches


def _parse_ssdp_response(payload, peer_address, subnet=None):
    if not _allowed_peer(peer_address, subnet) or not isinstance(payload, bytes) or len(payload) > 8192:
        return None
    text = payload.decode("utf-8", "replace")
    if not text.upper().startswith("HTTP/1.1 200"):
        return None
    headers = {}
    for line in text.splitlines()[1:50]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.lower().strip()] = value.strip()[:1000]
    hint = " ".join(headers.values())
    camera, vendor = _camera_details(hint)
    vendor = vendor or next((v for v in ("Samsung", "Hisense", "Google", "Apple", "Roku") if v.lower() in hint.lower()), None)
    kind = "camera" if camera else ("tv" if any(v in hint.lower() for v in ("mediarenderer", "samsung", "roku", "dial-multiscreen")) else "device")
    result = {"id": peer_address, "ip": peer_address, "name": f"{vendor} camera" if camera and vendor else vendor or peer_address,
        "type": kind, "vendor": vendor, "online": True, "lastSeen": _now(), "source": "SSDP response",
        "advertisement": headers.get("st", "")[:120]}
    if camera:
        endpoint = _advertised_endpoint(headers.get("location", ""), peer_address)
        parsed = urllib.parse.urlsplit(endpoint) if endpoint else None
        host = f"{parsed.scheme}://{parsed.netloc}" if parsed else f"http://{peer_address}"
        result.update(host=host, suggestedHost=host, endpoint=endpoint, cameraCandidate=True,
                      suggestedIntegration="hikvision" if vendor == "Hikvision" else None,
                      note="Camera advertisement detected; authorized credentials are required to view images.")
    else:
        lower=hint.lower()
        protocol=("roku" if "roku:ecp" in lower or "roku" in lower else
                  "samsung" if "samsung" in lower else
                  "webos" if "webos" in lower or "lgsmarttv" in lower else
                  "androidtv" if "androidtvremote" in lower else None)
        if protocol:
            default_port={"samsung":8001,"androidtv":6466,"roku":8060,"webos":3000}[protocol]
            result.update(type="tv",tvCandidate=True,suggestedIntegration=protocol,suggestedProtocol=protocol,
                          suggestedHost=f"http://{peer_address}:{default_port}",host=f"http://{peer_address}:{default_port}",
                          note="TV protocol advertisement detected. Explicit pairing and approved commands are required; model support varies.")
    return result


def _merge_device(old, new):
    # Multicast protocols can answer in either order. Preserve the stronger
    # camera identification while retaining neighbor MAC data and provenance.
    sources = list(dict.fromkeys([*old.get("sources", []), old.get("source"), new.get("source")]))
    sources = [source for source in sources if source][:4]
    if old.get("onvif") or (old.get("type") == "camera" and new.get("type") != "camera") or (old.get("tvCandidate") and new.get("type") not in {"camera","tv"}):
        result = {**new, **old, "online": new.get("online"), "lastSeen": new.get("lastSeen")}
    else:
        result = {**old, **new}
    if new.get("name") == new.get("ip") and old.get("name"):
        result["name"] = old["name"]
    result["sources"] = sources
    return result


def _discover_advertisements(local_address, subnet):
    """Send read-only SSDP and ONVIF probes concurrently within a fixed budget."""
    devices = {}
    ids = {f"uuid:{uuid.uuid4()}" for _ in range(2)}
    probe_types = ("dn:NetworkVideoTransmitter", "tds:Device")
    ssdp = b'M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\nMAN:"ssdp:discover"\r\nMX:1\r\nST:ssdp:all\r\n\r\n'
    with contextlib.ExitStack() as resources:
        protocols = {}
        for protocol, port in (("ssdp", 1900), ("onvif", 3702)):
            try:
                sock = resources.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                if local_address and _private(local_address):
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_address))
                    sock.bind((local_address, 0))
                sock.setblocking(False)
                if protocol == "ssdp":
                    sock.sendto(ssdp, ("239.255.255.250", port))
                else:
                    for message_id, kind in zip(sorted(ids), probe_types):
                        sock.sendto(_onvif_probe(message_id, kind), ("239.255.255.250", port))
                protocols[sock] = protocol
            except OSError:
                continue
        end, packets = time.monotonic() + 2.5, 0
        while protocols and time.monotonic() < end and packets < 1024:
            try:
                ready, _, _ = select.select(list(protocols), [], [], min(0.25, max(0, end - time.monotonic())))
            except (OSError, ValueError):
                break
            for sock in ready:
                try:
                    data, peer = sock.recvfrom(32769 if protocols[sock] == "onvif" else 8193)
                except (OSError, BlockingIOError):
                    continue
                packets += 1
                if protocols[sock] == "onvif":
                    candidates = _parse_onvif_response(data, peer[0], ids, subnet)
                else:
                    candidate = _parse_ssdp_response(data, peer[0], subnet)
                    candidates = [candidate] if candidate else []
                for candidate in candidates:
                    if len(devices) >= 512 and candidate["ip"] not in devices:
                        continue
                    devices[candidate["ip"]] = _merge_device(devices.get(candidate["ip"], {}), candidate)
    return list(devices.values())


class Monitor:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._history = collections.deque(maxlen=360)
        self._devices = {}
        self._last_counter = None
        self._last_cpu = None
        self._host = {}
        self._network = {}
        self._scan_enabled = False
        self._scan_lock = threading.Lock()
        self._last_discovery = 0
        self._network_identity = None
        self._discovery_paused_reason = None
        self._sample()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="host-telemetry")
        self._thread.start()

    def close(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(3):
            try:
                self._sample()
                if self._scan_enabled and time.monotonic() - self._last_discovery >= 60:
                    self.discover()
            except Exception:
                # An unavailable OS probe must never take down the dashboard.
                pass

    def _sample(self):
        system = platform.system()
        host = {"name": socket.gethostname(), "platform": f"{system} {platform.release()}",
                "cpuCount": os.cpu_count(), "cpuPercent": None, "memoryPercent": None,
                "memoryUsed": None, "memoryTotal": None, "uptimeSeconds": None}
        disk = shutil.disk_usage(Path.home() if Path.home().exists() else Path("/"))
        host["diskPercent"] = round(disk.used / disk.total * 100, 1)
        host["diskUsed"], host["diskTotal"] = disk.used, disk.total
        interface, address, gateway, counters, netmask = None, None, None, None, None
        if system == "Darwin":
            route = _run(["/sbin/route", "-n", "get", "default"])
            m = re.search(r"interface:\s*(\S+)", route)
            interface = m.group(1) if m else None
            m = re.search(r"gateway:\s*(\S+)", route)
            gateway = m.group(1) if m and _private(m.group(1)) else None
            if interface and re.fullmatch(r"[A-Za-z][A-Za-z0-9.:-]{0,30}", interface):
                address = _run(["/usr/sbin/ipconfig", "getifaddr", interface]).strip() or None
                netmask = _run(["/usr/sbin/ipconfig", "getoption", interface, "subnet_mask"]).strip() or None
                lines = _run(["/usr/sbin/netstat", "-ibnI", interface]).splitlines()
                if lines:
                    header = lines[0].split()
                    for line in lines[1:]:
                        fields = line.split()
                        if len(fields) >= len(header) and "<Link#" in line:
                            try:
                                counters = (int(fields[header.index("Ibytes")]), int(fields[header.index("Obytes")]))
                            except (ValueError, IndexError):
                                pass
                            break
            total = _run(["/usr/sbin/sysctl", "-n", "hw.memsize"]).strip()
            if total.isdigit():
                host["memoryTotal"] = int(total)
                vm = _run(["/usr/bin/vm_stat"])
                page = re.search(r"page size of (\d+) bytes", vm)
                pages = {k.strip(): int(v) for k, v in re.findall(r"([^\n:]+):\s+(\d+)\.", vm)}
                if page and pages:
                    available = sum(pages.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative")) * int(page.group(1))
                    host["memoryUsed"] = max(0, int(total) - available)
                    host["memoryPercent"] = round(host["memoryUsed"] / int(total) * 100, 1)
            usage = _run(["/bin/ps", "-A", "-o", "%cpu="])
            try:
                host["cpuPercent"] = round(min(100, sum(float(v) for v in usage.split()) / (os.cpu_count() or 1)), 1) if usage else None
                host["cpuMeasurement"] = "process CPU average, normalized by core count"
            except ValueError:
                pass
            boot = _run(["/usr/sbin/sysctl", "-n", "kern.boottime"])
            m = re.search(r"sec\s*=\s*(\d+)", boot)
            if m:
                host["uptimeSeconds"] = int(time.time() - int(m.group(1)))
        elif system == "Linux":
            try:
                routes = Path("/proc/net/route").read_text().splitlines()[1:]
                for line in routes:
                    f = line.split()
                    if f[1] == "00000000":
                        interface, gateway = f[0], socket.inet_ntoa(struct.pack("<L", int(f[2], 16)))
                        break
                # Route lookup is local kernel state, without sending a packet.
                if gateway:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.connect((gateway, 9))
                        address = s.getsockname()[0]
                        try:
                            import fcntl
                            request = struct.pack("256s", interface[:15].encode())
                            netmask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x891b, request)[20:24])
                        except (OSError, ImportError):
                            pass
                for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
                    name, values = line.split(":", 1)
                    if name.strip() == interface:
                        f = values.split()
                        counters = (int(f[0]), int(f[8]))
                cpu = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
                cpu_total, cpu_idle = sum(cpu), cpu[3] + (cpu[4] if len(cpu) > 4 else 0)
                if self._last_cpu:
                    total_delta = cpu_total - self._last_cpu[0]
                    if total_delta:
                        host["cpuPercent"] = round((1 - (cpu_idle - self._last_cpu[1]) / total_delta) * 100, 1)
                self._last_cpu = (cpu_total, cpu_idle)
                mem = {k: int(v) * 1024 for k, v in re.findall(r"^(\w+):\s+(\d+)", Path("/proc/meminfo").read_text(), re.M)}
                host["memoryTotal"] = mem.get("MemTotal")
                if host["memoryTotal"]:
                    host["memoryUsed"] = mem["MemTotal"] - mem.get("MemAvailable", mem.get("MemFree", 0))
                    host["memoryPercent"] = round(host["memoryUsed"] / host["memoryTotal"] * 100, 1)
                host["uptimeSeconds"] = int(float(Path("/proc/uptime").read_text().split()[0]))
            except (OSError, ValueError, IndexError, KeyError):
                pass
        current = time.monotonic()
        rx, tx = None, None
        if counters and self._last_counter:
            elapsed = current - self._last_counter[0]
            if elapsed > 0:
                rx = round(max(0, counters[0] - self._last_counter[1]) / elapsed, 1)
                tx = round(max(0, counters[1] - self._last_counter[2]) / elapsed, 1)
        if counters:
            self._last_counter = (current, *counters)
        subnet = None
        if address and netmask:
            try:
                subnet = str(ipaddress.ip_network(f"{address}/{netmask}", strict=False))
            except ValueError:
                pass
        identity = (interface, subnet or address, gateway)
        network_changed = False
        with self._lock:
            if self._network_identity is not None and identity != self._network_identity:
                network_changed = True
                if self._scan_enabled:
                    self._discovery_paused_reason = "Network changed. Start a new scan to authorize discovery on this network."
                self._scan_enabled = False
                self._devices.clear()
                self._history.clear()
                rx, tx = None, None
            self._network_identity = identity
        latency = None if network_changed else self._network.get("latencyMs")
        # Only probe a gateway after explicit discovery. No internet targets.
        if self._scan_enabled and gateway and _private(gateway):
            args = ["/sbin/ping" if system == "Darwin" else "ping", "-n", "-c", "1", "-W", "1000" if system == "Darwin" else "1", gateway]
            m = re.search(r"time[=<]([\d.]+)\s*ms", _run(args, 2))
            latency = float(m.group(1)) if m else None
        with self._lock:
            point = {"time": _now(), "rxBps": rx, "txBps": tx, "latencyMs": latency}
            self._history.append(point)
            self._host = host
            self._network = {"interface": interface, "localAddress": address, "gateway": gateway,
                             "rxBps": rx, "txBps": tx, "latencyMs": latency,
                             "subnet": subnet,
                             "connectivity": "local interface" if address else "unavailable",
                             "measurement": "Current host interface throughput; not an internet speed test",
                             "discoveryStarted": self._scan_enabled,
                             "discoveryPausedReason": self._discovery_paused_reason,
                             "discoveryRefreshSeconds": 60 if self._scan_enabled else None}

    def overview(self):
        with self._lock:
            for device in self._devices.values():
                if device.get("online") is True and device.get("lastSeen"):
                    try:
                        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(device["lastSeen"])).total_seconds()
                        if age > 65:
                            device["online"] = None
                    except (TypeError, ValueError):
                        device["online"] = None
            return {"host": dict(self._host), "network": {**self._network, "history": list(self._history)},
                    "devices": [dict(d) for d in self._devices.values()]}

    def discover(self):
        """Read neighbors and SSDP/ONVIF advertisements; no device controls."""
        if not self._scan_lock.acquire(blocking=False):
            return self.overview()["devices"]
        try:
            return self._discover()
        finally:
            self._scan_lock.release()

    def _discover(self):
        with self._lock:
            scan_identity = self._network_identity
            local_address = self._network.get("localAddress")
            subnet = self._network.get("subnet")
            interface = self._network.get("interface")
        discovered = {}
        if platform.system() == "Darwin":
            output = _run(["/usr/sbin/arp", "-an"])
            for name, address, mac in re.findall(r"(\S+)\s+\(([\d.]+)\)\s+at\s+([0-9a-f:]+)", output, re.I):
                if not _allowed_peer(address, subnet) or mac == "ff:ff:ff:ff:ff:ff":
                    continue
                discovered[address] = {"id": address, "name": address if name == "?" else name,
                    "ip": address, "mac": mac, "type": "device", "vendor": None,
                    "online": None, "lastSeen": None, "source": "neighbor cache",
                    "note": "Cached neighbor; current reachability unverified"}
        else:
            try:
                for line in Path("/proc/net/arp").read_text().splitlines()[1:]:
                    f = line.split()
                    if (len(f) >= 6 and _allowed_peer(f[0], subnet) and f[2] != "0x0"
                            and (not interface or f[5] == interface)):
                        discovered[f[0]] = {"id": f[0], "name": f[0], "ip": f[0], "mac": f[3],
                            "type": "device", "vendor": None, "online": None,
                            "lastSeen": None, "source": "neighbor cache",
                            "note": "Cached neighbor; current reachability unverified"}
            except OSError:
                pass
        for candidate in _discover_advertisements(local_address, subnet):
            discovered[candidate["ip"]] = _merge_device(discovered.get(candidate["ip"], {}), candidate)
        # Optional standard mDNS TV service discovery; it never pairs a remote
        # or sends a key. Missing optional dependencies leave SSDP operational.
        try:
            from .direct_tv import discover_tvs
            for candidate in discover_tvs(timeout=2,subnet=subnet,local_address=local_address):
                if _allowed_peer(candidate.get("ip",""),subnet):
                    discovered[candidate["ip"]] = _merge_device(discovered.get(candidate["ip"],{}),candidate)
        except (OSError,ValueError,ImportError):
            pass
        with self._lock:
            if scan_identity != self._network_identity:
                self._scan_enabled = False
                self._discovery_paused_reason = "Network changed during discovery. Start a new scan on the current network."
                return [dict(d) for d in self._devices.values()]
            # Retain previous discoveries but never imply they are still reachable.
            for device in self._devices.values():
                device["online"] = None
            self._devices.update(discovered)
            self._scan_enabled = True
            self._last_discovery = time.monotonic()
            self._discovery_paused_reason = None
            self._network.update(discoveryStarted=True, discoveryPausedReason=None, discoveryRefreshSeconds=60)
            return [dict(d) for d in self._devices.values()]
