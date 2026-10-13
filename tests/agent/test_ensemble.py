"""The ensemble's outer graph (arch7.1 section 22): rewordings, the fidelity
gate, the runs, the Judge, the vote, a second wave when the vote does not
settle the question, and the chosen run delivered with what the group's
other runs found fused into it.

Most tests here script the Paraphraser, the Supervisor's readings, the SQL
and the rows each run gets, and read back what the ensemble made of them:
which rewordings ran and why the others did not, how the runs agreed, which
run was chosen and the line the answer opens with. The model calls are
counted where the design depends on them -- a rewording that fails a check
in code costs none.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest
from nl2sql_agent import ensemble as ensemble_module
from nl2sql_agent.config import Settings
from nl2sql_agent.database import QueryResult as DbRows
from nl2sql_agent.ensemble import STEP_LABELS, EnsembleAgent, build_agent, run_pool, step_label
from nl2sql_agent.ensemble_state import Paraphrase, new_ensemble_state
from nl2sql_agent.graph import Nl2SqlAgent
from nl2sql_agent.judge import Ruling, Rulings
from nl2sql_agent.paraphrase import Rewording, Rewordings
from nl2sql_agent.present import Narrative
from nl2sql_agent.supervisor import Screening

from .conftest import FakeDatabase, FakeKnowledgeBase, ScriptedLLM, _StructuredBinding, make_chunk
from .test_graph import TABLES, make_agent, scripted

SQL = "SELECT count(*) AS n FROM dim_store"
QUESTION = "how many stores are there?"
#: Three rewordings that keep every number, name and direction, and differ
#: from the question and from each other.
FAITHFUL = ("count the stores we operate", "what is our store count", "tell me the number of stores in total")


def ensemble(db: FakeDatabase, llm: ScriptedLLM, *, on_progress=None, **settings) -> EnsembleAgent:
    agent = make_agent(db, llm, **settings)
    return EnsembleAgent(agent.settings, agent=agent, on_progress=on_progress)


def rewordings(*texts: str) -> Rewordings:
    return Rewordings(rewordings=[Rewording(text=text, changed=f"changed {i}") for i, text in enumerate(texts, 1)])


def model(*, sql=None, written=None, screening=None, claims=None) -> ScriptedLLM:
    """A model that screens, rewords as told, writes the SQL and narrates
    one claim -- or none, when `claims=[]`."""
    llm = scripted(sql or [SQL] * 4, claims=claims)
    llm.rewordings = written
    if screening is not None:
        llm.screening = screening
    return llm


def calls(llm: ScriptedLLM, schema: type) -> int:
    return sum(1 for asked, _ in llm.structured_invocations if asked is schema)


def rows(*values) -> list[DbRows]:
    """One result per run, in the order the runs execute."""
    return [DbRows(columns=["n"], rows=[(value,)], truncated=False) for value in values]


def ask(llm: ScriptedLLM, *, db: FakeDatabase | None = None, question: str = QUESTION, **settings) -> dict:
    return ensemble(db or FakeDatabase(tables=TABLES), llm, **settings).run(question)


# ---------------------------------------------------------------------------
# One wording, and the answer it gets
# ---------------------------------------------------------------------------


def test_with_no_rewording_the_original_is_answered_as_the_pipeline_answers_it_and_says_so():
    plain = make_agent(FakeDatabase(tables=TABLES), scripted([SQL])).run(QUESTION)
    llm = model()
    state = ask(llm)

    assert state["answer"] == "*Asked 1 way; one run answered.*\n\n" + plain["answer"]
    for field in ("narrative", "sql", "result", "chart", "claims", "audit", "assumptions", "error"):
        assert state[field] == plain[field], field
    assert calls(llm, Screening) == 1, "the anchor's screening is the run's; it is not paid twice"
    assert calls(llm, Rewordings) == 2, "none written, so asked once more, and only once"
    assert state["paraphrase_retried"] is True


def test_the_originals_run_is_recorded_whole_as_candidate_zero():
    state = ask(model())

    [candidate] = state["candidates"]
    assert (candidate.index, candidate.origin, candidate.wave, candidate.outcome) == (0, "original", 1, "answered")
    assert (candidate.admissible, candidate.reasons, candidate.group, candidate.signature) == (True, [], 0, "1")
    assert candidate.state["sql"] == SQL and candidate.ms > 0
    assert state["waves"] == 1 and state["wave_plan"] == [0]
    assert state["agreement"].level == "single"
    assert (state["decision"].chosen, state["decision"].fused_from, state["decision"].columns_fused) == (0, [0], True)


def test_the_originals_run_makes_no_supervisor_call_of_its_own():
    state = ask(model())

    supervise = next(entry for entry in state["candidates"][0].state["trace"] if entry.node == "supervise")
    assert (supervise.model_calls, supervise.detail.startswith("screened by the ensemble")) == (0, True)
    assert [entry.node for entry in state["trace"]] == [
        "screen", "paraphrase", "screen_paraphrase", "plan_wave", "answer", "validate", "judge", "vote", "fuse",
        "deliver",
    ]
    assert state["answer_contract"] == state["candidates"][0].state["answer_contract"]


# ---------------------------------------------------------------------------
# Four wordings: the happy path
# ---------------------------------------------------------------------------


def test_four_wordings_that_agree_are_unanimous_and_the_original_is_chosen():
    llm = model(written=rewordings(*FAITHFUL))
    state = ask(llm)

    assert [p.status for p in state["paraphrases"]] == ["faithful"] * 3
    assert [c.index for c in state["candidates"]] == [0, 1, 2, 3]
    assert [c.origin for c in state["candidates"]] == ["original", "paraphrase", "paraphrase", "paraphrase"]
    assert state["candidates"][2].wording == FAITHFUL[1]
    assert state["agreement"].level == "unanimous" and state["agreement"].agreed == 4
    assert state["decision"].chosen == 0, "all else equal, the original's own run"
    assert state["decision"].fused_from == [0, 1, 2, 3]
    assert state["answer"].startswith("*Agreed by 4 of 4 independent runs of the question, each worded differently.*")
    assert calls(llm, Screening) == 4, "the anchor, and one reading per rewording"
    assert calls(llm, Rewordings) == 1, "three faithful: no retry"


def test_each_rewording_runs_seeded_with_the_reading_made_of_its_own_words():
    readings = [Screening(verdict="proceed", intent=intent) for intent in ("aggregate", "lookup", "compare", "trend")]
    state = ask(model(written=rewordings(*FAITHFUL), screening=readings))

    assert [c.state["intent"] for c in state["candidates"]] == ["aggregate", "lookup", "compare", "trend"]
    assert all(c.state["screened"] for c in state["candidates"])
    assert state["paraphrases"][0].screening["intent"] == "lookup"


def test_the_first_wave_is_the_original_and_the_first_faithful_rewordings_in_the_order_written():
    state = ask(model(written=rewordings(*FAITHFUL, "give me the total store count")), ensemble_paraphrases=3)
    assert state["wave_plan"] == [0, 1, 2, 3]
    assert [p.status for p in state["paraphrases"]] == ["faithful"] * 4, "the fourth is kept for a second wave"
    assert state["trace"][3].detail == "wave 1: the original and rewording(s) 1, 2, 3"


# ---------------------------------------------------------------------------
# The fidelity gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "rewording", "check"),
    [
        ("which 10 stores sold the most?", "which 5 stores sold the most?", "F1 numbers"),
        ("what were sales for Dairy & Eggs?", "what were sales for dairy?", "F2 literals"),
        ("which store sold the most?", "which store sold the least?", "F3 polarity"),
        ("how many stores are there?", "how many stores are there", "F5 distinct"),
    ],
)
def test_a_rewording_that_fails_a_check_in_code_is_never_run_and_costs_no_model_call(question, rewording, check):
    llm = model(written=rewordings(rewording))
    state = ask(llm, question=question)

    # The retry hands the same rewording back; it is not judged twice.
    [discarded] = [p for p in state["paraphrases"] if p.text == rewording]
    assert discarded.status == "discarded" and discarded.reason.startswith(check)
    assert [c.index for c in state["candidates"]] == [0]
    assert calls(llm, Screening) == 1, "the anchor's only: the check in code came first"


@pytest.mark.parametrize("verdict", ["out_of_domain", "injection", "ambiguous"])
def test_a_rewording_the_supervisor_will_not_proceed_with_is_discarded_whatever_it_said(verdict):
    """R7: every verdict but proceed discards -- a refusal and a request to
    clarify alike -- and what is discarded is never run."""
    reading = Screening(verdict=verdict, intent="aggregate", clarification="Which year?")
    llm = model(written=rewordings(FAITHFUL[0]), screening=[Screening(verdict="proceed", intent="aggregate"), reading])
    state = ask(llm, clarify_enabled=True)

    assert state["paraphrases"][0].reason == f"F4 verdict: {verdict}"
    assert state["paraphrases"][0].screening is None
    assert [c.index for c in state["candidates"]] == [0]


def test_a_rewording_whose_reading_builds_another_contract_is_discarded_saying_what_differs():
    readings = [Screening(verdict="proceed", intent="aggregate"),
                Screening(verdict="proceed", intent="aggregate", period="fiscal year 2024", measure="count")]
    state = ask(model(written=rewordings(FAITHFUL[0]), screening=readings))
    reason = state["paraphrases"][0].reason
    assert reason.startswith("F4 contract:") and "period None vs 'fiscal year 2024'" in reason


def test_a_screening_that_failed_discards_the_rewording_it_was_for():
    class _FailsSecond(ScriptedLLM):
        def with_structured_output(self, schema):
            if schema is Screening and calls(self, Screening) == 1:
                return _Unanswered(self, schema)
            return super().with_structured_output(schema)

    llm = _FailsSecond(sql_responses=[SQL], narration=scripted([]).narration, rewordings=rewordings(FAITHFUL[0]))
    state = ask(llm)
    assert state["paraphrases"][0].reason == "F4 screening: the Supervisor could not read it"


def test_fewer_faithful_than_wanted_asks_once_more_naming_each_failure_and_numbering_on():
    first = rewordings("which 5 stores are there", "how many stores are there")  # F1, F5
    second = rewordings(FAITHFUL[0])
    llm = model(written=[first, second])
    state = ask(llm)

    assert [(p.index, p.status) for p in state["paraphrases"]] == [(1, "discarded"), (2, "discarded"), (3, "faithful")]
    retry = [messages for schema, messages in llm.structured_invocations if schema is Rewordings][1]
    prompt = retry[-1].content
    assert "These rewordings were rejected:" in prompt
    assert "which 5 stores are there -- because F1 numbers" in prompt
    assert "how many stores are there -- because F5 distinct" in prompt
    assert "Write 3 rewordings" in prompt, "replacements for the three wanted, none faithful yet"
    assert calls(llm, Rewordings) == 2, "one retry, never two"
    assert [c.index for c in state["candidates"]] == [0, 3]
    assert state["trace"][2].detail == "1 faithful of 3; F1 x1, F5 x1; asked once more"


def test_a_paraphraser_that_fails_costs_the_rewordings_and_nothing_else():
    """arch7 section 22.12: the original runs alone, and the answer says it
    was asked one way."""

    class _Fails(ScriptedLLM):
        def with_structured_output(self, schema):
            if schema is Rewordings:
                return _Unanswered(self, schema)
            return super().with_structured_output(schema)

    state = ask(_Fails(sql_responses=[SQL], narration=scripted([]).narration))
    assert state["node_errors"]["paraphraser"] == "the model host did not answer"
    assert state["paraphrases"] == [] and state["paraphrase_retried"] is False
    assert state["answer"].startswith("*Asked 1 way; one run answered.*")
    assert state["trace"][1].detail == "failed: ConnectionError"


def test_a_retry_that_fails_is_recorded_and_the_faithful_ones_run():
    class _RetryFails(ScriptedLLM):
        def with_structured_output(self, schema):
            if schema is Rewordings and calls(self, Rewordings) == 1:
                return _Unanswered(self, schema)
            return super().with_structured_output(schema)

    llm = _RetryFails(sql_responses=[SQL] * 2, narration=scripted([]).narration, rewordings=rewordings(FAITHFUL[0]))
    state = ask(llm)
    assert state["node_errors"]["paraphraser"] == "the model host did not answer"
    assert [c.index for c in state["candidates"]] == [0, 1]


def test_with_the_supervisor_off_nothing_is_reworded_and_nothing_screened():
    """No rewording runs that the Supervisor has not read (R7), and with it
    off nobody reads one: the original alone, as the pipeline answers it."""
    plain = make_agent(FakeDatabase(tables=TABLES), scripted([SQL]), supervisor_enabled=False).run(QUESTION)
    llm = model(written=rewordings(*FAITHFUL))
    state = ask(llm, supervisor_enabled=False)

    assert calls(llm, Rewordings) == 0 and calls(llm, Screening) == 0
    assert state["answer"].endswith(plain["answer"])
    assert state["trace"][1].detail.startswith("skipped") and state["trace"][2].detail.startswith("skipped")
    assert not state["candidates"][0].state["screened"]


@pytest.mark.parametrize("verdict", ["out_of_domain", "injection"])
def test_a_refused_question_is_reworded_by_nobody_and_answered_as_the_pipeline_refuses_it(verdict):
    plain = make_agent(FakeDatabase(tables=TABLES), ScriptedLLM(screening=Screening(verdict=verdict, intent="lookup"))).run("q")
    llm = ScriptedLLM(screening=Screening(verdict=verdict, intent="lookup"), rewordings=rewordings(*FAITHFUL))
    state = ask(llm, question="q")

    assert state["answer"] == plain["answer"] and calls(llm, Rewordings) == 0
    assert state["candidates"] == [] and state["agreement"].level == "none"
    assert [entry.node for entry in state["trace"]] == ["screen", "refuse"]


# ---------------------------------------------------------------------------
# The vote, and the run chosen
# ---------------------------------------------------------------------------


def _vote(values, *, explain_error=None, **settings) -> dict:
    db = FakeDatabase(tables=TABLES, run_select_result=rows(*values), explain_error=explain_error)
    return ask(model(written=rewordings(*FAITHFUL), claims=[]), db=db, **settings)


def test_three_of_four_agreeing_is_a_majority_and_the_one_is_outvoted():
    state = _vote([10, 10, 99, 10])

    assert state["agreement"].level == "majority" and state["agreement"].agreed == 3
    assert [g.members for g in state["groups"]] == [[0, 1, 3], [2]]
    assert [c.group for c in state["candidates"]] == [0, 0, 1, 0]
    assert state["result"].rows == [[10]]
    assert state["answer"].startswith("*3 of 4 runs agreed; 1 answered differently.*")


def test_a_two_two_split_goes_to_the_originals_group_as_contested():
    state = _vote([10, 99, 10, 99])

    assert state["agreement"].level == "contested", "no majority, and no Judge yet"
    assert state["groups"][0].members == [0, 2]
    assert state["decision"].chosen == 0 and state["result"].rows == [[10]]
    assert state["answer"].startswith("*The runs disagreed and no answer had a majority; this is the largest group's (2 of 4 runs).*")


def test_two_one_one_is_no_majority_either():
    state = _vote([10, 10, 20, 30])
    assert state["agreement"].level == "contested" and state["agreement"].agreed == 2
    assert len(state["groups"]) == 3


def test_a_run_that_gave_up_does_not_vote_and_the_rest_can_still_be_unanimous():
    explain = [None, 'column "nope" does not exist', None, None]
    state = _vote([10, 10, 10], explain_error=explain, max_attempts=1)

    gave_up = state["candidates"][1]
    assert (gave_up.outcome, gave_up.admissible) == ("gave_up", False)
    assert gave_up.reasons == ["E1 answered: gave up after 1 attempts"]
    assert gave_up.group is None and gave_up.signature == ""
    assert state["agreement"].level == "unanimous" and (state["agreement"].admissible, state["agreement"].total) == (3, 4)
    assert state["answer"].startswith(
        "*Agreed by 3 of 4 independent runs of the question, each worded differently; 1 could not answer.*"
    )


def test_when_no_run_can_vote_the_original_is_delivered_as_it_gave_up():
    plain = make_agent(FakeDatabase(tables=TABLES, explain_error='column "nope" does not exist'),
                       scripted([SQL]), max_attempts=1).run(QUESTION)
    db = FakeDatabase(tables=TABLES, explain_error='column "nope" does not exist')
    state = ask(model(written=rewordings(*FAITHFUL)), db=db, max_attempts=1)

    assert state["agreement"].level == "none" and state["decision"].chosen is None
    assert state["error"] == plain["error"] and state["answer"] == plain["answer"]
    assert [entry.node for entry in state["trace"]][-3:] == ["validate", "vote", "deliver"], "nothing to judge or fuse"


def test_the_originals_own_run_is_chosen_over_a_rewordings_even_after_a_repair():
    """Selection (arch7 section 22.8): the ranking puts the wording the person
    used before fewer attempts -- the SQL they read should be written for
    their words."""
    explain = ['column "nope" does not exist', None, None, None, None]
    llm = model(written=rewordings(*FAITHFUL), sql=[SQL] * 5, claims=[])
    db = FakeDatabase(tables=TABLES, run_select_result=rows(10, 10, 10, 10), explain_error=explain)
    state = ask(llm, db=db)

    assert state["candidates"][0].state["attempts"] == 2
    assert state["decision"].chosen == 0


def test_among_rewordings_fewer_attempts_and_then_the_lower_index_decide():
    explain = [None, 'column "nope" does not exist', None, None, None]
    llm = model(written=rewordings(*FAITHFUL), sql=[SQL] * 5, claims=[])
    db = FakeDatabase(tables=TABLES, run_select_result=rows(99, 10, 10, 10), explain_error=explain)
    state = ask(llm, db=db)

    assert state["groups"][0].members == [1, 2, 3], "the original outvoted"
    assert [c.state["attempts"] for c in state["candidates"]] == [1, 2, 1, 1]
    assert state["decision"].chosen == 2 and state["decision"].fused_from == [1, 2, 3]
    assert state["sql"] == state["candidates"][2].state["sql"]


# ---------------------------------------------------------------------------
# The Judge, before the vote (arch7.1 section 22.7)
# ---------------------------------------------------------------------------

#: B07, as the paraphrase set found it: the original right, three rewordings
#: agreeing on the grain trap's figure.
B07 = (34.20, 34.65, 34.65, 34.65)
SETS_ASIDE_B = Rulings(rulings=[
    Ruling(answer="A", accepted=True, why="It rolls both facts up to the month first."),
    Ruling(answer="B", accepted=False, why="It joins daily sales to monthly costs on the date."),
])


def _judged(values, rulings, **settings) -> tuple[ScriptedLLM, dict]:
    llm = model(written=rewordings(*FAITHFUL), claims=[])
    llm.rulings = rulings
    db = FakeDatabase(tables=TABLES, run_select_result=rows(*values))
    return llm, ask(llm, db=db, **settings)


def _shown(llm: ScriptedLLM) -> str:
    [messages] = [messages for schema, messages in llm.structured_invocations if schema is Rulings]
    return "\n".join(message.content for message in messages)


def test_the_judge_is_asked_once_at_the_heavy_rung_with_every_answer_and_the_originals_knowledge():
    knowledge = FakeKnowledgeBase(chunks=[make_chunk(content="fact_item_cogs is recorded once a month.")])
    llm, state = _judged([10, 10, 99, 10], None, knowledge_base=knowledge)

    assert calls(llm, Rulings) == 1
    entry = next(entry for entry in state["trace"] if entry.node == "judge")
    assert (entry.model_calls, entry.rung) == (1, "heavy")
    shown = _shown(llm)
    assert "Answer A" in shown and "Answer B" in shown and "Answer C" not in shown
    assert "fact_item_cogs is recorded once a month." in shown
    assert [(v.group, v.accepted) for v in state["judgement"].verdicts] == [(0, True), (1, True)]
    assert state["agreement"].level == "majority", "nothing set aside: the vote as the runs cast it"


def test_a_majority_the_judge_sets_aside_loses_to_the_answer_it_accepts():
    llm, state = _judged(B07, SETS_ASIDE_B)

    assert [g.members for g in state["groups"]] == [[1, 2, 3], [0]]
    agreement = state["agreement"]
    assert (agreement.level, agreement.agreed, agreement.admissible, agreement.set_aside) == ("judged", 1, 1, 3)
    assert state["decision"].chosen == 0 and state["result"].rows == [[34.2]]
    judgement = state["judgement"]
    assert (judgement.set_aside, judgement.overruled, judgement.instead_of) == ([1, 2, 3], True, 1)
    assert state["answer"].startswith(
        "*The Judge set aside the answer 3 of 4 runs gave -- It joins daily sales to monthly costs on the date -- "
        "and accepted this one, which 1 gave.*"
    )


def test_with_the_judge_off_it_is_never_asked_and_the_runs_vote_alone():
    llm, state = _judged(B07, SETS_ASIDE_B, ensemble_judge_enabled=False)

    assert calls(llm, Rulings) == 0 and state["judgement"] is None
    assert "judge" not in [entry.node for entry in state["trace"]]
    assert (state["agreement"].level, state["decision"].chosen) == ("majority", 1)


def test_a_judge_that_fails_lets_the_runs_vote_alone_and_the_answer_says_so():
    llm, state = _judged([10, 10, 10, 10], ConnectionError("refused"))

    assert state["agreement"].level == "unanimous"
    assert state["judgement"].error == "ConnectionError: refused" and state["node_errors"]["judge"] == "refused"
    assert next(entry for entry in state["trace"] if entry.node == "judge").model_calls == 0
    assert state["answer"].startswith(
        "*Agreed by 4 of 4 independent runs of the question, each worded differently. The Judge could not be asked.*"
    )


def test_when_the_judge_accepts_none_the_runs_own_choice_is_delivered_as_contested():
    rulings = Rulings(rulings=[Ruling(answer="A", accepted=False, why="It keeps only two channels.")])
    _, state = _judged([10, 10, 10, 10], rulings)

    assert (state["agreement"].level, state["decision"].chosen) == ("contested", 0)
    assert state["judgement"].set_aside == [0, 1, 2, 3] and not state["judgement"].overruled
    assert state["answer"].startswith(
        "*The Judge accepted none of the answers -- It keeps only two channels -- "
        "so this is the runs' own choice, which 4 of 4 gave.*"
    )


# ---------------------------------------------------------------------------
# A second wave (arch7.1 section 22.7)
# ---------------------------------------------------------------------------

#: Two faithful rewordings more than the first wave runs: a second wave's.
MORE = ("give me the total store count", "how many stores do we run")


def _waves(values, *, rulings=None, **settings) -> tuple[ScriptedLLM, dict]:
    llm = model(written=rewordings(*FAITHFUL, *MORE), sql=[SQL] * 6, claims=[])
    llm.rulings = rulings
    db = FakeDatabase(tables=TABLES, run_select_result=rows(*values))
    return llm, ask(llm, db=db, **settings)


def test_a_two_two_split_runs_the_rewordings_left_and_the_judge_and_the_vote_again_over_every_run():
    llm, state = _waves([10, 99, 10, 99, 10, 10])

    assert state["waves"] == 2
    assert [(c.index, c.wave) for c in state["candidates"]] == [(0, 1), (1, 1), (2, 1), (3, 1), (4, 2), (5, 2)]
    assert [g.members for g in state["groups"]] == [[0, 2, 4, 5], [1, 3]], "recounted over both waves"
    assert (state["agreement"].level, state["agreement"].agreed, state["agreement"].total) == ("majority", 4, 6)
    assert calls(llm, Rulings) == 2, "the Judge once a wave"
    nodes = [entry.node for entry in state["trace"]]
    assert nodes == [
        "screen", "paraphrase", "screen_paraphrase", "plan_wave", "answer", "validate", "judge", "vote",
        "plan_wave", "answer", "validate", "judge", "vote", "fuse", "deliver",
    ]
    first_vote, second_plan = state["trace"][7], state["trace"][8]
    assert first_vote.detail.endswith("unsettled: a second wave")
    assert second_plan.detail == "wave 2: rewording(s) 4, 5"
    assert state["another_wave"] is False
    assert state["answer"].startswith("*4 of 6 runs agreed; 2 answered differently.*")


def test_one_run_left_standing_by_the_judge_is_given_a_second_wave_and_the_judge_reads_every_group_again():
    llm, state = _waves([*B07, 34.20, 34.20], rulings=[SETS_ASIDE_B, SETS_ASIDE_B])

    assert state["waves"] == 2 and calls(llm, Rulings) == 2
    agreement = state["agreement"]
    assert (agreement.level, agreement.agreed, agreement.admissible, agreement.set_aside) == ("unanimous", 3, 3, 3)
    assert state["decision"].chosen == 0 and state["result"].rows == [[34.2]]
    assert state["answer"].startswith(
        "*Agreed by 3 of 6 independent runs of the question, each worded differently; the Judge set aside the "
        "answer of 3 others.*"
    )


def test_a_settled_vote_runs_no_second_wave_though_rewordings_are_left():
    llm, state = _waves([10, 10, 10, 10])
    assert state["waves"] == 1 and calls(llm, Rulings) == 1
    assert [p.index for p in state["paraphrases"] if p.status == "faithful"] == [1, 2, 3, 4, 5]


def test_when_the_judge_accepts_none_no_second_wave_is_taken():
    rulings = Rulings(rulings=[Ruling(answer="A", accepted=False, why="It keeps only two channels.")])
    llm, state = _waves([10, 10, 10, 10], rulings=rulings)
    assert (state["waves"], state["agreement"].level, calls(llm, Rulings)) == (1, "contested", 1)


def test_one_wave_allowed_is_one_wave():
    llm, state = _waves([10, 99, 10, 99], ensemble_waves=1)
    assert (state["waves"], state["agreement"].level, calls(llm, Rulings)) == (1, "contested", 1)
    assert "deadline" not in state["agreement"].why


def test_a_deadline_passed_starts_no_second_wave_and_the_vote_says_so():
    llm, state = _waves([10, 99, 10, 99], ensemble_deadline_seconds=1e-6)
    assert (state["waves"], state["agreement"].level, calls(llm, Rulings)) == (1, "contested", 1)
    assert state["agreement"].why.endswith("; the deadline passed before a second wave")


def test_a_second_wave_plans_only_faithful_rewordings_not_yet_run_and_empties_the_vote_before_it():
    """R7, widened: a rewording the gate discarded in the first wave is never
    picked up in the second."""
    agent = ensemble(FakeDatabase(tables=TABLES), model())
    state = {
        **new_ensemble_state(QUESTION),
        "waves": 1,
        "paraphrases": [
            Paraphrase(index=1, text="a", changed="", status="faithful"),
            Paraphrase(index=2, text="b", changed="", status="discarded", reason="F2 literals"),
            Paraphrase(index=3, text="c", changed="", status="faithful"),
            Paraphrase(index=4, text="d", changed="", status="pending"),
            Paraphrase(index=5, text="e", changed="", status="faithful"),
        ],
        "candidates": [_ran(0), _ran(1)],
        "another_wave": True,
    }
    update = agent._plan_wave(state)
    assert (update["waves"], update["wave_plan"]) == (2, [3, 5])
    assert (update["groups"], update["judgement"], update["another_wave"]) == ([], None, False)


def _ran(index: int):
    from nl2sql_agent.ensemble_state import Candidate

    return Candidate(index=index, wording=str(index), origin="paraphrase", wave=1, state={}, outcome="answered")


# ---------------------------------------------------------------------------
# Fusion (arch7 section 22.8), through the graph
# ---------------------------------------------------------------------------

LISTED = ("list the stores we have", "show me every store we have", "name all of our stores")


def test_a_column_another_agreeing_run_carried_is_joined_onto_the_chosen_rows_and_its_run_named():
    from nl2sql_agent.contract import ContractResources, Label, LabelMap

    resources = ContractResources(
        label_map=LabelMap([Label(table="dim_store", key="store_key", label="store_name")]),
        dimensions={"store_key": "dim_store", "store_name": "dim_store", "region_name": "dim_store"},
    )
    plain = DbRows(columns=["store_key", "store_name"], rows=[(1, "North"), (2, "South")], truncated=False)
    wider = DbRows(columns=["store_key", "store_name", "region_name"],
                   rows=[(2, "South", "Coast"), (1, "North", "Hills")], truncated=False)
    db = FakeDatabase(tables=TABLES, run_select_result=[plain, wider, plain, plain])
    state = ask(model(written=rewordings(*LISTED), claims=[]), db=db, question="which stores do we have?",
                contract_resources=resources)

    assert (state["agreement"].level, state["decision"].chosen) == ("unanimous", 0)
    assert state["result"].columns == ["store_key", "store_name", "region_name"]
    assert state["result"].rows == [[1, "North", "Hills"], [2, "South", "Coast"]]
    assert [(c.column, c.from_candidate) for c in state["decision"].joined_columns] == [("region_name", 1)]
    assert state["candidates"][0].state["result"].columns == ["store_key", "store_name"], "its own record, unwidened"
    assert state["sql"] == state["candidates"][0].state["sql"], "no SQL is fused"
    assert "region_name is from run 1, which agreed, joined on store_key" in state["answer"]
    assert "| Hills |" in state["answer"]


def test_with_column_fusion_off_the_chosen_rows_are_delivered_as_they_ran():
    plain = DbRows(columns=["n"], rows=[(10,)], truncated=False)
    state = ask(model(written=rewordings(*FAITHFUL), claims=[]), db=FakeDatabase(tables=TABLES,
                run_select_result=[plain] * 4), ensemble_fuse_columns=False)
    assert state["decision"].columns_fused is False and state["decision"].joined_columns == []


def test_each_answer_that_lost_is_named_in_the_answer():
    state = _vote([10, 10, 99, 10])
    [lost] = state["decision"].dissent
    assert (lost.group, lost.members, lost.signature, lost.differs) == (1, [2], "99", "")
    assert state["answer"].endswith("*Run 2 answered differently (99).*")


def test_the_same_claim_from_every_run_is_said_once():
    state = ask(model(written=rewordings(*FAITHFUL)))
    assert (state["decision"].claims_added, state["decision"].claims_dropped) == (0, 0)
    assert [c.text for c in state["claims"]] == ["The count is 1."]
    fuse = next(entry for entry in state["trace"] if entry.node == "fuse")
    assert fuse.detail == (
        "[0] of [0, 1, 2, 3] (unanimous): 0 column(s) joined, 0 declined; 0 claim(s) added, 0 dropped; 0 dissenting"
    )


def test_the_assumptions_every_run_made_are_stated_once_in_the_order_first_made():
    from nl2sql_agent.ensemble_state import Agreement, Candidate, Decision, Group
    from nl2sql_agent.state import QueryResult, new_state

    def made(index: int, assumptions: list[str]) -> Candidate:
        state = {**new_state(QUESTION), "sql": SQL, "result": QueryResult(columns=["n"], rows=[[1]]),
                 "assumptions": assumptions}
        return Candidate(index=index, wording=QUESTION, origin="original" if index == 0 else "paraphrase", wave=1,
                         state=state, outcome="answered", admissible=True, group=0)

    agent = ensemble(FakeDatabase(tables=TABLES), model())
    state = {
        **new_ensemble_state(QUESTION),
        "candidates": [made(0, ["fiscal year 2025"]), made(1, ["fiscal year 2025", "net of returns"])],
        "groups": [Group(index=0, members=[0, 1], representative=0, signature="1")],
        "agreement": Agreement(2, 2, 2, "unanimous"),
        "decision": Decision(chosen=0, fused_from=[0, 1]),
    }
    assert agent._fuse(state)["assumptions"] == ["fiscal year 2025", "net of returns"]


# ---------------------------------------------------------------------------
# Progress, the pool, and the surface the API and the CLI hold
# ---------------------------------------------------------------------------


def test_progress_names_each_runs_steps_and_none_for_the_ensembles_own():
    seen: list[tuple[int | None, str]] = []
    ensemble(FakeDatabase(tables=TABLES), model(written=rewordings(*FAITHFUL))).run(
        QUESTION, on_progress=lambda step, detail, candidate=None: seen.append((candidate, step))
    )
    outer = [step for candidate, step in seen if candidate is None]
    assert outer == [
        "screen", "paraphrase", "screen_paraphrase", "plan_wave", "answer", "validate", "judge", "vote", "fuse", "deliver",
    ]
    for index in range(4):
        steps = [step for candidate, step in seen if candidate == index]
        assert steps[0] == "supervise" and steps[-1] == "finish"


def test_every_label_is_a_node_registered_through_the_wrapper():
    """R2, widened: every outer node is wrapped by `traced_node`, so none can
    let a private key through to the state, the wire or the trace."""
    source = Path(ensemble_module.__file__).read_text()
    wrapped = re.findall(r'graph\.add_node\("([a-z_]+)", self\._traced\("\1"', source)
    assert sorted(wrapped) == sorted(STEP_LABELS)


class _Timed(ScriptedLLM):
    """A model host that records when each SQL call was in it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.windows: list[tuple[float, float]] = []
        self._lock = threading.Lock()

    def invoke(self, messages):
        entered = time.perf_counter()
        time.sleep(0.05)
        with self._lock:
            answer = super().invoke(messages)
            self.windows.append((entered, time.perf_counter()))
        return answer


