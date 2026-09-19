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
| `agent` | The v3 agent, run on demand per question |

### Two scripts

| | When | What it does |
|---|---|---|
| [`./setup.sh`](setup.sh) | First run on a machine | Pulls every image, pins them in `.env`, starts the databases, verifies retrieval end to end |
| [`./launch.sh`](launch.sh) | Every time after | Starts whatever is down and checks it is *populated* and both models are reachable |

Afterwards, in both cases:

```bash
docker compose run --rm agent "<your question>"
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

==> Checking the models
    chat model qwen3.8-256k is available at http://192.168.10.82:11434
    embedding model bge-m3 is available on this machine

==> Ready. Ask a question:

    docker compose run --rm agent "How many stores are there?"
```

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

## NL2SQL agent (v3, RAG + worked examples)

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
docker pull mcfaddja/nl2sql-agent:v3
```

| Tag | Use |
|---|---|
| `v3` | RAG plus the golden-pair ensemble. Pinned -- what `setup.sh` pulls. |
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
pytest                  # 572 tests, no Docker or network needed
pytest --run-docker     # all 765, including ones that build and run containers
```

| Directory | Covers |
|---|---|
| [`tests/data_gen/`](tests/data_gen) | The generator: calendar, dimensions, facts, validation, CSV/SQLite writing, and `generate_data.py` as a script |
| [`tests/agent/`](tests/agent) | The agent: config, prompts, the LangGraph pipeline, the tools, both retrievers, the ensemble fusion, and read-only enforcement |
| [`tests/rag/`](tests/rag) | The RAG pipeline: parsing the golden pairs, the BM25 index checked against an independent implementation, the pgvector storage layer, and both loader scripts |
| [`tests/docker/`](tests/docker) | The Dockerfiles, `docker-compose.yml` as `docker compose config` resolves it, retrieval end to end inside the real containers, and `setup.sh`/`launch.sh` run against fake `docker`/`curl` binaries |
| [`tests/docs/`](tests/docs) | These documents and the architecture diagrams, checked against the code they describe |
| [`tests/benchmarks/`](tests/benchmarks) | The benchmark's own ground truth: every reference query executed against the dataset, and the scorer tested against both kinds of mistake it could make |

The 193 tests behind `--run-docker` are the ones that need a working daemon:
they build the agent image and run it, resolve the real compose file, and query
the three live databases. Everything else runs offline in about 20 seconds --
`setup.sh` included, since it is exercised against fake binaries rather than
real Docker.

Thirty-eight of those 187 also need the **embedding host**: a local Ollama
serving `bge-m3`, the model both vector stores were built with. Without it they
skip with that as the stated reason rather than failing. Start it with
`ollama serve` (and `ollama pull bge-m3` once) to run the whole suite.

The 148 tests in [`tests/rag/`](tests/rag) need the databases but **not** the
embedding model: they exercise the storage layer with synthetic vectors, which
makes the distances predictable rather than merely plausible. Each one runs
against a throwaway database created and dropped around it, so the published
golden pairs and embeddings in the running containers are never touched.

Coverage is **100%** of all three packages -- the agent, the data generator and
the RAG pipeline -- measured with `--run-docker`:

```bash
pytest --run-docker --cov=agent/nl2sql_agent --cov=rag/ragproc --cov=data_gen/datagen
```

Exactly one statement is excluded, and the reason is written beside it: a
defensive `continue` in `facts.py` that is unreachable by construction, because
the loop runs to `max(k)` and the basket whose `k` equals that maximum always
satisfies the condition the guard tests. It is kept in case the loop bounds ever
change.

Getting the RAG pipeline there turned up a real defect. `vector_store.search()`
bound its query vector as a Python list, which Postgres reads as
`double precision[]` -- a type with no `<=>` operator at all, so the function
raised for every caller. It had only ever been reached from a README example.
The fix is the same text-literal cast the agent-side retrievers use, and the
regression is pinned by a test.
