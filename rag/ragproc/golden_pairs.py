"""Parsing and storage for the golden question/SQL pairs.

`context_questions/translated_questions.md` holds 45 natural-language questions
paired with verified PostgreSQL. Unlike the documents in `knowledge/`, these are
not prose to be semantically chunked: every pair already has exactly the same
eight fields, and the `##` heading boundary is the record boundary. So they get
a real relational table rather than a `<doc>_chunks` table of free text.

Two things are built here that the knowledge pipeline has no need for:

1. **A structured table.** `chunk_id`, `type`, `tables`, `keywords`, `question`,
   `reasoning_target`, `sql_code` and `result` become columns, so the retriever
   can search one field and return another -- search the question, hand the
   model the SQL.

2. **A BM25 index over the keyword column.** Postgres ships `ts_rank`, which is
   a length-normalised tf-idf and not BM25; the ranking the ensemble asks for is
   specifically BM25, so the term statistics are materialised here and the
   scoring function is written out in SQL. Over 45 short documents this costs
   nothing to maintain and keeps the ranking in the database, next to the data.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import sql

TABLE = "golden_pairs"
TERMS_TABLE = "golden_pair_keyword_terms"
STATS_TABLE = "golden_pair_keyword_stats"
DOCS_TABLE = "golden_pair_keyword_docs"
PARAMS_TABLE = "golden_pair_bm25_params"
BM25_FUNCTION = "golden_pairs_bm25"

# The text search configuration used for BM25. It stems and drops stopwords, so
# a question asking about "monthly costs" reaches a pair keyworded "monthly
# cost". Indexing and querying must use the same configuration or nothing
# matches, which is why it lives here as one constant.
TS_CONFIG = "english"

# Okapi BM25 defaults. k1 damps term-frequency saturation, b controls how much
# a long keyword list is penalised.
DEFAULT_K1 = 1.2
DEFAULT_B = 0.75

SUITE_RE = re.compile(r"^# (Suite .+?)\s*$", re.M)
ENTRY_RE = re.compile(
    r"^## (?P<pair_id>Q\d{2}) - (?P<title>.+?)\n"
    r"\n```meta\n(?P<meta>.*?)\n```\n"
    r"\n\*\*Question:\*\* \"(?P<question>.*?)\"\n"
    r"\n\*\*Reasoning target:\*\* (?P<reasoning_target>.*?)\n"
    r"\n```sql\n(?P<sql_code>.*?)\n```\n"
    r"\n\*\*Result:\*\* (?P<result>.*?)\n"
    r"\n\*\*Translation note:\*\* (?P<note>.*?)\n",
    re.S | re.M,
)
META_KV_RE = re.compile(r"^(\w[\w.-]*):\s*(.*)$")


@dataclass
class GoldenPair:
    pair_id: str  # Q01 .. Q45
    chunk_id: str  # eval:q01 .. eval:q45, from the meta block
    title: str
    suite: str
    ordinal: int
    type: str
    tables: str
    keywords: str
    question: str
    reasoning_target: str
    sql_code: str
    result: str
    translation_note: str
    meta: dict = field(default_factory=dict)

    @property
    def table_list(self) -> list[str]:
        return [t.strip() for t in self.tables.split(",") if t.strip()]

    @property
    def keyword_list(self) -> list[str]:
        return [k.strip() for k in self.keywords.split(",") if k.strip()]

    @property
    def content_hash(self) -> str:
        """Hash of every field that is loaded, so an edit anywhere is detected."""
        payload = "\x1f".join(
            [
                self.chunk_id,
                self.type,
                self.tables,
                self.keywords,
                self.question,
                self.reasoning_target,
                self.sql_code,
                self.result,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_meta(block: str) -> dict:
    meta = {}
    for line in block.splitlines():
        match = META_KV_RE.match(line.strip())
        if match:
            meta[match.group(1)] = match.group(2).strip()
    return meta


def _suite_at(text: str, position: int) -> str:
    """The `# Suite ...` heading in force at this offset in the document."""
    current = ""
    for match in SUITE_RE.finditer(text):
        if match.start() > position:
            break
        current = match.group(1).strip()
    return current


def parse_document(path: Path) -> list[GoldenPair]:
    """Every question/SQL pair in the markdown, in document order.

    Strict by design: a pair missing any of the eight fields does not match the
    entry pattern at all, and the count check below turns that into a loud
    failure rather than a quietly short load.
    """
    text = path.read_text()
    pairs: list[GoldenPair] = []
    for ordinal, match in enumerate(ENTRY_RE.finditer(text), start=1):
        meta = parse_meta(match.group("meta"))
        missing = [k for k in ("chunk_id", "type", "tables", "keywords") if not meta.get(k)]
        if missing:
            raise ValueError(
                f"{match.group('pair_id')} has a meta block missing {', '.join(missing)}"
            )
        pairs.append(
            GoldenPair(
                pair_id=match.group("pair_id"),
                chunk_id=meta["chunk_id"],
                title=match.group("title").strip(),
                suite=_suite_at(text, match.start()),
                ordinal=ordinal,
                type=meta["type"],
                tables=meta["tables"],
                keywords=meta["keywords"],
                question=match.group("question").strip(),
                reasoning_target=match.group("reasoning_target").strip(),
                sql_code=match.group("sql_code").strip(),
                result=match.group("result").strip(),
                translation_note=match.group("note").strip(),
                meta=meta,
            )
        )

    headings = len(re.findall(r"^## Q\d{2} - ", text, re.M))
    if headings != len(pairs):
        raise ValueError(
            f"{path.name} has {headings} pair headings but only {len(pairs)} parsed "
            "cleanly -- a pair is missing one of its labelled fields"
        )
    return pairs


# --- storage ---------------------------------------------------------------


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url)


def ensure_tables(conn: psycopg.Connection) -> None:
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                chunk_id         TEXT PRIMARY KEY,
                pair_id          TEXT NOT NULL UNIQUE,
                source_doc       TEXT NOT NULL,
                ordinal          INT  NOT NULL,
                suite            TEXT NOT NULL DEFAULT '',
                title            TEXT NOT NULL DEFAULT '',
                type             TEXT NOT NULL,
                tables           TEXT NOT NULL,
                keywords         TEXT NOT NULL,
                question         TEXT NOT NULL,
                reasoning_target TEXT NOT NULL,
                sql_code         TEXT NOT NULL,
                result           TEXT NOT NULL,
                translation_note TEXT NOT NULL DEFAULT '',
                chunk_meta       JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                content_hash     TEXT NOT NULL,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(TABLE))
    )
    # A generated tsvector keeps the searchable form of the keywords beside the
    # text itself, so the two can never drift apart.
    conn.execute(
        sql.SQL(
            """
            ALTER TABLE {} ADD COLUMN IF NOT EXISTS keywords_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector({}, keywords)) STORED
            """
        ).format(sql.Identifier(TABLE), sql.Literal(TS_CONFIG))
    )
    conn.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING gin (keywords_tsv)").format(
            sql.Identifier(f"idx_{TABLE}_keywords"), sql.Identifier(TABLE)
        )
    )

    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                chunk_id TEXT NOT NULL REFERENCES {} (chunk_id) ON DELETE CASCADE,
                term     TEXT NOT NULL,
                tf       INT  NOT NULL,
                PRIMARY KEY (chunk_id, term)
            )
            """
        ).format(sql.Identifier(TERMS_TABLE), sql.Identifier(TABLE))
    )
    conn.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (term)").format(
            sql.Identifier(f"idx_{TERMS_TABLE}_term"), sql.Identifier(TERMS_TABLE)
        )
    )
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                term TEXT PRIMARY KEY,
                df   INT NOT NULL,
                idf  DOUBLE PRECISION NOT NULL
            )
            """
        ).format(sql.Identifier(STATS_TABLE))
    )
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                chunk_id TEXT PRIMARY KEY REFERENCES {} (chunk_id) ON DELETE CASCADE,
                doc_len  INT NOT NULL
            )
            """
        ).format(sql.Identifier(DOCS_TABLE), sql.Identifier(TABLE))
    )
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                only_row    BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (only_row),
                doc_count   INT NOT NULL,
                avg_doc_len DOUBLE PRECISION NOT NULL,
                k1          DOUBLE PRECISION NOT NULL,
                b           DOUBLE PRECISION NOT NULL,
                ts_config   TEXT NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(PARAMS_TABLE))
    )
    conn.commit()


