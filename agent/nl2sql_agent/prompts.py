"""Prompt templates for the three LLM steps of the pipeline."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

TABLE_SELECTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You select which database tables are needed to answer a question.\n"
            "Return only tables that are genuinely required, including any needed "
            "purely to join two other tables together. Never invent a table name: "
            "every name you return must appear verbatim in the catalog.",
        ),
        (
            "human",
            "Database catalog:\n{catalog}\n\n"
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
            "- Apply a sensible LIMIT when the question implies a top-N or sample.",
        ),
        (
            "human",
            "Schema and sample data:\n{schema}\n\n"
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
            "not problems. If the query is correct, say so.",
        ),
        (
            "human",
            "Schema and sample data:\n{schema}\n\n"
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
