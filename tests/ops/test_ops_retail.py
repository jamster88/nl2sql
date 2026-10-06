"""nl2sql_ops.retail: the reader and sign-in's roles, as Postgres receives them."""

from __future__ import annotations

import pytest

from nl2sql_ops import retail

from .conftest import FakeConn


def test_the_reader_is_made_limited_and_given_select_and_nothing_else():
    conn = FakeConn({"FROM pg_database": [("postgres",), ("template1",)]})
    retail.ensure_reader(conn, reader="nl2sql_reader", owner="nl2sql", database="nl2sql_retail", password="pw-1")
    assert conn.statements[1] == 'CREATE ROLE "nl2sql_reader"', "absent, so made"
    assert ('ALTER ROLE "nl2sql_reader" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
            in conn.statements)
    assert 'ALTER ROLE "nl2sql_reader" PASSWORD \'pw-1\'' in conn.statements
    assert 'ALTER ROLE "nl2sql_reader" SET default_transaction_read_only = on' in conn.statements
    assert 'ALTER ROLE "nl2sql_reader" CONNECTION LIMIT 60' in conn.statements
    assert 'ALTER ROLE "nl2sql_reader" SET "work_mem" = \'16MB\'' in conn.statements
    assert conn.ran("GRANT") == [
        'GRANT CONNECT ON DATABASE "nl2sql_retail" TO "nl2sql_reader"',
        'GRANT USAGE ON SCHEMA public TO "nl2sql_reader"',
        'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "nl2sql_reader"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "nl2sql" IN SCHEMA public GRANT SELECT ON TABLES TO "nl2sql_reader"',
    ]
    assert conn.ran("REVOKE") == [
        'REVOKE CONNECT ON DATABASE "postgres" FROM PUBLIC',
        'REVOKE CONNECT ON DATABASE "template1" FROM PUBLIC',
        "REVOKE EXECUTE ON FUNCTION pg_catalog.pg_cancel_backend(integer) FROM PUBLIC",
        "REVOKE EXECUTE ON FUNCTION pg_catalog.pg_terminate_backend(integer, bigint) FROM PUBLIC",
    ]


def test_a_reader_that_exists_is_not_made_again_and_no_password_leaves_its_own():
    conn = FakeConn({"FROM pg_roles": [(1,)]})
    retail.ensure_reader(conn, reader="nl2sql_reader", owner="nl2sql", database="nl2sql_retail", password=None)
    assert not conn.ran("CREATE ROLE") and not conn.ran("PASSWORD")


def test_pg_trgm_is_created_quietly():
    conn = FakeConn()
    retail.ensure_extensions(conn)
    assert conn.statements == ["SET client_min_messages = warning", "CREATE EXTENSION IF NOT EXISTS pg_trgm"]


def test_sign_in_makes_the_groups_the_sync_and_the_revocation_lists():
    conn = FakeConn({
        "person.rolname <> %(sync)s": [("alice",)],
        "NOT IN (%(sync)s, %(reader)s)": [("bob",)],
    })
    retail.ensure_signin(conn, reader="nl2sql_reader", owner="nl2sql", rolesync="nl2sql_rolesync",
                         password="sync-pw", database="nl2sql_retail")
    for group in ("nl2sql_ldap", "nl2sql_users", "nl2sql_reviewers", "nl2sql_curators", "nl2sql_admins"):
        assert f'CREATE ROLE "{group}" NOLOGIN' in conn.statements
        assert f'ALTER ROLE "{group}" NOLOGIN' in conn.statements
    assert ('GRANT "nl2sql_users" TO "nl2sql_reviewers", "nl2sql_curators", "nl2sql_admins" '
            'WITH INHERIT TRUE, SET FALSE') in conn.statements
    assert ('ALTER ROLE "nl2sql_rolesync" WITH LOGIN PASSWORD \'sync-pw\' CREATEROLE NOSUPERUSER NOCREATEDB '
            'NOREPLICATION NOBYPASSRLS NOINHERIT') in conn.statements
    assert 'ALTER ROLE "nl2sql_rolesync" CONNECTION LIMIT 10' in conn.statements
    assert ('GRANT "nl2sql_ldap", "nl2sql_users", "nl2sql_reviewers", "nl2sql_curators", "nl2sql_admins" '
            'TO "nl2sql_rolesync" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE') in conn.statements
    assert 'GRANT "alice" TO "nl2sql_rolesync" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE' in conn.statements
    assert 'GRANT "bob" TO "nl2sql_reader" WITH INHERIT FALSE, SET TRUE' in conn.statements
    assert 'CREATE ROLE "nl2sql_sessions" NOLOGIN' in conn.statements
    assert conn.ran("CREATE OR REPLACE FUNCTION nl2sql_auth.session_revoked")
    assert conn.statements[-3:] == [
        'GRANT USAGE ON SCHEMA nl2sql_auth TO "nl2sql_reader", "nl2sql_rolesync"',
        'GRANT EXECUTE ON FUNCTION nl2sql_auth.session_revoked(text, text, bigint) TO "nl2sql_reader", "nl2sql_rolesync"',
        'GRANT SELECT, INSERT, UPDATE, DELETE ON nl2sql_auth.revoked_sessions, nl2sql_auth.session_cutoffs '
        'TO "nl2sql_rolesync"',
    ]


def test_the_rules_send_people_to_the_directory_inside_tls_and_services_to_their_passwords():
    lines = retail.signin_lines(reader="nl2sql_reader", rolesync="nl2sql_rolesync", database="nl2sql_retail",
                                host="nl2sql-ldap", port=389, tls="starttls", base_dn="dc=nl2sql,dc=local")
    assert lines == [
        "hostssl all nl2sql_reader,nl2sql_rolesync all scram-sha-256",
        'hostssl nl2sql_retail +nl2sql_ldap all ldap ldapserver=nl2sql-ldap ldapport=389 ldaptls=1 '
        'ldapprefix="uid=" ldapsuffix=",ou=people,dc=nl2sql,dc=local"',
    ]
    ldaps = retail.signin_lines(reader="r", rolesync="s", database="d", host="h", port=636, tls="ldaps", base_dn="b")
    assert "ldapport=636 ldapscheme=ldaps ldapprefix" in ldaps[1]
    plain = retail.signin_lines(reader="r", rolesync="s", database="d", host="h", port=389, tls="none", base_dn="b")
    assert "ldapport=389 ldapprefix" in plain[1]


@pytest.mark.parametrize(
    ("tls", "base", "message"),
    [("tls", "dc=x", "must be starttls, ldaps or none"), ("none", 'dc="x', "cannot contain a double quote")],
)
def test_a_rule_that_would_not_parse_is_refused_before_it_is_written(tls, base, message):
    with pytest.raises(ValueError, match=message):
        retail.signin_lines(reader="r", rolesync="s", database="d", host="h", port=1, tls=tls, base_dn=base)


def test_the_rules_are_the_sign_in_block_and_none_takes_it_out(monkeypatch):
    seen = []
    monkeypatch.setattr(retail, "rewrite", lambda conn, begin, end, lines: seen.append((begin, end, lines)) or True)
    assert retail.write_signin_rules(object(), ["rule"]) is True
    retail.write_signin_rules(object(), [])
    assert seen == [(retail.SIGNIN_BEGIN, retail.SIGNIN_END, ["rule"]), (retail.SIGNIN_BEGIN, retail.SIGNIN_END, [])]
