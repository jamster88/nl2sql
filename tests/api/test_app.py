"""The HTTP surface, against a scripted pipeline.

Every route, every status code, the authentication, the error body and the
event stream, with no Ollama, no Postgres and no container -- so all of it
runs on a plain `pytest` rather than only when someone remembers
`--run-docker`.

What is being protected here is a contract other people's code is written
against. A route that quietly changes its status code or its response shape
breaks a GUI that this repository never sees, and the failure shows up in
their build, not ours.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from nl2sql_agent import __version__
from nl2sql_agent.api.models import MAX_METADATA_ENTRIES, MAX_QUESTION_LENGTH
from nl2sql_agent.api.settings import ApiSettings
from nl2sql_agent.config import Settings
from nl2sql_agent.llm import LlmUnavailableError

from .conftest import ask, make_runner


def events_from(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE body the way a client does: by event name and payload."""
    parsed, name = [], None
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: ") and name:
            parsed.append((name, json.loads(line.removeprefix("data: "))))
    return parsed


# ---------------------------------------------------------------------------
# Discovery: what a client hits first
# ---------------------------------------------------------------------------


def test_the_root_says_what_this_is_and_where_to_go(client):
    body = client.get("/").json()
    assert body["service"] == "nl2sql-agent"
    assert body["version"] == __version__
    assert body["endpoints"]["ask"] == "POST /v1/questions"


def test_health_answers_without_touching_anything(make_client):
    """An orchestrator's liveness probe must not depend on Postgres, or a
    database restart becomes a container restart loop.
    """

    def explode() -> None:
        raise AssertionError("health must not build the agent")

    client = make_client(agent_factory=explode)
    body = client.get("/healthz").json()
    assert body["status"] == "ok" and body["version"] == __version__
    assert body["uptime_seconds"] >= 0


def test_readiness_reports_the_reason_it_is_not_ready(make_client):
    client = make_client(
        agent_factory=lambda: (_ for _ in ()).throw(LlmUnavailableError("Cannot reach Ollama"))
    )
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert "Cannot reach Ollama" in body["checks"]["agent"]["detail"]
    assert body["checks"]["database"]["ok"] is False


def test_readiness_is_ready_when_every_check_passes(make_client, fake_agent):
    client = make_client(agent_factory=lambda: fake_agent)
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["database"]["detail"] == "3 tables"


def test_readiness_repeats_the_configuration_warnings(make_client):
    """The place an operator looks when something is odd, so the "TLS is off
    and there is no token" facts belong there and not only in the log.
    """
    client = make_client(api=ApiSettings(tls_enabled=False, token=None))
    warnings = " ".join(client.get("/readyz").json()["warnings"])
    assert "clear text" in warnings and "No API_TOKEN" in warnings


def test_a_reachable_model_and_an_unreachable_database_is_not_ready(make_client, fake_agent):
    """The half-failure the other readiness tests miss. The agent constructs,
    so Ollama answered, and only then does Postgres refuse -- which is the
    ordinary shape of a restart, and the one an orchestrator has to be able
    to wait out rather than restart the container over.
    """

    def refuse() -> list[str]:
        raise OSError("connection to server at 127.0.0.1 port 5432 refused")

    fake_agent.db.table_names = refuse
    client = make_client(agent_factory=lambda: fake_agent)
    response = client.get("/readyz")

    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["database"]["ok"] is False
    assert "OSError" in checks["database"]["detail"]
    assert "port 5432 refused" in checks["database"]["detail"]
    # The model is reported as fine, so the detail says which half broke.
    assert checks["llm"]["ok"] is True
    assert checks["agent"]["ok"] is True


def test_a_database_that_is_up_but_empty_is_not_ready(make_client, fake_agent):
    """The failure this repository has already seen twice: a container that
    is healthy and holds nothing.
    """
    fake_agent.db.tables = []
    client = make_client(agent_factory=lambda: fake_agent)
    assert client.get("/readyz").status_code == 503
    assert "schema is empty" in client.get("/readyz").json()["checks"]["database"]["detail"]


# ---------------------------------------------------------------------------
# /v1/meta: the whole framework-agnostic claim
# ---------------------------------------------------------------------------


