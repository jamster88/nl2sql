"""Deciding who a request is from, for every service that serves people.

One dependency for the agent API, the SQL console and the review service,
where there used to be three separately written token checks. It reads a
caller in one of four ways, in this order:

1. **A bearer token** -- the desktop client, a script, MLflow's own client.
   Either a session token the auth service signed, or the deployment's
   static service token for machines (`API_TOKEN` and its siblings).
2. **`X-API-Key`** -- the static service token under its other name, as the
   services have always accepted it.
3. **The session cookie** -- a browser. Each GUI's proxy passes it through;
   no proxy adds a credential of its own once sign-in is on.
4. **Nothing** -- refused when sign-in is on; when it is off, the service
   behaves as it did before sign-in existed: open when no token is set,
   token-only when one is.

A cookie is the one credential a browser sends on its own, so a request
that changes something and is authenticated by one has to show it came from
the page's own origin (`check_origin`). A bearer token is never ambient and
needs no such check.

Roles are re-read from Postgres when the service has a way to (`recheck`),
once a minute per user: the token says what someone held when they signed
in, and the database says what they hold now.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from .tokens import (
    ANONYMOUS,
    SERVICE,
    SESSION,
    Identity,
    TokenError,
    load_public_key,
    verify,
)

#: Where the auth service writes the public half of its signing key, and
#: where every other service mounts that volume read-only.
DEFAULT_PUBLIC_KEY_FILE = "/etc/nl2sql/auth/session.pub"

#: The browser's session cookie. Host-only, so one sign-in covers every GUI
#: served from the same host -- cookies are scoped by host, not by port.
SESSION_COOKIE = "nl2sql_session"

#: The four Postgres group roles the directory's groups are synced into.
USERS = "nl2sql_users"
REVIEWERS = "nl2sql_reviewers"
CURATORS = "nl2sql_curators"
ADMINS = "nl2sql_admins"
ROLES = (USERS, REVIEWERS, CURATORS, ADMINS)

#: Methods that change something, and so must not ride on a cookie a page
#: on some other origin made the browser send.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: How often a missing or replaced public key file is looked for again.
KEY_RECHECK_SECONDS = 30.0


def _env(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    return default if raw is None else raw.lower() in {"1", "true", "yes", "on"}


def env_roles(name: str, default: Iterable[str]) -> frozenset[str]:
    """A comma-separated list of role names, with empty read as unset."""
    raw = _env(name)
    if raw is None:
        return frozenset(default)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class IdentityError(HTTPException):
    """A refusal with the machine-readable code every service's error body carries."""

    def __init__(self, status_code: int, code: str, detail: str, **headers: str) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers or None)
        self.code = code


@dataclass
class GuardSettings:
    """What a service needs to know to tell its callers apart."""

    #: Sign-in on -- by default here, not only in compose, so a service
    #: started any other way is not open by accident. Off is the deployment
    #: from before there was any: the static token below, or nothing.
    enabled: bool = True
    public_key_file: str = DEFAULT_PUBLIC_KEY_FILE
    cookie_name: str = SESSION_COOKIE
    #: The static credential for machines, which every service has had
    #: since before sign-in. Still accepted with sign-in on -- the smoke test
    #: and scripts have no person to sign in -- and scoped to `service_roles`.
    service_token: str | None = None
    service_roles: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls, *, token_variable: str, service_roles: Iterable[str]) -> "GuardSettings":
        return cls(
            enabled=_env_bool("AUTH_ENABLED", True),
            public_key_file=_env("AUTH_PUBLIC_KEY_FILE") or DEFAULT_PUBLIC_KEY_FILE,
            cookie_name=_env("AUTH_COOKIE_NAME") or SESSION_COOKIE,
            service_token=_env(token_variable),
            service_roles=frozenset(service_roles),
        )


def _bearer(request: Request) -> str | None:
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


def check_origin(request: Request) -> None:
    """Refuse a cookie-authenticated write that a page elsewhere caused.

    `Sec-Fetch-Site` is set by the browser and cannot be set by a page, so
    when it is present it decides: `same-origin`, or `none` for something
    the user typed. Every current browser sends it. Without it the `Origin`
    (or `Referer`) must name the host the request was sent to -- the GUI's,
    as its proxy reports it in `X-Forwarded-Host`, port and all, because
    another port on the same host is another origin.
    """
    if request.method not in UNSAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site in ("same-origin", "none"):
            return
        raise IdentityError(
            HTTP_403_FORBIDDEN,
            "cross_site",
            f"a request from another site ({site}) cannot change anything with your session",
        )
    source = request.headers.get("origin") or request.headers.get("referer")
    target = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not source or urlsplit(source).netloc != target:
        raise IdentityError(
            HTTP_403_FORBIDDEN,
            "cross_site",
            "a request that changes something must come from this site's own pages",
        )


