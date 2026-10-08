"""Asking it several ways: the outer graph around the pipeline (arch7 section 22).

The design: the question is screened once, reworded three to ten ways, each
rewording held to the original's answer contract by a fidelity gate, the
pipeline run once per wording, the results checked against their own
question and against each other, and the largest agreeing group's answer
delivered with the record of every run. Temperature is zero everywhere, so a
different wording is the only independent second draw the pipeline has.

What is built so far is the plumbing, with nothing riding on it yet: the
outer graph screens the original -- the anchor every rewording will be held
to -- and runs it as candidate 0, seeded with that screening so its
Supervisor call is not paid twice, then delivers it as it ran. So the answer
is arch6's, to the character; what is added is the record around it, and
the seams the later stages ride on:

    screen ─┬─ refuse                      the Supervisor, once, on the original
            └─ plan_wave ─► answer ─► deliver
                           one run per wording, through the pool, each in a
                           "Candidate k" span, its progress carrying k

* **One pipeline, shared.** Every candidate runs on the same `Nl2SqlAgent`
  -- its router, database pool, retrievers, literal catalog and label map
  built once, as the API's jobs already share them -- through
  `Nl2SqlAgent.answer`, which runs a seeded state and opens no trace.
* **The pool.** The candidates of a wave run through a pool of
  `OLLAMA_PARALLEL_CALLS` workers, each submitted in a copy of the
  submitter's context so the active span, the trace's tags and the progress
  callback travel with it (`run_pool`). With the default of one, the pool is
  a loop in all but name -- but the loop body runs on the worker's thread,
  which is why the copy is made either way.
* **The host gate.** Every model call any candidate makes takes a slot of
  the process's gate first (`hostgate.py`), so the width of the pool is
  never more calls on the host than the deployer said it serves.

`ENSEMBLE_ENABLED=false` is arch6 to the answer: `build_agent` hands back the
pipeline itself, and nothing here runs.
"""

from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Sequence, TypeVar

from langgraph.graph import END, START, StateGraph

from . import contract as answer_contract, supervisor, tracing
from .config import Settings
from .ensemble_state import (
    ORIGINAL,
    PARAPHRASE,
    Agreement,
    Candidate,
    Decision,
    EnsembleState,
    new_ensemble_state,
)
from .graph import _DETAIL, Nl2SqlAgent, ProgressFn, progress_to, reporter, traced_node
from .graph import step_label as pipeline_label
from .state import AuditReport, new_state

#: The outer graph's own bound on supersteps: five nodes, one pass. Its own
#: constant, not the pipeline's: each candidate's run is bounded by that.
RECURSION_LIMIT = 40

#: A short human label per outer node, for the CLI's stderr lines and the
#: REST event stream, as `graph.STEP_LABELS` is for the pipeline's.
STEP_LABELS = {
    "screen": "screen",
    "refuse": "refused",
    "plan_wave": "wave",
    "answer": "candidate",
    "deliver": "answer",
}

#: Each outer node as a span of the question's trace: the agent's name as
#: arch7 section 22.1 gives it, the kind of span, and the state it reads.
#: The candidate runs read the plan; what they wrote is in their own spans,
#: so the node's span records a summary of them (`_runs`), not the runs.
TRACE_SPANS = {
    "screen": ("Supervisor", "AGENT", ("question",)),
    "refuse": ("Refusal", "TASK", ("verdict", "clarification")),
    "plan_wave": ("Wave Planner", "TASK", ("waves",)),
    "answer": ("Candidate Runs", "CHAIN", ("wave_plan", "waves")),
    "deliver": ("Answer", "TASK", ("wave_plan",)),
}

Item = TypeVar("Item")
Result = TypeVar("Result")


def step_label(step: str, candidate: int | None = None) -> str:
    """A progress line's label: an outer node's, or a candidate's step's."""
    if candidate is None and step in STEP_LABELS:
        return STEP_LABELS[step]
    return pipeline_label(step)


def run_pool(fn: Callable[[Item], Result], items: Sequence[Item], width: int) -> list[Result]:
    """Each item through `fn` on up to `width` worker threads; the results in
    the order the items were given.

    Each item is submitted as `copy_context().run(fn, item)`, a copy per
    item -- one context cannot be entered by two threads at once -- so what
    the submitter's context holds reaches the worker: the progress callback,
    the tracer whose trace is open, the trace's tags, and the span MLflow
    nests the next one under (arch7 section 22.4, R1 of the risks by phase).
    A run that raises raises here, once every other has finished.
    """
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(width, len(items)), thread_name_prefix="nl2sql-candidate") as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in items]
        return [future.result() for future in futures]


def build_agent(settings: Settings, **kwargs: Any) -> "Nl2SqlAgent | EnsembleAgent":
    """The agent the settings ask for: the ensemble, or arch6's pipeline alone."""
    if settings.ensemble_enabled:
        return EnsembleAgent(settings, **kwargs)
    return Nl2SqlAgent(settings, **kwargs)


