# Multi-Agent NL2SQL v8: the risks, phase by phase

**Status:** a reading of
[`Multi-Agent_NL2SQL_arch8_implementation.md`](Multi-Agent_NL2SQL_arch8_implementation.md)
section 11 ("Risks and the ways round them") against its section 10 (the
build order), written 2026-10-14 against the tree as 7.0.0 published it,
with nothing of arch8 built. Section 11 names twelve risks and the way
round each; section 10 names six phases and a release, each a merge
candidate on its own. Neither says which phase meets which risk. This
document does: for every phase, the risks that are live in it, what the
exposure looks like there, how that phase builds the way round, what holds
it, and what to check before the phase merges. It is written as arch7.3's
was ([`Multi-Agent_NL2SQL_arch7_3_risks_by_phase.md`](Multi-Agent_NL2SQL_arch7_3_risks_by_phase.md)),
whose ten risks stand for the ensemble as it is built; none of them is
reopened here, and the one it placed on the review service's migration
(S8 there) is the pattern two of this document's risks ride on.

Precedence: [`Multi-Agent_NL2SQL_arch8.md`](Multi-Agent_NL2SQL_arch8.md)
decides the design, the implementation specification decides the build,
and this document adds nothing to either except the placement -- with the
owner's rule that where this document and the implementation specification
differ, this one is followed. Placing the twelve surfaced a handful of
things section 11 does not say (section 11 here). A specification in this
folder is never edited in place, so they are recorded here, for the build
to act on and the next specification to carry.

**The owner's decision, placed.** `v6_3_1` sets the ensemble off and
nothing else (open decision 42, decided 2026-10-14). That is a one-field
change, and the risk it carries is the one R6 names: that the name and the
switch disagree, or that a configuration changes more than the name says.
Nothing else in this document depends on the decision; a name that folded
the grain check in would have added a row to R6 and a line to the
benchmark's acceptance, not a phase.

**The vocabulary.** A cell of the matrix, and a paragraph under a phase,
uses these words for the relation between a risk and a phase:

| Word | Meaning |
|---|---|
| lands | the code that creates the exposure is written in this phase, and so is its way round -- section 11 puts them together, and so does the build |
| held | a test or the acceptance criterion of this phase holds the way round |
| widens | the phase adds another instance of the exposure under a way round already built |
| live | the first phase in which a deployment can meet the exposure in use |
| stated | the way round is, in part, a sentence a deployer or a person reads, and this phase writes the document or the line that carries it |
| seam | the phase cuts the seam a later phase's way round rests on, with nothing yet passing through it |
| early | the way round can be built and measured before the exposure exists |
| seen | the phase is where a person first sees the way round working, or failing |
| measured | the phase takes the number the way round is argued from |
| verify | a line in the release's verification |
| -- | nothing in the phase touches it |

## 1. The twelve risks, named

Section 11's bullets, each given a short name for the matrix and the
sections below. The exposure and the way round are section 11's,
compressed; the module is where the implementation specification puts the
code.

| Id | Risk | The exposure | The way round | Where |
|---|---|---|---|---|
| R1 | The restatement as input | a follow-up's restatement is model output that becomes `question` for every later stage: the retrievers embed it, the generator writes to it, the record keeps it | the words typed are screened first and the verdict is given on them; C1 to C4 in code, no model call, discard a restatement that drops a numeral, a literal or a direction; `asked` is kept beside it everywhere and the answer opens with "Read as" | `memory.py` (`resolve`), `supervisor.py`, `graph.py` (`screen`), `present.py` |
| R2 | Independence | memory that changes a question which stands alone: a Supervisor restating what needs none, a generator borrowing an earlier filter the question did not ask for | "not restated" when `standalone` is empty or equals the words typed; the generator's clause says *usually* and names the last question as the one asked; the benchmark's independence must be 15 of 15 before the default is five | `memory.py`, `prompts.py`, `benchmarks/run_benchmark.py` |
| R3 | One field rewritten at one node | `question` means the words typed before the Supervisor and the question answered after it; a reader on the wrong side reads the wrong thing | the lifetimes table declares it; `asked` is the words typed for the whole run and the only field the surfaces show for them; a test compares the Supervisor's whole update against a run with no turns | `state.py`, `graph.py`, `api/translate.py`, `api/routes.py` |
| R4 | The conversation's bookkeeping | a conversation marked busy and never freed is refused until the process restarts; one pruned while a question runs loses its turn; a store that grows | `finish` in a `finally` on every terminal path and on cancel; a busy conversation is never pruned; a `deque` with `maxlen` and a counted cap | `api/conversations.py`, `api/jobs.py` |
| R5 | Two readers, one condition | two prompts gain a placeholder each; one rendered when it should not be changes every 7.0 answer | both default to `""` through `.partial`; the block functions return `""` for no turns; the tests that pin the prompts byte for byte are kept | `prompts.py`, `supervisor.py`, `graph.py` |
| R6 | The name and the switch | a `.env` that says one thing and a name that says another; a configuration that changes more than the name | `ENSEMBLE_ENABLED` unset is read as unsaid and the name decides; a contradiction is refused at start and by the probe; `validate` after every configuration the benchmark applies; one test that the name changes one field | `config.py`, `docker-compose.yml`, `launch.sh`, `benchmarks/run_benchmark.py` |
| R7 | The snapshot's two more columns | the optional-column pattern built for one column covers three; the review service, a profile, adds two more and need never start | one catalog query for all three; a vote rehearsed before and after the migration; the contract tests hold the writer's columns to the store's | `api/feedback.py`, `review/nl2sql_review/store.py` |
| R8 | The question column changes meaning | the snapshot's `question` was the words typed and becomes the question answered, because a golden pair is built from it and must stand alone; a reviewer looking for what was typed | outside a conversation the two are the same words; inside one `conversation.asked` is in the same row and the review page shows both | `api/routes.py` (`capture_from`), `review/gui/src/` |
| R9 | The Supervisor at the standard rung with turns | a question in a conversation costs a standard screening; a light model pinned for the task reads the turns however suited it is | the rule is `complexity.py`'s and measured in Phase 5; the pin is the deployer's word, as for every task | `complexity.py`, `models/calibrate.py` |
| R10 | A wrong turn remembered | a follow-up on a wrong answer is a right follow-up on a wrong turn, and its SQL the nearest exemplar | a `no` forgets the turn at once; `incomplete` keeps it; the record shows which turn was read | `api/conversations.py` (`forget_turn`), `api/routes.py` |
| R11 | Three clients and one id | the id is the client's; a reused id after the TTL, two clients of one person, a collision with sign-in off | the pattern on the request; the key by owner; a random id of 122 bits; `Answer.conversation.turn` shows a conversation begun afresh | `api/models.py`, `api/conversations.py`, `gui/src/`, `desktop/` |
| R12 | The trace's inputs grow | the Supervisor's span records the turns and the generator's the same, per candidate and per attempt | bounded by `MEMORY_ROWS` and `MEMORY_ANSWER_CHARS`; measured in Phase 2 beside the tokens | `graph.py` (`TRACE_SPANS`), `tracing.py` |

