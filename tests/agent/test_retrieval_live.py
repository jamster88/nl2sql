"""Retrieval against the real pgvector knowledge base and a real embedding
model -- the part that fakes cannot prove: that the query embedding lands in
the same space as the stored vectors and actually surfaces the right chunk.

Opt-in (`pytest --run-docker`). Defaults to the compose vectordb published on
localhost:5434 and the bge-m3 served by the Ollama on this machine. Skips,
rather than fails, when either is unavailable.
"""

from __future__ import annotations

import os

import pytest
from nl2sql_agent.retrieval import KnowledgeBase, build_embedder, format_chunks, tables_mentioned

pytestmark = pytest.mark.docker

VECTOR_DB_URL = os.environ.get(
    "TEST_VECTOR_DB_URL", "postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors"
)
EMBED_BASE_URL = os.environ.get("TEST_EMBED_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("TEST_EMBED_MODEL", "bge-m3")


class _Settings:
    embed_model = EMBED_MODEL
    embed_base_url = EMBED_BASE_URL


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    knowledge_base = KnowledgeBase(VECTOR_DB_URL, build_embedder(_Settings()), top_k=4)
    try:
        collections = knowledge_base.collections()
    except Exception as exc:
        pytest.skip(f"no reachable vector store at {VECTOR_DB_URL}: {exc}")
    if not collections:
        pytest.skip("vector store is reachable but has no collections")
    try:
        knowledge_base.search("smoke test")
    except Exception as exc:
        pytest.skip(f"embedding model {EMBED_MODEL} unavailable at {EMBED_BASE_URL}: {exc}")
    return knowledge_base


def test_collections_are_discovered_from_the_catalog(kb: KnowledgeBase):
    collections = kb.collections()
    assert all(c.endswith("_embeddings") for c in collections)
    assert "ddl_index_embeddings" in collections


def test_store_was_built_with_the_model_we_query_with(kb: KnowledgeBase):
    """A store embedded with a different model than the query uses puts the
    vectors in different spaces and silently returns noise.
    """
    models = kb.embedding_models()
    assert models, "no embedding_model recorded in the store"
    assert any(EMBED_MODEL.split(":")[0] in m for m in models), (
        f"store was built with {models}, but queries use {EMBED_MODEL}"
    )


def test_search_returns_chunks_from_every_collection(kb: KnowledgeBase):
    chunks = kb.search("What tables describe promotions and their performance?")
    assert chunks
    assert len({c.collection for c in chunks}) > 1
    # Sorted best-match first.
    assert chunks == sorted(chunks, key=lambda c: c.distance)


def test_top_k_bounds_results_per_collection(kb: KnowledgeBase):
    chunks = kb.search("market share", top_k=1)
    per_collection: dict[str, int] = {}
    for chunk in chunks:
        per_collection[chunk.collection] = per_collection.get(chunk.collection, 0) + 1
    assert all(count == 1 for count in per_collection.values())


@pytest.mark.parametrize(
    ("question", "expected_fragment"),
    [
        ("How do I avoid double counting market share?", "fan-out"),
        ("Does fiscal year 2024 mean calendar 2024?", "iscal calendar"),
    ],
)
def test_known_questions_retrieve_the_chunk_that_answers_them(kb, question, expected_fragment):
    chunks = kb.search(question)
    headings = " | ".join(c.heading_path for c in chunks)
    assert expected_fragment in headings, f"got: {headings}"


def test_retrieval_surfaces_the_relevant_table_names(kb: KnowledgeBase):
    chunks = kb.search("Which stores sold the most last fiscal year?")
    assert "fact_pos_retail_sales" in tables_mentioned(chunks)


def test_formatted_context_is_prompt_ready(kb: KnowledgeBase):
    context = format_chunks(kb.search("market share"), max_chars=4000)
    assert context
    assert len(context) <= 4400
    assert "---" in context  # per-chunk provenance headers
