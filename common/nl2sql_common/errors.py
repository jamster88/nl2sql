"""The failures a service tells apart, so it can catch what it means (V6-23).

There were 79 `except Exception` in the agent and the review service. Most
meant one of four things -- a database did not answer, a model host did not,
the network between did not, or a text could not be parsed -- and caught
everything else besides: a typo's `NameError`, a `KeyError` from a changed
shape, a bug, all reported as "the store is down". Catching the family it
means lets a bug be a bug.

The families are tuples, built from whichever drivers this image has: the
directory's image has no SQLAlchemy and needs no stub of it. A boundary that
must survive anything -- a job runner, a graph node whose failure is part of
the answer -- still catches `Exception`, and says why on the line
(`# noqa: BLE001 - <why>`); `tests/security/test_error_taxonomy.py` holds
every one to that.
"""

from __future__ import annotations

import http.client
import json
from typing import Iterable


class Nl2SqlError(Exception):
    """Something this system decided, with a code a caller can branch on."""

    code = "error"


class Unavailable(Nl2SqlError):
    """A dependency -- a database, a model host, the directory -- did not answer."""

    code = "unavailable"


class Refused(Nl2SqlError):
    """A dependency answered, and the answer was no."""

    code = "refused"


class Invalid(Nl2SqlError):
    """An input this system cannot use, whoever sent it."""

    code = "invalid"


def _optional(*names: str) -> list[type[BaseException]]:
    """The exception classes named, from whichever of their modules import."""
    found: list[type[BaseException]] = []
    for name in names:
        module, _, attribute = name.rpartition(".")
        try:
            imported = __import__(module, fromlist=[attribute])
        except ImportError:
            continue
        found.append(getattr(imported, attribute))
    return found


def _family(*members: Iterable[type[BaseException]]) -> tuple[type[BaseException], ...]:
    seen: list[type[BaseException]] = []
    for group in members:
        for member in group:
            if member not in seen:
                seen.append(member)
    return tuple(seen)


#: A socket, a file, a timeout: what the operating system says. `OSError`
#: covers `ConnectionError`, `TimeoutError` and urllib's `URLError`.
OS_ERRORS: tuple[type[BaseException], ...] = (OSError,)

#: The network between: HTTP as the standard library, httpx (Ollama's and
#: MLflow's clients) and requests (the loaders') each report it.
NETWORK_ERRORS = _family(
    OS_ERRORS,
    [http.client.HTTPException],
    _optional("httpx.HTTPError", "requests.RequestException"),
)

#: A database that did not answer, refused, or was asked something it could
#: not do -- as the driver says it, as SQLAlchemy wraps it, or as one of this
#: system's own wrappers already said it (`Unavailable`).
DATABASE_ERRORS = _family(
    OS_ERRORS,
    _optional("psycopg.Error", "sqlalchemy.exc.SQLAlchemyError"),
    [Unavailable],
)

#: A model host that did not answer, or answered in a shape that could not be
#: used: the network, Ollama's own error, LangChain's, and a reply that would
#: not parse (`ValueError` covers pydantic's `ValidationError` and LangChain's
#: `OutputParserException`).
MODEL_ERRORS = _family(
    NETWORK_ERRORS,
    [ValueError],
    _optional("ollama.ResponseError", "langchain_core.exceptions.LangChainException"),
    [Unavailable],
)

#: A text that would not parse: JSON, or SQL as pglast reads it (`pglast.Error`
#: is the base of its parser's and its printers' own).
PARSE_ERRORS = _family(
    [json.JSONDecodeError, ValueError],
    _optional("pglast.Error"),
)


def described(exc: BaseException, limit: int = 300) -> str:
    """What happened, in one bounded line: the exception's type and message."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    message = f"{type(exc).__name__}: {text}" if text != type(exc).__name__ else text
    return message if len(message) <= limit else message[: limit - 3] + "..."
