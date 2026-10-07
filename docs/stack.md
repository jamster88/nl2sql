# Running the stack

*Part of the [nl2sql documentation](../README.md#documentation).*

The three scripts, the containers they start, the flags they take and what each one prints.

```bash
./start.sh              # the web interface, in your browser
./start.sh --desktop    # the Java desktop client instead, in a window
```

That is the whole thing. [`start.sh`](../start.sh) starts Docker if it is not
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
the first run generates into `secrets/` (`cat secrets/ldap_admin_password`); they
add everyone else on the directory page at <https://localhost:8084>. See
[Sign-in](sign_in.md#sign-in), and `--no-auth` for a run without it.

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
| `nl2sql-stores` | The runtime stores, four databases in one pgvector server: the SQL snippets -- joins, filters, measures and dimensions, each with an embedding of what it means, loaded from `context_questions/sql_snippets.md` -- the verdicts given in the web interface, waiting to be reviewed, and the validated queries that fix wrong answers and complete incomplete ones |
| `agent` | The v4 agent, run on demand per question |
| `nl2sql-api` | The same agent as a TLS REST server, started only with `--api` |
| `nl2sql-gui` | The web interface, and the proxy in front of the API, with `--gui` |
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

Every page -- the web interface, the review, curation and SQL console
interfaces, the directory page and MLflow's front door -- is one published
image, `nl2sql-proxy`, each container told which page it serves. It, the
agent, the review service, the desktop client's jar, MLflow's server and
store, the directory and the auth service are published images (`v6_3`);
the rest are built or pulled by `setup.sh` as well -- the runtime stores are
a stock pgvector, pinned by digest like every image this project does not
build. Two one-shot containers run before the rest and exit: `pki`, which
issues each service its certificate, and `dbprep`, which prepares every
database -- the roles, the extensions, the sign-in rules -- so neither
script runs SQL of its own. [Pulling the images](images.md#pulling-the-images) has the
tags, and [`CHANGELOG_SIMPLE.md`](CHANGELOG_SIMPLE.md) what changed in each.

Every container is read-only, holds no Linux capability it does not use,
cannot gain a privilege, and has a ceiling on its memory and its processes;
no password or token is in any container's environment, only in a file in
`secrets/` mounted where the service reads it. See
[What each container may use](hardening.md#what-each-container-may-use).

## Three scripts

| | When | What it does |
|---|---|---|
| [`./start.sh`](../start.sh) | You just want to use it | Starts Docker and this machine's Ollama if they are down, runs the two below -- `setup.sh` too whenever `.env` is older than this checkout -- and opens an interface: the web one in your browser by default, or the Java desktop client with `--desktop`. `--review` brings the feedback system up as well and opens the review page in a window of its own, `--curate` the curation page, `--console` the SQL console, and `--mlflow` MLflow |
| [`./setup.sh`](../setup.sh) | First run on a machine | Pulls every image, pins them in `.env`, starts the databases, verifies retrieval end to end |
| [`./launch.sh`](../launch.sh) | Every time after | Starts whatever is down and checks it is *populated* -- loading the SQL snippets when their document has changed -- that both models are reachable, and which models calls will be routed to |

`start.sh` adds nothing to the stack itself -- that is the other two
scripts' -- but it starts what the stack runs on, keeps `.env` pinned to what
this checkout ships, and opens an interface. Use the other two directly when
you want the parts separately: a terminal session with no API, a different
agent tag, no knowledge base.

```bash
./start.sh --review        # and the review interface, in a window of its own
./start.sh --desktop       # the Java desktop client instead of the web one
./start.sh --desktop --review   # the window, and the review page in a browser
./start.sh --feedback      # the same as --api since 6.3: verdicts are kept whenever it is up
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

`--review` is the whole feedback system in one command: the service that
promotes verdicts into the golden question set -- they are staged in the
runtime stores, which start with the databases -- and a second page at <https://localhost:8081> in a browser window of its
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

Or in a browser -- see [The web interface](web_interface.md#the-web-interface):

```bash
./launch.sh --gui          # without the browser step
open https://localhost:8080
```

Or with the feedback system, which keeps the verdicts people give in the web
interface and lets them be turned into golden questions -- see
[Feedback](feedback.md#feedback):

```bash
./start.sh --review        # both pages, opened for you
./launch.sh --review       # the same containers, without the browser step
```

Or, when an answer is wrong, the SQL console -- the retail database queried
as the agent's read-only role, through the agent's own gates, with the
verdict of each beside the rows; see [The SQL console](sql_console.md#the-sql-console):

```bash
./start.sh --console       # opened for you, in a window of its own
./launch.sh --console      # the same containers, without the browser step
```

Or, to teach it this database's pieces -- how two tables join, what a phrase
filters to, how a measure is calculated -- and to add or remove golden pairs,
corrections and completions without going through the review queue, the
curation interface, at <https://localhost:8083>; see
[SQL snippets and curation](snippets.md#sql-snippets-and-curation):

```bash
./start.sh --curate        # opened for you, in a window of its own
./launch.sh --curate       # the same containers, without the browser step
```

Or, to see what the agent did with a question -- each agent's span with what
it read and what it wrote, and every model call inside it -- MLflow, at
<https://localhost:5001>; see [Tracing](tracing.md#tracing):

```bash
./start.sh --mlflow        # opened for you, in a window of its own
./launch.sh --mlflow       # the same containers, without the browser step
```

Or in a window rather than a browser -- the same questions, the same answers
and the same verdicts, from a Java application on this machine; see
[The desktop client](desktop_client.md#the-desktop-client):

```bash
./start.sh --desktop       # builds it, trusts the API, opens it
./launch.sh --desktop      # build it and stop there
```

Or as a REST server for something else to talk to, which is the same agent
started as a server instead of a command -- see
[Connecting a GUI](rest_api.md#connecting-a-gui):

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

[`setup.sh`](../setup.sh) pulls each image, starts the databases, writes a `.env` so
plain `docker compose` commands pick all of that up, checks that the chat and
embedding models are reachable, and finishes by proving the agent container can
actually retrieve from the knowledge base:

```
==> Checking the agent can reach the knowledge base
    retrieval works end to end (3 collections searched)

==> Setup complete. Running now:

    nl2sql-postgres    the retail dataset
    nl2sql-stores      feedback, corrections, completions and SQL snippets
    nl2sql-vectordb    the embedded knowledge base
    nl2sql-chunkdb     the golden pairs and their BM25 index
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
