# Business Index

Business terminology, code/decode reference, join rules and query recipes for
the `nl2sql_retail` database. This index answers "what does the business mean
by this, and how do I express it in SQL", where the DDL index answers "what
columns exist".

**Chunking:** split on `## ` headings. Every chunk is self-contained and names
its tables and columns explicitly, so it stands alone when retrieved without
its neighbours. The `meta` block is machine-readable metadata for the vector
store.

All figures verified against the live database. The data is synthetic; every
brand, banner, competitor, vendor and region is fictional.

---

## Business overview: what this company is

```meta
chunk_id: biz:overview
type: context
keywords: company, business, grocery, retail, overview, what data is available
```

A fictional grocery retail chain of **10 stores** across **4 banners**,
selling **200 products**, tracked over **two fiscal years** (FY2024 and
FY2025, covering 2023-04-01 through 2025-03-31).

The data answers questions in four areas:

- **Sales and cost** — what sold, for how much, at what cost, and what trade
  funding suppliers contributed. Tables: `fact_pos_retail_sales`,
  `fact_item_cogs`, `fact_vendor_allowances`.
- **Pricing** — the shelf price of every stocked item at every store each
  week. Table: `fact_item_prices`.
- **Competition and market share** — rival prices and syndicated regional
  share. Tables: `fact_competitor_pricing`, `fact_market_share_weekly`.
- **Promotions and advertising** — campaign lift and ad performance. Tables:
  `fact_promo_performance`, `fact_ad_performance`.

The four banners are Thrift & Table (value/discount), Corner Fresh Grocers
(neighborhood market), Metro Express Foods (urban small format) and Heritage
Provisions Co-op (traditional supermarket).

---

## Fiscal calendar: "2024" does not mean calendar 2024

```meta
chunk_id: biz:fiscal-calendar
type: business rule
tables: dim_date
keywords: fiscal year, calendar, year, FY, week, month, quarter, 4-4-5, time period, last year
```

Fiscal year `Y` runs **April 1 of year `Y-1` through March 31 of year `Y`**.

- **FY2024** = 2023-04-01 .. 2024-03-31
- **FY2025** = 2024-04-01 .. 2025-03-31

These are the only two fiscal years in the data. A question that says "in
2024" almost always means `dim_date.fiscal_year = 2024`, which is mostly
calendar 2023. If a user genuinely means the calendar year, filter on
`calendar_date` instead.

Each fiscal year has 52 weeks, 12 months and 4 quarters in a **4-4-5** layout:
fiscal months 3, 6, 9 and 12 contain 5 weeks; the rest contain 4. Fiscal month
1 is April. Fiscal month 12 absorbs the remainder and can run 37 days.

`nrf_454_week_num` holds the same value as `fiscal_week_num` in every row and
carries no extra information — always use `fiscal_week_num`.

The weekday a fiscal week starts on **changes between years** (FY2024 weeks
start Saturday, FY2025 weeks start Monday) because the fiscal year start date
moves. Never hardcode a week-start weekday; derive it from `dim_date`.

```sql
-- Filter to a fiscal year
JOIN dim_date d ON d.date_key = f.date_key
WHERE d.fiscal_year = 2024
```

---

## Grain: which tables are daily, weekly and monthly

```meta
chunk_id: biz:grain
type: business rule
tables: fact_pos_retail_sales, fact_item_prices, fact_item_cogs, fact_vendor_allowances, fact_competitor_pricing, fact_market_share_weekly
keywords: grain, granularity, daily, weekly, monthly, join, date mismatch, no rows
```

Four tables have a **daily-looking primary key but are not populated daily**.
This is the single most common cause of a query that returns zero rows.

| Table | Actual grain | Date values | Stamped with |
|---|---|---|---|
| `fact_pos_retail_sales` | daily | 731 | the sale date |
| `fact_promo_performance` | daily | 397 | the event date |
| `fact_ad_performance` | daily | 505 | the event date |
| `fact_item_prices` | **weekly** | 104 | first day of the fiscal week |
| `fact_competitor_pricing` | **weekly** | 104 | first day of the fiscal week |
| `fact_market_share_weekly` | **weekly** | 104 | first day of the fiscal week |
| `fact_item_cogs` | **monthly** | 24 | first day of the fiscal month |
| `fact_vendor_allowances` | **monthly** | 24 | first day of the fiscal month |

