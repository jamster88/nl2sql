"""The HTTP surface.

Built to be consumed by something this repository does not contain. That is
the whole design constraint, and it produces three rules:

* **No client library.** Everything is JSON over HTTP with an OpenAPI
  document at `/openapi.json`, so a TypeScript GUI, a Django view, a Spring
  service or `curl` all reach it the same way -- and the first two can
  generate their client from the document rather than hand-writing one.
* **A question is a resource, not a request.** Answering takes about a
  minute. `POST /v1/questions` returns a job immediately; the client polls
  it, streams its progress, or asks the server to hold the connection with
  `?wait=`. All three are the same document at the same URL.
* **Progress is real.** The event stream carries the pipeline's own nodes,
  so a GUI shows what the agent is actually doing rather than a spinner.

Authentication is a bearer token when one is configured and nothing when it
is not, because the common deployment is a private network and a token that
must be invented before anything works is a token that gets committed to a
repository.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader, HTTPBearer
from starlette.middleware.cors import CORSMiddleware
from starlette.status import (
    HTTP_202_ACCEPTED,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from .. import __version__
from ..config import Settings
from ..graph import STEP_LABELS, Nl2SqlAgent
from ..llm import LlmUnavailableError
from ..supervisor import INTENT_FRAMING, describe_scope
from .jobs import Job, JobStore, StreamChunk
from .feedback import AlreadyReviewed, Capture, FeedbackSink, FeedbackUnavailable, build_sink
from .models import (
    ApiError,
    AskRequest,
    Check,
    FeedbackModel,
    FeedbackRequest,
    Health,
    Job as JobModel,
    JobList,
    Limits,
    Meta,
    Pipeline,
    Readiness,
)
from .settings import ApiSettings
from .tls import CertificateInfo
from .translate import answer_from_state, job_model, progress_event

#: Status codes a client can be given without a code of our own. Anything
#: raised deliberately below carries a specific one; this is the fallback so
#: every error body has the same shape whatever produced it.
FALLBACK_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid_request",
    500: "internal_error",
    503: "unavailable",
}


class ApiHTTPError(HTTPException):
    """An HTTPException that also carries the machine-readable code.

    The message is for a person reading a log; `code` is what a GUI branches
    on, and it is stable across rewordings of the message.
    """

    def __init__(self, status_code: int, code: str, detail: str, **headers: str) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers or None)
        self.code = code


_bearer = HTTPBearer(auto_error=False, description="API token, when one is configured.")
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="Alternative to the bearer token.")


class AgentHolder:
    """The pipeline, built once, on the first request that needs it.

    Not at import and not at startup: constructing it opens a database
    connection and asks Ollama whether it has the model, and a container
    that cannot start because a dependency is still booting is a container
    that never recovers. So the failure is per-request and retried, and
    `/readyz` is where an orchestrator goes to find out.
    """

    def __init__(self, factory: Callable[[], Nl2SqlAgent]) -> None:
        self._factory = factory
        self._agent: Nl2SqlAgent | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def get(self) -> Nl2SqlAgent:
        with self._lock:
            if self._agent is None:
                try:
                    self._agent = self._factory()
                    self._error = None
                except LlmUnavailableError as exc:
                    self._error = str(exc)
                    raise
                except Exception as exc:  # a database that is not up yet
                    self._error = f"{type(exc).__name__}: {exc}"
                    raise
            return self._agent

    def tables(self) -> list[str]:
        """Table names, best effort: `/v1/meta` is useful without them."""
        try:
            return sorted(self.get().db.table_names())
        except Exception:
            return []

    def checks(self) -> dict[str, Check]:
        """What `/readyz` reports, in the order things fail in practice."""
        try:
            agent = self.get()
        except Exception as exc:
            return {
                "agent": Check(ok=False, detail=f"{type(exc).__name__}: {exc}"),
                "database": Check(ok=False, detail="not checked: the agent did not start"),
                "llm": Check(ok=False, detail="not checked: the agent did not start"),
            }

        results = {
            # Construction validates the model against the Ollama host, so an
            # agent that exists is an agent whose model answered.
            "llm": Check(ok=True, detail=f"{agent.settings.ollama_model} at {agent.settings.ollama_base_url}"),
        }
        try:
            names = agent.db.table_names()
            results["database"] = Check(
                ok=bool(names),
                detail=f"{len(names)} tables" if names else "connected, but the schema is empty",
            )
        except Exception as exc:
            results["database"] = Check(ok=False, detail=f"{type(exc).__name__}: {exc}")
        results["agent"] = Check(ok=True, detail=f"nl2sql-agent {__version__}")
        return results


def _error_response(status: int, code: str, message: str, **detail: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=ApiError.of(code, message, **detail).model_dump()
    )


def _sse(chunk: StreamChunk, *, base: str) -> str:
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
        payload = job_model(chunk.job, base=base).model_dump(mode="json")
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


def create_app(
    *,
    settings: Settings | None = None,
    api_settings: ApiSettings | None = None,
    agent_factory: Callable[[], Nl2SqlAgent] | None = None,
    store: JobStore | None = None,
    certificate: CertificateInfo | None = None,
    feedback: FeedbackSink | None = None,
) -> FastAPI:
    """The application, with every collaborator injectable.

    The injection is not ceremony: it is what lets the whole HTTP surface be
    tested against a fake pipeline, with no Ollama, no Postgres and no
    container, which is the only way these routes get exercised on every run.
    """
    settings = settings or Settings.from_env()
    api = api_settings or ApiSettings.from_env()
    sink = feedback if feedback is not None else build_sink(api.feedback_db_url)
    holder = AgentHolder(agent_factory or (lambda: Nl2SqlAgent(settings)))

    def default_runner(question: str, principal: str | None, on_progress) -> dict:
        # The callback goes to `run`, not to the agent: one agent answers
        # several questions at once here, and an instance-level callback
        # would put one caller's progress on another caller's stream.
        return holder.get().run(question, principal=principal, on_progress=on_progress)

    jobs = store or JobStore(
        default_runner,
        max_concurrency=api.max_concurrency,
        max_jobs=api.max_jobs,
        ttl_seconds=api.job_ttl_seconds,
    )
    started = time.monotonic()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        jobs.shutdown()

    app = FastAPI(
        title="NL2SQL agent",
        version=__version__,
        summary="Ask a natural-language question about the retail database.",
        description=(
            "A question is a resource. POST one, then poll it, stream its "
            "progress, or ask the server to hold the connection with `?wait=`. "
            "Every response is JSON and the schema below is generated from the "
            "server, so a client can be generated rather than written."
        ),
        root_path=api.root_path,
        docs_url="/docs" if api.docs_enabled else None,
        redoc_url="/redoc" if api.docs_enabled else None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.api_settings = api
    app.state.jobs = jobs
    app.state.agent = holder
    app.state.certificate = certificate
    app.state.feedback = sink

    if api.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(api.cors_origins),
            # Credentials and "*" are a combination browsers reject outright,
            # so the flag follows the configuration rather than being set to
            # a value that would silently break every request.
            allow_credentials="*" not in api.cors_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", "Last-Event-ID"],
            expose_headers=["Location", "Retry-After"],
        )

    # --- authentication ---------------------------------------------------

    def authenticate(
        request: Request,
        bearer=Depends(_bearer),
        api_key: str | None = Depends(_api_key),
        access_token: str | None = Query(
            default=None,
            description=(
                "The token, for clients that cannot set headers. Browsers' "
                "EventSource is the reason this exists; prefer the header "
                "everywhere else."
            ),
        ),
    ) -> None:
        if not api.token:
            return
        presented = (bearer.credentials if bearer else None) or api_key or access_token
        if presented != api.token:
            raise ApiHTTPError(
                HTTP_401_UNAUTHORIZED,
                "unauthorized",
                "a valid API token is required",
                **{"WWW-Authenticate": "Bearer"},
            )

    guarded = [Depends(authenticate)]

    # --- error shape ------------------------------------------------------

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = getattr(exc, "code", None) or FALLBACK_CODES.get(exc.status_code, "error")
        response = _error_response(exc.status_code, code, str(exc.detail))
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            422,
            "invalid_request",
            "the request body or query string is not valid",
            errors=json.loads(json.dumps(exc.errors(), default=str)),
        )

    # --- the unauthenticated routes ---------------------------------------

    @app.get("/", tags=["service"], summary="What this is and where to go next")
    def root() -> dict[str, Any]:
        return {
            "service": "nl2sql-agent",
            "version": __version__,
            "docs": "/docs" if api.docs_enabled else None,
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

    @app.get("/healthz", tags=["service"], response_model=Health, summary="Is the process alive")
    def healthz() -> Health:
        return Health(version=__version__, uptime_seconds=round(time.monotonic() - started, 3))

    @app.get(
        "/readyz",
        tags=["service"],
        response_model=Readiness,
        summary="Can it answer a question right now",
        responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
    )
    def readyz(response: Response) -> Readiness:
        checks = holder.checks()
        # Readiness is decided before feedback is looked at, and feedback is
        # reported after: a server whose staging database is down can still
        # answer questions, which is the job. Failing readiness over it would
        # have an orchestrator restart a working agent because an optional
        # side channel was unavailable.
        ready = all(check.ok for check in checks.values())
        ok, detail = sink.check()
        checks["feedback"] = Check(ok=ok, detail=detail)
        if not ready:
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(ready=ready, checks=checks, warnings=api.warnings())

    # --- the API ----------------------------------------------------------

    @app.get(
        "/v1/meta",
        tags=["service"],
        response_model=Meta,
        dependencies=guarded,
        summary="Everything a client needs to configure itself",
    )
    def meta() -> Meta:
        tables = holder.tables()
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
            ),
            pipeline=Pipeline(
                supervisor=settings.supervisor_enabled,
                literals=settings.literals_enabled,
                narrate=settings.narrate_enabled,
                audit=settings.audit_enabled,
                schema_retrieval=settings.schema_retrieval,
                nodes=list(STEP_LABELS),
            ),
            tls=(
                certificate.summary()
                if certificate is not None
                else {"enabled": api.tls_enabled, "self_signed": False}
            ),
            authentication="bearer" if api.token else "none",
            feedback=sink.check()[0],
        )

    @app.post(
        "/v1/questions",
        tags=["questions"],
        response_model=JobModel,
        status_code=HTTP_202_ACCEPTED,
        dependencies=guarded,
        summary="Ask a question",
        responses={
            HTTP_400_BAD_REQUEST: {"model": ApiError},
            HTTP_503_SERVICE_UNAVAILABLE: {"model": ApiError},
        },
    )
    def ask(
        body: AskRequest,
        response: Response,
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
        if body.principal and not api.allow_principal:
            raise ApiHTTPError(
                HTTP_400_BAD_REQUEST,
                "principal_not_allowed",
                "this server does not accept a caller-chosen database principal; "
                "start it with API_ALLOW_PRINCIPAL=true to enable it",
            )
        try:
            job = jobs.submit(
                body.question,
                principal=body.principal if api.allow_principal else None,
                metadata=body.metadata,
            )
        except RuntimeError as exc:
            raise ApiHTTPError(HTTP_503_SERVICE_UNAVAILABLE, "unavailable", str(exc)) from exc

        if wait:
            jobs.wait(job, min(wait, api.max_wait_seconds))
        model = job_model(job, base=api.root_path)
        response.status_code = 200 if job.terminal else HTTP_202_ACCEPTED
        response.headers["Location"] = model.links.self
        return model

    @app.get(
        "/v1/questions",
        tags=["questions"],
        response_model=JobList,
        dependencies=guarded,
        summary="Recent questions, newest first",
    )
    def list_jobs(limit: int = Query(default=50, ge=1, le=500)) -> JobList:
        found = jobs.list(limit=limit)
        return JobList(
            jobs=[job_model(job, base=api.root_path) for job in found], count=len(found)
        )

    def _require(job_id: str) -> Job:
        job = jobs.get(job_id)
        if job is None:
            raise ApiHTTPError(
                HTTP_404_NOT_FOUND,
                "not_found",
                f"no job {job_id}. Finished jobs are kept for "
                f"{api.job_ttl_seconds} seconds.",
            )
        return job

    @app.get(
        "/v1/questions/{job_id}",
        tags=["questions"],
        response_model=JobModel,
        dependencies=guarded,
        summary="One question, running or finished",
        responses={HTTP_404_NOT_FOUND: {"model": ApiError}},
    )
    def get_job(
        job_id: str,
        response: Response,
        wait: float | None = Query(
            default=None,
            ge=0,
            description="Seconds to wait for the job to finish before answering.",
        ),
    ) -> JobModel:
        job = _require(job_id)
        if wait:
            jobs.wait(job, min(wait, api.max_wait_seconds))
        if not job.terminal:
            # Tells a polling client how long to sleep, so the interval is the
            # server's decision rather than every client's guess.
            response.headers["Retry-After"] = "2"
        return job_model(job, base=api.root_path)

    @app.get(
        "/v1/questions/{job_id}/events",
        tags=["questions"],
        dependencies=guarded,
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
    ) -> StreamingResponse:
        job = _require(job_id)
        if last_event_id and last_event_id.isdigit():
            from_seq = int(last_event_id)

        def body() -> Iterator[str]:
            for chunk in jobs.stream(
                job,
                from_seq=from_seq,
                timeout=api.event_stream_timeout_seconds,
                keepalive=api.keepalive_seconds,
            ):
                yield _sse(chunk, base=api.root_path)

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

    # --- feedback ---------------------------------------------------------

    def _capture_from(job: Job, body: FeedbackRequest) -> Capture:
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

    @app.post(
        "/v1/questions/{job_id}/feedback",
        tags=["feedback"],
        response_model=FeedbackModel,
        status_code=201,
        dependencies=guarded,
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
    def record_feedback(job_id: str, body: FeedbackRequest) -> FeedbackModel:
        job = _require(job_id)
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
            submission_id = sink.record(_capture_from(job, body))
        except AlreadyReviewed as exc:
            raise ApiHTTPError(HTTP_409_CONFLICT, "already_reviewed", str(exc)) from exc
        except FeedbackUnavailable as exc:
            raise ApiHTTPError(
                HTTP_503_SERVICE_UNAVAILABLE, "feedback_unavailable", str(exc)
            ) from exc
        return FeedbackModel(
            id=submission_id, job_id=job.id, verdict=body.verdict, comment=body.comment
        )

    @app.delete(
        "/v1/questions/{job_id}/feedback",
        tags=["feedback"],
        status_code=204,
        dependencies=guarded,
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
    def withdraw_feedback(job_id: str) -> Response:
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
        return Response(status_code=204)

    @app.delete(
        "/v1/questions/{job_id}",
        tags=["questions"],
        status_code=204,
        dependencies=guarded,
        summary="Cancel a queued question, or forget a finished one",
        responses={
            HTTP_404_NOT_FOUND: {"model": ApiError},
            HTTP_409_CONFLICT: {"model": ApiError},
        },
    )
    def delete_job(job_id: str) -> Response:
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

    return app