def upsert_pairs(conn: psycopg.Connection, pairs: list[GoldenPair], source_doc: str) -> int:
    import json

    for pair in pairs:
        conn.execute(
            sql.SQL(
                """
                INSERT INTO {} (chunk_id, pair_id, source_doc, ordinal, suite, title,
                                type, tables, keywords, question, reasoning_target,
                                sql_code, result, translation_note, chunk_meta, content_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    pair_id          = EXCLUDED.pair_id,
                    source_doc       = EXCLUDED.source_doc,
                    ordinal          = EXCLUDED.ordinal,
                    suite            = EXCLUDED.suite,
                    title            = EXCLUDED.title,
                    type             = EXCLUDED.type,
                    tables           = EXCLUDED.tables,
                    keywords         = EXCLUDED.keywords,
                    question         = EXCLUDED.question,
                    reasoning_target = EXCLUDED.reasoning_target,
                    sql_code         = EXCLUDED.sql_code,
                    result           = EXCLUDED.result,
                    translation_note = EXCLUDED.translation_note,
                    chunk_meta       = EXCLUDED.chunk_meta,
                    content_hash     = EXCLUDED.content_hash,
                    updated_at       = now()
                """
            ).format(sql.Identifier(TABLE)),
            (
                pair.chunk_id,
                pair.pair_id,
                source_doc,
                pair.ordinal,
                pair.suite,
                pair.title,
                pair.type,
                pair.tables,
                pair.keywords,
                pair.question,
                pair.reasoning_target,
                pair.sql_code,
                pair.result,
                pair.translation_note,
                json.dumps(pair.meta),
                pair.content_hash,
            ),
        )
    conn.commit()
    return len(pairs)


