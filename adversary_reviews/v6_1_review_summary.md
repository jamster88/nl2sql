# v6.1 adversarial review: summary of findings

**Reviewed at:** commit `9b0b340` (release 6.0.1), branch `utils/ldap_auth`, 2026-10-04.
**Review tag:** `v6_1_review`, the second cycle. The first, `v6_x_review`, reviewed commit `a625cf0` (5.6.1) on 2026-10-03. The full reviews are
[`v6_1_review_architecture_as_documented.md`](v6_1_review_architecture_as_documented.md),
[`v6_1_review_architecture_as_implemented.md`](v6_1_review_architecture_as_implemented.md) and
[`v6_1_review_implementation.md`](v6_1_review_implementation.md); the combined, dependency-ordered plan is
[`v6_1_review_mitigation_plan.md`](v6_1_review_mitigation_plan.md), and the finding-by-finding comparison with the first cycle is
[`v6_1_review_changes_since_v6_x.md`](v6_1_review_changes_since_v6_x.md). Neither is repeated here.

## Scope

The same three lenses as the first cycle, by request: the architecture as documented, the architecture as implemented, and the implementation itself (code hygiene, container and microservice practice, and security controls other than LLM-specific ones, which remain deferred). Method was static reading of the specs, READMEs, changelogs, the Python packages including the new `auth/` and `ldap/`, the five TypeScript clients, the Java client, all fifteen Dockerfiles, `docker-compose.yml`, the shell scripts and the test suite. Nothing was executed and nothing outside this repository was consulted. Every first-cycle citation was re-checked at this commit. Finding ids are kept from the first cycle where a finding persists; every finding carries a status: **Unchanged**, **Worse**, **Improved**, **Mitigated**, **Resolved** or **New**.

## Headline verdict

6.0 did the hardest thing in the first plan and skipped the easiest. The identity seam the first cycle asked for exists and is well made: one shared package verifies an Ed25519 session, Postgres is the authority for passwords and groups, every person's SQL runs as their own role, jobs have owners, promotions are signed by the person who made them, token comparison is constant-time and the query-string token is confined to the one route that needs it. Eight first-cycle findings are resolved by it, three of them High.

Everything the first cycle rated most consequential is where it was. The published retail image still carries the `postgres` superuser with password `nl2sql`, compose still publishes it and six more stores on every interface with passwords equal to their user names, and 6.0's new `pg_hba` block was written above the catch-all rule rather than in place of it. The three pipeline defects in the audit stage are at the same line numbers. No application image but the directory's runs as a non-root user, and the auth service's own Dockerfile says why: four services share one `0600` private key. The sign-in itself brought new exposure: a person's directory password crosses to a Postgres server that has no TLS, by an authentication method that sends it in clear, through a port open to the network, and connecting that way is a documented feature. The secure default lives in compose rather than in the services, a session cannot be revoked, and the static tokens that sign-in made optional are, when set, an unattributed identity holding every role. Structurally the repository grew the way the first cycle predicted: five byte-identical copies of the sign-in front end, a sixth nginx image, a fourth thousand-line `create_app` closure, 3,208 lines of shell that now run superuser SQL, and a design authority that is five releases and a major version behind with no document for the largest architectural change since arch4.

Of the first cycle's 66 findings: 8 resolved, 5 improved, 3 mitigated by the new default, 32 unchanged, 18 worse. 19 findings are new. Of the 17 that were Critical or High: 3 resolved, 1 mitigated, 10 unchanged (both Criticals among them), 3 worse.

## The ten findings that matter most now

