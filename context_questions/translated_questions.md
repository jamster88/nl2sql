# Translated Golden Question Pairs

Forty-five natural-language questions paired with PostgreSQL that runs, unmodified,
against the `nl2sql_retail` database shipped in the retail Postgres container.

These are translations of the three drafts in [`RAW/`](RAW/). The drafts were written
against an imagined schema: calendar-2026 dates, store IDs like `S102`, real-world
brands, and departments that do not exist here. Every literal below was checked
against the live database, and every query was executed - all 45 return rows.

## How to read a pair

```meta
chunk_id: eval:how-to-read
type: context
keywords: golden pair, evaluation, benchmark, how to use, structure, format
```

Each pair is one `##` section and is self-contained, so it survives being chunked
and retrieved on its own. A section carries:

- **Question** - the natural-language input, phrased the way a merchant would ask it.
- **Reasoning target** - the specific capability the pair tests, and where generated
  SQL typically goes wrong.
- **SQL** - a verified answer, not necessarily the only correct one.
- **Result** - the shape of what comes back, so an empty result reads as a failure
  rather than as an answer.
- **Translation note** - what changed from the original draft, and why. Where a draft
  question was unanswerable against this data, the note says so.

## The data these questions run against

```meta
chunk_id: eval:anchors
type: reference
tables: dim_date, dim_store, dim_competitor, dim_geography, dim_product
keywords: reference values, anchors, fiscal calendar, store ids, competitors, regions, literals
```

Values that recur throughout, all verified against the live database:

| | |
|---|---|
| Coverage | 2023-04-01 .. 2025-03-31. **FY2024** = 2023-04-01 .. 2024-03-31, **FY2025** = 2024-04-01 .. 2025-03-31. No calendar 2026. |
| Fiscal months | 4-4-5 pattern, month 1 is April. `fiscal_month_num = 8` is **not** August. |
| Most recent business day | 2025-03-31 (fiscal week 52, month 12, Q4 of FY2025). |
| Latest weekly snapshot | 2025-03-24 - use this date for price and competitor-price questions. |
| Stores | `STR001`-`STR010`. Banners: Thrift & Table, Corner Fresh Grocers, Metro Express Foods, Heritage Provisions Co-op. |
| Store formats | Value/Discount, Neighborhood Market, Urban Small Format, Traditional Supermarket. Largest store is 45,639 sq ft. |
| Competitors | `COMP01` Foothill Fresh, `COMP02` QuickStop Grocery, `COMP03` Heritage Fine Foods, `COMP04` Bulk Barn Wholesale Club, `COMP05` ValuMax Foods. |
| Regions | `RGN01`-`RGN08`, e.g. Tristate Metro (`DMA-335`), Pacific Northwest (`DMA-144`). |
| Products | 200 SKUs, 24 fictional brands, 41 private label. Hierarchy: 17 departments > 33 categories > 48 sub-categories. |
| Promo cycles | 47 cycles named `<season> - Phase <n>`, IDs like `PROMO_2025_WK15_007`. 11 season types. |
| Allowance codes | SCAN_BACK, SLOTTING, SPOILAGE, VOLUME_REBATE, ADVERTISING, NEW_ITEM, DISPLAY. |
| Ad channels | Print Flyer, Digital Mailer, Paid Social, In-App Push, Website Banner. |

## Grain, and why most rewrites were necessary

```meta
chunk_id: eval:grain-warning
type: business rule
tables: fact_pos_retail_sales, fact_item_cogs, fact_item_prices, fact_vendor_allowances, fact_competitor_pricing, fact_market_share_weekly
keywords: grain, granularity, join, zero rows, daily, weekly, monthly, mixed grain, fan-out
```

Four facts have a daily-looking primary key but are not written daily. This is the
single largest difference between the drafts and reality, and it changed more
queries than the renamed literals did.

| Table | Grain | Dates | Stamped with |
|---|---|---|---|
| `fact_pos_retail_sales` | daily | 731 | the sale date |
| `fact_promo_performance` | daily | 397 | the event date |
| `fact_ad_performance` | daily | 505 | the event date |
| `fact_item_prices` | **weekly** | 104 | first day of the fiscal week |
| `fact_competitor_pricing` | **weekly** | 104 | first day of the fiscal week |
| `fact_market_share_weekly` | **weekly** | 104 | first day of the fiscal week |
| `fact_item_cogs` | **monthly** | 24 | first day of the fiscal month |
| `fact_vendor_allowances` | **monthly** | 24 | first day of the fiscal month |

Three consequences run through the pairs below:

1. **Daily gross profit does not exist.** Costs are monthly, so questions asking for
   yesterday's margin were re-anchored to a fiscal month.
2. **Joining sales to costs on `date_key` matches only 24 days a year.** Aggregate
   both sides to fiscal month first.
3. **`fact_market_share_weekly` holds five rows per cell**, one per competitor, and
   two of its three measures repeat identically across them. Summing them raw
   inflates the total exactly 5x. De-duplicate first; only `competitor_sales_amount`
   is safe to sum directly.

## What the drafts asked for that this data cannot answer

```meta
chunk_id: eval:unanswerable
type: business rule
keywords: cannot answer, coverage limit, missing data, lane, loyalty, negative lift, out of scope
```

Five draft questions had no answerable form and were rewritten rather than dropped.
Each is worth knowing on its own:

- **No checkout lane exists.** `basket_id` is `BSK` plus a 10-digit sequence. It
  identifies a transaction, not a register and not a shopper. Store-day is the finest
  grain available for markdown exceptions (Q42).
- **No customer or loyalty dimension exists**, so "customers who bought X" can only
  be answered as "baskets containing X" (Q16).
- **Promotional lift is never negative** - the minimum is 0.495 - so cannibalization
  cannot be found by looking for negative lift (Q32).
- **No column maps a store to a market region.** A region can narrow which products
  are in scope, but regional share can never be tied back to a store (Q24, Q44).
- **Competitor pricing covers 4 of 10 stores and 70 of 200 products**, and not every
  competitor is surveyed at every store (Q03, Q07).

---

# Suite 1 - Sales and margin analytics

## Q01 - Total net sales and gross profit for a department in one fiscal month

```meta
chunk_id: eval:q01
type: golden pair
tables: fact_pos_retail_sales, fact_item_cogs, dim_product, dim_date
keywords: gross profit, gross margin, produce, department, COGS, monthly cost, mixed grain
```

**Question:** "What were our total net sales and total gross profit for the Produce department in fiscal month 12 of FY2025 (24 Feb - 31 Mar 2025)?"

**Reasoning target:** Reconciling daily sales against monthly costs. The naive join `sales.sales_date_key = cogs.date_key` matches only the 24 month-start days and silently understates the answer by roughly 30x.

```sql
WITH monthly_sales AS (
    SELECT d.fiscal_year, d.fiscal_month_num, s.product_key, s.store_key,
           SUM(s.net_sales_amt) AS net_sales,
           SUM(s.quantity_sold)  AS units
    FROM fact_pos_retail_sales s
    JOIN dim_date    d ON d.date_key    = s.sales_date_key
    JOIN dim_product p ON p.product_key = s.product_key
    WHERE p.department_name = 'Produce'
      AND d.fiscal_year = 2025
      AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4
),
monthly_cost AS (
    SELECT d.fiscal_year, d.fiscal_month_num, c.product_key, c.store_key,
           AVG(c.net_item_cost) AS unit_cost
    FROM fact_item_cogs c
    JOIN dim_date d ON d.date_key = c.date_key
    WHERE d.fiscal_year = 2025
      AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4
)
SELECT ROUND(SUM(s.net_sales), 2)                                   AS total_net_sales,
       ROUND(SUM(s.units * c.unit_cost), 2)                         AS total_cogs,
       ROUND(SUM(s.net_sales) - SUM(s.units * c.unit_cost), 2)      AS total_gross_profit,
       ROUND(100.0 * (SUM(s.net_sales) - SUM(s.units * c.unit_cost))
             / NULLIF(SUM(s.net_sales), 0), 2)                      AS gross_margin_pct
FROM monthly_sales s
JOIN monthly_cost  c USING (fiscal_year, fiscal_month_num, product_key, store_key);
```

**Result:** 1 row: net sales, COGS, gross profit and margin percentage.

**Translation note:** The original asked for 'yesterday'. Costs are monthly here, so daily gross profit does not exist; the smallest period that yields a real margin is a fiscal month.

---

# Suite 2 - Dual-calendar routing

## Q02 - Top SKUs by promotional volume in one marketing cycle

```meta
chunk_id: eval:q02
type: golden pair
tables: fact_promo_performance, dim_promo_calendar, dim_product
keywords: promo cycle, campaign, marketing calendar, top SKUs, units sold, back to school
```

**Question:** "Find the top 5 SKUs by total promotional quantity sold during the 'Back to School - Phase 1' promo cycle."

**Reasoning target:** Routing to `dim_promo_calendar` (the marketing calendar) rather than `dim_date` (the fiscal calendar). The cycle floats freely and does not align to fiscal week or month boundaries.

```sql
SELECT prod.sku_id,
       prod.product_name,
       SUM(perf.promo_quantity_sold) AS total_units_sold
FROM fact_promo_performance perf
JOIN dim_promo_calendar cal  ON cal.promo_calendar_key = perf.promo_calendar_key
JOIN dim_product        prod ON prod.product_key       = perf.product_key
WHERE cal.promo_cycle_name = 'Back to School - Phase 1'
GROUP BY prod.sku_id, prod.product_name
ORDER BY total_units_sold DESC
LIMIT 5;
```

**Result:** 5 rows: SKU, product name, units sold on promotion.

**Translation note:** Cycle renamed: 'Back to School Blast Phase 1' does not exist; the real cycle names follow the pattern '<season> - Phase <n>'.

---

# Suite 3 - Cross-domain competitive price indexing

## Q03 - Products where we are priced above a competitor

```meta
chunk_id: eval:q03
type: golden pair
tables: fact_item_prices, fact_competitor_pricing, dim_store, dim_competitor, dim_product
keywords: competitor price, price gap, undercut, more expensive, shelf price, weekly price
```

**Question:** "List the products at store STR003 where our regular shelf price in the week beginning 2025-03-24 was higher than ValuMax Foods' (COMP05) regular price."

**Reasoning target:** Bridging our own price file to the competitor survey. Both are weekly and share week-anchor date keys, so they join directly on date, product and store.

