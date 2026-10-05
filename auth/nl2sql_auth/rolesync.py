"""The directory's people, made roles in the retail database.

Postgres can check a password against a directory (pg_hba's `ldap` method)
but cannot learn who is in one: a role has to exist before anyone can sign in
as it. This makes the roles, on an interval and straight after the web
interface changes someone:

* a person in at least one mapped group gets a login role of their own name,
  a member of `nl2sql_ldap` (which sends their password to the directory),
  of the nl2sql roles their groups map to, and with their display name as
  its comment -- which is where signing in reads it from;
* the agent's reader is granted each such role WITH SET, so a question can
  run as the person who asked it (`SET LOCAL ROLE`);
* a person's own login is read-only and time-limited when they connect
  directly, with psql or a BI tool;
* a person no longer in any mapped group, or no longer in the directory, has
  their role dropped -- or, if Postgres will not drop it, disabled;
* a person the password policy has locked has every session from before the
  lock refused (`revocation.Revocations.locked`), and the revocation lists
  are swept of what refuses only expired tokens.

Only roles this made are touched: they are the members of `nl2sql_ldap`, and
the sync's login holds ADMIN on nothing else. A directory person whose name is
already a role the sync did not make -- `nl2sql`, say -- is reported and left
alone, rather than handed the owner's account.

Each person is a transaction of their own, so one that fails is reported and
the rest still happen.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable

import psycopg
from psycopg import sql

from nl2sql_ldap.layout import login_problem

from .revocation import Revocations

MARKER = "nl2sql_ldap"

#: The people this sync made -- every member of the marker but the sync's own
#: login, which holds ADMIN on it -- with their comment and which of the
#: mapped roles they hold directly.
MANAGED_SQL = """
SELECT person.rolname,
       COALESCE(shobj_description(person.oid, 'pg_authid'), ''),
       ARRAY(SELECT held.rolname
             FROM pg_auth_members grant_
             JOIN pg_roles held ON held.oid = grant_.roleid
             WHERE grant_.member = person.oid AND held.rolname = ANY(%(roles)s))
FROM pg_auth_members marker
JOIN pg_roles person ON person.oid = marker.member
WHERE marker.roleid = %(marker)s::regrole
  AND person.rolname <> current_user
"""

#: Every role name in the cluster, to tell a person the sync may create from
#: a name that is already somebody else's.
NAMES_SQL = "SELECT rolname FROM pg_roles"

#: The people the reader may already become.
READER_SQL = """
SELECT person.rolname
FROM pg_auth_members grant_
JOIN pg_roles person ON person.oid = grant_.roleid
WHERE grant_.member = %(reader)s::regrole AND grant_.set_option
"""


@dataclass(frozen=True)
class Wanted:
    """What one person's role should be."""

    roles: frozenset[str]
    comment: str


@dataclass
class Plan:
    create: dict[str, Wanted] = field(default_factory=dict)
    grant: list[tuple[str, str]] = field(default_factory=list)  # (role, person)
    revoke: list[tuple[str, str]] = field(default_factory=list)
    comment: list[tuple[str, str]] = field(default_factory=list)  # (person, text)
    reader: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.create or self.grant or self.revoke or self.comment or self.reader or self.remove)


@dataclass
class SyncResult:
    at: str
    ok: bool
    people: int = 0
    created: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: People locked by the password policy, whose earlier sessions are refused.
    locked: list[str] = field(default_factory=list)


def comment_for(name: str, mail: str) -> str:
    """The role's comment: what a DBA reads in \\du, and what sign-in shows."""
    return f"{name} <{mail}>" if mail else name


def wanted(people: Iterable, group_roles: dict[str, str]) -> tuple[dict[str, Wanted], list[str]]:
    """Each person who should have a role, and why anyone was left out."""
    found: dict[str, Wanted] = {}
    skipped: list[str] = []
    for person in people:
        roles = frozenset(group_roles[group] for group in person.groups if group in group_roles)
        if not roles:
            continue
        problem = login_problem(person.uid)
        if problem:
            skipped.append(problem)
            continue
        found[person.uid] = Wanted(roles=roles, comment=comment_for(person.name, person.mail))
    return found, skipped


def plan(
    desired: dict[str, Wanted],
    managed: dict[str, tuple[str, frozenset[str]]],
    names: set[str],
    reader_holds: set[str],
) -> Plan:
    """What to change to get from `managed` to `desired`. Pure: no database."""
    result = Plan()
    for person, want in sorted(desired.items()):
        if person not in managed:
            if person in names:
                result.conflicts.append(
                    f"{person} is already a role the sync did not make, so it is left alone"
                )
            else:
                result.create[person] = want
            continue
        comment, held = managed[person]
        result.grant += [(role, person) for role in sorted(want.roles - held)]
        result.revoke += [(role, person) for role in sorted(held - want.roles)]
        if comment != want.comment:
            result.comment.append((person, want.comment))
        if person not in reader_holds:
            result.reader.append(person)
    result.remove = sorted(set(managed) - set(desired))
    return result


