-- ============================================================================
-- 1. DUAL CALENDAR DIMENSIONS
-- ============================================================================

-- Corporate Financial & Operational Calendar
CREATE TABLE dim_date (
    date_key INT PRIMARY KEY, -- Format: YYYYMMDD
    calendar_date DATE NOT NULL UNIQUE,
    day_of_week_name VARCHAR(9) NOT NULL,
    fiscal_week_num INT NOT NULL,
    fiscal_month_num INT NOT NULL,
    fiscal_quarter INT NOT NULL,
    fiscal_year INT NOT NULL,
    nrf_454_week_num INT,
    is_holiday BOOLEAN NOT NULL DEFAULT FALSE
);

-- Decoupled Marketing & Merchandising Promotion Calendar
CREATE TABLE dim_promo_calendar (
    promo_calendar_key SERIAL PRIMARY KEY,
    promo_cycle_id VARCHAR(50) NOT NULL UNIQUE, -- e.g., "PROMO_2026_WK32_FALL"
    promo_cycle_name VARCHAR(150) NOT NULL,    -- e.g., "Back to School Blast Phase 1"
    promo_season_type VARCHAR(50) NOT NULL,    -- e.g., "Holiday", "Summer Grilling", "Lent"
    cycle_start_date DATE NOT NULL,
    cycle_end_date DATE NOT NULL,
    is_major_event_cycle BOOLEAN NOT NULL DEFAULT FALSE
);

-- ============================================================================
-- 2. CORE CONFORMED DIMENSIONS
-- ============================================================================

