# DDL Index

Table and column definitions for the `nl2sql_retail` database, one
self-contained chunk per table. Each chunk carries the full `CREATE TABLE`
statement with table and column descriptions as SQL comments, so a retrieved
chunk alone is enough to write a correct query against that table.

**Chunking:** split on `## ` headings. Each chunk is independent and repeats
whatever context it needs. The `meta` block is machine-readable metadata for
the vector store; the SQL block is the embedding payload.

Verified against the live database. Row counts are exact.

---

## dim_date

```meta
chunk_id: ddl:dim_date
table: dim_date
type: dimension
domain: calendar
grain: one row per calendar day
rows: 731
keywords: date, calendar, fiscal year, fiscal week, fiscal month, quarter, holiday, time
```

```sql
-- Corporate fiscal calendar. Fiscal year Y runs April 1 of year Y-1 through
-- March 31 of year Y, so FY2024 = 2023-04-01..2024-03-31. Covers FY2024 and
-- FY2025 (2023-04-01..2025-03-31). Weeks follow a 4-4-5 pattern: fiscal months
-- 3, 6, 9 and 12 span 5 weeks, all others span 4. Every other fact table joins
-- to this table, though the joining column is named date_key, sales_date_key
-- or week_key depending on the fact.
CREATE TABLE dim_date (
    date_key          INT PRIMARY KEY,      -- YYYYMMDD as an integer, e.g. 20230401. Range 20230401-20250331.
    calendar_date     DATE NOT NULL UNIQUE, -- The actual date.
    day_of_week_name  VARCHAR(9) NOT NULL,  -- Full English day name: Monday..Sunday.
    fiscal_week_num   INT NOT NULL,         -- 1-52 within the fiscal year.
    fiscal_month_num  INT NOT NULL,         -- 1-12; fiscal month 1 is April.
    fiscal_quarter    INT NOT NULL,         -- 1-4.
    fiscal_year       INT NOT NULL,         -- 2024 or 2025 only.
    nrf_454_week_num  INT,                  -- Holds the SAME value as fiscal_week_num in all 731 rows; no independent NRF numbering. Prefer fiscal_week_num.
    is_holiday        BOOLEAN NOT NULL DEFAULT FALSE -- True on 32 days (US retail holidays incl. Easter, Black Friday, Christmas/New Year).
);
```

---

## dim_promo_calendar

```meta
chunk_id: ddl:dim_promo_calendar
table: dim_promo_calendar
type: dimension
domain: promotions
grain: one row per marketing cycle
rows: 47
keywords: promo calendar, campaign cycle, marketing season, promotion window
```

```sql
-- Marketing calendar, deliberately decoupled from the fiscal calendar in
-- dim_date. Cycles float and do not align to fiscal week or month boundaries.
-- Joined only by fact_promo_performance, which also joins dim_date, allowing
-- promotional results to be sliced by campaign cycle or by accounting period.
-- Only 26 of these 47 cycles have any performance rows.
CREATE TABLE dim_promo_calendar (
    promo_calendar_key   SERIAL PRIMARY KEY,
    promo_cycle_id       VARCHAR(50) NOT NULL UNIQUE,  -- e.g. 'PROMO_2024_WK01_001'.
    promo_cycle_name     VARCHAR(150) NOT NULL,        -- e.g. 'Easter & Spring Refresh - Phase 1'.
    promo_season_type    VARCHAR(50) NOT NULL,         -- 11 values: Summer Grilling, Back to School, Memorial Day Kickoff, Easter & Spring Refresh, Holiday Season, New Year New You, Spring Cleaning, Thanksgiving Feast, Big Game & Valentine's, Fall Harvest & Halloween, Labor Day Savings.
    cycle_start_date     DATE NOT NULL,                -- Circular prices go live.
    cycle_end_date       DATE NOT NULL,                -- Cycles run 8-21 days.
    is_major_event_cycle BOOLEAN NOT NULL DEFAULT FALSE -- True for 24 of 47 cycles.
);
```

---

## dim_product

```meta
chunk_id: ddl:dim_product
table: dim_product
type: dimension
domain: conformed
grain: one row per SKU
rows: 200
keywords: product, item, SKU, brand, department, category, private label, UPC
```

