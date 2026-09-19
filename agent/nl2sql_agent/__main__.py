"""Command line entry point: ask a question, get rows back."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import Settings
from .graph import Nl2SqlAgent
from .llm import LlmUnavailableError
from .state import to_jsonable

STEP_LABELS = {
    "supervise": "screen",
    "refuse": "refused",
    "retrieve_schema": "tables",
    "retrieve_literals": "literals",
    "retrieve_knowledge": "knowledge",
    "retrieve_examples": "examples",
    "aggregate": "schema",
    "generate_sql": "sql",
    "validate_static": "validation",
    "planner_gate": "planner",
    "execute_query": "result",
    "repair": "repair",
    "give_up": "gave up",
    "visualise": "chart",
    "narrate": "narrative",
    "audit": "audit",
    "finish": "answer",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = Settings.from_env()
    p = argparse.ArgumentParser(
        prog="nl2sql-agent", description="Ask a natural language question about the database."
    )
    p.add_argument("question", nargs="*", help="the question (omit for interactive mode)")
    p.add_argument("--model", default=settings.ollama_model, help="Ollama model tag")
    p.add_argument("--base-url", default=settings.ollama_base_url, help="Ollama host URL")
    p.add_argument("--database-url", default=settings.database_url)
    p.add_argument("--max-rows", type=int, default=settings.max_rows)
    p.add_argument("--max-attempts", type=int, default=settings.max_attempts)
    p.add_argument("--sample-rows", type=int, default=settings.sample_rows)
    p.add_argument(
        "--reasoning",
        action=argparse.BooleanOptionalAction,
        default=settings.reasoning,
        help="let the model emit reasoning tokens (slower)",
    )
    p.add_argument(
        "--rag",
        action=argparse.BooleanOptionalAction,
        default=settings.rag_enabled,
        help="retrieve knowledge-base context for the question (default: on)",
    )
    p.add_argument("--vector-db-url", default=settings.vector_db_url, help="pgvector knowledge base URL")
    p.add_argument("--embed-model", default=settings.embed_model, help="embedding model for retrieval")
    p.add_argument("--embed-url", default=settings.embed_base_url, help="Ollama host serving the embedding model")
    p.add_argument("--rag-top-k", type=int, default=settings.rag_top_k, help="chunks retrieved per collection")
    p.add_argument(
        "--examples",
        action=argparse.BooleanOptionalAction,
        default=settings.examples_enabled,
        help="retrieve worked question/SQL pairs for the question (default: on)",
    )
    p.add_argument(
        "--multi-shot",
        action=argparse.BooleanOptionalAction,
        default=settings.multi_shot_enabled,
        help="show the retrieved examples to the SQL generator (default: off)",
    )
    p.add_argument("--context-db-url", default=settings.context_db_url, help="context store URL (golden pairs + BM25)")
    p.add_argument("--examples-top-k", type=int, default=settings.examples_top_k, help="worked examples handed to the model")
    p.add_argument("--json", action="store_true", help="emit the full result as JSON")
    p.add_argument("--quiet", action="store_true", help="only print the final answer")
    return p.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    settings.ollama_model = args.model
    settings.ollama_base_url = args.base_url
    settings.database_url = args.database_url
    settings.max_rows = args.max_rows
    settings.max_attempts = args.max_attempts
    settings.sample_rows = args.sample_rows
    settings.reasoning = args.reasoning
    settings.rag_enabled = args.rag
    settings.vector_db_url = args.vector_db_url
    settings.embed_model = args.embed_model
    settings.embed_base_url = args.embed_url
    settings.rag_top_k = args.rag_top_k
    settings.examples_enabled = args.examples
    settings.multi_shot_enabled = args.multi_shot
    settings.context_db_url = args.context_db_url
    settings.examples_top_k = args.examples_top_k
    return settings


def format_rows(result: Any) -> str:
    """The plain-text table for a terminal, from a QueryResult or a dict.

    Both shapes are accepted because the benchmark and older callers still
    hand over plain dictionaries, while the v4 pipeline carries a dataclass.
    """
    if result is None:
        return "(no rows)"
    if isinstance(result, dict):
        columns, rows, truncated = result["columns"], result["rows"], result["truncated"]
    else:
        columns, rows, truncated = result.columns, result.rows, result.truncated
    if not rows:
        return "(no rows)"
    cells = [list(columns)] + [["NULL" if v is None else str(v) for v in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    lines = [
        " | ".join(value.ljust(widths[i]) for i, value in enumerate(cells[0])),
        "-+-".join("-" * w for w in widths),
    ]
    lines.extend(" | ".join(value.ljust(widths[i]) for i, value in enumerate(row)) for row in cells[1:])
    if truncated:
        lines.append(f"... truncated at {len(rows)} rows")
    return "\n".join(lines)


def answer(agent: Nl2SqlAgent, question: str, *, as_json: bool, quiet: bool) -> int:
    state = agent.run(question)
    if as_json:
        print(
            json.dumps(
                to_jsonable(
                    {
                        "question": question,
                        "verdict": state.get("verdict"),
                        "intent": state.get("intent"),
                        "knowledge_chunks": state.get("knowledge_chunks", []),
                        "example_pairs": state.get("example_pairs", []),
                        "literal_map": state.get("literal_map", []),
                        "retrieval_errors": state.get("retrieval_errors", {}),
                        "selected_tables": state.get("selected_tables", []),
                        "sql": state.get("sql"),
                        "attempts": state.get("attempts"),
                        "plan_cost": state.get("plan_cost"),
                        "attempt_history": state.get("attempt_history", []),
                        "error": state.get("error"),
                        "result": state.get("result"),
                        "chart": state.get("chart"),
                        "claims": state.get("claims", []),
                        "narrative": state.get("narrative"),
                        "audit": state.get("audit"),
                        "answer": state.get("answer"),
                        "trace": state.get("trace", []),
                    }
                ),
                indent=2,
                default=str,
            )
        )
        return 1 if state.get("error") else 0

    if state.get("error"):
        print(f"\nfailed: {state['error']}", file=sys.stderr)
        return 1
    if not quiet:
        print()
    # The refusal and clarification paths never reach a result, so the answer
    # is all there is to print.
    if state.get("result") is None:
        print(state.get("answer") or "(no answer)")
        return 0
    narrative = (state.get("narrative") or "").strip()
    if narrative:
        print(narrative)
        print()
    print(format_rows(state.get("result")))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = settings_from_args(args)

    def on_progress(step: str, detail: str) -> None:
        if args.quiet or args.json:
            return
        label = STEP_LABELS.get(step, step)
        print(f"[{label}] {detail}", file=sys.stderr)

    try:
        agent = Nl2SqlAgent(settings, on_progress=on_progress)
    except LlmUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.question:
        return answer(agent, " ".join(args.question), as_json=args.json, quiet=args.quiet)

    print(f"Connected to {settings.ollama_model} at {settings.ollama_base_url}.")
    if settings.rag_enabled:
        print(f"Knowledge base: {settings.embed_model} embeddings against {settings.vector_db_url}")
    if settings.examples_enabled:
        shown = "shown to the generator" if settings.multi_shot_enabled else "retrieved but not prompted with"
        print(
            f"Worked examples: ensemble over {settings.context_db_url} "
            f"(question {settings.example_weight_question:g} / "
            f"keywords {settings.example_weight_keywords:g} / "
            f"reasoning {settings.example_weight_reasoning:g}), {shown}"
        )
    print("Ask a question, or Ctrl-D to exit.")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question:
            answer(agent, question, as_json=args.json, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
