# Benchmark

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
python benchmarks/run_benchmark.py                   # 15 questions, accuracy then speed
python benchmarks/run_benchmark.py --config ensemble # the same, through the ensemble (arch7)
python benchmarks/run_benchmark.py --compare         # schema-only vs ... vs snippets vs ensemble
python benchmarks/run_benchmark.py --paraphrase-set  # each question four ways: the stability
```

Since v5.2 the report also says which model answered each agent at each
rung, how many of the questions it touched came out right, its P50, and how
the generator's task was scored across the set -- read from the trace, so a
routed run is attributed per model and not only per agent.

Since v5.5, with MLflow up (`./launch.sh --mlflow`), each configuration is
also an MLflow run holding every question's trace -- see [Tracing](tracing.md#tracing).

[`benchmarks/`](../benchmarks) holds fifteen questions that are deliberately **not**
the golden pairs the agent retrieves from -- a benchmark drawn from those
would measure how well it can look something up. Accuracy is **execution
accuracy**: the SQL is run and its rows compared against reference SQL verified
against the shipped dataset. Query text is never compared, because two correct
queries for the same question rarely look alike.

## Measured

All three configurations, same 15 questions, `qwen3.8-256k` at 256k context:

| | accuracy | produced SQL | retries | total | median |
|---|---|---|---|---|---|
| `schema-only` (v1) | 12/15 (80%) | 14/15 | 5 | 511s | 21.0s |
| `knowledge` (v2) | **15/15 (100%)** | 15/15 | 0 | 1251s | 81.8s |
| `multi-shot` (v3) | **15/15 (100%)** | 15/15 | 0 | 1499s | 100.5s |
| multi-agent (v4) | **15/15 (100%)** | 15/15 | 3 | 910s | 61.2s |

| | analysis | calendar | fan-out | grain | schema |
|---|---|---|---|---|---|
| `schema-only` | 5/5 | 3/3 | **0/1** | **1/3** | 3/3 |
| `knowledge` | 5/5 | 3/3 | 1/1 | 3/3 | 3/3 |
| `multi-shot` | 5/5 | 3/3 | 1/1 | 3/3 | 3/3 |

Three things this says, including one that is not flattering:

**The knowledge base earns its place.** Every question schema-only missed is a
grain or fan-out question, and the knowledge base fixes all of them. It also
removes every retry: 5 down to 0.

**Multi-shot adds no measurable accuracy here, and costs 20% more time.** Both
retrieval configurations score 15/15, so this set cannot distinguish them --
once the knowledge base is on there is no headroom left to measure. That is a
limitation of a 15-question benchmark, not evidence that the examples do
nothing, but it is what the numbers say and they should not be read as more.
Telling the two apart needs harder questions than these.

**Accuracy costs roughly 3x the wall time**, and almost none of it is retrieval.
Across 15 questions the knowledge base and the golden-pair ensemble together
take **1.2 seconds**; the rest is the model:

```
  generate_sql            634s
  validate_sql            499s
  select_tables           364s
  retrieve_knowledge      1.1s
  retrieve_examples       0.1s
```

**v4 spends a third less time for the same answers**, by deleting the two model
calls that were not writing them. Validation became an AST parse and a plain
`EXPLAIN`; table selection became a vector search over the DDL chunks plus
foreign-key closure:

```
  v3  validate_sql   499.4s   ->   v4  validate_static + planner_gate   0.1s
  v3  select_tables  363.9s   ->   v4  retrieve_schema                  1.2s
