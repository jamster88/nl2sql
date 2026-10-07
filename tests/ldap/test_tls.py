"""The directory's own certificate."""

from __future__ import annotations

import datetime as dt
import stat

import pytest
from cryptography import x509

from nl2sql_ldap import tls
from nl2sql_ldap.settings import DirectorySettings

NOW = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)


def _settings(tmp_path, **overrides) -> DirectorySettings:
    return DirectorySettings(
        tls_cert_file=str(tmp_path / "tls" / "ldap.crt"),
        tls_key_file=str(tmp_path / "keys" / "ldap.key"),
        **overrides,
    )


def test_a_development_certificate_is_written_when_there_is_none(tmp_path):
    settings = _settings(tmp_path, tls_hostnames=("nl2sql-ldap", "ldap", "127.0.0.1"), tls_days=10)
    assert tls.ensure(settings, now=NOW).startswith("wrote a development certificate for nl2sql-ldap, ldap, 127.0.0.1")
    certificate = x509.load_pem_x509_certificate((tmp_path / "tls" / "ldap.crt").read_bytes())
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["nl2sql-ldap", "ldap"]
    assert [str(ip) for ip in names.get_values_for_type(x509.IPAddress)] == ["127.0.0.1"]
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert (certificate.not_valid_after_utc - NOW).days == 9
    assert stat.S_IMODE((tmp_path / "keys" / "ldap.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "tls" / "ldap.crt").stat().st_mode) == 0o644


def test_a_certificate_in_place_is_used_as_it_is(tmp_path):
    settings = _settings(tmp_path)
    tls.ensure(settings, now=NOW)
    before = (tmp_path / "tls" / "ldap.crt").read_bytes()
    assert tls.ensure(settings, now=NOW).startswith("using the certificate at")
    assert (tmp_path / "tls" / "ldap.crt").read_bytes() == before


def test_an_expired_one_is_replaced_when_generating_is_allowed(tmp_path):
    settings = _settings(tmp_path, tls_days=1)
    tls.ensure(settings, now=NOW)
    later = NOW + dt.timedelta(days=5)
    assert tls.expired(tmp_path / "tls" / "ldap.crt", now=later)
    assert tls.ensure(settings, now=later).startswith("replaced the expired certificate")
    assert not tls.expired(tmp_path / "tls" / "ldap.crt", now=later)


def test_with_generating_off_a_missing_or_expired_certificate_is_an_error(tmp_path):
    settings = _settings(tmp_path, tls_generate=False)
    with pytest.raises(tls.CertificateError, match="no certificate at"):
        tls.ensure(settings, now=NOW)
    tls.ensure(_settings(tmp_path, tls_days=1), now=NOW)
    with pytest.raises(tls.CertificateError, match="has expired"):
        tls.ensure(settings, now=NOW + dt.timedelta(days=5))


def test_expiry_is_judged_against_now_by_default(tmp_path):
    tls.ensure(_settings(tmp_path))
    assert not tls.expired(tmp_path / "tls" / "ldap.crt")
