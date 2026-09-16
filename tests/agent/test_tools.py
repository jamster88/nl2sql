"""build_tools(): the four tools from basic_agent_steps.md, exercised
directly against FakeDatabase/ScriptedLLM.
"""

from __future__ import annotations

from nl2sql_agent.config import Settings
from nl2sql_agent.database import QueryResult
from nl2sql_agent.tools import SqlReview, build_tools

from .conftest import FakeDatabase, ScriptedLLM


def test_describe_all_tables_delegates_to_the_database(fake_db, scripted_llm):
    tools = build_tools(fake_db, scripted_llm, Settings())
    result = tools["describe_all_tables"].invoke({})
    assert result == fake_db.describe_all_tables()


def test_get_schema_and_data_passes_through_tables_and_sample_rows(fake_db, scripted_llm):
    settings = Settings(sample_rows=7)
    tools = build_tools(fake_db, scripted_llm, settings)
    tools["get_schema_and_data"].invoke({"tables": ["dim_store"]})
    assert fake_db.schema_and_samples_calls == [(["dim_store"], 7)]


def test_validate_sql_rejects_unsafe_sql_before_touching_the_database(fake_db, scripted_llm):
    tools = build_tools(fake_db, scripted_llm, Settings())
    result = tools["validate_sql"].invoke(
        {"sql": "DELETE FROM dim_store", "question": "q", "schema_context": "s"}
    )
    assert result["is_valid"] is False
    assert "Only SELECT/WITH" in result["issues"][0]
    assert fake_db.explain_calls == []
    assert scripted_llm.structured_invocations == []


def test_validate_sql_rejects_on_a_definitive_planner_error_without_asking_the_model(scripted_llm):
    db = FakeDatabase(explain_error='relation "nope" does not exist')
    tools = build_tools(db, scripted_llm, Settings())
    result = tools["validate_sql"].invoke(
        {"sql": "SELECT * FROM nope", "question": "q", "schema_context": "s"}
    )
    assert result["is_valid"] is False
    assert "Database rejected the query" in result["issues"][0]
    assert "nope" in result["issues"][0]
    assert db.explain_calls == ["SELECT * FROM nope"]
    assert scripted_llm.structured_invocations == []  # no point asking the model


def test_validate_sql_accepts_when_planner_and_model_both_approve(fake_db):
    llm = ScriptedLLM(sql_reviews=[SqlReview(is_valid=True, issues=[])])
    tools = build_tools(fake_db, llm, Settings())
    result = tools["validate_sql"].invoke(
        {"sql": "SELECT * FROM dim_store", "question": "q", "schema_context": "s"}
    )
    assert result == {"is_valid": True, "issues": []}
    assert fake_db.explain_calls == ["SELECT * FROM dim_store"]


def test_validate_sql_surfaces_model_rejection_issues(fake_db):
    llm = ScriptedLLM(sql_reviews=[SqlReview(is_valid=False, issues=["wrong join key"])])
    tools = build_tools(fake_db, llm, Settings())
    result = tools["validate_sql"].invoke(
        {"sql": "SELECT * FROM dim_store", "question": "q", "schema_context": "s"}
    )
    assert result == {"is_valid": False, "issues": ["wrong join key"]}


def test_execute_query_shapes_the_result(scripted_llm):
    db = FakeDatabase(
        run_select_result=QueryResult(columns=["a", "b"], rows=[(1, "x"), (2, "y")], truncated=True)
    )
    tools = build_tools(db, scripted_llm, Settings())
    result = tools["execute_query"].invoke({"sql": "SELECT a, b FROM t"})
    assert result == {
        "columns": ["a", "b"],
        "rows": [[1, "x"], [2, "y"]],
        "row_count": 2,
        "truncated": True,
    }
    assert db.run_select_calls == ["SELECT a, b FROM t"]
