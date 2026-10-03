"""build_tools(): the four tools from basic_agent_steps.md, exercised
directly against FakeDatabase/ScriptedLLM.
"""

from __future__ import annotations

from nl2sql_agent.config import Settings
from nl2sql_agent.database import QueryResult
from nl2sql_agent.tools import build_tools

from .conftest import FakeDatabase, FakeKnowledgeBase, FakeSnippetLibrary, make_chunk, make_snippet


def test_describe_all_tables_delegates_to_the_database(fake_db, scripted_llm):
    tools = build_tools(fake_db, scripted_llm, Settings())
    result = tools["describe_all_tables"].invoke({})
    assert result == fake_db.describe_all_tables()


def test_get_schema_and_data_passes_through_tables_and_sample_rows(fake_db, scripted_llm):
    settings = Settings(sample_rows=7)
    tools = build_tools(fake_db, scripted_llm, settings)
    tools["get_schema_and_data"].invoke({"tables": ["dim_store"]})
    assert fake_db.schema_and_samples_calls == [(["dim_store"], 7)]


def test_search_knowledge_returns_context_tables_and_provenance(fake_db, scripted_llm):
    kb = FakeKnowledgeBase([
        make_chunk(meta={"table": "fact_market_share_weekly"}, distance=0.2),
        make_chunk(chunk_id="ddl:x", source_doc="ddl_index", meta={"table": "dim_competitor"}, distance=0.4),
    ])
    tools = build_tools(fake_db, scripted_llm, Settings(), kb)
    result = tools["search_knowledge"].invoke({"question": "market share"})

    assert result["error"] is None
    assert result["tables"] == ["fact_market_share_weekly", "dim_competitor"]
    assert "Market share fan-out" in result["context"]
    assert [c["source_doc"] for c in result["chunks"]] == ["business_index", "ddl_index"]
    assert result["chunks"][0]["distance"] == 0.2


def test_search_knowledge_reports_an_unreachable_store_instead_of_raising(fake_db, scripted_llm):
    kb = FakeKnowledgeBase(error="connection refused")
    tools = build_tools(fake_db, scripted_llm, Settings(), kb)
    result = tools["search_knowledge"].invoke({"question": "q"})
    assert result["context"] == ""
    assert result["tables"] == []
    assert "connection refused" in result["error"]


def test_search_knowledge_without_a_knowledge_base_reports_disabled(fake_db, scripted_llm):
    tools = build_tools(fake_db, scripted_llm, Settings())
    result = tools["search_knowledge"].invoke({"question": "q"})
    assert result["context"] == ""
    assert result["error"] == "retrieval is disabled"


def test_search_knowledge_honors_the_configured_top_k(fake_db, scripted_llm):
    kb = FakeKnowledgeBase()
    tools = build_tools(fake_db, scripted_llm, Settings(rag_top_k=7), kb)
    tools["search_knowledge"].invoke({"question": "q"})
    assert kb.search_calls == [("q", 7)]


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


def test_search_snippets_returns_the_snippets_and_small_summaries(fake_db, scripted_llm):
    library = FakeSnippetLibrary([make_snippet()], warning="meaning skipped: x")
    tools = build_tools(fake_db, scripted_llm, Settings(snippets_top_k=2), snippet_library=library)
    result = tools["search_snippets"].invoke({"question": "net sales"})
    assert [s.snippet_id for s in result["snippets"]] == ["S19"]
    assert result["hits"][0]["found_by"] == "keywords: net sales; meaning 0.550"
    assert (result["warning"], result["error"]) == ("meaning skipped: x", None)
    assert library.find_calls == [("net sales", 2)]


def test_search_snippets_reports_an_unreachable_store_and_a_missing_library(fake_db, scripted_llm):
    down = build_tools(fake_db, scripted_llm, Settings(), snippet_library=FakeSnippetLibrary(error="refused"))
    assert down["search_snippets"].invoke({"question": "q"}) == {
        "snippets": [], "hits": [], "warning": None, "error": "refused"
    }
    off = build_tools(fake_db, scripted_llm, Settings())
    assert off["search_snippets"].invoke({"question": "q"})["error"] == "snippets are disabled"