def _overlapped(windows) -> bool:
    ordered = sorted(windows)
    return any(later[0] < earlier[1] for earlier, later in zip(ordered, ordered[1:]))


def _timed(slots: int) -> _Timed:
    llm = _Timed(sql_responses=[SQL] * 4, narration=Narrative(), screening=Screening(verdict="proceed", intent="aggregate"),
                 rewordings=rewordings(*FAITHFUL))
    ensemble(FakeDatabase(tables=TABLES), llm, ollama_parallel_calls=slots, narrate_enabled=False).run(QUESTION)
    return llm


def test_at_one_slot_the_runs_calls_to_the_host_never_overlap():
    llm = _timed(1)
    assert len(llm.windows) == 4 and not _overlapped(llm.windows)


def test_at_two_slots_two_runs_calls_may():
    assert any(_overlapped(_timed(2).windows) for _ in range(3))


def test_an_outer_steps_label_is_its_own_and_a_runs_is_the_pipelines():
    assert step_label("deliver") == "answer"
    assert step_label("screen_paraphrase") == "fidelity"
    assert step_label("supervise", 0) == "screen"
    assert step_label("answer", 0) == "answer", "a run has no node of that name: its own name"


def test_the_ensemble_answers_to_the_surface_the_pipeline_does():
    db = FakeDatabase(tables=TABLES)
    agent = ensemble(db, model())
    assert agent.db is db and agent.router is agent.agent.router
    assert agent.tracer is agent.agent.tracer and agent.reload() == []


