"""The certificate the API server presents, and the switch that refuses it.

A GUI talks to this agent over the network, so the transport is encrypted by
default rather than on request. Getting a real certificate into a container
that anyone can `docker compose up` is not something this repository can do
for you, so it makes its own: a self-signed certificate written on first
start, valid for the names the container is actually reachable by.

That is a development convenience and it should never quietly become the
production arrangement, which is what `API_TLS_ALLOW_SELF_SIGNED=false` is
for. With it off the server will not generate a throwaway certificate and
will not load one it finds -- it stops, and says which file is self-signed.
The failure happens at startup, in front of whoever is deploying it, rather
than in a browser warning that someone clicks through.

`cryptography` does the work rather than the `openssl` binary: the slim
Python image is not guaranteed to have the CLI, and a certificate built here
can be built again inside a test without Docker.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .settings import ApiSettings

#: The private key is readable by nobody but its owner. Written with this
#: mode from the start rather than chmodded afterwards, so there is no window
#: in which it is world-readable.
KEY_MODE = 0o600
CERT_MODE = 0o644

#: 2048 rather than 4096: this is a throwaway certificate regenerated on a
#: fresh volume, and key generation is on the container's startup path.
KEY_BITS = 2048


class TlsError(RuntimeError):
    """The server cannot start with the TLS configuration it was given."""


@dataclass
class CertificateInfo:
    """What a certificate says about itself, in the terms an operator cares about."""

    path: str
    subject: str
    issuer: str
    not_before: dt.datetime
    not_after: dt.datetime
    hostnames: list[str] = field(default_factory=list)
    self_signed: bool = False
    fingerprint_sha256: str = ""
    generated: bool = False
    #: Names that were missing from a certificate already on disk, and which
    #: it was reissued to cover. Empty unless that happened.
    reissued_for: list[str] = field(default_factory=list)
    #: Names the configuration asks for that this certificate does not cover
    #: and which cannot be added, because it is CA-issued. Empty otherwise.
    missing_hostnames: list[str] = field(default_factory=list)

    @property
    def expired(self) -> bool:
        return dt.datetime.now(dt.timezone.utc) >= self.not_after

    @property
    def days_remaining(self) -> int:
        delta = self.not_after - dt.datetime.now(dt.timezone.utc)
        return delta.days

    def summary(self) -> dict[str, object]:
        """The JSON a client is shown at /v1/meta.

        Deliberately not the certificate itself: a client that wants to pin
        one can read it off the connection. This is enough to tell a browser
        warning apart from a misconfiguration -- whether it is self-signed,
        what names it covers, and when it stops being valid.
        """
        return {
            "enabled": True,
            "self_signed": self.self_signed,
            "subject": self.subject,
            "issuer": self.issuer,
            "hostnames": list(self.hostnames),
            "not_after": self.not_after.isoformat(),
            "days_remaining": self.days_remaining,
            "fingerprint_sha256": self.fingerprint_sha256,
            "generated": self.generated,
        }


def _name_to_text(name: x509.Name) -> str:
    return name.rfc4514_string()


def _hostnames(cert: x509.Certificate) -> list[str]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    names = [str(v) for v in san.get_values_for_type(x509.DNSName)]
    names += [str(v) for v in san.get_values_for_type(x509.IPAddress)]
    return names


def describe_certificate(path: str | os.PathLike[str]) -> CertificateInfo:
    """Read a PEM certificate off disk.

    Raises `TlsError` rather than letting a parse error out, because every
    caller here is deciding whether to start the server and wants one
    exception type to catch.
    """
    file = Path(path)
    try:
        cert = x509.load_pem_x509_certificate(file.read_bytes())
    except FileNotFoundError as exc:
        raise TlsError(f"no certificate at {file}") from exc
    except Exception as exc:  # a truncated file, a key pasted in its place
        raise TlsError(f"{file} is not a readable PEM certificate: {exc}") from exc

    return CertificateInfo(
        path=str(file),
        subject=_name_to_text(cert.subject),
        issuer=_name_to_text(cert.issuer),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        hostnames=_hostnames(cert),
        # Issuer equal to subject is what self-signed means. A real chain has
        # a CA in the issuer field, so this needs no list of known-dummy
        # values to stay true.
        self_signed=cert.issuer == cert.subject,
        fingerprint_sha256=cert.fingerprint(hashes.SHA256()).hex(),
    )


def generate_self_signed(
    cert_path: str | os.PathLike[str],
    key_path: str | os.PathLike[str],
    *,
    hostnames: tuple[str, ...] | list[str],
    days: int = 365,
) -> CertificateInfo:
    """Write a throwaway certificate and key covering `hostnames`.

    Every name is put in the SubjectAlternativeName extension, addresses as
    IP entries and everything else as DNS entries, because a certificate
    without a SAN matching the host is rejected outright by anything modern
    -- the common name has not been consulted for years.
    """
    names = list(hostnames) or ["localhost"]
    cert_file, key_file = Path(cert_path), Path(key_path)
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, names[0]),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "nl2sql agent (development)"),
        ]
    )

    alt: list[x509.GeneralName] = []
    for name in names:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alt.append(x509.DNSName(name))

    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # A minute of backdating, so a client whose clock is a little behind
        # the container's does not reject a certificate written seconds ago.
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    _write(
        key_file,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        KEY_MODE,
    )
    _write(cert_file, certificate.public_bytes(serialization.Encoding.PEM), CERT_MODE)

    info = describe_certificate(cert_file)
    info.generated = True
    return info


def _write(path: Path, data: bytes, mode: int) -> None:
    """Create with the right mode from the outset, then write."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, mode)


