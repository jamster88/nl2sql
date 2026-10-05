# Security

What this stack protects, from whom, where its boundaries are, what each
credential is worth, and which deployments its defaults are built for. As of
**6.1.0**. The sign-in design these rest on -- why it is built this way, the
alternatives rejected, the limits chosen -- is section 20 of
[`multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md);
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
| `nl2sql-auth` | the session signing key; the role sync's password; the directory service account's password | sign a session for anyone; make and drop people's roles (nothing else in the database); read and, standalone, edit the directory |
| `nl2sql-api`, `nl2sql-console` | the reader's password; with feedback, the INSERT-only writer's; any service token | read the retail data as the agent does; become any person for a transaction; insert a verdict |
| `nl2sql-review` | the staging, corrections, completions, snippet, context and vector stores' owners; the reader's password; `REVIEW_TOKEN` if set | rewrite what the agent learns from; read every submission |
| `nl2sql-postgres` | its own TLS key | nothing over the network: the superuser has no password and is refused there |
| `nl2sql-ldap` | its own TLS key; Argon2 hashes of every password | serve the directory |
| a page's nginx | its own TLS key, the CA's certificate; with sign-in off, its service's token | serve its page; with sign-in off, call its service as the service |

## What each credential is worth

| Credential | Grants | For how long | Limited by |
| --- | --- | --- | --- |
| a directory password | everything the person's groups allow, everywhere; a direct database login as them | until changed | 5 wrong tries per name and 50 per address in 15 minutes at the auth service; the directory's own lockout, 5 in a row for 15 minutes, which also covers a direct `psql` |
| a session | the person's groups, as Postgres says they are now | `AUTH_SESSION_HOURS` (8) | groups re-read at most once a minute per service: someone removed loses access within a minute. **Not revocable** otherwise |
| a service token (`API_TOKEN`, `REVIEW_TOKEN`, `CONSOLE_TOKEN`) | every role that service grants, unattributed; with sign-in off, everything | until changed | only configured when asked for (`setup.sh --tokens`) |
| a store's owner password | that store, entirely | until changed | the store's port is this machine's by default; generated per installation |
| the reader's password | read every retail table; become any person for a transaction | until changed | generated per installation; `SELECT` only, read-only transactions |
| the CA key | a certificate any client of this stack trusts | ten years | mounted by the pki service alone. Delete the `pkica` volume to replace it: every server is reissued on the next start, and every client must be given the new `nl2sql-ca.crt` |
| the signing key | a session for anyone | until replaced | the auth service's alone. Deleting it signs everyone out: the one revocation there is |

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
  a person's password accepted only over it, and verified end to end.

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

`tests/security/` is the tier for posture: what the stack exposes by
default, read from the files that decide it.

## Known limits

Each is a finding of the latest review
([`adversary_reviews/v6_1_review_summary.md`](adversary_reviews/v6_1_review_summary.md))
with its plan item.

- **A session cannot be revoked.** Signing out deletes the cookie; a copy
  works until it expires, eight hours at most. Changing a password, locking
  an account and an administrator's reset leave existing sessions valid.
  The role recheck catches a person removed from a group or the directory
  within a minute; nothing catches a stolen token. (S-18; V6-71, V6-61.)
- **A service token is an identity with every role and no name.** Its
  actions are recorded under whatever `X-Reviewer` says. (S-19; V6-62.)
- **"Runs as the person" is attribution, not isolation.** Every person's
  role reads what the agent's reader reads -- there are no row-level
  policies -- and the connection is the reader's, so the server log and
  `pg_stat_activity` name the reader; only `current_user`, inside the
  transaction, names the person. (I-18; V6-64.)
- **Every application image but the directory's runs as root**, and no
  container has a memory limit, a read-only root or dropped capabilities.
  6.1 removed the reason they could not (the shared key); the change itself
  is next. (M-01, M-03; V6-31, V6-34.)
- **Health checks do not verify the certificate they connect to.** They
  check the container's own socket. (S-15; V6-37.)
- **The per-address sign-in throttle trusts `X-Forwarded-For`** on the
  sign-in port, which is published for the desktop client; the per-name
  limit and the directory's lockout still hold. (S-20; V6-63.)
- **Dependencies are not hashed and base images are not pinned by digest.**
  (C-07, M-02; V6-24, V6-35.)
- **The directory's own API answers inside the stack only** (port 8447, not
  published), and still needs an administrator's session there.

## Reporting

This is a research codebase. Report a security problem to its maintainer
privately rather than in a public issue, with what you saw and how to
reproduce it.
