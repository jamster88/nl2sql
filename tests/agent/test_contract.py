"""The answer contract (arch5 section 4.1), on a catalog shaped like the real one.

The catalog below is `data_gen/ddl.sql` as `Database.catalog()` returns it --
column names, and key constraints printed the way `pg_get_constraintdef`
prints them -- for the dimensions whose labels are the interesting cases,
plus one fact table, whose composite key must never be read as an identity.
The live test in `test_database_live.py` checks the same map against the
real database.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from nl2sql_agent.contract import (
    DEFAULT_RANK_MEASURE,
    ContractResources,
    Label,
    LabelMap,
    build_contract,
    asks_for_one_number,
    build_label_map,
    contract_tables,
    default_period_assumption,
    describe,
    is_ranked,
    load_resources,
    render_contract,
    requested_row_count,
    stated_measure,
)
from nl2sql_agent.state import AnswerContract, EntityRef


def table(name: str, columns: list[str], constraints: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        columns=[SimpleNamespace(name=c) for c in columns],
        constraints=constraints,
    )


CATALOG = [
    table("dim_date", ["date_key", "calendar_date", "day_of_week_name", "fiscal_year"],
          ["PRIMARY KEY (date_key)", "UNIQUE (calendar_date)"]),
    table("dim_product", ["product_key", "sku_id", "product_name", "brand_name",
                          "department_name", "category_name", "package_size_desc"],
          ["PRIMARY KEY (product_key)", "UNIQUE (sku_id)"]),
    table("dim_store", ["store_key", "store_id", "store_name", "banner_name", "layout_type_desc"],
          ["PRIMARY KEY (store_key)", "UNIQUE (store_id)"]),
    table("dim_promo_calendar", ["promo_calendar_key", "promo_cycle_id", "promo_cycle_name"],
          ["PRIMARY KEY (promo_calendar_key)", "UNIQUE (promo_cycle_id)"]),
    table("dim_ad_channel", ["ad_channel_key", "channel_id", "channel_type", "medium_platform"],
          ["PRIMARY KEY (ad_channel_key)", "UNIQUE (channel_id)"]),
    table("dim_ad_placement", ["ad_placement_key", "ad_id", "ad_channel_key", "ad_theme_name"],
          ["PRIMARY KEY (ad_placement_key)", "UNIQUE (ad_id)",
           "FOREIGN KEY (ad_channel_key) REFERENCES dim_ad_channel(ad_channel_key)"]),
    table("dim_geography", ["market_region_key", "region_id", "region_name"],
          ["PRIMARY KEY (market_region_key)", "UNIQUE (region_id)"]),
    table("dim_vendor", ["vendor_key", "vendor_id", "vendor_name", "payment_terms_desc"],
          ["PRIMARY KEY (vendor_key)", "UNIQUE (vendor_id)"]),
    table("fact_pos_retail_sales", ["sales_date_key", "product_key", "store_key", "basket_id",
                                    "net_sales_amt"],
          ["PRIMARY KEY (sales_date_key, product_key, store_key, basket_id)",
           "FOREIGN KEY (store_key) REFERENCES dim_store(store_key)"]),
]


@pytest.fixture
def labels() -> LabelMap:
    return build_label_map(CATALOG)


@pytest.fixture
def resources(labels: LabelMap) -> ContractResources:
    return ContractResources(
        label_map=labels,
        fiscal_year=2025,
        fiscal_year_start=date(2024, 4, 1),
        fiscal_year_end=date(2025, 3, 31),
    )


# ---------------------------------------------------------------------------
# The label map
# ---------------------------------------------------------------------------


def test_every_key_of_a_dimension_is_labelled_by_the_column_that_names_it(labels: LabelMap):
    assert labels.for_key("sku_id") == Label("dim_product", "sku_id", "product_name")
    assert labels.for_key("product_key").label == "product_name"
    assert labels.for_key("store_id").label == "store_name"
    assert labels.for_key("vendor_key").label == "vendor_name"


def test_a_label_is_found_through_the_natural_id_when_the_surrogate_names_nothing(labels: LabelMap):
    # promo_calendar_key has no promo_calendar_name; promo_cycle_id does.
    assert labels.for_key("promo_calendar_key").label == "promo_cycle_name"
    assert labels.for_key("region_id").label == "region_name"
    assert labels.for_key("market_region_key").label == "region_name"


def test_a_name_column_sharing_the_keys_leading_word_is_the_last_resort(labels: LabelMap):
    assert labels.for_key("ad_id").label == "ad_theme_name"


def test_a_dimension_with_no_name_column_has_no_label(labels: LabelMap):
    assert labels.for_key("channel_id") is None
    assert labels.for_key("ad_channel_key") is None


def test_the_date_dimension_is_not_labelled_by_its_day_of_the_week(labels: LabelMap):
    """date_key already reads as the date it is; the only *_name column in
    dim_date is an attribute of the day, not its name."""
    assert labels.for_key("date_key") is None
    assert labels.for_key("calendar_date") is None


def test_a_composite_key_is_a_grain_not_an_identity(labels: LabelMap):
    assert labels.for_key("basket_id") is None
    assert labels.for_key("sales_date_key") is None
    assert "fact_pos_retail_sales" not in labels.tables


def test_lookups_ignore_case_and_know_labels_from_keys(labels: LabelMap):
    assert labels.for_key("SKU_ID").label == "product_name"
    assert labels.for_key("") is None
    assert labels.is_label("Store_Name")
    assert not labels.is_label("store_id")
    assert len(labels) == 12
    assert labels.tables == [
        "dim_ad_placement", "dim_geography", "dim_product", "dim_promo_calendar",
        "dim_store", "dim_vendor",
    ]
    assert [l.key for l in labels.labels()][:2] == ["ad_id", "ad_placement_key"]


def test_a_key_name_two_tables_label_differently_is_dropped_rather_than_guessed():
    ambiguous = LabelMap([Label("a", "code", "a_name"), Label("b", "code", "b_name"),
                          Label("c", "c_id", "c_name"), Label("c", "c_id", "c_name")])
    assert ambiguous.for_key("code") is None
    assert ambiguous.for_key("c_id").label == "c_name"


def test_a_table_without_keys_or_names_contributes_nothing():
    assert len(build_label_map([table("t", ["a_name"], []), table("u", ["u_key"], ["PRIMARY KEY (u_key)"])])) == 0


def test_a_desc_column_labels_a_table_that_has_no_name_column():
    lm = build_label_map([table("dim_terms", ["terms_code", "terms_desc"], ["UNIQUE (terms_code)"])])
    assert lm.for_key("terms_code").label == "terms_desc"


def test_a_key_constraint_on_a_column_the_table_does_not_list_is_ignored():
    lm = build_label_map([table("dim_x", ["x_name"], ['UNIQUE ("x_id")'])])
    assert len(lm) == 0


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word,key,label",
    [
        ("sku", "sku_id", "product_name"),
        ("SKUs", "sku_id", "product_name"),
        ("products", "sku_id", "product_name"),   # the natural id, not product_key
        ("stores", "store_id", "store_name"),
        ("vendor", "vendor_id", "vendor_name"),
        ("market regions", "region_id", "region_name"),
    ],
)
def test_a_noun_resolves_to_the_key_and_label_a_reader_knows(labels, word, key, label):
    entity = labels.for_entity(word)
    assert (entity.key, entity.label) == (key, label)


def test_the_noun_printed_is_the_form_that_matched(labels: LabelMap):
    assert labels.for_entity("SKUs").word == "sku"
    assert labels.for_entity("market regions").word == "market region"
    assert labels.for_entity("product categories").word == "category"


def test_a_noun_with_a_name_and_no_key_resolves_to_its_name_column(labels: LabelMap):
    assert labels.for_entity("departments") == EntityRef(
        word="department", label="department_name", table="dim_product"
    )
    assert labels.for_entity("product categories").label == "category_name"
    assert labels.for_entity("banner").label == "banner_name"


def test_an_unknown_noun_is_kept_so_the_contract_can_still_say_it(labels: LabelMap):
    assert labels.for_entity("items") == EntityRef(word="item")
    assert labels.for_entity("  ") == EntityRef(word="  ")


@pytest.mark.parametrize(
    "word,singular",
    [("classes", "class"), ("status", "status"), ("analysis", "analysis"), ("bus", "bus")],
)
def test_plurals_are_undone_without_mangling_words_that_end_in_s(labels, word, singular):
    assert labels.for_entity(word).word == singular


# ---------------------------------------------------------------------------
# Reading the question
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,count",
    [
        ("top 10 SKUs", 10),
        ("What are the top ten stores?", 10),
        ("Which 5 products had the highest net sales?", 5),
        ("show the 3 best banners", 3),
        ("What were total net sales in fiscal year 2025?", None),
        ("gross margin in fiscal month 12", None),
        ("sales for the last 12 months", None),
        ("the top 0 stores", None),
        ("", None),
    ],
)
def test_the_row_count_the_question_asks_for(question, count):
    assert requested_row_count(question) == count


def test_a_ranking_is_read_from_the_questions_own_words():
    assert is_ranked("Which competitor prices lowest relative to us?")
    assert is_ranked("our biggest vendor")
    assert not is_ranked("How many stores are there?")
    assert not is_ranked("")


# ---------------------------------------------------------------------------
# Building and rendering the contract
# ---------------------------------------------------------------------------


def test_top_ten_skus_asks_for_names_the_measure_and_the_latest_complete_year(resources):
    contract = build_contract("top 10 SKUs", entities=["sku"], resources=resources)

    assert contract.entities == [EntityRef("sku", "sku_id", "product_name", "dim_product")]
    assert contract.measure == DEFAULT_RANK_MEASURE
    assert contract.ranked and contract.limit == 10
    assert contract.period == "FY2025" and contract.period_default
    assert (contract.fiscal_year_start, contract.fiscal_year_end) == ("2024-04-01", "2025-03-31")
    assert render_contract(contract) == (
        "each sku named by `product_name` beside any `sku_id` it shows; the net sales "
        "the rows are ranked by, as a column; for any total over time, FY2025 "
        "(2024-04-01 to 2025-03-31), the latest complete fiscal year, filtered on "
        "`fiscal_year` = 2025, since the question names no period; no more than 10 "
        "rows, as the question asks."
    )
    assert describe(contract) == "sku_id->product_name, ranked by net sales, FY2025 (default), 10 rows"


def test_a_named_period_is_kept_and_no_default_is_applied(resources):
    contract = build_contract(
        "net sales by department in fiscal year 2024",
        entities=["department", "department"],
        measure="net sales",
        period="fiscal year 2024",
        resources=resources,
    )
    assert contract.period == "fiscal year 2024" and not contract.period_default
    assert contract.fiscal_year is None
    assert len(contract.entities) == 1
    assert render_contract(contract) == (
        "each department named by `department_name`; the net sales itself, as a column."
    )
    assert describe(contract) == "department_name, net sales, fiscal year 2024"


def test_an_answer_that_does_not_depend_on_time_gets_no_default_period(resources):
    """A store count "for FY2025" invites a join the question never needed."""
    contract = build_contract(
        "How many stores does each banner operate?",
        entities=["banner"],
        measure="store count",
        period="none",
        resources=resources,
    )
    assert contract.period is None and not contract.period_default


def test_no_measure_means_no_period(resources):
    contract = build_contract("which vendor supplies Dairy & Eggs", entities=["vendor", ""],
                              resources=resources)
    assert contract.measure is None and contract.period is None
    assert render_contract(contract) == "each vendor named by `vendor_name` beside any `vendor_id` it shows."


def test_without_a_calendar_the_contract_simply_says_less():
    contract = build_contract("top 10 SKUs", entities=["sku", "widget"])
    assert contract.period is None and not contract.period_default
    assert contract.entities[1] == EntityRef(word="widget")
    assert describe(contract) == "sku, widget, ranked by net sales, 10 rows"
    assert "for any total" not in render_contract(contract)


def test_an_empty_contract_renders_nothing_and_describes_itself():
    assert render_contract(None) == ""
    assert render_contract(AnswerContract()) == ""
    assert describe(AnswerContract()) == "nothing to add"
    assert describe(AnswerContract(entities=[EntityRef(word="widget")])) == "widget"


def test_the_default_period_assumption_names_the_year_and_its_span(resources):
    contract = build_contract("top ten stores", entities=["store"], resources=resources)
    assert default_period_assumption(contract) == (
        "FY2025 (2024-04-01 to 2025-03-31), the latest complete fiscal year, "
        "since the question did not name a period"
    )
    bare = AnswerContract(period="FY2025", period_default=True, fiscal_year=2025)
    assert default_period_assumption(bare).startswith("FY2025, the latest")


# ---------------------------------------------------------------------------
# Reading the resources, best-effort
# ---------------------------------------------------------------------------


class _Database:
    def __init__(self, *, catalog=None, latest=None, catalog_error=None, calendar_error=None):
        self._catalog, self._latest = catalog or [], latest
        self._catalog_error, self._calendar_error = catalog_error, calendar_error

    def catalog(self):
        if self._catalog_error:
            raise self._catalog_error
        return self._catalog

    def latest_complete_fiscal_year(self):
        if self._calendar_error:
            raise self._calendar_error
        return self._latest


def test_resources_are_read_from_the_catalog_and_the_calendar():
    loaded = load_resources(_Database(catalog=CATALOG, latest=(2025, date(2024, 4, 1), date(2025, 3, 31))))
    assert loaded.errors == {}
    assert loaded.label_map.for_key("sku_id").label == "product_name"
    assert (loaded.fiscal_year, loaded.fiscal_year_start) == (2025, date(2024, 4, 1))


def test_each_half_of_the_resources_fails_on_its_own():
    loaded = load_resources(_Database(catalog_error=RuntimeError("no catalog"),
                                      latest=(2025, date(2024, 4, 1), date(2025, 3, 31))))
    assert loaded.errors == {"label_map": "no catalog"}
    assert len(loaded.label_map) == 0 and loaded.fiscal_year == 2025

    loaded = load_resources(_Database(catalog=CATALOG, calendar_error=RuntimeError("no dim_date")))
    assert loaded.errors == {"fiscal_calendar": "no dim_date"}
    assert loaded.fiscal_year is None and len(loaded.label_map) == 12


def test_a_database_with_no_complete_year_offers_no_default():
    loaded = load_resources(_Database(catalog=CATALOG, latest=None))
    assert loaded.errors == {} and loaded.fiscal_year is None


def test_the_contract_names_the_tables_its_columns_come_from(resources):
    contract = build_contract("top 10 SKUs by department", entities=["sku", "department", "widget"],
                              resources=resources)
    assert contract_tables(contract) == ["dim_product", "dim_date"]
    named = build_contract("sales by store in FY2024", entities=["store", "stores"], measure="net sales",
                           period="FY2024", resources=resources)
    assert contract_tables(named) == ["dim_store", "dim_date"]
    timeless = build_contract("How many stores does each banner operate?", entities=["banner"],
                              measure="store count", period="none", resources=resources)
    assert contract_tables(timeless) == ["dim_store"]  # no period, so no calendar
    assert contract_tables(None) == []


@pytest.mark.parametrize(
    "question,one",
    [
        ("How many stores are there?", True),
        ("how much did we sell in FY2025?", True),
        ("What were our total net sales in fiscal year 2025?", True),
        ("What is the number of private label products?", True),
        ("How many stores does each banner operate?", False),
        ("How much did we collect in allowances, broken down by type?", False),
        ("top 10 SKUs", False),
        ("Which vendor supplies Dairy & Eggs?", False),
        ("", False),
    ],
)
def test_a_question_whose_answer_is_one_number(question, one):
    assert asks_for_one_number(question) is one


def test_a_count_of_stores_names_no_store(resources):
    """B01 on the first arch5 benchmark run: told to name each store, the
    generator listed ten of them instead of answering 10."""
    contract = build_contract("How many stores are there?", entities=["store"], measure="count",
                              resources=resources)
    assert contract.entities == []
    assert "store_name" not in render_contract(contract)
    grouped = build_contract("How many stores does each banner operate?", entities=["banner"],
                             measure="store count", period="none", resources=resources)
    assert [e.label for e in grouped.entities] == ["banner_name"]


def test_the_contract_names_only_the_measure_it_supplies_itself(resources):
    """B13 on the second benchmark run: the Supervisor paraphrased "prices
    lowest relative to us on average" as a price *difference*, the contract
    repeated it, and the generator computed a difference instead of a ratio.
    The question defines its own measure; the contract names only the
    default it supplies."""
    b13 = build_contract(
        "Which competitor prices lowest relative to us on average, and by how much?",
        entities=["competitor"], measure="average price difference relative to our prices",
        resources=resources,
    )
    assert stated_measure(b13) is None
    rendered = render_contract(b13)
    assert "difference" not in rendered
    assert "the figure the rows are ranked by, as a column" in rendered

    top = build_contract("top 10 SKUs", entities=["sku"], resources=resources)
    assert stated_measure(top) == "net sales"

    asked = build_contract("net sales by store", entities=["store"], measure="gross margin",
                           period="none", resources=resources)
    assert "the figure the question asks for, as a column" in render_contract(asked)
