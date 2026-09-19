"""Principle of least access, checked against a real Postgres.

The agent connects as `nl2sql_reader` (docker/reader_role.sql). These tests
ask the catalog what that role can actually do -- not what the SQL file says
it grants -- and then try the things it must not be able to do. The static
checks in tests/docker/test_dockerfiles.py read the file; only this proves
the cluster agrees with it, which is what matters once a volume has outlived
several image versions.

Opt-in (`pytest --run-docker`). Connects as the reader at POSTGRES_URL and,
for the one test that needs to create a table, as the owner at
POSTGRES_OWNER_URL (defaults: the compose postgres on localhost:5432 with the
credentials from docker/Dockerfile). Skips rather than fails when nothing is
listening.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy
from nl2sql_agent.database import Database
from sqlalchemy import text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.docker

POSTGRES_URL = os.environ.get(
    "POSTGRES_URL", "postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail"
)
POSTGRES_OWNER_URL = os.environ.get(
    "POSTGRES_OWNER_URL", "postgresql+psycopg://nl2sql:nl2sql@localhost:5432/nl2sql_retail"
)

READER = make_url(POSTGRES_URL).username
OWNER = make_url(POSTGRES_OWNER_URL).username

# Every privilege Postgres defines on a table other than SELECT.
TABLE_WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def _engine_or_skip(url: str) -> sqlalchemy.Engine:
    engine = sqlalchemy.create_engine(url)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except sqlalchemy.exc.SQLAlchemyError as exc:
        pytest.skip(f"no reachable Postgres at {url}: {exc}")
    return engine


@pytest.fixture(scope="module")
def reader() -> sqlalchemy.Engine:
    return _engine_or_skip(POSTGRES_URL)


@pytest.fixture(scope="module")
def owner() -> sqlalchemy.Engine:
    return _engine_or_skip(POSTGRES_OWNER_URL)


@pytest.fixture(scope="module")
def public_tables(reader: sqlalchemy.Engine) -> list[str]:
    with reader.connect() as conn:
        names = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        ).scalars().all()
    assert len(names) >= 19, "the retail schema should be loaded before checking its grants"
    return list(names)


# ---------------------------------------------------------------------------
# What the role is
# ---------------------------------------------------------------------------


def test_the_reader_is_a_plain_login_role_with_no_special_attributes(reader):
    with reader.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin "
                "FROM pg_roles WHERE rolname = :name"
            ),
            {"name": READER},
        ).one()
    assert row.rolcanlogin is True
    assert not any([row.rolsuper, row.rolcreatedb, row.rolcreaterole, row.rolreplication, row.rolbypassrls])


def test_the_reader_is_a_member_of_no_other_role(reader):
    """Membership is how a "read-only" role quietly inherits the owner's
    rights. It has none, so its own grants are the whole story.
    """
    with reader.connect() as conn:
        memberships = conn.execute(
            text(
                "SELECT r.rolname FROM pg_auth_members m "
                "JOIN pg_roles r ON r.oid = m.roleid "
                "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = :name"
            ),
            {"name": READER},
        ).scalars().all()
    assert memberships == []


def test_the_reader_owns_nothing(reader):
    with reader.connect() as conn:
        owned = conn.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE r.rolname = :name"
            ),
            {"name": READER},
        ).scalars().all()
    assert owned == []


def test_the_readers_sessions_start_read_only(reader):
    with reader.connect() as conn:
        assert conn.exec_driver_sql("SHOW default_transaction_read_only").scalar() == "on"


# ---------------------------------------------------------------------------
# What the role can do: SELECT on every table, and only that
# ---------------------------------------------------------------------------


def test_the_reader_can_select_from_every_table_in_public(reader, public_tables):
    """Least privilege cuts both ways: the agent needs every table, or a
    question routed to an ungranted one fails in a way that looks like the
    model being wrong.
    """
    with reader.connect() as conn:
        missing = [
            table for table in public_tables
            if not conn.execute(
                text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
                {"role": READER, "table": f'public."{table}"'},
            ).scalar()
        ]
    assert missing == []


@pytest.mark.parametrize("privilege", TABLE_WRITE_PRIVILEGES)
def test_the_reader_holds_no_write_privilege_on_any_table(reader, public_tables, privilege):
    with reader.connect() as conn:
        granted = [
            table for table in public_tables
            if conn.execute(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": READER, "table": f'public."{table}"', "privilege": privilege},
            ).scalar()
        ]
    assert granted == [], f"{READER} has {privilege} on {granted}"


def test_the_reader_cannot_create_objects_in_the_schema(reader):
    with reader.connect() as conn:
        assert conn.execute(
            text("SELECT has_schema_privilege(:role, 'public', 'USAGE')"), {"role": READER}
        ).scalar() is True
        assert conn.execute(
            text("SELECT has_schema_privilege(:role, 'public', 'CREATE')"), {"role": READER}
        ).scalar() is False


def test_the_reader_cannot_advance_any_sequence(reader):
    """Sequences back the surrogate keys. Advancing one is a write in all but
    name, and the reader has no reason to touch them.
    """
    with reader.connect() as conn:
        sequences = conn.execute(
            text("SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'")
        ).scalars().all()
        assert sequences, "the retail schema has serial keys; none found"
        writable = [
            seq for seq in sequences
            if conn.execute(
                text("SELECT has_sequence_privilege(:role, :seq, 'USAGE, UPDATE')"),
                {"role": READER, "seq": f'public."{seq}"'},
            ).scalar()
        ]
    assert writable == []


def test_the_reader_has_exactly_one_kind_of_table_grant(reader):
    """The whole grant list, as the catalog sees it: nothing but SELECT."""
    with reader.connect() as conn:
        kinds = conn.execute(
            text(
                "SELECT DISTINCT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = :role"
            ),
            {"role": READER},
        ).scalars().all()
    assert kinds == ["SELECT"]


# ---------------------------------------------------------------------------
# What happens when it tries anyway
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO dim_store (store_key) VALUES (-1)",
        "UPDATE dim_store SET store_key = store_key WHERE false",
        "DELETE FROM dim_store WHERE false",
        "TRUNCATE dim_store",
        "ALTER TABLE dim_store ADD COLUMN least_privilege_probe int",
        "DROP TABLE dim_store",
        "CREATE TABLE public.least_privilege_probe (a int)",
        "CREATE INDEX least_privilege_probe ON dim_store (store_key)",
        "SELECT nextval('dim_store_store_key_seq')",
        "COPY dim_store TO '/tmp/least_privilege_probe'",
    ],
)
def test_every_kind_of_write_is_refused_by_the_server_not_the_agent(reader, statement):
    """Bypasses all three of the agent's own layers -- the regex, the READ ONLY
    transaction and the role's read-only default -- and issues the statement
    directly in a READ WRITE transaction. Postgres itself has to say no.
    """
    with reader.connect() as conn, conn.begin() as tx:
        conn.exec_driver_sql("SET TRANSACTION READ WRITE")
        with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied|must be owner"):
            conn.exec_driver_sql(statement)
        tx.rollback()


def test_the_reader_cannot_grant_anyone_else_access(reader):
    """A GRANT by a non-owner is only a warning in Postgres, so the check is
    on the effect: PUBLIC still cannot write afterwards.
    """
    with reader.connect() as conn, conn.begin() as tx:
        conn.exec_driver_sql("SET TRANSACTION READ WRITE")
        conn.exec_driver_sql("GRANT INSERT ON dim_store TO PUBLIC")
        assert conn.exec_driver_sql(
            "SELECT has_table_privilege('public', 'public.dim_store', 'INSERT')"
        ).scalar() is False
        tx.rollback()


def test_the_reader_cannot_widen_its_own_access(reader):
    with reader.connect() as conn, conn.begin() as tx:
        conn.exec_driver_sql("SET TRANSACTION READ WRITE")
        with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
            conn.exec_driver_sql(f"ALTER ROLE {READER} CREATEDB")
        tx.rollback()
    with reader.connect() as conn, conn.begin() as tx:
        conn.exec_driver_sql("SET TRANSACTION READ WRITE")
        with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
            conn.exec_driver_sql(f"GRANT {OWNER} TO {READER}")
        tx.rollback()


def test_the_reader_cannot_reach_the_server_filesystem(reader):
    """pg_read_file is superuser-only; a reader with it could read pg_hba.conf
    and the other roles' password hashes.
    """
    with reader.connect() as conn:
        with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
            conn.exec_driver_sql("SELECT pg_read_file('pg_hba.conf')")


# ---------------------------------------------------------------------------
# The default privilege: tables the owner adds later
# ---------------------------------------------------------------------------


def test_a_table_the_owner_creates_later_is_readable_but_not_writable(owner, reader):
    """A regenerated dataset or a new dimension must not need reader_role.sql
    run again. Creates a throwaway table as the owner, checks it from the
    reader's side, and drops it whatever happens.
    """
    table = f"least_privilege_probe_{uuid.uuid4().hex[:8]}"
    with owner.begin() as conn:
        conn.exec_driver_sql(f"CREATE TABLE public.{table} (a int)")
        conn.exec_driver_sql(f"INSERT INTO public.{table} VALUES (1)")
    try:
        with reader.connect() as conn:
            assert conn.exec_driver_sql(f"SELECT a FROM public.{table}").scalar() == 1
        with reader.connect() as conn, conn.begin() as tx:
            conn.exec_driver_sql("SET TRANSACTION READ WRITE")
            with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
                conn.exec_driver_sql(f"DELETE FROM public.{table}")
            tx.rollback()
    finally:
        with owner.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS public.{table}")


# ---------------------------------------------------------------------------
# The agent really is the reader
# ---------------------------------------------------------------------------


def test_the_agents_database_layer_runs_as_the_reader(reader):
    """The whole point: the identity the agent's own Database class carries
    into every query is the reader, and everything it needs still works.
    """
    db = Database(POSTGRES_URL)
    assert db.run_select("SELECT current_user").rows[0][0] == READER
    assert db.run_select("SELECT current_user").rows[0][0] != OWNER
    assert len(db.table_names()) >= 19
    assert db.explain("SELECT count(*) FROM fact_pos_retail_sales") is None
    assert "sample rows" in db.schema_and_samples(["dim_store"], sample_rows=1)
