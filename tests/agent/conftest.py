"""Fakes for the two external dependencies the agent pipeline has -- Postgres
(Database) and the model (a BaseChatModel from langchain_ollama) -- so the
graph, tools, and CLI can be unit-tested without a live Postgres or Ollama.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from nl2sql_agent.completeness import Reflection
from nl2sql_agent.database import QueryResult
from nl2sql_agent.examples import ExamplesUnavailableError, GoldenPair
from nl2sql_agent.retrieval import KnowledgeUnavailableError, RetrievedChunk
from nl2sql_agent.supervisor import Screening
from nl2sql_agent.tools import TableSelection


class FakeDatabase:
    """Stands in for nl2sql_agent.database.Database. No network/socket use."""

    def __init__(
        self,
        *,
        tables: list[str] | None = None,
        dialect: str = "postgresql",
        explain_error: str | None = None,
        plan_cost: float | None = 100.0,
        run_select_result: QueryResult | None = None,
        run_select_error: Exception | None = None,
    ) -> None:
        self._tables = tables if tables is not None else ["dim_store", "fact_pos_retail_sales"]
        self._dialect = dialect
        self.explain_error = explain_error
        # The v4 Planner Gate reads a cost as well as an error, and a list
        # lets a test script a different plan per attempt so the repair loop
        # can be driven to a success.
        self.plan_cost = plan_cost
        self.run_select_result = run_select_result or QueryResult(columns=["n"], rows=[(1,)], truncated=False)
        self.run_select_error = run_select_error
        # arch5: what the answer contract reads once per process.
        self.tables_detail: list = []
        self.fiscal_year = None
        self.catalog_calls = 0
        # A rendered schema block, for tests whose agents read column names
        # out of it (the Completeness Reviewer's reflection does).
        self.schema_text: str | None = None

        self.explain_calls: list[str] = []
        self.run_select_calls: list[str] = []
        self.run_select_principals: list[str | None] = []
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
        if self.schema_text is not None:
            return self.schema_text
        return "\n".join(f"=== {t} ===\ncolumns: id" for t in tables)

    def explain(self, sql: str) -> str | None:
        self.explain_calls.append(sql)
        return self.explain_error

    def explain_plan(self, sql: str) -> tuple[float | None, str | None]:
        """The v4 Planner Gate: (estimated cost, error message)."""
        self.explain_calls.append(sql)
        error = self._next(self.explain_error)
        if error:
            return None, error
        return self._next(self.plan_cost), None

    @staticmethod
    def _next(value):
        """Pop the next scripted value, or reuse a scalar for every call."""
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value

    def catalog(self) -> list:
        """No key constraints, so an empty label map, unless a test scripts one."""
        self.catalog_calls += 1
        return list(self.tables_detail)

    def latest_complete_fiscal_year(self):
        """No calendar, so no default period, unless a test scripts one."""
        return self.fiscal_year

    def run_select(self, sql: str, *, principal: str | None = None) -> QueryResult:
        self.run_select_principals.append(principal)
        self.run_select_calls.append(sql)
        if self.run_select_error is not None:
            raise self.run_select_error
        # A list scripts one result per execution, so a test can show the
        # Completeness Reviewer a bare result and then the complete one.
        if isinstance(self.run_select_result, list):
            return self.run_select_result.pop(0)
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
        if self._schema is Screening:
            return self._llm.screening or Screening(verdict="proceed", intent="aggregate")
        if self._schema is Reflection:
            # A list scripts one verdict per call; the default finds the
            # result complete, which is the happy path.
            reflection = self._llm.reflection
            if isinstance(reflection, Exception):
                raise reflection
            if isinstance(reflection, list):
                return reflection.pop(0)
            return reflection if reflection is not None else Reflection(complete=True)
        # The narrator's output model is matched structurally rather than by
        # import, so this fake does not depend on the internal naming of the
        # module it stands in front of.
        if "claims" in getattr(self._schema, "model_fields", {}):
            narration = self._llm.narration
            if narration is None:
                raise AssertionError("the narrator was invoked but no narration was scripted")
            # A list scripts one narration per call, so the audit's single
            # rewrite can be driven; a bare object answers every call.
            if isinstance(narration, list):
                if not narration:
                    raise AssertionError("the narrator was invoked but no more narrations are scripted")
                return narration.pop(0)
            return narration
        raise AssertionError(f"unexpected structured_output schema: {self._schema}")


class ScriptedLLM:
    """Stands in for the ChatOllama model. Scripted, deterministic, no network.

    - `.invoke(...)` (plain chat, used for SQL generation) returns the next
      queued string from `sql_responses`, wrapped like a real AIMessage.
    - `.with_structured_output(TableSelection).invoke(...)` returns
      `table_selection`.
    - `.with_structured_output(Screening).invoke(...)` returns `screening`.
    - The narrator's claims model returns `narration`.
    - The Completeness Reviewer's reflection returns `reflection`, or
      "complete" when none is scripted.
    """

    def __init__(
        self,
        *,
        sql_responses: list[str] | None = None,
        table_selection: TableSelection | None = None,
        screening: Any = None,
        narration: Any = None,
        reflection: Any = None,
    ) -> None:
        self.sql_responses = list(sql_responses or [])
        self.table_selection = table_selection if table_selection is not None else TableSelection(tables=[])
        self.screening = screening
        self.narration = narration
        self.reflection = reflection
        self.plain_invocations: list[Any] = []
        self.structured_invocations: list[tuple[type, Any]] = []

    def invoke(self, messages: Any) -> Any:
        self.plain_invocations.append(messages)
        if not self.sql_responses:
            raise AssertionError("llm.invoke() called but no more sql_responses scripted")
        return SimpleNamespace(content=self.sql_responses.pop(0))

    def with_structured_output(self, schema: type) -> _StructuredBinding:
        return _StructuredBinding(self, schema)


class FakeKnowledgeBase:
    """Stands in for nl2sql_agent.retrieval.KnowledgeBase.

    Set `error` to simulate an unreachable vector store or embedding model,
    which the pipeline is supposed to survive.
    """

    def __init__(
        self,
        chunks: list[RetrievedChunk] | None = None,
        error: str | None = None,
    ) -> None:
        self._chunks = chunks if chunks is not None else [make_chunk()]
        self.error = error
        self.search_calls: list[tuple[str, int | None]] = []

    def search(self, question: str, top_k: int | None = None) -> list[RetrievedChunk]:
        self.search_calls.append((question, top_k))
        if self.error is not None:
            raise KnowledgeUnavailableError(self.error)
        return list(self._chunks)


class FakeGoldenPairLibrary:
    """Stands in for nl2sql_agent.examples.GoldenPairLibrary.

    Set `error` to simulate an unreachable context store, an unreachable vector
    store, or a missing embedding model -- all three of which the pipeline is
    supposed to survive with examples simply absent.
    """

    def __init__(
        self,
        pairs: list[GoldenPair] | None = None,
        error: str | None = None,
    ) -> None:
        self._pairs = pairs if pairs is not None else [make_pair()]
        self.error = error
        self.search_calls: list[tuple[str, int | None]] = []

    def search(self, question: str, top_k: int | None = None) -> list[GoldenPair]:
        self.search_calls.append((question, top_k))
        if self.error is not None:
            raise ExamplesUnavailableError(self.error)
        return list(self._pairs)


def make_pair(
    *,
    chunk_id: str = "eval:q10",
    pair_id: str = "Q10",
    title: str = "Weekly market share trend for a category in one region",
    suite: str = "Suite 10 - Syndicated market share",
    type: str = "golden pair",
    tables: str = "fact_market_share_weekly, dim_geography, dim_product, dim_date",
    keywords: str = "market share, fan-out, de-duplicate, region, weekly trend",
    question: str = "Show our weekly market share for Cheese in the Pacific Northwest.",
    reasoning_target: str = "The five-row fan-out: the totals repeat once per competitor.",
    sql_code: str = "SELECT DISTINCT week_key, grocer_sales_amount FROM fact_market_share_weekly",
    result: str = "13 rows: one per fiscal week.",
    score: float = 0.87,
    ranks: dict[str, int] | None = None,
) -> GoldenPair:
    return GoldenPair(
        chunk_id=chunk_id,
        pair_id=pair_id,
        title=title,
        suite=suite,
        type=type,
        tables=tables,
        keywords=keywords,
        question=question,
        reasoning_target=reasoning_target,
        sql_code=sql_code,
        result=result,
        score=score,
        ranks=ranks if ranks is not None else {"question": 1, "keywords": 2, "reasoning": 3},
    )


def make_chunk(
    *,
    collection: str = "business_index_embeddings",
    chunk_id: str = "business_index:abc123",
    source_doc: str = "business_index",
    heading_path: str = "Business Index > Market share fan-out: the five-row trap",
    content: str = "Each (week, product, region) cell repeats the same totals once per competitor.",
    meta: dict | None = None,
    distance: float = 0.25,
) -> RetrievedChunk:
    return RetrievedChunk(
        collection=collection,
        chunk_id=chunk_id,
        source_doc=source_doc,
        heading_path=heading_path,
        content=content,
        meta=meta if meta is not None else {"table": "fact_market_share_weekly"},
        distance=distance,
    )


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def scripted_llm() -> ScriptedLLM:
    return ScriptedLLM()
