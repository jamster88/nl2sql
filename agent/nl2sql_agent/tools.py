"""The tools the pipeline calls: catalog and schema reads, and the two
retrievers.

v4 removed the `validate_sql` tool that lived here. It wrapped a model call
that reviewed generated SQL, and the benchmark showed it cost 499 of 1499
seconds and was the only component in 45 runs that ever turned a correct
answer into no answer at all. Validation is deterministic now -- an AST parse
in `validate.py` and a planner call in `database.py` -- and the semantic
judgement it was reaching for happens after execution, in `present.audit`,
where there are real rows to look at.

They are LangChain tools rather than plain functions so they can also be bound
to a tool-calling model later; the graph invokes them directly at fixed points.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .config import Settings
from .database import Database
from .examples import ExamplesUnavailableError, GoldenPairLibrary, format_examples
from .examples import tables_mentioned as example_tables
from .retrieval import KnowledgeBase, KnowledgeUnavailableError, format_chunks, tables_mentioned


class TableSelection(BaseModel):
    """Tables required to answer a question."""

    tables: list[str] = Field(description="Names of the required tables")


def build_tools(
    db: Database,
    llm: BaseChatModel,
    settings: Settings,
    knowledge_base: KnowledgeBase | None = None,
    example_library: GoldenPairLibrary | None = None,
) -> dict[str, BaseTool]:
    @tool
    def describe_all_tables() -> str:
        """List every table in the database with its description and columns."""
        return db.describe_all_tables()

    @tool
    def get_schema_and_data(tables: list[str]) -> str:
        """Get full column details, keys, and sample rows for the given tables."""
        return db.schema_and_samples(tables, settings.sample_rows)

    @tool
    def search_knowledge(question: str) -> dict[str, Any]:
        """Retrieve business rules, table docs, and query recipes for a question."""
        if knowledge_base is None:
            return {"context": "", "tables": [], "chunks": [], "error": "retrieval is disabled"}
        try:
            chunks = knowledge_base.search(question, settings.rag_top_k)
        except KnowledgeUnavailableError as exc:
            return {"context": "", "tables": [], "chunks": [], "error": str(exc)}
        return {
            "context": format_chunks(chunks, settings.rag_max_context_chars),
            "tables": tables_mentioned(chunks),
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "source_doc": c.source_doc,
                    "heading_path": c.heading_path,
                    "distance": round(c.distance, 4),
                }
                for c in chunks
            ],
            "error": None,
        }

    @tool
    def search_examples(question: str) -> dict[str, Any]:
        """Retrieve worked question/SQL pairs closest to this question."""
        empty = {"context": "", "tables": [], "pairs": [], "shots": [], "error": None}
        if example_library is None:
            return {**empty, "error": "examples are disabled"}
        try:
            pairs = example_library.search(question, settings.examples_top_k)
        except ExamplesUnavailableError as exc:
            return {**empty, "error": str(exc)}
        return {
            "context": format_examples(pairs, settings.examples_max_context_chars),
            "tables": example_tables(pairs),
            # Summaries for tracing and --json: small, and safe to log.
            "pairs": [
                {
                    "chunk_id": p.chunk_id,
                    "pair_id": p.pair_id,
                    "title": p.title,
                    "score": round(p.score, 5),
                    "rerank_score": round(p.rerank_score, 5),
                    "found_by": p.found_by,
                }
                for p in pairs
            ],
            # The full text the multi-shot turns are built from.
            "shots": [
                {
                    "pair_id": p.pair_id,
                    "question": p.question,
                    "reasoning_target": p.reasoning_target,
                    "sql_code": p.sql_code,
                }
                for p in pairs
            ],
            "error": None,
        }

    @tool
    def execute_query(sql: str) -> dict[str, Any]:
        """Run a read-only query and return its rows as structured output."""
        result = db.run_select(sql)
        return {
            "columns": result.columns,
            "rows": [list(row) for row in result.rows],
            "row_count": len(result.rows),
            "truncated": result.truncated,
        }

    return {
        "describe_all_tables": describe_all_tables,
        "get_schema_and_data": get_schema_and_data,
        "search_knowledge": search_knowledge,
        "search_examples": search_examples,
        "execute_query": execute_query,
    }
