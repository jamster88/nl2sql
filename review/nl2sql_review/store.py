"""The staging database: where a verdict waits until someone has looked at it.

This is deliberately a *separate* Postgres from everything else in the
project, and the reason is what the other three databases are. The retail
store is the subject under test, and writing curation data into it would
pollute the thing being measured. The chunk and vector stores ship their
data inside published images, so they are artefacts rather than places to
keep something that accumulates. What lands here is neither: it is ordinary
runtime data written by strangers, and it wants its own volume, its own
backup story and its own blast radius.

Two processes reach the tables below, with deliberately different powers:

* **The agent's REST API** holds `nl2sql_feedback_writer` and may only
  INSERT into `feedback_submissions`. It is the internet-facing process; it
  cannot read back what other people submitted, cannot change a review and
  cannot see the promotion log.
* **The review service** owns the schema. It reads, updates and promotes.

The writer role is recreated on every start, the same way the retail image
recreates `nl2sql_reader`, so the grants are whatever this file says rather
than whatever a past version of it said.

One submission per job, not one per click. A user who votes "no", thinks
better of it and votes "yes" has one opinion, not two, and `job_id` is
unique so the second vote replaces the first -- but only while nobody has
started reviewing it, or a revote would silently undo a curator's work.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

SUBMISSIONS = "feedback_submissions"
PROMOTIONS = "feedback_promotions"

#: The role the agent API connects as. It exists so the public process can
#: add a row and do nothing else.
WRITER_ROLE = "nl2sql_feedback_writer"

#: Every state a submission can be in, in the order it travels through them.
#: `promoted` is terminal: the pair is in the golden set and the row is now
#: a record of how it got there.
STATES = ("pending", "accepted", "rejected", "promoted")

#: The columns the agent API is allowed to write. Named here rather than in
#: the API so there is one list; `tests/review/test_writer_contract.py`
#: checks the API's INSERT against it, because the two live in different
#: images and nothing else would catch them drifting apart.
SUBMISSION_COLUMNS = (
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


@dataclass
class Submission:
    """One verdict, with enough of the job attached to survive the job.

    The snapshot is not redundancy. A job is forgotten after
    `API_JOB_TTL_SECONDS` -- an hour by default -- so a record holding only
    an id would be pointing at nothing by the time anyone reviewed it. The
    SQL in particular is the whole reason a "yes" is interesting: it is the
    candidate answer a golden pair would be built from.
    """

    id: str
    job_id: str
    verdict: str
    question: str
    sql_code: str = ""
    answer: str = ""
    narrative: str = ""
    intent: str = ""
    tables: str = ""
    row_count: int = 0
    columns: list[str] = field(default_factory=list)
    comment: str = ""
    agent_version: str = ""
    submitted_at: datetime | None = None
    state: str = "pending"
    reviewer: str = ""
    review_note: str = ""
    reviewed_at: datetime | None = None
    draft: dict[str, Any] = field(default_factory=dict)
    promoted_pair_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row)


@contextmanager
def connection(url: str) -> Iterator[psycopg.Connection]:
    conn = connect(url)
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema(conn: psycopg.Connection) -> None:
    """Create the tables, then the writer role and its grants.

    Idempotent in both halves: `IF NOT EXISTS` for the tables, and the role
    is created only when absent but has its grants reset every time. That
    ordering matters -- a grant naming a table that does not exist yet is an
    error, not a no-op.
    """
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                id               TEXT PRIMARY KEY,
                job_id           TEXT NOT NULL UNIQUE,
                verdict          TEXT NOT NULL CHECK (verdict IN ('yes', 'no')),
                question         TEXT NOT NULL,
                sql_code         TEXT NOT NULL DEFAULT '',
                answer           TEXT NOT NULL DEFAULT '',
                narrative        TEXT NOT NULL DEFAULT '',
                intent           TEXT NOT NULL DEFAULT '',
                tables           TEXT NOT NULL DEFAULT '',
                row_count        INT  NOT NULL DEFAULT 0,
                columns          JSONB NOT NULL DEFAULT '[]'::jsonb,
                comment          TEXT NOT NULL DEFAULT '',
                agent_version    TEXT NOT NULL DEFAULT '',
                submitted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                state            TEXT NOT NULL DEFAULT 'pending'
                                   CHECK (state IN ('pending', 'accepted', 'rejected', 'promoted')),
                reviewer         TEXT NOT NULL DEFAULT '',
                review_note      TEXT NOT NULL DEFAULT '',
                reviewed_at      TIMESTAMPTZ,
                draft            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                promoted_pair_id TEXT
            )
            """
        ).format(sql.Identifier(SUBMISSIONS))
    )
    # The review queue is read by state, newest first, on every page load.
    conn.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (state, submitted_at DESC)").format(
            sql.Identifier(f"idx_{SUBMISSIONS}_state"), sql.Identifier(SUBMISSIONS)
        )
    )

    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                pair_id       TEXT PRIMARY KEY,
                submission_id TEXT NOT NULL REFERENCES {} (id) ON DELETE RESTRICT,
                chunk_id      TEXT NOT NULL,
                suite         TEXT NOT NULL DEFAULT '',
                title         TEXT NOT NULL DEFAULT '',
                markdown      TEXT NOT NULL,
                reviewer      TEXT NOT NULL DEFAULT '',
                promoted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                reloaded      BOOLEAN NOT NULL DEFAULT false,
                reload_detail TEXT NOT NULL DEFAULT ''
            )
            """
        ).format(sql.Identifier(PROMOTIONS), sql.Identifier(SUBMISSIONS))
    )
    conn.commit()


def ensure_writer_role(conn: psycopg.Connection, password: str) -> None:
    """(Re)create the role the agent API connects as, and fence it in.

    Recreated rather than created-if-absent, because a role whose grants were
    widened by hand once stays widened forever otherwise. The password is
    reset too, so rotating it is a restart.

    The interesting part is that the fence is **row-level security**, not
    just column grants. The public process has to be able to replace a
    verdict -- a user who clicks "no", thinks again and clicks "yes" has one
    opinion, not two -- and that means UPDATE, which as a plain grant would
    also let it rewrite a submission a curator had already judged. The
    `state = 'pending'` guard in `record()` is this service's own SQL, and a
    guard that lives only in the caller is not a guard against the caller.

    So the policies below say it in the database: the writer may see, replace
    and delete rows that are still pending, and rows in any other state do
    not exist as far as it is concerned. A curator's decision is out of reach
    of the internet-facing process by construction rather than by care. The
    table's owner -- this service -- bypasses all of it, which is why the
    review routes still see everything.
    """
    role = sql.Identifier(WRITER_ROLE)
    table = sql.Identifier(SUBMISSIONS)
    conn.execute(
        sql.SQL(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {}) THEN
                    CREATE ROLE {} LOGIN;
                END IF;
            END
            $$
            """
        ).format(sql.Literal(WRITER_ROLE), role)
    )
    conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(role, sql.Literal(password)))

    # Start from nothing every time: this is what makes the grants below the
    # whole truth about what the public process can do.
    conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role))
    conn.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(role))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))

    updatable = [c for c in SUBMISSION_COLUMNS if c != "job_id"]
    conn.execute(
        sql.SQL("GRANT INSERT, DELETE ON {} TO {}").format(table, role)
    )
    conn.execute(
        sql.SQL("GRANT SELECT ({}) ON {} TO {}").format(
            sql.SQL(", ").join(sql.Identifier(c) for c in ("id", "job_id", "state")), table, role
        )
    )
    conn.execute(
        sql.SQL("GRANT UPDATE ({}) ON {} TO {}").format(
            sql.SQL(", ").join(sql.Identifier(c) for c in (*updatable, "submitted_at")), table, role
        )
    )

    conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))
    for name, clause in (
        ("writer_select", sql.SQL("FOR SELECT TO {} USING (state = 'pending')").format(role)),
        ("writer_insert", sql.SQL("FOR INSERT TO {} WITH CHECK (state = 'pending')").format(role)),
        (
            "writer_update",
            sql.SQL(
                "FOR UPDATE TO {} USING (state = 'pending') WITH CHECK (state = 'pending')"
            ).format(role),
        ),
        ("writer_delete", sql.SQL("FOR DELETE TO {} USING (state = 'pending')").format(role)),
    ):
        # Dropped and recreated for the same reason the grants are revoked
        # first: a policy edited by hand should not outlive a restart.
        conn.execute(
            sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(sql.Identifier(name), table)
        )
        conn.execute(sql.SQL("CREATE POLICY {} ON {} ").format(sql.Identifier(name), table) + clause)
    conn.commit()


