# nl2sql

## Quick start

```bash
./setup.sh
docker compose run --rm agent "How many stores are there?"
```

That is the whole setup. [`setup.sh`](setup.sh) brings up the three containers
the agent needs and leaves them ready:

| Container | What it holds |
|---|---|
| `nl2sql-postgres` | The retail dataset, baked into the image |
| `nl2sql-vectordb` | pgvector with the embedded knowledge base |
| `agent` | The v2 agent, run on demand per question |

It pulls each image, starts both databases, writes a `.env` so plain
`docker compose` commands pick all of that up, checks that the chat and
embedding models are reachable, and finishes by proving the agent container can
actually retrieve from the knowledge base:

```
==> Checking the agent can reach the knowledge base
    retrieval works end to end (3 collections searched)

==> Setup complete. Running now:

    nl2sql-postgres    the retail dataset
    nl2sql-vectordb    the embedded knowledge base
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

**v3 also retrieves worked examples.** A second, independent step searches the
45 question/SQL pairs in
[`context_questions/translated_questions.md`](context_questions/translated_questions.md)
-- each verified to run against this database -- through an ensemble of three
retrievers: question similarity (0.50), BM25 over the pairs' keywords (0.35),
and reasoning-target similarity (0.15). Prose tells the model the rule; a worked
example shows it applied.

Retrieving the examples is on by default; **showing** them to the SQL generator
is a separate switch (`--multi-shot`, off by default) so the ranking can be
inspected before it steers generation.

See [`agent/USAGE.md`](agent/USAGE.md) for how to launch it and ask questions,
and [`agent/README.md`](agent/README.md) for how it works.

```bash
docker compose run --rm agent "What were the top 5 departments by net sales in fiscal year 2024?"
```

Where it shows: asked for overall market share in FY2024, v1 answers **107.5%**
(it sums a total that repeats once per competitor); v2 retrieves the documented
fan-out rule, de-duplicates per cell, and answers **21.5%**.

```bash
docker compose run --rm agent "What is our overall market share in fiscal year 2024?"
docker compose run --rm agent --no-rag "..."   # schema-only, v1 behavior
```

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

Other overrides: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (applied at
build time), `POSTGRES_PORT`, `IMAGE_NAME`, `IMAGE_TAG`.

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
fine for synthetic test data, and not to be reused elsewhere.

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
pytest                  # 388 tests, no Docker or network needed
pytest --run-docker     # all 460, including ones that build and run containers
```

| Directory | Covers |
|---|---|
| [`tests/data_gen/`](tests/data_gen) | The generator: calendar, dimensions, facts, validation, CSV/SQLite writing, and `generate_data.py` as a script |
| [`tests/agent/`](tests/agent) | The agent: config, prompts, the LangGraph pipeline, the tools, both retrievers, the ensemble fusion, and read-only enforcement |
| [`tests/rag/`](tests/rag) | The RAG pipeline: parsing the golden pairs out of the markdown before anything is loaded or embedded |
| [`tests/docker/`](tests/docker) | The Dockerfiles, `docker-compose.yml` as `docker compose config` resolves it, and `setup.sh` run against fake `docker`/`curl` binaries |
| [`tests/docs/`](tests/docs) | These documents and the architecture diagrams, checked against the code they describe |

The 72 tests behind `--run-docker` are the ones that need a working daemon:
they build the agent image and run it, resolve the real compose file, and query
the three live databases. Everything else runs offline in about 20 seconds --
`setup.sh` included, since it is exercised against fake binaries rather than
real Docker.

Twenty-eight of those 72 also need the **embedding host**: a local Ollama serving
`bge-m3`, the model both vector stores were built with. Without it they skip
with that as the stated reason rather than failing. Start it with `ollama serve`
(and `ollama pull bge-m3` once) to run the whole suite.

Coverage is **99%** of both the agent package and the data generator, which is
every reachable statement. Exactly two are not covered, and neither can be:
`__main__.py`'s `if __name__ == "__main__"` guard, which pytest never executes,
and one defensive `continue` in `facts.py` that is unreachable by construction
(the loop runs to `max(k)`, so the index set it guards against is never empty).
