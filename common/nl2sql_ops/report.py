"""What each database actually holds, said the way the start-up scripts say it.

A healthy container is not a populated one: a volume made before an image
shipped its data comes up healthy and empty, and the only symptom is an
agent quietly answering without retrieval. These checks were SQL that
`launch.sh` and `setup.sh` piped into each container; they are read here,
over the sockets, and printed as lines the scripts relay as they are:

    STEP <heading>     a step, as `==> heading`
    INFO <text>        an indented line
    WARN <text>        a warning
    STATE <key>=<val>  a value a script decides something with

Every check reads; nothing here writes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .connect import connect, listening
from .settings import OpsSettings

#: A golden pair in the question document: `## Q12 - ...`.
PAIR_HEADING = re.compile(r"^## Q\d{2,} - ", re.MULTILINE)

#: Every embedded chunk in the knowledge base: one table per document, each
#: named `<collection>_embeddings`, counted without knowing their names.
CHUNKS_SQL = r"""
SELECT COALESCE(sum(n), 0) FROM (
    SELECT (xpath('/row/c/text()',
            query_to_xml('SELECT count(*) AS c FROM ' || quote_ident(tablename), false, true, '')))[1]::text::bigint AS n
    FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE '%\_embeddings'
) t
"""


@dataclass(frozen=True)
class Line:
    kind: str  # STEP, INFO, WARN, STATE
    text: str

    def __str__(self) -> str:
        return f"{self.kind} {self.text}"


def _count(conn, query: str, params=None) -> int:
    """One number, or 0 when the table it counts is not there."""
    try:
        with conn.transaction():
            row = conn.execute(query, params).fetchone()
        return int(row[0] or 0) if row else 0
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedObject):
        return 0


def document_hash(path: Path) -> str | None:
    """The snippet document's hash as the loader records it: of its bytes."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def snippets_state(settings: OpsSettings) -> str:
    """`behind` when the snippet store was loaded from another version of the
    document (or never), `current` when not, `unknown` when either is not
    there to ask."""
    in_document = document_hash(settings.snippets_document)
    if in_document is None or not listening(settings.sockets, "stores"):
        return "unknown"
    store = settings.store("snippets")
    with connect(settings.sockets, "stores", user=store.role, dbname=store.database) as conn:
        try:
            with conn.transaction():
                row = conn.execute("SELECT document_hash FROM sql_snippet_source").fetchone()
        except psycopg.errors.UndefinedTable:
            row = None
    return "current" if row and row[0] == in_document else "behind"


def report(settings: OpsSettings) -> list[Line]:
    lines = [Line("STEP", "Checking what is actually in each database")]
    say = lambda kind, text: lines.append(Line(kind, text))  # noqa: E731

    with connect(settings.sockets, "retail", user="postgres", dbname=settings.retail_database) as retail:
        rows = _count(retail, "SELECT count(*) FROM fact_pos_retail_sales")
        lines.append(Line("STATE", f"retail_rows={rows}"))
        if rows:
            say("INFO", f"retail dataset: {rows} sales rows")
        else:
            say("WARN", "the retail database is up but has no sales rows in it.")
            say("WARN", "The agent will connect and then answer nothing. Try: ./setup.sh --reset")
        trgm = _count(retail, "SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")
        extra = _count(
            retail,
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE grantee = %s AND privilege_type <> 'SELECT'",
            (settings.reader,),
        )

    if listening(settings.sockets, "vector") and listening(settings.sockets, "context"):
        vector, context = settings.login("vector"), settings.login("context")
        with connect(settings.sockets, "vector", user=vector.role, dbname=vector.database) as conn:
            chunks = _count(conn, CHUNKS_SQL)
            vectors = _count(conn, "SELECT count(*) FROM golden_pair_question_vectors")
            ddl = _count(conn, "SELECT count(*) FROM ddl_index_embeddings")
        with connect(settings.sockets, "context", user=context.role, dbname=context.database) as conn:
            pairs = _count(conn, "SELECT count(*) FROM golden_pairs")
        if chunks:
            say("INFO", f"knowledge base: {chunks} embedded chunks")
        else:
            say("WARN", "the vector store is up but holds no embedded chunks.")
            say("WARN", "Retrieval will be skipped; the agent falls back to schema-only.")
        if pairs and vectors:
            say("INFO", f"worked examples: {pairs} golden pairs, {vectors} embedded questions")
        else:
            say("WARN", f"the context store holds {pairs} golden pairs and the vector store")
            say("WARN", f"{vectors} of their embeddings. Multi-shot needs both; it will be skipped.")
        # The store against the document it was loaded from: the images ship
        # the set as it was when they were published, and only a load catches up.
        try:
            in_document = len(PAIR_HEADING.findall(settings.golden_document.read_text()))
        except OSError:
            in_document = None
        if in_document is not None and pairs and pairs != in_document:
            say("WARN", f"the context store holds {pairs} golden pairs, and the question document {in_document}.")
            say("WARN", "The agent's worked examples are the store's until it is loaded: ./start.sh --load-golden")
        if listening(settings.sockets, "stores"):
            store = settings.store("snippets")
            with connect(settings.sockets, "stores", user=store.role, dbname=store.database) as conn:
                snippets = _count(conn, "SELECT count(*) FROM sql_snippets")
                meanings = _count(conn, "SELECT count(*) FROM sql_snippet_vectors")
            if snippets:
                say("INFO", f"SQL snippets: {snippets} snippets, {meanings} embedded meanings")
            else:
                say("WARN", "the snippet store holds no SQL snippets, so the generator is shown none.")
                say("WARN", "They are loaded from context_questions/sql_snippets.md on start; check the review image can run.")
        # The Schema Retriever selects tables from this one collection instead
        # of asking the model; without it table selection falls back to hints.
        if ddl:
            say("INFO", f"schema index: {ddl} DDL chunks (table selection needs no model call)")
        else:
            say("WARN", "the vector store has no ddl_index_embeddings collection.")
            say("WARN", "Table selection falls back to the knowledge and example hints.")

    # Two things the multi-agent pipeline needs that the stores above do not
    # cover. Neither is fatal: the matcher falls back to difflib.
    say("STEP", "Checking the multi-agent pipeline")
    if trgm:
        say("INFO", "literal matching: pg_trgm installed (trigram search)")
    else:
        say("WARN", "pg_trgm is not installed; literal matching falls back to difflib.")
    if extra == 0:
        say("INFO", "least privilege: the agent's role holds SELECT and nothing else")
    else:
        say("WARN", f"the agent's role holds {extra} non-SELECT grants; it should hold none.")
    return lines
