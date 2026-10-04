"""The guard every service authenticates its callers through.

Driven through a real FastAPI app, because what matters is what a request
with a given set of headers and cookies is answered with -- including the
cross-site check that only a cookie needs.
"""

from __future__ import annotations

import os

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from nl2sql_identity import guard as guard_module
from nl2sql_identity.guard import (
    ADMINS,
    CURATORS,
    DEFAULT_PUBLIC_KEY_FILE,
    REVIEWERS,
    SESSION_COOKIE,
    USERS,
    Guard,
    GuardSettings,
    IdentityError,
    check_origin,
    env_roles,
)
from nl2sql_identity.tokens import ANONYMOUS, SERVICE, SESSION, public_key_pem

from .conftest import NOW

SERVICE_TOKEN = "s3rvice-token"


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_app(guard: Guard) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": getattr(exc, "code", None), "message": exc.detail},
            headers=exc.headers,
        )

    def show(identity):
        return {
            "user": identity.user,
            "kind": identity.kind,
            "roles": sorted(identity.roles),
            "principal": identity.principal,
        }

    @app.get("/anyone")
    def anyone(identity=Depends(guard.require())):
        return show(identity)

    @app.get("/users")
    def users(identity=Depends(guard.require(USERS))):
        return show(identity)

    @app.post("/review")
    def review(identity=Depends(guard.require(REVIEWERS, CURATORS))):
        return show(identity)

    @app.get("/events")
    def events(identity=Depends(guard.require(USERS, query_token=True))):
        return show(identity)

    @app.get("/who")
    def who(request: Request, identity=Depends(guard.require())):
        return {"state": request.state.identity.user}

    return app


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def signed_in(public_key, clock):
    """A client for a service with sign-in on and the static token configured."""

    def make(*, recheck=None, service_token=SERVICE_TOKEN) -> TestClient:
        settings = GuardSettings(
            enabled=True, service_token=service_token, service_roles=frozenset({USERS})
        )
        guard = Guard(settings, public_key=public_key, recheck=recheck, clock=clock)
        return TestClient(make_app(guard))

    return make


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- settings -------------------------------------------------------------


