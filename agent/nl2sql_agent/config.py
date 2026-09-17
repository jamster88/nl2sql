"""Runtime settings for the NL2SQL agent.

Everything is environment-driven so the same image can point at a different
Ollama host, a different model, or a different database without a rebuild.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_OLLAMA_BASE_URL = "http://192.168.44.129:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.8:latest"
DEFAULT_DATABASE_URL = "postgresql+psycopg://nl2sql:nl2sql@postgres:5432/nl2sql_retail"

# The knowledge base built by the RAG pipeline: one pgvector collection per
# document in knowledge/. Host name is the compose service; override for a
# vector database running anywhere else.
DEFAULT_VECTOR_DB_URL = "postgresql+psycopg://ragproc:ragproc@vectordb:5432/nl2sql_vectors"
# Embeddings must come from the same model the store was built with, or the
# vectors live in different spaces and retrieval returns noise. bge-m3 is
# typically served by the Ollama on the machine running Docker, which is not
# necessarily the host serving the chat model.
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_EMBED_BASE_URL = "http://host.docker.internal:11434"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass
class Settings:
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    temperature: float = 0.0
    # Qwen3 emits reasoning into a separate field, so leaving this off costs
    # nothing in output quality but saves a lot of latency.
    reasoning: bool = False
    # Ollama defaults num_ctx to a few thousand tokens regardless of what the
    # model supports; the schema context alone can exceed that.
    num_ctx: int = 16384

    database_url: str = DEFAULT_DATABASE_URL
    db_schema: str = "public"

    # --- Retrieval (RAG) ------------------------------------------------
    # Retrieval is best-effort: when the vector store or the embedding model
    # is unreachable the agent still answers, just without knowledge context.
    rag_enabled: bool = True
    vector_db_url: str = DEFAULT_VECTOR_DB_URL
    embed_model: str = DEFAULT_EMBED_MODEL
    embed_base_url: str = DEFAULT_EMBED_BASE_URL
    # Chunks per collection; with three collections this is ~3x that many.
    rag_top_k: int = 4
    rag_max_context_chars: int = 12000

    sample_rows: int = 3
    max_rows: int = 50
    statement_timeout_ms: int = 30000
    max_sql_attempts: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            ollama_model=os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0")),
            reasoning=_env_bool("OLLAMA_REASONING", False),
            num_ctx=_env_int("OLLAMA_NUM_CTX", 16384),
            database_url=os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL),
            db_schema=os.getenv("DB_SCHEMA", "public"),
            rag_enabled=_env_bool("RAG_ENABLED", True),
            vector_db_url=os.getenv("VECTOR_DB_URL", DEFAULT_VECTOR_DB_URL),
            embed_model=os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL),
            embed_base_url=os.getenv("EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL),
            rag_top_k=_env_int("RAG_TOP_K", 4),
            rag_max_context_chars=_env_int("RAG_MAX_CONTEXT_CHARS", 12000),
            sample_rows=_env_int("SAMPLE_ROWS", 3),
            max_rows=_env_int("MAX_ROWS", 50),
            statement_timeout_ms=_env_int("STATEMENT_TIMEOUT_MS", 30000),
            max_sql_attempts=_env_int("MAX_SQL_ATTEMPTS", 3),
        )