def test_meta_publishes_everything_a_client_needs_to_configure_itself(make_client, fake_agent):
    client = make_client(agent_factory=lambda: fake_agent)
    body = client.get("/v1/meta").json()
    assert body["version"] == __version__
    assert body["tables"] == ["dim_product", "dim_store", "fact_pos_retail_sales"]
    assert "dim_store" in body["scope"]
    assert body["limits"]["max_rows"] == Settings().max_rows
    assert body["pipeline"]["schema_retrieval"] == "vector"
    assert "generate_sql" in body["pipeline"]["nodes"]
    assert body["intents"]


def test_meta_publishes_the_two_limits_that_describe_the_request(make_client, fake_agent):
    """A browser learns these from a 422 in its network tab. A desktop client
    shows the user whatever it was handed, and "422 Unprocessable Entity" is
    not an explanation of a text box forty characters too long -- which is
    why they are published at all.
    """
    client = make_client(agent_factory=lambda: fake_agent)
    limits = client.get("/v1/meta").json()["limits"]

    assert limits["max_question_length"] == MAX_QUESTION_LENGTH
    assert limits["max_metadata_entries"] == MAX_METADATA_ENTRIES


def test_the_published_question_length_is_the_one_actually_enforced(client):
    """The number is only worth publishing if a client can act on it. A
    server advertising 2000 while refusing at 1500 is worse than one that
    advertises nothing: the client stops the user at the wrong place and the
    server rejects what it said it would take.
    """
    limit = client.get("/v1/meta").json()["limits"]["max_question_length"]

    assert client.post("/v1/questions", json={"question": "a" * limit}).status_code != 422
    over = client.post("/v1/questions", json={"question": "a" * (limit + 1)})
    assert over.status_code == 422
    assert over.json()["error"]["code"] == "invalid_request"


def test_the_published_metadata_count_is_the_one_actually_enforced(client):
    limit = client.get("/v1/meta").json()["limits"]["max_metadata_entries"]
    at_the_limit = {str(index): "x" for index in range(limit)}

    assert client.post(
        "/v1/questions", json={"question": "how many stores?", "metadata": at_the_limit}
    ).status_code != 422
    over = client.post(
        "/v1/questions",
        json={"question": "how many stores?", "metadata": {**at_the_limit, "one": "more"}},
    )
    assert over.status_code == 422


def test_meta_still_answers_when_the_database_is_down(make_client):
    """A GUI needs the limits and the pipeline flags to render its own
    screen; refusing them because Postgres is booting makes it unusable at
    exactly the wrong moment.
    """
    client = make_client(
        agent_factory=lambda: (_ for _ in ()).throw(RuntimeError("connection refused"))
    )
    body = client.get("/v1/meta").json()
    assert body["tables"] == []
    assert body["limits"]["max_rows"] > 0


def test_meta_says_which_optional_stages_are_running(make_client, fake_agent):
    """A GUI with no narrator has no paragraph to show and no audit badge to
    draw; it finds that out here rather than from an empty field.
    """
    settings = Settings(narrate_enabled=False, audit_enabled=False, literals_enabled=False)
    client = make_client(agent_factory=lambda: fake_agent, settings=settings)
    pipeline = client.get("/v1/meta").json()["pipeline"]
    assert pipeline["narrate"] is False and pipeline["audit"] is False
    assert pipeline["supervisor"] is True


def test_meta_describes_the_certificate_being_presented(make_client, fake_agent, tmp_path):
    from nl2sql_agent.api.tls import generate_self_signed

    info = generate_self_signed(
        tmp_path / "c.pem", tmp_path / "k.pem", hostnames=("nl2sql-api",)
    )
    from nl2sql_agent.api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app(
        settings=Settings(),
        api_settings=ApiSettings(token=None),
        agent_factory=lambda: fake_agent,
        certificate=info,
    )
    with TestClient(app) as client:
        tls = client.get("/v1/meta").json()["tls"]
    assert tls["self_signed"] is True
    assert tls["hostnames"] == ["nl2sql-api"]
    assert tls["fingerprint_sha256"] == info.fingerprint_sha256


def test_the_openapi_document_describes_every_route(client):
    """The document is the client library. Anything missing from it is
    invisible to a generated client, however well it works over curl.
    """
    document = client.get("/openapi.json").json()
    assert set(document["paths"]) == {
        "/", "/healthz", "/readyz", "/v1/meta",
        "/v1/questions", "/v1/questions/{job_id}", "/v1/questions/{job_id}/events",
        "/v1/questions/{job_id}/feedback",
    }
    assert document["info"]["version"] == __version__


