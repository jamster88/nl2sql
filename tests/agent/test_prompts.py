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
    SQL_VALIDATION_PROMPT,
    TABLE_SELECTION_PROMPT,
    example_messages,
    knowledge_block,
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


def test_sql_validation_prompt_renders_with_knowledge():
    text = render(
        SQL_VALIDATION_PROMPT.format_messages(
            dialect="postgresql",
            schema="=== dim_store ===",
            knowledge=knowledge_block("market share fans out per competitor"),
            question="q",
            sql="SELECT 1",
            engine_feedback="",
        )
    )
    assert "market share fans out per competitor" in text
    assert "SELECT 1" in text


@pytest.mark.parametrize(
    ("template", "kwargs"),
    [
        (TABLE_SELECTION_PROMPT, {"catalog": "c", "question": "q"}),
        (
            SQL_GENERATION_PROMPT,
            {
                "dialect": "postgresql",
                "schema": "s",
                "question": "q",
                "feedback": "",
                "examples": [],
            },
        ),
        (
            SQL_VALIDATION_PROMPT,
            {"dialect": "postgresql", "schema": "s", "question": "q", "sql": "SELECT 1", "engine_feedback": ""},
        ),
    ],
)
def test_every_template_renders_cleanly_with_no_knowledge(template, kwargs):
    """The with- and without-RAG paths share these templates, so an empty
    knowledge block must leave no orphan heading behind.
    """
    text = render(template.format_messages(knowledge=knowledge_block(""), **kwargs))
    assert "Knowledge base" not in text


def test_generation_prompt_tells_the_model_to_prefer_knowledge_over_assumptions():
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            dialect="postgresql", schema="s", knowledge="", examples=[],
            question="q", feedback="",
        )
    ).lower()
    assert "knowledge base" in text
    assert "assumptions" in text or "authoritative" in text


def test_validation_prompt_asks_for_knowledge_rule_violations():
    text = render(
        SQL_VALIDATION_PROMPT.format_messages(
            dialect="postgresql", schema="s", knowledge="", question="q",
            sql="SELECT 1", engine_feedback="",
        )
    ).lower()
    assert "double count" in text or "fan-out" in text


# ---------------------------------------------------------------------------
# Retry feedback (unchanged by v2, still part of the generation contract)
# ---------------------------------------------------------------------------


def test_retry_feedback_includes_previous_sql_and_issues():
    text = RETRY_FEEDBACK.format(sql="SELECT bad", issues="- wrong join key")
    assert "SELECT bad" in text
    assert "wrong join key" in text


def test_generation_prompt_carries_retry_feedback_and_knowledge_together():
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
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
        dialect="postgresql", schema="s", knowledge=knowledge_block(""),
        examples=[], question="q", feedback="",
    )
    assert [m.type for m in zero] == ["system", "human"]


def test_examples_are_replayed_before_the_real_question():
    messages = SQL_GENERATION_PROMPT.format_messages(
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
        dialect="postgresql", schema="=== dim_store ===", knowledge=knowledge_block(""),
        examples=example_messages(SHOTS), question="q", feedback="",
    )[0].content
    assert "=== dim_store ===" in system
    assert "worked examples" in system.lower()
    assert "the question actually asked" in system.lower()