def _row_to_submission(row: dict[str, Any]) -> Submission:
    # `columns` and `draft` are JSONB, which psycopg hands back already
    # decoded, so there is nothing to parse here.
    return Submission(**dict(row))


# There is deliberately no `record()` in this module. Writing a verdict is
# the agent API's job and the writer role's only power, and a second write
# path here -- owned by the role that can do everything -- would be a way
# around the fence the role exists to be. The API's INSERT is in
# `nl2sql_agent.api.feedback`; `tests/api/test_feedback.py` checks that its
# column list still matches `SUBMISSION_COLUMNS` above.


def get(conn: psycopg.Connection, submission_id: str) -> Submission | None:
    row = conn.execute(
        sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(SUBMISSIONS)),
        (submission_id,),
    ).fetchone()
    return _row_to_submission(row) if row else None


def get_by_job(conn: psycopg.Connection, job_id: str) -> Submission | None:
    row = conn.execute(
        sql.SQL("SELECT * FROM {} WHERE job_id = %s").format(sql.Identifier(SUBMISSIONS)),
        (job_id,),
    ).fetchone()
    return _row_to_submission(row) if row else None


def listing(
    conn: psycopg.Connection,
    *,
    state: str | None = None,
    verdict: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Submission]:
    clauses: list[sql.Composable] = []
    values: list[Any] = []
    if state:
        clauses.append(sql.SQL("state = %s"))
        values.append(state)
    if verdict:
        clauses.append(sql.SQL("verdict = %s"))
        values.append(verdict)
    where = (
        sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("")
    )
    rows = conn.execute(
        sql.SQL("SELECT * FROM {} {} ORDER BY submitted_at DESC LIMIT %s OFFSET %s").format(
            sql.Identifier(SUBMISSIONS), where
        ),
        [*values, limit, offset],
    ).fetchall()
    return [_row_to_submission(row) for row in rows]


