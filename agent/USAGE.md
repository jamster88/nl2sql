# Running the agent and asking questions

A practical guide to launching the NL2SQL agent and getting answers out of it.
For how it works internally and how to extend it, see
[`README.md`](README.md).

## Before you start

Run the setup script once from the repo root, with Docker running:

```bash
./setup.sh
```

One call sets up all four containers:

| Container | Role |
|---|---|
| `nl2sql-postgres` | the retail dataset you are querying |
| `nl2sql-vectordb` | pgvector: the knowledge base and the golden-pair vectors |
| `nl2sql-chunkdb` | the context store: the golden pairs and their BM25 index |
| `agent` | the agent itself, started per question and removed after |

It pulls each image, starts the databases, checks that the Ollama hosts have
the chat model and the embedding model, and ends by proving the agent container
can actually reach the knowledge base:

```
==> Checking the agent can reach the knowledge base
    retrieval works end to end (3 collections searched)
```

If that last check warns instead, the stack is still usable -- the agent just
answers without retrieved context until the vector database or the embedding
host is reachable.

It takes a couple of minutes -- mostly downloads -- and is safe to re-run.
Point it somewhere else with `./setup.sh --ollama-url URL --model NAME`;
`./setup.sh --help` lists every flag.

### Afterwards, use launch.sh

`setup.sh` is the first-time script. Day to day -- after a reboot, or when you
are not sure the stack is up -- use the launch script instead:

```bash
./launch.sh
docker compose run --rm agent "How many stores are there?"
```

It starts whatever is down, then checks that each database is actually
*populated* and that both models are reachable. Those are the failures that
happen later and are invisible from the outside: a container that comes up
healthy but empty, a chat host that has moved, an embedding model that is not
the one the vectors were built with. It pulls nothing, so it takes seconds.

```
==> Checking what is actually in each database
    retail dataset: 1291781 sales rows
    knowledge base: 53 embedded chunks
    worked examples: 45 golden pairs, 45 embedded questions
```

`--no-rag` starts only the retail database, `--restart` recreates the
containers, `-q` prints only problems, and running it with no `.env` hands off
to `setup.sh`.

The agent uses **two** models on **two** possibly different hosts: a chat model
(default `qwen3.8-256k` on `http://192.168.10.82:11434`) that writes the SQL,
and an embedding model (default `bge-m3` on the Ollama running on your own
machine) that searches the knowledge base. `./setup.sh` warns about either one
being unreachable. Retrieval is optional -- without it the agent still answers,
just without the business context that makes hard questions come out right.

Every command below is run from the repo root, and assumes setup has completed.

The one thing setup cannot do for you is make the Ollama host reachable. The
default is `http://192.168.10.82:11434`; check it before debugging anything
else:

```bash
curl -s http://192.168.10.82:11434/api/tags | head -c 200
```

That should print a JSON list of models. Note it is **http**, not https -- port
11434 does not terminate TLS, and an `https://` URL fails with an SSL error.

## Launching

### Ask one question and exit

```bash
docker compose run --rm agent "How many stores are there?"
```

This is the normal way to use it. Compose starts Postgres, waits for its health
check, runs your question, and removes the agent container afterward. The
database keeps running.

### Interactive session

Leave the question off to get a prompt that keeps the connection open:

```bash
docker compose run --rm agent
```

```
Connected to qwen3.8-256k at http://192.168.10.82:11434.
Ask a question, or Ctrl-D to exit.

> How many vendors are there?
```

Each question is answered independently -- the agent does not remember the
previous one, so ask complete questions rather than follow-ups like "and by
store?".

Ctrl-D exits.

### Pipe questions in

```bash
echo "How many vendors are there?" | docker compose run --rm -T agent
```

The `-T` matters: without it, compose allocates a TTY and the pipe will not be
read.

## Reading the output

Progress goes to **stderr**, the answer goes to **stdout**:

