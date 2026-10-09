"""Asking it several ways: the outer graph around the pipeline (arch7.1 section 22).

Temperature is zero everywhere, so a different wording is the only
independent second draw the pipeline has. The question is screened once,
reworded, each rewording held to the original's answer contract by a
fidelity gate, the pipeline run once per wording, the results checked
against their own question and grouped by agreement, every distinct answer
judged, the largest group of accepted answers chosen -- a second wave run
first when the vote leaves the question unsettled -- and what the group's
runs found fused into the chosen one's answer, with the record of every run:

    screen ─┬─ refuse                          the Supervisor, once, on the original
            └─ paraphrase                      up to 10 rewordings, most different first
               └─ screen_paraphrase            F1-F3 and F5 in code, then F4: the
                  │                            Supervisor's reading of each
                  └─ plan_wave <───────────┐  the original + the first 3 faithful;
                     └─ answer             │  then every faithful one not yet run
                        └─ validate        │  E1-E5, agreement, the groups
                           └─ judge        │  every group's answer, accepted or set aside
                              └─ vote ─┬───┘  the accepted runs vote; unsettled, a second wave
                                       ├─ fuse ─┐  columns, claims, dissent onto the chosen
                                       └────────┴─ deliver   its answer, its agreement first

* **The anchor is screened first, and only the anchor.** An injection or an
  ambiguity stops the question before anything is reworded.
* **No rewording runs unscreened.** Each passes the checks in code first --
  so one that visibly changed the question costs no model call -- then the
  Supervisor reads it, and its reading must build the original's contract
  and proceed. A run is seeded with the screening made on its exact words.
* **One pipeline, shared.** Every candidate runs on the same `Nl2SqlAgent`
  -- its router, database pool, retrievers, literal catalog and label map
  built once, as the API's jobs already share them -- through
  `Nl2SqlAgent.answer`, which runs a seeded state and opens no trace.
* **The pool.** The screenings and the candidates run through a pool of
  `OLLAMA_PARALLEL_CALLS` workers, each submitted in a copy of the
  submitter's context so the active span, the trace's tags and the progress
  callback travel with it (`run_pool`). With the default of one, the pool is
  a loop in all but name -- but the loop body runs on the worker's thread,
  which is why the copy is made either way.
* **The host gate.** Every model call takes a slot of the process's gate
  first (`hostgate.py`), so the width of the pool is never more calls on the
  host than the deployer said it serves.

* **The Judge reads before the vote counts** (arch7.1). One heavy call a
  wave sees each distinct answer -- not how many runs gave it -- and
  sets aside those it can name a mistake in; only the runs whose answer it
  accepted vote. Off, or failed, the runs vote alone; when it accepts none,
  their own choice is delivered as `contested`, with its objection.

* **A second wave when the vote does not settle it.** No majority among the
  runs that voted, or one run standing alone, and the faithful rewordings
  not yet run are run, once (`ENSEMBLE_WAVES`, at most two in effect, since
  the second runs every one left), unless `ENSEMBLE_DEADLINE_SECONDS` has
  passed; then the Judge reads every group again and the vote is recounted
  over every run of both waves.
* **Fusion, in code** (`fuse.py`). The chosen rows widened by the
  attributes the group's other runs carried, joined on the entity's key
  only when every row matches once (`ENSEMBLE_FUSE_COLUMNS`); their claims
  the delivered rows bear out, up to `ENSEMBLE_MAX_CLAIMS`; the assumptions
  every run made, once; and each losing answer named with how its query
  differs. No SQL is ever fused: the delivered SQL is the chosen run's.

`ENSEMBLE_ENABLED=false` is arch6 to the answer: `build_agent` hands back the
pipeline itself, and nothing here runs.
"""

from __future__ import annotations

import contextvars
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, Callable, Sequence, TypeVar

from langgraph.graph import END, START, StateGraph

from . import agreement as votes
from . import complexity, fidelity, fuse, judge as judges, paraphrase as paraphraser, present, supervisor, tracing
from . import contract as answer_contract
from .config import Settings
from .ensemble_state import (
    ORIGINAL,
    PARAPHRASE,
    Agreement,
    Candidate,
    Decision,
    EnsembleState,
    Judgement,
    Paraphrase,
    new_ensemble_state,
    wave_reset,
)
from .fuse import agreement_line
from .graph import _DETAIL, _MODEL_CALLS, _ROUTE, Nl2SqlAgent, ProgressFn, progress_to, reporter, traced_node
from .graph import step_label as pipeline_label
from .state import AuditReport, QueryResult, new_state
from nl2sql_common.errors import MODEL_ERRORS

