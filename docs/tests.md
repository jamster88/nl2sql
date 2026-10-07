# Tests

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
pip install -r tests/requirements.txt
pytest                                          # 4896 tests, no Docker, npm, JDK or network needed
pytest --run-docker --run-node --run-java       # and the ones that need a daemon, npm or a JDK
pytest --run-docker --run-node --run-java --run-acceptance   # all 5721, the whole stack included
```

The proxy image's start-up tests use this machine's `envsubst`, which
gettext provides (`brew install gettext`, `apt install gettext-base`) and the
nginx image carries; without it they skip, saying so.

| Directory | Covers |
|---|---|
| [`tests/data_gen/`](../tests/data_gen) | The generator: calendar, dimensions, facts, validation, CSV/SQLite writing, and `generate_data.py` as a script |
| [`tests/agent/`](../tests/agent) | The agent: config, prompts, the LangGraph pipeline, the model router -- the table built from catalogs made to show each rule, the fallback chain, and every rung rule on its boundary, then again inside the pipeline, with the trace naming each call's model -- the tools, both retrievers, the ensemble fusion, the answer contract and the Completeness Reviewer -- rule by rule on hand-built rows, then again on real ones from the live database -- read-only enforcement, least privilege -- what the reader role can and cannot do, asked of a live catalog, including that it may become a signed-in person for a transaction and inherits nothing from anyone -- and the MLflow trace every run writes, read back as a tree from a fake MLflow: a span per agent named as the architecture names it, the four concurrent retrievers under their own run, a repair as a second generation, a routed call that fell back as two calls, and a server that is down, comes back, or refuses a verdict costing nothing but the trace |
| [`tests/api/`](../tests/api) | The REST server: the certificate policy and the switch that refuses a self-signed one, the job store, every route and status code, the event stream, the two published request limits checked against the lengths actually enforced, a real uvicorn bound to a loopback port over real TLS, the curl-only smoke script run against it for real, and a verdict recorded on, replaced on and withdrawn from the answer's trace -- after the staging database takes it, and never instead |
| [`tests/rag/`](../tests/rag) | The RAG pipeline: parsing the golden pairs, the BM25 index checked against an independent implementation, the pgvector storage layer, the semantic chunker the markdown one inherits from, both loader scripts -- their flags offline and their writes against a throwaway database created and dropped around each test -- and the seven shell scripts that build and publish the knowledge base, run against a fake `docker`, plus the two published images and the compose file that runs them |
| [`tests/docker/`](../tests/docker) | The Dockerfiles, the reader-role SQL, `docker-compose.yml` as `docker compose config` resolves it (including that the owner's credentials never reach the agent and that every setting the agent reads can be set through it), retrieval end to end inside the real containers, in a compose project of their own, and `start.sh`/`setup.sh`/`launch.sh` run against fake `docker`, `curl` and browser binaries -- including the browser opener each platform gets, chosen from a fake `uname` so the Linux and Windows branches run on a Mac too, the window each default browser is asked for, and Docker and Ollama started when they are down -- plus a structural check that every flag, warning and fatal message in the nine scripts that take them is exercised by some test, an inventory check that every shell script, Dockerfile and compose file git tracks -- and every service in both compose files -- is named by tests that mention it, every set of compose profiles a script runs or a document prints resolved by the real compose file, `docker/init_db.sh` run against fake `initdb`, `pg_ctl` and `psql`, and the measurement that says they all reach 100%, the API container reached over TLS by a curl-only container with nothing of this project in it, and the GUI container driven against a real API container on a private network, over HTTPS verified against the certificate that API wrote -- and the twelve tags `setup.sh` pins, asked of Docker Hub: published, for both architectures, and at this checkout's version, and the three dataset images it pins, for both architectures -- and MLflow: its two services as compose resolves them, one version across the server and both clients, and tracing against a real server started from the pinned image on a private network under the name `setup.sh` writes -- the pipeline's trace read back by MLflow's own client, verdicts, the benchmark's runs, and the agent image's own tracing client doing everything the agent asks of it |
| [`tests/gui/`](../tests/gui) | The web interface: its TypeScript types compared field by field against the pydantic models they mirror, the proxy configuration in both of the places it exists -- the dev server and the page's template in the proxy image -- the event stream left unbuffered, and the GUI's own 340-test suite run from here |
| [`tests/java/`](../tests/java) | The desktop client: its Java records compared component by component -- and in order, because records are positional -- against the pydantic models they mirror, the pom's pins and its coverage gate, the image that cross-builds its jar, and the client's own 411-test Java suite run from here |
| [`tests/review/`](../tests/review) | The feedback system: rendering a golden pair against the rules the loader actually enforces, the promotion path round-tripped through the loader's own parser on a real copy of the real question document, the whole HTTP surface against a fake repository, the staging schema and its row-level policies asked of a live Postgres -- including everything the public process must *not* be able to do -- a reviewer's corrected SQL validated against the live retail database, including a writing CTE the database itself refuses, the corrections and completions stores and their vectors in a real pgvector Postgres, a judgement taken back -- a promoted pair withdrawn from a real copy of the document and checked by the loader's parser, a fix deleted with its vector, the promotion log cleared and the row handed back to the public process -- the compose wiring that no single file shows -- every setting the service and its proxy read, and nothing either does not -- and the review interface's own 182-test review GUI suite run from here |
| [`tests/curate/`](../tests/curate) | The curation interface: its TypeScript types compared field by field against the review service's curation models, its dev server and its page's template agreeing on what they proxy, the project's pins and coverage gate, the compose wiring both ways -- every setting it reads, and the review service's token reaching it and never a browser -- and its own 108-test curation GUI suite run from here. The service behind it is in `tests/review/`: each snippet kind validated against the live retail database, every curation route, the snippet document round-tripped through the loader's parser, and golden pairs and fixes added and removed directly |
| [`tests/console/`](../tests/console) | The SQL console: the agent's gates in the agent's order against a scripted database -- what is refused before the database is asked, the read-only fence, the cost judged as the planner gate judges it -- every route and error code, the certificate it presents and refuses to start without, the real retail database as the real reader, including a write the transaction refuses and a timeout, the compose wiring both ways -- the agent's own URL and limits, one credential, loopback ports -- four real containers on a private network, the interface's types against the models, and its own 157-test console GUI suite run from here |
| [`tests/auth/`](../tests/auth) | Sign-in: the session format -- signed, verified, refused in each way it can be wrong -- and the guard every service puts in front of its routes, with its origin check and its roles re-read from Postgres; the auth service's settings, keys, throttle, sign-in against the database, the role sync's plan, every route and status code, and the directory page's API; the four sign-in services as compose resolves them; the directory page as a project, its types against the service's models and its own 61-test directory GUI suite; and, behind `--run-docker`, sign-in end to end -- a real retail database, directory and MLflow on a private network: the first administrator, a person added on the page who reads as their own role and stops being able to once removed, the database prepared again on a later start changing no membership, and MLflow's front door |
| [`tests/ldap/`](../tests/ldap) | The directory: its settings and both modes, the CSV and LDIF it loads, every operation on people and groups against ldap3's in-memory directory, the `slapd.conf` it renders, its certificate, the first start, the replica's copy -- paged, nested groups resolved, a copy that would empty it refused -- and the supervisor; and, behind `--run-docker`, the image, slaptest on both modes' configuration, a replica copying a second directory end to end, and -- as compose leaves it -- a certificate volume another image's container was created on last still written, with nothing running as root |
| [`tests/security/`](../tests/security) | The security tier (6.1): what the stack exposes by default, read from the files that decide it -- every store on this machine only, no password baked into the dataset image and its start refusing clear text and the superuser over the network, sign-in's database rules TLS-only and the auth service verifying the database, every TLS server its own key and no container another's, sign-in on by default in every service's own code, no secret on a script's command line -- every route in every service that does something carrying a guard, walked from each application's own route table, and every wire model in the four contracts refusing a field it does not declare -- and since 6.2 every route on a router that carries its guard and none added past one, every image naming its account or dropping root by a route the test can point at and every key handed to its service's account, every Python image installing a hash-checked lock, and every broad `except` saying why -- and since 6.3 every container read-only, capability-less but for the few given back and capped in memory and processes, no secret in any service's environment and every secret compose mounts one `setup.sh` writes, each database's socket shared with the dbprep one-shot alone, no health check that skips verifying, and every base and stock image pinned by digest (`tools/pin_images.py`, at 100%). [`SECURITY.md`](SECURITY.md) lists what each defends |
| [`tests/common/`](../tests/common) | The shared Python package (6.2, [`common/README.md`](../common/README.md)): settings read the same way by every service, the error families and the one envelope, the readiness an operator and anyone else are each shown, dropping root -- to an account, or to whoever owns a mounted directory -- and a person's name in a transaction's `application_name` -- and since 6.3 a URL with its password from the file beside it, the ceilings on each service's role, and the health check that verifies the certificate it is answered with |
| [`tests/ops/`](../tests/ops) | The dbprep one-shot (6.3, `common/nl2sql_ops/`): against a fake connection that records each statement and answers what each question asks -- the retail database's extensions, the reader and its ceilings, the sign-in schema and role sync, the sign-in block of `pg_hba.conf` written through the server and checked before it is reloaded, put back when it does not parse; the runtime stores' databases, owners and pgvector; every login's password; the report and the snippets' state; each way a run fails, and what it says -- and behind `--run-docker` against a real Postgres over its socket, run twice to show nothing changes the second time -- and the service as compose declares it, both ways: every setting it is given read, every setting it reads settable but the six the stack fixes, each socket mounted where its settings look |
| [`tests/proxy/`](../tests/proxy) | The proxy image (6.3, [`proxy/README.md`](../proxy/README.md)): its start-up script sourced the way the nginx entrypoint sources it, for every page -- the token header only with sign-in off, each upstream verified or plainly not, MLflow's sign-in, the page's own listener, each way it stops a page saying why -- and its health check, against a `curl` that says what it was asked -- and, once, what every page's template shares: the site rules, each page's read timeout its own setting, the token added where a page has one |
| [`tests/web/`](../tests/web) | The shared web package (6.2, [`web/README.md`](../web/README.md)): every page taking it in the same way -- its Vite, Vitest and TypeScript configuration, its image -- and the desktop client unescaping the agent's text by the same rule. Its own tests run in every page's suite |
| [`tests/docs/`](../tests/docs) | These documents and the architecture diagrams, checked against the code they describe -- including every place the repository writes its own version down, which a release has to move together -- the phrases the adversarial reviews retired, absent from every document and comment, and the newest specification's security blueprint naming files and tests that exist -- and the adversarial reviews' figures: each committed draw.io file held to the script that computes it, its SVG and PNG exports to the file, and the `_enhanced` review documents to the originals they add figures to |
| [`tests/benchmarks/`](../tests/benchmarks) | The benchmark's own ground truth: every reference query executed against the dataset, the scorer tested against both kinds of mistake it could make, and its MLflow runs -- one per configuration with its parameters, metrics and report, every question's trace in it and judged, a run cut short ended as such, and a host without the runs API told so |
| [`tests/models/`](../tests/models) | The calibrator, against fake models that answer by what each prompt says -- which probe counts toward which rung, what counts as right, the reference's reflection as the key, the cold load and resident size read from the host's own API, and what reaches the catalog -- and the model catalog builder, run against a fake Ollama host answering exactly what the real one did on 2026-09-27 and a fake ollama.com serving that day's pages: every model catalogued from the host's own answers, the MLX builds described by `/api/show` where `/api/tags` says nothing, a local build described by its parent's page, the prior checked against the table the spec worked by hand and then rule by rule on each boundary, every way of naming a host, measurements carried across a rebuild only for unchanged weights on the same host, every way the host or the site can fail to answer, borrowing the system's certificate authorities when Python has none -- over real TLS, and never by turning verification off -- and the committed catalog re-derived from its own facts; plus, behind `--run-docker`, the real host and the real library page |

The 758 tests behind `--run-docker` are the ones that need a working daemon:
they build the agent, GUI, console, desktop, directory and auth images and run them, resolve the real
compose file, query the live databases -- found as compose finds them, with the passwords in
`secrets/` ([`tests/live_stores.py`](../tests/live_stores.py)), so a database that refuses the login is a
failure rather than a skip -- trace into a real MLflow server, and ask Docker Hub whether the
tags `setup.sh` pins were really published -- which also needs the network,
and skips rather than fails without it. Two more ask the chat host and
ollama.com what the model catalog is built from. The 51 behind `--run-node`
need npm, and run the five GUIs' own suites. The 6 behind `--run-java` need
Maven and a JDK of 21 or later, and run the desktop client's. Three flags
rather than one because the three needs are different -- a clone with Docker
but no npm should still be able to run every container test, a GUI developer
with npm and no Docker daemon should still be able to run the interface's,
and neither of them should be asked for a JDK to run the Python ones.

The 10 behind `--run-acceptance` are the **acceptance tier**
([`tests/acceptance/`](../tests/acceptance)), and they are what a release passes
before it is published. They copy this checkout, start it as a stack of its
own beside any other -- `NL2SQL_INSTANCE` names its containers, volumes and
compose project, every port is a free one -- with the two commands a user
types, `./setup.sh --build-all --review --curate --console --mlflow --desktop`
and `./start.sh --review --curate --console --mlflow --no-browser`, and then
use it: the generated administrator signs in on every page, a question goes
through the web interface, its verdict through review into the golden set,
SQL through the console, and MLflow is asked for the question's trace through
its front door. Four of the ten are the four defects 6.0 shipped with every
other tier green: a container that restarted for ever, a database nobody
could sign in to, a lockout of everybody behind one address, and the desktop
client's classes against a server that asks who it is. Since 6.3 one more
reads the running stack itself: no process in any container is root, no
container can write its own image, and no secret is in any container's
environment. Everything is removed
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

Twenty-eight of those 758 also need the **embedding host**: a local Ollama
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
[`tests/docker/test_compose_rag_integration.py`](../tests/docker/test_compose_rag_integration.py)
need Ollama too, but reach it the way the agent container does -- through
compose's settings -- so they are not in that count; they skip, with the same
reason, when it is down. The model catalog's live test reads
`TEST_OLLAMA_BASE_URL` for the chat host, for the same reason, and skips when
that host cannot be reached.

The 80 database-backed tests in [`tests/rag/`](../tests/rag) need the stores
but **not** the embedding model: they exercise the storage layer with
synthetic vectors, which makes the distances predictable rather than merely
plausible. Each one runs against a throwaway database created and dropped
around it, so the published golden pairs and embeddings in the running
containers are never touched.

## Coverage

```bash
COVERAGE_FILE=$PWD/.coverage COVERAGE_PROCESS_START=$PWD/.coveragerc \
  coverage run --rcfile=.coveragerc -m pytest --run-docker
