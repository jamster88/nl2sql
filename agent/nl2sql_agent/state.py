"""The shared state every agent reads from and writes to.

This is section 3 of `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5.md`, the
contract all three source architecture documents promised and none defined.
arch5 adds three fields to arch4's: the answer contract the Supervisor's
reading of the question is turned into, the assumptions the pipeline made on
the user's behalf, and the Completeness Reviewer's report.
Every agent in the pipeline is a function of a subset of `AgentState` and
returns a partial update; LangGraph merges the updates. Because the contract
is explicit, each agent is unit-testable without the graph, which is the
property the architecture is for.

Three rules follow from the contract and are enforced by the agents, not here:

* **Retrieval is best-effort.** A retriever that cannot reach its store writes
  to `retrieval_errors` and the run continues without it. At the limit, with
  every store down, the pipeline degrades to schema-only. A node that is not
  a retriever and fails the same survivable way -- the Supervisor's or the
  Narrator's model call -- writes to `node_errors` instead, so a reader of
  either map knows which kind of thing went missing.
* **`attempts` is the only retry counter.** Every failure source -- static
  validation, the planner, execution, the Completeness Reviewer, the audit --
  increments the same one, so there is no way to loop that does not spend
  budget.
* **`trace` is appended by every node**, which is what lets the benchmark
  attribute time per agent rather than per stage.
* **Every field has a lifetime** (`LIFETIMES`). A *run* field describes the
  whole run; an *attempt* field describes one generated query and what
  became of it, and the Repair Agent empties it (`attempt_reset`) when it
  sends the run round again, so no node of attempt N+1 can read what
  attempt N left behind; a *handoff* field is carried across that edge on
  purpose -- the failed SQL and the hints about it are what the next
  generation is written from.

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
#: `completeness` is the one a correct query can raise: it ran, and it is not
#: yet the answer (arch5 section 6.6).
IssueSource = Literal["static", "planner", "runtime", "completeness", "audit"]

STATIC = "static"
PLANNER = "planner"
RUNTIME = "runtime"
COMPLETENESS = "completeness"
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
class EntityRef:
    """One thing the answer's rows are about, resolved against the label map.

    `word` is the Supervisor's noun ("sku", "store"). `key` and `label` are
    the columns that identify and name it (`sku_id` and `product_name`);
    either can be None -- a department has a name and no key, and a
    dimension with no name column has a key and no label.
    """

    word: str
    key: str | None = None
    label: str | None = None
    table: str | None = None


@dataclass
class AnswerContract:
    """What a complete answer carries, before any SQL exists (arch5 section 4.1).

    Built deterministically from the Supervisor's three fields, the label map
    and the fiscal calendar, and read twice: by the SQL Generator before the
    query is written and by the Completeness Reviewer after it has run. It
    names columns, never SQL.

    `period` is the span the question named, in its own words, or the
    default's label when it named none; `period_default` says which, and
    `fiscal_year` is the default year. `limit` is the row count the question
    asked for ("top 10"), when it asked for one.
    """

    entities: list[EntityRef] = field(default_factory=list)
    measure: str | None = None
    period: str | None = None
    period_default: bool = False
    fiscal_year: int | None = None
    fiscal_year_start: str | None = None  # ISO date
    fiscal_year_end: str | None = None  # ISO date
    ranked: bool = False
    limit: int | None = None


@dataclass
class MissingColumn:
    """One thing a result lacks, and the sentence that says how to add it.

    `column` names what is missing -- a column, an expression the rows are
    ordered by, a period filter, a row count -- and `rule` which check found
    it: R1-R4 for the rules, `reflection` for the model's judgement.
    """

    column: str
    why: str
    rule: str = ""
    #: The table the missing column lives in, when it is known: the graph
    #: widens the query's scope to it rather than send the generator a hint
    #: the table allowlist would then reject.
    table: str | None = None

    @property
    def key(self) -> str:
        """What the same-result guard compares: the same rule, the same gap."""
        return f"{self.rule}:{self.column.lower()}"


@dataclass
class CompletenessReport:
    """The Completeness Reviewer's verdict on the latest result (section 6.6).

    `missing` is what the current result lacks. `sent_back` remembers every
    gap already returned to the generator once, which is the same-result
    guard: a gap that comes back a second time is accepted into
    `accepted_gaps` and told to the reader rather than sent back again.
    `requested` holds the columns the one reflection asked for, so a later
    result is checked for them without a second model call.
    """

    passed: bool = True
    missing: list[MissingColumn] = field(default_factory=list)
    reflected: bool = False
    requested: list[MissingColumn] = field(default_factory=list)
    sent_back: list[str] = field(default_factory=list)
    accepted_gaps: list[MissingColumn] = field(default_factory=list)
    note: str = ""


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
    #: Assumptions no surviving claim states (arch5, section 7.3 rule 5). Sent
    #: back to the narrator under the same once-only rule as a dropped claim;
    #: after that the renderer states them itself.
    missing_assumptions: list[str] = field(default_factory=list)
    #: Set when the audit concludes the SQL is wrong rather than the prose.
    #: It routes to the Repair Agent and spends the shared budget (W3).
    semantic_issue: str | None = None


@dataclass
class Complexity:
    """How hard the generator's task looks, scored by the Context Aggregator
    from what retrieval returned (arch5.2 section 15.2). `signals` are the
    reasons, each with the points it added."""

    score: int = 0
    signals: list[str] = field(default_factory=list)
    rung: str = "light"


@dataclass
class TraceEntry:
    """One node's cost, for per-agent benchmark attribution (section 11).

    Since arch5.2 an entry that called a model also says which one answered
    and why it was asked: `rung` is what the call was routed at, `route` the
    router's reasoning, and `hops` every routed model that failed first --
    not on the host, unreachable, or empty -- before `model` answered.
    """

    node: str
    ms: float = 0.0
    model_calls: int = 0
    detail: str = ""
    model: str = ""
    rung: str = ""
    route: str = ""
    hops: list[str] = field(default_factory=list)


# --- reducers ---------------------------------------------------------------
# Stage 1's five retrievers are branches of one LangGraph superstep, so they
# return their updates concurrently. A key two branches both write needs a
# reducer or the graph refuses the update; a key only one branch writes does
# not. Only these are shared: every retriever may record a failure, and every
# node in the pipeline appends to the trace. `node_errors` takes the same
# reducer so a later node's failure adds to an earlier one's rather than
# replacing it.


def merge_errors(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """Union of two nodes' failure notes; the later one wins a tie."""
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
    answer_contract: AnswerContract  # what a complete answer carries (arch5)
    #: Defaults the pipeline chose for the user, such as the fiscal year; the
    #: narrator must state every one (arch5 sections 6.6 and 7.2).
    assumptions: list[str]

    selected_tables: list[str]  # Schema Retriever: <= 10, FK-closed
    schema: str  # DDL + comments + sample rows for selected_tables
    literal_map: list[LiteralMatch]
    knowledge: str
    knowledge_tables: list[str]
    knowledge_chunks: list[dict[str, Any]]
    example_shots: list[Shot]
    example_tables: list[str]
    example_pairs: list[dict[str, Any]]
    #: The Snippet Retriever's finds (v5.6): verified joins, filters, measures
    #: and dimensions, beside the summaries the trace and --json show.
    snippets: list[Any]
    snippet_hits: list[dict[str, Any]]
    #: The ones whose tables are all in scope, as the generator reads them --
    #: written by the Context Aggregator, which is where scope is decided.
    snippet_context: str
    schema_tables: list[str]  # what the Schema Retriever alone proposed
    retrieval_errors: Annotated[dict[str, str], merge_errors]
    #: A node other than a retriever that failed and was survived -- the
    #: Supervisor's screening, the Narrator's claims -- keyed by agent.
    node_errors: Annotated[dict[str, str], merge_errors]
    #: The generator's task, scored by the Context Aggregator (arch5.2).
    complexity: Complexity

    # --- stage 2: synthesis -----------------------------------------------
    sql: str
    attempts: int  # generations so far; the one retry budget
    #: The rung the next generation is routed at: the score's, raised one
    #: per repair (arch5.2 section 15.2). Nothing lowers it within a run.
    generation_rung: str
    #: Same-rung retries spent: a completeness rules gap holds the rung once.
    rung_holds: int

    # --- stage 3: validation and repair ------------------------------------
    issues: list[Issue]
    attempt_history: list[Attempt]
    plan_cost: float | None
    result: QueryResult | None
    completeness: CompletenessReport  # the Completeness Reviewer's report (arch5)

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
    #: MLflow's id for this run's trace (`tracing.py`), empty when the run was
    #: not traced. Written after the graph ends, by `Nl2SqlAgent.run`: it is
    #: the trace's name for the run, not something any agent decides.
    trace_id: str


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
        "answer_contract": AnswerContract(),
        "assumptions": [],
        "selected_tables": [],
        "schema": "",
        "literal_map": [],
        "knowledge": "",
        "knowledge_tables": [],
        "knowledge_chunks": [],
        "example_shots": [],
        "example_tables": [],
        "example_pairs": [],
        "snippets": [],
        "snippet_hits": [],
        "snippet_context": "",
        "schema_tables": [],
        "retrieval_errors": {},
        "node_errors": {},
        "complexity": Complexity(),
        "sql": "",
        "attempts": 0,
        "generation_rung": "light",
        "rung_holds": 0,
        "issues": [],
        "attempt_history": [],
        "plan_cost": None,
        "result": None,
        "completeness": CompletenessReport(),
        "chart": None,
        "claims": [],
        "narrative": "",
        "audit": AuditReport(),
        "narration_retries": 0,
        "answer": "",
        "error": None,
        "trace": [],
        "trace_id": "",
    }


