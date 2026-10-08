# The shared Python package

`nl2sql-common` (7.0.0): what the stack's Python services share, installed
into each image as a package (`pip install --no-deps`) rather than copied
into it, so `pip show nl2sql-common` in any container says which version it
carries. Three import packages:

| Package | What | Used by |
| --- | --- | --- |
| `nl2sql_identity` | Who is calling. `tokens` is the session format -- an Ed25519-signed JWT with a fixed header, issuer and audience; `guard` the FastAPI dependency every service puts in front of its routers -- a session or the service's own token, the cookie's origin check, the roles and the session's standing re-read from Postgres at most once a minute (`postgres`), who counts as an operator; `pki` the development CA that gives each server its own certificate and each key to the account its service runs as | the API, the console, the review service, the auth service |
| `nl2sql_common` | `env` reads settings (an empty variable is unset; `secret` reads `NAME` or the file `NAME_FILE` names, and `env_url` a URL with its password from the file `<NAME without _URL>_PASSWORD_FILE` names, 6.3); `errors` the families a service catches -- `DATABASE_ERRORS`, `MODEL_ERRORS`, `NETWORK_ERRORS`, `PARSE_ERRORS` -- and its own `Unavailable`, `Refused`, `Invalid`; `envelope` the one error and health shape every service answers with; `urls` a URL with its password hidden, or given (`with_password`); `values` a value as JSON; `vectors` a vector as pgvector reads one; `privileges` dropping root once a process has what it writes; `attribution` the person's name in a transaction's `application_name`; `roles` the ceilings on a role a service connects as -- statement timeout, `work_mem`, idle-in-transaction timeout, connection limit -- and the values for each (6.3); `health` the health check of the four Python services, `python -m nl2sql_common.health API`, which verifies the certificate it is answered with (6.3) | every Python image, the directory's included |
| `nl2sql_ops` | The `dbprep` one-shot (6.3): `python -m nl2sql_ops prepare` makes what every database needs -- the retail database's extensions, the agent's reader, the sign-in schema, the role sync and the sign-in rules in `pg_hba.conf`; the runtime stores' databases and owners; every login's password from its file -- over each database's own socket, as its superuser. `report` says what each holds and `snippets` whether the snippets are behind their document, for the scripts. `settings`, `connect`, `retail`, `stores`, `passwords`, `hba`, `report` | the `dbprep` service, from the agent's image |

It depends on nothing beyond what each image already installs from its own
hash-checked lock (`requirements.lock`): an import a package does not have
-- SQLAlchemy in the directory's image -- is left out of the families
rather than required.

## dbprep's settings

`python -m nl2sql_ops` reads the stack's own names, as compose gives them to
the `dbprep` service, so `.env` says each once. A password is read from the
file `<NAME>_FILE` names -- compose mounts each from `secrets/` -- or, for a
run by hand, from the variable itself.

| Setting | Default | What |
| --- | --- | --- |
| `POSTGRES_DB` | `nl2sql_retail` | The retail database |
| `POSTGRES_USER` | `nl2sql` | Its owner, who owns what the reader is granted |
| `POSTGRES_READER_USER` | `nl2sql_reader` | The agent's reader, made here |
| `POSTGRES_READER_PASSWORD` | -- | Its password, from `secrets/postgres_reader_password` |
| `AUTH_ENABLED` | `true` | Sign-in: its schema, its role and its `pg_hba.conf` lines. Only `false`, by name, takes them out |
| `AUTH_ROLESYNC_USER` | `nl2sql_rolesync` | The auth service's role in the retail database |
| `AUTH_ROLESYNC_PASSWORD` | -- | Its password, from `secrets/auth_rolesync_password`; required with sign-in on |
| `LDAP_BASE_DN` | `dc=nl2sql,dc=local` | Where the sign-in rule finds a person in the directory |
| `NL2SQL_LDAP_HOST` | `nl2sql-ldap` | The directory, for the sign-in rule; the stack's own, so compose sets none of these three |
| `NL2SQL_LDAP_PORT` | `389` | |
| `NL2SQL_LDAP_TLS` | `starttls` | `starttls`, `ldaps` or `none` |
| `FEEDBACK_DB_USER`, `FEEDBACK_DB_NAME` | `feedback`, `nl2sql_feedback` | The staging database in the runtime stores, and its owner |
| `CORRECTIONS_DB_USER`, `CORRECTIONS_DB_NAME` | `corrections`, `nl2sql_corrections` | The corrections store |
| `COMPLETIONS_DB_USER`, `COMPLETIONS_DB_NAME` | `completions`, `nl2sql_completions` | The completions store |
| `SNIPPETS_DB_USER`, `SNIPPETS_DB_NAME` | `snippets`, `nl2sql_snippets` | The snippet store |
| `FEEDBACK_DB_PASSWORD`, `CORRECTIONS_DB_PASSWORD`, `COMPLETIONS_DB_PASSWORD`, `SNIPPETS_DB_PASSWORD` | -- | Each owner's, from its file in `secrets/` |
| `CONTEXT_DB_USER`, `CONTEXT_DB_NAME`, `CONTEXT_DB_PASSWORD` | `ragproc`, `nl2sql_chunks`, -- | The context store's login, whose password is set from its file |
| `VECTOR_DB_USER`, `VECTOR_DB_NAME`, `VECTOR_DB_PASSWORD` | `ragproc`, `nl2sql_vectors`, -- | The vector store's |
| `MLFLOW_DB_USER`, `MLFLOW_DB_NAME`, `MLFLOW_DB_PASSWORD` | `mlflow`, `mlflow`, -- | MLflow's store's, when it is running |
| `NL2SQL_SOCKETS_DIR` | `/run/nl2sql/sockets` | Where each database's socket is mounted, one directory each: `retail`, `stores`, `context`, `vector`, `mlflow` |
| `NL2SQL_GOLDEN_DOCUMENT` | `/app/context_questions/translated_questions.md` | For `report`, which says whether the stores hold the document's pairs |
| `NL2SQL_SNIPPETS_DOCUMENT` | `/app/context_questions/sql_snippets.md` | For `snippets`, which says whether the store is behind its document |

## Since when

- 6.0: `nl2sql_identity`, in `auth/`, copied into three images.
- 6.2: moved here with `nl2sql_common` (V6-20, V6-66), and installed;
  revocation and the named service token in the guard (V6-61, V6-62); the
  operator test and the error families (V6-32, V6-23); `privileges` and
  `pki`'s owners for the unprivileged images (V6-31).
- 6.3: `secret` and `env_url` read every password from a file (V6-38);
  `roles` (V6-39); `health` (V6-37); `nl2sql_ops`, which took the database
  preparation out of `setup.sh` and `launch.sh` (V6-41).

## Tests

`tests/common/` for `nl2sql_common`; `tests/ops/` for `nl2sql_ops`, five
of them live (`--run-docker`); `tests/auth/test_identity_*.py` for
`nl2sql_identity`; `tests/security/` for what the two promise together --
every broad `except` explained, every route guarded, every image
unprivileged.
