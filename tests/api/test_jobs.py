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
from nl2sql_agent.api.jobs import Job, JobStore, ProgressRecord, QueueFull

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
    assert job.error == "the question could not be answered: RuntimeError"
    assert job.fault == "RuntimeError: ollama went away", "in its own words, for an operator (V6-32)"
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


def test_a_stream_with_no_timeout_keeps_alive_for_as_long_as_it_takes(store):
    """No deadline is the default for a client that will wait: keepalives,
    and never a timeout chunk."""
    gate = threading.Event()
    jobs = store(make_runner(steps=(), gate=gate))
    job = jobs.submit("q")
    seen = []
    for chunk in jobs.stream(job, keepalive=0.05):
        seen.append(chunk.kind)
        if seen.count("keepalive") >= 3:
            break
    gate.set()
    assert seen.count("keepalive") >= 3
    assert "timeout" not in seen


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


def _running(job: Job) -> None:
    for _ in range(200):
        if job.status == "running":
            return
        time.sleep(0.01)
    raise AssertionError(f"job stayed {job.status}")


def test_a_full_queue_refuses_a_question_rather_than_growing(store):
    """V6-13. Past `max_queued` waiting, a submission is refused with a time
    to come back, not accepted into a queue that only grows."""
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_concurrency=1, max_queued=2)
    _running(jobs.submit("running"))
    jobs.submit("waiting 1")
    jobs.submit("waiting 2")
    with pytest.raises(QueueFull, match="API_MAX_QUEUED=2") as refused:
        jobs.submit("one too many")
    assert refused.value.retry_after == 60
    gate.set()


def test_one_person_cannot_fill_the_queue_for_everyone(store):
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_concurrency=1, max_per_person=2)
    _running(jobs.submit("a1", owner="ann"))
    jobs.submit("a2", owner="ann")
    with pytest.raises(QueueFull, match="API_MAX_PER_PERSON=2"):
        jobs.submit("a3", owner="ann")
    assert jobs.submit("b1", owner="bob").status == "queued"
    gate.set()


def test_a_finished_question_gives_its_place_back(store):
    jobs = store(max_per_person=1)
    finished(jobs, jobs.submit("first", owner="ann"))
    finished(jobs, jobs.submit("second", owner="ann"))


def test_a_service_or_an_open_server_is_bounded_by_the_queue_alone(store):
    """With no signed-in person every caller is the same caller, so a per-
    person limit there would be a limit on the whole server."""
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_concurrency=1, max_per_person=1)
    made = [jobs.submit(f"q{i}") for i in range(4)]
    assert all(job.status in ("queued", "running") for job in made)
    gate.set()


def test_a_question_that_waited_too_long_is_failed_rather_than_run(store):
    gate = threading.Event()
    asked: list[str] = []
    runner = make_runner(gate=gate)

    def counting(question, principal, on_progress, turns=()):
        asked.append(question)
        return runner(question, principal, on_progress)

    jobs = store(counting, max_concurrency=1, queue_ttl_seconds=0.05)
    first = jobs.submit("first")
    _running(first)
    stale = jobs.submit("stale")
    time.sleep(0.1)
    gate.set()
    finished(jobs, stale)
    assert stale.status == "failed" and stale.error.startswith("expired: waited")
    assert "API_QUEUE_TTL_SECONDS" in stale.error
    assert asked == ["first"]


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


def test_a_jobs_run_carries_its_id_onto_the_trace(store):
    """The id is what a verdict on the job finds its trace by -- and, once
    the job is forgotten, the only thing it can find it by."""
    from nl2sql_agent import tracing

    seen = []

    def runner(question, principal, on_progress, turns=()):
        seen.append(dict(tracing._tags.get()))
        return {"question": question}

    jobs = store(runner)
    job = finished(jobs, jobs.submit("q"))
    assert seen == [{"nl2sql.entrypoint": "api", "nl2sql.job_id": job.id}]


# ---------------------------------------------------------------------------
# A question in a conversation (arch8, R4 and S2 of its risks by phase)
# ---------------------------------------------------------------------------


from nl2sql_agent.api.conversations import ConversationBusy, ConversationStore  # noqa: E402


def _admitted(jobs: JobStore, conversation: str = "c") -> Job:
    """A question in the conversation, which only a free one admits."""
    job = jobs.submit("next", conversation=conversation)
    assert job.turn is not None
    return job


def test_a_question_in_a_conversation_is_handed_the_turns_before_it(store):
    runner = make_runner()
    jobs = store(runner, conversations=ConversationStore(depth=5))
    first = finished(jobs, jobs.submit("how many stores?", conversation="c"))
    second = finished(jobs, jobs.submit("and by state?", conversation="c"))
    assert (first.conversation, first.turn, first.turns) == ("c", 1, ())
    assert second.turn == 2 and runner.turns == [(), second.turns]
    [turn] = second.turns
    assert (turn.question, turn.job_id) == ("how many stores?", first.id)


def test_with_no_conversation_store_the_id_is_kept_and_nothing_handed_on(store):
    runner = make_runner()
    jobs = store(runner)
    finished(jobs, jobs.submit("q1", conversation="c"))
    job = finished(jobs, jobs.submit("q2", conversation="c"))
    assert (job.conversation, job.turn, job.turns) == ("c", None, ())
    assert runner.turns == [(), ()] and jobs.conversations is None


