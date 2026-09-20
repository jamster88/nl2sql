"""KnowledgeBase and the helpers that turn retrieved chunks into prompt
context. No vector store or embedding model is required here -- the live
behavior is covered in test_retrieval_live.py.
"""

from __future__ import annotations

import pytest
from nl2sql_agent.retrieval import (
    KnowledgeBase,
    KnowledgeUnavailableError,
    _vector_literal,
    format_chunks,
    tables_mentioned,
)

from .conftest import make_chunk


# ---------------------------------------------------------------------------
# RetrievedChunk
# ---------------------------------------------------------------------------


def test_chunk_table_reads_the_meta_key():
    chunk = make_chunk(meta={"table": "dim_store"})
    assert chunk.table == "dim_store"


@pytest.mark.parametrize("meta", [{}, {"table": ""}, {"table": None}, {"domain": "sales"}])
def test_chunk_table_is_none_without_a_usable_meta_table(meta):
    assert make_chunk(meta=meta).table is None


# ---------------------------------------------------------------------------
# tables_mentioned
# ---------------------------------------------------------------------------


def test_tables_mentioned_preserves_best_match_order_and_dedupes():
    chunks = [
        make_chunk(meta={"table": "fact_market_share_weekly"}, distance=0.1),
        make_chunk(meta={}, distance=0.2),
        make_chunk(meta={"table": "dim_competitor"}, distance=0.3),
        make_chunk(meta={"table": "fact_market_share_weekly"}, distance=0.4),
    ]
    assert tables_mentioned(chunks) == ["fact_market_share_weekly", "dim_competitor"]


def test_tables_mentioned_empty_for_chunks_without_table_metadata():
    assert tables_mentioned([make_chunk(meta={})]) == []


# ---------------------------------------------------------------------------
# format_chunks
# ---------------------------------------------------------------------------


def test_format_chunks_includes_provenance_and_distance():
    text = format_chunks([make_chunk(distance=0.25)])
    assert "business_index" in text
    assert "Market share fan-out" in text
    assert "0.250" in text


def test_format_chunks_empty_input():
    assert format_chunks([]) == ""


def test_format_chunks_respects_the_character_budget():
    chunks = [make_chunk(content="x" * 500, chunk_id=f"c{i}") for i in range(10)]
    text = format_chunks(chunks, max_chars=1200)
    assert len(text) <= 1400  # budget plus the final block's header allowance
    assert text.count("--- business_index") < 10


def test_format_chunks_keeps_the_best_match_when_truncating():
    chunks = [
        make_chunk(heading_path="closest", content="a" * 400, distance=0.1),
        make_chunk(heading_path="furthest", content="b" * 400, distance=0.9),
    ]
    text = format_chunks(chunks, max_chars=450)
    assert "closest" in text
    assert "furthest" not in text


# ---------------------------------------------------------------------------
# Vector literal
# ---------------------------------------------------------------------------


def test_vector_literal_is_pgvector_text_format():
    assert _vector_literal([1.0, -0.5, 0.25]) == "[1.0,-0.5,0.25]"


def test_vector_literal_handles_ints():
    assert _vector_literal([1, 2]) == "[1.0,2.0]"


# ---------------------------------------------------------------------------
# KnowledgeBase behavior that needs no server
# ---------------------------------------------------------------------------


class _BoomEmbedder:
    def embed_query(self, text: str):
        raise RuntimeError("connection refused")


class _StaticEmbedder:
    def __init__(self, vector=None):
        self.vector = vector or [0.1, 0.2, 0.3]
        self.calls: list[str] = []

    def embed_query(self, text: str):
        self.calls.append(text)
        return self.vector


def _kb(embedder, **kwargs) -> KnowledgeBase:
    # A URL that parses but points nowhere; construction must not connect.
    return KnowledgeBase("postgresql+psycopg://u:p@127.0.0.1:1/db", embedder, **kwargs)


def test_construction_does_not_connect():
    kb = _kb(_StaticEmbedder())
    assert isinstance(kb, KnowledgeBase)


def test_search_wraps_embedding_failures_with_actionable_text():
    kb = _kb(_BoomEmbedder())
    with pytest.raises(KnowledgeUnavailableError, match="Could not embed the question"):
        kb.search("anything")


def test_search_wraps_database_failures():
    kb = _kb(_StaticEmbedder(), collections=["business_index_embeddings"])
    with pytest.raises(KnowledgeUnavailableError, match="Could not search the knowledge base"):
        kb.search("anything")