## 2. The matrix

| | Phase 0 -- the premise | Phase 1 -- the name, plumbing | Phase 2 -- memory in the agent | Phase 3 -- the conversation over REST | Phase 4 -- surfaces | Phase 5 -- calibration | Release |
|---|---|---|---|---|---|---|---|
| R1 restatement as input | early (S1) | seam | lands, live, held | widens | seen, stated | measured | verify |
| R2 independence | -- | held (no turns, no change) | lands, live, held, measured | widens | -- | measured | verify |
| R3 one field rewritten | -- | seam | lands, held | widens (the job, the snapshot), held | seen | -- | -- |
| R4 bookkeeping | -- | -- | -- | lands, live, held | seen | -- | verify |
| R5 two readers | -- | lands, held | widens, held | -- | -- | -- | -- |
| R6 name and switch | -- | lands, live, held, stated | -- | -- | stated | -- | verify |
| R7 snapshot columns | -- | lands, held (one column) | -- | widens (the second), held | -- | -- | live, verify |
| R8 question column | -- | -- | -- | lands, live, held | stated | -- | verify |
| R9 Supervisor rung | -- | -- | lands, live | -- | stated | measured | -- |
| R10 wrong turn remembered | -- | -- | live (the CLI) | lands, held | stated | -- | -- |
| R11 clients and the id | -- | -- | -- | lands, held | live, seen | -- | verify |
| R12 trace inputs | -- | -- | lands, measured | widens (the tags) | -- | -- | verify |

Read down a column for a phase's risks, across a row for a risk's path.
Phase 1 is where the name lands whole (R6) and where memory's plumbing is
proved with nothing passing through it (R5, and the seams of R1 and R3):
it is the phase that "changes no answer", and its acceptance -- the
benchmark the same run as 7.0 to the SQL under `v7_0_0`, the same as
`snippets` under `v6_3_1` -- is the proof. Phase 2 is the phase with risks
of its own that no earlier phase can hold: the first model-written
question the pipeline runs that was not a rewording held to an original
(R1), and the first chance for memory to change an answer it should not
(R2). Phase 3 is where the conversation becomes a thing a process keeps
(R4, R10, R11) and where the snapshot's `question` changes meaning (R8).
Phase 4 turns five of them into sentences a person reads. The release
verifies six.

## 3. Phase 0 -- the premise

What it builds: `benchmarks/followups.py` (fifteen hand-written pairs: a
fragment, the complete question it means, its reference SQL, the literals
of the first turn it keeps), its test, `--followups` and `--memory` with
the depth forced to zero, and `memory.py`'s `resolve` with its check cases.
The fifteen fragments run through the live 7.0 stack, each asked alone --
there is nothing yet to hand a turn to -- under `ensemble` and `snippets`;
the follow-up accuracy with nothing remembered recorded in
`docs/benchmark.md`. No agent change.

**No risk of section 11 is live.** Nothing is restated (R1); no prompt
has a placeholder (R5); no setting is read (R6); no store exists (R4); the
fifteen standalones are a person's, not a model's.

**R1, early.** Section 8 of the implementation specification has the
Phase 0 test hold every hand-written standalone to `memory.resolve`
against its fragment, and section 10 builds `memory.py` in Phase 1 and the
Supervisor's reading in Phase 2. One of the two moves, and the implementation
specification already moved it: `resolve` and the three extractors it
rests on are pure functions with no model behind them, so they are built
here with `tests/agent/test_memory.py`'s check cases, and the rest of
`memory.py` follows in Phase 1. That buys Phase 0 a second number beside
the follow-up accuracy: how many of fifteen standalones a person wrote
and checked by hand the checks in code would have discarded. Each false
discard is a word for the extractors' lists or a rule too strict, and this
is the first place it can show -- the same move arch7.3's risks made for the
fidelity checks (S1 there, S1 here).

**What the number means.** The fragment asked alone is what 7.0 does with
a follow-up today: "and by store?" screened as a question with no measure
and no period, the generator writing a query about stores. Expected low.
If it is not -- if retrieval alone guesses follow-ups right more often than
not -- memory buys consistency and the record rather than accuracy, and
the owner decides the default with that in hand (arch8 section 10). Either
way the run is `--memory 0` by construction, so the number is the
premise's and not memory's.

