"""The Literal Matcher: what it extracts from a question, what it resolves
against the catalog, and what it refuses to resolve.

Section 4.3 of the v4 architecture exists to stop one specific failure:
`WHERE department_name = 'Dairy and Eggs'` returns zero rows and no error, so
a wrong answer arrives looking exactly like a right one. These tests therefore
prove both halves of that -- that a phrase typed the way a person types it
("dairy and eggs", "scan-back") reaches the value spelled the way the database
spells it (`Dairy & Eggs`, `SCAN_BACK`), and that a coincidental near-miss
("sales" against `Salem`, which scores 0.80) never reaches the generator at
all, because a confidently wrong literal is worse than none.

The bulk runs offline against a hand-built eight-value catalog, including the
`pg_trgm`/`difflib` choice, which is exercised with a scripted engine rather
than an installed extension. The `docker`-marked tests build the real catalog
from the running retail database and check it against the counts in the
architecture document: 42 of the 43 text columns, `basket_id` excluded.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy
from nl2sql_agent.database import Database
from nl2sql_agent.literals import (
    LITERAL_MAP_HEADER,
    CatalogEntry,
    LiteralMatcher,
    build_catalog,
    extract_candidates,
    normalize,
    render_literal_map,
    trigram_available,
)
from nl2sql_agent.state import LiteralMatch
from tests import live_stores

POSTGRES_URL = live_stores.url("retail", variable="POSTGRES_URL")

# A miniature of the real catalog. `Dairy & Eggs` appears in two columns (the
# tie the generator must be shown), `Salem` is the coincidental near-miss for
# "sales", and the two milk sizes are one column with two plausible values.
CATALOG = [
    CatalogEntry("dim_product", "department_name", "Dairy & Eggs"),
    CatalogEntry("dim_product", "category_name", "Dairy & Eggs"),
    CatalogEntry("dim_product", "department_name", "Produce"),
    CatalogEntry("dim_product", "product_name", "Whole Milk 1 Gal"),
    CatalogEntry("dim_product", "product_name", "Whole Milk 2 Gal"),
    CatalogEntry("dim_allowance_type", "allowance_type_code", "SCAN_BACK"),
    CatalogEntry("dim_allowance_type", "allowance_type_name", "Scan-Back Allowance"),
    CatalogEntry("dim_ad_channel", "channel_type", "Print Flyer"),
    CatalogEntry("dim_geography", "region_name", "Pacific Northwest"),
    CatalogEntry("dim_store", "city", "Salem"),
]


@pytest.fixture
def matcher() -> LiteralMatcher:
    return LiteralMatcher(CATALOG)


def resolved(matches: list[LiteralMatch]) -> set[tuple[str, str, str]]:
    return {(m.table, m.column, m.value) for m in matches}


# --- a scripted engine, for the two code paths that need one ----------------


class FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class FakeConnection:
    def __init__(self, engine: "FakeEngine") -> None:
        self._engine = engine

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, statement, params=None) -> FakeResult:
        return self._engine.answer(str(statement), params or {})


class FakeEngine:
    """Answers the handful of statements this module issues, by shape.

    Dispatching on a fragment of the SQL rather than on call order keeps the
    tests readable and lets one engine serve both `build_catalog` and the
    trigram scorer.
    """

    def __init__(
        self,
        *,
        trigram_installed: bool = False,
        columns: list[tuple[str, str]] | None = None,
        counts: dict[tuple[str, str], int] | None = None,
        values: dict[tuple[str, str], list[str]] | None = None,
        fail_on: str | None = None,
    ) -> None:
        self.trigram_installed = trigram_installed
        self.columns = columns or []
        self.counts = counts or {}
        self.values = values or {}
        self.fail_on = fail_on
        self.statements: list[str] = []

    def connect(self) -> FakeConnection:
        return FakeConnection(self)

    def answer(self, sql: str, params: dict) -> FakeResult:
        self.statements.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise sqlalchemy.exc.OperationalError("boom", {}, Exception("boom"))
        if "pg_extension" in sql:
            return FakeResult([(1,)] if self.trigram_installed else [])
        if "pg_attribute" in sql:
            return FakeResult(
                [SimpleNamespace(table_name=t, column_name=c) for t, c in self.columns]
            )
        if "count(DISTINCT" in sql:
            return FakeResult(
                [
                    SimpleNamespace(table_name=t, column_name=c, distinct_count=n)
                    for (t, c), n in self.counts.items()
                ]
            )
        if "word_similarity" in sql:
            return FakeResult(self._similarities(sql, params))
        return FakeResult(
            [
                SimpleNamespace(table_name=t, column_name=c, value=v)
                for (t, c), vs in self.values.items()
                if f"'{t}'" in sql and f"'{c}'" in sql
                for v in vs
            ]
        )

    def _similarities(self, sql: str, params: dict) -> list:
        """A stand-in for word_similarity: 1.0 when the phrase is a run of
        whole words inside the value, which is the property the real function
        has and the property the ordinality round-trip is being tested on."""
        rows = []
        for i, phrase in enumerate(params["phrases"], start=1):
            for j, value in enumerate(params["values"], start=1):
                if phrase and (phrase == value or f" {phrase} " in f" {value} "):
                    rows.append(SimpleNamespace(phrase_ord=i, value_ord=j, score=1.0))
        return rows


class StubDatabase:
    """The one thing this module needs from `Database`: its public engine."""

    def __init__(self, engine: FakeEngine) -> None:
        self.engine = engine


# --- normalisation ----------------------------------------------------------


def test_normalize_folds_case_ampersands_and_separators_together():
    assert normalize("Dairy & Eggs") == normalize("dairy and eggs") == "dairy and eggs"
    assert normalize("SCAN_BACK") == normalize("scan-back") == normalize("Scan Back")
    assert normalize("  Print   Flyer! ") == "print flyer"


def test_normalize_of_punctuation_alone_is_empty():
    assert normalize("...") == ""
    assert normalize("  -- ") == ""


# --- candidate extraction ---------------------------------------------------


def test_extract_candidates_finds_a_quoted_string():
    assert "Back to School" in extract_candidates('Show the "Back to School" cycle')
    assert "Dairy & Eggs" in extract_candidates("Show the 'Dairy & Eggs' department")


def test_extract_candidates_finds_a_capitalised_multi_word_phrase():
    candidates = extract_candidates("Show Dairy & Eggs revenue by store")
    assert "Dairy & Eggs" in candidates


def test_extract_candidates_finds_all_caps_and_underscored_tokens():
    candidates = extract_candidates("How many SCAN_BACK allowances did CH01 run?")
    assert "SCAN_BACK" in candidates
    assert "CH01" in candidates


def test_extract_candidates_finds_a_lower_case_multi_word_phrase():
    """The form that matters most: nobody capitalises when they type."""
    assert "dairy and eggs" in extract_candidates("what were sales for dairy and eggs")


def test_extract_candidates_keeps_a_hyphenated_token_whole():
    assert "scan-back" in extract_candidates("how much did we book in scan-back allowances")


def test_extract_candidates_skips_stopwords_and_column_names():
    candidates = extract_candidates(
        "Show the total for each department_name",
        column_names=["department_name", "channel_type"],
    )
    folded = {c.casefold() for c in candidates}
    assert "the" not in folded
    assert "total" not in folded
    assert "department_name" not in folded


def test_extract_candidates_does_not_run_a_phrase_across_punctuation():
    candidates = extract_candidates("Sales by store, dairy and eggs only")
    assert not any("," in c for c in candidates)
    assert "dairy and eggs" in candidates


def test_extract_candidates_returns_nothing_for_an_empty_question():
    assert extract_candidates("") == []
    assert extract_candidates("   ") == []


# --- matching ---------------------------------------------------------------


def test_a_paraphrased_department_resolves_to_the_stored_spelling(matcher: LiteralMatcher):
    """The headline case: "and" for "&", lower case throughout."""
    matches = matcher.match("what were sales for dairy and eggs last quarter")
    assert ("dim_product", "department_name", "Dairy & Eggs") in resolved(matches)


def test_a_hyphenated_code_resolves_to_the_underscored_stored_value(matcher: LiteralMatcher):
    matches = matcher.match("how much did we book in scan-back allowances")
    assert ("dim_allowance_type", "allowance_type_code", "SCAN_BACK") in resolved(matches)


def test_a_misspelling_resolves_on_character_similarity_alone(matcher: LiteralMatcher):
    """"Produse" shares no whole word with `Produce`, so only the near-miss
    score can carry it -- the other half of what fuzzy matching is for."""
    matches = matcher.match("how did Produse do")
    assert ("dim_product", "department_name", "Produce") in resolved(matches)


def test_a_coincidental_near_miss_is_not_reported(matcher: LiteralMatcher):
    """"sales" scores 0.80 against `Salem` and means nothing by it."""
    matches = matcher.match("show total sales by store")
    assert all(m.value != "Salem" for m in matches)


def test_the_min_score_floor_excludes_a_weak_match():
    question = "how did Produse do"
    assert LiteralMatcher(CATALOG).match(question), "0.6 should admit the misspelling"
    strict = LiteralMatcher(CATALOG, min_score=0.9)
    assert strict.match(question) == []


def test_ties_across_different_columns_are_all_kept(matcher: LiteralMatcher):
    """`Dairy & Eggs` is a department and a category. The generator is told
    both rather than left to guess which column holds it."""
    matches = matcher.match("sales for dairy and eggs")
    columns = {(m.table, m.column) for m in matches if m.value == "Dairy & Eggs"}
    assert columns == {("dim_product", "department_name"), ("dim_product", "category_name")}


def test_only_the_best_value_from_a_column_is_reported(matcher: LiteralMatcher):
    matches = matcher.match("sales of Whole Milk 1 Gal")
    milk = [m for m in matches if m.column == "product_name"]
    assert len(milk) == 1
    assert milk[0].value == "Whole Milk 1 Gal"


def test_limit_per_phrase_caps_how_many_columns_one_phrase_reports(matcher: LiteralMatcher):
    matches = matcher.match("sales for dairy and eggs", limit_per_phrase=1)
    assert len([m for m in matches if m.value == "Dairy & Eggs"]) == 1


def test_several_phrasings_of_one_value_are_reported_once(matcher: LiteralMatcher):
    """"Show Dairy & Eggs" yields the candidates "Show Dairy & Eggs", "Dairy &
    Eggs" and "dairy and eggs"; all three resolve to the same row."""
    matches = matcher.match("Show Dairy & Eggs revenue")
    keys = [(m.table, m.column, m.value) for m in matches]
    assert len(keys) == len(set(keys))