```
[screen] proceed / aggregate                              <- scope, injection, intent
[knowledge] 12 chunk(s) -- business_index:Market share fan-out ...  <- what it retrieved
[examples] Q42 (0.675), Q36 (0.500), Q16 (0.394)          <- worked examples it found
[tables] fact_pos_retail_sales, dim_product, dim_date     <- tables the vectors ranked
[literals] "dairy and eggs" -> dim_product.department_name = 'Dairy & Eggs'
[schema] fact_pos_retail_sales, dim_product, dim_date (+1 bridge: dim_date)
[sql] SELECT p.department_name, SUM(...)                  <- the query it wrote
[validation] valid                                        <- AST parse, no model call
[planner] cost 20,555.96                                  <- EXPLAIN, under the ceiling
[result] 5 row(s)
[chart] bar                                               <- chosen from the shape
[narrative] 2 claim(s)                                    <- each naming its cells
[audit] passed                                            <- every number traced back

Meat & Seafood led on net sales at 821785.92.

department_name | total_net_sales
----------------+----------------
Meat & Seafood  | 821785.92
```

The first five lines are Stage 1 and they run concurrently, so their order in
the output is whichever finished first, not a sequence.

Because the streams are separate, you can keep just the answer:

```bash
docker compose run --rm agent "How many stores are there?" 2>/dev/null
```

Or silence the progress lines entirely with `--quiet`.

### Structured output for scripts

`--json` prints the question, retrieved chunks, chosen tables, generated SQL,
and rows as one JSON object:

```bash
docker compose run --rm agent --json "Which 3 promotions had the highest total promo quantity sold?"
```

```json
{
  "question": "Which 3 promotions had the highest total promo quantity sold?",
  "knowledge_chunks": [
    {
      "chunk_id": "ddl_index:1f2a...",
      "source_doc": "ddl_index",
      "heading_path": "DDL Index > fact_promo_performance",
      "distance": 0.3417
    }
  ],
  "retrieval_error": null,
  "selected_tables": ["fact_promo_performance", "dim_promotion"],
  "sql": "SELECT\n  p.promotion_name,\n  SUM(f.promo_quantity_sold) AS ...",
  "error": null,
  "result": {
    "columns": ["promotion_name", "total_promo_quantity_sold"],
    "rows": [["Back to School Flash Sale", "203812.674"]],
    "row_count": 3,
    "truncated": false
  }
}
```

The exit code is 0 on success and 1 when the agent could not answer, so it
works in a shell pipeline. Pull out just the SQL with
`... --json | jq -r .sql`, or see which knowledge shaped an answer with
`... --json | jq -r '.knowledge_chunks[].heading_path'`.

## Asking in a browser instead

Everything above is the terminal. There is also a web interface, and one
command that brings up everything it needs and opens it:

```bash
./start.sh
```

It asks the same questions of the same pipeline, shows the agent's own
pipeline steps while it works, draws whatever chart the Visual Formatter
asked for, and takes a yes/no verdict on the answer.
[`gui/README.md`](../gui/README.md) explains how it is put together.

## Asking over the network instead

The same agent also answers over HTTPS, for a GUI of your own or anything
else that is not a shell:

```bash
./launch.sh --api
```

That starts the `api` service -- the same image, run as
`python -m nl2sql_agent.api` -- and checks it came up. It prints the URL, the
OpenAPI document's address, and warns if TLS is off or no token is set.

The server writes itself a self-signed certificate on first start, so a
client has to be told to trust it:

```bash
docker compose --profile api cp api:/etc/nl2sql/tls/server.crt ./nl2sql-api.crt

curl --cacert ./nl2sql-api.crt https://localhost:8443/v1/meta
curl --cacert ./nl2sql-api.crt -X POST 'https://localhost:8443/v1/questions?wait=180' \
     -H 'Content-Type: application/json' \
     -d '{"question": "How many stores are there?"}'
```

Or drive the whole thing from a container with nothing of this project in it:

```bash
docker compose --profile api run --rm apitest
docker compose --profile api run --rm apitest "total net sales for produce in FY2025"
```

A question takes about a minute, so `POST /v1/questions` without `?wait=`
returns a job straight away and `GET /v1/questions/{id}/events` streams the
pipeline's progress as it happens. [`API.md`](API.md) is the full contract --
every endpoint, the response shapes, the error codes, the settings, and
client snippets for TypeScript, Python and Java, with [`gui/`](../gui) as a
worked example at full size.

