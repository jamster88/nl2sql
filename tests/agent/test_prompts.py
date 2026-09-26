"""Prompt templates and the blocks that v2 and v3 thread through them.

These are the seam between retrieval and the model: if a template stops
accepting `{knowledge}` or `{examples}`, or a block renders when there is
nothing to say, the failure shows up as a confusing model answer rather than an
exception -- so it gets pinned here.
"""

from __future__ import annotations

import pytest
from nl2sql_agent.prompts import (
    RETRY_FEEDBACK,
    SQL_GENERATION_PROMPT,
    SUPERVISOR_PROMPT,
    TABLE_SELECTION_PROMPT,
    contract_block,
    example_messages,
    knowledge_block,
    literal_block,
    task_block,
)


def render(messages) -> str:
    return "\n".join(getattr(m, "content", str(m)) for m in messages)


# ---------------------------------------------------------------------------
# knowledge_block
# ---------------------------------------------------------------------------


def test_knowledge_block_wraps_content_with_a_labelled_header():
    block = knowledge_block("Never sum a pre-aggregated total.")
    assert "Knowledge base" in block
    assert "Never sum a pre-aggregated total." in block


def test_knowledge_block_marks_the_context_authoritative():
    # The model has to prefer retrieved rules over its own priors, so the
    # framing is load-bearing, not decoration.
    block = knowledge_block("some rule").lower()
    assert "authoritative" in block


@pytest.mark.parametrize("empty", ["", "   ", "\n\n", "\t"])
def test_knowledge_block_renders_nothing_when_there_is_no_context(empty):
    # No dangling "Knowledge base:" heading above an empty section when
    # retrieval is off or unavailable.
    assert knowledge_block(empty) == ""


def test_knowledge_block_strips_surrounding_whitespace():
    assert "\n\n\nrule" not in knowledge_block("\n\n\nrule\n\n\n")


# ---------------------------------------------------------------------------
# Every template must accept the knowledge variable
# ---------------------------------------------------------------------------


def test_table_selection_prompt_renders_with_knowledge():
    text = render(
        TABLE_SELECTION_PROMPT.format_messages(
            catalog="dim_store -- stores",
            knowledge=knowledge_block("dim_store is one row per store"),
            question="how many stores",
        )
    )
    assert "dim_store is one row per store" in text
    assert "how many stores" in text


def test_sql_generation_prompt_renders_with_knowledge():
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            literals="",
            task="",
            dialect="postgresql",
            schema="=== dim_store ===",
            knowledge=knowledge_block("fiscal year 2024 starts 2023-04-01"),
            examples=[],
            question="q",
            feedback="",
        )
    )
    assert "fiscal year 2024 starts 2023-04-01" in text
    assert "postgresql" in text


def test_generation_prompt_tells_the_model_to_prefer_knowledge_over_assumptions():
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            literals="",
            task="",
            dialect="postgresql", schema="s", knowledge="", examples=[],
            question="q", feedback="",
        )
    ).lower()
    assert "knowledge base" in text
    assert "assumptions" in text or "authoritative" in text


def test_retry_feedback_includes_previous_sql_and_issues():
    text = RETRY_FEEDBACK.format(sql="SELECT bad", issues="- wrong join key")
    assert "SELECT bad" in text
    assert "wrong join key" in text


def test_generation_prompt_carries_retry_feedback_and_knowledge_together():
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            literals="",
            task="",
            dialect="postgresql",
            schema="s",
            knowledge=knowledge_block("the rule"),
            examples=[],
            question="q",
            feedback=RETRY_FEEDBACK.format(sql="SELECT bad", issues="- broke the rule"),
        )
    )
    assert "the rule" in text
    assert "SELECT bad" in text
    assert "broke the rule" in text


# ---------------------------------------------------------------------------
# Multi-shot: the worked examples as conversation turns
# ---------------------------------------------------------------------------