def test_settings_default_to_sign_in_off(monkeypatch):
    for name in ("AUTH_ENABLED", "AUTH_PUBLIC_KEY_FILE", "AUTH_COOKIE_NAME", "SOME_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    settings = GuardSettings.from_env(token_variable="SOME_TOKEN", service_roles={USERS})
    assert settings == GuardSettings(
        enabled=False,
        public_key_file=DEFAULT_PUBLIC_KEY_FILE,
        cookie_name=SESSION_COOKIE,
        service_token=None,
        service_roles=frozenset({USERS}),
    )


def test_settings_read_the_environment_and_treat_empty_as_unset(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PUBLIC_KEY_FILE", "/keys/session.pub")
    monkeypatch.setenv("AUTH_COOKIE_NAME", "  ")
    monkeypatch.setenv("SOME_TOKEN", " abc ")
    settings = GuardSettings.from_env(token_variable="SOME_TOKEN", service_roles=())
    assert settings.enabled and settings.public_key_file == "/keys/session.pub"
    assert settings.cookie_name == SESSION_COOKIE
    assert settings.service_token == "abc"


def test_role_lists_come_from_a_comma_separated_variable(monkeypatch):
    monkeypatch.setenv("SOME_ROLES", "nl2sql_reviewers, nl2sql_admins,,")
    assert env_roles("SOME_ROLES", {USERS}) == {REVIEWERS, ADMINS}
    monkeypatch.setenv("SOME_ROLES", "")
    assert env_roles("SOME_ROLES", {USERS}) == {USERS}


# --- sign-in off: the deployment from before ------------------------------


def test_with_sign_in_off_and_no_token_everyone_is_anonymous_and_allowed():
    client = TestClient(make_app(Guard(GuardSettings())))
    for path in ("/anyone", "/users", "/events"):
        answer = client.get(path)
        assert answer.status_code == 200
        assert answer.json()["kind"] == ANONYMOUS
    assert client.post("/review").status_code == 200


def test_with_sign_in_off_a_configured_token_is_required():
    guard = Guard(GuardSettings(service_token=SERVICE_TOKEN, service_roles=frozenset({USERS, REVIEWERS})))
    client = TestClient(make_app(guard))
    refused = client.get("/users")
    assert refused.status_code == 401
    assert refused.json()["code"] == "unauthorized"
    assert refused.headers["WWW-Authenticate"] == "Bearer"
    assert client.get("/users", headers=bearer("wrong")).status_code == 401
    assert client.get("/users", headers=bearer(SERVICE_TOKEN)).json()["kind"] == SERVICE
    assert client.get("/users", headers={"X-API-Key": SERVICE_TOKEN}).status_code == 200
    assert client.post("/review", headers=bearer(SERVICE_TOKEN)).status_code == 200


def test_the_query_string_token_is_accepted_only_where_a_route_allows_it():
    """The event stream allows it because `EventSource` cannot set headers;
    nothing else does, so the token stays out of every other URL."""
    guard = Guard(GuardSettings(service_token=SERVICE_TOKEN, service_roles=frozenset({USERS})))
    client = TestClient(make_app(guard))
    assert client.get(f"/events?access_token={SERVICE_TOKEN}").status_code == 200
    assert client.get(f"/users?access_token={SERVICE_TOKEN}").status_code == 401


# --- sign-in on ------------------------------------------------------------


def test_a_session_in_the_authorization_header_is_accepted(signed_in, token_for):
    answer = signed_in().get("/users", headers=bearer(token_for("alice")))
    assert answer.status_code == 200
    assert answer.json() == {"user": "alice", "kind": SESSION, "roles": [USERS], "principal": "alice"}


def test_a_session_in_the_cookie_is_accepted(signed_in, token_for):
    client = signed_in()
    client.cookies.set(SESSION_COOKIE, token_for("alice"))
    assert client.get("/users").json()["user"] == "alice"


def test_nobody_at_all_is_asked_to_sign_in(signed_in):
    answer = signed_in().get("/anyone")
    assert answer.status_code == 401
    assert answer.json()["code"] == "sign_in_required"


def test_an_expired_session_says_so(signed_in, token_for, clock):
    token = token_for(lifetime=60)
    clock.now = NOW + 3600
    answer = signed_in().get("/users", headers=bearer(token))
    assert answer.status_code == 401
    assert answer.json()["code"] == "expired"


def test_a_session_without_the_role_is_forbidden_and_told_which_it_needs(signed_in, token_for):
    answer = signed_in().post("/review", headers=bearer(token_for("alice", roles=(USERS,))))
    assert answer.status_code == 403
    body = answer.json()
    assert body["code"] == "forbidden"
    assert "nl2sql_curators, nl2sql_reviewers" in body["message"]
    assert "Alice Smith" in body["message"]


def test_the_service_token_still_works_and_holds_only_its_roles(signed_in):
    client = signed_in()
    assert client.get("/users", headers=bearer(SERVICE_TOKEN)).json()["kind"] == SERVICE
    assert client.get("/users", headers={"X-API-Key": SERVICE_TOKEN}).json()["principal"] is None
    assert client.get(f"/events?access_token={SERVICE_TOKEN}").status_code == 200
    assert client.post("/review", headers=bearer(SERVICE_TOKEN)).status_code == 403


def test_without_a_service_token_configured_a_bearer_must_be_a_session(signed_in):
    answer = signed_in(service_token=None).get("/users", headers=bearer(SERVICE_TOKEN))
    assert answer.status_code == 401
    assert answer.json()["code"] == "malformed"


def test_the_identity_is_left_on_the_request_for_the_route(signed_in, token_for):
    assert signed_in().get("/who", headers=bearer(token_for("carol"))).json() == {"state": "carol"}


# --- the cookie and the page that sent it ---------------------------------


@pytest.fixture
def cookie_client(signed_in, token_for):
    client = signed_in()
    client.cookies.set(SESSION_COOKIE, token_for("rita", roles=(REVIEWERS, USERS)))
    return client


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_a_cookie_write_from_the_page_itself_is_allowed(cookie_client, site):
    assert cookie_client.post("/review", headers={"Sec-Fetch-Site": site}).status_code == 200


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_a_cookie_write_caused_by_another_page_is_refused(cookie_client, site):
    """`same-site` too: another port on this host is another origin, and
    another of this stack's pages has no business writing through this one."""
    answer = cookie_client.post("/review", headers={"Sec-Fetch-Site": site})
    assert answer.status_code == 403
    assert answer.json()["code"] == "cross_site"


def test_without_fetch_metadata_the_origin_must_be_the_host(cookie_client):
    ok = cookie_client.post("/review", headers={"Origin": "http://testserver"})
    assert ok.status_code == 200
    assert cookie_client.post("/review", headers={"Origin": "https://evil.example"}).status_code == 403
    assert cookie_client.post("/review").status_code == 403


def test_behind_a_proxy_the_forwarded_host_is_the_one_that_counts(cookie_client):
    headers = {"Referer": "https://localhost:8081/queue", "X-Forwarded-Host": "localhost:8081"}
    assert cookie_client.post("/review", headers=headers).status_code == 200
    headers["X-Forwarded-Host"] = "localhost:8080"
    assert cookie_client.post("/review", headers=headers).status_code == 403


def test_reading_with_a_cookie_needs_no_origin(cookie_client):
    assert cookie_client.get("/users", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_a_bearer_write_needs_no_origin_because_nothing_sends_one_on_its_own(signed_in, token_for):
    headers = {**bearer(token_for("rita", roles=(REVIEWERS,))), "Sec-Fetch-Site": "cross-site"}
    assert signed_in().post("/review", headers=headers).status_code == 200


def test_check_origin_ignores_safe_methods():
    class Fake:
        method = "GET"
        headers: dict = {}

    check_origin(Fake())  # no exception
    Fake.method = "DELETE"
    with pytest.raises(IdentityError):
        check_origin(Fake())


# --- the key ---------------------------------------------------------------


def _file_guard(path, clock, **settings) -> Guard:
    return Guard(GuardSettings(enabled=True, public_key_file=str(path), **settings), clock=clock)


def test_without_its_key_a_service_says_it_cannot_check_sign_ins_yet(tmp_path, clock, token_for):
    guard = _file_guard(tmp_path / "session.pub", clock)
    ok, detail = guard.check()
    assert not ok and "writes it on its first start" in detail
    answer = TestClient(make_app(guard)).get("/users", headers=bearer(token_for()))
    assert answer.status_code == 503
    assert answer.json()["code"] == "sign_in_unavailable"


def test_a_key_that_appears_later_is_found(tmp_path, clock, public_key, token_for):
    path = tmp_path / "session.pub"
    guard = _file_guard(path, clock)
    assert guard.public_key() is None
    path.write_bytes(public_key_pem(public_key))
    clock.now += 1  # a missing key is looked for again on the next request
    assert guard.public_key() is not None
    ok, detail = guard.check()
    assert ok and str(path) in detail
    assert TestClient(make_app(guard)).get("/users", headers=bearer(token_for())).status_code == 200


def test_a_replaced_key_is_read_again_once_the_recheck_interval_passes(tmp_path, clock, public_key):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    path = tmp_path / "session.pub"
    path.write_bytes(public_key_pem(public_key))
    guard = _file_guard(path, clock)
    first = guard.public_key()
    replacement = Ed25519PrivateKey.generate().public_key()
    path.write_bytes(public_key_pem(replacement))
    os.utime(path, (NOW + 5, NOW + 5))
    assert guard.public_key() is first  # cached inside the interval
    clock.now += guard_module.KEY_RECHECK_SECONDS + 1
    assert guard.public_key() != first
    clock.now += guard_module.KEY_RECHECK_SECONDS + 1
    assert guard.public_key() is guard.public_key()  # unchanged file, same key


def test_a_file_that_is_not_a_key_is_reported(tmp_path, clock):
    path = tmp_path / "session.pub"
    path.write_text("not a key")
    ok, detail = _file_guard(path, clock).check()
    assert not ok and str(path) in detail


def test_check_with_sign_in_off_is_always_ready():
    assert Guard(GuardSettings()).check() == (True, "sign-in is off (AUTH_ENABLED=false)")


# --- roles as Postgres holds them now --------------------------------------


def test_roles_are_read_again_from_postgres(signed_in, token_for):
    asked = []

    def recheck(user):
        asked.append(user)
        return frozenset({REVIEWERS, USERS})

    client = signed_in(recheck=recheck)
    answer = client.post("/review", headers=bearer(token_for("alice", roles=(USERS,))))
    assert answer.status_code == 200, "promoted in the directory after signing in"
    assert answer.json()["roles"] == [REVIEWERS, USERS]
    assert asked == ["alice"]


def test_the_answer_is_cached_for_a_minute_per_user(signed_in, token_for, clock):
    asked = []
    client = signed_in(recheck=lambda user: asked.append(user) or frozenset({USERS}))
    token = token_for("alice")
    for _ in range(3):
        client.get("/users", headers=bearer(token))
    assert asked == ["alice"]
    clock.now += 61
    client.get("/users", headers=bearer(token))
    assert asked == ["alice", "alice"]


def test_a_user_removed_from_the_directory_is_signed_out(signed_in, token_for):
    answer = signed_in(recheck=lambda user: None).get("/users", headers=bearer(token_for()))
    assert answer.status_code == 401
    assert answer.json()["code"] == "account_removed"


def test_a_user_demoted_since_signing_in_loses_the_role_now(signed_in, token_for):
    client = signed_in(recheck=lambda user: frozenset({USERS}))
    answer = client.post("/review", headers=bearer(token_for("rita", roles=(REVIEWERS, USERS))))
    assert answer.status_code == 403


def test_a_database_that_cannot_be_asked_is_a_503_not_a_guess(signed_in, token_for):
    def broken(user):
        raise ConnectionError("down")

    answer = signed_in(recheck=broken).get("/users", headers=bearer(token_for()))
    assert answer.status_code == 503
    assert answer.json()["code"] == "roles_unavailable"


def test_the_service_token_is_not_rechecked(signed_in):
    def fail(user):
        raise AssertionError("asked about a service token")

    assert signed_in(recheck=fail).get("/users", headers=bearer(SERVICE_TOKEN)).status_code == 200


# --- what meta says ---------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "said"),
    [
        (GuardSettings(enabled=True), "session"),
        (GuardSettings(service_token="t"), "bearer"),
        (GuardSettings(), "none"),
    ],
)
def test_describe_names_the_scheme(settings, said):
    assert Guard(settings).describe() == said
