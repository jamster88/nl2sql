"""The console's HTTP surface, against a scripted database.

What is pinned: a refused query is an answer, not an error; the database
being down is a 503 in the same envelope every other failure uses; the token
guards everything but health; and nothing about the role the console is
connected as is left for a person to infer.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from nl2sql_agent import __version__
from nl2sql_agent.api.tls import CertificateInfo
from nl2sql_agent.config import Settings
from nl2sql_agent.console.app import create_app
from nl2sql_agent.console.query import Inspector
from nl2sql_agent.console.settings import MAX_SQL_LENGTH, ConsoleSettings
from tests.console.conftest import ScriptedDatabase


def _client(
    db: ScriptedDatabase,
    *,
    agent: Settings | None = None,
    certificate: CertificateInfo | None = None,
    **console,
) -> TestClient:
    agent = agent or Settings(max_rows=50, statement_timeout_ms=30000, max_plan_cost=1_000_000.0)
    # The open console unless a test says otherwise; sign-in has its own file.
    settings = ConsoleSettings(**{"token": None, "auth_enabled": False, **console})
    app = create_app(
        settings=agent,
        console_settings=settings,
        inspector_factory=lambda: Inspector(db, agent, max_rows=settings.max_rows),
        certificate=certificate,
    )
    return TestClient(app)


@pytest.fixture
def client(db) -> TestClient:
    return _client(db)


# ---------------------------------------------------------------------------
# Service furniture
# ---------------------------------------------------------------------------


def test_the_root_says_where_to_go(client):
    body = client.get("/").json()
    assert body["service"] == "nl2sql-console"
    assert body["endpoints"]["query"] == "POST /v1/query"
    assert body["docs"] == "/docs"


def test_health_is_only_the_process(client, db):
    db.connect_error = db.introspection_error = "down"
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_ready_means_the_database_answers_as_a_read_only_role(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["database"] == {"ok": True, "detail": "1 tables in public"}
    assert body["checks"]["role"] == {"ok": True, "detail": "nl2sql_reader, read-only"}


def test_a_database_that_is_down_is_not_ready(client, db):
    db.introspection_error = "connection refused"
    response = client.get("/readyz")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["database"] == {"ok": False, "detail": "connection refused"}
    assert checks["role"]["ok"] is False


def test_an_empty_schema_is_not_ready(client, db):
    db.tables = []
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["database"]["detail"] == "connected, but public has no tables"


def test_a_role_that_could_write_is_said_but_does_not_fail_readiness(client, db):
    """The fence holds either way; restarting a working console over it
    would fix nothing."""
    db.identity = {**db.identity, "role": "nl2sql", "can_write": True}
    response = client.get("/readyz")
    assert response.status_code == 200
    role = response.json()["checks"]["role"]
    assert role["ok"] is False
    assert "nl2sql, which is able to write to the retail tables" in role["detail"]


def test_readiness_carries_the_settings_warnings(client):
    assert any("CONSOLE_TOKEN" in note for note in client.get("/readyz").json()["warnings"])


def test_the_docs_can_be_switched_off(db):
    client = _client(db, docs_enabled=False)
    assert client.get("/docs").status_code == 404
    assert client.get("/").json()["docs"] is None


def test_an_unknown_path_is_the_error_envelope(client):
    response = client.get("/v1/nothing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# What it is connected to
# ---------------------------------------------------------------------------


def test_meta_names_the_database_the_role_and_the_agents_limits(client):
    body = client.get("/v1/meta").json()
    assert body["database"] == "nl2sql_retail"
    assert body["role"] == "nl2sql_reader"
    assert body["read_only"] is True
    assert body["tables"] == 1
    assert body["limits"] == {
        "statement_timeout_ms": 30000,
        "max_plan_cost": 1_000_000.0,
        "agent_max_rows": 50,
        "max_rows": 1000,
        "sample_rows": 3,
        "max_sql_length": MAX_SQL_LENGTH,
    }
    assert body["authentication"] == "none"
    assert body["tls"] == {"enabled": True, "self_signed": False}


@pytest.mark.parametrize(
    ("identity", "said"),
    [
        ({"superuser": True}, "which is a superuser"),
        ({"can_write": True}, "which is able to write to the retail tables"),
    ],
)
def test_meta_warns_about_a_role_that_could_write(client, db, identity, said):
    db.identity = {**db.identity, "role": "nl2sql", **identity}
    body = client.get("/v1/meta").json()
    assert body["read_only"] is False
    assert any(said in note for note in body["warnings"])


def test_meta_reports_the_certificate_it_presents(db):
    info = CertificateInfo(
        path="/etc/nl2sql/tls/server.crt",
        subject="CN=localhost",
        issuer="CN=localhost",
        not_before=dt.datetime.now(dt.timezone.utc),
        not_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30),
        hostnames=["nl2sql-console"],
        self_signed=True,
    )
    tls = _client(db, certificate=info).get("/v1/meta").json()["tls"]
    assert tls["self_signed"] is True
    assert tls["hostnames"] == ["nl2sql-console"]


def test_meta_needs_the_database(client, db):
    db.connect_error = "connection refused"
    response = client.get("/v1/meta")
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "database_unavailable",
        "message": "cannot reach the retail database: connection refused",
    }


def test_the_schema_is_the_agents_introspection(client):
    body = client.get("/v1/schema").json()
    assert body["db_schema"] == "public"
    table = body["tables"][0]
    assert table["name"] == "dim_store"
    assert table["comment"] == "One row per store."
    assert table["columns"][0] == {
        "name": "store_key",
        "data_type": "integer",
        "not_null": True,
        "comment": "Surrogate key.",
    }
    assert table["constraints"] == ["PRIMARY KEY (store_key)"]


def test_the_prompt_is_the_agents_block_for_one_table(client):
    body = client.get("/v1/schema/dim_store/prompt").json()
    assert body["table"] == "dim_store"
    assert body["sample_rows"] == 3
    assert body["text"].startswith("=== dim_store ===")


def test_the_prompt_of_a_table_that_is_not_there_is_a_404(client):
    response = client.get("/v1/schema/gone/prompt")
    assert response.status_code == 404
    assert response.json()["error"] == {"code": "unknown_table", "message": "gone is not a table in public"}


# ---------------------------------------------------------------------------
# A query
# ---------------------------------------------------------------------------


def test_a_query_comes_back_with_its_verdict_rows_and_plan(client):
    body = client.post("/v1/query", json={"sql": "SELECT store_key, store_name FROM dim_store"}).json()
    assert body["mode"] == "run"
    assert body["executed"] is True
    assert body["agent"] == {"accepted": True, "stage": None, "issues": [], "notes": []}
    assert body["columns"] == [
        {"name": "store_key", "data_type": "integer"},
        {"name": "store_name", "data_type": "character varying"},
    ]
    assert body["rows"] == [[1, "Ashland"], [2, "Salem"]]
    assert body["row_count"] == 2
    assert body["max_rows"] == 1000
    assert body["plan_cost"] == 1.4
    assert body["max_plan_cost"] == 1_000_000.0


def test_a_refused_query_is_an_answer_not_an_error(client):
    response = client.post(
        "/v1/query", json={"sql": "WITH d AS (DELETE FROM dim_store RETURNING *) SELECT * FROM d"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert body["agent"]["stage"] == "static"
    assert body["agent"]["issues"][0]["stage"] == "static"
    assert body["plan"] is None


@pytest.mark.parametrize("mode", ["plan", "analyze"])
def test_the_mode_is_passed_through(client, mode):
    assert client.post("/v1/query", json={"sql": "SELECT 1", "mode": mode}).json()["mode"] == mode


@pytest.mark.parametrize(
    "body",
    [
        {"sql": ""},
        {"sql": "x" * (MAX_SQL_LENGTH + 1)},
        {"sql": "SELECT 1", "mode": "execute"},
        {"sql": "SELECT 1", "principal": "nl2sql"},
        {},
    ],
    ids=["empty", "too-long", "unknown-mode", "extra-field", "missing"],
)
def test_a_body_that_is_not_a_query_is_refused_with_the_reason(client, body):
    response = client.post("/v1/query", json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["detail"]["errors"]


def test_a_query_against_a_database_that_is_down_is_a_503(client, db):
    db.connect_error = "connection refused"
    response = client.post("/v1/query", json={"sql": "SELECT 1"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


# ---------------------------------------------------------------------------
# Who may call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/v1/meta"), ("get", "/v1/schema"), ("get", "/v1/schema/dim_store/prompt"), ("post", "/v1/query")],
)
def test_every_console_route_needs_the_token_when_one_is_set(db, method, path):
    client = _client(db, token="s3cret")
    kwargs = {"json": {"sql": "SELECT 1"}} if method == "post" else {}

    refused = getattr(client, method)(path, **kwargs)
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "unauthorized"
    assert refused.headers["WWW-Authenticate"] == "Bearer"

    wrong = getattr(client, method)(path, headers={"Authorization": "Bearer nope"}, **kwargs)
    assert wrong.status_code == 401

    for headers in ({"Authorization": "Bearer s3cret"}, {"X-API-Key": "s3cret"}):
        assert getattr(client, method)(path, headers=headers, **kwargs).status_code == 200


@pytest.mark.parametrize("path", ["/", "/healthz", "/readyz", "/openapi.json"])
def test_health_and_the_contract_need_no_token(db, path):
    assert _client(db, token="s3cret").get(path).status_code == 200


def test_no_cross_origin_browser_is_answered_unless_named(client):
    """A SQL runner any page in the browser could call is not something to
    offer without being asked."""
    response = client.options(
        "/v1/query",
        headers={"Origin": "https://elsewhere.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_a_named_origin_is_answered_with_credentials(db):
    client = _client(db, cors_origins=("https://gui.example",))
    response = client.options(
        "/v1/query",
        headers={"Origin": "https://gui.example", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "https://gui.example"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_a_wildcard_origin_is_answered_without_credentials(db):
    client = _client(db, cors_origins=("*",))
    response = client.options(
        "/v1/query",
        headers={"Origin": "https://any.example", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


# ---------------------------------------------------------------------------
# The default database
# ---------------------------------------------------------------------------


def test_by_default_it_reads_the_agents_database_with_the_agents_limits():
    """Built from the agent's own settings, and without a connection: the
    engine is a pool that opens nothing until the first query."""
    agent = Settings(
        database_url="postgresql+psycopg://nl2sql_reader:pw@127.0.0.1:1/nl2sql_retail",
        db_schema="analytics",
        max_rows=7,
    )
    app = create_app(settings=agent, console_settings=ConsoleSettings(auth_enabled=False, max_rows=11))
    inspector = app.state.inspector

    assert inspector.agent is agent
    assert inspector.max_rows == 11
    assert inspector.db._schema == "analytics"
    assert inspector.db._max_rows == 7
    assert inspector.db.engine.url.render_as_string(hide_password=False) == agent.database_url


def test_settings_come_from_the_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("CONSOLE_MAX_ROWS", "12")
    monkeypatch.setenv("MAX_PLAN_COST", "99")
    app = create_app(inspector_factory=lambda: None)
    assert app.state.console_settings.max_rows == 12
    assert app.state.settings.max_plan_cost == 99.0
