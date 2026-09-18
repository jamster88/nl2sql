"""The knowledge-document halves of the pipeline: chunk_store and vector_store.

These built the v1 and v2 stores and were restored onto this branch unchanged,
so nothing here is new behaviour -- but nothing here was covered either, and
they are the modules the golden-pair equivalents were modelled on. The
properties that matter are the same ones: a re-run must be incremental rather
than destructive, and a chunk removed from a document must not linger as a
vector that still retrieves.

Opt-in (`pytest --run-docker`). Each test gets a throwaway database, so the
published chunks and embeddings are never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

pytest.importorskip("psycopg", reason="rag/requirements.txt not installed")

from ragproc import chunk_store, vector_store  # noqa: E402
from ragproc.chunker import Chunk  # noqa: E402

SLUG = "business_index"
DIM = 8


def chunk(ordinal: int, content: str, *, heading="Doc > Topic", meta=None) -> Chunk:
    return Chunk(ordinal=ordinal, heading_path=heading, content=content, meta=meta or {})


def source(tmp_path: Path, text: str = "# Doc\n\nbody") -> Path:
    path = tmp_path / "business_index.md"
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------
# chunk_store
# ---------------------------------------------------------------------------


def test_a_chunk_id_is_namespaced_by_document_and_keyed_by_content():
    """Two documents can hold identical text; the slug is what keeps their
    chunks from colliding in a shared store.
    """
    same = chunk(0, "identical text")
    assert chunk_store.chunk_id_for("a", same) != chunk_store.chunk_id_for("b", same)
    assert chunk_store.chunk_id_for("a", same).startswith("a:")


def test_the_file_hash_follows_the_file(tmp_path):
    path = source(tmp_path, "one")
    first = chunk_store.file_hash(path)
    path.write_text("two")
    assert chunk_store.file_hash(path) != first


def test_storing_a_document_creates_its_table_and_registers_it(chunk_conn, tmp_path):
    stats = chunk_store.store_chunks(
        chunk_conn, SLUG, source(tmp_path), [chunk(0, "alpha"), chunk(1, "beta")]
    )
    assert stats.inserted == 2
    assert stats.changed

    stored = chunk_store.fetch_chunks(chunk_conn, SLUG)
    assert [c["content"] for c in stored] == ["alpha", "beta"]
    assert all(c["source_doc"] == SLUG for c in stored)

    [document] = chunk_store.known_documents(chunk_conn)
    assert document["slug"] == SLUG
    assert document["chunk_count"] == 2


def test_re_running_with_no_edits_inserts_nothing(chunk_conn, tmp_path):
    """The whole point of hashing content: an unchanged document costs one
    comparison, not a re-embed of everything.
    """
    chunks = [chunk(0, "alpha"), chunk(1, "beta")]
    path = source(tmp_path)
    chunk_store.store_chunks(chunk_conn, SLUG, path, chunks)

    again = chunk_store.store_chunks(chunk_conn, SLUG, path, chunks)
    assert again.inserted == 0
    assert again.deleted == 0
    assert again.unchanged == 2
    assert not again.changed


def test_editing_one_section_touches_only_that_chunk(chunk_conn, tmp_path):
    path = source(tmp_path)
    chunk_store.store_chunks(chunk_conn, SLUG, path, [chunk(0, "alpha"), chunk(1, "beta")])

    stats = chunk_store.store_chunks(chunk_conn, SLUG, path, [chunk(0, "alpha"), chunk(1, "edited")])
    assert (stats.inserted, stats.deleted, stats.unchanged) == (1, 1, 1)
    assert {c["content"] for c in chunk_store.fetch_chunks(chunk_conn, SLUG)} == {"alpha", "edited"}


def test_reordering_without_editing_updates_position_and_inserts_nothing(chunk_conn, tmp_path):
    """Ids are content hashes, not positions, so moving a section is a cheap
    metadata update rather than a full reload.
    """
    path = source(tmp_path)
    chunk_store.store_chunks(chunk_conn, SLUG, path, [chunk(0, "alpha"), chunk(1, "beta")])

    stats = chunk_store.store_chunks(chunk_conn, SLUG, path, [chunk(0, "beta"), chunk(1, "alpha")])
    assert stats.inserted == 0
    assert stats.refreshed == 2
    assert [c["content"] for c in chunk_store.fetch_chunks(chunk_conn, SLUG)] == ["beta", "alpha"]


def test_a_chunk_deleted_from_the_document_is_deleted_from_the_store(chunk_conn, tmp_path):
    path = source(tmp_path)
    chunk_store.store_chunks(chunk_conn, SLUG, path, [chunk(0, "alpha"), chunk(1, "beta")])

    stats = chunk_store.store_chunks(chunk_conn, SLUG, path, [chunk(0, "alpha")])
    assert stats.deleted == 1
    assert len(chunk_store.fetch_chunks(chunk_conn, SLUG)) == 1


def test_chunk_metadata_round_trips_as_json(chunk_conn, tmp_path):
    meta = {"chunk_id": "biz:grain", "tables": "dim_date", "keywords": "grain, join"}
    chunk_store.store_chunks(chunk_conn, SLUG, source(tmp_path), [chunk(0, "body", meta=meta)])
    assert chunk_store.fetch_chunks(chunk_conn, SLUG)[0]["chunk_meta"] == meta


def test_the_registry_records_the_settings_a_document_was_chunked_with(chunk_conn, tmp_path):
    """Chunk boundaries depend on them, so a store built with different settings
    is a different store even when the document is identical.
    """
    chunk_store.store_chunks(
        chunk_conn, SLUG, source(tmp_path), [chunk(0, "alpha")],
        settings={"max_chunk_tokens": 350},
    )
    row = chunk_conn.execute(
        f"SELECT settings FROM {chunk_store.REGISTRY_TABLE} WHERE slug = %s", (SLUG,)
    ).fetchone()
    assert row[0] == {"max_chunk_tokens": 350}


def test_documents_are_tracked_independently(chunk_conn, tmp_path):
    chunk_store.store_chunks(chunk_conn, "one", source(tmp_path), [chunk(0, "a")])
    chunk_store.store_chunks(chunk_conn, "two", source(tmp_path), [chunk(0, "a"), chunk(1, "b")])
    assert {d["slug"]: d["chunk_count"] for d in chunk_store.known_documents(chunk_conn)} == {
        "one": 1, "two": 2
    }


def test_an_empty_registry_lists_nothing_rather_than_failing(chunk_conn):
    assert chunk_store.known_documents(chunk_conn) == []


def test_the_load_stats_read_as_a_sentence():
    stats = chunk_store.LoadStats(inserted=1, unchanged=2, deleted=1, refreshed=2)
    assert str(stats) == "1 new, 2 unchanged, 1 removed, 2 re-ordered"


# ---------------------------------------------------------------------------
# vector_store
# ---------------------------------------------------------------------------


def unit(*values: float) -> list[float]:
    vector = list(values) + [0.0] * (DIM - len(values))
    total = sum(v * v for v in vector) ** 0.5 or 1.0
    return [v / total for v in vector]


def embedding(chunk_id: str, ordinal: int, vector, *, content="text", hash_="h1") -> dict:
    return {
        "chunk_id": chunk_id,
        "source_doc": SLUG,
        "ordinal": ordinal,
        "heading_path": "Doc > Topic",
        "chunk_meta": {"table": "dim_store"},
        "content": content,
        "content_hash": hash_,
        "embedding": vector,
    }


def test_the_embedding_table_is_named_for_its_document(vector_conn):
    """The agent's knowledge retriever discovers collections by this suffix, so
    the name is an interface rather than an implementation detail.
    """
    assert vector_store.ensure_embedding_table(vector_conn, SLUG, DIM) == f"{SLUG}_embeddings"


def test_the_vector_column_is_sized_from_the_model(vector_conn):
    """Switching embedding models means a different dimension, and pgvector
    refuses the mismatch rather than storing something meaningless.
    """
    vector_store.ensure_embedding_table(vector_conn, SLUG, DIM)
    with pytest.raises(Exception):
        vector_store.upsert_embeddings(
            vector_conn, SLUG, [embedding("a", 0, [0.0] * (DIM + 1))], "m"
        )
    vector_conn.rollback()


def test_an_hnsw_cosine_index_is_created(vector_conn):
    vector_store.ensure_embedding_table(vector_conn, SLUG, DIM)
    definition = vector_conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
        "AND tablename = %s AND indexdef LIKE %s",
        (f"{SLUG}_embeddings", "%hnsw%"),
    ).fetchone()
    assert definition and "vector_cosine_ops" in definition[0]


def test_current_state_reports_which_model_each_vector_came_from(vector_conn):
    """Vectors from two models cannot be compared, so the model is stored per
    row rather than assumed for the table.
    """
    vector_store.ensure_embedding_table(vector_conn, SLUG, DIM)
    vector_store.upsert_embeddings(vector_conn, SLUG, [embedding("a", 0, unit(1))], "bge-m3")
    assert vector_store.current_state(vector_conn, SLUG) == {"a": "bge-m3"}


def test_re_embedding_replaces_rather_than_duplicating(vector_conn):
    vector_store.ensure_embedding_table(vector_conn, SLUG, DIM)
    vector_store.upsert_embeddings(vector_conn, SLUG, [embedding("a", 0, unit(1))], "bge-m3")
    vector_store.upsert_embeddings(
        vector_conn, SLUG, [embedding("a", 0, unit(0, 1), content="new")], "nomic"
    )
    assert vector_store.current_state(vector_conn, SLUG) == {"a": "nomic"}
    assert vector_store.search(vector_conn, SLUG, unit(0, 1), limit=1)[0]["content"] == "new"


def test_a_vector_whose_chunk_is_gone_is_deleted(vector_conn):
    """Otherwise a section removed from a document keeps being retrieved, which
    reads as the knowledge base confidently citing text that no longer exists.
    """
    vector_store.ensure_embedding_table(vector_conn, SLUG, DIM)
    vector_store.upsert_embeddings(
        vector_conn, SLUG,
        [embedding("a", 0, unit(1)), embedding("b", 1, unit(0, 1))], "bge-m3",
    )
    assert vector_store.delete_missing(vector_conn, SLUG, ["a"]) == 1
    assert list(vector_store.current_state(vector_conn, SLUG)) == ["a"]


def test_search_returns_the_nearest_first(vector_conn):
    vector_store.ensure_embedding_table(vector_conn, SLUG, DIM)
    vector_store.upsert_embeddings(
        vector_conn, SLUG,
        [
            embedding("far", 0, unit(0, 1), content="far"),
            embedding("near", 1, unit(1, 0.05), content="near"),
        ],
        "bge-m3",
    )
    hits = vector_store.search(vector_conn, SLUG, unit(1, 0), limit=2)
    assert [h["content"] for h in hits] == ["near", "far"]
    assert hits[0]["distance"] < hits[1]["distance"]
    assert hits[0]["heading_path"] == "Doc > Topic"


def test_the_embed_stats_read_as_a_sentence():
    stats = vector_store.EmbedStats(embedded=1, skipped=2, deleted=3)
    assert str(stats) == "1 embedded, 2 already current, 3 removed"


def test_a_plain_python_list_is_accepted_as_a_query_vector(vector_conn):
    """Regression: `search()` bound the list directly, which Postgres reads as
    `double precision[]`. There is no `<=>` operator for that, so the function
    raised for every caller -- it was only ever reached from a README example.
    """
    vector_store.ensure_embedding_table(vector_conn, SLUG, DIM)
    vector_store.upsert_embeddings(vector_conn, SLUG, [embedding("a", 0, unit(1))], "bge-m3")
    assert vector_store.search(vector_conn, SLUG, [1.0] + [0.0] * (DIM - 1), limit=1)


def test_the_vector_literal_is_pgvector_text_format():
    assert vector_store.vector_literal([0.5, -1.25]) == "[0.5,-1.25]"


def test_equal_distances_are_broken_by_document_order(vector_conn):
    vector_store.ensure_embedding_table(vector_conn, SLUG, DIM)
    vector_store.upsert_embeddings(
        vector_conn, SLUG,
        [embedding("later", 9, unit(0, 1)), embedding("earlier", 2, unit(0, 1))], "bge-m3",
    )
    assert [h["chunk_id"] for h in vector_store.search(vector_conn, SLUG, unit(1, 0), limit=2)] == [
        "earlier", "later",
    ]


# ---------------------------------------------------------------------------
# Opening a connection
# ---------------------------------------------------------------------------


def test_the_chunk_store_opens_a_plain_connection(chunk_conn):
    """No extension, no adapters -- it holds text, and keeping it plain is why
    the chunk store does not need pgvector installed at all.
    """
    conn = chunk_store.connect(chunk_conn.scratch_url)
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_the_vector_store_installs_pgvector_on_a_fresh_database(vector_conn):
    """A brand-new database has no `vector` type, and `ensure_embedding_table`
    would fail on its first CREATE. connect() is what makes a fresh store work.
    """
    conn = vector_store.connect(vector_conn.scratch_url)
    try:
        assert conn.execute(
            "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()[0] == 1
        vector_store.ensure_embedding_table(conn, "fresh", DIM)
    finally:
        conn.close()
