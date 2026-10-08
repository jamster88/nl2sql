# Changelog -- the short version

What each version added, updated or fixed, one line per change, newest first.
[`CHANGELOG.md`](CHANGELOG.md) has the same history with every major artifact
each version created, updated or fixed.

Version numbers are the agent's: `__version__` 5.1.2 is the image tag `v5_1_2`.
The app images -- agent, web interface, review service, review interface, the
SQL console's interface, the desktop client's jar, since v5_5 MLflow's
server and store, since v5_6 the curation interface, and since v6_0 the
directory, the auth service, the directory page and MLflow's front door --
every page and the front door one image, the proxy, since v6_3 -- are released together
at one number. The three
dataset images (`retail-postgres`, `rag-vectordb`, `rag-chunkdb`) version on
their own and are listed under the release they shipped with. A version marked
*unpublished* is a checkpoint in the repository that published no image tag.

## v6_3 (6.3.0) -- 2026-10-05

**Added**
- Every container is read-only, holds no capability it does not use, cannot gain a privilege, and has a ceiling on its memory and processes.
- Every password and token is a file in `secrets/`, mounted into only the services that read it; none is in any container's environment.
- One image, `nl2sql-proxy`, serves every page and MLflow's front door; twelve tags where there were seventeen.
- Every health check verifies the certificate it is answered with.
- The feedback, corrections, completions and snippet stores are four databases in one server, `nl2sql-stores`; the old stores' containers are stopped and removed, and `launch.sh` moves what they held into it.
- A one-shot, `dbprep`, prepares every database in Python over its own socket; neither script runs SQL.
- Every role a service connects as has a statement timeout, a memory ceiling and a connection limit.
- Every base image and stock image is pinned by digest; `tools/pin_images.py` checks and moves them.

**Updated**
- `.env` holds no password; `setup.sh` and `launch.sh` move the ones an older `.env` holds into `secrets/`.
- The first sign-in's password is `cat secrets/ldap_admin_password`.
- `setup.sh --proxy-image`/`--proxy-tag` replace the six pages' flags.
- `--feedback` is the same as `--api`: verdicts are staged whenever the API is up.
- `launch.sh --api` replaces `nl2sql-ca.crt` when the stack's CA has changed, and says to trust it again.
- MLflow's store URL is built from the password file, never on its command line.

**Fixed**
- The retail database's health check could pass while its entrypoint's socket-only server was still setting passwords; it asks over TCP.
- An upgrade from 6.2 left the old stores' containers running, one on the runtime stores' port; they are stopped and removed first, their volumes kept.

**Published**
- All twelve tags as `v6_3`, `nl2sql-proxy` among them for the first time; the dataset images are unchanged. The upgrade was rehearsed on a stack of its own first, then `start.sh` upgraded a running 6.2 stack -- its stores moved, its passwords into `secrets/` -- with 98 checks passing.

**After publishing** (not in the images)
- The coverage exclusions are the one the README names again; tests run each module's entry point instead.
- dbprep, MLflow's server and the store migration are checked both ways against what compose gives them.
- Every page's settings are checked by their `.env` names, and the review and directory pages, the outside client and dbprep have settings tables.
- The checks every page's template shares are made once, not four times; a handful of duplicate, dead or vacuous tests are gone.
- The RAG integration test runs as a stack of its own rather than in the running one's project.
- The proxy's start-up tests need gettext's `envsubst`, and skip without it.
- The README is a front page -- the quick start, what is new, an index -- and what it held is a document a topic in `docs/`, where the usage guide, the quick start, `SECURITY.md` and the changelogs now live too; the tests follow each fact to the document that holds it.
- "Twenty-eight of those 656 need the embedding host" re-counted with the host down: 28 of 758; the three DDL-vector tests skip naming the embedding host rather than the vector store; a test holds both numbers.
- The second review's 71 plan items verified at 6.3.0: 65 done, one superseded, V6-40 and V6-41 partial as the 6.3 spec says, V6-42 (provenance) and V6-47 (one home per fact) open. Fixed: the "fourteen pipeline nodes" comment. Added: the narrative-drift test, the newest spec's blueprint held to the tree, and a test that only the API holds its identity. `adversary_reviews/v6_1_review_misses.md` lists what was missed, for the third cycle.
- The next architecture designed, not built: `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7.md` and its diagram -- the question reworded three to ten ways, each held to the original's contract, the arch6 pipeline run once per wording, one after another unless the deployer says the model host serves more calls at once (`OLLAMA_PARALLEL_CALLS`, 1 by default), the results validated against their own question and against each other with the benchmark's scorer, the largest agreeing group chosen and fused -- columns (`ENSEMBLE_FUSE_COLUMNS`, on by default), claims, assumptions and the dissent -- a Judge only when the votes cannot decide; the pipeline a larger tool calls for its hardest questions. `Multi-Agent_NL2SQL_arch7_implementation.md` is its build plan, module by module.

