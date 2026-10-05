"""Every route that does something is guarded, in every service (V6-60).

Since 6.2 (V6-26) the four FastAPI services put every route on a router
that carries the guard, so a route added to one is refused to anyone the
guard refuses before anybody thinks to guard it; the routes open by design
are on one router of their own. This walks each application's own route
table regardless, and fails for a route whose dependency tree has no
`Guard.require` in it -- and below, for a route added to an application
directly, past the routers, and for a router other than the open one that
carries no guard.

Derived from `app.routes` rather than a list kept here, so a route added
tomorrow is checked tomorrow. `Guard.require` marks what it returns with
`nl2sql_guard`; that mark is what is looked for.
"""

from __future__ import annotations

from typing import Callable, Iterator

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from tests.route_table import flattened

#: Paths every service serves without a caller, on purpose: what it is, its
#: health, and its contract. Signing in is open because it is how a caller
#: becomes one; `/auth/verify` answers MLflow's front door with 401 itself.
OPEN_BY_DESIGN = {
    "/",
    "/healthz",
    "/readyz",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/auth/meta",
    "/auth/token",
    "/auth/logout",
    "/auth/login",
    "/auth/login/form",
    "/auth/verify",
}


def _api() -> FastAPI:
    from nl2sql_agent.api.app import create_app
    from nl2sql_agent.api.jobs import JobStore
    from nl2sql_agent.api.settings import ApiSettings
    from nl2sql_agent.config import Settings

    store = JobStore(lambda question, principal, on_progress: {})
    store.shutdown()
    return create_app(settings=Settings(), api_settings=ApiSettings(tls_enabled=False), store=store)


def _console() -> FastAPI:
    from nl2sql_agent.config import Settings
    from nl2sql_agent.console.app import create_app
    from nl2sql_agent.console.settings import ConsoleSettings

    return create_app(
        settings=Settings(), console_settings=ConsoleSettings(tls_enabled=False), inspector_factory=lambda: None
    )


def _review(tmp_path) -> FastAPI:
    from nl2sql_review.app import create_app
    from nl2sql_review.settings import ReviewSettings

    from tests.review.conftest import FakeRepository

    settings = ReviewSettings(
        tls_enabled=False,
        document=str(tmp_path / "golden.md"),
        snippets_document=str(tmp_path / "snippets.md"),
        reload_context=False,
        reload_vectors=False,
        reload_snippets=False,
    )
    return create_app(settings=settings, repository=FakeRepository([]))


def _auth() -> FastAPI:
    from nl2sql_auth.app import create_app
    from nl2sql_auth.settings import AuthSettings

    return create_app(settings=AuthSettings(), signing_key=Ed25519PrivateKey.generate())


SERVICES: dict[str, Callable[..., FastAPI]] = {
    "api": _api,
    "console": _console,
    "review": _review,
    "auth": _auth,
}


def _calls(dependant: Dependant) -> Iterator[object]:
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


def _routes(app: FastAPI) -> list[APIRoute]:
    return [route for route in flattened(app.routes) if isinstance(route, APIRoute) and route.path not in OPEN_BY_DESIGN]


@pytest.fixture(params=sorted(SERVICES))
def service(request, tmp_path) -> tuple[str, FastAPI]:
    build = SERVICES[request.param]
    return request.param, (build(tmp_path) if request.param == "review" else build())


def test_every_route_that_is_not_open_by_design_is_guarded(service):
    name, app = service
    unguarded = [
        f"{sorted(route.methods)} {route.path}"
        for route in _routes(app)
        if not any(getattr(call, "nl2sql_guard", None) is not None for call in _calls(route.dependant))
    ]
    assert unguarded == [], f"{name}: routes with no Guard dependency"


def test_each_service_has_guarded_routes_to_check(service):
    """A service whose routes all moved under a prefix this file does not
    look at would pass the test above with nothing in it."""
    name, app = service
    assert len(_routes(app)) >= 4, name


def test_every_guarded_prefix_is_under_v1_or_the_sessions_own_routes(service):
    """Anything outside `/v1`, `/directory/v1` and the two session routes is
    either open by design (listed above) or a new kind of route someone
    should look at."""
    name, app = service
    stray = [
        route.path
        for route in _routes(app)
        if not route.path.startswith(("/v1/", "/directory/v1/"))
        and route.path not in ("/v1", "/auth/session", "/auth/password")
    ]
    assert stray == [], name



APP_MODULES = {
    "api": "agent/nl2sql_agent/api/app.py",
    "console": "agent/nl2sql_agent/console/app.py",
    "review": "review/nl2sql_review/app.py",
    "auth": "auth/nl2sql_auth/app.py",
}


@pytest.mark.parametrize("name", sorted(APP_MODULES))
def test_no_route_is_added_to_an_application_directly(name: str):
    """Past the routers is past their guard."""
    from pathlib import Path
    import re

    source = (Path(__file__).resolve().parent.parent.parent / APP_MODULES[name]).read_text()
    assert not re.search(r"@app\.(get|post|put|patch|delete|api_route)\(", source), name


def test_every_router_but_the_open_one_carries_a_guard(service):
    name, app = service
    unguarded_routers = []
    for included in app.routes:
        router = getattr(included, "original_router", None)
        if router is None:
            continue
        guarded = any(getattr(dependency.dependency, "nl2sql_guard", None) is not None for dependency in router.dependencies)
        paths = {route.path for route in router.routes}
        if not guarded and not paths <= OPEN_BY_DESIGN | {"/directory/{rest:path}"}:
            unguarded_routers.append(sorted(paths))
    assert unguarded_routers == [], f"{name}: routers with no guard of their own"
