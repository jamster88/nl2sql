# SQL console

The retail database, queried the way the agent queries it -- for working out
why an answer was wrong.

```bash
./start.sh --console       # everything, and the console in a window of its own
./launch.sh --console      # the same containers, without the browser
open https://localhost:8082
```

When the agent gets an answer wrong, the useful questions are about the
database it read and the gates in front of it. Would the static validator
have let this query through? What did the planner estimate it would cost,
against the ceiling the agent refuses above? Did it finish inside the
statement timeout? What do the rows actually say, and would the agent have
read all of them? Every one of those has an exact answer, and only running
SQL can give it. This is where that SQL is run: as the agent's own read-only
role, under the agent's own limits, through the agent's own validator and
planner gate -- and beside every result, what the agent would have made of
it.

```
browser ──▶ nl2sql-console-gui   nginx on 127.0.0.1:8082: the page, and a proxy
                  │               that adds CONSOLE_TOKEN and verifies TLS
                  ▼
            nl2sql-console       python -m nl2sql_agent.console, the agent image
                  │               static validator ─▶ planner gate ─▶ executor
                  │               as nl2sql_reader, READ ONLY, statement_timeout
                  ▼
            nl2sql-postgres      the retail database
```

## Contents

- [What it shows](#what-it-shows)
- [The verdict](#the-verdict)
- [Why it runs from the agent's image](#why-it-runs-from-the-agents-image)
- [The fence](#the-fence)
- [Running it](#running-it)
- [Endpoints](#endpoints)
- [Configuration](#configuration)
- [The interface](#the-interface)
- [Tests](#tests)

---

## What it shows

Paste the SQL an answer was built from -- the web interface and the desktop
client both show it, folded away under the answer -- or pick a table from the
schema, and run it one of three ways:

| Button | What runs | What comes back |
|---|---|---|
| **Run** | the validator, `EXPLAIN`, then the query | the rows, each column headed by its type, the plan, and the verdict |
| **Plan** | the validator and `EXPLAIN` -- exactly the agent's planner gate | the plan and the verdict; the query itself is not run |
| **Analyze** | the validator, `EXPLAIN`, then `EXPLAIN ANALYZE` | the plan with what actually happened beside each estimate -- time, rows, loops -- and no rows: the one to reach for when the agent's answer was a timeout |

**The schema is the agent's.** The panel on the left is
`Database.catalog()`, the introspection the agent's prompt is built from:
every table in `DB_SCHEMA` with its size estimate, and -- opened -- its
columns, types, keys and comments. A table with no comment says so, because
that is the table the agent was told nothing about.

**So is the prompt.** *Agent's view* on a table shows the block of the
agent's prompt that describes it -- `Database.schema_and_samples`, run here
exactly as it runs there, sample rows and all. When the agent picked the
wrong column, this is usually why: the model only ever knew what that text
told it.

**Rows are shown as the database returned them.** A numeric is its digits
(`719279.97`, not `719279.9699999999`), `NULL` is shown as NULL and styled
apart from an empty string, and each column names its type the way the
schema does. Up to `CONSOLE_MAX_ROWS` rows (1000) are read, through a
server-side cursor, so a `SELECT *` over the 1.29-million-row sales fact
fetches what it shows and no more. **Copy CSV** puts them on the clipboard.

**What was run is kept, in this browser only** -- the last 25 queries, each
marked by whether the agent would have accepted it, one click from the
editor. It is `localStorage`: nothing is sent anywhere, and a browser that
refuses storage just forgets.

## The verdict

The reason the console exists. The rows say what a query returns; the
verdict says whether the agent would ever have got that far, which gate
would have stopped it, and in that gate's own words -- the message its
Repair Agent would have been handed.

The gates run in the agent's order, with the agent's code:

| Gate | Here, as in the agent | Refuses |
|---|---|---|
| **static validator** | `validate.validate`, over `pglast`'s parse tree | more than one statement, anything but a `SELECT`, a data-modifying CTE, `SELECT ... INTO`, a function on the denylist -- and a relation outside the schema, with the closest name that is in it |
| **planner gate** | `EXPLAIN (FORMAT JSON)`, read by `total_cost` and judged by `plan_cost_problem` | whatever the planner refuses (an unknown column, a type mismatch, an aggregate outside `GROUP BY`) and an estimated cost over `MAX_PLAN_COST` |
| **executor** | the statement, in a `READ ONLY` transaction under `STATEMENT_TIMEOUT_MS` | whatever the database refuses at run time: a timeout, a division by zero, a write the validator did not recognise |

The first gate that objects is the verdict's stage; every objection is
listed. Some findings are not refusals but still change the answer the agent
would give -- a result longer than `MAX_ROWS` is one: the agent reads the
first 50 and is told the result was truncated, and the verdict says so.

Two things the console does differently from the agent, both on purpose:

- **It runs what the scope check refuses.** The agent's scope is the tables
  its Schema Retriever chose for the question; the widest it ever allows is
  the whole schema, and that is the scope here. A query on `pg_stats` or
  `information_schema` is reported as the refusal it would be -- and run
  anyway, because looking at the catalogue is part of troubleshooting.
  Everything else the validator refuses, the console refuses too: it never
  runs a writing CTE, whatever it would have been told.
- **It runs what the planner gate refuses on cost**, for the same reason:
  the rows of a query the agent would never have run are often the
  diagnosis. The statement timeout still bounds it.

The planner's words are passed on verbatim, including its hint -- `column
"store_nam" does not exist ... Perhaps you meant to reference the column
"dim_store.store_name"` -- and including the `EXPLAIN` it was quoting,
because that is exactly the text the agent's Repair Agent would have read.

## Why it runs from the agent's image

Because every answer it gives has to be the agent's. The validator, the
cost reader, the cost judgement, the introspection and the prompt's table
blocks are imported from `nl2sql_agent` rather than re-implemented -- the
cost ceiling's message was moved into `database.plan_cost_problem` so the
gate and the console read one function -- and the limits are read through
the agent's own `Settings`. A console that drifted from the agent would
reproduce nothing.

It is a separate *process* from the API because of what it does, not what
it is. The API runs SQL the pipeline wrote; this runs SQL a person typed.
Whoever can reach one should not thereby be able to do the other, so it has
its own port, its own token, its own container and its own compose profile,
and nothing of it exists unless `--console` asks for it.

## The fence

Everything a query here runs inside, from the outside in:

| Layer | What it does |
|---|---|
| **The address** | Both ports are published on `127.0.0.1` unless `CONSOLE_BIND_ADDRESS` says otherwise -- every other port in the stack is opened the way Docker opens ports, and a page that runs SQL is not one to offer the network by default. `launch.sh` warns when it is opened up with neither sign-in nor a token. |
| **Sign-in** | On by default (`AUTH_ENABLED`), in the console's own settings as well as in compose: every `/v1` route -- each on a router that carries the guard (6.2) -- needs a signed-in person in `CONSOLE_ALLOWED_ROLES` -- `nl2sql_reviewers` and `nl2sql_curators` by default -- and each statement runs as them, `SET LOCAL ROLE` from the reader, so it can read what they can and nothing more, with their name in the transaction's `application_name` (`nl2sql:console:<person>`). A session signed out, or from before a password change or a lock, is refused within a minute. See [`auth/README.md`](../auth/README.md). |
| **The token** | `CONSOLE_TOKEN`, when set, is a static service token: with sign-in off it guards every `/v1` route, and the interface's nginx holds it and adds it so the browser never has it; with sign-in on it is accepted beside sessions, and the interface sends none. Since 6.2 it is a caller of its own, named by `CONSOLE_TOKEN_NAME` and holding `CONSOLE_TOKEN_ROLES` -- the console's own allowed roles unless set. |
| **CORS** | Off unless `CONSOLE_CORS_ORIGINS` names an origin. The page is same-origin behind its proxy, and a SQL runner any site in the browser could call is not something to offer without being asked. |
| **TLS** | The console presents a certificate of its own, which the stack's pki service issues (`CONSOLE_TLS_HOSTNAMES` covers `nl2sql-console`), and the proxy verifies it against the stack's CA rather than trusting whatever answers. |
| **The role** | `DATABASE_URL` is the agent's own -- compose anchors the one value -- so every query runs as `nl2sql_reader`: `SELECT` on the retail tables and nothing else. A URL pointed at the owner by mistake is shown in the status bar and the readiness check. |
| **The validator** | The agent's, as above. Nothing it refuses for safety reaches the database. |
| **The transaction** | `SET TRANSACTION READ ONLY`, then rolled back whatever happened. This is what refuses the write the validator does not know about -- `SELECT lo_create(0)` passes it and is refused here, by the server. |
| **The clock** | `SET LOCAL statement_timeout` to the agent's `STATEMENT_TIMEOUT_MS`, so a cross join costs a timeout rather than the database. |
| **The cursor** | Server-side, reading one row past `CONSOLE_MAX_ROWS` and no further. |

## Running it

`./start.sh --console` and `./launch.sh --console` bring it up after the API,
because the API writes the certificate the console presents -- and, on the
first start after upgrading, reissues it to cover `nl2sql-console`.
`launch.sh` then asks for `/readyz` *through* the interface's proxy, and
restarts the proxy if it is still trusting the certificate from before.
`./setup.sh --console` pulls and pins the interface's image; the console
itself is the agent's image, already pulled.

In compose it is two services, each in a profile of its own:

```bash
docker compose --profile console --profile consolegui up -d consolegui
docker compose --profile console --profile consolegui logs -f console
docker compose --profile console --profile consolegui down
```

A pinned agent image from before 5.3 has no `nl2sql_agent.console` in it, and
the container stops saying so; `launch.sh` says which, and that the cure is
`docker compose --profile console build console` or a newer tag.

Without Docker, from `agent/`:

```bash
DATABASE_URL=postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail \
  python -m nl2sql_agent.console --no-tls --port 8445
```

and the interface against it, with a dev server that proxies the same paths
nginx does:

```bash
cd console && npm install
NL2SQL_CONSOLE_URL=http://localhost:8445 npm run dev     # http://localhost:5175
```

`npm run dev` verifies nothing by default (`NL2SQL_CONSOLE_TLS_VERIFY=true`
turns that on), because the certificate it meets is the development one on
the developer's own machine; it adds `CONSOLE_TOKEN` from the environment,
as nginx does.

## Endpoints

| Route | What it answers |
|---|---|
| `GET /` | What this is, and where to go next |
| `GET /healthz` | Whether the process is alive. No token, no database |
| `GET /readyz` | Whether it can run a query: the database (and how many tables) and the role (and whether it is read-only). 503 when the database cannot be reached. No token |
| `GET /openapi.json` | The contract, generated; `/docs` browses it unless `CONSOLE_DOCS_ENABLED=false` |
| `GET /v1/meta` | The database, the role, the server version, the schema, and every limit a query runs under |
| `GET /v1/schema` | Every table, as the agent's introspection reads it |
| `GET /v1/schema/{table}/prompt` | The block of the agent's prompt that describes one table |
| `POST /v1/query` | `{"sql": "...", "mode": "run"}` -- `run`, `plan` or `analyze` -- through the gates. A query a gate refuses is still a 200: the refusal is the answer |

Every failure has the API's error shape, `{"error": {"code", "message"}}`:

| Code | Status | When |
|---|---|---|
| `unauthorized` | 401 | Sign-in is off, `CONSOLE_TOKEN` is set and the request did not carry it |
| `sign_in_required` | 401 | Sign-in is on and there is no session or token |
| `expired`, `malformed`, `bad_signature`, `wrong_key`, `wrong_audience`, `not_yet_valid`, `account_removed`, `session_revoked` | 401 | A session that is not good any more -- `session_revoked` one that was signed out, or signed in before a password change, a lock or a removal: sign in again |
| `forbidden` | 403 | Signed in, but in no group `CONSOLE_ALLOWED_ROLES` names |
| `cross_site` | 403 | A cookie-authenticated request from another site |
| `sign_in_unavailable`, `roles_unavailable` | 503 | The auth service's key is not there yet, or Postgres could not be asked about groups |
| `invalid_request` | 422 | The body is not a query: empty, longer than 20,000 characters, an unknown mode, or a field that is not `sql` or `mode` |
| `unknown_table` | 404 | The prompt of a table that is not in the schema |
| `not_found` | 404 | A path the console does not serve |
| `database_unavailable` | 503 | The retail database could not be reached, or went away mid-query |

## Configuration

### The console

Read by `nl2sql_agent/console/settings.py`, each forwarded by compose and
empty unless set, so the default below stands.

| Variable | Default | What it does |
|---|---|---|
| `CONSOLE_HOST` | `0.0.0.0` | The interface to bind, inside the container |
| `CONSOLE_PORT` | `8445` | The port to serve on, and to publish |
| `CONSOLE_ROOT_PATH` | *(empty)* | A path prefix, when served under one by a reverse proxy |
| `CONSOLE_TLS_ENABLED` | `true` | Serve HTTPS. Off only behind something that terminates TLS itself |
| `CONSOLE_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` | The certificate to present -- its own, from the pki service |
| `CONSOLE_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` | Its key |
| `AUTH_ENABLED` | `true` | Accept signed-in people, and run each statement as the one who typed it. Only `false`, set by name, turns it off |
| `AUTH_PUBLIC_KEY_FILE` | `/etc/nl2sql/auth/session.pub` | The auth service's public key, which sessions are checked against |
| `AUTH_COOKIE_NAME` | `nl2sql_session` | The cookie a browser's session is in |
| `CONSOLE_ALLOWED_ROLES` | `nl2sql_reviewers,nl2sql_curators` | Who may use it, signed in |
| `CONSOLE_TOKEN` | *(unset)* | A static service token (or `X-API-Key`): required on every `/v1` route when sign-in is off, accepted beside sessions when it is on |
| `CONSOLE_TOKEN_NAME` | `console-token` | Who the token is |
| `CONSOLE_TOKEN_ROLES` | *(the allowed roles)* | The roles it holds, and no others; unset, `CONSOLE_ALLOWED_ROLES` |
| `CONSOLE_CORS_ORIGINS` | *(none)* | Browser origins allowed to call it directly, comma-separated |
| `CONSOLE_MAX_ROWS` | `1000` | Rows read and sent back for one query |
| `CONSOLE_DOCS_ENABLED` | `true` | Serve `/docs` and `/redoc` |
| `CONSOLE_LOG_LEVEL` | `info` | uvicorn's log level |

### The agent's, which it runs under

Compose passes the agent's own value for each -- `DATABASE_URL` and
`MAX_PLAN_COST` are the same YAML anchors the agent's service is built from
-- and `tests/console/test_console_compose.py` holds the two services to the
same values. Set one on the host and it changes for both. They are
documented with the rest of the agent's in
[`agent/README.md`](../agent/README.md).

| Variable | Default | What it is here |
|---|---|---|
| `DATABASE_URL` | the reader on `postgres:5432/nl2sql_retail` | The database, and the role every query runs as |
| `DB_SCHEMA` | `public` | The schema introspected, and the validator's scope |
| `STATEMENT_TIMEOUT_MS` | `30000` | The timeout every query runs under |
| `MAX_PLAN_COST` | `1000000` | The ceiling the planner gate refuses above |
| `MAX_ROWS` | `50` | How many rows the agent would have read, for the verdict's note |
| `SAMPLE_ROWS` | `3` | Sample rows in each table's block of the agent's prompt |

### The interface

Read by the nginx template and its start-up script, and set by compose from
the names on the left.

| Variable | Sets | Default |
|---|---|---|
| `CONSOLE_GUI_PORT` | `CONSOLE_GUI_PORT` | `8082` |
| `CONSOLE_GUI_UPSTREAM` | `CONSOLE_UPSTREAM` | `https://nl2sql-console:8445` |
| `CONSOLE_GUI_SSL_NAME` | `CONSOLE_SSL_NAME` | `nl2sql-console` |
| `CONSOLE_GUI_CACERT` | `CONSOLE_CACERT` | `/etc/nl2sql/tls/ca.crt` |
| `CONSOLE_GUI_READ_TIMEOUT` | `CONSOLE_READ_TIMEOUT` | `120s` -- longer than the statement timeout, or the proxy cuts off an answer that is coming |
| `CONSOLE_GUI_RESOLVER` | `CONSOLE_GUI_RESOLVER` | `127.0.0.11`, Docker's DNS |
| `CONSOLE_TOKEN` | `CONSOLE_TOKEN` | *(unset)*: no `Authorization` header is sent at all -- nor with sign-in on, whatever it holds |
| `AUTH_ENABLED` | `AUTH_ENABLED` | `true`: the page asks who you are, and sends the session rather than a token |
| `GUI_AUTH_UPSTREAM` | `AUTH_UPSTREAM` | `https://nl2sql-auth:8446`, where `/auth/` is proxied: the sign-in form posts there |
| `GUI_AUTH_SSL_NAME` | `AUTH_SSL_NAME` | `nl2sql-auth` |
| `GUI_AUTH_CACERT` | `AUTH_CACERT` | `/etc/nl2sql/tls/ca.crt` |
| `GUI_TLS_ENABLED` | `CONSOLE_GUI_TLS_ENABLED` | `true`: the page is HTTPS, with its own certificate, so a password never crosses in clear |
| `GUI_TLS_CERT_FILE` | `CONSOLE_GUI_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` |
| `GUI_TLS_KEY_FILE` | `CONSOLE_GUI_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` |

The `GUI_` ones are shared: one line in `.env` sets them for every
interface. And one for compose alone: `CONSOLE_BIND_ADDRESS` (`127.0.0.1`),
the host address both ports are published on.

### Flags

`python -m nl2sql_agent.console` takes a flag for each setting a person
starting it by hand is likely to change; the flag wins over the variable.

| Flag | Overrides |
|---|---|
| `--host`, `--port` | `CONSOLE_HOST`, `CONSOLE_PORT` |
| `--tls` / `--no-tls` | `CONSOLE_TLS_ENABLED` |
| `--cert`, `--key` | `CONSOLE_TLS_CERT_FILE`, `CONSOLE_TLS_KEY_FILE` |
| `--token` | `CONSOLE_TOKEN` |
| `--max-rows` | `CONSOLE_MAX_ROWS` |
| `--docs` / `--no-docs` | `CONSOLE_DOCS_ENABLED` |
| `--log-level` | `CONSOLE_LOG_LEVEL` |
| `--print-openapi` | Write the OpenAPI document and exit |

It prints what it reads and as whom before binding -- the database URL with
its password taken out, the agent's limits, the certificate's names -- and
refuses to start, with exit code 2, when TLS is on and there is no
certificate to present.

## The interface

Three columns: the schema, the work, and the history, with a status bar
beneath saying what it is connected to, as whom, and under which limits.

```
console/
├── src/
│   ├── App.tsx                  the page: the query, and what came of it
│   ├── api/client.ts            four calls and an error class
│   ├── api/types.ts             the wire contract, mirrored by hand
│   ├── api/format.ts            cells, counts, costs, identifiers, CSV
│   ├── api/plan.ts              EXPLAIN's JSON, flattened into rows
│   ├── api/history.ts           the queries run from this browser
│   └── components/
│       ├── SchemaBrowser.tsx    the agent's introspection, filterable
│       ├── SqlEditor.tsx        the query, and Run, Plan, Analyze
│       ├── Verdict.tsx          what the agent would have done
│       ├── ResultTable.tsx      the rows, typed, NULL apart
│       ├── PlanView.tsx         the planner's estimate, node by node
│       ├── PromptView.tsx       the agent's view of a table
│       ├── History.tsx
│       └── StatusBar.tsx
├── nginx.conf.template          the page, and the proxy to the console
├── 10-nl2sql-console-config.envsh   the token header, and the TLS block
└── Dockerfile                   node builds it, nginx serves it
```

A separate npm project and image from the other two interfaces, for the
review interface's reason: an entry point in the public GUI's project would
be served by the public GUI's image, to anyone who could reach it.

## Tests

| File | Covers |
|---|---|
| [`test_query.py`](../tests/console/test_query.py) | The gates in order, against a scripted database: what is refused before the database is asked, the fence every query runs inside, the cost read and judged as the gate does, what the executor refuses, and every cell type as JSON carries it |
| [`test_app.py`](../tests/console/test_app.py) | Every route and error code, the token, CORS, and readiness with a role that could write |
| [`test_settings.py`](../tests/console/test_settings.py), [`test_server.py`](../tests/console/test_server.py) | The settings, and that the console reads exactly the agent settings it names; the flags, the certificate it presents, the banner, and refusing to start without one |
| [`test_console_live.py`](../tests/console/test_console_live.py) | The real retail database as the real reader: types, a write the transaction refuses, a timeout, the sales fact read as far as it is shown |
| [`test_console_compose.py`](../tests/console/test_console_compose.py) | The two services as compose resolves them: the agent's URL and limits, one credential, the certificate's names, loopback ports, and every setting both ways |
| [`test_console_container.py`](../tests/console/test_console_container.py) | Four real containers on a private network: the proxy verifies the console's certificate, adds the token, and a write is refused by the database |
| [`test_console_project.py`](../tests/console/test_console_project.py), [`test_console_gui_contract.py`](../tests/console/test_console_gui_contract.py), [`test_console_gui_suite.py`](../tests/console/test_console_gui_suite.py) | The npm project, nginx and the start-up script; the TypeScript types field by field against the models; and the interface's own suite -- console GUI: 157 tests, at 100% of statements, branches, functions and lines |
