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
    examples_block,
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
            examples=examples_block(""),
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
                "examples": examples_block(""),
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
            dialect="postgresql", schema="s", knowledge="", examples="",
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
            examples=examples_block(""),
            question="q",
            feedback=RETRY_FEEDBACK.format(sql="SELECT bad", issues="- broke the rule"),
        )
    )
    assert "the rule" in text
    assert "SELECT bad" in text
    assert "broke the rule" in text


# ---------------------------------------------------------------------------
# The worked-examples block (v3)
# ---------------------------------------------------------------------------


def test_generation_prompt_renders_retrieved_examples():
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            dialect="postgresql",
            schema="=== dim_store ===",
            knowledge=knowledge_block(""),
            examples=examples_block("Question: how many stores\nSQL:\nSELECT count(*)"),
            question="q",
            feedback="",
        )
    )
    assert "Worked examples" in text
    assert "SELECT count(*)" in text


def test_an_empty_examples_block_leaves_no_orphan_heading():
    """Multi-shot is a switch, so the same template renders both ways. An empty
    block must vanish entirely rather than leave a heading with nothing under it.
    """
    assert examples_block("") == ""
    assert examples_block("   \n  ") == ""
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            dialect="postgresql",
            schema="s",
            knowledge=knowledge_block(""),
            examples=examples_block(""),
            question="q",
            feedback="",
        )
    )
    assert "Worked examples" not in text


def test_knowledge_and_examples_are_separate_blocks_in_one_prompt():
    """They answer different questions -- rules versus worked patterns -- and
    the model is told so. Collapsing them into one block would lose that.
    """
    text = render(
        SQL_GENERATION_PROMPT.format_messages(
            dialect="postgresql",
            schema="s",
            knowledge=knowledge_block("fiscal year 2024 starts 2023-04-01"),
            examples=examples_block("Question: margin\nSQL:\nSELECT 1"),
            question="q",
            feedback="",
        )
    )
    assert "Knowledge base" in text
    assert "Worked examples" in text
    assert text.index("Knowledge base") < text.index("Worked examples")
