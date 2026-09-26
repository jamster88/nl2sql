# The web interface

A React and TypeScript front end for the agent's REST API. Ask a question,
watch the pipeline work through it, read the answer with its chart and its
rows, and say whether it was right.

    ./start.sh

Nothing here imports a line of the agent. It speaks the same JSON over HTTPS
that any other client would ([`agent/API.md`](../agent/API.md) is the
contract), which is the point: it is a demonstration that the API is
framework-agnostic, not a privileged special case.

---

## Contents

- [What it does](#what-it-does)
- [Running it](#running-it)
- [Why there is a proxy](#why-there-is-a-proxy)
- [How it is put together](#how-it-is-put-together)
- [The charts](#the-charts)
- [The feedback](#the-feedback)
- [Configuration](#configuration)
- [Tests](#tests)

---

## What it does

**Asks a question.** A textarea, because the questions that get good answers
are sentences. Enter sends, shift-Enter adds a line. Four examples from the
benchmark suite are offered on an empty page, chosen to land on four
different chart shapes.

**Shows the pipeline working.** A question takes about a minute. That minute
is filled with the agent's own graph nodes as they finish -- screening the
question, matching literals, reading the schema, writing SQL, checking the
plan, running it, narrating, auditing -- streamed over Server-Sent Events.
The steps still to come are listed greyed, because they are known: `/v1/meta`
says which nodes this server runs.

**Shows the answer, and its working.** The sentence first, then the chart,
then the rows. Folded away beneath: the SQL and how many attempts it took,
the phrases matched to database values, and how long each node took. Hovering
a claim lights up the exact cells it was read from -- the audit ties each
sentence to the rows that support it, and that is the clearest thing this API
makes possible.

**Handles the three answers that are not answers.** A refusal is still a
`succeeded` job with no table, because no query ran -- rendering an empty
grid there would report a failure that did not happen. An ambiguous question
comes back with a clarification, which is a question for the user, so it goes
where the answer would. A failed run still carries the SQL it tried.

**Takes a verdict.** Correct, wrong, or correct but incomplete, per answer.
See [the feedback](#the-feedback).

---

## Running it

### With compose, which is how nearly everyone will

    ./start.sh                                          # and opens the page
    ./launch.sh --gui                                   # without the browser
    docker compose --profile api --profile gui up -d    # or directly

Both profiles, because the `gui` service depends on the `api` service and
compose will not start what no active profile names.
[`start.sh`](../start.sh) is the one-command version of all of it: it runs
`setup.sh` on a first run and `launch.sh` after, waits until this page
actually answers -- which is later than the container calling itself healthy
-- and opens it. `--no-browser` skips the last step; `BROWSER` chooses what
does it.

The image is published, and `setup.sh --gui` pulls and pins it:

    docker pull mcfaddja/nl2sql-gui:v4_5
    ./setup.sh --gui        # pulls it and writes GUI_IMAGE_* into .env

Without that pin the first `./launch.sh --gui` builds the image here instead,
which works and takes a couple of minutes -- compose builds a service whose
image is missing. Publishing a new one:

    docker buildx build --platform linux/amd64,linux/arm64 \
      -f gui/Dockerfile --push -t mcfaddja/nl2sql-gui:v4_5 .

Multi-arch in one step, so the tag covers both architectures the way every
other tag in this project does. The version in the image label comes from
`package.json`, and tests pin it to the agent's `__version__` and pin the two
published tags to each other: the GUI and the API are built from one checkout
and only ever tested together.

### Against a running API, for development

    cd gui
    npm install
    npm run dev            # http://localhost:5173

`npm run dev` proxies the API the same way the container does (see below), so
it needs the API running -- `./launch.sh --api` -- and nothing else. Point it
somewhere other than the compose default with `NL2SQL_API_URL`, and give it a
token with `API_TOKEN`; neither reaches the browser.

    npm test               # the suite, with coverage
    npm run build          # typecheck, then build to dist/
    npm run typecheck

---

## Why there is a proxy

Three things stand between a browser and this API, and none of them is about
this application:

1. **The development certificate is self-signed.** A browser blocks every
   request behind a warning -- and `EventSource` gives no warning to click,
   it simply never connects, so progress silently stops working.
2. **The token would have to reach the browser** to be sent from it, which
   puts it in a bookmark, a screenshot and a support ticket.
3. **Two origins means CORS**, pre-flight requests, and a wildcard origin
   that browsers refuse to send credentials to.

One same-origin hop removes all three. In the container that hop is nginx
([`nginx.conf.template`](nginx.conf.template)); in development it is Vite's
dev server ([`vite.config.ts`](vite.config.ts)), which is the same three
rules in another syntax. The browser talks plain HTTP to something on
localhost; that something talks TLS to the API with a certificate it
*verifies* and a token it holds.

This is the "intermediary service" the API documentation recommends, and it
is worth knowing that it is a convenience rather than a requirement: the API
answers a browser directly when `API_CORS_ORIGINS` names its origin. The
proxy is here because it makes the first run work without anyone reading
anything.

Two details in that config are load-bearing and easy to undo:

* **`proxy_buffering off`, `proxy_cache off`, `gzip off`** on the API
  location. Any one of them missing holds the whole progress stream until the
  answer is finished -- the one thing the stream exists to avoid -- and it
  fails as a spinner that never moves rather than as an error.
* **The upstream is resolved per request**, via a variable and a `resolver`.
  A literal upstream is resolved once while nginx parses its config, which
  stops the GUI starting before the API is up and leaves it talking to a
  stale address after the API is restarted onto a new one.

---

## How it is put together

```
src/
  api/          types.ts     the wire contract, in TypeScript
                client.ts    four calls and an error class
                events.ts    the SSE watcher, and what it falls back to
                useAsk.ts    asking a question, as a piece of state
  charts/       palette.ts   the validated categorical colours
                values.ts    rows -> series, and every coercion that can fail
                scale.ts     linear and band scales, round ticks
                Frame.tsx    grid, axes, legend
                Plot.tsx     the marks themselves
                Chart.tsx    dispatch on the kind the pipeline chose
  components/   the interface
  feedback/     store.ts     one interface, one implementation behind it
```

Three decisions are worth knowing about before changing anything.

**`types.ts` is written by hand, and a test keeps it honest.** Generating it
is one command (`--print-openapi` piped into `openapi-typescript`), but a
generated `api.d.ts` is not something anyone reads, and this file is what a
client author opens first. The cost of that choice is drift, so
[`tests/gui/test_gui_contract.py`](../tests/gui/test_gui_contract.py)
introspects the pydantic models and parses the TypeScript, and fails when
either side gains or loses a field the other does not have. It has already
caught one.

**The stream is watched, not trusted.** `EventSource` reconnects silently
forever, so a proxy that buffers `text/event-stream` produces a spinner that
never moves and no error anywhere. `events.ts` counts failures, and after two
it abandons the stream, says so, and falls back to polling -- slower, duller,
and always works.

**A superseded question is refused before it opens a stream.** Two questions
in flight at once can come back in either order, and the second one's answer
must win. `useAsk` counts attempts and drops a POST that resolved after a
newer one was sent, which is the only place it can be caught: the older
question's watch does not exist yet when the newer one closes what is open.

---

## The charts

The agent's Visual Formatter already chose the form from the shape of the
result -- one number is a headline, a category against a measure is a bar, a
date against a measure is a line, two measures are a scatter -- so this does
not choose again. It renders what was asked for:

| `chart.kind` | Drawn as |
|---|---|
| `scalar` | The number, large. Not a bar of one |
| `bar` | Bars from a zero baseline, value labels up to twelve of them |
| `grouped_bar` | One bar per measure per band, with a 2px gap between them |
| `line` | A 2px line with a marker on every point, series labelled at the end |
| `scatter` | Points on two numeric axes |
| `table` | Nothing. The rows are the presentation |

They are hand-written SVG rather than a charting library, because the rules
they follow -- thin marks, rounded data ends anchored to the baseline, a
surface gap between adjacent fills, a surface ring on overlapping marks,
labels on some points and not all -- are mostly *undone* by a library's
defaults, and bending those back is more code than a linear scale.

The colours are not chosen here. They come from a palette whose ordering was
validated rather than eyeballed, on five checks: a lightness band, a chroma
floor, colour-vision-deficient separation between adjacent slots, a
normal-vision separation floor, and contrast against the surface. Two
consequences are load-bearing:

* **Slots are assigned in order and never cycled.** A ninth series does not
  get a generated colour; it folds into "Other", because a colour picked to
  fill a gap is a colour nobody checked. Folding rather than truncating: a
  chart that silently omits the ninth region is wrong about the total.
* **Scatter caps at three.** Adjacent-pair validation is enough when marks
  sit next to each other in a known order. A scatter puts every pair on
  screen at once, and under all-pairs scoring only the first three slots
  clear the floors in both light and dark.

Three light-mode slots sit below 3:1 contrast against the surface, which
obliges relief: the rows are always shown as a table beneath the chart, and
series of four or fewer are labelled directly as well as in the legend.

Every mark is focusable and carries its value in an `aria-label`, so the
chart is readable from the keyboard and by a screen reader, and a test
asserts nothing is ever drawn outside the plot area.

---

## The feedback

Three buttons under every answer -- **Correct**, **Wrong** and **Correct but
incomplete** -- and, once one is pressed, a box to say why. The third is for
an answer whose SQL was right and which still left out what a reader needed,
such as the product name beside a SKU: that calls for fleshing out rather
than correcting, and a plain "no" could not say which. On the wire the three
are `yes`, `no` and `incomplete`; the first two predate the third and are
kept so every verdict already recorded reads the same. The verdict is shown back in the answer panel and beside the question in
the session list, and it is sent to the API, where it is staged for review
and possibly promoted into the golden question set. See
[`review/README.md`](../review/README.md) for what happens to it after that.

The earlier version kept the verdict in the browser and did nothing else with
it. What it was careful about was *where* it was kept, on the grounds that
the next version would send it somewhere and the difference between an easy
change and an awkward one is whether the components ever learned that
`localStorage` was involved. That prediction held exactly: one interface in
[`feedback/store.ts`](src/feedback/store.ts), one implementation behind it,
and the change was a new `createApiFeedbackStore` and one line in `App.tsx`.

The POST is **not** awaited before the button changes state. A verdict is a
courtesy the user is doing us, and making them watch a spinner for it is how
a feedback system stops collecting feedback. So the browser store is written
first and is what the interface reads, the request goes out behind it, and
the record's `sync` field carries what happened -- which is why that field
exists rather than the failure being swallowed or thrown at a user who has
already moved on.

The browser store underneath is not a cache. A verdict given while the server
was unreachable is still on screen after a reload, still marked unsent, and
still the user's own record of what they thought.

Four decisions that came out of using it:

* **The comment box only appears after a verdict.** Asking for prose up front
  turns a one-click courtesy into a form, and a feedback widget that looks
  like a form is one people scroll past.
* **"Not sent" looks different from "sent".** It is the one case where saying
  nothing actively misleads: the user believes they have reported a problem,
  and nobody has heard it. There is a Retry button beside it.
* **A server with no staging database is not a failure.** `/v1/meta` carries
  `feedback: true|false`; when it is false the store records locally and
  claims nothing, rather than putting a red warning under every answer for
  something nobody did wrong.
* **The snapshot is the server's, not the page's.** The page sends a verdict
  and a comment. The question, the SQL and the result shape are read from the
  job by the server, so a client cannot stage evidence of an answer the agent
  never gave.

Two smaller ones that predate all of it:

* **Clicking the recorded verdict withdraws it.** A misclick on "Wrong" should
  not send someone hunting for an undo, and a verdict that cannot be taken
  back is a verdict people stop giving.
* **The record carries the question, not just the job id.** The server
  forgets a job after `API_JOB_TTL_SECONDS`, so a verdict holding only an id
  becomes an orphan pointing at nothing an hour later.

---

## Configuration

The page itself has none: it asks its own origin for everything, so one build
is deployable anywhere. Everything below configures the proxy in front of it,
and is read at container start-up.

| Variable | Default | What |
| --- | --- | --- |
| `GUI_PORT` | `8080` | Port nginx listens on, and the one compose publishes |
| `API_UPSTREAM` | `https://nl2sql-api:8443` | The API. An `http://` scheme turns certificate verification off, for the deployment behind a TLS terminator |
| `API_SSL_NAME` | `nl2sql-api` | The name the certificate is verified against. Must be one `API_TLS_HOSTNAMES` covers |
| `API_CACERT` | `/etc/nl2sql/tls/server.crt` | What to verify against, from the volume the API writes it into |
| `API_TOKEN` | *(none)* | Sent as a bearer token. Held here so the browser never has it |
| `API_READ_TIMEOUT` | `600s` | Must outlast a question, and `API_MAX_WAIT_SECONDS` |
| `GUI_RESOLVER` | `127.0.0.11` | Docker's embedded DNS, for the per-request lookup |

`npm run dev` reads `NL2SQL_API_URL`, `API_TOKEN`, `NL2SQL_API_TLS_VERIFY`
and `GUI_PORT` instead; the defaults assume the compose stack.

Behind `API_TLS_ENABLED=false`, point the GUI at it with
`GUI_API_UPSTREAM=http://nl2sql-api:8443` -- the scheme is what decides, and
the start-up script then writes no certificate block at all rather than
failing over a file that was never going to exist.

---

## Tests

    cd gui && npm test

312 tests, 100% of statements, branches, functions and lines -- matching the
Python side, and for the same reason: a threshold below 100 is a number
nobody looks at, while a failing build is read immediately. Only `main.tsx`
is excluded, and a test pins that list.

Nothing in them needs a server: the whole interface is exercised against a
fake client and a controllable `EventSource`, which is the only way these
paths run on every commit. What that buys is the awkward cases -- a stream
that drops twice, a job superseded mid-flight, a browser that refuses to
store anything, a `numeric` that arrived as a string, a null in the middle of
a measure column.

From the Python suite:

    pytest --run-node       # runs the above, and checks it is not empty
    pytest --run-docker     # builds the image and drives it against a real API

[`tests/gui/`](../tests/gui) holds the offline checks -- the wire contract
against the pydantic models, and the configuration for the things that rot
quietly: a path the dev server proxies and nginx does not, a dependency that
floated to a new major version, a coverage threshold quietly lowered.
[`tests/docker/test_gui_container.py`](../tests/docker/test_gui_container.py)
runs this image and the agent image on one network and drives the hop between
them, including that a certificate naming the wrong host is refused rather
than trusted.
