# Benchmark

Fifteen questions, scored on **accuracy first and speed second**.

```bash
./launch.sh                               # the stack has to be up
python benchmarks/run_benchmark.py        # every retrieval stage, one run a question
python benchmarks/run_benchmark.py --config ensemble   # the same, asked through the ensemble (arch7)
python benchmarks/run_benchmark.py --compare   # schema-only vs knowledge vs multi-shot vs snippets vs ensemble
```

## What is measured

**Accuracy is execution accuracy.** The agent's SQL is run, and its rows are
compared against a reference query whose answer was verified against the shipped
dataset. Comparing query *text* would be close to useless: two correct queries
for the same question rarely look alike, so text comparison fails the right
answer for naming an alias differently and passes the wrong one for joining on
the wrong key.

The comparison is forgiving about presentation and strict about values:

| | |
|---|---|
| Column names and order | ignored -- `count(*) AS n` and `AS store_count` are the same answer |
| Extra columns | allowed -- returning the department beside the total still answers the question |
| Missing columns | not allowed |
| Row order | ignored, unless the question asks for an order |
| Numbers | equal within 1e-6 relative, **or** equal once both are rounded to 2 decimals |

The rounding half of that rule is not a convenience. The reference queries round
to 2 places for legibility and agents mostly do not, so a pure relative
tolerance rejects `48.6076` against a reference `48.61` -- off by 5e-5, thirty
times 1e-6, and obviously the same number. It stays far from any real mistake:
the grain trap returns 98.81 against 34.20, and the fan-out is wrong by 5x.

One subtlety worth knowing, because getting it wrong would invalidate every
number the benchmark prints. Allowing reordered columns *and* ignoring row order
means the rows on each side cannot simply be sorted and zipped -- sorting by a
different column order produces a different row order. So the scorer searches
column assignments: is there a choice of the agent's columns under which its
rows **are** the reference rows? Checking each reference column independently is
the tempting shortcut, and it accepts a result whose values are all present but
attached to the wrong rows -- which is exactly what a join on the wrong key
produces.

**Speed is reported per question and per pipeline stage.** The graph calls back
as each node finishes, so the gaps between those callbacks are stage durations.
"Slow" and "slow in `generate_sql`" call for different fixes.

## The questions

Fifteen, spread across five categories. They were written apart from the
golden pairs the agent retrieves from -- a benchmark drawn from those would
measure how well it can look something up, which is not the thing worth knowing.

Two of them have since become golden pairs. The golden set grows from reviewed
feedback (since 4.4), and when users' verdicts on **B03** and **B14** were
promoted (5.4) their questions became **Q46** and **Q47**, word for word. They
stay in the benchmark by the owner's decision, so the fifteen stay comparable
across releases; read their scores as lookup, not generalisation. A test
(`tests/benchmarks/test_questions.py`) holds the overlap to exactly those two,
each named with the pair it became, so a third is a failure to look at rather
than a quiet change in what the benchmark measures.

| Category | n | What it tests |
|---|---|---|
| `schema` | 3 | Answerable from table and column names alone. The floor. |
| `calendar` | 3 | FY2025 is 2024-04-01 .. 2025-03-31. Reading it as a calendar year is wrong by nine months with no error to show for it. |
| `grain` | 3 | Sales are daily, costs and allowances monthly, prices weekly. Joining across them on `date_key` matches almost nothing. |
| `fan-out` | 1 | `fact_market_share_weekly` repeats each cell once per competitor; summing raw overstates by exactly 5x. |
| `analysis` | 5 | Correct but non-trivial: distinct-count denominators, ratios of sums, two-hop dimension bridges. |

Each question records the **trap** it was built around, so a failure report can
say whether the agent fell into it rather than only that a number was wrong.

Two are worth calling out. **B07** asks for a gross margin across the
daily/monthly grain boundary; the naive join matches only the 24 month-start
days in the dataset, and the schema-only agent answers 98.81% against a true
34.20%.