def test_the_settings_decide_which_agent_is_built(monkeypatch):
    built = []
    monkeypatch.setattr(ensemble_module, "Nl2SqlAgent", lambda settings, **kwargs: built.append(kwargs) or "pipeline")
    monkeypatch.setattr(ensemble_module, "EnsembleAgent", lambda settings, **kwargs: "ensemble")
    assert build_agent(Settings(), tracer=None) == "ensemble"
    assert build_agent(Settings(ensemble_enabled=False), tracer=None) == "pipeline"
    assert built == [{"tracer": None}]


def test_a_deadline_is_set_only_when_one_is_asked_for():
    seen = {}

    def keep(agent: EnsembleAgent) -> EnsembleAgent:
        graph = agent._graph

        class _Spy:
            def invoke(self, state, config):
                seen.update(deadline=state["deadline"], parallel_calls=state["parallel_calls"])
                return graph.invoke(state, config=config)

        agent._graph = _Spy()
        return agent

    keep(ensemble(FakeDatabase(tables=TABLES), model())).run("q")
    assert seen == {"deadline": 0.0, "parallel_calls": 1}
    keep(ensemble(FakeDatabase(tables=TABLES), model(), ensemble_deadline_seconds=60)).run("q")
    assert seen["deadline"] > 0


def test_the_pool_keeps_the_order_it_was_given_and_runs_nothing_for_nothing():
    assert run_pool(lambda n: n * 2, [3, 1, 2], 1) == [6, 2, 4]
    assert run_pool(lambda n: n * 2, [3, 1, 2], 3) == [6, 2, 4]
    assert run_pool(lambda n: n, [], 2) == []


