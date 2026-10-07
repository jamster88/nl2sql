"""The auth service's HTTP surface.

Three groups of routes:

* **sign-in** (`/auth/...`) -- for every GUI, the desktop client and MLflow's
  proxy. A browser signs in with `POST /auth/login` and holds the session as
  an HttpOnly cookie, which every GUI on this host then sends; the desktop
  client and scripts use `POST /auth/token` and hold the token themselves.
  `GET /auth/verify` is nginx's `auth_request`: MLflow has no login of its
  own, so its proxy asks here about every request.
* **the directory's web interface** (`/directory/v1/...`) -- people and
  groups, edited by nl2sql-admins, in a standalone directory only. A replica's
  people come from its primary, so in a replica these routes do not exist.
* **service furniture** -- health, readiness, one error shape, the same as
  the other services'.

Signing out, a password changed or set, and a person removed each end
sessions where they are, not only in the browser that asked
(`revocation.Revocations`, V6-61): every service's guard refuses them
within its one-minute recheck, and this one at once.

Every collaborator is injectable, for the reason the other services' are:
the whole surface is exercised on every run with no Postgres, no directory
and no container.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, ContextManager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from nl2sql_common.envelope import FALLBACK_CODES as SHARED_FALLBACK_CODES
from nl2sql_identity import Guard, GuardSettings
from nl2sql_identity.postgres import membership_lookup
from nl2sql_ldap.directory import Directory

from . import __version__, access
from .login import PostgresLogin, roles_named
from .proxies import TrustedProxies
from .revocation import Revocations
from .rolesync import RoleSync
from .routes import AuthContext, _error_response, routers
from .settings import AuthSettings
from .throttle import Throttle

#: The shared codes, and sign-in's own: a throttled sign-in is "too many
#: attempts", and the directory refusing is a 502 of its own.
FALLBACK_CODES = {**SHARED_FALLBACK_CODES, 429: "too_many_attempts", 502: "directory_refused"}


def create_app(
    *,
    settings: AuthSettings | None = None,
    signing_key=None,
    login: PostgresLogin | None = None,
    directory: Callable[[], ContextManager[Directory]] | None = None,
    password_changer: Callable[[str, str, str], None] | None = None,
    rolesync: RoleSync | None = None,
    revocations: Revocations | None = None,
    guard: Guard | None = None,
    throttle: Throttle | None = None,
    proxies: TrustedProxies | None = None,
    database_check: Callable[[], str] | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    settings = settings or AuthSettings.from_env()
    assert signing_key is not None, "the server loads the key (keys.load_or_create) before building the app"
    roles = roles_named(settings.group_roles.values())
    login = login or PostgresLogin(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        sslmode=settings.db_sslmode,
        sslrootcert=settings.db_ssl.get("sslrootcert"),
        timeout=settings.db_connect_timeout,
        roles=roles,
    )
    directory = directory or (lambda: access.directory(settings))
    password_changer = password_changer or (
        lambda uid, current, new: access.change_own_password(settings, uid, current, new)
    )
    if revocations is None and settings.rolesync_url:
        revocations = Revocations(settings.rolesync_conninfo, session_seconds=settings.session_seconds, clock=clock)
    if rolesync is None and settings.rolesync_url and settings.ldap_service_password:

        def people() -> list:
            with directory() as found:
                return found.people()

        rolesync = RoleSync(
            rolesync_url=settings.rolesync_conninfo,
            people=people,
            group_roles=settings.group_roles,
            reader=settings.reader_role,
            statement_timeout_ms=settings.user_statement_timeout_ms,
            connection_limit=settings.user_connection_limit,
            revocations=revocations,
        )
    guard = guard or Guard(
        GuardSettings(enabled=True, public_key_file=settings.public_key_file, cookie_name=settings.cookie_name),
        public_key=signing_key.public_key(),
        recheck=membership_lookup(settings.rolesync_conninfo, roles) if settings.rolesync_url else None,
        clock=clock,
    )
    proxies = proxies if proxies is not None else TrustedProxies(settings.trusted_proxies)
    throttle = throttle or Throttle(
        settings.throttle_failures, settings.throttle_seconds,
        per_kind={"address": settings.throttle_address_failures},
    )
    started = time.monotonic()
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if rolesync is not None:
            threading.Thread(
                target=rolesync.run_forever,
                args=(stop, settings.role_sync_interval),
                name="rolesync",
                daemon=True,
            ).start()
        yield
        stop.set()
        if rolesync is not None:
            rolesync.trigger()

    app = FastAPI(
        title="nl2sql auth",
        version=__version__,
        summary="Sign in against the retail database, and the directory behind it.",
        root_path=settings.root_path,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.rolesync = rolesync
    app.state.revocations = revocations
    app.state.guard = guard

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials="*" not in settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # --- the directory's own port -----------------------------------------

    if settings.directory_port:

        @app.middleware("http")
        async def _directory_on_its_own_port(request: Request, call_next):
            """The routes that make people -- administrators among them -- are
            answered on AUTH_DIRECTORY_PORT alone (V6-58). The sign-in port is
            published to the network for the desktop client; this one is
            not, and the directory page's nginx reaches it from inside. They
            still need an administrator's session there: this is the second
            line, not the first."""
            server = request.scope.get("server") or (None, None)
            if request.url.path.startswith("/directory/") and server[1] != settings.directory_port:
                return _error_response(
                    404,
                    "not_found",
                    "the directory's API is not served on this port: it answers inside "
                    "the stack, for the directory page, on AUTH_DIRECTORY_PORT",
                )
            return await call_next(request)

    # --- error shape ------------------------------------------------------

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = getattr(exc, "code", None) or FALLBACK_CODES.get(exc.status_code, "error")
        return _error_response(exc.status_code, code, str(exc.detail), dict(exc.headers or {}))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        response = _error_response(422, "invalid_request", "the request body or query string is not valid")
        body = json.loads(response.body)
        body["error"]["detail"] = {"errors": json.loads(json.dumps(exc.errors(), default=str))}
        return JSONResponse(status_code=422, content=body)

    # --- the routes (routes.py), each router carrying its guard -------------

    context = AuthContext(
        settings=settings,
        signing_key=signing_key,
        login=login,
        directory=directory,
        password_changer=password_changer,
        rolesync=rolesync,
        revocations=revocations,
        guard=guard,
        throttle=throttle,
        proxies=proxies,
        database_check=database_check,
        clock=clock,
        started=started,
    )
    app.state.context = context
    for router in routers(context):
        app.include_router(router)

    return app

