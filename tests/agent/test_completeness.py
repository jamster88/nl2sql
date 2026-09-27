"""The Completeness Reviewer (arch5 section 6.6), rule by rule and bound by bound.

Every SQL string below is the kind the generator writes, parsed for real by
`pglast`; the results are plain rows. The label map is the one
`test_contract.py` builds from a catalog shaped like the real database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from nl2sql_agent.completeness import (
    MAX_REFLECTION_COLUMNS,
    NO_ROWS,
    R1,
    R2,
    R3,
    R4,
    REFLECTION,
    Reflection,
    ReflectedColumn,
    assumptions_for,
    describe,
    filters_default_year,
    gap_message,
    hidden_order_by,
    measure_columns,
    question_implies_rows,
    read_query,
    reflect,
    review,
    rule_identity,
    rule_measure,
    rule_period,
    rule_rows,
    schema_column_pairs,
)
from nl2sql_agent.contract import ContractResources, build_contract, build_label_map
from nl2sql_agent.state import (
    COMPLETENESS,
    AnswerContract,
    CompletenessReport,
    MissingColumn,
    QueryResult,
)

from .test_contract import CATALOG

LABELS = build_label_map(CATALOG)
RESOURCES = ContractResources(
    label_map=LABELS,
    fiscal_year=2025,
    fiscal_year_start=date(2024, 4, 1),
    fiscal_year_end=date(2025, 3, 31),
)

SCHEMA = """=== dim_product ===
description: one row per product
columns:
  product_key (integer, NOT NULL)
  sku_id (character varying(50), NOT NULL)
  product_name (character varying(255), NOT NULL)
  brand_name (character varying(100), NOT NULL)
  department_name (character varying(100), NOT NULL)
keys:
  PRIMARY KEY (product_key)
sample rows (up to 1):
  product_key | sku_id
  1 | SKU-1

=== fact_pos_retail_sales ===
columns:
  sales_date_key (integer, NOT NULL)
  product_key (integer, NOT NULL)
  net_sales_amt (numeric(12,2), NOT NULL)
keys:
  PRIMARY KEY (sales_date_key, product_key)
"""

BARE_TOP_SKUS = (
    "SELECT p.sku_id FROM fact_pos_retail_sales s JOIN dim_product p USING (product_key) "
    "GROUP BY 1 ORDER BY SUM(s.net_sales_amt) DESC LIMIT 10"
)
FULL_TOP_SKUS = (
    "SELECT p.sku_id, p.product_name, ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
    "FROM fact_pos_retail_sales s JOIN dim_product p USING (product_key) "
    "JOIN dim_date d ON d.date_key = s.sales_date_key WHERE d.fiscal_year = 2025 "
    "GROUP BY 1, 2 ORDER BY SUM(s.net_sales_amt) DESC LIMIT 10"
)


def rows(columns: list[str], *data: list, truncated: bool = False) -> QueryResult:
    return QueryResult(columns=columns, rows=[list(r) for r in data], truncated=truncated)


def top_skus_contract() -> AnswerContract:
    return build_contract("top 10 SKUs", entities=["sku"], resources=RESOURCES)


TEN_SKUS = rows(["sku_id"], *[[f"SKU-{i}"] for i in range(10)])
TEN_FULL = rows(
    ["sku_id", "product_name", "net_sales"],
    *[[f"SKU-{i}", f"Item {i}", Decimal("100.50") - i] for i in range(10)],
)


class _Reflector:
    """A chat model that only knows how to reflect."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts) if isinstance(verdicts, list) else verdicts
        self.calls = []

    def with_structured_output(self, schema):
        assert schema is Reflection
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        verdict = self.verdicts.pop(0) if isinstance(self.verdicts, list) else self.verdicts
        if isinstance(verdict, Exception):
            raise verdict
        return verdict


def complete() -> Reflection:
    return Reflection(complete=True)


def wants(*columns: str, why: str = "a reader will ask") -> Reflection:
    return Reflection(complete=False, missing=[ReflectedColumn(column=c, why=why) for c in columns])


# ---------------------------------------------------------------------------
# Reading the query
# ---------------------------------------------------------------------------


