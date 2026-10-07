# Adversarial review, v6.1 cycle: the architecture as documented

**Reviewed at:** commit `9b0b340` (release 6.0.1), branch `utils/ldap_auth`, 2026-10-04.
**Review tag:** `v6_1_review`. The second cycle of a repeating exercise; the first was `v6_x_review` at commit `a625cf0` (5.6.1, 2026-10-03). Finding ids are kept from the first cycle wherever a finding persists, and every finding carries its status against that cycle: **Unchanged**, **Worse**, **Improved**, **Mitigated**, **Resolved** or **New**. The finding-by-finding comparison is [`v6_1_review_changes_since_v6_x.md`](v6_1_review_changes_since_v6_x.md).
**Companion documents:** [`v6_1_review_architecture_as_implemented.md`](v6_1_review_architecture_as_implemented.md), [`v6_1_review_implementation.md`](v6_1_review_implementation.md), [`v6_1_review_mitigation_plan.md`](v6_1_review_mitigation_plan.md) (the combined plan), [`v6_1_review_summary.md`](v6_1_review_summary.md).

## Scope and method

This document reviews the architecture **as the repository describes it**, not as the code does it. The question asked of every source is the first cycle's: if a competent engineer joined tomorrow and read only the documentation, would they form a correct and complete picture of the system, its boundaries, its security posture and its failure behaviour, and would the design they read be a sound one?

What has changed since the first cycle is the subject matter. Between `a625cf0` and `9b0b340` the repository gained sign-in: a directory container, an auth service, a directory page, a front door for MLflow, a session every service verifies, and a rule that every person's SQL runs as their own database role. That is the largest architectural change since arch4, and it was shipped as a major version (6.0.0) and corrected the next day (6.0.1). So the question this time has a second half: did the design record move with the system?

Sources read in full or in large part:

- `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_2.md`, still the highest-suffixed spec and therefore still the authority by the repository's own rule; `arch5_1.md`, `arch5.md`, `arch4.md` for lineage.
- `README.md` (the new Sign-in section, the container, image and tag tables, the Postgres container section), `USAGE_GUIDE.md` (Signing in, Security, Upgrading, Troubleshooting), `QUICKSTART.md`, `CHANGELOG.md` and `CHANGELOG_SIMPLE.md` (the v6_0 and v6_0_1 entries in full).
- `auth/README.md` and `ldap/README.md` (new), `agent/README.md`, `agent/API.md` (Authentication), `review/README.md`, `console/README.md`, `curate/README.md`, `gui/README.md`, `desktop/README.md`, `benchmarks/README.md`.
- The comments in `docker-compose.yml`, `docker/auth_roles.sql`, `docker/ldap_hba.sh` and the Dockerfiles, which are where several design decisions are actually written down.
- The first cycle's five documents, against which every finding below was checked.

Where a documented claim was checked against code, the check is noted and the code finding lives in the implemented-architecture review. LLM-specific security remains out of scope by request.

Finding ids are `D-nn`. Severity is Critical / High / Medium / Low. Confidence is **Verified** (the cited text was read) or **Inferred**.

## 1. Verdict

The documentation of what was built is as good as it was: `auth/README.md` explains the sign-in mechanism, the cookie's attributes, the session's limits and the role recheck with reasons; `ldap/README.md` gives a working Active Directory configuration; the changelog says, in its own words, that 6.0.0 shipped with four defects "none of which could be seen by a test that ran a service on its own." A reader of these documents will understand the system that exists.

The design record did not move. The architecture specification is now five releases and one major version behind and still says routing is "Designed, not built" and that "the agent connects as `nl2sql`, the owner." The identity model, the one decision a reviewer most needs to hold the code against, exists only as READMEs describing code, with no trust-boundary statement, no account of what a stolen cookie or a compromised page container can do, and no record of alternatives rejected. The posture statement that frames the whole Security section, "one person on one machine", is now contradicted by the product's own default: sign-in is on for everyone, while the same section still says the databases are published with development passwords and the README still tells the reader to connect as the owner, noting that the superuser has the same password. The documents describe a system that authenticates every person at the front door and leaves the back door open, and do not notice the contradiction.

