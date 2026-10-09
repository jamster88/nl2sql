"""Runtime settings for the NL2SQL agent.

Everything is environment-driven so the same image can point at a different
Ollama host, a different model, or a different database without a rebuild.
"""

from __future__ import annotations

from dataclasses import dataclass

from nl2sql_common.env import (
    env_bool as _env_bool,
    env_float as _env_float,
    env_int as _env_int,
    env_str as _env_str,
    env_url as _env_url,
)

DEFAULT_OLLAMA_BASE_URL = "http://192.168.10.82:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.8-256k"
# 256 * 1024. Ollama allocates the KV cache from this, so it is a real memory
# cost on the serving host, not just a cap.
DEFAULT_NUM_CTX = 262144
# The window of a routed model other than OLLAMA_MODEL (arch5.2). Measured on
# the benchmark, the largest prompt any agent sends is well inside it; see
# MODEL_NUM_CTX in agent/README.md.
DEFAULT_MODEL_NUM_CTX = 32768
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

# The snippet store: the SQL snippets of context_questions/sql_snippets.md --
# joins, filters, measures, dimensions -- with an embedding of what each
# means. Read as a role that can SELECT and nothing else, which the loader
# (rag/07_load_snippets.py) creates.
DEFAULT_SNIPPET_DB_URL = "postgresql+psycopg://snippets_reader:snippets_reader@nl2sql-stores:5432/nl2sql_snippets"

# The MLflow experiment every run's trace is filed under. One experiment for
# the agent wherever it runs -- the CLI, the API, the benchmark -- so its
# traces can be compared side by side; the trace's tags say which it was.
DEFAULT_MLFLOW_EXPERIMENT = "nl2sql-agent"