def test_scores_are_reported_and_an_exact_paraphrase_scores_one(matcher: LiteralMatcher):
    matches = matcher.match("sales for dairy and eggs")
    assert any(m.score == 1.0 for m in matches)
    assert all(0.6 <= m.score <= 1.0 for m in matches)


def test_an_empty_question_matches_nothing(matcher: LiteralMatcher):
    assert matcher.match("") == []
    assert matcher.match("   ") == []


def test_an_empty_catalog_matches_nothing():
    assert LiteralMatcher([]).match("sales for dairy and eggs") == []


def test_a_question_with_no_literal_in_it_matches_nothing(matcher: LiteralMatcher):
    assert matcher.match("how many rows are there in total") == []


# --- what it is to name a value (7.0) -----------------------------------------

# Values the real catalog holds, each of which a common word of a question is
# one word of. Until 7.0 sharing that one word was a match.
NOISY = CATALOG + [
    CatalogEntry("dim_promo_calendar", "promo_season_type", "New Year New You"),
    CatalogEntry("dim_vendor", "payment_terms_desc", "1/15 Net 45"),
    CatalogEntry("dim_product", "sub_category_name", "Paper Products"),
    CatalogEntry("dim_store", "layout_type_desc", "Value/Discount"),
    CatalogEntry("dim_ad_channel", "channel_type", "Website Banner"),
    CatalogEntry("dim_product", "sub_category_name", "Eggs"),
    CatalogEntry("dim_promotion", "mechanic_type", "Percent Off"),
    CatalogEntry("dim_promo_calendar", "promo_season_type", "Summer Grilling"),
    CatalogEntry("dim_ad_placement", "ad_theme_name", "Summer Grilling: Grilling Season"),
]


