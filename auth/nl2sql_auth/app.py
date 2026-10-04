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

Every collaborator is injectable, for the reason the other services' are:
the whole surface is exercised on every run with no Postgres, no directory
and no container.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, ContextManager
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from nl2sql_identity import ADMINS, Guard, GuardSettings, Identity, sign
from nl2sql_identity.guard import UNSAFE_METHODS, check_origin
from nl2sql_identity.postgres import membership_lookup
from nl2sql_ldap.directory import Directory, DirectoryError
from nl2sql_ldap.layout import normalise_login
from nl2sql_ldap.records import UserRecord, parse

from . import __version__, access
from .access import DirectoryUnavailable, PasswordRefused
from .login import PostgresLogin, SignInError, roles_named
from .models import (
    ApiError,
    AuthMeta,
    Check,
    DirectoryMeta,
    Group,
    GroupList,
    Health,
    ImportRequest,
    ImportResult,
    NewPassword,
    NewPerson,
    PasswordChange,
    Person,
    PersonChange,
    PersonList,
    Readiness,
    Session,
    SignIn,
    SyncReport,
    Token,
)
from .pages import login_page, safe_next
from .rolesync import RoleSync, SyncResult
from .settings import AuthSettings
from .throttle import Throttle

FALLBACK_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "invalid_request",
    429: "too_many_attempts",
    500: "internal_error",
    502: "directory_refused",
    503: "unavailable",
}

#: How a directory refusal is answered. Anything else it says is a 502: the
#: directory is upstream of this service, and it said no for its own reason.
DIRECTORY_STATUS = {
    "not_found": HTTP_404_NOT_FOUND,
    "already_exists": HTTP_409_CONFLICT,
    "invalid_login": HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_hash": HTTP_422_UNPROCESSABLE_CONTENT,
    "required": HTTP_422_UNPROCESSABLE_CONTENT,
    "constraintViolation": HTTP_422_UNPROCESSABLE_CONTENT,
}

#: How long a Basic-authenticated MLflow client is remembered, so its many
#: requests are not each a connection to Postgres. Keyed by a hash of the
#: name and password, never the password.
BASIC_CACHE_SECONDS = 300


class AuthHTTPError(HTTPException):
    def __init__(self, status_code: int, code: str, detail: str, **headers: str) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers or None)
        self.code = code


def _error_response(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=ApiError.of(code, message).model_dump(), headers=headers)


