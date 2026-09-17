#!/usr/bin/env python3
"""Step 2: load a document, parse it, semantically chunk it, store the chunks.

Each document gets its own table in the chunk-store Postgres instance, named
after the file: knowledge/business_index.md -> business_index_chunks.

Re-running is incremental. Chunk ids are content hashes, so unchanged chunks
keep their id and only genuinely new text is inserted.

Examples:
    python 02_chunk_document.py knowledge/business_index.md
    python 02_chunk_document.py --all knowledge/
    python 02_chunk_document.py --all knowledge/ --max-chunk-tokens 350
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ragproc import chunk_store
from ragproc.chunker import MarkdownSemanticChunker
from ragproc.config import Settings, document_slug
from ragproc.embedder import build_embedder


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Settings.from_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("documents", nargs="+", help="markdown files, or a directory with --all")
    p.add_argument("--all", action="store_true", help="treat arguments as directories and take every .md inside")
    p.add_argument("--db-url", default=defaults.chunk_db_url, help="chunk store connection URL")
    p.add_argument("--max-chunk-tokens", type=int, default=defaults.max_chunk_tokens)
    p.add_argument("--min-chunk-tokens", type=int, default=defaults.min_chunk_tokens)
    p.add_argument("--threshold-percentile", type=int, default=defaults.threshold_percentile)
    p.add_argument("--split-level", type=int, default=2, help="heading level that starts a new section")
    p.add_argument("--backend", default=defaults.embed_backend, choices=["ollama", "sentence-transformers"],
                   help="embedding backend used only when a section must be split semantically")
    p.add_argument("--embed-model", default=defaults.embed_model)
    p.add_argument("--ollama-url", default=defaults.ollama_url)
    p.add_argument("--dry-run", action="store_true", help="chunk and report without writing to the database")
    return p.parse_args(argv)


def resolve_documents(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for raw in args.documents:
        path = Path(raw)
        if args.all or path.is_dir():
            if not path.is_dir():
                raise SystemExit(f"error: {path} is not a directory")
            paths.extend(sorted(path.glob("*.md")))
        else:
            paths.append(path)
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise SystemExit("error: not found: " + ", ".join(str(m) for m in missing))
    if not paths:
        raise SystemExit("error: no markdown documents matched")
    return paths


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    documents = resolve_documents(args)

    # Only built lazily: most sections fit inside max_chunk_tokens and never
    # need drift detection, so nothing is embedded at chunk time.
    embedder_holder: dict = {}

    def embed_fn(texts: list[str]) -> list[list[float]]:
        if "e" not in embedder_holder:
            print(f"  (semantic split needed -- loading {args.backend}:{args.embed_model})")
            embedder_holder["e"] = build_embedder(args.backend, args.embed_model, args.ollama_url)
        return embedder_holder["e"].embed(texts)

    chunker = MarkdownSemanticChunker(
        embed_fn=embed_fn,
        threshold_percentile=args.threshold_percentile,
        max_chunk_tokens=args.max_chunk_tokens,
        min_chunk_tokens=args.min_chunk_tokens,
        split_level=args.split_level,
    )

    settings_used = {
        "max_chunk_tokens": args.max_chunk_tokens,
        "min_chunk_tokens": args.min_chunk_tokens,
        "threshold_percentile": args.threshold_percentile,
        "split_level": args.split_level,
    }

    conn = None if args.dry_run else chunk_store.connect(args.db_url)
    try:
        for path in documents:
            slug = document_slug(path.name)
            chunks = chunker.chunk_document(path.read_text(encoding="utf-8"))
            tokens = [c.token_estimate for c in chunks] or [0]
            print(
                f"{path} -> {slug}_chunks: {len(chunks)} chunks "
                f"(tokens min={min(tokens)} max={max(tokens)} avg={sum(tokens)//len(tokens)})"
            )
            if conn is None:
                continue
            stats = chunk_store.store_chunks(conn, slug, path, chunks, settings_used)
            print(f"  {stats}")
    finally:
        if conn is not None:
            conn.close()

    if args.dry_run:
        print("\ndry run: nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
