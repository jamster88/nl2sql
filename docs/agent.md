# NL2SQL agent (v4, multi-agent)

*Part of the [nl2sql documentation](../README.md#documentation).*

A LangChain/LangGraph agent that answers natural language questions by writing,
validating, and running SQL against the Postgres container below. It uses any
model served by Ollama.

**v2 retrieves before it writes.** Each question is embedded and matched against
a pgvector knowledge base built from [`knowledge/`](../knowledge) -- the data
dictionary, DDL index and business index -- and the matching sections are fed to
the model alongside the schema. That context carries what the schema cannot:
fiscal-calendar semantics, pre-aggregated columns, and joins that fan out.

**v3 also retrieves worked examples, and generates multi-shot.** A second,
independent step searches the question/SQL pairs in
[`context_questions/translated_questions.md`](../context_questions/translated_questions.md)
-- each verified to run against this database -- through an ensemble of three
retrievers: question similarity (0.50), BM25 over the pairs' keywords (0.35),
and reasoning-target similarity (0.15). The fused shortlist is then **reranked**:
scored against the pair's tables and SQL, which no retriever indexes, and
diversified so three exemplars teach three patterns rather than one three times.

The winners are replayed to the model as real conversation turns -- human asks,
assistant answers with SQL -- in front of the actual question. Prose tells the
model the rule; a worked example shows it applied.

**v4 splits the work across agents and takes the model out of two of them.**
The same retrieval runs, now as four parallel branches -- schema, literals,
knowledge, worked examples -- joined by a context aggregator. Table selection
became a vector search over the DDL chunks plus foreign-key closure, and
validation became a `pglast` parse plus a plain `EXPLAIN` with a cost ceiling.
Both had been model calls, and between them they cost 863 of v3's 1499
benchmark seconds without writing any part of the answer. A literal matcher
resolves phrases to real values, so "dairy and eggs" reaches the generator as
`dim_product.department_name = 'Dairy & Eggs'`. After execution a formatter
picks a chart from the result's shape, a narrator writes claims that each name
the cells they came from, and a deterministic audit checks every number against
those cells. Any failure -- parse, planner, runtime or audit -- routes to one
repair agent and spends one shared retry budget.

**v5 (arch5) checks that a correct answer is also a complete one.** "Top 10
SKUs" used to come back as ten `sku_id` values -- right, and useless without a
second query. The Supervisor now also reads what an answer is about, and an
*answer contract* built from it says what a complete answer carries: the name
beside every id (read from the catalog's key constraints), the measure a
ranking was ranked by, and -- when the question names no period -- the latest
complete fiscal year, which the answer then states. The generator sees the
contract before it writes; a Completeness Reviewer checks the rows against it
after they run and sends a gap back through the same repair loop, whose budget
grows from four generations to seven.

**v5.1 (arch5.1) gives each verdict its own treatment.** The agent is
unchanged; what happens to a reviewed answer is not. A correct one is promoted
into the golden set as before; a wrong or correct-but-incomplete one is fixed
-- a reviewer writes the query that should have been generated, validates it
against the live database, and it goes into a corrections or completions store
of its own. [Feedback](feedback.md#feedback) has the whole of it.

**v5.6 shows the generator the pieces a query is built from.** A *SQL
snippet* is one verified piece of SQL -- a join, a filter, a measure or a
dimension -- beside what it means in a question's words: "store brands" is
`p.is_private_label`, "transactions" is `COUNT(DISTINCT f.basket_id)`, and
sales reach the fiscal calendar through `f.sales_date_key`. They are curated
in [`context_questions/sql_snippets.md`](../context_questions/sql_snippets.md),
apart from the golden pairs, which are whole questions answered, and loaded
into a store of their own. A fifth Stage 1 retriever finds each question's
snippets by keyword phrase and by meaning, and the generator is shown the
ones whose tables are all in scope. [SQL snippets and
curation](snippets.md#sql-snippets-and-curation) has the whole of it.

**v5.2 (arch5.2) routes every model call.** Triage, draft, column check,
sentence and diagnosis each go to the fastest model on the Ollama host that
calibration measured to be suited to the task, at the complexity the question
presents: light, standard or heavy, computed from the pipeline's own state
without a model call. A repair climbs the ladder, a routed model that cannot
answer falls back to `OLLAMA_MODEL`, and the trace names the model that
answered every call. The list it routes from is the
[model catalog](model_catalog.md#model-catalog): what the Ollama host serves and what each
model was measured to be suited to. The committed catalog describes the host
this checkout was developed against; on any other host every call goes to
`OLLAMA_MODEL`, exactly as in v5.1, until that host has a catalog of its own,
and `MODEL_ROUTING_ENABLED=false` makes it v5.1 outright.

The design, and every place it departs from the source documents, is in
[`multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md`](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md):
arch5.2 as built in 6.1.0 -- the answer contract, the Completeness Reviewer,
human review and model routing, with each field of the shared state given
its lifetime and the security blueprint made a verified table -- plus the
console, taking a judgement back, tracing, snippets and curation, and the
sign-in design (section 20) and 6.1's transport (section 21). The threat
model and the deployment tiers the defaults implement are in
[`SECURITY.md`](SECURITY.md).

See [`agent/USAGE.md`](../agent/USAGE.md) for how to launch it and ask questions,
and [`agent/README.md`](../agent/README.md) for how it works.

```bash
docker compose run --rm agent "What were the top 5 departments by net sales in fiscal year 2024?"
```

Where it shows: the benchmark's grain and fan-out questions. Schema-only scores
1 of 3 on grain and 0 of 1 on fan-out; with the knowledge base both go to full
marks. See [Benchmark](benchmark.md#benchmark) for the run.

```bash
docker compose run --rm agent "What is our overall market share in fiscal year 2024?"
docker compose run --rm agent --no-rag "..."   # schema-only, v1 behavior
```

The market-share question is subtler than it first looks, and worth knowing
before reading too much into any single number. Summing both columns raw
inflates each by 5x and the **ratio survives** -- 21.51% either way. The mistake
that yields 107.5% is asymmetric: a raw numerator over a de-duplicated
denominator. In the benchmark run the schema-only agent produced neither. It
wrote a correct query, rejected it three times in its own validation step, and
gave up -- the only question in the whole run where any configuration failed to
produce an answer at all.