```

It spends some of that back on a narrator, about a quarter of the run, which
the architecture did not anticipate. What it buys is a narrative whose every
number has been checked against a cell of the result rather than asserted, and
`NARRATE_ENABLED=false` removes it.

The retries are not a regression either: v3 had none because its LLM validator
passed everything it did not reject outright, while v4's planner catches three
real errors and repairs them without a model call.

**v5 holds 15/15 and adds one model call where it can matter.** The answer
contract costs nothing -- a label map and a fiscal calendar read from the
catalog once -- and the Completeness Reviewer's rules cost nothing either. Its
one reflective call ran on the three questions whose rows name an entity, 16s
in all, for a run of 991.6s against v4's 909.7s (and wall time against the
shared Ollama host varies by about 30% between identical runs). Getting there
took four runs, each of which changed the design: the contract never restates
a measure the question names, names no entity for a "how many" question, and
brings the calendar into scope for any period. [`agent/README.md`](../agent/README.md)
has the details.

`select_tables` and `validate_sql` together cost more than generation itself.
Two model calls that do not write the answer take the majority of the time,
which is the obvious latency lever -- well ahead of anything in the RAG layer.

See [`benchmarks/README.md`](../benchmarks/README.md) for the scoring rules and
what they do and do not forgive.

## The paraphrase set

Phase 0 of [arch7](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7.md) --
the design that asks a question several ways and compares the answers --
measures the premise it is argued from, before anything is built on it: how
often the pipeline's answer depends on how a question is worded. Each of the
fifteen questions is asked as the benchmark asks it and as three hand-written
rewordings ask it ([`benchmarks/paraphrases.py`](../benchmarks/paraphrases.py)),
and every answer is scored against the question's one reference. The
**stability** is the fraction of questions every wording of which came out
right. [`benchmarks/README.md`](../benchmarks/README.md#the-paraphrase-set)
has how the rewordings were written and what the report and `--json` carry.

Measured 2026-10-07 against the running 6.3 stack: `--config snippets`, the
single pipeline as 6.3 ships it -- what arch7 calls `single`, a name that
waits for Phase 1's `ENSEMBLE_ENABLED` to mean anything -- on the chat host
`.env` names, routed by the committed catalog
(`MODEL_CATALOG=models/catalog.json`, as compose mounts it), untraced:

| | right |
|---|---|
| **stability** | **13/15** questions right in all four wordings (86.7%) |
| the benchmark's own wordings | 14/15 |
| the rewordings | 43/45 |
| every wording | 57/60, and runnable SQL for all 60 |

Two questions came out differently by wording:

| Question | Wordings right | What the wrong ones did |
|---|---|---|
| B07, Dairy & Eggs' gross margin in fiscal month 12 | 3 of 4: its own, 1 and 3 | rewording 2 ("Calculate the gross margin percentage ... for fiscal month 12 of FY2025") joined daily sales to monthly costs on `date_key` -- the grain trap the question was written around, which the other three wordings stepped round |
| B15, impressions and clicks by channel type | 2 of 4: rewordings 2 and 3 | the benchmark's own wording and rewording 1 read "Print Flyer, Paid Social and so on" as a filter -- `WHERE channel_type IN ('Print Flyer', 'Paid Social')`, two rows of five -- where "such as" and "like" were read as examples |

What it says, for arch7:

**The premise holds, on two questions in fifteen.** On each of them the
wording decided the answer, and each mistake was made by some wordings and
not by others: the kind a vote between wordings can see and a single run
cannot. On the other thirteen every wording agreed, so there the ensemble
buys a confidence signal -- agreement -- rather than a different answer.

**One miss was the benchmark's own wording.** B15 as the benchmark asks it
was wrong in this run: a run of the fifteen alone would have reported 14/15,
with no hint that two other wordings get it right.

**B07 is the case the vote decides; B15 is the case it cannot.** Three
wordings against one is a majority, and the majority is right. Two against
two is no majority: arch7 sends it to a second wave, then to the Judge, and
with neither the tie goes to the group that holds the original -- here, the
wrong one. Phase 2 should keep that in view when it builds the ranking.

**Four runs cost about four times one.** Sixty wordings took 1,692 s, 28 s
each, and a rewording cost what its question did (28.3 s against 27.8 s on
average), as arch7 assumed. One run: read each question's result as one
sample.

### The fidelity checks against the forty-five

The second number Phase 0 owes
([risks by phase](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_risks_by_phase.md),
S1): how many of these rewordings, each judged faithful by a person, the
fidelity gate's checks in code would have discarded -- the checks the
ensemble will hold a model's rewordings to before it spends a call on them
([`fidelity.py`](../agent/nl2sql_agent/fidelity.py)). With the implementation
specification's starting vocabulary (its section 3.4), **8 of 45**, one in
six:

| Rewording | Check | Discarded because | What was added |
|---|---|---|---|
| B06.3 "the fourth fiscal quarter" | F1 numbers | 4 missing | ordinals count as their numbers, "first" to "twentieth" (and "4th" is no letter-digit literal) |
| B07.3 "the twelfth fiscal month" | F1 numbers | 12 missing | the same |
| B09.1 "for each allowance type" | F3 polarity | "broken down by" read as a decline | "break down", "broken down" and their forms are no direction |
| B09.3 "per allowance type" | F3 polarity | the same | the same |
| B14.1 "Break down our ... net sales by state" | F3 polarity | a decline added | the same |
| B13.3 "which competitor is cheapest" | F3 polarity | "lowest" missing | "cheapest" is low |
| B10.1, B10.2 | F2 literals | "Give the SKU" read as a name | a sentence's first word starts no name |

With those, none of the forty-five is discarded, and
[`tests/benchmarks/test_paraphrases.py`](../tests/benchmarks/test_paraphrases.py)
holds it. One rule was added that no discard asked for: a code written with
an underscore is a literal, since B09's "like SCAN_BACK" names the column its
answer is reported by and nothing held it. F5 discarded none: no rewording
came closer than its 0.8 token Jaccard to its question or to another
rewording. Read the number for what it is: these were written with arch7's
rules in view, so they are kinder to the checks than a model's rewordings
will be; Phase 2's fidelity rejections by check are the number for those.

## The ensemble configuration

```bash
python benchmarks/run_benchmark.py --config ensemble
```

`snippets` with the ensemble on (arch7): the question screened once by the
ensemble's own outer graph and run as its first wording. As built so far the
ensemble asks the original alone and delivers it as it ran, so the claim to
check is that it changes no answer -- the same SQL, question by question, as
`snippets` -- while the screening is paid once, not twice.

Measured 2026-10-08 against the running 6.3 stack, `snippets` then
`ensemble`, the same fifteen questions routed by the committed catalog,
untraced:

| | Right | Same SQL as `snippets` | Attempts | Model calls | Total | Median |
|---|---|---|---|---|---|---|
| `snippets` | 14/15 | -- | 15 | 52 | 417.0s | 28.0s |
| `ensemble` | 14/15 | 15 of 15 | 15 | 52 | 391.3s | 28.7s |

Every question came out the same way in both -- the same outcome, the same
SQL, the same attempts, the same rung and the same number of model calls --
so the original's run made no Supervisor call of its own, the ensemble's
screening having been made for it. The one miss is B15 in both: its own
wording, "Print Flyer, Paid Social and so on", read as a filter, as in the
paraphrase set above, so `snippets` is 14 of 15 on this host today and that
is the number the ensemble has to match. The difference in total time is
the first question's: `snippets` ran first and loaded the models (B01 18.1s,
then 7.6s); the medians are within a second.

A question's state is bigger under the ensemble by what it copies up from
the delivered run: 40.1 KB rendered as JSON against the pipeline's 37.3 KB
for B04, and 43.5 KB against 38.2 KB for B09 -- the bulk of either is the run's
own retrieval context, which the ensemble keeps whole as its candidate. It is
the baseline a job of several candidates will be measured against.

