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

**Speed is measured per stage, not just per question.** The graph reports each
node as it finishes, so the gaps between those callbacks are the stage
durations. That is the difference between "slow" and "slow because the chat
model is doing three passes".
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import permutations
from typing import Any, Callable, Iterable, Sequence

# Relative tolerance for numeric comparison, for values that agree closely in
# their own right.
RELATIVE_TOLERANCE = 1e-6
ABSOLUTE_TOLERANCE = 1e-9

# Two numbers also match when they agree once rounded to this many decimals.
# The reference queries round to 2 for legibility and agents usually do not, so
# a pure relative tolerance rejects correct answers: 48.6076 against a reference
# 48.61 is off by 5e-5 relative, which is thirty times 1e-6 and yet obviously
# the same number. Rounding both sides is the comparison a person would make.
# It stays far from the real mistakes, which are wrong by multiples: the grain
# trap returns 98.81 against 34.20 and the fan-out 5x.
ROUNDING_DECIMALS = 2

# Outcomes, worst last so a report can sort by them.
CORRECT = "correct"
WRONG = "wrong"
FAILED = "failed"  # the agent produced no runnable SQL
ERROR = "error"  # the agent's SQL was rejected by the database


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def values_match(expected: Any, actual: Any) -> bool:
    """One cell against another, tolerant of how the number was produced."""
    if expected is None or actual is None:
        return expected is None and actual is None
    if _is_number(expected) and _is_number(actual):
        left, right = float(expected), float(actual)
        if abs(left - right) <= max(
            ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * max(abs(left), abs(right))
        ):
            return True
        return round(left, ROUNDING_DECIMALS) == round(right, ROUNDING_DECIMALS)
    # Booleans and strings compare after normalising case and padding, because
    # CHAR columns come back space-padded and 't'/'true' are the same answer.
    return str(expected).strip().lower() == str(actual).strip().lower()


def _row_matches(expected: Sequence[Any], actual: Sequence[Any]) -> bool:
    return all(values_match(e, a) for e, a in zip(expected, actual))


# Guard against a pathologically wide result turning the search below into a
# combinatorial one. Nothing in the benchmark returns more than a few columns.
MAX_COLUMN_ASSIGNMENTS = 5000


def result_matches(
    expected_rows: Sequence[Sequence[Any]],
    actual_rows: Sequence[Sequence[Any]],
    *,
    ordered: bool = False,
) -> bool:
    """Does the agent's result contain the reference answer?

    Column order and naming are presentation, extra columns are allowed, and
    row order only counts when the question asked for one. What is left after
    all that is the real question: is there a way of reading the agent's
    columns under which its rows *are* the reference rows?

    So the search is over column assignments. For each way of picking one agent
    column per reference column, the agent's rows are projected onto those
    columns and compared. Matching columns independently -- checking each
    reference column against some agent column on its own -- is the tempting
    shortcut and is wrong: it accepts a result whose values are all present but
    attached to the wrong rows, which is exactly what a join on the wrong key
    produces.
    """
    expected_rows = [list(r) for r in expected_rows]
    actual_rows = [list(r) for r in actual_rows]

    if not expected_rows:
        return not actual_rows
    if len(expected_rows) != len(actual_rows):
        return False

    expected_width = len(expected_rows[0])
    actual_width = len(actual_rows[0])
    if actual_width < expected_width:
        return False

    if math.perm(actual_width, expected_width) > MAX_COLUMN_ASSIGNMENTS:
        # Fall back to the reference's own column order rather than guessing.
        assignments: Iterable[tuple[int, ...]] = [tuple(range(expected_width))]
    else:
        assignments = permutations(range(actual_width), expected_width)

    reference = expected_rows if ordered else _sorted_rows(expected_rows)
    for assignment in assignments:
        projected = [[row[i] for i in assignment] for row in actual_rows]
        candidate = projected if ordered else _sorted_rows(projected)
        if all(_row_matches(e, a) for e, a in zip(reference, candidate)):
            return True
    return False


def _sorted_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """A stable order that does not depend on a column's values being mutually
    comparable -- NULLs, numbers and strings arrive in the same column."""
    return sorted(rows, key=lambda row: [_sort_key(v) for v in row])


def _sort_key(value: Any) -> tuple:
    if value is None:
        return (0, 0.0, "")
    if _is_number(value):
        return (1, float(value), "")
    return (2, 0.0, str(value))


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


class StageTimer:
    """Wraps the agent's progress callback and records the gaps between calls.

    The graph calls back as each node *finishes*, so the elapsed time since the
    previous callback is that node's duration. The first gap is measured from
    when the run started rather than from process start.
    """

    def __init__(self, inner: Callable[[str, str], None] | None = None) -> None:
        self._inner = inner
        self.timing = StageTiming()
        self._last = time.perf_counter()

    def start(self) -> None:
        self._last = time.perf_counter()
        self.timing = StageTiming()

    def __call__(self, step: str, detail: str) -> None:
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

    def stage_totals(self) -> dict[str, float]:
        """Seconds spent in each pipeline stage across the whole run."""
        totals: dict[str, float] = {}
        for result in self.results:
            for stage, seconds in result.timing.as_dict().items():
                totals[stage] = totals.get(stage, 0.0) + seconds
        return dict(sorted(totals.items(), key=lambda kv: -kv[1]))