**B08** asks for overall market share, and is subtler than it looks. Summing
both columns raw inflates each by 5x and the *ratio survives* -- 21.51% either
way. The mistake that yields 107.5% is asymmetric: a raw numerator over a
de-duplicated denominator. In the measured run the schema-only agent produced
neither. It wrote a correct query, rejected it three times in its own validation
step, and gave up.

## Measured

All three configurations, same 15 questions, `qwen3.8-256k` at 256k context:

| | accuracy | produced SQL | retries | total | median |
|---|---|---|---|---|---|
| `schema-only` | 12/15 (80%) | 14/15 | 5 | 511s | 21.0s |
| `knowledge` | **15/15** | 15/15 | 0 | 1251s | 81.8s |
| `multi-shot` | **15/15** | 15/15 | 0 | 1499s | 100.5s |

| | analysis | calendar | fan-out | grain | schema |
|---|---|---|---|---|---|
| `schema-only` | 5/5 | 3/3 | **0/1** | **1/3** | 3/3 |
| `knowledge` | 5/5 | 3/3 | 1/1 | 3/3 | 3/3 |
| `multi-shot` | 5/5 | 3/3 | 1/1 | 3/3 | 3/3 |

Every question schema-only missed is a grain or fan-out question, and the
knowledge base fixes all of them while also removing every retry.

**This set cannot distinguish `knowledge` from `multi-shot`.** Both score 15/15,
so there is no headroom left to measure once the knowledge base is on. That is a
limit of the benchmark, not a finding about the examples -- and it is the first
thing to fix if the examples are to be evaluated at all. Harder questions, or
scoring partial credit, would both help.

Retrieval is not where the time goes. Across 15 questions the knowledge base and
the golden-pair ensemble together take **1.2 seconds**; `generate_sql`,
`validate_sql` and `select_tables` take 634s, 499s and 364s.

### The multi-agent pipeline

The default configuration is the whole agent, so the same fifteen questions
measure the v4 pipeline and arch5's additions to it:

| | accuracy | total | median | model calls / question | narrative traced |
|---|---|---|---|---|---|
| v4 | **15/15** | 909.7s | 61.2s | 3.7 | 84.6% |
| v5 (arch5) | **15/15** | 991.6s | 63.8s | 3.7 | 83.3% |

