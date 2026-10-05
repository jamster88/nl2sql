"""Validating a reviewer's SQL: the checks, then the run.

The unit half drives `validate` with a scripted connection, which is what
lets every branch run on every invocation -- the refusal, the unreachable
database, the empty result, the row cap. The live half, behind
`--run-docker`, asks the real retail database the things a fake cannot
answer honestly: that the reader role refuses a writing CTE, that the
statement timeout fires, and that Postgres' own hint survives the trip.

It also asks the least-access questions from the reviewer's side. Unlike the
agent, this validator has no function denylist -- a reviewer is trusted to
write SQL, not to hold the database's powers -- so every guarantee below is
the server's: the query runs as `nl2sql_reader`, read-only and under its
timeout, and nothing it can call gets it out of any of the three, into
another session, or onto the server's filesystem.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest

from nl2sql_review.validation import (
    SAMPLE_ROWS,
    Validation,
    clean,
    json_safe,
    normalise,
    static_problems,
    validate,
)
from tests import live_stores


class Refusal(psycopg.Error):
    """A database refusing a statement, as the driver raises it. `diag` is a
    plain attribute here so a test can say what the server said."""

    diag = None




class FakeCursor:
    def __init__(self, *, columns, rows=None, plan=None):
        self.description = [SimpleNamespace(name=c) for c in columns] if columns is not None else None
        self._rows = list(rows or [])
        self._plan = plan

    def fetchone(self):
        return (self._plan,)

    def fetchmany(self, size):
        return self._rows[:size]


class FakeConnection:
    """Answers the four statements `validate` sends, in order."""

    def __init__(self, *, columns=("n",), rows=((1,),), plan=None, fail=None):
        self.columns, self.rows, self.fail = list(columns), list(rows), fail
        self.plan = plan if plan is not None else [{"Plan": {"Total Cost": 12.5}}]
        self.statements: list[str] = []
        self.rolled_back = self.closed = False

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        if self.fail is not None and len(self.statements) == 3:
            raise self.fail
        if len(self.statements) == 3:
            return FakeCursor(columns=None, plan=self.plan)
        return FakeCursor(columns=self.columns, rows=self.rows)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def run(sql="SELECT n FROM t", **kwargs):
    conn = kwargs.pop("conn", None) or FakeConnection()
    opened: list[tuple] = []

    def connect(url, **options):
        opened.append((url, options))
        return conn

    return validate(sql, url="postgresql://reader@retail/db", connect=connect, **kwargs), conn, opened


# ---------------------------------------------------------------------------
# Before anything runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("SELECT 1;", "SELECT 1"),
        ("  SELECT 1 ;; ", "SELECT 1"),
        ("```sql\nSELECT 1;\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("", ""),
        (None, ""),
    ],
)
def test_the_statement_is_cleaned_before_it_is_run(text, expected):
    assert clean(text) == expected


def test_queries_that_differ_in_spacing_and_case_are_the_same_query():
    assert normalise("select  sku_id\nFROM dim_product;") == normalise("SELECT sku_id FROM dim_product")


def test_an_empty_query_is_refused():
    assert static_problems("") == ["the SQL is empty"]


def test_two_statements_are_refused():
    problems = static_problems("SELECT 1; SELECT 2")
    assert problems == ["only one statement can be validated; remove everything after the first semicolon"]


def test_only_a_select_is_ever_run():
    assert static_problems("DELETE FROM dim_store") == [
        "an answer is a SELECT (or WITH ... SELECT); nothing else is run"
    ]
    assert static_problems("WITH x AS (SELECT 1) SELECT * FROM x") == []


def test_the_agents_own_query_is_not_a_fix():
    problems = static_problems("select sku_id from dim_product", "SELECT sku_id\nFROM dim_product;")
    assert problems == ["this is the query the agent generated; a fix has to change it"]


def test_a_static_problem_never_reaches_the_database():
    result, _, opened = run("UPDATE dim_store SET store_name = 'x'")
    assert result.valid is False and opened == []
    assert result.problems == ["an answer is a SELECT (or WITH ... SELECT); nothing else is run"]


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (True, True),
        (3, 3),
        (1.5, 1.5),
        ("x", "x"),
        (Decimal("719279.97"), "719279.97"),
        (date(2025, 3, 31), "2025-03-31"),
        (datetime(2025, 3, 31, 12, 30), "2025-03-31T12:30:00"),
        (time(9, 5), "09:05:00"),
        (uuid.UUID(int=0), "00000000-0000-0000-0000-000000000000"),
    ],
)
def test_every_cell_is_made_json_safe_without_losing_what_it_said(value, expected):
    assert json_safe(value) == expected


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def test_a_query_that_runs_is_valid_and_reports_what_it_returned():
    conn = FakeConnection(columns=("sku_id", "net"), rows=[("S1", Decimal("1.50")), ("S2", None)])
    result, conn, opened = run("SELECT sku_id, net FROM t;", conn=conn, timeout_ms=1234)

    assert result.valid is True and result.problems == [] and result.warnings == []
    assert result.sql == "SELECT sku_id, net FROM t"
    assert result.columns == ["sku_id", "net"]
    assert result.rows == [["S1", "1.50"], ["S2", None]]
    assert (result.row_count, result.truncated, result.plan_cost) == (2, False, 12.5)
    assert result.elapsed_ms >= 0
    # Read-only, time-limited, planned first, rolled back and closed.
    assert conn.statements[0] == "SET TRANSACTION READ ONLY"
    assert "statement_timeout" in conn.statements[1] and "1234" in conn.statements[1]
    assert conn.statements[2] == "EXPLAIN (FORMAT JSON) SELECT sku_id, net FROM t"
    assert conn.statements[3] == "SELECT sku_id, net FROM t"
    assert conn.rolled_back and conn.closed
    assert opened == [("postgresql://reader@retail/db", {"connect_timeout": 10})]


def test_no_rows_is_valid_with_a_warning():
    result, _, _ = run(conn=FakeConnection(rows=[]))
    assert result.valid is True and result.row_count == 0
    assert result.warnings[0].startswith("it ran and returned no rows")


def test_more_rows_than_the_cap_is_valid_truncated_and_says_so():
    many = [(i,) for i in range(30)]
    result, _, _ = run(conn=FakeConnection(rows=many), max_rows=25, sample_rows=5)
    assert result.valid is True
    assert (result.row_count, result.truncated) == (25, True)
    assert len(result.rows) == 5
    assert result.warnings == ["it returned more than 25 rows; only the first 25 were read"]


def test_only_a_sample_of_the_rows_is_sent_back():
    result, _, _ = run(conn=FakeConnection(rows=[(i,) for i in range(50)]))
    assert len(result.rows) == SAMPLE_ROWS and result.row_count == 50


def test_a_plan_with_no_cost_reads_as_none():
    result, _, _ = run(conn=FakeConnection(plan=[{}]))
    assert result.valid is True and result.plan_cost is None


def test_the_databases_refusal_is_reported_in_its_own_words():
    diag = SimpleNamespace(message_primary='column "store_nam" does not exist',
                           message_hint='Perhaps you meant to reference the column "dim_store.store_name".')
    error = Refusal("LINE 1: EXPLAIN (FORMAT JSON) SELECT store_nam ...")
    error.diag = diag
    result, conn, _ = run(conn=FakeConnection(fail=error))
    assert result.valid is False
    assert result.problems == [
        'the database refused it: column "store_nam" does not exist -- '
        'Perhaps you meant to reference the column "dim_store.store_name".'
    ]
    assert conn.rolled_back and conn.closed


def test_a_refusal_without_a_hint_or_a_diagnosis_still_says_what_happened():
    bare = Refusal("canceling statement\n  due to statement timeout")
    bare.diag = SimpleNamespace(message_primary=None)
    result, _, _ = run(conn=FakeConnection(fail=bare))
    assert result.problems == ["the database refused it: canceling statement due to statement timeout"]

    primary_only = Refusal("x")
    primary_only.diag = SimpleNamespace(message_primary="permission denied for table dim_store", message_hint=None)
    result, _, _ = run(conn=FakeConnection(fail=primary_only))
    assert result.problems == ["the database refused it: permission denied for table dim_store"]

    result, _, _ = run(conn=FakeConnection(fail=psycopg.OperationalError()))
    assert result.problems == ["the database refused it: OperationalError"]


def test_a_database_that_cannot_be_reached_is_a_problem_not_a_crash():
    def refuse(url, **options):
        raise OSError("connection refused")

    result = validate("SELECT 1", url="postgresql://x", connect=refuse)
    assert result.valid is False
    assert result.problems == ["cannot reach the retail database to validate it: connection refused"]


def test_a_validation_serialises_whole():
    assert Validation(sql="SELECT 1", valid=True).as_dict()["sql"] == "SELECT 1"


# ---------------------------------------------------------------------------
# Against the real retail database
# ---------------------------------------------------------------------------

RETAIL_URL = live_stores.url("retail", variable="RETAIL_DB_URL", driver="postgresql")


@pytest.fixture(scope="module")
def live():
    import psycopg

    try:
        psycopg.connect(RETAIL_URL, connect_timeout=3).close()
    except Exception as exc:  # noqa: BLE001
        live_stores.unreachable("retail database", RETAIL_URL, exc)
    return RETAIL_URL


@pytest.mark.docker
def test_live_a_real_fix_runs_and_comes_back_labelled(live):
    result = validate(
        "SELECT store_id, store_name FROM dim_store ORDER BY store_id LIMIT 3",
        url=live,
        reference="SELECT store_id FROM dim_store",
    )
    assert result.valid is True
    assert result.columns == ["store_id", "store_name"]
    assert result.rows[0][0] == "STR001" and result.row_count == 3
    assert result.plan_cost is not None


@pytest.mark.docker
def test_live_postgres_hint_reaches_the_reviewer(live):
    result = validate("SELECT store_nam FROM dim_store", url=live)
    assert result.valid is False
    assert 'Perhaps you meant to reference the column "dim_store.store_name"' in result.problems[0]


@pytest.mark.docker
def test_live_a_writing_cte_is_refused_by_the_database_itself(live):
    """The static check lets a WITH through; the reader role is what holds."""
    result = validate("WITH d AS (DELETE FROM dim_store RETURNING *) SELECT * FROM d", url=live)
    assert result.valid is False
    assert "the database refused it" in result.problems[0]


@pytest.mark.docker
def test_live_the_statement_timeout_fires(live):
    result = validate("SELECT pg_sleep(3)", url=live, timeout_ms=300)
    assert result.valid is False
    assert "statement timeout" in result.problems[0]


# ---------------------------------------------------------------------------
# Least access, from the reviewer's side
# ---------------------------------------------------------------------------


@pytest.mark.docker
def test_live_a_fix_runs_as_the_reader_read_only_and_under_its_timeout(live):
    result = validate(
        "SELECT current_user AS who, current_setting('transaction_read_only') AS read_only, "
        "current_setting('statement_timeout') AS timeout",
        url=live,
        timeout_ms=4321,
    )
    assert result.valid is True, result.problems
    assert result.rows == [["nl2sql_reader", "on", "4321ms"]]


@pytest.mark.docker
@pytest.mark.parametrize(
    "escape,refusal",
    [
        # Out of the read-only transaction: too late once a query has run.
        ("SELECT set_config('transaction_read_only', 'off', true)", "read-write mode must be set before any query"),
        # Into the owner's role, which the reader is not a member of.
        ("SELECT set_config('role', 'nl2sql', true)", 'permission denied to set role "nl2sql"'),
        # Onto the server's files.
        ("SELECT pg_read_file('pg_hba.conf')", "permission denied for function pg_read_file"),
        # A large object is a write, whatever the statement starts with.
        ("SELECT lo_create(0)", "cannot execute lo_create() in a read-only transaction"),
    ],
)
def test_live_a_reviewers_query_cannot_step_outside_the_reader(live, escape, refusal):
    result = validate(escape, url=live)
    assert result.valid is False
    assert refusal in result.problems[0]


@pytest.mark.docker
def test_live_a_reviewers_query_cannot_lift_its_own_timeout(live):
    """The timeout is armed when the statement starts; changing the setting
    from inside it changes nothing for the statement already running."""
    result = validate("SELECT set_config('statement_timeout', '0', true), pg_sleep(3)", url=live, timeout_ms=300)
    assert result.valid is False
    assert "statement timeout" in result.problems[0]


@pytest.mark.docker
@pytest.mark.parametrize("call", ["pg_cancel_backend", "pg_terminate_backend"])
def test_live_a_reviewers_query_cannot_end_someone_elses_session(live, call):
    """The agent's sessions are the same role as this one, which is all
    Postgres asks before letting one signal another -- unless the function
    itself is out of reach, as docker/reader_role.sql makes it."""
    import psycopg

    with psycopg.connect(live) as bystander:
        pid = bystander.execute("SELECT pg_backend_pid()").fetchone()[0]
        result = validate(f"SELECT {call}({pid})", url=live)
        assert result.valid is False
        assert f"permission denied for function {call}" in result.problems[0]
        assert bystander.execute("SELECT 1").fetchone()[0] == 1
