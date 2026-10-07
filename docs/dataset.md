# The retail dataset

*Part of the [nl2sql documentation](../README.md#documentation).*

The synthetic grocery dataset, its generator, and the Postgres image it ships in.

## Synthetic data generator

A synthetic dataset generator for a grocery retail data model, along with the schema it implements, lives in [`data_gen/`](../data_gen/README.md) -- see that README for details, setup, and usage.

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

It serves **TLS only** over the network, with a certificate it writes
itself on first start, and has **no password baked in** (`v1_2`): its
entrypoint sets the owner's from `secrets/postgres_password` on every start,
and the `dbprep` one-shot the reader's from `secrets/postgres_reader_password`
-- `setup.sh` generates both -- and the `postgres` superuser has none at all,
so it is reached only over the database's own socket: by `dbprep`, or with
`docker compose exec postgres psql -U postgres`. Its port is this
machine's unless `DB_BIND_ADDRESS` says otherwise. Connect as the owner with

```bash
psql "postgresql://nl2sql@localhost:5432/nl2sql_retail?sslmode=require"   # the password in secrets/postgres_password
```

and see [`USAGE_GUIDE.md`](USAGE_GUIDE.md#connecting-to-the-retail-database-directly)
for verifying its certificate (`sslmode=verify-full`) and for a person
signing in with their own directory password.

### Roles

The cluster has two application roles, and the agent only ever uses the second:

| Role | Password | Can | Used by |
|---|---|---|---|
| `nl2sql` | `nl2sql` | everything: owns the database and every table | the build (`ddl.sql`, the `COPY` load) and you, at a `psql` prompt |
| `nl2sql_reader` | `nl2sql_reader` | `SELECT` on every table in `public`, nothing else | the agent, the review service's validation of a reviewer's SQL, the benchmark, the live tests |

The reader is created by [`docker/reader_role.sql`](../docker/reader_role.sql):
a plain login role with no `CREATE`, `INSERT`, `UPDATE` or `DELETE` anywhere,
whose sessions also start read-only. Tables the owner adds later are readable
too, through a default privilege. The agent's own `SET TRANSACTION READ ONLY`
still runs on top of that; the grants are what hold if anything gets past it.

Two things every role gets by default are taken away, because every reader
session -- the API's, each `docker compose run`, the review service's -- is
the *same* role, and Postgres lets a role cancel or terminate backends of its
own role:

* **`pg_cancel_backend` and `pg_terminate_backend`** are revoked from PUBLIC
  in the retail database, so one query cannot end everyone else's. Before,
  `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename =
  current_user` from any reader session did exactly that. The agent's static
  validator already refused both names; the review service's validator never
  had that list, and the server is the boundary either way.
* **`CONNECT` on every other database** -- `postgres`, `template1`, anything
  created later -- is revoked from PUBLIC, so the reader's password opens the
  retail database and nothing else. A function privilege is per database, and
  a pid is not, so an open `postgres` database would have been the way round
  the first revoke.

The superuser keeps both, and nothing that reads the dataset needs either.

The image build creates the role, and so does the `dbprep` one-shot that
`setup.sh` and `launch.sh` run on every start, because a volume created from
an older image keeps the roles it had: the same statements, from Python
([`common/nl2sql_ops/retail.py`](../common/nl2sql_ops/retail.py)), so neither
script runs SQL of its own (6.3). Both are idempotent, so running either
again is always safe. If you run the container by hand, do the same once:

```bash
docker exec -i nl2sql-postgres psql -U postgres -d nl2sql_retail \
  -v reader=nl2sql_reader -v reader_password=nl2sql_reader -v owner=nl2sql \
  -f - < docker/reader_role.sql
```

`POSTGRES_READER_USER` renames the role, and `secrets/postgres_reader_password`
is its password; the build, `dbprep` and the agent's `DATABASE_URL` all read
them, so they stay in step.

That this is really least privilege, and not just a file that says so, is
tested against the live cluster by
[`tests/agent/test_least_privilege_live.py`](../tests/agent/test_least_privilege_live.py):
it reads the role's attributes and grants back out of the catalog, tries every
kind of write directly in a `READ WRITE` transaction, checks that a table the
owner adds later is readable but not writable, tries the other databases and
tries to cancel and kill another reader session, and confirms the agent's own
database layer runs as the reader. Pointed at the owner instead, 25 of its
44 checks fail. The review service's side -- a reviewer's query run through
its validator, trying to switch the transaction to read-write, become the
owner, lift its own timeout, read the server's files or end another session
-- is in [`tests/review/test_validation.py`](../tests/review/test_validation.py). Run it with `pytest tests/agent/test_least_privilege_live.py --run-docker`
against a started stack.

### How the build works

The build ([`docker/Dockerfile`](../docker/Dockerfile)) is two stages:

1. **generator** -- installs `data_gen/requirements.txt`, runs
   `generate_data.py --no-sqlite`, and writes one CSV per table.
2. **db** -- runs `initdb`, applies [`data_gen/ddl.sql`](../data_gen/ddl.sql),
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
docker pull mcfaddja/nl2sql-retail-postgres:v1_2
```

The repository is public, so no `docker login` is needed. It is multi-arch
(`linux/amd64` and `linux/arm64`), so Docker selects the right variant
automatically. Expect roughly a 290 MB download that expands to about 1.4 GB on
disk.

The tags:

| Tag | Use |
|---|---|
| `v1_2` | The same dataset again, with no password baked in and TLS on: its entrypoint writes a certificate on first start, refuses anything over the network without TLS and the superuser over the network at all, and sets the owner's and the reader's passwords from the environment on every start -- of a fresh volume or of one an older image made. Pinned -- what `setup.sh` pulls since 6.1. |
| `v1_1` | The same dataset as `v1`, byte for byte, with the agent's read-only role built in -- and that role unable to connect to the cluster's other databases or to cancel or kill another session (see [Roles](#roles)). Its superuser's and owner's password is `nl2sql` in every copy: do not publish its port. |
| `v1` | The first publish. Pinned; it predates the read-only role, which `setup.sh` and `launch.sh` create on every start. |
| `latest` | Moves to the newest publish. |

#### Run it directly

```bash
docker run -d --name nl2sql-postgres \
  -p 127.0.0.1:5432:5432 \
  -e POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
  -v nl2sql-pgdata:/var/lib/pgdata \
  mcfaddja/nl2sql-retail-postgres:v1_2
```

The volume must be mounted at `/var/lib/pgdata`, which is where this image puts
`PGDATA` (see the note above). The data is present on first start; the volume
only keeps what you write afterwards. Connect exactly as with a locally built
image, over TLS, with the password you gave it:

```
psql "postgresql://nl2sql@localhost:5432/nl2sql_retail?sslmode=require"
```

Give `POSTGRES_READER_PASSWORD` too to set the agent's reader's. With
neither, nothing can sign in over the network -- `docker exec` still can.
The older `v1_1` and `v1` carry the owner's and the superuser's password,
`nl2sql`, in every copy: treat it as public, and never publish their port
beyond this machine. `v1` predates the agent's read-only role, so with that
tag create it as shown under [Roles](#roles) before running the agent
against a container started this way.

#### Use it with compose

To point compose at the published image without running `setup.sh`:

```bash
export IMAGE_NAME=mcfaddja/nl2sql-retail-postgres IMAGE_TAG=v1_2
docker compose pull postgres
docker compose up -d --no-build
```

Everything else in this document still applies -- the volume, the persistence
table above, and `down -v` to reset to the pristine dataset.

### Publishing an update

A published tag never moves, so an update is a new tag: a patch (`v1_3`) for
a change to the image around the same dataset, as `v1_1` and `v1_2` were,
and a new major (`v2`) for a new dataset. Build both architectures in one step so the tag stays
multi-arch, then move `latest` onto it without rebuilding:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/Dockerfile --push -t mcfaddja/nl2sql-retail-postgres:v2 .
docker buildx imagetools create -t mcfaddja/nl2sql-retail-postgres:latest \
  mcfaddja/nl2sql-retail-postgres:v2
```

The generator is seeded, so a rebuild of an unchanged `data_gen/` is the same
data; compare a checksum of every table against the previous tag before
calling a patch a patch.
