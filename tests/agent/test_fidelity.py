"""fidelity.py: the fidelity gate's checks in code (arch7 section 22.3).

F1, F2, F3 and F5 are pure functions of two strings, so each is tested on a
pair it must pass and a pair it must fail -- arch7's own examples first --
and each word or rule the paraphrase set grew is tested beside the case
that grew it. F4 needs the Supervisor and is not here.
"""

from __future__ import annotations

import pytest
from nl2sql_agent import fidelity as f

# ---------------------------------------------------------------------------
# F1 numbers
# ---------------------------------------------------------------------------


def test_top_ten_is_not_top_five():
    assert f.check("Which ten stores sold the most?", "Which five stores sold the most?") == (
        "F1 numbers: 10 missing; 5 added"
    )


@pytest.mark.parametrize("word,digits", [("one", "1"), ("seven", "7"), ("twelve", "12"), ("twenty", "20")])
def test_a_number_word_is_its_number(word, digits):
    assert f.numerals(f"the top {word} products") == f.numerals(f"the top {digits} products")


def test_a_year_that_went_missing_is_named():
    assert f.check("What were net sales in fiscal year 2025?", "What were net sales this fiscal year?") == (
        "F1 numbers: 2025 missing"
    )


def test_numbers_are_a_multiset_so_a_dropped_repeat_counts():
    assert f.numerals("2024 against 2024") != f.numerals("2024 alone")


def test_a_number_is_compared_by_value_not_by_how_it_is_written():
    assert f.numerals("over 1,000 units at 12.50") == f.numerals("over 1000 units at 12.5")
    assert f.numerals("since 05") == f.numerals("since 5")


def test_a_number_inside_a_token_counts():
    """FY2025 is fiscal year 2025, and Q4 is quarter 4."""
    assert f.numerals("in Q4 of FY2025") == f.numerals("in fiscal quarter 4 of fiscal year 2025")


def test_an_ordinal_is_its_number():
    """Grown: B06's "the fourth fiscal quarter" and B07's "the twelfth fiscal
    month" were discarded until ordinals counted."""
    assert f.numerals("the fourth fiscal quarter") == f.numerals("fiscal quarter 4")
    assert f.numerals("the twelfth fiscal month") == f.numerals("fiscal month 12") == f.numerals("the 12th month")
    assert f.check("fiscal month 12 of fiscal year 2025", "the 12th fiscal month of FY2025, as before") is None


# ---------------------------------------------------------------------------
# F2 literals
# ---------------------------------------------------------------------------


def test_a_name_must_be_kept_as_written():
    original = "What was the gross margin for the Dairy & Eggs department?"
    assert f.check(original, "What gross margin did the dairy products department make?") == (
        "F2 literals: 'dairy & eggs' not found"
    )
    assert f.check(original, "For dairy & eggs, what gross margin did the department make?") is None


def test_a_name_is_matched_as_whole_words():
    assert f.check("clicks for Print Flyer ads", "the clicks Print Flyers got") == (
        "F2 literals: 'print flyer' not found"
    )


def test_a_name_may_carry_joining_words_but_not_end_with_one():
    assert f.literals("stores run by the Bank of America plan") == {"bank of america"}
    assert f.literals("channels such as Print Flyer, Paid Social and so on") == {"print flyer", "paid social"}


def test_a_quoted_literal_keeps_its_apostrophe():
    original = 'How many units of "Trader Joe\'s" did we sell?'
    assert f.literals(original) == {"trader joe's"}
    assert f.check(original, 'What unit count did "Trader Joe\'s" sell for us?') is None
    assert f.check(original, "What unit count did Trader Joes sell for us?") == (
        "F2 literals: 'trader joe's' not found"
    )


@pytest.mark.parametrize("text", ["sales at 'Joe's Market' stores", "sales at ‘Joe’s Market’ stores"])
def test_an_apostrophe_inside_single_quotes_does_not_end_them(text):
    assert f.literals(text) == {"joe's market"}


def test_an_apostrophe_quotes_nothing():
    assert f.literals("What were the company's sales in the stores' best week?") == set()


def test_letters_and_digits_together_are_a_literal_but_an_ordinal_is_a_number():
    assert f.literals("net sales in Q4 of FY2025") == {"q4", "fy2025"}
    assert f.literals("net sales in the 4th quarter") == set()


def test_a_code_is_a_literal():
    """Grown: "like SCAN_BACK" names the column B09 is reported by, and
    nothing held it."""
    original = "Report each type by its short code, like SCAN_BACK."
    assert f.literals(original) == {"scan_back"}
    assert f.check(original, "Give each type's short code, such as SCAN_BACK.") is None
    assert f.check(original, "Give each type's short code.") == "F2 literals: 'scan_back' not found"