## v6_2 (6.2.0) -- 2026-10-04

**Added**
- A session can be ended: signing out, a password changed or set, a lock or a removal ends it for every service within a minute.
- A service token is named and holds only the roles it is given; what it does is recorded under its name, never a header's.
- An administrator can make the agent read its catalogs again (`POST /v1/admin/reload`).
- The sign-in throttle believes `X-Forwarded-For` only from the page proxies (`AUTH_TRUSTED_PROXIES`).
- A person's name in each transaction's `application_name`, where the database's own views and log show it.
- The code every service shares is one package, installed as one, at the release's version; what every page shares is one source package, `web/`.
- Every Python image installs a hash-checked lock.

**Updated**
- Nothing runs as root but the one-shot `pki` service; each key belongs to its service's account, and the review service writes the checkout as the person who owns it.
- Every route is on a router that carries its guard.
- The review service loads the stores in its own process, under a lock, with no password on a command line.
- A failure's own words -- hosts, drivers, configuration -- are an administrator's to see.

**Fixed**
- A deleted fix's id was given to the next fix; ids come from sequences.
- The desktop client's sign-out told nobody; it ends the session at the auth service.

**Published**
- All seventeen tags as `v6_2`; the dataset images are unchanged. The acceptance tier passed first, and `start.sh` then upgraded a stack to them with every check passing, a signed-out session refused everywhere and nothing running as root among them.

## v6_1 (6.1.0) -- 2026-10-04

**Added**
- A development certificate authority: a one-shot `pki` service gives every server its own key and certificate, and a client trusts `nl2sql-ca.crt` once.
- `nl2sql-retail-postgres:v1_2`: no password in the image, TLS on, nothing over the network without it, and the superuser not over the network at all.
- `SECURITY.md`, the threat model and the deployment tiers; `Multi-Agent_NL2SQL_arch6.md`, the architecture as built, with the sign-in design.
- The acceptance tier (`--run-acceptance`): the whole stack, built from the checkout and started with `setup.sh` and `start.sh` beside any other, used on every page; four of its tests are 6.0's four shipped defects.
- `NL2SQL_INSTANCE` names a stack, so a second one runs beside the first; `setup.sh --build-all` builds every image from the checkout instead of pulling.
- `tests/security/`: every route guarded, every wire model strict, the compose posture.
- The live tests find the databases' passwords where compose does, and fail rather than skip when a database refuses them.

**Fixed**
- A repair started from the failed attempt's leftovers; it starts clean now.
- A narrator or supervisor that failed said nothing; `node_errors` says so.
- Moving the API's, the review service's, the console's or the auth service's port in `.env` left every page asking the old one; they follow it now.
- `start.sh --desktop` did not tell the window where to sign in, so with `AUTH_PORT` moved it signed in nowhere; it passes the auth service's address now.
- The review service's banner said `auth NONE` over a service that required sign-in; it says sign-in, and names its own certificate.
- The sensitive-column redaction promised a policy nothing applied; the claim is gone.
- The trace over REST dropped the model, rung, route and hops; it carries them, and the interfaces show the model.
- Every wire model refuses a field it does not know.
- The benchmark test that failed on every run names the two overlaps the owner kept.

