"""The seam between the pipeline's state and the published contract.

`state.py` is internal and changes with the architecture; `models.py` is what
other people's code is compiled against. This file is the only place that
knows both, so it is the only place that can catch the drift -- a renamed
field, a dataclass that gained a member the API never exposes, a cell type
that JSON cannot encode.

The property that matters most is totality: every list is a list and every
absent value has a defined stand-in, so a GUI never has to null-check before
rendering.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from nl2sql_agent.api.jobs import Job, ProgressRecord
from nl2sql_agent.api.translate import answer_from_state, job_model, progress_event, result_table
from nl2sql_agent.state import (
    AuditReport,
    ChartSpec,
    Claim,
    LiteralMatch,
    QueryResult,
    TraceEntry,
    new_state,
)


def full_state() -> dict:
    state = new_state("how many stores?")
    state.update(
        {
            "verdict": "proceed",
            "intent": "aggregate",
            "selected_tables": ["dim_store"],
            "sql": "SELECT count(*) AS n FROM dim_store",
            "attempts": 2,
            "plan_cost": 31.25,
            "result": QueryResult(columns=["n"], rows=[[42]], truncated=False),
            "chart": ChartSpec(kind="bar", x="n", y=["n"]),
            "claims": [Claim(text="42 stores", value=42.0, cells=[(0, "n")])],
            "narrative": "  There are 42 stores.  ",
            "audit": AuditReport(passed=True, drop_reasons=["one dropped"]),
            "literal_map": [
                LiteralMatch(phrase="produse", table="dim_product", column="dept",
                             value="Produce", score=0.81)
            ],
            "trace": [TraceEntry(node="finish", ms=2.5, model_calls=0, detail="done")],
            "retrieval_errors": {"knowledge": "vector store unreachable"},
            "answer": "There are 42 stores.",
        }
    )
    return state


# ---------------------------------------------------------------------------
# Totality
# ---------------------------------------------------------------------------


def test_an_empty_state_translates_to_a_complete_document():
    """A GUI renders `answer.tables.map(...)` without checking first."""
    answer = answer_from_state(None)
    assert answer.tables == [] and answer.literals == [] and answer.claims == []
    assert answer.trace == [] and answer.retrieval_errors == {}
    assert answer.result is None and answer.chart is None
    assert answer.audit.passed is True
    assert answer.verdict == "proceed" and answer.attempts == 0


def test_every_field_of_a_full_state_survives_the_crossing():
    answer = answer_from_state(full_state())
    assert answer.sql.startswith("SELECT count(*)")
    assert answer.tables == ["dim_store"]
    assert answer.plan_cost == 31.25 and answer.attempts == 2
    assert answer.chart is not None and answer.chart.kind == "bar"
    assert answer.claims[0].cells == [[0, "n"]]
    assert answer.literals[0].value == "Produce"
    assert answer.audit.drop_reasons == ["one dropped"]
    assert answer.trace[0].node == "finish"
    assert answer.retrieval_errors == {"knowledge": "vector store unreachable"}


def test_the_narrative_is_trimmed_because_it_is_rendered_verbatim():
    assert answer_from_state(full_state()).narrative == "There are 42 stores."


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def test_database_types_json_cannot_encode_become_strings():
    """A `Decimal` or a `date` out of Postgres must not be discovered by the
    client at the very end of a successful minute-long run.
    """
    state = new_state("q")
    state["result"] = QueryResult(
        columns=["amount", "day"], rows=[[Decimal("1234.56"), dt.date(2025, 3, 1)]]
    )
    table = answer_from_state(state).result
    assert table is not None
    assert table.rows == [["1234.56", "2025-03-01"]]


def test_the_row_count_is_computed_rather_than_trusted():
    state = new_state("q")
    state["result"] = QueryResult(columns=["n"], rows=[[1], [2], [3]], truncated=True)
    table = answer_from_state(state).result
    assert table is not None and table.row_count == 3 and table.truncated is True


def test_rows_that_arrived_as_a_plain_dict_translate_too():
    """The benchmark and older callers hand over dictionaries rather than the
    dataclass, and both reach this code through the job store.
    """
    table = result_table({"columns": ["n"], "rows": [[1]], "truncated": False})
    assert table is not None and table.columns == ["n"]


def test_no_rows_is_not_an_empty_table():
    """A refusal never ran a query. `result: null` says that; an empty table
    would say the query ran and matched nothing.
    """
    assert result_table(None) is None
    assert answer_from_state({"verdict": "out_of_domain", "answer": "no"}).result is None


def test_something_that_is_not_a_result_at_all_is_dropped_rather_than_crashing():
    assert result_table("not a result") is None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_refusal_is_the_same_shape_as_an_answer():
    """The client parses one document, not two, and learns what happened
    from `verdict` rather than from the response's shape.
    """
    state = new_state("what is the capital of France?")
    state.update({"verdict": "out_of_domain", "answer": "I can't answer that", "sql": ""})
    answer = answer_from_state(state)
    assert answer.verdict == "out_of_domain"
    assert answer.answer.startswith("I can't")
    assert answer.result is None and answer.sql == ""


def test_a_clarification_reaches_the_client():
    state = new_state("how many?")
    state.update({"verdict": "ambiguous", "clarification": "how many of what?"})
    assert answer_from_state(state).clarification == "how many of what?"


# ---------------------------------------------------------------------------
# Progress and jobs
# ---------------------------------------------------------------------------


def test_a_progress_record_gains_the_human_label_of_its_node():
    """The graph's node names are the contract; the labels are what a person
    reads. Both go on the wire so a GUI can key off one and display the other.
    """
    record = ProgressRecord(seq=3, step="generate_sql", detail="writing", at=dt.datetime.now(dt.timezone.utc))
    event = progress_event(record)
    assert (event.seq, event.step, event.label) == (3, "generate_sql", "sql")


def test_an_unknown_node_falls_back_to_its_own_name():
    record = ProgressRecord(seq=1, step="something_new", detail="", at=dt.datetime.now(dt.timezone.utc))
    assert progress_event(record).label == "something_new"


def test_a_job_carries_the_two_urls_the_client_needs_next():
    job = Job(id="abc", question="q")
    model = job_model(job)
    assert model.links.self == "/v1/questions/abc"
    assert model.links.events == "/v1/questions/abc/events"


def test_the_links_follow_a_reverse_proxys_path_prefix():
    model = job_model(Job(id="abc", question="q"), base="/nl2sql")
    assert model.links.self == "/nl2sql/v1/questions/abc"


def test_a_job_that_has_not_finished_carries_no_answer():
    """`answer: null` is how a polling client knows there is nothing to draw
    yet, without inspecting the status string.
    """
    assert job_model(Job(id="a", question="q")).answer is None


def test_a_finished_job_carries_the_whole_answer_and_its_metadata():
    job = Job(id="a", question="q", metadata={"turn": "7"}, state=full_state(), status="succeeded")
    model = job_model(job)
    assert model.metadata == {"turn": "7"}
    assert model.answer is not None and model.answer.tables == ["dim_store"]
