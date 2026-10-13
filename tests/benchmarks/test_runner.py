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
    timing_from_trace,
)


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


def _worded(qid, outcome, wording) -> QuestionResult:
    worded = result(qid, "schema", outcome, 1.0)
    worded.wording = wording
    return worded


def test_stability_is_the_fraction_of_questions_right_in_every_wording():
    """arch7's premise, measured: a question is stable when all four of its
    wordings came out right. One wrong rewording makes its question
    unstable however many others were right."""
    report = BenchmarkReport(results=[
        *(_worded("B01", CORRECT, wording) for wording in range(4)),
        _worded("B02", CORRECT, 0), _worded("B02", WRONG, 1), _worded("B02", CORRECT, 2), _worded("B02", CORRECT, 3),
    ])
    assert report.paraphrased
    assert [r.wording for r in report.by_question()["B02"]] == [0, 1, 2, 3]
    assert (report.stable, report.stability) == (1, 0.5)
    assert report.accuracy == 7 / 8, "the wordings' accuracy is not the stability"


def test_one_wording_a_question_is_not_the_paraphrase_set():
    report = BenchmarkReport(results=[result("B01", "schema", CORRECT, 1.0), result("B02", "schema", WRONG, 1.0)])
    assert not report.paraphrased
    assert report.stability == report.accuracy == 0.5
    assert BenchmarkReport().stability == 0.0


def test_a_result_is_labelled_by_its_question_and_its_wording():
    assert result("B07", "grain", CORRECT, 1.0).label == "B07"
    assert _worded("B07", CORRECT, 2).label == "B07.2"


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


def test_an_entry_that_names_no_node_is_not_a_stage():
    """Neither a trace entry nor a dictionary with a `node` -- a stray value
    in a hand-edited results file -- is skipped rather than timed as `None`."""
    timing = timing_from_trace([{"ms": 10.0}, object(), _Entry("narrate", 2000.0)])
    assert timing.as_dict() == {"narrate": 2.0}


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


def test_the_benchmark_scores_with_the_agents_scorer():
    """The scorer moved into the agent for the ensemble's agreement step
    (arch7); the benchmark imports it back by name, so one function decides
    both "correct" and "agree". Its cases are tests/agent/test_compare.py."""
    from benchmarks import runner
    from nl2sql_agent import compare

    for name in ("values_match", "result_matches", "MAX_COLUMN_ASSIGNMENTS", "ROUNDING_DECIMALS"):
        assert getattr(runner, name) is getattr(compare, name)


# ---------------------------------------------------------------------------
# Per-model attribution (arch5.2 section 11, item 5)
# ---------------------------------------------------------------------------


class _Routed(_Entry):
    def __init__(self, node, model="", rung="", ms=0.0, hops=()):
        super().__init__(node, ms)
        self.model, self.rung, self.hops = model, rung, list(hops)


def test_the_routes_are_the_entries_that_called_a_model():
    trace = [_Routed("retrieve_schema", ms=3.0), _Routed("generate_sql", "m:7b", "light", 900.0, ["x: down"]),
             {"node": "narrate", "model": "n:3b", "rung": "light", "ms": 300.0}]
    from benchmarks.runner import routes_from_trace

    assert routes_from_trace(trace) == [
        {"node": "generate_sql", "model": "m:7b", "rung": "light", "ms": 900.0, "hops": ["x: down"]},
        {"node": "narrate", "model": "n:3b", "rung": "light", "ms": 300.0, "hops": []},
    ]
    assert routes_from_trace(None) == []


def _routed_result(qid, outcome, rung, *routes):
    r = result(qid, "schema", outcome, 1.0)
    r.rung = rung
    r.routes = [{"node": n, "model": m, "rung": g, "ms": ms, "hops": list(h)} for n, m, g, ms, h in routes]
    return r


def test_accuracy_and_p50_are_attributed_per_agent_rung_and_model():
    """A question counts toward every route its run used."""
    report = BenchmarkReport(results=[
        _routed_result("B01", CORRECT, "light", ("generate_sql", "small:7b", "light", 1000.0, ()),
                       ("generate_sql", "big:70b", "standard", 4000.0, ["small:7b: empty"])),
        _routed_result("B02", WRONG, "light", ("generate_sql", "small:7b", "light", 3000.0, ())),
        _routed_result("B03", CORRECT, "standard", ("generate_sql", "small:7b", "light", 2000.0, ())),
        result("B04", "schema", CORRECT, 1.0),
    ])
    assert report.by_model() == [
        {"node": "generate_sql", "rung": "light", "model": "small:7b", "calls": 3, "questions": 3,
         "correct": 2, "p50_seconds": 2.0, "hops": 0},
        {"node": "generate_sql", "rung": "standard", "model": "big:70b", "calls": 1, "questions": 1,
         "correct": 1, "p50_seconds": 4.0, "hops": 1},
    ]
    assert report.rungs() == {"light": 2, "standard": 1}