def test_a_run_that_raises_raises_as_the_pipeline_would():
    def boom(n):
        raise RuntimeError(f"run {n}")

    with pytest.raises(RuntimeError, match="run 1"):
        run_pool(boom, [1], 1)


def test_a_rewording_in_the_plan_runs_on_its_own_words_trusting_its_own_screening():
    """R7's seam, by hand: a screened run is built only from a screening the
    outer graph made itself, and makes no Supervisor call."""
    llm = scripted([SQL, SQL])
    agent = ensemble(FakeDatabase(tables=TABLES), llm)
    screening = {"verdict": "proceed", "intent": "lookup", "clarification": None,
                 "entities": ["store"], "measure": "", "period": ""}
    state = {
        **new_ensemble_state(QUESTION),
        "screening": {**screening, "intent": "aggregate"},
        "paraphrases": [Paraphrase(index=1, text="count the stores", changed="verb", status="faithful",
                                   screening=screening)],
        "waves": 1,
        "wave_plan": [0, 1],
    }
    original, rewording = agent._answer(state)["candidates"]

    assert (rewording.index, rewording.origin, rewording.wording) == (1, "paraphrase", "count the stores")
    assert (rewording.state["intent"], rewording.state["screened"]) == ("lookup", True)
    assert original.state["intent"] == "aggregate"
    assert calls(llm, Screening) == 0


