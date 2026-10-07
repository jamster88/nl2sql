# Adversarial review, v6.x cycle: the implementation

**Reviewed at:** commit `a625cf0` (release 5.6.1), branch `misc/adversary-review/v6_x`, 2026-10-03.
**Review tag:** `v6_x_review`.
**Companion documents:** [`v6_x_review_architecture_as_documented.md`](v6_x_review_architecture_as_documented.md), [`v6_x_review_architecture_as_implemented.md`](v6_x_review_architecture_as_implemented.md), [`v6_x_review_mitigation_plan.md`](v6_x_review_mitigation_plan.md), [`v6_x_review_summary.md`](v6_x_review_summary.md).

## Scope and method

Three lenses, as requested:

- **A. Code hygiene** — clean code, comments, encapsulation, duplication, error handling, dependency management, tests.
- **B. Containers and microservices** — image construction, runtime hardening, composition, secrets, publishing.
- **C. Security controls** — authentication, authorisation, transport, data exposure, denial of service, supply chain. **LLM-specific concerns (prompt injection, retrieved text as instructions, model-side exfiltration) are excluded by request** and will be reviewed separately.

Method: static reading of the Python packages, the four TypeScript clients, the Java client, every Dockerfile, `docker-compose.yml`, the shell scripts and the test suite's layout; counts were taken with `grep` at the reviewed commit and are reproducible from the cited paths. Nothing was executed; nothing in the sibling `fireworks_nl2sql` folder was consulted.

Finding IDs are `C-nn` (hygiene), `M-nn` (containers/microservices) and `S-nn` (security). Severity is Critical / High / Medium / Low. Confidence is **Verified** (read in the code) or **Inferred**.

## 1. Verdict

Module by module this is a well-kept codebase: typed, documented beyond the norm, with a 100% statement-and-branch gate on every language in the repository and live tests for the things that matter most (role privileges, real Postgres, real TLS). The weaknesses are systemic rather than local. Hygiene suffers from copy-and-extend growth: five copies of the embedder protocol, six of the vector literal, three settings loaders, three token checks, four proxy images, and 78 bare `except Exception` clauses across the two services. Container practice is a generation behind the application code: every application image runs as root, every base image floats on a tag, no container has a resource limit, a read-only root filesystem or a dropped capability, and a bind mount lets a root process write into the source tree. The security controls inside the data path are strong (reader role, validator, planner gate, RLS writer fence); the controls around it default to off: no token on any service, seven databases on every interface with passwords equal to their user names, a published image whose Postgres superuser password is `nl2sql`, permissive CORS, a query-string token honoured on every route, and non-constant-time token comparison.

The single most consequential item is **S-08/M-08**: the published retail image carries a known superuser password and the compose default publishes its port to the network. Everything the reader role protects against is bypassed by connecting as `postgres`.

## 2. A. Code hygiene

### Strengths

- Docstrings explain intent and history; a reader can reconstruct why nearly every decision was made without the changelog.
- Types are used throughout Python and TypeScript; the Java side compiles with `-Xlint:all -Werror`.
- Every language has a 100% statement-and-branch gate (`coverage`, vitest v8, JaCoCo) and the shell has a measured coverage tool; the suite is roughly 3,400 Python test functions plus the four vitest suites and 379 JUnit tests.
- Contract tests compare the TypeScript and Java wire types against the pydantic models.
- Live tests exist for the least-privilege roles, real pgvector loads and real HTTPS.

### Findings

