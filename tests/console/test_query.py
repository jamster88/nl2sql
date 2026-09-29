"""The console's inspector: a person's SQL, through the agent's gates.

Against `ScriptedDatabase`, so every gate's refusal and every fence the
query runs inside can be asserted without a server. What a real Postgres
does with the same statements is `test_console_live.py`'s.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from nl2sql_agent.config import Settings
from nl2sql_agent.console.query import (
    MODES,
    DatabaseUnavailable,
    Identity,
    Inspector,
    error_message,
    json_safe,
)
from tests.console.conftest import ScriptedDatabase

WRITING_CTE = "WITH d AS (DELETE FROM dim_store RETURNING *) SELECT * FROM d"


# ---------------------------------------------------------------------------
# The first gate: the agent's static validator
# ---------------------------------------------------------------------------


def test_a_query_the_validator_refuses_for_safety_is_not_run_at_all(inspector, db):
    outcome = inspector.run(WRITING_CTE)

    assert outcome.executed is False
    assert outcome.verdict.accepted is False
    assert outcome.verdict.stage == "static"
    assert "DELETE" in outcome.verdict.issues[0].message
    assert db.connections == 0, "a refused query must never reach the database"


def test_an_empty_query_is_the_validators_to_refuse(inspector, db):
    outcome = inspector.run("   ;  ")
    assert outcome.verdict.stage == "static"
    assert outcome.sql == ""
    assert db.connections == 0


def test_a_fenced_query_is_run_without_its_fence(inspector, db):
    """The agent strips markdown fences and trailing semicolons from what a
    model wrote; a query pasted from the agent's answer often has both."""
    outcome = inspector.run("```sql\nSELECT store_key FROM dim_store;\n```")
    assert outcome.sql == "SELECT store_key FROM dim_store"
    assert "SELECT store_key FROM dim_store" in db.statements


def test_a_reference_outside_the_schema_is_reported_and_still_run(inspector, db):
    """The agent would refuse it as out of scope. The console runs it
    anyway: looking at the catalogue is part of troubleshooting, and the
    refusal is still the diagnosis."""
    outcome = inspector.run("SELECT * FROM pg_stats")

    assert outcome.executed is True
    assert outcome.verdict.stage == "static"
    assert outcome.verdict.issues[0].message == "pg_stats is not in scope."


def test_a_misspelt_table_gets_the_validators_suggestion(inspector):
    outcome = inspector.run("SELECT * FROM dim_stor")
    assert "dim_store" in outcome.verdict.issues[0].message


def test_the_scope_is_the_whole_schema(inspector):
    """The widest the agent ever allows: every table its introspection sees."""
    outcome = inspector.run("SELECT * FROM dim_store")
    assert outcome.verdict.accepted is True


def test_the_scope_check_needs_the_database(inspector, db):
    db.introspection_error = "connection refused"
    with pytest.raises(DatabaseUnavailable, match="connection refused"):
        inspector.run("SELECT 1")


# ---------------------------------------------------------------------------
# The fence every query runs inside
# ---------------------------------------------------------------------------


def test_every_query_runs_read_only_under_the_agents_timeout(db):
    inspector = Inspector(db, Settings(statement_timeout_ms=1234), max_rows=10)
    inspector.run("SELECT 1")

    assert db.statements[:2] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = 1234",
    ]


@pytest.mark.parametrize("mode", MODES)
def test_the_transaction_is_rolled_back_whatever_the_mode(inspector, db, mode):
    inspector.run("SELECT 1", mode)
    assert db.rolled_back == 1


def test_the_transaction_is_rolled_back_when_the_query_fails(inspector, db):
    db.run_error = "division by zero"
    inspector.run("SELECT 1")
    assert db.rolled_back == 1


def test_the_query_is_read_through_a_server_side_cursor(inspector, db):
    """A client-side cursor pulls the whole result into the process before
    the first row is read: a million rows of the sales fact, to show 1000."""
    inspector.run("SELECT * FROM dim_store")
    assert db.options[-1] == {"stream_results": True}
    assert db.fetched == [1001], "one row past the cap, and no more"


def test_a_connection_that_cannot_be_opened_is_the_database_being_down(inspector, db):
    db.connect_error = "could not connect to server"
    with pytest.raises(DatabaseUnavailable, match="could not connect to server"):
        inspector.run("SELECT 1")


