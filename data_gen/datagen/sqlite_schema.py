"""SQLite-flavored translation of ddl.sql, for the optional --sqlite output.

This is a mechanical port (SERIAL -> INTEGER, everything else is valid
SQLite as-is since it ignores VARCHAR/NUMERIC precision and has no
native BOOLEAN/DATE types but accepts the type names for column
affinity). Table order matches schema_columns.TABLE_ORDER so foreign
keys always reference an already-created table.
"""

from __future__ import annotations

CREATE_STATEMENTS: dict[str, str] = {
    "dim_date": """
        CREATE TABLE dim_date (
            date_key INTEGER PRIMARY KEY,
            calendar_date DATE NOT NULL UNIQUE,
            day_of_week_name VARCHAR(9) NOT NULL,
            fiscal_week_num INT NOT NULL,
            fiscal_month_num INT NOT NULL,
            fiscal_quarter INT NOT NULL,
            fiscal_year INT NOT NULL,
            nrf_454_week_num INT,
            is_holiday BOOLEAN NOT NULL DEFAULT 0
        )
    """,
    "dim_promo_calendar": """
        CREATE TABLE dim_promo_calendar (
            promo_calendar_key INTEGER PRIMARY KEY,
            promo_cycle_id VARCHAR(50) NOT NULL UNIQUE,
            promo_cycle_name VARCHAR(150) NOT NULL,
            promo_season_type VARCHAR(50) NOT NULL,
            cycle_start_date DATE NOT NULL,
            cycle_end_date DATE NOT NULL,
            is_major_event_cycle BOOLEAN NOT NULL DEFAULT 0
        )
    """,
    "dim_product": """
        CREATE TABLE dim_product (
            product_key INTEGER PRIMARY KEY,
            sku_id VARCHAR(50) NOT NULL UNIQUE,
            upc_barcode VARCHAR(14),
            product_name VARCHAR(255) NOT NULL,
            brand_name VARCHAR(100) NOT NULL,
            department_name VARCHAR(100) NOT NULL,
            category_name VARCHAR(100) NOT NULL,
            sub_category_name VARCHAR(100) NOT NULL,
            package_size_desc VARCHAR(50),
            is_private_label BOOLEAN NOT NULL DEFAULT 0
        )
    """,
    "dim_store": """
        CREATE TABLE dim_store (
            store_key INTEGER PRIMARY KEY,
            store_id VARCHAR(20) NOT NULL UNIQUE,
            store_name VARCHAR(150) NOT NULL,
            banner_name VARCHAR(100) NOT NULL,
            street_address VARCHAR(255),
            city VARCHAR(100),
            state_code CHAR(2),
            postal_code VARCHAR(12),
            square_footage INT,
            layout_type_desc VARCHAR(50)
        )
    """,
    "dim_competitor": """
        CREATE TABLE dim_competitor (
            competitor_key INTEGER PRIMARY KEY,
            competitor_id VARCHAR(20) NOT NULL UNIQUE,
            competitor_name VARCHAR(150) NOT NULL,
            banner_name VARCHAR(100) NOT NULL,
            market_positioning VARCHAR(50)
        )
    """,
    "dim_promotion": """
        CREATE TABLE dim_promotion (
            promotion_key INTEGER PRIMARY KEY,
            promotion_id VARCHAR(50) NOT NULL UNIQUE,
            promotion_name VARCHAR(150) NOT NULL,
            mechanic_type VARCHAR(50) NOT NULL,
            min_purchase_requirement INT DEFAULT 1
        )
    """,
    "dim_ad_channel": """
        CREATE TABLE dim_ad_channel (
            ad_channel_key INTEGER PRIMARY KEY,
            channel_id VARCHAR(20) NOT NULL UNIQUE,
            channel_type VARCHAR(50) NOT NULL,
            medium_platform VARCHAR(100)
        )
    """,
    "dim_ad_placement": """
        CREATE TABLE dim_ad_placement (
            ad_placement_key INTEGER PRIMARY KEY,
            ad_id VARCHAR(50) NOT NULL UNIQUE,
            ad_channel_key INT NOT NULL REFERENCES dim_ad_channel(ad_channel_key),
            ad_theme_name VARCHAR(150) NOT NULL,
            creative_version_code VARCHAR(50),
            page_number_slot VARCHAR(20),
            is_front_page_feature BOOLEAN NOT NULL DEFAULT 0
        )
    """,
    "dim_vendor": """
        CREATE TABLE dim_vendor (
            vendor_key INTEGER PRIMARY KEY,
            vendor_id VARCHAR(50) NOT NULL UNIQUE,
            vendor_name VARCHAR(150) NOT NULL,
            payment_terms_desc VARCHAR(50)
        )
    """,
    "dim_allowance_type": """
        CREATE TABLE dim_allowance_type (
            allowance_type_key INTEGER PRIMARY KEY,
            allowance_type_code VARCHAR(30) NOT NULL UNIQUE,
            allowance_type_name VARCHAR(100) NOT NULL
        )
    """,
    "dim_geography": """
        CREATE TABLE dim_geography (
            market_region_key INTEGER PRIMARY KEY,
            region_id VARCHAR(50) NOT NULL UNIQUE,
            region_name VARCHAR(100) NOT NULL,
            syndicated_market_code VARCHAR(50)
        )
    """,
    "fact_pos_retail_sales": """
        CREATE TABLE fact_pos_retail_sales (
            sales_date_key INT NOT NULL REFERENCES dim_date(date_key),
            product_key INT NOT NULL REFERENCES dim_product(product_key),
            store_key INT NOT NULL REFERENCES dim_store(store_key),
            basket_id VARCHAR(64) NOT NULL,
            quantity_sold NUMERIC(12,3) NOT NULL,
            gross_sales_amt NUMERIC(12,2) NOT NULL,
            markdown_discount_amt NUMERIC(12,2) NOT NULL DEFAULT 0.00,
            net_sales_amt NUMERIC(12,2) NOT NULL,
            PRIMARY KEY (sales_date_key, product_key, store_key, basket_id)
        )
    """,
    "fact_item_cogs": """
        CREATE TABLE fact_item_cogs (
            date_key INT NOT NULL REFERENCES dim_date(date_key),
            product_key INT NOT NULL REFERENCES dim_product(product_key),
            store_key INT NOT NULL REFERENCES dim_store(store_key),
            base_cost NUMERIC(12,4) NOT NULL,
            freight_cost NUMERIC(12,4) NOT NULL DEFAULT 0.0000,
            net_item_cost NUMERIC(12,4) NOT NULL,
            PRIMARY KEY (date_key, product_key, store_key)
        )
    """,
    "fact_vendor_allowances": """
        CREATE TABLE fact_vendor_allowances (
            date_key INT NOT NULL REFERENCES dim_date(date_key),
            product_key INT NOT NULL REFERENCES dim_product(product_key),
            store_key INT NOT NULL REFERENCES dim_store(store_key),
            vendor_key INT NOT NULL REFERENCES dim_vendor(vendor_key),
            allowance_type_key INT NOT NULL REFERENCES dim_allowance_type(allowance_type_key),
            allowance_rate_per_unit NUMERIC(12,4) NOT NULL DEFAULT 0.0000,
            total_allowance_amt NUMERIC(12,2) NOT NULL,
            PRIMARY KEY (date_key, product_key, store_key, vendor_key, allowance_type_key)
        )
    """,
    "fact_item_prices": """
        CREATE TABLE fact_item_prices (
            date_key INT NOT NULL REFERENCES dim_date(date_key),
            product_key INT NOT NULL REFERENCES dim_product(product_key),
            store_key INT NOT NULL REFERENCES dim_store(store_key),
            regular_retail_price NUMERIC(12,2) NOT NULL,
            base_promo_price NUMERIC(12,2),
            PRIMARY KEY (date_key, product_key, store_key)
        )
    """,
    "fact_competitor_pricing": """
        CREATE TABLE fact_competitor_pricing (
            date_key INT NOT NULL REFERENCES dim_date(date_key),
            product_key INT NOT NULL REFERENCES dim_product(product_key),
            competitor_key INT NOT NULL REFERENCES dim_competitor(competitor_key),
            store_key INT NOT NULL REFERENCES dim_store(store_key),
            comp_regular_price NUMERIC(12,2) NOT NULL,
            comp_promo_price NUMERIC(12,2),
            PRIMARY KEY (date_key, product_key, competitor_key, store_key)
        )
    """,
    "fact_market_share_weekly": """
        CREATE TABLE fact_market_share_weekly (
            week_key INT NOT NULL REFERENCES dim_date(date_key),
            product_key INT NOT NULL REFERENCES dim_product(product_key),
            market_region_key INT NOT NULL REFERENCES dim_geography(market_region_key),
            competitor_key INT NOT NULL REFERENCES dim_competitor(competitor_key),
            grocer_sales_amount NUMERIC(15,2) NOT NULL,
            competitor_sales_amount NUMERIC(15,2) NOT NULL,
            total_market_sales_amount NUMERIC(15,2) NOT NULL,
            PRIMARY KEY (week_key, product_key, market_region_key, competitor_key)
        )
    """,
    "fact_promo_performance": """
        CREATE TABLE fact_promo_performance (
            date_key INT NOT NULL REFERENCES dim_date(date_key),
            promo_calendar_key INT NOT NULL REFERENCES dim_promo_calendar(promo_calendar_key),
            product_key INT NOT NULL REFERENCES dim_product(product_key),
            store_key INT NOT NULL REFERENCES dim_store(store_key),
            promotion_key INT NOT NULL REFERENCES dim_promotion(promotion_key),
            promo_quantity_sold NUMERIC(12,3) NOT NULL,
            promo_quantity_lift NUMERIC(12,3) NOT NULL,
            PRIMARY KEY (date_key, promo_calendar_key, product_key, store_key, promotion_key)
        )
    """,
    "fact_ad_performance": """
        CREATE TABLE fact_ad_performance (
            date_key INT NOT NULL REFERENCES dim_date(date_key),
            ad_placement_key INT NOT NULL REFERENCES dim_ad_placement(ad_placement_key),
            product_key INT NOT NULL REFERENCES dim_product(product_key),
            store_key INT NOT NULL REFERENCES dim_store(store_key),
            ad_spend_amount NUMERIC(12,2) NOT NULL DEFAULT 0.00,
            impressions_count INT NOT NULL DEFAULT 0,
            clicks_or_coupon_clips_count INT NOT NULL DEFAULT 0,
            PRIMARY KEY (date_key, ad_placement_key, product_key, store_key)
        )
    """,
}

INDEX_STATEMENTS: list[str] = [
    "CREATE INDEX idx_promo_perf_calendars ON fact_promo_performance (date_key, promo_calendar_key)",
    "CREATE INDEX idx_ad_perf_lookup ON fact_ad_performance (ad_placement_key, store_key)",
]
