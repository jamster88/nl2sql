"""Generators for fact_* tables: business-rule invariants that ddl.sql's
foreign keys and validate.py's PK/FK checks don't cover -- non-negativity,
markdown/margin arithmetic, the market-share fan-out shape, and the two
edge-case branches for promo/ad performance when there's no participation.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from datagen import facts
from datagen.config import Config

# ---------------------------------------------------------------------------
# store_assortment / promo & ad participation (intermediate frames)
# ---------------------------------------------------------------------------


def test_store_assortment_every_store_has_products_within_config_range(built, tiny_config):
    df = built["store_assortment"]
    lo, hi = tiny_config.store_assortment_range
    counts = df.groupby("store_key").size()
    assert len(counts) == tiny_config.n_stores
    min_expected = max(1, round(lo * tiny_config.n_products)) - 1  # rounding slack
    max_expected = round(hi * tiny_config.n_products) + 1
    assert counts.between(min_expected, max_expected).all()
    assert not df.duplicated(subset=["store_key", "product_key"]).any()


def test_promo_participation_columns_and_discount_within_mechanic_ranges(built):
    df = built["promo_participation"]
    assert not df.empty
    assert {"product_key", "store_key", "date_key", "discount_pct", "promotion_key"} <= set(df.columns)
    assert df["discount_pct"].between(0.05, 0.55).all()
    # One active promo price per product/store/day.
    assert not df.duplicated(subset=["product_key", "store_key", "date_key"]).any()


def test_ad_participation_columns(built):
    df = built["ad_participation"]
    assert not df.empty
    assert {"ad_placement_key", "product_key", "store_key", "date_key"} <= set(df.columns)


def test_select_tracked_products(built, tiny_config):
    tracked = built["tracked_products"]
    n_expected = max(1, round(tiny_config.market_share_product_fraction * tiny_config.n_products))
    assert len(tracked) == n_expected
    assert len(set(tracked)) == len(tracked)  # no duplicates
    assert set(tracked) <= set(built["dim_product"]["product_key"])


# ---------------------------------------------------------------------------
# fact_item_prices
# ---------------------------------------------------------------------------


def test_fact_item_prices_positive_and_promo_below_regular(built):
    df = built["fact_item_prices"]
    assert (df["regular_retail_price"] > 0).all()
    on_promo = df["base_promo_price"].notna()
    assert on_promo.any()
    assert (df.loc[on_promo, "base_promo_price"] < df.loc[on_promo, "regular_retail_price"]).all()
    assert not df.duplicated(subset=["date_key", "product_key", "store_key"]).any()


# ---------------------------------------------------------------------------
# fact_pos_retail_sales
# ---------------------------------------------------------------------------


def test_fact_pos_retail_sales_quantities_match_unit_kind(built):
    sales = built["fact_pos_retail_sales"]
    product_unit_kind = built["dim_product"].set_index("product_key")["_unit_kind"]
    merged = sales.merge(
        product_unit_kind.rename("_unit_kind"), left_on="product_key", right_index=True
    )
    weight_rows = merged[merged["_unit_kind"] == "weight"]
    each_rows = merged[merged["_unit_kind"] == "each"]
    assert not weight_rows.empty and not each_rows.empty
    assert weight_rows["quantity_sold"].between(0.4, 3.5).all()
    assert each_rows["quantity_sold"].isin([1.0, 2.0, 3.0, 4.0]).all()


def test_fact_pos_retail_sales_amount_arithmetic(built):
    df = built["fact_pos_retail_sales"]
    assert (df["markdown_discount_amt"] >= 0).all()
    assert (df["gross_sales_amt"] >= 0).all()
    # net = gross - markdown, rounded to cents; allow float rounding slack.
    expected_net = (df["gross_sales_amt"] - df["markdown_discount_amt"]).round(2)
    assert np.allclose(df["net_sales_amt"], expected_net, atol=1e-6)
    assert (df["net_sales_amt"] <= df["gross_sales_amt"] + 1e-9).all()


def test_fact_pos_retail_sales_basket_id_format_and_pk(built):
    df = built["fact_pos_retail_sales"]
    assert df["basket_id"].str.match(r"^BSK\d{10}$").all()
    assert not df.duplicated(
        subset=["sales_date_key", "product_key", "store_key", "basket_id"]
    ).any()


def test_fact_pos_retail_sales_weekend_and_holiday_lift_basket_traffic(built):
    """weekend_basket_multiplier=1.5 / holiday_basket_multiplier=1.8 should show
    up as materially more distinct baskets per store on those days -- a loose
    (not exact-ratio) check that the multipliers are actually wired in.
    """
    sales = built["fact_pos_retail_sales"]
    dim_date = built["dim_date"][["date_key", "calendar_date", "is_holiday"]].copy()
    dim_date["dow"] = pd.to_datetime(dim_date["calendar_date"]).dt.dayofweek
    dim_date["is_weekend"] = dim_date["dow"] >= 5

    baskets = sales.drop_duplicates(subset=["sales_date_key", "store_key", "basket_id"])
    per_day = baskets.groupby("sales_date_key").size().rename("n_baskets")
    per_day = per_day.reindex(dim_date["date_key"], fill_value=0)
    merged = dim_date.set_index("date_key").join(per_day)

    weekday_normal = merged[~merged["is_weekend"] & ~merged["is_holiday"]]["n_baskets"].mean()
    weekend = merged[merged["is_weekend"] & ~merged["is_holiday"]]["n_baskets"].mean()
    holiday = merged[merged["is_holiday"]]["n_baskets"].mean()

    assert weekend > weekday_normal * 1.1
    assert holiday > weekday_normal * 1.1


# ---------------------------------------------------------------------------
# fact_item_cogs
# ---------------------------------------------------------------------------


def test_fact_item_cogs_positive_and_freight_ratio(built):
    df = built["fact_item_cogs"]
    assert (df["base_cost"] > 0).all()
    assert (df["freight_cost"] > 0).all()
    ratio = df["freight_cost"] / df["base_cost"]
    # uniform(0.01, 0.05) before rounding -- allow rounding slack at the edges.
    assert ratio.between(0.005, 0.06).all()


def test_fact_item_cogs_net_cost_matches_base_plus_freight_within_rounding(built):
    """base_cost and freight_cost are each rounded independently to 4dp, but
    net_item_cost is round(base_cost + freight_cost) computed from the
    *unrounded* values -- so net can differ from
    round(base_cost,4) + round(freight_cost,4) by a small amount. This is a
    known, investigated property of the generator (not a bug): the deltas
    are on the order of 1e-4, not a real accounting error.
    """
    df = built["fact_item_cogs"]
    diff = (df["net_item_cost"] - (df["base_cost"] + df["freight_cost"])).abs()
    assert (diff <= 0.0001 + 1e-9).all()
    # And it does actually happen at this scale -- otherwise the tolerance
    # above would be vacuous.
    assert (diff > 0).any()


# ---------------------------------------------------------------------------
# fact_vendor_allowances
# ---------------------------------------------------------------------------


def test_fact_vendor_allowances_positive_and_vendor_matches_product(built):
    df = built["fact_vendor_allowances"]
    assert not df.empty
    assert (df["allowance_rate_per_unit"] > 0).all()
    assert (df["total_allowance_amt"] > 0).all()

    product_vendor = built["dim_product"].set_index("product_key")["_vendor_key"]
    joined = df.merge(product_vendor.rename("_expected_vendor_key"), left_on="product_key", right_index=True)
    assert (joined["vendor_key"] == joined["_expected_vendor_key"]).all()


# ---------------------------------------------------------------------------
# fact_competitor_pricing
# ---------------------------------------------------------------------------


def test_fact_competitor_pricing_bounds_and_referential_shape(built, tiny_config):
    df = built["fact_competitor_pricing"]
    assert not df.empty
    assert (df["comp_regular_price"] > 0).all()
    on_promo = df["comp_promo_price"].notna()
    assert on_promo.any()
    assert (df.loc[on_promo, "comp_promo_price"] < df.loc[on_promo, "comp_regular_price"]).all()

    assert set(df["product_key"]) <= set(built["tracked_products"])
    n_audit = max(1, round(tiny_config.audit_store_fraction * tiny_config.n_stores))
    assert df["store_key"].nunique() <= n_audit

    # Every row must also have an our-price on file for that date/product/store
    # (fact_item_prices is inner-joined in).
    our_keys = set(
        map(tuple, built["fact_item_prices"][["date_key", "product_key", "store_key"]].to_numpy().tolist())
    )
    row_keys = set(map(tuple, df[["date_key", "product_key", "store_key"]].to_numpy().tolist()))
    assert row_keys <= our_keys


# ---------------------------------------------------------------------------
# fact_market_share_weekly
# ---------------------------------------------------------------------------


def test_fact_market_share_weekly_fan_out_shape(built, tiny_config):
    """Each (week, product, region) cell repeats grocer_sales_amount and
    total_market_sales_amount once per competitor row -- summing
    total_market_sales_amount naively over-counts by a factor of n_competitors.
    This is the fan-out trap documented in knowledge/; pin its shape here.
    """
    df = built["fact_market_share_weekly"]
    group_cols = ["week_key", "product_key", "market_region_key"]
    group_sizes = df.groupby(group_cols).size()
    assert (group_sizes == tiny_config.n_competitors).all()

    grouped = df.groupby(group_cols)
    assert (grouped["total_market_sales_amount"].nunique() == 1).all()
    assert (grouped["grocer_sales_amount"].nunique() == 1).all()


def test_fact_market_share_weekly_grocer_share_within_configured_range(built, tiny_config):
    df = built["fact_market_share_weekly"]
    share = df["grocer_sales_amount"] / df["total_market_sales_amount"]
    lo, hi = 0.08, 0.35
    assert share.between(lo - 1e-6, hi + 1e-6).all()


def test_fact_market_share_weekly_competitor_amounts_sum_to_remaining_market(built):
    df = built["fact_market_share_weekly"]
    group_cols = ["week_key", "product_key", "market_region_key"]
    grouped = df.groupby(group_cols).agg(
        total=("total_market_sales_amount", "first"),
        grocer=("grocer_sales_amount", "first"),
        competitor_sum=("competitor_sales_amount", "sum"),
    )
    expected_remaining = grouped["total"] - grouped["grocer"]
    # Dirichlet-weighted split of "remaining" across competitors, each amount
    # rounded to cents before summing -- small rounding slack per competitor.
    assert np.allclose(grouped["competitor_sum"], expected_remaining, atol=0.05)


# ---------------------------------------------------------------------------
# fact_promo_performance / fact_ad_performance
# ---------------------------------------------------------------------------


def test_fact_promo_performance_positive_and_lift_bounded(built):
    df = built["fact_promo_performance"]
    assert not df.empty
    assert (df["promo_quantity_sold"] > 0).all()
    assert (df["promo_quantity_lift"] > 0).all()
    # lift = baseline * uniform(0.15, 0.45) <= baseline, i.e. lift < promo_quantity_sold.
    assert (df["promo_quantity_lift"] < df["promo_quantity_sold"]).all()


def test_fact_promo_performance_empty_participation_returns_correctly_shaped_empty_frame(built):
    empty_participation = built["promo_participation"].iloc[0:0]
    config = Config()
    result = facts.gen_fact_promo_performance(config, empty_participation, built["dim_store"])
    assert result.empty
    assert list(result.columns) == [
        "date_key", "promo_calendar_key", "product_key", "store_key",
        "promotion_key", "promo_quantity_sold", "promo_quantity_lift",
    ]


def test_fact_ad_performance_clicks_never_exceed_impressions(built):
    df = built["fact_ad_performance"]
    assert not df.empty
    assert (df["ad_spend_amount"] > 0).all()
    assert (df["impressions_count"] > 0).all()
    assert (df["clicks_or_coupon_clips_count"] <= df["impressions_count"]).all()


def test_fact_ad_performance_empty_participation_returns_correctly_shaped_empty_frame(built):
    empty_participation = built["ad_participation"].iloc[0:0]
    config = Config()
    result = facts.gen_fact_ad_performance(config, empty_participation, built["dim_ad_placement"], built["dim_ad_channel"])
    assert result.empty
    assert list(result.columns) == [
        "date_key", "ad_placement_key", "product_key", "store_key",
        "ad_spend_amount", "impressions_count", "clicks_or_coupon_clips_count",
    ]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_fact_generation_is_deterministic_given_seed(built, tiny_config):
    """Re-running gen_fact_item_prices with the same seeded inputs must
    reproduce byte-identical output -- the whole reproducibility promise
    (`--seed` for consistent test data) rests on every generator being a
    pure function of Config + upstream frames.
    """
    again = facts.gen_fact_item_prices(
        tiny_config,
        built["dim_date"],
        built["dim_product"],
        built["dim_store"],
        built["store_assortment"],
        built["promo_participation"],
    )
    assert again.equals(built["fact_item_prices"])


# ---------------------------------------------------------------------------
# Degenerate inputs: the guards that keep a sparse calendar from producing
# rows dated outside it, or from crashing on an empty concat.
# ---------------------------------------------------------------------------


PROMO_PARTICIPATION_COLUMNS = [
    "promotion_key", "promo_calendar_key", "product_key", "store_key",
    "date_key", "calendar_date", "fiscal_year", "fiscal_week_num", "discount_pct",
]
AD_PARTICIPATION_COLUMNS = [
    "ad_placement_key", "product_key", "store_key", "date_key", "calendar_date",
]


def test_promo_participation_skips_cycles_that_fall_outside_the_calendar(tiny_config, built):
    """A promo cycle whose window lies outside dim_date contributes nothing.
    Without the guard the cross-join would emit rows dated to days the calendar
    does not contain, and every downstream fact joined on date_key would
    silently gain orphans.
    """
    far_future = pd.DataFrame([
        {
            "promo_calendar_key": 1,
            "promo_cycle_id": "PC0001",
            # date, not Timestamp: dim_date.calendar_date holds datetime.date,
            # and the two do not compare.
            "cycle_start_date": date(1990, 1, 1),
            "cycle_end_date": date(1990, 1, 31),
        }
    ])
    result = facts.build_promo_participation(
        tiny_config, built["dim_date"], far_future,
        built["dim_promotion"], built["dim_product"], built["dim_store"],
    )
    assert result.empty
    # Shape still has to be right: gen_fact_promo_performance reads these
    # columns off the frame whether or not it has rows in it.
    assert list(result.columns) == PROMO_PARTICIPATION_COLUMNS


def test_promo_participation_with_no_promotions_returns_a_shaped_empty_frame(tiny_config, built):
    result = facts.build_promo_participation(
        tiny_config, built["dim_date"], built["dim_promo_calendar"],
        built["dim_promotion"].iloc[0:0], built["dim_product"], built["dim_store"],
    )
    assert result.empty
    assert list(result.columns) == PROMO_PARTICIPATION_COLUMNS


def test_ad_participation_with_no_placements_returns_a_shaped_empty_frame(tiny_config, built):
    result = facts.build_ad_participation(
        tiny_config, built["dim_date"], built["dim_ad_placement"].iloc[0:0],
        built["dim_product"], built["dim_store"],
    )
    assert result.empty
    assert list(result.columns) == AD_PARTICIPATION_COLUMNS


def test_ad_participation_never_invents_dates_the_calendar_does_not_have(tiny_config, built):
    """Flight windows are derived from dim_date's own span, so a calendar with
    gaps can place a whole flight inside one. Those flights drop out rather
    than producing undated rows.
    """
    calendar = built["dim_date"]
    sparse = calendar[calendar["calendar_date"].isin(
        [calendar["calendar_date"].min(), calendar["calendar_date"].max()]
    )]
    assert len(sparse) == 2, "expected exactly the two endpoint days"

    result = facts.build_ad_participation(
        tiny_config, sparse, built["dim_ad_placement"], built["dim_product"], built["dim_store"],
    )
    assert list(result.columns) == AD_PARTICIPATION_COLUMNS
    assert set(result["calendar_date"]) <= set(sparse["calendar_date"])
