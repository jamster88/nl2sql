#!/usr/bin/env python3
"""Run the benchmark: 15 questions, scored on accuracy then on speed.

    python benchmarks/run_benchmark.py
    python benchmarks/run_benchmark.py --compare          # v1 vs v2 vs v3 vs v5.6
    python benchmarks/run_benchmark.py --only B07 B08
    python benchmarks/run_benchmark.py --json out.json
    python benchmarks/run_benchmark.py --paraphrase-set   # each question four ways: the stability
    python benchmarks/run_benchmark.py --followups        # each question's follow-up: the follow-up accuracy
    python benchmarks/run_benchmark.py --independence     # each question after another: answered as alone?

Accuracy is execution accuracy: the agent's SQL is run and its rows compared
against reference SQL whose answer was verified against the shipped dataset.
Speed is reported per question and per pipeline stage, because "slow" and "slow
in generate_sql" call for different fixes.

`--paraphrase-set` asks each question as the benchmark does and as each of
its three hand-written rewordings does (`benchmarks/paraphrases.py`), scores
all four against the question's one reference, and reports the stability:
the fraction of questions every wording of which came out right -- arch7's
premise, measured.

`--followups` asks, for each question, the second question a person types
after seeing its answer (`benchmarks/followups.py`) -- "and by banner?",
"the same for FY2024" -- and scores it against the reference of the
complete question it means: the follow-up accuracy, arch8's premise.
`--memory N` is how many earlier turns it is asked with -- by default
`CONVERSATION_MEMORY`'s: above 0 each first turn is asked first and handed
on as the conversation's turn, and the report adds the resolution rate and
why the rest were not resolved; at 0 each follow-up is asked alone, as 7.0
asks every question.

`--independence` asks each question alone and then as the second turn of a
conversation -- after the question before it in the list (`previous`, the
default) or after itself (`same`) -- and reports the fraction answered
exactly as alone: which memory must leave at all fifteen.

`--compare` runs the same questions through five configurations, each the
one before it plus one stage -- four of retrieval, which is the measurement
the whole project is for, then the ensemble:

    schema-only   no knowledge, no examples          (v1 behaviour)
    knowledge     knowledge base, no examples        (v2 behaviour)
    multi-shot    knowledge + reranked examples      (v3 behaviour)
    snippets      all of that + SQL snippets         (v5.6, the default: one run a question)
    ensemble      snippets, asked several ways       (arch7, the agent as 7.0 ships)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# The repository, the agent, and the shared package the agent imports since
# 6.2 (`common/`), which the images install and a checkout does not.
for _path in (REPO_ROOT / "common", REPO_ROOT / "agent", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from benchmarks import tracking  # noqa: E402
from benchmarks.followups import FOLLOWUPS, Followup, as_question, keeps  # noqa: E402
from benchmarks.paraphrases import wordings  # noqa: E402
from benchmarks.questions import CATEGORIES, QUESTIONS, BenchmarkQuestion, by_id  # noqa: E402
from benchmarks.runner import (  # noqa: E402
    CORRECT,
    ERROR,
    FAILED,
    WRONG,
    BenchmarkReport,
    QuestionResult,
    StageTimer,
    model_calls_from_trace,
    result_matches,
    routes_from_trace,
    timing_from_trace,
)
from nl2sql_agent import grain  # noqa: E402
from nl2sql_agent.completeness import read_query  # noqa: E402
from nl2sql_agent.config import MAX_CONVERSATION_MEMORY  # noqa: E402
from nl2sql_agent.contract import same_contract  # noqa: E402
from nl2sql_agent.memory import MemoryNote, turn_from_state  # noqa: E402
from nl2sql_agent.ensemble_state import is_ensemble, judged, run_state, whole_trace  # noqa: E402
from nl2sql_agent.state import to_jsonable  # noqa: E402

#: How the report marks each outcome: a miss in capitals, so it stands out.
MARKS = {CORRECT: "ok", WRONG: "WRONG", ERROR: "ERROR", FAILED: "FAILED"}

# The agent's own defaults are compose service names -- `postgres`, `vectordb`,
# `chunkdb`, `host.docker.internal` -- because that is where it normally runs.
# The benchmark runs on the host, where none of those resolve, so each published
# port is used instead. An environment variable still wins, so running the
# benchmark inside the compose network needs no flags.
HOST_DEFAULTS = {
    "database_url": ("DATABASE_URL", "postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail"),
    "vector_db_url": ("VECTOR_DB_URL", "postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors"),
    "context_db_url": ("CONTEXT_DB_URL", "postgresql+psycopg://ragproc:ragproc@localhost:5433/nl2sql_chunks"),
    # The runtime stores' port since 6.3, all four databases on one server.
    "snippet_db_url": (
        "SNIPPET_DB_URL", "postgresql+psycopg://snippets_reader:snippets_reader@localhost:5435/nl2sql_snippets",
    ),
    "embed_base_url": ("EMBED_BASE_URL", "http://localhost:11434"),
    # MLflow's front door, the proxy's published port: HTTPS, and behind
    # sign-in when the stack has it -- MLFLOW_TRACKING_USERNAME and _PASSWORD
    # (a reviewer's) or MLFLOW_TRACKING_TOKEN. Not up, and the benchmark runs
    # untraced after one refused connection; MLFLOW_TRACKING_URI= (empty)
    # turns tracing off even when it is up.
    "mlflow_tracking_uri": ("MLFLOW_TRACKING_URI", "https://localhost:5001"),
}


#: Each database's password as the stack keeps it since 6.3 (V6-38): a file
#: in `secrets/`, which setup.sh generates. Put into the host default when the
#: file is there; without it the default's own password stands.
SECRETS = REPO_ROOT / "secrets"
HOST_PASSWORDS = {
    "database_url": "postgres_reader_password",
    "vector_db_url": "vector_db_password",
    "context_db_url": "context_db_password",
    "snippet_db_url": "snippets_reader_password",
}


def host_default(field: str, url: str) -> str:
    """`url`, with the password the stack generated for it, if it has one."""
    from nl2sql_common.urls import with_password

    name = HOST_PASSWORDS.get(field)
    if name is None:
        return url
    try:
        password = (SECRETS / name).read_text().strip()
    except OSError:
        return url
    return with_password(url, password) if password else url


# The five configurations --compare measures. Each is the one before it plus
# one stage, so a difference between two rows is attributable to that stage
# and nothing else: four of retrieval, then the ensemble (arch7). Every one
# says whether the ensemble is on, so the first four are one run a question
# whatever ENSEMBLE_ENABLED says. The last is the agent as it ships.
CONFIGURATIONS = {
    "schema-only": {
        "rag_enabled": False, "examples_enabled": False, "multi_shot_enabled": False,
        "snippets_enabled": False, "ensemble_enabled": False,
    },
    "knowledge": {
        "rag_enabled": True, "examples_enabled": False, "multi_shot_enabled": False,
        "snippets_enabled": False, "ensemble_enabled": False,
    },
    "multi-shot": {
        "rag_enabled": True, "examples_enabled": True, "multi_shot_enabled": True,
        "snippets_enabled": False, "ensemble_enabled": False,
    },
    "snippets": {
        "rag_enabled": True, "examples_enabled": True, "multi_shot_enabled": True,
        "snippets_enabled": True, "ensemble_enabled": False,
    },
    "ensemble": {
        "rag_enabled": True, "examples_enabled": True, "multi_shot_enabled": True,
        "snippets_enabled": True, "ensemble_enabled": True,
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--only", nargs="+", metavar="ID", help="run just these question ids")
    p.add_argument("--category", choices=CATEGORIES, help="run only this category")
    p.add_argument(
        "--compare", action="store_true",
        help="run every configuration in CONFIGURATIONS and print them side by side",
    )
    p.add_argument("--config", choices=sorted(CONFIGURATIONS), default="snippets")
    sets = p.add_mutually_exclusive_group()
    sets.add_argument(
        "--paraphrase-set", action="store_true",
        help="ask each question its own way and its three rewordings (benchmarks/paraphrases.py), "
             "and report the stability",
    )
    sets.add_argument(
        "--followups", action="store_true",
        help="ask each question's follow-up as a person types it (benchmarks/followups.py), "
             "and report the follow-up accuracy",
    )
    sets.add_argument(
        "--independence", nargs="?", const="previous", choices=("previous", "same"),
        help="ask each question alone, then as the second turn after the question before it (previous) "
             "or after itself (same), and report how many were answered exactly as alone",
    )
    p.add_argument(
        "--memory", type=int, default=None, metavar="N",
        help=f"how many earlier turns a question is asked with, 0 to {MAX_CONVERSATION_MEMORY} "
             "(default: CONVERSATION_MEMORY's); 0 asks each follow-up alone",
    )
    p.add_argument("--json", metavar="PATH", help="also write the full results as JSON")
    p.add_argument("--database-url", help="override the retail database URL")
    p.add_argument("--model", help="override the chat model")
    p.add_argument("--base-url", help="override the chat model host")
    p.add_argument("-v", "--verbose", action="store_true", help="show each pipeline step")
    args = p.parse_args(argv)
    if args.memory is not None and not 0 <= args.memory <= MAX_CONVERSATION_MEMORY:
        p.error(f"--memory {args.memory}: it must be 0 to {MAX_CONVERSATION_MEMORY}")
    return args


def select(args: argparse.Namespace) -> list[BenchmarkQuestion]:
    questions = list(QUESTIONS)
    if args.category:
        questions = [q for q in questions if q.category == args.category]
    if args.only:
        wanted = {i.upper() for i in args.only}
        questions = [q for q in questions if q.id in wanted]
        missing = wanted - {q.id for q in questions}
        if missing:
            raise SystemExit(f"error: no such question(s): {', '.join(sorted(missing))}")
    if not questions:
        raise SystemExit("error: no questions selected")
    return questions


#: The stack's development CA, as launch.sh copies it out of the API's
#: volume: every server's certificate, MLflow's front door's among them, is
#: issued by it (since 6.1). MLflow's client trusts it when told to, and is
#: told to here unless the environment already says what to trust.
CERTIFICATE = Path(__file__).resolve().parent.parent / "nl2sql-ca.crt"


def trust_the_stack(environ=os.environ, certificate: Path = CERTIFICATE) -> None:
    if certificate.is_file() and not environ.get("MLFLOW_TRACKING_SERVER_CERT_PATH"):
        environ["MLFLOW_TRACKING_SERVER_CERT_PATH"] = str(certificate)


def build_settings(args: argparse.Namespace, configuration: str):
    from nl2sql_agent.config import Settings

    trust_the_stack()

    settings = Settings.from_env()
    for field, (variable, default) in HOST_DEFAULTS.items():
        if variable not in os.environ:
            setattr(settings, field, host_default(field, default))
    for field, value in CONFIGURATIONS[configuration].items():
        setattr(settings, field, value)
    if args.database_url:
        settings.database_url = args.database_url
    if args.model:
        settings.ollama_model = args.model
    if args.base_url:
        settings.ollama_base_url = args.base_url
    # Every configuration says whether the ensemble is on, and the name may
    # forbid it: checked here, so one is never run under a name it
    # contradicts (arch8, the risks by phase's S3).
    settings.validate()
    return settings


def reference_rows(database, question: BenchmarkQuestion) -> list[list]:
    result = database.run_select(question.reference_sql)
    return [list(row) for row in result.rows]


def narrative_score(state) -> float | None:
    """The fraction of the narrative's claims the audit traced back to a cell.

    Execution accuracy says whether the SQL was right. This says whether the
    user was told the truth about it, which is a different failure and one
    nothing in the v3 benchmark could see.
    """
    claims = state.get("claims") or []
    if not claims:
        return None
    report = state.get("audit")
    unsupported = len(getattr(report, "unsupported_claims", []) or []) if report else 0
    return round((len(claims) - unsupported) / len(claims), 4)


def run_question(
    agent, database, question: BenchmarkQuestion, timer: StageTimer, *, asked: str | None = None, wording: int = 0,
    turns: tuple = (), kept: dict | None = None,
) -> QuestionResult:
    """Ask one question, score it, and record where the time went.

    `asked` is the words put to the agent, the question's own unless the
    paraphrase set gives a rewording -- `wording` its number -- which is
    scored against the same reference: a rewording asks the same question.
    `turns` are a conversation's earlier answered questions (arch8), and
    the result then says how the question was read against them. `kept`,
    when given, is handed the finished state, for a turn made from it.
    """
    expected = reference_rows(database, question)
    asked = asked or question.question

    timer.start()
    started = time.perf_counter()
    try:
        state = agent.run(asked, turns=turns)
    except Exception as exc:  # noqa: BLE001 - a crash is a benchmark result, not a benchmark failure
        return QuestionResult(
            question_id=question.id, category=question.category, question=asked,
            outcome=FAILED, wall_seconds=time.perf_counter() - started, timing=timer.timing,
            error=str(exc)[:400], expected_row_count=len(expected), wording=wording,
        )
    wall = time.perf_counter() - started
    if kept is not None:
        kept["state"] = state

    # A v4 run times each node itself. Prefer that over the gaps between
    # progress callbacks, which cannot attribute time across Stage 1's
    # parallel branches -- the four retrievers overlap, so the gap after one
    # of them is not its duration.
    # Under the ensemble (arch7) the trace is every run's and the ensemble's
    # own nodes', and what retrieval found and how many attempts it took are
    # the delivered run's; for one run, both are the state itself.
    trace = whole_trace(state)
    timing = timing_from_trace(trace) if trace else timer.timing
    run = run_state(state)

    common = dict(
        question_id=question.id, category=question.category, question=asked, wording=wording,
        wall_seconds=wall, timing=timing, sql=state.get("sql"),
        attempts=run.get("attempts", 0), expected_row_count=len(expected),
        examples=[p["pair_id"] for p in run.get("example_pairs", [])],
        knowledge_chunks=len(run.get("knowledge_chunks", [])),
        model_calls=model_calls_from_trace(trace),
        narrative_score=narrative_score(state),
        routes=routes_from_trace(trace),
        rung=getattr(run.get("complexity"), "rung", None),
        trace_id=state.get("trace_id", ""),
        pipeline=state.get("pipeline") or "",
        conversation=conversation_note(state, turns),
        grain_repairs=grain_repairs(state, expected, question.ordered),
        **ensemble_measures(state),
    )

    if state.get("error"):
        # Either the graph gave up, or the database rejected the final query.
        outcome = ERROR if state.get("sql") else FAILED
        return QuestionResult(outcome=outcome, error=state["error"][:400], **common)

    result = state.get("result")
    if result is None:
        # No error and no rows means the Supervisor stopped the run before any
        # SQL was written -- it judged the question out of scope, an
        # injection, or too ambiguous to answer. That is a wrong answer for a
        # benchmark question by definition, and it has to be scored as one
        # rather than crash the run: a screen that refuses real questions is
        # exactly the regression this number should catch.
        verdict = state.get("verdict", "unknown")
        return QuestionResult(
            outcome=FAILED,
            error=f"refused before generating SQL: verdict={verdict}",
            **common,
        )
    rows = result["rows"] if isinstance(result, dict) else result.rows
    matched = result_matches(expected, rows, ordered=question.ordered)
    outcome = CORRECT if matched else WRONG
    return QuestionResult(
        outcome=outcome, row_count=len(rows),
        without_judge=without_judge(state, expected, question.ordered, outcome), **common
    )


def conversation_note(state: dict, turns: tuple) -> dict:
    """How a question asked with turns was read against them, as the run's
    note says (arch8): empty for a question asked alone."""
    if not turns:
        return {}
    note = state.get("memory") or MemoryNote()
    return {
        "turn": turns[-1].index + 1, "remembered": note.shown, "resolved": note.resolved,
        "read_as": note.read_as, "reason": note.reason,
    }


def run_followup(
    agent, database, followup: Followup, timer: StageTimer, *, memory: int = 0, screen=None,
) -> QuestionResult:
    """Ask a follow-up as a person types it and score it against the
    reference of the complete question it means (arch8).

    With nothing remembered the words typed are asked alone, which is what
    7.0 does with a follow-up (Phase 0). With memory, its first turn -- the
    benchmark question it follows, in its own words -- is asked first and
    handed on as the conversation's one turn, and the result says how the
    follow-up was read: whether a restatement held, as what, or why not;
    how the first turn scored; and, with `screen`, whether the reading
    builds the contract the complete question's own reading does (the
    resolution rate). Filed under the question it follows, with which of
    its filters the delivered SQL kept.
    """
    if not memory:
        result = run_question(agent, database, as_question(followup), timer, asked=followup.fragment)
    else:
        first, kept = {}, {}
        opening = run_question(agent, database, by_id(followup.first), timer, kept=first)
        turn = turn_from_state(first["state"], index=1) if "state" in first else None
        result = run_question(
            agent, database, as_question(followup), timer, asked=followup.fragment,
            turns=(turn,) if turn is not None else (), kept=kept,
        )
        result.conversation = {**result.conversation, "first": opening.outcome}
        if screen is not None and "state" in kept and result.conversation.get("turn"):
            asked_contract = kept["state"].get("answer_contract")
            result.conversation["contract"] = bool(
                asked_contract is not None and same_contract(screen(followup.standalone), asked_contract)
            )
    result.followup_of = followup.first
    result.keeps = keeps(followup, result.sql)
    return result


def rows_of(state: dict | None) -> list | None:
    """The rows a finished run delivered, or None when it delivered none."""
    result = (state or {}).get("result")
    if result is None:
        return None
    return result["rows"] if isinstance(result, dict) else result.rows


def run_independence(
    agent, database, questions, timer: StageTimer, mode: str, report, tracked, configuration, *, memory: int = 1,
) -> None:
    """The independence (arch8 section 11): every question asked alone,
    then each as the second turn of a conversation -- after the question
    before it in the list, the last wrapping to the first (`previous`), or
    after itself (`same`) -- and scored, besides its own reference, on
    whether it came out exactly as it did alone. With `memory` 0 the second
    turn is handed nothing, as the agent would be."""
    alone: dict[str, dict] = {}
    for question in questions:
        kept: dict = {}
        _ask(report, tracked, question, configuration, lambda q=question, k=kept: run_question(
            agent, database, q, timer, kept=k))
        alone[question.id] = kept.get("state")
    for index, question in enumerate(questions):
        first = questions[index - 1] if mode == "previous" else question
        state = alone.get(first.id)
        turn = turn_from_state(state, index=1) if state is not None and memory else None
        kept = {}

        def second(q=question, t=turn, k=kept):
            return run_question(agent, database, q, timer, turns=(t,) if t is not None else (), kept=k)

        result = _ask(report, tracked, question, configuration, second, after=first.id, scored=False)
        before, now = rows_of(alone.get(question.id)), rows_of(kept.get("state"))
        result.after = first.id
        result.independent = (before is None and now is None) or (
            before is not None and now is not None and result_matches(before, now, ordered=question.ordered)
        )
        if tracked is not None:
            tracked.score(result)
        print(f"       -> {MARKS[result.outcome]} in {result.wall_seconds:.1f}s, "
              f"{'as alone' if result.independent else 'NOT as alone'}", flush=True)


def _ask(report, tracked, question, configuration, ask, *, after: str = "", scored: bool = True):
    """One question of a run: its progress line, its tags, its result filed
    and, unless the caller scores it after, scored on its trace."""
    from nl2sql_agent import tracing

    label = f"{question.id}.i" if after else question.id
    print(f"  {label} [{question.category}] {question.question[:64]}...", flush=True)
    with tracing.tagged(tracking.question_tags(question, configuration, after=after)):
        result = ask()
    report.results.append(result)
    if scored:
        if tracked is not None:
            tracked.score(result)
        print(f"       -> {MARKS[result.outcome]} in {result.wall_seconds:.1f}s", flush=True)
    return result


def to_ask(args: argparse.Namespace, question: BenchmarkQuestion) -> list[tuple[str, int, Followup | None]]:
    """What is put to the agent for one question, each with its wording's
    number and the follow-up it is: the question's own words; with the
    paraphrase set, each of its four wordings; with the follow-up set, the
    follow-up a person types after its answer."""
    if args.followups:
        followup = FOLLOWUPS[question.id]
        return [(followup.fragment, 0, followup)]
    texts = wordings(question) if args.paraphrase_set else (question.question,)
    return [(text, wording, None) for wording, text in enumerate(texts)]


def without_judge(state, expected, ordered: bool, outcome: str) -> str:
    """How the answer the runs alone would have chosen scores: the delivered
    one's outcome, unless the Judge overruled them (arch7.1) -- then the run
    it set aside, scored as the delivered one is. Empty for one run."""
    if not is_ensemble(state):
        return ""
    judgement = state.get("judgement")
    if not getattr(judgement, "overruled", False):
        return outcome
    run = next((c for c in state.get("candidates") or [] if c.index == judgement.instead_of), None)
    result = run.state.get("result") if run is not None else None
    if result is None:
        return FAILED
    return CORRECT if result_matches(expected, result.rows, ordered=ordered) else WRONG


def grain_repairs(state, expected, ordered: bool) -> list[dict]:
    """The runs the grain check sent to repair, and how each ended (arch7.3
    section 22.14): scored against the reference, run by run, as the Judge's
    overrulings are -- so what the check bought is counted beside what it
    cost. One run, or under the ensemble every candidate's."""
    if is_ensemble(state):
        runs = [(c.index, c.state) for c in sorted(state.get("candidates") or [], key=lambda c: c.index)]
    else:
        runs = [(0, state)]
    repairs = []
    for index, run in runs:
        sent = sum(
            1 for attempt in run.get("attempt_history") or []
            if any(grain.describes(issue.message) for issue in attempt.issues)
        )
        if not sent:
            continue
        result = run.get("result")
        if result is None or run.get("error"):
            ended = FAILED
        else:
            rows = result["rows"] if isinstance(result, dict) else result.rows
            ended = CORRECT if result_matches(expected, rows, ordered=ordered) else WRONG
        repairs.append({"run": index, "sent": sent, "ended": ended})
    return repairs


