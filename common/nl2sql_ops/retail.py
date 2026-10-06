"""The retail database: the agent's reader, and the roles sign-in hangs from.

Run as the superuser on every start, because a volume made by an older
image keeps whatever roles it had. Everything here is idempotent, nothing
drops anything, and a person's role is never touched -- what a person
holds is the role sync's to decide (`nl2sql_auth.rolesync`).

These were `docker/reader_role.sql` and `docker/auth_roles.sql`, piped into
psql by the shell scripts; the SQL is the same, its `\\gexec` and `\\if`
are Python here, and the passwords are arguments rather than environment
variables read with `\\getenv`. `reader_role.sql` stays for the dataset
image's build (`docker/init_db.sh`), which makes the role before any
one-shot runs.
"""

from __future__ import annotations

from psycopg import sql

from nl2sql_common.roles import READER, ROLESYNC, limit_role

from .hba import rewrite

#: The roles sign-in is built from. `nl2sql_ldap` marks every person the
#: sync made, and pg_hba sends exactly its members to the directory;
#: `nl2sql_users` may read the retail tables; reviewers, curators and
#: administrators are each members of it, so anyone who may do those may ask.
MARKER = "nl2sql_ldap"
USERS = "nl2sql_users"
GROUPS = ("nl2sql_reviewers", "nl2sql_curators", "nl2sql_admins")
SESSIONS = "nl2sql_sessions"

SIGNIN_BEGIN = "# BEGIN nl2sql sign-in"
SIGNIN_END = "# END nl2sql sign-in"


def _exists(conn, role: str) -> bool:
    return conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is not None


def _create(conn, role: str, *options: str) -> None:
    if not _exists(conn, role):
        conn.execute(sql.SQL("CREATE ROLE {} " + " ".join(options)).format(sql.Identifier(role)))


def ensure_extensions(conn) -> None:
    """pg_trgm, for the literal matcher's trigram search."""
    conn.execute("SET client_min_messages = warning")
    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def ensure_reader(conn, *, reader: str, owner: str, database: str, password: str | None) -> None:
    """The agent's read-only role: SELECT on every table in public, nothing else.

    With no password the role's is left as it is. The two revokes at the end
    take from PUBLIC what reading the dataset never needs: connecting to the
    cluster's other databases -- the reader keeps CONNECT here by its own
    grant -- and signalling other sessions, which Postgres otherwise lets any
    role do to backends of the same role, and every reader session is one.
    """
    role = sql.Identifier(reader)
    _create(conn, reader)
    conn.execute(sql.SQL(
        "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    ).format(role))
    if password:
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(role, sql.Literal(password)))
    # A default a session can switch off, not a boundary: the grants are.
    conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role))
    limit_role(conn, reader, READER)
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), role))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(role))
    # Tables the owner makes later are readable too, without running this again.
    conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(
        sql.Identifier(owner), role))
    others = conn.execute(
        "SELECT datname FROM pg_database WHERE datname <> %s AND datallowconn", (database,)
    ).fetchall()
    for (name,) in others:
        conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
    conn.execute("REVOKE EXECUTE ON FUNCTION pg_catalog.pg_cancel_backend(integer) FROM PUBLIC")
    conn.execute("REVOKE EXECUTE ON FUNCTION pg_catalog.pg_terminate_backend(integer, bigint) FROM PUBLIC")


#: The revoked-session lists (V6-61): a session's `jti` when it is signed
#: out, and a person's cut-off, which refuses every session they signed in
#: before it. Owned by `nl2sql_sessions`, which never logs in; the sync may
#: read and write them, the reader may only ask `session_revoked()` about one
#: session. Seconds since 1970, the clock the tokens are signed with.
REVOCATION_DDL = """
CREATE SCHEMA IF NOT EXISTS nl2sql_auth AUTHORIZATION nl2sql_sessions;
ALTER SCHEMA nl2sql_auth OWNER TO nl2sql_sessions;
REVOKE ALL ON SCHEMA nl2sql_auth FROM PUBLIC;
CREATE TABLE IF NOT EXISTS nl2sql_auth.revoked_sessions (
    jti         text PRIMARY KEY,
    username    text NOT NULL,
    reason      text NOT NULL,
    revoked_at  bigint NOT NULL,
    expires_at  bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS nl2sql_auth.session_cutoffs (
    username    text PRIMARY KEY,
    not_before  bigint NOT NULL,
    reason      text NOT NULL,
    expires_at  bigint NOT NULL
);
ALTER TABLE nl2sql_auth.revoked_sessions OWNER TO nl2sql_sessions;
ALTER TABLE nl2sql_auth.session_cutoffs OWNER TO nl2sql_sessions;
CREATE OR REPLACE FUNCTION nl2sql_auth.session_revoked(username text, jti text, issued_at bigint)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT EXISTS (SELECT 1 FROM nl2sql_auth.revoked_sessions r WHERE r.jti = session_revoked.jti)
        OR EXISTS (SELECT 1 FROM nl2sql_auth.session_cutoffs c
                   WHERE c.username = session_revoked.username
                     AND session_revoked.issued_at < c.not_before)
$$;
ALTER FUNCTION nl2sql_auth.session_revoked(text, text, bigint) OWNER TO nl2sql_sessions;
REVOKE ALL ON FUNCTION nl2sql_auth.session_revoked(text, text, bigint) FROM PUBLIC;
"""


