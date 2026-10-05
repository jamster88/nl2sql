"""Ending a session before it expires (V6-61), without a Postgres.

The lists themselves -- the function a reader may call and the tables it may
not read -- are exercised against a real database in
tests/auth/test_auth_live.py. Here: what is written, when, and what each
route answers when it cannot be.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import psycopg
import pytest

from nl2sql_auth.revocation import (
    CUTOFF_SQL,
    LOCKED,
    LOCKED_FOR_GOOD,
    PASSWORD_CHANGED,
    PASSWORD_SET,
    PURGE_SQL,
    REMOVED,
    REVOKE_SQL,
    SIGNED_OUT,
    Revocations,
    lock_time,
)
from nl2sql_auth.rolesync import RoleSync
from nl2sql_identity import USERS, Guard, GuardSettings, Identity
from nl2sql_identity.tokens import LEEWAY_SECONDS, verify

from .test_auth_app import KEY, NOW, SAME, World
from .test_auth_rolesync import ROLES, FakeConn

HOUR = 3600


class Recorder:
    """A connection that keeps what it was asked."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.fail:
            raise psycopg.OperationalError("connection refused")
        self.asked.append((sql, params))


def _revocations(conn: Recorder, *, now: float = NOW + 0.5) -> tuple[Revocations, list]:
    opened = []

    def connect(conninfo, **options):
        opened.append((conninfo, options))
        return conn

    return Revocations("host=db", session_seconds=HOUR, connect=connect, clock=lambda: now), opened


# --- what is written -----------------------------------------------------------


def test_a_signed_out_session_is_kept_until_it_would_have_expired():
    conn = Recorder()
    revocations, opened = _revocations(conn)
    rita = Identity(user="rita", token_id="j1", issued_at=NOW - 60, expires_at=NOW + 100)
    assert revocations.session(rita) is True
    assert opened == [("host=db", {"autocommit": True, "connect_timeout": 5})]
    assert conn.asked == [
        (REVOKE_SQL, {"jti": "j1", "user": "rita", "reason": SIGNED_OUT, "now": NOW, "expires": NOW + 100 + LEEWAY_SECONDS})
    ]


def test_a_session_with_no_id_or_nobody_in_it_names_nothing_to_end():
    conn = Recorder()
    revocations, opened = _revocations(conn)
    assert revocations.session(Identity(user="rita")) is False
    assert revocations.session(Identity(user="", token_id="j1")) is False
    assert opened == [] and conn.asked == []


def test_one_without_an_expiry_is_kept_for_a_whole_session():
    conn = Recorder()
    revocations, _ = _revocations(conn)
    revocations.session(Identity(user="rita", token_id="j1"))
    assert conn.asked[0][1]["expires"] == NOW + HOUR + LEEWAY_SECONDS


def test_a_cut_off_is_the_next_second_and_kept_a_session_past_it():
    conn = Recorder()
    revocations, _ = _revocations(conn)
    assert revocations.cut_off("rita", PASSWORD_CHANGED) == NOW + 1
    assert conn.asked == [
        (CUTOFF_SQL, {"user": "rita", "not_before": NOW + 1, "reason": PASSWORD_CHANGED,
                      "expires": NOW + 1 + HOUR + LEEWAY_SECONDS})
    ]


def test_a_cut_off_at_a_given_moment_on_a_given_connection_opens_none_of_its_own():
    conn, own = Recorder(), Recorder()
    revocations, opened = _revocations(own)
    assert revocations.cut_off("rita", LOCKED, at=NOW - 30, conn=conn) == NOW - 30
    assert opened == [] and own.asked == [] and conn.asked[0][1]["not_before"] == NOW - 30


def test_a_later_cut_off_wins_and_an_earlier_one_never_moves_it_back():
    assert "GREATEST(c.not_before, EXCLUDED.not_before)" in CUTOFF_SQL
    assert "WHEN EXCLUDED.not_before > c.not_before THEN EXCLUDED.reason" in CUTOFF_SQL


@dataclass
class Person:
    uid: str
    locked: bool = False
    locked_since: str = ""
    groups: tuple = ()


def test_only_locked_people_are_cut_off_from_when_they_were_locked():
    conn = Recorder()
    revocations, _ = _revocations(conn)
    people = [Person("ann"), Person("bob", locked=True, locked_since="20261004120000Z"), Person("cy", locked=True)]
    assert revocations.locked(people, conn=conn) == ["bob", "cy"]
    locked_at = int(dt.datetime(2026, 10, 4, 12, tzinfo=dt.timezone.utc).timestamp())
    assert [(params["user"], params["not_before"], params["reason"]) for _, params in conn.asked] == [
        ("bob", locked_at, LOCKED),
        ("cy", NOW, LOCKED),
    ]


