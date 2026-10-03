"""The benchmark in MLflow: a run per configuration, its questions' traces in it.

Driven through `run_benchmark.main` with the stub agent and a fake MLflow, so
what is checked is what a person finds in the MLflow interface after a real
benchmark: one run per configuration, named for it, holding the settings it
measured, its score and timings, the report, and every question's trace with
the benchmark's verdict on it. The other half -- that the runs API is not
there in the agent image's client, and the benchmark says so rather than
failing -- is checked the same way.
"""

from __future__ import annotations

import pytest
from benchmarks import run_benchmark, tracking
from benchmarks.questions import by_id
from benchmarks.runner import CORRECT, FAILED, WRONG, BenchmarkReport, QuestionResult, StageTiming
from nl2sql_agent.config import Settings
from nl2sql_agent.tracing import Tracer

from tests.fake_mlflow import FakeMlflow, FakeMlflowWithRuns

from .test_run_benchmark import _drive

RIGHT = {"sql": "SELECT 1", "result": {"rows": [[10]], "columns": ["n"], "truncated": False}, "attempts": 1}
WRONG_ROWS = {"sql": "SELECT 2", "result": {"rows": [[11]], "columns": ["n"], "truncated": False}, "attempts": 3}


def tracer_on(client) -> Tracer:
    return Tracer(Settings(mlflow_tracking_uri="http://localhost:5001"), client=client, probe=lambda uri: None)


def result(question_id: str, outcome: str, *, seconds: float = 1.0, trace_id: str = "", **kwargs) -> QuestionResult:
    question = by_id(question_id)
    return QuestionResult(
        question_id=question.id, category=question.category, question=question.question,
        outcome=outcome, wall_seconds=seconds, timing=StageTiming(stages=[("generate_sql", seconds)]),
        trace_id=trace_id, **kwargs,
    )


# ---------------------------------------------------------------------------
# One run per configuration
# ---------------------------------------------------------------------------


def test_a_configuration_is_one_run_with_its_settings_score_and_report(monkeypatch, capsys):
    mlflow = FakeMlflowWithRuns()
    code, agents = _drive(monkeypatch, RIGHT, argv=["--only", "B01", "B02"], tracer=tracer_on(mlflow))

    assert code == 0
    [run] = mlflow.runs.values()
    assert run["name"] == "benchmark snippets"
    assert run["status"] == "FINISHED"
    assert run["tags"]["nl2sql.entrypoint"] == "benchmark"
    assert run["params"] == {
        "configuration": "snippets",
        "model": agents[0].settings.ollama_model,
        "model_routing": True,
        "max_attempts": 7,
        "questions": "B01,B02",
    }
    assert run["metrics"]["accuracy"] == 1.0
    assert run["metrics"]["questions"] == 2
    # The report as --json would write it, beside the numbers.
    [document] = run["artifacts"].values()
    assert document["configurations"][0]["label"] == "snippets"
    assert [r["id"] for r in document["configurations"][0]["results"]] == ["B01", "B02"]


def test_each_question_is_a_trace_in_the_run_tagged_and_judged(monkeypatch, capsys):
    mlflow = FakeMlflowWithRuns()
    _drive(monkeypatch, WRONG_ROWS, argv=["--only", "B01"], tracer=tracer_on(mlflow))

    [run_id] = mlflow.runs
    trace = mlflow.only_trace()
    assert trace.run_id == run_id
    assert trace.tags["nl2sql.entrypoint"] == "benchmark"
    assert trace.tags["benchmark.configuration"] == "snippets"
    assert trace.tags["benchmark.question_id"] == "B01"
    assert trace.tags["benchmark.category"] == by_id("B01").category
    [score] = trace.assessments
    assert (score.name, score.value, score.rationale) == (tracking.SCORE, False, WRONG)
    # Sent before it is scored, or the server has no trace to score.
    assert mlflow.flushes == 1
    assert (score.source.source_type, score.source.source_id) == ("CODE", tracking.SOURCE)


def test_compare_files_each_configuration_as_a_run_of_its_own(monkeypatch, capsys):
    mlflow = FakeMlflowWithRuns()
    _drive(monkeypatch, RIGHT, argv=["--only", "B01", "--compare"], tracer=tracer_on(mlflow))

    assert [r["name"] for r in mlflow.runs.values()] == [
        "benchmark schema-only", "benchmark knowledge", "benchmark multi-shot", "benchmark snippets",
    ]
    assert {t.run_id for t in mlflow.traces.values()} == set(mlflow.runs)