1. **M-08 / S-08 · Critical · Unchanged** — `docker/init_db.sh:34-35` still sets the `postgres` superuser's password to `nl2sql` at build time; the image is published; `docker-compose.yml:29` still publishes 5432 on every interface; `README.md:1519-1520` documents the connection string and that the superuser shares the password. 6.0's sign-in block (`ldap_hba.sh:76-77`) sits above the `host all all all` catch-all, which is still in force.
2. **S-16 · High · New** — pg_hba's `ldap` method is clear-text password authentication; the rule is `host`, not `hostssl`; the retail Postgres has no certificate and no `ssl=on`; the auth service connects with `sslmode=prefer` (`settings.py:132`) and so negotiates no TLS; direct connection by people "with psql or a BI tool" is a documented feature (`rolesync.py:14-15`, changelog v6_0_1). A person's password for every page crosses the LAN unencrypted.
3. **M-04 · High · Unchanged** — Seven stores on every interface with default passwords (compose lines 29, 55, 81, 119, 154, 184, 218); the loopback pattern is now used by four services and no store.
4. **I-01 / I-02 / I-03 · High · Unchanged** — The sensitive-column policy is dead (`graph.py:1107`), the audit leaks across repair cycles and spends the rewrite on the wrong attempt (`graph.py:1068, 1093, 1137`), the routing fields are dropped from the REST trace (`api/models.py:179`, `translate.py:88`). `graph.py` and the API models did not change.
5. **I-13 / M-01 / M-11 · High · Worse** — One private key presented by four services and mounted into eleven containers; `auth/Dockerfile:18-19`: "Root, as the review service and the console are, for the same reason: it presents the agent API's certificate, whose key that API writes 0600." The first plan listed non-root images and separate TLS identities as independent; the second is the prerequisite of the first, and the one image with its own key is the one that drops root.
6. **D-01 / D-03 / D-16 · High · Worse / New** — The spec still says routing is "Designed, not built" and the agent "connects as `nl2sql`, the owner"; its security table describes none of the roles 6.0 created; the identity model exists only as READMEs describing code, with no trust-boundary statement, no account of what a stolen cookie or a compromised container can do, and no record of alternatives.
7. **S-17 · Medium · New** — `AUTH_ENABLED` defaults `false` in `guard.py:124`, the three settings modules and the nginx fragments; sign-in is on by default only because compose says `${AUTH_ENABLED:-true}` eight times. A service started any other way is open.
8. **S-18 · Medium · New** — Sign-out deletes the cookie and nothing else; `jti` is minted and never checked; a password change, an administrator setting a password, and a lockout all leave existing sessions valid for eight hours. The role recheck catches a removed person within a minute and is not revocation.
9. **C-13 / C-11 · Medium · New** — 100% coverage in every language and four defects in the shipped 6.0.0, found by running `start.sh` by hand ("none of them could be seen by a test that ran a service on its own", changelog); and one test has failed on every run since 5.4 and is recorded as expected.
10. **C-01 / I-14 / S-19 · Medium · Worse / New** — `session.ts` and `SignInGate.tsx` are byte-identical in five projects (2,380 lines); six nginx images; environment helpers in five files; and the static service token, when set, holds every role of its service with the self-asserted `X-Reviewer` as its only name.

## Findings by severity

| Document | Critical | High | Medium | Low | Total | (first cycle) |
|---|---|---|---|---|---|---|
| Architecture as documented (D) | 0 | 6 | 7 | 3 | 16 | 15 |
| Architecture as implemented (I) | 0 | 5 | 11 | 3 | 19 | 17 |
| Implementation: hygiene (C) | 0 | 0 | 8 | 4 | 12 | 10 |
| Implementation: containers (M) | 1 | 3 | 6 | 2 | 12 | 9 |
| Implementation: security (S) | 1 | 1 | 8 | 8 | 18 | 15 |
| **All** | **2** | **15** | **40** | **20** | **77** | **66** |

Eight resolved findings (D-05, I-06, C-10, S-01, S-02, S-03, S-06, S-14) are listed in the index and not counted. Two severities rose (D-06 to High, I-13 to High), two fell (S-04 to Low, S-07 to Medium). Several findings remain one defect seen through more than one lens (M-04/M-08/S-08, I-13/M-05/M-11/S-09, C-01/I-09/I-14); the combined plan de-duplicates them.

## Findings by status against the first cycle

| Status | Count | Findings |
|---|---|---|
| Resolved | 8 | D-05, I-06, C-10, S-01, S-02, S-03, S-06, S-14 |
| Improved | 5 | D-07, M-06, S-10, S-11, S-13 |
| Mitigated (code unchanged; sign-in by default narrows who can reach it) | 3 | I-07, S-04, S-07 |
| Unchanged | 32 | D-04, D-09–D-15, I-01–I-05, I-08, I-10, I-16, I-17, C-02, C-04, C-05, C-07–C-09, M-01–M-04, M-08, M-09, S-05, S-08, S-12 |
| Worse | 18 | D-01, D-02, D-03, D-06, D-08, I-09, I-11–I-15, C-01, C-03, C-06, M-05, M-07, S-09, S-15 |
| New | 19 | D-16, D-17, I-18, I-19, I-20, C-11, C-12, C-13, M-10, M-11, M-12, S-16–S-23 |

## Full index

