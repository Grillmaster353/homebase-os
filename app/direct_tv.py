"""Direct TV protocols with explicit pairing/control approval and bounded I/O.

Android TV Remote v2 and Apple TV use maintained optional client libraries.
Samsung/LG/Roku vendor-local cleartext channels require an explicit per-device
opt-in. No credentials are guessed and read-only tests never register a remote.
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import contextlib
import copy
import hashlib
import importlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit
import xml.etree.ElementTree as ET


class TVError(ValueError):
    pass


PROTOCOLS = {"samsung", "androidtv", "appletv", "roku", "webos", "vidaa"}
TV_TYPES = PROTOCOLS | {"hisense"}
DEFAULT_PORTS = {"samsung":8001,"androidtv":6466,"appletv":7000,"roku":8060,"webos":3000,"vidaa":36669}
_LOOP = None
_LOOP_LOCK = threading.Lock()
_SESSIONS = {}
PAIR_TTL = 180
MAX_SESSIONS = 8

_SAMSUNG_KEYS = {"power_on":"KEY_POWERON", "power_off":"KEY_POWEROFF", "volume_up":"KEY_VOLUP", "volume_down":"KEY_VOLDOWN", "mute":"KEY_MUTE", "play":"KEY_PLAY", "pause":"KEY_PAUSE", "play_pause":"KEY_PLAYPAUSE", "stop":"KEY_STOP", "next":"KEY_FF", "previous":"KEY_REWIND", "up":"KEY_UP", "down":"KEY_DOWN", "left":"KEY_LEFT", "right":"KEY_RIGHT", "select":"KEY_ENTER", "home":"KEY_HOME", "back":"KEY_RETURN", "menu":"KEY_MENU"}
_ANDROID_KEYS = {"power_on":"WAKEUP", "power_off":"SLEEP", "volume_up":"VOLUME_UP", "volume_down":"VOLUME_DOWN", "mute":"MUTE", "play":"MEDIA_PLAY", "pause":"MEDIA_PAUSE", "play_pause":"MEDIA_PLAY_PAUSE", "stop":"MEDIA_STOP", "next":"MEDIA_NEXT", "previous":"MEDIA_PREVIOUS", "up":"DPAD_UP", "down":"DPAD_DOWN", "left":"DPAD_LEFT", "right":"DPAD_RIGHT", "select":"DPAD_CENTER", "home":"HOME", "back":"BACK", "menu":"MENU"}
_ROKU_KEYS = {"power_on":"PowerOn", "power_off":"PowerOff", "volume_up":"VolumeUp", "volume_down":"VolumeDown", "mute":"VolumeMute", "play":"Play", "pause":"Play", "play_pause":"Play", "stop":"Back", "next":"Fwd", "previous":"Rev", "up":"Up", "down":"Down", "left":"Left", "right":"Right", "select":"Select", "home":"Home", "back":"Back", "menu":"Info"}
_APPLE_REMOTE = {"play":"play", "pause":"pause", "play_pause":"play_pause", "stop":"stop", "next":"next", "previous":"previous", "up":"up", "down":"down", "left":"left", "right":"right", "select":"select", "home":"home", "back":"menu", "menu":"menu"}
_LG_URIS = {"power_off":"ssap://system/turnOff", "volume_up":"ssap://audio/volumeUp", "volume_down":"ssap://audio/volumeDown", "mute":"ssap://audio/setMute", "play":"ssap://media.controls/play", "pause":"ssap://media.controls/pause", "play_pause":"ssap://media.controls/play", "stop":"ssap://media.controls/stop", "next":"ssap://media.controls/fastForward", "previous":"ssap://media.controls/rewind"}
_LG_BUTTONS = {"up":"UP", "down":"DOWN", "left":"LEFT", "right":"RIGHT", "select":"ENTER", "home":"HOME", "back":"BACK", "menu":"MENU"}
_SAMSUNG_KEYS.update(channel_up="KEY_CHUP",channel_down="KEY_CHDOWN",input="KEY_SOURCE",info="KEY_INFO",guide="KEY_GUIDE",exit="KEY_EXIT",red="KEY_RED",green="KEY_GREEN",yellow="KEY_YELLOW",blue="KEY_BLUE")
_ANDROID_KEYS.update(channel_up="CHANNEL_UP",channel_down="CHANNEL_DOWN",input="TV_INPUT",info="INFO",guide="GUIDE",delete="DEL",enter="ENTER")
_ROKU_KEYS.update(channel_up="ChannelUp",channel_down="ChannelDown")
_LG_URIS.update(channel_up="ssap://tv/channelUp",channel_down="ssap://tv/channelDown")
for _digit in range(10):
    _SAMSUNG_KEYS[f"digit_{_digit}"]=f"KEY_{_digit}"
    _ANDROID_KEYS[f"digit_{_digit}"]=f"KEYCODE_{_digit}"
    _ROKU_KEYS[f"digit_{_digit}"]=f"Lit_{_digit}"
    _LG_BUTTONS[f"digit_{_digit}"]=str(_digit)
SUPPORTED_COMMANDS = {
    "samsung": tuple(key for key in _SAMSUNG_KEYS if key not in {"next", "previous"}),
    "androidtv": tuple(_ANDROID_KEYS),
    "appletv": tuple(["power_on","power_off","volume_up","volume_down",*_APPLE_REMOTE]),
    "roku": tuple(key for key in _ROKU_KEYS if key not in {"play", "pause", "stop", "next", "previous"}),
    "webos": tuple([key for key in _LG_URIS if key not in {"play_pause", "next", "previous"}] + list(_LG_BUTTONS)),
    "vidaa": ("power_toggle", "volume_up", "volume_down", "channel_up", "channel_down", "mute", "up", "down", "left", "right", "select", "home", "back", "menu", "play", "pause", "stop"),
}
for _protocol in SUPPORTED_COMMANDS:
    SUPPORTED_COMMANDS[_protocol] += ("text",)
from .vidaa import SUPPORTED_COMMANDS as _VIDAA_COMMANDS
SUPPORTED_COMMANDS["vidaa"] = _VIDAA_COMMANDS


def validate_text(text):
    try:
        valid = isinstance(text,str) and 1 <= len(text) <= 512 and len(text.encode('utf-8')) <= 2048 and not any(ord(c)<32 or ord(c)==127 for c in text)
    except UnicodeError:
        valid = False
    if not valid:
        raise TVError("Enter 1–512 characters without control characters.")
    return text


def _dependency(name):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise TVError("Direct TV dependencies are missing. Install requirements-tv.txt in the application's virtual environment, or rebuild its Docker image.") from exc


def protocol_for(config):
    protocol = str(config.get("protocol") or config.get("type") or "").lower()
    if protocol == "hisense":
        raise TVError("Choose the actual Hisense operating system: VIDAA, Android/Google TV, or Roku. The brand alone does not identify its remote protocol.")
    if protocol not in PROTOCOLS:
        raise TVError("Choose a supported direct TV protocol.")
    return protocol


def is_direct(config):
    return config.get("connectionMode") == "direct" and config.get("type") in TV_TYPES


def _target(config, default_port):
    from .integrations import validate_url
    raw = str(config.get("host") or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlsplit(raw)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise TVError("Use only the TV host address and optional port.")
        port = int(config.get("apiPort") or config.get("port") or parsed.port or default_port)
        if not 1 <= port <= 65535:
            raise TVError("Invalid TV port.")
        host = f"[{parsed.hostname}]" if ":" in (parsed.hostname or "") else parsed.hostname
        if parsed.username or parsed.password or parsed.scheme not in {"http", "https"}:
            raise TVError("Invalid TV address.")
        checked, address, _ = validate_url(f"{parsed.scheme}://{host}:{port}")
    except (ValueError, OSError) as exc:
        raise TVError("The TV must use a valid private-network address without credentials, paths or redirects.") from exc
    return address, port, checked.scheme


def _port(config, name, default):
    try:
        value = int(config.get(name) or default)
        if not 1 <= value <= 65535:
            raise ValueError
        return value
    except (ValueError, TypeError) as exc:
        raise TVError("Invalid direct TV service port.") from exc


def _http_origin(address, port):
    return f"http://{'['+address+']' if ':' in address else address}:{port}"


def _binding(config, address, protocol):
    # A session cannot be replayed onto a different saved integration or address.
    value = {k: config.get(k) for k in ("id", "host", "port", "protocol", "type", "apiPort", "pairPort", "applePairProtocol", "allowInsecureLocalControl")}
    value.update(address=address, resolvedProtocol=protocol)
    # Keep private client identities out of logs and session metadata while
    # binding a pairing session to the exact encrypted credential profile.
    value["vidaaProfileDigest"] = hashlib.sha256(json.dumps(config.get("vidaaProfile"), sort_keys=True).encode()).hexdigest()
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _vidaa_profile(config):
    from . import vidaa
    try:
        return vidaa.validate_profile(config.get("vidaaProfile"))
    except vidaa.VidaaError as exc:
        raise TVError(str(exc)) from None


async def _vidaa_call(operation, *args):
    from . import vidaa
    try:
        return await asyncio.to_thread(operation, *args)
    except vidaa.VidaaError as exc:
        raise TVError(str(exc)) from None


def _approval(approved):
    if approved is not True:
        raise TVError("Explicit approval is required before pairing or controlling a TV.")


def _local_channel_permission(config, protocol):
    if protocol in {"samsung", "webos", "roku"} and config.get("allowInsecureLocalControl") is not True:
        raise TVError("This vendor's local control channel is unencrypted. Explicitly allow local control for this TV on your trusted LAN before pairing. Dashboard credentials are never sent to the TV.")
    if protocol in {"samsung", "webos", "roku"} and str(config.get("host") or "").lower().startswith("https:"):
        raise TVError("This adapter does not silently downgrade HTTPS or bypass a TV certificate. Use the supported local HTTP/WS endpoint only after explicitly allowing that unencrypted channel.")


def _event_loop():
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None or _LOOP.is_closed():
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, name="direct-tv", daemon=True).start()
        return _LOOP


def _run(coroutine, timeout=20):
    future = asyncio.run_coroutine_threadsafe(coroutine, _event_loop())
    try:
        return future.result(timeout)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise TVError("The TV operation timed out. Check its local address, power state, remote-service setting, and pairing prompt.") from exc
    except TVError:
        raise
    except Exception as exc:
        # Library exception text can contain tokens, PINs or full requests.
        raise TVError("The TV did not accept the operation. Check its supported protocol, pairing state and on-screen permissions.") from exc


async def _close_session(session):
    if session.get("vidaa"):
        from . import vidaa
        await asyncio.to_thread(vidaa.close, session["vidaa"])
    task = session.get("task")
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    remote = session.get("android")
    if remote:
        remote.disconnect()
    pairing = session.get("applePairing")
    if pairing:
        with contextlib.suppress(Exception):
            await pairing.close()
    websocket = session.get("websocket")
    if websocket:
        with contextlib.suppress(Exception):
            await websocket.close()
    temporary = session.get("temporary")
    if temporary:
        temporary.cleanup()


async def _cleanup():
    now = time.time()
    for identity, session in list(_SESSIONS.items()):
        if session["expiresAt"] <= now:
            _SESSIONS.pop(identity, None)
            await _close_session(session)


def cleanup_sessions():
    _run(_cleanup(), 5)


async def _cancel(pairing_id, config=None):
    session = _SESSIONS.get(pairing_id)
    if not session:
        return {"cancelled": False}
    if config is not None and str(config.get("id")) != str(session.get("configId")):
        raise TVError("This pairing session belongs to another integration.")
    _SESSIONS.pop(pairing_id, None)
    await _close_session(session)
    return {"cancelled": True}


def cancel_pairing(pairing_id, config=None):
    return _run(_cancel(str(pairing_id), config), 5)


def _redirect_safe_connect_class(connect, version):
    # websockets 15–17 calls process_redirect() before making another
    # connection. Fail closed if that transport hook or supported API changes.
    if not isinstance(version, str) or not re.match(r"^(?:15|16|17)\.", version) or not isinstance(connect, type) or not callable(getattr(connect, "process_redirect", None)):
        raise TVError("This WebSocket client version cannot guarantee a pinned TV connection. Install the supported requirements-tv.txt versions.")

    class NoRedirectConnect(connect):
        def process_redirect(self, exc):
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if isinstance(status, int) and 300 <= status <= 399:
                return TVError("TV WebSocket redirects are refused. Use the saved private TV endpoint directly.")
            return exc

    return NoRedirectConnect


async def _websocket(address, port, path="/"):
    # These adapters intentionally use vendor-local WS, never CERT_NONE WSS.
    module = _dependency("websockets")
    connect = _redirect_safe_connect_class(_dependency("websockets.asyncio.client").connect, getattr(module, "__version__", None))
    return await connect(f"ws://{'['+address+']' if ':' in address else address}:{port}{path}", open_timeout=6, close_timeout=1, ping_interval=None, max_size=65536, proxy=None)


async def _receive_json(websocket, timeout=8):
    raw = await asyncio.wait_for(websocket.recv(), timeout)
    if not isinstance(raw, (str, bytes)) or len(raw) > 65536:
        raise TVError("The TV returned an oversized response.")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise TVError("The TV returned invalid protocol data.") from exc
    if not isinstance(value, dict):
        raise TVError("The TV returned invalid protocol data.")
    return value


def _samsung_path(token=""):
    params = {"name": base64.b64encode(b"Homebase").decode()}
    if token:
        params["token"] = str(token)
    return "/api/v2/channels/samsung.remote.control?" + urlencode(params)


async def _samsung_ready(websocket, existing_token=""):
    for _ in range(30):
        value = await _receive_json(websocket, 6)
        if value.get("event") == "ms.channel.connect":
            token = value.get("data", {}).get("token") or existing_token
            if token is None or not 1 <= len(str(token)) <= 2048:
                raise TVError("The Samsung TV did not issue a reusable remote token. Its firmware may use a different protocol.")
            return {"token": str(token)}
        if value.get("event") in {"ms.channel.unauthorized", "ms.channel.timeOut"}:
            raise TVError("The Samsung TV rejected or timed out the remote permission prompt.")
    raise TVError("Samsung pairing did not complete.")


_LG_PERMISSIONS = ["CONTROL_AUDIO", "CONTROL_POWER", "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_PLAYBACK", "READ_POWER_STATE"]


async def _lg_register(websocket, client_key=""):
    payload = {"pairingType": "PROMPT", "manifest": {"manifestVersion":1, "appVersion":"1.0", "permissions": _LG_PERMISSIONS}}
    if client_key:
        payload["client-key"] = str(client_key)
    await websocket.send(json.dumps({"id":"homebase-register", "type":"register", "payload":payload}))
    for _ in range(30):
        value = await _receive_json(websocket, 6)
        if value.get("type") == "registered":
            key = value.get("payload", {}).get("client-key") or client_key
            if not isinstance(key, str) or not 1 <= len(key) <= 4096:
                raise TVError("LG registration returned no reusable client key.")
            return {"clientKey": key}
        if value.get("type") == "error":
            raise TVError("LG webOS registration was rejected. Enable its supported local remote setting and accept the TV prompt.")
    raise TVError("LG pairing did not complete.")


def _private_temp_credentials(credentials=None):
    temporary = tempfile.TemporaryDirectory(prefix="homebase-tv-")
    os.chmod(temporary.name, 0o700)
    cert, key = Path(temporary.name)/"client.crt", Path(temporary.name)/"client.key"
    if credentials:
        for path, value in ((cert, credentials.get("certificate")), (key, credentials.get("clientKey"))):
            if not isinstance(value, str) or not 1 <= len(value) <= 65536:
                temporary.cleanup()
                raise TVError("Pair the Android TV first; its client certificate is missing.")
            fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(value)
    return temporary, cert, key


async def _apple_config(address):
    pyatv = _dependency("pyatv")
    configurations = await pyatv.scan(asyncio.get_running_loop(), hosts=[address], timeout=3)
    found = next((value for value in configurations if str(value.address) == address), None)
    if found is None:
        raise TVError("Apple TV did not advertise a supported service at this address. Check AirPlay/remote settings and network isolation.")
    return found


async def _start(config, address, protocol):
    await _cleanup()
    # Retrying one TV must close its previous handshake; otherwise competing
    # sessions can replace the on-screen PIN behind the user's form.
    for identity, previous in list(_SESSIONS.items()):
        if previous.get("configId") == str(config.get("id")):
            _SESSIONS.pop(identity, None)
            await _close_session(previous)
    if len(_SESSIONS) >= MAX_SESSIONS:
        raise TVError("Too many pairing sessions are open. Cancel one or wait three minutes.")
    session = {"id":secrets.token_urlsafe(24), "configId":str(config.get("id")), "binding":_binding(config,address,protocol), "expiresAt":time.time()+PAIR_TTL, "protocol":protocol, "address":address}
    try:
        if protocol == "androidtv":
            android = _dependency("androidtvremote2")
            temporary, cert, key = _private_temp_credentials()
            session.update(temporary=temporary, cert=cert, key=key)
            remote = android.AndroidTVRemote("Homebase", str(cert), str(key), address, api_port=_target(config,6466)[1], pair_port=_port(config,"pairPort",6467), enable_ime=False, enable_voice=False)
            session["android"] = remote
            await remote.async_generate_cert_if_missing()
            for path in (cert,key):
                os.chmod(path,0o600)
            await asyncio.wait_for(remote.async_start_pairing(),10)
            requires_code, message = True, "The Android remote service accepted the pairing request. Enter its six-character code. For a Chromecast, switch the TV to the Chromecast's HDMI input; the code appears on that device's screen. Keep that screen open."
        elif protocol == "vidaa":
            from . import vidaa
            profile = _vidaa_profile(config)
            session["vidaa"] = await _vidaa_call(vidaa.start_pairing, address, _target(config,36669)[1], profile)
            requires_code = True
            message = ("VIDAA confirmed its PIN prompt. Enter the four-digit code displayed on the TV."
                       if session["vidaa"].pin_shown else "VIDAA acknowledged the connection request but did not confirm a PIN display. If a four-digit code is visible, enter it here. If no code appears, check the TV endpoint and its remote-app permissions, then restart pairing. This TV has not been marked paired.")
        elif protocol == "appletv":
            pyatv = _dependency("pyatv")
            constants = _dependency("pyatv.const")
            found = await _apple_config(address)
            preferred = str(config.get("applePairProtocol") or "companion").lower()
            choices = [constants.Protocol.Companion,constants.Protocol.AirPlay] if preferred == "companion" else [constants.Protocol.AirPlay]
            service = next((found.get_service(p) for p in choices if found.get_service(p) and found.get_service(p).pairing in {constants.PairingRequirement.Optional,constants.PairingRequirement.Mandatory}),None)
            if service is None:
                raise TVError("This Apple TV has no enabled supported pairing service. Enable its AirPlay/remote pairing settings.")
            pairing = await pyatv.pair(found, service.protocol, asyncio.get_running_loop())
            session.update(applePairing=pairing,appleConfig=found,appleProtocol=service.protocol.name)
            await asyncio.wait_for(pairing.begin(),10)
            if not pairing.device_provides_pin:
                raise TVError("This legacy Apple TV requires PIN entry on the device and is not supported by this pairing form.")
            requires_code, message = True, f"Enter the PIN shown on Apple TV ({service.protocol.name} pairing)."
        elif protocol == "samsung":
            _,port,_ = _target(config,8001)
            ws = await _websocket(address,port,_samsung_path())
            session.update(websocket=ws,task=asyncio.create_task(_samsung_ready(ws)))
            requires_code, message = False, "Accept the Homebase remote permission prompt on the Samsung TV, then choose Finish pairing."
        elif protocol == "webos":
            _,port,_ = _target(config,3000)
            ws = await _websocket(address,port)
            session.update(websocket=ws,task=asyncio.create_task(_lg_register(ws)))
            requires_code, message = False, "Accept the Homebase connection prompt on the LG webOS TV, then choose Finish pairing."
        elif protocol == "roku":
            requires_code, message = False, "Roku uses local ECP rather than a pairing code. Enable Control by mobile apps on the Roku, then choose Finish pairing to confirm its read-only device response."
        else:
            raise TVError("Unsupported TV pairing protocol.")
        _SESSIONS[session["id"]] = session
        asyncio.get_running_loop().call_later(PAIR_TTL+1,lambda:asyncio.create_task(_cleanup()))
        return {"pairingId":session["id"],"status":"awaiting_code" if requires_code else "awaiting_confirmation","protocol":protocol,"requiresCode":requires_code,"message":message,"expiresAt":session["expiresAt"]}
    except BaseException:
        await _close_session(session)
        raise


def start_pairing(config, *, approved=False):
    _approval(approved)
    protocol = protocol_for(config)
    _local_channel_permission(config,protocol)
    address,_,_ = _target(config,DEFAULT_PORTS[protocol])
    return _run(_start(copy.deepcopy(config),address,protocol),20)


async def _finish(pairing_id, config, code):
    await _cleanup()
    session = _SESSIONS.get(pairing_id)
    if not session:
        raise TVError("Pairing expired or was cancelled. Start pairing again.")
    protocol = protocol_for(config)
    address,_,_ = _target(config,DEFAULT_PORTS[protocol])
    if session["binding"] != _binding(config,address,protocol):
        raise TVError("The integration changed or this pairing belongs to a different TV. Start pairing again.")
    if session.get("finishing"):
        raise TVError("Pairing is already being completed.")
    session["finishing"] = True
    try:
        if protocol == "androidtv":
            if not re.fullmatch(r"[0-9A-Fa-f]{6}",str(code)):
                raise TVError("Enter the six hexadecimal characters shown on the Android/Google TV.")
            await asyncio.wait_for(session["android"].async_finish_pairing(str(code)),10)
            credentials = {"certificate":session["cert"].read_text(),"clientKey":session["key"].read_text()}
        elif protocol == "vidaa":
            from . import vidaa
            credentials = await _vidaa_call(vidaa.finish_pairing, session["vidaa"], str(code))
        elif protocol == "appletv":
            if not re.fullmatch(r"[0-9]{4}",str(code)):
                raise TVError("Enter the four-digit PIN shown on Apple TV.")
            pairing = session["applePairing"]
            pairing.pin(int(code))
            await asyncio.wait_for(pairing.finish(),10)
            if not pairing.has_paired or not pairing.service.credentials:
                raise TVError("Apple TV did not confirm pairing.")
            previous = dict((config.get("tvCredentials") or {}).get("protocolCredentials") or {})
            previous[session["appleProtocol"]] = pairing.service.credentials
            credentials = {"protocolCredentials":previous}
        elif protocol in {"samsung","webos"}:
            try:
                credentials = await asyncio.wait_for(asyncio.shield(session["task"]),8)
            except asyncio.TimeoutError as exc:
                raise TVError("The TV permission prompt is still waiting. Accept it on the TV and choose Finish again.") from exc
        elif protocol == "roku":
            info = await asyncio.to_thread(test_tv,config)
            if info.get("status") != "available":
                raise TVError("Roku device information could not be verified.")
            credentials = {"approvedLocalControl":True}
        else:
            raise TVError("Unsupported TV pairing protocol.")
        if _SESSIONS.get(pairing_id) is not session:
            raise TVError("This pairing was cancelled or replaced. Start pairing again.")
        _SESSIONS.pop(pairing_id,None)
        await _close_session(session)
        return {"status":"paired","protocol":protocol,"message":"TV pairing/authorization completed. Device commands still require individual approval.","credentials":credentials}
    finally:
        session["finishing"] = False


def finish_pairing(pairing_id, config, code="", *, approved=False):
    _approval(approved)
    return _run(_finish(str(pairing_id),copy.deepcopy(config),str(code)),20)


def test_tv(config):
    """Read-only: HTTP device metadata, advertised Apple services, or TCP port."""
    from .integrations import safe_request
    protocol = protocol_for(config)
    address,port,scheme = _target(config,DEFAULT_PORTS[protocol])
    if protocol in {"samsung", "webos", "roku"} and scheme == "https":
        raise TVError("This vendor adapter cannot test an HTTPS-only endpoint without its supported TLS identity. It will not downgrade the saved address to HTTP.")
    if protocol == "samsung":
        data = json.loads(safe_request(_http_origin(address,port)+"/api/v2/",timeout=4,max_bytes=65536))
        if not isinstance(data,dict) or not isinstance(data.get("device"),dict):
            raise TVError("This host did not return Samsung TV metadata.")
        name = str(data["device"].get("name") or data["device"].get("modelName") or "Samsung TV")[:100]
    elif protocol == "roku":
        raw = safe_request(_http_origin(address,port)+"/query/device-info",timeout=4,max_bytes=65536)
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise TVError("Roku returned unsafe XML.")
        root = ET.fromstring(raw)
        if root.tag != "device-info":
            raise TVError("This host did not return Roku device information.")
        name = (root.findtext("friendly-device-name") or root.findtext("model-name") or "Roku")[:100]
    elif protocol == "appletv":
        found = _run(_apple_config(address),6)
        name = str(found.name)[:100]
    elif protocol == "vidaa" and config.get("tvCredentials"):
        from . import vidaa
        try:
            verified = vidaa.verify_connection(address,port,_vidaa_profile(config),config["tvCredentials"])
        except vidaa.VidaaError as exc:
            raise TVError(str(exc)) from None
        return {"status":"available","protocol":protocol,"name":config.get("name") or "Hisense VIDAA TV","paired":True,
                "authenticated":verified["authenticated"],"supportedCommands":list(SUPPORTED_COMMANDS[protocol]),"message":verified["message"]}
    else:
        try:
            with socket.create_connection((address,port),timeout=3):
                pass
        except OSError as exc:
            raise TVError("The TV's local remote service is not reachable. Check its protocol, address and TV settings.") from exc
        name = config.get("name") or protocol
    message = ("The VIDAA TCP service is reachable. This read-only check did not authenticate TLS/MQTT, request a PIN, or verify pairing. An authorized VIDAA client profile is required for pairing."
               if protocol == "vidaa" else "Local TV service responded. This read-only check did not register a remote or send a control command.")
    return {"status":"available","protocol":protocol,"name":name,"paired":bool(config.get("tvCredentials")),"supportedCommands":list(SUPPORTED_COMMANDS[protocol]),"message":message}


async def _apple_command(config,address,command,payload=None):
    pyatv = _dependency("pyatv")
    constants = _dependency("pyatv.const")
    found = await _apple_config(address)
    credentials = (config.get("tvCredentials") or {}).get("protocolCredentials") or {}
    if not credentials:
        raise TVError("Pair Apple TV before sending commands.")
    for protocol_name,value in credentials.items():
        protocol = getattr(constants.Protocol,str(protocol_name),None)
        if protocol and isinstance(value,str):
            found.set_credentials(protocol,value)
    for service in found.services:
        if service.protocol.name not in credentials:
            service.enabled = False
    remote = await pyatv.connect(found,asyncio.get_running_loop())
    try:
        if command == "text":
            await remote.keyboard.text_append((payload or {})["text"])
        elif command in ("power_on","power_off"):
            await (remote.power.turn_on() if command == "power_on" else remote.power.turn_off())
        elif command in ("volume_up","volume_down"):
            await getattr(remote.audio,command)()
        elif command == "mute":
            raise TVError("Apple TV mute is not universally supported. Use volume controls or your TV remote.")
        else:
            await getattr(remote.remote_control,_APPLE_REMOTE[command])()
    finally:
        await asyncio.gather(*remote.close(),return_exceptions=True)


async def _lg_request(websocket,uri,payload=None):
    identity=secrets.token_hex(6)
    await websocket.send(json.dumps({"id":identity,"type":"request","uri":uri,"payload":payload or {}}))
    for _ in range(10):
        value=await _receive_json(websocket,5)
        if value.get("id") == identity:
            if value.get("type") == "error":
                raise TVError("LG webOS rejected this command or permission.")
            return value.get("payload") or {}
    raise TVError("LG webOS did not acknowledge the command.")


async def _command(config,address,port,protocol,command,payload):
    credentials=config.get("tvCredentials") or {}
    if not isinstance(credentials,dict) or not credentials:
        raise TVError("Pair or authorize this TV before sending commands.")
    if protocol == "androidtv":
        android=_dependency("androidtvremote2")
        temporary,cert,key=_private_temp_credentials(credentials)
        remote=android.AndroidTVRemote("Homebase",str(cert),str(key),address,api_port=_target(config,6466)[1],pair_port=_port(config,"pairPort",6467),enable_ime=command=="text",enable_voice=False)
        try:
            await asyncio.wait_for(remote.async_connect(),8)
            if command == "text":
                await asyncio.sleep(.15)
                remote.send_text(payload["text"])
            else:
                remote.send_key_command(_ANDROID_KEYS[command])
            await asyncio.sleep(0.15)
        finally:
            remote.disconnect()
            temporary.cleanup()
    elif protocol == "vidaa":
        from . import vidaa
        await _vidaa_call(vidaa.command,address,port,_vidaa_profile(config),credentials,command, payload.get("text"))
    elif protocol == "appletv":
        await _apple_command(config,address,command,payload)
    elif protocol == "samsung":
        token=credentials.get("token")
        if not token:
            raise TVError("Pair Samsung TV before sending commands.")
        websocket=await _websocket(address,port,_samsung_path(token))
        try:
            await asyncio.wait_for(_samsung_ready(websocket,token),6)
            if command == "text":
                await websocket.send(json.dumps({"method":"ms.channel.emit","params":{"event":"custom.remote.textReceived","to":"broadcast"}}))
                await websocket.send(json.dumps({"method":"ms.remote.control","params":{"Cmd":base64.b64encode(payload["text"].encode()).decode(),"DataOfCmd":"base64","TypeOfRemote":"SendInputString"}}))
            else:
                await websocket.send(json.dumps({"method":"ms.remote.control","params":{"Cmd":"Click","DataOfCmd":_SAMSUNG_KEYS[command],"Option":"false","TypeOfRemote":"SendRemoteKey"}}))
            await asyncio.sleep(0.15)
        finally:
            await websocket.close()
    elif protocol == "webos":
        websocket=await _websocket(address,port)
        try:
            await asyncio.wait_for(_lg_register(websocket,credentials.get("clientKey","")),6)
            if command == "text":
                await _lg_request(websocket,"ssap://com.webos.service.ime/insertText",{"text":payload["text"],"replace":False})
            elif command in _LG_BUTTONS:
                info=await _lg_request(websocket,"ssap://com.webos.service.networkinput/getPointerInputSocket")
                path=str(info.get("socketPath") or "")
                parsed=urlsplit(path)
                if parsed.scheme != "ws" or parsed.hostname != address or parsed.port not in {port,3000,3001} or parsed.query or parsed.fragment:
                    raise TVError("LG returned an unexpected input-socket destination.")
                pointer=await _websocket(address,parsed.port,parsed.path)
                try:
                    await pointer.send("type:button\nname:"+_LG_BUTTONS[command]+"\n\n")
                finally:
                    await pointer.close()
            elif command == "power_on":
                raise TVError("LG power-on from deep sleep requires separately configured Wake-on-LAN and TV support. This adapter does not guess a MAC address.")
            else:
                await _lg_request(websocket,_LG_URIS[command],{"mute":bool(payload.get("muted",True))} if command=="mute" else None)
        finally:
            await websocket.close()
    elif protocol == "roku":
        from .integrations import safe_request
        if credentials.get("approvedLocalControl") is not True:
            raise TVError("Authorize Roku local control first.")
        from urllib.parse import quote
        key="Lit_"+quote(payload["text"],safe="") if command=="text" else _ROKU_KEYS[command]
        await asyncio.to_thread(safe_request,_http_origin(address,port)+"/keypress/"+key,method="POST",data=b"",timeout=4,max_bytes=65536)
    else:
        raise TVError("Unsupported TV control protocol.")
    return {"message":f"Approved {command} command sent using direct {protocol} control.","protocol":protocol}


def execute_tv_command(config,payload,*,approved=False):
    _approval(approved)
    protocol=protocol_for(config)
    _local_channel_permission(config,protocol)
    command=str(payload.get("command") or "")
    if command not in SUPPORTED_COMMANDS[protocol]:
        raise TVError("This TV command is outside the supported remote-control allowlist.")
    if command == "text":
        validate_text(payload.get("text"))
    address,port,_=_target(config,DEFAULT_PORTS[protocol])
    return _run(_command(copy.deepcopy(config),address,port,protocol,command,payload),20)


def _mdns_candidate(service_type,name,properties,address,subnet=None):
    services={"_androidtvremote2._tcp.local.":("androidtv","Google / Android TV"),"_companion-link._tcp.local.":("appletv","Apple TV"),"_mediaremotetv._tcp.local.":("appletv","Apple TV"),"_samsungmsf._tcp.local.":("samsung","Samsung TV")}
    if service_type not in services:
        return None
    try:
        ip=ipaddress.ip_address(address)
        network=ipaddress.ip_network(subnet,strict=False) if subnet else None
        if not isinstance(ip,ipaddress.IPv4Address) or not any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8","172.16.0.0/12","192.168.0.0/16")) or (network and ip not in network):
            return None
        if network and ip in (network.network_address,network.broadcast_address):
            return None
    except ValueError:
        return None
    properties={str(k.decode('utf-8','replace') if isinstance(k,bytes) else k).lower():str(v.decode('utf-8','replace') if isinstance(v,bytes) else v) for k,v in (properties or {}).items()}
    if service_type == "_companion-link._tcp.local." and not any(re.match(r"^appletv(?:[0-9,._ -]|$)",properties.get(key,"").replace(" ",""),re.I) for key in ("rpmd","model","md")):
        return None
    protocol,label=services[service_type]
    display=str(name).removesuffix('.'+service_type).replace('\\032',' ')
    display="".join(c for c in display if ord(c)>=32)[:100]
    default_port={"samsung":8001,"androidtv":6466,"appletv":7000}[protocol]
    return {"id":address,"ip":address,"name":display or label,"type":"tv","vendor":"Apple" if protocol=="appletv" else "Samsung" if protocol=="samsung" else "Google / Android TV","online":True,"lastSeen":__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),"source":"TV mDNS advertisement","tvCandidate":True,"suggestedIntegration":protocol,"suggestedProtocol":protocol,"suggestedHost":_http_origin(address,default_port),"host":_http_origin(address,default_port),"note":"TV remote advertisement detected; explicit pairing and approval are required."}


async def _discover(timeout,subnet,local_address):
    try:
        zc_module=_dependency("zeroconf")
        azc_module=_dependency("zeroconf.asyncio")
    except TVError:
        return []
    services={"_androidtvremote2._tcp.local.":("androidtv","Google / Android TV"),"_companion-link._tcp.local.":("appletv","Apple TV"),"_mediaremotetv._tcp.local.":("appletv","Apple TV"),"_samsungmsf._tcp.local.":("samsung","Samsung TV")}
    results={}
    tasks=set()
    async def inspect(service_type,name):
        info=azc_module.AsyncServiceInfo(service_type,name)
        if not await info.async_request(zc.zeroconf,1000):
            return
        for address in info.parsed_addresses(zc_module.IPVersion.V4Only):
            candidate=_mdns_candidate(service_type,name,info.properties,address,subnet)
            if candidate:
                results[address]=candidate
    def changed(zeroconf,service_type,name,state_change):
        if state_change != zc_module.ServiceStateChange.Removed and len(tasks)<100:
            task=asyncio.create_task(inspect(service_type,name));tasks.add(task);task.add_done_callback(tasks.discard)
    zc=azc_module.AsyncZeroconf(interfaces=[local_address] if local_address else zc_module.InterfaceChoice.Default,ip_version=zc_module.IPVersion.V4Only)
    browser=None
    try:
        browser=azc_module.AsyncServiceBrowser(zc.zeroconf,list(services),handlers=[changed])
        await asyncio.sleep(timeout)
        if tasks:
            await asyncio.wait(tasks,timeout=1)
    finally:
        for task in tasks:
            task.cancel()
        if browser:
            await browser.async_cancel()
        await zc.async_close()
    return list(results.values())


def discover_tvs(timeout=3,subnet=None,local_address=None):
    """Read only mDNS service advertisements; never pair, wake or send keys."""
    try:
        return _run(_discover(max(.1,min(float(timeout),4)),subnet,local_address),7)
    except (TVError,ValueError):
        return []
