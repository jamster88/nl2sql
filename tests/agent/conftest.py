"""Fakes for the two external dependencies the agent pipeline has -- Postgres
(Database) and the model (a BaseChatModel from langchain_ollama) -- so the
graph, tools, and CLI can be unit-tested without a live Postgres or Ollama.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from nl2sql_agent.database import QueryResult
from nl2sql_agent.tools import SqlReview, TableSelection


class FakeDatabase:
    """Stands in for nl2sql_agent.database.Database. No network/socket use."""

    def __init__(
        self,
        *,
        tables: list[str] | None = None,
        dialect: str = "postgresql",
        explain_error: str | None = None,
        run_select_result: QueryResult | None = None,
        run_select_error: Exception | None = None,
    ) -> None:
        self._tables = tables if tables is not None else ["dim_store", "fact_pos_retail_sales"]
        self._dialect = dialect
        self.explain_error = explain_error
        self.run_select_result = run_select_result or QueryResult(columns=["n"], rows=[(1,)], truncated=False)
        self.run_select_error = run_select_error

        self.explain_calls: list[str] = []
        self.run_select_calls: list[str] = []
        self.schema_and_samples_calls: list[tuple[list[str], int]] = []

    @property
    def dialect(self) -> str:
        return self._dialect

    def table_names(self) -> list[str]:
        return list(self._tables)

    def describe_all_tables(self) -> str:
        return "\n".join(f"{t} -- fake table" for t in self._tables)

    def schema_and_samples(self, tables: list[str], sample_rows: int) -> str:
        self.schema_and_samples_calls.append((list(tables), sample_rows))
        return "\n".join(f"=== {t} ===\ncolumns: id" for t in tables)

    def explain(self, sql: str) -> str | None:
        self.explain_calls.append(sql)
        return self.explain_error

    def run_select(self, sql: str) -> QueryResult:
        self.run_select_calls.append(sql)
        if self.run_select_error is not None:
            raise self.run_select_error
        return self.run_select_result


class _StructuredBinding:
    """What llm.with_structured_output(Schema) returns: an object exposing
    just the .invoke() the graph/tools code actually calls.
    """

    def __init__(self, llm: "ScriptedLLM", schema: type) -> None:
        self._llm = llm
        self._schema = schema

    def invoke(self, messages: Any) -> Any:
        self._llm.structured_invocations.append((self._schema, messages))
        if self._schema is TableSelection:
            if self._llm.table_selection is None:
                raise AssertionError("with_structured_output(TableSelection) invoked but no response scripted")
            return self._llm.table_selection
        if self._schema is SqlReview:
            if not self._llm.sql_reviews:
                raise AssertionError("with_structured_output(SqlReview) invoked but no more responses scripted")
            return self._llm.sql_reviews.pop(0)
        raise AssertionError(f"unexpected structured_output schema: {self._schema}")


class ScriptedLLM:
    """Stands in for the ChatOllama model. Scripted, deterministic, no network.

    - `.invoke(...)` (plain chat, used for SQL generation) returns the next
      queued string from `sql_responses`, wrapped like a real AIMessage.
    - `.with_structured_output(TableSelection).invoke(...)` returns
      `table_selection`.
    - `.with_structured_output(SqlReview).invoke(...)` returns the next
      queued verdict from `sql_reviews`.
    """

    def __init__(
        self,
        *,
        sql_responses: list[str] | None = None,
        table_selection: TableSelection | None = None,
        sql_reviews: list[SqlReview] | None = None,
    ) -> None:
        self.sql_responses = list(sql_responses or [])
        self.table_selection = table_selection if table_selection is not None else TableSelection(tables=[])
        self.sql_reviews = list(sql_reviews or [])
        self.plain_invocations: list[Any] = []
        self.structured_invocations: list[tuple[type, Any]] = []

    def invoke(self, messages: Any) -> Any:
        self.plain_invocations.append(messages)
        if not self.sql_responses:
            raise AssertionError("llm.invoke() called but no more sql_responses scripted")
        return SimpleNamespace(content=self.sql_responses.pop(0))

    def with_structured_output(self, schema: type) -> _StructuredBinding:
        return _StructuredBinding(self, schema)


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def scripted_llm() -> ScriptedLLM:
    return ScriptedLLM()
