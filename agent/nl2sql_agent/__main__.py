"""Command line entry point: ask a question, get rows back."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import tracing
from .config import Settings
from .ensemble import build_agent, step_label
from .ensemble_state import is_ensemble, run_state
from .llm import LlmUnavailableError
from .router import RoutingError
from .state import to_jsonable


def _default(on: bool) -> str:
    """What a switch's help says it defaults to: what it really does, which is
    the setting, so a `.env` that turns one off is described truthfully."""
    return f"(default: {'on' if on else 'off'})"


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
        help=f"retrieve knowledge-base context for the question {_default(settings.rag_enabled)}",
    )
    p.add_argument("--vector-db-url", default=settings.vector_db_url, help="pgvector knowledge base URL")
    p.add_argument("--embed-model", default=settings.embed_model, help="embedding model for retrieval")
    p.add_argument("--embed-url", default=settings.embed_base_url, help="Ollama host serving the embedding model")
    p.add_argument("--rag-top-k", type=int, default=settings.rag_top_k, help="chunks retrieved per collection")
    p.add_argument(
        "--examples",
        action=argparse.BooleanOptionalAction,
        default=settings.examples_enabled,
        help=f"retrieve worked question/SQL pairs for the question {_default(settings.examples_enabled)}",
    )
    p.add_argument(
        "--multi-shot",
        action=argparse.BooleanOptionalAction,
        default=settings.multi_shot_enabled,
        help=f"show the retrieved examples to the SQL generator {_default(settings.multi_shot_enabled)}",
    )
    p.add_argument("--context-db-url", default=settings.context_db_url, help="context store URL (golden pairs + BM25)")
    p.add_argument("--examples-top-k", type=int, default=settings.examples_top_k, help="worked examples handed to the model")
    p.add_argument(
        "--snippets",
        action=argparse.BooleanOptionalAction,
        default=settings.snippets_enabled,
        help=f"retrieve verified SQL snippets -- joins, filters, measures -- for the question {_default(settings.snippets_enabled)}",
    )
    p.add_argument("--snippet-db-url", default=settings.snippet_db_url, help="snippet store URL")
    p.add_argument("--snippets-top-k", type=int, default=settings.snippets_top_k, help="snippets shown to the model, at most")
    p.add_argument(
        "--ensemble",
        action=argparse.BooleanOptionalAction,
        default=settings.ensemble_enabled,
        help=f"ask the question through the ensemble, arch7 {_default(settings.ensemble_enabled)}",
    )
    p.add_argument(
        "--paraphrases",
        type=int,
        default=settings.ensemble_paraphrases,
        help=f"rewordings in the ensemble's first wave, 3 to 10 (default: {settings.ensemble_paraphrases})",
    )
    p.add_argument(
        "--parallel-calls",
        type=int,
        default=settings.ollama_parallel_calls,
        help=f"model calls in flight to the Ollama host at once (default: {settings.ollama_parallel_calls})",
    )
    p.add_argument(
        "--fuse-columns",
        action=argparse.BooleanOptionalAction,
        default=settings.ensemble_fuse_columns,
        help=f"widen the chosen rows with columns other agreeing runs carried {_default(settings.ensemble_fuse_columns)}",
    )
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
    settings.snippets_enabled = args.snippets
    settings.snippet_db_url = args.snippet_db_url
    settings.snippets_top_k = args.snippets_top_k
    settings.ensemble_enabled = args.ensemble
    settings.ensemble_paraphrases = args.paraphrases
    settings.ollama_parallel_calls = args.parallel_calls
    settings.ensemble_fuse_columns = args.fuse_columns
    # A flag is set after the settings were checked; check them again.
    settings.validate()
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


def answer(agent: Any, question: str, *, as_json: bool, quiet: bool) -> int:
    with tracing.tagged({"nl2sql.entrypoint": "cli"}):
        state = agent.run(question)
    if as_json:
        print(json.dumps(to_jsonable(as_document(question, state)), indent=2, default=str))
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
    # Under the ensemble (arch7) the answer opens with how its runs agreed.
    line = getattr(state.get("decision"), "line", "")
    if line:
        print(line)
        print()
    narrative = (state.get("narrative") or "").strip()
    if narrative:
        print(narrative)
        print()
    print(format_rows(state.get("result")))
    return 0


def as_document(question: str, state: dict[str, Any]) -> dict[str, Any]:
    """What `--json` prints: the run, and under the ensemble the record of it.

    The run's own fields -- what retrieval found, the attempts -- are the
    delivered run's; the answer's are the question's. Under the ensemble the
    trace is the outer nodes' followed by the delivered run's, as the REST
    answer gives it, and `ensemble` carries the vote and every candidate
    whole, each with its own state.
    """
    run = run_state(state)
    document = {
        "question": question,
        "verdict": state.get("verdict"),
        "intent": state.get("intent"),
        "answer_contract": state.get("answer_contract"),
        "knowledge_chunks": run.get("knowledge_chunks", []),
        "example_pairs": run.get("example_pairs", []),
        "snippet_hits": run.get("snippet_hits", []),
        "literal_map": run.get("literal_map", []),
        "retrieval_errors": run.get("retrieval_errors", {}),
        "node_errors": {**(run.get("node_errors") or {}), **(state.get("node_errors") or {})},
        "selected_tables": run.get("selected_tables", []),
        "sql": state.get("sql"),
        "attempts": run.get("attempts"),
        "plan_cost": run.get("plan_cost"),
        "attempt_history": run.get("attempt_history", []),
        "error": state.get("error"),
        "result": state.get("result"),
        "completeness": run.get("completeness"),
        "assumptions": state.get("assumptions", []),
        "chart": state.get("chart"),
        "claims": state.get("claims", []),
        "narrative": state.get("narrative"),
        "audit": state.get("audit"),
        "answer": state.get("answer"),
        "trace": list(state.get("trace", [])) + (list(run.get("trace", [])) if run is not state else []),
        "trace_id": state.get("trace_id", ""),
    }
    if is_ensemble(state):
        document["ensemble"] = {
            "agreement": state.get("agreement"),
            "decision": state.get("decision"),
            "waves": state.get("waves"),
            "paraphrases": state.get("paraphrases", []),
            "candidates": state.get("candidates", []),
        }
    return document


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        settings = settings_from_args(args)
    except ValueError as exc:
        # A setting out of range, from the environment or a flag: said by
        # name, with its bound, rather than as a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def on_progress(step: str, detail: str, candidate: int | None = None) -> None:
        if args.quiet or args.json:
            return
        # A candidate's step says which wording's run it was (arch7).
        which = f"[{candidate}] " if candidate is not None else ""
        print(f"{which}[{step_label(step, candidate)}] {detail}", file=sys.stderr)

    try:
        agent = build_agent(settings, on_progress=on_progress)
    except (LlmUnavailableError, RoutingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    routing = agent.router.table.lines()
    # Connected now rather than on the first question, so whether runs are
    # traced is said before one is -- and said only when tracing was asked
    # for: with MLFLOW_TRACKING_URI unset the CLI prints what it always has.
    agent.tracer.ready()
    if args.question:
        if not (args.quiet or args.json):
            # One line: which models a single question may be routed to.
            print(f"[routing] {routing[0].removeprefix('model routing ')}", file=sys.stderr)
            if agent.tracer.enabled:
                print(f"[tracing] {agent.tracer.status}", file=sys.stderr)
        return answer(agent, " ".join(args.question), as_json=args.json, quiet=args.quiet)

    print(f"Connected to {settings.ollama_model} at {settings.ollama_base_url}.")
    print("\n".join(routing))
    if agent.tracer.enabled:
        print(f"Traces: {agent.tracer.status}")
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
    if settings.snippets_enabled:
        print(
            f"SQL snippets: by keyword phrase and by meaning over {settings.snippet_db_url}, "
            f"up to {settings.snippets_top_k} shown when their tables are in scope"
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