**C-01 · Medium · Verified — Duplication by copy-and-extend.**
Non-test code, confirmed by search:
- `Embedder` protocol ×5: `agent/nl2sql_agent/retrieval.py:55`, `examples.py:90`, `snippets.py:83`, `review/nl2sql_review/corrections.py:99`, `rag/ragproc/embedder.py:14`. `build_embedder` ×3: `retrieval.py:59`, `examples.py:480`, `rag/ragproc/embedder.py:88`.
- pgvector literal ×6: `retrieval.py:213`, `examples.py:492`, `corrections.py:425`, `rag/ragproc/vector_store.py:120`, `rag/ragproc/snippets.py:492`, `rag/ragproc/golden_vectors.py:127`.
- Environment helpers ×3 families: `agent/nl2sql_agent/config.py:53–86`, `review/nl2sql_review/settings.py:72–98`, `rag/ragproc/config.py:24` (plus `_env_tuple` again in `api/settings.py:39`).
- API scaffolding ×2: `FALLBACK_CODES` (`api/app.py:76`, `review/app.py:152`); `Health`/`Readiness`/`ApiError` (`api/models.py:372–396`, `review/models.py:579–596`); `_redacted` (`review/app.py:1460`, `review/server.py:131`); `json_safe` (`console/query.py:115`, `review/validation.py:104`).
- Token check ×3: `api/app.py:310`, `console/app.py:169`, `review/app.py:369`.
- Front ends ×4: `api/client.ts`, `ApiError`, `isErrorBody`, `vite.config.ts` proxy block, `10-*.envsh`, `nginx.conf.template`, Dockerfile; `plainText` in `gui/src/api/text.ts` and `review/gui/src/api/text.ts`, and again as `Markup.plain` in Java; `counted` in two TS packages.
*Why it matters:* every security fix to the token check, every change to the error envelope and every embedder change must be made three to six times, and the tests give no signal when one copy is missed.

**C-02 · Medium · Verified — Broad exception handling.**
`except Exception` appears 54 times in `agent/nl2sql_agent` and 24 times in `review/nl2sql_review` (search at the reviewed commit). Many are deliberate degradation points (a retriever that fails should not fail the question) and each records a message, which is good practice. But the pattern is uniform: the same clause wraps a model call, a database error, a JSON decode and a programming error, and all four become a string in `retrieval_errors` or `detail`. A `TypeError` from a refactor is indistinguishable from an unreachable host. There is no error taxonomy (transient / configuration / bug) and no re-raise for the last category.

**C-03 · Medium · Verified — Oversized units.**
`review/nl2sql_review/app.py` 1,488 lines with `create_app` from line 277 (about 1,200 lines in one closure); `graph.py` 1,176; `present.py` 1,089; `launch.sh` 1,156; `docker-compose.yml` 970; `setup.sh` 839; `start.sh` 851; `api/app.py` 720. The closure-factory style (routes defined inside `create_app` so they can close over injected collaborators) is the direct cause of the three-way duplication in C-01 and prevents router-level reuse and testing.

**C-04 · Medium · Verified — Encapsulation leaks.**
`schema_retrieval.py:98` reads `database._engine` ("should move there" in the comment); `literals.py:158–166` falls back to `_engine`; `Database.engine` is public. The retrievers set `SET statement_timeout` at session scope on pooled connections (`examples.py:477`, `retrieval.py:177`, `snippets.py:231`) while the executor correctly uses `SET LOCAL` (`database.py:312`). `translate.answer_from_state` builds `TraceEntry(**t)` into a model that silently ignores unknown keys (`api/translate.py:88`, `api/models.py:179`), while two other models in the same file are `extra="forbid"` (`api/models.py:54`, `270`); strictness is inconsistent and the lenient case is the one that lost data (I-03 in the companion review).

**C-05 · Low · Verified — Comments carry version history and go stale.**
Comments such as "plain text from agent 5.1.1 on", "the narrator's prompt is 5.5.1's again" and the header of `gui/src/api/useAsk.ts` ("running through fourteen pipeline nodes" when the graph has eighteen) encode release history in code. The changelog is the right home; a comment that names a version is a comment that will be wrong. The "should move there" note in `schema_retrieval.py` is a TODO without a tracker.