Of the fifteen first-cycle findings in this document, one is resolved (there is an identity model), one is improved (two of three overstated controls now match the code), eight are unchanged and five are worse because the gap they describe widened with a release that had no design document. Two findings are new.

## 2. What the documentation does well

Preserved from the first cycle, and worth protecting in the plan:

- **Reasoning is still recorded.** The new READMEs say *why*: why Postgres rather than the auth service checks passwords (`auth/README.md`, "How a sign-in works"), why the session is asymmetric (`auth/nl2sql_identity/tokens.py` docstring, quoted in the README), why there is no JWT library, why MLflow's own login was not used (`docker/mlflow-proxy/Dockerfile:3-7`), why the directory has its own certificate (`ldap/nl2sql_ldap/tls.py:3-7`).
- **Limits are stated.** "What a session is worth" (`auth/README.md:55`) says the groups in a session are a starting point and the database is asked again; the throttle section says the directory's lockout is the real limit. Documentation that says what a control does *not* do is rare.
- **The changelog is honest about process.** The v6_0_1 entry opens by saying the correction was "found by doing what a user does", names the four defects, and says why the suite missed them. That is the raw material for a test-strategy finding (C-13 in the implementation review), and the project wrote it down itself.
- **Numbers are still tested.** `tests/docs/test_versions.py` now holds thirty-two version declarations and seventeen tags; the README's test counts are read from the collector.

## 3. Findings

### 3.1 Spec currency and supersession

**D-01 · High · Verified · Worse — The design authority is five releases and a major version behind.**
`Multi-Agent_NL2SQL_arch5_2.md:11` still describes routing as "**Designed, not built**", and line 785 still says "**Today the agent connects as `nl2sql`, the owner.**" Since the first cycle the repository shipped 6.0.0 and 6.0.1: a directory (`ldap/`), an auth service (`auth/`), a shared identity package, four new images, a rewrite of the retail database's `pg_hba.conf` on every start, per-person Postgres roles, HTTPS on every interface, and a rule that questions run as the person who asked. None of it has an architecture document; `multi-agent_arch_specs/` has no file newer than 2026-09-28. First-cycle plan item V6-43 (write arch6) is open, and the thing it would have to describe has doubled.
*Why it matters:* the repository's rule makes the highest-suffixed spec the authority. A reader who follows it is now told the agent connects as the owner, that routing does not exist, and nothing at all about who may call what.

**D-02 · Medium · Verified · Worse — Decisions are made and never recorded as decisions.**
The first cycle's plan opened with five owner decisions (V6-01 to V6-05). One was made, and made larger than recommended: V6-01 chose authenticated principals, and the implementation chose a directory, four groups and database-checked passwords. The other four (the sensitive-column policy, the topology, the review service's write model, the benchmark wording) are untouched. The four design answers that shaped 6.0 (on by default; SQL as the signed-in user; four groups; HTTPS everywhere) appear in `auth/README.md` and the changelog as descriptions of behaviour, not in any spec as decisions with alternatives. The spec's own open-decisions list is unchanged from 5.2.

### 3.2 The security architecture as written

**D-03 · High · Verified · Worse — The security blueprint describes none of the roles that now exist.**
Section 8 of arch5.2 (line 785) still lists role-level `statement_timeout` and `work_mem` that `docker/reader_role.sql` does not set, and the stale "connects as the owner" sentence. It is now also silent on everything 6.0 added to the cluster: the `nl2sql_ldap` marker role and the pg_hba `ldap` method that sends its members' passwords to the directory, the four group roles and what each may read, the `nl2sql_rolesync` login with `CREATEROLE` and `ADMIN` on five roles, the per-person `LOGIN` roles with a connection limit and a 60-second statement timeout, and the reader's `SET`-only membership of every person. The nearest thing to a blueprint is the 32-line comment at the top of `docker/auth_roles.sql`, which is accurate and is not a design document.
*Why it matters:* the one table meant to be a security checklist cannot be used to check the thing that most needs checking.

