"""The corrections and completions stores: where a fixed answer goes.

A verdict that the answer was right goes into the golden question set, as it
always has. The other two do not, because what they hold is a different
kind of thing:

* a **correction** is a *wrong* answer and the query that should have been
  generated instead -- the question, the SQL the agent wrote and what the
  user was shown, and the SQL a reviewer wrote and validated;
* a **completion** is an answer that was *correct but incomplete* -- the SQL
  was right, the answer left out something a reader needed -- and the query
  that would have carried it.

Neither is a golden pair. A golden pair is a question and its right answer,
and the golden set is what the agent is measured against; a correction is a
record of a mistake, and putting it in the benchmark would be measuring the
agent against its own failures. So each has its own store, apart from the
golden set and apart from each other: one Postgres per kind, holding the
kind's records and its RAG side by side -- the records in `<kind>` and an
embedding of each question, beside the question, the incorrect SQL and the
corrected SQL, in `<kind>_vectors`. A retrieval over the vectors is useful
on its own; the records carry everything else a reviewer saw.

Nothing reads these stores yet. They are what a later version's agent will
retrieve from -- "a question like this one was answered wrongly before, and
this is what fixed it" -- which is why the RAG half is written now, at save
time, rather than left for a batch job to backfill.

The embedding is best-effort in the way the golden set's reload is. The
record is the fact; a save whose embedding host is down still stores it, and
the next save that can reach the host embeds everything still missing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


@dataclass(frozen=True)
class Kind:
    """One of the two stores, and the verdict whose answers it fixes."""

    verdict: str
    slug: str
    label: str
    #: Fix ids are the prefix and a number, W0001 and I0001, so a record
    #: says which store it came from wherever it is quoted.
    prefix: str

    @property
    def table(self) -> str:
        return f"sql_{self.slug}"

    @property
    def vector_table(self) -> str:
        return f"sql_{self.slug}_vectors"


CORRECTIONS = Kind(verdict="no", slug="corrections", label="wrong", prefix="W")
COMPLETIONS = Kind(
    verdict="incomplete", slug="completions", label="correct but incomplete", prefix="I"
)

#: verdict -> the store its answers are fixed into. A `yes` is in neither:
#: it goes into the golden set.
KINDS = {kind.verdict: kind for kind in (CORRECTIONS, COMPLETIONS)}
BY_SLUG = {kind.slug: kind for kind in (CORRECTIONS, COMPLETIONS)}


class AlreadyFixed(Exception):
    """The submission already has a record in this store."""

    def __init__(self, submission_id: str, fix_id: str) -> None:
        super().__init__(f"{submission_id} is already {fix_id}")
        self.submission_id = submission_id
        self.fix_id = fix_id


class Embedder(Protocol):
    """`ragproc.embedder`'s interface: what saving a fix needs of one."""

    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class Fix:
    """One stored fix: the question, the incorrect answer, the correct answer."""

    fix_id: str
    submission_id: str
    job_id: str
    question: str
    incorrect_sql: str = ""
    incorrect_answer: str = ""
    incorrect_columns: list[str] = field(default_factory=list)
    incorrect_row_count: int = 0
    corrected_sql: str = ""
    corrected_columns: list[str] = field(default_factory=list)
    corrected_rows: list[list[Any]] = field(default_factory=list)
    corrected_row_count: int = 0
    corrected_truncated: bool = False
    plan_cost: float | None = None
    user_comment: str = ""
    reviewer: str = ""
    review_note: str = ""
    agent_version: str = ""
    created_at: datetime | None = None
    #: Whether its question has a current vector. Read, never written.
    embedded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


#: The columns `save` writes, in order.
FIX_COLUMNS = (
    "fix_id",
    "submission_id",
    "job_id",
    "question",
    "incorrect_sql",
    "incorrect_answer",
    "incorrect_columns",
    "incorrect_row_count",
    "corrected_sql",
    "corrected_columns",
    "corrected_rows",
    "corrected_row_count",
    "corrected_truncated",
    "plan_cost",
    "user_comment",
    "reviewer",
    "review_note",
    "agent_version",
)
_JSON_COLUMNS = {"incorrect_columns", "corrected_columns", "corrected_rows"}