CREATE TABLE dim_product (
    product_key SERIAL PRIMARY KEY,
    sku_id VARCHAR(50) NOT NULL UNIQUE,
    upc_barcode VARCHAR(14),
    product_name VARCHAR(255) NOT NULL,
    brand_name VARCHAR(100) NOT NULL,
    department_name VARCHAR(100) NOT NULL,
    category_name VARCHAR(100) NOT NULL,
    sub_category_name VARCHAR(100) NOT NULL,
    package_size_desc VARCHAR(50),
    is_private_label BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE dim_store (
    store_key SERIAL PRIMARY KEY,
    store_id VARCHAR(20) NOT NULL UNIQUE,
    store_name VARCHAR(150) NOT NULL,
    banner_name VARCHAR(100) NOT NULL,
    street_address VARCHAR(255),
    city VARCHAR(100),
    state_code CHAR(2),
    postal_code VARCHAR(12),
    square_footage INT,
    layout_type_desc VARCHAR(50)
);

CREATE TABLE dim_competitor (
    competitor_key SERIAL PRIMARY KEY,
    competitor_id VARCHAR(20) NOT NULL UNIQUE,
    competitor_name VARCHAR(150) NOT NULL,
    banner_name VARCHAR(100) NOT NULL,
    market_positioning VARCHAR(50)
);

CREATE TABLE dim_promotion (
    promotion_key SERIAL PRIMARY KEY,
    promotion_id VARCHAR(50) NOT NULL UNIQUE,
    promotion_name VARCHAR(150) NOT NULL,
    mechanic_type VARCHAR(50) NOT NULL, -- e.g., "BOGO", "Mix-and-Match", "Loyalty Price Drop"
    min_purchase_requirement INT DEFAULT 1
);

CREATE TABLE dim_ad_channel (
    ad_channel_key SERIAL PRIMARY KEY,
    channel_id VARCHAR(20) NOT NULL UNIQUE,
    channel_type VARCHAR(50) NOT NULL, -- e.g., "Print Flyer", "Digital Mailer", "Paid Social"
    medium_platform VARCHAR(100)       -- e.g., "Sunday Paper Insert", "Instagram", "In-App"
);

-- New: Structural Advertisement Metadata Table (Separated from Performance)
CREATE TABLE dim_ad_placement (
    ad_placement_key SERIAL PRIMARY KEY,
    ad_id VARCHAR(50) NOT NULL UNIQUE,
    ad_channel_key INT NOT NULL REFERENCES dim_ad_channel(ad_channel_key),
    ad_theme_name VARCHAR(150) NOT NULL,     -- e.g., "Labor Day Ribeye Extravaganza"
    creative_version_code VARCHAR(50),       -- Tracks creative variant for A/B testing
    page_number_slot VARCHAR(20),            -- e.g., "Page 1 - Top Left", "Mobile Banner 1"
    is_front_page_feature BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE dim_vendor (
    vendor_key SERIAL PRIMARY KEY,
    vendor_id VARCHAR(50) NOT NULL UNIQUE,
    vendor_name VARCHAR(150) NOT NULL,
    payment_terms_desc VARCHAR(50)
);

CREATE TABLE dim_allowance_type (
    allowance_type_key SERIAL PRIMARY KEY,
    allowance_type_code VARCHAR(30) NOT NULL UNIQUE,
    allowance_type_name VARCHAR(100) NOT NULL
);

CREATE TABLE dim_geography (
    market_region_key SERIAL PRIMARY KEY,
    region_id VARCHAR(50) NOT NULL UNIQUE,
    region_name VARCHAR(100) NOT NULL,
    syndicated_market_code VARCHAR(50)
);


-- ============================================================================
-- 3. SALES AND COST DOMAIN
-- ============================================================================

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
);

CREATE TABLE fact_item_cogs (
    date_key INT NOT NULL REFERENCES dim_date(date_key),
    product_key INT NOT NULL REFERENCES dim_product(product_key),
    store_key INT NOT NULL REFERENCES dim_store(store_key),
    base_cost NUMERIC(12,4) NOT NULL,
    freight_cost NUMERIC(12,4) NOT NULL DEFAULT 0.0000,
    net_item_cost NUMERIC(12,4) NOT NULL,
    PRIMARY KEY (date_key, product_key, store_key)
);

CREATE TABLE fact_vendor_allowances (
    date_key INT NOT NULL REFERENCES dim_date(date_key),
    product_key INT NOT NULL REFERENCES dim_product(product_key),
    store_key INT NOT NULL REFERENCES dim_store(store_key),
    vendor_key INT NOT NULL REFERENCES dim_vendor(vendor_key),
    allowance_type_key INT NOT NULL REFERENCES dim_allowance_type(allowance_type_key),
    allowance_rate_per_unit NUMERIC(12,4) NOT NULL DEFAULT 0.0000,
    total_allowance_amt NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (date_key, product_key, store_key, vendor_key, allowance_type_key)
);


-- ============================================================================
-- 4. SHARED PRICING LAYER BRIDGE
-- ============================================================================

CREATE TABLE fact_item_prices (
    date_key INT NOT NULL REFERENCES dim_date(date_key),
    product_key INT NOT NULL REFERENCES dim_product(product_key),
    store_key INT NOT NULL REFERENCES dim_store(store_key),
    regular_retail_price NUMERIC(12,2) NOT NULL,
    base_promo_price NUMERIC(12,2),
    PRIMARY KEY (date_key, product_key, store_key)
);


-- ============================================================================
-- 5. COMPETITION AND MARKET SHARE DOMAIN
-- ============================================================================

CREATE TABLE fact_competitor_pricing (
    date_key INT NOT NULL REFERENCES dim_date(date_key),
    product_key INT NOT NULL REFERENCES dim_product(product_key),
    competitor_key INT NOT NULL REFERENCES dim_competitor(competitor_key),
    store_key INT NOT NULL REFERENCES dim_store(store_key),
    comp_regular_price NUMERIC(12,2) NOT NULL,
    comp_promo_price NUMERIC(12,2),
    PRIMARY KEY (date_key, product_key, competitor_key, store_key)
);

CREATE TABLE fact_market_share_weekly (
    week_key INT NOT NULL REFERENCES dim_date(date_key), 
    product_key INT NOT NULL REFERENCES dim_product(product_key),
    market_region_key INT NOT NULL REFERENCES dim_geography(market_region_key),
    competitor_key INT NOT NULL REFERENCES dim_competitor(competitor_key),
    grocer_sales_amount NUMERIC(15,2) NOT NULL,
    competitor_sales_amount NUMERIC(15,2) NOT NULL,
    total_market_sales_amount NUMERIC(15,2) NOT NULL,
    PRIMARY KEY (week_key, product_key, market_region_key, competitor_key)
);


-- ============================================================================
-- 6. PROMOTIONS AND ADS DOMAIN (UPDATED MECHANICS)
-- ============================================================================

-- Promotion Performance linked simultaneously to Corporate Dates and Promotion Cycles
CREATE TABLE fact_promo_performance (
    date_key INT NOT NULL REFERENCES dim_date(date_key),
    promo_calendar_key INT NOT NULL REFERENCES dim_promo_calendar(promo_calendar_key),
    product_key INT NOT NULL REFERENCES dim_product(product_key),
    store_key INT NOT NULL REFERENCES dim_store(store_key),
    promotion_key INT NOT NULL REFERENCES dim_promotion(promotion_key),
    promo_quantity_sold NUMERIC(12,3) NOT NULL,
    promo_quantity_lift NUMERIC(12,3) NOT NULL,
    PRIMARY KEY (date_key, promo_calendar_key, product_key, store_key, promotion_key)
);

-- Separated Ad Performance Fact capturing granular metric performance rows over time
CREATE TABLE fact_ad_performance (
    date_key INT NOT NULL REFERENCES dim_date(date_key),
    ad_placement_key INT NOT NULL REFERENCES dim_ad_placement(ad_placement_key),
    product_key INT NOT NULL REFERENCES dim_product(product_key),
    store_key INT NOT NULL REFERENCES dim_store(store_key),
    ad_spend_amount NUMERIC(12,2) NOT NULL DEFAULT 0.00, -- Localized/daily split of the ad spend
    impressions_count INT NOT NULL DEFAULT 0,
    clicks_or_coupon_clips_count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (date_key, ad_placement_key, product_key, store_key)
);


-- ============================================================================
-- 7. PERFORMANCE AND REPORTING INDEXES
-- ============================================================================

-- Accelerates promotional lift analytics comparing financial date and promotional tracking structures
CREATE INDEX idx_promo_perf_calendars ON fact_promo_performance (date_key, promo_calendar_key);

-- Accelerates ad response analytics matching physical placements across specific stores
CREATE INDEX idx_ad_perf_lookup ON fact_ad_performance (ad_placement_key, store_key);