**Updated**
- The databases answer on this machine only unless `DB_BIND_ADDRESS` opens them, and every store password is generated.
- Sign-in to the retail database is over TLS, verified; the directory's API answers on its own port, behind its page.
- Sign-in is on in the code as well as in compose; CORS closed until opened; the job queue bounded (429); `EXPLAIN` time-boxed; tokens scrubbed from the access log.
- A replica refuses a clear-text primary; the pages honour `X-Forwarded-Proto`.
- Version 6.1.0 in every declaration; `setup.sh` pins `v6_1` and `nl2sql-retail-postgres:v1_2`.

**Published**
- All seventeen tags as `v6_1`, and `nl2sql-retail-postgres:v1_2` with `latest` moved onto it; the acceptance tier passed first, and `start.sh` then upgraded a running stack to them with every check passing.

## v6_0_1 (6.0.1) -- 2026-10-04

**Fixed**
- The directory could not write its certificate when compose made the auth service's container after its own, restarted for ever, and nobody could sign in; it now takes its directories as root and then runs as `ldap`.
- Five wrong passwords from one address -- everyone on one machine, or behind one proxy -- locked all sign-ins for fifteen minutes; an address now has its own limit of fifty (`AUTH_THROTTLE_ADDRESS_FAILURES`), a name still five.
- The desktop client said "Not connected." to a server that only wanted to know who it was; it offers to sign in instead.
- `launch.sh` did not pass `ldap_hba.sh` the directory's host, so the database was never prepared for sign-in.

**Updated**
- Version 6.0.1 in every declaration; `setup.sh` pins `v6_0_1`.

**Published**
- All seventeen tags as `v6_0_1`; `start.sh` upgraded a running stack to them and every sign-in check passed.

**After publishing** (the checkout, not the `v6_0_1` images)
- The container tests ask each page over HTTPS, verified; the least-privilege test says what it protects now that the reader may become people; `auth_roles.sql` no longer grants a second time what the role sync already granted.
- The usage guide covers upgrading to 6.0 and troubleshooting sign-in; still 100% coverage of the Python, the shell scripts, the desktop client and the five web interfaces.
- The second adversarial review cycle, `v6_1_review`, at 6.0.1: the five documents and their `_enhanced` editions again, every first-cycle finding given a status (8 resolved, 5 improved, 3 mitigated, 32 unchanged, 18 worse) and 19 new, the plan's 51 items tracked and 20 added, a comparison document, nine new figures; the first cycle's files untouched.

## v6_0 (6.0.0) -- 2026-10-03

**Added**
- Sign-in, on by default: a person signs in with the password a directory holds, which the retail database checks itself, and what they ask or run, runs as their own database role.
- Four groups decide what a person may open: `nl2sql-users` ask, `nl2sql-reviewers` review and read MLflow, `nl2sql-curators` curate, reviewers and curators use the SQL console, `nl2sql-admins` manage the directory.
- The directory (`nl2sql-ldap`): standalone, loaded from a CSV or LDIF file and edited on its page, or a read-only replica of Active Directory or any LDAP server, with each password checked by the primary.
- The auth service (`nl2sql-auth`, port 8446): sign-in, the session, and the database's roles kept in step with the directory.
- The directory page (`nl2sql-directory-gui`, port 8084), for administrators.
- MLflow's front door (`nl2sql-mlflow-proxy`): HTTPS, and sign-in asked about every request; MLflow itself is no longer published.
- Sign-in in the desktop client (`--auth-url`, `--user`).
- `--no-auth` for `start.sh`, `launch.sh` and `setup.sh`.

**Updated**
- Every web interface is HTTPS, asks who you are, and offers a password change and sign-out.
- The API shows each person only their own questions; a reviewer's name on a decision is the one they signed in as.
- `setup.sh` generates the directory's and the role sync's passwords into `.env`, which only its owner can read.
- `--mlflow` brings the API up too: the front door presents its certificate.
- Version 6.0.0 in every declaration; `setup.sh` pins `v6_0`, seventeen tags with the four new images'.

**Published**
- All seventeen tags as `v6_0`, the directory, the auth service, the directory page and MLflow's front door for the first time.

**After publishing**
- The stack run with `start.sh` found that nobody could sign in; corrected in `v6_0_1`.

## v5_6_1 (5.6.1) -- 2026-10-03

**Fixed**
- A narrator that names a number's cell in its sentence and lists no cells has its claims cited by what it wrote, so they survive the audit on the first pass instead of costing a rewrite -- or the whole narrative, when the rewrite failed too.

