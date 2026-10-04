"""The console's wire contract.

The same two rules as the API's models: nothing is `None` where a list would
do, and the agent's vocabulary is kept -- `static`, `planner` and `runtime`
are the names the pipeline gives its gates, and a person reading a verdict
here and a trace from the agent should not have to translate between them.

`Health`, `Check`, `Readiness` and the error envelope are the API's own,
re-exported rather than redefined: a client that already handles the agent's
errors handles these.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..api.models import ApiError, Check, Health, Readiness
from .settings import MAX_SQL_LENGTH

__all__ = [
    "AgentVerdict",
    "ApiError",
    "Check",
    "ColumnModel",
    "ConsoleLimits",
    "ConsoleMeta",
    "Health",
    "IssueModel",
    "PromptModel",
    "QueryRequest",
    "QueryResult",
    "Readiness",
    "ResultColumn",
    "SchemaModel",
    "TableModel",
]

Mode = Literal["run", "plan", "analyze"]
Stage = Literal["static", "planner", "runtime"]


class ConsoleLimits(BaseModel):
    """The agent's limits, which every query here runs under, and the console's own."""

    statement_timeout_ms: int
    max_plan_cost: float
    #: How many rows the agent reads (MAX_ROWS).
    agent_max_rows: int
    #: How many rows the console sends a browser (CONSOLE_MAX_ROWS).
    max_rows: int
    #: How many sample rows the agent's prompt carries per table.
    sample_rows: int
    max_sql_length: int


class ConsoleMeta(BaseModel):
    service: str = "nl2sql-console"
    version: str
    database: str
    role: str
    server_version: str
    #: False when the role could write were it not for the transaction: a
    #: DATABASE_URL pointed at the owner. Queries are still fenced.
    read_only: bool
    db_schema: str
    tables: int
    limits: ConsoleLimits
    authentication: Literal["bearer", "none", "session"]
    #: The role a query runs as: a signed-in person's own, or `role` above.
    runs_as: str
    tls: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class ColumnModel(BaseModel):
    name: str
    data_type: str
    not_null: bool
    comment: str | None


class TableModel(BaseModel):
    """A table as the agent's introspection reads it."""

    name: str
    comment: str | None
    approx_rows: int
    columns: list[ColumnModel]
    constraints: list[str]


class SchemaModel(BaseModel):
    db_schema: str
    tables: list[TableModel]


class PromptModel(BaseModel):
    """The block of the agent's prompt that describes one table."""

    table: str
    sample_rows: int
    text: str


class QueryRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"sql": "SELECT count(*) FROM dim_store", "mode": "run"}]
        },
    )

    sql: str = Field(min_length=1, max_length=MAX_SQL_LENGTH)
    #: `run` executes and returns rows; `plan` stops at EXPLAIN; `analyze`
    #: runs EXPLAIN ANALYZE, which executes the query to time it.
    mode: Mode = "run"


class IssueModel(BaseModel):
    stage: Stage
    message: str


class AgentVerdict(BaseModel):
    """What the agent would have done with this query."""

    accepted: bool
    #: The first gate that would have refused it.
    stage: Stage | None
    issues: list[IssueModel] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ResultColumn(BaseModel):
    name: str
    data_type: str


class QueryResult(BaseModel):
    sql: str
    mode: Mode
    #: False when a gate refused it, or when the mode was `plan`.
    executed: bool
    agent: AgentVerdict
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    #: More rows than `max_rows` came back; only the first `max_rows` are here.
    truncated: bool = False
    max_rows: int
    plan: Any = None
    plan_cost: float | None = None
    max_plan_cost: float
    error: str | None = None
    elapsed_ms: float = 0.0
