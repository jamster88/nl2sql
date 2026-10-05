"""The auth service's routes, by router, each refused by default (V6-26).

As the other services': the routes open by design are on the one router
that says so -- signing in is how a caller becomes one, and `/auth/verify`
answers MLflow's front door with its own 401 -- and everything else is on a
router that carries the guard: a person's own session and password need a
session, the directory's web interface an administrator. A route added to
one of those is guarded before anyone thinks to guard it.

The routes read what they need -- and the sign-in, cookie and revocation
helpers they share -- from an `AuthContext` rather than from `create_app`'s
locals.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, ContextManager
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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

from nl2sql_common.envelope import ApiError, Check, Health, Readiness
from nl2sql_common.errors import DATABASE_ERRORS
from nl2sql_identity import ADMINS, Guard, Identity, TokenError, sign, verify
from nl2sql_identity.guard import UNSAFE_METHODS, check_origin
from nl2sql_ldap.directory import Directory, DirectoryError
from nl2sql_ldap.layout import normalise_login
from nl2sql_ldap.records import UserRecord, parse

from . import __version__, access
from .access import DirectoryUnavailable, PasswordRefused
from .login import PostgresLogin, SignInError
from .models import (
    AuthMeta,
    DirectoryMeta,
    Group,
    GroupList,
    ImportRequest,
    ImportResult,
    NewPassword,
    NewPerson,
    PasswordChange,
    Person,
    PersonChange,
    PersonList,
    Session,
    SignIn,
    SyncReport,
    Token,
)
from .pages import login_page, safe_next
from .proxies import TrustedProxies
from .revocation import PASSWORD_CHANGED, PASSWORD_SET, REMOVED, Revocations
from .rolesync import RoleSync, SyncResult
from .settings import AuthSettings
from .throttle import Throttle

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


def _bearer_token(request: Request) -> str | None:
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    return credentials.strip() or None if scheme.lower() == "bearer" else None


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


class _OriginalRequest:
    """The request a proxy is asking about, as `check_origin` reads one."""

    def __init__(self, request: Request) -> None:
        self.method = request.headers.get("x-original-method", "GET").upper()
        self.headers = request.headers


@dataclass
class AuthContext:
    """Everything a route may reach, the helpers they share, and the two
    guards the routers carry."""

    settings: AuthSettings
    signing_key: Any
    login: PostgresLogin
    directory: Callable[[], ContextManager[Directory]]
    password_changer: Callable[[str, str, str], None]
    rolesync: RoleSync | None
    revocations: Revocations | None
    guard: Guard
    throttle: Throttle
    proxies: TrustedProxies
    database_check: Callable[[], str] | None
    clock: Callable[[], float]
    started: float
    basic_cache: dict[str, tuple[Identity, float]] = field(default_factory=dict)
    #: The cut-offs this service set, still in the future: a sign-in in the
    #: second a password changed is signed at the cut-off, not before it.
    cutoffs: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Any signed-in caller: their own session and their own password.
        self.current: Callable[[Request], Identity] = self.guard.require()
        self.admin: Callable[[Request], Identity] = self.guard.require(ADMINS)

    # --- signing in -------------------------------------------------------

    def client_address(self, request: Request) -> str:
        """Where a sign-in came from: a trusted proxy's word for it, else the
        connection's own address (V6-63)."""
        peer = request.client.host if request.client else None
        return self.proxies.client(peer, request.headers.get("x-forwarded-for", ""))

    def sign_in(self, request: Request, username: str, password: str) -> tuple[Session, str]:
        keys = (f"name:{normalise_login(username)}", f"address:{self.client_address(request)}")
        wait = self.throttle.wait(*keys)
        if wait:
            raise AuthHTTPError(
                HTTP_429_TOO_MANY_REQUESTS,
                "too_many_attempts",
                f"too many wrong passwords; try again in {wait} seconds",
                **{"Retry-After": str(wait)},
            )
        try:
            identity = self.login.check(username, password)
        except SignInError as exc:
            if exc.code == "invalid_credentials":
                self.throttle.failed(*keys)
                raise AuthHTTPError(HTTP_401_UNAUTHORIZED, exc.code, str(exc)) from exc
            raise AuthHTTPError(HTTP_503_SERVICE_UNAVAILABLE, exc.code, str(exc)) from exc
        self.throttle.succeeded(keys[0])
        now = self.clock()
        floor = self.cutoffs.get(identity.user)
        if floor is not None:
            if floor > now:
                now = floor
            else:
                self.cutoffs.pop(identity.user, None)
        token = sign(identity, self.signing_key, lifetime_seconds=self.settings.session_seconds, now=now)
        session = Session(
            user=identity.user,
            name=identity.display,
            roles=sorted(identity.roles),
            kind=identity.kind,
            expires_at=int(now) + self.settings.session_seconds,
        )
        return session, token

    def set_cookie(self, request: Request, response: Response, token: str) -> None:
        response.set_cookie(
            self.settings.cookie_name,
            token,
            max_age=self.settings.session_seconds,
            path="/",
            httponly=True,
            samesite="strict",
            secure=_secure(request),
        )

    def clear_cookie(self, request: Request, response: Response) -> None:
        response.delete_cookie(self.settings.cookie_name, path="/", httponly=True, samesite="strict", secure=_secure(request))

    def forget(self, user: str) -> None:
        """Ask Postgres about `user` again: here at once, rather than in a minute."""
        self.guard.forget(user)
        for key in [key for key, (who, _) in self.basic_cache.items() if who.user == user]:
            self.basic_cache.pop(key, None)

    def cut_off(self, user: str, reason: str) -> int | None:
        """End every session `user` signed in before now. The cut-off, or None
        when this deployment cannot revoke (no AUTH_ROLESYNC_DB_URL)."""
        # The Basic cache too: an old password must not keep working for MLflow.
        self.forget(user)
        if self.revocations is None:
            return None
        try:
            at = self.revocations.cut_off(user, reason)
        except DATABASE_ERRORS as exc:
            raise AuthHTTPError(
                HTTP_503_SERVICE_UNAVAILABLE,
                "revocation_unavailable",
                f"done, but the sessions {user} signed in with before could not be ended: "
                f"{type(exc).__name__}. Try again, or wait for them to expire.",
            ) from exc
        # Again: a request between the first forget and the write may have
        # cached the answer from before it.
        self.guard.forget(user)
        self.cutoffs[user] = at
        return at

    def session_of(self, identity: Identity) -> Session:
        return Session(
            user=identity.user,
            name=identity.display,
            roles=sorted(identity.roles),
            kind=identity.kind,
            expires_at=identity.expires_at,
        )

    def basic(self, request: Request) -> Identity | None:
        """An MLflow client's `MLFLOW_TRACKING_USERNAME` and `_PASSWORD`, checked."""
        scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "basic":
            return None
        try:
            username, _, password = base64.b64decode(encoded.strip(), validate=True).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise AuthHTTPError(HTTP_401_UNAUTHORIZED, "unauthorized", "the Basic credentials cannot be decoded") from exc
        key = hashlib.sha256(f"{username}\0{password}".encode()).hexdigest()
        cached = self.basic_cache.get(key)
        now = self.clock()
        if cached and cached[1] > now:
            return cached[0]
        session, _ = self.sign_in(request, username, password)
        identity = Identity(user=session.user, name=session.name, roles=frozenset(session.roles))
        self.basic_cache[key] = (identity, now + BASIC_CACHE_SECONDS)
        return identity

    def directory_call(self, work: Callable[[Directory], Any]) -> Any:
        try:
            with self.directory() as found:
                return work(found)
        except DirectoryUnavailable as exc:
            raise AuthHTTPError(HTTP_503_SERVICE_UNAVAILABLE, "directory_unavailable", str(exc)) from exc
        except DirectoryError as exc:
            raise AuthHTTPError(DIRECTORY_STATUS.get(exc.code, HTTP_502_BAD_GATEWAY), exc.code, str(exc)) from exc

    def changed(self) -> None:
        if self.rolesync is not None:
            self.rolesync.trigger()

    def check_length(self, password: str | None) -> None:
        if password is not None and len(password) < self.settings.min_password_length:
            raise AuthHTTPError(
                HTTP_422_UNPROCESSABLE_CONTENT,
                "password_too_short",
                f"a password must be at least {self.settings.min_password_length} characters",
            )

    def groups_of(self, found: Directory) -> list[Group]:
        return [
            Group(name=name, role=self.settings.group_roles.get(name), members=sorted(members))
            for name, members in sorted(found.groups().items())
        ]


