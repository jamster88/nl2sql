"""The agent pipeline, as a LangGraph state graph.

v2 adds retrieval in front of the v1 pipeline: the question is embedded and
matched against the pgvector knowledge base, and the chunks that come back
feed table selection, SQL generation, and validation.

v3 adds a second, independent retrieval step over the golden question/SQL
pairs -- worked examples rather than prose -- using the ensemble in
`examples.py`. It runs after knowledge retrieval and before table selection, so
the tables its examples query can reinforce the selection the same way the
knowledge chunks already do.

    retrieve_knowledge -> retrieve_examples -> select_tables -> fetch_schema -> generate_sql -> validate_sql -> execute_query
                                                                                     ^               |
                                                                                     +---- retry ----+

Both retrieval steps are best-effort. If a store or the embedding model is
unreachable the node records why and the run continues without it -- without
examples, then without knowledge, and at the limit exactly the v1 behavior.

Whether the retrieved examples are *shown* to the generator is a separate
switch (`MULTI_SHOT_ENABLED`). Retrieving them always, and prompting with them
only on request, is what makes the multi-shot step a prompt change rather than
a plumbing change.
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from .config import Settings
from .database import Database, strip_sql
from .examples import BY_KEYWORDS, BY_QUESTION, BY_REASONING, GoldenPairLibrary
from .llm import build_llm
from .prompts import (
    RETRY_FEEDBACK,
    SQL_GENERATION_PROMPT,
    TABLE_SELECTION_PROMPT,
    examples_block,
    knowledge_block,
)
from .retrieval import KnowledgeBase, build_embedder
from .tools import TableSelection, build_tools

ProgressFn = Callable[[str, str], None]


class AgentState(TypedDict, total=False):
    question: str
    knowledge: str
    knowledge_tables: list[str]
    knowledge_chunks: list[dict[str, Any]]
    retrieval_error: str
    examples: str
    example_tables: list[str]
    example_pairs: list[dict[str, Any]]
    examples_error: str
    selected_tables: list[str]
    schema: str
    sql: str
    issues: list[str]
    attempts: int
    result: dict[str, Any]
    error: str


class Nl2SqlAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: BaseChatModel | None = None,
        knowledge_base: KnowledgeBase | None = None,
        example_library: GoldenPairLibrary | None = None,
        on_progress: ProgressFn | None = None,
    ) -> None:
        self.settings = settings
        self.db = Database(
            settings.database_url,
            db_schema=settings.db_schema,
            statement_timeout_ms=settings.statement_timeout_ms,
            max_rows=settings.max_rows,
        )
        self.llm = llm or build_llm(settings)
        self.knowledge_base = knowledge_base or self._build_knowledge_base(settings)
        self.example_library = example_library or self._build_example_library(settings)
        self.tools = build_tools(
            self.db, self.llm, settings, self.knowledge_base, self.example_library
        )
        self._on_progress = on_progress or (lambda step, detail: None)
        self._graph = self._build_graph()

    @staticmethod
    def _build_knowledge_base(settings: Settings) -> KnowledgeBase | None:
        """Construct the knowledge base, or None when retrieval is off.

        Construction is lazy (no connection, no embedding call), so an
        unreachable vector store surfaces at search time as a degraded run
        rather than as a failure to start.
        """
        if not settings.rag_enabled:
            return None
        return KnowledgeBase(
            settings.vector_db_url,
            build_embedder(settings),
            top_k=settings.rag_top_k,
        )

    @staticmethod
    def _build_example_library(settings: Settings) -> GoldenPairLibrary | None:
        """Construct the golden-pair ensemble, or None when examples are off.

        Lazy for the same reason the knowledge base is: neither store is
        contacted until a question is asked, so a store that is down degrades
        one run rather than preventing the agent from starting.
        """
        if not settings.examples_enabled:
            return None
        return GoldenPairLibrary(
            settings.context_db_url,
            settings.vector_db_url,
            build_embedder(settings),
            top_k=settings.examples_top_k,
            candidate_k=settings.examples_candidate_k,
            weights={
                BY_QUESTION: settings.example_weight_question,
                BY_KEYWORDS: settings.example_weight_keywords,
                BY_REASONING: settings.example_weight_reasoning,
            },
            fusion=settings.examples_fusion,
            rrf_k=settings.examples_rrf_k,
        )

    def run(self, question: str) -> AgentState:
        return self._graph.invoke({"question": question, "attempts": 0})

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("retrieve_knowledge", self._retrieve_knowledge)
        graph.add_node("retrieve_examples", self._retrieve_examples)
        graph.add_node("select_tables", self._select_tables)
        graph.add_node("fetch_schema", self._fetch_schema)
        graph.add_node("generate_sql", self._generate_sql)
        graph.add_node("validate_sql", self._validate_sql)
        graph.add_node("execute_query", self._execute_query)
        graph.add_node("give_up", self._give_up)

        graph.add_edge(START, "retrieve_knowledge")
        graph.add_edge("retrieve_knowledge", "retrieve_examples")
        graph.add_edge("retrieve_examples", "select_tables")
        graph.add_edge("select_tables", "fetch_schema")
        graph.add_edge("fetch_schema", "generate_sql")
        graph.add_edge("generate_sql", "validate_sql")
        graph.add_conditional_edges(
            "validate_sql",
            self._route_after_validation,
            {"retry": "generate_sql", "execute": "execute_query", "give_up": "give_up"},
        )
        graph.add_edge("execute_query", END)
        graph.add_edge("give_up", END)
        return graph.compile()

    # --- Step 1: retrieve business rules / table docs for this question -----
    def _retrieve_knowledge(self, state: AgentState) -> AgentState:
        retrieved = self.tools["search_knowledge"].invoke({"question": state["question"]})
        if retrieved["error"]:
            self._on_progress("retrieve_knowledge", f"skipped: {retrieved['error']}")
            return {"knowledge": "", "knowledge_tables": [], "knowledge_chunks": [], "retrieval_error": retrieved["error"]}

        chunks = retrieved["chunks"]
        summary = ", ".join(f"{c['source_doc']}:{c['heading_path'].split('>')[-1].strip()}" for c in chunks[:4])
        self._on_progress("retrieve_knowledge", f"{len(chunks)} chunk(s) -- {summary}")
        return {
            "knowledge": retrieved["context"],
            "knowledge_tables": retrieved["tables"],
            "knowledge_chunks": chunks,
        }

    # --- Step 1b: retrieve worked question/SQL examples for this question ---
    def _retrieve_examples(self, state: AgentState) -> AgentState:
        retrieved = self.tools["search_examples"].invoke({"question": state["question"]})
        if retrieved["error"]:
            self._on_progress("retrieve_examples", f"skipped: {retrieved['error']}")
            return {
                "examples": "",
                "example_tables": [],
                "example_pairs": [],
                "examples_error": retrieved["error"],
            }

        pairs = retrieved["pairs"]
        summary = ", ".join(f"{p['pair_id']} ({p['score']:.3f})" for p in pairs)
        self._on_progress("retrieve_examples", f"{len(pairs)} pair(s) -- {summary}")
        return {
            "examples": retrieved["context"],
            "example_tables": retrieved["tables"],
            "example_pairs": pairs,
        }

    # --- Step 2: describe all tables, ask the model which ones matter --------
    def _select_tables(self, state: AgentState) -> AgentState:
        catalog = self.tools["describe_all_tables"].invoke({})
        selection = self.llm.with_structured_output(TableSelection).invoke(
            TABLE_SELECTION_PROMPT.format_messages(
                catalog=catalog,
                knowledge=knowledge_block(state.get("knowledge", "")),
                question=state["question"],
            )
        )
        known = set(self.db.table_names())
        tables = [t for t in selection.tables if t in known]

        # Tables the retrieved chunks explicitly document are a strong signal,
        # as are the tables a closely-matching worked example actually queries;
        # add any the model missed rather than replacing its choice.
        for hinted in list(state.get("knowledge_tables", [])) + list(
            state.get("example_tables", [])
        ):
            if hinted in known and hinted not in tables:
                tables.append(hinted)

        if not tables:
            tables = sorted(known)
        self._on_progress("select_tables", ", ".join(tables))
        return {"selected_tables": tables}

    # --- Step 3a: pull schema and sample rows for those tables --------------
    def _fetch_schema(self, state: AgentState) -> AgentState:
        schema = self.tools["get_schema_and_data"].invoke(
            {"tables": state["selected_tables"]}
        )
        self._on_progress("fetch_schema", f"{len(schema):,} characters of context")
        return {"schema": schema}

    # --- Step 3b: generate SQL (re-entered on validation failure) -----------
    def _generate_sql(self, state: AgentState) -> AgentState:
        feedback = ""
        if state.get("issues"):
            feedback = RETRY_FEEDBACK.format(
                sql=state.get("sql", ""),
                issues="\n".join(f"- {issue}" for issue in state["issues"]),
            )
        # Retrieval and prompting are separate switches: the examples are
        # fetched either way, and shown only when multi-shot is on.
        shots = state.get("examples", "") if self.settings.multi_shot_enabled else ""
        response = self.llm.invoke(
            SQL_GENERATION_PROMPT.format_messages(
                dialect=self.db.dialect,
                schema=state["schema"],
                knowledge=knowledge_block(state.get("knowledge", "")),
                examples=examples_block(shots),
                question=state["question"],
                feedback=feedback,
            )
        )
        sql = strip_sql(str(response.content))
        self._on_progress("generate_sql", sql)
        return {"sql": sql}

    # --- Step 4: validate, and feed problems back into generation -----------
    def _validate_sql(self, state: AgentState) -> AgentState:
        verdict = self.tools["validate_sql"].invoke(
            {
                "sql": state["sql"],
                "question": state["question"],
                "schema_context": state["schema"],
                "knowledge": state.get("knowledge", ""),
            }
        )
        attempts = state.get("attempts", 0) + 1
        issues = [] if verdict["is_valid"] else verdict["issues"] or ["Query rejected."]
        self._on_progress(
            "validate_sql",
            "valid" if verdict["is_valid"] else "; ".join(issues),
        )
        return {"issues": issues, "attempts": attempts}

    def _route_after_validation(self, state: AgentState) -> str:
        if not state["issues"]:
            return "execute"
        if state["attempts"] < self.settings.max_sql_attempts:
            return "retry"
        return "give_up"

    # --- Step 5: execute and return structured output -----------------------
    def _execute_query(self, state: AgentState) -> AgentState:
        try:
            result = self.tools["execute_query"].invoke({"sql": state["sql"]})
        except Exception as exc:
            message = str(getattr(exc, "orig", exc)).strip()
            self._on_progress("execute_query", f"failed: {message}")
            return {"error": message}
        self._on_progress("execute_query", f"{result['row_count']} row(s)")
        return {"result": result}

    def _give_up(self, state: AgentState) -> AgentState:
        message = (
            f"Could not produce a valid query in {state['attempts']} attempts. "
            f"Last problems: {'; '.join(state['issues'])}"
        )
        self._on_progress("give_up", message)
        return {"error": message}
