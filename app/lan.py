"""Dependency-free, local-link IPv4 mDNS/DNS-SD advertisement.

The responder claims one persistent unique hostname and advertises only the
authenticated HTTPS dashboard. It does not modify DNS, router or firewall
settings. Protocol framing follows RFC 6762/6763. IPv4 is intentional: clients
on an ordinary dual-stack LAN can resolve its A record without IPv6 listeners.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import random
import re
import secrets
import socket
import struct
import threading
import time


GROUP, MDNS_PORT = "224.0.0.251", 5353
SERVICE = "_https._tcp.local"
ENUMERATION = "_services._dns-sd._udp.local"
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"))


def is_local_peer(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
            parsed = parsed.ipv4_mapped
        return any(parsed in network for network in _LOCAL_NETWORKS)
    except (ValueError, AttributeError):
        return False


def get_lan_name(data_dir: str | Path) -> str:
    """Return a stable, public hostname without exposing the vault or host name."""
    directory = Path(data_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "lan-name"
    if not path.exists():
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(f"homebase-{secrets.token_hex(4)}.local\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
    value = path.read_text().strip().lower()
    if not re.fullmatch(r"homebase-[0-9a-f]{8}\.local", value):
        raise ValueError("Invalid persisted LAN hostname; restore its original lan-name file.")
    os.chmod(path, 0o600)
    return value


def encode_name(name: str) -> bytes:
    result = bytearray()
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        if not 0 < len(raw) <= 63:
            raise ValueError("Invalid DNS label.")
        result.append(len(raw))
        result.extend(raw)
    result.append(0)
    if len(result) > 255:
        raise ValueError("DNS name is too long.")
    return bytes(result)


def decode_name(packet: bytes, position: int) -> tuple[str, int]:
    labels, seen, end, total = [], set(), None, 0
    for _ in range(128):
        if position >= len(packet) or position in seen:
            raise ValueError("Invalid DNS name pointer.")
        seen.add(position)
        length = packet[position]
        if length & 0xC0 == 0xC0:
            if position + 1 >= len(packet):
                raise ValueError("Truncated DNS pointer.")
            if end is None:
                end = position + 2
            position = ((length & 0x3F) << 8) | packet[position + 1]
            continue
        if length & 0xC0 or length > 63:
            raise ValueError("Invalid DNS label.")
        position += 1
        if length == 0:
            return ".".join(labels).lower(), end if end is not None else position
        if position + length > len(packet):
            raise ValueError("Truncated DNS name.")
        labels.append(packet[position:position + length].decode("utf-8"))
        total += length + 1
        if total > 254:
            raise ValueError("DNS name is too long.")
        position += length
    raise ValueError("Too many DNS pointers.")


def parse_message(packet: bytes) -> dict:
    if not 12 <= len(packet) <= 4096:
        raise ValueError("Invalid DNS message size.")
    identity, flags, qd, an, ns, ar = struct.unpack_from("!6H", packet)
    if qd + an + ns + ar > 128:
        raise ValueError("Too many DNS records.")
    position, questions, records = 12, [], []
    for _ in range(qd):
        name, position = decode_name(packet, position)
        if position + 4 > len(packet):
            raise ValueError("Truncated DNS question.")
        kind, cls = struct.unpack_from("!HH", packet, position)
        position += 4
        questions.append({"name": name, "type": kind, "class": cls & 0x7FFF, "unicast": bool(cls & 0x8000)})
    question_wire = packet[12:position]
    # Preserve parsed questions, never echo compressed source bytes (their
    # pointers may refer to omitted records in a legacy-unicast response).
    question_wire = b"".join(encode_name(q["name"]) + struct.pack("!HH", q["type"], q["class"]) for q in questions)
    for _ in range(an + ns + ar):
        name, position = decode_name(packet, position)
        if position + 10 > len(packet):
            raise ValueError("Truncated DNS record.")
        kind, cls, ttl, length = struct.unpack_from("!HHIH", packet, position)
        position += 10
        if position + length > len(packet):
            raise ValueError("Truncated DNS data.")
        data = packet[position:position + length]
        value = data
        if kind == 12:
            value, _ = decode_name(packet, position)
        elif kind == 33 and length >= 7:
            target, _ = decode_name(packet, position + 6)
            value = (*struct.unpack_from("!HHH", packet, position), target)
        records.append({"name": name, "type": kind, "class": cls & 0x7FFF, "ttl": ttl, "data": data, "value": value})
        position += length
    return {"id": identity, "flags": flags, "questions": questions, "records": records, "questionWire": question_wire}


def _rr(name: str, kind: int, data: bytes, ttl=120, unique=False) -> bytes:
    return encode_name(name) + struct.pack("!HHIH", kind, 1 | (0x8000 if unique else 0), ttl, len(data)) + data


class MDNSAdvertiser:
    def __init__(self, hostname: str, port: int, address_supplier, on_state=None):
        if not re.fullmatch(r"homebase-[0-9a-f]{8}\.local", hostname):
            raise ValueError("The advertisement requires the persisted Homebase LAN hostname.")
        if not 1 <= port <= 65535:
            raise ValueError("Invalid dashboard port.")
        self.hostname, self.port = hostname, port
        self.instance = f"Homebase ({hostname[9:17]}).{SERVICE}"
        self.address_supplier, self.on_state = address_supplier, on_state
        self._stop = threading.Event()
        self._thread = None
        self._socket = None
        self.address = None
        self.state = "stopped"
        self.error = None
        self._last_sent = 0.0

    def _set_state(self, state, error=None):
        self.state, self.error = state, error
        if self.on_state:
            self.on_state(state, error, self.address)

    def _records(self, ttl=120, legacy=False):
        if not self.address:
            return []
        unique = not legacy
        ttl = min(ttl, 10) if legacy else ttl
        txt = b"".join(bytes([len(item)]) + item for item in (b"path=/", b"local-only=1", b"auth=required"))
        return [
            (ENUMERATION, 12, _rr(ENUMERATION, 12, encode_name(SERVICE), ttl)),
            (SERVICE, 12, _rr(SERVICE, 12, encode_name(self.instance), ttl)),
            (self.instance.lower(), 33, _rr(self.instance, 33, struct.pack("!HHH", 0, 0, self.port) + encode_name(self.hostname), ttl, unique)),
            (self.instance.lower(), 16, _rr(self.instance, 16, txt, ttl, unique)),
            (self.hostname, 1, _rr(self.hostname, 1, socket.inet_aton(self.address), ttl, unique)),
        ]

    def announcement(self, ttl=120):
        records = self._records(ttl)
        return struct.pack("!6H", 0, 0x8400, 0, len(records), 0, 0) + b"".join(record[2] for record in records)

    def probe(self):
        questions = b"".join(encode_name(name) + struct.pack("!HH", 255, 1) for name in (self.hostname, self.instance))
        authority = [record[2] for record in self._records(120, legacy=True) if record[1] in (1, 16, 33)]
        return struct.pack("!6H", 0, 0, 2, 0, len(authority), 0) + questions + b"".join(authority)

    def conflicts(self, message):
        for record in message["records"]:
            if record["class"] != 1 or not record["ttl"]:
                continue
            if record["name"] == self.hostname and record["type"] == 1 and record["data"] != socket.inet_aton(self.address):
                return True
            if record["name"] == self.instance.lower() and record["type"] == 33 and record["value"] != (0, 0, self.port, self.hostname):
                return True
        return False

    def response(self, message, peer_port):
        if message["flags"] & 0x8000 or message["flags"] & 0x7800:
            return None
        records = self._records(legacy=peer_port != MDNS_PORT)
        matching = [record for record in records if any(q["class"] in (1, 255) and q["name"] == record[0] and q["type"] in (255, record[1]) for q in message["questions"])]
        if not matching:
            return None
        legacy = peer_port != MDNS_PORT
        additional = [record for record in records if record not in matching]
        header = struct.pack("!6H", message["id"] if legacy else 0, 0x8400, len(message["questions"]) if legacy else 0, len(matching), 0, len(additional))
        packet = header + (message["questionWire"] if legacy else b"") + b"".join(record[2] for record in matching + additional)
        return packet, legacy or any(q["unicast"] for q in message["questions"])

    def _open(self, address):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", MDNS_PORT))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(GROUP) + socket.inet_aton(address))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.settimeout(0.25)
        except Exception:
            sock.close()
            raise
        self._socket = sock
        self.address = address

    def _send(self, packet, destination=(GROUP, MDNS_PORT)):
        self._socket.sendto(packet, destination)

    def _receive(self, probing=False):
        try:
            packet, peer = self._socket.recvfrom(4097)
        except socket.timeout:
            return True
        if not is_local_peer(peer[0]):
            return True
        try:
            message = parse_message(packet)
        except (ValueError, UnicodeError, struct.error):
            return True
        if self.conflicts(message):
            # Never silently claim a conflicting name: the certificate is bound
            # to the persisted hostname. Owner can repair/reissue it explicitly.
            self._set_state("conflict", "The dashboard's unique local hostname is already claimed by another device. Use its private IP and regenerate LAN identity/certificate before advertising again.")
            return False
        if not probing:
            reply = self.response(message, peer[1])
            if reply and time.monotonic() - self._last_sent > 0.05:
                data, unicast = reply
                self._send(data, peer if unicast else (GROUP, MDNS_PORT))
                self._last_sent = time.monotonic()
        return True

    def _claim(self):
        self._set_state("probing")
        if self._stop.wait(random.uniform(0, 0.25)):
            return False
        for _ in range(3):
            self._send(self.probe())
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline and not self._stop.is_set():
                if not self._receive(probing=True):
                    return False
        if self._stop.is_set():
            return False
        self._send(self.announcement())
        self._set_state("advertising")
        return True

    def _run(self):
        try:
            while not self._stop.is_set():
                address = self.address_supplier()
                if not address or not is_local_peer(address) or not isinstance(ipaddress.ip_address(address), ipaddress.IPv4Address) or ipaddress.ip_address(address).is_loopback:
                    self._set_state("waiting", "No active private IPv4 interface is available for local discovery.")
                    if self._stop.wait(5):
                        break
                    continue
                self._open(address)
                if not self._claim():
                    break
                next_announcement = time.monotonic() + 1
                next_address_check = time.monotonic() + 5
                while not self._stop.is_set():
                    if not self._receive():
                        return
                    now = time.monotonic()
                    if now >= next_announcement:
                        self._send(self.announcement())
                        next_announcement = now + 60
                    if now >= next_address_check:
                        next_address_check = now + 5
                        if self.address_supplier() != self.address:
                            try:
                                self._send(self.announcement(0))
                            except OSError:
                                pass
                            self._socket.close()
                            self._socket = None
                            break
        except OSError:
            self._set_state("unavailable", "Local mDNS advertisement could not bind or send. Use the HTTPS private-IP address; check that this LAN permits multicast.")
        finally:
            if self._socket:
                try:
                    if self.state == "advertising":
                        self._send(self.announcement(0))
                except OSError:
                    pass
                self._socket.close()
                self._socket = None
            if self.state not in {"conflict", "unavailable"}:
                self._set_state("stopped")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="local-mdns", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
