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

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_404_NOT_FOUND,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from .. import __version__
from ..api.app import FALLBACK_CODES, ApiHTTPError
from ..api.tls import CertificateInfo
from ..config import Settings
from ..database import Database
from .models import (
    AgentVerdict,
    ApiError,
    Check,
    ColumnModel,
    ConsoleLimits,
    ConsoleMeta,
    Health,
    IssueModel,
    PromptModel,
    QueryRequest,
    QueryResult,
    Readiness,
    ResultColumn,
    SchemaModel,
    TableModel,
)
from .query import DatabaseUnavailable, Identity, Inspector, Outcome
from .settings import MAX_SQL_LENGTH, ConsoleSettings

_bearer = HTTPBearer(auto_error=False, description="Console token, when one is configured.")
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="Alternative to the bearer token.")


def _error_response(status: int, code: str, message: str, **detail: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=ApiError.of(code, message, **detail).model_dump()
    )


def role_warning(identity: Identity) -> str | None:
    """Said once, wherever the role is shown, when it is not the reader."""
    if identity.read_only:
        return None
    power = "a superuser" if identity.superuser else "able to write to the retail tables"
    return (
        f"DATABASE_URL connects as {identity.role}, which is {power}. Every query is "
        "still run READ ONLY and through the agent's validator, but the console runs "
        "SQL a person typed and should run it as the agent's read-only role."
    )


def result_model(outcome: Outcome, *, max_rows: int, max_plan_cost: float) -> QueryResult:
    verdict = outcome.verdict
    return QueryResult(
        sql=outcome.sql,
        mode=outcome.mode,
        executed=outcome.executed,
        agent=AgentVerdict(
            accepted=verdict.accepted,
            stage=verdict.stage,
            issues=[IssueModel(stage=i.stage, message=i.message) for i in verdict.issues],
            notes=list(verdict.notes),
        ),
        columns=[ResultColumn(name=name, data_type=kind) for name, kind in outcome.columns],
        rows=outcome.rows,
        row_count=outcome.row_count,
        truncated=outcome.truncated,
        max_rows=max_rows,
        plan=outcome.plan,
        plan_cost=outcome.plan_cost,
        max_plan_cost=max_plan_cost,
        error=outcome.error,
        elapsed_ms=outcome.elapsed_ms,
    )