@dataclass
class EmbedResult:
    """What a save managed to embed. A failure here never undoes a save."""

    embedded: int = 0
    pending: int = 0
    ran: bool = False
    error: str | None = None

    @property
    def detail(self) -> str:
        if not self.ran:
            return f"not embedded: {self.error}" if self.error else "embedding is off"
        if self.error:
            return f"FAILED: {self.error}; {self.pending} still to embed"
        return f"embedded {self.embedded}; {self.pending} still to embed"


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row)


@contextmanager
def connection(url: str) -> Iterator[psycopg.Connection]:
    conn = connect(url)
    try:
        yield conn
    finally:
        conn.close()


def content_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def ensure_schema(conn: psycopg.Connection, kind: Kind) -> None:
    """The records table, and the extension the RAG table needs.

    The vector table itself is created on the first embed, because its
    column width is the embedding model's dimension and nothing here knows
    that until a model has answered.
    """
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                fix_id              TEXT PRIMARY KEY,
                submission_id       TEXT NOT NULL UNIQUE,
                job_id              TEXT NOT NULL,
                question            TEXT NOT NULL,
                incorrect_sql       TEXT NOT NULL DEFAULT '',
                incorrect_answer    TEXT NOT NULL DEFAULT '',
                incorrect_columns   JSONB NOT NULL DEFAULT '[]'::jsonb,
                incorrect_row_count INT  NOT NULL DEFAULT 0,
                corrected_sql       TEXT NOT NULL,
                corrected_columns   JSONB NOT NULL DEFAULT '[]'::jsonb,
                corrected_rows      JSONB NOT NULL DEFAULT '[]'::jsonb,
                corrected_row_count INT  NOT NULL DEFAULT 0,
                corrected_truncated BOOLEAN NOT NULL DEFAULT false,
                plan_cost           DOUBLE PRECISION,
                user_comment        TEXT NOT NULL DEFAULT '',
                reviewer            TEXT NOT NULL DEFAULT '',
                review_note         TEXT NOT NULL DEFAULT '',
                agent_version       TEXT NOT NULL DEFAULT '',
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(kind.table))
    )
    conn.commit()


def ensure_vector_table(conn: psycopg.Connection, kind: Kind, dimension: int) -> None:
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                fix_id          TEXT PRIMARY KEY REFERENCES {} (fix_id) ON DELETE CASCADE,
                question        TEXT NOT NULL,
                incorrect_sql   TEXT NOT NULL,
                corrected_sql   TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding       vector({}) NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(kind.vector_table), sql.Identifier(kind.table), sql.Literal(dimension))
    )
    conn.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING hnsw (embedding vector_cosine_ops)").format(
            sql.Identifier(f"idx_{kind.vector_table}_hnsw"), sql.Identifier(kind.vector_table)
        )
    )
    conn.commit()


def _vector_table_exists(conn: psycopg.Connection, kind: Kind) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (kind.vector_table,)).fetchone()
    return bool(row["present"])


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def next_fix_id(conn: psycopg.Connection, kind: Kind) -> str:
    row = conn.execute(
        sql.SQL(
            "SELECT max(substring(fix_id FROM 2)::int) AS n FROM {} WHERE fix_id ~ %s"
        ).format(sql.Identifier(kind.table)),
        (rf"^{re.escape(kind.prefix)}\d+$",),
    ).fetchone()
    return f"{kind.prefix}{(row['n'] or 0) + 1:04d}"


def find_by_submission(conn: psycopg.Connection, kind: Kind, submission_id: str) -> str | None:
    row = conn.execute(
        sql.SQL("SELECT fix_id FROM {} WHERE submission_id = %s").format(sql.Identifier(kind.table)),
        (submission_id,),
    ).fetchone()
    return row["fix_id"] if row else None