@pytest.fixture(params=["difflib", "pg_trgm"])
def noisy(request) -> LiteralMatcher:
    """The same catalog behind each scorer: what a phrase has to be of a
    value is one rule, whichever of them found the pair."""
    if request.param == "difflib":
        return LiteralMatcher(NOISY)
    return LiteralMatcher(NOISY, database=StubDatabase(FakeEngine(trigram_installed=True)))


@pytest.mark.parametrize("question", [
    "Which 5 products had the highest net sales in fiscal year 2025? Give the SKU and the amount.",
    "What was the average basket value for each store banner in fiscal year 2025?",
    "What were our total net sales in fiscal year 2025?",
])
def test_a_word_of_a_longer_value_does_not_name_it(noisy: LiteralMatcher, question: str):
    """The benchmark's own questions. `word_similarity` scores one word
    against any value that contains it at 1.00, and sharing a word was
    enough: "year" was the promotion season `New Year New You`, "net" the
    payment terms `1/15 Net 45`, "products" the sub-category `Paper
    Products`, "value" the store layout `Value/Discount` -- each handed to
    the generator as a value the question had named, and with nothing else
    to go on, B10's answer was filtered to paper products."""
    assert noisy.match(question) == []


def test_part_of_a_value_is_not_the_value(noisy: LiteralMatcher):
    """Told "whole milk" is `Whole Milk 1 Gal`, the generator filters to one
    size of it."""
    assert all(m.column != "product_name" for m in noisy.match("how did whole milk sell"))
    assert ("dim_product", "product_name", "Whole Milk 2 Gal") in resolved(noisy.match("sales of whole milk 2 gal"))


