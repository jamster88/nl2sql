"""The directory's people, made roles in the retail database.

The plan is pure and tested as such; the run is driven against a fake
connection that records the SQL it was sent, rendered the way Postgres
receives it. The real thing -- CREATEROLE, ADMIN, pg_hba -- is in
tests/auth/test_auth_live.py.
"""

from __future__ import annotations

import datetime as dt
import threading
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
import pytest

from nl2sql_auth.rolesync import (
    MANAGED_SQL,
    NAMES_SQL,
    READER_SQL,
    Plan,
    RoleSync,
    Wanted,
    comment_for,
    plan,
    wanted,
)

ROLES = {
    "nl2sql-users": "nl2sql_users",
    "nl2sql-reviewers": "nl2sql_reviewers",
    "nl2sql-admins": "nl2sql_admins",
}


@dataclass
class Person:
    uid: str
    name: str = ""
    mail: str = ""
    groups: tuple = ()


def test_only_people_in_a_mapped_group_are_wanted():
    found, skipped = wanted(
        [
            Person("alice", "Alice", "a@x", ("nl2sql-users", "nl2sql-reviewers", "sales")),
            Person("bob", "Bob", "", ("sales",)),
            Person("Jane Doe", "Jane", "", ("nl2sql-users",)),
        ],
        ROLES,
    )
    assert found == {
        "alice": Wanted(roles=frozenset({"nl2sql_users", "nl2sql_reviewers"}), comment="Alice <a@x>")
    }
    assert len(skipped) == 1 and "not a usable login name" in skipped[0]


def test_the_comment_is_the_name_and_the_mail_when_there_is_one():
    assert comment_for("Alice", "a@x") == "Alice <a@x>"
    assert comment_for("Alice", "") == "Alice"


def test_the_plan_creates_regrants_recomments_repairs_and_removes():
    desired = {
        "alice": Wanted(frozenset({"nl2sql_users", "nl2sql_reviewers"}), "Alice <a@x>"),
        "bob": Wanted(frozenset({"nl2sql_users"}), "Bob"),
        "carol": Wanted(frozenset({"nl2sql_users"}), "Carol"),
        "nl2sql": Wanted(frozenset({"nl2sql_users"}), "Not The Owner"),
    }
    managed = {
        "bob": ("Robert", frozenset({"nl2sql_users", "nl2sql_admins"})),
        "carol": ("Carol", frozenset({"nl2sql_users"})),
        "dave": ("Dave", frozenset({"nl2sql_users"})),
    }
    result = plan(desired, managed, {"bob", "carol", "dave", "nl2sql", "postgres"}, {"bob"})
    assert result.create == {"alice": desired["alice"]}
    assert result.grant == []
    assert result.revoke == [("nl2sql_admins", "bob")]
    assert result.comment == [("bob", "Bob")]
    assert result.reader == ["carol"], "a reader recreated since lost its grant"
    assert result.remove == ["dave"]
    assert result.conflicts == ["nl2sql is already a role the sync did not make, so it is left alone"]
    assert bool(result)


def test_a_plan_with_nothing_to_do_is_false():
    desired = {"carol": Wanted(frozenset({"nl2sql_users"}), "Carol")}
    managed = {"carol": ("Carol", frozenset({"nl2sql_users"}))}
    assert not plan(desired, managed, {"carol"}, {"carol"})
    assert plan(desired, {"carol": ("Carol", frozenset())}, {"carol"}, {"carol"}).grant == [("nl2sql_users", "carol")]
    assert not Plan()