def save(conn: psycopg.Connection, kind: Kind, fix: Fix) -> Fix:
    """Insert the fix under the next id in this store, and return it as stored.

    One per submission: the UNIQUE on `submission_id` is what makes a double
    click, or two reviewers at once, one record rather than two. The id is
    taken under a lock on the table, so two saves cannot both take W0007.
    """
    # The lock comes first, so the check below and the insert after it see
    # the same table: a second save of the same submission waits here, then
    # finds the first one's record instead of racing it to the constraint.
    conn.execute(
        sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(sql.Identifier(kind.table))
    )
    existing = find_by_submission(conn, kind, fix.submission_id)
    if existing is not None:
        conn.rollback()
        raise AlreadyFixed(fix.submission_id, existing)
    fix.fix_id = next_fix_id(conn, kind)
    values = [
        json.dumps(getattr(fix, column)) if column in _JSON_COLUMNS else getattr(fix, column)
        for column in FIX_COLUMNS
    ]
    row = conn.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING created_at").format(
            sql.Identifier(kind.table),
            sql.SQL(", ").join(sql.Identifier(c) for c in FIX_COLUMNS),
            sql.SQL(", ").join(
                sql.SQL("%s::jsonb") if c in _JSON_COLUMNS else sql.Placeholder() for c in FIX_COLUMNS
            ),
        ),
        values,
    ).fetchone()
    conn.commit()
    fix.created_at = row["created_at"]
    return fix


def delete_by_submission(conn: psycopg.Connection, kind: Kind, submission_id: str) -> Fix | None:
    """Take a submission's fix out of the store, and return it as it was.

    Its vector goes with it: the vector table's key references the record
    with `ON DELETE CASCADE`, so a fix cannot be retrievable after it is
    gone. None when the store holds nothing for the submission -- deleted by
    hand, or never saved.
    """
    row = conn.execute(
        sql.SQL("DELETE FROM {} WHERE submission_id = %s RETURNING *").format(sql.Identifier(kind.table)),
        (submission_id,),
    ).fetchone()
    conn.commit()
    return Fix(**dict(row)) if row else None


def listing(conn: psycopg.Connection, kind: Kind, limit: int = 50) -> list[Fix]:
    """Newest first, each saying whether its question has a vector yet."""
    if _vector_table_exists(conn, kind):
        query = sql.SQL(
            "SELECT f.*, (v.fix_id IS NOT NULL) AS embedded FROM {} f "
            "LEFT JOIN {} v ON v.fix_id = f.fix_id ORDER BY f.created_at DESC, f.fix_id DESC LIMIT %s"
        ).format(sql.Identifier(kind.table), sql.Identifier(kind.vector_table))
    else:
        query = sql.SQL(
            "SELECT f.*, false AS embedded FROM {} f ORDER BY f.created_at DESC, f.fix_id DESC LIMIT %s"
        ).format(sql.Identifier(kind.table))
    return [Fix(**dict(row)) for row in conn.execute(query, (limit,)).fetchall()]


def count(conn: psycopg.Connection, kind: Kind) -> int:
    row = conn.execute(
        sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(kind.table))
    ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# The RAG half
# ---------------------------------------------------------------------------


