"""The directory's certificate: its own, written on first start.

Its own rather than the agent API's, which three services already present:
a key in one more container is one more place it can be taken from, and the
directory is where every password goes. The certificate is self-signed, so
the retail database and the auth service trust it by name -- it is mounted
into both read-only, and each verifies the directory against it.

A certificate already in place is used as it is, whoever made it: a real
one is mounted over the volume by setting LDAP_TLS_CERT_FILE and
LDAP_TLS_KEY_FILE, with LDAP_TLS_GENERATE=false so a missing one is an
error rather than a quietly generated stand-in.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .settings import DirectorySettings


class CertificateError(RuntimeError):
    """No certificate, and none may be made."""


def _names(hostnames: tuple[str, ...]) -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = []
    for host in hostnames:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    return names


def generate(hostnames: tuple[str, ...], *, days: int, now: dt.datetime | None = None) -> tuple[bytes, bytes]:
    """A self-signed certificate and its key, both PEM."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])])
    start = (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(minutes=5)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(_names(hostnames)), critical=False)
        # A CA as well as a server: it is its own issuer, and a client given
        # it as the CA file has to be allowed to treat it as one.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def expired(path: Path, *, now: dt.datetime | None = None) -> bool:
    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    return certificate.not_valid_after_utc <= (now or dt.datetime.now(dt.timezone.utc))


def ensure(settings: DirectorySettings, *, now: dt.datetime | None = None) -> str:
    """Make sure a certificate is in place; say what was done."""
    cert = Path(settings.tls_cert_file)
    key = Path(settings.tls_key_file)
    if cert.is_file() and key.is_file():
        if not expired(cert, now=now):
            return f"using the certificate at {cert}"
        if not settings.tls_generate:
            raise CertificateError(f"{cert} has expired and LDAP_TLS_GENERATE=false")
        reason = "replaced the expired certificate"
    elif not settings.tls_generate:
        raise CertificateError(
            f"no certificate at {cert} (or no key at {key}) and LDAP_TLS_GENERATE=false"
        )
    else:
        reason = "wrote a development certificate"
    pem, private = generate(settings.tls_hostnames, days=settings.tls_days, now=now)
    cert.parent.mkdir(parents=True, exist_ok=True)
    key.parent.mkdir(parents=True, exist_ok=True)
    # The key first and private from the start, so it is never on disk
    # readable by anyone else even for a moment.
    descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(private)
    os.chmod(key, 0o600)
    cert.write_bytes(pem)
    os.chmod(cert, 0o644)
    return f"{reason} for {', '.join(settings.tls_hostnames)} at {cert}"
