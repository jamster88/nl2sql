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


def answer_from_state(state: dict[str, Any] | None) -> Answer:
    """The public answer for a finished run.

    A state that refused the question, or gave up inside its retry budget,
    translates just as completely as one that succeeded -- the caller finds
    out which from `verdict`, `answer` and the absence of `result`, not from
    a different response shape.
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
        retrieval_errors=dict(state.get("retrieval_errors") or {}),
    )


def progress_event(record: ProgressRecord) -> ProgressEvent:
    return ProgressEvent(
        seq=record.seq,
        step=record.step,
        label=step_label(record.step),
        detail=record.detail,
        at=record.at,
    )


def job_model(job: Job, *, base: str = "") -> JobModel:
    """A job as the client sees it, with the two URLs it needs next.

    `base` is the server's root path, so the links stay correct when the API
    is mounted under a prefix by a reverse proxy in front of a GUI.
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
        answer=answer_from_state(job.state) if job.state is not None else None,
        error=job.error,
        links=JobLinks(self=href, events=f"{href}/events"),
    )
