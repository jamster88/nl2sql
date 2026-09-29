# Changelog

Every version from the first, newest first, with the major artifacts each one
created, updated or fixed, and the image tags it published.
[`CHANGELOG_SIMPLE.md`](CHANGELOG_SIMPLE.md) is the same history as one line
per change.

**Versions.** The number is the agent's: `__version__` 5.1.2 is published as
the tag `v5_1_2`, and a tag with fewer components names a line
(`v5_1` is 5.1.x). The app images -- `nl2sql-agent`, `nl2sql-gui`,
`nl2sql-review`, `nl2sql-review-gui`, `nl2sql-console-gui` and the five
`nl2sql-desktop-build` platforms -- are released together at one number, which
[`tests/docs/test_versions.py`](tests/docs/test_versions.py) holds every
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
