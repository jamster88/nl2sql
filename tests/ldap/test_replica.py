"""Copying a primary into this directory.

The primary is a second in-memory server, shaped like the directories this
has to copy from: an Active Directory with nested groups, a disabled account
and mixed-case logins; an OpenLDAP with `memberUid` posix groups. The live
copy, from one real slapd to another, is in tests/ldap/test_ldap_image.py.
"""

from __future__ import annotations

import datetime as dt
import json
import threading

import pytest
from ldap3 import MOCK_SYNC, Connection, Server
from ldap3.core.exceptions import LDAPException

from nl2sql_ldap import replica as module
from nl2sql_ldap.directory import Directory, DirectoryError
from nl2sql_ldap.replica import (
    Changes,
    RemotePerson,
    ReplicaError,
    Replicator,
    Snapshot,
    Upstream,
    apply,
    dn_key,
    status_entry,
)
from nl2sql_ldap.settings import FLAVOURS, UpstreamSettings

from .conftest import store_password

CORP = "DC=corp,DC=example"


def _ad_settings(**overrides) -> UpstreamSettings:
    values = dict(
        uri="ldaps://dc1.corp.example",
        bind_dn=f"CN=svc,{CORP}",
        bind_password="pw",
        base_dn=CORP,
        flavour="ad",
        user_base=CORP,
        group_base=CORP,
        user_filter="(&(objectClass=user)(!(description=disabled)))",
        group_filter="(objectClass=group)",
        login_attribute="sAMAccountName",
        member_attribute="member",
        groups={"NL2SQL Users": "nl2sql-users", f"CN=NL2SQL Reviewers,OU=Groups,{CORP}": "nl2sql-reviewers"},
    )
    values.update(overrides)
    return UpstreamSettings(**values)


