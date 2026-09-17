"""Prompt templates for the three LLM steps of the pipeline.

Each one takes a `{knowledge}` block holding the chunks retrieved from the
vector store. It is rendered as an empty string when retrieval is off or
unavailable, so the same templates serve the with- and without-RAG paths.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

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
            "any query recipe it gives for this kind of question.",
        ),
        (
            "human",
            "Schema and sample data:\n{schema}\n\n"
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
