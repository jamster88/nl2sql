# Security

What this stack protects, from whom, where its boundaries are, what each
credential is worth, and which deployments its defaults are built for. As of
**6.2.0**. The sign-in design these rest on -- why it is built this way, the
alternatives rejected, the limits chosen -- is section 20 of
[`multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md),
with what 6.2 added -- revocation, named tokens, the accounts each service
runs as -- in
[`Multi-Agent_NL2SQL_arch6_2.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6_2.md);
how to switch each control on and off is in
[`USAGE_GUIDE.md`](USAGE_GUIDE.md#security).

Language-model risks -- prompt injection, what a model host learns from a
prompt, an answer that misleads -- are not covered here. The one fact that
belongs here anyway: every question sends the pruned schema and a few
**sample rows** of each table in scope to the Ollama host serving the
models (`OLLAMA_BASE_URL`). Treat that host as able to read the database.

## What there is to protect

| Asset | Where | What losing it costs |
| --- | --- | --- |
| The retail data | the retail database (`nl2sql-postgres`) | read by everyone signed in, written by nobody but its owner; a write is the dataset under test changed |
| A person's directory password | the directory (`nl2sql-ldap`), hashed with Argon2 | it is their password for every page, the API, the desktop client, MLflow and the database itself |
| A session | a cookie in a browser, a bearer token in the desktop client | eight hours of that person, wherever it is presented |
| What the agent learns from | `context_questions/` in the checkout; the snippet, corrections and completions stores | a poisoned golden pair or snippet is SQL the agent will be shown as correct |
| What people said and asked | the staging database, MLflow's store | every question, the SQL, and the rows it returned |
| Secrets | `.env` (0600), the auth service's signing key, the stack's CA key, each server's TLS key | listed below, with what each is worth |

## Who it is protected from

- **Someone on the same network without an account.** The main case: every
  page, the API and the sign-in service are reachable from the network by
  default, and need a person signed in.
- **A signed-in person reaching past their groups.** A reviewer is not an
  administrator; a user cannot curate.
- **Another web page in the same browser.** It can make the browser send a
  request; it must not be able to make it send one that carries a session,
  or read the answer.
- **Someone who has copied a session or a token.** Limited by lifetime, not
  yet by revocation (see *Known limits*).
- **A compromised container.** What it can reach is what it holds, listed
  below; since 6.1 no container holds another server's key.

Out of scope: someone with a shell on the machine running Docker. They can
read `.env`, every volume and every container; nothing here defends against
them, and nothing claims to.

## Trust boundaries

```
 browser ──HTTPS──▶ page's nginx ──HTTPS, verified──▶ API / review / console
    │                    │                                   │
    │                    └──HTTPS, verified──▶ auth service ─┼──TLS, verify-full──▶ retail Postgres ──StartTLS, verified──▶ directory
    │                                              │         │                          ▲
 desktop ──HTTPS, verified (the stack's CA)───────┘         └──TLS (SET LOCAL ROLE)────┘
 psql / a BI tool ──TLS required (hostssl)──────────────────────────────────────────────┘
```

| Boundary | What crosses it | What protects it |
| --- | --- | --- |
| browser → a page | the person's password (once), their session cookie | HTTPS with the page's own certificate; cookie `HttpOnly`, `SameSite=Strict`, `Secure`; every cookie-carrying write checked for its origin |
| page → service | the session, unchanged; with sign-in off, the page's token | HTTPS, verified against the stack's CA and the service's name (`proxy_ssl_verify`) |
| service → retail database | a reader password (SCRAM, never in clear), then `SET LOCAL ROLE` to the person | TLS (the server refuses anything else); the reader may become a person, never hold what they hold |
| auth service → retail database | **a person's directory password, in clear inside the session** -- pg_hba's `ldap` method needs it to bind | TLS with `verify-full` against the database's own certificate; the rule is `hostssl` |
| retail database → directory | the same password, to bind as the person | StartTLS, required by the directory and verified by libldap |
| replica → primary directory | every person's password, passed through | `ldaps://` or StartTLS; a clear-text primary is refused unless `LDAP_UPSTREAM_ALLOW_CLEARTEXT=true` |
| anything → a store | that store's owner password (SCRAM) | the store's port is on this machine only, unless `DB_BIND_ADDRESS` says otherwise |

## What each container holds

| Container | Secrets it holds | What it could do with them |
| --- | --- | --- |
| `nl2sql-pki` (one-shot) | the stack's CA key (`pkica`, mounted nowhere else) | issue a certificate every client of the stack trusts |
| each server (API, review, console, auth, six pages) | its own TLS key, in a volume only it mounts (6.1) | impersonate that server, and only that one |
| `nl2sql-auth` | the session signing key; the role sync's password; the directory service account's password | sign a session for anyone; make and drop people's roles, and write the revoked-session lists (nothing else in the database); read and, standalone, edit the directory |
| `nl2sql-api`, `nl2sql-console` | the reader's password; with feedback, the INSERT-only writer's; any service token | read the retail data as the agent does; become any person for a transaction; insert a verdict; ask whether a session was revoked (yes or no, one session at a time) |
| `nl2sql-review` | the staging, corrections, completions, snippet, context and vector stores' owners; the reader's password; `REVIEW_TOKEN` if set | rewrite what the agent learns from; read every submission |
| `nl2sql-postgres` | its own TLS key | nothing over the network: the superuser has no password and is refused there |
| `nl2sql-ldap` | its own TLS key; Argon2 hashes of every password | serve the directory |
| a page's nginx | its own TLS key, the CA's certificate; with sign-in off, its service's token | serve its page; with sign-in off, call its service as the service |

## What each credential is worth

| Credential | Grants | For how long | Limited by |
| --- | --- | --- | --- |
| a directory password | everything the person's groups allow, everywhere; a direct database login as them | until changed | 5 wrong tries per name and 50 per address in 15 minutes at the auth service; the directory's own lockout, 5 in a row for 15 minutes, which also covers a direct `psql` |
| a session | the person's groups, as Postgres says they are now | `AUTH_SESSION_HOURS` (8), or until it is ended | groups and the session's standing re-read at most once a minute per service: someone removed, a session signed out, one from before a password change or set or a lock, is refused within a minute (6.2) |
| a service token (`API_TOKEN`, `REVIEW_TOKEN`, `CONSOLE_TOKEN`) | the roles its `_TOKEN_ROLES` names, recorded under its `_TOKEN_NAME` (6.2); with sign-in off, everything | until changed | only configured when asked for (`setup.sh --tokens`); the review token reviews and does not curate unless granted |
| a store's owner password | that store, entirely | until changed | the store's port is this machine's by default; generated per installation |
| the reader's password | read every retail table; become any person for a transaction | until changed | generated per installation; `SELECT` only, read-only transactions |
| the CA key | a certificate any client of this stack trusts | ten years | mounted by the pki service alone. Delete the `pkica` volume to replace it: every server is reissued on the next start, and every client must be given the new `nl2sql-ca.crt` |
| the signing key | a session for anyone | until replaced | the auth service's alone. Deleting it signs everyone out at once; one person's sessions are ended by their revocation lists (6.2) |

## Deployment tiers

The defaults implement the second.

### 1. Alone: sign-in off

`./start.sh --no-auth`, or `./setup.sh --no-auth` for good. No directory, no
auth service; every page and port is open to whoever can reach it, and each
service's static token is its only control. **Only on a machine nothing
else can reach** -- a host firewall that admits nothing inbound, or a
machine on no network. The stores, the console, the directory page and
MLflow stay on this machine either way; the pages, the API and the review
service do not, which is why this tier needs the firewall. Every start
says it is open.

### 2. A team on a trusted network: the defaults

What `./setup.sh && ./start.sh` gives you:

- sign-in on everywhere, in compose and in every service's own defaults, so
  a service started any other way is not open by accident;
- the pages, the API, the review service and sign-in reachable from the
  network over HTTPS, each with its own certificate from the stack's CA;
- the SQL console, the directory page, MLflow's front door and every
  database on this machine only;
- every password generated per installation, in `.env`, readable by its
  owner alone; no service token unless asked for;
- TLS on every connection to the retail database, required by the server;
  a person's password accepted only over it, and verified end to end;
- every service running as an account of its own, not root, and reading
  only its own key (6.2); a session that can be ended -- by signing out, a
  password change, a lock or a removal -- for every service at once (6.2).

What it asks of you: give people `nl2sql-ca.crt` to trust (their browser
warns until they do), add this machine's name to `TLS_EXTRA_HOSTNAMES` so
the certificates cover the name they type, and keep the network trusted --
the *Known limits* below are why.

To let people connect to the retail database with their own tools, publish
it (`DB_BIND_ADDRESS`) and add the name they connect to to
`POSTGRES_TLS_HOSTNAMES`; their client must use TLS, and should verify the
database's certificate (`sslmode=verify-full`). [`USAGE_GUIDE.md`](USAGE_GUIDE.md#connecting-to-the-retail-database-directly)
has the commands.

### 3. Beyond a trusted network: not yet

Exposing the stack to people you do not trust, or to the internet, needs
more than this release has. The settings exist for part of it:

- real certificates for every server and page, with
  `API_TLS_ALLOW_SELF_SIGNED=false`, or a terminator in front of the pages
  (`GUI_TLS_ENABLED=false` behind it -- it must send `X-Forwarded-Proto`);
- your organisation's directory, copied by a replica (`LDAP_MODE=replica`),
  so accounts are created and removed where everything else's are;
- a backup of the volumes that hold what people typed (`feedbackdata`,
  `correctionsdata`, `completionsdata`, `mlflowdata`) and of `.env`.

And the work in *Known limits*, which no setting replaces.

## Properties, and the tests that hold them

| Property | Test |
| --- | --- |
| Every store is published on this machine only by default | `tests/security/test_posture.py` |
| The dataset image bakes in no password; the superuser has none and is refused over the network; nothing crosses to it in clear | `tests/security/test_posture.py`, `tests/docker/test_retail_entrypoint.py`, `tests/docker/test_init_db_script.py` |
| Sign-in's database rules are TLS-only; the auth service verifies the database | `tests/security/test_posture.py`, `tests/docker/test_ldap_hba_script.py`, `tests/auth/test_auth_settings.py` |
| Every server has its own key; no container holds another's; the CA key is the pki service's alone | `tests/security/test_posture.py`, `tests/auth/test_identity_pki.py` |
| Sign-in is on by default in every service's own code | `tests/security/test_posture.py` |
| Every route that does something is guarded | `tests/security/test_routes_guarded.py` |
| No wire model drops or accepts a field it does not declare | `tests/security/test_wire_models.py` |
| No secret is passed to a program on its command line by the scripts | `tests/security/test_posture.py` |
| A session's signature, issuer, audience and expiry are all checked; `alg: none` and key confusion are malformed, not negotiated | `tests/auth/test_identity_tokens.py` |
| A cookie-carrying write from another site is refused | `tests/auth/test_identity_guard.py` |
| The agent's reader cannot write, cannot reach another database, and cannot signal another session | `tests/agent/test_least_privilege_live.py` (`--run-docker`) |
| A person removed from the directory loses access within a minute | `tests/auth/test_identity_guard.py`, `tests/auth/test_auth_rolesync.py` |
| A session signed out, or from before a password change or set, a lock or a removal, is refused within a minute; the lists are the auth service's to write and nobody's to read | `tests/auth/test_auth_revocation.py`, `tests/auth/test_auth_live.py` (`--run-docker`) |
| A service token is named and holds only the roles it is given; a header never names who did something | `tests/auth/test_identity_guard.py`, `tests/review/test_signin.py` |
| Nothing runs as root but the one-shot pki service: every image names its account or drops to one, and each key is its account's | `tests/security/test_unprivileged.py`, `tests/auth/test_identity_pki.py` |
| Every broad `except` says why it is broad | `tests/security/test_error_taxonomy.py` |
| Every Python image installs a hash-checked lock | `tests/security/test_supply_chain.py` |
| A failure's own words -- hosts, drivers, configuration -- are an administrator's to see | `tests/api/test_signin.py`, `tests/auth/test_identity_guard.py` |
| The sign-in throttle counts by a trusted proxy's word or the connection's own address | `tests/auth/test_auth_proxies.py`, `tests/auth/test_auth_app.py` |

`tests/security/` is the tier for posture: what the stack exposes by
default, read from the files that decide it.

## Known limits

Each is a finding of the latest review
([`adversary_reviews/v6_1_review_summary.md`](adversary_reviews/v6_1_review_summary.md))
with its plan item.

- **A revoked session works for up to a minute more** at a service that
  checked it within the minute: each service asks Postgres about a session
  once a minute (6.2; S-18 was "cannot be revoked" until then). The auth
  service forgets at once, and an administrator's `POST /v1/admin/reload`
  makes the API forget too. A lockout ends the sessions from before it,
  which lets someone who can trigger one sign that person out; the per-name
  throttle slows that.
- **"Runs as the person" is attribution, not isolation.** Every person's
  role reads what the agent's reader reads -- there are no row-level
  policies. The connection is the reader's, so `session_user` names the
  reader; since 6.2 the transaction's `application_name` names the person
  (`nl2sql:agent:alice`) for `pg_stat_activity` and the log's `%a`. (I-18.)
- **No container has a memory limit, a read-only root or dropped
  capabilities.** Since 6.2 nothing runs as root but the one-shot pki
  service. (M-03; V6-34.)
- **Health checks do not verify the certificate they connect to.** They
  check the container's own socket. (S-15; V6-37.)
- **Base images are not pinned by digest.** Every Python dependency is
  installed from a hash-checked lock since 6.2. (M-02; V6-35.)
- **The directory's own API answers inside the stack only** (port 8447, not
  published), and still needs an administrator's session there.

## Reporting

This is a research codebase. Report a security problem to its maintainer
privately rather than in a public issue, with what you saw and how to
reproduce it.