**Before Phase 0 merges:**

- the fifteen standalones pass `resolve` against their fragments, or each false discard is named with the word or the rule changed for it;
- every `keeps` literal is in its reference SQL, and every reference runs against the dataset;
- the follow-up accuracy with nothing remembered is in `docs/benchmark.md` with the date and the configuration, under `ensemble` and under `snippets`, since Phase 2 compares under both pipelines;
- the run was `--memory 0`, and the report says so.

## 4. Phase 1 -- the pipeline by name, and the plumbing that changes no answer

What it builds: `PIPELINE_VERSION` read, validated and derived into
`ensemble_enabled`; `ENSEMBLE_ENABLED` read as `None` when unsaid and
forwarded empty by compose; the state's `pipeline` and `nl2sql.pipeline`;
`Answer.pipeline`, `Pipeline.version`, the routing table's first line; the
snapshot's `pipeline` column under the generalised optional-column
pattern and the review service's `ALTER TABLE`; `--pipeline` on the CLI and
both scripts, `launch.sh`'s line and the probe's `ROUTE error`; the
benchmark's labels and `validate` after a configuration. Then the empty
half of memory: `Turn`, `MemoryNote`, the four state fields seeded,
`run(..., turns=())` on both agents, `memory` written with nothing shown,
the two prompts' placeholders rendering empty, `CONVERSATION_MEMORY` read
and refused out of range, `Pipeline.memory` and the two API settings,
`--memory` read and not yet used. Acceptance: the benchmark under `v7_0_0`
is 7.0's run to the SQL and under `v6_3_1` 7.0's `snippets`; every pinned
prompt byte for byte 7.0's; every API test green with `pipeline` present
and `conversation` `None`; the mirrors hold; a vote before and after the
migration; two `Settings` differing only in the name differ in
`ensemble_enabled` alone.

**R6 lands, live, held, stated.** The whole of the name is in this phase,
and so is its one exposure: a deployment that runs something other than
what it says. Three places it could, and what holds each:

1. **The default.** `ENSEMBLE_ENABLED` has been `true` by default since
   7.0, in compose and in `from_env`. Under the name it must be *unsaid* by
   default, or `v6_3_1` would contradict compose's own default on every
   start. So compose forwards it empty, `from_env` reads it with a `None`
   default, and `validate` resolves `None` from the name. The compose
   tests already hold the empty-forward pattern for `MAX_TABLES`
   (`test_a_forwarded_setting_the_host_has_not_set_arrives_empty`) and that
   an empty forward never overrides the image's own default; both gain
   `ENSEMBLE_ENABLED`. The check to add: a `Settings()` with no
   environment, a `Settings(pipeline_version="v6_3_1")`, and a
   `Settings(pipeline_version="v6_3_1", ensemble_enabled=True)` -- the
   first the ensemble, the second one run, the third refused with the
   sentence.
2. **A field set after construction.** `Settings` is a dataclass, and a
   test or the benchmark sets fields on it after `__post_init__` has
   resolved `None`. `settings.pipeline_version = "v6_3_1"` alone then
   leaves `ensemble_enabled` `True` from the default name, and the next
   `validate` refuses a contradiction nobody wrote. The implementation
   specification's rule: whoever sets the name sets `ensemble_enabled`
   back to `None` or to what they mean before `validate`;
   `settings_from_args` does, the benchmark's configurations set it
   explicitly, and the field's comment says so. What section 11 does not
   say is that the benchmark's `build_settings` never calls `validate` at
   all today (S3): a configuration applied under `PIPELINE_VERSION=v6_3_1`
   with `ensemble_enabled=True` would run the ensemble and label the
   report `v6_3_1`. The phase adds the call and the refusal.
3. **The terminal.** `launch.sh` says what the stack is about to do, and
   its ensemble line is asserted word for word by
   `tests/docker/test_launch_script.py`. The line changes to name the
   pipeline, read as `from_env` reads it -- the name first, the switch
   second -- and the test moves with it. The routing probe calls
   `Settings.from_env()` inside the agent image and catches only
   `RoutingError`; a contradiction raises `ValueError`, which today would
   print a traceback the script reports as "could not ask the agent
   image". The probe catches it and prints `ROUTE error`, so the
   contradiction is said before compose starts anything, as a routing
   error is (S9).

**The one-field test.** The owner's decision is that the name changes
one thing. A test that constructs two `Settings` differing only in
`pipeline_version`, resolves both, and asserts they differ in
`ensemble_enabled` and no other field is what keeps the decision true as
the code grows -- and it is the test a later owner reads to see what
`v6_3_1` is.

**R5 lands, held.** Two prompts gain a placeholder each, rendered empty.
`SUPERVISOR_PROMPT` gains `{memory_rule}` and `{memory}`,
`SQL_GENERATION_PROMPT` gains `{conversation_rule}`; all three default to
`""` through `.partial`, as `contract` and `snippets` already do. From
this phase on every caller renders the 7.0 prompt by construction, and the
tests that pin it -- `test_the_screening_prompt_sees_the_question_and_nothing_else`,
`test_no_examples_leaves_the_zero_shot_prompt_untouched` and their
neighbours -- are kept, not rewritten. The check to add: the three
placeholders listed in one test beside the two partials 7.0 has, so a
fourth added without a default is a failing test and not a changed answer.

