"""Settings from the environment, read the same way by every service.

There were five copies of these, which disagreed in small ways: one read a
variable of spaces as a value, one did not strip it. They agree now because
there is one.

Empty and whitespace are unset. Docker Compose forwards a variable the host
has not set as an empty string, so without this every setting compose passes
through would override its own default with nothing -- `OLLAMA_MODEL=""`
would be a model with no name. Treating empty as absent is what lets compose
forward a setting without repeating its default.
"""

from __future__ import annotations

import os
from pathlib import Path

#: What a switch reads as on. Anything else -- `false`, `0`, `no`, a typo --
#: is off, except where a caller reads a switch the other way round.
TRUE = frozenset({"1", "true", "yes", "on"})


def env(name: str) -> str | None:
    """The variable, stripped, or None when it is unset, empty or blank."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def env_str(name: str, default: str) -> str:
    raw = env(name)
    return default if raw is None else raw


def env_int(name: str, default: int) -> int:
    raw = env(name)
    return default if raw is None else int(raw)


def env_float(name: str, default: float) -> float:
    raw = env(name)
    return default if raw is None else float(raw)


def env_bool(name: str, default: bool) -> bool:
    raw = env(name)
    return default if raw is None else raw.lower() in TRUE


def env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """A comma-separated list, its blanks dropped; unset is `default`."""
    raw = env(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def secret(name: str) -> str | None:
    """`NAME`, or the contents of the file `NAME_FILE` names (Docker secrets).

    The file wins when both are set: a secret mounted on purpose is a
    stronger statement than a variable that may have been inherited.
    """
    path = env(f"{name}_FILE")
    if path:
        return Path(path).read_text().strip() or None
    return env(name)
