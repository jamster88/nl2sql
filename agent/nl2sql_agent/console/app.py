"""The console's HTTP surface.

Five routes that matter: what the console is connected to, the schema as the
agent reads it, the block of the agent's prompt for one table, and a query
run through the agent's gates. The rest is the same service furniture as the
API's -- health, readiness, one error shape -- because a client that already
talks to one of these processes should not have to learn a second dialect to
talk to another.

A query the agent's gates refuse is still a 200: "the static validator would
refuse this, and here is why" is the answer being asked for, not a failure
to give one. The errors are for requests that could not be answered at all
-- no token, a body that is not a query, a database that is down.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from .. import __version__
from ..api.app import FALLBACK_CODES
from ..api.tls import CertificateInfo
from ..config import Settings
from ..database import Database
from nl2sql_common.envelope import ApiError
from nl2sql_identity import Guard, GuardSettings
from nl2sql_identity.postgres import membership_lookup
from .query import DatabaseUnavailable, Inspector
from .routes import ConsoleContext, routers
from .settings import ConsoleSettings

def default_guard(agent: Settings, console: ConsoleSettings) -> Guard:
    """Sign-in as the environment configures it, roles re-read from Postgres."""
    return Guard(
        GuardSettings(
            enabled=console.auth_enabled,
            public_key_file=console.auth_public_key_file,
            cookie_name=console.auth_cookie_name,
            service_token=console.token,
            service_roles=console.token_holds(),
            service_name=console.token_name,
        ),
        recheck=membership_lookup(agent.database_url) if console.auth_enabled else None,
    )


def _error_response(status: int, code: str, message: str, **detail: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=ApiError.of(code, message, **detail).model_dump()
    )


def create_app(
    *,
    settings: Settings | None = None,
    console_settings: ConsoleSettings | None = None,
    inspector_factory: Callable[[], Inspector] | None = None,
    certificate: CertificateInfo | None = None,
    guard: Guard | None = None,
) -> FastAPI:
    """The application, with the database behind an injectable seam.

    `inspector_factory` is what lets every route be exercised against a fake
    database on every run; the default builds the agent's own `Database`
    from the agent's own settings.
    """
    agent = settings or Settings.from_env()
    console = console_settings or ConsoleSettings.from_env()
    guard = guard or default_guard(agent, console)

    def default_inspector() -> Inspector:
        db = Database(
            agent.database_url,
            db_schema=agent.db_schema,
            statement_timeout_ms=agent.statement_timeout_ms,
            max_rows=agent.max_rows,
        )
        return Inspector(db, agent, max_rows=console.max_rows)

    # Built once: the engine is a connection pool, and it opens no connection
    # until the first query, so a database still starting costs nothing here.
    inspector = (inspector_factory or default_inspector)()
    started = time.monotonic()

    app = FastAPI(
        title="NL2SQL SQL console",
        version=__version__,
        summary="Query the retail database as the agent does, through the agent's gates.",
        description=(
            "Every query runs as the agent's database role, inside a read-only "
            "transaction and the agent's statement timeout, after the agent's own "
            "static validator and planner gate -- and says what each gate made of it."
        ),
        root_path=console.root_path,
        docs_url="/docs" if console.docs_enabled else None,
        redoc_url="/redoc" if console.docs_enabled else None,
        openapi_url="/openapi.json",
    )
    app.state.settings = agent
    app.state.console_settings = console
    app.state.inspector = inspector

    if console.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(console.cors_origins),
            allow_credentials="*" not in console.cors_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key"],
        )

    # --- error shape ------------------------------------------------------

    # Starlette's HTTPException rather than FastAPI's subclass of it: a path
    # no route matches is raised as the former, and would otherwise answer
    # in Starlette's own shape rather than the one every client here parses.
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
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

    @app.exception_handler(DatabaseUnavailable)
    async def _unavailable(request: Request, exc: DatabaseUnavailable) -> JSONResponse:
        return _error_response(
            HTTP_503_SERVICE_UNAVAILABLE,
            "database_unavailable",
            f"cannot reach the retail database: {exc}",
        )

    # --- the routes (routes.py), each router carrying its guard -------------

    context = ConsoleContext(
        agent=agent,
        console=console,
        inspector=inspector,
        guard=guard,
        certificate=certificate,
        started=started,
    )
    app.state.context = context
    for router in routers(context):
        app.include_router(router)

    return app
