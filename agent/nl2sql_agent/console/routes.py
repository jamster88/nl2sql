"""The console's routes, by router, each refused by default (V6-26).

As the API's (`api/routes.py`): the routes open by design -- what this is,
its health, its readiness -- are on the one router that says so; everything
else is on a router that carries the guard, so a route added to it needs one
of the console's roles before anyone thinks to ask for it. Routes read what
they need from a `ConsoleContext` rather than from `create_app`'s locals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import APIKeyHeader, HTTPBearer
from starlette.status import HTTP_404_NOT_FOUND, HTTP_503_SERVICE_UNAVAILABLE

from .. import __version__
from ..api.routes import ApiHTTPError
from ..api.tls import CertificateInfo
from ..config import Settings
from nl2sql_common.envelope import Check, Health, Readiness
from nl2sql_identity import Guard, Identity
from .models import (
    AgentVerdict,
    ColumnModel,
    ConsoleLimits,
    ConsoleMeta,
    IssueModel,
    PromptModel,
    QueryRequest,
    QueryResult,
    ResultColumn,
    SchemaModel,
    TableModel,
)
from .query import DatabaseUnavailable, Inspector, Outcome
from .settings import MAX_SQL_LENGTH, ConsoleSettings

_bearer = HTTPBearer(
    auto_error=False,
    description="A session token from the auth service (POST /auth/token), or the console token.",
)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="The console token, by another name.")

#: On every router that needs a caller, so the OpenAPI document says how to
#: authenticate. They decide nothing: `Guard` does.
DOCUMENTED = [Depends(_bearer), Depends(_api_key)]


def role_warning(identity: Any) -> str | None:
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


@dataclass
class ConsoleContext:
    """Everything a route may reach, and the guard the console's router carries."""

    agent: Settings
    console: ConsoleSettings
    inspector: Inspector
    guard: Guard
    certificate: CertificateInfo | None
    started: float

    def __post_init__(self) -> None:
        self.caller: Callable[[Request], Identity] = self.guard.require(*self.console.allowed_roles)


def public_routes(ctx: ConsoleContext) -> APIRouter:
    """What this is, its health and its readiness: open by design, and the
    only router that is."""
    router = APIRouter(tags=["service"])
    agent, console, inspector, guard = ctx.agent, ctx.console, ctx.inspector, ctx.guard

    @router.get("/", summary="What this is and where to go next")
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

    @router.get("/healthz", response_model=Health, summary="Is the process alive")
    def healthz() -> Health:
        return Health(version=__version__, uptime_seconds=round(time.monotonic() - ctx.started, 3))

    @router.get(
        "/readyz",
        response_model=Readiness,
        summary="Can it run a query right now",
        responses={HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
    )
    def readyz(request: Request, response: Response) -> Readiness:
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
            ).for_caller(operator=guard.operator(request))
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
        signed, detail = guard.check()
        checks["sign_in"] = Check(ok=signed, detail=detail)
        ready = ready and signed
        if not ready:
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(ready=ready, checks=checks, warnings=warnings).for_caller(operator=guard.operator(request))

    return router


def console_routes(ctx: ConsoleContext) -> APIRouter:
    """The console itself: anyone holding one of its roles."""
    router = APIRouter(tags=["console"], dependencies=[*DOCUMENTED, Depends(ctx.caller)])
    agent, console, inspector, guard, certificate = ctx.agent, ctx.console, ctx.inspector, ctx.guard, ctx.certificate
    caller = ctx.caller

    @router.get(
        "/v1/meta",
        response_model=ConsoleMeta,
        summary="What the console is connected to, and the limits every query runs under",
    )
    def meta(who: Identity = Depends(caller)) -> ConsoleMeta:
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
            authentication=guard.describe(),
            runs_as=who.principal or identity.role,
            tls=(
                certificate.summary()
                if certificate is not None
                else {"enabled": console.tls_enabled, "self_signed": False}
            ),
            warnings=console.warnings() + ([warning] if warning else []),
        )

    @router.get(
        "/v1/schema",
        response_model=SchemaModel,
        summary="Every table, as the agent's introspection reads it",
    )
    def schema(who: Identity = Depends(caller)) -> SchemaModel:
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

    @router.get(
        "/v1/schema/{table}/prompt",
        response_model=PromptModel,
        summary="The block of the agent's prompt that describes one table",
    )
    def prompt(table: str, who: Identity = Depends(caller)) -> PromptModel:
        text = inspector.prompt(table)
        if not text:
            raise ApiHTTPError(
                HTTP_404_NOT_FOUND,
                "unknown_table",
                f"{table} is not a table in {agent.db_schema}",
            )
        return PromptModel(table=table, sample_rows=agent.sample_rows, text=text)

    @router.post(
        "/v1/query",
        response_model=QueryResult,
        summary="Run a query through the agent's gates",
    )
    def query(body: QueryRequest, who: Identity = Depends(caller)) -> QueryResult:
        outcome = inspector.run(body.sql, body.mode, principal=who.principal)
        return result_model(outcome, max_rows=console.max_rows, max_plan_cost=agent.max_plan_cost)

    return router


def routers(ctx: ConsoleContext) -> list[APIRouter]:
    """Every router, the open one first."""
    return [public_routes(ctx), console_routes(ctx)]
