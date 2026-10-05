"""Steps 5, 6 and 7 as functions: what the loader scripts do, callable (V6-27).

The scripts are a command line over these. The review service calls them in
its own process after a promotion, where it used to run the scripts:
the credentials it holds are passed as arguments, never put on a command line
where `ps` shows them to anyone on the host, and the call is made under the
review service's lock, so two promotions cannot interleave their loads.

Each returns a report and prints nothing. Each raises what its driver
raised -- a database that did not answer, an embedding host that did not --
for the caller to say in its own way: the scripts exit, the review service
reports a step that did not succeed beside a pair that was written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import golden_pairs as gp
from . import golden_vectors as gv
from . import snippets as sn
from .config import document_slug
from .embedder import Embedder

#: The golden pairs' fields that are embedded, each into a table of its own.
PAIR_FIELDS = ("question", "reasoning_target")

#: Seconds to wait for a store to accept a connection: a loader called from a
#: request must not wait out TCP's own retries on a host that has gone.
CONNECT_TIMEOUT = 10


class NothingToEmbed(LookupError):
    """The context store holds no golden pairs: step 5 has not run."""


@dataclass
class PairsLoad:
    """What step 5 did."""

    document: str
    pairs: int
    suites: int
    written: int = 0
    removed: int = 0
    bm25: dict = field(default_factory=dict)
    probe: list[tuple] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.pairs} pairs across {self.suites} suites; {self.written} rows written, "
            f"{self.removed} stale rows removed; BM25 over {self.bm25.get('documents', 0)} documents"
        )


def load_golden_pairs(
    document: str | Path,
    db_url: str,
    *,
    k1: float = gp.DEFAULT_K1,
    b: float = gp.DEFAULT_B,
    probe: str | None = None,
    dry_run: bool = False,
) -> PairsLoad:
    """Parse the question document and bring the context store up to date.

    Rows are upserted on `chunk_id`, pairs gone from the document are
    deleted, and the BM25 statistics are rebuilt from what is left.
    """
    path = Path(document)
    pairs = gp.parse_document(path)
    report = PairsLoad(document=str(path), pairs=len(pairs), suites=len({p.suite for p in pairs}))
    if dry_run:
        return report
    conn = gp.connect(db_url, connect_timeout=CONNECT_TIMEOUT)
    try:
        gp.ensure_tables(conn)
        report.written = gp.upsert_pairs(conn, pairs, source_doc=document_slug(path.name))
        report.removed = gp.delete_missing(conn, [p.chunk_id for p in pairs])
        report.bm25 = gp.rebuild_bm25_index(conn, k1=k1, b=b)
        gp.ensure_bm25_function(conn)
        if probe:
            report.probe = conn.execute(
                f"""
                SELECT b.chunk_id, g.pair_id, round(b.score::numeric, 4), g.title
                FROM {gp.BM25_FUNCTION}(%s) b
                JOIN {gp.TABLE} g USING (chunk_id)
                ORDER BY b.score DESC LIMIT 5
                """,
                (probe,),
            ).fetchall()
    finally:
        conn.close()
    return report


@dataclass
class FieldEmbedded:
    field: str
    table: str
    written: int
    removed: int


@dataclass
class PairsEmbed:
    """What step 6 did."""

    pairs: int
    model: str
    dimension: int
    fields: list[FieldEmbedded] = field(default_factory=list)
    probe: dict[str, list[dict]] = field(default_factory=dict)

    def summary(self) -> str:
        done = "; ".join(
            f"{f.field}: {f.written} embedded, {self.pairs - f.written} already current, {f.removed} removed"
            for f in self.fields
        )
        return f"{self.pairs} golden pairs with {self.model} ({self.dimension} dimensions); {done}"


def embed_golden_pairs(
    chunk_db_url: str,
    vector_db_url: str,
    embedder: Embedder,
    *,
    fields: tuple[str, ...] = PAIR_FIELDS,
    batch_size: int = 32,
    force: bool = False,
    probe: str | None = None,
) -> PairsEmbed:
    """Embed each pair's fields that changed since they were last embedded.

    A pair is re-embedded when its content hash or the model changed, or
    always with `force`; pairs gone from the context store lose their vectors.
    """
    chunk_conn = gp.connect(chunk_db_url, connect_timeout=CONNECT_TIMEOUT)
    try:
        pairs = gp.load_pairs(chunk_conn)
    finally:
        chunk_conn.close()
    if not pairs:
        raise NothingToEmbed(f"no rows in {gp.TABLE} -- run 05_load_golden_pairs.py first")

    check = getattr(embedder, "check", None)
    if check is not None:
        check()
    report = PairsEmbed(pairs=len(pairs), model=embedder.model_name, dimension=embedder.dimension)
    conn = gv.connect(vector_db_url, connect_timeout=CONNECT_TIMEOUT)
    try:
        for name in fields:
            table = gv.ensure_table(conn, name, report.dimension)
            stored = gv.current_state(conn, name)
            pending = [
                pair
                for pair in pairs
                if force or stored.get(pair["chunk_id"]) != (pair["content_hash"], embedder.model_name)
            ]
            records = []
            for start in range(0, len(pending), batch_size):
                batch = pending[start : start + batch_size]
                for pair, vector in zip(batch, embedder.embed([p[name] for p in batch])):
                    records.append(
                        {
                            "chunk_id": pair["chunk_id"],
                            "pair_id": pair["pair_id"],
                            "ordinal": pair["ordinal"],
                            "content": pair[name],
                            "content_hash": pair["content_hash"],
                            "embedding": vector,
                        }
                    )
            written = gv.upsert(conn, name, records, embedder.model_name)
            removed = gv.delete_missing(conn, name, [p["chunk_id"] for p in pairs])
            report.fields.append(FieldEmbedded(field=name, table=table, written=written, removed=removed))
        if probe:
            vector = embedder.embed([probe])[0]
            report.probe = {name: gv.search(conn, name, vector, limit=5) for name in fields}
    finally:
        conn.close()
    return report


@dataclass
class SnippetsLoad:
    """What step 7 did."""

    document: str
    snippets: int
    kinds: dict[str, int]
    written: int = 0
    removed: int = 0
    reader: str = ""
    #: None when the vectors were not attempted (`embed=False`).
    embedded: int | None = None
    #: Why the vectors are not current, when the embedding host failed. The
    #: rows are loaded and searchable by keyword; the next load embeds.
    embed_error: str | None = None
    keyword_probe: list[tuple] = field(default_factory=list)
    meaning_probe: list[tuple] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.embedded is not None and self.embed_error is None

    def summary(self) -> str:
        rows = f"{self.snippets} snippets; {self.written} rows written, {self.removed} stale rows removed"
        if self.embed_error is not None:
            return f"{rows}; vectors FAILED: {self.embed_error}"
        if self.embedded is None:
            return f"{rows}; vectors skipped"
        return f"{rows}; {self.embedded} embedded, {self.snippets - self.embedded} already current"


def load_snippets(
    document: str | Path,
    db_url: str,
    *,
    reader_role: str,
    reader_password: str,
    embedder: Callable[[], Embedder] | None,
    model: str,
    batch_size: int = 32,
    force: bool = False,
    probe: str | None = None,
    dry_run: bool = False,
) -> SnippetsLoad:
    """Load the snippet store from its document, and embed what changed.

    `embedder` is a factory, called only when something needs embedding, so a
    load with nothing new never needs the embedding host; None loads the rows
    and leaves the vectors for the next load. The load record is written only
    when every snippet has a current vector.
    """
    path = Path(document)
    data = path.read_bytes()
    snippets = sn.parse_text(path.read_text(), path.name)
    report = SnippetsLoad(
        document=str(path),
        snippets=len(snippets),
        kinds={kind: sum(1 for s in snippets if s.kind == kind) for kind in sn.KINDS},
    )
    if dry_run:
        return report
    source_doc = document_slug(path.name)
    conn = sn.connect(db_url, connect_timeout=CONNECT_TIMEOUT)
    try:
        sn.ensure_tables(conn)
        report.written = sn.upsert_snippets(conn, snippets, source_doc=source_doc)
        report.removed = sn.delete_missing(conn, [s.chunk_id for s in snippets])
        sn.ensure_reader(conn, reader_role, reader_password)
        report.reader = reader_role
        if embedder is None:
            # The rows are current and the vectors may not be, so the store
            # does not claim to hold this document: the next load embeds.
            sn.forget_load(conn)
            return report

        stored = sn.vector_state(conn)
        pending = [s for s in snippets if force or stored.get(s.chunk_id) != (s.content_hash, model)]
        built = None
        embedded = 0
        try:
            if pending:
                built = embedder()
                built.check()
                sn.ensure_vector_table(conn, built.dimension)
                for start in range(0, len(pending), batch_size):
                    batch = pending[start : start + batch_size]
                    embedded += sn.upsert_vectors(conn, batch, built.embed([s.search_text for s in batch]), model)
        except Exception as exc:  # noqa: BLE001 - the rows are loaded; the report says why the vectors are not
            conn.rollback()
            sn.forget_load(conn)
            report.embedded = embedded
            report.embed_error = str(exc)
            return report
        report.embedded = embedded
        sn.record_load(
            conn,
            source_doc=source_doc,
            digest=sn.document_hash(data),
            snippets=len(snippets),
            embedded=len(snippets),
            model=model,
        )
        if probe:
            report.keyword_probe = sn.search_keywords(conn, probe)
            built = built or embedder()
            report.meaning_probe = sn.search_vectors(conn, built.embed([probe])[0])
    finally:
        conn.close()
    return report
