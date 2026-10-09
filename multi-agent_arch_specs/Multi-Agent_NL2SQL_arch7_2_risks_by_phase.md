# Multi-Agent NL2SQL v7.2: the risks, phase by phase

**Status:** a reading of
[`Multi-Agent_NL2SQL_arch7_2_implementation.md`](Multi-Agent_NL2SQL_arch7_2_implementation.md)
section 11 ("Risks and the ways round them") against its section 10 (the
build order), written 2026-10-09 against the tree as 7.0.0 built it
through Phase 3. It is arch7.1's risks by phase
([`Multi-Agent_NL2SQL_arch7_1_risks_by_phase.md`](Multi-Agent_NL2SQL_arch7_1_risks_by_phase.md),
which stands where this one does not change it) with Phase 3's checks
marked as built and held, and with Phase 6, the grain check, placed -- and
a tenth risk, the grain check itself (R10). Section 11 names ten risks and
the way round each; section 10 names seven phases and a release, each a
merge candidate on its own. Neither says which phase meets which risk.
This document does: for every phase, the risks that are live in it, what
the exposure looks like there, how that phase builds the way round, what
holds it, and what to check before the phase merges.

Precedence: [`Multi-Agent_NL2SQL_arch7_2.md`](Multi-Agent_NL2SQL_arch7_2.md)
decides the design, the implementation specification decides the build,
and this document adds nothing to either except the placement -- with one
exception, and the owner's rule that where this document and the
implementation specification differ, this one is followed. Placing the
ten surfaced a handful of things section 11 does not say (section 11
here). A specification in this folder is never edited in place, so they
are recorded here, for the build to act on and the next specification to
carry.

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

## 1. The ten risks, named

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
| R9 | A Judge before every vote | a model now decides, on every question, which answers are counted at all -- the role arch6 found a model wrong for (a correct query rejected three times by its own reviewer); a Judge wrong about a right answer sets it aside; and its prompt carries model-written SQL and database rows | it never sees how many runs gave an answer; it sets one aside only for a mistake it names in the query; an answer it says nothing about stands; with none accepted the runs' own choice is delivered, flagged; a failure lets the runs vote alone; and the benchmark scores the runs' own choice wherever the Judge overruled them, so its effect is measured both ways | `judge.py`, `agreement.py` (`decide`), `ensemble.py`, `prompts.py`, `benchmarks/run_benchmark.py` |
| R10 | A grain check that rejects a right query | a deterministic gate in every run, the ensemble off included: a join it misreads, or a grain measured from data that holds one date a month for a table daily in kind, sends a right query to repair; and the grains are read with a query per date-stamped table | it reads only what it can read and passes the rest; a grain from all of a table's dates; a wrong rejection costs an attempt while the budget lasts; every reference passes it, held by a test; `GRAIN_CHECK_ENABLED` turns it off | `grain.py`, `validate.py`, `database.py`, `config.py` |

## 2. The matrix

| | Phase 0 -- the premise | Phase 1 -- plumbing | Phase 2 -- rewordings, Judge, vote, selection | Phase 3 -- fusion, second wave (built) | Phase 4 -- calibration | Phase 5 -- surfaces | Phase 6 -- the grain check | Release |
|---|---|---|---|---|---|---|---|---|
| R1 worker threads | -- | lands, held | widens, live | widens, held | -- | seen | -- | verify |
| R2 one wrapper | -- | lands, held | widens (the Judge too) | widens | -- | -- | -- | -- |
| R3 state in the state | -- | lands, held | widens | widens (the widest), measured | -- | widens (the store) | -- | verify |
| R4 schema check | -- | -- | lands, held | -- | widens, held | stated | -- | live |
| R5 connection limit | -- | lands, held | live | widens | -- | stated | widens (the grains, once) | -- |
| R6 Ollama swaps | -- | lands | live; widens (the Judge, heavy, every question) | widens | widens | stated | -- | verify |
| R7 paraphrases as input | early (S1) | seam | lands, live, held; widens (the Judge) | widens, held | -- | stated | -- | -- |
| R8 the migration | -- | -- | -- | -- | -- | lands, held | -- | live, verify |
| R9 a Judge before every vote | -- | -- | lands, live, held | widens (once a wave), held | widens (calibrated) | stated | eases | verify |
| R10 a grain check | -- | -- | -- | seam (the reading, for the dissent) | -- | -- | lands, live, held | verify |

