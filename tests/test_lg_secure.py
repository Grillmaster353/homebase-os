"""Pinned LG transport and enrollment contracts; no physical TV access."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json
import ssl
import types
import unittest
from unittest.mock import AsyncMock,patch
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from app import direct_tv
from tests.test_direct_tv import FakeWebSocket
from tests import test_tv_api as api_fixtures


class SecureLGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Local LG fixture')])
        now=datetime.now(timezone.utc)
        cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256())
        cls.der=cert.public_bytes(serialization.Encoding.DER)
        cls.pin=hashlib.sha256(cls.der).hexdigest()

    def config(self,**kw):
        return {'type':'webos','protocol':'webos','connectionMode':'direct','host':'https://192.168.1.20:3001','serverFingerprint':self.pin,**kw}

    def test_secure_pairing_requires_pin_not_unencrypted_channel_consent(self):
        direct_tv._local_channel_permission(self.config(),'webos')
        with self.assertRaisesRegex(direct_tv.TVError,'fingerprint'):
            direct_tv._local_channel_permission(self.config(serverFingerprint=''),'webos')
        self.assertEqual(direct_tv._default_port(self.config(host='https://192.168.1.20'),'webos'),3001)
        self.assertEqual(direct_tv._default_port(self.config(host='http://192.168.1.20'),'webos'),3000)

    def test_wrong_pin_fails_before_websocket_or_client_key(self):
        with patch('app.direct_tv._peer_certificate',return_value=self.der),patch('app.direct_tv._dependency') as dependency:
            with self.assertRaisesRegex(direct_tv.TVError,'does not match'):
                direct_tv._run(direct_tv._webos_websocket(self.config(serverFingerprint='0'*64),'192.168.1.20',3001))
        dependency.assert_not_called()

    def test_wss_uses_certificate_required_context_and_verifies_active_peer(self):
        ws=FakeWebSocket([])
        peer=types.SimpleNamespace(getpeercert=lambda **kw:self.der)
        ws.transport=types.SimpleNamespace(get_extra_info=lambda name:peer)
        connect=AsyncMock(return_value=ws)
        module=types.SimpleNamespace(__version__='15.0',connect=object)
        with patch('app.direct_tv._peer_certificate',return_value=self.der),patch('app.direct_tv._dependency',return_value=module),patch('app.direct_tv._redirect_safe_connect_class',return_value=connect):
            result=direct_tv._run(direct_tv._webos_websocket(self.config(),'192.168.1.20',3001))
        self.assertIs(result,ws)
        self.assertEqual(connect.call_args.args[0],'wss://192.168.1.20:3001/')
        context=connect.call_args.kwargs['ssl']
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)
        self.assertEqual(ws.sent,[])

    def test_new_webos_initialization_precedes_registration_and_allows_prompt_time(self):
        ws=FakeWebSocket([{'type':'hello','payload':{}},{'type':'response','payload':{}},{'type':'response','payload':{'pairingType':'PROMPT'}},{'type':'registered','payload':{'client-key':'fixture-lg-key'}}])
        timeouts=[]
        original=direct_tv._receive_json
        async def receive(socket,timeout):
            timeouts.append(timeout)
            return await original(socket,timeout)
        with patch('app.direct_tv._receive_json',side_effect=receive):
            credentials=direct_tv._run(direct_tv._lg_register(ws,modern=True))
        sent=[json.loads(v) for v in ws.sent]
        self.assertEqual([v['type'] for v in sent],['hello','request','register'])
        self.assertEqual(sent[1]['uri'],'ssap://system/getSystemInfo')
        self.assertEqual(credentials,{'clientKey':'fixture-lg-key'})
        self.assertGreater(timeouts[-1],170)
        self.assertFalse(any('sendkey' in json.dumps(v) for v in sent))

    def test_read_only_secure_check_never_registers_or_sends_keys(self):
        ws=FakeWebSocket([])
        with patch('app.direct_tv._webos_websocket',new=AsyncMock(return_value=ws)),patch('app.direct_tv._lg_register') as register:
            result=direct_tv.test_tv(self.config())
        self.assertEqual(result['status'],'available')
        self.assertEqual(ws.sent,[])
        self.assertTrue(ws.closed)
        register.assert_not_called()

    def test_certificate_identity_is_bound_to_pairing_session(self):
        first=self.config(id='fixture')
        self.assertNotEqual(direct_tv._binding(first,'192.168.1.20','webos'),direct_tv._binding({**first,'serverFingerprint':'f'*64},'192.168.1.20','webos'))


class LGCertificateAPITests(unittest.TestCase):
    setUp=api_fixtures.TVAPITests.setUp
    tearDown=api_fixtures.TVAPITests.tearDown
    request=api_fixtures.TVAPITests.request
    save_tv=api_fixtures.TVAPITests.save_tv

    def test_certificate_enrollment_requires_authentication_and_csrf(self):
        cookie=self.cookie
        with patch('app.direct_tv.inspect_webos_certificate') as inspect:
            self.cookie=''
            self.assertEqual(self.request('/api/tvs/certificate',{'host':'https://192.168.1.20:3001'})[0],401)
            self.cookie=cookie
            self.assertEqual(self.request('/api/tvs/certificate',{'host':'https://192.168.1.20:3001'},csrf=False)[0],403)
            inspect.assert_not_called()

    def test_reading_certificate_does_not_save_trust_or_start_pairing(self):
        with patch('app.direct_tv.inspect_webos_certificate',return_value={'fingerprint':'a'*64}) as inspect,patch('app.direct_tv.start_pairing') as pair:
            status,data=self.request('/api/tvs/certificate',{'host':'https://192.168.1.20:3001'})
        self.assertEqual(status,200,data)
        self.assertEqual(data['fingerprint'],'a'*64)
        self.assertEqual(self.app.integrations(),[])
        inspect.assert_called_once()
        pair.assert_not_called()

    def test_changed_pin_invalidates_pairing_and_manual_grant(self):
        tv,body=self.save_tv(type='webos',protocol='webos',host='https://192.168.1.20:3001',serverFingerprint='a'*64,manualControlEnabled=True)
        self.app.vault.data['integrations'][0]['tvCredentials']={'clientKey':'private-lg-fixture'}
        body.pop('manualControlEnabled')
        status,data=self.request('/api/integrations',{**body,'id':tv['id'],'serverFingerprint':'b'*64})
        self.assertEqual(status,200,data)
        self.assertFalse(data['integration']['manualControlEnabled'])
        self.assertNotIn('tvCredentials',self.app.integration(tv['id']))
