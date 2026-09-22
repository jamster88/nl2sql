"""Questions in flight.

A question takes about a minute. That single fact decides the whole shape of
this API: a GUI cannot hold a request open for a minute and show nothing, and
a mobile network will not let it try. So a question becomes a job -- created
immediately, watched while it runs, collected when it finishes -- and the
blocking call every client library already knows how to make is built on top
of that rather than the other way round.

Watching is the part that matters. The pipeline already reports each node it
enters through `on_progress`, which is what the CLI prints to stderr. Here
those same callbacks become a numbered event stream, so a GUI draws "reading
the schema / writing SQL / checking the plan" from the real pipeline instead
of an animation that means nothing.

Everything here is plain threads. The agent is synchronous, Ollama is the
bottleneck, and a thread pool of two is a truthful model of a machine that
can have two questions in the air at once.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

#: What the store is handed to actually answer a question:
#: `runner(question, principal, on_progress) -> AgentState`.
Runner = Callable[[str, str | None, Callable[[str, str], None]], dict]

TERMINAL = ("succeeded", "failed", "cancelled")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass
class ProgressRecord:
    seq: int
    step: str
    detail: str
    at: dt.datetime


@dataclass
class Job:
    """One question, from submitted to collected.

    The condition is the whole synchronisation story: waiters and event
    streams block on it, and the worker notifies it on every progress
    callback and on every status change.
    """

    id: str
    question: str
    principal: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    status: str = "queued"
    created_at: dt.datetime = field(default_factory=_now)
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    progress: list[ProgressRecord] = field(default_factory=list)
    state: dict[str, Any] | None = None
    error: str | None = None
    condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.RLock()), repr=False
    )

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def duration_ms(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds() * 1000, 2)


@dataclass
class StreamChunk:
    """One thing to put on the wire: a progress event, a status change, the
    finished job, a keep-alive, or the end of the server's patience."""

    kind: str  # "progress" | "status" | "done" | "keepalive" | "timeout"
    event: ProgressRecord | None = None
    status: str | None = None
    job: Job | None = None