Read down a column for a phase's risks, across a row for a risk's path.
Four of the ten land in Phase 1, which is why that phase "changes no
answer": it is where the plumbing the later phases ride on is proved with
nothing riding on it. Phase 2 is the phase with risks of its own that no
earlier phase can hold: the first model-written question the pipeline runs
(R7), and the first model that decides which answers count (R9). Phase 5 is where three of them turn into
sentences a deployer reads. Phase 6 has one risk, its own (R10), on a seam
Phase 3 cut: the reading of how a query combines its facts, built to
describe a losing query, becomes the input to a rejection. The release
verifies three numbers and rehearses two upgrades.

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
(S1 in section 11.)

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
phase, not from the Judge. If a key leaked it would reach
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
S2 in section 11, and a sentence in the release's upgrade note.

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

## 5. Phase 2 -- rewordings, the gate, the Judge, the vote, selection

What it builds: `paraphrase.py`, `fidelity.py` (or its F4 half, if S1 is
taken), `same_contract`, `agreement.py`, `judge.py`, `screen_paraphrase`,
`plan_wave` for real, `validate`, `judge`, `vote`, `fuse` selecting only;
one wave; the two routing tasks on the anchor model, schema 3 read
leniently. Built in 7.0 first with no Judge, as arch7 placed it, and the
Judge added before the vote when arch7.1 moved it (2026-10-08).
Acceptance: `ensemble` holds 15/15, the paraphrase set's stability under
`ensemble` is at least `single`'s, the report prints agreement by level,
fidelity rejections by check and what the Judge did, the runs' own choice
is scored wherever the Judge overruled them, and `test_ensemble.py`'s
happy path, gate cases and vote table pass, the Judge's cases among them.

This is the phase with risks of its own that no earlier phase can hold:
the first model-written question the pipeline runs, and the first model
that decides which answers count.

**R7 lands, live, held.** The chain, in the order the graph runs it:

1. `screen` -- the Supervisor on the original, before the Paraphraser sees anything. An injection attempt or an ambiguity stops here; nothing is reworded.
2. `paraphrase` -- the Paraphraser sees the question and the contract; never retrieved text, never rows. Its one untrusted input has already been screened.
3. `screen_paraphrase` -- for each rewording, F1, F2, F3 and F5 in code, no model call, so an obviously altered rewording costs nothing; then `supervisor.screen` on the rewording itself, at the rung the Supervisor's rule gives it (the deterministic pre-screen runs on the rewording too, and a flagged one goes to the standard rung as a flagged question would); then `build_contract` on that reading and `same_contract` against the anchor's (F4); then `verdict == "proceed"`. Any other verdict discards -- a refusal and a request to clarify alike.
4. `plan_wave` -- selects only `status == "faithful"`.
5. `answer` -- seeds the candidate with the screening made on its exact wording, so the Supervisor call the candidate skips is the one already made for those words, and holds it to the anchor contract.

The retry sends the discarded rewordings and their reasons back into the
Paraphraser's prompt: model output and reasons written by code (F1-F3, F5)
or by the Supervisor (F4), nothing from a third party; once, under
`paraphrase_retried`. Held by `test_ensemble.py` (a rewording failing F2
never run; one failing F4 never run; a refused original reworded by
nobody), `test_fidelity.py` and `test_supervisor.py`. Two checks to make
sure of: the model-call count for a rewording that fails a deterministic
check is zero (the order is the saving, and a test should count it); and
the "refused" case covers every verdict but `proceed`.