# --- lifetimes ---------------------------------------------------------------
# One retry loop means one question for every field: when the Repair Agent
# sends the run back to the generator, which of these still describe
# something true? Answered here, field by field, rather than by each node
# remembering to overwrite what it read -- arch4 left it to the nodes, and
# the narrator then read attempt 1's audit while narrating attempt 2.

RUN = "run"
ATTEMPT = "attempt"
HANDOFF = "handoff"

#: Every field of `AgentState` and how long it is true for.
#:
#: * `run` -- the question, what retrieval found, the budget, the record of
#:   every attempt, and the outputs. Written once or accumulated.
#:   `completeness` is one of these on purpose: the reviewer reads its
#:   earlier report so that it reflects once per run and does not send the
#:   same gap back twice.
#: * `attempt` -- one generated query and what became of it: its plan, its
#:   rows, the chart, the narration, the audit and the narrator's one
#:   rewrite. Emptied on the repair edge by `attempt_reset`.
#: * `handoff` -- the failed SQL and the issues about it, which the Repair
#:   Agent passes to the next generation and the generation then replaces.
LIFETIMES: dict[str, str] = {
    "question": RUN,
    "principal": RUN,
    "intent": RUN,
    "verdict": RUN,
    "clarification": RUN,
    "answer_contract": RUN,
    "assumptions": ATTEMPT,
    "selected_tables": RUN,
    "schema": RUN,
    "literal_map": RUN,
    "knowledge": RUN,
    "knowledge_tables": RUN,
    "knowledge_chunks": RUN,
    "example_shots": RUN,
    "example_tables": RUN,
    "example_pairs": RUN,
    "snippets": RUN,
    "snippet_hits": RUN,
    "snippet_context": RUN,
    "schema_tables": RUN,
    "retrieval_errors": RUN,
    "node_errors": RUN,
    "complexity": RUN,
    "sql": HANDOFF,
    "attempts": RUN,
    "generation_rung": RUN,
    "rung_holds": RUN,
    "issues": HANDOFF,
    "attempt_history": RUN,
    "plan_cost": ATTEMPT,
    "result": ATTEMPT,
    "completeness": RUN,
    "chart": ATTEMPT,
    "claims": ATTEMPT,
    "narrative": RUN,
    "audit": ATTEMPT,
    "narration_retries": ATTEMPT,
    "answer": RUN,
    "error": RUN,
    "trace": RUN,
    "trace_id": RUN,
}


def attempt_reset() -> dict[str, Any]:
    """Every `attempt` field, empty: what the Repair Agent's update carries.

    Fresh objects on every call, so no two runs share an empty report.
    """
    return {
        "assumptions": [],
        "plan_cost": None,
        "result": None,
        "chart": None,
        "claims": [],
        "audit": AuditReport(),
        "narration_retries": 0,
    }


def state_fields() -> tuple[str, ...]:
    """The names `AgentState` declares, in declaration order."""
    return tuple(AgentState.__annotations__)


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
