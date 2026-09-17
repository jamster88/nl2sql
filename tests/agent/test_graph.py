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

from .conftest import (
    FakeDatabase,
    FakeGoldenPairLibrary,
    FakeKnowledgeBase,
    ScriptedLLM,
    make_chunk,
    make_pair,
)


def make_agent(
    db: FakeDatabase,
    llm: ScriptedLLM,
    knowledge_base: FakeKnowledgeBase | None = None,
    example_library: FakeGoldenPairLibrary | None = None,
    **settings_kwargs,
) -> Nl2SqlAgent:
    settings = Settings(database_url="postgresql+psycopg://u:p@127.0.0.1:1/db", **settings_kwargs)
    agent = Nl2SqlAgent(
        settings, llm=llm, knowledge_base=knowledge_base, example_library=example_library
    )
    agent.db = db
    agent.tools = build_tools(db, llm, settings, knowledge_base, example_library)
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
    assert steps == [
        "retrieve_knowledge", "retrieve_examples", "select_tables", "fetch_schema",
        "generate_sql", "validate_sql", "execute_query",
    ]


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
# Retrieval (v2)
# ---------------------------------------------------------------------------


def test_retrieved_knowledge_reaches_generation_and_validation_prompts():
    """The whole point of v2: the business rule that was retrieved has to show
    up in the text the model actually sees, not just in state.
    """
    db = FakeDatabase(tables=["fact_market_share_weekly", "dim_date"])
    kb = FakeKnowledgeBase([
        make_chunk(
            heading_path="Business Index > Market share fan-out: the five-row trap",
            content="Never SUM total_market_sales_amount across competitor rows.",
            meta={"table": "fact_market_share_weekly"},
        )
    ])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["fact_market_share_weekly"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, kb)

    state = agent.run("What is our market share?")

    assert "Never SUM total_market_sales_amount" in state["knowledge"]
    assert kb.search_calls == [("What is our market share?", 4)]

    generation_prompt = "\n".join(
        getattr(m, "content", str(m)) for m in llm.plain_invocations[0]
    )
    assert "Never SUM total_market_sales_amount" in generation_prompt
    assert "Knowledge base" in generation_prompt

    # The validator sees it too, so it can catch a rule violation the planner
    # would happily accept.
    validation_prompt = "\n".join(
        getattr(m, "content", str(m))
        for schema, messages in llm.structured_invocations
        if schema is SqlReview
        for m in messages
    )
    assert "Never SUM total_market_sales_amount" in validation_prompt


def test_table_selection_prompt_includes_retrieved_context():
    db = FakeDatabase(tables=["dim_store"])
    kb = FakeKnowledgeBase([make_chunk(content="dim_store holds one row per store.")])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, kb)
    agent.run("how many stores")

    selection_prompt = "\n".join(
        getattr(m, "content", str(m))
        for schema, messages in llm.structured_invocations
        if schema is TableSelection
        for m in messages
    )
    assert "dim_store holds one row per store." in selection_prompt


def test_tables_hinted_by_retrieval_are_added_to_the_models_selection():
    """chunk_meta.table is a strong signal; a table the model missed but the
    knowledge base names explicitly still gets its schema fetched.
    """
    db = FakeDatabase(tables=["fact_market_share_weekly", "dim_competitor", "dim_store"])
    kb = FakeKnowledgeBase([
        make_chunk(meta={"table": "dim_competitor"}, distance=0.1),
        make_chunk(meta={"table": "fact_market_share_weekly"}, distance=0.2),
    ])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["fact_market_share_weekly"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, kb)

    state = agent.run("market share by competitor")

    assert state["selected_tables"] == ["fact_market_share_weekly", "dim_competitor"]
    assert db.schema_and_samples_calls[0][0] == ["fact_market_share_weekly", "dim_competitor"]


def test_hinted_tables_that_do_not_exist_are_ignored():
    db = FakeDatabase(tables=["dim_store"])
    kb = FakeKnowledgeBase([make_chunk(meta={"table": "table_from_an_older_schema"})])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, kb)
    state = agent.run("q")
    assert state["selected_tables"] == ["dim_store"]


