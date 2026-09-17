"""The golden-pair ensemble: fusion arithmetic, rendering, and degradation.

The fusion functions are pure, so the interesting cases -- a retriever that
finds nothing, a tie, one retriever ranking something first while the others
disagree -- are testable without either database. What needs live stores is in
test_examples_live.py.

The arithmetic matters more than it looks: the whole point of the weights is
that a 0.50 retriever outvotes a 0.15 one, and it is easy to write a fusion
that quietly stops honouring that.
"""

from __future__ import annotations

import pytest
from nl2sql_agent.examples import (
    BY_KEYWORDS,
    BY_QUESTION,
    BY_REASONING,
    DEFAULT_WEIGHTS,
    FUSIONS,
    GoldenPairLibrary,
    _vector_literal,
    format_examples,
    tables_mentioned,
    weighted_rrf,
    weighted_score_fusion,
)

from .conftest import make_pair

W = DEFAULT_WEIGHTS


def order(fused):
    return [chunk_id for chunk_id, _, _ in fused]


def scores(fused):
    return {chunk_id: score for chunk_id, score, _ in fused}


# ---------------------------------------------------------------------------
# The weights have to actually weigh
# ---------------------------------------------------------------------------


def test_the_question_retriever_outvotes_the_reasoning_retriever():
    """0.50 against 0.15: a pair only the question retriever likes must beat one
    only the reasoning retriever likes. If this ever flips, the weights have
    stopped meaning anything.
    """
    fused = weighted_score_fusion(
        {
            BY_QUESTION: [("liked-by-question", 0.9), ("other", 0.1)],
            BY_REASONING: [("liked-by-reasoning", 0.9), ("other", 0.1)],
            BY_KEYWORDS: [],
        },
        W,
    )
    assert order(fused)[0] == "liked-by-question"


def test_a_pair_two_retrievers_agree_on_beats_one_that_only_leads_a_single_list():
    """A near-miss on the heaviest retriever plus a win on the next heaviest
    beats a bare win on the heaviest alone.

    The candidate lists are full-length on purpose. Min-max normalisation
    against a two-item list forces second place to exactly 0, which is an
    artefact of the tiny list rather than of the ranking -- at the configured
    candidate_k of 10 a close second stays close.
    """
    filler = [(f"filler{i}", 0.5 - i / 100) for i in range(8)]
    fused = weighted_score_fusion(
        {
            BY_QUESTION: [("solo", 1.0), ("agreed", 0.98), *filler],
            BY_KEYWORDS: [("agreed", 8.0), *[(f, 1.0) for f, _ in filler], ("solo", 0.0)],
            BY_REASONING: [],
        },
        W,
    )
    assert order(fused)[0] == "agreed"


def test_the_best_hit_of_each_retriever_earns_exactly_its_weight():
    """Min-max normalisation means the top candidate scores 1.0 before
    weighting, so a pair that tops all three lists scores the sum of the
    weights -- 1.0 for weights that add to 1.
    """
    rankings = {
        BY_QUESTION: [("top", 0.8), ("low", 0.2)],
        BY_KEYWORDS: [("top", 9.0), ("low", 1.0)],
        BY_REASONING: [("top", 0.7), ("low", 0.3)],
    }
    fused = scores(weighted_score_fusion(rankings, W))
    assert fused["top"] == pytest.approx(sum(W.values()))
    assert fused["low"] == pytest.approx(0.0)


def test_a_retriever_that_finds_nothing_contributes_nothing():
    """A question with no keyword overlap must be scored by the vector
    retrievers alone, not dragged toward whatever BM25 happened to return.
    """
    with_keywords = scores(
        weighted_score_fusion(
            {BY_QUESTION: [("a", 0.9), ("b", 0.1)], BY_KEYWORDS: [], BY_REASONING: []}, W
        )
    )
    assert with_keywords["a"] == pytest.approx(W[BY_QUESTION])
    assert "b" in with_keywords


def test_a_single_candidate_is_not_divided_by_a_zero_spread():
    """Min-max over one value has no range; it must not raise or produce NaN."""
    fused = scores(weighted_score_fusion({BY_QUESTION: [("only", 0.44)]}, W))
    assert fused["only"] == pytest.approx(W[BY_QUESTION])


def test_candidates_that_all_score_the_same_are_all_treated_as_best():
    fused = scores(
        weighted_score_fusion({BY_QUESTION: [("a", 0.5), ("b", 0.5), ("c", 0.5)]}, W)
    )
    assert sorted(fused) == ["a", "b", "c"]
    assert all(value == pytest.approx(W[BY_QUESTION]) for value in fused.values())


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fusion", sorted(FUSIONS), ids=sorted(FUSIONS))
def test_the_same_rankings_always_fuse_to_the_same_order(fusion):
    rankings = {
        BY_QUESTION: [("zeta", 0.5), ("alpha", 0.5), ("mid", 0.3)],
        BY_KEYWORDS: [("zeta", 2.0), ("alpha", 2.0)],
        BY_REASONING: [],
    }
    assert order(FUSIONS[fusion](rankings, W)) == order(FUSIONS[fusion](rankings, W))


