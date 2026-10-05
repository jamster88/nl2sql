"""The auth service's settings, and every variable that sets them."""

from __future__ import annotations

import os

import pytest

from nl2sql_auth.settings import (
    DEFAULT_GROUP_ROLES,
    SETTING_FIELDS,
    AuthSettings,
    group_roles,
)

VARIABLES = (
    "AUTH_HOST AUTH_PORT AUTH_ROOT_PATH AUTH_TLS_ENABLED AUTH_TLS_CERT_FILE AUTH_TLS_KEY_FILE "
    "AUTH_SIGNING_KEY_FILE AUTH_PUBLIC_KEY_FILE AUTH_SESSION_HOURS AUTH_COOKIE_NAME "
    "AUTH_THROTTLE_FAILURES AUTH_THROTTLE_SECONDS AUTH_DB_HOST AUTH_DB_PORT AUTH_DB_NAME "
    "AUTH_DB_SSLMODE AUTH_DB_CONNECT_TIMEOUT AUTH_ROLESYNC_DB_URL AUTH_ROLESYNC_DB_URL_FILE "
    "AUTH_READER_ROLE AUTH_GROUP_ROLES AUTH_ROLE_SYNC_INTERVAL AUTH_USER_STATEMENT_TIMEOUT_MS "
    "AUTH_USER_CONNECTION_LIMIT AUTH_LDAP_URL AUTH_LDAP_STARTTLS AUTH_LDAP_CACERT LDAP_BASE_DN "
    "LDAP_MODE LDAP_SERVICE_PASSWORD LDAP_SERVICE_PASSWORD_FILE AUTH_LDAP_TIMEOUT "
    "LDAP_MIN_PASSWORD_LENGTH MLFLOW_ALLOWED_ROLES AUTH_CORS_ORIGINS AUTH_DOCS_ENABLED AUTH_LOG_LEVEL"
).split()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_with_nothing_set_the_environment_gives_the_defaults():
    settings = AuthSettings.from_env()
    assert settings == AuthSettings()
    assert settings.source == "environment"
    assert settings.group_roles == DEFAULT_GROUP_ROLES
    assert settings.session_seconds == 8 * 3600
    assert not settings.replica


