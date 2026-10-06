"""Parsing and storage for the SQL snippets.

`context_questions/sql_snippets.md` holds pieces of SQL -- a join, a filter, a
measure, a dimension -- each paired with what it *means* in the words a
question would use. A golden pair is a whole question and its whole answer; a
snippet is one part a query is built from: "net sales" is `SUM(f.net_sales_amt)`,
"store brand" is `p.is_private_label`, sales reach the fiscal calendar through
`sales_date_key`. The agent's SQL Generator is shown the ones that match a
question, beside the worked examples rather than instead of them.

Like the golden pairs, the document is the source of truth and the store is
built from it: this module parses the document, and `07_load_snippets.py`
loads what it parsed into the snippet store -- the rows, the lexical index
over their names and keywords, and an embedding of what each one means -- and
deletes rows whose snippet is no longer in the document. So a snippet written
straight into the database is gone at the next load, and the review service,
which is what writes snippets, writes the document first.

The format is a contract, and `ENTRY_RE` is it. A snippet is one `##` section:

    ## S01 - Sales on the fiscal calendar

    ```meta
    chunk_id: snippet:s01
    kind: join
    tables: fact_pos_retail_sales, dim_date
    keywords: fiscal year, fiscal month, sales by period
    ```

    **Means:** One line: what it is, and the questions it is for.

    **Applies to:**

    ```sql
    fact_pos_retail_sales f
    ```

    **SQL:**

    ```sql
    JOIN dim_date d ON d.date_key = f.sales_date_key
    ```

    **Note:** One line: where it came from, or the trap it avoids.

`Applies to` is the FROM clause the snippet is written against, aliases and
all; `SQL` is the snippet itself. What the snippet *is* depends on its kind,
and so does the query the review service builds to validate it:

| kind | the SQL is | validated as |
|---|---|---|
| join | a JOIN clause | `FROM <applies to> <sql>` |
| filter | a WHERE condition | `FROM <applies to> WHERE <sql>` |
| measure | an aggregate expression | `SELECT <sql> FROM <applies to>` |
| dimension | an expression to group by | `SELECT <sql> ... GROUP BY 1` |

Strict, as the golden pairs' parser is: a snippet missing a field does not
match at all, and the count of `## Snn -` headings against the number parsed
turns that into a loud failure rather than a quietly short load.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import sql
from nl2sql_common.roles import SNIPPETS_READER, limit_role
from nl2sql_common.vectors import vector_literal

TABLE = "sql_snippets"
VECTOR_TABLE = "sql_snippet_vectors"
SOURCE_TABLE = "sql_snippet_source"
MATCH_FUNCTION = "sql_snippets_keyword_match"

#: Indexing and querying must use the same text search configuration or
#: nothing matches; the golden pairs' BM25 uses the same one.
TS_CONFIG = "english"

#: How much a snippet's other matching phrases add to its best one. Low: a
#: snippet is relevant because of the most specific thing the question said
#: about it, and five vague matches are not one precise one.
EXTRA_PHRASE_WEIGHT = 0.25

#: A snippet's id: `S` and at least two digits, the golden pairs' rule.
SNIPPET_ID = r"S\d{2,}"

#: What a snippet can be, in the order the curation interface lists them.
KINDS = ("join", "filter", "measure", "dimension")

#: The meta keys every snippet carries, in the order they are written.
META_KEYS = ("chunk_id", "kind", "tables", "keywords")

HEADING_RE = re.compile(rf"^## {SNIPPET_ID} - ", re.M)
ENTRY_RE = re.compile(
    rf"^## (?P<snippet_id>{SNIPPET_ID}) - (?P<name>.+?)\n"
    r"\n```meta\n(?P<meta>.*?)\n```\n"
    r"\n\*\*Means:\*\* (?P<means>.*?)\n"
    r"\n\*\*Applies to:\*\*\n"
    r"\n```sql\n(?P<applies_to>.*?)\n```\n"
    r"\n\*\*SQL:\*\*\n"
    r"\n```sql\n(?P<sql>.*?)\n```\n"
    r"\n\*\*Note:\*\* (?P<note>.*?)\n",
    re.S | re.M,
)
META_KV_RE = re.compile(r"^(\w[\w.-]*):\s*(.*)$")


@dataclass
class Snippet:
    snippet_id: str  # S01, S02 .. S99, S100 ..
    chunk_id: str  # snippet:s01 .., from the meta block
    name: str
    kind: str
    tables: str
    keywords: str
    means: str
    applies_to: str
    sql: str
    note: str
    ordinal: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def table_list(self) -> list[str]:
        return [t.strip() for t in self.tables.split(",") if t.strip()]

    @property
    def keyword_list(self) -> list[str]:
        return [k.strip() for k in self.keywords.split(",") if k.strip()]

    @property
    def search_text(self) -> str:
        """What is embedded: the name, what it means, and how it is asked for.

        The meaning is the part a question is compared with. The keywords go
        in as well because they are the phrasings a curator has seen people
        use, and a question that says "store brand" should land near a
        snippet whose meaning says "private label".
        """
        return f"{self.name}. {self.means} Asked as: {', '.join(self.keyword_list)}."

    @property
    def content_hash(self) -> str:
        """Hash of every field that is loaded, so an edit anywhere is detected."""
        payload = "\x1f".join(
            [
                self.chunk_id,
                self.kind,
                self.name,
                self.tables,
                self.keywords,
                self.means,
                self.applies_to,
                self.sql,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chunk_id_for(snippet_id: str) -> str:
    """`S07` -> `snippet:s07`, the convention every snippet follows."""
    return f"snippet:{snippet_id.lower()}"


def document_hash(data: bytes | str) -> str:
    """The document's own hash: what a load records, and launch.sh compares.

    Of the file's bytes, as launch.sh's sha256sum takes it -- not of the text
    Python reads, which has a CRLF checkout's line endings translated away and
    would never match, reloading the store on every start.
    """
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def parse_meta(block: str) -> dict:
    meta = {}
    for line in block.splitlines():
        match = META_KV_RE.match(line.strip())
        if match:
            meta[match.group(1)] = match.group(2).strip()
    return meta


def parse_text(text: str, name: str = "the snippet document") -> list[Snippet]:
    """Every snippet in the markdown, in document order.

    Refuses rather than skips: a snippet with a missing meta key, a kind
    nobody validates, or an id used twice is an error naming it, and a
    heading the entry pattern could not read is caught by the count.
    """
    snippets: list[Snippet] = []
    for ordinal, match in enumerate(ENTRY_RE.finditer(text), start=1):
        snippet_id = match.group("snippet_id")
        meta = parse_meta(match.group("meta"))
        missing = [k for k in META_KEYS if not meta.get(k)]
        if missing:
            raise ValueError(f"{snippet_id} has a meta block missing {', '.join(missing)}")
        kind = meta["kind"].lower()
        if kind not in KINDS:
            raise ValueError(f"{snippet_id} is a {kind!r}; a snippet is one of {', '.join(KINDS)}")
        snippets.append(
            Snippet(
                snippet_id=snippet_id,
                chunk_id=meta["chunk_id"],
                name=match.group("name").strip(),
                kind=kind,
                tables=meta["tables"],
                keywords=meta["keywords"],
                means=match.group("means").strip(),
                applies_to=match.group("applies_to").strip(),
                sql=match.group("sql").strip(),
                note=match.group("note").strip(),
                ordinal=ordinal,
                meta=meta,
            )
        )

    headings = len(HEADING_RE.findall(text))
    if headings != len(snippets):
        raise ValueError(
            f"{name} has {headings} snippet headings but only {len(snippets)} parsed "
            "cleanly -- a snippet is missing one of its labelled fields"
        )
    seen: dict[str, str] = {}
    for snippet in snippets:
        for key in (snippet.snippet_id, snippet.chunk_id):
            if key in seen:
                raise ValueError(f"{key} is used by more than one snippet")
            seen[key] = snippet.snippet_id
    return snippets


def parse_document(path: Path) -> list[Snippet]:
    return parse_text(path.read_text(), path.name)


# --- storage ---------------------------------------------------------------
#
# One Postgres with pgvector holds all of it: the rows, the lexical index
# over them, their vectors, and a record of which document they came from.
# The agent reads it as a role that can SELECT and nothing else, created here
# because this is the one process that ever writes to the store.


def connect(url: str, *, connect_timeout: int | None = None) -> psycopg.Connection:
    options = {} if connect_timeout is None else {"connect_timeout": connect_timeout}
    return psycopg.connect(url, **options)


def ensure_tables(conn: psycopg.Connection) -> None:
    """The rows, the load record, and the keyword matcher over them.

    The vector table is created by `ensure_vector_table` on the first embed,
    because its width is the embedding model's dimension.
    """
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                chunk_id     TEXT PRIMARY KEY,
                snippet_id   TEXT NOT NULL UNIQUE,
                source_doc   TEXT NOT NULL,
                ordinal      INT  NOT NULL,
                kind         TEXT NOT NULL,
                name         TEXT NOT NULL,
                tables       TEXT NOT NULL,
                keywords     TEXT NOT NULL,
                means        TEXT NOT NULL,
                applies_to   TEXT NOT NULL,
                sql          TEXT NOT NULL,
                note         TEXT NOT NULL DEFAULT '',
                chunk_meta   JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                content_hash TEXT NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(TABLE))
    )
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                only_row        BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (only_row),
                source_doc      TEXT NOT NULL,
                document_hash   TEXT NOT NULL,
                snippets        INT  NOT NULL,
                embedded        INT  NOT NULL,
                embedding_model TEXT NOT NULL,
                loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(SOURCE_TABLE))
    )
    ensure_match_function(conn)
    conn.commit()


def ensure_match_function(conn: psycopg.Connection) -> None:
    """Keyword matching by phrase, weighted by how rare its words are.

    Each comma-separated keyword is a phrase, and so is the whole name; a
    phrase matches a question that contains every one of its words, in any
    order and any inflection ("weekend sales" matches "sales on weekends"),
    once the text search configuration has dropped its stopwords -- so "on
    promotion" is the one word "promotion". A snippet scores
    its best phrase -- the sum of its words' inverse document frequencies, so
    "private label" outweighs "sales" -- plus a quarter of the others.

    Phrases rather than words, because a word on its own is how the wrong
    snippet arrives: "stores" is in "How many stores are there?" and "store"
    is in "store brand", and a bag-of-words ranker counts that as a match.
    The statistics are computed per query from the rows, so they cannot
    disagree with them; in the database so the ranking can be read and
    tested with psql alone.
    """
    conn.execute(
        sql.SQL(
            """
            CREATE OR REPLACE FUNCTION {fn}(query_text TEXT)
            RETURNS TABLE (chunk_id TEXT, score DOUBLE PRECISION, matched TEXT)
            LANGUAGE sql STABLE AS $fn$
                WITH question AS (
                    SELECT to_tsvector({cfg}, query_text) AS tsv
                ),
                phrases AS (
                    SELECT s.chunk_id, btrim(p.phrase) AS phrase,
                           plainto_tsquery({cfg}, p.phrase) AS query,
                           tsvector_to_array(to_tsvector({cfg}, p.phrase)) AS lexemes
                    FROM {tbl} s,
                         unnest(ARRAY[s.name] || string_to_array(s.keywords, ',')) AS p(phrase)
                ),
                df AS (
                    SELECT l.lexeme, count(DISTINCT p.chunk_id)::double precision AS df
                    FROM phrases p, unnest(p.lexemes) AS l(lexeme)
                    GROUP BY l.lexeme
                ),
                n AS (
                    SELECT count(*)::double precision AS docs FROM {tbl}
                ),
                hits AS (
                    SELECT p.chunk_id, p.phrase,
                           (SELECT sum(ln(1.0 + (n.docs - df.df + 0.5) / (df.df + 0.5)))
                            FROM unnest(p.lexemes) AS l(lexeme)
                            JOIN df ON df.lexeme = l.lexeme) AS weight
                    FROM phrases p, question, n
                    WHERE cardinality(p.lexemes) > 0 AND question.tsv @@ p.query
                )
                SELECT chunk_id,
                       (max(weight) + {extra} * (sum(weight) - max(weight)))::double precision,
                       string_agg(phrase, ', ' ORDER BY weight DESC, phrase)
                FROM hits
                GROUP BY chunk_id
            $fn$
            """
        ).format(
            fn=sql.Identifier(MATCH_FUNCTION),
            tbl=sql.Identifier(TABLE),
            cfg=sql.Literal(TS_CONFIG),
            extra=sql.Literal(EXTRA_PHRASE_WEIGHT),
        )
    )


def ensure_vector_table(conn: psycopg.Connection, dimension: int) -> None:
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                chunk_id        TEXT PRIMARY KEY REFERENCES {} (chunk_id) ON DELETE CASCADE,
                snippet_id      TEXT NOT NULL,
                content         TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding       vector({}) NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(sql.Identifier(VECTOR_TABLE), sql.Identifier(TABLE), sql.Literal(dimension))
    )
    conn.execute(
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS {} ON {} USING hnsw (embedding vector_cosine_ops)"
        ).format(sql.Identifier(f"idx_{VECTOR_TABLE}_hnsw"), sql.Identifier(VECTOR_TABLE))
    )
    conn.commit()


def ensure_reader(conn: psycopg.Connection, role: str, password: str) -> None:
    """The agent's role: it can read every table here and write none.

    Recreated on every load, the way `nl2sql_reader` is on every start, so
    its grants are whatever this says rather than whatever a past version
    said. The default privilege covers the vector table, which is created
    after this on a first load.
    """
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    conn.execute(
        sql.SQL(f"{verb} ROLE {{}} WITH LOGIN PASSWORD {{}}").format(
            sql.Identifier(role), sql.Literal(password)
        )
    )
    # What it may cost the store (V6-39): the agent's retriever, one pool.
    limit_role(conn, role, SNIPPETS_READER)
    database = conn.execute("SELECT current_database()").fetchone()[0]
    conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), sql.Identifier(role)
        )
    )
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role)))
    conn.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(
            sql.Identifier(role)
        )
    )
    conn.commit()


def upsert_snippets(conn: psycopg.Connection, snippets: list[Snippet], source_doc: str) -> int:
    for snippet in snippets:
        conn.execute(
            sql.SQL(
                """
                INSERT INTO {} (chunk_id, snippet_id, source_doc, ordinal, kind, name, tables,
                                keywords, means, applies_to, sql, note, chunk_meta, content_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    snippet_id   = EXCLUDED.snippet_id,
                    source_doc   = EXCLUDED.source_doc,
                    ordinal      = EXCLUDED.ordinal,
                    kind         = EXCLUDED.kind,
                    name         = EXCLUDED.name,
                    tables       = EXCLUDED.tables,
                    keywords     = EXCLUDED.keywords,
                    means        = EXCLUDED.means,
                    applies_to   = EXCLUDED.applies_to,
                    sql          = EXCLUDED.sql,
                    note         = EXCLUDED.note,
                    chunk_meta   = EXCLUDED.chunk_meta,
                    content_hash = EXCLUDED.content_hash,
                    updated_at   = now()
                """
            ).format(sql.Identifier(TABLE)),
            (
                snippet.chunk_id,
                snippet.snippet_id,
                source_doc,
                snippet.ordinal,
                snippet.kind,
                snippet.name,
                snippet.tables,
                snippet.keywords,
                snippet.means,
                snippet.applies_to,
                snippet.sql,
                snippet.note,
                json.dumps(snippet.meta),
                snippet.content_hash,
            ),
        )
    conn.commit()
    return len(snippets)


def delete_missing(conn: psycopg.Connection, keep_ids: list[str]) -> int:
    """Rows whose snippet the document no longer holds; their vectors cascade."""
    result = conn.execute(
        sql.SQL("DELETE FROM {} WHERE NOT (chunk_id = ANY(%s))").format(sql.Identifier(TABLE)),
        (keep_ids,),
    )
    conn.commit()
    return result.rowcount or 0


def vector_state(conn: psycopg.Connection) -> dict[str, tuple[str, str]]:
    """chunk_id -> (content_hash, embedding_model) for what is already embedded."""
    present = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (VECTOR_TABLE,)).fetchone()[0]
    if not present:
        return {}
    rows = conn.execute(
        sql.SQL("SELECT chunk_id, content_hash, embedding_model FROM {}").format(
            sql.Identifier(VECTOR_TABLE)
        )
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def upsert_vectors(
    conn: psycopg.Connection, snippets: list[Snippet], vectors: list, model: str
) -> int:
    for snippet, vector in zip(snippets, vectors):
        conn.execute(
            sql.SQL(
                """
                INSERT INTO {} (chunk_id, snippet_id, content, content_hash,
                                embedding_model, embedding)
                VALUES (%s,%s,%s,%s,%s,%s::vector)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    snippet_id      = EXCLUDED.snippet_id,
                    content         = EXCLUDED.content,
                    content_hash    = EXCLUDED.content_hash,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding       = EXCLUDED.embedding,
                    created_at      = now()
                """
            ).format(sql.Identifier(VECTOR_TABLE)),
            (
                snippet.chunk_id,
                snippet.snippet_id,
                snippet.search_text,
                snippet.content_hash,
                model,
                vector_literal(vector),
            ),
        )
    conn.commit()
    return len(snippets)


def record_load(
    conn: psycopg.Connection,
    *,
    source_doc: str,
    digest: str,
    snippets: int,
    embedded: int,
    model: str,
) -> None:
    """Say which document the store now holds -- written only once it all does."""
    conn.execute(
        sql.SQL(
            """
            INSERT INTO {} (only_row, source_doc, document_hash, snippets, embedded, embedding_model)
            VALUES (TRUE, %s, %s, %s, %s, %s)
            ON CONFLICT (only_row) DO UPDATE SET
                source_doc = EXCLUDED.source_doc, document_hash = EXCLUDED.document_hash,
                snippets = EXCLUDED.snippets, embedded = EXCLUDED.embedded,
                embedding_model = EXCLUDED.embedding_model, loaded_at = now()
            """
        ).format(sql.Identifier(SOURCE_TABLE)),
        (source_doc, digest, snippets, embedded, model),
    )
    conn.commit()


def forget_load(conn: psycopg.Connection) -> None:
    """The store no longer holds all of one document: a load half-finished."""
    conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(SOURCE_TABLE)))
    conn.commit()


def search_keywords(conn: psycopg.Connection, text: str, limit: int = 5) -> list[tuple[str, str, float, str]]:
    """(snippet_id, name, score, the phrases that matched), best first."""
    rows = conn.execute(
        sql.SQL(
            "SELECT s.snippet_id, s.name, m.score, m.matched FROM {fn}(%s) m JOIN {tbl} s USING (chunk_id) "
            "ORDER BY m.score DESC, s.ordinal LIMIT %s"
        ).format(fn=sql.Identifier(MATCH_FUNCTION), tbl=sql.Identifier(TABLE)),
        (text, limit),
    ).fetchall()
    return [(r[0], r[1], float(r[2]), r[3]) for r in rows]


def search_vectors(conn: psycopg.Connection, vector, limit: int = 5) -> list[tuple[str, str, float]]:
    """(snippet_id, name, cosine similarity), nearest first."""
    literal = vector_literal(vector)
    rows = conn.execute(
        sql.SQL(
            "SELECT s.snippet_id, s.name, 1 - (v.embedding <=> %s::vector) AS similarity "
            "FROM {vec} v JOIN {tbl} s USING (chunk_id) "
            "ORDER BY v.embedding <=> %s::vector, s.ordinal LIMIT %s"
        ).format(vec=sql.Identifier(VECTOR_TABLE), tbl=sql.Identifier(TABLE)),
        (literal, literal, limit),
    ).fetchall()
    return [(r[0], r[1], float(r[2])) for r in rows]
