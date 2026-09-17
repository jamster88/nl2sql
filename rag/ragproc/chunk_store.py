"""Chunk storage: one Postgres table per source document.

Chunk ids are derived from a hash of the chunk's own content, so re-running
the loader after editing a document leaves untouched chunks with their
existing id. That is what makes the incremental update path cheap: only new
ids need embedding, and ids that disappeared are deleted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from .chunker import Chunk
from .config import chunk_table

REGISTRY_TABLE = "rag_documents"


@dataclass
class LoadStats:
    inserted: int = 0
    refreshed: int = 0
    deleted: int = 0
    unchanged: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.deleted)

    def __str__(self) -> str:
        return (
            f"{self.inserted} new, {self.unchanged} unchanged, "
            f"{self.deleted} removed, {self.refreshed} re-ordered"
        )


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def chunk_id_for(slug: str, chunk: Chunk) -> str:
    return f"{slug}:{chunk.content_hash[:16]}"


def ensure_registry(conn: psycopg.Connection) -> None:
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                slug         TEXT PRIMARY KEY,
                source_path  TEXT NOT NULL,
                file_hash    TEXT NOT NULL,
                chunk_count  INT  NOT NULL,
                chunk_table  TEXT NOT NULL,
                settings     JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(REGISTRY_TABLE))
    )


def ensure_chunk_table(conn: psycopg.Connection, slug: str) -> str:
    table = chunk_table(slug)
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                chunk_id       TEXT PRIMARY KEY,
                source_doc     TEXT NOT NULL,
                ordinal        INT  NOT NULL,
                heading_path   TEXT,
                chunk_meta     JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                content        TEXT NOT NULL,
                token_estimate INT  NOT NULL,
                content_hash   TEXT NOT NULL,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(table))
    )
    conn.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (ordinal)").format(
            sql.Identifier(f"idx_{table}_ordinal"), sql.Identifier(table)
        )
    )
    return table


def store_chunks(
    conn: psycopg.Connection,
    slug: str,
    source_path: Path,
    chunks: list[Chunk],
    settings: dict | None = None,
) -> LoadStats:
    table = ensure_chunk_table(conn, slug)
    ensure_registry(conn)

    incoming = {chunk_id_for(slug, c): c for c in chunks}
    existing = {
        row[0]
        for row in conn.execute(
            sql.SQL("SELECT chunk_id FROM {}").format(sql.Identifier(table))
        ).fetchall()
    }

    stats = LoadStats()
    for chunk_id, chunk in incoming.items():
        if chunk_id in existing:
            conn.execute(
                sql.SQL(
                    "UPDATE {} SET ordinal=%s, heading_path=%s, chunk_meta=%s, "
                    "token_estimate=%s, updated_at=now() WHERE chunk_id=%s"
                ).format(sql.Identifier(table)),
                (
                    chunk.ordinal,
                    chunk.heading_path,
                    json.dumps(chunk.meta),
                    chunk.token_estimate,
                    chunk_id,
                ),
            )
            stats.unchanged += 1
            stats.refreshed += 1
        else:
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (chunk_id, source_doc, ordinal, heading_path, "
                    "chunk_meta, content, token_estimate, content_hash) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
                ).format(sql.Identifier(table)),
                (
                    chunk_id,
                    slug,
                    chunk.ordinal,
                    chunk.heading_path,
                    json.dumps(chunk.meta),
                    chunk.content,
                    chunk.token_estimate,
                    chunk.content_hash,
                ),
            )
            stats.inserted += 1

    stale = existing - set(incoming)
    if stale:
        conn.execute(
            sql.SQL("DELETE FROM {} WHERE chunk_id = ANY(%s)").format(sql.Identifier(table)),
            (list(stale),),
        )
        stats.deleted = len(stale)

    conn.execute(
        sql.SQL(
            """
            INSERT INTO {} (slug, source_path, file_hash, chunk_count, chunk_table, settings, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (slug) DO UPDATE SET
                source_path = EXCLUDED.source_path,
                file_hash   = EXCLUDED.file_hash,
                chunk_count = EXCLUDED.chunk_count,
                chunk_table = EXCLUDED.chunk_table,
                settings    = EXCLUDED.settings,
                updated_at  = now()
            """
        ).format(sql.Identifier(REGISTRY_TABLE)),
        (
            slug,
            str(source_path),
            file_hash(source_path),
            len(chunks),
            table,
            json.dumps(settings or {}),
        ),
    )
    conn.commit()
    return stats


def fetch_chunks(conn: psycopg.Connection, slug: str) -> list[dict]:
    table = chunk_table(slug)
    rows = conn.execute(
        sql.SQL(
            "SELECT chunk_id, source_doc, ordinal, heading_path, chunk_meta, "
            "content, content_hash FROM {} ORDER BY ordinal"
        ).format(sql.Identifier(table))
    ).fetchall()
    return [
        {
            "chunk_id": r[0],
            "source_doc": r[1],
            "ordinal": r[2],
            "heading_path": r[3],
            "chunk_meta": r[4],
            "content": r[5],
            "content_hash": r[6],
        }
        for r in rows
    ]


def known_documents(conn: psycopg.Connection) -> list[dict]:
    ensure_registry(conn)
    rows = conn.execute(
        sql.SQL(
            "SELECT slug, source_path, file_hash, chunk_count, updated_at FROM {} ORDER BY slug"
        ).format(sql.Identifier(REGISTRY_TABLE))
    ).fetchall()
    return [
        {
            "slug": r[0],
            "source_path": r[1],
            "file_hash": r[2],
            "chunk_count": r[3],
            "updated_at": r[4],
        }
        for r in rows
    ]