def test_the_values_own_words_name_it_through_any_connector(noisy: LiteralMatcher):
    for question in ("sales for dairy and eggs", "sales for Dairy & Eggs"):
        assert ("dim_product", "department_name", "Dairy & Eggs") in resolved(noisy.match(question)), question
    # With the connector left out. (On `difflib` alone: the stand-in for
    # `word_similarity` above only knows a run of whole words.)
    assert ("dim_product", "department_name", "Dairy & Eggs") in resolved(LiteralMatcher(NOISY).match("sales for dairy eggs"))


def test_every_word_of_a_value_is_one_of_its_words_a_stopword_too(noisy: LiteralMatcher):
    """`Percent Off` is two words. Compared by the words that carry meaning,
    "off" did not count and "percent" was the whole of it."""
    assert noisy.match("what percent of sales came from stores") == []
    assert ("dim_promotion", "mechanic_type", "Percent Off") in resolved(noisy.match("how did Percent Off promotions do"))
    assert ("dim_promotion", "mechanic_type", "Percent Off") in resolved(noisy.match('how did "percent off" promotions do'))


def test_a_word_inside_a_phrase_that_is_a_value_is_not_a_name_of_its_own(noisy: LiteralMatcher):
    """"Dairy & Eggs" is the department. Its "Eggs" is not also the
    sub-category `Eggs` -- though asked about by itself, it is."""
    department = resolved(noisy.match("What was the gross margin for the Dairy & Eggs department?"))
    assert ("dim_product", "department_name", "Dairy & Eggs") in department
    assert ("dim_product", "sub_category_name", "Eggs") not in department
    assert ("dim_product", "sub_category_name", "Eggs") in resolved(noisy.match("how did Eggs sell last year"))


def test_inside_a_phrase_that_only_resembles_a_value_the_shorter_name_stands(noisy: LiteralMatcher):
    """"Summer Grilling season" has the words of an ad theme, `Summer
    Grilling: Grilling Season`, and is not it word for word. What the
    question names is the season, `Summer Grilling`, and a golden pair's
    verified query filters on exactly that."""
    found = resolved(noisy.match("Which promotions ran in the Summer Grilling season?"))
    assert ("dim_promo_calendar", "promo_season_type", "Summer Grilling") in found


def test_a_word_the_schema_is_named_with_is_not_a_misspelling_of_a_value(noisy: LiteralMatcher):
    """"product" is one letter from `Produce` -- as near as "produse", which
    is how a misspelling is told. But `dim_product` and `product_name` say
    what "product" means here."""
    assert all(m.value != "Produce" for m in noisy.match("which product sold the most"))
    assert ("dim_product", "department_name", "Produce") in resolved(LiteralMatcher(NOISY).match("how did Produse do"))
    assert ("dim_product", "department_name", "Produce") in resolved(noisy.match("how did produce do"))