Joining daily sales to weekly prices or monthly costs directly on the date key
matches almost nothing. Join through `dim_date` on fiscal week or fiscal month
instead — see the recipes for margin and for pricing a sale.

---

## Date column names differ between facts

```meta
chunk_id: biz:date-columns
type: business rule
tables: fact_pos_retail_sales, fact_market_share_weekly, dim_date
keywords: date_key, sales_date_key, week_key, join, column name, date column
```

All facts reference `dim_date.date_key`, but the local column name is not
always `date_key`:

- `fact_pos_retail_sales` uses **`sales_date_key`**
- `fact_market_share_weekly` uses **`week_key`**
- every other fact uses `date_key`

```sql
JOIN dim_date d ON d.date_key = s.sales_date_key   -- POS sales
JOIN dim_date d ON d.date_key = m.week_key         -- market share
JOIN dim_date d ON d.date_key = f.date_key         -- everything else
```

---

## Market share fan-out: the five-row trap

```meta
chunk_id: biz:market-share-fanout
type: business rule
tables: fact_market_share_weekly
keywords: market share, fan-out, double counting, duplicate, total market, share of market, overstated
```

`fact_market_share_weekly` has **exactly five rows per (week_key, product_key,
market_region_key) cell**, one per competitor. `grocer_sales_amount` and
`total_market_sales_amount` **repeat identically on all five rows**; only
`competitor_sales_amount` varies.

Summing `grocer_sales_amount` or `total_market_sales_amount` without
de-duplicating **inflates that total by exactly 5x** (verified: 32,052,502,955
naive vs 6,410,500,591 de-duplicated). `competitor_sales_amount` varies per row
and is safe to sum.

Note the subtlety: a **ratio** of the two repeated columns still comes out
right, because numerator and denominator inflate equally — naive and
de-duplicated share both give 21.48%. It is **absolute figures** — market size,
our sales dollars, "how big is this category" — that come out 5x too large.
De-duplicate anyway, so the intermediate totals are also correct.

The relationship that actually holds:

```
total_market_sales_amount = grocer_sales_amount + SUM(competitor_sales_amount over all 5 competitors)
```

Correct share calculation — de-duplicate to one row per cell first:

```sql
SELECT SUM(cell.grocer_sales) / NULLIF(SUM(cell.total_market), 0) AS grocer_share
FROM (
    SELECT week_key, product_key, market_region_key,
           MAX(grocer_sales_amount)       AS grocer_sales,
           MAX(total_market_sales_amount) AS total_market
    FROM fact_market_share_weekly
    GROUP BY week_key, product_key, market_region_key
) cell;
```

Filtering to a single `competitor_key` is an equally valid way to de-duplicate
when you only need our own numbers. Our share of the total market averages
**21.5%**.

---

## Sales metrics: gross, markdown and net

```meta
chunk_id: biz:sales-metrics
type: glossary
tables: fact_pos_retail_sales
keywords: sales, revenue, net sales, gross sales, markdown, discount, units, basket, transaction, average basket
```

From `fact_pos_retail_sales`:

- **Gross sales** = `SUM(gross_sales_amt)` — quantity × regular shelf price,
  before any discount.
- **Markdown** = `SUM(markdown_discount_amt)` — register-level discounts.
  Non-zero on only 4.6% of lines.
- **Net sales** = `SUM(net_sales_amt)` — actual revenue collected. **When a
  user says "sales" or "revenue" without qualification, use net sales.**
- **Units sold** = `SUM(quantity_sold)`. Fractional values are normal: 291,531
  lines are weight-sold produce, meat and deli items.
- **Transactions / baskets** = `COUNT(DISTINCT basket_id)`. A basket spans
  multiple rows, one per item, so never use `COUNT(*)` for transaction counts.
- **Average basket value** = `SUM(net_sales_amt) / COUNT(DISTINCT basket_id)`.
- **Units per basket** = `SUM(quantity_sold) / COUNT(DISTINCT basket_id)`.

