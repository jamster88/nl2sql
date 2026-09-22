"""The certificate, and the switch that refuses the development one.

Two things are being pinned here. The first is that a container with no
certificate mounted still comes up on HTTPS, with a certificate that names
every way the container is reachable -- a SAN-less certificate is rejected
outright by every modern client, and would turn "TLS by default" into "TLS
that nothing can connect to".

The second is the switch. `API_TLS_ALLOW_SELF_SIGNED=false` has to fail at
startup, loudly, in both directions: when a self-signed certificate is
already on disk, and when there is none and one would have to be generated.
Only catching the first would let a fresh deployment quietly write itself a
throwaway certificate and serve it.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest
from nl2sql_agent.api.settings import ApiSettings
from nl2sql_agent.api.tls import (
    CertificateInfo,
    TlsError,
    certificate_notes,
    describe_certificate,
    ensure_certificate,
    generate_self_signed,
)


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "tls" / "server.crt", tmp_path / "tls" / "server.key"


def settings_for(paths: tuple[Path, Path], **kwargs) -> ApiSettings:
    cert, key = paths
    return ApiSettings(tls_cert_file=str(cert), tls_key_file=str(key), **kwargs)


# ---------------------------------------------------------------------------
# Generating one
# ---------------------------------------------------------------------------


def test_a_generated_certificate_names_every_host_it_was_asked_to(paths):
    cert, key = paths
    info = generate_self_signed(cert, key, hostnames=("localhost", "nl2sql-api", "127.0.0.1"))
    assert set(info.hostnames) == {"localhost", "nl2sql-api", "127.0.0.1"}
    assert info.self_signed is True
    assert cert.exists() and key.exists()


def test_an_address_becomes_an_ip_entry_and_a_name_a_dns_entry(paths):
    """A client connecting to https://127.0.0.1 checks the IP SAN, not the
    DNS one, so putting an address in as a DNS name fails verification.
    """
    from cryptography import x509

    cert, key = paths
    generate_self_signed(cert, key, hostnames=("localhost", "127.0.0.1", "::1"))
    loaded = x509.load_pem_x509_certificate(cert.read_bytes())
    san = loaded.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(n) for n in san.get_values_for_type(x509.DNSName)] == ["localhost"]
    assert len(san.get_values_for_type(x509.IPAddress)) == 2


def test_the_private_key_is_not_readable_by_anyone_else(paths):
    cert, key = paths
    generate_self_signed(cert, key, hostnames=("localhost",))
    assert os.stat(key).st_mode & 0o077 == 0, "the private key is group/world readable"


def test_the_certificate_is_backdated_a_little(paths):
    """A client whose clock is a few seconds behind the container's would
    otherwise reject a certificate written moments ago.
    """
    cert, key = paths
    info = generate_self_signed(cert, key, hostnames=("localhost",))
    assert info.not_before < dt.datetime.now(dt.timezone.utc)


def test_the_lifetime_is_what_was_asked_for(paths):
    cert, key = paths
    info = generate_self_signed(cert, key, hostnames=("localhost",), days=30)
    assert 28 <= info.days_remaining <= 30
    assert info.expired is False


def test_generating_creates_the_directory_it_was_pointed_at(paths):
    """The compose volume is empty on first start, and the server has to be
    able to write into it without a preparatory step.
    """
    cert, key = paths
    assert not cert.parent.exists()
    generate_self_signed(cert, key, hostnames=("localhost",))
    assert cert.parent.is_dir()


def test_generating_with_no_hostnames_still_produces_something_usable(paths):
    cert, key = paths
    info = generate_self_signed(cert, key, hostnames=())
    assert info.hostnames == ["localhost"]


# ---------------------------------------------------------------------------
# Reading one
# ---------------------------------------------------------------------------


def test_a_missing_certificate_is_a_tls_error_not_an_oserror(tmp_path):
    with pytest.raises(TlsError, match="no certificate at"):
        describe_certificate(tmp_path / "nothing.crt")


def test_a_file_that_is_not_a_certificate_says_so(tmp_path):
    bad = tmp_path / "server.crt"
    bad.write_text("-----BEGIN CERTIFICATE-----\nnot base64 at all\n")
    with pytest.raises(TlsError, match="not a readable PEM certificate"):
        describe_certificate(bad)


def test_self_signed_is_decided_by_the_issuer_not_by_a_list_of_names(paths):
    """Issuer equal to subject is what self-signed means. Deciding it any
    other way -- an organisation name, a filename -- would be a heuristic
    that a real certificate could trip over.
    """
    cert, key = paths
    info = generate_self_signed(cert, key, hostnames=("localhost",))
    assert info.issuer == info.subject
    assert info.self_signed is True


def test_the_summary_is_enough_to_explain_a_browser_warning(paths):
    cert, key = paths
    summary = generate_self_signed(cert, key, hostnames=("localhost",)).summary()
    assert summary["enabled"] is True
    assert summary["self_signed"] is True
    assert "localhost" in summary["hostnames"]
    assert summary["fingerprint_sha256"]
    assert "not_after" in summary


# ---------------------------------------------------------------------------
# ensure_certificate: the policy
# ---------------------------------------------------------------------------


def test_tls_off_means_there_is_nothing_to_present(paths):
    assert ensure_certificate(settings_for(paths, tls_enabled=False)) is None


def test_a_missing_pair_is_generated_on_first_start(paths):
    info = ensure_certificate(settings_for(paths))
    assert info is not None and info.generated is True
    assert Path(paths[0]).exists()


def test_a_certificate_already_on_disk_is_used_rather_than_replaced(paths):
    first = ensure_certificate(settings_for(paths))
    second = ensure_certificate(settings_for(paths))
    assert second is not None and first is not None
    assert second.fingerprint_sha256 == first.fingerprint_sha256
    assert second.generated is False, "a restart must not invalidate a pinned certificate"


def test_half_a_pair_is_refused_rather_than_half_regenerated(paths):
    """Regenerating the certificate over an existing key, or the other way
    round, produces a pair that does not match and a handshake failure with
    no useful message.
    """
    cert, key = paths
    ensure_certificate(settings_for(paths))
    key.unlink()
    with pytest.raises(TlsError, match="only half the pair"):
        ensure_certificate(settings_for(paths))


def test_generation_can_be_turned_off_for_a_mounted_certificate(paths):
    with pytest.raises(TlsError, match="API_TLS_GENERATE is false"):
        ensure_certificate(settings_for(paths, tls_generate=False))


def test_refusing_self_signed_refuses_to_generate_one(paths):
    """The half that is easy to miss: with nothing on disk the server would
    write itself a throwaway certificate, which is exactly what this switch
    exists to prevent.
    """
    with pytest.raises(TlsError, match="API_TLS_ALLOW_SELF_SIGNED=false"):
        ensure_certificate(settings_for(paths, tls_allow_self_signed=False))
    assert not Path(paths[0]).exists(), "it wrote one anyway"


def test_refusing_self_signed_refuses_one_that_is_already_there(paths):
    ensure_certificate(settings_for(paths))
    with pytest.raises(TlsError, match="is self-signed"):
        ensure_certificate(settings_for(paths, tls_allow_self_signed=False))


def test_the_refusal_says_which_file_and_how_to_fix_it(paths):
    ensure_certificate(settings_for(paths))
    with pytest.raises(TlsError) as raised:
        ensure_certificate(settings_for(paths, tls_allow_self_signed=False))
    message = str(raised.value)
    assert str(paths[0]) in message
    assert "CA-issued" in message


def test_a_ca_issued_certificate_passes_the_switch(paths, tmp_path):
    """The other side of the switch: with a real chain it starts. Built here
    by signing a leaf with a separate CA key, because "not self-signed" is
    the only property under test and a real CA is not available offline.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert, key = paths
    cert.parent.mkdir(parents=True, exist_ok=True)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example CA")])
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nl2sql-api")])
    now = dt.datetime.now(dt.timezone.utc)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("nl2sql-api")]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(
        leaf_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    info = ensure_certificate(settings_for(paths, tls_allow_self_signed=False))
    assert info is not None and info.self_signed is False
    assert certificate_notes(info) == []


# ---------------------------------------------------------------------------
# What gets said about it
# ---------------------------------------------------------------------------


def test_nothing_is_said_when_tls_is_off():
    assert certificate_notes(None) == []


def test_a_self_signed_certificate_tells_the_reader_how_to_trust_it(paths):
    info = ensure_certificate(settings_for(paths))
    notes = " ".join(certificate_notes(info))
    assert "--cacert" in notes
    assert "API_TLS_ALLOW_SELF_SIGNED=false" in notes


def test_an_expiring_certificate_is_mentioned_before_it_bites(paths):
    cert, key = paths
    info = generate_self_signed(cert, key, hostnames=("localhost",), days=3)
    assert any("expires in" in note for note in certificate_notes(info))


def test_an_expired_certificate_says_what_to_do_about_it():
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    info = CertificateInfo(
        path="/etc/nl2sql/tls/server.crt",
        subject="CN=localhost",
        issuer="CN=localhost",
        not_before=past - dt.timedelta(days=365),
        not_after=past,
        self_signed=True,
    )
    notes = " ".join(certificate_notes(info))
    assert info.expired is True
    assert "Delete it and restart" in notes


def test_a_certificate_with_no_subject_alternative_names_reads_as_having_none(tmp_path):
    """Nothing modern accepts such a certificate, but it can be mounted, and
    reading it must report an empty list rather than raising in the banner.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "legacy")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / "legacy.crt"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    info = describe_certificate(path)
    assert info.hostnames == []
    assert info.summary()["hostnames"] == []
