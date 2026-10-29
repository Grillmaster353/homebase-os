"""Generate application-local TLS material without installing system trust.

The private CA stays outside the running service's certificate mount. Only the
public root certificate should be copied to devices that will trust this app.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .security import atomic_write


def _private_ip(value: str):
    address = ipaddress.ip_address(value)
    allowed = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "fc00::/7", "fe80::/10", "::1/128")]
    if not any(address in network for network in allowed if address.version == network.version):
        raise ValueError("TLS setup accepts only local/private network addresses.")
    return address


def _key_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def create_certificates(output_dir, ca_dir, hostname: str, addresses: list[str], *, uid=None, gid=None):
    if not hostname.endswith(".local") or len(hostname) > 200 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in hostname):
        raise ValueError("Use the application's generated .local hostname.")
    ips = list(dict.fromkeys([_private_ip(value) for value in addresses] + [ipaddress.ip_address("127.0.0.1")]))
    output, ca_folder = Path(output_dir).resolve(), Path(ca_dir).resolve()
    if output == ca_folder or ca_folder.is_relative_to(output):
        raise ValueError("The private CA directory must be outside the runtime certificate directory.")
    for directory in (output, ca_folder):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    ca_key_path, ca_cert_path = ca_folder / "rootCA.key", ca_folder / "rootCA.crt"
    now = dt.datetime.now(dt.timezone.utc)
    if ca_key_path.exists() != ca_cert_path.exists():
        raise ValueError("The local CA is incomplete. Restore its matching certificate and private key.")
    if ca_key_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
        if ca_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != ca_cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
            raise ValueError("The local CA key and certificate do not match.")
        if ca_cert.not_valid_after_utc < now + dt.timedelta(days=398):
            raise ValueError("The local CA is near expiry. Renew it and update trust on your devices.")
    else:
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Homebase Local CA " + hostname.removesuffix(".local"))])
        ca_cert = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
                   .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=3650))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
                   .sign(ca_key, hashes.SHA256()))
        atomic_write(ca_key_path, _key_bytes(ca_key))
        atomic_write(ca_cert_path, ca_cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(ca_key_path, 0o600)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_cert.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=397))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname), x509.DNSName("localhost"), *[x509.IPAddress(a) for a in ips]]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    files = {output / "server.key": _key_bytes(key), output / "server.crt": cert.public_bytes(serialization.Encoding.PEM), output / "homebase-rootCA.crt": ca_cert.public_bytes(serialization.Encoding.PEM)}
    for path, contents in files.items():
        atomic_write(path, contents)
        os.chmod(path, 0o600 if path.suffix == ".key" else 0o644)
        if uid is not None or gid is not None:
            os.chown(path, uid if uid is not None else -1, gid if gid is not None else -1)
    if uid is not None or gid is not None:
        os.chown(output, uid if uid is not None else -1, gid if gid is not None else -1)
    primary = next((str(a) for a in ips if not a.is_loopback), "127.0.0.1")
    return {"hostname": hostname, "url": f"https://{hostname}:8420", "ipUrl": f"https://{'['+primary+']' if ':' in primary else primary}:8420", "caCertificate": str(output / "homebase-rootCA.crt"), "certificateFingerprintSHA256": ca_cert.fingerprint(hashes.SHA256()).hex(":").upper()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="certs")
    parser.add_argument("--ca-dir", default=".setup-ca")
    parser.add_argument("--data-dir", default=".data")
    parser.add_argument("--ip", action="append", default=[])
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    args = parser.parse_args(argv)
    from .lan import get_lan_name
    addresses = args.ip
    if not addresses:
        from .monitor import Monitor
        monitor = Monitor()
        address = monitor.overview().get("network", {}).get("localAddress")
        monitor.close()
        if not address:
            parser.error("Could not detect a local IPv4 address. Provide --ip YOUR-LAN-IP.")
        addresses = [address]
    try:
        result = create_certificates(args.output_dir, args.ca_dir, get_lan_name(args.data_dir), addresses, uid=args.uid, gid=args.gid)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    print("No system trust, firewall, router, or public access settings were changed.")


if __name__ == "__main__":
    main()
