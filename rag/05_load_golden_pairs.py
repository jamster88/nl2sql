#!/usr/bin/env python3
"""Step 5: load the golden question/SQL pairs into the context store.

`context_questions/translated_questions.md` is not chunked the way the
`knowledge/` documents are. Every pair already carries the same eight fields, so
it lands in a real table -- one row per pair, one column per field -- and the
keyword column gets a BM25 index built alongside it.

Re-running is safe: rows are upserted on `chunk_id`, pairs deleted from the
document are removed, and the BM25 statistics are rebuilt from whatever ends up
in the table.

Examples:
    python 05_load_golden_pairs.py
    python 05_load_golden_pairs.py ../context_questions/translated_questions.md
    python 05_load_golden_pairs.py --dry-run
    python 05_load_golden_pairs.py --probe "gross margin for produce"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ragproc import golden_pairs as gp
from ragproc.config import Settings, document_slug

DEFAULT_DOCUMENT = Path(__file__).resolve().parent.parent / "context_questions" / "translated_questions.md"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Settings.from_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "document",
        nargs="?",
        default=str(DEFAULT_DOCUMENT),
        help="the translated questions markdown file",
    )
    p.add_argument("--db-url", default=defaults.chunk_db_url, help="context store connection URL")
    p.add_argument("--k1", type=float, default=gp.DEFAULT_K1, help="BM25 term-frequency saturation")
    p.add_argument("--b", type=float, default=gp.DEFAULT_B, help="BM25 length normalisation")
    p.add_argument("--dry-run", action="store_true", help="parse and report without writing")
    p.add_argument(
        "--probe",
        metavar="QUESTION",
        help="after loading, run this text through the BM25 ranker and show the top matches",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.document)
    if not path.is_file():
        raise SystemExit(f"error: not found: {path}")

    pairs = gp.parse_document(path)
    suites = len({p.suite for p in pairs})
    print(f"{path} -> {gp.TABLE}: {len(pairs)} pairs across {suites} suites")
    print(
        "  fields: chunk_id, type, tables, keywords, question, "
        "reasoning_target, sql_code, result"
    )

    if args.dry_run:
        for pair in pairs[:3]:
            print(f"  {pair.pair_id} {pair.chunk_id}: {pair.title}")
        print("  ...")
        print("\ndry run: nothing written")
        return 0

    conn = gp.connect(args.db_url)
    try:
        gp.ensure_tables(conn)
        written = gp.upsert_pairs(conn, pairs, source_doc=document_slug(path.name))
        removed = gp.delete_missing(conn, [p.chunk_id for p in pairs])
        print(f"  {written} rows written, {removed} stale rows removed")

        stats = gp.rebuild_bm25_index(conn, k1=args.k1, b=args.b)
        gp.ensure_bm25_function(conn)
        print(
            f"  BM25 over {gp.TS_CONFIG} lexemes: {stats['documents']} documents, "
            f"{stats['distinct_terms']} distinct terms, "
            f"avg keyword length {stats['avg_doc_len']} (k1={args.k1}, b={args.b})"
        )

        if args.probe:
            rows = conn.execute(
                f"""
                SELECT b.chunk_id, g.pair_id, round(b.score::numeric, 4), g.title
                FROM {gp.BM25_FUNCTION}(%s) b
                JOIN {gp.TABLE} g USING (chunk_id)
                ORDER BY b.score DESC LIMIT 5
                """,
                (args.probe,),
            ).fetchall()
            print(f"\n  BM25 probe {args.probe!r}:")
            for chunk_id, pair_id, score, title in rows:
                print(f"    {score:>8}  {pair_id}  {title}")
            if not rows:
                print("    (no keyword overlap)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
