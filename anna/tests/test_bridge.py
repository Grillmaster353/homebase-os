import datetime
import importlib.util
import json
import os
from pathlib import Path
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('HOMEBASE_SOURCE', str(ROOT.parent)))
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(ROOT/'bridge'))
import homebase_bridge as b
from app.server import Application, Server
from app.security import password_hash
from app.lan_setup import create_certificates


class OfflineMonitor:
    def overview(self):
        return {'host': {'name': 'fixture-server'}, 'network': {'history': []}, 'devices': []}

    def discover(self):
        return []


class LauncherTests(unittest.TestCase):
    def test_installed_bin_symlink_finds_sidecar_with_foreign_python_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'homebase-anna-bridge'
            link.symlink_to(ROOT / 'dist/homebase-anna-bridge')
            env = {**os.environ, 'PYTHONHOME': '/missing', 'PYTHONPATH': '/missing'}
            frames = ''.join(json.dumps({'jsonrpc': '2.0', 'id': i, 'method': m})+'\n'
                             for i, m in enumerate(['initialize', 'describe', 'health']))
            result = subprocess.run([str(link)], input=frames, capture_output=True,
                                    text=True, env=env, check=True, timeout=5)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(replies[0]['result']['protocolVersion'], '2.0')
            self.assertEqual(replies[0]['result']['capabilities'], {})
            self.assertEqual(replies[1]['result']['version'], b.VERSION)
            self.assertEqual(replies[2]['result']['status'], 'ready')


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.app=Application(self.root/'data', monitor=OfflineMonitor())
        self.app.vault.data['account']={'username':'owner','password':password_hash('fixture account password for tests')}
        self.app.vault.save()
        create_certificates(self.root/'certs',self.root/'ca','fixture.local',['127.0.0.1'])
        self.server=Server(('127.0.0.1',0),self.app)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.root/'certs/server.crt',self.root/'certs/server.key')
        self.server.tls_context=ctx
        self.app.secure=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.client=b.HomeBaseClient(f'https://127.0.0.1:{self.server.server_port}',self.root/'certs/homebase-rootCA.crt')

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        self.temp.cleanup()

    def login(self):
        return self.client.login({'username':'owner','password':'fixture account password for tests'})

    def test_real_homebase_login_telemetry_and_csrf_stay_local(self):
        self.login()
        s=self.client.request({'path':'/api/status'})['data']
        self.assertTrue(s['authenticated'])
        self.assertNotIn('csrfToken',s)
        self.assertEqual(s['user'],'HomeBase')
        self.assertTrue(self.client.csrf)
        self.assertEqual(self.client.request({'path':'/api/overview'})['data']['host']['name'],'fixture-server')
        self.assertFalse(s['bridge']['commands'])

    def test_write_gate_and_existing_csrf_protection(self):
        self.login()
        p={'path':'/api/settings','method':'POST','body':{'workspaceName':'Fixture workspace'}}
        with self.assertRaises(b.BridgeError): self.client.request(p)
        self.client.commands=True
        self.assertEqual(self.client.request(p)['status'],200)
        self.assertEqual(self.app.vault.data['settings']['workspaceName'],'Fixture workspace')
        self.client.csrf='invalid'
        self.assertEqual(self.client.request(p)['status'],403)

    def test_secrets_pairing_and_traversal_are_denied(self):
        self.login();self.client.commands=True
        for p in ['/api/auth/login','/api/auth/mfa/disable','/api/tvs/kitchen/pair/finish','/api/tvs/certificate','/api/../.data/vault.enc','/api/%2e%2e/secret','/api/overview?x=1','https://evil.example/api/status']:
            with self.subTest(path=p),self.assertRaises(b.BridgeError):self.client.request({'path':p,'method':'POST','body':{}})
        with self.assertRaises(b.BridgeError): self.client.request({'path':'/api/settings','method':'POST','body':{'ai':{'apiKey':'private'}}})
        with self.assertRaises(b.BridgeError): self.client.request({'path':'/api/chat','method':'POST','body':{'nested':{'password':'private'}}})

    def test_camera_sharing_requires_explicit_opt_in(self):
        with self.assertRaises(b.BridgeError): self.client.request({'path':'/api/cameras/cam/snapshot'})
        b.check_path('/api/cameras/cam/snapshot','GET',None,cameras=True)

    def test_logout_revokes_existing_session_in_read_only_mode(self):
        self.login()
        self.assertEqual(self.client.request({'path':'/api/auth/logout','method':'POST','body':{}})['status'],200)
        self.assertFalse(self.client.request({'path':'/api/status'})['data']['authenticated'])
        self.assertFalse(self.client.csrf)

    def test_certificate_verification_and_public_url_denial(self):
        for url in ['http://localhost:8420','https://127.0.0.1/other','https://owner:password@127.0.0.1','https://8.8.8.8','https://0.0.0.0']:
            with self.subTest(url=url),self.assertRaises(b.BridgeError): b.private_url(url)
        wrong=b.HomeBaseClient(self.client.origin,self.root/'ca/rootCA.crt')
        # A system CA does not trust this isolated, temporary test certificate.
        wrong.opener=__import__('urllib.request',fromlist=['']).build_opener(__import__('urllib.request',fromlist=['']).ProxyHandler({}),__import__('urllib.request',fromlist=['']).HTTPSHandler(context=ssl.create_default_context()))
        with self.assertRaises(OSError):wrong.request({'path':'/api/status'})

    def test_stdio_lives_for_multiple_frames_and_negotiates_empty_v2_capabilities(self):
        frames=[{'id':1,'method':'initialize'},{'id':2,'method':'describe'},{'id':3,'method':'health'}]
        p=subprocess.run([sys.executable,str(ROOT/'bridge/homebase_bridge.py')],input=''.join(json.dumps({'jsonrpc':'2.0',**x})+'\n' for x in frames),text=True,capture_output=True,check=True)
        replies=[json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(len(replies),3)
        self.assertEqual(replies[0]['result']['protocolVersion'],'2.0')
        self.assertEqual(replies[0]['result']['capabilities'],{})
        self.assertEqual(replies[1]['result']['tools'][0]['name'],'request')
        self.assertEqual(replies[2]['result']['status'],'ready')

    def test_rpc_cannot_inject_local_login_or_other_broker_operations(self):
        req={'jsonrpc':'2.0','id':1,'method':'invoke','params':{'tool':'request','arguments':{'path':'/api/status','op':'login','body':{'username':'owner','password':'private'}}}}
        p=subprocess.run([sys.executable,str(ROOT/'bridge/homebase_bridge.py')],input=json.dumps(req)+'\n',text=True,capture_output=True,check=True)
        reply=json.loads(p.stdout)
        self.assertFalse(reply['result']['success'])
        self.assertNotIn('private',p.stdout)

    def test_unix_socket_relay_against_real_homebase(self):
        previous=b.STATE_DIR
        b.STATE_DIR=self.root/'bridge-state'
        self.login()
        directory=b.state_dir()
        relay=socketserver.ThreadingUnixStreamServer(str(directory/'session.sock'),b.Handler)
        relay.client=self.client;relay.daemon_threads=True
        import os
        os.chmod(directory/'session.sock',0o600)
        t=threading.Thread(target=relay.serve_forever,daemon=True);t.start()
        try:
            result=b.local_call({'path':'/api/overview'})
            self.assertEqual(result['data']['host']['name'],'fixture-server')
            self.assertEqual(directory.stat().st_mode & 0o777,0o700)
            self.assertEqual((directory/'session.sock').stat().st_mode & 0o777,0o600)
            self.assertEqual([p.name for p in directory.iterdir()],['session.sock'])
        finally:
            relay.shutdown();relay.server_close();t.join(2);b.STATE_DIR=previous


if __name__=='__main__':unittest.main()
