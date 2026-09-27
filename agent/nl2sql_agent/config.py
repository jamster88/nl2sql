"""Runtime settings for the NL2SQL agent.

Everything is environment-driven so the same image can point at a different
Ollama host, a different model, or a different database without a rebuild.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_OLLAMA_BASE_URL = "http://192.168.10.82:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.8-256k"
# 256 * 1024. Ollama allocates the KV cache from this, so it is a real memory
# cost on the serving host, not just a cap.
DEFAULT_NUM_CTX = 262144
# The read-only role created by docker/reader_role.sql, not the owner that
# loads the data: the agent only ever reads.
DEFAULT_DATABASE_URL = "postgresql+psycopg://nl2sql_reader:nl2sql_reader@postgres:5432/nl2sql_retail"

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

# The context store: the plain-Postgres half of the RAG pipeline. It holds the
# golden question/SQL pairs as rows and the BM25 statistics over their keywords.
# The vectors for those same pairs live in the pgvector store above.
DEFAULT_CONTEXT_DB_URL = "postgresql+psycopg://ragproc:ragproc@chunkdb:5432/nl2sql_chunks"


def _env(name: str) -> str | None:
    """An environment variable, with empty and whitespace read as unset.

    Docker Compose forwards a variable the host has not set as an empty
    string, so without this every knob compose passes through would override
    its own default with nothing: `OLLAMA_MODEL=""` becomes a model with no
    name, and `SCHEMA_RETRIEVAL=""` is neither `vector` nor `llm`. Treating
    empty as absent is what lets compose forward a setting without also
    having to repeat its default and keep the two in step.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _env_str(name: str, default: str) -> str:
    raw = _env(name)
    return default if raw is None else raw


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return default if raw is None else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return default if raw is None else float(raw)