**C-06 · Medium · Verified — Shell as the orchestration language.**
2,846 lines across `setup.sh`, `launch.sh` and `start.sh`: grep-based `.env` parsing (`compose_env`), `.env` rewriting with a `.env.bak` copy (`setup.sh:376`), document-hash comparison executed inside containers, loaders run via `--entrypoint sh -c`, a Python routing probe run inside the agent image. The tests are thorough and the shell-coverage tool is a genuine achievement, but the quoting and parsing rules of `.env` (values containing `=`, `#`, spaces, quotes) are reimplemented rather than delegated to compose, and the logic cannot be unit-tested without a fake `docker`.

**C-07 · Medium · Verified — Dependency pinning is inconsistent and never hashed.**
`agent/requirements.txt`: 10 exact pins, 1 range. `review/requirements.txt`: 3 exact, 4 ranges. `rag/requirements.txt`: 0 exact, 5 ranges. `tests/requirements.txt`: 1 exact, 1 range. `data_gen/requirements.txt`: 0 exact, 3 ranges. No `--hash` anywhere. Node uses lockfiles with `npm ci` (good); Maven pins every version (good). A rebuild of the review or RAG image on a different day installs different code.

**C-08 · Low · Inferred — Coverage-shaped tests.**
Several tests exist to reach a branch rather than to assert a behaviour: the desktop `Tls` methods take a `storeType`/`protocol` parameter "only so the failure below can be reached from a test"; `tests/rag/test_golden_pairs_parser.py` computes its expected pair count from the same regex the parser uses; a 100% gate on both branches of every `except Exception` encourages tests of the catch rather than of the cause. None of this is wrong; it is the known cost of a hard 100% rule, and the plan should allow explicit, reviewed exclusions where a test would be tautological.

**C-09 · Low · Verified — Hand-rolled persistence idioms.**
Fix ids as `max(...) + 1` under a table lock (`corrections.py:293`, `319`); feedback verdicts as delete-then-insert (`feedback.py`) though the writer role has column `UPDATE` (`store.py:275`). Sequences and `ON CONFLICT` are the idiom.

**C-10 · Low · Verified — Self-asserted attribution.**
`X-Reviewer` is read as a header and used as `reviewer or found.reviewer` (`review/app.py:799`, `854`, `969`, `996`). Fine for one reviewer; a correctness problem the moment there are two.

## 3. B. Containers and microservices

### Strengths

- Multi-stage builds everywhere a build toolchain is needed; the shipped stages are slim (`python:3.12-slim`, `nginx:1.29-alpine`, `alpine:3.21`).
- `--platform=$BUILDPLATFORM` build stages and multi-arch publishing for every image.
- Healthchecks on every long-running service; the proxies verify the upstream certificate (`proxy_ssl_verify on`).
- Volumes are declared; the dataset is a published image rather than a build step at install time; published tags never move and a test asks Docker Hub whether every pinned tag exists.
- The catalog and TLS material are mounted read-only where they are mounted for reading.

### Findings

**M-01 · High · Verified — Every application image runs as root.**
`grep -c '^USER'` is 0 for `agent/Dockerfile`, `review/Dockerfile`, `gui/Dockerfile`, `review/gui/Dockerfile`, `curate/Dockerfile`, `console/Dockerfile`, `desktop/Dockerfile`, `docker/mlflow/Dockerfile`, `docker/mlflowdb/Dockerfile` and `docker/apitest/Dockerfile`. Only `docker/Dockerfile` (the Postgres dataset image) switches user. Combined with the read-write bind mount of `./context_questions` into the review container (`docker-compose.yml:597`), a promotion writes root-owned files into the developer's checkout.

**M-02 · Medium · Verified — Base images float on tags.**
`python:3.12-slim` (agent, review), `postgres:18` (mlflowdb), `pgvector/pgvector:pg18` (stores), `nginx:1.29-alpine` (four proxies), `node:26-alpine` (four build stages), `alpine:3.21` (apitest, desktop ship stage), `maven:3.9.16-eclipse-temurin-21`, `ghcr.io/mlflow/mlflow:v3.16.1-full`. None is pinned by digest. "Tags never move" is true of this project's tags and false of its inputs; a rebuild of `v5_6_1` is not reproducible.

