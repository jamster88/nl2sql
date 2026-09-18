"""The second-stage reranker: grounding, MMR, and the guarantees around them.

All pure functions, so every interesting case is reachable without a database.
The two properties worth defending are that the rerank never damages the top
result -- it is the one the fusion was most confident about -- and that the
diversity term actually diversifies.
"""

from __future__ import annotations

import pytest
from nl2sql_agent.rerank import (
    RERANK_MMR,
    RERANK_NONE,
    RERANK_RELEVANCE,
    blended_relevance,
    grounding_score,
    grounding_vocabulary,
    mmr_select,
    rerank,
    terms,
)

from .conftest import make_pair


def pair(chunk_id, score, *, tables="", sql="SELECT 1", title=""):
    return make_pair(chunk_id=chunk_id, pair_id=chunk_id.upper(), tables=tables,
                     sql_code=sql, title=title, score=score)


# ---------------------------------------------------------------------------
# Tokenising
# ---------------------------------------------------------------------------


def test_identifiers_are_split_so_a_question_can_reach_a_column_name():
    assert terms("net_sales_amt") == {"net", "sales", "amt"}


def test_sql_and_english_noise_words_are_dropped():
    """Every pair's SQL contains SELECT and FROM. Matching on them would score
    every candidate identically and drown out the literals that discriminate.
    """
    assert terms("SELECT sum FROM where group by") == set()
    assert terms("what are the top stores for us") == {"top", "stores"}


def test_very_short_tokens_are_dropped():
    # `a`, `s`, `c` are table aliases in almost every pair's SQL.
    assert terms("a s c d1 revenue") == {"revenue"}


# ---------------------------------------------------------------------------
# Grounding: the signal stage one never saw
# ---------------------------------------------------------------------------


def test_string_literals_are_separated_from_identifiers():
    literals, identifiers = grounding_vocabulary(
        pair("a", 1.0, tables="fact_pos_retail_sales",
             sql="SELECT x FROM t WHERE department_name = 'Produce'")
    )
    assert "produce" in literals
    assert "department" in identifiers
    assert "produce" not in identifiers


def test_a_literal_match_outweighs_an_identifier_match():
    """'Produce' appearing in a pair's SQL says that pair is about produce. The
    word "sales" appearing in a column name says almost nothing.
    """
    literal = pair("a", 1.0, sql="SELECT 1 FROM t WHERE department_name = 'Produce'")
    identifier = pair("b", 1.0, sql="SELECT produce_count FROM t")
    assert grounding_score("produce", literal) > grounding_score("produce", identifier)


def test_grounding_scores_coverage_of_the_question_not_of_the_pair():
    """A long query is not more relevant for having more words in it -- which is
    what a Jaccard or dot-product score would wrongly reward.
    """
    short = pair("a", 1.0, tables="fact_market_share_weekly")
    long = pair("b", 1.0, tables="fact_market_share_weekly",
                sql="SELECT " + ", ".join(f"col_{i}" for i in range(50)) + " FROM t")
    assert grounding_score("market share", short) == grounding_score("market share", long)


def test_grounding_is_bounded_to_the_unit_interval():
    every_term_a_literal = pair("a", 1.0, sql="SELECT 1 WHERE x = 'market share weekly'")
    assert grounding_score("market share weekly", every_term_a_literal) == 1.0
    assert grounding_score("nothing matches here", pair("b", 1.0)) == 0.0


def test_a_question_of_pure_noise_words_scores_zero_rather_than_dividing_by_zero():
    assert grounding_score("the a of for", pair("a", 1.0)) == 0.0
    assert grounding_score("", pair("a", 1.0)) == 0.0


def test_grounding_reads_tables_which_no_first_stage_retriever_indexes():
    """BM25 searches the keywords column only, and the two vector legs search
    the question and reasoning text. The tables column is the rerank's own.
    """
    named = pair("a", 1.0, tables="fact_market_share_weekly, dim_geography")
    other = pair("b", 1.0, tables="dim_store")
    assert grounding_score("market share by region", named) > grounding_score(
        "market share by region", other
    )


# ---------------------------------------------------------------------------
# Blending
# ---------------------------------------------------------------------------


def test_the_fused_score_still_dominates_at_the_default_weight():
    """Grounding corrects the fusion; it does not overrule it. A candidate the
    fusion ranked far ahead must stay ahead of one with slightly better lexical
    overlap.
    """
    favourite = pair("a", 0.9, sql="SELECT 1")
    wordy = pair("b", 0.1, tables="stores sales revenue region", sql="SELECT 1")
    blended = blended_relevance("stores sales revenue region", [favourite, wordy])
    assert blended["a"] > blended["b"]