class JobStore:
    """The set of questions this server knows about.

    Bounded three ways, because an HTTP endpoint is something anyone can
    call in a loop: `max_concurrency` runs at once, `max_jobs` are
    remembered, and a finished job is forgotten `ttl_seconds` after it
    finished.
    """

    def __init__(
        self,
        runner: Runner,
        *,
        max_concurrency: int = 2,
        max_jobs: int = 200,
        ttl_seconds: float = 3600.0,
    ) -> None:
        self._runner = runner
        self._max_jobs = max(1, max_jobs)
        self._ttl = ttl_seconds
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, max_concurrency), thread_name_prefix="nl2sql-job"
        )
        self._closed = False

    # --- lifecycle --------------------------------------------------------

    def submit(
        self,
        question: str,
        *,
        principal: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Job:
        if self._closed:
            raise RuntimeError("the job store is shut down")
        job = Job(
            id=uuid.uuid4().hex,
            question=question,
            principal=principal,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._prune_locked()
            self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def _run(self, job: Job) -> None:
        with job.condition:
            # Cancelled between submit and pickup: never start it. This is the
            # only point at which cancellation is honest, which is why DELETE
            # refuses a job that is already running rather than pretending.
            if job.status == "cancelled":
                job.condition.notify_all()
                return
            job.status = "running"
            job.started_at = _now()
            job.condition.notify_all()

        def on_progress(step: str, detail: str) -> None:
            with job.condition:
                job.progress.append(
                    ProgressRecord(
                        seq=len(job.progress) + 1,
                        step=step,
                        detail=detail or "",
                        at=_now(),
                    )
                )
                job.condition.notify_all()

        try:
            state = self._runner(job.question, job.principal, on_progress)
        except BaseException as exc:  # noqa: BLE001 -- the job records it, the server survives
            with job.condition:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.finished_at = _now()
                job.condition.notify_all()
            return

        with job.condition:
            job.state = state
            # A pipeline that refuses a question or exhausts its retries
            # still returns a state; `error` there is the agent's own verdict,
            # not a server fault, and the job reports it as a failed run with
            # the state attached so a GUI can still show the attempts.
            job.error = (state or {}).get("error")
            job.status = "failed" if job.error else "succeeded"
            job.finished_at = _now()
            job.condition.notify_all()

    def shutdown(self, *, wait: bool = False) -> None:
        self._closed = True
        self._pool.shutdown(wait=wait, cancel_futures=True)

    # --- reading ----------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._prune_locked()
            return self._jobs.get(job_id)

    def list(self, *, limit: int = 50) -> list[Job]:
        """Newest first, which is the order a GUI's history panel wants."""
        with self._lock:
            self._prune_locked()
            return list(self._jobs.values())[::-1][:limit]

    def cancel(self, job_id: str) -> str:
        """Returns what happened: 'cancelled', 'forgotten', 'running', or 'missing'.

        A running job cannot be stopped: it is inside a LangGraph invocation
        that owns a database cursor and an HTTP call to Ollama, and there is
        no truthful way to interrupt that from here. Saying so is better than
        marking it cancelled and letting it finish anyway.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return "missing"
            with job.condition:
                if job.status == "queued":
                    job.status = "cancelled"
                    job.finished_at = _now()
                    job.condition.notify_all()
                    return "cancelled"
                if job.status == "running":
                    return "running"
            del self._jobs[job_id]
            return "forgotten"

    # --- waiting ----------------------------------------------------------

    def wait(self, job: Job, timeout: float | None = None) -> bool:
        """Block until the job is terminal. False if the timeout ran out first."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with job.condition:
            while not job.terminal:
                if deadline is None:
                    job.condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                job.condition.wait(remaining)
            return True

    def stream(
        self,
        job: Job,
        *,
        from_seq: int = 0,
        timeout: float | None = None,
        keepalive: float = 15.0,
    ) -> Iterator[StreamChunk]:
        """Progress as it happens, resumable from a sequence number.

        Written as a blocking generator rather than anything async because
        the producer is a worker thread: a `Condition` is exactly the right
        primitive, and wrapping a thread-safe iterator is something every
        server framework already knows how to do.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        seq = from_seq
        status: str | None = None

        while True:
            keep_alive_due = False
            with job.condition:
                events = [e for e in job.progress if e.seq > seq]
                changed = job.status != status
                terminal = job.terminal
                if not events and not changed and not terminal:
                    remaining = keepalive
                    if deadline is not None:
                        remaining = min(remaining, deadline - time.monotonic())
                    if remaining <= 0:
                        yield StreamChunk("timeout")
                        return
                    # False means it waited the whole interval with nothing
                    # happening, which is when a proxy needs to see a byte.
                    keep_alive_due = not job.condition.wait(remaining)
                else:
                    status = job.status

            if keep_alive_due:
                yield StreamChunk("keepalive")
                continue
            if not events and not changed and not terminal:
                continue

            for event in events:
                seq = event.seq
                yield StreamChunk("progress", event=event)
            if changed:
                yield StreamChunk("status", status=status)
            if terminal and not events:
                yield StreamChunk("done", job=job)
                return

    # --- housekeeping -----------------------------------------------------

    def _prune_locked(self) -> None:
        """Drop expired jobs, then the oldest finished ones if still over cap.

        Only ever terminal jobs: a running one is dropped by nothing, or a
        client would lose the answer to a question the server is still paying
        for.
        """
        now = _now()
        for job_id, job in list(self._jobs.items()):
            if (
                job.terminal
                and job.finished_at is not None
                and (now - job.finished_at).total_seconds() > self._ttl
            ):
                del self._jobs[job_id]

        if len(self._jobs) <= self._max_jobs:
            return
        for job_id, job in list(self._jobs.items()):
            if len(self._jobs) <= self._max_jobs:
                return
            if job.terminal:
                del self._jobs[job_id]