### Architecture as documented
- **D-01 High · Worse** — Spec five releases and a major behind; still "Designed, not built".
- **D-02 Medium · Worse** — Decisions made in code (V6-01, the four 6.0 design answers), none recorded; four first-cycle decisions untaken.
- **D-03 High · Worse** — Security blueprint stale and silent on every role 6.0 created.
- **D-04 High · Unchanged** — Sensitive-column policy documented, dead.
- **D-05 Resolved** — An identity model exists (`auth/README.md`).
- **D-06 High · Worse** — "One person on one machine" beside sign-in by default; databases open with development passwords; the README's `psql` as owner.
- **D-07 Medium · Improved** — Review token wording and query-string scope fixed; trace claim remains; "the database knows who asked" overstated.
- **D-08 Medium · Worse** — Nine servers, 23 services, 13 profiles, asserted.
- **D-09 Medium · Unchanged** — Review write model undiscussed.
- **D-10 Low · Unchanged** — Modular monolith, called services.
- **D-11 High · Unchanged** — Field lifetimes undocumented; I-02 stands.
- **D-12 Medium · Unchanged** — `retrieval_errors` overloaded.
- **D-13 Medium · Unchanged** — Narrative drift untested; "fourteen pipeline nodes" still there.
- **D-14 Low · Unchanged** — Benchmark independence wording; its test is red (C-11).
- **D-15 Low · Unchanged** — No security tier; the first posture tests arrived unnamed; no acceptance tier.
- **D-16 High · New** — The largest design change since arch4 has no design document.
- **D-17 Medium · New** — Direct database access documented as a feature without its transport.

### Architecture as implemented
- **I-01 High · Unchanged** — Sensitive-column policy dead code.
- **I-02 High · Unchanged** — Stale audit across repair cycles; narrator failure under `retrieval_errors`.
- **I-03 High · Unchanged** — Routing fields dropped from the REST trace.
- **I-04 Medium · Unchanged** — `EXPLAIN` untimed (now runs as the principal).
- **I-05 Medium · Unchanged** — Sample rows to the model host.
- **I-06 Resolved** — Identity seam; RLS path has a caller.
- **I-07 Medium · Mitigated** — Unbounded queue; needs an account now.
- **I-08 Medium · Unchanged** — `_engine` leaks; session-level `SET`.
- **I-09 Medium · Worse** — One shared package (identity); everything else copied, env helpers now ×5.
- **I-10 Medium · Unchanged** — Review writes to the checkout as root.
- **I-11 Medium · Worse** — Four `create_app` closures; review 1,537 lines.
- **I-12 High · Worse** — Nine servers; superuser unchanged; catch-all rule still last.
- **I-13 High · Worse** — One key, four identities, eleven containers; why nothing drops root.
- **I-14 Medium · Worse** — Six nginx images; five identical sign-in front ends.
- **I-15 Medium · Worse** — 3,208 lines of shell doing superuser SQL and `pg_hba` rewrites.
- **I-16 Low · Unchanged** — Process-lifetime caches (the new ones have TTLs).
- **I-17 Low · Unchanged** — `max+1` fix ids (feedback half withdrawn).
- **I-18 Low · New** — Identity reaches `current_user` only; no RLS; no `application_name`.
- **I-19 Medium · New** — Every start depends on the API's certificate volume.
- **I-20 Medium · New** — Authorization is opt-in per route; no default deny.

### Implementation: code hygiene
- **C-01 Medium · Worse** — Copy-and-extend applied to sign-in (×5 front end, ×6 nginx, ×5 env helpers, ×3 `_redacted`).
- **C-02 Medium · Unchanged** — 78 broad catches in the old packages; the new ones are disciplined.
- **C-03 Medium · Worse** — review 1,537; compose 1,312; launch 1,381; auth app 732.
- **C-04 Medium · Unchanged** — Encapsulation leaks; lenient wire model.
- **C-05 Low · Unchanged** — Version history in comments.
- **C-06 Medium · Worse** — Shell orchestration now runs superuser SQL.
- **C-07 Medium · Unchanged** — Pinning inconsistent, never hashed.
- **C-08 Low · Unchanged** — Coverage-shaped tests.
- **C-09 Low · Unchanged (narrowed)** — `max+1` ids; feedback upsert withdrawn with credit.
- **C-10 Resolved (sessions)** — Author is the signed-in principal.
- **C-11 Medium · New** — A permanently red test in the suite.
- **C-12 Low · New** — Shared package shared by `COPY`, not by package.
- **C-13 Medium · New** — 100% coverage, four defects in the shipped release; no acceptance tier.