def test_blending_rescales_fused_scores_that_do_not_span_zero_to_one():
    """Nothing forces a caller's ensemble weights to sum to 1, so the fused
    score is renormalised against the shortlist before it is blended.
    """
    blended = blended_relevance("x", [pair("a", 7.0), pair("b", 3.0)], grounding_weight=0.0)
    assert blended["a"] == pytest.approx(1.0)
    assert blended["b"] == pytest.approx(0.0)


def test_a_shortlist_with_no_spread_is_not_divided_by_zero():
    blended = blended_relevance("x", [pair("a", 0.5), pair("b", 0.5)], grounding_weight=0.0)
    assert blended == {"a": pytest.approx(1.0), "b": pytest.approx(1.0)}


# ---------------------------------------------------------------------------
# MMR
# ---------------------------------------------------------------------------


def test_the_first_pick_is_always_the_most_relevant_candidate():
    """This is why MMR costs no recall@1: the first selection has nothing to be
    redundant with, so the diversity term cannot touch it at any lambda.
    """
    relevance = {"a": 0.9, "b": 0.8, "c": 0.1}
    for lam in (0.0, 0.3, 0.5, 0.9, 1.0):
        assert mmr_select(relevance, lambda x, y: 1.0, k=3, lambda_=lam)[0] == "a"


def test_a_near_duplicate_is_passed_over_for_something_different():
    """The point of the whole stage: three exemplars that say the same thing
    teach one pattern three times.
    """
    relevance = {"a": 1.0, "twin": 0.9, "different": 0.6}
    similar = {("a", "twin"): 0.99, ("a", "different"): 0.1, ("twin", "different"): 0.1}

    def similarity(x, y):
        return 1.0 if x == y else similar.get(tuple(sorted((x, y))), 0.0)

    assert mmr_select(relevance, similarity, k=2, lambda_=0.5) == ["a", "different"]
    # With diversity switched off, raw relevance wins and the twin comes back.
    assert mmr_select(relevance, similarity, k=2, lambda_=1.0) == ["a", "twin"]


def test_mmr_returns_at_most_k_and_never_repeats():
    relevance = {c: 1.0 - i / 10 for i, c in enumerate("abcdef")}
    picked = mmr_select(relevance, lambda x, y: 0.5, k=3)
    assert len(picked) == 3
    assert len(set(picked)) == 3


def test_mmr_is_deterministic_when_scores_tie():
    relevance = {"b": 0.5, "a": 0.5, "c": 0.5}
    first = mmr_select(relevance, lambda x, y: 0.0, k=3)
    assert first == mmr_select(relevance, lambda x, y: 0.0, k=3)
    assert first[0] == "a"


# ---------------------------------------------------------------------------
# The rerank entry point
# ---------------------------------------------------------------------------


def test_none_leaves_the_fusion_order_alone():
    pairs = [pair("a", 0.1), pair("b", 0.9)]
    assert [p.chunk_id for p in rerank("q", pairs, 2, strategy=RERANK_NONE)] == ["a", "b"]


def test_relevance_reorders_but_does_not_diversify():
    pairs = [pair("a", 0.1), pair("b", 0.9)]
    assert [p.chunk_id for p in rerank("q", pairs, 2, strategy=RERANK_RELEVANCE)] == ["b", "a"]


def test_mmr_without_a_similarity_source_degrades_to_relevance_order():
    """The pair-to-pair cosine comes from the vector store, which can be down.
    Losing it must cost the diversity term, not the whole search.
    """
    pairs = [pair("a", 0.1), pair("b", 0.9)]
    assert [p.chunk_id for p in rerank("q", pairs, 2, strategy=RERANK_MMR, similarity=None)] == [
        "b",
        "a",
    ]


def test_the_rerank_cuts_the_shortlist_to_k():
    pairs = [pair(c, 1.0 - i / 10) for i, c in enumerate("abcdef")]
    assert len(rerank("q", pairs, 3, strategy=RERANK_MMR, similarity=lambda x, y: 0.0)) == 3


def test_reranked_pairs_carry_both_scores():
    """The fused score and the rerank score are both kept so --json shows which
    stage moved a pair, instead of one silently overwriting the other.
    """
    [top] = rerank("q", [pair("a", 0.7)], 1, strategy=RERANK_RELEVANCE)
    assert top.score == 0.7
    assert top.rerank_score > 0


def test_an_empty_shortlist_or_zero_k_returns_nothing():
    assert rerank("q", [], 3) == []
    assert rerank("q", [pair("a", 1.0)], 0) == []


def test_an_unknown_strategy_is_refused():
    with pytest.raises(ValueError, match="unknown rerank"):
        rerank("q", [pair("a", 1.0)], 1, strategy="vibes")
