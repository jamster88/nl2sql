# The REST API

The agent answers questions over HTTPS as well as from a terminal, so a
graphical front end can be written in anything. There is no client library
here and there is not meant to be one: the interface is JSON over HTTP with
an OpenAPI document the server generates itself, which a TypeScript, Python,
Java or Go client is generated from rather than written against by hand.

    ./launch.sh --api
    curl --cacert ./nl2sql-api.crt https://localhost:8443/v1/meta

The same image serves both. `docker compose run --rm agent "..."` is the CLI;
the `api` service is `python -m nl2sql_agent.api` in the same container, so
the pipeline answering a GUI is the pipeline that was benchmarked.

---

## Contents

- [The shape of it](#the-shape-of-it)
- [Starting the server](#starting-the-server)
- [TLS](#tls)
- [Authentication](#authentication)
- [Endpoints](#endpoints)
- [The answer](#the-answer)
- [Progress events](#progress-events)
- [Feedback](#feedback)
- [Errors](#errors)
- [Writing a client](#writing-a-client)
- [Configuration](#configuration)
- [Testing it from outside](#testing-it-from-outside)

---

## The shape of it

A question takes about a minute. That single fact decides the design: no GUI
can hold a request open for a minute and show nothing, and no mobile network
will let it try. So **a question is a resource, not a request**.

    POST /v1/questions        ->  202, a job
    GET  /v1/questions/{job_id}          poll it
    GET  /v1/questions/{job_id}/events   or watch it happen
    GET  /v1/questions/{job_id}?wait=180 or let the server hold the connection

All four return the same document. A client that only wants the simple thing
can `POST /v1/questions?wait=180` and treat it as a blocking call; if the
wait runs out it gets the unfinished job back with `202` and keeps polling,
so waiting is an optimisation rather than a second contract to implement.

The event stream is what makes a GUI feel like anything. It carries the
pipeline's own node names as they happen -- screening the question, reading
the schema, matching literals, writing SQL, checking the plan, running it,
narrating, auditing -- so the progress a user sees is the work being done
and not an animation.

---

## Starting the server

With compose, which is how nearly everyone will:

    ./launch.sh --api                        # starts it and checks it
    docker compose --profile api up -d api   # or directly

Or as a plain process, with the agent's Python environment:

    python -m nl2sql_agent.api --help
    python -m nl2sql_agent.api --port 8443 --token "$(openssl rand -hex 16)"

Every flag overrides an environment variable and `--help` names which, so
`--help` doubles as the configuration reference from inside a container.

Two flags have no environment equivalent:

| Flag | What it does |
| --- | --- |
| `--print-openapi` | Writes the OpenAPI document to stdout and exits. This is how a GUI's build step generates its client without starting anything. |
| `--hostname NAME` | Repeatable; names the generated certificate should cover. |

---

## TLS

On by default. The container has no certificate to be given, so on first
start it **writes itself one** -- self-signed, valid for a year, covering
`localhost`, `127.0.0.1`, `::1`, `api` and `nl2sql-api` (the compose service
name other containers reach it by). It lands in the `apitls` volume, so a
restart presents the same certificate and a client that pinned it keeps
working.

A self-signed certificate is refused by every client that checks, which is
the point of having one. Three ways to work with it, best first:

    # 1. Trust it explicitly -- still proves you reached the right server
    docker compose --profile api cp api:/etc/nl2sql/tls/server.crt ./nl2sql-api.crt
    curl --cacert ./nl2sql-api.crt https://localhost:8443/v1/meta

    # 2. Pin its fingerprint, which the server prints at startup
    docker compose --profile api logs api | grep fingerprint

    # 3. Turn verification off, for a throwaway experiment only
    curl --insecure https://localhost:8443/healthz

### When a generated certificate is replaced

A certificate already on disk is normally kept exactly as it is. That is what
makes a generated one survive a restart, so a client that trusted it once --
or pinned its fingerprint -- keeps working.

There is one exception. If the certificate is self-signed and no longer covers
every name in `API_TLS_HOSTNAMES`, a new one is generated to cover them, and
the server says so at start-up.

This exists because of what happens otherwise. When a new service joins the
stack, `API_TLS_HOSTNAMES` grows to cover it -- the review service presents
this certificate rather than generating a second one, so `nl2sql-review` had
to be added to the list. On an existing deployment the volume still held the
certificate from before. It was silently missing the new name, and the only
symptom was the new service's proxy refusing to verify it, with a message
about a hostname mismatch and nothing about which name, or why, or that a
certificate written months ago was the reason.

Two consequences worth knowing:

* **The fingerprint changes.** Anything that pinned the old one, or copied it
  out with `docker compose cp`, needs it again. The start-up warning says so.
* **A proxy in front of the API has to be restarted.** nginx reads the
  certificate once, while it parses its config, so an already-running proxy
  goes on trusting a certificate nobody presents any more -- and its own
  health check will not notice, because that asks for a page served from
  disk. `launch.sh` checks by asking for `/readyz` *through* the proxy and
  restarts it when that fails.

A CA-issued certificate is never replaced. It cannot be reissued here, and
replacing it is a decision for whoever obtained it, so the mismatch is
reported at start-up and the certificate is left alone.

### Taking the development certificate away

Once a real certificate is mounted, set `API_TLS_ALLOW_SELF_SIGNED=false`.
The server then **refuses to start** behind a self-signed certificate: it
will not generate one, and it will not load one it finds. It exits `2` and
names the file.

    API_TLS_ALLOW_SELF_SIGNED=false docker compose --profile api up api
    # error: /etc/nl2sql/tls/server.crt is self-signed (issuer and subject are
    #        both O=nl2sql agent (development),CN=localhost) and
    #        API_TLS_ALLOW_SELF_SIGNED=false. Replace it with a CA-issued
    #        certificate, or set API_TLS_ALLOW_SELF_SIGNED=true ...

That is the difference between a deployment that is insecure and one that is
insecure without anyone noticing. Mount the real pair over the defaults:

    volumes:
      - ./certs/fullchain.pem:/etc/nl2sql/tls/server.crt:ro
      - ./certs/privkey.pem:/etc/nl2sql/tls/server.key:ro

`API_TLS_ENABLED=false` serves plain HTTP instead, which is right in exactly
one situation: something in front of the process already terminates TLS. The
server says so at startup, and `/readyz` repeats it.

---

## Authentication

Unset by default, because the usual deployment is a private network and a
token that has to be invented before anything works is a token that ends up
committed. Set `API_TOKEN` and every `/v1` route requires it:

    Authorization: Bearer <token>       # preferred
    X-API-Key: <token>                  # for clients that find that easier
    ?access_token=<token>               # event streams only, see below

`/`, `/healthz`, `/readyz` and `/openapi.json` stay open so an orchestrator's
probes and a client's code generation keep working.

The query-string form exists because a browser's `EventSource` cannot set
headers, and a GUI that cannot stream progress is back to a spinner. Use a
header everywhere else -- query strings end up in access logs.

### Browsers

Set `API_CORS_ORIGINS` to the GUI's origin:

    API_CORS_ORIGINS=https://gui.example.com,http://localhost:5173

The default is `*`, which works for a token-less private deployment. Note
that browsers refuse to send credentials to a wildcard origin, so `*`
together with `API_TOKEN` is a combination that looks configured and fails
only in a browser; the server warns about it at startup.

---

## Endpoints

| Method | Path | Auth | What |
| --- | --- | --- | --- |
| `GET` | `/` | no | Service banner and where everything is |
| `GET` | `/healthz` | no | The process is alive. Touches nothing else |
| `GET` | `/readyz` | no | It can answer a question *now*. `503` when it cannot, with the reason per dependency |
| `GET` | `/openapi.json` | no | The schema. Generate your client from this |
| `GET` | `/docs` | no | The same thing, browsable (`API_DOCS_ENABLED=false` to remove) |
| `GET` | `/redoc` | no | The same schema again, as reference documentation (same switch) |
| `GET` | `/v1/meta` | yes | Version, model, tables in scope, limits, which pipeline stages are on, the certificate |
| `POST` | `/v1/questions` | yes | Ask. `?wait=<seconds>` to block |
| `GET` | `/v1/questions` | yes | Recent questions, newest first |
| `GET` | `/v1/questions/{job_id}` | yes | One question. `?wait=<seconds>` to block |
| `GET` | `/v1/questions/{job_id}/events` | yes | Progress, as Server-Sent Events |
| `DELETE` | `/v1/questions/{job_id}` | yes | Cancel a queued question, forget a finished one |
| `POST` | `/v1/questions/{job_id}/feedback` | yes | Say whether the answer was right |
| `DELETE` | `/v1/questions/{job_id}/feedback` | yes | Withdraw a verdict |

`/healthz` and `/readyz` are separate because the failures want different
responses: a wedged process should be restarted, a database that has not
finished starting should just be waited for.

`/readyz` is meant to be polled, so it answers quickly even when a dependency
is missing: the agent's chat host is probed with a bounded timeout
(`OLLAMA_CONNECT_TIMEOUT`, 5 seconds) rather than left to the operating
system, which takes about three minutes to give up on a host that is routed
and silent.

`/v1/meta` is worth fetching on start-up. It tells a client what it may ask
about (`tables`, `scope`), what it must respect (`limits.max_rows`), and what
to render (`pipeline.narrate` false means there is no paragraph to show,
`pipeline.audit` false means no verification badge).

Two of those limits describe the request rather than the answer:

| Field | Default | What |
| --- | --- | --- |
| `limits.max_question_length` | `2000` | Characters. A longer question is `422`, not a truncated one |
| `limits.max_metadata_entries` | `20` | Pairs. Keys are capped at 64 characters and values at 256 |

They are published because of who needs them. A browser that sends an
over-long question sees the `422` in its network tab; a desktop client shows
the user whatever it was handed, and "422 Unprocessable Entity" is not an
explanation of a text box forty characters too long. `desktop/` reads both on
start-up and refuses the question in the box, which is the only place the
user can still do something about it.

---

## The answer

A finished job carries an `answer` object. Everything in it is total: every
list is a list, so nothing needs a null check before it is rendered.

```jsonc
{
  "id": "3f2c...", "status": "succeeded",
  "question": "What was total net sales for Produce in FY2025?",
  "created_at": "...", "finished_at": "...", "duration_ms": 61432.5,
  "progress": [ /* every step, see below */ ],
  "answer": {
    "answer":    "Produce net sales were $719,279.97 in FY2025.",
    "narrative": "Produce net sales were $719,279.97 in FY2025.",
    "sql":       "SELECT sum(...) FROM fact_pos_retail_sales ...",
    "verdict":   "proceed",          // or refused / out_of_domain / ambiguous
    "intent":    "aggregate",
    "clarification": null,           // set when verdict is "ambiguous"
    "tables":    ["dim_product", "fact_pos_retail_sales"],
    "literals":  [{"phrase": "produse", "table": "dim_product",
                   "column": "department", "value": "Produce", "score": 0.81}],
    "result":    {"columns": ["net_sales"], "rows": [["719279.97"]],
                  "row_count": 1, "truncated": false},
    "chart":     {"kind": "bar", "x": null, "y": ["net_sales"], "series": null},
    "claims":    [{"text": "...", "value": 719279.97,
                   "cells": [[0, "net_sales"]], "formula": null}],
    "audit":     {"passed": true, "unsupported_claims": [], "drop_reasons": [],
                  "redactions": [], "semantic_issue": null},
    "plan_cost": 125767.4, "attempts": 1,
    "trace":     [{"node": "generate_sql", "ms": 8123.4, "model_calls": 1,
                   "detail": "..."}],
    "retrieval_errors": {}
  },
  "error": null,
  "links": {"self": "/v1/questions/3f2c...", "events": "/v1/questions/3f2c.../events"}
}
```

Notes a client author will want:

* **Cells are strings when JSON cannot hold them.** A `numeric` or a `date`
  out of Postgres arrives as its string form rather than losing precision or
  failing to encode. Parse against `columns` if you need types.
* **`result` is `null` for a refusal**, not an empty table -- no query ran.
  An empty table means the query ran and matched nothing.
* **A refusal is still `succeeded`.** The agent worked correctly and said no;
  `verdict` says which kind. `status: "failed"` means the run itself failed.
* **`claims` and `audit` are the verification story.** Each claim points at
  the cells it was read from, and the audit drops any the rows do not
  support. A GUI can underline a sentence and highlight its cells from this.
* **`chart` is a suggestion, not a rendering.** Its fields name columns of
  `result`; the GUI owns the chart library.
* **`trace` is per-node cost.** Useful for a debug panel, and it is what the
  benchmark measures from.

---

## Progress events

`GET /v1/questions/{job_id}/events` is a `text/event-stream`:

```
id: 4
event: progress
data: {"seq":4,"step":"generate_sql","label":"sql","detail":"3 tables, 2 examples","at":"..."}

event: status
data: {"status":"running"}

: keep-alive

event: done
data: { ...the whole finished job... }
```

* `step` is the graph node -- key your UI off it. `label` is the short human
  word for it. The full list is in `/v1/meta` under `pipeline.nodes`.
* `id:` is the sequence number. Reconnect with `Last-Event-ID: 4` (browsers
  send it automatically) or `?from_seq=4` and the server resumes rather than
  replaying.
* A comment line every `API_KEEPALIVE_SECONDS` keeps proxies from dropping an
  idle connection while the model thinks.
* The stream is closed after `API_EVENT_STREAM_TIMEOUT_SECONDS` idle, with an
  `event: timeout` saying to reconnect.
* Connecting after the job finished is fine: the backlog is delivered, then
  `done`.

---

## Feedback

A verdict on an answer, recorded in a staging database where it waits to be
reviewed and possibly promoted into the golden question set the agent is
measured against.

```http
POST /v1/questions/{job_id}/feedback
Content-Type: application/json

{"verdict": "no", "comment": "the fiscal month is off by one"}
```

```json
{
  "id": "1d9b162b-c3b8-4940-829f-cf4629542404",
  "job_id": "a0be5c6bff6442bb9a4ff300eaa67fdc",
  "verdict": "no",
  "comment": "the fiscal month is off by one",
  "state": "pending"
}
```

`verdict` is the whole of the required input, one of three:

| `verdict` | The GUIs say | Meaning |
|---|---|---|
| `"yes"` | Correct | The answer was right |
| `"no"` | Wrong | The answer was wrong |
| `"incomplete"` | Correct but incomplete | The SQL was right, and the answer still lacked something a reader needed -- a name beside an id, the figure a ranking was ranked by |

The wire values `"yes"` and `"no"` predate the third and are kept, so
every verdict already recorded still reads the same. `comment` is optional
free text for whoever reviews it.

**The question, the SQL and the result shape are not sent.** They are taken
from the job, which the server still has -- a vote happens while the answer
is on screen. A client that supplied its own snapshot could supply one that
never matched the job, and the staging table would then hold evidence of an
answer this server never gave.

They are taken *now* rather than looked up later because there is no later:
a job is forgotten after `API_JOB_TTL_SECONDS`, and a verdict holding only a
job id would be pointing at nothing within the hour. The SQL in particular is
the entire reason a "yes" is worth keeping -- it is the candidate a golden
question/SQL pair gets built from.

Four rules are worth knowing:

* **The job has to be finished.** `409 job_running` otherwise. There is
  nothing to have an opinion about yet, and a snapshot of a half-finished run
  is the one thing a golden pair must never be built from. A *failed* job can
  be judged, and a "no" on one is among the most useful feedback there is.
* **Voting again replaces the verdict**, until somebody has reviewed it.
  After that the vote stands and the second one is refused with `409
  already_reviewed` -- the earlier opinion was recorded and is still there.
* **`DELETE` withdraws it**, on the same terms and for the same reason: a
  verdict that cannot be taken back is a verdict people stop giving. It needs
  no job to still exist, because the verdict outlives the job.
* **`503 feedback_unavailable` when the server has no staging database.** The
  routes still exist and still appear in the OpenAPI document, so a generated
  client does not change shape depending on the server it met. `/v1/meta`
  carries `feedback: true|false` so a GUI can decide whether to draw the
  buttons at all.

### What the server can do with it

This process writes one row and can do nothing else with it. It connects as
`nl2sql_feedback_writer`, a role that may insert a submission, replace one
that is still pending, delete one that is still pending, and read back three
of its columns. Rows a curator has accepted, rejected or promoted are
invisible to it -- by a row-level security policy, so the guarantee does not
rest on the SQL in this package being careful.

Everything else -- reading the queue, editing a draft pair, writing the
golden question document -- belongs to a separate service on a separate port
with a separate token. See [`review/README.md`](../review/README.md).


## Errors

One shape, whatever failed:

```json
{"error": {"code": "job_running", "message": "this question is already running and cannot be interrupted; it can be deleted once it finishes"}}
```

Branch on `code`; the message is for a person.

| Code | Status | Meaning |
| --- | --- | --- |
| `invalid_request` | 422 | The body or query string is wrong. `detail.errors` says where |
| `unauthorized` | 401 | Missing or wrong token |
| `not_found` | 404 | No such job. Finished jobs are kept `API_JOB_TTL_SECONDS` |
| `job_running` | 409 | A question in flight cannot be interrupted |
| `principal_not_allowed` | 400 | `principal` was sent to a server started without `API_ALLOW_PRINCIPAL` |
| `already_reviewed` | 409 | A verdict has been acted on and no longer belongs to the voter |
| `feedback_unavailable` | 503 | No staging database is configured (`API_FEEDBACK_DB_URL`) |
| `unavailable` | 503 | The server is shutting down |

A question the pipeline could not answer is **not** an HTTP error: the job
comes back with `status: "failed"` and `error` set, so the client can still
show the SQL it tried and the attempts it made.

---

## Writing a client

Nothing below imports anything from this repository.

There are two complete ones, in two languages, and they are worth reading
before writing a third: the snippets below are the shape, and those are the
shape at full size.

[`gui/`](../gui) is React and TypeScript -- every endpoint here, the event
stream with its resume and its fallback, and the whole answer rendered
including the charts. [`desktop/`](../desktop) is Java and JavaFX, and is the
one that proves the claim this page makes: a contract only one implementation
has ever met is a contract nobody has checked. Writing it found that the two
limits describing the *request* were not published, because a browser
discovers those from a 422 in its network tab and a desktop application shows
the user whatever it was handed. They are in `limits` now.

The differences between the two are worth more than the similarities. The
browser is served by the nginx that proxies this API, so it talks to its own
origin and the proxy holds both the token and the trust decision; the desktop
client opens the connection itself and has to be told which certificate to
believe. Anything written against this page will be one or the other.

### TypeScript / React

```ts
const base = "https://nl2sql-api.example.com";
const headers = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };

// Ask, then watch. The POST returns before any work is done.
const job = await fetch(`${base}/v1/questions`, {
  method: "POST", headers, body: JSON.stringify({ question }),
}).then(r => r.json());

const events = new EventSource(`${base}${job.links.events}?access_token=${token}`);
events.addEventListener("progress", e => setStep(JSON.parse(e.data).label));
events.addEventListener("done", e => { setJob(JSON.parse(e.data)); events.close(); });
```

Generating the client instead of writing it:

    python -m nl2sql_agent.api --print-openapi > openapi.json
    npx openapi-typescript openapi.json -o src/api.d.ts

### Python (a Django view, a service, a notebook)

```python
import httpx

client = httpx.Client(
    base_url="https://nl2sql-api.example.com",
    headers={"Authorization": f"Bearer {token}"},
    verify="nl2sql-api.crt",     # or True once a real certificate is mounted
    timeout=300,
)
job = client.post("/v1/questions", json={"question": question},
                  params={"wait": 240}).json()
rows = job["answer"]["result"]["rows"]
```

Streaming progress, for a Django channel or a websocket relay:

```python
with client.stream("GET", job["links"]["events"]) as stream:
    for line in stream.iter_lines():
        if line.startswith("data: "):
            handle(json.loads(line[6:]))
```

### Java

```java
var http = HttpClient.newBuilder().sslContext(contextTrusting("nl2sql-api.crt")).build();
var post = HttpRequest.newBuilder(URI.create(base + "/v1/questions?wait=240"))
    .header("Authorization", "Bearer " + token)
    .header("Content-Type", "application/json")
    .POST(BodyPublishers.ofString("{\"question\":" + quoted(question) + "}"))
    .build();
var job = mapper.readTree(http.send(post, BodyHandlers.ofString()).body());
```

### An intermediary service

If the GUI is a browser application, putting a small service of your own in
front is usually right: it holds `API_TOKEN`, so the browser never sees it,
and it can relay the event stream to whatever the front end already speaks.
Nothing here assumes it -- CORS and the query-string token exist so a browser
can call the API directly when that is simpler -- but the stream is plain SSE
and relays without translation.

[`gui/nginx.conf.template`](../gui/nginx.conf.template) is that service, at
its smallest: thirty lines of nginx that verify this server's certificate,
add the token, and pass the event stream through unbuffered. The three
settings that keep it unbuffered are the ones worth copying --
`proxy_buffering off`, `proxy_cache off` and `gzip off` -- because without
any one of them the stream is held until the answer is finished, and it
fails as a spinner that never moves rather than as an error.

### curl

Every request this API serves is demonstrated in
[`docker/apitest/smoke.sh`](../docker/apitest/smoke.sh), which is curl and
nothing else. It is the shortest complete reference; `gui/` is the longest.

---

## Configuration

Every setting is an environment variable, forwarded by compose so it can be
set without a rebuild. An unset variable keeps the default -- compose passes
an unset variable through as an empty string, and empty is read as absent.

### The socket

| Variable | Default | What |
| --- | --- | --- |
| `API_HOST` | `0.0.0.0` | Interface to bind |
| `API_PORT` | `8443` | Port to bind and publish |
| `API_ROOT_PATH` | *(none)* | Path prefix when a reverse proxy serves it under one |

### TLS

| Variable | Default | What |
| --- | --- | --- |
| `API_TLS_ENABLED` | `true` | Serve HTTPS. Off only behind a TLS terminator |
| `API_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` | PEM certificate |
| `API_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` | PEM private key |
| `API_TLS_GENERATE` | `true` | Write a development certificate when none is present |
| `API_TLS_ALLOW_SELF_SIGNED` | `true` | **Set false to refuse to start without a CA-issued certificate** |
| `API_TLS_HOSTNAMES` | `localhost,nl2sql-api,api,127.0.0.1,::1` | Names the generated certificate covers |
| `API_TLS_DAYS` | `365` | Lifetime of the generated certificate |

### Who may call

| Variable | Default | What |
| --- | --- | --- |
| `API_TOKEN` | *(none)* | Require this bearer token on `/v1` |
| `API_CORS_ORIGINS` | `*` | Browser origins allowed to call it |
| `API_ALLOW_PRINCIPAL` | `false` | Let callers choose the database role rows are read as (`SET LOCAL ROLE`, for row-level security). Only with something authenticating them in front |

### Feedback

| Variable | Default | What |
| --- | --- | --- |
| `API_FEEDBACK_DB_URL` | *(none)* | The staging database a verdict is written to, as `nl2sql_feedback_writer`. Unset means the feedback routes answer `503` and everything else works |

This is the only write credential this process holds, and the role it names
can see nothing a curator has already judged. The review service creates that
role and resets its grants on every start.

### Work

| Variable | Default | What |
| --- | --- | --- |
| `API_MAX_CONCURRENCY` | `2` | Questions answered at once. They queue on one Ollama host anyway |
| `API_JOB_TTL_SECONDS` | `3600` | How long a finished job can still be collected |
| `API_MAX_JOBS` | `200` | How many jobs are remembered |
| `API_MAX_WAIT_SECONDS` | `900` | Ceiling on `?wait=` |
| `API_EVENT_STREAM_TIMEOUT_SECONDS` | `300` | How long an idle stream is held open |
| `API_KEEPALIVE_SECONDS` | `15` | Comment line interval on a stream |
| `API_DOCS_ENABLED` | `true` | Serve `/docs` and `/redoc` |
| `API_LOG_LEVEL` | `info` | uvicorn log level |

The pipeline's own settings -- the model, the retry budget, which stages run
-- are the agent's and are documented in [README.md](README.md#configuration).
The `api` service is given exactly the same set as the `agent` service, so a
question asked over HTTP is answered the same way as one asked in a terminal.

---

## Testing it from outside

    docker compose --profile api run --rm apitest
    docker compose --profile api run --rm apitest "total net sales for produce in FY2025"

`apitest` is an Alpine image with curl and jq in it and nothing else -- no
Python, no shared code, no shared dependencies. That is deliberate: if the
smoke test needed the agent package installed, it would not be testing an
API. It verifies the certificate rather than skipping verification, walks
every endpoint, streams the progress of a real question, and exits `1` on a
failed check or `2` when the API was never reachable at all, so a CI job can
tell a retry apart from a defect.

It is configured entirely from the environment, which is what compose sets:

| Variable | Default | What |
| --- | --- | --- |
| `API_BASE_URL` | `https://nl2sql-api:8443` | The API to drive |
| `API_TOKEN` | *(none)* | Presented as a bearer token when set |
| `API_CACERT` | *(none)* | A CA file to verify against; tried first |
| `API_INSECURE` | `true` in compose | Allow `--insecure` as a last resort. With this false and nothing to verify against, it refuses to run |
| `APITEST_QUESTION` | `How many stores are there?` | The question to ask, unless one is given as an argument |
| `APITEST_WAIT_SECONDS` | `240` | How long to wait for the answer |

For the automated suite:

    pytest tests/api                       # the whole HTTP surface, offline
    pytest tests/docker -m docker --run-docker   # the real container, real TLS

`tests/api/test_live_tls.py` binds a real socket with the real certificate
and talks to it with the standard library, so the encrypted path is covered
on an ordinary `pytest` without Docker.
