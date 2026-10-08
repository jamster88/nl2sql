"""The API's routes, by router, each refused by default (V6-26).

Until 6.2 every route was a closure inside `create_app`, and authorization
was a dependency each one added by hand: a route written without it was open
to anyone. Now a route belongs to a router, and the router carries the
guard -- `asker` for the questions, `streamer` for the one stream a static
token may open from the query string, `administrator` for the reload -- so a
route added to one is guarded before anyone thinks to guard it. The routes
open by design are on the one router that says so, `public_routes`.

The routes read what they need from an `ApiContext`, built once by
`create_app`, rather than from its local variables: what a route can reach
is written down in one place.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader, HTTPBearer
from starlette.status import (
    HTTP_202_ACCEPTED,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from .. import __version__
from ..config import Settings
from .. import ensemble
from ..graph import STEP_LABELS
from ..supervisor import INTENT_FRAMING, describe_scope
from ..tracing import Tracer
from nl2sql_common.envelope import ApiError, Check, Health, Readiness
from nl2sql_identity import ADMINS, USERS, Guard, Identity
from .feedback import AlreadyReviewed, Capture, FeedbackSink, FeedbackUnavailable
from .jobs import Job, JobStore, QueueFull, StreamChunk
from .models import (
    MAX_METADATA_ENTRIES,
    MAX_QUESTION_LENGTH,
    AskRequest,
    EnsembleSettings,
    FeedbackModel,
    FeedbackRequest,
    Job as JobModel,
    JobList,
    Limits,
    Meta,
    Pipeline,
    Reloaded,
)
from .settings import ApiSettings
from .tls import CertificateInfo
from .translate import answer_from_state, job_model, progress_event


class ApiHTTPError(HTTPException):
    """An HTTPException that also carries the machine-readable code.

    The message is for a person reading a log; `code` is what a GUI branches
    on, and it is stable across rewordings of the message.
    """

    def __init__(self, status_code: int, code: str, detail: str, **headers: str) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers or None)
        self.code = code


_bearer = HTTPBearer(
    auto_error=False,
    description="A session token from the auth service (POST /auth/token), or the API token.",
)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="The API token, by another name.")

#: On every router that needs a caller, so the OpenAPI document says how to
#: authenticate. They decide nothing: `Guard` does.
DOCUMENTED = [Depends(_bearer), Depends(_api_key)]


def _error_response(status: int, code: str, message: str, **detail: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=ApiError.of(code, message, **detail).model_dump()
    )


def _sse(chunk: StreamChunk, *, base: str, detail: bool = True) -> str:
    """One Server-Sent Event.

    SSE rather than a WebSocket because progress only ever flows one way and
    every environment already speaks it: a browser has `EventSource` built
    in, and everything else reads a chunked response line by line. A
    WebSocket would need a library in each of them.
    """
    if chunk.kind == "keepalive":
        return ": keep-alive\n\n"
    if chunk.kind == "progress" and chunk.event is not None:
        payload = progress_event(chunk.event).model_dump(mode="json")
        return f"id: {chunk.event.seq}\nevent: progress\ndata: {json.dumps(payload)}\n\n"
    if chunk.kind == "status":
        return f"event: status\ndata: {json.dumps({'status': chunk.status})}\n\n"
    if chunk.kind == "done" and chunk.job is not None:
        payload = job_model(chunk.job, base=base, detail=detail).model_dump(mode="json")
        return f"event: done\ndata: {json.dumps(payload)}\n\n"
    return (
        "event: timeout\ndata: "
        + json.dumps(
            {
                "message": "the stream was idle too long; reconnect with "
                "Last-Event-ID to resume"
            }
        )
        + "\n\n"
    )


@dataclass
class ApiContext:
    """Everything a route may reach, and the three guards the routers carry."""

    settings: Settings
    api: ApiSettings
    holder: Any
    jobs: JobStore
    sink: FeedbackSink
    tracer: Tracer
    guard: Guard
    certificate: CertificateInfo | None
    started: float

    def __post_init__(self) -> None:
        self.asker: Callable[[Request], Identity] = self.guard.require(USERS)
        # The one route whose static token may come in the query string,
        # because `EventSource` cannot set headers. A signed-in browser sends
        # its cookie.
        self.streamer: Callable[[Request], Identity] = self.guard.require(USERS, query_token=True)
        self.administrator: Callable[[Request], Identity] = self.guard.require(ADMINS)

    def sees_detail(self, identity: Identity) -> bool:
        """An operator sees a failure in its own words; anyone else, which
        part failed (V6-32). `API_DEBUG_DETAIL` shows everyone, for a
        development server."""
        return self.api.debug_detail or identity.has_any({ADMINS})

    def require_job(self, job_id: str, identity: Identity) -> Job:
        """The job, if it exists and is this caller's to see.

        Someone else's is a 404, not a 403: whether a job id exists is
        itself something only its owner should learn.
        """
        job = self.jobs.get(job_id)
        if job is None or (identity.principal is not None and job.owner != identity.principal):
            raise ApiHTTPError(
                HTTP_404_NOT_FOUND,
                "not_found",
                f"no job {job_id}. Finished jobs are kept for "
                f"{self.api.job_ttl_seconds} seconds.",
            )
        return job

    def capture_from(self, job: Job, body: FeedbackRequest) -> Capture:
        """Build the staged snapshot out of the job the server still holds.

        Everything but the verdict and the comment comes from here rather
        than from the request, so a submission can never describe an answer
        this server did not give.

        The snapshot is taken through `answer_from_state`, which is the same
        translation the job's own document goes through. Reading `job.state`
        directly here would be a second translation to keep in step with the
        first -- and the one that handles a `Decimal` out of Postgres, a
        refusal with no result, and a chart that is a dataclass in one code
        path and a dict in another is the one that already exists.
        """
        answer = answer_from_state(job.state)
        table = answer.result
        return Capture(
            job_id=job.id,
            verdict=body.verdict,
            question=job.question,
            sql_code=answer.sql,
            answer=answer.answer,
            narrative=answer.narrative,
            intent=answer.intent,
            tables=", ".join(answer.tables),
            row_count=table.row_count if table else 0,
            columns=tuple(table.columns) if table else (),
            comment=body.comment,
            agent_version=__version__,
        )


def public_routes(ctx: ApiContext) -> APIRouter:
    """What this is, its health and its readiness: open by design, and the
    only router that is."""
    router = APIRouter(tags=["service"])

    @router.get("/", summary="What this is and where to go next")
    def root() -> dict[str, Any]:
        return {
            "service": "nl2sql-agent",
            "version": __version__,
            "docs": "/docs" if ctx.api.docs_enabled else None,
            "openapi": "/openapi.json",
            "endpoints": {
                "meta": "/v1/meta",
                "ask": "POST /v1/questions",
                "job": "/v1/questions/{id}",
                "events": "/v1/questions/{id}/events",
                "health": "/healthz",
                "readiness": "/readyz",
            },
        }

    @router.get("/healthz", response_model=Health, summary="Is the process alive")
    def healthz() -> Health:
        return Health(version=__version__, uptime_seconds=round(time.monotonic() - ctx.started, 3))

    @router.get(
        "/readyz",
        response_model=Readiness,
        summary="Can it answer a question right now",
        responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
    )
    def readyz(request: Request, response: Response) -> Readiness:
        checks = ctx.holder.checks()
        # Readiness is decided before feedback is looked at, and feedback is
        # reported after: a server whose staging database is down can still
        # answer questions, which is the job. Failing readiness over it would
        # have an orchestrator restart a working agent because an optional
        # side channel was unavailable.
        ready = all(check.ok for check in checks.values())
        ok, detail = ctx.sink.check()
        checks["feedback"] = Check(ok=ok, detail=detail)
        # Sign-in is reported and counted: with it on and no key to verify a
        # session with, nobody can ask anything.
        signed, detail = ctx.guard.check()
        checks["sign_in"] = Check(ok=signed, detail=detail)
        ready = ready and signed
        if not ready:
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        readiness = Readiness(ready=ready, checks=checks, warnings=ctx.api.warnings())
        return readiness.for_caller(operator=ctx.guard.operator(request, debug=ctx.api.debug_detail))

    return router


def question_routes(ctx: ApiContext) -> APIRouter:
    """Asking, and everything about what was asked: anyone who may ask."""
    router = APIRouter(dependencies=[*DOCUMENTED, Depends(ctx.asker)])
    api, jobs, sink, tracer = ctx.api, ctx.jobs, ctx.sink, ctx.tracer

    @router.get(
        "/v1/meta",
        tags=["service"],
        response_model=Meta,
        summary="Everything a client needs to configure itself",
    )
    def meta() -> Meta:
        settings = ctx.settings
        tables = ctx.holder.tables()
        return Meta(
            version=__version__,
            model=settings.ollama_model,
            intents=sorted(INTENT_FRAMING),
            tables=tables,
            scope=describe_scope(tables),
            limits=Limits(
                max_rows=settings.max_rows,
                max_attempts=settings.max_attempts,
                max_plan_cost=settings.max_plan_cost,
                statement_timeout_ms=settings.statement_timeout_ms,
                max_concurrency=api.max_concurrency,
                max_wait_seconds=api.max_wait_seconds,
                max_question_length=MAX_QUESTION_LENGTH,
                max_metadata_entries=MAX_METADATA_ENTRIES,
            ),
            pipeline=Pipeline(
                supervisor=settings.supervisor_enabled,
                literals=settings.literals_enabled,
                narrate=settings.narrate_enabled,
                audit=settings.audit_enabled,
                schema_retrieval=settings.schema_retrieval,
                # Under the ensemble, its own nodes and then each run's.
                nodes=(list(ensemble.STEP_LABELS) if settings.ensemble_enabled else []) + list(STEP_LABELS),
                ensemble=EnsembleSettings(
                    enabled=settings.ensemble_enabled,
                    paraphrases=settings.ensemble_paraphrases,
                    max_paraphrases=settings.ensemble_max_paraphrases,
                    waves=settings.ensemble_waves,
                    parallel_calls=settings.ollama_parallel_calls,
                    judge=settings.ensemble_judge_enabled,
                    fuse_columns=settings.ensemble_fuse_columns,
                ),
            ),
            tls=(
                ctx.certificate.summary()
                if ctx.certificate is not None
                else {"enabled": api.tls_enabled, "self_signed": False}
            ),
            authentication=ctx.guard.describe(),
            routing=ctx.holder.routing(),
            feedback=sink.check()[0],
        )

    @router.post(
        "/v1/questions",
        tags=["questions"],
        response_model=JobModel,
        status_code=HTTP_202_ACCEPTED,
        summary="Ask a question",
        responses={
            HTTP_400_BAD_REQUEST: {"model": ApiError},
            HTTP_429_TOO_MANY_REQUESTS: {
                "model": ApiError,
                "description": "Too many questions waiting; Retry-After says when to ask again.",
            },
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
    )
    def ask(
        body: AskRequest,
        response: Response,
        identity: Identity = Depends(ctx.asker),
        wait: float | None = Query(
            default=None,
            ge=0,
            description=(
                "Seconds to hold the connection open waiting for the answer. "
                "Omit for the asynchronous flow. The server caps this at "
                "API_MAX_WAIT_SECONDS and returns 202 with the unfinished job "
                "if the time runs out, so a wait is an optimisation, never a "
                "different contract."
            ),
        ),
    ) -> JobModel:
        # A signed-in person's questions run as them, and only as them: a
        # principal they name must be themselves. Choosing one is otherwise
        # the old opt-in, for a trusted caller in front of an open server.
        if identity.principal is not None:
            if body.principal and body.principal != identity.principal:
                raise ApiHTTPError(
                    HTTP_400_BAD_REQUEST,
                    "principal_not_allowed",
                    f"signed in as {identity.principal}, your questions run as {identity.principal}",
                )
            principal: str | None = identity.principal
        elif body.principal and not api.allow_principal:
            raise ApiHTTPError(
                HTTP_400_BAD_REQUEST,
                "principal_not_allowed",
                "this server does not accept a caller-chosen database principal; "
                "start it with API_ALLOW_PRINCIPAL=true to enable it",
            )
        else:
            principal = body.principal if api.allow_principal else None
        try:
            job = jobs.submit(
                body.question,
                principal=principal,
                owner=identity.principal,
                metadata=body.metadata,
            )
        except QueueFull as exc:
            raise ApiHTTPError(
                HTTP_429_TOO_MANY_REQUESTS, "queue_full", str(exc), **{"Retry-After": str(exc.retry_after)}
            ) from exc
        except RuntimeError as exc:
            raise ApiHTTPError(HTTP_503_SERVICE_UNAVAILABLE, "unavailable", str(exc)) from exc

        if wait:
            jobs.wait(job, min(wait, api.max_wait_seconds))
        model = job_model(job, base=api.root_path, detail=ctx.sees_detail(identity))
        response.status_code = 200 if job.terminal else HTTP_202_ACCEPTED
        response.headers["Location"] = model.links.self
        return model

    @router.get(
        "/v1/questions",
        tags=["questions"],
        response_model=JobList,
        summary="Recent questions, newest first -- your own, once you have signed in",
    )
    def list_jobs(
        identity: Identity = Depends(ctx.asker), limit: int = Query(default=50, ge=1, le=500)
    ) -> JobList:
        found = jobs.list(limit=limit, owner=identity.principal)
        return JobList(
            jobs=[job_model(job, base=api.root_path, detail=ctx.sees_detail(identity)) for job in found],
            count=len(found),
        )

    @router.get(
        "/v1/questions/{job_id}",
        tags=["questions"],
        response_model=JobModel,
        summary="One question, running or finished",
        responses={HTTP_404_NOT_FOUND: {"model": ApiError}},
    )
    def get_job(
        job_id: str,
        response: Response,
        identity: Identity = Depends(ctx.asker),
        wait: float | None = Query(
            default=None,
            ge=0,
            description="Seconds to wait for the job to finish before answering.",
        ),
    ) -> JobModel:
        job = ctx.require_job(job_id, identity)
        if wait:
            jobs.wait(job, min(wait, api.max_wait_seconds))
        if not job.terminal:
            # Tells a polling client how long to sleep, so the interval is the
            # server's decision rather than every client's guess.
            response.headers["Retry-After"] = "2"
        return job_model(job, base=api.root_path, detail=ctx.sees_detail(identity))

    @router.post(
        "/v1/questions/{job_id}/feedback",
        tags=["feedback"],
        response_model=FeedbackModel,
        status_code=201,
        summary="Say whether this answer was right",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_409_CONFLICT: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
        description=(
            "Records a verdict in the staging database, where it waits to be "
            "reviewed and possibly promoted into the golden question set. The "
            "job's question, SQL and result shape are captured with it, because "
            "the job itself is forgotten after API_JOB_TTL_SECONDS and a verdict "
            "pointing at a forgotten job is not reviewable.\n\n"
            "Voting again replaces the verdict, until a reviewer has acted on it."
        ),
    )
    def record_feedback(
        job_id: str, body: FeedbackRequest, identity: Identity = Depends(ctx.asker)
    ) -> FeedbackModel:
        job = ctx.require_job(job_id, identity)
        if not job.terminal:
            # There is nothing to have an opinion about yet, and the snapshot
            # taken now would be of a half-finished run -- which is the one
            # thing a golden pair must never be built from.
            raise ApiHTTPError(
                HTTP_409_CONFLICT,
                "job_running",
                f"job {job_id} is {job.status}; wait for it to finish before judging it",
            )
        try:
            submission_id = sink.record(ctx.capture_from(job, body))
        except AlreadyReviewed as exc:
            raise ApiHTTPError(HTTP_409_CONFLICT, "already_reviewed", str(exc)) from exc
        except FeedbackUnavailable as exc:
            raise ApiHTTPError(
                HTTP_503_SERVICE_UNAVAILABLE, "feedback_unavailable", str(exc)
            ) from exc
        # And on the run's trace, when it was traced. After the staging
        # database has it, because that is the record a reviewer acts on;
        # MLflow not taking it is logged and costs the response nothing.
        tracer.record_verdict((job.state or {}).get("trace_id"), body.verdict, comment=body.comment)
        return FeedbackModel(
            id=submission_id, job_id=job.id, verdict=body.verdict, comment=body.comment
        )

    @router.delete(
        "/v1/questions/{job_id}/feedback",
        tags=["feedback"],
        status_code=204,
        summary="Withdraw a verdict",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
        description=(
            "For the misclick. Succeeds only while nobody has reviewed the "
            "verdict; once one has been acted on it is a record of what "
            "happened and stops being the voter's to take back."
        ),
    )
    def withdraw_feedback(job_id: str, identity: Identity = Depends(ctx.asker)) -> Response:
        # Only the asker's own, while the job is still known; after that the
        # job id -- unguessable, and only ever shown to its owner -- is the
        # proof of having asked it.
        known = jobs.get(job_id)
        if known is not None:
            ctx.require_job(job_id, identity)
        try:
            removed = sink.withdraw(job_id)
        except FeedbackUnavailable as exc:
            raise ApiHTTPError(
                HTTP_503_SERVICE_UNAVAILABLE, "feedback_unavailable", str(exc)
            ) from exc
        if not removed:
            raise ApiHTTPError(
                HTTP_404_NOT_FOUND,
                "not_found",
                f"no feedback for job {job_id} that can still be withdrawn",
            )
        # A verdict can outlive its job (API_JOB_TTL_SECONDS), so a job the
        # server has forgotten is found by the id its trace is tagged with.
        trace_id = (known.state or {}).get("trace_id") if known else tracer.find_job_trace(job_id)
        tracer.withdraw_verdict(trace_id)
        return Response(status_code=204)

    @router.delete(
        "/v1/questions/{job_id}",
        tags=["questions"],
        status_code=204,
        summary="Cancel a queued question, or forget a finished one",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_409_CONFLICT: {"model": ApiError},
        },
    )
    def delete_job(job_id: str, identity: Identity = Depends(ctx.asker)) -> Response:
        if jobs.get(job_id) is not None:
            ctx.require_job(job_id, identity)
        outcome = jobs.cancel(job_id)
        if outcome == "missing":
            raise ApiHTTPError(HTTP_404_NOT_FOUND, "not_found", f"no job {job_id}")
        if outcome == "running":
            raise ApiHTTPError(
                HTTP_409_CONFLICT,
                "job_running",
                "this question is already running and cannot be interrupted; "
                "it can be deleted once it finishes",
            )
        return Response(status_code=204)

    return router


def stream_routes(ctx: ApiContext) -> APIRouter:
    """The progress stream: anyone who may ask, the static token from the
    query string as well, because `EventSource` cannot set a header."""
    router = APIRouter(dependencies=[*DOCUMENTED, Depends(ctx.streamer)])
    api, jobs = ctx.api, ctx.jobs

    @router.get(
        "/v1/questions/{job_id}/events",
        tags=["questions"],
        summary="Progress as it happens (Server-Sent Events)",
        response_class=StreamingResponse,
        responses={
            200: {
                "content": {"text/event-stream": {}},
                "description": (
                    "A `progress` event per pipeline node, `status` on each "
                    "transition, and one `done` event carrying the finished job."
                ),
            },
            HTTP_404_NOT_FOUND: {"model": ApiError},
        },
    )
    def job_events(
        job_id: str,
        from_seq: int = Query(
            default=0,
            ge=0,
            description="Resume after this event number. Last-Event-ID wins over it.",
        ),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        identity: Identity = Depends(ctx.streamer),
    ) -> StreamingResponse:
        job = ctx.require_job(job_id, identity)
        if last_event_id and last_event_id.isdigit():
            from_seq = int(last_event_id)

        def body() -> Iterator[str]:
            for chunk in jobs.stream(
                job,
                from_seq=from_seq,
                timeout=api.event_stream_timeout_seconds,
                keepalive=api.keepalive_seconds,
            ):
                yield _sse(chunk, base=api.root_path, detail=ctx.sees_detail(identity))

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                # nginx buffers proxied responses by default, which turns a
                # live progress stream into one delivery at the end.
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return router


def admin_routes(ctx: ApiContext) -> APIRouter:
    """What only an administrator may ask for."""
    router = APIRouter(dependencies=[*DOCUMENTED, Depends(ctx.administrator)])

    @router.post(
        "/v1/admin/reload",
        tags=["service"],
        response_model=Reloaded,
        summary="Read again what the agent read once: the catalog, the calendar, the foreign keys, the collections",
        responses={HTTP_403_FORBIDDEN: {"model": ApiError}},
    )
    def reload() -> Reloaded:
        """For an operator who changed the retail data or loaded a knowledge
        document (V6-33). A promotion needs none: the pairs, snippets and
        fixes it writes are read from their stores on every question. The
        guard's answers about sessions go too, so a revocation is felt here
        at once rather than within the minute."""
        return Reloaded(reloaded=ctx.holder.reload(), sessions_forgotten=ctx.guard.forget())

    return router


def routers(ctx: ApiContext) -> list[APIRouter]:
    """Every router, the open one first."""
    return [public_routes(ctx), question_routes(ctx), stream_routes(ctx), admin_routes(ctx)]
