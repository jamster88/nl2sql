"""Validating a SQL snippet against the live retail database.

A snippet is not a query, so it cannot be run as one. What can be run is the
smallest query in which it plays the part its kind says it plays -- a join
joined, a filter filtering, a measure measured -- written over the FROM
clause the snippet says it applies to. That is what this module builds, and
it runs each one the way `validation.py` runs a reviewer's fix: as
`nl2sql_reader`, in a READ ONLY transaction, under a statement timeout.

Running it is the check that matters, and also the one a person cannot do by
eye: the planner is what knows that `f.sales_date_key` exists and `f.date_key`
does not. Beyond "it runs", each kind is asked the question that kind gets
wrong without failing:

* a **join** is counted before and after -- one that multiplies rows (the
  market-share fan-out) or loses them (an inner join to a sparse fact) runs
  perfectly and answers wrongly, so the curator is told by how much;
* a **filter** is counted as well -- one that matches nothing is the
  misspelled literal ('Dairy and Eggs' for 'Dairy & Eggs'), and one that
  matches everything filters nothing;
* a **measure** has to come back as one value, or it is not an aggregate;
* a **dimension** is grouped by, which an aggregate cannot be.

Before any of that, the checks that need no database: each piece is one
fragment, with no statement terminator and no comment that could hide the
rest of the probe, and with its parentheses and quotes closed -- because a
fragment that closes a parenthesis it did not open would validate a
different query from the one the agent is shown.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg
from psycopg import sql as pgsql

from .validation import SAMPLE_ROWS, _message, _plan_cost, json_safe

KINDS = ("join", "filter", "measure", "dimension")

#: What each kind's SQL is, for messages and the interface.
KIND_ROLES = {
    "join": "a JOIN clause",
    "filter": "a WHERE condition",
    "measure": "an aggregate expression",
    "dimension": "an expression to group by",
}

_JOIN_START = re.compile(
    r"^\s*(?:(?:inner|cross|natural)\s+|(?:left|right|full)(?:\s+outer)?\s+)?join\b", re.I
)
_LEADING = {
    "filter": re.compile(r"^\s*where\s+", re.I),
    "applies_to": re.compile(r"^\s*from\s+", re.I),
}
_SELECT_START = re.compile(r"^\s*(select|with)\b", re.I)


@dataclass
class SnippetValidation:
    """What running the snippet showed, and whether it may be stored."""

    kind: str
    valid: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: The query the snippet was run inside -- shown, so the curator can see
    #: what was checked rather than take it on trust.
    probe_sql: str = ""
    applies_to: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    #: Rows of the FROM clause alone, and with the snippet applied: a join's
    #: result, or a filter's matches. None for a measure and a dimension.
    rows_before: int | None = None
    rows_after: int | None = None
    #: Retail tables the two pieces name, by word, for the `tables` field.
    tables: list[str] = field(default_factory=list)
    plan_cost: float | None = None
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean(text: str, piece: str) -> str:
    """The fragment as it will be placed: no fence, no trailing semicolon,
    and no leading WHERE or FROM, which the probe writes itself."""
    cleaned = (text or "").strip()
    fence = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1)
    cleaned = cleaned.strip().rstrip(";").strip()
    leading = _LEADING.get(piece)
    if leading:
        cleaned = leading.sub("", cleaned, count=1)
    return cleaned.strip()


def _balance_problems(text: str, label: str) -> list[str]:
    """Unclosed quotes and unbalanced parentheses, outside string literals."""
    depth = 0
    quote: str | None = None
    for char in text:
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return [f"{label} closes a parenthesis it never opened"]
    if quote:
        return [f"{label} has an unclosed {quote} quote"]
    if depth:
        return [f"{label} leaves {depth} parenthesis(es) open"]
    return []


def _fragment_problems(text: str, label: str) -> list[str]:
    if not text:
        return [f"{label} is empty"]
    found: list[str] = []
    if ";" in text:
        found.append(f"{label} contains a semicolon; a snippet is one fragment, not a statement")
    if "--" in text or "/*" in text:
        found.append(f"{label} contains a comment, which would hide part of the query it is checked in")
    if "```" in text:
        found.append(f"{label} contains a ``` fence, which ends its block in the document early")
    found.extend(_balance_problems(text, label))
    return found


def static_problems(kind: str, applies_to: str, snippet: str) -> list[str]:
    """What is wrong before anything is sent anywhere."""
    if kind not in KINDS:
        return [f"{kind!r} is not a kind of snippet; it is one of {', '.join(KINDS)}"]
    found = _fragment_problems(applies_to, "applies to")
    found += _fragment_problems(snippet, "the SQL")
    if applies_to and _SELECT_START.match(applies_to):
        found.append("applies to is a FROM clause -- tables and joins -- not a query")
    if snippet and kind == "join" and not _JOIN_START.match(snippet):
        found.append("a join snippet starts with JOIN (or LEFT JOIN, INNER JOIN, ...)")
    if snippet and kind != "join" and _SELECT_START.match(snippet):
        found.append(f"a {kind} snippet is {KIND_ROLES[kind]}, not a query")
    return found


def probe_for(kind: str, applies_to: str, snippet: str, sample_rows: int = SAMPLE_ROWS) -> str:
    """The query a snippet of this kind is checked inside."""
    if kind == "join":
        return f"SELECT *\nFROM {applies_to}\n{snippet}\nLIMIT {sample_rows}"
    if kind == "filter":
        return f"SELECT *\nFROM {applies_to}\nWHERE {snippet}\nLIMIT {sample_rows}"
    if kind == "measure":
        # Wrapped, so a measure that is not an aggregate costs a row cap and
        # not a million rows sent back to say so.
        return f"SELECT * FROM (\n  SELECT {snippet} AS value\n  FROM {applies_to}\n) probe\nLIMIT 2"
    return (
        f"SELECT {snippet} AS value, count(*) AS row_count\nFROM {applies_to}\n"
        f"GROUP BY 1\nORDER BY 2 DESC\nLIMIT {sample_rows}"
    )


def count_for(kind: str, applies_to: str, snippet: str) -> str | None:
    """The before-and-after count a join or a filter is judged by."""
    if kind == "join":
        return (
            f"SELECT (SELECT count(*) FROM {applies_to}) AS rows_before,\n"
            f"       (SELECT count(*) FROM {applies_to}\n{snippet}) AS rows_after"
        )
    if kind == "filter":
        return (
            f"SELECT count(*) AS rows_before, count(*) FILTER (WHERE {snippet}) AS rows_after\n"
            f"FROM {applies_to}"
        )
    return None


def tables_named(text: str, known: list[str]) -> list[str]:
    """Known table names that appear as words in the text, in the order of `known`."""
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower()))
    return [name for name in known if name.lower() in words]


def _judge(kind: str, result: SnippetValidation) -> None:
    """The kind's own question, asked of what came back."""
    before, after = result.rows_before, result.rows_after
    if kind == "join" and before is not None and after is not None:
        # Only a RIGHT or FULL join gives an empty table rows, and then
        # there is no per-row ratio to state.
        if after > before and before:
            result.warnings.append(
                f"the join multiplies rows: {before:,} become {after:,} "
                f"({after / before:.2f} per row). Summing a column of the first table "
                "over it counts each row more than once."
            )
        elif after < before:
            result.warnings.append(
                f"the join drops rows: {before - after:,} of {before:,} find no match "
                "and leave the result -- a LEFT JOIN keeps them"
            )
    if kind == "filter" and before is not None and after is not None:
        if after == 0:
            result.warnings.append(
                "the filter matches no rows -- make sure that is intended and not a "
                "literal spelled differently from the data"
            )
        elif after == before:
            result.warnings.append(f"the filter matches every one of the {before:,} rows; it filters nothing")
    if kind == "measure" and len(result.rows) > 1:
        result.valid = False
        result.problems.append(
            "a measure comes back as one value over the rows -- SUM, COUNT, AVG and the "
            "like -- and this returned one value per row"
        )
    if kind == "dimension" and not result.rows:
        result.warnings.append("the expression grouped no rows; the FROM clause it applies to is empty")


