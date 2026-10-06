"""The one-shot's SQL against a real Postgres: what the fakes cannot say.

A throwaway pgvector server with TLS on (its image's own snakeoil
certificate), so that pg_hba's `hostssl` lines parse the way they do in the
retail database. The functions are called over TCP as the superuser here;
in the stack they run over the socket (`nl2sql_ops.connect`), which changes
how they arrive and nothing about what Postgres makes of them. Proven:

* every statement is one Postgres accepts, and running everything twice is
  the same as running it once;
* the reader reads and writes nothing; the sync can make a person and grant
  them a group, and can read neither revocation list, which the reader may
  only ask about through `session_revoked`;
* pg_hba.conf is rewritten in place, parses, and takes effect on reload;
* a store's owner reaches its own database and no other, makes the roles
  its service makes (CREATEROLE) and nothing beyond them, and the superuser
  is refused over the network once the transport block is in;
* the review service's writer and the snippet loader's reader are made by
  their real code, as owners that are no longer superusers.
"""

from __future__ import annotations

import subprocess
import time
import uuid

import pytest

pytestmark = pytest.mark.docker

psycopg = pytest.importorskip("psycopg")

from nl2sql_common import roles  # noqa: E402
from nl2sql_ops import hba, retail, stores  # noqa: E402
from nl2sql_ops.settings import Store  # noqa: E402

IMAGE = "pgvector/pgvector:pg18"
SUPER = "super-password-1"


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


