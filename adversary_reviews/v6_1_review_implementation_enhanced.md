# Adversarial review, v6.1 cycle: the implementation

**Reviewed at:** commit `9b0b340` (release 6.0.1), branch `utils/ldap_auth`, 2026-10-04.
**Review tag:** `v6_1_review`. Second cycle; the first was `v6_x_review` at `a625cf0` (5.6.1). Finding ids are kept from the first cycle where a finding persists, each with its status: **Unchanged**, **Worse**, **Improved**, **Mitigated**, **Resolved**, **New**. The comparison is [`v6_1_review_changes_since_v6_x.md`](v6_1_review_changes_since_v6_x.md).
**Companion documents:** [`v6_1_review_architecture_as_documented.md`](v6_1_review_architecture_as_documented.md), [`v6_1_review_architecture_as_implemented.md`](v6_1_review_architecture_as_implemented.md), [`v6_1_review_mitigation_plan.md`](v6_1_review_mitigation_plan.md), [`v6_1_review_summary.md`](v6_1_review_summary.md).

> **Enhanced edition.** The text below is [`v6_1_review_implementation.md`](v6_1_review_implementation.md) unchanged, with 4 figures added (1 copy-and-extend at 6.0.1; 2 one key, four identities, eleven containers, and why nothing drops root; 3 how a sign-in travels, and where the password is in clear text; 4 the compose topology at 6.0.1 defaults). Each figure is an SVG rendered by draw.io from the `.drawio` source in [`diagrams/`](diagrams/), with a PNG beside it; a figure the first cycle drew is embedded unchanged where the code it shows did not change, and its caption says so. The original file is untouched.

## Scope and method

Three lenses, as in the first cycle:

- **A. Code hygiene** — clean code, comments, encapsulation, duplication, error handling, dependency management, tests.
- **B. Containers and microservices** — image construction, runtime hardening, composition, secrets, publishing.
- **C. Security controls** — authentication, authorisation, transport, data exposure, denial of service, supply chain. **LLM-specific concerns remain excluded by request.**

Method: static reading of the Python packages (now including `auth/nl2sql_auth`, `auth/nl2sql_identity` and `ldap/nl2sql_ldap`), the five TypeScript clients, the Java client, all fifteen Dockerfiles, `docker-compose.yml`, the shell scripts and the test suite's layout; counts were taken with `grep` and `wc` at the reviewed commit and are reproducible from the cited paths. Nothing was executed; nothing in the sibling `fireworks_nl2sql` folder was consulted.

Finding ids are `C-nn` (hygiene), `M-nn` (containers/microservices) and `S-nn` (security). Severity is Critical / High / Medium / Low. Confidence is **Verified** or **Inferred**.

## 1. Verdict

The new code is of the quality the old code was: typed, explained, tested at 100% with 357 test functions in `tests/auth/` and `tests/ldap/`, and in three places better than anything before it (the session format, the directory image's privilege drop, the role sync's scoping). The security controls the first cycle rated weakest are the ones 6.0 fixed: authentication is on by default, token comparison is constant-time, the query-string token is confined to the one route that needs it, jobs belong to people, and promotions are signed by the person who made them.

Everything the first cycle rated *most consequential* is where it was. The published retail image still carries the `postgres` superuser with password `nl2sql`; compose still publishes it and six more stores on every interface with passwords equal to their user names; the new pg_hba block sits above the catch-all rule rather than replacing it. No application image runs as a non-root user but the directory's; no service has a resource limit, a read-only root or a dropped capability; dependencies are still unpinned and unhashed in two of six requirements files and hashed in none. And the fix itself brought new exposure: a person's directory password crosses to a Postgres server that has no TLS, by an authentication method that sends it in clear, through a port open to the network; the secure default lives in compose rather than in the services; a session cannot be revoked; and the static tokens that sign-in made optional are, when set, an unattributed identity holding every role.

Hygiene moved the wrong way in the ways the first cycle predicted. The sign-in front end is five byte-identical copies; the nginx image is six; the shell scripts are 3,208 lines; the review application is 1,537; and the one test that was already failing in the first cycle still fails on every run.

The single most consequential item is still **S-08/M-08**, and the new **S-16** sits beside it: a release whose purpose is to check every person's password sends that password in clear text to a server whose superuser password is known.

## 2. A. Code hygiene

### Strengths

- The first cycle's five points stand. New: the identity package is small, dependency-light and documented (`tokens.py`'s docstring is the design rationale the spec lacks); `ldap/` and `auth/` have injectable collaborators throughout and a fake-free test for every branch; 357 test functions cover the two new packages, with live tests against a real directory, database and MLflow behind `--run-docker`.
- `layout.py:38-56` turns a login name into a Postgres identifier by refusing anything that is not already safe, which is the right way round.

### Findings

