"""The follow-up set: a second question after each benchmark question, written by hand.

Phase 0 of arch8 (`multi-agent_arch_specs/Multi-Agent_NL2SQL_arch8.md`
section 10, and the implementation specification's section 8). Memory
rests on a premise -- that people ask the second question of a session as a
fragment that borrows from the first answer ("and by store?", "the same for
FY2024", "which of those"), and that the pipeline, which answers every
question alone, answers such a fragment wrongly most of the time -- and
these fifteen measure the premise before anything is built on it.
`python benchmarks/run_benchmark.py --followups` asks each fragment and
scores it against the reference of the complete question it means; with
nothing remembered (`--memory 0`, all the agent takes until Phase 2) the
fragment is asked alone, which is what 7.0 does with a follow-up.

Each follow-up is written as a person types one after seeing the first
question's answer: it borrows an entity, a measure, a period or a filter
from that turn -- B05's from its answer, the department it named -- and
says the rest. Its `standalone` is the complete question it means, written
by the rules arch8 section 23.3 gives the Supervisor: every number, name
and quoted value of the fragment kept as written, and nothing added that
the first turn does not supply. Each reference was checked by hand as the
benchmark's are, returns only what the standalone asks -- the scorer
allows extra columns and not missing ones -- and rounds to two places, as
the scorer compares. `tests/benchmarks/test_followups.py` holds every
standalone to the checks in code a model's restatement will meet
(`nl2sql_agent.memory.resolve`: C1 numbers, C2 literals, C3 polarity, C4
shape) against its fragment, so a standalone a person wrote that the checks
would discard is a failing test, which is how their word lists grow.

`keeps` are the first turn's filters the reference keeps, as the reference
writes them -- `fiscal_year = 2025`, `'Meat & Seafood'` -- and a delivered
query keeps one when it contains it, case and spacing aside: arch8 section
11's consistency, read from the SQL.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchmarks.questions import BenchmarkQuestion, by_id


@dataclass(frozen=True)
class Followup:
    """A second turn: what a person types, what they mean, and its answer."""

    #: The benchmark question it follows: the conversation's first turn.
    first: str
    #: The follow-up as a person types it after seeing that answer.
    fragment: str
    #: The complete question it means.
    standalone: str
    #: That question's reference, checked by hand as the benchmark's are.
    reference_sql: str
    #: True when the standalone names an order, so row order is part of the answer.
    ordered: bool = False
    expected_rows: int | None = None
    #: The first turn's filters the reference keeps, as it writes them.
    keeps: tuple[str, ...] = ()


FOLLOWUPS: dict[str, Followup] = {
    followup.first: followup
    for followup in (
        Followup(
            first="B01",
            fragment="And how many in each state?",
            standalone="How many stores are there in each state?",
            reference_sql="SELECT state_code, count(*) AS stores FROM dim_store GROUP BY 1 ORDER BY 2 DESC, 1",
            expected_rows=8,
        ),
        Followup(
            first="B02",
            fragment="Break that down by department.",
            standalone="How many products do we carry in each department, and how many of them are private label?",
            reference_sql=(
                "SELECT department_name, count(*) AS products, "
                "count(*) FILTER (WHERE is_private_label) AS private_label "
                "FROM dim_product GROUP BY 1 ORDER BY 1"
            ),
            expected_rows=17,
            keeps=("is_private_label",),
        ),
        Followup(
            first="B03",
            fragment="What's the average square footage for each?",
            standalone="What is the average square footage of the stores each banner operates?",
            reference_sql=(
                "SELECT banner_name, ROUND(AVG(square_footage), 2) AS avg_square_footage "
                "FROM dim_store GROUP BY 1 ORDER BY 1"
            ),
            expected_rows=4,
        ),
        Followup(
            first="B04",
            fragment="The same for FY2024?",
            standalone="What were our total net sales in FY2024?",
            reference_sql=(
                "SELECT ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
                "FROM fact_pos_retail_sales s "
                "JOIN dim_date d ON d.date_key = s.sales_date_key "
                "WHERE d.fiscal_year = 2024"
            ),
            expected_rows=1,
        ),
        Followup(
            first="B05",
            fragment="What were its top 2 categories?",
            standalone="Which 2 categories of the Meat & Seafood department had the highest net sales in fiscal year 2025?",
            reference_sql=(
                "SELECT p.category_name "
                "FROM fact_pos_retail_sales s "
                "JOIN dim_date d ON d.date_key = s.sales_date_key "
                "JOIN dim_product p ON p.product_key = s.product_key "
                "WHERE d.fiscal_year = 2025 AND p.department_name = 'Meat & Seafood' "
                "GROUP BY 1 ORDER BY SUM(s.net_sales_amt) DESC LIMIT 2"
            ),
            ordered=True,
            expected_rows=2,
            keeps=("fiscal_year = 2025", "'Meat & Seafood'"),
        ),
        Followup(
            first="B06",
            fragment="What about Q3?",
            standalone="What was the total markdown discount given in fiscal Q3 of fiscal year 2025?",
            reference_sql=(
                "SELECT ROUND(SUM(s.markdown_discount_amt), 2) AS markdowns "
                "FROM fact_pos_retail_sales s "
                "JOIN dim_date d ON d.date_key = s.sales_date_key "
                "WHERE d.fiscal_year = 2025 AND d.fiscal_quarter = 3"
            ),
            expected_rows=1,
            keeps=("fiscal_year = 2025",),
        ),
        Followup(
            first="B07",
            fragment="And for Frozen Foods?",
            standalone=(
                "What was the gross margin percentage for the Frozen Foods department "
                "in fiscal month 12 of fiscal year 2025?"
            ),
            reference_sql=(
                "WITH s AS ("
                "  SELECT d.fiscal_year, d.fiscal_month_num, x.product_key, x.store_key,"
                "         SUM(x.net_sales_amt) AS net_sales, SUM(x.quantity_sold) AS units"
                "  FROM fact_pos_retail_sales x"
                "  JOIN dim_date d ON d.date_key = x.sales_date_key"
                "  JOIN dim_product p ON p.product_key = x.product_key"
                "  WHERE p.department_name = 'Frozen Foods'"
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
            keeps=("fiscal_month_num = 12", "fiscal_year = 2025"),
        ),
        Followup(
            first="B08",
            fragment="And by market region?",
            standalone=(
                "What was our market share, as a percentage, in each market region "
                "across all tracked products in fiscal year 2024?"
            ),
            reference_sql=(
                "WITH cell AS ("
                "  SELECT DISTINCT m.week_key, m.product_key, m.market_region_key,"
                "         m.grocer_sales_amount, m.total_market_sales_amount"
                "  FROM fact_market_share_weekly m"
                "  JOIN dim_date d ON d.date_key = m.week_key"
                "  WHERE d.fiscal_year = 2024) "
                "SELECT g.region_name, ROUND(100.0 * SUM(c.grocer_sales_amount)"
                "             / NULLIF(SUM(c.total_market_sales_amount), 0), 2) AS market_share_pct "
                "FROM cell c JOIN dim_geography g ON g.market_region_key = c.market_region_key "
                "GROUP BY 1 ORDER BY 2 DESC"
            ),
            expected_rows=8,
            keeps=("fiscal_year = 2024",),
        ),
        Followup(
            first="B09",
            fragment="Which vendor paid us the most in SCAN_BACK?",
            standalone="Which vendor paid us the most in SCAN_BACK vendor allowances in fiscal year 2025?",
            reference_sql=(
                "SELECT v.vendor_name "
                "FROM fact_vendor_allowances a "
                "JOIN dim_allowance_type t ON t.allowance_type_key = a.allowance_type_key "
                "JOIN dim_vendor v ON v.vendor_key = a.vendor_key "
                "JOIN dim_date d ON d.date_key = a.date_key "
                "WHERE d.fiscal_year = 2025 AND t.allowance_type_code = 'SCAN_BACK' "
                "GROUP BY 1 ORDER BY SUM(a.total_allowance_amt) DESC LIMIT 1"
            ),
            ordered=True,
            expected_rows=1,
            keeps=("fiscal_year = 2025",),
        ),
        Followup(
            first="B10",
            fragment="What were their product names and brands?",
            standalone=(
                "What are the product names and brands of the 5 products "
                "that had the highest net sales in fiscal year 2025?"
            ),
            reference_sql=(
                "SELECT p.product_name, p.brand_name FROM dim_product p "
                "WHERE p.product_key IN ("
                "  SELECT s.product_key FROM fact_pos_retail_sales s"
                "  JOIN dim_date d ON d.date_key = s.sales_date_key"
                "  WHERE d.fiscal_year = 2025"
                "  GROUP BY 1 ORDER BY SUM(s.net_sales_amt) DESC LIMIT 5)"
            ),
            expected_rows=5,
            keeps=("fiscal_year = 2025", "LIMIT 5"),
        ),
        Followup(
            first="B11",
            fragment="And how many baskets did each have?",
            standalone="How many baskets did each store banner have in fiscal year 2025?",
            reference_sql=(
                "SELECT st.banner_name, COUNT(DISTINCT s.basket_id) AS baskets "
                "FROM fact_pos_retail_sales s "
                "JOIN dim_store st ON st.store_key = s.store_key "
                "JOIN dim_date d ON d.date_key = s.sales_date_key "
                "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC"
            ),
            expected_rows=4,
            keeps=("fiscal_year = 2025",),
        ),
        Followup(
            first="B12",
            fragment="What about the BOGO ones, by fiscal quarter?",
            standalone=(
                "For fiscal year 2025, how many promotional units were sold and how much "
                "incremental lift was generated by BOGO promotions, by fiscal quarter?"
            ),
            reference_sql=(
                "SELECT d.fiscal_quarter, "
                "       ROUND(SUM(f.promo_quantity_sold), 2) AS units, "
                "       ROUND(SUM(f.promo_quantity_lift), 2) AS lift "
                "FROM fact_promo_performance f "
                "JOIN dim_promotion pr ON pr.promotion_key = f.promotion_key "
                "JOIN dim_date d ON d.date_key = f.date_key "
                "WHERE d.fiscal_year = 2025 AND pr.mechanic_type = 'BOGO' GROUP BY 1 ORDER BY 1"
            ),
            expected_rows=3,
            keeps=("fiscal_year = 2025",),
        ),
        Followup(
            first="B13",
            fragment="And which prices highest?",
            standalone="Which competitor prices highest relative to us on average, and by how much?",
            reference_sql=(
                "SELECT c.competitor_name, "
                "       ROUND(AVG(100.0 * f.comp_regular_price "
                "             / NULLIF(p.regular_retail_price, 0)), 1) AS pct_of_our_price "
                "FROM fact_competitor_pricing f "
                "JOIN dim_competitor c ON c.competitor_key = f.competitor_key "
                "JOIN fact_item_prices p ON p.date_key = f.date_key "
                "  AND p.product_key = f.product_key AND p.store_key = f.store_key "
                "GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
            ),
            ordered=True,
            expected_rows=1,
        ),
        Followup(
            first="B14",
            fragment="And by banner?",
            standalone="What were our net sales by banner in fiscal year 2025?",
            reference_sql=(
                "SELECT st.banner_name, ROUND(SUM(s.net_sales_amt), 2) AS net_sales "
                "FROM fact_pos_retail_sales s "
                "JOIN dim_store st ON st.store_key = s.store_key "
                "JOIN dim_date d ON d.date_key = s.sales_date_key "
                "WHERE d.fiscal_year = 2025 GROUP BY 1 ORDER BY 2 DESC"
            ),
            expected_rows=4,
            keeps=("fiscal_year = 2025",),
        ),
        Followup(
            first="B15",
            fragment="Which of them had the fewest clicks?",
            standalone="Which type of advertising channel had the fewest clicks in fiscal year 2025?",
            reference_sql=(
                "SELECT ch.channel_type "
                "FROM fact_ad_performance a "
                "JOIN dim_ad_placement pl ON pl.ad_placement_key = a.ad_placement_key "
                "JOIN dim_ad_channel ch ON ch.ad_channel_key = pl.ad_channel_key "
                "JOIN dim_date d ON d.date_key = a.date_key "
                "WHERE d.fiscal_year = 2025 "
                "GROUP BY 1 ORDER BY SUM(a.clicks_or_coupon_clips_count) ASC LIMIT 1"
            ),
            ordered=True,
            expected_rows=1,
            keeps=("fiscal_year = 2025",),
        ),
    )
}


def as_question(followup: Followup) -> BenchmarkQuestion:
    """The complete question a follow-up means, as the benchmark scores one:
    its first turn's id and category, the standalone's words, its own
    reference and order."""
    first = by_id(followup.first)
    return BenchmarkQuestion(
        id=first.id, category=first.category, question=followup.standalone,
        reference_sql=followup.reference_sql, ordered=followup.ordered, expected_rows=followup.expected_rows,
    )


def _squeezed(text: str) -> str:
    return "".join(text.split()).casefold()


def kept(literal: str, sql: str | None) -> bool:
    """Whether a query keeps one of the first turn's filters: the filter as
    the reference writes it, found in the query with case and spacing aside."""
    return bool(sql) and _squeezed(literal) in _squeezed(sql)


def keeps(followup: Followup, sql: str | None) -> dict[str, bool]:
    """Each of the first turn's filters the reference keeps, and whether `sql` keeps it too."""
    return {literal: kept(literal, sql) for literal in followup.keeps}
