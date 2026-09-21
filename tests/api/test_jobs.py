"""The job store: what happens to a question between POST and an answer.

Everything here is about time and threads, which is where an HTTP server in
front of a one-minute pipeline actually goes wrong. Three properties carry
the weight:

* a caller watching one job never sees another caller's progress;
* nothing unbounded accumulates, because an endpoint can be called in a loop;
* a job that fails is a recorded failure, not a dead worker thread.
"""

from __future__ import annotations

import datetime as dt
import threading
import time

import pytest
from nl2sql_agent.api.jobs import Job, JobStore, ProgressRecord

from .conftest import make_runner


@pytest.fixture
def store():
    created: list[JobStore] = []

    def _make(runner=None, **kwargs) -> JobStore:
        made = JobStore(runner or make_runner(), **kwargs)
        created.append(made)
        return made

    yield _make
    for store in created:
        store.shutdown()


def finished(store: JobStore, job: Job, timeout: float = 5.0) -> Job:
    assert store.wait(job, timeout), f"job stayed {job.status}"
    return job


# ---------------------------------------------------------------------------
# One question
# ---------------------------------------------------------------------------


def test_a_submitted_question_runs_and_records_its_answer(store):
    jobs = store()
    job = finished(jobs, jobs.submit("how many stores?"))
    assert job.status == "succeeded"
    assert job.state is not None and job.state["question"] == "how many stores?"
    assert job.error is None


def test_a_job_is_queued_the_moment_it_is_created(store):
    """The POST returns before the work starts, so the first thing a client
    ever sees has to be a valid job document.
    """
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_concurrency=1)
    job = jobs.submit("q")
    assert job.status in ("queued", "running")
    assert job.id and job.created_at.tzinfo is dt.timezone.utc
    gate.set()
    finished(jobs, job)


def test_every_progress_callback_becomes_a_numbered_event(store):
    jobs = store()
    job = finished(jobs, jobs.submit("q"))
    assert [e.step for e in job.progress] == [
        "supervise", "retrieve_schema", "generate_sql", "execute_query", "finish"
    ]
    assert [e.seq for e in job.progress] == [1, 2, 3, 4, 5]


def test_timings_are_only_reported_once_there_are_two_of_them(store):
    jobs = store()
    job = jobs.submit("q")
    finished(jobs, job)
    assert job.started_at is not None and job.finished_at is not None
    assert job.duration_ms is not None and job.duration_ms >= 0
    assert Job(id="x", question="q").duration_ms is None


def test_an_exception_inside_the_pipeline_is_a_failed_job_not_a_lost_worker(store):
    jobs = store(make_runner(raises=RuntimeError("ollama went away")))
    job = finished(jobs, jobs.submit("q"))
    assert job.status == "failed"
    assert job.error == "RuntimeError: ollama went away"
    # The pool survives it: the next question still runs.
    assert finished(jobs, jobs.submit("q2")).status == "failed"


def test_a_pipeline_that_gave_up_is_a_failed_job_with_its_state_attached(store):
    """The agent reports exhausting its retry budget through `error` in the
    state, not by raising. A GUI still wants the attempts and the trace.
    """
    jobs = store(make_runner(state={"error": "gave up after 4 attempts", "sql": "SELECT 1"}))
    job = finished(jobs, jobs.submit("q"))
    assert job.status == "failed"
    assert job.error == "gave up after 4 attempts"
    assert job.state is not None and job.state["sql"] == "SELECT 1"


# ---------------------------------------------------------------------------
# Several at once
# ---------------------------------------------------------------------------


def test_two_callers_progress_does_not_cross(store):
    jobs = store(max_concurrency=2)
    first = jobs.submit("first question")
    second = jobs.submit("second question")
    finished(jobs, first)
    finished(jobs, second)
    assert all("first question" in e.detail for e in first.progress)
    assert all("second question" in e.detail for e in second.progress)


