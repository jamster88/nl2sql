# Tracing

*Part of the [nl2sql documentation](../README.md#documentation).*

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

**Under the ensemble (7.0) a question is still one trace.** Its root span's
children are the ensemble's own steps, and each run of the pipeline is a
span beneath them, holding that run's agents as above:

```
nl2sql                      AGENT       the question in, the delivered answer out
  Supervisor                AGENT       the screening, once for the question
    qwen3.8-256k:latest     CHAT_MODEL
  Paraphraser               AGENT       the rewordings, in one call
    <model>                 CHAT_MODEL
  Fidelity Gate             GUARDRAIL   each rewording that passed the checks in
    <model>                 CHAT_MODEL  code, read by the Supervisor (F4)
    ...
  Wave Planner              TASK
  Candidate Runs            CHAIN       a line per run: its wording, outcome and SQL
    Candidate 0             AGENT       the question as asked
      Supervisor            AGENT       screened above, so no model call here
      Schema Retriever      RETRIEVER
      ...
      Answer                TASK
    Candidate 1             AGENT       the first rewording, the same again
    ...
  Agreement                 EVALUATOR   each run marked: could it vote, its group
  Judge                     EVALUATOR   every group's answer, accepted or set aside
    <model>                 CHAT_MODEL  heavy, always: one call, before the vote
  Vote                      EVALUATOR   the accepted runs counted, the winner
  Fusion                    TASK        the run chosen
  Answer                    TASK        what was delivered
```

The trace gains three tags, `nl2sql.agreement` (`3/4 majority`: agreed of
run, and the level), `nl2sql.candidates` and `nl2sql.judge` -- what the
Judge did: `accepted`, `set aside`, `overruled`, `accepted none`, `failed`
or `not asked` -- and its attempts and model calls are read from the runs
-- the delivered run's attempts, every run's calls with the ensemble's own.
Searching ``tags.`nl2sql.judge` = 'overruled'`` finds the questions where the
Judge changed what the runs would have delivered.

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
[`benchmarks/README.md`](../benchmarks/README.md#mlflow).

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
-- and the benchmark -- reach it through `nl2sql-mlflow-proxy`, the proxy
image's `mlflow` page, nginx over HTTPS with a certificate of its own, which asks the auth service about every
request. Someone not signed in is sent to sign in; someone signed in who is
not in `nl2sql-reviewers` or `nl2sql-admins` (`MLFLOW_ALLOWED_ROLES`) is
turned away; an MLflow client signs in with `MLFLOW_TRACKING_USERNAME` and
`MLFLOW_TRACKING_PASSWORD`. The agent traces to the server directly, on the
stack's own network. It is published on this machine only unless
`MLFLOW_BIND_ADDRESS` says otherwise; with sign-in off `launch.sh` warns
when it is. See [Sign-in](sign_in.md#sign-in).

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
| `MLFLOW_DB_USER`, `MLFLOW_DB_NAME` | `mlflow` | The store's role and database, each one setting that both containers read. Its password is `secrets/mlflow_db_password`, from which the server's entrypoint builds the store's URL -- no URL with a password in it is on its command line or in its environment (6.3) |

[`agent/README.md`](../agent/README.md#tracing-mlflow) has the agent's two
settings and the reasons.
