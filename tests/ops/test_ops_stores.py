"""nl2sql_ops.stores and .passwords: the runtime stores' server, and the rest."""

from __future__ import annotations

import pytest

from nl2sql_ops import stores
from nl2sql_ops.passwords import set_password
from nl2sql_ops.settings import Store

from .conftest import FakeConn

FEEDBACK = Store("feedback", "feedback", "nl2sql_feedback", "fb-pw", creates_roles=True)
CORRECTIONS = Store("corrections", "corrections", "nl2sql_corrections", "co-pw", vectors=True)


def test_a_new_store_is_an_owner_that_is_no_superuser_and_a_database_only_it_reaches():
    conn = FakeConn()
    stores.ensure_store(conn, FEEDBACK)
    assert conn.statements[1] == 'CREATE ROLE "feedback"'
    assert ('ALTER ROLE "feedback" WITH LOGIN PASSWORD \'fb-pw\' NOSUPERUSER NOCREATEDB CREATEROLE '
            'NOREPLICATION NOBYPASSRLS') in conn.statements
    assert 'ALTER ROLE "feedback" CONNECTION LIMIT 20' in conn.statements
    assert conn.statements[-3:] == [
        'CREATE DATABASE "nl2sql_feedback" OWNER "feedback"',
        'ALTER DATABASE "nl2sql_feedback" OWNER TO "feedback"',
        'REVOKE CONNECT ON DATABASE "nl2sql_feedback" FROM PUBLIC',
    ]


def test_a_store_that_makes_no_roles_may_not_and_one_there_already_is_kept():
    conn = FakeConn({"FROM pg_roles": [(1,)], "FROM pg_database": [(1,)]})
    stores.ensure_store(conn, CORRECTIONS)
    assert not conn.ran("CREATE ROLE") and not conn.ran("CREATE DATABASE")
    assert conn.ran("NOCREATEROLE")


def test_a_store_without_a_password_is_refused():
    with pytest.raises(ValueError, match="the feedback store has no password"):
        stores.ensure_store(FakeConn(), Store("feedback", "feedback", "nl2sql_feedback", None))


def test_the_superusers_databases_are_closed_and_pgvector_is_made_quietly():
    conn = FakeConn()
    stores.close_maintenance(conn)
    stores.ensure_vectors(conn)
    assert conn.statements == [
        'REVOKE CONNECT ON DATABASE "postgres" FROM PUBLIC',
        'REVOKE CONNECT ON DATABASE "template1" FROM PUBLIC',
        "SET client_min_messages = warning",
        "CREATE EXTENSION IF NOT EXISTS vector",
    ]


def test_the_superuser_is_kept_off_the_network(monkeypatch):
    seen = []
    monkeypatch.setattr(stores, "rewrite", lambda conn, begin, end, lines: seen.append((begin, end, lines)) or False)
    assert stores.write_transport_rules(object()) is False
    assert seen == [("# BEGIN nl2sql transport", "# END nl2sql transport", ["host all postgres all reject"])]


def test_a_password_is_set_only_on_a_role_that_exists():
    present = FakeConn({"FROM pg_roles": [(1,)]})
    assert set_password(present, "ragproc", "pw") is True
    assert present.statements[-1] == "ALTER ROLE \"ragproc\" PASSWORD 'pw'"
    absent = FakeConn()
    assert set_password(absent, "ragproc", "pw") is False
    assert not absent.ran("ALTER")