@pytest.fixture
def server(docker_daemon_available):
    """A fresh server for each test: the transport block shuts the superuser out."""
    if not docker_daemon_available:
        pytest.skip("no Docker daemon")
    name = f"nl2sql-ops-live-{uuid.uuid4().hex[:8]}"
    docker(
        "run", "-d", "--name", name, "-e", f"POSTGRES_PASSWORD={SUPER}", "-p", "127.0.0.1::5432", IMAGE,
        "-c", "ssl=on", "-c", "ssl_cert_file=/etc/ssl/certs/ssl-cert-snakeoil.pem",
        "-c", "ssl_key_file=/etc/ssl/private/ssl-cert-snakeoil.key",
    )
    try:
        for _ in range(60):
            if docker("exec", name, "pg_isready", "-q", "-h", "127.0.0.1", check=False).returncode == 0:
                break
            time.sleep(1)
        port = int(docker("port", name, "5432/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1])

        def connect(user="postgres", password=SUPER, dbname="postgres"):
            return psycopg.connect(host="127.0.0.1", port=port, user=user, password=password, dbname=dbname,
                                   autocommit=True, connect_timeout=5)

        yield name, connect
    finally:
        docker("rm", "-f", name, check=False)


def refused(connect, **kwargs) -> str:
    with pytest.raises(psycopg.Error) as error:
        connect(**kwargs).close()
    return str(error.value)


def retail_database(connect) -> None:
    with connect() as su:
        su.execute("CREATE ROLE nl2sql LOGIN PASSWORD 'owner-pw'")
        su.execute("CREATE DATABASE nl2sql_retail OWNER nl2sql")
    with connect("nl2sql", "owner-pw", "nl2sql_retail") as owner:
        owner.execute("CREATE TABLE fact_pos_retail_sales (id int)")
        owner.execute("INSERT INTO fact_pos_retail_sales VALUES (1)")


def prepare_retail(conn) -> None:
    retail.ensure_extensions(conn)
    retail.ensure_reader(conn, reader="nl2sql_reader", owner="nl2sql", database="nl2sql_retail", password="reader-pw")
    retail.ensure_signin(conn, reader="nl2sql_reader", owner="nl2sql", rolesync="nl2sql_rolesync",
                         password="sync-pw", database="nl2sql_retail")
    retail.write_signin_rules(conn, retail.signin_lines(
        reader="nl2sql_reader", rolesync="nl2sql_rolesync", database="nl2sql_retail", host="nl2sql-ldap",
        port=389, tls="starttls", base_dn="dc=nl2sql,dc=local"))


def test_the_retail_database_is_prepared_and_preparing_it_again_changes_nothing(server):
    _, connect = server
    retail_database(connect)
    with connect(dbname="nl2sql_retail") as su:
        prepare_retail(su)
        rules = su.execute("SELECT count(*) FROM pg_hba_file_rules").fetchone()[0]
        prepare_retail(su)
        assert su.execute("SELECT count(*) FROM pg_hba_file_rules").fetchone()[0] == rules
        assert su.execute("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL").fetchone()[0] == 0
        first = su.execute("SELECT type, database, user_name, auth_method FROM pg_hba_file_rules "
                           "ORDER BY rule_number LIMIT 2").fetchall()
        assert first == [("hostssl", ["all"], ["nl2sql_reader", "nl2sql_rolesync"], "scram-sha-256"),
                         ("hostssl", ["nl2sql_retail"], ["+nl2sql_ldap"], "ldap")]
        config = su.execute("SELECT rolconfig, rolconnlimit FROM pg_roles WHERE rolname = 'nl2sql_reader'").fetchone()
        assert not roles.READER.differ(*config)
        assert su.execute("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'").fetchone()[0] == 1

    with connect("nl2sql_reader", "reader-pw", "nl2sql_retail") as reader:
        assert reader.execute("SELECT count(*) FROM fact_pos_retail_sales").fetchone()[0] == 1
        assert reader.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert reader.execute("SELECT nl2sql_auth.session_revoked('alice', 'j1', 0)").fetchone()[0] is False
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reader.execute("SELECT * FROM nl2sql_auth.revoked_sessions")
    assert "permission denied for database" in refused(connect, user="nl2sql_reader", password="reader-pw")

    with connect("nl2sql_rolesync", "sync-pw", "nl2sql_retail") as sync:
        sync.execute("CREATE ROLE alice LOGIN")
        sync.execute("GRANT nl2sql_ldap, nl2sql_users TO alice")
        sync.execute("INSERT INTO nl2sql_auth.revoked_sessions VALUES ('j1', 'alice', 'signed out', 0, 9e9)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            sync.execute("ALTER ROLE nl2sql_reader PASSWORD 'stolen'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            sync.execute("SELECT count(*) FROM fact_pos_retail_sales")
    with connect("nl2sql_reader", "reader-pw", "nl2sql_retail") as reader:
        assert reader.execute("SELECT nl2sql_auth.session_revoked('alice', 'j1', 0)").fetchone()[0] is True


def test_turning_sign_in_off_takes_its_rules_out(server):
    _, connect = server
    with connect() as su:
        before = su.execute("SELECT count(*) FROM pg_hba_file_rules").fetchone()[0]
        retail.write_signin_rules(su, ["hostssl all nobody all scram-sha-256"])
        assert su.execute("SELECT count(*) FROM pg_hba_file_rules").fetchone()[0] == before + 1
        assert retail.write_signin_rules(su, []) is True
        assert su.execute("SELECT count(*) FROM pg_hba_file_rules").fetchone()[0] == before


def test_a_rule_that_does_not_parse_leaves_the_file_as_it_was(server):
    _, connect = server
    with connect() as su:
        path = su.execute("SELECT current_setting('hba_file')").fetchone()[0]
        old = su.execute("SELECT pg_read_file(%s)", (path,)).fetchone()[0]
        with pytest.raises(hba.HbaError):
            hba.rewrite(su, "# BEGIN t", "# END t", ["hostssl all all nonsense-method"])
        assert su.execute("SELECT pg_read_file(%s)", (path,)).fetchone()[0].rstrip("\n") == old.rstrip("\n")
        assert su.execute("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL").fetchone()[0] == 0


STORES = (
    Store("feedback", "feedback", "nl2sql_feedback", "fb-pw", creates_roles=True),
    Store("corrections", "corrections", "nl2sql_corrections", "co-pw", vectors=True),
    Store("snippets", "snippets", "nl2sql_snippets", "sn-pw", creates_roles=True, vectors=True),
)


def test_each_store_is_its_owners_alone_and_the_superuser_leaves_the_network(server):
    _, connect = server
    with connect() as su:
        for store in STORES:
            stores.ensure_store(su, store)
            stores.ensure_store(su, store)  # again: the same
        stores.close_maintenance(su)
    for store in STORES:
        if store.vectors:
            with connect(dbname=store.database) as su:
                stores.ensure_vectors(su)
    with connect() as su:
        stores.write_transport_rules(su)

    assert "rejects connection" in refused(connect)
    with connect("corrections", "co-pw", "nl2sql_corrections") as owner:
        owner.execute("CREATE TABLE fix (id int, v vector(3))")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            owner.execute("CREATE ROLE sneaky")
        config = owner.execute("SELECT rolconfig, rolconnlimit FROM pg_roles WHERE rolname = 'corrections'").fetchone()
        assert not roles.STORE_OWNER.differ(*config)
    for user, password, database in (("corrections", "co-pw", "nl2sql_feedback"), ("feedback", "fb-pw", "postgres")):
        assert "permission denied for database" in refused(connect, user=user, password=password, dbname=database)


def test_the_review_service_and_the_snippet_loader_make_their_roles_as_owners(server):
    from nl2sql_review import store as feedback_store
    from ragproc import snippets as sn

    _, connect = server
    with connect() as su:
        for store in STORES:
            stores.ensure_store(su, store)
    with connect(dbname="nl2sql_snippets") as su:
        stores.ensure_vectors(su)

    # The service's own connection, rows as dicts and all: a test connection
    # of its own handed back tuples, and passed a grant the service could not
    # make (6.3's acceptance run found it).
    url = connect("feedback", "fb-pw", "nl2sql_feedback").info
    with feedback_store.connect(f"postgresql://feedback:fb-pw@{url.host}:{url.port}/nl2sql_feedback") as owner:
        feedback_store.ensure_schema(owner)
        feedback_store.ensure_writer_role(owner, "writer-pw")
        feedback_store.ensure_writer_role(owner, "writer-pw-2")  # a restart, rotating it
    with connect(feedback_store.WRITER_ROLE, "writer-pw-2", "nl2sql_feedback") as writer:
        assert writer.execute("SELECT count(*) FROM feedback_submissions").fetchone()[0] == 0
        config = writer.execute("SELECT rolconfig, rolconnlimit FROM pg_roles WHERE rolname = current_user").fetchone()
        assert not roles.FEEDBACK_WRITER.differ(*config)

    with connect("snippets", "sn-pw", "nl2sql_snippets") as owner:
        owner.autocommit = False
        sn.ensure_tables(owner)
        owner.commit()
        sn.ensure_reader(owner, "snippets_reader", "reader-pw")
    with connect("snippets_reader", "reader-pw", "nl2sql_snippets") as reader:
        assert reader.execute("SELECT count(*) FROM sql_snippets").fetchone()[0] == 0
        assert not roles.SNIPPETS_READER.differ(
            *reader.execute("SELECT rolconfig, rolconnlimit FROM pg_roles WHERE rolname = current_user").fetchone())
    assert "permission denied for database" in refused(
        connect, user="snippets_reader", password="reader-pw", dbname="nl2sql_feedback")
