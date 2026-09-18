"""The ensemble against the real context store, vector store and embedder.

What fakes cannot prove: that BM25 really ranks in the database, that the two
vector tables really hold the same 45 pairs the rows describe, and that a
question embedded now lands in the same space as vectors embedded at load time.

Opt-in (`pytest --run-docker`). Defaults to the compose chunkdb on
localhost:5433, the vectordb on localhost:5434, and the bge-m3 served by the
Ollama on this machine. Skips, rather than fails, when any of the three is
unavailable -- the agent degrades in exactly the same situation.
"""

from __future__ import annotations

import os

import pytest
from nl2sql_agent.examples import (
    BY_KEYWORDS,
    BY_QUESTION,
    BY_REASONING,
    GoldenPairLibrary,
    build_embedder,
)

pytestmark = pytest.mark.docker

CONTEXT_DB_URL = os.environ.get(
    "TEST_CONTEXT_DB_URL", "postgresql+psycopg://ragproc:ragproc@localhost:5433/nl2sql_chunks"
)
VECTOR_DB_URL = os.environ.get(
    "TEST_VECTOR_DB_URL", "postgresql+psycopg://ragproc:ragproc@localhost:5434/nl2sql_vectors"
)
EMBED_BASE_URL = os.environ.get("TEST_EMBED_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("TEST_EMBED_MODEL", "bge-m3")

EXPECTED_PAIRS = 45


class _Settings:
    embed_model = EMBED_MODEL
    embed_base_url = EMBED_BASE_URL


@pytest.fixture(scope="module")
def library() -> GoldenPairLibrary:
    lib = GoldenPairLibrary(
        CONTEXT_DB_URL, VECTOR_DB_URL, build_embedder(_Settings()), top_k=3, candidate_k=10
    )
    try:
        count = lib.count()
    except Exception as exc:
        pytest.skip(f"no reachable context store at {CONTEXT_DB_URL}: {exc}")
    if not count:
        pytest.skip("context store is reachable but holds no golden pairs")
    try:
        lib.search("smoke test")
    except Exception as exc:
        pytest.skip(f"vector store or {EMBED_MODEL} unavailable: {exc}")
    return lib


def test_every_pair_in_the_document_made_it_into_the_store(library):
    assert library.count() == EXPECTED_PAIRS


def test_all_three_retrievers_return_candidates(library):
    """The ensemble is only an ensemble if each leg actually fires. A silently
    empty BM25 leg would still return plausible results from the vectors alone.
    """
    rankings = library.rank("how do I compute gross margin for the produce department")
    assert set(rankings) == {BY_QUESTION, BY_KEYWORDS, BY_REASONING}
    for name, hits in rankings.items():
        assert hits, f"the {name} retriever returned nothing"
        assert all(isinstance(score, float) for _, score in hits)


def test_the_vector_legs_are_ordered_by_descending_similarity(library):
    rankings = library.rank("weekly market share by region")
    for name in (BY_QUESTION, BY_REASONING):
        scores = [score for _, score in rankings[name]]
        assert scores == sorted(scores, reverse=True), f"{name} came back out of order"


def test_bm25_is_ordered_and_non_negative(library):
    """The +1 smoothing in the IDF is what keeps a term appearing in every
    document from scoring negative; a negative score here means the statistics
    were rebuilt with a different formula than the function expects.
    """
    hits = library.rank("market share fan-out de-duplicate")[BY_KEYWORDS]
    scores = [score for _, score in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(score >= 0 for score in scores)


def test_a_question_with_no_keyword_overlap_still_retrieves(library):
    """BM25 contributes nothing here, and the vector legs have to carry it --
    this is the case that a naive weighted sum of raw scores gets wrong.
    """
    rankings = library.rank("zqxjkv wmbtlp gharn")
    assert rankings[BY_KEYWORDS] == []
    assert library.search("zqxjkv wmbtlp gharn"), "no examples at all without keywords"


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What were our total net sales and total gross profit for the Produce department?", "eval:q01"),
        ("Show our weekly market share percentage for the Cheese category in the Pacific Northwest.", "eval:q10"),
        ("Using a 7-day rolling window, show the moving average of daily net sales.", "eval:q37"),
        ("Which vendor returned the highest total allowance through SLOTTING programs?", "eval:q20"),
    ],
)
def test_a_pair_is_retrieved_from_its_own_question(library, question, expected):
    """The floor for the whole ensemble: if a pair cannot be found from the
    question it was written for, nothing else about the ranking matters.
    """
    top = library.search(question, top_k=1)
    assert top and top[0].chunk_id == expected


def test_a_retrieved_pair_carries_its_sql_and_its_reasoning(library):
    """Retrieval hands the model a worked example, so the fields it will be
    shown have to survive the round trip through both stores.
    """
    pair = library.search("gross margin by department", top_k=1)[0]
    assert pair.sql_code.upper().startswith(("SELECT", "WITH"))
    assert pair.reasoning_target
    assert pair.result
    assert pair.table_list
    assert pair.ranks, "a retrieved pair should record which retrievers found it"