```sql
SELECT prod.sku_id,
       prod.product_name,
       ours.regular_retail_price                            AS our_price,
       comp.comp_regular_price                              AS competitor_price,
       ROUND(ours.regular_retail_price - comp.comp_regular_price, 2) AS price_gap
FROM fact_item_prices ours
JOIN dim_store  store ON store.store_key = ours.store_key
JOIN fact_competitor_pricing comp
       ON comp.date_key    = ours.date_key
      AND comp.product_key = ours.product_key
      AND comp.store_key   = ours.store_key
JOIN dim_competitor rival ON rival.competitor_key = comp.competitor_key
JOIN dim_product    prod  ON prod.product_key     = ours.product_key
WHERE store.store_id    = 'STR003'
  AND rival.competitor_id = 'COMP05'
  AND ours.date_key     = 20250324
  AND ours.regular_retail_price > comp.comp_regular_price
ORDER BY price_gap DESC;
```

**Result:** 62 rows: SKU, product name, both prices and the gap.

**Translation note:** Store and competitor both remapped. ValuMax Foods is the discount banner and is the only surveyed rival that undercuts us at STR003 - the two premium banners never do, so asking about them returns nothing.

---

# Suite 4 - Marketing performance and efficiency

## Q04 - Return on ad spend and volume lift for a brand in one channel

```meta
chunk_id: eval:q04
type: golden pair
tables: fact_ad_performance, fact_pos_retail_sales, fact_promo_performance, dim_ad_channel, dim_ad_placement, dim_product
keywords: ROAS, return on ad spend, advertising efficiency, lift, print flyer, brand
```

**Question:** "Calculate the return on ad spend and the total volume lift for Timberline products advertised in Print Flyer placements during FY2025 Q4."

**Reasoning target:** Combining three facts without fan-out. Joining ads, sales and promo performance in one FROM clause multiplies rows by every basket line and every placement; each source must be aggregated to date/product/store first.

```sql
WITH ad AS (
    SELECT a.date_key, a.product_key, a.store_key,
           SUM(a.ad_spend_amount) AS ad_spend
    FROM fact_ad_performance a
    JOIN dim_ad_placement pl ON pl.ad_placement_key = a.ad_placement_key
    JOIN dim_ad_channel   ch ON ch.ad_channel_key   = pl.ad_channel_key
    JOIN dim_product      p  ON p.product_key       = a.product_key
    JOIN dim_date         d  ON d.date_key          = a.date_key
    WHERE ch.channel_type = 'Print Flyer'
      AND p.brand_name    = 'Timberline'
      AND d.fiscal_year   = 2025
      AND d.fiscal_quarter = 4
    GROUP BY 1, 2, 3
),
sales AS (
    SELECT s.sales_date_key AS date_key, s.product_key, s.store_key,
           SUM(s.net_sales_amt) AS net_sales
    FROM fact_pos_retail_sales s
    WHERE s.sales_date_key IN (SELECT date_key FROM ad)
    GROUP BY 1, 2, 3
),
lift AS (
    SELECT f.date_key, f.product_key, f.store_key,
           SUM(f.promo_quantity_lift) AS units_lift
    FROM fact_promo_performance f
    WHERE f.date_key IN (SELECT date_key FROM ad)
    GROUP BY 1, 2, 3
)
SELECT ROUND(SUM(ad.ad_spend), 2)                                          AS total_ad_spend,
       ROUND(SUM(COALESCE(s.net_sales, 0)), 2)                             AS attributed_net_sales,
       ROUND(SUM(COALESCE(s.net_sales, 0)) / NULLIF(SUM(ad.ad_spend), 0), 2) AS return_on_ad_spend,
       ROUND(SUM(COALESCE(l.units_lift, 0)), 2)                            AS total_volume_lift
FROM ad
LEFT JOIN sales s USING (date_key, product_key, store_key)
LEFT JOIN lift  l USING (date_key, product_key, store_key);
```

**Result:** 1 row: ad spend, attributed net sales, ROAS and lift.

**Translation note:** Brand remapped - no real-world brands exist in this data. The original query's direct three-way join would have inflated both sales and lift.

---

# Suite 5 - Vendor trade funding and net-net cost

## Q05 - Base cost versus net-net cost after vendor allowances

```meta
chunk_id: eval:q05
type: golden pair
tables: fact_item_cogs, fact_vendor_allowances, dim_product, dim_store, dim_date
keywords: net-net cost, allowance, trade funding, private label, banner, invoice cost
```

**Question:** "Show the average base cost, landed cost and net-net cost after vendor allowances for private label products in Thrift & Table stores during fiscal month 12 of FY2025."

**Reasoning target:** Stacking allowances onto cost. COGS and allowances share the monthly grain so they join on `date_key` directly - but allowances carry vendor and allowance type in the key, so up to two rows per cell fan the costs out unless pre-aggregated.

```sql
WITH allowance AS (
    SELECT a.date_key, a.product_key, a.store_key,
           SUM(a.allowance_rate_per_unit) AS allowance_per_unit
    FROM fact_vendor_allowances a
    GROUP BY 1, 2, 3
)
SELECT prod.sku_id,
       prod.product_name,
       ROUND(AVG(cogs.base_cost), 4)                                          AS avg_invoice_base_cost,
       ROUND(AVG(cogs.net_item_cost), 4)                                      AS avg_landed_cost,
       ROUND(AVG(cogs.net_item_cost - COALESCE(a.allowance_per_unit, 0)), 4)  AS avg_net_net_cost
FROM fact_item_cogs cogs
JOIN dim_product prod  ON prod.product_key = cogs.product_key
JOIN dim_store   store ON store.store_key  = cogs.store_key
JOIN dim_date    d     ON d.date_key       = cogs.date_key
LEFT JOIN allowance a
       ON a.date_key    = cogs.date_key
      AND a.product_key = cogs.product_key
      AND a.store_key   = cogs.store_key
WHERE prod.is_private_label = TRUE
  AND store.banner_name     = 'Thrift & Table'
  AND d.fiscal_year         = 2025
  AND d.fiscal_month_num    = 12
GROUP BY prod.sku_id, prod.product_name
ORDER BY prod.sku_id
LIMIT 10;
```

**Result:** 10 rows: SKU, base cost, landed cost, net-net cost.

**Translation note:** Banner remapped. The original subtracted the allowance without collapsing the vendor/type dimension first, double-counting cost rows.

---

# Suite 6 - Price erosion and markdown efficiency

## Q06 - Markdown erosion as a share of gross sales

```meta
chunk_id: eval:q06
type: golden pair
tables: fact_pos_retail_sales, dim_product, dim_date
keywords: markdown, erosion, discount, gross sales, dairy, department, weekly
```

**Question:** "Which 5 items in the Dairy & Eggs department had the highest markdown erosion - markdown discount as a percentage of gross sales - in fiscal week 52 of FY2025?"

**Reasoning target:** Choosing gross rather than net sales as the denominator, since markdown is the difference between them.

```sql
SELECT prod.sku_id,
       prod.product_name,
       ROUND(SUM(sales.markdown_discount_amt), 2) AS total_markdowns,
       ROUND(SUM(sales.gross_sales_amt), 2)       AS total_gross_sales,
       ROUND(100.0 * SUM(sales.markdown_discount_amt)
             / NULLIF(SUM(sales.gross_sales_amt), 0), 2) AS markdown_erosion_pct
FROM fact_pos_retail_sales sales
JOIN dim_product prod ON prod.product_key = sales.product_key
JOIN dim_date    d    ON d.date_key       = sales.sales_date_key
WHERE prod.department_name = 'Dairy & Eggs'
  AND d.fiscal_year        = 2025
  AND d.fiscal_week_num    = 52
GROUP BY prod.sku_id, prod.product_name
ORDER BY markdown_erosion_pct DESC
LIMIT 5;
```

**Result:** 5 rows: SKU, markdowns, gross sales, erosion percentage.

**Translation note:** Department renamed from 'Dairy' to 'Dairy & Eggs'. The original filtered `nrf_454_week_num`, which in this data is a verbatim copy of `fiscal_week_num`.

---

# Suite 7 - Competitor price indexing

## Q07 - Competitor price index for a department at one store

```meta
chunk_id: eval:q07
type: golden pair
tables: fact_item_prices, fact_competitor_pricing, dim_store, dim_competitor, dim_product
keywords: CPI, price index, competitor, department, store, price comparison
```

**Question:** "Calculate the competitor price index for the Dairy & Eggs department at store STR005 against Foothill Fresh (COMP01) for the week beginning 2025-03-24, where an index above 100 means our shelf price is higher."

**Reasoning target:** Computing a ratio of two averages across a filtered join, and stating the index direction explicitly so the numerator is unambiguous.

```sql
SELECT prod.department_name,
       COUNT(*)                                   AS items_compared,
       ROUND(AVG(ours.regular_retail_price), 4)   AS our_avg_price,
       ROUND(AVG(comp.comp_regular_price), 4)     AS competitor_avg_price,
       ROUND(100.0 * AVG(ours.regular_retail_price)
             / NULLIF(AVG(comp.comp_regular_price), 0), 1) AS competitor_price_index
FROM fact_item_prices ours
JOIN dim_store store ON store.store_key = ours.store_key
JOIN fact_competitor_pricing comp
       ON comp.date_key    = ours.date_key
      AND comp.product_key = ours.product_key
      AND comp.store_key   = ours.store_key
JOIN dim_competitor rival ON rival.competitor_key = comp.competitor_key
JOIN dim_product    prod  ON prod.product_key     = ours.product_key
WHERE store.store_id      = 'STR005'
  AND rival.competitor_id = 'COMP01'
  AND prod.department_name = 'Dairy & Eggs'
  AND ours.date_key       = 20250324
GROUP BY prod.department_name;
```

**Result:** 1 row: index of 87.1 - we are cheaper than this premium rival.

**Translation note:** Competitor remapped: not every competitor is surveyed at every store. COMP03 is absent from STR005, so the original pairing returns nothing.

---

# Suite 8 - Promotion cannibalization

## Q08 - Promotions that lifted volume but earned thin profit

```meta
chunk_id: eval:q08
type: golden pair
tables: fact_promo_performance, fact_pos_retail_sales, fact_item_cogs, dim_promotion, dim_product
keywords: cannibalization, promotion, lift, gross profit, bakery, margin erosion
```

**Question:** "Identify promotions in the Bakery department during FY2025 Q4 where volume lift was positive but the gross profit generated was smaller than the cost of the goods sold."

**Reasoning target:** A three-fact reconciliation at mixed grains (daily promo, daily sales, monthly cost) with a HAVING clause over two derived aggregates.