SHOTS = [
    {
        "pair_id": "Q01",
        "question": "Gross profit for Produce in fiscal month 12 of FY2025?",
        "reasoning_target": "Reconciling daily sales against monthly costs.",
        "sql_code": "WITH s AS (SELECT 1)\nSELECT * FROM s",
    },
    {
        "pair_id": "Q10",
        "question": "Weekly market share for Cheese in the Pacific Northwest?",
        "reasoning_target": "The five-row fan-out.",
        "sql_code": "SELECT DISTINCT week_key FROM fact_market_share_weekly",
    },
]


def test_each_example_becomes_a_human_turn_and_an_assistant_turn():
    """The shape is the whole point: a model continues a demonstrated pattern
    more reliably than it follows a described one.
    """
    messages = example_messages(SHOTS)
    assert [m.type for m in messages] == ["human", "ai", "human", "ai"]
    assert "Gross profit for Produce" in messages[0].content
    assert messages[1].content == "WITH s AS (SELECT 1)\nSELECT * FROM s"


def test_the_assistant_turns_are_bare_sql():
    """Whatever the assistant turns contain, the model imitates. A leading SQL
    comment would be imitated too -- and `ensure_read_only` rejects anything not
    starting with SELECT or WITH, so every generated query would then fail.
    """
    for message in example_messages(SHOTS):
        if message.type != "ai":
            continue
        assert message.content.upper().startswith(("SELECT", "WITH"))
        assert "--" not in message.content
        assert "```" not in message.content


def test_an_example_turn_carries_the_rule_that_applies_to_it():
    human = example_messages(SHOTS)[0].content
    assert "Reconciling daily sales against monthly costs." in human
    assert human.index("Rule that applies here") < human.index("Question:")


def test_example_turns_mirror_the_real_question_turn():
    """Both are [the rule that applies] then [the question]. An exemplar shaped
    differently from the real task demonstrates the wrong task.
    """
    exemplar = example_messages(SHOTS)[0].content
    real = render(
        SQL_GENERATION_PROMPT.format_messages(
            literals="",
            task="",
            dialect="postgresql", schema="s",
            knowledge=knowledge_block("FY2024 starts 2023-04-01"),
            examples=[], question="How many stores?", feedback="",
        )
    )
    for text in (exemplar, real):
        assert "Question:" in text
        assert text.rstrip().endswith("SQL:")


def test_a_pair_missing_its_question_or_sql_is_skipped():
    """A half-rendered exemplar teaches the model to answer with nothing."""
    assert example_messages([{"question": "q", "sql_code": ""}]) == []
    assert example_messages([{"question": "", "sql_code": "SELECT 1"}]) == []
    assert example_messages([{"question": "q", "sql_code": "SELECT 1"}]) != []


def test_an_example_with_no_rule_still_renders_a_clean_turn():
    [human, _] = example_messages([{"question": "q", "sql_code": "SELECT 1"}])
    assert "Rule that applies here" not in human.content
    assert human.content.startswith("Question: q")


def test_no_examples_leaves_the_zero_shot_prompt_untouched():
    """Multi-shot is a switch, and off it has to cost exactly nothing -- same
    messages, same order, as if the feature were not there.
    """
    zero = SQL_GENERATION_PROMPT.format_messages(
            literals="",
            task="",
        dialect="postgresql", schema="s", knowledge=knowledge_block(""),
        examples=[], question="q", feedback="",
    )
    assert [m.type for m in zero] == ["system", "human"]


def test_examples_are_replayed_before_the_real_question():
    messages = SQL_GENERATION_PROMPT.format_messages(
            literals="",
            task="",
        dialect="postgresql", schema="s", knowledge=knowledge_block(""),
        examples=example_messages(SHOTS), question="the real one", feedback="",
    )
    assert [m.type for m in messages] == ["system", "human", "ai", "human", "ai", "human"]
    assert "the real one" in messages[-1].content
    assert "the real one" not in "".join(str(m.content) for m in messages[:-1])


def test_the_system_turn_carries_the_schema_and_names_the_examples():
    """The schema is shared by every turn, so it belongs once in the system
    message rather than repeated per exemplar -- and the model is told what the
    earlier turns are, or it may read them as prior user requests to revisit.
    """
    system = SQL_GENERATION_PROMPT.format_messages(
            literals="",
            task="",
        dialect="postgresql", schema="=== dim_store ===", knowledge=knowledge_block(""),
        examples=example_messages(SHOTS), question="q", feedback="",
    )[0].content
    assert "=== dim_store ===" in system
    assert "worked examples" in system.lower()
    assert "the question actually asked" in system.lower()


