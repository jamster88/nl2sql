"""Turning an `AgentState` into the published `Answer`.

Its own module because it is the seam that has to hold: `state.py` is
internal and changes with the pipeline, `models.py` is the contract other
people's code is written against. Everything that knows about both lives
here, so a field renamed inside the graph breaks one file and one set of
tests rather than leaking into the API.

The translation is deliberately total. Every list is a list, every missing
value has a defined stand-in, and a state carrying a `Decimal` or a `date`
out of Postgres is passed through `to_jsonable` first -- a GUI should never
be the thing that discovers a cell type JSON cannot encode.

It is also strict the other way. The nested payloads are built with
`Model(**fields)` into models that forbid what they do not declare, so a
field the pipeline's dataclass gains and the wire model does not is an
error in the tests rather than a field silently left behind.
"""

from __future__ import annotations

from typing import Any

from ..ensemble import step_label
from ..ensemble_state import is_ensemble, run_state
from ..state import to_jsonable
from .jobs import Job, ProgressRecord
from .models import (
    Answer,
    AuditReport,
    ChartSpec,
    Claim,
    DeclinedColumn,
    DiscardedRewording,
    Ensemble,
    EnsembleAgreement,
    EnsembleCandidate,
    EnsembleDissent,
    EnsembleJudgement,
    Job as JobModel,
    JobLinks,
    JoinedColumn,
    LiteralMatch,
    ProgressEvent,
    ResultTable,
    TraceEntry,
)


def _dicts(values: Any) -> list[dict[str, Any]]:
    """A list of dataclasses or dicts, as plain dicts."""
    return [d for d in (to_jsonable(values) or []) if isinstance(d, dict)]


def result_table(result: Any) -> ResultTable | None:
    """The rows, whether they arrived as a `QueryResult` or a plain dict.

    Both shapes turn up: the pipeline carries the dataclass and the
    benchmark's saved states carry dictionaries.
    """
    if result is None:
        return None
    data = to_jsonable(result)
    if not isinstance(data, dict):
        return None
    rows = data.get("rows") or []
    return ResultTable(
        columns=list(data.get("columns") or []),
        rows=[list(row) for row in rows],
        row_count=len(rows),
        truncated=bool(data.get("truncated")),
    )


def _codes(errors: Any, failed: str) -> dict[str, str]:
    """Which parts failed, without their words: a retriever that is switched
    off says so; anything else says only that it `failed`."""
    return {
        name: "disabled" if str(message).endswith("disabled") else failed
        for name, message in dict(errors or {}).items()
    }


def answer_from_state(state: dict[str, Any] | None, *, detail: bool = True) -> Answer:
    """The public answer for a finished run.

    A state that refused the question, or gave up inside its retry budget,
    translates just as completely as one that succeeded -- the caller finds
    out which from `verdict`, `answer` and the absence of `result`, not from
    a different response shape.

    The ensemble's state (arch7) translates to the same shape, so a client
    written for 6.3 reads one coherent run: the answer, the rows and the
    claims are what was delivered; the tables, literals, attempts and
    retrieval failures are the delivered run's; the trace is the ensemble's
    own nodes followed by that run's; and `ensemble` holds the vote and
    every run's record. A single run's state has `ensemble` None.

    Without `detail` -- anyone but an operator (V6-32) -- `retrieval_errors`
    and `node_errors` say which part failed and not the driver's words for
    it, which name hosts and ports.
    """
    state = state or {}
    run = run_state(state)
    audit = to_jsonable(state.get("audit")) or {}
    chart = to_jsonable(state.get("chart"))
    node_errors = {**(run.get("node_errors") or {}), **(state.get("node_errors") or {})}
    trace = list(state.get("trace") or []) + (list(run.get("trace") or []) if run is not state else [])

    return Answer(
        answer=state.get("answer") or "",
        narrative=(state.get("narrative") or "").strip(),
        sql=state.get("sql") or "",
        verdict=state.get("verdict") or "proceed",
        intent=state.get("intent") or "aggregate",
        clarification=state.get("clarification"),
        tables=list(run.get("selected_tables") or []),
        literals=[LiteralMatch(**m) for m in _dicts(run.get("literal_map"))],
        result=result_table(state.get("result")),
        chart=ChartSpec(**chart) if isinstance(chart, dict) else None,
        claims=[Claim(**c) for c in _dicts(state.get("claims"))],
        audit=AuditReport(**audit) if isinstance(audit, dict) else AuditReport(),
        plan_cost=run.get("plan_cost"),
        attempts=int(run.get("attempts") or 0),
        trace=[TraceEntry(**t) for t in _dicts(trace)],
        retrieval_errors=(
            dict(run.get("retrieval_errors") or {}) if detail else _codes(run.get("retrieval_errors"), "unavailable")
        ),
        node_errors=node_errors if detail else _codes(node_errors, "failed"),
        ensemble=ensemble_from_state(state, detail=detail) if is_ensemble(state) else None,
    )