**C-01 · Medium · Verified · Worse — Duplication by copy-and-extend, applied to the new feature.**
Still copied from the first cycle: `Embedder` ×5, pgvector literal ×6, `FALLBACK_CODES`/`Health`/`Readiness`/`ApiError` (now ×3 with `auth/app.py:89-101` and `auth/models.py`), `json_safe` ×2. Now worse:
- environment helpers in **five** files: `agent/nl2sql_agent/config.py`, `review/nl2sql_review/settings.py`, `auth/nl2sql_auth/settings.py:53-76`, `auth/nl2sql_identity/guard.py:78-87`, `ldap/nl2sql_ldap/settings.py:87-112`;
- `_redacted` ×3: `review/app.py:1509`, `review/server.py:131`, `auth/server.py:48`;
- the sign-in front end ×5, byte-identical: `src/auth/session.ts` (145 lines) and `src/auth/SignInGate.tsx` (331 lines) in `gui/`, `review/gui/`, `console/`, `curate/`, `auth/gui/` — `diff` reports no difference between any pair; 2,380 lines;
- the nginx proxy image ×6 (I-14) and its `upstream_tls` shell function ×6.
One copy was *removed*: the three token-check closures are now one `Guard` (`guard.py:237-241`). That is what a shared package does, and it is the only one.
*Why it matters:* a defect in the sign-in gate, the one component every person now meets first, must be fixed in five places, and the vitest suites give no signal when one is missed.

**Figure 1. Copy-and-extend at 6.0.1.** Fifty-three copies of thirteen things, up from forty-one of twelve. The sign-in gate is five identical copies, the nginx image six, the environment helpers seven. One row went the other way: the three token checks are one `Guard`.

![Copy-and-extend at 6.0.1](diagrams/v6_1_review_duplication_matrix.svg)

<sub>Also as [PNG](diagrams/v6_1_review_duplication_matrix.png) · editable source [`v6_1_review_duplication_matrix.drawio`](diagrams/v6_1_review_duplication_matrix.drawio)</sub>