def rejected_references(grains, questions: list[BenchmarkQuestion]) -> list[str]:
    """The questions whose reference query the grain check would refuse,
    against these grains. None is the only acceptable answer: a reference is
    right by definition, and a gate that rejects it is wrong."""
    return [q.id for q in questions if grains and grain.mismatches(read_query(q.reference_sql), grains)]


def ensemble_measures(state) -> dict:
    """What the ensemble did with the question (arch7 section 11): how its
    runs agreed, the rewordings its gate discarded by check, the rung each
    run's generator was scored at, the state's size, whether a second wave
    ran and what fusion did. Nothing for one run."""
    if not is_ensemble(state):
        return {}
    agreement = state.get("agreement")
    rejections: dict[str, int] = {}
    for paraphrase in state.get("paraphrases") or []:
        if paraphrase.status == "discarded":
            check = paraphrase.reason.split(" ", 1)[0]
            rejections[check] = rejections.get(check, 0) + 1
    candidates = state.get("candidates") or []
    judgement = state.get("judgement")
    decision = state.get("decision")
    verdicts = {v.group: v.accepted for v in getattr(judgement, "verdicts", None) or []}
    return {
        "wave2": state.get("waves", 0) > 1,
        "columns_fused": getattr(decision, "columns_fused", False),
        "joined": [asdict(column) for column in getattr(decision, "joined_columns", None) or []],
        "declined": [asdict(column) for column in getattr(decision, "declined_columns", None) or []],
        "claims_added": getattr(decision, "claims_added", 0),
        "claims_dropped": getattr(decision, "claims_dropped", 0),
        "judged": judged(judgement),
        "verdicts": [
            {"group": v.group, "accepted": v.accepted, "why": v.why} for v in getattr(judgement, "verdicts", None) or []
        ],
        "agreement": getattr(agreement, "level", ""),
        "agreed": getattr(agreement, "agreed", 0),
        "candidates": len(candidates),
        "rejections": rejections,
        "candidate_rungs": [
            getattr(c.state.get("complexity"), "rung", "") for c in sorted(candidates, key=lambda c: c.index)
        ],
        "state_bytes": len(json.dumps(to_jsonable(state), default=str).encode()),
        "rewordings": [
            {"index": p.index, "text": p.text, "changed": p.changed, "status": p.status, "reason": p.reason}
            for p in state.get("paraphrases") or []
        ],
        "runs": [
            {"index": c.index, "wording": c.wording, "outcome": c.outcome, "admissible": c.admissible,
             "reasons": list(c.reasons), "group": c.group, "signature": c.signature,
             "accepted": verdicts.get(c.group),
             "chosen": c.index == getattr(state.get("decision"), "chosen", None), "sql": c.state.get("sql", "")}
            for c in sorted(candidates, key=lambda c: c.index)
        ],
    }