**R7 lands, held, for one column.** The snapshot gains `pipeline` here
and `conversation` in Phase 3, so the pattern is generalised now, with one
column, and widened later. `OPTIONAL` becomes a tuple, `columns` asks the
catalog for every optional column in one query and keeps the set it found,
the `UNDEFINED_COLUMN` retry empties the set, and the log line names the
columns the table lacks. `tests/api/test_feedback.py`'s three cases for
the 7.0 column -- a migrated store sent the column and asked once, a store
from before 7.0 taking the verdict without it, the first vote after the
review service starts carrying it -- are parametrised over the tuple, and
`tests/review/test_store_live.py`'s migration case gains the column. The
rehearsal is Phase 3's and the release's.

**R1 and R3, the seams.** `asked`, `turns`, `memory` and `pipeline` are
seeded by `new_state` and `new_ensemble_state`, `run` takes `turns`, and
the lifetimes table gains its rows and the comment on `question`. Nothing
rewrites anything yet: `memory` is written with `shown = 0` and
`resolved = False`, and `asked` equals `question` in every run. The
invariant to hold from the first day: a state is handed its turns only by
`run` and the ensemble's `_answer`, never by anything on the wire
(`AskRequest` is a `Wire`, extras forbidden; `turns` is not a field of
it), which is arch7.3's R7 seam again, for a second seeded input.
Held by `tests/agent/test_state.py` (the four seeded; `turns` copied) and
by the wire models' construction.

**R2 held, trivially.** With no turns the Supervisor's prompt is 7.0's
and the generator's is 7.0's, so a question asked alone is answered as it
was -- which is the independence at zero, and the acceptance's "the same
run to the SQL" is its measurement.

**R4, R8, R9, R10, R11, R12: --.** No store, no snapshot `question` change,
no turns shown, no span input grown.

**Before Phase 1 merges:**

- the three `Settings` cases above; the one-field test; `build_settings` calling `validate` and `--config ensemble` under `v6_3_1` refused with the sentence;
- compose forwarding `ENSEMBLE_ENABLED` empty, both compose tests green;
- `launch.sh`'s line under each of the four combinations of name and switch, and the probe's `ROUTE error` on the contradiction, each asserted;
- the three placeholders listed beside the two partials in one test;
- the optional-column cases parametrised over the tuple; the migration adding `pipeline` on a 7.0 table;
- the benchmark under `v7_0_0` and under `v6_3_1` against 7.0's numbers, SQL for SQL, in the phase's write-up;
- `turns` reachable only through `run` and `_answer`; no such field on the wire.

## 5. Phase 2 -- memory in the agent

What it builds: `turn_from_state`, `render_turns`, `first_sentence`; the
Supervisor's `standalone`, `MEMORY_RULE` and the block; `screen` resolving
and rewriting `question` once; the generator's turns and
`CONVERSATION_RULE`; the rung rule; `render_answer`'s line; the ensemble's
anchor with turns and its candidates seeded; the three tags and the two
spans' inputs; the CLI's interactive conversation. Benchmark: `--followups
--memory 5` under both pipelines and `--independence` under `v7_0_0`.
Acceptance: the tests of sections 3.1 to 3.5; the follow-up accuracy with
five remembered against Phase 0's; the independence 15 of 15; model calls
per question unmoved; the Supervisor's tokens and wall time recorded.

This is the phase with risks of its own that no earlier phase can hold:
the first model-written question the pipeline runs that was not held to
an original by a two-way gate, and the first chance for memory to change
an answer it should not.

**R1 lands, live, held.** The chain, in the order the graph runs it:

