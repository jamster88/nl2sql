"""The wire contract: what a client sends and what it gets back.

These models exist so the API has a published shape rather than whatever
`to_jsonable` happened to produce that day. They are what FastAPI turns into
the OpenAPI document at `/openapi.json`, which is the whole framework-
agnostic story -- a TypeScript GUI generates a client from it, a Django or
Spring service generates one too, and neither imports a line of this package.

Three rules held throughout:

* **Nothing is ever `None` where a list would do.** A GUI rendering
  `result.rows.map(...)` should not have to null-check first.
* **The pipeline's vocabulary is preserved.** `verdict`, `intent`, `claims`,
  `audit` and `trace` are the architecture's own words (arch4), and renaming
  them at the boundary would mean two names for everything.
* **Nothing is dropped on the way out.** Every model is `Wire`, which
  forbids a field it does not declare -- in a request, a misspelt field is a
  422 rather than ignored; in a response, `translate.py` building one from
  the pipeline's state fails a test the day the state gains a field this
  contract does not carry. The lenient default is how the trace's model
  routing fields went missing over REST for four releases.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator
from nl2sql_common.envelope import Wire

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

#: A person's verdict on an answer. `yes` is "correct" and `no` is "wrong",
#: as they always were; `incomplete` is "correct but incomplete" -- the SQL
#: was right and the answer still lacked something a reader needed, such as
#: a name beside an id. The three are kept apart at capture because they call
#: for different follow-up: a wrong query is corrected, an incomplete one is
#: fleshed out.
Verdict = Literal["yes", "no", "incomplete"]

#: The longest question this API will accept, in characters.
#:
#: Named rather than written into the validator because it is published in
#: `/v1/meta` as well, and the two must be the same number. A client that
#: learns the rule from `Limits` and then hits a 422 has been lied to, which
#: is worse than not publishing it at all.
MAX_QUESTION_LENGTH = 2000

#: How many `metadata` entries a caller may attach to a question.
MAX_METADATA_ENTRIES = 20

#: How long a single metadata key and value may be.
MAX_METADATA_KEY_LENGTH = 64
MAX_METADATA_VALUE_LENGTH = 256


class AskRequest(Wire):
    """A question, and the few things a caller may say about how to run it."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"question": "What was total net sales for Produce in FY2025?"}
            ]
        },
    )

    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_LENGTH,
        description="The natural-language question.",
    )
    principal: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Database role the rows should be read as, for row-level security. "
            "Rejected unless the server was started with API_ALLOW_PRINCIPAL=true."
        ),
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Opaque key/value pairs echoed back on the job. For a GUI to "
            "correlate a job with whatever it calls a conversation turn. "
            f"At most {MAX_METADATA_ENTRIES} entries, and nothing the server reads."
        ),
    )

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Whitespace is not a question.

        `min_length` alone lets "   " through, and the pipeline would then
        spend a model call screening nothing. Normalising here rather than in
        the route means a generated client sees the same rule in the schema.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("a question cannot be blank")
        return stripped

    @field_validator("metadata")
    @classmethod
    def _bounded(cls, value: dict[str, str]) -> dict[str, str]:
        """Echoed back and kept for the job's lifetime, so it is bounded.

        This is an endpoint anyone who can reach the port may call in a
        loop; unbounded caller-supplied data held in memory is how that
        becomes a problem.
        """
        if len(value) > MAX_METADATA_ENTRIES:
            raise ValueError(f"at most {MAX_METADATA_ENTRIES} metadata entries")
        for key, item in value.items():
            if len(key) > MAX_METADATA_KEY_LENGTH or len(item) > MAX_METADATA_VALUE_LENGTH:
                raise ValueError(f"metadata entry {key!r} is too long")
        return value


class ProgressEvent(Wire):
    """One step of the pipeline, as it happens.

    `seq` is monotonic per job and is what a reconnecting client passes as
    `Last-Event-ID` to resume without replaying what it already drew.
    """

    seq: int
    step: str = Field(description="Graph node name, e.g. 'generate_sql'.")
    label: str = Field(description="Short human label for the step, e.g. 'sql'.")
    detail: str = ""
    at: datetime


class ResultTable(Wire):
    """The rows, in the shape a table widget wants them."""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = Field(
        default=False,
        description="True when the query returned more rows than MAX_ROWS.",
    )


class ChartSpec(Wire):
    """What the Visualiser thinks these rows should be drawn as.

    A suggestion, not a rendering: the GUI owns the chart library. `kind` is
    one of the pipeline's own kinds ('bar', 'line', 'scatter', 'table', ...)
    and the rest name columns of `result`, so a client can map them onto
    whatever it draws with without consulting anything else.
    """

    kind: str
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    series: str | None = None


class Claim(Wire):
    """A sentence in the narrative, tied to the cells that support it."""

    text: str
    value: float | None = None
    cells: list[list[Any]] = Field(
        default_factory=list,
        description="[row_index, column_name] pairs the claim was read from.",
    )
    formula: str | None = None


class AuditReport(Wire):
    """What the Audit Checker made of the narrative."""

    passed: bool = True
    unsupported_claims: list[str] = Field(default_factory=list)
    drop_reasons: list[str] = Field(default_factory=list)
    missing_assumptions: list[str] = Field(
        default_factory=list,
        description=(
            "Defaults the pipeline chose for the question (a fiscal year, say) "
            "that no surviving claim states; the answer states them instead."
        ),
    )
    semantic_issue: str | None = None