def unembedded(conn: psycopg.Connection, kind: Kind, model: str) -> list[dict[str, Any]]:
    """Fixes whose question has no vector, or one from another model or text."""
    if not _vector_table_exists(conn, kind):
        query = sql.SQL(
            "SELECT fix_id, question, incorrect_sql, corrected_sql FROM {} ORDER BY fix_id"
        ).format(sql.Identifier(kind.table))
        return [dict(row) for row in conn.execute(query).fetchall()]
    rows = conn.execute(
        sql.SQL(
            "SELECT f.fix_id, f.question, f.incorrect_sql, f.corrected_sql, "
            "v.content_hash, v.embedding_model FROM {} f LEFT JOIN {} v ON v.fix_id = f.fix_id "
            "ORDER BY f.fix_id"
        ).format(sql.Identifier(kind.table), sql.Identifier(kind.vector_table))
    ).fetchall()
    return [
        {k: row[k] for k in ("fix_id", "question", "incorrect_sql", "corrected_sql")}
        for row in rows
        if row["embedding_model"] != model or row["content_hash"] != content_hash(row["question"])
    ]


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def write_vectors(
    conn: psycopg.Connection,
    kind: Kind,
    rows: Sequence[dict[str, Any]],
    vectors: Sequence[Sequence[float]],
    model: str,
) -> int:
    for row, vector in zip(rows, vectors):
        conn.execute(
            sql.SQL(
                """
                INSERT INTO {} (fix_id, question, incorrect_sql, corrected_sql,
                                content_hash, embedding_model, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (fix_id) DO UPDATE SET
                    question = EXCLUDED.question,
                    incorrect_sql = EXCLUDED.incorrect_sql,
                    corrected_sql = EXCLUDED.corrected_sql,
                    content_hash = EXCLUDED.content_hash,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding = EXCLUDED.embedding,
                    created_at = now()
                """
            ).format(sql.Identifier(kind.vector_table)),
            (
                row["fix_id"],
                row["question"],
                row["incorrect_sql"],
                row["corrected_sql"],
                content_hash(row["question"]),
                model,
                vector_literal(vector),
            ),
        )
    conn.commit()
    return len(rows)


def search(
    conn: psycopg.Connection, kind: Kind, vector: Sequence[float], limit: int = 5
) -> list[dict[str, Any]]:
    """The fixes whose questions are nearest this one -- what an agent will ask."""
    if not _vector_table_exists(conn, kind):
        return []
    rows = conn.execute(
        sql.SQL(
            "SELECT fix_id, question, incorrect_sql, corrected_sql, "
            "embedding <=> %s::vector AS distance FROM {} ORDER BY distance LIMIT %s"
        ).format(sql.Identifier(kind.vector_table)),
        (vector_literal(vector), limit),
    ).fetchall()
    return [dict(row) for row in rows]


class FixStore:
    """One kind's store, as an object the routes can be handed or faked."""

    def __init__(self, kind: Kind, url: str) -> None:
        self.kind = kind
        self.url = url

    def setup(self) -> None:
        with connection(self.url) as conn:
            ensure_schema(conn, self.kind)

    def ping(self) -> None:
        with connection(self.url) as conn:
            conn.execute("SELECT 1").fetchone()

    def save(self, fix: Fix) -> Fix:
        with connection(self.url) as conn:
            return save(conn, self.kind, fix)

    def delete(self, submission_id: str) -> Fix | None:
        with connection(self.url) as conn:
            return delete_by_submission(conn, self.kind, submission_id)

    def find_by_submission(self, submission_id: str) -> str | None:
        with connection(self.url) as conn:
            return find_by_submission(conn, self.kind, submission_id)

    def listing(self, limit: int = 50) -> list[Fix]:
        with connection(self.url) as conn:
            return listing(conn, self.kind, limit)

    def count(self) -> int:
        with connection(self.url) as conn:
            return count(conn, self.kind)

    def search(self, vector: Sequence[float], limit: int = 5) -> list[dict[str, Any]]:
        with connection(self.url) as conn:
            return search(conn, self.kind, vector, limit)

    def embed_pending(self, embedder: Embedder) -> EmbedResult:
        """Embed every question in this store that has no current vector."""
        result = EmbedResult(ran=True)
        try:
            with connection(self.url) as conn:
                pending = unembedded(conn, self.kind, embedder.model_name)
                result.pending = len(pending)
                if not pending:
                    return result
                vectors = embedder.embed([row["question"] for row in pending])
                ensure_vector_table(conn, self.kind, len(vectors[0]))
                result.embedded = write_vectors(conn, self.kind, pending, vectors, embedder.model_name)
                result.pending -= result.embedded
        except Exception as exc:  # noqa: BLE001 - the record is saved; say why the vector is not
            result.error = f"{type(exc).__name__}: {exc}"
        return result