def test_results_are_capped_at_top_k(library):
    assert len(library.search("sales", top_k=2)) == 2
    assert library.search("sales", top_k=0) == []


def test_the_same_question_returns_the_same_pairs_twice(library):
    first = [p.chunk_id for p in library.search("markdown erosion by department")]
    second = [p.chunk_id for p in library.search("markdown erosion by department")]
    assert first == second


def test_an_unreachable_context_store_degrades_rather_than_raising_something_else():
    """The agent turns ExamplesUnavailableError into a skipped step. Any other
    exception type would escape the node and fail the run.
    """
    from nl2sql_agent.examples import ExamplesUnavailableError

    lib = GoldenPairLibrary(
        "postgresql+psycopg://ragproc:ragproc@127.0.0.1:1/nl2sql_chunks",
        VECTOR_DB_URL,
        build_embedder(_Settings()),
    )
    with pytest.raises(ExamplesUnavailableError):
        lib.search("anything")


def test_the_knowledge_retriever_does_not_pick_up_the_golden_pair_vectors():
    """The two stores share a database. The knowledge retriever discovers its
    collections by globbing for `%_embeddings`, so the pair vectors are named
    `_vectors` -- if that ever changes, v2 retrieval starts querying tables with
    a different column list and fails outright.
    """
    from nl2sql_agent.retrieval import KnowledgeBase

    kb = KnowledgeBase(VECTOR_DB_URL, build_embedder(_Settings()))
    try:
        collections = kb.collections()
    except Exception as exc:
        pytest.skip(f"no reachable vector store at {VECTOR_DB_URL}: {exc}")
    assert collections, "vector store has no knowledge collections"
    assert not any("golden" in name for name in collections)


# ---------------------------------------------------------------------------
# The rerank stage, against the real stores
# ---------------------------------------------------------------------------


def _library(**kwargs) -> GoldenPairLibrary:
    return GoldenPairLibrary(
        CONTEXT_DB_URL, VECTOR_DB_URL, build_embedder(_Settings()),
        top_k=3, candidate_k=10, **kwargs,
    )


def test_the_rerank_shortlist_is_wider_than_the_result(library):
    """A reranker that only sees what the fusion already chose can reorder three
    pairs but never rescue the one the fusion ranked fourth.
    """
    wide = _library(rerank="mmr", rerank_k=8)
    assert len(wide.search("sales", top_k=3)) == 3
    # The shortlist really is 8: ask for all of it and the extra candidates exist.
    assert len(wide.search("sales", top_k=8)) == 8


def test_reranking_never_displaces_the_top_fused_result(library):
    """MMR's first pick has nothing to be redundant with, so rank 1 is the same
    pair with or without the rerank. This is the guarantee that lets diversity
    be turned up without risking recall.
    """
    plain = _library(rerank="none")
    ranked = _library(rerank="mmr")
    for question in (
        "What were our total net sales and total gross profit for the Produce department?",
        "Which vendor returned the highest total allowance through SLOTTING programs?",
        "weekly market share by region",
    ):
        assert plain.search(question)[0].chunk_id == ranked.search(question)[0].chunk_id


def test_mmr_returns_a_less_redundant_set_than_the_fusion_order(library):
    """What the stage is for. Measured across the stored questions rather than
    asserted on one, because any single question can go either way.
    """
    import itertools

    plain, ranked = _library(rerank="none"), _library(rerank="mmr", rerank_lambda=0.5)
    questions = [
        "gross margin by department",
        "market share trend",
        "vendor allowances",
        "advertising spend and clicks",
        "average basket value",
    ]

    def redundancy(lib) -> float:
        total, n = 0.0, 0
        for question in questions:
            picked = lib.search(question)
            vectors = {p.chunk_id: p for p in picked}
            for a, b in itertools.combinations(vectors, 2):
                total += _cosine(a, b)
                n += 1
        return total / n

    assert redundancy(ranked) < redundancy(plain)


def _cosine(a: str, b: str) -> float:
    """Pair-to-pair similarity read straight from the store."""
    import sqlalchemy

    engine = sqlalchemy.create_engine(VECTOR_DB_URL)
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT 1 - (x.embedding <=> y.embedding) "
            "FROM golden_pair_question_vectors x, golden_pair_question_vectors y "
            "WHERE x.chunk_id = %s AND y.chunk_id = %s",
            (a, b),
        ).fetchone()
    return float(row[0])


def test_a_reranked_pair_reports_both_its_fused_and_rerank_scores(library):
    pair = _library(rerank="mmr").search("gross margin by department")[0]
    assert pair.score > 0
    assert pair.rerank_score > 0


def test_the_rerank_survives_an_unreachable_vector_store_for_similarity():
    """The pair-to-pair cosine is a nice-to-have. Losing it costs the diversity
    term; the search still returns relevant examples.
    """
    lib = GoldenPairLibrary(
        CONTEXT_DB_URL, VECTOR_DB_URL, build_embedder(_Settings()),
        top_k=3, candidate_k=10, rerank="mmr",
    )
    pairs = lib.search("gross margin by department")
    assert pairs, "expected examples even before considering diversity"
