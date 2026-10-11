"""The grain check: section 22.14 of the v7.3 architecture.

Every fact here is stamped with a date, and not every fact has a row for
every date. Sales are daily. Costs are monthly: one row a product, stamped
on one date of each fiscal month. Join the two on the date and each month's
cost meets one day's sales, so a margin for the month is computed from one
day of it -- about a thirtieth of the answer, with nothing in the SQL to say
so. The planner has no objection, the rows come back, and the number is
wrong. It is the best-documented trap in the knowledge base, the generator
is shown that passage, and runs fall into it all the same: on the paraphrase
set it was the majority's answer on three of one question's four wordings.

Whether a join is at the right grain is a question code can answer once it
knows each table's grain, and this module is that answer in three parts.

**The grains, measured.** `read_grains` asks the data, once a process: for
each table with a column that references the date dimension, how many of
its distinct dates fall in any one fiscal week, and in any one fiscal month.
A table with at most one date in every month is monthly, whatever its key
is called and whatever anyone wrote down about it. Measured rather than
declared, because a declaration goes wrong silently when the data changes
and a measurement cannot.

**The rule.** `mismatches` reads a query -- with the reading the dissent
uses (`completeness.read_query`) and its own, below -- for two tables it
aggregates, joined to each other, whose grains differ, in one of two shapes:

* **joined on the date** -- each one's date column is in the same join
  equality, directly or through the date dimension's key, and the finer one
  was not rolled up to the coarser's period first;
* **joined on no date** -- nothing about time is among the columns they are
  joined on, and the coarser still brings a row per period to the join:
  read row by row, or rolled up to columns that keep its date or a period.
  Each row of the finer then meets every period's row of the coarser.

**The words.** `message` says both grains and the way round, for the Repair
Agent to pass on as written. It writes no SQL.

The rule rejects, so it has to be sure. A deterministic gate that sends a
right query to repair is the mistake arch6's first section warns about, made
in code -- and what it costs is not a wrong sentence in a note but an attempt
of the shared budget and a rewrite of something that was correct. So every
question the reading cannot answer is answered "pass":

* a join condition that is not an equality between two qualified columns --
  an expression, a `NATURAL` join, a `USING` across several tables;
* a column whose origin cannot be followed through a CTE or a subquery -- a
  `SELECT *`, a set operation;
* a table read more than once;
* the coarser table held to what may be one period -- its date, or every
  column of its period, compared with a constant, or any filter on its time
  this cannot weigh: a range, a list, a subquery. One period's rows, joined
  on product alone, are right;
* a roll-up it cannot see through: a window function, a `DISTINCT ON`.

What it does not catch it leaves to what was there before it: the Judge,
which reads the knowledge, and the dissent, which describes the difference.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from pglast import ast, enums

from . import completeness as reading
from .contract import CALENDAR_TABLE
from .repair import GRAIN_ISSUE

#: The finest grain, and the name of the grain of a table no period holds at
#: most one date of.
DAY = "day"

#: The two shapes of a mismatch.
ON_THE_DATE = "on the date"
ON_NO_DATE = "on no date"

#: What a period's last column is called beyond the period's own name:
#: `fiscal_week_num` is the fiscal week.
_COUNTER = re.compile(r"_(num|number|no|nbr|id|key|code)$")

#: A foreign key to one column of another table, as `pg_get_constraintdef`
#: writes it: the column, the table (with or without its schema), its column.
_FOREIGN_KEY = re.compile(
    r'^FOREIGN KEY \("?(?P<column>\w+)"?\) REFERENCES (?:"?\w+"?\.)?"?(?P<table>\w+)"?\("?(?P<key>\w+)"?\)'
)


class UnknownPeriod(ValueError):
    """`GRAIN_PERIODS` names a column the date dimension does not have."""


@dataclass(frozen=True)
class Grain:
    """How often one table has a row: its date column, and the coarsest
    period that holds at most one of its dates."""

    table: str
    #: Its column that references the date dimension's key.
    column: str
    #: "day", or the period's name: "fiscal week", "fiscal month".
    period: str = DAY
    #: The period's columns in the date dimension; none for the day.
    columns: tuple[str, ...] = ()
    #: 0 for the day, then the period's place in `GRAIN_PERIODS`, which is
    #: written finest first: the larger, the coarser.
    order: int = 0


@dataclass(frozen=True)
class Mismatch:
    """Two tables of different grains, joined in a way that miscounts."""

    finer: Grain
    coarser: Grain
    shape: str


# ---------------------------------------------------------------------------
# The grains, read once
# ---------------------------------------------------------------------------


def period_name(columns: Sequence[str]) -> str:
    """A period by its last column: `fiscal_year, fiscal_month_num` is the
    fiscal month."""
    return _COUNTER.sub("", columns[-1].lower()).replace("_", " ")


def date_column(table: Any, calendar: str = CALENDAR_TABLE) -> tuple[str, str] | None:
    """The column a table is stamped with and the date dimension's column it
    references, or None for a table that references no date. Of several, the
    first in the table's own column order: the date it is stamped with."""
    found = {}
    for constraint in table.constraints:
        match = _FOREIGN_KEY.match(constraint)
        if match and match.group("table").lower() == calendar:
            found[match.group("column")] = match.group("key")
    for column in table.columns:
        if column.name in found:
            return column.name, found[column.name]
    return None