```sql
-- Master item dimension. Hierarchy is strictly
-- department_name > category_name > sub_category_name, e.g.
-- 'Produce > Fresh Fruit > Apples'. 17 departments, 33 categories,
-- 48 sub-categories, 24 brands. All brand names are fictional.
-- NOTE: there is no vendor key here. Product-to-vendor attribution exists
-- only through fact_vendor_allowances.
CREATE TABLE dim_product (
    product_key       SERIAL PRIMARY KEY,
    sku_id            VARCHAR(50) NOT NULL UNIQUE, -- Internal item code, format 'SKU100001'.
    upc_barcode       VARCHAR(14),                 -- Always 12 zero-padded digits in practice.
    product_name      VARCHAR(255) NOT NULL,       -- Brand + item, e.g. 'Bright Orchard Honey Nut Cereal'.
    brand_name        VARCHAR(100) NOT NULL,       -- 24 fictional brands; 4 are private label.
    department_name   VARCHAR(100) NOT NULL,       -- Top level. Largest: Dairy & Eggs (32 items), Produce (31), Pantry & Canned Goods (16), Meat & Seafood (15).
    category_name     VARCHAR(100) NOT NULL,       -- Middle level, e.g. 'Salty Snacks', 'Fresh Fruit'.
    sub_category_name VARCHAR(100) NOT NULL,       -- Lowest level, e.g. 'Chips', 'Apples'.
    package_size_desc VARCHAR(50),                 -- 15 values e.g. '12 oz', '6-Pack', 'Sold by the Lb'. NULL for 25 items whose name already states a size.
    is_private_label  BOOLEAN NOT NULL DEFAULT FALSE -- True for 41 items. Private-label brands: Everyday Basics, Homestead Select, Pantry Essentials, ValueChoice.
);
```

---

## dim_store

```meta
chunk_id: ddl:dim_store
table: dim_store
type: dimension
domain: conformed
grain: one row per store
rows: 10
keywords: store, location, banner, city, state, square footage, format, layout
```

```sql
-- Store dimension: 10 stores across 4 banners and 4 layout formats.
-- Banners: Thrift & Table (Value/Discount), Corner Fresh Grocers
-- (Neighborhood Market), Metro Express Foods (Urban Small Format),
-- Heritage Provisions Co-op (Traditional Supermarket).
-- Stores are NOT linked to dim_geography; that dimension serves market share
-- reporting only.
CREATE TABLE dim_store (
    store_key        SERIAL PRIMARY KEY,
    store_id         VARCHAR(20) NOT NULL UNIQUE, -- 'STR001'..'STR010'.
    store_name       VARCHAR(150) NOT NULL,       -- '<banner> - <city>', e.g. 'Thrift & Table - Auburn'.
    banner_name      VARCHAR(100) NOT NULL,       -- One of the 4 banners above.
    street_address   VARCHAR(255),
    city             VARCHAR(100),                -- e.g. Ashland, Salem, Riverside, Madison, Bristol, Auburn, Fairview, Georgetown.
    state_code       CHAR(2),                     -- KY, OR, CA, WI, CT, AL, NC, TX.
    postal_code      VARCHAR(12),
    square_footage   INT,                         -- 11,462-45,639.
    layout_type_desc VARCHAR(50)                  -- Value/Discount, Neighborhood Market, Urban Small Format, Traditional Supermarket.
);
```

---

## dim_competitor

```meta
chunk_id: ddl:dim_competitor
table: dim_competitor
type: dimension
domain: competition
grain: one row per competitor banner
rows: 5
keywords: competitor, rival, banner, market positioning, price index
```

```sql
-- The five rival banners tracked for price and share. competitor_name and
-- banner_name are identical in every row. There are no competitor store rows;
-- competitive data is anchored to OUR stores.
CREATE TABLE dim_competitor (
    competitor_key     SERIAL PRIMARY KEY,
    competitor_id      VARCHAR(20) NOT NULL UNIQUE, -- 'COMP01'..'COMP05'.
    competitor_name    VARCHAR(150) NOT NULL,       -- Foothill Fresh, QuickStop Grocery, Heritage Fine Foods, Bulk Barn Wholesale Club, ValuMax Foods.
    banner_name        VARCHAR(100) NOT NULL,       -- Same value as competitor_name.
    market_positioning VARCHAR(50)                  -- Premium/Specialty (~115% of our price), Convenience/Small Format (~116%), Value/Discount (~91%), Club/Warehouse (~84%).
);
```

