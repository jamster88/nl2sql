# v6.1 combined mitigation, repair and improvement plan

**Built from:** the three v6.1 adversarial reviews at commit `9b0b340` (release 6.0.1), 2026-10-04:
[`v6_1_review_architecture_as_documented.md`](v6_1_review_architecture_as_documented.md) (plan items P1–P14),
[`v6_1_review_architecture_as_implemented.md`](v6_1_review_architecture_as_implemented.md) (A1–A22),
[`v6_1_review_implementation.md`](v6_1_review_implementation.md) (H1–H13, K1–K13, X1–X17).
A one-page summary of the findings is in [`v6_1_review_summary.md`](v6_1_review_summary.md); the finding-by-finding and item-by-item comparison with the first cycle is [`v6_1_review_changes_since_v6_x.md`](v6_1_review_changes_since_v6_x.md).

## How this plan was built

1. **Started from the first cycle's plan.** Every `V6-nn` id from [`v6_x_review_mitigation_plan.md`](v6_x_review_mitigation_plan.md) is kept with the same meaning, and each carries a status at this commit: **Done**, **Partial**, **Open**, or **Superseded** (achieved by other means). Ids are never reused.
2. **Added the new items** as `V6-52` onward, de-duplicated across the three reviews the same way (TLS for the database: K13 + X11; revocation: A21 + X12; service tokens: A22 + X13; the default-deny route test: A18 + X10; the acceptance tier: H13 + D-15).
3. **Re-ordered where the first cycle's dependencies were wrong.** The first plan put separate TLS identities (V6-36) in Phase 5 and non-root images (V6-31) in Phase 4 as independent items. `auth/Dockerfile:18-19` says why four services run as root: they read the API's `0600` key. V6-36 is the prerequisite of V6-31 and moves to Phase 1; V6-31 waits for it.
4. **Kept the repository's standing rules.** Specs are never edited in place (arch6). Published tags never move; every fix to a shipped image is a new version with a changelog entry. Model-specific detail stays out of the documentation. Nothing touches `../fireworks_nl2sql`.

Effort key: **S** under a day, **M** one to three days, **L** a week or more. "Sources" names the plan items and findings each entry satisfies.

## What happened to the first plan

