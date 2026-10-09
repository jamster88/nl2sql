"""Admissibility, agreement, the vote and the representative (`agreement.py`;
arch7.1 sections 22.5-22.8).

All on hand-built candidates: what a run must be to vote, when two runs agree,
how they group, what the vote is for every row of arch7's table, how the
Judge's verdicts change it, and which run stands for a group -- each ranking
criterion on its own.
"""

from __future__ import annotations

import pytest
from dataclasses import replace

from nl2sql_agent import agreement
from nl2sql_agent import agreement as agreement_module
from nl2sql_agent.contract import Label, LabelMap
from nl2sql_agent.ensemble_state import Candidate, Group, GroupVerdict, Judgement
from nl2sql_agent.state import (
    AnswerContract,
    AuditReport,
    Claim,
    CompletenessReport,
    MissingColumn,
    QueryResult,
    new_state,
)

LABELS = LabelMap([Label(table="dim_store", key="store_key", label="store_name")])
CONTRACT = AnswerContract()
QUESTION = "how many stores are there in each state?"


def run(index: int, rows=((10,),), *, columns=("n",), outcome="answered", origin=None, **state) -> Candidate:
    result = QueryResult(columns=list(columns), rows=[list(r) for r in rows], truncated=state.pop("truncated", False))
    return Candidate(
        index=index,
        wording=f"wording {index}",
        origin=origin or ("original" if index == 0 else "paraphrase"),
        wave=1,
        state={**new_state(f"wording {index}"), "result": result, "sql": "SELECT 1", "attempts": 1, **state},
        outcome=outcome,
    )


def voters(*values) -> list[Candidate]:
    return [Candidate(**{**vars(run(i, ((v,),))), "admissible": True}) for i, v in enumerate(values)]


# ---------------------------------------------------------------------------
# Tier 1: E1-E5
# ---------------------------------------------------------------------------


def test_a_run_that_answered_with_nothing_wrong_is_admissible():
    assert agreement.admissible(run(0), CONTRACT, LABELS, question=QUESTION) == []


@pytest.mark.parametrize(("outcome", "reason"), [
    ("gave_up", "E1 answered: gave up after 3 attempts"),
    ("refused", "E1 answered: refused"),
])
def test_e1_a_run_that_did_not_answer_cannot_vote(outcome, reason):
    assert agreement.admissible(run(1, outcome=outcome, attempts=3), CONTRACT, LABELS, question=QUESTION) == [reason]


def test_e2_a_wording_the_gate_did_not_pass_cannot_vote():
    reasons = agreement.admissible(run(1), CONTRACT, LABELS, question=QUESTION, faithful=False)
    assert reasons == ["E2 faithful: its wording did not pass the fidelity gate"]


def test_e3_no_rows_for_a_question_that_implies_some_is_fatal():
    empty = run(1, rows=())
    assert agreement.admissible(empty, CONTRACT, LABELS, question=QUESTION) == [
        "E3 complete enough: no rows, for a question that implies some"
    ]
    # An existence question is answered by no rows.
    assert agreement.admissible(empty, CONTRACT, LABELS, question="are there any stores in Alaska?") == []


def test_e3_a_gap_that_is_not_fatal_still_lets_the_run_vote():
    """A store key with no name beside it is a gap the reviewer names, not a
    reason to stop counting the run."""
    keyed = run(1, rows=((7, 10),), columns=("store_key", "n"))
    assert agreement.admissible(keyed, CONTRACT, LABELS, question=QUESTION) == []


def test_e4_the_audit_judging_the_rows_wrong_is_fatal():
    judged = run(1, audit=AuditReport(passed=False, semantic_issue="the rows count baskets, not stores"))
    assert agreement.admissible(judged, CONTRACT, LABELS, question=QUESTION) == [
        "E4 audited: the rows count baskets, not stores"
    ]