**Updated**
- Version 5.6.1 in every declaration; `setup.sh` pins `v5_6_1`.
- All thirteen app image tags published as `v5_6_1`.

**After publishing** (tests and documentation; no image changed)
- A coverage and relevance audit: still 100% everywhere; the snippet store's and the curation page's compose settings are exercised and documented with their defaults; two review settings documented; a fake switch nobody set and two duplicate tests removed.
- The first adversarial review (`adversary_reviews/`, tag `v6_x_review`): three reviews, one combined plan of 51 items, a summary of 66 findings, and figures generated as draw.io files, exported and tested.

## v5_6 (5.6.0) -- 2026-10-03

**Added**
- SQL snippets: joins, filters, measures and dimensions, each run against the database and written beside what it means, in `context_questions/sql_snippets.md` (32 to start).
- The snippet store (`snippetsdb`) and its loader, `rag/07_load_snippets.py`; `launch.sh` loads it whenever the document has changed.
- A fifth retriever in the agent: snippets found by keyword phrase and by meaning, shown to the SQL Generator when their tables are in scope (`--snippets`, `SNIPPETS_*`).
- The curation interface (port 8083, `start.sh --curate`): write SQL snippets, golden pairs, corrections and completions directly, each run against the retail database before it can be saved, or remove them.
- The review service's curation routes, and `/v1/schema`.
- The benchmark's fourth configuration, `snippets`, now its default.
- `arch_v5_6`, the architecture with the Snippet Retriever.

**Updated**
- Promotion runs the golden SQL against the database first; SQL that fails or returns no rows is refused.
- A fix can be stored without a submission, and removing a pair or a fix that came from one reopens it.
- `setup.sh` pins the review service whenever retrieval is on, since it carries the snippet loader.
- Version 5.6.0 in every declaration; `setup.sh` pins `v5_6`, nine app tags with the curation interface's.

**Fixed**
- A correct claim whose sentence named the cell it came from ("as shown in row 0, column ...") was dropped by the audit, which read the row number as a figure; on some answers that left no sentence at all. The address is kept out of the sentence now, and a row number is not held against a claim.
- A loader's output in the review interface ran together on one line.
- Before release: a snippet's note is shown to the SQL Generator with its SQL, and it is told to keep each piece as written; without them, an end-to-end run dropped a cast and answered 0.

**Published**
- All thirteen app image tags as `v5_6`, the curation interface (`nl2sql-curate-gui`) for the first time.

**After publishing** (the checkout, not the `v5_6` images)
- A narrator that writes the cell it read into its sentence and lists no cells has its claims cited by what it wrote, so they survive the audit the first time; the narrator's prompt is 5.5.1's again.
- `start.sh` checked live against the published images: it re-pins an older `.env`, pulls the thirteen tags, loads the snippets, and brings every page up.

## v5_5_1 (5.5.1) -- 2026-10-01

**Fixed**
- The golden question set stopped at Q99; it has no ceiling now, and Q100 follows Q99.
- The agent's `--help` said `--multi-shot` was off by default; it is on, and each switch's help now says what its setting is.
- `start.sh` re-pinned an agent version chosen with `setup.sh --agent-tag`; it keeps it now.

**Updated**
- The review interface no longer shows a maximum pair id.
- `agent/USAGE.md`: the seven-attempt budget, and stopping all three databases.
- Version 5.5.1 in every declaration; `setup.sh` pins `v5_5_1`.
- All twelve app image tags published as `v5_5_1`.

**After publishing** (dataset images, scripts, compose, tests and documentation; no app image changed)
- `rag-chunkdb` and `rag-vectordb` republished as `v3_2`, holding the golden set as the document has it (48 pairs); `setup.sh` pins them.
- `start.sh --load-golden` and `launch.sh --load-golden` load the golden question document into the stores on start.
- `launch.sh` warns when the stores hold a different number of golden pairs from the document.
- The review service embeds with the agent's embedding host (`EMBED_BASE_URL`).
- Two `docker compose ... logs` commands the scripts print when a page does not come up failed outright; they work now, and every compose command the scripts run or the docs show is checked against the real compose file.

## v5_5 (5.5.0) -- 2026-10-01