def run_configuration(args, configuration: str, questions: list[BenchmarkQuestion]) -> BenchmarkReport:
    from nl2sql_agent import ensemble, graph, tracing
    from nl2sql_agent.database import Database
    from nl2sql_agent.llm import LlmUnavailableError

    settings = build_settings(args, configuration)
    database = Database(
        settings.database_url, db_schema=settings.db_schema,
        statement_timeout_ms=settings.statement_timeout_ms, max_rows=settings.max_rows,
    )

    def show(step: str, detail: str) -> None:
        if args.verbose:
            print(f"      [{step}] {detail[:90]}", file=sys.stderr)

    timer = StageTimer(show)
    try:
        agent = pipeline = graph.Nl2SqlAgent(settings, on_progress=timer)
        if settings.ensemble_enabled:
            agent = ensemble.EnsembleAgent(settings, agent=agent, on_progress=timer)
    except LlmUnavailableError as exc:
        raise SystemExit(f"error: {exc}")

    # The depth a question is asked with: the flag's, else the setting's.
    memory = settings.conversation_memory if args.memory is None else args.memory
    report = BenchmarkReport(
        label=configuration, grain_checked=settings.grain_check_enabled, memory=memory,
        pipeline=settings.pipeline_version,
    )
    # Before anything is asked: the check held to the references, against
    # the grains this database measures to.
    report.rejected_references = rejected_references(pipeline.grains(), questions)
    tracked = tracking.open_run(
        agent, configuration, questions, paraphrase_set=args.paraphrase_set, followups=args.followups,
        memory=memory, independence=args.independence or "",
    )

    def screen(text: str):
        """The contract the complete question's own reading builds: one
        Supervisor call, paid by the benchmark for the resolution rate."""
        return pipeline.screen(text)[0]["answer_contract"]

    complete = False
    try:
        if args.independence:
            run_independence(
                agent, database, questions, timer, args.independence, report, tracked, configuration, memory=memory,
            )
            questions = []
        for question in questions:
            for text, wording, followup in to_ask(args, question):
                label = f"{question.id}.f" if followup else f"{question.id}.{wording}" if wording else question.id
                print(f"  {label} [{question.category}] {text[:64]}...", flush=True)
                tags = tracking.question_tags(question, configuration, wording=wording, followup=bool(followup))
                with tracing.tagged(tags):
                    if followup is None:
                        result = run_question(agent, database, question, timer, asked=text, wording=wording)
                    else:
                        result = run_followup(agent, database, followup, timer, memory=memory, screen=screen)
                report.results.append(result)
                if tracked is not None:
                    tracked.score(result)
                print(f"       -> {MARKS[result.outcome]} in {result.wall_seconds:.1f}s", flush=True)
        complete = True
    finally:
        if tracked is not None:
            tracked.finish(report, as_json([report]), complete=complete)
    return report