def test_the_purge_forgets_what_refuses_only_expired_tokens():
    conn = Recorder()
    revocations, _ = _revocations(conn)
    revocations.purge()
    assert conn.asked == [(statement, {"now": NOW}) for statement in PURGE_SQL]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20261004121314Z", dt.datetime(2026, 10, 4, 12, 13, 14)),
        ("20261004121314.123456Z", dt.datetime(2026, 10, 4, 12, 13, 14)),
        ("20261004121314,5Z", dt.datetime(2026, 10, 4, 12, 13, 14)),
        ("2026-10-04 12:13:14+00:00", dt.datetime(2026, 10, 4, 12, 13, 14)),
        ("2026-10-04T12:13:14", dt.datetime(2026, 10, 4, 12, 13, 14)),
        (LOCKED_FOR_GOOD, None),
        ("", None),
        ("  ", None),
        ("next tuesday", None),
    ],
)
def test_the_lock_time_is_read_and_anything_else_counts_from_now(value, expected):
    when = lock_time(value, now=NOW)
    assert when == (NOW if expected is None else int(expected.replace(tzinfo=dt.timezone.utc).timestamp()))


# --- the role sync records locks -----------------------------------------------


class FakeLists:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list = []

    def locked(self, people, *, conn=None):
        if self.fail:
            raise psycopg.errors.InsufficientPrivilege("permission denied for schema nl2sql_auth")
        self.calls.append(("locked", [person.uid for person in people], conn))
        return ["bob"]

    def purge(self, *, conn=None):
        self.calls.append(("purge", conn))


def _sync(conn, lists, people):
    return RoleSync(
        rolesync_url="x",
        people=lambda: list(people),
        group_roles=ROLES,
        reader="nl2sql_reader",
        connect=lambda url, **options: conn,
        revocations=lists,
    )


def test_each_sync_ends_locked_peoples_sessions_and_sweeps_the_lists():
    conn, lists = FakeConn(), FakeLists()
    result = _sync(conn, lists, [Person("bob", locked=True)]).run_once()
    assert result.ok and result.locked == ["bob"]
    assert lists.calls == [("locked", ["bob"], conn), ("purge", conn)], "on the sync's own connection"


def test_lists_that_cannot_be_written_are_a_failed_sync():
    result = _sync(FakeConn(), FakeLists(fail=True), []).run_once()
    assert not result.ok
    assert result.errors == ["the revoked-session lists: permission denied for schema nl2sql_auth"]


# --- the routes ------------------------------------------------------------------


class FakeRevocations:
    def __init__(self) -> None:
        self.down = False
        self.sessions: list[Identity] = []
        self.cut: list[tuple[str, str]] = []

    def session(self, identity):
        if self.down:
            raise psycopg.OperationalError("connection refused")
        self.sessions.append(identity)
        return True

    def cut_off(self, user, reason):
        if self.down:
            raise psycopg.OperationalError("connection refused")
        self.cut.append((user, reason))
        return NOW + 1


@pytest.fixture
def lists():
    return FakeRevocations()


@pytest.fixture
def world(lists):
    return World(revocations=lists)


def _cleared(answer) -> bool:
    cookie = answer.headers.get("set-cookie", "")
    return cookie.startswith("nl2sql_session=") and "Max-Age=0" in cookie


def test_signing_out_with_a_token_ends_that_session_everywhere(world, lists):
    headers = world.token("rita")
    session = verify(headers["Authorization"].split()[1], KEY.public_key(), now=NOW)
    answer = world.client.post("/auth/logout", headers=headers)
    assert answer.status_code == 204
    assert [identity.token_id for identity in lists.sessions] == [session.token_id]


def test_signing_out_a_browser_ends_its_session_and_forgets_its_cookie(world, lists):
    world.sign_in("rita")
    answer = world.client.post("/auth/logout", headers=SAME)
    assert answer.status_code == 204 and _cleared(answer)
    assert [identity.user for identity in lists.sessions] == ["rita"]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not.a.token"}], ids=["nothing", "not-ours"])
def test_signing_out_with_nothing_this_service_signed_has_nothing_to_end(world, lists, headers):
    answer = world.client.post("/auth/logout", headers=headers)
    assert answer.status_code == 204 and lists.sessions == []


def test_without_the_lists_signing_out_only_forgets_the_cookie():
    world = World()
    world.sign_in("rita")
    answer = world.client.post("/auth/logout", headers=SAME)
    assert answer.status_code == 204 and _cleared(answer)


def test_a_sign_out_that_cannot_be_recorded_says_so_and_still_forgets_the_cookie(world, lists):
    world.sign_in("rita")
    lists.down = True
    answer = world.client.post("/auth/logout", headers=SAME)
    assert answer.status_code == 503 and _cleared(answer)
    error = answer.json()["error"]
    assert error["code"] == "revocation_unavailable"
    assert "signed out of this browser" in error["message"] and "OperationalError" in error["message"]


def test_changing_your_password_ends_your_sessions_and_gives_this_browser_a_new_one(world, lists):
    world.sign_in("rita")
    answer = world.client.post("/auth/password", json={"current": "a", "new": "b"}, headers=SAME)
    assert answer.status_code == 204
    assert lists.cut == [("rita", PASSWORD_CHANGED)]
    renewed = verify(answer.cookies["nl2sql_session"], KEY.public_key(), now=NOW)
    assert renewed.user == "rita" and renewed.issued_at == NOW + 1, "signed at the cut-off, so not before it"


