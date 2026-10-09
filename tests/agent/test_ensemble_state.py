"""The ensemble's own state (arch7 section 22.2): its fields, their lifetimes,
the helpers that read the delivered run out of it, and its rendering.

Held as `test_state.py` holds the pipeline's: a field without a lifetime, a
lifetime naming no field, or a reset that empties something it should not,
is a failing test rather than a vote that reads the wave before.
"""

from __future__ import annotations

import json
import operator
from dataclasses import replace
from typing import Annotated, get_args, get_origin, get_type_hints

import pytest
from nl2sql_agent.ensemble_state import (
    GroupVerdict,
    Judgement,
    judged,
    LIFETIMES,
    WAVE,
    Agreement,
    Candidate,
    Decision,
    EnsembleState,
    chosen,
    ensemble_fields,
    is_ensemble,
    new_ensemble_state,
    run_state,
    upsert_candidates,
    wave_reset,
    whole_trace,
)
from nl2sql_agent.state import RUN, QueryResult, TraceEntry, merge_errors, new_state, to_jsonable

from .conftest import FakeDatabase
from .test_graph import TABLES, make_agent, scripted


def _candidate(index: int, **state) -> Candidate:
    return Candidate(index=index, wording=f"wording {index}", origin="original" if index == 0 else "paraphrase",
                     wave=1, state={**new_state(f"wording {index}"), **state}, outcome="answered")


def test_every_field_has_a_lifetime_and_no_lifetime_names_a_missing_field():
    assert set(LIFETIMES) == set(ensemble_fields())
    assert set(LIFETIMES.values()) == {RUN, WAVE}


def test_the_vote_and_what_was_decided_from_it_are_one_waves():
    """A second wave's vote is over every candidate, the first wave's
    included; the groups, agreement and decision before it describe a vote
    that is about to be taken again."""
    assert {name for name, lifetime in LIFETIMES.items() if lifetime == WAVE} == set(wave_reset())
    assert wave_reset() == {
        "wave_plan": [], "groups": [], "agreement": Agreement(), "judgement": None, "decision": Decision(),
    }
    assert wave_reset()["agreement"] is not wave_reset()["agreement"]


def test_a_new_state_seeds_every_field():
    assert set(new_ensemble_state("q")) == set(ensemble_fields())
    state = new_ensemble_state("q", principal="jo", deadline=12.5, parallel_calls=2)
    assert (state["principal"], state["deadline"], state["parallel_calls"]) == ("jo", 12.5, 2)
    assert state["candidates"] == [] and state["waves"] == 0


def test_the_keys_several_writers_share_have_reducers():
    hints = get_type_hints(EnsembleState, include_extras=True)
    reducers = {name: get_args(hint)[1] for name, hint in hints.items() if get_origin(hint) is Annotated}
    assert reducers == {"candidates": upsert_candidates, "node_errors": merge_errors, "trace": operator.add}


def test_a_wave_appends_its_runs_and_the_vote_marks_them_where_they_stand():
    """`answer` adds candidates and `validate` marks them; a run comes back
    with its own index and replaces itself, and none is ever removed."""
    first = [_candidate(0), _candidate(1)]
    marked = [replace(first[1], admissible=True, group=0)]
    merged = upsert_candidates(first, marked)
    assert [c.index for c in merged] == [0, 1] and merged[1].admissible and merged[1].group == 0
    assert merged[0] is first[0]
    later = upsert_candidates(merged, [_candidate(2)])
    assert [c.index for c in later] == [0, 1, 2]
    assert upsert_candidates(None, [_candidate(0)])[0].index == 0
    assert upsert_candidates(first, None) == first


def test_the_delivered_run_is_the_decisions_and_otherwise_the_originals():
    state = {**new_ensemble_state("q"), "candidates": [_candidate(1, sql="B"), _candidate(0, sql="A")]}
    assert chosen(state).index == 0  # nothing decided: the original's
    state["decision"] = Decision(chosen=1)
    assert chosen(state).index == 1
    assert run_state(state)["sql"] == "B"
    assert chosen(new_ensemble_state("q")) is None
    assert run_state(new_ensemble_state("q")) == {}


def test_a_single_runs_state_is_its_own_record():
    state = new_state("q")
    assert not is_ensemble(state)
    assert run_state(state) is state
    assert whole_trace({**state, "trace": [TraceEntry(node="finish")]}) == [TraceEntry(node="finish")]
    assert not is_ensemble(None)


def test_the_whole_trace_lists_every_node_once():
    """The outer node that ran the candidates is their time, so it is left
    out where their own nodes are counted; plain dicts are read too."""
    outer = [TraceEntry(node="screen", model_calls=1), {"node": "answer", "ms": 900.0}, TraceEntry(node="deliver")]
    state = {
        **new_ensemble_state("q"),
        "trace": outer,
        "candidates": [_candidate(1, trace=[TraceEntry(node="finish")]), _candidate(0, trace=[TraceEntry(node="generate_sql")])],
    }
    assert [e.node if isinstance(e, TraceEntry) else e["node"] for e in whole_trace(state)] == [
        "screen", "deliver", "generate_sql", "finish",
    ]


def test_a_candidate_with_a_real_run_inside_renders_whole():
    """R3: the state in the state. A remembered job holds every candidate's
    run, and `--json` and the wire render it -- dataclasses inside a dict
    inside a dataclass."""
    run = make_agent(FakeDatabase(tables=TABLES), scripted(["SELECT count(*) AS n FROM dim_store"])).run("how many?")
    candidate = Candidate(index=0, wording="how many?", origin="original", wave=1, state=run, outcome="answered")
    rendered = to_jsonable(candidate)
    assert rendered["state"]["result"] == to_jsonable(QueryResult(columns=["n"], rows=[[1]]))
    assert rendered["state"]["trace"][0]["node"] == "supervise"
    assert json.loads(json.dumps(rendered)) == rendered


@pytest.mark.parametrize(("judgement", "said"), [
    (None, "not asked"),
    (Judgement(error="ConnectionError: refused"), "failed"),
    (Judgement(verdicts=[GroupVerdict(0, False)], set_aside=[0, 1]), "accepted none"),
    (Judgement(verdicts=[GroupVerdict(0, False), GroupVerdict(1, True)], set_aside=[1], overruled=True), "overruled"),
    (Judgement(verdicts=[GroupVerdict(0, True), GroupVerdict(1, False)], set_aside=[3]), "set aside"),
    (Judgement(verdicts=[GroupVerdict(0, True)]), "accepted"),
])
def test_what_the_judge_did_in_a_word(judgement, said):
    assert judged(judgement) == said
