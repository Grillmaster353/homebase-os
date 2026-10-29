import unittest

from app.router_ouis import PREFIX_COUNT, lookup_router_brand, normalize_mac


class RouterOUITests(unittest.TestCase):
    def test_known_registry_examples_are_real_manufacturers_not_isps(self):
        examples = {
            '34:f7:16:00:01:02': ('tplink', 'TP-LINK TECHNOLOGIES CO.,LTD.'),
            '00:26:18:00:01:02': ('asus', 'ASUSTek COMPUTER INC.'),
            '40:5d:82:00:01:02': ('netgear', 'NETGEAR'),
            'f0:9f:c2:00:01:02': ('ubiquiti', 'Ubiquiti Inc'),
            '00:0c:42:00:01:02': ('mikrotik', 'Routerboard.com'),
            '58:1d:d8:00:01:02': ('sagemcom', 'Sagemcom Broadband SAS'),
            '34:2b:70:00:01:02': ('arris', 'Arris'),
            '08:f0:1e:00:01:02': ('eero', 'eero inc.'),
        }
        for mac, (identity, manufacturer) in examples.items():
            with self.subTest(mac=mac):
                result = lookup_router_brand(mac)
                self.assertEqual(result['id'], identity)
                self.assertEqual(result['manufacturer'], manufacturer)
                self.assertNotIn('isp', result)
        self.assertGreater(PREFIX_COUNT, 4000)

    def test_mac_formats_including_unpadded_arp_are_normalized(self):
        for value in ('00:0c:42:01:02:03', '0:c:42:1:2:3', '00-0C-42-01-02-03', '000c.4201.0203', '000C42010203'):
            self.assertEqual(normalize_mac(value), '000C42010203')
            self.assertEqual(lookup_router_brand(value)['id'], 'mikrotik')

    def test_randomized_group_unknown_and_invalid_addresses_stay_unknown(self):
        for value in (None, '', 'not-a-mac', '00:0c:42:01:02:03:04', '02:0c:42:01:02:03', '01:0c:42:01:02:03', '00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff', '04:00:00:00:00:01', '00-0c:42-01-02-03'):
            with self.subTest(value=value):
                self.assertIsNone(lookup_router_brand(value))


if __name__ == '__main__':
    unittest.main()
