"""Where the live tests find the stack's databases.

A test is given the URL in its own variable when one is set -- `POSTGRES_URL`,
`TEST_VECTOR_DB_URL` and the rest, as before. Otherwise it is given the
stack's: the port, the login, the password and the database name read the way
compose reads them for the containers, from the shell and then from `.env`,
falling back to the defaults compose falls back to.

Before 6.1 those defaults were every store's password, and a URL written out
in each test file was right for every stack. Since 6.1 `setup.sh` and
`launch.sh` generate the passwords, so the written-out URL reaches nothing on
a stack started from this checkout -- and the tests skipped, saying nothing
was listening, which reads exactly like a stack that is down. `unreachable`
says which it was: no server is a skip; a server that refuses the login is a
failure, because then the test did not run and nothing else will say so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, urlsplit, urlunsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Read when a test asks, not at import, so a test of this module can point
#: it elsewhere.
DOTENV = REPO_ROOT / ".env"


@dataclass(frozen=True)
class Store:
    """One database as compose publishes it: each part the variable compose
    reads and the default it falls back to."""

    port: tuple[str, str]
    user: tuple[str, str]
    password: tuple[str, str]
    database: tuple[str, str]


STORES = {
    "retail": Store(("POSTGRES_PORT", "5432"), ("POSTGRES_READER_USER", "nl2sql_reader"),
                    ("POSTGRES_READER_PASSWORD", "nl2sql_reader"), ("POSTGRES_DB", "nl2sql_retail")),
    "retail_owner": Store(("POSTGRES_PORT", "5432"), ("POSTGRES_USER", "nl2sql"),
                          ("POSTGRES_PASSWORD", "nl2sql"), ("POSTGRES_DB", "nl2sql_retail")),
    "chunks": Store(("CONTEXT_DB_PORT", "5433"), ("CONTEXT_DB_USER", "ragproc"),
                    ("CONTEXT_DB_PASSWORD", "ragproc"), ("CONTEXT_DB_NAME", "nl2sql_chunks")),
    "vectors": Store(("VECTOR_DB_PORT", "5434"), ("VECTOR_DB_USER", "ragproc"),
                     ("VECTOR_DB_PASSWORD", "ragproc"), ("VECTOR_DB_NAME", "nl2sql_vectors")),
    "feedback": Store(("FEEDBACK_DB_PORT", "5435"), ("FEEDBACK_DB_USER", "feedback"),
                      ("FEEDBACK_DB_PASSWORD", "feedback"), ("FEEDBACK_DB_NAME", "nl2sql_feedback")),
    "corrections": Store(("CORRECTIONS_DB_PORT", "5436"), ("CORRECTIONS_DB_USER", "corrections"),
                         ("CORRECTIONS_DB_PASSWORD", "corrections"), ("CORRECTIONS_DB_NAME", "nl2sql_corrections")),
    "completions": Store(("COMPLETIONS_DB_PORT", "5437"), ("COMPLETIONS_DB_USER", "completions"),
                         ("COMPLETIONS_DB_PASSWORD", "completions"), ("COMPLETIONS_DB_NAME", "nl2sql_completions")),
    "snippets": Store(("SNIPPETS_DB_PORT", "5438"), ("SNIPPETS_DB_USER", "snippets"),
                      ("SNIPPETS_DB_PASSWORD", "snippets"), ("SNIPPETS_DB_NAME", "nl2sql_snippets")),
}


def _dotenv() -> dict[str, str]:
    """`.env` as compose reads it: KEY=VALUE lines, the last one winning."""
    try:
        lines = DOTENV.read_text().splitlines()
    except OSError:
        return {}
    found = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            found[key.strip()] = value.strip()
    return found


def setting(key: str, default: str) -> str:
    """What compose would hand a container for `${KEY:-default}`."""
    return os.environ.get(key) or _dotenv().get(key) or default


def url(store: str, *, variable: str, driver: str = "postgresql+psycopg") -> str:
    """The URL a live test connects with: `variable` when it is set, else the
    stack's own, published on this machine."""
    given = os.environ.get(variable)
    if given:
        return given
    parts = STORES[store]
    user, password = (quote(setting(*pair), safe="") for pair in (parts.user, parts.password))
    return f"{driver}://{user}:{password}@localhost:{setting(*parts.port)}/{setting(*parts.database)}"


def redacted(address: str) -> str:
    """The URL without its password, for a message a test prints."""
    split = urlsplit(address)
    if split.password is None:
        return address
    host = split.hostname or ""
    netloc = f"{split.username}:***@{host}" + (f":{split.port}" if split.port else "")
    return urlunsplit(split._replace(netloc=netloc))


#: What Postgres says when something is listening and turned the login away.
_REFUSED = ("password authentication failed", "no pg_hba.conf entry", "rejects connection")


def unreachable(what: str, address: str, exc: Exception) -> NoReturn:
    """Skip when nothing answers at `address`; fail when it answers and says no."""
    message = str(exc)
    if any(phrase in message for phrase in _REFUSED):
        pytest.fail(
            f"{what} at {redacted(address)} is up but refused the login, so nothing here ran. "
            f"The password is the stack's (.env) unless a URL variable says otherwise: {message}"
        )
    pytest.skip(f"no reachable {what} at {redacted(address)}: {message}")
