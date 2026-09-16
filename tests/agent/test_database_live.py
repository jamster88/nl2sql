"""Database against a real Postgres instance: catalog introspection via
pg_catalog COMMENT ON metadata, and -- the part that can't be faked -- proof
that `SET TRANSACTION READ ONLY` actually stops a data-modifying CTE that
`ensure_read_only`'s static check lets through (see test_database_safety.py).

Opt-in (`pytest --run-docker`): connects to whatever Postgres is reachable at
POSTGRES_URL (default: the local docker-compose postgres service on
localhost:5432, credentials nl2sql/nl2sql per docker/Dockerfile). Skips
rather than fails if nothing is listening there -- this test verifies
*behavior against a real server*, it doesn't stand up one of its own.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy
from nl2sql_agent.database import Database, UnsafeQueryError

pytestmark = pytest.mark.docker

POSTGRES_URL = os.environ.get(
    "POSTGRES_URL", "postgresql+psycopg://nl2sql:nl2sql@localhost:5432/nl2sql_retail"
)


@pytest.fixture(scope="module")
def db() -> Database:
    database = Database(POSTGRES_URL)
    try:
        database.table_names()
    except sqlalchemy.exc.SQLAlchemyError as exc:
        pytest.skip(f"no reachable Postgres at {POSTGRES_URL}: {exc}")
    return database


def test_table_names_include_the_known_retail_schema(db: Database):
    names = set(db.table_names())
    assert {"dim_store", "dim_product", "fact_pos_retail_sales"} <= names


def test_describe_all_tables_lists_row_counts(db: Database):
    text = db.describe_all_tables()
    assert "dim_store" in text
    assert "columns:" in text


def test_schema_and_samples_includes_column_types_and_sample_rows(db: Database):
    text = db.schema_and_samples(["dim_store"], sample_rows=2)
    assert "=== dim_store ===" in text
    assert "store_key" in text
    assert "sample rows" in text


def test_run_select_executes_a_real_query(db: Database):
    result = db.run_select("SELECT COUNT(*) AS n FROM dim_store")
    assert result.columns == ["n"]
    assert result.rows[0][0] > 0
    assert result.truncated is False


def test_run_select_respects_max_rows_and_marks_truncated():
    limited = Database(POSTGRES_URL, max_rows=2)
    result = limited.run_select("SELECT * FROM dim_date ORDER BY date_key")
    assert len(result.rows) == 2
    assert result.truncated is True


def test_run_select_rejects_an_obviously_unsafe_statement(db: Database):
    with pytest.raises(UnsafeQueryError):
        db.run_select("DELETE FROM dim_store")


def test_read_only_transaction_stops_a_data_modifying_cte_the_static_check_misses(db: Database):
    """ensure_read_only lets this string through (it starts with WITH) --
    Database.run_select's `SET TRANSACTION READ ONLY` must be what actually
    rejects it, and dim_store must be provably untouched afterward.
    """
    before = db.run_select("SELECT COUNT(*) FROM dim_store").rows[0][0]

    sneaky = "WITH d AS (DELETE FROM dim_store RETURNING *) SELECT * FROM d"
    with pytest.raises(Exception):  # a real driver/DB error, not UnsafeQueryError
        db.run_select(sneaky)

    after = db.run_select("SELECT COUNT(*) FROM dim_store").rows[0][0]
    assert after == before


def test_explain_returns_none_for_a_valid_query(db: Database):
    assert db.explain("SELECT * FROM dim_store") is None


def test_explain_returns_an_error_message_for_an_unknown_table(db: Database):
    error = db.explain("SELECT * FROM this_table_does_not_exist")
    assert error is not None
    assert "this_table_does_not_exist" in error
