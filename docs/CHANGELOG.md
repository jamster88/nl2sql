# Changelog

Every version from the first, newest first, with the major artifacts each one
created, updated or fixed, and the image tags it published.
[`CHANGELOG_SIMPLE.md`](CHANGELOG_SIMPLE.md) is the same history as one line
per change.

**Versions.** The number is the agent's: `__version__` 5.1.2 is published as
the tag `v5_1_2`, and a tag with fewer components names a line
(`v5_1` is 5.1.x). The app images -- `nl2sql-agent`, `nl2sql-gui`,
`nl2sql-review`, `nl2sql-review-gui`, `nl2sql-console-gui`, the five
`nl2sql-desktop-build` platforms, since v5_5 `nl2sql-mlflow` and
`nl2sql-mlflowdb`, since v5_6 `nl2sql-curate-gui`, since v6_0
`nl2sql-ldap`, `nl2sql-auth`, `nl2sql-directory-gui` and
`nl2sql-mlflow-proxy`, and since v6_3 `nl2sql-proxy` in place of the six
pages' -- are released
together at one number, which
[`tests/docs/test_versions.py`](../tests/docs/test_versions.py) holds every
declaration in the repository to. The dataset images --
`nl2sql-retail-postgres`, `nl2sql-rag-vectordb`, `nl2sql-rag-chunkdb` --
version independently, because their content changes independently of the
code; each is listed under the release it shipped with. A version marked
*unpublished* is a checkpoint in the repository that published no tag.

**Tags never move.** A published tag is never re-pushed: a correction to
something already published is a new patch version and a new tag. Every
versioned tag below is still on Docker Hub as it was first published, with one
exception from before the rule existed (the `v4_5` desktop tags, see v4_5_1);
only the `latest` tags move, which is what they are for. Publish dates are
Docker Hub's, in UTC; release dates are the repository's.

**Artifacts.** Paths are relative to the repository root. Tests are named by
file where a file is new; the tests a version merely extended are summarised.

---

## v6_3 (6.3.0) -- 2026-10-05

Phase 5 of the second adversarial review's mitigation plan
(`adversary_reviews/v6_1_review_mitigation_plan.md`): container hardening and
topology, on the non-root images 6.2 made. Before it, every container could
write its own filesystem and held Docker's default capabilities with no
ceiling on what it used (M-03); every password and token was an environment
variable, which `docker inspect` shows anyone who can run it (M-06, S-10);
six nginx images carried copies of one start-up script, and twelve health
checks skipped verifying the certificate they were answered with (I-14,
M-07, S-15); base images were tags that could move under a build (M-02,
M-12, S-12); the service roles had no ceiling on what a session could cost
(S-11); the four runtime stores were four Postgres servers, each owner its
server's superuser (I-12, D-08); and the scripts prepared every database
with SQL of their own (I-15, C-06). The plan item each change closes is named
beside it; V6-40 and V6-41 are begun, as the plan's phase says, not finished.

### Added
- The `x-hardened` block in `docker-compose.yml` (V6-34), which every service starts from: `read_only`, `cap_drop: [ALL]`, `no-new-privileges` and a `/tmp` in memory. Each service has a `mem_limit` and a `pids_limit`; the ones that start as root to hand a volume over and drop -- the five Postgres servers, the directory, the auth service and MLflow -- get back only `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID` and `SETUID` (`x-postgres-caps`), `pki` only the first three, the review service and the desktop image only the last two, everything else none. The directory has a second in-memory directory for `slapd`'s socket. `tests/security/test_posture.py` holds every service to the block, and to giving back nothing beyond those five.
- Compose `secrets:` (V6-38): nineteen files in `secrets/` -- the directory `0700`, ignored by git and by every build context -- mounted at `/run/secrets/<name>` in only the services that read them, and no password or token in any service's environment. `nl2sql_common.env.secret(NAME)` reads `NAME` or the file `NAME_FILE` names; `env_url(NAME)` reads a URL with no password in it and puts in the one from `<NAME minus _URL>_PASSWORD_FILE` (`nl2sql_common.urls.with_password`). Every settings module reads through them -- the agent's, the API's, the console's, the review service's, the auth service's, the directory's and the RAG loaders'. `setup.sh` writes the fifteen that must exist and leaves the service tokens and a replica's bind password empty until asked for; it and `launch.sh` move every password an older `.env` holds into its file, the same value, and take the line out of `.env` (`RETIRED_KEYS`).
- `proxy/` -- one image, `nl2sql-proxy`, for every page and MLflow's front door (V6-37), replacing `nl2sql-gui`, `nl2sql-review-gui`, `nl2sql-curate-gui`, `nl2sql-console-gui`, `nl2sql-directory-gui` and `nl2sql-mlflow-proxy`. Five build stages, one per page, then nginx as account 101 with the five bundles side by side; `NL2SQL_PAGE` says which one a container serves, from that page's own root. One start-up script (`10-nl2sql-proxy.envsh`), one sign-in location, one template per kind of page (`gui`, `service`, `directory`, `mlflow`), all rendered into `/tmp/nginx` on a read-only root. `proxy/README.md`; `tests/proxy/test_proxy_startup.py`.
- Health checks that verify (V6-37): `proxy/health.sh` asks a page's own listener as `localhost` -- a name every page's certificate covers -- against the stack's CA, and `python -m nl2sql_common.health API|REVIEW|CONSOLE|AUTH` does the same for the four Python services (`common/nl2sql_common/health.py`). The twelve that used `wget --no-check-certificate` or an unverified context are gone; `tests/security/test_posture.py` holds that none comes back. `tests/common/test_health.py`.
- `tools/pin_images.py` (V6-35): every `FROM`, every `ARG *_IMAGE` default and every stock image compose runs is pinned by index digest as well as tag, and Alpine is one tag, 3.22 (`docker/apitest` and `desktop` were 3.21). The tool checks that nothing is unpinned and, with `--update`, asks the registry for each tag's digest and writes it in. `tests/security/test_pinned_images.py`, the tool at 100%.
- `common/nl2sql_common/roles.py` -- role-level limits (V6-39): `RoleLimits` and `limit_role`, which set a role's `statement_timeout`, `work_mem`, `idle_in_transaction_session_timeout` and connection limit. The agent's reader (2 min, 16 MB, 1 min idle, 60 connections), the snippet store's reader (30 s, 30), the API's feedback writer (10 s, 4 MB, 20), the role sync (1 min, 4 MB, 10), each runtime store's owner (16 MB, 20) and every person the auth service makes (`AUTH_USER_STATEMENT_TIMEOUT_MS`, 16 MB, 1 min idle, `AUTH_USER_CONNECTION_LIMIT`), each applied where the role is made and checked again on every start. `tests/common/test_roles.py`.
- The runtime stores in one server (V6-40, begun): a new `stores` service, stock `pgvector/pgvector:pg18` pinned by digest, on the `storesdata` volume and port 5435 (`STORES_DB_PORT`), holding the feedback, corrections, completions and snippet databases, each owned by its own role, none of them a superuser; the two that make roles of their own (feedback's writer, the snippets' reader) have `CREATEROLE`, and pgvector is made in the three that need it by the superuser, which only the socket reaches. The four old names are aliases on its network, so a URL written for 6.2 still reaches its database; every default URL names `nl2sql-stores`. The two RAG stores stay their own images, as the plan's V6-03 decides.
- `docker/migrate_store.sh` and the `storesmigrate` service: a store from before 6.3, moved into its database. `launch.sh` runs it once for each old volume it finds -- `<instance>_feedbackdata`, `_correctionsdata`, `_completionsdata` -- with the volume mounted read-only: a Postgres of its own on a copy in `/tmp`, `pg_dump`, and `pg_restore` into a database that has no tables yet as its owner, without the row-level policies and extensions the review service and dbprep make again; the database is marked as moved, so it is never moved twice, and one already in use is refused rather than merged. The old volumes are kept, and `launch.sh` prints the command that removes them. `tests/docker/test_migrate_store.py`, against fakes and against a real store made the way 6.2 made one.
- `common/nl2sql_ops/` and the `dbprep` service (V6-41, begun): `python -m nl2sql_ops prepare`, in the agent's image as its own account (10001), run by compose before anything that connects to a database. It holds each database's socket, a volume shared with nobody else (`pgsocket`, `storessocket`, `contextsocket`, `vectorsocket`, `mlflowsocket`), and over it -- the superuser over the socket -- makes the retail database's extensions, the agent's reader and the sign-in schema and role sync (what `docker/reader_role.sql`'s and `docker/auth_roles.sql`'s statements did, from Python), rewrites the sign-in block of `pg_hba.conf` through the server itself (`pg_read_file`, a large object exported over the file, `pg_hba_file_rules` checked before `pg_reload_conf`), makes the runtime stores' databases and owners, and sets every login's password from its file. `report` and `snippets` say what each database holds and whether the snippets are behind their document, for the scripts, as `STEP`/`INFO`/`WARN`/`STATE` lines. Neither `setup.sh` nor `launch.sh` runs SQL any more. The plan put this in the auth service; it is a one-shot instead, so the service that faces the network holds no superuser socket. `tests/ops/`, five of them live.
- `tests/settings_names.py` -- the names a settings module reads, as compose may set them: a secret by its file, a URL beside its password file, and the proxy image's settings per page.
- `tests/acceptance/test_stack.py` -- `test_nothing_runs_as_root_writes_its_image_or_shows_a_secret`: in the running stack, no process in any container is root, no container can write its own image, and no secret is in any container's environment.

### Removed
- `gui/Dockerfile`, `review/gui/Dockerfile`, `curate/Dockerfile`, `console/Dockerfile`, `auth/gui/Dockerfile` and `docker/mlflow-proxy/`, with their nginx templates and start-up fragments -- the proxy image's now. The pages' sources stay where they were.
- `docker/auth_roles.sql` and `docker/ldap_hba.sh` -- `nl2sql_ops` does both; `docker/reader_role.sql` stays, for the image build.
- The `feedbackdb`, `correctionsdb`, `completionsdb` and `snippetsdb` services, their ports 5436 to 5438, and the `feedback` profile.
- `setup.sh`'s twelve per-page image flags -- `--gui-image`, `--review-gui-tag` and the rest -- for `--proxy-image` and `--proxy-tag`.

### Fixed
- The retail database's health check asked over the socket, which the entrypoint's own temporary server answers while it sets the passwords -- so the container could report healthy before Postgres had really started. It asks over TCP now, which that server does not listen on.
- `tests/agent/test_database_live.py` -- one test connected without the module's reachability check, and failed rather than skipped when no database was up.
- `setup.sh`, `launch.sh` -- an upgrade from 6.2 left the four stores' own containers running, the feedback store's on the port the runtime stores publish, so `stores` could not start; found by rehearsing the upgrade before publishing (below). They are stopped, with time to shut down cleanly, and removed first; their volumes are kept.

