"""Asking Postgres which nl2sql roles someone holds now.

The directory is synced into the retail database as roles (the auth
service's `rolesync`), so the database is where a service asks whether the
person behind a session is still a reviewer -- not the directory, which a
replica may be minutes behind and a service has no credential for anyway.

Any role can ask: `pg_roles` and `pg_has_role` are readable by everyone, so
the read-only connection a service already holds is enough.
"""

from __future__ import annotations

from typing import Callable, Iterable

import psycopg

from .guard import ROLES

#: Whether the user is still a role that can sign in. Asked first because
#: `pg_has_role` raises rather than answering for a role that is gone.
EXISTS_SQL = "SELECT rolcanlogin FROM pg_roles WHERE rolname = %(user)s"

#: Which of the nl2sql roles the user is a member of, directly or through
#: another (a reviewer is a member of nl2sql_users through nl2sql_reviewers).
ROLES_SQL = """
SELECT r.rolname
FROM pg_roles r
WHERE r.rolname = ANY(%(roles)s)
  AND pg_has_role(%(user)s, r.oid, 'MEMBER')
"""


def plain_url(url: str) -> str:
    """A libpq URL from a SQLAlchemy one: the agent's carry `+psycopg`."""
    scheme, sep, rest = url.partition("://")
    return f"{scheme.split('+', 1)[0]}{sep}{rest}" if sep else url


def membership_lookup(
    url: str,
    roles: Iterable[str] = ROLES,
    *,
    connect: Callable = psycopg.connect,
    timeout_seconds: int = 5,
) -> Callable[[str], frozenset[str] | None]:
    """A `Guard` recheck: the roles `user` holds, or None when they are gone."""
    known = sorted(roles)
    dsn = plain_url(url)

    def lookup(user: str) -> frozenset[str] | None:
        with connect(dsn, connect_timeout=timeout_seconds, autocommit=True) as conn:
            row = conn.execute(EXISTS_SQL, {"user": user}).fetchone()
            if row is None or not row[0]:
                return None
            found = conn.execute(ROLES_SQL, {"user": user, "roles": known}).fetchall()
        return frozenset(name for (name,) in found)

    return lookup
