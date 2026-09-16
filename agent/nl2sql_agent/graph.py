"""The agent pipeline, as a LangGraph state graph.

Node order mirrors basic_agent_steps.md:

    select_tables -> fetch_schema -> generate_sql -> validate_sql -> execute_query
                                          ^               |
                                          +---- retry ----+

Each step is its own node, so an extra step (a retrieval node feeding
`schema`, for instance) is added by registering a node and moving one edge.
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from .config import Settings
from .database import Database, strip_sql
from .llm import build_llm
from .prompts import RETRY_FEEDBACK, SQL_GENERATION_PROMPT, TABLE_SELECTION_PROMPT
from .tools import TableSelection, build_tools

ProgressFn = Callable[[str, str], None]


class AgentState(TypedDict, total=False):
    question: str
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
        self.tools = build_tools(self.db, self.llm, settings)
        self._on_progress = on_progress or (lambda step, detail: None)
        self._graph = self._build_graph()

    def run(self, question: str) -> AgentState:
        return self._graph.invoke({"question": question, "attempts": 0})

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("select_tables", self._select_tables)
        graph.add_node("fetch_schema", self._fetch_schema)
        graph.add_node("generate_sql", self._generate_sql)
        graph.add_node("validate_sql", self._validate_sql)
        graph.add_node("execute_query", self._execute_query)
        graph.add_node("give_up", self._give_up)

        graph.add_edge(START, "select_tables")
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

    # --- Step 2: describe all tables, ask the model which ones matter --------
    def _select_tables(self, state: AgentState) -> AgentState:
        catalog = self.tools["describe_all_tables"].invoke({})
        selection = self.llm.with_structured_output(TableSelection).invoke(
            TABLE_SELECTION_PROMPT.format_messages(
                catalog=catalog, question=state["question"]
            )
        )
        known = set(self.db.table_names())
        tables = [t for t in selection.tables if t in known]
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
        response = self.llm.invoke(
            SQL_GENERATION_PROMPT.format_messages(
                dialect=self.db.dialect,
                schema=state["schema"],
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