def public_routes(ctx: AuthContext) -> APIRouter:
    """What this is, its health, and signing in: open by design, and the
    only router that is -- `/auth/verify` refuses for itself."""
    router = APIRouter()
    settings, directory, rolesync = ctx.settings, ctx.directory, ctx.rolesync
    revocations, guard, signing_key, database_check = ctx.revocations, ctx.guard, ctx.signing_key, ctx.database_check
    started, sign_in, set_cookie, clear_cookie = ctx.started, ctx.sign_in, ctx.set_cookie, ctx.clear_cookie
    basic, current, clock = ctx.basic, ctx.current, ctx.clock

    @router.get("/", tags=["service"], summary="What this is and where to go next")
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

    @router.get("/healthz", tags=["service"], response_model=Health)
    def healthz() -> Health:
        return Health(version=__version__, uptime_seconds=round(time.monotonic() - started, 3))

    @router.get("/readyz", tags=["service"], response_model=Readiness, responses={503: {"model": Readiness}})
    def readyz(request: Request, response: Response) -> Readiness:
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
        return Readiness(ready=ready, checks=checks, warnings=settings.warnings() + settings.problems()).for_caller(operator=guard.operator(request))

    @router.get("/auth/meta", tags=["sign-in"], response_model=AuthMeta, summary="What a sign-in form needs to know")
    def meta() -> AuthMeta:
        return AuthMeta(
            version=__version__,
            mode=settings.ldap_mode,
            directory_editable=not settings.replica,
            session_hours=settings.session_hours,
            min_password_length=settings.min_password_length,
        )

    @router.post(
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

    @router.post(
        "/auth/token",
        tags=["sign-in"],
        response_model=Token,
        summary="Sign in a client that holds its own token: the desktop client, a script",
        responses={401: {"model": ApiError}, 429: {"model": ApiError}, 503: {"model": ApiError}},
    )
    def token_route(body: SignIn, request: Request) -> Token:
        session, token = sign_in(request, body.username, body.password)
        return Token(**session.model_dump(), token=token)

    @router.post(
        "/auth/logout",
        tags=["sign-in"],
        status_code=HTTP_204_NO_CONTENT,
        summary="Sign out: end this session everywhere, and forget a browser's cookie",
        responses={503: {"model": ApiError}},
    )
    def logout(request: Request) -> Response:
        """The session presented -- the bearer token, else the cookie -- is
        ended for every service, not only forgotten by this browser. A token
        that is not this service's, or has expired, has nothing to end."""
        _browser_origin(request)
        response = Response(status_code=HTTP_204_NO_CONTENT)
        clear_cookie(request, response)
        token = _bearer_token(request) or request.cookies.get(settings.cookie_name)
        if not token or revocations is None:
            return response
        try:
            identity = verify(token, signing_key.public_key(), now=clock())
        except TokenError:
            return response
        try:
            revocations.session(identity)
        except DATABASE_ERRORS as exc:
            failed = _error_response(
                HTTP_503_SERVICE_UNAVAILABLE,
                "revocation_unavailable",
                "signed out of this browser, but the session could not be ended everywhere: "
                f"{type(exc).__name__}. Try again, or it ends when it expires.",
            )
            clear_cookie(request, failed)
            return failed
        guard.forget(identity.user)
        return response

    @router.get(
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

    @router.get("/auth/login", tags=["sign-in"], response_class=HTMLResponse, include_in_schema=False)
    def login_form(next: str = Query(default="/")) -> HTMLResponse:
        return HTMLResponse(login_page(next_path=next))

    @router.post("/auth/login/form", tags=["sign-in"], include_in_schema=False)
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

    return router


def session_routes(ctx: AuthContext) -> APIRouter:
    """A person's own session and password: anyone signed in."""
    router = APIRouter(dependencies=[Depends(ctx.current)])
    settings, signing_key, password_changer = ctx.settings, ctx.signing_key, ctx.password_changer
    set_cookie, session_of, cut_off, current = ctx.set_cookie, ctx.session_of, ctx.cut_off, ctx.current

    @router.get(
        "/auth/session",
        tags=["sign-in"],
        response_model=Session,
        summary="Who is signed in, with the roles Postgres says they hold now",
        responses={401: {"model": ApiError}},
    )
    def session_route(identity: Identity = Depends(current)) -> Session:
        return session_of(identity)

    @router.post(
        "/auth/password",
        tags=["sign-in"],
        status_code=HTTP_204_NO_CONTENT,
        summary="Change your own password (standalone directory)",
        responses={403: {"model": ApiError}, 409: {"model": ApiError}, 422: {"model": ApiError}},
    )
    def change_password(body: PasswordChange, request: Request, identity: Identity = Depends(current)) -> Response:
        """Every session signed in with the old password ends. A browser gets
        a new one in its place; a client holding a bearer token signs in again."""
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
        response = Response(status_code=HTTP_204_NO_CONTENT)
        at = cut_off(identity.user, PASSWORD_CHANGED)
        if at is not None and _bearer_token(request) is None and request.cookies.get(settings.cookie_name):
            # Signed at the cut-off, which it is not before.
            set_cookie(request, response, sign(identity, signing_key, lifetime_seconds=settings.session_seconds, now=at))
        return response

    return router


def replica_routes(ctx: AuthContext) -> APIRouter:
    """In a replica, the directory's interface is a refusal, for anyone."""
    router = APIRouter()

    @router.api_route("/directory/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"], include_in_schema=False)
    def replica(rest: str) -> None:
        raise AuthHTTPError(
            HTTP_404_NOT_FOUND,
            "replica_read_only",
            "this directory is a read-only replica of another; people and groups are edited on the primary",
        )

    return router


def directory_routes(ctx: AuthContext) -> APIRouter:
    """The directory's web interface: administrators only."""
    router = APIRouter(dependencies=[Depends(ctx.admin)])
    admin = ctx.admin
    settings, rolesync, signing_key = ctx.settings, ctx.rolesync, ctx.signing_key
    set_cookie, cut_off, directory_call, changed = ctx.set_cookie, ctx.cut_off, ctx.directory_call, ctx.changed
    check_length, groups_of = ctx.check_length, ctx.groups_of

    @router.get("/directory/v1/meta", tags=["directory"], response_model=DirectoryMeta)
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

    @router.get("/directory/v1/people", tags=["directory"], response_model=PersonList)
    def people(identity: Identity = Depends(admin)) -> PersonList:
        found = directory_call(lambda d: [_person(p) for p in d.people()])
        return PersonList(people=found, count=len(found))

    @router.get("/directory/v1/people/{uid}", tags=["directory"], response_model=Person)
    def person(uid: str, identity: Identity = Depends(admin)) -> Person:
        found = directory_call(lambda d: d.person(uid))
        if found is None:
            raise AuthHTTPError(HTTP_404_NOT_FOUND, "not_found", f"there is nobody called {uid}")
        return _person(found)

    @router.post("/directory/v1/people", tags=["directory"], response_model=Person, status_code=HTTP_201_CREATED)
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

    @router.patch("/directory/v1/people/{uid}", tags=["directory"], response_model=Person)
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

    @router.delete("/directory/v1/people/{uid}", tags=["directory"], status_code=HTTP_204_NO_CONTENT)
    def remove_person(uid: str, identity: Identity = Depends(admin)) -> Response:
        if uid == identity.user:
            raise AuthHTTPError(
                HTTP_409_CONFLICT, "cannot_remove_yourself", "you cannot remove yourself: ask another administrator"
            )
        directory_call(lambda d: d.delete_person(uid))
        changed()
        cut_off(uid, REMOVED)
        return Response(status_code=HTTP_204_NO_CONTENT)

    @router.post("/directory/v1/people/{uid}/password", tags=["directory"], status_code=HTTP_204_NO_CONTENT)
    def set_password(uid: str, body: NewPassword, request: Request, identity: Identity = Depends(admin)) -> Response:
        check_length(body.password)
        directory_call(lambda d: d.set_password(uid, body.password))
        response = Response(status_code=HTTP_204_NO_CONTENT)
        at = cut_off(uid, PASSWORD_SET)
        if at is not None and uid == identity.user and _bearer_token(request) is None:
            # An administrator setting their own: this browser stays signed in.
            set_cookie(request, response, sign(identity, signing_key, lifetime_seconds=settings.session_seconds, now=at))
        return response

    @router.post("/directory/v1/people/{uid}/unlock", tags=["directory"], status_code=HTTP_204_NO_CONTENT)
    def unlock(uid: str, identity: Identity = Depends(admin)) -> Response:
        directory_call(lambda d: d.unlock(uid))
        return Response(status_code=HTTP_204_NO_CONTENT)

    @router.get("/directory/v1/groups", tags=["directory"], response_model=GroupList)
    def groups(identity: Identity = Depends(admin)) -> GroupList:
        return GroupList(groups=directory_call(groups_of))

    @router.post("/directory/v1/import", tags=["directory"], response_model=ImportResult)
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

    @router.post(
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

    return router


def routers(ctx: AuthContext) -> list[APIRouter]:
    """Every router, the open one first; the directory's, or a replica's refusal."""
    return [
        public_routes(ctx),
        session_routes(ctx),
        replica_routes(ctx) if ctx.settings.replica else directory_routes(ctx),
    ]
