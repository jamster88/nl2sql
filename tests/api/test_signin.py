"""The API with sign-in on: a person's question runs as them and is theirs.

The guard is the shared one (tests/auth/test_identity_guard.py tests it on
its own); this is what the API does with the identity it hands over.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nl2sql_agent.api.app import default_guard
from nl2sql_agent.api.settings import ApiSettings
from nl2sql_agent.config import Settings
from nl2sql_identity import REVIEWERS, USERS, Guard, GuardSettings, Identity, sign

KEY = Ed25519PrivateKey.generate()
SERVICE = "machine-token"


def bearer(user: str, roles=(USERS,)) -> dict[str, str]:
    token = sign(Identity(user=user, roles=frozenset(roles)), KEY, lifetime_seconds=600)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def signed_in(make_client):
    guard = Guard(
        GuardSettings(enabled=True, service_token=SERVICE, service_roles=frozenset({USERS})),
        public_key=KEY.public_key(),
    )
    return make_client(guard=guard)


def ask_as(client, headers, question="how many stores?", **body):
    answer = client.post("/v1/questions", json={"question": question, **body}, params={"wait": 10}, headers=headers)
    assert answer.status_code in (200, 202), answer.text
    return answer.json()


def test_nobody_signed_in_is_asked_to(signed_in):
    answer = signed_in.post("/v1/questions", json={"question": "q"})
    assert answer.status_code == 401 and answer.json()["error"]["code"] == "sign_in_required"


def test_a_question_runs_as_the_person_who_asked_it(signed_in):
    job = ask_as(signed_in, bearer("alice"))
    assert job["status"] == "succeeded"
    assert signed_in.app.state.jobs.get(job["id"]).principal == "alice"
    assert signed_in.app.state.jobs.get(job["id"]).owner == "alice"


def test_naming_yourself_as_the_principal_is_allowed_and_anyone_else_is_not(signed_in):
    assert ask_as(signed_in, bearer("alice"), principal="alice")["status"] == "succeeded"
    answer = signed_in.post("/v1/questions", json={"question": "q", "principal": "bob"}, headers=bearer("alice"))
    assert answer.status_code == 400 and answer.json()["error"]["code"] == "principal_not_allowed"
    assert "your questions run as alice" in answer.json()["error"]["message"]


def test_a_question_is_its_askers_alone(signed_in):
    job = ask_as(signed_in, bearer("alice"))
    path = f"/v1/questions/{job['id']}"
    for method, route in (
        ("get", path),
        ("get", f"{path}/events"),
        ("delete", path),
        ("delete", f"{path}/feedback"),
    ):
        answer = getattr(signed_in, method)(route, headers=bearer("bob"))
        assert answer.status_code == 404, f"{method} {route}"
    answer = signed_in.post(f"{path}/feedback", json={"verdict": "yes"}, headers=bearer("bob"))
    assert answer.status_code == 404
    assert signed_in.get(path, headers=bearer("alice")).status_code == 200


def test_each_person_lists_their_own_questions_and_a_machine_lists_all(signed_in):
    ask_as(signed_in, bearer("alice"), "a1")
    ask_as(signed_in, bearer("bob"), "b1")
    ask_as(signed_in, bearer("alice"), "a2")
    mine = signed_in.get("/v1/questions", headers=bearer("alice")).json()
    assert [job["question"] for job in mine["jobs"]] == ["a2", "a1"]
    machine = signed_in.get("/v1/questions", headers={"Authorization": f"Bearer {SERVICE}"}).json()
    assert machine["count"] == 3


def test_the_service_token_runs_as_the_service_and_owns_nothing(signed_in):
    job = ask_as(signed_in, {"X-API-Key": SERVICE})
    stored = signed_in.app.state.jobs.get(job["id"])
    assert (stored.principal, stored.owner) == (None, None)


def test_the_owner_can_withdraw_their_verdict_and_forget_their_question(signed_in, make_client):
    job = ask_as(signed_in, bearer("alice"))
    path = f"/v1/questions/{job['id']}"
    # No feedback store is configured here, so the withdrawal is the 503 the
    # store's absence answers -- after ownership has let it through.
    assert signed_in.delete(f"{path}/feedback", headers=bearer("alice")).status_code == 503
    assert signed_in.delete(path, headers=bearer("alice")).status_code == 204


def test_a_signed_in_browser_streams_with_its_cookie(signed_in):
    job = ask_as(signed_in, bearer("alice"))
    signed_in.cookies.set("nl2sql_session", bearer("alice")["Authorization"].split(" ", 1)[1])
    stream = signed_in.get(f"/v1/questions/{job['id']}/events")
    assert stream.status_code == 200 and "event: done" in stream.text


def test_meta_and_readiness_report_sign_in(signed_in):
    assert signed_in.get("/v1/meta", headers=bearer("alice")).json()["authentication"] == "session"
    assert signed_in.get("/readyz").json()["checks"]["sign_in"]["ok"] is True


def test_without_the_auth_services_key_the_api_is_not_ready(make_client, tmp_path):
    guard = Guard(GuardSettings(enabled=True, public_key_file=str(tmp_path / "missing.pub")))
    readiness = make_client(guard=guard).get("/readyz").json()
    assert readiness["ready"] is False
    assert "writes it on its first start" in readiness["checks"]["sign_in"]["detail"]


def test_a_reviewer_may_ask_too():
    assert Identity(user="r", roles=frozenset({REVIEWERS, USERS})).has_any({USERS})


def test_the_default_guard_follows_the_settings():
    off = default_guard(Settings(), ApiSettings(auth_enabled=False, token="t"))
    assert not off.settings.enabled and off.settings.service_token == "t" and off._recheck is None
    on = default_guard(Settings(), ApiSettings(auth_enabled=True, auth_cookie_name="sid"))
    assert on.settings.enabled and on.settings.cookie_name == "sid" and on._recheck is not None


def test_sign_in_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PUBLIC_KEY_FILE", "/keys/session.pub")
    monkeypatch.setenv("AUTH_COOKIE_NAME", "sid")
    api = ApiSettings.from_env()
    assert (api.auth_enabled, api.auth_public_key_file, api.auth_cookie_name) == (True, "/keys/session.pub", "sid")
    assert not any("No API_TOKEN" in note for note in api.warnings())
