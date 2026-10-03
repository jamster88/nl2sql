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
and nothing else.

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

from ragproc import snippets as sn
from ragproc.config import Settings, document_slug
from ragproc.embedder import build_embedder

DEFAULT_DOCUMENT = Path(__file__).resolve().parent.parent / "context_questions" / "sql_snippets.md"
DEFAULT_DB_URL = "postgresql://snippets:snippets@localhost:5438/nl2sql_snippets"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Settings.from_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("document", nargs="?", default=str(DEFAULT_DOCUMENT), help="the snippet markdown file")
    p.add_argument(
        "--db-url",
        default=os.getenv("SNIPPETS_DB_URL") or DEFAULT_DB_URL,
        help="snippet store connection URL, as its owner",
    )
    p.add_argument(
        "--reader-role",
        default=os.getenv("SNIPPETS_READER_USER") or "snippets_reader",
        help="the read-only role the agent connects as",
    )
    p.add_argument(
        "--reader-password",
        default=os.getenv("SNIPPETS_READER_PASSWORD") or "snippets_reader",
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

    text = path.read_text()
    snippets = sn.parse_text(text, path.name)
    kinds = ", ".join(
        f"{sum(1 for s in snippets if s.kind == kind)} {kind}" for kind in sn.KINDS
    )
    print(f"{path} -> {sn.TABLE}: {len(snippets)} snippets ({kinds})")

    if args.dry_run:
        for snippet in snippets[:3]:
            print(f"  {snippet.snippet_id} {snippet.kind:9s} {snippet.name}")
        print("\ndry run: nothing written")
        return 0

    source_doc = document_slug(path.name)
    conn = sn.connect(args.db_url)
    try:
        sn.ensure_tables(conn)
        written = sn.upsert_snippets(conn, snippets, source_doc=source_doc)
        removed = sn.delete_missing(conn, [s.chunk_id for s in snippets])
        print(f"  {written} rows written, {removed} stale rows removed")
        sn.ensure_reader(conn, args.reader_role, args.reader_password)
        print(f"  role {args.reader_role} can read the store and write nothing")

        if args.no_embed:
            # The rows are current and the vectors may not be, so the store
            # does not claim to hold this document: the next load embeds.
            sn.forget_load(conn)
            print("  vectors: skipped (--no-embed); the next load embeds them")
            return 0

        # Only what changed is embedded, so a load with nothing new never
        # needs the embedding host at all -- which is what lets every start
        # check the store against the document for the price of one query.
        stored = sn.vector_state(conn)
        pending = [
            s for s in snippets if args.force or stored.get(s.chunk_id) != (s.content_hash, args.model)
        ]
        embedder = build_embedder(args.backend, args.model, args.ollama_url)
        embedded = 0
        try:
            if pending:
                embedder.check()
                sn.ensure_vector_table(conn, embedder.dimension)
                for start in range(0, len(pending), args.batch_size):
                    batch = pending[start : start + args.batch_size]
                    vectors = embedder.embed([s.search_text for s in batch])
                    embedded += sn.upsert_vectors(conn, batch, vectors, args.model)
        except Exception as exc:  # noqa: BLE001 - the rows are loaded; say why the vectors are not
            conn.rollback()
            sn.forget_load(conn)
            print(f"  vectors -> {sn.VECTOR_TABLE}: FAILED: {exc}")
            print("  the rows are loaded and searchable by keyword; the next load embeds them")
            return 1
        print(
            f"  vectors -> {sn.VECTOR_TABLE}: {embedded} embedded, "
            f"{len(snippets) - embedded} already current"
        )
        sn.record_load(
            conn,
            source_doc=source_doc,
            digest=sn.document_hash(path.read_bytes()),
            snippets=len(snippets),
            embedded=len(snippets),
            model=args.model,
        )

        if args.probe:
            print(f"\n  by keyword for {args.probe!r}:")
            hits = sn.search_keywords(conn, args.probe)
            for snippet_id, name, score, matched in hits:
                print(f"    {score:>8.4f}  {snippet_id}  {name}  [{matched}]")
            if not hits:
                print("    (no keyword phrase matched)")
            print(f"\n  by meaning for {args.probe!r}:")
            for snippet_id, name, similarity in sn.search_vectors(conn, embedder.embed([args.probe])[0]):
                print(f"    {similarity:>8.4f}  {snippet_id}  {name}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