def ensure_signin(conn, *, reader: str, owner: str, rolesync: str, password: str, database: str) -> None:
    """The group roles, the sync's login, and the revoked-session lists."""
    for group in (MARKER, USERS, *GROUPS):
        _create(conn, group, "NOLOGIN")
        # Group roles never log in, whatever someone did to them by hand.
        conn.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(group)))
    users = sql.Identifier(USERS)
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), users))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(users))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(users))
    conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(
        sql.Identifier(owner), users))
    # Inherited, so the SELECT reaches them; SET FALSE, so holding one of
    # these is not a way to become nl2sql_users -- nothing needs to.
    conn.execute(sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET FALSE").format(
        users, sql.SQL(", ").join(sql.Identifier(group) for group in GROUPS)))

    # The sync's login: CREATEROLE, and ADMIN on the five above and nothing
    # else -- Postgres lets a CREATEROLE role change only roles it holds
    # ADMIN on, so it can make and remove people but not touch the owner,
    # the reader, or any role it did not make. Neither inherited nor
    # SET-able: its login reads no data and cannot become any of them.
    sync = sql.Identifier(rolesync)
    _create(conn, rolesync)
    conn.execute(sql.SQL(
        "ALTER ROLE {} WITH LOGIN PASSWORD {} CREATEROLE NOSUPERUSER NOCREATEDB NOREPLICATION NOBYPASSRLS NOINHERIT"
    ).format(sync, sql.Literal(password)))
    limit_role(conn, rolesync, ROLESYNC)
    conn.execute(sql.SQL("GRANT {} TO {} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE").format(
        sql.SQL(", ").join(sql.Identifier(role) for role in (MARKER, USERS, *GROUPS)), sync))

    # The people the sync made belong to the sync whatever the grantor of
    # record: a sync role recreated by a superuser would otherwise find every
    # person the old one made beyond its reach. Only where it is missing --
    # made again, a grant is a second membership with another grantor.
    for (person,) in conn.execute("""
        SELECT person.rolname FROM pg_auth_members marker
        JOIN pg_roles person ON person.oid = marker.member
        WHERE marker.roleid = %(marker)s::regrole AND person.rolname <> %(sync)s
          AND NOT EXISTS (SELECT 1 FROM pg_auth_members held
                          WHERE held.roleid = person.oid AND held.member = %(sync)s::regrole AND held.admin_option)
    """, {"marker": MARKER, "sync": rolesync}).fetchall():
        conn.execute(sql.SQL("GRANT {} TO {} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE").format(
            sql.Identifier(person), sync))
    # And the reader may become any of them for one transaction: that is how
    # a question runs as the person who asked it. The sync grants it as it
    # makes each person; this repairs it for a reader recreated since.
    for (person,) in conn.execute("""
        SELECT person.rolname FROM pg_auth_members marker
        JOIN pg_roles person ON person.oid = marker.member
        WHERE marker.roleid = %(marker)s::regrole AND person.rolname NOT IN (%(sync)s, %(reader)s)
          AND NOT EXISTS (SELECT 1 FROM pg_auth_members held
                          WHERE held.roleid = person.oid AND held.member = %(reader)s::regrole)
    """, {"marker": MARKER, "sync": rolesync, "reader": reader}).fetchall():
        conn.execute(sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(
            sql.Identifier(person), sql.Identifier(reader)))

    _create(conn, SESSIONS, "NOLOGIN")
    conn.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(SESSIONS)))
    conn.execute(REVOCATION_DDL)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA nl2sql_auth TO {}, {}").format(sql.Identifier(reader), sync))
    conn.execute(sql.SQL(
        "GRANT EXECUTE ON FUNCTION nl2sql_auth.session_revoked(text, text, bigint) TO {}, {}"
    ).format(sql.Identifier(reader), sync))
    conn.execute(sql.SQL(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON nl2sql_auth.revoked_sessions, nl2sql_auth.session_cutoffs TO {}"
    ).format(sync))


def signin_lines(*, reader: str, rolesync: str, database: str, host: str, port: int, tls: str, base_dn: str) -> list[str]:
    """pg_hba's sign-in rules: the service roles on passwords, people on LDAP.

    `hostssl`, both: pg_hba's `ldap` method is clear-text password
    authentication -- the client sends the person's directory password to
    the server, which binds to the directory with it -- so it is accepted
    only inside TLS. The first line is there because `+role` matches
    indirect members, and the reader and the sync are both: without it their
    passwords would go to a directory that has never heard of them.
    """
    if tls not in ("starttls", "ldaps", "none"):
        raise ValueError(f"NL2SQL_LDAP_TLS must be starttls, ldaps or none, not {tls}")
    if '"' in base_dn:
        raise ValueError(f"LDAP_BASE_DN cannot contain a double quote: {base_dn}")
    scheme = {"starttls": " ldaptls=1", "ldaps": " ldapscheme=ldaps", "none": ""}[tls]
    return [
        f"hostssl all {reader},{rolesync} all scram-sha-256",
        f"hostssl {database} +{MARKER} all ldap ldapserver={host} ldapport={port}{scheme} "
        f'ldapprefix="uid=" ldapsuffix=",ou=people,{base_dn}"',
    ]


def write_signin_rules(conn, lines: list[str]) -> bool:
    """The sign-in block, or with no lines its removal -- all turning sign-in
    off takes: without it people could still sign in to the database with
    their directory passwords while every service had stopped asking."""
    return rewrite(conn, SIGNIN_BEGIN, SIGNIN_END, lines)