```sql
WITH promo AS (
    SELECT pr.promotion_id, pr.promotion_name,
           d.fiscal_year, d.fiscal_month_num, f.product_key, f.store_key,
           SUM(f.promo_quantity_lift) AS units_lift
    FROM fact_promo_performance f
    JOIN dim_promotion pr ON pr.promotion_key = f.promotion_key
    JOIN dim_product   p  ON p.product_key    = f.product_key
    JOIN dim_date      d  ON d.date_key       = f.date_key
    WHERE p.department_name = 'Bakery'
      AND d.fiscal_year = 2025
      AND d.fiscal_quarter = 4
    GROUP BY 1, 2, 3, 4, 5, 6
),
sales AS (
    SELECT d.fiscal_year, d.fiscal_month_num, s.product_key, s.store_key,
           SUM(s.net_sales_amt) AS net_sales,
           SUM(s.quantity_sold) AS units
    FROM fact_pos_retail_sales s
    JOIN dim_date d ON d.date_key = s.sales_date_key
    WHERE d.fiscal_year = 2025 AND d.fiscal_quarter = 4
    GROUP BY 1, 2, 3, 4
),
cost AS (
    SELECT d.fiscal_year, d.fiscal_month_num, c.product_key, c.store_key,
           AVG(c.net_item_cost) AS unit_cost
    FROM fact_item_cogs c
    JOIN dim_date d ON d.date_key = c.date_key
    WHERE d.fiscal_year = 2025 AND d.fiscal_quarter = 4
    GROUP BY 1, 2, 3, 4
)
SELECT promo.promotion_id,
       promo.promotion_name,
       ROUND(SUM(promo.units_lift), 2)                                AS total_volume_lift,
       ROUND(SUM(sales.net_sales), 2)                                 AS net_sales,
       ROUND(SUM(sales.units * cost.unit_cost), 2)                    AS cogs,
       ROUND(SUM(sales.net_sales) - SUM(sales.units * cost.unit_cost), 2) AS gross_profit
FROM promo
JOIN sales USING (fiscal_year, fiscal_month_num, product_key, store_key)
JOIN cost  USING (fiscal_year, fiscal_month_num, product_key, store_key)
GROUP BY promo.promotion_id, promo.promotion_name
HAVING SUM(promo.units_lift) > 0
   AND SUM(sales.net_sales) - SUM(sales.units * cost.unit_cost)
       < SUM(sales.units * cost.unit_cost)
ORDER BY gross_profit;
```

**Result:** 6 rows: promotion, lift, net sales, COGS, gross profit.

**Translation note:** Window changed from 'last 30 days' to a fiscal quarter, because monthly costs cannot be resolved inside a rolling 30-day window.

---

# Suite 9 - Ad channel conversion

## Q09 - Engagement rate by advertising channel

```meta
chunk_id: eval:q09
type: golden pair
tables: fact_ad_performance, dim_ad_channel, dim_ad_placement, dim_product, dim_date
keywords: conversion rate, CTR, coupon clips, impressions, digital mailer, paid social, channel
```

**Question:** "Compare the engagement rate - clicks or coupon clips divided by impressions - of Digital Mailer against Paid Social for Beverages products across FY2025."

**Reasoning target:** Reaching the channel through the placement dimension; `fact_ad_performance` has no direct channel key.

```sql
SELECT chan.channel_type,
       SUM(ad.impressions_count)                AS total_impressions,
       SUM(ad.clicks_or_coupon_clips_count)     AS total_engagements,
       ROUND(100.0 * SUM(ad.clicks_or_coupon_clips_count)
             / NULLIF(SUM(ad.impressions_count), 0), 3) AS engagement_rate_pct
FROM fact_ad_performance ad
JOIN dim_ad_placement place ON place.ad_placement_key = ad.ad_placement_key
JOIN dim_ad_channel   chan  ON chan.ad_channel_key    = place.ad_channel_key
JOIN dim_product      prod  ON prod.product_key       = ad.product_key
JOIN dim_date         d     ON d.date_key             = ad.date_key
WHERE chan.channel_type IN ('Digital Mailer', 'Paid Social')
  AND prod.department_name = 'Beverages'
  AND d.fiscal_year        = 2025
GROUP BY chan.channel_type
ORDER BY chan.channel_type;
```

**Result:** 2 rows: one per channel type, with impressions, engagements and rate.

**Translation note:** Widened from one month to a full fiscal year: Beverages advertising is sparse and no single month carries both channels.

---

# Suite 10 - Syndicated market share

## Q10 - Weekly market share trend for a category in one region

```meta
chunk_id: eval:q10
type: golden pair
tables: fact_market_share_weekly, dim_geography, dim_product, dim_date
keywords: market share, syndicated, region, weekly trend, fan-out, de-duplicate
```

**Question:** "Show our weekly market share percentage for the Cheese category in the Pacific Northwest region across FY2025 Q4."

**Reasoning target:** The five-row fan-out. `fact_market_share_weekly` holds one row per competitor, and `grocer_sales_amount` and `total_market_sales_amount` repeat identically across all five - summing them raw inflates both totals exactly 5x.

```sql
WITH cell AS (
    SELECT DISTINCT m.week_key, m.product_key, m.market_region_key,
           m.grocer_sales_amount, m.total_market_sales_amount
    FROM fact_market_share_weekly m
    JOIN dim_geography geo  ON geo.market_region_key = m.market_region_key
    JOIN dim_product   prod ON prod.product_key      = m.product_key
    JOIN dim_date      d    ON d.date_key            = m.week_key
    WHERE geo.region_name    = 'Pacific Northwest'
      AND prod.category_name = 'Cheese'
      AND d.fiscal_year      = 2025
      AND d.fiscal_quarter   = 4
)
SELECT d.fiscal_week_num,
       ROUND(SUM(cell.grocer_sales_amount), 2)         AS our_sales,
       ROUND(SUM(cell.total_market_sales_amount), 2)   AS total_market_sales,
       ROUND(100.0 * SUM(cell.grocer_sales_amount)
             / NULLIF(SUM(cell.total_market_sales_amount), 0), 2) AS market_share_pct
FROM cell
JOIN dim_date d ON d.date_key = cell.week_key
GROUP BY d.fiscal_week_num
ORDER BY d.fiscal_week_num;
```

**Result:** 13 rows: one per fiscal week, with our sales, market size and share.

**Translation note:** Category remapped to one the syndicated panel actually covers; market share tracks 70 of the 200 products.

---

# Suite 11 - Spoilage and allowance optimization

## Q11 - Spoilage allowance recovered by store

```meta
chunk_id: eval:q11
type: golden pair
tables: fact_vendor_allowances, dim_allowance_type, dim_store, dim_date
keywords: spoilage, allowance, trade funding, recovered, banner, quarter
```

**Question:** "What was the total vendor allowance recovered specifically for SPOILAGE across Heritage Provisions Co-op stores during FY2025 Q4?"

**Reasoning target:** Decoding an allowance type through its lookup table rather than assuming a literal in the fact.

```sql
SELECT store.store_id,
       store.store_name,
       ROUND(SUM(allow.total_allowance_amt), 2) AS recovered_spoilage_dollars
FROM fact_vendor_allowances allow
JOIN dim_allowance_type atype ON atype.allowance_type_key = allow.allowance_type_key
JOIN dim_store          store ON store.store_key          = allow.store_key
JOIN dim_date           d     ON d.date_key               = allow.date_key
WHERE atype.allowance_type_code = 'SPOILAGE'
  AND store.banner_name         = 'Heritage Provisions Co-op'
  AND d.fiscal_year             = 2025
  AND d.fiscal_quarter          = 4
GROUP BY store.store_id, store.store_name
ORDER BY recovered_spoilage_dollars DESC;
```

**Result:** 2 rows: one per store in the banner.

**Translation note:** Banner remapped; the SPOILAGE code itself is real and unchanged.

---

# Suite 12 - Private label versus national brand

## Q12 - Private label versus national brand margin mix

```meta
chunk_id: eval:q12
type: golden pair
tables: fact_pos_retail_sales, fact_item_cogs, dim_product, dim_date
keywords: private label, national brand, margin mix, frozen foods, boolean grouping
```

**Question:** "Compare total units, net revenue and gross margin percentage between private label and national brand products in the Frozen Foods department during fiscal month 12 of FY2025."

**Reasoning target:** Grouping on a boolean flag while still reconciling daily sales to monthly costs.

```sql
WITH monthly_sales AS (
    SELECT d.fiscal_year, d.fiscal_month_num, s.product_key, s.store_key,
           SUM(s.quantity_sold) AS units,
           SUM(s.net_sales_amt) AS net_sales
    FROM fact_pos_retail_sales s
    JOIN dim_date    d ON d.date_key    = s.sales_date_key
    JOIN dim_product p ON p.product_key = s.product_key
    WHERE p.department_name = 'Frozen Foods'
      AND d.fiscal_year = 2025 AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4
),
monthly_cost AS (
    SELECT d.fiscal_year, d.fiscal_month_num, c.product_key, c.store_key,
           AVG(c.net_item_cost) AS unit_cost
    FROM fact_item_cogs c
    JOIN dim_date d ON d.date_key = c.date_key
    WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4
)
SELECT p.is_private_label,
       ROUND(SUM(s.units), 2)                                        AS total_units,
       ROUND(SUM(s.net_sales), 2)                                    AS total_net_sales,
       ROUND(100.0 * (SUM(s.net_sales) - SUM(s.units * c.unit_cost))
             / NULLIF(SUM(s.net_sales), 0), 2)                       AS gross_margin_pct
FROM monthly_sales s
JOIN monthly_cost  c USING (fiscal_year, fiscal_month_num, product_key, store_key)
JOIN dim_product   p ON p.product_key = s.product_key
GROUP BY p.is_private_label
ORDER BY p.is_private_label;
```

**Result:** 2 rows: one per value of `is_private_label`.

**Translation note:** Period changed from a single day to a fiscal month for the same cost-grain reason as Q01.

---

# Suite 13 - Front-page circular efficiency

## Q13 - Sales on days a product was a front-page feature

```meta
chunk_id: eval:q13
type: golden pair
tables: fact_pos_retail_sales, fact_ad_performance, dim_ad_placement, dim_product, dim_date
keywords: front page, circular, feature, ad placement, top products, semi-join
```

**Question:** "List the top 10 products by net sales on days when they were a front-page feature, during fiscal month 5 of FY2025 (29 Jul - 25 Aug 2024)."

**Reasoning target:** Using EXISTS rather than a join. A product can carry several placements on one day, and an inner join to `fact_ad_performance` would count each basket line once per placement.