**R7 widens to a second input: the Judge.** Moved here from Phase 4. The
Judge's prompt carries each group's SQL -- the generator's output, a
model's -- five rows of database content, and the knowledge the original's
run retrieved. None of it is screened; the Judge is bounded instead:
structured output, a verdict per letter, a letter that names no answer
read as nothing, no schema in the prompt, one call a wave, and no way to
write SQL, call an agent or start a run (W3 in arch7.1's blueprint). Its
`why` -- model output -- reaches the agreement line and is rendered as
text, as the narrative is. Held by `test_judge.py` (the prompt's content;
an unknown letter and a repeated one ignored; a failure raised) and by
`test_ensemble.py`'s Judge cases.

**R9 lands, live, held.** The Judge reads every question's answers before
any is counted, so from this phase a model's opinion decides what the vote
may see. The ways round, each held: it is never told how many runs gave
an answer (`test_judge.py`: no count in the prompt) and reads the answers
in the order the runs were asked, never by size; it is told to set an
answer aside only for a mistake it can name in the query; an answer it
gives no verdict on stands (`test_judge.py`); when it accepts none, the
runs' own choice is delivered as `contested` with its objection
(`test_agreement.py`, `test_ensemble.py`); when it fails, the runs vote
alone and the answer says so (`test_ensemble.py`); and with
`ENSEMBLE_JUDGE_ENABLED=false` it is never asked. What no test can hold is
whether it is right: that is measured, on the benchmark and the paraphrase
set, by scoring the runs' own choice wherever the Judge overruled them
(`without_judge`) -- the fixes and the breaks side by side, so a Judge that
takes right answers away shows as plainly as one that rescues wrong ones.

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

**R2 widens.** Six more outer nodes return details -- "7 rewordings", "3
faithful of 7; F2 x2, F4 x2", "4 run, 4 admissible, 2 group(s)", "group 0
set aside, group 1 accepted", "4 run, 1 voted, 1 agree (judged)" -- and
the `judge` node makes a model call of its own, as `screen` and
`paraphrase` do --
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

**R6 widens, even at one slot.** The Judge is heavy, always, and now
asked on every question: the host loads the heavy model for one call a
question -- no new resident model when the heavy rung's model is the
ladder's own, a fourth when `MODEL_ROUTE_JUDGE` pins another, and a
one-model host swaps either way. The phase's benchmark records the Judge's
time beside the other calls'.

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
- stability under `ensemble` at least `single`'s (the acceptance), with the fidelity rejections by check beside it;
- the Judge never shown the count; a letter naming no answer ignored; silence accepting; none accepted delivering the runs' own choice, flagged; a failure leaving the runs' vote (tests);
- what the Judge did, by question, and where it overruled the runs the runs' own choice scored beside the delivered answer, in the phase's benchmark write-up.

## 6. Phase 3 -- fusion and the second wave

What it builds: `fuse_columns` under the join rule with its toggle,
`fuse_claims`, `dissent` with `read_query.filters`, `render_answer`'s
notes; `waves`, the optional deadline, the Judge again and the recomputed
vote. Acceptance: the fusion tests of section 3.7, a benchmark run
reporting columns joined and declined and claims added and dropped, 15/15
held, the second wave only on disagreement among the accepted, the Judge
once a wave.

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
added and dropped), and `plan_wave`, `answer`, `validate`, `judge` and
`vote` run twice in one graph execution: `wave_reset` empties the five
wave fields, and the
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

**R9 widens, once a wave.** A second wave adds runs and may merge
groups, so the Judge reads every group again -- at most `ENSEMBLE_WAVES`
calls a question, which a test holds. A group it set aside in wave 1 is
read afresh in wave 2: its verdicts are a wave field, emptied with the
vote.

**R4 and R8: --.**

**Before Phase 3 merges** -- each held, as built (2026-10-09):