@pytest.fixture
def primary():
    """An AD-shaped primary: people, a nested group, a cycle, a disabled account."""
    server = Server("primary")
    seed = Connection(server, user=f"CN=svc,{CORP}", password="pw", client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(f"CN=svc,{CORP}", {"userPassword": "pw", "sn": "svc"})
    seed.bind()

    def user(cn, login, **extra):
        seed.add(
            f"CN={cn},OU=Staff,{CORP}",
            ["top", "person", "organizationalPerson", "user"],
            {"cn": cn, "sAMAccountName": login, **extra},
        )

    def group(cn, members, ou="Groups"):
        seed.add(f"CN={cn},OU={ou},{CORP}", ["top", "group"], {"cn": cn, "member": members})

    user("Alice Smith", "ASmith", sn="Smith", givenName="Alice", mail="alice@corp.example", displayName="Alice S.")
    user("Bob Jones", "bjones")
    user("Carol King", "carol")
    user("Dave Disabled", "dave", description="disabled")
    user("Jane Doe", "Jane Doe")  # cannot be a login: skipped
    staff = lambda cn: f"CN={cn},OU=Staff,{CORP}"  # noqa: E731
    group("Engineering", [staff("Carol King"), f"CN=Platform,OU=Teams,{CORP}"], ou="Teams")
    group("Platform", [staff("Bob Jones"), f"CN=Engineering,OU=Teams,{CORP}"], ou="Teams")  # a cycle
    group("NL2SQL Users", [staff("Alice Smith"), staff("Dave Disabled"), staff("Jane Doe")])
    group("NL2SQL Reviewers", [f"CN=Engineering,OU=Teams,{CORP}", f"CN=Nobody Here,{CORP}"])
    return server


def _connect(server):
    def connect(settings):
        conn = Connection(server, user=settings.bind_dn, password=settings.bind_password, client_strategy=MOCK_SYNC)
        conn.bind()
        return conn

    return connect


def test_a_dn_key_ignores_case_and_spacing():
    assert dn_key("CN=Alice Smith, OU=Staff,DC=corp") == dn_key("cn=alice smith,ou=staff,dc=CORP")
    assert dn_key("cn=a\\,b,dc=x") != dn_key("cn=a,cn=b,dc=x")
    assert dn_key("not a dn") == ("not a dn",)


def test_the_mirrored_groups_and_their_people_are_read_through_nesting(primary):
    snapshot = Upstream(_ad_settings(), connect=_connect(primary)).read()
    assert sorted(snapshot.people) == ["asmith", "bjones", "carol"]
    assert snapshot.groups == {"nl2sql-users": {"asmith"}, "nl2sql-reviewers": {"bjones", "carol"}}
    alice = snapshot.people["asmith"]
    assert alice.dn == f"CN=Alice Smith,OU=Staff,{CORP}"
    assert alice.attributes == {
        "cn": "Alice Smith",
        "sn": "Smith",
        "givenName": "Alice",
        "mail": "alice@corp.example",
        "displayName": "Alice S.",
    }
    assert snapshot.missing_groups == []
    assert any("'jane doe' is not a usable login name" in reason for reason in snapshot.skipped)


def test_two_people_with_one_login_keep_one_and_report_the_other(primary):
    """Which one the primary lists first is its business; one of them is kept."""
    seed = _connect(primary)(_ad_settings())
    seed.add(f"CN=Alice Again,OU=Staff,{CORP}", ["top", "user"], {"cn": "Alice Again", "sAMAccountName": "asmith"})
    snapshot = Upstream(_ad_settings(only_group_members=False), connect=_connect(primary)).read()
    kept = snapshot.people["asmith"].dn
    assert kept in (f"CN=Alice Smith,OU=Staff,{CORP}", f"CN=Alice Again,OU=Staff,{CORP}")
    assert [reason for reason in snapshot.skipped if "asmith is already" in reason] == [
        next(reason for reason in snapshot.skipped if reason.endswith(f"asmith is already {kept}"))
    ]


def test_everyone_the_filter_matches_can_be_copied_instead(primary):
    snapshot = Upstream(_ad_settings(only_group_members=False), connect=_connect(primary)).read()
    assert sorted(snapshot.people) == ["asmith", "bjones", "carol"], "the disabled account is still left out"


def test_a_group_the_primary_does_not_have_is_copied_empty_and_reported(primary):
    settings = _ad_settings(groups={"NL2SQL Admins": "nl2sql-admins", "NL2SQL Users": "nl2sql-users"})
    snapshot = Upstream(settings, connect=_connect(primary)).read()
    assert snapshot.groups["nl2sql-admins"] == set()
    assert snapshot.missing_groups == ["NL2SQL Admins"]


def test_a_person_without_a_login_is_skipped(primary):
    seed = _connect(primary)(_ad_settings())
    seed.add(f"CN=No Login,OU=Staff,{CORP}", ["top", "user"], {"cn": "No Login"})
    snapshot = Upstream(_ad_settings(only_group_members=False), connect=_connect(primary)).read()
    assert any("has no sAMAccountName" in reason for reason in snapshot.skipped)


def test_posix_groups_name_their_members_by_login():
    server = Server("posix")
    seed = Connection(server, user="cn=svc,dc=x", password="pw", client_strategy=MOCK_SYNC)
    seed.strategy.add_entry("cn=svc,dc=x", {"userPassword": "pw", "sn": "s"})
    seed.bind()
    seed.add("uid=erin,ou=people,dc=x", ["inetOrgPerson"], {"uid": "erin", "cn": "Erin", "sn": "E"})
    seed.add("cn=analysts,ou=groups,dc=x", ["posixGroup"], {"cn": "analysts", "memberUid": ["ERIN", "ghost"]})
    settings = UpstreamSettings(
        uri="ldap://x",
        bind_dn="cn=svc,dc=x",
        bind_password="pw",
        base_dn="dc=x",
        user_base="dc=x",
        group_base="dc=x",
        user_filter=FLAVOURS["generic"]["user_filter"].replace("person", "inetOrgPerson"),
        group_filter="(objectClass=posixGroup)",
        member_attribute="memberUid",
        groups={"analysts": "nl2sql-users"},
    )
    snapshot = Upstream(settings, connect=_connect(server)).read()
    assert snapshot.groups == {"nl2sql-users": {"erin"}}


def test_nesting_stops_at_a_depth_limit(primary, monkeypatch):
    monkeypatch.setattr(module, "MAX_NESTING", 0)
    snapshot = Upstream(_ad_settings(), connect=_connect(primary)).read()
    assert snapshot.groups["nl2sql-reviewers"] == set(), "nested people are past a limit of zero"


def test_referrals_are_not_entries():
    """AD hands out referrals to its DNS zones beside the entries."""

    class Conn:
        class extend:
            class standard:
                @staticmethod
                def paged_search(*args, **kwargs):
                    return [
                        {"type": "searchResEntry", "dn": "CN=a", "attributes": {}},
                        {"type": "searchResRef", "uri": ["ldap://DomainDnsZones.corp.example/DC=x"]},
                    ]

    found = Upstream(_ad_settings())._search(Conn(), CORP, "(x=*)", ["cn"])
    assert [entry["dn"] for entry in found] == ["CN=a"]


def test_an_unreachable_or_failing_primary_is_a_replica_error(primary):
    def unreachable(settings):
        raise LDAPException("connection refused")

    with pytest.raises(ReplicaError, match="cannot reach ldaps://dc1.corp.example: connection refused"):
        Upstream(_ad_settings(), connect=unreachable).read()

    upstream = Upstream(_ad_settings(), connect=_connect(primary))

    def broken(*args, **kwargs):
        raise LDAPException("size limit")

    upstream._search = broken
    with pytest.raises(ReplicaError, match="reading ldaps://dc1.corp.example: size limit"):
        upstream.read()


# --- making the local tree say it ------------------------------------------


def _snapshot(people: dict[str, dict], groups: dict[str, set[str]]) -> Snapshot:
    return Snapshot(
        people={
            login: RemotePerson(dn=f"CN={login},{CORP}", login=login, attributes=attributes)
            for login, attributes in people.items()
        },
        groups=groups,
    )


@pytest.fixture
def local(directory) -> Directory:
    directory.ensure_base("nl2sql")
    return directory


def test_a_first_copy_adds_people_and_groups(local, layout):
    changes = apply(
        local,
        _snapshot(
            {"alice": {"cn": "Alice Smith", "sn": "Smith", "mail": "a@x"}, "bob": {"cn": "", "sn": ""}},
            {"nl2sql-users": {"alice", "bob", "stranger"}, "nl2sql-reviewers": {"alice"}},
        ),
    )
    assert changes.added == ["alice", "bob"] and changes.groups == ["nl2sql-reviewers", "nl2sql-users"]
    assert bool(changes)
    alice, bob = local.people()
    assert (alice.cn, alice.mail, alice.upstream) == ("Alice Smith", "a@x", f"CN=alice,{CORP}")
    assert (bob.cn, bob.sn) == ("bob", "bob"), "an entry still needs both"
    assert local.groups() == {"nl2sql-users": {"alice", "bob"}, "nl2sql-reviewers": {"alice"}}


def test_a_later_copy_updates_removes_and_regroups(local):
    apply(local, _snapshot({"alice": {"mail": "a@x"}, "bob": {}}, {"nl2sql-users": {"alice", "bob"}, "old": {"bob"}}))
    changes = apply(local, _snapshot({"alice": {"mail": "alice@x", "cn": "Alice"}}, {"nl2sql-users": {"alice"}}))
    assert changes == Changes(updated=["alice"], removed=["bob"], groups=["old"])
    assert local.person("alice").mail == "alice@x"
    assert local.groups() == {"nl2sql-users": {"alice"}}
    assert not apply(local, _snapshot({"alice": {"mail": "alice@x", "cn": "Alice"}}, {"nl2sql-users": {"alice"}}))


def test_an_attribute_the_primary_dropped_is_dropped_here(local):
    apply(local, _snapshot({"alice": {"mail": "a@x"}}, {}))
    apply(local, _snapshot({"alice": {}}, {}))
    assert local.person("alice").mail == ""


def test_a_primary_that_suddenly_holds_nobody_is_not_believed(local):
    apply(local, _snapshot({"alice": {}}, {"nl2sql-users": {"alice"}}))
    with pytest.raises(ReplicaError, match="would remove all 1 people here"):
        apply(local, _snapshot({}, {"nl2sql-users": set()}))
    assert [person.uid for person in local.people()] == ["alice"]
    apply(local, _snapshot({}, {"nl2sql-users": set()}), allow_empty=True)
    assert local.people() == []


def test_an_empty_primary_copied_into_an_empty_directory_is_fine(local):
    assert not apply(local, _snapshot({}, {}))


def test_the_status_entry_is_written_and_rewritten(local, layout):
    status_entry(local, {"ok": True, "people": 1})
    status_entry(local, {"ok": False, "error": "x"})
    local.conn.search(layout.replica_status, "(objectClass=*)", search_scope="BASE", attributes=["description"])
    assert json.loads(local.conn.response[0]["attributes"]["description"][0]) == {"error": "x", "ok": False}


# --- the loop ----------------------------------------------------------------


class FakeUpstream:
    def __init__(self, *snapshots):
        self.snapshots = list(snapshots)

    def read(self):
        found = self.snapshots.pop(0)
        if isinstance(found, Exception):
            raise found
        return found


def _replicator(local, upstream, logs, monkeypatch, allow_empty=""):
    monkeypatch.setenv("LDAP_REPLICA_ALLOW_EMPTY", allow_empty)
    return Replicator(
        _ad_settings(interval_seconds=0),
        lambda: local,
        upstream=upstream,
        clock=lambda: dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc),
        log=logs.append,
    )


