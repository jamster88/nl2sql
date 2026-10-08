# Multi-Agent NL2SQL v7: the risks, phase by phase

**Status:** a reading of
[`Multi-Agent_NL2SQL_arch7_implementation.md`](Multi-Agent_NL2SQL_arch7_implementation.md)
section 11 ("Risks and the ways round them") against its section 10 (the
build order), written 2026-10-07 against the tree at 6.3.0, before anything
is built. Section 11 names eight risks and the way round each; section 10
names six phases and a release, each a merge candidate on its own. Neither
says which phase meets which risk. This document does: for every phase, the
risks that are live in it, what the exposure looks like there, how that
phase builds the way round, what holds it, and what to check before the
phase merges.

Precedence: [`Multi-Agent_NL2SQL_arch7.md`](Multi-Agent_NL2SQL_arch7.md)
decides the design, the implementation specification decides the build,
and this document adds nothing to either except the placement -- with one
exception. Placing the eight surfaced a handful of things section 11 does
not say (section 10 here). A specification in this folder is never edited
in place, so they are recorded here, for the build to act on and the next
specification to carry.

**The vocabulary.** A cell of the matrix, and a paragraph under a phase,
uses these words for the relation between a risk and a phase:

| Word | Meaning |
|---|---|
| lands | the code that creates the exposure is written in this phase, and so is its way round -- section 11 puts them together, and so does the build |
| held | a test or the acceptance criterion of this phase holds the way round |
| widens | the phase adds another instance of the exposure under a way round already built |
| live | the first phase in which a deployment can meet the exposure in use |
| stated | the way round is, in part, a sentence a deployer reads, and this phase writes the document that carries it |
| seam | the phase cuts the seam a later phase's way round rests on, with nothing yet passing through it |
| early | the way round can be built and measured before the exposure exists |
| seen | the phase is where a person first sees the way round working, or failing |
| verify | a line in the release's verification |
| -- | nothing in the phase touches it |

## 1. The eight risks, named

Section 11's bullets, each given a short name for the matrix and the
sections below. The exposure and the way round are section 11's,
compressed; the module is where the implementation specification puts the
code.

