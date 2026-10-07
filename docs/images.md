# Images and versions

*Part of the [nl2sql documentation](../README.md#documentation).*

What is published, how it is pulled and pinned, and what moves when a checkout is upgraded.

## Pulling the images

```bash
docker pull mcfaddja/nl2sql-agent:v6_3     # the agent, the REST API, the SQL console and dbprep
docker pull mcfaddja/nl2sql-proxy:v6_3     # every page, and MLflow's front door
docker pull mcfaddja/nl2sql-review:v6_3    # the review service, and the snippet loader
docker pull mcfaddja/nl2sql-mlflow:v6_3    # MLflow, where every question is traced
docker pull mcfaddja/nl2sql-mlflowdb:v6_3  # the Postgres MLflow keeps traces in
docker pull mcfaddja/nl2sql-ldap:v6_3      # the directory
docker pull mcfaddja/nl2sql-auth:v6_3      # sign-in
```

`nl2sql-proxy` is nginx with every page built into it -- the web interface,
the review, curation and SQL console interfaces and the directory page --
and MLflow's front door, which serves no page; `NL2SQL_PAGE` says which a
container is ([`proxy/README.md`](../proxy/README.md)). Until 6.3 each was an
image of its own: `nl2sql-gui`, `nl2sql-review-gui`, `nl2sql-curate-gui`,
`nl2sql-console-gui`, `nl2sql-directory-gui` and `nl2sql-mlflow-proxy`, six
copies of one nginx and one start-up script to keep in step. Their
published tags are still there, and still `6.2`'s.

The console has no image of its own: it is the agent's, started a third way,
and so is `dbprep`. Nor do the runtime stores: they are stock
`pgvector/pgvector:pg18`, pinned by digest, the snippets filled from their
document by the loader in the review service's image -- which is why
`setup.sh` pins that image whenever retrieval is on.
MLflow's two are thin: MLflow's own published server
([`docker/mlflow/Dockerfile`](../docker/mlflow/Dockerfile)), pinned to the
version of the tracing client the agent carries, and stock Postgres
([`docker/mlflowdb/Dockerfile`](../docker/mlflowdb/Dockerfile)), each labelled
and published with the release so that a release's images are one set.

The desktop client is published too, but by platform rather than by
architecture, because a jar carries native code for the machine it will draw
on: `mcfaddja/nl2sql-desktop-build:v6_3-mac-aarch64` and the four siblings
named in [The desktop client](desktop_client.md#the-desktop-client). The image holds the jar
and nothing else -- 33 MB, not the gigabyte of Maven that produced it --
and `./launch.sh --desktop` pulls the one this machine needs, falling back to
building it when there is nothing to pull.

`setup.sh` pulls the agent for you and pins it in `.env`. Every page is one
image, `nl2sql-proxy`, which `setup.sh` pins whenever anything will serve a
page: the directory's, with sign-in on, which it is unless `--no-auth`; any
page asked for; or MLflow's front door. With sign-in off and no page asked
for, it is left out, because most people ask questions from a terminal and
an image for a container that is never started is a download nobody asked
for:

```bash
./setup.sh                    # sign-in is on, so the pages' image is pinned
./setup.sh --no-auth          # leaves it out; ./launch.sh --gui builds it here
./setup.sh --no-auth --gui    # pulls and pins it, so ./launch.sh --gui runs it
./setup.sh --mlflow           # and MLflow's server and store
```

Both work. The difference is that compose builds a service whose image is
missing, so without the pin the first `./launch.sh --gui` spends several
minutes running `npm ci` for every page inside a build.

## Upgrading an existing checkout

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

Going to `v6_3` moves three things, and the first `launch.sh` after the
pull does all three:

* **The runtime stores are one server.** The feedback, corrections,
  completions and snippet stores were four Postgres containers until 6.3;
  they are four databases in `nl2sql-stores` now, on one port (5435, where
  feedback's was). `setup.sh` and `launch.sh` stop the four old containers
  -- cleanly, so what each holds is whole -- and remove them, keeping their
  volumes; then the first `launch.sh` finds each old volume --
  `nl2sql_feedbackdata`, `nl2sql_correctionsdata`, `nl2sql_completionsdata`
  -- and moves what it holds into its database with the `storesmigrate`
  service ([`docker/migrate_store.sh`](../docker/migrate_store.sh)): the old
  volume mounted read-only, dumped by a Postgres of its own, restored into a
  database that has no tables yet, and marked as migrated so it is never
  moved twice. It leaves the old volumes where they were and says how to
  remove them once you are satisfied. The snippets are not moved: their
  document is what they are loaded from, and they are loaded again.
* **The passwords are files.** Every password and token moves out of `.env`
  into `secrets/`, a file each, which compose mounts into only the services
  that read it; `setup.sh` and `launch.sh` move the values a `.env` from
  before holds, so nothing changes but where they are. `cat
  secrets/ldap_admin_password` is the first sign-in's password now.
* **The pages are one image.** `setup.sh` pins `nl2sql-proxy` in place of
  the six it replaces; `start.sh` runs it again for that on its own.

Going to `v5_6` brought up the snippet store and `v5_1` the corrections and
completions stores, on new empty volumes; `v6_3`'s `nl2sql-stores` is where
all three are now, and the review service still creates each schema on its
first start, so nothing is migrated by hand.

To publish new ones, build both architectures in the same step so the tags
stay multi-arch, as every earlier tag is:

```bash
docker login
docker buildx build --platform linux/amd64,linux/arm64 \
  -f agent/Dockerfile --push -t mcfaddja/nl2sql-agent:v6_3 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f proxy/Dockerfile --push -t mcfaddja/nl2sql-proxy:v6_3 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f review/Dockerfile --push -t mcfaddja/nl2sql-review:v6_3 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/mlflow/Dockerfile --push -t mcfaddja/nl2sql-mlflow:v6_3 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f docker/mlflowdb/Dockerfile --push -t mcfaddja/nl2sql-mlflowdb:v6_3 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f ldap/Dockerfile --push -t mcfaddja/nl2sql-ldap:v6_3 .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f auth/Dockerfile --push -t mcfaddja/nl2sql-auth:v6_3 .
```

Every base image those builds start from, and every stock image compose
runs, is pinned by digest as well as by tag, so a build is of the bytes it
was tested with rather than whatever a tag points at that day.
[`tools/pin_images.py`](../tools/pin_images.py) checks that nothing is
unpinned -- `tests/security/test_pinned_images.py` runs it -- and, with
`--update`, asks the registry for each tag's current digest and writes it
in, which is how a base image is moved on purpose.

The desktop client is published along a second axis as well. Every tag is
multi-architecture like the ones above -- that is the machine the image
*runs* on, to copy the jar out -- but the jar inside carries native code for
one JavaFX platform, so there is a tag per platform:

```bash
for platform in mac-aarch64 mac linux linux-aarch64 win; do
  docker buildx build --platform linux/amd64,linux/arm64 \
    -f desktop/Dockerfile --build-arg JAVAFX_PLATFORM=$platform \
    --push -t mcfaddja/nl2sql-desktop-build:v6_3-$platform .
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
[`tests/docs/test_versions.py`](../tests/docs/test_versions.py) holds the
thirty places that say so to the same number.

The database images are not in that list. `mcfaddja/nl2sql-retail-postgres`
(`v1_2`) and the two RAG stores (`v3_2`) version independently, because their
*content* changes independently of the code. All three are multi-arch, and
[`tests/docker/test_published_images.py`](../tests/docker/test_published_images.py)
asks the registry so. The RAG stores used not to be: they are published by
[`rag/publish_db_image.sh`](../rag/publish_db_image.sh), which used to tar a
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
[`agent/Dockerfile`](../agent/Dockerfile) and the pages' from
[`gui/package.json`](../gui/package.json) and its siblings; tests pin both to
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
| `v6_3` | The second review's phase 5. Every container is read-only, holds no capability it does not use, cannot gain a privilege, and has a ceiling on its memory and its processes. Every password and token is a file in `secrets/`, mounted into only the services that read it, and in no container's environment. Every page and MLflow's front door are one image, `nl2sql-proxy`, whose health check -- like every service's -- verifies the certificate it is answered with. The feedback, corrections, completions and snippet stores are four databases in one server, `nl2sql-stores`, and `launch.sh` moves a store from before into it. A one-shot, `dbprep`, prepares every database in Python, over each database's own socket, so neither script runs SQL. Each service's database role has a statement timeout, a memory ceiling and a connection limit; every base image is pinned by digest. Twelve tags where `v6_2` had seventeen. Pinned -- what `setup.sh` pulls. |
| `v6_2` | The second review's phases 3 and 4. A session can be ended: signing out, a password changed or set, an account locked or removed ends the sessions it should for every service within a minute, from lists only the auth service writes. A service token is somebody -- named, recorded as `token:<name>`, holding only the roles it is given -- and a header never names who did something. Nothing runs as root but the one-shot `pki`: each service runs as an account of its own, each key is that account's, and the review service writes the checkout's documents as the person who owns them, loading the stores in its own process under a lock. Every route is on a router that carries its guard; a failure's own words are an administrator's; a person's name is in the transaction's `application_name`; the sign-in throttle believes only the page proxies' `X-Forwarded-For`; fix ids come from sequences; an administrator can make the agent read its catalogs again. What the services share is one installed package, `common/`, and what the pages share one source package, `web/`; every Python image installs a hash-checked lock. Pinned. |
| `v6_1` | Hardening, from the second adversarial review (`v6_1_review`). The databases answer on this machine only unless `DB_BIND_ADDRESS` says otherwise, and `setup.sh` generates every store's password; the retail database is `v1_2`, with no password baked in, TLS on, and sign-in accepted only over TLS, verified by the auth service. Every service has its own certificate, issued by a development CA that a new one-shot service, `pki`, keeps; a client trusts `nl2sql-ca.crt` once. Sign-in is on in the code as well as in compose, CORS is closed until opened, the job queue is bounded and answers 429, `EXPLAIN` is time-boxed, tokens are scrubbed from the access log, and the directory's API answers on its own port, behind its page. A repair starts from a clean attempt, a narrator or supervisor that fails says so in `node_errors`, every wire model refuses a field it does not know, and each trace entry carries its model, rung, route and hops. `SECURITY.md` is the threat model; `Multi-Agent_NL2SQL_arch6.md` the architecture as built. Pinned. |
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
