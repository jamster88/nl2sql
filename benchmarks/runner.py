"""Scoring and timing for the benchmark.

**Accuracy is execution accuracy.** The agent's SQL is run and its rows compared
against the reference query's rows. Comparing query *text* would be nearly
useless: two correct queries for the same question rarely look alike, and a
query that differs only in alias naming would score as wrong while one that
differs in a join key would score as right.

The comparison is deliberately forgiving about presentation and strict about
values:

- **Column order and names are ignored.** `SELECT count(*) AS n` and
  `SELECT count(*) AS store_count` are the same answer.
- **Extra columns are allowed.** An agent that returns the department name
  alongside the total has answered the question; one that omits the total has
  not. The test is whether there is *some* choice of the agent's columns under
  which its rows are the reference rows -- checking each reference column
  against some agent column independently is the tempting shortcut, and it
  wrongly accepts a result whose values are all present but attached to the
  wrong rows, which is what a join on the wrong key produces.
- **Row order is ignored unless the question asks for an order.** "Top 5 by
  sales" means the order is the answer; "sales by state" does not.
- **Numbers compare with tolerance.** Rounding, numeric vs float, and
  `Decimal` vs `int` are presentation. A relative tolerance catches the real
  errors -- a 5x fan-out or a 30x grain miss -- without failing on the last
  decimal place.

The comparison itself is `nl2sql_agent.compare` since 7.0, where the
ensemble's agreement step reads it too; it is imported back here by name.

**Speed is measured per stage, not just per question.** The graph reports each
node as it finishes, so the gaps between those callbacks are the stage
durations. That is the difference between "slow" and "slow because the chat
model is doing three passes".
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from nl2sql_agent.compare import (  # noqa: F401 - the scorer, moved into the agent (arch7); imported back by name
    ABSOLUTE_TOLERANCE,
    MAX_COLUMN_ASSIGNMENTS,
    RELATIVE_TOLERANCE,
    ROUNDING_DECIMALS,
    _is_number,
    _row_matches,
    _sort_key,
    _sorted_rows,
    result_matches,
    values_match,
)

# Outcomes, worst last so a report can sort by them.
CORRECT = "correct"
WRONG = "wrong"
FAILED = "failed"  # the agent produced no runnable SQL
ERROR = "error"  # the agent's SQL was rejected by the database


@dataclass
class StageTiming:
    """How long each pipeline node took, in the order they ran."""

    stages: list[tuple[str, float]] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(seconds for _, seconds in self.stages)

    def seconds_for(self, stage: str) -> float:
        """Summed, because generate_sql and validate_sql repeat on a retry."""
        return sum(s for name, s in self.stages if name == stage)

    def as_dict(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for name, seconds in self.stages:
            out[name] = out.get(name, 0.0) + seconds
        return out


def timing_from_trace(trace: Sequence[Any]) -> StageTiming:
    """Per-agent timing read from the run's own trace.

    Preferred over `StageTimer` for the v4 pipeline, and not merely more
    precise: Stage 1's four retrievers are branches of one superstep, so the
    gap between two of their progress callbacks is not either one's duration.
    Each node times itself instead, and reports the result in `trace`.
    """
    timing = StageTiming()
    for entry in trace or ():
        node = getattr(entry, "node", None)
        ms = getattr(entry, "ms", None)
        if node is None and isinstance(entry, dict):
            node, ms = entry.get("node"), entry.get("ms")
        if node is not None:
            timing.stages.append((str(node), float(ms or 0.0) / 1000.0))
    return timing


def model_calls_from_trace(trace: Sequence[Any]) -> dict[str, int]:
    """How many model calls each agent made.

    This is what makes the architecture's economic claim measurable: the
    happy path should cost three calls, and the Repair Agent's classifier
    should keep most retries from costing a fourth.
    """
    calls: dict[str, int] = {}
    for entry in trace or ():
        node = getattr(entry, "node", None)
        count = getattr(entry, "model_calls", None)
        if node is None and isinstance(entry, dict):
            node, count = entry.get("node"), entry.get("model_calls")
        if node is not None and count:
            calls[str(node)] = calls.get(str(node), 0) + int(count)
    return calls


def routes_from_trace(trace: Sequence[Any]) -> list[dict[str, Any]]:
    """Every model call in a run: the node, the model that answered, the rung
    it was routed at and how long it took (arch5.2). Entries without a model
    made no call and are left out."""
    routes = []
    for entry in trace or ():
        fields = entry if isinstance(entry, dict) else vars(entry)
        if fields.get("model"):
            routes.append({
                "node": fields["node"],
                "model": fields["model"],
                "rung": fields.get("rung", ""),
                "ms": float(fields.get("ms") or 0.0),
                "hops": list(fields.get("hops") or []),
            })
    return routes


class StageTimer:
    """Wraps the agent's progress callback and records the gaps between calls.

    The graph calls back as each node *finishes*, so the elapsed time since the
    previous callback is that node's duration. The first gap is measured from
    when the run started rather than from process start.

    Superseded by `timing_from_trace` where the run carries a trace, because
    this cannot attribute time correctly across parallel branches.
    """

    def __init__(self, inner: Callable[[str, str], None] | None = None) -> None:
        self._inner = inner
        self.timing = StageTiming()
        self._last = time.perf_counter()

    def start(self) -> None:
        self._last = time.perf_counter()
        self.timing = StageTiming()

    def __call__(self, step: str, detail: str, candidate: int | None = None) -> None:
        """One node finished. Under the ensemble a candidate's steps carry
        its index; the timer times them all the same."""
        now = time.perf_counter()
        self.timing.stages.append((step, now - self._last))
        self._last = now
        if self._inner is not None:
            self._inner(step, detail)


@dataclass
class QuestionResult:
    """One question's outcome: whether it was right, and where the time went."""

    question_id: str
    category: str
    question: str
    outcome: str
    wall_seconds: float
    timing: StageTiming
    sql: str | None = None
    attempts: int = 0
    row_count: int | None = None
    expected_row_count: int | None = None
    error: str | None = None
    examples: list[str] = field(default_factory=list)
    knowledge_chunks: int = 0
    #: Model calls per agent, from the run's trace. Empty for a v3-shaped run.
    model_calls: dict[str, int] = field(default_factory=dict)
    #: Fraction of the narrative's numbers the Audit Checker traced to a cell.
    #: None when nothing narrated, so it is never confused with zero.
    narrative_score: float | None = None
    #: Every model call, with the model that answered and its rung (arch5.2).
    routes: list[dict[str, Any]] = field(default_factory=list)
    #: The rung the Context Aggregator scored the generator's task at.
    rung: str | None = None
    #: The run's MLflow trace, when it was traced (`benchmarks/tracking.py`).
    trace_id: str = ""
    #: Which wording asked it: 0 the benchmark's own, 1 to 3 the paraphrase
    #: set's rewordings (`benchmarks/paraphrases.py`), whose words `question`
    #: then holds.
    wording: int = 0

    @property
    def label(self) -> str:
        """B07 for the benchmark's own wording, B07.2 for the second rewording."""
        return f"{self.question_id}.{self.wording}" if self.wording else self.question_id

    @property
    def correct(self) -> bool:
        return self.outcome == CORRECT

    @property
    def answered(self) -> bool:
        """Produced runnable SQL, right or wrong. Separating this from accuracy
        matters: a pipeline that gives up is failing differently from one that
        confidently answers the wrong question."""
        return self.outcome in (CORRECT, WRONG)


