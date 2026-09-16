"""Config dataclass: calendar span derivation and the scale() knob.

scaled() is the one place with real logic in an otherwise plain settings
object: only some fields grow with --scale, and it floors every scaled count
at 1. That behavior is easy to break silently, so it's pinned explicitly
here rather than left to be noticed via a generation-pipeline crash.
"""

from __future__ import annotations

from datetime import date

from datagen.config import Config


def test_fiscal_year_range_single_year():
    config = Config(first_fiscal_year=2024, num_fiscal_years=1)
    assert list(config.fiscal_year_range) == [2024]


def test_fiscal_year_range_multi_year():
    config = Config(first_fiscal_year=2024, num_fiscal_years=3)
    assert list(config.fiscal_year_range) == [2024, 2025, 2026]


def test_start_and_end_date_single_year():
    # FY2024 runs 2023-04-01 .. 2024-03-31 (fiscal year Y starts April 1 of Y-1).
    config = Config(first_fiscal_year=2024, num_fiscal_years=1)
    assert config.start_date == date(2023, 4, 1)
    assert config.end_date == date(2024, 3, 31)


def test_start_and_end_date_multi_year():
    config = Config(first_fiscal_year=2024, num_fiscal_years=2)
    assert config.start_date == date(2023, 4, 1)
    assert config.end_date == date(2025, 3, 31)


def test_scaled_grows_cardinality_knobs():
    config = Config(n_stores=10, n_products=200, n_vendors=30, n_promotions=40, n_ad_placements=90)
    scaled = config.scaled(2.0)
    assert scaled.n_stores == 20
    assert scaled.n_products == 400
    assert scaled.n_vendors == 60
    assert scaled.n_promotions == 80
    assert scaled.n_ad_placements == 180


def test_scaled_leaves_reference_pool_backed_counts_alone():
    # These are bounded by fixed reference-data pools (COMPETITORS,
    # MARKET_REGIONS, AD_CHANNELS), not by generation volume, so scale must
    # not touch them.
    config = Config(n_competitors=5, n_market_regions=8, n_ad_channels=7)
    scaled = config.scaled(3.0)
    assert scaled.n_competitors == 5
    assert scaled.n_market_regions == 8
    assert scaled.n_ad_channels == 7


def test_scaled_only_scales_baskets_range_not_other_ranges():
    config = Config()
    scaled = config.scaled(2.0)
    assert scaled.baskets_per_store_day_range == tuple(2 * v for v in config.baskets_per_store_day_range)
    # These ranges/rates are left exactly as-is by scaled().
    assert scaled.items_per_basket_range == config.items_per_basket_range
    assert scaled.promo_products_per_event_range == config.promo_products_per_event_range
    assert scaled.ad_products_per_placement_range == config.ad_products_per_placement_range
    assert scaled.promo_cycle_length_days_range == config.promo_cycle_length_days_range
    assert scaled.ad_flight_length_days_range == config.ad_flight_length_days_range
    assert scaled.store_assortment_range == config.store_assortment_range
    assert scaled.audit_store_fraction == config.audit_store_fraction
    assert scaled.market_share_product_fraction == config.market_share_product_fraction


def test_scaled_floors_counts_at_one():
    config = Config(n_stores=10)
    scaled = config.scaled(0.01)
    assert scaled.n_stores == 1


def test_scaled_floors_range_bounds_at_one():
    config = Config(baskets_per_store_day_range=(15, 45))
    scaled = config.scaled(0.01)
    lo, hi = scaled.baskets_per_store_day_range
    assert lo >= 1
    assert hi >= 1


def test_scaled_preserves_seed_and_calendar():
    config = Config(seed=99, first_fiscal_year=2030, num_fiscal_years=5, output_dir="custom")
    scaled = config.scaled(2.0)
    assert scaled.seed == 99
    assert scaled.first_fiscal_year == 2030
    assert scaled.num_fiscal_years == 5
    assert scaled.output_dir == "custom"


def test_scaled_by_one_is_a_distinct_equal_copy():
    config = Config()
    scaled = config.scaled(1.0)
    assert scaled == config
    assert scaled is not config
