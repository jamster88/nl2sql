# Changelog -- the short version

What each version added, updated or fixed, one line per change, newest first.
[`CHANGELOG.md`](CHANGELOG.md) has the same history with every major artifact
each version created, updated or fixed.

Version numbers are the agent's: `__version__` 5.1.2 is the image tag `v5_1_2`.
The app images -- agent, web interface, review service, review interface and
the desktop client's jar -- are released together at one number. The three
dataset images (`retail-postgres`, `rag-vectordb`, `rag-chunkdb`) version on
their own and are listed under the release they shipped with. A version marked
*unpublished* is a checkpoint in the repository that published no image tag.

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
