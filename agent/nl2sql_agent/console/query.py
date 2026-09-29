"""Running a person's SQL the way the agent runs its own.

When the agent answers wrongly the useful questions are about its gates and
its database, and every one of them has an exact answer. Would the static
validator have let this query through? What does the planner estimate it
costs, against the ceiling the agent refuses above? Does it finish inside
the statement timeout? How many rows does it return, and would the agent have
read them all? This module answers them with the agent's own code, in the
agent's own order:

1. **Static** -- `validate.validate`, twice. Once with no scope, which is
   every check that is about safety (one statement, a SELECT, no writing
   CTE, no INTO, nothing on the function denylist); a query that fails one
   of those is not run here either. Once with the whole schema as scope,
   which is the widest the agent ever allows: a reference outside it --
   `pg_stats`, or a misspelt table the validator suggests a name for -- is
   reported as the refusal it would be, and the query still runs, because
   looking at the catalogue is part of troubleshooting.
2. **Planner** -- `EXPLAIN (FORMAT JSON)`, its cost read by the agent's
   `total_cost` and judged by the agent's `plan_cost_problem`.
3. **Runtime** -- the statement itself, as the agent's role, inside
   `SET TRANSACTION READ ONLY` and the agent's `statement_timeout`, read
   through a server-side cursor so a `SELECT *` over the sales fact fetches
   the rows it shows and no more.

`plan` stops after the second and `analyze` replaces the third with
`EXPLAIN ANALYZE`, which runs the query to time it and returns no rows. The
transaction is rolled back whatever happened: nothing here should have
changed anything, and a rollback says so to the server as well.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as clock, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text

from ..config import Settings
from ..database import Database, plan_cost_problem, strip_sql, total_cost
from ..state import PLANNER, RUNTIME, STATIC
from ..validate import validate

Mode = Literal["run", "plan", "analyze"]
MODES: tuple[Mode, ...] = ("run", "plan", "analyze")


class DatabaseUnavailable(RuntimeError):
    """The database could not be reached at all -- not a query that failed."""


@dataclass
class Finding:
    """One gate's objection, in that gate's own words."""

    stage: str
    message: str


@dataclass
class Verdict:
    """What the agent would have done with this query.

    `stage` is the first gate that would have refused it, or None when none
    would. `notes` are things that are not refusals but change the answer
    the agent would give -- a result longer than it reads.
    """

    accepted: bool
    stage: str | None
    issues: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    """Everything one query showed."""

    sql: str
    mode: Mode
    executed: bool
    verdict: Verdict
    columns: list[tuple[str, str]] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    plan: Any = None
    plan_cost: float | None = None
    error: str | None = None
    elapsed_ms: float = 0.0

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass
class Identity:
    """Who the console is connected as, and whether that is safe."""

    role: str
    database: str
    server_version: str
    superuser: bool
    can_write: bool

    @property
    def read_only(self) -> bool:
        return not (self.superuser or self.can_write)


def json_safe(value: Any) -> Any:
    """A cell as JSON can carry it without losing what it said.

    Decimals become strings rather than floats: `719279.97` is the answer
    being checked, and a float would show `719279.9699999999`. A float that
    is not finite is a string too, because JSON has no spelling for NaN and
    the response would otherwise fail to serialise at all.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, clock)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, timedelta):
        return str(value)
    return str(value)


def error_message(exc: BaseException) -> str:
    """The database's words, as the agent's gates record them."""
    return str(getattr(exc, "orig", exc)).strip() or type(exc).__name__