def print_report(report: BenchmarkReport) -> None:
    named = f"  (pipeline {report.pipeline})" if report.pipeline else ""
    print(f"\n{'=' * 72}\n{report.label or 'benchmark'}{named}\n{'=' * 72}")

    print("\nACCURACY")
    print(f"  execution accuracy   {report.correct}/{report.total}  ({100 * report.accuracy:.1f}%)")
    print(f"  produced runnable SQL {report.answered}/{report.total}")
    print("\n  by category")
    for category, (correct, total) in sorted(report.by_category().items()):
        bar = "#" * correct + "." * (total - correct)
        print(f"    {category:<10} {correct}/{total}  {bar}")

    scored = [r.narrative_score for r in report.results if r.narrative_score is not None]
    if scored:
        # Execution accuracy says whether the SQL was right. This says whether
        # the user was told the truth about it, which nothing in the v3
        # harness could see.
        traced = sum(scored) / len(scored)
        print(f"  narrative traced      {100 * traced:.1f}%  ({len(scored)} of {report.total} narrated)")

    missed = [r for r in report.results if not r.correct]
    if missed:
        print("\n  not correct")
        for result in missed:
            detail = result.error or f"{result.row_count} rows, expected {result.expected_row_count}"
            print(f"    {result.label} {result.outcome:<7} {detail[:60]}")

    if report.paraphrased:
        print_stability(report)

    if report.followed_up:
        print_followups(report)

    if report.second_turns():
        print_independence(report)

    if report.ensembled:
        print_ensemble(report)

    if report.grain_checked:
        print_grain(report)

    print("\nSPEED")
    asked = "wordings" if report.paraphrased else "follow-ups" if report.followed_up else "questions"
    print(f"  total                {report.total_seconds:.1f}s for {report.total} {asked}")
    print(f"  median per question  {report.median_seconds:.1f}s")
    print("\n  slowest")
    for result in report.slowest(3):
        print(f"    {result.label}  {result.wall_seconds:6.1f}s  {result.question[:52]}")

    calls: dict[str, int] = {}
    for result in report.results:
        for node, n in (result.model_calls or {}).items():
            calls[node] = calls.get(node, 0) + n
    if calls:
        # The architecture's economic claim, made checkable: three calls on
        # the happy path, and a repair classifier that keeps most retries
        # from costing a fourth.
        total_calls = sum(calls.values())
        per_question = total_calls / report.total if report.total else 0
        breakdown = ", ".join(f"{node} {n}" for node, n in sorted(calls.items()))
        print(f"\n  model calls          {total_calls} ({per_question:.1f} per question)")
        print(f"    {breakdown}")

    by_model = report.by_model()
    if by_model:
        # arch5.2: which model answered each agent at each rung, how often the
        # questions it touched came out right, and how fast it answered.
        rungs = ", ".join(f"{rung} {n}" for rung, n in sorted(report.rungs().items()))
        print(f"\nROUTING\n  generator's task scored   {rungs or 'not scored'}")
        print(f"\n  {'agent':<13} {'rung':<9} {'model':<26} {'calls':>5} {'right':>7} {'P50':>8}")
        for row in by_model:
            hops = f"  ({row['hops']} hop(s))" if row["hops"] else ""
            print(
                f"  {row['node']:<13} {row['rung']:<9} {row['model']:<26} {row['calls']:>5} "
                f"{row['correct']:>3}/{row['questions']:<3} {row['p50_seconds']:>7.2f}s{hops}"
            )

    totals = report.stage_totals()
    if totals:
        overall = sum(totals.values()) or 1.0
        print("\n  where the time went")
        for stage, seconds in totals.items():
            share = 100 * seconds / overall
            print(f"    {stage:<20} {seconds:7.1f}s  {share:5.1f}%  {'#' * int(share / 3)}")


