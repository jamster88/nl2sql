"""Slowing down someone guessing passwords.

Per name and per address: a name being guessed from many places, and one
place guessing many names, are both stopped. The directory's lockout is the
real limit -- it also covers a direct connection to Postgres -- but it locks
the person out; this one only makes the guesser wait, and says how long.

In memory: one process, and a restart forgetting a count only gives a guesser
back a few attempts the directory is still counting.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable


class Throttle:
    def __init__(self, failures: int, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.failures = failures
        self.seconds = seconds
        self._clock = clock
        self._seen: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        kept = [moment for moment in self._seen.get(key, []) if now - moment < self.seconds]
        if kept:
            self._seen[key] = kept
        else:
            self._seen.pop(key, None)
        return kept

    def wait(self, *keys: str) -> int:
        """Seconds until any of `keys` may try again; 0 when all may now."""
        if self.failures <= 0:
            return 0
        now = self._clock()
        with self._lock:
            longest = 0.0
            for key in keys:
                recent = self._recent(key, now)
                if len(recent) >= self.failures:
                    longest = max(longest, self.seconds - (now - recent[-self.failures]))
        return math.ceil(longest)

    def failed(self, *keys: str) -> None:
        now = self._clock()
        with self._lock:
            for key in keys:
                self._seen.setdefault(key, []).append(now)

    def succeeded(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._seen.pop(key, None)