def test_search_with_zero_top_k_does_no_work():
    embedder = _StaticEmbedder()
    kb = _kb(embedder)
    assert kb.search("anything", top_k=0) == []
    assert embedder.calls == []  # not even embedded


def test_configured_collections_filter_out_unsafe_identifiers():
    """Collection names get interpolated into SQL, so anything that isn't a
    plain identifier must be dropped rather than passed through.
    """
    kb = _kb(
        _StaticEmbedder(),
        collections=["good_embeddings", 'bad"; DROP TABLE x; --', "UPPER_CASE", "also-bad"],
    )
    assert kb.collections() == ["good_embeddings"]


# ---------------------------------------------------------------------------
# search() / collections() against a stubbed engine
#
# The live suite proves retrieval works against a real pgvector; these cover
# the query construction and row mapping without needing a server, including
# the failure branches a live store will not produce on demand.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def exec_driver_sql(self, sql, params=None):
        self._engine.calls.append((" ".join(sql.split()), params))
        if self._engine.raises is not None:
            raise self._engine.raises
        for pattern, rows in self._engine.responses.items():
            if pattern in sql:
                return _FakeResult(rows)
        return _FakeResult([])


class _FakeEngine:
    def __init__(self, responses=None, raises=None):
        self.responses = responses or {}
        self.raises = raises
        self.calls: list[tuple[str, object]] = []

    def connect(self):
        return _FakeConnection(self)


def _row(chunk_id, source_doc, heading, content, meta, distance):
    return (chunk_id, source_doc, heading, content, meta, distance)


def _kb_with_engine(engine, **kwargs) -> KnowledgeBase:
    kb = _kb(_StaticEmbedder(), **kwargs)
    kb._engine = engine
    return kb


def test_collections_are_discovered_from_the_catalog_and_filtered():
    engine = _FakeEngine({
        "pg_class": [("ddl_index_embeddings",), ("business_index_embeddings",), ("Bad-Name_embeddings",)]
    })
    kb = _kb_with_engine(engine)
    assert kb.collections() == ["ddl_index_embeddings", "business_index_embeddings"]
    # Discovery matches the naming convention rather than a hardcoded list.
    sql, params = engine.calls[0]
    assert "relkind = 'r'" in sql
    assert params == ("%_embeddings",)


def test_collection_discovery_is_cached():
    engine = _FakeEngine({"pg_class": [("ddl_index_embeddings",)]})
    kb = _kb_with_engine(engine)
    kb.collections()
    kb.collections()
    assert len(engine.calls) == 1


def test_configured_collections_skip_catalog_discovery_entirely():
    engine = _FakeEngine({"pg_class": [("should_not_be_used_embeddings",)]})
    kb = _kb_with_engine(engine, collections=["ddl_index_embeddings"])
    assert kb.collections() == ["ddl_index_embeddings"]
    assert engine.calls == []


def test_embedding_models_aggregates_across_collections():
    engine = _FakeEngine({"DISTINCT embedding_model": [("bge-m3",), (None,)]})
    kb = _kb_with_engine(engine, collections=["a_embeddings", "b_embeddings"])
    assert kb.embedding_models() == {"bge-m3"}


def test_search_maps_rows_onto_chunks():
    engine = _FakeEngine({
        "embedding <=>": [
            _row("c1", "business_index", "B > rule", "body text", {"table": "dim_store"}, 0.25)
        ]
    })
    kb = _kb_with_engine(engine, collections=["business_index_embeddings"])

    [chunk] = kb.search("q")

    assert chunk.collection == "business_index_embeddings"
    assert chunk.chunk_id == "c1"
    assert chunk.source_doc == "business_index"
    assert chunk.heading_path == "B > rule"
    assert chunk.content == "body text"
    assert chunk.table == "dim_store"
    assert chunk.distance == 0.25


def test_search_passes_the_embedding_as_a_pgvector_literal_and_honors_top_k():
    engine = _FakeEngine({"embedding <=>": []})
    kb = _kb_with_engine(engine, collections=["a_embeddings"])
    kb.search("q", top_k=7)

    search_sql, params = [c for c in engine.calls if "embedding <=>" in c[0]][0]
    literal, literal_again, limit = params
    assert literal == _vector_literal(_StaticEmbedder().vector)
    # Same literal for the SELECT distance and the ORDER BY, so the ranking
    # and the reported distance cannot drift apart.
    assert literal_again == literal
    assert limit == 7
    assert "::vector" in search_sql