**M-03 · High · Verified — No runtime hardening in compose.**
`docker-compose.yml` has no `deploy.resources`/`mem_limit`/`cpus`, no `read_only: true`, no `cap_drop`, no `security_opt: [no-new-privileges:true]`, no `user:`, and `restart: unless-stopped` on every service. A model that degenerates or a query that the planner gate misjudges can take the host's memory, and a compromised process has the full default capability set.

**M-04 · High · Verified — Seven Postgres servers published on every interface with default credentials.**
Lines 21, 46, 72, 110, 145, 175, 209 publish ports 5432–5438 with no bind address; passwords default to `nl2sql`, `ragproc`, `feedback`, `corrections`, `completions`, `snippets` (lines 12, 69, 106, 141, 171, 205). `mlflowdb` is internal but defaults to `mlflow`/`mlflow` (line 815). The console and MLflow show the right pattern (`${CONSOLE_BIND_ADDRESS:-127.0.0.1}`, line 743; `${MLFLOW_BIND_ADDRESS:-127.0.0.1}`, line 871); the databases do not follow it.

**M-05 · Medium · Verified — Private key distributed to containers that only need the certificate.**
The `apitls` volume (read-write at line 429 for the API) holds `server.key` and is mounted into gui, review, reviewgui, curategui, console, consolegui and apitest (lines 494, 588, 648, 686, 746, 794, 907). The review service and console present the API's key as their own.

**M-06 · Medium · Verified — Secrets as environment, duplicated on disk.**
Tokens and passwords are passed through `environment:` (visible in `docker inspect` and `/proc`), read from `.env`, and copied to `.env.bak` on every `setup.sh` run (`setup.sh:376`). Database URLs carry passwords inline (`API_FEEDBACK_DB_URL=postgresql://nl2sql_feedback_writer:...`). No compose `secrets:`, no file-based secrets, no rotation procedure. `_redacted` exists for logs, which is good, and should be the norm everywhere a URL is printed.

**M-07 · Medium · Verified — The microservice shape without its benefits.**
Four proxy images that differ by two environment variables (I-14 in the companion review); one agent image with three entry points (CLI, API, console); a review image that also carries the RAG loaders and the documents; all app images released together at one version by rule. Healthchecks are Python one-liners with `ssl._create_unverified_context()` in compose (lines 441, 605, 754) and duplicated as a `HEALTHCHECK` in `review/Dockerfile:53` while `agent/Dockerfile` has none. These are symptoms of growth by copy, and each copy is a place to diverge.

**M-08 · Critical · Verified — The published dataset image carries a known superuser password.**
`docker/init_db.sh:34–35` runs `ALTER ROLE postgres PASSWORD '${DB_PASSWORD}'` and creates the owner with the same password at **image build time**; `DB_PASSWORD` defaults to `nl2sql` (`docker-compose.yml:12`). `init_db.sh:23` appends `host all all all scram-sha-256` to `pg_hba.conf`. The image is published on Docker Hub (`nl2sql-retail-postgres:v1_1`) and compose publishes its port on every interface by default (M-04). Anyone on the network can connect as `postgres`, which makes the reader role, the validator and the planner gate irrelevant for the retail data and lets an attacker alter the data the agent is measured against.

**M-09 · Low · Verified — No provenance in the publish path.**
Publishing is a documented sequence of `docker buildx` commands; `tests/docker/test_published_images.py` checks that tags exist and are multi-arch, not that they were built from a given commit. No SBOM, no signature, no vulnerability scan.

## 4. C. Security controls (non-LLM)

### Strengths

