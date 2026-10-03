# v6.x combined mitigation, repair and improvement plan

**Built from:** the three v6.x adversarial reviews at commit `a625cf0` (release 5.6.1), 2026-10-03:
[`v6_x_review_architecture_as_documented.md`](v6_x_review_architecture_as_documented.md) (plan items P1–P12),
[`v6_x_review_architecture_as_implemented.md`](v6_x_review_architecture_as_implemented.md) (A1–A17),
[`v6_x_review_implementation.md`](v6_x_review_implementation.md) (H1–H11, K1–K11, X1–X10).
A one-page summary of the findings themselves is in [`v6_x_review_summary.md`](v6_x_review_summary.md).

## How this plan was built

1. **Merged** the three plans into one list and gave each item a stable id `V6-nn`.
2. **Removed redundancy.** Where two reviews asked for the same change from different angles, one item remains and the table lists every source it satisfies. Examples: the shared package (A6 + H1 + X2), the job-store bound (A9 + X6), `EXPLAIN` timeout (A5 + X7), TLS identity separation (A13 + K7), the proxy image (A14 + K8 + H11), orchestration in Python (A15 + H8), store consolidation (A12 + K11), the identity seam (A10 + X9), the sensitive-column decision (P2 + A3), the state-lifetime fix (A1 + A2 + P3's contract table).
3. **Ordered by dependency.** Upstream items (decisions, safe defaults, the shared package, state resets) come before the items that consume them (router refactor, identity, documentation of the as-built system, posture tests). Within a phase, items are independent and can run in parallel.
4. **Kept the repository's standing rules.** Specs are never edited in place; the design changes go into `Multi-Agent_NL2SQL_arch6.md`. Published image tags never move; every fix to a shipped image is a new version with a `CHANGELOG.md` entry. Model-specific detail stays out of the documentation. Nothing here touches `../fireworks_nl2sql`.

Effort key: **S** under a day, **M** one to three days, **L** a week or more. "Sources" names the plan items and findings each entry satisfies.

## Phase 0 — Decisions (owner; nothing else should wait on them longer than necessary)

| Id | Decision | Options and recommendation | Unblocks | Sources |
|---|---|---|---|---|
| V6-01 | **Identity model.** | (a) Keep one static token per service and say so as a design limit; (b) a `Principal` issued per token (several tokens per service, each naming a person), mapped to reviewer attribution and to `principal` → `SET ROLE`. **Recommend (b)**: it is the smallest step that makes attribution real and gives the RLS path a caller, and it is far cheaper now than after more services exist. | V6-21, V6-30, V6-40 | P1, D-05, I-06, S-06, S-14 |
| V6-02 | **Sensitive-column policy.** | (a) Implement it where data leaves the database (schema prompt, validator, renderer); (b) remove the claim, the parameters and the `redactions` field. **Recommend (b) now, (a) when a tagged column exists**: the retail schema has none, and a control nobody can exercise is a liability. | V6-12 | P2, D-04, I-01, I-05 |
| V6-03 | **Topology target.** | (a) Keep eight Postgres servers, justify it in arch6; (b) one pgvector cluster for every non-retail store (databases + roles), retail stays its own published image. **Recommend (b)**, staged over releases after the safe-default work in Phase 1. | V6-34 | P7, D-08, I-12, M-07 |
| V6-04 | **Review service write model.** | Keep markdown-first (decided). Decide whether loaders run in-process with a lock and the service runs as a non-root owner of the directory (recommended), and whether promotions should also create a git commit. | V6-27, V6-28 | P8, D-09, I-10 |
| V6-05 | **Benchmark independence wording.** | Already decided (B03/B14 remain). Only the documentation changes (V6-45). | V6-45 | P10, D-14 |

## Phase 1 — Safe defaults and secrets (independent of code structure; ship as the first v6 patch)

| Id | Action | Depends on | Effort | Sources |
|---|---|---|---|---|
| V6-06 | **Loopback by default for every store**: `${DB_BIND_ADDRESS:-127.0.0.1}` on the seven published Postgres ports; opening them is an explicit opt-in with a warning when passwords are defaults. | — | S | K3, M-04, S-08 |
| V6-07 | **Stop baking the superuser password into the dataset image**: set the `postgres` and owner passwords at first start from the environment or a secret file; publish `nl2sql-retail-postgres:v1_2`; pin it in `setup.sh`; changelog entry. | — | S | K1, M-08, S-08 |
| V6-08 | **Generated secrets on first setup**: random `API_TOKEN`, `REVIEW_TOKEN`, `CONSOLE_TOKEN` and store/role passwords written to `.env` (mode 0600); `.env.bak` keeps non-secret keys only. | — | S | K4, X1, M-06, S-10 |
| V6-09 | **Tokens required by default**: services refuse an empty token unless `*_ALLOW_ANONYMOUS=true`, mirroring `API_TLS_ALLOW_SELF_SIGNED`; `review/README.md` wording and the code finally agree. | V6-08 | S | X1, S-01, D-07 |
| V6-10 | **Constant-time token comparison** (`hmac.compare_digest`) in the three current places; superseded by the shared dependency in V6-15 but worth landing first because it is one line each. | — | S | X2, S-02 |
| V6-11 | **Scope the query-string token to the events route**, scrub `access_token=` from access logs, correct `agent/API.md`. | — | S | X3, S-03, S-13, D-07 |
| V6-12 | **CORS default to none** for API and review; setup writes the GUI origins when the GUIs are enabled. | — | S | X4, S-04 |
| V6-13 | **Bound the job queue and rate-limit**: `max_queued` with 429 + `Retry-After`, queued-job TTL, `limit_req` in the proxy, limits published in `/v1/meta`. | — | S | A9, X6, I-07, S-07 |
| V6-14 | **Time-box `EXPLAIN`** with its own `SET LOCAL statement_timeout`; expose in console meta. | — | S | A5, X7, I-04, S-07 |

## Phase 2 — Correctness fixes in the pipeline (independent of Phase 1; ship with it)

| Id | Action | Depends on | Effort | Sources |
|---|---|---|---|---|
| V6-15 | **Per-attempt state reset on the repair edge**: clear `audit`, `claims`, `narration_retries`, `chart`, `completeness` (and any other per-attempt key) when the loop re-enters `generate_sql`; declare field lifetimes in `state.py` as data the reset reads; graph test for semantic_issue → repair → one renarration on the new result. | — | S | A1, I-02, D-11 |
| V6-16 | **Node-failure channel** `node_errors` distinct from `retrieval_errors`; the narrator writes there; API, GUI and desktop types gain the field; API.md updated. | — | S | A2, I-02, D-12 |
| V6-17 | **Resolve the sensitive-column policy** per V6-02: remove (parameters, `redactions`, docs) or implement at the executor/prompt/validator layer with a tagged fixture column. | V6-02 | S (remove) / M (implement) | A3, I-01, D-04 |
| V6-18 | **Carry routing fields over REST**: `model`, `rung`, `route`, `hops` on the API `TraceEntry` with `extra="forbid"`; TypeScript and Java types; contract tests; API.md. Optionally a model summary in the feedback snapshot. | — | S | A4, I-03, D-07 |
| V6-19 | **`extra="forbid"` on every wire model** plus a test that every state field is either exposed or explicitly listed as not exposed. | V6-18 | S | H5, C-04 |

## Phase 3 — Shared foundations (upstream of every refactor)

| Id | Action | Depends on | Effort | Sources |
|---|---|---|---|---|
| V6-20 | **Shared Python package** (`nl2sql_common` or a `platform` sub-package installed into the review and RAG images): env helpers, `Embedder`/`build_embedder`, `vector_literal`, error envelope + `FALLBACK_CODES`, `Health`/`Readiness`/`ApiError`, `_redacted`, `json_safe`, and an `authenticate` dependency factory (constant-time, header-only by default, query-string opt-in per route). Replace every copy. | — | M | A6, H1, X2, I-09, C-01, S-02 |
| V6-21 | **`Principal` type and token→principal mapping** in the shared package, per V6-01. Today's single token becomes a single principal; the shape is ready for several. | V6-01, V6-20 | S | A10, X9 (first half) |
| V6-22 | **Single engine accessor; `SET LOCAL` in retrievers**: remove the `_engine` fallbacks, borrow connections through `Database`, no session-level `SET` on pooled connections. | V6-20 | S | A7, H4, I-08, C-04 |
| V6-23 | **Error taxonomy** (`TransientError`, `ConfigurationError`, propagate the rest; one recording catch in the node wrapper); cut the 78 broad catches to the deliberate degradation points. | V6-20 | M | H2, C-02 |
| V6-24 | **Pin and hash Python dependencies** per image (`requirements.in` → compiled with hashes); Renovate/Dependabot for pins and base images. | — | S | H7, C-07, S-12 |
| V6-25 | **Shared front-end workspace package** (`ApiError`, `request`, `plainText`, `counted`, vite proxy factory) consumed by the four apps; contract test that Java `Markup.plain` applies the same three-entity rule. | — | M | H11, C-01 |

## Phase 4 — Service refactors (consume Phase 3)

| Id | Action | Depends on | Effort | Sources |
|---|---|---|---|---|
| V6-26 | **Routers instead of `create_app` closures** in the API, console and review service; `create_app` assembles routers with injected dependencies from the shared package. Review first (largest), then API, then console. | V6-20 | M + S + S | A8, H3, I-11, C-03 |
| V6-27 | **Review service writes through a library with a lock**: in-process loader calls, advisory lock around a promotion, credentials as arguments not environment, per V6-04. | V6-04, V6-20 | M | A11, I-10, I-09 |
| V6-28 | **Non-root review container that owns the mounted directory** (or writes to a volume synced to the checkout), so promoted documents are not root-owned on the host. | V6-04, V6-31 | S | A11, M-01 |
| V6-29 | **Sequences and upserts** for fix ids and feedback verdicts. | — | S | A17, H10, I-17, C-09 |
| V6-30 | **Identity applied**: API passes the principal to `Nl2SqlAgent.run`; job listing and cancellation filter by principal; review records the principal and treats `X-Reviewer` as a display name; the RLS path has a caller. | V6-21, V6-26 | M | A10, X9, I-06, S-06, S-14 |
| V6-31 | **Non-root `USER` in every application image** (unprivileged nginx for the proxies; a `nl2sql` user in the Python images). | — | M | K2, M-01 |
| V6-32 | **Operator-only detail**: exception text behind `API_DEBUG_DETAIL`, machine codes in `node_errors` for clients, boolean-only `/readyz` for unauthenticated callers. | V6-16 | S | X5, S-05 |
| V6-33 | **Cache reload signal**: `Nl2SqlAgent.reload()` and an authenticated admin route the review service calls after a promotion. | V6-30 | S | A16, I-16 |

## Phase 5 — Container hardening and topology (consume Phase 4's non-root images)

| Id | Action | Depends on | Effort | Sources |
|---|---|---|---|---|
| V6-34 | **Compose hardening block** on every service: `read_only` + `tmpfs`, `cap_drop: [ALL]`, `no-new-privileges`, memory/pids limits. | V6-31 | M | K5, M-03 |
| V6-35 | **Pin base images by digest** in Dockerfiles and compose; bots bump them. | V6-24 | S | K6, M-02, S-12 |
| V6-36 | **Separate TLS identities**: own key pair for review and console; proxies receive certificates only. | — | S | A13, K7, I-13, M-05, S-09 |
| V6-37 | **One proxy image** parameterised by upstream/token/static root, with one healthcheck pattern that verifies TLS (or checks a loopback HTTP health port); remove the duplicate `HEALTHCHECK`. | V6-25, V6-36 | M | A14, K8, I-14, M-07, S-15 |
| V6-38 | **Compose `secrets:`** (file-backed) for tokens and passwords with environment fallback. | V6-08 | S | K9, M-06, S-10 |
| V6-39 | **Role-level limits** on the reader, snippet and feedback roles (`statement_timeout`, `work_mem`, `idle_in_transaction_session_timeout`, `CONNECTION LIMIT`, `REVOKE EXECUTE` on denylisted functions where possible); live tests extended. | — | S | X8, S-11, D-03 |
| V6-40 | **Store consolidation** per V6-03: one pgvector cluster hosting the six non-retail stores and MLflow as databases with per-consumer roles; one reader-role script; staged release by release. | V6-03, V6-06, V6-08 | L | A12, K11, I-12, D-08, M-07 |
| V6-41 | **Orchestration moves to Python** (`nl2sql_ops`): `.env` reconciliation via `docker compose config`, pins, store reconciliation inside the owning service's startup, loader calls; thin shell wrappers remain. | V6-27 | L | A15, H8, I-15, C-06 |
| V6-42 | **Provenance in publishing**: SBOM + provenance attestations, cosign signature, `org.opencontainers.image.revision` label checked by `tests/docker/test_published_images.py`. | — | M | K10, M-09 |

## Phase 6 — Documentation of the as-built system (after the behaviour it describes exists)

| Id | Action | Depends on | Effort | Sources |
|---|---|---|---|---|
| V6-43 | **`Multi-Agent_NL2SQL_arch6.md`** superseding 5.2: current status; sections for console, reopen/undo, tracing, snippets/curation; the six routing departures as decisions; open decisions closed; state-contract field-lifetime table; `node_errors`; the identity model (V6-01); the topology decision (V6-03); the review write model (V6-04). | V6-01..04, V6-15..18 | L | P3, P7, P8, D-01, D-02, D-11, D-12 |
| V6-44 | **Verified security blueprint** in arch6: every row names file, line and test; stale sentences gone; role limits rows match V6-39. | V6-43, V6-39 | M | P4, D-03, D-07 |
| V6-45 | **Threat model and deployment tiers** (`SECURITY.md`): assets, actors, trust boundaries, three tiers with the compose settings each requires; `QUICKSTART.md` and `USAGE_GUIDE.md` point at it and the Security section becomes a checklist. Update `benchmarks/README.md` on how the golden set grows (V6-05). | V6-06..12 | M | P5, P10, D-06, D-14 |
| V6-46 | **Fix overstated controls in the editable docs** (`review/README.md`, `agent/API.md`) to match the code after V6-09, V6-11, V6-18. Can land with Phase 1/2 since those docs are not specs. | V6-09, V6-11, V6-18 | S | P6, D-07 |
| V6-47 | **One home per fact**: ports/defaults, settings, flags each stated once and linked elsewhere; `tests/docs/test_docs.py` extended to the single sources. | V6-43 | M | P12, D-13 |
| V6-48 | **Comment policy**: no version numbers in code comments; TODOs reference an issue; fix the stale "fourteen pipeline nodes" comment and the "should move there" note (closed by V6-22). | — | S | H6, C-05 |

## Phase 7 — Tests that keep it fixed (cross-cutting; each lands with the item it guards)

| Id | Action | Depends on | Effort | Sources |
|---|---|---|---|---|
| V6-49 | **Security test tier** (`tests/security/`): compose defaults (loopback stores, non-empty tokens after setup, CORS not `*`), constant-time compare in use, query token rejected off the events route, `reader_role.sql` matches the arch6 table, every Dockerfile has `USER`, no floating base tag, no secret in `.env.bak`. Name the tier in `README.md`. | V6-06..12, V6-31, V6-35, V6-39 | M | X10, P11, D-15 |
| V6-50 | **Conformance and narrative-drift tests**: arch6's spec→module→test table read by a docs test; a forbidden-phrase list for docs and comments. | V6-43 | S | P9, D-13 |
| V6-51 | **Reviewed coverage exclusions** for tautological branches, with the test-only parameters they required removed. | — | S | H9, C-08 |

## Dependency overview

```
Phase 0 decisions ──┬──> V6-17 (sensitive policy) ──> V6-43 (arch6)
                    ├──> V6-21/V6-30 (identity) ────> V6-33, V6-44, V6-49
                    ├──> V6-27/V6-28 (review writes) > V6-41 (ops in Python)
                    └──> V6-40 (consolidation) <──── V6-06, V6-08 (safe defaults)

Phase 1 safe defaults ──> V6-09 (tokens required) ──> V6-46 (docs), V6-49 (tests)
Phase 2 state fixes ────> V6-16 ──> V6-32 ──> V6-43
Phase 3 shared package ─> V6-22, V6-23, V6-26 ──> V6-27 ──> V6-41
                        └> V6-21 ──> V6-30 ──> V6-33
V6-25 (web package) + V6-36 (TLS identities) ──> V6-37 (one proxy image)
V6-31 (non-root) ──> V6-28, V6-34
V6-24 (pins) ──> V6-35 (digests)
```

## Traceability: findings → plan items

| Finding | Plan items |
|---|---|
| D-01, D-02 | V6-43 |
| D-03 | V6-39, V6-44 |
| D-04 | V6-02, V6-17 |
| D-05 | V6-01, V6-21, V6-30 |
| D-06 | V6-06..12, V6-45 |
| D-07 | V6-09, V6-11, V6-18, V6-46 |
| D-08 | V6-03, V6-40 |
| D-09 | V6-04, V6-27, V6-28 |
| D-10 | V6-43 (named as modular monolith) |
| D-11, D-12 | V6-15, V6-16, V6-43 |
| D-13 | V6-47, V6-50 |
| D-14 | V6-05, V6-45 |
| D-15 | V6-49 |
| I-01 | V6-02, V6-17 |
| I-02 | V6-15, V6-16 |
| I-03 | V6-18 |
| I-04 | V6-14 |
| I-05 | V6-17 (if implemented) |
| I-06 | V6-01, V6-21, V6-30 |
| I-07 | V6-13 |
| I-08 | V6-22 |
| I-09 | V6-20, V6-27 |
| I-10 | V6-04, V6-27, V6-28 |
| I-11 | V6-26 |
| I-12 | V6-06, V6-07, V6-40 |
| I-13 | V6-36 |
| I-14 | V6-25, V6-37 |
| I-15 | V6-41 |
| I-16 | V6-33 |
| I-17 | V6-29 |
| C-01 | V6-20, V6-25 |
| C-02 | V6-23 |
| C-03 | V6-26 |
| C-04 | V6-19, V6-22 |
| C-05 | V6-48 |
| C-06 | V6-41 |
| C-07 | V6-24 |
| C-08 | V6-51 |
| C-09 | V6-29 |
| C-10 | V6-30 |
| M-01 | V6-28, V6-31 |
| M-02 | V6-35 |
| M-03 | V6-34 |
| M-04 | V6-06 |
| M-05 | V6-36 |
| M-06 | V6-08, V6-38 |
| M-07 | V6-37, V6-40 |
| M-08 | V6-07 |
| M-09 | V6-42 |
| S-01 | V6-08, V6-09 |
| S-02 | V6-10, V6-20 |
| S-03, S-13 | V6-11 |
| S-04 | V6-12 |
| S-05 | V6-32 |
| S-06, S-14 | V6-30 |
| S-07 | V6-13, V6-14 |
| S-08 | V6-06, V6-07 |
| S-09 | V6-36 |
| S-10 | V6-08, V6-38 |
| S-11 | V6-39 |
| S-12 | V6-24, V6-35 |
| S-15 | V6-37 |

## Suggested release shape

- **v6.0.0** — Phases 1 and 2 plus V6-46 and the matching tests: safe defaults, generated secrets, tokens required, the three pipeline correctness fixes, the trace fields over REST, `nl2sql-retail-postgres:v1_2`. This is the release that changes behaviour for every operator, so it is the major bump.
- **v6.1** — Phases 3 and 4: the shared package, routers, identity, non-root images, review writes through a library.
- **v6.2** — Phase 5: hardening block, digests, TLS identities, one proxy image, secrets, role limits; consolidation and ops-in-Python begin.
- **arch6 and `SECURITY.md`** are written alongside v6.1 once the behaviour they describe exists, with the editable-doc corrections landing in v6.0.0.