def test_a_sentences_first_word_starts_no_name():
    """Grown: "Give the SKU and the amount" made "Give the SKU" a name, and
    every rewording of B10 that did not open with those words was
    discarded."""
    assert f.literals("Which 5 products sold most? Give the SKU and the amount.") == set()
    assert f.literals("Show Print Flyer clicks. Then Paid Social.") == {"print flyer", "paid social"}


def test_the_price_of_that_is_a_name_that_opens_a_sentence():
    """Held by F4 instead, which reads what the question is about."""
    assert f.literals("Dairy & Eggs margin in FY2025?") == {"fy2025"}


# ---------------------------------------------------------------------------
# F3 polarity
# ---------------------------------------------------------------------------


def test_lowest_is_not_highest():
    assert f.check("Which competitor prices lowest?", "Which competitor prices highest?") == (
        "F3 polarity: low missing; high added"
    )


def test_a_lost_negation_is_caught():
    assert f.check("Net sales excluding markdowns?", "Net sales with markdowns?") == "F3 polarity: negation missing"


@pytest.mark.parametrize("text", ["the top 5 products", "the 5 best-selling products", "the 5 products that sold most"])
def test_words_of_one_class_are_one_direction(text):
    assert f.polarity(text) == {"high"}


def test_a_contraction_is_a_negation():
    assert f.polarity("Which stores didn't sell it?") == f.polarity("Which stores did not sell it?") == {"negation"}


@pytest.mark.parametrize("text", [
    "How much did we collect, broken down by allowance type?",
    "Break down our net sales by state.",
    "Break it down by banner.",
])
def test_a_breakdown_is_not_a_decline(text):
    """Grown: read as a decline, "broken down by allowance type" discarded two
    of B09's rewordings, and "break down our net sales" put one into B14's."""
    assert f.polarity(text) == set()
    assert f.polarity("Which stores' sales went down?") == {"down"}


def test_cheapest_is_low():
    """Grown: B13's "which competitor is cheapest" was discarded against
    "prices lowest"."""
    assert f.polarity("Which competitor is cheapest relative to us?") == f.polarity("Which prices lowest?")


# ---------------------------------------------------------------------------
# F5 distinct
# ---------------------------------------------------------------------------


def test_two_rewordings_that_differ_by_a_comma_are_one():
    kept = ["In fiscal year 2025, what were our net sales by state?"]
    assert not f.distinct("In fiscal year 2025 what were our net sales by state?", kept)
    assert f.check("What were our net sales by state in fiscal year 2025?",
                   "In fiscal year 2025 what were our net sales by state?") == (
        "F5 distinct: too close to the original (Jaccard 1.00)"
    )


def test_the_same_words_in_another_order_are_not_distinct():
    assert f.similarity("net sales by state in 2025", "in 2025, by state: net sales") == 1.0


def test_at_most_four_in_five_shared_is_distinct_and_more_is_not():
    assert f.distinct("alpha beta gamma delta epsilon", ["alpha beta gamma delta"])
    assert not f.distinct("alpha beta gamma delta epsilon zeta", ["alpha beta gamma delta epsilon"])


def test_a_rewording_is_held_apart_from_every_kept_one():
    original = "What were our net sales by state in fiscal year 2025?"
    kept = ["Break down our fiscal year 2025 net sales by state.", "For each state, what were the net sales?"]
    assert f.check(original, "Break down our fiscal year 2025 net sales, by state.", kept) == (
        "F5 distinct: too close to kept rewording 1 (Jaccard 1.00)"
    )


def test_two_empty_wordings_are_the_same():
    assert f.similarity("", "?") == 1.0


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_a_faithful_rewording_passes_every_check():
    assert f.check(
        "What was the gross margin percentage for the Dairy & Eggs department in fiscal month 12 of fiscal year 2025?",
        "What gross margin percentage did Dairy & Eggs achieve in the twelfth fiscal month of fiscal year 2025?",
    ) is None


def test_the_first_failing_check_is_the_one_named():
    """F1, F2, F3, F5, in that order: the reason a record shows is one check's."""
    assert f.check("The top 10 Dairy & Eggs products", "The bottom 5 dairy items").startswith("F1 numbers")
    assert f.check("The top 10 Dairy & Eggs products", "The bottom 10 dairy items").startswith("F2 literals")
    assert f.check("The top 10 Dairy & Eggs products", "The bottom 10 Dairy & Eggs items").startswith("F3 polarity")
