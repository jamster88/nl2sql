"""The benchmark in MLflow: one run per configuration, its questions' traces in it.

When the agent traces -- MLFLOW_TRACKING_URI answers; on the host it is the
compose `mlflow` service's published port unless the environment says
otherwise -- each configuration the benchmark measures becomes one MLflow
run:

* **parameters**: what was measured -- the configuration, the model, whether
  calls were routed, the retry budget, the questions, whether they were
  asked the paraphrase set's four ways;
* **metrics**: the score and where the time went -- accuracy overall and per
  category, answered, the total and median seconds, seconds per stage, and
  how many questions the Aggregator scored at each rung; with the
  paraphrase set, the stability too; through the ensemble, how its runs
  agreed, which checks discarded rewordings, what the Judge did and how
  often its overruling turned a wrong answer right or a right one wrong,
  how often a second wave ran, and what fusion joined, added and dropped;
* **an artifact**: the whole report, as `--json` writes it;
* **traces**: every question's, filed under the run by MLflow itself, tagged
  with the question's id, category and wording, and carrying the benchmark's
  judgement as feedback -- so a wrong answer is one click from the run that
  gave it.

The runs API is MLflow's `mlflow-skinny`, which tests/requirements.txt
installs and the agent image does not: the image carries the tracing client
alone. Without it the traces are still written, just not grouped, and the
benchmark says so rather than failing a measurement over its bookkeeping.
"""

from __future__ import annotations

import sys
from typing import Any

from benchmarks.questions import BenchmarkQuestion
from benchmarks.runner import CORRECT, BenchmarkReport, QuestionResult

#: The feedback each question's trace carries: right or not, by execution.
SCORE = "benchmark_correct"
#: Who judged it, as MLflow records a judgement made by code.
SOURCE = "benchmarks/run_benchmark.py"


def question_tags(question: BenchmarkQuestion, configuration: str, *, wording: int = 0) -> dict[str, str]:
    """The tags a question's trace is filed with, whether or not it is grouped.

    `wording` is 0 for the question's own words and 1 to 3 for the
    paraphrase set's rewordings, so the four traces of one question can be
    told apart -- and found together by its id.
    """
    return {
        "nl2sql.entrypoint": "benchmark",
        "benchmark.configuration": configuration,
        "benchmark.question_id": question.id,
        "benchmark.category": question.category,
        "benchmark.wording": str(wording),
    }


class BenchmarkRun:
    """One configuration's MLflow run, open while its questions are asked."""

    def __init__(self, mlflow: Any, run_id: str) -> None:
        self._mlflow = mlflow
        self.run_id = run_id

    def score(self, result: QuestionResult) -> None:
        """The benchmark's verdict on one answer, as feedback on its trace.

        A run that crashed has no trace id to put it on; its failure is in
        the report and in the run's metrics all the same.
        """
        if not result.trace_id:
            return
        from mlflow.entities import AssessmentSource

        rationale = result.outcome + (f": {result.error}" if result.error else "")
        try:
            # The trace is sent from a queue in the background; until it has
            # gone, the server has nothing to put the score on.
            self._mlflow.flush_trace_async_logging()
            self._mlflow.log_feedback(
                trace_id=result.trace_id,
                name=SCORE,
                value=result.outcome == CORRECT,
                rationale=rationale,
                source=AssessmentSource(source_type="CODE", source_id=SOURCE),
            )
        except Exception as exc:  # noqa: BLE001 - the score is in the report; this is a copy
            print(f"      note: could not score trace {result.trace_id}: {exc}", file=sys.stderr)

    def finish(self, report: BenchmarkReport, document: dict[str, Any], *, complete: bool) -> None:
        """The score as metrics, the report as an artifact, and the run ended.

        A configuration cut short -- interrupted, or stopped by a database
        that went away -- still records what it measured, under a status
        that says it is not the whole benchmark.
        """
        self._mlflow.log_metrics(metrics(report))
        self._mlflow.log_dict(document, "benchmark.json")
        self._mlflow.end_run(status="FINISHED" if complete else "KILLED")


def metrics(report: BenchmarkReport) -> dict[str, float]:
    values: dict[str, float] = {
        "accuracy": report.accuracy,
        "correct": report.correct,
        "answered": report.answered,
        "questions": report.total,
        "total_seconds": round(report.total_seconds, 3),
        "median_seconds": round(report.median_seconds, 3),
    }
    for category, (correct, total) in report.by_category().items():
        values[f"accuracy.{category}"] = correct / total
    for stage, seconds in report.stage_totals().items():
        values[f"seconds.{stage}"] = round(seconds, 3)
    for rung, count in report.rungs().items():
        values[f"rung.{rung}"] = count
    if report.paraphrased:
        values["stability"] = report.stability
        values["stable"] = report.stable
    if report.ensembled:
        for level, count in report.agreement_levels().items():
            values[f"agreement.{level}"] = count
        for check, count in report.fidelity_rejections().items():
            values[f"rejected.{check}"] = count
        values["agreed_on_wrong"] = report.agreed_on_wrong
        values["rung_spread"] = report.rung_spread
        for action, count in report.judge_actions().items():
            values[f"judge.{action.replace(' ', '_')}"] = count
        effect = report.judge_effect()
        values["judge_fixed"] = effect["fixed"]
        values["judge_broke"] = effect["broke"]
        values["second_waves"] = report.second_waves
        for measure, count in report.fusion().items():
            values[measure.replace(" ", "_")] = count
    return values


def open_run(
    agent: Any, configuration: str, questions: list[BenchmarkQuestion], *, paraphrase_set: bool = False,
) -> BenchmarkRun | None:
    """The configuration's run, or None when its traces cannot be grouped."""
    if not agent.tracer.ready():
        return None
    mlflow = agent.tracer.mlflow()
    if not hasattr(mlflow, "start_run"):
        print(
            "  note: traced, but not grouped into an MLflow run -- that needs mlflow-skinny "
            "(pip install -r tests/requirements.txt)",
            file=sys.stderr,
        )
        return None
    from nl2sql_agent import __version__

    settings = agent.settings
    run = mlflow.start_run(
        run_name=f"benchmark {configuration}" + (" (paraphrase set)" if paraphrase_set else ""),
        tags={"nl2sql.entrypoint": "benchmark", "nl2sql.version": __version__},
    )
    mlflow.log_params(
        {
            "configuration": configuration,
            "model": settings.ollama_model,
            "model_routing": settings.model_routing_enabled,
            "max_attempts": settings.max_attempts,
            "questions": ",".join(q.id for q in questions),
            "paraphrase_set": paraphrase_set,
        }
    )
    return BenchmarkRun(mlflow, run.info.run_id)
