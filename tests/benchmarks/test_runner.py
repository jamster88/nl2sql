"""The scorer and the timer.

The scorer decides what "correct" means, so it is the one thing in the benchmark
that can quietly invalidate every number it reports. Too strict and correct
queries score as wrong because a column is named differently; too loose and a
query off by the 5x fan-out scores as right.

All offline -- the comparison is pure, and the timer is exercised with a fake
clock rather than by sleeping.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.runner import (
    # noqa: E402,
    BenchmarkReport,
    CORRECT,
    ERROR,
    FAILED,
    QuestionResult,
    StageTimer,
    StageTiming,
    WRONG,
    model_calls_from_trace,
    result_matches,
    timing_from_trace,
    values_match,
)


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


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def test_the_timer_records_one_entry_per_progress_callback(monkeypatch):
    # One extra leading value: the constructor reads the clock before start().
    clock = iter([0.0, 100.0, 100.5, 101.5, 103.0])
    monkeypatch.setattr("benchmarks.runner.time.perf_counter", lambda: next(clock))

    timer = StageTimer()
    timer.start()
    timer("retrieve_knowledge", "")
    timer("retrieve_examples", "")
    timer("generate_sql", "")

    assert timer.timing.stages == [
        ("retrieve_knowledge", 0.5),
        ("retrieve_examples", 1.0),
        ("generate_sql", 1.5),
    ]
    assert timer.timing.total == pytest.approx(3.0)


def test_a_repeated_stage_is_summed(monkeypatch):
    """generate_sql and validate_sql run again on every retry, and the question
    worth answering is how long generation took in total.
    """
    clock = iter([0.0, 0.0, 1.0, 2.0, 5.0])
    monkeypatch.setattr("benchmarks.runner.time.perf_counter", lambda: next(clock))
    timer = StageTimer()
    timer.start()
    timer("generate_sql", "")
    timer("validate_sql", "")
    timer("generate_sql", "")
    assert timer.timing.seconds_for("generate_sql") == pytest.approx(4.0)
    assert timer.timing.as_dict()["generate_sql"] == pytest.approx(4.0)


def test_the_timer_still_forwards_to_the_callers_callback():
    """`--verbose` prints the pipeline steps; wrapping must not swallow them."""
    seen = []
    timer = StageTimer(lambda step, detail: seen.append((step, detail)))
    timer.start()
    timer("generate_sql", "SELECT 1")
    assert seen == [("generate_sql", "SELECT 1")]


def test_starting_again_discards_the_previous_question():
    timer = StageTimer()
    timer.start()
    timer("a", "")
    timer.start()
    assert timer.timing.stages == []


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def result(qid, category, outcome, seconds, stages=()) -> QuestionResult:
    return QuestionResult(
        question_id=qid, category=category, question="q", outcome=outcome,
        wall_seconds=seconds, timing=StageTiming(list(stages)),
    )


def test_accuracy_counts_only_correct_answers():
    report = BenchmarkReport(results=[
        result("B01", "schema", CORRECT, 1.0),
        result("B02", "schema", WRONG, 1.0),
        result("B03", "grain", ERROR, 1.0),
        result("B04", "grain", FAILED, 1.0),
    ])
    assert report.correct == 1
    assert report.accuracy == 0.25


def test_answered_separates_a_wrong_answer_from_no_answer():
    """A pipeline that gives up fails differently from one that confidently
    answers the wrong question, and the fixes are different too.
    """
    report = BenchmarkReport(results=[
        result("B01", "schema", CORRECT, 1.0),
        result("B02", "schema", WRONG, 1.0),
        result("B03", "grain", FAILED, 1.0),
    ])
    assert report.answered == 2


def test_category_breakdown_shows_where_failures_cluster():
    """12/15 with every grain question wrong is a different system from 12/15
    with three scattered misses.
    """
    report = BenchmarkReport(results=[
        result("B01", "schema", CORRECT, 1.0),
        result("B02", "schema", CORRECT, 1.0),
        result("B03", "grain", WRONG, 1.0),
    ])
    assert report.by_category() == {"schema": (2, 2), "grain": (0, 1)}


def test_median_is_robust_to_one_slow_question():
    """One retry loop can double a mean; the median says what a typical question
    costs.
    """
    report = BenchmarkReport(results=[
        result("B01", "schema", CORRECT, 1.0),
        result("B02", "schema", CORRECT, 2.0),
        result("B03", "schema", CORRECT, 60.0),
    ])
    assert report.median_seconds == 2.0
    assert report.total_seconds == 63.0


def test_median_of_an_even_number_of_questions_averages_the_middle_two():
    report = BenchmarkReport(results=[
        result("B01", "s", CORRECT, 1.0), result("B02", "s", CORRECT, 2.0),
        result("B03", "s", CORRECT, 4.0), result("B04", "s", CORRECT, 8.0),
    ])
    assert report.median_seconds == 3.0


def test_an_empty_report_does_not_divide_by_zero():
    report = BenchmarkReport()
    assert report.accuracy == 0.0
    assert report.median_seconds == 0.0


def test_the_slowest_questions_come_back_worst_first():
    report = BenchmarkReport(results=[
        result("B01", "s", CORRECT, 1.0),
        result("B02", "s", CORRECT, 9.0),
        result("B03", "s", CORRECT, 5.0),
    ])
    assert [r.question_id for r in report.slowest(2)] == ["B02", "B03"]


def test_stage_totals_aggregate_across_questions_worst_first():
    """The output that answers "where did the time go" -- and the answer is
    almost always a model call rather than a database one.
    """
    report = BenchmarkReport(results=[
        result("B01", "s", CORRECT, 3.0, [("generate_sql", 2.0), ("fetch_schema", 1.0)]),
        result("B02", "s", CORRECT, 3.0, [("generate_sql", 2.5), ("fetch_schema", 0.5)]),
    ])
    assert list(report.stage_totals()) == ["generate_sql", "fetch_schema"]
    assert report.stage_totals()["generate_sql"] == pytest.approx(4.5)


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


# ---------------------------------------------------------------------------
# Per-agent timing, read from the run's own trace
# ---------------------------------------------------------------------------


class _Entry:
    """Stands in for nl2sql_agent.state.TraceEntry without importing it."""

    def __init__(self, node: str, ms: float = 0.0, model_calls: int = 0) -> None:
        self.node = node
        self.ms = ms
        self.model_calls = model_calls


def test_timing_is_read_from_the_trace_in_milliseconds():
    """v4's nodes time themselves. The harness reports seconds, so the unit
    has to change on the way in or every stage reads a thousand times slow.
    """
    timing = timing_from_trace([_Entry("generate_sql", 1500.0), _Entry("planner_gate", 12.5)])
    assert timing.as_dict() == {"generate_sql": 1.5, "planner_gate": 0.0125}


def test_a_node_that_ran_twice_is_summed_like_any_other_stage():
    timing = timing_from_trace([_Entry("generate_sql", 1000.0), _Entry("generate_sql", 500.0)])
    assert timing.seconds_for("generate_sql") == 1.5


def test_trace_entries_may_arrive_as_plain_dictionaries():
    """A trace read back from a JSON file has no dataclasses in it."""
    timing = timing_from_trace([{"node": "narrate", "ms": 2000.0}])
    assert timing.as_dict() == {"narrate": 2.0}


def test_an_absent_or_empty_trace_yields_no_stages():
    assert timing_from_trace([]).stages == []
    assert timing_from_trace(None).stages == []


def test_an_entry_without_a_duration_still_counts_as_a_stage_that_ran():
    """Better a stage at zero than a stage missing from the breakdown."""
    assert timing_from_trace([_Entry("visualise")]).as_dict() == {"visualise": 0.0}


def test_model_calls_are_counted_per_agent():
    """The architecture's economic claim: three calls on the happy path. A
    number nothing counts is a number nobody can check.
    """
    calls = model_calls_from_trace(
        [_Entry("supervise", model_calls=1), _Entry("generate_sql", model_calls=1),
         _Entry("narrate", model_calls=1), _Entry("planner_gate")]
    )
    assert calls == {"supervise": 1, "generate_sql": 1, "narrate": 1}


def test_a_node_that_called_the_model_twice_is_summed():
    calls = model_calls_from_trace(
        [_Entry("generate_sql", model_calls=1), _Entry("generate_sql", model_calls=1)]
    )
    assert calls == {"generate_sql": 2}


def test_nodes_that_called_no_model_are_left_out_rather_than_recorded_as_zero():
    """The interesting fact is which agents cost a call, and a table of
    fourteen zeroes hides it."""
    assert model_calls_from_trace([_Entry("validate_static"), _Entry("audit")]) == {}


def test_model_calls_survive_a_trace_that_came_back_as_json():
    assert model_calls_from_trace([{"node": "narrate", "model_calls": 2}]) == {"narrate": 2}
