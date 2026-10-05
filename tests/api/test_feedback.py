"""Capturing a verdict on an answer.

Two routes, and what they are *not* allowed to do is most of the point. The
snapshot is taken from the job the server already holds rather than from the
request, so a client cannot stage evidence of an answer this server never
gave; and the sink it writes through holds a role that cannot read a
submission back. The privileges are tested against a real Postgres in
`tests/review/test_store_live.py`; these are about the HTTP surface.
"""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest
from nl2sql_agent.api.feedback import (
    AlreadyReviewed,
    Capture,
    DisabledSink,
    FeedbackUnavailable,
    PostgresSink,
    build_sink,
)
from nl2sql_agent.api.settings import ApiSettings

from .conftest import ANSWERED, ask, make_runner


class FakeSink:
    """A sink that records, and can be told to misbehave."""

    def __init__(self, *, fail=None, reachable=True) -> None:
        self.captures: list[Capture] = []
        self.withdrawn: list[str] = []
        self.fail = fail
        self.reachable = reachable
        self.removes = True

    def record(self, capture: Capture) -> str:
        if self.fail is not None:
            raise self.fail
        self.captures.append(capture)
        return "sub-1"

    def withdraw(self, job_id: str) -> bool:
        self.withdrawn.append(job_id)
        return self.removes

    def check(self) -> tuple[bool, str]:
        return (True, "reachable") if self.reachable else (False, "not configured")


@pytest.fixture
def sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
def client(make_client, sink):
    return make_client(feedback=sink)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_a_verdict_is_recorded_against_a_finished_job(client, sink):
    job = ask(client)
    response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})

    assert response.status_code == 201
    body = response.json()
    assert body == {"id": "sub-1", "job_id": job["id"], "verdict": "yes", "comment": "", "state": "pending"}
    assert len(sink.captures) == 1


@pytest.mark.parametrize("verdict", ["yes", "no", "incomplete"])
def test_each_of_the_three_verdicts_is_recorded(client, sink, verdict):
    """Correct, wrong, and correct but incomplete -- the three buttons."""
    job = ask(client)
    response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": verdict})
    assert response.status_code == 201
    assert response.json()["verdict"] == verdict
    assert sink.captures[-1].verdict == verdict


def test_the_verdicts_the_api_takes_are_the_ones_the_staging_table_allows():
    """The API and the review service live in different images; a verdict
    one accepts and the other's CHECK refuses is a 503 at the worst moment."""
    from typing import get_args

    from nl2sql_agent.api.models import Verdict
    from nl2sql_review.store import VERDICTS

    assert get_args(Verdict) == VERDICTS


def test_the_snapshot_is_taken_from_the_job_not_from_the_request(client, sink):
    """A client cannot describe an answer this server did not give."""
    job = ask(client)
    client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "no", "comment": "wrong"})

    captured = sink.captures[0]
    assert captured.job_id == job["id"]
    assert captured.question == "how many stores?"
    assert captured.sql_code == ANSWERED["sql"]
    assert captured.answer == ANSWERED["answer"]
    assert captured.narrative == ANSWERED["narrative"]
    assert captured.intent == "aggregate"
    assert captured.tables == "dim_store"
    assert captured.row_count == 1
    assert captured.columns == ("n",)
    assert captured.comment == "wrong"
    assert captured.agent_version


def test_a_run_with_no_result_is_captured_without_one(make_client, sink):
    refused = dict(ANSWERED, verdict="refuse", result=None, sql="", answer="Out of scope.")
    client = make_client(make_runner(state=refused), feedback=sink)
    job = ask(client)

    client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "no"})
    captured = sink.captures[0]
    assert captured.row_count == 0
    assert captured.columns == ()
    assert captured.sql_code == ""


def test_a_failed_run_can_still_be_judged(make_client, sink):
    client = make_client(make_runner(raises=RuntimeError("the model went away")), feedback=sink)
    job = ask(client)
    assert job["status"] == "failed"

    response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "no"})
    assert response.status_code == 201
    # A "no" on a failure is among the most useful feedback there is.
    assert sink.captures[0].verdict == "no"


def test_a_comment_is_optional(client, sink):
    job = ask(client)
    client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})
    assert sink.captures[0].comment == ""


# ---------------------------------------------------------------------------
# What is refused
# ---------------------------------------------------------------------------