# ---------------------------------------------------------------------------
# The two blocks v4 adds to the generator's human turn
# ---------------------------------------------------------------------------


def test_the_literal_block_renders_nothing_when_no_literal_matched():
    """Same contract as the knowledge block: an empty retrieval must leave no
    orphan heading, or the model is shown a promise with nothing behind it.
    """
    assert literal_block("") == ""
    assert literal_block("   ") == ""


def test_the_literal_block_names_the_column_a_phrase_resolves_to():
    rendered = literal_block('"dairy and eggs" -> dim_product.department_name = \'Dairy & Eggs\'')
    assert "Literal values in this database" in rendered
    assert "dim_product.department_name = 'Dairy & Eggs'" in rendered


def test_the_task_block_renders_nothing_without_an_intent():
    assert task_block("") == ""


def test_the_task_block_carries_the_supervisors_framing():
    assert task_block("This is a comparison; return both sides.") == (
        "Task: This is a comparison; return both sides.\n\n"
    )


def test_the_generator_turn_carries_literals_and_task_alongside_the_question():
    """All three blocks land in the same human turn, in a fixed order, so the
    model reads the rules, then the values, then the task, then the question.
    """
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            dialect="postgresql",
            schema="s",
            knowledge=knowledge_block("a rule"),
            literals=literal_block('"x" -> t.c = \'X\''),
            task=task_block("This is a trend."),
            examples=[],
            question="how did sales trend?",
            feedback="",
        )
    )
    assert text.index("Knowledge base") < text.index("Literal values")
    assert text.index("Literal values") < text.index("Task: This is a trend.")
    assert text.index("Task: This is a trend.") < text.index("how did sales trend?")


def test_the_supervisor_prompt_states_the_scope_it_screens_against():
    """An out-of-domain refusal is only fair if the screener was told what the
    domain is; otherwise it is guessing at the boundary it enforces.
    """
    text = render(
        SUPERVISOR_PROMPT.format_messages(domain="retail sales for FY2024-FY2025", question="q")
    )
    assert "retail sales for FY2024-FY2025" in text
    assert "injection" in text
    assert "ambiguous" in text


# ---------------------------------------------------------------------------
# The answer contract line (arch5 section 5.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty", ["", "   "])
def test_the_contract_block_renders_nothing_when_there_is_nothing_to_add(empty):
    assert contract_block(empty) == ""


def test_the_contract_block_says_what_a_complete_answer_includes():
    assert contract_block(" `product_name` beside any `sku_id`. ") == (
        "A complete answer includes: `product_name` beside any `sku_id`.\n\n"
    )


def test_the_contract_line_sits_after_the_task_and_before_the_question():
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            dialect="postgresql",
            schema="s",
            knowledge="",
            literals="",
            task=task_block("This is an aggregate."),
            contract=contract_block("the net sales the rows are ranked by, as a column."),
            examples=[],
            question="top 10 SKUs",
            feedback="",
        )
    )
    assert text.index("Task:") < text.index("A complete answer includes:") < text.index("top 10 SKUs")


def test_a_caller_that_predates_the_contract_renders_the_arch4_prompt():
    """The template defaults the contract to nothing, so leaving it out is
    byte-for-byte the prompt arch4 sent."""
    common = dict(dialect="postgresql", schema="s", knowledge="", literals="", task="",
                  examples=[], question="q", feedback="")
    assert render(SQL_GENERATION_PROMPT.format_messages(**common)) == render(
        SQL_GENERATION_PROMPT.format_messages(contract="", **common)
    )


def test_the_supervisor_prompt_asks_for_the_contracts_three_fields():
    text = render(SUPERVISOR_PROMPT.format_messages(domain="d", question="q")).lower()
    assert "entities" in text and "measure" in text and "period" in text
    assert "write none" in text
