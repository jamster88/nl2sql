"""The Snippet Retriever: scoring, choosing, rendering, and degrading.

The store is faked at the level of the SQL it is sent -- the keyword match,
the meaning search, the hydration -- so what is tested here is the part that
decides: which snippets qualify, how the two signals are put on one scale,
and what the generator is shown. What needs a real store and a real
embedder is in test_snippets_live.py.
"""

from __future__ import annotations

import math

import pytest
from nl2sql_agent.snippets import (
    CANDIDATE_K,
    KEYWORD_SCALE,
    MATCH_FUNCTION,
    SIMILARITY_CEILING,
    SIMILARITY_FLOOR,
    Found,
    Snippet,
    SnippetLibrary,
    SnippetsUnavailableError,
    format_snippets,
    render,
    usable,
)


def snippet(**overrides) -> Snippet:
    fields = dict(
        snippet_id="S19",
        chunk_id="snippet:s19",
        kind="measure",
        name="Net sales",
        tables="fact_pos_retail_sales",
        means="Revenue actually collected.",
        applies_to="fact_pos_retail_sales f",
        sql="SUM(f.net_sales_amt)",
    )
    fields.update(overrides)
    return Snippet(**fields)


# ---------------------------------------------------------------------------
# What the generator reads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,applies_to,sql,body",
    [
        (
            "join",
            "fact_pos_retail_sales f",
            "JOIN dim_date d ON d.date_key = f.sales_date_key",
            ["FROM fact_pos_retail_sales f", "JOIN dim_date d ON d.date_key = f.sales_date_key"],
        ),
        ("filter", "dim_date d", "d.fiscal_year = 2025", ["FROM dim_date d", "WHERE d.fiscal_year = 2025"]),
        (
            "measure",
            "fact_pos_retail_sales f",
            "SUM(f.net_sales_amt)",
            ["SELECT SUM(f.net_sales_amt)", "FROM fact_pos_retail_sales f"],
        ),
        (
            "dimension",
            "dim_product p",
            "p.department_name",
            ["SELECT p.department_name", "FROM dim_product p", "GROUP BY p.department_name"],
        ),
    ],
)
def test_each_kind_is_written_as_the_clause_it_is_inside_the_one_it_belongs_to(kind, applies_to, sql, body):
    rendered = render(snippet(kind=kind, applies_to=applies_to, sql=sql, snippet_id="S07", name="X", means="Y."))
    lines = rendered.splitlines()
    assert lines[0] == f"[S07 {kind}] X -- Y."
    assert lines[1:] == [f"  {line}" for line in body]


def test_the_note_follows_the_sql_because_it_explains_the_detail_a_model_drops():
    """The live run that found it: S26 shown without its note came back with
    the division and without the cast, and every rate was 0."""
    ctr = snippet(
        snippet_id="S26",
        name="Click-through rate",
        means="Clicks per impression.",
        applies_to="fact_ad_performance a",
        sql="SUM(a.clicks_or_coupon_clips_count)::numeric / NULLIF(SUM(a.impressions_count), 0)",
        note="Both counts are integers, so without the cast to numeric the division truncates to zero.",
    )
    assert render(ctr).splitlines()[1:] == [
        "  SELECT SUM(a.clicks_or_coupon_clips_count)::numeric / NULLIF(SUM(a.impressions_count), 0)",
        "  FROM fact_ad_performance a",
        "  Note: Both counts are integers, so without the cast to numeric the division truncates to zero.",
    ]
    # No note, no line: the snippet reads as it did.
    assert "Note:" not in render(snippet(note="  "))


def test_the_prompt_block_keeps_the_best_snippets_that_fit_the_budget():
    first, second = snippet(snippet_id="S01"), snippet(snippet_id="S02")
    one = render(first)
    assert format_snippets([first, second], max_chars=len(one)) == one
    assert format_snippets([first, second]) == f"{one}\n{render(second)}"
    assert format_snippets([]) == ""


def test_a_snippet_is_usable_only_when_every_one_of_its_tables_is_in_scope():
    join = snippet(snippet_id="S01", tables="fact_pos_retail_sales, dim_date")
    measure = snippet(snippet_id="S19", tables="fact_pos_retail_sales")
    unlisted = snippet(snippet_id="S99", tables=" , ")
    scope = ["fact_pos_retail_sales", "dim_store"]
    assert usable([join, measure, unlisted], scope) == [measure]
    assert usable([join, measure], [*scope, "dim_date"]) == [join, measure]


def test_found_by_says_which_signals_found_it():
    assert snippet(matched="net sales", similarity=0.5123).found_by == "keywords: net sales; meaning 0.512"
    assert snippet(similarity=0.7).found_by == "meaning 0.700"
    assert snippet(matched="sales").found_by == "keywords: sales"
    assert snippet().found_by == ""


