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

from ..graph import step_label
from ..state import to_jsonable
from .jobs import Job, ProgressRecord
from .models import (
    Answer,
    AuditReport,
    ChartSpec,
    Claim,
    Job as JobModel,
    JobLinks,
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

    Without `detail` -- anyone but an operator (V6-32) -- `retrieval_errors`
    and `node_errors` say which part failed and not the driver's words for
    it, which name hosts and ports.
    """
    state = state or {}
    audit = to_jsonable(state.get("audit")) or {}
    chart = to_jsonable(state.get("chart"))

    return Answer(
        answer=state.get("answer") or "",
        narrative=(state.get("narrative") or "").strip(),
        sql=state.get("sql") or "",
        verdict=state.get("verdict") or "proceed",
        intent=state.get("intent") or "aggregate",
        clarification=state.get("clarification"),
        tables=list(state.get("selected_tables") or []),
        literals=[LiteralMatch(**m) for m in _dicts(state.get("literal_map"))],
        result=result_table(state.get("result")),
        chart=ChartSpec(**chart) if isinstance(chart, dict) else None,
        claims=[Claim(**c) for c in _dicts(state.get("claims"))],
        audit=AuditReport(**audit) if isinstance(audit, dict) else AuditReport(),
        plan_cost=state.get("plan_cost"),
        attempts=int(state.get("attempts") or 0),
        trace=[TraceEntry(**t) for t in _dicts(state.get("trace"))],
        retrieval_errors=(
            dict(state.get("retrieval_errors") or {}) if detail else _codes(state.get("retrieval_errors"), "unavailable")
        ),
        node_errors=dict(state.get("node_errors") or {}) if detail else _codes(state.get("node_errors"), "failed"),
    )


def progress_event(record: ProgressRecord) -> ProgressEvent:
    return ProgressEvent(
        seq=record.seq,
        step=record.step,
        label=step_label(record.step),
        detail=record.detail,
        at=record.at,
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