def ensure_certificate(settings: ApiSettings) -> CertificateInfo | None:
    """The certificate the server will present, or a refusal to start.

    Returns None when TLS is off -- the one case where there is nothing to
    present. Otherwise the order is: use what is on disk, generate one if
    there is nothing and generating is allowed, and in either case refuse if
    what we ended up with is self-signed and self-signed is not allowed.
    """
    if not settings.tls_enabled:
        return None

    cert_file, key_file = Path(settings.tls_cert_file), Path(settings.tls_key_file)
    have_cert, have_key = cert_file.exists(), key_file.exists()

    if have_cert != have_key:
        missing = key_file if have_cert else cert_file
        raise TlsError(
            f"TLS is enabled but only half the pair is present: {missing} is missing. "
            "Provide both files, or remove both and let the server generate a pair."
        )

    if not have_cert:
        if not settings.tls_generate:
            raise TlsError(
                f"TLS is enabled and there is no certificate at {cert_file}, but "
                "API_TLS_GENERATE is false. Mount a certificate and key, or set "
                "API_TLS_GENERATE=true to have a development one written."
            )
        if not settings.tls_allow_self_signed:
            raise TlsError(
                "TLS is enabled with no certificate present, so one would have to be "
                "generated -- and a generated certificate is self-signed, which "
                "API_TLS_ALLOW_SELF_SIGNED=false forbids. Mount a CA-issued "
                f"certificate at {cert_file} with its key at {key_file}."
            )
        info = generate_self_signed(
            cert_file,
            key_file,
            hostnames=settings.tls_hostnames,
            days=settings.tls_days,
        )
    else:
        info = describe_certificate(cert_file)
        # A certificate already on disk is normally kept as it is -- that is
        # what makes a generated one survive a restart, so a client that
        # pinned its fingerprint keeps working.
        #
        # The exception is a *generated* certificate that no longer covers
        # the names it is supposed to. That happens when a new service joins
        # the stack and API_TLS_HOSTNAMES grows: the volume still holds the
        # certificate from before, it is silently missing the new name, and
        # the only symptom is the new service's proxy failing to verify it,
        # with a message about a hostname mismatch and nothing about which
        # name or why.
        #
        # So a self-signed one is reissued to cover them. A CA-issued one
        # never is -- it cannot be, and replacing it is a decision for
        # whoever obtained it -- and `certificate_notes` says so instead.
        missing = [name for name in settings.tls_hostnames if name not in info.hostnames]
        if missing and info.self_signed and settings.tls_generate:
            info = generate_self_signed(
                cert_file,
                key_file,
                hostnames=settings.tls_hostnames,
                days=settings.tls_days,
            )
            info.reissued_for = missing
        elif missing:
            info.missing_hostnames = missing

    if info.self_signed and not settings.tls_allow_self_signed:
        raise TlsError(
            f"{cert_file} is self-signed (issuer and subject are both {info.subject}) "
            "and API_TLS_ALLOW_SELF_SIGNED=false. Replace it with a CA-issued "
            "certificate, or set API_TLS_ALLOW_SELF_SIGNED=true to accept the "
            "development one."
        )
    return info


def certificate_notes(info: CertificateInfo | None) -> list[str]:
    """Things about the certificate worth saying out loud at startup."""
    if info is None:
        return []
    notes: list[str] = []
    if info.expired:
        notes.append(
            f"the certificate at {info.path} expired on {info.not_after.date()}; "
            "clients will refuse to connect. Delete it and restart to have a new "
            "one generated."
        )
    elif info.days_remaining <= 14:
        notes.append(
            f"the certificate at {info.path} expires in {info.days_remaining} day(s)."
        )
    if info.reissued_for:
        notes.append(
            f"the certificate at {info.path} did not cover "
            f"{', '.join(info.reissued_for)}, so a new one was generated. Anything "
            "that pinned the old fingerprint -- or copied it out with `docker "
            "compose cp` -- needs it again."
        )
    if info.missing_hostnames:
        notes.append(
            f"the certificate at {info.path} does not cover "
            f"{', '.join(info.missing_hostnames)} and is CA-issued, so it was left "
            "alone. Anything connecting under those names will fail to verify it; "
            "reissue it to cover them."
        )
    if info.self_signed:
        notes.append(
            "the certificate is self-signed, so clients must trust it explicitly: "
            "curl --cacert, or the CA store of whatever is calling. Set "
            "API_TLS_ALLOW_SELF_SIGNED=false to refuse to start without a real one."
        )
    return notes