Verified invariant: `net_sales_amt = gross_sales_amt - markdown_discount_amt`
on every row, so markdown rate is
`SUM(markdown_discount_amt) / NULLIF(SUM(gross_sales_amt), 0)`.

---

## Cost, margin and trade funding

```meta
chunk_id: biz:cost-margin
type: glossary
tables: fact_item_cogs, fact_vendor_allowances, dim_allowance_type
keywords: COGS, cost, margin, gross margin, profit, freight, allowance, trade funding, rebate, vendor credit
```

- **Base cost** (`fact_item_cogs.base_cost`) — supplier invoice cost per unit.
- **Freight cost** (`freight_cost`) — allocated logistics cost per unit.
- **Net item cost** (`net_item_cost`) — base + freight, the landed cost to use
  for margin. Components are rounded independently, so the sum can differ from
  the stored column by up to 0.0001; trust `net_item_cost`.
- **Gross margin** = net sales − COGS. Costs are **monthly** and sales are
  **daily**, so the two must be reconciled on fiscal month, not date key.
- **Trade funding / allowances** (`fact_vendor_allowances.total_allowance_amt`)
  — money suppliers credit back. Also monthly. A single product/store/month can
  carry several rows, one per vendor and allowance type, so always aggregate.
- **Allowance rate** (`allowance_rate_per_unit`) — the contractual per-unit
  funding rate, 0.0079–1.8267.

Vendor allowances are the **only** link between products and vendors;
`dim_product` has no vendor key.

---

## Pricing terms

```meta
chunk_id: biz:pricing
type: glossary
tables: fact_item_prices, fact_competitor_pricing
keywords: price, shelf price, regular price, promo price, on promotion, discount, price index, assortment
```

From `fact_item_prices` (weekly, first day of the fiscal week):

- **Regular retail price** (`regular_retail_price`) — the standard shelf
  price, 0.67–26.77. Never NULL.
- **Base promo price** (`base_promo_price`) — the promotional price.
  **NULL on 93.3% of rows and NULL means "not on promotion that week"** — it
  is never a zero price. An item is on promotion when
  `base_promo_price IS NOT NULL`, and it is always strictly below the regular
  price.
- **Discount depth** = `(regular_retail_price - base_promo_price) /
  regular_retail_price`, computed only where the promo price is present.

**Assortment:** rows exist for 1,725 of the 2,000 possible product-store
pairs. Stores carry 75–100% of the catalog, and a missing row means the store
does not stock that item — not that the price is unknown.

---

## Competitive pricing and market positioning

```meta
chunk_id: biz:competitive
type: glossary
tables: fact_competitor_pricing, dim_competitor, fact_item_prices
keywords: competitor, rival, price index, cheaper, more expensive, competitive, positioning, undercut
```

`fact_competitor_pricing` holds rival shelf prices observed by the
competitive-intelligence team, weekly.

- **`store_key` is OUR store**, the anchor defining the competitive radius —
  not a competitor's store. No competitor store rows exist anywhere.
- **Coverage is partial**: only the 4-store audit panel (STR003, STR005,
  STR009, STR010) and only 70 of 200 products. Never present competitive
  figures as chain-wide.
- **`comp_promo_price`** is populated on 24.6% of rows; NULL means no observed
  promotion.
- **Price index** = `comp_regular_price / regular_retail_price`. Below 1 means
  the competitor is cheaper than us.

Observed index by `dim_competitor.market_positioning`:

| Positioning | Competitors | Typical price vs ours |
|---|---|---|
| Club/Warehouse | Bulk Barn Wholesale Club | ~84% (cheapest) |
| Value/Discount | ValuMax Foods | ~91% |
| Premium/Specialty | Foothill Fresh, Heritage Fine Foods | ~115% |
| Convenience/Small Format | QuickStop Grocery | ~116% (dearest) |

---

## Promotions: mechanics, cycles and lift

```meta
chunk_id: biz:promotions
type: glossary
tables: fact_promo_performance, dim_promotion, dim_promo_calendar
keywords: promotion, promo, lift, incremental, BOGO, multi-buy, campaign, season, mechanic
```

- **Promo quantity sold** (`promo_quantity_sold`) — units moved under a
  promotion, 3.256–97.203.