---

## dim_promotion

```meta
chunk_id: ddl:dim_promotion
table: dim_promotion
type: dimension
domain: promotions
grain: one row per promotion
rows: 40
keywords: promotion, discount, BOGO, multi-buy, mechanic, offer
```

```sql
-- Promotional offer definitions: what kind of discount, and the minimum
-- quantity that triggers it.
CREATE TABLE dim_promotion (
    promotion_key            SERIAL PRIMARY KEY,
    promotion_id             VARCHAR(50) NOT NULL UNIQUE,
    promotion_name           VARCHAR(150) NOT NULL, -- Shelf-label campaign name, e.g. 'Back to School Flash Sale'.
    mechanic_type            VARCHAR(50) NOT NULL,  -- 6 values: Multi-Buy (12), BOGO (7), Loyalty Price Drop (7), Mix-and-Match (7), Percent Off (5), Dollar Off (2).
    min_purchase_requirement INT DEFAULT 1          -- Units needed to trigger: 1 for Loyalty Price Drop/Percent Off/Dollar Off, 2 for BOGO, 2-5 Multi-Buy, 3-5 Mix-and-Match.
);
```

---

## dim_ad_channel

```meta
chunk_id: ddl:dim_ad_channel
table: dim_ad_channel
type: dimension
domain: advertising
grain: one row per media channel
rows: 7
keywords: advertising channel, media, print, social, email, digital
```

```sql
-- Media channels used to deliver campaigns. Reached from fact_ad_performance
-- only via dim_ad_placement (fact -> dim_ad_placement -> dim_ad_channel).
CREATE TABLE dim_ad_channel (
    ad_channel_key  SERIAL PRIMARY KEY,
    channel_id      VARCHAR(20) NOT NULL UNIQUE, -- 'CH01'..'CH07'.
    channel_type    VARCHAR(50) NOT NULL,        -- 5 types: Print Flyer (CH01,CH02), Digital Mailer (CH03), Paid Social (CH04,CH05), In-App Push (CH06), Website Banner (CH07).
    medium_platform VARCHAR(100)                 -- Sunday Paper Insert, Weekly Circular, Email Newsletter, Instagram, Facebook, Mobile App, Homepage.
);
```

---

## dim_ad_placement

```meta
chunk_id: ddl:dim_ad_placement
table: dim_ad_placement
type: dimension
domain: advertising
grain: one row per ad placement
rows: 90
keywords: ad placement, creative, slot, front page, theme, A/B test
```

```sql
-- Structural metadata for an advertisement, kept separate from its daily
-- performance so campaign descriptors are not repeated per row in
-- fact_ad_performance.
CREATE TABLE dim_ad_placement (
    ad_placement_key      SERIAL PRIMARY KEY,
    ad_id                 VARCHAR(50) NOT NULL UNIQUE,
    ad_channel_key        INT NOT NULL REFERENCES dim_ad_channel(ad_channel_key), -- The delivery channel.
    ad_theme_name         VARCHAR(150) NOT NULL, -- Creative concept, 64 distinct values.
    creative_version_code VARCHAR(50),           -- A/B test variant, 9 distinct values.
    page_number_slot      VARCHAR(20),           -- 10 values: Page 1 - Full, Page 1 - Top Left, Page 2 - Top Right, Center Spread, Back Page, Email Header, Homepage Hero, In-Feed Card 1, Mobile Banner 1, Search Result Top.
    is_front_page_feature BOOLEAN NOT NULL DEFAULT FALSE -- True for 13 of 90 placements.
);
```

---

## dim_vendor

```meta
chunk_id: ddl:dim_vendor
table: dim_vendor
type: dimension
domain: cost
grain: one row per vendor
rows: 30
keywords: vendor, supplier, payment terms, trade
```

