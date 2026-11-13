"""Automated generation of local Certificate Authority and SAN SSL certificates for Chappie services."""

import ipaddress
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("generate_certs")


def generate_private_key(key_size: int = 2048) -> rsa.RSAPrivateKey:
    """Generate an RSA private key."""
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
    )


def save_key(key: rsa.RSAPrivateKey, path: Path) -> None:
    """Serialize and save private key to PEM format."""
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    logger.info("Saved private key: %s", path)


def save_cert(cert: x509.Certificate, path: Path) -> None:
    """Serialize and save X.509 certificate to PEM format."""
    pem = cert.public_bytes(serialization.Encoding.PEM)
    path.write_bytes(pem)
    logger.info("Saved certificate: %s", path)


def generate_ca(output_dir: Path) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Generate self-signed Certificate Authority."""
    ca_key = generate_private_key(key_size=4096)
    save_key(ca_key, output_dir / "ca.key")

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Chappie AI Engineering"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Chappie Root CA"),
    ])

    now = datetime.now(UTC)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))  # 10 years validity
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    save_cert(ca_cert, output_dir / "ca.crt")
    return ca_key, ca_cert


def generate_service_cert(
    service_name: str,
    common_name: str,
    alt_names: list[str],
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    output_dir: Path,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Generate a service certificate signed by the Root CA."""
    service_key = generate_private_key(key_size=2048)
    save_key(service_key, output_dir / f"{service_name}.key")

    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Chappie System"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    san_entries: list[x509.GeneralName] = []
    for name in alt_names:
        try:
            ip = ipaddress.ip_address(name)
            san_entries.append(x509.IPAddress(ip))
        except ValueError:
            san_entries.append(x509.DNSName(name))

    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(service_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1825))  # 5 years validity
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH,
                ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    save_cert(cert, output_dir / f"{service_name}.crt")
    return service_key, cert


def generate_all_certs(output_directory: Path | str | None = None) -> None:
    """Generate Root CA and all service SSL certificates."""
    if output_directory is None:
        root_dir = Path(__file__).resolve().parent.parent
        output_dir = root_dir / "certs"
    else:
        output_dir = Path(output_directory)

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Generating SSL certificates in: %s", output_dir)

    # 1. Root CA
    ca_key, ca_cert = generate_ca(output_dir)

    # 2. Reverse Proxy (Nginx)
    generate_service_cert(
        service_name="reverse_proxy",
        common_name="0.0.0.0",
        alt_names=["0.0.0.0", "localhost", "127.0.0.1", "reverse_proxy", "nginx"],
        ca_key=ca_key,
        ca_cert=ca_cert,
        output_dir=output_dir,
    )

    # 3. Backend
    generate_service_cert(
        service_name="backend",
        common_name="backend",
        alt_names=["backend", "0.0.0.0", "localhost", "127.0.0.1"],
        ca_key=ca_key,
        ca_cert=ca_cert,
        output_dir=output_dir,
    )

    # 4. Agent
    generate_service_cert(
        service_name="agent",
        common_name="agent",
        alt_names=["agent", "0.0.0.0", "localhost", "127.0.0.1"],
        ca_key=ca_key,
        ca_cert=ca_cert,
        output_dir=output_dir,
    )

    # 5. Database (PostgreSQL)
    generate_service_cert(
        service_name="db",
        common_name="db",
        alt_names=["db", "postgres", "0.0.0.0", "localhost", "127.0.0.1"],
        ca_key=ca_key,
        ca_cert=ca_cert,
        output_dir=output_dir,
    )

    logger.info("Successfully generated CA and all service SSL certificates.")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    generate_all_certs(out)