def create_app(
    *,
    settings: Settings | None = None,
    console_settings: ConsoleSettings | None = None,
    inspector_factory: Callable[[], Inspector] | None = None,
    certificate: CertificateInfo | None = None,
) -> FastAPI:
    """The application, with the database behind an injectable seam.

    `inspector_factory` is what lets every route be exercised against a fake
    database on every run; the default builds the agent's own `Database`
    from the agent's own settings.
    """
    agent = settings or Settings.from_env()
    console = console_settings or ConsoleSettings.from_env()

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

    # --- authentication ---------------------------------------------------

    def authenticate(bearer=Depends(_bearer), api_key: str | None = Depends(_api_key)) -> None:
        if not console.token:
            return
        presented = (bearer.credentials if bearer else None) or api_key
        if presented != console.token:
            raise ApiHTTPError(
                HTTP_401_UNAUTHORIZED,
                "unauthorized",
                "a valid console token is required",
                **{"WWW-Authenticate": "Bearer"},
            )

    guarded = [Depends(authenticate)]

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

    # --- the unauthenticated routes ---------------------------------------

    @app.get("/", tags=["service"], summary="What this is and where to go next")
    def root() -> dict[str, Any]:
        return {
            "service": "nl2sql-console",
            "version": __version__,
            "docs": "/docs" if console.docs_enabled else None,
            "openapi": "/openapi.json",
            "endpoints": {
                "meta": "/v1/meta",
                "schema": "/v1/schema",
                "prompt": "/v1/schema/{table}/prompt",
                "query": "POST /v1/query",
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
        summary="Can it run a query right now",
        responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
    )
    def readyz(response: Response) -> Readiness:
        warnings = console.warnings()
        try:
            names = inspector.tables()
            identity = inspector.identity()
        except DatabaseUnavailable as exc:
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
            return Readiness(
                ready=False,
                checks={
                    "database": Check(ok=False, detail=str(exc)),
                    "role": Check(ok=False, detail="not checked: the database is unreachable"),
                },
                warnings=warnings,
            )
        # Readiness is the database answering. A role that could write is
        # reported beside it rather than failing it: the fence holds either
        # way, and an orchestrator restarting a working console over it
        # would fix nothing.
        warning = role_warning(identity)
        checks = {
            "database": Check(
                ok=bool(names),
                detail=f"{len(names)} tables in {agent.db_schema}"
                if names
                else f"connected, but {agent.db_schema} has no tables",
            ),
            "role": Check(
                ok=warning is None,
                detail=f"{identity.role}, read-only" if warning is None else warning,
            ),
        }
        ready = checks["database"].ok
        if not ready:
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(ready=ready, checks=checks, warnings=warnings)

    # --- the console ------------------------------------------------------

    @app.get(
        "/v1/meta",
        tags=["console"],
        response_model=ConsoleMeta,
        dependencies=guarded,
        summary="What the console is connected to, and the limits every query runs under",
    )
    def meta() -> ConsoleMeta:
        identity = inspector.identity()
        tables = inspector.tables()
        warning = role_warning(identity)
        return ConsoleMeta(
            version=__version__,
            database=identity.database,
            role=identity.role,
            server_version=identity.server_version,
            read_only=identity.read_only,
            db_schema=agent.db_schema,
            tables=len(tables),
            limits=ConsoleLimits(
                statement_timeout_ms=agent.statement_timeout_ms,
                max_plan_cost=agent.max_plan_cost,
                agent_max_rows=agent.max_rows,
                max_rows=console.max_rows,
                sample_rows=agent.sample_rows,
                max_sql_length=MAX_SQL_LENGTH,
            ),
            authentication="bearer" if console.token else "none",
            tls=(
                certificate.summary()
                if certificate is not None
                else {"enabled": console.tls_enabled, "self_signed": False}
            ),
            warnings=console.warnings() + ([warning] if warning else []),
        )

    @app.get(
        "/v1/schema",
        tags=["console"],
        response_model=SchemaModel,
        dependencies=guarded,
        summary="Every table, as the agent's introspection reads it",
    )
    def schema() -> SchemaModel:
        return SchemaModel(
            db_schema=agent.db_schema,
            tables=[
                TableModel(
                    name=table.name,
                    comment=table.comment,
                    approx_rows=table.approx_rows,
                    columns=[
                        ColumnModel(
                            name=column.name,
                            data_type=column.data_type,
                            not_null=column.not_null,
                            comment=column.comment,
                        )
                        for column in table.columns
                    ],
                    constraints=list(table.constraints),
                )
                for table in inspector.catalog()
            ],
        )

    @app.get(
        "/v1/schema/{table}/prompt",
        tags=["console"],
        response_model=PromptModel,
        dependencies=guarded,
        summary="The block of the agent's prompt that describes one table",
    )
    def prompt(table: str) -> PromptModel:
        text = inspector.prompt(table)
        if not text:
            raise ApiHTTPError(
                HTTP_404_NOT_FOUND,
                "unknown_table",
                f"{table} is not a table in {agent.db_schema}",
            )
        return PromptModel(table=table, sample_rows=agent.sample_rows, text=text)

    @app.post(
        "/v1/query",
        tags=["console"],
        response_model=QueryResult,
        dependencies=guarded,
        summary="Run a query through the agent's gates",
    )
    def query(body: QueryRequest) -> QueryResult:
        outcome = inspector.run(body.sql, body.mode)
        return result_model(outcome, max_rows=console.max_rows, max_plan_cost=agent.max_plan_cost)

    return app
