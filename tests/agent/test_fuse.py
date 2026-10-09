"""The agreement line every chosen answer opens with (`fuse.agreement_line`;
arch7 section 22.8). The count in it is the runs made, not the rewordings
written."""

from __future__ import annotations

import pytest
from nl2sql_agent.ensemble_state import Agreement, Judgement
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


def test_the_judges_sentences():
    judged = Agreement(4, 2, 4, "judged")
    assert agreement_line(judged, Judgement(group=1, why="It filters the fiscal year.")) == (
        "The runs disagreed; the Judge chose this answer: It filters the fiscal year."
    )
    assert agreement_line(Agreement(4, 2, 4, "contested"), Judgement(group=None, why="neither")) == (
        "The runs disagreed and the Judge could not say which was right; this is the largest group's (2 of 4 runs)."
    )