def delete_missing(conn: psycopg.Connection, keep_ids: list[str]) -> int:
    result = conn.execute(
        sql.SQL("DELETE FROM {} WHERE NOT (chunk_id = ANY(%s))").format(sql.Identifier(TABLE)),
        (keep_ids,),
    )
    conn.commit()
    return result.rowcount or 0


def rebuild_bm25_index(
    conn: psycopg.Connection, k1: float = DEFAULT_K1, b: float = DEFAULT_B
) -> dict:
    """Recompute term frequencies, document lengths and IDF from scratch.

    Cheap enough at this corpus size to always rebuild rather than track deltas,
    and a full rebuild cannot leave the statistics disagreeing with the rows.
    """
    conn.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(TERMS_TABLE)))
    conn.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(STATS_TABLE)))
    conn.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(DOCS_TABLE)))

    # Term frequency is the number of positions the lexeme occupies in the
    # keyword tsvector, which is exactly how often the term was written.
    conn.execute(
        sql.SQL(
            """
            INSERT INTO {} (chunk_id, term, tf)
            SELECT g.chunk_id, v.lexeme, GREATEST(COALESCE(array_length(v.positions, 1), 1), 1)
            FROM {} g, unnest(g.keywords_tsv) AS v
            """
        ).format(sql.Identifier(TERMS_TABLE), sql.Identifier(TABLE))
    )
    conn.execute(
        sql.SQL(
            """
            INSERT INTO {} (chunk_id, doc_len)
            SELECT chunk_id, COALESCE(SUM(tf), 0) FROM {} GROUP BY chunk_id
            """
        ).format(sql.Identifier(DOCS_TABLE), sql.Identifier(TERMS_TABLE))
    )
    # Robertson/Sparck-Jones IDF with the +1 smoothing, which keeps the score
    # non-negative even for a term that appears in every document.
    conn.execute(
        sql.SQL(
            """
            INSERT INTO {stats} (term, df, idf)
            SELECT t.term,
                   COUNT(DISTINCT t.chunk_id) AS df,
                   ln(1.0 + ((SELECT COUNT(*) FROM {docs}) - COUNT(DISTINCT t.chunk_id) + 0.5)
                             / (COUNT(DISTINCT t.chunk_id) + 0.5)) AS idf
            FROM {terms} t
            GROUP BY t.term
            """
        ).format(
            stats=sql.Identifier(STATS_TABLE),
            docs=sql.Identifier(DOCS_TABLE),
            terms=sql.Identifier(TERMS_TABLE),
        )
    )

    row = conn.execute(
        sql.SQL("SELECT COUNT(*), COALESCE(AVG(doc_len), 0) FROM {}").format(
            sql.Identifier(DOCS_TABLE)
        )
    ).fetchone()
    doc_count, avg_doc_len = int(row[0]), float(row[1])
    conn.execute(
        sql.SQL(
            """
            INSERT INTO {} (only_row, doc_count, avg_doc_len, k1, b, ts_config)
            VALUES (TRUE, %s, %s, %s, %s, %s)
            ON CONFLICT (only_row) DO UPDATE SET
                doc_count = EXCLUDED.doc_count, avg_doc_len = EXCLUDED.avg_doc_len,
                k1 = EXCLUDED.k1, b = EXCLUDED.b, ts_config = EXCLUDED.ts_config,
                updated_at = now()
            """
        ).format(sql.Identifier(PARAMS_TABLE)),
        (doc_count, avg_doc_len or 1.0, k1, b, TS_CONFIG),
    )
    conn.commit()

    terms = conn.execute(
        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(STATS_TABLE))
    ).fetchone()[0]
    return {
        "documents": doc_count,
        "distinct_terms": int(terms),
        "avg_doc_len": round(avg_doc_len, 3),
    }