| Id | Risk | The exposure | The way round | Where |
|---|---|---|---|---|
| R1 | Worker threads | the inner graph reads its progress callback from a contextvar (`graph.py`'s `_progress`) and MLflow its active span from the OpenTelemetry context, also a contextvar; the ensemble runs screenings and candidates in a thread pool, and a fresh thread has neither | every item is submitted as `copy_context().run(fn, ...)`; `test_ensemble_tracing.py` holds it with two workers | `ensemble.py` (the pool), `tracing.py` |
| R2 | One wrapper, two graphs | `traced_node` must strip `_detail`, `_model_calls` and `_route` exactly as `_traced` does today, or a private key reaches the outer state and the strict wire fails | one function for both graphs; the existing tracing tests move to it | `graph.py` |
| R3 | The state in the state | `Candidate.state` is the inner `AgentState` whole; a remembered job holds up to eleven of them; `to_jsonable` renders them all | the job store's memory is a line in the release's verification | `ensemble_state.py`, `api/jobs.py`, `api/translate.py` |
| R4 | The catalog's schema check | `load_catalog` refuses a mismatched schema today; schema 2 must be read leniently by a deliberate branch, and any other schema still refused | the branch with its own test; schema 2 routes the two new tasks to the anchor | `router.py`, `models/` |
| R5 | The reader's connection limit | `nl2sql_reader` may hold sixty sessions (`common/nl2sql_common/roles.py`); `OLLAMA_PARALLEL_CALLS x API_MAX_CONCURRENCY` candidates can hold one each | a check at start warns when the product exceeds half the limit; nothing is forced | `config.py`, `api/app.py` |
| R6 | Ollama swaps | with several slots, two candidates at different rungs ask the host for two models at once; a host that holds one swaps on every call | the document: raise the slots only for a host with the memory for its resident models times the slots | `agent/README.md` |
| R7 | Paraphrases as prompt text | a rewording is model output that becomes a candidate's `question` | the deterministic checks first, with no model call, then the Supervisor's screening of the rewording (F4); a rewording the Supervisor refuses is never run, and a test holds it | `fidelity.py`, `ensemble.py`, `supervisor.py` |
| R8 | The review service's migration | the staging table gains a column, added on the review service's start | `ADD COLUMN IF NOT EXISTS` on a table the review service's role owns needs no superuser; a test runs it twice on a live store | `review/nl2sql_review/store.py` |

## 2. The matrix

| | Phase 0 -- the premise | Phase 1 -- plumbing | Phase 2 -- rewordings, vote, selection | Phase 3 -- fusion, second wave | Phase 4 -- Judge, calibration | Phase 5 -- surfaces | Release |
|---|---|---|---|---|---|---|---|
| R1 worker threads | -- | lands, held | widens, live | widens | -- | seen | verify |
| R2 one wrapper | -- | lands, held | widens | widens | widens | -- | -- |
| R3 state in the state | -- | lands, held | widens | widens (the widest) | -- | widens (the store) | verify |
| R4 schema check | -- | -- | lands, held | -- | widens, held | stated | live |
| R5 connection limit | -- | lands, held | live | widens | -- | stated | -- |
| R6 Ollama swaps | -- | lands | live | widens | widens | stated | verify |
| R7 paraphrases as input | early (S1) | seam | lands, live, held | widens | widens (the Judge) | stated | -- |
| R8 the migration | -- | -- | -- | -- | -- | lands, held | live, verify |

Read down a column for a phase's risks, across a row for a risk's path.
Four of the eight land in Phase 1, which is why that phase "changes no
answer": it is where the plumbing the later phases ride on is proved with
nothing riding on it. Phase 2 is the one phase with a risk of its own (R7)
that no earlier phase can hold. Phase 5 is where three of them turn into
sentences a deployer reads. The release verifies two numbers and rehearses
two upgrades.

## 3. Phase 0 -- the premise

What it builds: `benchmarks/paraphrases.py` (three hand-written rewordings
of each benchmark question), `--paraphrase-set`, and
`tests/benchmarks/test_paraphrases.py`; the sixty wordings run through the
live 6.3 stack as `single`; stability recorded in `docs/benchmark.md`. No
agent change.

**No risk of section 11 is live.** Nothing runs in a worker thread (R1);
there is no outer graph (R2) and no outer state (R3); the catalog, the
gate and the staging table are untouched (R4, R5, R6, R8); and the
forty-five rewordings are a person's, not a model's (R7).

**R7, early.** Section 8 of the implementation specification has the
Phase 0 test check every rewording against `fidelity.check` with the
original -- F1 numbers, F2 literals, F3 polarity, F5 distinct -- and
section 10 builds `fidelity.py` in Phase 2. One of the two moves. The
deterministic checks are pure functions with no model behind them, so the
cheap move is `fidelity.py` (everything but `same_contract`, which needs
the Supervisor's reading) into Phase 0, with its own test file. That buys
Phase 0 a second number beside stability: how many of forty-five
rewordings a person wrote and checked by hand the deterministic checks
would have rejected. Section 3.4 says the word lists "grow from" the
paraphrase set; this is the first place they can. The alternative -- the
test asserting numbers and literals with code of its own -- is two
checkers that drift, the argument decision 26 made against two scorers.
(S1 in section 10.)

**Before Phase 0 merges:** the forty-five pass the deterministic checks,
or each false rejection is named with the word added for it; stability is
in `docs/benchmark.md` with the date and the configuration; the run was
`single`, so the number is the premise's and not the ensemble's.

## 4. Phase 1 -- plumbing that changes no answer

What it builds: `hostgate.py` and the gate around every routed call;
`compare.py` (the scorer moved); `ensemble_state.py`; `graph.py`'s
`traced_node`, `answer(state)` and `screened`; `ensemble.py` with the
Paraphraser not called and `plan_wave` planning candidate 0 alone;
`api/app.py` choosing the agent; the wire models, `translate`,
`ProgressEvent.candidate`, `Pipeline.ensemble`; the eleven settings in
`config.py` and compose; the CLI flags. Acceptance: the benchmark at
`ensemble` with no rewordings is 6.3's run to the answer, every API test
passes with `ensemble` present, the mirrors hold, and with one slot two
threads' calls never overlap.

Four risks land here, which is the point of the phase: the exposure is
built with nothing riding on it, so the way round is proved before Phase 2
puts four candidates through it.

**R1 lands, held.** Even one candidate runs through the pool: section 3.8
submits every item of `answer` to a `ThreadPoolExecutor`, and with one
worker "the pool is a loop in all but name" -- but the loop body runs on
the worker's thread, not the caller's. From this phase on the inner graph
never runs on the thread that holds the job's progress callback and the
root span. Two things have to travel. The progress callback travels twice
over: the pool copies the submitting context, and `Nl2SqlAgent.answer`
sets `_progress` again inside the worker to the candidate's own callback
(`candidate_progress(k)`), so progress would survive a missing copy. The
span would not: `tracing.agent_span("Candidate 0", ...)` nests under
whatever span is active in the thread it runs in, and in an uncopied
thread nothing is, so the candidate's run would open as a trace of its own
beside the question's, and the acceptance's "the trace nests one run"
would fail. That acceptance is R1's check in this phase.
`test_ensemble_tracing.py` holds it at two workers, so the copy is tested
as a copy and not as the main thread's own context by luck of the loop;
the sharper form of the test exercises the pool helper directly with two
fake items that each open a span and read `_progress`, and asserts both
are the submitter's. The two tags `finish_ensemble` adds are set after the
pool returns, on the main thread, and need nothing.

**R2 lands, held.** `traced_node` is `graph.py`'s `_traced` -- which pops
`_detail`, `_route` and `_model_calls` out of the node's update and into a
`TraceEntry` -- made a module-level function both graphs register their
nodes through. `screen` is the first outer node with a model call of its
own, the anchor's Supervisor call, so it returns all three private keys,
and the wrapper is exercised on the outer graph in full from the first
phase, not from Phase 4's Judge. If a key leaked it would reach
`EnsembleState`, `to_jsonable` and `translate`, and the strict wire (every
model a `Wire`, extras forbidden) would refuse the answer. Held by the
tracing tests moved to the shared function, by
`tests/api/test_ensemble_wire.py`, and by the acceptance's "every API test
passes with `ensemble` present". The check to add: one test that
`traced_node`, given a fake node returning all three keys and a field,
returns the field, the `trace` entry and nothing beginning with an
underscore -- one test for both graphs, since it is one function.

**R3 lands, held.** `Candidate.state` holds the inner state whole, and
`translate` copies the chosen candidate's `answer`, `sql`, `result`,
`chart`, `claims` and `audit` up into the outer state, so a Phase 1 job
holds roughly twice what a 6.3 job holds. `to_jsonable` meets a shape it
has not rendered before: a list of dataclasses each holding a dict whose
values are dataclasses (`QueryResult`, `ChartSpec`, the claims, the trace
entries). `--json` with `candidates` whole (`test_cli.py`) and the wire
test render it; `test_ensemble_state.py` should render a `Candidate` built
from a real inner state. The release's line is a comparison only if there
is a baseline: write down the JSON size of one job's state at one
candidate here.

**R5 lands, held.** The settings land in this phase, so the check at start
does too; section 11 does not place it, and this document puts it where
both numbers are known. `OLLAMA_PARALLEL_CALLS` is the agent's
(`Settings`) and `API_MAX_CONCURRENCY` is the API's (`api/settings.py`), so
the product is known in `api/app.py` at start and nowhere earlier; the CLI
has no concurrency and no check. The limit is read from
`nl2sql_common.roles.READER.connection_limit`, not written as a literal:
one home per fact is the second review's open item V6-47, and a literal
sixty would be a second home. Two bounds, not one: `database.py` builds
its engine with `create_engine(url, pool_pre_ping=True)` and nothing else,
so SQLAlchemy's defaults apply -- five pooled connections, ten more on
overflow, fifteen in all per process, and a thirty-second wait before a
pool error -- and a process meets fifteen before the role meets sixty. The
warning names both (S3). In this phase nothing can reach either: one
candidate per job holds what a 6.3 job holds. The check is about the
configuration and fires correctly on one no run can yet exercise. Held: a
test each way -- two workers and one slot silent; two workers and twenty
slots warns, naming both variables and both bounds.

**R6 lands; not live.** The gate lands at one slot. Swaps do not change:
with several slots and one candidate per job, two jobs' calls at different
rungs can be in flight together, which is what 6.3 does today without a
gate. What does change is the default's other edge: at one slot the API's
two workers, which 6.3 let call the host at once, take turns. A deployment
whose host serves two calls (Ollama's `OLLAMA_NUM_PARALLEL` at two) gets
one until it sets `OLLAMA_PARALLEL_CALLS=2`. Not a risk section 11 lists;
S2 in section 10, and a sentence in the release's upgrade note.

**R7, the seam.** No rewording exists yet, but the seam the way round rests
on is cut here: `screened` on `AgentState`, `new_state(screening=...)`, and
`_supervise` building the contract from the seed with no model call. From
this phase on a candidate can run without a Supervisor call of its own,
trusting a screening made for it. The invariant to hold from the first
day: a screened state is built only by the outer graph, from a screening
it made itself -- `screen` now, `screen_paraphrase` from Phase 2 -- and
never from anything a client sent. `screened` and `screening_fields` are
not on the wire (`AskRequest` is a `Wire`, extras forbidden), and
`new_state`'s keyword is the only way in. Held by `test_graph.py`
(`screened` skips the Supervisor's call) and by the wire models'
construction.

**R4 and R8: not live.** `router.py` gains the gate and nothing else; the
two pin settings are read and validated by `parse_pin` but used by nothing
until Phase 2, which is worth a comment beside them so no one wires a pin
for a task the router does not yet have. The staging table is untouched:
the snapshot's `ensemble` lands with its column, in Phase 5.

**Before Phase 1 merges:**

- the pool test at two workers: the span's parent and the progress callback are both the submitter's;
- the strip test on `traced_node`; the moved tracing tests green;
- `to_jsonable` on a `Candidate` with a real inner state; the size of one job's state written down as the baseline;
- the check at start reads the limit from `roles.py` and names both bounds; a test each way;
- `screened` reachable only through `new_state(screening=...)`; no such field on the wire;
- the changelog's draft entry notes the default's effect on two-worker deployments.

## 5. Phase 2 -- rewordings, the gate, the vote, selection

What it builds: `paraphrase.py`, `fidelity.py` (or its F4 half, if S1 is
taken), `same_contract`, `agreement.py`, `screen_paraphrase`, `plan_wave`
for real, `validate`, `fuse` selecting only; one wave, no Judge; the two
routing tasks on the anchor model, schema 3 read leniently. Acceptance:
`ensemble` holds 15/15, the paraphrase set's stability under `ensemble` is
at least `single`'s, the report prints agreement by level and fidelity
rejections by check, and `test_ensemble.py`'s happy path, gate cases and
vote table pass.

This is the one phase with a risk of its own that no earlier phase can
hold: the first model-written question the pipeline runs.

**R7 lands, live, held.** The chain, in the order the graph runs it:

1. `screen` -- the Supervisor on the original, before the Paraphraser sees anything. An injection attempt or an ambiguity stops here; nothing is reworded.
2. `paraphrase` -- the Paraphraser sees the question and the contract; never retrieved text, never rows. Its one untrusted input has already been screened.
3. `screen_paraphrase` -- for each rewording, F1, F2, F3 and F5 in code, no model call, so an obviously altered rewording costs nothing; then `supervisor.screen` on the rewording itself, at the rung the Supervisor's rule gives it (the deterministic pre-screen runs on the rewording too, and a flagged one goes to the standard rung as a flagged question would); then `build_contract` on that reading and `same_contract` against the anchor's (F4); then `verdict == "proceed"`. Any other verdict discards -- a refusal and a request to clarify alike.
4. `plan_wave` -- selects only `status == "faithful"`.
5. `answer` -- seeds the candidate with the screening made on its exact wording, so the Supervisor call the candidate skips is the one already made for those words.

The retry sends the discarded rewordings and their reasons back into the
Paraphraser's prompt: model output and reasons written by code (F1-F3, F5)
or by the Supervisor (F4), nothing from a third party; once, under
`paraphrase_retried`. Held by `test_ensemble.py` (a rewording failing F2
never run; one failing F4 never run; a refused original reworded by
nobody), `test_fidelity.py` and `test_supervisor.py`. Two checks to make
sure of: the model-call count for a rewording that fails a deterministic
check is zero (the order is the saving, and a test should count it); and
the "refused" case covers every verdict but `proceed`.

**R1 widens, live.** `screen_paraphrase` runs the screenings through the
pool -- model calls in worker threads, with their spans and `_route`
records -- and `answer` runs up to four candidates. At one slot the pool is
still a loop; with two or more, these are the first concurrent runs, and
the copy is what keeps them apart: each worker's copied context gets its
own `_progress` set by `answer` to its own candidate's callback, so a step
cannot land in another candidate's lane, and each "Candidate k" span nests
under the one root. Held by `test_ensemble.py` (the progress stream
carrying `candidate` for inner steps and `None` for outer),
`test_ensemble_tracing.py` (one trace, a span per run), and the overlap
tests at one slot and two.

**R2 widens.** Four more outer nodes return details -- "7 rewordings", "3
faithful of 7; F2 x2, F4 x2", "4 run, 4 admissible, 3 agree (majority)" --
and `screen_paraphrase`'s pool items make model calls whose counts and
routes the node must total into its own return for the wrapper to strip
and record. Held by the wire test over the happy path. The check to add: a
test that every name in `ensemble.STEP_LABELS` is a node registered
through `traced_node`; `tests/docs` reads the names, and this holds the
wrapping.

**R3 widens.** Four candidates per job, and the first chance to measure
rather than estimate: the benchmark run at `ensemble` can print the JSON
size of each question's state beside its wall time. It is not one of
section 8's seven metrics, and it costs a line. Record the per-job size at
width three beside Phase 1's baseline.

**R4 lands, held.** `TASKS` gains `paraphraser` and `judge`;
`CATALOG_SCHEMA` becomes 3; `load_catalog` accepts 2 and 3, fills the two
tasks as unmeasured for 2 so `candidates()` routes them to the anchor
(`OLLAMA_MODEL`), and refuses any other schema as it does today. The
committed `models/catalog.json` is still schema 2 in this phase -- Phase 4
rebuilds it -- so every start and every test run goes through the lenient
branch by construction, which is good coverage now and the reason the
strict branch needs a schema-3 fixture of its own already. The Paraphraser
routes to the anchor at the Supervisor's rung rule; the Judge is not yet
built. Held by `test_router.py`: schema 2 accepted with the two tasks on
the anchor, schema 3 accepted, any other refused; and the routing table's
display with two unmeasured tasks.

**R5 live.** With slots above one, several candidates of one question run
SQL at once, and the bounds of Phase 1 can be met. Not on the host this was
designed against: at one slot nothing is concurrent, and the warning is
what a deployer who raises the slots gets. If the phase's benchmark can be
run once at several slots on a host that serves them, record the reader's
high-water mark (`pg_stat_activity` for `nl2sql_reader`); if it cannot,
say so where the number would go.

**R6 live.** A deployer at several slots: candidates of one question at
different rungs -- a rewording's complexity score need not be the
original's -- ask for two models at once, and a one-model host swaps per
call. One thing is measurable at one slot: from the benchmark's
per-candidate `rung`, the fraction of questions whose candidates span more
than one rung, which says how often a several-slot host would be asked for
two models at once. A line in the Phase 2 report, beside the agreement
rate.

**R8: --.**

**Before Phase 2 merges:**

- zero model calls for a rewording that fails F1-F3 or F5; every verdict but `proceed` discards;
- the `STEP_LABELS`-to-`traced_node` test;
- the lenient read tested on a schema-2 fixture of the test's own and on the committed file;
- the per-job state size at width three, and the rung-spread fraction, in the phase's benchmark write-up;
- stability under `ensemble` at least `single`'s (the acceptance), with the fidelity rejections by check beside it.

## 6. Phase 3 -- fusion and the second wave

What it builds: `fuse_columns` under the join rule with its toggle,
`fuse_claims`, `dissent` with `read_query.filters`, `render_answer`'s
notes; `waves`, the optional deadline, the recomputed vote. Acceptance: the
fusion tests of section 3.7, a benchmark run reporting columns joined and
declined and claims added and dropped, 15/15 held, the second wave only on
disagreement.

**R3 widens to the widest.** A second wave adds up to seven candidates:
eleven per job, each with its rows (up to `MAX_ROWS`) and its trace. This
is the state the release's line is about, so measure it here: a 2-2 split
at `ENSEMBLE_MAX_PARAPHRASES=10`, the JSON size written beside Phase 2's.
And a shape point the record depends on: `fuse_columns` returns a new
`QueryResult` -- its signature says so -- and must not append columns to
the representative's own `state["result"]` in place. If it did, candidate
k's record would show columns its run never returned, and the provenance
the design promises ("brand_name from run 2") would be contradicted by the
record of run k itself. Held by a `test_fuse.py` case asserting the
representative's `Candidate.state["result"]` is unchanged after a join
(S4).

**R1 widens.** The second wave submits a second batch to the pool after
`wave_reset`; the root span is still active on the main thread, so nothing
new is needed -- but section 3.8 lists the tracing test's cases without
naming a second-wave run. Confirm `test_ensemble_tracing.py` covers one
(the 2-2 split), and add it if it does not.

**R2 widens.** `fuse`'s detail grows (columns joined and declined, claims
added and dropped), and `plan_wave`, `answer` and `validate` run twice in
one graph execution: `wave_reset` empties the four wave fields, and the
`trace` entries accumulate by `operator.add`, which is intended -- the
record shows both waves. Nothing new to strip.

**R7 widens.** Wave 2 runs rewordings that were screened in wave 1; the
`Paraphrase` carries its status and screening, and nothing is screened
again. The invariant is `plan_wave`'s: every index in `wave_plan` is a
faithful paraphrase, and one discarded in wave 1 is never picked up in
wave 2. Held by the 2-2 split test; the check to add is a `plan_wave` test
on a mixed list.

**R5 and R6 widen, in width only.** More candidates per question in wave 2
under the same slots: the bounds are per slot and do not move. The
deadline is the first control that bounds the clock, and these two are
about width, not length.

**R4 and R8: --.**

**Before Phase 3 merges:**

- `fuse_columns` leaves the representative's record untouched;
- `wave_plan` selects only faithful rewordings;
- the tracing test covers a second-wave run;
- the widest job state's size recorded.

## 7. Phase 4 -- the Judge and calibration

What it builds: `judge.py`, the `judge` node and its route;
`models/build_catalog.py` and `calibrate.py` for the two tasks; the
probes; the committed catalog rebuilt at schema 3 and calibrated;
`docs/model_catalog.md`. Acceptance: the Judge is reached only on the
no-majority, no-wave path; a schema-2 catalog loads and routes the two
tasks to the anchor; the catalog names a suited rung for each task on the
maintainer's host; the document stays generic.

**R4 widens, held.** The committed catalog moves to schema 3, and from
here the strict branch runs everywhere and the lenient branch runs only
where a test sends it. The schema-2 test must therefore carry a fixture of
its own -- a schema-2 catalog under `tests/models/`, or one built in the
test -- and not the committed file it could lean on in Phase 2. The
acceptance names the test; this is why it has to be written so. A second
reader appears here too: `build_catalog.py --resume` reads the old catalog
to carry measurements for unchanged weights, and the old catalog is schema
2. It should read through `router.load_catalog` rather than parse the file
itself -- one reader of schema 2, for the reason decision 26 gave against
two scorers. Check: `--resume` from a schema-2 file yields a schema-3 file
with the five tasks' measurements kept and the two new tasks unmeasured
until `calibrate.py --tasks paraphraser judge` measures them (S5).

**R7 widens to a second input.** The Judge's prompt carries each group's
SQL -- the generator's output, a model's -- and five rows of database
content. Neither is screened; the Judge is bounded instead: structured
output `Verdict.group: int | None`, an id outside the groups read as
`None`, no schema in the prompt, one call, and no way to write SQL, call
an agent or start a run (W3 in arch7's blueprint). Its `why` -- model
output -- reaches the agreement line and is rendered as text, as the
narrative is. Held by `test_judge.py` (the prompt's content; an id outside
the groups is `None`; a model failure is `None` with
`node_errors["judge"]`) and by `test_ensemble.py`'s route tests.
`docs/SECURITY.md` says both halves in Phase 5: rewordings screened, the
Judge bounded.

**R6 widens, even at one slot.** The Judge is heavy, always. On a judged
question the host loads the heavy model for one call: no new resident
model when the heavy rung's model is the ladder's own, a fourth when
`MODEL_ROUTE_JUDGE` pins another, and a one-model host swaps either way.
Calibration on the maintainer's host is where the Judge's cost per call is
first measured; the report should record it. The sentence for the deployer
is Phase 5's.

**R2 widens.** The `judge` node is an outer node with a model call of its
own, as `screen` and `paraphrase` are; the wrapper strips as before.

**R1: not widened.** The Judge runs on the main thread -- an outer node,
not a pool item.

**R3, R5, R8: --.** The Judge reads the state and writes a small
`Judgement`. Calibration runs the probes' SQL live, but as the
calibrator's own process and connection, outside the API's bounds.

**Before Phase 4 merges:**

- the schema-2 test on a fixture of its own;
- `--resume` through one reader, its result checked as above;
- the Judge reached only on the no-majority, no-wave path;
- the catalog document generic -- the names of tasks and rungs, no per-model results, as the owner's standing rule has it.

## 8. Phase 5 -- the surfaces

What it builds: the web interface's lanes, badge and disclosure
(`Progress.tsx`, `AnswerView.tsx`, `Candidates.tsx`); the desktop's panes;
the review GUI's candidates pane and the staging column; `arch_v7.svg`;
every document in section 7; the counts. Acceptance: the `--run-node` and
`--run-java` suites green with the new tests, `tests/docs` green with the
moved counts, and the acceptance tier green from a clean start with a
question answered through the ensemble.

**R8 lands, held -- and wider than section 11 says.** The column:
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS ensemble JSONB NOT NULL DEFAULT
'{}'::jsonb` in `ensure_schema`, which already migrates on start (today it
drops and re-adds a `CHECK` constraint), so the column follows the store's
own idiom. The owner claim holds: `ensure_schema` is what creates the
table, so the role the review service connects as owns it, and `ALTER
TABLE` needs no superuser. The live-store test, run twice, is the proof;
if it ever fails with "must be owner", the DDL has moved and so must the
column. `NOT NULL DEFAULT '{}'` on a populated table adds the column
without rewriting its rows (a constant default), and every 6.3 submission
reads `{}`, which the candidates pane must render as "a single run; no
ensemble record" rather than an empty table or an error.

What section 11 does not say is who else writes the table. The API inserts
the snapshot itself, as `nl2sql_feedback_writer`, with an explicit column
list (`api/feedback.py`, `COLUMNS`), and it cannot run DDL: it is the
INSERT-only writer by design. The review service runs `ensure_schema` at
its own start, and the review service is behind the `review` profile: a
stack started without `--review` has no review service and runs no
migration, ever. So a 7.0 API whose `COLUMNS` names `ensemble` fails every
vote with "column does not exist" on any stack whose review service has
not started since the upgrade -- which, without `--review`, is every
stack. The contract tests -- `tests/api/test_feedback.py`'s column-list
test offline, `tests/review/test_store_live.py`'s on a live store -- hold
the two column lists to each other and cannot hold their timing. The way round that fits the
design: the writer asks `information_schema.columns` once at start whether
`ensemble` exists -- and once more on an undefined-column error -- and
includes it only when it does, logging once that snapshots carry no
ensemble until the review service has started; the contract test holds
`ensemble` as the one optional column. The alternative, the one-shot
`dbprep` adding the column, is wrong on ownership: `dbprep` does not create
this table, and a column it added would belong to its role. (S8.) The
release rehearsal exercises both orders: a vote before the review service's
first start, and one after.

**R3 widens into the store.** The snapshot's `ensemble` is the wire
`Ensemble` as JSON: no rows, but up to eleven candidates each with its
`trace` -- every attempt's entries -- bounded by `MAX_ATTEMPTS` times the
nodes times eleven. Small per row, and the first ensemble state that
outlives `API_MAX_JOBS`: it lives as long as the submission. Take the
widest snapshot's size from Phase 3's state and write it beside the job
store's.

**R1 seen.** The lanes are `ProgressEvent.candidate` made visible, and the
MLflow page (`docs/tracing.md`) is the nesting made visible. If R1 had
bitten anywhere, a lane would stay empty or a step would land in the wrong
one, and the trace would show a run beside the question instead of inside
it. The acceptance tier's question through the ensemble is where a person
sees both: check that the trace has exactly `agreement.total` candidate
spans under the root and that every candidate's lane shows the inner
steps.

**R4, R5, R6 stated.** The documents carry the deployer's half of each way
round:

- `docs/model_catalog.md`: seven tasks, schema 3, a schema-2 catalog read with the two tasks unmeasured and routed to the anchor; generic throughout, no per-model results (R4).
- `agent/README.md`, configuration: `OLLAMA_PARALLEL_CALLS` no higher than the host's `OLLAMA_NUM_PARALLEL`, and only for a host with the memory for its resident models times the slots; a pinned Judge model is a resident model more (R6). `API_MAX_CONCURRENCY=1` recommended with one slot (decision 29); the product rule and both bounds, the engine's fifteen and the role's sixty (R5). And the process ceiling: arch7 section 22.9 says the agent container's `pids_limit` is re-derived when the slots exceed one, and the implementation specification changes nothing in the containers; the two meet in a sentence telling the deployer who raises the slots to raise the API container's `pids_limit` (512 today) with them, and `docs/hardening.md`'s table says the same (S6).
- `docs/SECURITY.md`: the two inputs the ensemble adds -- rewordings screened before they run, the Judge bounded to a group id -- under what the stack protects (R7). The rewordings shown in lanes and the Judge's `why` are model-written text; both GUIs render text, never markup, as they do the narrative today.

**R2: --.** `tests/gui/test_types.py` and `tests/java/` hold the mirrors to
the pydantic models; a leaked key would have failed in Phase 1.

**Before Phase 5 merges:**

- the migration twice on a live store; 6.3 rows render as a single run;
- the writer inserts `ensemble` only when the column exists; a vote succeeds on a stack whose review service has never started;
- the widest snapshot's size recorded;
- the acceptance run's trace and lanes checked once by eye, against `agreement.total`;
- the four documents above carry the sentences listed, and `docs/SECURITY.md` its two rows.

## 9. Release

What it does: the version in every declaration
`tests/docs/test_versions.py` holds together; `setup.sh` pinning `v7_0`;
the published-tag tests after the push; the upgrade rehearsed on an
isolated instance (`NL2SQL_INSTANCE`) first, as 6.3's was. A published tag
never moves; a correction is `v7_0_1`.

**R3 verify.** The API container's memory ceiling is 2 GB (`mem_limit` in
compose). The line section 11 asks for is `API_MAX_JOBS` (200) remembered
jobs at the widest state (Phase 3's number) against that ceiling, beside
what the process holds anyway -- the retrievers, the catalog, the label
map. The procedure on the isolated instance: enough questions through the
ensemble to fill the store, or fewer and the arithmetic, and the
container's memory from `docker stats`; the number and the headroom go in
the changelog's entry. The failure the line guards against is not
slowness: a job store past the ceiling is an API container killed and
restarted, and every remembered job gone with it.

**R4 live.** A deployer with a calibrated schema-2 catalog of their own
host, from 5.2 to 6.3, starts 7.0 and gets the two tasks on the anchor.
The rehearsal should start once with a schema-2 catalog from a 6.3
checkout in place, and the log should say what happened: if the lenient
read is silent, add a line at start naming the catalog's schema and the
tasks routed to the anchor, because a fallback no one can see is a
surprise later (S7).

**R6 verify, as an upgrade note.** The default serialises the API's two
workers at the host. A deployment whose host served two calls at once
under 6.3 gets one until it sets `OLLAMA_PARALLEL_CALLS`; the changelog's
entry and the upgrading section of `docs/images.md` say so (S2).

**R8 live, verify.** The rehearsal's store is a 6.3 store with
submissions: the column is added on the review service's first start, the
review page shows the old submissions and the new, and -- the other order
-- a vote cast before the review service has started is recorded without
an ensemble and the log says why.

**R1 verify.** The acceptance tier green from a clean start, with the
trace and lanes of Phase 5's check.

**R2, R5, R7: nothing beyond the suites.**

## 10. What placing them surfaced

Section 11 is not edited in place; these are recorded here, for the build
to act on and the next specification to carry as departures or additions.
Each names the phase it belongs to.

| Id | What | Phase | The way round |
|---|---|---|---|
| S1 | Section 8's Phase 0 test calls `fidelity.check`, which section 10 builds in Phase 2 | 0 | build the deterministic half of `fidelity.py` (F1, F2, F3, F5, `check`) in Phase 0, with its test; `same_contract` stays in Phase 2; the false-rejection rate on the forty-five is Phase 0's second number |
| S2 | The gate at one slot serialises the API's two workers, which 6.3 let call the host at once | 1 (lands), release (stated) | a sentence in the changelog's entry and the upgrading section: a host that serves two calls needs `OLLAMA_PARALLEL_CALLS=2` to keep them |
| S3 | The engine's pool (fifteen connections by SQLAlchemy's default, a thirty-second wait then an error) is the bound a process meets before the role's sixty | 1 | the check at start names both bounds, reading the role's from `roles.py`; `database.py` is not on the touched list and stays as it is until Phase 2's measurement says a sized pool (`pool_size`, `max_overflow` from the product) is needed |
| S4 | `fuse_columns` widening the representative's own result in place would make a candidate's record show columns its run never returned | 3 | return a new `QueryResult`; a test that the representative's `Candidate.state["result"]` is unchanged after a join |
| S5 | After Phase 4 the lenient branch runs only in tests, and `--resume` is a second reader of schema 2 | 4 | the schema-2 test carries its own fixture; `--resume` reads through `router.load_catalog` |
| S6 | arch7 section 22.9 says `pids_limit` is re-derived when the slots exceed one; the implementation specification changes nothing in the containers | 5 | the deployer who raises the slots raises the API container's `pids_limit` with them; `agent/README.md` and `docs/hardening.md` say so |
| S7 | A lenient catalog read that says nothing | release | a line at start naming the catalog's schema and the tasks routed to the anchor |
| S8 | The API inserts the snapshot with an explicit column list and cannot run DDL; the review service, which adds the column, is a profile and need never start | 5 | the writer includes `ensemble` only when `information_schema.columns` has it, re-checking on an undefined-column error, and logs once; the contract test holds it as the one optional column; the rehearsal votes in both orders |

Of the eight, S8 is the one that would have shipped as a defect: every
other item is a measurement taken earlier or a sentence placed, and S8 is
a vote that fails on every stack whose review service has not started
since the upgrade. It is also the one section 11 came closest to and did
not reach: the migration's idempotence and its privilege were thought
through, and the question of who runs it, and whether anyone does, was
not.