def test_search_merges_collections_and_sorts_by_distance():
    engine = _FakeEngine({
        "embedding <=>": [
            _row("far", "d", "h", "c", {}, 0.9),
            _row("near", "d", "h", "c", {}, 0.1),
        ]
    })
    kb = _kb_with_engine(engine, collections=["a_embeddings", "b_embeddings"])

    chunks = kb.search("q")

    assert [c.distance for c in chunks] == [0.1, 0.1, 0.9, 0.9]
    assert len({c.collection for c in chunks}) == 2


def test_search_applies_a_statement_timeout():
    engine = _FakeEngine({"embedding <=>": []})
    kb = _kb_with_engine(engine, collections=["a_embeddings"], statement_timeout_ms=1234)
    kb.search("q")
    assert any("SET statement_timeout = 1234" in sql for sql, _ in engine.calls)


def test_search_handles_a_null_chunk_meta():
    engine = _FakeEngine({"embedding <=>": [_row("c1", "d", None, None, None, 0.5)]})
    kb = _kb_with_engine(engine, collections=["a_embeddings"])
    [chunk] = kb.search("q")
    assert chunk.meta == {}
    assert chunk.heading_path == ""
    assert chunk.content == ""
    assert chunk.table is None


def test_search_does_not_double_wrap_an_already_wrapped_failure():
    original = KnowledgeUnavailableError("the original message")
    engine = _FakeEngine(raises=original)
    kb = _kb_with_engine(engine, collections=["a_embeddings"])
    with pytest.raises(KnowledgeUnavailableError) as excinfo:
        kb.search("q")
    assert str(excinfo.value) == "the original message"


# ---------------------------------------------------------------------------
# build_embedder
# ---------------------------------------------------------------------------


def test_build_embedder_configures_ollama_from_settings():
    from types import SimpleNamespace

    from nl2sql_agent.retrieval import build_embedder

    embedder = build_embedder(
        SimpleNamespace(embed_model="bge-m3", embed_base_url="http://embedhost:11434")
    )
    assert embedder.model == "bge-m3"
    assert embedder.base_url == "http://embedhost:11434"
    # Satisfies the Embedder protocol the KnowledgeBase depends on.
    assert hasattr(embedder, "embed_query")


# ---------------------------------------------------------------------------
# Excluding a collection: how v4 splits this store between two agents
# ---------------------------------------------------------------------------


def test_an_excluded_collection_is_left_out_of_discovery():
    """v4 gives the DDL chunks to the Schema Retriever and everything else to
    the Knowledge Retriever. Without the exclusion the knowledge budget is
    spent re-describing tables whose full definition the generator already has.
    """
    engine = _FakeEngine({
        "pg_class": [
            ("ddl_index_embeddings",),
            ("business_index_embeddings",),
            ("data_dictionary_embeddings",),
        ]
    })
    kb = _kb_with_engine(engine, exclude_collections=["ddl_index_embeddings"])
    assert kb.collections() == ["business_index_embeddings", "data_dictionary_embeddings"]


def test_an_excluded_collection_is_left_out_of_a_configured_list_too():
    """Both ways of naming collections have to honour the exclusion, or the
    two agents overlap whenever a caller pins the list.
    """
    engine = _FakeEngine({})
    kb = _kb_with_engine(
        engine,
        collections=["ddl_index_embeddings", "business_index_embeddings"],
        exclude_collections=["ddl_index_embeddings"],
    )
    assert kb.collections() == ["business_index_embeddings"]


def test_excluding_nothing_leaves_every_collection_searchable():
    engine = _FakeEngine({"pg_class": [("a_embeddings",), ("b_embeddings",)]})
    assert _kb_with_engine(engine).collections() == ["a_embeddings", "b_embeddings"]


def test_the_exclusion_does_not_defeat_discovery_caching():
    engine = _FakeEngine({"pg_class": [("a_embeddings",), ("ddl_index_embeddings",)]})
    kb = _kb_with_engine(engine, exclude_collections=["ddl_index_embeddings"])
    assert kb.collections() == ["a_embeddings"]
    assert kb.collections() == ["a_embeddings"]
    assert len(engine.calls) == 1


def test_searching_never_touches_an_excluded_collection():
    """The real guarantee: not merely absent from the listing, but never
    queried, so the two retrievers cannot return each other's chunks.
    """
    engine = _FakeEngine({"pg_class": [("ddl_index_embeddings",), ("business_index_embeddings",)]})
    kb = _kb_with_engine(engine, exclude_collections=["ddl_index_embeddings"])
    kb.search("anything")
    queried = " ".join(sql for sql, _ in engine.calls)
    assert "business_index_embeddings" in queried
    assert "ddl_index_embeddings" not in queried
