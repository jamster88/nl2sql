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
    "POSTGRES_URL", "postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail"
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


def test_an_empty_schema_yields_no_tables(db: Database):
    """The early return when the catalog query matches nothing -- otherwise
    the follow-up column query would run with an empty name list.
    """
    empty = Database(POSTGRES_URL, db_schema="schema_that_does_not_exist")
    assert empty.table_names() == []
    assert empty.describe_all_tables() == ""


def test_the_reader_role_cannot_write_even_with_the_read_only_default_switched_off():
    """The agent's role (docker/reader_role.sql) is the layer under both of
    the others: `ensure_read_only` is a regex, `SET TRANSACTION READ ONLY` is
    per transaction and the role's read-only default is a session setting.
    Grants are what hold when all three are bypassed.
    """
    engine = sqlalchemy.create_engine(POSTGRES_URL)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except sqlalchemy.exc.SQLAlchemyError as exc:
        pytest.skip(f"no reachable Postgres at {POSTGRES_URL}: {exc}")

    for statement in (
        "DELETE FROM dim_store WHERE false",
        "CREATE TABLE public.reader_role_probe (a int)",
        "SELECT setval('dim_store_store_key_seq', 1)",
    ):
        with engine.connect() as conn, conn.begin() as tx:
            # Overrides the role's read-only default for this transaction.
            conn.exec_driver_sql("SET TRANSACTION READ WRITE")
            with pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"):
                conn.exec_driver_sql(statement)
            tx.rollback()


# ---------------------------------------------------------------------------
# arch5: what the answer contract reads, against the real schema
# ---------------------------------------------------------------------------


def test_the_catalog_carries_the_key_constraints_the_label_map_is_read_from(db: Database):
    tables = {t.name: t for t in db.catalog()}
    assert len(tables) == 19
    assert "PRIMARY KEY (product_key)" in tables["dim_product"].constraints
    assert "UNIQUE (sku_id)" in tables["dim_product"].constraints


def test_the_real_label_map_names_every_dimension_but_the_date_and_the_ad_channel(db: Database):
    from nl2sql_agent.contract import build_label_map

    labels = build_label_map(db.catalog())
    assert labels.tables == [
        "dim_ad_placement", "dim_allowance_type", "dim_competitor", "dim_geography",
        "dim_product", "dim_promo_calendar", "dim_promotion", "dim_store", "dim_vendor",
    ]
    assert labels.for_key("sku_id").label == "product_name"
    assert labels.for_key("store_id").label == "store_name"
    assert labels.for_key("vendor_key").label == "vendor_name"
    assert labels.for_key("date_key") is None
    assert labels.for_key("channel_id") is None


def test_the_latest_complete_fiscal_year_is_the_last_one_the_sales_cover(db: Database):
    from datetime import date

    year, first, last = db.latest_complete_fiscal_year()
    assert year == 2025
    assert (first, last) == (date(2024, 4, 1), date(2025, 3, 31))
    # Complete means its last day has sales: nothing is sold after it.
    latest_sale = db.run_select("SELECT MAX(sales_date_key) FROM fact_pos_retail_sales").rows[0][0]
    assert latest_sale >= int(last.strftime("%Y%m%d"))


def test_a_schema_without_the_sales_calendar_raises_for_the_contract_to_absorb(db: Database):
    """No dim_date, no default period: `contract.load_resources` records the
    error and the run goes on without one (see test_contract.py)."""
    empty = Database(POSTGRES_URL, db_schema="pg_catalog")
    with pytest.raises(sqlalchemy.exc.SQLAlchemyError):
        empty.latest_complete_fiscal_year()