def test_e4_a_narrative_the_audit_could_not_trace_still_votes():
    """The narrator's failure, not the rows': measured, it was the one reason
    runs could not vote, and it took right answers out of the vote. `rank`
    puts an audited run of the same group before it."""
    dropped = run(1, claims=[Claim(text="There are 9.", value=9.0, cells=[(0, "n")])],
                  audit=AuditReport(passed=False, unsupported_claims=["There are 9."]))
    assert agreement.admissible(dropped, CONTRACT, LABELS, question=QUESTION) == []
    assert agreement.rank([dropped, run(2)]).index == 2


def test_e5_a_sample_agrees_with_nothing_unless_the_rows_are_ranked():
    capped = run(1, truncated=True)
    assert agreement.admissible(capped, CONTRACT, LABELS, question=QUESTION) == [
        "E5 comparable: the rows hit the row cap and are not ranked, so they are a sample"
    ]
    assert agreement.admissible(capped, AnswerContract(ranked=True), LABELS, question=QUESTION) == []


# ---------------------------------------------------------------------------
# Tier 2: agreement, groups, signatures
# ---------------------------------------------------------------------------


def test_agreement_is_the_scorers_either_way_round():
    narrow = QueryResult(columns=["n"], rows=[[10], [20]])
    wide = QueryResult(columns=["name", "n"], rows=[["a", 10], ["b", 20]])
    assert agreement.agree(narrow, wide, ordered=False) and agreement.agree(wide, narrow, ordered=False)
    assert agreement.agree(QueryResult(rows=[[48.6076]]), QueryResult(rows=[[48.61]]), ordered=False)
    assert not agreement.agree(QueryResult(rows=[[34.20]]), QueryResult(rows=[[98.81]]), ordered=False)
    # Order counts only when the contract ranks the rows.
    first, second = QueryResult(rows=[[1], [2]]), QueryResult(rows=[[2], [1]])
    assert agreement.agree(first, second, ordered=False) and not agreement.agree(first, second, ordered=True)


@pytest.mark.parametrize(("values", "sizes"), [
    ((10, 10, 10, 10), [4]),
    ((10, 10, 99, 10), [3, 1]),
    ((10, 99, 10, 99), [2, 2]),
    ((10, 10, 20, 30), [2, 1, 1]),
    ((10, 20, 30, 40), [1, 1, 1, 1]),
])
def test_runs_group_by_agreement_largest_first(values, sizes):
    groups = agreement.group(voters(*values), CONTRACT, LABELS)
    assert [len(g.members) for g in groups] == sizes
    assert [g.index for g in groups] == list(range(len(sizes)))


def test_a_tie_goes_to_the_originals_group_then_the_lowest_member():
    groups = agreement.group(voters(99, 10, 99, 10), CONTRACT, LABELS)
    assert [g.members for g in groups] == [[0, 2], [1, 3]]
    groups = agreement.group(voters(5, 10, 20, 20, 10), CONTRACT, LABELS)
    assert [g.members for g in groups] == [[1, 4], [2, 3], [0]]


def test_agreement_is_transitive_through_the_groups():
    """A tolerance that lets a match b and b match c puts all three together."""
    # Each within one part in a million of the next; the ends are not.
    a, b, c = 1_000_000.0, 1_000_000.9, 1_000_001.8
    assert not agreement.agree(QueryResult(rows=[[a]]), QueryResult(rows=[[c]]), ordered=False)
    candidates = voters(a, b, c)
    assert [g.members for g in agreement.group(candidates, CONTRACT, LABELS)] == [[0, 1, 2]]


def test_only_the_admissible_are_grouped():
    candidates = voters(10, 10)
    candidates.append(run(2, ((10,),)))  # not marked admissible
    assert [g.members for g in agreement.group(candidates, CONTRACT, LABELS)] == [[0, 1]]


