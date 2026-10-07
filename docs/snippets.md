# SQL snippets and curation

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
./start.sh --curate
```

The knowledge base says how this database works, and the golden pairs show
whole questions answered. Between the two sits what a query is actually
built from: how two tables join, what a phrase in a question filters to, how
a measure is calculated. A model that has the schema still guesses these,
and the guesses are where answers go wrong -- the store's state column is
`state_code`, a store brand is a flag on the product rather than a brand
name, and a market share is a ratio of two sums that only survives when both
are summed the same way.

A **SQL snippet** is one of those pieces, run against this database and
written beside what it means:

| Kind | The SQL | Validated as | Example |
|---|---|---|---|
| join | a `JOIN ... ON ...` | `SELECT * FROM <applies to> <join>`, with the rows before and after, so a fan-out or dropped rows is a warning | sales to the fiscal calendar |
| filter | a condition, without the `WHERE` | inside a `WHERE`, with how many rows it keeps; nothing or everything is a warning | "store brands" is `p.is_private_label` |
| measure | an aggregate expression | one row, or it is not a measure | net sales, average basket value |
| dimension | an expression to group by | under `GROUP BY` | a fiscal quarter labelled `FY2025 Q3` |

They live in a tracked document,
[`context_questions/sql_snippets.md`](../context_questions/sql_snippets.md),
which ships 32, each one run on the retail database before it was written
down. Each snippet is a section with its kind, the tables it uses, the
phrases a question says it with, what it means, the `FROM` clause it applies
to, its SQL, and a note:

````markdown
## S13 - Store brands

```meta
chunk_id: snippet:s13
kind: filter
tables: dim_product
keywords: private label, store brand, store brands, house brand, own-brand products
```

**Means:** Restricts to the private-label products, which store brand, own brand and own label all mean.

**Applies to:**

```sql
dim_product p
```

**SQL:**

```sql
p.is_private_label
```

**Note:** 41 of the 200 products, under the brands Everyday Basics, Homestead Select, Pantry Essentials and ValueChoice.
````

**How the agent uses them.** The document is loaded into a store of its own,
the snippets database in `nl2sql-stores`, by [`rag/07_load_snippets.py`](../rag/07_load_snippets.py):
the rows, a keyword matcher, and an embedding of what each snippet means.
The agent reads it through a role that can only `SELECT`. A fifth Stage 1
retriever finds each question's snippets on two signals. A keyword phrase
matches when every one of its words is in the question, in any order and any
inflection, weighted by how rare those words are, so "stores" on its own
does not reach the store-brand snippet. Meaning is the cosine similarity
between the question and what the snippet means. Each signal is put on a
fixed scale and the two are averaged, and at most five are kept. The Context
Aggregator then shows the SQL Generator only the snippets whose tables are
all in the selected set: a snippet is a hint about tables already in play,
never a reason to bring one in. With none in scope the prompt is exactly the
one 5.5.1 sent.

**The document is the source of truth**, as the golden question document
is: the loader removes from the store any snippet no longer in it. Every
start compares the document's hash with the one the last complete load
recorded and loads again when they differ, so a snippet arrives with a
`git pull` like any other change. A load that cannot embed records no hash
and is retried on the next start, and the keyword half answers meanwhile.

**The curation interface** is where they are written, at
<https://localhost:8083> -- a fourth web page, in front of the review service
like the review interface, with three tabs:

| Tab | What it writes |
|---|---|
| SQL snippets | Add, change or remove a snippet. Validated as its kind is, previewed as the markdown it will become, then written into the document -- the previous version kept as `.bak` -- and loaded into the store, embedding only what changed |
| Golden pairs | Add a pair no feedback produced, or remove one. A removed pair that came from the review queue reopens its submission |
| Corrections & completions | Store the query that answers a question the agent gets wrong or leaves incomplete, without a submission, or remove a fix, with its vector |

**Nothing is saved that has not run.** Every add is run against the live
retail database first, as `nl2sql_reader`, read-only, under a timeout, and
the save stays disabled until the exact text in the editor has passed. The
service runs it again before it writes, whatever the page said. A golden
pair's SQL must also return rows, because in the golden set an empty result
reads as a failure, and since 5.6 a promotion out of the review queue is held
to the same rule. A snippet's SQL must use the tables it says it does.

The runtime stores read these from `.env`, like the rest of the stack -- the
four of them one server since 6.3, each a database with an owner of its own:

| Setting | Default | |
|---|---|---|
| `STORES_DB_PORT` | `5435` | The host port of the runtime stores: all four, where the feedback store's was |
| `DB_BIND_ADDRESS` | `127.0.0.1` | The address that port is published on -- this machine's, as for every store (6.1) |
| `SNIPPETS_DB_USER`, `SNIPPETS_DB_NAME` | `snippets`, `nl2sql_snippets` | The snippets' owner and database: what the `dbprep` one-shot creates, and what the review service loads it as. Its password is `secrets/snippets_db_password` |
| `SNIPPETS_READER_USER` | `snippets_reader` | The read-only role the loader creates and the agent reads as; its password is `secrets/snippets_reader_password` |
| `FEEDBACK_DB_USER`, `FEEDBACK_DB_NAME` | `feedback`, `nl2sql_feedback` | The staging database and its owner, the review service; `secrets/feedback_db_password`. The API writes as `nl2sql_feedback_writer`, `secrets/feedback_writer_password` |
| `CORRECTIONS_DB_USER`, `CORRECTIONS_DB_NAME` | `corrections`, `nl2sql_corrections` | The corrections store and its owner; `secrets/corrections_db_password` |
| `COMPLETIONS_DB_USER`, `COMPLETIONS_DB_NAME` | `completions`, `nl2sql_completions` | The completions store and its owner; `secrets/completions_db_password` |
| `STORES_IMAGE` | `pgvector/pgvector:pg18`, pinned by digest | The server's image: stock pgvector, since what it holds comes from people and documents, not a build |

No owner is a superuser: each owns its database and nothing else, and the
server's own superuser (`secrets/stores_db_password`) is reached only over
its socket, by `dbprep`. Before 6.3 these were four servers, on ports 5435
to 5438; see [Upgrading an existing checkout](images.md#upgrading-an-existing-checkout)
for what moves a store from then.

Snippets and golden pairs are written into the documents in this checkout, so
they show up in `git diff` and are committed by a person. Fixes are rows in
their stores, as they are when a review produces them.
[`curate/README.md`](../curate/README.md) has the interface, and
[`review/README.md`](../review/README.md#curation) the routes behind it.
