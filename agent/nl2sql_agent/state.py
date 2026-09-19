"""The shared state every v4 agent reads from and writes to.

This is section 3 of `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch4.md`, the
contract all three source architecture documents promised and none defined.
Every agent in the pipeline is a function of a subset of `AgentState` and
returns a partial update; LangGraph merges the updates. Because the contract
is explicit, each agent is unit-testable without the graph, which is the
property the architecture is for.

Three rules follow from the contract and are enforced by the agents, not here:

* **Retrieval is best-effort.** A retriever that cannot reach its store writes
  to `retrieval_errors` and the run continues without it. At the limit, with
  every store down, the pipeline degrades to schema-only.
* **`attempts` is the only retry counter.** Every failure source -- static
  validation, the planner, execution, the audit -- increments the same one,
  so there is no way to loop that does not spend budget.
* **`trace` is appended by every node**, which is what lets the benchmark
  attribute time per agent rather than per stage.

The payload types are dataclasses rather than TypedDicts so they carry their
own construction and rendering behaviour. They are converted to plain dicts at
the CLI/JSON boundary by `to_jsonable`.
"""

from __future__ import annotations

import operator
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Annotated, Any, Literal, TypedDict

# --- vocabularies -----------------------------------------------------------

Intent = Literal["lookup", "aggregate", "compare", "trend", "narrative"]
Verdict = Literal["proceed", "out_of_domain", "injection", "ambiguous"]

#: Where a failure came from. Every one of these routes to the Repair Agent
#: under the shared `attempts` budget -- W1 in the architecture document.
IssueSource = Literal["static", "planner", "runtime", "audit"]

STATIC = "static"
PLANNER = "planner"
RUNTIME = "runtime"
AUDIT = "audit"

CHART_KINDS = ("bar", "line", "grouped_bar", "scatter", "scalar", "table")


# --- payloads ---------------------------------------------------------------


@dataclass
class LiteralMatch:
    """A phrase in the question resolved to a real value in the database.

    `score` is the trigram (or difflib) similarity that matched it. Ties across
    columns are all kept: "Dairy & Eggs" is a department name and the generator
    should be told so rather than left to guess which column holds it.
    """

    phrase: str
    table: str
    column: str
    value: str
    score: float = 0.0

    def render(self) -> str:
        return f'"{self.phrase}" -> {self.table}.{self.column} = {self.value!r}'


@dataclass
class Shot:
    """One worked example, replayed as a human/assistant turn pair."""

    pair_id: str
    question: str
    reasoning_target: str
    sql_code: str


@dataclass
class Issue:
    """Why a generated query was rejected, and what to tell the generator.

    `message` is what happened, verbatim from whichever gate produced it.
    `hint` is the Repair Agent's actionable rewrite of it; it stays empty until
    the Repair Agent has classified the issue.
    """

    source: IssueSource
    message: str
    hint: str = ""

    def render(self) -> str:
        return f"[{self.source}] {self.message}" + (f"\n  fix: {self.hint}" if self.hint else "")


@dataclass
class Attempt:
    """One spent generation: the SQL it produced and why that was rejected."""

    sql: str
    issues: list[Issue] = field(default_factory=list)


@dataclass
class QueryResult:
    """Rows from the Safe Executor.

    `truncated` says the row cap was hit, which the narrator must be told: a
    claim about "the total" is false when it is the total of a sample.
    """

    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def cell(self, row: int, column: str) -> Any:
        """One cell by row index and column name, for claim verification."""
        if column not in self.columns:
            raise KeyError(f"no column {column!r} in the result set")
        if not 0 <= row < len(self.rows):
            raise IndexError(f"no row {row} in a {len(self.rows)}-row result")
        return self.rows[row][self.columns.index(column)]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row)) for row in self.rows]


@dataclass
class ChartSpec:
    """What to draw, chosen from the result's shape. Nothing renders here."""

    kind: str
    x: str | None = None
    y: list[str] = field(default_factory=list)
    series: str | None = None


@dataclass
class Claim:
    """One assertion in the narrative, pointing at the cells it came from.

    `cells` are `(row index, column name)` pairs. `formula` is an expression
    over `cells`, as in `cells[0] - cells[1]`, for a number the narrator
    derived rather than read. A claim whose value the Audit Checker cannot
    reproduce from its own cells is dropped.
    """

    text: str
    value: float | None = None
    cells: list[tuple[int, str]] = field(default_factory=list)
    formula: str | None = None