def ensemble_from_state(state: dict[str, Any], *, detail: bool = True) -> Ensemble:
    """The ensemble's record, as the wire carries it.

    Without `detail` a run's reasons keep their rule's name and lose what
    follows it, as `_codes` does for the error maps: a reason can quote a
    driver.
    """
    decision = to_jsonable(state.get("decision")) or {}
    judgement = to_jsonable(state.get("judgement"))
    paraphrases = {p.index: p for p in state.get("paraphrases") or []}
    candidates = [
        EnsembleCandidate(
            index=c.index,
            wording=c.wording,
            origin=c.origin,
            wave=c.wave,
            changed=paraphrases[c.index].changed if c.index in paraphrases else "",
            outcome=c.outcome,
            admissible=c.admissible,
            reasons=list(c.reasons) if detail else [reason.split(":", 1)[0] for reason in c.reasons],
            sql=c.state.get("sql") or "",
            signature=c.signature,
            attempts=int(c.state.get("attempts") or 0),
            group=c.group,
            duration_ms=c.ms,
            trace=[TraceEntry(**t) for t in _dicts(c.state.get("trace"))],
        )
        for c in sorted(state.get("candidates") or [], key=lambda c: c.index)
    ]
    return Ensemble(
        agreement=EnsembleAgreement(**to_jsonable(state.get("agreement"))),
        chosen=decision.get("chosen"),
        fused_from=list(decision.get("fused_from") or []),
        columns_fused=bool(decision.get("columns_fused", True)),
        joined_columns=[JoinedColumn(**c) for c in decision.get("joined_columns") or []],
        declined_columns=[DeclinedColumn(**c) for c in decision.get("declined_columns") or []],
        claims_added=int(decision.get("claims_added") or 0),
        claims_dropped=int(decision.get("claims_dropped") or 0),
        dissent=[EnsembleDissent(**d) for d in decision.get("dissent") or []],
        judged=EnsembleJudgement(**judgement) if isinstance(judgement, dict) else None,
        candidates=candidates,
        discarded=[
            DiscardedRewording(index=p.index, text=p.text, changed=p.changed, reason=p.reason)
            for p in paraphrases.values()
            if p.status == "discarded"
        ],
        parallel_calls=int(state.get("parallel_calls") or 1),
    )


def progress_event(record: ProgressRecord) -> ProgressEvent:
    return ProgressEvent(
        seq=record.seq,
        step=record.step,
        label=step_label(record.step, record.candidate),
        detail=record.detail,
        at=record.at,
        candidate=record.candidate,
    )


def job_model(job: Job, *, base: str = "", detail: bool = True) -> JobModel:
    """A job as the client sees it, with the two URLs it needs next.

    `base` is the server's root path, so the links stay correct when the API
    is mounted under a prefix by a reverse proxy in front of a GUI. `detail`
    is whether the caller is an operator, who sees a crash in its own words.
    """
    href = f"{base}/v1/questions/{job.id}"
    return JobModel(
        id=job.id,
        status=job.status,  # type: ignore[arg-type]
        question=job.question,
        metadata=dict(job.metadata),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_ms=job.duration_ms,
        progress=[progress_event(record) for record in job.progress],
        answer=answer_from_state(job.state, detail=detail) if job.state is not None else None,
        error=(job.fault or job.error) if detail else job.error,
        links=JobLinks(self=href, events=f"{href}/events"),
    )
