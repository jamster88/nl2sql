# nl2sql

## Quick start

```bash
./setup.sh
docker compose run --rm agent "How many stores are there?"
```

[`setup.sh`](setup.sh) pulls the Postgres image with the test dataset already
inside it, builds the agent image, starts the database, and writes a `.env` so
plain `docker compose` commands pick all of that up. It takes a couple of
minutes, mostly downloading the image, and is safe to re-run.

Useful flags: `--ollama-url URL` and `--model NAME` to point the agent at a
different Ollama host or model, `--build` to generate the dataset locally
instead of pulling it, and `--reset` to discard an existing database volume and
start from the image's data. `./setup.sh --help` lists them all.

## NL2SQL agent

A LangChain/LangGraph agent that answers natural language questions by writing,
validating, and running SQL against the Postgres container below. It uses any
model served by Ollama. See [`agent/USAGE.md`](agent/USAGE.md) for how to launch
it and ask questions, and [`agent/README.md`](agent/README.md) for how it works.

```bash
docker compose run --rm agent "What were the top 5 departments by net sales in fiscal year 2024?"
```

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
