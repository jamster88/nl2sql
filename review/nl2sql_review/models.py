"""The review service's wire contract.

Same two rules as the agent API's `models.py`, for the same reasons:
nothing is `None` where a list would do, and the vocabulary already in use
is preserved rather than renamed at the boundary. `verdict`, `draft`,
`suite`, `chunk_id` and `pair_id` all mean here exactly what they mean in
`ragproc.golden_pairs`.

One rule is specific to this service. **A submission's own fields are never
writable.** The question, the SQL and the verdict are what a user said, and
an API that let a curator edit them would turn the staging table into a
place where evidence changes. Everything editable lives in `draft`, beside
the original and visibly derived from it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Correct, wrong, correct but incomplete: `store.VERDICTS`, as a type.
Verdict = Literal["yes", "no", "incomplete"]
State = Literal["pending", "accepted", "rejected", "promoted"]


class SubmissionModel(BaseModel):
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


class SubmissionList(BaseModel):
    submissions: list[SubmissionModel]
    count: int
    #: How many sit in each state, every state present even at zero, so a
    #: queue badge reads "0" rather than vanishing.
    counts: dict[str, int] = Field(default_factory=dict)


class DraftModel(BaseModel):
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


class ReviewRequest(BaseModel):
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
        return value


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: DraftModel


class PreviewModel(BaseModel):
    """The block that would be written, and why it could not be."""

    pair_id: str = ""
    markdown: str = ""
    valid: bool = False
    problems: list[str] = Field(default_factory=list)
    #: The suite a pair appended now would inherit, so the form can show what
    #: leaving the suite field blank actually means.
    suite_in_force: str = ""


class StepModel(BaseModel):
    name: str
    ran: bool
    ok: bool = True
    detail: str = ""


class PromotionModel(BaseModel):
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


class PromotionRecord(BaseModel):
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


class PromotionList(BaseModel):
    promotions: list[PromotionRecord]
    count: int


class GoldenPairModel(BaseModel):
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


class GoldenSet(BaseModel):
    pairs: list[GoldenPairModel]
    count: int
    document: str = ""
    next_pair_id: str = ""
    suite_in_force: str = ""
    #: Present when the document cannot be read or parsed. The review GUI
    #: shows it instead of an empty set, because "no golden questions" and
    #: "the file is unreadable" should not look the same.
    error: str | None = None


class ReviewLimits(BaseModel):
    max_pair_number: int
    reload_timeout_seconds: float


class ReviewMeta(BaseModel):
    """Everything the review GUI needs to configure itself."""

    service: str = "nl2sql-review"
    version: str
    states: list[str] = Field(default_factory=list)
    document: str = ""
    golden_count: int = 0
    next_pair_id: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    reload_context: bool = True
    reload_vectors: bool = True
    limits: ReviewLimits
    authentication: Literal["none", "bearer"] = "none"
    warnings: list[str] = Field(default_factory=list)


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    uptime_seconds: float


class Check(BaseModel):
    ok: bool
    detail: str = ""


class Readiness(BaseModel):
    ready: bool
    checks: dict[str, Check]
    warnings: list[str] = Field(default_factory=list)


class ApiError(BaseModel):
    """The same error envelope the agent API uses, so one client parses both."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "not_promotable",
                        "message": "keywords is empty -- needs keywords for the BM25 index",
                    }
                }
            ]
        }
    )

    error: dict[str, Any]