```sql
-- Suppliers. Reachable only through fact_vendor_allowances; dim_product has
-- no vendor key, so "which vendor supplies product X" can only be answered
-- via allowance rows.
CREATE TABLE dim_vendor (
    vendor_key         SERIAL PRIMARY KEY,
    vendor_id          VARCHAR(50) NOT NULL UNIQUE,
    vendor_name        VARCHAR(150) NOT NULL,
    payment_terms_desc VARCHAR(50) -- 5 values: Net 60 (8 vendors), Net 30 (7), 1/15 Net 45 (7), 2/10 Net 30 (5), Net 45 (3).
);
```

---

## dim_allowance_type

```meta
chunk_id: ddl:dim_allowance_type
table: dim_allowance_type
type: dimension
domain: cost
grain: one row per allowance type
rows: 7
keywords: allowance, trade funding, rebate, slotting, scan back, code decode
```

```sql
-- Code/decode table for vendor trade-funding programs. Join
-- fact_vendor_allowances to this table to turn allowance_type_key into a
-- readable program name.
CREATE TABLE dim_allowance_type (
    allowance_type_key  SERIAL PRIMARY KEY,
    allowance_type_code VARCHAR(30) NOT NULL UNIQUE, -- SCAN_BACK, SLOTTING, SPOILAGE, VOLUME_REBATE, ADVERTISING, NEW_ITEM, DISPLAY.
    allowance_type_name VARCHAR(100) NOT NULL        -- Scan-Back Allowance, Slotting Fee, Spoilage Allowance, Volume Rebate, Cooperative Advertising Allowance, New Item Introduction Allowance, Display Allowance.
);
```

---

## dim_geography

```meta
chunk_id: ddl:dim_geography
table: dim_geography
type: dimension
domain: market share
grain: one row per market region
rows: 8
keywords: region, geography, market, DMA, syndicated
```

```sql
-- Syndicated market regions for share reporting. NOT linked to dim_store:
-- there is no store-to-region mapping anywhere in the schema, so regional
-- market share cannot be tied back to individual stores.
CREATE TABLE dim_geography (
    market_region_key      SERIAL PRIMARY KEY,
    region_id              VARCHAR(50) NOT NULL UNIQUE, -- 'RGN01'..'RGN08'.
    region_name            VARCHAR(100) NOT NULL,       -- Tristate Metro, Southeast Coastal, Piedmont, New England, Gulf Coast, Central Plains, Pacific Northwest, Mountain West.
    syndicated_market_code VARCHAR(50)                  -- Fictional 'DMA-nnn' codes; not real syndicator numbering.
);
```

---

## fact_pos_retail_sales

```meta
chunk_id: ddl:fact_pos_retail_sales
table: fact_pos_retail_sales
type: fact
domain: sales
grain: one row per date + product + store + basket line (daily)
rows: 1291781
keywords: sales, revenue, POS, register, basket, receipt, units, markdown, net sales
```

```sql
-- Register line items: the main sales fact. Daily grain, 731 dates,
-- 258,308 baskets, all 10 stores and all 200 products.
-- WARNING: the date column is sales_date_key here, NOT date_key.
-- Verified invariant: net_sales_amt = gross_sales_amt - markdown_discount_amt
-- for every row. gross_sales_amt / quantity_sold reproduces that week's
-- regular_retail_price on full-price lines.
CREATE TABLE fact_pos_retail_sales (
    sales_date_key        INT NOT NULL REFERENCES dim_date(date_key),       -- Note the non-standard column name.
    product_key           INT NOT NULL REFERENCES dim_product(product_key),
    store_key             INT NOT NULL REFERENCES dim_store(store_key),
    basket_id             VARCHAR(64) NOT NULL,                -- Degenerate dimension, format 'BSK0000000001'. COUNT(DISTINCT basket_id) = transaction count.
    quantity_sold         NUMERIC(12,3) NOT NULL,              -- 0.400-4.000. Fractional on 291,531 rows (weight-sold produce/deli).
    gross_sales_amt       NUMERIC(12,2) NOT NULL,              -- 0.27-107.00. Quantity x regular shelf price, before discount.
    markdown_discount_amt NUMERIC(12,2) NOT NULL DEFAULT 0.00, -- Greater than zero on only 4.6% of rows.
    net_sales_amt         NUMERIC(12,2) NOT NULL,              -- Actual revenue. Use this for "sales" unless gross is requested.
    PRIMARY KEY (sales_date_key, product_key, store_key, basket_id)
);
```

