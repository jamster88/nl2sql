"""Fakes for the SQL console: a database that answers from a script.

The console's `Inspector` talks to the agent's `Database` for introspection
and to its SQLAlchemy engine for queries. `ScriptedDatabase` stands in for
both, and records every statement it is sent, so a test can assert on the
fence -- READ ONLY, the statement timeout, the rollback -- as well as on what
came back. Anything it is not told to fail, it answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from nl2sql_agent.config import Settings
from nl2sql_agent.console.query import Inspector
from nl2sql_agent.database import Column, Table

#: A plan as Postgres 18 writes it for a small scan.
PLAN = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "dim_store", "Total Cost": 1.4}}]
ANALYZED = [
    {
        "Plan": {"Node Type": "Seq Scan", "Total Cost": 1.4, "Actual Total Time": 0.05},
        "Planning Time": 0.02,
        "Execution Time": 0.08,
    }
]


class DatabaseError(Exception):
    """What the driver raises, wrapped the way SQLAlchemy wraps it."""

    def __init__(self, message: str) -> None:
        super().__init__(f"(psycopg.errors.X) {message}")
        self.orig = message


@dataclass
class ScriptedDatabase:
    """The agent's `Database`, answering from attributes a test sets."""

    tables: list[Table] = field(
        default_factory=lambda: [
            Table(
                name="dim_store",
                comment="One row per store.",
                approx_rows=40,
                columns=[
                    Column("store_key", "integer", True, "Surrogate key."),
                    Column("store_name", "character varying(150)", True, None),
                ],
                constraints=["PRIMARY KEY (store_key)"],
            )
        ]
    )
    plan: Any = field(default_factory=lambda: PLAN)
    analyzed: Any = field(default_factory=lambda: ANALYZED)
    #: (name, type oid) per result column.
    description: list[tuple[str, int]] = field(
        default_factory=lambda: [("store_key", 23), ("store_name", 1043)]
    )
    rows: list[tuple] = field(default_factory=lambda: [(1, "Ashland"), (2, "Salem")])
    type_names: dict[int, str] = field(
        default_factory=lambda: {23: "integer", 1043: "character varying"}
    )
    identity: dict[str, Any] = field(
        default_factory=lambda: {
            "role": "nl2sql_reader",
            "database": "nl2sql_retail",
            "server_version": "18.6",
            "superuser": False,
            "can_write": False,
        }
    )

    # --- failures a test can switch on --------------------------------------
    plan_error: str | None = None
    run_error: str | None = None
    analyze_error: str | None = None
    connect_error: str | None = None
    introspection_error: str | None = None
    #: A statement prefix that raises as though the connection had dropped.
    drop_on: str | None = None

    # --- what happened -------------------------------------------------------
    statements: list[str] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    rolled_back: int = 0
    connections: int = 0
    fetched: list[int] = field(default_factory=list)

    # --- the Database surface ------------------------------------------------

    def _introspect(self) -> None:
        if self.introspection_error:
            raise DatabaseError(self.introspection_error)

    def table_names(self) -> list[str]:
        self._introspect()
        return [table.name for table in self.tables]

    def catalog(self) -> list[Table]:
        self._introspect()
        return list(self.tables)

    def schema_and_samples(self, names: list[str], sample_rows: int) -> str:
        self._introspect()
        known = [name for name in names if name in {t.name for t in self.tables}]
        return "".join(f"=== {name} ===\nsample rows (up to {sample_rows}):" for name in known)

    @property
    def engine(self) -> "ScriptedDatabase":
        return self

    def connect(self) -> "_Connection":
        if self.connect_error:
            raise DatabaseError(self.connect_error)
        self.connections += 1
        return _Connection(self)


class _Result:
    def __init__(self, *, scalar: Any = None, description=None, rows=None, db=None) -> None:
        self._scalar = scalar
        self._rows = list(rows or [])
        self._db = db
        self.cursor = SimpleNamespace(
            description=[SimpleNamespace(name=n, type_code=t) for n, t in (description or [])]
        )
        self.closed = False

    def scalar(self) -> Any:
        return self._scalar

    def fetchmany(self, size: int) -> list[tuple]:
        self._db.fetched.append(size)
        return self._rows[:size]

    def close(self) -> None:
        self.closed = True

    def one(self) -> Any:
        return SimpleNamespace(**self._scalar)

    def all(self) -> list[Any]:
        return [SimpleNamespace(**row) for row in self._rows]


class _Transaction:
    def __init__(self, db: ScriptedDatabase) -> None:
        self._db = db

    def rollback(self) -> None:
        self._db.rolled_back += 1


class _Connection:
    def __init__(self, db: ScriptedDatabase) -> None:
        self._db = db

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction(self._db)

    def exec_driver_sql(self, sql: str, parameters=None, execution_options=None) -> _Result:
        db = self._db
        db.statements.append(sql)
        db.options.append(dict(execution_options or {}))
        if db.drop_on and sql.startswith(db.drop_on):
            raise DatabaseError("server closed the connection unexpectedly")
        if sql.startswith("SET "):
            return _Result(db=db)
        if sql.startswith("EXPLAIN (FORMAT JSON)"):
            if db.plan_error:
                raise DatabaseError(db.plan_error)
            return _Result(scalar=db.plan, db=db)
        if sql.startswith("EXPLAIN (ANALYZE"):
            if db.analyze_error:
                raise DatabaseError(db.analyze_error)
            return _Result(scalar=db.analyzed, db=db)
        if db.run_error:
            raise DatabaseError(db.run_error)
        return _Result(description=db.description, rows=db.rows, db=db)

    def execute(self, clause: Any, params: dict | None = None) -> _Result:
        db = self._db
        sql = str(clause)
        db.statements.append(sql)
        if db.drop_on and sql.lstrip().startswith(db.drop_on):
            raise DatabaseError("server closed the connection unexpectedly")
        if "pg_type" in sql:
            wanted = set((params or {})["oids"])
            return _Result(
                rows=[{"oid": oid, "name": name} for oid, name in db.type_names.items() if oid in wanted],
                db=db,
            )
        return _Result(scalar=db.identity, db=db)


@pytest.fixture
def db() -> ScriptedDatabase:
    return ScriptedDatabase()


@pytest.fixture
def agent() -> Settings:
    """The agent's settings, with the limits the console runs under."""
    return Settings(statement_timeout_ms=30000, max_rows=50, max_plan_cost=1_000_000.0, sample_rows=3)


@pytest.fixture
def inspector(db: ScriptedDatabase, agent: Settings) -> Inspector:
    return Inspector(db, agent, max_rows=1000)