def test_a_configuration_cut_short_keeps_what_it_measured_under_a_status_that_says_so(monkeypatch, capsys):
    mlflow = FakeMlflowWithRuns()
    asked = run_benchmark.run_question

    def interrupted_on_the_second(agent, database, question, timer):
        if question.id == "B02":
            raise KeyboardInterrupt
        return asked(agent, database, question, timer)

    monkeypatch.setattr(run_benchmark, "run_question", interrupted_on_the_second)
    with pytest.raises(KeyboardInterrupt):
        _drive(monkeypatch, RIGHT, argv=["--only", "B01", "B02"], tracer=tracer_on(mlflow))
    [run] = mlflow.runs.values()
    assert run["status"] == "KILLED"
    assert run["metrics"]["questions"] == 1


def test_untraced_there_is_no_run_and_the_benchmark_is_unchanged(monkeypatch, capsys):
    code, _ = _drive(monkeypatch, RIGHT, argv=["--only", "B01"])
    assert code == 0
    assert "note:" not in capsys.readouterr().err


def test_with_only_the_tracing_client_the_traces_are_written_ungrouped_and_it_says_so(monkeypatch, capsys):
    mlflow = FakeMlflow()
    code, _ = _drive(monkeypatch, RIGHT, argv=["--only", "B01"], tracer=tracer_on(mlflow))

    assert code == 0
    assert "traced, but not grouped into an MLflow run -- that needs mlflow-skinny" in capsys.readouterr().err
    trace = mlflow.only_trace()
    assert trace.run_id is None and trace.assessments == []
    assert trace.tags["benchmark.question_id"] == "B01"


# ---------------------------------------------------------------------------
# The pieces
# ---------------------------------------------------------------------------


def test_the_metrics_are_the_score_and_where_the_time_went():
    report = BenchmarkReport(
        label="multi-shot",
        results=[
            result("B01", CORRECT, seconds=2.0, rung="light"),
            result("B02", WRONG, seconds=4.0, rung="heavy"),
            result("B04", CORRECT, seconds=3.0, rung="light"),
        ],
    )
    assert tracking.metrics(report) == {
        "accuracy": 2 / 3,
        "correct": 2,
        "answered": 3,
        "questions": 3,
        "total_seconds": 9.0,
        "median_seconds": 3.0,
        "accuracy.schema": 0.5,
        "accuracy.calendar": 1.0,
        "seconds.generate_sql": 9.0,
        "rung.light": 2,
        "rung.heavy": 1,
    }


def test_a_question_that_crashed_has_no_trace_to_score():
    tracked = tracking.BenchmarkRun(FakeMlflowWithRuns(), "run-1")
    tracked.score(result("B01", FAILED, error="boom"))  # no trace id: nothing to call


def test_a_score_the_server_will_not_take_is_a_note_not_a_failure(capsys):
    tracked = tracking.BenchmarkRun(FakeMlflowWithRuns(), "run-1")
    tracked.score(result("B01", FAILED, trace_id="tr-404", error="refused before generating SQL"))
    assert "note: could not score trace tr-404: no trace tr-404" in capsys.readouterr().err


def test_a_failure_is_scored_with_its_reason():
    mlflow = FakeMlflowWithRuns()
    with mlflow.start_span("nl2sql") as root:
        trace_id = root.trace_id
    tracking.BenchmarkRun(mlflow, "run-1").score(result("B01", FAILED, trace_id=trace_id, error="gave up"))
    [score] = mlflow.traces[trace_id].assessments
    assert (score.value, score.rationale) == (False, "failed: gave up")


def test_on_the_host_the_tracking_server_is_the_published_port(monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    settings = run_benchmark.build_settings(run_benchmark.parse_args([]), "multi-shot")
    assert settings.mlflow_tracking_uri == "http://localhost:5001"


def test_an_empty_tracking_uri_turns_the_benchmarks_tracing_off(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "")
    settings = run_benchmark.build_settings(run_benchmark.parse_args([]), "multi-shot")
    assert settings.mlflow_tracking_uri == ""


def test_the_json_report_names_each_questions_trace():
    report = BenchmarkReport(label="multi-shot", results=[result("B01", CORRECT, trace_id="tr-7")])
    assert run_benchmark.as_json([report])["configurations"][0]["results"][0]["trace_id"] == "tr-7"