def test_the_outermost_select_list_is_read_for_outputs_and_the_columns_they_read():
    query = read_query(
        "SELECT p.sku_id AS sku, p.product_name, ROUND(SUM(s.net_sales_amt), 2) AS total "
        "FROM fact_pos_retail_sales s JOIN dim_product p USING (product_key) GROUP BY 1, 2",
        LABELS,
    )
    assert query.outputs == {"sku", "product_name", "total"}
    assert query.reads == {"sku_id", "product_name", "net_sales_amt"}
    assert query.aliased_keys == ["sku_id"]
    assert query.relations == {"fact_pos_retail_sales", "dim_product"}
    assert query.functions == {"round", "sum"}
    assert not query.star


def test_a_query_that_does_not_parse_or_is_not_one_select_is_read_as_text_only():
    assert read_query("SELEC nothing").select is None
    assert read_query("SELECT 1; SELECT 2").select is None
    union = read_query("SELECT store_id FROM dim_store UNION SELECT sku_id FROM dim_product")
    assert union.select is None and union.relations == {"dim_store", "dim_product"}
    assert read_query("").select is None


def test_a_star_is_noted_so_the_select_list_checks_stand_down():
    query = read_query("SELECT * FROM dim_store ORDER BY store_name")
    assert query.star
    assert hidden_order_by(query) == []


def test_an_aliased_key_is_only_noticed_with_a_label_map():
    assert read_query("SELECT sku_id AS s FROM dim_product").aliased_keys == []


def test_existence_questions_are_answered_by_no_rows():
    assert not question_implies_rows("Are there any stores in Texas?")
    assert question_implies_rows("top 10 SKUs")
    assert question_implies_rows("")


# ---------------------------------------------------------------------------
# R1 identity
# ---------------------------------------------------------------------------


def test_r1_wants_the_name_beside_a_bare_id():
    gaps = rule_identity(TEN_SKUS, read_query(BARE_TOP_SKUS, LABELS), LABELS)
    assert [(g.rule, g.column) for g in gaps] == [(R1, "product_name")]
    assert "`dim_product.product_name`" in gaps[0].why
    assert gaps[0].table == "dim_product"


def test_r1_is_satisfied_by_the_label_under_any_name():
    sql = "SELECT p.sku_id, p.product_name AS item FROM dim_product p"
    result = rows(["sku_id", "item"], ["SKU-1", "Milk"])
    assert rule_identity(result, read_query(sql, LABELS), LABELS) == []


def test_r1_catches_an_id_selected_under_another_name():
    sql = "SELECT p.sku_id AS sku FROM dim_product p"
    gaps = rule_identity(rows(["sku"], ["SKU-1"]), read_query(sql, LABELS), LABELS)
    assert [g.column for g in gaps] == ["product_name"]


def test_r1_asks_once_for_a_label_two_keys_share():
    sql = "SELECT product_key, sku_id FROM dim_product"
    gaps = rule_identity(rows(["product_key", "sku_id"], [1, "SKU-1"]), read_query(sql, LABELS), LABELS)
    assert len(gaps) == 1


def test_r1_ignores_columns_that_are_not_keys():
    sql = "SELECT department_name, count(*) AS n FROM dim_product GROUP BY 1"
    assert rule_identity(rows(["department_name", "n"], ["Dairy", 3]), read_query(sql, LABELS), LABELS) == []


# ---------------------------------------------------------------------------
# R2 measure
# ---------------------------------------------------------------------------


def test_measure_columns_are_numbers_that_are_not_keys_codes_or_calendar_positions():
    result = rows(
        ["sku_id", "store_key", "fiscal_year", "fiscal_month_num", "rank", "flag", "empty", "sales", "units"],
        ["S", 1, 2025, 3, 1, True, None, Decimal("1.5"), 2],
        ["T", 2, 2025, 4, 2, False, None, 3.0, None],
    )
    assert measure_columns(result, LABELS) == ["sales", "units"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT sku_id, SUM(net_sales_amt) AS total FROM t GROUP BY 1 ORDER BY 2 DESC",
        "SELECT sku_id, SUM(net_sales_amt) AS total FROM t GROUP BY 1 ORDER BY total DESC",
        "SELECT sku_id, ROUND(SUM(s.net_sales_amt), 2) AS total FROM t s GROUP BY 1 ORDER BY SUM(net_sales_amt) DESC",
        "SELECT p.sku_id, p.product_name FROM dim_product p ORDER BY p.product_name",
        "SELECT sku_id FROM dim_product",
    ],
)
def test_an_order_the_select_list_shows_is_not_hidden(sql):
    assert hidden_order_by(read_query(sql)) == []


