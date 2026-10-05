"""The review service's wire contract.

Same two rules as the agent API's `models.py`, for the same reasons:
nothing is `None` where a list would do, and the vocabulary already in use
is preserved rather than renamed at the boundary. `verdict`, `draft`,
`suite`, `chunk_id` and `pair_id` all mean here exactly what they mean in
`ragproc.golden_pairs`.

Every model is `Wire`, which forbids a field it does not declare: in a
request a misspelt field is a 422 rather than ignored, and a response built
from a row or a dict that has grown a field fails a test rather than
quietly dropping it.

One rule is specific to this service. **A submission's own fields are never
writable.** The question, the SQL and the verdict are what a user said, and
an API that let a curator edit them would turn the staging table into a
place where evidence changes. Everything editable lives in `draft`, beside
the original and visibly derived from it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator
from nl2sql_common.envelope import Wire

#: Correct, wrong, correct but incomplete: `store.VERDICTS`, as a type.
Verdict = Literal["yes", "no", "incomplete"]
State = Literal["pending", "accepted", "rejected", "promoted", "corrected"]
#: The two stores a fix can go into: `corrections` for a wrong answer,
#: `completions` for one that was right and incomplete.
FixKind = Literal["corrections", "completions"]


class SubmissionModel(Wire):
    """One captured verdict, with the snapshot that outlives the job."""

    id: str
    job_id: str
    verdict: Verdict
    question: str
    sql_code: str = ""
    answer: str = ""
    narrative: str = ""
    intent: str = ""
    tables: str = ""
    row_count: int = 0
    columns: list[str] = Field(default_factory=list)
    comment: str = ""
    agent_version: str = ""
    submitted_at: datetime | None = None
    state: State = "pending"
    reviewer: str = ""
    review_note: str = ""
    reviewed_at: datetime | None = None
    draft: dict[str, Any] = Field(default_factory=dict)
    promoted_pair_id: str | None = None


class SubmissionList(Wire):
    submissions: list[SubmissionModel]
    count: int
    #: How many sit in each state, every state present even at zero, so a
    #: queue badge reads "0" rather than vanishing.
    counts: dict[str, int] = Field(default_factory=dict)
    #: The same, per verdict: the review GUI keeps one queue per verdict.
    counts_by_verdict: dict[str, dict[str, int]] = Field(default_factory=dict)


class DraftModel(Wire):
    """The golden pair a curator is building out of a submission.

    Seeded from the submission, but three of these cannot be: `keywords`,
    `reasoning_target` and `result` are judgements about what the question
    tests, and no thumbs-up carries them. They are the work.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)
    question: str = Field(default="", max_length=2000)
    tables: str = Field(default="", max_length=1000)
    keywords: str = Field(default="", max_length=1000)
    reasoning_target: str = Field(default="", max_length=4000)
    sql_code: str = Field(default="", max_length=20000)
    result: str = Field(default="", max_length=1000)
    translation_note: str = Field(default="", max_length=2000)
    suite: str = Field(default="", max_length=200)
    extra_meta: dict[str, str] = Field(default_factory=dict)

    @field_validator("extra_meta")
    @classmethod
    def _bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 10:
            raise ValueError("at most 10 extra meta entries")
        for key, item in value.items():
            if len(key) > 64 or len(item) > 512:
                raise ValueError(f"meta entry {key!r} is too long")
        return value


class ReviewRequest(Wire):
    """What a curator may change. Deliberately four fields."""

    model_config = ConfigDict(extra="forbid")

    state: State | None = None
    reviewer: str | None = Field(default=None, max_length=120)
    review_note: str | None = Field(default=None, max_length=4000)
    draft: DraftModel | None = None

    @field_validator("state")
    @classmethod
    def _not_promoted(cls, value: State | None) -> State | None:
        """`promoted` is something that happens, not something that is set.

        Setting it by hand would mark a submission as being in the golden
        set without anything having been written to the document -- the one
        inconsistency this service exists to prevent.
        """
        if value == "promoted":
            raise ValueError(
                "a submission becomes 'promoted' by POSTing to /v1/submissions/{id}/promote, "
                "which writes the question document; it cannot be set directly"
            )
        if value == "corrected":
            raise ValueError(
                "a submission becomes 'corrected' by POSTing a validated fix to "
                "/v1/submissions/{id}/fix; it cannot be set directly"
            )
        return value