- **Database least privilege** is the best-implemented control in the repository: `nl2sql_reader` (`SELECT` only, `default_transaction_read_only`, `CONNECT` revoked on other databases, `pg_cancel_backend`/`pg_terminate_backend` revoked from `PUBLIC`), `nl2sql_feedback_writer` with column grants and RLS fencing to pending rows, `snippets_reader` with `SELECT` only; all tested live.
- **Statement controls**: pglast AST validation (one statement, SELECT only, function denylist including `pg_sleep`, `pg_read_file`, `lo_import`, `dblink`; table allowlist), planner-gate cost ceiling, per-transaction `statement_timeout`, row cap with `truncated`.
- **Transport**: HTTPS on every service by default with an explicit `API_TLS_ALLOW_SELF_SIGNED` off switch; proxies verify upstream; the desktop client offers CA file or pinned fingerprint and labels `--insecure` loudly.
- **Output**: HTML escaping in `render_answer`; the clients un-escape only the three entities the escaper produces rather than using `innerHTML`; the formula evaluator is an AST whitelist.
- **Warnings** on insecure configuration are printed by every settings module and by `launch.sh`.

### Findings

**S-01 · High · Verified — Authentication is off by default on every service.**
`API_TOKEN`, `REVIEW_TOKEN`, `CONSOLE_TOKEN` default empty (`docker-compose.yml:405`, `542`, `736`); `authenticate` returns early when the token is unset (`api/app.py:306–307`); the review service only warns (`review/nl2sql_review/settings.py:282–286`) although its README says the token is "not optional". The API and three GUIs publish on every interface by default.

**S-02 · Medium · Verified — Token comparison is not constant-time.**
`if presented != api.token` (`api/app.py:310`), `if presented != console.token` (`console/app.py:169`), `if presented != config.token` (`review/app.py:369`). No use of `hmac.compare_digest` or `secrets` anywhere in the three services. Practical exploitability over a network is low; the fix is one line in each place (or one line in a shared dependency).

**S-03 · Medium · Verified — The query-string token is honoured on every route, not only the event stream.**
`authenticate` accepts `access_token` as a `Query` parameter (`api/app.py:298–309`) and is the single `guarded` dependency attached to every `/v1` route (`api/app.py:318`, applied at 389, 433, 482, 506, 531, 617, 664, 700). API.md and the GUI comments describe it as accepted "for this one route". A token in a URL is a token in access logs, browser history and `Referer` headers; the surface should be the SSE route only, and the server's access log should scrub it.

**S-04 · Medium · Verified — Permissive CORS by default.**
API `cors_origins` defaults to `("*",)` (`api/settings.py:85`, `144`); review the same (`review/settings.py:137`, `213`); the console correctly defaults to none (`console/settings.py:90`, `113`). With no token (S-01) and `*`, any web page can query the API from a visitor's browser on the same network.

**S-05 · Medium · Verified — Information disclosure in normal responses.**
Answers carry the SQL, `plan_cost`, per-node `detail` strings (which include exception text), and `retrieval_errors` (raw error messages from stores and the model host). `/readyz` returns per-dependency detail. Database URLs are redacted in readiness via `_redacted`, but exception strings elsewhere are not filtered. Decide which of these are operator-only and gate them (or trim them) accordingly.

**S-06 · Medium · Verified — No tenancy: any token holder sees every job.**
`GET /v1/questions` lists all jobs (`api/app.py:485–486`); jobs can be cancelled by id by any holder. With a single shared token this is by design, and it is the design that needs changing (I-06 in the companion review).

**S-07 · High · Verified — Denial of service by queue growth.**
`JobStore._prune_locked` only removes finished jobs (`api/jobs.py:308–327`); queued jobs are unbounded; the worker pool is two; there is no rate limit or queue-depth rejection. With S-01 the attacker needs no token. `explain_plan` has no statement timeout (`database.py:272–296`), so the console's Plan mode and the planner gate can be held by a slow-to-plan query.

**S-08 · Critical · Verified — Databases reachable from the network with known passwords, including a superuser.**
See M-04 and M-08. This is the headline finding: the data-path controls are excellent and the perimeter default makes them optional.

