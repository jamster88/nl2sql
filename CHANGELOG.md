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
`nl2sql-mlflowdb`, and since v5_6 `nl2sql-curate-gui` -- are released
together at one number, which
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
