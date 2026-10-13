"""The Paraphraser (`paraphrase.py`; arch7 section 22.3): its prompt, and what
it hands the fidelity gate.

The prompt must carry the contract the question was read into and name what
may not change; the rewordings come back numbered, normalised, never twice,
and a model that writes nothing writes no rewordings rather than failing.
"""

from __future__ import annotations

from nl2sql_agent.contract import build_contract
from nl2sql_agent.paraphrase import Rewording, Rewordings, invariants, messages, paraphrase
from nl2sql_agent.state import AnswerContract, EntityRef


class _Model:
    def __init__(self, answer) -> None:
        self.answer = answer
        self.asked = []

    def with_structured_output(self, schema):
        assert schema is Rewordings
        return self

    def invoke(self, messages):
        self.asked.append(messages)
        return self.answer


RANKED = AnswerContract(
    entities=[EntityRef(word="sku", key="sku_id", label="product_name", table="dim_product")],
    measure="net sales", period="FY2025", ranked=True, limit=10,
)


def test_the_prompt_names_every_invariant_in_words_and_no_column():
    """The contract as the generator sees it names columns, and a Paraphraser
    shown them wrote them into its rewordings ("for every banner_name")."""
    system, human = messages("top 10 SKUs in FY2025", RANKED, count=10)
    assert "what the answer is about (sku)" in system.content
    assert "the quantity that answers or ranks it (net sales)" in system.content
    assert "the period (FY2025)" in system.content
    assert "the row count asked for (10)" in system.content
    assert "Keep every number, year, name and quoted value exactly as written" in system.content
    assert "keep any code or name the question itself uses, and add no column, table or field name" in system.content
    assert "Question: top 10 SKUs in FY2025" in human.content
    assert "product_name" not in system.content + human.content and "sku_id" not in system.content + human.content
    assert "Write 10 rewordings" in human.content and "These rewordings were rejected" not in human.content


def test_a_measure_the_question_names_is_not_put_in_the_supervisors_words():
    """B13: "prices lowest relative to us", read by the Supervisor as "average
    price difference" and given to the Paraphraser so, came back as
    rewordings asking for a difference. The measure is named only as the
    generator is told it."""
    named = AnswerContract(entities=[EntityRef(word="competitor")], measure="average price difference",
                           ranked=True)
    system, _ = messages("Which competitor prices lowest relative to us on average?", named, count=3)
    assert "price difference" not in system.content
    assert "(the figure the question asks for, in its own words)" in system.content


def test_a_contract_that_names_nothing_is_said_in_words_too():
    assert invariants(build_contract("how many stores are there?")) == {
        "entities": "the single figure it asks for",
        "measure": "the figure the question asks for, in its own words",
        "period": "none named",
        "limit": "none asked for",
    }


def test_the_rewordings_come_back_numbered_normalised_and_never_twice():
    model = _Model(Rewordings(rewordings=[
        Rewording(text="  Which ten   SKUs sold best in FY2025? ", changed=" verb,  order "),
        Rewording(text="", changed="nothing"),
        Rewording(text="which ten skus sold best in fy2025?", changed="the same again"),
        Rewording(text="Name the 10 best-selling SKUs of FY2025."),
    ]))
    written = paraphrase(model, "top 10 SKUs in FY2025", RANKED, count=10)
    assert [(p.index, p.text, p.changed, p.status) for p in written] == [
        (1, "Which ten SKUs sold best in FY2025?", "verb, order", "pending"),
        (2, "Name the 10 best-selling SKUs of FY2025.", "", "pending"),
    ]


def test_no_more_than_were_asked_for_and_numbered_on_from_where_the_last_left_off():
    model = _Model(Rewordings(rewordings=[Rewording(text=f"wording {n}") for n in range(5)]))
    written = paraphrase(model, "q", RANKED, count=2, start=4)
    assert [(p.index, p.text) for p in written] == [(4, "wording 0"), (5, "wording 1")]


def test_the_retry_names_each_failure_and_skips_what_was_written_before():
    model = _Model(Rewordings(rewordings=[Rewording(text="top 5 SKUs"), Rewording(text="the 10 best SKUs")]))
    written = paraphrase(
        model, "top 10 SKUs", RANKED, count=1,
        failed=[("top 5 SKUs", "F1 numbers: 10 missing; 5 added")], written=["top 5 SKUs"],
    )
    prompt = model.asked[0][-1].content
    assert "These rewordings were rejected:\n- top 5 SKUs -- because F1 numbers: 10 missing; 5 added" in prompt
    assert "Write replacements that avoid the same mistakes." in prompt
    assert [p.text for p in written] == ["the 10 best SKUs"]


def test_a_model_that_writes_nothing_writes_no_rewordings():
    assert paraphrase(_Model(None), "q", RANKED, count=3) == []
    assert paraphrase(_Model(Rewordings()), "q", RANKED, count=3) == []
