"""The grain check: the grains read from the data, the rule, and its words.

A rule that rejects has to be right twice over: about the queries it
refuses, and -- which is most of this file -- about the ones it lets by. A
check that sends a right query to repair is arch6's warning about validation
gates, made in code. So beside each shape that fails is every right query
that looks like it, and everything the reading cannot tell passes.

The trap queries here are the ones the benchmark's runs really wrote, taken
from the saved runs of 7.0's earlier phases.
"""

from __future__ import annotations

import psycopg
import pytest

from nl2sql_agent import grain
from nl2sql_agent.completeness import read_query
from nl2sql_agent.database import Column, Table
from nl2sql_agent.grain import ON_NO_DATE, ON_THE_DATE, Grain, Mismatch, UnknownPeriod
from nl2sql_agent.repair import GRAIN_HINT, classify, repair_hint
from nl2sql_agent.state import STATIC, Attempt, Issue
from nl2sql_agent.validate import validate

from tests.grain_fixture import COSTS, GRAINS, MONTH, PERIODS, PRICES, SALES, WEEK

# ---------------------------------------------------------------------------
# The grains, read once
# ---------------------------------------------------------------------------


def _table(name: str, columns: list[str], *constraints: str) -> Table:
    return Table(name=name, comment=None, approx_rows=1,
                 columns=[Column(column, "integer", True, None) for column in columns],
                 constraints=list(constraints))


CALENDAR = _table("dim_date", ["date_key", "calendar_date", "fiscal_week_num", "fiscal_month_num", "fiscal_year"],
                  "PRIMARY KEY (date_key)", "UNIQUE (calendar_date)")
DAILY = _table("fact_pos_retail_sales", ["sales_date_key", "product_key", "net_sales_amt"],
               "FOREIGN KEY (sales_date_key) REFERENCES dim_date(date_key)",
               "PRIMARY KEY (sales_date_key, product_key)")
WEEKLY = _table("fact_item_prices", ["date_key", "product_key"], "FOREIGN KEY (date_key) REFERENCES dim_date(date_key)")
MONTHLY = _table("fact_item_cogs", ["date_key", "product_key"], "FOREIGN KEY (date_key) REFERENCES dim_date(date_key)")
UNDATED = _table("dim_store", ["store_key", "store_name"], "PRIMARY KEY (store_key)")
EMPTY = _table("fact_returns", ["date_key"], "FOREIGN KEY (date_key) REFERENCES dim_date(date_key)")
CATALOG = [CALENDAR, DAILY, WEEKLY, MONTHLY, UNDATED, EMPTY]


class _Dates:
    """A database that answers `dates_per_period` from a table of answers:
    the most dates any one week holds, and any one month."""

    def __init__(self, most: dict[str, list[int]] | None = None, error: Exception | None = None) -> None:
        self.most = most or {
            "fact_pos_retail_sales": [9, 37], "fact_item_prices": [1, 5], "fact_item_cogs": [1, 1],
            "fact_returns": [0, 0],
        }
        self.error = error
        self.asked: list[tuple] = []

    def dates_per_period(self, table, column, periods, *, calendar, key):
        self.asked.append((table, column, tuple(periods), calendar, key))
        if self.error is not None:
            raise self.error
        return self.most[table]


def test_each_tables_grain_is_the_coarsest_period_that_holds_one_of_its_dates():
    """Nine dates in some week is daily. One a week and five a month is
    weekly. One in every month is monthly, whatever the key is called."""
    grains = grain.read_grains(_Dates(), CATALOG, PERIODS)

    assert grains["fact_pos_retail_sales"] == Grain("fact_pos_retail_sales", "sales_date_key", "day", (), 0)
    assert grains["fact_item_prices"] == Grain("fact_item_prices", "date_key", "fiscal week", WEEK, 1)
    assert grains["fact_item_cogs"] == Grain("fact_item_cogs", "date_key", "fiscal month", MONTH, 2)


def test_a_table_is_asked_about_by_the_column_that_references_the_date_dimensions_key():
    database = _Dates()
    grain.read_grains(database, CATALOG, PERIODS)

    assert ("fact_pos_retail_sales", "sales_date_key", PERIODS, "dim_date", "date_key") in database.asked
    # One query a date-stamped table, and none for the rest: a dimension, and
    # the date dimension itself.
    assert sorted(asked[0] for asked in database.asked) == [
        "fact_item_cogs", "fact_item_prices", "fact_pos_retail_sales", "fact_returns"]


def test_a_table_with_no_dates_has_no_grain_and_neither_has_one_with_no_date_column():
    grains = grain.read_grains(_Dates(), CATALOG, PERIODS)
    assert "fact_returns" not in grains and "dim_store" not in grains and "dim_date" not in grains


