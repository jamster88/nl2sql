#!/usr/bin/env python3
"""Step 4: read a document's chunks from Postgres, embed them, store vectors.

Reads from the chunk store written by 02_chunk_document.py and writes to the
pgvector instance, one table per document: business_index -> business_index_embeddings.

Embedding defaults to bge-m3 served by Ollama on the local machine, which
avoids downloading a second copy of the weights. Any Ollama embedding model
works via --model, and --backend sentence-transformers uses local weights
instead.

Re-running is incremental: a chunk already embedded with the same model is
skipped, and vectors whose chunk no longer exists are deleted.

Examples:
    python 04_embed_document.py --all
    python 04_embed_document.py business_index ddl_index
    python 04_embed_document.py --all --model nomic-embed-text
    python 04_embed_document.py --all --force
"""

from __future__ import annotations

import argparse
import sys

from ragproc import chunk_store, vector_store
from ragproc.config import Settings, document_slug
from ragproc.embedder import build_embedder


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Settings.from_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("documents", nargs="*", help="document slugs or file names; omit with --all")
    p.add_argument("--all", action="store_true", help="every document registered in the chunk store")
    p.add_argument("--chunk-db-url", default=defaults.chunk_db_url)
    p.add_argument("--vector-db-url", default=defaults.vector_db_url)
    p.add_argument("--backend", default=defaults.embed_backend, choices=["ollama", "sentence-transformers"])
    p.add_argument("--model", default=defaults.embed_model, help="embedding model (default: bge-m3)")
    p.add_argument("--ollama-url", default=defaults.ollama_url)
    p.add_argument("--batch-size", type=int, default=defaults.embed_batch_size)
    p.add_argument("--force", action="store_true", help="re-embed every chunk, even unchanged ones")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    chunk_conn = chunk_store.connect(args.chunk_db_url)
    try:
        if args.all:
            slugs = [d["slug"] for d in chunk_store.known_documents(chunk_conn)]
            if not slugs:
                print("no documents in the chunk store -- run 02_chunk_document.py first", file=sys.stderr)
                return 1
        elif args.documents:
            slugs = [document_slug(d) for d in args.documents]
        else:
            print("error: name at least one document, or pass --all", file=sys.stderr)
            return 2

        embedder = build_embedder(args.backend, args.model, args.ollama_url)
        if hasattr(embedder, "check"):
            embedder.check()
        dimension = embedder.dimension
        print(f"embedding with {args.model} via {args.backend} ({dimension} dimensions)")

        vector_conn = vector_store.connect(args.vector_db_url)
        try:
            for slug in slugs:
                chunks = chunk_store.fetch_chunks(chunk_conn, slug)
                if not chunks:
                    print(f"{slug}: no chunks found, skipping")
                    continue

                vector_store.ensure_embedding_table(vector_conn, slug, dimension)
                already = vector_store.current_state(vector_conn, slug)

                todo = [
                    c for c in chunks
                    if args.force or already.get(c["chunk_id"]) != args.model
                ]
                stats = vector_store.EmbedStats(skipped=len(chunks) - len(todo))

                for start in range(0, len(todo), args.batch_size):
                    batch = todo[start : start + args.batch_size]
                    vectors = embedder.embed([c["content"] for c in batch])
                    if len(vectors) != len(batch):
                        raise RuntimeError(
                            f"expected {len(batch)} vectors, got {len(vectors)}"
                        )
                    records = [dict(c, embedding=v) for c, v in zip(batch, vectors)]
                    stats.embedded += vector_store.upsert_embeddings(
                        vector_conn, slug, records, args.model
                    )
                    print(f"  {slug}: {stats.embedded}/{len(todo)}", end="\r", flush=True)

                stats.deleted = vector_store.delete_missing(
                    vector_conn, slug, [c["chunk_id"] for c in chunks]
                )
                print(f"{slug}_embeddings: {stats}".ljust(60))
        finally:
            vector_conn.close()
    finally:
        chunk_conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
