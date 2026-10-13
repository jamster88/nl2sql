"""The scorer: do two results hold the same answer (`nl2sql_agent.compare`)?

It decides what "correct" means for the benchmark and, from arch7, what
"agree" means for the ensemble, so it is the one thing that can quietly
invalidate every number either reports. Too strict and correct queries score
as wrong because a column is named differently; too loose and a query off by
the 5x fan-out scores as right. Moved here from `tests/benchmarks/test_runner.py`
with the code, cases intact.
"""

from __future__ import annotations

import math
from decimal import Decimal

from nl2sql_agent.compare import MAX_COLUMN_ASSIGNMENTS, result_matches, values_match


# ---------------------------------------------------------------------------
# Comparing single values
# ---------------------------------------------------------------------------


def test_the_same_number_matches_however_the_driver_produced_it():
    """Postgres NUMERIC arrives as Decimal, a count as int, a ratio as float.
    All three are the same answer.
    """
    assert values_match(Decimal("10"), 10)
    assert values_match(10, 10.0)
    assert values_match(Decimal("21.51"), 21.51)


def test_rounding_at_the_last_place_still_matches():
    assert values_match(6032194.28, 6032194.280000001)


def test_a_real_mistake_does_not_match():
    """The two traps the benchmark exists for: a 5x fan-out and a ~30x grain
    miss. Neither may be absorbed by the tolerance.
    """
    assert not values_match(21.51, 107.55)
    assert not values_match(18187.20, 605.3)
    assert not values_match(100, 101)


def test_strings_match_ignoring_case_and_padding():
    """state_code is CHAR(2) and comes back space-padded; department names can
    differ in case between a literal and the column.
    """
    assert values_match("WI", "wi ")
    assert values_match("Meat & Seafood", "meat & seafood")
    assert not values_match("WI", "CT")


def test_booleans_are_not_treated_as_numbers():
    """True == 1 in Python, and a boolean column matching an integer one would
    make an is_private_label flag indistinguishable from a count.
    """
    assert not values_match(True, 1.0)
    assert values_match(True, "true")


def test_null_only_matches_null():
    assert values_match(None, None)
    assert not values_match(None, 0)
    assert not values_match(0, None)


# ---------------------------------------------------------------------------
# Comparing result sets
# ---------------------------------------------------------------------------


def test_an_identical_result_matches():
    assert result_matches([[1, "a"], [2, "b"]], [[1, "a"], [2, "b"]])


def test_column_order_does_not_matter():
    """`SELECT total, name` answers the same question as `SELECT name, total`."""
    assert result_matches([["a", 1], ["b", 2]], [[1, "a"], [2, "b"]])


def test_extra_columns_are_allowed():
    """An agent that returns the department alongside the total has answered the
    question; grading it wrong would punish being informative.
    """
    assert result_matches([["Meat & Seafood", 100]], [["Meat & Seafood", 100, "extra"]])


def test_a_missing_column_is_not_allowed():
    """B02 asks for two numbers. Returning only the first is half an answer."""
    assert not result_matches([[200, 41]], [[200]])


def test_row_order_is_ignored_by_default():
    assert result_matches([["a", 1], ["b", 2]], [["b", 2], ["a", 1]])


def test_row_order_matters_when_the_question_asks_for_an_order():
    """"Top 5 by sales" means the order is the answer."""
    ranked = [["a", 3], ["b", 2], ["c", 1]]
    shuffled = [["b", 2], ["a", 3], ["c", 1]]
    assert result_matches(ranked, shuffled, ordered=False)
    assert not result_matches(ranked, shuffled, ordered=True)


def test_a_different_row_count_never_matches():
    assert not result_matches([[1], [2]], [[1]])
    assert not result_matches([[1]], [[1], [2]])


def test_the_right_values_in_the_wrong_pairing_do_not_match():
    """Both columns contain the same values, but attached to the wrong rows --
    a join on the wrong key produces exactly this.
    """
    assert not result_matches([["a", 1], ["b", 2]], [["a", 2], ["b", 1]])


def test_two_columns_holding_the_same_values_are_each_matched_once():
    """A reference with two identical columns must not be satisfied by an agent
    result that has only one.
    """
    assert result_matches([[1, 1]], [[1, 1]])
    assert not result_matches([[1, 1]], [[1, "x"]])


def test_an_empty_reference_only_matches_an_empty_result():
    assert result_matches([], [])
    assert not result_matches([], [[1]])
    assert not result_matches([[1]], [])


def test_rows_with_mixed_types_sort_without_raising():
    """The ordering used for the comparison cannot assume a column's values are
    mutually comparable -- NULLs and numbers arrive in the same column.
    """
    assert result_matches([[None], [1], ["a"]], [["a"], [None], [1]])


def test_a_reference_rounded_to_two_places_matches_an_unrounded_answer():
    """The references round for legibility and agents usually do not. A pure
    relative tolerance rejects 48.6076 against 48.61 -- off by 5e-5, thirty
    times 1e-6, and obviously the same number. This cost correct agents four
    marks before it was fixed.
    """
    assert values_match(48.61, 48.607604215)
    assert values_match(34.20, 34.19905297061825)
    assert values_match(Decimal("83.5"), 83.4999999)


def test_rounding_tolerance_does_not_absorb_a_real_mistake():
    """The margin between the two is wide: the grain trap returns 98.81 against
    34.20 and the fan-out is wrong by 5x, neither of which is a rounding
    difference.
    """
    assert not values_match(34.20, 98.81)
    assert not values_match(21.51, 0.2151)
    assert not values_match(48.61, 48.62)


def test_a_pathologically_wide_result_falls_back_to_the_reference_column_order():
    """The search is over column assignments, which is factorial in the width
    of the agent's result. Nothing the benchmark asks returns more than a few
    columns, but a `SELECT *` on a wide fact table would, and the scorer must
    not become the slow part of a run that is already a minute a question.
    """
    # 8 columns offered, 5 wanted: 6,720 assignments, over the cap.
    assert math.perm(8, 5) > MAX_COLUMN_ASSIGNMENTS

    expected = [[1, 2, 3, 4, 5]]
    leading = [[1, 2, 3, 4, 5, 6, 7, 8]]
    assert result_matches(expected, leading) is True

    # Past the cap it stops searching, so the same values behind three
    # padding columns are no longer found. That is the trade the cap makes,
    # and it is worth pinning: it is a refusal to guess, not a wrong answer.
    trailing = [[0, 0, 0, 1, 2, 3, 4, 5]]
    assert result_matches(expected, trailing) is False

    # One column narrower and the search runs, so the shape above is not
    # simply unmatchable.
    assert math.perm(7, 5) <= MAX_COLUMN_ASSIGNMENTS
    assert result_matches([[1, 2, 3, 4, 5]], [[0, 0, 1, 2, 3, 4, 5]]) is True