1. `supervise` (or the ensemble's `screen`) -- the Supervisor on the words
   typed, with the turns rendered as history and the question as the last
   line. An injection attempt or an out-of-scope question in the words
   typed stops here, on those words; the turns cannot carry one, because
   only answered turns are remembered and each was screened on its own
   day, and what a turn holds beyond its question is the agent's own SQL,
   the rows it returned and the narrator's sentence.
2. `memory.resolve` -- in code, no model call: "not restated" for an empty
   or unchanged `standalone`; C1, C2, C3 against the words typed in subset
   form; C4's length. A restatement that drops a numeral, a literal or a
   direction is discarded with the check's name, and the question runs as
   typed with the Supervisor's contextual reading of entities, measure and
   period, which was read in context and is a better contract than one
   built from a fragment.
3. `question := read_as` -- the one rewrite, at the one node.
4. Everything after reads `question`: the retrievers, the Paraphraser, the
   fidelity gate's F4 screenings (with no turns), the Judge, the contract,
   the narrator, the record.

Held by `tests/agent/test_graph.py` (a restatement that drops a number is
never run: `question` unchanged and `memory.reason` naming C1; a held one
rewrites `question` and only `question`), `test_supervisor.py` (the prompt
with turns; `standalone` read defensively) and `test_memory.py` (each
check both ways). Two checks to make sure of: the model-call count of a
run whose restatement fails a check is the same as one whose restatement
holds (the checks cost nothing); and the verdict is given on the words
typed -- a fake Supervisor that refuses the standalone and proceeds the
typed words is a test that cannot be written, since the Supervisor gives
one verdict, so the test is that `refuse` renders the refusal for `asked`
and the state's `question` is still the words typed after a refusal.

**R2 lands, live, held, measured.** Two ways memory can change a question
that stands alone, and a way round each:

- **The Supervisor restates it anyway.** The description of `standalone`
  says "empty when the question already stands alone", and `resolve`
  treats an empty or unchanged restatement as not restated. What a model
  does with "What were net sales in FY2025?" after two turns about banners
  -- restate it as "What were net sales by banner in FY2025?" -- is the
  defect the independence number catches, and C1 to C3 would pass it (the
  restatement drops nothing). The way round is the prompt's sentence "A
  question that stands alone already is left as it is", and the number.
- **The generator borrows a filter.** The earlier turn's SQL carries a
  banner filter; the question asks for the whole company; the generator
  keeps the filter because the clause said a follow-up usually keeps them.
  The clause says *usually* and the system turn says the question asked is
  the last one; the consistency number is argued from the clause and the
  independence number bounds it.

Measured: `--independence` asks the fifteen alone and as the second turn
after an unrelated first, under `v7_0_0`, and the fraction equal must be
15 of 15 before the phase merges. A question memory changes is a defect of
the Supervisor's prompt or the generator's clause, fixed before the
default is five -- not a number to record and move on from. A second
measurement worth taking here, cheaply: the same fifteen with `--memory 0`
and `--memory 5` where the first turn is the *same* question, which is
the nearest thing to a person asking twice, and should be 15 of 15 too.

**R3 lands, held.** The rewrite happens here. What reads `question` before
the Supervisor: `new_state` (which also seeds `asked` from it), the root
span's input (`tracer.run(question, ...)` is called with the words typed),
and nothing else -- the retrievers run after it. What reads it after:
everything. Held by the test that compares every key of the Supervisor's
update against a run with no turns and finds `question`, `memory` and the
detail changed and nothing else; and by `test_graph_tracing.py`, which
checks the root span's input is the words typed and the Supervisor span's
outputs carry the rewritten `question`.

**R5 widens, held.** The placeholders now render: `MEMORY_RULE` and the
block in the Supervisor's prompt, the clause and the turns in the
generator's. The invariant is the one Phase 1 proved -- empty for no turns
-- and the check is that the pinned tests of Phase 1 still pass unchanged
while the new tests show each placeholder rendered with turns. One more:
the block ends with a blank line and the question is the last line of the
human turn, which the rule promises the model; a test holds the human
turn's last line.

**R9 lands, live.** With turns shown the Supervisor is routed at the
standard rung. On a host that serves one call at a time that is a few
seconds more per question in a conversation -- once under the ensemble
(the anchor only; the F4 screenings carry no turns and keep 7.0's rule),
once under `v6_3_1`. Recorded in the phase's benchmark: the Supervisor's
wall time with turns against without, beside its tokens. Not yet measured
is whether the standard rung is *needed*; that is Phase 5's probe, and
decision 45 says the rule goes back to light if the light model resolves
the follow-up set as the reference does.

**R12 lands, measured.** `TRACE_SPANS["supervise"]` and `["generate_sql"]`
(and the ensemble's `["screen"]`) read `turns` into their span inputs, so
a reader of the trace sees what each model was given (S10). The inputs
grow by fifteen turns at most, each three rows and a query -- small
beside the schema the generator's span already records -- but per
candidate and per attempt. The phase records the size of one traced
question's state at `--memory 15` beside 7.0's figure in
`docs/benchmark.md`, and `MEMORY_ROWS` and `MEMORY_ANSWER_CHARS` are the
bound.

**R10 live, in the CLI.** The interactive session remembers its turns,
and there is no verdict in a terminal; a wrong answer's turn stays for the
session. Nothing to build: the person can ask again, and a session is one
person's. Stated in `agent/USAGE.md` in Phase 4.

**R4, R6, R7, R8, R11: --.** No store yet; the name is built; the snapshot
is untouched by this phase.

**Before Phase 2 merges:**

- a restatement that drops a number never run; one that holds rewrites `question` and only `question`; the model-call count the same either way;
- the independence 15 of 15 under `v7_0_0`, and the same-question variant 15 of 15;
- the follow-up accuracy with five remembered against Phase 0's with none, under both pipelines, with the resolution rate and the discards by check;
- model calls per question unmoved from 7.0's, in the report;
- the Supervisor's wall time and tokens with turns against without; the traced state's size at `--memory 15`;
- the human turn's last line is the question; the pinned prompt tests unchanged and green;
- the root span's input is the words typed, the Supervisor span's outputs the reading.

## 6. Phase 3 -- the conversation over REST

What it builds: `api/conversations.py`; `AskRequest.conversation`,
`Job.conversation`, `Answer.conversation`, `Limits.max_conversation_id_length`;
`begin` at submit and `finish` on every terminal path; the 409; `DELETE
/v1/conversations/{id}`; the TTL and the cap; the verdict that forgets a
turn; the snapshot's `question` from the state and its `conversation`
column; `nl2sql.conversation` and `nl2sql.turn`. Acceptance: the two test
files as section 9 lists them; two questions through a real ensemble on
fakes in one conversation; the four terminal paths each freeing; a vote in
both orders on a live store.

This is the phase in which the conversation becomes a thing a process
keeps, and three risks land in the keeping.

**R4 lands, live, held.** The store is small and its failure modes are the
ordinary ones of a thing with a lock and a lifetime:

- **Busy forever.** `begin` marks a conversation busy with the job's id;
  only `finish` frees it. A job reaches a terminal state four ways --
  cancelled while queued, expired in the queue past `API_QUEUE_TTL_SECONDS`,
  the runner raising, the runner returning -- and `_run` covers three of
  them in one function while `cancel` covers the fourth from another
  thread. `finish` is the `finally` of `_run` and a line of `cancel`, and
  `tests/api/test_jobs.py` holds all four: after each, a second question
  in the conversation is admitted (S2). A busy conversation is never
  pruned, so the TTL cannot free one either; if `finish` were missed the
  conversation would be refused for the process's life, which is why the
  four cases are the phase's first check.
- **Pruned under a question.** The cap forgets the least recently touched;
  if that were the one busy, `finish` would append its turn to nothing.
  The store skips busy conversations when it prunes, and a test fills the
  cap with one busy and checks it survives.
- **The 409 before the 429, or after.** `submit` admits the job under the
  store's lock (`_admit_locked`) and then calls `begin`; a 429 is decided
  before the conversation is marked busy, so a refused question never
  leaves its conversation busy, and a 409 is raised with nothing to undo.
  Held by a test that fills the queue and asks in a conversation: 429, and
  the conversation free.
- **Growth.** The depth is a `deque`'s `maxlen`, the cap a count, each turn
  bounded by `memory.py`'s constants; nothing a client sends is kept but
  the id, bounded by its pattern. `/v1/meta` says all four numbers.

**R8 lands, live, held.** `capture_from` takes `question` from the job's
state rather than from `job.question`, and the snapshot's `question`
becomes the question answered -- the standalone when there was one. Two
things follow that section 11 does not spell out (S4): the review queue's
titles are made from the question column (`_title_from`), so a reviewer's
list shows the standalone and not the words typed; and
`tests/api/test_feedback.py`'s `test_the_snapshot_is_taken_from_the_job_not_from_the_request`
asserts the question is the job's, which stays true outside a conversation
and gains a case inside one where the two differ. The words typed are in
the same row, in `conversation.asked`, and the review page shows them in
Phase 4. Promotion reads `question` as today and promotes the standalone
with the chosen SQL, which is the only form a golden pair can be retrieved
by (arch8 section 23.6). Held by the feedback tests and the live store's.

**R10 lands, held.** A `no` on a job forgets its turn, after the sink has
recorded the verdict and not before, so a verdict the staging database
refused forgets nothing. `forget_turn` finds the turn by the job's id in
whichever conversation holds it, through an index `finish` keeps (S11);
`answered` is not decremented, so the next turn's number does not reuse a
forgotten one's; `incomplete` and `yes` forget nothing; withdrawing the
verdict does not bring the turn back (the person asks again). Held by
`test_memory_wire.py` and `test_conversations.py`.

**R11 lands, held.** The id is validated by the pattern on `AskRequest`
(`[A-Za-z0-9._-]{1,64}`), so a 422 says what is wrong before any store is
asked; the key is `(owner, id)` with `owner` the signed-in principal or
`None`, so two people's ids of one spelling are two conversations and
`tests/api/test_signin.py` gains the case; with sign-in off every caller
is the same caller, as for the jobs, and the client's random id keeps
conversations apart. A reused id after the TTL begins at turn 1, which
`Answer.conversation.turn` shows. `DELETE` forgets only the caller's own:
a stranger's id of the same spelling is a 404, not a 403, for the reason
`require_job` gives.

**R3 widens, held.** The job's `question` is the words typed (it was
submitted with them) and the answer's `conversation.asked` says the same;
the answer's `read_as` is the state's `question` when it was restated; the
snapshot's `question` is the state's. Three documents, one rule: the wire
and the snapshot carry both, labelled. `test_translate.py` holds
`answer_from_state` with and without a conversation.

**R7 widens, held.** The second column, `conversation`, under the pattern
Phase 1 generalised. The live store's test adds it on a 7.0 table and on a
Phase 1 table (which has `pipeline` and not `conversation`), and the
writer's columns are held to the store's by the contract tests both ways.
The rehearsal in both orders is the release's.

**R1 widens.** The turns reach the agent from a store the API fills from
its own finished states -- `turn_from_state` in `finish` -- and from
nowhere else. The seam Phase 1 cut holds: nothing on the wire is a turn.

**R12 widens.** The job store tags `nl2sql.conversation` and `nl2sql.turn`
through `tagged`, beside `nl2sql.job_id`, since the id and the turn are
the job's; `tests/api/test_jobs.py` holds the tags as it holds the job's.

**R2, R5, R6, R9: --.**

**Before Phase 3 merges:**

- the four terminal paths each freeing the conversation, and a 429 leaving it free;
- a busy conversation surviving the cap; the turn number right after the window has slid;
- a `no` forgetting the turn after the sink records, `incomplete` not, and the withdrawn verdict not restoring it;
- two people's same id two conversations; a stranger's DELETE a 404;
- the snapshot's `question` the standalone inside a conversation and the job's outside, with `conversation.asked` beside it;
- the second column on a 7.0 table and on a Phase 1 table, both reading `{}` for old rows;
- two questions through the ensemble on fakes in one conversation, the second's generator prompt carrying the first's SQL.

## 7. Phase 4 -- the surfaces

What it builds: the page and the window -- the id, the "New conversation"
button, the "Read as" line, the status bar's sentence; the review page's
"asked as"; `agent/USAGE.md`'s sentence; every document in section 7 but
the catalog's; `arch_v8.svg`; the counts. Acceptance: `--run-node` and
`--run-java` green with the new tests; `tests/docs` green with the moved
counts; the acceptance tier green from a clean start with a follow-up
answered through the web interface.

**R1 seen, stated.** "Read as: ..." under the question is the way round
made visible: a person answered wrongly sees whether the reading or the
query went wrong. The page shows it only when `answer.conversation.resolved`;
a discarded restatement shows nothing, and `reason` is in the job's
document for whoever looks. `docs/SECURITY.md` gains the row: a turn is
history, the restatement held in code, both GUIs rendering it as text.

**R11 live, seen.** The page makes its id on mount and remakes it on the
button; the window the same per window. The session list stays a list of
jobs, so a new conversation does not empty it -- which is the one thing a
person could misread, and `docs/web_interface.md` says so in a sentence.
The acceptance tier's second question is where a person first sees turn 2
answered in the light of turn 1.

**R4 seen.** A 409 reaches the page only if it sends a second question
before the first's answer is on screen, which the ask box does not allow;
the window the same. The status bar shows the 409's message if it ever
arrives, as it shows a 429's.

**R6, R8, R9, R10 stated.** The documents carry the person's and the
deployer's half of each way round:

- `agent/README.md`, configuration: `PIPELINE_VERSION` names the pipeline
  and sets nothing else; `ENSEMBLE_ENABLED` unset follows the name, set it
  is what you said, and a contradiction is refused; `GRAIN_CHECK_ENABLED`
  is the other switch a comparison of the releases as published would set
  (R6).
- `docs/feedback.md` and `review/README.md`: the snapshot's `question` is
  the question answered and `conversation.asked` the words typed; the
  review page shows "asked as" (R8). The curation page shows nothing new:
  it shows golden pairs, snippets and fixes, never a staged question (S7).
- `agent/README.md`, model routing: the Supervisor is routed standard with
  turns shown; a pin applies as for every task (R9).
- `agent/USAGE.md`: the interactive session remembers the last N turns; a
  wrong answer's turn stays for the session, so ask again or start a new
  one; a question on the command line is asked alone (R10).

**R3 seen.** The page shows "Read as" under the words typed, which is the
two fields side by side; `docs/rest_api.md` names both.

**R2, R5, R7, R12: --.** `tests/gui/test_gui_contract.py` and
`tests/java/test_desktop_contract.py` hold the mirrors; a leaked or
misnamed field would have failed in Phase 3.

**Before Phase 4 merges:**

- the "Read as" line present only when resolved, in both clients' tests;
- the button remaking the id and leaving the session list;
- the acceptance tier's second question: `conversation.turn` 2, `read_as` a complete sentence, the first turn's SQL in the second's generator span (`docs/tracing.md`'s page is where a person sees it);
- the five documents above carry the sentences listed; `agent/USAGE.md`'s old sentence gone and `test_no_document_or_comment_says_what_stopped_being_true` gains it as a forbidden phrase;
- `arch_v8.svg` adds no node to either graph, held by `test_arch_diagrams.py`.

## 8. Phase 5 -- calibration

What it builds: the Supervisor's probe gains the follow-up set, each case
screened with its first turn and scored by `contract.same_contract` of the
restatement's contract against the standalone's reference contract; the
catalog's `supervisor` entry gains a `remembered` measurement per rung;
the committed catalog rebuilt once; `docs/model_catalog.md`'s sentence.
Acceptance: the probe reports per rung; a 7.0 catalog loads and routes by
the rule; the document names no model.

**R9 measured.** The rule "standard with turns shown" was chosen without a
number (decision 45); this is where the number is taken. Per rung, the
fraction of fifteen follow-ups whose restatement builds the reference's
contract, and the fraction discarded by each check. If the light rung
resolves the set as the reference does, the rule goes back to light and
`supervisor_rung` loses its `remembered` branch -- a departure the next
specification records. If it does not, the rule stands and the catalog
says why.

**R1 measured.** The probe's discards by check are the checks' false
rejection rate on a model's restatements, which Phase 0 could only take on
a person's: a check that discards restatements the reference contract
would have accepted is too strict, and the word goes in the extractor's
list.

**R2 measured.** The probe can ask each benchmark question with an
unrelated turn shown, as `--independence` does, per rung: a rung whose
model restates what stands alone is unsuited to the task with turns
however well it screens without them. One more number per rung, and the
one that says whether a light model is safe to show turns to.

**The catalog's shape (S14).** The measurement is an optional key of a
schema-3 catalog; `load_catalog` reads a catalog without it as unmeasured
for the Supervisor with turns and routes by the rule, so a deployer's
calibrated 7.0 catalog loads unchanged. Held by `tests/models/test_calibrate.py`
on a schema-3 fixture without the key, beside the schema-2 fixture arch7.3
required.

**R3 to R8, R10 to R12: --.** Calibration runs the probes as the
calibrator's own process.

**Before Phase 5 merges:**

- the probe's three numbers per rung in the phase's report, and the rule kept or changed with the number beside it;
- a catalog without the key loading and routing by the rule;
- the document generic -- the names of tasks and rungs, no per-model results, as the owner's standing rule has it.

## 9. Release

What it does: the version in every declaration `tests/docs/test_versions.py`
holds together; `setup.sh` pinning `v8_0`; the changelog entry; the
upgrade rehearsed on an isolated instance built from a `git archive` of
7.0.0 -- a vote before and after the review service's first 8.0 start, a
question in a conversation through the page, the window and the CLI,
`PIPELINE_VERSION=v6_3_1` started once and `/v1/meta` read -- then every
image published, the published-tag tests, and the running stack upgraded
in place. A published tag never moves; a correction is `v8_0_1`.

**R6 verify, as an upgrade note.** A 7.0 deployment whose `.env` sets
nothing gets `v7_0_0`, which is 7.0.0 to the answer. One whose `.env` sets
`ENSEMBLE_ENABLED=false` gets one run under `v7_0_0`, as before, and the
note says that `PIPELINE_VERSION=v6_3_1` is the same answer with a name the
record carries. The rehearsal starts the archive's `.env` with
`ENSEMBLE_ENABLED=true` beside `PIPELINE_VERSION=v6_3_1` once and reads
the refusal in `launch.sh`'s output before anything starts.

**R7 live, verify.** The rehearsal's store is a 7.0 store with
submissions: a vote before the review service's first 8.0 start is
recorded without the two columns and the log says so once; the review
service adds them; a vote after carries both; the old rows read `''` and
`{}` and the review page shows them as a question asked alone.

**R4 verify.** `API_MAX_CONVERSATIONS` (200) conversations of
`CONVERSATION_MEMORY` (15 at most) turns, each three rows and a query, is
small beside the job store's 2 GB ceiling and the state sizes arch7.3's
release recorded; the line is the arithmetic, and the rehearsal's
`docker stats` after the conversations above.

**R1, R2 verify.** The acceptance tier green from a clean start with
Phase 4's follow-up; the release's benchmark run against the published
tags prints the follow-up accuracy, the resolution rate and the
independence, and 15 of 15 on the last is a release blocker as a rejected
reference is for the grain check.

**R8 verify.** The rehearsal's vote on a follow-up, reviewed on the page:
the standalone as the question, the words typed beside it, the promoted
pair standing alone.

**R11 verify.** The page and the window open at once on the rehearsal: two
ids, two conversations, neither reading the other's turn.

**R12 verify.** The rehearsal's trace in MLflow: the Supervisor's span
showing the turns, the five tags, `nl2sql.read_as` on the root.

**R3, R5, R9, R10: nothing beyond the suites.**

## 10. What placing them surfaced

Section 11 is not edited in place; these are recorded here, for the build
to act on and the next specification to carry as departures or additions.
Each names the phase it belongs to.

| Id | What | Phase | The way round |
|---|---|---|---|
| S1 | Section 8's Phase 0 test calls `memory.resolve`, which section 10's first draft placed in Phase 1 | 0 | build `resolve` and its check cases in Phase 0 (the implementation specification already does); the false-discard rate on the fifteen is Phase 0's second number |
| S2 | A conversation is freed only by `finish`, and a job reaches a terminal state four ways in two functions | 3 | `finish` in `_run`'s `finally` and in `cancel`; a test per path, each followed by a second question admitted |
| S3 | `benchmarks/run_benchmark.py`'s `build_settings` never calls `validate`; a configuration applied under `PIPELINE_VERSION=v6_3_1` with the ensemble on would run and be labelled wrongly | 1 | `validate` after the configuration; `--config ensemble` refused with the sentence; `--compare` running the four and naming the fifth as refused |
| S4 | The snapshot's `question` becomes the question answered: the review queue's titles come from it, and one existing test asserts it is the job's | 3 | the words typed in `conversation.asked` of the same row; the test gains the inside-a-conversation case; the review page shows both in Phase 4 |
| S5 | `Answer.conversation` cannot be built from the state alone: the id and the turn are the job's | 3 | `answer_from_state(..., conversation=, turn=)` passed by `job_model` and `capture_from`; the CLI's `--json` names its session |
| S6 | What `--compare` does under `v6_3_1` -- arch8 says "refuses the ensemble configuration" | 1 | the four one-run configurations run and the comparison's footer says the fifth was refused and why, rather than the whole comparison refusing |
| S7 | arch8 section 23.7 says "the review and curation pages show asked as"; the curation page shows golden pairs, snippets and fixes and never a staged question | 4 | the review page only; nothing on the curation page; the next specification corrects the sentence |
| S8 | The independence run asks every question twice | 2 | `--independence` is its own flag, not part of `--followups` |
| S9 | `launch.sh`'s routing probe catches `RoutingError` only; a contradiction between the name and the switch raises `ValueError` inside the image and would be reported as "could not ask the agent image" | 1 | the probe catches `ValueError` and prints `ROUTE error`, so the refusal is said before compose starts anything; the existing ensemble line's test moves with the line |
| S10 | The Supervisor's and the generator's spans record `question` and not the turns they were shown | 2 | `TRACE_SPANS["supervise"]`, `["generate_sql"]` and the ensemble's `["screen"]` read `turns`; `docs/tracing.md` says a reader sees what the model was given |
| S11 | A verdict names a job, and the job's turn may be in any of two hundred conversations | 3 | an index `job_id -> (owner, id)` kept by `finish`; `answered` never decremented; a verdict on a job of a forgotten conversation is a no-op |
| S12 | C4's bound is `MAX_QUESTION_LENGTH`, which lives in `api/models.py`; `memory.py` cannot import the API package | 1 | the constant moves to `memory.py` and `api/models.py` imports it: one home, and the request's bound and C4's the same number by construction |
| S13 | `ENSEMBLE_ENABLED` must be readable as unsaid, and a dataclass field set after construction is not re-resolved | 1 | `env_bool`'s default widens to `bool \| None`; `validate` resolves `None`; whoever sets the name sets the switch back to `None` first, which `settings_from_args` does and the field's comment says |
| S14 | A calibrated 7.0 catalog has no measurement for the Supervisor with turns | 5 | an optional key of schema 3, read as unmeasured; a fixture without it in the calibrator's tests |
| S15 | Phase 0 has nothing to hand a turn to | 0 | the follow-up accuracy with nothing remembered is the fragment asked alone, which is what 7.0 does with a follow-up; `--memory` forced to zero and the report saying so |
| S16 | The acceptance tier gains a second question through the ensemble | 4 | about a minute more on a clean start; the second question is the follow-up, so the tier verifies R1, R2 and R11 in one run |

Of the sixteen, S2 and S3 are the two that would have shipped as defects:
every other item is a measurement taken earlier, a constant moved or a
sentence placed. S2 is a conversation refused until the process restarts
because one of four paths forgot to free it, and section 11 named the
exposure without counting the paths. S3 is the benchmark running the
ensemble under the name of one run -- the one thing the owner's decision
on 42 was for -- because the function that applies a configuration has
never needed to validate what it applied.