def test_a_question_still_running_cannot_be_judged(make_client, sink):
    """There is nothing to have an opinion about yet.

    And the snapshot taken now would be of a half-finished run, which is the
    one thing a golden pair must never be built from.
    """
    gate = threading.Event()
    client = make_client(make_runner(gate=gate), feedback=sink)
    try:
        job = client.post("/v1/questions", json={"question": "slow one"}).json()
        response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "job_running"
        assert sink.captures == []
    finally:
        gate.set()


def test_an_unknown_job_is_a_404(client):
    response = client.post("/v1/questions/nope/feedback", json={"verdict": "yes"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("body", [{}, {"verdict": "maybe"}, {"verdict": "yes", "extra": 1}])
def test_a_malformed_verdict_is_refused(client, body):
    job = ask(client)
    response = client.post(f"/v1/questions/{job['id']}/feedback", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_an_over_long_comment_is_refused(client):
    job = ask(client)
    response = client.post(
        f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes", "comment": "x" * 5000}
    )
    assert response.status_code == 422


def test_a_verdict_already_reviewed_is_a_conflict(make_client):
    sink = FakeSink(fail=AlreadyReviewed("job-1"))
    client = make_client(feedback=sink)
    job = ask(client)

    response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "no"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_reviewed"
    assert "already been reviewed" in response.json()["error"]["message"]


def test_an_unconfigured_server_says_so_rather_than_failing_oddly(make_client):
    client = make_client(feedback=DisabledSink())
    job = ask(client)

    response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "feedback_unavailable"
    assert "API_FEEDBACK_DB_URL" in response.json()["error"]["message"]


def test_a_staging_database_that_is_down_is_a_503(make_client):
    sink = FakeSink(fail=FeedbackUnavailable("cannot record feedback: OperationalError"))
    client = make_client(feedback=sink)
    job = ask(client)

    response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Withdrawing
# ---------------------------------------------------------------------------


def test_a_verdict_can_be_withdrawn(client, sink):
    job = ask(client)
    client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})

    response = client.delete(f"/v1/questions/{job['id']}/feedback")
    assert response.status_code == 204
    assert sink.withdrawn == [job["id"]]


def test_withdrawing_something_that_cannot_be_withdrawn_is_a_404(client, sink):
    sink.removes = False
    job = ask(client)
    response = client.delete(f"/v1/questions/{job['id']}/feedback")

    assert response.status_code == 404
    assert "can still be withdrawn" in response.json()["error"]["message"]


def test_withdrawing_needs_no_job_to_still_exist(client, sink):
    """The verdict outlives the job, so taking it back has to as well."""
    response = client.delete("/v1/questions/long-forgotten/feedback")
    assert response.status_code == 204
    assert sink.withdrawn == ["long-forgotten"]


def test_withdrawing_from_an_unconfigured_server_is_a_503(make_client):
    client = make_client(feedback=DisabledSink())
    assert client.delete("/v1/questions/x/feedback").status_code == 503


# ---------------------------------------------------------------------------
# On the answer's MLflow trace, as well
# ---------------------------------------------------------------------------


def _traced(make_client, sink, *, answers: bool = True, job_id: str | None = None):
    """A client whose one answer has an MLflow trace, and the fake holding it."""
    from nl2sql_agent.config import Settings
    from nl2sql_agent.tracing import Tracer

    from tests.fake_mlflow import FakeMlflow

    mlflow = FakeMlflow()
    with mlflow.start_span("nl2sql") as root:
        trace_id = root.trace_id
    if job_id:
        mlflow.traces[trace_id].tags["nl2sql.job_id"] = job_id

    def probe(uri):
        if not answers:
            raise ConnectionRefusedError("connection refused")

    tracer = Tracer(Settings(mlflow_tracking_uri="http://nl2sql-mlflow:5000"), client=mlflow, probe=probe)
    runner = make_runner(state=dict(ANSWERED, trace_id=trace_id))
    return make_client(runner, feedback=sink, tracer=tracer), mlflow, trace_id


def test_a_verdict_is_recorded_on_the_answers_trace_too(make_client, sink):
    client, mlflow, trace_id = _traced(make_client, sink)
    job = ask(client)
    response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "no", "comment": "wrong key"})

    assert response.status_code == 201
    [verdict] = mlflow.traces[trace_id].assessments
    assert (verdict.name, verdict.value, verdict.rationale) == ("verdict", "no", "wrong key")
    assert verdict.source.source_type == "HUMAN"


def test_voting_again_replaces_the_verdict_on_the_trace_as_it_does_in_staging(make_client, sink):
    client, mlflow, trace_id = _traced(make_client, sink)
    job = ask(client)
    client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "no"})
    client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})

    assert [(a.value, a.valid) for a in mlflow.traces[trace_id].assessments] == [("no", False), ("yes", True)]


