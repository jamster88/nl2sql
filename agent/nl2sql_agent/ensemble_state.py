"""The ensemble's own state: one question, asked several ways (arch7 section 22.2).

`AgentState` is the state of one run of the pipeline. Above it, the ensemble
keeps a state of its own with the same rules -- every node a function of a
subset returning a partial update, a reducer on every key two writers share,
a lifetime on every field:

* **The anchor contract is one for the whole question.** Every candidate's
  result is held to the contract read from the original; a rewording whose
  own reading would build a different one is never run.
* **`candidates` is append-only and `waves` is the only loop counter.** A
  second wave adds candidates; nothing removes or replaces one.
* **Each candidate's state is whole.** Its trace, attempt history,
  completeness report and audit are kept as the run left them, so whoever
  reads the answer's record sees each run as arch6 would have shown it. The
  outer `trace` holds the outer nodes only.

The payloads are dataclasses, as `state.py`'s are, so `to_jsonable` renders
them -- a `Candidate` whole, inner state and all.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from .state import (
    RUN,
    AnswerContract,
    AuditReport,
    ChartSpec,
    Claim,
    Intent,
    QueryResult,
    TraceEntry,
    Verdict,
    merge_errors,
)

#: A fourth lifetime beside `state.py`'s three: true of one wave's vote, and
#: emptied by the wave planner when a second wave starts, because it
#: describes the vote before it.
WAVE = "wave"

#: Whose wording a candidate asked.
ORIGINAL = "original"
PARAPHRASE = "paraphrase"


@dataclass
class Paraphrase:
    """One rewording of the question, and what the fidelity gate made of it."""

    index: int  # 1..10; the original is candidate 0 and is not a Paraphrase
    text: str
    changed: str  # the Paraphraser's own words for what it varied
    status: str = "pending"  # pending | faithful | discarded
    reason: str = ""  # "F2 literals: 'dairy & eggs' not found", etc.
    screening: dict[str, Any] | None = None  # the Supervisor's reading, seeded into its run


@dataclass
class Candidate:
    """One run of the pipeline on one wording, kept whole."""

    index: int  # 0 the original; otherwise the paraphrase's index
    wording: str
    origin: str  # ORIGINAL | PARAPHRASE
    wave: int
    state: dict[str, Any]  # the inner AgentState, whole
    outcome: str  # how its run ended: `tracing.outcome` of its state
    admissible: bool = False
    reasons: list[str] = field(default_factory=list)  # "E1 answered: gave_up", ...
    signature: str = ""
    group: int | None = None
    started_ms: float = 0.0  # since the question arrived, for the record
    ms: float = 0.0  # how long the run took


@dataclass
class Group:
    """Candidates whose results agree: the connected components of agreement."""

    index: int  # 0 is the largest
    members: list[int]  # candidate indices
    representative: int
    signature: str


@dataclass
class Agreement:
    """How the vote went: `agreed` of the `admissible` agreed, of `total` run.

    `admissible` counts the runs that voted: those that passed E1-E5 and
    whose answer the Judge did not set aside. `set_aside` is how many the
    Judge kept from voting, so `total - admissible - set_aside` could not
    answer. When the Judge accepted no answer at all the runs vote anyway,
    and `set_aside` is 0 (arch7.1 section 22.7).
    """

    admissible: int = 0
    agreed: int = 0
    total: int = 0
    level: str = "none"  # unanimous | majority | judged | contested | single | none
    why: str = ""
    set_aside: int = 0


@dataclass
class GroupVerdict:
    """The Judge's verdict on one group's answer."""

    group: int
    accepted: bool
    why: str = ""


@dataclass
class Judgement:
    """The Judge's verdicts on the answers, given before the vote (arch7.1 section 22.7).

    `verdicts` is one per group, in group order; `set_aside` the runs whose
    answer it rejected; `overruled` whether it set aside the answer the vote
    alone would have delivered, and `instead_of` that answer's run. `error`
    is why it could not be asked, when it could not -- and then the vote is
    the runs' own.
    """

    verdicts: list[GroupVerdict] = field(default_factory=list)
    set_aside: list[int] = field(default_factory=list)
    overruled: bool = False
    instead_of: int | None = None
    model: str = ""
    error: str = ""


@dataclass
class JoinedColumn:
    """A column another agreeing run carried, joined onto the chosen rows."""

    column: str
    from_candidate: int
    key: str  # the entity key it was joined on
    table: str


@dataclass
class DeclinedColumn:
    """A column another agreeing run carried that would not join cleanly."""

    column: str
    from_candidate: int
    why: str  # "row 3 has no match", "key repeats in run 2", ...


@dataclass
class Dissent:
    """A group that lost the vote, by its key fact and how its query differs."""

    group: int
    members: list[int]
    signature: str
    differs: str


@dataclass
class Decision:
    """What was delivered, and from which runs."""

    chosen: int | None = None
    fused_from: list[int] = field(default_factory=list)
    columns_fused: bool = True  # the toggle's value for this run
    joined_columns: list[JoinedColumn] = field(default_factory=list)
    declined_columns: list[DeclinedColumn] = field(default_factory=list)
    claims_added: int = 0
    claims_dropped: int = 0
    dissent: list[Dissent] = field(default_factory=list)
    line: str = ""  # the agreement line, as rendered


def judged(judgement: Judgement | None) -> str:
    """What the Judge did, in a word or two, for a trace's tag and the
    benchmark's record: `not asked` (off, or no answer to judge), `failed`,
    `accepted` every answer, `set aside` some, `overruled` the runs' own
    choice, or `accepted none`."""
    if judgement is None:
        return "not asked"
    if judgement.error:
        return "failed"
    if judgement.verdicts and not any(verdict.accepted for verdict in judgement.verdicts):
        return "accepted none"
    if judgement.overruled:
        return "overruled"
    return "set aside" if judgement.set_aside else "accepted"


def upsert_candidates(left: list[Candidate], right: list[Candidate]) -> list[Candidate]:
    """A wave's runs appended; a run already there updated where it stands.

    `answer` adds candidates and `validate` marks them -- admissible or not,
    their group -- so a candidate comes back with its own index and replaces
    itself. Nothing is ever removed: append-only, as arch7 section 22.2 has
    it, in what ran.
    """
    merged = list(left or [])
    where = {candidate.index: position for position, candidate in enumerate(merged)}
    for candidate in right or []:
        if candidate.index in where:
            merged[where[candidate.index]] = candidate
        else:
            where[candidate.index] = len(merged)
            merged.append(candidate)
    return merged


class EnsembleState(TypedDict, total=False):
    """arch7 section 22.2, field for field, and four fields more.

    `screening` is the anchor screening's reading, kept to seed the
    original's run, `wave_plan` the candidates the current wave runs and
    `paraphrase_retried` the Paraphraser's one retry spent (the
    implementation specification's sections 3.8.1, 3.8.5 and 3.8.4);
    `parallel_calls` is how many runs the question was allowed at once,
    which its record on the wire states.
    """

    # --- input -----------------------------------------------------------
    question: str
    principal: str | None
    parallel_calls: int

    # --- stage 0: the anchor and the rewordings -----------------------------
    verdict: Verdict
    intent: Intent
    clarification: str | None
    answer_contract: AnswerContract
    screening: dict[str, Any] | None
    paraphrases: list[Paraphrase]
    #: The Paraphraser's one retry has been spent (arch7 section 22.3).
    paraphrase_retried: bool
    waves: int
    deadline: float  # monotonic; 0.0 when none
    wave_plan: list[int]

    # --- the candidate runs ------------------------------------------------
    candidates: Annotated[list[Candidate], upsert_candidates]

    # --- stage 6 -----------------------------------------------------------
    groups: list[Group]
    agreement: Agreement
    judgement: Judgement | None
    decision: Decision

    # --- output and observability -------------------------------------------
    answer: str
    narrative: str
    sql: str
    result: QueryResult | None
    chart: ChartSpec | None
    claims: list[Claim]
    audit: AuditReport
    assumptions: list[str]
    error: str | None
    node_errors: Annotated[dict[str, str], merge_errors]
    trace: Annotated[list[TraceEntry], operator.add]
    trace_id: str


def new_ensemble_state(
    question: str, *, principal: str | None = None, deadline: float = 0.0, parallel_calls: int = 1
) -> EnsembleState:
    """The initial state for one question: everything a node might read, empty."""
    return {
        "question": question,
        "principal": principal,
        "parallel_calls": parallel_calls,
        "verdict": "proceed",
        "intent": "aggregate",
        "clarification": None,
        "answer_contract": AnswerContract(),
        "screening": None,
        "paraphrases": [],
        "paraphrase_retried": False,
        "waves": 0,
        "deadline": deadline,
        "wave_plan": [],
        "candidates": [],
        "groups": [],
        "agreement": Agreement(),
        "judgement": None,
        "decision": Decision(),
        "answer": "",
        "narrative": "",
        "sql": "",
        "result": None,
        "chart": None,
        "claims": [],
        "audit": AuditReport(),
        "assumptions": [],
        "error": None,
        "node_errors": {},
        "trace": [],
        "trace_id": "",
    }


#: Every field of `EnsembleState` and how long it is true for: the vote and
#: what was decided from it are one wave's; everything else is the run's.
LIFETIMES: dict[str, str] = {
    name: WAVE if name in ("wave_plan", "groups", "agreement", "judgement", "decision") else RUN
    for name in EnsembleState.__annotations__
}


def wave_reset() -> dict[str, Any]:
    """Every `wave` field, empty: what the wave planner's update carries
    when a second wave starts. Fresh objects on every call."""
    return {"wave_plan": [], "groups": [], "agreement": Agreement(), "judgement": None, "decision": Decision()}


def ensemble_fields() -> tuple[str, ...]:
    """The names `EnsembleState` declares, in declaration order."""
    return tuple(EnsembleState.__annotations__)


def is_ensemble(state: Any) -> bool:
    """Whether a finished state is the ensemble's rather than one run's."""
    return isinstance(state, dict) and "candidates" in state


def chosen(state: Any) -> Candidate | None:
    """The candidate whose run was delivered: the decision's, or the
    original's when nothing was chosen -- `agreement.level` none, whose
    answer is the original's give-up -- and None when nothing ran."""
    candidates = list(state.get("candidates") or [])
    decision = state.get("decision")
    index = getattr(decision, "chosen", None)
    if index is None:
        index = 0
    return next((candidate for candidate in candidates if candidate.index == index), None)


def run_state(state: Any) -> dict[str, Any]:
    """The run whose record stands for the question: the chosen candidate's
    state under the ensemble, the state itself without it, and an empty one
    for an ensemble that ran nothing."""
    if not is_ensemble(state):
        return state
    candidate = chosen(state)
    return candidate.state if candidate is not None else {}


def whole_trace(state: Any) -> list[Any]:
    """Every node that ran for the question, once: the outer nodes but the
    one that ran the candidates -- whose time is theirs -- then each
    candidate's own, in index order. What the benchmark attributes time and
    model calls from."""
    if not is_ensemble(state):
        return list(state.get("trace") or [])
    entries = [entry for entry in state.get("trace") or [] if _node(entry) != "answer"]
    for candidate in sorted(state.get("candidates") or [], key=lambda c: c.index):
        entries.extend(candidate.state.get("trace") or [])
    return entries


def _node(entry: Any) -> str:
    return entry.get("node", "") if isinstance(entry, dict) else getattr(entry, "node", "")