def test_an_order_by_an_aggregate_nobody_selected_is_hidden():
    assert hidden_order_by(read_query(BARE_TOP_SKUS)) == ["sum(s.net_sales_amt)"]


def test_r2_names_the_hidden_order_and_otherwise_asks_for_the_measure():
    contract = top_skus_contract()
    gaps = rule_measure(contract, TEN_SKUS, read_query(BARE_TOP_SKUS), LABELS)
    assert [(g.rule, g.column) for g in gaps] == [(R2, "sum(s.net_sales_amt)")]

    ordered_by_output = "SELECT p.sku_id, p.product_name FROM dim_product p ORDER BY 2"
    names = rows(["sku_id", "product_name"], ["S", "Milk"])
    gaps = rule_measure(contract, names, read_query(ordered_by_output), LABELS)
    assert [g.column for g in gaps] == ["net sales"]
    assert "ranked by net sales" in gaps[0].why

    # A measure the question named is the question's to define: the hint
    # does not repeat the Supervisor's paraphrase of it.
    unranked = AnswerContract(measure="store count")
    gaps = rule_measure(unranked, rows(["banner_name"], ["A"]), read_query("SELECT banner_name FROM dim_store"), LABELS)
    assert gaps[0].column == "measure"
    assert "the question asks for a figure" in gaps[0].why and "store count" not in gaps[0].why

    bare = AnswerContract(ranked=True, measure="average price difference")
    gaps = rule_measure(bare, names, read_query(ordered_by_output), LABELS)
    assert gaps[0].column == "measure"
    assert "ranked by a figure" in gaps[0].why and "difference" not in gaps[0].why


def test_r2_stands_down_without_a_ranking_or_a_measure_or_rows():
    query = read_query(BARE_TOP_SKUS)
    assert rule_measure(AnswerContract(), TEN_SKUS, query, LABELS) == []
    assert rule_measure(top_skus_contract(), rows(["sku_id"]), query, LABELS) == []
    assert rule_measure(top_skus_contract(), TEN_FULL, read_query(FULL_TOP_SKUS), LABELS) == []


# ---------------------------------------------------------------------------
# R3 period
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "where",
    [
        "WHERE d.fiscal_year = 2025",
        "WHERE s.sales_date_key BETWEEN 20240401 AND 20250331",
        "WHERE s.sales_date_key >= 20240401 AND s.sales_date_key < 20250401",
        "WHERE d.calendar_date BETWEEN '2024-04-01' AND '2025-03-31'",
        "WHERE d.calendar_date >= '2024-04-01' AND d.calendar_date < '2025-04-01'",
    ],
)
def test_the_default_year_is_recognised_by_every_usual_route(where):
    sql = f"SELECT SUM(s.net_sales_amt) FROM fact_pos_retail_sales s JOIN dim_date d ON true {where}"
    assert filters_default_year(sql, top_skus_contract(), rows(["sum"], [1]))


def test_a_result_that_shows_it_is_the_default_year_needs_no_filter_in_the_text():
    contract = top_skus_contract()
    assert filters_default_year("SELECT 1", contract, rows(["fiscal_year", "n"], [2025, 1]))
    assert not filters_default_year("SELECT 1", contract, rows(["fiscal_year", "n"], [2024, 1]))
    assert not filters_default_year("SELECT 1 WHERE year = 2025", contract, rows(["n"], [1]))


def test_r3_wants_the_default_year_on_a_total_over_a_fact():
    gaps, applies = rule_period(top_skus_contract(), TEN_SKUS, read_query(BARE_TOP_SKUS))
    assert [(g.rule, g.column) for g in gaps] == [(R3, "fiscal_year = 2025")]
    assert gaps[0].table == "dim_date"
    assert not applies

    gaps, applies = rule_period(top_skus_contract(), TEN_FULL, read_query(FULL_TOP_SKUS))
    assert gaps == [] and applies