**D-04 · High · Verified · Unchanged — The sensitive-column policy is documented and cannot take effect.**
Spec 7.3 rule 3 and `agent/API.md`'s `audit.redactions` field still promise it; `present.tagged_sensitive_columns` (`present.py:249`) still has no caller from the graph (I-01). The first cycle's point stands that even wired up it would redact last, after rows have reached the model host, the job store, the feedback snapshot and the trace. First-cycle decision V6-02 was not taken.

**D-05 · Resolved — There is an identity model.**
The first cycle said no document named where a principal would come from. Now `auth/README.md` says exactly where: a person signs in with a directory password that the retail database checks through pg_hba, the auth service signs an Ed25519 session naming them and the groups Postgres says they hold, every service verifies it with the public half, and `Identity.principal` reaches `SET LOCAL ROLE` in the executor, the planner gate, the console and the review validators. Reviewer attribution is the signed-in name. This is a complete answer to D-05 as written. Two residues are recorded elsewhere: the model exists as documentation of code rather than as a design (D-16), and the row-level-security row it was meant to feed is still "production requirement, not implemented": a person's role has the same `SELECT` on every table as the reader, so running as them is attribution, not isolation (I-18).

**D-06 · High · Verified · Worse (raised from Medium) — The documented posture now contradicts the documented default.**
`USAGE_GUIDE.md:1014`: "The stack is set up for one person on one machine." The same section's first bullet says sign-in is on and every page, the API, the console, the review service and MLflow need a person in the right group. A stack whose default is to authenticate people is not set up for one person. Three bullets later (line 1038): "The databases are published on their ports with the development passwords compose defaults to." `README.md:1519` tells the reader to `psql postgresql://nl2sql:nl2sql@localhost:5432/nl2sql_retail` and adds "(the `postgres` superuser has the same password)". So the documents describe, without reconciling them, a front door that checks every person against a directory and a back door on every interface with a known superuser password. There is still no threat model and no deployment tier (V6-45 open). The one new posture statement, HTTPS everywhere, is documented with the honest caveat that every browser will warn until the machine trusts a self-signed certificate.
*Why it matters:* 6.0's major version was justified "because the defaults change under existing users." The default that changed is the front door; the back door's default is unchanged and the documents say so in a sentence that reads as a footnote.

**D-07 · Medium · Verified · Improved — Two of the three overstated controls now match the code; one new overstatement.**
- `review/README.md:610` now reads "With sign-in off, the token is not optional the way the agent's is", which matches `settings.py`'s warning (a start-up warning, not a refusal). The compose comment at `docker-compose.yml:569`, "Not optional in the way API_TOKEN is", is the unqualified sentence from the first cycle and is now the stale one.
- `agent/API.md:210` says the query-string token is accepted "on event streams only", and that is now true: `Guard.identify(query_token=True)` is used by one dependency, the stream's (`api/app.py:318`).
- `agent/API.md:290` still says "Each answer's trace names the model that actually answered", and the REST `TraceEntry` still has four fields (I-03, unchanged).
- New: `USAGE_GUIDE.md:352` says "the database knows who asked, not just that the agent did." It knows inside the transaction, where `current_user` is the person; the connection is still the reader's, so `session_user`, the connection log and `pg_stat_activity` say the agent asked (I-18).

### 3.3 Decomposition and topology as designed

**D-08 · Medium · Verified · Worse — Nine servers, twenty-three services, thirteen profiles, asserted.**
The deployment is now eight Postgres servers plus OpenLDAP, twenty-three compose services, fourteen volumes and thirteen profile names. The additions are each explained (`ldap/README.md` says why OpenLDAP and why Alpine; the compose comments say why the auth service is its own image), and the choice of a directory was the owner's. What is still missing is the weighing the first cycle asked for: no document compares a store per server with a shared cluster, and the cost is still paid in the documents themselves (seven published store ports, seven passwords, the "reader role recreated on every start" story now told for retail, snippets, feedback and the per-person roles). First-cycle decision V6-03 was not taken.