def test_the_documented_response_shapes_are_the_ones_the_routes_return(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert {"Job", "Answer", "Meta", "ApiError", "ProgressEvent", "ResultTable"} <= set(schemas)


# ---------------------------------------------------------------------------
# Asking a question
# ---------------------------------------------------------------------------


def test_a_question_returns_a_job_immediately(make_client):
    gate = threading.Event()
    client = make_client(make_runner(gate=gate))
    response = client.post("/v1/questions", json={"question": "how many stores?"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] in ("queued", "running")
    assert response.headers["Location"] == body["links"]["self"]
    assert body["answer"] is None
    gate.set()


def test_waiting_turns_the_same_call_into_a_blocking_one(client):
    response = client.post("/v1/questions", json={"question": "how many stores?"}, params={"wait": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["answer"]["result"]["rows"] == [[42]]
    assert body["answer"]["sql"].startswith("SELECT count(*)")


def test_a_wait_that_runs_out_returns_the_unfinished_job_not_an_error(make_client):
    """A wait is an optimisation, never a different contract: the client gets
    the same document and keeps polling.
    """
    gate = threading.Event()
    client = make_client(make_runner(gate=gate))
    response = client.post("/v1/questions", json={"question": "q"}, params={"wait": 0.2})
    assert response.status_code == 202
    assert response.json()["status"] == "running"
    gate.set()


def test_the_wait_is_capped_by_the_server(make_client):
    """Otherwise any client can pin a connection open for as long as it likes."""
    gate = threading.Event()
    api = ApiSettings(token=None, tls_enabled=False, max_wait_seconds=0.2)
    client = make_client(make_runner(gate=gate), api=api)
    started = time.monotonic()
    client.post("/v1/questions", json={"question": "q"}, params={"wait": 600})
    assert time.monotonic() - started < 5
    gate.set()


def test_the_progress_of_a_finished_job_is_the_whole_pipeline(client):
    body = ask(client)
    assert [event["step"] for event in body["progress"]] == [
        "supervise", "retrieve_schema", "generate_sql", "execute_query", "finish"
    ]
    assert body["progress"][2]["label"] == "sql"


def test_metadata_is_echoed_back_so_a_gui_can_correlate_turns(client):
    body = ask(client, metadata={"conversation": "abc", "turn": "3"})
    assert body["metadata"] == {"conversation": "abc", "turn": "3"}


def test_an_empty_question_is_rejected_with_the_documented_error_shape(client):
    response = client.post("/v1/questions", json={"question": "   "})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["detail"]["errors"]


def test_an_unknown_field_is_rejected_rather_than_ignored(client):
    """A client sending `questionn` should be told, not silently answered
    about nothing.
    """
    response = client.post("/v1/questions", json={"question": "q", "questionn": "typo"})
    assert response.status_code == 422


def test_a_pipeline_failure_is_a_failed_job_not_a_500(make_client):
    """The server survives the pipeline. A 500 would tell a GUI to retry the
    request; a failed job tells it what went wrong.
    """
    client = make_client(make_runner(raises=RuntimeError("ollama went away")))
    body = ask(client)
    assert body["status"] == "failed"
    assert body["error"] == "RuntimeError: ollama went away"


def test_a_refusal_comes_back_as_an_ordinary_answer(make_client):
    """Out of domain is a verdict, not an HTTP error: the agent worked
    correctly and said no.
    """
    client = make_client(
        make_runner(state={"verdict": "out_of_domain", "answer": "I can't answer that", "error": None})
    )
    body = ask(client, "what is the capital of France?")
    assert body["status"] == "succeeded"
    assert body["answer"]["verdict"] == "out_of_domain"
    assert body["answer"]["result"] is None


# ---------------------------------------------------------------------------
# Reading a question back
# ---------------------------------------------------------------------------


def test_a_job_is_the_same_document_throughout_its_life(make_client):
    gate = threading.Event()
    client = make_client(make_runner(gate=gate))
    created = client.post("/v1/questions", json={"question": "q"}).json()
    running = client.get(created["links"]["self"]).json()
    assert set(running) == set(created)
    gate.set()
    done = client.get(created["links"]["self"], params={"wait": 10}).json()
    assert done["status"] == "succeeded" and done["id"] == created["id"]


def test_polling_an_unfinished_job_is_told_how_long_to_wait(make_client):
    gate = threading.Event()
    client = make_client(make_runner(gate=gate))
    created = client.post("/v1/questions", json={"question": "q"}).json()
    response = client.get(created["links"]["self"])
    assert response.headers["Retry-After"] == "2"
    gate.set()


def test_a_finished_job_is_not_told_to_come_back(client):
    body = ask(client)
    assert "Retry-After" not in client.get(body["links"]["self"]).headers


def test_an_unknown_job_is_a_404_that_explains_itself(client):
    response = client.get("/v1/questions/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "kept for" in response.json()["error"]["message"]


def test_recent_questions_are_listed_newest_first(client):
    first, second = ask(client, "first"), ask(client, "second")
    listed = client.get("/v1/questions").json()
    assert [job["id"] for job in listed["jobs"][:2]] == [second["id"], first["id"]]
    assert listed["count"] >= 2


def test_the_listing_is_bounded(client):
    for index in range(4):
        ask(client, f"q{index}")
    assert len(client.get("/v1/questions", params={"limit": 2}).json()["jobs"]) == 2
    assert client.get("/v1/questions", params={"limit": 0}).status_code == 422


# ---------------------------------------------------------------------------
# The event stream
# ---------------------------------------------------------------------------


def test_the_stream_carries_the_pipeline_and_ends_with_the_answer(client):
    body = ask(client)
    stream = client.get(body["links"]["events"])
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    parsed = events_from(stream.text)
    assert [name for name, _ in parsed].count("progress") == 5
    assert parsed[-1][0] == "done"
    assert parsed[-1][1]["answer"]["result"]["rows"] == [[42]]


def test_each_progress_event_carries_its_sequence_number_as_the_event_id(client):
    """Which is what `Last-Event-ID` resumes from: without it a reconnecting
    browser replays the whole pipeline.
    """
    body = ask(client)
    text = client.get(body["links"]["events"]).text
    assert "id: 1\nevent: progress" in text


def test_a_reconnecting_client_resumes_rather_than_replaying(client):
    body = ask(client)
    text = client.get(body["links"]["events"], headers={"Last-Event-ID": "3"}).text
    seqs = [payload["seq"] for name, payload in events_from(text) if name == "progress"]
    assert seqs == [4, 5]


def test_the_sequence_can_also_be_resumed_from_the_query_string(client):
    """Not every client can set a header on a stream, and some proxies drop
    Last-Event-ID.
    """
    body = ask(client)
    text = client.get(body["links"]["events"], params={"from_seq": 4}).text
    assert [p["seq"] for n, p in events_from(text) if n == "progress"] == [5]


def test_a_nonsense_last_event_id_is_ignored_rather_than_fatal(client):
    body = ask(client)
    text = client.get(body["links"]["events"], headers={"Last-Event-ID": "not-a-number"}).text
    assert len([n for n, _ in events_from(text) if n == "progress"]) == 5


def test_the_stream_asks_proxies_not_to_buffer_it(client):
    """nginx buffers proxied responses by default, which turns a live
    progress stream into one delivery at the very end.
    """
    body = ask(client)
    headers = client.get(body["links"]["events"]).headers
    assert headers["X-Accel-Buffering"] == "no"
    assert "no-cache" in headers["Cache-Control"]


def test_streaming_an_unknown_job_is_a_404_not_an_empty_stream(client):
    assert client.get("/v1/questions/nope/events").status_code == 404


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


def test_a_finished_job_can_be_forgotten(client):
    body = ask(client)
    assert client.delete(body["links"]["self"]).status_code == 204
    assert client.get(body["links"]["self"]).status_code == 404


def test_a_running_job_reports_that_it_cannot_be_interrupted(make_client):
    gate = threading.Event()
    client = make_client(make_runner(gate=gate))
    created = client.post("/v1/questions", json={"question": "q"}).json()
    for _ in range(100):
        if client.get(created["links"]["self"]).json()["status"] == "running":
            break
        time.sleep(0.01)
    response = client.delete(created["links"]["self"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_running"
    gate.set()


def test_deleting_an_unknown_job_is_a_404(client):
    assert client.delete("/v1/questions/nope").status_code == 404


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.fixture
def secured(make_client):
    return make_client(api=ApiSettings(token="s3cret", tls_enabled=False, max_wait_seconds=10))


def test_without_a_token_nothing_under_v1_answers(secured):
    for method, path in (
        ("get", "/v1/meta"),
        ("get", "/v1/questions"),
        ("get", "/v1/questions/x"),
        ("get", "/v1/questions/x/events"),
        ("delete", "/v1/questions/x"),
    ):
        response = getattr(secured, method)(path)
        assert response.status_code == 401, f"{method.upper()} {path}"
        assert response.json()["error"]["code"] == "unauthorized"
    assert secured.post("/v1/questions", json={"question": "q"}).status_code == 401


#: The routes that answer before anything is authenticated: the ones an
#: orchestrator calls to decide whether this container is alive, and the
#: self-describing ones a developer opens in a browser. Everything else has
#: to refuse. `app.py` carried this as a constant once; nothing consulted
#: it, so it could not have caught a route that drifted out of the set --
#: which is the whole reason it is here, in a test, instead.
OPEN_BY_DESIGN = {
    "/",
    "/healthz",
    "/readyz",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


def test_the_probes_stay_open_so_an_orchestrator_can_use_them(secured):
    for path in ("/", "/healthz", "/readyz", "/openapi.json"):
        assert secured.get(path).status_code in (200, 503), path


def test_every_other_route_refuses_an_anonymous_caller(secured):
    """Read off the running app, not off a list kept by hand.

    A route added without `dependencies=guarded` is a route that answers
    before the caller has proved anything. The only way to notice is to ask
    the app what routes it has, so a new one is refused by default and
    opening it deliberately means editing OPEN_BY_DESIGN above.
    """
    checked = 0
    for route in secured.app.routes:
        path = getattr(route, "path", None)
        methods = (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}
        if not path or not methods or path in OPEN_BY_DESIGN:
            continue
        for method in sorted(methods):
            response = secured.request(
                method,
                path.replace("{job_id}", "x"),
                json={"question": "q"} if method == "POST" else None,
            )
            assert response.status_code == 401, f"{method} {path} answered anonymously"
            checked += 1
    assert checked, "no guarded routes found -- the route walk is not working"


def test_a_bearer_token_is_accepted(secured):
    assert secured.get("/v1/meta", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_an_api_key_header_is_accepted_for_clients_that_prefer_it(secured):
    assert secured.get("/v1/meta", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_the_token_may_travel_in_the_query_string_for_event_streams(secured):
    """Browsers' EventSource cannot set headers, and a GUI that cannot stream
    progress is back to showing a spinner for a minute.
    """
    created = secured.post(
        "/v1/questions", json={"question": "q"},
        params={"wait": 10}, headers={"Authorization": "Bearer s3cret"},
    ).json()
    stream = secured.get(created["links"]["events"], params={"access_token": "s3cret"})
    assert stream.status_code == 200


def test_a_wrong_token_is_refused_with_a_challenge(secured):
    response = secured.get("/v1/meta", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_the_openapi_document_says_authentication_is_in_use(secured):
    assert secured.get("/v1/meta", headers={"X-API-Key": "s3cret"}).json()["authentication"] == "bearer"


def test_an_open_server_says_so_too(client):
    assert client.get("/v1/meta").json()["authentication"] == "none"


# ---------------------------------------------------------------------------
# The database principal
# ---------------------------------------------------------------------------


def test_a_caller_chosen_principal_is_refused_by_default(client):
    response = client.post("/v1/questions", json={"question": "q", "principal": "analyst"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "principal_not_allowed"
    assert "API_ALLOW_PRINCIPAL=true" in response.json()["error"]["message"]


def test_a_principal_reaches_the_pipeline_when_it_is_switched_on(make_client):
    api = ApiSettings(token=None, tls_enabled=False, allow_principal=True, max_wait_seconds=10)
    client = make_client(api=api)
    body = ask(client, principal="analyst")
    assert body["answer"] is not None
    assert body["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Browsers
# ---------------------------------------------------------------------------


def test_a_browser_origin_is_allowed_when_it_is_listed(make_client):
    api = ApiSettings(token=None, tls_enabled=False, cors_origins=("https://gui.example.com",))
    client = make_client(api=api)
    response = client.get("/v1/meta", headers={"Origin": "https://gui.example.com"})
    assert response.headers["access-control-allow-origin"] == "https://gui.example.com"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_an_unlisted_origin_gets_no_permission(make_client):
    api = ApiSettings(token=None, tls_enabled=False, cors_origins=("https://gui.example.com",))
    client = make_client(api=api)
    response = client.get("/v1/meta", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


def test_the_wildcard_default_does_not_claim_to_support_credentials(client):
    """Browsers reject "*" plus credentials outright, so claiming both makes
    every request from a browser fail rather than some of them.
    """
    response = client.get("/v1/meta", headers={"Origin": "https://anything.example.com"})
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_the_preflight_allows_what_a_gui_actually_sends(make_client):
    api = ApiSettings(token=None, tls_enabled=False, cors_origins=("https://gui.example.com",))
    client = make_client(api=api)
    response = client.options(
        "/v1/questions",
        headers={
            "Origin": "https://gui.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed and "content-type" in allowed


# ---------------------------------------------------------------------------
# Documentation pages
# ---------------------------------------------------------------------------


def test_the_interactive_docs_are_served_by_default(client):
    assert client.get("/docs").status_code == 200


def test_the_docs_can_be_turned_off_without_taking_the_schema_with_them(make_client):
    """A generated client still needs /openapi.json even where a browsable
    page is not wanted.
    """
    client = make_client(api=ApiSettings(token=None, tls_enabled=False, docs_enabled=False))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_metadata_is_bounded_because_the_server_keeps_it(client):
    """An endpoint anyone can call in a loop must not let a caller store
    unbounded data for the lifetime of a job.
    """
    too_many = client.post(
        "/v1/questions", json={"question": "q", "metadata": {str(i): "x" for i in range(21)}}
    )
    assert too_many.status_code == 422
    too_long = client.post(
        "/v1/questions", json={"question": "q", "metadata": {"k": "x" * 300}}
    )
    assert too_long.status_code == 422


def test_a_question_reaches_the_pipeline_with_its_padding_removed(client):
    """Normalised in the schema rather than in the route, so a generated
    client documents the same rule.
    """
    body = ask(client, "  how many stores?  ")
    assert body["question"] == "how many stores?"


# ---------------------------------------------------------------------------
# The pieces the routes are made of
# ---------------------------------------------------------------------------


def test_a_keepalive_is_a_comment_line_rather_than_an_event():
    """Clients must not see it as data. An SSE comment keeps the connection
    warm and is invisible to `EventSource`.
    """
    from nl2sql_agent.api.app import _sse
    from nl2sql_agent.api.jobs import StreamChunk

    assert _sse(StreamChunk("keepalive"), base="") == ": keep-alive\n\n"


def test_a_timed_out_stream_says_how_to_come_back():
    """Rather than closing silently, which a client cannot tell apart from a
    dropped network.
    """
    from nl2sql_agent.api.app import _sse
    from nl2sql_agent.api.jobs import StreamChunk

    body = _sse(StreamChunk("timeout"), base="")
    assert body.startswith("event: timeout")
    assert "Last-Event-ID" in body


def test_the_default_runner_hands_the_question_to_the_agent(make_client, fake_agent):
    """The wiring between a POST and `Nl2SqlAgent.run` -- injected away in
    every other test here, and the one line that has to be right for any of
    this to answer anything.
    """
    client = make_client(agent_factory=lambda: fake_agent)
    body = client.post(
        "/v1/questions", json={"question": "how many stores?"}, params={"wait": 10}
    ).json()
    assert body["status"] == "succeeded"
    assert body["answer"]["result"]["rows"] == [[42]]
    assert [event["step"] for event in body["progress"]] == ["finish"]


def test_a_question_asked_of_a_shutting_down_server_is_told_to_come_back(make_client):
    from nl2sql_agent.api.jobs import JobStore

    store = JobStore(make_runner())
    client = make_client(api=ApiSettings(token=None, tls_enabled=False))
    client.app.state.jobs.shutdown()
    store.shutdown()
    response = client.post("/v1/questions", json={"question": "q"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
