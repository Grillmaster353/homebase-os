"""Bounded, local-network integration clients. Mutations run only after approval.

TV control supports explicit direct-protocol configurations and existing Home
Assistant gateways. Commands still execute only after approval.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import tempfile
import time
import urllib.parse
import urllib.request


class IntegrationError(ValueError):
    pass


_LAN_RANGES = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128", "fc00::/7"))


def validate_url(url, allow_remote=False):
    """Resolve once, validate every address, and return an IP to pin transport to."""
    try:
        parsed = urllib.parse.urlsplit(str(url))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise IntegrationError("Enter a valid URL and port.") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise IntegrationError("Use an HTTP(S) URL without credentials or fragments.")
    if any(c.isspace() or ord(c) < 32 for c in url) or "%" in parsed.hostname:
        raise IntegrationError("Invalid destination address.")
    if allow_remote and parsed.scheme != "https":
        raise IntegrationError("Remote AI providers require HTTPS.")
    try:
        ips = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)))
    except OSError as exc:
        raise IntegrationError("Could not resolve the configured host.") from exc
    if not ips:
        raise IntegrationError("The configured host has no address.")
    for address in ips:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        local = any(ip in network for network in _LAN_RANGES)
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or (not local and not (allow_remote and ip.is_global)):
            raise IntegrationError("Destination must be a local/private host. Remote AI requires an explicitly configured HTTPS provider.")
    return parsed, ips[0], port


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, ip, port, timeout):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _digest_header(challenge, username, password, method, uri):
    if not challenge.lower().startswith("digest "):
        raise IntegrationError("Device did not offer supported Digest authentication.")
    fields = urllib.request.parse_keqv_list(urllib.request.parse_http_list(challenge[7:]))
    algorithm = fields.get("algorithm", "MD5").upper()
    if algorithm not in ("MD5", "SHA-256", "MD5-SESS", "SHA-256-SESS"):
        raise IntegrationError("Camera Digest algorithm is unsupported.")
    digest = hashlib.sha256 if algorithm.startswith("SHA-256") else hashlib.md5
    h = lambda value: digest(value.encode("utf-8")).hexdigest()
    nonce, realm = fields.get("nonce", ""), fields.get("realm", "")
    if not nonce:
        raise IntegrationError("Camera returned an invalid authentication challenge.")
    cnonce, nc = secrets.token_hex(16), "00000001"
    a1 = h(f"{username}:{realm}:{password}")
    if algorithm.endswith("-SESS"):
        a1 = h(f"{a1}:{nonce}:{cnonce}")
    a2 = h(f"{method}:{uri}")
    qops = [x.strip() for x in fields.get("qop", "").split(",")]
    qop = "auth" if "auth" in qops else None
    if fields.get("qop") and not qop:
        raise IntegrationError("Camera requires unsupported Digest protection.")
    response = h(f"{a1}:{nonce}:{nc}:{cnonce}:auth:{a2}") if qop else h(f"{a1}:{nonce}:{a2}")
    values = {"username": username, "realm": realm, "nonce": nonce, "uri": uri, "response": response}
    if fields.get("opaque"):
        values["opaque"] = fields["opaque"]
    quote = lambda value: '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
    parts = [f"{key}={quote(value)}" for key, value in values.items()]
    parts.append(f"algorithm={algorithm}")
    if qop:
        parts += ["qop=auth", f"nc={nc}", f"cnonce={quote(cnonce)}"]
    return "Digest " + ", ".join(parts)


def safe_request(url, method="GET", data=None, headers=None, timeout=5,
                 allow_remote=False, max_bytes=4 * 1024 * 1024, username=None, password=None):
    parsed, address, port = validate_url(url, allow_remote)
    uri = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    request_headers = {"Accept": "application/json", "User-Agent": "HomeCommand/1.0", **(headers or {})}
    if isinstance(data, (dict, list)):
        data = json.dumps(data, allow_nan=False).encode()
        request_headers.setdefault("Content-Type", "application/json")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if port != (443 if parsed.scheme == "https" else 80):
        host += f":{port}"
    request_headers["Host"] = host
    if method not in ("GET", "POST"):
        raise IntegrationError("Unsupported integration request method.")
    for attempt in range(2):
        conn = (_PinnedHTTPS(parsed.hostname, address, port, min(60, timeout)) if parsed.scheme == "https"
                else http.client.HTTPConnection(address, port, timeout=min(60, timeout)))
        try:
            conn.request(method, uri, body=data, headers=request_headers)
            response = conn.getresponse()
            if response.status == 401 and username is not None and attempt == 0:
                challenge = response.getheader("WWW-Authenticate", "")
                request_headers["Authorization"] = _digest_header(challenge, str(username), str(password or ""), method, uri)
                continue
            if response.status in (301, 302, 303, 307, 308):
                raise IntegrationError("Integration redirected the request. Configure its final local URL directly.")
            if response.status == 401 or response.status == 403:
                raise IntegrationError("Authorization failed. Check the saved credentials and device permissions.")
            if response.status < 200 or response.status >= 300:
                raise IntegrationError(f"Integration returned HTTP {response.status}.")
            length = response.getheader("Content-Length")
            if length and int(length) > max_bytes:
                raise IntegrationError("Integration response exceeded the size limit.")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise IntegrationError("Integration response exceeded the size limit.")
            return body
        except IntegrationError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise IntegrationError("Could not reach the configured service. Check its address, certificate, and network connection.") from exc
        finally:
            conn.close()
    raise IntegrationError("Device authentication failed.")


def base_url(config, default_port=None):
    raw = str(config.get("host") or config.get("baseUrl") or "").strip()
    if not raw:
        raise IntegrationError("Enter the service's local address first.")
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise IntegrationError("Service addresses cannot include credentials, queries, or fragments.")
        if not parsed.port and (config.get("port") or (default_port and "://" not in str(config.get("host", "")))):
            port = int(config.get("port") or default_port)
            if not 1 <= port <= 65535:
                raise IntegrationError("Port must be between 1 and 65535.")
            hostname = f"[{parsed.hostname}]" if ":" in (parsed.hostname or "") else parsed.hostname
            raw = urllib.parse.urlunsplit((parsed.scheme, f"{hostname}:{port}", parsed.path, "", ""))
    except ValueError as exc:
        raise IntegrationError("Invalid service address or port.") from exc
    return raw.rstrip("/")


def _ha(config, path, method="GET", data=None):
    token = config.get("token") or config.get("accessToken")
    if not token:
        raise IntegrationError("A Home Assistant long-lived access token is required.")
    raw = safe_request(base_url(config, 8123) + path, method=method, data=data,
                       headers={"Authorization": f"Bearer {token}"})
    try:
        return json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise IntegrationError("Home Assistant returned an invalid JSON response.") from exc


def get_entities(config):
    states = _ha(config, "/api/states")
    if not isinstance(states, list):
        raise IntegrationError("Home Assistant returned invalid entity data.")
    result = []
    for entity in states[:3000]:
        attributes = entity.get("attributes", {})
        result.append({"entityId": entity.get("entity_id"), "entity_id": entity.get("entity_id"), "state": entity.get("state"),
            "name": attributes.get("friendly_name", entity.get("entity_id")),
            "domain": str(entity.get("entity_id", "")).split(".")[0],
            "attributes": {k: v for k, v in attributes.items() if k in (
                "friendly_name", "device_class", "unit_of_measurement", "brightness", "temperature", "current_temperature",
                "volume_level", "is_volume_muted", "media_title", "source", "supported_features", "hvac_modes")},
            "integrationId": config.get("id"), "lastChanged": entity.get("last_changed")})
    return result


def get_snapshot(config):
    channel = str(config.get("channel", "101"))
    if not re.fullmatch(r"[1-9][0-9]{0,4}", channel):
        raise IntegrationError("Camera channel must be a numeric Hikvision channel, such as 101.")
    if not config.get("username") or not config.get("password"):
        raise IntegrationError("Authorized camera username and password are required.")
    raw = safe_request(base_url(config, 80) + f"/ISAPI/Streaming/channels/{channel}/picture",
                       headers={"Accept": "image/jpeg"}, username=config["username"], password=config["password"], max_bytes=8 * 1024 * 1024)
    if not raw.startswith(b"\xff\xd8"):
        raise IntegrationError("The camera did not return a JPEG snapshot. Check the ISAPI channel and permissions.")
    return raw


def _parse_metrics(text):
    values = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r'([a-zA-Z_:][\w:]*)((?:\{[^\n]*\})?)\s+([-+\w.eE]+)(?:\s+\d+)?', line.strip())
        if not m:
            continue
        try:
            value = float(m.group(3))
            if not (-1e300 < value < 1e300):
                continue
        except ValueError:
            continue
        labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m.group(2)))
        values.setdefault(m.group(1), []).append((labels, value))
    return values


_SERVER_SAMPLES = {}


def get_server(config):
    headers = {"Authorization": f"Bearer {config['token']}"} if config.get("token") else {}
    metrics_path = config.get("monitoringPath") or "/metrics"
    if (not isinstance(metrics_path, str) or len(metrics_path) > 500 or not metrics_path.startswith("/")
            or metrics_path.startswith("//") or "\\" in metrics_path
            or urllib.parse.urlsplit(metrics_path).scheme or urllib.parse.urlsplit(metrics_path).netloc
            or urllib.parse.urlsplit(metrics_path).fragment or urllib.parse.urlsplit(metrics_path).query
            or ".." in urllib.parse.unquote(metrics_path).split("/")
            or any(ord(c) < 33 for c in metrics_path)):
        raise IntegrationError("Metrics path must be a local URL path, such as /metrics.")
    raw = safe_request(base_url(config, 9100) + metrics_path, headers=headers, max_bytes=4 * 1024 * 1024)
    metrics = _parse_metrics(raw.decode("utf-8", "replace"))
    scalar = lambda key: metrics.get(key, [({}, None)])[0][1]
    total, available = scalar("node_memory_MemTotal_bytes"), scalar("node_memory_MemAvailable_bytes")
    used = total - available if total and available is not None else None
    cpu_total = sum(v for labels, v in metrics.get("node_cpu_seconds_total", []))
    cpu_idle = sum(v for labels, v in metrics.get("node_cpu_seconds_total", []) if labels.get("mode") == "idle")
    key, now = config.get("id") or base_url(config), time.monotonic()
    previous = _SERVER_SAMPLES.get(key)
    cpu_percent = None
    if previous and cpu_total > previous[0]:
        cpu_percent = round(max(0, min(100, (1 - (cpu_idle - previous[1]) / (cpu_total - previous[0])) * 100)), 1)
    _SERVER_SAMPLES[key] = (cpu_total, cpu_idle, now)
    size = next((v for l, v in metrics.get("node_filesystem_size_bytes", []) if l.get("mountpoint") == "/"), None)
    disk_free = next((v for l, v in metrics.get("node_filesystem_avail_bytes", []) if l.get("mountpoint") == "/"), None)
    return {"id": config.get("id"), "name": config.get("name", "Server"), "host": config.get("host"),
            "online": True, "cpuPercent": cpu_percent, "memoryTotal": total, "memoryUsed": used,
            "memoryPercent": round(used / total * 100, 1) if total and used is not None else None,
            "diskPercent": round((1 - disk_free / size) * 100, 1) if size and disk_free is not None else None,
            "uptimeSeconds": max(0, int(time.time() - scalar("node_boot_time_seconds"))) if scalar("node_boot_time_seconds") else None,
            "load1": scalar("node_load1"), "measurement": "node_exporter; CPU requires two samples"}


def test_integration(config):
    kind = config.get("type")
    if config.get("connectionMode") == "direct" and kind in {"samsung", "androidtv", "appletv", "hisense", "roku", "webos"}:
        from .direct_tv import test_tv
        return test_tv(config)
    if kind == "homeassistant":
        _ha(config, "/api/")
        return {"status": "connected", "message": "Home Assistant authorized REST API is connected."}
    if kind in ("samsung", "androidtv", "appletv", "hisense"):
        if config.get("homeAssistantId") and not config.get("token"):
            return {"status": "configured", "message": "Uses the saved Home Assistant gateway. Test that gateway and select a paired entity."}
        entity = str(config.get("entityId", ""))
        if not re.fullmatch(r"(?:media_player|remote)\.[a-z0-9_]+", entity):
            raise IntegrationError("Select a paired Home Assistant media_player or remote entity.")
        state = _ha(config, "/api/states/" + entity)
        return {"status": "connected" if state.get("state") not in ("unavailable", "unknown") else "unavailable",
                "message": f"Home Assistant entity {entity}: {state.get('state', 'unknown')}"}
    if kind == "hikvision":
        get_snapshot(config)
        return {"status": "connected", "message": "Authorized ISAPI JPEG snapshot received."}
    if kind == "server":
        result = get_server(config)
        if result.get("memoryTotal") is None and result.get("load1") is None:
            raise IntegrationError("No node_exporter system metrics found at /metrics.")
        return {"status": "connected", "message": "Prometheus node_exporter metrics are available."}
    if kind == "ollama":
        body = json.loads(safe_request(base_url(config, 11434) + "/api/tags"))
        return {"status": "connected", "message": f"Ollama is available with {len(body.get('models', []))} installed models."}
    raise IntegrationError("Unsupported integration type.")


def _workspace_path(workspace, relative):
    if not isinstance(relative, str) or not relative or len(relative) > 500 or "\x00" in relative:
        raise IntegrationError("Enter a relative workspace file path.")
    rel = Path(relative)
    if rel.is_absolute() or any(part in ("..", ".") for part in rel.parts):
        raise IntegrationError("File paths must stay inside the approved workspace.")
    root = Path(workspace).expanduser().resolve()
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise IntegrationError("Symlinks are not allowed for AI workspace access.")
    target = (root / rel).resolve()
    if root not in target.parents:
        raise IntegrationError("File paths must stay inside the approved workspace.")
    return root, target


def execute_action(action, integrations, workspace):
    """Called by the authenticated approval route only. Never used by chat()."""
    kind, payload = action.get("type"), action.get("payload") or {}
    if kind in ("file.read", "file.write"):
        root, path = _workspace_path(workspace, payload.get("path"))
        if kind == "file.read":
            if not path.is_file() or path.stat().st_size > 128 * 1024:
                raise IntegrationError("File must exist and be smaller than 128 KiB.")
            return {"path": str(path.relative_to(root)), "content": path.read_text(encoding="utf-8")}
        content = payload.get("content")
        if not isinstance(content, str) or len(content.encode()) > 1024 * 1024:
            raise IntegrationError("File writes require text smaller than 1 MiB.")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Re-check after making parents; block traversal through existing symlinks.
        root, path = _workspace_path(workspace, payload.get("path"))
        fd, temporary = tempfile.mkstemp(prefix=".hc-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"path": str(path.relative_to(root)), "bytes": len(content.encode()), "message": "Workspace file saved."}
    config = next((c for c in integrations if c.get("id") == action.get("target")), None)
    if not config:
        raise IntegrationError("Choose a saved integration as the action target.")
    if kind == "tv.command" and config.get("connectionMode") == "direct":
        from .direct_tv import execute_tv_command
        return execute_tv_command(config, payload, approved=action.get("status") == "executing")
    if config.get("homeAssistantId"):
        gateway = next((c for c in integrations if c.get("id") == config["homeAssistantId"] and c.get("type") == "homeassistant"), None)
        if not gateway:
            raise IntegrationError("The selected Home Assistant gateway is no longer configured.")
        config = {**gateway, "entityId": config.get("entityId"), "remoteEntityId": config.get("remoteEntityId")}
    if kind == "tv.command":
        entity = str(config.get("entityId") or payload.get("entityId") or "")
        if not re.fullmatch(r"media_player\.[a-z0-9_]+", entity):
            raise IntegrationError("TV commands require a paired Home Assistant media_player entity.")
        command = payload.get("command")
        services = {"power_on": "turn_on", "power_off": "turn_off", "volume_up": "volume_up",
                    "volume_down": "volume_down", "play": "media_play", "pause": "media_pause",
                    "play_pause": "media_play_pause", "stop": "media_stop", "next": "media_next_track", "previous": "media_previous_track"}
        if command in services:
            result = _ha(config, f"/api/services/media_player/{services[command]}", "POST", {"entity_id": entity})
        elif command == "mute":
            result = _ha(config, "/api/services/media_player/volume_mute", "POST", {"entity_id": entity, "is_volume_muted": bool(payload.get("muted", True))})
        elif command in ("up", "down", "left", "right", "select", "home", "back", "menu"):
            remote = str(config.get("remoteEntityId") or "")
            if not re.fullmatch(r"remote\.[a-z0-9_]+", remote):
                raise IntegrationError("Navigation requires a paired Home Assistant remote entity (remoteEntityId).")
            result = _ha(config, "/api/services/remote/send_command", "POST", {"entity_id": remote, "command": command})
        else:
            raise IntegrationError("Unsupported TV command.")
        return {"message": f"Home Assistant accepted {command} for {entity}.", "updatedEntities": len(result) if isinstance(result, list) else None}
    if kind == "ha.service":
        domain, service = str(payload.get("domain", "")), str(payload.get("service", ""))
        if domain not in ("light", "switch", "scene", "automation", "script", "climate", "media_player", "remote", "cover", "fan", "lock", "button", "input_boolean", "vacuum"):
            raise IntegrationError("This Home Assistant domain is outside the home-control allowlist.")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,60}", service):
            raise IntegrationError("Invalid Home Assistant service name.")
        entity = str(payload.get("entityId", ""))
        if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", entity) or not entity.startswith(domain + "."):
            raise IntegrationError("Choose an entity belonging to the selected service domain.")
        data = payload.get("data") or {}
        if not isinstance(data, dict) or len(json.dumps(data)) > 16 * 1024:
            raise IntegrationError("Service data must be a small JSON object.")
        result = _ha(config, f"/api/services/{domain}/{service}", "POST", {**data, "entity_id": entity})
        return {"message": f"Home Assistant accepted {domain}.{service} for {entity}.", "updatedEntities": len(result) if isinstance(result, list) else None}
    raise IntegrationError("Unsupported action type.")