def test_r3_leaves_averages_dimension_counts_and_multi_year_results_alone():
    contract = top_skus_contract()
    average = "SELECT competitor_key, AVG(comp_regular_price) FROM fact_competitor_pricing GROUP BY 1"
    assert rule_period(contract, rows(["competitor_key", "avg"], [1, 2]), read_query(average)) == ([], False)

    count = "SELECT count(*) AS stores FROM dim_store"
    assert rule_period(contract, rows(["stores"], [40]), read_query(count)) == ([], False)

    by_year = "SELECT d.fiscal_year, SUM(net_sales_amt) FROM fact_pos_retail_sales s JOIN dim_date d ON true GROUP BY 1"
    two_years = rows(["fiscal_year", "sum"], [2024, 1], [2025, 2])
    assert rule_period(contract, two_years, read_query(by_year)) == ([], False)


def test_r3_checks_a_named_period_reaches_the_query():
    contract = build_contract("sales in fiscal year 2024", measure="net sales",
                              period="fiscal year 2024", resources=RESOURCES)
    missing = "SELECT SUM(net_sales_amt) FROM fact_pos_retail_sales"
    gaps, applies = rule_period(contract, rows(["sum"], [1]), read_query(missing))
    assert [g.column for g in gaps] == ["period fiscal year 2024"] and not applies

    present = "SELECT SUM(net_sales_amt) FROM fact_pos_retail_sales s JOIN dim_date d ON true WHERE d.fiscal_year = 2024"
    assert rule_period(contract, rows(["sum"], [1]), read_query(present)) == ([], False)

    vague = build_contract("sales last quarter", measure="net sales", period="last quarter", resources=RESOURCES)
    assert rule_period(vague, rows(["sum"], [1]), read_query(missing)) == ([], False)
    no_fact = "SELECT count(*) FROM dim_store"
    assert rule_period(contract, rows(["count"], [1]), read_query(no_fact)) == ([], False)


# ---------------------------------------------------------------------------
# R4 rows
# ---------------------------------------------------------------------------


def test_r4_sends_back_an_empty_result_unless_the_question_was_whether_any_exist():
    empty = rows(["sku_id"])
    gaps = rule_rows(AnswerContract(), "top 10 SKUs", empty, read_query("SELECT sku_id FROM dim_product"))
    assert [(g.rule, g.column) for g in gaps] == [(R4, NO_ROWS)]
    assert rule_rows(AnswerContract(), "Are there any stores in Texas?", empty, read_query("SELECT 1")) == []


def test_r4_holds_a_ranking_to_the_n_it_asked_for():
    contract = top_skus_contract()
    eleven = rows(["sku_id"], *[[i] for i in range(11)])
    query = read_query("SELECT sku_id FROM dim_product")
    assert "the result has 11" in rule_rows(contract, "top 10 SKUs", eleven, query)[0].why

    capped = rows(["sku_id"], *[[i] for i in range(10)], truncated=True)
    assert "10 or more" in rule_rows(contract, "top 10 SKUs", capped, query)[0].why

    assert rule_rows(contract, "top 10 SKUs", TEN_SKUS, query) == []


def test_r4_tells_a_short_result_from_a_short_limit():
    contract = top_skus_contract()
    five = rows(["sku_id"], *[[i] for i in range(5)])
    stopped = rule_rows(contract, "top 10 SKUs", five, read_query("SELECT sku_id FROM dim_product LIMIT 5"))
    assert "stops at LIMIT 5" in stopped[0].why
    # The data ran out: LIMIT 10 and five rows is all there is.
    assert rule_rows(contract, "top 10 SKUs", five, read_query("SELECT sku_id FROM dim_product LIMIT 10")) == []
    assert rule_rows(contract, "top 10 SKUs", five, read_query("SELECT sku_id FROM dim_product")) == []
    assert rule_rows(contract, "top 10 SKUs", five, read_query("SELECT sku_id FROM dim_product LIMIT ALL")) == []


def test_r4_lets_a_per_group_ranking_return_n_per_group():
    contract = build_contract("top 3 SKUs in each department", resources=RESOURCES)
    many = rows(["sku_id"], *[[i] for i in range(12)])
    assert rule_rows(contract, "top 3 SKUs in each department", many, read_query("SELECT 1")) == []


