"""strip_sql / ensure_read_only: the static half of the agent's read-only
safety net (the other half is `SET TRANSACTION READ ONLY`, exercised against
a live database in test_database_live.py).
"""

from __future__ import annotations

import pytest
from nl2sql_agent.database import Database, UnsafeQueryError, ensure_read_only, strip_sql

# ---------------------------------------------------------------------------
# strip_sql
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SELECT 1", "SELECT 1"),
        ("  SELECT 1  ", "SELECT 1"),
        ("SELECT 1;", "SELECT 1"),
        ("SELECT 1;   ", "SELECT 1"),
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("```SQL\nSELECT 1;\n```", "SELECT 1"),
        ("  ```sql\n  SELECT 1;  \n```  ", "SELECT 1"),
    ],
)
def test_strip_sql(raw: str, expected: str):
    assert strip_sql(raw) == expected


def test_strip_sql_preserves_internal_structure():
    raw = "```sql\nSELECT a, b\nFROM t\nWHERE x = 1\n```"
    assert strip_sql(raw) == "SELECT a, b\nFROM t\nWHERE x = 1"


# ---------------------------------------------------------------------------
# ensure_read_only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select 1",
        "SeLeCt * FROM dim_store",
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "with cte as (select 1) select * from cte",
        "  SELECT 1  ",
    ],
)
def test_ensure_read_only_accepts_select_and_with(sql: str):
    cleaned = ensure_read_only(sql)
    assert cleaned.lower().startswith(("select", "with"))


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "\n\n",
    ],
)
def test_ensure_read_only_rejects_empty(sql: str):
    with pytest.raises(UnsafeQueryError, match="empty"):
        ensure_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dim_store",
        "UPDATE dim_store SET store_name = 'x'",
        "INSERT INTO dim_store VALUES (1)",
        "DROP TABLE dim_store",
        "not sql at all",
    ],
)
def test_ensure_read_only_rejects_non_select(sql: str):
    with pytest.raises(UnsafeQueryError, match="Only SELECT/WITH"):
        ensure_read_only(sql)


def test_ensure_read_only_rejects_multiple_statements():
    with pytest.raises(UnsafeQueryError, match="single statement"):
        ensure_read_only("SELECT 1; SELECT 2")


def test_ensure_read_only_does_not_catch_a_data_modifying_cte():
    """Documented limitation: the static check only looks at the very start
    of the string, so a CTE that starts with WITH/SELECT but *contains* a
    DELETE/UPDATE is NOT rejected here. Database.run_select's
    `SET TRANSACTION READ ONLY` is what actually stops this at execution
    time (see test_database_live.py). Pinning this behavior so nobody
    "fixes" this function in a way that silently drops the READ ONLY layer
    thinking this check alone is sufficient.
    """
    sneaky = "WITH d AS (DELETE FROM dim_store RETURNING *) SELECT * FROM d"
    cleaned = ensure_read_only(sneaky)
    assert cleaned == sneaky


# ---------------------------------------------------------------------------
# Database construction is lazy (no connection attempt until a query runs)
# ---------------------------------------------------------------------------


def test_database_construction_does_not_connect():
    # SQLAlchemy's create_engine is lazy; this must not raise even though
    # nothing is listening on this host/port.
    db = Database("postgresql+psycopg://user:pass@127.0.0.1:1/db")
    assert db.dialect == "postgresql"


def test_database_reports_its_dialect():
    db = Database("sqlite://")
    assert db.dialect == "sqlite"


# ---------------------------------------------------------------------------
# Result shaping and schema rendering (no server required)
# ---------------------------------------------------------------------------


def test_query_result_to_dicts_pairs_columns_with_values():
    from nl2sql_agent.database import QueryResult

    result = QueryResult(columns=["id", "name"], rows=[(1, "a"), (2, "b")], truncated=False)
    assert result.to_dicts() == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


def test_query_result_to_dicts_on_no_rows():
    from nl2sql_agent.database import QueryResult

    assert QueryResult(columns=["id"], rows=[], truncated=False).to_dicts() == []


def test_schema_rendering_includes_a_table_comment_when_one_exists(monkeypatch):
    """COMMENT ON metadata is how the schema teaches the model what a table
    means; the retail database has none today, so this path needs a stub.
    """
    from nl2sql_agent.database import Column, Table

    db = Database("postgresql+psycopg://u:p@127.0.0.1:1/db")
    table = Table(
        name="dim_store",
        comment="One row per retail store.",
        approx_rows=10,
        columns=[Column("store_key", "integer", True, "surrogate key")],
    )
    monkeypatch.setattr(db, "_load_tables", lambda names: [table])
    monkeypatch.setattr(db, "_sample_rows", lambda name, limit: "  (stubbed)")

    text = db.schema_and_samples(["dim_store"], sample_rows=1)

    assert "description: One row per retail store." in text
    assert "store_key (integer, NOT NULL)  -- surrogate key" in text


def test_sample_rows_reports_an_empty_table_rather_than_a_bare_header(monkeypatch):
    from nl2sql_agent.database import QueryResult

    db = Database("postgresql+psycopg://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(db, "run_select", lambda sql: QueryResult(columns=["a"], rows=[], truncated=False))
    assert db._sample_rows("dim_store", 3) == "  (table is empty)"
