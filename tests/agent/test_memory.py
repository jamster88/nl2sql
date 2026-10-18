"""memory.py: a follow-up's restatement held to the words typed (arch8 section 23.3).

`resolve` is a pure function of two strings, so each check is tested on a
pair it must keep and a pair it must discard -- arch8's own examples first
-- and the order of the checks on a restatement that fails two. The
hand-written follow-up set holds it to fifteen standalones a person wrote
(`tests/benchmarks/test_followups.py`).
"""

from __future__ import annotations

import pytest
from nl2sql_agent import memory
from nl2sql_agent.memory import NOT_RESTATED, MemoryNote, Turn, resolve
from nl2sql_agent.state import to_jsonable

# ---------------------------------------------------------------------------
# C4, the empty half: not restated is no failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("standalone", ["", "   ", "And by store.", "and BY store", "And by  store?!"])
def test_the_question_itself_again_is_not_restated(standalone):
    """The model returned the question with a full stop, or nothing: the
    question stands alone, and nothing is resolved."""
    assert resolve("And by store?", standalone) == MemoryNote(reason=NOT_RESTATED)


# ---------------------------------------------------------------------------
# C1 numbers
# ---------------------------------------------------------------------------


def test_a_number_the_restatement_dropped_discards_it():
    assert resolve("the top 5 of those", "the top banners by net sales").reason == "C1 numbers: 5 missing"


def test_a_number_kept_as_a_word_is_kept():
    note = resolve("just the top five of those", "Which 5 banners had the highest net sales in FY2025?")
    assert note.resolved


def test_a_number_the_turns_supplied_may_be_added():
    """Subset, not equality: the year is the turn's, and a restatement
    that writes it out has added what the turns supply."""
    assert resolve("the top 5 of those", "the top 5 banners by net sales in fiscal year 2025").resolved


# ---------------------------------------------------------------------------
# C2 literals
# ---------------------------------------------------------------------------


def test_a_name_the_restatement_dropped_discards_it():
    assert resolve("and for Dairy & Eggs?", "and for dairy products?").reason == "C2 literals: 'dairy & eggs' missing"


def test_a_name_is_kept_whatever_its_case():
    note = resolve("and for Dairy & Eggs?", "What was the gross margin for the dairy & eggs department in FY2025?")
    assert note.resolved


@pytest.mark.parametrize(
    "asked,standalone,literal",
    [
        ("The same for FY2024?", "What were our total net sales in fiscal year 2024?", "fy2024"),
        ("Which vendor paid the most in SCAN_BACK?", "Which vendor paid the most in scan-back allowances?", "scan_back"),
        ('and the "Back to School" ones?', "and the back-to-school promotions?", "back to school"),
    ],
)
def test_a_code_a_mixed_token_and_a_quoted_value_are_literals_too(asked, standalone, literal):
    assert resolve(asked, standalone).reason == f"C2 literals: '{literal}' missing"


# ---------------------------------------------------------------------------
# C3 polarity
# ---------------------------------------------------------------------------


def test_the_lowest_is_not_the_highest():
    assert resolve("and the lowest?", "Which department had the highest net sales?").reason == "C3 polarity: low missing"


def test_a_lost_exclusion_discards_it():
    note = resolve("excluding markdowns?", "What were our gross sales in fiscal year 2025, with markdowns?")
    assert note.reason == "C3 polarity: negation missing"


def test_a_direction_the_turns_supplied_may_be_added():
    assert resolve("and by store?", "Which stores had the highest net sales in fiscal year 2025?").resolved


# ---------------------------------------------------------------------------
# C4, the bound
# ---------------------------------------------------------------------------


def test_a_restatement_longer_than_a_question_may_be_discards_it():
    standalone = "What were our net sales by store " + "x" * (memory.MAX_QUESTION_LENGTH - 32)
    assert len(standalone) == memory.MAX_QUESTION_LENGTH + 1
    assert resolve("and by store?", standalone).reason == "C4 shape: longer than 2000 characters"
    assert resolve("and by store?", standalone[:-1]).resolved


def test_the_bound_is_the_apis_own():
    """One home: the longest question a request may ask is the longest a
    restatement may be."""
    from nl2sql_agent.api import models

    assert models.MAX_QUESTION_LENGTH is memory.MAX_QUESTION_LENGTH == 2000


def test_the_bound_can_be_given():
    assert resolve("and by store?", "What were net sales by store?", max_length=10).reason == (
        "C4 shape: longer than 10 characters"
    )


# ---------------------------------------------------------------------------
# Held, and the order
# ---------------------------------------------------------------------------


def test_a_restatement_that_adds_the_turns_period_and_entity_and_keeps_everything_is_held():
    note = resolve(
        "and the bottom 3 for Dairy & Eggs?",
        "Which  3 products in the Dairy & Eggs department had the lowest\nnet sales in fiscal year 2025?",
    )
    assert note == MemoryNote(
        resolved=True,
        read_as="Which 3 products in the Dairy & Eggs department had the lowest net sales in fiscal year 2025?",
    )


def test_the_first_failing_check_is_the_one_named():
    """A restatement that drops a number, a name and a direction is
    discarded for the number: nothing after the first failure is computed."""
    note = resolve("and the bottom 3 for Dairy & Eggs?", "and the top dairy products?")
    assert note.reason == "C1 numbers: 3 missing"
    assert resolve("and the bottom 3 for Dairy & Eggs?", "and the top 3 dairy products?").reason == (
        "C2 literals: 'dairy & eggs' missing"
    )


def test_shown_is_the_callers_to_fill():
    assert resolve("and by store?", "What were net sales by store?").shown == 0


def test_a_note_renders_as_json():
    note = MemoryNote(shown=2, resolved=True, read_as="What were net sales by store?")
    assert to_jsonable(note) == {"shown": 2, "resolved": True, "read_as": "What were net sales by store?", "reason": ""}


def test_a_turn_renders_as_json_its_cells_already_text():
    turn = Turn(index=2, asked="And by banner?", question="Net sales by banner in FY2025?", sql="SELECT 1",
                columns=["banner_name", "net_sales"], rows=[["Mainline", "412300000.00"]], row_count=5,
                answer="Mainline led.", assumptions=["FY2025"], job_id="j1")
    assert to_jsonable(turn) == {
        "index": 2, "asked": "And by banner?", "question": "Net sales by banner in FY2025?", "sql": "SELECT 1",
        "columns": ["banner_name", "net_sales"], "rows": [["Mainline", "412300000.00"]], "row_count": 5,
        "answer": "Mainline led.", "assumptions": ["FY2025"], "job_id": "j1",
    }