**D-09 · Medium · Verified · Unchanged — The review service's write model is undiscussed.**
`docker-compose.yml:630-640` still presents the read-write bind mount of `./context_questions` as "the whole point of the service"; the review image still has no `USER`; no document describes concurrency between two reviewers or a recovery procedure. 6.0 made reviewers real people and left what they write to unchanged. Decision V6-04 was not taken.

**D-10 · Low · Verified · Unchanged — Called services, released as a modular monolith.**
Thirteen image families now move together at one version (`tests/docs/test_versions.py`); the documents still call them services and never name the rule.

### 3.4 The state contract and failure semantics

**D-11 · High · Verified · Unchanged — Field lifetimes are undocumented, and the defect they caused is still there.**
The state contract still does not say which fields are reset when the repair loop re-enters `generate_sql`; `graph.py:1068` still reads the previous attempt's `audit` into the new attempt's first narration (I-02). The lines are the same as in the first cycle because `graph.py` did not change.

**D-12 · Medium · Verified · Unchanged — `retrieval_errors` still carries the narrator's failure** (`graph.py:1088`).

### 3.5 Consistency between documents

**D-13 · Medium · Verified · Unchanged — Narrative drift is untested.**
`gui/src/api/useAsk.ts:5` still says "fourteen pipeline nodes"; `agent/API.md:290` still makes the trace claim; `agent/README.md` is now titled "(v6, multi-agent)" and opens with a version-by-version narrative ("v2 adds retrieval", "v5 checks", "v4.1 adds", "v5.3 adds", "v5.6 adds", "v6 asks who is asking") that restates the changelog in a second place. The facts about sign-in are now stated in `README.md`, `USAGE_GUIDE.md`, `QUICKSTART.md`, `auth/README.md`, `agent/API.md`, five interface READMEs and the compose comments.

**D-14 · Low · Verified · Unchanged — Benchmark independence is documented as a guarantee the repository no longer keeps.**
The wording is unchanged, and the test that checks it, `test_no_benchmark_question_is_a_golden_pair_verbatim`, fails on every run (B03 is Q46 word for word); the v6_0_1 changelog records the failure as expected. The first cycle described the test as if it passed; it did not check. See C-11.

### 3.6 The testing strategy as documented

**D-15 · Low · Verified · Unchanged — No documented security tier, and the first posture tests arrived unnamed.**
`tests/auth/test_auth_compose.py` now asserts that every interface is HTTPS unless one variable says otherwise and that CORS is off on the auth service unless origins are named. Those are posture tests, the kind V6-49 asked for, and nothing in `README.md`'s testing section names them as a tier or lists what the tier defends: default binds, default passwords, `USER` in Dockerfiles, constant-time compare, the pg_hba rules. The changelog's own account of 6.0's four shipped defects is also a statement about the strategy: 100% coverage in every language and a live test per service did not include running the stack the way a user does. That is C-13 in the implementation review.

### 3.7 New since the first cycle

**D-16 · High · Verified · New — The largest design change since arch4 has no design document.**
The identity model is documented as code: `auth/README.md` says what the auth service does, `ldap/README.md` what the directory does, the compose comments why each container exists. Missing is the design: a statement of trust boundaries (what a compromised GUI container can reach with the certificate volume it mounts; what a stolen cookie is worth for eight hours; what the static service token can do), the alternatives weighed (a users table in Postgres, an identity provider, MLflow's own basic auth is the one alternative whose rejection is recorded), the reasons for the limits chosen (eight-hour sessions, a 60-second role recheck, five failures per name and fifty per address, a 30-second role sync), and the properties the system is meant to have (a removed person loses access within a minute; a person's password never reaches a service other than the auth service; one sign-in covers every page on a host). Every one of those is implied by the code and stated by no document a reviewer could hold the code to. The user's four design decisions are recorded only in the changelog's prose.
*Why it matters:* the next changes to sign-in that this review asks for (revocation, a stricter default, TLS to the database) will each be a change to a design nobody has written down, and the spec that is supposed to hold it says the agent connects as the owner.

