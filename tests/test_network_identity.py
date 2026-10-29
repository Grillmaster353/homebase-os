import copy
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.network_identity import CACHE_SECONDS, DEFAULT_SETTINGS, LOOKUP_URL, NetworkIdentity, canonical_name, router_identity, validate_settings


class NetworkIdentityTests(unittest.TestCase):
    def test_telstra_and_bigpond_reported_names_share_the_bundled_logo_id(self):
        for name in ('Telstra', 'Telstra Internet Pty Ltd', 'Telstra Corporation Limited', 'Telstra Limited', 'BigPond', 'Bigpond Internet'):
            with self.subTest(name=name):
                self.assertEqual(canonical_name(name), {'name': 'Telstra', 'id': 'telstra'})

    def setUp(self):
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.overview = {"network": {"interface": "en0", "localAddress": "192.168.1.5", "gateway": "192.168.1.1"}, "devices": [{"ip": "192.168.1.1", "mac": "00:11:22:33:44:55", "lastSeen": None}]}
        self.identity = NetworkIdentity(lambda: copy.deepcopy(self.settings), lambda: copy.deepcopy(self.overview))

    def test_reads_and_defaults_never_call_external_lookup(self):
        with patch("app.network_identity.safe_request") as request:
            self.assertFalse(self.identity.snapshot()["externalLookupEnabled"])
            self.identity.snapshot()
            request.assert_not_called()
            with self.assertRaises(PermissionError):
                self.identity.detect()
            request.assert_not_called()

    def test_explicit_lookup_uses_only_fixed_https_endpoint_and_caches(self):
        response = {"success": True, "connection": {"isp": "Telstra Internet Pty Ltd"}, "ip": "203.0.113.8", "city": "SHOULD NOT RETAIN"}
        with patch("app.network_identity.safe_request", return_value=json.dumps(response).encode()) as request:
            first = self.identity.detect(allow_external=True)
            second = self.identity.detect(allow_external=True)
            self.assertEqual(first["isp"]["id"], "telstra")
            self.assertEqual(first["isp"]["reportedName"], "Telstra Internet Pty Ltd")
            self.assertEqual(second["isp"]["detectedAt"], first["isp"]["detectedAt"])
            request.assert_called_once_with(LOOKUP_URL, timeout=5, allow_remote=True, max_bytes=32768, headers={"Accept": "application/json"})
            self.assertNotIn("203.0.113.8", json.dumps(first))
            self.assertNotIn("SHOULD NOT RETAIN", json.dumps(first))
            self.assertFalse(first["externalLookupEnabled"], "A one-shot approval must not persist background consent")

    def test_persistent_consent_unknown_provider_and_expiry(self):
        self.settings["allowExternalLookup"] = True
        with patch("app.network_identity.safe_request", return_value=b'{"success":true,"connection":{"isp":"Example Regional Internet Ltd"}}') as request:
            result = self.identity.detect()
            self.assertEqual(result["isp"]["name"], "Example Regional Internet Ltd")
            self.assertEqual(result["isp"]["id"], "example-regional-internet-ltd")
            self.assertTrue(result["externalLookupEnabled"])
            self.identity._isp["detectedAt"] -= CACHE_SECONDS + 1
            self.identity._last_attempt -= CACHE_SECONDS + 1
            self.identity.detect()
            self.assertEqual(request.call_count, 2)

    def test_lookup_errors_are_safe_and_back_off(self):
        with patch("app.network_identity.safe_request", side_effect=RuntimeError("do not expose this response")) as request:
            result = self.identity.detect(allow_external=True)
            self.assertEqual(result["isp"]["status"], "unavailable")
            self.assertNotIn("do not expose", json.dumps(result))
            self.identity.detect(allow_external=True)
            request.assert_called_once()

    def test_network_change_invalidates_cached_isp(self):
        with patch("app.network_identity.safe_request", return_value=b'{"success":true,"connection":{"isp":"Optus"}}'):
            self.identity.detect(allow_external=True)
        self.overview["network"].update(localAddress="10.0.0.5", gateway="10.0.0.1")
        self.assertIsNone(self.identity.snapshot()["isp"]["name"])

    def test_explicit_detection_returns_the_lookup_result_instead_of_manual_choice(self):
        self.settings['ispName']='Exetel'
        with patch('app.network_identity.safe_request', return_value=b'{"success":true,"connection":{"isp":"SingTel Optus Pty Ltd"}}'):
            result=self.identity.detect(allow_external=True,prefer_detected=True)
        self.assertEqual(result['isp']['id'],'optus')
        self.assertEqual(result['isp']['source'],'ipwho.is')
        self.assertEqual(self.identity.snapshot()['isp']['id'],'exetel')
        self.assertEqual(self.identity.snapshot()['detectedIsp']['id'],'optus')

    def test_user_requested_refresh_bypasses_the_old_success_cache(self):
        with patch('app.network_identity.safe_request',side_effect=[b'{"success":true,"connection":{"isp":"Telstra"}}',b'{"success":true,"connection":{"isp":"Optus"}}']) as lookup:
            self.assertEqual(self.identity.detect(allow_external=True)['isp']['id'],'telstra')
            self.assertEqual(self.identity.detect(allow_external=True)['isp']['id'],'telstra')
            self.assertEqual(self.identity.detect(allow_external=True,force_refresh=True)['isp']['id'],'optus')
            self.assertEqual(lookup.call_count,2)

    def test_manual_overrides_support_all_names_without_network_calls(self):
        self.settings.update(ispName="Exetel", routerName="My Own Router Brand")
        with patch("app.network_identity.safe_request") as request:
            result = self.identity.snapshot()
            self.assertEqual(result["isp"]["id"], "exetel")
            self.assertEqual(result["isp"]["source"], "manual")
            self.assertEqual(result["router"]["name"], "My Own Router Brand")
            request.assert_not_called()
        self.assertEqual(canonical_name("SingTel Optus Pty Ltd")["id"], "optus")
        self.assertEqual(canonical_name("SingTel Singapore")["id"], "singtel-singapore")
        self.assertEqual(canonical_name("AVM Fritz!Box", "router")["id"], "fritzbox")

    def test_router_uses_cached_gateway_manufacturer_without_isp_inference(self):
        lookup = Mock(return_value={"id": "sagemcom", "name": "Sagemcom", "manufacturer": "Sagemcom Broadband SAS", "source": "IEEE MA-L", "prefix": "001122"})
        with patch.dict(sys.modules, {"app.router_ouis": SimpleNamespace(lookup_router_brand=lookup)}):
            result = self.identity.snapshot()
            self.assertEqual(result["router"]["id"], "sagemcom")
            self.assertEqual(result["router"]["status"], "inferred")
            self.assertEqual(result["router"]["ip"], "192.168.1.1")
            self.assertIsNone(result["isp"]["name"])
            lookup.assert_called_with("00:11:22:33:44:55")

    def test_validate_settings_requires_boolean_consent(self):
        with self.assertRaises(ValueError):
            validate_settings({"allowExternalLookup": "true"})
        with self.assertRaises(ValueError):
            validate_settings({"ispName": "x" * 161})
        self.assertEqual(validate_settings({"ispName": "  Any ISP  ", "allowExternalLookup": False}), {"ispName": "Any ISP", "allowExternalLookup": False})

    def test_oversized_or_malformed_responses_never_become_isp_names(self):
        for raw in (b"not json", b"[]", b'{"success":false,"message":"private error"}', b"x" * 32769):
            identity = NetworkIdentity(lambda: self.settings, lambda: self.overview)
            with patch("app.network_identity.safe_request", return_value=raw):
                result = identity.detect(allow_external=True)
                self.assertIsNone(result["isp"]["name"])
                self.assertEqual(result["isp"]["status"], "unavailable")