def test_the_vote_and_the_runs_spans_record_a_line_per_run_not_the_runs():
    state = ask(model())
    summary = ensemble_module._runs({"candidates": state["candidates"], "agreement": state["agreement"],
                                     "trace": ["x"]})
    assert summary["agreement"] == state["agreement"] and "trace" not in summary
    assert summary["candidates"] == [{"index": 0, "wording": QUESTION, "outcome": "answered", "sql": SQL,
                                      "ms": state["candidates"][0].ms, "admissible": True, "reasons": [], "group": 0}]


def test_the_pipeline_alone_is_still_a_pipeline():
    assert isinstance(make_agent(FakeDatabase(tables=TABLES), scripted([SQL])), Nl2SqlAgent)


class _Unanswered(_StructuredBinding):
    def invoke(self, messages):
        raise ConnectionError("the model host did not answer")


# ---------------------------------------------------------------------------
# The anchor contract: what every run is held to, and what F4 compares
# ---------------------------------------------------------------------------


def _labels(*names: str):
    """A label map naming each dimension: `store` is dim_store's store_key and store_name."""
    from nl2sql_agent.contract import ContractResources, Label, LabelMap

    return ContractResources(
        label_map=LabelMap([Label(table=f"dim_{name}", key=f"{name}_key", label=f"{name}_name") for name in names])
    )


