"""Postgres introspection and read-only query execution.

Introspection reads COMMENT ON metadata straight from the catalog, so adding
descriptions to the schema makes the agent smarter without touching this code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

_SELECT_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


class UnsafeQueryError(ValueError):
    """Raised for SQL that is not a single read-only statement."""


@dataclass
class Column:
    name: str
    data_type: str
    not_null: bool
    comment: str | None


@dataclass
class Table:
    name: str
    comment: str | None
    approx_rows: int
    columns: list[Column] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool

    def to_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row)) for row in self.rows]


def strip_sql(sql: str) -> str:
    """Remove markdown fences and trailing semicolons from model output."""
    cleaned = sql.strip()
    fence = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1)
    return cleaned.strip().rstrip(";").strip()


def ensure_read_only(sql: str) -> str:
    """Check that sql is a single SELECT/WITH statement, and return it cleaned.

    This is the first of two layers; execution additionally runs inside a
    READ ONLY transaction, which is what actually stops a data-modifying CTE.
    """
    cleaned = strip_sql(sql)
    if not cleaned:
        raise UnsafeQueryError("Query is empty.")
    if not _SELECT_START.match(cleaned):
        raise UnsafeQueryError("Only SELECT/WITH queries are allowed.")
    if ";" in cleaned:
        raise UnsafeQueryError("Only a single statement is allowed.")
    return cleaned


class Database:
    def __init__(
        self,
        url: str,
        *,
        db_schema: str = "public",
        statement_timeout_ms: int = 30000,
        max_rows: int = 50,
    ) -> None:
        self._engine: Engine = create_engine(url, pool_pre_ping=True)
        self._schema = db_schema
        self._statement_timeout_ms = statement_timeout_ms
        self._max_rows = max_rows

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    def table_names(self) -> list[str]:
        return [t.name for t in self._load_tables()]

    def _load_tables(self, names: list[str] | None = None) -> list[Table]:
        params: dict[str, Any] = {"schema": self._schema}
        name_filter = ""
        if names:
            name_filter = " AND c.relname = ANY(:names)"
            params["names"] = names

        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT c.relname AS name,
                           obj_description(c.oid) AS comment,
                           GREATEST(c.reltuples, 0)::bigint AS approx_rows
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema
                      AND c.relkind IN ('r', 'p', 'v', 'm')
                      {name_filter}
                    ORDER BY c.relname
                    """
                ),
                params,
            ).all()
            tables = {
                r.name: Table(name=r.name, comment=r.comment, approx_rows=int(r.approx_rows))
                for r in rows
            }
            if not tables:
                return []

            params["names"] = list(tables)
            for col in conn.execute(
                text(
                    """
                    SELECT c.relname AS table_name,
                           a.attname AS name,
                           format_type(a.atttypid, a.atttypmod) AS data_type,
                           a.attnotnull AS not_null,
                           col_description(c.oid, a.attnum) AS comment
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_attribute a ON a.attrelid = c.oid
                    WHERE n.nspname = :schema
                      AND c.relname = ANY(:names)
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    ORDER BY c.relname, a.attnum
                    """
                ),
                params,
            ).all():
                tables[col.table_name].columns.append(
                    Column(col.name, col.data_type, col.not_null, col.comment)
                )

            for con in conn.execute(
                text(
                    """
                    SELECT c.relname AS table_name,
                           pg_get_constraintdef(con.oid) AS definition
                    FROM pg_constraint con
                    JOIN pg_class c ON c.oid = con.conrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema
                      AND c.relname = ANY(:names)
                      AND con.contype IN ('p', 'f', 'u')
                    ORDER BY c.relname, con.contype
                    """
                ),
                params,
            ).all():
                tables[con.table_name].constraints.append(con.definition)

        return list(tables.values())

    def catalog(self) -> list[Table]:
        """Every table with its columns and key constraints.

        The label map (arch5 section 4.1) is read from this: which columns
        identify a row of a dimension, and which one names it.
        """
        return self._load_tables()

    def latest_complete_fiscal_year(self) -> tuple[int, date, date] | None:
        """The last fiscal year the sales data holds in full, with its bounds.

        The greatest `fiscal_year` whose last calendar date is on or before
        the last date anything was sold. That is the default period for a
        question that names none (arch5 section 4.1): a calendar that runs
        past the data would otherwise offer a year that has barely begun.
        `MAX(sales_date_key)` is the leading column of the sales fact's
        primary key, so this reads one index entry, not the fact.
        """
        with self._engine.connect() as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                row = conn.execute(
                    text(
                        f"""
                        SELECT d.fiscal_year,
                               MIN(d.calendar_date) AS first_date,
                               MAX(d.calendar_date) AS last_date
                        FROM {self._quote("dim_date")} d
                        GROUP BY d.fiscal_year
                        HAVING MAX(d.date_key) <= (
                            SELECT MAX(s.sales_date_key)
                            FROM {self._quote("fact_pos_retail_sales")} s
                        )
                        ORDER BY d.fiscal_year DESC
                        LIMIT 1
                        """
                    )
                ).first()
        return (int(row.fiscal_year), row.first_date, row.last_date) if row is not None else None

    def describe_all_tables(self) -> str:
        """Compact catalog of every table: description, size, column names."""
        lines = []
        for table in self._load_tables():
            description = table.comment or "(no description available)"
            lines.append(f"{table.name} -- {description} [~{table.approx_rows:,} rows]")
            lines.append("    columns: " + ", ".join(c.name for c in table.columns))
        return "\n".join(lines)

    def schema_and_samples(self, names: list[str], sample_rows: int) -> str:
        """Full column detail, keys, and a few example rows per table."""
        blocks = []
        for table in self._load_tables(names):
            block = [f"=== {table.name} ==="]
            if table.comment:
                block.append(f"description: {table.comment}")
            block.append("columns:")
            for col in table.columns:
                null = "NOT NULL" if col.not_null else "NULL"
                comment = f"  -- {col.comment}" if col.comment else ""
                block.append(f"  {col.name} ({col.data_type}, {null}){comment}")
            if table.constraints:
                block.append("keys:")
                block.extend(f"  {c}" for c in table.constraints)
            block.append(f"sample rows (up to {sample_rows}):")
            block.append(self._sample_rows(table.name, sample_rows))
            blocks.append("\n".join(block))
        return "\n\n".join(blocks)

    def _sample_rows(self, table_name: str, limit: int) -> str:
        quoted = self._quote(table_name)
        result = self.run_select(f"SELECT * FROM {quoted} LIMIT {int(limit)}")
        if not result.rows:
            return "  (table is empty)"
        header = " | ".join(result.columns)
        body = [
            " | ".join("NULL" if v is None else str(v)[:40] for v in row) for row in result.rows
        ]
        return "\n".join(f"  {line}" for line in [header, *body])

    def _quote(self, table_name: str) -> str:
        return f'"{self._schema}"."{table_name}"'

    def explain(self, sql: str) -> str | None:
        """Plan the query without running it. Returns an error message or None."""
        _, error = self.explain_plan(sql)
        return error

    def explain_plan(self, sql: str) -> tuple[float | None, str | None]:
        """Plan without executing; return the estimated total cost and any error.

        This is the Planner Gate of the v4 architecture (section 6.2). It is
        plain `EXPLAIN`, never `EXPLAIN ANALYZE`: ANALYZE *executes* the
        statement to report real timings, so a gate built on it would run
        every candidate query before the executor ran it again, and its
        "timeout check" would be the full run.

        Plain EXPLAIN plans in milliseconds and is worth far more than a
        syntax check, because it reports every semantic error the parser
        cannot: unknown column, type mismatch, an aggregate outside GROUP BY,
        an ambiguous reference. Those are the common real mistakes, and the
        message comes back verbatim for the Repair Agent to classify.

        The cost is `Plan."Total Cost"` from the JSON plan, which is what the
        cost ceiling compares against. Returns `(None, message)` on failure
        and `(cost, None)` on success.
        """
        cleaned = ensure_read_only(sql)
        try:
            with self._engine.connect() as conn:
                with conn.begin():
                    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                    row = conn.exec_driver_sql(
                        f"EXPLAIN (FORMAT JSON) {cleaned}"
                    ).scalar()
        except Exception as exc:  # surfaced to the Repair Agent as feedback
            return None, str(getattr(exc, "orig", exc)).strip()
        return _total_cost(row), None

    def run_select(self, sql: str, *, principal: str | None = None) -> QueryResult:
        """Execute inside a READ ONLY transaction with a timeout and a row cap.

        `principal` is the end-user identity: when one is supplied the
        executor does `SET LOCAL ROLE`, so the database's row-level security
        policies apply to that user rather than to the agent's own role. The
        test system has no RLS policies, so this is plumbing for production
        rather than a behaviour change here (section 8).
        """
        cleaned = ensure_read_only(sql)
        with self._engine.connect() as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(self._statement_timeout_ms)}")
                if principal:
                    # Parameters are not allowed here, so the identifier is
                    # quoted rather than interpolated raw.
                    conn.exec_driver_sql(f'SET LOCAL ROLE "{_quote_identifier(principal)}"')
                cursor = conn.exec_driver_sql(cleaned)
                columns = list(cursor.keys())
                rows = cursor.fetchmany(self._max_rows + 1)
        truncated = len(rows) > self._max_rows
        return QueryResult(columns=columns, rows=rows[: self._max_rows], truncated=truncated)


def _quote_identifier(name: str) -> str:
    """Escape an identifier for use inside double quotes."""
    return name.replace('"', '""')


def _total_cost(plan: Any) -> float | None:
    """`Plan."Total Cost"` out of an EXPLAIN (FORMAT JSON) payload.

    psycopg may hand back the JSON already decoded or as text depending on the
    column type it infers, so both are accepted. A plan that does not carry a
    cost is not an error; the gate simply has nothing to compare.
    """
    if isinstance(plan, str):
        import json

        try:
            plan = json.loads(plan)
        except ValueError:
            return None
    if isinstance(plan, list) and plan:
        plan = plan[0]
    if isinstance(plan, dict):
        node = plan.get("Plan")
        if isinstance(node, dict) and "Total Cost" in node:
            try:
                return float(node["Total Cost"])
            except (TypeError, ValueError):
                return None
    return None