**Added**
- MLflow tracing: every question is one trace, a span per agent with what it read and wrote, and every model call inside it with its messages, answer, tokens and route.
- MLflow in compose, with a Postgres of its own, both published with the release: `start.sh --mlflow`, `launch.sh --mlflow` and `setup.sh --mlflow`, its interface on this machine only.
- A verdict given in the web or desktop interface is recorded on the trace of the answer it judges.
- The benchmark files each configuration as an MLflow run, with its questions' traces, scores and timings.

**Updated**
- `setup.sh` points the agent at MLflow in `.env`; whenever MLflow is not up the agent answers untraced.
- The CLI's `--json` names the run's trace.
- Version 5.5.0 in every declaration; `setup.sh` pins `v5_5`.
- All twelve app image tags published as `v5_5`, MLflow's server and store for the first time.

**After publishing** (scripts, tests and documentation; no image changed, no new tag)
- Every launch mode checked live against the v5_5 stack, the desktop client included.
- `launch.sh` and `start.sh` say a review can be taken back; `setup.sh` names the databases it actually started.
- `start.sh --help` shows how to bring up every page at once.
- MLflow's server settings documented and tested; a stale count of Dockerfiles corrected and now checked.
- `USAGE_GUIDE.md`, a full usage guide, and `QUICKSTART.md`, a quick start; tests hold both to the scripts, the agent's flags and compose.

## v5_4 (5.4.0) -- 2026-09-30

**Added**
- In the review interface, any submission can be put back to pending or deleted.
- A promoted pair comes back out of the golden set with it, checked by the loader's parser, the previous document kept and both stores reloaded.
- A stored fix is deleted from its store with its vector.
- A reopened submission keeps its work: the pair as its draft, or the corrected SQL in the query editor.

**Updated**
- All ten app image tags published as `v5_4`.

**Fixed**
- Tests across the review service, the RAG loaders and the agent failed once anything had been promoted, because they pinned the golden set at 45 pairs or expected every pair's SQL to end in a semicolon.

**After publishing** (tests and documentation; no behaviour change, no new tag)
- Seven paths the coverage report had been told to skip are tested, or removed where they did nothing.
- The agent's settings are checked against its README in full, and against compose in both directions.
- Stale test counts in the READMEs corrected, and the console interface's suite added to Coverage.

## v5_3 (5.3.0) -- 2026-09-28

