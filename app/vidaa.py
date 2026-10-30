"""Bounded VIDAA MQTT transport using an owner-provided remote identity.

No vendor passwords or private certificates are bundled. An approved leaf
certificate SHA-256 pin, or a CA with normal hostname verification, is required
before MQTT credentials are transmitted. PIN request acknowledgments are kept
separate from the TV's actual PIN-display notification.

Protocol references: warrenrees/pyvidaa, pyvidaa/{client,topics}.py.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import ssl
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field


class VidaaError(ValueError):
    """A sanitized, user-facing VIDAA failure."""


_KEYS = {
    "power_toggle": "KEY_POWER",
    "volume_up": "KEY_VOLUMEUP", "volume_down": "KEY_VOLUMEDOWN",
    "channel_up": "KEY_CHANNELUP", "channel_down": "KEY_CHANNELDOWN",
    "mute": "KEY_MUTE", "up": "KEY_UP", "down": "KEY_DOWN",
    "left": "KEY_LEFT", "right": "KEY_RIGHT", "select": "KEY_OK",
    "home": "KEY_HOME", "back": "KEY_RETURNS", "menu": "KEY_MENU",
    "play": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP",
}
# VIDAA's power key is exposed only as an explicit toggle; it cannot honestly
# implement directional power_on / power_off actions. Exact key spellings are
# documented in warrenrees/pyvidaa, pyvidaa/keys.py.
_KEYS.update(info="KEY_INFO",exit="KEY_EXIT",red="KEY_RED",green="KEY_GREEN",yellow="KEY_YELLOW",blue="KEY_BLUE")
_KEYS.update({f"digit_{digit}":f"KEY_{digit}" for digit in range(10)})
SUPPORTED_COMMANDS = tuple(_KEYS) + ("text",)
PAIR_TTL = 180
OP_TIMEOUT = 8.0
PIN_WAIT = 2.0
MAX_PACKET = 262144
MAX_MESSAGES = 64
_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "127.0.0.0/8", "::1/128", "fc00::/7",
))


def _text(value, label, limit, *, optional=False):
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise VidaaError(f"The VIDAA profile needs a valid {label}.")
    try:
        if len(value.encode("utf-8")) > limit:
            raise ValueError
    except (UnicodeError, ValueError):
        raise VidaaError(f"The VIDAA profile has an invalid {label}.") from None
    return value


def validate_profile(profile):
    """Normalize owner-provided configuration without files or network I/O."""
    if not isinstance(profile, dict) or not profile:
        raise VidaaError("Add an owner-provided VIDAA profile with MQTT identity, client certificate/key, and an approved server fingerprint or CA before pairing.")
    allowed = {"clientId", "username", "password", "certificatePem", "privateKeyPem",
               "caPem", "serverFingerprint", "authMode"}
    if set(profile) - allowed:
        raise VidaaError("The VIDAA profile contains unsupported fields.")
    result = {
        key: _text(profile.get(key), label, limit)
        for key, label, limit in (
            ("clientId", "client ID", 512), ("username", "MQTT username", 1024),
            ("password", "MQTT password", 16384),
            ("certificatePem", "client certificate PEM", 131072),
            ("privateKeyPem", "client private key PEM", 131072),
        )
    }
    if re.search(r"[/+#\x00-\x20\x7f]", result["clientId"]):
        raise VidaaError("The VIDAA client ID cannot contain MQTT topic separators, wildcards or whitespace.")
    if "-----BEGIN CERTIFICATE-----" not in result["certificatePem"] or not re.search(
        r"-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----", result["privateKeyPem"]
    ):
        raise VidaaError("The VIDAA profile needs PEM client certificate and private key material.")
    result["caPem"] = _text(profile.get("caPem"), "CA certificate PEM", 131072, optional=True)
    if result["caPem"] and "-----BEGIN CERTIFICATE-----" not in result["caPem"]:
        raise VidaaError("The VIDAA CA must be a PEM certificate.")
    fingerprint = _text(profile.get("serverFingerprint"), "server certificate fingerprint", 128, optional=True)
    fingerprint = re.sub(r"^sha256:", "", fingerprint, flags=re.I).replace(":", "").lower()
    if fingerprint and not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise VidaaError("Use the approved server certificate's SHA-256 fingerprint (64 hexadecimal characters).")
    if not fingerprint and not result["caPem"]:
        raise VidaaError("An approved VIDAA server certificate fingerprint or CA is required; unverified TLS is refused.")
    result["serverFingerprint"] = fingerprint
    result["authMode"] = profile.get("authMode", "modern")
    if not isinstance(result["authMode"], str) or result["authMode"] not in {"modern", "legacy"}:
        raise VidaaError("VIDAA authMode must be modern or legacy for the owner's configured identity.")
    return result


def _target(address, port):
    try:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not any(ip in network for network in _NETWORKS) or isinstance(port, bool) or not 1 <= int(port) <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        raise VidaaError("VIDAA needs a validated numeric private-network address and service port.") from None
    return str(ip), int(port)


def _remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise VidaaError("The VIDAA operation timed out.")
    return value


def _tls_connection(address, port, profile, deadline):
    """Authenticate TLS before the caller is permitted to send MQTT CONNECT."""
    raw = secured = None
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if profile["caPem"]:
            context.load_verify_locations(cadata=profile["caPem"])
            context.verify_mode = ssl.CERT_REQUIRED
            # An explicit approved leaf pin supplies identity when the vendor
            # certificate has no host/IP SAN. The CA chain is still checked.
            context.check_hostname = not bool(profile["serverFingerprint"])
        else:
            # Pin-only trust is explicit. No MQTT bytes are sent until the
            # certificate below has matched that pre-approved exact leaf pin.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        with tempfile.TemporaryDirectory(prefix="homebase-vidaa-") as directory:
            cert_path, key_path = (os.path.join(directory, name) for name in ("client.pem", "client.key"))
            for path, content in ((cert_path, profile["certificatePem"]), (key_path, profile["privateKeyPem"])):
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    output.write(content)
            # No password callback: an encrypted/invalid key fails instead of
            # invoking an interactive terminal password prompt in the server.
            context.load_cert_chain(cert_path, key_path, password=lambda: "")
        raw = socket.create_connection((address, port), timeout=_remaining(deadline))
        raw.settimeout(_remaining(deadline))
        secured = context.wrap_socket(raw, server_hostname=address)
        expected = profile["serverFingerprint"]
        if expected:
            certificate = secured.getpeercert(binary_form=True)
            if not certificate or not hmac.compare_digest(hashlib.sha256(certificate).hexdigest(), expected):
                raise VidaaError("The VIDAA server certificate does not match the approved fingerprint. No MQTT credentials were sent.")
        return secured
    except VidaaError:
        if secured is not None:
            secured.close()
        elif raw is not None:
            raw.close()
        raise
    except Exception:
        if secured is not None:
            secured.close()
        elif raw is not None:
            raw.close()
        raise VidaaError("The VIDAA TLS connection or client identity could not be verified. Check the configured certificate, private key, CA/fingerprint and TV endpoint.") from None


def _string(value):
    encoded = value.encode("utf-8")
    if len(encoded) > 65535 or b"\x00" in encoded:
        raise VidaaError("Invalid VIDAA MQTT string.")
    return struct.pack("!H", len(encoded)) + encoded


def _packet(header, body=b""):
    if len(body) > MAX_PACKET:
        raise VidaaError("The VIDAA MQTT packet is too large.")
    remaining = len(body)
    prefix = bytearray([header])
    while True:
        digit = remaining % 128
        remaining //= 128
        prefix.append(digit | (128 if remaining else 0))
        if not remaining:
            return bytes(prefix) + body


class _IdleTimeout(Exception):
    pass


@dataclass(repr=False)
class PairingHandle:
    """Opaque in-memory session. Never serialize or log its private fields."""
    _socket: object
    _profile: dict
    _expires: float = field(default_factory=lambda: time.monotonic() + PAIR_TTL)
    _pending: list = field(default_factory=list)
    _lock: object = field(default_factory=threading.Lock)
    pin_shown: bool = False
    request_accepted: bool = False
    closed: bool = False

    def __repr__(self):
        return f"<VIDAA pairing closed={self.closed} pin_shown={self.pin_shown}>"

    def _send(self, data, deadline):
        self._socket.settimeout(_remaining(deadline))
        self._socket.sendall(data)

    def _read(self, size, deadline, *, idle=False):
        result = bytearray()
        while len(result) < size:
            self._socket.settimeout(_remaining(deadline))
            try:
                piece = self._socket.recv(size - len(result))
            except (socket.timeout, TimeoutError):
                if idle and not result:
                    raise _IdleTimeout from None
                raise VidaaError("The VIDAA response was incomplete or timed out.") from None
            if not piece:
                raise VidaaError("The VIDAA connection closed before the response completed.")
            result.extend(piece)
        return bytes(result)

    def _receive(self, deadline):
        header = self._read(1, deadline, idle=True)[0]
        size, multiplier = 0, 1
        for index in range(4):
            digit = self._read(1, deadline)[0]
            size += (digit & 127) * multiplier
            if size > MAX_PACKET:
                raise VidaaError("The VIDAA response exceeded the permitted size.")
            if not digit & 128:
                if index and digit == 0:
                    raise VidaaError("The VIDAA response has invalid MQTT framing.")
                return header, self._read(size, deadline) if size else b""
            multiplier *= 128
        raise VidaaError("The VIDAA response has invalid MQTT framing.")

    def _publish(self, service, action, payload, deadline, *, packet_id=None):
        topic = f"/remoteapp/tv/{service}/{self._profile['clientId']}/actions/{action}"
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send(_packet(0x32 if packet_id else 0x30, _string(topic) +
                           (struct.pack("!H", packet_id) if packet_id else b"") + payload), deadline)

    def _published_message(self, header, body, deadline):
        if header >> 4 != 3 or len(body) < 2:
            raise VidaaError("The VIDAA server sent an unexpected MQTT response.")
        length = struct.unpack("!H", body[:2])[0]
        offset = 2 + length
        qos = (header >> 1) & 3
        if not length or length > 2048 or offset > len(body) or qos > 1:
            raise VidaaError("The VIDAA server sent an invalid MQTT publication.")
        try:
            topic = body[2:offset].decode("utf-8")
        except UnicodeError:
            raise VidaaError("The VIDAA server sent an invalid MQTT topic.") from None
        if qos:
            if len(body) < offset + 2 or body[offset:offset + 2] == b"\x00\x00":
                raise VidaaError("The VIDAA server sent an invalid MQTT packet ID.")
            self._send(_packet(0x40, body[offset:offset + 2]), deadline)
            offset += 2
        return topic, body[offset:], bool(header & 1)

    def _message(self, deadline):
        if self._pending:
            return self._pending.pop(0)
        for _ in range(MAX_MESSAGES):
            try:
                header, body = self._receive(deadline)
            except _IdleTimeout:
                return None
            if header == 0xD0 and not body:
                continue
            if header == 0xC0 and not body:
                self._send(_packet(0xD0), deadline)
                continue
            return self._published_message(header, body, deadline)
        raise VidaaError("The VIDAA server exceeded the permitted message count.")


def close(handle):
    """Release a pairing connection; safe to call repeatedly."""
    if not isinstance(handle, PairingHandle) or handle.closed:
        return
    handle.closed = True
    with contextlib.suppress(Exception):
        handle._socket.close()
    handle._pending.clear()
    handle._profile = {}


def _connect(address, port, profile, deadline, credentials=None):
    address, port = _target(address, port)
    secured = _tls_connection(address, port, profile, deadline)
    handle = PairingHandle(secured, profile)
    try:
        password = credentials["accessToken"] if credentials and profile["authMode"] == "modern" else profile["password"]
        body = _string("MQTT") + b"\x04\xc2" + struct.pack("!H", 180)
        body += _string(profile["clientId"]) + _string(profile["username"]) + _string(password)
        handle._send(_packet(0x10, body), deadline)
        header, response = handle._receive(deadline)
        if header != 0x20 or len(response) != 2 or response[0] != 0 or response[1] > 5:
            raise VidaaError("The TV did not return a valid VIDAA MQTT connection response.")
        if response[1]:
            raise VidaaError("VIDAA rejected the configured MQTT identity or saved pairing. Verify the profile and pair again; no alternate passwords were attempted.")
        return handle
    except BaseException:
        close(handle)
        raise


def _topics(client):
    base = f"/remoteapp/mobile/{client}"
    return [f"{base}/ui_service/data/{name}" for name in (
        "authentication", "authenticationcode", "authenticationcodeclose",
        "vidaa_app_connect", "tokenissuance", "state",
    )] + [f"{base}/platform_service/data/tokenissuance", "/remoteapp/mobile/broadcast/ui_service/state"]


def _subscribe(handle, deadline):
    topics = _topics(handle._profile["clientId"])
    optional = {
        f"/remoteapp/mobile/{handle._profile['clientId']}/ui_service/data/state",
        "/remoteapp/mobile/broadcast/ui_service/state",
    }
    handle._send(_packet(0x82, b"\x00\x01" + b"".join(_string(topic) + b"\x00" for topic in topics)), deadline)
    for _ in range(MAX_MESSAGES):
        header, body = handle._receive(deadline)
        if header == 0x90:
            if body[:2] != b"\x00\x01" or len(body) != len(topics) + 2 or any(
                code not in (0, 1, 128) or (code == 128 and topic not in optional)
                for topic, code in zip(topics, body[2:])
            ):
                raise VidaaError("VIDAA did not authorize the required pairing subscriptions.")
            return
        handle._pending.append(handle._published_message(header, body, deadline))
    raise VidaaError("VIDAA did not acknowledge the pairing subscriptions.")


def _json(payload):
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError):
        raise VidaaError("VIDAA returned an invalid protocol response.") from None
    return value if isinstance(value, dict) else {}


def _relevant(handle, topic, retained):
    # Pairing success must never be inferred from retained/stale broadcasts or
    # another client's messages. Only the exact subscribed client is trusted.
    return not retained and topic.startswith(f"/remoteapp/mobile/{handle._profile['clientId']}/")


def start_pairing(address, port, profile):
    """Return a live opaque handle; its booleans describe observed TV signals."""
    profile = validate_profile(profile)
    handle = None
    try:
        deadline = time.monotonic() + OP_TIMEOUT
        handle = _connect(address, port, profile, deadline)
        _subscribe(handle, deadline)
        if profile["authMode"] == "legacy":
            handle._publish("ui_service", "gettvstate", b"", deadline)
        else:
            handle._publish("ui_service", "vidaa_app_connect", {"app_version": 2, "connect_result": 0, "device_type": "Mobile App"}, deadline)
        deadline = min(deadline, time.monotonic() + PIN_WAIT)
        for _ in range(MAX_MESSAGES):
            message = handle._message(deadline)
            if message is None:
                break
            topic, payload, retained = message
            if not _relevant(handle, topic, retained):
                continue
            if topic.endswith("/ui_service/data/authentication"):
                handle.pin_shown = True
                return handle
            if topic.endswith("/ui_service/data/vidaa_app_connect"):
                handle.request_accepted = _json(payload).get("connect_result") == 1
            elif profile["authMode"] == "legacy" and topic.endswith("/ui_service/data/state"):
                handle.request_accepted = bool(_json(payload))
        if handle.request_accepted:
            # Some firmware displays a PIN without the authentication push.
            # The caller must keep PIN entry available and explain that this
            # weaker acknowledgment does not establish a displayed PIN or an
            # authenticated connection. finish_pairing still requires both.
            return handle
        raise VidaaError("VIDAA did not confirm the PIN request. Check that the TV screen is awake, the identity is supported and this is a VIDAA endpoint.")
    except VidaaError:
        close(handle)
        raise
    except Exception:
        close(handle)
        raise VidaaError("VIDAA pairing could not start. Check the endpoint, configured identity and TV screen.") from None


def _expiry(payload, prefix):
    try:
        issued = float(payload[f"{prefix}_time"])
        days = float(payload[f"{prefix}_duration_day"])
        if 0 < issued < 1e11 and 0 < days <= 3650:
            return issued + days * 86400
    except (KeyError, ValueError, TypeError, OverflowError):
        pass
    return None


def finish_pairing(handle, code):
    """Submit the TV's PIN and return credentials for the caller's encrypted vault."""
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{4}", code):
        raise VidaaError("Enter the four-digit PIN displayed on the VIDAA TV.")
    if not isinstance(handle, PairingHandle) or handle.closed or time.monotonic() >= handle._expires:
        close(handle)
        raise VidaaError("VIDAA pairing expired or closed. Start pairing again.")
    if not handle._lock.acquire(blocking=False):
        raise VidaaError("VIDAA pairing is already being completed.")
    try:
        deadline = time.monotonic() + OP_TIMEOUT
        handle._publish("ui_service", "authenticationcode", {"authNum": int(code)}, deadline)
        accepted, token_payload = False, None
        for _ in range(MAX_MESSAGES):
            message = handle._message(deadline)
            if message is None:
                break
            topic, payload, retained = message
            if not _relevant(handle, topic, retained):
                continue
            if topic.endswith("/ui_service/data/authenticationcode"):
                result = _json(payload).get("result")
                if result == 0:
                    raise VidaaError("VIDAA rejected the PIN. Start pairing again and enter the current code.")
                if result == 1:
                    accepted = True
                    if handle._profile["authMode"] == "modern":
                        token_topic = f"/remoteapp/tv/platform_service/{handle._profile['clientId']}/data/gettoken"
                        handle._send(_packet(0x30, _string(token_topic) + b'{"refreshtoken":""}'), deadline)
                    handle._publish("ui_service", "authenticationcodeclose", b"", deadline)
            elif accepted and topic.endswith(("/platform_service/data/tokenissuance", "/ui_service/data/tokenissuance")):
                token_payload = _json(payload)
            if accepted and (handle._profile["authMode"] == "legacy" or token_payload):
                result = {"clientId": handle._profile["clientId"], "username": handle._profile["username"], "authMode": handle._profile["authMode"], "paired": True}
                if handle._profile["authMode"] == "modern":
                    for source, target in (("accesstoken", "accessToken"), ("refreshtoken", "refreshToken")):
                        result[target] = _text(token_payload.get(source), "issued pairing token", 16384)
                    for prefix, target in (("accesstoken", "accessTokenExpiresAt"), ("refreshtoken", "refreshTokenExpiresAt")):
                        expiry = _expiry(token_payload, prefix)
                        if expiry is not None:
                            result[target] = expiry
                return result
        raise VidaaError("VIDAA did not complete PIN authentication and token issuance. Start pairing again; the TV has not been marked paired.")
    except VidaaError:
        raise
    except Exception:
        raise VidaaError("VIDAA pairing could not complete. Start pairing again and check the TV screen.") from None
    finally:
        close(handle)
        handle._lock.release()


def _validate_credentials(profile, credentials):
    if not isinstance(credentials, dict) or credentials.get("paired") is not True or any(
        credentials.get(key) != profile[key] for key in ("clientId", "username", "authMode")
    ):
        raise VidaaError("Pair this configured VIDAA identity before sending commands.")
    if profile["authMode"] == "modern":
        _text(credentials.get("accessToken"), "saved pairing token", 16384)
        expires = credentials.get("accessTokenExpiresAt")
        if expires is not None and (not isinstance(expires, (float, int)) or expires <= time.time()):
            raise VidaaError("The VIDAA access token expired. Pair the TV again before sending commands.")


def verify_connection(address, port, profile, credentials):
    """Verify saved-pairing TLS/MQTT authentication without issuing TV actions.

    Sends only MQTT CONNECT, waits for CONNACK and closes. It never subscribes,
    publishes, requests a PIN, refreshes tokens or sends remote keys.
    """
    profile = validate_profile(profile)
    _validate_credentials(profile, credentials)
    handle = None
    try:
        handle = _connect(address, port, profile, time.monotonic() + OP_TIMEOUT, credentials)
        return {"authenticated": True, "protocol": "vidaa", "stateConfirmed": False,
                "message": "The saved VIDAA pairing authenticated over verified TLS and MQTT. No TV command or PIN request was sent."}
    except VidaaError:
        raise
    except Exception:
        raise VidaaError("The saved VIDAA pairing could not authenticate. Check the TV endpoint and pairing validity.") from None
    finally:
        close(handle)


def command(address, port, profile, credentials, command, text=None):
    """Send one allowlisted key; MQTT acknowledgment does not prove TV state."""
    if command not in SUPPORTED_COMMANDS:
        raise VidaaError("This VIDAA action is not supported. Directional power control cannot use VIDAA's power toggle.")
    if command == "text" and (not isinstance(text,str) or not 1 <= len(text) <= 512 or len(text.encode('utf-8')) > 2048 or any(ord(c)<32 or ord(c)==127 for c in text)):
        raise VidaaError("Enter 1–512 text characters without control characters.")
    profile = validate_profile(profile)
    _validate_credentials(profile, credentials)
    handle = None
    try:
        deadline = time.monotonic() + OP_TIMEOUT
        handle = _connect(address, port, profile, deadline, credentials)
        pending = {2}
        if command == "text":
            # Native TV input and browser input have separate platform routes.
            # Payload text is never included in results or diagnostic messages.
            handle._publish("platform_service", "txtinputdata", {"text":text,"action":"insert"}, deadline, packet_id=2)
            handle._publish("platform_service", "bwsinputdata", {"text":text,"action":"insert"}, deadline, packet_id=3)
            pending.add(3)
        else:
            handle._publish("remote_service", "sendkey", _KEYS[command].encode("ascii"), deadline, packet_id=2)
        for _ in range(MAX_MESSAGES):
            header, body = handle._receive(deadline)
            if header == 0x40 and len(body)==2 and int.from_bytes(body,'big') in pending:
                pending.remove(int.from_bytes(body,'big'))
                if not pending:
                    return {"sent": True, "acknowledged": True, "stateConfirmed": False,
                            "message": "VIDAA acknowledged the remote input delivery; the resulting TV state is unverified."}
                continue
            if header >> 4 == 3:
                handle._published_message(header, body, deadline)
                continue
            if header == 0xD0 and not body:
                continue
            raise VidaaError("VIDAA returned an unexpected command acknowledgment.")
        raise VidaaError("VIDAA did not acknowledge the remote key.")
    except VidaaError:
        raise
    except Exception:
        raise VidaaError("The VIDAA remote key could not be delivered or acknowledged.") from None
    finally:
        close(handle)