```sql
SELECT prod.sku_id,
       prod.product_name,
       ROUND(SUM(sales.net_sales_amt), 2) AS net_sales_on_front_page_days
FROM fact_pos_retail_sales sales
JOIN dim_product prod ON prod.product_key = sales.product_key
JOIN dim_date    d    ON d.date_key       = sales.sales_date_key
WHERE d.fiscal_year      = 2025
  AND d.fiscal_month_num = 5
  AND EXISTS (
        SELECT 1
        FROM fact_ad_performance ad
        JOIN dim_ad_placement place ON place.ad_placement_key = ad.ad_placement_key
        WHERE ad.date_key    = sales.sales_date_key
          AND ad.product_key = sales.product_key
          AND ad.store_key   = sales.store_key
          AND place.is_front_page_feature = TRUE
      )
GROUP BY prod.sku_id, prod.product_name
ORDER BY net_sales_on_front_page_days DESC
LIMIT 10;
```

**Result:** 6 rows: SKU and net sales on featured days.

**Translation note:** Period moved to a month that actually contains front-page placements - they run in bursts, not continuously.

---

# Suite 14 - Seasonal promo cycle elasticity

## Q14 - Promotional volume and lift by cycle within a season

```meta
chunk_id: eval:q14
type: golden pair
tables: fact_promo_performance, dim_promo_calendar, dim_product
keywords: promo season, cycle, lift, sub-category, cereal, marketing calendar
```

**Question:** "What were the promotional units sold and the incremental lift for the Cold Cereal sub-category, broken out by each marketing cycle in the Summer Grilling season?"

**Reasoning target:** Rolling the marketing calendar up one level - cycle to season type - with no reference to the fiscal calendar at all.

```sql
SELECT cal.promo_cycle_id,
       cal.promo_cycle_name,
       ROUND(SUM(perf.promo_quantity_sold), 2) AS units_on_promo,
       ROUND(SUM(perf.promo_quantity_lift), 2) AS incremental_units
FROM fact_promo_performance perf
JOIN dim_promo_calendar cal  ON cal.promo_calendar_key = perf.promo_calendar_key
JOIN dim_product        prod ON prod.product_key       = perf.product_key
WHERE cal.promo_season_type     = 'Summer Grilling'
  AND prod.sub_category_name    = 'Cold Cereal'
GROUP BY cal.promo_cycle_id, cal.promo_cycle_name
ORDER BY incremental_units DESC;
```

**Result:** 3 rows: one per Summer Grilling cycle that carries cereal performance.

**Translation note:** Sub-category corrected: 'Cereal' is a category in this hierarchy; its sub-category is 'Cold Cereal'.

---

# Suite 15 - Out-of-stock proxy signals

## Q15 - Out-of-stock proxy: promoted items that never rang

```meta
chunk_id: eval:q15
type: golden pair
tables: fact_item_prices, fact_pos_retail_sales, dim_store, dim_product, dim_date
keywords: out of stock, OOS, proxy, promoted, zero sales, anti-join, week anchor
```

**Question:** "Across FY2025, which promoted items registered no sales at all in the store and week where they carried a promotional price?"

**Reasoning target:** An anti-join across grains: the promotion is a weekly snapshot, the sales are daily, so absence has to be tested over the whole fiscal week rather than on the snapshot date.

```sql
WITH week_anchor AS (
    SELECT fiscal_year, fiscal_week_num, MIN(date_key) AS week_start_key
    FROM dim_date
    GROUP BY fiscal_year, fiscal_week_num
)
SELECT store.store_id,
       w.fiscal_week_num,
       prod.sku_id,
       prod.product_name,
       prices.base_promo_price
FROM fact_item_prices prices
JOIN week_anchor w   ON w.week_start_key = prices.date_key
JOIN dim_store   store ON store.store_key  = prices.store_key
JOIN dim_product prod  ON prod.product_key = prices.product_key
WHERE w.fiscal_year = 2025
  AND prices.base_promo_price IS NOT NULL
  AND NOT EXISTS (
        SELECT 1
        FROM fact_pos_retail_sales s
        JOIN dim_date sd ON sd.date_key = s.sales_date_key
        WHERE s.product_key = prices.product_key
          AND s.store_key   = prices.store_key
          AND sd.fiscal_year     = w.fiscal_year
          AND sd.fiscal_week_num = w.fiscal_week_num
      )
ORDER BY w.fiscal_week_num, store.store_id;
```

**Result:** 7 rows: store, week, SKU and promotional price.

**Translation note:** Broadened from one store-day to the whole fiscal year. The condition is genuinely rare - at most one hit per store-week - so a single-store query returns an empty result that reads like a bug.

---

# Suite 16 - Basket co-occurrence and multi-buy

## Q16 - Basket co-occurrence between two areas of the store

```meta
chunk_id: eval:q16
type: golden pair
tables: fact_pos_retail_sales, dim_product, dim_store
keywords: basket, co-occurrence, affinity, same receipt, cross-department, market basket
```

**Question:** "Of the baskets at store STR001 on 2025-03-31 that contained a Meat & Seafood item, how many also contained a Salty Snacks item?"

**Reasoning target:** Self-joining the POS fact on the degenerate `basket_id`. Counting distinct baskets rather than rows is what keeps a multi-item receipt from being counted twice.

```sql
SELECT COUNT(DISTINCT meat.basket_id) AS baskets_with_both
FROM fact_pos_retail_sales meat
JOIN dim_product meat_prod ON meat_prod.product_key = meat.product_key
JOIN dim_store   store     ON store.store_key       = meat.store_key
WHERE store.store_id           = 'STR001'
  AND meat_prod.department_name = 'Meat & Seafood'
  AND meat.sales_date_key       = 20250331
  AND EXISTS (
        SELECT 1
        FROM fact_pos_retail_sales snack
        JOIN dim_product snack_prod ON snack_prod.product_key = snack.product_key
        WHERE snack.basket_id      = meat.basket_id
          AND snack.sales_date_key = meat.sales_date_key
          AND snack_prod.category_name = 'Salty Snacks'
      );
```

**Result:** 1 row: 3 baskets.

**Translation note:** 'Salty Snacks' is a category here, not a department, and department names differ. `basket_id` identifies a transaction, never a shopper - the original's 'customers who bought' framing is not answerable.

---

## Q17 - Sub-categories most often bought alongside a BOGO item

```meta
chunk_id: eval:q17
type: golden pair
tables: fact_pos_retail_sales, fact_promo_performance, dim_promotion, dim_product, dim_date
keywords: BOGO, mechanic, affinity, co-purchase, basket, sub-category
```

**Question:** "Which 5 sub-categories were most frequently purchased in the same basket as a BOGO-promoted item during fiscal month 1 of FY2025 (1-28 Apr 2024)?"

**Reasoning target:** Isolating qualifying baskets in a CTE before the self-join, so the promotion filter does not multiply the co-purchase counts.

```sql
WITH bogo_baskets AS (
    SELECT DISTINCT s.basket_id, s.sales_date_key
    FROM fact_pos_retail_sales s
    JOIN fact_promo_performance f
           ON f.date_key    = s.sales_date_key
          AND f.product_key = s.product_key
          AND f.store_key   = s.store_key
    JOIN dim_promotion pr ON pr.promotion_key = f.promotion_key
    JOIN dim_date      d  ON d.date_key       = s.sales_date_key
    WHERE pr.mechanic_type   = 'BOGO'
      AND d.fiscal_year      = 2025
      AND d.fiscal_month_num = 1
)
SELECT prod.sub_category_name,
       COUNT(DISTINCT co.basket_id) AS co_purchase_baskets
FROM bogo_baskets b
JOIN fact_pos_retail_sales co
       ON co.basket_id      = b.basket_id
      AND co.sales_date_key = b.sales_date_key
JOIN dim_product prod ON prod.product_key = co.product_key
GROUP BY prod.sub_category_name
ORDER BY co_purchase_baskets DESC
LIMIT 5;
```

**Result:** 5 rows: sub-category and distinct co-purchase baskets.

**Translation note:** Period moved to a month with BOGO activity. The original also excluded a 'Grocery' department that does not exist in this hierarchy.

---

## Q18 - Average basket value by banner

```meta
chunk_id: eval:q18
type: golden pair
tables: fact_pos_retail_sales, dim_store
keywords: basket size, average basket, banner, ticket value, last 30 days
```

**Question:** "What was the average basket value for each store banner over the last 30 days of data (2 - 31 Mar 2025)?"

**Reasoning target:** Dividing a sum by a distinct count across the basket grain - the one place `COUNT(DISTINCT basket_id)` is the correct denominator.

```sql
SELECT store.banner_name,
       COUNT(DISTINCT sales.basket_id)                  AS baskets,
       ROUND(SUM(sales.net_sales_amt), 2)               AS net_sales,
       ROUND(SUM(sales.net_sales_amt)
             / NULLIF(COUNT(DISTINCT sales.basket_id), 0), 2) AS avg_basket_value
FROM fact_pos_retail_sales sales
JOIN dim_store store ON store.store_key = sales.store_key
WHERE sales.sales_date_key BETWEEN 20250302 AND 20250331
GROUP BY store.banner_name
ORDER BY avg_basket_value DESC;
```

**Result:** 4 rows: one per banner.

**Translation note:** Banner names remapped; the date window is anchored to the end of the data rather than to a wall-clock 'today'.

---

# Suite 17 - Stacked trade funding

## Q19 - Net-net procurement cost after two allowance types

```meta
chunk_id: eval:q19
type: golden pair
tables: fact_item_cogs, fact_vendor_allowances, dim_allowance_type, dim_product, dim_date
keywords: net-net cost, scan-back, spoilage, procurement, poultry, allowance stacking
```

**Question:** "Calculate our net-net procurement cost for Poultry items in fiscal month 12 of FY2025, subtracting both SCAN_BACK and SPOILAGE allowances from landed cost."

**Reasoning target:** Filtering allowance types inside the pre-aggregation, then LEFT JOINing so items with no qualifying allowance still appear at full cost.

```sql
WITH targeted_allowance AS (
    SELECT a.date_key, a.product_key, a.store_key,
           SUM(a.allowance_rate_per_unit) AS allowance_per_unit
    FROM fact_vendor_allowances a
    JOIN dim_allowance_type t ON t.allowance_type_key = a.allowance_type_key
    WHERE t.allowance_type_code IN ('SCAN_BACK', 'SPOILAGE')
    GROUP BY 1, 2, 3
)
SELECT prod.sku_id,
       prod.product_name,
       ROUND(AVG(cogs.net_item_cost), 4)                                         AS avg_landed_cost,
       ROUND(AVG(COALESCE(ta.allowance_per_unit, 0)), 4)                         AS avg_allowance_per_unit,
       ROUND(AVG(cogs.net_item_cost - COALESCE(ta.allowance_per_unit, 0)), 4)    AS avg_net_net_cost
FROM fact_item_cogs cogs
JOIN dim_product prod ON prod.product_key = cogs.product_key
JOIN dim_date    d    ON d.date_key       = cogs.date_key
LEFT JOIN targeted_allowance ta
       ON ta.date_key    = cogs.date_key
      AND ta.product_key = cogs.product_key
      AND ta.store_key   = cogs.store_key
WHERE prod.category_name  = 'Poultry'
  AND d.fiscal_year       = 2025
  AND d.fiscal_month_num  = 12
GROUP BY prod.sku_id, prod.product_name
ORDER BY prod.sku_id;
```

