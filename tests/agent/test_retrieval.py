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
