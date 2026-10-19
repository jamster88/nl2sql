# Benchmark

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
python benchmarks/run_benchmark.py                   # 15 questions, accuracy then speed
python benchmarks/run_benchmark.py --config ensemble # the same, through the ensemble (arch7)
python benchmarks/run_benchmark.py --compare         # schema-only vs ... vs snippets vs ensemble
python benchmarks/run_benchmark.py --paraphrase-set  # each question four ways: the stability
python benchmarks/run_benchmark.py --followups       # each question's follow-up: the follow-up accuracy
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

The first three rows are the pipeline before v4. Measured again on 7.0
(2026-10-12), `schema-only` scores 9 of 15 and `knowledge` 13, as set
out under [Schema-only, with the whole schema](#schema-only-with-the-whole-schema),
[What the knowledge is about](#what-the-knowledge-is-about), [The literal
block's heading](#the-literal-blocks-heading) and [What a question
names](#what-a-question-names) -- 1 and 13 before their scopes were fixed,
10 and 14 after, and a question fewer each since the literal block was
put right, which is what any change to their prompts moves them by; the
paragraphs below read the table as it was measured.

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

**The widest snapshot measured.** A verdict on an answer is staged with the
record of its runs -- the answer's `ensemble`, which holds no rows -- and
that record outlives the job: it is kept as long as the submission is.
B15 asked eleven ways in one wave, the most the settings allow (the
question as asked and ten rewordings, `ENSEMBLE_PARAPHRASES=10`), made a
record of 50.3 KB as JSON: about 4.5 KB a run, 3.6 KB of it the run's
trace of sixteen nodes. Every run there answered at its first attempt; an
attempt more is about five nodes more in that run's trace, a little over
1 KB. So eleven runs that each used all seven attempts would stage about
130 KB, and the usual four runs about 20 KB.

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
one, and was named by its figure alone. The dissent has since been taught
to read the grain (arch7.2): read from B07's real queries, its line now
says the losing runs join fact_item_cogs to the sales on the date, row by
row, where the chosen one rolls both up to the fiscal month and joins them
on that.

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

### On the calibrated catalog

Phase 4 measured the Paraphraser and the Judge on every model the host
serves and rebuilt the committed catalog with what it found
([`model_catalog.md`](model_catalog.md)). Before it, both tasks went to
`OLLAMA_MODEL`; now each goes to the fastest model measured suited to it,
among the `MODEL_MAX_LOADED` models the router keeps. Measured 2026-10-09
and 10 on the same stack, the fifteen questions through `ensemble`, before
and after the router was changed to count only the rungs a task is called
at:

| | Right | Agreement | Second wave | Total | Median |
|---|---|---|---|---|---|
| with the second wave and fusion (6.3's catalog) | 15/15 | 11 unanimous, 2 judged, 2 single | 1 of 15 | 2590.6s | 146.7s |
| on the calibrated catalog, every rung counted | 14/15 | 14 unanimous, 1 contested | 0 of 15 | 3767.9s | 211.4s |
| on the calibrated catalog, the rungs called at | **15/15** | 14 unanimous, 1 judged | 0 of 15 | 2193.5s | 168.2s |

**Counted at every rung, the five older tasks moved too.** The router
chooses which models to keep by how many task-and-rung cells each would
take from the anchor, and the two new tasks add six. A model measured fast
at the Judge took all three of the Judge's cells -- though the Judge is
only ever called at heavy -- and won one of the three places, and two
models the older tasks used lost theirs: the Supervisor's light rung, the
generator's light and standard, the narrator's light and all three of the
repair's were answered by other models than in every run above. B13 was
the miss. Its runs were asked at the heavy rung rather than standard, and
its original was written as an absolute difference, the question's
recorded trap; of its four runs, one returned the competitor's price as a
percentage of ours beside that difference, so it agreed with a run that
returned the difference alone, and that run stood for the group. The
Judge, then another model, accepted none of the three answers -- two of
them for not filtering to a fiscal year the question does not name -- and
the runs' own choice went out `contested`. The narrator, on `OLLAMA_MODEL`
at every rung, took 940.6s of the 3,767.9s.

**Counted at the rungs called at, only the Paraphraser moved.** A rung no
rule asks for saves no call, so it now wins no model a place
(`complexity.CALLED_AT`; the changelog's Fixed). On this catalog the
router keeps the three models 6.3's catalog chose, the five older tasks
are routed exactly as before, and the Judge stays on `OLLAMA_MODEL`, the
one of the three measured suited to it; only the Paraphraser's light rung
moves, to a model measured suited and faster. Every original's SQL was the
SQL of the Phase 3 run above; the rewordings were new, and with them the
runs beside it. The Judge overruled the runs once, on B15, from wrong to
right, with four runs where Phase 3 had needed ten; it set one answer
aside on B07 without changing the answer. B08 and B13, which Phase 3
delivered with one run standing, had four runs agreeing. The host dropped
off the network during B12 of this run, so B12 to B15 were run again once
it was back; the table counts that second run.

**What it cost.** 337 model calls, 22.5 a question against 22.7. The
Paraphraser's call took 6.5s at the median, where `OLLAMA_MODEL`'s had
taken 32.6s in the calibration, and the Judge's 15.5s. In all, 397.1s
less than Phase 3's run, mostly B15's second wave not needed; at the
median, 21.5s more.

## The grain check

Phase 6 of arch7.3's build puts a rule in the Static Validator: two tables
of different grains may not be joined on the date unless the finer is
first rolled up to the coarser's period, nor on no date at all while the
coarser still brings a row for every period
([`agent/README.md`](../agent/README.md#the-pipeline)). It is the first
phase that changes the pipeline itself, so it is measured through all
five configurations and not only `ensemble`, with the check and without
(`GRAIN_CHECK_ENABLED=false`).

**The grains it reads, on this catalog.** Eight tables are stamped with a
date. Measured from their own dates: the sales, the ad performance and the
promotion performance are daily; the competitor prices, the item prices and
the market share weekly; the item costs and the vendor allowances monthly.
Each read took under a tenth of a second.

**It refuses no reference.** The fifteen reference queries pass, against
the grains above. Two of them add up two date-stamped facts: B07's rolls
the daily sales and the monthly costs up to the fiscal month before
joining them, and B13's joins two weekly tables on the date, which is what
the date is for. A test holds them to it offline and, under `--run-docker`,
against the grains read from the live catalog; and every benchmark run
checks its references with the grains it read itself, and fails if one is
refused.

**Read against the queries of the measurements above.** Before anything
was run with it, the rule was given every distinct query the earlier
phases' runs wrote that adds up two date-stamped facts -- 34 of them,
B07's and B13's, from the fifteen and the paraphrase set through
`ensemble` -- and each was run again and scored. It refused five, all
B07's, all the trap this document has followed since the paraphrase set:
daily sales joined to monthly costs on the date, 34.65 where the answer is
34.20. It passed the other 29: the three B07 queries that are right, and
all 26 of B13's, 14 right and 12 wrong -- B13's recorded mistake is a
difference where a ratio was asked for, which is not a matter of grain and
not this rule's to catch.

**Measured 2026-10-10** on the same stack, untraced, routed by the
committed catalog: the fifteen questions through all five configurations
with the check, and again without it.

| | Right, with the check | Right, without | Runs it sent to repair | Total, with | Median, with |
|---|---|---|---|---|---|
| `schema-only` | 1/15 | 1/15 | 0 | 419.8s | 23.3s |
| `knowledge` | 13/15 | 13/15 | 0 | 671.0s | 48.0s |
| `multi-shot` | 15/15 | 15/15 | 0 | 438.4s | 26.6s |
| `snippets` | 14/15 | 14/15 | 0 | 388.2s | 28.5s |
| `ensemble` | **15/15** | 15/15 | 2 of 60, both ending right | 2337.6s | 174.3s |

**One run a question, in the benchmark's own wordings, it fired on
nothing.** In the first four configurations the SQL delivered for every one
of the fifteen was the same, character for character, with the check and
without. B07 as the benchmark words it does not fall into the trap once
the worked examples are shown, and where it is wrong -- `schema-only`,
`knowledge` -- it is wrong for a reason below that is not its grain. So
those four rows are the same row twice, which is the first thing a rule
in every run's validator has to show: it left alone what was right, and
what was wrong for another reason.

**Through the ensemble it fired on B07, in two of its four runs.** The
rewordings were the same with the check and without. Two of them joined
the daily sales to the monthly costs on the date. Without the check, each
delivered 34.65, the Judge set that answer aside, and 34.20 went out with
two of the four runs behind it. With it, each was refused once -- "...
joined on the date, each fiscal month's row of fact_item_cogs meets only
the rows of fact_pos_retail_sales for that one date. Roll
fact_pos_retail_sales up to the fiscal month ..." -- and wrote the roll-up:
34.20, four runs of four agreeing, and nothing for the Judge to set aside.
The answer is the one it was. What changed is how many runs stand behind
it, at a cost of two generations: 303.0s for the question against 173.1s.
The other fourteen questions ran as before -- 14 unanimous and one judged
(B15, wrong to right) both times, no second wave, 339 model calls against
337.

**The grain questions, each four ways** -- B07, B09 and B13 in the
paraphrase set's wordings, twelve in all, which is where the trap was
first measured:

| | Right, with the check | Right, without | Stable questions | Runs it sent to repair | Total |
|---|---|---|---|---|---|
| `snippets` | **12/12** | 11/12 | 3 of 3, from 2 of 3 | 3, all ending right | 645.3s, from 500.8s |
| `ensemble` | 12/12 | 12/12 | 3 of 3 both ways | 10 of 47, all ending right | 3098.2s, from 2782.6s |

One run a question, this is the miss the paraphrase set found, closed.
B07's three rewordings each wrote the trap first; each was refused once
and answered 34.20 at its second attempt. Without the check the second
("Calculate the gross margin percentage ... for fiscal month 12 of
FY2025") delivered 34.65 at its first attempt, and the other two reached
34.20 by another gate, at their second and third. B09's and B13's eight
wordings wrote the same SQL either way.

Through the ensemble all twelve are right either way, because the Judge
was already setting the trap aside. What the check changes is what the
Judge is left to do. Without it, six of B07's sixteen runs delivered
34.65, in every one of its four wordings; the Judge set that answer aside
each time, and in the second rewording, where the trap was the runs' own
choice, it overruled them. With it, no run delivered 34.65: ten were
refused once each -- those six, and four that without the check reached
34.20 at a later attempt -- and B07's wordings were agreed by 4, 4, 4 and
3 of their 4 runs where they had been by 2, 3, 2 and 2. Over the twelve,
the wordings in which the Judge set an answer aside fell from five to two,
and it overruled the runs in none where it had in one, with the Judge and
its rules as they were -- so the fall is the check's and not a change in
the Judge.

**Every run it refused ended right, and it refused nothing that was.**
Across the passes above it sent a run to repair fifteen times -- twice the
same two runs, B07's own wording through the ensemble being in both
tables -- each refused once and each then answering 34.20; every one was
B07's. No answer that was right without the check was wrong with it.

**What it did not catch.** The run that kept B07's fourth wording from
four of four joined the costs on a key it made up -- `c.date_key =
d.fiscal_year * 100 + d.fiscal_month_num`, which matches no row -- and
returned NULL. That is a join on an expression, which the rule passes: it
refuses only what it can read. The Judge set it aside, with the check and
without. The other answer still set aside was one run of B13's second
rewording that averaged only the products the competitor undercut, which
is not a matter of grain.

**What it costs.** The grains are eight queries once a process, under a
tenth of a second each. A refusal is one generation of the shared budget,
about a minute on this host.

**`schema-only` and `knowledge` are not the rows at the top of this
document**, and the check has no part in it: both score the same without
it, on the same SQL. Those rows were measured before v4, when a model
call chose the tables from the whole catalog. Since v4 the tables come
from the schema retriever, and the scope is enforced. With retrieval off
(`schema-only`) there is no schema retriever, so the scope is the answer
contract's tables alone -- a product and a date dimension, for a question
about sales -- or, with none, the first ten tables by name; the sales fact
is in neither, and a draft that reads it is refused as out of scope until
the attempts run out. (B02 needs no fact and missed another way: the
literal matcher took the word "products" for the sub-category 'Paper
Products', and the count was filtered to it.) With the knowledge base but
no worked examples (`knowledge`), the schema retriever did not rank the
sales fact for B06 or B07; B07's first draft began `WITH sales AS (` and
was refused for reading `fact_pos_retail_sales`, which was not in scope.
In `multi-shot` and above the worked examples' tables bring it into scope.
Nothing in this document measured these two configurations after v4,
which is how it went unseen. Both are fixed since, below.

### Schema-only, with the whole schema

The measurement above found `schema-only` answering from a scope with no
fact table in it. That is fixed (the changelog's Fixed, 7.0): when no
retriever chose a table -- retrieval off, or every store down -- the scope
is the whole schema, the answer contract's tables first and `MAX_TABLES`
not applied. Here that is nineteen tables and about 15,000 characters of
prompt. Measured 2026-10-10 on the same stack, the fifteen through all
five configurations again, the grain check on:

| | Right, before | Right, now | Total | Median |
|---|---|---|---|---|
| `schema-only` | 1/15 | **10/15** | 318.7s | 16.7s |
| `knowledge` | 13/15 | 13/15 | 711.6s | 48.4s |
| `multi-shot` | 15/15 | 15/15 | 456.4s | 27.2s |
| `snippets` | 14/15 | 14/15 | 396.0s | 28.6s |
| `ensemble` | 15/15 | 15/15 | 2365.5s | 178.6s |

**Only `schema-only` moved.** The other four wrote the SQL they wrote
before the fix, for every question -- through the ensemble the same
rewordings, the same agreement, and B07's same two runs refused by the
grain check and repaired. A configuration whose retrievers choose tables
does not pass through what was changed.

**`schema-only` no longer loses a draft to its scope.** Before, thirteen
of the fifteen needed more than one attempt -- 41 retries in all -- and two
ran out of attempts; followed step by step, a draft that read a table
outside the scope was refused each time. Now no draft is refused as out of
scope: all fifteen produced SQL that ran, with three retries, each the
planner's.
By category: schema 2 of 3, calendar 2 of 3, grain 1 of 3, fan-out 1 of 1,
analysis 4 of 5. Its five misses are a model with the schema and nothing
else: B03 took the banner from the competitor dimension, B06 computed the
markdown as net less gross sales where a column holds it, B07 joined the
daily sales to the monthly costs on the date, B11 averaged sales lines
where a basket is several, and B13 averaged only the products the
competitor undercut. With the knowledge base and the worked examples all
five are answered, which is `multi-shot`'s row.

**It is 10 of 15, or 9.** Run again without the grain check, which fired
in neither run, fourteen answers were the same SQL and B02 was not: the
literal matcher offers the sub-category 'Paper Products' for the word
"products", and on the same prompt, the same model and the same rung the
generator applied it once and not the other time.

**The grain check passed B07's trap here, and this is why.** The query
joins the sales to the costs on the date, which is the shape the check
refuses -- but it reads the sales twice, once for the sales total and once
joined to the costs, and the rule passes any query that reads one of the
two tables twice, since which reading a roll-up or a filter belongs to
cannot be told from the reading alone. Of the 230 distinct queries the
measurements in this section and the ones before it wrote, 18 join two
facts of different grains; the rule refused six, and that caution passed
exactly one, this one, which was wrong. The rule is narrowed since, below.

### A table read twice

The rule passed any query that read one of its two tables twice, because
what the reading says of a table -- what it is rolled up to, how its time
is filtered -- is then said of one reading or the other. But the first
shape needs none of that when the SELECT that joins the two dates reads
the finer table itself: it joins the table's rows as stored, so nothing
rolled them up first, whatever another SELECT does with the same table.
The rule now reads that much from the one SELECT (the changelog's Fixed,
7.0). A table read twice still passes where the whole statement would
have to be read: joined on no date, or reaching the date join through a
CTE that may have rolled it up.

The same reading closes a second gap, which no measured query fell into.
A SELECT that joins on the date and then groups by the month was refused
when it was the outermost and passed inside a CTE, its grouping taken for
the roll-up the message asks for. A roll-up after the join rolls up what
the join already lost, and both are refused.

**Before anything ran**, the rule as it was and the rule as it is were
both given the 230 queries above, the 48 golden pairs and the 15
references. They refuse the same six of the 230, and the narrowed rule
one more -- `schema-only`'s B07. Neither refuses a golden pair or a
reference.

**Measured 2026-10-10** on the same stack, the grain check on: the fifteen
through all five configurations, and the grain questions' twelve wordings
through `snippets` and `ensemble`.

| | Right, before | Right, now | Runs it sent to repair | Total |
|---|---|---|---|---|
| `schema-only` | 10/15 | 10/15 | 1, from none | 334.9s |
| `knowledge` | 13/15 | 13/15 | 0 | 720.4s |
| `multi-shot` | 15/15 | 15/15 | 0 | 452.9s |
| `snippets` | 14/15 | 14/15 | 0 | 387.7s |
| `ensemble` | 15/15 | 15/15 | 2 of 60, both ending right | 2370.2s |
| `snippets`, the twelve wordings | 12/12 | 12/12 | 3, all ending right | 647.4s |
| `ensemble`, the twelve wordings | 12/12 | 12/12 | 10 of 47, all ending right | 3099.8s |

**Nothing moved but the one query.** Every other answer is the SQL it was
before the change: the other fourteen of `schema-only`, the forty-five of
the three configurations above it, the twelve wordings through
`snippets`, and through the ensemble every one of its 60 runs on the
fifteen and its 47 on the twelve wordings -- refused at the same places,
repaired to the same queries. The narrowing refused nothing it had
passed, save what it was made to refuse.

**`schema-only`'s B07 is refused now and its grain repaired; it is still
wrong, for a reason that is not its grain.** Before, it delivered the
trap: 97.65 where the answer is 34.20, each month's cost having met one
day's sales. Now that draft is refused. The generator wrote it again,
word for word, and it was refused again; told that the approach itself
had to change, it read the costs and the sales each through its own
calendar, both held to the month -- a join at one grain, which the rule
passes -- and answered 33.84. Over net sales the same query gives 34.20.
It divides by gross sales, as every one of its drafts did: the measure's
definition, which the knowledge base states and the schema does not. So
the report's line for it is "refused 2 times, ended WRONG", the first
refusal in these measurements not to end right -- a refusal of a wrong
query, repaired in what it was refused for, with a second mistake left
that this check does not look for. It took 67.1s where the trap had taken
7.9s.

### What the knowledge is about

The measurements above left `knowledge` at 13 of 15, B06 and B07 both
refused for reading `fact_pos_retail_sales`: the vectors had not ranked it,
and with no worked examples nothing else brought it into scope. In both,
the knowledge shown to the generator said to read it -- "Sales metrics:
gross, markdown and net" for B06, the gross-margin recipe for B07 -- and
each of those chunks lists the table under `tables` in its metadata, which
nothing read. A draft that reads a table the knowledge in its prompt lists
is now admitted to it, while the cap has room (the changelog's Fixed,
7.0). Measured 2026-10-11 on the same stack, the grain check on:

| | Right, before | Right, now | Total |
|---|---|---|---|
| `schema-only` | 10/15 | 10/15 | 354.6s |
| `knowledge` | 13/15 | **14/15** | 414.4s |
| `multi-shot` | 15/15 | 15/15 | 418.7s |
| `snippets` | 14/15 | 14/15 | 383.8s |
| `ensemble` | 15/15 | 15/15 | 2322.8s |
| `snippets`, the twelve wordings | 12/12 | 12/12 | 599.7s |
| `ensemble`, the twelve wordings | 12/12 | 12/12 | 3041.4s |

**Only `knowledge` moved, and in it only B06 and B07.** Every other answer
is the SQL it was, at the attempt it was -- through the ensemble, every
run's. Nothing is put in scope ahead of a draft, so no prompt changes
until a draft has read such a table, and outside `knowledge` no draft
did.

**No draft of `knowledge` is refused for its scope now.** Five retries
where there were ten, 414.4s where there were 720.4s.

**B06 is right.** Its first draft sums the markdown column of the sales,
its name misspelt, as it did before. The table is admitted, the planner
says there is no such column, and the second draft -- the table now
described -- spells it: 22,707.43, at its second attempt, where it had
taken six and answered from the promotions.

**B07 is no longer refused, and is still wrong.** Its first draft is
admitted and runs: 33.99 for 34.20. It rolls the sales and the costs up to
the fiscal month, which is the grain the recipe asks for and nothing the
grain check objects to -- but by department, one average cost times all
the department's units, where the recipe keeps the product and the store.
Before, with that draft refused, it had answered from the costs alone: 0.
So `knowledge` misses one question, and not for its scope.

**Three other ways were measured first.** *The tables the knowledge
lists, put in scope before the first draft* in whatever room the cap left:
`knowledge` 14 of 15 the other way round -- B07 right, and B06 wrong, the
generator at its light rung summing net sales once the sales were
described (the same prompt at the standard and heavy rungs sums the
markdown) -- and `multi-shot` 14, B15 going from right to wrong with one
table more in scope that it had no use for. *The tables the knowledge
names in its text*, fourteen a question to the lists' five: every scope
filled to the cap. *The admitted draft sent back once*, to be written
again with the table described: B07's second draft, at the heavy rung,
was the first one over again, for an attempt spent.

### The literal block's heading

The generator's prompt carries the Literal Matcher's finds -- phrases of
the question beside the values the database spells them as -- under the
heading "Literal values in this database that match the question:". From
v4 to 7.0 that line was there twice running: the matcher's block came with
it, the prompt's place for the block added it again, and each half was
tested alone. It is there once now (the changelog's Fixed, 7.0). It is the
smallest change measured in this document, and it reaches every prompt
that has a literal in it. Measured 2026-10-11 on the same stack, the grain
check on:

| | Right, before | Right, now | Total |
|---|---|---|---|
| `schema-only` | 10/15 | **9/15** | 363.1s |
| `knowledge` | 14/15 | **12/15** | 511.1s |
| `multi-shot` | 15/15 | 15/15 | 486.4s |
| `snippets` | 14/15 | 14/15 | 373.4s |
| `ensemble` | 15/15 | 15/15 | 2426.6s |
| `snippets`, the twelve wordings | 12/12 | 12/12 | 679.3s |
| `ensemble`, the twelve wordings | 12/12 | 12/12 | 3113.2s |

**It cost three answers, all where there are no worked examples.** B10
under `schema-only` and under `knowledge`, and B08 under `knowledge`. B10
asks for the five products with the highest net sales; both runs now
filter to `sub_category_name = 'Paper Products'`, which is what the
matcher offers for the word "products" and what, before, neither run had
taken up. B08's de-duplication lost a column of its grouping, 26.16 for
21.51, with no literal in it: a different prompt, a different draft.

**What the heading heads is almost all noise.** For the fifteen questions
the matcher offers 63 values, and the reference queries use one: 'Dairy &
Eggs', for B07. Thirty-three of the others are the word "year", matched to
a promotion season called "New Year New You"; the rest are "net" for the
payment term '1/15 Net 45', "value" for the positioning 'Value/Discount',
"banner" for the channel 'Website Banner', "products" for 'Paper
Products'. A single common word of the question scores 1.00 against a
value that contains it. Said once, under one heading, the block reads as
an instruction, and without a worked example to follow the generator
follows it. That is the finding of this measurement: the doubled line was
a flaw, and the block under it is the defect.

**With worked examples it holds.** `multi-shot` 15 and `snippets` 14 of
15, as before; three answers reworded and still right, one by a second
attempt.

**Through the ensemble the score holds and the agreement is weaker.**
15 of 15 on the fifteen and 12 of 12 on the wordings, as before. Of the 60
runs of the fifteen asked in the same words both times, 15 wrote other
SQL than before -- every one of them a wording with a literal block, and
none of the seven without. B13 was
agreed by its four runs both times and now goes out contested: the run
that stands for the group was rewritten to average only the products the
competitor undercuts, which the Judge sets aside, and which gives the same
figure for the competitor that is always cheaper. B15 took a second wave,
eleven runs, to be settled the way four had settled it. 352 model calls
for 339, 2426.6s for 2322.8s. On the wordings, B13's own went out
contested the same way, and B07's fourth was agreed by four runs of four
where it had been by three.

The matcher is narrowed since, below, and the two are measured together
there.

### What a question names

The measurement above found what the literal block had been holding: for
the fifteen questions, 63 values offered and one used. The Literal Matcher
kept any value that shared a word with a phrase of the question, and the
database's `word_similarity`, which does its searching, scores one word
against any value that contains it at 1.00. It now keeps a value only when
the phrase names it -- every word of it, in any order and through any
connector, or a near-miss of its whole spelling (the changelog's Fixed,
7.0). A word inside a longer phrase that is a value word for word is part
of that name: "Dairy & Eggs" is the department, and its "Eggs" is not also
the sub-category `Eggs`. And a word the schema itself is named with is not
a misspelling of a value: "product" is not the department `Produce`.

**What it offers, measured before any model is asked.** Over the 108
questions this repository holds a verified query for -- the benchmark's
sixty wordings and the 48 golden pairs -- against the real catalog:

| | Before | Now |
|---|---|---|
| Values offered | 466 | 80 |
| Of them, a literal of the verified query | 54 | 54 |
| Questions offered any value | 95 of 108 | 43 of 108 |
| The fifteen, as the benchmark words them | 63 values over 13 questions | 5 over 3 |
| Questions on which the two scorers resolve different values | 87 of 108 | 0 |

Every one of the 54 was a phrase that is its value whole, and none of the
383 that were part of a value was used. The module has always said that a
database without `pg_trgm` gives "the same answers" by `difflib`; on 87 of
these questions it did not, and now it does. What is still offered and not
used is what a question spells and its query does not filter on: the two
channels B15 gives as examples, a banner that is also a competitor's name,
the word "advertising", which is also an allowance code.

**What naming a value reaches.** Each of the catalog's 1,271 distinct
values, put in a question. In quotes, all 1,271 resolve where 1,268 did,
with 33 other values offered beside them where there were 2,334. Written
as the catalog spells it and unquoted, 925 resolve where 967 did, with 245
others beside them where there were 2,326. The 43 lost begin with a digit
or have a slash in them -- `12-Pack`, `1/15 Net 45`, `2606 Elm St` --
which the matcher cannot take from a sentence as one phrase. Before, one
of their words reached them, along with every other value that had it.
They are reached in quotes.

**Measured 2026-10-12** on the same stack, the grain check on: the heading
and the matcher together, beside the committed code and the heading alone.

| | Committed | Heading once | Heading once, matcher narrowed |
|---|---|---|---|
| `schema-only` | 10/15 | 9/15 | 9/15 |
| `knowledge` | 14/15 | 12/15 | 13/15 |
| `multi-shot` | 15/15 | 15/15 | 14/15 |
| `snippets` | 14/15 | 14/15 | **15/15** |
| `ensemble` | 15/15 | 15/15 | 15/15 |
| `snippets`, the twelve wordings | 12/12 | 12/12 | 12/12 |
| `ensemble`, the twelve wordings | 12/12 | 12/12 | 12/12 |
| `snippets`, the paraphrase set's sixty | 58/60 | -- | **59/60** |

**As the agent ships, these are its best figures in this document.**
`snippets`, the default for one run a question, answers all fifteen: B15,
its standing miss, returns every channel. Through the ensemble all fifteen are
unanimous, where one had been judged -- and where the heading alone had
left one contested and one in a second wave -- with no answer overruled
and no second wave, in 340 model calls and 2282.7s. On the twelve wordings
all twelve are unanimous and the Judge sets one answer aside where it set
aside two. On the paraphrase set's sixty, run on the committed code and on
this, 59 are right where 58 were, with 14 of 15 questions stable both
times: B15's own wording and its first rewording come right, and B08's
third rewording goes wrong.

**Without the examples or the snippets, a question fewer each, and never
the same question.** `schema-only` gains B03 -- "banner" is no longer the
ad channel `Website Banner`, and the stores are counted from the store
dimension -- and loses B08 and B09, neither over a literal: B09 takes
calendar 2025 for the fiscal year. `knowledge` gains B07, whose roll-up
now keeps the product and the store, and loses B06 and B08. `multi-shot`
loses B15: with the noise gone the block holds the two channels the
question gives as examples, and the generator filters to them -- the
question `snippets` gained by the same change.

**What a fifteen-question score can carry.** Every configuration answers
at temperature zero, so its score is a function of its prompt, and any
change to the prompt moves the questions that are on an edge. B15 went
right in one configuration and wrong in the next by one edit. Over the
five prompts `knowledge` has been given in this section and the two before
it, it scored 14, 14, 14, 12 and 13, and missed B06, B07, B08 and B10 by
turns. A question either way is what rewording a prompt gives or takes,
whatever the rewording says. So the matcher is measured on what it offers,
where nothing is on an edge, and the configuration that ships was run on
sixty wordings both ways.

## The follow-up set

Phase 0 of [arch8](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8.md)
-- the design that remembers the conversation, so that "and by store?" is
read as the complete question it means -- measures the premise it is
argued from, before anything is built on it: how often the pipeline, which
answers every question alone, answers a follow-up written as people write
them. Each of the fifteen questions has a second turn written by hand
([`benchmarks/followups.py`](../benchmarks/followups.py)): a fragment that
borrows a measure, a period, an entity or a filter from the first turn or
its answer, the complete question it means, and that question's reference
query. The fragment is asked in the words typed, with nothing remembered
-- what 7.0 does with every follow-up -- and scored against the complete
question's reference. The **follow-up accuracy** is the fraction right.
[`benchmarks/README.md`](../benchmarks/README.md#the-follow-up-set) has how
the follow-ups were written and what the report and `--json` carry.

**Measured 2026-10-16** with the agent as 7.0 ships, against the running
stack, `--memory 0`: each fragment asked alone, and the report saying so.
Beside it, the fifteen asked complete on 2026-10-12
([What a question names](#what-a-question-names)):

| | `snippets` | `ensemble` |
|---|---|---|
| the fifteen, asked complete | 15/15 | 15/15 |
| **follow-up accuracy**, nothing remembered | **1/15** | **2/15** |
| runnable SQL | 12/15 | 12/15 |
| refused before any SQL | 3: B03, B10, B11 | 3: B03, B10, B11 |
| the first turns' filters kept | 3/14 | 3/14 |
| model calls | 60 | 284 |
| total, median | 543.9s, 37.8s | 1925.6s, 88.7s |

**The premise holds.** The risks by phase expected the number to be low,
and it is: one or two follow-ups of fifteen right, where the same fifteen
asked complete are all right. Three fragments never reach the generator.
"What's the average square footage for each?", "What were their product
names and brands?" and "And how many baskets did each have?" are screened
as out of domain: a pronoun with nothing for it to stand for reads as a
question the database cannot answer. The other twelve are answered, and
each answers a different question. "And by banner?" counts each banner's
stores, where it meant their net sales in fiscal year 2025. "Break that
down by department." sums net sales, where it meant products and how many
are private label. "What were its top 2 categories?" ranks the categories
of every department, not Meat & Seafood's. "Which vendor paid us the most
in SCAN_BACK?" ranks every year's allowances, not fiscal 2025's. Through
`snippets`, "The same for FY2024?" gives FY2024 month by month.

**The right shape is no sign.** B02, B05, B08, B14 and B15 return as many
rows as their references in both configurations: a breakdown by the
dimension asked for, over the wrong measure or the wrong period. A person
reading the answer sees the table they expected.

**What was kept is a year the generator wrote unasked.** The delivered
SQL kept 3 of the 14 filters the first turns set, the same three in both
configurations: `fiscal_year = 2025` in B05, B07 and B15, a year the
generator gave a fragment that named none, as it did not in B06, B09, B12
or B14. Nothing else was kept: not `'Meat & Seafood'`, not fiscal month
12, not B08's `fiscal_year = 2024`, not B02's private label, not B10's
top five.

**The ensemble does not see it.** Where its readings of a fragment
agree, they agree on a wrong question as readily as on the right one:
B02's runs are unanimous and wrong, B09's a majority and wrong. Where the
fragment says too little, they scatter: B06's seven readings of "What
about Q3?" ask six different questions, from the quarter's dates to its
market share. Five follow-ups are contested, the Judge accepting none of
their answers, and an answer is given for each all the same. Of its two
right, B01 is right in both configurations: after "How many stores are
there?", "And how many in each state?" has one likely reading and it is
the one meant. B04 is the Judge's, which overruled the runs' wrong answer
in the second wave. The ensemble's one gain cost 224 model calls more.

**What it means for arch8.** Retrieval alone guesses a follow-up right
once or twice in fifteen, so remembering the conversation is bought for
accuracy, as arch8 argues, and not only for consistency and the record.
Phase 2 measures five turns remembered against these two numbers, under
both pipelines.

### The checks in code against the fifteen

The second number Phase 0 owes
([risks by phase](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8_risks_by_phase.md),
S1): how many of the fifteen standalones, each a complete question a
person wrote for a fragment, the checks in code would have discarded --
the checks a model's restatement will be held to before it becomes the
question the pipeline answers ([`memory.py`](../agent/nl2sql_agent/memory.py),
`resolve`: C1 numbers, C2 literals, C3 polarity, C4 shape, the fidelity
gate's extractors in subset form). **None of 15**, with the word lists as
the paraphrase set grew them; nothing was added.
[`tests/benchmarks/test_followups.py`](../tests/benchmarks/test_followups.py)
holds it. Four fragments carry a literal the standalone had to keep
(`FY2024`, `Q3`, `Frozen Foods`, `SCAN_BACK`), three a number (`2`, and
the `2024` and the `3` inside `FY2024` and `Q3`) and four a direction
(`top`, `most`, `highest`, `fewest`). Read the number for what it is: the standalones were written
with arch8's rules in view, so they are kinder to the checks than a
model's restatements will be; Phase 2's rejections by check are the number
for those.

## The pipeline by name

Phase 1 of [arch8](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8.md)
names the pipeline -- `PIPELINE_VERSION`, the release that built it:
`v7_0_0` the ensemble, `v6_3_1` one run a question and nothing else
different -- and lays the plumbing memory will use, with nothing yet handed
a turn: every run's state carries the words typed, its turns (none), how
they were read and the name, and the Supervisor's and the generator's
prompts each gain a placeholder that renders empty. Its acceptance is that
none of it changes an answer: under `v7_0_0` the benchmark is 7.0's
ensemble to the SQL, and under `v6_3_1` 7.0's `snippets`.

**Measured 2026-10-17**, untraced, on the host against the running 7.0
stack, routed by the committed catalog: 7.0's own code, run from a `git
archive` of the commit Phase 1 started from, and then Phase 1's:

| | Right | Same outcome | Same SQL | Same attempts, rung and model calls | Total | Median |
|---|---|---|---|---|---|---|
| 7.0, `snippets` | 15/15 | -- | -- | -- | 384.9s | 26.9s |
| Phase 1, `snippets` under `v6_3_1` | 15/15 | 15 of 15 | 15 of 15 | 15 of 15 | 383.3s | 26.9s |
| 7.0, `ensemble` | 15/15 | -- | -- | -- | 1964.8s | 127.4s |
| Phase 1, `ensemble` under `v7_0_0` | 15/15 | 15 of 15 | 15 of 15 | 15 of 15 | 1973.8s | 127.4s |

**No answer moved.** Every question came out the same way under each name
as under 7.0: the same outcome, SQL, attempts, rung and number of model
calls. The ensemble's record moved with none of it -- all fifteen
unanimous both times, the same 45 rewordings discarded by the same checks
(F3 13, F4 27, F5 5), the Judge setting the same one answer aside, no
second wave, 338 model calls. The two prompts were held offline as well:
rendered from 7.0's own module and from Phase 1's with the same inputs,
the Supervisor's and the generator's are the same byte for byte. Under
`v6_3_1`, `--config ensemble` is refused before anything is asked, with the
agent's own sentence, and `--compare` runs the four one-run configurations
and says why not the fifth
([`benchmarks/README.md`](../benchmarks/README.md#comparing-configurations)).

## Memory in the agent

Phase 2 of [arch8](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8.md)
builds the conversation into the agent: the Supervisor reads a question in
the light of the turns before it and restates a follow-up so that it stands
alone, the restatement held to the words typed in code; the generator is
shown the earlier questions with their SQL; and the answer opens with how a
follow-up was read. It is measured on the follow-up set with five turns
remembered, against [the follow-up set](#the-follow-up-set)'s numbers with
none, and on the **independence**: each of the fifteen asked again as the
second turn of a conversation -- after the question before it in the list,
and after itself -- which must come out exactly as alone, 15 of 15.

**Measured 2026-10-18**, untraced, on the host against the running 7.0
stack, routed by the committed catalog, `--memory 5`:

| | `snippets` under `v6_3_1` | `ensemble` under `v7_0_0` |
|---|---|---|
| follow-up accuracy, nothing remembered (Phase 0) | 1/15 | 2/15 |
| **follow-up accuracy, five remembered** | **12/15** | **13/15** |
| first turns right | 15/15 | 15/15 |
| resolution rate | 13/15 | 13/15 |
| not resolved | C2 literals 2 | C2 literals 2 |
| the first turns' filters kept | 14/14 | 14/14 |
| **independence**, after the question before | **15/15** | **15/15** |
| independence, after itself | -- | 15/15 |
| model calls, as second turns against alone | 68 against 53 | 353 against 338 |
| the Supervisor's time, median, as a second turn against alone | 5.06s against 1.11s | 5.25s against 1.08s |

**A follow-up is now answered as the complete question it means.**
Twelve and thirteen of fifteen, where nothing remembered answered one and
two. Thirteen restatements held, each then read as its complete question
is (the resolution rate); the other two -- "The same for FY2024?" and "What
about Q3?" -- were restated with "fiscal year 2024" and "the third quarter"
for the words typed, which C2 holds verbatim, and were answered right all
the same, asked as typed with the reading made in context and the generator
shown the first turn. Every filter the first turns set was kept, where
nothing remembered kept three of fourteen. The misses were read right and
answered nearly so: B12 returns the reference's units and lift for each
quarter, labelled "FY2025 Q1" where the reference has 1, which the
comparison counts as another answer; and B13, "And which prices highest?",
names the right competitor, QuickStop Grocery, with its premium as a
difference -- $0.93 through `snippets`, 16.4 percent through the ensemble --
where the reference states its price as a percentage of ours, 116.4. (Built
the reference's way, in an earlier run, 7.0's completeness check refused
116.5 as an impossible percentage.) Through `snippets` B10 missed as well, after a
restatement that carried the first answer's values into the question
("... (SKU100189 Ambervale T-Bone Steak, and the other 4 from the previous
answer)"): the generator filtered to SKUs of its own. The rule asks for
nothing the turns do not supply, and the turns supplied that; a
restatement of the question and not of its answer is the next thing to ask
of the prompt.

**Two departures the measurement made.** Built as arch8 has it -- one
Supervisor call reading the question in context, the generator always shown
the turns -- the independence came out 14 of 15 twice. Under `v6_3_1`, B07
asked after B06 was written from B06's query, its nearest exemplar, and fell
into the grain trap B07 tests; so the generator is shown the turns only for
a question the Supervisor read as a follow-up -- restated, a restatement a
check discarded, or no reading at all -- and a question read as standing
alone is written with 7.0's prompt. Then under the ensemble, B08 asked after
B07 was read differently in context -- the measure "market share percentage"
where alone it is "overall market share percentage", and no entity -- and
the Paraphraser, handed those words, wrote other rewordings, three of whose
four runs returned a fraction where alone all four returned the percentage.
Read after the turn of the question before it, the anchor's reading of the
fifteen kept its words for 5 at the standard rung and 6 at the light one,
and at the light rung B11 was refused in context. So a question is now read
in context first and then screened as 7.0 screens one -- the words typed
when nothing was restated, the restatement when one held -- at one
Supervisor call more for a question asked with turns (the owner's choice,
2026-10-18). With both, the independence is 15 of 15 under each name, and 15
of 15 with the first turn the question itself.

**What it costs.** One Supervisor call more a question in a conversation,
and none for a question asked alone: 15 calls over fifteen questions under
each name. On this host that is about four seconds a question -- the reading
in context on the standard rung, then 7.0's on the light -- and the
question's median time grew from 27.9s to 31.8s through `snippets` and from
126.3s to 130.3s through the ensemble. The follow-ups' prompts, counted by
the model host (medians over the fifteen): 486 tokens alone, 724 with the
one turn shown -- the rule, the block and the restatement's field -- and
1,217 for the two calls together. A question's state grows by its turns,
once in the outer state and once in each run's: asked after fifteen turns,
every benchmark question's, B15's state through the ensemble's four runs
grew from 160.4 KB to 221.0 KB, the turns themselves 12.1 KB as JSON; it was
read as standing alone and came out as it does alone.