**Result:** 5 rows: SKU, landed cost, allowance per unit, net-net cost.

**Translation note:** Scoped to a category rather than a brand - each brand carries at most one poultry item, so a brand filter yields a single row.

---

## Q20 - Vendor collecting the most slotting funding

```meta
chunk_id: eval:q20
type: golden pair
tables: fact_vendor_allowances, dim_vendor, dim_allowance_type, dim_date
keywords: slotting, vendor, trade funding, allowance, top vendor, quarter
```

**Question:** "Which vendor returned the highest total allowance through SLOTTING programs during FY2025 Q4?"

**Reasoning target:** A straightforward top-1 across a five-table star - the control case against the harder allowance questions.

```sql
SELECT vend.vendor_id,
       vend.vendor_name,
       ROUND(SUM(allow.total_allowance_amt), 2) AS total_slotting_collected
FROM fact_vendor_allowances allow
JOIN dim_vendor         vend  ON vend.vendor_key         = allow.vendor_key
JOIN dim_allowance_type atype ON atype.allowance_type_key = allow.allowance_type_key
JOIN dim_date           d     ON d.date_key              = allow.date_key
WHERE atype.allowance_type_code = 'SLOTTING'
  AND d.fiscal_year    = 2025
  AND d.fiscal_quarter = 4
GROUP BY vend.vendor_id, vend.vendor_name
ORDER BY total_slotting_collected DESC
LIMIT 1;
```

**Result:** 1 row: vendor ID, name and total collected.

**Translation note:** Fiscal quarter re-anchored to the years this data covers.

---

## Q21 - Items where trade funding exceeded register profit

```meta
chunk_id: eval:q21
type: golden pair
tables: fact_pos_retail_sales, fact_item_cogs, fact_vendor_allowances, dim_product, dim_date
keywords: trade funding, allowance, gross profit, exceeds, margin, profitability
```

**Question:** "Which items collected more in vendor allowances during fiscal month 12 of FY2025 than they earned in gross profit at the register?"

**Reasoning target:** Three facts at two grains, each pre-aggregated independently, compared in a HAVING clause. The allowance join must be LEFT so unfunded items are not silently dropped before the comparison.

```sql
WITH monthly_sales AS (
    SELECT d.fiscal_year, d.fiscal_month_num, s.product_key, s.store_key,
           SUM(s.net_sales_amt) AS net_sales,
           SUM(s.quantity_sold) AS units
    FROM fact_pos_retail_sales s
    JOIN dim_date d ON d.date_key = s.sales_date_key
    WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4
),
monthly_cost AS (
    SELECT d.fiscal_year, d.fiscal_month_num, c.product_key, c.store_key,
           AVG(c.net_item_cost) AS unit_cost
    FROM fact_item_cogs c
    JOIN dim_date d ON d.date_key = c.date_key
    WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4
),
monthly_allowance AS (
    SELECT d.fiscal_year, d.fiscal_month_num, a.product_key, a.store_key,
           SUM(a.total_allowance_amt) AS trade_funding
    FROM fact_vendor_allowances a
    JOIN dim_date d ON d.date_key = a.date_key
    WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4
)
SELECT p.sku_id,
       p.product_name,
       ROUND(SUM(COALESCE(al.trade_funding, 0)), 2)                       AS trade_funding,
       ROUND(SUM(s.net_sales) - SUM(s.units * c.unit_cost), 2)            AS pos_gross_profit
FROM monthly_sales s
JOIN monthly_cost  c  USING (fiscal_year, fiscal_month_num, product_key, store_key)
LEFT JOIN monthly_allowance al USING (fiscal_year, fiscal_month_num, product_key, store_key)
JOIN dim_product p ON p.product_key = s.product_key
GROUP BY p.sku_id, p.product_name
HAVING SUM(COALESCE(al.trade_funding, 0))
       > SUM(s.net_sales) - SUM(s.units * c.unit_cost)
ORDER BY trade_funding DESC;
```

**Result:** 37 rows: SKU, trade funding and POS gross profit.

**Translation note:** The original joined allowances directly to daily sales, which matches on only 24 days of the year.

---

# Suite 18 - Competitive pricing elasticity

## Q22 - Price index for private label in one state

```meta
chunk_id: eval:q22
type: golden pair
tables: fact_item_prices, fact_competitor_pricing, dim_product, dim_store, dim_competitor
keywords: price index, private label, state, competitor, category, positioning
```

**Question:** "What is our price index for private label products against ValuMax Foods (COMP05) at our Wisconsin stores, by category, for the week beginning 2025-03-24?"

**Reasoning target:** Layering a product attribute filter, a store geography filter and a competitor filter over the weekly price pair.

```sql
SELECT prod.category_name,
       COUNT(*)                                          AS items_compared,
       ROUND(AVG(ours.regular_retail_price), 4)          AS our_avg_price,
       ROUND(AVG(comp.comp_regular_price), 4)            AS competitor_avg_price,
       ROUND(100.0 * AVG(ours.regular_retail_price)
             / NULLIF(AVG(comp.comp_regular_price), 0), 1) AS price_index_score
FROM fact_item_prices ours
JOIN dim_product prod  ON prod.product_key = ours.product_key
JOIN dim_store   store ON store.store_key  = ours.store_key
JOIN fact_competitor_pricing comp
       ON comp.date_key    = ours.date_key
      AND comp.product_key = ours.product_key
      AND comp.store_key   = ours.store_key
JOIN dim_competitor rival ON rival.competitor_key = comp.competitor_key
WHERE prod.is_private_label = TRUE
  AND rival.competitor_id   = 'COMP05'
  AND store.state_code      = 'WI'
  AND ours.date_key         = 20250324
GROUP BY prod.category_name
ORDER BY price_index_score DESC;
```

**Result:** 11 rows: one per category, with both averages and the index.

**Translation note:** State changed to one that exists (WA is not in this data) and that has a store inside the four-store competitor survey.

---

## Q23 - Competitors promoting below our landed cost

```meta
chunk_id: eval:q23
type: golden pair
tables: fact_competitor_pricing, fact_item_cogs, fact_item_prices, dim_store, dim_product, dim_competitor
keywords: price war, below cost, promo price, competitor, loss leader, price match
```

**Question:** "Where did a competitor promote an item below our own landed cost during fiscal month 12 of FY2025, and did we match the price?"

**Reasoning target:** Bridging a weekly fact to a monthly fact on fiscal month, then LEFT JOINing our own weekly promotional price so a non-response is visible as NULL rather than dropped.

```sql
WITH monthly_cost AS (
    SELECT d.fiscal_year, d.fiscal_month_num, c.product_key, c.store_key,
           AVG(c.net_item_cost) AS unit_cost
    FROM fact_item_cogs c
    JOIN dim_date d ON d.date_key = c.date_key
    GROUP BY 1, 2, 3, 4
)
SELECT store.store_id,
       rival.competitor_name,
       prod.sku_id,
       prod.product_name,
       ROUND(mc.unit_cost, 4)        AS our_landed_cost,
       comp.comp_promo_price         AS competitor_promo_price,
       ours.base_promo_price         AS our_promo_price
FROM fact_competitor_pricing comp
JOIN dim_date    cd    ON cd.date_key       = comp.date_key
JOIN dim_store   store ON store.store_key   = comp.store_key
JOIN dim_product prod  ON prod.product_key  = comp.product_key
JOIN dim_competitor rival ON rival.competitor_key = comp.competitor_key
JOIN monthly_cost mc
       ON mc.fiscal_year      = cd.fiscal_year
      AND mc.fiscal_month_num = cd.fiscal_month_num
      AND mc.product_key      = comp.product_key
      AND mc.store_key        = comp.store_key
LEFT JOIN fact_item_prices ours
       ON ours.date_key    = comp.date_key
      AND ours.product_key = comp.product_key
      AND ours.store_key   = comp.store_key
WHERE cd.fiscal_year      = 2025
  AND cd.fiscal_month_num = 12
  AND comp.comp_promo_price IS NOT NULL
  AND comp.comp_promo_price < mc.unit_cost
ORDER BY (mc.unit_cost - comp.comp_promo_price) DESC
LIMIT 15;
```

**Result:** 15 rows - and `our_promo_price` is NULL throughout: we never matched.

**Translation note:** The original required an exact price match as a filter. No such row exists, so the condition is moved into the SELECT where the absence of a match is itself the finding.

---

## Q24 - Competitor banner with the highest tracked price

```meta
chunk_id: eval:q24
type: golden pair
tables: fact_competitor_pricing, fact_market_share_weekly, dim_competitor, dim_product, dim_geography
keywords: competitor banner, tracked price, baby care, region, premium positioning
```

**Question:** "Which competitor banner logs the highest average price on Baby Care items among the products tracked in the Tristate Metro market region?"

**Reasoning target:** Using a region as a product filter rather than a join path. Nothing maps a store to a market region, so the region can only qualify *which products* are in scope.

```sql
SELECT rival.banner_name                          AS competitor_banner,
       COUNT(*)                                   AS observations,
       ROUND(AVG(comp.comp_regular_price), 4)     AS avg_tracked_price
FROM fact_competitor_pricing comp
JOIN dim_competitor rival ON rival.competitor_key = comp.competitor_key
JOIN dim_product    prod  ON prod.product_key     = comp.product_key
WHERE prod.category_name = 'Baby Care'
  AND comp.date_key      = 20250324
  AND EXISTS (
        SELECT 1
        FROM fact_market_share_weekly m
        JOIN dim_geography geo ON geo.market_region_key = m.market_region_key
        WHERE m.product_key  = comp.product_key
          AND geo.region_name = 'Tristate Metro'
      )
GROUP BY rival.banner_name
ORDER BY avg_tracked_price DESC;
```

**Result:** 3 rows: one per surveyed banner.

**Translation note:** The original joined competitor pricing to market share to obtain a region, producing a large fan-out and an answer that implied a store-to-region link this schema does not have.

---

# Suite 19 - Market share shifts and leakage

