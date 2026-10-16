# nl2sql

Ask a question about a retail database in plain English and get back the
answer, a chart, the rows and the SQL that produced them. A multi-agent
LangChain/LangGraph pipeline writes, validates and runs the SQL against a
Postgres container, using any model served by Ollama and retrieving what it
has been taught -- a knowledge base, worked examples and SQL snippets. In
front of it: a web interface, a desktop client, a REST API and a SQL console,
each asking who you are; behind it, a review workflow that turns the verdicts
people give into what it learns next. Everything runs in Docker. This is
version 6.3.

## Quick start

```bash
./start.sh              # the web interface, in your browser
./start.sh --desktop    # the Java desktop client instead, in a window
```

That is the whole thing. `start.sh` starts Docker and this machine's Ollama
if they are down, pulls what is missing, starts every container, and opens
<https://localhost:8080> once the page answers. The first run is a few
minutes and about 3 GB of images; afterwards it is seconds. Every page asks
who you are: the first person is `admin`, whose password the first run
generates (`cat secrets/ldap_admin_password`); `./start.sh --no-auth` runs
without sign-in.

You need Docker with Compose v2, an Ollama host serving a chat model with
tool support, and Ollama on this machine for the embedding model.
[`docs/QUICKSTART.md`](docs/QUICKSTART.md) takes a fresh clone to a first
answer, pointing the stack at your own Ollama host on the way;
[`docs/USAGE_GUIDE.md`](docs/USAGE_GUIDE.md) covers using every part of it.

Prefer a terminal?

```bash
./setup.sh
docker compose run --rm agent "How many stores are there?"
```

The rest of the stack is a flag away, each page opened in a window of its
own:

```bash
./start.sh --review        # and the review interface: the verdicts people gave
./start.sh --console       # and the SQL console, for working out a wrong answer
./start.sh --mlflow        # and MLflow: everything the agent did, per question
./start.sh --curate        # and the curation interface: snippets, golden pairs, fixes
./start.sh --review --curate --console --mlflow   # every page
docker compose --profile '*' down                 # stop; your data is kept
```

## What's new in 7.0