class Guard:
    """The dependency, and the state behind it: the public key and a role cache."""

    def __init__(
        self,
        settings: GuardSettings,
        *,
        public_key=None,
        recheck: Callable[[str], frozenset[str] | None] | None = None,
        recheck_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._key = public_key
        self._key_fixed = public_key is not None
        self._key_mtime: float | None = None
        self._key_checked = float("-inf")
        self._key_problem = "not read yet"
        self._recheck = recheck
        self._recheck_seconds = recheck_seconds
        self._clock = clock
        self._roles: dict[str, tuple[frozenset[str] | None, float]] = {}
        self._lock = threading.Lock()

    # --- the key --------------------------------------------------------

    def public_key(self):
        """The verifying key, read when it appears and again when it changes.

        The auth service writes it on its first start, which may be after
        this service's; a missing file is retried rather than fatal, so a
        stack started in any order settles.
        """
        if self._key_fixed:
            return self._key
        with self._lock:
            now = self._clock()
            if self._key is not None and now - self._key_checked < KEY_RECHECK_SECONDS:
                return self._key
            self._key_checked = now
            path = self.settings.public_key_file
            try:
                mtime = os.stat(path).st_mtime
                if self._key is None or mtime != self._key_mtime:
                    self._key = load_public_key(path)
                    self._key_mtime = mtime
                self._key_problem = ""
            except (OSError, ValueError) as exc:
                self._key_problem = f"{path}: {exc}"
            return self._key

    def check(self) -> tuple[bool, str]:
        """For `/readyz`: can this service tell who anyone is?"""
        if not self.settings.enabled:
            return True, "sign-in is off (AUTH_ENABLED=false)"
        if self.public_key() is None:
            return False, (
                f"no session key to verify sign-ins with ({self._key_problem}); the auth "
                "service writes it on its first start"
            )
        return True, f"sign-in verified against {self.settings.public_key_file}"

    # --- who ------------------------------------------------------------

    def _service(self) -> Identity:
        return Identity(user="", name="service token", roles=self.settings.service_roles, kind=SERVICE)

    def _is_service_token(self, presented: str | None) -> bool:
        token = self.settings.service_token
        return bool(token and presented) and hmac.compare_digest(
            presented.encode("utf-8"), token.encode("utf-8")
        )

    def _session(self, token: str) -> Identity:
        key = self.public_key()
        if key is None:
            raise IdentityError(
                HTTP_503_SERVICE_UNAVAILABLE,
                "sign_in_unavailable",
                "this service cannot check sign-ins yet: the auth service has not written its key",
            )
        try:
            return verify(token, key, now=self._clock())
        except TokenError as exc:
            raise IdentityError(
                HTTP_401_UNAUTHORIZED, exc.code, str(exc), **{"WWW-Authenticate": "Bearer"}
            ) from exc

    def identify(self, request: Request, *, query_token: bool = False) -> Identity:
        """The caller, or `IdentityError` (401) when there is none to accept.

        `query_token` lets the static token arrive as `?access_token=`, which
        only the API's event stream allows: `EventSource` cannot set headers.
        A browser streaming with a session sends its cookie instead.
        """
        bearer = _bearer(request)
        api_key = request.headers.get("x-api-key")
        query = request.query_params.get("access_token") if query_token else None

        if not self.settings.enabled:
            if not self.settings.service_token:
                return Identity(user="", kind=ANONYMOUS)
            if self._is_service_token(bearer or api_key or query):
                return self._service()
            raise IdentityError(
                HTTP_401_UNAUTHORIZED,
                "unauthorized",
                "a valid token is required",
                **{"WWW-Authenticate": "Bearer"},
            )

        if bearer:
            return self._service() if self._is_service_token(bearer) else self._session(bearer)
        if self._is_service_token(api_key or query):
            return self._service()
        cookie = request.cookies.get(self.settings.cookie_name)
        if cookie:
            identity = self._session(cookie)
            check_origin(request)
            return identity
        raise IdentityError(
            HTTP_401_UNAUTHORIZED,
            "sign_in_required",
            "sign in to use this service",
            **{"WWW-Authenticate": "Bearer"},
        )

    def current(self, identity: Identity) -> Identity:
        """The identity with the roles Postgres says it holds now.

        Cached per user for `recheck_seconds`. A user who no longer exists
        is signed out (401); a database that cannot be asked is a 503 rather
        than a guess either way.
        """
        if identity.kind != SESSION or self._recheck is None:
            return identity
        now = self._clock()
        with self._lock:
            cached = self._roles.get(identity.user)
        if cached is not None and now - cached[1] < self._recheck_seconds:
            roles = cached[0]
        else:
            try:
                roles = self._recheck(identity.user)
            except Exception as exc:  # noqa: BLE001 - any failure to ask is the same answer
                raise IdentityError(
                    HTTP_503_SERVICE_UNAVAILABLE,
                    "roles_unavailable",
                    f"cannot confirm your access right now: {type(exc).__name__}",
                ) from exc
            with self._lock:
                self._roles[identity.user] = (roles, now)
        if roles is None:
            raise IdentityError(
                HTTP_401_UNAUTHORIZED,
                "account_removed",
                "your account no longer exists in the directory; sign in again",
                **{"WWW-Authenticate": "Bearer"},
            )
        return replace(identity, roles=roles)

    def require(self, *roles: str, query_token: bool = False) -> Callable[[Request], Identity]:
        """A FastAPI dependency: the caller, who must hold one of `roles`.

        No roles means any caller the guard accepts. The dependency carries
        `nl2sql_guard` (the roles it asks for), which is how a test over an
        application's route table tells a guarded route from one that was
        added without a guard (`tests/security/test_routes_guarded.py`).
        """
        wanted = frozenset(roles)

        def dependency(request: Request) -> Identity:
            identity = self.current(self.identify(request, query_token=query_token))
            if not identity.has_any(wanted):
                raise IdentityError(
                    HTTP_403_FORBIDDEN,
                    "forbidden",
                    f"{identity.display} is not in a group that may do this "
                    f"(needs one of: {', '.join(sorted(wanted))})",
                )
            request.state.identity = identity
            return identity

        dependency.nl2sql_guard = wanted  # type: ignore[attr-defined]
        return dependency

    def describe(self) -> str:
        """What `/v1/meta` says about authentication."""
        if self.settings.enabled:
            return "session"
        return "bearer" if self.settings.service_token else "none"
