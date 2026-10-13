"""The retail catalog's grains, as its data measures to.

What `grain.read_grains` reads from the live database, written down for the
tests that have none: three facts with a row most days, three stamped once a
fiscal week, two once a fiscal month. `tests/agent/test_database_live.py`
holds this to the catalog under `--run-docker`, so a dataset that changes its
grains fails there rather than leaving these tests checking a rule against
grains nothing has.
"""

from __future__ import annotations

from nl2sql_agent.grain import Grain

WEEK = ("fiscal_year", "fiscal_week_num")
MONTH = ("fiscal_year", "fiscal_month_num")

#: `GRAIN_PERIODS`' default, parsed: finest first.
PERIODS = (WEEK, MONTH)


def _daily(table: str, column: str = "date_key") -> Grain:
    return Grain(table=table, column=column)


def _weekly(table: str, column: str = "date_key") -> Grain:
    return Grain(table=table, column=column, period="fiscal week", columns=WEEK, order=1)


def _monthly(table: str, column: str = "date_key") -> Grain:
    return Grain(table=table, column=column, period="fiscal month", columns=MONTH, order=2)


SALES = _daily("fact_pos_retail_sales", "sales_date_key")
ADS = _daily("fact_ad_performance")
PROMOS = _daily("fact_promo_performance")
COMPETITOR_PRICES = _weekly("fact_competitor_pricing")
PRICES = _weekly("fact_item_prices")
MARKET_SHARE = _weekly("fact_market_share_weekly", "week_key")
COSTS = _monthly("fact_item_cogs")
ALLOWANCES = _monthly("fact_vendor_allowances")

GRAINS: dict[str, Grain] = {
    grain.table: grain
    for grain in (SALES, ADS, PROMOS, COMPETITOR_PRICES, PRICES, MARKET_SHARE, COSTS, ALLOWANCES)
}