def read_grains(
    database: Any,
    tables: Sequence[Any],
    periods: Sequence[Sequence[str]],
    *,
    calendar: str = CALENDAR_TABLE,
) -> dict[str, Grain]:
    """Each date-stamped table's grain, measured from its own dates.

    `tables` is the catalog (`Database.catalog()`), which is where a table's
    foreign key to the date dimension is read from; `periods` is
    `GRAIN_PERIODS`, finest first. One query a table
    (`Database.dates_per_period`), over its distinct dates and never its
    rows. A table's grain is the coarsest period that holds at most one of
    its dates; one with two dates in some week is daily; one with no dates
    has no grain, and neither has a table in a database with no date
    dimension, where there is nothing to read and nothing wrong.

    `UnknownPeriod` when a period names a column the date dimension lacks.
    The caller records that and a database error alike, and stands the
    check down: a check that cannot read its grains has nothing to say.
    """
    dates = next((table for table in tables if table.name == calendar), None)
    if dates is None or not periods:
        return {}
    held = {column.name.lower() for column in dates.columns}
    missing = sorted({name for period in periods for name in period} - held)
    if missing:
        raise UnknownPeriod(
            f"GRAIN_PERIODS names {', '.join(missing)}, which {calendar} does not have "
            f"(it has {', '.join(sorted(held))})"
        )

    grains: dict[str, Grain] = {}
    for table in tables:
        stamped = date_column(table, calendar) if table.name != calendar else None
        if stamped is None:
            continue
        column, key = stamped
        most = database.dates_per_period(table.name, column, periods, calendar=calendar, key=key)
        if not any(most):
            continue
        # The coarsest period no one of which holds two of its dates.
        order = max((place for place, count in enumerate(most, start=1) if count <= 1), default=0)
        period = tuple(periods[order - 1]) if order else ()
        grains[table.name] = Grain(
            table=table.name, column=column, period=period_name(period) if period else DAY,
            columns=period, order=order,
        )
    return grains


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def mismatches(query: reading._Query, grains: Mapping[str, Grain], *, calendar: str = CALENDAR_TABLE) -> list[Mismatch]:
    """The joins of `query` that combine tables of different grains wrongly.

    The candidates are the reading's own: the tables the query aggregates
    that are joined to another it aggregates (`joined_on`). For each two of
    them whose grains differ, the statement's SELECTs are read for the two
    shapes above; whatever cannot be read is no mismatch.
    """
    measured = sorted(table for table in query.joined_on if table in grains)
    if query.statement is None or len(measured) < 2:
        return []
    statement = _Statement(query.statement, grains, calendar)
    found: list[Mismatch] = []
    for one, other in combinations(measured, 2):
        finer, coarser = sorted((grains[one], grains[other]), key=lambda grain: grain.order)
        if finer.order == coarser.order or statement.read_twice(finer.table, coarser.table):
            continue
        shape = statement.shape(finer, coarser, query.rolled_up)
        if shape:
            found.append(Mismatch(finer=finer, coarser=coarser, shape=shape))
    return found


