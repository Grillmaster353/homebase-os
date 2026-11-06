"""Real TLS / normal authentication / owner-only Anna enrollment checks."""
import json
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import http.cookiejar

from app.server import Application, Server
from app.security import password_hash
from app.lan_setup import create_certificates
from app import anna_transport


class OfflineMonitor:
    def overview(self):
        return {'host': {'name': 'fixture-home'}, 'network': {'history': []}, 'devices': []}


class AnnaEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.state=patch.object(anna_transport,'STATE_DIR',self.root/'connector');self.state.start()
        create_certificates(self.root/'certs',self.root/'ca','fixture.local',['127.0.0.1'])
        self.app=Application(self.root/'data',monitor=OfflineMonitor())
        self.app.vault.data['account']={'username':'owner','password':password_hash('a temporary test password only')};self.app.vault.save()
        self.server=Server(('127.0.0.1',0),self.app)
        self.server.tls_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server.tls_context.load_cert_chain(self.root/'certs/server.crt',self.root/'certs/server.key');self.app.secure=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'https://localhost:{self.server.server_port}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(self.root/'certs/homebase-rootCA.crt'))),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=''

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.state.stop();self.temp.cleanup()

    def request(self,path,body=None,headers=None):
        h={'Content-Type':'application/json','X-CSRF-Token':self.csrf};h.update(headers or {})
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:r=self.opener.open(req,timeout=8)
        except urllib.error.HTTPError as e:r=e
        with r:return r.status,json.load(r)

    def login(self):
        c,d=self.request('/api/auth/login',{'username':'owner','password':'a temporary test password only'});self.assertEqual(c,200);self.csrf=d['csrfToken']

    def test_enrollment_requires_existing_login_csrf_https_and_local_origin(self):
        self.assertEqual(self.request('/api/anna/connect',{})[0],401)
        self.login()
        self.assertEqual(self.request('/api/anna/connect',{}, {'X-CSRF-Token':'wrong'})[0],403)
        self.assertEqual(self.request('/api/anna/connect',{}, {'Origin':'https://anna.partners'})[0],403)
        self.app.secure=False
        self.assertEqual(self.request('/api/anna/connect',{})[0],403)

    def test_browser_enrollment_real_telemetry_and_revocation(self):
        self.login()
        c,d=self.request('/api/anna/connect',{'commands':True,'cameraImages':False})
        self.assertEqual(c,200,d);self.assertEqual(d,{'connected':True,'commands':True,'cameraImages':False})
        self.assertEqual(set(d),{'connected','commands','cameraImages'})
        result=anna_transport.local_call({'path':'/api/overview'})
        self.assertEqual(result['data']['host']['name'],'fixture-home')
        with self.assertRaises(anna_transport.BridgeError):anna_transport.local_call({'path':'/api/cameras/cam/snapshot'})
        self.assertEqual(self.request('/api/anna/disconnect',{})[0],200)
        self.assertFalse((self.root/'connector/session.sock').exists())
        self.assertTrue(self.request('/api/status')[1]['authenticated'])

    def test_grants_default_off_and_reject_credential_fields(self):
        self.login()
        self.assertEqual(self.request('/api/anna/connect',{'commands':1})[0],400)
        self.assertEqual(self.request('/api/anna/connect',{'password':'not accepted'})[0],400)
        c,d=self.request('/api/anna/connect',{})
        self.assertEqual(c,200,d);self.assertFalse(d['commands']);self.assertFalse(d['cameraImages'])
        with self.assertRaises(anna_transport.BridgeError):anna_transport.local_call({'path':'/api/settings','method':'POST','body':{'workspaceName':'change'}})

if __name__=='__main__':unittest.main()
