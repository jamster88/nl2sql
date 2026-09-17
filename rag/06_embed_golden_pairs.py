#!/usr/bin/env python3
"""Step 6: embed the golden pairs' question and reasoning_target columns.

Reads the rows written by `05_load_golden_pairs.py` from the context store and
writes two independent pgvector tables -- one for each embedded field -- so the
ensemble retriever can score them separately and weight them differently.

Re-running is incremental: a pair is re-embedded only when its content hash or
the embedding model changed.

Examples:
    python 06_embed_golden_pairs.py
    python 06_embed_golden_pairs.py --field question
    python 06_embed_golden_pairs.py --probe "gross margin by department"
    python 06_embed_golden_pairs.py --ollama-url http://host:11434
"""

from __future__ import annotations

import argparse
import sys

from ragproc import golden_pairs as gp
from ragproc import golden_vectors as gv
from ragproc.config import Settings
from ragproc.embedder import build_embedder

FIELDS = ("question", "reasoning_target")


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

    chunk_conn = gp.connect(args.chunk_db_url)
    try:
        pairs = gp.load_pairs(chunk_conn)
    finally:
        chunk_conn.close()
    if not pairs:
        raise SystemExit(
            f"error: no rows in {gp.TABLE} -- run 05_load_golden_pairs.py first"
        )
    print(f"{len(pairs)} golden pairs in the context store")

    embedder = build_embedder(args.backend, args.model, args.ollama_url)
    if hasattr(embedder, "check"):
        embedder.check()
    dimension = embedder.dimension
    print(f"embedding with {args.backend}:{embedder.model_name} ({dimension} dimensions)")

    conn = gv.connect(args.vector_db_url)
    try:
        for field in fields:
            table = gv.ensure_table(conn, field, dimension)
            stored = gv.current_state(conn, field)

            pending = [
                pair
                for pair in pairs
                if args.force
                or stored.get(pair["chunk_id"]) != (pair["content_hash"], embedder.model_name)
            ]
            records = []
            for start in range(0, len(pending), args.batch_size):
                batch = pending[start : start + args.batch_size]
                vectors = embedder.embed([p[field] for p in batch])
                for pair, vector in zip(batch, vectors):
                    records.append(
                        {
                            "chunk_id": pair["chunk_id"],
                            "pair_id": pair["pair_id"],
                            "ordinal": pair["ordinal"],
                            "content": pair[field],
                            "content_hash": pair["content_hash"],
                            "embedding": vector,
                        }
                    )
            written = gv.upsert(conn, field, records, embedder.model_name)
            removed = gv.delete_missing(conn, field, [p["chunk_id"] for p in pairs])
            print(
                f"  {field:16s} -> {table}: {written} embedded, "
                f"{len(pairs) - written} already current, {removed} removed"
            )

        if args.probe:
            vector = embedder.embed([args.probe])[0]
            for field in fields:
                print(f"\n  nearest by {field} for {args.probe!r}:")
                for hit in gv.search(conn, field, vector, limit=5):
                    print(f"    {hit['distance']:.4f}  {hit['pair_id']}  {hit['content'][:72]}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
