# Curation interface

What the agent learns from, written directly: the SQL snippets its generator
is shown, the golden pairs it retrieves worked examples from and is measured
against, and the corrections and completions a later agent will retrieve
from. Each one is run against the live retail database before it can be
saved.

```bash
./start.sh --curate          # opened for you, in a window of its own
./launch.sh --curate         # the same containers, without the browser step
open https://localhost:8083
```

It is a page, not a service. Behind it is the review service
([`review/`](../review/README.md#curation)), which does every write, so the
page holds no credential: nginx adds the review service's token to each
request it proxies, and the browser never sees it. That is the review
interface's arrangement too, and for its reason: the token can rewrite the
golden set. Both pages can be up at once, against the same service, and
`--curate` brings up the service, the two fix stores and this page without
the review interface or its queue.

## Contents

- [The three tabs](#the-three-tabs)
- [Nothing is saved that has not run](#nothing-is-saved-that-has-not-run)
- [Where a write goes](#where-a-write-goes)
- [Configuration](#configuration)
- [The project](#the-project)
- [Tests](#tests)

## The three tabs

| Tab | Lists | Writes |
|---|---|---|
| **SQL snippets** | Every snippet in `context_questions/sql_snippets.md`, by kind and by search, beside what the store holds and whether it holds this document | A new snippet, a change to one, or its removal |
| **Golden pairs** | Every pair in `context_questions/translated_questions.md` | A pair no feedback produced, or a pair's removal |
| **Corrections & completions** | Either fix store | A fix no submission produced, or a fix's removal |

A **snippet** is one piece of SQL beside what it means: a join (the `JOIN`
and the reason for it), a filter (a condition, without the `WHERE`), a
measure (an aggregate) or a dimension (an expression to group by). The form
asks for it in the order a curator thinks it: its kind and name, what it
means, the phrases a question says it with, then the `FROM` clause it is
written over and the SQL. *Tables* can be left empty; the service fills it
from the SQL. The *Note* is shown to the SQL Generator with the snippet,
so it is the place for the mistake the piece prevents -- a cast, a key, a
column that looks right and is not. The README's
[SQL snippets and curation](../README.md#sql-snippets-and-curation) says how
the agent finds and uses them.

Keywords are matched as phrases: a phrase matches a question that has every
one of its words, in any order and any inflection. So a two-word phrase is
usually better than one common word -- "store brands" rather than "store",
which every question about stores would match.

## Nothing is saved that has not run

*Validate against the live database* sends the text to the service, which
runs it as `nl2sql_reader`, in a read-only transaction, under a timeout:

| What | Is run as | Passes when |
|---|---|---|
| a join | `SELECT * FROM <applies to> <join>` | it runs. More rows after the join than before is a fan-out, and fewer is rows dropped; each is a warning, said with both counts |
| a filter | the `FROM` clause with the filter as its `WHERE` | it runs. Matching no rows, or every row, is a warning |
| a measure | the measure over the `FROM` clause | it runs and returns one row |
| a dimension | the `FROM` clause, grouped by the dimension | it runs |
| a golden pair | the query | it runs and returns rows: in the golden set an empty answer reads as a failure |
| a fix | the query | it runs, and differs from the agent's query when that is given: a fix that changes nothing is not a fix |

Before any of that, a snippet is checked as text: no `;`, comments or code
fences; balanced parentheses and quotes; a join that starts with `JOIN`; no
`SELECT` in the other three. The tables its SQL uses must be the ones it
lists. The page shows the query the snippet was checked inside, not only the
verdict, along with the rows it returned and the planner's cost.

The save button is bound to the exact text that passed. One keystroke in the
SQL afterwards and it is disabled again, saying the text was edited since it
was validated; the words around the SQL -- the name, the meaning, the note
-- can change freely. The service runs the SQL again when it saves, whatever
the page said, so a page that has been tampered with saves nothing either.

## Where a write goes

| | Written to | Then |
|---|---|---|
| a snippet | `context_questions/sql_snippets.md`, the previous version kept as `.bak` | loaded into the snippet store, embedding only what changed; the agent is shown it from the next question |
| a golden pair | `context_questions/translated_questions.md`, likewise | loaded into the context store and the vectors |
| a fix | its store, with an embedding of its question | -- |

The documents are the ones in this checkout, bind-mounted into the review
service, so a snippet or a pair shows up in `git diff` and is committed by a
person. Every write is parsed back with the loader's own parser and compared
field by field before it is kept: a section that does not parse is not
reported as malformed, it is simply not seen.

A removal asks first, in the page rather than in a browser dialog, saying
which file and which store it leaves. A golden pair or a fix that came from
the review queue reopens its submission when it is removed, so the queue and
the stores never disagree. A loader that fails after the document was
written is shown as a failed step, in the loader's words: the document holds
the change and the store catches up on the next load, which
`launch.sh` runs on every start.

## Configuration

Read by the nginx template and its start-up script, and set by compose from
the names on the left.

| Variable | Sets | Default |
|---|---|---|
| `CURATE_GUI_PORT` | `CURATE_GUI_PORT` | `8083` |
| `CURATE_GUI_UPSTREAM` | `CURATE_UPSTREAM` | `https://nl2sql-review:8444` |
| `CURATE_GUI_SSL_NAME` | `CURATE_SSL_NAME` | `nl2sql-review` |
| `CURATE_GUI_CACERT` | `CURATE_CACERT` | `/etc/nl2sql/tls/ca.crt` |
| `CURATE_GUI_READ_TIMEOUT` | `CURATE_READ_TIMEOUT` | `900s` -- a save waits for the loaders, and embedding takes a while |
| `CURATE_GUI_RESOLVER` | `CURATE_GUI_RESOLVER` | `127.0.0.11`, Docker's DNS |
| `REVIEW_TOKEN` | `CURATE_TOKEN` | *(unset)*: no `Authorization` header is sent at all -- nor with sign-in on, whatever it holds |
| `AUTH_ENABLED` | `AUTH_ENABLED` | `true`: the page asks who you are and admits `nl2sql_curators`; their session, not a token, reaches the service, and their name is on what they write |
| `GUI_AUTH_UPSTREAM` | `AUTH_UPSTREAM` | `https://nl2sql-auth:8446`, where `/auth/` is proxied: the sign-in form posts there |
| `GUI_AUTH_SSL_NAME` | `AUTH_SSL_NAME` | `nl2sql-auth` |
| `GUI_AUTH_CACERT` | `AUTH_CACERT` | `/etc/nl2sql/tls/ca.crt` |
| `GUI_TLS_ENABLED` | `CURATE_GUI_TLS_ENABLED` | `true`: the page is HTTPS, with its own certificate |
| `GUI_TLS_CERT_FILE` | `CURATE_GUI_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` |
| `GUI_TLS_KEY_FILE` | `CURATE_GUI_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` |

The `GUI_` ones are shared by every interface, so one line in `.env` sets
them all. [`auth/README.md`](../auth/README.md) has how sign-in works.

`CURATE_GUI_IMAGE_NAME` and `CURATE_GUI_IMAGE_TAG` choose the image;
`setup.sh --curate` pins them. The service's own settings -- where the
documents are, the snippet store, whether a save reloads it -- are in
[`review/README.md`](../review/README.md#configuration).

## The project

```
curate/
├── src/
│   ├── App.tsx                  the three tabs, and the status bar
│   ├── api/client.ts            the curation routes, and an error class
│   ├── api/types.ts             the wire contract, mirrored by hand
│   ├── api/snippet.ts           the snippet form, and what each kind asks for
│   ├── api/text.ts              counts and cells, worded
│   └── components/
│       ├── SnippetsPane.tsx     the snippet list beside one snippet
│       ├── SnippetEditor.tsx    one snippet: its meaning, then its SQL
│       ├── SnippetCheck.tsx     what running it inside its probe showed
│       ├── GoldenPane.tsx       the golden pairs
│       ├── FixesPane.tsx        the corrections and completions
│       ├── RunResult.tsx        a query's validation, and whether it is stale
│       ├── RemoveButton.tsx     removal in two steps, in the page
│       ├── Steps.tsx            the loaders a write ran
│       ├── Rows.tsx, Problem.tsx
│       └── StatusBar.tsx
├── nginx.conf.template          the page, and the proxy to the review service
├── 10-nl2sql-curate-config.envsh   the token header, and the TLS block
└── Dockerfile                   node builds it, nginx serves it
```

A separate npm project and image from the review interface, though both
front the same service: a curator writing snippets needs neither the queue
nor the page people vote in, and an image is the unit `setup.sh` pins and
compose starts. React and TypeScript, built by Vite, like the other three.

```bash
cd curate
npm ci
npm run dev      # http://localhost:5176, proxying to https://localhost:8444
                 # (NL2SQL_REVIEW_URL and REVIEW_TOKEN to change them)
npm test         # vitest, with coverage
```

## Tests

| File | Covers |
|---|---|
| [`test/`](test) | The page itself, in vitest against a scripted client -- curation GUI: 108 tests, at 100% of statements, branches, functions and lines, with only `main.tsx` excluded |
| [`test_curate_gui_contract.py`](../tests/curate/test_curate_gui_contract.py) | The TypeScript types, field by field, against the review service's models |
| [`test_curate_project.py`](../tests/curate/test_curate_project.py) | The npm project's pins and coverage gate, nginx's template, and the start-up script's branches |
| [`test_curate_compose.py`](../tests/curate/test_curate_compose.py) | The snippet store and this page as compose resolves them: the store's own volume and port, the agent reading it as the loader's role and only the review service holding the owner, the page in its own profile waiting for and verifying the review service, the token held by the proxy, and every setting the proxy reads both ways |
| [`test_curate_gui_suite.py`](../tests/curate/test_curate_gui_suite.py) | Runs the 108-test curation GUI suite from pytest, with `--run-node`, and holds the counts these documents quote to it |
| [`tests/review/test_curation.py`](../tests/review/test_curation.py), [`test_snippet_validation.py`](../tests/review/test_snippet_validation.py), [`test_snippets.py`](../tests/review/test_snippets.py) | The service behind it: every curation route, each snippet kind validated against the live retail database, and the snippet document written and round-tripped through the loader's parser |