def _client(request: Request) -> str:
    """The address a sign-in came from: the last hop the proxy recorded, if any.

    The last one, not the first: the proxy appends what it saw, and anything
    before it is whatever the client claimed.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if hops:
        return hops[-1]
    return request.client.host if request.client else "unknown"


def _secure(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _browser_origin(request: Request) -> None:
    """A browser's sign-in must come from one of this stack's own pages.

    Only when it says it is a browser: a script setting no Origin and no
    fetch metadata has no ambient cookie to abuse and nobody to trick.
    """
    if "sec-fetch-site" in request.headers or "origin" in request.headers:
        check_origin(request)


def _person(found) -> Person:
    return Person(
        uid=found.uid,
        name=found.name,
        cn=found.cn,
        sn=found.sn,
        given_name=found.given_name,
        mail=found.mail,
        display_name=found.display_name,
        groups=list(found.groups),
        locked=found.locked,
    )


def _sync_report(result: SyncResult | None) -> SyncReport | None:
    if result is None:
        return None
    return SyncReport(**{name: getattr(result, name) for name in SyncReport.model_fields})


def create_app(
    *,
    settings: AuthSettings | None = None,
    signing_key=None,
    login: PostgresLogin | None = None,
    directory: Callable[[], ContextManager[Directory]] | None = None,
    password_changer: Callable[[str, str, str], None] | None = None,
    rolesync: RoleSync | None = None,
    guard: Guard | None = None,
    throttle: Throttle | None = None,
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
        timeout=settings.db_connect_timeout,
        roles=roles,
    )
    directory = directory or (lambda: access.directory(settings))
    password_changer = password_changer or (
        lambda uid, current, new: access.change_own_password(settings, uid, current, new)
    )
    if rolesync is None and settings.rolesync_url and settings.ldap_service_password:

        def people() -> list:
            with directory() as found:
                return found.people()

        rolesync = RoleSync(
            rolesync_url=settings.rolesync_url,
            people=people,
            group_roles=settings.group_roles,
            reader=settings.reader_role,
            statement_timeout_ms=settings.user_statement_timeout_ms,
            connection_limit=settings.user_connection_limit,
        )
    guard = guard or Guard(
        GuardSettings(enabled=True, public_key_file=settings.public_key_file, cookie_name=settings.cookie_name),
        public_key=signing_key.public_key(),
        recheck=membership_lookup(settings.rolesync_url, roles) if settings.rolesync_url else None,
        clock=clock,
    )
    throttle = throttle or Throttle(
        settings.throttle_failures, settings.throttle_seconds,
        per_kind={"address": settings.throttle_address_failures},
    )
    basic_cache: dict[str, tuple[Identity, float]] = {}
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
    app.state.guard = guard

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials="*" not in settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

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

    # --- signing in -------------------------------------------------------

    def sign_in(request: Request, username: str, password: str) -> tuple[Session, str]:
        keys = (f"name:{normalise_login(username)}", f"address:{_client(request)}")
        wait = throttle.wait(*keys)
        if wait:
            raise AuthHTTPError(
                HTTP_429_TOO_MANY_REQUESTS,
                "too_many_attempts",
                f"too many wrong passwords; try again in {wait} seconds",
                **{"Retry-After": str(wait)},
            )
        try:
            identity = login.check(username, password)
        except SignInError as exc:
            if exc.code == "invalid_credentials":
                throttle.failed(*keys)
                raise AuthHTTPError(HTTP_401_UNAUTHORIZED, exc.code, str(exc)) from exc
            raise AuthHTTPError(HTTP_503_SERVICE_UNAVAILABLE, exc.code, str(exc)) from exc
        throttle.succeeded(keys[0])
        now = clock()
        token = sign(identity, signing_key, lifetime_seconds=settings.session_seconds, now=now)
        session = Session(
            user=identity.user,
            name=identity.display,
            roles=sorted(identity.roles),
            kind=identity.kind,
            expires_at=int(now) + settings.session_seconds,
        )
        return session, token

    def set_cookie(request: Request, response: Response, token: str) -> None:
        response.set_cookie(
            settings.cookie_name,
            token,
            max_age=settings.session_seconds,
            path="/",
            httponly=True,
            samesite="strict",
            secure=_secure(request),
        )

    def current(request: Request) -> Identity:
        return guard.current(guard.identify(request))

    def session_of(identity: Identity) -> Session:
        return Session(
            user=identity.user,
            name=identity.display,
            roles=sorted(identity.roles),
            kind=identity.kind,
            expires_at=identity.expires_at,
        )

    @app.get("/", tags=["service"], summary="What this is and where to go next")
    def root() -> dict[str, Any]:
        return {
            "service": "nl2sql-auth",
            "version": __version__,
            "openapi": "/openapi.json",
            "endpoints": {
                "meta": "/auth/meta",
                "sign_in": "POST /auth/login",
                "token": "POST /auth/token",
                "session": "/auth/session",
                "verify": "/auth/verify",
                "directory": None if settings.replica else "/directory/v1/meta",
            },
        }

    @app.get("/healthz", tags=["service"], response_model=Health)
    def healthz() -> Health:
        return Health(version=__version__, uptime_seconds=round(time.monotonic() - started, 3))

    @app.get("/readyz", tags=["service"], response_model=Readiness, responses={503: {"model": Readiness}})
    def readyz(response: Response) -> Readiness:
        checks: dict[str, Check] = {}
        try:
            with directory() as found:
                count = len(found.people())
                status = access.replica_status(found) if settings.replica else None
            checks["directory"] = Check(ok=True, detail=f"{count} people, {settings.ldap_mode}")
            if settings.replica:
                checks["replica"] = Check(
                    ok=bool(status and status.get("ok")),
                    detail=(
                        f"last copy from {status.get('upstream')} at {status.get('at')}"
                        + ("" if status.get("ok") else f": {status.get('error')}")
                        if status
                        else "the replica has not copied from its primary yet"
                    ),
                )
        except (DirectoryUnavailable, DirectoryError) as exc:
            checks["directory"] = Check(ok=False, detail=str(exc))
        if database_check is not None:
            try:
                checks["database"] = Check(ok=True, detail=database_check())
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                checks["database"] = Check(ok=False, detail=f"{type(exc).__name__}: {exc}")
        last = rolesync.last if rolesync is not None else None
        checks["role_sync"] = Check(
            ok=rolesync is not None and (last is None or last.ok),
            detail=(
                "not configured: nobody can be made a role"
                if rolesync is None
                else "not run yet"
                if last is None
                else f"{last.people} people at {last.at}"
                + ("" if last.ok else f"; {'; '.join(last.errors[:3])}")
            ),
        )
        ready = all(check.ok for check in checks.values())
        if not ready:
            response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(ready=ready, checks=checks, warnings=settings.warnings() + settings.problems())

    @app.get("/auth/meta", tags=["sign-in"], response_model=AuthMeta, summary="What a sign-in form needs to know")
    def meta() -> AuthMeta:
        return AuthMeta(
            version=__version__,
            mode=settings.ldap_mode,
            directory_editable=not settings.replica,
            session_hours=settings.session_hours,
            min_password_length=settings.min_password_length,
        )

    @app.post(
        "/auth/login",
        tags=["sign-in"],
        response_model=Session,
        summary="Sign in a browser: the session comes back as a cookie",
        responses={401: {"model": ApiError}, 429: {"model": ApiError}, 503: {"model": ApiError}},
    )
    def login_route(body: SignIn, request: Request, response: Response) -> Session:
        _browser_origin(request)
        session, token = sign_in(request, body.username, body.password)
        set_cookie(request, response, token)
        return session

    @app.post(
        "/auth/token",
        tags=["sign-in"],
        response_model=Token,
        summary="Sign in a client that holds its own token: the desktop client, a script",
        responses={401: {"model": ApiError}, 429: {"model": ApiError}, 503: {"model": ApiError}},
    )
    def token_route(body: SignIn, request: Request) -> Token:
        session, token = sign_in(request, body.username, body.password)
        return Token(**session.model_dump(), token=token)

    @app.post("/auth/logout", tags=["sign-in"], status_code=HTTP_204_NO_CONTENT, summary="Sign a browser out")
    def logout(request: Request) -> Response:
        _browser_origin(request)
        response = Response(status_code=HTTP_204_NO_CONTENT)
        response.delete_cookie(settings.cookie_name, path="/", httponly=True, samesite="strict", secure=_secure(request))
        return response

    @app.get(
        "/auth/session",
        tags=["sign-in"],
        response_model=Session,
        summary="Who is signed in, with the roles Postgres says they hold now",
        responses={401: {"model": ApiError}},
    )
    def session_route(identity: Identity = Depends(current)) -> Session:
        return session_of(identity)

    @app.post(
        "/auth/password",
        tags=["sign-in"],
        status_code=HTTP_204_NO_CONTENT,
        summary="Change your own password (standalone directory)",
        responses={403: {"model": ApiError}, 409: {"model": ApiError}, 422: {"model": ApiError}},
    )
    def change_password(body: PasswordChange, identity: Identity = Depends(current)) -> Response:
        if identity.principal is None:
            raise AuthHTTPError(HTTP_403_FORBIDDEN, "not_a_person", "only a signed-in person has a password to change")
        if settings.replica:
            raise AuthHTTPError(
                HTTP_409_CONFLICT,
                "replica_read_only",
                "this directory is a replica: change your password where your account lives",
            )
        try:
            password_changer(identity.user, body.current, body.new)
        except PasswordRefused as exc:
            raise AuthHTTPError(HTTP_403_FORBIDDEN, "wrong_password", str(exc)) from exc
        except DirectoryUnavailable as exc:
            raise AuthHTTPError(HTTP_503_SERVICE_UNAVAILABLE, "directory_unavailable", str(exc)) from exc
        except DirectoryError as exc:
            raise AuthHTTPError(DIRECTORY_STATUS.get(exc.code, HTTP_502_BAD_GATEWAY), exc.code, str(exc)) from exc
        return Response(status_code=HTTP_204_NO_CONTENT)

    def basic(request: Request) -> Identity | None:
        """An MLflow client's `MLFLOW_TRACKING_USERNAME` and `_PASSWORD`, checked."""
        scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "basic":
            return None
        try:
            username, _, password = base64.b64decode(encoded.strip(), validate=True).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise AuthHTTPError(HTTP_401_UNAUTHORIZED, "unauthorized", "the Basic credentials cannot be decoded") from exc
        key = hashlib.sha256(f"{username}\0{password}".encode()).hexdigest()
        cached = basic_cache.get(key)
        now = clock()
        if cached and cached[1] > now:
            return cached[0]
        session, _ = sign_in(request, username, password)
        identity = Identity(user=session.user, name=session.name, roles=frozenset(session.roles))
        basic_cache[key] = (identity, now + BASIC_CACHE_SECONDS)
        return identity

    @app.get(
        "/auth/verify",
        tags=["sign-in"],
        status_code=HTTP_204_NO_CONTENT,
        summary="For a proxy's auth_request: may this request through?",
        responses={401: {"model": ApiError}, 403: {"model": ApiError}},
    )
    def verify_route(request: Request, role: list[str] = Query(default=[])) -> Response:
        identity = basic(request)
        if identity is None:
            identity = current(request)
            # The proxy's subrequest is a GET whatever the request was; the
            # method it is asking about comes in a header, and a write riding
            # on a cookie gets the same origin check a write here would.
            if request.headers.get("x-original-method", "GET").upper() in UNSAFE_METHODS and not request.headers.get(
                "authorization"
            ):
                original = _OriginalRequest(request)
                check_origin(original)  # type: ignore[arg-type]
        wanted = role or list(settings.mlflow_roles)
        if not identity.has_any(wanted):
            raise AuthHTTPError(
                HTTP_403_FORBIDDEN,
                "forbidden",
                f"{identity.display} is not in a group that may use this (needs one of: {', '.join(sorted(wanted))})",
            )
        return Response(status_code=HTTP_204_NO_CONTENT, headers={"X-Auth-User": identity.user})

    @app.get("/auth/login", tags=["sign-in"], response_class=HTMLResponse, include_in_schema=False)
    def login_form(next: str = Query(default="/")) -> HTMLResponse:
        return HTMLResponse(login_page(next_path=next))

    @app.post("/auth/login/form", tags=["sign-in"], include_in_schema=False)
    async def login_form_post(request: Request) -> Response:
        _browser_origin(request)
        fields = parse_qs((await request.body()).decode("utf-8", "replace"))
        username = (fields.get("username") or [""])[0]
        password = (fields.get("password") or [""])[0]
        target = safe_next((fields.get("next") or ["/"])[0])
        try:
            _, token = sign_in(request, username, password)
        except AuthHTTPError as exc:
            return HTMLResponse(
                login_page(next_path=target, message=str(exc.detail), username=username),
                status_code=exc.status_code,
            )
        response = RedirectResponse(target, status_code=303)
        set_cookie(request, response, token)
        return response

    # --- the directory's web interface --------------------------------------

    if settings.replica:

        @app.api_route("/directory/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"], include_in_schema=False)
        def replica(rest: str) -> None:
            raise AuthHTTPError(
                HTTP_404_NOT_FOUND,
                "replica_read_only",
                "this directory is a read-only replica of another; people and groups are edited on the primary",
            )

        return app

    admin = guard.require(ADMINS)

    def directory_call(work: Callable[[Directory], Any]) -> Any:
        try:
            with directory() as found:
                return work(found)
        except DirectoryUnavailable as exc:
            raise AuthHTTPError(HTTP_503_SERVICE_UNAVAILABLE, "directory_unavailable", str(exc)) from exc
        except DirectoryError as exc:
            raise AuthHTTPError(DIRECTORY_STATUS.get(exc.code, HTTP_502_BAD_GATEWAY), exc.code, str(exc)) from exc

    def changed() -> None:
        if rolesync is not None:
            rolesync.trigger()

    def check_length(password: str | None) -> None:
        if password is not None and len(password) < settings.min_password_length:
            raise AuthHTTPError(
                HTTP_422_UNPROCESSABLE_CONTENT,
                "password_too_short",
                f"a password must be at least {settings.min_password_length} characters",
            )

    def groups_of(found: Directory) -> list[Group]:
        return [
            Group(name=name, role=settings.group_roles.get(name), members=sorted(members))
            for name, members in sorted(found.groups().items())
        ]

    @app.get("/directory/v1/meta", tags=["directory"], response_model=DirectoryMeta)
    def directory_meta(identity: Identity = Depends(admin)) -> DirectoryMeta:
        def work(found: Directory) -> DirectoryMeta:
            return DirectoryMeta(
                version=__version__,
                mode=settings.ldap_mode,
                base_dn=settings.ldap_base_dn,
                people=len(found.people()),
                groups=groups_of(found),
                min_password_length=settings.min_password_length,
                role_sync=_sync_report(rolesync.last if rolesync is not None else None),
            )

        return directory_call(work)

    @app.get("/directory/v1/people", tags=["directory"], response_model=PersonList)
    def people(identity: Identity = Depends(admin)) -> PersonList:
        found = directory_call(lambda d: [_person(p) for p in d.people()])
        return PersonList(people=found, count=len(found))

    @app.get("/directory/v1/people/{uid}", tags=["directory"], response_model=Person)
    def person(uid: str, identity: Identity = Depends(admin)) -> Person:
        found = directory_call(lambda d: d.person(uid))
        if found is None:
            raise AuthHTTPError(HTTP_404_NOT_FOUND, "not_found", f"there is nobody called {uid}")
        return _person(found)

    @app.post("/directory/v1/people", tags=["directory"], response_model=Person, status_code=HTTP_201_CREATED)
    def add_person(body: NewPerson, identity: Identity = Depends(admin)) -> Person:
        check_length(body.password)
        record = UserRecord(
            uid=normalise_login(body.uid),
            given_name=body.given_name.strip(),
            surname=body.surname.strip(),
            display_name=body.display_name.strip(),
            mail=body.mail.strip(),
            groups=tuple(body.groups),
            password=body.password,
        )

        def work(found: Directory):
            found.add_person(record)
            return found.person(record.uid)

        created = directory_call(work)
        changed()
        return _person(created)

    @app.patch("/directory/v1/people/{uid}", tags=["directory"], response_model=Person)
    def change_person(uid: str, body: PersonChange, identity: Identity = Depends(admin)) -> Person:
        if uid == identity.user and body.groups is not None and "nl2sql-admins" not in body.groups:
            raise AuthHTTPError(
                HTTP_409_CONFLICT,
                "cannot_demote_yourself",
                "you cannot take yourself out of nl2sql-admins: ask another administrator",
            )
        updates = {
            key: value.strip()
            for key, value in (
                ("given_name", body.given_name),
                ("sn", body.surname),
                ("display_name", body.display_name),
                ("mail", body.mail),
            )
            if value is not None
        }

        def work(found: Directory):
            if updates:
                found.update_person(uid, **updates)
            if body.groups is not None:
                found.set_groups(uid, body.groups)
            return found.person(uid)

        updated = directory_call(work)
        if updated is None:
            raise AuthHTTPError(HTTP_404_NOT_FOUND, "not_found", f"there is nobody called {uid}")
        changed()
        return _person(updated)

    @app.delete("/directory/v1/people/{uid}", tags=["directory"], status_code=HTTP_204_NO_CONTENT)
    def remove_person(uid: str, identity: Identity = Depends(admin)) -> Response:
        if uid == identity.user:
            raise AuthHTTPError(
                HTTP_409_CONFLICT, "cannot_remove_yourself", "you cannot remove yourself: ask another administrator"
            )
        directory_call(lambda d: d.delete_person(uid))
        changed()
        return Response(status_code=HTTP_204_NO_CONTENT)

    @app.post("/directory/v1/people/{uid}/password", tags=["directory"], status_code=HTTP_204_NO_CONTENT)
    def set_password(uid: str, body: NewPassword, identity: Identity = Depends(admin)) -> Response:
        check_length(body.password)
        directory_call(lambda d: d.set_password(uid, body.password))
        return Response(status_code=HTTP_204_NO_CONTENT)

    @app.post("/directory/v1/people/{uid}/unlock", tags=["directory"], status_code=HTTP_204_NO_CONTENT)
    def unlock(uid: str, identity: Identity = Depends(admin)) -> Response:
        directory_call(lambda d: d.unlock(uid))
        return Response(status_code=HTTP_204_NO_CONTENT)

    @app.get("/directory/v1/groups", tags=["directory"], response_model=GroupList)
    def groups(identity: Identity = Depends(admin)) -> GroupList:
        return GroupList(groups=directory_call(groups_of))

    @app.post("/directory/v1/import", tags=["directory"], response_model=ImportResult)
    def import_file(body: ImportRequest, identity: Identity = Depends(admin)) -> ImportResult:
        records = parse(body.content, filename=body.filename)
        summary = directory_call(lambda d: d.apply(records))
        changed()
        return ImportResult(
            created=summary.created,
            updated=summary.updated,
            passwords=summary.passwords,
            groups=summary.groups,
            problems=summary.problems,
        )

    @app.post(
        "/directory/v1/sync",
        tags=["directory"],
        response_model=SyncReport,
        responses={503: {"model": ApiError}},
        summary="Make the directory's people roles in the retail database now",
    )
    def sync(identity: Identity = Depends(admin)) -> SyncReport:
        if rolesync is None:
            raise AuthHTTPError(
                HTTP_503_SERVICE_UNAVAILABLE,
                "role_sync_unavailable",
                "the role sync is not configured (AUTH_ROLESYNC_DB_URL, LDAP_SERVICE_PASSWORD)",
            )
        return _sync_report(rolesync.run_once())

    return app


class _OriginalRequest:
    """The request a proxy is asking about, as `check_origin` reads one."""

    def __init__(self, request: Request) -> None:
        self.method = request.headers.get("x-original-method", "GET").upper()
        self.headers = request.headers

