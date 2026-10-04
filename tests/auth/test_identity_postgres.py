"""Asking Postgres what someone holds now, without a Postgres.

The live version -- real roles, a real `pg_has_role` -- is in
tests/auth/test_auth_live.py; this pins the questions asked and what each
answer is turned into.
"""

from __future__ import annotations

import pytest

from nl2sql_identity.guard import ROLES
from nl2sql_identity.postgres import EXISTS_SQL, ROLES_SQL, membership_lookup, plain_url


class FakeConnection:
    def __init__(self, exists, roles):
        self.exists = exists
        self.roles = roles
        self.asked: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.asked.append((sql, params))
        return self

    def fetchone(self):
        return self.exists

    def fetchall(self):
        return [(name,) for name in self.roles]


def _lookup(exists, roles=()):
    connection = FakeConnection(exists, roles)
    opened = []

    def connect(dsn, **options):
        opened.append((dsn, options))
        return connection

    lookup = membership_lookup("postgresql+psycopg://r:p@db:5432/retail", connect=connect)
    return lookup, connection, opened


@pytest.mark.parametrize(
    ("url", "plain"),
    [
        ("postgresql+psycopg://r:p@db/x", "postgresql://r:p@db/x"),
        ("postgresql://r:p@db/x", "postgresql://r:p@db/x"),
        ("host=db dbname=x", "host=db dbname=x"),
    ],
)
def test_a_sqlalchemy_url_becomes_one_libpq_reads(url, plain):
    assert plain_url(url) == plain


def test_the_roles_held_are_returned(monkeypatch):
    lookup, connection, opened = _lookup((True,), ["nl2sql_users", "nl2sql_reviewers"])
    assert lookup("alice") == frozenset({"nl2sql_users", "nl2sql_reviewers"})
    assert opened == [("postgresql://r:p@db:5432/retail", {"connect_timeout": 5, "autocommit": True})]
    assert connection.asked == [
        (EXISTS_SQL, {"user": "alice"}),
        (ROLES_SQL, {"user": "alice", "roles": sorted(ROLES)}),
    ]


@pytest.mark.parametrize("exists", [None, (False,)], ids=["gone", "cannot-log-in"])
def test_a_user_who_is_gone_or_cannot_sign_in_is_none(exists):
    lookup, connection, _ = _lookup(exists)
    assert lookup("alice") is None
    assert len(connection.asked) == 1, "pg_has_role raises for a role that does not exist"