Two settings are worth knowing before this leaves your own machine:
`API_TOKEN` requires a bearer token on every question, and
`API_TLS_ALLOW_SELF_SIGNED=false` makes the server refuse to start unless a
real certificate has been mounted over the development one.

## The knowledge base

Retrieval is on by default. It searches the embedded contents of
[`../knowledge/`](../knowledge) -- the data dictionary, DDL index and business
index -- and hands the best-matching sections to the model along with the
schema.

It matters most on questions where the schema is misleading:

```bash
docker compose run --rm agent "What is our overall market share in fiscal year 2024?"
```

`fact_market_share_weekly` repeats each cell's totals once per competitor, so a
natural-looking `SUM()` over-counts fivefold. With retrieval the agent
de-duplicates per cell and answers ~21.5%; with `--no-rag` it has returned
107.5% for the same question.

Useful flags:

```bash
docker compose run --rm agent --no-rag "..."            # schema only, v1 behavior
docker compose run --rm agent --rag-top-k 8 "..."       # more context per collection
docker compose run --rm agent --embed-model bge-m3 "..."
docker compose run --rm agent --embed-url http://other-host:11434 "..."
```

The embedding model must be the one the knowledge base was built with (`bge-m3`)
-- a different model puts the query in a different vector space and retrieval
returns confident nonsense.

If the vector database or the embedding host is unreachable, the agent says so
and carries on without it:

```
[knowledge] skipped: Could not search the knowledge base: connection failed ...
```

## Worked examples and multi-shot

Alongside the knowledge base the agent retrieves **worked examples**: real
questions from
[`../context_questions/translated_questions.md`](../context_questions/translated_questions.md)
already answered with SQL that runs against this database. They are chosen by an
ensemble of three retrievers, reranked for relevance and variety, and then
replayed to the model as conversation turns in front of your question -- so it
sees the pattern demonstrated rather than described.

The `[examples]` progress line names what was chosen and its score:

```
[examples] 3 pair(s) -- Q01 (0.850), Q08 (0.255), Q21 (0.279)
```

Useful flags:

```bash
docker compose run --rm agent --no-multi-shot "..."      # retrieve them, but do not prompt with them
docker compose run --rm agent --no-examples "..."        # skip the retrieval entirely, v2 behavior
docker compose run --rm agent --examples-top-k 5 "..."   # more exemplar turns
docker compose run --rm agent --json "..." | jq .example_pairs
```

`--json` reports both scores per pair: `score` is where the ensemble's fusion
put it, `rerank_score` is where the second pass moved it to. A pair with a lower
fused score sitting above a higher one is the diversity term working -- three
exemplars that all demonstrate the same trick teach less than three that do not.

If the context store, the vector store or the embedding host is unreachable, the
examples are skipped and the run continues:

```
[examples] skipped: Could not read golden_pairs from the context store: ...
```

## Choosing a model or host

```bash
docker compose run --rm agent --model gemma4:12b-mlx "How many stores are there?"
docker compose run --rm agent --base-url http://other-host:11434 "..."
```

List what a host has available with
`curl -s http://192.168.10.82:11434/api/tags | jq -r '.models[].name'`.
Pick a model with **tool support** -- table selection and validation use
structured output, which needs it.

For a permanent change, set `OLLAMA_MODEL` or `OLLAMA_BASE_URL` in the
environment or in `docker-compose.yml` instead of passing flags each time.

Add `--reasoning` for hard questions. It lets the model think before answering,
which costs noticeably more time but helps on questions involving several
joins or subtle grain.

## Example questions that work

These have all been run against the shipped dataset:

| Question | What it exercises |
|---|---|
| `How many stores are there?` | single table |
| `How many vendors are there?` | single table |
| `What were the top 5 product departments by net sales in fiscal year 2024?` | 3-table join, fiscal calendar, group + order |
| `Which 3 promotions had the highest total promo quantity sold?` | fact/dimension join on promotions |
| `Which 3 stores had the highest average net sales per basket?` | subquery to basket grain, then averaging |

