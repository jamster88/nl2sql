"""The v4 multi-agent pipeline, as a LangGraph state graph.

Four stages, one shared state object (`state.py`), one retry loop:

    supervise ─┬─ retrieve_schema ──┐
               ├─ retrieve_literals ┤
               ├─ retrieve_knowledge┤► aggregate ─► generate_sql
               └─ retrieve_examples ┘                   │
                                                        ▼
        give_up ◄── repair ◄─────────────────────  validate_static
                      ▲   │                             │ pass
        ┌─────────────┘   └─ attempts < max ─┐          ▼
        │                                    └──► planner_gate ─► execute
        │                                                            │
        └──────── semantic_issue ── audit ◄── narrate ◄── visualise ◄─┘

What changed from v3 and why, in one line each:

* **Stage 1 fans out.** The four retrievers are independent given the
  question, so they are branches of one superstep and the aggregator is the
  fan-in. Retrieval is 0.1% of runtime either way; this is for clarity.
* **No model call selects tables.** The Schema Retriever searches the DDL
  chunk vectors and closes over foreign keys. In v3 that was an LLM call
  costing 364 of 1499 benchmark seconds.
* **No model call validates.** A `pglast` AST check and a plain `EXPLAIN`
  replace v3's LLM reviewer, which cost 499 seconds and was the only
  component in 45 runs that ever turned a right answer into no answer.
* **Every failure goes to one place.** Static, planner, runtime and audit
  failures all route to the Repair Agent and all spend the same `attempts`
  counter, so there is no path that loops without being counted. The
  architecture's source diagram let planner failures skip repair entirely
  and let the audit re-enter generation uncounted; both are fixed here.
* **The model's semantic review moved after execution.** It is the Audit
  Checker's `semantic_issue`, raised with rows in hand rather than guessed
  from the schema, and it spends the shared budget like anything else.

Every stage the architecture makes optional is a setting, so an ablation is
an environment change rather than a code change.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from . import present, repair as repair_agent, supervisor
from .config import Settings
from .database import Database, strip_sql
from .examples import BY_KEYWORDS, BY_QUESTION, BY_REASONING, GoldenPairLibrary
from .literals import LiteralMatcher, build_catalog, render_literal_map
from .llm import build_llm
from .prompts import (
    RETRY_FEEDBACK,
    SQL_GENERATION_PROMPT,
    TABLE_SELECTION_PROMPT,
    example_messages,
    knowledge_block,
    literal_block,
    task_block,
)
from .retrieval import KnowledgeBase, build_embedder
from .schema_retrieval import DDL_COLLECTION, SchemaRetriever
from .state import (
    AUDIT,
    PLANNER,
    RUNTIME,
    STATIC,
    AgentState,
    Attempt,
    AuditReport,
    Issue,
    QueryResult,
    Shot,
    TraceEntry,
    new_state,
)
from .tools import TableSelection, build_tools
from .validate import validate as validate_sql_statically

ProgressFn = Callable[[str, str], None]

#: LangGraph stops a run that exceeds this many supersteps. The loop is about
#: six nodes deep per attempt, so the default of 25 would abort a run that was
#: still inside its own retry budget -- a failure that looks like a hang.
RECURSION_LIMIT = 80

#: How many times the narrator may be asked to rewrite claims the audit could
#: not verify. One, per section 7.3: a second failure means the rows do not
#: support the sentence, and asking again is how a loop starts.
MAX_NARRATION_RETRIES = 1

#: Keys a node returns for the tracer rather than for the state.
_DETAIL = "_detail"
_MODEL_CALLS = "_model_calls"


class Nl2SqlAgent:
    """The pipeline. Construct once, call `run` per question."""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: BaseChatModel | None = None,
        knowledge_base: KnowledgeBase | None = None,
        example_library: GoldenPairLibrary | None = None,
        schema_retriever: SchemaRetriever | None = None,
        literal_matcher: LiteralMatcher | None = None,
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
        self.schema_retriever = schema_retriever or self._build_schema_retriever(settings)
        self._literal_matcher = literal_matcher
        self._literal_catalog_built = literal_matcher is not None
        self.tools = build_tools(
            self.db, self.llm, settings, self.knowledge_base, self.example_library
        )
        self._on_progress = on_progress or (lambda step, detail: None)
        self._graph = self._build_graph()

    # --- construction -------------------------------------------------------

    @staticmethod
    def _build_knowledge_base(settings: Settings) -> KnowledgeBase | None:
        """The business-rule collections, with the DDL index left out.

        The DDL chunks are the Schema Retriever's input now, so including them
        here would spend the knowledge budget re-describing tables whose full
        definition the generator is already given.
        """
        if not settings.rag_enabled:
            return None
        return KnowledgeBase(
            settings.vector_db_url,
            build_embedder(settings),
            top_k=settings.rag_top_k,
            exclude_collections=[DDL_COLLECTION],
        )

    @staticmethod
    def _build_example_library(settings: Settings) -> GoldenPairLibrary | None:
        """The v3 ensemble, unchanged: it measures 45/45 on its own corpus."""
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
            rerank=settings.examples_rerank,
            rerank_k=settings.examples_rerank_k,
            rerank_lambda=settings.examples_rerank_lambda,
            grounding_weight=settings.examples_grounding_weight,
        )

    def _build_schema_retriever(self, settings: Settings) -> SchemaRetriever | None:
        """Vector search restricted to the one collection of DDL chunks."""
        if settings.schema_retrieval != "vector" or not settings.rag_enabled:
            return None
        return SchemaRetriever(
            KnowledgeBase(
                settings.vector_db_url,
                build_embedder(settings),
                top_k=settings.schema_top_k,
                collections=[DDL_COLLECTION],
            ),
            self.db,
            top_k=settings.schema_top_k,
            max_tables=settings.max_tables,
        )

    def _literals(self) -> LiteralMatcher | None:
        """The literal catalog, built once on first use.

        Building it reads every low-cardinality text column in the database,
        which is worth doing once per process and never per question. A
        failure is not fatal: without a catalog the generator simply has to
        spell literals from the question, which is what v3 did.
        """
        if not self.settings.literals_enabled:
            return None
        if not self._literal_catalog_built:
            self._literal_catalog_built = True
            try:
                catalog = build_catalog(
                    self.db,
                    max_distinct=self.settings.literal_max_distinct,
                    schema=self.settings.db_schema,
                )
                # Handing over the database lets the matcher use pg_trgm when
                # the extension is installed; it falls back to difflib on its
                # own when it is not, so this is quality, not capability.
                self._literal_matcher = LiteralMatcher(
                    catalog,
                    min_score=self.settings.literal_min_score,
                    database=self.db,
                )
            except Exception:
                self._literal_matcher = None
        return self._literal_matcher

    # --- running ------------------------------------------------------------

    def run(self, question: str, *, principal: str | None = None) -> AgentState:
        return self._graph.invoke(
            new_state(question, principal=principal),
            config={"recursion_limit": RECURSION_LIMIT},
        )

    def _traced(self, name: str, fn: Callable[[AgentState], dict]) -> Callable[[AgentState], dict]:
        """Wrap a node so it reports its own cost.

        Per-agent timing is how the architecture's central claim gets tested:
        that the Supervisor and Narrator together cost less than the table
        selection and validation calls they replaced. A node reports its
        progress detail and model-call count through two private keys, which
        are stripped here rather than reaching the state.
        """

        def node(state: AgentState) -> dict:
            started = time.perf_counter()
            update = dict(fn(state) or {})
            detail = str(update.pop(_DETAIL, ""))
            update["trace"] = [
                TraceEntry(
                    node=name,
                    ms=round((time.perf_counter() - started) * 1000, 2),
                    model_calls=int(update.pop(_MODEL_CALLS, 0)),
                    detail=detail,
                )
            ]
            self._on_progress(name, detail)
            return update

        return node

    def _build_graph(self):
        graph = StateGraph(AgentState)
        # Registered one literal call each rather than from a loop: the node
        # names are the interface this pipeline is documented and diagrammed
        # by, and tests/docs reads them straight out of this file as text so
        # the checks run without the agent's dependencies installed.
        graph.add_node("supervise", self._traced("supervise", self._supervise))
        graph.add_node("refuse", self._traced("refuse", self._refuse))
        graph.add_node("retrieve_schema", self._traced("retrieve_schema", self._retrieve_schema))
        graph.add_node("retrieve_literals", self._traced("retrieve_literals", self._retrieve_literals))
        graph.add_node("retrieve_knowledge", self._traced("retrieve_knowledge", self._retrieve_knowledge))
        graph.add_node("retrieve_examples", self._traced("retrieve_examples", self._retrieve_examples))
        graph.add_node("aggregate", self._traced("aggregate", self._aggregate))
        graph.add_node("generate_sql", self._traced("generate_sql", self._generate_sql))
        graph.add_node("validate_static", self._traced("validate_static", self._validate_static))
        graph.add_node("planner_gate", self._traced("planner_gate", self._planner_gate))
        graph.add_node("execute_query", self._traced("execute_query", self._execute_query))
        graph.add_node("repair", self._traced("repair", self._repair))
        graph.add_node("give_up", self._traced("give_up", self._give_up))
        graph.add_node("visualise", self._traced("visualise", self._visualise))
        graph.add_node("narrate", self._traced("narrate", self._narrate))
        graph.add_node("audit", self._traced("audit", self._audit))
        graph.add_node("finish", self._traced("finish", self._finish))

        retrievers = [
            "retrieve_schema",
            "retrieve_literals",
            "retrieve_knowledge",
            "retrieve_examples",
        ]

        graph.add_edge(START, "supervise")
        # The Supervisor either stops the run before any retrieval, or opens
        # all four retrieval branches at once.
        graph.add_conditional_edges(
            "supervise", self._route_after_supervisor, ["refuse", *retrievers]
        )
        for name in retrievers:
            graph.add_edge(name, "aggregate")
        graph.add_edge("aggregate", "generate_sql")
        graph.add_edge("generate_sql", "validate_static")
        graph.add_conditional_edges(
            "validate_static",
            self._route_after_static,
            {"planner": "planner_gate", "repair": "repair"},
        )
        graph.add_conditional_edges(
            "planner_gate",
            self._route_after_planner,
            {"execute": "execute_query", "repair": "repair"},
        )
        graph.add_conditional_edges(
            "execute_query",
            self._route_after_execution,
            {"present": "visualise", "repair": "repair"},
        )
        # One budget, checked in one place.
        graph.add_conditional_edges(
            "repair", self._route_after_repair, {"retry": "generate_sql", "give_up": "give_up"}
        )
        graph.add_edge("visualise", "narrate")
        graph.add_edge("narrate", "audit")
        graph.add_conditional_edges(
            "audit",
            self._route_after_audit,
            {"repair": "repair", "renarrate": "narrate", "finish": "finish"},
        )
        graph.add_edge("refuse", END)
        graph.add_edge("give_up", END)
        graph.add_edge("finish", END)
        return graph.compile()

    # --- stage 1: intake ----------------------------------------------------

    def _supervise(self, state: AgentState) -> dict:
        """Screen for injection and scope, and classify intent."""
        if not self.settings.supervisor_enabled:
            return {_DETAIL: "disabled"}
        update = supervisor.screen(
            self.llm,
            state["question"],
            clarify_enabled=self.settings.clarify_enabled,
            tables=self.db.table_names(),
        )
        update[_MODEL_CALLS] = 0 if update.get("retrieval_errors") else 1
        update[_DETAIL] = f"{update['verdict']} / {update['intent']}"
        return update

    def _route_after_supervisor(self, state: AgentState) -> list[str] | str:
        if state.get("verdict", "proceed") != "proceed":
            return "refuse"
        return [
            "retrieve_schema",
            "retrieve_literals",
            "retrieve_knowledge",
            "retrieve_examples",
        ]

    def _refuse(self, state: AgentState) -> dict:
        verdict = state.get("verdict", "proceed")
        answer = supervisor.refusal(
            verdict,
            state.get("clarification"),
            domain=supervisor.describe_scope(self.db.table_names()),
        )
        return {"answer": answer, "narrative": answer, _DETAIL: verdict}

    def _retrieve_schema(self, state: AgentState) -> dict:
        """Tables from the DDL-chunk vectors. No model call."""
        if self.settings.schema_retrieval == "llm":
            return self._select_tables_with_llm(state)
        if self.schema_retriever is None:
            return {"schema_tables": [], _DETAIL: "disabled"}
        selection = self.schema_retriever.select(state["question"])
        update: dict[str, Any] = {"schema_tables": selection.tables}
        if selection.error:
            update["retrieval_errors"] = {"schema": selection.error}
        update[_DETAIL] = ", ".join(selection.tables) or "none"
        return update

    def _select_tables_with_llm(self, state: AgentState) -> dict:
        """v3's table-selection call, kept only so the two can be compared.

        This is the stage the architecture removes: it cost 364 of 1499
        benchmark seconds, a quarter of the run, to do what a vector search
        over the DDL chunks does in milliseconds. It stays reachable through
        `SCHEMA_RETRIEVAL=llm` because a claim of that size deserves to be
        re-measurable rather than taken on trust.
        """
        catalog = self.tools["describe_all_tables"].invoke({})
        known = set(self.db.table_names())
        try:
            selection = self.llm.with_structured_output(TableSelection).invoke(
                TABLE_SELECTION_PROMPT.format_messages(
                    catalog=catalog,
                    knowledge=knowledge_block(state.get("knowledge", "")),
                    question=state["question"],
                )
            )
            tables = [t for t in selection.tables if t in known]
        except Exception as exc:
            return {"schema_tables": [], "retrieval_errors": {"schema": str(exc)}}
        return {
            "schema_tables": tables,
            _MODEL_CALLS: 1,
            _DETAIL: ", ".join(tables) or "none",
        }

    def _retrieve_literals(self, state: AgentState) -> dict:
        """Phrases in the question resolved to real values in the database."""
        matcher = self._literals()
        if matcher is None:
            return {"literal_map": [], _DETAIL: "disabled"}
        try:
            matches = matcher.match(state["question"])
        except Exception as exc:
            return {"literal_map": [], "retrieval_errors": {"literals": str(exc)}}
        return {
            "literal_map": matches,
            _DETAIL: "; ".join(m.render() for m in matches[:3]) or "no literals matched",
        }

    def _retrieve_knowledge(self, state: AgentState) -> dict:
        retrieved = self.tools["search_knowledge"].invoke({"question": state["question"]})
        if retrieved["error"]:
            return {
                "knowledge": "",
                "knowledge_tables": [],
                "knowledge_chunks": [],
                "retrieval_errors": {"knowledge": retrieved["error"]},
                _DETAIL: f"skipped: {retrieved['error']}",
            }
        chunks = retrieved["chunks"]
        summary = ", ".join(
            f"{c['source_doc']}:{c['heading_path'].split('>')[-1].strip()}" for c in chunks[:4]
        )
        return {
            "knowledge": retrieved["context"],
            "knowledge_tables": retrieved["tables"],
            "knowledge_chunks": chunks,
            _DETAIL: f"{len(chunks)} chunk(s) -- {summary}",
        }

    def _retrieve_examples(self, state: AgentState) -> dict:
        retrieved = self.tools["search_examples"].invoke({"question": state["question"]})
        if retrieved["error"]:
            return {
                "example_shots": [],
                "example_tables": [],
                "example_pairs": [],
                "retrieval_errors": {"examples": retrieved["error"]},
                _DETAIL: f"skipped: {retrieved['error']}",
            }
        pairs = retrieved["pairs"]
        return {
            "example_shots": [Shot(**shot) for shot in retrieved["shots"]],
            "example_tables": retrieved["tables"],
            "example_pairs": pairs,
            _DETAIL: ", ".join(f"{p['pair_id']} ({p['score']:.3f})" for p in pairs),
        }

    def _aggregate(self, state: AgentState) -> dict:
        """The fan-in: one table set, foreign-key closed, capped, described.

        Each retriever proposes tables from its own evidence -- similarity,
        the documents, the worked examples -- and only here are all three
        known, so this is the only place the closure and the cap can be
        applied to the union of them.
        """
        known = set(self.db.table_names())
        proposed: list[str] = []
        for name in (
            list(state.get("schema_tables", []))
            + list(state.get("knowledge_tables", []))
            + list(state.get("example_tables", []))
        ):
            if name in known and name not in proposed:
                proposed.append(name)

        bridges: list[str] = []
        if self.schema_retriever is not None and proposed:
            closed = self.schema_retriever.close_and_cap(proposed)
            tables, bridges = closed.tables, closed.bridges
        else:
            tables = proposed[: self.settings.max_tables]

        if not tables:
            # Nothing was retrievable. Schema-only is exactly v1's behaviour
            # and still answers a good many questions.
            tables = sorted(known)[: self.settings.max_tables]

        schema = self.tools["get_schema_and_data"].invoke({"tables": tables})
        detail = ", ".join(tables)
        if bridges:
            detail += f" (+{len(bridges)} bridge: {', '.join(bridges)})"
        return {"selected_tables": tables, "schema": schema, _DETAIL: detail}

    # --- stage 2: synthesis -------------------------------------------------

    def _generate_sql(self, state: AgentState) -> dict:
        """The only place SQL is written, on the first draft and every repair."""
        feedback = ""
        issues = state.get("issues", [])
        if issues:
            feedback = RETRY_FEEDBACK.format(
                sql=state.get("sql", ""),
                issues="\n".join(f"- {issue.render()}" for issue in issues),
            )
        shots = state.get("example_shots", []) if self.settings.multi_shot_enabled else []
        messages = SQL_GENERATION_PROMPT.format_messages(
            dialect=self.db.dialect,
            schema=state.get("schema", ""),
            knowledge=knowledge_block(state.get("knowledge", "")),
            literals=literal_block(render_literal_map(state.get("literal_map", []))),
            task=task_block(supervisor.intent_framing(state.get("intent", ""))),
            examples=example_messages([_shot_dict(s) for s in shots]),
            question=state["question"],
            feedback=feedback,
        )
        response = self.llm.invoke(messages)
        sql = strip_sql(str(response.content))
        return {
            "sql": sql,
            "attempts": state.get("attempts", 0) + 1,
            "issues": [],
            _MODEL_CALLS: 1,
            _DETAIL: sql,
        }

    # --- stage 3: validation and repair -------------------------------------

    def _validate_static(self, state: AgentState) -> dict:
        """The AST gate: one statement, SELECT only, in-scope tables."""
        issues = validate_sql_statically(
            state.get("sql", ""), allowed_tables=state.get("selected_tables") or None
        )
        return {
            "issues": issues,
            _DETAIL: "valid" if not issues else "; ".join(i.message for i in issues),
        }

    def _route_after_static(self, state: AgentState) -> str:
        return "repair" if state.get("issues") else "planner"

    def _planner_gate(self, state: AgentState) -> dict:
        """Plan without executing; reject server errors and runaway costs.

        This is where the architecture's most valuable feedback comes from.
        The parser cannot see an unknown column, a type mismatch or an
        aggregate outside GROUP BY; the planner sees all three, in
        milliseconds, and its message goes to the Repair Agent verbatim.
        """
        cost, error = self.db.explain_plan(state.get("sql", ""))
        if error:
            return {
                "issues": [Issue(source=PLANNER, message=error)],
                _DETAIL: error.splitlines()[0],
            }
        ceiling = self.settings.max_plan_cost
        if cost is not None and cost > ceiling:
            message = (
                f"estimated plan cost {cost:,.2f} exceeds the ceiling of {ceiling:,.2f}"
            )
            return {
                "plan_cost": cost,
                "issues": [Issue(source=PLANNER, message=message)],
                _DETAIL: message,
            }
        return {"plan_cost": cost, "issues": [], _DETAIL: f"cost {cost:,.2f}" if cost else "planned"}

    def _route_after_planner(self, state: AgentState) -> str:
        return "repair" if state.get("issues") else "execute"

    def _execute_query(self, state: AgentState) -> dict:
        """Run it: reader role, READ ONLY, statement timeout, row cap."""
        try:
            raw = self.db.run_select(state["sql"], principal=state.get("principal"))
        except Exception as exc:
            message = str(getattr(exc, "orig", exc)).strip()
            return {
                "issues": [Issue(source=RUNTIME, message=message)],
                _DETAIL: f"failed: {message.splitlines()[0]}",
            }
        result = QueryResult(
            columns=list(raw.columns),
            rows=[list(row) for row in raw.rows],
            truncated=raw.truncated,
        )
        return {
            "result": result,
            "issues": [],
            _DETAIL: f"{result.row_count} row(s)" + (" (truncated)" if result.truncated else ""),
        }

    def _route_after_execution(self, state: AgentState) -> str:
        return "repair" if state.get("issues") else "present"

    def _repair(self, state: AgentState) -> dict:
        """Turn whatever failed into a hint. This agent never writes SQL."""
        issues = state.get("issues", []) or [Issue(source=STATIC, message="Query rejected.")]
        # The record of this attempt has to exist before the classifier runs:
        # the syntax rule quotes the failed SQL, and the repeat warning looks
        # for the same error having been seen before.
        history = list(state.get("attempt_history", [])) + [
            Attempt(sql=state.get("sql", ""), issues=list(issues))
        ]
        hinted, model_calls = repair_agent.repair_hint(
            issues,
            llm=self.llm,
            schema=state.get("schema", ""),
            allowed_tables=state.get("selected_tables", []),
            history=history,
        )
        # Record the hinted issues, not the bare ones: the history is what a
        # give-up shows the user and what the next hint reads, so it should
        # say both why an attempt failed and what the generator was told to
        # do about it.
        history[-1] = Attempt(sql=history[-1].sql, issues=hinted)
        return {
            "issues": hinted,
            "attempt_history": history,
            _MODEL_CALLS: model_calls,
            _DETAIL: "; ".join(i.hint or i.message for i in hinted),
        }

    def _route_after_repair(self, state: AgentState) -> str:
        return "retry" if state.get("attempts", 0) < self.settings.max_attempts else "give_up"

    def _give_up(self, state: AgentState) -> dict:
        """Return the last SQL and everything that was tried.

        A user who gets no answer should at least see the attempts, which is
        also what makes a give-up debuggable rather than merely disappointing.
        """
        issues = state.get("issues", [])
        message = (
            f"Could not produce a valid query in {state.get('attempts', 0)} attempts. "
            f"Last problems: {'; '.join(i.message for i in issues)}"
        )
        return {"error": message, "answer": message, _DETAIL: message}

    # --- stage 4: presentation and audit ------------------------------------

    def _visualise(self, state: AgentState) -> dict:
        """Chart choice is a lookup on result shape, not a model call."""
        result = state.get("result")
        if result is None:
            return {"chart": None, _DETAIL: "no result"}
        chart = present.choose_chart(result, intent=state.get("intent", "aggregate"))
        return {"chart": chart, _DETAIL: chart.kind}

    def _narrate(self, state: AgentState) -> dict:
        """Write the claims, once, or rewrite them once after an audit drop.

        Section 7.3 rule 4: unsupported claims go back to the narrator exactly
        once, with the reasons they failed, and on the second failure they are
        dropped and the report says so. `narration_retries` counts that and is
        separate from `attempts` on purpose -- a claim the checker could not
        reproduce says nothing about whether the SQL was right, so it must not
        spend the budget that decides whether to rewrite the query.
        """
        result = state.get("result")
        if result is None or not self.settings.narrate_enabled:
            return {"claims": [], _DETAIL: "disabled"}
        shots = state.get("example_shots", [])
        report = state.get("audit")
        rejected = list(report.drop_reasons) if report else []
        try:
            claims = present.narrate(
                self.llm,
                state["question"],
                result,
                chart=state.get("chart"),
                reasoning_target=shots[0].reasoning_target if shots else "",
                max_rows=self.settings.max_rows,
                rejected=rejected,
            )
        except Exception as exc:
            return {"claims": [], "retrieval_errors": {"narrator": str(exc)}, _DETAIL: str(exc)}
        update: dict[str, Any] = {"claims": claims, _MODEL_CALLS: 1}
        if rejected:
            update["narration_retries"] = state.get("narration_retries", 0) + 1
        update[_DETAIL] = f"{len(claims)} claim(s)" + (" (rewritten)" if rejected else "")
        return update

    def _audit(self, state: AgentState) -> dict:
        """Verify every number against the rows it claims to come from."""
        result = state.get("result")
        if result is None:
            return {"audit": AuditReport(), _DETAIL: "no result"}
        if not self.settings.audit_enabled:
            return {"audit": AuditReport(), _DETAIL: "disabled"}
        # The question is part of the semantic check: an empty result is only
        # suspicious when the question implied there would be rows.
        report = present.audit(
            state.get("claims", []), result, question=state.get("question", "")
        )
        update: dict[str, Any] = {"audit": report}
        if report.semantic_issue:
            # The audit judged the SQL wrong, not the prose. That is a repair,
            # and it spends the same budget as every other failure.
            update["issues"] = [Issue(source=AUDIT, message=report.semantic_issue)]
        detail = "passed" if report.passed else f"{len(report.unsupported_claims)} unsupported"
        update[_DETAIL] = report.semantic_issue or detail
        return update

    def _route_after_audit(self, state: AgentState) -> str:
        report = state.get("audit")
        if report is None:
            return "finish"
        if report.semantic_issue:
            # The audit judged the query wrong, not the prose. That is a
            # repair, and it spends the shared budget like any other failure.
            return "repair"
        if report.drop_reasons and state.get("narration_retries", 0) < MAX_NARRATION_RETRIES:
            return "renarrate"
        return "finish"

    def _finish(self, state: AgentState) -> dict:
        result = state.get("result") or QueryResult()
        report = state.get("audit") or AuditReport()
        claims = present.surviving_claims(state.get("claims", []), report)
        narrative = " ".join(c.text for c in claims)
        answer = present.render_answer(
            state["question"], result, claims, state.get("chart"), report
        )
        return {"narrative": narrative, "answer": answer, _DETAIL: f"{len(answer):,} characters"}


def _shot_dict(shot: Shot | dict) -> dict:
    """The exemplar shape `prompts.example_messages` takes."""
    if isinstance(shot, dict):
        return shot
    return {
        "pair_id": shot.pair_id,
        "question": shot.question,
        "reasoning_target": shot.reasoning_target,
        "sql_code": shot.sql_code,
    }