7.0 answers a hard question several ways, has a Judge read the answers,
and votes ([the design](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_3.md)).
Every image is published as `v7_0`, and `./start.sh` upgrades a 6.3 stack
in place ([upgrading](docs/images.md#upgrading-an-existing-checkout)):

- **The question, asked four ways.** Every question is screened once, then
  reworded by a model -- up to ten rewordings, each held to the original's
  numbers, names, direction and answer contract by a fidelity gate, the
  Supervisor reading each one -- and the pipeline runs on the original and
  the first three that pass. The runs are checked against their own
  question and grouped by agreement with the benchmark's scorer; a Judge
  reads every distinct answer -- its query and first rows, never how many
  runs gave it -- and sets aside the ones it can name a mistake in; and the
  largest group of accepted answers is delivered, opening with how the runs
  agreed: "Agreed by 4 of 4 independent runs of the question, each worded
  differently." When the vote leaves the question unsettled -- no majority,
  or one run standing alone -- the rewordings not yet run are asked too,
  and the Judge and the vote go again. The answer is the chosen run's,
  with what the other agreeing runs found that its rows bear out: a column
  of the same entities, joined on their key; what they said of rows it
  did not speak of, where those rows reproduce it; and each answer that
  lost, with how its query differs. Measured on the fifteen benchmark
  questions, it scores 15 to one run's 14, and on the sixty wordings of the
  paraphrase set 59 to 57, with 14 questions of 15 right in every wording
  to 13. Without the Judge the same runs scored 13 and 55: where rewordings
  shared a mistake the original avoided, they outvoted it. The Judge's
  rules name the kinds of mistake these questions were built around, so
  they are no longer a blind test of it ([the
  measurement](docs/benchmark.md#the-judge-before-the-vote)). With the
  second wave and fusion it still scores 15: one question took a second
  wave, and the answer the Judge accepted went from one run behind it to
  five ([the second wave and
  fusion](docs/benchmark.md#the-second-wave-and-fusion)).
- **The record of every run**, in `--json` and the REST answer's `ensemble`:
  each wording, its SQL and outcome, whether it could vote, and every
  rewording the gate discarded with the check it failed. Progress lines name
  the run, and MLflow keeps one trace per question with each run inside it.
- **The runs on screen.** The web interface and the desktop client draw a
  lane for every run while a question is answered, show how the runs agreed
  beside the answer -- "Most agree · 3 of 4", and the agent's own line
  about the vote -- and fold every wording, its outcome and its SQL away
  beneath it, with the Judge's verdict on each answer ([the web
  interface](docs/web_interface.md#the-web-interface)). A verdict is staged
  with that record, so a reviewer fixing a wrong answer sees the queries
  that agreed and the ones that lost; the review service adds the column
  when it starts, and until it has, verdicts are staged without it
  ([feedback](docs/feedback.md#what-happens-to-a-verdict)).
  [`arch_v7.svg`](arch_diagrams/arch_v7.svg) draws the whole of it.
  `--no-ensemble` (`ENSEMBLE_ENABLED=false`) is the pipeline alone.
- **How much the wording matters, measured.** `python
  benchmarks/run_benchmark.py --paraphrase-set` asks each benchmark
  question its own way and three others; `--config ensemble` asks it
  through the ensemble -- see [The paraphrase
  set](docs/benchmark.md#the-paraphrase-set) and [The ensemble
  configuration](docs/benchmark.md#the-ensemble-configuration).
- **One gate on the model host.** Every model call takes a slot first, and
  there is one slot unless `OLLAMA_PARALLEL_CALLS` says the host serves
  more. Upgrading from 6.3: the REST API's two workers, which called the
  host at once, now take turns; a host that serves two calls needs
  `OLLAMA_PARALLEL_CALLS=2` to keep them.
- **Two routing tasks more**, the Paraphraser and the Judge. A model catalog
  built before 7.0 is still read: the two are unmeasured and go to
  `OLLAMA_MODEL`. `models/build_catalog.py` rebuilds it at schema 3,
  keeping what was measured, and `models/calibrate.py --tasks paraphraser
  judge` measures the two ([the model catalog](docs/model_catalog.md)).
- **The benchmark and calibration scripts run on a host again**, which
  they had not since 6.2.
- **The grain check**, on by default, **and it changes the pipeline
  itself** -- so an upgrade to 7.0 changes what one run answers, with
  `ENSEMBLE_ENABLED=false` as well. Each table's grain -- daily, weekly,
  monthly -- is measured from its own dates, once, at the agent's first
  question. A query
  that joins two tables of different grains on their dates without rolling
  the finer up to the coarser's period, or on no date at all while the
  coarser still brings a row for every period, is refused by the Static
  Validator before it runs and sent back to be repaired, with a message
  naming the two tables, their grains and the columns to join on.
  `GRAIN_CHECK_ENABLED=false` turns it off, and `GRAIN_PERIODS` names the
  periods for another calendar. Measured through all five benchmark
  configurations with it and without: it fired only on the trap it was
  built for, every run it refused came back with its grain right -- all
  but one the right answer, the one a run with the schema alone that also
  divided by the wrong figure -- and it refused nothing that was right.
  One run a question, the grain questions in all their wordings
  went from 11 of 12 right to 12; through the ensemble they were right
  either way, and the question that fell into the trap is now agreed by
  all of its runs where the Judge had been setting two of four aside ([the
  pipeline](agent/README.md#the-pipeline), [what it
  measured](docs/benchmark.md#the-grain-check)).
- **`--no-rag` answers from the whole schema again.** With retrieval off,
  or every store down, the tables in scope had been the first ten by name
  since v4 and the answer contract's alone since v5 -- no fact table
  either way, so a query that read the sales was refused as out of scope.
  The scope is every table again, as v1's was, and the benchmark's
  `schema-only` went from 1 of 15 to 10 ([the
  measurement](docs/benchmark.md#schema-only-with-the-whole-schema)).
- **A query may read the tables its knowledge is about.** A business rule
  or a recipe lists the tables it covers; a draft that reads one is
  admitted to it, where it used to be refused as out of scope whenever
  the table ranking had missed that table. With the knowledge base and no
  worked examples the benchmark goes from 13 of 15 to 14, and nothing else
  moves ([the measurement](docs/benchmark.md#what-the-knowledge-is-about)).
- **A value is matched when the question names it.** The Literal Matcher
  resolved a phrase to any value that shared a word with it: "year" was a
  promotion called `New Year New You`, "products" the sub-category `Paper
  Products`. It now takes the whole value -- every word of it, or a close
  spelling -- and for the questions this repository has a verified query
  for it offers 80 values where it offered 466, with every one the queries
  use kept. The block they arrive in is headed once, where it had its
  heading twice. As the agent ships, the default configuration answers all
  fifteen benchmark questions where it answered fourteen, and through the
  ensemble all fifteen are unanimous; a value that begins with a digit or
  has a slash in it is matched in quotes ([the
  measurement](docs/benchmark.md#what-a-question-names)).
- **A health check is not root's.** Docker runs a container's health check
  as the user the container was started as, and nine of the stack's
  containers start as root, to take their volumes before they drop to an
  account. Each of those checks is now started as that account, so what
  Docker runs in a container to ask after its health is not root's either:
  watched for five minutes, a 6.3 stack showed a root process 43 times and
  this one none ([what each container may
  use](docs/hardening.md)).

## What's new in 6.3

- **Every container is hardened.** Read-only, no Linux capability it does
  not use, no way to gain a privilege, and a ceiling on its memory and its
  processes.
- **Every password and token is a file in `secrets/`**, mounted into only
  the services that read it, and in no container's environment. The first
  sign-in's password is `cat secrets/ldap_admin_password`.
- **One image serves every page**, `nl2sql-proxy`, and MLflow's front door:
  twelve published tags where 6.2 had seventeen.
- **The runtime stores are one server.** The feedback, corrections,
  completions and snippet stores are four databases in `nl2sql-stores`;
  `launch.sh` moves what the stores from before held into it.
- **A one-shot, `dbprep`, prepares every database** in Python, over each
  database's own socket, so neither script runs SQL; every role a service
  connects as has a statement timeout, a memory ceiling and a connection
  limit.
- **Every base and stock image is pinned by digest.**

Upgrading is `git pull` and `./start.sh`: it re-pins the images, retires
the old stores' containers and moves their data, and puts the passwords
into `secrets/` -- see [Upgrading an existing
checkout](docs/images.md#upgrading-an-existing-checkout). The whole history
is in [`docs/CHANGELOG_SIMPLE.md`](docs/CHANGELOG_SIMPLE.md), one line per
change, and [`docs/CHANGELOG.md`](docs/CHANGELOG.md), every artifact each
version created, updated or fixed.

## Documentation

Everything that was in this file is in [`docs/`](docs), a document a topic.

### Using it

| Document | What it covers |
|---|---|
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | From a fresh clone to a first answer, on your own Ollama host |
| [`docs/USAGE_GUIDE.md`](docs/USAGE_GUIDE.md) | Using every part of it: asking questions, reading an answer, reviewing and curating, the SQL console, MLflow, the benchmark, configuration, security, upgrading, troubleshooting, the script reference |
| [`docs/stack.md`](docs/stack.md) | Running the stack: the three scripts and when to use each, the containers they start, every flag, and what each script prints |
| [`docs/images.md`](docs/images.md) | The published images and their tags, pulling and pinning them, upgrading an existing checkout, and publishing a release |
| [`docs/sign_in.md`](docs/sign_in.md) | Sign-in: the directory, the auth service, the four groups, the certificates, and `--no-auth` |

### How it works

| Document | What it covers |
|---|---|
| [`docs/agent.md`](docs/agent.md) | The NL2SQL agent: the pipeline version by version, from schema-only to routed multi-agent, and where it shows |
| [`docs/web_interface.md`](docs/web_interface.md) | The web interface: what the minute an answer takes shows, how the answer arrives, feedback, and the proxy in front of the API |
| [`docs/desktop_client.md`](docs/desktop_client.md) | The desktop client: why it exists, how it trusts the server, and the tag per platform |
| [`docs/rest_api.md`](docs/rest_api.md) | Connecting a GUI: the REST API, a question as a resource, the progress stream, TLS, and the outside client |
| [`docs/sql_console.md`](docs/sql_console.md) | The SQL console: the retail database queried as the agent queries it, with the agent's verdict beside the rows |
| [`docs/feedback.md`](docs/feedback.md) | Feedback: what happens to a verdict, promotion into the golden set, fixes for wrong and incomplete answers, taking a judgement back |
| [`docs/snippets.md`](docs/snippets.md) | SQL snippets and curation: the pieces a query is built from, how the agent uses them, the curation interface, and the runtime stores' settings |
| [`docs/tracing.md`](docs/tracing.md) | Tracing: every question as an MLflow trace, verdicts on traces, the front door, and MLflow's settings |
| [`docs/benchmark.md`](docs/benchmark.md) | The benchmark: what it measures, the results per configuration, and what they do and do not say |
| [`docs/model_catalog.md`](docs/model_catalog.md) | The model catalog the router routes from, and how to build and calibrate one for your host |
| [`docs/dataset.md`](docs/dataset.md) | The retail dataset: the generator, the Postgres image, its roles, persistence, and publishing it |
| [`docs/hardening.md`](docs/hardening.md) | What each container may use: read-only filesystems, capabilities, memory and process ceilings, secrets as files, sockets, and every role's limits |
| [`docs/architecture_diagrams.md`](docs/architecture_diagrams.md) | One generated diagram per agent version, and the adversarial reviews' figures |
| [`docs/tests.md`](docs/tests.md) | The test suite tier by tier, how to run it, and the coverage -- Python, TypeScript, Java, shell and compose |

### Reference

| Document | What it covers |
|---|---|
| [`docs/SECURITY.md`](docs/SECURITY.md) | What the stack protects, from whom, its trust boundaries, the deployment tiers its defaults are built for, known limits, and how to report a problem |
| [`docs/CHANGELOG_SIMPLE.md`](docs/CHANGELOG_SIMPLE.md) | Every version, one line per change |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | Every version, with every artifact it created, updated or fixed, and the tags it published |
| [`docs/basic_agent_steps.md`](docs/basic_agent_steps.md) | The five steps the first agent was built from |
| [`multi-agent_arch_specs/`](multi-agent_arch_specs) | The architecture as designed and as built; [`Multi-Agent_NL2SQL_arch6.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch6.md) is the current one, with 6.2 and 6.3 beside it, and [`Multi-Agent_NL2SQL_arch7.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7.md) the design of what comes next -- the question asked several ways, the answers validated against each other, one chosen or fused -- with its build plan, module by module, in [`Multi-Agent_NL2SQL_arch7_implementation.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_implementation.md), and that plan's risks placed on its phases in [`Multi-Agent_NL2SQL_arch7_risks_by_phase.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_risks_by_phase.md); [`Multi-Agent_NL2SQL_arch7_1.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_1.md) is arch7 with the Judge before the vote and what 7.0's build taught it, with its own [implementation specification](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_1_implementation.md) and [risks by phase](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_1_risks_by_phase.md), and supersedes arch7's three where they differ; and [`Multi-Agent_NL2SQL_arch7_2.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_2.md) is arch7.1 with its Phase 3 as built -- a dissent that reads how each query combines its facts -- and a grain check, a phase of its own, with its own [implementation specification](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_2_implementation.md) and [risks by phase](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_2_risks_by_phase.md), superseding arch7.1's three where they differ; and [`Multi-Agent_NL2SQL_arch7_3.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_3.md) is arch7.2 with the owner's decisions on the grain check -- in 7.0.0, on by default, its grains measured, a join on no date caught too -- with its own [implementation specification](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_3_implementation.md) and [risks by phase](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_3_risks_by_phase.md), superseding arch7.2's three where they differ; and [`Multi-Agent_NL2SQL_arch8.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8.md) is the design of what comes after 7.0, not yet built -- the conversation remembered, so a follow-up is read in the light of the last `CONVERSATION_MEMORY` answered turns (five by default, fifteen at most, none at zero), and the pipeline chosen by release name at start (`PIPELINE_VERSION`: `v7_0_0` the question asked several ways, `v6_3_1` one run a question), said in every record -- with its diagram, its build plan, module by module, in [`Multi-Agent_NL2SQL_arch8_implementation.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8_implementation.md), and that plan's risks placed on its phases in [`Multi-Agent_NL2SQL_arch8_risks_by_phase.md`](multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8_risks_by_phase.md) |
| [`adversary_reviews/`](adversary_reviews) | The adversarial reviews each hardening release answered, and what the second one's plan left open at 6.3.0 ([`v6_1_review_misses.md`](adversary_reviews/v6_1_review_misses.md)) |
| [`LICENSE`](LICENSE) | The license |

### Each component

| Document | What it covers |
|---|---|
| [`agent/README.md`](agent/README.md) | The agent's pipeline, every setting, model routing, tracing |
| [`agent/USAGE.md`](agent/USAGE.md) | The terminal in depth |
| [`agent/API.md`](agent/API.md) | The REST API contract, with client code |
| [`gui/README.md`](gui/README.md) | The web interface |
| [`desktop/README.md`](desktop/README.md) | The desktop client |
| [`review/README.md`](review/README.md) | The review service: promotion, fixes, taking a judgement back, and the curation routes |
| [`curate/README.md`](curate/README.md) | The curation interface |
| [`console/README.md`](console/README.md) | The SQL console |
| [`auth/README.md`](auth/README.md) | Sign-in: the auth service and the directory page |
| [`ldap/README.md`](ldap/README.md) | The directory, standalone or as a replica |
| [`proxy/README.md`](proxy/README.md) | The one image every page is served from |
| [`common/README.md`](common/README.md) | The shared Python package, and the `dbprep` one-shot |
| [`web/README.md`](web/README.md) | The shared web package every page is built with |
| [`rag/README.md`](rag/README.md) | Building the knowledge base and the stores |
| [`models/README.md`](models/README.md) | The model catalog and calibration |
| [`benchmarks/README.md`](benchmarks/README.md) | The benchmark and its scoring |
| [`data_gen/README.md`](data_gen/README.md) | The synthetic retail data |