## Q25 - Categories losing ground while the market grew

```meta
chunk_id: eval:q25
type: golden pair
tables: fact_market_share_weekly, dim_product, dim_date
keywords: market share loss, leakage, category, year over year, share shift, de-duplicate
```

**Question:** "Which categories saw our sales fall from FY2024 to FY2025 while the total market for them grew?"

**Reasoning target:** A year-over-year self-join on a de-duplicated aggregate, with the divergence expressed as two growth rates rather than one blended number.

```sql
WITH cell AS (
    SELECT DISTINCT m.week_key, m.product_key, m.market_region_key,
           m.grocer_sales_amount, m.total_market_sales_amount
    FROM fact_market_share_weekly m
),
yearly AS (
    SELECT p.category_name, d.fiscal_year,
           SUM(cell.grocer_sales_amount)       AS our_sales,
           SUM(cell.total_market_sales_amount) AS market_sales
    FROM cell
    JOIN dim_date    d ON d.date_key    = cell.week_key
    JOIN dim_product p ON p.product_key = cell.product_key
    GROUP BY 1, 2
)
SELECT fy24.category_name,
       ROUND(fy24.our_sales, 2)    AS our_sales_fy2024,
       ROUND(fy25.our_sales, 2)    AS our_sales_fy2025,
       ROUND(fy24.market_sales, 2) AS market_sales_fy2024,
       ROUND(fy25.market_sales, 2) AS market_sales_fy2025,
       ROUND(100.0 * (fy25.our_sales - fy24.our_sales)
             / NULLIF(fy24.our_sales, 0), 2)       AS our_growth_pct,
       ROUND(100.0 * (fy25.market_sales - fy24.market_sales)
             / NULLIF(fy24.market_sales, 0), 2)    AS market_growth_pct
FROM yearly fy24
JOIN yearly fy25 ON fy25.category_name = fy24.category_name
                AND fy24.fiscal_year = 2024
                AND fy25.fiscal_year = 2025
WHERE fy25.our_sales    < fy24.our_sales
  AND fy25.market_sales > fy24.market_sales
ORDER BY our_growth_pct;
```

**Result:** 6 rows: category with both years' figures and both growth rates.

**Translation note:** Rewritten. The original's `HAVING SUM(...) < 0` can never be true on non-negative sales; it did not express the question it was asking.

---

## Q26 - Week-over-week market share velocity

```meta
chunk_id: eval:q26
type: golden pair
tables: fact_market_share_weekly, dim_product, dim_geography, dim_date
keywords: velocity, week over week, LAG, window function, market share, brand, region
```

**Question:** "Calculate the week-over-week change in market share, in percentage points, for Silver Creek Co. products in the Pacific Northwest across the most recent three tracked weeks."

**Reasoning target:** Computing the share inside a CTE before applying LAG. Nesting a window function over an aggregate of a fan-out-prone table is where this one usually goes wrong.

```sql
WITH cell AS (
    SELECT DISTINCT m.week_key, m.product_key, m.market_region_key,
           m.grocer_sales_amount, m.total_market_sales_amount
    FROM fact_market_share_weekly m
    JOIN dim_product   prod ON prod.product_key      = m.product_key
    JOIN dim_geography geo  ON geo.market_region_key = m.market_region_key
    JOIN dim_date      d    ON d.date_key            = m.week_key
    WHERE prod.brand_name  = 'Silver Creek Co.'
      AND geo.region_name  = 'Pacific Northwest'
      AND d.fiscal_year    = 2025
),
weekly AS (
    SELECT d.fiscal_week_num,
           100.0 * SUM(cell.grocer_sales_amount)
                 / NULLIF(SUM(cell.total_market_sales_amount), 0) AS market_share_pct
    FROM cell
    JOIN dim_date d ON d.date_key = cell.week_key
    GROUP BY d.fiscal_week_num
)
SELECT fiscal_week_num,
       ROUND(market_share_pct, 3) AS market_share_pct,
       ROUND(market_share_pct - LAG(market_share_pct) OVER (ORDER BY fiscal_week_num), 3)
           AS share_change_pts
FROM weekly
ORDER BY fiscal_week_num DESC
LIMIT 3;
```

**Result:** 3 rows: fiscal week, share, and change in points.

**Translation note:** Brand remapped to one the syndicated panel covers.

---

## Q27 - Competitor with the largest volume in a market

```meta
chunk_id: eval:q27
type: golden pair
tables: fact_market_share_weekly, dim_competitor, dim_product, dim_geography, dim_date
keywords: competitor sales, market region, DMA, syndicated market code, dairy
```

**Question:** "Which competitor captured the largest dollar volume in the Dairy & Eggs department inside syndicated market DMA-335 during FY2025?"

**Reasoning target:** Knowing which market-share column survives the fan-out. `competitor_sales_amount` varies per row and is safe to sum; the other two are not.

```sql
SELECT rival.competitor_id,
       rival.competitor_name,
       ROUND(SUM(m.competitor_sales_amount), 2) AS competitor_sales
FROM fact_market_share_weekly m
JOIN dim_competitor rival ON rival.competitor_key     = m.competitor_key
JOIN dim_product    prod  ON prod.product_key         = m.product_key
JOIN dim_geography  geo   ON geo.market_region_key    = m.market_region_key
JOIN dim_date       d     ON d.date_key               = m.week_key
WHERE prod.department_name      = 'Dairy & Eggs'
  AND geo.syndicated_market_code = 'DMA-335'
  AND d.fiscal_year             = 2025
GROUP BY rival.competitor_id, rival.competitor_name
ORDER BY competitor_sales DESC
LIMIT 1;
```

**Result:** 1 row: the winning competitor.

**Translation note:** Market code reformatted - codes are `DMA-nnn` with a hyphen.

---

# Suite 20 - Advertising efficiency and placement

## Q28 - Spend and coupon clips for front-page features

```meta
chunk_id: eval:q28
type: golden pair
tables: fact_ad_performance, dim_ad_placement, dim_date
keywords: ad spend, coupon clips, front page, ad theme, cost per clip
```

**Question:** "What was the total ad spend and the total coupon clips generated by front-page features during fiscal month 5 of FY2025, broken out by ad theme?"

**Reasoning target:** Grouping on placement metadata rather than on product or channel, and deriving a cost-per-clip from two independent sums.

```sql
SELECT place.ad_theme_name,
       ROUND(SUM(ad.ad_spend_amount), 2)         AS campaign_spend,
       SUM(ad.clicks_or_coupon_clips_count)      AS coupon_clips,
       ROUND(SUM(ad.ad_spend_amount)
             / NULLIF(SUM(ad.clicks_or_coupon_clips_count), 0), 4) AS cost_per_clip
FROM fact_ad_performance ad
JOIN dim_ad_placement place ON place.ad_placement_key = ad.ad_placement_key
JOIN dim_date         d     ON d.date_key             = ad.date_key
WHERE place.is_front_page_feature = TRUE
  AND d.fiscal_year      = 2025
  AND d.fiscal_month_num = 5
GROUP BY place.ad_theme_name
ORDER BY campaign_spend DESC;
```

**Result:** 3 rows: one per front-page ad theme.

**Translation note:** Period moved to a month containing front-page placements.

---

## Q29 - Ranking creative versions by cost per click

```meta
chunk_id: eval:q29
type: golden pair
tables: fact_ad_performance, dim_ad_placement, dim_ad_channel, dim_date
keywords: cost per click, CPC, creative version, paid social, ranking, efficiency
```

**Question:** "Rank our Paid Social creative versions by cost per click across FY2025."

**Reasoning target:** A ratio of sums, not an average of ratios - the two give different answers whenever spend is unevenly distributed.

```sql
SELECT place.creative_version_code,
       ROUND(SUM(ad.ad_spend_amount), 2)    AS total_spend,
       SUM(ad.clicks_or_coupon_clips_count) AS total_clicks,
       ROUND(SUM(ad.ad_spend_amount)
             / NULLIF(SUM(ad.clicks_or_coupon_clips_count), 0), 4) AS cost_per_click
FROM fact_ad_performance ad
JOIN dim_ad_placement place ON place.ad_placement_key = ad.ad_placement_key
JOIN dim_ad_channel   chan  ON chan.ad_channel_key    = place.ad_channel_key
JOIN dim_date         d     ON d.date_key             = ad.date_key
WHERE chan.channel_type = 'Paid Social'
  AND d.fiscal_year      = 2025
GROUP BY place.creative_version_code
ORDER BY cost_per_click;
```

**Result:** 6 rows: creative version, spend, clicks and CPC.

**Translation note:** Widened from a 14-day window: Paid Social activity stops on 2025-02-05, and the last two weeks of data contain only one creative version.

---

## Q30 - Impressions against private label in small-format stores

```meta
chunk_id: eval:q30
type: golden pair
tables: fact_ad_performance, dim_product, dim_store, dim_date
keywords: impressions, private label, store format, layout, urban, footprint
```

**Question:** "What was the total advertising impression footprint against private label products in Urban Small Format stores during fiscal month 7 of FY2025 (30 Sep - 27 Oct 2024)?"

**Reasoning target:** Filtering on a store attribute and a product attribute at once, with no dimension bridging either.

```sql
SELECT prod.sku_id,
       prod.product_name,
       SUM(ad.impressions_count) AS total_impressions
FROM fact_ad_performance ad
JOIN dim_product prod  ON prod.product_key = ad.product_key
JOIN dim_store   store ON store.store_key  = ad.store_key
JOIN dim_date    d     ON d.date_key       = ad.date_key
WHERE prod.is_private_label   = TRUE
  AND store.layout_type_desc  = 'Urban Small Format'
  AND d.fiscal_year      = 2025
  AND d.fiscal_month_num = 7
GROUP BY prod.sku_id, prod.product_name
ORDER BY total_impressions DESC;
```

**Result:** 4 rows: SKU and impressions.

**Translation note:** Layout renamed to 'Urban Small Format' and the window moved to a month where private label ads ran in that format.

---

# Suite 21 - Decoupled marketing calendar alignment

## Q31 - Promo season type with the highest volume

```meta
chunk_id: eval:q31
type: golden pair
tables: fact_promo_performance, dim_promo_calendar, dim_product, dim_date
keywords: promo season, highest volume, department, marketing calendar, dual calendar
```

**Question:** "Which promo season type drove the highest promotional volume in our Dairy & Eggs department during FY2025?"

**Reasoning target:** Filtering on the fiscal calendar while grouping on the marketing calendar - both keys hang off the same fact and each serves a different half of the question.

