"""Retrieval over the SQL snippets: the pieces a query is built from.

The knowledge base says how this database works, and the golden pairs show
whole questions answered. A snippet is smaller than either: one join, one
filter, one measure or one dimension, written as SQL that has been run
against this database, beside what it means in a question's words --
"store brand" is `p.is_private_label`, "transactions" is
`COUNT(DISTINCT f.basket_id)`, and sales reach the fiscal calendar through
`f.sales_date_key`. They are curated in `context_questions/sql_snippets.md`
and loaded into a store of their own (`rag/07_load_snippets.py`).

Two signals decide which snippets a question gets, because each fails where
the other does not:

| Signal | Searches | Finds |
|---|---|---|
| keywords | the phrasings a curator listed, as phrases (in the store) | "private label" when the question says it |
| meaning | an embedding of what each snippet means (pgvector, cosine) | "spend per trip" for average basket value |

A keyword phrase matches only when every one of its words is in the
question, so "stores" alone does not reach the store-brand snippets. Each
signal is put on a fixed 0..1 scale -- keywords by how rare the matched words
are, meaning on a band of cosine similarity -- and the two are averaged. Fixed
rather than relative to this question's best, both of them: a relative
meaning scale would call the nearest of several unrelated snippets a perfect
match, and a relative keyword scale let one long phrase -- "click-through
rate" -- push an exact "weekends" out of the question it was asked in. A snippet qualifies when one of its phrases matched, or
when its meaning alone is close enough; it is kept when the average clears
`min_score`; at most `top_k` are kept.

Questions usually combine several snippets -- a measure, a filter and the
join between them -- so a question is never near any one of them by
meaning. That is why the keyword half carries the weight, and why the
meaning-only route has a high bar.

Every failure degrades: an unreachable store costs the snippets and nothing
else, and an embedding host that is down costs the meaning half while the
keyword half still answers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

TABLE = "sql_snippets"
VECTOR_TABLE = "sql_snippet_vectors"
MATCH_FUNCTION = "sql_snippets_keyword_match"

#: The cosine similarities a meaning score is read between: at or below the
#: floor is unrelated, at or above the ceiling is as close as a question gets
#: to one snippet. Measured with bge-m3 on the starter set: a snippet the
#: question is about sits around 0.50-0.65, an unrelated one at 0.25-0.40.
SIMILARITY_FLOOR = 0.35
SIMILARITY_CEILING = 0.65

#: The keyword weight at which a match counts for 63% of a perfect one: the
#: score saturates as 1 - exp(-weight / scale). A word only one snippet of
#: the starter set's 32 uses weighs about 3, so a one-word phrase like that
#: counts 0.64 and a two-word one 0.87 -- and "promotion", which a third of
#: them use, about 0.4.
KEYWORD_SCALE = 3.0

#: How much meaning counts against keywords in the average.
MEANING_WEIGHT = 0.5

#: Candidates each signal proposes before they are combined.
CANDIDATE_K = 20

DEFAULT_TOP_K = 5
DEFAULT_MIN_SCORE = 0.35
DEFAULT_MIN_SIMILARITY = 0.62


class SnippetsUnavailableError(RuntimeError):
    """The snippet store could not be read."""


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


@dataclass
class Snippet:
    """One retrieved snippet, with how it was found."""

    snippet_id: str
    chunk_id: str
    kind: str
    name: str
    tables: str
    means: str
    applies_to: str
    sql: str
    #: The curator's note: often the mistake the piece prevents, which is
    #: the part a model is most likely to "simplify" away without it.
    note: str = ""
    score: float = 0.0
    #: Cosine similarity of its meaning to the question, when it was measured.
    similarity: float | None = None
    keyword_score: float = 0.0
    #: The keyword phrases that matched, best first; empty when it was found
    #: by meaning alone.
    matched: str = ""

    @property
    def table_list(self) -> list[str]:
        return [t.strip() for t in self.tables.split(",") if t.strip()]

    @property
    def found_by(self) -> str:
        parts = []
        if self.matched:
            parts.append(f"keywords: {self.matched}")
        if self.similarity is not None:
            parts.append(f"meaning {self.similarity:.3f}")
        return "; ".join(parts)


@dataclass
class Found:
    """What one search returned."""

    snippets: list[Snippet] = field(default_factory=list)
    #: Why the meaning half was skipped, when it was; the keyword half answered.
    warning: str | None = None


def render(snippet: Snippet) -> str:
    """One snippet as the generator reads it: what it is, where it goes, and why.

    Written as the clause it is, inside the clause it belongs to, so a join
    reads as a FROM, a filter as a WHERE and a measure as a SELECT -- the
    shape the model is about to write, rather than a description of it. The
    note follows, because the detail it explains is the one a model drops:
    shown `SUM(clicks)::numeric / NULLIF(SUM(impressions), 0)` without "both
    counts are integers", a live run kept the division and lost the cast, and
    every rate came back 0.
    """
    head = f"[{snippet.snippet_id} {snippet.kind}] {snippet.name} -- {snippet.means}"
    base = snippet.applies_to.strip()
    piece = snippet.sql.strip()
    if snippet.kind == "join":
        body = f"FROM {base}\n{piece}"
    elif snippet.kind == "filter":
        body = f"FROM {base}\nWHERE {piece}"
    elif snippet.kind == "measure":
        body = f"SELECT {piece}\nFROM {base}"
    else:
        body = f"SELECT {piece}\nFROM {base}\nGROUP BY {piece}"
    lines = body.splitlines()
    if snippet.note.strip():
        lines.append(f"Note: {snippet.note.strip()}")
    indented = "\n".join(f"  {line}" for line in lines)
    return f"{head}\n{indented}"


def format_snippets(snippets: list[Snippet], max_chars: int = 4000) -> str:
    """The snippets for a prompt, best first, under a budget."""
    blocks: list[str] = []
    used = 0
    for snippet in snippets:
        block = render(snippet)
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


def usable(snippets: list[Snippet], tables: list[str]) -> list[Snippet]:
    """The snippets every one of whose tables is in scope.

    The generator may only use the tables it is shown -- the static validator
    refuses any other -- so a snippet over a table that was not selected
    could only send it into that refusal. The snippets propose no tables of
    their own for the same reason the examples' tables are proposed last: the
    table selection is what decides scope, and a snippet that matched on one
    phrase is weaker evidence than a schema search.
    """
    scope = set(tables)
    return [s for s in snippets if s.table_list and set(s.table_list) <= scope]


class SnippetLibrary:
    """The two searches over the snippet store, combined."""

    def __init__(
        self,
        url: str,
        embedder: Embedder | None,
        *,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        statement_timeout_ms: int = 15000,
    ) -> None:
        self._engine: Engine = create_engine(url, pool_pre_ping=True)
        self._embedder = embedder
        self._top_k = top_k
        self._min_score = min_score
        self._min_similarity = min_similarity
        self._statement_timeout_ms = statement_timeout_ms

    def count(self) -> int:
        """How many snippets are loaded -- a cheap reachability check."""
        try:
            with self._engine.connect() as conn:
                return int(conn.exec_driver_sql(f"SELECT count(*) FROM {TABLE}").scalar())
        except Exception as exc:
            raise SnippetsUnavailableError(f"Could not read {TABLE} from the snippet store: {exc}") from exc

    def search(self, question: str, top_k: int | None = None) -> list[Snippet]:
        return self.find(question, top_k).snippets

    def find(self, question: str, top_k: int | None = None) -> "Found":
        """The snippets for a question, and why the meaning half sat out if it did.

        The warning is returned rather than kept on the library: the REST
        server answers several questions at once through one library.
        """
        k = self._top_k if top_k is None else top_k
        if k <= 0 or not question.strip():
            return Found()
        try:
            with self._engine.connect() as conn:
                conn.exec_driver_sql(f"SET statement_timeout = {int(self._statement_timeout_ms)}")
                keywords = {
                    row[0]: (float(row[1]), row[2] or "")
                    for row in conn.exec_driver_sql(
                        f"SELECT chunk_id, score, matched FROM {MATCH_FUNCTION}(%s) "
                        "ORDER BY score DESC, chunk_id LIMIT %s",
                        (question, CANDIDATE_K),
                    ).fetchall()
                }
                similarities, warning = self._similarities(conn, question, list(keywords))
                chosen = self._choose(keywords, similarities, k)
                return Found(self._hydrate(conn, chosen), warning)
        except Exception as exc:
            raise SnippetsUnavailableError(f"Could not search the snippet store: {exc}") from exc

    def _similarities(
        self, conn, question: str, keyword_ids: list[str]
    ) -> tuple[dict[str, float], str | None]:
        """Meaning similarity for the nearest snippets, and for every keyword hit.

        The keyword hits are measured even when they are not among the nearest,
        so a snippet the question named is scored on both signals rather than
        on one with a zero standing in for the other.
        """
        if self._embedder is None:
            return {}, None
        try:
            vector = self._embedder.embed_query(question)
        except Exception as exc:  # the keyword half still answers
            return {}, f"meaning skipped: could not embed the question: {exc}"
        literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
        present = conn.exec_driver_sql("SELECT to_regclass(%s) IS NOT NULL", (VECTOR_TABLE,)).scalar()
        if not present:
            return {}, "meaning skipped: the snippets have not been embedded"
        rows = conn.exec_driver_sql(
            f"""
            (SELECT chunk_id, 1 - (embedding <=> %s::vector) FROM {VECTOR_TABLE}
             ORDER BY embedding <=> %s::vector LIMIT %s)
            UNION
            (SELECT chunk_id, 1 - (embedding <=> %s::vector) FROM {VECTOR_TABLE}
             WHERE chunk_id = ANY(%s))
            """,
            (literal, literal, CANDIDATE_K, literal, keyword_ids),
        ).fetchall()
        return {row[0]: float(row[1]) for row in rows}, None

    def _choose(
        self, keywords: dict[str, tuple[float, str]], similarities: dict[str, float], k: int
    ) -> list[tuple[str, float, float | None, float, str]]:
        """(chunk_id, score, similarity, keyword score, matched), best first."""
        scored = []
        for chunk_id in set(keywords) | set(similarities):
            similarity = similarities.get(chunk_id)
            keyword_score, matched = keywords.get(chunk_id, (0.0, ""))
            if not matched and (similarity is None or similarity < self._min_similarity):
                continue
            meaning = 0.0
            if similarity is not None:
                meaning = (similarity - SIMILARITY_FLOOR) / (SIMILARITY_CEILING - SIMILARITY_FLOOR)
                meaning = min(1.0, max(0.0, meaning))
            words = 1.0 - math.exp(-keyword_score / KEYWORD_SCALE)
            score = MEANING_WEIGHT * meaning + (1 - MEANING_WEIGHT) * words
            if score >= self._min_score:
                scored.append((chunk_id, score, similarity, keyword_score, matched))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:k]

    def _hydrate(self, conn, chosen) -> list[Snippet]:
        if not chosen:
            return []
        rows = conn.exec_driver_sql(
            f"SELECT chunk_id, snippet_id, kind, name, tables, means, applies_to, sql, note "
            f"FROM {TABLE} WHERE chunk_id = ANY(%s)",
            ([c[0] for c in chosen],),
        ).fetchall()
        found = {row[0]: row for row in rows}
        snippets = []
        for chunk_id, score, similarity, keyword_score, matched in chosen:
            row = found.get(chunk_id)
            if row is None:  # a vector with no row behind it: skip, don't fail
                continue
            snippets.append(
                Snippet(
                    chunk_id=row[0],
                    snippet_id=row[1],
                    kind=row[2],
                    name=row[3],
                    tables=row[4],
                    means=row[5],
                    applies_to=row[6],
                    sql=row[7],
                    note=row[8] or "",
                    score=score,
                    similarity=similarity,
                    keyword_score=keyword_score,
                    matched=matched,
                )
            )
        return snippets
