"""The pgvector half of the golden-pair loader.

Deliberately uses synthetic vectors rather than calling bge-m3: the storage
layer is what is under test here, and made-up vectors make the distances
predictable instead of merely plausible. The real embedding round trip is
covered by tests/agent/test_examples_live.py.

Opt-in (`pytest --run-docker`). Writes into a throwaway schema, so the published
embeddings in `public` are never touched.
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.docker

pytest.importorskip("psycopg", reason="rag/requirements.txt not installed")

from ragproc import golden_vectors as gv  # noqa: E402

DIM = 8
MODEL = "test-embedder"


def unit(*values: float) -> list[float]:
    """A unit-length vector padded to DIM, so cosine distance is predictable."""
    vector = list(values) + [0.0] * (DIM - len(values))
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def record(chunk_id: str, ordinal: int, embedding: list[float], *, content="text", hash_="h1"):
    return {
        "chunk_id": chunk_id,
        "pair_id": chunk_id.upper(),
        "ordinal": ordinal,
        "content": content,
        "content_hash": hash_,
        "embedding": embedding,
    }


def test_both_fields_get_their_own_table(vector_conn):
    """Separate on purpose: the ensemble scores the question and the reasoning
    target independently and weights them differently, which one shared table
    could not express.
    """
    assert set(gv.FIELD_TABLES) == {"question", "reasoning_target"}
    for field in gv.FIELD_TABLES:
        assert gv.ensure_table(vector_conn, field, DIM) == gv.FIELD_TABLES[field]
    tables = {
        r[0]
        for r in vector_conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
    }
    assert tables == set(gv.FIELD_TABLES.values())


def test_neither_table_is_named_like_a_knowledge_collection():
    """The knowledge retriever discovers its collections by globbing for
    `%_embeddings`. A pair table named that way would be searched as if it were
    prose and fail on the columns it does not have.
    """
    assert not any(name.endswith("_embeddings") for name in gv.FIELD_TABLES.values())


def test_creating_a_table_twice_keeps_what_is_in_it(vector_conn):
    gv.ensure_table(vector_conn, "question", DIM)
    gv.upsert(vector_conn, "question", [record("a", 1, unit(1))], MODEL)
    gv.ensure_table(vector_conn, "question", DIM)
    assert len(gv.current_state(vector_conn, "question")) == 1


def test_an_hnsw_cosine_index_is_created(vector_conn):
    """Without the right opclass the index is silently unused by `<=>`, and
    retrieval quietly falls back to a sequential scan.
    """
    gv.ensure_table(vector_conn, "question", DIM)
    definition = vector_conn.execute(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = current_schema() AND tablename = %s AND indexdef LIKE %s",
        (gv.QUESTION_TABLE, "%hnsw%"),
    ).fetchone()
    assert definition is not None
    assert "vector_cosine_ops" in definition[0]


def test_current_state_reports_the_hash_and_model_that_drive_re_embedding(vector_conn):
    gv.ensure_table(vector_conn, "question", DIM)
    gv.upsert(vector_conn, "question", [record("a", 1, unit(1), hash_="abc")], MODEL)
    assert gv.current_state(vector_conn, "question") == {"a": ("abc", MODEL)}


def test_current_state_of_a_table_that_does_not_exist_is_empty_not_an_error(vector_conn):
    """The embedder asks before it creates, on a store that may be brand new."""
    assert gv.current_state(vector_conn, "question") == {}


def test_re_embedding_replaces_the_vector_rather_than_adding_a_second(vector_conn):
    gv.ensure_table(vector_conn, "question", DIM)
    gv.upsert(vector_conn, "question", [record("a", 1, unit(1), hash_="v1")], MODEL)
    gv.upsert(vector_conn, "question", [record("a", 1, unit(0, 1), hash_="v2")], "other-model")
    assert gv.current_state(vector_conn, "question") == {"a": ("v2", "other-model")}


def test_pairs_dropped_from_the_document_are_dropped_from_the_store(vector_conn):
    gv.ensure_table(vector_conn, "question", DIM)
    gv.upsert(
        vector_conn, "question",
        [record("a", 1, unit(1)), record("b", 2, unit(0, 1)), record("c", 3, unit(0, 0, 1))],
        MODEL,
    )
    assert gv.delete_missing(vector_conn, "question", ["a", "c"]) == 1
    assert set(gv.current_state(vector_conn, "question")) == {"a", "c"}


def test_search_orders_by_cosine_distance(vector_conn):
    gv.ensure_table(vector_conn, "question", DIM)
    gv.upsert(
        vector_conn, "question",
        [
            record("same", 1, unit(1, 0)),
            record("close", 2, unit(1, 0.1)),
            record("orthogonal", 3, unit(0, 1)),
        ],
        MODEL,
    )
    hits = gv.search(vector_conn, "question", unit(1, 0), limit=3)
    assert [h["pair_id"] for h in hits] == ["SAME", "CLOSE", "ORTHOGONAL"]
    assert hits[0]["distance"] == pytest.approx(0.0, abs=1e-6)
    assert hits[2]["distance"] == pytest.approx(1.0, abs=1e-6)


def test_search_respects_its_limit(vector_conn):
    gv.ensure_table(vector_conn, "question", DIM)
    gv.upsert(vector_conn, "question", [record(c, i, unit(1, i)) for i, c in enumerate("abcde")], MODEL)
    assert len(gv.search(vector_conn, "question", unit(1), limit=2)) == 2


def test_equal_distances_are_broken_by_document_order(vector_conn):
    """Two pairs can sit the same distance from a query. Without a tiebreak the
    same question returns a different example run to run.
    """
    gv.ensure_table(vector_conn, "question", DIM)
    gv.upsert(
        vector_conn, "question",
        [record("later", 9, unit(0, 1)), record("earlier", 2, unit(0, 1))],
        MODEL,
    )
    assert [h["chunk_id"] for h in gv.search(vector_conn, "question", unit(1, 0), limit=2)] == [
        "earlier",
        "later",
    ]


def test_a_plain_python_list_is_accepted_as_a_query_vector(vector_conn):
    """Sending a list binds as double precision[], which has no `<=>` operator.
    The text-literal form plus a cast is what avoids needing a client-side
    vector type, and this is the test that fails if that is ever undone.
    """
    gv.ensure_table(vector_conn, "question", DIM)
    gv.upsert(vector_conn, "question", [record("a", 1, unit(1))], MODEL)
    assert gv.search(vector_conn, "question", [1.0] + [0.0] * (DIM - 1), limit=1)


def test_the_vector_literal_is_pgvector_text_format():
    assert gv.vector_literal([0.5, -1.25]) == "[0.5,-1.25]"
    assert gv.vector_literal([]) == "[]"


def test_the_stored_content_is_the_text_that_was_embedded(vector_conn):
    """Kept beside the vector so a retrieved hit can be read without going back
    to the context store to find out what it was.
    """
    gv.ensure_table(vector_conn, "reasoning_target", DIM)
    gv.upsert(
        vector_conn, "reasoning_target",
        [record("a", 1, unit(1), content="The five-row fan-out.")], MODEL,
    )
    [hit] = gv.search(vector_conn, "reasoning_target", unit(1), limit=1)
    assert hit["content"] == "The five-row fan-out."
