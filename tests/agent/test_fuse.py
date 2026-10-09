"""The agreement line every chosen answer opens with (`fuse.agreement_line`;
arch7.1 sections 22.7 and 22.8). The count in it is the runs made, not the
rewordings written; what the Judge did is said beside it."""

from __future__ import annotations

import pytest
from nl2sql_agent.ensemble_state import Agreement, Group, GroupVerdict, Judgement
from nl2sql_agent.fuse import agreement_line


@pytest.mark.parametrize(("agreement", "line"), [
    (Agreement(4, 4, 4, "unanimous"), "Agreed by 4 of 4 independent runs of the question, each worded differently."),
    (Agreement(3, 3, 4, "unanimous"),
     "Agreed by 3 of 4 independent runs of the question, each worded differently; 1 could not answer."),
    (Agreement(4, 3, 4, "majority"), "3 of 4 runs agreed; 1 answered differently."),
    (Agreement(3, 2, 4, "majority"), "2 of 4 runs agreed; 1 answered differently; 1 could not answer."),
    (Agreement(4, 2, 4, "contested"),
     "The runs disagreed and no answer had a majority; this is the largest group's (2 of 4 runs)."),
    (Agreement(1, 1, 1, "single"), "Asked 1 way; one run answered."),
    (Agreement(1, 1, 4, "single"), "Asked 4 ways; only one run answered."),
    (Agreement(0, 0, 4, "none"), ""),
])
def test_every_agreement_has_its_sentence(agreement, line):
    assert agreement_line(agreement) == line


def _groups(*members) -> list[Group]:
    return [Group(index=i, members=list(m), representative=m[0], signature="") for i, m in enumerate(members)]


@pytest.mark.parametrize(("agreement", "line"), [
    (Agreement(3, 3, 4, "unanimous", set_aside=1),
     "Agreed by 3 of 4 independent runs of the question, each worded differently; the Judge set aside the answer of "
     "1 other."),
    (Agreement(2, 2, 4, "unanimous", set_aside=1),
     "Agreed by 2 of 4 independent runs of the question, each worded differently; 1 could not answer; the Judge set "
     "aside the answer of 1 other."),
    (Agreement(3, 2, 5, "majority", set_aside=2),
     "2 of 5 runs agreed; 1 answered differently; the Judge set aside the answer of 2 others."),
    (Agreement(2, 1, 4, "contested", set_aside=2),
     "The runs disagreed and no answer had a majority; this is the largest group's (1 of 4 runs); the Judge set aside "
     "the answer of 2 others."),
    (Agreement(1, 1, 4, "single", set_aside=3), "Asked 4 ways; one run's answer stands; the Judge set aside the answer "
     "of 3 others."),
])
def test_the_answers_the_judge_set_aside_are_counted_apart_from_the_runs_that_failed(agreement, line):
    assert agreement_line(agreement, Judgement(set_aside=list(range(agreement.set_aside)))) == line


def test_when_the_judge_overrules_the_runs_the_line_says_what_it_set_aside_and_why():
    judgement = Judgement(
        verdicts=[GroupVerdict(0, False, "It joins daily sales to monthly costs on the date."), GroupVerdict(1, True)],
        set_aside=[1, 2, 3], overruled=True, instead_of=1,
    )
    line = agreement_line(Agreement(1, 1, 4, "judged", set_aside=3), judgement, _groups([1, 2, 3], [0]))
    assert line == (
        "The Judge set aside the answer 3 of 4 runs gave -- It joins daily sales to monthly costs on the date -- "
        "and accepted this one, which 1 gave."
    )


def test_when_the_judge_accepts_none_the_runs_own_choice_is_delivered_with_its_objection():
    judgement = Judgement(verdicts=[GroupVerdict(0, False, "It filters to two channels."), GroupVerdict(1, False)],
                          set_aside=[0, 1, 2, 3])
    line = agreement_line(Agreement(4, 3, 4, "contested"), judgement, _groups([0, 1, 3], [2]))
    assert line == (
        "The Judge accepted none of the answers -- It filters to two channels -- "
        "so this is the runs' own choice, which 3 of 4 gave."
    )
    # A verdict with no reason still makes a sentence.
    judgement = Judgement(verdicts=[GroupVerdict(0, False)], set_aside=[0])
    assert "-- it gave no reason --" in agreement_line(Agreement(1, 1, 1, "contested"), judgement, _groups([0]))


def test_a_judge_that_could_not_be_asked_is_said_so():
    failed = Judgement(error="ConnectionError: refused")
    assert agreement_line(Agreement(4, 4, 4, "unanimous"), failed) == (
        "Agreed by 4 of 4 independent runs of the question, each worded differently. The Judge could not be asked."
    )
    assert agreement_line(Agreement(1, 1, 1, "single"), failed).endswith("one run answered. The Judge could not be asked.")