---

## fact_item_cogs

```meta
chunk_id: ddl:fact_item_cogs
table: fact_item_cogs
type: fact
domain: cost
grain: one row per product + store + fiscal month (MONTHLY, despite daily key)
rows: 41400
keywords: COGS, cost of goods, base cost, freight, margin, landed cost
```

```sql
-- Unit cost components. GRAIN WARNING: although the primary key looks daily,
-- rows exist for only 24 date_key values -- one per fiscal month, each the
-- FIRST day of that fiscal month. Joining this to daily sales on date_key
-- matches almost nothing; join on fiscal month instead.
CREATE TABLE fact_item_cogs (
    date_key      INT NOT NULL REFERENCES dim_date(date_key),       -- First day of a fiscal month. Only 24 distinct values.
    product_key   INT NOT NULL REFERENCES dim_product(product_key),
    store_key     INT NOT NULL REFERENCES dim_store(store_key),
    base_cost     NUMERIC(12,4) NOT NULL,                  -- Supplier invoice unit cost, 0.4660-18.0268.
    freight_cost  NUMERIC(12,4) NOT NULL DEFAULT 0.0000,   -- Allocated logistics cost, 0.0051-0.8681.
    net_item_cost NUMERIC(12,4) NOT NULL,                  -- base_cost + freight_cost, each rounded independently, so the sum may differ by up to 0.0001. Use this for margin.
    PRIMARY KEY (date_key, product_key, store_key)
);
```

---

## fact_vendor_allowances

```meta
chunk_id: ddl:fact_vendor_allowances
table: fact_vendor_allowances
type: fact
domain: cost
grain: one row per product + store + vendor + allowance type + fiscal month (MONTHLY)
rows: 32350
keywords: allowance, trade funding, vendor credit, rebate, slotting, scan back
```

```sql
-- Trade funding credited by suppliers. GRAIN WARNING: monthly, 24 date_key
-- values (first day of each fiscal month), not daily.
-- A single product/store/month can carry several rows, one per vendor and
-- allowance type, so always aggregate rather than assuming one row.
CREATE TABLE fact_vendor_allowances (
    date_key                INT NOT NULL REFERENCES dim_date(date_key),        -- First day of a fiscal month.
    product_key             INT NOT NULL REFERENCES dim_product(product_key),
    store_key               INT NOT NULL REFERENCES dim_store(store_key),
    vendor_key              INT NOT NULL REFERENCES dim_vendor(vendor_key),    -- All 30 vendors appear. This is the only product-to-vendor link in the schema.
    allowance_type_key      INT NOT NULL REFERENCES dim_allowance_type(allowance_type_key), -- Join dim_allowance_type to decode.
    allowance_rate_per_unit NUMERIC(12,4) NOT NULL DEFAULT 0.0000, -- Funding per unit, 0.0079-1.8267.
    total_allowance_amt     NUMERIC(12,2) NOT NULL,                -- Total credit earned, 0.76-912.45.
    PRIMARY KEY (date_key, product_key, store_key, vendor_key, allowance_type_key)
);
```

---

## fact_item_prices

```meta
chunk_id: ddl:fact_item_prices
table: fact_item_prices
type: fact
domain: pricing
grain: one row per product + store + fiscal week (WEEKLY, despite daily key)
rows: 179400
keywords: price, shelf price, regular price, promo price, markdown, price index
```

```sql
-- The price of record for every stocked item at every store, and the bridge
-- between the sales, cost and competitive domains.
-- GRAIN WARNING: weekly, not daily. 104 date_key values, each the FIRST day
-- of a fiscal week; that price holds for the rest of the week. To price a
-- sale, join to the row for the first day of the sale's fiscal week.
-- COVERAGE: 1,725 of 2,000 possible product-store pairs. A missing row means
-- the store does not stock that item.
CREATE TABLE fact_item_prices (
    date_key             INT NOT NULL REFERENCES dim_date(date_key),       -- First day of a fiscal week.
    product_key          INT NOT NULL REFERENCES dim_product(product_key),
    store_key            INT NOT NULL REFERENCES dim_store(store_key),
    regular_retail_price NUMERIC(12,2) NOT NULL, -- Standard shelf price, 0.67-26.77.
    base_promo_price     NUMERIC(12,2),          -- Promotional price. NULL on 93.3% of rows, meaning NOT on promotion that week -- never treat NULL as zero. Always strictly below regular_retail_price when present.
    PRIMARY KEY (date_key, product_key, store_key)
);
```

