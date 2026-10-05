"""Ending a session before it expires (V6-61).

A session is a signed token, good for `AUTH_SESSION_HOURS` wherever it is
presented, and until 6.2 nothing could end one sooner: signing out deleted a
browser's cookie and left every copy of the token working, and a password
changed because it leaked left the sessions signed in with it. Two lists in
the retail database end them now (`docker/auth_roles.sql`):

* a session signed out, by its `jti`;
* a person's cut-off -- every session they signed in before it -- when their
  password is changed or set, their account is locked, or they are removed.

This service writes both, as the role sync's login; every service's guard
asks `nl2sql_auth.session_revoked` about the session in front of it once a
minute (`nl2sql_identity.postgres`). A row is kept until every token it could
refuse has expired anyway, and the role sync sweeps it then.

Time is whole seconds, the resolution a token's `iat` has. A cut-off is the
second after the change, so a session signed in during the second the
password changed is refused with the rest; the session this service issues
in its place is signed at the cut-off itself, which it is not before.
"""

from __future__ import annotations

import datetime as dt
import time
from contextlib import contextmanager
from typing import Callable, Iterable, Iterator

import psycopg

from nl2sql_identity import Identity
from nl2sql_identity.tokens import LEEWAY_SECONDS

#: Why a session ended, as the lists record it.
SIGNED_OUT = "signed out"
PASSWORD_CHANGED = "password changed"
PASSWORD_SET = "password set by an administrator"
LOCKED = "account locked"
REMOVED = "removed from the directory"

REVOKE_SQL = """
INSERT INTO nl2sql_auth.revoked_sessions (jti, username, reason, revoked_at, expires_at)
VALUES (%(jti)s, %(user)s, %(reason)s, %(now)s, %(expires)s)
ON CONFLICT (jti) DO NOTHING
"""

#: A later cut-off replaces an earlier one; an earlier one never moves a
#: later one back, so a lock re-recorded on every sync cannot undo a
#: password change made since.
CUTOFF_SQL = """
INSERT INTO nl2sql_auth.session_cutoffs AS c (username, not_before, reason, expires_at)
VALUES (%(user)s, %(not_before)s, %(reason)s, %(expires)s)
ON CONFLICT (username) DO UPDATE
SET reason = CASE WHEN EXCLUDED.not_before > c.not_before THEN EXCLUDED.reason ELSE c.reason END,
    not_before = GREATEST(c.not_before, EXCLUDED.not_before),
    expires_at = GREATEST(c.expires_at, EXCLUDED.expires_at)
"""

PURGE_SQL = (
    "DELETE FROM nl2sql_auth.revoked_sessions WHERE expires_at < %(now)s",
    "DELETE FROM nl2sql_auth.session_cutoffs WHERE expires_at < %(now)s",
)

#: The password policy's lock with no end, which only an administrator sets
#: or clears (RFC draft-behera-ldap-password-policy, `pwdAccountLockedTime`).
LOCKED_FOR_GOOD = "000001010000Z"


def lock_time(value: str, *, now: int) -> int:
    """When the password policy locked an account, in seconds since 1970.

    `pwdAccountLockedTime` is LDAP generalized time, `20261004121314Z` or
    with a fraction. A lock with no end, or a value this cannot read, counts
    from `now`: nobody can sign in while it holds, so a cut-off at the
    moment it was seen refuses exactly the sessions from before it.
    """
    text = value.strip()
    if not text or text == LOCKED_FOR_GOOD:
        return now
    digits = text.rstrip("Zz").split(".", 1)[0].split(",", 1)[0]
    try:
        moment = dt.datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        # As ldap3 renders it when it reads the schema (`get_info=ALL`).
        try:
            moment = dt.datetime.fromisoformat(text)
        except ValueError:
            return now
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
    return int(moment.timestamp())


class Revocations:
    """The two lists, written through the role sync's login."""

    def __init__(
        self,
        conninfo: str,
        *,
        session_seconds: int,
        connect: Callable = psycopg.connect,
        timeout_seconds: int = 5,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.conninfo = conninfo
        self.session_seconds = session_seconds
        self._connect = connect
        self._timeout = timeout_seconds
        self._clock = clock

    @contextmanager
    def _connection(self, conn=None) -> Iterator:
        if conn is not None:
            yield conn
            return
        with self._connect(self.conninfo, autocommit=True, connect_timeout=self._timeout) as fresh:
            yield fresh

    def session(self, identity: Identity, reason: str = SIGNED_OUT) -> bool:
        """Refuse this one session from now on. False when it names nothing to refuse."""
        if not identity.token_id or not identity.user:
            return False
        now = int(self._clock())
        expires = (identity.expires_at or now + self.session_seconds) + LEEWAY_SECONDS
        with self._connection() as conn:
            conn.execute(
                REVOKE_SQL,
                {"jti": identity.token_id, "user": identity.user, "reason": reason, "now": now, "expires": expires},
            )
        return True

    def cut_off(self, user: str, reason: str, *, at: int | None = None, conn=None) -> int:
        """Refuse every session `user` signed in before `at` -- by default,
        before the next second. Says the cut-off."""
        not_before = int(self._clock()) + 1 if at is None else int(at)
        expires = not_before + self.session_seconds + LEEWAY_SECONDS
        with self._connection(conn) as connection:
            connection.execute(
                CUTOFF_SQL, {"user": user, "not_before": not_before, "reason": reason, "expires": expires}
            )
        return not_before

    def locked(self, people: Iterable, *, conn=None) -> list[str]:
        """Cut off everyone the password policy has locked, from when it did. Says who."""
        now = int(self._clock())
        found = []
        for person in people:
            if getattr(person, "locked", False):
                self.cut_off(person.uid, LOCKED, at=lock_time(getattr(person, "locked_since", ""), now=now), conn=conn)
                found.append(person.uid)
        return found

    def purge(self, *, conn=None) -> None:
        """Forget what refuses only tokens that have expired anyway."""
        now = int(self._clock())
        with self._connection(conn) as connection:
            for statement in PURGE_SQL:
                connection.execute(statement, {"now": now})
