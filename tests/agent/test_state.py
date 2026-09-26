"""The shared state contract: the interface that makes the agents testable.

The architecture's stated advantage over the v3 pipeline is that every agent
is independently unit-testable. That is only true if the message between them
is real, so this file tests the contract itself: the reducers that let Stage
1's parallel branches write at the same time, the cell lookup the Audit
Checker verifies claims against, and the JSON conversion at the CLI boundary.
"""

from __future__ import annotations

import json
import operator
from typing import get_args, get_origin, get_type_hints

import pytest
from nl2sql_agent.state import (
    AgentState,
    Attempt,
    AuditReport,
    ChartSpec,
    Claim,
    Issue,
    LiteralMatch,
    QueryResult,
    Shot,
    TraceEntry,
    merge_errors,
    new_state,
    to_jsonable,
)

# ---------------------------------------------------------------------------
# The reducers Stage 1's fan-out depends on
# ---------------------------------------------------------------------------


def _reducer_for(field: str):
    """The reducer LangGraph will apply to a field, or None if it overwrites."""
    hints = get_type_hints(AgentState, include_extras=True)
    annotation = hints[field]
    if get_origin(annotation) is not None and hasattr(annotation, "__metadata__"):
        return annotation.__metadata__[0]
    return None


def test_the_two_fields_every_branch_writes_have_reducers():
    """Four retrievers run as branches of one superstep. Without a reducer on
    a field two of them write, LangGraph rejects the concurrent update and the
    whole fan-out fails at runtime rather than in a test.
    """
    assert _reducer_for("retrieval_errors") is merge_errors
    assert _reducer_for("trace") is operator.add


def test_fields_only_one_agent_owns_deliberately_overwrite():
    """A reducer where it is not needed is worse than none: `sql` must be
    replaced by each generation, not accumulated across retries.
    """
    for field in ("sql", "schema", "selected_tables", "attempts", "issues"):
        assert _reducer_for(field) is None, f"{field} should overwrite, not merge"


def test_merging_two_retrievers_failures_keeps_both():
    assert merge_errors({"knowledge": "down"}, {"examples": "no pairs"}) == {
        "knowledge": "down",
        "examples": "no pairs",
    }


def test_merging_tolerates_a_branch_that_recorded_nothing():
    assert merge_errors({}, {"literals": "no catalog"}) == {"literals": "no catalog"}
    assert merge_errors({"literals": "no catalog"}, {}) == {"literals": "no catalog"}


def test_appending_traces_concatenates_rather_than_replacing():
    """Per-agent timing is the benchmark's whole way of attributing cost, so a
    node whose entry was overwritten by a sibling would be invisible.
    """
    left = [TraceEntry(node="retrieve_knowledge", ms=1.0)]
    right = [TraceEntry(node="retrieve_examples", ms=2.0)]
    assert [e.node for e in operator.add(left, right)] == [
        "retrieve_knowledge",
        "retrieve_examples",
    ]


# ---------------------------------------------------------------------------
# A run's starting point
# ---------------------------------------------------------------------------


def test_a_new_state_seeds_every_collection_a_node_might_read():
    """Nodes read these without a default. Seeding them here is what keeps
    each node's reads total, and total reads are what make a node testable on
    its own.
    """
    state = new_state("how many stores?")
    assert state["question"] == "how many stores?"
    assert state["attempts"] == 0
    assert state["issues"] == []
    assert state["attempt_history"] == []
    assert state["retrieval_errors"] == {}
    assert state["trace"] == []


def test_a_new_state_proceeds_by_default_so_the_supervisor_can_be_disabled():
    state = new_state("how many stores?")
    assert state["verdict"] == "proceed"
    assert state["intent"] == "aggregate"


def test_a_principal_is_carried_for_row_level_security():
    assert new_state("q", principal="analyst_jo")["principal"] == "analyst_jo"
    assert new_state("q")["principal"] is None


# ---------------------------------------------------------------------------
# The result set the audit verifies claims against
# ---------------------------------------------------------------------------


@pytest.fixture
def result() -> QueryResult:
    return QueryResult(
        columns=["department_name", "gross_margin_pct"],
        rows=[["Dairy & Eggs", 31.4], ["Produce", 29.3]],
    )


def test_a_cell_is_addressed_by_row_index_and_column_name(result: QueryResult):
    assert result.cell(0, "gross_margin_pct") == 31.4
    assert result.cell(1, "department_name") == "Produce"


def test_addressing_a_column_that_is_not_there_says_so(result: QueryResult):
    """The narrator writes these coordinates, so a wrong one has to fail
    loudly enough for the audit to drop the claim rather than silently
    verifying against the wrong number.
    """
    with pytest.raises(KeyError, match="margin_pct_total"):
        result.cell(0, "margin_pct_total")


