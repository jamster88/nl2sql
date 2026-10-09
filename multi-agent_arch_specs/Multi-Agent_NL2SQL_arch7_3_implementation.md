# Multi-Agent NL2SQL v7.3: implementation specification

**Status:** the build plan for
[`Multi-Agent_NL2SQL_arch7_3.md`](Multi-Agent_NL2SQL_arch7_3.md), written
2026-10-09 against the tree as 7.0.0 built it through Phase 3 -- arch7.2's
implementation specification
([`Multi-Agent_NL2SQL_arch7_2_implementation.md`](Multi-Agent_NL2SQL_arch7_2_implementation.md),
which stands where this one does not change it) with the owner's decisions
on arch7.2's open questions 32 to 35: Phase 6, the grain check (section
3.9), ships in 7.0.0, on by default, its grains measured from the data, and
its rule catches tables of different grains joined on no date as well as
on the date. Phases 0 to 3 are built; Phases 4 to 6 are not. arch7.3 says
*what* and *why*; this document says *where*, *how* and *in what order*:
every setting, module, function, state field, wire model, prompt, test and
document the build touches, with the acceptance criterion of each phase.
Where this document and arch7.3 disagree, arch7.3's design decision stands
and this document is wrong; where this document and the code as built
come to disagree, the code and the changelog are right and the next
specification records the departure, as this one's predecessor recorded
Phase 3's.
[`Multi-Agent_NL2SQL_arch7_3_risks_by_phase.md`](Multi-Agent_NL2SQL_arch7_3_risks_by_phase.md)
changes the phases below, and wins where the two differ.

**The version.** The release that ships this is a new major -- the pipeline
changes shape -- and is 7.0.0, tag `v7_0`, the owner's choice
(2026-10-08). Every rule of a release applies: a published tag never moves,
the acceptance tier passes before anything is pushed, and a CHANGELOG entry
names every artifact.

**Out of scope.** Nothing in the containers, the databases, sign-in, the
proxy or the stores changes; no new image, port, role, volume or secret.
Everything below is code in the agent's image, the two clients that mirror
its wire, the benchmark, and the documents.

---

## 1. Settings

Every setting is read in `agent/nl2sql_agent/config.py` by `Settings.from_env`
through the existing helpers, carried as a field of `Settings`, settable
through `docker-compose.yml`'s agent and API services, documented in
`agent/README.md`'s configuration table, and -- for the ones a person asks
one question with -- a flag of the CLI. The compose tests hold the first two
both ways (`tests/docker/test_compose_config.py`,
`test_every_setting_the_agent_reads_can_be_set_through_compose`, through
`tests/settings_names.py`), so a setting added to `from_env` and not to
compose fails a test, and so does the reverse.

