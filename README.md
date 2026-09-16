# nl2sql

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

[`docker-compose.yml`](docker-compose.yml) builds a Postgres image with the
generated dataset **already loaded into the cluster**, so the data travels with
the image and is available the moment a container starts.

```bash
docker compose up -d --build     # first time: generates, loads, starts (~3 min)
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

### Publishing to Docker Hub

The image is self-contained, so pushing it shares the exact dataset:

```bash
docker tag nl2sql-retail-postgres:latest <dockerhub-user>/nl2sql-retail-postgres:v1
docker push <dockerhub-user>/nl2sql-retail-postgres:v1
```

Building `IMAGE_NAME=<dockerhub-user>/nl2sql-retail-postgres` skips the retag
step. A couple of things worth knowing before publishing:

- The image is ~1.4 GB at default scale, and the database credentials are baked
  into the cluster -- fine for synthetic test data, but treat a public image as
  public credentials.
- `docker compose build` produces an image for the machine you build on. For a
  multi-arch image, use
  `docker buildx build --platform linux/amd64,linux/arm64 -f docker/Dockerfile --push -t <repo>:<tag> .`
