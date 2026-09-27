"""Validating a reviewer's SQL against the live retail database.

A *wrong* answer, or one that was right and incomplete, is fixed by a person
writing the query the agent should have written. Nothing about that query
can be trusted until it has run: a correction that does not parse, names a
column that is not there, or times out is not a correction, and storing one
would teach whatever reads the store to make a new mistake. So a fix goes
into its store only after this module has run it against the database the
agent answers from -- and the save route runs it *again*, because a
browser's word that a query passed is not the same thing as a query passing.

The query is a stranger's, so it runs the way the agent's own queries do:

* as `nl2sql_reader`, which can SELECT from the retail tables and nothing
  else, and whose sessions are read-only by default;
* inside `SET TRANSACTION READ ONLY` as well, so a writing CTE is refused by
  the server even if the role were ever widened;
* under a `statement_timeout`, and with a row cap, so a cross join costs a
  timeout rather than the database.

Before any of that, three checks that need no database: one statement,
which begins with SELECT or WITH, and which is not the query the agent
already generated. The last is the one a person is likeliest to trip: a
correction identical to the SQL it corrects is a verdict with no fix in it.

A later version puts an agent here to sanity-check the reviewer's query
before it runs, and a later one still an agent to help write it. This
module is the part both will sit in front of: whatever they suggest, only a
query that runs is stored.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as clock
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql as pgsql

#: How many rows of the result are sent back to the browser and stored with
#: the fix: enough to see that it is the right answer, not the whole answer.
SAMPLE_ROWS = 20

_FENCE = re.compile(r"^```(?:sql)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_SELECT_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


@dataclass
class Validation:
    """What running the query showed, and whether it may be stored."""

    sql: str
    valid: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    plan_cost: float | None = None
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean(text: str) -> str:
    """The statement as it will be run: no fence, no trailing semicolons."""
    cleaned = (text or "").strip()
    fence = _FENCE.match(cleaned)
    if fence:
        cleaned = fence.group(1)
    return cleaned.strip().rstrip(";").strip()


def normalise(text: str) -> str:
    """Two queries that differ only in spacing and case are the same query."""
    return " ".join(clean(text).lower().split())


def static_problems(statement: str, reference: str = "") -> list[str]:
    """What is wrong with the query before it is ever sent anywhere."""
    if not statement:
        return ["the SQL is empty"]
    problems: list[str] = []
    if ";" in statement:
        problems.append(
            "only one statement can be validated; remove everything after the first semicolon"
        )
    if not _SELECT_START.match(statement):
        problems.append("an answer is a SELECT (or WITH ... SELECT); nothing else is run")
    if reference and normalise(statement) == normalise(reference):
        problems.append(
            "this is the query the agent generated; a fix has to change it"
        )
    return problems


def json_safe(value: Any) -> Any:
    """A cell as JSON can carry it without losing what it said.

    Decimals become strings rather than floats: `719279.97` is the answer a
    reviewer checked, and a float would store `719279.9699999999`.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, clock)):
        return value.isoformat()
    return str(value)


def _plan_cost(plan: Any) -> float | None:
    try:
        return float(plan[0]["Plan"]["Total Cost"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _message(exc: BaseException) -> str:
    """The server's own words, and its hint when it gave one.

    Postgres' primary message rather than the whole error text: the full text
    quotes the statement it failed on, which here is the `EXPLAIN` wrapper
    this module added, with a caret pointing into that rather than into what
    the reviewer typed.
    """
    diag = getattr(exc, "diag", None)
    primary = getattr(diag, "message_primary", None) if diag is not None else None
    if primary:
        hint = getattr(diag, "message_hint", None)
        return f"{primary} -- {hint}" if hint else primary
    text = str(exc).strip() or type(exc).__name__
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def validate(
    statement: str,
    *,
    url: str,
    reference: str = "",
    timeout_ms: int = 30000,
    max_rows: int = 200,
    sample_rows: int = SAMPLE_ROWS,
    connect: Callable[..., Any] = psycopg.connect,
) -> Validation:
    """Run the query against the retail database and say whether it may be kept.

    Valid means it passed the static checks, planned, and ran to completion
    inside the limits. An empty result is valid with a warning -- "no rows"
    is sometimes the answer -- and so is a truncated one.
    """
    cleaned = clean(statement)
    problems = static_problems(cleaned, reference)
    if problems:
        return Validation(sql=cleaned, valid=False, problems=problems)

    started = time.perf_counter()
    try:
        conn = connect(url, connect_timeout=10)
    except Exception as exc:  # noqa: BLE001 - reported to the reviewer, not raised
        return Validation(
            sql=cleaned,
            valid=False,
            problems=[f"cannot reach the retail database to validate it: {_message(exc)}"],
        )
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(
            pgsql.SQL("SET LOCAL statement_timeout = {}").format(pgsql.Literal(int(timeout_ms)))
        )
        plan = conn.execute(f"EXPLAIN (FORMAT JSON) {cleaned}").fetchone()[0]
        cursor = conn.execute(cleaned)
        columns = [column.name for column in (cursor.description or [])]
        fetched: Sequence[Sequence[Any]] = cursor.fetchmany(max_rows + 1)
    except Exception as exc:  # noqa: BLE001 - the database's refusal is the answer
        return Validation(
            sql=cleaned,
            valid=False,
            problems=[f"the database refused it: {_message(exc)}"],
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )
    finally:
        # Rolled back rather than committed: nothing here should have
        # changed anything, and a rollback says so to the server as well.
        try:
            conn.rollback()
        finally:
            conn.close()

    truncated = len(fetched) > max_rows
    rows = [[json_safe(cell) for cell in row] for row in fetched[:max_rows]]
    warnings: list[str] = []
    if not rows:
        warnings.append(
            "it ran and returned no rows -- make sure that is the answer and not a "
            "filter that matches nothing"
        )
    if truncated:
        warnings.append(f"it returned more than {max_rows} rows; only the first {max_rows} were read")
    return Validation(
        sql=cleaned,
        valid=True,
        warnings=warnings,
        columns=columns,
        rows=rows[:sample_rows],
        row_count=len(rows),
        truncated=truncated,
        plan_cost=_plan_cost(plan),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
    )
