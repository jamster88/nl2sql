"""Second-stage reranking of the fused golden-pair candidates.

Fusing three rankings is not the same thing as reranking them. The fusion in
`examples.py` combines scores each retriever produced *independently*: query
against the pair's question, query against its reasoning target, query against
its keywords. No stage of that ever looks at the query and the whole pair
together, and nothing anywhere considers the retrieved set as a set.

So the shortlist gets a second pass that does both:

**Grounding.** Stage 1 searched three fields and ignored the rest. The pair's
`tables` and `sql_code` are never indexed by any of them, and they carry the
most literal evidence there is -- a question naming "Produce" should favour the
pair whose SQL says `department_name = 'Produce'`, and a question about market
share should favour the one selecting from `fact_market_share_weekly`. That is
information the first stage structurally could not use, which is what makes
this a rerank rather than a fourth retriever.

**Diversity.** Multi-shot is what the examples are for, and three near-identical
exemplars teach one pattern three times. Maximal Marginal Relevance trades a
little relevance for coverage, so the model sees three different shapes of
answer instead of three phrasings of the same one.

Both are deliberately cheap and deterministic. A cross-encoder would score the
query and the pair jointly with a model rather than lexically, and an LLM
reranker would do better still, but both cost a call per question and neither
can be checked offline; the measured numbers for what is here are in the README.
"""

from __future__ import annotations

import re
from typing import Callable, Protocol

# Rerank strategies, in increasing order of what they consider.
RERANK_NONE = "none"  # fusion order, unchanged
RERANK_RELEVANCE = "relevance"  # + grounding against tables/SQL
RERANK_MMR = "mmr"  # + diversity across the selected set
DEFAULT_RERANK = RERANK_MMR

# MMR: 1.0 is pure relevance, 0.0 pure diversity. 0.5 is measured, not guessed:
# recall was flat from 1.0 down to 0.3 on every evaluation set, because MMR's
# first pick is by construction the most relevant candidate, so only positions
# 2 and 3 move. Given that, the balanced value buys the most coverage for free.
DEFAULT_LAMBDA = 0.5
# How much the grounding score moves relevance. 0.25 captures the whole of the
# measured gain -- questions naming a table or column outright go from 5/8 to
# 6/8 at rank 3 -- while leaving the fused score, which is the better-validated
# signal, dominant. Higher values did no better on that set.
DEFAULT_GROUNDING_WEIGHT = 0.25
DEFAULT_RERANK_K = 8  # shortlist size entering the rerank

# Words that carry no signal about *which* pair is wanted. SQL keywords have to
# go: every pair's SQL contains SELECT and FROM, so matching on them would score
# every candidate identically and wash out the literals that do discriminate.
_SQL_NOISE = {
    "select", "from", "where", "join", "left", "inner", "outer", "on", "and", "or",
    "not", "null", "as", "by", "group", "order", "having", "limit", "with", "case",
    "when", "then", "else", "end", "asc", "desc", "distinct", "count", "sum", "avg",
    "min", "max", "round", "coalesce", "nullif", "cast", "over", "partition",
    "between", "exists", "using", "key", "true", "false", "filter", "greatest",
}
_ENGLISH_NOISE = {
    "the", "a", "an", "of", "for", "in", "on", "at", "to", "by", "is", "are", "was",
    "were", "be", "been", "we", "our", "us", "i", "my", "what", "which", "who",
    "how", "many", "much", "do", "does", "did", "show", "list", "find", "get",
    "give", "me", "all", "any", "and", "or", "not", "that", "this", "it", "its",
    "there", "their", "with", "without", "per", "each", "every", "some", "across",
}
_NOISE = _SQL_NOISE | _ENGLISH_NOISE

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_STRING_LITERAL = re.compile(r"'([^']*)'")

# A literal match is stronger evidence than an identifier-word match: 'Produce'
# appearing in a pair's SQL says that pair is about produce, while the word
# "sales" appearing in a column name says almost nothing.
LITERAL_WEIGHT = 2.0
IDENTIFIER_WEIGHT = 1.0


class Scored(Protocol):
    """What the reranker needs from a candidate: an id, a score, and text."""

    chunk_id: str
    score: float
    tables: str
    sql_code: str
    title: str


def terms(text: str) -> set[str]:
    """Content words of a phrase, lowercased, noise removed.

    Identifiers are split on underscores so `net_sales_amt` contributes `net`,
    `sales` and `amt` -- a question saying "net sales" should reach it.
    """
    words = {w.lower() for w in _WORD.findall(text.replace("_", " "))}
    return {w for w in words if len(w) > 2 and w not in _NOISE}


