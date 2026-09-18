"""Vector storage in pgvector: one table per source document."""

from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql

from .config import embedding_table


@dataclass
class EmbedStats:
    embedded: int = 0
    skipped: int = 0
    deleted: int = 0

    def __str__(self) -> str:
        return f"{self.embedded} embedded, {self.skipped} already current, {self.deleted} removed"


def connect(url: str) -> psycopg.Connection:
    conn = psycopg.connect(url)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    return conn


def ensure_embedding_table(conn: psycopg.Connection, slug: str, dimension: int) -> str:
    table = embedding_table(slug)
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                chunk_id        TEXT PRIMARY KEY,
                source_doc      TEXT NOT NULL,
                ordinal         INT  NOT NULL,
                heading_path    TEXT,
                chunk_meta      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                content         TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding       vector({}) NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(table), sql.Literal(dimension))
    )
    # Cosine distance matches how bge-m3 embeddings are normally compared.
    conn.execute(
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS {} ON {} USING hnsw (embedding vector_cosine_ops)"
        ).format(sql.Identifier(f"idx_{table}_hnsw"), sql.Identifier(table))
    )
    conn.commit()
    return table


def current_state(conn: psycopg.Connection, slug: str) -> dict[str, str]:
    """chunk_id -> embedding_model for everything already stored."""
    table = embedding_table(slug)
    rows = conn.execute(
        sql.SQL("SELECT chunk_id, embedding_model FROM {}").format(sql.Identifier(table))
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def upsert_embeddings(
    conn: psycopg.Connection, slug: str, records: list[dict], model: str
) -> int:
    table = embedding_table(slug)
    for record in records:
        conn.execute(
            sql.SQL(
                """
                INSERT INTO {} (chunk_id, source_doc, ordinal, heading_path, chunk_meta,
                                content, content_hash, embedding_model, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    ordinal         = EXCLUDED.ordinal,
                    heading_path    = EXCLUDED.heading_path,
                    chunk_meta      = EXCLUDED.chunk_meta,
                    content         = EXCLUDED.content,
                    content_hash    = EXCLUDED.content_hash,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding       = EXCLUDED.embedding,
                    created_at      = now()
                """
            ).format(sql.Identifier(table)),
            (
                record["chunk_id"],
                record["source_doc"],
                record["ordinal"],
                record["heading_path"],
                json.dumps(record["chunk_meta"]),
                record["content"],
                record["content_hash"],
                model,
                record["embedding"],
            ),
        )
    conn.commit()
    return len(records)


def delete_missing(conn: psycopg.Connection, slug: str, keep_ids: list[str]) -> int:
    table = embedding_table(slug)
    result = conn.execute(
        sql.SQL("DELETE FROM {} WHERE NOT (chunk_id = ANY(%s))").format(sql.Identifier(table)),
        (keep_ids,),
    )
    conn.commit()
    return result.rowcount or 0


def vector_literal(vector) -> str:
    """pgvector's text input format.

    A plain list of floats binds as `double precision[]`, which has no `<=>`
    operator at all, so the query fails rather than returning something wrong.
    The text form plus an explicit cast avoids needing a client-side vector
    type, and matches what the agent-side retrievers do.
    """
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def search(
    conn: psycopg.Connection, slug: str, query_vector, limit: int = 5
) -> list[dict]:
    """Nearest chunks by cosine distance -- used by the RAG retriever later."""
    table = embedding_table(slug)
    literal = vector_literal(query_vector)
    rows = conn.execute(
        sql.SQL(
            "SELECT chunk_id, heading_path, content, embedding <=> %s::vector AS distance "
            "FROM {} ORDER BY embedding <=> %s::vector, ordinal LIMIT %s"
        ).format(sql.Identifier(table)),
        (literal, literal, limit),
    ).fetchall()
    return [
        {"chunk_id": r[0], "heading_path": r[1], "content": r[2], "distance": float(r[3])}
        for r in rows
    ]