**C-02 · Medium · Verified · Unchanged — Broad exception handling.**
`except Exception` 54 times in `agent/nl2sql_agent`, 24 in `review/nl2sql_review` (the first cycle's counts); 2 each in `auth/nl2sql_auth` and `ldap/nl2sql_ldap`, each annotated with a reason (`guard.py:314`, `rolesync.py:256`). The new code shows the discipline; the old code has not been revisited.

**C-03 · Medium · Verified · Worse — Oversized units.**
`review/nl2sql_review/app.py` 1,537 lines (was 1,488); `docker-compose.yml` 1,312 (970); `launch.sh` 1,381 (1,156); `setup.sh` 944 (839); `start.sh` 883 (851); `agent/nl2sql_agent/api/app.py` 769 (720); `auth/nl2sql_auth/app.py` 732 (new, one closure).

**C-04 · Medium · Verified · Unchanged — Encapsulation leaks and inconsistent strictness.**
`schema_retrieval.py:98`, `literals.py:166`; session-level `SET` at `examples.py:477`, `retrieval.py:177`, `snippets.py:231`; `TraceEntry(**t)` at `translate.py:88` into a lenient model.

**C-05 · Low · Verified · Unchanged — Version history in comments.**
`gui/src/api/useAsk.ts:5` still says "fourteen pipeline nodes" (the graph has eighteen). Comments naming releases remain in the agent and review packages, and `agent/README.md` opens with six paragraphs of them.

**C-06 · Medium · Verified · Worse — Shell as the orchestration language, now with superuser SQL.**
3,208 lines (was 2,846). `launch.sh:306-315` runs `docker/auth_roles.sql` as `postgres` with `-v rolesync_password=$(...)`; `launch.sh:317-325` pipes `docker/ldap_hba.sh` into the database container; `launch.sh:287-300` writes secrets into `.env`. The grep-based `.env` parser (`compose_env`) now reads passwords.

**C-07 · Medium · Verified · Unchanged — Pinning inconsistent, never hashed.**
`agent/requirements.txt` 10 exact, 1 range; `auth/requirements.txt` 5 exact, 1 range; `review/requirements.txt` 4 exact, 4 ranges; `rag/requirements.txt` 0 exact, 4 ranges; `tests/` 1 and 1; `data_gen/` 0 and 3. No `--hash` anywhere. The directory image takes `ldap3` and `cryptography` from Alpine's package index at whatever version `alpine:3.22` carries today (`ldap/Dockerfile:34-36`).

**C-08 · Low · Inferred · Unchanged — Coverage-shaped tests.** As before; V6-51 open.

**C-09 · Low · Verified · Unchanged, half withdrawn — Hand-rolled id allocation.**
`corrections.py:293, 319` (`max + 1` under a table lock). The first cycle also faulted `feedback.py`'s delete-then-insert; its docstring (`feedback.py:144-150`) gives a privilege reason (`ON CONFLICT DO UPDATE` needs table-level `SELECT`, which the writer role is shaped to lack) that the first cycle should have credited. Withdrawn for feedback; stands for fix ids.

**C-10 · Resolved (for sessions) — Self-asserted attribution.**
`author(caller, claimed)` (`review/app.py:194-200`) records the signed-in principal and uses the `X-Reviewer` header only when there is no principal, which with sign-in on means only for the static service token. That remainder is S-19.

**C-11 · Medium · Verified · New — A test that fails on every run ships in the suite.**
`tests/benchmarks/test_questions.py::test_no_benchmark_question_is_a_golden_pair_verbatim` fails because benchmark question B03 is golden pair Q46 word for word (since 5.4, when the pair was promoted). The v6_0_1 changelog records "every test ... passes but" this one as the expected state. A suite with one permanent red teaches everyone to read "1 failed" as green, which is how a second failure hides; and the first cycle itself read the test's name and reported it as a guarantee (D-14) without noticing it was red. Mark it `xfail(strict=True, reason=...)`, change the assertion to the provenance the repository actually guarantees, or change the data; any of the three takes an hour.

**C-12 · Low · Verified · New — The shared package is shared by `COPY`, not by package.**
`auth/nl2sql_identity` has no `pyproject.toml` and no version; `agent/Dockerfile:12`, `review/Dockerfile:29` and `auth/Dockerfile:28` each `COPY` the directory. The images are released together, so the copies agree today; nothing but a rebuild of all three would show a divergence, and nothing records which revision of the package an image carries.

**C-13 · Medium · Verified · New — 100% coverage in every language, four defects in the shipped release.**
The v6_0_1 changelog: the directory restarted for ever (a volume-ownership race between two containers), nobody could sign in (`launch.sh` omitted a variable the fake `docker` did not require), five wrong passwords from one address locked everyone out (NAT), the desktop client reported "Not connected" (a route that now needs a session). Its own diagnosis: "none of them could be seen by a test that ran a service on its own." The test strategy has unit tests at 100%, per-service live tests, compose-config tests and script tests against fakes, and no tier that starts the published stack with `start.sh` and uses every page the way the changelog's "Checked live" section does by hand. That section is a test plan written as prose after each release; it should be a test run before one.

## 3. B. Containers and microservices

### Strengths

- The first cycle's five points stand. New: `ldap/Dockerfile` with `nl2sql_ldap/service.py:53-86` is the first image to start as root only to take ownership of its volumes and then drop to an unprivileged user, with the health check and `docker exec` dropping the same way; its certificate is its own and private from the first byte (`tls.py:112-117`); the auth service's signing key is created `0600` in a volume nothing else mounts (`keys.py:27-35`, `docker-compose.yml:1037-1041`); the public half is in a separate volume mounted read-only (`authkeys`).
- MLflow's server is no longer published; its front door is (`docker-compose.yml:1163-1168`), and it asks the auth service about every request with the method it is about (`docker/mlflow-proxy/nginx.conf.template:47-60`).

### Findings

**M-01 · High · Verified · Unchanged — Every application image but one runs as root.**
`grep -c '^USER'` is 0 for fourteen of fifteen Dockerfiles; only `docker/Dockerfile` (the dataset) switches user. The directory image has no `USER` and drops privileges in its entry point, which is the fix; the other four Python images and six nginx images do not, and `auth/Dockerfile:18-19` says why: they present the API's `0600` key. The root-owned writes into `./context_questions` (`docker-compose.yml:640`) are unchanged.

**M-02 · Medium · Verified · Unchanged — Base images float on tags**, now with drift inside one release: `alpine:3.21` (`docker/apitest/Dockerfile:12`, `desktop/Dockerfile:42`) and `alpine:3.22` (`ldap/Dockerfile:22`); `python:3.12-slim` ×3, `nginx:1.29-alpine` ×6, `node:26-alpine` ×5, `postgres:18`, `pgvector/pgvector:pg18`, `maven:3.9.16-eclipse-temurin-21`, `ghcr.io/mlflow/mlflow:v3.16.1-full`. None by digest (M-12).

**M-03 · High · Verified · Unchanged — No runtime hardening.**
1,312 lines of compose and not one `read_only`, `cap_drop`, `no-new-privileges`, `mem_limit`, `pids_limit` or `deploy.resources`; `restart: unless-stopped` everywhere.

**M-04 · High · Verified · Unchanged — Seven Postgres servers on every interface with default credentials.**
`docker-compose.yml:29, 55, 81, 119, 154, 184, 218` publish 5432–5438 with no bind address; passwords default to `nl2sql`, `ragproc`, `feedback`, `corrections`, `completions`, `snippets` (lines 12, 78, 115, 150, 180, 214); `mlflowdb` is internal and defaults to `mlflow`/`mlflow` (1108). The pattern that is right is used for four services now (`CONSOLE_BIND_ADDRESS`, `DIRECTORY_GUI_BIND_ADDRESS`, `MLFLOW_BIND_ADDRESS`; lines 807, 867, 1085, 1214) and still for no store.

**M-05 · Medium · Verified · Worse — The private key in eleven containers.**
`apitls` read-write at `445`; read-only at `524, 626, 697, 744, 810, 869, 1033, 1087, 1216, 1241`. Seven containers need only the certificate; four present the key as their own identity.

**M-06 · Medium · Verified · Improved — Secrets as environment, now generated and 0600.**
`setup.sh:410-413` and `launch.sh:291-300` generate `LDAP_ADMIN_PASSWORD`, `LDAP_SERVICE_PASSWORD` and `AUTH_ROLESYNC_PASSWORD` once (24 bytes from `/dev/urandom`); `.env` and `.env.bak` are `chmod 600` (`setup.sh:531-533`); `auth/nl2sql_auth/settings.py:79-84` and `ldap/nl2sql_ldap/settings.py:115-124` read `*_FILE` so Docker secrets would work. Still: every secret is `environment:` (the rolesync password inline in a URL at `docker-compose.yml:1010`; the first administrator's password at `:913`); compose uses no `secrets:`; the seven store passwords and the three service tokens are not generated.

**M-07 · Medium · Verified · Worse — The microservice shape without its benefits.**
Six nginx images differing by their upstream and paths; four Python services sharing one certificate; thirteen image families released together; healthchecks that skip verification in twelve places (`ssl._create_unverified_context()` at `docker-compose.yml:458, 645, 820, 1047`, `review/Dockerfile:56`, `auth/Dockerfile:45`; `wget --no-check-certificate` in the six nginx Dockerfiles). The first cycle said the pattern would spread; it spread to every new image.

**M-08 · Critical · Verified · Unchanged — The published dataset image carries a known superuser password.**
`docker/init_db.sh:34-35` sets `postgres` and the owner to `DB_PASSWORD` (`nl2sql`) at build time; `init_db.sh:23` appends `host all all all scram-sha-256`; `docker-compose.yml:29` publishes the port on every interface; `README.md:1519-1520` documents the connection string and that the superuser shares the password. 6.0's `ldap_hba.sh:76-77` writes its block *above* the catch-all, so the rule that admits the superuser from anywhere is still in force. The published image is `nl2sql-retail-postgres:v1_1`; the first cycle's K1 (publish `v1_2` without the baked password) was not done.

**M-09 · Low · Verified · Unchanged — No provenance in publishing.** No `--provenance`, `--sbom`, signature or revision label in the documented commands.

**M-10 · Medium · Verified · New — Loopback binding protects the page, not the capability.**
The directory page is published on `127.0.0.1` "because it is where people are made" (`docker-compose.yml:1077-1085`), and every call it makes goes to `/directory/v1/*` on the auth service, which is published on every interface (`:1031`) because the desktop client signs in there. The protection is on the static files, not on the API that creates administrators. The console is consistent (its backend `:807` is loopback too); the directory is not. The routes need an admin session, so this is defence in depth lost rather than a hole, and it is the kind of inconsistency a threat model (V6-45) would have caught.

**M-11 · Medium · Verified · New — The shared key is the dependency the plan missed.**
`auth/Dockerfile:18-19`: "Root, as the review service and the console are, for the same reason: it presents the agent API's certificate, whose key that API writes 0600." Four services run as root so that they can read one file. The first cycle listed non-root images (V6-31) and separate TLS identities (V6-36) as independent items in different phases; V6-36 is a prerequisite of V6-31, and the directory image, which has its own key and its own user, is the proof. Recorded as a finding because a plan with the wrong dependency order is a plan whose first step fails.

**Figure 2. One key, four identities, eleven containers, and why nothing drops root.** The API writes `server.key` 0600 as root. Four services present it as their own identity and run as root to read it; seven more mount it needing only the certificate. The directory image, with its own key, is the one image that drops privileges, and it is the template for the rest.

![One key, four identities, eleven containers, and why nothing drops root](diagrams/v6_1_review_key_sharing.svg)

<sub>Also as [PNG](diagrams/v6_1_review_key_sharing.png) · editable source [`v6_1_review_key_sharing.drawio`](diagrams/v6_1_review_key_sharing.drawio)</sub>

**M-12 · Low · Verified · New — Base-image drift inside one release** (`alpine:3.21` and `alpine:3.22`, above).

## 4. C. Security controls (non-LLM)

### Strengths

- The first cycle's five stand (database least privilege, statement controls, transport, output escaping, warnings).
- **Authentication on by default**, with Postgres as the authority (`login.py`), a session that resists algorithm confusion (`tokens.py:47, 178`), constant-time comparison for the static token (`guard.py:239`), the query-string token confined to the event stream (`api/app.py:318`), `HttpOnly`/`SameSite=Strict`/`Secure` cookies (`auth/app.py:315-324`), a cross-site check on every cookie write (`guard.py:139-167`), a throttle per name and per address (`throttle.py`), the directory's own lockout and Argon2 hashing, a reader that can become a person only for a transaction, a role sync that can touch only what it made, reserved role names refused (`layout.py:43-56`), an open redirect refused (`pages.py:15-23`), and roles re-read within a minute.
- **Ownership**: a person sees and cancels only their own jobs; another owner's is a 404 (`api/app.py:527-533`), which also hides whether the id exists.

### Findings

**S-01 · Resolved — Authentication is on by default.**
`AUTH_ENABLED: ${AUTH_ENABLED:-true}` in eight compose services; without a session or a configured token every `/v1` route answers `401 sign_in_required`. With `--no-auth` the first cycle's posture returns and `launch.sh` warns (lines 701, 1109, 1162). The default is in compose, not in the services (S-17).

**S-02 · Resolved — Constant-time comparison** (`guard.py:239`, `hmac.compare_digest`); the three `!=` checks are gone.

**S-03 · Resolved — The query-string token is scoped to the event stream** (`guard.py:258-267`, `api/app.py:314-318`); the review service has none (`review/README.md:614`).

**S-04 · Low · Verified · Mitigated (code unchanged) — Permissive CORS default on the API and the review service.**
`api/settings.py:87, 155` and `review/settings.py:139, 226` still default to `("*",)`; the console and the auth service default to none. With `*`, `allow_credentials` is forced false (`api/app.py:304-306`) and the cookie is `SameSite=Strict`, so a cross-site page can neither send the cookie nor read a response; the exposure that remains is with sign-in off and a static token.

**S-05 · Medium · Verified · Unchanged — Information disclosure in normal responses.**
Exception text in `detail` and `retrieval_errors`; `/readyz` unauthenticated with per-dependency detail (`api/app.py:374`), which now also says whether sessions can be verified and why not (`guard.py:221-230`).

**S-06 · Resolved — Tenancy by owner** (`jobs.py:214-224`; `api/app.py:519, 531`).

**S-07 · Medium · Verified · Mitigated (code unchanged) — Denial of service by queue growth** now needs an account; `EXPLAIN` still untimed. With `--no-auth`, High again.

**S-08 · Critical · Verified · Unchanged — Databases reachable from the network with known passwords, including a superuser.** See M-04 and M-08. The headline finding of the first cycle is the headline finding of this one.

**S-09 · Medium · Verified · Worse — One TLS key for four services, in eleven containers.** See M-05, I-13.

**S-10 · Medium · Verified · Improved — Secret handling.** See M-06: three secrets generated and the files `0600`; the rest as before.

**S-11 · Low · Verified · Improved — Role-level limits exist for people and not for services.**
Each person's role gets `default_transaction_read_only = on`, `statement_timeout` (60 s) and `CONNECTION LIMIT 5` (`rolesync.py:194-205`). `nl2sql_reader`, `snippets_reader` and the feedback writer still have none; no role has `work_mem` or `idle_in_transaction_session_timeout`.

**S-12 · Medium · Verified · Unchanged — Supply chain.** See C-07, M-02, M-09.

**S-13 · Low · Verified · Improved — Logging of secrets.** The query-string token is accepted on one route (S-03), and uvicorn's access log still records it there; no filter masks `access_token=`.

**S-14 · Resolved (for sessions) — Non-repudiation.** A decision is recorded under the signed-in principal (`review/app.py:194-200`); the static-token remainder is S-19.

**S-15 · Low · Verified · Worse — Health checks skip verification in twelve places** (M-07). The first cycle asked for one pattern that verifies; the pattern that does not was copied into every new image.

**S-16 · High · Verified · New — A person's directory password crosses to Postgres in clear text, and the port is on every interface.**
pg_hba's `ldap` method is clear-text password authentication: the client sends the password to the server, which binds to the directory with it. `ldap_hba.sh:77` writes the rule as `host` (any connection, encrypted or not), not `hostssl`. The retail Postgres has no server certificate and no `ssl=on`: nothing in `docker/Dockerfile`, `docker/init_db.sh` or the `postgres` service in compose configures TLS, so every connection to it is plain TCP. Two consequences:
- inside the compose network, the auth service's sign-in connection (`login.py:86-96`, `sslmode=prefer` from `settings.py:132`) negotiates no TLS and sends every password it checks in clear across the bridge; `settings.warnings()` (`settings.py:264-269`) warns about `disable` and `allow` and treats `prefer` as fine, which it is only if the server offers TLS, and this one does not;
- outside it, `docker-compose.yml:29` publishes the port on every interface, and direct connection by people "with psql or a BI tool" is a documented feature (`rolesync.py:14-15`; changelog v6_0_1, "psql straight to the retail database with a directory password"). A person doing that from another machine sends their directory password, which is also their password for every page, across the LAN unencrypted.
The directory's own hop is right (StartTLS required, certificate verified through `LDAPTLS_CACERT`, `docker-compose.yml:20-27`), which makes the unencrypted hop before it the weak one.
*Fix:* give the retail database a certificate the way the directory gives itself one (a volume, generated on first start, `ssl=on`), write the sign-in rule as `hostssl`, set `AUTH_DB_SSLMODE=verify-full` with the certificate mounted, and keep `host` for nothing that carries a clear-text password.

**Figure 3. How a sign-in travels, and where the password is in clear text.** The browser's hop, the proxy's hop and the directory's hop are TLS with the certificate verified. Between them the auth service connects to Postgres as the person with `sslmode=prefer` against a server that has no certificate, so the password crosses the compose network as typed; and a person connecting directly reaches the same `host ... ldap` rule from anywhere on the network.

![How a sign-in travels, and where the password is in clear text](diagrams/v6_1_review_signin_flow.svg)

<sub>Also as [PNG](diagrams/v6_1_review_signin_flow.png) · editable source [`v6_1_review_signin_flow.drawio`](diagrams/v6_1_review_signin_flow.drawio)</sub>

**S-17 · Medium · Verified · New — The secure default lives in compose, not in the services.**
`GuardSettings.from_env` reads `AUTH_ENABLED` with default `False` (`guard.py:124`); so do `api/settings.py:157`, `console/settings.py:125`, `review/settings.py:227`, and the GUI's nginx fragment (`gui/10-nl2sql-config.envsh:21`, `${AUTH_ENABLED:-false}`). Sign-in is on by default only because `docker-compose.yml` says `${AUTH_ENABLED:-true}` eight times. A service started any other way (a bare `python -m nl2sql_agent.api`, a different compose file, Kubernetes) is open. The first cycle's X1 asked for services that refuse to start open unless told to; 6.0 made the default on for compose and off in the code.

**S-18 · Medium · Verified · New — A session cannot be revoked.**
A session is a signed token valid for `AUTH_SESSION_HOURS` (8). `jti` is minted (`tokens.py:144`) and never stored or checked. Sign-out deletes the cookie (`auth/app.py:434-439`) and nothing else; a copy of the token keeps working. Changing one's own password (`:451-475`), an administrator setting someone's password (`:679-683`), locking an account and the directory's own lockout all leave existing sessions valid until expiry. The role recheck (`guard.py:297-329`) catches a removed person and a removed group within a minute, which is good and is not revocation: it answers "is this person still allowed" and never "is this token still theirs". The one revocation that exists is deleting the signing key, which signs everyone out (`keys.py:8-9`).

**S-19 · Medium · Verified · New — The static service token is a second identity with every role and no name.**
With sign-in on, `API_TOKEN`, `REVIEW_TOKEN` and `CONSOLE_TOKEN` are still accepted (`guard.py:281-284`) and yield `Identity(kind=SERVICE, roles=service_roles)`: `USERS` for the API (`api/app.py:126`), reviewers **and** curators for the review service (`review/app.py:187`, "as it always could do everything here"), the console's allowed roles. A holder of `REVIEW_TOKEN` can promote, fix, curate and delete, and the record says whatever `X-Reviewer` they sent (`author()`), which is the first cycle's C-10 and S-14 intact for this path. The tokens default empty, so the path is closed until someone configures one; the documentation presents them as the way for scripts (`README.md`, `agent/API.md:654`), so someone will.

**S-20 · Low · Verified · New — The per-address throttle trusts a header the client may have written.**
`_client()` (`auth/app.py:130-140`) uses the last hop of `X-Forwarded-For`, on the reasoning that the proxy appended it. The auth service is published on every interface for the desktop client (`docker-compose.yml:1031`); a client there has no proxy in front and the whole header is theirs, so the fifty-per-address limit is evaded by varying it. The per-name limit of five and the directory's lockout still hold, so this is an evasion of the second line, not the first.

**S-21 · Low · Verified · New — A secret on the command line.**
`launch.sh:313` passes the rolesync password as `psql -v rolesync_password=...` through `docker compose exec`; for the run it is in the argument list of `docker`, `compose` and `psql`, readable in `ps` on the host and in the container. `psql` reads variables from the environment (`-v name=:'VAR'` is not a thing, but `PGPASSWORD`-style passing through `-e` and `\set` from `\getenv` is), and `ldap_hba` already passes its inputs with `-e` (`launch.sh:318-323`).

**S-22 · Low · Verified · New — The cookie loses `Secure` behind a TLS terminator.**
`set_cookie` marks the cookie `Secure` when `X-Forwarded-Proto` or the scheme is `https` (`auth/app.py:143-144, 323`). Every nginx template sets `X-Forwarded-Proto $scheme` (`gui/nginx.conf.template:71` and its siblings), overwriting whatever a terminator in front sent. `GUI_TLS_ENABLED=false` is documented as "only behind something that terminates TLS itself"; in exactly that deployment `$scheme` is `http`, and the session cookie is set without `Secure`.

**S-23 · Low · Verified · New — The replica's upstream transport is insecure by default.**
`LDAP_UPSTREAM_STARTTLS` defaults `False` (`ldap/nl2sql_ldap/settings.py:165, 210`); with an `ldap://` primary, the bind account's password and every person's password passed through by `remoteauth` cross to the primary in clear, and `settings.warnings()` says so (`:312-317`) rather than refusing. `LDAP_UPSTREAM_TLS_VERIFY=false` is likewise a warning. The directory refuses clear-text binds to *itself* by default (`slapd.py:158`); it should hold its primary to the same rule unless told otherwise.

### Default posture, in one table (6.0.1 defaults)

| Surface | Default bind | Default auth | Default secret |
|---|---|---|---|
| API 8443 | all interfaces | session (`nl2sql_users`) | — |
| Web GUI 8080 | all interfaces | session | — |
| Review 8444, Review GUI 8081, Curate GUI 8083 | all interfaces | session (reviewers / curators) | — |
| Auth 8446 (sign-in, `/directory/v1`) | all interfaces | it *is* the sign-in; directory routes need `nl2sql_admins` | signing key generated, 0600, own volume |
| Console 8445, Console GUI 8082 | 127.0.0.1 | session (reviewers, curators) | — |
| Directory GUI 8084 | 127.0.0.1 (page); its API on 8446, all interfaces | session (admins) | — |
| MLflow front door 5001 | 127.0.0.1 | session, or Basic through `/auth/verify` | — |
| MLflow server 5000 | not published | none | — |
| LDAP 389/636 | not published | StartTLS required; Argon2; lockout after 5 | `LDAP_SERVICE_PASSWORD`, `LDAP_ADMIN_PASSWORD` generated |
| Retail Postgres 5432 | all interfaces | password; people by `ldap` in clear text; **no TLS** | owner `nl2sql`/`nl2sql`; **superuser `postgres`/`nl2sql`**; reader `nl2sql_reader`/`nl2sql_reader`; rolesync generated |
| chunkdb 5433, vectordb 5434 | all interfaces | password | `ragproc`/`ragproc` |
| feedbackdb 5435 | all interfaces | password | `feedback`/`feedback` |
| correctionsdb 5436, completionsdb 5437 | all interfaces | password | user name |
| snippetsdb 5438 | all interfaces | password | `snippets`/`snippets` |
| mlflowdb | not published | password | `mlflow`/`mlflow` |

**Figure 4. The compose topology at 6.0.1 defaults.** Twenty-three services and fourteen volumes. Every page and the API ask for a session; seven stores still listen on every interface with default passwords and the retail superuser's is known; the API's private key is in eleven containers; the directory alone is published nowhere and runs unprivileged.

![The compose topology at 6.0.1 defaults](diagrams/v6_1_review_deployment_topology.svg)

<sub>Also as [PNG](diagrams/v6_1_review_deployment_topology.png) · editable source [`v6_1_review_deployment_topology.drawio`](diagrams/v6_1_review_deployment_topology.drawio)</sub>

## 5. Mitigation, repair and improvement plan (implementation)

The first cycle's items with status, and the new ones. Any change to shipped images goes out as a new version with a changelog entry; published tags are never re-pushed.

### A. Hygiene

| # | Action | Addresses | Status / depends on | Effort |
|---|---|---|---|---|
| H1 | **Shared package** for the copied infrastructure, installed as a versioned package (not `COPY`); `nl2sql_identity` is its seed. | C-01, C-04, C-12 | Partial | M |
| H2 | **Error taxonomy**; cut the 78 broad catches. | C-02 | Open | M |
| H3 | **Routers instead of closures**, now four services. | C-03 | Open | M |
| H4 | **Single engine accessor; `SET LOCAL`.** | C-04 | Open | S |
| H5 | **`extra="forbid"` on every wire model.** | C-04 | Open | S |
| H6 | **Comment policy**; fix "fourteen pipeline nodes". | C-05 | Open | S |
| H7 | **Pin and hash** Python dependencies per image; pin the Alpine packages the directory image installs. | C-07, S-12 | Open | S |
| H8 | **Orchestration to Python**; the auth service owns `auth_roles.sql` and the hba rewrite. | C-06 | Open; worse | L |
| H9 | **Reviewed coverage exclusions.** | C-08 | Open | S |
| H10 | **Sequences for fix ids** (feedback upsert withdrawn). | C-09 | Open, narrowed | S |
| H11 | **Shared front-end workspace**, now including `session.ts` and `SignInGate.tsx`. | C-01 | Open; worse | M |
| H12 | **New. No permanently red test**: `xfail(strict=True)` with the reason, or a provenance assertion, or a data change; a suite test that fails if any test is marked as expected-to-fail without a reason. | C-11 | — | S |
| H13 | **New. A stack-level acceptance tier**: a test behind `--run-docker` (or a `make accept`) that runs `start.sh` with every page against freshly built images on a private compose project, signs in as the generated administrator, asks a question through the web proxy, runs the console, promotes a pair, opens MLflow's door, and tears down. The "Checked live" prose of each changelog entry is its specification. | C-13 | — | M |

### B. Containers and microservices

| # | Action | Addresses | Status / depends on | Effort |
|---|---|---|---|---|
| K1 | **Rotate the dataset's superuser secret**: set `postgres` and owner passwords at first start, publish `nl2sql-retail-postgres:v1_2`, pin it. | M-08, S-08 | **Open; first** | S |
| K2 | **Non-root everywhere**, using the directory image's `become` pattern or per-service users that own their key. | M-01 | Open; **after K7** (M-11) | M |
| K3 | **Loopback by default for every store.** | M-04, S-08 | Open | S |
| K4 | **Generated passwords** for every store and role, and the three tokens when set. | M-06, S-10 | Partial (three sign-in secrets) | S |
| K5 | **Compose hardening block.** | M-03 | Open; after K2 | M |
| K6 | **Digests**; one Alpine tag. | M-02, M-12, S-12 | Open | S |
| K7 | **Separate TLS identities; certificates only into proxies.** The prerequisite for K2; the directory image is the template. | M-05, M-11, S-09 | Open; **do before K2** | S |
| K8 | **One proxy image**; one health check that verifies (loopback HTTP health port, or `--cacert` with the volume's certificate). | M-07, S-15 | Open; worse | M |
| K9 | **Compose `secrets:`**; the `*_FILE` readers already exist in two packages. | M-06, S-10 | Partial | S |
| K10 | **Provenance in publishing.** | M-09 | Open | M |
| K11 | **Store consolidation.** | M-07 | Open | L |
| K12 | **New. The directory API behind the same binding as its page**: a second listener on loopback for `/directory/`, or a separate port, or the auth service split so `/auth/token` alone is on every interface. | M-10 | — | S |
| K13 | **New. TLS for the retail database**: a certificate volume generated on first start (as `ldap/nl2sql_ldap/tls.py` does), `ssl=on`, mounted read-only where it is verified; part of the `v1_2` dataset image or applied by the start-up step that already rewrites `pg_hba.conf`. | S-16 | with K1 | S |

### C. Security controls

| # | Action | Addresses | Status / depends on | Effort |
|---|---|---|---|---|
| X1 | **Services refuse to start open unless told**: `AUTH_ENABLED` defaults `true` in `guard.py`, the three settings modules and the six nginx fragments; `--no-auth` sets it false explicitly. | S-17 (was S-01) | Open in the code; done in compose | S |
| X2 | **Constant-time comparison.** | S-02 | **Done** | — |
| X3 | **Query-string token scoped**; scrub `access_token=` from the access log. | S-03, S-13 | Scoped; log filter open | S |
| X4 | **CORS default to none** for API and review, as the console and auth service do. | S-04 | Open | S |
| X5 | **Operator-only detail.** | S-05 | Open | S |
| X6 | **Bound the queue and rate-limit.** | S-07 | Open | S |
| X7 | **Time-box `EXPLAIN`.** | S-07 | Open | S |
| X8 | **Role-level limits** for the service roles (reader, snippet reader, feedback writer): `statement_timeout`, `work_mem`, `idle_in_transaction_session_timeout`, `CONNECTION LIMIT`; the person roles have the first and last. | S-11 | Partial | S |
| X9 | **Identity seam.** | S-06, S-14 | **Done** | — |
| X10 | **Security test tier**, seeded with `tests/auth`'s posture tests and: every store bound to loopback, no `host ... ldap` rule without `hostssl`, every Dockerfile with `USER` or a documented drop, `AUTH_ENABLED` default `true` in every settings module, no secret in a command line in the scripts, every `/v1` route guarded (A18). | all S | Open | M |
| X11 | **New. `hostssl` for the sign-in rule and `verify-full` for the auth service's connection** once the database has a certificate (K13); until then, say in the docs that direct connection by people is for the compose network only (P14). | S-16 | K13 | S |
| X12 | **New. Session revocation**: record `jti` with its expiry in Postgres (the guard's recheck connection and the auth service's rolesync connection both reach it); write a row on sign-out, on a password change or set, on lock and on removal; the guard's `current()` checks it with the same once-a-minute cache, or per request for writes. | S-18 | — | M |
| X13 | **New. Service tokens that are someone**: a token names a principal (`service:benchmark`) and holds the roles it is granted, recorded on every action; `X-Reviewer` is never read as the author. | S-19 | — | S |
| X14 | **New. Trusted-proxy list**: `_client()` uses the socket peer unless the peer is in `AUTH_TRUSTED_PROXIES` (the GUIs' network), in which case the last forwarded hop. | S-20 | — | S |
| X15 | **New. The rolesync password off the command line**: pass it with `-e` and read it in SQL with `\getenv`, or let the auth service create its own role on first start. | S-21 | — | S |
| X16 | **New. Honour a terminator's `X-Forwarded-Proto`** (`proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto` when present, else `$scheme`), or set the cookie `Secure` whenever `GUI_TLS_ENABLED` is on anywhere in the chain. | S-22 | — | S |
| X17 | **New. The replica refuses a plain `ldap://` primary** unless `LDAP_UPSTREAM_ALLOW_CLEARTEXT=true`, mirroring the directory's own `LDAP_REQUIRE_TLS`. | S-23 | — | S |

Effort key: S under a day, M one to three days, L a week or more.