---

## fact_competitor_pricing

```meta
chunk_id: ddl:fact_competitor_pricing
table: fact_competitor_pricing
type: fact
domain: competition
grain: one row per product + competitor + anchor store + fiscal week (WEEKLY)
rows: 54146
keywords: competitor price, price check, audit, rival, price index, competitive
```

```sql
-- Rival shelf prices collected by the competitive-intelligence team.
-- GRAIN: weekly, 104 date_key values (first day of each fiscal week).
-- COVERAGE WARNING: only the 4-store audit panel (STR003, STR005, STR009,
-- STR010) and only 70 tracked products, not the full 200-item catalog.
-- store_key is OUR anchor store defining the competitive radius, NOT a
-- competitor's store -- no competitor store rows exist anywhere.
CREATE TABLE fact_competitor_pricing (
    date_key           INT NOT NULL REFERENCES dim_date(date_key),       -- First day of a fiscal week.
    product_key        INT NOT NULL REFERENCES dim_product(product_key), -- One of 70 tracked products.
    competitor_key     INT NOT NULL REFERENCES dim_competitor(competitor_key),
    store_key          INT NOT NULL REFERENCES dim_store(store_key),     -- OUR store anchoring the comparison.
    comp_regular_price NUMERIC(12,2) NOT NULL, -- Observed rival shelf price. Ratio to our regular_retail_price tracks market_positioning: Club ~84%, Value ~91%, Premium ~115%, Convenience ~116%.
    comp_promo_price   NUMERIC(12,2),          -- Observed rival promo price; populated on 24.6% of rows, NULL otherwise.
    PRIMARY KEY (date_key, product_key, competitor_key, store_key)
);
```

---

## fact_market_share_weekly

```meta
chunk_id: ddl:fact_market_share_weekly
table: fact_market_share_weekly
type: fact
domain: market share
grain: one row per fiscal week + product + region + competitor (WEEKLY)
rows: 291200
keywords: market share, syndicated, region, total market, share of market, competitor sales
```

```sql
-- Syndicated regional market-share estimates: 8 regions x 70 tracked products
-- x 5 competitors x 104 weeks.
-- WARNING 1: the date column is week_key, NOT date_key, though it still
-- references dim_date(date_key). It holds the FIRST day of the fiscal week
-- (verified for all 104 values), not the week-ending date.
-- WARNING 2 -- FAN-OUT: every (week_key, product_key, market_region_key) cell
-- has exactly 5 rows, one per competitor, and grocer_sales_amount and
-- total_market_sales_amount REPEAT IDENTICALLY on all 5. Summing either column
-- directly multiplies that total by 5. De-duplicate first, e.g. with MAX()
-- grouped by the cell, or by filtering to a single competitor_key.
-- A ratio of the two repeated columns survives un-de-duplicated (both inflate
-- equally, giving 21.48% either way); it is ABSOLUTE totals that come out 5x
-- too large.
-- The relationship that holds is:
--   total_market_sales_amount = grocer_sales_amount + SUM(competitor_sales_amount across all 5)
-- Our share of total market averages 21.5%.
CREATE TABLE fact_market_share_weekly (
    week_key                  INT NOT NULL REFERENCES dim_date(date_key),       -- First day of the fiscal week. Non-standard column name.
    product_key               INT NOT NULL REFERENCES dim_product(product_key), -- One of 70 tracked products.
    market_region_key         INT NOT NULL REFERENCES dim_geography(market_region_key),
    competitor_key            INT NOT NULL REFERENCES dim_competitor(competitor_key),
    grocer_sales_amount       NUMERIC(15,2) NOT NULL, -- OUR sales in that region/product/week. REPEATS across the 5 competitor rows.
    competitor_sales_amount   NUMERIC(15,2) NOT NULL, -- That one competitor's sales. Varies per row -- safe to SUM.
    total_market_sales_amount NUMERIC(15,2) NOT NULL, -- Whole market, all retailers. REPEATS across the 5 competitor rows.
    PRIMARY KEY (week_key, product_key, market_region_key, competitor_key)
);
```

