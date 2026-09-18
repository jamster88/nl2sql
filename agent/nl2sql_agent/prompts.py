"""Prompt templates for the three LLM steps of the pipeline.

Each one takes a `{knowledge}` block holding the chunks retrieved from the
vector store. It is rendered as an empty string when retrieval is off or
unavailable, so the same templates serve the with- and without-RAG paths.

Generation is **multi-shot**: the retrieved golden pairs are not pasted into the
prompt as a block of text, they are replayed as real conversation turns --
human asks a question, assistant answers with SQL, repeated -- before the actual
question is asked. That is the shape instruct models are tuned on, and it is
what makes the demonstration a demonstration rather than a quotation. A text
block invites the model to describe the examples; a turn sequence invites it to
continue the pattern.

The turns are symmetric with the real one on purpose. Every human turn is
[the rule that applies here] + [the question], and every assistant turn is bare
SQL and nothing else. For an exemplar the rule is its `reasoning_target`; for
the real question it is whatever the knowledge base returned. Anything the
assistant turns contain, the model will imitate -- which is why they carry no
commentary, and no leading SQL comment either: `ensure_read_only` requires the
statement to begin with SELECT or WITH, so a model that learned to prefix a
comment would have every query rejected.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

TABLE_SELECTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You select which database tables are needed to answer a question.\n"
            "Return only tables that are genuinely required, including any needed "
            "purely to join two other tables together. Never invent a table name: "
            "every name you return must appear verbatim in the catalog.\n"
            "Reference material retrieved from the project's knowledge base may "
            "name the relevant tables directly -- prefer those, but still verify "
            "each name against the catalog.",
        ),
        (
            "human",
            "Database catalog:\n{catalog}\n\n"
            "{knowledge}"
            "Question: {question}\n\n"
            "Which tables are required?",
        ),
    ]
)

SQL_GENERATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You write a single {dialect} SELECT query answering the user's question.\n"
            "Rules:\n"
            "- Use only the tables and columns shown in the schema below.\n"
            "- Join on the declared key relationships.\n"
            "- Return only the SQL, with no explanation and no markdown fence.\n"
            "- A single statement, read-only, no semicolon at the end.\n"
            "- Apply a sensible LIMIT when the question implies a top-N or sample.\n"
            "- The knowledge base section, when present, is authoritative about "
            "this database's business rules, grains, and traps: fiscal calendar "
            "semantics, which columns are pre-aggregated, and where a naive join "
            "would double count. Follow it over your own assumptions, and prefer "
            "any query recipe it gives for this kind of question.\n"
            "- Earlier turns in this conversation are worked examples: real "
            "questions about this same database, each answered with SQL verified "
            "to run. Follow the patterns they establish -- how they reconcile "
            "grains, de-duplicate, and resolve the fiscal calendar -- but answer "
            "the question actually asked, which is the last one.\n\n"
            "Schema and sample data:\n{schema}",
        ),
        # The worked examples, replayed as human/assistant turns. Empty when
        # multi-shot is off or nothing was retrieved, which leaves the prompt
        # byte-for-byte the zero-shot one.
        MessagesPlaceholder("examples"),
        (
            "human",
            "{knowledge}"
            "Question: {question}\n\n"
            "{feedback}"
            "SQL:",
        ),
    ]
)

SQL_VALIDATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You review a {dialect} query for correctness against a schema.\n"
            "Report a problem only when the query would fail or would answer the "
            "wrong question: an unknown table or column, a wrong join key, a "
            "misuse of aggregation, or a dialect error. Stylistic preferences are "
            "not problems. If the query is correct, say so.\n"
            "When the knowledge base section documents a rule this query breaks "
            "-- double counting a fan-out, treating a fiscal year as a calendar "
            "year, summing a pre-aggregated total -- report that as a problem and "
            "say which rule it breaks.",
        ),
        (
            "human",
            "Schema and sample data:\n{schema}\n\n"
            "{knowledge}"
            "Question: {question}\n\n"
            "Query:\n{sql}\n\n"
            "{engine_feedback}"
            "Is this query valid and does it answer the question?",
        ),
    ]
)

RETRY_FEEDBACK = (
    "Your previous attempt was rejected.\n"
    "Previous SQL:\n{sql}\n"
    "Problems found:\n{issues}\n"
    "Write a corrected query.\n\n"
)

KNOWLEDGE_BLOCK = (
    "Knowledge base (retrieved for this question -- authoritative on business "
    "rules, grains, and join traps):\n{knowledge}\n\n"
)


def knowledge_block(knowledge: str) -> str:
    """Wrap retrieved context for a prompt, or render nothing when there is none."""
    if not knowledge or not knowledge.strip():
        return ""
    return KNOWLEDGE_BLOCK.format(knowledge=knowledge.strip())


EXAMPLE_RULE_BLOCK = "Rule that applies here: {rule}\n\n"


def example_messages(shots: list[dict]) -> list:
    """Turn retrieved golden pairs into the exemplar turns of a multi-shot prompt.

    Each pair becomes one human turn and one assistant turn. The human turn is
    shaped exactly like the real question's turn -- the rule that applies, then
    the question -- so the model is shown the same task it is about to be given,
    not a differently-formatted cousin of it.

    A pair missing its question or its SQL is skipped rather than rendered half
    empty: an exemplar whose answer is blank teaches the model to answer blank.
    """
    messages: list = []
    for shot in shots:
        question = (shot.get("question") or "").strip()
        sql = (shot.get("sql_code") or "").strip()
        if not question or not sql:
            continue
        rule = (shot.get("reasoning_target") or "").strip()
        prefix = EXAMPLE_RULE_BLOCK.format(rule=rule) if rule else ""
        messages.append(HumanMessage(content=f"{prefix}Question: {question}\n\nSQL:"))
        messages.append(AIMessage(content=sql))
    return messages
