"""The shapes every nl2sql API answers in when it is not answering the question.

One error body, one `/healthz`, one `/readyz`, declared once: the agent
API, the review service and the auth service each had their own copy, which
agreed on the wire only because nothing had changed one of them yet.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Wire(BaseModel):
    """Every model on the wire: an unknown field is refused, not ignored.

    A misspelt field that a lenient model drops is a setting silently not
    applied -- or, coming back, a field a client thinks it sent.
    """

    model_config = ConfigDict(extra="forbid")


#: The code an error carries when the route raising it gave none of its own.
#: A service adds its own on top (`dict(FALLBACK_CODES, ...)`).
FALLBACK_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "invalid_request",
    429: "too_many_requests",
    500: "internal_error",
    503: "unavailable",
}


class ApiError(Wire):
    """One error shape for every failure, so a client parses one thing.

    `code` is what a page branches on, and is stable across rewordings of
    `message`, which is for a person reading it.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"error": {"code": "not_found", "message": "no question with that id"}}]
        }
    )

    error: dict[str, Any]

    @classmethod
    def of(cls, code: str, message: str, **detail: Any) -> "ApiError":
        payload: dict[str, Any] = {"code": code, "message": message}
        if detail:
            payload["detail"] = detail
        return cls(error=payload)


class Health(Wire):
    """The process is alive: nothing else is touched to say so."""

    status: Literal["ok"] = "ok"
    version: str
    uptime_seconds: float


class Check(Wire):
    ok: bool
    detail: str = ""


class Readiness(Wire):
    """Whether this service can do its job right now, and if not, which part cannot.

    Separate from `/healthz` because the two failures want different
    answers: a wedged process is restarted, a database that has not finished
    starting is waited for.
    """

    ready: bool
    checks: dict[str, Check]
    warnings: list[str] = Field(default_factory=list)

    def for_caller(self, *, operator: bool) -> "Readiness":
        """Readiness as this caller may see it (V6-32).

        An operator, every word. Anyone else -- `/readyz` asks for no
        credential -- whether each part is up, and not the hosts, versions,
        configuration and driver errors that say how to reach it.
        """
        if operator:
            return self
        return Readiness(ready=self.ready, checks={name: Check(ok=check.ok) for name, check in self.checks.items()})
