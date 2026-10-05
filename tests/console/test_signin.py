"""The console with sign-in on: a reviewer's or curator's query runs as them."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from nl2sql_agent.config import Settings
from nl2sql_agent.console.app import create_app, default_guard
from nl2sql_agent.console.query import Inspector
from nl2sql_agent.console.settings import ConsoleSettings
from nl2sql_identity import CURATORS, REVIEWERS, USERS, Guard, GuardSettings, Identity, sign

KEY = Ed25519PrivateKey.generate()


def bearer(user: str, *roles: str) -> dict[str, str]:
    token = sign(Identity(user=user, roles=frozenset(roles)), KEY, lifetime_seconds=600)
    return {"Authorization": f"Bearer {token}"}


def _client(db, **console) -> TestClient:
    agent = Settings(max_rows=50, statement_timeout_ms=30000)
    settings = ConsoleSettings(**{"token": "script-token", **console})
    guard = Guard(
        GuardSettings(enabled=True, service_token="script-token", service_roles=frozenset(settings.allowed_roles)),
        public_key=KEY.public_key(),
    )
    app = create_app(
        settings=agent,
        console_settings=settings,
        inspector_factory=lambda: Inspector(db, agent, max_rows=settings.max_rows),
        guard=guard,
    )
    return TestClient(app)


def test_a_reviewers_query_runs_as_the_reviewer(db):
    answer = _client(db).post("/v1/query", json={"sql": "SELECT 1"}, headers=bearer("rita", REVIEWERS, USERS))
    assert answer.status_code == 200
    assert db.statements[:3] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = 30000",
        'SET LOCAL ROLE "rita"',
    ]


def test_a_curator_may_use_it_and_someone_who_only_asks_may_not(db):
    client = _client(db)
    assert client.get("/v1/schema", headers=bearer("cora", CURATORS)).status_code == 200
    refused = client.get("/v1/schema", headers=bearer("uma", USERS))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"


def test_the_allowed_roles_are_configurable(db):
    client = _client(db, allowed_roles=(USERS,))
    assert client.get("/v1/schema", headers=bearer("uma", USERS)).status_code == 200


def test_meta_says_whose_role_a_query_runs_as(db):
    client = _client(db)
    signed = client.get("/v1/meta", headers=bearer("rita", REVIEWERS)).json()
    assert signed["runs_as"] == "rita" and signed["authentication"] == "session"
    script = client.get("/v1/meta", headers={"Authorization": "Bearer script-token"}).json()
    assert script["runs_as"] == "nl2sql_reader", "a script runs as the connection's own role"


def test_a_script_runs_as_the_reader(db):
    _client(db).post("/v1/query", json={"sql": "SELECT 1"}, headers={"X-API-Key": "script-token"})
    assert not any(statement.startswith("SET LOCAL ROLE") for statement in db.statements)


def test_the_prompt_route_takes_a_caller_too(db):
    client = _client(db)
    assert client.get("/v1/schema/dim_store/prompt").status_code == 401
    assert client.get("/v1/schema/dim_store/prompt", headers=bearer("rita", REVIEWERS)).status_code == 200


def test_readiness_counts_sign_in(db, tmp_path):
    agent = Settings()
    guard = Guard(GuardSettings(enabled=True, public_key_file=str(tmp_path / "missing.pub")))
    app = create_app(
        settings=agent,
        console_settings=ConsoleSettings(),
        inspector_factory=lambda: Inspector(db, agent, max_rows=10),
        guard=guard,
    )
    readiness = TestClient(app).get("/readyz")
    assert readiness.status_code == 503
    assert readiness.json()["checks"]["sign_in"]["ok"] is False


def test_the_default_guard_follows_the_settings():
    off = default_guard(Settings(), ConsoleSettings(auth_enabled=False, token="t"))
    assert not off.settings.enabled and off._recheck is None
    assert off.settings.service_roles == {REVIEWERS, CURATORS}
    on = default_guard(Settings(), ConsoleSettings(auth_enabled=True, allowed_roles=(USERS,)))
    assert on.settings.enabled and on._recheck is not None and on.settings.service_roles == {USERS}
