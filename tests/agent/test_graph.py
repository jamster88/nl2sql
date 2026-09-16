"""Nl2SqlAgent / the LangGraph pipeline: select_tables -> fetch_schema ->
generate_sql -> validate_sql -> (retry | execute | give_up).

Built entirely on FakeDatabase/ScriptedLLM -- no live Postgres or Ollama.
Database() itself is still constructed for real (SQLAlchemy's create_engine
is lazy, see test_database_safety.py), then swapped out for the fake before
any node runs, since Nl2SqlAgent.__init__ doesn't take a db= override.
"""

from __future__ import annotations

import pytest
from nl2sql_agent.config import Settings
from nl2sql_agent.graph import Nl2SqlAgent
from nl2sql_agent.tools import SqlReview, TableSelection, build_tools

from .conftest import FakeDatabase, ScriptedLLM


def make_agent(db: FakeDatabase, llm: ScriptedLLM, **settings_kwargs) -> Nl2SqlAgent:
    settings = Settings(database_url="postgresql+psycopg://u:p@127.0.0.1:1/db", **settings_kwargs)
    agent = Nl2SqlAgent(settings, llm=llm)
    agent.db = db
    agent.tools = build_tools(db, llm, settings)
    return agent


@pytest.fixture
def progress_log():
    log: list[tuple[str, str]] = []

    def on_progress(step: str, detail: str) -> None:
        log.append((step, detail))

    on_progress.log = log
    return on_progress


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_selects_tables_generates_and_executes(progress_log):
    db = FakeDatabase(tables=["dim_store", "fact_pos_retail_sales"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT COUNT(*) FROM dim_store"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, max_sql_attempts=3)
    agent._on_progress = progress_log

    state = agent.run("How many stores are there?")

    assert state.get("error") is None
    assert state["selected_tables"] == ["dim_store"]
    assert state["sql"] == "SELECT COUNT(*) FROM dim_store"
    assert state["attempts"] == 1
    assert state["result"]["row_count"] == 1
    assert db.run_select_calls == ["SELECT COUNT(*) FROM dim_store"]
    steps = [step for step, _ in progress_log.log]
    assert steps == ["select_tables", "fetch_schema", "generate_sql", "validate_sql", "execute_query"]


def test_fetch_schema_only_requests_the_selected_tables():
    db = FakeDatabase(tables=["dim_store", "fact_pos_retail_sales", "dim_product"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store", "dim_product"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm)
    agent.run("q")
    assert db.schema_and_samples_calls[0][0] == ["dim_store", "dim_product"]


def test_select_tables_falls_back_to_every_known_table_when_the_model_invents_names():
    db = FakeDatabase(tables=["dim_store", "dim_product"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["not_a_real_table"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm)
    state = agent.run("q")
    assert state["selected_tables"] == sorted(db.table_names())


# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------


def test_retries_on_validation_failure_and_feeds_issues_back(progress_log):
    db = FakeDatabase(tables=["dim_store"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT bad", "SELECT * FROM dim_store"],
        sql_reviews=[SqlReview(is_valid=False, issues=["wrong join key"]), SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, max_sql_attempts=3)
    agent._on_progress = progress_log

    state = agent.run("q")

    assert state.get("error") is None
    assert state["sql"] == "SELECT * FROM dim_store"
    assert state["attempts"] == 2
    # The second generate_sql call must have seen the first attempt's SQL and
    # the reviewer's issue in its feedback message.
    second_call_messages = llm.plain_invocations[1]
    rendered = "\n".join(getattr(m, "content", str(m)) for m in second_call_messages)
    assert "SELECT bad" in rendered
    assert "wrong join key" in rendered
    assert ("validate_sql", "wrong join key") in progress_log.log


def test_gives_up_after_max_attempts_and_reports_the_last_issues():
    db = FakeDatabase(tables=["dim_store"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT 1", "SELECT 2"],
        sql_reviews=[
            SqlReview(is_valid=False, issues=["bad #1"]),
            SqlReview(is_valid=False, issues=["bad #2"]),
        ],
    )
    agent = make_agent(db, llm, max_sql_attempts=2)

    state = agent.run("q")

    assert state["attempts"] == 2
    assert "error" in state and state["error"]
    assert "2 attempts" in state["error"]
    assert "bad #2" in state["error"]
    assert "result" not in state
    assert db.run_select_calls == []  # never reached execute_query


def test_unsafe_sql_is_rejected_without_ever_reaching_the_database_planner():
    db = FakeDatabase(tables=["dim_store"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["DELETE FROM dim_store"],
        sql_reviews=[],  # must never be consulted
    )
    agent = make_agent(db, llm, max_sql_attempts=1)

    state = agent.run("delete everything")

    assert state["error"]
    assert "Only SELECT/WITH" in state["error"]
    assert db.explain_calls == []
    assert db.run_select_calls == []


# ---------------------------------------------------------------------------
# Execution failure
# ---------------------------------------------------------------------------


def test_execute_query_failure_is_captured_as_an_error_not_an_exception():
    db = FakeDatabase(tables=["dim_store"], run_select_error=RuntimeError("statement timeout"))
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT * FROM dim_store"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm)

    state = agent.run("q")

    assert state["error"] == "statement timeout"
    assert "result" not in state


# ---------------------------------------------------------------------------
# Routing logic in isolation
# ---------------------------------------------------------------------------


def test_route_after_validation_executes_when_no_issues():
    agent = make_agent(FakeDatabase(), ScriptedLLM(), max_sql_attempts=3)
    assert agent._route_after_validation({"issues": [], "attempts": 1}) == "execute"


def test_route_after_validation_retries_under_the_attempt_limit():
    agent = make_agent(FakeDatabase(), ScriptedLLM(), max_sql_attempts=3)
    assert agent._route_after_validation({"issues": ["x"], "attempts": 1}) == "retry"


def test_route_after_validation_gives_up_at_the_attempt_limit():
    agent = make_agent(FakeDatabase(), ScriptedLLM(), max_sql_attempts=3)
    assert agent._route_after_validation({"issues": ["x"], "attempts": 3}) == "give_up"