# ---------------------------------------------------------------------------
# Tier 2: the reflection
# ---------------------------------------------------------------------------


def test_the_schema_is_read_as_table_dot_column_pairs():
    assert schema_column_pairs(SCHEMA) == [
        "dim_product.product_key", "dim_product.sku_id", "dim_product.product_name",
        "dim_product.brand_name", "dim_product.department_name",
        "fact_pos_retail_sales.sales_date_key", "fact_pos_retail_sales.product_key",
        "fact_pos_retail_sales.net_sales_amt",
    ]
    assert schema_column_pairs("") == []


def test_the_reflection_may_only_name_columns_in_the_pruned_schema():
    llm = _Reflector([wants("dim_product.brand_name", "dim_store.region", "department_name",
                            "dim_product.sku_id", "dim_product.brand_name", why="")])
    gaps, note, calls = reflect(llm, question="top 10 SKUs", intent="aggregate",
                                contract=top_skus_contract(), result=TEN_FULL,
                                query=read_query(FULL_TOP_SKUS), schema=SCHEMA)
    # Not in the schema: discarded. Already shown: discarded. Named twice: once.
    # A bare name that is in the schema is qualified.
    assert [g.column for g in gaps] == ["dim_product.brand_name", "dim_product.department_name"]
    assert all(g.rule == REFLECTION for g in gaps)
    assert {g.table for g in gaps} == {"dim_product"}
    assert gaps[0].why == "add `dim_product.brand_name`"
    assert calls == 1 and note == ""
    prompt = llm.calls[0][1].content
    assert "A complete answer includes:" in prompt and "dim_product.brand_name" in prompt


def test_a_column_named_twice_by_the_reflection_is_asked_for_once():
    llm = _Reflector([wants("dim_product.brand_name", "brand_name")])
    gaps, _, _ = reflect(llm, question="q", intent="", contract=AnswerContract(), result=TEN_FULL,
                         query=read_query("SELECT 1"), schema=SCHEMA)
    assert [g.column for g in gaps] == ["dim_product.brand_name"]


def test_the_reflection_may_only_add_detail_about_entities_the_rows_identify():
    """The benchmark's first question: a correct count of stores, and a
    reflection that asked for store names. A column of a dimension the rows
    do not identify changes what a row is, so it is discarded."""
    llm = _Reflector([wants("dim_product.brand_name", "fact_pos_retail_sales.net_sales_amt")])
    gaps, _, _ = reflect(llm, question="q", intent="", contract=AnswerContract(), result=TEN_FULL,
                         query=read_query(FULL_TOP_SKUS), schema=SCHEMA, tables={"dim_store"})
    assert gaps == []


def test_a_count_identifies_nothing_so_the_reflection_is_not_asked():
    llm = _Reflector([])
    outcome = run_review("SELECT count(*) AS stores FROM dim_store", rows(["stores"], [10]),
                         llm=llm, question="How many stores are there?", contract=AnswerContract())
    assert outcome.issue is None and outcome.model_calls == 0 and llm.calls == []
    assert not outcome.report.reflected


def test_a_key_counted_inside_an_aggregate_identifies_no_entity():
    """The second benchmark run: COUNT(DISTINCT store_id) reads store_id, and
    reading it was taken to mean the rows were stores."""
    llm = _Reflector([])
    sql = "SELECT COUNT(DISTINCT s.store_id) AS store_count FROM dim_store s"
    outcome = run_review(sql, rows(["store_count"], [10]), llm=llm,
                         question="How many stores are there?", contract=AnswerContract())
    assert outcome.model_calls == 0 and llm.calls == []
    assert read_query(sql).selected == set()
    assert read_query("SELECT p.sku_id AS sku FROM dim_product p").selected == {"sku_id"}


def test_rows_identified_by_their_label_alone_can_still_be_reflected_on():
    result = rows(["product_name", "net_sales"], ["Whole Milk", 1])
    sql = "SELECT p.product_name, SUM(s.net_sales_amt) AS net_sales FROM fact_pos_retail_sales s JOIN dim_product p USING (product_key) GROUP BY 1"
    outcome = run_review(sql, result, llm=_Reflector([wants("dim_product.brand_name")]),
                         question="best products", contract=AnswerContract())
    assert [g.column for g in outcome.report.missing] == ["dim_product.brand_name"]