class PreviewRequest(Wire):
    model_config = ConfigDict(extra="forbid")
    draft: DraftModel


class PreviewModel(Wire):
    """The block that would be written, and why it could not be."""

    pair_id: str = ""
    markdown: str = ""
    valid: bool = False
    problems: list[str] = Field(default_factory=list)
    #: The suite a pair appended now would inherit, so the form can show what
    #: leaving the suite field blank actually means.
    suite_in_force: str = ""


class StepModel(Wire):
    name: str
    ran: bool
    ok: bool = True
    detail: str = ""


class PromotionModel(Wire):
    """What a promotion did, including the parts that did not work.

    `reloaded` false with `pair_id` set is a real and useful state: the pair
    is in the golden question document -- which is the source of truth -- and
    the stores built from it have not caught up. Nothing is lost; the agent
    just will not retrieve it until a loader runs.
    """

    pair_id: str
    chunk_id: str
    suite: str = ""
    title: str = ""
    markdown: str = ""
    document: str = ""
    backup: str = ""
    pairs_before: int = 0
    pairs_after: int = 0
    reloaded: bool = False
    steps: list[StepModel] = Field(default_factory=list)


class PromotionRecord(Wire):
    """A row of the promotion log."""

    pair_id: str
    submission_id: str
    chunk_id: str
    suite: str = ""
    title: str = ""
    reviewer: str = ""
    promoted_at: datetime | None = None
    reloaded: bool = False
    reload_detail: str = ""


class PromotionList(Wire):
    promotions: list[PromotionRecord]
    count: int


class GoldenPairModel(Wire):
    """A pair already in the set, as the document holds it.

    Read from the markdown rather than from the context store on purpose: the
    document is the source of truth, and a reviewer deciding whether a new
    question duplicates an existing one should be shown the thing their edit
    will be appended to.
    """

    pair_id: str
    chunk_id: str
    title: str
    suite: str
    question: str
    tables: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    reasoning_target: str = ""
    sql_code: str = ""
    result: str = ""
    #: The submission it was promoted from, when the review queue produced
    #: it: taking the pair out puts that submission back in the queue.
    submission_id: str | None = None


class GoldenSet(Wire):
    pairs: list[GoldenPairModel]
    count: int
    document: str = ""
    next_pair_id: str = ""
    suite_in_force: str = ""
    #: Present when the document cannot be read or parsed. The review GUI
    #: shows it instead of an empty set, because "no golden questions" and
    #: "the file is unreadable" should not look the same.
    error: str | None = None


class ReviewLimits(Wire):
    reload_timeout_seconds: float
    #: What a validation run is held to: the same kind of limits the agent's
    #: own queries have.
    validate_timeout_ms: int = 30000
    validate_max_rows: int = 200


# ---------------------------------------------------------------------------
# Fixing a wrong or incomplete answer
# ---------------------------------------------------------------------------


class ValidateRequest(Wire):
    """The SQL a reviewer thinks the agent should have written."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(max_length=20000)


class ValidationModel(Wire):
    """What running it against the live retail database showed.

    `valid` is the only field that decides anything: a fix is stored only
    when the query passed the static checks and ran to completion. Warnings
    -- no rows, more rows than were read -- are for the reviewer to judge.
    """

    sql: str
    valid: bool
    problems: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    plan_cost: float | None = None
    elapsed_ms: float = 0.0


class FixRequest(Wire):
    """Store a validated fix. The SQL is validated again here, server side."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(max_length=20000)
    review_note: str = Field(default="", max_length=4000)