class EnsembleAgent:
    """The question asked several ways. Construct once, call `run` per question.

    Answers to the same surface as `Nl2SqlAgent` -- `run`, `settings`, `db`,
    `router`, `tracer`, `reload` -- so whatever holds one can hold the other.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        agent: Nl2SqlAgent | None = None,
        on_progress: ProgressFn | None = None,
        tracer: tracing.Tracer | None = None,
        **agent_kwargs: Any,
    ) -> None:
        self.settings = settings
        self.agent = agent or Nl2SqlAgent(settings, tracer=tracer, **agent_kwargs)
        self.tracer = tracer or self.agent.tracer
        self._on_progress = on_progress or _ignore
        self._graph = self._build_graph()

    @property
    def db(self) -> Any:
        return self.agent.db

    @property
    def router(self) -> Any:
        return self.agent.router

    def reload(self) -> list[str]:
        return self.agent.reload()

    # --- running ------------------------------------------------------------

    def run(
        self,
        question: str,
        *,
        principal: str | None = None,
        on_progress: ProgressFn | None = None,
    ) -> EnsembleState:
        """Answer one question, asked every way this build asks it.

        One trace for the question, as for one run: the outer nodes are its
        spans, and each candidate's run is a span of its own beneath them.
        `on_progress` overrides the constructor's callback for this question
        only; a candidate's steps reach it with `candidate=k`.
        """
        seconds = self.settings.ensemble_deadline_seconds
        deadline = time.monotonic() + seconds if seconds else 0.0
        with progress_to(on_progress), self.tracer.run(question, principal=principal) as trace:
            state = self._graph.invoke(
                new_ensemble_state(
                    question,
                    principal=principal,
                    deadline=deadline,
                    parallel_calls=self.settings.ollama_parallel_calls,
                ),
                config={"recursion_limit": RECURSION_LIMIT},
            )
            if trace is not None:
                trace.finish(state)
                trace.finish_ensemble(state)
                state["trace_id"] = trace.trace_id
        return state

    def _traced(self, name: str, fn: Callable[[EnsembleState], dict], **kwargs: Any) -> Callable:
        return traced_node(name, fn, spans=TRACE_SPANS, progress=self._report, **kwargs)

    def _report(self) -> ProgressFn:
        return reporter(self._on_progress)

    def _build_graph(self):
        graph = StateGraph(EnsembleState)
        # One literal call per node, as graph.py registers the pipeline's:
        # the names are the interface the ensemble is documented by.
        graph.add_node("screen", self._traced("screen", self._screen))
        graph.add_node("refuse", self._traced("refuse", self._refuse))
        graph.add_node("plan_wave", self._traced("plan_wave", self._plan_wave))
        graph.add_node("answer", self._traced("answer", self._answer, outputs=_runs))
        graph.add_node("deliver", self._traced("deliver", self._deliver))

        graph.add_edge(START, "screen")
        # A refused or ambiguous question is answered as arch6 answers it,
        # and nothing is run.
        graph.add_conditional_edges("screen", self._route_after_screen, ["refuse", "plan_wave"])
        graph.add_edge("plan_wave", "answer")
        graph.add_edge("answer", "deliver")
        graph.add_edge("refuse", END)
        graph.add_edge("deliver", END)
        return graph.compile()

    # --- stage 0: the anchor ------------------------------------------------

    def _screen(self, state: EnsembleState) -> dict:
        """The Supervisor on the original: the anchor (arch7 section 22.3).

        The verdict, the intent and the answer contract every later wording
        is held to, read once from the words the person wrote. The reading
        itself is kept to seed the original's run, which then makes no call
        of its own. With the Supervisor off nothing is screened anywhere,
        and the original's run builds its contract from its own wording as
        arch6 does.
        """
        question = state["question"]
        if not self.settings.supervisor_enabled:
            contract = self.agent.build_contract(question)
            return {"answer_contract": contract, _DETAIL: f"disabled; contract: {answer_contract.describe(contract)}"}
        update, screening = self.agent.screen(question)
        update["screening"] = screening
        return update

    def _route_after_screen(self, state: EnsembleState) -> str:
        return "refuse" if state.get("verdict", "proceed") != "proceed" else "plan_wave"

    def _refuse(self, state: EnsembleState) -> dict:
        verdict = state.get("verdict", "proceed")
        answer = supervisor.refusal(
            verdict,
            state.get("clarification"),
            domain=supervisor.describe_scope(self.db.table_names()),
        )
        return {
            "answer": answer,
            "narrative": answer,
            "agreement": Agreement(level="none", why=f"the question was screened out ({verdict}); nothing ran"),
            _DETAIL: verdict,
        }

    def _plan_wave(self, state: EnsembleState) -> dict:
        """Which wordings run now. The Paraphraser is not built yet, so there
        is no rewording to plan: a wave is the original alone."""
        waves = state.get("waves", 0) + 1
        return {"waves": waves, "wave_plan": [0], _DETAIL: f"wave {waves}: the original"}

    # --- the candidate runs -------------------------------------------------

    def _answer(self, state: EnsembleState) -> dict:
        """One run of the pipeline per wording in the plan (arch7 section 22.4).

        Each is seeded with the screening made for its wording and run in a
        "Candidate k" span of the question's trace, its progress reported
        with `candidate=k`. E1 -- did it answer -- is read off each run as
        it is recorded; the rest of the checks are the validator's.
        """
        report = self._report()
        wave = state.get("waves", 1)
        started = time.perf_counter()

        def run(index: int) -> Candidate:
            wording, origin, screening = _wording(state, index)
            began = time.perf_counter()
            with tracing.agent_span(f"Candidate {index}", "AGENT", lambda: {"question": wording}) as span:
                finished = self.agent.answer(
                    new_state(wording, principal=state.get("principal"), screening=screening),
                    on_progress=_for_candidate(report, index),
                )
                tracing.finish_run_span(span, finished)
            ended = tracing.outcome(finished)
            return Candidate(
                index=index,
                wording=wording,
                origin=origin,
                wave=wave,
                state=finished,
                outcome=ended,
                admissible=ended == tracing.ANSWERED,
                reasons=[] if ended == tracing.ANSWERED else [f"E1 answered: {ended}"],
                started_ms=round((began - started) * 1000, 2),
                ms=round((time.perf_counter() - began) * 1000, 2),
            )

        candidates = run_pool(run, list(state.get("wave_plan") or []), self.settings.ollama_parallel_calls)
        return {
            "candidates": candidates,
            _DETAIL: f"{len(candidates)} run(s): " + ", ".join(f"[{c.index}] {c.outcome}" for c in candidates),
        }

    # --- stage 6 ------------------------------------------------------------

    def _deliver(self, state: EnsembleState) -> dict:
        """The original's run, delivered as it ran.

        With one wording there is nothing to vote on: an answered run is the
        `single` agreement and is delivered, and one that gave up is `none`,
        delivered as arch6 delivers a give-up -- its error and its attempts
        -- with its record beneath. Either way the answer is the run's own.
        """
        original = min(state.get("candidates") or [], key=lambda candidate: candidate.index)
        run = original.state
        total = len(state.get("candidates") or [])
        fuse_columns = self.settings.ensemble_fuse_columns
        if original.admissible:
            agreement = Agreement(admissible=1, agreed=1, total=total, level="single", why="asked 1 way")
            decision = Decision(chosen=original.index, fused_from=[original.index], columns_fused=fuse_columns)
        else:
            agreement = Agreement(total=total, level="none", why=f"the original's run {original.outcome}")
            decision = Decision(columns_fused=fuse_columns)
        return {
            "agreement": agreement,
            "decision": decision,
            "answer": run.get("answer") or "",
            "narrative": run.get("narrative") or "",
            "sql": run.get("sql") or "",
            "result": run.get("result"),
            "chart": run.get("chart"),
            "claims": list(run.get("claims") or []),
            "audit": run.get("audit") or AuditReport(),
            "assumptions": list(run.get("assumptions") or []),
            "error": run.get("error"),
            "node_errors": dict(run.get("node_errors") or {}),
            _DETAIL: f"{agreement.level}: the original's run",
        }


def _ignore(step: str, detail: str, candidate: int | None = None) -> None:
    """The progress callback of an agent nobody is watching."""


def _for_candidate(report: ProgressFn, index: int) -> ProgressFn:
    """The question's callback, with the candidate's index on every step."""

    def candidate_progress(step: str, detail: str) -> None:
        report(step, detail, candidate=index)

    return candidate_progress


def _wording(state: EnsembleState, index: int) -> tuple[str, str, dict[str, Any] | None]:
    """A candidate's words, whose they are, and the screening made for them.

    The screening is the outer graph's own -- the anchor's for the original,
    the fidelity gate's for a rewording -- never anything a client sent: a
    screened run trusts it and makes no Supervisor call (R7's seam).
    """
    if index == 0:
        return state["question"], ORIGINAL, state.get("screening")
    paraphrase = next(p for p in state.get("paraphrases") or [] if p.index == index)
    return paraphrase.text, PARAPHRASE, paraphrase.screening


def _runs(update: dict) -> dict:
    """What the candidate runs' span records: each run in a line, not whole --
    every run's own spans are beneath it."""
    return {
        "candidates": [
            {"index": c.index, "wording": c.wording, "outcome": c.outcome, "sql": c.state.get("sql", ""), "ms": c.ms}
            for c in update.get("candidates", [])
        ]
    }