def test_the_reflection_names_at_most_three_columns():
    llm = _Reflector([wants("dim_product.brand_name", "dim_product.department_name",
                            "dim_product.product_key", "fact_pos_retail_sales.sales_date_key",
                            why="because.")])
    gaps, _, _ = reflect(llm, question="q", intent="", contract=AnswerContract(), result=TEN_FULL,
                         query=read_query("SELECT 1"), schema=SCHEMA)
    assert len(gaps) <= MAX_REFLECTION_COLUMNS
    assert gaps[0].why == "add `dim_product.brand_name`: because"


def test_a_reflection_that_fails_or_says_nothing_costs_nothing_but_its_note():
    gaps, note, calls = reflect(_Reflector([RuntimeError("model away")]), question="q", intent="",
                                contract=AnswerContract(), result=TEN_FULL, query=read_query("SELECT 1"),
                                schema="")
    assert (gaps, calls) == ([], 0) and "model away" in note
    assert reflect(_Reflector([None]), question="q", intent="", contract=AnswerContract(),
                   result=TEN_FULL, query=read_query("SELECT 1"), schema="") == ([], "", 1)


# ---------------------------------------------------------------------------
# The review
# ---------------------------------------------------------------------------


def run_review(sql, result, *, llm=None, prior=None, last_attempt=False, reflect_enabled=True,
               question="top 10 SKUs", contract=None):
    return review(
        question=question, sql=sql, result=result,
        contract=contract or top_skus_contract(), label_map=LABELS, intent="aggregate",
        prior=prior, llm=llm, reflect_enabled=reflect_enabled, schema=SCHEMA,
        last_attempt=last_attempt,
    )


def test_bare_skus_are_sent_back_with_every_gap_named_and_no_model_call():
    llm = _Reflector([])
    outcome = run_review(BARE_TOP_SKUS, TEN_SKUS, llm=llm)

    assert outcome.issue.source == COMPLETENESS
    assert [g.rule for g in outcome.report.missing] == [R1, R2, R3]
    assert outcome.issue.message.splitlines()[0].startswith("The query ran")
    assert outcome.model_calls == 0 and llm.calls == []
    assert not outcome.report.passed and not outcome.report.reflected
    assert outcome.assumptions == []
    assert outcome.report.sent_back == ["R1:product_name", "R2:sum(s.net_sales_amt)", "R3:fiscal_year = 2025"]


def test_a_complete_result_is_reflected_on_once_and_carries_its_assumption():
    outcome = run_review(FULL_TOP_SKUS, TEN_FULL, llm=_Reflector([complete()]))

    assert outcome.issue is None and outcome.report.passed
    assert outcome.model_calls == 1 and outcome.report.reflected
    assert outcome.assumptions == [
        "FY2025 (2024-04-01 to 2025-03-31), the latest complete fiscal year, "
        "since the question did not name a period"
    ]


def test_the_reflection_is_never_called_twice_and_its_asks_are_checked_without_it():
    first = run_review(FULL_TOP_SKUS, TEN_FULL, llm=_Reflector([wants("dim_product.brand_name")]))
    assert first.issue is not None and first.model_calls == 1
    assert [g.column for g in first.report.requested] == ["dim_product.brand_name"]

    # The next result has the brand: the ask is met with no second call.
    with_brand = rows(["sku_id", "product_name", "brand_name", "net_sales"], ["S", "Milk", "Acme", 1])
    llm = _Reflector([])
    second = run_review(FULL_TOP_SKUS.replace("p.product_name,", "p.product_name, p.brand_name,"),
                        with_brand, llm=llm, prior=first.report)
    assert second.issue is None and second.model_calls == 0 and llm.calls == []


def test_the_same_gap_is_never_sent_back_twice():
    first = run_review(FULL_TOP_SKUS, TEN_FULL, llm=_Reflector([wants("dim_product.brand_name")]))
    again = run_review(FULL_TOP_SKUS, TEN_FULL, llm=_Reflector([]), prior=first.report)

    assert again.issue is None
    assert [g.column for g in again.report.accepted_gaps] == ["dim_product.brand_name"]
    assert not again.report.passed
    assert describe(again.report) == "accepted without dim_product.brand_name"