### Updated
- `docker/mlflow/entrypoint.sh` -- builds the tracking store's URL from `MLFLOW_DB_*` and the password file and hands it to MLflow as `MLFLOW_BACKEND_STORE_URI`; the URL with its password is no longer on the server's command line, where `ps` and `docker inspect` showed it.
- `docker/apitest/smoke.sh` -- reads the token from `API_TOKEN_FILE` when `API_TOKEN` is unset, and never prints it.
- `docker-compose.yml` -- the blocks and anchors above; `dbprep` before every service that connects to a database; the URLs without passwords, beside their files; the six pages as the proxy image; the Python services' health checks.
- `setup.sh` -- `secrets/`; the stores' own containers from before stopped and removed, as `launch.sh` does; the databases prepared by `dbprep` and its report relayed; `--proxy-image`, `--proxy-tag`, and the proxy pinned whenever a page will be served -- the directory's, with sign-in on; the snippets loaded as the review image's account with no URL on its command line; `v6_3`, twelve tags.
- `launch.sh` -- `secrets/`; the four stores started as one; the stores' own containers from before stopped cleanly and removed, their volumes kept -- the feedback store's held the port the runtime stores publish -- and what they held moved; `dbprep` run on every start and its report shown as the contents table; the CA copied out again when it has changed, with a warning to trust it again; `--feedback` the same as `--api`, since a verdict is staged whenever the API is up.
- `start.sh` -- re-pins `.env` when the pages' image is missing; help and messages for `secrets/`.
- `tests/live_stores.py` -- the live tests find each password in `secrets/`, or an older `.env`, and the four runtime stores on one port.
- The compose tests (`tests/docker/test_compose_config.py` and the API's, GUI's, review's, curation's, console's, auth's and MLflow's) hold the 6.3 shape; `tests/docker/test_gui_container.py` and `tests/console/test_console_container.py` run the proxy image as compose does -- read-only, with no capabilities, its token a mounted file -- and check its health check fails against a CA that did not issue its certificate; `tests/auth/test_auth_live.py` prepares its database with `nl2sql_ops`.
- Version 6.3.0 in every declaration, `proxy/Dockerfile` among them (thirty places); `setup.sh` pins `v6_3`.

### Documentation
- `README.md` -- the containers, the twelve tags and how to publish them, digests, upgrading to 6.3, the runtime stores' settings, and a new section, *What each container may use*: the limits, the secrets, the sockets and the role ceilings. `USAGE_GUIDE.md`, `QUICKSTART.md`, `SECURITY.md` (as of 6.3.0), `agent/API.md`, `common/README.md`, `review/README.md`, `console/README.md`, `curate/README.md`, `gui/README.md`, `auth/README.md`, `ldap/README.md`, `rag/README.md`; `proxy/README.md`, new; `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6_3.md`, what 6.3 changed in arch6.

### Verified
Before publishing, against images built from this checkout, beside the 6.2 stack this machine runs and without touching it.
- The acceptance tier (`--run-acceptance`): 10 passed, none skipped, from a clean start -- every image built from the checkout, the stack started with the user's two commands beside the running one, the administrator signed in on every page, a question answered and its verdict promoted, SQL through the console, the trace through MLflow's front door, the desktop client signing in -- and the new one: of the seventeen containers running, none has a process running as root, every one is read-only, has dropped every capability, cannot gain a privilege and has a memory and process ceiling, and no secret is in any container's environment, command or arguments, the two one-shots' included.
- The live-store tests against that stack, kept after its run: 471 passed and the RAG tier's 337, the tests that make a scratch database against a throwaway pgvector, as a superuser -- since 6.3 no store's owner may make one. One count differed, the golden set the acceptance run had just grown by a pair; one teardown restored a writer's password on a server that had none, and now restores it only where a staging table is.
- The docker tier: 372 passed, then 39 after the four failures it found were fixed -- the proxy image shipping nginx's own welcome page, the API's health-check test still expecting a shell, a live test that failed rather than skipped with no database, and the migration's collation warning, which `PGOPTIONS` came too late to quiet. The migration against a real store made the way 6.2 made one: its rows moved, owned by the store's owner, its policies left for the review service, marked, and a second run moving nothing.
- Python coverage at 100% of 16,092 statements and 3,776 branches across all four tiers; the shell scripts at 100% of 1,734 commands; every page's suite (`--run-node`, 50) and the desktop client's (`--run-java`, 6), each at 100%; the offline suite, 4,829.
- Not run here: `tests/docker/test_compose_rag_integration.py`, which runs `docker compose up` in this checkout's own project -- the running 6.2 stack's, which 6.3's compose file would have recreated; and the published-tag checks, which wait for the push.

### Published
- All twelve tags as `v6_3` -- `nl2sql-agent`, `nl2sql-proxy` (its first), `nl2sql-review`, `nl2sql-mlflow`, `nl2sql-mlflowdb`, `nl2sql-ldap`, `nl2sql-auth` (amd64, arm64) and `nl2sql-desktop-build:v6_3-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-06 UTC), each checked absent just before its push, all first time. The six page images 6.3 replaces keep their `6.2` tags; the dataset images are unchanged. Checked after: every image labelled 6.3.0 on both architectures, the agent and proxy as their own accounts (10001, 101); the agent carries `nl2sql_ops` and `nl2sql-common` 6.3.0, the proxy the five pages and `curl` and no nginx welcome page, the review image the role ceilings, on both; every desktop jar 6.3.0 with its own platform's native code; `tests/docker/test_published_images.py`, 18 passed.
- Before the user's stack, the upgrade rehearsed on a stack of its own: the 6.2 commit started from its published images with the runtime stores' port the same as its feedback store's, rows put in its feedback and corrections stores, then this checkout laid over it and `./start.sh --review --curate --console --mlflow --no-browser` run. It found that nothing stopped the 6.2 stores' own containers -- the feedback store's held the port the runtime stores publish, and each had open the data the move copies -- which `setup.sh` and `launch.sh` now stop cleanly and remove, keeping their volumes (`tests/docker/test_setup_script.py`, `test_launch_script.py`). Rehearsed again: the four retired, every row moved and owned by its store's owner, `.env` left with no password, the 19 secrets in `secrets/`, and 98 checks passed.
- Then the same command upgraded the user's stack: it re-pinned `.env` to `v6_3`, pulled the twelve, moved the passwords into `secrets/`, retired the four old stores' containers, moved the three stores -- every table's rows the same after as before -- and brought every page up with no warning but the usual one about the kept database volume. 98 checks passed against it: every container healthy on 6.3 images and `pki` and `dbprep` exited cleanly; the administrator signed in on every page with the password that moved; each page reaching its service at 6.3.0; every store on this machine only; a signed-out session refused by three services; no process in any of the seventeen running containers running as root, every one read-only, capability-less and capped, and no secret in any container's configuration; the review service listing every staged submission; and a question answered.

### After publishing
Not in the images: the scripts, the tests and the documents, from the coverage and relevance audit asked for once the stack ran 6.3 (2026-10-06), and the upgrade fix above.
- **Coverage, held as the README states it.** Three `# pragma: no cover` had joined the one the README names: on `nl2sql_common.health`'s and `nl2sql_ops`'s module entries -- which tests already ran as `__main__`, so the exclusions only hid covered lines -- and on `tools/pin_images.py`'s, now run as a script against this checkout (`tests/security/test_pinned_images.py`). Dead imports out of three settings modules and seven test files.
- **Both directions, for what 6.3 added.** `tests/ops/test_ops_compose.py`: dbprep's compose settings against what `nl2sql_ops` reads -- every one it is given read, every one it reads settable but the six the stack fixes, named with the reason -- each socket mounted where its settings look, the documents read-only where the snippet check reads them. MLflow's server's environment against its entrypoint; what `launch.sh` hands `storesmigrate` against what the migration requires.
- **Settings nothing exercised or documented.** A compose-variable scan found the review and directory pages' settings, the outside client's (`APITEST_*`), `AUTH_TLS_HOSTNAMES` and three of the review service's URL overrides in neither a document nor a test. Each page's settings are now checked by the name `.env` gives them, all six pages in one test (`test_each_page_setting_reaches_the_proxy_by_the_name_compose_documents`, which replaces the curation page's own); every identity's `*_TLS_HOSTNAMES`; every `REVIEW_*_DB_URL` override; and documents hold each page's, the client's, and -- with no row anywhere until then -- dbprep's thirty-five settings (`common/README.md`), the directory's, the auth service's and the RAG loaders' (`EMBED_DIM`, `EMBED_BATCH_SIZE`).
- **Duplicates removed.** Every page's project tests checked the template it was served from; since 6.3 the review, curation and console pages share one, so the same five assertions were made four times. What the templates share is now `tests/proxy/test_proxy_templates.py`, once; each page keeps what is its own. Also gone: three offline timeout checks the compose tests make on resolved compose, a Dockerfile test wholly inside another, the curation page's URL-override test (one case of the new one), an unused helper and two unused fixtures, a fake `docker` branch no script reaches, and a diagram dispatch whose branches for older trees can never run in this one.
- **Tests that tested nothing any more.** Two asserted that `feedbackdb` was not started, which 6.3 made true of every run; they assert what still applies. Two docstrings described `--feedback` as 6.2 had it.
- **Failure modes no test set.** A fake switch for MLflow's front door that stopped, and one for an old store's server that will not start, read by the fakes and set by no test; each has a test now.
- **A wrong comment, and a test for the right claim.** Compose said the web interface's read timeout (600 s) outlasted `API_MAX_WAIT_SECONDS` (900 s); it outlasts what the page uses -- its progress stream, closed after `API_EVENT_STREAM_TIMEOUT_SECONDS` -- which `tests/docker/test_gui_compose.py` now checks, and the comment and `gui/README.md` say.
- **A test that could not run beside a stack.** `tests/docker/test_compose_rag_integration.py` ran `docker compose up` in the checkout's own project -- the running stack's -- which with 6.3 would have recreated its stores and re-run dbprep over its databases. It is a compose project of its own now, every port free, removed afterwards.
- **A host requirement, said.** 6.3's proxy start-up runs `envsubst` itself, so its tests need gettext's on the host; a test that reaches the templates skips without it, saying so, and the README and `proxy/README.md` name it.
- Verified after: every tier under coverage that measured the tests too -- the acceptance tier 10 passed, its stack kept for the live-store tests (677 passed; the one count that differed was the pair its own run had promoted, and passes against the published stores), the RAG tier 80, every page's suite 50, the desktop client's 6, the offline suite 4,839 -- with 100% of 16,096 statements and 3,782 branches; the shell scripts at 100% of 1,734 commands; the fake binaries traced line by line, every line run but a `grep` that finds no real one. What the tests' own report left unrun is skip and failure paths, guards that assert nothing calls them, and the shell-trace mode.
- **The documentation reorganised** (2026-10-07). `README.md` had grown to 2,400 lines; it is a front page now -- what this is, the quick start, what is new in the release, and an index -- and what it held is a document a topic in `docs/`: `stack.md` (the scripts, the containers, every flag), `agent.md`, `images.md` (the tags, pulling, upgrading, publishing), `sign_in.md`, `hardening.md` (what each container may use), `web_interface.md`, `desktop_client.md`, `rest_api.md` (connecting a GUI), `sql_console.md`, `feedback.md`, `snippets.md`, `tracing.md`, `benchmark.md`, `model_catalog.md`, `dataset.md` (the generator and the Postgres container), `architecture_diagrams.md` and `tests.md`. `USAGE_GUIDE.md`, `QUICKSTART.md`, `SECURITY.md`, both changelogs and `basic_agent_steps.md` moved into `docs/` beside them, every section heading kept so a link into one still lands. Every link was re-based, in the pieces and in the documents that pointed at the README (`auth/`, `curate/`, `data_gen/` and `rag/`'s READMEs, a compose comment). `tests/docs/test_docs.py` reads each fact from the document that holds it now -- the counts and the coverage command from `tests.md`, the tag tables from `images.md`, MLflow's settings from `tracing.md`, the stores' from `snippets.md`, the flags from `stack.md` -- checks that every section link in every document lands on a heading, and that the README's index lists every document in `docs/`; `test_versions.py` and `test_script_coverage.py` likewise -- and the six interface and desktop suite tests behind `--run-node` and `--run-java` read their counts from `tests.md` too, which only the measured full run found, since nothing offline runs them; the directory page's count in `sign_in.md` is read by the same test now.
- **The embedding-host count, checked with the host down.** "Twenty-eight of those 656 also need the embedding host" was the one count in the tests document nothing read: twenty-eight is still right -- run with this machine's Ollama stopped, and again with `TEST_EMBED_BASE_URL` at a dead port, 28 of the 31 live tests in the three files that read it skip -- but 656 was the docker count two releases ago, and is 758 now. Three of the twenty-eight, the DDL-vector tests in `tests/agent/test_schema_retrieval.py`, skipped saying the vector store was unreachable when it was answering and the embedder was not: one `search` had done both, so the fixture now asks the store first and the embedder second, naming each. `tests/docs/test_docs.py` holds both numbers from here on -- the N to the docker count, the word to the docker tests that take the fixture building an embedder in each file that reads `TEST_EMBED_BASE_URL`.
- **The second review's plan, verified item by item** (2026-10-07, at 6.3.0). Of `v6_1_review_mitigation_plan.md`'s 71 items, 65 are done and cited by the release that did them, in the code and in the tests that hold them -- 6.1 Phases 1 and 2 with the documents, 6.2 Phases 3 and 4, 6.3 Phase 5 -- and V6-09 was superseded. Partial, as `Multi-Agent_NL2SQL_arch6_3.md` records: V6-40 (the runtime stores are one server; the RAG stores stay their own, by decision) and V6-41 (the databases are prepared by `dbprep`; the scripts still orchestrate in shell). Open: V6-42, provenance in publishing -- the images carry version labels and no signature or attestation, which `SECURITY.md` lists among the known limits -- and V6-47, one home per fact: the sign-in facts are stated in thirteen documents. Found still wrong, and fixed: the comment both cycles flagged, `gui/src/api/useAsk.ts` saying "fourteen pipeline nodes" (V6-48; the pipeline has nineteen). Added: V6-50's narrative-drift test -- the four phrases the reviews retired are absent from every tracked document and source file outside the reviews, the specs and the changelogs that quote them as history -- and the newest specification's blueprint held to the tree (V6-44): every file and test its rows name exists; the conformance table in the plan's full sense, spec section to module to test, is not written, since a spec is never edited in place and only the newest one's rows describe this tree. V6-65 -- nothing but the API and the one-shot that issued it holds the API's identity -- was true since 6.1's `pki` and is now held by `tests/security/test_posture.py`.

## v6_2 (6.2.0) -- 2026-10-04

Phases 3 and 4 of the second adversarial review's mitigation plan
(`adversary_reviews/v6_1_review_mitigation_plan.md`): the shared foundations,
and the service refactors that consume them. Before it, a session could not
be ended (S-18), a service token was an identity with every role and the
name a header claimed (S-19), every application image but the directory's
ran as root (M-01), every route was guarded by a dependency added by hand
(C-03), the review service ran its loaders as scripts with the stores'
passwords on their command line (I-10, S-21), and five pages each carried a
copy of the same sign-in. The plan item each change closes is named beside it.

### Added
- `common/` -- the shared package, installed as one (V6-20, V6-66): `nl2sql_identity`, moved here from `auth/`, beside `nl2sql_common` -- settings from the environment, the error envelope, the error taxonomy, embeddings, dropping root, a person's name for Postgres -- with a `pyproject.toml` at the release's version, which every image installs with pip and records. `common/README.md`, `tests/common/`.
- Session revocation (V6-61, V6-71's option (a)). `docker/auth_roles.sql` makes schema `nl2sql_auth`, owned by a `NOLOGIN` role, with two lists -- a session's `jti` signed out, and a person's cut-off -- that the role sync's login may write and nobody may read, and `nl2sql_auth.session_revoked(user, jti, issued_at)`, a `SECURITY DEFINER` function the reader and the sync may call, which answers yes or no about one session. The auth service writes the lists (`auth/nl2sql_auth/revocation.py`): `POST /auth/logout` ends the session presented, bearer or cookie; `POST /auth/password` and an administrator's password set cut off every session from before -- the browser that changed it gets a new one, signed at the cut-off; a removal cuts off too; and the role sync records the directory's own lock time for a person the password policy locked (`Person.locked_since`). Rows are swept once nothing they refuse could still be presented. Every service's guard asks in its once-a-minute recheck, now per session rather than per person, and answers `401 session_revoked`; the auth service forgets its answers at once. `revocation_unavailable` (503) when the lists cannot be written. `tests/auth/test_auth_revocation.py`, and four live tests in `tests/auth/test_auth_live.py`.
- `auth/nl2sql_auth/proxies.py` -- `AUTH_TRUSTED_PROXIES` (V6-63): the addresses, networks or names whose `X-Forwarded-For` counts as where a sign-in came from; names are looked up every half minute. Anyone else is counted by the address it connected from. Compose names the six page proxies. `tests/auth/test_auth_proxies.py`.
- Named service tokens (V6-62): `API_TOKEN_NAME`/`_ROLES`, `REVIEW_TOKEN_NAME`/`_ROLES`, `CONSOLE_TOKEN_NAME`/`_ROLES`. A token is a caller named `token:<name>` -- what it does is recorded so, never as the `X-Reviewer` it sends, which only an open service with no token records -- holding the roles it is given: `nl2sql_users` for the API's, the console's allowed roles for the console's, and for the review service's the reviewer roles with sign-in on (curating by token is granted by name) and both with it off.
- `POST /v1/admin/reload` -- for `nl2sql_admins` (V6-33): the agent reads again what it read once -- the literal catalog, the label map and calendar, the foreign keys, the knowledge collections (`Nl2SqlAgent.reload`) -- and the guard asks Postgres about every session again. A promotion needs none: the pairs, snippets and fixes it writes are read from their stores on every question. `Reloaded` is mirrored in the web interface's types and the desktop's records.
- `API_DEBUG_DETAIL` and `--debug-detail` (V6-32): every caller sees a failure in its own words, for a development server.
- `rag/ragproc/loaders.py` -- steps 5, 6 and 7 as functions (`load_golden_pairs`, `embed_golden_pairs`, `load_snippets`), returning reports; the three scripts are command lines over them (V6-27). `tests/rag/test_loaders.py`.
- `web/` -- the shared web package (V6-25): the sign-in gate and session client, `ApiError`, `plainText` and `counted`, the dev server's proxies, and how a page's configuration takes it in. Source, with no dependencies of its own; each page resolves `@nl2sql/web` to it and compiles it with its own toolchain, its tests run in every page's suite, and its sources count towards every page's coverage. `web/README.md`, `tests/web/`.
- `nl2sql_common/attribution.py` -- the person's name in the transaction's `application_name` (`nl2sql:agent:alice`, `:console:`, `:review:`), set beside each `SET LOCAL ROLE` (V6-64), so `pg_stat_activity` and the database's log say whom a statement was for.
- Hash-checked locks for every Python image (V6-24): `agent/`, `review/`, `auth/`, `ldap/` and `data_gen/` each have a `requirements.lock`, compiled with hashes from its `requirements.txt` and installed with `--require-hashes`; the directory's Alpine packages pinned to their release. `tests/security/test_supply_chain.py`.
- `tests/security/test_unprivileged.py`, `test_error_taxonomy.py`; `tests/route_table.py`.

### Fixed
- The review service's loads -- the stores' URLs, passwords and all, were on the loaders' command lines, which `ps` shows anyone on the host (S-21); `launch.sh` passed them the same way to its one-off loads. Both pass nothing secret on a command line now.
- Fix ids -- the highest plus one, so deleting the newest fix gave its id to the next, and anything that had quoted it then meant another fix (V6-29). Each store draws them from a sequence, moved past the highest id on every start; a store whose schema someone else manages keeps the old way.
- The desktop client's sign-out forgot its token and told nobody; it ends the session at the auth service too.
- `tests/agent/test_least_privilege_live.py` -- a test that refused when the stack was down rather than skipping; and the docs tests' route walks, which passed with nothing in them once a router held the routes.

### Updated
- Every service's routes are on routers, each carrying its guard (V6-26): `agent/nl2sql_agent/api/routes.py`, `console/routes.py`, `review/nl2sql_review/routes.py`, `auth/nl2sql_auth/routes.py`. The routes open by design are on one router of their own; every other route is refused to anyone the router's guard refuses before anyone thinks to guard it. What a route can reach is an explicit context rather than `create_app`'s locals. `tests/security/test_routes_guarded.py` holds that no route is added to an application directly and that every router but the open one carries a guard.
- Nothing runs as root but the one-shot `pki` (V6-31, V6-28). The agent image -- the API, the console, the CLI -- runs as `nl2sql` (10001); the six page proxies as nginx's own account (101), with the `user` directive gone and what nginx writes at start made theirs; the smoke test as `nobody`. The auth service starts as root only long enough to give `nl2sql` its two key directories and what an older release wrote there (`privileges.become(..., recursive=True)`); MLflow gives its artifact volume to `mlflow` (10002) and drops to it with `setpriv` (`docker/mlflow/entrypoint.sh`); the desktop image copies its jar out as the owner of where it lands (`desktop/copy-out.sh`). The review service becomes the owner of the mounted `context_questions/` -- the person who cloned the checkout -- keeping `nl2sql`'s group to read its key, and writes as `nl2sql` when the directory is root's. The pki service runs as root in compose and hands each key to its service's account, 0640, on every run, so a 6.1 volume's root-owned key is moved over on the first start (`NAME=DIR=HOSTS=UID:GID`).
- The review service writes through a library with a lock (V6-27, V6-04): a promotion, a withdrawal and a snippet write call `ragproc.loaders` in its own process, with each store an argument, holding a lock in the process and an advisory lock on the documents' directory between processes, from reading the document to the last load. `REVIEW_RELOAD_TIMEOUT_SECONDS` is how long one request to the embedding host may take. A load that breaks is reported beside the written pair, not raised.
- Operator-only detail (V6-32): `/readyz` gives each dependency's state to anyone and the reason only to an administrator, in all four services; an answer's `retrieval_errors` and `node_errors` say `unavailable`, `disabled` or `failed` to anyone else; a crashed job's `error` names the exception's type, with its words kept for an administrator; and the progress stream never carries a driver's words.
- Engine and `SET` hygiene (V6-22); the error taxonomy (V6-23) -- 79 `except Exception` in the agent and the review service catch the family they mean, and the boundaries left say why on the line.
- `docker-compose.yml` -- `pki` as root, each identity's owner; `AUTH_TRUSTED_PROXIES`; the token names and roles; `API_DEBUG_DETAIL`.
- `launch.sh` -- its one-off loads run as `10001:10001`, with the stores' URLs from their environment.
- Version 6.2.0 in every declaration, `web/package.json` among them (thirty-five places); `setup.sh` pins `v6_2`.

### Documentation
- `agent/API.md`, `auth/README.md`, `review/README.md`, `console/README.md`, `USAGE_GUIDE.md`, `SECURITY.md` (as of 6.2.0) -- revocation and `session_revoked`, the named tokens and their settings, the reload route, who sees a failure's words, the trusted proxies, running unprivileged, the in-process loads and the lock, the fix-id sequence, `application_name`; `common/README.md` and `web/README.md`; `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6_2.md`, what 6.2 changed in arch6.

### Verified
Before publishing, against images built from this checkout.
- The acceptance tier (`--run-acceptance`): 9 passed, none skipped, from a clean start -- every image built from the checkout, the stack started with the user's two commands beside nothing else, the administrator signed in on every page, a question answered through the web interface, its verdict promoted into the golden set through the review service's in-process loads, SQL through the console, the question's trace through MLflow's front door, and the desktop client signing in.
- Each changed image built and started as its account: the agent as `nl2sql` (10001), the six pages as nginx (101), the smoke test as `nobody`, MLflow as `mlflow` (10002); the auth service dropping to `nl2sql` with its key directories handed over and its keys written as the account's; the review service dropping to the owner of a mounted `context_questions/`, which it could write; the desktop image copying its jar out.
- The sign-in tier against a real retail database and directory (`tests/auth/test_auth_live.py`): 14 passed, the four new ones among them -- the reader may ask about one session and read neither list, a session signed out is refused by the lookup every service runs and only that session, a password an administrator set ends that person's sessions while the sign-in straight after is kept, and the directory's lockout ends the sessions from before it.
- The fix stores against a throwaway pgvector: 37 passed, the sequence among them.
- Every page's suite (`--run-node`, 50) and the desktop client's (`--run-java`, 411 tests), each at 100%; the shell scripts at 100% of 1,990 commands; the offline suite, 4,859.
- Not run here: the live tests that read the running stack's own stores (`tests/live_stores.py`), which need a stack `launch.sh` started -- it sets the stores' passwords -- and the published-tag checks, which wait for the push (they pass since, below).

### Published
- All seventeen tags as `v6_2` -- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-curate-gui`, `nl2sql-console-gui`, `nl2sql-mlflow`, `nl2sql-mlflowdb`, `nl2sql-mlflow-proxy`, `nl2sql-ldap`, `nl2sql-auth`, `nl2sql-directory-gui` (amd64, arm64) and `nl2sql-desktop-build:v6_2-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-05 UTC), each checked absent just before its push, all first time. The dataset images are unchanged: `nl2sql-retail-postgres:v1_2`, which needs nothing new -- `launch.sh` applies `docker/auth_roles.sql`, the revocation lists among it, on every start -- and the knowledge-base stores at `v3_2`. Checked after: every image is labelled 6.2.0 on both architectures, with the account each was meant to have (the agent 10001, the six pages 101; the auth, review, directory and MLflow images start as root and drop); the agent, review and auth images report 6.2.0 on both and carry 6.2's code (`nl2sql-common` 6.2.0, the routers, the revocation check, the trusted proxies, `become_owner_of`, `ragproc.loaders`); every desktop jar is 6.2.0 with its own platform's native code, copied out as the owner of where it landed; `tests/docker/test_published_images.py`, 23 passed.
- Then `./start.sh --review --curate --console --mlflow` upgraded a stack: it re-pinned `.env` to `v6_2`, pulled the seventeen and brought every page up with no warning but the usual one about the kept database volume. 68 checks passed against it -- every container healthy on the new images and the `pki` service exited cleanly; the administrator signed in on every page and each page reaching its service at 6.2.0; the console, the directory and its API's port, MLflow's door, every store on this machine only; a session signed out refused by the API, the review service and the console (`401 session_revoked`) while another session of the same person's was still good; the reload, which named the four caches it dropped once a question had filled them; no process in any of the twenty running containers running as root; and a question answered.

## v6_1 (6.1.0) -- 2026-10-04

Phase 1 and Phase 2 of the second adversarial review's mitigation plan
(`adversary_reviews/v6_1_review_mitigation_plan.md`), with V6-69 and the
documents Phase 6 says can be written alongside: safe defaults, transport and
secrets, then the five pipeline fixes. Before it, every database answered on
every interface of the host with the password `nl2sql`, the retail image
carried its superuser's password in its layers, a person's directory password
crossed to that database in clear text (S-16), every service presented the
API's one key, and a service started without `AUTH_ENABLED` was open. Sign-in
and every page work as they did; what changed is what they stand on. The plan
item each change closes is named beside it.

### Added
- `auth/nl2sql_identity/pki.py` -- a development certificate authority (V6-36). The new one-shot `pki` compose service runs it before anything that serves TLS: it makes the CA once (`pkica` volume), gives each server its own EC P-256 key and certificate in its own volume (`apitls`, `reviewtls`, `consoletls`, `authtls`, and one per page and MLflow's front door), with the CA's certificate beside it, and puts the CA where clients read it (`tlstrust`). A certificate is kept while it is valid, from this CA and covers the names asked for, and reissued otherwise; one from the 6.0 self-signed scheme is replaced, one from anybody else left alone. `TLS_EXTRA_HOSTNAMES` adds a name to every certificate. `tests/auth/test_identity_pki.py`.
- `docker/entrypoint.sh` -- the dataset image's entry point (`nl2sql-retail-postgres:v1_2`; V6-07, V6-52, V6-53). It writes a certificate into `/etc/nl2sql/pg-tls` (the `pgtls` volume) on first start, and again for a new `POSTGRES_TLS_HOSTNAMES` or within thirty days of expiry; starts Postgres with `ssl=on`; ends `pg_hba.conf` with `hostnossl all all all reject` and `host all postgres all reject` -- nothing over the network without TLS, and the superuser not over the network at all; and sets the owner's and the reader's passwords from the environment on every start, through a server on the socket only and psql's `\getenv`, so neither reaches a command line, a layer or the log. `POSTGRES_REQUIRE_TLS=false` drops the first rule. `tests/docker/test_retail_entrypoint.py`.
- `SECURITY.md` -- the threat model (V6-45, V6-68): assets, actors, trust boundaries, what each container holds and what each credential is worth and for how long, three deployment tiers (alone; a team on a trusted network, which the defaults are for; beyond one, not yet), the properties promised with the test that holds each, the known limits, and how to report a problem. It retires "one person on one machine".
- `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md`, with `make_arch6_drawio.py`, `Multi-Agent_NL2SQL_arch6.drawio` and `.png` -- the architecture as built (V6-43, V6-44, V6-68), superseding arch5.2: its corrections (the Snippet Retriever in the graph, state lifetimes, `node_errors`, rule 3 of 7.3 withdrawn), the security blueprint verified against a file and a test per row, the departures from 5.2, and new sections on the SQL console, undo, tracing, snippets and curation, the sign-in design, and transport and TLS identities.
- `tests/acceptance/` -- the acceptance tier (V6-67), behind `--run-acceptance`: what a release passes before it is published. A copy of this checkout, started as a stack of its own beside any other on the machine with the two commands a user types -- `./setup.sh --build-all --review --curate --console --mlflow --desktop`, `./start.sh --review --curate --console --mlflow --no-browser` -- then used: the generated administrator signs in on every page, a question goes through the web interface, its verdict through review into the golden set, SQL through the console, MLflow is asked for the question's trace through its front door, and the desktop client's own classes, from the jar `launch.sh --desktop` built, sign in when the server asks. Four of its nine tests are the four defects 6.0 shipped with every other tier green. It refuses to start without about 3.5 GiB of Docker's memory free, skips what needs an answer when the chat model cannot be reached, and removes everything afterwards (`NL2SQL_ACCEPTANCE_KEEP=1` keeps it). Its first runs found the two defects under Fixed that name it.
- `NL2SQL_INSTANCE` -- the stack's name, `nl2sql` by default: compose's project, every container (`<instance>-api`) and the retail volume (`<instance>-pgdata`), with each service's usual `nl2sql-*` name kept as an alias on its own network, the name the others reach it by and its certificate covers. `setup.sh` and `launch.sh` wait on the instance's containers. A second stack, with its ports moved, runs beside the first. `tests/docker/test_compose_config.py`, `test_setup_script.py`, `test_launch_script.py`.
- `setup.sh --build-all` -- every image this checkout has a Dockerfile for, built from it, tagged `local` and pinned, instead of pulled; the knowledge-base stores are still pulled. `tests/docker/test_compose_config.py` hands the profiles it builds with to the real compose, which the fake `docker` cannot judge.
- `tests/live_stores.py` -- where the live tests find the stack's databases. Each test file wrote out a URL with the shared password, which reaches nothing once the passwords are generated, and its test skipped, saying nothing was listening. A URL variable still wins; otherwise the port, login and password are read as compose reads them, from the shell and then `.env`; a server that refuses the login is a failure, and no message prints a password. Fifteen test files use it. `tests/docker/test_live_stores.py`.
- `tests/security/` -- a security tier (V6-60): every `/v1` and `/directory/v1` route in the API, the console, the review service and the auth service carries a `Guard` dependency, read off `app.routes`, with the routes open by design named; every wire model forbids extra fields; and the posture, read from compose, the Dockerfiles and the scripts -- every store on this machine unless `DB_BIND_ADDRESS` says otherwise and MLflow's not published at all; no password in the dataset image, whose start refuses clear text and the superuser over the network; sign-in's rules TLS only and the auth service verifying the database; every TLS server its own identity, the CA's key mounted by `pki` alone and clients given only its certificate; sign-in on by default in every service's code and every page; no password on a `psql` command line.

### Fixed
- `agent/nl2sql_agent/state.py`, `graph.py` -- a repair carried the failed attempt's assumptions, plan cost, result, chart, claims and audit into the next (V6-15). Every field is declared run, attempt or handoff (`LIFETIMES`), and the Repair Agent's update begins with `attempt_reset()`; a test fails if either is undone.
- `graph.py`, `supervisor.py`, `__main__.py` -- a narrator or supervisor that raised was indistinguishable from one that had nothing to say (V6-16). Each writes `node_errors`, which the supervisor reads, the CLI's JSON carries and the API returns; `models/calibrate.py` scores a supervisor that failed from it.
- `present.py` -- the sensitive-column redaction claimed a policy that was never applied where data leaves the database; the claim is removed with it, per the owner's decision (V6-02, V6-17). `AuditReport.redactions` is gone.
- `agent/nl2sql_agent/api/models.py`, `translate.py` -- the trace over REST dropped the model, the rung, the route and the hops the agent records (V6-18); each `TraceEntry` carries them, and the web interface and desktop client show the model with the route as its tooltip. `agent/API.md:290` said they were carried.
- `api/models.py`, `review/nl2sql_review/models.py`, `console/models.py`, `auth/nl2sql_auth/models.py` -- every wire model is on a strict base, `extra="forbid"` (V6-19).
- `docker-compose.yml` -- `API_PORT`, `REVIEW_PORT`, `CONSOLE_PORT` and `AUTH_PORT` move the port a service listens on inside its container as well as the one it publishes, but every page's upstream named the default, so a moved port answered every page with 502 -- though `USAGE_GUIDE.md` says any port can be moved in `.env`. Each upstream's default now follows its setting, the directory page's new one too (`AUTH_DIRECTORY_PORT`); `tests/docker/test_compose_config.py` moves them all and checks no upstream keeps a port of its own, and `tests/docs/test_docs.py` reads a default that names another setting as compose resolves it. Found by the live check below.
- `start.sh` -- `--desktop` gave the window the API's address and not the auth service's, and the client's own default is the API's host on 8446: with `AUTH_PORT` moved it signed in nowhere, or -- as the acceptance tier found, beside another stack -- at the other stack's. It passes `--auth-url` as `.env` sets it.
- `review/nl2sql_review/server.py` -- the review service's banner said `auth NONE` over a service that refused everyone not signed in, and that it presented a certificate the agent API wrote; it says sign-in, and names its own certificate -- what issued it and for which names -- as the console's does. Found by the live check below.
- `tests/benchmarks/test_questions.py` -- the test that failed on every run since 5.4 (B03 is golden pair Q46 word for word, B14 is Q47) states the owner's decision instead: those two overlaps, by name and pair, and any third a failure (V6-59).

### Updated
- `docker-compose.yml` -- the seven stores' ports bound to `${DB_BIND_ADDRESS:-127.0.0.1}` (V6-06); the `pki` service and the TLS volumes, every TLS service waiting for it; the retail database on `v1_2` with the `pgtls` volume, mounted read-only into the auth service; `API_TLS_GENERATE` off, since `pki` issues; clients pointed at `ca.crt`; the directory page's upstream on the directory API's own port; `LDAP_UPSTREAM_ALLOW_CLEARTEXT`; the `REVIEW_TOKEN` comment that overstated what it guards (V6-46).
- `docker/Dockerfile`, `init_db.sh`, `reader_role.sql`, `auth_roles.sql`, `ldap_hba.sh` -- no password build arguments; roles created without one; the reader's and the role sync's passwords read with `\getenv`; the sign-in rules `hostssl` (V6-53, V6-55).
- `auth/nl2sql_auth/` -- `AUTH_DB_SSLMODE` defaults to `verify-full` against `AUTH_DB_SSLROOTCERT` (the database's certificate), for sign-in and the role sync, with a warning for anything that does not verify (V6-53); the directory API answers only on `AUTH_DIRECTORY_PORT` (8447, not published), one server over two sockets, and the directory page proxies to it (V6-58).
- `auth/nl2sql_identity/guard.py`, the three settings modules and the six nginx fragments -- sign-in defaults on in the code, not only in compose; a service started open says so in its banner and its log (V6-54).
- `agent/nl2sql_agent/api/` -- CORS closed until `API_CORS_ORIGINS` opens it (V6-12, and the review service); the job queue bounded, `API_MAX_QUEUED` (20) in all and `API_MAX_PER_PERSON` (3) each, answered 429 `queue_full` with `Retry-After`, and a job that waits longer than `API_QUEUE_TTL_SECONDS` (600) fails rather than running late (V6-13); `access_token=` scrubbed from the access log (V6-11); the banner names a certificate from the development CA as one.
- `agent/nl2sql_agent/database.py` -- `EXPLAIN` runs under `SET LOCAL statement_timeout` (V6-14).
- `ldap/nl2sql_ldap/settings.py` -- a replica refuses a primary over clear text unless `LDAP_UPSTREAM_ALLOW_CLEARTEXT` says otherwise (V6-57).
- Six nginx templates -- `X-Forwarded-Proto` from a TLS terminator in front is honoured (V6-56).
- `setup.sh`, `launch.sh` -- every store password, the reader's, the snippet reader's, the feedback writer's and the role sync's generated (24 random bytes, hex) where `.env` has none, and set in each store by `launch.sh` through an environment variable, never an argument (V6-08, V6-55); the three service tokens with `--tokens`; `.env.bak` keeps the keys but not the secrets; a warning when `DB_BIND_ADDRESS` opens the stores with a default password; `setup.sh` pins `nl2sql-retail-postgres:v1_2`; the desktop client is given `nl2sql-ca.crt`.
- `desktop/` -- `NL2SQL_TLS_FINGERPRINT` takes several fingerprints, and the help points at the CA; the trace shows the model; `missing_assumptions` and `node_errors` read. `gui/` -- the same trace line.
- `start.sh`, `launch.sh`, `benchmarks/run_benchmark.py` -- `nl2sql-ca.crt`; `--desktop` copies the CA's certificate out, and `--mlflow`'s help gives the reason it still implies `--api` (sign-in comes up with it), not a certificate it no longer shares.
- `tests/docker/test_gui_container.py`, `tests/console/test_console_container.py` -- the containers they start are 6.1's: the certificate issued as the pki service issues it, every page verified against the CA, and sign-in off by name for the open hop and its token, which is what they test (the signed-in hop is `tests/auth/test_auth_live.py`'s). The console's no longer starts an API only for its certificate.
- `tests/auth/test_auth_live.py` -- built on the `v1_2` image from this checkout: passwords from the environment, verified TLS for every login over the network, and five more tests -- the directory's API not on the sign-in port, clear text refused, the superuser refused whatever the password, no baked password, the database's own certificate.
- `tests/shell_coverage.py` -- an array assigned over several lines is one command, and a loop's `done` carrying its redirects is not one; `tests/docker/test_retail_entrypoint.py` writes its trace, so the entrypoint is measured. `launch.sh`'s loopback check is an `if` rather than a `case` with an empty branch, which bash never numbers.
- Version 6.1.0 in every declaration; `setup.sh` pins `v6_1`, seventeen tags with the five desktop platforms, and the dataset image `v1_2`.

### Documentation
- `README.md`, `USAGE_GUIDE.md`, `QUICKSTART.md`, `agent/API.md`, `agent/README.md`, `agent/USAGE.md`, `auth/README.md` and the review, console, curation, web, desktop, directory and benchmark READMEs -- the CA and how to trust it; the stores on loopback and `DB_BIND_ADDRESS`; `v1_2`; the directory API's port; the queue and its 429; CORS; what upgrading from `v6_0_1` changes for a running stack; Troubleshooting rows for each.
- `USAGE_GUIDE.md` "Connecting to the retail database directly" and `auth/README.md` "Connecting to the database directly" -- what direct access costs and how to do it over TLS, verified (V6-69); the Security section rewritten around `SECURITY.md`'s tiers; "what the database knows" said as it is (V6-46).

### Verified
Before publishing, against images built from this checkout.
- The acceptance tier (`--run-acceptance`), 9 passed from a clean start, with nothing left behind. Its first two runs failed, on the two defects it was written to find: `setup.sh --build-all` named the review service's profile without its databases', which compose refuses outright; and the desktop client, told the API's port but not the auth service's, signed in at another stack's -- both fixed above.
- The live check: every image built from this checkout and started as a separate compose project beside the running `v6_0_1` stack -- its own network, volumes and ports, the database prepared the way `launch.sh` prepares it. 50 checks passed: ten servers, each presenting its own certificate from the stack's CA; the seven stores on loopback and MLflow's not published; the reader reading over verified TLS, clear text and the superuser refused, the shared password refused and the generated one taken, no password in the image's environment or layers; the administrator signing in on every page and each page reaching its service; the directory's API only on its own port; the console running SQL as the person signed in; MLflow behind its front door; no CORS grant; the API's banner. A question asked through the web interface was answered (`SELECT COUNT(*) AS store_count FROM dim_store`) with `node_errors` empty and each model call's model, rung and route in the trace. It found the four defects listed under Fixed: the pages' upstreams, the review banner, and in the tests the live databases' passwords and the container fixtures.
- The upgrade: a `v1_1` volume started under `v1_2` kept its 1,291,781 sales rows; the owner and reader took the passwords from the environment, the old ones were refused, the superuser had no password left and was refused over the network, and clear text was refused.
- Every test, `--run-docker --run-node --run-java`, passes but `tests/docker/test_published_images.py`, which asked for the eighteen tags before they were pushed (it passes since, below). Python 100% the way `README.md` measures it, 14,811 statements and 3,526 branches; every shell script and nginx fragment 100%, 1,976 commands, the dataset image's entrypoint among them; the desktop client's 409 tests at 100% under JaCoCo; the five web interfaces at 100%.
- Running two stacks at once took Docker's VM (7.75 GiB) out of memory once: the running stack's directory was restarted by its restart policy and a process in its MLflow container was killed; both came back healthy, and nothing on their volumes changed. The acceptance tier now checks the headroom before it starts.

### Published
- All seventeen tags as `v6_1` -- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-curate-gui`, `nl2sql-console-gui`, `nl2sql-mlflow`, `nl2sql-mlflowdb`, `nl2sql-mlflow-proxy`, `nl2sql-ldap`, `nl2sql-auth`, `nl2sql-directory-gui` (amd64, arm64) and `nl2sql-desktop-build:v6_1-{mac-aarch64,mac,linux,linux-aarch64,win}` -- and the dataset image `nl2sql-retail-postgres:v1_2` (amd64, arm64), with its `latest` moved onto it from `v1_1` (2026-10-04 UTC), each checked absent just before its push, all first time. Checked after: the agent, review and auth images report 6.1.0 on both architectures and carry 6.1's code (the CA module, the trace's routing fields, the review banner's certificate, sign-in's `verify-full` and port 8447), the directory its clear-text refusal; every image is labelled 6.1.0 on both, the dataset 1.2.0 with its entrypoint and no password in its environment; every desktop jar is 6.1.0 with its own platform's native code; `tests/docker/test_published_images.py`, 23 passed.
- Then `./start.sh --review --curate --console --mlflow` upgraded the running `v6_0_1` stack: it re-pinned `.env`, generated the stores' passwords, pulled the eighteen and brought every page up with no warning but the usual one about the kept database volume. The retail volume, `v1_1`'s until then, kept its 1,291,781 sales rows under `v1_2`: verified TLS answers with the generated reader password, while clear text, the superuser and the old shared password are refused. 42 checks passed against it -- every container healthy on the new images, the administrator signed in on every page, each page reaching its service at 6.1.0, the console, the directory and its API's port, MLflow's door, every store on this machine only, and a question answered. The old `nl2sql-api.crt` is replaced by `nl2sql-ca.crt`, which a browser trusts once.

## v6_0_1 (6.0.1) -- 2026-10-04

A correction to 6.0, found by doing what a user does: `./start.sh` with every
page, against the published `v6_0` images. Three things in the images were
wrong and one in the checkout, and none of them could be seen by a test that
ran a service on its own. Nobody could sign in: the directory restarted for
ever, and `launch.sh` had not prepared the database. Once that was fixed, five
wrong passwords from anyone on the machine locked everyone out for fifteen
minutes; and the desktop client said it was not connected to a server that was
only asking who it was.

### Fixed
- `ldap/nl2sql_ldap/service.py`, `ldap/Dockerfile`:
  - **Before:** the image ran as `ldap` (`USER ldap`) and wrote its certificate into the `ldaptls` volume. Compose creates the directory's and the auth service's containers before it starts either, and creating a container on an empty volume copies that image's directory onto it, ownership included -- the last created wins. The auth image's `/etc/nl2sql/ldap-tls` is root's, so the directory started on a volume it could not write, failed with `PermissionError` on `ldap.key`, and restarted for ever. The live test had started the directory alone, first, and passed.
  - **After:** no `USER`; the entry point starts as root only to give the `ldap` user the directories `serve` writes (`become`, not recursive, and a directory that cannot be given is left alone), then drops groups, group and user before anything else, so slapd and everything it starts run as `ldap`. A `docker exec` of the module and the health check give root up the same way.
- `auth/nl2sql_auth/throttle.py`, `settings.py`, `app.py`:
  - **Before:** five wrong passwords per name *and* five per address. Behind a page's nginx, or Docker's NAT, every browser on a machine is one address, so one person's typing locked everybody out of signing in for fifteen minutes -- which is how the live check found it.
  - **After:** an address has its own limit, `AUTH_THROTTLE_ADDRESS_FAILURES` (50); a name's stays at five (`Throttle(per_kind=...)`).
- `desktop/.../ui/MainWindow.java`:
  - **Before:** `/v1/meta` is a `/v1` route and answers only someone signed in, so against a server with sign-in on the window's first call failed with 401 and the status bar said "Not connected." -- with no sign-in panel, which appeared only after a question was refused. The tests' fake server had answered `/v1/meta` to anyone.
  - **After:** a 401 from `/v1/meta` brings up the sign-in panel, and the server is asked again once someone has signed in.
- `launch.sh` -- `ldap_hba.sh` needs `NL2SQL_LDAP_HOST`, and `launch.sh` did not pass it, so the database was never given its sign-in lines; the fake `docker` the script is tested against takes any environment. It passes `nl2sql-ldap` now.
- `tests/docker/test_published_images.py` -- its list of images was still the nine of 5.6, so the four new ones were never asked about; held to `setup.sh`'s thirteen families now, seventeen references.

### Updated
- `tests/ldap/test_ldap_image.py` -- the race itself, reproduced: the directory's container created, then one whose image owns the directory as root, on one volume; the directory is healthy, its certificate written, slapd and the supervisor running as `ldap`, and a root `docker exec` of the module works. `v6_0`'s image fails it with the same `PermissionError`. OpenLDAP's own tools are run with `-u ldap`.
- `tests/ldap/test_service.py` -- `become`: hands over and drops in order, leaves a directory it cannot give, does nothing when not root; `main` hands over the directories only for `serve`. `tests/auth/test_auth_keys_login.py`, `test_auth_app.py` -- the address's own limit. `tests/docker/test_launch_script.py` -- every `${NAME:?}` `ldap_hba.sh` requires is passed. `MainWindowTest` -- a server that answers nobody until they sign in.
- `docker-compose.yml` -- `AUTH_THROTTLE_ADDRESS_FAILURES`. `auth/README.md`, `ldap/README.md`, `README.md`, `desktop/README.md` -- the throttle, the entry point and root, test counts.
- Version 6.0.1 in every declaration; `setup.sh` pins `v6_0_1`.

### Checked live, before publishing
On the user's stack, upgraded by `./start.sh --review --curate --console --mlflow` and then run with the fixed directory and auth images built here:
- Every service healthy, with no warning; `pg_hba.conf` holds the sign-in lines; the role sync made two throwaway people -- loaded with the directory's own `import`, one in every group and one in `nl2sql-users` only -- into roles with their groups and a connection limit of five.
- 39 checks through each published port: sign-in and its refusals; the API answering the asker as their own role, hiding their job from everyone else and refusing another principal; the web interface's cookie through its nginx, and a write from another site refused; the console running a statement as the person who typed it; review, curation and the directory page refusing an asker and serving their own group; another page's port refused as another origin; MLflow's front door sending a browser to sign in, letting a reviewer's Basic credentials through and refusing an asker; a person changing their own password.
- The desktop client's own classes, driven from `jshell`: refused before signing in, a wrong password refused, signed in, and a question asked and answered as the person. `psql` straight to the retail database with a directory password: reads as the person, cannot write, refuses a wrong password itself. MLflow's Python client through the front door as a reviewer, and refused as an asker. The first administrator, with the password `setup.sh` generated. `--no-auth`, and back.
- The throwaway people removed, and their roles gone with them.

### Published
- All seventeen tags as `v6_0_1` -- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-curate-gui`, `nl2sql-console-gui`, `nl2sql-mlflow`, `nl2sql-mlflowdb`, `nl2sql-mlflow-proxy`, `nl2sql-ldap`, `nl2sql-auth`, `nl2sql-directory-gui` (amd64, arm64) and `nl2sql-desktop-build:v6_0_1-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-04 UTC), each checked absent just before its push. Checked after: every image labelled 6.0.1 on both architectures; the directory image has `become` and no `USER`, the auth image an address limit of fifty, and every desktop jar `describeServer`, 6.0.1 and its own platform's native code; `tests/docker/test_published_images.py`, 23 passed. Then a plain `./start.sh --review --curate --console --mlflow` re-pinned the user's `.env`, pulled the seventeen tags and brought every page up with no warning but the usual one about the kept database volume; the 39 live checks passed against the published images, and the published desktop jar for this machine signed in and was answered.

### After publishing
In the checkout, not in the `v6_0_1` images: the whole suite, Docker tests
included, run against the published stack, and what it found.
- `tests/docker/test_gui_container.py`, `tests/console/test_console_container.py` -- still asked each page over plain HTTP, which a page that is HTTPS since 6.0 answers 400; they speak HTTPS now, verified against the certificate the API container wrote, so they also show each page presents the right one.
- `tests/agent/test_least_privilege_live.py` -- "the reader is a member of no other role" stopped being true by design in 6.0: it is granted each person's role to become them. Restated as what it was protecting: the reader inherits from nothing, can grant nothing, and may become only people.
- `docker/auth_roles.sql` -- its two repairs granted again what the role sync already held, which Postgres records as a second membership with the superuser as grantor; they grant only what is missing now. `tests/auth/test_auth_live.py` -- applying it again on a later start changes no membership. The one duplicate on the user's database was revoked.
- `USAGE_GUIDE.md` -- what upgrading to `v6_0_1` changes for a running stack, and why `v6_0` is not worth pinning; Troubleshooting rows for each sign-in warning `launch.sh` prints, the certificate warning, someone just added, the throttle, a locked account and an old desktop client; the new services among the log names; MLflow's front door. `agent/API.md` -- what `/v1/meta` says to a caller it will not answer yet. `desktop/README.md` -- how the window decides to ask. `README.md` -- the web interface's token with sign-in on, five web interfaces' thresholds, the new tests in the test table, test counts. `ldap/README.md`, `auth/README.md` -- their tests.
- Measured on the published stack: every test, `--run-docker --run-node --run-java`, passes but `test_no_benchmark_question_is_a_golden_pair_verbatim` (benchmark question B03 is golden pair Q46 word for word, since 5.4). Python 100% the way `README.md` measures it, 14,266 statements and 3,410 branches; every shell script and nginx fragment 100%, 1,829 commands; the desktop client's 405 tests at 100% under JaCoCo; the five web interfaces at 100%. `tests/review/test_store_live.py` deadlocked once against the live staging database during one full run, and passed alone three times and in the next full run: it shares a role with the running review service.
- `adversary_reviews/` -- the second adversarial review cycle, tagged `v6_1_review`, at this commit (`9b0b340`): the same five documents as the first cycle (`v6_x_review`, at 5.6.1) -- the architecture as documented, the architecture as implemented, the implementation, the combined plan and the summary -- each with an `_enhanced` edition, keeping every finding id from the first cycle and giving each a status: 8 resolved (the identity findings D-05, I-06, C-10, S-01, S-02, S-03, S-06, S-14), 5 improved, 3 mitigated by sign-in being the default, 32 unchanged, 18 worse; 19 new, 77 in all, both Criticals (M-08/S-08) unchanged and S-16 new: a person's directory password crosses to the retail database in clear text. The plan carries the first plan's 51 items with their status (4 done, 1 superseded, 8 partial, 38 open) and 20 new ones, and moves separate TLS identities (V6-36) ahead of non-root images (V6-31), which wait for them. A sixth document, `v6_1_review_changes_since_v6_x.md`, is the finding-by-finding and item-by-item comparison, with where the first cycle was wrong. `adversary_reviews/diagrams/generate.py` writes nine more figures under the new tag, the first cycle's ten unchanged byte for byte; `tests/docs/test_review_diagrams.py` covers both cycles, a second-cycle document that re-embeds a first-cycle figure saying so, and the comparison's ids. `README.md` -- the figures, test counts.

## v6_0 (6.0.0) -- 2026-10-03

Sign-in, on by default. A person signs in with the user name and password a
directory holds, and the retail database checks that password itself:
pg_hba's `ldap` method binds to the directory, so a person the directory
does not know cannot connect to anything, by any route. The groups they are
in become the database roles they hold, and those decide what they may open:
`nl2sql-users` ask questions, `nl2sql-reviewers` review and read MLflow,
`nl2sql-curators` curate, reviewers and curators use the SQL console, and
`nl2sql-admins` manage the directory. What a person asks or runs, runs as
their own database role. The directory is standalone -- people loaded from a
file on its first start and edited on a page of its own -- or a read-only
replica of Active Directory or any LDAP server, with every password checked
by the primary. Every interface is HTTPS and asks who you are, MLflow is
reached only through a front door that does the same, and the desktop client
signs in too. `--no-auth` turns all of it off. Four new images, and the
major version, because the defaults change under existing users.

### Created
- `auth/nl2sql_identity/` -- what every service shares: `tokens.py`, the session (an Ed25519-signed JWT with a fixed header, issuer and audience, refused in its own words when malformed, unsigned by the key, for another audience, early or expired); `guard.py`, the FastAPI dependency each service puts in front of its routes -- a session from a bearer token or the `nl2sql_session` cookie, the service's static token beside it, the origin check for a write riding on a cookie (`Sec-Fetch-Site`, else `Origin`/`Referer` against `X-Forwarded-Host`), the auth service's public key read when it appears and again when it changes, and the caller's groups re-read from Postgres at most once a minute; `postgres.py`, that lookup (`pg_has_role`).
- `ldap/` -- the directory image (`nl2sql-ldap`): OpenLDAP 2.6 on Alpine with the memberof, refint, ppolicy and remoteauth overlays and Argon2 hashing, run as the `ldap` user, which is also the directory's root on its local socket by peer credentials. `nl2sql_ldap/`: the settings and both modes; the layout and the rule a user name must meet to be a role name; the CSV and LDIF readers; people and groups, read and written; `slapd.conf`, rendered on every start; a self-signed certificate for StartTLS; the first start (the base, the four groups, the auth service's account, the first administrator, the seed file); the replica's copy -- paged searches, nested groups resolved, groups mapped, and a copy that would empty a directory with people in it refused; the supervisor and its commands (`serve`, `health`, `import`, `sync`, `config`). `ldap/README.md`; `ldap/seed/people.example.csv`.
- `docker/auth_roles.sql` -- `nl2sql_ldap` (every person's marker), the four group roles and what each may read, the three narrower ones members of `nl2sql_users` without becoming it, and `nl2sql_rolesync`, which can create roles and administer those five and nothing else. Idempotent; applied on every start.
- `docker/ldap_hba.sh` -- writes the sign-in lines at the top of `pg_hba.conf` between markers -- the roles that keep their own passwords by `scram-sha-256`, then every member of `nl2sql_ldap` by `ldap` over StartTLS -- checks the file with `pg_hba_file_rules`, puts the old one back if it has errors, and reloads. With sign-in off it takes them out.
- `auth/nl2sql_auth/` -- the auth service (`nl2sql-auth`, port 8446): signing in by opening a connection to the retail database as the person, with their password; the session as a cookie (`POST /auth/login`) or a token (`POST /auth/token`); `/auth/session`, `/auth/logout`, a person's own password change (`/auth/password`); `/auth/verify` for a proxy's `auth_request`, including an MLflow client's Basic credentials, checked by signing in and kept five minutes; a plain HTML sign-in form for a proxy to send a browser to; a throttle per name and per address; the role sync, every 30 seconds and after every edit -- a `LOGIN` role per person with a connection limit, read-only transactions and a statement timeout, group memberships added and taken away, the reader granted each person's role to become them and nothing more, a person gone dropped or, if they own anything, disabled; and the directory page's API, for `nl2sql_admins`, with a replica refused. `auth/Dockerfile`, `auth/requirements.txt`, `auth/README.md`.
- `auth/gui/` -- the directory page (`nl2sql-directory-gui`, port 8084): React and TypeScript behind nginx, which proxies `/auth/` and `/directory/` to the auth service; people listed, added with a generated password if wanted, edited, put in groups, given passwords, unlocked and removed; a file imported; the role sync run. Its start-up refuses `LDAP_MODE=replica`. 54 vitest tests at 100% coverage.
- `docker/mlflow-proxy/` -- MLflow's front door (`nl2sql-mlflow-proxy`): nginx over HTTPS with the API's certificate, asking `/auth/verify` about every request with the method it is asking about; a browser that has not signed in is sent to sign in, an API client is told `401`, and `/health` stays open.
- `desktop/` -- `Session`, the token held in memory for every call; `SignIn` and `HttpSignIn`, `POST /auth/token` over the API's TLS settings; `SignInView`, the panel above the question box.
- Tests: `tests/auth/` (the session format, the guard, the auth service's every part, the compose wiring of all four services, the image, the directory page's project, contract and suite, and sign-in end to end against a real database, directory and MLflow), `tests/ldap/` (every module against ldap3's in-memory directory, and the image: slaptest on both modes, and a replica copying a second directory end to end), `tests/api/test_signin.py`, `tests/console/test_signin.py`, `tests/review/test_signin.py`, `tests/docker/test_ldap_hba_script.py`, `tests/docker/test_mlflow_proxy.py`; `HttpSignInTest`, `SessionTest`, `SignInViewTest` in the desktop client.

### Updated
- `agent/nl2sql_agent/api/` -- every `/v1` route behind the guard (`nl2sql_users`); a signed-in person's questions run as them, and a `principal` naming anyone else is refused; each job has an owner, and someone else's is a `404`; `/readyz` says whether sessions can be checked; `/v1/meta`'s `authentication` can be `session`. `AUTH_ENABLED`, `AUTH_PUBLIC_KEY_FILE`, `AUTH_COOKIE_NAME`.
- `agent/nl2sql_agent/console/` -- behind the guard, for `CONSOLE_ALLOWED_ROLES` (reviewers and curators); each statement runs as the person who typed it, and the answer says as whom (`runs_as`).
- `review/nl2sql_review/` -- the queue for `REVIEW_REVIEWER_ROLES`, direct writes for `REVIEW_CURATOR_ROLES`, reading for either; a reviewer's name on a decision is the one they signed in as; corrected SQL and snippets are validated as the person.
- `agent/nl2sql_agent/database.py`, `graph.py` -- the planner gate asks as the principal too. `tracing.py` -- the probe trusts the certificate MLflow's client is told to.
- `agent/Dockerfile`, `review/Dockerfile` -- carry `nl2sql_identity`; `review/requirements.txt` -- `cryptography`.
- The four web interfaces -- a sign-in gate admitting the groups each is for, with password change and sign-out; a `401` brings the gate back; nginx serves HTTPS with the API's certificate (`GUI_TLS_ENABLED`), proxies `/auth/` to the auth service, reports `X-Forwarded-Host`, and sends no token with sign-in on; HTTPS health checks. 332, 175, 101 and 150 tests, each at 100%.
- `desktop/` -- `--auth-url`/`NL2SQL_AUTH_URL` (the API's host, port 8446) and `--user`/`NL2SQL_USER`; asks before the first question when the server wants sign-in, asks again on a `401` and then asks the refused question again; the status bar says who and offers to sign out. 404 tests at 100%.
- `benchmarks/run_benchmark.py` -- MLflow at `https://localhost:5001`, trusting `./nl2sql-api.crt` when it is there.
- `docker-compose.yml` -- `ldap` and `auth` (profile `auth`), `directorygui` (profile `directorygui`), `mlflowproxy` (profile `mlflow`); MLflow's server no longer published; `AUTH_ENABLED` (default `true`), the public key's volume and the cookie's name for the API, the console and the review service; sign-in and TLS settings for every interface, one `GUI_` line in `.env` for all of them; the directory's certificate for the retail database (`LDAPTLS_CACERT`); `nl2sql-auth` in `API_TLS_HOSTNAMES`; volumes `ldapdata`, `ldaptls`, `authdata`, `authkeys`.
- `launch.sh` -- with the API, prepares the database for sign-in (`auth_roles.sql`, `ldap_hba.sh`), generates any of the three sign-in passwords `.env` lacks, starts the directory, the auth service and -- beside a standalone directory -- the directory page, and says who signs in first and where; with sign-in off, takes the `pg_hba` lines out. `--no-auth`. `--mlflow` implies `--api`. Every page's address is HTTPS; the proxy probe follows the pages' scheme; the warnings about no token and exposed ports apply with sign-in off.
- `setup.sh` -- pulls and pins `nl2sql-ldap`, `nl2sql-auth` and `nl2sql-directory-gui` (and `nl2sql-mlflow-proxy` with `--mlflow`); generates `LDAP_ADMIN_PASSWORD`, `LDAP_SERVICE_PASSWORD` and `AUTH_ROLESYNC_PASSWORD` once and carries them from one `.env` to the next; `.env` and `.env.bak` are 0600. `--no-auth` writes `AUTH_ENABLED=false`, which later runs keep.
- `start.sh` -- `--no-auth` for one run; HTTPS addresses; how to sign in; stop commands that name the sign-in profiles.
- `README.md` (Sign-in, the container, image and tag tables, the publish commands, Tracing's front door, test and file counts), `USAGE_GUIDE.md` (Signing in, the addresses, ports and flags, Security), `QUICKSTART.md`, `agent/API.md` (Authentication, the error codes, the settings), `review/README.md`, `console/README.md`, `curate/README.md`, `gui/README.md`, `desktop/README.md` (Signing in), `benchmarks/README.md`.
- `tests/docs/test_versions.py` -- the four new images among the tags that move together. `tests/docker/` -- the fake `docker` knows the sign-in containers and records what compose was told about sign-in.
- Version 6.0.0 in every declaration -- thirty-two places, each lockfile counted twice; `setup.sh` pins `v6_0`, seventeen tags with the five desktop platforms.

### Checked live, before publishing
- `tests/auth/test_auth_live.py`: the directory, the auth service and MLflow's front door built from this checkout, beside a copy of the published retail database on a private network, the database prepared as `launch.sh` prepares it. The first administrator signs in through the database; a person added on the directory page's API signs in once the sync has run, reads the retail tables as their own role, cannot write, and can no longer connect once removed; the reader becomes a person only for a transaction and never a group; a browser is sent to sign in at MLflow's door, an MLflow client's Basic credentials are let through, and a write from another site is refused.
- `tests/ldap/test_ldap_image.py`: both modes' `slapd.conf` pass slaptest, and a replica copies a second directory, passes a sign-in through to it, and refuses to be edited.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-curate-gui`, `nl2sql-console-gui`, `nl2sql-mlflow`, `nl2sql-mlflowdb` `:v6_0`, and for the first time `nl2sql-mlflow-proxy`, `nl2sql-ldap`, `nl2sql-auth` and `nl2sql-directory-gui` `:v6_0` (amd64, arm64); `nl2sql-desktop-build:v6_0-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-04 UTC), each checked absent just before its push. The `mac` desktop build failed once on Maven Central, pushing nothing, and was built again. Checked after: the agent, review and auth images report 6.0.0 on both architectures and carry the session guard, the directory imports its package and ldap3; every image is labelled 6.0.0 on both; every interface's bundle carries the sign-in and the front door its `auth_request`; every desktop jar is 6.0.0 with the sign-in classes and its own platform's native code; `tests/docker/test_published_images.py`, 23 passed once it asked about the four new images.

### After publishing
`./start.sh --review --curate --console --mlflow` against these images found three defects in them and one in `launch.sh`: nobody could sign in. They are corrected in `v6_0_1`, not by moving `v6_0`.

## v5_6_1 (5.6.1) -- 2026-10-03

A correction to 5.6. The narrator writes the cell a number came from into
its sentence -- "..., as shown in row 0, column click_through_rate_pct" --
and on a first pass leaves the claim's list of cells empty. 5.6 took the
address out of the sentence, but with no cells nothing backed the number,
the audit dropped every claim, and only the narrator's rewrite survived: an
extra model call on such answers, and a table with no sentence when the
rewrite failed too. A claim with no cells of its own is now cited by the
address its sentence wrote. Everything under v5_6's *After publishing* --
this fix, the scripts checked live, the documentation -- ships in this
release.

### Fixed
- `agent/nl2sql_agent/present.py`:
  - **Before:** a claim the narrator cited only in its own words ("as shown in row 0, column ...") had no cells; the audit found nothing behind its number and dropped it. 5.6 also told the narrator to put the address in `cells`, which it took as leaving it out of the sentence while still not filling `cells`.
  - **After:** `written_cells` reads the address in the sentence -- rows the result has, the named column when the result has it, else the whole row -- as the claim's cells when it lists none; the address is then taken out of the sentence, and the narrator's prompt is 5.5.1's again. On the live click-through question the first pass now survives, twice in two runs, with no rewrite; benchmark questions B07 and B11 match their reference rows with the audit passing first time.

### Updated
- `tests/agent/test_present.py` -- the narrator's real first pass, word for word, cited by its sentences and surviving the audit; an address with no column, one naming a row the result lacks, and cells the narrator did list, which win.
- `README.md` -- the `v5_6_1` tag, pull and publish commands; `CHANGELOG.md`, `CHANGELOG_SIMPLE.md`.
- Version 5.6.1 in every declaration; `setup.sh` pins `v5_6_1`.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-curate-gui`, `nl2sql-console-gui`, `nl2sql-mlflow`, `nl2sql-mlflowdb` `:v5_6_1` (amd64, arm64); `nl2sql-desktop-build:v5_6_1-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-03 UTC), each checked absent just before its push. Checked after: the agent runs 5.6.1 on both architectures with `written_cells` and the narrator's 5.5.1 prompt, the review service 5.6.1 with the snippet loader; every image is labelled 5.6.1 on both; every desktop jar is 5.6.1 with its own platform's native code; `tests/docker/test_published_images.py`, 19 passed. `start.sh --review --curate --console --mlflow` then found `.env` pinning `v5_6`, re-ran `setup.sh`, pulled the thirteen tags and brought every page up; the snippet store already held the document, so nothing was re-embedded. Through it the published agent answered the click-through question with its narrative passing the audit on the first pass.

### After publishing
In the checkout, not in the `v5_6_1` images: a coverage and relevance audit
of the tests, and the documentation it found wanting. No image changed.
- Coverage held: 100% of the Python, statements and branches, with the tests themselves measured as well -- what they leave unexecuted is skips, failure messages and the canary a formula must never call; all four web interfaces at 100%; the desktop client under JaCoCo; every shell script and nginx fragment at 100% by the shell measurement.
- `tests/curate/test_curate_compose.py` -- the snippet store's own settings (owner, password, database, port, image) set through compose and found in the store, the review service's owner URL and the agent's reader URL; the review service pointed at another store; and every setting of the curation page set by its compose name and found under the name nginx reads. Ten of 5.6's compose settings had been exercised by nothing.
- `tests/docs/test_docs.py` -- every setting the snippet store and the curation page read has a documented row with its default; every setting the review service reads has a row in `review/README.md`, which found two, `REVIEW_DOCS_ENABLED` and `REVIEW_LOG_LEVEL`, with none since they were added; `rag/README.md`'s count of database-backed tests is read. `tests/docs/test_versions.py` -- the README's "twenty-four places" and "thirteen tags" are counted from the declarations and `setup.sh`.
- Removed: the fake `docker`'s `FAKE_GOLDEN_EMBEDDED`, a switch no test set; two `--help` tests for the curation flags, which `test_every_parsed_flag_appears_in_the_usage_text` already asks of every flag; an unused import and a second import of the same module in two older test files.
- `README.md` -- the snippet store's settings, five retrievers in the trace sketch, test counts; `review/README.md` -- the two settings; `USAGE_GUIDE.md`, `agent/USAGE.md`, `benchmarks/README.md` -- four databases, not three, and the snippet store among those `setup.sh` starts and `docker compose stop` names; five Stage 1 retrievers since v5.6.
- `adversary_reviews/` -- the first adversarial review cycle, tagged `v6_x_review`: the architecture as documented, the architecture as implemented, and the implementation (hygiene, containers, security controls short of the LLM's), each ending in a plan; the three plans combined into one, de-duplicated and ordered by dependency (`V6-01` to `V6-51`); a summary of the 66 findings; and an `_enhanced` edition of each with figures. `adversary_reviews/diagrams/generate.py` writes the ten figures as draw.io files, with draw.io's SVG and PNG exports beside them; `tests/docs/test_review_diagrams.py` holds the committed files to the script, the exports to the files and the enhanced documents to their originals; `.coveragerc` measures the script. `README.md` -- the script and its tests under the architecture diagrams, test counts.

## v5_6 (5.6.0) -- 2026-10-03

SQL snippets: pieces of SQL that have been run against this database, each
beside what it means in a question's words. A snippet is a join (how two
tables meet, and what the join is for), a filter ("store brands" is
`p.is_private_label`), a measure (how net sales or the average basket is
calculated) or a dimension (a fiscal quarter labelled the way people say
it). They are curated in a tracked document,
`context_questions/sql_snippets.md`, which ships 32 of them, and loaded into
a store of their own. That store is a fifth retriever for the agent,
separate from the knowledge base, the golden pairs and the fix stores. Each
question's snippets are found by keyword phrase and by meaning, and the SQL
Generator sees those whose tables are all in scope. A fourth web interface,
the curation interface, writes snippets, golden pairs, corrections and
completions directly, without a submission. Each one is run against the
retail database before it can be saved. Promotion from the review queue
runs the golden SQL first as well. Thirteen tags were published, the
curation interface's for the first time.

### Created
- `context_questions/sql_snippets.md` -- the snippet document: 32 snippets (8 joins, 10 filters, 10 measures, 4 dimensions), each with its kind, tables, keyword phrases, meaning, the FROM clause it applies to, its SQL and a note. Every one was run against the retail database, and the counts in the notes are what it returned.
- `rag/ragproc/snippets.py` -- the document's strict parser (a snippet that does not parse is an error, not skipped); the store's three tables (`sql_snippets`, `sql_snippet_vectors`, `sql_snippet_source`); the keyword matcher `sql_snippets_keyword_match()`, in SQL, which matches each keyword and the name as a phrase whose words must all be in the question, weighted by IDF; the read-only role; and the document hash a complete load records.
- `rag/07_load_snippets.py` -- loads the document into the store, removes snippets no longer in it, (re)creates the agent's `snippets_reader` role with SELECT and nothing else, and embeds only the meanings that changed, so a load with nothing new needs no embedding host. A failed embed exits non-zero and records no hash, so the next start loads again.
- `agent/nl2sql_agent/snippets.py` -- `SnippetLibrary`: candidates from the keyword matcher and the 20 nearest meanings; a snippet qualifies on a matched phrase or on meaning alone at cosine 0.62. It is kept when the average of the two signals, each on a fixed 0..1 scale, reaches 0.35, and at most five are kept. An embedding host that is down costs the meaning half and says so; an unreachable store costs the snippets.
- `review/nl2sql_review/snippet_validation.py` -- a snippet is run the way it would be used: a join as `SELECT * FROM <applies to> <join>`, with row counts before and after so a fan-out or dropped rows is a warning; a filter in a `WHERE`, warning when it matches nothing or everything; a measure as one aggregate row; a dimension under `GROUP BY`. Static checks run first (no `;`, comments or fences; balanced parentheses and quotes; a join starts with `JOIN`). The probes run as `nl2sql_reader` in a read-only transaction under a timeout, and the tables the SQL uses are held to the ones the snippet lists.
- `review/nl2sql_review/snippets.py` -- adds, changes and removes snippets in the document and reloads the store. Every write is parsed back with the loader's own parser, compared field by field, and kept as `.bak` beside the document.
- `curate/` -- the curation interface (`nl2sql-curate-gui`, port 8083): React and TypeScript behind nginx, a page in front of the review service like the review interface, with three tabs. *SQL snippets*: browse by kind, search, edit, validate, preview the markdown, add, save, remove. *Golden pairs*: add a pair no feedback produced, or remove one, which reopens the submission it came from. *Corrections & completions*: write a fix for a question directly, or remove one. Nothing can be saved until the exact text has passed against the database. 81 vitest tests at 100% coverage.
- `arch_diagrams/arch_v5_6.{svg,png,tif}` -- v5.2 with the Snippet Retriever, the snippet store in the deployment and the curation interface beside the review one.
- Tests: `tests/agent/test_snippets.py`; `tests/rag/test_snippets_parser.py`, `tests/rag/test_snippets_store.py` (the matcher, the loader and the reader role in a real pgvector); `tests/review/test_snippet_validation.py` (each kind against the live retail database), `tests/review/test_snippets.py`, `tests/review/test_curation.py`; `tests/curate/` (the interface's types against the service's models, the nginx start-up script, the project and the compose wiring both ways, and its own suite run from here).

### Updated
- `agent/nl2sql_agent/graph.py` -- a fifth Stage 1 node, `retrieve_snippets` ("Snippet Retriever", a retriever span), beside the other four. The Context Aggregator keeps the snippets whose tables are all in the selected set, and widening the scope after a repair recomputes them. Snippets propose no tables.
- `agent/nl2sql_agent/prompts.py` -- the generator's prompt has a snippets block after the knowledge and before the literals, with its own instruction. With no snippet in scope it is empty, and the prompt is byte for byte 5.5.1's.
- `agent/nl2sql_agent/tools.py` -- `search_snippets`. `state.py` -- `snippets`, `snippet_hits`, `snippet_context`.
- `agent/nl2sql_agent/config.py` -- `SNIPPETS_ENABLED` (on), `SNIPPET_DB_URL` (the reader role), `SNIPPETS_TOP_K` (5), `SNIPPETS_MIN_SCORE` (0.35), `SNIPPETS_MIN_SIMILARITY` (0.62) and `SNIPPETS_MAX_CONTEXT_CHARS` (4000). `__main__.py` -- `--snippets/--no-snippets`, `--snippet-db-url`, `--snippets-top-k`; `--json` carries `snippet_hits`.
- `review/nl2sql_review/app.py` -- the curation routes: `POST /v1/golden/validate`, `/v1/golden/preview` and `/v1/golden`; `DELETE /v1/golden/{pair_id}`; `POST /v1/fixes/{kind}/validate` and `/v1/fixes/{kind}`; `DELETE /v1/fixes/{kind}/{fix_id}`; `GET`/`POST /v1/snippets`, `POST /v1/snippets/validate` and `/v1/snippets/preview`, `PUT`/`DELETE /v1/snippets/{id}`; `GET /v1/schema`. Readiness checks the snippet document and whether the store holds it.
- `review/nl2sql_review/app.py` -- promotion runs the golden SQL against the retail database before it writes anything: it must run and return rows, and the pair carries the SQL as it was run, with any trailing `;` removed. A query that fails is refused with the database's reason.
- `review/nl2sql_review/corrections.py` -- a fix can be stored without a submission (`source` is `review` or `curated`; the columns are widened on start, so stored fixes are untouched). A fix can be read and deleted by id. Removing a golden pair or a fix that came from a submission puts the submission back to pending.
- `review/nl2sql_review/settings.py` -- `REVIEW_SNIPPETS_DOCUMENT`, `SNIPPETS_DB_URL`, `SNIPPETS_READER_USER`, `SNIPPETS_READER_PASSWORD`, `REVIEW_RELOAD_SNIPPETS`. `review/Dockerfile` -- carries the loader and the document.
- `review/gui/src/api/types.ts` -- the golden pair and fix models as the service now returns them.
- `docker-compose.yml` -- `snippetsdb` (stock `pgvector/pgvector:pg18`, port 5438, volume `snippetsdata`), which the agent and the API depend on and read as `snippets_reader`; the review service writes it as the owner; `curategui` behind the `curategui` profile.
- `launch.sh` -- starts and waits on the snippet store with the other two. It compares the document's hash with the one the last complete load recorded, both taken inside the store's container, and when they differ it runs the loader in the review service's image. A review image from before 5.6 is named as the reason it cannot. It reports what the store holds, and warns when it holds none. `--curate` starts the review service, the two fix stores and the curation interface, without the review interface.
- `setup.sh` -- pins the review service's image whenever retrieval is on, because it carries the loader, and loads the snippets at the end. The two interfaces are pinned only with their own flags: `--curate` (and `--curate-gui-image`, `--curate-gui-tag`) for the curation interface. A re-run keeps each interface the last run pinned.
- `start.sh --curate` -- opens the curation interface in a window of its own. `.env` is re-pinned when the review interface or the curation interface was asked for and is not pinned, or when retrieval is on and the review service is not. `--load-golden` no longer brings in the review interface's pin.
- `benchmarks/run_benchmark.py` -- a fourth configuration, `snippets` (multi-shot plus the snippet store), which is now the default because it is the agent as it ships. The other three turn snippets off, so each row still adds exactly one stage. The store is found on its published port.
- `arch_diagrams/generate.py` -- `build_v5_6`; the earlier diagrams are unchanged.
- `tests/docker/test_published_images.py` -- asks Docker Hub about the curation interface's tag too. Its list of images was written by hand and counted, which is how the new image was missing from it; it is now held to every image `setup.sh` pins at the release's tag. `tests/docker/conftest.py` -- every switch the fake `docker` gained for the snippet store is set by a test.
- `README.md` (SQL snippets and curation, the container, tag and diagram tables, the publish commands, test and file counts), `USAGE_GUIDE.md` (Curating what it learns from, `--curate` for each script, the snippet store's port, the troubleshooting rows), `QUICKSTART.md`, `agent/README.md` (SQL snippets (v5.6), the node, the tool, the six settings), `agent/USAGE.md`, `review/README.md` (Curation, the routes, promotion's first step, the snippet settings), `rag/README.md` (step 7), `benchmarks/README.md`; `curate/README.md` (new).
- Version 5.6.0 in every declaration, `curate/` included -- twenty-four places, with each lockfile counted twice; `setup.sh` pins `v5_6`, and `CURATE_GUI_TAG` is the ninth tag it pins.

### Fixed
- `agent/nl2sql_agent/present.py` -- an answer whose narrator wrote the cell it read into the sentence ("... at 0.0728, as shown in row 0, column click_through_rate_pct") lost that claim: the audit read the row number as a figure no cell backs and dropped it, and on the end-to-end click-through question it dropped all three, with and without snippets, leaving a table and no sentence. The narrator is now told the address goes in `cells`, never in the sentence; an address it writes anyway is taken out of the sentence before the audit reads it, so the reader never sees one; and a number that addresses a row the result has ("Row 0 shows ...") is an address to the audit, not a figure -- a row it does not have still fails the claim. The three claims from that run are a test, word for word, and all three now survive.
- `review/gui/src/styles.css` -- a loader's output in the outcome of a promotion or withdrawal ran together on one line ("... 0 stale rows removed role ..."); each line it printed is its own line now. The curation interface shares the fix.
- `agent/nl2sql_agent/snippets.py`, `prompts.py` (before release) -- asked end to end through the chat model, "What was our click-through rate by ad channel on weekends in fiscal year 2025?" came back 0 for every channel. The generator had been shown S26, `SUM(a.clicks_or_coupon_clips_count)::numeric / NULLIF(SUM(a.impressions_count), 0)`, and kept the division without the cast; without snippets it wrote `100.0 * ...` and was right. A snippet's note -- here "both counts are integers, so without the cast to numeric the division truncates to zero" -- is now shown under its SQL, and the instruction says to keep each piece exactly as written, casts and `NULLIF`s included, changing only aliases and literals. Asked again, twice, it kept the cast and every rate matched a reference query written by hand.
- `rag/ragproc/snippets.py` (before release) -- the document hash is taken over the file's bytes, as `sha256sum` takes it in `launch.sh`, not over the text Python reads, which turns a CRLF checkout's line endings into LF and would never match.

### Checked live, before publishing
- The loader in the built review image, run exactly as `launch.sh` runs it, against an empty pgvector: 32 rows, 32 meanings, the reader role, and a recorded hash equal to `sha256sum` of the document taken in the store's container. Run again with the embedding host unreachable, it changed nothing and embedded nothing.
- The agent image reading the store as `snippets_reader`: a write is refused with "permission denied".
- The curation interface image in front of the review service image, on a private network, against the live retail database: a filter snippet validated (32 of 200 products), added as S33, embedded, found first by the agent's search for a question about it, and removed, leaving the document byte for byte as it was. Also a golden pair refused for a missing column, added once fixed and removed again, and a correction added without a submission, embedded, and removed with its vector.
- End to end through the chat model, with the stack's own `.env` read by compose, the built agent image, and a snippet store loaded by the built review image on the compose network: "What were our net sales by state in fiscal year 2025?" (benchmark B14) was shown S09, S01, S19 and S03, all four in scope, and its eight rows match the benchmark's reference. B14 is also golden pair Q47 word for word, as B03 is Q46, so the worked example alone could have answered it; the click-through question above, which no pair covers, is the one that showed what the snippets do.
- Retrieval over the fifteen benchmark questions against the loaded store: thirteen are shown snippets, every one about what the question asks; the two bare count questions are shown none. A question that combined a measure with two filters lost both filters when keyword strength was scored relative to the question's best phrase. It is scored by its own weight now (`KEYWORD_SCALE`), and the case is a test.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-console-gui`, `nl2sql-mlflow`, `nl2sql-mlflowdb` `:v5_6` (amd64, arm64); `nl2sql-curate-gui:v5_6` (amd64, arm64), its first publish; `nl2sql-desktop-build:v5_6-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-03 UTC). Each tag was checked absent just before its push. Checked after: the agent and review images run 5.6.0 on both architectures, the agent with the snippet retriever and the audit fix, the review service with the loader and the snippet document; the review and curation interfaces' bundles carry their fixes; every desktop jar is 5.6.0 with its own platform's native code; `tests/docker/test_published_images.py`, 19 passed.

### After publishing
In the checkout, not in the `v5_6` images: one agent change, the scripts
run live, and documentation. A correction to the published agent would be a
new patch tag, not a re-push of `v5_6`.
- `agent/nl2sql_agent/present.py` -- the published fix took the address out of the sentence and stopped the audit holding a row number against a claim, and told the narrator to put the address in `cells`. Asked again through the published agent, the click-through question's first pass still lost all three claims and only the rewrite survived. Its trace shows why: on a first pass the narrator writes the address into the sentence *instead of* into `cells`, which it leaves empty -- with or without that instruction, and as it did before 5.6 -- so no cell backed the number. A claim with no cells of its own is now cited by the address its sentence wrote (`written_cells`: only rows the result has; a named column if the result has it, else the whole row), and the instruction is gone, so the narrator's prompt is 5.5.1's again. Asked twice more, the first pass survived both times with no rewrite; benchmark questions B07 and B11 matched their reference rows with the audit passing first time.
- `start.sh --review --curate --console --mlflow`, run live against the published images: `.env` pinned 5.5.1, so it re-ran `setup.sh`, which kept the Ollama host, models and port, pulled all thirteen `v5_6` tags, and loaded the 32 snippets into the snippet store with the `v5_6` review image. `launch.sh` then found the store's hash equal to the document's and loaded nothing, counted 32 snippets and 32 meanings, and every page answered: the web interface, review, curation, the SQL console and MLflow. The published agent answered the click-through question correctly, narrative included.
- `README.md`, `CHANGELOG.md`, `CHANGELOG_SIMPLE.md` -- the release as published, and this section.

## v5_5_1 (5.5.1) -- 2026-10-01

A correction to 5.5. The golden question set stopped at `Q99`: a pair id was
exactly two digits, so the hundredth promotion was refused. It now has no
ceiling. The agent's `--help` has said `--multi-shot` was off since both
arrived in v3, when it has always been on. `start.sh` re-pinned an agent tag chosen with
`setup.sh --agent-tag` on its next start, so an older agent could only be run
without it. Everything under v5_5's *After publishing* -- the scripts' review
text, `setup.sh`'s database list, the usage guide and quick start, and the
tests that came with them -- ships in this release's checkout too.

### Fixed
- `rag/ragproc/golden_pairs.py`, `review/nl2sql_review/render.py`:
  - **Before:** a pair id was `Q\d{2}`. `Q99` was the last one the loader matched, and the review service refused the hundredth promotion with "the golden set is full" rather than write a pair the loader would not see.
  - **After:** a pair id is `Q` and at least two digits (`PAIR_ID = r"Q\d{2,}"`), so `Q100` follows `Q99`. Ids keep two digits until they need three, so every existing id, chunk id and stored row is unchanged; the columns that hold them were always `TEXT`, so neither dataset image changes.
- `agent/nl2sql_agent/__main__.py`:
  - **Before:** `--multi-shot` was described as "(default: off)" while `MULTI_SHOT_ENABLED` defaulted on, and `--rag` and `--examples` said "on" whatever the environment had set.
  - **After:** each switch's help gives the default the parser really has, read from the same setting.
- `start.sh`, `setup.sh`, `launch.sh`:
  - **Before:** `start.sh` treated any agent tag other than the shipped one as left over from an older checkout and re-ran `setup.sh`, which pinned the shipped tag back.
  - **After:** `setup.sh` writes `SETUP_RELEASE` -- the release it belongs to -- into `.env`. A different agent tag in a file this release wrote was chosen, and `start.sh` keeps it, passing `--agent-tag` on when it re-runs `setup.sh` for another reason; `launch.sh` says which agent is pinned instead of warning. A file an older checkout wrote is still brought up to date.

### Updated
- `review/nl2sql_review/models.py`, `app.py`, `promote.py`; `review/gui/src/api/types.ts`, `components/StatusBar.tsx` -- `/v1/meta` no longer reports `limits.max_pair_number`, the status bar no longer shows "max Q99", and the two "golden set is full" paths are gone, since nothing can reach them.
- `agent/USAGE.md` -- the retry budget is seven generations (it said three retries and four attempts); stopping names all three databases, and `--profile '*'` for the rest.
- `USAGE_GUIDE.md` -- a chosen older agent runs with `start.sh`; the golden set has no size limit. `review/README.md` -- the id rule. `README.md` -- the `v5_5_1` tag, pull and publish commands, and when `start.sh` leaves `.env` alone.
- Tests: Q100 parsed by the loader, promoted after Q99 end to end and offered by `/v1/golden`; the renderer and the loader held to one answer on what a pair id is; the help text checked against the parser for each switch and setting; a chosen agent tag kept by `start.sh`, carried through a re-run of `setup.sh`, and said rather than warned by `launch.sh`, while one an older checkout wrote is still re-pinned.
- Version 5.5.1 in every declaration; `setup.sh` pins `v5_5_1`.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-console-gui`, `nl2sql-mlflow`, `nl2sql-mlflowdb` `:v5_5_1` (amd64, arm64); `nl2sql-desktop-build:v5_5_1-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-02 UTC). Checked after the push: the agent and review images run 5.5.1 on both architectures, with the corrected help and `Q100` after `Q99`; the review interface's bundle no longer shows a maximum id; every desktop jar is 5.5.1 with its platform's native code; MLflow's two are their bases' layers exactly.

### After publishing
In the checkout, not in the `v5_5_1` images: the dataset images, the
scripts, compose, tests and documentation. No app image changed.
- `nl2sql-rag-chunkdb`, `nl2sql-rag-vectordb` `:v3_2` (amd64, arm64; 2026-10-02 UTC) -- `v3_1` with the golden set brought up to the question document: 48 pairs where `v3_1` has 45. Made by running the two golden-pair loaders in the `v5_5_1` review image against a copy of `v3_1`, then `rag/publish_db_image.sh --from` that copy. Every knowledge table hashed the same before and after, and the stored knowledge file hashes match `knowledge/`; both architectures were checked against the source table by table, with the roles, a network login and the HNSW nearest neighbours. `setup.sh` and compose's defaults pin `v3_2`.
- `launch.sh --load-golden`, `start.sh --load-golden` -- load `context_questions/translated_questions.md` into the context store and its vectors before anything is asked: the loaders a promotion runs, in a one-off review-image container with nothing else of the review system started. `start.sh` pins the review image first when it is not, rather than letting compose build it. A failed load is a warning and the stack comes up as it was.
- `launch.sh` -- compares the context store's golden pairs with the document's on every start, and names `--load-golden` when they differ: the images ship the set as it was when they were published, and only a promotion reloads it.
- `launch.sh`, `start.sh` -- two commands they print when a page does not come up, `docker compose --profile reviewgui logs reviewgui` and `--profile consolegui logs consolegui`, failed outright: each service depends on one behind another profile, and compose rejects a project with a dependency left undefined. They name the profiles they need now. `tests/docker/test_compose_config.py` resolves every `docker compose --profile ...` in a tracked script or document against the real compose file; the script tests' fake `docker` accepts anything, which is how these, and `--load-golden`'s first form, passed them -- a live run found it.
- `docker-compose.yml` -- the review service embeds with `EMBED_BASE_URL`, the agent's embedding host, where it read an `OLLAMA_URL` nothing set: with `setup.sh --embed-url`, promotions embedded against this machine while the agent queried another.
- Tests: the load run in the review image before the stores are counted, starting nothing else, failing as a warning, and doing nothing under `--no-rag`; the store-against-document warning and its silence when they agree; `start.sh` handing the flag on and pinning the review image; the review service's embedding host and model held to the agent's.
- `README.md`, `USAGE_GUIDE.md`, `review/README.md`, `rag/README.md` -- `--load-golden`, the `v3_2` images and how they were made; descriptions of the live golden set no longer give it a count.

## v5_5 (5.5.0) -- 2026-10-01

The agent and its subagents are traced in MLflow. Each question is one trace
shaped like the architecture: a span per agent -- the Supervisor, the four
retrievers, the Context Aggregator, the SQL Generator, the Static Validator
and Planner Gate, the Safe Executor, the Completeness Reviewer, the Repair
Agent, the Visual Formatter, the Insight Narrator and the Audit Checker --
holding the state it read and the update it wrote, and inside each the model
calls it made, with their messages, answers and tokens and the task, rung and
route the Model Router chose them by. MLflow comes up in compose with
`--mlflow`: MLflow's own server and a Postgres of its own, each a thin image
built here and published with the release. A verdict
given in the web or desktop interface is recorded on the trace it judges, and
the benchmark files each configuration as an MLflow run holding its
questions' traces. Tracing is best effort: with no server, or one that does
not answer, the agent answers untraced and asks again thirty seconds later.
Of the ten images v5.4 published, only the agent's changed; the others are
v5.4's under the new version.

### Created
- `agent/nl2sql_agent/tracing.py` -- the `Tracer`: connects on first use (a health check, then the tracking URI and experiment, never importing MLflow before a server answers); a trace per run, tagged with its outcome, screening, attempts, model calls, rows, version, entrypoint and job; a span per agent and per model call; verdicts as human feedback on the trace, overridden when given again and deleted when withdrawn; a forgotten job's trace found by its tag; MLflow's HTTP retries bounded, which otherwise held the CLI's exit four minutes when the server had gone.
- `docker/mlflow/Dockerfile` -- MLflow's own server image (`ghcr.io/mlflow/mlflow:v3.16.1-full`, the variant with a Postgres driver), labelled and nothing added: how it is served stays in compose.
- `docker/mlflowdb/Dockerfile` -- stock Postgres for MLflow's store, labelled likewise.
- `benchmarks/tracking.py` -- a run per configuration: its settings as parameters, accuracy overall and per category, timings per stage and rungs as metrics, the report as `benchmark.json`, and each question's trace tagged with the question and scored `benchmark_correct`.
- `tests/fake_mlflow.py` -- the slice of MLflow the agent and the benchmark call, in memory, with and without the runs API.
- `tests/agent/test_tracing.py`, `tests/agent/test_graph_tracing.py`, `tests/benchmarks/test_tracking.py`, `tests/docker/test_mlflow_compose.py`, and `tests/docker/test_mlflow_live.py` -- the last against a real server from the pinned image, on a private network under the name `setup.sh` writes, and through the agent image's own client.

### Updated
- `agent/nl2sql_agent/graph.py` -- `TRACE_SPANS` beside `STEP_LABELS`: each node's agent name, span type and the state it reads. `_traced` opens the agent's span, `run` opens the run's trace and puts its id on the state, and v3's table-selection call is traced.
- `agent/nl2sql_agent/router.py` -- every model a routed call asks is a `CHAT_MODEL` span, so a fallback shows as two.
- `agent/nl2sql_agent/state.py` -- `trace_id`.
- `agent/nl2sql_agent/config.py` -- `MLFLOW_TRACKING_URI` (unset: nothing traced) and `MLFLOW_EXPERIMENT_NAME` (`nl2sql-agent`).
- `agent/nl2sql_agent/api/app.py`, `api/jobs.py` -- one tracer for the server, shared by its agent and its feedback routes; each job's run tagged with the job's id; a verdict recorded on the trace after the staging database has taken it, and taken off when withdrawn.
- `agent/nl2sql_agent/__main__.py` -- the CLI says where traces go, tags its runs, and `--json` carries `trace_id`.
- `agent/requirements.txt` -- `mlflow-tracing==3.16.1`; `tests/requirements.txt` -- `mlflow-skinny==3.16.1`, for the benchmark's runs.
- `benchmarks/run_benchmark.py`, `benchmarks/runner.py` -- traced when MLflow answers on the host (`http://localhost:5001` unless `MLFLOW_TRACKING_URI` says otherwise), each question's `trace_id` in the report.
- `docker-compose.yml` -- `mlflowdb` (built from `docker/mlflowdb/Dockerfile`, its port unpublished, volume `mlflowdata`) and `mlflow` (built from `docker/mlflow/Dockerfile`, artifacts in `mlflowartifacts`, the rebinding guard given the agent's names for it, published on `127.0.0.1:5001` because macOS keeps 5000) behind the `mlflow` profile; the agent and the API forward the two settings.
- `launch.sh --mlflow`, `start.sh --mlflow` -- start MLflow after the stack, which does not wait on it, and open it in a window of its own; warn when `.env` names no tracking server or the interface is published beyond this machine. `start.sh` re-runs `setup.sh` when MLflow is asked for and not pinned.
- `setup.sh` -- writes `MLFLOW_TRACKING_URI=http://nl2sql-mlflow:5000` into `.env`, writing back a previous `.env`'s own value instead (empty is how tracing is turned off); `--mlflow` (and `--mlflow-image`, `--mlflow-tag`, `--mlflow-db-image`, `--mlflow-db-tag`) pulls and pins MLflow's two images.
- `README.md` (Tracing, the container and tag tables, test counts), `agent/README.md` (Tracing (MLflow), the two settings), `agent/API.md`, `benchmarks/README.md`.
- Version 5.5.0 in every declaration; `setup.sh` pins `v5_5`.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-console-gui` `:v5_5` (amd64, arm64); `nl2sql-desktop-build:v5_5-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-01 UTC).
- `nl2sql-mlflow`, `nl2sql-mlflowdb` `:v5_5` (amd64, arm64), their first publish (2026-10-01 UTC).

### After publishing
In the checkout, not in the `v5_5` images: the three scripts, tests and
documentation. No image changed, so no tag was needed.
- Every launch mode checked live against the v5_5 stack. `start.sh --review
  --console --mlflow` brought every service up healthy, and a question asked
  through the web interface's proxy was in MLflow as one trace of nineteen
  spans, tagged with its job. `start.sh --desktop` took the jar from
  `nl2sql-desktop-build:v5_5-mac-aarch64` and opened the client against the
  API.
- `launch.sh --review` -- the closing lines say a judgement can be taken back,
  5.4's *Back to pending* and *Delete*; `start.sh`'s line for the review page
  says so too.
- `setup.sh` -- the closing lines named two databases and it starts three:
  the context store was never named, and with `--no-rag` the vector store was
  named and never started. They list what was started now. The header
  comment still described the v2 agent.
- `start.sh --help` -- the flags combine, and `--review --console --mlflow`
  brings up every page.
- `tests/docker/test_setup_script.py` -- the closing-message test looked for
  two container names the pull lines print anyway, so it passed while the
  message was wrong. A test that reads the list replaces it.
- `tests/docker/test_mlflow_compose.py` -- `MLFLOW_WORKERS` and
  `MLFLOW_ALLOWED_HOSTS` are set through compose by a test.
- `tests/docs/test_docs.py` -- every setting MLflow's two services read is in
  the README with its default. `tests/docker/test_script_coverage.py` -- the
  README's count of shell scripts, Dockerfiles and the rest is held to
  `git ls-files`: it said eleven Dockerfiles, and 5.5 made thirteen.
- `README.md` -- MLflow's server settings, the command that brings up every
  page, the file, command and test counts. `agent/README.md` -- what
  `setup.sh` starts. `agent/USAGE.md` -- how MLflow is started.
- `USAGE_GUIDE.md` (new) -- using every part of the stack, task by task:
  first run against your own Ollama host, the three scripts and every flag
  they take, where each page and port is, asking in each interface, reading
  an answer, feedback and review, the SQL console, MLflow, the benchmark,
  models, configuration, security, upgrading and troubleshooting.
  `QUICKSTART.md` (new) -- from a fresh clone to a first answer. `README.md`
  points to both.
- `tests/docs/test_docs.py` -- the two guides are held to the scripts, the
  agent and compose: every flag of the three scripts and the agent is in the
  usage guide, every command either guide shows takes only flags that exist,
  every local address is one compose publishes, every setting that moves a
  port is named, and every section link lands on a heading.

## v5_4 (5.4.0) -- 2026-09-30

A judgement in the review interface can be taken back. Every submission can
be put back to pending or deleted, and when it had been acted on, what it
produced comes out with it: a promoted pair is taken back out of
`context_questions/translated_questions.md` -- checked by the loader's own
parser to have lost exactly that pair and changed no other, the previous
version kept beside it, both stores reloaded -- and a fix is deleted from the
corrections or completions store with its vector. The queue and what it
produced are never allowed to disagree, so a reopened question cannot be
promoted into the golden set twice. A reopened submission keeps its work: the
pair comes back as its draft, the corrected SQL comes back to the query
editor. The agent, the web interface, the desktop client and the console are
v5.3's.

### Created
- `review/gui/src/components/RecordEditor.tsx` -- "Change this review": back to pending, and delete; anything that reaches past the staging table asks first, in the words of the file or store it changes.
- `review/gui/src/components/Withdrawn.tsx` -- what reopening or deleting did: the pair or fix that came out, the counts, the backup and whether the stores caught up.

### Updated
- `review/nl2sql_review/app.py`:
  - `POST /v1/submissions/{id}/reopen` and `DELETE /v1/submissions/{id}`, each taking a promoted pair or a stored fix back out before the row changes, so a failure changes nothing and a failure after it heals on the next attempt;
  - `already_pending` and `not_withdrawable`; the `already_promoted` and `already_fixed` refusals say how to change one.
- `review/nl2sql_review/promote.py` -- `withdraw`: promotion in reverse, round-tripped through the loader's parser with every remaining pair compared field by field; the pair as the document held it, as a draft.
- `review/nl2sql_review/render.py` -- `remove_pair`: the block from its heading to the next, with a suite heading left empty by it; undoes `append_pair` exactly.
- `review/nl2sql_review/store.py` -- `reopen` and `delete`, each clearing the submission's promotion log entries in the same transaction.
- `review/nl2sql_review/corrections.py` -- `delete_by_submission`, returning the fix as it was; its vector goes by the existing cascade.
- `review/nl2sql_review/models.py` -- `WithdrawalModel`, `UndoModel`; `review/gui/src/api/types.ts` mirrors them.
- `review/gui/src/App.tsx`, `api/client.ts`, `styles.css` -- the editor under every submission, `reopen` and `remove`, a reopened fix's SQL seeded into the query editor.
- `tests/review/` -- reopen and delete over HTTP, end to end through the real promoter and withdrawal on a copy of the document; `remove_pair` on every layout the document has; `withdraw` refusing what the parser will not accept; both live against Postgres -- the promotion log cleared, a reopened row handed back to the public process, a fix deleted with its vector.
- `review/README.md`, `README.md` -- changing your mind, the two routes and their errors, a `v5_4` row in the tag table, test counts.
- Version 5.4.0 in every declaration; `setup.sh` pins `v5_4`.

### Fixed
- Twenty-four tests pinned the golden set at 45 pairs, with Q46 next, while reading the live document -- so they failed for the first person who promoted anything through the review interface: the review service's (`test_app.py`, `test_promote.py`, `test_render.py`), the RAG loaders' (`test_golden_pairs_parser.py`, `test_pipeline_cli.py`), the agent's live store test and the compose retrieval probe. The count, the suites and the next id are read from the document now.
- `test_golden_pairs_parser.py` took a SQL block not ending in `;` as truncated. Every hand-written pair ends in one and no promoted pair does -- the agent strips them -- so it compares each pair's SQL with its whole fenced block instead.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui`, `nl2sql-console-gui` `:v5_4` (amd64, arm64); `nl2sql-desktop-build:v5_4-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-10-01 UTC).

### After publishing
In the checkout, not in the `v5_4` images: tests and documentation, and in
`agent/` and `review/` nothing but comments and an `except` that re-raised,
so no behaviour changed and no tag was needed.
- Coverage exclusions: the README said one statement was excluded, and eight
  were. Seven `# pragma: no cover` lines came out of
  `review/nl2sql_review/app.py`, `promote.py`,
  `agent/nl2sql_agent/completeness.py` and `api/feedback.py`. Six paths are
  tested now: a submission deleted by another reviewer while it was being
  judged or reopened, a loader whose interpreter cannot be started, the
  feedback sink's real connection, and pglast failing to print or walk a
  statement it parsed. The seventh was `except ModuleNotFoundError: raise`,
  which did nothing, and was removed.
- `tests/docs/test_docs.py` -- the check that every agent setting is
  documented matched two of `config.py`'s readers and saw 32 of its 60
  settings. It reads all of them now, and is held to every upper-case name
  `config.py` passes to a call. `rag/README.md`'s test count is checked.
- `tests/docker/test_compose_config.py` -- the agent's compose settings are
  checked in the other direction too, as every other service's already were.
- `tests/docker/test_launch_script.py`, `test_setup_script.py` -- the review
  interface's proxy test asserts the request it is named for; a test whose
  only assertion another test makes is removed.
- `tests/review/test_app.py` -- a row deleted mid-request is deleted, so the
  fake repository answers as the real one would, rather than the method being
  replaced with one that returns nothing.
- `README.md`, `rag/README.md` -- the console interface's suite in Coverage,
  the review interface's 155 tests where it still said 128, `rag/`'s 281 where
  it said 265, the test counts and this pass.

---

## v5_3 (5.3.0) -- 2026-09-28

The SQL console: the retail database, queried the way the agent queries it,
for working out why an answer was wrong. A third process from the agent's
image, `python -m nl2sql_agent.console`, runs a query as the agent's read-only
role, in a read-only transaction, under the agent's statement timeout,
through the agent's own static validator and planner gate -- and says beside
the rows or the plan which gate would have refused it, in that gate's words,
against which limit. A third React/TypeScript interface, served by its own
nginx on this machine only, puts in one page the schema as the agent's
introspection reads it, the block of the agent's prompt for each table, three
ways to run a query -- Run, Plan, Analyze -- and the queries run before.
`start.sh --console`, `launch.sh --console` and `setup.sh --console` bring it
up. The pipeline is v5.2's.

### Created
- `agent/nl2sql_agent/console/` -- the SQL console:
  - `query.py` -- the Inspector: the agent's validator twice (for safety, then with the whole schema as scope), `EXPLAIN` judged by the planner gate's own functions, the query read through a server-side cursor inside `READ ONLY` and the agent's timeout, and the verdict; the `plan` and `analyze` modes;
  - `app.py`, `models.py` -- `/v1/meta`, `/v1/schema`, `/v1/schema/{table}/prompt` and `POST /v1/query`, with the API's error envelope and readiness;
  - `settings.py`, `server.py`, `__main__.py` -- `CONSOLE_*`, the six agent settings it runs under, the API's certificate presented rather than generated, and the banner.
- `console/` -- the interface (React/TypeScript), a separate npm project and image from the other two:
  - `App.tsx`, `SchemaBrowser`, `SqlEditor`, `Verdict`, `ResultTable`, `PlanView`, `PromptView`, `History`, `StatusBar`;
  - `api/client.ts`, `api/types.ts`, `api/plan.ts`, `api/format.ts`, `api/history.ts`;
  - `Dockerfile`, `nginx.conf.template`, `10-nl2sql-console-config.envsh`, `README.md`, and its own suite in `console/test/`.
- Tests: `tests/console/` -- `test_query.py`, `test_app.py`, `test_settings.py`, `test_server.py`, `test_console_live.py`, `test_console_compose.py`, `test_console_container.py`, `test_console_project.py`, `test_console_gui_contract.py`, `test_console_gui_suite.py`.

### Updated
- `agent/nl2sql_agent/database.py`, `graph.py` -- the planner gate's cost judgement moved into `plan_cost_problem` and `total_cost` made public, so the gate and the console read one function; `Database.engine` for a caller that runs statements of its own. The agent's behaviour is unchanged.
- `docker-compose.yml`:
  - `console` and `consolegui` services (profiles `console`, `consolegui`), their ports published on `127.0.0.1` unless `CONSOLE_BIND_ADDRESS` says otherwise;
  - the agent's `DATABASE_URL` and `MAX_PLAN_COST` anchored, so the console reads the same values;
  - `nl2sql-console` in `API_TLS_HOSTNAMES`.
- `start.sh`, `launch.sh`, `setup.sh` -- `--console`, and `setup.sh --console-gui-image` and `--console-gui-tag`. `launch.sh`'s proxy repair is given the container's name rather than deriving it, which only worked for the first two interfaces.
- `tests/docker/` -- the fake `docker` answers for the console's containers; the three scripts' `--console` paths; the inventories name the console's Dockerfile, services and start-up script; `test_published_images.py` asks for the console's interface too.
- `tests/docs/` -- `console/README.md` held to the console's settings, defaults, routes, error codes and flags; the new project's version declarations and lockfile.
- `.gitignore`, `.dockerignore` -- the console's `node_modules`, `dist` and `coverage`.
- `README.md`, `agent/USAGE.md`, `gui/README.md`, `desktop/README.md` -- the SQL console, a `v5_3` row in the tag table, the tags, test counts and coverage.
- Version 5.3.0 in every declaration; `setup.sh` pins `v5_3`.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui` `:v5_3`; `nl2sql-console-gui:v5_3` -- first publish; all amd64 and arm64. `nl2sql-desktop-build:v5_3-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-09-29 UTC).

---

## v5_2 (5.2.0) -- 2026-09-28

arch5.2: model routing. Every agent that calls a model -- the Supervisor, the
SQL Generator, the Completeness Reviewer's reflection, the Insight Narrator
and the Repair Agent's diagnosis -- asks for one by task and rung (light,
standard or heavy, computed from the pipeline's own state), and gets the
fastest model on the Ollama host that calibration measured to be suited to
that task at that rung. A repair climbs the ladder; a routed model that
cannot answer falls back to `OLLAMA_MODEL`. The catalog it routes from is
built by a scanner that takes any Ollama host by its address, and measured by
a calibrator that runs each model through a probe per task. With no catalog
for its host the agent behaves as v5.1. `start.sh` now starts what the stack
runs on -- Docker, and the Ollama on this machine -- brings a `.env` that
predates the checkout up to date, and opens the review page in a browser
window of its own; `launch.sh` says which models the calls will go to. Six departures from the spec, each
found by running it, are recorded in `agent/README.md`: the context window
is per model, the generator's score is read from the answer contract, a
catalog of another host is ignored rather than refused, the Supervisor and
the generator must match the reference on every probe, a rung needs five
probes before its score counts, and a near worked example is judged by its
question's similarity, not the fused score.

### Created
- `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_2.{md,drawio,png}`, `make_arch5_2_drawio.py` -- the spec: arch5.1 plus section 15, model routing.
- `agent/nl2sql_agent/router.py` -- the Model Router: the catalog read and checked against the host, the routing table (suited candidates, speed order, at most `MODEL_MAX_LOADED` models, one per behaviour fingerprint, pins), and the fallback chain each call runs down.
- `agent/nl2sql_agent/complexity.py` -- the rung of every model call: the generator's score, the ladder, the Supervisor's pre-screen, the reflection's, narrator's and diagnosis's rules.
- `models/build_catalog.py` -- the catalog scanner: any Ollama host by address, `/api/tags` and `/api/show` per model, the library page, a prior per task and rung, a behaviour fingerprint, and measurements kept across a rebuild for unchanged weights. Standard library only.
- `models/calibrate.py` -- calibration: a probe per task, per rung, against the reference model; twins measured once, early stop, failures contained, `--resume`.
- `models/probes/triage.json`, `models/probes/repair.json` -- the Supervisor's and the Repair Agent's probes.
- `models/catalog.json` -- the development host's catalog, calibrated.
- `models/README.md` -- the catalog, the prior's rules, calibration.
- `Ollama_Modelfiles/` -- the Modelfiles for the development host's local builds with larger context windows.
- `arch_diagrams/arch_v5_2.{svg,png,tif}` -- the v5.2 diagram.
- `tests/models/` -- the scanner against a snapshot of a real host and library (`fixtures/`), the calibrator against fake models, and the live host and library (`test_build_catalog_live.py`).
- `tests/agent/test_router.py`, `tests/agent/test_complexity.py`, `tests/agent/test_graph_routing.py` -- the router, the rung rules, and routing inside the pipeline.

### Updated
- `agent/nl2sql_agent/graph.py` -- every model call routed; the Context Aggregator scores the generator's task; each repair sets the next generation's rung.
- `agent/nl2sql_agent/state.py` -- `complexity`, `generation_rung`, `rung_holds`; each trace entry names its `model`, `rung`, `route` and `hops`.
- `agent/nl2sql_agent/config.py` -- `MODEL_ROUTING_ENABLED`, `MODEL_CATALOG`, `MODEL_ROUTE_ON_PRIOR`, `MODEL_ROUTE_<TASK>`, `MODEL_MAX_LOADED`, `MODEL_NUM_CTX`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_NUM_PREDICT`, `OLLAMA_TIMEOUT`.
- `agent/nl2sql_agent/llm.py` -- a client per routed model; the keep-alive, token cap and timeout on every client.
- `agent/nl2sql_agent/examples.py`, `agent/nl2sql_agent/tools.py` -- each retrieved pair carries its question similarity.
- `agent/nl2sql_agent/__main__.py` -- the routing table at startup; a routing configuration that cannot be used is an error, not a traceback.
- `agent/nl2sql_agent/api/models.py`, `api/app.py` -- `/v1/meta` reports the routing table; `gui/src/api/types.ts` and the desktop client's `Models.java` mirror the field.
- `benchmarks/runner.py`, `benchmarks/run_benchmark.py` -- accuracy and P50 per agent, rung and model, and the rung distribution.
- `docker-compose.yml` -- the routing settings forwarded; the catalog mounted read-only into the agent and the API.
- `arch_diagrams/generate.py` -- the v5.2 builder; step tags that wrap, for it alone.
- `.coveragerc` -- `models/` measured.
- `README.md`, `agent/README.md`, `agent/USAGE.md`, `agent/API.md`, `benchmarks/README.md` -- model routing, the catalog and calibration, the new settings, a `v5_2` row in the tag table, coverage and test counts.
- `start.sh` -- starts Docker Desktop when its daemon is down (`open -a Docker`
  on macOS, the `docker-desktop` user service on Linux) and waits for it;
  starts the Ollama on this machine when the embedding model is served from
  here and nothing answers, and pulls that model into it when it is missing;
  re-runs `setup.sh` when `.env` pins an older agent than the checkout ships,
  or never pinned the interface asked for; and opens the review page in a
  window of its own, asking the default browser directly -- Safari through
  AppleScript, Firefox, Chrome and the Chromium browsers with their own
  new-window flag -- and falling back to the generic opener.
- `launch.sh` -- asks the agent image for the routing table it will build, and
  prints how many models the calls are shared between or why every one goes
  to `OLLAMA_MODEL`; a catalog the agent cannot read is a warning.
- `setup.sh` -- keeps every setting of the previous `.env` it does not write
  itself.
- `tests/docker/` -- fakes for `systemctl`, `ollama`, `defaults`, `osascript`,
  `xdg-settings` and the browsers' own commands; a fake `docker compose
  config` that leaves out a service whose profile is not named, as the real
  one does; and each script's helper run against the real compose file.
- `tests/review/test_review_compose.py` -- the review service's settings and
  its proxy's checked against compose in both directions, as the agent's, the
  API's and the GUI's already were.
- `tests/` -- measured with themselves in the report: a structured call that
  raises, a calibration model that is down and an embedding host answering 500
  each given a test; seven tests that repeated another's scenario and
  assertions folded into it; a promotion test that only ever saw three of the
  six mechanics removed in favour of the one that sees all six; the smoke
  script's array check, which matched nothing, made to check every array the
  script expands; an unused container helper, a fake's unused failure mode, a
  guard that could never fire and unused imports removed.
- `data_gen/datagen/facts.py`, `config.py` -- an unused assignment and an
  unused import removed; the dataset is unchanged.
- Version 5.2.0 in every declaration; `setup.sh` pins `v5_2`.

### Fixed
- A model call had neither an output cap nor a timeout, so a model that degenerated could generate without end -- Ollama shifts a full window rather than stopping -- and hold its question with it. Found when a calibration probe ran for over an hour; every client now carries `OLLAMA_NUM_PREDICT` and `OLLAMA_TIMEOUT`.
- `launch.sh` and `setup.sh` read the agent's settings from `docker compose
  config` without naming the agent's profile, so the agent was left out, the
  read found nothing, and both checked the default chat host and model
  whatever `.env` said. Present since the first `launch.sh`, and hidden by the
  tests' fake `docker`, which answered for the agent whatever profile was
  named.
- Re-running `setup.sh` moved any setting it does not write itself -- an
  `API_TOKEN`, a port -- into `.env.bak` and left it out of the new `.env`.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui` `:v5_2` (amd64, arm64); `nl2sql-desktop-build:v5_2-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-09-28 UTC).

---

## v5_1_2 (5.1.2) -- 2026-09-26

A coverage and relevance pass. Branch coverage was switched on for the Python
code and gated at 100%; the conditions it found only ever tested one way were
either tested, deleted as unreachable, or -- once -- fixed. The tests were
measured the same way, and dead test code came out. No behaviour of the agent
changes; the images are republished so the published code matches the
repository.

### Created
- `CHANGELOG.md`, `CHANGELOG_SIMPLE.md` -- this file and its one-line companion.
- `tests/docs/test_versions.py` -- the changelogs:
  - both open with the version `setup.sh` pins;
  - both run newest first, back to v1;
  - both list the same versions;
  - neither misses an agent tag the README lists.
- Tests, 24 new, for the conditions branch coverage found untested:
  - `tests/agent/test_cli.py`: the interactive banner with worked examples switched off.
  - `tests/agent/test_present.py`: a NULL in a cited row, count columns with no negatives, a result with no columns.
  - `tests/agent/test_validate.py`: a denylisted function called twice; the pglast slot layout the walker relies on.
  - `tests/api/test_app.py`, `tests/review/test_app.py`: services configured with no CORS origins.
  - `tests/api/test_jobs.py`: an event stream with no deadline.
  - `tests/review/test_app.py`, `tests/review/test_render.py`: meta entries the parser does read.
  - `tests/api/test_server.py`, `tests/review/test_settings.py`, `tests/data_gen/test_generate_data_cli.py`, `tests/docker/test_dockerfiles.py`: the four entry points imported rather than run.
  - `tests/rag/test_semantic_chunker.py`: the review image's layout, with `rag/ragproc` and no `chunking/`.
  - `tests/benchmarks/test_run_benchmark.py`, `tests/benchmarks/test_runner.py`: an environment that overrides a host default, a quiet run, a trace entry with no node.
  - `tests/docs/test_arch_diagrams.py`: the generator's options no published diagram uses.

### Updated
- `.coveragerc` -- `branch = true` and `fail_under = 100`: `coverage report` fails below full statement and branch coverage.
- `agent/nl2sql_agent/validate.py` -- the SQL walker no longer guards against a slot it can never meet.
- `agent/nl2sql_agent/database.py` -- `Database.explain` removed: the v3 validator's wrapper, superseded by `explain_plan` and called only by its own tests.
- `chunking/semantic_chunker.py`, `rag/ragproc/chunker.py` -- four guards that could never be false removed.
- Test code removed:
  - the `FakeDatabase.explain` fake and the scripted model's unused exception and list modes (`tests/agent/conftest.py`);
  - the fake repository's `get_by_job` (`tests/review/conftest.py`);
  - the RAG fake `docker`'s `image` case (`tests/rag/test_rag_scripts.py`);
  - the two tests of `Database.explain` (`tests/agent/test_database_live.py`).
- `README.md`:
  - the coverage section (branch standard, totals, what the pass found), the shell-coverage figure and test counts;
  - the embedding-host recount command, which named the wrong variable;
  - a `v5_1_2` row in the tag table, and links to both changelogs.
- Version 5.1.2 in every declaration; `setup.sh` pins `v5_1_2`.

### Fixed
- `arch_diagrams/generate.py` -- a pipeline row of an unknown kind fell through the dispatch and was drawn as a copy of the row above it, at its height; it now raises.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui` `:v5_1_2` (amd64, arm64); `nl2sql-desktop-build:v5_1_2-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-09-27 UTC).

---

## v5_1_1 (5.1.1) -- 2026-09-26

A correction to 5.1: the narrator was shown the result rows HTML-escaped and
copied what it read, so a name with `&` came back as an entity.

### Updated
- `agent/API.md` -- says which answer fields are markdown (`answer`) and which are plain text (`narrative`, `claims[].text`).
- `gui/src/api/text.ts`, `gui/src/components/AnswerView.tsx`, `review/gui/src/api/text.ts`, `desktop/.../ui/AnswerView.java`, `desktop/.../ui/Markup.java` -- no longer describe the narrative as escaped. They still unescape it: that is still right against an older server and for review submissions staged before the fix.
- `tests/agent/test_present.py` -- a regression test with a narrator that copies names out of the table it is shown; a test that had pinned the escaped prompt as intended now pins the opposite.
- Version 5.1.1 in every declaration; `setup.sh` pins `v5_1_1`.

### Fixed
- `agent/nl2sql_agent/present.py`:
  - **Before:** the narrator's table used the escaping meant for output (`_markdown_cell`), so claims and the narrative carried `&amp;`, and the markdown answer escaped them again into `&amp;amp;`. The CLI printed `Meat &amp; Seafood`.
  - **After:** the narrator reads cells as the database holds them (`_prompt_cell`), and the answer is escaped once, on the way out.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui` `:v5_1_1` (amd64, arm64); `nl2sql-desktop-build:v5_1_1-*`, five platforms (2026-09-27 UTC).

---

## v5_1 (5.1.0) -- 2026-09-26

arch5.1: the review service handles each verdict its own way. A correct answer
is promoted into the golden set as before. A wrong or correct-but-incomplete
one is fixed: the reviewer writes the SQL that should have been generated,
validates it against the live database, and it goes into a store of its own.
The agent itself is unchanged. The dataset images also moved in this period:
the read-only role was tightened, and both RAG stores became multi-arch.

### Created
- Review service:
  - `review/nl2sql_review/validation.py` -- a reviewer's SQL run as `nl2sql_reader`, read-only, under a timeout and a row cap, with Postgres's own message and hint reported back.
  - `review/nl2sql_review/corrections.py` -- the corrections and completions stores: records plus bge-m3 embeddings, with ids `W####` / `I####`.
  - Routes `POST /v1/submissions/{id}/validate`, `POST /v1/submissions/{id}/fix` and `GET /v1/fixes/{kind}`; a `corrected` staging state.
- Review interface:
  - `review/gui/src/components/FixEditor.tsx`, `Fixed.tsx` -- the query editor with its validate-then-save flow, and the record of a stored fix.
  - `review/gui/src/api/text.ts` -- plain-text display of the agent's escaped prose, and noun agreement in counts.
- `docker-compose.yml` -- services `correctionsdb` (port 5436) and `completionsdb` (port 5437), stock pgvector, each with its own volume.
- `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_1.{md,drawio,png}`, `make_arch5_1_drawio.py` -- the spec, with section 14 on human review.
- `arch_diagrams/arch_v5_1.{svg,png,tif}` -- the v5 pipeline with the review side added to the deployment.
- `rag/docker/restore.Dockerfile` -- restores a dump into a cluster initialised on each build platform.
- Tests:
  - `tests/review/test_validation.py`, `tests/review/test_corrections_live.py`;
  - least-access tests in `tests/agent/test_least_privilege_live.py`, `tests/review/test_validation.py` and `tests/review/test_review_compose.py`.

### Updated
- Review service (`review/nl2sql_review/app.py`, `models.py`, `store.py`, `server.py`, `settings.py`) -- one workflow per verdict; `promote` refuses the other two.
- Review interface (`App.tsx`, `Queue.tsx`, `StatusBar.tsx`, `Original.tsx`, `api/client.ts`, `api/types.ts`, `styles.css`) -- one pane per verdict.
- `launch.sh` -- starts and waits on both fix stores before the review service. `start.sh` -- help and summary text.
- `docker/reader_role.sql` -- revokes PUBLIC's `CONNECT` on every other database, and `pg_cancel_backend`/`pg_terminate_backend`, in the retail database.
- `rag/publish_db_image.sh`:
  - `pg_dumpall` plus a restore per platform in one `docker buildx build`, replacing the tar of an arm64 data directory;
  - `--from IMAGE` republishes an already-published tag.
- `setup.sh` -- pins `v5_1`, then `retail-postgres:v1_1` and `rag-*:v3_1`; `docker-compose.yml` defaults follow.
- `tests/docker/test_published_images.py` -- also checks that every pinned dataset image is multi-arch.
- `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5.md` -- one status line (built as of 5.0.0).
- Docs: `README.md`, `review/README.md`, `rag/README.md`, `agent/API.md`, `agent/README.md`, `gui/README.md`, `desktop/README.md`.

### Fixed
- `docker/reader_role.sql`:
  - **Before:** any `nl2sql_reader` session could cancel or kill another -- the agent API, each compose run and review validations all share the role -- and the role could connect to `postgres` and `template1`.
  - **After:** neither is possible.
- `rag-vectordb:v3`, `rag-chunkdb:v3` were arm64 only and could not be pulled on amd64; `v3_1` restores the same data for both architectures.
- `review/gui` -- showed `Thrift &amp; Table` in answers and "1 corrections" in the status bar.
- `tests/docker/test_compose_rag_integration.py` -- built the agent through compose with `.env`'s image name, overwriting the local copy of the published tag with a source build; it now builds under a name of its own.

### Updated (removed)
- `rag/docker/seeded.Dockerfile` -- replaced by `restore.Dockerfile`.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui` `:v5_1`; `nl2sql-desktop-build:v5_1-*` (2026-09-26 UTC).
- Dataset images:
  - `nl2sql-retail-postgres:v1_1` -- the same data as `v1`, verified table by table, with the tightened read-only role built in. `latest` moved to it.
  - `nl2sql-rag-vectordb:v3_1`, `nl2sql-rag-chunkdb:v3_1` -- `v3`'s data, multi-arch, verified against `v3` on both architectures by schema, table checksums and nearest-neighbour results (2026-09-27 UTC).

---

## v5 (5.0.0) -- 2026-09-26

arch5: a correct answer must also be a complete one. "Top 10 SKUs" had come
back as ten `sku_id` values -- right, and useless without a second query.

### Created
- `agent/nl2sql_agent/contract.py`:
  - the answer contract: the label beside every id, read from the catalog's key constraints; the measure a ranking was ranked by; the latest complete fiscal year when the question names no period;
  - the rendered contract line the generator sees before it writes.
- `agent/nl2sql_agent/completeness.py` -- the Completeness Reviewer:
  - rules R1-R4, plus one reflection that may only add columns of dimensions the rows already identify;
  - a guard against returning the same result twice;
  - the `review` node, the eighteenth in the graph.
- `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5.{md,drawio,png}`, `make_arch5_drawio.py` -- the spec (added the day before, in #29).
- `arch_diagrams/arch_v5.{svg,png,tif}`.
- Tests:
  - `tests/agent/test_contract.py`, `tests/agent/test_completeness.py`, `tests/agent/test_completeness_live.py`;
  - `tests/docker/test_published_images.py`, which asks Docker Hub whether every tag `setup.sh` pins is published, for both architectures, at this version.

### Updated
- `agent/nl2sql_agent/graph.py`, `config.py`, `state.py`, `supervisor.py`, `prompts.py`, `present.py`, `repair.py`, `database.py`:
  - the review stage, and a retry budget of seven generations (`MAX_ATTEMPTS`, up from four);
  - the Supervisor reads what an answer is about;
  - assumptions the pipeline made are stated by the narrator and checked by the audit.
- A third verdict, "correct but incomplete" (wire value `incomplete`):
  - `agent/nl2sql_agent/api/models.py`, `gui/src/components/FeedbackBar.tsx`, `History.tsx`, `desktop/.../ui/FeedbackBar.java`, `HistoryView.java`, `api/Models.java`, `feedback/FeedbackStore.java`;
  - the review queue. The staging table's CHECK is re-applied on start.
- `benchmarks/README.md`, `agent/README.md` -- the v5 benchmark: 15/15, and what the first three runs found.
- `arch_diagrams/generate.py`, `README.md`, `agent/API.md`, `agent/USAGE.md`, `gui/README.md`, `desktop/README.md`, `review/README.md`.
- Version 5.0.0 in every declaration; `setup.sh` pins `v5`.

### Fixed
Three rules of the contract line, each found by a benchmark run before the one that was published:
- a "how many" question names no entity -- B01 had been answered with ten named stores;
- a measure the question names is never restated -- B13's ratio had been paraphrased into a difference, and computed as one;
- any period brings the calendar into scope -- B11 had lost a draft to the table allowlist.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui` `:v5`; `nl2sql-desktop-build:v5-*` (2026-09-26 UTC).

---

## v4_5_1 -- 2026-09-25 -- *unpublished*

### Fixed
- `desktop/` -- successive layout defects in the desktop client, found after
  `v4_5` was published and pushed four times over the five `v4_5-*` desktop tags.

### Updated
- Release policy: a published tag does not move, and the next correction was to
  be `v4_5_1`. The next release was 5.0.0 instead, and the rule has held since.

---

## v4_5 (4.5.0) -- 2026-09-25

The Java desktop client: the same questions, answers, charts and verdicts as
the web interface, in a window.

### Created
- `desktop/` -- a JavaFX 21 client (`pom.xml`, `Dockerfile`, `README.md`):
  - `Main.java`, `Settings.java`;
  - `api/` -- `HttpApiClient`, `ApiClient`, `EventStream`, `JobWatcher`, `Tls`, `Json`, `Models`, `ApiException`;
  - `chart/` -- `ChartData`, `ChartKind`, `Values`;
  - `feedback/` -- `ApiSender`, `FeedbackStore`, `FeedbackRecord`, `Sender`, `SyncState`;
  - `ui/` -- `DesktopApp`, `MainWindow`, `AskBox`, `AnswerView`, `ChartView`, `ResultTableView`, `ProgressView`, `HistoryView`, `FeedbackBar`, `StatusBar`, `Markup`, `Styles`;
  - `nl2sql.css`;
  - a 376-test suite (JUnit, headless through Monocle), held to 100% of lines and branches by JaCoCo.
- `nl2sql-desktop-build` images, one per JavaFX platform: the jar and nothing else.
- Tests:
  - `tests/java/test_desktop_contract.py`, `test_desktop_project.py`, `test_desktop_suite.py`;
  - `tests/docker/test_desktop_image.py`;
  - `tests/docs/test_versions.py`, which holds every version declaration to `__version__`.

### Updated
- `agent/nl2sql_agent/api/app.py`, `models.py` -- the two request limits the client needs, in `/v1/meta`.
- `launch.sh`, `start.sh` -- `--desktop`: fetch or build the jar for this machine, copy the API's certificate out, open the window.
- `docker-compose.yml` -- the `desktop` build service. `pytest.ini` -- the `java` marker.
- `gui/`, `review/` -- version and type updates. Docs throughout.

### Fixed
- `desktop/.../api/HttpApiClient.java` -- a 500 with an empty body was treated as success before the status was read, and parsed into a document of defaults.
- `tests/docker/test_script_coverage.py` -- the `desktop` compose service was examined by tests but named by no compose test, so the inventory missed it.

### Published
- `nl2sql-agent`, `nl2sql-gui`, `nl2sql-review`, `nl2sql-review-gui` `:v4_5`; `nl2sql-desktop-build:v4_5-{mac-aarch64,mac,linux,linux-aarch64,win}` (2026-09-25 UTC).

---

## v4_4 (4.4.0) -- 2026-09-24

The feedback system: a verdict given in the web interface is staged, reviewed,
and possibly promoted into the golden questions.

### Created
- `agent/nl2sql_agent/api/feedback.py` -- `POST`/`DELETE /v1/questions/{id}/feedback`:
  - the server takes the job's snapshot itself; the client sends only the verdict;
  - the write goes through `nl2sql_feedback_writer`, an insert-only role fenced by row-level security.
- `review/nl2sql_review/` -- the review service:
  - `app.py`, `server.py`, `settings.py`, `models.py`;
  - `store.py` -- the staging schema and the writer role, reset on every start;
  - `render.py` -- a golden pair rendered in the loader's format;
  - `promote.py` -- appends to `context_questions/translated_questions.md`, checked by the loader's own parser, then reloads both stores.
- `review/gui/` -- the review interface (React/TypeScript), a separate npm project from `gui/`: `App.tsx`, `Queue`, `Original`, `DraftEditor`, `Promoted`, `StatusBar`, `api/draft.ts`.
- `review/Dockerfile`, `review/gui/Dockerfile`, `review/README.md`, `review/requirements.txt`.
- `gui/src/feedback/apiStore.ts` -- verdicts sent to the API, with the browser copy kept.
- Tests:
  - `tests/review/` -- `test_app.py`, `test_promote.py`, `test_render.py`, `test_settings.py`, `test_store_live.py`, `test_review_compose.py`, `test_review_image.py`, `test_review_project.py`, `test_review_gui_contract.py`, `test_review_gui_suite.py`;
  - `tests/api/test_feedback.py`.

### Updated
- `docker-compose.yml` -- `feedbackdb`, `review` and `reviewgui` services (profiles `feedback`, `review`, `reviewgui`).
- `launch.sh`, `setup.sh`, `start.sh` -- `--feedback` and `--review`.
- `agent/nl2sql_agent/api/app.py`, `models.py`, `settings.py`, `tls.py` -- feedback routes, and `nl2sql-review` in the certificate's hostnames.
- `gui/` -- `App.tsx`, `api/client.ts`, `api/types.ts`, `feedback/store.ts`, `feedback/useFeedback.ts`.
- `.coveragerc` -- the review package. Docs throughout.

### Published
- `nl2sql-agent`, `nl2sql-gui` `:v4_4`; `nl2sql-review:v4_4`, `nl2sql-review-gui:v4_4` -- first publish (2026-09-25 UTC).

---

## v4_3 -- 2026-09-24 -- *unpublished*

A hardening pass on what the tests could not see.

### Created
- `tests/docker/test_init_db_script.py` -- `docker/init_db.sh` run against the stock Postgres image it is built on, with a two-row dataset. It asserts the rows, `pg_trgm`, and what the read-only role can and cannot do.

### Updated
- `tests/docker/test_script_coverage.py` -- the inventory extended from shell scripts to every Dockerfile and both compose files.
- `agent/nl2sql_agent/api/app.py` -- an unused `PUBLIC_PATHS` constant removed.
- `tests/docs/test_docs.py`, `tests/docker/test_setup_script.py`, `tests/shell_coverage.py`, `README.md`, `agent/API.md`.

### Fixed
- `docker/init_db.sh` was tracked, shell, and in no coverage list, so the measurement reported 100% of eleven scripts while a twelfth had never run.

---

## v4_2_1 -- 2026-09-23 -- *unpublished*

### Created
- `start.sh` -- the front door: pulls what is missing, starts every container, waits until the page answers, and opens it.
- `tests/shell_coverage.py` -- line coverage for shell scripts, from `bash -x` traces of their own test suites.
- Tests: `tests/docker/test_start_script.py`, `tests/docker/test_shell_coverage_tool.py`.

### Updated
- `README.md` ("Three scripts"), `agent/USAGE.md`, `gui/README.md`.

### Fixed
- `tests/docker/test_launch_script.py` -- sixty tests marked `docker` without needing a daemon, which kept them out of the default run.

---

## v4_2 (4.2.0) -- 2026-09-23

The web interface.

### Created
- `gui/` -- React/TypeScript, built by Vite and served by nginx, which proxies the API:
  - `App.tsx`;
  - `api/` -- `client`, `events`, `types`, `useAsk`, `text`;
  - `charts/` -- `Chart`, `Frame`, `Plot`, `palette`, `scale`, `values`;
  - `components/` -- `AskBox`, `AnswerView`, `ResultTable`, `Progress`, `History`, `FeedbackBar`, `StatusBar`, `Disclosure`;
  - `feedback/` -- verdicts kept in the browser;
  - `nginx.conf.template`, `10-nl2sql-config.envsh`, `Dockerfile`, `README.md`;
  - a vitest suite gated at 100% of statements, branches, functions and lines.
- Tests:
  - `tests/gui/test_gui_contract.py`, `test_gui_project.py`, `test_gui_suite.py`;
  - `tests/docker/test_gui_compose.py`, `test_gui_container.py`;
  - `tests/rag/test_rag_images.py`, `tests/rag/test_rag_scripts.py`.

### Updated
- `docker-compose.yml` -- the `gui` service (profile `gui`). `launch.sh`, `setup.sh` -- `--gui`. `pytest.ini` -- the `node` marker.
- `rag/01_start_chunk_db.sh`, `03_start_vector_db.sh`, `publish_db_image.sh`, `run_update.sh`, `start_rag_db.sh`, `rag/README.md`.
- `agent/API.md`, `agent/README.md`, `agent/USAGE.md`, `README.md`.

### Fixed
- `gui/` -- the guard against a stale answer compared job ids, which are known only after the POST returns, so two questions in flight could have the first answer overwrite the second. It counts attempts now.
- `rag/publish_db_image.sh` -- forgetting the image reference was answered with `unknown option: chunkdb`.
- `rag/run_update.sh --help` -- printed two lines of its own shell source.
- `rag/01_start_chunk_db.sh`, `03_start_vector_db.sh` -- accepted an undocumented `-i`.
- The flag sweep in `tests/docker/test_script_coverage.py` counted a flag named in a list as exercised; it reads the syntax tree now, which is how `setup.sh --build` came to have tests at all.

### Published
- `nl2sql-agent:v4_2`; `nl2sql-gui:v4_2` -- first publish (2026-09-22 UTC).

---

## v4_1 (4.1.0) -- 2026-09-21

The agent as a TLS REST server.

### Created
- `agent/nl2sql_agent/api/` -- FastAPI under uvicorn:
  - `app.py` -- routes, errors and authentication;
  - `jobs.py` -- job store and resumable progress stream;
  - `models.py`, `translate.py`, `settings.py`, `server.py`, `__main__.py`;
  - `tls.py` -- a self-signed development certificate, with a switch that refuses one.
- `agent/API.md` -- the API reference.
- `docker/apitest/Dockerfile`, `docker/apitest/smoke.sh` -- a curl-only client in a container with nothing of this project in it.
- `.coveragerc` -- coverage including code run in its own process.
- Tests:
  - `tests/api/` -- `test_app.py`, `test_jobs.py`, `test_live_tls.py`, `test_server.py`, `test_settings.py`, `test_smoke_script.py`, `test_tls.py`, `test_translate.py`;
  - `tests/docker/test_api_compose.py`, `test_api_container.py`;
  - `tests/rag/test_semantic_chunker.py`.

### Updated
- `docker-compose.yml` -- the `api` and `apitest` services. `launch.sh`, `setup.sh` -- `--api`.
- `agent/nl2sql_agent/graph.py`, `llm.py`, `config.py`, `present.py`, `__main__.py`, `arch_diagrams/generate.py`.

### Fixed
- `docker/apitest/smoke.sh`:
  - aborted under `set -u` on bash 3.2, which macOS ships;
  - exited `2` ("the API was never there") for an unauthorised client;
  - counted a missing `.tables` as zero and passed.

### Published
- `nl2sql-agent:v4_1` (2026-09-21 UTC).

---

## v4 (4.0.0) -- 2026-09-20

The multi-agent pipeline, from the combined spec arch4.

### Created
- `agent/nl2sql_agent/`:
  - `supervisor.py` -- screening and intent;
  - `schema_retrieval.py` -- table selection by vector search over the DDL chunks plus foreign-key closure, where v3 had asked the model;
  - `literals.py` -- phrases resolved to real column values;
  - `state.py` -- the shared-state contract;
  - `validate.py` -- a `pglast` parse, a function denylist and a table allowlist;
  - `repair.py` -- one Repair Agent spending one shared retry budget;
  - `present.py` -- the Visual Formatter, the Insight Narrator whose claims cite their cells, and the Audit Checker.
- `arch_diagrams/arch_v4.{svg,png,tif}`.
- Tests:
  - `tests/agent/` -- `test_literals.py`, `test_present.py`, `test_repair.py`, `test_schema_retrieval.py`, `test_state.py`, `test_supervisor.py`, `test_validate.py`;
  - `tests/docker/test_script_coverage.py`.

### Updated
- `agent/nl2sql_agent/graph.py` -- four stages, four parallel retrieval branches, and deterministic gates: a static parse, then a plain `EXPLAIN` with a cost ceiling.
- `agent/nl2sql_agent/database.py`, `retrieval.py`, `tools.py`, `prompts.py`, `config.py`, `__main__.py`, `agent/requirements.txt`.
- `benchmarks/` -- per-agent timing and model calls. Measured 15/15, with total time down from 1499s to 910s.
- `docker/init_db.sh`, `docker-compose.yml`, `launch.sh`, `setup.sh`, `arch_diagrams/generate.py`, docs.

### Published
- `nl2sql-agent:v4` (2026-09-20 UTC). CLI only; `./launch.sh --api` cannot run against it, and says so.

---

## v3_1 -- 2026-09-18 -- *unpublished*

Least privilege for the agent, and the multi-agent specs.

### Created
- `docker/reader_role.sql` -- `nl2sql_reader`:
  - `SELECT` on `public` and nothing else;
  - sessions read-only by default;
  - a default privilege for tables added later.
- `tests/agent/test_least_privilege_live.py` -- the role asked of a live catalog, and every write tried anyway.
- `multi-agent_arch_specs/`:
  - `Multi-Agent_NL2SQL_arch1.md` to `arch4.md`;
  - `Multi-Agent_NL2SQL_arch4.{drawio,png}`, `make_arch4_drawio.py`;
  - the original `Multi-agent NL2SQL.drawio`/`.json`, and `OLD/`.

### Updated
- `docker/Dockerfile`, `docker/init_db.sh` -- the role created at build time. `setup.sh`, `launch.sh` -- and again on every start.
- `agent/nl2sql_agent/config.py`, `benchmarks/run_benchmark.py`, `docker-compose.yml` -- connect as the reader, never the owner.
- `README.md` ("Roles"), `agent/README.md`.

---

## v3 (3.0.0) -- 2026-09-18

Worked examples, generated multi-shot.

### Created
- `agent/nl2sql_agent/examples.py` -- the 45 golden pairs retrieved through an ensemble:
  - question similarity 0.50, BM25 over keywords 0.35, reasoning-target similarity 0.15;
  - the winners replayed to the model as conversation turns.
- `agent/nl2sql_agent/rerank.py` -- the shortlist scored against tables and SQL, then diversified.
- `rag/` -- the pipeline that builds both stores:
  - `01_start_chunk_db.sh`, `02_chunk_document.py`, `03_start_vector_db.sh`, `04_embed_document.py`, `05_load_golden_pairs.py`, `06_embed_golden_pairs.py`;
  - `run_all.sh`, `run_update.sh`, `start_rag_db.sh`, `publish_db_image.sh`, `lib.sh`;
  - `ragproc/` -- `chunker`, `chunk_store`, `embedder`, `golden_pairs`, `golden_vectors`, `vector_store`, `config`;
  - `docker/{chunkdb,vectordb,seeded}.Dockerfile`, `docker-compose.yml`, `README.md`.
  - It had built v2's knowledge base before it was committed.
- `chunking/semantic_chunker.py` -- the base semantic chunker the markdown one inherits from.
- `benchmarks/` -- `questions.py`, `runner.py`, `run_benchmark.py`, `README.md`: 15 questions, execution accuracy, three configurations.
- `launch.sh` -- start the stack from a set-up checkout.
- `arch_diagrams/arch_v3.{svg,png,tif}`.
- Tests:
  - `tests/agent/test_examples.py`, `test_examples_live.py`, `test_rerank.py`;
  - `tests/benchmarks/`;
  - `tests/docker/test_launch_script.py`;
  - `tests/rag/` -- `test_chunker.py`, `test_golden_pairs_parser.py`, `test_golden_pairs_store.py`, `test_golden_vectors.py`, `test_knowledge_stores.py`, `test_pipeline_cli.py`, `test_ragproc_support.py`.

### Updated
- `agent/nl2sql_agent/graph.py` (`retrieve_examples`), `prompts.py`, `tools.py`, `config.py`, `__main__.py`.
- `setup.sh` -- pins `rag-vectordb:v3` and `rag-chunkdb:v3`. `docker-compose.yml` -- the context store.
- `data_gen/datagen/facts.py`, `arch_diagrams/generate.py`, docs.

### Published
- `nl2sql-agent:v3` (2026-09-18 UTC).
- `nl2sql-rag-vectordb:v3` -- the knowledge collections plus both golden-pair vector tables (2026-09-17 UTC).
- `nl2sql-rag-chunkdb:v3` -- the knowledge chunks plus `golden_pairs` and its BM25 statistics (2026-09-17 UTC).

---

## v2_1 -- 2026-09-16 -- *unpublished*

### Created
- `arch_diagrams/generate.py`, `arch_diagrams/arch_v1.*`, `arch_v2.*` -- generated rather than drawn: layout computed against font metrics, with the drawn nodes recorded in the SVG.
- `tests/docs/test_arch_diagrams.py` -- the pictures checked against `graph.py`.
- `context_questions/translated_questions.md` -- the 45 golden question/SQL pairs, each verified against this database.

### Updated
- `README.md` ("Architecture diagrams"), `agent/README.md`, data generator and compose tests.

---

## v2 (2.0.0) -- 2026-09-16

Retrieval before writing.

### Created
- `knowledge/` -- `data_dictionary.md`, `ddl_index.md`, `business_index.md`, chunked into 53 sections for the knowledge base.
- `agent/nl2sql_agent/retrieval.py` -- the question embedded and matched against pgvector, and the matching sections given to the model beside the schema.
- Tests:
  - `tests/agent/test_prompts.py`, `test_retrieval.py`, `test_retrieval_live.py`;
  - `tests/docker/conftest.py`, `test_compose_rag_integration.py`, `test_setup_script.py`;
  - `tests/docs/test_docs.py`, which checks the documents against the code.

### Updated
- `agent/nl2sql_agent/graph.py` (`retrieve_knowledge`), `prompts.py`, `tools.py`, `config.py`, `__main__.py`, `agent/Dockerfile`.
- `docker-compose.yml` -- the vector store. `setup.sh` -- pulls the knowledge base and pins the agent tag.

### Published
- `nl2sql-agent:v1`, `nl2sql-agent:v2` and `latest`, the same image as `v2` (2026-09-17 UTC).
- `nl2sql-rag-vectordb:v1` -- the knowledge collections. `nl2sql-rag-chunkdb:v1` (2026-09-16 UTC).

---

## v1 -- 2026-09-15

The dataset and a schema-only agent.

### Created
- `data_gen/` -- a synthetic grocery-retail generator:
  - `generate_data.py`;
  - the `datagen` package -- calendar, dimensions, facts, reference data, schema columns, SQLite schema, validation, CSV writer;
  - `ddl.sql`, `data_schema.md`, `README.md`.
- `docker/Dockerfile`, `docker/init_db.sh`, `docker/emit_load_sql.py`, `.dockerignore` -- Postgres with the dataset generated and loaded at image build time.
- `docker-compose.yml`.
- `agent/` -- the schema-only agent:
  - `nl2sql_agent/` -- `__main__`, `config`, `database`, `graph`, `llm`, `prompts`, `tools`;
  - the graph runs `select_tables`, `fetch_schema`, `generate_sql`, `validate_sql`, `execute_query`, with a retry and `give_up`;
  - `Dockerfile`, `README.md`, `USAGE.md`, `requirements.txt`, and `basic_agent_steps.md`.
- `setup.sh` -- pull the dataset, start Postgres, and run `docker compose run --rm agent "..."`.
- `data_gen/detailed_data_dictionary.md`, `data_gen/index_descriptions.md` -- the dataset described column by column, with its indexes.
- The first test suite (`pytest.ini`, `tests/`), added the next day:
  - `tests/agent/` -- `test_cli`, `test_config`, `test_database_live`, `test_database_safety`, `test_graph`, `test_llm`, `test_tools`;
  - `tests/data_gen/` -- calendar, config, dimensions, facts, CLI, validation, writer;
  - `tests/docker/` -- agent image, compose config, Dockerfiles.

### Published
- `nl2sql-retail-postgres:v1` and `latest` (2026-09-16 UTC). The agent's `v1` tag was published with v2.
