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

Who may call is decided by `nl2sql_identity.Guard`. With sign-in on
(AUTH_ENABLED) a person's session -- the cookie their GUI sends, or the
bearer token the desktop client holds -- is what every /v1 route needs; their
question runs as their own database role, and the questions they see are
theirs. The static API_TOKEN still works, for machines with no person to sign
in. With sign-in off it is the token when one is configured and nothing when
it is not, as it always was.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from .. import __version__
from ..config import Settings
from ..graph import Nl2SqlAgent
from ..llm import LlmUnavailableError
from ..tracing import Tracer
from nl2sql_common.envelope import FALLBACK_CODES as SHARED_FALLBACK_CODES
from nl2sql_common.envelope import Check
from nl2sql_common.errors import DATABASE_ERRORS
from nl2sql_identity import Guard, GuardSettings
from nl2sql_identity.postgres import membership_lookup
from .feedback import FeedbackSink, build_sink
from .jobs import JobStore
from .routes import ApiContext, _error_response, routers
from .settings import ApiSettings
from .tls import CertificateInfo

#: Status codes a client can be given without a code of our own. Anything
#: raised deliberately below carries a specific one; this is the fallback so
#: every error body has the same shape whatever produced it.
#: The codes an error gets when the route raising it gave none: the shared
#: set, which every nl2sql API answers with.
FALLBACK_CODES = dict(SHARED_FALLBACK_CODES)


def default_guard(settings: Settings, api: ApiSettings) -> Guard:
    """Sign-in as the environment configures it, with roles re-read from Postgres.

    The re-read uses the agent's own connection: any role can ask
    `pg_has_role`, and the reader is a role.
    """
    return Guard(
        GuardSettings(
            enabled=api.auth_enabled,
            public_key_file=api.auth_public_key_file,
            cookie_name=api.auth_cookie_name,
            service_token=api.token,
            service_roles=frozenset(api.token_roles),
            service_name=api.token_name,
        ),
        recheck=membership_lookup(settings.database_url) if api.auth_enabled else None,
    )


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
                except Exception as exc:  # noqa: BLE001 - recorded for /readyz, then raised
                    self._error = f"{type(exc).__name__}: {exc}"
                    raise
            return self._agent

    def reload(self) -> list[str]:
        """What the agent read once, read again -- if it has read anything yet."""
        with self._lock:
            agent = self._agent
        return agent.reload() if agent is not None else []

    def tables(self) -> list[str]:
        """Table names, best effort: `/v1/meta` is useful without them."""
        try:
            return sorted(self.get().db.table_names())
        except Exception:  # noqa: BLE001 - best effort; /readyz reports why
            return []

    def routing(self) -> dict[str, Any]:
        """The routing table, best effort, as the table names are."""
        try:
            return self.get().router.table.describe()
        except Exception:  # noqa: BLE001 - best effort; /readyz reports why
            return {}

    def checks(self) -> dict[str, Check]:
        """What `/readyz` reports, in the order things fail in practice."""
        try:
            agent = self.get()
        except Exception as exc:  # noqa: BLE001 - /readyz reports whatever stopped the agent
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
        except DATABASE_ERRORS as exc:
            results["database"] = Check(ok=False, detail=f"{type(exc).__name__}: {exc}")
        results["agent"] = Check(ok=True, detail=f"nl2sql-agent {__version__}")
        return results


def create_app(
    *,
    settings: Settings | None = None,
    api_settings: ApiSettings | None = None,
    agent_factory: Callable[[], Nl2SqlAgent] | None = None,
    store: JobStore | None = None,
    certificate: CertificateInfo | None = None,
    feedback: FeedbackSink | None = None,
    tracer: Tracer | None = None,
    guard: Guard | None = None,
) -> FastAPI:
    """The application, with every collaborator injectable.

    The injection is not ceremony: it is what lets the whole HTTP surface be
    tested against a fake pipeline, with no Ollama, no Postgres and no
    container, which is the only way these routes get exercised on every run.
    """
    settings = settings or Settings.from_env()
    api = api_settings or ApiSettings.from_env()
    sink = feedback if feedback is not None else build_sink(api.feedback_db_url)
    guard = guard or default_guard(settings, api)
    # One connection to MLflow for the server: the agent traces its runs on
    # it, and the feedback routes put verdicts on those traces.
    tracer = tracer or Tracer(settings)
    holder = AgentHolder(agent_factory or (lambda: Nl2SqlAgent(settings, tracer=tracer)))

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
        max_queued=api.max_queued,
        max_per_person=api.max_per_person,
        queue_ttl_seconds=api.queue_ttl_seconds,
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
    app.state.tracer = tracer
    app.state.guard = guard

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

    # --- the routes (routes.py), each router carrying its guard -------------

    context = ApiContext(
        settings=settings,
        api=api,
        holder=holder,
        jobs=jobs,
        sink=sink,
        tracer=tracer,
        guard=guard,
        certificate=certificate,
        started=started,
    )
    app.state.context = context
    for router in routers(context):
        app.include_router(router)

    return app