def print_ensemble(report: BenchmarkReport) -> None:
    """What the ensemble did (arch7 section 11): how the runs agreed, how often
    they agreed on a wrong answer, which checks discarded rewordings, how
    often a question's runs were scored at different rungs, how large a
    question's state grew, what the Judge did, how often a second wave ran
    and what fusion added."""
    print("\nENSEMBLE")
    levels = report.agreement_levels()
    print(f"  agreement            {', '.join(f'{level} {n}' for level, n in sorted(levels.items()))}")
    print(f"  agreed, but wrong    {report.agreed_on_wrong}")
    runs = sorted(r.candidates for r in report.results if r.candidates)
    print(f"  runs per question    {runs[len(runs) // 2]} (median), {runs[0]} to {runs[-1]}")
    rejections = report.fidelity_rejections()
    shown = ", ".join(f"{check} {n}" for check, n in rejections.items()) or "none"
    print(f"  rewordings discarded {sum(rejections.values())}: {shown}")
    print(f"  runs at mixed rungs  {report.rung_spread} of {report.total} question(s)")
    sizes = report.state_sizes()
    if sizes:
        print(f"  state, as JSON       {sizes[len(sizes) // 2] / 1024:.1f} KB median, {sizes[-1] / 1024:.1f} KB largest")
    actions = report.judge_actions()
    if actions:
        print(f"  the Judge            {', '.join(f'{action} {n}' for action, n in actions.items())}")
        effect = report.judge_effect()
        print(f"  overruled the runs   {effect['overruled']}: wrong -> right {effect['fixed']}, "
              f"right -> wrong {effect['broke']}")
    print(f"  second wave          {report.second_waves} of {report.total} question(s)")
    fusion = report.fusion()
    print(f"  fusion               columns joined {fusion['columns joined']}, declined {fusion['columns declined']}; "
          f"claims added {fusion['claims added']}, dropped {fusion['claims dropped']}")


