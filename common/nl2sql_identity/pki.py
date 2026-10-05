"""Each service's own TLS identity, issued by one development CA (V6-36).

Until 6.1 the agent API wrote one self-signed certificate and four services
presented it, its key mounted into eleven containers -- so one stolen key
was every server's, and every service ran as root to read a file another
service wrote 0600. Now each server has a key of its own, in a volume only
it mounts, and a certificate for its own names, issued by a CA that exists
only on this machine.

One CA rather than a self-signed certificate per service, for the people at
the other end: a browser or the desktop client trusts `ca.crt` once and
every page, the API and the sign-in service verify, where a certificate per
service would be a warning per page. The proxies verify their upstreams
against the same file, so a service's certificate can be reissued without
any of them noticing.

Run as a one-shot container before anything that serves TLS (`pki` in
docker-compose.yml). Each run, for every identity it is given:

* no certificate: one is issued;
* a certificate this CA issued, still good, for every name asked: kept, so
  a client that pinned it keeps working;
* one this CA issued that is close to expiry, lacks a name, or was signed
  by an earlier CA: reissued;
* the development certificate 6.0 generated: replaced, which is what moves
  an existing volume onto its own identity;
* anything else -- a certificate someone mounted from a real CA: left alone,
  and said so.

`ca.crt` is written beside every identity, and into any trust-only
directory named, so a container that verifies others needs no second mount.
The CA's own key never leaves the CA directory, which only this container
mounts.

An identity may name its owner (V6-31): the uid and gid of the account the
service runs as, which then owns the key and may read it -- 0640, so its
group may too -- while nothing else in the container can. Given on every
run, kept certificate or new, so a volume from 6.1, whose key root wrote
0600 for a service that ran as root, is handed over on the first start.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

#: The CA's common name. Read back by the agent API (`api/tls.py`), which
#: treats anything this CA issued as a development certificate.
CA_NAME = "nl2sql development CA"
ORGANISATION = "nl2sql (development)"

#: What the agent API's own generator wrote as its organisation through 6.0,
#: which marks a certificate this module may replace.
LEGACY_ORGANISATION = "nl2sql agent (development)"

CA_DAYS = 3650
LEAF_DAYS = 365
#: Reissued when it has less than this left, so a stack restarted now and
#: then never meets an expired certificate.
RENEW_DAYS = 30

KEY_FILE = "server.key"
CERT_FILE = "server.crt"
CA_FILE = "ca.crt"


class PkiError(RuntimeError):
    """An identity could not be issued as asked."""


@dataclass(frozen=True)
class Identity:
    """One server's TLS identity: where it lives and the names it answers to."""

    name: str
    directory: Path
    hostnames: tuple[str, ...]
    #: The uid and gid the service runs as, which own its key; None leaves
    #: the key to whoever runs this, 0600.
    owner: tuple[int, int] | None = None


def parse_identity(text: str, *, also: Sequence[str] = ()) -> Identity:
    """`NAME=DIRECTORY=HOST,HOST,...[=UID:GID]`, as compose writes them.

    `=` rather than `:` between the parts because an IPv6 address is one of
    the names. `also` are names every identity covers besides its own: the
    machine's name on the network, say, for a browser elsewhere. The last
    part, when there is one, is the account the service runs as.
    """
    parts = text.split("=", 3)
    if len(parts) < 3 or not parts[0] or not parts[1]:
        raise PkiError(f"an identity is NAME=DIRECTORY=HOST,HOST,...[=UID:GID]; got {text!r}")
    name, directory, hosts = parts[:3]
    owner = _owner(name, parts[3]) if len(parts) == 4 else None
    names = [host.strip() for host in hosts.split(",") if host.strip()]
    names += [host for host in also if host and host not in names]
    if not names:
        raise PkiError(f"identity {name} names no host to be issued for")
    return Identity(name=name, directory=Path(directory), hostnames=tuple(names), owner=owner)


def _owner(name: str, text: str) -> tuple[int, int]:
    uid, _, gid = text.partition(":")
    if not (uid.isdigit() and gid.isdigit()):
        raise PkiError(f"identity {name}'s owner is UID:GID, by number; got {text!r}")
    return int(uid), int(gid)