def test_a_table_with_two_dates_is_read_by_the_first_in_its_own_column_order():
    """The date it is stamped with, not whichever constraint is listed first."""
    orders = _table("fact_orders", ["order_date_key", "ship_date_key", "amount"],
                    "FOREIGN KEY (ship_date_key) REFERENCES dim_date(date_key)",
                    "FOREIGN KEY (order_date_key) REFERENCES dim_date(date_key)")
    database = _Dates({"fact_orders": [3, 9]})

    assert grain.read_grains(database, [CALENDAR, orders], PERIODS)["fact_orders"].column == "order_date_key"
    assert grain.date_column(orders) == ("order_date_key", "date_key")


def test_a_foreign_key_is_read_with_its_schema_and_its_quotes():
    quoted = _table("fact_x", ["Day"], 'FOREIGN KEY ("Day") REFERENCES public."dim_date"("date_key")')
    assert grain.date_column(quoted) == ("Day", "date_key")
    assert grain.date_column(_table("fact_y", ["k"], "FOREIGN KEY (k) REFERENCES dim_store(store_key)")) is None


def test_a_database_with_no_date_dimension_has_no_grains_and_is_not_asked():
    database = _Dates()
    assert grain.read_grains(database, [DAILY, UNDATED], PERIODS) == {}
    assert database.asked == []


def test_with_no_periods_named_there_is_nothing_coarser_than_a_day_to_find():
    database = _Dates()
    assert grain.read_grains(database, CATALOG, ()) == {}
    assert database.asked == []


def test_a_period_naming_a_column_the_date_dimension_lacks_is_refused_by_name():
    """A name in a setting, about to be written into a query."""
    with pytest.raises(UnknownPeriod, match=r"GRAIN_PERIODS names fiscal_wek_num, which dim_date does not have "
                                             r"\(it has calendar_date, date_key, fiscal_month_num"):
        grain.read_grains(_Dates(), CATALOG, (("fiscal_year", "fiscal_wek_num"),))


def test_a_database_that_cannot_be_read_says_so_to_whoever_asked():
    """`contract.load_resources` is who: it records the error and the check
    stands down (`tests/agent/test_contract.py`)."""
    with pytest.raises(psycopg.OperationalError):
        grain.read_grains(_Dates(error=psycopg.OperationalError("no route")), CATALOG, PERIODS)


@pytest.mark.parametrize("columns,name", [
    (("fiscal_year", "fiscal_week_num"), "fiscal week"),
    (("fiscal_year", "fiscal_month_num"), "fiscal month"),
    (("fiscal_year", "fiscal_quarter"), "fiscal quarter"),
    (("fiscal_year",), "fiscal year"),
    (("NRF_454_Week_Num",), "nrf 454 week"),
])
def test_a_period_is_named_by_its_last_column(columns, name):
    assert grain.period_name(columns) == name


# ---------------------------------------------------------------------------
# The rule: what fails
# ---------------------------------------------------------------------------


def shapes(sql: str, grains=GRAINS) -> list[str]:
    return [mismatch.shape for mismatch in grain.mismatches(read_query(sql), grains)]


MARGIN = ("SELECT ROUND(100.0 * (SUM(s.net_sales_amt) - SUM(s.quantity_sold * c.net_item_cost)) "
          "/ NULLIF(SUM(s.net_sales_amt), 0), 2) AS gross_margin_pct ")
DAIRY_IN_MONTH_12 = "WHERE p.department_name = 'Dairy & Eggs' AND d.fiscal_year = 2025 AND d.fiscal_month_num = 12"

#: B07's trap as the runs wrote it: the costs joined to the sales on the date.
TRAP = (
    MARGIN + "FROM fact_pos_retail_sales s "
    "JOIN dim_date d ON d.date_key = s.sales_date_key "
    "JOIN dim_product p ON p.product_key = s.product_key "
    "JOIN fact_item_cogs c ON c.date_key = s.sales_date_key AND c.product_key = s.product_key "
    + DAIRY_IN_MONTH_12
)
#: The same through the date dimension's key, and the product's dimension.
TRAP_THROUGH_THE_DATES = (
    MARGIN.replace("s.", "sales.").replace("c.", "cogs.") + "FROM fact_pos_retail_sales sales "
    "JOIN dim_date d ON d.date_key = sales.sales_date_key "
    "JOIN dim_product prod ON prod.product_key = sales.product_key "
    "JOIN fact_item_cogs cogs ON cogs.date_key = d.date_key AND cogs.product_key = prod.product_key "
    "WHERE prod.department_name = 'Dairy & Eggs' AND d.fiscal_year = 2025 AND d.fiscal_month_num = 12"
)
#: Written the old way, the joins in the WHERE.
TRAP_IN_THE_WHERE = (
    MARGIN + "FROM fact_pos_retail_sales s, fact_item_cogs c, dim_date d "
    "WHERE d.date_key = s.sales_date_key AND c.date_key = s.sales_date_key AND c.product_key = s.product_key "
    "AND d.fiscal_year = 2025 AND d.fiscal_month_num = 12"
)
#: The sales rolled up first -- by the day, which is no roll-up at all.
TRAP_ROLLED_UP_BY_DAY = (
    "WITH daily AS (SELECT x.sales_date_key, x.product_key, SUM(x.quantity_sold) AS units "
    "               FROM fact_pos_retail_sales x GROUP BY 1, 2) "
    "SELECT SUM(daily.units * c.net_item_cost) AS cost FROM daily "
    "JOIN fact_item_cogs c ON c.date_key = daily.sales_date_key AND c.product_key = daily.product_key"
)


