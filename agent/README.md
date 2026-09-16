# NL2SQL Agent

A natural-language-to-SQL agent built with LangChain and LangGraph. It talks to
any model served by Ollama and queries the Postgres container from
[`docker-compose.yml`](../docker-compose.yml).

For launching it and asking questions day to day, see [`USAGE.md`](USAGE.md).
This file covers how it works and how to extend it.

## Quick start

```bash
docker compose run --rm agent "What were the top 5 departments by net sales in fiscal year 2024?"
docker compose run --rm agent --json "Which 3 promotions had the highest promo quantity sold?"
docker compose run --rm agent            # interactive; Ctrl-D to exit
```

`docker compose run` starts the database first and waits for it to pass its
health check. The agent is behind a compose profile, so a plain
`docker compose up` still starts only Postgres.

Output goes to two streams: progress lines on stderr, the result table on
stdout, so `... > answer.txt` captures just the answer.

## The pipeline

The five steps in [`basic_agent_steps.md`](../basic_agent_steps.md) map onto one
LangGraph node each, defined in [`nl2sql_agent/graph.py`](nl2sql_agent/graph.py):

```
select_tables -> fetch_schema -> generate_sql -> validate_sql -> execute_query
                                      ^               |
                                      +---- retry ----+
```

| Step | Node | Tool | LLM |
|---|---|---|---|
| 2. Find relevant tables | `select_tables` | `describe_all_tables` | picks tables |
| 3. Get schema + samples | `fetch_schema` / `generate_sql` | `get_schema_and_data` | writes SQL |
| 4. Validate | `validate_sql` | `validate_sql` | reviews dialect/semantics |
| 5. Execute | `execute_query` | `execute_query` | -- |

Validation failures loop back to `generate_sql` with the specific problems
appended to the prompt, up to `--max-attempts` (default 3). If the attempts run
out, the agent reports the failure instead of executing anything.

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
| `OLLAMA_NUM_CTX` | -- | 16384 |
| `DATABASE_URL` | `--database-url` | the compose Postgres |
| `MAX_ROWS` | `--max-rows` | 50 |
| `MAX_SQL_ATTEMPTS` | `--max-attempts` | 3 |
| `SAMPLE_ROWS` | `--sample-rows` | 3 |
| `STATEMENT_TIMEOUT_MS` | -- | 30000 |

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

## Adding steps later (RAG and friends)

Each step is a node with plain-dict state, so a new step is a node plus an edge.
To add retrieval that enriches the SQL-generation prompt:

1. Add a field to `AgentState` in `graph.py` (e.g. `retrieved: str`).
2. Add a node that populates it.
3. Point the edge at it: `fetch_schema -> retrieve_context -> generate_sql`.
4. Include the field in `SQL_GENERATION_PROMPT` in `prompts.py`.

Nothing else needs to change -- the retry loop, validation, and execution are
independent of how the prompt was assembled. Tools are constructed by
`build_tools()` in `tools.py` and are ordinary LangChain tools, so they can also
be bound to a tool-calling model if you later want the model to choose its own
sequence rather than following a fixed graph.

## Running outside Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
DATABASE_URL="postgresql+psycopg://nl2sql:nl2sql@localhost:5432/nl2sql_retail" \
  python -m nl2sql_agent "How many stores are there?"
```

Run it from the `agent/` directory so `nl2sql_agent` is importable. Note the
host database URL uses `localhost`, not the `postgres` hostname that only
resolves inside the compose network.
