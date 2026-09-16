"""Authoritative column order per table, taken directly from ddl.sql.

Generation modules are free to carry extra working columns on their
DataFrames (e.g. an internal price anchor or unit-of-sale flag used only
to keep other facts consistent); writer.py slices down to exactly these
columns, in this order, before anything is persisted.
"""

TABLE_COLUMNS: dict[str, list[str]] = {
    "dim_date": [
        "date_key", "calendar_date", "day_of_week_name", "fiscal_week_num",
        "fiscal_month_num", "fiscal_quarter", "fiscal_year", "nrf_454_week_num",
        "is_holiday",
    ],
    "dim_promo_calendar": [
        "promo_calendar_key", "promo_cycle_id", "promo_cycle_name",
        "promo_season_type", "cycle_start_date", "cycle_end_date",
        "is_major_event_cycle",
    ],
    "dim_product": [
        "product_key", "sku_id", "upc_barcode", "product_name", "brand_name",
        "department_name", "category_name", "sub_category_name",
        "package_size_desc", "is_private_label",
    ],
    "dim_store": [
        "store_key", "store_id", "store_name", "banner_name", "street_address",
        "city", "state_code", "postal_code", "square_footage", "layout_type_desc",
    ],
    "dim_competitor": [
        "competitor_key", "competitor_id", "competitor_name", "banner_name",
        "market_positioning",
    ],
    "dim_promotion": [
        "promotion_key", "promotion_id", "promotion_name", "mechanic_type",
        "min_purchase_requirement",
    ],
    "dim_ad_channel": [
        "ad_channel_key", "channel_id", "channel_type", "medium_platform",
    ],
    "dim_ad_placement": [
        "ad_placement_key", "ad_id", "ad_channel_key", "ad_theme_name",
        "creative_version_code", "page_number_slot", "is_front_page_feature",
    ],
    "dim_vendor": [
        "vendor_key", "vendor_id", "vendor_name", "payment_terms_desc",
    ],
    "dim_allowance_type": [
        "allowance_type_key", "allowance_type_code", "allowance_type_name",
    ],
    "dim_geography": [
        "market_region_key", "region_id", "region_name", "syndicated_market_code",
    ],
    "fact_pos_retail_sales": [
        "sales_date_key", "product_key", "store_key", "basket_id",
        "quantity_sold", "gross_sales_amt", "markdown_discount_amt", "net_sales_amt",
    ],
    "fact_item_cogs": [
        "date_key", "product_key", "store_key", "base_cost", "freight_cost",
        "net_item_cost",
    ],
    "fact_vendor_allowances": [
        "date_key", "product_key", "store_key", "vendor_key", "allowance_type_key",
        "allowance_rate_per_unit", "total_allowance_amt",
    ],
    "fact_item_prices": [
        "date_key", "product_key", "store_key", "regular_retail_price",
        "base_promo_price",
    ],
    "fact_competitor_pricing": [
        "date_key", "product_key", "competitor_key", "store_key",
        "comp_regular_price", "comp_promo_price",
    ],
    "fact_market_share_weekly": [
        "week_key", "product_key", "market_region_key", "competitor_key",
        "grocer_sales_amount", "competitor_sales_amount", "total_market_sales_amount",
    ],
    "fact_promo_performance": [
        "date_key", "promo_calendar_key", "product_key", "store_key",
        "promotion_key", "promo_quantity_sold", "promo_quantity_lift",
    ],
    "fact_ad_performance": [
        "date_key", "ad_placement_key", "product_key", "store_key",
        "ad_spend_amount", "impressions_count", "clicks_or_coupon_clips_count",
    ],
}

# Load order that respects FK dependencies (dims before facts, referenced
# dims before dependent dims).
TABLE_ORDER: list[str] = [
    "dim_date", "dim_promo_calendar", "dim_product", "dim_store",
    "dim_competitor", "dim_promotion", "dim_ad_channel", "dim_ad_placement",
    "dim_vendor", "dim_allowance_type", "dim_geography",
    "fact_pos_retail_sales", "fact_item_cogs", "fact_vendor_allowances",
    "fact_item_prices", "fact_competitor_pricing", "fact_market_share_weekly",
    "fact_promo_performance", "fact_ad_performance",
]