# ---------------------------------------------------------------------------
# Choosing, against a faked store
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _Conn:
    """Answers the four kinds of statement the library sends, by their text."""

    def __init__(self, store: "FakeStore") -> None:
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def exec_driver_sql(self, statement: str, params=None):
        self.store.statements.append((statement, params))
        if self.store.fail:
            raise RuntimeError(self.store.fail)
        if statement.startswith("SET statement_timeout"):
            return _Rows()
        if "count(*)" in statement:
            return _Rows(scalar=len(self.store.rows))
        if MATCH_FUNCTION in statement:
            return _Rows(sorted(self.store.keywords, key=lambda r: -r[1]))
        if "to_regclass" in statement:
            return _Rows(scalar=self.store.embedded)
        if "embedding <=>" in statement:
            return _Rows(list(self.store.similarities.items()))
        ids = params[0]
        return _Rows([row for row in self.store.rows if row[0] in ids])


class FakeStore:
    def __init__(self, *, keywords=(), similarities=None, rows=None, embedded=True, fail=None):
        self.keywords = list(keywords)
        self.similarities = dict(similarities or {})
        self.rows = rows if rows is not None else [_row(cid) for cid in self.ids()]
        self.embedded = embedded
        self.fail = fail
        self.statements: list = []

    def ids(self):
        return sorted({k[0] for k in self.keywords} | set(self.similarities))

    def connect(self):
        return _Conn(self)


def _row(chunk_id: str):
    n = chunk_id.split(":")[-1].upper()
    return (chunk_id, n, "filter", f"name {n}", "dim_date", f"means {n}", "dim_date d", "d.is_holiday", f"note {n}")


class FakeEmbedder:
    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError(self.fail)
        return [0.25, -0.5]


def library(store: FakeStore, embedder=None, **kwargs) -> SnippetLibrary:
    lib = SnippetLibrary("postgresql+psycopg://u:p@127.0.0.1:1/db", embedder or FakeEmbedder(), **kwargs)
    lib._engine = store
    return lib


def ids(found) -> list[str]:
    return [s.chunk_id for s in found]


def meaning(similarity: float) -> float:
    return min(1.0, max(0.0, (similarity - SIMILARITY_FLOOR) / (SIMILARITY_CEILING - SIMILARITY_FLOOR)))


def words(weight: float) -> float:
    return 1.0 - math.exp(-weight / KEYWORD_SCALE)


def test_a_keyword_phrase_match_qualifies_a_snippet_and_the_scores_are_averaged():
    store = FakeStore(
        keywords=[("snippet:s19", 6.0, "net sales"), ("snippet:s09", 3.0, "fiscal year")],
        similarities={"snippet:s19": 0.50, "snippet:s09": 0.42},
    )
    found = library(store).search("net sales in fiscal year 2025")
    assert ids(found) == ["snippet:s19", "snippet:s09"]
    best, other = found
    assert best.score == pytest.approx(0.5 * meaning(0.50) + 0.5 * words(6.0))
    assert other.score == pytest.approx(0.5 * meaning(0.42) + 0.5 * words(3.0))
    assert (best.matched, best.similarity, best.keyword_score) == ("net sales", 0.50, 6.0)
    assert best.name == "name S19" and best.applies_to == "dim_date d" and best.note == "note S19"


def test_meaning_alone_qualifies_a_snippet_only_above_the_similarity_bar():
    store = FakeStore(similarities={"snippet:s22": 0.63, "snippet:s21": 0.61})
    found = library(store, min_similarity=0.62).search("spend per trip")
    assert ids(found) == ["snippet:s22"]
    assert found[0].matched == "" and found[0].score == pytest.approx(0.5 * meaning(0.63))


def test_a_snippet_below_the_combined_score_is_dropped_even_when_a_phrase_matched():
    store = FakeStore(
        keywords=[("snippet:s01", 10.0, "sales in fiscal year"), ("snippet:s16", 1.0, "promotion")],
        similarities={"snippet:s01": 0.40, "snippet:s16": 0.20},
    )
    assert ids(library(store, min_score=0.35).search("q")) == ["snippet:s01"]


def test_at_most_top_k_are_kept_best_first():
    store = FakeStore(keywords=[(f"snippet:s{n:02d}", float(n), "x") for n in range(1, 8)])
    found = library(store, top_k=3).search("q")
    assert ids(found) == ["snippet:s07", "snippet:s06", "snippet:s05"]
    assert ids(library(store).search("q", top_k=2)) == ["snippet:s07", "snippet:s06"]


