"""The API with sign-in on: a person's question runs as them and is theirs.

The guard is the shared one (tests/auth/test_identity_guard.py tests it on
its own); this is what the API does with the identity it hands over.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nl2sql_agent.api.settings import ApiSettings
from nl2sql_identity import ADMINS, USERS, Guard, GuardSettings, Identity, sign

from .conftest import ANSWERED, make_runner

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
    readiness = make_client(guard=guard, api=ApiSettings(debug_detail=True)).get("/readyz").json()
    assert readiness["ready"] is False
    assert "writes it on its first start" in readiness["checks"]["sign_in"]["detail"]


# --- operator-only detail (V6-32) -----------------------------------------------


def test_readiness_says_what_is_up_to_anyone_and_why_to_an_administrator(signed_in):
    anyone = signed_in.get("/readyz").json()
    assert anyone["checks"] and all(check["detail"] == "" for check in anyone["checks"].values())
    assert anyone["warnings"] == []
    person = signed_in.get("/readyz", headers=bearer("uma")).json()
    assert all(check["detail"] == "" for check in person["checks"].values())
    admin = signed_in.get("/readyz", headers=bearer("ada", (ADMINS, USERS))).json()
    assert admin["checks"]["sign_in"]["detail"].startswith("sign-in verified")
    assert {name: check["ok"] for name, check in admin["checks"].items()} == {
        name: check["ok"] for name, check in anyone["checks"].items()
    }


FAILED_PARTS = {
    **ANSWERED,
    "retrieval_errors": {
        "knowledge": 'connection to server at "nl2sql-vectordb" (172.18.0.5), port 5432 failed',
        "examples": "examples are disabled",
    },
    "node_errors": {"narrator": "ResponseError: model 'x' not found at http://nl2sql-ollama:11434"},
}


def test_an_answer_says_which_part_failed_to_anyone_and_how_to_an_administrator(make_client):
    guard = Guard(GuardSettings(enabled=True), public_key=KEY.public_key())
    client = make_client(make_runner(state=FAILED_PARTS), guard=guard)
    person = ask_as(client, bearer("uma"))["answer"]
    assert person["retrieval_errors"] == {"knowledge": "unavailable", "examples": "disabled"}
    assert person["node_errors"] == {"narrator": "failed"}
    admin = ask_as(client, bearer("ada", (ADMINS, USERS)))["answer"]
    assert admin["retrieval_errors"] == FAILED_PARTS["retrieval_errors"]
    assert admin["node_errors"] == FAILED_PARTS["node_errors"]


def test_a_crash_is_named_to_anyone_and_described_to_an_administrator(make_client):
    guard = Guard(GuardSettings(enabled=True), public_key=KEY.public_key())
    client = make_client(make_runner(raises=OSError("cannot open /etc/nl2sql/secret")), guard=guard)
    person = ask_as(client, bearer("uma"))
    assert person["error"] == "the question could not be answered: OSError"
    listed = client.get("/v1/questions", headers=bearer("ada", (ADMINS, USERS))).json()["jobs"]
    assert listed == [], "someone else's question is not listed, administrator or not"
    admin = ask_as(client, bearer("ada", (ADMINS, USERS)))
    assert admin["error"] == "OSError: cannot open /etc/nl2sql/secret"
    events = client.get(f"/v1/questions/{admin['id']}/events", headers=bearer("ada", (ADMINS, USERS))).text
    assert "cannot open /etc/nl2sql/secret" in events
    events = client.get(f"/v1/questions/{person['id']}/events", headers=bearer("uma")).text
    assert "event: done" in events and "/etc/nl2sql/secret" not in events


def test_a_development_server_can_show_everyone_everything(make_client):
    guard = Guard(GuardSettings(enabled=True), public_key=KEY.public_key())
    client = make_client(make_runner(state=FAILED_PARTS), guard=guard, api=ApiSettings(debug_detail=True))
    assert ask_as(client, bearer("uma"))["answer"]["node_errors"] == FAILED_PARTS["node_errors"]



# --- the reload signal (V6-33) --------------------------------------------------


class Reloadable:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1
        return ["literals", "schema_edges"]


def test_only_an_administrator_may_have_the_agent_read_again(make_client):
    agent = Reloadable()
    guard = Guard(GuardSettings(enabled=True), public_key=KEY.public_key())
    client = make_client(guard=guard, agent_factory=lambda: agent)
    assert client.post("/v1/admin/reload").status_code == 401
    refused = client.post("/v1/admin/reload", headers=bearer("uma"))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden"
    assert agent.reloads == 0
    client.get("/v1/meta", headers=bearer("ada", (ADMINS, USERS)))  # builds the agent
    answer = client.post("/v1/admin/reload", headers=bearer("ada", (ADMINS, USERS)))
    assert answer.status_code == 200 and agent.reloads == 1
    assert answer.json()["reloaded"] == ["literals", "schema_edges"]
    assert answer.json()["sessions_forgotten"] == 0, "this guard asks Postgres nothing, so holds nothing"


def test_before_the_agent_has_read_anything_there_is_nothing_to_reload(make_client):
    def never():
        raise AssertionError("a reload must not build the agent")

    guard = Guard(GuardSettings(enabled=True), public_key=KEY.public_key())
    client = make_client(guard=guard, agent_factory=never)
    answer = client.post("/v1/admin/reload", headers=bearer("ada", (ADMINS, USERS)))
    assert answer.status_code == 200 and answer.json() == {"reloaded": [], "sessions_forgotten": 0}


def test_a_reload_makes_the_guard_ask_about_every_session_again(make_client):
    asked = []
    guard = Guard(
        GuardSettings(enabled=True),
        public_key=KEY.public_key(),
        recheck=lambda identity: asked.append(identity.user) or frozenset({ADMINS, USERS}),
    )
    client = make_client(guard=guard, agent_factory=lambda: Reloadable())
    admin = bearer("ada", (ADMINS, USERS))
    answer = client.post("/v1/admin/reload", headers=admin)
    assert answer.json()["sessions_forgotten"] == 1 and asked == ["ada"]
    client.post("/v1/admin/reload", headers=admin)
    assert asked == ["ada", "ada"], "asked again after the reload forgot the answer"