def test_addressing_a_row_past_the_end_says_so(result: QueryResult):
    with pytest.raises(IndexError, match="no row 7"):
        result.cell(7, "gross_margin_pct")


def test_row_count_and_dict_rendering(result: QueryResult):
    assert result.row_count == 2
    assert result.to_dicts()[0] == {"department_name": "Dairy & Eggs", "gross_margin_pct": 31.4}


def test_an_empty_result_is_usable_rather_than_special(result: QueryResult):
    empty = QueryResult()
    assert empty.row_count == 0
    assert empty.to_dicts() == []


# ---------------------------------------------------------------------------
# Rendering into prompts and out to JSON
# ---------------------------------------------------------------------------


def test_a_literal_match_renders_the_way_the_generator_prompt_shows_it():
    match = LiteralMatch("dairy and eggs", "dim_product", "department_name", "Dairy & Eggs", 0.82)
    assert match.render() == "\"dairy and eggs\" -> dim_product.department_name = 'Dairy & Eggs'"


def test_an_issue_renders_its_source_and_its_fix():
    issue = Issue(source="planner", message='column "x" does not exist', hint="use x_amt")
    rendered = issue.render()
    assert "[planner]" in rendered
    assert "does not exist" in rendered
    assert "fix: use x_amt" in rendered


def test_an_issue_with_no_hint_yet_renders_without_an_empty_fix_line():
    assert "fix:" not in Issue(source="static", message="nope").render()


def test_the_whole_state_survives_the_json_boundary():
    """`--json` and the benchmark's trace files both go through this, and a
    run that succeeded and then failed to serialise would be the worst
    possible time to find out.
    """
    state = new_state("q")
    state.update(
        {
            "literal_map": [LiteralMatch("a", "t", "c", "v", 0.9)],
            "example_shots": [Shot("B01", "q", "rule", "SELECT 1")],
            "issues": [Issue(source="static", message="m", hint="h")],
            "attempt_history": [Attempt(sql="SELECT 1", issues=[Issue(source="runtime", message="m")])],
            "result": QueryResult(columns=["n"], rows=[[1]]),
            "chart": ChartSpec(kind="bar", x="dept", y=["total"]),
            "claims": [Claim(text="t", value=1.0, cells=[(0, "n")], formula=None)],
            "audit": AuditReport(passed=True),
            "trace": [TraceEntry(node="generate_sql", ms=12.5, model_calls=1)],
        }
    )
    encoded = json.dumps(to_jsonable(state))
    assert '"phrase": "a"' in encoded
    assert '"node": "generate_sql"' in encoded


def test_values_the_json_encoder_cannot_take_become_strings_rather_than_raising():
    """Postgres hands back Decimal and date objects. A successful run must not
    die at the very last step because one of them reached the encoder.
    """
    from datetime import date
    from decimal import Decimal

    encoded = to_jsonable(QueryResult(columns=["d", "amt"], rows=[[date(2025, 1, 1), Decimal("3.50")]]))
    assert encoded["rows"] == [["2025-01-01", "3.50"]]
    json.dumps(encoded)


def test_a_claims_cells_survive_as_pairs_not_as_a_flattened_list():
    """The audit re-reads these coordinates to verify the claim, so a tuple
    that flattened into two loose values would silently unpair rows from
    columns.
    """
    encoded = to_jsonable(Claim(text="t", value=2.1, cells=[(0, "a"), (1, "b")]))
    assert encoded["cells"] == [[0, "a"], [1, "b"]]


# ---------------------------------------------------------------------------
# The contract matches the document it comes from
# ---------------------------------------------------------------------------


def test_every_field_the_architecture_names_exists_in_the_state():
    """Section 3 of the architecture document is the contract; drift between
    it and this class is how the documents got into the state the v4 rewrite
    was needed to fix.
    """
    documented = {
        "question", "principal", "intent", "verdict", "clarification",
        "selected_tables", "schema", "literal_map", "knowledge", "knowledge_tables",
        "example_shots", "example_tables", "retrieval_errors", "sql", "attempts",
        "issues", "attempt_history", "plan_cost", "result", "chart", "claims",
        "narrative", "audit", "answer", "error", "trace",
    }
    assert documented <= set(get_type_hints(AgentState, include_extras=True))


def test_the_five_issue_sources_are_the_five_the_repair_loop_handles():
    """Every failure routes to the Repair Agent under one budget. A sixth
    source would be a path out of the loop that nothing counts. The fifth,
    `completeness`, is arch5's: the one a query that ran correctly can raise.
    """
    from nl2sql_agent.state import AUDIT, COMPLETENESS, PLANNER, RUNTIME, STATIC, IssueSource

    assert set(get_args(IssueSource)) == {STATIC, PLANNER, RUNTIME, COMPLETENESS, AUDIT}
