# Multi-Agent NL2SQL Architecture, v6.3

**Status:** arch6 and arch6.2 plus what 6.3.0 changed beneath them.
`Multi-Agent_NL2SQL_arch6.md` is the architecture as built at 6.1.0 and
`Multi-Agent_NL2SQL_arch6_2.md` what 6.2 changed in it; both stand. The
pipeline, its agents and their state are untouched by this release. This
document (2026-10-05) records what 6.3 changed in the containers and the
databases they run on -- phase 5 of the second adversarial review's
mitigation plan -- section by section of arch6, and supersedes arch6 and
arch6.2 where they disagree. Section numbers below are arch6's.

What changed, in one line each:

| Area | Before (6.2) | Now (6.3) | arch6 |
| --- | --- | --- | --- |
| Containers | writable roots, Docker's default capabilities, no ceiling on memory or processes | read-only, every capability dropped but the few given back to switch user, no privilege gained, a memory and process ceiling each (V6-34) | 2, 21.3 |
| Secrets | environment variables from `.env`, visible to `docker inspect` | a file each in `secrets/`, mounted into only the services that read it (V6-38) | 21.3 |
| The pages | six nginx images, six copies of one start-up script | one image, `nl2sql-proxy`, told which page it serves (V6-37) | 2 |
| Health checks | twelve that did not verify the certificate they were answered with | each verifies against the stack's CA (V6-37) | 21.2 |
| Base images | tags | tags and digests, checked by a tool (V6-35) | 8 |
| Role ceilings | a statement timeout and connection limit on each person | a statement timeout, `work_mem`, an idle-in-transaction timeout and a connection limit on every role a service connects as (V6-39) | 8 |
| The runtime stores | four Postgres servers, each owner its server's superuser | four databases in one server, no owner a superuser (V6-40, begun) | 2, 14.3 |
| Preparing the databases | SQL run by `setup.sh` and `launch.sh` through `docker exec` | a Python one-shot, `dbprep`, over each database's own socket (V6-41, begun) | 8, 10, 21.1 |

---

## 2. What runs where: the rows that changed

| Component | Host | Used by |
|---|---|---|
| The runtime stores: the staging database, the corrections and completions stores (pgvector) and the snippet store (pgvector), a database each with an owner of its own | `nl2sql-stores` (port 5435), answering to `nl2sql-feedbackdb`, `nl2sql-correctionsdb`, `nl2sql-completionsdb` and `nl2sql-snippetsdb` as well | the REST API (INSERT only, staging); the review service (owner of all four); the Snippet Retriever (reading, as `snippets_reader`) |
| Every page and MLflow's front door | `nl2sql-proxy`, one image: `nl2sql-gui`, `nl2sql-review-gui`, `nl2sql-curate-gui`, `nl2sql-console-gui`, `nl2sql-directory-gui`, `nl2sql-mlflow-proxy` are containers of it, each with its `NL2SQL_PAGE` | browsers; an MLflow client |
| Database preparation | `nl2sql-dbprep`, a one-shot from the agent's image, before every start | every database: the roles, extensions, sign-in rules and passwords |
| Moving a store from before 6.3 | `storesmigrate`, a one-shot from the stores' image, run by `launch.sh` once for each old volume | -- |

The RAG stores, `nl2sql-vectordb` and `nl2sql-chunkdb`, stay servers of
their own: they ship their data inside published images, and the plan's
V6-03 keeps them that way. MLflow's store stays its own as well.

## 8. Security blueprint: the rows that changed, and the rows 6.3 adds

| Layer | Rule | Enforced in | Held by |
|---|---|---|---|
| Containers | Every service starts from `x-hardened`: read-only root, `/tmp` in memory, `cap_drop: [ALL]`, `no-new-privileges`, and a `mem_limit` and `pids_limit` of its own. A service that starts as root to hand a volume over gets back `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID` and `SETUID` at most. | `docker-compose.yml` | `tests/security/test_posture.py`; `tests/acceptance/test_stack.py` in the running stack |
| Secrets | No password or token in any service's environment: each is a file in `secrets/`, mounted at `/run/secrets/<name>` where its service reads `<NAME>_FILE`; a URL carries no password, and its service puts in the one from the file beside it. | `docker-compose.yml`, `common/nl2sql_common/env.py` (`secret`, `env_url`), `setup.sh`, `launch.sh` | `tests/security/test_posture.py`, `tests/common/test_env.py`, `tests/docker/test_setup_script.py` |
| The superuser | No password, refused over the network (unchanged) -- and reached over a database's own socket by `dbprep` alone, whose socket volume is shared with nobody else. | `docker-compose.yml` (`pgsocket`, `storessocket`, `contextsocket`, `vectorsocket`, `mlflowsocket`) | `tests/security/test_posture.py` |
| pg_hba order | The sign-in block first, written by `dbprep` through the server itself -- read with `pg_read_file`, written as a large object exported over the file, checked with `pg_hba_file_rules` before `pg_reload_conf`, put back if it does not parse -- then the transport block, then the file's own rules. The runtime stores refuse their superuser over the network the same way. | `common/nl2sql_ops/hba.py`, `retail.py`, `stores.py`; `docker/entrypoint.sh` | `tests/ops/test_ops_hba.py`, `tests/ops/test_ops_live.py` |
| Role ceilings | The agent's reader 2 min a statement, 16 MB of `work_mem`, a minute idle in a transaction, 60 connections; the snippet reader 30 s and 30; the feedback writer 10 s, 4 MB, 30 s and 20; the role sync 1 min, 4 MB and 10; each store's owner 16 MB and 20; each person `AUTH_USER_STATEMENT_TIMEOUT_MS`, 16 MB, a minute and `AUTH_USER_CONNECTION_LIMIT`. Set where each role is made, and again on every start. | `common/nl2sql_common/roles.py`, applied by `nl2sql_ops`, `ragproc.snippets`, `nl2sql_review.store` and `nl2sql_auth.rolesync` | `tests/common/test_roles.py`, `tests/ops/`, `tests/auth/test_auth_rolesync.py` |
| Health | Every health check verifies the certificate it is answered with, against the stack's CA, under a name every certificate covers (`localhost`). | `proxy/health.sh`, `common/nl2sql_common/health.py` | `tests/proxy/test_proxy_startup.py`, `tests/common/test_health.py`, `tests/docker/test_gui_container.py` |
| Supply chain | Every Python image installs a hash-checked lock (6.2); every base image and stock image is pinned by digest. | each Dockerfile, `docker-compose.yml`, `tools/pin_images.py` | `tests/security/test_pinned_images.py` |