def hand_over(identity: Identity, *, chown=os.chown) -> str | None:
    """Give the key and certificate to the account the service runs as.

    The key 0640: the account reads it, and so may its group -- the review
    service runs as whoever owns the checkout it writes and reads its key
    through the group. Says to whom, or None when no owner was named.
    """
    if identity.owner is None:
        return None
    uid, gid = identity.owner
    for name, mode in ((KEY_FILE, 0o640), (CERT_FILE, 0o644), (CA_FILE, 0o644)):
        path = identity.directory / name
        if path.is_file():
            chown(path, uid, gid)
            os.chmod(path, mode)
    return f"{uid}:{gid}"


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or dt.datetime.now(dt.timezone.utc)


def _write(path: Path, data: bytes, mode: int) -> None:
    """Created with its mode from the start, so a key is never readable by
    anyone else even for a moment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.chmod(path, mode)


def _private(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _pem(certificate: x509.Certificate) -> bytes:
    return certificate.public_bytes(serialization.Encoding.PEM)


def _alt_names(hostnames: Sequence[str]) -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = []
    for host in hostnames:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    return names


def covered_names(certificate: x509.Certificate) -> set[str]:
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set()
    return {str(v) for v in san.get_values_for_type(x509.DNSName)} | {
        str(v) for v in san.get_values_for_type(x509.IPAddress)
    }


def _organisation(name: x509.Name) -> str:
    found = name.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    return str(found[0].value) if found else ""


def is_development(certificate: x509.Certificate) -> bool:
    """Issued by this module's CA, or the development certificate 6.0 made.

    Either way a certificate no browser trusts until someone tells it to,
    which is what the agent API's `API_TLS_ALLOW_SELF_SIGNED=false` refuses.
    """
    issuer_cn = certificate.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    if issuer_cn and issuer_cn[0].value == CA_NAME:
        return True
    return certificate.issuer == certificate.subject and _organisation(certificate.subject) == LEGACY_ORGANISATION


# --- the CA -------------------------------------------------------------------


def _new_ca(now: dt.datetime, days: int) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANISATION),
        ]
    )
    start = now - dt.timedelta(minutes=5)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + dt.timedelta(days=days))
        # A CA for one level of servers and nothing below them.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return certificate, key


def ensure_ca(
    directory: Path, *, days: int = CA_DAYS, now: dt.datetime | None = None
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey, str]:
    """The CA in `directory`, made when there is none or it is near its end.

    A new CA means every identity is reissued on this same run, and every
    client that trusted the old `ca.crt` has to be given the new one -- so it
    is made rarely: once, and again only within `RENEW_DAYS` of ten years.
    """
    now = _now(now)
    cert_path, key_path = directory / CA_FILE, directory / "ca.key"
    if cert_path.is_file() and key_path.is_file():
        try:
            certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except ValueError as exc:
            raise PkiError(f"the CA in {directory} cannot be read: {exc}") from exc
        if certificate.not_valid_after_utc - now > dt.timedelta(days=RENEW_DAYS):
            return certificate, key, f"using the CA in {directory}"  # type: ignore[return-value]
        reason = "replaced the CA, which was about to expire"
    else:
        reason = f"made a CA in {directory}"
    certificate, key = _new_ca(now, days)
    _write(key_path, _private(key), 0o600)
    _write(cert_path, _pem(certificate), 0o644)
    return certificate, key, reason


# --- the identities -----------------------------------------------------------


def _issue_leaf(
    identity: Identity,
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    *,
    now: dt.datetime,
    days: int,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    start = now - dt.timedelta(minutes=5)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, identity.hostnames[0]),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANISATION),
                    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, identity.name),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        # Never past the CA that vouches for it.
        .not_valid_after(min(start + dt.timedelta(days=days), ca_cert.not_valid_after_utc))
        .add_extension(x509.SubjectAlternativeName(_alt_names(identity.hostnames)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return certificate, key


def _signed_by(certificate: x509.Certificate, ca_cert: x509.Certificate) -> bool:
    try:
        certificate.verify_directly_issued_by(ca_cert)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def _why_reissue(
    certificate: x509.Certificate, identity: Identity, ca_cert: x509.Certificate, now: dt.datetime
) -> str | None:
    """Why a certificate this CA issued must be replaced, or None to keep it."""
    if not _signed_by(certificate, ca_cert):
        return "it was signed by an earlier CA"
    missing = [host for host in identity.hostnames if host not in covered_names(certificate)]
    if missing:
        return f"it did not cover {', '.join(missing)}"
    if certificate.not_valid_after_utc - now <= dt.timedelta(days=RENEW_DAYS):
        return "it was about to expire"
    return None


def ensure_identity(
    identity: Identity,
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    *,
    days: int = LEAF_DAYS,
    now: dt.datetime | None = None,
) -> str:
    """Make `identity`'s directory hold its own good certificate; say what was done."""
    now = _now(now)
    cert_path = identity.directory / CERT_FILE
    key_path = identity.directory / KEY_FILE
    ca_pem = _pem(ca_cert)
    if not (identity.directory / CA_FILE).is_file() or (identity.directory / CA_FILE).read_bytes() != ca_pem:
        _write(identity.directory / CA_FILE, ca_pem, 0o644)

    if not cert_path.is_file() or not key_path.is_file():
        reason = "issued"
    else:
        try:
            existing = x509.load_pem_x509_certificate(cert_path.read_bytes())
        except ValueError:
            existing = None
        if existing is None:
            reason = "reissued: the certificate there could not be read"
        elif existing.issuer == ca_cert.subject or _organisation(existing.issuer) == ORGANISATION:
            why = _why_reissue(existing, identity, ca_cert, now)
            if why is None:
                return f"{identity.name}: kept, good until {existing.not_valid_after_utc.date()}"
            reason = f"reissued: {why}"
        elif is_development(existing):
            reason = "issued its own, replacing the shared development certificate"
        else:
            return (
                f"{identity.name}: left alone -- {cert_path} was issued by "
                f"{existing.issuer.rfc4514_string()}, not by this CA"
            )

    certificate, key = _issue_leaf(identity, ca_cert, ca_key, now=now, days=days)
    _write(key_path, _private(key), 0o600)
    _write(cert_path, _pem(certificate), 0o644)
    return f"{identity.name}: {reason} for {', '.join(identity.hostnames)}"