def test_concurrency_is_bounded_by_the_setting(store):
    """A pool of one is a real queue: the second question does not start
    until the first is done, which is what the setting promises.
    """
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_concurrency=1)
    first = jobs.submit("a")
    second = jobs.submit("b")
    for _ in range(100):
        if first.status == "running":
            break
        time.sleep(0.01)
    assert first.status == "running"
    assert second.status == "queued"
    gate.set()
    finished(jobs, first)
    finished(jobs, second)


# ---------------------------------------------------------------------------
# Waiting and watching
# ---------------------------------------------------------------------------


def test_waiting_returns_false_rather_than_blocking_forever(store):
    gate = threading.Event()
    jobs = store(make_runner(gate=gate))
    job = jobs.submit("q")
    assert jobs.wait(job, timeout=0.05) is False
    gate.set()
    assert jobs.wait(job, timeout=5) is True


def test_waiting_on_a_finished_job_returns_at_once(store):
    jobs = store()
    job = finished(jobs, jobs.submit("q"))
    started = time.monotonic()
    assert jobs.wait(job, timeout=5) is True
    assert time.monotonic() - started < 0.5


def test_the_stream_ends_with_the_finished_job(store):
    jobs = store()
    job = jobs.submit("q")
    chunks = list(jobs.stream(job, timeout=5, keepalive=0.2))
    assert chunks[-1].kind == "done"
    assert chunks[-1].job is job
    assert [c.event.step for c in chunks if c.kind == "progress"] == [
        "supervise", "retrieve_schema", "generate_sql", "execute_query", "finish"
    ]


def test_the_stream_reports_each_status_change(store):
    jobs = store()
    job = jobs.submit("q")
    statuses = [c.status for c in jobs.stream(job, timeout=5, keepalive=0.2) if c.kind == "status"]
    assert statuses[-1] == "succeeded"


def test_a_reconnecting_client_can_resume_from_where_it_stopped(store):
    """`from_seq` is what makes a dropped connection cost nothing: the GUI
    does not redraw five steps it already drew.
    """
    jobs = store()
    job = finished(jobs, jobs.submit("q"))
    resumed = [c.event.seq for c in jobs.stream(job, from_seq=3, timeout=2) if c.kind == "progress"]
    assert resumed == [4, 5]


def test_streaming_a_job_that_already_finished_still_delivers_everything(store):
    """A client that connects late must not be given an empty stream that
    never ends -- it gets the backlog and then the done event.
    """
    jobs = store()
    job = finished(jobs, jobs.submit("q"))
    chunks = list(jobs.stream(job, timeout=2))
    assert len([c for c in chunks if c.kind == "progress"]) == 5
    assert chunks[-1].kind == "done"


def test_an_idle_stream_emits_keepalives_rather_than_silence(store):
    """Proxies and load balancers drop a connection that sends nothing, so
    an agent thinking for 60 seconds has to still be producing bytes.
    """
    gate = threading.Event()
    jobs = store(make_runner(steps=(), gate=gate))
    job = jobs.submit("q")
    seen = []
    for chunk in jobs.stream(job, timeout=2, keepalive=0.05):
        seen.append(chunk.kind)
        if seen.count("keepalive") >= 2:
            break
    gate.set()
    assert seen.count("keepalive") >= 2


def test_a_stream_the_server_is_done_holding_open_says_so(store):
    """Rather than closing silently, which a client cannot tell apart from a
    network failure.
    """
    gate = threading.Event()
    jobs = store(make_runner(steps=(), gate=gate))
    job = jobs.submit("q")
    chunks = list(jobs.stream(job, timeout=0.3, keepalive=0.05))
    gate.set()
    assert chunks[-1].kind == "timeout"


# ---------------------------------------------------------------------------
# Cancelling and forgetting
# ---------------------------------------------------------------------------


def test_a_queued_job_is_cancelled_and_never_runs(store):
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_concurrency=1)
    first = jobs.submit("a")
    second = jobs.submit("b")
    assert jobs.cancel(second.id) == "cancelled"
    gate.set()
    finished(jobs, first)
    assert jobs.wait(second, timeout=5)
    assert second.status == "cancelled"
    assert second.progress == [], "a cancelled job ran anyway"