def print_grain(report: BenchmarkReport) -> None:
    """What the grain check did (arch7.3 section 22.14): the references it
    would refuse, which must be none, and the runs it sent to repair with
    how each ended."""
    print("\nGRAIN CHECK")
    rejected = ", ".join(report.rejected_references)
    print(f"  references refused   {rejected + '  <-- a right query, refused' if rejected else 'none'}")
    repairs = report.grain_repairs()
    print(f"  runs sent to repair  {repairs['runs']}: ended right {repairs['right']}, wrong {repairs['wrong']}, "
          f"failed {repairs['failed']}")
    for result in report.results:
        for repair in result.grain_repairs:
            times = "once" if repair["sent"] == 1 else f"{repair['sent']} times"
            print(f"    {result.label} run {repair['run']}  refused {times}, ended {MARKS[repair['ended']]}")


def print_stability(report: BenchmarkReport) -> None:
    """The paraphrase set's verdict: how many questions were right however
    they were worded, and per question, which wordings were."""
    questions = report.by_question()
    own = [r for r in report.results if not r.wording]
    reworded = [r for r in report.results if r.wording]
    print("\nSTABILITY  (the paraphrase set: each question in its own words and three others)")
    print(f"  stable questions     {report.stable}/{len(questions)}  ({100 * report.stability:.1f}%)  "
          "every wording right")
    print(f"  own wordings         {sum(r.correct for r in own)}/{len(own)}")
    print(f"  rewordings           {sum(r.correct for r in reworded)}/{len(reworded)}")
    print("\n  per question                    wordings 0 1 2 3")
    for question_id, results in questions.items():
        right = sum(r.correct for r in results)
        marks = " ".join(MARKS[r.outcome] for r in results)
        print(f"    {question_id} {results[0].category:<10} {right}/{len(results)}  {marks}")