**Added**
- The SQL console: the retail database queried as the agent's read-only role, under its limits and through its own validator and planner gate, with each gate's verdict beside the rows.
- Run, Plan (exactly the agent's planner gate) and Analyze (a timed run), the schema as the agent's introspection reads it, and the block of the agent's prompt for each table.
- The console's interface, a third React/TypeScript page and image, published on this machine only unless asked otherwise.
- `start.sh --console`, `launch.sh --console` and `setup.sh --console`.

**Updated**
- The planner gate's cost judgement is one function the gate and the console share; the agent behaves as before.
- The API's development certificate covers `nl2sql-console`.
- All ten app image tags published as `v5_3`, the console's interface for the first time.

## v5_2 (5.2.0) -- 2026-09-28

**Added**
- Model routing (arch5.2): every model call goes to the fastest model on the Ollama host measured to be suited to its task at the question's complexity, and a repair climbs from light to standard to heavy.
- A routed model that cannot answer falls back to `OLLAMA_MODEL`; with no catalog for its host the agent behaves as v5.1.
- `models/build_catalog.py`, which catalogues any Ollama host given its address.
- `models/calibrate.py`, which measures each model per task and rung against the reference model.
- The routing table in `/v1/meta` and the CLI, the answering model in every trace entry, and accuracy per model in the benchmark report.
- The architecture spec arch5.2 and its diagrams, and `arch_v5_2`.
- The development host's calibrated catalog, and the Modelfiles for its local builds.

**Updated**
- The routing settings, and a token cap and timeout on every model call, read from the environment and forwarded by compose, which mounts the catalog.
- `start.sh` starts Docker and this machine's Ollama when they are down, gives that Ollama the embedding model, and re-pins a `.env` older than the checkout.
- `start.sh --review` opens the review page in a browser window of its own.
- `launch.sh` says which models the calls will be routed to, or why they all go to one.
- Re-running `setup.sh` keeps every setting in `.env` it does not write itself.
- The tests: the review service's settings checked against compose, duplicate tests merged, and test code nothing ran removed.
- All nine app image tags published as `v5_2`.

**Fixed**
- A model call had neither an output cap nor a timeout, so a model that degenerated could generate without end.
- `launch.sh` and `setup.sh` checked the default chat host and model whatever `.env` said.
- Re-running `setup.sh` dropped settings added to `.env` by hand, such as `API_TOKEN`.

## v5_1_2 (5.1.2) -- 2026-09-26

**Added**
- Branch coverage for the Python code, gated at 100%: `coverage report` now fails below it.
- 24 tests for conditions that had only ever been tested one way.
- A test that pins the pglast behaviour the SQL walker relies on.
- `CHANGELOG.md` and this file, with tests that keep them in step with each release.

**Updated**
- Removed `Database.explain`, a v3 leftover that only its own tests still called.
- Removed five guards that could never be false, in the SQL walker and both chunkers.
- Removed dead test code: an unused fake method, fake modes and a fake `docker` case.
- The README's coverage figures, test counts, embedding-host recount command and tag table.
- All nine app image tags republished as `v5_1_2`.

**Fixed**
- A diagram row of an unknown kind was silently drawn as a copy of the row above it; it is now refused.

## v5_1_1 (5.1.1) -- 2026-09-26

**Updated**
- `agent/API.md` says which answer fields are markdown and which are plain text.
- All nine app image tags republished as `v5_1_1`.

**Fixed**
- Names with `&` in them came back escaped: the CLI printed `Meat &amp; Seafood`, and the markdown answer carried `&amp;amp;`.

## v5_1 (5.1.0) -- 2026-09-26

**Added**
- The review interface has one pane per verdict: correct, wrong, and correct but incomplete.
- A reviewer can correct a wrong or incomplete answer's SQL and validate it against the live retail database.
- A corrections store and a completions store (pgvector), each holding records and their embeddings.
- The architecture spec arch5.1 and its diagrams.
- Least-access tests for the read-only role, from both the agent's side and the reviewer's.
- Dataset image `retail-postgres:v1_1`, with the tightened read-only role built in.
- Dataset images `rag-vectordb:v3_1` and `rag-chunkdb:v3_1`, now for amd64 as well as arm64.

**Updated**
- `launch.sh --review` also starts the two new stores.
- `rag/publish_db_image.sh` builds multi-arch images from a database dump.
- The Docker Hub tests also check that the dataset images are multi-arch.
- All app image tags published as `v5_1`.

**Fixed**
- The read-only role could cancel or kill other sessions, and could connect to the cluster's other databases.
- The RAG images could not be pulled on amd64.
- The review interface showed `&amp;` in answers, and "1 corrections".
- A test overwrote the local copy of the published agent image with a source build.

## v5 (5.0.0) -- 2026-09-26

**Added**
- The answer contract: a name beside every id, the measure a ranking used, and the fiscal year assumed when none is given.
- The Completeness Reviewer, which sends an incomplete answer back for another attempt.
- A third verdict, "correct but incomplete", in the web and desktop clients and the review queue.
- The architecture spec arch5 and the v5 diagram.
- Tests that ask Docker Hub whether every pinned tag was published, for both architectures, at this version.

**Updated**
- The retry budget grew from four generations to seven.
- The answer states any default it assumed.
- All app image tags published as `v5`.

**Fixed**
- A "how many" question could be answered with a list.
- A measure the question named could be paraphrased and computed differently.
- A question naming a period could lose the calendar table from scope.

## v4_5_1 -- 2026-09-25 -- *unpublished*

**Fixed**
- Successive layout defects in the desktop client.

**Updated**
- New rule: a published tag never moves, so every later correction gets a new patch tag.

## v4_5 (4.5.0) -- 2026-09-25

**Added**
- The Java desktop client (JavaFX), with its jar published per platform.
- `/v1/meta` publishes the API's request limits.
- Tests that hold every version declaration in the repository to the same number.

**Updated**
- `launch.sh` and `start.sh` gained `--desktop`.
- All app image tags published as `v4_5`.

**Fixed**
- The desktop client read a 500 with an empty body as a successful answer.
- The `desktop` compose service was checked by no test that named it.

## v4_4 (4.4.0) -- 2026-09-24

**Added**
- The feedback system: verdicts staged in their own database.
- A review service and interface that promote verdicts into the golden questions.
- Feedback routes on the API, which writes through an insert-only role.

**Updated**
- The web interface sends its verdicts to the API.
- Images published as `v4_4`, including the first review service and review interface images.

## v4_3 -- 2026-09-24 -- *unpublished*

**Added**
- `docker/init_db.sh` is tested.
- Inventory tests for every Dockerfile and compose file.

**Updated**
- Removed an unused constant from the API.

**Fixed**
- `docker/init_db.sh` had never been run by any test.

## v4_2_1 -- 2026-09-23 -- *unpublished*

**Added**
- `start.sh`: one command that starts everything and opens the interface.
- A line-coverage measurement for the shell scripts.

**Fixed**
- Sixty `launch.sh` tests were left out of the default run because they were marked as needing Docker.

## v4_2 (4.2.0) -- 2026-09-23

**Added**
- The web interface (React and TypeScript): charts, session history and verdicts.
- Tests for the RAG images and scripts.

**Updated**
- Images published as `v4_2`, including the first web interface image.

**Fixed**
- Two questions in flight at once could have the first one's answer overwrite the second's.
- `publish_db_image.sh` gave a misleading error when the image reference was forgotten.
- `run_update.sh --help` printed its own shell source.
- The store starters accepted an undocumented `-i`.

## v4_1 (4.1.0) -- 2026-09-21

**Added**
- The REST API over HTTPS: job-based questions, a live progress stream, and a self-signed development certificate.
- An outside-client smoke test, run in a container of its own.
- Coverage measurement of scripts run in their own process.

**Updated**
- Agent image published as `v4_1`.

**Fixed**
- The smoke script crashed on macOS's bash 3.2.
- The smoke script reported an unauthorised client as an API that was not there.
- The smoke script read an error body as a healthy server with no tables.

## v4 (4.0.0) -- 2026-09-20

**Added**
- The multi-agent pipeline, in four stages:
  - screening by a Supervisor;
  - four retrievers run in parallel;
  - checks that need no model, then execution, with one Repair Agent;
  - a chart, a narrative in which every claim cites its cells, and an audit of those claims.
- Architecture specs arch1 to arch4, and the v4 diagram.

**Updated**
- Table selection and validation no longer call the model, which cut benchmark time by a third.
- Agent image published as `v4`.

## v3_1 -- 2026-09-18 -- *unpublished*

**Added**
- The agent reads as `nl2sql_reader`, a role that can only `SELECT`.
- Live tests of what that role can and cannot do.

**Updated**
- `setup.sh` and `launch.sh` create the role on every start.

## v3 (3.0.0) -- 2026-09-18

**Added**
- Worked examples: 45 verified question/SQL pairs, found by combining three kinds of search and reranked, then shown to the model as example conversations.
- The RAG pipeline that builds both stores, and the scripts that publish them.
- A 15-question benchmark.
- `launch.sh`.
- The v3 diagram.
- Dataset images `rag-vectordb:v3` and `rag-chunkdb:v3`, adding the worked examples.

**Updated**
- Agent image published as `v3`.

## v2_1 -- 2026-09-16 -- *unpublished*

**Added**
- Architecture diagrams for v1 and v2, and the script that generates them.
- The 45 golden questions (`context_questions/`).

## v2 (2.0.0) -- 2026-09-16

**Added**
- Retrieval: each question is matched against a knowledge base of the data dictionary, DDL index and business index.
- Tests that check the documentation against the code.
- Dataset images `rag-vectordb:v1` and `rag-chunkdb:v1`.

**Updated**
- `setup.sh` also pulls and starts the knowledge base.
- Agent images published as `v1` and `v2`.

## v1 -- 2026-09-15

**Added**
- A generator for a synthetic grocery-retail dataset.
- A Postgres image with that dataset loaded in (`retail-postgres:v1`).
- The first agent: schema-only, run from the command line against a local Ollama model.
- `setup.sh`.
- The first test suite.
