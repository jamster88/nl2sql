"""Scale and behavior knobs for the synthetic data generator.

All "how much data" decisions live here so the generation modules stay
free of magic numbers. Defaults are tuned to produce a demo-sized
dataset (runs in well under a minute, tens of MB) -- pass a larger
--scale on the CLI (see generate_data.py) to grow every count together,
or override individual fields for finer control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Config:
    seed: int = 42

    # --- Calendar -----------------------------------------------------
    # Fiscal year Y runs from April 1 of (Y-1) through March 31 of Y.
    first_fiscal_year: int = 2024
    num_fiscal_years: int = 2

    # --- Dimension cardinalities ---------------------------------------
    n_stores: int = 10
    n_products: int = 200
    n_competitors: int = 5
    n_vendors: int = 30
    n_market_regions: int = 8
    n_promotions: int = 40
    n_ad_channels: int = 7
    n_ad_placements: int = 90

    # --- Assortment / participation rates ------------------------------
    # Fraction of the full product catalog each store carries.
    store_assortment_range: tuple[float, float] = (0.75, 1.0)
    # Fraction of stores that participate in the competitor price audit
    # panel (not every store is shopped by the competitive-intelligence
    # team every week).
    audit_store_fraction: float = 0.4
    # Fraction of the catalog that is "tracked" for syndicated market
    # share reporting (usually a curated subset of key items, not
    # everything the store sells).
    market_share_product_fraction: float = 0.35

    # --- Sales volume ----------------------------------------------------
    baskets_per_store_day_range: tuple[int, int] = (15, 45)
    weekend_basket_multiplier: float = 1.5
    holiday_basket_multiplier: float = 1.8
    items_per_basket_range: tuple[int, int] = (1, 9)

    # --- Promotions & ads -------------------------------------------------
    promo_cycle_length_days_range: tuple[int, int] = (10, 21)
    promo_products_per_event_range: tuple[int, int] = (5, 25)
    ad_flight_length_days_range: tuple[int, int] = (3, 14)
    ad_products_per_placement_range: tuple[int, int] = (1, 6)

    # --- Output -----------------------------------------------------------
    output_dir: str = "output"
    write_sqlite: bool = True
    sqlite_filename: str = "nl2sql_retail.db"

    @property
    def fiscal_year_range(self) -> range:
        return range(self.first_fiscal_year, self.first_fiscal_year + self.num_fiscal_years)

    @property
    def start_date(self) -> date:
        return date(self.first_fiscal_year - 1, 4, 1)

    @property
    def end_date(self) -> date:
        last_fy = self.first_fiscal_year + self.num_fiscal_years - 1
        return date(last_fy, 3, 31)

    def scaled(self, factor: float) -> "Config":
        """Return a copy with every cardinality/volume knob scaled by factor.

        Calendar span, ratios and ranges that are already rates (0-1) are
        left untouched; only counts and per-day/per-event volumes scale.
        """

        def s_int(v: int) -> int:
            return max(1, round(v * factor))

        def s_range(v: tuple) -> tuple:
            lo, hi = v
            return (max(1, round(lo * factor)), max(1, round(hi * factor)))

        return Config(
            seed=self.seed,
            first_fiscal_year=self.first_fiscal_year,
            num_fiscal_years=self.num_fiscal_years,
            n_stores=s_int(self.n_stores),
            n_products=s_int(self.n_products),
            n_competitors=self.n_competitors,
            n_vendors=s_int(self.n_vendors),
            n_market_regions=self.n_market_regions,
            n_promotions=s_int(self.n_promotions),
            n_ad_channels=self.n_ad_channels,
            n_ad_placements=s_int(self.n_ad_placements),
            store_assortment_range=self.store_assortment_range,
            audit_store_fraction=self.audit_store_fraction,
            market_share_product_fraction=self.market_share_product_fraction,
            baskets_per_store_day_range=s_range(self.baskets_per_store_day_range),
            weekend_basket_multiplier=self.weekend_basket_multiplier,
            holiday_basket_multiplier=self.holiday_basket_multiplier,
            items_per_basket_range=self.items_per_basket_range,
            promo_cycle_length_days_range=self.promo_cycle_length_days_range,
            promo_products_per_event_range=self.promo_products_per_event_range,
            ad_flight_length_days_range=self.ad_flight_length_days_range,
            ad_products_per_placement_range=self.ad_products_per_placement_range,
            output_dir=self.output_dir,
            write_sqlite=self.write_sqlite,
            sqlite_filename=self.sqlite_filename,
        )