def counts(conn: psycopg.Connection) -> dict[str, int]:
    """How many submissions sit in each state, including the empty ones.

    Every state is present whether or not anything is in it, so a queue
    badge reads `0` rather than disappearing.
    """
    rows = conn.execute(
        sql.SQL("SELECT state, count(*) AS n FROM {} GROUP BY state").format(
            sql.Identifier(SUBMISSIONS)
        )
    ).fetchall()
    found = {row["state"]: row["n"] for row in rows}
    return {state: found.get(state, 0) for state in STATES}


def review(
    conn: psycopg.Connection,
    submission_id: str,
    *,
    state: str | None = None,
    reviewer: str | None = None,
    review_note: str | None = None,
    draft: dict[str, Any] | None = None,
) -> Submission | None:
    """Update the review fields, leaving the submitted snapshot untouched.

    Only the four fields a curator owns can be set here. The question, the
    SQL and the verdict are what the user said; an editor that could rewrite
    them would turn the staging table into a place where evidence changes.
    Edits for the golden set go into `draft`, beside the original.
    """
    assignments: list[sql.Composable] = []
    values: list[Any] = []
    if state is not None:
        assignments.append(sql.SQL("state = %s"))
        values.append(state)
        assignments.append(sql.SQL("reviewed_at = now()"))
    if reviewer is not None:
        assignments.append(sql.SQL("reviewer = %s"))
        values.append(reviewer)
    if review_note is not None:
        assignments.append(sql.SQL("review_note = %s"))
        values.append(review_note)
    if draft is not None:
        assignments.append(sql.SQL("draft = %s::jsonb"))
        values.append(json.dumps(draft))
    if not assignments:
        return get(conn, submission_id)

    row = conn.execute(
        sql.SQL("UPDATE {} SET {} WHERE id = %s RETURNING *").format(
            sql.Identifier(SUBMISSIONS), sql.SQL(", ").join(assignments)
        ),
        [*values, submission_id],
    ).fetchone()
    conn.commit()
    return _row_to_submission(row) if row else None


