# nl2sql

New here? [`QUICKSTART.md`](QUICKSTART.md) takes you from a fresh clone to a
first answer, pointing the stack at your own Ollama host on the way.
[`USAGE_GUIDE.md`](USAGE_GUIDE.md) covers using every part of it. This README
is how it all works.

## Quick start

```bash
./start.sh              # the web interface, in your browser
./start.sh --desktop    # the Java desktop client instead, in a window
```

That is the whole thing. [`start.sh`](start.sh) starts Docker if it is not
running, and the Ollama on this machine that embeds each question, giving it
the embedding model if it lacks it; pulls what is missing, and whatever this
checkout ships that is newer than `.env` pins; starts every container; and
puts an interface in front of you. Which interface is the only choice it asks
you to make, and it has a default: with no flag it waits until the page
actually answers and opens it in your browser at <https://localhost:8080>; with
`--desktop` it fetches the desktop client's jar (building it if there is no
published one for this machine), copies the stack's CA certificate out for
the client to verify against, and opens the window instead. Either way
`--review` brings the feedback system up as well and opens the review page
in a browser window of its own, `--console` does the same for the SQL
console -- the retail database, queried the way the agent queries it --
`--mlflow` for MLflow, where every question is traced agent by agent, and
`--curate` for the curation interface, where the SQL snippets and the golden
pairs the agent learns from are written directly.