def test_a_withdrawn_verdict_comes_off_the_trace(make_client, sink):
    client, mlflow, trace_id = _traced(make_client, sink)
    job = ask(client)
    client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})

    assert client.delete(f"/v1/questions/{job['id']}/feedback").status_code == 204
    assert mlflow.traces[trace_id].assessments == []


def test_a_verdict_withdrawn_after_its_job_is_forgotten_is_found_by_the_jobs_tag(make_client, sink):
    client, mlflow, trace_id = _traced(make_client, sink, job_id="feedbeef")
    mlflow.log_feedback(trace_id=trace_id, name="verdict", value="yes")

    assert client.delete("/v1/questions/feedbeef/feedback").status_code == 204
    assert sink.withdrawn == ["feedbeef"]
    assert mlflow.traces[trace_id].assessments == []


def test_mlflow_being_down_costs_the_trace_its_verdict_and_nothing_else(make_client, sink):
    client, mlflow, trace_id = _traced(make_client, sink, answers=False)
    job = ask(client)
    response = client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"})

    assert response.status_code == 201
    assert len(sink.captures) == 1
    assert mlflow.traces[trace_id].assessments == []
    assert client.delete(f"/v1/questions/{job['id']}/feedback").status_code == 204


def test_a_verdict_staging_would_not_take_is_not_put_on_the_trace_either(make_client):
    sink = FakeSink(fail=FeedbackUnavailable("cannot record feedback: OperationalError"))
    client, mlflow, trace_id = _traced(make_client, sink)
    job = ask(client)

    assert client.post(f"/v1/questions/{job['id']}/feedback", json={"verdict": "yes"}).status_code == 503
    assert mlflow.traces[trace_id].assessments == []


def test_the_server_shares_one_tracer_between_its_agent_and_its_verdicts(monkeypatch, api_settings):
    from nl2sql_agent.api import app as app_module
    from nl2sql_agent.config import Settings

    built = {}

    def agent(settings, *, tracer):
        built["tracer"] = tracer
        return object()

    monkeypatch.setattr(app_module, "Nl2SqlAgent", agent)
    app = app_module.create_app(settings=Settings(), api_settings=api_settings, feedback=FakeSink())
    try:
        app.state.agent.get()
        assert built["tracer"] is app.state.tracer
    finally:
        app.state.jobs.shutdown()


# ---------------------------------------------------------------------------
# What the rest of the API says about it
# ---------------------------------------------------------------------------


def test_meta_says_whether_feedback_is_accepted(make_client):
    assert make_client(feedback=FakeSink()).get("/v1/meta").json()["feedback"] is True
    assert make_client(feedback=DisabledSink()).get("/v1/meta").json()["feedback"] is False


def test_readiness_reports_feedback_without_failing_over_it(make_client, fake_agent):
    """A server whose staging database is down can still answer questions.

    Failing readiness over it would have an orchestrator restart a working
    agent because an optional side channel was unavailable.
    """
    client = make_client(agent_factory=lambda: fake_agent, feedback=FakeSink(reachable=False))
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["checks"]["feedback"] == {"ok": False, "detail": "not configured"}


def test_the_routes_need_the_token_like_everything_else(make_client, api_settings):
    client = make_client(api=replace(api_settings, token="s3cret"), feedback=FakeSink())
    assert client.post("/v1/questions/x/feedback", json={"verdict": "yes"}).status_code == 401
    assert client.delete("/v1/questions/x/feedback").status_code == 401


def test_the_routes_are_in_the_openapi_document_even_when_unconfigured(make_client):
    """So a generated client does not change shape with the server's config."""
    document = make_client(feedback=DisabledSink()).get("/openapi.json").json()
    path = document["paths"]["/v1/questions/{job_id}/feedback"]
    assert set(path) == {"post", "delete"}


# ---------------------------------------------------------------------------
# The sinks themselves
# ---------------------------------------------------------------------------


def test_build_sink_chooses_by_configuration():
    assert isinstance(build_sink(None), DisabledSink)
    assert isinstance(build_sink(""), DisabledSink)
    assert isinstance(build_sink("postgresql://x/y"), PostgresSink)


def test_the_disabled_sink_refuses_with_a_reason():
    sink = DisabledSink()
    for call in (lambda: sink.record(Capture(job_id="j", verdict="yes", question="q")),
                 lambda: sink.withdraw("j")):
        with pytest.raises(FeedbackUnavailable, match="API_FEEDBACK_DB_URL"):
            call()
    assert sink.check() == (False, "not configured")