# How many rewordings the ensemble may ask beside the original: at least three
# and at most ten, as the architecture was asked for (arch7 section 22.9).
MIN_PARAPHRASES = 3
MAX_PARAPHRASES = 10


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
    # The most tokens one call may generate, and how long one call may take.
    # Without them a model that degenerates -- found calibrating a reasoning
    # model with thinking off -- generates without end, since Ollama shifts a
    # full window rather than stopping, and holds the question with it. The
    # largest answer any agent gave on the benchmark was 440 tokens; raise
    # the cap if you turn reasoning on, since thinking counts against it. A
    # routed call that hits either limit falls back like any other failure.
    num_predict: int = 2048
    ollama_timeout: float = 600.0

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

    # --- SQL snippets ----------------------------------------------------
    # Verified pieces of SQL beside what they mean -- a join's keys, what a
    # phrase filters to, how a measure is calculated -- retrieved by keyword
    # phrase and by meaning, and shown to the generator when every table
    # they use is in scope. Best-effort like the rest of retrieval.
    snippets_enabled: bool = True
    snippet_db_url: str = DEFAULT_SNIPPET_DB_URL
    # At most this many shown. A question combines a few -- a measure, a
    # filter, the join between them -- and more than that is noise.
    snippets_top_k: int = 5
    # The combined keyword-and-meaning score a snippet must reach, 0..1.
    snippets_min_score: float = 0.35
    # The cosine similarity at which a snippet qualifies on meaning alone,
    # with none of its keyword phrases in the question. High on purpose: a
    # question is rarely that close to one snippet by meaning.
    snippets_min_similarity: float = 0.62
    snippets_max_context_chars: int = 4000

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

    # --- arch5.2: model routing -------------------------------------------
    # Each model call is routed by its task and the complexity of the task in
    # hand to the cheapest model on the host the catalog shows is suited to
    # it (arch5.2 section 15). Off is v5.1: every call to OLLAMA_MODEL.
    model_routing_enabled: bool = True
    # The catalog models/build_catalog.py writes. None means every rung is
    # OLLAMA_MODEL; compose mounts the committed one.
    model_catalog: str = ""
    # Route on the catalog's prior -- a guess from size and description -- as
    # well as on what calibration measured. Off: by size alone a 2023 mixture
    # of experts outranks the reference model.
    model_route_on_prior: bool = False
    # Pins, per task: one model for every rung, or light=a,standard=b,heavy=c.
    model_route_supervisor: str = ""
    model_route_generator: str = ""
    model_route_reflection: str = ""
    model_route_narrator: str = ""
    model_route_repair: str = ""
    # At most this many distinct models in the table: Ollama swaps from disk
    # when more are asked for, and a ladder that thrashes is slower than one
    # model (section 15.4).
    model_max_loaded: int = 3
    # The context window of every routed model but OLLAMA_MODEL, which keeps
    # OLLAMA_NUM_CTX. Fixed per model because Ollama reloads a model whenever
    # a request asks for a different window.
    model_num_ctx: int = DEFAULT_MODEL_NUM_CTX
    # How long a model stays loaded after a call, sent with every call when
    # routing is on, so the models a session uses are resident by its second
    # question rather than reloaded after Ollama's own five minutes.
    ollama_keep_alive: str = "30m"

    # --- tracing (MLflow) -------------------------------------------------
    # Where each run's trace goes: a span per agent and per model call,
    # under one trace per question (`tracing.py`). Empty is off, and so is a
    # server that does not answer -- a trace is worth having and never worth
    # an answer. Compose's is the `mlflow` service; outside it, set this.
    mlflow_tracking_uri: str = ""
    # The experiment the traces are filed under, created if it is missing.
    mlflow_experiment_name: str = DEFAULT_MLFLOW_EXPERIMENT

    # --- arch7: asking it several ways --------------------------------------
    # The question reworded, the pipeline run once per wording, the results
    # validated against each other and one chosen (arch7 section 22). Off is
    # arch6 to the answer. 7.0 is built in phases, and so far there is one
    # wave and no Judge or fusion: ENSEMBLE_WAVES, ENSEMBLE_DEADLINE_SECONDS,
    # ENSEMBLE_JUDGE_ENABLED and ENSEMBLE_MAX_CLAIMS are read, checked and
    # carried for the stages that will read them.
    ensemble_enabled: bool = True
    # Rewordings in the first wave, beside the original: 3 to 10.
    ensemble_paraphrases: int = 3
    # The most the Paraphraser writes, and a second wave spends from.
    ensemble_max_paraphrases: int = MAX_PARAPHRASES
    # The one loop counter's limit: a second wave only on disagreement.
    ensemble_waves: int = 2
    # No wave starts this many seconds after the question arrived; 0 is none.
    ensemble_deadline_seconds: float = 0.0
    # The Judge, asked only when the votes cannot decide; off delivers the
    # plurality as contested.
    ensemble_judge_enabled: bool = True
    # The fused narrative's length, in claims.
    ensemble_max_claims: int = 8
    # Columns other agreeing runs carried, joined on the entity's key.
    ensemble_fuse_columns: bool = True
    # Model calls in flight to the Ollama host at once, process-wide, and
    # candidate runs at once per question (`hostgate.py`). One is serial: the
    # host this was built against serves one call at a time. Set it no higher
    # than the host's own OLLAMA_NUM_PARALLEL.
    ollama_parallel_calls: int = 1
    # Pins for the ensemble's two routing tasks, the Paraphraser and the
    # Judge, as the five above.
    model_route_paraphraser: str = ""
    model_route_judge: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Stop at start on a setting out of range, naming it and its bound.

        Run on construction, and again by whoever sets a field afterwards --
        the CLI's flags, the benchmark's configurations -- since a dataclass
        does not check an assignment.
        """
        problems = []
        if not MIN_PARAPHRASES <= self.ensemble_paraphrases <= MAX_PARAPHRASES:
            problems.append(
                f"ENSEMBLE_PARAPHRASES is {self.ensemble_paraphrases}; it must be "
                f"{MIN_PARAPHRASES} to {MAX_PARAPHRASES}"
            )
        if not self.ensemble_paraphrases <= self.ensemble_max_paraphrases <= MAX_PARAPHRASES:
            problems.append(
                f"ENSEMBLE_MAX_PARAPHRASES is {self.ensemble_max_paraphrases}; it must be at least "
                f"ENSEMBLE_PARAPHRASES ({self.ensemble_paraphrases}) and at most {MAX_PARAPHRASES}"
            )
        for name, value, least in (
            ("ENSEMBLE_WAVES", self.ensemble_waves, 1),
            ("ENSEMBLE_DEADLINE_SECONDS", self.ensemble_deadline_seconds, 0),
            ("ENSEMBLE_MAX_CLAIMS", self.ensemble_max_claims, 1),
            ("OLLAMA_PARALLEL_CALLS", self.ollama_parallel_calls, 1),
        ):
            if value < least:
                problems.append(f"{name} is {value:g}; it must be at least {least}")
        if problems:
            raise ValueError("; ".join(problems))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ollama_base_url=_env_str("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            ollama_model=_env_str("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            temperature=_env_float("OLLAMA_TEMPERATURE", 0.0),
            reasoning=_env_bool("OLLAMA_REASONING", False),
            num_ctx=_env_int("OLLAMA_NUM_CTX", DEFAULT_NUM_CTX),
            ollama_connect_timeout=_env_float("OLLAMA_CONNECT_TIMEOUT", 5.0),
            num_predict=_env_int("OLLAMA_NUM_PREDICT", 2048),
            ollama_timeout=_env_float("OLLAMA_TIMEOUT", 600.0),
            database_url=_env_url("DATABASE_URL", DEFAULT_DATABASE_URL),
            db_schema=_env_str("DB_SCHEMA", "public"),
            rag_enabled=_env_bool("RAG_ENABLED", True),
            vector_db_url=_env_url("VECTOR_DB_URL", DEFAULT_VECTOR_DB_URL),
            embed_model=_env_str("EMBED_MODEL", DEFAULT_EMBED_MODEL),
            embed_base_url=_env_str("EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL),
            rag_top_k=_env_int("RAG_TOP_K", 4),
            rag_max_context_chars=_env_int("RAG_MAX_CONTEXT_CHARS", 12000),
            examples_enabled=_env_bool("EXAMPLES_ENABLED", True),
            context_db_url=_env_url("CONTEXT_DB_URL", DEFAULT_CONTEXT_DB_URL),
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
            snippets_enabled=_env_bool("SNIPPETS_ENABLED", True),
            snippet_db_url=_env_url("SNIPPET_DB_URL", DEFAULT_SNIPPET_DB_URL),
            snippets_top_k=_env_int("SNIPPETS_TOP_K", 5),
            snippets_min_score=_env_float("SNIPPETS_MIN_SCORE", 0.35),
            snippets_min_similarity=_env_float("SNIPPETS_MIN_SIMILARITY", 0.62),
            snippets_max_context_chars=_env_int("SNIPPETS_MAX_CONTEXT_CHARS", 4000),
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
            model_routing_enabled=_env_bool("MODEL_ROUTING_ENABLED", True),
            model_catalog=_env_str("MODEL_CATALOG", ""),
            model_route_on_prior=_env_bool("MODEL_ROUTE_ON_PRIOR", False),
            model_route_supervisor=_env_str("MODEL_ROUTE_SUPERVISOR", ""),
            model_route_generator=_env_str("MODEL_ROUTE_GENERATOR", ""),
            model_route_reflection=_env_str("MODEL_ROUTE_REFLECTION", ""),
            model_route_narrator=_env_str("MODEL_ROUTE_NARRATOR", ""),
            model_route_repair=_env_str("MODEL_ROUTE_REPAIR", ""),
            model_max_loaded=_env_int("MODEL_MAX_LOADED", 3),
            model_num_ctx=_env_int("MODEL_NUM_CTX", DEFAULT_MODEL_NUM_CTX),
            ollama_keep_alive=_env_str("OLLAMA_KEEP_ALIVE", "30m"),
            mlflow_tracking_uri=_env_str("MLFLOW_TRACKING_URI", ""),
            mlflow_experiment_name=_env_str("MLFLOW_EXPERIMENT_NAME", DEFAULT_MLFLOW_EXPERIMENT),
            ensemble_enabled=_env_bool("ENSEMBLE_ENABLED", True),
            ensemble_paraphrases=_env_int("ENSEMBLE_PARAPHRASES", 3),
            ensemble_max_paraphrases=_env_int("ENSEMBLE_MAX_PARAPHRASES", MAX_PARAPHRASES),
            ensemble_waves=_env_int("ENSEMBLE_WAVES", 2),
            ensemble_deadline_seconds=_env_float("ENSEMBLE_DEADLINE_SECONDS", 0.0),
            ensemble_judge_enabled=_env_bool("ENSEMBLE_JUDGE_ENABLED", True),
            ensemble_max_claims=_env_int("ENSEMBLE_MAX_CLAIMS", 8),
            ensemble_fuse_columns=_env_bool("ENSEMBLE_FUSE_COLUMNS", True),
            ollama_parallel_calls=_env_int("OLLAMA_PARALLEL_CALLS", 1),
            model_route_paraphraser=_env_str("MODEL_ROUTE_PARAPHRASER", ""),
            model_route_judge=_env_str("MODEL_ROUTE_JUDGE", ""),
        )