def test_a_signature_is_the_results_key_fact():
    assert agreement.signature(QueryResult(columns=["n"], rows=[[42]]), LABELS) == "42"
    assert agreement.signature(QueryResult(columns=["n"], rows=[]), LABELS) == "no rows"
    named = QueryResult(columns=["store_key", "store_name", "n"], rows=[[7, "Oak St", 99.5], [8, "Elm", 3]])
    assert agreement.signature(named, LABELS) == "2 rows; first: Oak St, 99.5"
    bare = QueryResult(columns=["code", "flag"], rows=[["A", None], ["B", "x"]])
    assert agreement.signature(bare, LABELS) == "2 rows; first: A, NULL"


# ---------------------------------------------------------------------------
# The vote: every row of arch7 section 22.6's table
# ---------------------------------------------------------------------------


def _groups(*sizes) -> list[Group]:
    groups, start = [], 0
    for index, size in enumerate(sizes):
        groups.append(Group(index=index, members=list(range(start, start + size)), representative=start, signature=""))
        start += size
    return groups


@pytest.mark.parametrize(("sizes", "admissible", "total", "level"), [
    ((4,), 4, 4, "unanimous"),
    ((3,), 3, 4, "unanimous"),
    ((3, 1), 4, 4, "majority"),
    ((2, 1), 3, 4, "majority"),
    ((2, 2), 4, 4, "open"),
    ((2, 1, 1), 4, 4, "open"),
    ((1, 1, 1), 3, 4, "open"),
    ((1,), 1, 4, "single"),
    ((), 0, 4, "none"),
])
def test_the_vote(sizes, admissible, total, level):
    vote = agreement.vote(_groups(*sizes), admissible, total)
    assert vote.level == level
    assert (vote.admissible, vote.total, vote.agreed) == (admissible, total, sizes[0] if sizes else 0)
    assert vote.why


def test_the_quorum_is_of_the_admissible_not_of_every_run():
    """Two of four agreeing is a majority when the other two gave up."""
    assert agreement.vote(_groups(2), 2, 4).level == "unanimous"
    assert agreement.vote(_groups(2, 1), 3, 4).level == "majority"


# ---------------------------------------------------------------------------
# After the Judge: only the accepted vote (arch7.1 section 22.7)
# ---------------------------------------------------------------------------


def _judged(*accepted: bool) -> Judgement:
    return Judgement(verdicts=[GroupVerdict(i, ok, "ok" if ok else f"group {i} is wrong") for i, ok in enumerate(accepted)])


def test_with_no_verdicts_the_runs_vote_alone():
    for judgement in (None, Judgement(error="ConnectionError: refused")):
        agreement, winner, after = agreement_module.decide(_groups(3, 1), 4, 4, judgement)
        assert (agreement.level, agreement.agreed, agreement.set_aside, winner.index) == ("majority", 3, 0, 0)
        assert after == (None if judgement is None else replace(judgement, set_aside=[]))


def test_a_vote_with_no_majority_is_delivered_as_contested():
    agreement, winner, _ = agreement_module.decide(_groups(2, 2), 4, 4)
    assert (agreement.level, winner.index) == ("contested", 0)
    assert agreement.why.endswith("the largest group delivered")


def test_the_judge_accepting_every_answer_changes_nothing():
    agreement, winner, after = agreement_module.decide(_groups(3, 1), 4, 4, _judged(True, True))
    assert (agreement.level, agreement.agreed, agreement.admissible, winner.index) == ("majority", 3, 4, 0)
    assert (after.set_aside, after.overruled, after.instead_of) == ([], False, None)


def test_an_answer_set_aside_does_not_vote_and_the_rest_may_be_unanimous():
    agreement, winner, after = agreement_module.decide(_groups(3, 1), 4, 4, _judged(True, False))
    assert (agreement.level, agreement.agreed, agreement.admissible, agreement.set_aside) == ("unanimous", 3, 3, 1)
    assert winner.index == 0 and (after.set_aside, after.overruled) == ([3], False)