class MinuteISPRefreshTests(unittest.TestCase):
    def setUp(self):
        self.settings = {**DEFAULT_SETTINGS, 'allowExternalLookup': True}
        self.identity = NetworkIdentity(lambda: self.settings.copy(), lambda: {'network': {'interface':'eth0','localAddress':'192.168.1.5','gateway':'192.168.1.1'},'devices':[]})

    def test_checks_each_minute_and_changes_provider_without_repeating_unchanged_lookup(self):
        from app.network_identity import CHANGE_CHECK_URL
        public_ip, provider = ['8.8.8.8'], ['Telstra']
        requests = []
        def fixture(url, **kwargs):
            requests.append(url)
            self.assertTrue(kwargs['allow_remote'])
            self.assertNotIn('data', kwargs)
            return json.dumps({'ip': public_ip[0]} if url == CHANGE_CHECK_URL else {'success':True,'connection':{'isp':provider[0]}}).encode()
        with patch('app.network_identity.time.time', return_value=1000) as clock, patch('app.network_identity.safe_request',side_effect=fixture):
            first=self.identity.refresh_automatic()
            self.assertEqual(first['refreshSeconds'],60)
            self.assertEqual(first['isp']['id'],'telstra')
            clock.return_value=1059
            self.identity.refresh_automatic()
            self.assertEqual(len(requests),2)
            clock.return_value=1060
            unchanged=self.identity.refresh_automatic()
            self.assertEqual(unchanged['lastCheckedAt'],1060)
            self.assertEqual(requests.count(LOOKUP_URL),1)
            self.assertEqual(requests.count(CHANGE_CHECK_URL),2)
            public_ip[0],provider[0]='1.1.1.1','Optus'
            clock.return_value=1120
            changed=self.identity.refresh_automatic()
            self.assertEqual(changed['isp']['id'],'optus')
            self.assertEqual(requests.count(LOOKUP_URL),2)
            self.assertNotIn('1.1.1.1',json.dumps(changed))
            self.assertNotIn('8.8.8.8',json.dumps(changed))

    def test_disabled_or_manual_mode_never_makes_background_requests(self):
        with patch('app.network_identity.safe_request') as request:
            self.settings['allowExternalLookup']=False
            self.identity.refresh_automatic()
            self.settings.update(allowExternalLookup=True,ispName='My provider')
            self.identity.refresh_automatic()
            request.assert_not_called()

    def test_invalid_public_ip_and_failures_are_safe_and_retry_after_one_minute(self):
        with patch('app.network_identity.time.time',return_value=1000) as clock, patch('app.network_identity.safe_request',return_value=b'{"ip":"192.168.1.1"}') as request:
            self.assertEqual(self.identity.refresh_automatic()['isp']['status'],'unavailable')
            clock.return_value=1059
            self.identity.refresh_automatic()
            self.assertEqual(request.call_count,1)
            clock.return_value=1060
            request.return_value=b'{"ip":"http://attacker.example/"}'
            self.identity.refresh_automatic()
            self.assertEqual(request.call_count,2)
            self.assertNotIn('attacker',json.dumps(self.identity.snapshot()))

    def test_periodic_metadata_refresh_even_when_public_ip_is_unchanged(self):
        from app.network_identity import CHANGE_CHECK_URL, PROVIDER_CACHE_SECONDS
        def fixture(url,**_):
            return b'{"ip":"8.8.8.8"}' if url==CHANGE_CHECK_URL else b'{"success":true,"connection":{"isp":"Telstra"}}'
        with patch('app.network_identity.time.time',return_value=1000) as clock, patch('app.network_identity.safe_request',side_effect=fixture) as request:
            self.identity.refresh_automatic()
            clock.return_value=1000+PROVIDER_CACHE_SECONDS
            self.identity.refresh_automatic()
            self.assertEqual(sum(c.args[0]==LOOKUP_URL for c in request.call_args_list),2)

    def test_worker_schedules_checks_at_sixty_second_intervals(self):
        from app.network_identity import CHANGE_CHECK_URL
        now=[1000]
        waits=[]
        def wait(seconds):
            waits.append(seconds)
            now[0]+=seconds
            if len(waits)==3:
                self.identity._stop.set()
        def fixture(url,**_):
            return b'{"ip":"8.8.8.8"}' if url==CHANGE_CHECK_URL else b'{"success":true,"connection":{"isp":"Telstra"}}'
        with patch('app.network_identity.time.time',side_effect=lambda:now[0]),patch('app.network_identity.safe_request',side_effect=fixture) as request,patch.object(self.identity._wake,'wait',side_effect=wait):
            self.identity._run()
        self.assertEqual(waits,[60,60,60])
        self.assertEqual(sum(c.args[0]==CHANGE_CHECK_URL for c in request.call_args_list),3)
        self.assertEqual(sum(c.args[0]==LOOKUP_URL for c in request.call_args_list),1)


if __name__ == "__main__":
    unittest.main()