@pytest.mark.parametrize("question", [
    "What was the gross margin for the Dairy & Eggs department in fiscal month 12?",
    "how much SCAN_BACK did we book",
    "Compare Print Flyer impressions in the Pacific Northwest for the year",
    "Which 5 products had the highest net sales?",
    "sales of Whole Milk 1 Gal by store banner",
])
def test_the_two_scorers_resolve_the_same_values(question: str):
    """The module has always said they would. With one shared word enough,
    they did not: `word_similarity` found every value containing a word and
    `difflib`'s ratio found none of them."""
    plain = LiteralMatcher(NOISY)
    trigram = LiteralMatcher(NOISY, database=StubDatabase(FakeEngine(trigram_installed=True)))
    assert trigram.scorer == "pg_trgm" and plain.scorer == "difflib"
    assert resolved(trigram.match(question)) == resolved(plain.match(question))


def test_a_value_named_twice_is_reported_once_by_its_better_phrase():
    """A misspelling and then the word itself: one row, and the phrase kept
    is the one that is the value."""
    for question in ("did Produse mean Produce", "did Produce mean Produse"):
        [match] = [m for m in LiteralMatcher(NOISY).match(question) if m.value == "Produce"]
        assert (match.phrase, match.score) == ("Produce", 1.0), question


def test_two_values_of_one_column_that_spell_alike_are_one_match():
    """`SCAN_BACK` and `Scan-Back` are the same words, and a phrase that
    names one names the other: a column still reports one of them."""
    catalog = [
        CatalogEntry("dim_allowance_type", "allowance_type_code", "SCAN_BACK"),
        CatalogEntry("dim_allowance_type", "allowance_type_code", "Scan-Back"),
    ]
    for matcher in (LiteralMatcher(catalog), LiteralMatcher(catalog, database=StubDatabase(FakeEngine(trigram_installed=True)))):
        assert len(matcher.match("how much scan-back did we book")) == 1, matcher.scorer


def test_naming_is_the_values_words_or_a_near_miss_of_the_whole():
    from nl2sql_agent.literals import _Indexed, _name_words, _names, _spelled, _within

    value = _Indexed(CatalogEntry("dim_product", "department_name", "Dairy & Eggs"), "dairy and eggs", _name_words("dairy and eggs"))
    assert _names("dairy and eggs", value) and _names("eggs and dairy", value) and _names("dairy eggs", value)
    assert not _names("dairy", value) and not _names("eggs", value) and not _names("dairy eggs milk", value)
    assert not _names("", value) and not _names("and", value)

    produce = _Indexed(CatalogEntry("dim_product", "department_name", "Produce"), "produce", _name_words("produce"))
    assert _names("produse", produce) and _names("product", produce)
    assert not _names("product", produce, frozenset({"product"}))
    assert _names("produce", produce, frozenset({"produce"})), "a word of the schema that is the value is the value"

    assert _name_words("percent off") == {"percent", "off"} and _name_words("the top of the range") == {"top", "range"}
    assert _spelled("summer grilling season") != _spelled("summer grilling grilling season")
    assert _name_words("summer grilling season") == _name_words("summer grilling grilling season")
    assert _within(["eggs"], [["dairy", "and", "eggs"]]) and _within(["and", "eggs"], [["dairy", "and", "eggs"]])
    assert not _within(["dairy", "and", "eggs"], [["dairy", "and", "eggs"]]), "a phrase is not inside itself"
    assert not _within(["eggs", "dairy"], [["dairy", "and", "eggs"]]) and not _within(["eggs"], [])


# --- choosing a scorer ------------------------------------------------------


def test_difflib_is_chosen_when_the_trigram_extension_is_not_installed():
    database = StubDatabase(FakeEngine(trigram_installed=False))
    assert trigram_available(database) is False
    assert LiteralMatcher(CATALOG, database=database).scorer == "difflib"


def test_trigram_is_chosen_when_the_extension_is_installed():
    engine = FakeEngine(trigram_installed=True)
    matcher = LiteralMatcher(CATALOG, database=StubDatabase(engine))
    assert matcher.scorer == "pg_trgm"

    matches = matcher.match("sales for dairy and eggs")
    assert any("word_similarity" in s for s in engine.statements)
    assert ("dim_product", "department_name", "Dairy & Eggs") in resolved(matches)


def test_an_unreachable_database_falls_back_to_difflib_rather_than_raising():
    database = StubDatabase(FakeEngine(fail_on="pg_extension"))
    assert trigram_available(database) is False
    assert LiteralMatcher(CATALOG, database=database).scorer == "difflib"