Every page asks who you are. The first person is `admin`, whose password
the first run generates into `.env` (`grep LDAP_ADMIN_PASSWORD .env`); they
add everyone else on the directory page at <https://localhost:8084>. See
[Sign-in](#sign-in), and `--no-auth` for a run without it.

First run is a few minutes and about 3 GB of images; afterwards it is
seconds.

Prefer a terminal?

```bash
./setup.sh
docker compose run --rm agent "How many stores are there?"
```

Either way the same containers come up:

| Container | What it holds |
|---|---|
| `nl2sql-postgres` | The retail dataset, baked into the image |
| `nl2sql-vectordb` | pgvector: the knowledge base and the golden-pair vectors |
| `nl2sql-chunkdb` | The context store: the golden pairs and their BM25 index |
| `nl2sql-snippetsdb` | pgvector: the SQL snippets -- joins, filters, measures and dimensions -- and an embedding of what each means, loaded from `context_questions/sql_snippets.md` |
| `agent` | The v4 agent, run on demand per question |
| `nl2sql-api` | The same agent as a TLS REST server, started only with `--api` |
| `nl2sql-gui` | The web interface, and the proxy in front of the API, with `--gui` |
| `nl2sql-feedbackdb` | Verdicts from the web interface, waiting to be reviewed, with `--feedback` |
| `nl2sql-correctionsdb` | pgvector: wrong answers and the validated queries that fix them, with `--review` |
| `nl2sql-completionsdb` | pgvector: incomplete answers and the validated queries that complete them, with `--review` |
| `nl2sql-review` | The service that turns reviewed feedback into golden questions or fixes, with `--review` |
| `nl2sql-review-gui` | The review interface, and the proxy in front of that service, with `--review` |
| `nl2sql-curate-gui` | The curation interface, and the proxy in front of the same service, with `--curate` |
| `nl2sql-console` | The agent image again, as the SQL console: the retail database queried as the agent's read-only role and through its gates, with `--console` |
| `nl2sql-console-gui` | The SQL console's interface, and the proxy in front of it, on this machine only, with `--console` |
| `nl2sql-mlflowdb` | MLflow's tracking store: every question's trace, and the verdicts given on it, with `--mlflow` |
| `nl2sql-mlflow` | MLflow's server and interface, with `--mlflow`; reached only through its front door |
| `nl2sql-mlflow-proxy` | MLflow's front door: HTTPS, and sign-in asked about every request, on this machine only, with `--mlflow` |
| `nl2sql-ldap` | The directory: people, their passwords and the four groups -- standalone, or a read-only replica of another directory -- with the API |
| `nl2sql-auth` | Sign-in: checks a password by signing in to the retail database, issues the session, keeps the database's roles in step with the directory, with the API |
| `nl2sql-directory-gui` | The directory page, where administrators add and edit people, on this machine only, beside a standalone directory |

The agent, the GUI, both halves of the review system, the curation
interface, the SQL console's interface, the desktop client's jar, MLflow's
server, store and front door, the directory, the auth service and the
directory page are published images (`v6_1`); the rest are built or pulled
by `setup.sh` as well -- the snippet store is a stock pgvector, filled from
its document by `launch.sh`. [Pulling the images](#pulling-the-images) has
the tags, and [`CHANGELOG_SIMPLE.md`](CHANGELOG_SIMPLE.md) what changed in each.

### Three scripts

| | When | What it does |
|---|---|---|
| [`./start.sh`](start.sh) | You just want to use it | Starts Docker and this machine's Ollama if they are down, runs the two below -- `setup.sh` too whenever `.env` is older than this checkout -- and opens an interface: the web one in your browser by default, or the Java desktop client with `--desktop`. `--review` brings the feedback system up as well and opens the review page in a window of its own, `--curate` the curation page, `--console` the SQL console, and `--mlflow` MLflow |
| [`./setup.sh`](setup.sh) | First run on a machine | Pulls every image, pins them in `.env`, starts the databases, verifies retrieval end to end |
| [`./launch.sh`](launch.sh) | Every time after | Starts whatever is down and checks it is *populated* -- loading the SQL snippets when their document has changed -- that both models are reachable, and which models calls will be routed to |

`start.sh` adds nothing to the stack itself -- that is the other two
scripts' -- but it starts what the stack runs on, keeps `.env` pinned to what
this checkout ships, and opens an interface. Use the other two directly when
you want the parts separately: a terminal session with no API, a different
agent tag, no knowledge base.

```bash
./start.sh --review        # and the review interface, in a window of its own
./start.sh --desktop       # the Java desktop client instead of the web one
./start.sh --desktop --review   # the window, and the review page in a browser
./start.sh --feedback      # keep verdicts, without the review interface
./start.sh --load-golden   # load the golden question document into the stores first
./start.sh --console       # and the SQL console, in a window of its own
./start.sh --mlflow        # and MLflow, where every question is traced
./start.sh --curate        # and the curation interface, in a window of its own
./start.sh --review --curate --console --mlflow   # all five pages, the last four in windows of their own
./start.sh --no-browser    # everything up, prints the URLs instead
./start.sh --no-rag        # schema-only, like v1
./start.sh --no-auth       # no sign-in, this once: every page open to whoever reaches it
./start.sh --restart       # recreate the containers
./start.sh --quiet         # only print problems
BROWSER=firefox ./start.sh # open it with something in particular
```

`--review` is the whole feedback system in one command: the staging database
that keeps verdicts, the service that promotes them into the golden question
set, and a second page at <https://localhost:8081> in a browser window of its
own. `open` and `xdg-open` cannot ask for a window -- they hand the browser a
link and its settings pick a tab or a window -- so `start.sh` asks the
default browser itself: Safari through AppleScript, which macOS lets a
terminal do once you have said it may, and Firefox, Chrome and the browsers
built on Chromium with their own new-window flag. Any other browser, or one
`BROWSER` names, is handed the page the way it would be handed any link.

Afterwards, whichever route you took:

```bash
docker compose run --rm agent "<your question>"
```

Or in a browser -- see [The web interface](#the-web-interface):

```bash
./launch.sh --gui          # without the browser step
open https://localhost:8080
```

Or with the feedback system, which keeps the verdicts people give in the web
interface and lets them be turned into golden questions -- see
[Feedback](#feedback):

```bash
./start.sh --review        # both pages, opened for you
./launch.sh --review       # the same containers, without the browser step
```

Or, when an answer is wrong, the SQL console -- the retail database queried
as the agent's read-only role, through the agent's own gates, with the
verdict of each beside the rows; see [The SQL console](#the-sql-console):

```bash
./start.sh --console       # opened for you, in a window of its own
./launch.sh --console      # the same containers, without the browser step
```

Or, to teach it this database's pieces -- how two tables join, what a phrase
filters to, how a measure is calculated -- and to add or remove golden pairs,
corrections and completions without going through the review queue, the
curation interface, at <https://localhost:8083>; see
[SQL snippets and curation](#sql-snippets-and-curation):

```bash
./start.sh --curate        # opened for you, in a window of its own
./launch.sh --curate       # the same containers, without the browser step
```

Or, to see what the agent did with a question -- each agent's span with what
it read and what it wrote, and every model call inside it -- MLflow, at
<https://localhost:5001>; see [Tracing](#tracing):

```bash
./start.sh --mlflow        # opened for you, in a window of its own
./launch.sh --mlflow       # the same containers, without the browser step
```

Or in a window rather than a browser -- the same questions, the same answers
and the same verdicts, from a Java application on this machine; see
[The desktop client](#the-desktop-client):

```bash
./start.sh --desktop       # builds it, trusts the API, opens it
./launch.sh --desktop      # build it and stop there
```

Or as a REST server for something else to talk to, which is the same agent
started as a server instead of a command -- see
[Connecting a GUI](#connecting-a-gui):

```bash
./launch.sh --api
curl --cacert ./nl2sql-ca.crt https://localhost:8443/v1/meta
```

Setup and launch fail in different ways, which is why they are separate.
Setup fails when an image will not pull. Launch catches the things that go
wrong later: a container that is up but empty, a chat host that has moved, an
embedding model that is not the one the vectors were built with. None of those stop the stack from starting,
and all of them make the agent look bad at its job rather than broken.

```
$ ./launch.sh
==> Checking what is actually in each database
    retail dataset: 1291781 sales rows
    knowledge base: 53 embedded chunks
    worked examples: 48 golden pairs, 48 embedded questions
    SQL snippets: 32 snippets, 32 embedded meanings
    schema index: 20 DDL chunks (table selection needs no model call)

==> Checking the multi-agent pipeline
    literal matching: pg_trgm installed (trigram search)
    least privilege: the agent's role holds SELECT and nothing else

==> Checking the models
    chat model qwen3.8-256k is available at http://192.168.10.82:11434
    embedding model bge-m3 is available on this machine

==> Ready. Ask a question:

    docker compose run --rm agent "How many stores are there?"
```

Then ask. That is the whole contract: one script, then one command per
question.

```bash
docker compose run --rm agent "total net sales for dairy and eggs in FY2025"
```

Everything the script prints is something that fails *later* and looks like
the agent being bad at its job. A container that is up but empty. A chat host
that moved. An embedding model that is not the one the vectors were built
with. A `.env` still pinning the previous agent image, so an upgrade silently
has no effect. And the two the multi-agent pipeline added: the DDL-chunk
collection its table selection reads instead of calling the model, and whether
its database role has picked up a grant it should not have. The snippet store
is the one thing it fills rather than checks: the store is built from a
tracked document, and whenever the document's hash differs from the one the
last complete load recorded, the loader runs before anything is asked.

`./launch.sh --no-rag` starts only the retail database; `--restart` recreates the
containers; `-q` prints only problems. Run it with no `.env` present and it hands
off to `setup.sh` rather than guessing.

[`setup.sh`](setup.sh) pulls each image, starts the databases, writes a `.env` so
plain `docker compose` commands pick all of that up, checks that the chat and
embedding models are reachable, and finishes by proving the agent container can
actually retrieve from the knowledge base:

```
==> Checking the agent can reach the knowledge base
    retrieval works end to end (3 collections searched)

==> Setup complete. Running now:

    nl2sql-postgres    the retail dataset
    nl2sql-vectordb    the embedded knowledge base
    nl2sql-chunkdb     the golden pairs and their BM25 index
    nl2sql-snippetsdb  the SQL snippets the generator is shown
```

It takes a couple of minutes, mostly downloading, and is safe to re-run.

Useful flags: `--ollama-url URL` and `--model NAME` to point the agent at a
different Ollama host or model, `--embed-url URL` for the host serving the
embedding model, `--no-rag` to skip the knowledge base entirely, `--build-agent`
to build the agent from source instead of pulling it, `--build` to generate the
dataset locally, `--no-verify` to skip the closing check, and `--reset` to
discard an existing database volume and start from the image's data.
`./setup.sh --help` lists them all.

Stop the four databases with `docker compose down`; they keep their data.

## NL2SQL agent (v4, multi-agent)

A LangChain/LangGraph agent that answers natural language questions by writing,
validating, and running SQL against the Postgres container below. It uses any
model served by Ollama.

**v2 retrieves before it writes.** Each question is embedded and matched against
a pgvector knowledge base built from [`knowledge/`](knowledge) -- the data
dictionary, DDL index and business index -- and the matching sections are fed to
the model alongside the schema. That context carries what the schema cannot:
fiscal-calendar semantics, pre-aggregated columns, and joins that fan out.

**v3 also retrieves worked examples, and generates multi-shot.** A second,
independent step searches the question/SQL pairs in
[`context_questions/translated_questions.md`](context_questions/translated_questions.md)
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
of its own. [Feedback](#feedback) has the whole of it.

**v5.6 shows the generator the pieces a query is built from.** A *SQL
snippet* is one verified piece of SQL -- a join, a filter, a measure or a
dimension -- beside what it means in a question's words: "store brands" is
`p.is_private_label`, "transactions" is `COUNT(DISTINCT f.basket_id)`, and
sales reach the fiscal calendar through `f.sales_date_key`. They are curated
in [`context_questions/sql_snippets.md`](context_questions/sql_snippets.md),
apart from the golden pairs, which are whole questions answered, and loaded
into a store of their own. A fifth Stage 1 retriever finds each question's
snippets by keyword phrase and by meaning, and the generator is shown the
ones whose tables are all in scope. [SQL snippets and
curation](#sql-snippets-and-curation) has the whole of it.

**v5.2 (arch5.2) routes every model call.** Triage, draft, column check,
sentence and diagnosis each go to the fastest model on the Ollama host that
calibration measured to be suited to the task, at the complexity the question
presents: light, standard or heavy, computed from the pipeline's own state
without a model call. A repair climbs the ladder, a routed model that cannot
answer falls back to `OLLAMA_MODEL`, and the trace names the model that
answered every call. The list it routes from is the
[model catalog](#model-catalog): what the Ollama host serves and what each
model was measured to be suited to. The committed catalog describes the host
this checkout was developed against; on any other host every call goes to
`OLLAMA_MODEL`, exactly as in v5.1, until that host has a catalog of its own,
and `MODEL_ROUTING_ENABLED=false` makes it v5.1 outright.

The design, and every place it departs from the source documents, is in
[`multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md):
arch5.2 as built in 6.1.0 -- the answer contract, the Completeness Reviewer,
human review and model routing, with each field of the shared state given
its lifetime and the security blueprint made a verified table -- plus the
console, taking a judgement back, tracing, snippets and curation, and the
sign-in design (section 20) and 6.1's transport (section 21). The threat
model and the deployment tiers the defaults implement are in
[`SECURITY.md`](SECURITY.md).

See [`agent/USAGE.md`](agent/USAGE.md) for how to launch it and ask questions,
and [`agent/README.md`](agent/README.md) for how it works.

```bash
docker compose run --rm agent "What were the top 5 departments by net sales in fiscal year 2024?"
```

Where it shows: the benchmark's grain and fan-out questions. Schema-only scores
1 of 3 on grain and 0 of 1 on fan-out; with the knowledge base both go to full
marks. See [Benchmark](#benchmark) for the run.

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

### Pulling the images

```bash
docker pull mcfaddja/nl2sql-agent:v6_1     # the agent, the REST API and the SQL console
docker pull mcfaddja/nl2sql-gui:v6_1       # the web interface
docker pull mcfaddja/nl2sql-review:v6_1    # the review service, and the snippet loader
docker pull mcfaddja/nl2sql-review-gui:v6_1  # the review interface
docker pull mcfaddja/nl2sql-curate-gui:v6_1  # the curation interface
docker pull mcfaddja/nl2sql-console-gui:v6_1  # the SQL console's interface
docker pull mcfaddja/nl2sql-mlflow:v6_1    # MLflow, where every question is traced
docker pull mcfaddja/nl2sql-mlflowdb:v6_1  # the Postgres MLflow keeps traces in
docker pull mcfaddja/nl2sql-mlflow-proxy:v6_1  # MLflow's front door
docker pull mcfaddja/nl2sql-ldap:v6_1      # the directory
docker pull mcfaddja/nl2sql-auth:v6_1      # sign-in
docker pull mcfaddja/nl2sql-directory-gui:v6_1  # the directory page
```

The console has no image of its own: it is the agent's, started a third way.
Nor does the snippet store: it is stock `pgvector/pgvector:pg18`, filled from
the document by the loader in the review service's image -- which is why
`setup.sh` pins that image whenever retrieval is on.
MLflow's two are thin: MLflow's own published server
([`docker/mlflow/Dockerfile`](docker/mlflow/Dockerfile)), pinned to the
version of the tracing client the agent carries, and stock Postgres
([`docker/mlflowdb/Dockerfile`](docker/mlflowdb/Dockerfile)), each labelled
and published with the release so that a release's images are one set.

The desktop client is published too, but by platform rather than by
architecture, because a jar carries native code for the machine it will draw
on: `mcfaddja/nl2sql-desktop-build:v6_1-mac-aarch64` and the four siblings
named in [The desktop client](#the-desktop-client). The image holds the jar
and nothing else -- 33 MB, not the gigabyte of Maven that produced it --
and `./launch.sh --desktop` pulls the one this machine needs, falling back to
building it when there is nothing to pull.

`setup.sh` pulls the agent for you and pins it in `.env`. The GUI is opt-in,
because most people ask questions from a terminal and an image for a
container that is never started is a download nobody asked for:

```bash
./setup.sh --gui        # pulls and pins it too, so ./launch.sh --gui runs it
./setup.sh              # leaves it out; ./launch.sh --gui builds it here
./setup.sh --curate     # the same for the curation interface
./setup.sh --console    # the same for the SQL console's interface
./setup.sh --mlflow     # and for MLflow's server and store
```

Both work. The difference is that compose builds a service whose image is
missing, so without the pin the first `./launch.sh --gui` spends a couple of
minutes running `npm ci` inside a container.

### Upgrading an existing checkout

`.env` pins the image tags. `start.sh` notices when this checkout ships
newer ones than `.env` pins -- or when the interface asked for was never
pinned, and would be built from source -- and runs `setup.sh` again before
anything starts, so after pulling a new checkout the one command is still
one command:

```bash
git pull
./start.sh --review
```

`launch.sh` does not rewrite `.env`. It says the agent is older than the
checkout, and re-running `setup.sh` is the cure:

```bash
./setup.sh --review     # re-pins the tags, and pulls the two review images
./launch.sh --review
```

Re-running it is safe. It rewrites `.env` from scratch, but carries over what
the last run chose -- the Ollama host, the models, the port, and whether the
web interface, the review images, the curation interface, the SQL console's
interface and the desktop client were pinned -- and
keeps every other setting it finds there, an `API_TOKEN` or a port set by
hand, so only the tags change. The previous file is still kept as `.env.bak`.

`start.sh` leaves `.env` alone when it pins no agent image (the agent is
built from this checkout), when it pins one from another repository, when
`AGENT_IMAGE_TAG` is exported for the run, or -- since `v5_5_1` -- when this
checkout's own `setup.sh` pinned an agent other than the one it ships, as
`./setup.sh --agent-tag v5_3` does: those are choices rather than leftovers.
`setup.sh` writes the release it belongs to into `.env` as `SETUP_RELEASE`,
which is how the two are told apart. A chosen tag is kept even when
`start.sh` runs `setup.sh` again for something else, such as `--review`
asked for the first time; `./setup.sh` on its own goes back to the shipped
one, and a newer checkout re-pins it like any other.

The golden question set is the other thing a new checkout can bring. The
context store and the vectors are images, published holding the pairs the
document held then, and a promotion is the only thing that reloads them -- so
pairs promoted on another machine and committed arrive in the document and
not in the stores. `launch.sh` compares the two on every start and says so;
`--load-golden` loads the document before anything is asked, with the two
loaders a promotion runs, in the review service's image:

```bash
git pull
./start.sh --load-golden
```

Both loaders are idempotent, and the embedding step embeds only pairs that
changed, so with nothing new in the document a load changes nothing and costs
a few seconds -- `--load-golden` is safe to leave on every start.

Without the re-pin, an older `.env` brings up an agent that predates what the
checkout expects of it -- no feedback routes before `v4_4`, so the review
interface sits at an empty queue forever, no model routing before `v5_2`,
no way to take a review back before `v5_4`, nothing traced before `v5_5`,
no snippets before `v5_6` -- a review image from before it cannot load them,
and `launch.sh` says so -- and no SQL console before `v5_3`, whose container
then stops, saying the module is missing, which `launch.sh --console` passes
on.

Going to `v5_6` brings up one new container, `nl2sql-snippetsdb`, on a new
empty volume. The first `launch.sh` finds its hash missing and loads
[`context_questions/sql_snippets.md`](context_questions/sql_snippets.md) into
it, in the review service's image; from then on it loads again only when the
document changes -- a `git pull` that brings snippets, or a save in the
curation interface.

Going from `v5` to `v5_1` or later, the same two commands also bring up two new
containers, `nl2sql-correctionsdb` and `nl2sql-completionsdb`, each on a new
empty volume. The review service creates their schemas on its first start and
widens the staging table to the new `corrected` state, so nothing is migrated
by hand and every verdict already staged is still there, in its pane.

To publish new ones, build both architectures in the same step so the tags
stay multi-arch, as every earlier tag is:

```bash
docker login
docker buildx build --platform linux/amd64,linux/arm64 \
  -f agent/Dockerfile --push -t mcfaddja/nl2sql-agent:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f gui/Dockerfile --push -t mcfaddja/nl2sql-gui:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f review/Dockerfile --push -t mcfaddja/nl2sql-review:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f review/gui/Dockerfile --push -t mcfaddja/nl2sql-review-gui:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f curate/Dockerfile --push -t mcfaddja/nl2sql-curate-gui:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f console/Dockerfile --push -t mcfaddja/nl2sql-console-gui:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/mlflow/Dockerfile --push -t mcfaddja/nl2sql-mlflow:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/mlflowdb/Dockerfile --push -t mcfaddja/nl2sql-mlflowdb:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/mlflow-proxy/Dockerfile --push -t mcfaddja/nl2sql-mlflow-proxy:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f ldap/Dockerfile --push -t mcfaddja/nl2sql-ldap:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f auth/Dockerfile --push -t mcfaddja/nl2sql-auth:v6_1 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f auth/gui/Dockerfile --push -t mcfaddja/nl2sql-directory-gui:v6_1 .
```

The desktop client is published along a second axis as well. Every tag is
multi-architecture like the ones above -- that is the machine the image
*runs* on, to copy the jar out -- but the jar inside carries native code for
one JavaFX platform, so there is a tag per platform:

```bash
for platform in mac-aarch64 mac linux linux-aarch64 win; do
  docker buildx build --platform linux/amd64,linux/arm64 \
    -f desktop/Dockerfile --build-arg JAVAFX_PLATFORM=$platform \
    --push -t mcfaddja/nl2sql-desktop-build:v6_1-$platform .
done
```

The builder stage is pinned to `$BUILDPLATFORM` because its output is the
same bytes whatever it runs on; the stage that ships is not, because that is
the one a manifest needs a variant of.

A published tag does not move. A correction to something already published
is a new patch version and a new tag -- `v5_1_1` for the escaping fix, then
`v5_1_2` -- rather than a re-push of `v5_1`, because a tag that changes under
somebody is the one kind of breakage they cannot debug from their own
checkout. The tag is the
version with its dots turned into underscores, truncated to however many
components the tag carries: `v5_1_2` is exactly 5.1.2, `v5_1` is 5.1.x and
`v5` is 5.x, and
[`tests/docs/test_versions.py`](tests/docs/test_versions.py) holds the
thirty-two places that say so to the same number.

The database images are not in that list. `mcfaddja/nl2sql-retail-postgres`
(`v1_2`) and the two RAG stores (`v3_2`) version independently, because their
*content* changes independently of the code. All three are multi-arch, and
[`tests/docker/test_published_images.py`](tests/docker/test_published_images.py)
asks the registry so. The RAG stores used not to be: they are published by
[`rag/publish_db_image.sh`](rag/publish_db_image.sh), which used to tar a
stopped container's data directory -- one machine's, so `v3` is arm64 only. It
now dumps the store and restores the dump inside the image build, once per
platform; `v3_1` is `v3` republished that way, checked table by table and
nearest neighbour by nearest neighbour against it on both architectures.
`v3_2` is `v3_1` with the golden set brought up to the question document --
48 pairs where `v3_1` has 45 -- loaded by the two loaders a promotion runs,
in the review image; every knowledge table is unchanged, checked the same
way. A store that falls behind the document again is what
`./start.sh --load-golden` catches up on start.

The agent's version label comes from `AGENT_VERSION` in
[`agent/Dockerfile`](agent/Dockerfile) and the GUI's from
[`gui/package.json`](gui/package.json); tests pin both to
`nl2sql_agent.__version__`, and pin the two published tags to each other, so
none of them can drift. They are built from one checkout and only ever tested
together, so "which GUI goes with which API" should not be a question anyone
has to ask.

What each version changed is in
[`CHANGELOG_SIMPLE.md`](CHANGELOG_SIMPLE.md), one line per change, and
[`CHANGELOG.md`](CHANGELOG.md), every artifact each version created, updated or
fixed -- both back to v1, including the versions that published no tag.

| Tag | Use |
|---|---|
| `v6_1` | Hardening, from the second adversarial review (`v6_1_review`). The databases answer on this machine only unless `DB_BIND_ADDRESS` says otherwise, and `setup.sh` generates every store's password; the retail database is `v1_2`, with no password baked in, TLS on, and sign-in accepted only over TLS, verified by the auth service. Every service has its own certificate, issued by a development CA that a new one-shot service, `pki`, keeps; a client trusts `nl2sql-ca.crt` once. Sign-in is on in the code as well as in compose, CORS is closed until opened, the job queue is bounded and answers 429, `EXPLAIN` is time-boxed, tokens are scrubbed from the access log, and the directory's API answers on its own port, behind its page. A repair starts from a clean attempt, a narrator or supervisor that fails says so in `node_errors`, every wire model refuses a field it does not know, and each trace entry carries its model, rung, route and hops. `SECURITY.md` is the threat model; `Multi-Agent_NL2SQL_arch6.md` the architecture as built. Pinned -- what `setup.sh` pulls. |
| `v6_0_1` | A correction to `v6_0`, found by running the published stack under compose. The directory starts as root just long enough to take its certificate's volume -- which compose creates owned by root when the auth service's container is made after the directory's -- and then runs as `ldap`; under `v6_0` it could not write its certificate, restarted for ever, and nobody could sign in. The sign-in throttle lets an address fail fifty times rather than five, since everyone behind one proxy or one machine's NAT is one address. The desktop client offers to sign in when `/v1/meta` asks who it is, instead of saying it is not connected. The other images are `v6_0`'s under the new version. Pinned. |
| `v6_0` | Sign-in, on by default. A person signs in with the password a directory holds, which the retail database checks itself, and the groups they are in decide what they may open; what they ask or run, runs as their own database role. Four new images: `nl2sql-ldap`, the directory -- standalone, or a read-only replica of Active Directory or any LDAP server -- `nl2sql-auth`, which signs people in and keeps the database's roles in step with the directory, `nl2sql-directory-gui`, its page, and `nl2sql-mlflow-proxy`, MLflow's front door. Every interface is HTTPS and asks who you are; the desktop client signs in too. `--no-auth` turns it off. Pinned. |
| `v5_6_1` | A correction to `v5_6`: a claim the narrator cited only in its own sentence ("as shown in row 0, column ...") is cited by that address, so it survives the audit on the first pass rather than costing a rewrite, or the whole narrative when the rewrite failed too. The other images are `v5_6`'s under the new version. Pinned. |
| `v5_6` | SQL snippets: a fifth retriever shows the SQL Generator verified joins, filters, measures and dimensions whose meaning matches the question and whose tables are in scope, from a store loaded out of `context_questions/sql_snippets.md`. A new image, `nl2sql-curate-gui`, is the curation interface: snippets, golden pairs, corrections and completions written or removed directly, each run against the retail database first. The review service carries the snippet loader and the curation routes, and runs a golden pair's SQL before promoting it. The audit no longer drops a correct claim whose sentence named the cell it came from. Pinned. |
| `v5_5_1` | A correction to `v5_5`. The golden set is no longer capped at `Q99`: a pair id is `Q` and two or more digits, so the hundredth promotion is `Q100`, and the review service's `/v1/meta` no longer reports the `max_pair_number` that described the cap. The agent's `--help` gives each retrieval switch's real default (it said `--multi-shot` was off). `start.sh` keeps an agent tag chosen with `setup.sh --agent-tag` rather than re-pinning it. The web interface, the SQL console's interface, the desktop client and MLflow's two are `v5_5`'s under the new version. Pinned. |
| `v5_5` | The agent traces every question into MLflow -- one trace per question, a span per agent with what it read and wrote, and every model call inside it with its messages, its answer and the route that chose it -- when `MLFLOW_TRACKING_URI` names a server that answers; `--mlflow` starts one. A verdict given on an answer is recorded on its trace, and the benchmark files each configuration as an MLflow run. MLflow's server and store are published with the release from here on, as `nl2sql-mlflow` and `nl2sql-mlflowdb`. Of the rest only the agent image changed; the others are `v5_4`'s under a new version. Pinned. |
| `v5_4` | A judgement in the review interface can be taken back: a submission is put back to pending or deleted, and a promoted pair comes back out of the golden set, or a stored fix out of its store, with it. The agent and the console are `v5_3`'s. Pinned. |
| `v5_3` | Adds the SQL console: the same image run as `python -m nl2sql_agent.console`, which queries the retail database as the agent's read-only role, under its timeout and plan-cost ceiling, through its validator and planner gate -- and says which gate would have refused a query, in that gate's words -- with an interface of its own, `nl2sql-console-gui`. The pipeline is `v5_2`'s. Pinned. |
| `v5_2` | arch5.2: every model call is routed -- by its task and the complexity of the question -- to the fastest model on the Ollama host that calibration measured to be suited to it, from a catalog built by `models/build_catalog.py` and measured by `models/calibrate.py`. Repairs climb the ladder, a routed model that cannot answer falls back to `OLLAMA_MODEL`, every call is capped in tokens and time, and `/v1/meta` and the trace say which model answered. With no catalog for its host it behaves as `v5_1_2`. Pinned. |
| `v5_1_2` | `v5_1_1` with code nothing reached taken out -- `Database.explain`, which only its own tests called, and five guards that could never be false -- found when branch coverage was switched on and gated at 100%. No behaviour changes. Pinned. |
| `v5_1_1` | A fix to `v5_1`: the narrator was shown HTML-escaped rows and copied the entities into its claims, so the CLI printed `Meat &amp; Seafood` and the markdown answer carried `&amp;amp;`. The narrator now reads the rows as the database has them, and the answer is escaped once, on the way out. Pinned. |
| `v5_1` | arch5.1: the review service handles each verdict its own way -- correct answers promoted into the golden set, wrong and correct-but-incomplete ones fixed, validated against the live retail database, and stored in the corrections and completions stores. Pinned. |
| `v5` | arch5: the answer contract and the Completeness Reviewer, a seven-generation retry budget, and a third verdict -- correct but incomplete -- in the web and desktop clients and the review queue. Pinned. |
| `v4_5` | Adds the Java desktop client, and the two request limits in `/v1/meta` it needed. Pinned. |
| `v4_4` | Adds the feedback system: verdicts staged from the web interface, and the review service and interface that promote them into the golden questions. Pinned. |
| `v4_2` | The multi-agent pipeline, the REST API, and the web interface. Pinned. |
| `v4_1` | The same pipeline and REST API, before the GUI. Pinned. |
| `v4` | The multi-agent pipeline, CLI only. Pinned; `./launch.sh --api` cannot run against it, and says so. |
| `v3` | RAG plus the golden-pair ensemble, one linear graph. Pinned. |
| `v2` | Retrieval over the knowledge base only. Pinned. |
| `v1` | The original schema-only agent, before retrieval. Pinned. |
| `latest` | Moves to the newest publish (currently the same image as `v2`). |

Each is a genuinely different image rather than the newest one with switches
turned off -- `v1` has no `retrieval` module and no `--rag` flags; `v2` has no
`examples` module and no `--multi-shot`:

```bash
./setup.sh --agent-tag v2                 # the v2 stack
./setup.sh --agent-tag v1 --no-rag        # the v1 stack
```

`v2` also reads the v3 databases quite happily: it finds its knowledge
collections by name and never looks at the golden-pair tables.

`v4` needs one thing the earlier images did not: a `nl2sql_reader` role, and
`pg_trgm` if literal matching is to use trigram search rather than falling
back to `difflib`. Both are created by `setup.sh` and `launch.sh` on every
start, so a `v1` retail image works with the `v4` agent.

`v1` is what the comparison below is measured against, and it is a genuinely
different image rather than `v2` with retrieval switched off -- it has no
`--rag` flags and no `retrieval` module at all:

```bash
docker pull mcfaddja/nl2sql-agent:v1
./setup.sh --agent-tag v1 --no-rag      # set the stack up against it
```

The two retrieval databases are separate images, started for you by compose:

```bash
docker pull mcfaddja/nl2sql-rag-vectordb:v3_2    # pgvector: knowledge + golden-pair vectors
docker pull mcfaddja/nl2sql-rag-chunkdb:v3_2     # context store: golden pairs + BM25 statistics
```

| Tag | Holds |
|---|---|
| `nl2sql-rag-vectordb:v3_2` | The 53 knowledge chunks as in `v1`, plus `golden_pair_question_vectors` and `golden_pair_reasoning_vectors` -- 48 rows each, the golden set as the question document held it on 2026-10-01. `linux/amd64` and `linux/arm64`; what `setup.sh` pulls |
| `nl2sql-rag-chunkdb:v3_2` | `golden_pairs` (48 rows, 8 content columns) plus the BM25 term statistics and the `golden_pairs_bm25()` ranking function. `linux/amd64` and `linux/arm64`; what `setup.sh` pulls |
| `nl2sql-rag-vectordb:v3_1`, `nl2sql-rag-chunkdb:v3_1` | The same, with the first 45 pairs. Pinned, and superseded by `v3_2` |
| `nl2sql-rag-vectordb:v3`, `nl2sql-rag-chunkdb:v3` | The same contents as `v3_1`, arm64 only. Pinned, and superseded by `v3_1` |
| `nl2sql-rag-vectordb:v1` | Knowledge collections only -- what v2 searches |

## Sign-in

```bash
./start.sh                       # sign-in is on: every page asks who you are
grep LDAP_ADMIN_PASSWORD .env    # the first person, admin
open https://localhost:8084      # add everyone else
```

Who may use what is decided by the retail database and a directory beside
it. A person signs in with the user name and password the directory holds;
**Postgres checks that password itself** -- pg_hba's `ldap` method, which
`launch.sh` writes -- and the groups they are in become the database roles
they hold. Every page, the desktop client, the REST API, the SQL console,
the review service and MLflow then believe the session that sign-in issued,
and what a person asks or runs, runs as their own database role.

| Group | Lets in |
|---|---|
| `nl2sql-users` | the web interface, the desktop client, the API |
| `nl2sql-reviewers` | the review interface, the SQL console, MLflow |
| `nl2sql-curators` | the curation interface, the SQL console |
| `nl2sql-admins` | the directory page, MLflow |

Everyone in a group can ask questions. Three containers make it work, all
started with the API:

| Container | Port | |
|---|---|---|
| `nl2sql-ldap` | none | the directory: OpenLDAP, **standalone** -- people loaded from a file on its first start and edited on the directory page -- or a **read-only replica** of another directory, Active Directory or any LDAP server, copied on an interval with every password passed through to it. See [`ldap/README.md`](ldap/README.md) |
| `nl2sql-auth` | 8446 | signs people in, issues the session, keeps the database's roles in step with the directory, and answers the directory page and MLflow's front door. See [`auth/README.md`](auth/README.md) |
| `nl2sql-directory-gui` | 8084 | the directory page, for `nl2sql-admins`; standalone only |

Every page is HTTPS, so a password is never typed into a page served in
clear, and since 6.1 every server has a certificate of its own: a one-shot
container, `nl2sql-pki`, keeps a development CA for the stack and issues
each one a key that no other container holds. A browser warns until this
machine is told to trust that CA, once, for every page --
`docker compose --profile api cp api:/etc/nl2sql/tls/ca.crt
./nl2sql-ca.crt`, then add it to the system's trust store (Keychain Access
on macOS, `update-ca-certificates` on Debian and Ubuntu) -- or until real
certificates are mounted. `TLS_EXTRA_HOSTNAMES` adds this machine's name to
every certificate, for a browser elsewhere. Behind something that terminates
TLS itself, `GUI_TLS_ENABLED=false` makes every page plain again; the
terminator must send `X-Forwarded-Proto: https`, which the pages pass on so
the session cookie stays `Secure`.

A person's password reaches the database only over TLS: the retail
database serves TLS and refuses everything over the network without it,
and the auth service verifies its certificate (`verify-full`). The
directory's own API -- the routes that make people -- answers on the auth
service's port 8447, which is not published: only the directory page
reaches it. [`SECURITY.md`](SECURITY.md) says what each of these protects,
from whom, and what is still open.

Off with `--no-auth` for one run of `start.sh` or `launch.sh`, or
`./setup.sh --no-auth` -- `AUTH_ENABLED=false` in `.env` -- for good: no
directory, no auth service, and every page and port as it was before, open
to whoever can reach it unless a static token is set.

The directory GUI: 54 tests, at 100% of statements, branches, functions and
lines.

## The web interface

```bash
./start.sh
```

A React and TypeScript front end, built to static files and served by nginx.
Ask a question, watch the pipeline work through it, read the answer with its
chart and its rows, and say whether it was right.

It imports nothing from the agent. It speaks the same JSON over HTTPS that
any other client would, which is the point: it is a demonstration that the
API is framework-agnostic rather than a privileged special case. Everything
it does, the four snippets in [`agent/API.md`](agent/API.md) do too.

**The minute a question takes is filled with what the agent is doing.** The
progress stream carries the graph's own node names -- screening the question,
matching literals, reading the schema, writing SQL, checking the plan,
running it, narrating, auditing -- so the user watches work rather than an
animation, and the nodes still to come are listed greyed because `/v1/meta`
says which ones this server runs.

**The answer arrives with its working.** The sentence, then the chart the
Visual Formatter asked for, then the rows. Folded away beneath: the SQL and
how many attempts it took, the phrases matched to database values, and how
long each node took. Hovering a claim highlights the exact cells it was read
from -- the audit ties every sentence to the rows that support it, and this
is the clearest thing that makes possible.

**A refusal is rendered as a refusal.** `verdict` is not `proceed` for an
out-of-scope or unsafe question, and there is no table because no query ran;
showing an empty grid would report a failure that did not happen. An
ambiguous question comes back with a clarification, which is a question for
the user, so it goes where the answer would.

**Feedback is three buttons.** Correct, wrong, or correct but incomplete, per
answer, shown back in the answer and in the session list. The third is for an
answer whose SQL was right and which still left out something a reader
needed -- a name beside an id, the figure a ranking was ranked by -- because
that calls for fleshing out rather than correcting, and a plain "no" cannot
say which. With the staging database up (`--feedback` or
`--review`) the verdict is also sent to the API and waits to be reviewed; on
a server without one it is kept in the browser and says so, because a button
whose every click fails is worse than no button. See [Feedback](#feedback).

The container verifies the API's certificate against the stack's CA, so the
browser never has to,
and with sign-in off it holds the API token as well; with sign-in on, the
session the page signed in with is what reaches the API, and no token is
added. Neither is a requirement of the API -- it answers
a browser directly when `API_CORS_ORIGINS` names the origin (none does by
default) -- but an untrusted certificate blocks `EventSource` with no
warning to click, and
[`gui/README.md`](gui/README.md) explains the three problems one same-origin
hop removes.

`start.sh` waits until the page actually answers before opening it. That is
not the same as waiting for the container to call itself healthy: nginx
reports healthy as soon as it is up, which is a moment before it has read the
configuration written for it at start-up, and a browser opened on the health
check alone lands on a connection error often enough to matter. On a machine
with no desktop it prints the URL and carries on, which is not a failure --
`--no-browser` asks for that deliberately, and `BROWSER` picks what opens it.

For development against a running API:

```bash
./launch.sh --api
cd gui && npm install && npm run dev      # http://localhost:5173
```

## The desktop client

```bash
./start.sh --desktop
```

A JavaFX application in a window on this machine, rather than a page in a
browser. The same questions, the same progress stream, the same charts and
the same three-way verdict -- correct, wrong, correct but incomplete -- as the web interface --
[`desktop/README.md`](desktop/README.md) is the whole of it.

**It exists because a contract only one implementation has ever met is a
contract nobody has checked.** [`agent/API.md`](agent/API.md) claims the
interface is framework-agnostic, and until this was written the only things
that had ever read it were a React application and a curl script. Writing the
second client found something immediately: `/v1/meta` published the limits
that describe the *answer* and not the two that describe the *request*,
because a browser discovers those from a 422 in its network tab and a desktop
application shows the user whatever it was handed. Both are now in `limits`,
and this client reads them before it will let a question be sent.

**Verdicts take the same route as the web interface's.** The same endpoint,
the same staging table, the same row-level security fence, the same review
queue. A reviewer sees one queue because there is only one -- not because two
writers were made to agree -- which is what the API's design buys: the client
sends an opinion and nothing else, and the server reads the snapshot off the
job it still has. There is no Java half of the review interface and there
should not be: the thing that can rewrite the golden question set is one
service with one token.

**It has to decide for itself whether to believe the server**, which a
browser never does here. The web interface is served by the nginx that
proxies the API, so it talks to its own origin and the proxy holds both the
token and the trust decision. This one opens the connection itself, so
`./launch.sh --desktop` copies the stack's CA certificate out and the client
is run with `--cacert`, which covers the API and the sign-in service alike.
`--fingerprint` -- both servers', comma-separated -- and `--insecure` are
the other two answers, and the status bar says which of them is in force
for as long as it is true.

**Docker fetches it; Java runs it.** Nothing runs a desktop application in a
container, so what the `desktop` image does is *carry* a jar -- and that keeps
the promise the rest of this repository makes, that Docker is the only thing
anyone has to install. Running it needs a Java runtime of 21 or later and
nothing else; JavaFX is inside the jar.

**The window outlives the command that opened it**, which took two things
rather than one. `nohup` is what survives the terminal being closed
afterwards; the subshell it is started in is what survives `start.sh` itself
exiting, because a process backgrounded directly is a job of that shell and
is reaped with its process group moments later. `start.sh` also waits a beat
and checks the window is still there before it claims to have opened one --
the same promise its browser half makes by waiting for the page to answer --
and prints what the client said if it stopped. Run it again and it says the
client is already open rather than putting a second window onto the same
API.

The jar is built in a Linux container for a machine that is not the
container, so `launch.sh` reads `uname`, pulls the tag for what it finds, and
builds locally only when there is nothing to pull:

```bash
./launch.sh --desktop        # fetch it and copy the CA certificate out
java -jar desktop/target/nl2sql-desktop.jar --cacert ./nl2sql-ca.crt
```

One jar is one platform. The same native library file names are used on macOS
x86-64 and arm64, so a jar carrying both would carry one of them twice under
one name and load whichever came first. That is why there is a published tag
per platform --

| Tag | For |
|---|---|
| `mcfaddja/nl2sql-desktop-build:v6_1-mac-aarch64` | Apple silicon |
| `mcfaddja/nl2sql-desktop-build:v6_1-mac` | Intel Macs |
| `mcfaddja/nl2sql-desktop-build:v6_1-linux` | x86-64 Linux |
| `mcfaddja/nl2sql-desktop-build:v6_1-linux-aarch64` | arm64 Linux |
| `mcfaddja/nl2sql-desktop-build:v6_1-win` | Windows |

-- and why `launch.sh` records which platform the jar beside it was built
for, and fetches again when that or a source file changes.

## Feedback

The web interface and the desktop client both ask whether an answer was
right. This is where those answers go.

```bash
./start.sh --review
```

That is the whole thing: databases, the API, the web interface, the staging
database, the corrections and completions stores, the review service and the
review interface -- and both pages opened in your browser, the review page in
a window of its own. [`./launch.sh --review`](launch.sh) is the same
containers without the browser step.

Without it, a verdict stays in the browser and nothing is lost -- the buttons
still work, the verdict is still shown, and `/v1/meta` tells the page not to
claim it was sent anywhere. With it, a verdict is staged and reviewed, and
where it goes depends on what it said:

| Verdict | Review pane | Where it ends up |
|---|---|---|
| **Correct** | Correct → golden set | Promoted into the golden question set, as before |
| **Wrong** | Wrong → corrections | A corrected query, validated against the live database, in `nl2sql-correctionsdb` |
| **Correct but incomplete** | Correct but incomplete → completions | A completed query, validated the same way, in `nl2sql-completionsdb` |

### Why bother

The golden pairs in
[`context_questions/translated_questions.md`](context_questions/translated_questions.md)
are the best-understood thing in this repository. The agent retrieves worked
examples from them, the benchmark scores against them, and every question
they *don't* cover is a gap that only shows up as an answer somebody
disagrees with. A *wrong* or *correct but incomplete* verdict in either client
is the cheapest possible report of such a gap. A *correct* one is a new pair
waiting to be written; the other two are a mistake and its fix, which is a
different thing and is kept apart from the set the agent is measured
against.

### What happens to a verdict

| | |
|---|---|
| **Captured** | With a snapshot of the job -- question, SQL, answer, result shape, comment. Taken at vote time, because a job is forgotten after an hour and a verdict pointing at a forgotten job is not reviewable |
| **Staged** | In `nl2sql-feedbackdb`, its own Postgres, in its own volume. Not the retail database, which is the subject under test, and not the RAG stores, which ship their data inside published images |
| **Reviewed** | In a second web interface, one pane per verdict: what was asked, what the agent answered and the SQL it wrote, what the user thought -- and beside it a form for building a golden pair, or an editor for the query that should have been generated |
| **Promoted** *(correct)* | Appended to the question document *in this checkout*, then loaded into the context store and embedded into the vector store |
| **Fixed** *(wrong, incomplete)* | The reviewer's query is run against the live retail database, read-only, as the agent's own role; only one that runs can be saved. The question, the incorrect answer and the corrected query go into that verdict's own store, with an embedding of the question beside them for retrieval |

### The three fields nobody can guess

A vote gives you a question and some SQL. A golden pair also needs
`keywords`, a `reasoning_target` and a `result` -- which words someone would
search for, where generated SQL typically goes wrong on this question, and
what coming back looks like. Those are judgements about what the question
*tests*, and the review form leaves them empty rather than guessing: a
reviewer skimming a pre-filled form approves it, and the BM25 index fills up
with keywords nobody chose.

### Promotion writes a file you commit

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

### A judgement can be taken back

Every submission in the review interface can be put back to pending, or
deleted. When it had already been acted on, what it produced comes out with
it: a promoted pair is taken back out of the question document -- the
previous version kept as `.bak`, both stores reloaded -- and a fix is deleted
from its store with its vector. The queue and the golden set never disagree,
so a reopened question cannot end up in the set twice. A reopened submission
gets its work back: the pair as its draft, or the corrected SQL in the query
editor. Anything that reaches past the staging table asks first, saying what
it is about to change. [`review/README.md`](review/README.md#changing-your-mind)
has the rules.

### Only SQL that runs is stored

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

Corrections and completions each get their own Postgres -- records and RAG
side by side, on ports 5436 and 5437 -- because they are different lessons,
and neither is a golden pair. The curation interface writes
them directly too, without a submission. Nothing reads them yet; they are what a later
agent will retrieve from ("a question like this one was answered wrongly
before, and this is what fixed it"), and a later version adds an agent to
sanity-check a reviewer's query and another to help write it.

### Who can do what

The internet-facing process can add a verdict and nothing else. It connects
to the staging database as a role that cannot read a submission back, cannot
change a review, and cannot see any row a curator has already judged -- by a
row-level security policy, not by the SQL in the API being careful. The
powers that matter belong to a separate service, on a separate port, and it
is the only client of the two fix stores. With sign-in on, only
`nl2sql-reviewers` work its queue and only `nl2sql-curators` write to it
directly, each under their own name and with the SQL they validate run as
their own database role; with it off, it is behind a token of its own.

[`review/README.md`](review/README.md) has the whole of it.

## SQL snippets and curation

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
[`context_questions/sql_snippets.md`](context_questions/sql_snippets.md),
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
`nl2sql-snippetsdb`, by [`rag/07_load_snippets.py`](rag/07_load_snippets.py):
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

The store reads these from `.env`, like the rest of the stack:

| Setting | Default | |
|---|---|---|
| `SNIPPETS_DB_PORT` | `5438` | The host port of the snippet store |
| `DB_BIND_ADDRESS` | `127.0.0.1` | The address that port is published on -- this machine's, as for every store (6.1) |
| `SNIPPETS_DB_USER`, `SNIPPETS_DB_PASSWORD`, `SNIPPETS_DB_NAME` | `snippets`, `snippets`, `nl2sql_snippets` | The store's owner and database: what the store is created with, and what the review service loads it as. `setup.sh` generates the password into `.env`, and `launch.sh` tells a store made before 6.1 |
| `SNIPPETS_READER_USER`, `SNIPPETS_READER_PASSWORD` | `snippets_reader` | The read-only role the loader creates and the agent reads as; the password generated the same way |
| `SNIPPETS_IMAGE` | `pgvector/pgvector:pg18` | The store's image: stock pgvector, since what it holds comes from the document |

Snippets and golden pairs are written into the documents in this checkout, so
they show up in `git diff` and are committed by a person. Fixes are rows in
their stores, as they are when a review produces them.
[`curate/README.md`](curate/README.md) has the interface, and
[`review/README.md`](review/README.md#curation) the routes behind it.

## The SQL console

```bash
./start.sh --console
```

When the agent answers a question wrongly, the questions worth asking next
are about the database it read and the gates in front of it -- would the
validator have passed this query, what did the planner estimate, did it
finish inside the timeout, what do the rows actually say -- and each has an
exact answer that only running SQL can give. The SQL console is where that
SQL is run, at <https://localhost:8082>, as the agent would run it: as
`nl2sql_reader`, inside a read-only transaction, under the agent's statement
timeout, through the agent's own static validator and planner gate.

**Beside every result, what the agent would have done with it.** Which gate
would have refused the query, in that gate's own words -- the message its
Repair Agent would have been handed -- and the estimated cost against the
`MAX_PLAN_COST` ceiling. A result longer than the agent reads says so,
because the agent would have seen the first 50 rows and been told the rest
were cut off.

**Three ways to run it.** *Run* returns the rows, each column headed by its
type, with NULL shown as NULL. *Plan* stops at `EXPLAIN`, which is exactly
the agent's planner gate. *Analyze* runs the query to time it, and shows what
really happened beside each of the planner's estimates.

**The schema is the agent's.** The browser on the left is the introspection
the agent's prompt is built from, comments and keys included, and *Agent's
view* on a table shows the block of the prompt that describes it, sample rows
and all -- when the agent picked the wrong column, that text is usually why.

It runs from the agent's image, `python -m nl2sql_agent.console`, so every
one of those answers is the agent's code rather than a copy of it; and it is
its own process, on its own port, behind its own token, because the API runs
SQL the pipeline wrote and this runs SQL a person typed. Both of its ports
are published on this machine only unless `CONSOLE_BIND_ADDRESS` says
otherwise, and the database refuses what the validator does not catch:
`SELECT lo_create(0)` passes the one and is stopped by the other.

[`console/README.md`](console/README.md) has the whole of it.


## Tracing

```bash
./start.sh --mlflow
```

The agent's own trace -- the list of nodes, their timings and the model each
call went to -- says *what* happened to a question. MLflow, at
<https://localhost:5001>, shows *why*: every question is a trace, every agent
in it a span holding the part of the state it read and the part it wrote
back, and every model call inside its agent a span of its own, with the
messages it was sent, the answer it gave, the tokens that cost (where the
call reports them: a structured call does not), and the task, rung and route
the Model Router chose it by.

```
nl2sql                      AGENT       the question in, the answer out
  Supervisor                AGENT
    qwen3.8-256k:latest     CHAT_MODEL  the screening call
  Schema Retriever          RETRIEVER   the five retrievers, which ran
  Literal Matcher           RETRIEVER   concurrently, under the run that
  Knowledge Retriever       RETRIEVER   asked for them
  Example Retriever         RETRIEVER
  Snippet Retriever         RETRIEVER
  Context Aggregator        TASK
  SQL Generator             AGENT
    <model>                 CHAT_MODEL  a routed call that fell back is two
  Static Validator          GUARDRAIL   spans, the first marked failed
  Planner Gate              GUARDRAIL
  Safe Executor             TOOL
  Completeness Reviewer     EVALUATOR
  ...
```

A repair is a second *SQL Generator* in the same trace, after a *Repair
Agent*, so the attempts the answer took are read top to bottom. Each trace is
tagged with how the run ended (`answered`, `gave_up`, `refused`), the
Supervisor's screening, the attempts, the model calls, the agent's version
and -- from the REST API -- the job, so the trace of any job is a filter away:
``tags.`nl2sql.job_id` = '<id>'``.

**A verdict lands on the trace it judges.** *Correct*, *Wrong* or *Correct
but incomplete*, given in the web or desktop interface, is staged for review
as before and also recorded on that answer's trace as human feedback; voting
again replaces it, with the earlier one kept as its history, and withdrawing
it takes it off. The staged verdict is the one a reviewer acts on: MLflow not
taking it costs the trace its verdict and the vote nothing.

**The benchmark is a run per configuration.** With MLflow up,
`python benchmarks/run_benchmark.py` files each configuration it measures as
an MLflow run -- its settings as parameters, accuracy and timings as metrics,
the report as an artifact -- and every question's trace inside it, tagged with
the question and judged right or wrong by execution. See
[`benchmarks/README.md`](benchmarks/README.md#mlflow).

**It is best effort.** `setup.sh` writes `MLFLOW_TRACKING_URI` into `.env`,
pointing the agent at the `mlflow` service; whenever that service is up every
run is traced, and when it is not the agent answers untraced and asks again
thirty seconds later, so MLflow can be started or stopped under a running
API. `MLFLOW_TRACKING_URI=` (empty) turns tracing off outright, and survives
`setup.sh` being run again. The server is MLflow's own image, pinned to the
version of the tracing client in the agent image and published with the
release (`nl2sql-mlflow`), with a Postgres of its own behind it
(`nl2sql-mlflowdb`, whose port is not published).

**It has a front door.** MLflow's interface has no login of its own and shows
the rows every question returned, so the server publishes nothing: a browser
-- and the benchmark -- reach it through `nl2sql-mlflow-proxy`, nginx over
HTTPS with a certificate of its own, which asks the auth service about every
request. Someone not signed in is sent to sign in; someone signed in who is
not in `nl2sql-reviewers` or `nl2sql-admins` (`MLFLOW_ALLOWED_ROLES`) is
turned away; an MLflow client signs in with `MLFLOW_TRACKING_USERNAME` and
`MLFLOW_TRACKING_PASSWORD`. The agent traces to the server directly, on the
stack's own network. It is published on this machine only unless
`MLFLOW_BIND_ADDRESS` says otherwise; with sign-in off `launch.sh` warns
when it is. See [Sign-in](#sign-in).

The server, its store and its front door read these from `.env`, like the
rest of the stack:

| Setting | Default | |
|---|---|---|
| `MLFLOW_PORT` | `5001` | The host port of its interface and API, through the front door. Not 5000, which macOS keeps for AirPlay |
| `MLFLOW_BIND_ADDRESS` | `127.0.0.1` | The address that port is published on |
| `MLFLOW_PROXY_PORT` | `5001` | The port the front door listens on inside its container |
| `MLFLOW_PROXY_UPSTREAM` | `http://nl2sql-mlflow:5000` | The server, on the stack's network. An `https://` one is verified against `MLFLOW_PROXY_CACERT`, as `MLFLOW_PROXY_SSL_NAME` |
| `MLFLOW_PROXY_SSL_NAME` | `nl2sql-mlflow` | Read only for an `https://` upstream |
| `MLFLOW_PROXY_CACERT` | `/etc/nl2sql/tls/ca.crt` | Read only for an `https://` upstream |
| `MLFLOW_PROXY_READ_TIMEOUT` | `300s` | How long a request may take, through it |
| `MLFLOW_PROXY_RESOLVER` | `127.0.0.11` | Docker's DNS, for the per-request lookup |
| `AUTH_ENABLED` | `true` | Ask the auth service about every request. Off, the front door lets anyone through |
| `GUI_AUTH_UPSTREAM` | `https://nl2sql-auth:8446` | The auth service, as every interface reaches it |
| `GUI_AUTH_SSL_NAME` | `nl2sql-auth` | |
| `GUI_AUTH_CACERT` | `/etc/nl2sql/tls/ca.crt` | The stack's CA, which the pki service puts beside every page's own certificate |
| `GUI_TLS_ENABLED` | `true` | HTTPS, with the front door's own certificate, as every interface is |
| `GUI_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` | |
| `GUI_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` | |
| `MLFLOW_WORKERS` | `2` | The server's worker processes |
| `MLFLOW_ALLOWED_HOSTS` | MLflow's own list, plus `nl2sql-mlflow` and `mlflow` | The `Host` headers it answers, against DNS rebinding. Setting it replaces the whole list, as MLflow's own flag does, so keep `nl2sql-mlflow:*` in it or the agent is turned away |
| `MLFLOW_DB_USER`, `MLFLOW_DB_PASSWORD`, `MLFLOW_DB_NAME` | `mlflow` | The store's role, password and database, each one setting that both containers read |

[`agent/README.md`](agent/README.md#tracing-mlflow) has the agent's two
settings and the reasons.


## Connecting a GUI

The agent also answers over HTTPS, so a front end can be written in anything.
There is no client library here and there is not meant to be one: the
interface is JSON over HTTP with an OpenAPI document the server generates
itself, and a TypeScript, Python, Java or Go client is generated from that
rather than written by hand.

```bash
./launch.sh --api
docker compose --profile api cp api:/etc/nl2sql/tls/ca.crt ./nl2sql-ca.crt

curl --cacert ./nl2sql-ca.crt https://localhost:8443/v1/meta
curl --cacert ./nl2sql-ca.crt -X POST 'https://localhost:8443/v1/questions?wait=180' \
     -H 'Content-Type: application/json' \
     -d '{"question": "How many stores are there?"}'
```

It is the *same image* as the agent, started as a server instead of a command
(`python -m nl2sql_agent.api`), so the pipeline answering a GUI is the
pipeline that was benchmarked.

**A question is a resource, not a request.** Answering takes about a minute,
which no GUI can hold a connection open for while showing nothing. `POST
/v1/questions` returns a job immediately; the client polls it, streams its
progress, or asks the server to hold the connection with `?wait=`. All three
return the same document, so waiting is an optimisation rather than a second
contract.

**Progress is the pipeline, not an animation.** `GET
/v1/questions/{id}/events` is a Server-Sent Event stream carrying the graph's
own nodes as they happen -- screening, schema, literals, SQL, the plan gate,
execution, the narrator, the audit -- and it resumes from `Last-Event-ID`
after a dropped connection.

**TLS is on by default.** Under compose the API's certificate is its own,
issued by the stack's development CA before it starts (the `pki` service),
and kept in a volume so a restart presents the same one; started on its
own, the server writes itself a self-signed one instead. Either is a
development convenience, and `API_TLS_ALLOW_SELF_SIGNED=false` takes both
away: the server then refuses to start behind a development certificate at
all -- self-signed or issued by the stack's CA -- so a deployment meant to
have a real chain fails at startup instead of quietly serving the throwaway
one.

Sign-in is on by default, in the server's own settings as well as in
compose. `API_TOKEN` adds a service token for scripts (`setup.sh --tokens`
generates one); `API_CORS_ORIGINS` names a browser origin allowed to call
the API directly, and none is by default. At most `API_MAX_QUEUED` questions
wait behind those running and a signed-in person has at most
`API_MAX_PER_PERSON` in the air; past either, `POST /v1/questions` is `429`
with a `Retry-After`.

To try the whole thing from outside, with no Python and no shared code:

```bash
docker compose --profile api run --rm apitest
```

`apitest` is an Alpine image holding curl and jq. It verifies the
certificate, walks every endpoint, streams a real question's progress, and
exits `1` on a failed check or `2` when the API was never reachable -- so CI
can tell a retry apart from a defect. It is also the shortest complete
reference for writing a client.

[`agent/API.md`](agent/API.md) is the contract: every endpoint, the response
shapes, the event stream, the error codes, the settings, and worked client
snippets for TypeScript/React, Python and Java. [`gui/`](gui) is a complete
client written against it, if a working example is more useful than a
snippet.

## Benchmark

```bash
python benchmarks/run_benchmark.py            # 15 questions, accuracy then speed
python benchmarks/run_benchmark.py --compare  # schema-only vs knowledge vs multi-shot
```

Since v5.2 the report also says which model answered each agent at each
rung, how many of the questions it touched came out right, its P50, and how
the generator's task was scored across the set -- read from the trace, so a
routed run is attributed per model and not only per agent.

Since v5.5, with MLflow up (`./launch.sh --mlflow`), each configuration is
also an MLflow run holding every question's trace -- see [Tracing](#tracing).

[`benchmarks/`](benchmarks) holds fifteen questions that are deliberately **not**
the golden pairs the agent retrieves from -- a benchmark drawn from those
would measure how well it can look something up. Accuracy is **execution
accuracy**: the SQL is run and its rows compared against reference SQL verified
against the shipped dataset. Query text is never compared, because two correct
queries for the same question rarely look alike.

### Measured

All three configurations, same 15 questions, `qwen3.8-256k` at 256k context:

| | accuracy | produced SQL | retries | total | median |
|---|---|---|---|---|---|
| `schema-only` (v1) | 12/15 (80%) | 14/15 | 5 | 511s | 21.0s |
| `knowledge` (v2) | **15/15 (100%)** | 15/15 | 0 | 1251s | 81.8s |
| `multi-shot` (v3) | **15/15 (100%)** | 15/15 | 0 | 1499s | 100.5s |
| multi-agent (v4) | **15/15 (100%)** | 15/15 | 3 | 910s | 61.2s |

| | analysis | calendar | fan-out | grain | schema |
|---|---|---|---|---|---|
| `schema-only` | 5/5 | 3/3 | **0/1** | **1/3** | 3/3 |
| `knowledge` | 5/5 | 3/3 | 1/1 | 3/3 | 3/3 |
| `multi-shot` | 5/5 | 3/3 | 1/1 | 3/3 | 3/3 |

Three things this says, including one that is not flattering:

**The knowledge base earns its place.** Every question schema-only missed is a
grain or fan-out question, and the knowledge base fixes all of them. It also
removes every retry: 5 down to 0.

**Multi-shot adds no measurable accuracy here, and costs 20% more time.** Both
retrieval configurations score 15/15, so this set cannot distinguish them --
once the knowledge base is on there is no headroom left to measure. That is a
limitation of a 15-question benchmark, not evidence that the examples do
nothing, but it is what the numbers say and they should not be read as more.
Telling the two apart needs harder questions than these.

**Accuracy costs roughly 3x the wall time**, and almost none of it is retrieval.
Across 15 questions the knowledge base and the golden-pair ensemble together
take **1.2 seconds**; the rest is the model:

```
  generate_sql            634s
  validate_sql            499s
  select_tables           364s
  retrieve_knowledge      1.1s
  retrieve_examples       0.1s
```

**v4 spends a third less time for the same answers**, by deleting the two model
calls that were not writing them. Validation became an AST parse and a plain
`EXPLAIN`; table selection became a vector search over the DDL chunks plus
foreign-key closure:

```
  v3  validate_sql   499.4s   ->   v4  validate_static + planner_gate   0.1s
  v3  select_tables  363.9s   ->   v4  retrieve_schema                  1.2s
```

It spends some of that back on a narrator, about a quarter of the run, which
the architecture did not anticipate. What it buys is a narrative whose every
number has been checked against a cell of the result rather than asserted, and
`NARRATE_ENABLED=false` removes it.

The retries are not a regression either: v3 had none because its LLM validator
passed everything it did not reject outright, while v4's planner catches three
real errors and repairs them without a model call.

**v5 holds 15/15 and adds one model call where it can matter.** The answer
contract costs nothing -- a label map and a fiscal calendar read from the
catalog once -- and the Completeness Reviewer's rules cost nothing either. Its
one reflective call ran on the three questions whose rows name an entity, 16s
in all, for a run of 991.6s against v4's 909.7s (and wall time against the
shared Ollama host varies by about 30% between identical runs). Getting there
took four runs, each of which changed the design: the contract never restates
a measure the question names, names no entity for a "how many" question, and
brings the calendar into scope for any period. [`agent/README.md`](agent/README.md)
has the details.

`select_tables` and `validate_sql` together cost more than generation itself.
Two model calls that do not write the answer take the majority of the time,
which is the obvious latency lever -- well ahead of anything in the RAG layer.

See [`benchmarks/README.md`](benchmarks/README.md) for the scoring rules and
what they do and do not forgive.

## Architecture diagrams

[`arch_diagrams/`](arch_diagrams) holds one diagram per agent version: what
each step does, why it is there, and how control flows.

| | |
|---|---|
| [`arch_v1.svg`](arch_diagrams/arch_v1.svg) | The schema-only pipeline |
| [`arch_v2.svg`](arch_diagrams/arch_v2.svg) | The same pipeline with retrieval in front of it, and that context threaded into three of the five steps |
| [`arch_v3.svg`](arch_diagrams/arch_v3.svg) | Both retrieval steps, the three-retriever ensemble behind the second, and the two data-flow rails they feed |
| [`arch_v4.svg`](arch_diagrams/arch_v4.svg) | The multi-agent pipeline: four stages, the parallel retrievers, the deterministic gates, the repair loop, and the presentation trio |
| [`arch_v5.svg`](arch_diagrams/arch_v5.svg) | v4 plus the answer contract and the Completeness Reviewer inside the repair loop |
| [`arch_v5_1.svg`](arch_diagrams/arch_v5_1.svg) | The v5 pipeline unchanged, with the review side added to the deployment: one pane per verdict, the golden set, and the corrections and completions stores |
| [`arch_v5_2.svg`](arch_diagrams/arch_v5_2.svg) | v5.1 with every model call routed: the catalog and the routed chat models in the deployment, the router and the ladder, and on each step that calls a model, the rung it is routed at |
| [`arch_v5_6.svg`](arch_diagrams/arch_v5_6.svg) | v5.2 with the Snippet Retriever as a fifth Stage 1 branch, the snippet store and its document in the deployment, and the curation interface beside the review one |

All of them are laid out identically so the versions can be read side by side --
everything new or changed is marked, in teal for v2's retrieval and indigo for
v3's examples. Each shows the deployment (what runs where), the startup
preflight, every LangGraph node paired with the reasoning behind it, the retry
loop, and the exit codes.

They are generated, not drawn:

```bash
python arch_diagrams/generate.py
```

[`generate.py`](arch_diagrams/generate.py) computes the layout -- text wrapped
against real font metrics, row heights following their content -- and records
the nodes it drew in the SVG, which is what lets
[`tests/docs/test_arch_diagrams.py`](tests/docs/test_arch_diagrams.py) check
the pictures against `graph.py` and fail when a node is renamed. Edit the
content in the `build_v*()` functions and re-run; do not hand-edit the SVGs.

The adversarial reviews in [`adversary_reviews/`](adversary_reviews) are
illustrated the same way:
[`adversary_reviews/diagrams/generate.py`](adversary_reviews/diagrams/generate.py)
writes their nineteen figures as draw.io files -- ten for the first cycle
(`v6_x_review`, at 5.6.1) and nine for the second (`v6_1_review`, at 6.0.1),
a first-cycle figure never edited for the second -- draw.io's own command
line exports the SVG and PNG beside each (the commands are in the script's
docstring), and
[`tests/docs/test_review_diagrams.py`](tests/docs/test_review_diagrams.py)
holds the committed files to the script, the exports to the files, the
`_enhanced` editions of the reviews to the originals they add figures to,
and the second cycle's comparison document to the finding ids both cycles
use.

## Model catalog

[`models/`](models) holds the list arch5.2's model router routes from: every
model the chat host serves, what the host and ollama.com say about it, and
the highest rung of each task -- light, standard or heavy -- it is suited to.

```bash
python3 models/build_catalog.py                  # the host .env points at -> models/catalog.json
python3 models/build_catalog.py 192.168.1.20     # any Ollama host, by its address alone
.venv/bin/python models/calibrate.py             # measure what each model is suited to
```

The scanner needs only the standard library, so it runs with the `python3` a
Mac already has; it says what each model *is* and guesses from that what it
is suited to. The calibrator runs each model through a probe per task
against the live stack and records what it *measured*, and only a measured
suitability is routed on. A full calibration of a host with dozens of models
takes hours, so it measures each set of identical builds once, stops probing
a generator that cannot be suited, survives any one model failing, and
resumes where it stopped. A catalog describes one host: the committed one is
ignored anywhere else, so build and calibrate your own. Re-run the scanner
when the host's models change -- it keeps the measurements of every model
whose weights have not -- and commit the result like code.
[`models/README.md`](models/README.md) has the rules, the probes and the
catalog's shape.

`launch.sh`, and so `start.sh`, asks the agent image on every start which
models it will route to, and says: how many models the calls are shared
between, or why every one goes to `OLLAMA_MODEL` -- the catalog describes
another host, nothing in it has been measured, or routing is off.

## Synthetic data generator

A synthetic dataset generator for a grocery retail data model, along with the schema it implements, lives in [`data_gen/`](data_gen/README.md) -- see that README for details, setup, and usage.

Quick start:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r data_gen/requirements.txt
python data_gen/generate_data.py
```

## Postgres container

The Postgres image has the generated dataset **already loaded into the
cluster**, so the data travels with the image and is available the moment a
container starts. `./setup.sh` pulls a prebuilt copy; the sections below cover
building your own.

```bash
docker compose up -d --build     # build it yourself: generates, loads, starts (~3 min)
docker compose up -d             # afterwards: just starts, nothing regenerated
```

It serves **TLS only** over the network, with a certificate it writes
itself on first start, and has **no password baked in** (`v1_2`): its
entrypoint sets the owner's from `POSTGRES_PASSWORD` and the reader's from
`POSTGRES_READER_PASSWORD` on every start -- `setup.sh` generates both into
`.env` -- and the `postgres` superuser has none at all, so it is reached only
with `docker compose exec postgres psql -U postgres`. Its port is this
machine's unless `DB_BIND_ADDRESS` says otherwise. Connect as the owner with

```bash
psql "postgresql://nl2sql@localhost:5432/nl2sql_retail?sslmode=require"   # asks for POSTGRES_PASSWORD
```

and see [`USAGE_GUIDE.md`](USAGE_GUIDE.md#connecting-to-the-retail-database-directly)
for verifying its certificate (`sslmode=verify-full`) and for a person
signing in with their own directory password.

### Roles

The cluster has two application roles, and the agent only ever uses the second:

| Role | Password | Can | Used by |
|---|---|---|---|
| `nl2sql` | `nl2sql` | everything: owns the database and every table | the build (`ddl.sql`, the `COPY` load) and you, at a `psql` prompt |
| `nl2sql_reader` | `nl2sql_reader` | `SELECT` on every table in `public`, nothing else | the agent, the review service's validation of a reviewer's SQL, the benchmark, the live tests |

The reader is created by [`docker/reader_role.sql`](docker/reader_role.sql):
a plain login role with no `CREATE`, `INSERT`, `UPDATE` or `DELETE` anywhere,
whose sessions also start read-only. Tables the owner adds later are readable
too, through a default privilege. The agent's own `SET TRANSACTION READ ONLY`
still runs on top of that; the grants are what hold if anything gets past it.

Two things every role gets by default are taken away, because every reader
session -- the API's, each `docker compose run`, the review service's -- is
the *same* role, and Postgres lets a role cancel or terminate backends of its
own role:

* **`pg_cancel_backend` and `pg_terminate_backend`** are revoked from PUBLIC
  in the retail database, so one query cannot end everyone else's. Before,
  `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename =
  current_user` from any reader session did exactly that. The agent's static
  validator already refused both names; the review service's validator never
  had that list, and the server is the boundary either way.
* **`CONNECT` on every other database** -- `postgres`, `template1`, anything
  created later -- is revoked from PUBLIC, so the reader's password opens the
  retail database and nothing else. A function privilege is per database, and
  a pid is not, so an open `postgres` database would have been the way round
  the first revoke.

The superuser keeps both, and nothing that reads the dataset needs either.

The image build creates the role, and so do `setup.sh` and `launch.sh` on every
start, because a volume created from an older image keeps the roles it had.
The file is idempotent, so running it again is always safe. If you run the
container by hand, do the same once:

```bash
docker exec -i nl2sql-postgres psql -U postgres -d nl2sql_retail \
  -v reader=nl2sql_reader -v reader_password=nl2sql_reader -v owner=nl2sql \
  -f - < docker/reader_role.sql
```

`POSTGRES_READER_USER` / `POSTGRES_READER_PASSWORD` rename the role; both the
build and the agent's `DATABASE_URL` read them, so they stay in step.

That this is really least privilege, and not just a file that says so, is
tested against the live cluster by
[`tests/agent/test_least_privilege_live.py`](tests/agent/test_least_privilege_live.py):
it reads the role's attributes and grants back out of the catalog, tries every
kind of write directly in a `READ WRITE` transaction, checks that a table the
owner adds later is readable but not writable, tries the other databases and
tries to cancel and kill another reader session, and confirms the agent's own
database layer runs as the reader. Pointed at the owner instead, 25 of its
44 checks fail. The review service's side -- a reviewer's query run through
its validator, trying to switch the transaction to read-write, become the
owner, lift its own timeout, read the server's files or end another session
-- is in [`tests/review/test_validation.py`](tests/review/test_validation.py). Run it with `pytest tests/agent/test_least_privilege_live.py --run-docker`
against a started stack.

### How the build works

The build ([`docker/Dockerfile`](docker/Dockerfile)) is two stages:

1. **generator** -- installs `data_gen/requirements.txt`, runs
   `generate_data.py --no-sqlite`, and writes one CSV per table.
2. **db** -- runs `initdb`, applies [`data_gen/ddl.sql`](data_gen/ddl.sql),
   bulk-loads every CSV with `COPY` in foreign-key-safe order, runs
   `VACUUM ANALYZE`, and shuts the cluster down cleanly so the populated data
   directory becomes an image layer.

The CSVs are bind-mounted from stage 1 rather than copied, so they never become
a layer in the final image and nothing is written to the host -- the only place
the data survives is inside Postgres. Stage 1's output does stay in the local
build cache; `docker builder prune` reclaims it.

Two details make the baking work:

- `PGDATA` is set to `/var/lib/pgdata`. The base image declares
  `/var/lib/postgresql` as a `VOLUME`, and writes to a volume path during a
  build are discarded.
- Because the cluster is initialized at build time, the `POSTGRES_USER` /
  `POSTGRES_PASSWORD` environment variables are *not* consulted at runtime.
  Credentials are fixed when the image is built.

### Persistence

The compose service mounts the named volume `nl2sql-pgdata` at `PGDATA`. Docker
seeds that volume from the image the first time it's created, and it then
retains anything written afterwards:

| Command | Data |
|---|---|
| `docker compose restart` / `stop` + `start` | kept |
| `docker compose down` then `up -d` | kept |
| `docker compose down -v` | **deleted**, reseeded from the image on next `up` |

So `down -v` is also how you reset to the pristine generated dataset.

### Changing the dataset

Any `generate_data.py` flag can be passed through `GEN_ARGS`. The volume takes
precedence over the image, so drop it when rebuilding:

```bash
GEN_ARGS="--scale 3 --seed 7" docker compose build
docker compose down -v && docker compose up -d
```

Other overrides: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_READER_USER`, `POSTGRES_READER_PASSWORD` (applied at build time),
`POSTGRES_PORT`, `IMAGE_NAME`, `IMAGE_TAG`.

### Pulling the prebuilt image

`./setup.sh` does this for you; this section covers doing it by hand. The
dataset is published, so pulling it avoids building anything -- no Python, no
generator run -- and everyone gets byte-identical data:

```bash
docker pull mcfaddja/nl2sql-retail-postgres:v1_2
```

The repository is public, so no `docker login` is needed. It is multi-arch
(`linux/amd64` and `linux/arm64`), so Docker selects the right variant
automatically. Expect roughly a 290 MB download that expands to about 1.4 GB on
disk.

The tags:

| Tag | Use |
|---|---|
| `v1_2` | The same dataset again, with no password baked in and TLS on: its entrypoint writes a certificate on first start, refuses anything over the network without TLS and the superuser over the network at all, and sets the owner's and the reader's passwords from the environment on every start -- of a fresh volume or of one an older image made. Pinned -- what `setup.sh` pulls since 6.1. |
| `v1_1` | The same dataset as `v1`, byte for byte, with the agent's read-only role built in -- and that role unable to connect to the cluster's other databases or to cancel or kill another session (see [Roles](#roles)). Its superuser's and owner's password is `nl2sql` in every copy: do not publish its port. |
| `v1` | The first publish. Pinned; it predates the read-only role, which `setup.sh` and `launch.sh` create on every start. |
| `latest` | Moves to the newest publish. |

#### Run it directly

```bash
docker run -d --name nl2sql-postgres \
  -p 127.0.0.1:5432:5432 \
  -e POSTGRES_PASSWORD="$(openssl rand -hex 24)" \
  -v nl2sql-pgdata:/var/lib/pgdata \
  mcfaddja/nl2sql-retail-postgres:v1_2
```

The volume must be mounted at `/var/lib/pgdata`, which is where this image puts
`PGDATA` (see the note above). The data is present on first start; the volume
only keeps what you write afterwards. Connect exactly as with a locally built
image, over TLS, with the password you gave it:

```
psql "postgresql://nl2sql@localhost:5432/nl2sql_retail?sslmode=require"
```

Give `POSTGRES_READER_PASSWORD` too to set the agent's reader's. With
neither, nothing can sign in over the network -- `docker exec` still can.
The older `v1_1` and `v1` carry the owner's and the superuser's password,
`nl2sql`, in every copy: treat it as public, and never publish their port
beyond this machine. `v1` predates the agent's read-only role, so with that
tag create it as shown under [Roles](#roles) before running the agent
against a container started this way.

#### Use it with compose

To point compose at the published image without running `setup.sh`:

```bash
export IMAGE_NAME=mcfaddja/nl2sql-retail-postgres IMAGE_TAG=v1_2
docker compose pull postgres
docker compose up -d --no-build
```

Everything else in this README still applies -- the volume, the persistence
table above, and `down -v` to reset to the pristine dataset.

### Publishing an update

A published tag never moves, so an update is a new tag: a patch (`v1_3`) for
a change to the image around the same dataset, as `v1_1` and `v1_2` were,
and a new major (`v2`) for a new dataset. Build both architectures in one step so the tag stays
multi-arch, then move `latest` onto it without rebuilding:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/Dockerfile --push -t mcfaddja/nl2sql-retail-postgres:v2 .
docker buildx imagetools create -t mcfaddja/nl2sql-retail-postgres:latest \
  mcfaddja/nl2sql-retail-postgres:v2
```

The generator is seeded, so a rebuild of an unchanged `data_gen/` is the same
data; compare a checksum of every table against the previous tag before
calling a patch a patch.

## Tests

```bash
pip install -r tests/requirements.txt
pytest                                          # 4630 tests, no Docker, npm, JDK or network needed
pytest --run-docker --run-node --run-java       # and the ones that need a daemon, npm or a JDK
pytest --run-docker --run-node --run-java --run-acceptance   # all 5427, the whole stack included
```

| Directory | Covers |
|---|---|
| [`tests/data_gen/`](tests/data_gen) | The generator: calendar, dimensions, facts, validation, CSV/SQLite writing, and `generate_data.py` as a script |
| [`tests/agent/`](tests/agent) | The agent: config, prompts, the LangGraph pipeline, the model router -- the table built from catalogs made to show each rule, the fallback chain, and every rung rule on its boundary, then again inside the pipeline, with the trace naming each call's model -- the tools, both retrievers, the ensemble fusion, the answer contract and the Completeness Reviewer -- rule by rule on hand-built rows, then again on real ones from the live database -- read-only enforcement, least privilege -- what the reader role can and cannot do, asked of a live catalog, including that it may become a signed-in person for a transaction and inherits nothing from anyone -- and the MLflow trace every run writes, read back as a tree from a fake MLflow: a span per agent named as the architecture names it, the four concurrent retrievers under their own run, a repair as a second generation, a routed call that fell back as two calls, and a server that is down, comes back, or refuses a verdict costing nothing but the trace |
| [`tests/api/`](tests/api) | The REST server: the certificate policy and the switch that refuses a self-signed one, the job store, every route and status code, the event stream, the two published request limits checked against the lengths actually enforced, a real uvicorn bound to a loopback port over real TLS, the curl-only smoke script run against it for real, and a verdict recorded on, replaced on and withdrawn from the answer's trace -- after the staging database takes it, and never instead |
| [`tests/rag/`](tests/rag) | The RAG pipeline: parsing the golden pairs, the BM25 index checked against an independent implementation, the pgvector storage layer, the semantic chunker the markdown one inherits from, both loader scripts -- their flags offline and their writes against a throwaway database created and dropped around each test -- and the seven shell scripts that build and publish the knowledge base, run against a fake `docker`, plus the two published images and the compose file that runs them |
| [`tests/docker/`](tests/docker) | The Dockerfiles, the reader-role SQL, `docker-compose.yml` as `docker compose config` resolves it (including that the owner's credentials never reach the agent and that every setting the agent reads can be set through it), retrieval end to end inside the real containers, and `start.sh`/`setup.sh`/`launch.sh` run against fake `docker`, `curl` and browser binaries -- including the browser opener each platform gets, chosen from a fake `uname` so the Linux and Windows branches run on a Mac too, the window each default browser is asked for, and Docker and Ollama started when they are down -- plus a structural check that every flag, warning and fatal message in the nine scripts that take them is exercised by some test, an inventory check that every shell script, Dockerfile and compose file git tracks -- and every service in both compose files -- is named by tests that mention it, every set of compose profiles a script runs or a document prints resolved by the real compose file, `docker/init_db.sh` run against fake `initdb`, `pg_ctl` and `psql`, and the measurement that says they all reach 100%, the API container reached over TLS by a curl-only container with nothing of this project in it, and the GUI container driven against a real API container on a private network, over HTTPS verified against the certificate that API wrote -- and the seventeen tags `setup.sh` pins, asked of Docker Hub: published, for both architectures, and at this checkout's version, and the three dataset images it pins, for both architectures -- and MLflow: its two services as compose resolves them, one version across the server and both clients, and tracing against a real server started from the pinned image on a private network under the name `setup.sh` writes -- the pipeline's trace read back by MLflow's own client, verdicts, the benchmark's runs, and the agent image's own tracing client doing everything the agent asks of it |
| [`tests/gui/`](tests/gui) | The web interface: its TypeScript types compared field by field against the pydantic models they mirror, the proxy configuration in both of the places it exists, the nginx start-up script's branches, and the GUI's own 333-test suite run from here |
| [`tests/java/`](tests/java) | The desktop client: its Java records compared component by component -- and in order, because records are positional -- against the pydantic models they mirror, the pom's pins and its coverage gate, the image that cross-builds its jar, and the client's own 409-test Java suite run from here |
| [`tests/review/`](tests/review) | The feedback system: rendering a golden pair against the rules the loader actually enforces, the promotion path round-tripped through the loader's own parser on a real copy of the real question document, the whole HTTP surface against a fake repository, the staging schema and its row-level policies asked of a live Postgres -- including everything the public process must *not* be able to do -- a reviewer's corrected SQL validated against the live retail database, including a writing CTE the database itself refuses, the corrections and completions stores and their vectors in a real pgvector Postgres, a judgement taken back -- a promoted pair withdrawn from a real copy of the document and checked by the loader's parser, a fix deleted with its vector, the promotion log cleared and the row handed back to the public process -- the compose wiring that no single file shows -- every setting the service and its proxy read, and nothing either does not -- and the review interface's own 175-test review GUI suite run from here |
| [`tests/curate/`](tests/curate) | The curation interface: its TypeScript types compared field by field against the review service's curation models, its nginx start-up script's branches, the project's pins and coverage gate, the compose wiring both ways -- every setting it reads, and the review service's token reaching it and never a browser -- and its own 101-test curation GUI suite run from here. The service behind it is in `tests/review/`: each snippet kind validated against the live retail database, every curation route, the snippet document round-tripped through the loader's parser, and golden pairs and fixes added and removed directly |
| [`tests/console/`](tests/console) | The SQL console: the agent's gates in the agent's order against a scripted database -- what is refused before the database is asked, the read-only fence, the cost judged as the planner gate judges it -- every route and error code, the certificate it presents and refuses to start without, the real retail database as the real reader, including a write the transaction refuses and a timeout, the compose wiring both ways -- the agent's own URL and limits, one credential, loopback ports -- four real containers on a private network, the interface's types against the models, and its own 150-test console GUI suite run from here |
| [`tests/auth/`](tests/auth) | Sign-in: the session format -- signed, verified, refused in each way it can be wrong -- and the guard every service puts in front of its routes, with its origin check and its roles re-read from Postgres; the auth service's settings, keys, throttle, sign-in against the database, the role sync's plan, every route and status code, and the directory page's API; the four sign-in services as compose resolves them; the directory page as a project, its types against the service's models and its own 54-test directory GUI suite; and, behind `--run-docker`, sign-in end to end -- a real retail database, directory and MLflow on a private network: the first administrator, a person added on the page who reads as their own role and stops being able to once removed, the database prepared again on a later start changing no membership, and MLflow's front door |
| [`tests/ldap/`](tests/ldap) | The directory: its settings and both modes, the CSV and LDIF it loads, every operation on people and groups against ldap3's in-memory directory, the `slapd.conf` it renders, its certificate, the first start, the replica's copy -- paged, nested groups resolved, a copy that would empty it refused -- and the supervisor; and, behind `--run-docker`, the image, slaptest on both modes' configuration, a replica copying a second directory end to end, and -- as compose leaves it -- a certificate volume another image's container was created on last still written, with nothing running as root |
| [`tests/security/`](tests/security) | The security tier (6.1): what the stack exposes by default, read from the files that decide it -- every store on this machine only, no password baked into the dataset image and its start refusing clear text and the superuser over the network, sign-in's database rules TLS-only and the auth service verifying the database, every TLS server its own key and no container another's, sign-in on by default in every service's own code, no secret on a script's command line -- every route in every service that does something carrying a guard, walked from each application's own route table, and every wire model in the four contracts refusing a field it does not declare. [`SECURITY.md`](SECURITY.md) lists what each defends |
| [`tests/docs/`](tests/docs) | These documents and the architecture diagrams, checked against the code they describe -- including every place the repository writes its own version down, which a release has to move together -- and the adversarial reviews' figures: each committed draw.io file held to the script that computes it, its SVG and PNG exports to the file, and the `_enhanced` review documents to the originals they add figures to |
| [`tests/benchmarks/`](tests/benchmarks) | The benchmark's own ground truth: every reference query executed against the dataset, the scorer tested against both kinds of mistake it could make, and its MLflow runs -- one per configuration with its parameters, metrics and report, every question's trace in it and judged, a run cut short ended as such, and a host without the runs API told so |
| [`tests/models/`](tests/models) | The calibrator, against fake models that answer by what each prompt says -- which probe counts toward which rung, what counts as right, the reference's reflection as the key, the cold load and resident size read from the host's own API, and what reaches the catalog -- and the model catalog builder, run against a fake Ollama host answering exactly what the real one did on 2026-09-27 and a fake ollama.com serving that day's pages: every model catalogued from the host's own answers, the MLX builds described by `/api/show` where `/api/tags` says nothing, a local build described by its parent's page, the prior checked against the table the spec worked by hand and then rule by rule on each boundary, every way of naming a host, measurements carried across a rebuild only for unchanged weights on the same host, every way the host or the site can fail to answer, borrowing the system's certificate authorities when Python has none -- over real TLS, and never by turning verification off -- and the committed catalog re-derived from its own facts; plus, behind `--run-docker`, the real host and the real library page |

The 732 tests behind `--run-docker` are the ones that need a working daemon:
they build the agent, GUI, console, desktop, directory and auth images and run them, resolve the real
compose file, query the live databases -- found as compose finds them, with the passwords in
`.env` ([`tests/live_stores.py`](tests/live_stores.py)), so a database that refuses the login is a
failure rather than a skip -- trace into a real MLflow server, and ask Docker Hub whether the
tags `setup.sh` pins were really published -- which also needs the network,
and skips rather than fails without it. Two more ask the chat host and
ollama.com what the model catalog is built from. The 50 behind `--run-node`
need npm, and run the five GUIs' own suites. The 6 behind `--run-java` need
Maven and a JDK of 21 or later, and run the desktop client's. Three flags
rather than one because the three needs are different -- a clone with Docker
but no npm should still be able to run every container test, a GUI developer
with npm and no Docker daemon should still be able to run the interface's,
and neither of them should be asked for a JDK to run the Python ones.

The 9 behind `--run-acceptance` are the **acceptance tier**
([`tests/acceptance/`](tests/acceptance)), and they are what a release passes
before it is published. They copy this checkout, start it as a stack of its
own beside any other -- `NL2SQL_INSTANCE` names its containers, volumes and
compose project, every port is a free one -- with the two commands a user
types, `./setup.sh --build-all --review --curate --console --mlflow --desktop`
and `./start.sh --review --curate --console --mlflow --no-browser`, and then
use it: the generated administrator signs in on every page, a question goes
through the web interface, its verdict through review into the golden set,
SQL through the console, and MLflow is asked for the question's trace through
its front door. Four of the nine are the four defects 6.0 shipped with every
other tier green: a container that restarted for ever, a database nobody
could sign in to, a lockout of everybody behind one address, and the desktop
client's classes against a server that asks who it is. Everything is removed
afterwards (`NL2SQL_ACCEPTANCE_KEEP=1` keeps it). It needs Docker with about
3.5 GiB of its memory free -- it says so rather than starting -- and ten to
twenty minutes; the tests that need an answer are skipped, saying why, when
the chat model `.env` names cannot be reached.
Everything else runs offline,
in about five minutes -- `setup.sh` included, since it is exercised against
fake binaries rather than real Docker -- as are `launch.sh`'s and
`start.sh`'s, which is worth saying because `launch.sh`'s were marked
`docker` for months without needing to be, keeping sixty tests out of the
default run. Those three scripts' suites are most of the five minutes: each
test runs the real script, and each of `start.sh`'s runs the real `setup.sh`
and `launch.sh` beneath it.

Twenty-eight of those 656 also need the **embedding host**: a local Ollama
serving `bge-m3`, the model both vector stores were built with. Without it they
skip with that as the stated reason rather than failing -- the rest of the
suite still passes, which is the property that matters. Start it with
`ollama serve` (and `ollama pull bge-m3` once) to run everything. To re-count
after a change:

```bash
TEST_EMBED_BASE_URL=http://127.0.0.1:9 pytest --run-docker -m docker   # whatever skips needs it
```

`TEST_EMBED_BASE_URL`, not the agent's own `EMBED_BASE_URL`: the tests read a
variable of their own, so pointing them at a dead port cannot also misdirect
an agent running beside them. The retrieval probes in
[`tests/docker/test_compose_rag_integration.py`](tests/docker/test_compose_rag_integration.py)
need Ollama too, but reach it the way the agent container does -- through
compose's settings -- so they are not in that count; they skip, with the same
reason, when it is down. The model catalog's live test reads
`TEST_OLLAMA_BASE_URL` for the chat host, for the same reason, and skips when
that host cannot be reached.

The 80 database-backed tests in [`tests/rag/`](tests/rag) need the stores
but **not** the embedding model: they exercise the storage layer with
synthetic vectors, which makes the distances predictable rather than merely
plausible. Each one runs against a throwaway database created and dropped
around it, so the published golden pairs and embeddings in the running
containers are never touched.

### Coverage

```bash
COVERAGE_FILE=$PWD/.coverage COVERAGE_PROCESS_START=$PWD/.coveragerc \
  coverage run --rcfile=.coveragerc -m pytest --run-docker
coverage combine && coverage report --show-missing --skip-covered
```

**100% of every Python file in the repository, statements and branches** --
14,811 statements and 3,526 branches, none missed. `coverage report` fails below
that (`fail_under = 100` in [`.coveragerc`](.coveragerc)) rather than printing
a number, the way the five web interfaces' vitest thresholds and the desktop
client's JaCoCo rule already did. Not four packages with the scripts left out: the agent, its REST
server and its SQL console, the feedback review service, the benchmark, the RAG pipeline and its
four loader scripts, the data generator and its CLI, the chunker, the
architecture-diagram generator, the build-time SQL emitter, and the model
catalog's builder and calibrator.

Exactly one statement is excluded, and the reason is written beside it: a
defensive `continue` in `facts.py` that is unreachable by construction,
because the loop runs to `max(k)` and the basket whose `k` equals that maximum
always satisfies the condition the guard tests. It is kept in case the loop
bounds ever change.

`COVERAGE_PROCESS_START` is not incidental. Several things here are tested the
way they are *used* -- as scripts, in their own process. `generate_data.py` is
run by `docker/Dockerfile`, `emit_load_sql.py` during the image build, the RAG
loaders by hand. Their tests invoke them the same way, with `subprocess.run`,
and without [`.coveragerc`](.coveragerc) turning on subprocess measurement the
report shows **0%** for a file with nine tests on it -- which is worse than no
number, because it sends someone off to write tests that already exist.
Switching it on moved five files from "untested" to 100% without a line of new
test code.

Getting the rest there deletes code as often as it adds tests. The last few
statements turned up a re-raise that could never fire (none of the five
whitelisted formula functions raises the exception it caught), two
`except ValueError` guards behind a regex that only matches valid floats, and
two properties on the API's agent holder that nothing read. A line no test can
reach is usually a line that cannot happen, and deleting it is the honest fix.

What was left after that was real: the `main()` of both RAG loaders -- the only
way the golden pairs reach either store -- the base `SemanticChunker` that
`MarkdownSemanticChunker` inherits from, and the entry points of the diagram
generator and both loaders. Those have tests now, against throwaway databases
and stub embedders.

Branches came later and went the same way. With every statement covered,
switching branch measurement on found 29 conditions that had only ever been
seen one way. Five were guards that could not be false -- both chunkers checked
for emptiness a list that always holds at least its first sentence, a flush
checked for something to flush that it is only ever called with, a prose run
checked for text that blank lines never join, and the SQL walker skipped a
pglast slot that is never among the slots it walks -- and they came out, with
a test pinning the one assumption a library upgrade could break. One was a
bug: a diagram row of an unknown kind fell through the dispatch and was drawn
as a copy of the row before it, silently; it is refused now. The rest were
real cases nothing had asked about: an API or review service with no CORS
origins, a NULL in a row a claim cites, an event stream with no deadline, a
meta key the parser does read, the four entry points imported rather than run,
and the review image's own layout, which carries `rag/ragproc` and no
`chunking/` beside it.

The tests were measured the same way, with themselves in the report, which is
how dead test code shows up: a fixture nobody requests, a fake's mode nobody
sets. That removed `Database.explain` -- the v3 validator's wrapper, which the
Planner Gate's `explain_plan` replaced and which only its own two tests still
called -- along with its fake, a scripted-model mode and a fake-repository
lookup nothing used, and a fake `docker` case for a command no script makes any
more. A later pass -- the same run, with `*/tests/*` taken out of `omit` and
`coverage report --include='tests/*'` -- removed a container helper nothing
called and a fake sink's failure mode no test chose, and gave three others a
test each, because each was a path worth one: a structured call that raises,
a calibration model that is down, an embedding host that answers 500. It also
found two tests asserting less than they read. One checked promotions against
the shared four-row dataset, which holds three of the six mechanics, so its
rule for the other three never ran; a test of three hundred promotions already
checked all six, and it came out. The other checked the smoke script for
arrays initialised empty and expanded unguarded -- and there are none, so its
loop never ran either; it now checks that every array the script expands is
given an element.

The same pass folded seven tests into others that ran the same scenario and
asserted the same thing. Removing one of them failed the warning sweep: it had
been the only quotation of a `setup.sh` warning, by way of its docstring,
while the test that triggers that warning quoted it too loosely to count. That
test quotes it in full now.

A third pass, after 5.4, found the sentence above about exactly one excluded
statement no longer true: seven more lines carried `# pragma: no cover`, and
the 100% had been measured around them. Six were reachable and have tests now
-- a submission deleted by another reviewer while it was being judged or
reopened, which the review interface's Delete made possible; a loader whose
interpreter cannot be started; the feedback sink's real connection, which
every other test replaced; and the two fallbacks for a pglast that cannot
print or walk what it parsed. The seventh was an `except` that only re-raised,
and it came out. The same pass found the agent's settings checked against
compose in one direction only, and against its README by a pattern that knew
two of `config.py`'s readers and so saw 32 of its 60 settings; both are
checked in full now. It also traced the fake binaries the script tests run
against -- with a `DEBUG` trap writing to a file, since a trace on stderr
would reach what the scripts read -- and every command in them runs except
the catch-alls for commands no script makes.

What is left unexecuted in the tests is the part that should be: the skips
for a missing daemon, registry or database, the failure messages of
assertions that pass, the clean-up of what an interrupted run left behind,
the diagram checks for versions that live on other branches, and the hooks
the shell measurement turns on.

The other two languages -- TypeScript and Java -- are measured separately,
because they have different runners, and to the same standard. First the
five web interfaces:

```bash
cd gui && npm test           # the web interface
cd review/gui && npm test    # the review interface
cd curate && npm test        # the curation interface
cd console && npm test       # the SQL console's interface
cd auth/gui && npm test      # the directory page
```

**100% of statements, branches, functions and lines** across 333 tests, with
only `main.tsx` excluded -- it mounts React onto a DOM element that exists
only in a browser, and a test pins the exclusion list so nothing else joins
it. The review interface is held to the same thresholds in its 175-test
review GUI suite: it decides what goes into the question set the agent is
measured against, so a partially tested path there is a partially tested
benchmark. The console's is held to them in its 150-test console GUI suite:
what it shows is what someone decides the agent did wrong from. The curation
interface's is held to them in its 101-test curation GUI suite, for the same
reason as the review interface's: it writes what the agent learns from. And
the directory page's in its 54-test directory GUI suite: it makes the people
everything else lets in.

The thresholds are in each project's `vitest.config.ts` and fail the run
rather than printing a number, and `pytest --run-node` runs all five suites
from the Python one so none can go stale unnoticed.

Getting there deleted code in the same way. Four guards came out that no
input could reach: a poll that re-checked a flag every caller had already
checked, a stream reconnect guarded three times over, and two index lookups
written as `?? []` to satisfy `noUncheckedIndexedAccess` on a list built from
the very keys being looked up. The third of those was replaced by carrying
the map's entries instead of a list of labels beside it, which removed the
possibility rather than the check.

One of the branches that would not cover turned out to be a real bug. The
guard against a stale answer compared job ids, and job ids are only known
*after* the POST returns -- so two questions in flight at once could have the
first one's answer overwrite the second's. Counting attempts instead fixed
it, and the test that could not be written before now drives exactly that
race.

The desktop client is held to the same standard in Java:

```bash
cd desktop && mvn test       # or pytest tests/java --run-java
```

**100% of lines and branches** across the desktop client's own 409-test Java
suite, gated by JaCoCo rather than reported by it, with only `Main` excluded
-- it calls `Application.launch()`, which does not return until the window is
closed. The interface half is tested through the real toolkit, headless via
Monocle, on the toolkit's own thread: a button is pressed with `fire()` and
the scene graph read afterwards, which exercises the handlers a click would
with nothing to wait for. `HttpApiClient` is driven against a real
`HttpsServer` on a loopback port with a certificate `keytool` generates at
test time -- the encrypted path is where a client's mistakes stay invisible
until deployment, and a committed private key is a private key in every
clone.

Getting the Java to 100% deleted code as well, and found a bug. Three guards
came out that nothing could reach: a second `finished` check in the watcher that both
of its callers had already made, a `finished` in the pause before the next
poll that the poll returns before reaching, and a lower bound on the HTTP
status that `HttpResponse` never reports. The bug was next to the last of
those -- an empty body was treated as success *before* the status was looked
at, so a 500 with no body parsed into a document of defaults instead of
raising. The test that found it is now the one that pins the order.

#### The parts a coverage report cannot see

Fourteen shell scripts, six nginx entrypoint fragments, two compose files,
eighteen Dockerfiles and six nginx templates, none of them Python. They are covered by
reading and by running -- and, since nothing in coverage.py can see a shell
script, by a measurement of their own:

* **Every one of them runs, and all of them reach 100%.**

  ```bash
  python -m tests.shell_coverage
  ```

  `start.sh`, `setup.sh` and `launch.sh` are driven against fake `docker`,
  `curl`, `sleep`, `uname`, `grep`, `systemctl`, `ollama`, `defaults`,
  `osascript`, `xdg-settings` and browser binaries; the seven scripts in
  [`rag/`](rag) the same way; `docker/apitest/smoke.sh` against a real HTTPS
  server; `docker/init_db.sh` -- which otherwise runs only inside `docker
  build` -- against fake `initdb`, `pg_ctl` and `psql`; `docker/ldap_hba.sh`
  against a fake `psql`; the dataset image's `docker/entrypoint.sh` against a
  fake stock entrypoint, `openssl`, `psql` and `gosu`; and all six
  `10-nl2sql-*.envsh` fragments as the nginx entrypoint sources them.
  That tool re-runs those suites with `bash -x` on and counts which commands
  the traces mention -- **1976 of 1976**.

  An inventory test compares those lists against `git ls-files`, because the
  lists are written by hand and a script that joins none of them is not
  reported as uncovered -- it is simply absent, which reads exactly like a
  script that passes. `docker/init_db.sh` sat in that blind spot: tracked,
  shell, and in no list, so the measurement said 100% of eleven scripts while
  a twelfth had never been run by anything. The same check now covers the
  Dockerfiles and both compose files.

  Compose *services* are inventoried the same way, in both compose files,
  and for a reason `git ls-files` cannot reach: a service is not a file. One
  added to either file and asserted on by nothing is absent rather than
  uncovered, which again reads like one that passes. `desktop` sat there for
  a day -- examined by two test files and named by neither of the compose
  ones.

  Two more file kinds are checked the same way, and driven straight off
  `git ls-files` with no list to keep in step at all: every
  `requirements.txt` must bound every version it names, and a package pinned
  exactly in two of them must be pinned to the same version -- the agent and
  the review service install four of the same packages, and a bump applied
  to one and not the other is two services that were only ever tested as
  one. Every nginx template must verify its upstream, resolve it per
  request, and take its token from a variable rather than carrying one.

  The same sweep found `review/gui/node_modules` in `.gitignore` and not in
  `.dockerignore`: 110 MB and four thousand files uploaded to the daemon on
  every build, to be thrown away. The rule is now derived from the npm
  projects that exist rather than written out, so a third interface cannot
  repeat it.

  A second blind spot was in the counting rather than the inventory, and was
  worse because the file was listed. The scanner counted quote characters to
  decide whether a line continued, and an escaped `\"` -- one line of
  `launch.sh` has one -- left it permanently inside a string, folding every
  command after it into the one before. The result is a *shorter* list of
  commands, all still reported as covered, so the report read `112 of 112,
  100%` while a third of the script was never looked at. It is a real scanner
  now, and the number went from 657 to 797 without a single new test: those
  commands were always being run, just never counted. Two tests now assert
  that no script is scanned only part way.

  A third was in the arithmetic. The percentage was formatted with `%.0f`,
  which rounded 867 of 869 up to `100%` -- the one number the tool exists to
  be trusted about. Only a clean sweep prints 100 now; everything else rounds
  down.

  It counts *commands*, not lines, because bash does not report lines
  individually and does not even report them consistently: a
  backslash-continued simple command is traced at its first line, an
  assignment from a multi-line `$(...)` at its last. Modelling that exactly
  is a losing game; a command counts as run when the trace mentions any of
  its lines, which is the question actually being asked.

  The percentage is a measurement, not the gate. What runs in CI is
  structural, in
  [`tests/docker/test_script_coverage.py`](tests/docker/test_script_coverage.py):
  every flag parsed, documented and *passed by a test*, and every `warn` and
  `die` message quoted by one. These scripts are almost entirely warnings,
  and a warning nobody triggers looks exactly like a warning that works.

  Two platform cases deserve their own note, because neither can happen on
  the machine the suite runs on. `start.sh` picks a browser opener from
  `uname`, so a fake `uname` runs the Linux and Windows branches on a Mac --
  an opener that is wrong for Linux is otherwise invisible until someone on
  Linux runs it. And WSL is told apart by reading `/proc/version`, which a
  Mac does not have, so a `grep` that answers for that one path and defers to
  the real one for everything else makes that branch reachable too. The
  default browser is read from LaunchServices on a Mac and from
  `xdg-settings` on Linux, and both are faked, so the new-window command of
  every browser family `start.sh` knows runs on whichever machine the suite
  does.
* **`docker/apitest/smoke.sh`**, the outside client, is run *for real* by
  [`tests/api/test_smoke_script.py`](tests/api/test_smoke_script.py): bash,
  curl and jq against a live HTTPS server built from `create_app` with a
  scripted pipeline. No Docker and no model, so every one of its paths runs on
  an ordinary `pytest` -- each of the three ways it decides to trust the
  server, a server that is up but not ready, a question that fails, a stream
  that carries nothing, and the refusals that make its two exit codes mean
  something.
* **Both compose files** are checked in both directions for every service
  that takes settings: nothing is set that the code never reads, and nothing
  the code reads is missing from it. That holds for the agent's own settings
  against `config.py`, the API's against `api/settings.py`, the GUI proxy's
  against its nginx template, the review service's against its own
  `settings.py`, the review interface's proxy against its template and
  start-up script, the smoke script's against the script itself, and -- in [`rag/docker-compose.yml`](rag/docker-compose.yml) -- the two
  image overrides the start-up scripts `export`, where a name compose does not
  read would make `--image` a silent no-op that pulls a published store and
  then starts a local one.
* **`docker/init_db.sh`**, which builds the retail cluster at image-build
  time, is the one script that cannot be driven by fake binaries -- faking
  `initdb`, `pg_ctl` and `psql` would leave nothing real under test. So it is
  run against the stock Postgres image it is built on, with a two-row dataset
  instead of a million: the cluster it leaves behind is started again
  afterwards, and asserted to hold the rows, to have `pg_trgm` installed, and
  to let the read-only role read and refuse to let it write.
* **The images and the proxy.** Every Dockerfile is read for the things that
  fail quietly: that the GUI's toolchain does not ship and its build stage is
  not emulated, and that both RAG stores keep `PGDATA` *outside* the path
  their base images declare as a `VOLUME` -- get that wrong and the published
  image is a perfectly valid, completely empty database, which looks like
  retrieval finding nothing rather than like a broken build. The GUI's nginx
  template is checked for the three settings that keep the event stream
  unbuffered, and its start-up script is run for both of its branches.

Running the scripts rather than only reading them is what earns its keep.
Doing it turned up three defects in one pass: the smoke script aborted under
`set -u` on bash 3.2 -- which is what macOS ships, and invisible inside its
own Alpine container; an unauthorised client exited `2`, the code that means
"the API was never there, retry", when the API was answering perfectly well;
and a missing `.tables` was counted as zero and announced as a pass, so an
error body read as a healthy server with nothing in it.

Bringing the `rag/` scripts under the same treatment turned up three more.
`publish_db_image.sh` answered its likeliest mistake -- forgetting the image
reference -- with `unknown option: chunkdb`, because `shift 2` fails with one
argument and leaves it in place for the option loop; the check now runs
before the shift. `run_update.sh --help` printed two lines of its own shell
source, its `sed` range having been written to the wrong line. And both store
starters accepted `-i` while documenting only `--image`.

A fourth came from the coverage measurement rather than from the tests. The
structural sweep said every flag was exercised, and `xtrace` said
`setup.sh --build` had never run: the flag appeared in a test as *data* --
one entry in a list of flags `--help` ought to mention -- and a substring
search cannot tell that from an argument. It reads the syntax tree now,
counting only flags actually passed to a script, which is how the local
dataset build came to have tests at all.

The same sweep, turned on the `warn` and `die` messages, found four more
hiding the same way. A message counted as checked if any four consecutive
words of it appeared in a test -- and "could not pull" appears in three of
setup.sh's messages, so one test about the vector store was marking the
postgres and context-store failures checked too. Neither had ever run, nor
had the warnings for an empty knowledge base or an empty context store. The
rule is uniqueness now rather than length: a quotation counts when no other
message *in the same script* contains it, which is what makes it a
quotation of that one.

Getting the last of it also meant fixing the measurement three times. A
command split across lines was being counted once per line and missed on
every one, which is how two scripts came to be quoted at 92% and 96% when
they were at 100%; `awk -F'"'` read as an unbalanced quote and swallowed
everything after it; and a `$(...)` holding a `while` loop was closed at the
`do`. Each is pinned by a test in
[`tests/docker/test_shell_coverage_tool.py`](tests/docker/test_shell_coverage_tool.py),
because a mistake in a measurement is a number in a document that nobody can
tell is wrong.

Getting the RAG pipeline to 100% turned up a real defect the same way.
`vector_store.search()` bound its query vector as a Python list, which
Postgres reads as `double precision[]` -- a type with no `<=>` operator at all,
so the function raised for every caller. It had only ever been reached from a
README example. The fix is the same text-literal cast the agent-side
retrievers use, and the regression is pinned by a test.