class FixModel(Wire):
    """One stored fix: the question, the incorrect answer and the correct one."""

    fix_id: str
    #: None for a fix written in the curation interface.
    submission_id: str | None = None
    job_id: str = ""
    question: str
    incorrect_sql: str = ""
    incorrect_answer: str = ""
    incorrect_columns: list[str] = Field(default_factory=list)
    incorrect_row_count: int = 0
    corrected_sql: str = ""
    corrected_columns: list[str] = Field(default_factory=list)
    corrected_rows: list[list[Any]] = Field(default_factory=list)
    corrected_row_count: int = 0
    corrected_truncated: bool = False
    plan_cost: float | None = None
    user_comment: str = ""
    reviewer: str = ""
    review_note: str = ""
    agent_version: str = ""
    #: `review` for a fix made from a submission, `curated` for one written
    #: straight into the store.
    source: str = "review"
    created_at: datetime | None = None
    embedded: bool = False


class FixList(Wire):
    kind: FixKind
    fixes: list[FixModel]
    count: int


class FixResultModel(Wire):
    """What saving a fix did, including the part that may not have worked.

    `embedded` false with a `fix` present is a real state, like a promotion
    whose reload failed: the record is stored, and its vector will be
    written by the next save that can reach the embedding host.
    """

    kind: FixKind
    fix: FixModel
    validation: ValidationModel
    submission: SubmissionModel
    embedded: bool = False
    embed_detail: str = ""


class WithdrawalModel(Wire):
    """What reopening or deleting a submission took back out.

    `kind` says where from: `golden` for a promoted pair taken out of the
    question document -- with the counts, the backup and the reload, as a
    promotion reports them -- or the fix store a correction or completion was
    deleted from. `found` false means it was already gone, removed by hand,
    and there was nothing to take out.
    """

    kind: Literal["golden", "corrections", "completions"]
    id: str
    found: bool
    document: str = ""
    backup: str = ""
    pairs_before: int = 0
    pairs_after: int = 0
    #: For `golden`: whether both stores were rebuilt from the document.
    reloaded: bool = False
    steps: list[StepModel] = Field(default_factory=list)


class UndoModel(Wire):
    """A submission reopened or deleted, and what came out with it."""

    action: Literal["reopened", "deleted"]
    #: As it is now when reopened; as it was when deleted.
    submission: SubmissionModel
    #: None when it had produced nothing: it was pending, accepted or rejected.
    withdrawn: WithdrawalModel | None = None


# ---------------------------------------------------------------------------
# Curation (5.6): golden pairs, fixes and SQL snippets written directly
# ---------------------------------------------------------------------------


class GoldenResultModel(Wire):
    """A golden pair added in the curation interface: its SQL's run, then the write."""

    promotion: PromotionModel
    validation: ValidationModel


class GoldenRemovalModel(Wire):
    """A pair taken out of the golden set, and the submission that went back.

    `submission` is set when the review queue had produced the pair: it is
    reopened -- back to pending, the pair as its draft -- so the queue never
    claims a pair the golden set no longer holds.
    """

    pair_id: str
    withdrawal: WithdrawalModel
    submission: SubmissionModel | None = None


class CuratedFixValidateRequest(Wire):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(max_length=20000)
    #: The query the agent wrote, when the curator has it: a fix has to differ.
    incorrect_sql: str = Field(default="", max_length=20000)


class CuratedFixRequest(Wire):
    """A fix with no submission behind it. The SQL is validated here again."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(max_length=20000)
    incorrect_sql: str = Field(default="", max_length=20000)
    review_note: str = Field(default="", max_length=4000)


class CuratedFixResultModel(Wire):
    kind: FixKind
    fix: FixModel
    validation: ValidationModel
    embedded: bool = False
    embed_detail: str = ""


class FixRemovalModel(Wire):
    """A fix taken out of its store, and the submission that went back, if any."""

    kind: FixKind
    fix_id: str
    found: bool
    fix: FixModel | None = None
    submission: SubmissionModel | None = None


SnippetKind = Literal["join", "filter", "measure", "dimension"]


class SnippetDraftModel(Wire):
    """A snippet as the curation form holds it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=200)
    kind: str = Field(default="", max_length=20)
    tables: str = Field(default="", max_length=1000)
    keywords: str = Field(default="", max_length=2000)
    means: str = Field(default="", max_length=2000)
    applies_to: str = Field(default="", max_length=4000)
    sql: str = Field(default="", max_length=8000)
    note: str = Field(default="", max_length=2000)