@dataclass(frozen=True)
class _Side:
    """One side of a join equality: the column as written, and what it reads.

    `bases` are the base columns under it, followed through any CTE or
    subquery that outputs it, or None when that cannot be followed; `bare`
    is whether it is one base column as stored rather than an expression
    over some -- `MIN(date_key) AS date_key` reads a date and is not one.
    """

    qualifier: str
    name: str
    bases: frozenset[tuple[str, str]] | None
    bare: bool


class _Statement:
    """A parsed statement, read for how its date-stamped tables meet."""

    def __init__(self, statement: ast.Node, grains: Mapping[str, Grain], calendar: str) -> None:
        self.grains = grains
        self.calendar = calendar
        self.ctes = {
            node.ctename.lower(): node.ctequery
            for node in reading._walk(statement)
            if isinstance(node, ast.CommonTableExpr)
        }
        self.selects = [
            node for node in reading._walk(statement) if isinstance(node, ast.SelectStmt) and node.larg is None
        ]
        self.scopes = {id(select): reading._scope(select, self.ctes) for select in self.selects}
        self.joins = {id(select): self._joins(select) for select in self.selects}

    # --- what a column reads --------------------------------------------------

    def _read(self, select: ast.SelectStmt, qualifier: str, name: str,
              seen: frozenset[int] = frozenset()) -> tuple[frozenset[tuple[str, str]] | None, bool]:
        """The base columns `qualifier.name` reads in `select`, and whether
        it is one of them as stored."""
        source = self.scopes[id(select)].get(qualifier)
        if source is None:
            return None, False
        if isinstance(source, str):
            return frozenset({(source, name)}), True
        return self._output(source, name, seen)

    def _output(self, select: ast.Node, name: str,
                seen: frozenset[int]) -> tuple[frozenset[tuple[str, str]] | None, bool]:
        """The same for the column `name` a CTE's or subquery's SELECT
        outputs. Unreadable: a set operation, a star, a name it does not
        output, a column no one name of its FROM can be said to own."""
        if id(select) in seen or id(select) not in self.scopes:
            return None, False
        targets = select.targetList or ()
        if any(_is_star(target.val) for target in targets):
            return None, False
        for target in targets:
            value = target.val
            shown = target.name or (reading._column_name(value) if isinstance(value, ast.ColumnRef) else None)
            if (shown or "").lower() != name:
                continue
            bases: set[tuple[str, str]] = set()
            bare = isinstance(value, ast.ColumnRef)
            for ref in reading._walk(value):
                if not isinstance(ref, ast.ColumnRef):
                    continue
                qualifier = reading._qualifier(ref) or _only(self.scopes[id(select)])
                found, stored = self._read(select, qualifier, reading._column_name(ref) or "", seen | {id(select)})
                if found is None:
                    return None, False
                bases |= found
                bare = bare and stored
            return frozenset(bases), bare
        return None, False

    def _joins(self, select: ast.SelectStmt) -> list[list[_Side]]:
        """A SELECT's join equalities, in classes: the columns it makes equal."""
        return [
            [_Side(qualifier, name, *self._read(select, qualifier, name)) for qualifier, name in sorted(linked)]
            for linked in reading._linked_columns(reading._join_pairs(select))
        ]

    # --- whose a column is ----------------------------------------------------

    def _calendars(self, select: ast.SelectStmt) -> set[str]:
        """The names the date dimension goes by in a SELECT's own FROM."""
        return {name for name, source in self.scopes[id(select)].items() if source == self.calendar}

    def _is_date(self, side: _Side, grain: Grain) -> bool:
        """Is this side the table's own date column, as stored?"""
        return side.bare and side.bases == frozenset({(grain.table, grain.column)})

    def _ties(self, select: ast.SelectStmt) -> dict[str, set[str]]:
        """Which tables each of the SELECT's date dimensions is the calendar
        of: the ones whose date column is joined to it."""
        calendars = self._calendars(select)
        ties: dict[str, set[str]] = {name: set() for name in calendars}
        for linked in self.joins[id(select)]:
            dated = {grain.table for grain in self.grains.values() if any(self._is_date(side, grain) for side in linked)}
            for side in linked:
                if side.qualifier in calendars:
                    ties[side.qualifier] |= dated
        return ties

    def _holds(self, select: ast.SelectStmt, qualifier: str) -> set[str]:
        """The date-stamped tables a name of a SELECT's FROM stands for:
        itself, or the ones a CTE or subquery reads."""
        source = self.scopes[id(select)].get(qualifier)
        if source is None:
            return set()
        if isinstance(source, str):
            return {source} & set(self.grains)
        return reading._tables_under(source, self.ctes, set()) & set(self.grains)

    def _is_time(self, side: _Side, calendars: set[str]) -> bool:
        """Does this side say anything about when? The date dimension's
        column, a table's own date, an expression over either -- or a column
        that cannot be followed, which may."""
        if side.qualifier in calendars or side.bases is None:
            return True
        return any(
            table == self.calendar or (table in self.grains and self.grains[table].column == column)
            for table, column in side.bases
        )

    def read_twice(self, *tables: str) -> bool:
        """Is any of these tables in more than one FROM? Then which reading
        of it a join or a filter belongs to cannot be told."""
        named = [source for scope in self.scopes.values() for source in scope.values() if isinstance(source, str)]
        return any(named.count(table) > 1 for table in tables)

    # --- the two shapes -------------------------------------------------------

    def shape(self, finer: Grain, coarser: Grain, rolled_up: Mapping[str, tuple[str, ...]]) -> str | None:
        """How `finer` and `coarser` are wrongly joined, if they are."""
        for select in self.selects:
            if self._on_the_date(select, finer, coarser, rolled_up):
                return ON_THE_DATE
        for select in self.selects:
            if self._on_no_date(select, finer, coarser, rolled_up):
                return ON_NO_DATE
        return None

    def _on_the_date(self, select: ast.SelectStmt, finer: Grain, coarser: Grain,
                     rolled_up: Mapping[str, tuple[str, ...]]) -> bool:
        """One join equality holds each one's date column -- so through the
        date dimension's key counts, the three being one class -- and the
        finer was not rolled up to the coarser's period first."""
        joined = any(
            any(self._is_date(side, finer) for side in linked) and any(self._is_date(side, coarser) for side in linked)
            for linked in self.joins[id(select)]
        )
        rolled = rolled_up.get(finer.table)
        return joined and not (rolled is not None and all(column in rolled for column in coarser.columns))

    def _on_no_date(self, select: ast.SelectStmt, finer: Grain, coarser: Grain,
                    rolled_up: Mapping[str, tuple[str, ...]]) -> bool:
        """They are joined, on nothing about time, and the coarser still
        brings a row per period."""
        calendars, ties = self._calendars(select), self._ties(select)

        def owners(side: _Side) -> set[str]:
            return ties[side.qualifier] if side.qualifier in calendars else self._holds(select, side.qualifier)

        linking = [
            linked for linked in self.joins[id(select)]
            if any(finer.table in owners(side) for side in linked) and any(coarser.table in owners(side) for side in linked)
        ]
        if not linking or any(self._is_time(side, calendars) for linked in linking for side in linked):
            return False
        # Joined to each other, then, and on no date or period -- in what this
        # SELECT's equalities say. A join it could not read may say more.
        if self._unread_join(select):
            return False

        rolled = rolled_up.get(coarser.table)
        if rolled is None:
            # Row by row -- which can only be said of a SELECT that reads the
            # table itself. Behind a CTE or a subquery that does not group
            # it, a DISTINCT ON or a window may have left it one row a key.
            if not any(self.scopes[id(select)].get(side.qualifier) == coarser.table for linked in linking for side in linked):
                return False
        elif not (coarser.column in rolled or set(rolled) & self._period_columns()):
            return False  # rolled up past its period: its rows are no longer by period
        return not self._may_be_one_period(coarser)

    def _period_columns(self) -> set[str]:
        return {column for grain in self.grains.values() for column in grain.columns}

    def _unread_join(self, select: ast.SelectStmt) -> bool:
        """Does this SELECT join on anything the reading does not read? A
        `NATURAL` join; a `USING` with a join on one side; a condition that
        compares columns of two names of its FROM and is not a plain
        equality between them -- `s.month = date_trunc('month', d.day)`."""
        pending = list(select.fromClause or [])
        while pending:
            item = pending.pop()
            if isinstance(item, ast.JoinExpr):
                pending.extend([item.larg, item.rarg])
                if item.isNatural or (item.usingClause and not all(reading._range_name(side) for side in (item.larg, item.rarg))):
                    return True
        for term in _conditions(select):
            if _is_join_equality(term):
                continue
            if len({reading._qualifier(ref) for ref in reading._walk(term) if isinstance(ref, ast.ColumnRef)}) > 1:
                return True
        return False

    # --- one period, or every one ---------------------------------------------

    def _may_be_one_period(self, grain: Grain) -> bool:
        """May the statement hold `grain.table` to the rows of one period?

        Yes when its date column, or every column of its period, is compared
        with a constant -- wherever the table is read, or on what a CTE or
        subquery that holds it outputs. Yes, too, when its time is filtered
        in any way this cannot weigh: a range may be a month. No only when
        nothing filters its time, or equalities pin some of its period's
        columns and not all: the fiscal year alone is twelve months.
        """
        pinned: set[str] = set()
        for select in self.selects:
            scope = self.scopes[id(select)]
            direct = {name for name, source in scope.items() if source == grain.table}
            derived = {name for name, source in scope.items()
                       if not isinstance(source, str) and grain.table in reading._tables_under(source, self.ctes, set())}
            if not direct and not derived:
                continue
            own = {name for name, tables in self._ties(select).items() if grain.table in tables}
            for term in _conditions(select):
                if _is_join_equality(term):
                    continue
                for ref in reading._walk(term):
                    if not isinstance(ref, ast.ColumnRef):
                        continue
                    time = self._time_filtered(select, ref, grain, direct, derived, own)
                    if time is None:
                        continue
                    if time == "" or not _equals_constant(term, ref):
                        return True
                    pinned.add(time)
        return grain.column in pinned or all(column in pinned for column in grain.columns)

    def _time_filtered(self, select: ast.SelectStmt, ref: ast.ColumnRef, grain: Grain,
                       direct: set[str], derived: set[str], own: set[str]) -> str | None:
        """What of the table's time a filter's column is: its date column,
        or a column of its own period, by name; "" for any other time -- a
        calendar date, a week of a monthly table, an expression -- which may
        be one period and cannot be weighed; None for a column that is not
        its time at all."""
        qualifier, name = reading._qualifier(ref), reading._column_name(ref) or ""
        if not qualifier:
            return ""  # whose column it is cannot be told, and it may be the date's
        if qualifier in direct:
            return name if name == grain.column else None
        if qualifier in own:
            return name if name in grain.columns else ""
        if qualifier not in derived:
            return None
        bases, bare = self._read(select, qualifier, name)
        if bases is None:
            return ""
        times = {(table, column) for table, column in bases
                 if table == self.calendar or (table == grain.table and column == grain.column)}
        if not times:
            return None
        if not bare:
            return ""
        table, column = next(iter(times))
        return column if table == grain.table or column in grain.columns else ""