| Variable | `Settings` field | Type, default | Validation | Read by |
|---|---|---|---|---|
| `ENSEMBLE_ENABLED` | `ensemble_enabled` | bool, `true` | -- | `api/app.py` (which agent answers), `__main__.py` |
| `ENSEMBLE_PARAPHRASES` | `ensemble_paraphrases` | int, `3` | 3 to 10 inclusive, else `ValueError` at construction naming the bounds | `ensemble.py` (`plan_wave`) |
| `ENSEMBLE_MAX_PARAPHRASES` | `ensemble_max_paraphrases` | int, `10` | at least `ensemble_paraphrases`, at most 10 | `paraphrase.py` (how many to write), `ensemble.py` |
| `ENSEMBLE_WAVES` | `ensemble_waves` | int, `2` | at least 1 | `ensemble.py` (`_route_after_vote`) |
| `ENSEMBLE_DEADLINE_SECONDS` | `ensemble_deadline_seconds` | float, `0` (none) | at least 0 | `ensemble.py` (`plan_wave`) |
| `ENSEMBLE_JUDGE_ENABLED` | `ensemble_judge_enabled` | bool, `true` | -- | `ensemble.py` (`_route_after_validate`: the Judge before every vote; off, the runs vote alone) |
| `ENSEMBLE_MAX_CLAIMS` | `ensemble_max_claims` | int, `8` | at least 1 | `fuse.py` |
| `ENSEMBLE_FUSE_COLUMNS` | `ensemble_fuse_columns` | bool, `true` | -- | `fuse.py` (the column join; off leaves the representative's columns and sets `decision.columns_fused = False`) |
| `OLLAMA_PARALLEL_CALLS` | `ollama_parallel_calls` | int, `1` | at least 1 | `hostgate.py` (the slots), `ensemble.py` (the pool's width) |
| `MODEL_ROUTE_PARAPHRASER` | `model_route_paraphraser` | str, `""` | as the five pins (`router.parse_pin`) | `router.py` |
| `MODEL_ROUTE_JUDGE` | `model_route_judge` | str, `""` | as the five pins | `router.py` |
| `GRAIN_CHECK_ENABLED` (Phase 6) | `grain_check_enabled` | bool, `true` (the owner, 2026-10-09) | -- | `graph.py` (`_validate_static` passes the grains to `validate.validate` only when on) |
| `GRAIN_PERIODS` (Phase 6) | `grain_periods` | str, `fiscal_year+fiscal_week_num,fiscal_year+fiscal_month_num` | each period a `+`-joined list of the date dimension's columns, finest first; a name the date dimension lacks refused at start | `grain.py` (which periods a grain can be) |

Validation lives in `Settings.__post_init__`, which does not exist today
and is added for these; a value out of range stops the agent at start with
the variable's name and the bound, which is how a misconfigured `MAX_ROWS`
should also have behaved and does not -- leave that alone.

CLI flags (`__main__.py`, `parse_args` and `settings_from_args`):
`--ensemble` / `--no-ensemble` (BooleanOptionalAction, default from the
environment), `--paraphrases N`, `--parallel-calls N`, `--fuse-columns` /
`--no-fuse-columns`. The help strings follow the file's pattern, with
`_default(...)`.

Compose: the agent service and the API service both list every variable
as `${NAME:-default}`, the way `MAX_ATTEMPTS` is listed, so `.env` can set
it and nothing need be written to `.env` for the default to apply.
`setup.sh` writes nothing new.

## 2. The state contract

### 2.1 `ensemble_state.py` (new)

The outer state, as arch7.3 section 22.2 declares it, with the same
construction as `state.py`: dataclasses for the payloads (so `to_jsonable`
renders them), a `TypedDict` with `total=False`, reducers on every key more
than one node or worker writes, a lifetimes table and a reset function, and
a test that holds the table to the fields.

```python
WAVE = "wave"   # a fourth lifetime: emptied by plan_wave when a second wave starts

@dataclass
class Paraphrase:
    index: int                      # 1..10; the original is candidate 0 and is not a Paraphrase
    text: str
    changed: str                    # the Paraphraser's own words for what it varied
    status: str = "pending"         # pending | faithful | discarded
    reason: str = ""                # "F2 literals: 'Dairy & Eggs' not found", etc.
    screening: dict[str, Any] | None = None   # verdict, intent, clarification, entities, measure, period

@dataclass
class Candidate:
    index: int                      # 0 the original; otherwise the paraphrase's index
    wording: str
    origin: str                     # "original" | "paraphrase"
    wave: int
    state: dict[str, Any]           # the inner AgentState, whole
    outcome: str                    # "answered" | "gave_up" | "refused"
    admissible: bool = False
    reasons: list[str] = field(default_factory=list)   # "E1 answered: gave up after 7 attempts", ...
    signature: str = ""
    group: int | None = None
    started_ms: float = 0.0         # wall time of the run, for the record
    ms: float = 0.0

@dataclass
class Group:
    index: int                      # 0 is the largest
    members: list[int]              # candidate indices
    representative: int
    signature: str

@dataclass
class Agreement:
    admissible: int = 0             # the runs that voted: passed E1-E5 and not set aside
    agreed: int = 0
    total: int = 0
    level: str = "none"             # unanimous | majority | judged | contested | single | none
    why: str = ""
    set_aside: int = 0              # the runs the Judge kept from voting; 0 when it accepted none

@dataclass
class GroupVerdict:
    group: int                      # the group's index
    accepted: bool
    why: str = ""

@dataclass
class Judgement:
    verdicts: list[GroupVerdict] = field(default_factory=list)   # one per group, in group order
    set_aside: list[int] = field(default_factory=list)           # candidates whose answer it rejected
    overruled: bool = False         # it set aside the group the runs alone would have chosen
    instead_of: int | None = None   # that group's representative, when it did
    model: str = ""
    error: str = ""                 # why it could not be asked; the runs then vote alone

@dataclass
class JoinedColumn:
    column: str
    from_candidate: int
    key: str                        # the entity key it was joined on
    table: str

@dataclass
class DeclinedColumn:
    column: str
    from_candidate: int
    why: str                        # "row 3 has no match", "key repeats in run 2", "not a dimension the rows identify"

@dataclass
class Dissent:
    group: int
    members: list[int]
    signature: str
    differs: str                    # from two ASTs: tables and filtered columns each side has alone

@dataclass
class Decision:
    chosen: int | None = None
    fused_from: list[int] = field(default_factory=list)
    columns_fused: bool = True      # the toggle's value for this run
    joined_columns: list[JoinedColumn] = field(default_factory=list)
    declined_columns: list[DeclinedColumn] = field(default_factory=list)
    claims_added: int = 0
    claims_dropped: int = 0
    dissent: list[Dissent] = field(default_factory=list)
    line: str = ""                  # the agreement line, as rendered
```

```python
class EnsembleState(TypedDict, total=False):
    question: str
    principal: str | None
    verdict: Verdict
    intent: Intent
    clarification: str | None
    answer_contract: AnswerContract
    screening: dict[str, Any] | None                  # the anchor's reading, candidate 0's seed
    paraphrases: list[Paraphrase]
    paraphrase_retried: bool                          # the Paraphraser's one retry spent
    waves: int
    deadline: float                                   # monotonic; 0.0 when none
    wave_plan: list[int]                              # a wave field: who runs now
    parallel_calls: int                               # the width, for the wire's record
    candidates: Annotated[list[Candidate], upsert_candidates]   # appended; a run marked where it stands
    groups: list[Group]
    judgement: Judgement | None
    agreement: Agreement
    decision: Decision
    answer: str
    narrative: str
    sql: str
    result: QueryResult | None
    chart: ChartSpec | None
    claims: list[Claim]
    audit: AuditReport
    assumptions: list[str]
    error: str | None
    node_errors: Annotated[dict[str, str], merge_errors]
    trace: Annotated[list[TraceEntry], operator.add]
    trace_id: str
```

`new_ensemble_state(question, *, principal, deadline, parallel_calls)`
seeds every field, as `new_state` does. As built, one field more:
`another_wave: bool`, the vote's word that a second wave runs, decided
once where the deadline is read (section 3.8.9). `LIFETIMES` marks
`wave_plan`, `another_wave`, `groups`, `agreement`, `judgement` and
`decision` as `wave` and everything else as `run`; `wave_reset()` returns
the six, empty. `judged(judgement)`
says what the Judge did in a word -- `not asked`, `failed`, `accepted`,
`set aside`, `overruled`, `accepted none` -- for the trace's tag and the
benchmark. `tests/agent/test_ensemble_state.py` holds the
table to the fields and the reset to the table, as `test_state.py` does for
`AgentState`.

### 2.2 `state.py` (touched)

`AgentState` gains `screened: bool` (lifetime `run`; `LIFETIMES` and
`new_state` updated; `tests/agent/test_state.py` follows). `new_state`
gains a keyword `screening: dict | None = None`: when given, it seeds
`verdict`, `intent`, `clarification` from it and sets `screened = True`;
the contract is built by the Supervisor node from the screening's
`entities`, `measure` and `period` (section 3.8, `_supervise`), so `new_state` stays free
of the catalog.

## 3. New modules

All in `agent/nl2sql_agent/`. Each has a module docstring in the package's
voice -- what it is, why it is shaped so, which section of arch7 it is --
and a test file of its own under `tests/agent/`. Nothing imports
`benchmarks/`.

### 3.1 `hostgate.py` -- the host gate

```python
class HostGate:
    """OLLAMA_PARALLEL_CALLS slots for calls to the model host, process-wide (arch7 section 22.9)."""
    def __init__(self, slots: int) -> None: ...
    @contextmanager
    def slot(self) -> Iterator[None]: ...      # acquire; release on return or exception
    @property
    def slots(self) -> int: ...
    @property
    def waiting(self) -> int: ...              # for the trace's detail and a test

def gate_for(settings: Settings) -> HostGate   # one per process, keyed by slots; built on first use
```

A `threading.BoundedSemaphore(slots)`. `RoutedModel._traced_call` wraps
`call(self._client(name))` in `gate.slot()`, so every model call the agent
makes -- the five tasks and the two new ones, every candidate's, every
job's -- goes through it; nothing else does (the embedder is on another
host and is not gated). `OLLAMA_TIMEOUT` is already on every client, so a
hung call releases its slot when the client gives up. The gate is
process-wide on purpose: the API's two workers are two callers of one host.

Tests (`tests/agent/test_hostgate.py`): with one slot two threads' calls
never overlap (a fake client records entry and exit times); with two they
may; a call that raises releases its slot; `gate_for` returns the same gate
for the same settings.

### 3.2 `compare.py` -- the scorer, moved

`values_match`, `_row_matches`, `result_matches`, `_sorted_rows`,
`_sort_key` and their constants (`ABSOLUTE_TOLERANCE`, `RELATIVE_TOLERANCE`,
`ROUNDING_DECIMALS`, `MAX_COLUMN_ASSIGNMENTS`) move here from
`benchmarks/runner.py` unchanged; `benchmarks/runner.py` imports them back
by name so every existing caller and test keeps working, and
`tests/benchmarks/test_run_benchmark.py`'s scorer tests are moved to
`tests/agent/test_compare.py` with their cases intact (the two kinds of
mistake it could make: values present but on the wrong rows; a reordered
column read as a different answer). `.coveragerc`'s include list already
covers the agent package.

### 3.3 `paraphrase.py` -- the Paraphraser

```python
class Rewording(BaseModel):
    text: str
    changed: str = Field(description="a few words on what was varied: form, verb, order, register, synonym")

class Rewordings(BaseModel):
    rewordings: list[Rewording]

PARAPHRASE_PROMPT: ChatPromptTemplate     # arch7.3 section 22.3's text, verbatim, in prompts.py

def invariants(contract: AnswerContract) -> dict[str, str]   # entities, measure (as stated), period, limit, in words
def paraphrase(llm, question: str, contract: AnswerContract, *, count: int,
               failed: Sequence[tuple[str, str]] = (), start: int = 1,
               written: Sequence[str] = ()) -> list[Paraphrase]
```

One structured call (`llm.with_structured_output(Rewordings)`), as the
Supervisor makes its. `count` is `ensemble_max_paraphrases`. `failed`, on
the one retry, is the discarded rewordings with their reasons, rendered as
a block ("These were rejected: ... because ...; write replacements that
avoid the same mistakes"). The result is normalised -- whitespace collapsed,
a trailing question mark kept, empty strings dropped, none kept twice
nor one already `written` -- and numbered from `start` in the order
returned. It is never shown the contract as the generator sees it, and the
measure only as `contract.stated_measure` gives it (arch7.3 section 22.3). A model failure (`MODEL_ERRORS`) raises; the node
catches it and writes `node_errors["paraphraser"]` (section 3.9). Routed
as `router.model("paraphraser", *complexity.paraphraser_rung(question))`.

Tests: the prompt carries every invariant in words and no column name;
the output is numbered and normalised; the retry block names each
failure; a `None` from the model is an empty list, not a crash.

### 3.4 `fidelity.py` -- F1, F2, F3, F5; and `contract.same_contract` for F4

```python
NUMBER_WORDS = {"one": "1", ..., "twenty": "20"}
HIGH = ("top", "highest", "best", "most", "largest", "biggest", "greatest", "maximum", "max", "leading", "strongest", "peak")
LOW = ("bottom", "lowest", "worst", "least", "fewest", "smallest", "minimum", "min", "weakest", "poorest")
UP = ("increase", "increased", "increasing", "grew", "grow", "growth", "rose", "rise", "rising", "gain", "gained", "improved", "up")
DOWN = ("decrease", "decreased", "decreasing", "decline", "declined", "declining", "fell", "fall", "falling", "drop", "dropped", "shrank", "shrink", "loss", "lost", "down")
NEGATION = ("not", "no", "never", "without", "excluding", "exclude", "excluded", "except", "non")

def numerals(text: str) -> Counter[str]          # F1: digits (commas stripped) and NUMBER_WORDS, as strings
def literals(text: str) -> set[str]              # F2: quoted strings; capitalised multi-word phrases (joined by &, and, of, the);
                                                 #     tokens mixing letters and digits (FY2025, Q4); case-folded
def polarity(text: str) -> set[str]              # F3: {"high", "low", "up", "down", "negation"} present
def distinct(text: str, others: Sequence[str], *, max_jaccard: float = 0.8) -> bool   # F5
def check(original: str, rewording: str, kept: Sequence[str]) -> str | None
    # None when F1, F2, F3 and F5 pass; else "F1 numbers: ..." naming the check and the difference
```

`contract.same_contract(a, b) -> bool` and `contract.differences(a, b)
-> list[str]` (F4): the same set of `(key, label, table)` over the
entities the label map resolved, the same `stated_measure` (one that
contains the other's named measure is the same), the same `period` --
compared by its words, with "FY", "Q" and ordinals spelled out -- and
`period_default` and `fiscal_year`, the same `ranked` and `limit`. Intent
is not compared (arch7.3 section 22.3). The rewording's contract is built
in the original's shape: `Nl2SqlAgent.screen(text, shaped_by=question)`
reads the rewording and builds its contract with the original's one-number
or list, ranking and row count, which F1 and F3 have held the words to, so
in practice F4 compares the entities, the measure and the period.

The word lists are the starting vocabulary; `benchmarks/paraphrases.py`
(section 8) is where a false rejection shows, and the lists grow from
there. Tests (`tests/agent/test_fidelity.py`): each check on a passing and
a failing pair from arch7's table ("top ten" / "top five"; "Dairy & Eggs" /
"dairy"; "lowest" / "highest"; two rewordings differing by a comma); the
number words; a quoted literal with an apostrophe; `same_contract` on
contracts differing in each field, and on two readings of one question
that differ only in intent.

### 3.5 `agreement.py` -- admissibility, agreement, the vote, the representative

```python
def admissible(candidate: Candidate, contract: AnswerContract, label_map: LabelMap, *,
               question: str, faithful: bool = True) -> list[str]
    # E1..E5 failures, each "E3 complete enough: no rows, for a question that implies some"; empty when admissible

def agree(a: QueryResult, b: QueryResult, *, ordered: bool) -> bool
    # result_matches(a.rows, b.rows, ordered=ordered) or result_matches(b.rows, a.rows, ordered=ordered)

def group(candidates: Sequence[Candidate], contract: AnswerContract, label_map: LabelMap) -> list[Group]
    # union-find over every admissible pair (ordered = contract.ranked); groups ordered by size desc,
    # then "holds candidate 0", then lowest member

def signature(result: QueryResult, label_map: LabelMap) -> str
    # a scalar's value; else "N rows; first: <label values>, <measure value>"

def vote(groups: Sequence[Group], admissible_count: int, total: int) -> Agreement
    # unanimous | majority | single | none | the no-majority marker ("open")

def decide(groups: Sequence[Group], admissible_count: int, total: int,
           judgement: Judgement | None) -> tuple[Agreement, Group | None, Judgement | None]
    # the vote after the Judge: only the accepted vote; judged / contested / the runs' own choice

def rank(candidates: Sequence[Candidate]) -> Candidate
    # the representative: complete > audited > original > fewer attempts > lower plan cost > lower index
```

E3 calls `completeness.check_rules` with the anchor contract, the
candidate's `result`, `sql` and the label map, the way `completeness.review`
calls it, and treats as fatal only the empty-result case
(`question_implies_rows`); other gaps are recorded on the candidate but do
not exclude it. E4 reads `candidate.state["audit"].semantic_issue`, and
nothing else (arch7.3 section 22.5). E5 reads
`result.truncated` and `contract.ranked`. `rank` reads `completeness.accepted_gaps`
(empty is better), `audit.unsupported_claims` (empty is better), `origin`,
`attempts`, `plan_cost`, `index`.

`decide` is arch7.3 section 22.7's table: `vote` over every admissible
group is the runs' own choice; the groups the Judge accepted -- those it
gave no verdict on included -- vote, `admissible_count` less the runs it
set aside; the winner is the first accepted group in `group`'s order. The
level is `judged` when that winner is not the runs' own choice, an open
vote is `contested`, and with no verdicts (the Judge off, not asked, or
failed) the runs' own vote stands. When the Judge accepted none, the runs'
own choice is returned as `contested` and `agreement.set_aside` is 0. The
judgement comes back with `set_aside`, `overruled` and `instead_of`
filled.

Tests (`tests/agent/test_agreement.py`): each E rule on a hand-built
candidate; `agree` symmetric and tolerant exactly as the scorer is
(reuse its cases); `group` on 4-0, 3-1, 2-2, 2-1-1 and 1-1-1-1 splits;
`vote` on every row of arch7's table in section 22.6; `decide` on no
verdicts, every answer accepted, a minority set aside, the runs' own
choice set aside (B07's shape), a split it settles either way, a split
among the accepted, and none accepted; `rank` on candidates differing in
each criterion alone, in order.

### 3.6 `judge.py` -- the Judge, before the vote

```python
class Ruling(BaseModel):
    answer: str            # the answer's letter
    accepted: bool
    why: str = ""          # one sentence: the mistake in its query, or why it is right

class Rulings(BaseModel):
    rulings: list[Ruling]

LETTERS = "ABCDEFGHIJK"    # one original and at most ten rewordings
ROWS_SHOWN = 5
KNOWLEDGE_CHARS = 6000

def shown(groups: Sequence[Group]) -> list[Group]          # by earliest member: the original's first, never by size
def messages(question, contract, groups, candidates, *, knowledge="", assumptions=()) -> list
def answer_block(letter: str, candidate: Candidate) -> str  # SQL, row count, columns, first five rows
def judge(llm, question: str, contract: AnswerContract, groups: Sequence[Group],
          candidates: Sequence[Candidate], *, knowledge: str = "",
          assumptions: Sequence[str] = ()) -> list[GroupVerdict]
```

One structured call (`Rulings`). The prompt (section 6) carries the
question, what every answer was held to in words (`paraphrase.invariants`),
the assumptions the original's run was told to make, the knowledge the
original's run retrieved, capped at `KNOWLEDGE_CHARS`, and each group's
representative by letter -- never how many runs gave it. A ruling is read
by its letter ("B", "b", "Answer B." alike); a letter that names no answer
shown, and a second ruling on one, are ignored; a group with no ruling is
accepted with the reason "no verdict given". The verdicts come back in
group order. A model failure (`MODEL_ERRORS`, an unparseable answer among
them) raises for the node to record. Routed as `router.model("judge",
*complexity.judge_rung())`, which is heavy, always.

Tests (`tests/agent/test_judge.py`): the answers lettered the original's
first and no count shown; the knowledge and the assumptions shown, the
knowledge capped; an answer block's rows, row count and `NULL`; verdicts
read by letter into group order; an unknown letter, a repeated one and
silence; a failure raised.

### 3.7 `fuse.py` -- fusion

```python
def fuse_columns(representative: Candidate, members: Sequence[Candidate], label_map: LabelMap,
                 dimensions: Mapping[str, str], *, enabled: bool) -> tuple[QueryResult, list[JoinedColumn], list[DeclinedColumn]]
def fuse_claims(representative: Candidate, members: Sequence[Candidate], result: QueryResult,
                *, question: str, assumptions: Sequence[str], cap: int) -> tuple[list[Claim], AuditReport, int, int]
def dissent(chosen: Candidate, losing: Sequence[Group], candidates: Sequence[Candidate]) -> list[Dissent]
def agreement_line(agreement: Agreement, judgement: Judgement | None = None,
                   groups: Sequence[Group] = ()) -> str
```

**The column join rule**, as arch7 section 22.8 states it, in steps. For
each member of the winning group in `rank` order, for each column `c` of
its result not in the representative's: (1) `c` must be a column of a
dimension table (`dimensions: column -> table`, read once from the
catalog -- every column of every table whose name the label map knows as
a dimension -- and kept on the agent beside the label map:
`contract.dimensions(tables, label_map)`, a column two dimensions share
left out); a column that is not one -- a figure under another name -- is
passed over, not declined; (2) that
dimension must be one the representative's rows identify: its key or its
label column is in the representative's columns (`completeness.entity_tables`);
(3) the key column `k` of that dimension must be in both results; (4) the
member's rows must map `k -> c` with no key repeated, and every
representative row's `k` must be found. Each failure of steps 2 to 4 is a
`DeclinedColumn` with its step's reason, unless another member's copy of
the column joins; success appends `c` to the representative's
columns and the values to its rows, in the member's order of columns, and
a `JoinedColumn`. With `enabled=False` the function returns the
representative's result unchanged, no joined and no declined columns, and
the caller sets `decision.columns_fused = False`.

**Claims** (as built). `fuse_claims` starts from
`present.surviving_claims(rep.claims, rep.audit)` and the rows they cite;
then, for each member in rank order and each of its surviving claims,
while the narrative is under `cap`: skip a claim every row of which is
already cited -- a repeat, whichever columns it cites; count as dropped a
claim that cites no cell, or for which `present.check_claim(claim, result,
question=question, assumptions=assumptions)` returns a reason; else
append it and add its rows. Then `present.audit` over the representative's
surviving claims and those added, with the representative's own dropped
claims carried into the report. It returns the representative's claims as
narrated followed by those added, the report, how many were added and how
many dropped. No model call anywhere in this module.

**Dissent** (as built). `completeness.read_query` gains, beside the
tables, select list, order and limit it already reads:

- `filters: set[str]` -- every column reference under any `WHERE` and
  `HAVING`, in a CTE or subquery too, as `table.column` with an alias read
  as its table;
- `rolled_up: dict[str, tuple[str, ...]]` -- for each table a SELECT
  aggregates (a column of it inside `sum`, `avg`, `count`, `min`, `max`
  and the like), the output columns of the nested grouped SELECT that rolls
  it up -- a CTE's or a subquery's, never the outermost, whose grouping is
  the answer's own; a `GROUP BY` position read as the select list's column;
- `joined_on: dict[str, tuple[str, ...]]` -- for each aggregated table
  joined to another, the columns on its side: the equalities of every
  join's `ON`, of the `WHERE`, and of a `USING` between two single names,
  grouped into classes of equal columns by union-find so a join through a
  dimension counts, each side resolved through the CTE or subquery that
  holds it to the tables it reads. Only qualified columns are read; a join
  whose sides cannot be told apart is left out.

`dissent` renders, per losing group: its signature and "run k's query
uses {tables}, filters on {columns}, {how it combines each aggregated
table}; the chosen one ...", each clause omitted when empty, the filters
compared by column name. How a table is combined is said only where the
two queries differ: "rolls {tables} up to {columns} and joins them on
those", "rolls {tables} up to {columns}", or "joins {tables} on {columns},
row by row"; tables combined alike are said together.

**The line.** `agreement_line` renders arch7.1's sentences: "Agreed by 4
of 4 independent runs of the question, each worded differently." / "3 of 4
runs agreed; 1 answered differently." -- each with "; 1 could not answer"
and "; the Judge set aside the answer of 1 other" when they apply / "The
Judge set aside the answer 3 of 4 runs gave -- {why} -- and accepted this
one, which 1 gave." / "The Judge accepted none of the answers -- {why} --
so this is the runs' own choice, which 4 of 4 gave." / "The runs disagreed
and no answer had a majority; this is the largest group's (2 of 4 runs)."
/ "Asked 1 way; one run answered." -- and " The Judge could not be asked."
after the runs' own line when it failed. The count in the sentence is
`agreement.total`, the runs made, not the rewordings written; the runs that
could not answer are counted apart from those the Judge set aside.

Tests (`tests/agent/test_fuse.py`): each step of the join rule declining
on its own; a clean join adds the column and names its run; `enabled=False`
changes nothing and reports so; a member's claim citing a joined column
survives when its value matches and is dropped when it does not; the cap;
a claim about rows already spoken of, and one citing no cell;
`dissent` on two queries differing in a table, in a filter, in both, and
in how they roll up and join the aggregated tables -- B07's pair among
them; every agreement line, the Judge's among them.

### 3.8 `ensemble.py` -- the outer graph

```python
class EnsembleAgent:
    def __init__(self, settings: Settings, *, agent: Nl2SqlAgent | None = None,
                 on_progress: ProgressFn | None = None, tracer: tracing.Tracer | None = None, **agent_kwargs) -> None
    def run(self, question: str, *, principal: str | None = None, on_progress: ProgressFn | None = None) -> EnsembleState
```

One `Nl2SqlAgent` (built here with `agent_kwargs`, or given), whose
router, database pool, retrievers, literal catalog and label map every
candidate shares. `run` mirrors `Nl2SqlAgent.run`: the progress
contextvar, `self.tracer.run(question, principal=principal)` for the root
span, `trace.finish(state)` and `state["trace_id"]`, with the three tags
`nl2sql.agreement` (`"3/4 majority"`), `nl2sql.candidates` and
`nl2sql.judge` (`judged(judgement)`) added in a `RunTrace.finish_ensemble`
beside `finish`.

The graph (`_build_graph`) registers eleven nodes by literal name, as
`graph.py` does and for the same reason -- `tests/docs` reads them as text:

| Node | Label (`STEP_LABELS`) | Span (`TRACE_SPANS`) | Reads |
|---|---|---|---|
| `screen` | `screen` | Supervisor, AGENT | `question` |
| `refuse` | `refused` | Refusal, TASK | `verdict`, `clarification` |
| `paraphrase` | `rewordings` | Paraphraser, AGENT | `question`, `answer_contract` |
| `screen_paraphrase` | `fidelity` | Fidelity Gate, GUARDRAIL | `paraphrases` |
| `plan_wave` | `wave` | Wave Planner, TASK | `paraphrases`, `waves`, `deadline` |
| `answer` | `candidate` | Candidate Runs, CHAIN | `paraphrases`, `waves` |
| `validate` | `agreement` | Agreement, EVALUATOR | `candidates`, `answer_contract` |
| `judge` | `judge` | Judge, EVALUATOR | `question`, `groups` |
| `vote` | `vote` | Vote, EVALUATOR | `groups`, `judgement` |
| `fuse` | `fusion` | Fusion, TASK | `decision`, `agreement` |
| `deliver` | `answer` | Answer, TASK | `decision` |

Edges: `START -> screen`; `screen -> refuse | paraphrase` on `verdict`;
`paraphrase -> screen_paraphrase -> plan_wave -> answer -> validate`;
`validate -> judge | vote` (section 3.8.7); `judge -> vote`; `vote -> fuse
| plan_wave | deliver` (section 3.8.9); `fuse -> deliver`; `refuse -> END`,
`deliver -> END`. The outer graph's `recursion_limit` is its own constant,
40: eleven nodes, two waves, with room.

`graph.py`'s `_traced` becomes a module-level `traced_node(name, fn, *,
labels, spans, progress, candidate=None)` that both graphs use, so the
outer nodes report cost, progress and spans exactly as the inner ones do.

**3.8.1 `screen`.** `supervisor.screen(routed, question, clarify_enabled=...,
tables=agent.db.table_names())`, routed at `complexity.supervisor_rung`;
the contract through `agent._build_contract` (made public as
`build_contract`). Writes `verdict`, `intent`, `clarification`,
`answer_contract`, and keeps the screening's raw fields for candidate 0's
seed. One model call.

**3.8.2 `refuse`.** As `graph.py`'s: `supervisor.refusal(...)` into
`answer` and `narrative`; `error` stays `None`.

**3.8.3 `paraphrase`.** `paraphrase.paraphrase(...)` with
`count=ensemble_max_paraphrases`; on `MODEL_ERRORS`, `paraphrases = []` and
`node_errors["paraphraser"]`. Detail: "7 rewordings" or "failed: ...".

**3.8.4 `screen_paraphrase`.** For each pending rewording, through the
pool (section 3.8.9): `fidelity.check(original, text, kept)`; if it
passes, `supervisor.screen(...)` on the rewording at the light rung
(`complexity.supervisor_rung` is used, so a flagged rewording goes to
standard as a flagged question would), `build_contract` on its reading,
`same_contract` against the anchor's, and `verdict == "proceed"`. Status
and reason are written on the `Paraphrase`; the screening is kept for the
seed. When fewer than `ensemble_paraphrases` are faithful and the retry
has not been made, the node calls `paraphrase.paraphrase(..., failed=...)`
once more for `ensemble_paraphrases - faithful` replacements and screens
those; a flag on the state, `paraphrase_retried`, stops a second retry.
Detail: "3 faithful of 7; F2 x2, F4 x2".

**3.8.5 `plan_wave`.** `waves += 1`; applies `wave_reset()` when `waves >
1`; selects the candidates of this wave: wave 1 is index 0 and the first
`ensemble_paraphrases` faithful rewordings by index; a later wave is every
faithful rewording not yet run. Writes the selection as `wave_plan:
list[int]` (a `wave` field). As built, `plan_wave` is reached a second
time only when the vote has found a faithful rewording left and the
deadline unpassed (section 3.8.9), so a plan is never empty.

**3.8.6 `answer`.** For each index in the plan, through the pool: seed
`new_state(wording, principal=..., screening=...)` and call
`agent.answer(state, on_progress=candidate_progress(k))` inside
`tracing.agent_span(f"Candidate {k}", "AGENT", ...)`; wrap the result in a
`Candidate` with `outcome` from `tracing.outcome(state)`, `wave`, the
timings. Returns `{"candidates": [...]}` -- the reducer appends. The
candidate's progress callback is the job's with `candidate=k`.

**3.8.7 `validate`.** For every candidate: `admissible`, `signature`.
Then `group` over all admissible, and the markers on each candidate
(`group`), written back through the upsert reducer. Writes `candidates`
and `groups`; no vote. Detail: "4 run, 4 admissible, 2 group(s)".

`_route_after_validate`: `judge` when there is a group and
`ensemble_judge_enabled`; otherwise `vote`.

**3.8.8 `judge`.** `judge.judge(...)` over `groups`, with the original's
run's `knowledge` and `assumptions`; writes `judgement =
Judgement(verdicts=..., model=...)`. One model call. On `MODEL_ERRORS`, no
call counted, `judgement = Judgement(error="<type>: <message>")` and
`node_errors["judge"]`. Detail: "group 0 set aside, group 1 accepted".

**3.8.9 `vote`.** `agreement.decide(groups, admissible, total,
judgement)`; writes `agreement`, the completed `judgement`, and `decision`
with the winner's representative as `chosen`, its members as
`fused_from`, and `agreement_line(agreement, judgement, groups)` as
`line`; no winner leaves `chosen` None. Detail: "4 run, 1 voted, 1 agree
(judged); 3 set aside by the Judge".

As built, the vote also decides on a second wave and writes it as
`another_wave`: when the vote is unsettled (`agreement.settled` -- no
majority among the runs that voted, or one run alone; the Judge accepting
none settles it), `waves < ensemble_waves`, a faithful rewording has not
run and the deadline, if set, has not passed. A deadline that stops a wave
is said in `agreement.why`. `_route_after_vote`: `deliver` when nothing
was chosen (`none`); `plan_wave` when `another_wave`; otherwise `fuse`.

**3.8.10 `fuse`.** The winning group is the decision's: its
representative by `rank`, the other members by `agreement.ranked`;
`fuse_columns`; `fuse_claims`; `dissent` over every group the winner is
not; the agreement line is the vote's. Writes `decision`, `sql`, `result`, `chart` (the
representative's), `claims`, `audit`, `assumptions` (the union, ordered by
first appearance), `narrative`. With `agreement.level == "none"` this node
is not reached.

**3.8.11 `deliver`.** `present.render_answer(question, result, claims,
chart, audit, assumptions=..., completeness=rep.completeness,
ensemble=decision)` -- `render_answer` gains the keyword and puts
`decision.line` first and each `Dissent` and each `DeclinedColumn`'s note
among the notes, and names each `JoinedColumn`'s run in a note ("brand_name
from run 2"). For `agreement.level == "none"`: `answer` and `error` are
candidate 0's (its give-up text), and the record is in `candidates`.

**The pool.** `screen_paraphrase` and `answer` run their items through
`concurrent.futures.ThreadPoolExecutor(max_workers=settings.ollama_parallel_calls)`,
each item submitted as `contextvars.copy_context().run(fn, ...)` so the
tracer's active span, the tags and the progress callback travel with it;
with one worker the pool is a loop in all but name, and a test holds that
the order of completion is the order of submission then. Results are
collected in submission order, so `candidates` is appended in the order
the runs finished and `Candidate.index` says which wording each was.

**`Nl2SqlAgent.answer(state, *, on_progress=None) -> AgentState`** (new,
`graph.py`): sets the progress contextvar for the call and invokes the
compiled graph with the given state; opens no root trace, which is `run`'s
job. `run` becomes `answer(new_state(...))` inside `self.tracer.run(...)`.

**`_supervise`** (touched): when `state.get("screened")`, build the
contract from the seeded screening's `entities`, `measure` and `period`
(carried in the seed as `screening_fields`, a `run` field consumed here)
and return `{"answer_contract": contract, _DETAIL: f"screened by the
ensemble; contract: ..."}` with no model call; otherwise as today.

Tests (`tests/agent/test_ensemble.py`, on `conftest`'s fakes with a
`ScriptedLLM` that answers the Paraphraser, the screenings, the
generators and the narrators in turn): the happy path at width 3 to a
unanimous answer with `ensemble.candidates` of four; a refused original
reworded by nobody (the Paraphraser never called); a Paraphraser failure
answering as arch6 with `node_errors`; a rewording that fails F2 never run;
one that fails F4 never run; the Judge asked once a question at the heavy
rung, shown every answer and the original's knowledge; a majority it sets
aside losing to the answer it accepts (`judged`, the line naming why); the
Judge off never asked and the runs voting alone; a Judge that fails
leaving the runs' vote and the line saying so; the Judge accepting none
delivering the runs' own choice as `contested`; a 2-2 split running the
second wave (Phase 3); a deadline set in the past skipping the second
wave; `agreement.level == "none"` delivering candidate 0's
error; the progress stream carrying `candidate` for inner steps and `None`
for outer; one trace with a "Candidate k" span per run (`test_ensemble_tracing.py`,
on the fake MLflow of `test_graph_tracing.py`); with
`OLLAMA_PARALLEL_CALLS=1` the candidates' model calls never overlap, and
with 2 two may (the fake records times).

### 3.9 `grain.py` -- the grain check (Phase 6)

```python
@dataclass(frozen=True)
class Grain:
    table: str
    column: str                    # its column that references the date dimension's key
    period: str                    # "day", or the period's name: "fiscal week", "fiscal month"
    columns: tuple[str, ...]       # the period's columns in the date dimension; () for the day

@dataclass(frozen=True)
class Mismatch:
    finer: Grain
    coarser: Grain
    shape: str                     # "on the date" | "on no date"

def read_grains(database: Database, tables: Sequence[Table], periods: Sequence[tuple[str, ...]]) -> dict[str, Grain]
def mismatches(query: completeness._Query, grains: Mapping[str, Grain]) -> list[Mismatch]
def message(mismatch: Mismatch) -> str
```

**Reading the grains.** For each table of the catalog with a foreign key
to the date dimension's key (`contract.CALENDAR_TABLE`, read from the
constraints `build_label_map` already reads), its distinct values of that
column, and for each period of `GRAIN_PERIODS`, finest first, whether any
period holds two of them -- one query per table
(`Database.dates_per_period(table, column, periods)`, new, on the reader
role, its result a row per period). The grain is the coarsest period
with at most one value in each; a table with two values in some fiscal
week is daily. A table with more than one date column takes the first in
column order (the date it is stamped with). Read with the label map, on
first use, into `ContractResources.grains`, and a database error recorded
in `ContractResources.errors["grains"]` and the check stood down.

**The rule.** `mismatches` reads `query.joined_on` and
`query.rolled_up` (section 3.7). For every two tables in `joined_on` that
both have a grain and whose grains differ:

- **on the date** -- the date column of each among its joined columns, and
  the finer's `rolled_up` not holding every column of the coarser's
  period;
- **on no date** -- neither one's joined columns holding its date column
  or a column of any period of `GRAIN_PERIODS`, and the coarser still by
  period: absent from `rolled_up`, or rolled up to columns that hold its
  date column or a period's column.

Either is a `Mismatch` of that shape. A table rolled up to the period and
joined on it has the period's columns in `joined_on`, not the date, and
passes; a coarser rolled up past its period -- by product alone, say --
passes the second shape, since each row of the finer meets one of it; so
does every join the reading leaves out.

**Where it runs.** `validate.validate(sql, *, allowed_tables=None,
grains=None)` gains the keyword; with grains given, a parsed statement's
mismatches are `Issue(source=STATIC, message=message(m))` after the
statement's own issues. `graph.py`'s `_validate_static` passes
`self._contract_resources().grains` when `grain_check_enabled`, so a
mismatch routes to the Repair Agent as any static issue does and spends
the shared budget. The messages, worded from the grains with nothing
about any one table in code -- on the date: "{coarser.table} is
{coarser.period}ly -- one date in each {period} -- and {finer.table}
{finer.period}ly; joined on the date, each {period}'s row meets one
day's. Roll {finer.table} up to the {period} ({columns}) and join on
those, not on the date."; on no date: "{coarser.table} is
{coarser.period}ly and {finer.table} {finer.period}ly, and they are joined
on no date or period, so each row of {finer.table} meets every {period}'s
row of {coarser.table}. Join them on the {period} as well ({columns}), or
roll {coarser.table} up past the {period} before joining it."

**Under the ensemble.** Nothing: every candidate runs the pipeline's
Static Validator. `fuse.dissent` is unchanged; with the check on, a query
that reaches the vote has passed it.

Tests (`tests/agent/test_grain.py`, new): each grain read from a fake
database's dates -- daily, weekly, monthly, a table with no date column,
a database error standing the check down; B07's wrong query, directly and
through the date dimension, a `Mismatch` on the date with its message; the
costs joined to the sales on product and store alone, the costs read row
by row or rolled up by month, a `Mismatch` on no date with its; B07's
right query, a query joining the two on the period's columns, the costs
rolled up by product alone and joined on product, a join of two daily
tables, and a join the reading cannot read, none; `validate` with
and without grains; `_validate_static` passing them only when on
(`tests/agent/test_graph.py`); the fifteen references through
`mismatches`, none (`tests/benchmarks/`).

## 4. Touched modules

| Module | Change |
|---|---|
| `graph.py` | `traced_node` factored out; `answer(state)`; `_supervise` honours `screened`; `ProgressFn = Callable[..., None]` and every call site passes `candidate` through when it has one; `STEP_LABELS` and `TRACE_SPANS` unchanged |
| `state.py` | `screened`, `screening_fields` (both `run`); `new_state(screening=...)` |
| `complexity.py` | `paraphraser_rung(question) -> (rung, why)` = `supervisor_rung`; `judge_rung() -> (HEAVY, "the Judge: heavy, always")` |
| `router.py` | `TASKS += ("paraphraser", "judge")`; `CATALOG_SCHEMA = 3`; `load_catalog` accepts schema 2 and 3, and for 2 fills `tasks`, `task_budgets` and each model's `prior`/`suited`/`measured` for the two tasks with "unmeasured" so `candidates()` routes them to the anchor; the two pins in `build_table`; `RoutedModel._traced_call` takes the gate's slot |
| `llm.py` | nothing; the gate wraps the call, not the client |
| `contract.py` | `same_contract`; `dimensions(tables, label_map) -> dict[str, str]` beside `build_label_map`, kept in `ContractResources` (built); Phase 6: `ContractResources.grains`, read by `grain.read_grains` in `load_resources` |
| `completeness.py` | `read_query` reads `filters`, `rolled_up` and `joined_on` (built); `check_rules` callable from `agreement.py` as it is from `review` |
| `validate.py` | Phase 6: `validate(..., grains=None)`, the grain check's issues after the statement's own |
| `database.py` | Phase 6: `dates_per_period(table, column, periods)`, the one query a grain is read from |
| `present.py` | `render_answer(..., ensemble: Decision | None = None)`: the line first, the dissent, declined and joined column notes |
| `prompts.py` | `PARAPHRASE_PROMPT`, `JUDGE_PROMPT`, the retry block |
| `tracing.py` | `RunTrace.finish_ensemble(state)` adds the three tags; nothing else -- `agent_span` nests under whatever is active, in a copied context too |
| `config.py` | the eleven settings; `__post_init__` validation; Phase 6: `grain_check_enabled`, `grain_periods` |
| `__main__.py` | the four flags; `[k]` before a candidate's progress line; `--json` emits the ensemble state through `to_jsonable` with `candidates` whole; when `ensemble_enabled` the CLI builds an `EnsembleAgent` |
| `api/models.py` | section 5 |
| `api/translate.py` | section 5 |
| `api/jobs.py` | `ProgressRecord.candidate: int \| None`; `on_progress(step, detail, candidate=None)` |
| `api/app.py` | `default_runner` builds an `EnsembleAgent` when `settings.ensemble_enabled`, else `Nl2SqlAgent`; `AgentHolder` unchanged; `/v1/meta` fills `pipeline.ensemble` and extends `nodes` with the outer graph's |
| `api/routes.py` | `nodes=list(ensemble.STEP_LABELS) + list(STEP_LABELS)` when enabled; `Pipeline.ensemble` |
| `api/feedback.py` | the snapshot gains `ensemble: dict` (the wire `Ensemble`, as JSON) |
| `review/nl2sql_review/store.py` | the staging table gains `ensemble JSONB NOT NULL DEFAULT '{}'::jsonb`, added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on start beside the `CREATE TABLE IF NOT EXISTS`; the review GUI shows the candidates in the submission's detail pane; promotion reads `question` (the original) and `sql` (the chosen), as today |
| `models/build_catalog.py` | `TASKS`, `TASK_BUDGETS` (`paraphraser` 8192, `judge` 16384), `prior_for` (`paraphraser` by the narrator's size rule; `judge` by the reflection's), `schema: 3`; `--resume` carries measurements for unchanged weights |
| `models/calibrate.py` | `--tasks paraphraser judge`; `models/probes/paraphrase.json` (each benchmark question with its reference reading's contract; score = fidelity rate against F1-F5 with the reference model's F4, and the distinct rate) and `models/probes/judge.json` (per question: two or three answers built from the reference SQL and the question's `trap` as written SQL, with their rows run live, shown by letter without counts; score = the fraction of verdicts that match the reference -- the reference's accepted, a trap's set aside); `suited[task]` by the same within-the-reference rule as the five |
| `models/catalog.json` | rebuilt at schema 3 and calibrated for the two tasks; the document about it stays generic |
| `benchmarks/runner.py` | imports the scorer from `nl2sql_agent.compare`; `QuestionResult` gains `agreement`, `candidates`, `agreed`, `rejections`, `candidate_rungs`, `state_bytes`, `rewordings`, `runs`, `judged`, `verdicts` and `without_judge` (Phase 3 adds `wave2` and `columns_fused`); `BenchmarkReport` the metrics of section 8 |
| `benchmarks/run_benchmark.py` | `CONFIGURATIONS["ensemble"]`; `--configuration ensemble` builds an `EnsembleAgent`; `--paraphrase-set` runs `benchmarks/paraphrases.py` |
| `benchmarks/tracking.py` | the new metrics into the MLflow run |
| `arch_diagrams/generate.py` | `build_v7()`: the outer stages drawn around a collapsed v5.6 pipeline; `tests/docs/test_arch_diagrams.py` reads `ensemble.py`'s node names as it reads `graph.py`'s and holds `arch_v7.svg` |
| `docker-compose.yml` | the eleven variables on the agent and API services |

## 5. The wire

In `api/models.py`, every model a `Wire` (extra forbidden):

```python
class EnsembleAgreement(Wire):
    admissible: int; agreed: int; total: int
    level: Literal["unanimous", "majority", "judged", "contested", "single", "none"]
    why: str = ""; set_aside: int = 0

class EnsembleVerdict(Wire):
    group: int; accepted: bool; why: str = ""

class EnsembleJudgement(Wire):
    verdicts: list[EnsembleVerdict] = Field(default_factory=list)
    set_aside: list[int] = Field(default_factory=list)
    overruled: bool = False; instead_of: int | None = None
    model: str = ""; error: str = ""

class JoinedColumn(Wire):
    column: str; from_candidate: int; key: str; table: str

class DeclinedColumn(Wire):
    column: str; from_candidate: int; why: str

class EnsembleDissent(Wire):
    group: int; members: list[int]; signature: str; differs: str

class EnsembleCandidate(Wire):
    index: int; wording: str; origin: Literal["original", "paraphrase"]; wave: int; changed: str = ""
    outcome: Literal["answered", "gave_up", "refused"]
    admissible: bool; reasons: list[str] = Field(default_factory=list)
    sql: str = ""; signature: str = ""; attempts: int = 0; group: int | None = None
    duration_ms: float = 0.0
    trace: list[TraceEntry] = Field(default_factory=list)

class DiscardedRewording(Wire):
    index: int; text: str; changed: str; reason: str

class Ensemble(Wire):
    agreement: EnsembleAgreement
    chosen: int | None
    fused_from: list[int] = Field(default_factory=list)
    columns_fused: bool = True
    joined_columns: list[JoinedColumn] = Field(default_factory=list)
    declined_columns: list[DeclinedColumn] = Field(default_factory=list)
    claims_added: int = 0
    claims_dropped: int = 0
    dissent: list[EnsembleDissent] = Field(default_factory=list)
    judged: EnsembleJudgement | None = None
    candidates: list[EnsembleCandidate] = Field(default_factory=list)
    discarded: list[DiscardedRewording] = Field(default_factory=list)
    parallel_calls: int = 1

class EnsembleSettings(Wire):
    enabled: bool; paraphrases: int; max_paraphrases: int; waves: int
    parallel_calls: int; judge: bool; fuse_columns: bool
```

`Answer` gains `ensemble: Ensemble | None = None`. `ProgressEvent` gains
`candidate: int | None = None` (`0` the original, `1..` a rewording, `None`
an outer node). `Pipeline` gains `ensemble: EnsembleSettings | None = None`.
`Limits` is unchanged.

`translate.answer_from_state` recognises an ensemble state by
`"candidates" in state`. Then, with `chosen` the decision's (or `0` when
`None`) and `cand` that candidate's inner state: `answer`, `narrative`,
`sql`, `result`, `chart`, `claims`, `audit` from the **outer** state (the
fused values); `verdict`, `intent`, `clarification` from the outer state
(the anchor's); `tables`, `literals`, `plan_cost`, `attempts`,
`retrieval_errors` from `cand`; `node_errors` the outer's merged with
`cand`'s; `trace` the outer trace followed by `cand`'s trace, so a client
written for 6.3 reads one coherent run; `ensemble` built by a new
`ensemble_from_state(state, detail=...)`, where without `detail` each
candidate's `reasons` keep their rule names and lose a driver's words, as
`_codes` does for the error maps. A plain `AgentState` translates as
today, `ensemble=None`.

`gui/src/api/types.ts` gains the eight interfaces and the three fields,
mirrored field for field (`tests/gui/test_types.py` holds them to the
pydantic models); `desktop/src/main/java/org/nl2sql/desktop/api/Models.java`
gains the records, components in the pydantic order (`tests/java/` holds
them in order). `agent/API.md` documents each.

## 6. Prompts

Both in `prompts.py`, in the package's style (a `ChatPromptTemplate`, a
block function for an optional part).

**`PARAPHRASE_PROMPT`.** System: arch7.3 section 22.3's text, the four
named invariants (`entities`, `measure`, `period`, `limit`) each stated in
words by `paraphrase.invariants` -- never the contract as the generator is
shown it. Human: `Question: {question}` / `Write {count} rewordings, each
different from the question and from the others, the most different
first. For each, say in a few words what you changed.` / `{failed}`.

**`JUDGE_PROMPT`.** System: "You check answers to a question about a
retail database before they are counted. Each answer is a SQL query and
the first rows it returned. Judge each one on its own against the question
as asked and the knowledge given: you are not told how many runs gave each
answer, and one answer, several, or none may be right. Accept an answer
whose query computes what the question asks for: the measure it names, in
the units it names (a percentage as a percentage); for the entities and the
period it names; with no filter the question does not state -- names given
as examples, with "such as", "like" or "and so on", are examples, not a
list to keep -- and with every join at the grain the knowledge gives, so
that no row is counted twice or matched to the wrong period. Answers that
differ only in column names, column order or rounding are equally right.
Set an answer aside only for a mistake you can point to in its query, and
say what it is in one sentence. Do not write SQL. Give a verdict for every
answer, by its letter." Human: `Question: {question}` / `Every answer was
held to: {held}` / the assumptions block / the knowledge block
(`knowledge_block`) / `Answers:` and per answer `Answer {letter}`, its
SQL, `Rows ({n}[, first 5 shown]):`, the columns and the rows joined by
` | ` / `Give a verdict for each of {letters}.`

## 7. Documents

Written when the phase that makes them true is built; each named with its
test where one exists.

| Document | What changes |
|---|---|
| `README.md` | "What's new in 7.0" (the test holds the heading to `__version__`); the specs row names arch7 as current |
| `agent/README.md` | the title's `(v7, multi-agent)`; "The pipeline" gains the outer graph's diagram and table -- every node of `ensemble.py` named (the docs test reads both files); "Measured" gains the ensemble's numbers; "Configuration" the eleven settings; "Model routing" the two tasks |
| `agent/API.md` | `Answer.ensemble`, `ProgressEvent.candidate`, `Pipeline.ensemble`, with a client snippet reading the agreement |
| `agent/USAGE.md` | the four flags; a `--json` example with `candidates` |
| `docs/agent.md` | a v7 paragraph in the version-by-version account |
| `docs/web_interface.md` | the lanes, the badge, the disclosure |
| `docs/desktop_client.md` | the same |
| `docs/rest_api.md` | the three wire additions, and watching a job rather than blocking |
| `docs/benchmark.md` | the paraphrase set, `ensemble` beside `single`, the seven metrics |
| `docs/model_catalog.md` | seven tasks, schema 3, the two probes, `--tasks paraphraser judge`; still no per-model results |
| `docs/tracing.md` | one trace per question, the candidate spans, the Judge's and the vote's spans, the three tags |
| `docs/architecture_diagrams.md` | `arch_v7.svg` |
| `docs/tests.md` | the counts; `tests/agent/`'s row names the ensemble |
| `docs/feedback.md`, `review/README.md` | the snapshot's `ensemble`; promotion unchanged |
| `docs/stack.md` | nothing, unless a start-script flag is added (none is planned) |
| `docs/SECURITY.md` | the two inputs the ensemble adds (rewordings screened; the Judge bounded), under what the stack protects |
| `docs/CHANGELOG.md`, `docs/CHANGELOG_SIMPLE.md` | the v7_0 entry, every artifact |
| `multi-agent_arch_specs/` | nothing more: arch7.3, this document and its risks by phase stand beside arch7.2's, arch7.1's and arch7's three; departures go in the changelog and the next spec |
| `.coveragerc` | nothing, unless a new directory is made |

## 8. Benchmark

`benchmarks/paraphrases.py` (new): `PARAPHRASES: dict[str, tuple[str, str, str]]`,
three hand-written rewordings per benchmark question, each checked by hand
against the reference contract and by `tests/benchmarks/test_paraphrases.py`
against `fidelity.check` with the original (every rewording must pass
F1-F3 and F5; F4 needs a model and is not a test). `run_benchmark.py
--paraphrase-set` runs the 60 wordings through the configuration given
and reports per question how many wordings matched the reference, and
overall the **stability**.

`CONFIGURATIONS["ensemble"] = {**CONFIGURATIONS["snippets"], "ensemble_enabled": True}`;
there is no `"single"`: `snippets` is one run a question and stays the
default (7.0's departure). The metrics of arch7.3 section 11, in
`BenchmarkReport` and `tracking.metrics`: accuracy (as today), stability
(paraphrase set), agreement rate by level, second-wave rate,
agreement-on-wrong count, what the Judge did by question
(`judge_actions`) and where it overruled the runs, the runs' own choice
scored beside the delivered answer (`judge_effect`: fixed, broke), fidelity
rejections by check, model calls and wall time per question and per
candidate, claims added and dropped, columns joined and declined; and,
from Phase 6, the runs the grain check sent to repair, each scored as the
Judge's overrulings are, and the references it rejects -- which a test
holds at none.

## 9. Tests, as a list

New: `tests/agent/test_grain.py` (Phase 6), `tests/agent/test_ensemble_state.py`, `test_hostgate.py`,
`test_compare.py` (moved cases), `test_paraphrase.py`, `test_fidelity.py`,
`test_agreement.py`, `test_judge.py`, `test_fuse.py`, `test_ensemble.py`,
`test_ensemble_tracing.py`; `tests/api/test_ensemble_wire.py` (an ensemble
state through `answer_from_state`, `ensemble=None` for a plain one, the
progress event's `candidate`, `/v1/meta`'s `pipeline.ensemble` and
`nodes`); `tests/benchmarks/test_paraphrases.py`; `tests/models/` cases
for the two tasks' priors, probes and the schema-2 catalog read leniently.

Touched: `tests/agent/test_state.py` (the new fields), `test_graph.py`
(`screened` skips the Supervisor's call; `answer(state)`),
`test_graph_routing.py`, `test_router.py` (the gate; the two tasks; schema
3), `test_complexity.py` (two rung rules), `test_config.py` (the eleven
settings and the validation), `test_cli.py` (the flags, `[k]`),
`test_present.py` (`render_answer` with a decision), `test_completeness.py`
(`filters`, `rolled_up`, `joined_on`), `test_contract.py` (`same_contract`,
`dimensions`; Phase 6, `grains`), `test_validate.py` (Phase 6, `grains`);
`tests/api/test_jobs.py` (`candidate` on the record), `test_routes*.py`
(`meta`); `tests/review/` (the `ensemble` column and the migration on an
existing table); `tests/docs/test_docs.py` (node names from both graphs;
the README tables; counts), `test_arch_diagrams.py` (`arch_v7.svg`);
`tests/docker/test_compose_config.py` (the variables, both ways);
`tests/gui/`, `tests/java/` (the mirrored types and records; the GUIs' own
suites gain the lanes, the badge and the disclosure); `tests/security/test_wire_models.py`
(the new models forbid extras, by construction).

The whole suite stays at 100% of statements and branches across the four
tiers, measured as `docs/tests.md` says; the shell scripts are untouched,
so their 1,734 commands stay as they are.

## 10. Build order and acceptance

Each phase is a merge candidate on its own: green suite, coverage held,
documents of that phase written, counts moved.

**Phase 0 -- the premise.** `benchmarks/paraphrases.py` and
`--paraphrase-set`; `tests/benchmarks/test_paraphrases.py`. Run against the
live stack at 6.3 as `single`. Acceptance: the 60 wordings run; the
report prints stability and per-question matches. Record the number in
`docs/benchmark.md`. No agent change.

**Phase 1 -- plumbing that changes no answer.** `hostgate.py` and the gate
in `router.py`; `compare.py` and the import in `benchmarks/runner.py`;
`ensemble_state.py`; `graph.py`'s `traced_node`, `answer`, `screened`;
`ensemble.py` with the Paraphraser not called and `plan_wave` planning
candidate 0 alone; `api/app.py` choosing the agent; the wire models,
`translate`, `ProgressEvent.candidate`, `Pipeline.ensemble`; the eleven
settings in `config.py` and compose; the CLI flags. Acceptance: the
benchmark at `ensemble` with `ENSEMBLE_PARAPHRASES` forced to zero is the
same 15/15 with the same SQL as `single`; every API test passes with
`ensemble` present and `candidates` of one; `tests/gui` and `tests/java`
hold the mirrors; with one slot a test shows two threads' calls never
overlap.

**Phase 2 -- rewordings, the gate, the Judge, the vote, selection.**
`paraphrase.py`, `fidelity.py`, `same_contract`, `agreement.py`,
`judge.py`, `screen_paraphrase`, `plan_wave` for real, `validate`, `judge`,
`vote`, `fuse` selecting only (columns and claims off by construction, the
agreement line on), one wave. The two routing tasks with the anchor model
(`TASKS`, rung rules, schema 3 read leniently -- calibration comes in Phase
4). Built in 7.0 as arch7's Phase 2 with no Judge, and the Judge added
before the vote when arch7.1 moved it there (2026-10-08). Acceptance:
`ensemble` holds 15/15 on the benchmark; the paraphrase set's stability
under `ensemble` is at least `single`'s; the report prints agreement by
level, fidelity rejections by check and what the Judge did; where the Judge
overruled the runs, the runs' own choice is scored beside the delivered
answer; `test_ensemble.py`'s happy path, the gate cases and the vote table
pass, the Judge's cases among them.

**Phase 3 -- fusion and the second wave.** Built (2026-10-09).
`fuse_columns` under the join rule with its toggle, `fuse_claims`,
`dissent` with `read_query`'s filters and how the aggregated tables are
rolled up and joined, `render_answer`'s notes; `waves`, the optional
deadline, the Judge again and the recomputed vote. Acceptance: the fusion
tests of section 3.7; a benchmark run reporting columns joined and
declined and claims added and dropped; 15/15 held; the second wave runs
only when the vote among the accepted is unsettled, and the Judge once a
wave, in `test_ensemble.py`. Met: 15/15, one second wave, four claims
added and none of the columns joined (`docs/benchmark.md`).

**Phase 4 -- calibration.** `models/build_catalog.py` and `calibrate.py`
for the two tasks; the probes; the committed catalog rebuilt and
calibrated; `docs/model_catalog.md`. (The Judge itself moved to Phase 2.)
Acceptance: a schema-2 catalog loads and routes the two tasks to the
anchor (test, since Phase 2); the catalog at schema 3 names a suited rung
for each task on the maintainer's host, and the document stays generic.

**Phase 5 -- the surfaces.** The web interface's lanes, badge and
disclosure (`Progress.tsx`, `AnswerView.tsx`, `Candidates.tsx`, their
tests); the desktop's panes; the review GUI's candidates pane and the
staging column; `arch_v7.svg`; every document in section 7; the counts.
Acceptance: `--run-node` and `--run-java` suites green with the new tests;
`tests/docs` green with the moved counts; the acceptance tier green from a
clean start with a question answered through the ensemble.

**Phase 6 -- the grain check.** `grain.py` (section 3.9), `Database.dates_per_period`,
`ContractResources.grains`, `validate(..., grains=...)`,
`GRAIN_CHECK_ENABLED` and `GRAIN_PERIODS` in `config.py` and compose, the
benchmark's grain measures; `agent/README.md`'s Static Validator and
configuration, `docs/benchmark.md`, the changelog, and the release's
note that 7.0.0 changes the single pipeline. Acceptance: every benchmark
reference passes the check (a test, and a release blocker, since 7.0.0
ships it on by default); both shapes of the rule and the passes above, in
`test_grain.py`; the grain questions and the paraphrase set, through all
five configurations, with the check and without, the runs it sent to
repair scored.

**Release.** Version bumped in every declaration the 6.3 changelog counted
(thirty places; `tests/docs/test_versions.py` holds them together);
`setup.sh` pins `v7_0`; the published-tag tests after the push; the
upgrade rehearsed on an isolated instance first (`NL2SQL_INSTANCE`), as
6.3's was.

## 11. Risks and the ways round them

- **Progress and spans in worker threads.** The inner graph reads its
  progress callback from a contextvar and MLflow its active span from the
  OpenTelemetry context, both contextvars; `copy_context().run` in the
  pool carries both. The five retrievers' branches already rely on this
  inside LangGraph; `test_ensemble_tracing.py` holds it for the pool with
  two workers.
- **Two graphs, one wrapper.** `traced_node` must strip `_DETAIL`,
  `_MODEL_CALLS` and `_ROUTE` exactly as `_traced` does today, or a private
  key reaches the outer state and the strict wire fails; the existing
  tracing tests move to the shared function.
- **The state in the state.** `Candidate.state` is a dict of dataclasses;
  `to_jsonable` renders it, and `job.state` for an ensemble run is larger
  than today's -- `API_MAX_JOBS` (200) remembered jobs of eleven candidates
  each is still small, but the job store's memory is worth a line in the
  release's verification.
- **`load_catalog`'s schema check** refuses a mismatched schema today;
  the lenient read of schema 2 must be a deliberate branch with a test,
  and a schema other than 2 or 3 still refused.
- **The reader's connection limit** (60) and `OLLAMA_PARALLEL_CALLS x
  API_MAX_CONCURRENCY`: a settings check at start warns when the product
  exceeds half the limit; nothing is forced.
- **Ollama swaps.** With one slot the ladder's at-most-three resident
  models behave as today; with several slots, two candidates at different
  rungs ask for two models at once, and a host that holds one swaps on
  every call. The document says: raise `OLLAMA_PARALLEL_CALLS` only for a
  host with the memory for its resident models times the slots.
- **Paraphrases as prompt text.** A rewording is model output that becomes
  the generator's `question`; the screening (F4) is the injection screen
  for it, and the fidelity checks run before the screening so an obviously
  altered rewording costs no model call. A test holds that a rewording the
  Supervisor refuses is never run.
- **The review service's migration.** `ADD COLUMN IF NOT EXISTS` on a
  table whose owner is the review service's role (6.3) needs no superuser;
  a test runs it twice on a live store.
- **A Judge on every question.** It is the model arch6 found the wrong
  tool for a validation gate, now before every vote. What holds it: it
  never sees the count; it is told to set an answer aside only for a
  mistake it can name in the query; a letter it does not judge stands;
  and it cannot take the answer away -- with none accepted, the runs' own
  choice is delivered, flagged. What it costs is a heavy call a question
  with the largest prompt of the outer calls (every distinct answer's
  query and rows, and up to `KNOWLEDGE_CHARS` of knowledge), inside the
  16,384-token budget the catalog gives the task. The benchmark scores the
  runs' own choice wherever it overruled them, so its effect is measured
  both ways.
- **A grain check that rejects a right query** (Phase 6). A deterministic
  gate is only as right as its rule: a join the reading misreads, or a
  grain measured from data that happens to hold one date per month for a
  daily table (a table loaded monthly so far), would send a right query to
  repair. What holds it: the check reads only what it can read and passes
  the rest; a grain is measured from all of a table's dates, not a sample;
  a wrong rejection costs a repair attempt, not the answer, while the
  budget lasts; every benchmark reference passes it, held by a test; and
  `GRAIN_CHECK_ENABLED` turns it off. The rule's second shape, a join on
  no date, has an exposure of its own: a query that rolled the coarser
  table up over a filtered period first, and joined on product alone, is
  right; it passes because the coarser is rolled up past its period, and
  a test holds it.
- **Reading the grains** (Phase 6). One query per date-stamped table, on
  first use, over each table's dates: on the sales fact a scan of its date
  column, once a process. Measured in the phase; if it is slow, the
  distinct dates come from an index-only read of the date column, or from
  the date dimension's join -- measured either way, as the owner decided
  (arch7.2's open decision 34), never declared.