**S-09 · Medium · Verified — One TLS key for three services, copied to seven containers.**
See M-05. A proxy compromise yields the API's, the review service's and the console's private key.

**S-10 · Medium · Verified — Secret handling.**
See M-06: environment-borne secrets, `.env.bak` duplication, inline passwords in URLs, default passwords equal to user names, no rotation story.

**S-11 · Low · Verified — Role-level limits absent.**
No `statement_timeout` or `work_mem` on the reader role (`docker/reader_role.sql`); the executor's `SET LOCAL` protects the agent's path only. A direct connection as the reader (its password defaults to its user name) has no limits at all. Defence in depth suggests `ALTER ROLE nl2sql_reader SET statement_timeout`, `SET work_mem`, `SET idle_in_transaction_session_timeout`, and `CONNECTION LIMIT`.

**S-12 · Medium · Verified — Supply chain.**
See C-07 and M-02: unpinned Python ranges without hashes, floating base-image tags, no SBOM or signing.

**S-13 · Low · Inferred — Logging of secrets.**
With a query-string token (S-03), uvicorn's access log records it. Nginx in front strips nothing it did not add. Add a log filter that masks `access_token=` and never log `Authorization`.

**S-14 · Low · Verified — Non-repudiation.**
Promotions and fixes are attributed to a self-asserted header (C-10); no authenticated identity exists to attribute to.

**S-15 · Low · Verified — Healthchecks disable verification.**
`ssl._create_unverified_context()` in compose lines 441, 605, 754 and `review/Dockerfile:53`. Acceptable on loopback inside the container; worth replacing with a plain-HTTP health port or a check that trusts the volume's certificate, so the pattern does not spread.

### Default posture, in one table

| Surface | Default bind | Default auth | Default secret |
|---|---|---|---|
| API 8443 | all interfaces | none | — |
| Web GUI 8080 | all interfaces | none (proxy holds empty token) | — |
| Review 8444, Review GUI 8081, Curate GUI 8083 | all interfaces | none | — |
| Console 8445, Console GUI 8082 | 127.0.0.1 | none | — |
| MLflow 5001 | 127.0.0.1 | none (MLflow has no login) | — |
| Retail Postgres 5432 | all interfaces | password | `nl2sql` / `nl2sql`; superuser `postgres` / `nl2sql` |
| chunkdb 5433, vectordb 5434 | all interfaces | password | `ragproc` / `ragproc` |
| feedbackdb 5435 | all interfaces | password | `feedback` / `feedback` |
| correctionsdb 5436, completionsdb 5437 | all interfaces | password | user name |
| snippetsdb 5438 | all interfaces | password | `snippets` / `snippets` |

## 5. Mitigation, repair and improvement plan (implementation)

Grouped by lens, ordered within each group so that each step has what it needs. Any change to shipped images goes out as a new version with a changelog entry; published tags are never re-pushed.

### A. Hygiene