@pytest.mark.parametrize("sql", [TRAP, TRAP_THROUGH_THE_DATES, TRAP_IN_THE_WHERE, TRAP_ROLLED_UP_BY_DAY],
                         ids=["directly", "through the date dimension", "in the WHERE", "rolled up by day"])
def test_daily_sales_joined_to_monthly_costs_on_the_date_is_a_mismatch(sql):
    [mismatch] = grain.mismatches(read_query(sql), GRAINS)
    assert mismatch == Mismatch(finer=SALES, coarser=COSTS, shape=ON_THE_DATE)


def test_weekly_prices_joined_to_monthly_costs_on_the_date_is_one_too():
    """Any two grains: the finer is whichever has more dates."""
    sql = ("SELECT AVG(p.regular_retail_price - c.net_item_cost) FROM fact_item_prices p "
           "JOIN fact_item_cogs c ON c.date_key = p.date_key AND c.product_key = p.product_key")
    assert grain.mismatches(read_query(sql), GRAINS) == [Mismatch(finer=PRICES, coarser=COSTS, shape=ON_THE_DATE)]


#: The repair that drops the date and stops: every month's cost meets each sale.
NO_DATE = (
    MARGIN + "FROM fact_pos_retail_sales s "
    "JOIN dim_date d ON d.date_key = s.sales_date_key "
    "JOIN dim_product p ON p.product_key = s.product_key "
    "JOIN fact_item_cogs c ON c.product_key = s.product_key AND c.store_key = s.store_key "
    + DAIRY_IN_MONTH_12
)
#: The costs rolled up, and still a row a month.
NO_DATE_ROLLED_UP_BY_MONTH = (
    "WITH monthly_cost AS (SELECT d.fiscal_year, d.fiscal_month_num, k.product_key, AVG(k.net_item_cost) AS unit_cost "
    "                      FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key GROUP BY 1, 2, 3) "
    "SELECT SUM(s.quantity_sold * mc.unit_cost) AS cost FROM fact_pos_retail_sales s "
    "JOIN monthly_cost mc ON mc.product_key = s.product_key"
)
#: Both rolled up to the month, and the month left out of the join.
NO_DATE_BOTH_ROLLED_UP = (
    "WITH s AS (SELECT d.fiscal_year, d.fiscal_month_num, x.product_key, SUM(x.quantity_sold) AS units "
    "           FROM fact_pos_retail_sales x JOIN dim_date d ON d.date_key = x.sales_date_key GROUP BY 1, 2, 3), "
    "c AS (SELECT d.fiscal_year, d.fiscal_month_num, k.product_key, AVG(k.net_item_cost) AS unit_cost "
    "      FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key GROUP BY 1, 2, 3) "
    "SELECT SUM(s.units * c.unit_cost) AS cost FROM s JOIN c ON c.product_key = s.product_key"
)
#: Each held to the fiscal year, which is twelve months of costs.
NO_DATE_IN_ONE_YEAR = (
    MARGIN + "FROM fact_pos_retail_sales s "
    "JOIN dim_date d ON d.date_key = s.sales_date_key "
    "JOIN fact_item_cogs c ON c.product_key = s.product_key AND c.store_key = s.store_key "
    "JOIN dim_date dc ON dc.date_key = c.date_key "
    "WHERE d.fiscal_year = 2025 AND dc.fiscal_year = 2025"
)


@pytest.mark.parametrize(
    "sql", [NO_DATE, NO_DATE_ROLLED_UP_BY_MONTH, NO_DATE_BOTH_ROLLED_UP, NO_DATE_IN_ONE_YEAR],
    ids=["row by row", "rolled up by month", "both rolled up", "each held to the year"],
)
def test_the_two_joined_on_no_date_with_the_costs_still_by_month_is_a_mismatch(sql):
    """Product and store alone: each sale meets every month's cost."""
    assert grain.mismatches(read_query(sql), GRAINS) == [Mismatch(finer=SALES, coarser=COSTS, shape=ON_NO_DATE)]


def test_a_roll_up_held_to_the_year_from_outside_is_still_twelve_months():
    sql = NO_DATE_ROLLED_UP_BY_MONTH + " WHERE mc.fiscal_year = 2025"
    assert shapes(sql) == [ON_NO_DATE]


def test_a_filter_on_the_roll_up_that_is_not_about_time_changes_nothing():
    assert shapes(NO_DATE_ROLLED_UP_BY_MONTH + " WHERE mc.product_key = 7") == [ON_NO_DATE]


# ---------------------------------------------------------------------------
# The rule: what passes
# ---------------------------------------------------------------------------

