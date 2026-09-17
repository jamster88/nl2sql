"""Command line entry point: ask a question, get rows back."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import Settings
from .graph import Nl2SqlAgent
from .llm import LlmUnavailableError

STEP_LABELS = {
    "retrieve_knowledge": "knowledge",
    "select_tables": "tables",
    "fetch_schema": "schema",
    "generate_sql": "sql",
    "validate_sql": "validation",
    "execute_query": "result",
    "give_up": "gave up",
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
    p.add_argument("--max-attempts", type=int, default=settings.max_sql_attempts)
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
    p.add_argument("--json", action="store_true", help="emit the full result as JSON")
    p.add_argument("--quiet", action="store_true", help="only print the final answer")
    return p.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    settings.ollama_model = args.model
    settings.ollama_base_url = args.base_url
    settings.database_url = args.database_url
    settings.max_rows = args.max_rows
    settings.max_sql_attempts = args.max_attempts
    settings.sample_rows = args.sample_rows
    settings.reasoning = args.reasoning
    settings.rag_enabled = args.rag
    settings.vector_db_url = args.vector_db_url
    settings.embed_model = args.embed_model
    settings.embed_base_url = args.embed_url
    settings.rag_top_k = args.rag_top_k
    return settings


def format_rows(result: dict[str, Any]) -> str:
    columns, rows = result["columns"], result["rows"]
    if not rows:
        return "(no rows)"
    cells = [columns] + [["NULL" if v is None else str(v) for v in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    lines = [
        " | ".join(value.ljust(widths[i]) for i, value in enumerate(cells[0])),
        "-+-".join("-" * w for w in widths),
    ]
    lines.extend(" | ".join(value.ljust(widths[i]) for i, value in enumerate(row)) for row in cells[1:])
    if result["truncated"]:
        lines.append(f"... truncated at {len(rows)} rows")
    return "\n".join(lines)


def answer(agent: Nl2SqlAgent, question: str, *, as_json: bool, quiet: bool) -> int:
    state = agent.run(question)
    if as_json:
        print(
            json.dumps(
                {
                    "question": question,
                    "knowledge_chunks": state.get("knowledge_chunks", []),
                    "retrieval_error": state.get("retrieval_error"),
                    "selected_tables": state.get("selected_tables", []),
                    "sql": state.get("sql"),
                    "error": state.get("error"),
                    "result": state.get("result"),
                },
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
    print(format_rows(state["result"]))
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