def print_followups(report: BenchmarkReport) -> None:
    """The follow-up set's verdict (arch8 section 11): how many follow-ups
    came out right with what was remembered, how often the delivered SQL
    kept the filters the first turn set, and per follow-up, its outcome,
    its words and the filters kept."""
    followups = report.followups()
    read = report.read_in_context()
    print("\nFOLLOW-UPS  (the follow-up set: the question a person asks after each answer)")
    if report.memory:
        print(f"  turns remembered     {report.memory}  (--memory; each follow-up asked after its first turn, "
              "which is remembered)")
    else:
        print(f"  turns remembered     {report.memory}  (--memory; at 0 each follow-up is asked alone, "
              "as 7.0 asks every question)")
    print(f"  follow-up accuracy   {report.followup_correct}/{len(followups)}  ({100 * report.followup_accuracy:.1f}%)")
    kept, total = report.filters_kept()
    if total:
        print(f"  filters kept         {kept}/{total}  the first turn's, in the delivered SQL")
    if read:
        first = sum(1 for result in read if result.conversation.get("first") == CORRECT)
        held = sum(1 for result in read if result.conversation.get("resolved"))
        print(f"  first turns right    {first}/{len(read)}")
        print(f"  resolution rate      {round(report.resolution_rate * len(read))}/{len(read)}  restated, held, "
              f"and read as the complete question is ({held} held)")
        rejections = report.rejections_by_check()
        if rejections:
            print("  not resolved         " + ", ".join(f"{reason} {count}" for reason, count in rejections.items()))
    print("\n  per follow-up")
    for result in followups:
        filters = f"  kept {sum(result.keeps.values())}/{len(result.keeps)}" if result.keeps else ""
        print(f"    {result.label:<6} {result.category:<10} {MARKS[result.outcome]:<6} {result.question[:44]}{filters}")
        if result.conversation:
            reading = result.conversation
            said = f"read as: {reading['read_as']}" if reading.get("resolved") else reading.get("reason") or "-"
            print(f"           {said[:100]}")


def print_independence(report: BenchmarkReport) -> None:
    """The independence (arch8 section 11): each question asked as a second
    turn against itself asked alone -- which memory must leave as it was --
    and what each cost in model calls, which memory must not move."""
    seconds = report.second_turns()
    alone = {r.question_id: r for r in report.results if not r.after}
    same = sum(1 for result in seconds if result.independent)
    restated = [result for result in seconds if result.conversation.get("resolved")]
    print("\nINDEPENDENCE  (each question asked again as a second turn)")
    print(f"  answered as alone    {same}/{len(seconds)}  ({100 * (report.independence or 0):.1f}%)")
    print(f"  restated             {len(restated)}/{len(seconds)}  though each stands alone")
    calls_alone = sum(sum(alone[r.question_id].model_calls.values()) for r in seconds if r.question_id in alone)
    calls_after = sum(sum(r.model_calls.values()) for r in seconds)
    print(f"  model calls          {calls_after} as second turns, {calls_alone} alone")
    for result in seconds:
        if not result.independent or result.conversation.get("resolved"):
            reading = result.conversation.get("read_as") or result.conversation.get("reason") or ""
            mark = "as alone" if result.independent else "NOT as alone"
            print(f"    {result.label:<6} after {result.after:<4} {mark:<12} {reading[:70]}")


def print_comparison(reports: list[BenchmarkReport], refused: dict[str, str] | None = None) -> None:
    """The configurations side by side, under the pipeline's name, and any
    the name refused below them, each with the reason."""
    # The stability column only where a question was asked more than one way.
    paraphrased = any(report.paraphrased for report in reports)
    print(f"\n{'=' * 72}\ncomparison\n{'=' * 72}\n")
    for pipeline in sorted({report.pipeline for report in reports} - {""}):
        print(f"  pipeline {pipeline}\n")
    print(f"  {'configuration':<14} {'accuracy':>12} {'answered':>10} {'total':>9} {'median':>9}"
          + (f" {'stable':>8}" if paraphrased else ""))
    print(f"  {'-' * 14} {'-' * 12} {'-' * 10} {'-' * 9} {'-' * 9}" + (f" {'-' * 8}" if paraphrased else ""))
    for report in reports:
        print(
            f"  {report.label:<14} {report.correct:>4}/{report.total} "
            f"({100 * report.accuracy:>4.0f}%) {report.answered:>10} "
            f"{report.total_seconds:>8.1f}s {report.median_seconds:>8.1f}s"
            + (f" {report.stable:>5}/{len(report.by_question()):<2}" if paraphrased else "")
        )

    categories = sorted({c for r in reports for c in r.by_category()})
    print(f"\n  by category\n  {'configuration':<14}" + "".join(f"{c:>12}" for c in categories))
    for report in reports:
        cells = report.by_category()
        row = "".join(
            f"{cells.get(c, (0, 0))[0]:>8}/{cells.get(c, (0, 0))[1]:<4}" for c in categories
        )
        print(f"  {report.label:<14}{row}")
    for configuration, reason in (refused or {}).items():
        print(f"\n  {configuration} was not run: {reason}")


