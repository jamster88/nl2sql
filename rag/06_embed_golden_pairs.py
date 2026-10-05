#!/usr/bin/env python3
"""Step 6: embed the golden pairs' question and reasoning_target columns.

Reads the rows written by `05_load_golden_pairs.py` from the context store and
writes two independent pgvector tables -- one for each embedded field -- so the
ensemble retriever can score them separately and weight them differently.

Re-running is incremental: a pair is re-embedded only when its content hash or
the embedding model changed. The work is `ragproc.loaders.embed_golden_pairs`,
which the review service calls itself after a promotion.

Examples:
    python 06_embed_golden_pairs.py
    python 06_embed_golden_pairs.py --field question
    python 06_embed_golden_pairs.py --probe "gross margin by department"
    python 06_embed_golden_pairs.py --ollama-url http://host:11434
"""

from __future__ import annotations

import argparse
import sys

from ragproc import loaders
from ragproc.config import Settings
from ragproc.embedder import build_embedder

FIELDS = loaders.PAIR_FIELDS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Settings.from_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--field", choices=FIELDS, action="append", help="embed only this field (repeatable)")
    p.add_argument("--chunk-db-url", default=defaults.chunk_db_url, help="context store URL")
    p.add_argument("--vector-db-url", default=defaults.vector_db_url, help="vector store URL")
    p.add_argument("--backend", default=defaults.embed_backend, choices=["ollama", "sentence-transformers"])
    p.add_argument("--model", default=defaults.embed_model)
    p.add_argument("--ollama-url", default=defaults.ollama_url)
    p.add_argument("--batch-size", type=int, default=defaults.embed_batch_size)
    p.add_argument("--force", action="store_true", help="re-embed every row even if unchanged")
    p.add_argument("--probe", metavar="QUESTION", help="after embedding, show nearest pairs for this text")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fields = tuple(dict.fromkeys(args.field)) if args.field else FIELDS
    embedder = build_embedder(args.backend, args.model, args.ollama_url)
    try:
        report = loaders.embed_golden_pairs(
            args.chunk_db_url,
            args.vector_db_url,
            embedder,
            fields=fields,
            batch_size=args.batch_size,
            force=args.force,
            probe=args.probe,
        )
    except loaders.NothingToEmbed as exc:
        raise SystemExit(f"error: {exc}") from exc
    print(f"{report.pairs} golden pairs in the context store")
    print(f"embedding with {args.backend}:{report.model} ({report.dimension} dimensions)")
    for done in report.fields:
        print(
            f"  {done.field:16s} -> {done.table}: {done.written} embedded, "
            f"{report.pairs - done.written} already current, {done.removed} removed"
        )
    for name, hits in report.probe.items():
        print(f"\n  nearest by {name} for {args.probe!r}:")
        for hit in hits:
            print(f"    {hit['distance']:.4f}  {hit['pair_id']}  {hit['content'][:72]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