# --- running it against a database ------------------------------------------------


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConn:
    """Answers the three reads; records every write as Postgres would read it."""

    def __init__(self, *, names=(), managed=(), reader=(), fail=(), fail_reads=False):
        self.names = list(names)
        self.managed = list(managed)
        self.reader = list(reader)
        self.fail = fail
        self.fail_reads = fail_reads
        self.statements: list[str] = []
        self.transactions = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    def execute(self, query, params=None):
        if query in (NAMES_SQL, MANAGED_SQL, READER_SQL):
            if self.fail_reads:
                raise psycopg.errors.UndefinedObject('role "nl2sql_reader" does not exist')
            if query is NAMES_SQL:
                return Rows([(name,) for name in self.names])
            if query is READER_SQL:
                assert params == {"reader": "nl2sql_reader"}
                return Rows([(name,) for name in self.reader])
            assert params["marker"] == "nl2sql_ldap"
            return Rows(self.managed)
        text = query.as_string(None)
        if any(word in text for word in self.fail):
            raise psycopg.errors.InsufficientPrivilege(f"permission denied: {text}\nDETAIL: more")
        self.statements.append(text)
        return Rows([])


def _sync(conn=None, people=(), connect=None, **extra) -> RoleSync:
    def opened(url, **options):
        assert url == "postgresql://sync:pw@db/retail" and options == {"autocommit": True, "connect_timeout": 5}
        return conn

    return RoleSync(
        rolesync_url="postgresql://sync:pw@db/retail",
        people=lambda: list(people),
        group_roles=ROLES,
        reader="nl2sql_reader",
        connect=connect or opened,
        clock=lambda: dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc),
        **extra,
    )


def test_a_new_person_is_made_a_limited_read_only_role_the_reader_can_become():
    conn = FakeConn()
    result = _sync(conn, [Person("alice", "Alice", "a@x", ("nl2sql-reviewers", "nl2sql-users"))]).run_once()
    assert result.ok and result.created == ["alice"] and result.people == 1
    assert result.at == "2026-10-03T00:00:00+00:00"
    assert conn.statements == [
        'CREATE ROLE "alice" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5',
        'GRANT "nl2sql_ldap" TO "alice" WITH INHERIT FALSE, SET FALSE',
        'GRANT "nl2sql_reviewers" TO "alice" WITH INHERIT TRUE, SET FALSE',
        'GRANT "nl2sql_users" TO "alice" WITH INHERIT TRUE, SET FALSE',
        'GRANT "alice" TO "nl2sql_reader" WITH INHERIT FALSE, SET TRUE',
        'ALTER ROLE "alice" SET default_transaction_read_only = on',
        'ALTER ROLE "alice" SET statement_timeout = 60000',
        "COMMENT ON ROLE \"alice\" IS 'Alice <a@x>'",
    ]
    assert conn.transactions == 1, "one person, one transaction"


def test_changes_to_existing_people_are_made_and_reported_once_each():
    conn = FakeConn(
        names=["bob", "dave"],
        managed=[("bob", "Robert", ["nl2sql_users", "nl2sql_admins"]), ("dave", "Dave", ["nl2sql_users"])],
        reader=["dave"],
    )
    result = _sync(conn, [Person("bob", "Bob", "", ("nl2sql-users", "nl2sql-reviewers"))]).run_once()
    assert result.changed == ["bob"]
    assert result.removed == ["dave (dropped)"]
    assert conn.statements == [
        'GRANT "nl2sql_reviewers" TO "bob" WITH INHERIT TRUE, SET FALSE',
        'REVOKE "nl2sql_admins" FROM "bob"',
        "COMMENT ON ROLE \"bob\" IS 'Bob'",
        'GRANT "bob" TO "nl2sql_reader" WITH INHERIT FALSE, SET TRUE',
        'DROP ROLE "dave"',
    ]


def test_a_role_postgres_will_not_drop_is_disabled_instead():
    conn = FakeConn(names=["dave"], managed=[("dave", "Dave", ["nl2sql_users"])], fail=("DROP ROLE",))
    result = _sync(conn).run_once()
    assert result.removed == ["dave (disabled (Postgres would not drop it))"]
    assert conn.statements == [
        'ALTER ROLE "dave" NOLOGIN',
        'REVOKE "nl2sql_admins" FROM "dave"',
        'REVOKE "nl2sql_reviewers" FROM "dave"',
        'REVOKE "nl2sql_users" FROM "dave"',
    ]


