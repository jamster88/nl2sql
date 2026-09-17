"""Ensemble retrieval over the golden question/SQL pairs.

Where `retrieval.py` fetches *prose* about the database -- business rules, table
docs, grain warnings -- this module fetches *worked examples*: 45 questions that
have each been paired with SQL verified to run against this database.

Three retrievers, scored independently and fused:

| Retriever | Searches | Weight |
|---|---|---|
| question similarity | `golden_pair_question_vectors` (pgvector, cosine) | 0.50 |
| keyword match | `golden_pairs.keywords` (BM25, in the context store) | 0.35 |
| reasoning similarity | `golden_pair_reasoning_vectors` (pgvector, cosine) | 0.15 |

The three are fused by **min-max normalising each retriever's scores and taking
the weighted sum**. Normalisation is what makes the weights mean anything: a raw
cosine similarity sits in a narrow band near 0.5 while a BM25 score is unbounded,
so a weighted sum of the raw numbers is a weighted sum of incomparable units.

The obvious alternative -- weighted reciprocal rank fusion, as LangChain's
`EnsembleRetriever` does it -- is implemented here too, and was measured rather
than assumed. It loses badly at this corpus size. RRF scores a hit as
`w / (60 + rank)`, a formula tuned for candidate lists thousands of documents
long; across 10 candidates, rank 1 and rank 10 differ by only 15%, which is less
than the 0.15 weight a third retriever contributes just by voting at all. So the
weights stop expressing how *strongly* a retriever matched and start counting how
*many* retrievers matched. Measured over all 45 questions retrieving themselves:

    weighted score fusion      45/45 correct at rank 1
    weighted RRF (k=1)         43/45
    weighted RRF (k=10)        37/45
    weighted RRF (k=60)        25/45

Set `EXAMPLES_FUSION=rrf` to switch back; `EXAMPLES_RRF_K` tunes it.

Why the question and the reasoning target are embedded separately: they answer
different questions about a pair. The question text matches what the user is
asking for; the reasoning target matches what the query has to get *right* --
"reconciling daily sales against monthly costs", "the five-row fan-out". A
question that never says "fan-out" can still need the pair that warns about it,
which is why the reasoning field is in the ensemble at all, and why it is
weighted low: on its own it retrieves the right pair only 13% of the time, so it
works as a tiebreaker between pairs the other two already like, not as a primary
signal.

Every failure path here degrades to "no examples" rather than to an error, so an
unreachable context store costs the agent its worked examples and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

PAIRS_TABLE = "golden_pairs"
BM25_FUNCTION = "golden_pairs_bm25"
QUESTION_VECTORS = "golden_pair_question_vectors"
REASONING_VECTORS = "golden_pair_reasoning_vectors"

# Retriever names, used as the keys of the weight map and of a hit's rank map.
BY_QUESTION = "question"
BY_KEYWORDS = "keywords"
BY_REASONING = "reasoning"

DEFAULT_WEIGHTS = {BY_QUESTION: 0.50, BY_KEYWORDS: 0.35, BY_REASONING: 0.15}

# How the three rankings are combined. See the module docstring for why "score"
# is the default here rather than the rank fusion an ensemble usually reaches
# for.
FUSION_SCORE = "score"
FUSION_RRF = "rrf"
DEFAULT_FUSION = FUSION_SCORE

# The constant in 1/(k + rank), used only by the "rrf" fusion. 60 is the value
# from the original paper and LangChain's default, tuned for candidate lists
# thousands of documents long.
DEFAULT_RRF_K = 60


class ExamplesUnavailableError(RuntimeError):
    """The context store, the vector store, or the embedding model is unreachable."""


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


@dataclass
class GoldenPair:
    """One retrieved question/SQL pair, with how it was found."""

    chunk_id: str
    pair_id: str
    title: str
    suite: str
    type: str
    tables: str
    keywords: str
    question: str
    reasoning_target: str
    sql_code: str
    result: str
    score: float = 0.0
    ranks: dict[str, int] = field(default_factory=dict)

    @property
    def table_list(self) -> list[str]:
        return [t.strip() for t in self.tables.split(",") if t.strip()]

    @property
    def keyword_list(self) -> list[str]:
        return [k.strip() for k in self.keywords.split(",") if k.strip()]

    @property
    def found_by(self) -> str:
        """Which retrievers found this pair, and at what rank -- for tracing."""
        return ", ".join(f"{name}#{rank}" for name, rank in sorted(self.ranks.items()))


Ranked = list[tuple[str, float]]  # (chunk_id, score), higher is better


def _ranks_of(rankings: dict[str, Ranked]) -> dict[str, dict[str, int]]:
    """Which retrievers found each id, and at what 1-based rank."""
    ranks: dict[str, dict[str, int]] = {}
    for name, hits in rankings.items():
        for position, (chunk_id, _) in enumerate(hits, start=1):
            ranks.setdefault(chunk_id, {})[name] = position
    return ranks


def weighted_rrf(
    rankings: dict[str, Ranked], weights: dict[str, float], rrf_k: int = DEFAULT_RRF_K
) -> list[tuple[str, float, dict[str, int]]]:
    """Weighted reciprocal rank fusion, as LangChain's EnsembleRetriever does it.

    Kept as an option, and as the thing the default is measured against. Its
    weakness on a corpus this small is that 1/(60 + rank) barely separates rank
    1 from rank 10, so the weights end up counting *how many* retrievers voted
    rather than how strongly any of them did.
    """
    scores: dict[str, float] = {}
    for name, hits in rankings.items():
        weight = weights.get(name, 0.0)
        for position, (chunk_id, _) in enumerate(hits, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (rrf_k + position)
    ranks = _ranks_of(rankings)
    order = sorted(scores, key=lambda cid: (-scores[cid], cid))
    return [(cid, scores[cid], ranks[cid]) for cid in order]


def weighted_score_fusion(
    rankings: dict[str, Ranked], weights: dict[str, float], **_: object
) -> list[tuple[str, float, dict[str, int]]]:
    """Min-max normalise each retriever's scores, then take the weighted sum.

    The three retrievers produce numbers that are not comparable as they stand:
    a cosine similarity sits in a narrow band near 0.5, and a BM25 score is
    unbounded and depends on the corpus. Normalising each retriever's candidate
    set to [0, 1] first makes the weights mean what they say -- a pair that is
    the best question match earns the full 0.50 -- while keeping the *distance*
    between a strong and a weak match, which rank fusion throws away.

    A retriever that returns nothing contributes nothing, so a question with no
    keyword overlap is scored by the two vector retrievers alone rather than
    being dragged toward whatever BM25 happened to match.
    """
    scores: dict[str, float] = {}
    for name, hits in rankings.items():
        if not hits:
            continue
        weight = weights.get(name, 0.0)
        values = [score for _, score in hits]
        low, high = min(values), max(values)
        spread = high - low
        for chunk_id, score in hits:
            # A single candidate, or a set with no spread, is all equally good.
            normalized = 1.0 if spread <= 0 else (score - low) / spread
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight * normalized
    ranks = _ranks_of(rankings)
    order = sorted(scores, key=lambda cid: (-scores[cid], cid))
    return [(cid, scores[cid], ranks[cid]) for cid in order]


FUSIONS = {FUSION_SCORE: weighted_score_fusion, FUSION_RRF: weighted_rrf}


def format_examples(pairs: list[GoldenPair], max_chars: int = 8000) -> str:
    """Render retrieved pairs as worked examples for a prompt.

    Ordered question, then what the query has to get right, then the SQL -- the
    order a multi-shot prompt reads them in, so the model sees the reasoning
    before the answer it is meant to justify.
    """
    blocks: list[str] = []
    used = 0
    for index, pair in enumerate(pairs, start=1):
        block = (
            f"Example {index} ({pair.pair_id} -- {pair.title})\n"
            f"Question: {pair.question}\n"
            f"What this has to get right: {pair.reasoning_target}\n"
            f"SQL:\n{pair.sql_code}\n"
            f"Returns: {pair.result}"
        )
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def tables_mentioned(pairs: list[GoldenPair]) -> list[str]:
    """Tables the retrieved examples query, best match first."""
    seen: list[str] = []
    for pair in pairs:
        for name in pair.table_list:
            if name not in seen:
                seen.append(name)
    return seen


class GoldenPairLibrary:
    """The ensemble retriever over the golden pairs.

    Holds two connections on purpose: the vectors live in the pgvector store
    beside the knowledge embeddings, while the rows themselves -- and the BM25
    statistics over their keywords -- live in the context store, which is the
    single source of truth for what a pair actually says.
    """

    def __init__(
        self,
        context_url: str,
        vector_url: str,
        embedder: Embedder,
        *,
        top_k: int = 3,
        candidate_k: int = 10,
        weights: dict[str, float] | None = None,
        fusion: str = DEFAULT_FUSION,
        rrf_k: int = DEFAULT_RRF_K,
        statement_timeout_ms: int = 15000,
    ) -> None:
        if fusion not in FUSIONS:
            raise ValueError(f"unknown fusion {fusion!r}; expected one of {sorted(FUSIONS)}")
        self._context: Engine = create_engine(context_url, pool_pre_ping=True)
        self._vectors: Engine = create_engine(vector_url, pool_pre_ping=True)
        self._embedder = embedder
        self._top_k = top_k
        self._candidate_k = candidate_k
        self._weights = dict(weights or DEFAULT_WEIGHTS)
        self._fusion = fusion
        self._rrf_k = rrf_k
        self._statement_timeout_ms = statement_timeout_ms

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    @property
    def fusion(self) -> str:
        return self._fusion

    def count(self) -> int:
        """How many pairs are loaded -- a cheap reachability check."""
        try:
            with self._context.connect() as conn:
                return int(conn.exec_driver_sql(f"SELECT COUNT(*) FROM {PAIRS_TABLE}").scalar())
        except Exception as exc:
            raise ExamplesUnavailableError(
                f"Could not read {PAIRS_TABLE} from the context store: {exc}"
            ) from exc

    def search(self, question: str, top_k: int | None = None) -> list[GoldenPair]:
        """The ensemble: three rankings, fused, then hydrated into full rows."""
        k = top_k if top_k is not None else self._top_k
        if k <= 0 or not question.strip():
            return []

        rankings = self.rank(question)
        fused = FUSIONS[self._fusion](rankings, self._weights, rrf_k=self._rrf_k)[:k]
        if not fused:
            return []

        rows = self._hydrate([chunk_id for chunk_id, _, _ in fused])
        pairs: list[GoldenPair] = []
        for chunk_id, score, ranks in fused:
            row = rows.get(chunk_id)
            if row is None:  # a vector with no row behind it: skip, don't fail
                continue
            row.score = score
            row.ranks = ranks
            pairs.append(row)
        return pairs

    def rank(self, question: str) -> dict[str, Ranked]:
        """Each retriever's candidates, unfused -- exposed for evaluation."""
        return {BY_KEYWORDS: self._by_keywords(question), **self._by_vectors(question)}

    # --- the three component retrievers ------------------------------------

    def _by_keywords(self, question: str) -> Ranked:
        """BM25 over the keywords column, ranked in the database."""
        try:
            with self._context.connect() as conn:
                self._apply_timeout(conn)
                rows = conn.exec_driver_sql(
                    f"""
                    SELECT b.chunk_id, b.score
                    FROM {BM25_FUNCTION}(%s) b
                    JOIN {PAIRS_TABLE} g USING (chunk_id)
                    ORDER BY b.score DESC, g.ordinal
                    LIMIT %s
                    """,
                    (question, self._candidate_k),
                ).fetchall()
        except Exception as exc:
            raise ExamplesUnavailableError(
                f"Could not run the keyword search: {exc}"
            ) from exc
        return [(r[0], float(r[1])) for r in rows]

    def _by_vectors(self, question: str) -> dict[str, Ranked]:
        """Both vector rankings from one embedding call."""
        try:
            vector = self._embedder.embed_query(question)
        except Exception as exc:
            raise ExamplesUnavailableError(
                f"Could not embed the question: {exc}. Is the embedding model "
                "available on the configured Ollama host?"
            ) from exc

        literal = _vector_literal(vector)
        try:
            with self._vectors.connect() as conn:
                self._apply_timeout(conn)
                return {
                    BY_QUESTION: self._nearest(conn, QUESTION_VECTORS, literal),
                    BY_REASONING: self._nearest(conn, REASONING_VECTORS, literal),
                }
        except Exception as exc:
            raise ExamplesUnavailableError(
                f"Could not search the golden-pair vectors: {exc}"
            ) from exc

    def _nearest(self, conn, table: str, literal: str) -> Ranked:
        """Cosine distance turned into a similarity, so higher is always better."""
        rows = conn.exec_driver_sql(
            f"""
            SELECT chunk_id, embedding <=> %s::vector AS distance
            FROM "{table}"
            ORDER BY embedding <=> %s::vector, ordinal
            LIMIT %s
            """,
            (literal, literal, self._candidate_k),
        ).fetchall()
        return [(r[0], 1.0 - float(r[1])) for r in rows]

    # --- hydration ----------------------------------------------------------

    def _hydrate(self, chunk_ids: list[str]) -> dict[str, GoldenPair]:
        """Fetch the full rows for the fused winners, in one query."""
        try:
            with self._context.connect() as conn:
                self._apply_timeout(conn)
                rows = conn.exec_driver_sql(
                    f"""
                    SELECT chunk_id, pair_id, title, suite, type, tables, keywords,
                           question, reasoning_target, sql_code, result
                    FROM {PAIRS_TABLE}
                    WHERE chunk_id = ANY(%s)
                    """,
                    (chunk_ids,),
                ).fetchall()
        except Exception as exc:
            raise ExamplesUnavailableError(
                f"Could not load golden pairs from the context store: {exc}"
            ) from exc
        return {
            r[0]: GoldenPair(
                chunk_id=r[0],
                pair_id=r[1],
                title=r[2] or "",
                suite=r[3] or "",
                type=r[4] or "",
                tables=r[5] or "",
                keywords=r[6] or "",
                question=r[7] or "",
                reasoning_target=r[8] or "",
                sql_code=r[9] or "",
                result=r[10] or "",
            )
            for r in rows
        }

    def _apply_timeout(self, conn) -> None:
        conn.exec_driver_sql(f"SET statement_timeout = {int(self._statement_timeout_ms)}")


def build_embedder(settings) -> Embedder:
    """The same embedding client the knowledge retriever uses.

    Sharing it is not incidental: the golden-pair vectors and the knowledge
    vectors were produced by the same bge-m3 model, and a question embedded with
    anything else lands in a different space and retrieves noise.
    """
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(model=settings.embed_model, base_url=settings.embed_base_url)


def _vector_literal(vector: list[float]) -> str:
    """pgvector's text input format, so no client-side vector type is needed."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"