def test_a_failing_trigram_query_falls_back_to_difflib_mid_question():
    engine = FakeEngine(trigram_installed=True, fail_on="word_similarity")
    matcher = LiteralMatcher(CATALOG, database=StubDatabase(engine))
    assert matcher.scorer == "pg_trgm"

    matches = matcher.match("sales for dairy and eggs")
    assert matcher.scorer == "difflib"
    assert ("dim_product", "department_name", "Dairy & Eggs") in resolved(matches)


def test_asking_for_trigram_without_a_database_is_refused():
    with pytest.raises(ValueError):
        LiteralMatcher(CATALOG, use_trigram=True)


# --- building the catalog ---------------------------------------------------


def test_build_catalog_keeps_narrow_columns_and_drops_wide_ones():
    engine = FakeEngine(
        columns=[
            ("dim_product", "department_name"),
            ("fact_pos_retail_sales", "basket_id"),
        ],
        counts={
            ("dim_product", "department_name"): 17,
            ("fact_pos_retail_sales", "basket_id"): 258308,
        },
        values={("dim_product", "department_name"): ["Dairy & Eggs", "Produce"]},
    )
    catalog = build_catalog(StubDatabase(engine), max_distinct=500)

    assert catalog == [
        CatalogEntry("dim_product", "department_name", "Dairy & Eggs"),
        CatalogEntry("dim_product", "department_name", "Produce"),
    ]


def test_build_catalog_never_reads_the_values_of_a_column_it_discarded():
    """258,308 basket ids is the reason the count pass runs first."""
    engine = FakeEngine(
        columns=[("dim_product", "department_name"), ("fact_pos_retail_sales", "basket_id")],
        counts={
            ("dim_product", "department_name"): 17,
            ("fact_pos_retail_sales", "basket_id"): 258308,
        },
        values={("dim_product", "department_name"): ["Dairy & Eggs"]},
    )
    build_catalog(StubDatabase(engine))

    value_reads = [s for s in engine.statements if "SELECT DISTINCT" in s]
    assert len(value_reads) == 1
    assert "basket_id" not in value_reads[0]


def test_build_catalog_stays_at_three_round_trips():
    engine = FakeEngine(
        columns=[("dim_store", "city")],
        counts={("dim_store", "city"): 8},
        values={("dim_store", "city"): ["Salem"]},
    )
    build_catalog(StubDatabase(engine))
    assert len(engine.statements) == 3


def test_build_catalog_of_a_schema_with_no_text_columns_is_empty():
    assert build_catalog(StubDatabase(FakeEngine(columns=[]))) == []


# --- rendering --------------------------------------------------------------


def test_render_literal_map_produces_the_aligned_block_from_the_spec():
    block = render_literal_map(
        [
            LiteralMatch("Dairy & Eggs", "dim_product", "department_name", "Dairy & Eggs", 1.0),
            LiteralMatch(
                "SCAN_BACK", "dim_allowance_type", "allowance_type_code", "SCAN_BACK", 1.0
            ),
        ]
    )
    lines = block.splitlines()
    assert lines[0] == LITERAL_MAP_HEADER
    assert lines[1] == '  "Dairy & Eggs" -> dim_product.department_name = \'Dairy & Eggs\''
    assert lines[2].endswith("dim_allowance_type.allowance_type_code = 'SCAN_BACK'")
    assert len({line.index("->") for line in lines[1:]}) == 1, "arrows must line up"


def test_render_literal_map_is_empty_for_no_matches():
    assert render_literal_map([]) == ""


def test_render_literal_map_agrees_with_the_state_objects_own_rendering():
    match = LiteralMatch("Print Flyer", "dim_ad_channel", "channel_type", "Print Flyer", 1.0)
    assert render_literal_map([match]).splitlines()[1].strip() == match.render()


# --- against the running retail database ------------------------------------


@pytest.fixture(scope="module")
def live_db() -> Database:
    database = Database(POSTGRES_URL)
    try:
        database.table_names()
    except sqlalchemy.exc.SQLAlchemyError as exc:
        live_stores.unreachable("Postgres", POSTGRES_URL, exc)
    return database


@pytest.fixture(scope="module")
def live_catalog(live_db: Database) -> list[CatalogEntry]:
    return build_catalog(live_db)


