# Multi-Agent NL2SQL Architecture, v6.2

**Status:** arch6 plus what 6.2.0 changed in it. `Multi-Agent_NL2SQL_arch6.md`
is the architecture as built at 6.1.0, and all of it stands: the pipeline,
its agents and their state are untouched by this release. This document
(2026-10-04) records what 6.2 changed beneath them -- the second adversarial
review's phases 3 and 4 -- section by section of arch6, and supersedes arch6
where the two disagree. Section numbers below are arch6's.

What changed, in one line each:

| Area | Before (arch6) | Now (6.2) | arch6 |
| --- | --- | --- | --- |
| Shared code | `nl2sql_identity` in `auth/`, copied into three images | `common/`: `nl2sql_identity` and `nl2sql_common`, one package installed into every Python image at the release's version | 10, 20.5 |
| Routes | each a closure in `create_app`, guarded by a dependency added by hand | on routers, each carrying its guard; the routes open by design on one router of their own | 8 |
| Sessions | valid until they expired | ended by signing out, a password change or set, a lock or a removal, for every service within a minute | 20.3, 20.7 |
| Service tokens | an identity with every role, named by a header | named, recorded as `token:<name>`, holding only the roles they are given | 20.2, 20.7 |
| Accounts | every application image but the directory's ran as root | nothing runs as root but the one-shot `pki`; each key is its service's account's | 21.2, 21.3 |
| The review service's writes | the loaders run as scripts, passwords on their command line | the loaders called in-process under a lock; written as the checkout's owner | 14, 19 |
| What a failure says | the driver's words to anyone | its words to an administrator; which part failed to anyone else | 8 |
| Attribution | `current_user` inside the transaction, and nothing else | the transaction's `application_name` names the person too | 8, 20.7 |

---

## 8. Security blueprint: the rows that changed

| Layer | Rule | Enforced in | Held by |
|---|---|---|---|
| Who is asking | A session signed with the auth service's Ed25519 key, verified by every service with the public half; header compared whole, issuer, audience and expiry checked; **its holder's groups and the session's own standing re-read from Postgres at most once a minute per session**: a session signed out, or issued before a cut-off its holder's password change or set, lock or removal set, is refused (`session_revoked`). On by default in every service's code. | `common/nl2sql_identity/`, `docker/auth_roles.sql` | `tests/auth/test_identity_guard.py`, `tests/auth/test_auth_revocation.py`, `tests/auth/test_auth_live.py` |
| Every route | **Every route is on a router whose dependencies carry a `Guard`**; the routes open by design -- what a service is, its health and readiness, signing in -- are on one router of their own, and nothing is added to an application past its routers. | each service's `routes.py` | `tests/security/test_routes_guarded.py` |
| Service tokens | A static token is a caller named by its `_TOKEN_NAME`, holding its `_TOKEN_ROLES` and no others; what it does is recorded as `token:<name>`. A header never names who did something; only an open service with no token records the name it is given. | `common/nl2sql_identity/guard.py`, the settings modules, `review/nl2sql_review/routes.py` | `tests/auth/test_identity_guard.py`, `tests/review/test_signin.py` |
| Operator detail | A failure's own words -- a driver's error, a host and port, the readiness detail, configuration warnings -- are an administrator's (`nl2sql_admins`), or everyone's with `API_DEBUG_DETAIL`. Anyone else is told which part failed. | `common/nl2sql_identity/guard.py` (`operator`), `nl2sql_common/envelope.py`, `agent/nl2sql_agent/api/translate.py`, `graph.py` | `tests/api/test_signin.py`, `tests/auth/test_identity_guard.py` |
| Accounts | No container runs as root but the one-shot `pki`, which writes every other service's key into that service's volume and gives it to that service's account. | each Dockerfile; `privileges.py`; `docker/mlflow/entrypoint.sh`; `desktop/copy-out.sh` | `tests/security/test_unprivileged.py` |
| Supply chain | Every Python image installs its dependencies from a lock with a hash for every file. | each `requirements.lock` | `tests/security/test_supply_chain.py` |
| Row-level security | Unchanged: the mechanism has a caller, no table has a policy. A person's statement now also sets `application_name` to `nl2sql:<service>:<person>` for its transaction, so `pg_stat_activity` and the log name them -- attribution, still not isolation. | `agent/nl2sql_agent/database.py`, the console, the review validators | `tests/agent/test_database_safety.py`, `tests/agent/test_least_privilege_live.py` |

## 10. Mapping to the code: what moved

- `auth/nl2sql_identity/` is `common/nl2sql_identity/`, installed with
  `pip install --no-deps` from `common/` by every Python image, as
  `nl2sql-common` at the release's version (`pip show nl2sql-common`).
- `common/nl2sql_common/`: `env`, `errors` (the families a service catches,
  and its own `Unavailable`, `Refused`, `Invalid`), `envelope`, `urls`,
  `values`, `vectors`, `privileges`, `attribution`.
