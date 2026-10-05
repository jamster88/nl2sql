"""Each server's own TLS identity, from one development CA (V6-36).

The properties that matter: every identity gets a key no other identity
has; a client that trusts `ca.crt` verifies every one of them, by name; a
certificate is kept while it is good and replaced when it is not; and a
certificate someone mounted from a real CA is never touched.
"""

from __future__ import annotations

import datetime as dt
import runpy
import socket
import ssl
import stat
import sys
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from nl2sql_identity import pki

NOW = dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.timezone.utc)


def _cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


@pytest.fixture
def ca(tmp_path: Path):
    certificate, key, _ = pki.ensure_ca(tmp_path / "ca", now=NOW)
    return certificate, key


def _identity(tmp_path: Path, name: str = "api", hosts: str = "localhost,nl2sql-api,127.0.0.1,::1") -> pki.Identity:
    return pki.parse_identity(f"{name}={tmp_path / name}={hosts}")


# --- parsing ------------------------------------------------------------------


def test_an_identity_is_a_name_a_directory_and_its_hosts(tmp_path):
    identity = pki.parse_identity(f"api={tmp_path}=localhost, nl2sql-api ,::1", also=["host.lan", "localhost"])
    assert identity == pki.Identity("api", tmp_path, ("localhost", "nl2sql-api", "::1", "host.lan"))


@pytest.mark.parametrize("text", ["api", "api=/x", "=/x=localhost", "api==localhost", "api=/x= , "])
def test_a_malformed_identity_is_refused(text):
    with pytest.raises(pki.PkiError):
        pki.parse_identity(text)


# --- the CA -------------------------------------------------------------------


def test_the_ca_is_made_once_and_its_key_is_private(tmp_path):
    first, _, said = pki.ensure_ca(tmp_path, now=NOW)
    assert said.startswith("made a CA")
    assert stat.S_IMODE((tmp_path / "ca.key").stat().st_mode) == 0o600
    again, _, said = pki.ensure_ca(tmp_path, now=NOW + dt.timedelta(days=400))
    assert said.startswith("using the CA") and again.serial_number == first.serial_number
    assert first.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    assert first.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == pki.CA_NAME


def test_a_ca_near_its_end_is_replaced(tmp_path):
    first, _, _ = pki.ensure_ca(tmp_path, now=NOW, days=40)
    second, _, said = pki.ensure_ca(tmp_path, now=NOW + dt.timedelta(days=20))
    assert said.startswith("replaced the CA") and second.serial_number != first.serial_number


def test_an_unreadable_ca_is_an_error_not_a_new_one(tmp_path):
    pki.ensure_ca(tmp_path, now=NOW)
    (tmp_path / "ca.key").write_text("not a key")
    with pytest.raises(pki.PkiError, match="cannot be read"):
        pki.ensure_ca(tmp_path, now=NOW)


# --- identities ---------------------------------------------------------------


def test_an_identity_gets_its_own_key_and_a_certificate_for_its_names(tmp_path, ca):
    said = pki.ensure_identity(_identity(tmp_path), *ca, now=NOW)
    directory = tmp_path / "api"
    assert said.startswith("api: issued for localhost")
    assert stat.S_IMODE((directory / "server.key").stat().st_mode) == 0o600
    certificate = _cert(directory / "server.crt")
    assert pki.covered_names(certificate) == {"localhost", "nl2sql-api", "127.0.0.1", "::1"}
    assert certificate.issuer == ca[0].subject
    assert (directory / "ca.crt").read_bytes() == ca[0].public_bytes(serialization.Encoding.PEM)
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    certificate.verify_directly_issued_by(ca[0])


def test_no_two_identities_share_a_key(tmp_path, ca):
    for name in ("api", "review", "auth"):
        pki.ensure_identity(_identity(tmp_path, name, f"nl2sql-{name}"), *ca, now=NOW)
    keys = {(tmp_path / name / "server.key").read_bytes() for name in ("api", "review", "auth")}
    assert len(keys) == 3


def test_a_good_certificate_is_kept_so_a_pinned_one_keeps_working(tmp_path, ca):
    identity = _identity(tmp_path)
    pki.ensure_identity(identity, *ca, now=NOW)
    before = (tmp_path / "api" / "server.crt").read_bytes()
    said = pki.ensure_identity(identity, *ca, now=NOW + dt.timedelta(days=100))
    assert said.startswith("api: kept, good until")
    assert (tmp_path / "api" / "server.crt").read_bytes() == before


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ("name", "did not cover nl2sql-api.lan"),
        ("time", "about to expire"),
        ("ca", "signed by an earlier CA"),
        ("garbage", "could not be read"),
    ],
)
def test_a_certificate_that_is_no_longer_right_is_reissued(tmp_path, ca, change, why):
    identity = _identity(tmp_path)
    pki.ensure_identity(identity, *ca, now=NOW)
    now, signer = NOW, ca
    if change == "name":
        identity = pki.parse_identity(f"api={tmp_path / 'api'}=localhost,nl2sql-api.lan")
    elif change == "time":
        now = NOW + dt.timedelta(days=pki.LEAF_DAYS - 10)
    elif change == "ca":
        certificate, key, _ = pki.ensure_ca(tmp_path / "another", now=NOW)
        signer = (certificate, key)
    else:
        (tmp_path / "api" / "server.crt").write_text("garbage")
    said = pki.ensure_identity(identity, *signer, now=now)
    assert why in said
    _cert(tmp_path / "api" / "server.crt").verify_directly_issued_by(signer[0])


