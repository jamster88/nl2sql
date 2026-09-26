# NL2SQL Agent (v4, multi-agent)

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

**v4.1 adds a REST interface.** The same image also runs as an HTTPS server
(`python -m nl2sql_agent.api`) so a GUI -- in any language, with no client
library from here -- can ask questions and watch the pipeline work. The
contract is [`API.md`](API.md); how it is built is
[Serving it over HTTP](#serving-it-over-http) below.

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

Or over the network, for something with a screen:

```bash
../launch.sh --api
curl --cacert ./nl2sql-api.crt https://localhost:8443/v1/meta
```

## The pipeline

Four stages, one shared state object, one retry loop, defined in
[`nl2sql_agent/graph.py`](nl2sql_agent/graph.py) and drawn in full by
[`arch_diagrams/arch_v4.svg`](../arch_diagrams/arch_v4.svg), with
[`arch_v3.svg`](../arch_diagrams/arch_v3.svg),
[`arch_v2.svg`](../arch_diagrams/arch_v2.svg) and
[`arch_v1.svg`](../arch_diagrams/arch_v1.svg) alongside it for the earlier
versions. The design and the reasoning behind each departure from it are in
[`Multi-Agent_NL2SQL_arch4.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch4.md).

```
supervise --+-- retrieve_schema ----+
            |-- retrieve_literals --|
            |-- retrieve_knowledge -+--> aggregate --> generate_sql
            +-- retrieve_examples --+                       |
            |                                               v
            +--> refuse                              validate_static
                                                            | pass
   give_up <-- repair <------------------------------+      v
                 ^   |                               |  planner_gate --> execute_query
                 |   +-- attempts < max --> generate_sql                      |
                 |                                                            v
                 +---------- semantic_issue -- audit <-- narrate <-- visualise
```

| Stage | Node | What it does | LLM |
|---|---|---|---|
| 1. Intake | `supervise` | Screens for prompt injection and out-of-scope questions; classifies intent | screens |
| 1. Intake | `refuse` | Answers a refused or ambiguous question without touching the database | -- |
| 1. Context | `retrieve_schema` | Tables from the DDL-chunk vectors | -- |
| 1. Context | `retrieve_literals` | Phrases in the question resolved to real values | -- |
| 1. Context | `retrieve_knowledge` | Business rules and data-dictionary chunks | -- |
| 1. Context | `retrieve_examples` | The three-retriever golden-pair ensemble | -- |
| 1. Join | `aggregate` | One table set: deduplicated, foreign-key closed, capped, described | -- |
| 2. Synthesis | `generate_sql` | The only place SQL is written, on the draft and every repair | writes SQL |
| 3. Gate | `validate_static` | `pglast` AST: one statement, SELECT only, no writing CTE, tables in scope | -- |
| 3. Gate | `planner_gate` | `EXPLAIN (FORMAT JSON)` in a READ ONLY transaction; cost ceiling | -- |
| 3. Execute | `execute_query` | Reader role, READ ONLY, statement timeout, row cap | -- |
| 3. Repair | `repair` | Classifies the failure into a hint; asks the model only when it cannot | rarely |
| 3. Repair | `give_up` | Returns the last SQL and every attempt that was made | -- |
| 4. Present | `visualise` | Chart choice from the result's shape, as a lookup | -- |
| 4. Present | `narrate` | Structured claims, each pointing at the cells it came from | narrates |
| 4. Present | `audit` | Verifies every number against those cells | -- |
| 4. Present | `finish` | Renders the markdown answer | -- |

The four Stage 1 retrievers are branches of one LangGraph superstep, so they
run concurrently and `aggregate` is the fan-in. Each is best-effort: one that
cannot reach its store records why in `retrieval_errors` and the run continues
without it. With all of them down the pipeline degrades to schema-only, which
is exactly what v1 was.

**Three model calls on the happy path**: `supervise`, `generate_sql`,
`narrate`. v3 also made three, but two of them were table selection and
validation review, which cost 364 and 499 of 1499 benchmark seconds and
neither of which wrote the answer. Validation is now an AST parse and a
planner call, both deterministic and both measured in milliseconds.

**One retry budget.** A failure from any gate -- the AST check, the planner, a
runtime error, or the audit -- becomes an `Issue`, routes to `repair`, and
spends the same `attempts` counter. There is no path that loops without being
counted, and `MAX_ATTEMPTS` (default 4) is one draft and three repairs.

The Repair Agent does not write SQL. It turns a failure into a hint and hands
it back to the generator, which keeps a single component responsible for the
query. It classifies deterministically first and calls the model only for
errors it does not recognise, so the common failures cost no model call at all.

Table and column descriptions come from Postgres `COMMENT ON` metadata. The
current schema has no comments, so the agent works from names alone -- adding
comments to [`ddl.sql`](../data_gen/ddl.sql) feeds straight into the schema
prompt with no code change.

### Tools

The nodes reach the database and the retrieval stores through LangChain tools
built by `build_tools()` in [`nl2sql_agent/tools.py`](nl2sql_agent/tools.py),
so the same functions can later be bound to a tool-calling model rather than
invoked at fixed points:

| Tool | Used by | What it reads |
|---|---|---|
| `search_knowledge` | `retrieve_knowledge` | The business-rule and data-dictionary collections |
| `search_examples` | `retrieve_examples` | The golden pairs, through the three-retriever ensemble |
| `get_schema_and_data` | `aggregate` | Columns, types, keys, comments and sample rows |
| `describe_all_tables` | `retrieve_schema`, only under `SCHEMA_RETRIEVAL=llm` | The whole catalog, for v3's table-selection call |
| `execute_query` | -- | Retained for callers outside the graph; the graph executes through `Database` directly so it can pass a principal |

### Measured

The 15-question benchmark, same questions and same model as v3:

| | v3 | v4 |
|---|---|---|
| Execution accuracy | 15/15 | 15/15 |
| Total | 1499s | 909.7s |
| Median per question | 100.5s | 61.2s |
| Model calls per question | 3 | 3.7 |
| Narrative traced to cells | not measured | 84.6% |

The two model calls v4 removes are what that difference is made of:

```
v3  validate_sql    499.4s      v4  validate_static + planner_gate   0.1s
v3  select_tables   363.9s      v4  retrieve_schema                  1.2s
```

Accuracy holds because neither call was writing the answer. The Supervisor is
cheap, 45.8s across 15 questions. The Narrator is not: 217.3s, a quarter of the
run, which the architecture did not anticipate. It buys a narrative whose every
number has been checked against a cell, and `NARRATE_ENABLED=false` turns it off
for a run that only wants rows.

Model calls come to 3.7 per question rather than the 3 the architecture
predicts, and the gap is all retries: 18 generations for 15 questions, and 22
narrations because the audit sends a claim back when it cannot reproduce one.
Not one of the repairs cost a model call -- the classifier recognised every
failure -- which is the part of the design that was most at risk of being
merely asserted.

Treat the totals as one run. Wall time against a shared Ollama host varied by
about 30% across three runs of the same 15 questions; what does not vary is the
shape, and the shape is that generation is most of the time and the gates are
none of it.

Re-measure with `python benchmarks/run_benchmark.py`, which now reports
per-agent timing, model calls per node, and the fraction of the narrative the
audit could trace back to the result.

## Serving it over HTTP

[`nl2sql_agent/api/`](nl2sql_agent/api) is the REST interface. It is a
separate package from the pipeline, imported only when the server runs, so
`python -m nl2sql_agent` costs nothing for it. The wire contract, the
endpoints and every setting are in [`API.md`](API.md); this is how it is put
together and why.

| Module | What it owns |
|---|---|
| [`settings.py`](nl2sql_agent/api/settings.py) | Everything about the socket, none of it about the pipeline. Same empty-is-unset discipline as `config.py`, for the same compose reason |
| [`tls.py`](nl2sql_agent/api/tls.py) | Generates the development certificate, reads one off disk, and enforces `API_TLS_ALLOW_SELF_SIGNED` |
| [`models.py`](nl2sql_agent/api/models.py) | The published request and response shapes -- what becomes `/openapi.json` |
| [`jobs.py`](nl2sql_agent/api/jobs.py) | Questions in flight: a bounded thread pool, numbered progress events, and the resumable stream over them |
| [`translate.py`](nl2sql_agent/api/translate.py) | The seam between `state.py` and `models.py`, so a field renamed inside the graph breaks one file |
| [`app.py`](nl2sql_agent/api/app.py) | The routes, the authentication, the error shape, CORS |
| [`server.py`](nl2sql_agent/api/server.py) | Flags, the certificate decision, the startup banner, uvicorn |

Four decisions worth knowing about:

**A question is a resource.** Answering takes about a minute, so `POST
/v1/questions` returns a job and the client polls, streams, or asks the
server to hold the connection. All three are the same document at the same
URL, which is what keeps the simple client simple without giving the patient
one a different contract to implement.

**Progress is the graph.** `Nl2SqlAgent.run` takes an `on_progress` callback
per run, carried in a `ContextVar` rather than set on the instance -- one
agent now answers several questions at once, and an instance attribute would
put one caller's progress on another caller's stream. LangGraph copies the
context into the threads it fans stage 1 across, so the four concurrent
retrievers report to the right run too.

**TLS is the default and the development certificate is removable.** The
container writes itself a self-signed certificate on first start because
there is no way to hand it a real one from `docker compose up`.
`API_TLS_ALLOW_SELF_SIGNED=false` refuses to start behind one at all --
neither generating nor loading -- so the convenience cannot quietly become
the deployment.

**The translation layer is separate on purpose.** `state.py` is internal and
changes with the architecture; `models.py` is what other people's code is
compiled against. `translate.py` is the only module that knows both.

Tested in [`tests/api/`](../tests/api) without Docker. Two of those tests run
what would otherwise need a container:
[`test_live_tls.py`](../tests/api/test_live_tls.py) binds a real socket with
the real certificate and talks to it with the standard library, and
[`test_smoke_script.py`](../tests/api/test_smoke_script.py) runs
[`docker/apitest/smoke.sh`](../docker/apitest/smoke.sh) -- bash, curl and jq
-- against a live server, which is how a shell script with no Python in it
gets its branches covered.
[`tests/docker/test_api_container.py`](../tests/docker/test_api_container.py)
then does it again against the packaged container, reached by a curl-only
image over verified TLS.

The other clients of this package are [`gui/`](../gui), a React and
TypeScript front end, and [`desktop/`](../desktop), a JavaFX one, and neither
imports anything from it -- every endpoint, the event stream with its resume
and its fallback to polling, and the whole answer rendered including the
charts. They are the two worked examples of the contract in
[`API.md`](API.md), in two languages, and a useful thing to read before
writing a client of your own.

## Configuration

Every setting is an environment variable, most with a CLI override. Every one
of them is also forwarded by `docker-compose.yml`, so a setting works the same
way whether the agent runs in the container or on the host:

```bash
MAX_TABLES=6 docker compose run --rm agent "how many stores are there?"
```

A variable the host has not set arrives as an empty string, which is read as
unset rather than as a blank value -- otherwise forwarding a setting would
override its own default with nothing. A test checks the correspondence in
both directions: nothing is forwarded that the agent never reads, and nothing
the agent reads is missing from compose.

Every setting is an environment variable with a CLI override:

| Variable | Flag | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | `--base-url` | `http://192.168.10.82:11434` |
| `OLLAMA_MODEL` | `--model` | `qwen3.8-256k` |
| `OLLAMA_REASONING` | `--reasoning` / `--no-reasoning` | off |
| `OLLAMA_TEMPERATURE` | -- | 0.0 |
| `OLLAMA_NUM_CTX` | -- | 262144 (256k) |
| `OLLAMA_CONNECT_TIMEOUT` | -- | 5.0 seconds to decide the host is not there. Not a limit on answering |
| `DATABASE_URL` | `--database-url` | the compose Postgres, as the read-only `nl2sql_reader` role |
| `DB_SCHEMA` | -- | `public` |
| `MAX_ROWS` | `--max-rows` | 50 |
| `MAX_ATTEMPTS` | `--max-attempts` | 4 generations: one draft, three repairs |
| `SAMPLE_ROWS` | `--sample-rows` | 3 |
| `STATEMENT_TIMEOUT_MS` | -- | 30000 |
| `RAG_ENABLED` | `--rag` / `--no-rag` | on |
| `VECTOR_DB_URL` | `--vector-db-url` | the compose pgvector |
| `EMBED_MODEL` | `--embed-model` | `bge-m3` |
| `EMBED_BASE_URL` | `--embed-url` | `http://host.docker.internal:11434` |
| `RAG_TOP_K` | `--rag-top-k` | 4 per collection |
| `RAG_MAX_CONTEXT_CHARS` | -- | 12000 |
| `EXAMPLES_ENABLED` | `--examples` / `--no-examples` | on |
| `MULTI_SHOT_ENABLED` | `--multi-shot` / `--no-multi-shot` | on |
| `CONTEXT_DB_URL` | `--context-db-url` | the compose chunkdb |
| `EXAMPLES_TOP_K` | `--examples-top-k` | 3 |
| `EXAMPLES_CANDIDATE_K` | -- | 10 per retriever |
| `EXAMPLE_WEIGHT_QUESTION` | -- | 0.50 |
| `EXAMPLE_WEIGHT_KEYWORDS` | -- | 0.35 |
| `EXAMPLE_WEIGHT_REASONING` | -- | 0.15 |
| `EXAMPLES_FUSION` | -- | `score` (or `rrf`) |
| `EXAMPLES_RRF_K` | -- | 60, used only by `rrf` |
| `EXAMPLES_RERANK` | -- | `mmr` (or `relevance`, `none`) |
| `EXAMPLES_RERANK_K` | -- | 8 candidates into the rerank |
| `EXAMPLES_RERANK_LAMBDA` | -- | 0.5 |
| `EXAMPLES_GROUNDING_WEIGHT` | -- | 0.25 |
| `EXAMPLES_MAX_CONTEXT_CHARS` | -- | 8000 |

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

## Worked examples (v3)

Retrieval in v2 fetches **prose**: business rules, table docs, grain warnings.
v3 adds a second, independent step that fetches **worked examples** -- the 45
question/SQL pairs in
[`context_questions/translated_questions.md`](../context_questions/translated_questions.md),
each one verified to run against this database and return rows.

The distinction is the point. The knowledge base can tell the model that costs
are monthly and sales are daily; the model can read that and still emit the join
that matches 24 days a year. A pair that has already reconciled the two grains
carries the shape of the answer, not just the warning.

### The ensemble

Three retrievers over the same 45 pairs, each answering a different question
about a pair, fused into one ranking:

| Retriever | Searches | Where | Weight |
|---|---|---|---|
| Question similarity | `golden_pair_question_vectors` | vectordb (pgvector, cosine) | **0.50** |
| Keyword match | `golden_pairs.keywords` | chunkdb (BM25) | **0.35** |
| Reasoning similarity | `golden_pair_reasoning_vectors` | vectordb (pgvector, cosine) | **0.15** |

Question similarity matches what the user is asking for. BM25 catches the
vocabulary a paraphrase preserves but an embedding blurs -- `slotting`,
`fan-out`, `BOGO`. Reasoning similarity matches what the query has to get
*right* rather than what it asks, so a question that never says "fan-out" can
still reach the pair that warns about it; it is weighted low because on its own
it finds the right pair only 13% of the time.

BM25 is computed **in the database**, not in the client. Postgres ships
`ts_rank`, which is a length-normalised tf-idf and not BM25, so the term
statistics are materialised at load time and the scoring function is written
out as SQL -- see
[`golden_pairs_bm25()`](../rag/ragproc/golden_pairs.py). Over 45 short keyword
lists this costs nothing to maintain and keeps the ranking next to the data.

### The rerank

Fusing three rankings is not the same as reranking them. Every score the fusion
combines was produced *independently* -- the query against the pair's question,
against its reasoning target, against its keywords -- and no stage of that ever
looks at the query and the whole pair together, or at the retrieved set as a
set. So the top `EXAMPLES_RERANK_K` fused candidates get a second pass, in
[`rerank.py`](nl2sql_agent/rerank.py):

**Grounding** scores the query against the pair's `tables` and `sql_code`, which
*no* first-stage retriever indexes -- BM25 searches only the `keywords` column.
A question naming "Produce" should favour the pair whose SQL says
`department_name = 'Produce'`, and a quoted literal counts double an identifier
word, because `'Produce'` in a WHERE clause says what a pair is about while the
word `sales` inside a column name says almost nothing.

**MMR** then trades a little relevance for coverage. This is what multi-shot
needs: three near-identical exemplars teach one pattern three times.

Both were measured rather than assumed:

| | self-retrieval @1 | paraphrase @3 | entity @1 | names-a-table @3 | redundancy |
|---|---|---|---|---|---|
| fusion order | 45/45 | 12/15 | 10/10 | 5/8 | 0.560 |
| + grounding | 45/45 | 12/15 | 10/10 | **6/8** | 0.551 |
| + MMR (λ=0.5) | 45/45 | 12/15 | 10/10 | 6/8 | **0.514** |

*Redundancy* is the mean pairwise cosine between the three chosen pairs -- lower
means the model sees three different shapes of answer. *names-a-table* is the
query class grounding exists for; on the other three the first stage is already
saturated and grounding correctly changes nothing.

Recall never degrades, at any λ from 1.0 down to 0.3, and that is not luck:
MMR's first pick has nothing to be redundant with, so rank 1 is untouched by the
diversity term by construction. That is what makes the coverage free.

### How the three are fused

Each retriever's scores are min-max normalised across its own candidates, then
weighted and summed. Normalising first is what makes the weights mean anything:
a cosine similarity sits in a narrow band near 0.5 and a BM25 score is unbounded
and corpus-dependent, so weighting the raw numbers would weight incomparable
units.

Weighted reciprocal rank fusion -- what LangChain's `EnsembleRetriever` does --
is implemented too (`EXAMPLES_FUSION=rrf`) and was measured rather than assumed.
It is worse here. RRF scores a hit as `w / (60 + rank)`, a formula tuned for
candidate lists thousands of documents long; across 10 candidates rank 1 and
rank 10 differ by only 15%, which is less than the 0.15 a third retriever
contributes just by voting at all. The weights stop expressing how *strongly* a
retriever matched and start counting how *many* did.

Measured over all 45 questions retrieving their own pair at rank 1:

| Fusion | Correct |
|---|---|
| weighted score (default) | **45/45** |
| weighted RRF, k=1 | 43/45 |
| weighted RRF, k=10 | 37/45 |
| weighted RRF, k=60 (the usual default) | 25/45 |

On 15 hand-written paraphrases that deliberately avoid each pair's own wording,
the ensemble matches the best single retriever rather than beating it (10/15 at
rank 1, against 10/15 for question similarity alone and 6/15 for BM25 alone).
What the ensemble buys there is robustness, not a uniform lift: it is never much
worse than the best leg, and the keyword leg rescues the keyword-heavy questions
where embeddings drift.

### Multi-shot generation

The retrieved pairs are **not** pasted into the prompt as a block of text. They
are replayed as real conversation turns in front of the actual question:

```
system     rules, then the schema and sample data
human      Rule that applies here: <pair 1 reasoning target>
           Question: <pair 1 question>
ai         <pair 1 SQL>
human      Rule that applies here: <pair 2 reasoning target>
           Question: <pair 2 question>
ai         <pair 2 SQL>
...
human      <knowledge block>
           Question: <the real question>
```

That shape is what instruct models are tuned on. A text block invites the model
to *describe* the examples; a turn sequence invites it to *continue the pattern*.

Three properties hold it together:

- **The turns are symmetric with the real one.** Every human turn is [the rule
  that applies] + [the question]. For an exemplar the rule is its
  `reasoning_target`; for the real question it is whatever the knowledge base
  returned. An exemplar shaped differently from the real task demonstrates the
  wrong task.
- **Assistant turns are bare SQL.** Anything they contain gets imitated -- which
  is also why they carry no leading SQL comment: `ensure_read_only` requires the
  statement to begin with SELECT or WITH, so a model that learned to prefix a
  comment would have every query rejected.
- **The schema lives in the system turn.** It is shared by every turn, so
  repeating it per exemplar would cost tokens and say nothing new.

The system turn also states what the earlier turns *are*, and that the question
to answer is the last one. Without that a model can read the exemplars as prior
user requests still waiting to be answered.

### Two switches, not one

`EXAMPLES_ENABLED` controls retrieval; `MULTI_SHOT_ENABLED` controls whether the
pairs are replayed as turns. Both default on, but they stay separate: with the
second off the examples are still fetched, ranked and visible in `--json`, so
the ranking can be inspected without it steering generation. Off, the prompt is
byte-for-byte the zero-shot one -- a system turn, then the question.

```bash
docker compose run --rm agent --json "gross margin for produce" | jq .example_pairs
docker compose run --rm agent --no-multi-shot "gross margin for produce"
```

### Degradation

Three separate things can be down -- the context store, the vector store, and
the embedding host -- and all three surface as `ExamplesUnavailableError`, which
`search_examples` turns into an empty context plus a reason on
`state.examples_error`. A run without examples is a v2 run; a run without either
retrieval is a v1 run.


## Safety

v3 reviewed generated SQL with a model call. The benchmark showed that cost
499 of 1499 seconds and that it was the only component in 45 runs ever to
turn a correct answer into no answer, so v4 removed it. What it was reaching
for now happens twice, in better places: the structural half deterministically
before execution, and the semantic half after it, where there are real rows to
check against.

Generated SQL is untrusted, so execution has three independent layers:

1. **Static check** (`ensure_read_only`) -- must be a single statement starting
   with `SELECT` or `WITH`.
2. **`READ ONLY` transaction** with a statement timeout -- this is what stops a
   data-modifying CTE such as `WITH d AS (DELETE ... RETURNING *) SELECT * FROM d`,
   which is a legitimate `WITH` query as far as layer 1 is concerned.
3. **A read-only database role** -- the agent connects as `nl2sql_reader`, which
   holds `SELECT` on the tables and nothing else (see
   [`docker/reader_role.sql`](../docker/reader_role.sql)). The owner that loads
   the data is never in the agent's `DATABASE_URL`, so a write that somehow got
   past the first two layers is refused by Postgres itself with
   `permission denied`.

Layers 1 and 2 are tested in `tests/agent/test_database_safety.py` and
`tests/agent/test_database_live.py`; layer 3 in
`tests/agent/test_least_privilege_live.py`, which asks the live catalog what
the role holds and then tries every write path anyway. The last two need a
started stack and `pytest --run-docker`.

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


The v4 pipeline adds these. Each one names a stage the architecture makes
optional, so an ablation is an environment change rather than a code change:

| Variable | Flag | Default |
|---|---|---|
| `SUPERVISOR_ENABLED` | -- | on |
| `CLARIFY_ENABLED` | -- | off (batch and benchmark runs have nobody to answer) |
| `SCHEMA_RETRIEVAL` | -- | `vector` (or `llm` for v3's table-selection call) |
| `SCHEMA_TOP_K` | -- | 6 tables from the DDL vectors, before FK closure |
| `MAX_TABLES` | -- | 10 after closure |
| `LITERALS_ENABLED` | -- | on |
| `LITERAL_MAX_DISTINCT` | -- | 500 distinct values per catalogued column |
| `LITERAL_MIN_SCORE` | -- | 0.6 |
| `MAX_PLAN_COST` | -- | 1000000 |
| `NARRATE_ENABLED` | -- | on |
| `AUDIT_ENABLED` | -- | on |
| `MAX_SQL_ATTEMPTS` | -- | v3's name for `MAX_ATTEMPTS`; still honoured |

`MAX_PLAN_COST` is calibrated against this dataset rather than chosen: the most
expensive of the 45 golden pairs plans at 125,767 and a full scan of the sales
fact at 20,096, while that fact cross-joined with `dim_product` is 1.6 million
and with itself 12.5 billion. The default is eight times the hardest known-good
query and below the cheapest cross join involving the fact table, so it rejects
runaway plans without rejecting real work. Re-derive it the same way whenever
the data is regenerated, since plan costs scale with row counts.

## Running outside Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
DATABASE_URL="postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail" \
VECTOR_DB_URL="postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors" \
EMBED_BASE_URL="http://localhost:11434" \
  python -m nl2sql_agent "How many stores are there?"
```

Run it from the `agent/` directory so `nl2sql_agent` is importable. Note that
the host URLs use `localhost` rather than the `postgres` / `vectordb` hostnames
that only resolve inside the compose network, and `localhost` rather than
`host.docker.internal` for the embedding host.
