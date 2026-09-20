#!/usr/bin/env python3
"""Run the benchmark: 15 questions, scored on accuracy then on speed.

    python benchmarks/run_benchmark.py
    python benchmarks/run_benchmark.py --compare          # v1 vs v2 vs v3
    python benchmarks/run_benchmark.py --only B07 B08
    python benchmarks/run_benchmark.py --json out.json

Accuracy is execution accuracy: the agent's SQL is run and its rows compared
against reference SQL whose answer was verified against the shipped dataset.
Speed is reported per question and per pipeline stage, because "slow" and "slow
in generate_sql" call for different fixes.

`--compare` runs the same questions through three configurations that differ
only in what retrieval is switched on, which is the measurement the whole
project is for:

    schema-only   no knowledge, no examples          (v1 behaviour)
    knowledge     knowledge base, no examples        (v2 behaviour)
    multi-shot    knowledge + reranked examples      (v3 behaviour)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "agent") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "agent"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.questions import CATEGORIES, QUESTIONS, BenchmarkQuestion  # noqa: E402
from benchmarks.runner import (  # noqa: E402
    CORRECT,
    ERROR,
    FAILED,
    WRONG,
    BenchmarkReport,
    QuestionResult,
    StageTimer,
    model_calls_from_trace,
    result_matches,
    timing_from_trace,
)

# The agent's own defaults are compose service names -- `postgres`, `vectordb`,
# `chunkdb`, `host.docker.internal` -- because that is where it normally runs.
# The benchmark runs on the host, where none of those resolve, so each published
# port is used instead. An environment variable still wins, so running the
# benchmark inside the compose network needs no flags.
HOST_DEFAULTS = {
    "database_url": ("DATABASE_URL", "postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail"),
    "vector_db_url": ("VECTOR_DB_URL", "postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors"),
    "context_db_url": ("CONTEXT_DB_URL", "postgresql+psycopg://ragproc:ragproc@localhost:5433/nl2sql_chunks"),
    "embed_base_url": ("EMBED_BASE_URL", "http://localhost:11434"),
}


# The three configurations --compare measures. Each is the one before it plus a
# retrieval stage, so a difference between two rows is attributable to that
# stage and nothing else.
CONFIGURATIONS = {
    "schema-only": {"rag_enabled": False, "examples_enabled": False, "multi_shot_enabled": False},
    "knowledge": {"rag_enabled": True, "examples_enabled": False, "multi_shot_enabled": False},
    "multi-shot": {"rag_enabled": True, "examples_enabled": True, "multi_shot_enabled": True},
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--only", nargs="+", metavar="ID", help="run just these question ids")
    p.add_argument("--category", choices=CATEGORIES, help="run only this category")
    p.add_argument(
        "--compare", action="store_true",
        help="run every configuration in CONFIGURATIONS and print them side by side",
    )
    p.add_argument("--config", choices=sorted(CONFIGURATIONS), default="multi-shot")
    p.add_argument("--json", metavar="PATH", help="also write the full results as JSON")
    p.add_argument("--database-url", help="override the retail database URL")
    p.add_argument("--model", help="override the chat model")
    p.add_argument("--base-url", help="override the chat model host")
    p.add_argument("-v", "--verbose", action="store_true", help="show each pipeline step")
    return p.parse_args(argv)


def select(args: argparse.Namespace) -> list[BenchmarkQuestion]:
    questions = list(QUESTIONS)
    if args.category:
        questions = [q for q in questions if q.category == args.category]
    if args.only:
        wanted = {i.upper() for i in args.only}
        questions = [q for q in questions if q.id in wanted]
        missing = wanted - {q.id for q in questions}
        if missing:
            raise SystemExit(f"error: no such question(s): {', '.join(sorted(missing))}")
    if not questions:
        raise SystemExit("error: no questions selected")
    return questions


def build_settings(args: argparse.Namespace, configuration: str):
    from nl2sql_agent.config import Settings

    settings = Settings.from_env()
    for field, (variable, host_default) in HOST_DEFAULTS.items():
        if variable not in os.environ:
            setattr(settings, field, host_default)
    for field, value in CONFIGURATIONS[configuration].items():
        setattr(settings, field, value)
    if args.database_url:
        settings.database_url = args.database_url
    if args.model:
        settings.ollama_model = args.model
    if args.base_url:
        settings.ollama_base_url = args.base_url
    return settings


def reference_rows(database, question: BenchmarkQuestion) -> list[list]:
    result = database.run_select(question.reference_sql)
    return [list(row) for row in result.rows]


def narrative_score(state) -> float | None:
    """The fraction of the narrative's claims the audit traced back to a cell.

    Execution accuracy says whether the SQL was right. This says whether the
    user was told the truth about it, which is a different failure and one
    nothing in the v3 benchmark could see.
    """
    claims = state.get("claims") or []
    if not claims:
        return None
    report = state.get("audit")
    unsupported = len(getattr(report, "unsupported_claims", []) or []) if report else 0
    return round((len(claims) - unsupported) / len(claims), 4)


def run_question(agent, database, question: BenchmarkQuestion, timer: StageTimer) -> QuestionResult:
    """Ask one question, score it, and record where the time went."""
    expected = reference_rows(database, question)

    timer.start()
    started = time.perf_counter()
    try:
        state = agent.run(question.question)
    except Exception as exc:  # a crash is a benchmark result, not a benchmark failure
        return QuestionResult(
            question_id=question.id, category=question.category, question=question.question,
            outcome=FAILED, wall_seconds=time.perf_counter() - started, timing=timer.timing,
            error=str(exc)[:400], expected_row_count=len(expected),
        )
    wall = time.perf_counter() - started

    # A v4 run times each node itself. Prefer that over the gaps between
    # progress callbacks, which cannot attribute time across Stage 1's
    # parallel branches -- the four retrievers overlap, so the gap after one
    # of them is not its duration.
    trace = state.get("trace") or []
    timing = timing_from_trace(trace) if trace else timer.timing

    common = dict(
        question_id=question.id, category=question.category, question=question.question,
        wall_seconds=wall, timing=timing, sql=state.get("sql"),
        attempts=state.get("attempts", 0), expected_row_count=len(expected),
        examples=[p["pair_id"] for p in state.get("example_pairs", [])],
        knowledge_chunks=len(state.get("knowledge_chunks", [])),
        model_calls=model_calls_from_trace(trace),
        narrative_score=narrative_score(state),
    )

    if state.get("error"):
        # Either the graph gave up, or the database rejected the final query.
        outcome = ERROR if state.get("sql") else FAILED
        return QuestionResult(outcome=outcome, error=state["error"][:400], **common)

    result = state.get("result")
    if result is None:
        # No error and no rows means the Supervisor stopped the run before any
        # SQL was written -- it judged the question out of scope, an
        # injection, or too ambiguous to answer. That is a wrong answer for a
        # benchmark question by definition, and it has to be scored as one
        # rather than crash the run: a screen that refuses real questions is
        # exactly the regression this number should catch.
        verdict = state.get("verdict", "unknown")
        return QuestionResult(
            outcome=FAILED,
            error=f"refused before generating SQL: verdict={verdict}",
            **common,
        )
    rows = result["rows"] if isinstance(result, dict) else result.rows
    matched = result_matches(expected, rows, ordered=question.ordered)
    return QuestionResult(
        outcome=CORRECT if matched else WRONG, row_count=len(rows), **common
    )


def run_configuration(args, configuration: str, questions: list[BenchmarkQuestion]) -> BenchmarkReport:
    from nl2sql_agent.database import Database
    from nl2sql_agent.graph import Nl2SqlAgent
    from nl2sql_agent.llm import LlmUnavailableError

    settings = build_settings(args, configuration)
    database = Database(
        settings.database_url, db_schema=settings.db_schema,
        statement_timeout_ms=settings.statement_timeout_ms, max_rows=settings.max_rows,
    )

    def show(step: str, detail: str) -> None:
        if args.verbose:
            print(f"      [{step}] {detail[:90]}", file=sys.stderr)

    timer = StageTimer(show)
    try:
        agent = Nl2SqlAgent(settings, on_progress=timer)
    except LlmUnavailableError as exc:
        raise SystemExit(f"error: {exc}")

    report = BenchmarkReport(label=configuration)
    for question in questions:
        print(f"  {question.id} [{question.category}] {question.question[:64]}...", flush=True)
        result = run_question(agent, database, question, timer)
        report.results.append(result)
        mark = {CORRECT: "ok", WRONG: "WRONG", ERROR: "ERROR", FAILED: "FAILED"}[result.outcome]
        print(f"       -> {mark} in {result.wall_seconds:.1f}s", flush=True)
    return report


def print_report(report: BenchmarkReport) -> None:
    print(f"\n{'=' * 72}\n{report.label or 'benchmark'}\n{'=' * 72}")

    print("\nACCURACY")
    print(f"  execution accuracy   {report.correct}/{report.total}  ({100 * report.accuracy:.1f}%)")
    print(f"  produced runnable SQL {report.answered}/{report.total}")
    print("\n  by category")
    for category, (correct, total) in sorted(report.by_category().items()):
        bar = "#" * correct + "." * (total - correct)
        print(f"    {category:<10} {correct}/{total}  {bar}")

    scored = [r.narrative_score for r in report.results if r.narrative_score is not None]
    if scored:
        # Execution accuracy says whether the SQL was right. This says whether
        # the user was told the truth about it, which nothing in the v3
        # harness could see.
        traced = sum(scored) / len(scored)
        print(f"  narrative traced      {100 * traced:.1f}%  ({len(scored)} of {report.total} narrated)")

    missed = [r for r in report.results if not r.correct]
    if missed:
        print("\n  not correct")
        for result in missed:
            detail = result.error or f"{result.row_count} rows, expected {result.expected_row_count}"
            print(f"    {result.question_id} {result.outcome:<7} {detail[:60]}")

    print("\nSPEED")
    print(f"  total                {report.total_seconds:.1f}s for {report.total} questions")
    print(f"  median per question  {report.median_seconds:.1f}s")
    print("\n  slowest")
    for result in report.slowest(3):
        print(f"    {result.question_id}  {result.wall_seconds:6.1f}s  {result.question[:52]}")

    calls: dict[str, int] = {}
    for result in report.results:
        for node, n in (result.model_calls or {}).items():
            calls[node] = calls.get(node, 0) + n
    if calls:
        # The architecture's economic claim, made checkable: three calls on
        # the happy path, and a repair classifier that keeps most retries
        # from costing a fourth.
        total_calls = sum(calls.values())
        per_question = total_calls / report.total if report.total else 0
        breakdown = ", ".join(f"{node} {n}" for node, n in sorted(calls.items()))
        print(f"\n  model calls          {total_calls} ({per_question:.1f} per question)")
        print(f"    {breakdown}")

    totals = report.stage_totals()
    if totals:
        overall = sum(totals.values()) or 1.0
        print("\n  where the time went")
        for stage, seconds in totals.items():
            share = 100 * seconds / overall
            print(f"    {stage:<20} {seconds:7.1f}s  {share:5.1f}%  {'#' * int(share / 3)}")


def print_comparison(reports: list[BenchmarkReport]) -> None:
    print(f"\n{'=' * 72}\ncomparison\n{'=' * 72}\n")
    print(f"  {'configuration':<14} {'accuracy':>12} {'answered':>10} {'total':>9} {'median':>9}")
    print(f"  {'-' * 14} {'-' * 12} {'-' * 10} {'-' * 9} {'-' * 9}")
    for report in reports:
        print(
            f"  {report.label:<14} {report.correct:>4}/{report.total} "
            f"({100 * report.accuracy:>4.0f}%) {report.answered:>10} "
            f"{report.total_seconds:>8.1f}s {report.median_seconds:>8.1f}s"
        )

    categories = sorted({c for r in reports for c in r.by_category()})
    print(f"\n  by category\n  {'configuration':<14}" + "".join(f"{c:>12}" for c in categories))
    for report in reports:
        cells = report.by_category()
        row = "".join(
            f"{cells.get(c, (0, 0))[0]:>8}/{cells.get(c, (0, 0))[1]:<4}" for c in categories
        )
        print(f"  {report.label:<14}{row}")


def as_json(reports: list[BenchmarkReport]) -> dict:
    return {
        "configurations": [
            {
                "label": report.label,
                "accuracy": report.accuracy,
                "correct": report.correct,
                "answered": report.answered,
                "total": report.total,
                "total_seconds": report.total_seconds,
                "median_seconds": report.median_seconds,
                "stage_totals": report.stage_totals(),
                "by_category": {k: list(v) for k, v in report.by_category().items()},
                "results": [
                    {
                        "id": r.question_id,
                        "category": r.category,
                        "question": r.question,
                        "outcome": r.outcome,
                        "wall_seconds": round(r.wall_seconds, 3),
                        "stages": {k: round(v, 3) for k, v in r.timing.as_dict().items()},
                        "attempts": r.attempts,
                        "rows": r.row_count,
                        "expected_rows": r.expected_row_count,
                        "examples": r.examples,
                        "knowledge_chunks": r.knowledge_chunks,
                        "model_calls": r.model_calls,
                        "narrative_score": r.narrative_score,
                        "sql": r.sql,
                        "error": r.error,
                    }
                    for r in report.results
                ],
            }
            for report in reports
        ]
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    questions = select(args)
    configurations = list(CONFIGURATIONS) if args.compare else [args.config]

    reports = []
    for configuration in configurations:
        print(f"\n--- {configuration} ({len(questions)} questions) ---")
        reports.append(run_configuration(args, configuration, questions))

    for report in reports:
        print_report(report)
    if len(reports) > 1:
        print_comparison(reports)

    if args.json:
        Path(args.json).write_text(json.dumps(as_json(reports), indent=2, default=str))
        print(f"\nwrote {args.json}")

    # Non-zero when anything was not correct, so CI can gate on it.
    return 0 if all(r.accuracy == 1.0 for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
