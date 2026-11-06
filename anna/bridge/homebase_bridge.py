#!/usr/bin/env python3
"""HomeBase's restricted Anna Executa and owner-only, memory-only login broker.

No account password, MFA secret, HomeBase cookie or CSRF token reaches Anna.
Connect in the local HomeBase browser page; default invocation is JSON-RPC stdio.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import http.cookiejar
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.1.2"
STATE_DIR = Path.home() / ".config" / "homebase-anna"
MAX_FRAME = 3_800_000
READS = {"/status", "/overview", "/system", "/network-identity", "/devices", "/integrations", "/actions", "/settings", "/entities", "/servers", "/ai/models"}
WRITES = {"/discovery", "/network-identity/detect", "/actions", "/chat", "/settings"}
SECRET_KEYS = {"password", "confirm", "totp", "secret", "mfasecret", "apikey", "api_key", "accesstoken", "access_token", "refreshtoken", "refresh_token", "token", "authorization", "csrftoken", "csrf_token", "cookie", "clientkey", "privatekey", "pin", "code", "username"}


class BridgeError(ValueError):
    pass


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k.lower().replace("-", "_") not in SECRET_KEYS}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def contains_secret(value):
    if isinstance(value, dict):
        return any(k.lower().replace("-", "_") in SECRET_KEYS or contains_secret(v) for k, v in value.items())
    return isinstance(value, list) and any(contains_secret(v) for v in value)


def check_path(path, method, body, commands=False, cameras=False):
    if not isinstance(path, str) or not path.startswith("/api/") or len(path) > 250:
        raise BridgeError("Only HomeBase API routes are supported.")
    route = path[4:]
    if any(c in path for c in ("%", "?", "#", "\\")) or ".." in path or "//" in path:
        raise BridgeError("Invalid HomeBase API path.")
    if method == "GET" and route in READS:
        return
    if method == "POST" and route == "/auth/logout" and body == {}:
        return
    if method == "GET" and re.fullmatch(r"/cameras/[A-Za-z0-9_-]+/snapshot", route):
        if not cameras:
            raise BridgeError("Camera image sharing is off. Enable it explicitly in the local connector.")
        return
    if method != "POST" or not commands:
        raise BridgeError("This bridge is read-only. Enable commands explicitly in the local connector.")
    if not isinstance(body, dict) or contains_secret(body):
        raise BridgeError("Configure passwords, credentials and pairing directly in local HomeBase.")
    if route == "/settings" and set(body) - {"workspaceName"}:
        raise BridgeError("Manage connection and security settings directly in local HomeBase.")
    if route in WRITES or re.fullmatch(r"/(?:tvs/[A-Za-z0-9_-]+/command|integrations/[A-Za-z0-9_-]+/test|actions/[A-Za-z0-9_-]+/(?:approve|reject))", route):
        return
    raise BridgeError("This operation is available only in local HomeBase.")


def private_url(value):
    u = urllib.parse.urlsplit(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.path not in ("", "/") or u.query or u.fragment:
        raise BridgeError("Use a private HTTPS origin, such as https://localhost:8420.")
    addresses = {r[4][0] for r in socket.getaddrinfo(u.hostname, u.port or 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not (ipaddress.ip_address(a).is_private or ipaddress.ip_address(a).is_loopback) or ipaddress.ip_address(a).is_unspecified or ipaddress.ip_address(a).is_multicast for a in addresses):
        raise BridgeError("The HomeBase address must resolve only to private network addresses.")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, "", "", ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HomeBaseClient:
    def __init__(self, origin, ca, commands=False, cameras=False):
        self.origin = private_url(origin)
        self.ca = Path(ca).expanduser().resolve(strict=True)
        self.commands, self.cameras = commands, cameras
        self.cookies = http.cookiejar.CookieJar()
        self.csrf = ""
        self.lock = threading.Lock()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(self.ca))))

    def raw(self, path, method="GET", body=None):
        # Verify DNS remains private before each request; HTTPS still checks hostname/CA.
        private_url(self.origin)
        headers = {"Accept": "application/json"}
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-CSRF-Token": self.csrf})
        req = urllib.request.Request(self.origin + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            response = self.opener.open(req, timeout=65)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            content = response.read(2_600_001)
            if len(content) > 2_600_000:
                raise BridgeError("Response exceeds the bridge's size limit.")
            kind = response.headers.get_content_type()
            if kind == "image/jpeg" and response.status == 200:
                data = {"base64": base64.b64encode(content).decode(), "contentType": kind}
            else:
                try:
                    data = json.loads(content)
                except (ValueError, UnicodeError):
                    raise BridgeError("HomeBase returned an unsupported response.") from None
            return response.status, data

    def login(self, body):
        with self.lock:
            code, data = self.raw("/api/auth/login", "POST", body)
            if code != 200:
                raise BridgeError(data.get("message", "Local HomeBase sign-in failed."))
            self.csrf = data["csrfToken"]
            return {"connected": True, "commands": self.commands, "cameraImages": self.cameras}

    def request(self, payload):
        path, method, body = payload.get("path", ""), payload.get("method", "GET"), payload.get("body")
        check_path(path, method, body, self.commands, self.cameras)
        with self.lock:
            code, data = self.raw(path, method, body)
            if path == "/api/auth/logout":
                self.csrf = ""
                self.cookies.clear()
            data = clean(data)
            if path == "/api/status":
                data.update(user="HomeBase", bridge={"connected": bool(data.get("authenticated")), "commands": self.commands, "cameraImages": self.cameras, "url": self.origin})
            return {"status": code, "data": data}


def state_dir():
    p = STATE_DIR
    if p.is_symlink():
        raise BridgeError("Connector state directory cannot be a symlink.")
    p.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = p.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise BridgeError("Connector directory must be owned by you with mode 700.")
    return p


def local_call(payload):
    sockpath = state_dir() / "session.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(70)
        sock.connect(str(sockpath))
        sock.sendall(json.dumps(payload).encode() + b"\n")
        with sock.makefile("rb") as stream:
            line = stream.readline(MAX_FRAME + 1)
    if len(line) > MAX_FRAME:
        raise BridgeError("Connector response exceeds the message limit.")
    result = json.loads(line)
    if result.get("error"):
        raise BridgeError(result["error"])
    return result["result"]


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(75)
        try:
            line = self.rfile.readline(300_001)
            if len(line) > 300_000:
                raise BridgeError("Request exceeds the message limit.")
            payload = json.loads(line)
            op = payload.get("op")
            if op == "login":
                result = self.server.client.login(payload["body"])
            elif op == "disconnect":
                with self.server.client.lock:
                    if self.server.client.csrf:
                        self.server.client.raw("/api/auth/logout", "POST", {})
                    self.server.client.csrf = ""
                    self.server.client.cookies.clear()
                result = {"disconnected": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                result = self.server.client.request(payload)
            reply = {"result": result}
        except (OSError, ValueError, KeyError) as exc:
            reply = {"error": str(exc) if isinstance(exc, BridgeError) else "Local HomeBase is unavailable. Check its service, certificate and local connector."}
        try:
            self.wfile.write(json.dumps(reply).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve(args):
    os.umask(0o077)
    directory = state_dir()
    sockpath = directory / "session.sock"
    if sockpath.exists():
        raise BridgeError("A connector socket already exists. Stop the old connector before starting another.")
    client = HomeBaseClient(args.url, args.ca, args.commands, args.cameras)
    with socketserver.ThreadingUnixStreamServer(str(sockpath), Handler) as server:
        server.daemon_threads = True
        server.client = client
        os.chmod(sockpath, 0o600)
        def stop(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        try:
            server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass
        finally:
            sockpath.unlink(missing_ok=True)


def connect(args):
    origin = private_url(args.url)
    ca = str(Path(args.ca).expanduser().resolve(strict=True))
    print("HomeBase → Anna local connector\nThe selected home status and commands pass through Anna. Passwords and session credentials stay on this Mac.\nThe HTTPS dashboard stays private. Closing this connector or signing out ends access.")
    if input("Enable this connection? Type CONNECT: ") != "CONNECT":
        raise BridgeError("Connection was not enabled.")
    directory = state_dir()
    if (directory / "session.sock").exists():
        local_call({"op": "disconnect"})
        for _ in range(50):
            if not (directory / "session.sock").exists():
                break
            time.sleep(.1)
        else:
            raise BridgeError("Existing connector did not stop; no second connector was started.")
    command = [sys.executable, str(Path(__file__).resolve()), "--serve", "--url", origin, "--ca", ca]
    if args.commands:
        command += ["--commands"]
    if args.cameras:
        command += ["--cameras"]
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(50):
            if (directory / "session.sock").exists():
                break
            if child.poll() is not None:
                raise BridgeError("Connector could not start. Check for an existing connector.")
            time.sleep(.1)
        credentials = {"username": input("Local HomeBase username: "), "password": getpass.getpass("Local HomeBase password: "), "totp": getpass.getpass("Authenticator code (blank if off): ")}
        result = local_call({"op": "login", "body": credentials})
        credentials.clear()
        (directory / "broker.pid").write_text(str(child.pid))
        print("Connected. Return to HomeBase OS in Anna and select Check connection. Session expires with normal HomeBase limits.")
        print("Commands:", result["commands"], "· Camera images:", result["cameraImages"])
    except BaseException:
        child.terminate()
        raise


def manifest():
    return {"name": "tool-grillmaster-homebase-local-bridge-vn4fj8eg", "display_name": "HomeBase Local Bridge", "version": VERSION, "description": "Restricted bridge to an explicitly connected private HomeBase installation. Credentials stay in a local memory-only broker. Pair devices and manage secrets in local HomeBase.", "category": "utilities", "license": "MIT", "credentials": [], "tools": [{"name": "request", "description": "Read HomeBase telemetry or send a user-requested supported operation through the owner's local connector. A local connected Anna Agent and an authenticated connector are required. Credentials and security administration are never accepted.", "timeout": 70, "parameters": [{"name": "path", "type": "string", "required": True, "description": "Supported /api/ path."}, {"name": "method", "description": "GET reads state; POST requests an explicitly permitted operation.", "type": "string", "enum": ["GET", "POST"], "default": "GET", "required": False}, {"name": "body", "description": "Optional operation payload. Never include credentials, passwords or pairing codes.", "type": "object", "required": False}]}]}


def stdio():
    for line in sys.stdin.buffer:
        rid = None
        try:
            if len(line) > 300_000:
                raise BridgeError("Request exceeds the message limit.")
            req = json.loads(line)
            rid = req.get("id")
            method = req.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2.0", "server_info": {"name": "tool-grillmaster-homebase-local-bridge-vn4fj8eg", "version": VERSION}, "capabilities": {}}
            elif method == "describe":
                result = manifest()
            elif method == "health":
                result = {"status": "ready", "message": "Connect separately in the local HomeBase browser page."}
            elif method == "invoke":
                p = req.get("params", {})
                if p.get("tool") != "request":
                    raise BridgeError("Unknown HomeBase bridge method.")
                args = p.get("arguments") or {}
                if not isinstance(args, dict) or set(args) - {"path", "method", "body"}:
                    raise BridgeError("Unsupported bridge arguments.")
                check_path(args.get("path", ""), args.get("method", "GET"), args.get("body"), True, True)
                try:
                    data = local_call(args)
                except (OSError, ValueError):
                    if args.get("path") != "/api/status":
                        raise BridgeError("Connect HomeBase locally first, then check the connection in Anna.") from None
                    data = {"status": 200, "data": {"authenticated": False, "setupRequired": False, "version": "1.1.1", "bridge": {"connected": False, "commands": False, "cameraImages": False}}}
                result = {"success": True, "data": data}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}), flush=True)
                continue
            reply = {"jsonrpc": "2.0", "id": rid, "result": result}
        except (ValueError, TypeError, KeyError) as exc:
            reply = {"jsonrpc": "2.0", "id": rid, "result": {"success": False, "error": str(exc)}}
        print(json.dumps(reply), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--disconnect", action="store_true")
    parser.add_argument("--url", default="https://localhost:8420")
    parser.add_argument("--ca")
    parser.add_argument("--commands", action="store_true", help="Explicitly allow supported user commands and approvals")
    parser.add_argument("--cameras", action="store_true", help="Explicitly allow camera JPEGs to pass through Anna")
    args = parser.parse_args()
    try:
        if args.connect or args.serve:
            if not args.ca:
                raise BridgeError("Supply HomeBase's public CA certificate with --ca.")
            (serve if args.serve else connect)(args)
        elif args.disconnect:
            local_call({"op": "disconnect"})
            print("HomeBase session revoked; the bridge is disconnected.")
        else:
            stdio()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