```sql
SELECT cal.promo_season_type,
       ROUND(SUM(perf.promo_quantity_sold), 2) AS units_on_promo,
       ROUND(SUM(perf.promo_quantity_lift), 2) AS incremental_units
FROM fact_promo_performance perf
JOIN dim_promo_calendar cal  ON cal.promo_calendar_key = perf.promo_calendar_key
JOIN dim_product        prod ON prod.product_key       = perf.product_key
JOIN dim_date           d    ON d.date_key             = perf.date_key
WHERE prod.department_name = 'Dairy & Eggs'
  AND d.fiscal_year        = 2025
GROUP BY cal.promo_season_type
ORDER BY units_on_promo DESC
LIMIT 1;
```

**Result:** 1 row: the winning season type.

**Translation note:** Department renamed; 'operational year 2026' re-anchored to FY2025.

---

## Q32 - Products with the weakest lift in a cycle

```meta
chunk_id: eval:q32
type: golden pair
tables: fact_promo_performance, dim_promo_calendar, dim_product
keywords: lift, weak performance, cannibalization, promo cycle, incremental units
```

**Question:** "In promo cycle PROMO_2025_WK15_007, which products converted the least of their promotional volume into incremental lift?"

**Reasoning target:** Normalising lift as a share of promotional volume, so a large item and a small item can be compared on the same scale.

```sql
SELECT prod.sku_id,
       prod.product_name,
       ROUND(SUM(perf.promo_quantity_sold), 2) AS units_on_promo,
       ROUND(SUM(perf.promo_quantity_lift), 2) AS incremental_units,
       ROUND(100.0 * SUM(perf.promo_quantity_lift)
             / NULLIF(SUM(perf.promo_quantity_sold), 0), 2) AS lift_share_of_promo_volume
FROM fact_promo_performance perf
JOIN dim_promo_calendar cal  ON cal.promo_calendar_key = perf.promo_calendar_key
JOIN dim_product        prod ON prod.product_key       = perf.product_key
WHERE cal.promo_cycle_id = 'PROMO_2025_WK15_007'
GROUP BY prod.sku_id, prod.product_name
ORDER BY lift_share_of_promo_volume ASC
LIMIT 10;
```

**Result:** 10 rows: SKU, promo units, lift units and lift share.

**Translation note:** Rewritten. The original looked for negative lift; `promo_quantity_lift` is strictly positive in this data (minimum 0.495), so that query can only ever return nothing. Weakest-relative-lift is the answerable form of the same business question.

---

## Q33 - Longest cycle in a promotional season

```meta
chunk_id: eval:q33
type: golden pair
tables: dim_promo_calendar
keywords: cycle duration, longest, holiday, promo calendar, date arithmetic
```

**Question:** "What was the longest promo cycle in the Holiday Season framework, and how many days did it run?"

**Reasoning target:** Date subtraction inside a single dimension - no fact table is needed, which is itself the thing to recognise.

```sql
SELECT promo_cycle_id,
       promo_cycle_name,
       cycle_start_date,
       cycle_end_date,
       (cycle_end_date - cycle_start_date) AS cycle_duration_days
FROM dim_promo_calendar
WHERE promo_season_type = 'Holiday Season'
ORDER BY cycle_duration_days DESC
LIMIT 1;
```

**Result:** 1 row: the 14-day 'Holiday Season - Phase 2' cycle.

**Translation note:** Season type renamed from 'Holiday' to 'Holiday Season'.

---

# Suite 22 - Multi-store variance and geography

## Q34 - Net sales per square foot by store

```meta
chunk_id: eval:q34
type: golden pair
tables: fact_pos_retail_sales, dim_store
keywords: sales per square foot, productivity, store size, city, density
```

**Question:** "Calculate net sales per square foot for each store in Madison on the most recent business day, 2025-03-31."

**Reasoning target:** Dividing an additive measure by a non-additive store attribute. `MAX(square_footage)` inside the group is what stops the footage being summed across rows.

```sql
SELECT store.store_id,
       store.store_name,
       store.square_footage,
       ROUND(SUM(sales.net_sales_amt), 2) AS net_sales,
       ROUND(SUM(sales.net_sales_amt) / NULLIF(MAX(store.square_footage), 0), 4)
           AS net_sales_per_sq_ft
FROM fact_pos_retail_sales sales
JOIN dim_store store ON store.store_key = sales.store_key
WHERE store.city            = 'Madison'
  AND sales.sales_date_key  = 20250331
GROUP BY store.store_id, store.store_name, store.square_footage
ORDER BY net_sales_per_sq_ft DESC;
```

**Result:** 2 rows: the two Madison stores.

**Translation note:** City remapped to one with more than one store, so the comparison the question implies is possible.

---

## Q35 - State with the highest markdown concessions

```meta
chunk_id: eval:q35
type: golden pair
tables: fact_pos_retail_sales, dim_store, dim_date
keywords: markdown, state, geography, concessions, weekly, top state
```

**Question:** "Which state generated the highest total markdown discount during fiscal week 52 of FY2025?"

**Reasoning target:** A plain geographic rollup - the baseline case for the harder geography questions around it.

```sql
SELECT store.state_code,
       ROUND(SUM(sales.markdown_discount_amt), 2) AS total_markdowns
FROM fact_pos_retail_sales sales
JOIN dim_store store ON store.store_key = sales.store_key
JOIN dim_date  d     ON d.date_key      = sales.sales_date_key
WHERE d.fiscal_year     = 2025
  AND d.fiscal_week_num = 52
GROUP BY store.state_code
ORDER BY total_markdowns DESC
LIMIT 1;
```

**Result:** 1 row: Connecticut.

**Translation note:** `nrf_454_week_num` replaced with `fiscal_week_num`; the two columns hold identical values in all 731 rows.

---

## Q36 - Large stores ranked by recent sales

```meta
chunk_id: eval:q36
type: golden pair
tables: fact_pos_retail_sales, dim_store
keywords: square footage, large stores, contraction, ranking, last 30 days
```

**Question:** "Among stores larger than 35,000 square feet, which recorded the weakest net sales over the last 30 days of data (2 - 31 Mar 2025)?"

**Reasoning target:** Applying a dimension threshold before aggregating, and reading 'weakest' as an ascending order rather than a filter.

```sql
SELECT store.store_id,
       store.store_name,
       store.square_footage,
       ROUND(SUM(sales.net_sales_amt), 2) AS net_sales_last_30_days
FROM fact_pos_retail_sales sales
JOIN dim_store store ON store.store_key = sales.store_key
WHERE store.square_footage > 35000
  AND sales.sales_date_key BETWEEN 20250302 AND 20250331
GROUP BY store.store_id, store.store_name, store.square_footage
ORDER BY net_sales_last_30_days ASC;
```

**Result:** 4 rows, weakest first.

**Translation note:** Threshold lowered from 80,000 sq ft: the largest store in this chain is 45,639 sq ft, so the original filter excludes every store.

---

# Suite 23 - Time-series window functions

## Q37 - Seven-day rolling average of daily sales

```meta
chunk_id: eval:q37
type: golden pair
tables: fact_pos_retail_sales, dim_store, dim_product, dim_date
keywords: rolling average, moving average, window frame, time series, bakery, daily
```

**Question:** "Using a 7-day rolling window, show the moving average of daily net sales for the Bakery department at store STR001 through fiscal month 12 of FY2025."

**Reasoning target:** A window function applied over an aggregate - `AVG(SUM(...)) OVER (...)` with an explicit ROWS frame, which is the form that trips up most generated SQL.

```sql
SELECT d.calendar_date,
       ROUND(SUM(sales.net_sales_amt), 2) AS daily_net_sales,
       ROUND(AVG(SUM(sales.net_sales_amt)) OVER (
                 ORDER BY d.calendar_date
                 ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS rolling_7_day_avg
FROM fact_pos_retail_sales sales
JOIN dim_store   store ON store.store_key   = sales.store_key
JOIN dim_product prod  ON prod.product_key  = sales.product_key
JOIN dim_date    d     ON d.date_key        = sales.sales_date_key
WHERE store.store_id       = 'STR001'
  AND prod.department_name = 'Bakery'
  AND d.fiscal_year        = 2025
  AND d.fiscal_month_num   = 12
GROUP BY d.calendar_date
ORDER BY d.calendar_date;
```

**Result:** 35 rows: one per day in the fiscal month.

**Translation note:** Store remapped; 'September 2026' re-anchored to the last fiscal month in the data.

---

## Q38 - Ranking categories within their department

```meta
chunk_id: eval:q38
type: golden pair
tables: fact_pos_retail_sales, dim_product
keywords: rank, window function, partition, category, department, contribution
```

**Question:** "Rank each product category by net sales within its own department for 2025-03-31, and show each category's share of its department."

**Reasoning target:** PARTITION BY on two different window functions at once - RANK for position and a partitioned SUM for the denominator of the share.

```sql
SELECT prod.department_name,
       prod.category_name,
       ROUND(SUM(sales.net_sales_amt), 2) AS category_net_sales,
       RANK() OVER (PARTITION BY prod.department_name
                    ORDER BY SUM(sales.net_sales_amt) DESC) AS rank_in_department,
       ROUND(100.0 * SUM(sales.net_sales_amt)
             / SUM(SUM(sales.net_sales_amt)) OVER (PARTITION BY prod.department_name), 2)
           AS pct_of_department
FROM fact_pos_retail_sales sales
JOIN dim_product prod ON prod.product_key = sales.product_key
WHERE sales.sales_date_key = 20250331
GROUP BY prod.department_name, prod.category_name
ORDER BY prod.department_name, rank_in_department;
```

**Result:** 33 rows: every category with its rank and department share.

**Translation note:** Date re-anchored to the most recent business day. The share column is added because the original mentioned a contribution percentage but never computed one.

---

## Q39 - Day-over-day revenue growth for one store

```meta
chunk_id: eval:q39
type: golden pair
tables: fact_pos_retail_sales, dim_store, dim_date
keywords: day over day, growth, LAG, window function, trend, store
```

**Question:** "Show the day-over-day net revenue growth for store STR005 across fiscal weeks 48 and 49 of FY2025 (24 Feb - 9 Mar 2025)."

**Reasoning target:** Repeating LAG inside an arithmetic expression, and guarding the division so the first day yields NULL rather than an error.