def test_every_keyword_hit_has_its_meaning_measured_not_only_the_nearest():
    store = FakeStore(keywords=[("snippet:s10", 4.0, "last year")])
    library(store).search("last year")
    statement, params = next(s for s in store.statements if "embedding <=>" in s[0])
    assert "ANY(%s)" in statement
    assert params[2] == CANDIDATE_K
    assert params[4] == ["snippet:s10"]


def test_without_an_embedder_or_vectors_the_keyword_half_still_answers():
    store = FakeStore(keywords=[("snippet:s12", 5.0, "weekend")], similarities={"snippet:s99": 0.9})
    lib = SnippetLibrary("postgresql+psycopg://u:p@127.0.0.1:1/db", None)
    lib._engine = store
    result = lib.find("weekend sales")
    assert ids(result.snippets) == ["snippet:s12"] and result.warning is None
    assert not any("embedding <=>" in s for s, _ in store.statements)

    unembedded = FakeStore(keywords=[("snippet:s12", 5.0, "weekend")], embedded=False)
    result = library(unembedded).find("weekend sales")
    assert ids(result.snippets) == ["snippet:s12"]
    assert result.warning == "meaning skipped: the snippets have not been embedded"


def test_an_embedding_host_that_is_down_costs_the_meaning_half_and_says_so():
    store = FakeStore(keywords=[("snippet:s12", 5.0, "weekend")], similarities={"snippet:s99": 0.9})
    result = library(store, FakeEmbedder(fail="connection refused")).find("weekend sales")
    assert ids(result.snippets) == ["snippet:s12"]
    assert result.warning == "meaning skipped: could not embed the question: connection refused"


def test_a_vector_with_no_row_behind_it_is_skipped_not_fatal():
    store = FakeStore(keywords=[("snippet:s01", 2.0, "x"), ("snippet:s02", 1.0, "y")], rows=[_row("snippet:s02")])
    assert ids(library(store, min_score=0.0).search("q")) == ["snippet:s02"]


def test_nothing_is_asked_for_an_empty_question_or_a_zero_k():
    store = FakeStore(keywords=[("snippet:s01", 2.0, "x")])
    assert library(store).find("   ") == Found()
    assert library(store, top_k=0).search("q") == []
    assert store.statements == []


def test_nothing_chosen_is_nothing_hydrated():
    store = FakeStore()
    assert library(store).search("q") == []
    assert not any("WHERE chunk_id = ANY" in s and "embedding" not in s for s, _ in store.statements)


def test_an_unreachable_store_is_one_error_the_pipeline_can_carry_on_from():
    store = FakeStore(fail="could not connect to server")
    with pytest.raises(SnippetsUnavailableError, match="Could not search the snippet store: could not connect"):
        library(store).search("q")
    with pytest.raises(SnippetsUnavailableError, match="Could not read sql_snippets"):
        library(store).count()


def test_count_is_how_many_snippets_are_loaded():
    assert library(FakeStore(keywords=[("snippet:s01", 1.0, "x"), ("snippet:s02", 1.0, "y")])).count() == 2


def test_meaning_is_read_on_a_fixed_band_of_similarity():
    """The nearest of several unrelated snippets is not made a perfect match
    by being the nearest."""
    assert (SIMILARITY_FLOOR, SIMILARITY_CEILING) == (0.35, 0.65)
    store = FakeStore(similarities={"snippet:s01": 0.30, "snippet:s02": 0.99})
    found = library(store, min_similarity=0.0, min_score=0.0).search("q")
    assert {s.chunk_id: s.score for s in found} == {"snippet:s02": 0.5, "snippet:s01": 0.0}


def test_keywords_are_read_by_their_own_weight_so_a_strong_match_crowds_out_no_other():
    """The weights and similarities the starter set gives "What was our
    click-through rate by channel on weekends in fiscal year 2025?" with
    bge-m3. Scored against the question's best phrase, "weekends" and "fiscal
    year" came to a third and a half of "click-through rate" and fell below
    the bar -- and a question that combines a measure with a filter is what
    snippets are for."""
    store = FakeStore(
        keywords=[
            ("snippet:s26", 10.95, "click-through rate"),
            ("snippet:s12", 3.37, "weekends"),
            ("snippet:s09", 5.29, "fiscal year"),
        ],
        similarities={"snippet:s26": 0.482, "snippet:s12": 0.421, "snippet:s09": 0.361},
    )
    found = library(store).search("q")
    assert ids(found) == ["snippet:s26", "snippet:s12", "snippet:s09"]
    assert found[1].score == pytest.approx(0.5 * meaning(0.421) + 0.5 * words(3.37))
    # Saturating, never past a perfect match, and nothing for no match.
    assert words(0.0) == 0.0 and 0.99 < words(15.0) < 1.0