def _status(local, layout):
    local.conn.search(layout.replica_status, "(objectClass=*)", search_scope="BASE", attributes=["description"])
    return json.loads(local.conn.response[0]["attributes"]["description"][0])


def test_a_good_copy_records_what_it_found(local, layout, monkeypatch):
    logs: list[str] = []
    snapshot = _snapshot({"alice": {}}, {"nl2sql-users": {"alice"}, "nl2sql-admins": set()})
    snapshot.missing_groups = ["NL2SQL Admins"]
    snapshot.skipped = ["x"] * 30
    assert _replicator(local, FakeUpstream(snapshot), logs, monkeypatch).run_once()
    status = _status(local, layout)
    assert status["ok"] and status["people"] == 1
    assert status["groups"] == {"nl2sql-admins": 0, "nl2sql-users": 1}
    assert status["at"] == "2026-10-03T00:00:00+00:00"
    assert len(status["skipped"]) == module.STATUS_LIMIT and status["skipped_count"] == 30
    assert logs[0].startswith("replica: 1 added, 0 updated, 0 removed")
    assert logs[1] == "replica: the primary has no group NL2SQL Admins, so its copy is empty"


def test_a_copy_that_changes_nothing_logs_nothing(local, layout, monkeypatch):
    logs: list[str] = []
    snapshot = _snapshot({}, {})
    assert _replicator(local, FakeUpstream(snapshot), logs, monkeypatch).run_once()
    assert logs == []


