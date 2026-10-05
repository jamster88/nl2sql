# The shared Python package

`nl2sql-common` (6.2.0): what the stack's Python services share, installed
into each image as a package (`pip install --no-deps`) rather than copied
into it, so `pip show nl2sql-common` in any container says which version it
carries. Two import packages:

| Package | What | Used by |
| --- | --- | --- |
| `nl2sql_identity` | Who is calling. `tokens` is the session format -- an Ed25519-signed JWT with a fixed header, issuer and audience; `guard` the FastAPI dependency every service puts in front of its routers -- a session or the service's own token, the cookie's origin check, the roles and the session's standing re-read from Postgres at most once a minute (`postgres`), who counts as an operator; `pki` the development CA that gives each server its own certificate and each key to the account its service runs as | the API, the console, the review service, the auth service |
| `nl2sql_common` | `env` reads settings (an empty variable is unset; `NAME_FILE` for a secret); `errors` the families a service catches -- `DATABASE_ERRORS`, `MODEL_ERRORS`, `NETWORK_ERRORS`, `PARSE_ERRORS` -- and its own `Unavailable`, `Refused`, `Invalid`; `envelope` the one error and health shape every service answers with; `urls` a URL with its password hidden; `values` a value as JSON; `vectors` a vector as pgvector reads one; `privileges` dropping root once a process has what it writes; `attribution` the person's name in a transaction's `application_name` | every Python image, the directory's included |

It depends on nothing beyond what each image already installs from its own
hash-checked lock (`requirements.lock`): an import a package does not have
-- SQLAlchemy in the directory's image -- is left out of the families
rather than required.

## Since when

- 6.0: `nl2sql_identity`, in `auth/`, copied into three images.
- 6.2: moved here with `nl2sql_common` (V6-20, V6-66), and installed;
  revocation and the named service token in the guard (V6-61, V6-62); the
  operator test and the error families (V6-32, V6-23); `privileges` and
  `pki`'s owners for the unprivileged images (V6-31).

## Tests

`tests/common/` for `nl2sql_common`; `tests/auth/test_identity_*.py` for
`nl2sql_identity`; `tests/security/` for what the two promise together --
every broad `except` explained, every route guarded, every image
unprivileged.
