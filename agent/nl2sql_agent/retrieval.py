"""Knowledge-base retrieval over the pgvector store built by the RAG pipeline.

The store holds one table per knowledge document (`<doc>_embeddings`), each row
a semantically chunked section with its bge-m3 embedding, the heading path it
came from, and a `chunk_meta` JSON blob carrying the authored metadata
(`table`, `domain`, `grain`, `keywords`, ...).

Retrieval embeds the question once and runs a cosine-distance search against
every collection, so a question picks up DDL detail, data-dictionary context and
business rules together. The `table` key in `chunk_meta` doubles as a table-name
hint for the selection step.

Everything here degrades gracefully: if the vector database or the embedding
model is unreachable, the agent falls back to schema-only behavior rather than
failing the question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Collections are discovered from the catalog, but the name still gets
# interpolated into SQL, so it has to match a strict identifier pattern.
_SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

COLLECTION_SUFFIX = "_embeddings"


class KnowledgeUnavailableError(RuntimeError):
    """The vector store or the embedding model could not be reached."""


@dataclass
class RetrievedChunk:
    collection: str
    chunk_id: str
    source_doc: str
    heading_path: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)
    distance: float = 0.0

    @property
    def table(self) -> str | None:
        """The table this chunk documents, when it documents exactly one."""
        value = self.meta.get("table")
        return value if isinstance(value, str) and value else None


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


def build_embedder(settings) -> Embedder:
    """An Ollama embedding client for the configured model.

    Reuses langchain-ollama (already a dependency for the chat model) rather
    than adding an HTTP client just for this.
    """
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(model=settings.embed_model, base_url=settings.embed_base_url)


def format_chunks(chunks: list[RetrievedChunk], max_chars: int = 12000) -> str:
    """Render retrieved chunks for a prompt, newest-best first, under a budget."""
    blocks: list[str] = []
    used = 0
    for chunk in chunks:
        header = f"--- {chunk.source_doc}: {chunk.heading_path} (distance {chunk.distance:.3f})"
        block = f"{header}\n{chunk.content.strip()}"
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def tables_mentioned(chunks: list[RetrievedChunk]) -> list[str]:
    """Table names the retrieved chunks explicitly document, best match first."""
    seen: list[str] = []
    for chunk in chunks:
        name = chunk.table
        if name and name not in seen:
            seen.append(name)
    return seen


class KnowledgeBase:
    """Cosine-similarity search across every knowledge collection."""

    def __init__(
        self,
        url: str,
        embedder: Embedder,
        *,
        top_k: int = 4,
        collections: list[str] | None = None,
        statement_timeout_ms: int = 15000,
    ) -> None:
        self._engine: Engine = create_engine(url, pool_pre_ping=True)
        self._embedder = embedder
        self._top_k = top_k
        self._configured_collections = collections
        self._statement_timeout_ms = statement_timeout_ms
        self._cached_collections: list[str] | None = None

    def collections(self) -> list[str]:
        """Every `<doc>_embeddings` table in the store.

        Discovered rather than hardcoded, so adding a knowledge document to the
        RAG pipeline makes it searchable here without a code change.
        """
        if self._configured_collections is not None:
            return [c for c in self._configured_collections if _SAFE_IDENTIFIER.match(c)]
        if self._cached_collections is None:
            with self._engine.connect() as conn:
                rows = conn.exec_driver_sql(
                    """
                    SELECT c.relname
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind = 'r'
                      AND c.relname LIKE %s
                    ORDER BY c.relname
                    """,
                    (f"%{COLLECTION_SUFFIX}",),
                ).fetchall()
            self._cached_collections = [r[0] for r in rows if _SAFE_IDENTIFIER.match(r[0])]
        return list(self._cached_collections)

    def embedding_models(self) -> set[str]:
        """Which model(s) the stored vectors were produced with."""
        models: set[str] = set()
        with self._engine.connect() as conn:
            for collection in self.collections():
                rows = conn.exec_driver_sql(
                    f'SELECT DISTINCT embedding_model FROM "{collection}"'
                ).fetchall()
                models.update(r[0] for r in rows if r[0])
        return models

    def search(self, question: str, top_k: int | None = None) -> list[RetrievedChunk]:
        """Top-k chunks per collection, merged and sorted by cosine distance."""
        k = top_k if top_k is not None else self._top_k
        if k <= 0:
            return []

        try:
            vector = self._embedder.embed_query(question)
        except Exception as exc:
            raise KnowledgeUnavailableError(
                f"Could not embed the question: {exc}. Is the embedding model "
                "available on the configured Ollama host?"
            ) from exc

        literal = _vector_literal(vector)
        results: list[RetrievedChunk] = []
        try:
            with self._engine.connect() as conn:
                conn.exec_driver_sql(
                    f"SET statement_timeout = {int(self._statement_timeout_ms)}"
                )
                for collection in self.collections():
                    rows = conn.exec_driver_sql(
                        f"""
                        SELECT chunk_id, source_doc, heading_path, content, chunk_meta,
                               embedding <=> %s::vector AS distance
                        FROM "{collection}"
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (literal, literal, k),
                    ).fetchall()
                    for row in rows:
                        results.append(
                            RetrievedChunk(
                                collection=collection,
                                chunk_id=row[0],
                                source_doc=row[1],
                                heading_path=row[2] or "",
                                content=row[3] or "",
                                meta=row[4] or {},
                                distance=float(row[5]),
                            )
                        )
        except KnowledgeUnavailableError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError(
                f"Could not search the knowledge base: {exc}"
            ) from exc

        results.sort(key=lambda c: c.distance)
        return results


def _vector_literal(vector: list[float]) -> str:
    """pgvector's text input format, so no client-side vector type is needed."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"