def _only(scope: Mapping[str, Any]) -> str:
    """The one name a FROM has, for a column written without one; a name no
    FROM has when there are several, so that it reads as nothing."""
    return next(iter(scope)) if len(scope) == 1 else ""


def _is_star(value: ast.Node) -> bool:
    return isinstance(value, ast.ColumnRef) and bool(value.fields) and isinstance(value.fields[-1], ast.A_Star)


def _conditions(select: ast.SelectStmt) -> list[ast.Node]:
    """Every term a SELECT's WHERE and its joins' ON clauses AND together."""
    conditions = [select.whereClause]
    pending = list(select.fromClause or [])
    while pending:
        item = pending.pop()
        if isinstance(item, ast.JoinExpr):
            pending.extend([item.larg, item.rarg])
            conditions.append(item.quals)
    return [term for condition in conditions for term in reading._conjuncts(condition)]


def _is_equality(term: ast.Node) -> bool:
    return (
        isinstance(term, ast.A_Expr) and term.kind == enums.A_Expr_Kind.AEXPR_OP
        and bool(term.name) and term.name[-1].sval == "="
    )


def _is_join_equality(term: ast.Node) -> bool:
    """`a.x = b.y`: a join the reading reads, not a filter."""
    return (
        _is_equality(term)
        and isinstance(term.lexpr, ast.ColumnRef) and isinstance(term.rexpr, ast.ColumnRef)
        and bool(reading._qualifier(term.lexpr)) and bool(reading._qualifier(term.rexpr))
    )