coverage combine && coverage report --show-missing --skip-covered
```

**100% of every Python file in the repository, statements and branches** --
16,096 statements and 3,782 branches, none missed. `coverage report` fails below
that (`fail_under = 100` in [`.coveragerc`](../.coveragerc)) rather than printing
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
and without [`.coveragerc`](../.coveragerc) turning on subprocess measurement the
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

**100% of statements, branches, functions and lines** across 340 tests, with
only `main.tsx` excluded -- it mounts React onto a DOM element that exists
only in a browser, and a test pins the exclusion list so nothing else joins
it. The review interface is held to the same thresholds in its 182-test
review GUI suite: it decides what goes into the question set the agent is
measured against, so a partially tested path there is a partially tested
benchmark. The console's is held to them in its 157-test console GUI suite:
what it shows is what someone decides the agent did wrong from. The curation
interface's is held to them in its 108-test curation GUI suite, for the same
reason as the review interface's: it writes what the agent learns from. And
the directory page's in its 61-test directory GUI suite: it makes the people
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

**100% of lines and branches** across the desktop client's own 411-test Java
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

### The parts a coverage report cannot see

Seventeen shell scripts, one nginx entrypoint fragment, two compose files,
thirteen Dockerfiles and five nginx templates, none of them Python. They are covered by
reading and by running -- and, since nothing in coverage.py can see a shell
script, by a measurement of their own:

* **Every one of them runs, and all of them reach 100%.**

  ```bash
  python -m tests.shell_coverage
  ```

  `start.sh`, `setup.sh` and `launch.sh` are driven against fake `docker`,
  `curl`, `sleep`, `uname`, `grep`, `systemctl`, `ollama`, `defaults`,
  `osascript`, `xdg-settings` and browser binaries; the seven scripts in
  [`rag/`](../rag) the same way; `docker/apitest/smoke.sh` against a real HTTPS
  server; `docker/init_db.sh` -- which otherwise runs only inside `docker
  build` -- against fake `initdb`, `pg_ctl` and `psql`;
  `docker/migrate_store.sh` against fake `psql`, `pg_ctl`, `pg_dump` and
  `pg_restore`; the dataset image's `docker/entrypoint.sh` against a fake
  stock entrypoint, `openssl`, `psql` and `gosu`; and the proxy image's
  `10-nl2sql-proxy.envsh` and `health.sh`, for every page, as the nginx
  entrypoint and Docker's health check run them.
  That tool re-runs those suites with `bash -x` on and counts which commands
  the traces mention -- **1734 of 1734**.

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
  [`tests/docker/test_script_coverage.py`](../tests/docker/test_script_coverage.py):
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
  [`tests/api/test_smoke_script.py`](../tests/api/test_smoke_script.py): bash,
  curl and jq against a live HTTPS server built from `create_app` with a
  scripted pipeline. No Docker and no model, so every one of its paths runs on
  an ordinary `pytest` -- each of the three ways it decides to trust the
  server, a server that is up but not ready, a question that fails, a stream
  that carries nothing, and the refusals that make its two exit codes mean
  something.
* **Both compose files** are checked in both directions for every service
  that takes settings: nothing is set that the code never reads, and nothing
  the code reads is missing from it. That holds for the agent's own settings
  against `config.py`, the API's against `api/settings.py`, the console's,
  the review service's and the auth service's against their own settings,
  the directory's against its settings and its replica, every page against
  what the proxy image reads for it -- and each page's settings by the name
  `.env` gives them, all at once -- the dbprep one-shot's against
  `nl2sql_ops` (but six the stack itself fixes, named), MLflow's server
  against its entrypoint, the migration's against what `launch.sh` hands it,
  the smoke script's against the script itself, and -- in [`rag/docker-compose.yml`](../rag/docker-compose.yml) -- the two
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
[`tests/docker/test_shell_coverage_tool.py`](../tests/docker/test_shell_coverage_tool.py),
because a mistake in a measurement is a number in a document that nobody can
tell is wrong.

Getting the RAG pipeline to 100% turned up a real defect the same way.
`vector_store.search()` bound its query vector as a Python list, which
Postgres reads as `double precision[]` -- a type with no `<=>` operator at all,
so the function raised for every caller. It had only ever been reached from a
README example. The fix is the same text-literal cast the agent-side
retrievers use, and the regression is pinned by a test.