| Status | Items | Count |
|---|---|---|
| **Done** | V6-01 (identity model: decided, as principals from a directory), V6-10 (constant-time compare), V6-21 (`Principal` type: `Identity`), V6-30 (identity applied: principal to the agent, owners on jobs, signed-in author) | 4 |
| **Superseded** | V6-09 (tokens required by default): achieved differently, sign-in is required by default and the tokens became optional service credentials | 1 |
| **Partial** | V6-08 (three sign-in secrets generated; tokens and store passwords not), V6-11 (query token scoped; log not scrubbed), V6-12 (CORS none on the auth service; `*` still on API and review), V6-20 (one shared package, for identity only), V6-31 (one image drops root, the directory's), V6-38 (`*_FILE` readers exist; compose uses no `secrets:`), V6-39 (role limits on people; none on service roles), V6-46 (review token wording fixed; the trace claim not) | 8 |
| **Open** | the remaining 38, including both Phase 1 items that address the Critical (V6-06, V6-07) and all five pipeline fixes (V6-15 to V6-19) | 38 |

The first plan's "Suggested release shape" said 6.0.0 should be Phases 1 and 2: safe defaults, generated secrets, the pipeline fixes, the trace fields, the `v1_2` dataset image. What shipped as 6.0.0 was V6-01, V6-21 and V6-30 (Phase 0 decision and Phase 3–4 identity work), done larger than recommended, plus HTTPS on every interface. The Critical and ten of the fifteen Highs shipped unchanged under a major version whose stated reason was that defaults change for existing users.

## Phase 0 — Decisions (owner)

| Id | Decision | Options and recommendation | Status | Sources |
|---|---|---|---|---|
| V6-01 | Identity model | Decided: principals from a directory, four groups, Postgres as the authority. | **Done** | — |
| V6-02 | Sensitive-column policy | (a) implement where data leaves the database; (b) remove the claim. **Still recommend (b)**. | Open | D-04, I-01 |
| V6-03 | Topology target | (a) nine servers, justified; (b) one pgvector cluster for the non-retail stores. **Still recommend (b)**, staged. | Open | D-08, I-12 |
| V6-04 | Review write model | In-process loaders with a lock; non-root owner of the mounted directory. **Recommended**. | Open | D-09, I-10 |
| V6-05 | Benchmark wording | Decided (B03/B14 stay). The documentation and the test have not followed (C-11). | Open | D-14, C-11 |
| V6-70 | **New. Direct database access by people** | (a) keep it: then the database needs TLS and the rule `hostssl` before anyone connects from another machine (V6-52, V6-53); (b) withdraw it from the documents and bind 5432 to loopback. **Recommend (a)**: it is a real use (a BI tool) and the cost is one certificate. | Open | S-16, D-17 |
| V6-71 | **New. Session revocation model** | (a) a revocation list keyed by `jti`, checked through the guard's existing recheck; (b) server-side sessions; (c) accept eight hours of exposure and say so. **Recommend (a)**: it fits the stateless token and the connection the guard already has. | Open | S-18 |

## Phase 1 — Safe defaults, transport and secrets (ship as 6.1's first patch; independent of structure)

| Id | Action | Depends on | Effort | Status | Sources |
|---|---|---|---|---|---|
| V6-06 | **Loopback by default for every store**: `${DB_BIND_ADDRESS:-127.0.0.1}` on the seven published ports; a warning when opened with default passwords. | — | S | Open | K3, M-04, S-08 |
| V6-07 | **Stop baking the superuser password into the dataset image**; publish `nl2sql-retail-postgres:v1_2`; pin it. | — | S | Open | K1, M-08, S-08 |
| V6-52 | **New. TLS for the retail database**: a certificate generated on first start into a volume (as the directory does), `ssl=on`, mounted read-only where verified; in the `v1_2` image or in the start-up step that already rewrites `pg_hba.conf`. | V6-07 | S | — | K13, S-16 |
| V6-53 | **New. `hostssl` for the sign-in rule; `AUTH_DB_SSLMODE=verify-full`** with the certificate mounted into the auth service; `settings.warnings()` warns about `prefer` against a server with no certificate. | V6-52 | S | — | X11, S-16 |
| V6-08 | **Generated secrets** for every store password, the reader, the snippet reader, the feedback writer and the three tokens when set; `.env.bak` keeps non-secret keys. | — | S | Partial | K4, M-06, S-10 |
| V6-54 | **New. Secure default in the services**: `AUTH_ENABLED` defaults `true` in `guard.py`, the three settings modules and the six nginx fragments; a service started open says so at start. | — | S | — | X1, S-17 |
| V6-36 | **Separate TLS identities** (moved from Phase 5): the review service, the console, the auth service and MLflow's proxy each generate or receive their own key; proxies receive certificates only. The directory image (`ldap/nl2sql_ldap/tls.py`) is the template. **Prerequisite of V6-31.** | — | S | Open | A13, K7, I-13, M-05, M-11, S-09 |
| V6-10 | Constant-time comparison. | — | — | **Done** | — |
| V6-11 | **Scrub `access_token=` from the access log** (the scoping is done). | — | S | Partial | X3, S-13 |
| V6-12 | **CORS default to none** on the API and the review service. | — | S | Partial | X4, S-04 |
| V6-13 | **Bound the job queue and rate-limit.** | — | S | Open | A9, X6, I-07, S-07 |
| V6-14 | **Time-box `EXPLAIN`.** | — | S | Open | A5, X7, I-04 |
| V6-55 | **New. The rolesync password off the command line** (`-e` and `\getenv`, or the auth service creates its role). | — | S | — | X15, S-21 |
| V6-56 | **New. Honour a terminator's `X-Forwarded-Proto`** in every nginx template. | — | S | — | X16, S-22 |
| V6-57 | **New. The replica refuses a clear-text primary** unless told otherwise. | — | S | — | X17, S-23 |
| V6-58 | **New. The directory API behind the same binding as its page.** | — | S | — | K12, M-10 |
| V6-59 | **New. No permanently red test**: `xfail(strict=True, reason=...)`, a provenance assertion, or a data change. | — | S | — | H12, C-11, D-14 |
| V6-60 | **New. Default-deny route test**: every `/v1` and `/directory/v1` route in every service has a `Guard` dependency, asserted over `app.routes`. | — | S | — | A18, I-20 |

## Phase 2 — Correctness fixes in the pipeline (unchanged from the first plan; still open)

| Id | Action | Depends on | Effort | Status | Sources |
|---|---|---|---|---|---|
| V6-15 | **Per-attempt state reset on the repair edge**; lifetimes declared in `state.py`; graph test. | — | S | Open | A1, I-02, D-11 |
| V6-16 | **Node-failure channel** `node_errors`. | — | S | Open | A2, I-02, D-12 |
| V6-17 | **Resolve the sensitive-column policy** per V6-02. | V6-02 | S / M | Open | A3, I-01, D-04 |
| V6-18 | **Carry routing fields over REST** with `extra="forbid"`; fix `agent/API.md:290`. | — | S | Open | A4, I-03, D-07 |
| V6-19 | **`extra="forbid"` on every wire model.** | V6-18 | S | Open | H5, C-04 |

## Phase 3 — Shared foundations

| Id | Action | Depends on | Effort | Status | Sources |
|---|---|---|---|---|---|
| V6-20 | **Shared Python package**: grow `nl2sql_identity` into (or beside) `nl2sql_common` with the env helpers (now five copies), `Embedder`, `vector_literal`, the error envelope, `Health`/`Readiness`/`ApiError`, `_redacted` (three), `json_safe`. | — | M | Partial | A6, H1, I-09, C-01 |
| V6-66 | **New. Install the shared package as a package**, with a `pyproject.toml` and a version the images record, rather than `COPY` into three images. | V6-20 | S | — | C-12 |
| V6-21 | `Principal` type. | — | — | **Done** (`Identity`) | — |
| V6-22 | **Single engine accessor; `SET LOCAL` in retrievers.** | V6-20 | S | Open | A7, H4, I-08, C-04 |
| V6-23 | **Error taxonomy**; cut the 78 broad catches. | V6-20 | M | Open | H2, C-02 |
| V6-24 | **Pin and hash** Python dependencies per image; pin the directory image's Alpine packages. | — | S | Open | H7, C-07, S-12 |
| V6-25 | **Shared front-end workspace package**, now carrying `session.ts` and `SignInGate.tsx` (five identical copies, 2,380 lines) beside `ApiError`, `request`, `plainText`, `counted` and the vite proxy factory. | — | M | Open; worse | A14, H11, C-01, I-14 |

## Phase 4 — Service refactors (consume Phase 3)

| Id | Action | Depends on | Effort | Status | Sources |
|---|---|---|---|---|---|
| V6-26 | **Routers instead of `create_app` closures** in four services, with a router-level `Guard` dependency so denial is the default and V6-60's test becomes redundant. | V6-20 | M + S + S + S | Open; worse | A8, H3, I-11, I-20, C-03 |
| V6-27 | **Review service writes through a library with a lock.** | V6-04, V6-20 | M | Open | A11, I-10 |
| V6-28 | **Non-root review container that owns the mounted directory.** | V6-04, V6-31 | S | Open | A11, M-01 |
| V6-29 | **Sequences for fix ids** (feedback upsert withdrawn: the delete-then-insert has a recorded privilege reason). | — | S | Open, narrowed | A17, H10, I-17, C-09 |
| V6-30 | Identity applied. | — | — | **Done** | — |
| V6-31 | **Non-root `USER` in every application image**, by the directory image's `become` pattern or by a service user that owns its own key. | **V6-36** | M | Partial (1 of 15) | K2, M-01, M-11 |
| V6-32 | **Operator-only detail.** | V6-16 | S | Open | X5, S-05 |
| V6-33 | **Cache reload signal** behind `nl2sql_admins` (the admin role now exists). | V6-30 | S | Open; unblocked | A16, I-16 |
| V6-61 | **New. Session revocation** per V6-71: `jti` rows written on sign-out, password change or set, lock and removal; checked in `Guard.current()`. | V6-71, V6-20 | M | — | A21, X12, S-18 |
| V6-62 | **New. Service tokens name a principal and hold explicit roles**; `X-Reviewer` is never the author. | V6-20 | S | — | A22, X13, S-19 |
| V6-63 | **New. Trusted-proxy list** for the auth service's `X-Forwarded-For`. | — | S | — | X14, S-20 |
| V6-64 | **New. `SET LOCAL application_name` to the person** beside `SET LOCAL ROLE`; docs say what the database knows and where. | — | S | — | A19, I-18, D-07 |

## Phase 5 — Container hardening and topology (consume Phase 4's non-root images)

| Id | Action | Depends on | Effort | Status | Sources |
|---|---|---|---|---|---|
| V6-34 | **Compose hardening block** on every service. | V6-31 | M | Open | K5, M-03 |
| V6-35 | **Pin base images by digest**; one Alpine tag. | V6-24 | S | Open | K6, M-02, M-12, S-12 |
| V6-65 | **New. Decouple start-up from the API's certificate**: nothing `depends_on` the API for TLS material once each service has its own (V6-36). | V6-36 | S | — | A20, I-19 |
| V6-37 | **One proxy image**, one health check that verifies. | V6-25, V6-36 | M | Open; worse | A14, K8, I-14, M-07, S-15 |
| V6-38 | **Compose `secrets:`**; the `*_FILE` readers exist in two packages. | V6-08 | S | Partial | K9, M-06, S-10 |
| V6-39 | **Role-level limits on the service roles** (reader, snippet reader, feedback writer); `work_mem` everywhere. | — | S | Partial | X8, S-11, D-03 |
| V6-40 | **Store consolidation** per V6-03. | V6-03, V6-06, V6-08 | L | Open | A12, K11, I-12, D-08 |
| V6-41 | **Orchestration to Python**; the auth service owns `auth_roles.sql` and the hba rewrite. | V6-27 | L | Open; worse | A15, H8, I-15, C-06 |
| V6-42 | **Provenance in publishing.** | — | M | Open | K10, M-09 |

## Phase 6 — Documentation of the as-built system

| Id | Action | Depends on | Effort | Status | Sources |
|---|---|---|---|---|---|
| V6-43 | **`Multi-Agent_NL2SQL_arch6.md`** superseding 5.2, now also with sign-in (6.0): the identity model as a design, the group model, the role sync, the replica, the pg_hba block and its order. | V6-01..05, V6-15..18, V6-68 | L | Open; larger | P3, P7, P8, D-01, D-02, D-11, D-12, D-16 |
| V6-68 | **New. Design, not description, for sign-in**: trust boundaries, what each credential is worth and for how long, alternatives rejected, limits chosen and why, properties promised. Written against the code as it is, then the code is held to it. Can be written now and folded into arch6. | — | M | — | P13, D-16 |
| V6-44 | **Verified security blueprint** in arch6 with the rows D-03 lists. | V6-43, V6-39 | M | Open | P4, D-03, D-07 |
| V6-45 | **Threat model and deployment tiers** (`SECURITY.md`); retire "one person on one machine". | V6-06..12 | M | Open | P5, D-06, D-16 |
| V6-46 | **Fix the remaining overstated controls**: `docker-compose.yml:569`, `agent/API.md:290` (after V6-18), `USAGE_GUIDE.md:352` (after V6-64). | V6-18, V6-64 | S | Partial | P6, D-07 |
| V6-69 | **New. Say what direct database access costs**, in the Security section and `auth/README.md`, until V6-52/53 land; then say it is encrypted. | V6-70 | S | — | P14, D-17, S-16 |
| V6-47 | **One home per fact**; the sign-in facts are in nine places. | V6-43 | M | Open | P12, D-13 |
| V6-48 | **Comment policy**; "fourteen pipeline nodes". | — | S | Open | H6, C-05 |

## Phase 7 — Tests that keep it fixed

| Id | Action | Depends on | Effort | Status | Sources |
|---|---|---|---|---|---|
| V6-49 | **Security test tier** (`tests/security/`), seeded with `tests/auth`'s posture tests plus: every store on loopback, no `host ... ldap` rule, `USER` or a documented drop in every Dockerfile, `AUTH_ENABLED` default `true` in every settings module, no secret on a command line in the scripts, every guarded route guarded (V6-60). Named in `README.md`. | V6-06, V6-31, V6-35, V6-39, V6-53, V6-54 | M | Open | X10, P11, D-15 |
| V6-67 | **New. Stack-level acceptance tier**: `start.sh` with every page against freshly built images on a private compose project; sign in as the generated administrator; a question through the web proxy; the console; a promotion; MLflow's door; teardown. The changelog's "Checked live" prose is its specification. | — | M | — | H13, C-13, D-15 |
| V6-50 | **Conformance and narrative-drift tests**; the forbidden-phrase list gains "one person on one machine" once V6-45 lands. | V6-43 | S | Open | P9, D-13 |
| V6-51 | **Reviewed coverage exclusions.** | — | S | Open | H9, C-08 |

## Dependency overview

```
Phase 0 ──┬──> V6-17 (sensitive policy) ──────────> V6-43 (arch6)
          ├──> V6-70 (direct access) ─────────────> V6-52/V6-53 (database TLS, hostssl) ──> V6-69 (docs)
          ├──> V6-71 (revocation model) ──────────> V6-61
          ├──> V6-27/V6-28 (review writes) ───────> V6-41 (ops in Python)
          └──> V6-40 (consolidation) <──────────── V6-06, V6-08

Phase 1: V6-36 (own TLS identities) ──> V6-31 (non-root) ──> V6-28, V6-34
                                     └> V6-65 (no start-up dependency on the API)
         V6-54 (secure default in code) ──> V6-49 (tests)
         V6-07 (v1_2 image) ──> V6-52 (database TLS) ──> V6-53
Phase 2: V6-15 ──> V6-16 ──> V6-32 ──> V6-43
Phase 3: V6-20 ──> V6-66, V6-22, V6-23, V6-26 ──> V6-27 ──> V6-41
                └> V6-61, V6-62
         V6-25 (web package incl. sign-in gate) + V6-36 ──> V6-37 (one proxy image)
         V6-24 (pins) ──> V6-35 (digests)
Phase 6: V6-68 (sign-in design) ──> V6-43 ──> V6-44, V6-47, V6-50
Phase 7: V6-67 (acceptance tier) is independent and should land first
```

## Traceability: findings → plan items

| Finding | Status | Plan items |
|---|---|---|
| D-01, D-02 | Worse | V6-43, V6-68 |
| D-03 | Worse | V6-39, V6-44 |
| D-04 | Unchanged | V6-02, V6-17 |
| D-05 | Resolved | (V6-01, V6-21, V6-30 done) |
| D-06 | Worse | V6-06, V6-07, V6-45 |
| D-07 | Improved | V6-18, V6-46, V6-64 |
| D-08 | Worse | V6-03, V6-40 |
| D-09 | Unchanged | V6-04, V6-27, V6-28 |
| D-10 | Unchanged | V6-43 |
| D-11, D-12 | Unchanged | V6-15, V6-16, V6-43 |
| D-13 | Unchanged | V6-47, V6-48, V6-50 |
| D-14 | Unchanged | V6-05, V6-59 |
| D-15 | Unchanged | V6-49, V6-67 |
| D-16 | New | V6-68, V6-43, V6-45 |
| D-17 | New | V6-69, V6-70 |
| I-01 | Unchanged | V6-02, V6-17 |
| I-02 | Unchanged | V6-15, V6-16 |
| I-03 | Unchanged | V6-18 |
| I-04 | Unchanged | V6-14 |
| I-05 | Unchanged | V6-17 |
| I-06 | Resolved | (V6-21, V6-30 done) |
| I-07 | Mitigated | V6-13 |
| I-08 | Unchanged | V6-22 |
| I-09 | Worse | V6-20, V6-66 |
| I-10 | Unchanged | V6-04, V6-27, V6-28 |
| I-11 | Worse | V6-26 |
| I-12 | Worse | V6-06, V6-07, V6-40 |
| I-13 | Worse | V6-36, V6-65 |
| I-14 | Worse | V6-25, V6-37 |
| I-15 | Worse | V6-41, V6-55 |
| I-16 | Unchanged | V6-33 |
| I-17 | Unchanged | V6-29 |
| I-18 | New | V6-64, V6-46 |
| I-19 | New | V6-36, V6-65 |
| I-20 | New | V6-60, V6-26 |
| C-01 | Worse | V6-20, V6-25 |
| C-02 | Unchanged | V6-23 |
| C-03 | Worse | V6-26 |
| C-04 | Unchanged | V6-19, V6-22 |
| C-05 | Unchanged | V6-48 |
| C-06 | Worse | V6-41 |
| C-07 | Unchanged | V6-24 |
| C-08 | Unchanged | V6-51 |
| C-09 | Unchanged (narrowed) | V6-29 |
| C-10 | Resolved (sessions) | V6-62 for the token path |
| C-11 | New | V6-59 |
| C-12 | New | V6-66 |
| C-13 | New | V6-67 |
| M-01 | Unchanged | V6-36, V6-31, V6-28 |
| M-02 | Unchanged | V6-35 |
| M-03 | Unchanged | V6-34 |
| M-04 | Unchanged | V6-06 |
| M-05 | Worse | V6-36 |
| M-06 | Improved | V6-08, V6-38 |
| M-07 | Worse | V6-37, V6-40 |
| M-08 | Unchanged | V6-07 |
| M-09 | Unchanged | V6-42 |
| M-10 | New | V6-58 |
| M-11 | New | V6-36 (ordering), V6-31 |
| M-12 | New | V6-35 |
| S-01, S-02, S-03, S-06 | Resolved | — |
| S-04 | Mitigated | V6-12 |
| S-05 | Unchanged | V6-32 |
| S-07 | Mitigated | V6-13, V6-14 |
| S-08 | Unchanged | V6-06, V6-07 |
| S-09 | Worse | V6-36 |
| S-10 | Improved | V6-08, V6-38 |
| S-11 | Improved | V6-39 |
| S-12 | Unchanged | V6-24, V6-35 |
| S-13 | Improved | V6-11 |
| S-14 | Resolved (sessions) | V6-62 |
| S-15 | Worse | V6-37 |
| S-16 | New | V6-52, V6-53, V6-69, V6-70 |
| S-17 | New | V6-54 |
| S-18 | New | V6-71, V6-61 |
| S-19 | New | V6-62 |
| S-20 | New | V6-63 |
| S-21 | New | V6-55 |
| S-22 | New | V6-56 |
| S-23 | New | V6-57 |

## Suggested release shape

- **v6.1.0** — Phase 1 and Phase 2, plus V6-69 and the matching tests: loopback stores, the `v1_2` dataset image without the baked superuser password, TLS on the retail database with `hostssl` for sign-in, own TLS identities per service, the secure default in the services, generated secrets, the five pipeline fixes, the trace fields over REST. This is the release the first plan said 6.0.0 should be, and the one that makes the sign-in 6.0 added safe to use from another machine.
- **v6.2** — Phases 3 and 4: the shared package installed as a package, routers with default-deny, non-root images (now unblocked), revocation, named service tokens, the review service writing through a library.
- **v6.3** — Phase 5: the hardening block, digests, one proxy image, compose secrets, service-role limits; consolidation and ops-in-Python begin.
- **V6-67, the acceptance tier,** does not wait for any release: it is the test that would have caught all four 6.0 defects before they were published, and it should be in place before 6.1.0 is.
- **arch6, the sign-in design and `SECURITY.md`** are written alongside v6.1 (V6-68 can start now); the editable-document corrections land with v6.1.0.