What arch6's row "a role-level timeout and `work_mem` that `reader_role.sql`
never set" called an intention is now set, and held by a test.

## 10. Mapping to the code: what was added and what went

- `common/nl2sql_ops/`: the `dbprep` one-shot, `python -m nl2sql_ops
  prepare | report | snippets`. `settings` (what it prepares, from the
  stack's own names and the secret files), `connect` (a database's socket),
  `retail` (the extensions, the reader, the sign-in schema and role sync, the
  sign-in rules), `stores` (the runtime stores' databases and owners, and
  their transport rules), `passwords`, `hba` (the block rewrite), `report`.
- `common/nl2sql_common/roles.py` (`RoleLimits`, `limit_role`) and
  `health.py`.
- `proxy/`: the one page image -- `Dockerfile`, `nginx.conf`,
  `10-nl2sql-proxy.envsh`, `health.sh`, `pages/`, `shared/`.
- `docker/migrate_store.sh`: a store from before 6.3, into its database.
- `tools/pin_images.py`.
- Gone: the six pages' Dockerfiles, templates and start-up fragments
  (the pages' sources stay where they were), `docker/mlflow-proxy/`,
  `docker/auth_roles.sql` and `docker/ldap_hba.sh`.

## 14.3 The two stores, and the other two

The corrections and completions stores, the staging database and the
snippet store are databases in one server, `nl2sql-stores`. Each has an owner
of its own, which owns its database and nothing else -- none is a superuser,
as each store's owner was of its own server until 6.3. The two whose owners
make a role of their own -- the staging database's (the API's INSERT-only
writer) and the snippets' (the agent's reader) -- have `CREATEROLE`, with
which they make and change only roles they made. pgvector is made in the
three that need it by the superuser, which only the socket reaches.

A store from before 6.3 is moved by `docker/migrate_store.sh`, which
`launch.sh` runs once for each old volume it finds: the old volume mounted
read-only, dumped by a Postgres of its own on a copy, and restored as the
owner into a database with no tables yet -- without the row-level policies
and extensions, which the review service and `dbprep` make again. The
database is marked as moved; a database already in use is refused rather
than merged; the old volume is kept until someone removes it.

## 21. Transport, TLS identities and secrets: what 6.3 adds

### 21.1 The retail database

Its health check asks over TCP, which the entrypoint's own temporary server
-- the one it sets the owner's password through, on the socket only -- does
not answer, so a container is not healthy before Postgres has really
started. Everything `launch.sh` used to do to it with `docker exec` is
`dbprep`'s, which starts only once it is healthy.

### 21.3 Secrets, defaults and limits

- **Every password is generated per installation** into `secrets/`, a file
  each -- the directory `0700`, ignored by git and every build context --
  and moved there, with the same value, from a `.env` written before 6.3.
  Compose mounts each into only the services that read it. A service reads
  `<NAME>_FILE`; the variable itself still works for a service started by
  hand, and the file wins when both are set.
- **The service tokens and a replica's bind password are optional files**,
  empty until asked for (`setup.sh --tokens`) or written.
- **MLflow's store URL is built by its entrypoint** from the password file,
  and handed to the server as `MLFLOW_BACKEND_STORE_URI` -- not on its
  command line, where `ps` showed it.

### 21.4 Decisions

**One image for every page (V6-37).** The six images differed only in their
upstream, their paths and which bundle they served. One image with every
bundle, a page chosen at start, keeps one start-up script, one sign-in
location and one health check to get right. A page's container serves only
its own root, so the review page's files being in the public GUI's
container gives nobody a way to them. The cost is a larger image, pulled
once.

**A one-shot rather than the auth service (V6-41).** The plan put the
database preparation in the auth service, which already owns the sign-in
roles. It is a one-shot instead: preparing a database takes its superuser,
and the auth service faces the network for as long as the stack runs; the
one-shot holds every socket for the seconds before each start, as an
unprivileged account with no capabilities, and exits. The scripts relay
what it reports and run no SQL of their own.

**The runtime stores together, the RAG stores apart (V6-40).** What
accumulates from use -- verdicts, fixes, snippets -- is in one server with
an owner per database; what ships inside a published image stays in its
own. A move of what people typed, not a rebuild, so it is a migration with
a marker rather than a reload.

Still to do from phase 5: provenance in publishing (V6-42); and the rest of
V6-40 and V6-41 -- the RAG stores' own preparation and the scripts'
orchestration in Python.
