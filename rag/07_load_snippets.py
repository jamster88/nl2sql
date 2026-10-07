#!/usr/bin/env python3
"""Step 7: load the SQL snippets into the snippet store, and embed them.

`context_questions/sql_snippets.md` holds pieces of SQL -- joins, filters,
measures, dimensions -- each with what it means in a question's words. They
land in their own store, apart from the golden pairs: one Postgres with
pgvector holding the rows, a keyword matcher over their names and keywords,
an embedding of each snippet's meaning, and a record of which document the
store was loaded from.

One script rather than two, unlike the golden pairs' steps 5 and 6, because
it is one store. Re-running is safe: rows are upserted on `chunk_id`, snippets
deleted from the document are removed with their vectors, and a snippet is
re-embedded only when its content or the embedding model changed. The load
record is written last, and only when every snippet has a current vector, so
a load the embedding host interrupted is one the next start finishes.

It also (re)creates the role the agent reads the store as, which can SELECT
and nothing else. The work is `ragproc.loaders.load_snippets`, which the review
service calls itself after a curator changes the document.

Examples:
    python 07_load_snippets.py
    python 07_load_snippets.py ../context_questions/sql_snippets.md --dry-run
    python 07_load_snippets.py --no-embed
    python 07_load_snippets.py --probe "net sales by department in fiscal 2024"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ragproc import loaders
from ragproc import snippets as sn
from ragproc.config import Settings
from ragproc.embedder import build_embedder

from nl2sql_common.env import env_url, secret

DEFAULT_DOCUMENT = Path(__file__).resolve().parent.parent / "context_questions" / "sql_snippets.md"
DEFAULT_DB_URL = "postgresql://snippets:snippets@localhost:5435/nl2sql_snippets"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Settings.from_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("document", nargs="?", default=str(DEFAULT_DOCUMENT), help="the snippet markdown file")
    p.add_argument(
        "--db-url",
        default=env_url("SNIPPETS_DB_URL", DEFAULT_DB_URL),
        help="snippet store connection URL, as its owner",
    )
    p.add_argument(
        "--reader-role",
        default=os.getenv("SNIPPETS_READER_USER") or "snippets_reader",
        help="the read-only role the agent connects as",
    )
    p.add_argument(
        "--reader-password",
        default=secret("SNIPPETS_READER_PASSWORD") or "snippets_reader",
        help="its password",
    )
    p.add_argument("--backend", default=defaults.embed_backend, choices=["ollama", "sentence-transformers"])
    p.add_argument("--model", default=defaults.embed_model)
    p.add_argument("--ollama-url", default=defaults.ollama_url)
    p.add_argument("--batch-size", type=int, default=defaults.embed_batch_size)
    p.add_argument("--no-embed", action="store_true", help="load the rows and skip the vectors")
    p.add_argument("--force", action="store_true", help="re-embed every snippet even if unchanged")
    p.add_argument("--dry-run", action="store_true", help="parse and report without writing")
    p.add_argument("--probe", metavar="QUESTION", help="after loading, show the snippets nearest this text")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.document)
    if not path.is_file():
        raise SystemExit(f"error: not found: {path}")

    report = loaders.load_snippets(
        path,
        args.db_url,
        reader_role=args.reader_role,
        reader_password=args.reader_password,
        embedder=None if args.no_embed else lambda: build_embedder(args.backend, args.model, args.ollama_url),
        model=args.model,
        batch_size=args.batch_size,
        force=args.force,
        probe=args.probe,
        dry_run=args.dry_run,
    )
    kinds = ", ".join(f"{count} {kind}" for kind, count in report.kinds.items())
    print(f"{path} -> {sn.TABLE}: {report.snippets} snippets ({kinds})")

    if args.dry_run:
        for snippet in sn.parse_text(path.read_text(), path.name)[:3]:
            print(f"  {snippet.snippet_id} {snippet.kind:9s} {snippet.name}")
        print("\ndry run: nothing written")
        return 0

    print(f"  {report.written} rows written, {report.removed} stale rows removed")
    print(f"  role {args.reader_role} can read the store and write nothing")
    if report.embedded is None:
        print("  vectors: skipped (--no-embed); the next load embeds them")
        return 0
    if report.embed_error is not None:
        print(f"  vectors -> {sn.VECTOR_TABLE}: FAILED: {report.embed_error}")
        print("  the rows are loaded and searchable by keyword; the next load embeds them")
        return 1
    print(
        f"  vectors -> {sn.VECTOR_TABLE}: {report.embedded} embedded, "
        f"{report.snippets - report.embedded} already current"
    )
    if args.probe:
        print(f"\n  by keyword for {args.probe!r}:")
        for snippet_id, name, score, matched in report.keyword_probe:
            print(f"    {score:>8.4f}  {snippet_id}  {name}  [{matched}]")
        if not report.keyword_probe:
            print("    (no keyword phrase matched)")
        print(f"\n  by meaning for {args.probe!r}:")
        for snippet_id, name, similarity in report.meaning_probe:
            print(f"    {similarity:>8.4f}  {snippet_id}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