| # | Action | Addresses | Depends on | Effort |
|---|---|---|---|---|
| H1 | **Shared package for the infrastructure that is copied**: env helpers, `Embedder`/`build_embedder`, `vector_literal`, error envelope + `FALLBACK_CODES`, `Health`/`Readiness`/`ApiError`, `_redacted`, `json_safe`, `authenticate` factory with `hmac.compare_digest`. Install it into the review and RAG images from the agent wheel (or a top-level `common/` package both images copy). | C-01, C-04, S-02 | — | M |
| H2 | **Error taxonomy**: `TransientError`, `ConfigurationError`, and let everything else propagate (or be caught once at the node wrapper and re-raised after recording). Replace `except Exception` in retrievers with `except (TransientError, DBAPIError, httpx.HTTPError)`; keep one broad catch at the graph's `_traced` wrapper that records and re-raises programming errors. Target: well under half the current 78. | C-02 | H1 | M |
| H3 | **Routers instead of closures** for the three FastAPI apps (A8 in the implemented-architecture review); `create_app` assembles. | C-03 | H1 | M |
| H4 | **Single engine accessor and `SET LOCAL`** in retrievers (A7). | C-04 | H1 | S |
| H5 | **`extra="forbid"` on every wire model** in `api/models.py` and `review/models.py`, with a test that every state field appears in the API model or in an explicit "not exposed" list. | C-04 | — | S |
| H6 | **Comment policy**: no version numbers in code comments (history goes in `CHANGELOG.md`); TODOs carry an issue reference; a docs test greps for `\b5\.\d(\.\d)?\b` in comments outside `__version__` and for the known stale phrases. Fix the "fourteen pipeline nodes" comment. | C-05 | — | S |
| H7 | **Pin and hash Python dependencies** with `pip-compile --generate-hashes` (or `uv pip compile`) per image; keep a loose `requirements.in`. Add a Dependabot/Renovate configuration for the pins and the base images. | C-07, S-12 | — | S |
| H8 | **Move orchestration to Python** (A15): a `nl2sql_ops` CLI owning `.env` reconciliation (using compose's own parser via `docker compose config`), pins, store reconciliation and loader calls; thin shell wrappers remain for discoverability. | C-06 | H1, H10 | L |
| H9 | **Allow reviewed coverage exclusions** (`# pragma: no cover -- unreachable: <reason>`) for branches that exist only to satisfy the gate, and remove the test-only parameters they required. | C-08 | — | S |
| H10 | **Sequences and upserts** (A17). | C-09 | — | S |
| H11 | **Shared front-end workspace** (`web/packages/client`): `ApiError`, `request`, `plainText`, `counted`, the vite proxy factory; the four apps import it. Port the Java `Markup` to the same three-entity rule by contract test. | C-01 (front ends) | — | M |

### B. Containers and microservices

| # | Action | Addresses | Depends on | Effort |
|---|---|---|---|---|
| K1 | **Rotate the published dataset's superuser secret**: stop setting the `postgres` password at build time; set it from `POSTGRES_PASSWORD` at first start (the official image's entrypoint already does this) or generate it into a secret file; publish `nl2sql-retail-postgres:v1_2` with the change and a changelog entry; `setup.sh` pins it. Until then, bind 5432 to loopback (K3). | M-08, S-08 | — | S |
| K2 | **Non-root everywhere**: `USER` in every application Dockerfile (nginx's unprivileged variant or `nginx-unprivileged`; a `nl2sql` user in the Python images; the desktop ship stage needs none). Chown the mounted `context_questions` directory to that uid, or write through a volume and sync (A11). | M-01 | — | M |
| K3 | **Loopback by default for every store**: `"${DB_BIND_ADDRESS:-127.0.0.1}:${X_PORT:-nnnn}:5432"` for all seven; document `DB_BIND_ADDRESS=0.0.0.0` as the opt-in; `launch.sh`/ops warns when it is opened without non-default passwords. | M-04, S-08 | — | S |
| K4 | **Generated passwords**: `setup.sh`/ops generates random passwords for every store and the reader/writer roles on first run (as it should for tokens), writes them to `.env` with mode 0600, and never copies secrets into `.env.bak` (back up non-secret keys only, or encrypt). | M-06, S-10 | H8 (or do it in shell first) | S |
| K5 | **Compose hardening block** on every service: `read_only: true` with `tmpfs` for writable paths, `cap_drop: [ALL]` (+ `NET_BIND_SERVICE` for nginx on <1024, not needed on 8080+), `security_opt: [no-new-privileges:true]`, `deploy.resources.limits` (memory per service, with the agent sized for its prompt and pool), `pids_limit`. | M-03 | K2 | M |
| K6 | **Pin base images by digest** (`image@sha256:...`) in every Dockerfile and compose `image:`; let Renovate/Dependabot bump them. | M-02, S-12 | H7 | S |
| K7 | **Separate TLS identities and mount only certificates into proxies** (A13). | M-05, S-09 | — | S |
| K8 | **One proxy image** parameterised by env (A14), and one healthcheck pattern that does not disable verification (an HTTP health listener on loopback, or `curl --cacert /etc/nl2sql/tls/server.crt`). Remove the duplicate `HEALTHCHECK` from `review/Dockerfile` or add the same to `agent/Dockerfile`, but not one of each. | M-07, S-15 | K7 | M |
| K9 | **Compose `secrets:`** for tokens and passwords (file-backed), read by the services from `/run/secrets/*` with the environment variable as fallback. | M-06, S-10 | K4 | S |
| K10 | **Provenance in publishing**: a `publish.sh`/ops command that builds with `--provenance`/`--sbom`, signs with cosign, and records the commit in an image label (`org.opencontainers.image.revision`); `tests/docker/test_published_images.py` checks the label against the tag's changelog entry. | M-09 | — | M |
| K11 | **Store consolidation** (A12), staged after K3 and K4 have made the current topology safe. | M-07 | K3, K4, owner decision | L |

### C. Security controls

| # | Action | Addresses | Depends on | Effort |
|---|---|---|---|---|
| X1 | **Require tokens by default**: `setup.sh`/ops generates `API_TOKEN`, `REVIEW_TOKEN` and `CONSOLE_TOKEN` on first run; services refuse to start with an empty token unless `*_ALLOW_ANONYMOUS=true` is set explicitly (mirroring `API_TLS_ALLOW_SELF_SIGNED`); the proxies already carry the token so the browser flow is unchanged; align `review/README.md`. | S-01 | K4 | S |
| X2 | **Constant-time comparison** via the shared `authenticate` (H1); until H1 lands, `hmac.compare_digest` in the three places. | S-02 | — | S |
| X3 | **Scope the query-string token** to the events route only (a second dependency used by `/v1/questions/{id}/events`), scrub `access_token=` from uvicorn access logs with a logging filter, and correct API.md. | S-03, S-13 | — | S |
| X4 | **CORS default to none** for API and review (as the console does); `setup.sh`/ops writes the GUI origins when the GUIs are enabled; keep the "token with `*`" warning. | S-04 | — | S |
| X5 | **Operator-only detail**: move exception text out of `detail`/`retrieval_errors` in answers unless `API_DEBUG_DETAIL=true`; keep machine codes (`node_errors: {narrator: "model_unreachable"}`) for clients; filter `/readyz` detail to booleans for unauthenticated callers. | S-05 | A2 | S |
| X6 | **Bound the queue and rate-limit** (A9): `max_queued`, 429 with `Retry-After`, per-token or per-address limiter (nginx `limit_req` in the proxy plus a server-side limiter for direct clients), published in `/v1/meta`. | S-07 | — | S |
| X7 | **Time-box `EXPLAIN`** (A5). | S-07 | — | S |
| X8 | **Role-level limits**: in `reader_role.sql` and the snippet/feedback role scripts, `ALTER ROLE ... SET statement_timeout`, `SET work_mem`, `SET idle_in_transaction_session_timeout`, `CONNECTION LIMIT`; revoke `EXECUTE` on the denylisted functions from the reader as a second layer where Postgres allows (`pg_sleep`, `lo_import`, `lo_export`); re-run the live least-privilege tests and add assertions for each. Record the new rows in the arch6 security table (documentation plan P4). | S-11 | — | S |
| X9 | **Identity seam** (A10) so that job listing, cancellation and reviewer attribution are per principal; `X-Reviewer` becomes a display name attached to an authenticated principal, not the identity. | S-06, S-14 | H1, owner decision | M |
| X10 | **Security test tier** (`tests/security/`): compose defaults (every store bound to loopback, tokens non-empty after setup, CORS not `*`), constant-time compare used, query token rejected on non-events routes, `reader_role.sql` matches the spec table, no `USER`-less Dockerfile, no floating base tag, no secret in `.env.bak`. Wire into the existing docs/compose test style. | all S | X1–X8 | M |

Effort key: S under a day, M one to three days, L a week or more.