@dataclass
class BenchmarkReport:
    results: list[QuestionResult] = field(default_factory=list)
    label: str = ""

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def correct(self) -> int:
        return sum(1 for r in self.results if r.correct)

    @property
    def answered(self) -> int:
        return sum(1 for r in self.results if r.answered)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def by_category(self) -> dict[str, tuple[int, int]]:
        """category -> (correct, total). Where the failures cluster says more
        than the headline number: 12/15 with every grain question wrong is a
        different system from 12/15 with three scattered misses."""
        out: dict[str, list[int]] = {}
        for result in self.results:
            bucket = out.setdefault(result.category, [0, 0])
            bucket[0] += int(result.correct)
            bucket[1] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def by_question(self) -> dict[str, list[QuestionResult]]:
        """Question id -> its results, in the order they were asked: with the
        paraphrase set, the benchmark's own wording first, then each rewording."""
        out: dict[str, list[QuestionResult]] = {}
        for result in self.results:
            out.setdefault(result.question_id, []).append(result)
        return out

    @property
    def paraphrased(self) -> bool:
        """Whether a question was asked in words not its own: the paraphrase set."""
        return any(result.wording for result in self.results)

    @property
    def stable(self) -> int:
        """How many questions came out right however they were worded."""
        return sum(1 for results in self.by_question().values() if all(r.correct for r in results))

    @property
    def stability(self) -> float:
        """The fraction of questions every wording of which came out right --
        arch7 section 11's first measure, the premise the ensemble is argued
        from. With one wording a question it is the accuracy, and says
        nothing about wording."""
        questions = self.by_question()
        return self.stable / len(questions) if questions else 0.0

    def seconds(self) -> list[float]:
        return sorted(r.wall_seconds for r in self.results)

    @property
    def total_seconds(self) -> float:
        return sum(r.wall_seconds for r in self.results)

    @property
    def median_seconds(self) -> float:
        values = self.seconds()
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2

    def slowest(self, n: int = 3) -> list[QuestionResult]:
        return sorted(self.results, key=lambda r: -r.wall_seconds)[:n]

    def by_model(self) -> list[dict[str, Any]]:
        """Accuracy and P50 per (agent, rung, model) -- section 11 item 5.

        A question counts toward every route its run used: this says how
        often a question that went through a model came out right, which is
        the calibration's own measure, taken on questions in sequence with
        the models warm and cold as they are in use.
        """
        rows: dict[tuple[str, str, str], dict[str, Any]] = {}
        for result in self.results:
            for route in result.routes:
                key = (route["node"], route["rung"], route["model"])
                row = rows.setdefault(key, {"ms": [], "questions": set(), "correct": set(), "hops": 0})
                row["ms"].append(route["ms"])
                row["questions"].add(result.question_id)
                row["hops"] += len(route["hops"])
                if result.correct:
                    row["correct"].add(result.question_id)
        return [
            {
                "node": node, "rung": rung, "model": model, "calls": len(row["ms"]),
                "questions": len(row["questions"]), "correct": len(row["correct"]),
                "p50_seconds": round(statistics.median(row["ms"]) / 1000, 3), "hops": row["hops"],
            }
            for (node, rung, model), row in sorted(rows.items())
        ]

    def rungs(self) -> dict[str, int]:
        """How many questions the Aggregator scored at each rung: a rung
        nothing is ever routed at is visible here."""
        counts: dict[str, int] = {}
        for result in self.results:
            if result.rung:
                counts[result.rung] = counts.get(result.rung, 0) + 1
        return counts

    def stage_totals(self) -> dict[str, float]:
        """Seconds spent in each pipeline stage across the whole run."""
        totals: dict[str, float] = {}
        for result in self.results:
            for stage, seconds in result.timing.as_dict().items():
                totals[stage] = totals.get(stage, 0.0) + seconds
        return dict(sorted(totals.items(), key=lambda kv: -kv[1]))