#: B07's reference, as the benchmark holds it and as the runs that got it
#: right wrote it: both rolled up to the fiscal month and joined on it.
RIGHT = (
    "WITH s AS (SELECT d.fiscal_year, d.fiscal_month_num, x.product_key, x.store_key, "
    "                  SUM(x.net_sales_amt) AS net_sales, SUM(x.quantity_sold) AS units "
    "           FROM fact_pos_retail_sales x JOIN dim_date d ON d.date_key = x.sales_date_key "
    "           JOIN dim_product p ON p.product_key = x.product_key "
    "           WHERE p.department_name = 'Dairy & Eggs' AND d.fiscal_year = 2025 AND d.fiscal_month_num = 12 "
    "           GROUP BY 1, 2, 3, 4), "
    "c AS (SELECT d.fiscal_year, d.fiscal_month_num, k.product_key, k.store_key, AVG(k.net_item_cost) AS unit_cost "
    "      FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key "
    "      WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12 GROUP BY 1, 2, 3, 4) "
    "SELECT ROUND(100.0 * (SUM(s.net_sales) - SUM(s.units * c.unit_cost)) / NULLIF(SUM(s.net_sales), 0), 2) "
    "FROM s JOIN c USING (fiscal_year, fiscal_month_num, product_key, store_key)"
)
RIGHT_AS_SUBQUERIES = (
    "SELECT SUM(s.units * c.unit_cost) FROM "
    "(SELECT d.fiscal_year, d.fiscal_month_num, x.product_key, SUM(x.quantity_sold) AS units "
    " FROM fact_pos_retail_sales x JOIN dim_date d ON d.date_key = x.sales_date_key GROUP BY 1, 2, 3) s "
    "JOIN (SELECT d.fiscal_year, d.fiscal_month_num, k.product_key, AVG(k.net_item_cost) AS unit_cost "
    "      FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key GROUP BY 1, 2, 3) c "
    "ON c.fiscal_year = s.fiscal_year AND c.fiscal_month_num = s.fiscal_month_num AND c.product_key = s.product_key"
)
#: Row by row, each through its own calendar, the calendars joined on the period.
ON_THE_PERIOD_THROUGH_TWO_CALENDARS = (
    MARGIN + "FROM fact_pos_retail_sales s "
    "JOIN dim_date d ON d.date_key = s.sales_date_key "
    "JOIN fact_item_cogs c ON c.product_key = s.product_key AND c.store_key = s.store_key "
    "JOIN dim_date dc ON dc.date_key = c.date_key "
    "  AND dc.fiscal_year = d.fiscal_year AND dc.fiscal_month_num = d.fiscal_month_num "
    "WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12"
)
#: The sales rolled up to the month, the costs read row by row through their
#: calendar and joined to that month.
ROLLED_UP_SALES_TO_COSTS_THROUGH_THEIR_CALENDAR = (
    "WITH ms AS (SELECT d.fiscal_year, d.fiscal_month_num, x.product_key, SUM(x.quantity_sold) AS units "
    "            FROM fact_pos_retail_sales x JOIN dim_date d ON d.date_key = x.sales_date_key GROUP BY 1, 2, 3) "
    "SELECT SUM(ms.units * c.net_item_cost) FROM ms "
    "JOIN fact_item_cogs c ON c.product_key = ms.product_key "
    "JOIN dim_date dc ON dc.date_key = c.date_key "
    "  AND dc.fiscal_year = ms.fiscal_year AND dc.fiscal_month_num = ms.fiscal_month_num"
)
#: One average cost a product: no longer by month, so product alone is right.
COSTS_ROLLED_UP_PAST_THE_MONTH = (
    "WITH unit AS (SELECT k.product_key, AVG(k.net_item_cost) AS unit_cost FROM fact_item_cogs k "
    "              JOIN dim_date d ON d.date_key = k.date_key WHERE d.fiscal_year = 2025 GROUP BY k.product_key) "
    "SELECT SUM(s.quantity_sold * unit.unit_cost) FROM fact_pos_retail_sales s "
    "JOIN unit ON unit.product_key = s.product_key"
)
#: Both held to one month, each by its own calendar: one cost a sale.
COSTS_HELD_TO_ONE_MONTH = (
    MARGIN + "FROM fact_pos_retail_sales s "
    "JOIN dim_date d ON d.date_key = s.sales_date_key "
    "JOIN fact_item_cogs c ON c.product_key = s.product_key AND c.store_key = s.store_key "
    "JOIN dim_date dc ON dc.date_key = c.date_key "
    "WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12 AND dc.fiscal_year = 2025 AND dc.fiscal_month_num = 12"
)


@pytest.mark.parametrize("sql", [
    RIGHT, RIGHT_AS_SUBQUERIES, ON_THE_PERIOD_THROUGH_TWO_CALENDARS, ROLLED_UP_SALES_TO_COSTS_THROUGH_THEIR_CALENDAR,
    COSTS_ROLLED_UP_PAST_THE_MONTH, COSTS_HELD_TO_ONE_MONTH,
], ids=["the reference", "as subqueries", "through two calendars", "roll-up to costs' calendar",
        "costs past the month", "costs held to one month"])
