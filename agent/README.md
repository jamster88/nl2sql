# NL2SQL Agent (v7, multi-agent)

A natural-language-to-SQL agent built with LangChain and LangGraph. It talks to
any model served by Ollama and queries the Postgres container from
[`docker-compose.yml`](../docker-compose.yml).

**v2 adds retrieval.** Before it picks tables or writes SQL, the agent embeds
the question and searches the pgvector knowledge base built from
[`knowledge/`](../knowledge) -- the data dictionary, the DDL index, and the
business index. The chunks that come back carry the things a schema alone
cannot tell a model: fiscal-calendar semantics, which columns are
pre-aggregated, and which joins fan out. That context is what lets it answer
questions v1 got confidently wrong (see [Why retrieval](#why-retrieval)).

**v5 checks that a correct answer is also a complete one** (arch5). An
answer contract read from the question -- the name beside every id, the
measure a ranking was ranked by, the latest complete fiscal year when no
period is named -- is shown to the generator before it writes, and a
Completeness Reviewer checks the rows against it after they run. See
[The pipeline](#the-pipeline).

**v4.1 adds a REST interface.** The same image also runs as an HTTPS server
(`python -m nl2sql_agent.api`) so a GUI -- in any language, with no client
library from here -- can ask questions and watch the pipeline work. The
contract is [`API.md`](API.md); how it is built is
[Serving it over HTTP](#serving-it-over-http) below.

**v5.3 adds a SQL console.** The same image again, run as
`python -m nl2sql_agent.console`: a query run as the agent runs its own, with
the verdict of each of the agent's gates beside the rows -- see
[The SQL console](#the-sql-console).

**v5.6 adds SQL snippets.** A fifth retriever finds the verified pieces of
SQL a question's answer is built from -- a join, a filter, a measure, a
dimension, each beside what it means -- and the generator is shown the ones
whose tables are in scope. See [SQL snippets (v5.6)](#sql-snippets-v56).

**v6 asks who is asking.** The pipeline is v5.6's. What changed is who it
runs for: with sign-in on, the REST API, the SQL console and the review
service accept a person signed in through the auth service, and run that
person's questions and statements as their own database role -- the agent
still connects as its read-only reader, and becomes the person for a
transaction with `SET LOCAL ROLE`. The command line is unchanged: it is
whoever runs it. See [`../auth/README.md`](../auth/README.md).

**v7 asks it several ways** (arch7.1, being built). The question is
screened once, reworded, the pipeline run once per wording, every distinct
answer read by a Judge, and the answers it accepts voted on -- a second wave
when the vote does not settle it -- and what the agreeing runs found fused
into the chosen one's answer. See [Asking it several ways (arch7)](#asking-it-several-ways-arch7).

For launching it and asking questions day to day, see [`USAGE.md`](USAGE.md).
This file covers how it works and how to extend it.

## Quick start

Run [`../setup.sh`](../setup.sh) once from the repo root. It pulls the
images, starts the retail database and the two retrieval stores -- the
pgvector knowledge base and the golden pairs -- and verifies the agent
container can retrieve from them. After that:

```bash
docker compose run --rm agent "What were the top 5 departments by net sales in fiscal year 2024?"
docker compose run --rm agent --json "Which 3 promotions had the highest promo quantity sold?"
docker compose run --rm agent            # interactive; Ctrl-D to exit
```

`docker compose run` starts both databases first and waits for each to pass its
health check. The agent is behind a compose profile, so a plain
`docker compose up` starts the two databases and not the agent.

Output goes to two streams: progress lines on stderr, the result table on
stdout, so `... > answer.txt` captures just the answer.

Or over the network, for something with a screen:

```bash
../launch.sh --api
curl --cacert ./nl2sql-ca.crt https://localhost:8443/v1/meta
```

## The pipeline

Four stages, one shared state object, one retry loop, defined in
[`nl2sql_agent/graph.py`](nl2sql_agent/graph.py) and drawn in full by
[`arch_diagrams/arch_v5.svg`](../arch_diagrams/arch_v5.svg), with
[`arch_v4.svg`](../arch_diagrams/arch_v4.svg),
[`arch_v3.svg`](../arch_diagrams/arch_v3.svg),
[`arch_v2.svg`](../arch_diagrams/arch_v2.svg) and
[`arch_v1.svg`](../arch_diagrams/arch_v1.svg) alongside it for the earlier
versions. The design and the reasoning behind each departure from it are in
[`Multi-Agent_NL2SQL_arch5.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5.md),
which is arch4 plus the answer contract and the Completeness Reviewer.
[`Multi-Agent_NL2SQL_arch5_1.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_1.md)
supersedes it without changing anything in this package: it adds the human
review of section 14, where a reviewed answer goes depending on its verdict.
[`Multi-Agent_NL2SQL_arch5_2.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_2.md)
supersedes both, adding section 15, model routing: each model call routed
by its task's complexity to the cheapest suited model in a catalog of the
Ollama host's models. It is built -- [Model routing](#model-routing-arch52)
below -- with the catalog made by
[`models/build_catalog.py`](../models/build_catalog.py) and measured by
[`models/calibrate.py`](../models/calibrate.py).

```
supervise --+-- retrieve_schema ----+
            |-- retrieve_literals --|
            |-- retrieve_knowledge -+--> aggregate --> generate_sql
            |-- retrieve_examples --|                       |
            +-- retrieve_snippets --+                       v
            |                                        validate_static
            +--> refuse                                     | pass
   give_up <-- repair <------------------------------+      v
                 ^   |                               |  planner_gate --> execute_query
                 |   +-- attempts < max --> generate_sql                      |
                 |                                                            v
                 +---------- incomplete -------------------------------- review
                 |                                                            | complete
                 +---------- semantic_issue -- audit <-- narrate <-- visualise
```

| Stage | Node | What it does | LLM |
|---|---|---|---|
| 1. Intake | `supervise` | Screens for prompt injection and out-of-scope questions; classifies intent; reads the answer contract (what the rows are about, the measure, the period) | screens |
| 1. Intake | `refuse` | Answers a refused or ambiguous question without touching the database | -- |
| 1. Context | `retrieve_schema` | Tables from the DDL-chunk vectors | -- |
| 1. Context | `retrieve_literals` | Phrases in the question resolved to real values | -- |
| 1. Context | `retrieve_knowledge` | Business rules and data-dictionary chunks | -- |
| 1. Context | `retrieve_examples` | The three-retriever golden-pair ensemble | -- |
| 1. Context | `retrieve_snippets` | SQL snippets -- joins, filters, measures, dimensions -- found by keyword phrase and by meaning (v5.6) | -- |
| 1. Join | `aggregate` | One table set -- the contract's tables first -- deduplicated, foreign-key closed, capped, described; the snippets over those tables | -- |
| 2. Synthesis | `generate_sql` | The only place SQL is written, on the draft and every repair | writes SQL |
| 3. Gate | `validate_static` | `pglast` AST: one statement, SELECT only, no writing CTE, tables in scope | -- |
| 3. Gate | `planner_gate` | `EXPLAIN (FORMAT JSON)` in a READ ONLY transaction; cost ceiling | -- |
| 3. Execute | `execute_query` | Reader role, READ ONLY, statement timeout, row cap | -- |
| 3. Review | `review` | The Completeness Reviewer: a label beside every id, the measure a ranking was ranked by, the period, the row count; then one reflection | once, when the rows name something |
| 3. Repair | `repair` | Classifies the failure into a hint; asks the model only when it cannot | rarely |
| 3. Repair | `give_up` | Returns the last SQL and every attempt that was made | -- |
| 4. Present | `visualise` | Chart choice from the result's shape, as a lookup | -- |
| 4. Present | `narrate` | Structured claims, each pointing at the cells it came from, stating every assumption | narrates |
| 4. Present | `audit` | Verifies every number against those cells, and that every assumption is stated | -- |
| 4. Present | `finish` | Renders the markdown answer | -- |

The five Stage 1 retrievers are branches of one LangGraph superstep, so they
run concurrently and `aggregate` is the fan-in. Each is best-effort: one that
cannot reach its store records why in `retrieval_errors` and the run continues
without it. With all of them down the pipeline degrades to schema-only, which
is exactly what v1 was.

**Three model calls on the happy path, or four**: `supervise`,
`generate_sql`, `narrate`, and -- when the rows identify an entity it could
flesh out -- the Completeness Reviewer's one reflection. v3 also made three,
but two of them were table selection and validation review, which cost 364
and 499 of 1499 benchmark seconds and neither of which wrote the answer.
Validation is now an AST parse and a planner call, both deterministic and
both measured in milliseconds.

**A result that ran is not yet an answer** (arch5). "Top 10 SKUs" used to
come back as ten `sku_id` values: correct, and useless without a second
query. The Supervisor now also reads what the answer is about -- entities,
measure, period -- and [`contract.py`](nl2sql_agent/contract.py) turns that
into an *answer contract*: the label beside every key (read from the
catalog's key constraints: `sku_id` -> `product_name`, `store_id` ->
`store_name`), the measure a ranking was ranked by (net sales when the
question names none), the latest complete fiscal year when it names no
period, and the N it asked for. The generator is shown the contract as one
line before it writes; [`completeness.py`](nl2sql_agent/completeness.py)
checks the result against it after it runs -- rules R1-R4 first, at no
cost, then one bounded reflection that may only add a column about an
entity the rows already name. A gap is sent back once; if it survives, or
the budget runs out, the answer is shown with the gap named. A default the
pipeline chose, such as the fiscal year, is written to `assumptions`, and
the narrator must state it -- the audit checks, and the answer states it
itself if the narrative did not.

**One retry budget.** A failure from any gate -- the AST check, the planner, a
runtime error, the Completeness Reviewer, or the audit -- becomes an
`Issue`, routes to `repair`, and spends the same `attempts` counter. There
is no path that loops without being counted, and `MAX_ATTEMPTS` (default 7)
is one draft and six repairs: arch4's 4 paid for four failure sources, and
the reviewer is a fifth.

The Repair Agent does not write SQL. It turns a failure into a hint and hands
it back to the generator, which keeps a single component responsible for the
query. It classifies deterministically first and calls the model only for
errors it does not recognise, so the common failures cost no model call at all.

Table and column descriptions come from Postgres `COMMENT ON` metadata. The
current schema has no comments, so the agent works from names alone -- adding
comments to [`ddl.sql`](../data_gen/ddl.sql) feeds straight into the schema
prompt with no code change.

### Tools

The nodes reach the database and the retrieval stores through LangChain tools
built by `build_tools()` in [`nl2sql_agent/tools.py`](nl2sql_agent/tools.py),
so the same functions can later be bound to a tool-calling model rather than
invoked at fixed points:

| Tool | Used by | What it reads |
|---|---|---|
| `search_knowledge` | `retrieve_knowledge` | The business-rule and data-dictionary collections |
| `search_examples` | `retrieve_examples` | The golden pairs, through the three-retriever ensemble |
| `search_snippets` | `retrieve_snippets` | The SQL snippets, by keyword phrase and by meaning |
| `get_schema_and_data` | `aggregate` | Columns, types, keys, comments and sample rows |
| `describe_all_tables` | `retrieve_schema`, only under `SCHEMA_RETRIEVAL=llm` | The whole catalog, for v3's table-selection call |
| `execute_query` | -- | Retained for callers outside the graph; the graph executes through `Database` directly so it can pass a principal |

### Measured

The 15-question benchmark, same questions and same model as v3:

| | v3 | v4 | v5 (arch5) |
|---|---|---|---|
| Execution accuracy | 15/15 | 15/15 | 15/15 |
| Total | 1499s | 909.7s | 991.6s |
| Median per question | 100.5s | 61.2s | 63.8s |
| Model calls per question | 3 | 3.7 | 3.7 |
| Narrative traced to cells | not measured | 84.6% | 83.3% |

The two model calls v4 removes are what that difference is made of:

```
v3  validate_sql    499.4s      v4  validate_static + planner_gate   0.1s
v3  select_tables   363.9s      v4  retrieve_schema                  1.2s
```

Accuracy holds because neither call was writing the answer. The Supervisor is
cheap, 45.8s across 15 questions. The Narrator is not: 217.3s, a quarter of the
run, which the architecture did not anticipate. It buys a narrative whose every
number has been checked against a cell, and `NARRATE_ENABLED=false` turns it off
for a run that only wants rows.

Model calls come to 3.7 per question rather than the 3 the architecture
predicts, and the gap is all retries: 18 generations for 15 questions, and 22
narrations because the audit sends a claim back when it cannot reproduce one.
Not one of the repairs cost a model call -- the classifier recognised every
failure -- which is the part of the design that was most at risk of being
merely asserted.

Treat the totals as one run. Wall time against a shared Ollama host varied by
about 30% across three runs of the same 15 questions; what does not vary is the
shape, and the shape is that generation is most of the time and the gates are
none of it.

**What the Completeness Reviewer costs.** On the v5 run it reflected on
three of the fifteen questions -- the three whose rows name an entity (B09's
allowance types, B10's SKUs, B13's competitors) -- for 16.1s in all. B09 and
B10 passed on their first draft, which is what the architecture predicted of
the contract line. B13 used a second generation; the benchmark does not keep
attempt histories, but traced separately that retry came from v4's audit (a
first draft ranked the wrong way round and reported 115% of our price), not
from the reviewer. The run that
produced these numbers was the fourth; the three before it are why the
contract line never restates a measure the question names (B13's ratio was
paraphrased into a difference and computed as one), names no entity for a
"how many" question (B01 was answered with ten named stores), and brings the
calendar into scope for any period, named or not (B11 lost a draft to the
table allowlist).

Re-measure with `python benchmarks/run_benchmark.py`, which now reports
per-agent timing, model calls per node, and the fraction of the narrative the
audit could trace back to the result.

## Asking it several ways (arch7)

Temperature is zero everywhere, so the only independent second opinion the
pipeline can give itself is a different wording of the same question. arch7
([`Multi-Agent_NL2SQL_arch7.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7.md))
builds on that: the question is screened once, reworded three to ten ways,
each rewording held to the original's answer contract, the pipeline above run
once per wording, and the largest agreeing group's answer delivered with the
record of every run. arch7.1
([`Multi-Agent_NL2SQL_arch7_1.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_1.md))
is arch7 with the Judge moved before the vote: every distinct answer is read
against the question first, and only the runs whose answer it accepts are
counted. It is built in phases
([`Multi-Agent_NL2SQL_arch7_1_implementation.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_1_implementation.md),
with [`..._risks_by_phase.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_1_risks_by_phase.md)),
and Phases 0 to 3 of it are built -- the outer graph, the rewordings, the
Judge and the vote, the second wave and fusion; the calibration of the two
new routing tasks and the clients' views of the runs are still to come --
[`ensemble.py`](nl2sql_agent/ensemble.py), around the pipeline:

```
screen ─┬─ refuse                    the Supervisor, once, on the original
        └─ paraphrase                up to 10 rewordings, most different first
           └─ screen_paraphrase      F1-F3 and F5 in code; F4, the Supervisor's reading
              └─ plan_wave <──────────┐  the original + the first 3 faithful;
                 └─ answer            │  then every faithful one not yet run
                    └─ validate       │  E1-E5, agreement, the groups
                       └─ judge       │  every group's answer, accepted or set aside
                          └─ vote ─┬──┘  the accepted runs vote; unsettled, a second wave
                                   ├─ fuse ─┐   columns, claims, dissent onto the chosen
                                   └────────┴─ deliver   the answer, its line first
```

| Node | What it does | LLM |
|---|---|---|
| `screen` | The Supervisor on the original: verdict, intent and the answer contract every wording is held to. An injection or an ambiguity stops here, and nothing is reworded | screens |
| `refuse` | A refused or ambiguous question is answered as the pipeline answers it, and nothing runs | -- |
| `paraphrase` | The Paraphraser ([`paraphrase.py`](nl2sql_agent/paraphrase.py)): `ENSEMBLE_MAX_PARAPHRASES` rewordings in one structured call, from the question and what its answer may not change, named in everyday words and nothing else, each with a few words on what it varied. A failure costs the rewordings: the original runs alone | writes rewordings |
| `screen_paraphrase` | The fidelity gate: each rewording held to F1 numbers, F2 literals, F3 polarity and F5 distinct in code ([`fidelity.py`](nl2sql_agent/fidelity.py)) -- so one that visibly changed the question costs no call -- then read by the Supervisor, and kept only when that reading proceeds and builds the original's contract (F4, `contract.same_contract`): the same entities, measure and period, the contract built in the original's shape, since its numbers and direction are F1's and F3's. Fewer than `ENSEMBLE_PARAPHRASES` kept, the Paraphraser is asked once more, told which failed and why | screens, light |
| `plan_wave` | The first wave: the original and the first `ENSEMBLE_PARAPHRASES` faithful rewordings, in the order written. A second: every faithful rewording not yet run, with the vote before it emptied | -- |
| `answer` | The pipeline on each wording, seeded with the screening made for those exact words and the anchor contract -- every run is held to the contract read from the question as asked -- so no run makes a Supervisor call of its own (`Nl2SqlAgent.answer`) | the pipeline's |
| `validate` | Each run checked against its own question ([`agreement.py`](nl2sql_agent/agreement.py)): E1 answered, E2 faithful, E3 rows when the question implies some, E4 the audit did not judge the rows wrong, E5 not a sample -- only what makes a run's rows no answer keeps it from voting. Then every pair compared with the benchmark's scorer ([`compare.py`](nl2sql_agent/compare.py)) and the runs grouped by agreement | -- |
| `judge` | The Judge ([`judge.py`](nl2sql_agent/judge.py)), on every question with an answer to judge, before anything is counted: each group's representative query and first five rows, lettered in the order the runs were asked -- never how many runs gave it -- read against the question, what every answer was held to, and the knowledge the original's run retrieved. A verdict per letter, accepted or set aside with the mistake in the query named; an answer it says nothing about stands. A failure costs the verdicts: the runs vote alone | judges, heavy |
| `vote` | Only the runs whose answer the Judge accepted vote: a strict majority of them, the largest accepted group winning, ties to the original's. `judged` when that is not the group the runs alone would have chosen; when the Judge accepted none, the runs' own choice, `contested`, with its objection. A vote that does not settle the question -- no majority among the runs that voted, or one run standing alone -- goes back to `plan_wave` while a wave is left (`ENSEMBLE_WAVES`), a faithful rewording has not run and `ENSEMBLE_DEADLINE_SECONDS`, if set, has not passed; the Judge then reads every group again and the vote is recounted over both waves. With no wave left, the largest accepted group is delivered as `contested` | -- |
| `fuse` | The representative of the winning group -- complete before a gap was accepted, audited before claims were dropped, the original's wording before a rewording's, fewer attempts, the cheaper plan -- whose SQL and chart are the answer's ([`fuse.py`](nl2sql_agent/fuse.py)). Then, in code: a column of a dimension the rows identify that another run of the group carried, joined on that dimension's key only when every row matches exactly once (`ENSEMBLE_FUSE_COLUMNS`); the other runs' claims that speak of a row the narrative does not yet speak of, each kept only when the delivered rows reproduce it, up to `ENSEMBLE_MAX_CLAIMS`, and the whole re-audited; the assumptions every run made, once; and each losing answer with the tables and filtered columns its query has that the chosen one's has not. No SQL is fused | -- |
| `deliver` | The answer, rendered for the question as asked, opening with its agreement line: "Agreed by 4 of 4 independent runs of the question, each worded differently.", or, when the Judge overruled the runs, "The Judge set aside the answer 3 of 4 runs gave -- *its reason* -- and accepted this one, which 1 gave." Its notes name each joined column's run, each column left out and why, and each answer that lost. When no run could vote, the original's give-up, as the pipeline delivers one | -- |

The outer state ([`ensemble_state.py`](nl2sql_agent/ensemble_state.py))
keeps each run whole as a candidate -- its wording, its outcome, whether it
could vote and why not, its group, its state with its own trace -- the
Judge's verdict on each group, and every rewording with the check that
discarded it; `--json` and the REST answer's
`ensemble` carry that record ([`API.md`](API.md)). Progress lines from a
run's nodes carry the run's index (`[2] [sql] ...` on the CLI, `candidate`
on the event stream), and the trace is one per question, each run a
"Candidate k" span beneath the ensemble's own nodes (see
[Tracing](#tracing-mlflow)). `ENSEMBLE_ENABLED=false` is the pipeline alone,
and the answer's `ensemble` is null; with `SUPERVISOR_ENABLED=false` nothing
is reworded, since no rewording runs that the Supervisor has not read.

**The gate was measured on real rewordings.** F1-F3 started from the
implementation specification's word lists and grew until all 45 of the
benchmark's hand-written rewordings passed ([`docs/benchmark.md`](../docs/benchmark.md)).
F4 was measured with the live Supervisor reading the same 45. Compared as
the specification first had it -- each rewording's contract built from its
own words -- it discarded 18 of them: the Supervisor copies a period in the
question's own spelling, so "FY2025" and "fiscal year 2025" differed, and
the contract's reading of a question's shape knows a count only as "how
many", so "What is the store count?" read as a list. F4 now compares periods
however they are spelled, and builds the rewording's contract in the
original's shape -- the numbers and the direction are F1's and F3's to hold
-- so what it compares is what the Supervisor read: the entities, the
measure, the period. The 45 lose 3, each where it read a different entity.

**Why the Judge reads before the vote counts.** arch7 voted first and
asked a Judge only when the vote could not decide. Measured on the
paraphrase set, the runs' mistakes were shared: runs that fell into the
same trap got the same number, so they agreed and outvoted the run that did
not, and every wrong answer the vote delivered had a majority behind it --
which a Judge asked only on a split never sees
([`docs/benchmark.md`](../docs/benchmark.md)). So the Judge reads every
answer first, without the count, and the vote counts what it accepts. It
cannot take the answer away: with none accepted, the runs' own choice is
still delivered, marked contested. Its rules name the kinds of mistake the
benchmark's runs made -- a filter the question does not state, a join off
the knowledge's grain, other units than asked -- so the benchmark is no
longer blind to them, and the report scores the runs' own choice wherever
the Judge overruled them, so its effect shows both ways.

**The host gate.** Every call the agent makes to its chat models -- any
run's, any job's, the Supervisor's included -- takes a slot of one gate
first, held around the call and given back when it returns, fails or meets
`OLLAMA_TIMEOUT` ([`hostgate.py`](nl2sql_agent/hostgate.py)).
`OLLAMA_PARALLEL_CALLS` is how many slots there are, one by default, because
the host this was built against serves one call at a time; it is also how
many runs of one question go at once. It is a statement about the host, not
something measured, so set it to what the host serves -- Ollama's own
`OLLAMA_NUM_PARALLEL` -- and no higher, and raise it only for a host with the
memory for its resident models times the slots: two runs at different rungs
ask for two models at once, and a host that holds one swaps on every call.
One consequence for an upgrade: at one slot the REST API's two workers,
which 6.3 let call the host at once, take turns; a host that serves two
calls needs `OLLAMA_PARALLEL_CALLS=2` to keep them. And the API warns at
start when `OLLAMA_PARALLEL_CALLS` times `API_MAX_CONCURRENCY` -- the runs
that may hold a database connection at once -- is more than the agent's
connection pool holds (15, SQLAlchemy's defaults) or more than half the
reader role's connection limit (60, set in
[`common/nl2sql_common/roles.py`](../common/nl2sql_common/roles.py)).

## Serving it over HTTP

[`nl2sql_agent/api/`](nl2sql_agent/api) is the REST interface. It is a
separate package from the pipeline, imported only when the server runs, so
`python -m nl2sql_agent` costs nothing for it. The wire contract, the
endpoints and every setting are in [`API.md`](API.md); this is how it is put
together and why.

| Module | What it owns |
|---|---|
| [`settings.py`](nl2sql_agent/api/settings.py) | Everything about the socket, none of it about the pipeline. Same empty-is-unset discipline as `config.py`, for the same compose reason |
| [`tls.py`](nl2sql_agent/api/tls.py) | Generates the development certificate, reads one off disk, and enforces `API_TLS_ALLOW_SELF_SIGNED` |
| [`models.py`](nl2sql_agent/api/models.py) | The published request and response shapes -- what becomes `/openapi.json` |
| [`jobs.py`](nl2sql_agent/api/jobs.py) | Questions in flight: a bounded thread pool, numbered progress events, and the resumable stream over them |
| [`translate.py`](nl2sql_agent/api/translate.py) | The seam between `state.py` and `models.py`, so a field renamed inside the graph breaks one file |
| [`app.py`](nl2sql_agent/api/app.py) | The routes, the authentication, the error shape, CORS |
| [`server.py`](nl2sql_agent/api/server.py) | Flags, the certificate decision, the startup banner, uvicorn |

Four decisions worth knowing about:

**A question is a resource.** Answering takes about a minute, so `POST
/v1/questions` returns a job and the client polls, streams, or asks the
server to hold the connection. All three are the same document at the same
URL, which is what keeps the simple client simple without giving the patient
one a different contract to implement.

**Progress is the graph.** `Nl2SqlAgent.run` takes an `on_progress` callback
per run, carried in a `ContextVar` rather than set on the instance -- one
agent now answers several questions at once, and an instance attribute would
put one caller's progress on another caller's stream. LangGraph copies the
context into the threads it fans stage 1 across, so the four concurrent
retrievers report to the right run too.

**TLS is the default and the development certificate is removable.** Under
compose the stack's pki service issues the API a certificate of its own
from a development CA before it starts; on its own, the server writes
itself a self-signed one. `API_TLS_ALLOW_SELF_SIGNED=false` refuses to
start behind either -- neither generating nor loading one -- so the
convenience cannot quietly become the deployment.

**The translation layer is separate on purpose.** `state.py` is internal and
changes with the architecture; `models.py` is what other people's code is
compiled against. `translate.py` is the only module that knows both.

Tested in [`tests/api/`](../tests/api) without Docker. Two of those tests run
what would otherwise need a container:
[`test_live_tls.py`](../tests/api/test_live_tls.py) binds a real socket with
the real certificate and talks to it with the standard library, and
[`test_smoke_script.py`](../tests/api/test_smoke_script.py) runs
[`docker/apitest/smoke.sh`](../docker/apitest/smoke.sh) -- bash, curl and jq
-- against a live server, which is how a shell script with no Python in it
gets its branches covered.
[`tests/docker/test_api_container.py`](../tests/docker/test_api_container.py)
then does it again against the packaged container, reached by a curl-only
image over verified TLS.

The other clients of this package are [`gui/`](../gui), a React and
TypeScript front end, and [`desktop/`](../desktop), a JavaFX one, and neither
imports anything from it -- every endpoint, the event stream with its resume
and its fallback to polling, and the whole answer rendered including the
charts. They are the two worked examples of the contract in
[`API.md`](API.md), in two languages, and a useful thing to read before
writing a client of your own.

## The SQL console

[`nl2sql_agent/console/`](nl2sql_agent/console) is a third way into the same
image, `python -m nl2sql_agent.console`: the retail database queried the way
the pipeline queries it, for working out why an answer was wrong. It imports
the pipeline's pieces rather than copying them, so what it reports is what
the agent would have done -- and it is its own process, apart from the API,
because the API runs SQL the pipeline wrote and this runs SQL a person
typed. What it is for, and its settings, are in
[`console/README.md`](../console/README.md).

| Module | What it owns |
|---|---|
| [`query.py`](nl2sql_agent/console/query.py) | The Inspector: `validate.validate` for safety and then with the whole schema as scope, `EXPLAIN` judged by `database.total_cost` and `plan_cost_problem` -- the functions the planner gate calls -- and the query inside `READ ONLY` and the agent's statement timeout, through a server-side cursor |
| [`settings.py`](nl2sql_agent/console/settings.py) | `CONSOLE_*`, and `AGENT_SETTINGS`: the six of this package's settings it runs under, read through `config.Settings` |
| [`models.py`](nl2sql_agent/console/models.py) | Its wire shapes; health, readiness and the error envelope are the API's own |
| [`app.py`](nl2sql_agent/console/app.py) | The routes, the token, readiness with the role's privileges in it |
| [`server.py`](nl2sql_agent/console/server.py) | Flags, its own certificate (from the pki service) presented rather than generated, the banner |

`plan_cost_problem` is the one change it made here: the planner gate's
"estimated plan cost ... exceeds the ceiling" used to be written inside
`_planner_gate`, and a console that wrote its own would have been a second
copy to drift. `Database.engine` is the other addition, for a caller that
runs statements of its own on the same pool.

## Model routing (arch5.2)

Every agent that calls a model asks [`router.py`](nl2sql_agent/router.py) for
one by task and **rung** -- light, standard or heavy -- and gets the fastest
model on the Ollama host that calibration measured to be suited to that task
at that rung. [`complexity.py`](nl2sql_agent/complexity.py) computes the rung
from state the pipeline already holds; nothing in routing calls a model.

| Agent | Rung | Climbs when |
|---|---|---|
| Supervisor | light; standard when a free pre-screen flags the question -- over 60 words, system vocabulary such as *ignore* or *reveal*, or SQL in it | never: one call |
| SQL Generator | scored by the Context Aggregator: the tables the answer needs, estimated from its contract (3-4 +1, 5+ +2), the intent, a ranked measure, a trap rule among the three nearest chunks, a value named exactly and found in two columns, a question over 40 words, and a near worked example (-2); 1 or less light, 2-3 standard, 4+ heavy | every repair; a gap the completeness *rules* found holds the rung once, since its fix is mechanical |
| Completeness reflection | the generation's, capped at standard | with the generation |
| Insight Narrator | light for at most 5 rows of at most 3 numbers, else standard | an audit send-back |
| Repair diagnosis | one above the generator's, standard at least | with the generator |
| Paraphraser (arch7) | the Supervisor's: light, standard when the pre-screen flags the question | never: one call, and one retry |
| Judge (arch7.1) | heavy, always | never: one call a question, before the vote |

The two arch7 tasks are new in 7.0, and a catalog built before it -- schema
2, every calibrated host's, the committed one included -- knows nothing of
them. It is read, not refused: the two are taken as unmeasured, so
`OLLAMA_MODEL` answers them until `models/calibrate.py` measures them, and
the routing table's notes say so. A catalog of any other schema than 2 or 3
is refused, as before.

The **routing table** is built once, at startup, from the catalog
`MODEL_CATALOG` names. Each task and rung gets the fastest model whose
*measured* suitability reaches it -- the prior in the catalog is a guess from
size and description, and by size alone a 2023 mixture of experts outranks
the reference, so it is used only with `MODEL_ROUTE_ON_PRIOR=true`. At most
`MODEL_MAX_LOADED` distinct models fill the table, because Ollama swaps from
disk when too many are asked for: `OLLAMA_MODEL` first, then whichever
models take the most rungs. `MODEL_ROUTE_<TASK>` pins a task's rungs
outright. The table is printed by the CLI, logged, and reported by
`/v1/meta`.

A **call** goes to its rung's model. One that is not on the host, cannot be
reached, fails or answers with nothing is retried once on the rung's
fallback and then on `OLLAMA_MODEL` -- and so is one that runs past
`OLLAMA_TIMEOUT` or `OLLAMA_NUM_PREDICT` tokens, since a model that
degenerates would otherwise generate without end: Ollama shifts a full window
rather than stopping. A model that answers badly is not a
routing failure: the gates catch that, and the repair climbs.

The **trace** says, for every call, which model answered (`model`), the rung
it was routed at (`rung`), why (`route`), and every model that failed first
(`hops`), so `--json` and the benchmark attribute accuracy and time per
model and not only per agent.

**Off is v5.1.** `MODEL_ROUTING_ENABLED=false`, no catalog, a catalog in
which nothing was measured, or a catalog built for another host sends every
call to `OLLAMA_MODEL`. The committed catalog describes the host this
checkout was developed against, so on any other host the routing table says
it was ignored, and routing starts once
[`models/build_catalog.py`](../models/build_catalog.py) and
[`models/calibrate.py`](../models/calibrate.py) have described that host
([`models/README.md`](../models/README.md)).

`launch.sh` -- and so `start.sh` -- asks the agent image for this table on
every start, built exactly as the agent builds it, and prints how many
models the calls are shared between or why every one goes to
`OLLAMA_MODEL`. A catalog the agent cannot read, which would stop it
starting, is a warning there rather than a surprise at the first question.

Two names for one model -- a local build that only bakes in a bigger window,
say -- share a behaviour fingerprint in the catalog, and the table routes to
at most one of them, so the same weights are never loaded twice.

Six things the spec says that the code does differently, each found by
running it:

- **The window is per model, not per task.** Ollama reloads a model whenever
  a request asks for a different `num_ctx`, so a light task's small window
  and a heavy task's large one on the same model would reload it between
  calls. `OLLAMA_MODEL` keeps `OLLAMA_NUM_CTX`, exactly as without routing;
  every other model gets the smaller of its own context and `MODEL_NUM_CTX`.
  Measured on the benchmark, the largest prompt any agent sends is 8,861
  tokens (the generator's), so the 32,768 default leaves nearly four times
  that in hand.
- **The generator's score reads the answer, not the retrieval.** The spec
  counted the tables after closure; measured on the benchmark that is seven
  to ten tables for every question, "how many stores are there?" included,
  because it is the scope four retrievers proposed -- and with it twelve of
  the fifteen questions scored heavy. The count now comes from the answer
  contract (one table per entity, one for a measure's fact unless it is a
  count, one for a period), which is within one table of every reference
  query. For the same reason a trap rule counts only among the three chunks
  nearest the question, an ambiguous literal only when a value the question
  names exactly is found in two columns ("year" inside a promotion's name is
  not a choice), and the bridge signal is gone: closure over the scope says
  nothing about the query. The benchmark now scores six questions light and
  nine standard.
- **A catalog of another host is ignored, not refused.** The spec chose to
  refuse, and said it would annoy someone first: with a calibrated catalog
  committed, it would stop the agent starting anywhere but the machine it
  was measured on. Ignoring it routes nothing on another machine's numbers
  either, and the routing table says why.
- **The Supervisor and the generator must match the reference on every
  probe.** The spec calls a model suited within one question of the
  reference. A light Supervisor one question behind refused a valid
  benchmark question as out of domain, and a refusal is no answer. A
  generator one question behind lost that very question in the routed
  benchmark: the spec expected a repair up the ladder to rescue it, but the
  repairs start from the wrong draft, and even on the reference they did not
  recover. The narrator, the reflection and the diagnosis keep the tolerance
  of one, since their miss costs a retry or a sentence.
- **A rung needs five probes.** "Within one question" of three probes allows
  a third of them wrong. The first calibrated benchmark run routed the
  reflection to a model that had agreed with the reference on its three
  probes; it sent a right answer back, and the answer was lost. A rung probed
  fewer than five times now keeps its prior, so it stays on `OLLAMA_MODEL`.
- **A near worked example is judged by similarity, not the fused score.**
  The fused score is normalised within one search, so the best of even a
  poor shortlist scores near 1.0; the question-vector similarity says how
  near the pair really is. The spec's "with the same intent" is not
  applied: the golden pairs carry no intent.

## Tracing (MLflow)

[`tracing.py`](nl2sql_agent/tracing.py) writes every run into MLflow when
`MLFLOW_TRACKING_URI` names a server: one trace per question, filed under
`MLFLOW_EXPERIMENT_NAME`. The trace is drawn by the same wrapper that writes
the state's `trace` entries (`graph.traced_node`), so the two records of a
run cannot disagree about what ran:

| Span | Type | Holds |
|---|---|---|
| `nl2sql` | `AGENT` | the question in; the answer, SQL and error out. Tagged `nl2sql.outcome` (`answered`, `gave_up`, `refused`), `nl2sql.screening`, `nl2sql.attempts`, `nl2sql.model_calls`, `nl2sql.rows`, `nl2sql.version`, and where the run came from: `nl2sql.entrypoint` (`cli`, `api`, `benchmark`) and, from the API, `nl2sql.job_id`. The principal, when there is one, is MLflow's own user field |
| one per agent | from `graph.TRACE_SPANS` | the state the agent reads, as inputs; the update it returns, as outputs; its trace entry -- detail, model calls, model, rung, route, hops -- as attributes. Named as the architecture names them: Supervisor, Schema Retriever, ..., Repair Agent, Insight Narrator, Audit Checker |
| one per model call | `CHAT_MODEL` | inside the agent that made it, named for the model that was asked: the messages, the answer (a chat completion, or the object a structured call parsed), the tokens it cost -- for a plain call; LangChain's structured output, which the Supervisor, reflection and narrator use, does not pass the count on -- and the task, rung and route. A call the router passed down its chain is a span per model asked, the failed ones marked |

`graph.TRACE_SPANS`, beside `STEP_LABELS`, is the one place a node's name,
span type and inputs are written down, and a test fails when a node is added
without one.

**Under the ensemble** (arch7.1) a question is still one trace. The root
span's children are the ensemble's own nodes (`ensemble.TRACE_SPANS`:
Supervisor, Paraphraser, Fidelity Gate, Wave Planner, Candidate Runs,
Agreement, Judge, Vote, Fusion, Answer), and beneath Candidate Runs is a
span per run, "Candidate k", holding that run's agents as the table above
describes them -- its Supervisor with no model call inside, the screening
having been made once above it. A second wave repeats Wave Planner,
Candidate Runs, Agreement, Judge and Vote in the same trace, its runs
beneath its own Candidate Runs. The runs happen on worker threads, each
started in a copy of the question's context, so their spans land in the
question's trace whichever thread made them. The trace gains three tags,
`nl2sql.agreement` (`4/4 unanimous`), `nl2sql.candidates` and
`nl2sql.judge` -- what the Judge did: `accepted`, `set aside`, `overruled`,
`accepted none`, `failed` or `not asked` -- and its `nl2sql.attempts` and
`nl2sql.model_calls` are read from the runs: the delivered run's attempts,
and every run's calls with the ensemble's own.

**Verdicts.** The REST API records each verdict -- `yes`, `no`,
`incomplete` -- on the answer's trace as human feedback named `verdict`,
after the staging database has taken it. Voting again overrides it, which
MLflow keeps as history; withdrawing deletes it, finding a job the server has
already forgotten by its `nl2sql.job_id` tag. The staged verdict is the
record of truth; MLflow is told second, and not hearing does not fail the
vote.

**Best effort, like retrieval.** Nothing here imports MLflow until a server
has answered its health check, so with the setting unset the agent never
loads it. A server that does not answer costs the run its trace and nothing
else; the reason is logged once, and runs go untraced for thirty seconds
before one asks again, so a server started after the API is found without a
restart. MLflow's HTTP client would otherwise retry a failed send for
minutes -- measured: four, holding the CLI's exit -- so the agent bounds it
(`MLFLOW_HTTP_REQUEST_MAX_RETRIES`, `_BACKOFF_FACTOR`, `_TIMEOUT`) unless the
environment already has.

**The client is MLflow's tracing package alone**, `mlflow-tracing`: spans,
traces and assessments, without the tracking server, model registry or the
scientific stack the full `mlflow` package brings into an image. It is
pinned to the server's version in
[`docker/mlflow/Dockerfile`](../docker/mlflow/Dockerfile), and a test holds
the two together. The benchmark's runs need `mlflow-skinny`, which
`tests/requirements.txt` installs on the host; see
[`benchmarks/README.md`](../benchmarks/README.md#mlflow).

## Configuration

Every setting is an environment variable, most with a CLI override. Every one
of them is also forwarded by `docker-compose.yml`, so a setting works the same
way whether the agent runs in the container or on the host:

```bash
MAX_TABLES=6 docker compose run --rm agent "how many stores are there?"
```

A variable the host has not set arrives as an empty string, which is read as
unset rather than as a blank value -- otherwise forwarding a setting would
override its own default with nothing. A test checks the correspondence in
both directions: nothing is forwarded that the agent never reads, and nothing
the agent reads is missing from compose.

Every setting is an environment variable with a CLI override:

| Variable | Flag | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | `--base-url` | `http://192.168.10.82:11434` |
| `OLLAMA_MODEL` | `--model` | `qwen3.8-256k` |
| `OLLAMA_REASONING` | `--reasoning` / `--no-reasoning` | off |
| `OLLAMA_TEMPERATURE` | -- | 0.0 |
| `OLLAMA_NUM_CTX` | -- | 262144 (256k) |
| `OLLAMA_CONNECT_TIMEOUT` | -- | 5.0 seconds to decide the host is not there. Not a limit on answering |
| `OLLAMA_NUM_PREDICT` | -- | 2048 tokens at most per call. Raise it with reasoning on, since thinking counts against it |
| `OLLAMA_TIMEOUT` | -- | 600.0 seconds at most per call; a routed call that hits it, or the token cap, falls back |
| `DATABASE_URL` | `--database-url` | the compose Postgres, as the read-only `nl2sql_reader` role |
| `DB_SCHEMA` | -- | `public` |
| `MAX_ROWS` | `--max-rows` | 50 |
| `MAX_ATTEMPTS` | `--max-attempts` | 7 generations: one draft, six repairs (arch5; arch4 used 4) |
| `SAMPLE_ROWS` | `--sample-rows` | 3 |
| `STATEMENT_TIMEOUT_MS` | -- | 30000 |
| `RAG_ENABLED` | `--rag` / `--no-rag` | on |
| `VECTOR_DB_URL` | `--vector-db-url` | the compose pgvector |
| `EMBED_MODEL` | `--embed-model` | `bge-m3` |
| `EMBED_BASE_URL` | `--embed-url` | `http://host.docker.internal:11434` |
| `RAG_TOP_K` | `--rag-top-k` | 4 per collection |
| `RAG_MAX_CONTEXT_CHARS` | -- | 12000 |
| `EXAMPLES_ENABLED` | `--examples` / `--no-examples` | on |
| `MULTI_SHOT_ENABLED` | `--multi-shot` / `--no-multi-shot` | on |
| `CONTEXT_DB_URL` | `--context-db-url` | the compose chunkdb |
| `EXAMPLES_TOP_K` | `--examples-top-k` | 3 |
| `EXAMPLES_CANDIDATE_K` | -- | 10 per retriever |
| `EXAMPLE_WEIGHT_QUESTION` | -- | 0.50 |
| `EXAMPLE_WEIGHT_KEYWORDS` | -- | 0.35 |
| `EXAMPLE_WEIGHT_REASONING` | -- | 0.15 |
| `EXAMPLES_FUSION` | -- | `score` (or `rrf`) |
| `EXAMPLES_RRF_K` | -- | 60, used only by `rrf` |
| `EXAMPLES_RERANK` | -- | `mmr` (or `relevance`, `none`) |
| `EXAMPLES_RERANK_K` | -- | 8 candidates into the rerank |
| `EXAMPLES_RERANK_LAMBDA` | -- | 0.5 |
| `EXAMPLES_GROUNDING_WEIGHT` | -- | 0.25 |
| `EXAMPLES_MAX_CONTEXT_CHARS` | -- | 8000 |
| `SNIPPETS_ENABLED` | `--snippets` / `--no-snippets` | on |
| `SNIPPET_DB_URL` | `--snippet-db-url` | the snippets database in the compose runtime stores (`nl2sql-stores`, 6.3), as the read-only `snippets_reader` role, with its password from `SNIPPET_DB_PASSWORD_FILE` |
| `SNIPPETS_TOP_K` | `--snippets-top-k` | 5 |
| `SNIPPETS_MIN_SCORE` | -- | 0.35, the combined score a snippet must reach |
| `SNIPPETS_MIN_SIMILARITY` | -- | 0.62, the cosine similarity at which meaning alone qualifies a snippet |
| `SNIPPETS_MAX_CONTEXT_CHARS` | -- | 4000 |

Any Ollama model and host works:

```bash
docker compose run --rm agent --model gemma4:12b-mlx "How many stores are there?"
OLLAMA_BASE_URL=http://other-host:11434 docker compose run --rm agent "..."
```

Two notes on the defaults:

- The host is reached over **http**, not https -- port 11434 there does not
  terminate TLS, and an `https://` URL fails with an SSL error.
- `num_ctx` is set explicitly because Ollama otherwise caps context at a few
  thousand tokens regardless of the model's real limit, which silently truncates
  the schema prompt.

Reasoning is off by default. Qwen3 returns reasoning in a separate field, so
disabling it costs no output quality and saves a large share of the latency;
turn it on with `--reasoning` for harder questions.

## Worked examples (v3)

Retrieval in v2 fetches **prose**: business rules, table docs, grain warnings.
v3 adds a second, independent step that fetches **worked examples** -- the 45
question/SQL pairs in
[`context_questions/translated_questions.md`](../context_questions/translated_questions.md),
each one verified to run against this database and return rows.

The distinction is the point. The knowledge base can tell the model that costs
are monthly and sales are daily; the model can read that and still emit the join
that matches 24 days a year. A pair that has already reconciled the two grains
carries the shape of the answer, not just the warning.

### The ensemble

Three retrievers over the same golden pairs, each answering a different question
about a pair, fused into one ranking:

| Retriever | Searches | Where | Weight |
|---|---|---|---|
| Question similarity | `golden_pair_question_vectors` | vectordb (pgvector, cosine) | **0.50** |
| Keyword match | `golden_pairs.keywords` | chunkdb (BM25) | **0.35** |
| Reasoning similarity | `golden_pair_reasoning_vectors` | vectordb (pgvector, cosine) | **0.15** |

Question similarity matches what the user is asking for. BM25 catches the
vocabulary a paraphrase preserves but an embedding blurs -- `slotting`,
`fan-out`, `BOGO`. Reasoning similarity matches what the query has to get
*right* rather than what it asks, so a question that never says "fan-out" can
still reach the pair that warns about it; it is weighted low because on its own
it finds the right pair only 13% of the time.

BM25 is computed **in the database**, not in the client. Postgres ships
`ts_rank`, which is a length-normalised tf-idf and not BM25, so the term
statistics are materialised at load time and the scoring function is written
out as SQL -- see
[`golden_pairs_bm25()`](../rag/ragproc/golden_pairs.py). Over 45 short keyword
lists this costs nothing to maintain and keeps the ranking next to the data.

### The rerank

Fusing three rankings is not the same as reranking them. Every score the fusion
combines was produced *independently* -- the query against the pair's question,
against its reasoning target, against its keywords -- and no stage of that ever
looks at the query and the whole pair together, or at the retrieved set as a
set. So the top `EXAMPLES_RERANK_K` fused candidates get a second pass, in
[`rerank.py`](nl2sql_agent/rerank.py):

**Grounding** scores the query against the pair's `tables` and `sql_code`, which
*no* first-stage retriever indexes -- BM25 searches only the `keywords` column.
A question naming "Produce" should favour the pair whose SQL says
`department_name = 'Produce'`, and a quoted literal counts double an identifier
word, because `'Produce'` in a WHERE clause says what a pair is about while the
word `sales` inside a column name says almost nothing.

**MMR** then trades a little relevance for coverage. This is what multi-shot
needs: three near-identical exemplars teach one pattern three times.

Both were measured rather than assumed:

| | self-retrieval @1 | paraphrase @3 | entity @1 | names-a-table @3 | redundancy |
|---|---|---|---|---|---|
| fusion order | 45/45 | 12/15 | 10/10 | 5/8 | 0.560 |
| + grounding | 45/45 | 12/15 | 10/10 | **6/8** | 0.551 |
| + MMR (λ=0.5) | 45/45 | 12/15 | 10/10 | 6/8 | **0.514** |

*Redundancy* is the mean pairwise cosine between the three chosen pairs -- lower
means the model sees three different shapes of answer. *names-a-table* is the
query class grounding exists for; on the other three the first stage is already
saturated and grounding correctly changes nothing.

Recall never degrades, at any λ from 1.0 down to 0.3, and that is not luck:
MMR's first pick has nothing to be redundant with, so rank 1 is untouched by the
diversity term by construction. That is what makes the coverage free.

### How the three are fused

Each retriever's scores are min-max normalised across its own candidates, then
weighted and summed. Normalising first is what makes the weights mean anything:
a cosine similarity sits in a narrow band near 0.5 and a BM25 score is unbounded
and corpus-dependent, so weighting the raw numbers would weight incomparable
units.

Weighted reciprocal rank fusion -- what LangChain's `EnsembleRetriever` does --
is implemented too (`EXAMPLES_FUSION=rrf`) and was measured rather than assumed.
It is worse here. RRF scores a hit as `w / (60 + rank)`, a formula tuned for
candidate lists thousands of documents long; across 10 candidates rank 1 and
rank 10 differ by only 15%, which is less than the 0.15 a third retriever
contributes just by voting at all. The weights stop expressing how *strongly* a
retriever matched and start counting how *many* did.

Measured over all 45 questions retrieving their own pair at rank 1:

| Fusion | Correct |
|---|---|
| weighted score (default) | **45/45** |
| weighted RRF, k=1 | 43/45 |
| weighted RRF, k=10 | 37/45 |
| weighted RRF, k=60 (the usual default) | 25/45 |

On 15 hand-written paraphrases that deliberately avoid each pair's own wording,
the ensemble matches the best single retriever rather than beating it (10/15 at
rank 1, against 10/15 for question similarity alone and 6/15 for BM25 alone).
What the ensemble buys there is robustness, not a uniform lift: it is never much
worse than the best leg, and the keyword leg rescues the keyword-heavy questions
where embeddings drift.

### Multi-shot generation

The retrieved pairs are **not** pasted into the prompt as a block of text. They
are replayed as real conversation turns in front of the actual question:

```
system     rules, then the schema and sample data
human      Rule that applies here: <pair 1 reasoning target>
           Question: <pair 1 question>
ai         <pair 1 SQL>
human      Rule that applies here: <pair 2 reasoning target>
           Question: <pair 2 question>
ai         <pair 2 SQL>
...
human      <knowledge block>
           Question: <the real question>
```

That shape is what instruct models are tuned on. A text block invites the model
to *describe* the examples; a turn sequence invites it to *continue the pattern*.

Three properties hold it together:

- **The turns are symmetric with the real one.** Every human turn is [the rule
  that applies] + [the question]. For an exemplar the rule is its
  `reasoning_target`; for the real question it is whatever the knowledge base
  returned. An exemplar shaped differently from the real task demonstrates the
  wrong task.
- **Assistant turns are bare SQL.** Anything they contain gets imitated -- which
  is also why they carry no leading SQL comment: `ensure_read_only` requires the
  statement to begin with SELECT or WITH, so a model that learned to prefix a
  comment would have every query rejected.
- **The schema lives in the system turn.** It is shared by every turn, so
  repeating it per exemplar would cost tokens and say nothing new.

The system turn also states what the earlier turns *are*, and that the question
to answer is the last one. Without that a model can read the exemplars as prior
user requests still waiting to be answered.

### Two switches, not one

`EXAMPLES_ENABLED` controls retrieval; `MULTI_SHOT_ENABLED` controls whether the
pairs are replayed as turns. Both default on, but they stay separate: with the
second off the examples are still fetched, ranked and visible in `--json`, so
the ranking can be inspected without it steering generation. Off, the prompt is
byte-for-byte the zero-shot one -- a system turn, then the question.

```bash
docker compose run --rm agent --json "gross margin for produce" | jq .example_pairs
docker compose run --rm agent --no-multi-shot "gross margin for produce"
```

### Degradation

Three separate things can be down -- the context store, the vector store, and
the embedding host -- and all three surface as `ExamplesUnavailableError`, which
`search_examples` turns into an empty context plus a reason on
`state.examples_error`. A run without examples is a v2 run; a run without either
retrieval is a v1 run.


## SQL snippets (v5.6)

The knowledge base says how the database works and the golden pairs show
whole questions answered. A snippet is the piece in between: one join, one
filter, one measure or one dimension, written as SQL that ran against this
database, beside what it means in a question's words. "Store brands" is
`p.is_private_label`; "transactions" is `COUNT(DISTINCT f.basket_id)`; sales
reach the fiscal calendar through `f.sales_date_key`, not the date itself.
They are curated in
[`context_questions/sql_snippets.md`](../context_questions/sql_snippets.md)
and loaded by [`rag/07_load_snippets.py`](../rag/07_load_snippets.py) into a
store of their own, which the agent reads as `snippets_reader`, a role that
can only `SELECT`. The code is
[`nl2sql_agent/snippets.py`](nl2sql_agent/snippets.py).

**Two signals, because each misses what the other finds.**

| Signal | Searches | Finds |
|---|---|---|
| keywords | the phrases a curator listed, and the snippet's name, matched in the store | "private label" when the question says it |
| meaning | an embedding of what each snippet means (pgvector, cosine) | "spend per trip" for average basket value |

A keyword phrase matches when every one of its words is in the question, in
any order and inflection, once the stopwords are gone, so "stores" alone
does not reach the store-brand snippet the way a bag of words would. A match
weighs the IDF of its words, summed. Each signal is put on a fixed 0..1
scale: keywords as `1 - exp(-weight / 3)`, so a word one snippet in thirty
uses counts about 0.64 and a two-word phrase of them about 0.87; meaning
between a cosine of 0.35 (unrelated) and 0.65 (as close as a question gets to
one snippet). The two are averaged. A snippet qualifies when a phrase
matched or when its meaning alone reaches `SNIPPETS_MIN_SIMILARITY`. It is
kept when the average reaches `SNIPPETS_MIN_SCORE`, and at most
`SNIPPETS_TOP_K` are kept, best first.

Neither scale is relative to the question's best candidate, for opposite
reasons. A relative meaning scale would call the nearest of several
unrelated snippets a perfect match. A relative keyword scale did worse in
practice: in "What was our click-through rate by channel on weekends in
fiscal year 2025?", the long phrase "click-through rate" pushed an exact
"weekends" and "fiscal year" below the bar. A question that combines a
measure, a filter and the join between them is the case snippets are for.

**Shown only when their tables are in scope.** The retriever runs beside the
other four and proposes no tables. The Context Aggregator keeps the snippets
whose tables are all in the selected set, renders them -- kind, name,
meaning, the `FROM` clause, the SQL and the curator's note -- under their own
instruction after the knowledge block, and recomputes them when a repair
widens the scope. The instruction says to keep a piece exactly as written,
changing only aliases and literals, and the note is there for the same
reason: in a live run, shown the click-through rate's
`SUM(clicks)::numeric / NULLIF(SUM(impressions), 0)` without its note --
"both counts are integers" -- the generator kept the division, dropped the
cast, and every rate came back 0. With the note and the instruction it kept
the cast. A
snippet is a hint about tables already in play, never a reason to bring one
in: that is the schema retriever's decision. With none in scope the block is
empty and the prompt is byte for byte the one 5.5.1 sent.

**Degradation.** The keyword half needs only the store; the meaning half
needs the embedding host too. With the host down the keyword half still
answers and the trace says why meaning sat out. With the store down,
`search_snippets` returns an empty list and a reason, recorded in
`retrieval_errors` like any other retriever's. `--no-snippets` turns it off.

## Safety

v3 reviewed generated SQL with a model call. The benchmark showed that cost
499 of 1499 seconds and that it was the only component in 45 runs ever to
turn a correct answer into no answer, so v4 removed it. What it was reaching
for now happens twice, in better places: the structural half deterministically
before execution, and the semantic half after it, where there are real rows to
check against.

Generated SQL is untrusted, so execution has three independent layers:

1. **Static check** (`ensure_read_only`) -- must be a single statement starting
   with `SELECT` or `WITH`.
2. **`READ ONLY` transaction** with a statement timeout -- this is what stops a
   data-modifying CTE such as `WITH d AS (DELETE ... RETURNING *) SELECT * FROM d`,
   which is a legitimate `WITH` query as far as layer 1 is concerned.
3. **A read-only database role** -- the agent connects as `nl2sql_reader`, which
   holds `SELECT` on the tables and nothing else (see
   [`docker/reader_role.sql`](../docker/reader_role.sql)). The owner that loads
   the data is never in the agent's `DATABASE_URL`, so a write that somehow got
   past the first two layers is refused by Postgres itself with
   `permission denied`. The role also cannot connect to the cluster's other
   databases, and cannot cancel or terminate another session: every reader
   session is the same role, and Postgres lets a role signal its own, so the
   two signalling functions are revoked in the retail database. The static
   check's function denylist refuses both names too, but a denylist is the
   agent being careful; the revoke is the server saying no.

Layers 1 and 2 are tested in `tests/agent/test_database_safety.py` and
`tests/agent/test_database_live.py`; layer 3 in
`tests/agent/test_least_privilege_live.py`, which asks the live catalog what
the role holds and then tries every write path anyway -- and the other
databases, and ending another reader's session. The last two need a
started stack and `pytest --run-docker`.

Results are capped at `--max-rows`, and the flag reports when output was
truncated rather than silently cutting it off.

## Retrieval

The knowledge base is a pgvector database with one collection per document in
[`knowledge/`](../knowledge), each row a semantically chunked section with its
bge-m3 embedding, the heading path it came from, and the authored `chunk_meta`
(`table`, `domain`, `grain`, `keywords`).

`retrieval.py` embeds the question once and runs a cosine search against every
collection, so one question picks up DDL detail, dictionary context and
business rules together. Collections are **discovered from the catalog**, not
hardcoded: adding a document to the RAG pipeline makes it searchable with no
code change here.

What the retrieved chunks feed:

| Consumer | What it gets |
|---|---|
| `select_tables` | the context, plus `chunk_meta.table` names merged into the model's own choice |
| `generate_sql` | the context, marked authoritative over the model's assumptions |
| `validate_sql` | the context, so a rule violation is a reported problem, not a pass |

`--json` includes the `chunk_id`, source document and distance of every chunk
used, so an answer can be traced back to the text that shaped it.

### Why retrieval

Asked *"What is our overall market share across all tracked products in fiscal
year 2024?"*, the two versions diverge:

| | Answer | What it did |
|---|---|---|
| v1 (`--no-rag`) | **107.5%** | Summed `grocer_sales_amount` across the five competitor rows per cell, inflating the numerator |
| v2 | **21.5%** | Retrieved *"Market share fan-out: the five-row trap"* and de-duplicated per (week, product, region) cell on the first attempt |

`fact_market_share_weekly` repeats the same `grocer_sales_amount` and
`total_market_sales_amount` once per competitor. Nothing in the schema says so;
the business index does.

### Degradation

Retrieval is best-effort. If the vector store or the embedding model is
unreachable, the node records why, the prompts drop the knowledge block
entirely, and the run continues schema-only -- exactly v1 behavior. A missing
knowledge base slows the agent down; it never fails a question that v1 could
have answered.

```
[knowledge] skipped: Could not search the knowledge base: connection failed ...
[tables] dim_store
```

### The embedding host is not the chat host

Queries must be embedded with the same model the store was built with, or the
vectors land in different spaces and retrieval returns confident noise. `bge-m3`
typically runs on the machine hosting Docker, while the chat model runs on a
remote Ollama -- hence separate `EMBED_BASE_URL` and `OLLAMA_BASE_URL`. The
live test `test_store_was_built_with_the_model_we_query_with` checks the store's
recorded `embedding_model` against the configured one.

## Adding steps later

Each step is a node with plain-dict state, so a new step is a node plus an edge:
add a field to `AgentState` in `graph.py`, add a node that populates it, point
the edges at it, and include the field in the relevant prompt. The retry loop,
validation, and execution are independent of how the prompt was assembled.
Tools are constructed by `build_tools()` in `tools.py` and are ordinary
LangChain tools, so they can also be bound to a tool-calling model if you later
want the model to choose its own sequence rather than following a fixed graph.


The v4 pipeline adds these. Each one names a stage the architecture makes
optional, so an ablation is an environment change rather than a code change:

| Variable | Flag | Default |
|---|---|---|
| `SUPERVISOR_ENABLED` | -- | on |
| `CLARIFY_ENABLED` | -- | off (batch and benchmark runs have nobody to answer) |
| `SCHEMA_RETRIEVAL` | -- | `vector` (or `llm` for v3's table-selection call) |
| `SCHEMA_TOP_K` | -- | 6 tables from the DDL vectors, before FK closure |
| `MAX_TABLES` | -- | 10 after closure |
| `LITERALS_ENABLED` | -- | on |
| `LITERAL_MAX_DISTINCT` | -- | 500 distinct values per catalogued column |
| `LITERAL_MIN_SCORE` | -- | 0.6 |
| `MAX_PLAN_COST` | -- | 1000000 |
| `NARRATE_ENABLED` | -- | on |
| `AUDIT_ENABLED` | -- | on |
| `REVIEW_ENABLED` | -- | on: the arch5 Completeness Reviewer. Off, results go straight to presentation, but a default fiscal year the generator applied is still stated |
| `REVIEW_REFLECTION_ENABLED` | -- | on: the reviewer's one reflective model call. Off keeps its rules and drops the call |
| `MAX_SQL_ATTEMPTS` | -- | v3's name for `MAX_ATTEMPTS`; still honoured |

`MAX_PLAN_COST` is calibrated against this dataset rather than chosen: the most
expensive of the 45 golden pairs plans at 125,767 and a full scan of the sales
fact at 20,096, while that fact cross-joined with `dim_product` is 1.6 million
and with itself 12.5 billion. The default is eight times the hardest known-good
query and below the cheapest cross join involving the fact table, so it rejects
runaway plans without rejecting real work. Re-derive it the same way whenever
the data is regenerated, since plan costs scale with row counts.

Model routing (arch5.2, [above](#model-routing-arch52)) adds these:

| Variable | Flag | Default |
|---|---|---|
| `MODEL_ROUTING_ENABLED` | -- | on. Off is v5.1: every call to `OLLAMA_MODEL` |
| `MODEL_CATALOG` | -- | none, so every rung is `OLLAMA_MODEL`; compose mounts the committed [`models/catalog.json`](../models/catalog.json) at `/app/models/catalog.json` and names it |
| `MODEL_ROUTE_ON_PRIOR` | -- | off: route only on what calibration measured |
| `MODEL_ROUTE_SUPERVISOR` | -- | none. A model for every rung, or `light=a,standard=b,heavy=c` |
| `MODEL_ROUTE_GENERATOR` | -- | none, as above |
| `MODEL_ROUTE_REFLECTION` | -- | none, as above |
| `MODEL_ROUTE_NARRATOR` | -- | none, as above |
| `MODEL_ROUTE_REPAIR` | -- | none, as above |
| `MODEL_MAX_LOADED` | -- | 3 distinct models in the table, `OLLAMA_MODEL` among them |
| `MODEL_NUM_CTX` | -- | 32768, the window of every routed model but `OLLAMA_MODEL` |
| `OLLAMA_KEEP_ALIVE` | -- | `30m` with routing on, so a session's models stay loaded; not sent with routing off |

The ensemble ([above](#asking-it-several-ways-arch7)) adds these. A value
out of range stops the agent at start, naming the variable and its bound;
all but the pins are published in `/v1/meta`:

| Variable | Flag | Default |
|---|---|---|
| `ENSEMBLE_ENABLED` | `--ensemble` / `--no-ensemble` | on. Off is the pipeline alone, to the answer |
| `ENSEMBLE_PARAPHRASES` | `--paraphrases` | 3 rewordings in the first wave; 3 to 10 |
| `ENSEMBLE_MAX_PARAPHRASES` | -- | 10, the most the Paraphraser writes; at least `ENSEMBLE_PARAPHRASES` |
| `ENSEMBLE_WAVES` | -- | 2; a second wave only when the vote does not settle the question. It runs every faithful rewording left, so more than 2 is 2 |
| `ENSEMBLE_DEADLINE_SECONDS` | -- | 0, none: set, no wave starts that many seconds after the question arrived |
| `ENSEMBLE_JUDGE_ENABLED` | -- | on: the Judge reads every question's answers before the vote, which counts only those it accepts. Off, the runs vote alone |
| `ENSEMBLE_MAX_CLAIMS` | -- | 8 claims in the fused narrative |
| `ENSEMBLE_FUSE_COLUMNS` | `--fuse-columns` / `--no-fuse-columns` | on: columns other agreeing runs carried, joined on the entity's key |
| `OLLAMA_PARALLEL_CALLS` | `--parallel-calls` | 1 model call in flight to the host at once, and one run of a question at a time |
| `MODEL_ROUTE_PARAPHRASER` | -- | none. A model for every rung, or `light=a,standard=b,heavy=c`, as the five pins |
| `MODEL_ROUTE_JUDGE` | -- | none, as above |

Tracing ([above](#tracing-mlflow)) adds these:

| Variable | Flag | Default |
|---|---|---|
| `MLFLOW_TRACKING_URI` | -- | none: nothing is traced. `setup.sh` writes `http://nl2sql-mlflow:5000`, the compose `mlflow` service, into `.env`; empty there turns tracing off |
| `MLFLOW_EXPERIMENT_NAME` | -- | `nl2sql-agent`, created on first use |

## Running outside Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
DATABASE_URL="postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail" \
VECTOR_DB_URL="postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors" \
EMBED_BASE_URL="http://localhost:11434" \
  python -m nl2sql_agent "How many stores are there?"
```

Run it from the `agent/` directory so `nl2sql_agent` is importable. Note that
the host URLs use `localhost` rather than the `postgres` / `vectordb` hostnames
that only resolve inside the compose network, and `localhost` rather than
`host.docker.internal` for the embedding host.