def test_a_rewording_is_read_in_its_questions_shape_so_its_phrasing_is_not_held_against_it():
    """A count asked as "what is the store count" is the same question as
    "how many stores are there?", but the contract's reading of one number
    knows only the second's words. F4 builds the rewording's contract in the
    original's shape -- numbers and direction are F1's and F3's -- and so
    compares what the Supervisor read: here the same store."""
    from nl2sql_agent.contract import build_contract, differences

    resources = _labels("store")
    readings = [Screening(verdict="proceed", intent="lookup", entities=["store"]),
                Screening(verdict="proceed", intent="aggregate", entities=["store"])]
    state = ask(model(written=rewordings("what is the store count"), screening=readings),
                contract_resources=resources)

    assert state["paraphrases"][0].status == "faithful"
    own_words = build_contract("what is the store count", entities=["store"], resources=resources)
    assert differences(state["answer_contract"], own_words), "read in its own words, F4 would have discarded it"


def test_a_reading_of_another_entity_is_still_discarded():
    readings = [Screening(verdict="proceed", intent="lookup", entities=["store"]),
                Screening(verdict="proceed", intent="lookup", entities=["vendor"])]
    state = ask(model(written=rewordings("which vendor has the most stores"), screening=readings),
                question="which store has the most sales?", contract_resources=_labels("store", "vendor"))
    assert state["paraphrases"][0].reason == "F4 contract: entities [store_name] vs [vendor_name]"


def test_every_run_is_held_to_the_contract_read_from_the_question_as_asked():
    """arch7 section 22.2: one contract for the whole question. Each run is
    seeded with it and makes no Supervisor call, whatever its own reading's
    intent."""
    readings = [Screening(verdict="proceed", intent=intent) for intent in ("aggregate", "lookup", "compare", "trend")]
    state = ask(model(written=rewordings(*FAITHFUL), screening=readings))

    assert all(c.state["answer_contract"] == state["answer_contract"] for c in state["candidates"])
    assert all(c.state["screening_fields"]["contract"] is state["answer_contract"] for c in state["candidates"])
