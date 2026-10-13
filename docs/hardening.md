# What each container may use

*Part of the [nl2sql documentation](../README.md#documentation).*

Since 6.3 every service in [`docker-compose.yml`](../docker-compose.yml) starts
from one block, `x-hardened`: its filesystem is **read-only** but for a
`/tmp` in memory and the volumes it is given; it **drops every Linux
capability**; and it **cannot gain a privilege** (`no-new-privileges`), so
nothing it runs -- a setuid binary included -- can become more than the
account it started as. Each has a ceiling on its memory and its processes,
so one that runs away is stopped by Docker before it takes the machine with
it:

| Container | Memory | Processes | Capabilities given back |
|---|---|---|---|
| `nl2sql-postgres` | 2 GB | 512 | the five a Postgres entrypoint needs to hand its volume to `postgres` and drop to it: `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID` |
| `nl2sql-stores` | 1 GB | 512 | the same five |
| `nl2sql-vectordb`, `nl2sql-mlflowdb` | 1 GB | 256 | the same five |
| `nl2sql-chunkdb` | 512 MB | 256 | the same five |
| `api`, `agent` | 2 GB | 512 | none |
| `nl2sql-review` | 2 GB | 256 | `SETUID`, `SETGID`: it writes the checkout's documents as the person who owns them |
| `nl2sql-console` | 1 GB | 256 | none |
| `nl2sql-mlflow` | 3 GB | 512 | the five, to take its volume and drop |
| `nl2sql-ldap` | 1.5 GB | 128 | the five; and a second in-memory directory, where `slapd` keeps its socket |
| `nl2sql-auth` | 512 MB | 128 | the five |
| every page, MLflow's front door, `apitest` | 128 MB | 64 | none |
| `pki` | 256 MB | 64 | `CHOWN`, `DAC_OVERRIDE`, `FOWNER`: the one service that runs as root, to give each key to the account that reads it |
| `dbprep` | 256 MB | 64 | none: it runs as the agent image's account, 10001 |
| `storesmigrate` | 1 GB | 128 | none: it runs as the stores' `postgres` account, 999 |
| `desktop` | 256 MB | 64 | `SETUID`, `SETGID` |

The `api` container's 512 processes are sized for the defaults: one slot at
the model host and two questions at a time. Since 7.0 every run of a
question is a thread, with threads of its own for the retrievers, and
threads count against this ceiling. So a deployer who raises
`OLLAMA_PARALLEL_CALLS` -- the runs of a question that go at once -- or
`API_MAX_CONCURRENCY` well past the defaults should raise the `api`
service's `pids_limit` with them, as they raise the host's own memory for
its resident models ([the host
gate](../agent/README.md#asking-it-several-ways-arch7)).

`tests/security/test_posture.py` holds every service to the block, and to
giving back nothing beyond those five; the acceptance tier checks, in the
running stack, that no process in any container is root and that no
container can write its own image.

**Nor is a health check root's** (7.0). Docker runs a container's health
check as the user the container was started as. Nine services start as
root, to hand what they write to an account and then drop to it -- the five
Postgres servers, the directory, the auth and review services and MLflow --
so until 7.0 each one's check was root's, every ten seconds, long after the
service itself had dropped. Each is now started through `setpriv`, as the
account its service runs as:

| Container | Its health check runs as |
|---|---|
| `nl2sql-postgres`, `nl2sql-stores`, `nl2sql-vectordb`, `nl2sql-chunkdb`, `nl2sql-mlflowdb` | `postgres` |
| `nl2sql-ldap` | `ldap`, which the directory's own socket knows as its root |
| `nl2sql-auth`, `nl2sql-review` | `nl2sql`, which owns each one's certificate |
| `nl2sql-mlflow` | `mlflow` |

`setpriv` becomes the account -- its user, its group and no other group --
and then *is* the check, so `pg_isready`, or the interpreter, never runs as
root. What is left is the instant `setpriv` takes to stop being root: Docker
starts it as root, and nothing in a compose file can change who Docker
starts. Each check is a list rather than a line for a shell, because a shell
in front of a check stays root for as long as the check runs. Watched for
five minutes, as fast as Docker answers: a 6.3 stack showed a root process
43 times in 14,365 looks; 7.0, none in 14,807.

The two checks this project wrote -- `python -m nl2sql_common.health` and
`python3 -m nl2sql_ldap health` -- also give root up themselves, before they
read a setting or open a connection, because an image cannot know who it
will be started as: outside compose, under `docker run` or another
orchestrator's probe, the check is what does it. One that cannot -- started
as root in a container without `SETUID` -- fails saying so rather than ask
as root; started as anyone else, it runs as it was given. An image put in a
service's place (`STORES_IMAGE`) has to carry util-linux's `setpriv`, as
every Debian image does. Alpine's is BusyBox's, which cannot change who a
process is, until the `setpriv` package is installed -- which is what the
directory's image does.

`tests/security/test_unprivileged.py` holds every such check to that
beginning, and a health check added to any service is a failing test until
it says who it runs as. The acceptance tier watches every container of the
running stack for thirty seconds for a process of root's that is anything
but that instant, and runs each check's drop to see who comes out.

**No password or token is in a container's environment.** Each is a file in
`secrets/`, which `setup.sh` generates -- the directory `0700`, so only you
can list it -- and compose mounts at `/run/secrets/<name>` in only the
services that read it; each service reads `<NAME>_FILE`, and builds the URL
it connects with from the file. `docker inspect` shows a container's
environment to anyone who can run it, and it now shows no secret:

| File | What |
|---|---|
| `ldap_admin_password` | the first person's, `admin` |
| `ldap_service_password`, `auth_rolesync_password` | the auth service's, in the directory and in the retail database |
| `postgres_password`, `postgres_reader_password` | the retail database's owner, and the agent's reader |
| `context_db_password`, `vector_db_password` | the two RAG stores |
| `stores_db_password` | the runtime stores' superuser, which only `dbprep` uses, over its socket |
| `feedback_db_password`, `feedback_writer_password`, `corrections_db_password`, `completions_db_password`, `snippets_db_password`, `snippets_reader_password` | each runtime store's owner, and the API's and the agent's roles in them |
| `mlflow_db_password` | MLflow's store |
| `api_token`, `review_token`, `console_token` | the service tokens, empty unless `setup.sh --tokens` made them |
| `ldap_upstream_bind_password` | a replica directory's bind password, empty for a standalone one |

**No database's superuser is reachable over the network.** Each Postgres
container's socket is a volume of its own, shared with nobody but the
`dbprep` one-shot (and, for the runtime stores, `storesmigrate`): over its
own socket Postgres trusts whoever connects, so holding that volume is
holding the database. `dbprep` -- `python -m nl2sql_ops prepare`, in the
agent's image -- runs before anything else on every start: it makes the
roles and extensions each database needs, sets each login's password from
its file, and writes the retail database's sign-in rules into `pg_hba.conf`,
so neither `setup.sh` nor `launch.sh` runs SQL of its own.

**Every role a service connects as has a ceiling** (V6-39): how long a
statement may run, how much memory a sort may take, how long a transaction
may sit idle, and how many connections the role may hold at once.

| Role | Statement | `work_mem` | Idle in a transaction | Connections |
|---|---|---|---|---|
| `nl2sql_reader`, the agent's | 2 min | 16 MB | 1 min | 60 |
| a person's own, made by the auth service | `AUTH_USER_STATEMENT_TIMEOUT_MS`, 1 min | 16 MB | 1 min | `AUTH_USER_CONNECTION_LIMIT`, 5 |
| `nl2sql_rolesync`, the auth service's | 1 min | 4 MB | 1 min | 10 |
| `snippets_reader`, the agent's in the snippet store | 30 s | 16 MB | 1 min | 30 |
| `nl2sql_feedback_writer`, the API's | 10 s | 4 MB | 30 s | 20 |
| each runtime store's owner | -- | 16 MB | -- | 20 |

They are applied where each role is made -- by `dbprep`, by the snippet
loader, by the review service and by the auth service --
from one place, [`common/nl2sql_common/roles.py`](../common/nl2sql_common/roles.py).
