"""Post-generation checks that the produced tables actually satisfy the
constraints declared in ddl.sql: primary key uniqueness and foreign key
referential integrity. Raises AssertionError with a descriptive message
on the first violation found; prints a one-line OK per table otherwise.
"""

from __future__ import annotations

import pandas as pd

PRIMARY_KEYS: dict[str, list[str]] = {
    "dim_date": ["date_key"],
    "dim_promo_calendar": ["promo_calendar_key"],
    "dim_product": ["product_key"],
    "dim_store": ["store_key"],
    "dim_competitor": ["competitor_key"],
    "dim_promotion": ["promotion_key"],
    "dim_ad_channel": ["ad_channel_key"],
    "dim_ad_placement": ["ad_placement_key"],
    "dim_vendor": ["vendor_key"],
    "dim_allowance_type": ["allowance_type_key"],
    "dim_geography": ["market_region_key"],
    "fact_pos_retail_sales": ["sales_date_key", "product_key", "store_key", "basket_id"],
    "fact_item_cogs": ["date_key", "product_key", "store_key"],
    "fact_vendor_allowances": ["date_key", "product_key", "store_key", "vendor_key", "allowance_type_key"],
    "fact_item_prices": ["date_key", "product_key", "store_key"],
    "fact_competitor_pricing": ["date_key", "product_key", "competitor_key", "store_key"],
    "fact_market_share_weekly": ["week_key", "product_key", "market_region_key", "competitor_key"],
    "fact_promo_performance": ["date_key", "promo_calendar_key", "product_key", "store_key", "promotion_key"],
    "fact_ad_performance": ["date_key", "ad_placement_key", "product_key", "store_key"],
}

# (table, fk_column) -> (referenced_table, referenced_column)
FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    ("dim_ad_placement", "ad_channel_key", "dim_ad_channel", "ad_channel_key"),
    ("fact_pos_retail_sales", "sales_date_key", "dim_date", "date_key"),
    ("fact_pos_retail_sales", "product_key", "dim_product", "product_key"),
    ("fact_pos_retail_sales", "store_key", "dim_store", "store_key"),
    ("fact_item_cogs", "date_key", "dim_date", "date_key"),
    ("fact_item_cogs", "product_key", "dim_product", "product_key"),
    ("fact_item_cogs", "store_key", "dim_store", "store_key"),
    ("fact_vendor_allowances", "date_key", "dim_date", "date_key"),
    ("fact_vendor_allowances", "product_key", "dim_product", "product_key"),
    ("fact_vendor_allowances", "store_key", "dim_store", "store_key"),
    ("fact_vendor_allowances", "vendor_key", "dim_vendor", "vendor_key"),
    ("fact_vendor_allowances", "allowance_type_key", "dim_allowance_type", "allowance_type_key"),
    ("fact_item_prices", "date_key", "dim_date", "date_key"),
    ("fact_item_prices", "product_key", "dim_product", "product_key"),
    ("fact_item_prices", "store_key", "dim_store", "store_key"),
    ("fact_competitor_pricing", "date_key", "dim_date", "date_key"),
    ("fact_competitor_pricing", "product_key", "dim_product", "product_key"),
    ("fact_competitor_pricing", "competitor_key", "dim_competitor", "competitor_key"),
    ("fact_competitor_pricing", "store_key", "dim_store", "store_key"),
    ("fact_market_share_weekly", "week_key", "dim_date", "date_key"),
    ("fact_market_share_weekly", "product_key", "dim_product", "product_key"),
    ("fact_market_share_weekly", "market_region_key", "dim_geography", "market_region_key"),
    ("fact_market_share_weekly", "competitor_key", "dim_competitor", "competitor_key"),
    ("fact_promo_performance", "date_key", "dim_date", "date_key"),
    ("fact_promo_performance", "promo_calendar_key", "dim_promo_calendar", "promo_calendar_key"),
    ("fact_promo_performance", "product_key", "dim_product", "product_key"),
    ("fact_promo_performance", "store_key", "dim_store", "store_key"),
    ("fact_promo_performance", "promotion_key", "dim_promotion", "promotion_key"),
    ("fact_ad_performance", "date_key", "dim_date", "date_key"),
    ("fact_ad_performance", "ad_placement_key", "dim_ad_placement", "ad_placement_key"),
    ("fact_ad_performance", "product_key", "dim_product", "product_key"),
    ("fact_ad_performance", "store_key", "dim_store", "store_key"),
]


def validate(tables: dict[str, pd.DataFrame], verbose: bool = True) -> None:
    for table, cols in PRIMARY_KEYS.items():
        df = tables[table]
        dupes = df.duplicated(subset=cols).sum()
        assert dupes == 0, f"{table}: {dupes} duplicate rows on PK {cols}"
        if verbose:
            print(f"  OK  {table}: {len(df):>9,} rows, PK({','.join(cols)}) unique")

    for table, fk_col, ref_table, ref_col in FOREIGN_KEYS:
        df = tables[table]
        ref = tables[ref_table]
        orphans = ~df[fk_col].isin(ref[ref_col])
        n_orphans = int(orphans.sum())
        assert n_orphans == 0, (
            f"{table}.{fk_col}: {n_orphans} rows reference a missing "
            f"{ref_table}.{ref_col}"
        )
    if verbose:
        print(f"  OK  all {len(FOREIGN_KEYS)} foreign key relationships intact")