def grounding_vocabulary(pair: Scored) -> tuple[set[str], set[str]]:
    """The literals and identifier words a pair's SQL and tables actually use.

    Returned separately because they are weighted differently: a quoted literal
    is an entity this pair is specifically about.
    """
    literals: set[str] = set()
    for raw in _STRING_LITERAL.findall(pair.sql_code or ""):
        literals |= terms(raw)

    without_literals = _STRING_LITERAL.sub(" ", pair.sql_code or "")
    identifiers = terms(f"{pair.tables or ''} {without_literals} {pair.title or ''}")
    return literals, identifiers - literals


def grounding_score(question: str, pair: Scored) -> float:
    """How much of the question this pair's SQL and tables actually account for.

    Coverage of the *question's* terms rather than overlap of both vocabularies:
    a pair with a long query is not more relevant for having more words in it,
    which a Jaccard or dot-product score would wrongly reward.
    """
    wanted = terms(question)
    if not wanted:
        return 0.0
    literals, identifiers = grounding_vocabulary(pair)
    earned = sum(
        LITERAL_WEIGHT if term in literals else IDENTIFIER_WEIGHT
        for term in wanted
        if term in literals or term in identifiers
    )
    # Normalised by the best a pair could do: every question term matched as a
    # literal. Keeps the result in [0, 1] and comparable across questions.
    return earned / (LITERAL_WEIGHT * len(wanted))


def blended_relevance(
    question: str, pairs: list[Scored], grounding_weight: float = DEFAULT_GROUNDING_WEIGHT
) -> dict[str, float]:
    """Fused score moved by the grounding evidence, both on a [0, 1] scale.

    The fused score is already normalised when the weights sum to 1, but nothing
    guarantees a caller's weights do, so it is rescaled against this shortlist
    before being blended.
    """
    if not pairs:
        return {}
    fused = [p.score for p in pairs]
    low, high = min(fused), max(fused)
    spread = high - low
    blended = {}
    for pair in pairs:
        base = 1.0 if spread <= 0 else (pair.score - low) / spread
        ground = grounding_score(question, pair)
        blended[pair.chunk_id] = (1 - grounding_weight) * base + grounding_weight * ground
    return blended


def mmr_select(
    relevance: dict[str, float],
    similarity: Callable[[str, str], float],
    k: int,
    lambda_: float = DEFAULT_LAMBDA,
) -> list[str]:
    """Maximal Marginal Relevance: greedily pick relevant-but-not-redundant ids.

    At each step the next pick maximises
    `lambda * relevance - (1 - lambda) * max similarity to anything already
    picked`, so the first pick is always the most relevant candidate and later
    picks are penalised for repeating it.
    """
    remaining = sorted(relevance, key=lambda cid: (-relevance[cid], cid))
    selected: list[str] = []
    while remaining and len(selected) < k:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        best, best_score = None, None
        for cid in remaining:
            redundancy = max(similarity(cid, chosen) for chosen in selected)
            score = lambda_ * relevance[cid] - (1 - lambda_) * redundancy
            # Ties broken by id so the same shortlist always reranks the same way.
            if best_score is None or score > best_score or (score == best_score and cid < best):
                best, best_score = cid, score
        selected.append(best)
        remaining.remove(best)
    return selected


def rerank(
    question: str,
    pairs: list[Scored],
    k: int,
    *,
    strategy: str = DEFAULT_RERANK,
    similarity: Callable[[str, str], float] | None = None,
    lambda_: float = DEFAULT_LAMBDA,
    grounding_weight: float = DEFAULT_GROUNDING_WEIGHT,
) -> list[Scored]:
    """Reorder a fused shortlist and cut it to k.

    `similarity(a, b)` supplies pair-to-pair cosine for the MMR step; without
    one, MMR has no redundancy signal and degrades to the relevance ordering
    rather than guessing.
    """
    if strategy not in RERANK_STRATEGIES:
        raise ValueError(f"unknown rerank {strategy!r}; expected one of {sorted(RERANK_STRATEGIES)}")
    if not pairs or k <= 0:
        return []
    if strategy == RERANK_NONE:
        return pairs[:k]

    relevance = blended_relevance(question, pairs, grounding_weight)
    by_id = {p.chunk_id: p for p in pairs}

    if strategy == RERANK_MMR and similarity is not None:
        order = mmr_select(relevance, similarity, k, lambda_)
    else:
        order = sorted(relevance, key=lambda cid: (-relevance[cid], cid))[:k]

    chosen = []
    for cid in order:
        pair = by_id[cid]
        pair.rerank_score = relevance[cid]
        chosen.append(pair)
    return chosen


RERANK_STRATEGIES = {RERANK_NONE, RERANK_RELEVANCE, RERANK_MMR}