def test_the_shared_development_certificate_of_six_point_oh_is_replaced(tmp_path, ca):
    """What moves an existing apitls volume onto its own identity."""
    from nl2sql_agent.api.tls import generate_self_signed

    directory = tmp_path / "api"
    generate_self_signed(directory / "server.crt", directory / "server.key", hostnames=["localhost"])
    said = pki.ensure_identity(_identity(tmp_path), *ca, now=NOW)
    assert "replacing the shared development certificate" in said
    assert _cert(directory / "server.crt").issuer == ca[0].subject


def test_a_certificate_from_a_real_ca_is_left_alone(tmp_path, ca):
    directory = tmp_path / "api"
    mounted = _self_signed_named(tmp_path, "Example Corp").public_bytes(serialization.Encoding.PEM)
    directory.mkdir()
    (directory / "server.crt").write_bytes(mounted)
    (directory / "server.key").write_text("theirs")
    said = pki.ensure_identity(_identity(tmp_path), *ca, now=NOW)
    assert "left alone" in said and "not by this CA" in said
    assert (directory / "server.crt").read_bytes() == mounted
    assert (directory / "server.key").read_text() == "theirs"
    assert (directory / "ca.crt").is_file(), "it can still verify the others"


def test_a_leaf_never_outlives_its_ca(tmp_path):
    certificate, key, _ = pki.ensure_ca(tmp_path / "ca", now=NOW, days=100)
    pki.ensure_identity(_identity(tmp_path), certificate, key, days=365, now=NOW)
    assert _cert(tmp_path / "api" / "server.crt").not_valid_after_utc <= certificate.not_valid_after_utc


def test_a_trust_directory_receives_the_ca_certificate_and_nothing_else(tmp_path, ca):
    pki.ensure_trust(tmp_path / "trust", ca[0])
    assert sorted(p.name for p in (tmp_path / "trust").iterdir()) == ["ca.crt"]


def test_development_means_this_ca_or_the_old_self_signed_one(tmp_path, ca):
    from nl2sql_agent.api.tls import generate_self_signed

    pki.ensure_identity(_identity(tmp_path), *ca, now=NOW)
    assert pki.is_development(_cert(tmp_path / "api" / "server.crt"))
    generate_self_signed(tmp_path / "old.crt", tmp_path / "old.key", hostnames=["localhost"])
    assert pki.is_development(_cert(tmp_path / "old.crt"))
    assert not pki.is_development(_self_signed_named(tmp_path, "someone else"))


def _self_signed_named(tmp_path: Path, organisation: str) -> x509.Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, organisation)])
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(NOW)
        .not_valid_after(NOW + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )


def test_a_certificate_with_no_names_covers_none():
    assert pki.covered_names(_self_signed_named(Path("."), "x")) == set()


# --- what a client sees -----------------------------------------------------------


def test_a_client_that_trusts_the_ca_verifies_each_server_by_name(tmp_path):
    """The point of one CA: a TLS handshake against the issued identity,
    verified with `ca.crt` and the server's name, with no exception made --
    under Python's strict X.509 checking, which is the default since 3.13."""
    certificate, key, _ = pki.ensure_ca(tmp_path / "ca")
    pki.ensure_identity(_identity(tmp_path, "auth", "nl2sql-auth,localhost"), certificate, key)
    directory = tmp_path / "auth"
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(directory / "server.crt", directory / "server.key")
    client_context = ssl.create_default_context(cafile=str(directory / "ca.crt"))

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        connection, _ = listener.accept()
        with server_context.wrap_socket(connection, server_side=True) as tls:
            tls.recv(1)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    with socket.create_connection(listener.getsockname()) as raw:
        with client_context.wrap_socket(raw, server_hostname="nl2sql-auth") as tls:
            assert tls.getpeercert()["subject"]
            tls.send(b"x")
    thread.join(timeout=5)
    listener.close()


# --- the command ------------------------------------------------------------------


def test_the_command_issues_every_identity_and_the_trust_directory(tmp_path, capsys):
    status = pki.main(
        [
            f"--ca-dir={tmp_path / 'ca'}",
            f"--trust-dir={tmp_path / 'trust'}",
            "--also=host.lan",
            f"api={tmp_path / 'api'}=localhost,nl2sql-api",
            f"gui={tmp_path / 'gui'}=localhost",
        ],
        now=NOW,
    )
    said = capsys.readouterr().out
    assert status == 0
    assert "made a CA" in said and "api: issued" in said and "gui: issued" in said and "trust:" in said
    assert "host.lan" in pki.covered_names(_cert(tmp_path / "gui" / "server.crt"))


def test_the_command_says_what_went_wrong_and_exits_two(tmp_path, capsys):
    assert pki.main([f"--ca-dir={tmp_path}", "not-an-identity"]) == 2
    assert "NAME=DIRECTORY=HOST" in capsys.readouterr().err


def test_the_module_runs_as_a_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pki", f"--ca-dir={tmp_path / 'ca'}", f"api={tmp_path / 'api'}=localhost"])
    monkeypatch.delitem(sys.modules, "nl2sql_identity.pki", raising=False)
    with pytest.raises(SystemExit) as finished:
        runpy.run_module("nl2sql_identity.pki", run_name="__main__")
    assert finished.value.code == 0
    assert "api: issued" in capsys.readouterr().out
