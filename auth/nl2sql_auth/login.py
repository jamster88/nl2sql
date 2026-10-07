"""Signing in: a connection to the retail database as the person.

This service never checks a password. It opens a connection to the retail
database with the name and password it was given, and Postgres decides --
by its pg_hba rules, which send a person's password to the directory. So a
successful sign-in means three things at once: the directory accepted the
password (or, for a replica, the primary did), the role sync has made the
person a role, and Postgres let that role in. The same three things decide
whether their SQL can run later.

While connected as them it reads which of the nl2sql roles they hold and the
display name the sync wrote on their role, and that is the session.

Two different failures, kept apart because a client treats them differently:
a password that was refused (401, try again) and a database that could not be
reached at all (503, nobody can sign in right now). Which of two refusals it
was -- no such person, or a wrong password -- is never said: Postgres does not
say either, and a sign-in form that did would be a directory of who has an
account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import psycopg

from nl2sql_identity import ROLES, Identity
from nl2sql_ldap.layout import login_problem, normalise_login

#: SQLSTATEs Postgres answers a refused password with: invalid_password, and
#: invalid_authorization_specification (pg_hba matched nothing for them).
REFUSED = {"28P01", "28000"}

#: What libpq says when it is the server that refused. psycopg reports no
#: SQLSTATE for an error raised while connecting -- only libpq's message --
#: so a refusal is recognised by its words: "password authentication
#: failed", "LDAP authentication failed", "no pg_hba.conf entry".
REFUSED_WORDS = ("authentication failed", "no pg_hba.conf entry")

WHO_SQL = """
SELECT current_user,
       COALESCE(shobj_description(r.oid, 'pg_authid'), ''),
       ARRAY(SELECT g.rolname FROM pg_roles g
             WHERE g.rolname = ANY(%(roles)s) AND pg_has_role(current_user, g.oid, 'MEMBER')
             ORDER BY g.rolname)
FROM pg_roles r
WHERE r.rolname = current_user
"""


class SignInError(Exception):
    """A sign-in that did not happen, with the code a client branches on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


REFUSED_MESSAGE = "that name and password were not accepted"


def display_name(comment: str) -> str:
    """The name the sync wrote on the role: `Alice Smith <alice@x>` -> `Alice Smith`."""
    return comment.split(" <", 1)[0].strip()


@dataclass
class PostgresLogin:
    """Sign people in against one database."""

    host: str
    port: int
    dbname: str
    sslmode: str = "verify-full"
    #: The retail database's own certificate, for verify-ca and verify-full.
    sslrootcert: str | None = None
    timeout: int = 5
    roles: tuple[str, ...] = ROLES
    connect: Callable = psycopg.connect

    def check(self, username: str, password: str) -> Identity:
        name = normalise_login(username)
        if not name or not password or login_problem(name):
            raise SignInError("invalid_credentials", REFUSED_MESSAGE)
        try:
            conn = self.connect(
                host=self.host,
                port=self.port,
                dbname=self.dbname,
                user=name,
                password=password,
                sslmode=self.sslmode,
                **({"sslrootcert": self.sslrootcert} if self.sslrootcert else {}),
                connect_timeout=self.timeout,
                application_name="nl2sql-auth sign-in",
                autocommit=True,
            )
        except psycopg.OperationalError as exc:
            if getattr(exc, "sqlstate", None) in REFUSED or any(words in str(exc) for words in REFUSED_WORDS):
                raise SignInError("invalid_credentials", REFUSED_MESSAGE) from exc
            raise SignInError(
                "sign_in_unavailable", f"cannot reach the retail database to check that: {_first_line(exc)}"
            ) from exc
        with conn:
            user, comment, held = conn.execute(WHO_SQL, {"roles": list(self.roles)}).fetchone()
        return Identity(user=user, name=display_name(comment), roles=frozenset(held))

    def describe(self) -> str:
        return f"{self.host}:{self.port}/{self.dbname} (sslmode={self.sslmode})"


def _first_line(exc: BaseException) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]


def roles_named(names: Iterable[str]) -> tuple[str, ...]:
    """The roles a session may carry: the four, and any a deployment mapped."""
    return tuple(sorted(set(ROLES) | set(names)))
