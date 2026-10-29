import os
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
import unittest

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from app.admin import configure_account, generate_password
from app.lan_setup import create_certificates
from app.security import Vault, verify_password


class ProvisioningTests(unittest.TestCase):
    def test_password_generator_and_offline_reset_preserve_data_and_mfa(self):
        with TemporaryDirectory() as directory:
            vault = Vault(directory)
            old, new = generate_password(), generate_password()
            self.assertEqual(len(new), 40)
            self.assertNotEqual(old, new)
            configure_account(vault, "admin", old)
            vault.data["account"]["mfaSecret"] = "FIXTURE"
            vault.data["integrations"] = [{"id": "saved"}]
            self.assertFalse(configure_account(vault, "different", new, if_missing=True))
            self.assertTrue(configure_account(vault, "admin", new, reset=True))
            loaded = Vault(directory)
            self.assertTrue(verify_password(new, loaded.data["account"]["password"]))
            self.assertFalse(verify_password(old, loaded.data["account"]["password"]))
            self.assertEqual(loaded.data["account"]["mfaSecret"], "FIXTURE")
            self.assertEqual(loaded.data["integrations"], [{"id": "saved"}])
            self.assertNotIn(new.encode(), loaded.path.read_bytes())

    def test_tls_leaf_identity_chain_permissions_and_ca_reuse(self):
        with TemporaryDirectory() as directory:
            output, ca = Path(directory)/"certs", Path(directory)/"private-ca"
            result = create_certificates(output, ca, "homebase-a1b2c3d4.local", ["192.168.2.8"])
            cert = x509.load_pem_x509_certificate((output/"server.crt").read_bytes())
            root = x509.load_pem_x509_certificate((output/"homebase-rootCA.crt").read_bytes())
            cert.verify_directly_issued_by(root)
            sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertIn("homebase-a1b2c3d4.local", sans.get_values_for_type(x509.DNSName))
            self.assertIn(ExtendedKeyUsageOID.SERVER_AUTH, cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
            self.assertEqual(os.stat(output/"server.key").st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(ca/"rootCA.key").st_mode & 0o777, 0o600)
            self.assertFalse((output/"rootCA.key").exists())
            context = ssl.create_default_context(cafile=str(output/"homebase-rootCA.crt"))
            context.load_cert_chain(output/"server.crt", output/"server.key")
            before = (ca/"rootCA.crt").read_bytes()
            create_certificates(output, ca, result["hostname"], ["192.168.2.9"])
            self.assertEqual(before, (ca/"rootCA.crt").read_bytes())

    def test_tls_refuses_public_addresses_and_ca_in_runtime_mount(self):
        with TemporaryDirectory() as directory:
            output=Path(directory)/"certs"
            for address in ("8.8.8.8", "0.0.0.0", "224.0.0.1", "198.18.0.1"):
                with self.assertRaises(ValueError):
                    create_certificates(output, Path(directory)/"ca", "homebase-a1b2c3d4.local", [address])
            with self.assertRaises(ValueError):
                create_certificates(output, output/"private", "homebase-a1b2c3d4.local", ["192.168.1.2"])


if __name__ == "__main__":
    unittest.main()