def test_pipeline_still_answers_when_the_knowledge_base_is_unreachable(progress_log):
    """Retrieval is best-effort: an unreachable vector store degrades the run
    to v1 behavior instead of failing the question.
    """
    db = FakeDatabase(tables=["dim_store"])
    kb = FakeKnowledgeBase(error="connection refused")
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT COUNT(*) FROM dim_store"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, kb)
    agent._on_progress = progress_log

    state = agent.run("How many stores are there?")

    assert state.get("error") is None
    assert state["result"]["row_count"] == 1
    assert state["knowledge"] == ""
    assert "connection refused" in state["retrieval_error"]
    assert any(step == "retrieve_knowledge" and "skipped" in detail for step, detail in progress_log.log)

    # And no empty "Knowledge base" heading is left dangling in the prompt.
    generation_prompt = "\n".join(
        getattr(m, "content", str(m)) for m in llm.plain_invocations[0]
    )
    assert "Knowledge base (retrieved" not in generation_prompt


def test_rag_disabled_skips_retrieval_entirely(progress_log):
    db = FakeDatabase(tables=["dim_store"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT COUNT(*) FROM dim_store"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, None, rag_enabled=False)

    state = agent.run("How many stores are there?")

    assert state.get("error") is None
    assert state["knowledge"] == ""
    assert agent.knowledge_base is None


def test_rag_top_k_setting_is_passed_through_to_the_knowledge_base():
    db = FakeDatabase(tables=["dim_store"])
    kb = FakeKnowledgeBase()
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, kb, rag_top_k=9)
    agent.run("q")
    assert kb.search_calls == [("q", 9)]


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


# ---------------------------------------------------------------------------
# Knowledge base construction
# ---------------------------------------------------------------------------


def test_agent_builds_a_knowledge_base_from_settings_when_rag_is_on():
    from nl2sql_agent.retrieval import KnowledgeBase

    settings = Settings(
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db",
        vector_db_url="postgresql+psycopg://ragproc:ragproc@127.0.0.1:1/nl2sql_vectors",
        rag_top_k=5,
        embed_model="bge-m3",
        embed_base_url="http://embedhost:11434",
    )
    agent = Nl2SqlAgent(settings, llm=ScriptedLLM())

    assert isinstance(agent.knowledge_base, KnowledgeBase)
    assert agent.knowledge_base._top_k == 5
    assert agent.knowledge_base._embedder.model == "bge-m3"
    assert agent.knowledge_base._embedder.base_url == "http://embedhost:11434"


def test_building_the_agent_never_connects_to_the_vector_store():
    """Construction must stay lazy: an unreachable knowledge base should
    degrade a run, not stop the agent from starting.
    """
    settings = Settings(
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db",
        vector_db_url="postgresql+psycopg://u:p@127.0.0.1:1/nothing_here",
    )
    agent = Nl2SqlAgent(settings, llm=ScriptedLLM())  # must not raise
    assert agent.knowledge_base is not None


def test_an_explicit_knowledge_base_overrides_the_settings_built_one():
    kb = FakeKnowledgeBase()
    settings = Settings(database_url="postgresql+psycopg://u:p@127.0.0.1:1/db")
    agent = Nl2SqlAgent(settings, llm=ScriptedLLM(), knowledge_base=kb)
    assert agent.knowledge_base is kb


def test_the_retrieval_tools_are_registered_alongside_the_original_four():
    agent = make_agent(
        FakeDatabase(), ScriptedLLM(), FakeKnowledgeBase(), FakeGoldenPairLibrary()
    )
    assert set(agent.tools) == {
        "describe_all_tables", "get_schema_and_data", "search_knowledge",
        "search_examples", "validate_sql", "execute_query",
    }


# ---------------------------------------------------------------------------
# v3: the golden-pair ensemble in the pipeline
# ---------------------------------------------------------------------------


def test_retrieved_examples_land_in_state_with_their_provenance(progress_log):
    db = FakeDatabase(tables=["dim_store"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    library = FakeGoldenPairLibrary()
    agent = make_agent(db, llm, FakeKnowledgeBase(), library)
    agent._on_progress = progress_log

    state = agent.run("how is market share computed")

    assert library.search_calls == [("how is market share computed", 3)]
    assert state["example_pairs"][0]["pair_id"] == "Q10"
    assert state["example_pairs"][0]["found_by"] == "keywords#2, question#1, reasoning#3"
    assert "SELECT DISTINCT week_key" in state["examples"]
    assert ("retrieve_examples", "1 pair(s) -- Q10 (0.870)") in progress_log.log


def test_an_unavailable_example_store_is_recorded_and_the_run_continues(progress_log):
    """Three separate things can be down -- the context store, the vector store,
    the embedding host -- and none of them should cost more than the examples.
    """
    db = FakeDatabase(tables=["dim_store"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    library = FakeGoldenPairLibrary(error="context store is down")
    agent = make_agent(db, llm, FakeKnowledgeBase(), library)
    agent._on_progress = progress_log

    state = agent.run("how many stores")

    assert state.get("error") is None
    assert state["result"]["row_count"] == 1
    assert state["examples"] == ""
    assert state["examples_error"] == "context store is down"
    assert ("retrieve_examples", "skipped: context store is down") in progress_log.log


def test_tables_an_example_queries_are_added_to_the_selection():
    """A worked example that already answers a question of this shape knows
    which tables the answer needs -- a signal the catalog alone does not carry.
    """
    db = FakeDatabase(tables=["dim_store", "fact_market_share_weekly", "dim_geography"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    library = FakeGoldenPairLibrary(
        [make_pair(tables="fact_market_share_weekly, dim_geography, not_a_real_table")]
    )
    agent = make_agent(db, llm, FakeKnowledgeBase(chunks=[]), library)

    state = agent.run("market share by region")

    assert state["selected_tables"] == ["dim_store", "fact_market_share_weekly", "dim_geography"]
    assert "not_a_real_table" not in state["selected_tables"]


def test_examples_are_withheld_from_the_prompt_until_multi_shot_is_on():
    """Retrieval and prompting are separate switches. With multi-shot off the
    pairs are still retrieved and inspectable, but the generator must not see
    them -- otherwise the next step has already happened by accident.
    """
    db = FakeDatabase(tables=["dim_store"])

    def run(multi_shot: bool) -> str:
        llm = ScriptedLLM(
            table_selection=TableSelection(tables=["dim_store"]),
            sql_responses=["SELECT 1"],
            sql_reviews=[SqlReview(is_valid=True, issues=[])],
        )
        agent = make_agent(
            db, llm, FakeKnowledgeBase(), FakeGoldenPairLibrary(), multi_shot_enabled=multi_shot
        )
        state = agent.run("how many stores")
        assert state["examples"], "examples should be retrieved either way"
        # The generation prompt is the second plain invocation-free call: the
        # table selection is structured, so invoke() only sees generation.
        return "\n".join(str(m.content) for m in llm.plain_invocations[0])

    assert "Worked examples" not in run(multi_shot=False)
    assert "Worked examples" in run(multi_shot=True)


def test_examples_disabled_skips_the_step_but_keeps_the_node():
    """The node stays in the graph so the pipeline shape is constant; only its
    output is empty. A conditional node would make the diagram version-dependent.
    """
    db = FakeDatabase(tables=["dim_store"])
    llm = ScriptedLLM(
        table_selection=TableSelection(tables=["dim_store"]),
        sql_responses=["SELECT 1"],
        sql_reviews=[SqlReview(is_valid=True, issues=[])],
    )
    agent = make_agent(db, llm, FakeKnowledgeBase(), None, examples_enabled=False)
    state = agent.run("how many stores")
    assert state["examples"] == ""
    assert state["examples_error"] == "examples are disabled"
    assert state.get("error") is None