def test_one_failure_is_reported_and_the_rest_still_happen():
    conn = FakeConn(fail=('"alice"',))
    result = _sync(conn, [Person("alice", groups=("nl2sql-users",)), Person("bob", groups=("nl2sql-users",))]).run_once()
    assert not result.ok
    assert result.errors == ['alice: permission denied: CREATE ROLE "alice" LOGIN NOSUPERUSER NOCREATEDB '
                             "NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5"]
    assert result.created == ["bob"]


def test_a_removal_that_fails_both_ways_is_reported():
    conn = FakeConn(names=["dave"], managed=[("dave", "Dave", [])], fail=("DROP ROLE", "NOLOGIN"))
    result = _sync(conn).run_once()
    assert not result.ok and result.removed == []
    assert result.errors[0].startswith('dave: permission denied: ALTER ROLE "dave" NOLOGIN')


def test_a_name_that_is_already_someone_elses_role_is_reported_not_taken():
    conn = FakeConn(names=["analytics", "postgres"])  # a role somebody made by hand
    result = _sync(conn, [Person("analytics", groups=("nl2sql-users",))]).run_once()
    assert result.conflicts == ["analytics is already a role the sync did not make, so it is left alone"]
    assert conn.statements == []


def test_a_directory_that_cannot_be_read_changes_nothing():
    def broken():
        raise ConnectionError("the directory is down")

    sync = RoleSync(rolesync_url="x", people=broken, group_roles=ROLES, reader="nl2sql_reader")
    result = sync.run_once()
    assert not result.ok and result.errors == ["reading the directory: the directory is down"]
    assert sync.last is result


def test_a_database_that_cannot_be_reached_changes_nothing():
    def unreachable(url, **options):
        raise psycopg.OperationalError("connection refused")

    result = _sync(connect=unreachable).run_once()
    assert not result.ok and result.errors == ["connecting to the retail database: connection refused"]


def test_roles_that_cannot_be_read_change_nothing():
    result = _sync(FakeConn(fail_reads=True), [Person("alice", groups=("nl2sql-users",))]).run_once()
    assert not result.ok
    assert result.errors == ['reading the retail database\'s roles: role "nl2sql_reader" does not exist']


def test_an_error_without_words_is_named_by_its_type():
    conn = FakeConn()

    def blank(query, params=None):
        if query in (NAMES_SQL, MANAGED_SQL, READER_SQL):
            return Rows([])
        raise psycopg.errors.InsufficientPrivilege("")

    conn.execute = blank
    result = _sync(conn, [Person("alice", groups=("nl2sql-users",))]).run_once()
    assert result.errors == ["alice: InsufficientPrivilege"]


def test_the_limits_on_a_persons_own_login_come_from_the_settings():
    conn = FakeConn()
    _sync(conn, [Person("alice", groups=("nl2sql-users",))], statement_timeout_ms=5000, connection_limit=2).run_once()
    assert conn.statements[0].endswith("CONNECTION LIMIT 2")
    assert 'ALTER ROLE "alice" SET statement_timeout = 5000' in conn.statements


def test_the_loop_runs_until_stopped_and_can_be_woken_early():
    sync = _sync(FakeConn())
    runs = []
    stop = threading.Event()

    def once():
        runs.append(1)
        if len(runs) == 2:
            stop.set()  # as the service's shutdown does: stop, then wake it
        sync.trigger()

    sync.run_once = once
    sync.run_forever(stop, interval=3600)
    assert len(runs) == 2


def test_the_default_clock_is_utc():
    sync = RoleSync(rolesync_url="x", people=list, group_roles=ROLES, reader="r")
    assert sync._clock().tzinfo is dt.timezone.utc


@pytest.fixture(autouse=True)
def _no_real_connections(monkeypatch):
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("real connect")))
