"""The 15 benchmark questions, each with reference SQL that defines the answer.

These are **not** the golden pairs the agent retrieves from. A benchmark drawn
from the same 45 pairs would measure how well the agent can look something up,
which is not the thing worth knowing. These are new questions over the same
database, written to span what actually goes wrong:

- **schema** -- answerable from table and column names alone. The floor. A miss
  here is not a retrieval problem.
- **calendar** -- needs the fiscal calendar. FY2025 runs 2024-04-01 to
  2025-03-31, and a model that reads "2025" as a calendar year is wrong by nine
  months without any error to show for it.
- **grain** -- sales are daily, costs and allowances monthly, prices weekly.
  Joining across them on `date_key` matches almost nothing and returns a
  confident, tiny number.
- **fan-out** -- `fact_market_share_weekly` repeats each cell's totals once per
  competitor. Summing them raw overstates by exactly 5x, and the classic symptom
  is a market share above 100%.
- **analysis** -- correct but non-trivial aggregation: distinct-count
  denominators, ratios of sums, multi-table rollups.

Every reference query was run against the shipped dataset and its result
recorded below, so a change to the data is caught as a failing benchmark rather
than silently moving the target.

Accuracy is judged by **executing** the agent's SQL and comparing result sets,
not by comparing query text. Two correct queries rarely look alike, and the only
thing that matters is whether the rows come back right.

A question that admits two right answers cannot grade anything, so several are
more specific than they would first be asked. "Which department had the highest
net sales" is answerable with a bare name; "market share" is as legitimately a
fraction as a percentage; an allowance type is identified equally well by its
code or its name. Each of those cost a correct agent a mark before the question
was tightened, which is a defect in the benchmark rather than in the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA = "schema"
CALENDAR = "calendar"
GRAIN = "grain"
FANOUT = "fan-out"
ANALYSIS = "analysis"

CATEGORIES = (SCHEMA, CALENDAR, GRAIN, FANOUT, ANALYSIS)


@dataclass(frozen=True)
class BenchmarkQuestion:
    """One question, the SQL that defines its answer, and why it is here."""

    id: str
    category: str
    question: str
    reference_sql: str
    # What a wrong answer usually looks like. Recorded so a failure report can
    # say whether the agent fell into the trap the question was built around.
    trap: str = ""
    # True when the question names an order, so row order is part of the answer.
    ordered: bool = False
    expected_rows: int | None = None
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


QUESTIONS: tuple[BenchmarkQuestion, ...] = (
    BenchmarkQuestion(
        id="B01",
        category=SCHEMA,
        question="How many stores are there?",
        reference_sql="SELECT count(*) AS store_count FROM dim_store",
        expected_rows=1,
        notes="The floor. One table, one aggregate, no calendar and no joins.",
    ),
    BenchmarkQuestion(
        id="B02",
        category=SCHEMA,
        question="How many products do we carry, and how many of them are private label?",
        reference_sql=(
            "SELECT count(*) AS products, "
            "count(*) FILTER (WHERE is_private_label) AS private_label "
            "FROM dim_product"
        ),
        expected_rows=1,
        trap="Two aggregates over the same table; a second scan or a self-join also works.",
        notes="Tests conditional aggregation without needing any join.",
    ),
    BenchmarkQuestion(
        id="B03",
        category=SCHEMA,
        question="How many stores does each banner operate?",
        reference_sql=(
            "SELECT banner_name, count(*) AS stores FROM dim_store "
            "GROUP BY 1 ORDER BY 2 DESC, 1"
        ),
        expected_rows=4,
        notes="A plain grouping. Establishes that the model reads dim_store correctly.",
    ),
    BenchmarkQuestion(
        id="B04",
        category=CALENDAR,
        question="What were our total net sales in fiscal year 2025?",
        reference_sql=(
            "SELECT ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
            "FROM fact_pos_retail_sales s "
            "JOIN dim_date d ON d.date_key = s.sales_date_key "
            "WHERE d.fiscal_year = 2025"
        ),
        expected_rows=1,
        trap="Filtering on calendar 2025 instead of FY2025 (2024-04-01 .. 2025-03-31).",
        notes="6,032,194.28. A calendar-year read returns roughly a quarter of it.",
    ),
    BenchmarkQuestion(
        id="B05",
        category=CALENDAR,
        question=(
            "Which department had the highest net sales in fiscal year 2025, "
            "and what were those sales?"
        ),
        reference_sql=(
            "SELECT p.department_name, ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
            "FROM fact_pos_retail_sales s "
            "JOIN dim_date d ON d.date_key = s.sales_date_key "
            "JOIN dim_product p ON p.product_key = s.product_key "
            "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
        ),
        expected_rows=1,
        ordered=True,
        trap="Using gross_sales_amt, which ignores markdowns and can reorder the top.",
        notes="Meat & Seafood. Three tables plus a top-N.",
    ),
    BenchmarkQuestion(
        id="B06",
        category=CALENDAR,
        question="What was the total markdown discount given in fiscal quarter 4 of fiscal year 2025?",
        reference_sql=(
            "SELECT ROUND(SUM(s.markdown_discount_amt), 2) AS markdowns "
            "FROM fact_pos_retail_sales s "
            "JOIN dim_date d ON d.date_key = s.sales_date_key "
            "WHERE d.fiscal_year = 2025 AND d.fiscal_quarter = 4"
        ),
        expected_rows=1,
        trap="Deriving the quarter from calendar_date rather than using fiscal_quarter.",
        notes="Fiscal Q4 of FY2025 is 2024-12-30 .. 2025-03-31.",
    ),
    BenchmarkQuestion(
        id="B07",
        category=GRAIN,
        question=(
            "What was the gross margin percentage for the Dairy & Eggs department "
            "in fiscal month 12 of fiscal year 2025?"
        ),
        reference_sql=(
            "WITH s AS ("
            "  SELECT d.fiscal_year, d.fiscal_month_num, x.product_key, x.store_key,"
            "         SUM(x.net_sales_amt) AS net_sales, SUM(x.quantity_sold) AS units"
            "  FROM fact_pos_retail_sales x"
            "  JOIN dim_date d ON d.date_key = x.sales_date_key"
            "  JOIN dim_product p ON p.product_key = x.product_key"
            "  WHERE p.department_name = 'Dairy & Eggs'"
            "    AND d.fiscal_year = 2025 AND d.fiscal_month_num = 12"
            "  GROUP BY 1, 2, 3, 4), "
            "c AS ("
            "  SELECT d.fiscal_year, d.fiscal_month_num, k.product_key, k.store_key,"
            "         AVG(k.net_item_cost) AS unit_cost"
            "  FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key"
            "  WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12"
            "  GROUP BY 1, 2, 3, 4) "
            "SELECT ROUND(100.0 * (SUM(s.net_sales) - SUM(s.units * c.unit_cost))"
            "             / NULLIF(SUM(s.net_sales), 0), 2) AS gross_margin_pct "
            "FROM s JOIN c USING (fiscal_year, fiscal_month_num, product_key, store_key)"
        ),
        expected_rows=1,
        trap=(
            "Joining daily sales to monthly costs on date_key. That matches only the "
            "24 month-start days in the whole dataset and understates by ~30x."
        ),
        notes="34.20%. The mixed-grain reconciliation, on a department no golden pair uses.",
        tags=("mixed-grain",),
    ),
    BenchmarkQuestion(
        id="B08",
        category=FANOUT,
        question=(
            "What is our overall market share, as a percentage, across all tracked "
            "products in fiscal year 2024?"
        ),
        reference_sql=(
            "WITH cell AS ("
            "  SELECT DISTINCT m.week_key, m.product_key, m.market_region_key,"
            "         m.grocer_sales_amount, m.total_market_sales_amount"
            "  FROM fact_market_share_weekly m"
            "  JOIN dim_date d ON d.date_key = m.week_key"
            "  WHERE d.fiscal_year = 2024) "
            "SELECT ROUND(100.0 * SUM(grocer_sales_amount)"
            "             / NULLIF(SUM(total_market_sales_amount), 0), 2) AS market_share_pct "
            "FROM cell"
        ),
        expected_rows=1,
        trap=(
            "Summing grocer_sales_amount without de-duplicating. The table holds five "
            "rows per cell, one per competitor, with the two totals repeated on each; "
            "the schema-only agent answers 107.5% for this and does not flinch."
        ),
        notes="21.51%. The flagship retrieval case.",
        tags=("de-duplicate",),
    ),
    BenchmarkQuestion(
        id="B09",
        category=GRAIN,
        question=(
            "How much did we collect in vendor allowances in fiscal year 2025, "
            "broken down by allowance type? Report each type by its short code, "
            "like SCAN_BACK."
        ),
        reference_sql=(
            "SELECT t.allowance_type_code, ROUND(SUM(a.total_allowance_amt), 2) AS total "
            "FROM fact_vendor_allowances a "
            "JOIN dim_allowance_type t ON t.allowance_type_key = a.allowance_type_key "
            "JOIN dim_date d ON d.date_key = a.date_key "
            "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC"
        ),
        expected_rows=7,
        trap="Decoding the type from a literal rather than joining dim_allowance_type.",
        notes="Monthly grain, but summed within itself -- no cross-grain join needed.",
    ),
    BenchmarkQuestion(
        id="B10",
        category=ANALYSIS,
        question="Which 5 products had the highest net sales in fiscal year 2025? Give the SKU and the amount.",
        reference_sql=(
            "SELECT p.sku_id, ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
            "FROM fact_pos_retail_sales s "
            "JOIN dim_date d ON d.date_key = s.sales_date_key "
            "JOIN dim_product p ON p.product_key = s.product_key "
            "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
        ),
        expected_rows=5,
        ordered=True,
        notes="Top-N over the largest fact table, 1.29M rows.",
    ),
    BenchmarkQuestion(
        id="B11",
        category=ANALYSIS,
        question="What was the average basket value for each store banner in fiscal year 2025?",
        reference_sql=(
            "SELECT st.banner_name, "
            "       ROUND(SUM(s.net_sales_amt) / NULLIF(COUNT(DISTINCT s.basket_id), 0), 2) "
            "         AS avg_basket "
            "FROM fact_pos_retail_sales s "
            "JOIN dim_store st ON st.store_key = s.store_key "
            "JOIN dim_date d ON d.date_key = s.sales_date_key "
            "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC"
        ),
        expected_rows=4,
        trap=(
            "AVG(net_sales_amt), which averages basket *lines* rather than baskets. "
            "The denominator has to be COUNT(DISTINCT basket_id)."
        ),
        notes="The one question where the denominator is the whole difficulty.",
    ),
    BenchmarkQuestion(
        id="B12",
        category=ANALYSIS,
        question=(
            "For fiscal year 2025, how many promotional units were sold and how much "
            "incremental lift was generated, by promotion mechanic type?"
        ),
        reference_sql=(
            "SELECT pr.mechanic_type, "
            "       ROUND(SUM(f.promo_quantity_sold), 2) AS units, "
            "       ROUND(SUM(f.promo_quantity_lift), 2) AS lift "
            "FROM fact_promo_performance f "
            "JOIN dim_promotion pr ON pr.promotion_key = f.promotion_key "
            "JOIN dim_date d ON d.date_key = f.date_key "
            "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 3 DESC"
        ),
        expected_rows=6,
        trap=(
            "Grouping by the promo calendar instead of the fiscal one. The fact "
            "carries keys to both, and only dim_date has a fiscal_year."
        ),
        notes="Dual-calendar routing: filter on one calendar, group by a dimension of the other fact.",
        tags=("dual-calendar",),
    ),
    BenchmarkQuestion(
        id="B13",
        category=GRAIN,
        question="Which competitor prices lowest relative to us on average, and by how much?",
        reference_sql=(
            "SELECT c.competitor_name, "
            "       ROUND(AVG(100.0 * f.comp_regular_price "
            "             / NULLIF(p.regular_retail_price, 0)), 1) AS pct_of_our_price "
            "FROM fact_competitor_pricing f "
            "JOIN dim_competitor c ON c.competitor_key = f.competitor_key "
            "JOIN fact_item_prices p ON p.date_key = f.date_key "
            "  AND p.product_key = f.product_key AND p.store_key = f.store_key "
            "GROUP BY 1 ORDER BY 2 ASC LIMIT 1"
        ),
        expected_rows=1,
        ordered=True,
        trap=(
            "Both facts are weekly and share week-anchor date keys, so they join "
            "directly -- but only across the 4 audited stores and 70 surveyed products."
        ),
        notes="Bulk Barn Wholesale Club at 83.5% of our price.",
    ),
    BenchmarkQuestion(
        id="B14",
        category=ANALYSIS,
        question="What were our net sales by state in fiscal year 2025?",
        reference_sql=(
            "SELECT st.state_code, ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
            "FROM fact_pos_retail_sales s "
            "JOIN dim_store st ON st.store_key = s.store_key "
            "JOIN dim_date d ON d.date_key = s.sales_date_key "
            "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC"
        ),
        expected_rows=8,
        trap="Reaching for dim_geography, which is market regions and does not map to stores.",
        notes="Store geography lives on dim_store as plain columns.",
    ),
    BenchmarkQuestion(
        id="B15",
        category=ANALYSIS,
        question=(
            "How many ad impressions and clicks did each type of advertising channel "
            "-- Print Flyer, Paid Social and so on -- generate in fiscal year 2025?"
        ),
        reference_sql=(
            "SELECT ch.channel_type, SUM(a.impressions_count) AS impressions, "
            "       SUM(a.clicks_or_coupon_clips_count) AS clicks "
            "FROM fact_ad_performance a "
            "JOIN dim_ad_placement pl ON pl.ad_placement_key = a.ad_placement_key "
            "JOIN dim_ad_channel ch ON ch.ad_channel_key = pl.ad_channel_key "
            "JOIN dim_date d ON d.date_key = a.date_key "
            "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC"
        ),
        expected_rows=5,
        trap=(
            "fact_ad_performance has no channel key. The channel is reached through "
            "dim_ad_placement, and a model that misses that bridge cannot group at all."
        ),
        notes="Two-hop dimension bridge.",
    ),
)


def by_id(question_id: str) -> BenchmarkQuestion:
    for question in QUESTIONS:
        if question.id == question_id:
            return question
    raise KeyError(f"no benchmark question {question_id!r}")


def by_category(category: str) -> tuple[BenchmarkQuestion, ...]:
    return tuple(q for q in QUESTIONS if q.category == category)
