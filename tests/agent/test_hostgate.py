"""The host gate: how many model calls are in flight to the Ollama host (arch7 section 22.9).

The host this was built against serves one call at a time, so by default
every call the agent makes waits for the one before it -- whichever thread,
job or candidate made it. These hold that, and that a slot is always given
back.
"""

from __future__ import annotations

import threading
import time

import pytest
from nl2sql_agent.config import Settings
from nl2sql_agent.hostgate import HostGate, gate_for


class _Host:
    """A model host that records when each call was in it."""

    def __init__(self, seconds: float = 0.05) -> None:
        self.seconds = seconds
        self.calls: list[tuple[float, float]] = []
        self._lock = threading.Lock()

    def call(self) -> None:
        entered = time.perf_counter()
        time.sleep(self.seconds)
        with self._lock:
            self.calls.append((entered, time.perf_counter()))

    def overlapped(self) -> bool:
        ordered = sorted(self.calls)
        return any(later[0] < earlier[1] for earlier, later in zip(ordered, ordered[1:]))


def _from_threads(gate: HostGate, host: _Host, threads: int = 2) -> None:
    start = threading.Barrier(threads)

    def caller() -> None:
        start.wait()
        with gate.slot():
            host.call()

    workers = [threading.Thread(target=caller) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()


def test_with_one_slot_two_threads_calls_never_overlap():
    host = _Host()
    _from_threads(HostGate(1), host)
    assert len(host.calls) == 2
    assert not host.overlapped()


def test_with_two_slots_two_threads_calls_may():
    host = _Host(seconds=0.2)
    _from_threads(HostGate(2), host)
    assert host.overlapped()


def test_a_call_that_raises_gives_its_slot_back():
    gate = HostGate(1)
    with pytest.raises(ConnectionError):
        with gate.slot():
            raise ConnectionError("the host went away")
    # Were the slot still held this would wait for ever; it does not.
    with gate.slot():
        pass


def test_a_caller_waiting_for_a_slot_is_counted_until_it_has_one():
    gate = HostGate(1)
    held, waiting = threading.Event(), threading.Event()

    def second() -> None:
        waiting.set()
        with gate.slot():
            pass

    with gate.slot():
        held.set()
        worker = threading.Thread(target=second)
        worker.start()
        waiting.wait()
        deadline = time.monotonic() + 5
        while gate.waiting != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gate.waiting == 1
    worker.join()
    assert gate.waiting == 0


def test_the_same_settings_share_one_gate_for_the_process():
    """The API's workers are several callers of one host; so are a
    question's candidates. One gate for them all is the point."""
    assert gate_for(Settings()) is gate_for(Settings())
    assert gate_for(Settings(ollama_parallel_calls=3)).slots == 3
    assert gate_for(Settings(ollama_parallel_calls=3)) is not gate_for(Settings())


def test_a_gate_needs_a_slot():
    with pytest.raises(ValueError, match="at least one slot"):
        HostGate(0)