**D-17 · Medium · Verified · New — Direct database access by people is documented as a feature and its transport is not.**
`auth/nl2sql_auth/rolesync.py:14-15` describes a person's login as "read-only and time-limited when they connect directly, with psql or a BI tool"; the v6_0_1 changelog records checking "psql straight to the retail database with a directory password" as a feature of the release. No document says that a connection authenticated by pg_hba's `ldap` method sends the password in clear text, that the retail Postgres has no TLS, or that the port is published on every interface, so a person following this guidance from another machine sends their directory password across the network unencrypted (S-16). `USAGE_GUIDE.md`'s Security section does not mention it.

## 4. Mitigation, repair and improvement plan (documentation)

The first cycle's twelve items, with their status, and the items this cycle adds. The repository rule that a spec is never edited in place is respected: spec changes go into a new `Multi-Agent_NL2SQL_arch6.md`.

| # | Action | Addresses | Status / depends on | Effort |
|---|---|---|---|---|
| P1 | **Record the identity model as a design decision.** Made in code (principals from a directory, four groups, Postgres as password authority); not recorded. Becomes the identity section of arch6 (P13). | D-05, D-16 | Decided; unrecorded | S |
| P2 | **Decide the fate of the sensitive-column policy.** | D-04 | Open since cycle 1 | S (decision) |
| P3 | **Write `Multi-Agent_NL2SQL_arch6.md`** superseding 5.2: current status; sections for the console (5.3), reopen/undo (5.4), tracing (5.5), snippets and curation (5.6), **and sign-in (6.0)**; routing departures as decisions; open decisions closed; field-lifetime table; node-failure channel. | D-01, D-02, D-11, D-12, D-16 | P1, P2, P13; code fixes I-01/I-02/I-03 | L |
| P4 | **Rewrite the security blueprint as a verified table**: every row names file, line and test. Rows now needed: the pg_hba block and its order, the marker role, the four group roles and their grants, the rolesync login's powers, the per-person role's limits, the reader's `SET` grants, the session's signature and lifetime, the recheck interval, the throttle, the directory's lockout. Remove the two stale sentences. | D-03, D-07 | P3 | M |
| P5 | **Threat model and deployment tiers** (`SECURITY.md`): assets, actors, trust boundaries (browser → nginx → service → Postgres → directory → model host), what each container holds (the certificate volume, the public key, the signing key), three named tiers with the compose settings each requires. Replace "one person on one machine" with the tier the defaults implement. | D-06, D-16 | P1 | M |
| P6 | **Correct the overstated controls in place**: `docker-compose.yml:569`; `agent/API.md:290` **(code first, I-03)**; `USAGE_GUIDE.md:352` to say what the database knows and where **(or code first, I-18)**. | D-07 | I-03, I-18 | S |
| P7 | **Record the topology decision** (nine servers, or a consolidation target) and the "released together" rule. | D-08, D-10 | Open since cycle 1 | M |
| P8 | **Document the review service's write model** or the replacement for it. | D-09 | V6-04 | S |
| P9 | **Narrative-drift tests**: forbidden-phrase list ("Designed, not built", "fourteen pipeline nodes", "connects as `nl2sql`, the owner", "one person on one machine" once P5 lands); a spec→module→test conformance table read by a test. | D-13 | P3 | S |
| P10 | **`benchmarks/README.md`**: describe the independence the repository actually keeps, and make the test agree (C-11). | D-14 | — | S |
| P11 | **Name the security tier** in `README.md` and move `tests/auth`'s posture tests under it with the rest of V6-49. | D-15 | V6-49 | S |
| P12 | **One home per fact**: the sign-in facts are now in nine places. | D-13 | P3 | M |
| P13 | **New. Design, not description, for sign-in** (an arch6 section or `SECURITY.md` section): trust boundaries, what each credential is worth and for how long, the alternatives rejected, the limits chosen and why, the properties promised. Write it against the code as it is, then hold the code to it. | D-16 | — | M |
| P14 | **New. Say what direct database access costs**: in `USAGE_GUIDE.md`'s Security section and `auth/README.md`, that the `ldap` method is clear-text password authentication and needs the database's TLS (S-16) before a person connects from another machine; or withdraw the feature from the documents until it does. | D-17 | S-16 fix, or a decision | S |

Effort key: S under a day, M one to three days, L a week or more.