@pytest.mark.parametrize("drop_on", ["SET TRANSACTION", "SELECT oid::int"])
def test_a_connection_that_drops_mid_query_is_the_database_being_down(inspector, db, drop_on):
    """Not the query's fault, so not a runtime refusal: a query that ran and
    then lost its connection while its types were looked up is a database
    that went away."""
    db.drop_on = drop_on
    with pytest.raises(DatabaseUnavailable, match="closed the connection"):
        inspector.run("SELECT 1")


# ---------------------------------------------------------------------------
# The second gate: the planner
# ---------------------------------------------------------------------------


def test_a_query_the_planner_refuses_is_not_run(inspector, db):
    db.plan_error = 'column "nope" does not exist'
    outcome = inspector.run("SELECT nope FROM dim_store")

    assert outcome.executed is False
    assert outcome.verdict.stage == "planner"
    assert outcome.error == 'column "nope" does not exist'
    assert not any(s == "SELECT nope FROM dim_store" for s in db.statements)


def test_the_cost_is_read_and_judged_the_way_the_gate_judges_it(db):
    db.plan = [{"Plan": {"Node Type": "Seq Scan", "Total Cost": 4_200_000.0}}]
    outcome = Inspector(db, Settings(max_plan_cost=1_000_000.0), max_rows=10).run("SELECT 1")

    assert outcome.plan_cost == 4_200_000.0
    assert outcome.verdict.stage == "planner"
    assert outcome.verdict.issues[0].message == (
        "estimated plan cost 4,200,000.00 exceeds the ceiling of 1,000,000.00"
    )


def test_a_query_over_the_ceiling_is_still_run_so_its_rows_can_be_seen(db):
    db.plan = [{"Plan": {"Node Type": "Seq Scan", "Total Cost": 4_200_000.0}}]
    outcome = Inspector(db, Settings(max_plan_cost=1.0), max_rows=10).run("SELECT 1")
    assert outcome.executed is True
    assert outcome.rows


def test_every_objection_is_listed_and_the_first_decides_the_stage(db):
    db.plan = [{"Plan": {"Node Type": "Seq Scan", "Total Cost": 4_200_000.0}}]
    outcome = Inspector(db, Settings(max_plan_cost=1.0), max_rows=10).run("SELECT * FROM pg_stats")
    assert [issue.stage for issue in outcome.verdict.issues] == ["static", "planner"]
    assert outcome.verdict.stage == "static"


def test_plan_stops_at_explain(inspector, db):
    outcome = inspector.run("SELECT 1", "plan")

    assert outcome.executed is False
    assert outcome.verdict.accepted is True
    assert outcome.plan == db.plan
    assert outcome.plan_cost == 1.4
    assert outcome.rows == [] and outcome.columns == []
    assert "SELECT 1" not in db.statements


# ---------------------------------------------------------------------------
# The third gate: the executor
# ---------------------------------------------------------------------------


def test_a_run_returns_its_columns_named_by_type_and_its_rows(inspector):
    outcome = inspector.run("SELECT store_key, store_name FROM dim_store")

    assert outcome.executed is True
    assert outcome.verdict.accepted is True
    assert outcome.columns == [("store_key", "integer"), ("store_name", "character varying")]
    assert outcome.rows == [[1, "Ashland"], [2, "Salem"]]
    assert outcome.row_count == 2
    assert outcome.truncated is False
    assert outcome.elapsed_ms >= 0


def test_a_type_postgres_does_not_name_is_shown_by_its_oid(inspector, db):
    db.description = [("mystery", 99999)]
    db.rows = [("x",)]
    assert inspector.run("SELECT 1").columns == [("mystery", "99999")]


def test_a_result_longer_than_the_cap_is_cut_and_says_so(db, agent):
    db.rows = [(n, f"store {n}") for n in range(5)]
    outcome = Inspector(db, agent, max_rows=3).run("SELECT 1")
    assert outcome.truncated is True
    assert outcome.row_count == 3


def test_a_result_longer_than_the_agent_reads_is_noted(db):
    db.rows = [(n, "x") for n in range(4)]
    outcome = Inspector(db, Settings(max_rows=3), max_rows=100).run("SELECT 1")

    assert outcome.verdict.accepted is True, "a note is not a refusal"
    assert outcome.verdict.notes == [
        "the agent reads at most 3 rows (MAX_ROWS), so it would have seen the first 3 of "
        "these and been told the result was truncated"
    ]


def test_a_result_the_agent_reads_whole_has_no_note(db):
    db.rows = [(n, "x") for n in range(3)]
    outcome = Inspector(db, Settings(max_rows=3), max_rows=100).run("SELECT 1")
    assert outcome.verdict.notes == []