@pytest.mark.parametrize(
    "failure",
    [ReplicaError("cannot reach ldaps://dc1"), DirectoryError("refused", "creating x: refused")],
    ids=["primary", "local"],
)
def test_a_failed_copy_is_recorded_and_logged_and_changes_nothing(local, layout, monkeypatch, failure):
    logs: list[str] = []
    assert not _replicator(local, FakeUpstream(failure), logs, monkeypatch).run_once()
    status = _status(local, layout)
    assert status["ok"] is False and status["error"] == str(failure)
    assert logs == [f"replica: copy from ldaps://dc1.corp.example failed: {failure}"]


def test_allow_empty_comes_from_the_environment(local, layout, monkeypatch):
    apply(local, _snapshot({"alice": {}}, {}))
    logs: list[str] = []
    assert _replicator(local, FakeUpstream(_snapshot({}, {})), logs, monkeypatch, allow_empty="true").run_once()
    assert local.people() == []


def test_run_forever_copies_until_told_to_stop(local, monkeypatch):
    copies: list[int] = []
    replicator = _replicator(local, FakeUpstream(), [], monkeypatch)
    stop = threading.Event()

    def once():
        copies.append(1)
        if len(copies) == 3:
            stop.set()
        return True

    replicator.run_once = once
    replicator.run_forever(stop)
    assert len(copies) == 3


def test_the_default_upstream_and_clock(local):
    replicator = Replicator(_ad_settings(), lambda: local)
    assert isinstance(replicator._upstream, Upstream)
    assert replicator._clock().tzinfo is dt.timezone.utc


# --- connecting to the primary -------------------------------------------------


class Recorder:
    def __init__(self):
        self.calls: list = []


def test_connecting_starts_tls_when_asked_and_checks_the_certificate(monkeypatch, tmp_path):
    seen = Recorder()
    ca = tmp_path / "ca.pem"
    ca.write_text("ldap3 checks that the file is there; nothing here reads it")

    class FakeServer:
        def __init__(self, uri, **options):
            seen.calls.append(("server", uri, options))

    class FakeConnection:
        def __init__(self, server, **options):
            seen.calls.append(("connection", options))

        def open(self):
            seen.calls.append("open")

        def start_tls(self):
            seen.calls.append("start_tls")

        def bind(self):
            seen.calls.append("bind")

    monkeypatch.setattr(module, "Server", FakeServer)
    monkeypatch.setattr(module, "Connection", FakeConnection)
    settings = _ad_settings(uri="ldap://dc1", starttls=True, cacert=str(ca), timeout_seconds=4)
    module.connect_upstream(settings)
    (_, uri, server_options), (_, connection_options), *steps = seen.calls
    assert uri == "ldap://dc1" and server_options["use_ssl"] is False
    tls = server_options["tls"]
    assert tls.validate == module.ssl.CERT_REQUIRED and tls.ca_certs_file == str(ca)
    assert connection_options["read_only"] and not connection_options["auto_referrals"]
    assert connection_options["raise_exceptions"] and connection_options["receive_timeout"] == 4
    assert steps == ["open", "start_tls", "bind"]

    seen.calls.clear()
    module.connect_upstream(_ad_settings(verify=False))
    (_, uri, server_options), _, *steps = seen.calls
    assert server_options["use_ssl"] is True and server_options["tls"].validate == module.ssl.CERT_NONE
    assert steps == ["open", "bind"]


def test_the_password_stand_in_matches_the_seam(local):
    assert store_password(local.conn, local.layout.base_dn, "x") is True


def test_attribute_values_are_read_whatever_shape_and_case_they_arrive_in():
    assert module._values({"Member": ["a", "b"]}, "member") == ["a", "b"]
    assert module._values({"mail": "a@x"}, "MAIL") == ["a@x"]
    assert module._values({"mail": None}, "mail") == []
    assert module._values({}, "mail") == []
