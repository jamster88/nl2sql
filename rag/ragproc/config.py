"""Shared settings for the chunking and embedding pipeline.

Everything is environment-driven with CLI overrides, so the same image can be
pointed at a different database or embedding host without a rebuild.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Chunk store (plain Postgres) -- deliberately not 5432, which is the retail
# testing database.
DEFAULT_CHUNK_DB_URL = "postgresql://ragproc:ragproc@localhost:5433/nl2sql_chunks"
# Vector store (pgvector).
DEFAULT_VECTOR_DB_URL = "postgresql://ragproc:ragproc@localhost:5434/nl2sql_vectors"

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_EMBED_DIM = 1024


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass
class Settings:
    chunk_db_url: str = DEFAULT_CHUNK_DB_URL
    vector_db_url: str = DEFAULT_VECTOR_DB_URL

    embed_backend: str = "ollama"  # "ollama" or "sentence-transformers"
    embed_model: str = DEFAULT_EMBED_MODEL
    embed_dim: int = DEFAULT_EMBED_DIM
    ollama_url: str = DEFAULT_OLLAMA_URL
    embed_batch_size: int = 16

    # Chunking knobs, passed through to the semantic chunker.
    threshold_percentile: int = 60
    max_chunk_tokens: int = 500
    min_chunk_tokens: int = 40

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            chunk_db_url=os.getenv("CHUNK_DB_URL", DEFAULT_CHUNK_DB_URL),
            vector_db_url=os.getenv("VECTOR_DB_URL", DEFAULT_VECTOR_DB_URL),
            embed_backend=os.getenv("EMBED_BACKEND", "ollama"),
            embed_model=os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL),
            embed_dim=_env_int("EMBED_DIM", DEFAULT_EMBED_DIM),
            ollama_url=os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL),
            embed_batch_size=_env_int("EMBED_BATCH_SIZE", 16),
            threshold_percentile=_env_int("CHUNK_THRESHOLD_PERCENTILE", 60),
            max_chunk_tokens=_env_int("MAX_CHUNK_TOKENS", 500),
            min_chunk_tokens=_env_int("MIN_CHUNK_TOKENS", 40),
        )


def document_slug(path_or_name: str) -> str:
    """Turn 'knowledge/business_index.md' into 'business_index'."""
    base = os.path.basename(path_or_name)
    base = re.sub(r"\.(md|markdown|txt)$", "", base, flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_")
    if not slug:
        raise ValueError(f"Cannot derive a table name from {path_or_name!r}")
    if slug[0].isdigit():
        slug = f"doc_{slug}"
    return slug


def chunk_table(slug: str) -> str:
    return f"{slug}_chunks"


def embedding_table(slug: str) -> str:
    return f"{slug}_embeddings"
