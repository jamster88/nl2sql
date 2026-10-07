# Benchmark

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
python benchmarks/run_benchmark.py            # 15 questions, accuracy then speed
python benchmarks/run_benchmark.py --compare  # schema-only vs knowledge vs multi-shot
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