def ensure_trust(directory: Path, ca_cert: x509.Certificate) -> str:
    """Only `ca.crt`, for a container that verifies others and serves nothing."""
    _write(directory / CA_FILE, _pem(ca_cert), 0o644)
    return f"trust: {directory / CA_FILE}"


# --- the command --------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m nl2sql_identity.pki",
        description="Issue each server its own TLS certificate from one development CA.",
    )
    parser.add_argument("--ca-dir", required=True, help="where the CA's key and certificate live")
    parser.add_argument(
        "--trust-dir", action="append", default=[], help="a directory to receive only ca.crt; repeatable"
    )
    parser.add_argument(
        "--also",
        default="",
        help="comma-separated names every identity covers too (TLS_EXTRA_HOSTNAMES)",
    )
    parser.add_argument("--days", type=int, default=LEAF_DAYS, help="how long a certificate is good for")
    parser.add_argument("identity", nargs="*", help="NAME=DIRECTORY=HOST,HOST,...[=UID:GID]")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, now: dt.datetime | None = None) -> int:
    args = parse_args(argv)
    also = [host.strip() for host in args.also.split(",") if host.strip()]
    try:
        identities = [parse_identity(text, also=also) for text in args.identity]
        ca_cert, ca_key, said = ensure_ca(Path(args.ca_dir), now=now)
        print(f"nl2sql-pki: {said}")
        for identity in identities:
            done = ensure_identity(identity, ca_cert, ca_key, days=args.days, now=now)
            owner = hand_over(identity)
            print(f"nl2sql-pki: {done}" + (f"; the key is {owner}'s" if owner else ""))
        for directory in args.trust_dir:
            print(f"nl2sql-pki: {ensure_trust(Path(directory), ca_cert)}")
    except (PkiError, OSError) as exc:
        print(f"nl2sql-pki: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
