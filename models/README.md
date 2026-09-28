# Model catalog

What the Ollama host serves, and which tasks -- at which complexity -- each
model is suited to. It is the list the Model Router of
[arch5.2](../multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_2.md) (section 15)
routes from: compose mounts [`catalog.json`](catalog.json) into the agent and
the API, and [`agent/README.md`](../agent/README.md#model-routing-arch52) says
what the router does with it. Two scripts make it:

```bash
python3 models/build_catalog.py                  # the host the agent uses -> models/catalog.json
python3 models/build_catalog.py 192.168.1.20     # any Ollama host, by its address alone
python3 models/build_catalog.py --no-library     # the host's own answers only; nothing goes to ollama.com
.venv/bin/python models/calibrate.py             # measure what each model is suited to
```

`build_catalog.py` lists the host and says what each model *is*: its facts,
its library page, and a prior -- a guess at what it is suited to. Until
`calibrate.py` has measured a model, the router does not route to it on that
guess alone, so the committed, uncalibrated catalog sends every call to
`OLLAMA_MODEL`, as v5.1 did.

It needs only the standard library and Python 3.9 or later, so it runs with
the `python3` a Mac already has, outside any image or virtualenv. It takes a
few seconds. Re-run it whenever the host's models change, read the diff, and
commit [`catalog.json`](catalog.json) like code: the router will route from
what is committed, not from what the host happens to hold that day.

| Argument | Default | |
|---|---|---|
| *address* | `OLLAMA_BASE_URL` from the environment, then from `.env`, then the agent's own default | the Ollama host: an IP address (`192.168.1.20`), a name (`gpu-box`), `host:port`, an IPv6 address, or a URL. Without a scheme it is http, and without a port Ollama's own, 11434; a URL with a scheme is taken as written, so a proxy on 443 works |
| `--host` | | the same, as a flag |
| `--reference` | `OLLAMA_MODEL`, the same way; then `qwen3.8-256k` | the model the agent runs every call on today, which calibration will measure the others against |
| `--out` | `models/catalog.json` | where to write it |
| `--no-library` | off | skip ollama.com |
| `--timeout` | 10 | seconds per request |

The host follows compose's own precedence -- the shell's environment, then
the `.env` that `setup.sh` writes -- so with no flags the catalog describes
the host the stack is pointed at. It exits 0 when it has written the catalog,
1 when the host did not give it a model list (nothing is written, and the
previous catalog is left whole), and 2 for a flag it does not know.

## What it does

Steps 1 to 4 of the spec's section 15.1:

| Step | Asks | Records | When it fails |
|---|---|---|---|
| 1 Inventory | `GET /api/tags` | every model's name, digest, size and date | the run stops: there is nothing to catalogue |
| 2 Facts | `POST /api/show`, per model | capabilities, context length, exact parameter count, experts of a mixture, parent model, quantisation, baked-in defaults, license | the model is catalogued from `/api/tags` alone, with `show_error` saying why |
| 3 Library | `ollama.com/library/<name>`, then its parent's | the description, capability badges, sizes, and keyword tags from the description | `library.unavailable` says why; after the site has failed once it is not asked again |
| 4 Prior | the rules below | for each task, the highest rung presumed suited, and the reasons | -- |

Step 2 is not optional in practice. The MLX builds (`gemma4:12b-mlx` and its
siblings) leave their parameter size, family and context empty in
`/api/tags`, and only `/api/show` says what they are. Step 3 is why
`qwen3.8-256k` -- `qwen3.8` with a bigger window baked in, and no page of its
own -- is described by `qwen3.8`'s page.

Step 5 is `calibrate.py`, [below](#calibration). A rebuild keeps what it
measured for every model whose digest is unchanged -- the digest is what says
the weights are the ones measured -- so pulling one new model does not throw
away an hour of calibration on the others. Measurements of another host are
never kept.

## The catalog

One entry per model, sorted by name. The top of the file says which host it
describes and when, and `schema` is the version of this layout, so a router
can refuse a file it does not understand. Schema 2 records where each task's
`suited` came from, since calibration can measure some of a model's tasks and
not others.

```json
{
  "schema": 2,
  "host": "http://192.168.10.82:11434",
  "ollama_version": "0.34.4",
  "built": "2026-09-28T00:56:41Z",
  "reference": "qwen3.8-256k:latest",
  "reference_on_host": true,
  "calibrated": null,
  "rungs": ["light", "standard", "heavy"],
  "tasks": ["supervisor", "generator", "reflection", "narrator", "repair"],
  "task_budgets": {"supervisor": 8192, "generator": 16384, "reflection": 8192, "narrator": 8192, "repair": 16384},
  "models": [
    {
      "name": "qwen3.8-256k:latest",
      "digest": "ce7194a2d411...",
      "chat": true,
      "facts": {"family": "qwen35", "parent_model": "qwen3.8:latest", "parameter_count": 27320697856,
                "quantization": "Q4_K_M", "context_length": 262144,
                "capabilities": ["completion", "thinking", "tools", "vision"], "...": "..."},
      "facts_from": "show",
      "library": {"page": "https://ollama.com/library/qwen3.8", "badges": ["vision", "tools", "thinking"],
                  "tags": ["code"], "...": "..."},
      "prior": {"supervisor": "heavy", "generator": "standard", "reflection": "heavy",
                "narrator": "standard", "repair": "heavy",
                "reasons": ["27.3 B parameters: supervisor and reflection heavy (7 B and over); generator, narrator and repair standard (10 to 40 B)",
                            "a reasoning model: repair one rung up, and routed with thinking off everywhere"]},
      "measured": {},
      "suited": {"supervisor": "heavy", "generator": "standard", "reflection": "heavy",
                 "narrator": "standard", "repair": "heavy"},
      "suited_from": {"supervisor": "prior", "generator": "prior", "reflection": "prior",
                      "narrator": "prior", "repair": "prior"}
    }
  ]
}
```

A rung names the top of what the model is suited to: `"standard"` means the
light and standard rungs of that task, not the heavy one. `null` means the
model is no candidate for the task at all. The `digest` is there so a model
pulled again with new weights reads as a different model.

## The prior

The rules are section 15.1's. Each adjustment is a rung up or down; they are
summed and the sum is clamped to the ladder, so their order cannot change
the result.

| Fact | Effect |
|---|---|
| parameters: under 10 B / 10 to 40 B / over 40 B | generator, narrator and repair light / standard / heavy |
| parameters: under 7 B / 7 B and over | supervisor and reflection light / heavy |
| parameters unknown | light for every task, and no promotions |
| a mixture of experts | placed by total parameters, with a note that it will measure faster than its size |
| a code model | generator and repair one rung up |
| `thinking` capability, or *reasoning* in the description | repair one rung up; routed with thinking off everywhere |
| quantised below 4 bits | every task one rung down |
| no `tools` capability, or capabilities unknown | supervisor, reflection and narrator one rung down |
| a context window below a task's budget | no candidate for that task |
| an embedding model (the capability, or a BERT family) | no candidate for any task |
| served by another host (an Ollama cloud model) | no candidate for any task |

Three of those rows are this script's reading of the spec rather than its
words:

- **A code model is one that says it is for code.** The spec says "*code*
  in the library description". Every family on the host today names coding
  among its strengths -- qwen3.8's page lists "coding, professional work,
  research" -- so that reading would promote the reference model and a 12 B
  gemma4 to heavy generators, and contradict the spec's own worked table.
  So a model counts when its name has `code` in it (`qwen3-coder-next`,
  `sqlcoder`, `granite-code`) or its description says what it is for:
  "coding-focused", "a code model", "for coding", "codebases", "software
  engineering". The `code` keyword tag is still recorded for every mention.
- **A model of unknown size is promoted for nothing.** A promotion adjusts
  the rung the size gave. A thinking code model of unknown size might be
  1.5 B.
- **A cloud model is no candidate.** The spec does not mention Ollama's
  cloud models, which a host lists and another machine runs. Routing to one
  would send the question and the schema off the network, which should be
  a decision rather than a default.

The spec also demotes "a format the host runs slowly". Nothing the host says
reveals that, so it is left to calibration.

**The prior is a starting point, not a routing table.** By size alone, a
2023 `mixtral:8x7b` outranks the reference model as a heavy generator. That
is what calibration exists to correct, and why the router routes on measured
suitability only, unless `MODEL_ROUTE_ON_PRIOR=true` says otherwise.

## Calibration

```bash
.venv/bin/python models/calibrate.py                                   # every chat model, every task
.venv/bin/python models/calibrate.py --models gemma4:12b-mlx mistral:7b --tasks supervisor narrator
.venv/bin/python models/calibrate.py --questions B01 B07 B15 --host-memory 128G
```

Section 15.5 of the spec. It needs the agent and the stack, so it runs where
the benchmark runs -- the repository's virtualenv, against the stack's
published ports -- and against the host the catalog was built for. The
reference model answers the benchmark first; then each model is run through
one probe per task:

| Task | Probe | Right when |
|---|---|---|
| generator | the benchmark questions, the pipeline run with `MODEL_ROUTE_GENERATOR` pinned to the model | execution accuracy, as the benchmark scores it, with every draft the model's own |
| supervisor | the benchmark questions, which should all proceed, and [`probes/triage.json`](probes/triage.json) | the verdict is the expected one |
| reflection | each benchmark question's accepted result, replayed | the model agrees with the reference model's own reflection |
| narrator | each accepted result, narrated again | the audit passes every claim and every assumption is stated |
| repair | [`probes/repair.json`](probes/repair.json): six real Postgres errors the classifier cannot place | the diagnosis names the fault |

Each probe counts toward the rung the router would route it at -- a
benchmark question toward the rung its score gives it, a flagged triage case
toward standard -- and for each rung the catalog records correct out of
tried and the P50. A model is suited to a rung when it scores within one
question of the reference model on the same probes -- the Supervisor within
none: in the first routed run a light model one question behind the
reference refused a valid benchmark question as out of domain, and a refusal
is no answer, with nothing downstream to retry it. The first rung a model
fails stops the climb, since a model suited to heavy is suited to everything
below. The reflection is scored against the reference rather than a key:
whether a result is fleshed out is a judgement, and agreeing with the model
the pipeline was tuned on is what suited means there.

It also times each model's cold load and reads how much memory it holds
once loaded (`--no-load` skips both), and with `--host-memory` it warns when
the models the table routes to cannot all be resident at once. The catalog is
rewritten after every model, so an interrupted run keeps what it finished,
and the routing table the agent would build from it is printed at the end.
The generator probe is the one that takes real time: a full benchmark run
per model, about a quarter of an hour on the reference model.

## HTTPS on a Mac

python.org's macOS builds of Python ship with no certificate authorities
until their "Install Certificates" script has been run, and every https
request then fails verification. When Python has none of its own -- and
`SSL_CERT_FILE` is not set -- the script borrows the operating system's
bundle (`/etc/ssl/cert.pem` on macOS). Verification is never turned off: with
no bundle anywhere, the library step fails and says so.

## Tests

[`tests/models/`](../tests/models) runs the scanner against a fake host that
answers exactly what the real one answered on 2026-09-27, and a fake
ollama.com serving that day's descriptions and badges, and the calibrator
against fake models that answer by what each prompt says. The rules are then
tested one at a time, on models built to sit on their boundaries. The
committed catalog is checked the way the diagrams are: every prior in it must
be what the rules make of its own facts, so a hand edit, or a rule changed and
never re-run, fails. Two more tests, behind `--run-docker`, ask the real host
and the real ollama.com, so an Ollama upgrade or a redesigned library page
fails there rather than producing a thinner catalog. They read
`TEST_OLLAMA_BASE_URL` rather than the agent's `OLLAMA_BASE_URL`, and skip
with the reason when the service cannot be reached.
