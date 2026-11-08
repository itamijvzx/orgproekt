"""Issue one local TLS server certificate. The signing key exists only in memory."""

import os
import sys
from datetime import UTC, datetime, timedelta
from ipaddress import ip_network
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HOST = "orgproekt-lab.localhost"


def issue(directory: Path) -> None:
    directory.mkdir(mode=0o700, exist_ok=False)
    now = datetime.now(UTC)
    expires = now + timedelta(days=180)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OrgProekt HTTPS training CA")])
    server_usage = x509.KeyUsage(True, False, False, False, False, False, False, False, False)
    issuer_usage = x509.KeyUsage(False, False, False, False, False, True, True, False, False)
    names = x509.NameConstraints(
        permitted_subtrees=[x509.DNSName(HOST)],
        excluded_subtrees=[
            x509.IPAddress(ip_network("0.0.0.0/0")),
            x509.IPAddress(ip_network("::/0")),
        ],
    )
    common = (
        x509.CertificateBuilder()
        .issuer_name(issuer)
        .not_valid_before(now - timedelta(minutes=10))
        .not_valid_after(expires)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    )
    root = (
        common.subject_name(issuer)
        .serial_number(x509.random_serial_number())
        .public_key(ca_key.public_key())
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(issuer_usage, critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(names, critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server = (
        common.subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
        .serial_number(x509.random_serial_number())
        .public_key(server_key.public_key())
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(server_usage, critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "issuer.cer").write_bytes(root.public_bytes(serialization.Encoding.DER))
    (directory / "server.pem").write_bytes(server.public_bytes(serialization.Encoding.PEM))
    with (directory / "server.key").open("xb") as key_file:
        key_file.write(
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    print(f"{HOST}: certificate valid until {expires:%Y-%m-%d} UTC")


if __name__ == "__main__":
    os.umask(0o077)
    issue(Path(sys.argv[1]))
