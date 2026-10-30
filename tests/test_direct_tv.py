import asyncio
import enum
import json
from pathlib import Path
import types
import unittest
from unittest.mock import AsyncMock, patch

from app import direct_tv
from app.integrations import execute_action, test_integration
from app.monitor import _parse_ssdp_response


class FakeWebSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.closed = False

    async def recv(self):
        if not self.responses:
            await asyncio.sleep(20)
        return json.dumps(self.responses.pop(0))

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True


class FakeAndroidRemote:
    instances = []

    def __init__(self, name, certfile, keyfile, host, **kwargs):
        self.certfile, self.keyfile, self.host = certfile, keyfile, host
        self.options, self.keys, self.started, self.closed = kwargs, [], False, False
        self.__class__.instances.append(self)

    async def async_generate_cert_if_missing(self):
        Path(self.certfile).write_text('FIXTURE-CERTIFICATE')
        Path(self.keyfile).write_text('FIXTURE-PRIVATE-KEY')

    async def async_start_pairing(self):
        self.started = True

    async def async_finish_pairing(self, code):
        if code != 'A1B2C3':
            raise ValueError('Fixture wrong PIN')

    async def async_connect(self):
        return None

    def send_key_command(self, key):
        self.keys.append(key)

    def disconnect(self):
        self.closed = True