def test_the_capture_encodes_its_json_column():
    values = Capture(job_id="j", verdict="yes", question="q", columns=("a", "b")).values()
    assert values[-3] == '["a", "b"]'


def test_the_column_list_matches_the_schema_the_review_service_owns():
    """The two live in different images and cannot import each other.

    A column in one and not the other is either a field silently dropped at
    capture time or an INSERT that fails at runtime.
    """
    from nl2sql_agent.api.feedback import COLUMNS, TABLE
    from nl2sql_review.store import SUBMISSIONS, SUBMISSION_COLUMNS

    assert COLUMNS == SUBMISSION_COLUMNS
    assert TABLE == SUBMISSIONS


class FakeConnection:
    """Enough psycopg to drive PostgresSink without a database."""

    def __init__(self, *, rowcount=1, on_insert=None) -> None:
        self.statements: list[str] = []
        self.rowcount = rowcount
        self.on_insert = on_insert
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        self.statements.append(statement)
        if statement.startswith("INSERT") and self.on_insert is not None:
            raise self.on_insert
        self.rows = [(params[0],)] if statement.startswith("INSERT") else []
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_the_postgres_sink_deletes_before_inserting():
    """Not ON CONFLICT DO UPDATE, which needs table-level SELECT.

    Table-level SELECT would let the public process read the full text of
    every pending submission, which is the power the role exists to lack.
    """
    conn = FakeConnection()
    sink = PostgresSink("postgresql://x/y", connect=lambda url: conn)

    sink.record(Capture(job_id="j", verdict="yes", question="q"))
    assert conn.statements[0].startswith("DELETE FROM feedback_submissions")
    assert conn.statements[1].startswith("INSERT INTO feedback_submissions")
    assert "ON CONFLICT" not in conn.statements[1]
    assert conn.committed


class Duplicate(Exception):
    sqlstate = "23505"


def test_a_duplicate_key_means_the_verdict_was_already_reviewed():
    conn = FakeConnection(on_insert=Duplicate())
    sink = PostgresSink("postgresql://x/y", connect=lambda url: conn)

    with pytest.raises(AlreadyReviewed):
        sink.record(Capture(job_id="j", verdict="yes", question="q"))
    assert conn.rolled_back


def test_any_other_driver_failure_is_reported_as_unavailable():
    conn = FakeConnection(on_insert=RuntimeError("boom"))
    sink = PostgresSink("postgresql://x/y", connect=lambda url: conn)

    with pytest.raises(FeedbackUnavailable, match="RuntimeError"):
        sink.record(Capture(job_id="j", verdict="yes", question="q"))


def test_without_a_fake_the_sink_connects_with_psycopg_and_a_bounded_wait():
    """The one path every other test here replaces. A closed loopback port
    refuses at once, so this needs no database -- and what readiness reports
    is psycopg's own error, not a stand-in's."""
    ready, detail = PostgresSink("postgresql://nl2sql@127.0.0.1:9/feedback").check()
    assert ready is False
    assert detail.startswith("OperationalError:")


def test_a_withdrawal_reports_whether_it_removed_anything():
    removed = PostgresSink("postgresql://x/y", connect=lambda url: FakeConnection(rowcount=1))
    assert removed.withdraw("j") is True

    nothing = PostgresSink("postgresql://x/y", connect=lambda url: FakeConnection(rowcount=0))
    assert nothing.withdraw("j") is False


def test_a_withdrawal_that_throws_is_reported_as_unavailable():
    def explode(url):
        raise RuntimeError("no route to host")

    with pytest.raises(FeedbackUnavailable, match="cannot withdraw"):
        PostgresSink("postgresql://x/y", connect=explode).withdraw("j")


def test_the_check_reports_the_reason_rather_than_raising():
    def explode(url):
        raise RuntimeError("no route to host")

    ok, detail = PostgresSink("postgresql://x/y", connect=explode).check()
    assert ok is False
    assert "RuntimeError" in detail


def test_a_reachable_database_checks_out():
    assert PostgresSink("postgresql://x/y", connect=lambda url: FakeConnection()).check() == (
        True,
        "reachable",
    )


def test_a_settings_warning_names_the_open_combination():
    notes = ApiSettings(auth_enabled=False, feedback_db_url="postgresql://x/y", token=None).warnings()
    assert any("API_FEEDBACK_DB_URL is set while no API_TOKEN" in note for note in notes)