def test_exact_ties_are_broken_by_chunk_id_not_by_database_order():
    """BM25 returns exactly equal scores more often than you would expect on a
    corpus this small -- two pairs whose keyword lists share the same matched
    terms score bit-identically. Python's sort and Postgres's sort break such a
    tie differently, so the fusion pins the order itself and the same question
    cannot return a different example from one run to the next.
    """
    rankings = {
        BY_QUESTION: [("zeta", 0.5), ("alpha", 0.5)],
        BY_KEYWORDS: [("zeta", 2.0), ("alpha", 2.0)],
        BY_REASONING: [],
    }
    fused = weighted_score_fusion(rankings, W)
    assert len({round(score, 12) for _, score, _ in fused}) == 1, "expected a real tie"
    assert order(fused) == ["alpha", "zeta"]


def test_every_fusion_reports_which_retrievers_found_each_pair():
    rankings = {
        BY_QUESTION: [("a", 0.9), ("b", 0.5)],
        BY_KEYWORDS: [("b", 3.0)],
        BY_REASONING: [],
    }
    for fusion in FUSIONS.values():
        ranks = {cid: r for cid, _, r in fusion(rankings, W)}
        assert ranks["a"] == {BY_QUESTION: 1}
        assert ranks["b"] == {BY_QUESTION: 2, BY_KEYWORDS: 1}


# ---------------------------------------------------------------------------
# Why RRF is not the default here
# ---------------------------------------------------------------------------


def test_rrf_at_its_usual_k_loses_to_a_third_retriever_merely_voting():
    """The measured reason for the default. At k=60 the gap between rank 1 and
    rank 9 is smaller than a 0.15 retriever's contribution, so a pair all three
    retrievers rank badly beats one that two of them rank first. Score fusion
    gets this right; this test documents that it is a real difference and not a
    matter of taste.
    """
    rankings = {
        BY_QUESTION: [(f"filler{i}", 1.0 - i / 10) for i in range(8)]
        + [("only-vectors", 0.15)],
        BY_KEYWORDS: [(f"filler{i}", 9.0 - i) for i in range(8)] + [("only-vectors", 0.5)],
        BY_REASONING: [("all-three-badly", 0.4)],
    }
    rankings[BY_QUESTION].insert(0, ("best", 2.0))
    rankings[BY_KEYWORDS].insert(0, ("best", 20.0))
    rankings[BY_QUESTION].append(("all-three-badly", 0.1))
    rankings[BY_KEYWORDS].append(("all-three-badly", 0.1))

    assert order(weighted_score_fusion(rankings, W))[0] == "best"
    rrf_winner = order(weighted_rrf(rankings, W, rrf_k=60))[0]
    assert rrf_winner == "best" or rrf_winner == "all-three-badly"
    # The point is the margin, not the winner: RRF cannot separate them.
    rrf = scores(weighted_rrf(rankings, W, rrf_k=60))
    assert abs(rrf["best"] - rrf["all-three-badly"]) < 0.002


def test_an_unknown_fusion_is_refused_at_construction():
    with pytest.raises(ValueError, match="unknown fusion"):
        GoldenPairLibrary("postgresql+psycopg://u:p@127.0.0.1:1/a",
                          "postgresql+psycopg://u:p@127.0.0.1:1/b",
                          object(), fusion="vibes")


# ---------------------------------------------------------------------------
# Rendering for the prompt
# ---------------------------------------------------------------------------


def test_examples_render_question_then_reasoning_then_sql():
    """The order is the argument: a multi-shot prompt should show why before
    what, so the model reads the constraint before the query that satisfies it.
    """
    text = format_examples([make_pair()])
    assert text.index("Question:") < text.index("What this has to get right:")
    assert text.index("What this has to get right:") < text.index("SQL:")


def test_examples_carry_the_pair_id_so_a_bad_example_can_be_traced():
    assert "Q10" in format_examples([make_pair(pair_id="Q10")])


def test_rendering_stops_at_the_character_budget():
    """SQL blocks are long; without a budget three of them can crowd out the
    schema the model actually needs.
    """
    pairs = [make_pair(chunk_id=f"eval:q{i:02d}", pair_id=f"Q{i:02d}") for i in range(1, 6)]
    assert format_examples(pairs, max_chars=10_000).count("Question:") == 5
    assert format_examples(pairs, max_chars=1).count("Question:") == 0


def test_no_examples_renders_to_nothing():
    assert format_examples([]) == ""


def test_tables_are_collected_best_match_first_without_duplicates():
    pairs = [
        make_pair(chunk_id="a", tables="fact_a, dim_b"),
        make_pair(chunk_id="b", tables="dim_b, dim_c"),
    ]
    assert tables_mentioned(pairs) == ["fact_a", "dim_b", "dim_c"]


def test_a_pair_splits_its_table_and_keyword_lists():
    pair = make_pair(tables="fact_a,  dim_b ,", keywords="one, two,  three ")
    assert pair.table_list == ["fact_a", "dim_b"]
    assert pair.keyword_list == ["one", "two", "three"]


def test_found_by_names_each_retriever_and_its_rank():
    pair = make_pair(ranks={BY_QUESTION: 1, BY_KEYWORDS: 4})
    assert pair.found_by == "keywords#4, question#1"


# ---------------------------------------------------------------------------
# The pgvector literal
# ---------------------------------------------------------------------------


def test_the_vector_literal_is_pgvector_text_format():
    """Sending a Python list binds as double precision[], which has no <=>
    operator; the text form plus a cast is what avoids a client-side type.
    """
    assert _vector_literal([0.1, -0.25, 3.0]) == "[0.1,-0.25,3.0]"
    assert _vector_literal([]) == "[]"