def test_a_query_the_executor_refuses_says_so_in_its_words(inspector, db):
    db.run_error = "canceling statement due to statement timeout"
    outcome = inspector.run("SELECT 1")

    assert outcome.executed is False
    assert outcome.verdict.stage == "runtime"
    assert outcome.error == "canceling statement due to statement timeout"
    assert outcome.plan is not None, "the plan it got as far as is kept"


def test_analyze_times_the_query_and_returns_no_rows(inspector, db):
    outcome = inspector.run("SELECT 1", "analyze")

    assert outcome.executed is True
    assert outcome.plan == db.analyzed
    assert outcome.rows == []
    assert any(s.startswith("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)") for s in db.statements)


def test_analyze_reports_a_query_that_fails_as_the_executor_would(inspector, db):
    db.analyze_error = "canceling statement due to statement timeout"
    outcome = inspector.run("SELECT 1", "analyze")
    assert outcome.verdict.stage == "runtime"
    assert outcome.executed is False


# ---------------------------------------------------------------------------
# Introspection, as the agent reads it
# ---------------------------------------------------------------------------


def test_the_schema_and_the_prompt_are_the_agents_own(inspector, db):
    assert [table.name for table in inspector.catalog()] == ["dim_store"]
    assert inspector.prompt("dim_store").startswith("=== dim_store ===")
    assert "up to 3" in inspector.prompt("dim_store"), "the agent's SAMPLE_ROWS"
    assert inspector.prompt("absent") == ""


@pytest.mark.parametrize("call", ["tables", "catalog", "prompt", "identity"])
def test_introspection_that_cannot_reach_the_database_says_so(inspector, db, call):
    db.introspection_error = "connection refused"
    db.connect_error = "connection refused"
    with pytest.raises(DatabaseUnavailable, match="connection refused"):
        getattr(inspector, call)(*(["dim_store"] if call == "prompt" else []))


def test_the_identity_is_read_from_the_connection(inspector):
    identity = inspector.identity()
    assert identity == Identity("nl2sql_reader", "nl2sql_retail", "18.6", False, False)
    assert identity.read_only is True


@pytest.mark.parametrize(("superuser", "can_write"), [(True, False), (False, True), (True, True)])
def test_a_role_that_could_write_is_not_read_only(superuser, can_write):
    assert Identity("x", "db", "18", superuser, can_write).read_only is False


# ---------------------------------------------------------------------------
# Cells, as JSON can carry them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (7, 7),
        ("text", "text"),
        (1.5, 1.5),
        (Decimal("719279.97"), "719279.97"),
        (date(2025, 1, 31), "2025-01-31"),
        (datetime(2025, 1, 31, 9, 30, tzinfo=timezone.utc), "2025-01-31T09:30:00+00:00"),
        (time(9, 30), "09:30:00"),
        (b"\x00\xff", "\\x00ff"),
        (bytearray(b"\x01"), "\\x01"),
        (memoryview(b"\x02"), "\\x02"),
        ([Decimal("1.10"), None], ["1.10", None]),
        ((1, date(2025, 1, 1)), [1, "2025-01-01"]),
        ({"a": Decimal("2"), 3: [1]}, {"a": "2", "3": [1]}),
        (timedelta(days=1, hours=2), "1 day, 2:00:00"),
    ],
)
def test_a_cell_keeps_what_it_said(value, expected):
    assert json_safe(value) == expected


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_float_json_cannot_spell_becomes_text(value):
    """Otherwise the whole response fails to serialise, over one cell."""
    assert json_safe(value) == str(value)


def test_anything_else_is_shown_as_its_text():
    value = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert json_safe(value) == "12345678-1234-5678-1234-567812345678"


def test_an_error_is_reported_in_the_databases_words():
    class Wrapped(Exception):
        orig = "  relation \"x\" does not exist  "

    assert error_message(Wrapped("(psycopg) noise")) == 'relation "x" does not exist'
    assert error_message(RuntimeError("plain")) == "plain"
    assert error_message(RuntimeError()) == "RuntimeError"


def test_the_scripted_database_is_the_agents_shape(db: ScriptedDatabase):
    """The fake answers to the names the real `Database` has, so a rename
    there fails here rather than passing against a fake that still has it."""
    from nl2sql_agent.database import Database

    for name in ("table_names", "catalog", "schema_and_samples", "engine"):
        assert hasattr(Database, name)
        assert hasattr(db, name)