@pytest.mark.docker
def test_the_real_catalog_holds_42_of_the_43_text_columns(live_catalog, live_db):
    """The count the architecture document asserts, checked against the
    catalog the database actually has rather than against itself."""
    engine = sqlalchemy.create_engine(POSTGRES_URL)
    with engine.connect() as conn:
        text_columns = conn.execute(
            sqlalchemy.text(
                """
                SELECT count(*) FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid
                JOIN pg_type t ON t.oid = a.atttypid
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                  AND a.attnum > 0 AND NOT a.attisdropped
                  AND t.typname IN ('text', 'varchar', 'bpchar', 'char')
                """
            )
        ).scalar()

    columns = {(e.table, e.column) for e in live_catalog}
    assert text_columns == 43
    assert len(columns) == 42
    assert ("fact_pos_retail_sales", "basket_id") not in columns


@pytest.mark.docker
def test_no_catalogued_column_exceeds_the_distinct_cutoff(live_catalog):
    per_column: dict[tuple[str, str], int] = {}
    for entry in live_catalog:
        key = (entry.table, entry.column)
        per_column[key] = per_column.get(key, 0) + 1
    assert max(per_column.values()) <= 500
    assert len(live_catalog) > 1000


@pytest.mark.docker
def test_a_paraphrased_department_resolves_to_the_real_department_column(live_catalog, live_db):
    matcher = LiteralMatcher(live_catalog, database=live_db)
    matches = matcher.match("what were sales for dairy and eggs last quarter")
    assert ("dim_product", "department_name", "Dairy & Eggs") in resolved(matches)


@pytest.mark.docker
def test_the_three_benchmark_literals_resolve_to_their_real_columns(live_catalog, live_db):
    """B07, B09 and B15 each turn on one of these being spelled exactly."""
    matcher = LiteralMatcher(live_catalog, database=live_db)
    assert ("dim_allowance_type", "allowance_type_code", "SCAN_BACK") in resolved(
        matcher.match("how much did we book in scan-back allowances")
    )
    assert ("dim_ad_channel", "channel_type", "Print Flyer") in resolved(
        matcher.match("compare print flyer and paid social spend")
    )


@pytest.mark.docker
def test_the_benchmarks_questions_resolve_to_the_values_they_name_and_no_others(live_catalog, live_db):
    """Against the real catalog, 1,277 values. Until 7.0 the fifteen
    questions resolved to 63 values over thirteen of them, and their
    reference queries use one. These five are what the questions spell."""
    from benchmarks.questions import QUESTIONS

    matcher = LiteralMatcher(live_catalog, database=live_db)
    found = {q.id: resolved(matcher.match(q.question)) for q in QUESTIONS}
    assert {qid: values for qid, values in found.items() if values} == {
        "B07": {("dim_product", "department_name", "Dairy & Eggs")},
        "B09": {("dim_allowance_type", "allowance_type_code", "SCAN_BACK")},
        "B15": {
            ("dim_ad_channel", "channel_type", "Print Flyer"),
            ("dim_ad_channel", "channel_type", "Paid Social"),
            ("dim_allowance_type", "allowance_type_code", "ADVERTISING"),
        },
    }


@pytest.mark.docker
def test_the_real_scorers_resolve_the_same_values_for_every_benchmark_wording(live_catalog, live_db):
    from benchmarks.paraphrases import PARAPHRASES
    from benchmarks.questions import QUESTIONS

    trigram = LiteralMatcher(live_catalog, database=live_db)
    plain = LiteralMatcher(live_catalog, use_trigram=False)
    for question in QUESTIONS:
        for text in (question.question, *PARAPHRASES.get(question.id, ())):
            assert resolved(trigram.match(text)) == resolved(plain.match(text)), text


@pytest.mark.docker
def test_the_scorer_reflects_whether_pg_trgm_is_actually_installed(live_catalog, live_db):
    """Detected, never assumed: the extension is available in the retail image
    and not installed in the database the agent connects to."""
    matcher = LiteralMatcher(live_catalog, database=live_db)
    expected = "pg_trgm" if trigram_available(live_db) else "difflib"
    assert matcher.scorer == expected
    assert matcher.match("sales for dairy and eggs")