- **Promo lift** (`promo_quantity_lift`) — incremental units above the
  non-promotional baseline, 0.495–43.514. **Always positive in this dataset**;
  there are no negative-lift rows, so "promotions that lost money" has no
  answer here.
- **Mechanic type** (`dim_promotion.mechanic_type`) — how the discount works.
- **Promo cycle** (`dim_promo_calendar`) — the marketing campaign window,
  8–21 days, deliberately not aligned to fiscal weeks.
- **Season type** (`promo_season_type`) — the thematic grouping used for
  year-over-year comparison.

`fact_promo_performance` is the only fact joined to **both** calendars, so
lift can be reported by fiscal period (`date_key` → `dim_date`) or by campaign
(`promo_calendar_key` → `dim_promo_calendar`). Coverage: all 40 promotions
appear, but only 26 of the 47 promo cycles do.

---

## Advertising metrics

```meta
chunk_id: biz:advertising
type: glossary
tables: fact_ad_performance, dim_ad_placement, dim_ad_channel
keywords: ad, advertising, spend, impressions, clicks, coupon clips, CTR, channel, placement, creative
```

- **Ad spend** (`ad_spend_amount`) — daily localized spend for one placement.
- **Impressions** (`impressions_count`) — views, or print distribution volume.
- **Clicks or coupon clips** (`clicks_or_coupon_clips_count`) — link clicks on
  digital channels, coupon clips on print and loyalty channels.
- **CTR** = `SUM(clicks_or_coupon_clips_count) / NULLIF(SUM(impressions_count), 0)`.
- **Cost per impression** = `SUM(ad_spend_amount) / NULLIF(SUM(impressions_count), 0)`.

Channel attributes are two hops away: `fact_ad_performance` →
`dim_ad_placement` → `dim_ad_channel`. There is no channel key on the fact.

`dim_ad_placement.is_front_page_feature` (13 of 90 placements) marks premium
positions; `creative_version_code` identifies A/B test variants.

---

## Code and decode values

```meta
chunk_id: biz:code-decode
type: reference
tables: dim_allowance_type, dim_promotion, dim_competitor, dim_ad_channel, dim_store, dim_product
keywords: code, decode, lookup, enum, valid values, categories, allowed values, what values
```

Complete value lists. Match user wording to these exactly rather than
inventing filters.

**Allowance types** (`dim_allowance_type`, join to decode
`fact_vendor_allowances.allowance_type_key`):
`SCAN_BACK` = Scan-Back Allowance; `SLOTTING` = Slotting Fee;
`SPOILAGE` = Spoilage Allowance; `VOLUME_REBATE` = Volume Rebate;
`ADVERTISING` = Cooperative Advertising Allowance;
`NEW_ITEM` = New Item Introduction Allowance; `DISPLAY` = Display Allowance.

**Promotion mechanics** (`dim_promotion.mechanic_type`): Multi-Buy, BOGO,
Loyalty Price Drop, Mix-and-Match, Percent Off, Dollar Off.

**Market positioning** (`dim_competitor.market_positioning`):
Premium/Specialty, Value/Discount, Club/Warehouse, Convenience/Small Format.

**Ad channel types** (`dim_ad_channel.channel_type`): Print Flyer, Digital
Mailer, Paid Social, In-App Push, Website Banner.

**Store layout types** (`dim_store.layout_type_desc`): Value/Discount,
Neighborhood Market, Urban Small Format, Traditional Supermarket.

**Store banners** (`dim_store.banner_name`): Thrift & Table, Corner Fresh
Grocers, Metro Express Foods, Heritage Provisions Co-op.

**Payment terms** (`dim_vendor.payment_terms_desc`): Net 30, Net 45, Net 60,
2/10 Net 30, 1/15 Net 45.

**Promo season types** (`dim_promo_calendar.promo_season_type`): Summer
Grilling, Back to School, Memorial Day Kickoff, Easter & Spring Refresh,
Holiday Season, New Year New You, Spring Cleaning, Thanksgiving Feast, Big
Game & Valentine's, Fall Harvest & Halloween, Labor Day Savings.

