# The second review's plan: what was missed

**Second cycle:** `v6_1_review`, commit `9b0b340` (release 6.0.1), 2026-10-04 -- the plan [`v6_1_review_mitigation_plan.md`](v6_1_review_mitigation_plan.md), 71 items `V6-01`..`V6-71`, to be shipped as 6.1 (Phases 1 and 2), 6.2 (Phases 3 and 4) and 6.3 (Phase 5).
**Checked at:** release 6.3.0, 2026-10-07, commit `15ac4f7`, after the three releases had shipped.

This document lists every plan item that the three releases did not close, what each has instead, and what would close it. It is written so that the third cycle starts from it rather than from the plan's statuses, which are those of commit `9b0b340`.

## How it was checked

Every `V6-nn` id was looked up in the changelog entry of each release, to see which release claimed it, and in every tracked source, test, specification and document file outside this folder, to see what cites it. Every id is cited somewhere; the misses are the ids that no release's entry cites, read one by one against the code, the tests and [`Multi-Agent_NL2SQL_arch6_3.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6_3.md), whose closing paragraph is the 6.3 release's own account of what it left. Items a release cites were taken as done where the tests citing the id exist and pass; the whole suite -- the offline tier, the docker tier against a stack built from the checkout, the interface and desktop suites, and the acceptance tier -- passed on the day of the check, with every Python statement and branch and every shell command measured as run.

| Status at 6.3.0 | Items | Count |
|---|---|---|
| **Done** | 6.1: Phases 1 and 2 with V6-43, V6-44, V6-45, V6-46, V6-67, V6-68, V6-69; 6.2: Phases 3 and 4; 6.3: V6-34, V6-35, V6-37, V6-38, V6-39; and V6-01, V6-02 (resolved by removing the claim), V6-04, V6-05, V6-10, V6-21, V6-30, V6-49, V6-51, V6-70, V6-71 | 65 |
| **Superseded** | V6-09 | 1 |
| **Partial, by decision** | V6-40, V6-41 | 2 |
| **Missed** | V6-42, V6-47, V6-48 (one remnant), V6-50, V6-65 (unrecorded) | 5 |

The five missed items, and the two partial ones, are below. Three of the five were closed or narrowed on the day of the check; the rest need a decision or a release.

## The misses

### V6-42 -- Provenance in publishing (Phase 5; K10, M-09)

**The plan asked for:** SBOM and provenance attestations on every published image, a signature, and an `org.opencontainers.image.revision` label that [`tests/docker/test_published_images.py`](../tests/docker/test_published_images.py) reads back from the registry.

**What 6.3.0 has:** every Dockerfile labels its image with `org.opencontainers.image.title`, `.description` and `.version`, and the published-images test holds the version label to `__version__`. Every base image and stock image is pinned by digest ([`tools/pin_images.py`](../tools/pin_images.py), V6-35). Nothing records the commit an image was built from, nothing attests how it was built, and nothing signs it. [`docs/SECURITY.md`](../docs/SECURITY.md) names this among the known limits: the project's own images are pinned by tag and carry no signature or provenance.

**Why it was missed:** the 6.3 release took Phase 5's hardening, digests, proxy, secrets and limits and left this item, saying so in the specification's closing paragraph. No later release followed.

**What would close it:** `org.opencontainers.image.revision` and `.source` labels set from build arguments in the publish commands in [`docs/images.md`](../docs/images.md); `--provenance=mode=max --sbom=true` on the `buildx build` that pushes; a signature after the push; and the test asking the registry for the label and the attestation of each tag `setup.sh` pins. A published tag never moves, so none of this reaches an existing tag: it lands with the next release's.

**Status after the check:** open.

### V6-47 -- One home per fact (Phase 6; P12, D-13)

**The plan asked for:** each port, default, setting and flag stated once and linked elsewhere, with [`tests/docs/test_docs.py`](../tests/docs/test_docs.py) extended to the single sources; the finding counted the sign-in facts in nine places.