def test_a_conversation_is_free_again_after_its_question_is_answered(store):
    jobs = store(conversations=ConversationStore(depth=5))
    finished(jobs, jobs.submit("q", conversation="c"))
    assert finished(jobs, _admitted(jobs)).turn == 2


def test_a_conversation_is_free_again_after_its_question_raised(store):
    jobs = store(make_runner(raises=RuntimeError("ollama went away")), conversations=ConversationStore(depth=5))
    finished(jobs, jobs.submit("q", conversation="c"))
    assert finished(jobs, _admitted(jobs)).turn == 1, "a question that raised made no turn"


def test_a_conversation_is_free_again_after_its_question_was_cancelled_while_queued(store):
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_concurrency=1, conversations=ConversationStore(depth=5))
    _running(jobs.submit("first", conversation="other"))
    queued = jobs.submit("q", conversation="c")
    assert jobs.cancel(queued.id) == "cancelled"
    again = _admitted(jobs)
    gate.set()
    assert finished(jobs, again).turn == 1 and finished(jobs, queued).status == "cancelled"


def test_a_conversation_is_free_again_after_its_question_expired_in_the_queue(store):
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), max_concurrency=1, queue_ttl_seconds=0.05,
                 conversations=ConversationStore(depth=5))
    _running(jobs.submit("first", conversation="other"))
    stale = jobs.submit("q", conversation="c")
    time.sleep(0.1)
    gate.set()
    assert finished(jobs, stale).error.startswith("expired")
    assert finished(jobs, _admitted(jobs)).turn == 1


def test_a_conversation_whose_question_is_in_flight_refuses_the_next(store):
    gate = threading.Event()
    jobs = store(make_runner(gate=gate), conversations=ConversationStore(depth=5))
    first = jobs.submit("q", conversation="c")
    with pytest.raises(ConversationBusy):
        jobs.submit("next", conversation="c")
    assert len(jobs.list()) == 1, "the refused question is not a job"
    gate.set()
    finished(jobs, first)


def test_a_question_the_queue_refuses_leaves_its_conversation_free(store):
    """The 429 is decided before the conversation is marked busy, so a
    refused question never holds its conversation."""
    gate = threading.Event()
    conversations = ConversationStore(depth=5)
    jobs = store(make_runner(gate=gate), max_concurrency=1, max_queued=1, conversations=conversations)
    running = jobs.submit("running")
    _running(running)
    waiting = jobs.submit("waiting")
    with pytest.raises(QueueFull):
        jobs.submit("refused", conversation="c")
    assert conversations.count() == 0
    gate.set()
    finished(jobs, running)
    finished(jobs, waiting)
    assert finished(jobs, _admitted(jobs)).turn == 1


def test_a_question_that_could_not_be_scheduled_frees_its_conversation(store):
    conversations = ConversationStore(depth=5)
    jobs = store(conversations=conversations)
    jobs._pool.shutdown()
    with pytest.raises(RuntimeError, match="after shutdown"):
        jobs.submit("q", conversation="c")
    assert conversations.begin(None, "c", "probe") == ((), 1)


def test_the_conversation_is_free_before_anyone_is_told_the_job_has_ended(store):
    """A client that asks its follow-up the moment `?wait=` hands it the
    answer must find the conversation free and the turn in it, not a 409
    for a question that has been answered."""
    gate = threading.Event()
    seen: list[str] = []

    class Watching(ConversationStore):
        def finish(self, job_id, state):
            seen.append(job.status)
            super().finish(job_id, state)

    jobs = store(make_runner(gate=gate), conversations=Watching(depth=5))
    job = jobs.submit("q", conversation="c")
    follow_ups: list[Job] = []

    def ask_at_once() -> None:
        jobs.wait(job, timeout=5)
        follow_ups.append(jobs.submit("next", conversation="c"))

    waiter = threading.Thread(target=ask_at_once)
    waiter.start()
    gate.set()
    waiter.join(timeout=5)
    assert seen[0] == "running", "freed before the status changed"
    [follow_up] = follow_ups
    assert follow_up.turn == 2 and len(follow_up.turns) == 1
    finished(jobs, follow_up)


def test_a_jobs_run_in_a_conversation_carries_its_id_and_turn_onto_the_trace(store):
    """The id and the turn are the job's, not the state's, so the job store
    tags them beside its own id (R12)."""
    from nl2sql_agent import tracing

    seen = []

    def runner(question, principal, on_progress, turns=()):
        seen.append(dict(tracing._tags.get()))
        return {"question": question}

    jobs = store(runner, conversations=ConversationStore(depth=5))
    job = finished(jobs, jobs.submit("q", conversation="c-1"))
    alone = finished(jobs, jobs.submit("q"))
    assert seen == [
        {"nl2sql.entrypoint": "api", "nl2sql.job_id": job.id, "nl2sql.conversation": "c-1", "nl2sql.turn": "1"},
        {"nl2sql.entrypoint": "api", "nl2sql.job_id": alone.id},
    ]
