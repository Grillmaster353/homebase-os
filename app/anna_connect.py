"""Explicit, loopback-only browser enrollment of the restricted Anna bridge."""
import os
from pathlib import Path
import socketserver
import threading

from . import anna_transport as transport


class AnnaConnection:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.server = None
        self.thread = None
        self.token = None
        self.path = None

    def status(self):
        with self.lock:
            active = bool(self.server and self.token and self.app.sessions.get(self.token))
            return {"connected": active, "commands": bool(active and self.server.client.commands), "cameraImages": bool(active and self.server.client.cameras)}

    def enable(self, user, *, commands=False, cameras=False):
        with self.lock:
            self.close()
            directory = transport.state_dir()
            path = directory / 'session.sock'
            if path.exists() or path.is_symlink():
                raise ValueError('An existing local connector is running. Disconnect it before enabling the browser connector.')
            ca = self.app.vault.directory.parent / 'certs' / 'homebase-rootCA.crt'
            client = transport.HomeBaseClient(f'https://localhost:{self.app.port}', ca, commands, cameras)
            token, session = self.app.sessions.create(user)
            client.csrf = session['csrf']
            # This value never leaves local process memory or local HTTPS.
            client.opener.addheaders = [('Cookie', 'hc_session=' + token)]
            try:
                server = socketserver.ThreadingUnixStreamServer(str(path), transport.Handler)
                os.chmod(path, 0o600)
                server.daemon_threads = True
                server.client = client
                thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .2}, daemon=True)
                thread.start()
            except BaseException:
                self.app.sessions.delete(token)
                raise
            self.server, self.thread, self.token, self.path = server, thread, token, path
            self.app.vault.event('Anna connection enabled', 'Owner approved the local bridge. Commands: '+str(commands)+'. Camera images: '+str(cameras)+'.', 'security')
            return self.status()

    def close(self):
        with self.lock:
            if self.token:
                self.app.sessions.delete(self.token)
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                self.thread.join(2)
                self.path.unlink(missing_ok=True)
            self.server = self.thread = self.token = self.path = None
