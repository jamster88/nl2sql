# nl2sql

## Quick start

```bash
./setup.sh
docker compose run --rm agent "How many stores are there?"
```

That is the whole setup. [`setup.sh`](setup.sh) brings up the four containers
the agent needs and leaves them ready:

| Container | What it holds |
|---|---|
| `nl2sql-postgres` | The retail dataset, baked into the image |
| `nl2sql-vectordb` | pgvector: the knowledge base and the golden-pair vectors |
| `nl2sql-chunkdb` | The context store: the 45 golden pairs and their BM25 index |
| `agent` | The v4 agent, run on demand per question |
| `nl2sql-api` | The same agent as a TLS REST server, started only with `--api` |

### Two scripts

| | When | What it does |
|---|---|---|
| [`./setup.sh`](setup.sh) | First run on a machine | Pulls every image, pins them in `.env`, starts the databases, verifies retrieval end to end |
| [`./launch.sh`](launch.sh) | Every time after | Starts whatever is down and checks it is *populated* and both models are reachable |

Afterwards, in both cases:

```bash
docker compose run --rm agent "<your question>"
```

Or, for a GUI rather than a terminal, start the same agent as a REST server
over TLS -- see [Connecting a GUI](#connecting-a-gui):

```bash
./launch.sh --api
curl --cacert ./nl2sql-api.crt https://localhost:8443/v1/meta
```

They fail in different ways, which is why they are separate. Setup fails when an
image will not pull. Launch catches the things that go wrong later: a container
that is up but empty, a chat host that has moved, an embedding model that is not
the one the vectors were built with. None of those stop the stack from starting,
and all of them make the agent look bad at its job rather than broken.

```
$ ./launch.sh
==> Checking what is actually in each database
    retail dataset: 1291781 sales rows
    knowledge base: 53 embedded chunks
    worked examples: 45 golden pairs, 45 embedded questions
    schema index: 20 DDL chunks (table selection needs no model call)

==> Checking the multi-agent pipeline
    literal matching: pg_trgm installed (trigram search)
    least privilege: the agent's role holds SELECT and nothing else

==> Checking the models
    chat model qwen3.8-256k is available at http://192.168.10.82:11434
    embedding model bge-m3 is available on this machine

==> Ready. Ask a question:

    docker compose run --rm agent "How many stores are there?"
```

Then ask. That is the whole contract: one script, then one command per
question.

```bash
docker compose run --rm agent "total net sales for dairy and eggs in FY2025"
```

Everything the script prints is something that fails *later* and looks like
the agent being bad at its job. A container that is up but empty. A chat host
that moved. An embedding model that is not the one the vectors were built
with. A `.env` still pinning the previous agent image, so an upgrade silently
has no effect. And the two the multi-agent pipeline added: the DDL-chunk
collection its table selection reads instead of calling the model, and whether
its database role has picked up a grant it should not have.

`./launch.sh --no-rag` starts only the retail database; `--restart` recreates the
containers; `-q` prints only problems. Run it with no `.env` present and it hands
off to `setup.sh` rather than guessing.

[`setup.sh`](setup.sh) pulls each image, starts the databases, writes a `.env` so
plain `docker compose` commands pick all of that up, checks that the chat and
embedding models are reachable, and finishes by proving the agent container can
actually retrieve from the knowledge base:

```
==> Checking the agent can reach the knowledge base
    retrieval works end to end (3 collections searched)

==> Setup complete. Running now:

    nl2sql-postgres    the retail dataset
    nl2sql-vectordb    the embedded knowledge base
    nl2sql-chunkdb     the golden pairs and their BM25 index
```

It takes a couple of minutes, mostly downloading, and is safe to re-run.

Useful flags: `--ollama-url URL` and `--model NAME` to point the agent at a
different Ollama host or model, `--embed-url URL` for the host serving the
embedding model, `--no-rag` to skip the knowledge base entirely, `--build-agent`
to build the agent from source instead of pulling it, `--build` to generate the
dataset locally, `--no-verify` to skip the closing check, and `--reset` to
discard an existing database volume and start from the image's data.
`./setup.sh --help` lists them all.

Stop everything with `docker compose down`; both databases keep their data.

## NL2SQL agent (v4, multi-agent)

A LangChain/LangGraph agent that answers natural language questions by writing,
validating, and running SQL against the Postgres container below. It uses any
model served by Ollama.

**v2 retrieves before it writes.** Each question is embedded and matched against
a pgvector knowledge base built from [`knowledge/`](knowledge) -- the data
dictionary, DDL index and business index -- and the matching sections are fed to
the model alongside the schema. That context carries what the schema cannot:
fiscal-calendar semantics, pre-aggregated columns, and joins that fan out.

**v3 also retrieves worked examples, and generates multi-shot.** A second,
independent step searches the 45 question/SQL pairs in
[`context_questions/translated_questions.md`](context_questions/translated_questions.md)
-- each verified to run against this database -- through an ensemble of three
retrievers: question similarity (0.50), BM25 over the pairs' keywords (0.35),
and reasoning-target similarity (0.15). The fused shortlist is then **reranked**:
scored against the pair's tables and SQL, which no retriever indexes, and
diversified so three exemplars teach three patterns rather than one three times.

The winners are replayed to the model as real conversation turns -- human asks,
assistant answers with SQL -- in front of the actual question. Prose tells the
model the rule; a worked example shows it applied.

**v4 splits the work across agents and takes the model out of two of them.**
The same retrieval runs, now as four parallel branches -- schema, literals,
knowledge, worked examples -- joined by a context aggregator. Table selection
became a vector search over the DDL chunks plus foreign-key closure, and
validation became a `pglast` parse plus a plain `EXPLAIN` with a cost ceiling.
Both had been model calls, and between them they cost 863 of v3's 1499
benchmark seconds without writing any part of the answer. A literal matcher
resolves phrases to real values, so "dairy and eggs" reaches the generator as
`dim_product.department_name = 'Dairy & Eggs'`. After execution a formatter
picks a chart from the result's shape, a narrator writes claims that each name
the cells they came from, and a deterministic audit checks every number against
those cells. Any failure -- parse, planner, runtime or audit -- routes to one
repair agent and spends one shared retry budget.

The design, and every place it departs from the three source documents, is in
[`multi-agent_arch_specs/Multi-Agent_NL2SQL_arch4.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch4.md).

See [`agent/USAGE.md`](agent/USAGE.md) for how to launch it and ask questions,
and [`agent/README.md`](agent/README.md) for how it works.

```bash
docker compose run --rm agent "What were the top 5 departments by net sales in fiscal year 2024?"
```

Where it shows: the benchmark's grain and fan-out questions. Schema-only scores
1 of 3 on grain and 0 of 1 on fan-out; with the knowledge base both go to full
marks. See [Benchmark](#benchmark) for the run.

```bash
docker compose run --rm agent "What is our overall market share in fiscal year 2024?"
docker compose run --rm agent --no-rag "..."   # schema-only, v1 behavior
```

The market-share question is subtler than it first looks, and worth knowing
before reading too much into any single number. Summing both columns raw
inflates each by 5x and the **ratio survives** -- 21.51% either way. The mistake
that yields 107.5% is asymmetric: a raw numerator over a de-duplicated
denominator. In the benchmark run the schema-only agent produced neither. It
wrote a correct query, rejected it three times in its own validation step, and
gave up -- the only question in the whole run where any configuration failed to
produce an answer at all.

### Pulling the agent image

```bash
docker pull mcfaddja/nl2sql-agent:v4_1
```

To publish a new one, build both architectures in the same step so the tag
stays multi-arch, as every earlier agent tag is:

```bash
docker login
docker buildx build --platform linux/amd64,linux/arm64 \
  -f agent/Dockerfile --push -t mcfaddja/nl2sql-agent:v4_1 .
```

The image version label comes from `AGENT_VERSION` in
[`agent/Dockerfile`](agent/Dockerfile), and a test pins it to
`nl2sql_agent.__version__`, so the two cannot drift.

| Tag | Use |
|---|---|
| `v4_1` | The multi-agent pipeline plus the REST API. Pinned -- what `setup.sh` pulls. |
| `v4` | The multi-agent pipeline, CLI only. Pinned; `./launch.sh --api` cannot run against it, and says so. |
| `v3` | RAG plus the golden-pair ensemble, one linear graph. Pinned. |
| `v2` | Retrieval over the knowledge base only. Pinned. |
| `v1` | The original schema-only agent, before retrieval. Pinned. |
| `latest` | Moves to the newest publish (currently the same image as `v2`). |

Each is a genuinely different image rather than the newest one with switches
turned off -- `v1` has no `retrieval` module and no `--rag` flags; `v2` has no
`examples` module and no `--multi-shot`:

```bash
./setup.sh --agent-tag v2                 # the v2 stack
./setup.sh --agent-tag v1 --no-rag        # the v1 stack
```

`v2` also reads the v3 databases quite happily: it finds its knowledge
collections by name and never looks at the golden-pair tables.

`v4` needs one thing the earlier images did not: a `nl2sql_reader` role, and
`pg_trgm` if literal matching is to use trigram search rather than falling
back to `difflib`. Both are created by `setup.sh` and `launch.sh` on every
start, so a `v1` retail image works with the `v4` agent.

`v1` is what the comparison below is measured against, and it is a genuinely
different image rather than `v2` with retrieval switched off -- it has no
`--rag` flags and no `retrieval` module at all:

```bash
docker pull mcfaddja/nl2sql-agent:v1
./setup.sh --agent-tag v1 --no-rag      # set the stack up against it
```

The two retrieval databases are separate images, started for you by compose:

```bash
docker pull mcfaddja/nl2sql-rag-vectordb:v3    # pgvector: knowledge + golden-pair vectors
docker pull mcfaddja/nl2sql-rag-chunkdb:v3     # context store: golden pairs + BM25 statistics
```

| Tag | Holds |
|---|---|
| `nl2sql-rag-vectordb:v3` | The 53 knowledge chunks as in `v1`, plus `golden_pair_question_vectors` and `golden_pair_reasoning_vectors` -- 45 rows each |
| `nl2sql-rag-chunkdb:v3` | `golden_pairs` (45 rows, 8 content columns) plus the BM25 term statistics and the `golden_pairs_bm25()` ranking function |
| `nl2sql-rag-vectordb:v1` | Knowledge collections only -- what v2 searches |

## Connecting a GUI

The agent also answers over HTTPS, so a front end can be written in anything.
There is no client library here and there is not meant to be one: the
interface is JSON over HTTP with an OpenAPI document the server generates
itself, and a TypeScript, Python, Java or Go client is generated from that
rather than written by hand.

```bash
./launch.sh --api
docker compose --profile api cp api:/etc/nl2sql/tls/server.crt ./nl2sql-api.crt

curl --cacert ./nl2sql-api.crt https://localhost:8443/v1/meta
curl --cacert ./nl2sql-api.crt -X POST 'https://localhost:8443/v1/questions?wait=180' \
     -H 'Content-Type: application/json' \
     -d '{"question": "How many stores are there?"}'
```

It is the *same image* as the agent, started as a server instead of a command
(`python -m nl2sql_agent.api`), so the pipeline answering a GUI is the
pipeline that was benchmarked.

**A question is a resource, not a request.** Answering takes about a minute,
which no GUI can hold a connection open for while showing nothing. `POST
/v1/questions` returns a job immediately; the client polls it, streams its
progress, or asks the server to hold the connection with `?wait=`. All three
return the same document, so waiting is an optimisation rather than a second
contract.

**Progress is the pipeline, not an animation.** `GET
/v1/questions/{id}/events` is a Server-Sent Event stream carrying the graph's
own nodes as they happen -- screening, schema, literals, SQL, the plan gate,
execution, the narrator, the audit -- and it resumes from `Last-Event-ID`
after a dropped connection.

**TLS is on by default.** The container has no certificate to be given, so on
first start it writes itself a self-signed one covering `localhost` and the
compose service name, and keeps it in a volume so a restart presents the same
certificate. That is a development convenience, and
`API_TLS_ALLOW_SELF_SIGNED=false` takes it away: the server then refuses to
start behind a self-signed certificate at all -- it will not generate one and
will not load one it finds -- so a deployment meant to have a real chain fails
at startup instead of quietly serving the throwaway one.

Set `API_TOKEN` to require a bearer token, and `API_CORS_ORIGINS` to the
GUI's origin when a browser calls it directly.

To try the whole thing from outside, with no Python and no shared code:

```bash
docker compose --profile api run --rm apitest
```

`apitest` is an Alpine image holding curl and jq. It verifies the
certificate, walks every endpoint, streams a real question's progress, and
exits `1` on a failed check or `2` when the API was never reachable -- so CI
can tell a retry apart from a defect. It is also the shortest complete
reference for writing a client.

[`agent/API.md`](agent/API.md) is the contract: every endpoint, the response
shapes, the event stream, the error codes, the settings, and worked client
snippets for TypeScript/React, Python and Java.

## Benchmark

```bash
python benchmarks/run_benchmark.py            # 15 questions, accuracy then speed
python benchmarks/run_benchmark.py --compare  # schema-only vs knowledge vs multi-shot
```

[`benchmarks/`](benchmarks) holds fifteen questions that are deliberately **not**
the 45 golden pairs the agent retrieves from -- a benchmark drawn from those
would measure how well it can look something up. Accuracy is **execution
accuracy**: the SQL is run and its rows compared against reference SQL verified
against the shipped dataset. Query text is never compared, because two correct
queries for the same question rarely look alike.

### Measured

All three configurations, same 15 questions, `qwen3.8-256k` at 256k context:

| | accuracy | produced SQL | retries | total | median |
|---|---|---|---|---|---|
| `schema-only` (v1) | 12/15 (80%) | 14/15 | 5 | 511s | 21.0s |
| `knowledge` (v2) | **15/15 (100%)** | 15/15 | 0 | 1251s | 81.8s |
| `multi-shot` (v3) | **15/15 (100%)** | 15/15 | 0 | 1499s | 100.5s |
| multi-agent (v4) | **15/15 (100%)** | 15/15 | 3 | 910s | 61.2s |

| | analysis | calendar | fan-out | grain | schema |
|---|---|---|---|---|---|
| `schema-only` | 5/5 | 3/3 | **0/1** | **1/3** | 3/3 |
| `knowledge` | 5/5 | 3/3 | 1/1 | 3/3 | 3/3 |
| `multi-shot` | 5/5 | 3/3 | 1/1 | 3/3 | 3/3 |

Three things this says, including one that is not flattering:

**The knowledge base earns its place.** Every question schema-only missed is a
grain or fan-out question, and the knowledge base fixes all of them. It also
removes every retry: 5 down to 0.

**Multi-shot adds no measurable accuracy here, and costs 20% more time.** Both
retrieval configurations score 15/15, so this set cannot distinguish them --
once the knowledge base is on there is no headroom left to measure. That is a
limitation of a 15-question benchmark, not evidence that the examples do
nothing, but it is what the numbers say and they should not be read as more.
Telling the two apart needs harder questions than these.

**Accuracy costs roughly 3x the wall time**, and almost none of it is retrieval.
Across 15 questions the knowledge base and the golden-pair ensemble together
take **1.2 seconds**; the rest is the model:

```
  generate_sql            634s
  validate_sql            499s
  select_tables           364s
  retrieve_knowledge      1.1s
  retrieve_examples       0.1s
```

**v4 spends a third less time for the same answers**, by deleting the two model
calls that were not writing them. Validation became an AST parse and a plain
`EXPLAIN`; table selection became a vector search over the DDL chunks plus
foreign-key closure:

```
  v3  validate_sql   499.4s   ->   v4  validate_static + planner_gate   0.1s
  v3  select_tables  363.9s   ->   v4  retrieve_schema                  1.2s
```

It spends some of that back on a narrator, about a quarter of the run, which
the architecture did not anticipate. What it buys is a narrative whose every
number has been checked against a cell of the result rather than asserted, and
`NARRATE_ENABLED=false` removes it.

The retries are not a regression either: v3 had none because its LLM validator
passed everything it did not reject outright, while v4's planner catches three
real errors and repairs them without a model call.

`select_tables` and `validate_sql` together cost more than generation itself.
Two model calls that do not write the answer take the majority of the time,
which is the obvious latency lever -- well ahead of anything in the RAG layer.

See [`benchmarks/README.md`](benchmarks/README.md) for the scoring rules and
what they do and do not forgive.

## Architecture diagrams

[`arch_diagrams/`](arch_diagrams) holds one diagram per agent version: what
each step does, why it is there, and how control flows.

| | |
|---|---|
| [`arch_v1.svg`](arch_diagrams/arch_v1.svg) | The schema-only pipeline |
| [`arch_v2.svg`](arch_diagrams/arch_v2.svg) | The same pipeline with retrieval in front of it, and that context threaded into three of the five steps |
| [`arch_v3.svg`](arch_diagrams/arch_v3.svg) | Both retrieval steps, the three-retriever ensemble behind the second, and the two data-flow rails they feed |

All three are laid out identically so the versions can be read side by side --
everything new or changed is marked, in teal for v2's retrieval and indigo for
v3's examples. Each shows the deployment (what runs where), the startup
preflight, every LangGraph node paired with the reasoning behind it, the retry
loop, and the exit codes.

They are generated, not drawn:

```bash
python arch_diagrams/generate.py
```

[`generate.py`](arch_diagrams/generate.py) computes the layout -- text wrapped
against real font metrics, row heights following their content -- and records
the nodes it drew in the SVG, which is what lets
[`tests/docs/test_arch_diagrams.py`](tests/docs/test_arch_diagrams.py) check
the pictures against `graph.py` and fail when a node is renamed. Edit the
content in `build_v1()` / `build_v2()` / `build_v3()` and re-run; do not
hand-edit the SVGs.

## Synthetic data generator

A synthetic dataset generator for a grocery retail data model, along with the schema it implements, lives in [`data_gen/`](data_gen/README.md) -- see that README for details, setup, and usage.

Quick start:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r data_gen/requirements.txt
python data_gen/generate_data.py
```

## Postgres container

The Postgres image has the generated dataset **already loaded into the
cluster**, so the data travels with the image and is available the moment a
container starts. `./setup.sh` pulls a prebuilt copy; the sections below cover
building your own.

```bash
docker compose up -d --build     # build it yourself: generates, loads, starts (~3 min)
docker compose up -d             # afterwards: just starts, nothing regenerated
```

Connect with `psql postgresql://nl2sql:nl2sql@localhost:5432/nl2sql_retail`
(the `postgres` superuser has the same password).

### Roles

The cluster has two application roles, and the agent only ever uses the second:

| Role | Password | Can | Used by |
|---|---|---|---|
| `nl2sql` | `nl2sql` | everything: owns the database and every table | the build (`ddl.sql`, the `COPY` load) and you, at a `psql` prompt |
| `nl2sql_reader` | `nl2sql_reader` | `SELECT` on every table in `public`, nothing else | the agent, the benchmark, the live tests |

The reader is created by [`docker/reader_role.sql`](docker/reader_role.sql):
a plain login role with no `CREATE`, `INSERT`, `UPDATE` or `DELETE` anywhere,
whose sessions also start read-only. Tables the owner adds later are readable
too, through a default privilege. The agent's own `SET TRANSACTION READ ONLY`
still runs on top of that; the grants are what hold if anything gets past it.

The image build creates the role, and so do `setup.sh` and `launch.sh` on every
start, because a volume created from an older image keeps the roles it had.
The file is idempotent, so running it again is always safe. If you run the
container by hand, do the same once:

```bash
docker exec -i nl2sql-postgres psql -U postgres -d nl2sql_retail \
  -v reader=nl2sql_reader -v reader_password=nl2sql_reader -v owner=nl2sql \
  -f - < docker/reader_role.sql
```

`POSTGRES_READER_USER` / `POSTGRES_READER_PASSWORD` rename the role; both the
build and the agent's `DATABASE_URL` read them, so they stay in step.

That this is really least privilege, and not just a file that says so, is
tested against the live cluster by
[`tests/agent/test_least_privilege_live.py`](tests/agent/test_least_privilege_live.py):
it reads the role's attributes and grants back out of the catalog, tries every
kind of write directly in a `READ WRITE` transaction, checks that a table the
owner adds later is readable but not writable, and confirms the agent's own
database layer runs as the reader. Pointed at the owner instead, 23 of its
29 checks fail. Run it with `pytest tests/agent/test_least_privilege_live.py --run-docker`
against a started stack.

### How the build works

The build ([`docker/Dockerfile`](docker/Dockerfile)) is two stages:

1. **generator** -- installs `data_gen/requirements.txt`, runs
   `generate_data.py --no-sqlite`, and writes one CSV per table.
2. **db** -- runs `initdb`, applies [`data_gen/ddl.sql`](data_gen/ddl.sql),
   bulk-loads every CSV with `COPY` in foreign-key-safe order, runs
   `VACUUM ANALYZE`, and shuts the cluster down cleanly so the populated data
   directory becomes an image layer.

The CSVs are bind-mounted from stage 1 rather than copied, so they never become
a layer in the final image and nothing is written to the host -- the only place
the data survives is inside Postgres. Stage 1's output does stay in the local
build cache; `docker builder prune` reclaims it.

Two details make the baking work:

- `PGDATA` is set to `/var/lib/pgdata`. The base image declares
  `/var/lib/postgresql` as a `VOLUME`, and writes to a volume path during a
  build are discarded.
- Because the cluster is initialized at build time, the `POSTGRES_USER` /
  `POSTGRES_PASSWORD` environment variables are *not* consulted at runtime.
  Credentials are fixed when the image is built.

### Persistence

The compose service mounts the named volume `nl2sql-pgdata` at `PGDATA`. Docker
seeds that volume from the image the first time it's created, and it then
retains anything written afterwards:

| Command | Data |
|---|---|
| `docker compose restart` / `stop` + `start` | kept |
| `docker compose down` then `up -d` | kept |
| `docker compose down -v` | **deleted**, reseeded from the image on next `up` |

So `down -v` is also how you reset to the pristine generated dataset.

### Changing the dataset

Any `generate_data.py` flag can be passed through `GEN_ARGS`. The volume takes
precedence over the image, so drop it when rebuilding:

```bash
GEN_ARGS="--scale 3 --seed 7" docker compose build
docker compose down -v && docker compose up -d
```

Other overrides: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_READER_USER`, `POSTGRES_READER_PASSWORD` (applied at build time),
`POSTGRES_PORT`, `IMAGE_NAME`, `IMAGE_TAG`.

### Pulling the prebuilt image

`./setup.sh` does this for you; this section covers doing it by hand. The
dataset is published, so pulling it avoids building anything -- no Python, no
generator run -- and everyone gets byte-identical data:

```bash
docker pull mcfaddja/nl2sql-retail-postgres:v1
```

The repository is public, so no `docker login` is needed. It is multi-arch
(`linux/amd64` and `linux/arm64`), so Docker selects the right variant
automatically. Expect roughly a 290 MB download that expands to about 1.4 GB on
disk.

Two tags are published:

| Tag | Use |
|---|---|
| `v1` | Pinned. Use this for reproducible testing -- it will not change underneath you. |
| `latest` | Moves to the newest publish. |

#### Run it directly

```bash
docker run -d --name nl2sql-postgres \
  -p 5432:5432 \
  -v nl2sql-pgdata:/var/lib/pgdata \
  mcfaddja/nl2sql-retail-postgres:v1
```

The volume must be mounted at `/var/lib/pgdata`, which is where this image puts
`PGDATA` (see the note above). The data is present on first start; the volume
only keeps what you write afterwards. Connect exactly as with a locally built
image:

```
psql postgresql://nl2sql:nl2sql@localhost:5432/nl2sql_retail
```

The credentials are baked into the published cluster, so treat them as public --
fine for synthetic test data, and not to be reused elsewhere. The `v1` image
predates the agent's read-only role, so create it as shown under
[Roles](#roles) before running the agent against a container started this way.

#### Use it with compose

To point compose at the published image without running `setup.sh`:

```bash
export IMAGE_NAME=mcfaddja/nl2sql-retail-postgres IMAGE_TAG=v1
docker compose pull postgres
docker compose up -d --no-build
```

Everything else in this README still applies -- the volume, the persistence
table above, and `down -v` to reset to the pristine dataset.

### Publishing an update

Rebuilding and pushing replaces the published dataset. Build both architectures
in one step so the tag stays multi-arch:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/Dockerfile --push -t mcfaddja/nl2sql-retail-postgres:v2 .
```

## Tests

```bash
pip install -r tests/requirements.txt
pytest                  # 1244 tests, no Docker or network needed
pytest --run-docker     # all 1566, including ones that build and run containers
```

| Directory | Covers |
|---|---|
| [`tests/data_gen/`](tests/data_gen) | The generator: calendar, dimensions, facts, validation, CSV/SQLite writing, and `generate_data.py` as a script |
| [`tests/agent/`](tests/agent) | The agent: config, prompts, the LangGraph pipeline, the tools, both retrievers, the ensemble fusion, read-only enforcement, and least privilege -- what the reader role can and cannot do, asked of a live catalog |
| [`tests/api/`](tests/api) | The REST server: the certificate policy and the switch that refuses a self-signed one, the job store, every route and status code, the event stream, a real uvicorn bound to a loopback port over real TLS, and the curl-only smoke script run against it for real |
| [`tests/rag/`](tests/rag) | The RAG pipeline: parsing the golden pairs, the BM25 index checked against an independent implementation, the pgvector storage layer, the semantic chunker the markdown one inherits from, and both loader scripts -- their flags offline and their writes against a throwaway database created and dropped around each test |
| [`tests/docker/`](tests/docker) | The Dockerfiles, the reader-role SQL, `docker-compose.yml` as `docker compose config` resolves it (including that the owner's credentials never reach the agent and that every setting the agent reads can be set through it), retrieval end to end inside the real containers, and `setup.sh`/`launch.sh` run against fake `docker`/`curl` binaries -- plus a structural check that every flag, warning and fatal message in all three shell scripts is exercised by some test, and the API container reached over TLS by a curl-only container with nothing of this project in it |
| [`tests/docs/`](tests/docs) | These documents and the architecture diagrams, checked against the code they describe |
| [`tests/benchmarks/`](tests/benchmarks) | The benchmark's own ground truth: every reference query executed against the dataset, and the scorer tested against both kinds of mistake it could make |

The 322 tests behind `--run-docker` are the ones that need a working daemon:
they build the agent image and run it, resolve the real compose file, and query
the three live databases. Everything else runs offline in about 20 seconds --
`setup.sh` included, since it is exercised against fake binaries rather than
real Docker.

Twenty-eight of those 322 also need the **embedding host**: a local Ollama
serving `bge-m3`, the model both vector stores were built with. Without it they
skip with that as the stated reason rather than failing -- the rest of the
suite still passes, which is the property that matters. Start it with
`ollama serve` (and `ollama pull bge-m3` once) to run everything. To re-count
after a change:

```bash
EMBED_BASE_URL=http://127.0.0.1:9 pytest --run-docker   # whatever skips needs it
```

The 67 database-backed tests in [`tests/rag/`](tests/rag) need the two stores
but **not** the embedding model: they exercise the storage layer with
synthetic vectors, which makes the distances predictable rather than merely
plausible. Each one runs against a throwaway database created and dropped
around it, so the published golden pairs and embeddings in the running
containers are never touched.

### Coverage

```bash
COVERAGE_FILE=$PWD/.coverage COVERAGE_PROCESS_START=$PWD/.coveragerc \
  coverage run --rcfile=.coveragerc -m pytest --run-docker
coverage combine && coverage report --show-missing --skip-covered
```

**100% of every Python file in the repository** -- 5,587 statements, none
missed. Not four packages with the scripts left out: the agent and its REST
server, the benchmark, the RAG pipeline and its four loader scripts, the data
generator and its CLI, the chunker, the architecture-diagram generator, and
the build-time SQL emitter.

Exactly one statement is excluded, and the reason is written beside it: a
defensive `continue` in `facts.py` that is unreachable by construction,
because the loop runs to `max(k)` and the basket whose `k` equals that maximum
always satisfies the condition the guard tests. It is kept in case the loop
bounds ever change.

`COVERAGE_PROCESS_START` is not incidental. Several things here are tested the
way they are *used* -- as scripts, in their own process. `generate_data.py` is
run by `docker/Dockerfile`, `emit_load_sql.py` during the image build, the RAG
loaders by hand. Their tests invoke them the same way, with `subprocess.run`,
and without [`.coveragerc`](.coveragerc) turning on subprocess measurement the
report shows **0%** for a file with nine tests on it -- which is worse than no
number, because it sends someone off to write tests that already exist.
Switching it on moved five files from "untested" to 100% without a line of new
test code.

Getting the rest there deletes code as often as it adds tests. The last few
statements turned up a re-raise that could never fire (none of the five
whitelisted formula functions raises the exception it caught), two
`except ValueError` guards behind a regex that only matches valid floats, and
two properties on the API's agent holder that nothing read. A line no test can
reach is usually a line that cannot happen, and deleting it is the honest fix.

What was left after that was real: the `main()` of both RAG loaders -- the only
way the golden pairs reach either store -- the base `SemanticChunker` that
`MarkdownSemanticChunker` inherits from, and the entry points of the diagram
generator and both loaders. Those have tests now, against throwaway databases
and stub embedders.

#### The parts a coverage report cannot see

Three shell scripts and a compose file, none of them Python. They are covered
by reading and by running, not by a report:

* **`setup.sh` and `launch.sh`** are run against fake `docker`, `curl` and
  `sleep` binaries, once per scenario they can take.
  [`tests/docker/test_script_coverage.py`](tests/docker/test_script_coverage.py)
  then asserts structurally that every flag is parsed, documented and passed
  by some test, and that every `warn` and `die` message is asserted somewhere.
  A warning nobody triggers looks exactly like a warning that works, and these
  scripts are almost entirely warnings. Measured by `xtrace`, that reaches 94%
  and 93% of their lines; the remainder is lines bash cannot report at all --
  function headers, `case` labels, and multi-line command substitutions.
* **`docker/apitest/smoke.sh`**, the outside client, is run *for real* by
  [`tests/api/test_smoke_script.py`](tests/api/test_smoke_script.py): bash,
  curl and jq against a live HTTPS server built from `create_app` with a
  scripted pipeline. No Docker and no model, so every one of its paths runs on
  an ordinary `pytest` -- each of the three ways it decides to trust the
  server, a server that is up but not ready, a question that fails, a stream
  that carries nothing, and the refusals that make its two exit codes mean
  something. 89% of its lines by `xtrace`, the rest being the same
  bash-unreportable shapes.
* **`docker-compose.yml`** is checked in both directions for every service
  that takes settings: nothing is set that the code never reads, and nothing
  the code reads is missing from it. That holds for the agent's own settings
  against `config.py`, the API's against `api/settings.py`, and the smoke
  script's against the script itself.

Running the scripts rather than only reading them is what earns its keep.
Doing it turned up three defects in one pass: the smoke script aborted under
`set -u` on bash 3.2 -- which is what macOS ships, and invisible inside its
own Alpine container; an unauthorised client exited `2`, the code that means
"the API was never there, retry", when the API was answering perfectly well;
and a missing `.tables` was counted as zero and announced as a pass, so an
error body read as a healthy server with nothing in it.

Getting the RAG pipeline to 100% turned up a real defect the same way.
`vector_store.search()` bound its query vector as a Python list, which
Postgres reads as `double precision[]` -- a type with no `<=>` operator at all,
so the function raised for every caller. It had only ever been reached from a
README example. The fix is the same text-literal cast the agent-side
retrievers use, and the regression is pinned by a test.