def test_a_join_at_one_grain_passes(sql):
    assert shapes(sql) == []


@pytest.mark.parametrize("held", [
    "c.date_key = 20250302",                                   # its one date
    "c.date_key = '20250302'::int",                            # the same, cast
    "20250302 = c.date_key",                                   # the same, the other way round
    "c.date_key BETWEEN 20250302 AND 20250329",                # a range: it may be a month
    "c.date_key IN (20250302, 20250330)",                      # a list
    "c.date_key = (SELECT MAX(k.date_key) FROM fact_item_cogs k WHERE k.product_key = c.product_key)",  # the latest
    "c.date_key >= s.sales_date_key - 31",                     # a condition between the two, unread
])
def test_costs_whose_time_is_filtered_in_a_way_that_may_be_one_period_pass(held):
    """Nothing here can be weighed, and a right query is among them: the
    latest cost a product, the cost in force that month."""
    sql = (MARGIN + "FROM fact_pos_retail_sales s JOIN fact_item_cogs c "
           f"ON c.product_key = s.product_key AND c.store_key = s.store_key WHERE {held}")
    # The subquery reads the costs a second time, which passes for that too.
    assert shapes(sql) == []


@pytest.mark.parametrize("held", [
    "dc.calendar_date = DATE '2025-03-02'",          # a calendar column that is none of its period's
    "dc.fiscal_week_num = 48 AND dc.fiscal_year = 2025",  # a week of a monthly table: inside one month
    "dc.fiscal_month_num IN (11, 12)",               # two months -- wrong, and not this check's to prove
    "fiscal_month_num = 12",                         # a column with no name before it: whose?
])
def test_costs_filtered_through_their_calendar_by_anything_but_their_own_period_pass(held):
    sql = (MARGIN + "FROM fact_pos_retail_sales s JOIN dim_date d ON d.date_key = s.sales_date_key "
           "JOIN fact_item_cogs c ON c.product_key = s.product_key JOIN dim_date dc ON dc.date_key = c.date_key "
           f"WHERE {held}")
    assert shapes(sql) == []


def test_a_roll_up_held_to_one_month_from_outside_passes():
    sql = NO_DATE_ROLLED_UP_BY_MONTH + " WHERE mc.fiscal_year = 2025 AND mc.fiscal_month_num = 12"
    assert shapes(sql) == []


@pytest.mark.parametrize("cte,held", [
    # Its date, kept by the roll-up and pinned outside.
    ("SELECT k.date_key, k.product_key, AVG(k.net_item_cost) AS unit_cost FROM fact_item_cogs k GROUP BY 1, 2",
     "mc.date_key = 20250302"),
    # A month the roll-up computed: time, and not one this can weigh.
    ("SELECT d.fiscal_year, MIN(d.date_key) AS month_start, k.product_key, AVG(k.net_item_cost) AS unit_cost "
     "FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key GROUP BY d.fiscal_year, d.fiscal_month_num, 3",
     "mc.month_start = 20250302"),
    # A calendar column kept under its own name that is none of its period's.
    ("SELECT d.fiscal_year, d.fiscal_quarter, k.product_key, AVG(k.net_item_cost) AS unit_cost "
     "FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key GROUP BY 1, 2, 3",
     "mc.fiscal_quarter = 4"),
    # A column written with no name before it, in a FROM of two: whose?
    ("SELECT k.product_key, fiscal_year, fiscal_month_num, AVG(k.net_item_cost) AS unit_cost "
     "FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key GROUP BY 1, 2, 3",
     "mc.fiscal_year = 2025"),
    # A column the roll-up does not output at all: the planner's to refuse.
    ("SELECT d.fiscal_year, d.fiscal_month_num, k.product_key, AVG(k.net_item_cost) AS unit_cost "
     "FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key GROUP BY 1, 2, 3",
     "mc.fiscal_period = 12"),
])
def test_a_filter_on_what_a_roll_up_outputs_is_followed_to_what_it_reads(cte, held):
    sql = (f"WITH mc AS ({cte}) SELECT SUM(s.quantity_sold * mc.unit_cost) FROM fact_pos_retail_sales s "
           f"JOIN mc ON mc.product_key = s.product_key WHERE {held}")
    assert shapes(sql) == []


def test_a_cte_that_selects_everything_cannot_be_followed_so_its_join_may_be_on_time():
    """`k.*`: which of its columns the join's is cannot be said."""
    sql = ("WITH mc AS (SELECT k.*, AVG(k.net_item_cost) OVER (PARTITION BY k.product_key) AS unit_cost "
           "            FROM fact_item_cogs k) "
           "SELECT SUM(s.quantity_sold * mc.unit_cost) FROM fact_pos_retail_sales s "
           "JOIN mc ON mc.product_key = s.product_key")
    assert shapes(sql) == []


