# Usage guide

How to set up, run and use every part of this project: asking questions in a
browser, a desktop window, a terminal or over HTTPS; reviewing the verdicts
people give on the answers; working out why an answer was wrong; seeing what
the agent did with a question; and measuring how well it does.

[`QUICKSTART.md`](QUICKSTART.md) is the five-minute version of this guide.
[`README.md`](README.md) explains how everything works and why it was built
that way; this guide is about using it.

## Contents

- [What it is](#what-it-is)
- [What you need](#what-you-need)
- [First run](#first-run)
- [Starting and stopping](#starting-and-stopping)
- [Where everything is](#where-everything-is)
- [Signing in](#signing-in)
- [Asking questions](#asking-questions)
  - [In the web interface](#in-the-web-interface)
  - [In the desktop client](#in-the-desktop-client)
  - [From a terminal](#from-a-terminal)
  - [Over the REST API](#over-the-rest-api)
  - [Writing questions that get good answers](#writing-questions-that-get-good-answers)
- [Reading an answer](#reading-an-answer)
- [Giving feedback](#giving-feedback)
- [Reviewing feedback](#reviewing-feedback)
- [Curating what it learns from](#curating-what-it-learns-from)
- [When an answer is wrong: the SQL console](#when-an-answer-is-wrong-the-sql-console)
- [Seeing what the agent did: MLflow](#seeing-what-the-agent-did-mlflow)
- [Measuring it: the benchmark](#measuring-it-the-benchmark)
- [Models](#models)
- [The knowledge base and the golden pairs](#the-knowledge-base-and-the-golden-pairs)
- [Configuration](#configuration)
- [Security](#security)
- [Upgrading and older versions](#upgrading-and-older-versions)
- [Troubleshooting](#troubleshooting)
- [Script reference](#script-reference)
- [Where to read more](#where-to-read-more)

---

## What it is

You ask a question about a retail business in plain English -- *"top 5
departments by net sales in fiscal year 2024"* -- and the agent writes the
SQL, checks it, runs it against a Postgres database of synthetic grocery
retail data, and answers with a sentence, a chart and the rows. Every number
in the sentence is traced back to the cells it was read from, and one that
cannot be is left out.

Behind the question is a team of agents: a Supervisor that screens it, five
retrievers that gather the schema, matching database values, business
knowledge, worked examples and verified SQL snippets, a SQL Generator, a validator and planner gate
that check the query before it runs, an executor that runs it read-only, a
reviewer that checks the result answers the question, a narrator, and an
auditor. Each model call goes to a model on your Ollama host.

It all runs in Docker, and three scripts start it. What you get:

| Part | What it is for | How to start it |
|---|---|---|
| Web interface | Ask questions in a browser and say whether each answer was right | `./start.sh` |
| Desktop client | The same, in a Java window | `./start.sh --desktop` |
| Terminal | Ask from a shell or a script | `docker compose run --rm agent "..."` |
| REST API | Ask from your own code, over HTTPS | `./launch.sh --api` |
| Review interface | Turn those verdicts into new golden questions and corrected queries | `./start.sh --review` |
| Curation interface | Write SQL snippets, golden questions and corrected queries directly, each run against the database first | `./start.sh --curate` |
| SQL console | Run SQL exactly the way the agent runs it, to see why an answer was wrong | `./start.sh --console` |
| MLflow | See everything the agent did with a question, agent by agent | `./start.sh --mlflow` |
| Benchmark | Measure accuracy and speed on fifteen questions | `python benchmarks/run_benchmark.py` |

---

## What you need

**Docker, with Compose v2.** Docker Desktop on macOS and Windows; Docker
Engine and the compose plugin on Linux. `start.sh` starts Docker Desktop if
it is not running. The first run downloads about 3 GB of images.

**bash.** The scripts run as they are on macOS and Linux. On Windows, run
them from WSL or Git Bash.

**An Ollama host serving a chat model.** This is the model that screens the
question and writes the SQL. It must support tool calling -- the agent uses
structured output -- and a long context window helps. The host can be this
machine or another one on your network. The compose default is
`qwen3.8-256k` at `http://192.168.10.82:11434`, the host this project was
developed against; yours is almost certainly somewhere else, so
[First run](#first-run) starts by saying where.

**Ollama on this machine, serving `bge-m3`.** This is the embedding model the
knowledge base was built with, and every question is embedded with the same
one. `start.sh` starts Ollama if it is installed and not running, and pulls
`bge-m3` into it if it is missing. Without it the agent still answers, but
from the schema alone, without the business knowledge and worked examples
that get hard questions right. Ollama is at <https://ollama.com>.

**Optional:**

- A **Java runtime, 21 or later**, for the desktop client. JavaFX is inside
  the jar, and Docker builds the jar, so nothing else is needed.
- **Python 3 and a virtualenv**, for the benchmark and for calibrating models
  to your host, which run on this machine rather than in a container:

  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -r tests/requirements.txt
  ```

Node, npm and Maven are only needed to work on the interfaces themselves;
the images are built with them inside.

---

## First run

Check your Ollama host answers, and see which models it has:

```bash
curl -s http://<your-ollama-host>:11434/api/tags
```

That should print JSON listing its models. It is **http**, not https: port
11434 does not terminate TLS.

Then:

```bash
git clone https://github.com/jamster88/nl2sql.git
cd nl2sql
./setup.sh --gui --ollama-url http://<your-ollama-host>:11434 --model <a-model-it-serves>
./start.sh
```

`setup.sh` pulls the images, pins their tags in a `.env` file, starts the
four databases -- loading the SQL snippets into the last -- checks that both
models are reachable, and finishes by
proving the agent container can retrieve from the knowledge base:

```
==> Checking the agent can reach the knowledge base
    retrieval works end to end (3 collections searched)
```

`start.sh` then starts everything else, checks it, waits until the web
interface actually answers, and opens <https://localhost:8080> in your
browser.

If the compose default host is yours, skip `setup.sh`: `./start.sh` on its
own runs it the first time.

### What setup writes

`.env`, next to `docker-compose.yml`, which every `docker compose` command
reads. It holds the image tags, the Ollama host and model, and where the
API sends verdicts and the agent sends traces. Running `setup.sh` again
moves the old file to `.env.bak` and writes a new one, keeping the host,
model and ports it found and every setting it does not write itself -- so
it is safe to re-run, and anything you add to `.env` by hand survives it.

To change the host or model later:

```bash
./setup.sh --ollama-url http://<another-host>:11434 --model <another-model>
```

---

## Starting and stopping

### The three scripts

| Script | When | What it does |
|---|---|---|
| [`./start.sh`](start.sh) | Whenever you want to use it | Starts Docker, and the Ollama on this machine, if they are down; runs `setup.sh` when `.env` is missing or pins older images than this checkout ships; runs `launch.sh`; waits until each page answers; and opens it |
| [`./launch.sh`](launch.sh) | When you want the stack without the browser step | Starts whatever is down, and checks each database is populated, both models are reachable, and which models calls will be routed to. Pulls nothing, so it takes seconds |
| [`./setup.sh`](setup.sh) | The first time, after changing host or model, or to pin an older version | Pulls and pins the images, writes `.env`, verifies retrieval end to end |

`start.sh` is the front door and the other two are its parts. Use them
directly when you want something specific: a terminal session with no API,
another agent version, no knowledge base.

### start.sh

```bash
./start.sh                                # the web interface, in your browser
./start.sh --desktop                      # the desktop client instead of the web interface
./start.sh --review                       # and the review interface, in a window of its own
./start.sh --console                      # and the SQL console, in a window of its own
./start.sh --mlflow                       # and MLflow, in a window of its own
./start.sh --curate                       # and the curation interface, in a window of its own
./start.sh --review --curate --console --mlflow   # every page
./start.sh --feedback                     # keep verdicts, without the review interface
./start.sh --load-golden                  # load the golden question document into the stores first
./start.sh --no-browser                   # start everything, and print the URLs instead
./start.sh --no-rag                       # the retail database only: no knowledge, no examples
./start.sh --restart                      # recreate the containers
./start.sh --quiet                        # print only problems
BROWSER=firefox ./start.sh                # open the pages with a browser of your choice
```

The flags combine. `--desktop` replaces the web interface rather than adding
to it: the web interface is not started, and the window opens instead.
`--review`, `--curate`, `--console` and `--mlflow` still open their pages in
a browser beside it.

The extra pages open in windows of their own -- Safari is asked through
AppleScript, which macOS asks you to allow once; Chrome, Firefox and the
browsers built on Chromium through their own new-window flag. Any other
browser, or one `BROWSER` names, is handed the page like any other link.

A page that does not come up is a warning rather than a failure. The rest
of the stack stays up, and the warning says which logs to read.

### launch.sh

The same stack without the browser, without starting Docker or Ollama, and
without re-pinning anything:

```bash
./launch.sh                               # the four databases: enough for the terminal
./launch.sh --api                         # and the REST API
./launch.sh --gui                         # and the web interface, with the API behind it
./launch.sh --feedback                    # and the staging database verdicts are kept in
./launch.sh --review                      # and the whole review system (implies --feedback)
./launch.sh --console                     # and the SQL console (implies --api)
./launch.sh --mlflow                      # and MLflow
./launch.sh --curate                      # and the curation interface, with the review service behind it
./launch.sh --desktop                     # build or fetch the desktop jar, and copy the stack's CA certificate out
./launch.sh --load-golden                 # load the golden question document into the stores first
./launch.sh --no-rag                      # the retail database only
./launch.sh --restart                     # recreate the containers
./launch.sh -q                            # print only problems
```

What it checks, every time:

- each database is healthy **and populated** -- the sales rows, the
  knowledge chunks, the golden pairs and their vectors, the schema index, the
  SQL snippets -- and the context store holds as many golden pairs as the
  question document;
- the snippet store holds the snippet document as it is now, and when it
  does not -- a fresh volume, or a document a `git pull` or the curation
  interface changed -- the snippets are loaded before anything is asked;
- the agent's read-only role exists and holds `SELECT` and nothing else, and
  `pg_trgm` is installed for matching literal values;
- the chat model is on the chat host, and `bge-m3` on this machine;
- which models the agent's calls will be routed to, asked of the agent image
  itself;
- that `.env` pins the agent image this checkout ships.

Each of these is a failure that happens *later* and looks like the agent
being bad at its job, which is why it is checked before the first question
rather than diagnosed after it. [Troubleshooting](#troubleshooting) says what
to do about each warning.

### Stopping

```bash
docker compose --profile '*' down         # everything; all data is kept
docker compose down                       # the four databases only
docker compose --profile mlflow down      # MLflow and its front door, and the four databases
```

`down` stops and removes containers. The data is in volumes, which `down`
keeps, so the next start picks up where you left off: staged verdicts,
stored fixes and MLflow's traces included. A service behind a profile is only
stopped when its profile is named, which is what `--profile '*'` does for all
of them.

To start the databases over from the images:

```bash
./setup.sh --reset
```

That deletes the volumes of the retail database, the knowledge base, the
context store and the snippet store and recreates them -- the first three
from their images, the snippets from their document. Anything changed in
them by hand is lost. Staged verdicts, fixes and traces are in other volumes
and are kept. `docker compose --profile '*' down -v` deletes *every* volume,
those included.

---

## Where everything is

| What | Address | Started by |
|---|---|---|
| Web interface | <https://localhost:8080> | `start.sh`, `launch.sh --gui` |
| Review interface | <https://localhost:8081> | `--review` |
| Curation interface | <https://localhost:8083> | `--curate` |
| SQL console | <https://localhost:8082> (this machine only) | `--console` |
| MLflow | <https://localhost:5001> (this machine only) | `--mlflow` |
| Directory page (people and groups) | <https://localhost:8084> (this machine only) | `--api` and everything that implies it, beside a standalone directory |
| Auth service (sign-in) | <https://localhost:8446> | `--api` and everything that implies it |
| The directory's own API | `nl2sql-auth:8447`, inside the stack only -- not published | with the auth service |
| REST API | <https://localhost:8443> | `--api`, `--gui`, `--feedback`, `--review`, `--console`, `--desktop` |
| Review service | <https://localhost:8444> | `--review`, `--curate` |
| SQL console's service | <https://localhost:8445> (this machine only) | `--console` |
| Retail database | `localhost:5432` (this machine only) | always |
| Context store (golden pairs) | `localhost:5433` (this machine only) | always, unless `--no-rag` |
| Knowledge base (pgvector) | `localhost:5434` (this machine only) | always, unless `--no-rag` |
| Snippet store (pgvector) | `localhost:5438` (this machine only) | always, unless `--no-rag` |
| Staging database (verdicts) | `localhost:5435` (this machine only) | `--feedback`, `--review` |
| Corrections store | `localhost:5436` (this machine only) | `--review`, `--curate` |
| Completions store | `localhost:5437` (this machine only) | `--review`, `--curate` |

MLflow's own database is not published at all, nor is MLflow itself -- port
5001 is its front door -- nor the directory, nor the directory's own API.
The databases are this machine's unless `DB_BIND_ADDRESS` says otherwise
(6.1). Every port can be moved in
`.env`: `GUI_PORT`, `REVIEW_GUI_PORT`, `CURATE_GUI_PORT`, `CONSOLE_GUI_PORT`,
`DIRECTORY_GUI_PORT`, `MLFLOW_PORT`, `API_PORT`, `REVIEW_PORT`,
`CONSOLE_PORT`, `AUTH_PORT`, `AUTH_DIRECTORY_PORT`, `POSTGRES_PORT`,
`CONTEXT_DB_PORT`, `VECTOR_DB_PORT`, `SNIPPETS_DB_PORT`, `FEEDBACK_DB_PORT`,
`CORRECTIONS_DB_PORT` and `COMPLETIONS_DB_PORT` -- and, inside its own
container, MLflow's front door listens on `MLFLOW_PROXY_PORT`, which only a
proxy in front of it need know about. The scripts read the same file, so
the URLs they print and open follow, and so does every page that proxies to
a moved service (6.1; before it, moving `API_PORT`, `REVIEW_PORT`,
`CONSOLE_PORT` or `AUTH_PORT` left the pages asking the old one).

`docker compose ps` lists what is running, and
`docker compose --profile '*' logs -f <service>` follows one service's log.

---

## Signing in

Every page asks who you are, and so do the desktop client and the REST API.
The first person is `admin`, in every group; the first run generated their
password into `.env`, which only you can read:

```bash
grep LDAP_ADMIN_PASSWORD .env
```

Sign in as them on the directory page, <https://localhost:8084>, and add
everyone else: a user name, a name, an address, the groups they are in, and
a password -- typed, or generated by the page for you to hand over. Or load
them all at once, there (**Import**) or on the directory's first start, from
a CSV or LDIF file: copy `ldap/seed/people.example.csv` to
`ldap/seed/people.csv`, edit it, and set `LDAP_SEED_FILE=/seed/people.csv`
in `.env` before the first start.

| Group | What it lets a person open |
|---|---|
| `nl2sql-users` | the web interface, the desktop client, the API |
| `nl2sql-reviewers` | the review interface, the SQL console, MLflow |
| `nl2sql-curators` | the curation interface, the SQL console |
| `nl2sql-admins` | the directory page, MLflow |

Everyone in any group can ask questions, and what they ask runs as them:
inside the database, the statement's `current_user` is the person, so
their grants -- and any row-level rule a table is ever given -- apply to
them. The connection itself is the agent's reader, so the server's own log
and `pg_stat_activity` name the reader; what the person asked is in the
agent's trace (MLflow), under their name. Every group reads every table
today: running as them is attribution, not yet isolation. A person changes
their own password from any page, beside **Sign out**. Five wrong passwords
in a row lock an account for fifteen minutes, which the directory page can
clear sooner.

Already have a directory -- Active Directory, or any LDAP server? Make this
one a read-only copy of it instead: `LDAP_MODE=replica` and where the
primary is, in `.env` ([`ldap/README.md`](ldap/README.md#replica) has the
settings, with Active Directory's). People and groups are then copied every
minute, a password is checked by the primary itself at each sign-in, and
there is no directory page: everything is changed on the primary.

Every page is HTTPS, each with a certificate of its own issued by the
stack's development CA, so the browser warns until this machine trusts that
CA -- once, for every page. Copy it out and add it to the system's trust
store (Keychain Access on macOS, `update-ca-certificates` on Debian and
Ubuntu), or mount real certificates:

```bash
docker compose --profile api cp api:/etc/nl2sql/tls/ca.crt ./nl2sql-ca.crt
```

For a browser on another machine, add this machine's name to
`TLS_EXTRA_HOSTNAMES` in `.env` -- every certificate is then issued to cover
it on the next start -- and give that browser the same `nl2sql-ca.crt`.

To run without sign-in -- on a machine nothing else can reach ([`SECURITY.md`](SECURITY.md#1-alone-sign-in-off)):

```bash
./start.sh --no-auth           # this run only
./setup.sh --no-auth           # from now on: AUTH_ENABLED=false in .env
```

[`auth/README.md`](auth/README.md) explains how it works.

### Connecting to the retail database directly

A person can also connect to the retail database with their own tools --
`psql`, a BI tool -- as themselves, with their directory password: read
only, a minute per statement, five connections at a time. The connection
must be encrypted -- the database refuses anything else over the network --
and should check that the server is the database (`sslmode=verify-full`),
because their directory password is their password for everything:

```bash
docker compose cp postgres:/etc/nl2sql/pg-tls/server.crt ./nl2sql-postgres.crt
psql "host=localhost dbname=nl2sql_retail user=alice sslmode=verify-full sslrootcert=./nl2sql-postgres.crt"
```

The database is published on this machine only. To let people connect from
elsewhere, set `DB_BIND_ADDRESS` (`0.0.0.0` for every interface) and add the
name they will connect to to `POSTGRES_TLS_HOSTNAMES` (its certificate is
reissued to cover it on the next start), and give them `nl2sql-postgres.crt`.
`launch.sh` warns while the databases are published beyond this machine.
Every store's port moves with `DB_BIND_ADDRESS`, so keep their passwords --
generated into `.env` -- out of reach.

---

## Asking questions

A question takes about a minute: several model calls, each on your Ollama
host. Every interface shows the pipeline's progress while it works.

### In the web interface

```bash
./start.sh                                # opens https://localhost:8080
```

- **Ask.** Type the question and press Enter (Shift-Enter adds a line). An
  empty page offers four example questions to start from.
- **Watch it work.** The agent's own steps appear as they finish --
  screening the question, matching values, reading the schema, writing SQL,
  checking the plan, running it, narrating, auditing -- with the steps still
  to come greyed out.
- **Read the answer.** The sentence first, then the chart, then the rows.
  Folded away underneath: the SQL and how many attempts it took, the phrases
  that were matched to database values, and how long each step took. Hover
  over a sentence and the cells it was read from light up in the table.
- **Say whether it was right.** See [Giving feedback](#giving-feedback).

Earlier questions stay in the session list beside the answer.

### In the desktop client

```bash
./start.sh --desktop                      # everything, then the window
./start.sh --desktop --review             # and the review interface in a browser
```

The same questions, answers and verdicts as the web interface, in a JavaFX
window: progress as it happens, the sentence with each claim tied to its
cells, the chart, the rows, the SQL. Verdicts go to the same place the web
interface's do, so a reviewer sees one queue.

`start.sh --desktop` fetches the jar for this machine (or builds it, if no
published one fits), copies the stack's CA certificate (`nl2sql-ca.crt`) out
so the client can verify the API, and opens the window, which stays open after the terminal closes.
Running it again finds the window already open rather than opening another.
It needs a Java runtime of 21 or later; without one, it says so and prints
the command to run with a newer one.

To run it yourself, against this API or another:

```bash
java -jar desktop/target/nl2sql-desktop.jar --cacert ./nl2sql-ca.crt
java -jar desktop/target/nl2sql-desktop.jar --url https://other-host:8443 --token <token> --cacert other.crt
```

| Flag | What |
|---|---|
| `--url URL` | The API. Default `https://localhost:8443` |
| `--token TOKEN` | Bearer token, when the API has `API_TOKEN` set |
| `--cacert FILE` | The certificate to verify the API against |
| `--fingerprint HEX` | Accept exactly the certificate with this SHA-256 instead |
| `--insecure` | Do not verify the certificate; the status bar says so for as long as it is on |
| `--wait SECONDS` | How long the server may hold a question open |

Each flag also has an environment variable; [`desktop/README.md`](desktop/README.md)
lists them.

### From a terminal

The stack's databases have to be up (`./launch.sh`, or `./start.sh`).

```bash
docker compose run --rm agent "How many stores are there?"
```

The agent runs in a container started for the question and removed after
it. Progress goes to stderr and the answer to stdout:

```
[screen] proceed / aggregate
[knowledge] 12 chunk(s) -- business_index:Market share fan-out ...
[examples] Q42 (0.675), Q36 (0.500), Q16 (0.394)
[tables] fact_pos_retail_sales, dim_product, dim_date
[sql] SELECT p.department_name, SUM(...)
[validation] valid
[planner] cost 20,555.96
[result] 5 row(s)
[audit] passed

Meat & Seafood led on net sales at 821785.92.
```

Other ways to run it:

```bash
docker compose run --rm agent                                   # interactive: one question per line, Ctrl-D to exit
echo "How many vendors are there?" | docker compose run --rm -T agent   # piped (-T is required)
docker compose run --rm agent --quiet "..."                     # just the answer
docker compose run --rm agent --json "..."                      # the whole run, as one JSON object
docker compose run --rm agent --json "..." | jq -r .sql         # just the SQL
```

Each question is answered on its own -- the agent does not remember the
previous one -- so ask complete questions rather than follow-ups.

`--json` carries everything: the Supervisor's verdict and intent, what a
complete answer had to contain, the retrieved knowledge and examples, the
SQL and every attempt before it, the rows, the reviewer's report, the
assumptions the answer made, the audited claims, a cost per step and, with
MLflow up, the run's `trace_id`.

The exit code is `0` when it answered, `1` when it could not, and `2` when
the Ollama host or model is wrong -- it checks both before anything else.

| Flag | What |
|---|---|
| `--model MODEL` | The chat model, and the one every routed call falls back to |
| `--base-url URL` | The Ollama host serving it |
| `--reasoning`, `--no-reasoning` | Let the model think before answering: slower, better on hard joins |
| `--rag`, `--no-rag` | Retrieve business knowledge for the question (on by default) |
| `--examples`, `--no-examples` | Retrieve worked question/SQL examples (on by default) |
| `--multi-shot`, `--no-multi-shot` | Show the examples to the SQL Generator as worked turns (on by default) |
| `--snippets`, `--no-snippets` | Retrieve verified SQL snippets -- joins, filters, measures -- for the question (on by default) |
| `--rag-top-k N` | Knowledge chunks retrieved per collection |
| `--examples-top-k N` | Worked examples handed to the model |
| `--snippets-top-k N` | SQL snippets handed to the model, at most |
| `--embed-model NAME` | The embedding model; must be the one the knowledge base was built with |
| `--embed-url URL` | The Ollama host serving it |
| `--max-rows N` | Rows read back from a query |
| `--max-attempts N` | SQL generations before giving up |
| `--sample-rows N` | Sample rows per table shown to the model |
| `--database-url URL`, `--vector-db-url URL`, `--context-db-url URL`, `--snippet-db-url URL` | Point at other databases |
| `--json` | The whole run as JSON |
| `--quiet` | Only the final answer |

[`agent/USAGE.md`](agent/USAGE.md) goes further: reading the progress
lines, the JSON fields, and what each failure means.

### Over the REST API

The same agent, as an HTTPS server, for a GUI of your own or anything else
that is not a shell.

```bash
./launch.sh --api
docker compose --profile api cp api:/etc/nl2sql/tls/ca.crt ./nl2sql-ca.crt

curl --cacert ./nl2sql-ca.crt https://localhost:8443/v1/meta
curl --cacert ./nl2sql-ca.crt 'https://localhost:8443/v1/questions?wait=180' \
     -H 'Content-Type: application/json' \
     -d '{"question": "How many stores are there?"}'
```

The server makes itself a self-signed certificate on first start; the `cp`
copies it out so clients can verify it rather than skip verification.
`start.sh --desktop` and `launch.sh --desktop` copy it out for you.

A question is a **job**. Without `?wait=`, `POST /v1/questions` returns
the job at once, and you collect it later or follow it as it runs:

| Route | What |
|---|---|
| `GET /v1/meta` | Version, model, tables, limits, which pipeline stages are on, the model routing table |
| `POST /v1/questions` | Ask. `?wait=<seconds>` to block until it is answered |
| `GET /v1/questions` | Recent questions, newest first |
| `GET /v1/questions/{job_id}` | One question. `?wait=<seconds>` to block |
| `GET /v1/questions/{job_id}/events` | Its progress, as Server-Sent Events |
| `DELETE /v1/questions/{job_id}` | Cancel a queued question, or forget a finished one |
| `POST /v1/questions/{job_id}/feedback` | A verdict: `{"verdict": "yes" \| "no" \| "incomplete", "comment": "..."}` |
| `DELETE /v1/questions/{job_id}/feedback` | Withdraw it |
| `GET /readyz`, `GET /healthz` | Ready to answer now; the process is alive |

The OpenAPI document is at <https://localhost:8443/openapi.json>, browsable
at <https://localhost:8443/docs>. To drive the whole API from a container
with nothing of this project in it -- a check that it really is an API:

```bash
docker compose --profile api run --rm apitest
docker compose --profile api run --rm apitest "total net sales for produce in FY2025"
```

[`agent/API.md`](agent/API.md) is the full contract: every route, response
and error code, the settings, and client code for TypeScript, Python and
Java. Set `API_TOKEN` before anything other than this machine can reach the
port ([Security](#security)).

### Writing questions that get good answers

The data is a grocery retailer's star schema: sales, prices, costs,
promotions, advertising, vendor allowances, competitor prices and market
share, by store, product and fiscal calendar.

**Name a measure, a grain, and a period.** *"Top 5 departments by net sales
in fiscal year 2024"* has all three. *"How are we doing?"* has none, and the
agent has to guess at every one. When a question leaves the period out, the
agent assumes the latest complete fiscal year and says so in the answer.

**The fiscal year starts on 1 April.** Fiscal year 2025 runs from 1 April
2024 to 31 March 2025, so "fiscal year 2024" is not calendar 2024. Say
"calendar 2024" when that is what you mean.

**Values need not be spelled the database's way.** "dairy and eggs" is
matched to `Dairy & Eggs`, and the `[literals]` line says what it was
matched to.

**Vocabulary that maps cleanly onto the data:** net sales, gross sales,
markdown, quantity sold, promo quantity sold, ad spend, impressions,
competitor price, market share, gross profit, department, category, brand,
store, banner, vendor, fiscal year, fiscal month, fiscal week, promo cycle.

**What it will not do.** The agent is read-only: a question that asks it to
change data is refused, and so is one about something this database does
not hold. A refusal is an answer with no table, because no query ran.

Some questions to start with:

```text
How many stores are there?
What were the top 5 product departments by net sales in fiscal year 2024?
Which 3 promotions had the highest total promo quantity sold?
Which 3 stores had the highest average net sales per basket?
What is our overall market share in fiscal year 2024?
total net sales for dairy and eggs in FY2025
```

---

## Reading an answer

| Part | What it is |
|---|---|
| **The sentence** | The narrator's answer. Every number in it is a claim the auditor traced back to cells in the result; a claim it could not trace goes back to the narrator once, and is dropped if it still cannot be traced |
| **Assumptions** | What the agent chose that the question did not say -- a period, a measure, a ranking -- each stated in the answer |
| **The chart** | The shape the Visual Formatter picked for the result: a single number, bars, grouped bars, a line, a scatter, or the table alone |
| **The rows** | The result itself. At most 50 rows are read back (`MAX_ROWS`); a cut-off result says it was truncated |
| **The SQL** | The query that produced the rows, and how many attempts it took |
| **The trace** | How long each step took and which model answered each call |

Before a query runs, it is parsed and checked against the schema (no model
call), and the database's planner estimates its cost, which must be under
the ceiling (`MAX_PLAN_COST`). It then runs as a read-only role with a
30-second timeout (`STATEMENT_TIMEOUT_MS`). After it runs, the Completeness
Reviewer checks the result carries what the question asked for -- a name
beside a SKU, every period asked about -- and sends it back once if not.

Any failure -- a parse error, a cost over the ceiling, a database error, a
result the reviewer rejects -- spends one attempt from the same budget of
seven SQL generations (`MAX_ATTEMPTS`). When the budget is spent the agent
stops and says why, rather than running something it could not check:

```
failed: Could not produce a valid query in 7 attempts. Last problems: ...
```

That is the expected outcome for a question the data cannot answer.
Rephrasing it with an explicit measure, grain and period helps more than
raising the budget.

---

## Giving feedback

Under every answer, in the web interface and the desktop client:

| Button | Means | What review does with it |
|---|---|---|
| **Correct** | The answer was right | May promote it into the golden question set, so future questions learn from it |
| **Wrong** | The answer was wrong | A reviewer writes and validates the query that should have been generated, into the corrections store |
| **Correct but incomplete** | The SQL was right, and the answer left out something a reader needed -- a product name beside a SKU | The same, into the completions store |

After a verdict a box appears for saying why; a comment is what makes a
"Wrong" fixable. Click the verdict you gave again to withdraw it. A vote
can be changed until a reviewer acts on it.

Where a verdict goes depends on what is running:

- **With `--review` or `--feedback`**, the API stages it in the staging
  database for review.
- **Without either**, it is kept in the browser and shown as given, and the
  page does not claim it was sent anywhere.
- **With `--mlflow`** as well, it is also recorded on that answer's MLflow
  trace.

A verdict that could not be sent is marked *not sent*, with a Retry button.

---

## Reviewing feedback

```bash
./start.sh --review                       # both pages: https://localhost:8080 and https://localhost:8081
```

The review interface has three tabs, one per verdict, each counting what is
still pending: **Correct → golden set**, **Wrong → corrections** and
**Correct but incomplete → completions**. Pick a submission from the queue.
The left half is what the user saw and said, which cannot be edited; the
right half is what you build from it.

**A correct answer → a golden pair.** The form starts from the question,
tables and SQL. Fill in what a vote cannot carry -- the **keywords**
someone would search for, the **reasoning target** (where generated SQL
typically goes wrong on this question), and the expected **result** -- check
the preview, and press **Promote to golden set**. The pair is appended to
[`context_questions/translated_questions.md`](context_questions/translated_questions.md)
in this checkout, the previous version is kept beside it as `.bak`, and both
retrieval stores are reloaded, so the agent can use it on the next question.
The change shows up in `git diff`: review it and commit it like any other
edit.

**A wrong or incomplete answer → a fix.** The editor starts from the
agent's SQL. Write the query that should have been generated, press
**Validate against the live database** -- it runs as the agent's read-only
role, in a read-only transaction, and shows the rows or Postgres's error --
then **Add to corrections** (or **Add to completions**). Only a query that
passed validation can be saved, exactly as it passed: edit it and it has to
be validated again. Fixes go to their own stores, never into the golden set.

**Accept** and **Reject** record a judgement without acting on it. A
rejected submission has to be accepted before it can be promoted or fixed.

**Changing your mind.** Under every submission, *Change this review* has
**Back to pending**, which returns it to the queue, and **Delete…**, which
removes it for good. If it had been promoted or fixed, the pair comes back
out of the golden set, or the fix out of its store, first -- and a reopened
submission gets its work back, as a draft or in the query editor. Anything
that reaches beyond the staging database asks first.

Anyone who can reach the review service can edit the question set the agent
is measured against, so set `REVIEW_TOKEN` before it is reachable from
anywhere but this machine. [`review/README.md`](review/README.md) has the
rules each step enforces.

---

## Curating what it learns from

```bash
./start.sh --curate                       # opens https://localhost:8083
```

The review interface works through what people said about answers. The
curation interface writes what the agent learns from directly, with nothing
waiting in a queue. It has three tabs:

**SQL snippets.** A snippet is one piece of SQL beside what it means: how two
tables **join**, what a phrase **filters** to ("store brands" is
`p.is_private_label`), how a **measure** is calculated (net sales, average
basket value), or a **dimension** to group by (a fiscal quarter labelled
`FY2025 Q3`). The agent finds the snippets a question means, by the phrases
listed for each and by meaning, and shows the ones whose tables it is using
to the SQL Generator. To add one, press **New snippet** and fill in:

- its **kind** and **name**, and what it **means**, in a sentence or two:
  the meaning is half of how a question finds it;
- the **keywords**: the phrases a question says it with, comma-separated.
  A phrase matches a question that has every one of its words, so prefer
  "store brands" to "store", which every question about stores would match;
- **Applies to**: the `FROM` clause it is written over, with its aliases --
  `fact_pos_retail_sales f`, say;
- the **SQL**: the join, the condition (without `WHERE`), the aggregate or
  the expression;
- a **note**: where it came from, or the mistake it prevents. The SQL
  Generator is shown it beside the SQL, so "both counts are integers, so
  without the cast the division truncates to zero" is worth writing down.

Then **Validate against the live database**. The snippet is run the way it
would be used -- a join joined, a filter in a `WHERE`, a measure aggregated
-- and the page shows the query it was checked in, the rows, and anything
worth knowing: a join that multiplied or dropped rows, a filter that keeps
nothing or everything. **Add snippet** is enabled once it passes. It is
written into
[`context_questions/sql_snippets.md`](context_questions/sql_snippets.md) in
this checkout and loaded into the snippet store, so the agent can use it on
the next question; the change shows up in `git diff`, to commit like any
other edit. Pick a snippet in the list to change or remove it.

**Golden pairs.** Add a question and the SQL that answers it, with the same
fields a promotion asks for. The SQL must run and return rows. Or remove a
pair. A pair the review queue produced goes back to that queue as pending,
so it can be judged again.

**Corrections & completions.** Store the query that answers a question the
agent gets wrong, or answers incompletely, without waiting for someone to
vote on it. Or remove a fix, with the same rule about the queue.

Nothing is saved that has not run against the live database, exactly as
typed: edit the SQL after validating it and it has to be validated again.
The page uses the review service, so the same `REVIEW_TOKEN` protects it, and
it can be up with or without the review interface.
[`curate/README.md`](curate/README.md) has the rules for each kind.

---

## When an answer is wrong: the SQL console

```bash
./start.sh --console                      # opens https://localhost:8082
```

The retail database, queried the way the agent queries it: as its read-only
role, in a read-only transaction, under its timeout and plan-cost ceiling,
through its own validator and planner gate. Paste the SQL from an answer --
both interfaces show it under the answer, and the terminal's `--json` prints
it as `sql` -- or pick a table, and run it one of three ways:

| Button | What runs | What you get |
|---|---|---|
| **Run** | the validator, the planner's estimate, then the query | the rows, typed, and the verdict |
| **Plan** | the validator and the planner's estimate -- exactly the agent's planner gate | the plan and the verdict, without running the query |
| **Analyze** | the query under `EXPLAIN ANALYZE` | the plan with real times and row counts beside each estimate: the one to use when the agent's answer was a timeout |

Beside every result is **the verdict**: whether the agent would have let
the query through, and if not, which gate would have stopped it, in that
gate's words, against which limit.

The schema panel is the agent's own view of the database. **Agent's view**
on a table shows the block of the prompt the agent was given about it --
when it picked the wrong column, this is usually why. **Copy CSV** copies the
rows. The last 25 queries are kept in this browser only.

The console is published on this machine only. [`console/README.md`](console/README.md)
has the rest.

---

## Seeing what the agent did: MLflow

```bash
./start.sh --mlflow                       # opens https://localhost:5001
```

Every question asked from then on -- from the web interface, the desktop
client, the terminal, the API or the benchmark -- is a trace in the
experiment `nl2sql-agent`. Open the experiment and its traces:

- **One trace per question**, holding the question and the answer.
- **A span per agent** -- Supervisor, Schema Retriever, Literal Matcher,
  Knowledge Retriever, Example Retriever, Context Aggregator, SQL Generator,
  Static Validator, Planner Gate, Safe Executor, Completeness Reviewer,
  Repair Agent, Visual Formatter, Insight Narrator, Audit Checker -- with
  the part of the state it read and the part it wrote back.
- **A span per model call** inside the agent that made it, named for the
  model: the messages it was sent, the answer, the tokens, and why the
  router chose that model. A repair shows as a second SQL Generator, so the
  attempts read top to bottom.
- **Verdicts.** One given in either interface appears on the trace of the
  answer it judges, as human feedback.

Each trace is tagged, so it can be found with the search box:

| Tag | Values |
|---|---|
| `nl2sql.outcome` | `answered`, `gave_up`, `refused` |
| `nl2sql.entrypoint` | `cli`, `api`, `benchmark` |
| `nl2sql.job_id` | the API's job id, for questions asked through the API or an interface |
| `nl2sql.attempts`, `nl2sql.model_calls`, `nl2sql.rows` | how the run went |
| `nl2sql.screening`, `nl2sql.intent`, `nl2sql.version` | what the Supervisor decided, and the agent's version |

For example, ``tags.`nl2sql.outcome` = 'gave_up'`` lists every question the
agent could not answer. From the terminal, `--json` prints the run's
`trace_id`.

Tracing is best effort. When MLflow is not up, questions are answered
untraced, and the agent looks for it again thirty seconds later, so MLflow
can be started or stopped under a running stack. `MLFLOW_TRACKING_URI=`
(empty) in `.env` turns it off altogether. MLflow's interface has no login
of its own and shows every question's rows, so it is reached only through
its front door, which asks who you are and lets in `nl2sql-reviewers` and
`nl2sql-admins`, and is published on this machine only.
[`README.md`](README.md#tracing) has the server's settings.

---

## Measuring it: the benchmark

Fifteen questions in five categories, scored on **execution accuracy** --
the agent's SQL is run and its rows compared with a verified reference
result -- and then on speed, per question and per pipeline step. It runs on
this machine, against the stack's published ports, from the virtualenv in
[What you need](#what-you-need):

```bash
./launch.sh
.venv/bin/python benchmarks/run_benchmark.py                    # the full agent
.venv/bin/python benchmarks/run_benchmark.py --compare          # schema-only vs knowledge vs multi-shot
.venv/bin/python benchmarks/run_benchmark.py --only B07 B08     # just these questions
.venv/bin/python benchmarks/run_benchmark.py --category grain   # one category
.venv/bin/python benchmarks/run_benchmark.py --json results.json
.venv/bin/python benchmarks/run_benchmark.py --verbose          # every pipeline step
```

It exits non-zero when any answer was wrong, so it can gate a pipeline.
With MLflow up, each configuration it measures is an MLflow run, holding
its settings, its scores and timings, its report, and every question's trace
judged right or wrong. [`benchmarks/README.md`](benchmarks/README.md)
explains the scoring.

---

## Models

**The chat model** (`OLLAMA_MODEL`, on `OLLAMA_BASE_URL`) writes the SQL
and answers every call nothing else is routed to. Change it with
`./setup.sh --model`, or for one question with `--model`. Pick one with
tool support.

**The embedding model** (`EMBED_MODEL`, `bge-m3`, on `EMBED_BASE_URL`) must
be the one the knowledge base was built with. A different model puts the
question in a different vector space, and retrieval returns confident
nonsense.

**Model routing.** Each model call can go to the fastest model on your host
that was measured to be suited to its task, at the question's complexity,
with `OLLAMA_MODEL` as the fallback. What it routes from is
[`models/catalog.json`](models/catalog.json), and **a catalog describes one
host**: pointed at a host the catalog does not describe, the agent sends
every call to `OLLAMA_MODEL`, and `launch.sh` says so. To route on your own
host, describe it and then measure its models:

```bash
python3 models/build_catalog.py <your-ollama-host>    # what the host serves -> models/catalog.json
.venv/bin/python models/calibrate.py                  # what each model is suited to; needs the stack up
```

Calibration takes a while: it runs each model through the benchmark and a
probe per task. Until a model has been calibrated, nothing is routed to it.
`launch.sh` prints the routing table on every start. To pin one agent's
calls to a model, or switch routing off:

```bash
MODEL_ROUTE_NARRATOR=<model> docker compose run --rm agent "..."
MODEL_ROUTING_ENABLED=false docker compose run --rm agent "..."
```

The same settings go in `.env` to apply everywhere.
[`models/README.md`](models/README.md) explains the catalog and the
calibration.

---

## The knowledge base and the golden pairs

The agent retrieves from two stores that ship as images, and a third built
from a document in this checkout:

- **The knowledge base** (`nl2sql-vectordb`): the documents in
  [`knowledge/`](knowledge) -- a data dictionary, a DDL index and a business
  index -- chunked and embedded. It carries what the schema cannot, such as
  which table repeats its totals once per competitor and has to be
  de-duplicated before it is summed.
- **The golden pairs** (`nl2sql-chunkdb`, with their vectors in the
  knowledge base): questions already answered with SQL that runs, in
  [`context_questions/translated_questions.md`](context_questions/translated_questions.md).
  The closest are shown to the SQL Generator as worked examples.
- **The SQL snippets** (`nl2sql-snippetsdb`): joins, filters, measures and
  dimensions, each run against the database and written beside what it
  means, in
  [`context_questions/sql_snippets.md`](context_questions/sql_snippets.md).
  This store ships empty: `launch.sh` loads the document into it whenever
  the two differ, so a fresh volume, a pulled change or a save in the
  curation interface all reach the agent on the next start or sooner.

The golden set grows through the review interface: each promotion is
appended to that document and loaded into both stores straight away. It has
no size limit -- ids run `Q01` to `Q99`, then `Q100` and on.

**The stores can fall behind the document.** They ship as images holding the
golden set as it was when they were published, and only a promotion reloads
them. Pairs promoted on another machine and committed arrive in the document
when you pull, and not in the stores, and the agent's worked examples are the
stores'. `launch.sh` compares the two on every start and warns when they
differ; `--load-golden` catches the stores up before anything is asked:

```bash
./start.sh --load-golden
```

It runs the two loaders a promotion runs -- the golden pairs into the
context store, then their embeddings into the vector store -- in the review
service's image, against this checkout's document. Only pairs that changed
are embedded, so with nothing new it changes nothing and takes a few seconds.
It needs the embedding model, like the agent; when that is down the pairs
are loaded and their vectors are not, and `launch.sh` says so.

Changing the documents in `knowledge/` means re-chunking and re-embedding
them with the pipeline in [`rag/`](rag/README.md), which only re-embeds what
changed.

The retail data itself is synthetic: fictional stores, products and
vendors over two fiscal years, made by the generator in
[`data_gen/`](data_gen/README.md) and baked into the database image.
`./setup.sh --build` regenerates it locally instead of pulling the image.

---

## Configuration

Settings go in `.env`, as `NAME=value` lines. Compose reads them, and so do
the scripts. A setting exported in the shell wins over `.env` for that
command. After changing one, restart what reads it -- `./start.sh --restart`
recreates the containers.

| Setting | Default | What |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://192.168.10.82:11434` | The chat model's host. `setup.sh --ollama-url` writes it |
| `OLLAMA_MODEL` | `qwen3.8-256k` | The chat model. `setup.sh --model` writes it |
| `EMBED_BASE_URL` | `http://host.docker.internal:11434` | The embedding host: the Ollama on this machine, as a container sees it |
| `EMBED_MODEL` | `bge-m3` | The embedding model |
| `RAG_ENABLED` | `true` | Retrieve knowledge and examples |
| `SNIPPETS_ENABLED` | `true` | Retrieve SQL snippets |
| `MODEL_ROUTING_ENABLED` | `true` | Route calls by the catalog |
| `MAX_ATTEMPTS` | `7` | SQL generations before giving up |
| `MAX_ROWS` | `50` | Rows read back from a query |
| `STATEMENT_TIMEOUT_MS` | `30000` | How long a query may run |
| `MAX_PLAN_COST` | `1000000` | The planner-cost ceiling a query must be under |
| `API_TOKEN` | *(none)* | Require this bearer token on the API |
| `REVIEW_TOKEN` | *(none)* | Require this bearer token on the review service |
| `CONSOLE_TOKEN` | *(none)* | Require this bearer token on the SQL console |
| `CONSOLE_BIND_ADDRESS` | `127.0.0.1` | Where the console's ports are published |
| `MLFLOW_TRACKING_URI` | `http://nl2sql-mlflow:5000`, written by `setup.sh` | Where traces go; empty turns tracing off |
| `MLFLOW_EXPERIMENT_NAME` | `nl2sql-agent` | The experiment traces are filed under |
| `MLFLOW_BIND_ADDRESS` | `127.0.0.1` | Where MLflow's port is published |

The ports are in [Where everything is](#where-everything-is). The full lists:
the agent's settings in [`agent/README.md`](agent/README.md#configuration),
the API's in [`agent/API.md`](agent/API.md), the review service's in
[`review/README.md`](review/README.md#configuration), the console's in
[`console/README.md`](console/README.md#configuration), and MLflow's server
in [`README.md`](README.md#tracing).

---

## Security

[`SECURITY.md`](SECURITY.md) is the threat model: what the stack protects,
from whom, what each credential is worth, and the three deployment tiers.
The defaults are its second, **a team on a trusted network**:

- **Sign-in** is on: every page, the API, the console, the review service
  and MLflow need a person signed in and in the right group, and what they
  do runs as their own database role. It is on in every service's own
  defaults as well as in compose, so only `AUTH_ENABLED=false` -- set by
  name -- turns it off, and a service started that way says it is open.
- **Every connection is encrypted, with a key of its own.** Each server and
  page has its own certificate from the stack's development CA, which
  clients trust once (`nl2sql-ca.crt`). Mount real certificates for anything
  beyond a trusted network; `API_TLS_ALLOW_SELF_SIGNED=false` makes the API
  refuse to start without one. The retail database refuses anything over
  the network without TLS, and a person's password reaches it only over a
  verified connection.
- **The pages, the API, the review service and sign-in** are reachable from
  the network. **The SQL console, MLflow, the directory page and every
  database** are this machine's only; opening one up (`CONSOLE_BIND_ADDRESS`,
  `MLFLOW_BIND_ADDRESS`, `DIRECTORY_GUI_BIND_ADDRESS`, `DB_BIND_ADDRESS`)
  gets a warning from `launch.sh`.
- **Every password is generated** per installation into `.env`, which only
  its owner can read: each database's, the agent's reader's, the directory's
  and sign-in's. The `postgres` superuser has none. No service token is
  set unless you ask for them (`./setup.sh --tokens`); with sign-in off they
  are each service's only control, and its page's proxy holds it so the
  browser never sees it.
- **The agent cannot write.** It connects as a role with `SELECT` and
  nothing else, re-created on every start, and runs every query in a
  read-only transaction under a timeout -- the planner's `EXPLAIN`
  included.

With sign-in off (`--no-auth`) the stack is the first tier, **alone**: keep
it on a machine nothing else can reach. What none of this does yet -- revoke
a session, name a service token, run the images unprivileged -- is listed
at the end of [`SECURITY.md`](SECURITY.md#known-limits).

---

## Upgrading and older versions

```bash
git pull
./start.sh
```

When the checkout ships newer images than `.env` pins, `start.sh` runs
`setup.sh` again first, which pulls and pins them and keeps your host,
model, ports and other settings. `launch.sh` alone only warns about it.

Every published version stays pinned and can be run again, for comparison
or to step back:

```bash
./setup.sh --agent-tag v5_3 && ./start.sh              # one earlier agent
./setup.sh --agent-tag v1 --no-rag && ./launch.sh --no-rag  # the original schema-only agent, terminal only
./setup.sh                                             # back to what this checkout ships
```

A tag chosen this way stays chosen. `start.sh` keeps it -- even when it runs
`setup.sh` again for something else, such as `--review` asked for the first
time -- and `launch.sh` says which agent is running rather than warning about
it. `./setup.sh` with no `--agent-tag` goes back to the shipped version, and
pulling a newer checkout re-pins it like everything else. An agent from
before `v4_1` has no REST API, so only the terminal can ask it questions.

Upgrading to `v5_6` adds one container, `nl2sql-snippetsdb`, on an empty
volume of its own; the first start loads the snippet document into it.

Upgrading to `v6_0_1` turns sign-in on ([Signing in](#signing-in)). It adds
three containers with the API -- the directory, the auth service and the
directory page -- and MLflow's front door with `--mlflow`, which now starts
the API too. `start.sh` generates the directory's and the role sync's
passwords into `.env` and makes the file readable by you alone; nothing in
the databases is lost, and the first start prepares the retail database for
sign-in. Every page becomes HTTPS, so the browser warns until it trusts the
certificate, and asks you to sign in -- as `admin` at first. Replace a
desktop jar from before 6.0.1 (`./start.sh --desktop` fetches the current
one). `./start.sh --no-auth`, or `./setup.sh --no-auth` for good, keeps the
stack as it was. `v6_0` itself is not worth pinning: under compose its
directory could not write its certificate, and nobody could sign in.

Upgrading to `v6_1` changes nothing you do and much of what runs:

- **Every server gets a certificate of its own**, from a development CA
  the stack now keeps (a new one-shot container, `nl2sql-pki`). The browser
  warns once more, for the CA: trust `nl2sql-ca.crt` instead of
  `nl2sql-api.crt` ([Signing in](#signing-in)), and the desktop client is
  started with `--cacert ./nl2sql-ca.crt` (`./start.sh --desktop` does it).
- **The retail database is `v1_2`**: TLS on, no password baked in. The
  first start applies it to the volume you have -- your data is kept --
  takes the superuser's password away and gives the owner and the reader
  the ones now in `.env`.
- **Every database password is generated** into `.env` the first time, and
  each store is told its new one as it starts; nothing in them is lost.
- **The databases are on this machine only.** Set `DB_BIND_ADDRESS` to
  publish them as before.

An agent from before `v6_0` does not check sessions, so pinned with
`--agent-tag`, its API answers according to its own `API_TOKEN`, signed in
or not. An agent or a page from before `v6_1` does not know the stack's CA,
and a dataset image from before `v1_2` serves no TLS, so sign-in fails
against it: pin them back together or not at all.

[`README.md`](README.md) lists what each tag is, and
[`CHANGELOG_SIMPLE.md`](CHANGELOG_SIMPLE.md) what each version changed.

---

## Troubleshooting

Most problems are reported by `launch.sh` -- and so by `start.sh` -- before
the first question.

| It says | What it means | What to do |
|---|---|---|
| `could not reach the chat host at ...` | The Ollama host is down or unreachable from here, or the URL is `https://` | Check it with `curl http://<host>:11434/api/tags`; `./setup.sh --ollama-url` to point elsewhere |
| `... is reachable but does not have <model>` | The model is not on that host | `ollama pull <model>` there, or `./setup.sh --model` with one it has |
| `Ollama is running here but does not have bge-m3` / `no Ollama on this machine` | No embedding model: questions are answered without knowledge or examples | Install Ollama; `ollama pull bge-m3`. `start.sh` does both when it can |
| `the retail database is up but has no sales rows` | An empty volume | `./setup.sh --reset` |
| `the vector store is up but holds no embedded chunks` / `no ddl_index_embeddings` | The knowledge base volume predates its image | `./setup.sh --reset` |
| `the context store holds N golden pairs, and context_questions/translated_questions.md M` | The question document has pairs the stores do not -- promoted elsewhere and pulled | `./start.sh --load-golden` |
| `the golden pairs did not load completely` | A loader failed -- usually the embedding model is down -- and the stores keep what they had | Start Ollama here with `bge-m3`, then `--load-golden` again |
| `the SQL snippets did not load completely` | The snippet loader failed, usually on the embedding model: the snippets are found by keyword alone meanwhile | Start Ollama here with `bge-m3`; the next start loads them again |
| `the pinned review image predates SQL snippets` | `.env` pins a review image from before `v5_6`, which has no snippet loader | `./start.sh`, which re-pins it, or `./setup.sh` |
| `the snippet store holds no SQL snippets` | Nothing has loaded it yet, so the generator is shown none | Read the warning above it; `./start.sh` again once that is fixed |
| `.env pins the agent image at ..., but this checkout ships ...` | You are running an older agent than the checkout | `./start.sh`, or `./setup.sh` |
| `model routing: ...` and a note about another host | The catalog describes a different Ollama host, so every call goes to `OLLAMA_MODEL` | Fine as it is; or build and calibrate a catalog ([Models](#models)) |
| `the REST API container did not become healthy` | It failed to start | `docker compose --profile api logs api` |
| `no API_TOKEN is set` | Sign-in is off, and anything that can reach port 8443 may ask questions | Turn sign-in back on (take `AUTH_ENABLED=false` out of `.env`), or set `API_TOKEN` before sharing the machine |
| `could not prepare the retail database for sign-in` | The group roles or the `pg_hba.conf` lines could not be written, so nobody can sign in | `docker compose logs postgres`; `./launch.sh` tries again on every start |
| `the directory or the auth service did not become healthy` | Nobody can sign in. Most often a directory image older than `v6_0_1`, which cannot write its certificate under compose | `./start.sh`, which re-pins `.env`; otherwise `docker compose --profile api --profile auth logs ldap auth` |
| `the directory page did not become healthy` | People cannot be added from a browser; everything else works | `docker compose --profile api --profile auth --profile directorygui logs directorygui`. Beside a replica it is never started, on purpose |
| `... cannot reach the API through its proxy` | The proxy holds an old certificate | `launch.sh` restarts it; if that fails, `./start.sh --restart` |
| `API_FEEDBACK_DB_URL is not set` | Verdicts will not be stored | Re-run `./setup.sh`, which writes it |
| `MLFLOW_TRACKING_URI is not set` | Nothing will be traced | Re-run `./setup.sh`, which writes it |
| `the desktop client needs a Java runtime of 21 or later` | No suitable Java on `PATH` | Install a JDK of 21 or later, or run the printed command with one |
| `could not open a browser` | No browser opener on this machine | Open the printed URL yourself |

Problems with answers rather than with the stack:

| Symptom | What to do |
|---|---|
| An answer is wrong | Run its SQL in the [SQL console](#when-an-answer-is-wrong-the-sql-console); read its [trace](#seeing-what-the-agent-did-mlflow); mark it **Wrong** with a comment |
| `Could not produce a valid query in 7 attempts` | Rephrase with a measure, grain and period |
| An answer ignored a value you named | Check the `[literals]` line, or the matched phrases under the answer |
| Retrieval returns irrelevant knowledge | The embedding model is not the one the knowledge base was built with: use `bge-m3` |
| Questions take minutes | Normal is about a minute, on a host that is not busy with something else. `launch.sh`'s routing table shows which models are in play; `--reasoning` (`OLLAMA_REASONING`) is off by default and much slower when on |
| Results stop at 50 rows | Raise `MAX_ROWS`, or ask for fewer rows |
| The browser warns that the page's certificate is not trusted | Every page's certificate is issued by the stack's development CA: trust `nl2sql-ca.crt` once ([Signing in](#signing-in)), or mount real ones. From another machine, also add this machine's name to `TLS_EXTRA_HOSTNAMES` |
| `psql` says "pg_hba.conf rejects connection ... no encryption" | The database accepts nothing in clear over the network: add `sslmode=require`, or better `verify-full` ([Connecting to the retail database directly](#connecting-to-the-retail-database-directly)) |
| "That name and password were not accepted" for someone just added | The role sync makes them a database role within thirty seconds; try again then |
| "Too many wrong passwords; try again in N seconds" | Five wrong for one name, or fifty from one address, in fifteen minutes. Wait, or restart the auth service, which forgets the count: `docker compose --profile api --profile auth restart auth` |
| "Locked out after too many wrong passwords" on the directory page | The directory locked the account after five wrong in a row; **Unlock** on the page, or wait fifteen minutes |
| The desktop client says "Not connected." to a server with sign-in on | A client older than 6.0.1; use the current jar (`./start.sh --desktop`) |

The logs: `docker compose --profile '*' logs <service>`, with the service
names in [Where everything is](#where-everything-is) (`api`, `gui`,
`review`, `reviewgui`, `curategui`, `console`, `consolegui`, `mlflow`, `mlflowproxy`, `ldap`, `auth`,
`directorygui`, `snippetsdb`, `postgres`, ...).

---

## Script reference

### start.sh

| Flag | What |
|---|---|
| `--desktop` | Use the desktop client instead of the web interface |
| `--review` | Also the feedback system and the review interface |
| `--curate` | Also the curation interface, and the review service behind it |
| `--console` | Also the SQL console |
| `--mlflow` | Also MLflow |
| `--feedback` | Keep verdicts, without the review interface |
| `--load-golden` | Load the golden question document into the stores first |
| `--no-browser` | Print the URLs instead of opening them |
| `--no-rag` | The retail database only |
| `--no-auth` | Without sign-in, this run only |
| `--restart` | Recreate the containers |
| `-q`, `--quiet` | Only print problems |
| `-h`, `--help` | The usage |

### launch.sh

| Flag | What |
|---|---|
| `--api` | Also the REST API |
| `--gui` | Also the web interface, and the API |
| `--feedback` | Also the staging database, and the API |
| `--review` | Also the review service, its interface and the two fix stores; implies `--feedback` |
| `--curate` | Also the review service, the two fix stores and the curation interface, without the review interface; implies `--feedback` |
| `--console` | Also the SQL console, and the API |
| `--mlflow` | Also MLflow, and its front door; implies `--api`, which brings up the sign-in the front door asks |
| `--desktop` | Build or fetch the desktop client, and copy the stack's CA certificate out; implies `--api` |
| `--load-golden` | Load the golden question document into the context store and its vectors before anything is asked |
| `--no-rag` | The retail database only |
| `--no-auth` | Without sign-in, this run only: no directory, no auth service, every page open |
| `--restart` | Recreate the containers |
| `-q`, `--quiet` | Only print problems |
| `-h`, `--help` | The usage |

With the API -- and everything that implies it -- `launch.sh` also prepares
the retail database for sign-in and starts the directory, the auth service
and, beside a standalone directory, the directory page.

### setup.sh

| Flag | What |
|---|---|
| `-u`, `--ollama-url URL` | The chat model's Ollama host |
| `-m`, `--model NAME` | The chat model |
| `--embed-url URL` | The embedding model's Ollama host |
| `--embed-model NAME` | The embedding model |
| `-p`, `--port PORT` | The retail database's host port |
| `--gui` | Also pull and pin the web interface |
| `--review` | Also the review service and its interface (implies `--gui`) |
| `--curate` | Also the curation interface. The review service is pinned whenever retrieval is on, because it loads the snippets |
| `--console` | Also the SQL console's interface |
| `--mlflow` | Also MLflow's server and store |
| `--desktop` | Also the desktop client's jar, for this machine |
| `--agent-tag TAG`, `--agent-image NAME` | Another agent version, or repository |
| `--build-agent` | Build the agent from this checkout instead of pulling it |
| `-t`, `--tag TAG`, `-i`, `--image NAME` | Another retail database image |
| `--build` | Build the retail database here, regenerating the data |
| `--gui-tag`, `--gui-image`, `--review-tag`, `--review-image`, `--review-gui-tag`, `--review-gui-image`, `--curate-gui-tag`, `--curate-gui-image`, `--console-gui-tag`, `--console-gui-image`, `--mlflow-tag`, `--mlflow-image`, `--mlflow-db-tag`, `--mlflow-db-image`, `--desktop-tag`, `--desktop-image`, `--vector-tag`, `--vector-image`, `--context-tag`, `--context-image` | Another version or repository of each image |
| `--no-rag` | No knowledge base: the agent answers from the schema alone |
| `--no-auth` | Sign-in off from now on (`AUTH_ENABLED=false` in `.env`); without it the sign-in images are pulled and pinned and their passwords generated into `.env` |
| `--tokens` | Also generate the three service tokens -- `API_TOKEN`, `REVIEW_TOKEN`, `CONSOLE_TOKEN` -- for scripts, and keep them from then on. Without it none is set, and signing in is the only way in |
| `--no-verify` | Skip the closing retrieval check |
| `--reset` | Delete the four databases' volumes first and start from the images and the snippet document |
| `-h`, `--help` | The usage |

Each script's `--help` prints the same, with defaults.

---

## Where to read more

| Document | What it covers |
|---|---|
| [`README.md`](README.md) | How it works, the measurements, the images and their versions, the tests |
| [`agent/USAGE.md`](agent/USAGE.md) | The terminal in depth |
| [`agent/README.md`](agent/README.md) | The agent's pipeline, every setting, model routing, tracing |
| [`agent/API.md`](agent/API.md) | The REST API contract, with client code |
| [`gui/README.md`](gui/README.md) | The web interface |
| [`desktop/README.md`](desktop/README.md) | The desktop client |
| [`review/README.md`](review/README.md) | Feedback review: promotion, fixes, taking a judgement back, and the curation routes |
| [`curate/README.md`](curate/README.md) | The curation interface: SQL snippets, golden pairs and fixes, written directly |
| [`console/README.md`](console/README.md) | The SQL console |
| [`benchmarks/README.md`](benchmarks/README.md) | The benchmark and its scoring |
| [`models/README.md`](models/README.md) | The model catalog and calibration |
| [`rag/README.md`](rag/README.md) | Building the knowledge base |
| [`data_gen/README.md`](data_gen/README.md) | The synthetic retail data |

To run the test suite: `pip install -r tests/requirements.txt`, then
`pytest` for the tests that need nothing running, and
`pytest --run-docker --run-node --run-java` for all of them.
[`README.md`](README.md#tests) explains what each covers.