**Departments** (`dim_product.department_name`, 17): Dairy & Eggs, Produce,
Pantry & Canned Goods, Meat & Seafood, Bakery, Beverages, Deli, Frozen Foods,
Condiments & Sauces, Breakfast & Cereal, Snacks & Candy, Household & Cleaning,
Baby & Child, Baking Supplies, Pet Care, Health & Beauty, Floral & Garden.

---

## Private label vs national brands

```meta
chunk_id: biz:private-label
type: glossary
tables: dim_product
keywords: private label, store brand, own brand, national brand, penetration, brand
```

`dim_product.is_private_label` is true for **41 of 200 products**. The four
private-label brands are **Everyday Basics, Homestead Select, Pantry
Essentials, ValueChoice**; the other 20 brands are national brands.

"Store brand", "own brand" and "private label" all mean
`is_private_label = TRUE`.

**Private label penetration** is a share of sales, not a count of items:

```sql
SELECT SUM(s.net_sales_amt) FILTER (WHERE p.is_private_label)
       / NULLIF(SUM(s.net_sales_amt), 0) AS private_label_share
FROM fact_pos_retail_sales s
JOIN dim_product p ON p.product_key = s.product_key;
```

---

## Product hierarchy and store geography

```meta
chunk_id: biz:hierarchies
type: reference
tables: dim_product, dim_store, dim_geography
keywords: hierarchy, department, category, sub-category, region, geography, rollup, drill down
```

**Product hierarchy** is strictly three levels:
`department_name > category_name > sub_category_name`
(17 → 33 → 48 distinct values), e.g. `Produce > Fresh Fruit > Apples`. Roll up
by selecting the level the question asks for; there is no separate hierarchy
table.

**Store geography** is descriptive only: `city`, `state_code`, `postal_code`
on `dim_store`. States present: KY, OR, CA, WI, CT, AL, NC, TX.

**`dim_geography` is NOT store geography.** Its 8 market regions exist solely
for `fact_market_share_weekly`, and **no column anywhere maps a store to a
market region**. Questions like "market share for our Texas stores" cannot be
answered — regional share cannot be tied back to individual stores.

---

## Recipe: pricing a sale / joining sales to prices

```meta
chunk_id: biz:recipe-price-join
type: query recipe
tables: fact_pos_retail_sales, fact_item_prices, dim_date
keywords: join sales to price, unit price, price at time of sale, weekly price, recipe
```

Prices are weekly, stamped on the **first day of the fiscal week**. To attach
the price in effect for a given sale, resolve the sale's fiscal week to its
first day and join on that:

```sql
WITH week_anchor AS (
    SELECT fiscal_year, fiscal_week_num, MIN(date_key) AS week_start_key
    FROM dim_date
    GROUP BY fiscal_year, fiscal_week_num
)
SELECT s.*, p.regular_retail_price, p.base_promo_price
FROM fact_pos_retail_sales s
JOIN dim_date d       ON d.date_key = s.sales_date_key
JOIN week_anchor w    ON w.fiscal_year = d.fiscal_year
                     AND w.fiscal_week_num = d.fiscal_week_num
JOIN fact_item_prices p ON p.date_key    = w.week_start_key
                       AND p.product_key = s.product_key
                       AND p.store_key   = s.store_key;
```

A simpler alternative when you only need the realised unit price:
`gross_sales_amt / quantity_sold` reproduces that week's regular price on
full-price lines.

---

## Recipe: gross margin across mixed grains

```meta
chunk_id: biz:recipe-margin
type: query recipe
tables: fact_pos_retail_sales, fact_item_cogs, dim_date
keywords: margin, gross margin, profit, COGS, cost, profitability, recipe, monthly cost
```

Sales are daily, costs are monthly. Aggregate both to fiscal month before
combining — never join them on the date key.