def test_columns_written_without_a_table_are_the_one_tables_a_from_names():
    """A roll-up of one table need not say whose its columns are."""
    by_product = "SELECT product_key, AVG(net_item_cost) AS unit_cost FROM fact_item_cogs GROUP BY product_key"
    by_date = "SELECT date_key, product_key, AVG(net_item_cost) AS unit_cost FROM fact_item_cogs GROUP BY 1, 2"
    outer = "SELECT SUM(s.quantity_sold * mc.unit_cost) FROM fact_pos_retail_sales s JOIN mc ON "

    assert shapes(f"WITH mc AS ({by_product}) {outer} mc.product_key = s.product_key") == []
    assert shapes(f"WITH mc AS ({by_date}) {outer} mc.product_key = s.product_key") == [ON_NO_DATE]
    assert shapes(f"WITH mc AS ({by_date}) {outer} mc.date_key = s.sales_date_key") == [ON_THE_DATE]


def test_two_tables_of_one_grain_joined_on_the_date_are_what_the_date_is_for():
    daily = ("SELECT SUM(s.net_sales_amt), SUM(f.promo_quantity_sold) FROM fact_pos_retail_sales s "
             "JOIN fact_promo_performance f ON f.date_key = s.sales_date_key AND f.product_key = s.product_key")
    weekly = ("SELECT AVG(100.0 * f.comp_regular_price / NULLIF(p.regular_retail_price, 0)) "
              "FROM fact_competitor_pricing f JOIN fact_item_prices p ON p.date_key = f.date_key "
              "AND p.product_key = f.product_key AND p.store_key = f.store_key")
    assert shapes(daily) == [] and shapes(weekly) == []


@pytest.mark.parametrize("sql", [
    # A period join through CTEs that truncate the date to the month.
    "WITH s AS (SELECT date_trunc('month', d.calendar_date) AS month, x.product_key, SUM(x.quantity_sold) AS units "
    "           FROM fact_pos_retail_sales x JOIN dim_date d ON d.date_key = x.sales_date_key GROUP BY 1, 2), "
    "c AS (SELECT date_trunc('month', d.calendar_date) AS month, k.product_key, AVG(k.net_item_cost) AS unit_cost "
    "      FROM fact_item_cogs k JOIN dim_date d ON d.date_key = k.date_key GROUP BY 1, 2) "
    "SELECT SUM(s.units * c.unit_cost) FROM s JOIN c ON c.month = s.month AND c.product_key = s.product_key",
    # A rolled-up subquery joined on a month-start key, under the date column's own name.
    "WITH s AS (SELECT MIN(x.sales_date_key) AS sales_date_key, x.product_key, SUM(x.quantity_sold) AS units "
    "           FROM fact_pos_retail_sales x JOIN dim_date d ON d.date_key = x.sales_date_key "
    "           GROUP BY d.fiscal_year, d.fiscal_month_num, x.product_key) "
    "SELECT SUM(s.units * c.net_item_cost) FROM s "
    "JOIN fact_item_cogs c ON c.date_key = s.sales_date_key AND c.product_key = s.product_key",
    # A self-join of one fact: this year beside last.
    "SELECT SUM(a.net_sales_amt), SUM(b.net_sales_amt) FROM fact_pos_retail_sales a "
    "JOIN fact_pos_retail_sales b ON b.sales_date_key = a.sales_date_key - 364 AND b.product_key = a.product_key",
], ids=["months by date_trunc", "a month-start key", "a self-join"])
def test_the_right_shapes_a_rejection_would_hang_on_pass(sql):
    """arch7.3's risks by phase, S11: the reading was built to describe a
    difference, where a misreading costs a sentence. Here it costs a repair."""
    assert shapes(sql) == []


@pytest.mark.parametrize("cte", [
    # The latest cost a product, by DISTINCT ON.
    "SELECT DISTINCT ON (k.product_key) k.product_key, k.net_item_cost AS unit_cost "
    "FROM fact_item_cogs k ORDER BY k.product_key, k.date_key DESC",
    # The same by a window, numbered and filtered below.
    "SELECT k.product_key, k.net_item_cost AS unit_cost, "
    "ROW_NUMBER() OVER (PARTITION BY k.product_key ORDER BY k.date_key DESC) AS rn FROM fact_item_cogs k",
    # An average over a window, which leaves a row a month or one a product: it does not say.
    "SELECT DISTINCT k.product_key, AVG(k.net_item_cost) OVER (PARTITION BY k.product_key) AS unit_cost "
    "FROM fact_item_cogs k",
], ids=["DISTINCT ON", "ROW_NUMBER", "AVG OVER"])
def test_a_roll_up_the_reading_cannot_see_through_is_left_out_not_guessed(cte):
    """S13: behind a CTE that does not group them, the costs may be one row a
    product. Row by row is said only of a SELECT that reads the table itself."""
    sql = (f"WITH unit AS ({cte}) SELECT SUM(s.quantity_sold * unit.unit_cost) FROM fact_pos_retail_sales s "
           "JOIN unit ON unit.product_key = s.product_key")
    assert shapes(sql) == []