def validate_snippet(
    kind: str,
    applies_to: str,
    snippet: str,
    *,
    url: str,
    timeout_ms: int = 30000,
    known_tables: list[str] | None = None,
    sample_rows: int = SAMPLE_ROWS,
    principal: str | None = None,
    connect: Callable[..., Any] = psycopg.connect,
) -> SnippetValidation:
    """Run the snippet inside its probe, and its count, and say whether it may be kept."""
    kind = (kind or "").strip().lower()
    base = clean(applies_to, "applies_to")
    piece = clean(snippet, kind)
    result = SnippetValidation(kind=kind, valid=False, applies_to=base, sql=piece)
    result.problems = static_problems(kind, base, piece)
    if result.problems:
        return result
    result.probe_sql = probe_for(kind, base, piece, sample_rows)
    counting = count_for(kind, base, piece)

    started = time.perf_counter()
    try:
        conn = connect(url, connect_timeout=10)
    except Exception as exc:  # noqa: BLE001 - reported to the curator, not raised
        result.problems = [f"cannot reach the retail database to validate it: {_message(exc)}"]
        return result
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(
            pgsql.SQL("SET LOCAL statement_timeout = {}").format(pgsql.Literal(int(timeout_ms)))
        )
        if principal:
            # Signed in, a person's SQL runs as them -- the role their
            # questions run as -- so what they keep is what they can read.
            conn.execute(pgsql.SQL("SET LOCAL ROLE {}").format(pgsql.Identifier(principal)))
        if known_tables is None:
            known_tables = [
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema() ORDER BY table_name"
                ).fetchall()
            ]
        plan = conn.execute(f"EXPLAIN (FORMAT JSON) {result.probe_sql}").fetchone()[0]
        cursor = conn.execute(result.probe_sql)
        result.columns = [column.name for column in (cursor.description or [])]
        fetched = cursor.fetchmany(sample_rows + 1)
        if counting is not None:
            counted = conn.execute(counting).fetchone()
            result.rows_before, result.rows_after = int(counted[0]), int(counted[1])
    except Exception as exc:  # noqa: BLE001 - the database's refusal is the answer
        result.problems = [f"the database refused it: {_message(exc)}"]
        result.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return result
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    result.valid = True
    result.rows = [[json_safe(cell) for cell in row] for row in fetched[:sample_rows]]
    result.plan_cost = _plan_cost(plan)
    result.tables = tables_named(f"{base} {piece}", known_tables)
    result.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    _judge(kind, result)
    return result
