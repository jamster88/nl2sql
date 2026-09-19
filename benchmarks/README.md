# Benchmark

Fifteen questions, scored on **accuracy first and speed second**.

```bash
./launch.sh                               # the stack has to be up
python benchmarks/run_benchmark.py        # the full agent
python benchmarks/run_benchmark.py --compare   # schema-only vs knowledge vs multi-shot
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

Fifteen, spread across five categories. They are deliberately **not** the 45
golden pairs the agent retrieves from -- a benchmark drawn from those would
measure how well it can look something up, which is not the thing worth knowing.
A test asserts none of them is golden-pair text verbatim.

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
The v4 pipeline breaks that assumption. Its four Stage 1 retrievers are branches
of one superstep and run concurrently, so the gap after one of them is not its
duration.

So each v4 node times itself and reports the result in `state["trace"]`, and the
harness prefers that when it is there. The same entries carry a model-call
count, which is what makes the architecture's economic claim checkable rather
than merely stated: the happy path should cost three model calls, and the repair
agent's classifier should keep most retries from costing a fourth.

A v4 run also scores the **narrative**: the fraction of the answer's claims that
the audit could trace back to a cell of the result set. Execution accuracy says
whether the SQL was right; this says whether the user was told the truth about
it, which is a different failure and one nothing in the v3 harness could see.

## Comparing configurations

`--compare` runs the same questions through three configurations that differ
**only** in what retrieval is switched on, so a difference between two rows is
attributable to that stage and nothing else:

| | Knowledge base | Worked examples | Equivalent to |
|---|---|---|---|
| `schema-only` | off | off | v1 |
| `knowledge` | on | off | v2 |
| `multi-shot` | on | on | v3 |

## Options

```bash
python benchmarks/run_benchmark.py --only B07 B08      # just these
python benchmarks/run_benchmark.py --category grain    # just this category
python benchmarks/run_benchmark.py --json results.json # machine-readable
python benchmarks/run_benchmark.py --verbose           # every pipeline step
python benchmarks/run_benchmark.py --model other-model --base-url http://host:11434
```

It exits non-zero when anything was not correct, so CI can gate on it.

The database URLs default to the **published ports on localhost**, because the
benchmark runs on the host while the agent normally runs inside compose, where
the same names are service names. Set `DATABASE_URL`, `VECTOR_DB_URL`,
`CONTEXT_DB_URL` or `EMBED_BASE_URL` to override any of them.

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