- `agent/nl2sql_agent/api/routes.py`, `console/routes.py`,
  `review/nl2sql_review/routes.py`, `auth/nl2sql_auth/routes.py`: each
  service's routers and the context they read from.
- `auth/nl2sql_auth/revocation.py` and `proxies.py`.
- `rag/ragproc/loaders.py`: steps 5, 6 and 7 as functions.
- `web/`: what every page shares, as source each page compiles.

## 14 and 19. The review service's writes

A promotion, a withdrawal and a snippet write each run, from reading the
document to the last load, holding the write lock: a lock in the process,
and an advisory lock on the documents' directory between processes (not on
the document, which an atomic write replaces). The loads are
`ragproc.loaders`, called in the process -- the code the scripts run, with
each store's URL an argument rather than something on a command line. What
a load does wrong is reported beside the written pair, as the script's exit
code was. A promotion needs no reload of the agent: the pairs, snippets and
fixes it writes are read from their stores on every question; the agent's
process-lifetime caches -- the literal catalog, the label map and calendar,
the foreign keys, the knowledge collections -- describe what an operator
changes, and `POST /v1/admin/reload` reads them again.

Fix ids come from a sequence per store, so an id is never given to a second
fix.

The service starts as root, becomes the owner of the mounted
`context_questions/` -- the person whose checkout it is -- and writes as
them, keeping the `nl2sql` group to read the key the pki service gave it.

## 20. Sign-in: the decisions 6.2 adds

### 20.8 Revocation (V6-71's option (a))

Rejected: server-side sessions -- every service would need the store, and
the token would stop being self-verifying for the three that only read it;
and accepting eight hours of exposure, which 6.1 did.

Chosen: two lists in the retail database, beside the roles the guard
already asks about. A signed-out session's `jti`, kept until it would have
expired; and a person's cut-off -- every session issued before it is
refused -- set by a password change (the browser that changed it gets a new
session, signed at the cut-off), an administrator's set, a removal, and the
directory's own lockout, recorded by the role sync at the directory's lock
time. Whole seconds, as a token's `iat` is: the cut-off is the second after
the change, and a sign-in within that second is signed at the cut-off.

The lists belong to a `NOLOGIN` role; the role sync's login may write them;
the reader, and so the three services' rechecks, may only call
`nl2sql_auth.session_revoked(user, jti, issued_at)`, a `SECURITY DEFINER`
function with a fixed `search_path` that answers yes or no about one
session -- the reader cannot enumerate who signed out, and neither can any
SQL it runs. A database without the function fails the recheck, which the
guard answers `503`: a session that cannot be checked is not taken on
trust.

The guard caches per session, not per person, for its minute; the auth
service forgets its own answers when it writes, and an administrator's
reload makes the API forget. A revoked session is refused everywhere within
a minute -- the limit [`SECURITY.md`](../SECURITY.md) states.

A lockout ends the sessions from before it. That lets someone who can lock
an account -- five wrong passwords -- sign its holder out, which the lockout
already did to new sign-ins; the per-name throttle slows it.

### 20.9 Who a token is

A service token was every role its service grants, and the name a header
claimed. It is now a caller of its own: its `_TOKEN_NAME`, recorded as
`token:<name>` so it is never mistaken for a person who shares it, and its
`_TOKEN_ROLES`. The defaults are the least the stack's own uses need: the
API's asks (`nl2sql_users`), the console's does what the console does, and
the review service's reviews; with sign-in off -- where the pages' proxies
send the review token for everyone -- it holds both of that service's roles.

### 20.10 Who sees a failure's words

`Guard.operator`: an administrator, the open deployment (sign-in off, no
token, where every caller is everything), or anyone when the deployment
says so. `/readyz` asks for no credential, so it is the readiness every
service gives anyone, trimmed to each dependency's state.

## 21. The accounts each service runs as

| Image | Account | How |
| --- | --- | --- |
| agent (API, console, CLI) | `nl2sql` (10001) | `USER` |
| the six page proxies | nginx (101) | `USER`; the `user` directive removed, the pid in `/tmp`, what nginx writes at start made the account's |
| the smoke test | `nobody` | `USER` |
| auth | `nl2sql` (10001) | starts as root, gives the account its two key directories and what an older release wrote there, then drops |
| review | the owner of the mounted checkout, with `nl2sql`'s group | starts as root and becomes that owner; `nl2sql` when the directory is root's |
| directory | `ldap` | unchanged since 6.0.1 |
| MLflow | `mlflow` (10002) | its entrypoint gives the account the artifact volume and drops with `setpriv` |
| the desktop jar's copier | the owner of `desktop/target` | `su-exec` |
| the four databases' images | `postgres` | their entrypoints, as before |
| `pki` (one-shot) | root | compose's `user: "0:0"`: it writes every other service's key and hands it to that service's account, 0640, on every run -- a 6.1 volume's root-owned key is moved over on the first start |

Still not done, and next in the plan: a read-only root, dropped
capabilities and memory limits on every container (V6-34).
