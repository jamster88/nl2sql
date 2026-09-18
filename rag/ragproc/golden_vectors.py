"""pgvector storage for the golden pairs: one table per embedded field.

The question and the reasoning target are embedded **separately** and kept in
separate tables, because the ensemble scores them separately and weights them
differently. Concatenating them into one vector would average two different
signals -- what the user is asking for, and what the query has to get right --
and lose the ability to weight one above the other.

Neither table is named `<doc>_embeddings`. The v2 knowledge retriever discovers
its collections by globbing for that suffix, and picking these up would have it
searching golden pairs with a column list they do not have. The `_vectors`
suffix keeps the two stores visibly distinct.
"""

from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql

QUESTION_TABLE = "golden_pair_question_vectors"
REASONING_TABLE = "golden_pair_reasoning_vectors"

# Which column of `golden_pairs` each table holds vectors for.
FIELD_TABLES = {
    "question": QUESTION_TABLE,
    "reasoning_target": REASONING_TABLE,
}


def connect(url: str) -> psycopg.Connection:
    conn = psycopg.connect(url)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    return conn


def ensure_table(conn: psycopg.Connection, field: str, dimension: int) -> str:
    table = FIELD_TABLES[field]
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                chunk_id        TEXT PRIMARY KEY,
                pair_id         TEXT NOT NULL UNIQUE,
                ordinal         INT  NOT NULL,
                field           TEXT NOT NULL,
                content         TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding       vector({}) NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(table), sql.Literal(dimension))
    )
    conn.execute(
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS {} ON {} USING hnsw (embedding vector_cosine_ops)"
        ).format(sql.Identifier(f"idx_{table}_hnsw"), sql.Identifier(table))
    )
    conn.commit()
    return table


def current_state(conn: psycopg.Connection, field: str) -> dict[str, tuple[str, str]]:
    """chunk_id -> (content_hash, embedding_model) for what is already stored."""
    table = FIELD_TABLES[field]
    try:
        rows = conn.execute(
            sql.SQL("SELECT chunk_id, content_hash, embedding_model FROM {}").format(
                sql.Identifier(table)
            )
        ).fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return {}
    return {r[0]: (r[1], r[2]) for r in rows}


def upsert(conn: psycopg.Connection, field: str, records: list[dict], model: str) -> int:
    table = FIELD_TABLES[field]
    for record in records:
        conn.execute(
            sql.SQL(
                """
                INSERT INTO {} (chunk_id, pair_id, ordinal, field, content,
                                content_hash, embedding_model, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    pair_id         = EXCLUDED.pair_id,
                    ordinal         = EXCLUDED.ordinal,
                    field           = EXCLUDED.field,
                    content         = EXCLUDED.content,
                    content_hash    = EXCLUDED.content_hash,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding       = EXCLUDED.embedding,
                    created_at      = now()
                """
            ).format(sql.Identifier(table)),
            (
                record["chunk_id"],
                record["pair_id"],
                record["ordinal"],
                field,
                record["content"],
                record["content_hash"],
                model,
                record["embedding"],
            ),
        )
    conn.commit()
    return len(records)


def delete_missing(conn: psycopg.Connection, field: str, keep_ids: list[str]) -> int:
    table = FIELD_TABLES[field]
    result = conn.execute(
        sql.SQL("DELETE FROM {} WHERE NOT (chunk_id = ANY(%s))").format(sql.Identifier(table)),
        (keep_ids,),
    )
    conn.commit()
    return result.rowcount or 0


def vector_literal(vector) -> str:
    """pgvector's text input format.

    A plain list of floats is sent as `double precision[]`, which has no `<=>`
    operator; the text form plus an explicit cast avoids needing a client-side
    vector type at all, and is what the agent-side retriever uses too.
    """
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def search(conn: psycopg.Connection, field: str, query_vector, limit: int = 5) -> list[dict]:
    """Nearest pairs by cosine distance, for probing the store from the CLI."""
    table = FIELD_TABLES[field]
    literal = vector_literal(query_vector)
    rows = conn.execute(
        sql.SQL(
            "SELECT chunk_id, pair_id, content, embedding <=> %s::vector AS distance "
            "FROM {} ORDER BY embedding <=> %s::vector, ordinal LIMIT %s"
        ).format(sql.Identifier(table)),
        (literal, literal, limit),
    ).fetchall()
    return [
        {"chunk_id": r[0], "pair_id": r[1], "content": r[2], "distance": float(r[3])}
        for r in rows
    ]
