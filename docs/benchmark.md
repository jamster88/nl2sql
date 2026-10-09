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

`snippets` with the ensemble on (arch7.1): each question screened once,
reworded, the rewordings held to it by the fidelity gate, the pipeline run
on the original and the first three kept, every distinct answer read by the
Judge, the runs it accepted voted on, and the strongest run of the largest
accepted group delivered. The report gains an ENSEMBLE block: how the runs
agreed, how often they agreed on a wrong answer, the rewordings the gate
discarded by check, how often a question's runs were scored at different
rungs, how large a question's state grew, what the Judge did, and -- where
it overruled the runs -- how often that turned a wrong answer right or a
right one wrong; `--json` carries each question's runs, rewordings and the
Judge's verdicts.

### One wording: the plumbing changes no answer

Built first (arch7's Phase 1), the ensemble asked the original alone and
delivered it as it ran, so the claim to check was that it changed no answer.
Measured 2026-10-08 against the running 6.3 stack, `snippets` then
`ensemble`, the same fifteen questions routed by the committed catalog,
untraced:

| | Right | Same SQL as `snippets` | Attempts | Model calls | Total | Median |
|---|---|---|---|---|---|---|
| `snippets` | 14/15 | -- | 15 | 52 | 417.0s | 28.0s |
| `ensemble` | 14/15 | 15 of 15 | 15 | 52 | 391.3s | 28.7s |

Every question came out the same way in both -- the same outcome, SQL,
attempts, rung and number of model calls -- so the original's run made no
Supervisor call of its own. The one miss is B15 in both: its own wording,
"Print Flyer, Paid Social and so on", read as a filter, as in the paraphrase
set above, so `snippets` is 14 of 15 on this host. A question's state grew
by what the ensemble copies up from the delivered run: 40.1 KB rendered as
JSON against the pipeline's 37.3 KB for B04, 43.5 KB against 38.2 KB for B09.

### Four wordings and a vote

With the Paraphraser, the gate, the vote and selection built (Phase 2), the
same fifteen, the same stack, three times on 2026-10-08 -- the second and
third after fixes the run before found:

| Run | Right | Missed | Agreement | Rewordings discarded | Total | Median |
|---|---|---|---|---|---|---|
| first | 13/15 | B13, B15 | 12 unanimous, 1 majority, 2 contested | 66: F3 8, F4 49, F5 9 | 2488.1s | 152.7s |
| second | 13/15 | B07, B15 | 10 unanimous, 3 majority, 1 contested, 1 none | 52: F2 9, F3 7, F4 29, F5 7 | 2118.1s | 143.6s |
| third | 13/15 | B07, B15 | 11 unanimous, 2 majority, 2 contested | 46: F2 1, F3 9, F4 26, F5 10 | 2265.0s | 135.7s |

**What the first run found.** Two defects of the ensemble's own. The
Paraphraser was shown the contract as the generator sees it, column names
and all, and wrote them into its rewordings ("List the store count for
every banner_name"): F4 discarded all ten of B03's and the Supervisor
refused B11's. And it was told the measure in the Supervisor's own words --
for B13, "average price difference" -- so it reworded "which competitor
prices lowest relative to us" as "the smallest average price gap", two such
runs agreed on a difference, and they outvoted the original's right answer,
a percentage, which stood alone. The Paraphraser is now told what may not
change in everyday words, the measure only as the generator is told it, and
to add no column name of its own. F4's 49 discards also showed it reading
each rewording's shape from the rewording's own words: "What is the store
count?" read as a list, "the greatest" as unranked. Every run is now held to
the contract read from the question as asked, as arch7 has it, and F4
compares what the Supervisor read -- the entities, the measure, the period.

**What the second run found.** E4, "the audit dropped every claim the
narrator made", was the one reason a run could not vote -- six runs, right
answers among them, B09's original's -- and it judges the narrative, not the
rows. E4 is now the audit judging the rows wrong, and nothing else.

**What the third run shows.** Every run could vote, and the vote decided two
questions against the single run:

- **B07** -- the original was right (34.20), and three rewordings agreed on
  34.65 and outvoted it. They were near-copies -- "Can you tell me...", "I
  need...", "Could you share..." the same sentence -- since almost every word
  of B07 is a number or a name the Paraphraser must keep; at temperature
  zero their few different words were enough to change the query, and the
  same way each time. Three draws that agree are not three independent
  draws.
- **B15** -- the original read "Print Flyer, Paid Social and so on" as a
  filter, as the single run does; one rewording read the channels as
  examples and was right, and three read them as a filter and outvoted it.
  The rewordings keep the question's names exactly as written, as they must,
  so they keep its ambiguity too.

So on this benchmark, today, the ensemble does not do better than one run a
question -- 13 of 15 three times, against 14 of 15 -- and the two questions
it loses are the two the paraphrase set found unstable. Agreement is the
evidence the vote rests on, and where the rewordings share a failure the
original avoids, it is evidence for the failure; the second wave and the
Judge, as arch7 placed them, are where a split vote is weighed rather than
counted ([the Judge, moved before the vote](#the-judge-before-the-vote)).

**The other measures** (the third run). The gate discarded 46 of the 151
rewordings written (one a retry's): F4 26 -- 11 of them B08's, read by the Supervisor as
about products where B08 itself was read as about market share -- F5 10, F3
9, F2 1. A question's runs were scored at more than one rung in 2 of 15, so
a host serving several calls at once would have been asked for two models
at once that often (R6). A question's state, rendered as JSON, is 154.2 KB
at the median and 165.3 KB at the largest, at four runs a question, against
40 KB at one. The reader's high-water mark at several slots (R5) could not
be measured: this host serves one call at a time, so nothing ran at once.
The ensemble made 20.7 model calls a question against the single run's 3.5,
and took 135.7s a question at the median against 28.0s.

### The paraphrase set, through the ensemble

Phase 2's acceptance asks, beside 15 of 15, for the paraphrase set's
stability under `ensemble` to be at least `single`'s. Measured 2026-10-08
after the third run, on the same stack: `--config ensemble
--paraphrase-set`, each of the sixty wordings asked through the ensemble as
a question of its own -- so each was the original of its own runs --
against Phase 0's `snippets`:

| | `snippets` (Phase 0) | `ensemble` |
|---|---|---|
| **stability** | **13/15** | **13/15** |
| the benchmark's own wordings | 14/15 | 13/15 |
| the rewordings | 43/45 | 42/45 |
| every wording | 57/60 | 55/60 |

**The acceptance holds in the count, and only there.** The same thirteen
questions are stable both ways, and B07 and B15 are unstable in both. Across
the sixty wordings the ensemble gets two fewer right, both of them B07's.

**Every wording's own run answered as the single run did.** Candidate 0 was
right on the same 57 wordings as Phase 0's run and wrong on the same three:
B07's rewording 2, and B15's own wording and rewording 1. What changed is
the vote's doing.

**The vote changed two answers, both from right to wrong.** On B07's own
wording and its rewording 3 the original's 34.20 was outvoted by runs that
agreed on 34.65. It set none of the three wrong originals right: B07's
rewording 2 and B15's own wording each had one run that was right, outvoted
2 to 1 and 3 to 1, and every run of B15's rewording 1 read the channels it
names as a filter -- `IN ('Print Flyer', 'Paid Social')`, two rows of five.

**B07's majorities share one mistake.** Every 34.65 joins the daily sales to
the monthly item costs on the date and the product -- the grain trap B07 was
written around. Every 34.20 rolls both up to the month, product and store
first. The runs that fall into the trap come out with the same number, so
they agree, and the vote counts agreement. B07's right answer was among the
runs of all four of its wordings, and was delivered for one.

**What agreement told.** Every wrong answer had a majority behind it, and
one, B15's rewording 1, a unanimous one. Of the 47 unanimous answers 46 were
right; of the 9 majorities, 5; of the 4 contested, all 4 -- B08's two and
B13's two, each delivering the original's group, the largest or tied for
it. B08's splits were one share computed two ways, 21.51 as a percentage
and 0.22 as a fraction. A majority is final in arch7 (section 22.6): the
second wave and the Judge, as arch7 placed them, see only a vote without
one, so neither would have seen B07's -- which is why arch7.1 moved the
Judge before the vote ([below](#the-paraphrase-set-through-the-judge)).

**The other measures.** The gate discarded 196 of the 611 rewordings
written: F4 106, F5 46, F3 37, F2 5, F1 2. Fifty-six wordings ran four
times; B07's rewordings 2 and 3 three times, two of eleven rewordings kept;
one of B08's and one of B09's twice, one kept. A wording's runs were scored
at more than one rung for 13 of the 60, and its state was 152.3 KB at the
median and 165.5 KB at the largest. The run made 1,253 model calls, 20.9 a
wording, and took 9,028.6s, 138.1s a wording at the median, against Phase
0's 1,692s in all.

### The Judge before the vote

arch7.1 moved the Judge before the vote (the owner's decision, 2026-10-08):
it reads every distinct answer -- a group's query and first five rows, by
letter, never with how many runs gave it -- against the question and the
knowledge the original's run retrieved, and sets aside the answers it can
name a mistake in; only the runs it accepted vote. Measured 2026-10-08 on
the same stack, the fifteen questions through `ensemble`:

| | Right | Agreement | Total | Median |
|---|---|---|---|---|
| Phase 2's third run, no Judge | 13/15 | 11 unanimous, 2 majority, 2 contested | 2265.0s | 135.7s |
| the Judge before the vote | **15/15** | 11 unanimous, 2 judged, 2 single | 2507.7s | 152.2s |

**The runs were the same runs.** Temperature is zero, so the Paraphraser
wrote the same rewordings, the gate discarded the same 46, and every run
wrote the same SQL and returned the same rows as in Phase 2's third run.
Every difference is the Judge's. It accepted every answer on eleven
questions, set some aside on two, and overruled the runs on two.

**It overruled the runs twice, both from wrong to right.**

- **B07** -- it set aside the 34.65 three runs agreed on, "Incorrectly joins
  daily sales to monthly costs on the date key", and accepted the original's
  34.20, which "aggregates sales and costs to the fiscal month grain before
  joining, as required by the knowledge base".
- **B15** -- it set aside the two rows three runs agreed on, a query that
  "filters to only 'Print Flyer' and 'Paid Social', excluding the other
  channel types ... that the question asks for", and accepted the one run's
  five.

**It settled the two splits by reading them.** B08's 21.51 against 0.22 --
one share as a percentage and as a fraction -- it judged by the question's
"as a percentage". Of B13's four answers it accepted one, setting aside a
filter to the rows where the competitor was cheaper, a price difference where a
ratio was asked, and a single week where the question names none. Both were
right before, because a tie went to the original's group; now they are
right because the other answers were read and found wrong.

**What it cost.** One heavy call a question, 16.2 s at the median: 21.7
model calls a question against 20.7, and 152.2 s at the median against
135.7 s.

**What it does not show.** The Judge's rules name the kinds of mistake these
runs made -- a filter the question does not state, a join off the
knowledge's grain, other units than asked -- so these fifteen questions are
no longer a blind test of it (arch7.1, open decision 31). What is worth
reading beside the score is where it overruled the runs, scored both ways:
here two fixed and none broken.

#### The paraphrase set, through the Judge

The sixty wordings of [the run without it](#the-paraphrase-set-through-the-ensemble),
measured 2026-10-08 on the same stack:

| | `snippets` (Phase 0) | `ensemble`, no Judge | `ensemble`, the Judge first |
|---|---|---|---|
| **stability** | 13/15 | 13/15 | **14/15** |
| the benchmark's own wordings | 14/15 | 13/15 | 15/15 |
| the rewordings | 43/45 | 42/45 | 44/45 |
| every wording | 57/60 | 55/60 | 59/60 |

**Again the same runs.** Every wording was reworded, gated and run exactly
as without the Judge -- the same 196 rewordings discarded, the same SQL, the
same rows -- so the four answers that changed are the Judge's.

**It overruled the runs four times, each from wrong to right**, and set
answers aside on seven wordings more without changing what was delivered.
Three were B07's: its own wording and rewording 3, where the original's
34.20 had been outvoted, and rewording 2, where the one run that was right
had been -- each time setting aside the 34.65 that "joins daily sales
directly to monthly cost rows on the date key". The fourth was B15's own
wording, where one run in four had kept every channel. B07 is now stable.

**Its one miss went out flagged.** On B15's rewording 1 every run read the
channels it names as a filter, so there was one answer to judge. The Judge
set it aside -- "omitting the 'rest' of the channel types requested by the
question" -- and with nothing accepted the runs' own choice was delivered
as `contested`, with that objection. No wrong answer went out with
agreement behind it: 51 unanimous, 1 majority and 4 judged, all right,
where without the Judge five wrong answers had a majority or every run
behind them.

**What it cost.** 1,313 model calls, 21.9 a wording against 20.9, and
9,759.4s, 152.4s a wording at the median against 138.1s; the Judge's call
took 16.2s at the median. As on the fifteen, its rules name these mistakes,
so this is no blind test of it: where it overruled the runs, scored both
ways, four fixed and none broken.

### The second wave and fusion

Phase 3 of arch7.1's build adds a second wave -- when the vote does not
settle a question, no majority among the runs the Judge accepted or one run
left standing, every faithful rewording not yet run is asked too, and the
Judge and the vote go again over both waves -- and fusion: what the
winning group's other runs found, taken onto the chosen run's answer when
its rows bear it out. Measured 2026-10-09 on the same stack, the fifteen
questions through `ensemble`, twice -- the second time after the first had
shown the claims fused restating each other:

| | Right | Agreement | Second wave | Total | Median |
|---|---|---|---|---|---|
| the Judge before the vote (2026-10-08) | 15/15 | 11 unanimous, 2 judged, 2 single | -- | 2507.7s | 152.2s |
| with the second wave and fusion | **15/15** | 11 unanimous, 2 judged, 2 single | 1 of 15 | 2590.6s | 146.7s |

**The first wave's runs were the same runs**, the rewordings, the 46
discards, the SQL and the rows as before, and both of this phase's runs
made the same second wave with the same SQL. 15 of 15 held.

**One question took a second wave: B15.** Its first wave left one run
standing -- the Judge set aside the three that kept only the two channels
the question names as examples. Six of its rewordings were faithful and not
yet run; the second wave ran them, four answered with every channel and two
with the two. The Judge read both answers again and gave the same
verdicts, and the answer it accepted now had five runs behind it instead of
one: "The Judge set aside the answer 5 of 10 runs gave ... and accepted
this one, which 5 gave". Counted without the Judge, the five and five would
have tied and gone to the original's group, which was wrong. B07, B08 and
B13 were unsettled too, one run standing, but had no faithful rewording
left: the gate kept three of B07's nine rewordings, one of B08's twelve and
three of B13's ten, and the first wave ran them all.

**The widest job measured.** B15's ten runs made a state of 376.4 KB as
JSON, against 155.0 KB at the median at four runs -- the size arch7.1's
risks document asked to be recorded once a second wave ran.

**What fusion added.** No columns: every agreeing run returned the columns
the chosen one did, so there was nothing to join and nothing to decline.
Four claims, one each on B02, B09, B10 and B15, each about a row the chosen
run's narrative had not spoken of -- asked the same way from the command
line, B10's was the fifth product's sales, which the chosen narrator had
left out, and B15's the one channel its narrative had not named. Three
dropped: on B03 a narrator's fragment, "Corner Fresh Grocers has", which
cited no cell; on B15 two claims of runs that returned the same five
channels in another order, so the cells they cited held other figures in
the delivered rows. The dissent named what it could: B15's losing answer
as "run 0's query filters on dim_ad_channel.channel_type" -- the filter
the Judge set it aside for -- while B07's, whose mistake is the grain of a
join, reads the same tables and filters the same columns as the chosen
one, and was named by its figure alone.

**The claims rule was set by this run.** The specification counts a claim
as a repeat when it has the same value from the same cells, or the same
words. On B10, three of the four narrators said the top product sold the
most, one citing its sales and the others its name, SKU and sales, and that
rule added five claims where one said something new; a rule by the exact
set of rows cited then restated, on B03, two banners the chosen narrator had
named in one sentence. A claim is now added only when it speaks of a row
the narrative does not yet speak of (the changelog's departures, Phase 3).

**What it cost.** B15's second wave: six runs and a second Judge call,
373.6s for the question against 173.5s. The fifteen made 340 model calls,
22.7 a question against 21.7; fusion is code and took no measurable time.