def test_on_the_last_attempt_a_gap_is_told_rather_than_sent():
    outcome = run_review(BARE_TOP_SKUS, TEN_SKUS, last_attempt=True)
    assert outcome.issue is None
    assert [g.rule for g in outcome.report.accepted_gaps] == [R1, R2, R3]


def test_an_empty_result_is_sent_back_every_time_even_on_the_last_attempt():
    empty = rows(["sku_id"])
    first = run_review(BARE_TOP_SKUS, empty)
    again = run_review(BARE_TOP_SKUS, empty, prior=first.report, last_attempt=True)
    assert again.issue is not None and "no rows" in again.issue.message
    assert again.model_calls == 0


def test_the_reflection_can_be_switched_off_and_needs_a_model_and_rows():
    assert run_review(FULL_TOP_SKUS, TEN_FULL, llm=_Reflector([]), reflect_enabled=False).model_calls == 0
    assert run_review(FULL_TOP_SKUS, TEN_FULL, llm=None).report.reflected is False
    existence = run_review("SELECT store_id, store_name FROM dim_store", rows(["store_id", "store_name"]),
                           llm=_Reflector([]), question="Are there any stores?", contract=AnswerContract())
    assert existence.issue is None and not existence.report.reflected


def test_a_gap_sent_back_alongside_the_default_holds_the_assumption_until_it_is_fixed():
    partial = rows(["sku_id", "net_sales"], ["S", 1])
    sql = FULL_TOP_SKUS.replace("p.product_name, ", "").replace("GROUP BY 1, 2", "GROUP BY 1")
    outcome = run_review(sql, partial, llm=_Reflector([]))
    assert [g.rule for g in outcome.report.missing] == [R1]
    assert outcome.assumptions == []


def test_assumptions_are_found_without_the_gate():
    assert assumptions_for(top_skus_contract(), FULL_TOP_SKUS, TEN_FULL)[0].startswith("FY2025")
    assert assumptions_for(top_skus_contract(), BARE_TOP_SKUS, TEN_SKUS) == []


def test_the_message_and_the_trace_line_say_what_is_missing():
    gap = MissingColumn(column="product_name", why="add it", rule=R1)
    assert gap_message([gap]) == "The query ran, but the result is not yet a complete answer:\n- add it"
    assert describe(CompletenessReport()) == "complete"
    assert describe(CompletenessReport(reflected=True)) == "complete (reflected)"
    sent = CompletenessReport(passed=False, missing=[gap])
    assert describe(sent) == "missing product_name"
    both = CompletenessReport(
        passed=False,
        missing=[gap, MissingColumn(column="brand_name", why="x", rule=REFLECTION)],
        accepted_gaps=[MissingColumn(column="brand_name", why="x", rule=REFLECTION)],
    )
    assert describe(both) == "missing product_name; accepted without brand_name"


def test_a_whole_row_reference_inside_an_expression_is_not_a_column_name():
    query = read_query("SELECT count(s.*) AS n FROM fact_pos_retail_sales s")
    assert query.reads == set() and query.outputs == {"n"}


def test_an_order_by_a_column_nobody_selected_is_hidden():
    assert hidden_order_by(read_query("SELECT sku_id FROM dim_product ORDER BY brand_name")) == ["brand_name"]


def test_a_single_year_column_is_a_filter_not_a_span():
    sql = "SELECT d.fiscal_year, SUM(net_sales_amt) FROM fact_pos_retail_sales s JOIN dim_date d ON true GROUP BY 1"
    gaps, applies = rule_period(top_skus_contract(), rows(["fiscal_year", "sum"], [2025, 1]), read_query(sql))
    assert gaps == [] and applies


def test_without_the_years_bounds_only_the_fiscal_year_itself_counts():
    contract = AnswerContract(period="FY2025", period_default=True, fiscal_year=2025)
    assert not filters_default_year("WHERE sales_date_key BETWEEN 20240401 AND 20250331", contract, rows(["n"], [1]))
    assert filters_default_year("WHERE fiscal_year = 2025", contract, rows(["n"], [1]))
