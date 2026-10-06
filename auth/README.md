# Sign-in

Who is asking, decided once and believed everywhere. Three parts, each in
this directory or beside it:

| Part | Where | What it does |
| --- | --- | --- |
| the directory | [`ldap/`](../ldap/README.md), container `nl2sql-ldap` | people, their passwords and the four groups; standalone, or a read-only replica of another directory |
| the auth service | `auth/nl2sql_auth`, container `nl2sql-auth`, port 8446 | signs people in, issues the session, keeps the database's roles in step with the directory, serves the directory page's API |
| the directory page | `auth/gui`, served by the proxy image's `directory` page (6.3), container `nl2sql-directory-gui`, port 8084 | people and groups, for `nl2sql-admins`; standalone only |

and one package every other service imports, `nl2sql_identity`: the
session format and the guard the API, the SQL console and the review
service put in front of their routes. It lives in
[`common/`](../common/README.md) since 6.2.0, beside `nl2sql_common`, and
each image installs it as a package.

Sign-in is on by default. `AUTH_ENABLED=false` in `.env` -- or
`./launch.sh --no-auth` / `./start.sh --no-auth` for one run -- turns it off
everywhere, and the stack behaves as it did before: each service's own
static token, or none.

## How a sign-in works

The password is checked by **the retail database**, not by this service.
The `dbprep` one-shot writes two lines at the top of the database's
`pg_hba.conf` on every start (`common/nl2sql_ops/retail.py`, through the
server itself, checked by `pg_hba_file_rules` before it is reloaded; until
6.3, `docker/ldap_hba.sh` from `launch.sh`): the roles that keep their own passwords -- the
agent's reader and the role sync -- by `scram-sha-256`, and every member of
`nl2sql_ldap` by `ldap`, which binds to the directory as
`uid=<name>,ou=people,<base>` over StartTLS. Signing in is this service
opening a connection to the database as that person, with that password:
if Postgres lets them in, they are who they say.

Both lines are `hostssl` (6.1). pg_hba's `ldap` method is clear-text
password authentication -- the client hands the database the password, and
the database binds to the directory with it -- so it is accepted only over
an encrypted connection, and the database refuses anything over the
network without one. This service connects with `sslmode=verify-full`
against the database's own certificate (`AUTH_DB_SSLROOTCERT`), so a
password is never handed to anything that is not the database.

That is what makes the database the authority rather than a bystander. A
person the directory does not know, or knows with another password, cannot
connect -- to anything, by any route -- whatever a session says.

The answer is a session: a JWT signed with this service's Ed25519 key
(`EdDSA`), naming the person, their display name and the groups Postgres
says they hold, for `AUTH_SESSION_HOURS` (8). Only this service holds the
private key (`authdata` volume); the public half is written to the
`authkeys` volume, which the API, the console and the review service mount
read-only and check every session against. Nothing else is shared: they
never call this service to ask who someone is.