```sql
SELECT d.calendar_date,
       ROUND(SUM(sales.net_sales_amt), 2) AS net_sales,
       ROUND(LAG(SUM(sales.net_sales_amt)) OVER (ORDER BY d.calendar_date), 2) AS prior_day_net_sales,
       ROUND(100.0 * (SUM(sales.net_sales_amt)
                      - LAG(SUM(sales.net_sales_amt)) OVER (ORDER BY d.calendar_date))
             / NULLIF(LAG(SUM(sales.net_sales_amt)) OVER (ORDER BY d.calendar_date), 0), 2)
           AS day_over_day_growth_pct
FROM fact_pos_retail_sales sales
JOIN dim_store store ON store.store_key = sales.store_key
JOIN dim_date  d     ON d.date_key      = sales.sales_date_key
WHERE store.store_id = 'STR005'
  AND d.fiscal_year     = 2025
  AND d.fiscal_week_num IN (48, 49)
GROUP BY d.calendar_date
ORDER BY d.calendar_date;
```

**Result:** 14 rows: one per day, the first with a NULL growth rate.

**Translation note:** Store and window re-anchored; the period is expressed in fiscal weeks so it lines up with how the rest of the calendar is sliced.

---

# Suite 24 - Operations and valuation audits

## Q40 - Integrity audit of promotional pricing

```meta
chunk_id: eval:q40
type: golden pair
tables: fact_item_prices
keywords: audit, data quality, integrity, promo price, misconfiguration, validation
```

**Question:** "Are there any rows in the pricing fact where the promotional price was configured above the regular shelf price?"

**Reasoning target:** Returning a defensible answer to an audit question. A bare row-returning query answers 'no violations' with an empty result, which is indistinguishable from a broken query; counting makes the clean bill of health explicit.

```sql
SELECT COUNT(*)                                                     AS rows_checked,
       COUNT(*) FILTER (WHERE prices.base_promo_price IS NOT NULL)  AS rows_on_promotion,
       COUNT(*) FILTER (WHERE prices.base_promo_price
                              > prices.regular_retail_price)        AS promo_above_regular_violations
FROM fact_item_prices prices;
```

**Result:** 1 row: 179,400 rows checked, 11,975 on promotion, 0 violations.

**Translation note:** Reshaped from a row listing into a count. Also worth remembering that a NULL `base_promo_price` means no promotion, not a price of zero.

---

## Q41 - Freight as a share of landed cost

```meta
chunk_id: eval:q41
type: golden pair
tables: fact_item_cogs, dim_product, dim_date
keywords: freight, landed cost, overhead, cost structure, brand, percentage
```

**Question:** "Which brands carry the highest freight overhead as a percentage of landed cost in fiscal month 12 of FY2025?"

**Reasoning target:** Composing a ratio from two averages taken over the same rows, and recognising that landed cost already contains the freight being measured.

```sql
SELECT prod.brand_name,
       ROUND(AVG(cogs.base_cost), 4)     AS avg_base_cost,
       ROUND(AVG(cogs.freight_cost), 4)  AS avg_freight_cost,
       ROUND(AVG(cogs.net_item_cost), 4) AS avg_landed_cost,
       ROUND(100.0 * AVG(cogs.freight_cost)
             / NULLIF(AVG(cogs.net_item_cost), 0), 2) AS freight_pct_of_landed_cost
FROM fact_item_cogs cogs
JOIN dim_product prod ON prod.product_key = cogs.product_key
JOIN dim_date    d    ON d.date_key       = cogs.date_key
WHERE d.fiscal_year      = 2025
  AND d.fiscal_month_num = 12
GROUP BY prod.brand_name
ORDER BY freight_pct_of_landed_cost DESC
LIMIT 10;
```

**Result:** 10 rows: brand with base, freight, landed cost and freight share.

**Translation note:** The original filtered `brand_name LIKE '%Import%'`; no such brand exists, so the question is turned around to rank brands by the metric instead.

---

## Q42 - Store-days with unusually high markdowns

```meta
chunk_id: eval:q42
type: golden pair
tables: fact_pos_retail_sales, dim_store, dim_date
keywords: markdown outlier, exception, store day, threshold, HAVING, anomaly
```

**Question:** "Which store-days in FY2025 recorded unusually high markdown totals, over $150?"

**Reasoning target:** Aggregating to a compound grain and filtering the aggregate with HAVING rather than WHERE.

```sql
SELECT store.store_id,
       store.store_name,
       d.calendar_date,
       ROUND(SUM(sales.markdown_discount_amt), 2) AS markdown_total,
       COUNT(DISTINCT sales.basket_id)            AS baskets
FROM fact_pos_retail_sales sales
JOIN dim_store store ON store.store_key = sales.store_key
JOIN dim_date  d     ON d.date_key      = sales.sales_date_key
WHERE d.fiscal_year = 2025
GROUP BY store.store_id, store.store_name, d.calendar_date
HAVING SUM(sales.markdown_discount_amt) > 150.00
ORDER BY markdown_total DESC
LIMIT 10;
```

**Result:** 10 rows: store, date, markdown total and basket count.

**Translation note:** Reframed. The original split `basket_id` to recover a checkout lane; `basket_id` is `BSK` plus a 10-digit sequence and encodes no lane, register or shopper, so lane-level analysis is not possible here - store-day is the finest grain that is. The threshold is also scaled to this data, where the largest single store-day markdown is $304.

---

# Suite 25 - Brand loyalty and market penetration

## Q43 - Brand penetration within a department

```meta
chunk_id: eval:q43
type: golden pair
tables: fact_pos_retail_sales, dim_product, dim_store, dim_date
keywords: brand share, penetration, conditional aggregate, FILTER, department, store
```

**Question:** "What share of Dairy & Eggs revenue at store STR001 did Homestead Select capture in fiscal week 52 of FY2025?"

**Reasoning target:** Conditional aggregation - a FILTER clause (or a CASE) gives the numerator and the denominator from a single pass over the same rows.

```sql
SELECT ROUND(SUM(sales.net_sales_amt) FILTER (WHERE prod.brand_name = 'Homestead Select'), 2)
           AS brand_net_sales,
       ROUND(SUM(sales.net_sales_amt), 2) AS department_net_sales,
       ROUND(100.0 * SUM(sales.net_sales_amt) FILTER (WHERE prod.brand_name = 'Homestead Select')
             / NULLIF(SUM(sales.net_sales_amt), 0), 2) AS brand_share_of_department_pct
FROM fact_pos_retail_sales sales
JOIN dim_product prod  ON prod.product_key = sales.product_key
JOIN dim_store   store ON store.store_key  = sales.store_key
JOIN dim_date    d     ON d.date_key       = sales.sales_date_key
WHERE store.store_id       = 'STR001'
  AND prod.department_name = 'Dairy & Eggs'
  AND d.fiscal_year        = 2025
  AND d.fiscal_week_num    = 52;
```

**Result:** 1 row: brand revenue, department revenue and the share.

**Translation note:** Brand, department and store all remapped.

---

## Q44 - Private label revenue among regionally tracked products

```meta
chunk_id: eval:q44
type: golden pair
tables: fact_pos_retail_sales, fact_market_share_weekly, dim_product, dim_geography
keywords: private label, region, tracked products, revenue threshold, EXISTS, semi-join
```

**Question:** "Which private label products tracked in the Tristate Metro market region generated more than $500 in net sales over the last 14 days of data (18 - 31 Mar 2025)?"

**Reasoning target:** Recognising that a region can only narrow the product list, never the sales. EXISTS expresses that; a join would multiply every sale by the number of weeks the product was tracked.

```sql
SELECT prod.sku_id,
       prod.product_name,
       ROUND(SUM(sales.net_sales_amt), 2) AS net_sales_last_14_days
FROM fact_pos_retail_sales sales
JOIN dim_product prod ON prod.product_key = sales.product_key
WHERE prod.is_private_label = TRUE
  AND sales.sales_date_key BETWEEN 20250318 AND 20250331
  AND EXISTS (
        SELECT 1
        FROM fact_market_share_weekly m
        JOIN dim_geography geo ON geo.market_region_key = m.market_region_key
        WHERE m.product_key   = sales.product_key
          AND geo.region_name = 'Tristate Metro'
      )
GROUP BY prod.sku_id, prod.product_name
HAVING SUM(sales.net_sales_amt) > 500.00
ORDER BY net_sales_last_14_days DESC;
```

**Result:** 13 rows: SKU and net sales.

**Translation note:** Threshold scaled to this dataset. The original joined sales to market share on `product_key` alone, inflating revenue by a factor of several hundred.

---

## Q45 - Margin on discounted versus full-price lines

```meta
chunk_id: eval:q45
type: golden pair
tables: fact_pos_retail_sales, fact_item_cogs, dim_product, dim_date
keywords: promotional margin, discounted, full price, margin mix, beverages, national brand
```

**Question:** "Compare gross margin on discounted lines against full-price lines for national brand Beverages products in fiscal month 12 of FY2025."

**Reasoning target:** Deriving the grouping key from a measure - the CASE has to be applied at line level, before the aggregation that the margin is computed from.

```sql
WITH monthly_cost AS (
    SELECT d.fiscal_year, d.fiscal_month_num, c.product_key, c.store_key,
           AVG(c.net_item_cost) AS unit_cost
    FROM fact_item_cogs c
    JOIN dim_date d ON d.date_key = c.date_key
    WHERE d.fiscal_year = 2025 AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4
),
line AS (
    SELECT CASE WHEN sales.markdown_discount_amt > 0
                THEN 'Discounted line' ELSE 'Full price line' END AS sale_type,
           d.fiscal_year, d.fiscal_month_num, sales.product_key, sales.store_key,
           SUM(sales.net_sales_amt) AS net_sales,
           SUM(sales.quantity_sold) AS units
    FROM fact_pos_retail_sales sales
    JOIN dim_product prod ON prod.product_key = sales.product_key
    JOIN dim_date    d    ON d.date_key       = sales.sales_date_key
    WHERE prod.is_private_label = FALSE
      AND prod.department_name  = 'Beverages'
      AND d.fiscal_year = 2025 AND d.fiscal_month_num = 12
    GROUP BY 1, 2, 3, 4, 5
)
SELECT line.sale_type,
       ROUND(SUM(line.net_sales), 2)                                     AS net_sales,
       ROUND(SUM(line.units * c.unit_cost), 2)                           AS cogs,
       ROUND(100.0 * (SUM(line.net_sales) - SUM(line.units * c.unit_cost))
             / NULLIF(SUM(line.net_sales), 0), 2)                        AS gross_margin_pct
FROM line
JOIN monthly_cost c USING (fiscal_year, fiscal_month_num, product_key, store_key)
GROUP BY line.sale_type
ORDER BY line.sale_type;
```

**Result:** 2 rows: discounted at 11.2% margin, full price at 36.3%.

**Translation note:** 'Beverages' is a department here, not a category. The original grouped by the CASE expression while filtering on a category of the same name, so it would have returned nothing.

---