def mark_promoted(
    conn: psycopg.Connection,
    submission_id: str,
    *,
    pair_id: str,
    chunk_id: str,
    suite: str,
    title: str,
    markdown: str,
    reviewer: str,
    reloaded: bool,
    reload_detail: str,
) -> None:
    """Record the promotion and move the submission to its terminal state.

    One transaction: a promotion log naming a submission that is not marked
    promoted, or the reverse, would both be worse than either alone, because
    the next reviewer would not know which half to believe.
    """
    conn.execute(
        sql.SQL(
            """
            INSERT INTO {} (pair_id, submission_id, chunk_id, suite, title, markdown,
                            reviewer, reloaded, reload_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
        ).format(sql.Identifier(PROMOTIONS)),
        (pair_id, submission_id, chunk_id, suite, title, markdown, reviewer, reloaded, reload_detail),
    )
    conn.execute(
        sql.SQL(
            "UPDATE {} SET state = 'promoted', promoted_pair_id = %s, reviewed_at = now() WHERE id = %s"
        ).format(sql.Identifier(SUBMISSIONS)),
        (pair_id, submission_id),
    )
    conn.commit()


def promotions(conn: psycopg.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        sql.SQL("SELECT * FROM {} ORDER BY promoted_at DESC LIMIT %s").format(
            sql.Identifier(PROMOTIONS)
        ),
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


class Repository:
    """The staging tables, as an object the HTTP routes can be handed.

    A class rather than the module functions above, for exactly the reason
    the agent API injects its pipeline: it is what lets every route be tested
    against a fake with no Postgres, no container and no network, which is
    the only way these paths get exercised on every run. The functions stay
    module-level because the promotion path and the schema tools use them
    directly.

    A connection per call, closed after it. Not a pool: this service handles
    a curator clicking through a queue, so the connection count is one per
    click, and a pool would be machinery guarding against a load that does
    not exist.
    """

    def __init__(self, url: str) -> None:
        self.url = url

    def ping(self) -> None:
        """Round-trip the database. Raises whatever went wrong, for `/readyz`."""
        with connection(self.url) as conn:
            conn.execute("SELECT 1").fetchone()

    def setup(self, writer_password: str) -> None:
        with connection(self.url) as conn:
            ensure_schema(conn)
            ensure_writer_role(conn, writer_password)

    def get(self, submission_id: str) -> Submission | None:
        with connection(self.url) as conn:
            return get(conn, submission_id)

    def get_by_job(self, job_id: str) -> Submission | None:
        with connection(self.url) as conn:
            return get_by_job(conn, job_id)

    def listing(self, **kwargs: Any) -> list[Submission]:
        with connection(self.url) as conn:
            return listing(conn, **kwargs)

    def counts(self) -> dict[str, int]:
        with connection(self.url) as conn:
            return counts(conn)

    def review(self, submission_id: str, **kwargs: Any) -> Submission | None:
        with connection(self.url) as conn:
            return review(conn, submission_id, **kwargs)

    def mark_promoted(self, submission_id: str, **kwargs: Any) -> None:
        with connection(self.url) as conn:
            mark_promoted(conn, submission_id, **kwargs)

    def promotions(self, limit: int = 50) -> list[dict[str, Any]]:
        with connection(self.url) as conn:
            return promotions(conn, limit)
