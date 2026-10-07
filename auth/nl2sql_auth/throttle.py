"""Slowing down someone guessing passwords.

Per name and per address: a name being guessed from many places, and one
place guessing many names, are both stopped. The directory's lockout is the
real limit -- it also covers a direct connection to Postgres -- but it locks
the person out; this one only makes the guesser wait, and says how long.

The two get their own limits (`per_kind`, by the part of a key before its
`:`). Behind a proxy, or Docker's NAT on one machine, everyone on a network
can arrive as one address, and an address held to a name's five would let
one person's typing lock everybody else out of signing in.

In memory: one process, and a restart forgetting a count only gives a guesser
back a few attempts the directory is still counting.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable, Mapping


class Throttle:
    def __init__(
        self,
        failures: int,
        seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        per_kind: Mapping[str, int] | None = None,
    ) -> None:
        self.failures = failures
        self.per_kind = dict(per_kind or {})
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

    def limit(self, key: str) -> int:
        """How many failures `key` may have; 0 or less is no limit."""
        return self.per_kind.get(key.partition(":")[0], self.failures)

    def wait(self, *keys: str) -> int:
        """Seconds until any of `keys` may try again; 0 when all may now."""
        now = self._clock()
        with self._lock:
            longest = 0.0
            for key in keys:
                limit = self.limit(key)
                if limit <= 0:
                    continue
                recent = self._recent(key, now)
                if len(recent) >= limit:
                    longest = max(longest, self.seconds - (now - recent[-limit]))
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