class Inspector:
    """The retail database, through the agent's gates.

    Holds the agent's `Database` for introspection -- so the schema shown is
    the schema the agent's prompt is built from -- and runs queries on the
    same pool.
    """

    def __init__(self, db: Database, agent: Settings, *, max_rows: int) -> None:
        self.db = db
        self.agent = agent
        self.max_rows = max_rows

    # --- the database ------------------------------------------------------

    def tables(self) -> list[str]:
        try:
            return self.db.table_names()
        except Exception as exc:  # noqa: BLE001 - the caller answers 503
            raise DatabaseUnavailable(error_message(exc)) from exc

    def catalog(self) -> list:
        try:
            return self.db.catalog()
        except Exception as exc:  # noqa: BLE001
            raise DatabaseUnavailable(error_message(exc)) from exc

    def prompt(self, table: str) -> str:
        """The block the agent's prompt carries for `table`, samples and all.

        Empty for a table that is not in the schema.
        """
        try:
            return self.db.schema_and_samples([table], self.agent.sample_rows)
        except Exception as exc:  # noqa: BLE001
            raise DatabaseUnavailable(error_message(exc)) from exc

    def identity(self) -> Identity:
        """The role, and whether it could write if the fence were not there.

        The fence -- READ ONLY, and the validator -- holds whatever the role
        is. This is how a `DATABASE_URL` pointed at the owner by mistake is
        noticed anyway, because the console runs SQL a person typed and
        should run it as the role that can only read.
        """
        try:
            with self.db.engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT current_user AS role,
                               current_database() AS database,
                               current_setting('server_version') AS server_version,
                               COALESCE((SELECT rolsuper FROM pg_roles
                                         WHERE rolname = current_user), false) AS superuser,
                               EXISTS (
                                   SELECT 1
                                   FROM pg_class c
                                   JOIN pg_namespace n ON n.oid = c.relnamespace
                                   WHERE n.nspname = :schema
                                     AND c.relkind IN ('r', 'p')
                                     AND has_table_privilege(
                                         c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE')
                               ) AS can_write
                        """
                    ),
                    {"schema": self.agent.db_schema},
                ).one()
        except Exception as exc:  # noqa: BLE001
            raise DatabaseUnavailable(error_message(exc)) from exc
        return Identity(
            role=row.role,
            database=row.database,
            server_version=row.server_version,
            superuser=bool(row.superuser),
            can_write=bool(row.can_write),
        )

    # --- a query -----------------------------------------------------------

    def run(self, sql: str, mode: Mode = "run") -> Outcome:
        cleaned = strip_sql(sql)
        unsafe = validate(cleaned)
        if unsafe:
            return Outcome(
                sql=cleaned,
                mode=mode,
                executed=False,
                verdict=_verdict([Finding(STATIC, issue.message) for issue in unsafe]),
            )

        found = [
            Finding(STATIC, issue.message)
            for issue in validate(cleaned, allowed_tables=self.tables())
        ]

        started = time.perf_counter()
        # `_staged` answers every failure of the query itself. What reaches
        # here is the connection's: it could not be opened, or it went away
        # between one statement and the next -- a database that is down,
        # not a query that is wrong.
        try:
            with self.db.engine.connect() as conn:
                transaction = conn.begin()
                try:
                    outcome = self._staged(conn, cleaned, mode, found)
                finally:
                    transaction.rollback()
        except Exception as exc:  # noqa: BLE001
            raise DatabaseUnavailable(error_message(exc)) from exc
        outcome.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return outcome

    def _staged(self, conn: Any, sql: str, mode: Mode, found: list[Finding]) -> Outcome:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql(
            f"SET LOCAL statement_timeout = {int(self.agent.statement_timeout_ms)}"
        )

        try:
            plan = conn.exec_driver_sql(f"EXPLAIN (FORMAT JSON) {sql}").scalar()
        except Exception as exc:  # noqa: BLE001 - the planner's refusal is the answer
            message = error_message(exc)
            return Outcome(
                sql=sql,
                mode=mode,
                executed=False,
                error=message,
                verdict=_verdict(found + [Finding(PLANNER, message)]),
            )
        cost = total_cost(plan)
        over = plan_cost_problem(cost, self.agent.max_plan_cost)
        if over:
            found = found + [Finding(PLANNER, over)]
        outcome = Outcome(
            sql=sql, mode=mode, executed=False, plan=plan, plan_cost=cost, verdict=_verdict(found)
        )
        if mode == "plan":
            return outcome

        if mode == "analyze":
            try:
                outcome.plan = conn.exec_driver_sql(
                    f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
                ).scalar()
            except Exception as exc:  # noqa: BLE001 - the executor's refusal is the answer
                return _failed(outcome, found, exc)
            outcome.executed = True
            return outcome

        try:
            described, fetched = self._fetch(conn, sql)
        except Exception as exc:  # noqa: BLE001 - the executor's refusal is the answer
            return _failed(outcome, found, exc)
        # Outside the executor's `try`: the query has run, and a failure to
        # name its types is the connection's, not the query's.
        names = self._type_names(conn, {oid for _, oid in described})
        outcome.columns = [(name, names.get(oid, str(oid))) for name, oid in described]
        outcome.truncated = len(fetched) > self.max_rows
        outcome.rows = [[json_safe(cell) for cell in row] for row in fetched[: self.max_rows]]
        outcome.executed = True
        if outcome.row_count > self.agent.max_rows:
            outcome.verdict.notes.append(
                f"the agent reads at most {self.agent.max_rows} rows (MAX_ROWS), so it would "
                f"have seen the first {self.agent.max_rows} of these and been told the result "
                "was truncated"
            )
        return outcome

    def _fetch(self, conn: Any, sql: str) -> tuple[list[tuple[str, int]], list[Any]]:
        """Run the statement and read at most one row past the cap.

        A server-side cursor, because a client-side one pulls the whole
        result into this process before the first row can be read -- which
        for the sales fact is over a million rows to show a thousand.
        """
        result = conn.exec_driver_sql(sql, execution_options={"stream_results": True})
        described = [(column.name, column.type_code) for column in result.cursor.description]
        fetched = result.fetchmany(self.max_rows + 1)
        result.close()
        return described, list(fetched)

    @staticmethod
    def _type_names(conn: Any, oids: set[int]) -> dict[int, str]:
        """Each column's type as the schema browser names it, `format_type`."""
        rows = conn.execute(
            text("SELECT oid::int AS oid, format_type(oid, NULL) AS name FROM pg_type WHERE oid = ANY(:oids)"),
            {"oids": sorted(oids)},
        ).all()
        return {int(row.oid): row.name for row in rows}


def _failed(outcome: Outcome, found: list[Finding], exc: BaseException) -> Outcome:
    """The executor refused it: the database's words, as the agent records them."""
    outcome.error = error_message(exc)
    outcome.verdict = _verdict(found + [Finding(RUNTIME, outcome.error)])
    return outcome


def _verdict(found: list[Finding]) -> Verdict:
    """The first objection decides the stage; every objection is listed."""
    return Verdict(accepted=not found, stage=found[0].stage if found else None, issues=list(found))
