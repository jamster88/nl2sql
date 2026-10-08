"""The pipeline's MLflow trace, on the same fakes as test_graph.py.

The trace is the architecture drawn from a real run: a span per agent, named
as the architecture names it, and inside each the model calls it made. These
tests read that tree back and hold it to the run it records -- the five
retrievers under the run although they ran on other threads, a repair as a
second generation, a routed call that fell back as two calls -- and to the
state's own trace entries, which come from the same wrapper and must agree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from nl2sql_agent import graph as graph_module
from nl2sql_agent.config import Settings
from nl2sql_agent.graph import STEP_LABELS, TRACE_SPANS
from nl2sql_agent.tools import TableSelection
from nl2sql_agent.tracing import Tracer

from tests.fake_mlflow import FakeMlflow

from .conftest import FakeDatabase
from .test_graph import TABLES, make_agent, scripted
from .test_graph_routing import ANCHOR, Down

RETRIEVERS = [
    "Schema Retriever", "Literal Matcher", "Knowledge Retriever", "Example Retriever", "Snippet Retriever",
]


def traced(**settings) -> tuple[Tracer, FakeMlflow]:
    client = FakeMlflow()
    tracer = Tracer(
        Settings(mlflow_tracking_uri="http://mlflow.test:5000"), client=client, probe=lambda uri: None
    )
    return tracer, client


def test_every_node_has_a_span_and_every_span_a_node():
    source = Path(graph_module.__file__).read_text()
    registered = set(re.findall(r'graph\.add_node\("([a-z_]+)"', source))
    assert set(TRACE_SPANS) == registered == set(STEP_LABELS)


@pytest.mark.parametrize("node", sorted(TRACE_SPANS))
def test_a_span_reads_only_what_the_state_holds(node):
    from nl2sql_agent.state import AgentState

    _, span_type, reads = TRACE_SPANS[node]
    assert span_type in {"AGENT", "RETRIEVER", "TASK", "GUARDRAIL", "TOOL", "EVALUATOR"}
    assert set(reads) <= set(AgentState.__annotations__)


def test_a_question_is_one_trace_with_a_span_per_agent_and_each_model_call_inside_its_agent():
    tracer, client = traced()
    state = make_agent(FakeDatabase(tables=TABLES), scripted(["SELECT count(*) AS n FROM dim_store"]),
                       tracer=tracer).run("How many stores are there?")

    trace = client.only_trace()
    assert state["trace_id"] == trace.root.trace_id
    names = [span.name for span in trace.root.children]
    # The retrievers run concurrently, so only their set is fixed; every
    # other agent is in the order the graph ran it.
    assert set(names[1:6]) == set(RETRIEVERS)
    assert [names[0], *names[6:]] == [
        "Supervisor", "Context Aggregator", "SQL Generator", "Static Validator", "Planner Gate",
        "Safe Executor", "Completeness Reviewer", "Visual Formatter", "Insight Narrator",
        "Audit Checker", "Answer",
    ]
    # One span per node the state's own trace records: the two records agree.
    assert len(trace.root.children) == len(state["trace"])

    calls = {span.name: [c.attributes["nl2sql.task"] for c in span.children] for span in trace.root.children}
    assert calls["Supervisor"] == ["supervisor"]
    assert calls["SQL Generator"] == ["generator"]
    assert calls["Insight Narrator"] == ["narrator"]
    assert all(not calls[name] for name in RETRIEVERS + ["Static Validator", "Planner Gate", "Safe Executor"])

    generator = trace.root.child("SQL Generator")
    assert generator.span_type == "AGENT"
    assert generator.inputs["question"] == "How many stores are there?"
    assert generator.outputs["sql"] == "SELECT count(*) AS n FROM dim_store"
    assert generator.attributes["nl2sql.model"] == ANCHOR
    [call] = generator.children
    assert call.name == ANCHOR and call.span_type == "CHAT_MODEL"
    last = call.inputs["messages"][-1]
    assert last["role"] == "user" and "Question: How many stores are there?" in last["content"]

    assert trace.tags["nl2sql.outcome"] == "answered"
    assert trace.tags["nl2sql.attempts"] == "1"
    assert trace.root.outputs["sql"] == "SELECT count(*) AS n FROM dim_store"


def test_a_repair_is_a_second_generation_in_the_same_trace():
    tracer, client = traced()
    llm = scripted(["SELECT 1 AS n FROM dim_storefront", "SELECT count(*) AS n FROM dim_store"])
    state = make_agent(FakeDatabase(tables=TABLES), llm, tracer=tracer).run("q")

    names = [span.name for span in client.only_trace().root.children]
    assert names.count("SQL Generator") == 2
    repair = names.index("Repair Agent")
    assert names[repair - 1] == "Static Validator" and names[repair + 1] == "SQL Generator"
    assert client.only_trace().tags["nl2sql.attempts"] == str(state["attempts"]) == "2"


def test_a_run_that_gives_up_says_so_on_its_trace():
    tracer, client = traced()
    llm = scripted(["SELECT 1 FROM nowhere"] * 2)
    make_agent(FakeDatabase(tables=TABLES), llm, tracer=tracer, max_attempts=2).run("q")
    trace = client.only_trace()
    assert trace.tags["nl2sql.outcome"] == "gave_up"
    assert trace.root.children[-1].name == "Give Up"
    assert trace.root.outputs["error"].startswith("Could not produce a valid query in 2 attempts")


def test_a_routed_call_that_fell_back_is_two_calls_the_first_failed():
    tracer, client = traced()
    anchor = scripted(["SELECT count(*) AS n FROM dim_store"])
    clients = {"broken:7b": Down(), ANCHOR: anchor}
    make_agent(
        FakeDatabase(tables=TABLES), anchor, llm_factory=lambda name, num_ctx: clients[name],
        model_route_generator="broken:7b", tracer=tracer,
    ).run("q")

    generator = client.only_trace().root.child("SQL Generator")
    failed, answered = generator.children
    assert (failed.name, answered.name) == ("broken:7b", ANCHOR)
    assert "not found" in str(failed.error) and answered.error is None
    assert generator.attributes["nl2sql.hops"] == ["broken:7b: model 'broken:7b' not found, try pulling it first"]


def test_v3s_table_selection_call_is_traced_inside_the_schema_retriever():
    tracer, client = traced()
    llm = scripted(["SELECT count(*) AS n FROM dim_store"])
    llm.table_selection = TableSelection(tables=["dim_store"])
    make_agent(FakeDatabase(tables=TABLES), llm, schema_retriever=None, schema_retrieval="llm",
               tracer=tracer).run("q")

    [call] = client.only_trace().root.child("Schema Retriever").children
    assert call.attributes["nl2sql.task"] == "table selection"
    assert call.attributes["nl2sql.structured_output"] == "TableSelection"
    assert call.outputs == {"tables": ["dim_store"]}


def test_an_untraced_run_has_no_trace_id_and_never_touches_mlflow():
    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"MLflow was used ({name})")

    tracer = Tracer(Settings(), client=Untouchable())
    state = make_agent(FakeDatabase(tables=TABLES), scripted(["SELECT count(*) AS n FROM dim_store"]),
                       tracer=tracer).run("q")
    assert state["trace_id"] == ""
    assert state["result"] is not None


def test_the_agent_builds_its_own_tracer_from_its_settings():
    agent = make_agent(FakeDatabase(tables=TABLES), scripted([]), mlflow_tracking_uri="http://elsewhere:5000",
                       mlflow_experiment_name="ablations")
    assert (agent.tracer.uri, agent.tracer.experiment) == ("http://elsewhere:5000", "ablations")


# ---------------------------------------------------------------------------
# One wrapper for both graphs (arch7, R2 of its risks by phase)
# ---------------------------------------------------------------------------


def test_the_wrapper_strips_every_private_key_and_keeps_the_rest():
    """The pipeline's nodes and the ensemble's outer nodes are wrapped by the
    same function, so this is the one check for both: a private key that got
    through would reach the state, the strict wire and the trace."""
    reports = []

    def node(state):
        return {
            "sql": "SELECT 1",
            graph_module._DETAIL: "wrote it",
            graph_module._MODEL_CALLS: 2,
            graph_module._ROUTE: {"model": "m", "rung": "light", "route": "why", "hops": ["a"]},
        }

    wrapped = graph_module.traced_node(
        "generate_sql", node, spans=TRACE_SPANS, progress=lambda: lambda step, detail: reports.append((step, detail))
    )
    update = wrapped({"question": "q"})

    assert set(update) == {"sql", "trace"}
    assert not [key for key in update if key.startswith("_")]
    [entry] = update["trace"]
    assert (entry.node, entry.detail, entry.model_calls, entry.model, entry.hops) == (
        "generate_sql", "wrote it", 2, "m", ["a"])
    assert reports == [("generate_sql", "wrote it")]


def test_a_span_can_record_a_summary_of_what_its_node_wrote():
    tracer, client = traced()
    wrapped = graph_module.traced_node(
        "finish", lambda state: {"answer": "long"}, spans=TRACE_SPANS, progress=lambda: lambda *a: None,
        outputs=lambda update: {"length": len(update["answer"])},
    )
    with tracer.run("q"):
        wrapped({"claims": [], "audit": None, "completeness": None})
    assert client.only_trace().root.child("Answer").outputs == {"length": 4}
