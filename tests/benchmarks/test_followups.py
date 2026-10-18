"""The follow-up set (arch8 Phase 0): a second question after each benchmark question.

Each follow-up's standalone was checked by hand against its fragment and
its first turn, and each reference against the dataset, as the
benchmark's are. What code can hold is held here: every question has one
and nothing else has any; every standalone passes the checks in code a
model's restatement will meet (`memory.resolve`) against its fragment,
all fifteen reported at once when any fails; every standalone takes
something from its first turn the fragment does not say, which is what
makes the fragment one; every filter a reference is said to keep is in
it. Running the references needs the retail container (`pytest
--run-docker`).
"""

from __future__ import annotations

import pytest
from benchmarks.followups import FOLLOWUPS, Followup, as_question, kept, keeps
from benchmarks.paraphrases import wordings
from benchmarks.questions import QUESTIONS, by_id
from nl2sql_agent import fidelity
from nl2sql_agent.memory import resolve

from tests import live_stores

DATABASE_URL = live_stores.url("retail", variable="TEST_DATABASE_URL")

#: Words that say nothing about what a question asks: a standalone sharing
#: only these with its first turn has borrowed nothing from it.
FUNCTION_WORDS = {
    "a", "an", "and", "are", "as", "by", "did", "do", "each", "for", "how", "in", "is", "many", "much", "of",
    "on", "our", "the", "there", "to", "us", "was", "we", "were", "what", "which",
}


def test_every_benchmark_question_has_one_follow_up_and_nothing_else_has_any():
    assert list(FOLLOWUPS) == [question.id for question in QUESTIONS]
    assert all(followup.first == key for key, followup in FOLLOWUPS.items())


def test_every_standalone_passes_the_checks_against_its_fragment():
    """All fifteen at once, so a change to the checks shows every
    standalone it would discard, each with its reason."""
    discarded = {}
    for followup in FOLLOWUPS.values():
        note = resolve(followup.fragment, followup.standalone)
        if not note.resolved:
            discarded[followup.first] = note.reason
    assert discarded == {}


def test_every_standalone_borrows_from_its_first_turn():
    """What makes a fragment one: its standalone says something of the first
    turn's -- a measure, a period, an entity -- that the fragment does not."""
    borrowed_nothing = []
    for followup in FOLLOWUPS.values():
        first = set(fidelity.words(by_id(followup.first).question))
        borrowed = (set(fidelity.words(followup.standalone)) & first) - set(fidelity.words(followup.fragment))
        if not borrowed - FUNCTION_WORDS:
            borrowed_nothing.append(followup.first)
    assert borrowed_nothing == []


def test_a_fragment_is_never_its_standalone_or_another_question():
    fragments = [followup.fragment for followup in FOLLOWUPS.values()]
    asked = {wording for question in QUESTIONS for wording in wordings(question)}
    assert len(set(fragments)) == len(fragments) == 15
    assert not set(fragments) & asked
    assert all(f.fragment != f.standalone for f in FOLLOWUPS.values())


def test_every_standalone_is_phrased_as_a_question():
    assert [f.first for f in FOLLOWUPS.values() if not f.standalone.endswith("?")] == []


def test_ordered_follow_ups_are_the_ones_that_ask_for_an_order():
    """Row order is part of the answer only when the standalone ranks."""
    for followup in FOLLOWUPS.values():
        if followup.ordered:
            assert fidelity.polarity(followup.standalone) & {"high", "low"}, followup.first


def test_every_filter_a_reference_keeps_is_in_it():
    for followup in FOLLOWUPS.values():
        assert all(kept(literal, followup.reference_sql) for literal in followup.keeps), followup.first


def test_the_filters_kept_say_which_a_query_kept_with_case_and_spacing_aside():
    followup = FOLLOWUPS["B07"]
    assert keeps(followup, "SELECT 1 FROM x WHERE D.FISCAL_YEAR=2025 AND fiscal_month_num =  12") == {
        "fiscal_month_num = 12": True, "fiscal_year = 2025": True,
    }
    assert keeps(followup, "SELECT 1 FROM x WHERE fiscal_year = 2024") == {
        "fiscal_month_num = 12": False, "fiscal_year = 2025": False,
    }
    assert keeps(followup, None) == {"fiscal_month_num = 12": False, "fiscal_year = 2025": False}
    assert keeps(FOLLOWUPS["B01"], "SELECT 1") == {}


def test_a_follow_up_is_scored_as_the_complete_question_it_means():
    """Filed under its first turn's id and category; its words the
    standalone's, its reference and order its own."""
    followup = FOLLOWUPS["B05"]
    question = as_question(followup)
    assert (question.id, question.category) == ("B05", by_id("B05").category)
    assert (question.question, question.reference_sql) == (followup.standalone, followup.reference_sql)
    assert (question.ordered, question.expected_rows) == (True, 2)


# ---------------------------------------------------------------------------
# The references, against the real dataset
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database():
    from nl2sql_agent.database import Database

    db = Database(DATABASE_URL, statement_timeout_ms=60000, max_rows=1000)
    try:
        db.run_select("SELECT 1")
    except Exception as exc:
        live_stores.unreachable("retail database", DATABASE_URL, exc)
    return db


@pytest.mark.docker
@pytest.mark.parametrize("followup", list(FOLLOWUPS.values()), ids=list(FOLLOWUPS))
def test_every_reference_runs_returns_its_shape_and_the_same_rows_twice(database, followup: Followup):
    """A reference returning nothing would mark every answer wrong and look
    like a model problem; one that moved between runs would grade nothing."""
    first = [list(row) for row in database.run_select(followup.reference_sql).rows]
    assert len(first) == followup.expected_rows, f"{followup.first}: {len(first)} rows, {followup.expected_rows} said"
    assert first == [list(row) for row in database.run_select(followup.reference_sql).rows]


@pytest.mark.docker
def test_the_follow_ups_reference_is_not_its_first_turns(database):
    """A follow-up whose answer is its first turn's asks nothing new, and
    asked alone would score its first turn's accuracy."""
    same = [
        followup.first for followup in FOLLOWUPS.values()
        if [list(r) for r in database.run_select(followup.reference_sql).rows]
        == [list(r) for r in database.run_select(by_id(followup.first).reference_sql).rows]
    ]
    assert same == []
