"""Capturing a verdict on an answer, and nothing else.

This is the write half of the feedback system and it lives in the
internet-facing process, so what it *cannot* do is most of its design.

It connects to the staging database as `nl2sql_feedback_writer`, a role the
review service creates and re-fences on every start. That role may insert a
submission, replace one that is still pending, delete one that is still
pending, and read back three columns of one. Rows a curator has accepted,
rejected or promoted are invisible to it -- not by convention but by a
row-level security policy, so the guarantee does not depend on the SQL in
this file being careful. This file being careful is still worthwhile; it is
just not what is holding the line.

The snapshot is taken here, at vote time, rather than being looked up later,
because there is no later: a job is forgotten after `API_JOB_TTL_SECONDS`.
A record holding only a job id would be pointing at nothing within the hour,
and the SQL it pointed at is the entire reason a "yes" is worth keeping --
it is the candidate a golden question/SQL pair gets built from.

Nothing here is required for the API to run. With no `API_FEEDBACK_DB_URL`
the routes still exist, still appear in the OpenAPI document and answer 503
with a reason, which keeps the contract stable for a generated client whose
server happens not to have feedback configured.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

#: The table the review service owns. Named here because this process writes
#: to it and has no way to import the module that creates it -- the two run
#: in different images. `tests/review/test_writer_contract.py` is what keeps
#: this name and the columns below in agreement with that module.
TABLE = "feedback_submissions"

#: Exactly the columns `nl2sql_review.store.SUBMISSION_COLUMNS` lists, in the
#: same order. A column added there and not here is a field silently dropped
#: at capture time; a column added here and not there is an INSERT that fails
#: at runtime. The contract test catches both.
COLUMNS = (
    "job_id",
    "verdict",
    "question",
    "sql_code",
    "answer",
    "narrative",
    "intent",
    "tables",
    "row_count",
    "columns",
    "comment",
    "agent_version",
)


class FeedbackUnavailable(RuntimeError):
    """Feedback cannot be recorded, with a reason fit to show a user."""


@dataclass
class Capture:
    """One verdict plus the snapshot that outlives the job it describes."""

    job_id: str
    verdict: str
    question: str
    sql_code: str = ""
    answer: str = ""
    narrative: str = ""
    intent: str = ""
    tables: str = ""
    row_count: int = 0
    columns: tuple[str, ...] = ()
    comment: str = ""
    agent_version: str = ""

    def values(self) -> list[Any]:
        """Positional values for `COLUMNS`, with the JSON column encoded."""
        out: list[Any] = []
        for name in COLUMNS:
            value = getattr(self, name)
            out.append(json.dumps(list(value)) if name == "columns" else value)
        return out


class FeedbackSink(Protocol):
    """Where a verdict goes. Two implementations, one of which does nothing."""

    def record(self, capture: Capture) -> str: ...

    def withdraw(self, job_id: str) -> bool: ...

    def check(self) -> tuple[bool, str]: ...


class DisabledSink:
    """The sink for a server with no staging database configured.

    Answers the readiness check honestly and refuses writes with a message
    naming the variable that would enable them, rather than a stack trace
    about a connection string that is empty.
    """

    reason = (
        "feedback is not configured on this server: set API_FEEDBACK_DB_URL to the "
        "staging database the review service manages"
    )

    def record(self, capture: Capture) -> str:
        raise FeedbackUnavailable(self.reason)

    def withdraw(self, job_id: str) -> bool:
        raise FeedbackUnavailable(self.reason)

    def check(self) -> tuple[bool, str]:
        return False, "not configured"


class PostgresSink:
    """The staging database, reached as the INSERT-only writer role.

    A connection per call and no pool, deliberately: a vote is a click, the
    rate is one per answered question, and a pool held open by the public
    process would be a resource to exhaust for no benefit.
    """

    def __init__(self, url: str, *, connect=None) -> None:
        self.url = url
        self._connect = connect or self._psycopg_connect

    @staticmethod
    def _psycopg_connect(url: str):  # pragma: no cover - needs a live database
        import psycopg

        return psycopg.connect(url, connect_timeout=5)

    def record(self, capture: Capture) -> str:
        """Insert the verdict, replacing the pending one for that job.

        Delete-then-insert rather than `ON CONFLICT DO UPDATE`, and the
        reason is a privilege rather than a preference. `ON CONFLICT DO
        UPDATE` needs **table-level** SELECT; column-level grants are not
        enough, which is worth knowing because the grants look sufficient
        right up until the first revote. Table-level SELECT would let this
        process read the full text of every pending submission, which is
        exactly the power the writer role was shaped to withhold.

        The two statements are one transaction, so a crash between them
        cannot lose a verdict without replacing it. The delete is silently a
        no-op when the row has already been reviewed -- the row-level policy
        makes it invisible -- and the insert then trips the unique
        constraint on `job_id`, which is how "already reviewed" is detected
        without ever being able to read the row that refused.
        """
        submission_id = str(uuid.uuid4())
        statement = (
            f"INSERT INTO {TABLE} (id, {', '.join(COLUMNS)}) "
            f"VALUES ({', '.join(['%s'] * (len(COLUMNS) + 1))}) "
            "RETURNING id"
        )
        try:
            with self._connect(self.url) as conn:
                conn.execute(f"DELETE FROM {TABLE} WHERE job_id = %s", (capture.job_id,))
                try:
                    row = conn.execute(statement, [submission_id, *capture.values()]).fetchone()
                except Exception as exc:
                    if _is_unique_violation(exc):
                        conn.rollback()
                        raise AlreadyReviewed(capture.job_id) from exc
                    raise
                conn.commit()
        except (AlreadyReviewed, FeedbackUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 - every driver failure reads the same here
            raise FeedbackUnavailable(f"cannot record feedback: {type(exc).__name__}") from exc
        return str(row[0]) if row else submission_id

    def withdraw(self, job_id: str) -> bool:
        """Delete the verdict. The policy makes a reviewed one unreachable.

        A reviewed submission is not merely protected from deletion, it is
        not visible to this role at all, so the delete matches nothing and
        the answer is the same as for a job that was never voted on: there
        is nothing here of yours to take back.
        """
        try:
            with self._connect(self.url) as conn:
                result = conn.execute(f"DELETE FROM {TABLE} WHERE job_id = %s", (job_id,))
                conn.commit()
                return result.rowcount > 0
        except Exception as exc:  # noqa: BLE001
            raise FeedbackUnavailable(f"cannot withdraw feedback: {type(exc).__name__}") from exc

    def check(self) -> tuple[bool, str]:
        """For `/readyz`: can this process reach the staging database at all."""
        try:
            with self._connect(self.url) as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        return True, "reachable"


class AlreadyReviewed(RuntimeError):
    """The verdict stands because someone has already acted on it.

    Not an error in the user's sense -- their earlier vote was recorded and
    is still there. The route turns it into a 409 saying exactly that, which
    is more use than a silent success that discards the new opinion.
    """

    def __init__(self, job_id: str) -> None:
        super().__init__(
            f"feedback for job {job_id} has already been reviewed and cannot be changed"
        )
        self.job_id = job_id


def _is_unique_violation(exc: Exception) -> bool:
    """Whether this driver error is a duplicate key, without importing psycopg.

    The SQLSTATE is checked rather than the exception class so that a fake
    connection in a test can raise something of its own shape and still be
    understood -- and so this module keeps working if the driver is swapped.
    23505 is `unique_violation`, and it has been that since SQL:1999.
    """
    code = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    return str(code) == "23505"


def build_sink(url: str | None) -> FeedbackSink:
    """The sink for this configuration. Never raises; never returns None."""
    return PostgresSink(url) if url else DisabledSink()
