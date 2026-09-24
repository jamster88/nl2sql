"""Fixtures for the REST API tests: no Ollama, no Postgres, no container.

The whole HTTP surface is exercised against a scripted runner, which is what
makes these run on every `pytest` rather than only behind `--run-docker`.
The runner stands in for `Nl2SqlAgent.run`, so everything between the socket
and that call -- routing, authentication, the job lifecycle, the event
stream, the translation into the published shape -- is real code.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient
from nl2sql_agent.api.app import create_app
from nl2sql_agent.api.jobs import JobStore
from nl2sql_agent.api.settings import ApiSettings
from nl2sql_agent.config import Settings

#: A finished run, in the shape `Nl2SqlAgent.run` returns. Plain dicts and
#: plain values: the dataclasses are exercised in test_translate.py, and a
#: route test should fail for route reasons.
ANSWERED = {
    "question": "how many stores?",
    "verdict": "proceed",
    "intent": "aggregate",
    "selected_tables": ["dim_store"],
    "sql": "SELECT count(*) AS n FROM dim_store",
    "attempts": 1,
    "plan_cost": 12.5,
    "result": {"columns": ["n"], "rows": [[42]], "truncated": False},
    "narrative": "There are 42 stores.",
    "answer": "There are 42 stores.",
    "error": None,
    "trace": [{"node": "finish", "ms": 1.0, "model_calls": 0, "detail": ""}],
}

STEPS = ("supervise", "retrieve_schema", "generate_sql", "execute_query", "finish")


def make_runner(
    *,
    state: dict[str, Any] | None = None,
    steps: tuple[str, ...] = STEPS,
    raises: BaseException | None = None,
    gate: threading.Event | None = None,
) -> Callable:
    """A stand-in for the pipeline.

    `gate` is how a test holds a job in the running state long enough to
    observe it: the runner blocks on it until the test lets go.
    """

    def runner(question: str, principal: str | None, on_progress) -> dict:
        for step in steps:
            on_progress(step, f"{step} for {question}")
        if gate is not None:
            gate.wait(timeout=10)
        if raises is not None:
            raise raises
        answered = dict(state if state is not None else ANSWERED)
        answered["question"] = question
        if principal:
            answered["principal"] = principal
        return answered

    return runner


@pytest.fixture
def api_settings() -> ApiSettings:
    """Fast timings, so a stream test is not a five-minute test."""
    return ApiSettings(
        token=None,
        tls_enabled=False,
        max_concurrency=2,
        event_stream_timeout_seconds=5.0,
        keepalive_seconds=0.2,
        max_wait_seconds=10.0,
    )


@pytest.fixture
def make_client(api_settings: ApiSettings):
    """Build a client over a given runner and settings, closing the store after."""
    created: list[JobStore] = []

    def _make(
        runner: Callable | None = None,
        *,
        api: ApiSettings | None = None,
        settings: Settings | None = None,
        agent_factory: Callable | None = None,
        feedback=None,
        **store_kwargs,
    ) -> TestClient:
        api = api or api_settings
        store = None
        if agent_factory is None:
            store = JobStore(
                runner or make_runner(),
                max_concurrency=api.max_concurrency,
                **store_kwargs,
            )
            created.append(store)
        app = create_app(
            settings=settings or Settings(),
            api_settings=api,
            store=store,
            agent_factory=agent_factory,
            feedback=feedback,
        )
        return TestClient(app)

    yield _make
    for store in created:
        store.shutdown()


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def ask(client: TestClient, question: str = "how many stores?", **kwargs) -> dict:
    """POST a question and wait for it, the way most tests want it."""
    response = client.post(
        "/v1/questions", json={"question": question, **kwargs}, params={"wait": 10}
    )
    assert response.status_code in (200, 202), response.text
    return response.json()


class FakeDb:
    """Just enough database for the readiness and metadata routes."""

    def __init__(self, tables: list[str] | None = None) -> None:
        self.tables = tables if tables is not None else [
            "dim_store", "dim_product", "fact_pos_retail_sales"
        ]

    def table_names(self) -> list[str]:
        return list(self.tables)


class FakeAgent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.db = FakeDb()

    def run(self, question: str, *, principal=None, on_progress=None) -> dict:
        if on_progress:
            on_progress("finish", "done")
        return dict(ANSWERED, question=question)


@pytest.fixture
def fake_agent() -> FakeAgent:
    return FakeAgent()