v5's Completeness Reviewer reflected on the three questions whose rows name
an entity -- B09, B10 and B13 -- for 16.1s in all. Treat the totals as one
run each: wall time against the shared Ollama host varies by about 30%
between identical runs. [`agent/README.md`](../agent/README.md#measured) has
what the first three v5 runs found and what each changed.

### What the first run got wrong

The first run of this benchmark reported 9/15, 10/15 and 15/15, and the
conclusion drawn from it -- that the knowledge base barely helped and multi-shot
carried the difference -- was an artefact of the harness. Two defects:

- **The tolerance rejected the benchmark's own rounding.** References use
  `ROUND(..., 2)` and agents mostly do not, so `48.6076` against a reference
  `48.61` failed a 1e-6 relative tolerance. That alone cost four correct answers.
- **Four questions admitted two right answers.** "Which department had the
  highest net sales" is answerable with a bare name; "market share" is as
  legitimately a fraction as a percentage; an allowance type is identified
  equally well by its code or its name.

Both are fixed and pinned by tests. It is worth recording because the failure
mode is quiet: a benchmark that is too strict does not look broken, it looks
like a system that is performing badly.

## Reading the output

```
ACCURACY
  execution accuracy   12/15  (80.0%)
  produced runnable SQL 14/15

  by category
    analysis   5/5  #####
    calendar   3/3  ###
    fan-out    0/1  .
    grain      1/3  #..
    schema     3/3  ###
```

Two numbers, not one. **Execution accuracy** is how often the answer was right;
**produced runnable SQL** is how often there was an answer at all. A pipeline
that gives up is failing differently from one that confidently answers the wrong
question, and the fixes are different.

The category breakdown matters more than the headline. That run is
`schema-only`: 12/15 looks respectable until the breakdown shows every miss
sitting in grain and fan-out, which is a retrieval problem and not a scattering
of bad luck.

```
SPEED
  total              1499.0s for 15 questions
  median per question 100.5s

  where the time went
    generate_sql           634.0s   42.3%  ##############
    validate_sql           499.4s   33.3%  ###########
    select_tables          363.9s   24.3%  ########
    retrieve_knowledge       1.1s    0.1%
    retrieve_examples        0.1s    0.0%
```

Median rather than mean, because one retry loop doubles a mean and says nothing
about a typical question. Stage totals sum repeats: `generate_sql` runs again on
every retry, and the useful number is the total.

### Where the timing comes from

For a v3 run the harness times the gaps between the agent's progress callbacks:
the graph calls back as each node finishes, so the gap is that node's duration.
The v4 pipeline breaks that assumption. Its Stage 1 retrievers -- four in v4,
five since v5.6 -- are branches of one superstep and run concurrently, so the gap after one of them is not its
duration.

So each v4 node times itself and reports the result in `state["trace"]`, and the
harness prefers that when it is there. The same entries carry a model-call
count, which is what makes the architecture's economic claim checkable rather
than merely stated: the happy path should cost three model calls -- four when
arch5's Completeness Reviewer reflects on rows that name an entity -- and the
repair agent's classifier should keep most retries from costing another.

A v4 run also scores the **narrative**: the fraction of the answer's claims that
the audit could trace back to a cell of the result set. Execution accuracy says
whether the SQL was right; this says whether the user was told the truth about
it, which is a different failure and one nothing in the v3 harness could see.

### Which model answered (arch5.2)

With model routing, each trace entry that called a model also names the
model that answered, the rung it was routed at, and every routed model that
failed first. The report reads them into a table per agent, rung and model:
how many calls, how many of the questions it touched came out right, the P50
of those calls, and the hops. A question counts toward every route its run
used, which is the calibration's own measure taken on questions in sequence,
with the models warm and cold as they are in use. Above it, how the
generator's task was scored across the set -- a rung nothing is ever routed
at is visible there. `--json` carries both, and each question's routes.

### What the grain check did (arch7.3)

With the grain check on (`GRAIN_CHECK_ENABLED`, the default), every
configuration's report ends with what it did:

```
GRAIN CHECK
  references refused   none
  runs sent to repair  2: ended right 2, wrong 0, failed 0
    B07 run 0  refused once, ended ok
    B07 run 2  refused once, ended ok
```

**References refused** is the check run over the benchmark's own reference
queries with the grains this run read from the database. They are right by
construction, so one named there is the check wrong about a right query,
and the benchmark exits non-zero whatever the answers scored.
**Runs sent to repair** counts the runs in which the Static Validator
refused a query for its grain at least once -- a run is a question for the
first four configurations and one wording of it through the ensemble --
with how each ended against the reference: right, wrong, or no rows.
"Ended wrong" is a query that was repaired and still missed, or a right
query sent to repair and broken by it; the query and the message are in
the question's trace, which tells the two apart. To measure the check's
effect, run the same questions again with `GRAIN_CHECK_ENABLED=false` in
the environment: the block is then left out. `--json` carries a `grain`
block for each configuration (`rejected_references`, and `repairs` with
the four counts; `null` with the check off) and each result's
`grain_repairs`.

## Comparing configurations

`--compare` runs the same questions through five configurations, each the
one before it plus **one** stage -- four of retrieval, then the ensemble --
so a difference between two rows is attributable to that stage and nothing
else:

| | Knowledge base | Worked examples | SQL snippets | Ensemble | Equivalent to |
|---|---|---|---|---|---|
| `schema-only` | off | off | off | off | v1 |
| `knowledge` | on | off | off | off | v2 |
| `multi-shot` | on | on | off | off | v3 to v5.5 |
| `snippets` | on | on | on | off | v5.6 to 6.3, one run a question, and the default |
| `ensemble` | on | on | on | on | 7.0, the agent as it ships: the question asked several ways (arch7) |

Every configuration sets the ensemble on or off itself, so the first four
are one run a question whatever `ENSEMBLE_ENABLED` says. An ensemble answer
is scored on what it delivered -- its SQL and rows; its attempts, examples
and rung are the delivered run's; and its time and model calls are every
run's and the ensemble's own steps', the step that ran the candidates left
out so its time is not counted twice. The report gains an ENSEMBLE block:
how the runs agreed, by level; how often they agreed on a wrong answer; the
runs per question; the rewordings the fidelity gate discarded, by check;
how often a question's runs were scored at different rungs; the state's
size; what the Judge did, question by question; and, where it overruled the
runs, the runs' own choice scored as the delivered answer is -- "wrong ->
right" and "right -> wrong" counted apart, so the Judge's effect shows both
ways; how many questions took a second wave; and fusion's yield -- the
columns joined from other agreeing runs and those declined, the claims they
added and those of theirs the delivered rows did not bear out. `--json`
carries each question's runs, rewordings, the Judge's verdicts, that second
score (`without_judge`), whether a second wave ran (`wave2`) and what fusion
joined, declined, added and dropped
([`docs/benchmark.md`](../docs/benchmark.md#the-ensemble-configuration)).

**Under the pipeline's name (8.0).** A configuration is checked against
`PIPELINE_VERSION` before anything is asked. Under `v6_3_1`, which asks each
question once, `--config ensemble` is refused with the agent's own reason --
`PIPELINE_VERSION v6_3_1 asks each question once; ENSEMBLE_ENABLED=true
contradicts it` -- rather than run and labelled with the wrong name, and
`--compare` runs the four one-run configurations and says below the table
why the fifth was not run. The report's heading names the pipeline beside
the configuration (`snippets  (pipeline v6_3_1)`), the comparison opens with
it, and `--json` carries it for each configuration and, as each run's state
named it, for each question
([`docs/benchmark.md`](../docs/benchmark.md#the-pipeline-by-name)).

The snippet store is found on the runtime stores' published port,
`localhost:5435` (6.3), unless `SNIPPET_DB_URL` says otherwise; each
database's password is the one the stack generated, read from `secrets/`
when the URL's variable is unset. The measurements below predate the
`snippets` configuration.

## The paraphrase set

```bash
python benchmarks/run_benchmark.py --paraphrase-set
```

Each question asked four ways: as the benchmark asks it, and as each of the
three rewordings in [`paraphrases.py`](paraphrases.py) asks it -- sixty
wordings in all, each scored against its question's one reference query,
since a rewording asks the same question and so has the same answer. The
accuracy is then over sixty, and the report adds the **stability**: the
fraction of questions every wording of which came out right, with each
question's wordings marked in order, its own first:

```
STABILITY  (the paraphrase set: each question in its own words and three others)
  stable questions     13/15  (86.7%)  every wording right
  own wordings         14/15
  rewordings           43/45

  per question                    wordings 0 1 2 3
    B06 calendar   4/4  ok ok ok ok
    B07 grain      3/4  ok ok WRONG ok
```

It is Phase 0 of the next architecture
([`Multi-Agent_NL2SQL_arch7.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7.md),
section 10), which asks a question several ways and compares the answers --
worth what that costs only if the pipeline's answer does depend on the
wording. This measures how often, before anything is built on it; the
number is in [`docs/benchmark.md`](../docs/benchmark.md#the-paraphrase-set).

The rewordings follow the rules arch7 gives the model that will write them
(section 22.3): every number, year, name and quoted value kept; the same
entities, measure, period, direction and row count; no period, filter or
measure added and none dropped; the words and the shape varied. Each was
checked by hand against its question's reference SQL, and
[`tests/benchmarks/test_paraphrases.py`](../tests/benchmarks/test_paraphrases.py)
holds every one to the fidelity gate's checks in code
([`fidelity.py`](../agent/nl2sql_agent/fidelity.py): numbers, literals,
polarity, distinctness) against its question. A rewording a person judged
faithful that the checks would discard is a failing test, and the checks'
word lists grow from it, as they first did from these.

`--json` gives each result its `wording` -- 0 the benchmark's own, 1 to 3 a
rewording -- with the words asked as its `question`, and each configuration
a `stability` block: the fraction, the stable count, and every question's
outcomes in wording order; `null` for a run without the set.

## The follow-up set

```bash
python benchmarks/run_benchmark.py --followups --config snippets
python benchmarks/run_benchmark.py --followups --config ensemble
```

Each question's second turn: the question a person types after seeing its
answer, written by hand in [`followups.py`](followups.py) -- "And by
banner?", "The same for FY2024?", "What were its top 2 categories?" --
each a fragment that borrows a measure, a period, an entity or a filter
from the first turn, with the complete question it means and that
question's reference query. The follow-up is asked in the words typed and
scored against the complete question's reference; the report adds the
**follow-up accuracy** and how often the delivered SQL kept the filters the
first turn set (`fiscal_year = 2025`, `'Meat & Seafood'`), with each
follow-up's outcome and its filters kept. The start of the `snippets` run
of 2026-10-16:

```
FOLLOW-UPS  (the follow-up set: the question a person asks after each answer)
  turns remembered     0  (--memory; at 0 each follow-up is asked alone, as 7.0 asks every question)
  follow-up accuracy   1/15  (6.7%)
  filters kept         3/14  the first turn's, in the delivered SQL

  per follow-up
    B01.f  schema     ok     And how many in each state?
    B02.f  schema     WRONG  Break that down by department.  kept 0/1
    B03.f  schema     FAILED What's the average square footage for each?
    B04.f  calendar   WRONG  The same for FY2024?
    B05.f  calendar   WRONG  What were its top 2 categories?  kept 1/2
    ...
```

It is Phase 0 of the next architecture
([`Multi-Agent_NL2SQL_arch8.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8.md),
section 10), which remembers the conversation so that a follow-up is read
as the complete question it means -- worth building only if the pipeline,
which answers every question alone, answers such a fragment wrongly. This
measures how often, before anything is built on it; the number is in
[`docs/benchmark.md`](../docs/benchmark.md#the-follow-up-set).
`--memory N` is how many earlier turns a follow-up is asked with, 0 to 15,
by default `CONVERSATION_MEMORY`'s. At 0 the fragment is asked alone, which
is what 7.0 does with a follow-up (Phase 0). Above it (Phase 2) the first
turn -- the benchmark question in its own words -- is asked first and handed
on as the conversation's one turn, and the section adds how the first turns
scored, the **resolution rate** -- the follow-ups whose restatement held and
whose reading builds the contract the complete question's own reading does,
which costs the benchmark one Supervisor call a follow-up -- and why the
others were not resolved, counted by check (`C1 numbers`, `C2 literals`,
`C3 polarity`, `C4 shape`) or as `not restated`; and under each follow-up,
what it was read as, or why not.

Each standalone follows the rules arch8 gives the Supervisor that will
write them (section 23.3): every number, name and quoted value of the
fragment kept as written, and nothing added that the first turn does not
supply. Each was checked by hand against its fragment and its first turn,
and [`tests/benchmarks/test_followups.py`](../tests/benchmarks/test_followups.py)
holds every one to the checks in code a model's restatement will meet
([`memory.py`](../agent/nl2sql_agent/memory.py): numbers, literals,
polarity, shape) against its fragment.

`--json` gives each result its `followup_of` -- the question it follows,
whose id it is filed under -- with the words asked as its `question` and
its `keeps`, each filter of the first turn's with whether the delivered SQL
kept it, and with memory its `conversation`: the turn, the turns
remembered, whether a restatement held and as what or why not, how the
first turn scored (`first`) and whether the reading built the complete
question's contract (`contract`). Each configuration has a `followups`
block: the depth, the accuracy, the filters kept and the consistency, their
fraction (`null` when no reference keeps one), the resolution rate (`null`
with nothing remembered) and the reasons not resolved (`not_resolved`);
`null` for a run without the set.

## The independence

```bash
python benchmarks/run_benchmark.py --independence --config ensemble
python benchmarks/run_benchmark.py --independence same --config ensemble
```

Memory that changes a question which stands alone is a cost with no
benefit, and arch8 holds the default depth to it (section 11): each of the
fifteen is asked alone, and then again as the second turn of a conversation
-- after the question before it in the list, the first after the last
(`previous`, the default), or after itself (`same`, the nearest thing to a
person asking twice) -- and the **independence** is the fraction answered
exactly as alone, by the rows. It must be 15 of 15. The report's
INDEPENDENCE section says it, how many of the second turns were restated
though each stands alone, and the model calls the second turns made against
the same questions alone, which memory must not move; and lists every
question that came out differently or was restated, with its reading.
Each second turn is labelled `B07.i`; `--json` gives it `after`, the
question it was asked after, and `independent`, and each configuration an
`independence` block (`null` for a run without it).

## Options

```bash
python benchmarks/run_benchmark.py --only B07 B08      # just these
python benchmarks/run_benchmark.py --category grain    # just this category
python benchmarks/run_benchmark.py --paraphrase-set    # each question four ways: the stability
python benchmarks/run_benchmark.py --followups         # each question's follow-up: the follow-up accuracy
python benchmarks/run_benchmark.py --followups --memory 0  # with nothing remembered, as 7.0 asks
python benchmarks/run_benchmark.py --independence      # each question again after another: unchanged?
python benchmarks/run_benchmark.py --json results.json # machine-readable
python benchmarks/run_benchmark.py --verbose           # every pipeline step
python benchmarks/run_benchmark.py --model other-model --base-url http://host:11434
```

It exits non-zero when anything was not correct, or when the grain check
would refuse a reference query, so CI can gate on it.

The database URLs default to the **published ports on localhost**, because the
benchmark runs on the host while the agent normally runs inside compose, where
the same names are service names. Set `DATABASE_URL`, `VECTOR_DB_URL`,
`CONTEXT_DB_URL`, `SNIPPET_DB_URL` or `EMBED_BASE_URL` to override any of them.

The chat model is the agent's own setting, as everywhere else: `OLLAMA_BASE_URL`
and `OLLAMA_MODEL`, or `--base-url` and `--model`. Routing needs a catalog,
which compose mounts for the stack and nothing names on the host: to measure
the agent as the stack runs it, set `OLLAMA_BASE_URL` to what `.env` says and
`MODEL_CATALOG=models/catalog.json`; without the catalog every call goes to
`OLLAMA_MODEL`. The script puts the repository, `agent/` and the shared
`common/` package on its own path, so nothing needs installing beyond
`tests/requirements.txt`.

## MLflow

```bash
./launch.sh --mlflow                      # MLflow up, at https://localhost:5001
python benchmarks/run_benchmark.py --compare
```

With MLflow answering, each configuration the benchmark measures is an MLflow
run of its own, named `benchmark <configuration>` -- with ` (paraphrase set)`
or ` (follow-up set)` after it when a set was asked -- in the agent's experiment
(`nl2sql-agent`, or `MLFLOW_EXPERIMENT_NAME`):

| | |
|---|---|
| Parameters | the configuration, the pipeline (`pipeline`, `PIPELINE_VERSION`), the model, whether calls were routed, the retry budget, the question ids, whether the paraphrase set was asked (`paraphrase_set`), whether the follow-up set was (`followup_set`) and with how many turns remembered (`memory`), and the independence's mode (`independence`, empty without it) |
| Metrics | `accuracy`, `correct`, `answered`, `questions`, `total_seconds`, `median_seconds`; `accuracy.<category>`, `seconds.<stage>` and `rung.<rung>` for each one the run had; with the paraphrase set, `stability` and `stable`; with the follow-up set, `followup_accuracy` and `followups_correct`, `filters_kept` and `consistency` when a reference keeps a filter, and `resolution_rate` with memory; with the independence, `independence`; through the ensemble, `agreement.<level>`, `rejected.<check>`, `agreed_on_wrong`, `rung_spread`, `judge.<action>`, `judge_fixed`, `judge_broke`, `second_waves`, `columns_joined`, `columns_declined`, `claims_added` and `claims_dropped`; with the grain check on, `grain.references_refused`, `grain.repaired_runs`, `grain.repaired_right`, `grain.repaired_wrong` and `grain.repaired_failed` |
| Artifact | `benchmark.json`, the report as `--json` writes it |
| Traces | every question's, filed under the run by MLflow, tagged `benchmark.question_id`, `benchmark.category`, `benchmark.wording` (0 the question's own), `benchmark.followup` (`true` on a follow-up, filed under the question it follows), `benchmark.after` (on a second turn of the independence, the question before it) and `benchmark.configuration`, and judged by execution as feedback named `benchmark_correct` |

So a wrong answer is one click from the trace that gave it -- which agent
read what, which model wrote the SQL, what the repair said -- and two runs
can be compared in MLflow's own run view. A run cut short, by Ctrl-C or a
database that went away, keeps what it measured and ends as `KILLED`.

It runs on the host, so MLflow is `https://localhost:5001` -- its front door,
the port compose publishes -- unless `MLFLOW_TRACKING_URI` says otherwise;
set it empty to run untraced with MLflow up. The front door presents a
certificate issued by the stack's development CA, which the benchmark
trusts when `./nl2sql-ca.crt` is there (`./launch.sh --desktop` copies it
out, or `docker compose --profile api cp api:/etc/nl2sql/tls/ca.crt
./nl2sql-ca.crt`) and `MLFLOW_TRACKING_SERVER_CERT_PATH` does not name
another. With sign-in on it
also asks who is logging runs: set `MLFLOW_TRACKING_USERNAME` and
`MLFLOW_TRACKING_PASSWORD` to someone in `nl2sql-reviewers` or
`nl2sql-admins`, which MLflow's client sends as Basic credentials. With nothing answering there, the benchmark runs as
before. The runs need MLflow's `mlflow-skinny`, which
`pip install -r tests/requirements.txt` installs; with only the agent's
tracing client, the traces are still written, ungrouped, and the benchmark
says so.

## Tests

```bash
pytest tests/benchmarks --run-docker
```

The reference answers are the thing most worth testing: a reference query that
silently returned nothing would mark every agent wrong and look like a model
problem. So every one of the 15 is executed against the shipped dataset, checked
for the row count the question records, and checked for determinism. Two more
assert that the B07 and B08 references are not accidentally the naive query they
exist to catch.

The scorer is tested against both kinds of mistake it could make: too strict
(rejecting a correct answer whose columns are ordered differently) and too loose
(accepting the 5x fan-out or the calendar-year read).

The paraphrase set is held offline: every question has three rewordings, no
wording repeats another, and each rewording passes the fidelity checks
against its question, all forty-five reported at once when any fails.

So is the follow-up set: every question has one, no fragment is another
wording or its own standalone, every standalone passes the checks in code
against its fragment -- all fifteen reported at once when any fails -- and
takes something from its first turn that the fragment does not say, and
every filter a reference is said to keep is in it. Under `--run-docker`
each reference is run twice for its row count and its determinism, and
none returns its first turn's answer.