| Client | Signs in at | Holds the session as |
| --- | --- | --- |
| a web interface | `POST /auth/login`, through its own nginx | a cookie, `nl2sql_session`: `HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS |
| the desktop client, a script | `POST /auth/token`, here | the token in the answer, sent as `Authorization: Bearer` |
| an MLflow client | nowhere: `MLFLOW_TRACKING_USERNAME` and `_PASSWORD` | Basic credentials, checked through `/auth/verify` |

A write that rides on a cookie must come from the interface's own pages:
`Sec-Fetch-Site`, or else `Origin`/`Referer` against the host the
interface's nginx reports in `X-Forwarded-Host`. A bearer token is not
something another site can make a browser send, so it needs no such check.

### What a session is worth

The groups in a session are a starting point. Each service asks Postgres
again (`pg_has_role`), at most once a minute per person, so someone removed
from a group -- or from the directory -- loses what it gave them within a
minute, not when their session ends: a person who no longer exists is told
`401 account_removed`.

A session can also be ended before it expires (6.2). The same question asks
Postgres whether this session is still its holder's, and one that is not is
refused with `401 session_revoked`:

| What happened | What is ended | How |
| --- | --- | --- |
| Signing out (`POST /auth/logout`, the pages' and the desktop client's sign-out) | that session, wherever a copy of it is | its `jti`, kept until it would have expired anyway |
| A password changed (`POST /auth/password`) | every session signed in before it -- the browser that changed it gets a new one | a cut-off for the person, a second after the change |
| A password set by an administrator | every session that person signed in before it | the same |
| An account the directory locked after too many wrong passwords | every session from before the lock | the role sync records the directory's own lock time |
| Someone removed | every session they had | a cut-off, so a person later re-created under the same name does not get them back |

The lists live in the retail database, in a schema the sync's login writes
and nobody reads: the reader -- and so the services' rechecks -- may only
call `nl2sql_auth.session_revoked(user, jti, issued_at)`, which answers yes
or no about one session (made by the `dbprep` one-shot,
`nl2sql_ops.retail.ensure_signin`). This service forgets its
own answers at once; every other service within its minute, or at once on
an administrator's `POST /v1/admin/reload` to the API. A token counts whole
seconds, so a sign-in in the second a password changed is signed at the
cut-off rather than refused by it. Without `AUTH_ROLESYNC_DB_URL` nothing can
be revoked, and signing out forgets only the browser's cookie -- which the
start-up says.

And what they do runs as them. The API, the console and the review service
connect as the agent's read-only reader and, for each question or
statement, `SET LOCAL ROLE` to the person -- which the reader may do, and do
only that: it is granted each person's role `WITH INHERIT FALSE, SET TRUE`,
so it can become them for a transaction without ever holding what they
hold. The database's own grants and row-level rules then see a person, not
a service -- inside that transaction. The connection is still the reader's,
so `session_user` and the connection log say the reader; since 6.2 the
transaction's `application_name` says whom it was for --
`nl2sql:agent:alice`, `nl2sql:console:alice`, `nl2sql:review:alice` -- which is
what `pg_stat_activity` shows while it runs and `%a` puts in the database's
own log.

### The four groups

| Directory group | Database role | Lets in |
| --- | --- | --- |
| `nl2sql-users` | `nl2sql_users` | the web interface, the desktop client, the API |
| `nl2sql-reviewers` | `nl2sql_reviewers` | the review interface, the SQL console, MLflow |
| `nl2sql-curators` | `nl2sql_curators` | the curation interface, the SQL console |
| `nl2sql-admins` | `nl2sql_admins` | the directory page, MLflow |

All four can ask questions: the three narrower ones are members of
`nl2sql_users` (`WITH INHERIT TRUE, SET FALSE` -- what it may read, never who
it is). The `dbprep` one-shot makes them, and what each may `SELECT`, on
every start (`docker/auth_roles.sql` until 6.3, which `launch.sh` ran). Which groups a service lets in is that service's setting:
`REVIEW_REVIEWER_ROLES`, `REVIEW_CURATOR_ROLES`, `CONSOLE_ALLOWED_ROLES`,
`MLFLOW_ALLOWED_ROLES`.

### The role sync

Every `AUTH_ROLE_SYNC_INTERVAL` seconds (30), and straight after any change
made on the directory page, this service makes the database's people match
the directory's, logged in as `nl2sql_rolesync` -- a role that can create
roles and administer the five sign-in roles and nothing else:

- a person in the directory becomes a `LOGIN` role of the same name, with
  `CONNECTION LIMIT` `AUTH_USER_CONNECTION_LIMIT` (5), read-only
  transactions and a `statement_timeout` of `AUTH_USER_STATEMENT_TIMEOUT_MS`
  (60 000), commented with their name and address;
- their groups become role memberships, added and taken away;
- someone gone from the directory has their role dropped -- or, if it owns
  anything, made unable to log in.

The last result is on `/readyz` (`role_sync`), and on the directory page.

### Connecting to the database directly

A person's role is a real login: they can connect to the retail database
with `psql` or a BI tool, as themselves, with their directory password --
read only, a minute per statement, five connections at a time. What that
costs, and what 6.1 does about it:

- **Their password crosses to the database as it is.** pg_hba's `ldap`
  method needs it in hand to bind to the directory, so the client sends it
  inside the connection. Before 6.1 the database had no TLS and the rule
  admitted unencrypted connections: the password crossed the network in
  clear. Now the database serves TLS with a certificate of its own and the
  rule is `hostssl`: nothing else is accepted.
- **Encrypted is not verified.** `sslmode=require` keeps the password from
  being read on the way and does not check that the server is the
  database. Use `sslmode=verify-full` with the database's certificate
  (`docker compose cp postgres:/etc/nl2sql/pg-tls/server.crt
  ./nl2sql-postgres.crt`), connecting by a name it covers
  (`POSTGRES_TLS_HOSTNAMES`) -- this password is the person's password for
  every page.
- **It is this machine's by default.** The port is published on
  `127.0.0.1` unless `DB_BIND_ADDRESS` says otherwise; opening it is what
  makes the two points above matter, and `launch.sh` warns when it is.

[`USAGE_GUIDE.md`](../USAGE_GUIDE.md#connecting-to-the-retail-database-directly)
has the commands.

## MLflow's front door

MLflow has no login of its own. The proxy image's `mlflow` page
([`proxy/README.md`](../proxy/README.md)) is nginx in front of
it, over HTTPS, asking `GET /auth/verify` about every request (nginx's
`auth_request`): `204` lets it through, `401` sends a browser to sign in
(`/auth/login`, a plain HTML form served here) and answers an API client
`401`, and `403` is someone signed in who is not in `MLFLOW_ALLOWED_ROLES`
(`nl2sql_reviewers` and `nl2sql_admins`). A client's Basic credentials are
checked by signing in, and the answer kept for five minutes so a
benchmark's thousand requests are not a thousand sign-ins. The agent itself
traces to MLflow directly, on the stack's network.

## Routes

| Route | Who | |
| --- | --- | --- |
| `GET /healthz` | anyone | the process is up |
| `GET /readyz` | anyone | the directory, the database, the role sync, and for a replica its last copy |
| `GET /auth/meta` | anyone | what a sign-in form needs: the mode, the session length, the shortest password |
| `POST /auth/login` | anyone | sign a browser in: `{"username", "password"}` in, the session as a cookie |
| `POST /auth/token` | anyone | sign a client in: the same in, the session and its token out |
| `POST /auth/logout` | anyone | sign out: the session presented -- the bearer, else the cookie -- is ended everywhere, and the cookie forgotten |
| `GET /auth/session` | signed in | who, with the roles Postgres says they hold now |
| `POST /auth/password` | a person | change your own password: `{"current", "new"}` (standalone) |
| `GET /auth/verify` | a proxy | may this request through? `?role=` for groups other than MLflow's |
| `GET /auth/login` | anyone | the HTML sign-in form a proxy sends a browser to |
| `GET /directory/v1/meta` | `nl2sql_admins` | the mode, the groups, the last role sync |
| `GET`, `POST /directory/v1/people` | `nl2sql_admins` | everyone; add someone |
| `GET`, `PATCH`, `DELETE /directory/v1/people/{uid}` | `nl2sql_admins` | one person: read, change, remove |
| `POST /directory/v1/people/{uid}/password` | `nl2sql_admins` | set a password |
| `POST /directory/v1/people/{uid}/unlock` | `nl2sql_admins` | clear a lockout |
| `GET /directory/v1/groups` | `nl2sql_admins` | the groups and who is in them |
| `POST /directory/v1/import` | `nl2sql_admins` | a CSV or LDIF, as on first start |
| `POST /directory/v1/sync` | `nl2sql_admins` | run the role sync now |

Beside a replica every `/directory/` route answers `404 replica_read_only`
and a password change `409`. An administrator cannot remove themselves, or
take themselves out of `nl2sql-admins`: there would be nobody left to undo
it.

Failures use the stack's one envelope, `{"error": {"code", "message"}}`:

| Code | Status | |
| --- | --- | --- |
| `invalid_credentials` | 401 | the database did not accept that name and password |
| `too_many_attempts` | 429 | `AUTH_THROTTLE_FAILURES` (5) wrong for one name, or `AUTH_THROTTLE_ADDRESS_FAILURES` (50) from one address, in `AUTH_THROTTLE_SECONDS` (900) |
| `sign_in_unavailable` | 503 | the database could not be asked |
| `unauthorized`, `sign_in_required`, `expired`, `malformed`, `bad_signature`, `wrong_key`, `wrong_audience`, `not_yet_valid`, `account_removed`, `session_revoked` | 401 | no session, or one that is not good |
| `forbidden`, `cross_site`, `not_a_person` | 403 | signed in, but not allowed this |
| `wrong_password` | 403 | the current password, when changing it |
| `password_too_short` | 422 | shorter than `LDAP_MIN_PASSWORD_LENGTH` |
| `invalid_request` | 422 | a body or query string that is not valid, with what was wrong in `detail` |
| `cannot_remove_yourself`, `cannot_demote_yourself`, `replica_read_only` | 409 | |
| `not_found` | 404 | no such person |
| `directory_unavailable`, `role_sync_unavailable`, `roles_unavailable` | 503 | |
| `revocation_unavailable` | 503 | done -- signed out of this browser, the password changed or set, the person removed -- but the sessions from before could not be ended; try again, or they end when they expire |

## The directory page

`auth/gui`: React and TypeScript, built to static files and served by
nginx, which proxies `/auth/` and `/directory/` to this service -- the same
shape as every other interface here. It lists people, adds them (with a
generated password if wanted), edits their details and groups, sets
passwords, clears lockouts, removes them (two clicks, the second naming
who), imports a file and runs the role sync. It signs in like every other
interface and admits `nl2sql_admins` only.

Its start-up script refuses `LDAP_MODE=replica`: a replica's people are
edited on its primary, so there is no page to serve.

```bash
./launch.sh --api        # standalone: starts the directory, this service and the page
open https://localhost:8084
```

The directory GUI: 61 tests, at 100% of statements, branches, functions and
lines (`cd auth/gui && npm test`, or `pytest --run-node
tests/auth/test_directory_gui_suite.py`).

## Running unprivileged

Since 6.2 the service starts as root only long enough to give the account
`nl2sql` (10001) the two directories it writes -- the signing key's and the
public key's, with whatever an older release wrote there as root -- and runs
as it from then on; the start-up says `running as nl2sql`. Its TLS key is
that account's already: the pki service hands each key to the account its
service runs as (`docker-compose.yml`, the last part of each identity).

## Settings

The auth service's, read from the environment; compose passes each from
`.env`. The directory's own are in [`ldap/README.md`](../ldap/README.md).

| Variable | Default | |
| --- | --- | --- |
| `AUTH_HOST` | `0.0.0.0` | |
| `AUTH_PORT` | `8446` | also the published port: signing in, for the pages and the desktop client |
| `AUTH_DIRECTORY_PORT` | `8447` | the directory's own API -- the routes that make people -- answered here and nowhere else; not published, so only the directory page's nginx reaches it. `0` serves it on `AUTH_PORT`, as 6.0 did |
| `AUTH_ROOT_PATH` | -- | behind a path-prefixing proxy |
| `AUTH_TLS_ENABLED` | `true` | presents its own certificate, from the pki service, on both ports |
| `AUTH_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` | |
| `AUTH_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` | |
| `AUTH_SIGNING_KEY_FILE` | `/var/lib/nl2sql-auth/session.key` | written on first start, 0600 |
| `AUTH_PUBLIC_KEY_FILE` | `/etc/nl2sql/auth/session.pub` | written beside it, for everyone else |
| `AUTH_COOKIE_NAME` | `nl2sql_session` | the same on every service |
| `AUTH_SESSION_HOURS` | `8` | |
| `AUTH_THROTTLE_FAILURES` | `5` | wrong passwords for one name |
| `AUTH_THROTTLE_ADDRESS_FAILURES` | `50` | wrong passwords from one address, higher because a proxy or NAT makes many people one address |
| `AUTH_THROTTLE_SECONDS` | `900` | |
| `AUTH_TRUSTED_PROXIES` | -- (compose: the six page proxies) | addresses, networks or names whose `X-Forwarded-For` counts as where a sign-in came from; anyone else -- a desktop client on the published port -- is counted by the address it connected from, since it writes that header itself |
| `AUTH_DB_HOST` | `nl2sql-postgres` | the database people sign in to |
| `AUTH_DB_PORT` | `5432` | |
| `AUTH_DB_NAME` | `nl2sql_retail` | compose passes `POSTGRES_DB` |
| `AUTH_DB_SSLMODE` | `verify-full` | a person's password crosses this hop; anything weaker is warned about at start |
| `AUTH_DB_SSLROOTCERT` | `/etc/nl2sql/pg-tls/server.crt` | the database's own certificate, from the `pgtls` volume |
| `AUTH_DB_CONNECT_TIMEOUT` | `5` | seconds |
| `AUTH_ROLESYNC_DB_URL` | -- | compose builds it from `AUTH_ROLESYNC_USER` (`nl2sql_rolesync`), with no password; the password is read from `AUTH_ROLESYNC_DB_PASSWORD_FILE`, `secrets/auth_rolesync_password`, which setup.sh generates and dbprep sets the role's from (6.3); held to `AUTH_DB_SSLMODE` unless it says `sslmode=` itself |
| `AUTH_READER_ROLE` | `nl2sql_reader` | the role granted each person's, to become them |
| `AUTH_GROUP_ROLES` | the four above | `group=role,group=role` |
| `AUTH_ROLE_SYNC_INTERVAL` | `30` | seconds |
| `AUTH_USER_STATEMENT_TIMEOUT_MS` | `60000` | each person's own; with 16 MB of `work_mem` and a minute idle in a transaction (6.3), checked again by every sync |
| `AUTH_USER_CONNECTION_LIMIT` | `5` | each person's own |
| `AUTH_LDAP_URL` | `ldap://nl2sql-ldap:389` | for the directory page and password changes |
| `AUTH_LDAP_STARTTLS` | `true` | |
| `AUTH_LDAP_CACERT` | `/etc/nl2sql/ldap-tls/ldap.crt` | |
| `AUTH_LDAP_TIMEOUT` | `10` | seconds |
| `LDAP_MODE` | `standalone` | the directory's, so a replica is never edited |
| `LDAP_BASE_DN` | `dc=nl2sql,dc=local` | |
| `LDAP_SERVICE_PASSWORD` | -- | this service's account in the directory; compose gives `LDAP_SERVICE_PASSWORD_FILE`, `secrets/ldap_service_password`, which wins when set (6.3) |
| `LDAP_MIN_PASSWORD_LENGTH` | `12` | |
| `MLFLOW_ALLOWED_ROLES` | `nl2sql_reviewers,nl2sql_admins` | |
| `AUTH_CORS_ORIGINS` | -- | |
| `AUTH_DOCS_ENABLED` | `true` | `/docs` |
| `AUTH_LOG_LEVEL` | `info` | |