@pytest.mark.parametrize("join", [
    "JOIN fact_item_cogs c ON c.product_key = s.product_key "
    "AND date_trunc('month', dc.calendar_date) = date_trunc('month', d.calendar_date)",   # an expression
    "NATURAL JOIN fact_item_cogs c",                                                      # whatever they share
    "JOIN fact_item_cogs c ON c.product_key = s.product_key AND c.net_item_cost < s.net_sales_amt",  # not an equality
])
def test_a_join_the_reading_cannot_read_is_passed(join):
    sql = (MARGIN + "FROM fact_pos_retail_sales s JOIN dim_date d ON d.date_key = s.sales_date_key "
           "CROSS JOIN dim_date dc " + join)
    assert shapes(sql) == []


@pytest.mark.parametrize("more", [
    "NATURAL JOIN dim_store st",                 # joined to a third on whatever they share
    "JOIN dim_date dc USING (date_key)",         # a USING with a join on one side: whose date_key?
], ids=["a NATURAL join", "a USING across a join"])
def test_a_join_it_reads_beside_one_it_cannot_is_still_passed(more):
    """The two are joined on product, which is read. What else joins them
    is not, and may be the period."""
    sql = MARGIN + "FROM fact_pos_retail_sales s JOIN fact_item_cogs c ON c.product_key = s.product_key " + more
    assert shapes(sql) == []
    assert shapes(MARGIN + "FROM fact_pos_retail_sales s JOIN fact_item_cogs c ON c.product_key = s.product_key") == [
        ON_NO_DATE]


def test_a_using_across_a_join_is_passed_and_one_between_two_tables_is_read():
    across = (MARGIN + "FROM (fact_pos_retail_sales s JOIN dim_product p ON p.product_key = s.product_key) "
              "JOIN fact_item_cogs c USING (product_key)")
    between = MARGIN + "FROM fact_pos_retail_sales s JOIN fact_item_cogs c USING (product_key, store_key)"
    assert shapes(across) == []
    assert shapes(between) == [ON_NO_DATE]


def test_a_table_read_twice_is_passed():
    """Which reading a join or a filter belongs to cannot be told."""
    sql = (
        "WITH now AS (SELECT x.product_key, SUM(x.quantity_sold) AS units FROM fact_pos_retail_sales x GROUP BY 1), "
        "then AS (SELECT x.product_key, SUM(x.quantity_sold) AS units FROM fact_pos_retail_sales x GROUP BY 1) "
        "SELECT SUM(now.units * c.net_item_cost), SUM(then.units) FROM now JOIN then USING (product_key) "
        "JOIN fact_item_cogs c ON c.product_key = now.product_key"
    )
    assert shapes(sql) == []


def test_only_tables_the_query_adds_up_are_held_to_it():
    """The reading's own bound: a table joined to pick rows, not summed."""
    sql = ("SELECT SUM(s.net_sales_amt) FROM fact_pos_retail_sales s "
           "JOIN fact_item_cogs c ON c.date_key = s.sales_date_key AND c.product_key = s.product_key")
    assert shapes(sql) == []


@pytest.mark.parametrize("sql", ["", "not sql at all", "SELECT 1; SELECT 2",
                                 "SELECT store_key FROM dim_store UNION SELECT store_key FROM dim_store"])
def test_what_does_not_parse_or_joins_nothing_has_no_mismatch(sql):
    assert shapes(sql) == []


def test_with_no_grains_or_one_there_is_nothing_to_compare():
    assert shapes(TRAP, {}) == []
    assert shapes(TRAP, {"fact_item_cogs": COSTS}) == []


def test_two_ctes_that_read_each_other_are_followed_once_and_not_for_ever():
    """It parses, and the planner is what refuses it. This runs first, and
    has to come back: what `a` reads cannot be said, and the sales and the
    costs are joined on the date whatever it is."""
    sql = ("WITH a AS (SELECT b.sales_date_key, b.product_key FROM b), "
           "b AS (SELECT a.sales_date_key, a.product_key FROM a) "
           "SELECT SUM(s.quantity_sold * c.net_item_cost) FROM fact_pos_retail_sales s "
           "JOIN a ON a.sales_date_key = s.sales_date_key "
           "JOIN fact_item_cogs c ON c.product_key = s.product_key AND c.date_key = a.sales_date_key")
    assert shapes(sql) == [ON_THE_DATE]


def test_a_column_of_the_query_outside_is_not_one_of_this_selects_own():
    """A correlated subquery joins to a name its own FROM does not have."""
    sql = (
        "SELECT SUM(s.quantity_sold * (SELECT AVG(k.net_item_cost) FROM fact_item_cogs k "
        "                              JOIN dim_date dk ON dk.date_key = k.date_key "
        "                              WHERE k.product_key = s.product_key AND dk.fiscal_year = d.fiscal_year)) "
        "FROM fact_pos_retail_sales s JOIN dim_date d ON d.date_key = s.sales_date_key"
    )
    assert shapes(sql) == []