# ---------------------------------------------------------------------------
# The edges of the catalog and the matcher
# ---------------------------------------------------------------------------


def test_a_catalog_entry_names_its_column_the_way_the_prompt_does():
    entry = CatalogEntry(table="dim_product", column="department_name", value="Dairy & Eggs")
    assert entry.qualified_column == "dim_product.department_name"


def test_a_schema_where_every_text_column_is_too_wide_yields_no_catalog():
    """Not an error: a database of free text simply has no literals worth
    offering, and the generator spells them from the question as v3 did.
    """

    class _WideOnly:
        class _Engine:
            def connect(self):
                return _WideOnly._Conn()

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, statement, *args):
                sql = str(statement)
                if "information_schema.columns" in sql or "pg_attribute" in sql:
                    return _Rows([_Row(table_name="t", column_name="c", data_type="text")])
                if "distinct" in sql.lower():
                    return _Rows([_Row(table_name="t", column_name="c", distinct_count=99999)])
                return _Rows([])

        engine = _Engine()

    class _Row:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Rows(list):
        def all(self):
            return list(self)

    assert build_catalog(_WideOnly(), max_distinct=500) == []


def test_asking_for_trigram_without_a_database_is_refused_rather_than_ignored():
    """`word_similarity` runs inside Postgres, so a matcher told to use it
    with nowhere to run it is a configuration error, not a fallback.
    """
    with pytest.raises(ValueError, match="needs a database"):
        LiteralMatcher([], use_trigram=True)


def test_trigram_can_be_switched_off_explicitly_even_with_a_database():
    matcher = LiteralMatcher([], use_trigram=False, database=object())
    assert matcher.scorer == "difflib"


def test_an_empty_candidate_matches_nothing():
    catalog = [CatalogEntry(table="dim_product", column="department_name", value="Produce")]
    assert LiteralMatcher(catalog).match("") == []


def test_a_question_full_of_phrases_stops_at_the_candidate_cap():
    """Candidate extraction runs against every catalogued value, so an
    unbounded question would turn one call into an unbounded amount of work.
    """
    from nl2sql_agent.literals import _MAX_CANDIDATES

    question = " ".join(f'"Phrase Number {i}"' for i in range(180))
    assert len(extract_candidates(question)) == _MAX_CANDIDATES


def test_a_match_below_the_floor_is_refused_before_the_anchor_is_considered():
    catalog = [CatalogEntry(table="t", column="c", value="Produce")]
    assert LiteralMatcher(catalog, min_score=0.99).match("produse") == []


def test_a_near_miss_against_an_empty_string_is_never_a_match():
    """`SequenceMatcher` scores two empty strings as a perfect match, which
    would make every blank candidate resolve to the first catalogued value.
    """
    from nl2sql_agent.literals import _near_miss

    assert _near_miss("", "produce") is False
    assert _near_miss("produce", "") is False
    assert _near_miss("produse", "produce") is True


# ---------------------------------------------------------------------------
# The two floors in the matcher
# ---------------------------------------------------------------------------


def test_a_score_under_the_floor_is_rejected_whatever_else_it_shares():
    """`_keep` checks the floor first for a reason: a candidate can share a
    content word with the indexed value and still be a different thing
    ("sales region" against "sales"), so the word test is a reason to keep
    something already close enough, never a way around the threshold.
    """
    from nl2sql_agent.literals import LiteralMatcher, _Indexed, normalize

    matcher = LiteralMatcher([], min_score=0.6)
    indexed = _Indexed(
        entry=CatalogEntry(table="dim_product", column="department", value="Produce"),
        norm=normalize("Produce"),
        words={"produce"},
    )
    assert matcher._keep("Produce", indexed, 0.95) is True
    assert matcher._keep("Produce", indexed, 0.30) is False


def test_an_empty_candidate_matches_nothing_rather_than_everything():
    """`SequenceMatcher` against an empty string scores 0 with everything,
    which is harmless -- but the loop is over every catalogued value, and a
    phrase that normalises to nothing is worth skipping before paying for it.
    """
    from nl2sql_agent.literals import LiteralMatcher

    matcher = LiteralMatcher(
        [CatalogEntry(table="dim_product", column="department", value="Produce")],
        min_score=0.6,
    )
    assert matcher._score_difflib("") == {}
    assert matcher._score_difflib("produse") != {}