class SnippetRequest(Wire):
    model_config = ConfigDict(extra="forbid")
    draft: SnippetDraftModel


class SnippetPreviewRequest(Wire):
    model_config = ConfigDict(extra="forbid")
    draft: SnippetDraftModel
    #: The snippet being changed; absent for a new one.
    snippet_id: str | None = Field(default=None, max_length=20)


class SnippetPreviewModel(Wire):
    snippet_id: str = ""
    markdown: str = ""
    valid: bool = False
    problems: list[str] = Field(default_factory=list)


class SnippetValidationModel(Wire):
    """What running the snippet inside its probe query showed.

    `valid` decides; the warnings -- a join that multiplies or drops rows, a
    filter that matches nothing or everything -- are for the curator.
    """

    kind: str
    valid: bool
    problems: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    probe_sql: str = ""
    applies_to: str = ""
    sql: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    rows_before: int | None = None
    rows_after: int | None = None
    tables: list[str] = Field(default_factory=list)
    plan_cost: float | None = None
    elapsed_ms: float = 0.0


class SnippetModel(Wire):
    """A snippet as the document holds it."""

    snippet_id: str
    chunk_id: str
    name: str
    kind: str
    tables: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    means: str = ""
    applies_to: str = ""
    sql: str = ""
    note: str = ""


class SnippetStoreModel(Wire):
    """What the snippet store holds, beside the document it was loaded from.

    `current` is the store holding exactly this document, embedded: what the
    stack's start compares, and what a write's reload is meant to make true.
    """

    reachable: bool = False
    snippets: int = 0
    embedded: int = 0
    current: bool = False
    detail: str = ""


class SnippetSet(Wire):
    snippets: list[SnippetModel]
    count: int
    document: str = ""
    next_snippet_id: str = ""
    kinds: list[str] = Field(default_factory=list)
    store: SnippetStoreModel = Field(default_factory=SnippetStoreModel)
    error: str | None = None


class SnippetResultModel(Wire):
    """What adding, changing or removing a snippet did -- the reload included."""

    action: Literal["added", "changed", "removed"]
    snippet_id: str
    chunk_id: str
    markdown: str = ""
    document: str = ""
    backup: str = ""
    snippets_before: int = 0
    snippets_after: int = 0
    reloaded: bool = False
    steps: list[StepModel] = Field(default_factory=list)
    validation: SnippetValidationModel | None = None


class SchemaColumn(Wire):
    name: str
    type: str


class SchemaTable(Wire):
    name: str
    columns: list[SchemaColumn] = Field(default_factory=list)


class SchemaModel(Wire):
    """The retail tables and columns, as the role a snippet is validated as sees them."""

    tables: list[SchemaTable] = Field(default_factory=list)
    error: str | None = None


class ReviewMeta(Wire):
    """Everything the review GUI needs to configure itself."""

    service: str = "nl2sql-review"
    version: str
    states: list[str] = Field(default_factory=list)
    document: str = ""
    golden_count: int = 0
    next_pair_id: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    verdicts: list[str] = Field(default_factory=list)
    counts_by_verdict: dict[str, dict[str, int]] = Field(default_factory=dict)
    #: How many fixes each store holds, keyed by store.
    fixes: dict[str, int] = Field(default_factory=dict)
    reload_context: bool = True
    reload_vectors: bool = True
    limits: ReviewLimits
    authentication: Literal["none", "bearer", "session"] = "none"
    warnings: list[str] = Field(default_factory=list)