def ensure_bm25_function(conn: psycopg.Connection) -> None:
    """Install the BM25 scoring function.

    Kept in the database rather than assembled in the client so that the
    ranking can be inspected, explained and tested with psql alone, and so the
    published image carries its own ranker.
    """
    conn.execute(
        sql.SQL(
            """
            CREATE OR REPLACE FUNCTION {fn}(query_text TEXT)
            RETURNS TABLE (chunk_id TEXT, score DOUBLE PRECISION)
            LANGUAGE sql STABLE AS $fn$
                WITH p AS (SELECT * FROM {params} WHERE only_row),
                q AS (
                    SELECT DISTINCT lexeme AS term
                    FROM unnest(to_tsvector({cfg}, query_text))
                )
                SELECT t.chunk_id,
                       SUM(
                           s.idf * (t.tf * (p.k1 + 1.0))
                           / (t.tf + p.k1 * (1.0 - p.b + p.b * d.doc_len / p.avg_doc_len))
                       )::double precision AS score
                FROM q
                JOIN {terms} t ON t.term = q.term
                JOIN {stats} s ON s.term = q.term
                JOIN {docs}  d ON d.chunk_id = t.chunk_id
                CROSS JOIN p
                GROUP BY t.chunk_id
            $fn$
            """
        ).format(
            fn=sql.Identifier(BM25_FUNCTION),
            params=sql.Identifier(PARAMS_TABLE),
            terms=sql.Identifier(TERMS_TABLE),
            stats=sql.Identifier(STATS_TABLE),
            docs=sql.Identifier(DOCS_TABLE),
            cfg=sql.Literal(TS_CONFIG),
        )
    )
    conn.commit()


def load_pairs(conn: psycopg.Connection) -> list[dict]:
    """Every stored pair, in document order -- used to drive embedding."""
    rows = conn.execute(
        sql.SQL(
            "SELECT chunk_id, pair_id, ordinal, question, reasoning_target, content_hash "
            "FROM {} ORDER BY ordinal"
        ).format(sql.Identifier(TABLE))
    ).fetchall()
    return [
        {
            "chunk_id": r[0],
            "pair_id": r[1],
            "ordinal": r[2],
            "question": r[3],
            "reasoning_target": r[4],
            "content_hash": r[5],
        }
        for r in rows
    ]
