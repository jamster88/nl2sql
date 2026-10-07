# Feedback review

Turning a verdict into a golden question -- or into a fix.

The web interface and the desktop client both ask whether an answer was
right. This is where the answers to that question go -- from either of them,
into one queue -- and where each is turned into the thing it is worth
turning into:

| Verdict | Pane | Becomes | Stored in |
| --- | --- | --- | --- |
| **Correct** (`yes`) | Correct → golden set | a verified question/SQL pair | the golden question set, as before |
| **Wrong** (`no`) | Wrong → corrections | the question, the wrong answer, and a corrected query that ran | the corrections database in `nl2sql-stores`: records + RAG |
| **Correct but incomplete** (`incomplete`) | Correct but incomplete → completions | the question, the incomplete answer, and a completed query that ran | the completions database in `nl2sql-stores`: records + RAG |

Since 5.6 it is also the backend of a second page, the
[curation interface](../curate/README.md), which writes the same golden set
and fix stores directly, without a submission -- and the SQL snippets, which
no verdict produces at all. See [Curation](#curation).

Two processes and four databases, and the snippet store -- since 6.3 the
staging, corrections, completions and snippet databases are one server,
`nl2sql-stores`, each with an owner of its own (`stores/<database>` below):

```
browser ──nginx──▶ agent API   POST /v1/questions/{id}/feedback
desktop ─────────────▶ │
                       └── INSERT only ──▶ stores/feedback  (staging)
                                                │
reviewer ──nginx──▶ review service ──owner──────┘
                       │
                       ├─ promote (correct) ─▶ context_questions/translated_questions.md
                       │                        └─▶ 05_load_golden_pairs ─▶ chunkdb
                       │                        └─▶ 06_embed_golden_pairs ─▶ vectordb
                       │
                       ├─ validate ─ nl2sql_reader, READ ONLY ─▶ postgres (retail)
                       │
                       ├─ fix (wrong) ────────▶ stores/corrections   sql_corrections + _vectors
                       │  fix (incomplete) ───▶ stores/completions   sql_completions + _vectors
                       │
curator ──nginx──▶ (the same service)
                       └─ snippet ────────────▶ context_questions/sql_snippets.md
                                                └─▶ 07_load_snippets ─▶ stores/snippets
```

## Contents

- [Why it is a separate service](#why-it-is-a-separate-service)
- [What a verdict carries](#what-a-verdict-carries)
- [The fence around the public process](#the-fence-around-the-public-process)
- [Promotion](#promotion)
- [Fixing a wrong or incomplete answer](#fixing-a-wrong-or-incomplete-answer)
- [Changing your mind](#changing-your-mind)
- [Curation](#curation)
- [Running it](#running-it)
- [Endpoints](#endpoints)
- [Configuration](#configuration)
- [The interface](#the-interface)
- [Tests](#tests)

---

## Why it is a separate service

Because of what it can do, not what it is.

This process writes `context_questions/translated_questions.md` and reloads
the retrieval stores built from it. That file *is* the golden question set --
the verified pairs the agent retrieves worked examples from and that every
benchmark run is scored against. Giving those powers to the agent's own API
because both happen to be FastAPI would put the benchmark inside the blast
radius of the thing being benchmarked.

So the public API holds an INSERT-only role on one table, and everything else
lives here: its own container, its own port, its own token. The same goes for
the two fix stores -- the public process has no connection to either, and
the only SQL a reviewer can have run against the retail database goes
through the same read-only role the agent answers with.

## What a verdict carries

A verdict arrives with a snapshot of the job it is about: the question, the
generated SQL, the answer, the narrative, the intent, the tables, the result
shape, and any comment the user added.

The verdict itself is one of three: `yes` (correct), `no` (wrong), or
`incomplete` (correct but incomplete -- the SQL was right and the answer
left out something a reader needed). Each has its own pane in the review
interface and its own destination: a correct answer is
[promoted](#promotion) into the golden set, a wrong or incomplete one is
[fixed](#fixing-a-wrong-or-incomplete-answer) into its own store. The
table's two CHECK constraints -- on the verdict and on the state -- are
re-applied on every start, which is how a staging database created when
there were only two verdicts learns the third, and one created before 5.1
learns the `corrected` state.

The snapshot is taken at vote time rather than looked up later because there
is no later -- a job is forgotten after `API_JOB_TTL_SECONDS`, and a record
holding only a job id would be pointing at nothing within the hour. The SQL
in particular is the whole reason a verdict is interesting: for a "yes" it
is the candidate a golden pair gets built from, and for the other two it is
the mistake a fix is written against.

It is taken from the job by the *server*, not sent by the client. A client
that supplied its own snapshot could supply one that never matched the job,
and the staging table would then hold evidence of an answer the agent never
gave.

One submission per job, not one per click. A user who votes "no", thinks
better of it and votes "yes" has one opinion, and the second vote replaces
the first -- until a reviewer has acted on it, after which the held verdict
stands and a revote is refused.

## The fence around the public process

The agent's API connects as `nl2sql_feedback_writer`. That role is created,
and its grants reset, on every start of this service -- so they are whatever
[`nl2sql_review/store.py`](nl2sql_review/store.py) says rather than whatever
somebody widened them to once.

| It may | It may not |
| --- | --- |
| Insert a submission | Read a submission's question, SQL or comment |
| Replace one that is still pending | See any row a reviewer has judged |
| Delete one that is still pending | Change a state, a reviewer or a note |
| Read back `id`, `job_id`, `state` of a pending row | Read the promotion log |

The right-hand column is enforced by **row-level security**, not by grants
alone. The public process has to be able to replace a verdict, and that means
`UPDATE`, which as a plain grant would also let it rewrite a submission a
curator had already judged. A `state = 'pending'` guard living only in the
caller is not a guard against the caller, so the policies say it in the
database.

One consequence is worth knowing, because the grants look sufficient right up
until the first revote: `INSERT ... ON CONFLICT DO UPDATE` requires
*table-level* `SELECT`, which would hand this role the full text of every
pending submission. So a revote is a delete followed by an insert, in one
transaction, and "already reviewed" is detected by the unique constraint on
`job_id` tripping -- without ever reading the row that refused.

`tests/review/test_store_live.py` asks the cluster whether all of that is
true, rather than asserting that the SQL file says so.

The fix stores are not behind this fence because the public process never
reaches them: they are separate Postgres instances whose only client is this
service.

## Promotion

Only a *correct* answer is promoted. `promote` on a wrong or incomplete one
is refused with `wrong_workflow` -- a wrong answer's SQL in the golden set
would be the benchmark scoring the agent against its own mistake.

A reviewer opens a submission in the **Correct** pane, reads what was asked
and what the agent answered, and writes the parts a *correct* verdict cannot
carry:

| From the submission | From the reviewer |
| --- | --- |
| `title` (a first guess), `question`, `tables`, `sql_code` | `keywords`, `reasoning_target`, `result` |

Those three are judgements about what the question *tests* -- which words
someone would search for, where generated SQL typically goes wrong, what
coming back looks like. Nothing in a vote carries them, and the form leaves
them empty rather than guessing: a reviewer skimming a pre-filled form
approves it, and the BM25 index fills up with keywords nobody chose.

Promoting then does this, in this order:

1. **Run the SQL** against the live retail database, as `nl2sql_reader`,
   read-only, exactly as a fix is validated (since 5.6). It must run and it
   must return rows -- in the golden set an empty result reads as a failure
   -- or the promotion is refused with `not_valid` and the database's
   reason. The pair is written with the SQL as it was run, without a
   trailing `;`.
2. **Validate** the draft against the loader's format rules.
3. **Render and append** the pair, in memory.
4. **Parse the result with the loader's own parser** -- the actual
   `ragproc.golden_pairs.parse_document`, on the actual new document --
   and check that it yields exactly one more pair and that every field of
   the new one came back the way it went in.
5. **Write atomically**: a temporary file in the same directory, then
   `os.replace`, keeping the previous version as `.md.bak`.
6. **Reload** the context store and the vectors.

Step 4 is the one that matters. `ENTRY_RE` is a single regular expression
over the whole file and it fails in the worst possible way: a pair that does
not match it is not *reported* as malformed, it is simply not seen. The only
thing that notices is a count of `## Q..` headings disagreeing with the
number parsed. Round-tripping before writing turns a silently-dropped pair
into a refused promotion.

Both versions are parsed, not just the new one, because the count has to go
up by exactly one. A rendering bug that broke an existing pair while adding a
valid new one would otherwise pass -- and the loader *deletes* rows
for pairs it no longer sees, so that bug would quietly drop a golden question
from the set.

### Three limits the format imposes

These are the loader's rules, checked before anything is written:

* **A pair id is `Q` and at least two digits** (`Q\d{2,}`), so the set has
  no ceiling: `Q99` is followed by `Q100`. Until 5.5.1 it was exactly two
  digits, and Q99 was a wall rather than a slope -- Q100 would have parsed as
  nothing at all, so the hundredth promotion was refused. Ids keep two digits
  until they need three, so every existing one is unchanged.
* **The question is delimited by a double quote followed by a newline**, so a
  question ending in one closes its own field.
* **The SQL is fenced and the fence is not escapable**, so SQL containing a
  triple backtick ends the block wherever it appears.

### When the reload fails

The pair is still promoted. The document is the source of truth, it is
already written, and rolling it back because a downstream store did not
rebuild would undo the part that worked. The response says `reloaded: false`
and names which step failed, and the interface says so in words: the pair is
in the golden set, the agent cannot retrieve it yet, here is what to run.

### Why the document and not the database

The golden set has one source of truth and it is the markdown file.
`05_load_golden_pairs.py` parses it into `chunkdb`, `06_embed_golden_pairs.py`
embeds what that produced into `vectordb`, and **step 5 deletes rows whose
pair is no longer in the document**. A pair written straight into the database
is therefore erased the next time anyone reloads.

Writing the document also means a promotion is an ordinary edit to a tracked
file. It shows up in `git diff`, it is reviewed like any other change, and it
is committed by a person.

## Fixing a wrong or incomplete answer

A *wrong* answer is not a golden pair with a mistake in it, and neither is
an *incomplete* one. What they are worth is a record of the mistake and of
what fixed it -- the question, the SQL the agent wrote and what the user was
shown, and the query that should have been generated. So they get their own
panes, their own treatment and their own stores, and none of it touches the
golden set.

### The treatment

The two panes treat a submission the same way, for now; a later version
gives the incomplete pane a process of its own.

1. **See what was generated.** The left half is the submission, as
   submitted: the question, what the user said, the answer, and the agent's
   SQL.
2. **Write the query that should have been.** The editor starts from the
   agent's SQL, because most fixes are an edit rather than a rewrite.
3. **Validate it against the live retail database.** `POST
   /v1/submissions/{id}/validate` runs it and shows the result: its
   columns, the first rows, the row count, the plan cost and how long it
   took -- or, when it fails, Postgres's own message and hint.
4. **Save it.** Only a query that passed validation can be saved, and only
   the exact text that passed: edit it afterwards and the save button goes
   back to disabled until it is validated again. `POST
   /v1/submissions/{id}/fix` then **validates it again**, server-side,
   whatever the browser was told a moment ago -- a rule the client enforces
   is a suggestion.

A later version puts an agent in front of step 3 to sanity-check the
reviewer's query, and a later one still an agent to help write it. Whatever
they suggest, the rule stays: only SQL that runs is stored.

### What validation checks

Three things before anything is sent anywhere, all reported at once:

* **one statement** -- nothing after a semicolon;
* **a SELECT** (or `WITH ... SELECT`), and nothing else;
* **not the agent's query** -- compared with whitespace, case and a
  trailing semicolon ignored. A correction identical to the SQL it corrects
  is a verdict with no fix in it, and is the mistake a person is likeliest
  to make.

Then it runs, the way the agent's own queries run, because it is a
stranger's query against the database the agent answers from:

* as **`nl2sql_reader`**, which can SELECT from the retail tables and
  nothing else;
* inside **`SET TRANSACTION READ ONLY`** as well, so a writing CTE is
  refused by the server even if that role were ever widened -- and the
  transaction is rolled back regardless;
* under a **`statement_timeout`** (`REVIEW_VALIDATE_TIMEOUT_MS`) and a
  **row cap** (`REVIEW_VALIDATE_MAX_ROWS`), so a cross join costs a timeout
  rather than the database.

There is deliberately **no function denylist** here, unlike the agent's
static validator: a reviewer is trusted to write SQL, not to hold the
database's powers, and a denylist is the caller being careful where the
server can simply say no. So every guarantee is the server's, and
[`tests/review/test_validation.py`](../tests/review/test_validation.py) runs
a reviewer's query through the validator to check each one: it runs as
`nl2sql_reader`, read-only, under its timeout, and `set_config` cannot switch
the transaction to read-write, make it the owner, or lift the timeout on the
statement already running. `pg_read_file` is refused, and so are
`pg_cancel_backend` and `pg_terminate_backend` -- every reader session is the
same role, and without
[`docker/reader_role.sql`](../docker/reader_role.sql) revoking them one
reviewer's query could end the agent's queries for everyone else.

`EXPLAIN` runs first, so a query that does not plan is reported without
being executed. A query that runs and returns no rows is **valid with a
warning** -- sometimes zero rows is the answer, and sometimes it is a filter
that matches nothing, and only the reviewer knows which.

### What is stored

| Field | From |
| --- | --- |
| `fix_id` | `W0001`, `W0002`... for a correction, `I0001`... for a completion, so a record says which store it came from wherever it is quoted. Drawn from a sequence per store (6.2), so a deleted fix's id is never given to another |
| `question` | The submission |
| `incorrect_sql`, `incorrect_answer`, `incorrect_columns`, `incorrect_row_count` | What the agent generated and the user was shown |
| `corrected_sql` | The reviewer's query, as it was validated |
| `corrected_columns`, `corrected_rows`, `corrected_row_count`, `corrected_truncated`, `plan_cost` | What it returned: the first 20 rows, enough to see that it is the right answer |
| `user_comment`, `reviewer`, `review_note`, `agent_version`, `submission_id`, `job_id`, `created_at` | Where it came from, and who fixed it |

The staging row moves to the state **`corrected`** and records the fix id
where a promoted one records its pair id. A corrected submission cannot be
edited, fixed a second time, or promoted -- to change it, it is
[put back to pending](#changing-your-mind), which deletes the fix. If a save stores its
record and then loses the connection before the staging row is marked, the
next attempt finds the record, marks the row, and answers `already_fixed`
rather than storing it twice.

### Two stores, not one, and neither the golden set

Each kind has its own database, with its own owner, holding its records and
its RAG side by side -- in the runtime stores' server, `nl2sql-stores`, on
port 5435, since 6.3; each was a Postgres of its own until then, on 5436 and
5437:

| Database | Owner | Records | RAG |
| --- | --- | --- | --- |
| `nl2sql_corrections` | `corrections` | `sql_corrections` | `sql_corrections_vectors` |
| `nl2sql_completions` | `completions` | `sql_completions` | `sql_completions_vectors` |

The RAG half is a bge-m3 embedding of each question (1024 dimensions, an
HNSW cosine index) beside the question, the incorrect SQL and the corrected
SQL, so a retrieval over the vectors is useful without joining back to the
records. It is written at save time, through the same embedder the golden
set's `06_embed_golden_pairs.py` uses, and it is best-effort in the same way
a promotion's reload is: the record is the fact, a save whose embedding host
is down still stores it, and the next save that can reach the host embeds
everything still missing. The response says `embedded: false` and why.

Nothing reads these stores yet. They are what a later version's agent will
retrieve from -- "a question like this one was answered wrongly before, and
this is what fixed it" -- which is why the RAG half is written now rather
than left for a batch job to backfill. They are apart from the golden set
because the golden set is what the agent is measured against, and apart from
each other because a wrong query and an incomplete one are different lessons.

Each store's schema is created on start by this service, so a fresh volume
needs nothing run by hand. Either store being down does not stop the
service: `/readyz` answers 503 and names which one, and only a fix into that
store is refused -- promotion and the other store carry on.

## Changing your mind

A judgement can be taken back (5.4). Under every submission in the interface
is **Change this review**, with two actions:

| | What it does | Asks first? |
| --- | --- | --- |
| **Back to pending** | Puts the submission back in the queue, unjudged. The reviewer and the note are kept, as the history of who looked at it last and why | Only when it had been promoted or fixed |
| **Delete** | Removes the submission from the staging database for good | Always |

Neither is a plain state change when the submission had been acted on. The
queue and what it produced are never allowed to disagree -- a reopened
submission whose pair was still in the golden set would be promoted into it a
second time -- so both take it back out first:

| It had been | Back to pending, or Delete, first |
| --- | --- |
| **promoted** | Takes its pair out of `context_questions/translated_questions.md` -- the previous version kept beside it as `.bak`, as a promotion keeps one -- and reloads both stores, whose loaders drop a pair the document no longer holds. Its promotion log entry goes too. Reopened, the submission gets the pair back **as its draft**, exactly as the document held it, hand edits included |
| **corrected** | Deletes its fix from the corrections or completions store; its vector goes with it, by the vector table's `ON DELETE CASCADE`. Reopened, the submission gets the corrected SQL back in the query editor, ready to edit rather than retype |
| accepted, rejected or pending | Nothing else: nothing was produced |

Taking a pair out is held to the standard putting one in is. The candidate
document is parsed with the loader's own parser before it is written, and has
to have lost exactly that pair and changed **no other** -- every remaining
pair is compared field by field, because a removal that ate the start of the
next pair would leave the count right and that pair broken. The block goes
from its `## Qnn -` heading to the next heading, so a pair taken off the end
leaves the document exactly as it was before the pair was added; a suite
heading written for the pair, with no other pair under it, goes too.

The order is chosen so a failure costs nothing: the pair or the fix comes out
first, then the staging row changes. A document the parser will not let go
of (`not_withdrawable`), or a fix store that cannot be reached
(`unavailable`), refuses the whole thing and changes nothing. A failure
*after* the pair or fix is out heals on the next attempt, which finds it
already gone -- and so does a pair or fix someone removed by hand: it is
reported as not found, not as an error.

Two consequences worth knowing:

- **A pair id can be given out again.** The next promotion takes the id after
  the highest one in the document, so taking out the highest pair frees its
  number. The stores are reloaded in between, so nothing ever holds two
  pairs under one id.
- **A reopened submission is the voter's again.** Pending is pending: the
  row-level policies hand it back to the public process, so the person who
  voted can change their vote again, exactly as they could before anyone
  looked at it.

## Curation

The curation interface writes three things directly, each through routes of
its own, and each run against the live retail database before it is kept:

| | Add | Remove |
| --- | --- | --- |
| A golden pair | `POST /v1/golden`: the draft's SQL is run (it must return rows), then the pair goes through steps 2-6 of [Promotion](#promotion), with no submission behind it | `DELETE /v1/golden/{pair_id}`: withdrawn from the document as a reopened promotion's pair is, both stores reloaded. A pair the review queue produced reopens its submission |
| A fix | `POST /v1/fixes/{kind}`: validated as a reviewer's fix is, and refused when it is the agent's query unchanged; stored with `source` = `curated`, no submission, and its question embedded | `DELETE /v1/fixes/{kind}/{fix_id}`: the fix and its vector. A fix the review queue produced reopens its submission |
| A SQL snippet | `POST /v1/snippets` (and `PUT /v1/snippets/{id}` to change one): run inside its probe query, written into the snippet document, and the store loaded | `DELETE /v1/snippets/{id}`: out of the document and, on the load, out of the store |

Each add has a `validate` route that runs the SQL and writes nothing, and
golden pairs and snippets a `preview` that renders the markdown they would
add. A fix's `validate` takes the agent's query too, when there is one: a fix
identical to it is no fix.

**Snippets** are kept the way the golden pairs are: in a tracked document,
`context_questions/sql_snippets.md`, bind-mounted from the checkout and
committed by a person, from which the store is built. A write renders the
snippet, parses the whole new document with the loader's own parser
(`ragproc.snippets.parse_text`), and checks that every snippet but the one
being written came back unchanged and that one exactly as it went in. Only
then is the document written -- atomically, the previous version kept as
`.md.bak` -- and `07_load_snippets.py` run against the store as its owner.
The loader (re)creates the agent's read-only role, `snippets_reader`, with
the name and password the service is given, embeds only the meanings that
changed, and records the document's hash, which is how `launch.sh` and
`/readyz` know the store holds this document.

A snippet is validated as the kind it is -- how each is run, and what counts
as a warning rather than a refusal, is in
[`curate/README.md`](../curate/README.md#nothing-is-saved-that-has-not-run)
-- after static checks of the text, and its SQL must use the tables it
lists. A snippet listing none has them filled in from the SQL.

**Removing what a submission produced reopens it.** The queue and the stores
never disagree: a pair or a fix taken out here puts its submission back to
pending, with the draft or the corrected query it had, exactly as
*Back to pending* in the review interface does in the other direction.

`GET /v1/schema` lists the retail tables and columns as `nl2sql_reader` sees
them, for the snippet form's table picker.

## Running it

```bash
./start.sh --review         # everything, with both pages opened for you
```

That is the whole thing from cold: the databases, the API, the web interface
people vote in, the staging database, the corrections and completions stores,
this service, and the review interface -- then <https://localhost:8080> and
<https://localhost:8081> in your browser, the second in a window of its own.

The same containers without the browser step, or a smaller subset:

```bash
./launch.sh --review        # staging database, fix stores, review service, review interface
./launch.sh --feedback      # just the staging database, so verdicts are kept
```

`--review` implies `--api`: the interface is nothing without the service,
and the staging database is nothing without the API that writes to it. Since
6.3 the staging database starts with the other databases, so a verdict is
staged whenever the API is up -- once the review service has started once,
since it makes the table and the API's role -- and `--feedback` is the same
as `--api`.

On a checkout set up before this existed, `.env` still pins the older image
tags and knows nothing about the review service's image, so run
`./setup.sh --review` once first. It re-pins the tags and pulls them, carrying
over the Ollama host and everything else the last run chose.

Promotion writes the `context_questions/` directory of *this checkout*,
bind-mounted into the container. That is deliberate. Written into a
container's own copy, the golden set would grow somewhere nobody can see.

The stores are reloaded by a promotion and by nothing else, so a document
that arrives with pairs from elsewhere -- promoted on another machine and
committed -- is ahead of them until something loads it. `launch.sh` says when
the two differ, and `./start.sh --load-golden` runs the same two loaders
before anything is asked, in this service's image, without starting it.

Directly:

```bash
python -m nl2sql_review --no-tls --port 8444
python -m nl2sql_review --print-settings
python -m nl2sql_review --no-reload-vectors     # no embedding host here
```

## Endpoints

| Method | Path | Auth | What |
| --- | --- | --- | --- |
| `GET` | `/` | no | Service banner and where everything is |
| `GET` | `/healthz` | no | The process is alive |
| `GET` | `/readyz` | no | Staging database, retail database and both fix stores reachable; document readable *and writable*; the snippet document parses, and whether the store holds it |
| `GET` | `/openapi.json` | no | The schema |
| `GET` | `/docs` | no | The same thing, browsable |
| `GET` | `/v1/meta` | yes | States, verdicts, golden count, next pair id, queue counts (overall and per verdict), fix counts per store, warnings |
| `GET` | `/v1/submissions` | yes | The queue. `?state=` `?verdict=` `?limit=` `?offset=` |
| `GET` | `/v1/submissions/{id}` | yes | One submission, with a seeded draft if it has none |
| `PATCH` | `/v1/submissions/{id}` | yes | Accept, reject, or save a draft |
| `POST` | `/v1/submissions/{id}/reopen` | yes | Back to pending, taking a promoted pair or a stored fix back out first |
| `DELETE` | `/v1/submissions/{id}` | yes | Remove it for good, taking a promoted pair or a stored fix back out first |
| `POST` | `/v1/submissions/{id}/preview` | yes | The markdown a draft would add, without writing |
| `POST` | `/v1/submissions/{id}/promote` | yes | Write the pair into the golden set. Correct answers only |
| `POST` | `/v1/submissions/{id}/validate` | yes | Run a corrected query against the live retail database. Wrong and incomplete answers only |
| `POST` | `/v1/submissions/{id}/fix` | yes | Validate again and store the fix in its store, recorded as the caller's |
| `GET` | `/v1/fixes/{kind}` | yes | `corrections` or `completions`: what the store holds, newest first. `?limit=` |
| `GET` | `/v1/golden` | yes | The set as the document holds it |
| `GET` | `/v1/promotions` | yes | What has been promoted, newest first |
| `POST` | `/v1/golden/validate` | yes | Run a pair's SQL against the live retail database |
| `POST` | `/v1/golden/preview` | yes | The markdown a hand-written pair would add, without writing |
| `POST` | `/v1/golden` | yes | Validate a hand-written pair's SQL, then write it into the golden set |
| `DELETE` | `/v1/golden/{pair_id}` | yes | Take a pair out of the golden set, reopening the submission it came from |
| `POST` | `/v1/fixes/{kind}/validate` | yes | Run a fix's SQL against the live retail database |
| `POST` | `/v1/fixes/{kind}` | yes | Validate a fix with no submission behind it, then store it, recorded as the caller's |
| `DELETE` | `/v1/fixes/{kind}/{fix_id}` | yes | Take a fix out of its store, reopening the submission it came from |
| `GET` | `/v1/snippets` | yes | The snippets as their document holds them, the next id, and what the store holds |
| `POST` | `/v1/snippets/validate` | yes | Run a snippet inside its probe query |
| `POST` | `/v1/snippets/preview` | yes | The section a snippet would write, without writing |
| `POST` | `/v1/snippets` | yes | Validate a snippet, write it into its document, and load the store |
| `PUT` | `/v1/snippets/{id}` | yes | The same, for a change to one |
| `DELETE` | `/v1/snippets/{id}` | yes | Take a snippet out of its document, and out of the store |
| `GET` | `/v1/schema` | yes | The retail tables and columns, as the read-only role sees them |

`promote`, `fix`, `reopen` and `DELETE`, and the curation routes' `POST`,
`PUT` and `DELETE`, are the only calls with a consequence outside this
service's staging database, and none of them is a field on a `PATCH`. That is what stops a form which saves as you type from
writing the golden question set or a fix store, or taking anything out of
either. `validate` is a POST too, but it changes nothing: its transaction is
read-only and rolled back.

`reopen` and `DELETE` answer with what they did: the submission (as it is
now, or as it was), and `withdrawn` -- which pair or fix came out, whether it
was found, and for a pair the counts, the backup and the reload, as a
promotion reports them.

`state` cannot be set to `promoted` or `corrected` through `PATCH`. They are
things that happen, not things that are set; setting one by hand would mark
a submission as being in the golden set, or in a fix store, with nothing
written there -- the one inconsistency this service exists to prevent.

The error envelope is the agent API's, so one client parses both:
`{"error": {"code": "...", "message": "..."}}`.

| Code | Status | Meaning |
| --- | --- | --- |
| `invalid_request` | 422 | The body or query string is wrong |
| `unauthorized` | 401 | Missing or wrong token |
| `not_found` | 404 | No such submission -- including one another reviewer deleted while this request was judging or reopening it |
| `already_promoted` | 409 | It is in the golden set; reopen it to change it |
| `already_fixed` | 409 | It is in a fix store; reopen it to change it. It is not fixed twice |
| `already_pending` | 409 | Reopening something nobody has judged |
| `rejected` | 409 | Accept it before promoting or fixing it |
| `wrong_workflow` | 409 | Promoting a wrong or incomplete answer, or validating or fixing a correct one |
| `not_promotable` | 422 | The draft cannot become a pair. Every reason, not the first |
| `not_valid` | 422 | The query -- a corrected query, a golden pair's, or a snippet -- did not pass validation. Every reason, not the first |
| `not_withdrawable` | 422 | Taking the pair out would leave a document the loader cannot read, or change another pair. Nothing was changed |
| `not_writable` | 422 | A snippet change would leave a document the loader cannot read, or change another snippet. Nothing was changed |
| `unavailable` | 503 | A fix store could not be written or read. Nothing was changed |

## Configuration

### The socket

| Variable | Default | What |
| --- | --- | --- |
| `REVIEW_HOST` | `0.0.0.0` | Interface to bind |
| `REVIEW_PORT` | `8444` | Port to bind and publish |
| `REVIEW_ROOT_PATH` | *(none)* | Path prefix behind a reverse proxy |
| `REVIEW_TLS_ENABLED` | `true` | Serve HTTPS |
| `REVIEW_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` | PEM certificate |
| `REVIEW_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` | PEM private key |
| `REVIEW_DOCS_ENABLED` | `true` | Serve `/docs` and `/redoc`; `false` leaves only `/openapi.json` |
| `REVIEW_LOG_LEVEL` | `info` | uvicorn's log level |

This service presents **a certificate of its own**, which the stack's pki
service issues from its development CA before it starts (6.1; until then it
presented the agent API's, whose key was then in eleven containers). It
never writes one: the issuing is the pki service's, so there is one copy of
the certificate code and one CA its page's proxy verifies against. The
names it covers are `REVIEW_TLS_HOSTNAMES`, which must include
`nl2sql-review`; compose's default does.

### Who may call

| Variable | Default | What |
| --- | --- | --- |
| `AUTH_ENABLED` | `true` | Accept signed-in people ([`auth/README.md`](../auth/README.md)). Only `false`, set by name, turns it off, and the service then says it is open |
| `AUTH_PUBLIC_KEY_FILE` | `/etc/nl2sql/auth/session.pub` | The auth service's public key, which sessions are checked against |
| `AUTH_COOKIE_NAME` | `nl2sql_session` | The cookie a browser's session is in |
| `REVIEW_REVIEWER_ROLES` | `nl2sql_reviewers` | Who may work the review queue: judge, promote, fix, reopen, delete |
| `REVIEW_CURATOR_ROLES` | `nl2sql_curators` | Who may write directly: snippets, golden pairs, corrections and completions |
| `REVIEW_TOKEN` | *(none)* | A static service token: required on `/v1` when sign-in is off, accepted beside sessions when it is on |
| `REVIEW_TOKEN_NAME` | `review-token` | Who the token is: what it does is recorded as `token:<name>` |
| `REVIEW_TOKEN_ROLES` | *(see below)* | The roles it holds, and no others. Unset: with sign-in on the reviewer roles only; with it off both, because the pages' proxies send it for everyone |
| `REVIEW_CORS_ORIGINS` | *(none)* | Browser origins allowed to call it directly; its pages reach it through their own nginx |

With sign-in on, a reviewer's or curator's own name is what is recorded on
everything they decide -- not a name typed into a form -- and the SQL they
validate runs as their own database role, with their name in the
transaction's `application_name` (`nl2sql:review:<person>`, 6.2). Reading
the queue and the stores is open to either group; changing anything takes
the group the route belongs to, and each route is on a router that carries
that group's guard (6.2). A static token is a caller of its own since 6.2:
recorded as `token:<name>` -- never as the `X-Reviewer` it sends, which only
an open service with no token records -- and holding `REVIEW_TOKEN_ROLES`:
with sign-in on it reviews, and curating by token is something to grant
there by name. A session signed out, or from before a password change or a
lock, is refused with `401 session_revoked` within a minute
([`auth/README.md`](../auth/README.md)). `/readyz` gives the reason a
dependency is down to an administrator only.

With sign-in off, the token is not optional the way the agent's is. A caller
here can edit the question set the agent is measured against; `/readyz` and
the start-up banner both say so loudly when it is unset.

There is no query-string token, unlike the agent API. That one accepts one
because `EventSource` cannot set headers; nothing here streams, so the token
never has to go somewhere that ends up in an access log.

### The staging database

Since 6.3 every password here is a file (V6-38): a URL is read with its
password replaced by the one in the file `<NAME without _URL>_PASSWORD_FILE`
names -- `FEEDBACK_DB_PASSWORD_FILE` beside `FEEDBACK_DB_URL` -- and a
password or token by itself from `<NAME>_FILE`. Compose gives only the
files, from `secrets/`, so none is in the container's environment; a URL
with its password in it, or the variable itself, still works for a service
started by hand.

| Variable | Default | What |
| --- | --- | --- |
| `FEEDBACK_DB_URL` | `postgresql://feedback:feedback@localhost:5435/nl2sql_feedback` | As the owner |
| `FEEDBACK_WRITER_PASSWORD` | `nl2sql_feedback_writer` | Reset on the writer role at every start |
| `REVIEW_MANAGE_SCHEMA` | `true` | Create the schema and the role on start |

### Promotion

| Variable | Default | What |
| --- | --- | --- |
| `REVIEW_DOCUMENT` | `/app/context_questions/translated_questions.md` | The golden question set |
| `REVIEW_RAG_DIR` | `/app/rag` | Where `ragproc` lives |
| `REVIEW_RELOAD_CONTEXT` | `true` | Load the context store (step 5) after writing |
| `REVIEW_RELOAD_VECTORS` | `true` | Embed what changed (step 6) after that |
| `REVIEW_RELOAD_TIMEOUT_SECONDS` | `600` | How long one request to the embedding host may take |
| `CHUNK_DB_URL` | the compose chunkdb | Context store |
| `VECTOR_DB_URL` | the compose vectordb | Vector store |
| `OLLAMA_URL` / `EMBED_MODEL` | `http://localhost:11434` / `bge-m3` | For embedding |

The loaders are `rag/`'s own, `ragproc.loaders` -- the code of steps 5, 6
and 7 -- called in this process (6.2; they ran as scripts until then). They
already handle the upsert, the delete of pairs no longer in the document,
the BM25 rebuild and incremental re-embedding; a second implementation here
would be a second set of rules to keep in agreement with the first. Each
store's URL, password and all, is an argument to a function rather than
something on a command line `ps` shows anyone on the host. Everything from
reading the document to the last load is done holding the write lock -- in
this process, and an advisory lock on the documents' directory between
processes -- so two curators cannot both take Q47.

The service writes the checkout's documents as whoever owns them (6.2):
started as root, it becomes the owner of the mounted `context_questions/`
before it reads anything, so a promoted pair in the working tree is theirs,
as if they had typed it. A directory root owns -- the copy in the image,
with nothing mounted -- is written as the image's account, `nl2sql` (10001),
which is also the group its TLS key is read through.

### Fixes

| Variable | Default | What |
| --- | --- | --- |
| `RETAIL_DB_URL` | `postgresql://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail` | Where a corrected query is validated. The agent's read-only role, never the owner |
| `REVIEW_VALIDATE_TIMEOUT_MS` | `30000` | `statement_timeout` for one validation |
| `REVIEW_VALIDATE_MAX_ROWS` | `200` | Rows read before a result is called truncated |
| `CORRECTIONS_DB_URL` | `postgresql://corrections:corrections@localhost:5435/nl2sql_corrections` | Where wrong answers' fixes go, as the owner |
| `COMPLETIONS_DB_URL` | `postgresql://completions:completions@localhost:5435/nl2sql_completions` | Where incomplete answers' fixes go, as the owner |
| `REVIEW_EMBED_FIXES` | `true` | Embed each fix's question at save time, with `OLLAMA_URL` / `EMBED_MODEL` |

Compose points every URL here at the stack's own databases -- the runtime
stores at `nl2sql-stores`, with no password in the URL and the password from
the secret file beside it -- and takes an override for each:
`REVIEW_FEEDBACK_DB_URL`, `REVIEW_RETAIL_DB_URL`, `REVIEW_CORRECTIONS_DB_URL`,
`REVIEW_COMPLETIONS_DB_URL`, `REVIEW_CHUNK_DB_URL`, `REVIEW_VECTOR_DB_URL` and
`REVIEW_SNIPPETS_DB_URL`. `REVIEW_EMBED_FIXES=false` stores records
without their vectors -- the start-up banner, `/readyz` and `/v1/meta` all
warn that nothing will be retrievable from them until they are embedded.

### SQL snippets

| Variable | Default | What |
| --- | --- | --- |
| `REVIEW_SNIPPETS_DOCUMENT` | `/app/context_questions/sql_snippets.md` | The snippet document |
| `SNIPPETS_DB_URL` | `postgresql://snippets:snippets@localhost:5435/nl2sql_snippets` | The snippet store, as its owner |
| `SNIPPETS_READER_USER` / `SNIPPETS_READER_PASSWORD` | `snippets_reader` / `snippets_reader` | The read-only role the loader (re)creates for the agent |
| `REVIEW_RELOAD_SNIPPETS` | `true` | Load the snippet store (step 7) after writing |

Snippets are validated with `RETAIL_DB_URL` and the two limits above, and
embedded with `OLLAMA_URL` / `EMBED_MODEL`. Compose takes the store's URL as
`REVIEW_SNIPPETS_DB_URL`, built from `SNIPPETS_DB_USER` and
`SNIPPETS_DB_NAME` by default -- the database and owner the dbprep one-shot
makes -- with the password in `secrets/snippets_db_password`; the reader's
name and password file are the agent's too.
`REVIEW_RELOAD_SNIPPETS=false` writes the document and leaves the store
behind it, and `/readyz` and the start-up banner say so.

## The interface

A React/TypeScript single page in [`gui/`](gui), built to static files and
served over HTTPS by the proxy image's `review` page
([`proxy/README.md`](../proxy/README.md)), which proxies this service and the
auth service.
With sign-in on, the page asks who you are and admits `nl2sql_reviewers`;
the session cookie is what reaches the service. With it off, nginx holds the
token so the reviewer's browser never does.

A **separate npm project** from [`../gui`](../gui), and the separation is
physical rather than conventional. Two Vite entry points in one project share
a build, and the public GUI would then be serving the interface that
rewrites the golden question set to anyone who could reach it. A second
`package.json` is a few more files and a boundary that cannot be crossed by
forgetting something. Since 6.3 the two bundles are in one image, and each
container serves only the page `NL2SQL_PAGE` names, from that page's own
root: the public GUI's container answers nothing with this page's files.

The page's own settings, set in `.env` by the names on the left; compose
hands each to the proxy image as the name in the middle.

| Variable | Sets | Default |
|---|---|---|
| `REVIEW_GUI_PORT` | `PROXY_PORT` | `8081` |
| `REVIEW_GUI_UPSTREAM` | `UPSTREAM` | `https://nl2sql-review:8444` |
| `REVIEW_GUI_SSL_NAME` | `UPSTREAM_SSL_NAME` | `nl2sql-review` -- a name the service's certificate covers (`REVIEW_TLS_HOSTNAMES`) |
| `REVIEW_GUI_CACERT` | `UPSTREAM_CACERT` | `/etc/nl2sql/tls/ca.crt`, the stack's CA, beside the page's own certificate |
| `REVIEW_GUI_READ_TIMEOUT` | `UPSTREAM_READ_TIMEOUT` | `900s` -- longer than a promotion, which runs both loaders |
| `REVIEW_GUI_RESOLVER` | `PROXY_RESOLVER` | `127.0.0.11`, Docker's DNS |
| `secrets/review_token` | `UPSTREAM_TOKEN_FILE` | *(empty)*: sent only with sign-in off |
| `AUTH_ENABLED` | `AUTH_ENABLED` | `true`: the page asks who you are and admits `nl2sql_reviewers` |
| `GUI_AUTH_UPSTREAM` | `AUTH_UPSTREAM` | `https://nl2sql-auth:8446`, where the sign-in form posts |
| `GUI_AUTH_SSL_NAME` | `AUTH_SSL_NAME` | `nl2sql-auth` |
| `GUI_AUTH_CACERT` | `AUTH_CACERT` | `/etc/nl2sql/tls/ca.crt` |
| `GUI_TLS_ENABLED` | `PROXY_TLS_ENABLED` | `true`: the page is HTTPS, with its own certificate, so a password never crosses in clear |
| `GUI_TLS_CERT_FILE` | `PROXY_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` |
| `GUI_TLS_KEY_FILE` | `PROXY_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` |

The `GUI_` ones are shared: one line in `.env` sets them for every page.

Three tabs across the top, one per verdict, each with a count of what is
still pending in it: **Correct → golden set**, **Wrong → corrections** and
**Correct but incomplete → completions**. A pane's queue holds only its own
verdict, so a reviewer working through wrong answers never has a correct one
to skip over.

The layout is the argument: the left half of the detail pane is what a user
said and cannot be edited, the right half is what is being built out of it
-- a golden pair in the Correct pane, a corrected query in the other two.
That they are two panels rather than one form is what keeps a curator honest
-- it is very easy to fix a question until it matches the SQL, and a golden
pair built that way tests nothing.

In a fix pane the right half is the query editor, a **Validate against the
live database** button, and the result: the rows it returned, or Postgres's
reason it did not. The save button is enabled only while the editor holds
exactly the text that last passed. The status bar counts what each fix
store holds.

Beneath every submission, **Change this review** puts it back to pending or
deletes it -- [Changing your mind](#changing-your-mind) has what each does
to a promoted or fixed one. Anything that reaches past the staging table asks
first, in the words of the file or store it is about to change, and the
result is reported the way a promotion's is: what came out, the counts, and
whether the stores caught up.

```bash
cd review/gui
npm install
npm run dev      # proxies https://localhost:8444
npm run test     # review GUI: 182 tests, 100% coverage
```

## Tests

```bash
pytest tests/review                  # the service, offline
pytest tests/review --run-docker     # plus the live staging database
pytest tests/review --run-node       # plus the review GUI's own suite
```

The split matters. Every route is exercised against a fake repository, which
is what makes the whole HTTP surface run on an ordinary `pytest` with no
container. A fake cannot tell the truth about a privilege, though, and the
security story here is made entirely of privileges -- so
`test_store_live.py` creates the schema against a real Postgres, connects as
the writer role, and tries the things it must not be able to do.

The fix routes run against a fake validator and fake stores for the same
reason. `test_validation.py` then runs the validator against the live retail
database -- a writing CTE refused, a timeout reported, Postgres's hint passed
through -- and `test_corrections_live.py` creates both stores' schemas in a
real pgvector Postgres and checks the ids, the one-record-per-submission
constraint, the embedding catch-up and a nearest-neighbour search.

The promotion tests use a real copy of the real question document, not a
miniature stand-in. The whole contract is a bet that a rendered pair survives
the loader's parser, and that bet is only worth anything against the file the
loader actually reads: the original 45 pairs and whatever has been promoted
since, 25 suites, prose between them, and a `## How to read a pair` heading
that is not a pair and must not be counted as one. How many pairs there are,
and the id the next promotion takes, are read from the copy rather than
written into the tests: the document is the live golden set, and the tests
used to fail for the first person who promoted anything through the review
interface. Withdrawal is tested against the same copy, taking out a pair
just promoted, one between two others, and one of the original pairs between
`---` separators.
