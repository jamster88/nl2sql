"""How many model calls may be in flight to the Ollama host at once.

arch7 section 22.9. The host this was built against serves one call at a
time, and the ensemble runs one question several ways; left alone, every
candidate and every other job would put its calls on the host together and
the host would queue them, swapping models between them. So every call the
agent makes to its chat models takes a slot of one gate first, held around
the call and released when it returns or raises -- and `OLLAMA_TIMEOUT` is on
every client, so a call that hangs gives its slot back when the client gives
up.

The gate is process-wide on purpose: the API's workers are several callers
of one host, and so are a question's candidates. `OLLAMA_PARALLEL_CALLS` is
the number of slots, one unless the deployer says the host serves more. It
is the deployer's statement about the host, not something measured here; a
number above what the host serves only queues at the host what would have
queued at the gate. The embedder is on another host and is not gated.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from .config import Settings


class HostGate:
    """`slots` calls to the model host at once, process-wide (arch7 section 22.9)."""

    def __init__(self, slots: int) -> None:
        if slots < 1:
            raise ValueError(f"a host gate needs at least one slot, not {slots}")
        self._slots = slots
        self._semaphore = threading.BoundedSemaphore(slots)
        self._lock = threading.Lock()
        self._waiting = 0

    @property
    def slots(self) -> int:
        return self._slots

    @property
    def waiting(self) -> int:
        """Callers queued for a slot now: for a test, and for anyone asking
        why a call took longer than its model did."""
        with self._lock:
            return self._waiting

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Hold one slot for the block; given back however the block ends."""
        with self._lock:
            self._waiting += 1
        try:
            self._semaphore.acquire()
        finally:
            with self._lock:
                self._waiting -= 1
        try:
            yield
        finally:
            self._semaphore.release()


_gates: dict[int, HostGate] = {}
_gates_lock = threading.Lock()


def gate_for(settings: Settings) -> HostGate:
    """The process's gate for `settings.ollama_parallel_calls` slots, built
    on first use: every router, and so every agent and every job, built from
    the same settings shares it."""
    slots = settings.ollama_parallel_calls
    with _gates_lock:
        if slots not in _gates:
            _gates[slots] = HostGate(slots)
        return _gates[slots]
