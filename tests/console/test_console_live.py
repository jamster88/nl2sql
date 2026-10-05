"""The console against the real retail database, as the agent's role.

The fake in `conftest.py` proves the order of the gates; only a server
proves the fence. A write the validator does not recognise is refused by the
read-only transaction; a query that runs too long is cancelled by the
statement timeout; a `SELECT *` over the sales fact reads the rows it shows
and not the other million.

Opt-in (`pytest --run-docker`): connects to whatever Postgres is reachable
at POSTGRES_URL (default: the compose `postgres` service on localhost:5432,
as `nl2sql_reader`), and skips rather than fails if nothing is listening --
it tests behaviour against a real server, it does not stand one up.
"""

from __future__ import annotations


import pytest
import sqlalchemy

from nl2sql_agent.config import Settings
from nl2sql_agent.console.query import Inspector
from nl2sql_agent.database import Database
from tests import live_stores

pytestmark = pytest.mark.docker

POSTGRES_URL = live_stores.url("retail", variable="POSTGRES_URL")


def _inspector(*, max_rows: int = 1000, **agent) -> Inspector:
    settings = Settings(database_url=POSTGRES_URL, **agent)
    database = Database(
        POSTGRES_URL,
        db_schema=settings.db_schema,
        statement_timeout_ms=settings.statement_timeout_ms,
        max_rows=settings.max_rows,
    )
    return Inspector(database, settings, max_rows=max_rows)


@pytest.fixture(scope="module")
def inspector() -> Inspector:
    console = _inspector()
    try:
        console.db.table_names()
    except sqlalchemy.exc.SQLAlchemyError as exc:
        live_stores.unreachable("Postgres", POSTGRES_URL, exc)
    return console


def test_it_is_connected_as_the_agents_read_only_role(inspector):
    identity = inspector.identity()
    assert identity.role == "nl2sql_reader"
    assert identity.database == "nl2sql_retail"
    assert identity.read_only is True


def test_a_query_comes_back_with_its_types_named_as_the_schema_names_them(inspector):
    outcome = inspector.run(
        "SELECT 1::numeric(5,2) AS n, DATE '2025-01-01' AS d, 'x'::text AS t, "
        "ARRAY[1,2] AS a, '{\"k\": 1}'::jsonb AS j"
    )
    assert outcome.verdict.accepted is True
    assert outcome.columns == [
        ("n", "numeric"), ("d", "date"), ("t", "text"), ("a", "integer[]"), ("j", "jsonb")
    ]
    assert outcome.rows == [["1.00", "2025-01-01", "x", [1, 2], {"k": 1}]]


def test_a_write_the_validator_does_not_know_is_refused_by_the_transaction(inspector):
    """`lo_create` is not on the validator's denylist -- it is not the kind
    of thing a model writes -- and it writes. The READ ONLY transaction is
    what stops it, which is the point of having both layers."""
    outcome = inspector.run("SELECT lo_create(0)")
    assert outcome.executed is False
    assert outcome.verdict.stage == "runtime"
    assert outcome.error == "cannot execute lo_create() in a read-only transaction"


@pytest.mark.usefixtures("inspector")  # so a stack that is down is a skip, as for the rest
def test_a_query_that_runs_too_long_is_cancelled_at_the_agents_timeout():
    outcome = _inspector(statement_timeout_ms=300).run(
        "SELECT count(*) FROM fact_pos_retail_sales a CROSS JOIN dim_store b CROSS JOIN dim_store c"
    )
    assert outcome.verdict.stage == "runtime"
    assert outcome.error == "canceling statement due to statement timeout"


@pytest.mark.usefixtures("inspector")  # so a stack that is down is a skip, as for the rest
def test_the_sales_fact_is_read_as_far_as_it_is_shown_and_no_further():
    """With a client-side cursor this pulls 1.29 million rows into the
    process first; the server-side one it uses fetches eleven."""
    outcome = _inspector(max_rows=10).run("SELECT * FROM fact_pos_retail_sales")
    assert outcome.truncated is True
    assert outcome.row_count == 10
    assert outcome.elapsed_ms < 5000
    assert outcome.verdict.notes == [], "ten rows is inside the agent's fifty"


@pytest.mark.usefixtures("inspector")  # so a stack that is down is a skip, as for the rest
def test_a_plan_over_the_ceiling_is_the_planner_gates_refusal():
    outcome = _inspector(max_plan_cost=10.0).run("SELECT count(*) FROM fact_pos_retail_sales", "plan")
    assert outcome.verdict.stage == "planner"
    assert "exceeds the ceiling of 10.00" in outcome.verdict.issues[0].message
    assert outcome.plan_cost > 10


def test_the_planner_names_a_missing_column_and_suggests_the_right_one(inspector):
    outcome = inspector.run("SELECT store_nam FROM dim_store")
    assert outcome.verdict.stage == "planner"
    assert 'column "store_nam" does not exist' in outcome.error
    assert "store_name" in outcome.error


def test_analyze_reports_what_actually_happened(inspector):
    outcome = inspector.run("SELECT count(*) FROM dim_store", "analyze")
    assert outcome.executed is True
    assert outcome.plan[0]["Execution Time"] >= 0
    assert "Actual Total Time" in outcome.plan[0]["Plan"]


def test_the_catalogue_can_be_read_though_the_agent_would_refuse_it(inspector):
    outcome = inspector.run("SELECT tablename FROM pg_tables WHERE schemaname = 'public' LIMIT 1")
    assert outcome.executed is True
    assert outcome.verdict.stage == "static"
    assert outcome.verdict.issues[0].message == "pg_tables is not in scope."


def test_the_schema_and_the_prompt_are_what_the_agent_reads(inspector):
    names = [table.name for table in inspector.catalog()]
    assert {"dim_store", "fact_pos_retail_sales"} <= set(names)
    block = inspector.prompt("dim_store")
    assert block.startswith("=== dim_store ===")
    assert "sample rows (up to 3):" in block