The services that check a session read three: `AUTH_ENABLED` (compose:
`true`), `AUTH_PUBLIC_KEY_FILE` and `AUTH_COOKIE_NAME`. Every interface reads
`AUTH_ENABLED` and where to send sign-ins -- `GUI_AUTH_UPSTREAM`
(`https://nl2sql-auth:8446`), `GUI_AUTH_SSL_NAME` (`nl2sql-auth`),
`GUI_AUTH_CACERT` (`/etc/nl2sql/tls/ca.crt`) in `.env` -- and is HTTPS
with its own certificate unless `GUI_TLS_ENABLED=false`.

## Tests

| | |
| --- | --- |
| `tests/auth/test_identity_*.py` | the session format and the guard, at 100% |
| `tests/auth/test_auth_*.py` | settings, keys, sign-in, throttling, the role sync's plan, the directory routes, the server -- offline, at 100% |
| `tests/auth/test_auth_compose.py` | the four services as compose resolves them (`--run-docker`) |
| `tests/auth/test_auth_image.py`, `test_auth_live.py` | the image, and sign-in end to end against a real database, directory and MLflow -- including the database's sign-in roles applied again, as on a later start, changing no membership (`--run-docker`) |
| `tests/auth/test_directory_gui_*.py` | the page as a project, its types against these models, and its suite (`--run-node`) |
