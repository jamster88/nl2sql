"""Fixtures that build a small-but-complete synthetic dataset once per test
session, so every data_gen test file can inspect real generator output
without each re-running the pipeline.
"""

from __future__ import annotations

import pytest
from datagen import calendar_gen, dimensions, facts
from datagen.config import Config


@pytest.fixture(scope="session")
def tiny_config() -> Config:
    """Small enough to build in well under a second, large enough that every
    generator's branches (promos, ads, audits, allowances...) actually fire.
    """
    return Config(
        seed=7,
        first_fiscal_year=2024,
        num_fiscal_years=1,
        n_stores=3,
        n_products=25,
        n_competitors=3,
        n_vendors=5,
        n_market_regions=3,
        n_promotions=4,
        n_ad_channels=3,
        n_ad_placements=6,
        write_sqlite=False,
    )


@pytest.fixture(scope="session")
def built(tiny_config: Config) -> dict:
    """Run the full generation pipeline (mirrors generate_data.py:main) and
    return every intermediate and final frame, keyed by name.
    """
    config = tiny_config

    dim_date = calendar_gen.build_dim_date(config)
    dim_promo_calendar = calendar_gen.build_dim_promo_calendar(config, dim_date)

    dim_product = dimensions.gen_dim_product(config)
    dim_store = dimensions.gen_dim_store(config)
    dim_competitor = dimensions.gen_dim_competitor(config)
    dim_promotion = dimensions.gen_dim_promotion(config)
    dim_ad_channel = dimensions.gen_dim_ad_channel(config)
    dim_ad_placement = dimensions.gen_dim_ad_placement(config, dim_ad_channel)
    dim_vendor = dimensions.gen_dim_vendor(config)
    dim_product = dimensions.assign_product_vendors(config, dim_product, len(dim_vendor))
    dim_allowance_type = dimensions.gen_dim_allowance_type(config)
    dim_geography = dimensions.gen_dim_geography(config)

    store_assortment = facts.build_store_assortment(config, dim_store, dim_product)
    promo_participation = facts.build_promo_participation(
        config, dim_date, dim_promo_calendar, dim_promotion, dim_product, dim_store
    )
    ad_participation = facts.build_ad_participation(config, dim_date, dim_ad_placement, dim_product, dim_store)
    tracked_products = facts.select_tracked_products(config, dim_product)

    fact_item_prices = facts.gen_fact_item_prices(
        config, dim_date, dim_product, dim_store, store_assortment, promo_participation
    )
    fact_pos_retail_sales = facts.gen_fact_pos_retail_sales(
        config, dim_date, dim_product, dim_store, store_assortment, fact_item_prices
    )
    fact_item_cogs = facts.gen_fact_item_cogs(config, dim_date, dim_product, store_assortment)
    fact_vendor_allowances = facts.gen_fact_vendor_allowances(
        config, dim_date, dim_product, dim_store, dim_allowance_type
    )
    fact_competitor_pricing = facts.gen_fact_competitor_pricing(
        config, dim_date, dim_product, dim_store, dim_competitor, fact_item_prices, tracked_products
    )
    fact_market_share_weekly = facts.gen_fact_market_share_weekly(
        config, dim_date, dim_geography, dim_competitor, tracked_products
    )
    fact_promo_performance = facts.gen_fact_promo_performance(config, promo_participation, dim_store)
    fact_ad_performance = facts.gen_fact_ad_performance(config, ad_participation, dim_ad_placement, dim_ad_channel)

    return {
        "dim_date": dim_date,
        "dim_promo_calendar": dim_promo_calendar,
        "dim_product": dim_product,
        "dim_store": dim_store,
        "dim_competitor": dim_competitor,
        "dim_promotion": dim_promotion,
        "dim_ad_channel": dim_ad_channel,
        "dim_ad_placement": dim_ad_placement,
        "dim_vendor": dim_vendor,
        "dim_allowance_type": dim_allowance_type,
        "dim_geography": dim_geography,
        "store_assortment": store_assortment,
        "promo_participation": promo_participation,
        "ad_participation": ad_participation,
        "tracked_products": tracked_products,
        "fact_pos_retail_sales": fact_pos_retail_sales,
        "fact_item_cogs": fact_item_cogs,
        "fact_vendor_allowances": fact_vendor_allowances,
        "fact_item_prices": fact_item_prices,
        "fact_competitor_pricing": fact_competitor_pricing,
        "fact_market_share_weekly": fact_market_share_weekly,
        "fact_promo_performance": fact_promo_performance,
        "fact_ad_performance": fact_ad_performance,
    }


# The 19 tables named in ddl.sql / schema_columns.TABLE_ORDER, i.e. `built`
# minus the working-only intermediate frames (store_assortment, participation,
# tracked_products).
_OFFICIAL_TABLE_NAMES = [
    "dim_date", "dim_promo_calendar", "dim_product", "dim_store",
    "dim_competitor", "dim_promotion", "dim_ad_channel", "dim_ad_placement",
    "dim_vendor", "dim_allowance_type", "dim_geography",
    "fact_pos_retail_sales", "fact_item_cogs", "fact_vendor_allowances",
    "fact_item_prices", "fact_competitor_pricing", "fact_market_share_weekly",
    "fact_promo_performance", "fact_ad_performance",
]


@pytest.fixture(scope="session")
def official_tables(built: dict) -> dict:
    """Just the 19 tables generate_data.py would write out / hand to validate()."""
    return {name: built[name] for name in _OFFICIAL_TABLE_NAMES}