### Implementation: containers and microservices
- **M-01 High · Unchanged** — Fourteen of fifteen images run as root; the directory's drops privileges.
- **M-02 Medium · Unchanged** — Floating base tags.
- **M-03 High · Unchanged** — No hardening in 1,312 lines of compose.
- **M-04 High · Unchanged** — Seven stores on every interface with default credentials.
- **M-05 Medium · Worse** — The private key in eleven containers.
- **M-06 Medium · Improved** — Three secrets generated, files 0600, `*_FILE` readers; still environment-borne, store passwords default.
- **M-07 Medium · Worse** — Six proxies, four services on one certificate, twelve unverified health checks.
- **M-08 Critical · Unchanged** — Published image carries a known superuser password.
- **M-09 Low · Unchanged** — No provenance in publishing.
- **M-10 Medium · New** — Loopback binding protects the directory page, not its API.
- **M-11 Medium · New** — The shared key is the dependency the plan missed.
- **M-12 Low · New** — `alpine:3.21` and `alpine:3.22` in one release.

### Implementation: security controls (non-LLM)
- **S-01 Resolved** — Authentication on by default (in compose; see S-17).
- **S-02 Resolved** — Constant-time comparison.
- **S-03 Resolved** — Query-string token scoped to the event stream.
- **S-04 Low · Mitigated** — CORS `*` on API and review; cookie `SameSite=Strict`, no credentials with `*`.
- **S-05 Medium · Unchanged** — Exception text in responses; `/readyz` open.
- **S-06 Resolved** — Jobs belong to their owner.
- **S-07 Medium · Mitigated** — Queue growth needs an account; `EXPLAIN` untimed.
- **S-08 Critical · Unchanged** — Databases reachable with known passwords, including a superuser.
- **S-09 Medium · Worse** — One TLS key for four services.
- **S-10 Medium · Improved** — Secret handling.
- **S-11 Low · Improved** — Role limits on people, none on service roles.
- **S-12 Medium · Unchanged** — Supply chain.
- **S-13 Low · Improved** — Query token logged on one route only.
- **S-14 Resolved (sessions)** — Decisions attributed to the signed-in person.
- **S-15 Low · Worse** — Verification skipped in twelve health checks.
- **S-16 High · New** — Directory passwords in clear text to a Postgres with no TLS on an open port.
- **S-17 Medium · New** — The secure default lives in compose, not the services.
- **S-18 Medium · New** — No session revocation.
- **S-19 Medium · New** — Static service token: every role, no name.
- **S-20 Low · New** — Throttle trusts `X-Forwarded-For` from direct callers.
- **S-21 Low · New** — Rolesync password on the command line.
- **S-22 Low · New** — Cookie loses `Secure` behind a TLS terminator.
- **S-23 Low · New** — Replica's upstream transport insecure by default.

## Strengths worth protecting

- Everything the first cycle listed: database least privilege tested live; the pglast validator; the planner gate; the row cap; HTTPS by default; the desktop client's TLS design; 100% gates in every language; documentation that records reasons.
- New: the session format (fixed header, Ed25519, issuer and audience, `int` timestamps); Postgres as the password authority; groups re-read within a minute; `HttpOnly`/`SameSite=Strict`/`Secure` cookies with a cross-site check on every write; job ownership with 404 for strangers; the directory image's privilege drop and its own key; the role sync's `ADMIN`-scoped `CREATEROLE`; reserved role names refused; a replica that refuses to empty itself; `pg_hba` parsed before it is reloaded; a changelog that says what the tests missed and why.

## Decisions the owner needs to make

1. Sensitive-column policy (V6-02): implement where data leaves the database, or remove the claim (recommended).
2. Topology (V6-03): nine servers, or consolidate the non-retail stores (recommended, staged).
3. Review write model (V6-04): in-process loaders with a lock and a non-root owner (recommended).
4. **New.** Direct database access by people (V6-70): keep it and give the database TLS (recommended), or withdraw it.
5. **New.** Session revocation model (V6-71): a `jti` revocation list checked through the guard's existing recheck (recommended).

## What this cycle did not cover

LLM-specific security, by request; dynamic testing of any kind; dependency vulnerability scanning; the directory's OpenLDAP configuration beyond what `slapd.py` renders; Active Directory as a primary beyond the code that reads one; the data generator's content. The next cycle should start from [`v6_1_review_changes_since_v6_x.md`](v6_1_review_changes_since_v6_x.md), keep the ids, and add a fourth status, **Regressed**, for a resolved finding that comes back.