- `fuse_columns` leaves the representative's record untouched -- `test_fuse.py`, the representative's own record never widened;
- `wave_plan` selects only faithful rewordings -- `test_ensemble.py`, a second wave planning only faithful rewordings not yet run, on a mixed list;
- the tracing test covers a second-wave run -- `test_ensemble_tracing.py`, a second wave in the question's one trace at two workers;
- the widest job state's size recorded -- 376.4 KB as JSON, B15's ten runs, against 155.0 KB at the median (`docs/benchmark.md`);
- the Judge asked once a wave and no more -- `test_ensemble.py`, the Judge called twice on a two-wave question.

**What building it added.** Two departures, carried by arch7.2 section
22.13: a claim of another run is added only when it speaks of a row the
narrative does not yet speak of, after the specification's repeat rule
restated B10's top row three times over; and the dissent reads how each
query rolls up and joins the tables it aggregates, after B07's wrong
answer read the same tables and filters as its right one. The second is
the seam R10 rests on (section 9).

## 7. Phase 4 -- calibration

What it builds: `models/build_catalog.py` and `calibrate.py` for the two
tasks; the probes; the committed catalog rebuilt at schema 3 and
calibrated; `docs/model_catalog.md`. The Judge itself moved to Phase 2.
Acceptance: a schema-2 catalog loads and routes the two tasks to the
anchor; the catalog names a suited rung for each task on the maintainer's
host; the document stays generic.

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

**R9 widens, calibrated.** The Judge's probe measures what the benchmark
can only sample: per question, two or three answers built from the
reference and the question's trap, shown by letter without counts, scored
by whether its verdicts match the reference's -- the reference accepted, a
trap set aside. A rung is suited to the task when its verdicts are within
the catalog's rule of the reference model's; the probe's false rejections
of the reference answer are worth a line of their own, since they are the
Judge's way of taking a right answer away.

**R6 widens.** Calibration on the maintainer's host is where the Judge's
cost per call is first measured per rung; the report should record it.
The sentence for the deployer is Phase 5's.

**R1, R2, R3, R5, R7, R8: --.** Calibration runs the probes' SQL live, but
as the calibrator's own process and connection, outside the API's bounds.

**Before Phase 4 merges:**

- the schema-2 test on a fixture of its own;
- `--resume` through one reader, its result checked as above;
- the Judge's probe reporting its false rejections of the reference answer;
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
- `docs/SECURITY.md`: the two inputs the ensemble adds -- rewordings screened before they run, the Judge bounded to a verdict per letter, never told the count -- under what the stack protects (R7). The rewordings shown in lanes and the Judge's `why` are model-written text; both GUIs render text, never markup, as they do the narrative today.
- `agent/README.md` and `docs/benchmark.md`: the Judge reads every question's answers before the vote, what it costs, and its effect as measured -- the fixes and the breaks where it overruled the runs (R9).

**R2: --.** `tests/gui/test_types.py` and `tests/java/` hold the mirrors to
the pydantic models; a leaked key would have failed in Phase 1.

**Before Phase 5 merges:**

- the migration twice on a live store; 6.3 rows render as a single run;
- the writer inserts `ensemble` only when the column exists; a vote succeeds on a stack whose review service has never started;
- the widest snapshot's size recorded;
- the acceptance run's trace and lanes checked once by eye, against `agreement.total`;
- the five documents above carry the sentences listed, and `docs/SECURITY.md` its two rows.

## 9. Phase 6 -- the grain check

What it builds: `grain.py`, `Database.dates_per_period`,
`ContractResources.grains`, `validate(..., grains=...)`,
`GRAIN_CHECK_ENABLED` and `GRAIN_PERIODS`, the benchmark's grain measures,
its documents. Acceptance: every reference passes; the grain questions and
the paraphrase set with the check and without; the owner's answers to
arch7.2's open decisions 32 and 33.

**R10 lands, live, held.** The phase is where a rule in code first
decides, in every run, that a query is wrong for a reason the planner
would not object to. Three exposures:

- **A misreading.** The reading was built in Phase 3 to describe a
  difference, where a wrong description costs a sentence; here a wrong
  reading costs a repair. Its cases in `test_completeness.py` were written
  for the dissent. The phase adds the shapes a rejection hangs on -- a
  period join through a CTE that truncates the date to the month, a
  rolled-up subquery joined on a month-start key, a self-join of one fact
  -- and each that is right must pass (S11).
- **A grain measured wrong.** A table loaded one date a month so far, but
  daily in kind, reads as monthly, and a join of it to daily sales on the
  date is rejected. The measurement is right about what that join does
  today -- each month's row meets one day's sales -- and a deployment that
  loads it more often reads the new grain on its next start; the check
  cannot see intent, and does not try.
- **The references.** The fifteen pass, by a test that runs `mismatches`
  over each reference's reading against a fixture's grains offline and the
  live catalog's under `--run-docker`.

**The single pipeline.** Unlike every phase before it, this one changes
`snippets` -- the benchmark's default -- and every configuration below
`ensemble`. The phase's benchmark runs all five configurations, not only
`ensemble`, with the check and without (S12).

**R5 widens, once.** The grains are read on the reader's pool once a
process -- one query per date-stamped table, eight on this catalog. The
label map's read is the precedent, and its failure mode -- recorded, the
feature stood down -- is the check's.

**R9 eases.** With the trap repaired inside the run that falls into it,
the Judge meets fewer grain mistakes, and its overrulings on the grain
questions should fall. The phase's benchmark reports both, so a fall is
read as the check's and not as a change in the Judge.

**R1, R2, R3, R4, R6, R7, R8: --.** The check is a rule inside a node
every run already has.

**Before Phase 6 merges:**

- every reference passes the check, offline against a fixture and live against the catalog;
- the right shapes above pass, each a test;
- the five configurations measured with the check and without;
- a grains read that fails stands the check down, and the log says so once;
- the owner's answers to open decisions 32 and 33.

## 10. Release

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

**R9 verify.** The acceptance tier's question through the ensemble shows
the Judge's verdicts in its record and the trace's `nl2sql.judge` tag;
the release's benchmark run, against the published tags, prints what the
Judge did and where it changed the answer, both ways.

**R10 verify.** The release's benchmark run prints the runs the grain
check sent to repair, and a reference it rejects is a release blocker --
if 7.0.0 ships it (open decision 32).

**R2, R5, R7: nothing beyond the suites.**

## 11. What placing them surfaced

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
| S9 | arch7's Judge, asked only after the votes failed to decide, never saw a majority -- and measured, every wrong answer the ensemble delivered had one | 2 | the Judge before the vote, on every question (arch7.1; the owner, 2026-10-08), bounded as R9 says; its effect scored both ways in the phase's benchmark |
| S10 | The Judge's rules name the kinds of mistake the benchmark's runs made, so the benchmark is no longer blind to them | 2 (lands), 4 (measured) | report the Judge's effect where it overruled the runs rather than accuracy alone; its calibration probe counts false rejections of the reference; a fresh set of hard questions is the honest test, and arch7.1's open decision 31 says so |
| S11 | The dissent's reading of how a query combines its facts, built in Phase 3 to describe a losing query, becomes the grain check's input in Phase 6: a misreading that cost a wrong sentence would cost a repair | 6 | the phase adds to `test_completeness.py` the shapes a rejection hangs on, each right one passing |
| S12 | The grain check changes the single pipeline -- every configuration of the benchmark, not only `ensemble` | 6 | the phase's benchmark runs all five, with the check and without |

Of the twelve, S8 is the one that would have shipped as a defect: every
other item is a measurement taken earlier or a sentence placed, and S8 is
a vote that fails on every stack whose review service has not started
since the upgrade. It is also the one section 11 came closest to and did
not reach: the migration's idempotence and its privilege were thought
through, and the question of who runs it, and whether anyone does, was
not.
