# Feedback

*Part of the [nl2sql documentation](../README.md#documentation).*

The web interface and the desktop client both ask whether an answer was
right. This is where those answers go.

```bash
./start.sh --review
```

That is the whole thing: databases, the API, the web interface, the review
service and the review interface -- and both pages opened in your browser,
the review page in a window of its own. [`./launch.sh --review`](../launch.sh)
is the same containers without the browser step. Where a verdict is staged,
and where the fixes go, are three of the four runtime stores in
`nl2sql-stores`, which start with the databases.

The review service makes the staging table, and the API's role for writing
to it, on its first start; until it has run once, a verdict stays in the
browser and nothing is lost -- the buttons still work, the verdict is still
shown, and `/v1/meta` tells the page not to claim it was sent anywhere. From
then on every verdict is staged whenever the API is up, and waits for a
reviewer; `API_FEEDBACK_DB_URL` set empty keeps them in the browser. Where a
reviewed verdict goes depends on what it said:

| Verdict | Review pane | Where it ends up |
|---|---|---|
| **Correct** | Correct → golden set | Promoted into the golden question set, as before |
| **Wrong** | Wrong → corrections | A corrected query, validated against the live database, in the corrections store |
| **Correct but incomplete** | Correct but incomplete → completions | A completed query, validated the same way, in the completions store |

## Why bother

The golden pairs in
[`context_questions/translated_questions.md`](../context_questions/translated_questions.md)
are the best-understood thing in this repository. The agent retrieves worked
examples from them, the benchmark scores against them, and every question
they *don't* cover is a gap that only shows up as an answer somebody
disagrees with. A *wrong* or *correct but incomplete* verdict in either client
is the cheapest possible report of such a gap. A *correct* one is a new pair
waiting to be written; the other two are a mistake and its fix, which is a
different thing and is kept apart from the set the agent is measured
against.

## What happens to a verdict

| | |
|---|---|
| **Captured** | With a snapshot of the job -- question, SQL, answer, result shape, comment. Taken at vote time, because a job is forgotten after an hour and a verdict pointing at a forgotten job is not reviewable |
| **Staged** | In the feedback store, a database of its own in `nl2sql-stores`. Not the retail database, which is the subject under test, and not the RAG stores, which ship their data inside published images |
| **Reviewed** | In a second web interface, one pane per verdict: what was asked, what the agent answered and the SQL it wrote, what the user thought -- and beside it a form for building a golden pair, or an editor for the query that should have been generated |
| **Promoted** *(correct)* | Appended to the question document *in this checkout*, then loaded into the context store and embedded into the vector store |
| **Fixed** *(wrong, incomplete)* | The reviewer's query is run against the live retail database, read-only, as the agent's own role; only one that runs can be saved. The question, the incorrect answer and the corrected query go into that verdict's own store, with an embedding of the question beside them for retrieval |

## The three fields nobody can guess

A vote gives you a question and some SQL. A golden pair also needs
`keywords`, a `reasoning_target` and a `result` -- which words someone would
search for, where generated SQL typically goes wrong on this question, and
what coming back looks like. Those are judgements about what the question
*tests*, and the review form leaves them empty rather than guessing: a
reviewer skimming a pre-filled form approves it, and the BM25 index fills up
with keywords nobody chose.

## Promotion writes a file you commit

Not a row in a database. The golden set has one source of truth and it is the
markdown document; the context store and both vector tables are built from
it, and the loader **deletes rows whose pair is no longer in the document**.
A pair written straight into the database is erased the next time anyone
reloads.

So a promotion appends to the tracked file, which means it shows up in
`git diff`, reads like any other edit, and is committed by a person. Before
writing anything, the rendered pair is parsed back with the loader's own
parser and every field compared to what went in -- because that parser is one
regular expression over the whole file, and a pair that does not match it is
not reported as malformed, it is simply not seen.

## A judgement can be taken back

Every submission in the review interface can be put back to pending, or
deleted. When it had already been acted on, what it produced comes out with
it: a promoted pair is taken back out of the question document -- the
previous version kept as `.bak`, both stores reloaded -- and a fix is deleted
from its store with its vector. The queue and the golden set never disagree,
so a reopened question cannot end up in the set twice. A reopened submission
gets its work back: the pair as its draft, or the corrected SQL in the query
editor. Anything that reaches past the staging table asks first, saying what
it is about to change. [`review/README.md`](../review/README.md#changing-your-mind)
has the rules.

## Only SQL that runs is stored

A fix for a wrong answer is a query a person wrote, and a query nobody has
run is a guess. So the review interface has a **Validate against the live
database** button that runs it -- as `nl2sql_reader`, in a read-only
transaction, under a timeout and a row cap -- and shows the rows it returned
or Postgres's reason it did not. The save stays disabled until the exact text
in the editor has passed, and the service runs it again before storing it,
whatever the browser said. A query identical to the agent's is refused: that
is a verdict with no fix in it. Since 5.6 a promotion is held to the same
rule: the pair's SQL is run before anything is written, and it must return
rows.

Corrections and completions each get a database of their own -- records and
RAG side by side -- because they are different lessons, and neither is a
golden pair. The curation interface writes
them directly too, without a submission. Nothing reads them yet; they are what a later
agent will retrieve from ("a question like this one was answered wrongly
before, and this is what fixed it"), and a later version adds an agent to
sanity-check a reviewer's query and another to help write it.

## Who can do what

The internet-facing process can add a verdict and nothing else. It connects
to the staging database as a role that cannot read a submission back, cannot
change a review, and cannot see any row a curator has already judged -- by a
row-level security policy, not by the SQL in the API being careful. The
powers that matter belong to a separate service, on a separate port, and it
is the only client of the two fix stores. With sign-in on, only
`nl2sql-reviewers` work its queue and only `nl2sql-curators` write to it
directly, each under their own name and with the SQL they validate run as
their own database role; with it off, it is behind a token of its own.

[`review/README.md`](../review/README.md) has the whole of it.
