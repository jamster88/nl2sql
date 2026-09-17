# NL2SQL Agent (v2, RAG)

A natural-language-to-SQL agent built with LangChain and LangGraph. It talks to
any model served by Ollama and queries the Postgres container from
[`docker-compose.yml`](../docker-compose.yml).

**v2 adds retrieval.** Before it picks tables or writes SQL, the agent embeds
the question and searches the pgvector knowledge base built from
[`knowledge/`](../knowledge) -- the data dictionary, the DDL index, and the
business index. The chunks that come back carry the things a schema alone
cannot tell a model: fiscal-calendar semantics, which columns are
pre-aggregated, and which joins fan out. That context is what lets it answer
questions v1 got confidently wrong (see [Why retrieval](#why-retrieval)).

For launching it and asking questions day to day, see [`USAGE.md`](USAGE.md).
This file covers how it works and how to extend it.

## Quick start

Run [`../setup.sh`](../setup.sh) once from the repo root. It pulls all three
images, starts the retail database and the pgvector knowledge base, and
verifies the agent container can retrieve from it. After that:

```bash
docker compose run --rm agent "What were the top 5 departments by net sales in fiscal year 2024?"
docker compose run --rm agent --json "Which 3 promotions had the highest promo quantity sold?"
docker compose run --rm agent            # interactive; Ctrl-D to exit
```

`docker compose run` starts both databases first and waits for each to pass its
health check. The agent is behind a compose profile, so a plain
`docker compose up` starts the two databases and not the agent.

Output goes to two streams: progress lines on stderr, the result table on
stdout, so `... > answer.txt` captures just the answer.

## The pipeline

The five steps in [`basic_agent_steps.md`](../basic_agent_steps.md) map onto one
LangGraph node each, defined in [`nl2sql_agent/graph.py`](nl2sql_agent/graph.py):

```
retrieve_knowledge -> select_tables -> fetch_schema -> generate_sql -> validate_sql -> execute_query
                                                            ^               |
                                                            +---- retry ----+
                                                                            |
                                                          attempts exhausted +-> give_up
```

| Step | Node | Tool | LLM |
|---|---|---|---|
| 1. Retrieve context | `retrieve_knowledge` | `search_knowledge` | -- |
| 2. Find relevant tables | `select_tables` | `describe_all_tables` | picks tables |
| 3. Get schema + samples | `fetch_schema` / `generate_sql` | `get_schema_and_data` | writes SQL |
| 4. Validate | `validate_sql` | `validate_sql` | reviews dialect/semantics |
| 5. Execute | `execute_query` | `execute_query` | -- |

Validation failures loop back to `generate_sql` with the specific problems
appended to the prompt, up to `--max-attempts` (default 3). When the attempts
run out the graph routes to `give_up`, which records the last set of problems
and ends the run -- nothing is executed.

The validator runs `EXPLAIN` before consulting the model. A planner error
(unknown column, type mismatch) is definitive and skips the LLM call, so the
common failure mode costs nothing.

Table and column descriptions come from Postgres `COMMENT ON` metadata. The
current schema has no comments, so the agent works from names alone -- adding
comments to [`ddl.sql`](../data_gen/ddl.sql) feeds straight into both the
catalog and schema prompts with no code change.

## Configuration

Every setting is an environment variable with a CLI override:

| Variable | Flag | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | `--base-url` | `http://192.168.44.129:11434` |
| `OLLAMA_MODEL` | `--model` | `qwen3.8:latest` |
| `OLLAMA_REASONING` | `--reasoning` / `--no-reasoning` | off |
| `OLLAMA_TEMPERATURE` | -- | 0.0 |
| `OLLAMA_NUM_CTX` | -- | 16384 |
| `DATABASE_URL` | `--database-url` | the compose Postgres |
| `DB_SCHEMA` | -- | `public` |
| `MAX_ROWS` | `--max-rows` | 50 |
| `MAX_SQL_ATTEMPTS` | `--max-attempts` | 3 |
| `SAMPLE_ROWS` | `--sample-rows` | 3 |
| `STATEMENT_TIMEOUT_MS` | -- | 30000 |
| `RAG_ENABLED` | `--rag` / `--no-rag` | on |
| `VECTOR_DB_URL` | `--vector-db-url` | the compose pgvector |
| `EMBED_MODEL` | `--embed-model` | `bge-m3` |
| `EMBED_BASE_URL` | `--embed-url` | `http://host.docker.internal:11434` |
| `RAG_TOP_K` | `--rag-top-k` | 4 per collection |
| `RAG_MAX_CONTEXT_CHARS` | -- | 12000 |

Any Ollama model and host works:

```bash
docker compose run --rm agent --model gemma4:12b-mlx "How many stores are there?"
OLLAMA_BASE_URL=http://other-host:11434 docker compose run --rm agent "..."
```

Two notes on the defaults:

- The host is reached over **http**, not https -- port 11434 there does not
  terminate TLS, and an `https://` URL fails with an SSL error.
- `num_ctx` is set explicitly because Ollama otherwise caps context at a few
  thousand tokens regardless of the model's real limit, which silently truncates
  the schema prompt.

Reasoning is off by default. Qwen3 returns reasoning in a separate field, so
disabling it costs no output quality and saves a large share of the latency;
turn it on with `--reasoning` for harder questions.

## Safety

Generated SQL is untrusted, so execution has two independent layers:

1. **Static check** (`ensure_read_only`) -- must be a single statement starting
   with `SELECT` or `WITH`.
2. **`READ ONLY` transaction** with a statement timeout -- this is what stops a
   data-modifying CTE such as `WITH d AS (DELETE ... RETURNING *) SELECT * FROM d`,
   which is a legitimate `WITH` query as far as layer 1 is concerned.

Results are capped at `--max-rows`, and the flag reports when output was
truncated rather than silently cutting it off.

## Retrieval

The knowledge base is a pgvector database with one collection per document in
[`knowledge/`](../knowledge), each row a semantically chunked section with its
bge-m3 embedding, the heading path it came from, and the authored `chunk_meta`
(`table`, `domain`, `grain`, `keywords`).

`retrieval.py` embeds the question once and runs a cosine search against every
collection, so one question picks up DDL detail, dictionary context and
business rules together. Collections are **discovered from the catalog**, not
hardcoded: adding a document to the RAG pipeline makes it searchable with no
code change here.

What the retrieved chunks feed:

| Consumer | What it gets |
|---|---|
| `select_tables` | the context, plus `chunk_meta.table` names merged into the model's own choice |
| `generate_sql` | the context, marked authoritative over the model's assumptions |
| `validate_sql` | the context, so a rule violation is a reported problem, not a pass |

`--json` includes the `chunk_id`, source document and distance of every chunk
used, so an answer can be traced back to the text that shaped it.

### Why retrieval

Asked *"What is our overall market share across all tracked products in fiscal
year 2024?"*, the two versions diverge:

| | Answer | What it did |
|---|---|---|
| v1 (`--no-rag`) | **107.5%** | Summed `grocer_sales_amount` across the five competitor rows per cell, inflating the numerator |
| v2 | **21.5%** | Retrieved *"Market share fan-out: the five-row trap"* and de-duplicated per (week, product, region) cell on the first attempt |

`fact_market_share_weekly` repeats the same `grocer_sales_amount` and
`total_market_sales_amount` once per competitor. Nothing in the schema says so;
the business index does.

### Degradation

Retrieval is best-effort. If the vector store or the embedding model is
unreachable, the node records why, the prompts drop the knowledge block
entirely, and the run continues schema-only -- exactly v1 behavior. A missing
knowledge base slows the agent down; it never fails a question that v1 could
have answered.

```
[knowledge] skipped: Could not search the knowledge base: connection failed ...
[tables] dim_store
```

### The embedding host is not the chat host

Queries must be embedded with the same model the store was built with, or the
vectors land in different spaces and retrieval returns confident noise. `bge-m3`
typically runs on the machine hosting Docker, while the chat model runs on a
remote Ollama -- hence separate `EMBED_BASE_URL` and `OLLAMA_BASE_URL`. The
live test `test_store_was_built_with_the_model_we_query_with` checks the store's
recorded `embedding_model` against the configured one.

## Adding steps later

Each step is a node with plain-dict state, so a new step is a node plus an edge:
add a field to `AgentState` in `graph.py`, add a node that populates it, point
the edges at it, and include the field in the relevant prompt. The retry loop,
validation, and execution are independent of how the prompt was assembled.
Tools are constructed by `build_tools()` in `tools.py` and are ordinary
LangChain tools, so they can also be bound to a tool-calling model if you later
want the model to choose its own sequence rather than following a fixed graph.

## Running outside Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
DATABASE_URL="postgresql+psycopg://nl2sql:nl2sql@localhost:5432/nl2sql_retail" \
VECTOR_DB_URL="postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors" \
EMBED_BASE_URL="http://localhost:11434" \
  python -m nl2sql_agent "How many stores are there?"
```

Run it from the `agent/` directory so `nl2sql_agent` is importable. Note that
the host URLs use `localhost` rather than the `postgres` / `vectordb` hostnames
that only resolve inside the compose network, and `localhost` rather than
`host.docker.internal` for the embedding host.
