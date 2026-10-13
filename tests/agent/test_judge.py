"""The Judge (`judge.py`; arch7.1 section 22.7): what it is shown, and how its
verdicts are read.

It reads every distinct answer the runs gave before the vote counts them --
shown by letter, the original's first, never with how many runs gave it --
and returns a verdict per letter. What it writes is only ever read as a
verdict: a letter that names no answer, or a second verdict on one, is
ignored, and an answer it said nothing about stands.
"""

from __future__ import annotations

import pytest
from nl2sql_agent import judge
from nl2sql_agent.ensemble_state import Candidate, Group, GroupVerdict
from nl2sql_agent.judge import Ruling, Rulings
from nl2sql_agent.state import AnswerContract, EntityRef, QueryResult, new_state

from .conftest import ScriptedLLM

QUESTION = "What was the gross margin percentage for Dairy & Eggs in fiscal month 12 of FY2025?"
CONTRACT = AnswerContract(entities=[EntityRef(word="department")], period="fiscal month 12 of FY2025")
DAILY = "SELECT ... JOIN fact_item_cogs c ON c.date_key = s.sales_date_key"
MONTHLY = "WITH monthly_sales AS (...), monthly_cost AS (...) SELECT ..."


def run(index: int, sql: str, rows, *, columns=("gross_margin_pct",)) -> Candidate:
    result = QueryResult(columns=list(columns), rows=[list(r) for r in rows])
    return Candidate(index=index, wording=f"wording {index}", origin="original" if index == 0 else "paraphrase",
                     wave=1, state={**new_state(f"wording {index}"), "sql": sql, "result": result}, outcome="answered")


#: B07 as the paraphrase set found it: the original right, three rewordings
#: agreeing on the grain trap's figure.
CANDIDATES = [run(0, MONTHLY, [(34.20,)]), run(1, DAILY, [(34.65,)]), run(2, DAILY, [(34.65,)]), run(3, DAILY, [(34.65,)])]
GROUPS = [
    Group(index=0, members=[1, 2, 3], representative=1, signature="34.65"),
    Group(index=1, members=[0], representative=0, signature="34.20"),
]


def text(llm_messages) -> str:
    return "\n".join(message.content for message in llm_messages)


def test_the_judge_is_shown_each_answer_by_letter_the_originals_first_and_never_how_many_gave_it():
    shown = text(judge.messages(QUESTION, CONTRACT, GROUPS, CANDIDATES))

    assert QUESTION in shown
    a, b = shown.index("Answer A"), shown.index("Answer B")
    assert shown.index(MONTHLY) > a and shown.index(MONTHLY) < b, "A is the original's group, the smaller one"
    assert shown.index(DAILY) > b
    assert "34.2" in shown and "34.65" in shown and "gross_margin_pct" in shown
    assert "Give a verdict for each of A, B." in shown
    for count in ("3 runs", "1 run", "3 of 4", "majority"):
        assert count not in shown
    assert "department" in shown and "fiscal month 12 of FY2025" in shown


def test_the_knowledge_and_the_assumptions_the_runs_had_are_shown_and_the_knowledge_capped():
    long = "fact_item_cogs is monthly. " + "x" * judge.KNOWLEDGE_CHARS
    shown = text(judge.messages(QUESTION, CONTRACT, GROUPS, CANDIDATES, knowledge=long,
                                assumptions=["the fiscal calendar"]))
    assert "fact_item_cogs is monthly." in shown and "x" * judge.KNOWLEDGE_CHARS not in shown
    assert "Every answer was told to assume:\n- the fiscal calendar" in shown
    bare = text(judge.messages(QUESTION, CONTRACT, GROUPS, CANDIDATES))
    assert "Knowledge base" not in bare and "told to assume" not in bare


def test_an_answer_shows_its_first_rows_and_how_many_there_were():
    many = run(0, "SELECT store, n FROM t", [(f"s{i}", i) for i in range(8)], columns=("store", "n"))
    block = judge.answer_block("A", many)
    assert "Rows (8, first 5 shown):\nstore | n\ns0 | 0" in block and "s4 | 4" in block and "s5" not in block
    assert "Rows (0):\nn\n(no rows)" in judge.answer_block("B", run(1, "SELECT 1", [], columns=("n",)))
    assert "x | NULL" in judge.answer_block("C", run(2, "SELECT 1", [("x", None)], columns=("a", "b")))


def test_verdicts_come_back_in_group_order_read_by_letter():
    llm = ScriptedLLM(rulings=Rulings(rulings=[
        Ruling(answer="A", accepted=True, why="It rolls both facts up to the month first."),
        Ruling(answer="Answer B.", accepted=False, why="It joins daily sales to   monthly costs."),
    ]))
    verdicts = judge.judge(llm, QUESTION, CONTRACT, GROUPS, CANDIDATES)
    assert verdicts == [
        GroupVerdict(group=0, accepted=False, why="It joins daily sales to monthly costs."),
        GroupVerdict(group=1, accepted=True, why="It rolls both facts up to the month first."),
    ]
    [(schema, _)] = llm.structured_invocations
    assert schema is Rulings


@pytest.mark.parametrize(("rulings", "accepted"), [
    ([], [True, True]),
    ([Ruling(answer="C", accepted=False)], [True, True]),
    ([Ruling(answer="b", accepted=False), Ruling(answer="B", accepted=True)], [False, True]),
    ([Ruling(answer="", accepted=False)], [True, True]),
])
def test_only_a_verdict_on_an_answer_shown_counts_and_silence_accepts(rulings, accepted):
    verdicts = judge.judge(ScriptedLLM(rulings=Rulings(rulings=rulings)), QUESTION, CONTRACT, GROUPS, CANDIDATES)
    assert [v.accepted for v in verdicts] == accepted
    assert all(v.why == judge.NO_VERDICT for v in verdicts if v.accepted and not rulings)


def test_a_judge_that_cannot_answer_raises_for_the_caller_to_record():
    with pytest.raises(ConnectionError):
        judge.judge(ScriptedLLM(rulings=ConnectionError("refused")), QUESTION, CONTRACT, GROUPS, CANDIDATES)