class DirectTVTests(unittest.TestCase):
    def setUp(self):
        FakeAndroidRemote.instances.clear()

    def tearDown(self):
        for identity in list(direct_tv._SESSIONS):
            direct_tv.cancel_pairing(identity)

    def config(self, protocol='androidtv', **overrides):
        return {'id':'tv-fixture','type':protocol,'protocol':protocol,'connectionMode':'direct','host':'http://192.168.1.20',**overrides}

    def test_pair_and_control_require_explicit_approval_before_any_network(self):
        with patch('app.integrations.socket.getaddrinfo', side_effect=AssertionError('No network before approval')):
            for call in (
                lambda:direct_tv.start_pairing(self.config()),
                lambda:direct_tv.finish_pairing('fixture',self.config(),'1234'),
                lambda:direct_tv.execute_tv_command(self.config(),{'command':'power_off'}),
            ):
                with self.assertRaisesRegex(direct_tv.TVError,'approval'):
                    call()

    def test_hisense_requires_platform_and_vidaa_uses_separate_adapter(self):
        with self.assertRaisesRegex(direct_tv.TVError, 'actual Hisense'):
            direct_tv.protocol_for(self.config('hisense'))
        self.assertEqual(direct_tv.protocol_for(self.config('vidaa', type='hisense')), 'vidaa')
        self.assertEqual(direct_tv.DEFAULT_PORTS['vidaa'], 36669)
        self.assertNotIn('play_pause', direct_tv.SUPPORTED_COMMANDS['vidaa'])

    def test_retry_closes_previous_pairing_instead_of_competing_for_tv_pin(self):
        module = types.SimpleNamespace(AndroidTVRemote=FakeAndroidRemote)
        with patch('app.direct_tv._dependency',return_value=module):
            first = direct_tv.start_pairing(self.config(),approved=True)
            previous = FakeAndroidRemote.instances[-1]
            second = direct_tv.start_pairing(self.config(),approved=True)
            self.assertTrue(previous.closed)
            self.assertNotIn(first['pairingId'],direct_tv._SESSIONS)
            self.assertIn(second['pairingId'],direct_tv._SESSIONS)

    def test_vidaa_profile_is_bound_without_storing_plaintext_in_session_binding(self):
        first = self.config('vidaa',type='hisense',vidaaProfile={'password':'fixture-one'})
        second = {**first,'vidaaProfile':{'password':'fixture-two'}}
        binding = direct_tv._binding(first,'192.168.1.20','vidaa')
        self.assertNotEqual(binding,direct_tv._binding(second,'192.168.1.20','vidaa'))
        self.assertNotIn('fixture-one',binding)

    def test_paired_vidaa_check_verifies_saved_login_without_starting_pairing(self):
        profile, credentials = {'clientId':'fixture'}, {'paired':True}
        config = self.config('vidaa',type='hisense',tvCredentials=credentials)
        with patch('app.direct_tv._vidaa_profile',return_value=profile), patch('app.vidaa.verify_connection',return_value={'authenticated':True,'message':'Verified without commands'}) as verify, patch('app.vidaa.start_pairing') as start, patch('app.direct_tv.socket.create_connection') as tcp:
            result = direct_tv.test_tv(config)
        self.assertTrue(result['authenticated'])
        self.assertTrue(result['paired'])
        verify.assert_called_once_with('192.168.1.20',36669,profile,credentials)
        start.assert_not_called()
        tcp.assert_not_called()

    def test_local_cleartext_is_explicit_and_https_never_silently_downgrades(self):
        for protocol in ('samsung','webos','roku'):
            with self.subTest(protocol=protocol):
                with self.assertRaisesRegex(direct_tv.TVError,'unencrypted'):
                    direct_tv.start_pairing(self.config(protocol),approved=True)
                with self.assertRaisesRegex(direct_tv.TVError,'fingerprint' if protocol=='webos' else 'downgrade'):
                    direct_tv.start_pairing(self.config(protocol,host='https://192.168.1.20',allowInsecureLocalControl=True),approved=True)
                with patch('app.integrations.safe_request') as http, patch('app.direct_tv.socket.create_connection') as tcp:
                    with self.assertRaisesRegex(direct_tv.TVError,'fingerprint' if protocol=='webos' else 'downgrade'):
                        direct_tv.test_tv(self.config(protocol,host='https://192.168.1.20'))
                    http.assert_not_called()
                    tcp.assert_not_called()

    def test_private_destinations_and_port_validation(self):
        for host in ('http://8.8.8.8','http://user:secret@192.168.1.2','file:///tmp/test','http://192.168.1.20/path','http://192.168.1.20?x=1'):
            with self.subTest(host=host),self.assertRaises(direct_tv.TVError):
                direct_tv._target(self.config(host=host),6466)
        self.assertEqual(direct_tv._target(self.config(apiPort=7777),6466)[1],7777)
        with self.assertRaises(direct_tv.TVError):
            direct_tv._port(self.config(pairPort=-1),'pairPort',6467)

    def test_actual_websocket_client_refuses_every_redirect_without_revealing_token(self):
        try:
            import websockets
            from websockets.asyncio.client import connect
            from websockets.datastructures import Headers
            from websockets.exceptions import InvalidStatus
            from websockets.http11 import Response
        except ImportError:
            self.skipTest('Optional WebSocket TV dependency is not installed')
        safe_connect = direct_tv._redirect_safe_connect_class(connect, websockets.__version__)
        connector = safe_connect('ws://192.168.1.20:8001/remote?token=fixture-private-token', proxy=None)
        for status in (300,301,302,303,307,308):
            exception = InvalidStatus(Response(status, 'Redirect', Headers({'Location':'wss://public.example/remote?token=fixture-private-token'})))
            rejected = connector.process_redirect(exception)
            self.assertIsInstance(rejected, direct_tv.TVError)
            self.assertNotIn('fixture-private-token', str(rejected))
            self.assertNotIn('public.example', str(rejected))
        original = RuntimeError('non-redirect fixture')
        self.assertIs(connector.process_redirect(original), original)

    def test_unsupported_websocket_transport_fails_closed_before_connection(self):
        class MissingRedirectHook:
            pass
        for version in ('14.2','18.0',None):
            with self.assertRaises(direct_tv.TVError):
                direct_tv._redirect_safe_connect_class(MissingRedirectHook, version)
        with self.assertRaises(direct_tv.TVError):
            direct_tv._redirect_safe_connect_class(MissingRedirectHook, '17.3')

    def test_android_pin_flow_binds_target_and_keeps_private_keys_temporary(self):
        fake=types.SimpleNamespace(AndroidTVRemote=FakeAndroidRemote)
        with patch('app.direct_tv._dependency',return_value=fake):
            started=direct_tv.start_pairing(self.config(apiPort=7777),approved=True)
            self.assertTrue(started['requiresCode'])
            self.assertNotIn('credentials',started)
            remote=FakeAndroidRemote.instances[-1]
            self.assertEqual(remote.options['api_port'],7777)
            self.assertFalse(remote.keys)
            self.assertEqual(Path(remote.keyfile).stat().st_mode & 0o777,0o600)
            with self.assertRaisesRegex(direct_tv.TVError,'different TV'):
                direct_tv.finish_pairing(started['pairingId'],self.config(id='another-tv',apiPort=7777),'A1B2C3',approved=True)
            finished=direct_tv.finish_pairing(started['pairingId'],self.config(apiPort=7777),'A1B2C3',approved=True)
            self.assertEqual(finished['status'],'paired')
            self.assertEqual(finished['credentials']['clientKey'],'FIXTURE-PRIVATE-KEY')
            self.assertFalse(Path(remote.keyfile).exists())
            with self.assertRaisesRegex(direct_tv.TVError,'expired'):
                direct_tv.finish_pairing(started['pairingId'],self.config(apiPort=7777),'A1B2C3',approved=True)

    def test_android_control_sends_one_allowed_key_and_closes(self):
        fake=types.SimpleNamespace(AndroidTVRemote=FakeAndroidRemote)
        config=self.config(tvCredentials={'certificate':'FIXTURE-CERT','clientKey':'FIXTURE-KEY'})
        with patch('app.direct_tv._dependency',return_value=fake):
            result=direct_tv.execute_tv_command(config,{'command':'volume_up'},approved=True)
        remote=FakeAndroidRemote.instances[-1]
        self.assertEqual(remote.keys,['VOLUME_UP'])
        self.assertTrue(remote.closed)
        self.assertFalse(Path(remote.keyfile).exists())
        self.assertEqual(result['protocol'],'androidtv')

    def test_samsung_pairing_does_not_send_a_key_and_retains_existing_tokens(self):
        ws=FakeWebSocket([{'event':'ms.channel.connect','data':{'token':'fixture-token'}}])
        config=self.config('samsung',allowInsecureLocalControl=True)
        with patch('app.direct_tv._websocket',new=AsyncMock(return_value=ws)):
            start=direct_tv.start_pairing(config,approved=True)
            finish=direct_tv.finish_pairing(start['pairingId'],config,approved=True)
        self.assertEqual(ws.sent,[])
        self.assertTrue(ws.closed)
        self.assertEqual(finish['credentials'],{'token':'fixture-token'})
        ws=FakeWebSocket([{'event':'ms.channel.connect','data':{}}])
        result=direct_tv._run(direct_tv._samsung_ready(ws,'existing-token'))
        self.assertEqual(result,{'token':'existing-token'})
        with self.assertRaises(direct_tv.TVError):
            direct_tv._run(direct_tv._samsung_ready(FakeWebSocket([{'event':'ms.channel.connect','data':{}}])))

    def test_roku_read_only_test_never_registers_or_sends_a_command(self):
        xml=b'<device-info><friendly-device-name>Study Roku</friendly-device-name></device-info>'
        with patch('app.integrations.safe_request',return_value=xml) as request:
            result=direct_tv.test_tv(self.config('roku'))
        self.assertEqual(result['status'],'available')
        self.assertFalse(result['paired'])
        self.assertEqual(request.call_args.args[0],'http://192.168.1.20:8060/query/device-info')
        self.assertNotIn('method',request.call_args.kwargs)

    def test_apple_pin_credentials_and_command_use_the_supported_public_api(self):
        class Protocol(enum.Enum):
            Companion=1
            AirPlay=2
        class Requirement(enum.Enum):
            Optional=1
            Mandatory=2
        service=types.SimpleNamespace(protocol=Protocol.Companion,pairing=Requirement.Mandatory,credentials=None,enabled=True)
        other=types.SimpleNamespace(protocol=Protocol.AirPlay,pairing=Requirement.Optional,credentials=None,enabled=True)
        stored=[]
        config_object=types.SimpleNamespace(services=[service,other],get_service=lambda p:service if p==Protocol.Companion else other,set_credentials=lambda p,v:stored.append((p,v)))
        class Pairing:
            device_provides_pin=True
            has_paired=False
            def __init__(self): self.service=service;self.received_pin=None;self.closed=False
            async def begin(self): pass
            def pin(self,value): self.received_pin=value
            async def finish(self): self.has_paired=True;self.service.credentials='fixture-apple-credentials'
            async def close(self): self.closed=True
        pairing=Pairing()
        remote=types.SimpleNamespace(remote_control=types.SimpleNamespace(up=AsyncMock()),close=lambda:[])
        pyatv=types.SimpleNamespace(pair=AsyncMock(return_value=pairing),connect=AsyncMock(return_value=remote))
        constants=types.SimpleNamespace(Protocol=Protocol,PairingRequirement=Requirement)
        with patch('app.direct_tv._dependency',side_effect=lambda name:constants if name=='pyatv.const' else pyatv),patch('app.direct_tv._apple_config',new=AsyncMock(return_value=config_object)):
            start=direct_tv.start_pairing(self.config('appletv'),approved=True)
            finish=direct_tv.finish_pairing(start['pairingId'],self.config('appletv'),'1234',approved=True)
            self.assertEqual(pairing.received_pin,1234)
            self.assertTrue(pairing.closed)
            self.assertEqual(finish['credentials'],{'protocolCredentials':{'Companion':'fixture-apple-credentials'}})
            direct_tv.execute_tv_command(self.config('appletv',tvCredentials=finish['credentials']),{'command':'up'},approved=True)
        self.assertEqual(stored,[(Protocol.Companion,'fixture-apple-credentials')])
        self.assertFalse(other.enabled)
        remote.remote_control.up.assert_awaited_once()

    def test_lg_registration_requires_explicit_prompt_and_approved_command(self):
        config=self.config('webos',allowInsecureLocalControl=True)
        pairing_ws=FakeWebSocket([{'type':'registered','payload':{'client-key':'fixture-lg-key'}}])
        with patch('app.direct_tv._websocket',new=AsyncMock(return_value=pairing_ws)):
            start=direct_tv.start_pairing(config,approved=True)
            finish=direct_tv.finish_pairing(start['pairingId'],config,approved=True)
        self.assertEqual(finish['credentials'],{'clientKey':'fixture-lg-key'})
        register=json.loads(pairing_ws.sent[0])
        self.assertEqual(register['type'],'register')
        self.assertEqual(register['payload']['pairingType'],'PROMPT')
        self.assertTrue(pairing_ws.closed)
        command_ws=FakeWebSocket([{'type':'registered','payload':{}}])
        async def send_with_ack(payload):
            command_ws.sent.append(payload)
            data=json.loads(payload)
            if data['type']=='request':
                command_ws.responses.append({'id':data['id'],'type':'response','payload':{'returnValue':True}})
        command_ws.send=send_with_ack
        with patch('app.direct_tv._websocket',new=AsyncMock(return_value=command_ws)):
            direct_tv.execute_tv_command({**config,'tvCredentials':finish['credentials']},{'command':'volume_up'},approved=True)
        requests=[json.loads(p) for p in command_ws.sent if json.loads(p)['type']=='request']
        self.assertEqual([p['uri'] for p in requests],['ssap://audio/volumeUp'])
        self.assertTrue(command_ws.closed)

    def test_unsupported_commands_fail_before_network_and_legacy_ha_stays_ha(self):
        with patch('app.integrations.socket.getaddrinfo',side_effect=AssertionError('No network')):
            for protocol,command in [('webos','power_on'),('webos','play_pause'),('roku','play'),('appletv','mute'),('androidtv','factory_reset')]:
                config=self.config(protocol,allowInsecureLocalControl=True)
                with self.assertRaisesRegex(direct_tv.TVError,'allowlist'):
                    direct_tv.execute_tv_command(config,{'command':command},approved=True)
        self.assertFalse(direct_tv.is_direct({'type':'samsung','entityId':'media_player.tv'}))

    def test_direct_routing_preserves_the_one_time_action_approval_boundary(self):
        config=self.config(tvCredentials={'clientKey':'fixture'})
        action={'type':'tv.command','target':'tv-fixture','payload':{'command':'volume_up'},'status':'executing'}
        with patch('app.direct_tv.execute_tv_command',return_value={'message':'sent'}) as command:
            execute_action(action,[config],'/unused')
        command.assert_called_once_with(config,action['payload'],approved=True)
        with patch('app.direct_tv.test_tv',return_value={'status':'available'}) as test:
            self.assertEqual(test_integration(config),{'status':'available'})
            test.assert_called_once_with(config)

    def test_ssdp_tv_suggestions_preserve_protocol_and_private_host(self):
        for hint,protocol,port in [('roku:ecp','roku',8060),('Samsung SmartTV','samsung',8001),('webOS TV','webos',3000)]:
            found=_parse_ssdp_response(('HTTP/1.1 200 OK\r\nST: '+hint+'\r\n\r\n').encode(),'192.168.1.20','192.168.1.0/24')
            self.assertTrue(found['tvCandidate'])
            self.assertEqual(found['suggestedProtocol'],protocol)
            self.assertEqual(found['suggestedHost'],f'http://192.168.1.20:{port}')

    def test_companion_devices_are_not_automatically_mislabelled_as_apple_tv(self):
        service='_companion-link._tcp.local.'
        for model in ('iPhone16,1','MacBookPro18,3','NotAppleTV',None):
            properties={b'rpMd':model.encode()} if model else {}
            self.assertIsNone(direct_tv._mdns_candidate(service,'My device.'+service,properties,'192.168.1.20','192.168.1.0/24'))
        tv=direct_tv._mdns_candidate(service,'Lounge.'+service,{b'rpMd':b'AppleTV14,1'},'192.168.1.20','192.168.1.0/24')
        self.assertEqual(tv['suggestedProtocol'],'appletv')
        for address in ('8.8.8.8','198.18.0.1','127.0.0.1','192.168.2.20'):
            self.assertIsNone(direct_tv._mdns_candidate(service,'Lounge.'+service,{b'rpMd':b'AppleTV14,1'},address,'192.168.1.0/24'))


if __name__ == '__main__':
    unittest.main()