---

## fact_promo_performance

```meta
chunk_id: ddl:fact_promo_performance
table: fact_promo_performance
type: fact
domain: promotions
grain: one row per date + cycle + product + store + promotion (daily)
rows: 73057
keywords: promotion performance, lift, incremental units, campaign, promo sales
```

```sql
-- Promotional results, and the only table joined to BOTH calendars: date_key
-- to the corporate fiscal calendar and promo_calendar_key to the marketing
-- calendar. That dual link is what lets lift be sliced by accounting period
-- or by campaign cycle.
-- Daily grain, 397 dates. All 40 promotions appear; only 26 of the 47 promo
-- cycles do.
CREATE TABLE fact_promo_performance (
    date_key            INT NOT NULL REFERENCES dim_date(date_key),          -- Corporate fiscal date.
    promo_calendar_key  INT NOT NULL REFERENCES dim_promo_calendar(promo_calendar_key), -- Marketing cycle.
    product_key         INT NOT NULL REFERENCES dim_product(product_key),
    store_key           INT NOT NULL REFERENCES dim_store(store_key),
    promotion_key       INT NOT NULL REFERENCES dim_promotion(promotion_key), -- Join dim_promotion for mechanic_type.
    promo_quantity_sold NUMERIC(12,3) NOT NULL, -- Units moved on promotion, 3.256-97.203.
    promo_quantity_lift NUMERIC(12,3) NOT NULL, -- Incremental units vs baseline, 0.495-43.514. ALWAYS POSITIVE in this dataset; no negative-lift rows exist.
    PRIMARY KEY (date_key, promo_calendar_key, product_key, store_key, promotion_key)
);

CREATE INDEX idx_promo_perf_calendars ON fact_promo_performance (date_key, promo_calendar_key);
```

---

## fact_ad_performance

```meta
chunk_id: ddl:fact_ad_performance
table: fact_ad_performance
type: fact
domain: advertising
grain: one row per date + placement + product + store (daily)
rows: 18872
keywords: ad performance, spend, impressions, clicks, coupon clips, CTR, advertising
```

```sql
-- Daily advertising metrics by placement. Reach channel attributes through
-- dim_ad_placement -> dim_ad_channel; there is no direct channel key here.
-- Daily grain, 505 dates, all 90 placements.
-- Metric ranges vary by channel_type:
--   Print Flyer    spend 20.01-150.00, impressions 5,000-39,980, clicks <=800
--   Paid Social    spend 15.00-119.98, impressions 3,002-24,991, clicks <=900
--   Website Banner spend 10.03-89.98,  impressions 2,014-19,967, clicks <=600
--   Digital Mailer spend 10.04-79.87,  impressions 2,002-14,979, clicks <=1200
--   In-App Push    spend  5.00-40.00,  impressions 1,000-8,000,  clicks <=500
CREATE TABLE fact_ad_performance (
    date_key                     INT NOT NULL REFERENCES dim_date(date_key),
    ad_placement_key             INT NOT NULL REFERENCES dim_ad_placement(ad_placement_key), -- Join for theme, slot, channel.
    product_key                  INT NOT NULL REFERENCES dim_product(product_key),
    store_key                    INT NOT NULL REFERENCES dim_store(store_key),
    ad_spend_amount              NUMERIC(12,2) NOT NULL DEFAULT 0.00, -- Daily localized spend for this placement.
    impressions_count            INT NOT NULL DEFAULT 0,              -- Views/print distribution.
    clicks_or_coupon_clips_count INT NOT NULL DEFAULT 0,              -- Link clicks (digital) or coupon clips (print/loyalty). CTR = this / impressions_count.
    PRIMARY KEY (date_key, ad_placement_key, product_key, store_key)
);

CREATE INDEX idx_ad_perf_lookup ON fact_ad_performance (ad_placement_key, store_key);
```