def test_a_roll_up_named_for_a_column_it_does_not_output_cannot_be_followed():
    """The join names a column the CTE never selects: the planner's to refuse."""
    sql = ("WITH mc AS (SELECT k.product_key, AVG(k.net_item_cost) AS unit_cost FROM fact_item_cogs k "
           "            JOIN dim_date d ON d.date_key = k.date_key GROUP BY k.product_key, d.fiscal_month_num) "
           "SELECT SUM(s.quantity_sold * mc.unit_cost) FROM fact_pos_retail_sales s "
           "JOIN mc ON mc.product_key = s.product_key AND mc.fiscal_month_num = s.sales_date_key")
    assert shapes(sql) == []


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------

ON_THE_DATE_MESSAGE = (
    "fact_item_cogs is monthly -- one date in each fiscal month -- and fact_pos_retail_sales is daily; joined on "
    "the date, each fiscal month's row of fact_item_cogs meets only the rows of fact_pos_retail_sales for that "
    "one date. Roll fact_pos_retail_sales up to the fiscal month (fiscal_year, fiscal_month_num) and join on "
    "those columns, not on the date."
)
ON_NO_DATE_MESSAGE = (
    "fact_item_cogs is monthly and fact_pos_retail_sales is daily, and they are joined on no date or period, so "
    "each row of fact_pos_retail_sales meets every fiscal month's row of fact_item_cogs. Join them on the fiscal "
    "month as well (fiscal_year, fiscal_month_num), or roll fact_item_cogs up past the fiscal month before "
    "joining it."
)


def test_each_shape_says_both_grains_and_the_way_round():
    """Worded from the grains: nothing here knows a table's name."""
    assert grain.message(Mismatch(SALES, COSTS, ON_THE_DATE)) == ON_THE_DATE_MESSAGE
    assert grain.message(Mismatch(SALES, COSTS, ON_NO_DATE)) == ON_NO_DATE_MESSAGE
    assert "fact_item_prices is weekly" in grain.message(Mismatch(PRICES, COSTS, ON_THE_DATE))
    assert list(grain.issues(TRAP, GRAINS)) == [ON_THE_DATE_MESSAGE]
    assert list(grain.issues(NO_DATE, GRAINS)) == [ON_NO_DATE_MESSAGE]
    assert list(grain.issues(RIGHT, GRAINS)) == []


def test_an_issue_is_known_for_the_grain_checks_by_its_own_words():
    assert grain.describes(ON_THE_DATE_MESSAGE) and grain.describes(ON_NO_DATE_MESSAGE)
    assert not grain.describes('Table "fact_x" is not in scope.') and not grain.describes("")


@pytest.mark.parametrize("message", [ON_THE_DATE_MESSAGE, ON_NO_DATE_MESSAGE], ids=["on the date", "on no date"])
def test_the_repair_agent_passes_a_grain_issue_on_without_asking_a_model(message):
    """The message is already the diagnosis, written from the data."""
    issue = Issue(source=STATIC, message=message)
    assert classify(issue) == GRAIN_HINT

    class _NeverAsked:
        def invoke(self, messages):
            raise AssertionError("a grain issue is not the model's to diagnose")

    hinted, calls = repair_hint([issue], llm=_NeverAsked(), history=[Attempt(sql=TRAP, issues=[issue])])
    assert calls == 0 and hinted[0].hint == GRAIN_HINT and hinted[0].message == message


# ---------------------------------------------------------------------------
# In the Static Validator
# ---------------------------------------------------------------------------


def test_with_grains_the_static_validator_refuses_the_trap_in_the_checks_own_words():
    assert validate(TRAP, grains=GRAINS) == [Issue(source=STATIC, message=ON_THE_DATE_MESSAGE)]
    assert validate(NO_DATE, grains=GRAINS) == [Issue(source=STATIC, message=ON_NO_DATE_MESSAGE)]


def test_without_grains_it_is_the_validator_it_was():
    assert validate(TRAP) == [] and validate(TRAP, grains=None) == [] and validate(TRAP, grains={}) == []


def test_a_right_query_passes_with_grains_as_without():
    assert validate(RIGHT, grains=GRAINS) == []


def test_the_grain_issue_comes_after_the_statements_own():
    """One repair round can fix both; the scope's is the cheaper to read first."""
    issues = validate(TRAP, allowed_tables=["fact_pos_retail_sales", "dim_date", "dim_product"], grains=GRAINS)
    assert [issue.message for issue in issues] == ["fact_item_cogs is not in scope.", ON_THE_DATE_MESSAGE]


def test_a_statement_with_nothing_to_walk_is_refused_before_the_grains_are_read():
    [issue] = validate("DELETE FROM fact_item_cogs", grains=GRAINS)
    assert "SELECT" in issue.message