def test_a_running_job_is_reported_as_uncancellable_rather_than_pretended_at(store):
    """There is no honest way to interrupt a LangGraph invocation holding a
    database cursor and an HTTP call. Saying so beats marking it cancelled
    and letting it finish.
    """
    gate = threading.Event()
    jobs = store(make_runner(gate=gate))
    job = jobs.submit("q")
    for _ in range(100):
        if job.status == "running":
            break
        time.sleep(0.01)
    assert jobs.cancel(job.id) == "running"
    gate.set()
    finished(jobs, job)


def test_a_finished_job_is_forgotten_on_request(store):
    jobs = store()
    job = finished(jobs, jobs.submit("q"))
    assert jobs.cancel(job.id) == "forgotten"
    assert jobs.get(job.id) is None


def test_cancelling_something_that_was_never_there(store):
    assert store().cancel("nope") == "missing"


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_finished_jobs_are_forgotten_after_their_time(store):
    jobs = store(ttl_seconds=0.0)
    job = finished(jobs, jobs.submit("q"))
    time.sleep(0.01)
    assert jobs.get(job.id) is None


def test_the_store_keeps_a_bounded_number_of_jobs(store):
    jobs = store(max_jobs=3)
    submitted = [jobs.submit(f"q{i}") for i in range(10)]
    for job in submitted:
        finished(jobs, job)
    assert len(jobs.list(limit=100)) <= 3


def test_pruning_never_drops_a_job_that_is_still_running(store):
    """The one job whose answer somebody is paying for right now."""
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_jobs=1, max_concurrency=1)
    running = jobs.submit("still going")
    for _ in range(100):
        if running.status == "running":
            break
        time.sleep(0.01)
    for _ in range(5):
        jobs.submit("more")
    assert jobs.get(running.id) is running
    gate.set()


def test_listing_is_newest_first_and_limited(store):
    jobs = store()
    made = [finished(jobs, jobs.submit(f"q{i}")) for i in range(5)]
    listed = jobs.list(limit=2)
    assert [job.id for job in listed] == [made[-1].id, made[-2].id]


def test_a_shut_down_store_refuses_new_work(store):
    jobs = store()
    jobs.shutdown()
    with pytest.raises(RuntimeError, match="shut down"):
        jobs.submit("q")


def test_a_progress_record_is_plain_data():
    """It crosses a thread boundary and is serialised; anything clever here
    would be a problem in one of those two places.
    """
    record = ProgressRecord(seq=1, step="finish", detail="", at=dt.datetime.now(dt.timezone.utc))
    assert (record.seq, record.step) == (1, "finish")


def test_waiting_with_no_timeout_blocks_until_the_answer_arrives(store):
    """The in-process form, used by `?wait=` once the value has been capped."""
    gate = threading.Event()
    jobs = store(make_runner(gate=gate))
    job = jobs.submit("q")
    threading.Timer(0.05, gate.set).start()
    assert jobs.wait(job) is True
    assert job.status == "succeeded"


def test_a_wakeup_with_nothing_behind_it_does_not_end_the_stream(store):
    """Several streams can wait on one job's condition, and every progress
    callback wakes all of them. One that wakes with nothing new to send has
    to go back to waiting rather than closing the connection.
    """
    gate = threading.Event()
    jobs = store(make_runner(steps=("supervise",), gate=gate))
    job = jobs.submit("q")
    stream = jobs.stream(job, timeout=5, keepalive=5.0)
    assert next(stream).kind in ("progress", "status")

    def poke() -> None:
        time.sleep(0.05)
        with job.condition:
            job.condition.notify_all()
        time.sleep(0.05)
        gate.set()

    threading.Thread(target=poke, daemon=True).start()
    assert [chunk.kind for chunk in stream][-1] == "done"
