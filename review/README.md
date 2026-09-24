# Feedback review

Turning "that answer was wrong" into a golden question.

The web interface asks whether an answer was right. This is where the
answers to that question go, and where they are turned into the thing they
are worth turning into: a verified question/SQL pair in the set the agent is
measured and prompted against.

Two processes and a database:

```
browser ──nginx──▶ agent API   POST /v1/questions/{id}/feedback
                       │
                       └── INSERT only ──▶ feedbackdb  (staging)
                                                │
reviewer ──nginx──▶ review service ──owner──────┘
                       │
                       └─ promote ─▶ context_questions/translated_questions.md
                                       └─▶ 05_load_golden_pairs ─▶ chunkdb
                                       └─▶ 06_embed_golden_pairs ─▶ vectordb
```

## Contents

- [Why it is a separate service](#why-it-is-a-separate-service)
- [What a verdict carries](#what-a-verdict-carries)
- [The fence around the public process](#the-fence-around-the-public-process)
- [Promotion](#promotion)
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
the 45 verified pairs the agent retrieves worked examples from and that every
benchmark run is scored against. Giving those powers to the agent's own API
because both happen to be FastAPI would put the benchmark inside the blast
radius of the thing being benchmarked.

So the public API holds an INSERT-only role on one table, and everything else
lives here: its own container, its own port, its own token.

## What a verdict carries

A verdict arrives with a snapshot of the job it is about: the question, the
generated SQL, the answer, the narrative, the intent, the tables, the result
shape, and any comment the user added.

The snapshot is taken at vote time rather than looked up later because there
is no later -- a job is forgotten after `API_JOB_TTL_SECONDS`, and a record
holding only a job id would be pointing at nothing within the hour. The SQL
in particular is the whole reason a "yes" is interesting: it is the candidate
a golden pair gets built from.

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

## Promotion

A reviewer opens a submission, reads what was asked and what the agent
answered, and writes the parts a thumbs-up cannot carry:

| From the submission | From the reviewer |
| --- | --- |
| `title` (a first guess), `question`, `tables`, `sql_code` | `keywords`, `reasoning_target`, `result` |

Those three are judgements about what the question *tests* -- which words
someone would search for, where generated SQL typically goes wrong, what
coming back looks like. Nothing in a vote carries them, and the form leaves
them empty rather than guessing: a reviewer skimming a pre-filled form
approves it, and the BM25 index fills up with keywords nobody chose.

Promoting then does this, in this order:

1. **Validate** the draft against the loader's format rules.
2. **Render and append** the pair, in memory.
3. **Parse the result with the loader's own parser** -- the actual
   `ragproc.golden_pairs.parse_document`, on the actual new document --
   and check that it yields exactly one more pair and that every field of
   the new one came back the way it went in.
4. **Write atomically**: a temporary file in the same directory, then
   `os.replace`, keeping the previous version as `.md.bak`.
5. **Reload** the context store and the vectors.

Step 3 is the one that matters. `ENTRY_RE` is a single regular expression
over the whole file and it fails in the worst possible way: a pair that does
not match it is not *reported* as malformed, it is simply not seen. The only
thing that notices is a count of `## Q..` headings disagreeing with the
number parsed. Round-tripping before writing turns a silently-dropped pair
into a refused promotion.

Both versions are parsed, not just the new one, because the count has to go
up by exactly one. A rendering bug that broke an existing pair while adding a
valid new one would otherwise pass -- and step 5 of the loader *deletes* rows
for pairs it no longer sees, so that bug would quietly drop a golden question
from the set.

### Three limits the format imposes

These are the loader's rules, checked before anything is written:

* **`Q\d{2}` is exactly two digits**, so the set tops out at Q99. At 45 pairs
  that is a long way off, but it is a wall rather than a slope: Q100 would
  parse as nothing at all.
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

## Running it

```bash
./launch.sh --review        # staging database, review service, review interface
./launch.sh --feedback      # just the staging database, so verdicts are kept
```

`--review` implies `--feedback`, which implies `--api`: the interface is
nothing without the service, the service is nothing without the database, and
the database is nothing without the API that writes to it.

The interface is then at <http://localhost:8081>, and promotion writes the
`context_questions/` directory of *this checkout*, bind-mounted into the
container. That is deliberate. Written into a container's own copy, the
golden set would grow somewhere nobody can see.

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
| `GET` | `/readyz` | no | Staging database reachable, document readable *and writable* |
| `GET` | `/openapi.json` | no | The schema |
| `GET` | `/docs` | no | The same thing, browsable |
| `GET` | `/v1/meta` | yes | States, golden count, next pair id, queue counts, warnings |
| `GET` | `/v1/submissions` | yes | The queue. `?state=` `?verdict=` `?limit=` `?offset=` |
| `GET` | `/v1/submissions/{id}` | yes | One submission, with a seeded draft if it has none |
| `PATCH` | `/v1/submissions/{id}` | yes | Accept, reject, or save a draft |
| `POST` | `/v1/submissions/{id}/preview` | yes | The markdown a draft would add, without writing |
| `POST` | `/v1/submissions/{id}/promote` | yes | Write the pair into the golden set |
| `GET` | `/v1/golden` | yes | The set as the document holds it |
| `GET` | `/v1/promotions` | yes | What has been promoted, newest first |

`promote` is the only call with a consequence outside this service's own
database, and it is the only one that is a POST to a named action rather than
a field on a `PATCH`. That is what stops a form which saves as you type from
writing the golden question set.

`state` cannot be set to `promoted` through `PATCH`. It is something that
happens, not something that is set; setting it by hand would mark a
submission as being in the golden set with nothing written to the document --
the one inconsistency this service exists to prevent.

The error envelope is the agent API's, so one client parses both:
`{"error": {"code": "...", "message": "..."}}`.

| Code | Status | Meaning |
| --- | --- | --- |
| `invalid_request` | 422 | The body or query string is wrong |
| `unauthorized` | 401 | Missing or wrong token |
| `not_found` | 404 | No such submission |
| `already_promoted` | 409 | It is in the golden set; the record is not editable |
| `rejected` | 409 | Accept it before promoting it |
| `not_promotable` | 422 | The draft cannot become a pair. Every reason, not the first |

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

This service **presents the certificate the agent API generates** and never
writes one of its own. A second copy of the certificate code would be 250
lines whose only job is to agree with the first copy, and the two would be
discovered to disagree by a client failing to connect. The cost of that
subtraction is a configuration requirement instead: `API_TLS_HOSTNAMES` has
to cover `nl2sql-review`, which compose does.

### Who may call

| Variable | Default | What |
| --- | --- | --- |
| `REVIEW_TOKEN` | *(none)* | Require this bearer token on `/v1` |
| `REVIEW_CORS_ORIGINS` | `*` | Browser origins allowed to call it |

Not optional the way the agent's token is. A caller here can edit the
question set the agent is measured against; `/readyz` and the start-up banner
both say so loudly when it is unset.

There is no query-string token, unlike the agent API. That one accepts one
because `EventSource` cannot set headers; nothing here streams, so the token
never has to go somewhere that ends up in an access log.

### The staging database

| Variable | Default | What |
| --- | --- | --- |
| `FEEDBACK_DB_URL` | `postgresql://feedback:feedback@localhost:5435/nl2sql_feedback` | As the owner |
| `FEEDBACK_WRITER_PASSWORD` | `nl2sql_feedback_writer` | Reset on the writer role at every start |
| `REVIEW_MANAGE_SCHEMA` | `true` | Create the schema and the role on start |

### Promotion

| Variable | Default | What |
| --- | --- | --- |
| `REVIEW_DOCUMENT` | `/app/context_questions/translated_questions.md` | The golden question set |
| `REVIEW_RAG_DIR` | `/app/rag` | Where the loader scripts live |
| `REVIEW_RELOAD_CONTEXT` | `true` | Run `05_load_golden_pairs.py` after writing |
| `REVIEW_RELOAD_VECTORS` | `true` | Run `06_embed_golden_pairs.py` after that |
| `REVIEW_RELOAD_TIMEOUT_SECONDS` | `600` | Give up on a loader that hangs |
| `CHUNK_DB_URL` | the compose chunkdb | Context store |
| `VECTOR_DB_URL` | the compose vectordb | Vector store |
| `OLLAMA_URL` / `EMBED_MODEL` | `http://localhost:11434` / `bge-m3` | For embedding |

The loaders are run **as scripts**, not imported. They already handle the
upsert, the delete of pairs no longer in the document, the BM25 rebuild and
incremental re-embedding; a second implementation here would be a second set
of rules to keep in agreement with the first.

## The interface

A React/TypeScript single page in [`gui/`](gui), built to static files and
served by nginx, which proxies this service and holds the token so the
reviewer's browser never does.

A **separate npm project** from [`../gui`](../gui), and the separation is
physical rather than conventional. Two Vite entry points in one project share
a build, and the public GUI's image would then be serving the interface that
rewrites the golden question set to anyone who could reach it. A second
`package.json` is a few more files and a boundary that cannot be crossed by
forgetting something.

The layout is the argument: the left half of the detail pane is what a user
said and cannot be edited, the right half is the pair being built out of it.
That they are two panels rather than one form is what keeps a curator honest
-- it is very easy to fix a question until it matches the SQL, and a golden
pair built that way tests nothing.

```bash
cd review/gui
npm install
npm run dev      # proxies https://localhost:8444
npm run test     # review GUI: 93 tests, 100% coverage
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

The promotion tests use a real copy of the real question document, not a
miniature stand-in. The whole contract is a bet that a rendered pair survives
the loader's parser, and that bet is only worth anything against the file the
loader actually reads: 45 pairs, 25 suites, prose between them, and a
`## How to read a pair` heading that is not a pair and must not be counted as
one.