class TraceEntry(Wire):
    """Per-node cost, the measurement the architecture is argued from.

    A node that called a model also says which one answered and why it was
    asked (arch5.2): `rung` is what the call was routed at, `route` the
    router's reasoning, and `hops` the routed models that failed before
    `model` answered. All four are empty for a node that called none.
    """

    node: str
    ms: float = 0.0
    model_calls: int = 0
    detail: str = ""
    model: str = ""
    rung: str = ""
    route: str = ""
    hops: list[str] = Field(default_factory=list)


class LiteralMatch(Wire):
    """A phrase from the question, matched to a value in the database."""

    phrase: str
    table: str
    column: str
    value: str
    score: float = 0.0


class Answer(Wire):
    """Everything the pipeline produced for one question.

    A GUI that only wants to show a sentence reads `answer`; one that wants
    to show its work reads `sql`, `result` and `trace`; one that wants to
    show the caveats reads `audit`.
    """

    answer: str = ""
    narrative: str = ""
    sql: str = ""
    verdict: str = "proceed"
    intent: str = "aggregate"
    clarification: str | None = None
    tables: list[str] = Field(default_factory=list)
    literals: list[LiteralMatch] = Field(default_factory=list)
    result: ResultTable | None = None
    chart: ChartSpec | None = None
    claims: list[Claim] = Field(default_factory=list)
    audit: AuditReport = Field(default_factory=AuditReport)
    plan_cost: float | None = None
    attempts: int = 0
    trace: list[TraceEntry] = Field(default_factory=list)
    retrieval_errors: dict[str, str] = Field(
        default_factory=dict,
        description="Retrievers that could not reach their store, by name; the run went on without them.",
    )
    node_errors: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Agents other than retrievers that failed and were survived -- the "
            "supervisor's screening, the narrator's claims -- by name."
        ),
    )


class Reloaded(Wire):
    """What an administrator's reload dropped, to be read again (V6-33)."""

    reloaded: list[str] = Field(
        default_factory=list,
        description="What the agent had read once and now reads again: literals, contract, "
        "schema_edges, knowledge_collections. Empty when it had read none of them yet.",
    )
    sessions_forgotten: int = Field(
        default=0, description="Sessions whose roles and revocation this service will ask Postgres about again."
    )


class JobLinks(Wire):
    self: str
    events: str


class Job(Wire):
    """A question in flight, or one that has finished.

    The same document throughout its life, so a client polls one URL and
    watches `status` rather than learning a second shape when it completes.
    """

    id: str
    status: JobStatus
    question: str
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    progress: list[ProgressEvent] = Field(default_factory=list)
    answer: Answer | None = Field(
        default=None, description="Present once status is 'succeeded'."
    )
    error: str | None = Field(
        default=None, description="Present once status is 'failed'."
    )
    links: JobLinks


class JobList(Wire):
    jobs: list[Job]
    count: int


class FeedbackRequest(Wire):
    """What a user says about an answer: yes or no, and optionally why.

    Only these two fields. The question, the SQL, the row count and the rest
    of the snapshot are taken from the job by the server -- it still has it,
    since a vote happens while the answer is on screen -- rather than being
    sent by the client. A client that supplied its own snapshot could supply
    one that never matched the job, and the staging table would then hold
    evidence of an answer the agent never gave.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"verdict": "no", "comment": "fiscal month is off by one"}]},
    )

    verdict: Verdict = Field(
        description=(
            "Whether the answer was right: 'yes' (correct), 'no' (wrong), or "
            "'incomplete' (correct but incomplete). The whole of the required input."
        )
    )
    comment: str = Field(
        default="",
        max_length=4000,
        description="Optional free text for the reviewer: what was wrong, or what to check.",
    )


class FeedbackModel(Wire):
    """The receipt for a recorded verdict.

    `state` is what the review service has done with it. It is always
    `pending` at capture time -- the writer role cannot see any other state,
    let alone create one -- and is present so the field means the same thing
    in both services' documents.
    """

    id: str
    job_id: str
    verdict: Verdict
    comment: str = ""
    state: Literal["pending"] = "pending"


class Limits(Wire):
    """What a client may not exceed, so it can stop before the server does.

    `max_question_length` and `max_metadata_entries` are here for the clients
    that cannot find them out any other way. A browser sees a 422 in its
    network tab; a desktop application shows the user whatever it was given,
    and "422 Unprocessable Entity" is not an explanation of a text box that
    is forty characters too long.
    """

    max_rows: int
    max_attempts: int
    max_plan_cost: float
    statement_timeout_ms: int
    max_concurrency: int
    max_wait_seconds: float
    max_question_length: int = MAX_QUESTION_LENGTH
    max_metadata_entries: int = MAX_METADATA_ENTRIES


class Pipeline(Wire):
    """Which optional stages this server is running with.

    A GUI uses it to decide what to render: no narrator means no paragraph to
    show, no audit means no verification badge.
    """

    supervisor: bool
    literals: bool
    narrate: bool
    audit: bool
    schema_retrieval: str
    nodes: list[str] = Field(default_factory=list)


class Meta(Wire):
    """Everything a client needs to configure itself against this server."""

    service: str = "nl2sql-agent"
    version: str
    model: str
    intents: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    scope: str = Field(
        default="", description="One sentence naming what the database covers."
    )
    limits: Limits
    pipeline: Pipeline
    tls: dict[str, Any] = Field(default_factory=dict)
    authentication: Literal["none", "bearer", "session"] = "none"
    feedback: bool = Field(
        default=False,
        description=(
            "True when this server has a staging database and will accept "
            "POST /v1/questions/{id}/feedback. A GUI reads it to decide whether "
            "to draw the verdict buttons at all."
        ),
    )
    routing: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The model routing table this server answers with (arch5.2): which "
            "model each task gets at each rung, and why. Empty when the agent "
            "has not started."
        ),
    )

