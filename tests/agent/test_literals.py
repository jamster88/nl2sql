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

import os
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

POSTGRES_URL = os.environ.get(
    "POSTGRES_URL", "postgresql+psycopg://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail"
)

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
    """The one thing this module needs from `Database`: its private engine."""

    def __init__(self, engine: FakeEngine) -> None:
        self._engine = engine


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
        pytest.skip(f"no reachable Postgres at {POSTGRES_URL}: {exc}")
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

        _engine = _Engine()

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
