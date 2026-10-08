"""The paraphrase set (arch7 Phase 0): three rewordings of each benchmark question.

Each rewording was checked by hand against its question's reference SQL --
the same entities, measure, period, direction and row count -- which no
test can do without the Supervisor's reading (F4). What code can hold is
held here: every question has three, none repeats another wording, and
each passes the fidelity gate's checks in code against its question, with
the rewordings before it kept, as a model-written rewording will meet them
in the ensemble. A rewording a person judged faithful that the checks would
discard fails here, and the checks' vocabulary is what grows
(`agent/nl2sql_agent/fidelity.py`).
"""

from __future__ import annotations

from benchmarks.paraphrases import PARAPHRASES, wordings
from benchmarks.questions import QUESTIONS, by_id
from nl2sql_agent import fidelity


def test_every_benchmark_question_has_three_rewordings_and_nothing_else_has_any():
    assert list(PARAPHRASES) == [question.id for question in QUESTIONS]
    assert {len(rewordings) for rewordings in PARAPHRASES.values()} == {3}


def test_every_rewording_passes_the_fidelity_checks_against_its_question():
    """All forty-five at once, so a change to the checks shows every
    rewording it would discard, each with its reason."""
    discarded = {}
    for question in QUESTIONS:
        rewordings = PARAPHRASES[question.id]
        for number, rewording in enumerate(rewordings, start=1):
            reason = fidelity.check(question.question, rewording, rewordings[: number - 1])
            if reason:
                discarded[f"{question.id}.{number}"] = reason
    assert discarded == {}


def test_a_question_is_asked_in_its_own_words_first():
    """Wording 0 is the benchmark's own, as the original is candidate 0 in
    the ensemble; the rewordings are 1 to 3, in the order written."""
    question = by_id("B07")
    assert wordings(question) == (question.question, *PARAPHRASES["B07"])


def test_no_wording_is_another_one_again():
    every = [wording for question in QUESTIONS for wording in wordings(question)]
    assert len(set(every)) == len(every) == 60