def test_a_client_with_a_token_signs_in_again_after_changing_its_password(world, lists):
    answer = world.client.post("/auth/password", json={"current": "a", "new": "b"}, headers=world.token("rita"))
    assert answer.status_code == 204 and "set-cookie" not in answer.headers
    assert lists.cut == [("rita", PASSWORD_CHANGED)]


def test_a_password_changed_whose_old_sessions_cannot_be_ended_says_so(world, lists):
    world.sign_in("rita")
    lists.down = True
    answer = world.client.post("/auth/password", json={"current": "a", "new": "b"}, headers=SAME)
    assert answer.status_code == 503
    assert answer.json()["error"]["code"] == "revocation_unavailable"
    assert world.changed_passwords == [("rita", "a", "b")], "the password itself was changed"


def test_without_the_lists_a_password_change_still_happens():
    world = World()
    world.sign_in("rita")
    answer = world.client.post("/auth/password", json={"current": "a", "new": "b"}, headers=SAME)
    assert answer.status_code == 204 and "set-cookie" not in answer.headers


def test_a_password_an_administrator_sets_ends_that_persons_sessions(world, lists):
    world.sign_in("admin")
    answer = world.client.post("/directory/v1/people/rita/password", json={"password": "a-long-password"}, headers=SAME)
    assert answer.status_code == 204 and "set-cookie" not in answer.headers
    assert lists.cut == [("rita", PASSWORD_SET)]


def test_an_administrator_setting_their_own_stays_signed_in_here(world, lists):
    world.sign_in("admin")
    answer = world.client.post("/directory/v1/people/admin/password", json={"password": "a-long-password"}, headers=SAME)
    assert answer.status_code == 204 and lists.cut == [("admin", PASSWORD_SET)]
    assert verify(answer.cookies["nl2sql_session"], KEY.public_key(), now=NOW).issued_at == NOW + 1


def test_someone_removed_has_their_sessions_ended(world, lists):
    world.sign_in("admin")
    assert world.client.delete("/directory/v1/people/rita", headers=SAME).status_code == 204
    assert lists.cut == [("rita", REMOVED)]


def test_an_old_password_stops_working_for_mlflow_at_once(world, lists):
    """The Basic cache remembers a checked password for five minutes; a
    password set since must not ride on it."""
    basic = {"Authorization": "Basic " + __import__("base64").b64encode(b"rita:rita-password").decode()}
    for _ in range(2):
        assert world.client.get("/auth/verify", params={"role": USERS}, headers=basic).status_code == 204
    assert world.login.calls == 1
    world.sign_in("admin")
    world.client.post("/directory/v1/people/rita/password", json={"password": "a-long-password"}, headers=SAME)
    world.client.get("/auth/verify", params={"role": USERS}, headers=basic)
    assert world.login.calls == 3, "checked against Postgres again (the admin's sign-in was the second)"


def test_this_services_own_guard_asks_again_at_once(lists):
    asked = []
    guard = Guard(
        GuardSettings(enabled=True),
        public_key=KEY.public_key(),
        recheck=lambda identity: asked.append(identity.user) or frozenset({USERS}),
        clock=lambda: NOW,
    )
    world = World(revocations=lists, guard=guard)
    headers = world.token("rita")
    for _ in range(2):
        world.client.get("/auth/session", headers=headers)
    assert asked == ["rita"]
    world.client.post("/auth/logout", headers=headers)
    world.client.get("/auth/session", headers=headers)
    assert asked == ["rita", "rita"]


def test_a_sign_in_in_the_second_a_password_was_set_is_not_refused_by_it(world, lists):
    """The cut-off is the next second, and a token counts whole seconds: a
    sign-in straight after is signed at the cut-off, not before it -- and
    once that second has passed, signed as usual."""
    world.sign_in("admin")
    world.client.post("/directory/v1/people/rita/password", json={"password": "a-long-password"}, headers=SAME)
    first = world.client.post("/auth/token", json={"username": "rita", "password": "rita-password"}).json()
    assert verify(first["token"], KEY.public_key(), now=NOW).issued_at == NOW + 1
    assert first["expires_at"] == NOW + 1 + HOUR


def test_a_cut_off_already_passed_is_forgotten_at_the_next_sign_in(lists):
    now = [NOW]
    world = World(revocations=lists, clock=lambda: now[0])
    world.sign_in("admin")
    world.client.post("/directory/v1/people/rita/password", json={"password": "a-long-password"}, headers=SAME)
    now[0] = NOW + 5
    for _ in range(2):
        token = world.client.post("/auth/token", json={"username": "rita", "password": "rita-password"}).json()["token"]
        assert verify(token, KEY.public_key(), now=NOW + 5).issued_at == NOW + 5
