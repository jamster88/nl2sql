"""Reaching the directory from the auth service."""

from __future__ import annotations

import json

import pytest
from ldap3 import MODIFY_REPLACE
from ldap3.core.exceptions import LDAPSocketOpenError

from nl2sql_auth import access
from nl2sql_auth.access import DirectoryUnavailable, PasswordRefused
from nl2sql_auth.settings import AuthSettings
from nl2sql_ldap.directory import DirectoryError
from nl2sql_ldap.layout import Layout

from ..ldap.conftest import mock_connection, store_password  # noqa: F401 - the shared in-memory directory


class Recorded:
    def __init__(self):
        self.calls: list = []


@pytest.fixture
def fakes(monkeypatch):
    seen = Recorded()
    state = {"bind": True, "fail": None, "result": {"description": "invalidCredentials"}}

    class FakeServer:
        def __init__(self, url, **options):
            self.ssl = options["use_ssl"]
            seen.calls.append(("server", url, options))

    class FakeConnection:
        def __init__(self, server, **options):
            seen.calls.append(("connection", options))
            self.result = state["result"]

        def open(self):
            if state["fail"]:
                raise state["fail"]
            seen.calls.append("open")

        def start_tls(self):
            seen.calls.append("start_tls")

        def bind(self):
            seen.calls.append("bind")
            return state["bind"]

    monkeypatch.setattr(access, "Server", FakeServer)
    monkeypatch.setattr(access, "Connection", FakeConnection)
    return seen, state


def test_the_service_connects_with_starttls_verified_against_the_directorys_certificate(fakes, tmp_path):
    seen, _ = fakes
    ca = tmp_path / "ldap.crt"
    ca.write_text("present")
    settings = AuthSettings(ldap_service_password="svc", ldap_cacert=str(ca), ldap_timeout=3)
    access.connect(settings)
    (_, url, server), (_, connection), *steps = seen.calls
    assert url == "ldap://nl2sql-ldap:389" and server["use_ssl"] is False
    assert server["tls"].validate == access.ssl.CERT_REQUIRED and server["tls"].ca_certs_file == str(ca)
    assert connection["user"] == "cn=nl2sql-auth,ou=services,dc=nl2sql,dc=local"
    assert connection["password"] == "svc" and connection["receive_timeout"] == 3
    assert steps == ["open", "start_tls", "bind"]


def test_ldaps_needs_no_starttls_and_a_missing_certificate_falls_back_to_the_system_store(fakes, tmp_path):
    seen, _ = fakes
    access.connect(AuthSettings(ldap_url="ldaps://dir:636", ldap_cacert=str(tmp_path / "missing.crt")))
    (_, _, server), _, *steps = seen.calls
    assert server["use_ssl"] is True and server["tls"].ca_certs_file is None
    assert steps == ["open", "bind"]
    seen.calls.clear()
    access.connect(AuthSettings(ldap_cacert=None, ldap_starttls=False))
    assert seen.calls[2:] == ["open", "bind"]


def test_a_person_binds_as_themselves(fakes):
    seen, _ = fakes
    access.connect(AuthSettings(), user="uid=alice,ou=people,dc=nl2sql,dc=local", password="pw")
    connection = seen.calls[1][1]
    assert connection["user"] == "uid=alice,ou=people,dc=nl2sql,dc=local" and connection["password"] == "pw"


def test_a_directory_that_cannot_be_reached_is_unavailable(fakes):
    _, state = fakes
    state["fail"] = LDAPSocketOpenError("connection refused")
    with pytest.raises(DirectoryUnavailable, match="cannot reach the directory at ldap://nl2sql-ldap:389"):
        access.connect(AuthSettings())


def test_a_refused_bind_is_the_services_problem_or_the_persons(fakes):
    _, state = fakes
    state["bind"] = False
    with pytest.raises(DirectoryUnavailable, match="refused the auth service's account \\(invalidCredentials\\)"):
        access.connect(AuthSettings())
    with pytest.raises(PasswordRefused):
        access.connect(AuthSettings(), user="uid=a,ou=people,dc=x", password="wrong")
    state["result"] = None
    with pytest.raises(DirectoryUnavailable, match="\\(refused\\)"):
        access.connect(AuthSettings())


def test_the_directory_is_lent_for_a_block_and_closed_after():
    conn = mock_connection()
    unbound = []
    conn.unbind = lambda: unbound.append(True)
    with access.directory(AuthSettings(), opener=lambda settings: conn) as found:
        assert found.layout.base_dn == "dc=nl2sql,dc=local" and found.conn is conn
    assert unbound == [True]


class Standard:
    def __init__(self, ok):
        self.ok = ok
        self.calls = []

    def modify_password(self, **kwargs):
        self.calls.append(kwargs)
        return self.ok


class PasswordConn:
    def __init__(self, ok, result=None):
        self.extend = type("Extend", (), {"standard": Standard(ok)})()
        self.result = result
        self.unbound = False

    def unbind(self):
        self.unbound = True


def test_changing_your_own_password_binds_as_you_and_sends_both_passwords():
    conn = PasswordConn(True)
    opened = {}

    def opener(settings, **kwargs):
        opened.update(kwargs)
        return conn

    access.change_own_password(AuthSettings(), "alice", "old-pw", "new-pw", opener=opener)
    assert opened == {"user": "uid=alice,ou=people,dc=nl2sql,dc=local", "password": "old-pw"}
    assert conn.extend.standard.calls == [{"old_password": "old-pw", "new_password": "new-pw"}]
    assert conn.unbound


@pytest.mark.parametrize(
    ("result", "code", "message"),
    [
        ({"description": "constraintViolation", "message": "Password fails quality checking policy"},
         "constraintViolation", "Password fails quality checking policy"),
        (None, "refused", "the directory refused the new password"),
    ],
)
def test_a_new_password_the_directory_refuses_says_why(result, code, message):
    conn = PasswordConn(False, result)
    with pytest.raises(DirectoryError) as caught:
        access.change_own_password(AuthSettings(), "alice", "old", "new", opener=lambda s, **k: conn)
    assert caught.value.code == code and str(caught.value) == message
    assert conn.unbound


def test_the_replicas_status_is_read_when_there_is_one(directory, layout):
    directory.ensure_base("nl2sql")
    assert access.replica_status(directory) is None
    directory.ensure(layout.replica_status, ["top", "organizationalRole"], {"cn": "replica-status"})
    assert access.replica_status(directory) is None, "no description yet"
    for text, expected in ((json.dumps({"ok": True}), {"ok": True}), ("not json", None)):
        directory.conn.modify(layout.replica_status, {"description": [(MODIFY_REPLACE, [text])]})
        assert access.replica_status(directory) == expected


@pytest.fixture
def directory():
    from nl2sql_ldap.directory import Directory

    return Directory(mock_connection(), Layout("dc=nl2sql,dc=local"), set_password=store_password)


@pytest.fixture
def layout():
    return Layout("dc=nl2sql,dc=local")