def test_a_candidates_step_is_timed_like_any_other_and_forwarded_without_its_index():
    """Under the ensemble (arch7) a run's steps carry `candidate=k`; the timer
    times them all the same, and the caller's callback is the two-argument
    one it always was."""
    seen = []
    timer = StageTimer(lambda step, detail: seen.append((step, detail)))
    timer("generate_sql", "SELECT 1", candidate=0)
    assert [name for name, _ in timer.timing.stages] == ["generate_sql"]
    assert seen == [("generate_sql", "SELECT 1")]


# ---------------------------------------------------------------------------
# The ensemble's measures (arch7 section 11)
# ---------------------------------------------------------------------------


def _ensembled(
    qid, outcome, level, *, rejections=None, rungs=("light",), size=1000, runs=4, judged="accepted", without=None,
    wave2=False, joined=(), declined=(), added=0, dropped=0,
) -> QuestionResult:
    asked = result(qid, "schema", outcome, 1.0)
    asked.agreement, asked.candidates, asked.agreed = level, runs, 3
    asked.rejections = dict(rejections or {})
    asked.candidate_rungs, asked.state_bytes = list(rungs), size
    asked.judged, asked.without_judge = judged, without or outcome
    asked.wave2, asked.columns_fused = wave2, True
    asked.joined = [{"column": c, "from_candidate": 1, "key": "store_key", "table": "dim_store"} for c in joined]
    asked.declined = [{"column": c, "from_candidate": 2, "why": "row 1 has no match in run 2"} for c in declined]
    asked.claims_added, asked.claims_dropped = added, dropped
    return asked


def test_one_run_a_question_is_not_the_ensemble():
    report = BenchmarkReport(results=[result("B01", "schema", CORRECT, 1.0)])
    assert not report.ensembled
    assert report.agreement_levels() == {} and report.fidelity_rejections() == {} and report.state_sizes() == []


def test_the_ensembles_agreement_rejections_spread_and_size():
    report = BenchmarkReport(results=[
        _ensembled("B01", CORRECT, "unanimous", rejections={"F4": 2}, rungs=("light", "light"), size=900),
        _ensembled("B02", WRONG, "majority", rejections={"F2": 1, "F4": 1}, rungs=("light", "standard"), size=300),
        _ensembled("B03", WRONG, "contested", size=600),
        _ensembled("B04", CORRECT, "unanimous"),
    ])
    assert report.ensembled
    assert report.agreement_levels() == {"unanimous": 2, "majority": 1, "contested": 1}
    assert report.agreed_on_wrong == 1, "the majority that agreed on a wrong answer; contested is not agreement"
    assert report.fidelity_rejections() == {"F2": 1, "F4": 3}
    assert report.rung_spread == 1
    assert report.state_sizes() == [300, 600, 900, 1000]


def test_what_the_judge_did_and_where_it_changed_the_score():
    """arch7.1: the Judge's effect, scored both ways -- the answer delivered
    against the one the runs alone would have chosen."""
    report = BenchmarkReport(results=[
        _ensembled("B01", CORRECT, "unanimous"),
        _ensembled("B02", CORRECT, "judged", judged="overruled", without=WRONG),
        _ensembled("B03", WRONG, "judged", judged="overruled", without=CORRECT),
        _ensembled("B04", WRONG, "judged", judged="overruled", without=WRONG),
        _ensembled("B05", WRONG, "contested", judged="accepted none"),
        _ensembled("B06", CORRECT, "majority", judged="set aside"),
        _ensembled("B07", CORRECT, "unanimous", judged=""),
    ])
    assert report.judge_actions() == {"accepted": 1, "accepted none": 1, "overruled": 3, "set aside": 1}
    assert report.judge_effect() == {"overruled": 3, "fixed": 1, "broke": 1}
    assert BenchmarkReport(results=[result("B01", "schema", CORRECT, 1.0)]).judge_actions() == {}


def test_how_often_a_second_wave_ran_and_what_fusion_joined_added_and_dropped():
    """arch7 section 11, items 3 and 8: the second-wave rate, and fusion's
    yield -- columns joined and declined, claims added and dropped."""
    report = BenchmarkReport(results=[
        _ensembled("B01", CORRECT, "unanimous", joined=("region_name",), added=2),
        _ensembled("B02", CORRECT, "majority", wave2=True, declined=("brand_name", "store_key"), dropped=1),
        _ensembled("B03", WRONG, "contested", wave2=True, added=1),
    ])
    assert report.second_waves == 2
    assert report.fusion() == {"columns joined": 1, "columns declined": 2, "claims added": 3, "claims dropped": 1}