### Writing questions that land

The dataset is a grocery retail star schema, so questions work best when they
name three things: **a metric**, **a grain**, and optionally **a time period**.

- Good: *"top 5 departments by net sales in fiscal year 2024"* -- metric
  (net sales), grain (department), period (FY2024).
- Harder: *"how are we doing?"* -- no metric or grain, so the agent has to
  guess at both.

Useful vocabulary from this schema: net sales, gross sales, markdown, quantity
sold, promo quantity sold, ad spend, impressions, competitor price, market
share, department, category, brand, store, banner, vendor, fiscal year, fiscal
week, promo cycle.

Remember that fiscal year `Y` starts April 1 of `Y-1`, so "fiscal year 2024" is
not the 2024 calendar year. Say "calendar 2024" if that is what you mean.

## When it cannot answer

The agent validates every query before running it and retries up to three
times, feeding a specific repair hint back to the model. Every kind of failure
spends the same budget -- a parse rejection, a planner error, a runtime error,
or the audit judging the answer unsupported -- so there is no way to loop that
does not count. If it still cannot produce a valid query, it stops rather than
executing anything:

```
failed: Could not produce a valid query in 4 attempts. Last problems: ...
```

That is the expected outcome for questions the data cannot answer, and for
anything that asks to modify data -- the agent is read-only by design, so
"delete every row from dim_store" is refused rather than attempted.

Raising `--max-attempts` helps when a question is answerable but awkward.
Rephrasing with an explicit metric and grain usually helps more.

## Troubleshooting

The agent checks the Ollama host and model before doing anything else, so a bad
URL or model name fails immediately with a one-line message and exit code 2:

```
error: Cannot reach Ollama at http://127.0.0.1:1. Check that the host is
running and reachable, and note that port 11434 serves http, not https.

error: Model `no-such-model:latest` not found in Ollama. Please pull the model
(using `ollama pull no-such-model:latest`) or specify a valid model name.
Available local models: gemma4:12b-mlx, qwen3.6:latest, qwen3.8-256k,
qwen3.8:latest, qwen3-coder-next:latest
```

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot reach Ollama at ...` | host unreachable from the container, or `https://` was used | Check the URL and that Ollama is listening; confirm with the `curl` above |
| `Model ... not found in Ollama` | model not pulled on that host | `ollama pull <model>` on the host, or pick one from the list in the message |
| `does not provide tool support` or malformed table picks | model lacks tool/structured-output support | Choose a model whose `capabilities` include `tools` |
| Answers reference columns that do not exist | schema context truncated | Raise `OLLAMA_NUM_CTX` (default 262144) |
| Query times out | statement exceeded 30s | Narrow the question, or raise `STATEMENT_TIMEOUT_MS` |
| Results cut off with `... truncated at 50 rows` | row cap | Raise `--max-rows` |

Exit codes: `0` answered, `1` could not answer, `2` Ollama misconfigured.

**`setup.sh` warned that the agent could not retrieve** -- the same causes as
the next entry; setup still leaves a working stack, so fix the cause and re-run
`./setup.sh` (or just retry a question) to confirm.

**`[knowledge] skipped: ...`** -- the agent could not reach the vector database
or the embedding model, and answered from the schema alone. Check that
`nl2sql-vectordb` is up (`docker compose ps`) and that your local Ollama has
`bge-m3` (`ollama list`). Pull it with `ollama pull bge-m3`.

**Retrieval returns irrelevant chunks** -- usually an embedding-model mismatch.
The store records what it was built with:

```bash
docker compose exec vectordb psql -U ragproc -d nl2sql_vectors \
  -c "SELECT DISTINCT embedding_model FROM ddl_index_embeddings"
```

That has to match `--embed-model`.

## Stopping

The agent container removes itself after each question. The database keeps
running until you stop it:

```bash
docker compose stop postgres vectordb   # keeps data
docker compose down                     # removes the containers, keeps data
docker compose down -v                  # also deletes the volumes, resetting both
                                        # databases to what the images ship
```