def test_the_judge_setting_aside_the_runs_choice_is_judged_and_names_it():
    """B07: three runs agree on the grain trap's figure, the original does not."""
    groups = [Group(index=0, members=[1, 2, 3], representative=2, signature="34.65"),
              Group(index=1, members=[0], representative=0, signature="34.20")]
    agreement, winner, after = agreement_module.decide(groups, 4, 4, _judged(False, True))
    assert (agreement.level, agreement.agreed, agreement.admissible, agreement.set_aside) == ("judged", 1, 1, 3)
    assert winner.index == 1 and winner.representative == 0
    assert (after.set_aside, after.overruled, after.instead_of) == ([1, 2, 3], True, 2)
    assert agreement.why.startswith("the Judge set aside the answer the runs alone chose (3 of the 4")


def test_a_split_the_judge_settles_is_judged_or_unanimous_by_whose_group_it_keeps():
    agreement, winner, after = agreement_module.decide(_groups(2, 2), 4, 4, _judged(True, False))
    assert (agreement.level, winner.index, after.overruled) == ("unanimous", 0, False)
    agreement, winner, after = agreement_module.decide(_groups(2, 2), 4, 4, _judged(False, True))
    assert (agreement.level, winner.index, after.overruled) == ("judged", 1, True)


def test_accepted_answers_with_no_majority_among_them_are_contested():
    agreement, winner, after = agreement_module.decide(_groups(2, 2, 1), 5, 5, _judged(True, True, False))
    assert (agreement.level, agreement.agreed, agreement.admissible, winner.index) == ("contested", 2, 4, 0)
    assert after.set_aside == [4]
    # Two of the three it accepted is a majority of those that vote.
    agreement, _, _ = agreement_module.decide(_groups(2, 1, 1), 4, 4, _judged(True, True, False))
    assert (agreement.level, agreement.agreed, agreement.admissible) == ("majority", 2, 3)


def test_the_judge_accepting_none_delivers_the_runs_own_choice_as_contested():
    agreement, winner, after = agreement_module.decide(_groups(4), 4, 4, _judged(False))
    assert (agreement.level, agreement.agreed, agreement.admissible, agreement.set_aside) == ("contested", 4, 4, 0)
    assert agreement.why.startswith("the Judge accepted none of the answers; the runs' own vote: all 4")
    assert winner.index == 0 and (after.set_aside, after.overruled) == ([0, 1, 2, 3], False)


def test_with_no_run_to_vote_nothing_is_decided():
    agreement, winner, after = agreement_module.decide([], 0, 4, None)
    assert (agreement.level, winner, after) == ("none", None, None)


# ---------------------------------------------------------------------------
# Selection: the representative
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("weaker", "stronger"), [
    ({"completeness": CompletenessReport(accepted_gaps=[MissingColumn(column="store_name", why="")])}, {}),
    ({"audit": AuditReport(passed=False, unsupported_claims=["x"])}, {}),
    ({"attempts": 3}, {"attempts": 1}),
    ({"plan_cost": 900.0}, {"plan_cost": 10.0}),
    ({"plan_cost": None}, {"plan_cost": 10.0}),
])
def test_each_criterion_alone_decides_the_representative(weaker, stronger):
    first, second = run(1, **weaker), run(2, **stronger)
    assert agreement.rank([first, second]).index == 2


def test_the_criteria_in_order():
    """Complete before audited before the original before fewer attempts."""
    gap = CompletenessReport(accepted_gaps=[MissingColumn(column="store_name", why="")])
    complete_rewording = run(2, attempts=4)
    original_with_gap = run(0, completeness=gap)
    assert agreement.rank([original_with_gap, complete_rewording]).index == 2
    assert agreement.rank([run(0, attempts=3), run(1, attempts=1)]).index == 0, "the original before fewer attempts"
    assert agreement.rank([run(3), run(2)]).index == 2, "the lower index, last"
