"""Do two results hold the same answer? The benchmark's scorer, in the agent.

Moved here from `benchmarks/runner.py` unchanged (arch7 section 22.6; the
implementation specification's section 3.2): the ensemble's agreement step
compares one candidate's rows with another's using exactly the rule the
benchmark's accuracy rests on, and the agent does not import `benchmarks/`.
`benchmarks/runner.py` imports it back by name, so the benchmark scores with
the same functions it always did.

The comparison is forgiving about presentation and strict about values:

- **Column order and names are ignored.** `SELECT count(*) AS n` and
  `SELECT count(*) AS store_count` are the same answer.
- **Extra columns are allowed.** A result that carries the department name
  beside the total has the answer; one that omits the total has not. The test
  is whether there is *some* choice of the wider result's columns under which
  its rows are the other's -- checking each column against some column
  independently is the tempting shortcut, and it wrongly accepts a result
  whose values are all present but attached to the wrong rows, which is what
  a join on the wrong key produces.
- **Row order is ignored unless the question asks for an order.**
- **Numbers compare with tolerance**: within a relative `1e-6`, or equal once
  both are rounded to two places.
"""

from __future__ import annotations

import math
from decimal import Decimal
from itertools import permutations
from typing import Any, Iterable, Sequence

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