@dataclass
class AuditReport:
    passed: bool = True
    unsupported_claims: list[str] = field(default_factory=list)
    #: One line per dropped claim saying why it failed. The architecture sends
    #: unsupported claims back to the narrator once, and a retry that does not
    #: say what was wrong is a retry that reproduces it.
    drop_reasons: list[str] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)
    #: Set when the audit concludes the SQL is wrong rather than the prose.
    #: It routes to the Repair Agent and spends the shared budget (W3).
    semantic_issue: str | None = None


@dataclass
class TraceEntry:
    """One node's cost, for per-agent benchmark attribution (section 11)."""

    node: str
    ms: float = 0.0
    model_calls: int = 0
    detail: str = ""


# --- reducers ---------------------------------------------------------------
# Stage 1's four retrievers are branches of one LangGraph superstep, so they
# return their updates concurrently. A key two branches both write needs a
# reducer or the graph refuses the update; a key only one branch writes does
# not. Only these two are shared: every retriever may record a failure, and
# every node in the pipeline appends to the trace.


def merge_errors(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """Union of two retrievers' failure notes; the later one wins a tie."""
    return {**(left or {}), **(right or {})}


# --- the state --------------------------------------------------------------


class AgentState(TypedDict, total=False):
    """Section 3 of the architecture document, field for field.

    `total=False` because every node returns only the keys it owns; a node
    that returned the whole state would overwrite the parallel branches it ran
    beside.
    """

    # --- input -----------------------------------------------------------
    question: str
    principal: str | None  # end-user identity, for SET ROLE / RLS (section 8)

    # --- stage 1: intake --------------------------------------------------
    intent: Intent
    verdict: Verdict
    clarification: str | None  # what to ask back when verdict == "ambiguous"

    selected_tables: list[str]  # Schema Retriever: <= 10, FK-closed
    schema: str  # DDL + comments + sample rows for selected_tables
    literal_map: list[LiteralMatch]
    knowledge: str
    knowledge_tables: list[str]
    knowledge_chunks: list[dict[str, Any]]
    example_shots: list[Shot]
    example_tables: list[str]
    example_pairs: list[dict[str, Any]]
    schema_tables: list[str]  # what the Schema Retriever alone proposed
    retrieval_errors: Annotated[dict[str, str], merge_errors]

    # --- stage 2: synthesis -----------------------------------------------
    sql: str
    attempts: int  # generations so far; the one retry budget

    # --- stage 3: validation and repair ------------------------------------
    issues: list[Issue]
    attempt_history: list[Attempt]
    plan_cost: float | None
    result: QueryResult | None

    # --- stage 4: presentation ---------------------------------------------
    chart: ChartSpec | None
    claims: list[Claim]
    narrative: str
    audit: AuditReport
    #: Narrator rewrites spent. Deliberately not `attempts`: a claim the
    #: checker could not reproduce says nothing about whether the SQL was
    #: right, so it must not spend the budget that decides whether to rewrite
    #: the query.
    narration_retries: int

    # --- output and observability -------------------------------------------
    answer: str
    error: str | None
    trace: Annotated[list[TraceEntry], operator.add]


def new_state(question: str, *, principal: str | None = None) -> AgentState:
    """The initial state for a run: everything a node might read, empty.

    Seeding the collections here rather than relying on `state.get(...)`
    defaults keeps every node's reads total, which is what makes them
    individually testable.
    """
    return {
        "question": question,
        "principal": principal,
        "verdict": "proceed",
        "intent": "aggregate",
        "clarification": None,
        "selected_tables": [],
        "schema": "",
        "literal_map": [],
        "knowledge": "",
        "knowledge_tables": [],
        "knowledge_chunks": [],
        "example_shots": [],
        "example_tables": [],
        "example_pairs": [],
        "schema_tables": [],
        "retrieval_errors": {},
        "sql": "",
        "attempts": 0,
        "issues": [],
        "attempt_history": [],
        "plan_cost": None,
        "result": None,
        "chart": None,
        "claims": [],
        "narrative": "",
        "audit": AuditReport(),
        "narration_retries": 0,
        "answer": "",
        "error": None,
        "trace": [],
    }


def to_jsonable(value: Any) -> Any:
    """Plain JSON-safe data, for `--json` output and trace files.

    Dataclasses become dicts, tuples become lists, and anything the JSON
    encoder cannot take (a `Decimal` or a `date` out of the database) becomes
    its string form rather than raising at the very end of a successful run.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