def _is_constant(node: ast.Node | None) -> bool:
    if isinstance(node, ast.TypeCast):
        return _is_constant(node.arg)
    return isinstance(node, ast.A_Const)


def _equals_constant(term: ast.Node, ref: ast.ColumnRef) -> bool:
    """Is `term` this column compared for equality with a constant?"""
    if not _is_equality(term):
        return False
    return (term.lexpr is ref and _is_constant(term.rexpr)) or (term.rexpr is ref and _is_constant(term.lexpr))


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


def _every(grain: Grain) -> str:
    """A grain as an adjective: daily, weekly, monthly."""
    return "daily" if grain.period == DAY else f"{grain.period.split()[-1]}ly"


def message(mismatch: Mismatch) -> str:
    """What is wrong and the way round, in the grains' own names: the issue
    the Repair Agent hands the generator as written."""
    finer, coarser = mismatch.finer, mismatch.coarser
    columns = ", ".join(coarser.columns)
    if mismatch.shape == ON_THE_DATE:
        return (
            f"{coarser.table} is {_every(coarser)} -- one date in each {coarser.period} -- and {finer.table} is "
            f"{_every(finer)}; joined on the date, each {coarser.period}'s row of {coarser.table} meets only the "
            f"rows of {finer.table} for that one date. Roll {finer.table} up to the {coarser.period} ({columns}) "
            "and join on those columns, not on the date."
        )
    return (
        f"{coarser.table} is {_every(coarser)} and {finer.table} is {_every(finer)}, and they are joined on no "
        f"date or period, so each row of {finer.table} meets every {coarser.period}'s row of {coarser.table}. "
        f"Join them on the {coarser.period} as well ({columns}), or roll {coarser.table} up past the "
        f"{coarser.period} before joining it."
    )


def describes(text: str) -> bool:
    """Is this issue one of the grain check's? By its own words, which is
    all an issue carries: the benchmark counts the runs it sent to repair."""
    return bool(GRAIN_ISSUE.search(text or ""))


def issues(sql: str, grains: Mapping[str, Grain]) -> Iterator[str]:
    """The grain check's messages for a statement, none when it passes or
    cannot be read."""
    for mismatch in mismatches(reading.read_query(sql), grains):
        yield message(mismatch)