def as_json(reports: list[BenchmarkReport]) -> dict:
    return {
        "configurations": [
            {
                "label": report.label,
                "pipeline": report.pipeline,
                "accuracy": report.accuracy,
                "correct": report.correct,
                "answered": report.answered,
                "total": report.total,
                "total_seconds": report.total_seconds,
                "median_seconds": report.median_seconds,
                "stage_totals": report.stage_totals(),
                "by_category": {k: list(v) for k, v in report.by_category().items()},
                "by_model": report.by_model(),
                "rungs": report.rungs(),
                # The paraphrase set's: None for a run of each question's own words.
                "stability": {
                    "fraction": report.stability,
                    "stable": report.stable,
                    "questions": len(report.by_question()),
                    "by_question": {
                        question_id: [r.outcome for r in results]
                        for question_id, results in report.by_question().items()
                    },
                } if report.paraphrased else None,
                # The follow-up set's: None for a run without it.
                "followups": {
                    "memory": report.memory,
                    "accuracy": report.followup_accuracy,
                    "correct": report.followup_correct,
                    "total": len(report.followups()),
                    "filters_kept": report.filters_kept()[0],
                    "filters": report.filters_kept()[1],
                    "consistency": report.consistency,
                    "resolution_rate": report.resolution_rate,
                    "not_resolved": report.rejections_by_check(),
                } if report.followed_up else None,
                # The independence's: None for a run without it.
                "independence": {
                    "fraction": report.independence,
                    "as_alone": sum(1 for r in report.second_turns() if r.independent),
                    "total": len(report.second_turns()),
                    "restated": sum(1 for r in report.second_turns() if r.conversation.get("resolved")),
                } if report.second_turns() else None,
                # The ensemble's: None for one run a question.
                "ensemble": {
                    "agreement": report.agreement_levels(),
                    "agreed_on_wrong": report.agreed_on_wrong,
                    "fidelity_rejections": report.fidelity_rejections(),
                    "rung_spread": report.rung_spread,
                    "state_bytes": report.state_sizes(),
                    "judge": {**report.judge_actions(), **report.judge_effect()},
                    "second_waves": report.second_waves,
                    "fusion": report.fusion(),
                } if report.ensembled else None,
                # The grain check's: None with it off.
                "grain": {
                    "rejected_references": report.rejected_references,
                    "repairs": report.grain_repairs(),
                } if report.grain_checked else None,
                "results": [
                    {
                        "id": r.question_id,
                        "wording": r.wording,
                        "category": r.category,
                        "question": r.question,
                        "outcome": r.outcome,
                        "wall_seconds": round(r.wall_seconds, 3),
                        "stages": {k: round(v, 3) for k, v in r.timing.as_dict().items()},
                        "attempts": r.attempts,
                        "rows": r.row_count,
                        "expected_rows": r.expected_row_count,
                        "examples": r.examples,
                        "knowledge_chunks": r.knowledge_chunks,
                        "model_calls": r.model_calls,
                        "narrative_score": r.narrative_score,
                        "rung": r.rung,
                        "routes": r.routes,
                        "trace_id": r.trace_id,
                        "pipeline": r.pipeline,
                        "sql": r.sql,
                        "error": r.error,
                        "agreement": r.agreement,
                        "candidates": r.candidates,
                        "agreed": r.agreed,
                        "rejections": r.rejections,
                        "candidate_rungs": r.candidate_rungs,
                        "state_bytes": r.state_bytes,
                        "rewordings": r.rewordings,
                        "runs": r.runs,
                        "judged": r.judged,
                        "verdicts": r.verdicts,
                        "without_judge": r.without_judge,
                        "wave2": r.wave2,
                        "columns_fused": r.columns_fused,
                        "joined": r.joined,
                        "declined": r.declined,
                        "claims_added": r.claims_added,
                        "claims_dropped": r.claims_dropped,
                        "grain_repairs": r.grain_repairs,
                        "followup_of": r.followup_of,
                        "keeps": r.keeps,
                        "conversation": r.conversation,
                        "after": r.after,
                        "independent": r.independent,
                    }
                    for r in report.results
                ],
            }
            for report in reports
        ]
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    questions = select(args)
    configurations = list(CONFIGURATIONS) if args.compare else [args.config]
    # Every configuration checked before anything is asked (arch8, S3 and
    # S6): one the pipeline's name contradicts -- the ensemble under v6_3_1
    # -- is refused with the reason, and a comparison runs the rest.
    refused = {}
    for configuration in configurations:
        try:
            build_settings(args, configuration)
        except ValueError as exc:
            refused[configuration] = str(exc)
    if refused and not args.compare:
        raise SystemExit(f"error: --config {args.config} is refused: {refused[args.config]}")

    reports = []
    for configuration in (c for c in configurations if c not in refused):
        print(f"\n--- {configuration} ({len(questions)} questions) ---")
        reports.append(run_configuration(args, configuration, questions))

    for report in reports:
        print_report(report)
    if len(reports) > 1 or refused:
        print_comparison(reports, refused)

    if args.json:
        Path(args.json).write_text(json.dumps(as_json(reports), indent=2, default=str))
        print(f"\nwrote {args.json}")

    # Non-zero when anything was not correct, so CI can gate on it -- and
    # when the grain check would refuse a reference: a gate that rejects a
    # right query is wrong whatever the answers scored.
    return 0 if all(r.accuracy == 1.0 and not r.rejected_references for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