@dataclass
class Settings:
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    temperature: float = 0.0
    # Qwen3 emits reasoning into a separate field, so leaving this off costs
    # nothing in output quality but saves a lot of latency.
    reasoning: bool = False
    # Ollama defaults num_ctx to a few thousand tokens regardless of what the
    # model supports, and silently truncates past it. 256k is what the
    # qwen3.8-256k build is served with, and multi-shot needs the room: the
    # schema, the knowledge block and three worked SQL examples are all in the
    # same window.
    num_ctx: int = DEFAULT_NUM_CTX
    # How long to wait for the Ollama host to answer at all before deciding
    # it is not there. This is a reachability bound, not a generation one --
    # a question still takes as long as it takes. It exists because a routed
    # but silent host otherwise holds the connection until the operating
    # system gives up, which makes `/readyz` block for minutes.
    ollama_connect_timeout: float = 5.0

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

    # --- Worked examples (the golden-pair ensemble) ----------------------
    # Retrieved from the context store and the pgvector store together; like
    # knowledge retrieval, an unreachable store costs the examples and nothing
    # else.
    examples_enabled: bool = True
    context_db_url: str = DEFAULT_CONTEXT_DB_URL
    # Pairs handed to the model. Small on purpose: these are long SQL blocks,
    # and a wrong example is more expensive than a missing one.
    examples_top_k: int = 3
    # Pairs each of the three retrievers proposes before fusion. Wider than
    # top_k so a pair ranked mid-list by all three can still win overall.
    examples_candidate_k: int = 10
    # Ensemble weights, applied to each retriever's rank votes.
    example_weight_question: float = 0.50
    example_weight_keywords: float = 0.35
    example_weight_reasoning: float = 0.15
    # "score" (min-max normalise, then weighted sum) or "rrf" (weighted
    # reciprocal rank fusion). Measured on this corpus, score fusion retrieves
    # every pair from its own question and RRF at its usual k=60 manages 25/45.
    examples_fusion: str = "score"
    examples_rrf_k: int = 60
    # Second-stage rerank over the fused shortlist: "mmr" (grounding against the
    # pair's tables/SQL, then diversity across the chosen set), "relevance"
    # (grounding only), or "none" (fusion order).
    examples_rerank: str = "mmr"
    # How many fused candidates enter the rerank. Wider than examples_top_k, or
    # the reranker could only reorder what the fusion already picked.
    examples_rerank_k: int = 8
    # MMR: 1.0 is pure relevance, 0.0 pure diversity. Measured flat on recall
    # across that whole range, so the balanced value buys coverage for free.
    examples_rerank_lambda: float = 0.5
    # How far the grounding evidence is allowed to move the fused relevance.
    examples_grounding_weight: float = 0.25
    examples_max_context_chars: int = 8000
    # Whether the retrieved examples are replayed as exemplar turns in front of
    # the real question. On by default -- this is the generation architecture,
    # not an experiment. Retrieval stays a separate switch so the examples can
    # still be inspected with multi-shot off.
    multi_shot_enabled: bool = True

    sample_rows: int = 3
    max_rows: int = 50
    statement_timeout_ms: int = 30000

    # --- the one retry budget -------------------------------------------
    # Generations, not retries: one first draft and six repairs. Every
    # failure source -- static validation, the planner, execution, the
    # Completeness Reviewer, the audit -- increments the same counter, so
    # there is no way to loop that does not spend it (arch4 W5). arch4 set it
    # to 4 when four things could spend it; arch5 adds the reviewer, the one
    # gate a correct query can trip, and raises it to 7 (arch5 section 6.4).
    max_attempts: int = 7

    # --- v4: stage 1 ------------------------------------------------------
    # The Supervisor screens for prompt injection and out-of-domain questions
    # and classifies intent. It is the only input screen, which is why it is
    # on the happy path despite costing a model call (arch4 section 13.1).
    supervisor_enabled: bool = True
    # Clarification interrupts are off in batch and benchmark mode by
    # definition; an ambiguous verdict then collapses to "proceed".
    clarify_enabled: bool = False
    # "vector" is the v4 Schema Retriever (DDL-chunk vectors + FK closure);
    # "llm" is the v3 model call, kept for ablation.
    schema_retrieval: str = "vector"
    # Tables taken from the DDL-chunk vectors before FK closure widens them.
    schema_top_k: int = 6
    # The hard cap after closure. With 19 tables this is a real limit; the
    # largest of the 45 golden pairs touches 6 and the median touches 4.
    max_tables: int = 10
    literals_enabled: bool = True
    # A text column with more distinct values than this is not catalogued.
    # In this schema 42 of 43 text columns qualify; the exclusion is
    # fact_pos_retail_sales.basket_id at 258,308.
    literal_max_distinct: int = 500
    # Trigram/difflib similarity below which a match is not worth offering.
    literal_min_score: float = 0.6

    # --- v4: stage 3 ------------------------------------------------------
    # Estimated-plan cost ceiling. Calibrated on this dataset: the most
    # expensive of the 45 golden pairs plans at 125,767 and a full scan of
    # the sales fact at 20,096, while that fact cross-joined with dim_product
    # is 1.6 million and with itself 12.5 billion. So this is eight times the
    # hardest known-good query and below the cheapest cross join that
    # involves the fact table. Re-derive it whenever the data is regenerated.
    max_plan_cost: float = 1_000_000.0

    # --- arch5: the Completeness Reviewer ----------------------------------
    # The gate after execution that asks whether a result that ran is the
    # answer a person wanted: a label beside every id, the measure a ranking
    # was ranked by, the period stated. Off, results go straight to
    # presentation as in arch4 -- but a default period the generator applied
    # is still written to `assumptions`, because turning the gate off is an
    # ablation and not a licence to answer for a year nobody was told about.
    review_enabled: bool = True
    # Tier 2: the one reflective model call, made only when the rules pass.
    # Off keeps the rules and drops the call (arch5 sections 6.6 and 9).
    review_reflection_enabled: bool = True

    # --- v4: stage 4 ------------------------------------------------------
    narrate_enabled: bool = True
    audit_enabled: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ollama_base_url=_env_str("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            ollama_model=_env_str("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            temperature=_env_float("OLLAMA_TEMPERATURE", 0.0),
            reasoning=_env_bool("OLLAMA_REASONING", False),
            num_ctx=_env_int("OLLAMA_NUM_CTX", DEFAULT_NUM_CTX),
            ollama_connect_timeout=_env_float("OLLAMA_CONNECT_TIMEOUT", 5.0),
            database_url=_env_str("DATABASE_URL", DEFAULT_DATABASE_URL),
            db_schema=_env_str("DB_SCHEMA", "public"),
            rag_enabled=_env_bool("RAG_ENABLED", True),
            vector_db_url=_env_str("VECTOR_DB_URL", DEFAULT_VECTOR_DB_URL),
            embed_model=_env_str("EMBED_MODEL", DEFAULT_EMBED_MODEL),
            embed_base_url=_env_str("EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL),
            rag_top_k=_env_int("RAG_TOP_K", 4),
            rag_max_context_chars=_env_int("RAG_MAX_CONTEXT_CHARS", 12000),
            examples_enabled=_env_bool("EXAMPLES_ENABLED", True),
            context_db_url=_env_str("CONTEXT_DB_URL", DEFAULT_CONTEXT_DB_URL),
            examples_top_k=_env_int("EXAMPLES_TOP_K", 3),
            examples_candidate_k=_env_int("EXAMPLES_CANDIDATE_K", 10),
            example_weight_question=_env_float("EXAMPLE_WEIGHT_QUESTION", 0.50),
            example_weight_keywords=_env_float("EXAMPLE_WEIGHT_KEYWORDS", 0.35),
            example_weight_reasoning=_env_float("EXAMPLE_WEIGHT_REASONING", 0.15),
            examples_fusion=_env_str("EXAMPLES_FUSION", "score"),
            examples_rrf_k=_env_int("EXAMPLES_RRF_K", 60),
            examples_rerank=_env_str("EXAMPLES_RERANK", "mmr"),
            examples_rerank_k=_env_int("EXAMPLES_RERANK_K", 8),
            examples_rerank_lambda=_env_float("EXAMPLES_RERANK_LAMBDA", 0.5),
            examples_grounding_weight=_env_float("EXAMPLES_GROUNDING_WEIGHT", 0.25),
            examples_max_context_chars=_env_int("EXAMPLES_MAX_CONTEXT_CHARS", 8000),
            multi_shot_enabled=_env_bool("MULTI_SHOT_ENABLED", True),
            sample_rows=_env_int("SAMPLE_ROWS", 3),
            max_rows=_env_int("MAX_ROWS", 50),
            statement_timeout_ms=_env_int("STATEMENT_TIMEOUT_MS", 30000),
            # MAX_SQL_ATTEMPTS is v3's name for the same budget; it is still
            # honoured so an existing .env keeps working, but it counted
            # generations too, so the value carries over unchanged.
            max_attempts=_env_int("MAX_ATTEMPTS", _env_int("MAX_SQL_ATTEMPTS", 7)),
            supervisor_enabled=_env_bool("SUPERVISOR_ENABLED", True),
            clarify_enabled=_env_bool("CLARIFY_ENABLED", False),
            schema_retrieval=_env_str("SCHEMA_RETRIEVAL", "vector"),
            schema_top_k=_env_int("SCHEMA_TOP_K", 6),
            max_tables=_env_int("MAX_TABLES", 10),
            literals_enabled=_env_bool("LITERALS_ENABLED", True),
            literal_max_distinct=_env_int("LITERAL_MAX_DISTINCT", 500),
            literal_min_score=_env_float("LITERAL_MIN_SCORE", 0.6),
            max_plan_cost=_env_float("MAX_PLAN_COST", 1_000_000.0),
            review_enabled=_env_bool("REVIEW_ENABLED", True),
            review_reflection_enabled=_env_bool("REVIEW_REFLECTION_ENABLED", True),
            narrate_enabled=_env_bool("NARRATE_ENABLED", True),
            audit_enabled=_env_bool("AUDIT_ENABLED", True),
        )