#: The outer graph's own bound on supersteps: eleven nodes, two waves -- fifteen
#: steps at the most -- with room. Its own constant, not the pipeline's: each
#: candidate's run is bounded by that.
RECURSION_LIMIT = 40

FAITHFUL = "faithful"
DISCARDED = "discarded"
PENDING = "pending"

#: A short human label per outer node, for the CLI's stderr lines and the
#: REST event stream, as `graph.STEP_LABELS` is for the pipeline's.
STEP_LABELS = {
    "screen": "screen",
    "refuse": "refused",
    "paraphrase": "rewordings",
    "screen_paraphrase": "fidelity",
    "plan_wave": "wave",
    "answer": "candidate",
    "validate": "agreement",
    "judge": "judge",
    "vote": "vote",
    "fuse": "fusion",
    "deliver": "answer",
}

#: Each outer node as a span of the question's trace: the agent's name as
#: arch7 section 22.1 gives it, the kind of span, and the state it reads.
#: The candidate runs and the vote read whole runs, whose own spans are
#: beneath; their spans record a line per run (`_runs`), not the runs.
TRACE_SPANS = {
    "screen": ("Supervisor", "AGENT", ("question",)),
    "refuse": ("Refusal", "TASK", ("verdict", "clarification")),
    "paraphrase": ("Paraphraser", "AGENT", ("question", "answer_contract")),
    "screen_paraphrase": ("Fidelity Gate", "GUARDRAIL", ("paraphrases",)),
    "plan_wave": ("Wave Planner", "TASK", ("paraphrases", "waves")),
    "answer": ("Candidate Runs", "CHAIN", ("wave_plan", "waves")),
    "validate": ("Agreement", "EVALUATOR", ("answer_contract",)),
    "judge": ("Judge", "EVALUATOR", ("question", "groups")),
    "vote": ("Vote", "EVALUATOR", ("groups", "judgement")),
    "fuse": ("Fusion", "TASK", ("decision", "agreement")),
    "deliver": ("Answer", "TASK", ("decision",)),
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
        """Answer one question, asked several ways.

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
        graph.add_node("paraphrase", self._traced("paraphrase", self._paraphrase))
        graph.add_node("screen_paraphrase", self._traced("screen_paraphrase", self._screen_paraphrase))
        graph.add_node("plan_wave", self._traced("plan_wave", self._plan_wave))
        graph.add_node("answer", self._traced("answer", self._answer, outputs=_runs))
        graph.add_node("validate", self._traced("validate", self._validate, outputs=_runs))
        graph.add_node("judge", self._traced("judge", self._judge))
        graph.add_node("vote", self._traced("vote", self._vote))
        graph.add_node("fuse", self._traced("fuse", self._fuse))
        graph.add_node("deliver", self._traced("deliver", self._deliver))

        graph.add_edge(START, "screen")
        # A refused or ambiguous question is answered as arch6 answers it,
        # and nothing is reworded.
        graph.add_conditional_edges("screen", self._route_after_screen, ["refuse", "paraphrase"])
        graph.add_edge("paraphrase", "screen_paraphrase")
        graph.add_edge("screen_paraphrase", "plan_wave")
        graph.add_edge("plan_wave", "answer")
        graph.add_edge("answer", "validate")
        graph.add_conditional_edges("validate", self._route_after_validate, ["judge", "vote"])
        graph.add_edge("judge", "vote")
        graph.add_conditional_edges("vote", self._route_after_vote, ["fuse", "plan_wave", "deliver"])
        graph.add_edge("fuse", "deliver")
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
        return "refuse" if state.get("verdict", "proceed") != "proceed" else "paraphrase"

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

    # --- stage 0: the rewordings ----------------------------------------------

    def _paraphrase(self, state: EnsembleState) -> dict:
        """The Paraphraser: up to `ENSEMBLE_MAX_PARAPHRASES` rewordings in one
        call. A failure costs the rewordings and nothing else -- the original
        runs alone, and the answer says it was asked one way.

        With the Supervisor off nothing is reworded: no rewording runs that
        the Supervisor has not read (R7), and with it off nobody reads one.
        """
        if not self.settings.supervisor_enabled:
            return {"paraphrases": [], _DETAIL: "skipped: the Supervisor is off, and no rewording runs unread"}
        question = state["question"]
        routed = self.router.model("paraphraser", *complexity.paraphraser_rung(question))
        update: dict[str, Any] = {}
        try:
            written = paraphraser.paraphrase(
                routed,
                question,
                state["answer_contract"],
                count=self.settings.ensemble_max_paraphrases,
            )
        except MODEL_ERRORS as exc:
            update.update({"paraphrases": [], "node_errors": {"paraphraser": str(exc)}})
            update[_DETAIL] = f"failed: {type(exc).__name__}"
        else:
            update.update({"paraphrases": written, _MODEL_CALLS: 1, _DETAIL: f"{len(written)} rewording(s)"})
        # The call was made, whether or not it answered: the trace names its model.
        update[_ROUTE] = routed.record()
        return update

    def _screen_paraphrase(self, state: EnsembleState) -> dict:
        """The fidelity gate (arch7 section 22.3): which rewordings are the same question.

        Each pending rewording is held to the checks in code first -- F1
        numbers, F2 literals, F3 polarity, F5 distinct from the original and
        from those before it that passed -- so one that visibly changed the
        question costs no model call. The rest are read by the Supervisor,
        through the pool, at the rung its rule gives the rewording, and kept
        only when that reading proceeds and builds the original's contract
        (F4). The contract is built in the original's shape -- one number or
        a list, ranked, how many rows, which F1 and F3 have held the words to
        already -- so it is the reading that is compared: the entities, the
        measure, the period. Fewer faithful than `ENSEMBLE_PARAPHRASES`, the
        Paraphraser is asked once more for replacements, told which failed and
        why.
        """
        if not self.settings.supervisor_enabled:
            return {_DETAIL: "skipped: the Supervisor is off"}
        question = state["question"]
        contract = state["answer_contract"]
        paraphrases = list(state.get("paraphrases") or [])
        calls = [0]
        routes: list[dict[str, Any]] = []

        def gate(batch: list[Paraphrase]) -> list[Paraphrase]:
            kept = [p.text for p in paraphrases if p.status == FAITHFUL]
            passed, judged = [], {}
            for item in batch:
                reason = fidelity.check(question, item.text, kept + [p.text for p in passed])
                if reason:
                    judged[item.index] = replace(item, status=DISCARDED, reason=reason)
                else:
                    passed.append(item)
            readings = run_pool(lambda item: self.agent.screen(item.text, shaped_by=question), passed,
                                self.settings.ollama_parallel_calls)
            for item, read in zip(passed, readings):
                update, screening = read
                calls[0] += int(update.get(_MODEL_CALLS, 0))
                routes.append(update[_ROUTE])
                reason = _f4(update, contract)
                judged[item.index] = replace(
                    item,
                    status=DISCARDED if reason else FAITHFUL,
                    reason=reason or "",
                    screening=None if reason else screening,
                )
            return [judged[item.index] for item in batch]

        pending = [p for p in paraphrases if p.status == PENDING]
        screened = {p.index: p for p in gate(pending)}
        paraphrases = [screened.get(p.index, p) for p in paraphrases]

        retried = bool(state.get("paraphrase_retried"))
        update: dict[str, Any] = {}
        faithful = sum(1 for p in paraphrases if p.status == FAITHFUL)
        wanted = self.settings.ensemble_paraphrases
        if faithful < wanted and not retried and "paraphraser" not in (state.get("node_errors") or {}):
            retried = True
            failed = [(p.text, p.reason) for p in paraphrases if p.status == DISCARDED]
            routed = self.router.model("paraphraser", *complexity.paraphraser_rung(question))
            try:
                replacements = paraphraser.paraphrase(
                    routed, question, contract, count=wanted - faithful, failed=failed,
                    start=max((p.index for p in paraphrases), default=0) + 1,
                    written=[p.text for p in paraphrases],
                )
            except MODEL_ERRORS as exc:
                update["node_errors"] = {"paraphraser": str(exc)}
                replacements = []
            else:
                calls[0] += 1
            routes.append(routed.record())
            paraphrases += gate(replacements)

        faithful = sum(1 for p in paraphrases if p.status == FAITHFUL)
        tally = Counter(p.reason.split(" ", 1)[0] for p in paraphrases if p.status == DISCARDED)
        detail = f"{faithful} faithful of {len(paraphrases)}"
        if tally:
            detail += "; " + ", ".join(f"{check} x{count}" for check, count in sorted(tally.items()))
        if retried and not state.get("paraphrase_retried"):
            detail += "; asked once more"
        update.update({"paraphrases": paraphrases, "paraphrase_retried": retried, _MODEL_CALLS: calls[0], _DETAIL: detail})
        if routes:
            update[_ROUTE] = {**routes[0], "hops": [hop for route in routes for hop in route.get("hops", [])]}
        return update

    def _plan_wave(self, state: EnsembleState) -> dict:
        """Which wordings run now. The first wave: the original and the first
        `ENSEMBLE_PARAPHRASES` faithful rewordings, in the order written. A
        second: every faithful rewording not yet run -- never one the gate
        discarded -- with the vote before it emptied, since it describes
        runs that are no longer all the runs."""
        waves = state.get("waves", 0) + 1
        faithful = [p.index for p in state.get("paraphrases") or [] if p.status == FAITHFUL]
        if waves == 1:
            plan = [0, *faithful[: self.settings.ensemble_paraphrases]]
            detail = "wave 1: the original" + (
                f" and rewording(s) {', '.join(map(str, plan[1:]))}" if plan[1:] else " alone"
            )
            return {"waves": waves, "wave_plan": plan, _DETAIL: detail}
        plan = _unrun(state)
        return {**wave_reset(), "waves": waves, "wave_plan": plan,
                _DETAIL: f"wave {waves}: rewording(s) {', '.join(map(str, plan))}"}

    # --- the candidate runs -------------------------------------------------

    def _answer(self, state: EnsembleState) -> dict:
        """One run of the pipeline per wording in the plan (arch7 section 22.4).

        Each is seeded with the screening made for its wording and the
        anchor contract, and run in a "Candidate k" span of the question's
        trace, its progress reported with `candidate=k`.
        """
        report = self._report()
        wave = state.get("waves", 1)
        started = time.perf_counter()

        def run(index: int) -> Candidate:
            wording, origin, screening = _wording(state, index)
            if screening is not None:
                # Every run is held to the anchor contract, the one read from
                # the question as asked (arch7 section 22.2).
                screening = {**screening, "contract": state["answer_contract"]}
            began = time.perf_counter()
            with tracing.agent_span(f"Candidate {index}", "AGENT", lambda: {"question": wording}) as span:
                finished = self.agent.answer(
                    new_state(wording, principal=state.get("principal"), screening=screening),
                    on_progress=_for_candidate(report, index),
                )
                tracing.finish_run_span(span, finished)
            return Candidate(
                index=index,
                wording=wording,
                origin=origin,
                wave=wave,
                state=finished,
                outcome=tracing.outcome(finished),
                started_ms=round((began - started) * 1000, 2),
                ms=round((time.perf_counter() - began) * 1000, 2),
            )

        candidates = run_pool(run, list(state.get("wave_plan") or []), self.settings.ollama_parallel_calls)
        return {
            "candidates": candidates,
            _DETAIL: f"{len(candidates)} run(s): " + ", ".join(f"[{c.index}] {c.outcome}" for c in candidates),
        }

    # --- stage 6 ------------------------------------------------------------

    def _validate(self, state: EnsembleState) -> dict:
        """Tier 1, then the groups (arch7.1 sections 22.5 and 22.6): which
        runs are answers to their own question, and which agree. In code; no
        model call. The vote waits for the Judge."""
        question = state["question"]
        contract = state["answer_contract"]
        label_map = self.agent.label_map()
        faithful = {p.index for p in state.get("paraphrases") or [] if p.status == FAITHFUL}
        candidates = []
        for candidate in state.get("candidates") or []:
            reasons = votes.admissible(
                candidate, contract, label_map, question=question,
                faithful=candidate.origin == ORIGINAL or candidate.index in faithful,
            )
            result = candidate.state.get("result")
            signature = votes.signature(result, label_map) if result is not None and not reasons else ""
            candidates.append(replace(candidate, admissible=not reasons, reasons=reasons, signature=signature))
        groups = votes.group(candidates, contract, label_map)
        member_of = {member: g.index for g in groups for member in g.members}
        candidates = [replace(c, group=member_of.get(c.index)) for c in candidates]
        admissible = sum(1 for c in candidates if c.admissible)
        detail = f"{len(candidates)} run, {admissible} admissible, {len(groups)} group(s)"
        return {"candidates": candidates, "groups": groups, _DETAIL: detail}

    def _route_after_validate(self, state: EnsembleState) -> str:
        """The Judge, when there is an answer to judge and it is on."""
        return "judge" if state.get("groups") and self.settings.ensemble_judge_enabled else "vote"

    def _judge(self, state: EnsembleState) -> dict:
        """The Judge (arch7.1 section 22.7): one call, a verdict on every
        group's answer, before the vote counts them. A failure costs the
        verdicts and nothing else -- the runs vote alone, and the answer says
        the Judge could not be asked."""
        routed = self.router.model("judge", *complexity.judge_rung())
        original = next((c for c in state.get("candidates") or [] if c.index == 0), None)
        run = original.state if original is not None else {}
        update: dict[str, Any] = {}
        try:
            verdicts = judges.judge(
                routed,
                state["question"],
                state["answer_contract"],
                state["groups"],
                state["candidates"],
                knowledge=run.get("knowledge") or "",
                assumptions=run.get("assumptions") or [],
            )
        except MODEL_ERRORS as exc:
            update["judgement"] = Judgement(error=f"{type(exc).__name__}: {exc}")
            update["node_errors"] = {"judge": str(exc)}
            update[_DETAIL] = f"failed: {type(exc).__name__}"
        else:
            update["judgement"] = Judgement(verdicts=verdicts, model=routed.record()["model"])
            update[_MODEL_CALLS] = 1
            update[_DETAIL] = ", ".join(
                f"group {v.group} {'accepted' if v.accepted else 'set aside'}" for v in verdicts
            )
        update[_ROUTE] = routed.record()
        return update

    def _vote(self, state: EnsembleState) -> dict:
        """Tier 3 (arch7.1 section 22.7): the runs whose answer the Judge
        accepted vote, and the winning group's representative is chosen
        (section 22.8). In code; no model call.

        A vote that does not settle the question -- no majority among the
        runs that voted, or one run alone -- sends it to a second wave when
        one is left: fewer waves run than `ENSEMBLE_WAVES`, a faithful
        rewording not yet run, and the deadline, if one is set, not passed.
        Decided here, once, so the deadline is read once; a deadline that
        stopped a wave is said in the agreement's `why`.
        """
        candidates = state.get("candidates") or []
        groups = state.get("groups") or []
        agreement, winner, judgement = votes.decide(
            groups, sum(1 for c in candidates if c.admissible), len(candidates), state.get("judgement")
        )
        another = False
        if winner is not None and not votes.settled(agreement, judgement):
            if state.get("waves", 1) < self.settings.ensemble_waves and _unrun(state):
                deadline = state.get("deadline") or 0.0
                if deadline and time.monotonic() >= deadline:
                    agreement = replace(agreement, why=f"{agreement.why}; the deadline passed before a second wave")
                else:
                    another = True
        decision = Decision(columns_fused=self.settings.ensemble_fuse_columns)
        if winner is not None:
            decision = replace(
                decision,
                chosen=winner.representative,
                fused_from=list(winner.members),
                line=agreement_line(agreement, judgement, groups),
            )
        detail = (
            f"{agreement.total} run, {agreement.admissible} voted, {agreement.agreed} agree ({agreement.level})"
            + (f"; {agreement.set_aside} set aside by the Judge" if agreement.set_aside else "")
            + ("; unsettled: a second wave" if another else "")
        )
        return {
            "agreement": agreement, "judgement": judgement, "decision": decision, "another_wave": another,
            _DETAIL: detail,
        }

    def _route_after_vote(self, state: EnsembleState) -> str:
        """Nothing to deliver but the original's give-up, a second wave, or
        the chosen run to fuse."""
        if state["decision"].chosen is None:
            return "deliver"
        return "plan_wave" if state.get("another_wave") else "fuse"

    def _fuse(self, state: EnsembleState) -> dict:
        """Select, then fuse (arch7 section 22.8): the winning group's
        representative, whose SQL and chart are the answer's, and, in code,
        what the group's other runs found -- in `rank` order -- that the
        representative's rows bear out: the columns that join onto them on
        the entity's key, the claims those rows reproduce, the assumptions
        each run made, once. Then each losing answer, named with how its
        query differs. No SQL is fused."""
        decision = state["decision"]
        chosen = _candidate(state, decision.chosen)
        others = [c for c in votes.ranked([_candidate(state, i) for i in decision.fused_from]) if c is not chosen]
        result, joined, declined = fuse.fuse_columns(
            chosen, others, self.agent.label_map(), self.agent.dimensions(),
            enabled=self.settings.ensemble_fuse_columns,
        )
        assumptions = list(dict.fromkeys(a for c in [chosen, *others] for a in c.state.get("assumptions") or []))
        claims, report, added, dropped = fuse.fuse_claims(
            chosen, others, result, question=state["question"], assumptions=assumptions,
            cap=self.settings.ensemble_max_claims,
        )
        losing = [g for g in state.get("groups") or [] if decision.chosen not in g.members]
        dissent = fuse.dissent(chosen, losing, state.get("candidates") or [])
        decision = replace(
            decision, joined_columns=joined, declined_columns=declined, claims_added=added, claims_dropped=dropped,
            dissent=dissent,
        )
        return {
            "decision": decision,
            "sql": chosen.state.get("sql") or "",
            "result": result,
            "chart": chosen.state.get("chart"),
            "claims": claims,
            "audit": report,
            "assumptions": assumptions,
            "node_errors": dict(chosen.state.get("node_errors") or {}),
            _DETAIL: (
                f"[{chosen.index}] of {decision.fused_from} ({state['agreement'].level}): "
                f"{len(joined)} column(s) joined, {len(declined)} declined; "
                f"{added} claim(s) added, {dropped} dropped; {len(dissent)} dissenting"
            ),
        }

    def _deliver(self, state: EnsembleState) -> dict:
        """The answer (arch7 section 22.8), rendered for the question as the
        person asked it: the chosen run's rows and claims, opened by the
        agreement line. When no run could be delivered, the original's own
        record is -- its give-up, as arch6 delivers one -- with every run's
        beneath it in the answer's record."""
        decision = state.get("decision") or Decision()
        if decision.chosen is None:
            original = _candidate(state, 0)
            run = original.state
            return {
                "decision": Decision(columns_fused=self.settings.ensemble_fuse_columns),
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
                _DETAIL: f"none: the original's run, which {original.outcome}",
            }
        chosen = _candidate(state, decision.chosen)
        report = state.get("audit") or AuditReport()
        claims = present.surviving_claims(state.get("claims") or [], report)
        answer = present.render_answer(
            state["question"],
            state.get("result") or QueryResult(),
            claims,
            state.get("chart"),
            report,
            assumptions=state.get("assumptions") or [],
            completeness=chosen.state.get("completeness"),
            ensemble=decision,
        )
        return {
            "answer": answer,
            "narrative": " ".join(claim.text for claim in claims),
            _DETAIL: f"{state['agreement'].level}: [{decision.chosen}]",
        }


def _ignore(step: str, detail: str, candidate: int | None = None) -> None:
    """The progress callback of an agent nobody is watching."""


def _for_candidate(report: ProgressFn, index: int) -> ProgressFn:
    """The question's callback, with the candidate's index on every step."""

    def candidate_progress(step: str, detail: str) -> None:
        report(step, detail, candidate=index)

    return candidate_progress


def _f4(update: dict[str, Any], contract: Any) -> str | None:
    """Why a rewording's reading fails F4, or None: the screening could not
    be made, it did not proceed -- a refusal and a request to clarify alike --
    or its reading builds another contract than the original's."""
    if update.get("node_errors"):
        return "F4 screening: the Supervisor could not read it"
    if update.get("verdict", "proceed") != "proceed":
        return f"F4 verdict: {update['verdict']}"
    differ = answer_contract.differences(contract, update["answer_contract"])
    return f"F4 contract: {'; '.join(differ)}" if differ else None


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


def _unrun(state: EnsembleState) -> list[int]:
    """The faithful rewordings no wave has run yet, in the order written."""
    ran = {candidate.index for candidate in state.get("candidates") or []}
    return [p.index for p in state.get("paraphrases") or [] if p.status == FAITHFUL and p.index not in ran]


def _candidate(state: EnsembleState, index: int) -> Candidate:
    return next(c for c in state.get("candidates") or [] if c.index == index)


def _runs(update: dict) -> dict:
    """What the candidate runs' and the vote's spans record: each run in a
    line, not whole -- every run's own spans are beneath it."""
    return {
        **{key: value for key, value in update.items() if key not in ("candidates", "trace")},
        "candidates": [
            {"index": c.index, "wording": c.wording, "outcome": c.outcome, "sql": c.state.get("sql", ""),
             "ms": c.ms, "admissible": c.admissible, "reasons": list(c.reasons), "group": c.group}
            for c in update.get("candidates", [])
        ],
    }
