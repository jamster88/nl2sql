"""What the directory is told by its environment, and what it refuses to start with."""

from __future__ import annotations

import pytest

from nl2sql_ldap import settings as module
from nl2sql_ldap.settings import (
    DEFAULT_BASE_DN,
    DEFAULT_GROUPS,
    FLAVOURS,
    REPLICA,
    STANDALONE,
    DirectorySettings,
    SettingsError,
    UpstreamSettings,
    group_map,
    secret,
)

VARIABLES = [
    name
    for name in (
        "LDAP_MODE LDAP_BASE_DN LDAP_ORGANISATION LDAP_SERVICE_PASSWORD LDAP_SERVICE_PASSWORD_FILE "
        "LDAP_ADMIN_USER LDAP_ADMIN_PASSWORD LDAP_ADMIN_PASSWORD_FILE LDAP_ADMIN_NAME LDAP_SEED_FILE "
        "LDAP_GROUPS LDAP_TLS_CERT_FILE LDAP_TLS_KEY_FILE LDAP_TLS_GENERATE LDAP_TLS_HOSTNAMES "
        "LDAP_TLS_DAYS LDAP_REQUIRE_TLS LDAP_LOCKOUT_FAILURES LDAP_LOCKOUT_SECONDS "
        "LDAP_MIN_PASSWORD_LENGTH LDAP_LOG_LEVEL LDAP_UPSTREAM_URI LDAP_UPSTREAM_BIND_DN "
        "LDAP_UPSTREAM_BIND_PASSWORD LDAP_UPSTREAM_BIND_PASSWORD_FILE LDAP_UPSTREAM_BASE_DN "
        "LDAP_UPSTREAM_FLAVOUR LDAP_UPSTREAM_USER_BASE LDAP_UPSTREAM_GROUP_BASE "
        "LDAP_UPSTREAM_USER_FILTER LDAP_UPSTREAM_GROUP_FILTER LDAP_UPSTREAM_LOGIN_ATTRIBUTE "
        "LDAP_UPSTREAM_MEMBER_ATTRIBUTE LDAP_REPLICA_GROUPS LDAP_UPSTREAM_STARTTLS "
        "LDAP_UPSTREAM_CACERT LDAP_UPSTREAM_TLS_VERIFY LDAP_UPSTREAM_ALLOW_CLEARTEXT LDAP_REPLICA_INTERVAL "
        "LDAP_REPLICA_ONLY_GROUP_MEMBERS LDAP_UPSTREAM_PAGE_SIZE LDAP_UPSTREAM_TIMEOUT"
    ).split()
]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _replica_env(monkeypatch, **extra):
    values = {
        "LDAP_MODE": "replica",
        "LDAP_UPSTREAM_URI": "ldaps://dc1.corp.example",
        "LDAP_UPSTREAM_BIND_DN": "CN=svc,DC=corp,DC=example",
        "LDAP_UPSTREAM_BIND_PASSWORD": "pw",
        "LDAP_UPSTREAM_BASE_DN": "DC=corp,DC=example",
        **extra,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_with_nothing_set_it_is_a_standalone_directory_with_the_four_groups():
    settings = DirectorySettings.from_env()
    assert settings == DirectorySettings()
    assert settings.mode == STANDALONE and not settings.replica
    assert settings.base_dn == DEFAULT_BASE_DN
    assert settings.groups == DEFAULT_GROUPS
    assert settings.upstream is None


def test_every_standalone_setting_is_read(monkeypatch, tmp_path):
    seed = tmp_path / "people.csv"
    for name, value in {
        "LDAP_BASE_DN": "dc=example,dc=org",
        "LDAP_ORGANISATION": "Example",
        "LDAP_SERVICE_PASSWORD": "svc",
        "LDAP_ADMIN_USER": "Root.Admin",
        "LDAP_ADMIN_PASSWORD": "adm",
        "LDAP_ADMIN_NAME": "The Admin",
        "LDAP_SEED_FILE": str(seed),
        "LDAP_GROUPS": "a, b",
        "LDAP_TLS_CERT_FILE": "/c.crt",
        "LDAP_TLS_KEY_FILE": "/c.key",
        "LDAP_TLS_GENERATE": "false",
        "LDAP_TLS_HOSTNAMES": "dir.example,10.0.0.1",
        "LDAP_TLS_DAYS": "30",
        "LDAP_REQUIRE_TLS": "no",
        "LDAP_LOCKOUT_FAILURES": "3",
        "LDAP_LOCKOUT_SECONDS": "60",
        "LDAP_MIN_PASSWORD_LENGTH": "16",
        "LDAP_LOG_LEVEL": "none",
    }.items():
        monkeypatch.setenv(name, value)
    settings = DirectorySettings.from_env()
    assert settings == DirectorySettings(
        base_dn="dc=example,dc=org",
        organisation="Example",
        service_password="svc",
        admin_user="root.admin",
        admin_password="adm",
        admin_name="The Admin",
        seed_file=str(seed),
        groups=("a", "b"),
        tls_cert_file="/c.crt",
        tls_key_file="/c.key",
        tls_generate=False,
        tls_hostnames=("dir.example", "10.0.0.1"),
        tls_days=30,
        require_tls=False,
        lockout_failures=3,
        lockout_seconds=60,
        min_password_length=16,
        log_level="none",
    )


def test_an_unknown_mode_is_refused(monkeypatch):
    monkeypatch.setenv("LDAP_MODE", "primary")
    with pytest.raises(SettingsError, match="standalone, replica"):
        DirectorySettings.from_env()


def test_a_secret_can_come_from_a_file_which_wins(monkeypatch, tmp_path):
    path = tmp_path / "secret"
    path.write_text("from-file\n")
    monkeypatch.setenv("LDAP_ADMIN_PASSWORD", "from-variable")
    assert secret("LDAP_ADMIN_PASSWORD") == "from-variable"
    monkeypatch.setenv("LDAP_ADMIN_PASSWORD_FILE", str(path))
    assert secret("LDAP_ADMIN_PASSWORD") == "from-file"
    path.write_text("   ")
    assert secret("LDAP_ADMIN_PASSWORD") is None


def test_a_replica_reads_its_primary(monkeypatch):
    _replica_env(monkeypatch)
    settings = DirectorySettings.from_env()
    assert settings.mode == REPLICA and settings.replica
    upstream = settings.upstream
    assert upstream.uri == "ldaps://dc1.corp.example"
    assert upstream.user_base == upstream.group_base == "DC=corp,DC=example"
    assert upstream.flavour == "generic"
    assert upstream.user_filter == FLAVOURS["generic"]["user_filter"]
    assert upstream.groups == {name: name for name in DEFAULT_GROUPS}
    assert upstream.secure


def test_the_active_directory_flavour_sets_its_own_defaults(monkeypatch):
    _replica_env(monkeypatch, LDAP_UPSTREAM_FLAVOUR="AD")
    upstream = UpstreamSettings.from_env()
    assert upstream.login_attribute == "sAMAccountName"
    assert "userAccountControl:1.2.840.113556.1.4.803:=2" in upstream.user_filter
    assert upstream.group_filter == "(objectClass=group)"


def test_every_replica_setting_is_read(monkeypatch, tmp_path):
    password = tmp_path / "pw"
    password.write_text("filed")
    _replica_env(
        monkeypatch,
        LDAP_UPSTREAM_URI="ldap://dir",
        LDAP_UPSTREAM_BIND_PASSWORD_FILE=str(password),
        LDAP_UPSTREAM_FLAVOUR="openldap",
        LDAP_UPSTREAM_USER_BASE="ou=staff,DC=corp,DC=example",
        LDAP_UPSTREAM_GROUP_BASE="ou=teams,DC=corp,DC=example",
        LDAP_UPSTREAM_USER_FILTER="(objectClass=account)",
        LDAP_UPSTREAM_GROUP_FILTER="(objectClass=posixGroup)",
        LDAP_UPSTREAM_LOGIN_ATTRIBUTE="cn",
        LDAP_UPSTREAM_MEMBER_ATTRIBUTE="memberUid",
        LDAP_REPLICA_GROUPS="Analysts=nl2sql-users",
        LDAP_UPSTREAM_STARTTLS="true",
        LDAP_UPSTREAM_CACERT="/ca.pem",
        LDAP_UPSTREAM_TLS_VERIFY="false",
        LDAP_REPLICA_INTERVAL="15",
        LDAP_REPLICA_ONLY_GROUP_MEMBERS="false",
        LDAP_UPSTREAM_PAGE_SIZE="50",
        LDAP_UPSTREAM_TIMEOUT="3",
    )
    upstream = UpstreamSettings.from_env()
    assert upstream == UpstreamSettings(
        uri="ldap://dir",
        bind_dn="CN=svc,DC=corp,DC=example",
        bind_password="filed",
        base_dn="DC=corp,DC=example",
        flavour="openldap",
        user_base="ou=staff,DC=corp,DC=example",
        group_base="ou=teams,DC=corp,DC=example",
        user_filter="(objectClass=account)",
        group_filter="(objectClass=posixGroup)",
        login_attribute="cn",
        member_attribute="memberUid",
        groups={"Analysts": "nl2sql-users"},
        starttls=True,
        cacert="/ca.pem",
        verify=False,
        interval_seconds=15,
        only_group_members=False,
        page_size=50,
        timeout_seconds=3,
    )
    assert upstream.secure, "StartTLS on a plain URI is encrypted"


def test_a_replica_without_its_primary_says_what_is_missing(monkeypatch):
    monkeypatch.setenv("LDAP_MODE", "replica")
    monkeypatch.setenv("LDAP_UPSTREAM_URI", "ldap://dir")
    with pytest.raises(SettingsError) as caught:
        DirectorySettings.from_env()
    message = str(caught.value)
    for name in ("LDAP_UPSTREAM_BIND_DN", "LDAP_UPSTREAM_BASE_DN", "LDAP_UPSTREAM_BIND_PASSWORD"):
        assert name in message
    assert "LDAP_UPSTREAM_URI" not in message


def test_an_unknown_flavour_is_refused(monkeypatch):
    _replica_env(monkeypatch, LDAP_UPSTREAM_FLAVOUR="novell")
    with pytest.raises(SettingsError, match="ad, generic, openldap"):
        UpstreamSettings.from_env()


@pytest.mark.parametrize(
    ("raw", "mapping"),
    [
        (None, {name: name for name in DEFAULT_GROUPS}),
        ("NL2SQL Users=nl2sql-users; ; Admins", {"NL2SQL Users": "nl2sql-users", "Admins": "Admins"}),
        (
            "CN=Reviewers,OU=Groups,DC=corp=nl2sql-reviewers",
            {"CN=Reviewers,OU=Groups,DC=corp": "nl2sql-reviewers"},
        ),
        ("=x;y=", {"=x": "=x", "y=": "y="}),
    ],
    ids=["default", "names", "dn", "half-pairs"],
)
def test_group_mappings(raw, mapping):
    assert group_map(raw, DEFAULT_GROUPS) == mapping


def test_a_directory_needs_both_passwords_before_it_starts():
    assert len(DirectorySettings().problems()) == 2
    assert DirectorySettings(service_password="s", admin_password="a").problems() == []


def test_a_replica_needs_no_first_person():
    upstream = UpstreamSettings(uri="ldaps://d", bind_dn="x", bind_password="y", base_dn="z")
    settings = DirectorySettings(mode=REPLICA, service_password="s", upstream=upstream)
    assert settings.problems() == []


def test_warnings_name_the_risky_settings():
    upstream = UpstreamSettings(
        uri="ldap://d", bind_dn="x", bind_password="y", base_dn="z", verify=False, allow_cleartext=True
    )
    replica = DirectorySettings(mode=REPLICA, upstream=upstream, require_tls=False)
    notes = " ".join(replica.warnings())
    assert "clear text" in notes and "LDAP_UPSTREAM_ALLOW_CLEARTEXT=true" in notes
    assert "not checked" in notes and "unencrypted" in notes
    assert "without limit" in " ".join(DirectorySettings(lockout_failures=0).warnings())
    assert DirectorySettings().warnings() == []
    assert not module.UpstreamSettings(uri="ldap://d", bind_dn="", bind_password="", base_dn="").secure


def test_a_clear_text_primary_is_refused_unless_allowed_by_name(monkeypatch):
    """V6-57. The directory refuses clear-text binds to itself; a replica
    holds its primary to the same rule, so a person's password is never
    passed through in clear without someone having said so."""
    _replica_env(monkeypatch, LDAP_UPSTREAM_URI="ldap://dc1.corp.example", LDAP_SERVICE_PASSWORD="s")
    refused = DirectorySettings.from_env()
    [problem] = refused.problems()
    assert "without StartTLS" in problem and "LDAP_UPSTREAM_ALLOW_CLEARTEXT=true" in problem
    assert not any("clear text" in note for note in refused.warnings()), "a problem, not a warning"
    monkeypatch.setenv("LDAP_UPSTREAM_STARTTLS", "true")
    assert DirectorySettings.from_env().problems() == []
    monkeypatch.setenv("LDAP_UPSTREAM_STARTTLS", "false")
    monkeypatch.setenv("LDAP_UPSTREAM_ALLOW_CLEARTEXT", "true")
    allowed = DirectorySettings.from_env()
    assert allowed.problems() == [] and any("clear text" in note for note in allowed.warnings())