def test_every_setting_is_read_from_its_variable(monkeypatch, tmp_path):
    secret = tmp_path / "url"
    secret.write_text("postgresql://sync:pw@db/retail\n")
    values = {
        "AUTH_HOST": "127.0.0.1",
        "AUTH_PORT": "9446",
        "AUTH_ROOT_PATH": "/auth-svc",
        "AUTH_TLS_ENABLED": "false",
        "AUTH_TLS_CERT_FILE": "/c.crt",
        "AUTH_TLS_KEY_FILE": "/c.key",
        "AUTH_SIGNING_KEY_FILE": "/k/session.key",
        "AUTH_PUBLIC_KEY_FILE": "/k/session.pub",
        "AUTH_SESSION_HOURS": "1.5",
        "AUTH_COOKIE_NAME": "sid",
        "AUTH_THROTTLE_FAILURES": "3",
        "AUTH_THROTTLE_SECONDS": "60",
        "AUTH_DB_HOST": "db",
        "AUTH_DB_PORT": "6432",
        "AUTH_DB_NAME": "retail",
        "AUTH_DB_SSLMODE": "require",
        "AUTH_DB_CONNECT_TIMEOUT": "2",
        "AUTH_ROLESYNC_DB_URL_FILE": str(secret),
        "AUTH_READER_ROLE": "reader",
        "AUTH_GROUP_ROLES": "NL2SQL Users=nl2sql_users, bogus, analysts=nl2sql_reviewers",
        "AUTH_ROLE_SYNC_INTERVAL": "10",
        "AUTH_USER_STATEMENT_TIMEOUT_MS": "5000",
        "AUTH_USER_CONNECTION_LIMIT": "2",
        "AUTH_LDAP_URL": "ldaps://dir:636",
        "AUTH_LDAP_STARTTLS": "false",
        "AUTH_LDAP_CACERT": "/ca.crt",
        "LDAP_BASE_DN": "dc=corp,dc=example",
        "LDAP_MODE": "Replica",
        "LDAP_SERVICE_PASSWORD": "svc",
        "AUTH_LDAP_TIMEOUT": "4",
        "LDAP_MIN_PASSWORD_LENGTH": "16",
        "MLFLOW_ALLOWED_ROLES": "nl2sql_admins, ",
        "AUTH_CORS_ORIGINS": "https://a, https://b",
        "AUTH_DOCS_ENABLED": "no",
        "AUTH_LOG_LEVEL": "debug",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = AuthSettings.from_env()
    assert settings == AuthSettings(
        host="127.0.0.1",
        port=9446,
        root_path="/auth-svc",
        tls_enabled=False,
        tls_cert_file="/c.crt",
        tls_key_file="/c.key",
        signing_key_file="/k/session.key",
        public_key_file="/k/session.pub",
        session_hours=1.5,
        cookie_name="sid",
        throttle_failures=3,
        throttle_seconds=60,
        db_host="db",
        db_port=6432,
        db_name="retail",
        db_sslmode="require",
        db_connect_timeout=2,
        rolesync_url="postgresql://sync:pw@db/retail",
        reader_role="reader",
        group_roles={"NL2SQL Users": "nl2sql_users", "analysts": "nl2sql_reviewers"},
        role_sync_interval=10.0,
        user_statement_timeout_ms=5000,
        user_connection_limit=2,
        ldap_url="ldaps://dir:636",
        ldap_starttls=False,
        ldap_cacert="/ca.crt",
        ldap_base_dn="dc=corp,dc=example",
        ldap_mode="replica",
        ldap_service_password="svc",
        ldap_timeout=4,
        min_password_length=16,
        mlflow_roles=("nl2sql_admins",),
        cors_origins=("https://a", "https://b"),
        docs_enabled=False,
        log_level="debug",
    )
    assert settings.replica and settings.session_seconds == 5400


def test_every_field_has_a_variable_that_sets_it():
    """A field nobody can set from compose is a default nobody can change."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "..", "auth", "nl2sql_auth", "settings.py")).read()
    from_env = source[source.index("def from_env") : source.index("# --- derived")]
    for name in SETTING_FIELDS:
        assert f"{name}=" in from_env, f"{name} is not read from the environment"


def test_an_empty_secret_file_is_unset(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.write_text("  \n")
    monkeypatch.setenv("LDAP_SERVICE_PASSWORD_FILE", str(empty))
    assert AuthSettings.from_env().ldap_service_password is None


def test_group_roles_default_when_unset():
    assert group_roles(None) == DEFAULT_GROUP_ROLES


def test_the_certificate_is_present_only_with_both_halves(tmp_path):
    cert, key = tmp_path / "c.crt", tmp_path / "c.key"
    settings = AuthSettings(tls_cert_file=str(cert), tls_key_file=str(key))
    assert not settings.certificate_present
    cert.write_text("x")
    assert not settings.certificate_present
    key.write_text("x")
    assert settings.certificate_present


@pytest.mark.parametrize(
    ("host", "tls", "url"),
    [("0.0.0.0", True, "https://localhost:8446"), ("auth.example", False, "http://auth.example:8446")],
)
def test_the_public_url(host, tls, url):
    assert AuthSettings(host=host, tls_enabled=tls).public_url() == url


def test_problems_say_what_would_stop_anyone_signing_in():
    problems = " ".join(AuthSettings().problems())
    assert "AUTH_ROLESYNC_DB_URL" in problems and "LDAP_SERVICE_PASSWORD" in problems
    assert AuthSettings(rolesync_url="x", ldap_service_password="y").problems() == []


def test_warnings_name_the_risky_settings():
    notes = " ".join(
        AuthSettings(tls_enabled=False, ldap_starttls=False, ldap_url="ldap://d", db_sslmode="disable").warnings()
    )
    assert "AUTH_TLS_ENABLED=false" in notes
    assert "AUTH_LDAP_STARTTLS=false" in notes
    assert "AUTH_DB_SSLMODE=disable" in notes and "hostssl" in notes
    assert any("AUTH_DB_SSLMODE=require" in n for n in AuthSettings(db_sslmode="require").warnings())
    assert any("AUTH_DB_SSLMODE=prefer" in n for n in AuthSettings(db_sslmode="prefer").warnings())
    assert AuthSettings(db_sslrootcert=__file__).warnings() == []
    assert AuthSettings(ldap_starttls=False, ldap_url="ldaps://d", db_sslrootcert=__file__).warnings() == []


def test_verify_full_with_no_certificate_to_verify_against_is_said():
    notes = " ".join(AuthSettings(db_sslrootcert="/nowhere/server.crt").warnings())
    assert "no certificate at /nowhere/server.crt" in notes and "pgtls volume" in notes


def test_the_rolesync_url_is_held_to_the_same_tls_as_a_sign_in():
    settings = AuthSettings(rolesync_url="postgresql://sync:pw@db:5432/retail")
    assert settings.rolesync_conninfo == (
        "postgresql://sync:pw@db:5432/retail?sslmode=verify-full&sslrootcert=%2Fetc%2Fnl2sql%2Fpg-tls%2Fserver.crt"
    )
    said = "postgresql://sync:pw@db/retail?sslmode=require"
    assert AuthSettings(rolesync_url=said).rolesync_conninfo == said, "a URL that says how is left alone"
    assert AuthSettings(rolesync_url="postgresql://s@db/r", db_sslmode="prefer").rolesync_conninfo.endswith("?sslmode=prefer")
    assert AuthSettings().rolesync_conninfo is None