```sql
WITH sales AS (
    SELECT d.fiscal_year, d.fiscal_month_num, s.product_key, s.store_key,
           SUM(s.net_sales_amt) AS net_sales,
           SUM(s.quantity_sold) AS units
    FROM fact_pos_retail_sales s
    JOIN dim_date d ON d.date_key = s.sales_date_key
    GROUP BY 1, 2, 3, 4
),
cost AS (
    SELECT d.fiscal_year, d.fiscal_month_num, c.product_key, c.store_key,
           AVG(c.net_item_cost) AS unit_cost
    FROM fact_item_cogs c
    JOIN dim_date d ON d.date_key = c.date_key
    GROUP BY 1, 2, 3, 4
)
SELECT SUM(s.net_sales) AS net_sales,
       SUM(s.units * c.unit_cost) AS cogs,
       SUM(s.net_sales) - SUM(s.units * c.unit_cost) AS gross_margin
FROM sales s
JOIN cost c USING (fiscal_year, fiscal_month_num, product_key, store_key);
```

---

## Recipe: promotional lift by campaign or by period

```meta
chunk_id: biz:recipe-lift
type: query recipe
tables: fact_promo_performance, dim_promo_calendar, dim_promotion, dim_date
keywords: lift, promotion performance, campaign, season, dual calendar, recipe
```

The dual-calendar join is the point of this table — pick the calendar the
question implies.

```sql
-- By marketing campaign / season
SELECT pc.promo_season_type,
       SUM(f.promo_quantity_sold) AS units_on_promo,
       SUM(f.promo_quantity_lift) AS incremental_units
FROM fact_promo_performance f
JOIN dim_promo_calendar pc ON pc.promo_calendar_key = f.promo_calendar_key
GROUP BY 1 ORDER BY incremental_units DESC;

-- By fiscal period, with the offer mechanic
SELECT d.fiscal_year, d.fiscal_quarter, pr.mechanic_type,
       SUM(f.promo_quantity_lift) AS incremental_units
FROM fact_promo_performance f
JOIN dim_date d      ON d.date_key      = f.date_key
JOIN dim_promotion pr ON pr.promotion_key = f.promotion_key
GROUP BY 1, 2, 3 ORDER BY 1, 2, incremental_units DESC;
```

---

## Recipe: comparing our price against competitors

```meta
chunk_id: biz:recipe-price-index
type: query recipe
tables: fact_competitor_pricing, fact_item_prices, dim_competitor
keywords: price comparison, price index, cheaper, undercut, competitive, recipe
```

Both tables are weekly and share the same week-anchor date keys, so they join
directly on `date_key`, `product_key` and `store_key`.

```sql
SELECT c.competitor_name, c.market_positioning,
       ROUND(AVG(100.0 * f.comp_regular_price / p.regular_retail_price), 1)
           AS pct_of_our_price,
       COUNT(*) FILTER (WHERE f.comp_regular_price < p.regular_retail_price)
           AS times_cheaper_than_us
FROM fact_competitor_pricing f
JOIN dim_competitor c   ON c.competitor_key = f.competitor_key
JOIN fact_item_prices p ON p.date_key    = f.date_key
                       AND p.product_key = f.product_key
                       AND p.store_key   = f.store_key
GROUP BY 1, 2 ORDER BY pct_of_our_price;
```

Remember the scope: 4 audit stores, 70 products. Qualify results accordingly.

---

## Coverage limits: questions this data cannot answer

```meta
chunk_id: biz:coverage-limits
type: business rule
keywords: cannot answer, missing, coverage, limitation, no data, out of scope
```

State the limit rather than silently returning a partial or empty result:

- **Only FY2024 and FY2025 exist** (2023-04-01 .. 2025-03-31). No prior-year
  comparison beyond these two, and no data after 2025-03-31.
- **Competitive pricing covers 4 of 10 stores and 70 of 200 products.**
- **Market share covers 70 products** and cannot be tied to stores, because
  nothing maps a store to a market region.
- **Costs and allowances are monthly**, so daily margin is not available.
- **No negative promotional lift exists**, so under-performing promotions
  cannot be identified from `promo_quantity_lift`.
- **No vendor is linked to a product** except through recorded allowances, so
  "which vendor supplies X" is only answerable where an allowance row exists.
- **Only 26 of 47 promo cycles** have performance rows.
- **No customer or loyalty dimension exists.** `basket_id` identifies a
  transaction, not a shopper, so repeat-customer and per-customer questions
  cannot be answered.
- **No inventory, shrink, labour or supply-chain tables exist.**