class RoleSync:
    """The sync, against one directory and one database."""

    def __init__(
        self,
        *,
        rolesync_url: str,
        people: Callable[[], list],
        group_roles: dict[str, str],
        reader: str,
        statement_timeout_ms: int = 60000,
        connection_limit: int = 5,
        connect: Callable = psycopg.connect,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
        revocations: Revocations | None = None,
    ) -> None:
        self.rolesync_url = rolesync_url
        self.people = people
        self.group_roles = group_roles
        self.reader = reader
        self.statement_timeout_ms = statement_timeout_ms
        self.connection_limit = connection_limit
        self._connect = connect
        self._clock = clock
        self.revocations = revocations
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.last: SyncResult | None = None

    # --- reading -----------------------------------------------------------

    def _managed(self, conn) -> dict[str, tuple[str, frozenset[str]]]:
        rows = conn.execute(MANAGED_SQL, {"roles": sorted(set(self.group_roles.values())), "marker": MARKER})
        return {name: (comment, frozenset(held)) for name, comment, held in rows.fetchall()}

    # --- writing -----------------------------------------------------------

    def _create(self, conn, person: str, want: Wanted) -> None:
        role = sql.Identifier(person)
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                "NOBYPASSRLS CONNECTION LIMIT {}"
            ).format(role, sql.Literal(self.connection_limit))
        )
        conn.execute(sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET FALSE").format(sql.Identifier(MARKER), role))
        for granted in sorted(want.roles):
            self._grant(conn, granted, person)
        self._reader(conn, person)
        conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role))
        conn.execute(
            sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(role, sql.Literal(self.statement_timeout_ms))
        )
        self._comment(conn, person, want.comment)

    @staticmethod
    def _grant(conn, granted: str, person: str) -> None:
        conn.execute(
            sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET FALSE").format(
                sql.Identifier(granted), sql.Identifier(person)
            )
        )

    @staticmethod
    def _revoke(conn, granted: str, person: str) -> None:
        conn.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(granted), sql.Identifier(person)))

    def _reader(self, conn, person: str) -> None:
        conn.execute(
            sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(
                sql.Identifier(person), sql.Identifier(self.reader)
            )
        )

    @staticmethod
    def _comment(conn, person: str, text: str) -> None:
        conn.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(sql.Identifier(person), sql.Literal(text)))

    def _remove(self, conn, person: str) -> str:
        """Drop the role; if Postgres will not, take its login away. Says which."""
        try:
            with conn.transaction():
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(person)))
            return "dropped"
        except psycopg.Error:
            with conn.transaction():
                conn.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(person)))
                for granted in sorted(set(self.group_roles.values())):
                    self._revoke(conn, granted, person)
            return "disabled (Postgres would not drop it)"

    # --- one run -------------------------------------------------------------

    def run_once(self) -> SyncResult:
        with self._lock:
            result = self._run()
            self.last = result
            return result

    def _run(self) -> SyncResult:
        result = SyncResult(at=self._clock().isoformat(), ok=True)
        try:
            people = self.people()
        except Exception as exc:  # noqa: BLE001 - any failure to read is the same answer
            result.ok = False
            result.errors.append(f"reading the directory: {exc}")
            return result
        desired, result.skipped = wanted(people, self.group_roles)
        result.people = len(desired)
        try:
            conn = self._connect(self.rolesync_url, autocommit=True, connect_timeout=5)
        except psycopg.Error as exc:
            result.ok = False
            result.errors.append(f"connecting to the retail database: {exc}")
            return result
        with conn:
            try:
                names = {row[0] for row in conn.execute(NAMES_SQL).fetchall()}
                reader_holds = {row[0] for row in conn.execute(READER_SQL, {"reader": self.reader}).fetchall()}
                changes = plan(desired, self._managed(conn), names, reader_holds)
            except psycopg.Error as exc:
                result.ok = False
                result.errors.append(f"reading the retail database's roles: {_first_line(exc)}")
                return result
            result.conflicts = changes.conflicts
            steps: list[tuple[str, Callable[[], object]]] = []
            for person, want in changes.create.items():
                steps.append((person, lambda p=person, w=want: self._create(conn, p, w)))
            for role, person in changes.grant:
                steps.append((person, lambda r=role, p=person: self._grant(conn, r, p)))
            for role, person in changes.revoke:
                steps.append((person, lambda r=role, p=person: self._revoke(conn, r, p)))
            for person, text in changes.comment:
                steps.append((person, lambda p=person, t=text: self._comment(conn, p, t)))
            for person in changes.reader:
                steps.append((person, lambda p=person: self._reader(conn, p)))
            for person, step in steps:
                try:
                    with conn.transaction():
                        step()
                except psycopg.Error as exc:
                    result.ok = False
                    result.errors.append(f"{person}: {_first_line(exc)}")
                    continue
                target = result.created if person in changes.create else result.changed
                if person not in target:
                    target.append(person)
            for person in changes.remove:
                try:
                    outcome = self._remove(conn, person)
                except psycopg.Error as exc:
                    result.ok = False
                    result.errors.append(f"{person}: {_first_line(exc)}")
                    continue
                result.removed.append(f"{person} ({outcome})")
            if self.revocations is not None:
                try:
                    result.locked = self.revocations.locked(people, conn=conn)
                    self.revocations.purge(conn=conn)
                except psycopg.Error as exc:
                    result.ok = False
                    result.errors.append(f"the revoked-session lists: {_first_line(exc)}")
        return result

    # --- the loop ------------------------------------------------------------

    def trigger(self) -> None:
        """Run the next sync now rather than at the interval."""
        self._wake.set()

    def run_forever(self, stop: threading.Event, interval: float) -> None:
        while not stop.is_set():
            self.run_once()
            self._wake.wait(interval)
            self._wake.clear()


def _first_line(exc: BaseException) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0]