**What 6.3.0 has:** a design home for sign-in ([`docs/SECURITY.md`](../docs/SECURITY.md) and arch6 section 20), a how-it-works home ([`docs/sign_in.md`](../docs/sign_in.md)) and a how-to ([`docs/USAGE_GUIDE.md`](../docs/USAGE_GUIDE.md), "Signing in"), and tests that hold every page's settings table to compose. The facts are still restated rather than linked: outside this folder, the specifications and the changelogs, the four groups are named in thirteen documents, the directory page's port in twelve, the first administrator's password file in fifteen and the CA certificate's file in twenty-three.

**Why it was missed:** Phase 6 shipped with 6.1 as the documents it names -- arch6, the sign-in design, `SECURITY.md`, the corrected controls -- and this item, the one that would have removed text rather than added it, was not among them. The documentation reorganisation of 2026-10-07 moved the README's sections into `docs/` without consolidating across the components' READMEs.

**What would close it:** choosing the home of each sign-in fact -- the groups and what each lets in, the first password, the directory page's address, trusting the CA -- then replacing every other statement with a link to it, and a docs test that the groups' table and the port appear in prose only in their home.

**Status after the check:** open.

### V6-48 -- Comment policy (Phase 6; H6, C-05)

**The plan asked for:** no version numbers in code comments, TODOs that reference an issue, and two stale comments fixed -- "fourteen pipeline nodes" in the web interface's `useAsk.ts`, and a "should move there" note closed by V6-22.

**What 6.3.0 has:** no TODO, FIXME or similar marker anywhere in the sources; the "should move there" note gone with V6-22. The comment at `gui/src/api/useAsk.ts:5` still said "fourteen pipeline nodes", as both cycles had reported, while the pipeline has nineteen. On version numbers the code went the other way: release numbers and finding ids are cited throughout the sources and tests -- "(6.3)", "(V6-39)" -- as the way a reader is told which release made a line what it is.

**Why it was missed:** the comment was a one-line fix of effort S in two plans and no release's bullet, and nothing read the comment. The version-number rule was never decided for or against.

**What would close it:** the comment is fixed (2026-10-07), and a narrative-drift test now holds the phrase absent from every document and source file (see V6-50). The version-number rule needs a decision in the third cycle: adopt the plan's, or record the citing style as the policy -- it is the style every hardening release has used on purpose.

**Status after the check:** the remnant fixed; the policy question open.

### V6-50 -- Conformance and narrative-drift tests (Phase 7; P9, D-13)

**The plan asked for:** a forbidden-phrase list for documents and comments -- "Designed, not built", "fourteen pipeline nodes", "connects as `nl2sql`, the owner", "one person on one machine" once `SECURITY.md` landed -- and arch6's spec→module→test table read by a docs test.

**What 6.3.0 had:** neither. The phrases had been retired one by one by the releases that made them false, and nothing checked that they stayed retired; one had not (V6-48). The specifications' tables were read by nobody, and arch6's security blueprint still named `docker/auth_roles.sql` and `docker/ldap_hba.sh`, which 6.3 moved into `dbprep`.

**Why it was missed:** Phase 7 shipped with 6.1 as the security tier (V6-49) and the acceptance tier (V6-67); the tests about the documents were left with Phase 6's item V6-47, which was left too.

**What would close it:** two tests were added on 2026-10-07 in `tests/docs/test_docs.py`: the four phrases must be absent from every tracked document and source file outside this folder, the specifications and the changelogs, which quote them as history; and the newest specification's security blueprint -- the one whose rows describe this tree, since a specification is never edited in place and a higher suffix overrides a lower -- must name only files and tests that exist. The conformance table in the plan's sense is not written: arch6's section 10 maps components to modules with no test column, and the later specifications' section 10 is prose. The next specification should carry a full table -- section, module, test -- and the test should read it.

**Status after the check:** the phrases held; the conformance table open.

### V6-65 -- Decouple start-up from the API's certificate (Phase 5; A20, I-19)

**The plan asked for:** nothing `depends_on` the API for TLS material once each service has its own identity (V6-36).

**What 6.3.0 has:** it is true, and has been since 6.1's `pki` one-shot issued each TLS server its own key in its own volume: only the API and `pki` mount the API's identity, every page and service mounts its own, and the outside client mounts only the CA's certificate. No release's entry cited the item and no test held it, so it was true by consequence rather than by promise. One remnant: `launch.sh` copies the stack's CA certificate out of the API's container for the desktop client to trust -- the client's convenience, not a service's start-up, and the certificate is in every identity's volume.

**Why it was missed:** it was achieved by V6-36 and recorded under V6-36.

**What would close it:** a test, added on 2026-10-07 to [`tests/security/test_posture.py`](../tests/security/test_posture.py): nothing but the API and the one-shot that issued it holds the API's identity.

**Status after the check:** held.

## Partial by decision

### V6-40 -- Store consolidation (Phase 5; A12, K11, I-12, D-08)

**Decided and done:** V6-03's option (b), staged. The feedback, corrections, completions and snippet stores are four databases in one pgvector server, `nl2sql-stores`, with an owner per database and no owner a superuser; a store from before is moved in by `storesmigrate` once, with a marker. The decision to stop there -- the RAG stores and MLflow's store stay servers of their own -- is 6.3's, recorded in arch6_3 sections 2 and 14.3: what ships inside a published image stays in its image.

**Left, as the specification says:** the RAG stores' own preparation. `dbprep` sets their logins' passwords over their sockets; everything else about them -- roles, extensions, data -- is built into the published images by the scripts in `rag/`.

### V6-41 -- Orchestration to Python (Phase 5; A15, H8, I-15, C-06)

**Decided and done:** the database preparation is a Python one-shot, `dbprep` (`common/nl2sql_ops`), over each database's own socket, rather than the auth service as the plan proposed -- arch6_3 section 21.4 says why. The scripts run no SQL of their own.

**Left, as the specification says:** the orchestration itself. `start.sh`, `setup.sh` and `launch.sh` are still shell -- 1,734 commands, every one of them run by a test -- and whether they become a Python command, or stay shell by decision, is for the third cycle to settle and the next specification to record.

## Traceability: the findings these leave

| Finding | Status at 6.3.0 | Item |
|---|---|---|
| M-09 (K10) | Unchanged | V6-42 |
| D-13 (P12, P9) | Improved: the phrases are held, the newest spec's blueprint is held; one home per fact is not | V6-47, V6-50 |
| C-05 (H6) | Improved: the comment fixed and held; the policy undecided | V6-48 |
| D-08, I-12 (A12, K11) | Improved: the runtime stores consolidated; the RAG stores apart by decision | V6-40 |
| C-06, I-15 (A15, H8) | Improved: no SQL in the scripts; the orchestration still shell | V6-41 |
| I-19 (A20) | Resolved, and now held | V6-65 |

## Not misses, for the record

Four items the plan's table left Open were closed by a decision rather than by the action the table named, and the third cycle should not re-open them as missing: V6-02 and V6-17, the sensitive-column policy, resolved in 6.1 by removing the claim and the code that never applied it; V6-03, the topology, decided as option (b) and staged (V6-40 above); V6-05, the benchmark wording, decided, with the documentation and the test following in 6.1 (V6-59); V6-70 and V6-71, direct database access and the revocation model, decided as the recommended options and implemented as V6-52, V6-53 and V6-69, and as V6-61.

## For the third cycle

Start from the five items above and the two partial ones. Verify live, rather than assume, what the second cycle's Critical rested on -- V6-06, V6-07, V6-52 and V6-53 -- since the acceptance tier checks a stack built from the checkout and not a published one. Keep every id; the vocabulary for an item is still Done, Partial, Open or Superseded, and for a finding Unchanged, Worse, Improved, Mitigated, Resolved or New -- with Regressed added if anything here has gone back.
