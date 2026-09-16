# Data Dictionary & Business Context

Authoritative reference for the `nl2sql_retail` database. Every figure in this
document was verified by querying the running instance
(`mcfaddja/nl2sql-retail-postgres:v1`) rather than read from a specification.

The data is entirely synthetic. All brands, banners, stores, competitors,
vendors and regions are fictional and do not refer to any real company.

- **Schema:** `public`, 19 tables (11 dimensions, 8 facts)
- **Date coverage:** 2023-04-01 through 2025-03-31 (731 days)
- **Fiscal years present:** FY2024 and FY2025
- **Largest table:** `fact_pos_retail_sales`, 1,291,781 rows

---

## 1. Architecture

A star schema over three business domains that share a conformed dimension
layer and a shared pricing bridge.

| Domain | Fact tables |
|---|---|
| Sales & Cost | `fact_pos_retail_sales`, `fact_item_cogs`, `fact_vendor_allowances` |
| Shared pricing bridge | `fact_item_prices` |
| Competition & Market Share | `fact_competitor_pricing`, `fact_market_share_weekly` |
| Promotions & Ads | `fact_promo_performance`, `fact_ad_performance` |

Conformed dimensions shared across domains: `dim_date`, `dim_product`,
`dim_store`. Domain-specific dimensions: `dim_promo_calendar`,
`dim_competitor`, `dim_promotion`, `dim_ad_channel`, `dim_ad_placement`,
`dim_vendor`, `dim_allowance_type`, `dim_geography`.

### The dual calendar

Two independent time structures exist on purpose:

- **`dim_date`** is the corporate fiscal calendar used for financial reporting.
- **`dim_promo_calendar`** is the marketing calendar. Its cycles float freely
  and do not align to fiscal week or month boundaries.

`fact_promo_performance` carries a foreign key to *both*, which is what allows
promotional lift to be sliced either by accounting period or by campaign cycle.

### Grain summary

Grain is the single most important thing to get right when querying this
database, because four tables have a daily-looking primary key but are not
populated daily.

| Table | Declared PK grain | Rows actually written at |
|---|---|---|
| `fact_pos_retail_sales` | date + product + store + basket | **daily** (731 dates) |
| `fact_item_prices` | date + product + store | **weekly** (104 dates) |
| `fact_item_cogs` | date + product + store | **monthly** (24 dates) |
| `fact_vendor_allowances` | date + product + store + vendor + allowance type | **monthly** (24 dates) |
| `fact_competitor_pricing` | date + product + competitor + store | **weekly** (104 dates) |
| `fact_market_share_weekly` | week + product + region + competitor | **weekly** (104 weeks) |
| `fact_promo_performance` | date + cycle + product + store + promotion | **daily** (397 dates) |
| `fact_ad_performance` | date + placement + product + store | **daily** (505 dates) |

Weekly rows are stamped with the **first day of the fiscal week**. Monthly rows
are stamped with the **first day of the fiscal month**.

---

## 2. Calendar dimensions

### `dim_date` — corporate fiscal calendar (731 rows)

Fiscal year `Y` runs from April 1 of year `Y-1` through March 31 of year `Y`.
So **FY2024 = 2023-04-01 .. 2024-03-31** and **FY2025 = 2024-04-01 ..
2025-03-31**. A question about "2024" is ambiguous and usually means FY2024.

Each fiscal year has 52 weeks, 12 months and 4 quarters, laid out in a
**4-4-5** pattern: fiscal months 1, 2, 4, 5, 7, 8, 10 and 11 span 4 weeks;
months 3, 6, 9 and 12 span 5 weeks. Month 12 absorbs the remainder (37 days in
FY2024).

| Column | Type | Notes |
|---|---|---|
| `date_key` | INT PK | `YYYYMMDD` as an integer, e.g. `20230401`. Range 20230401–20250331. |
| `calendar_date` | DATE, unique | The actual date. |
| `day_of_week_name` | VARCHAR(9) | Full English day name, e.g. `Saturday`. |
| `fiscal_week_num` | INT | 1–52 within the fiscal year. |
| `fiscal_month_num` | INT | 1–12; month 1 is April. |
| `fiscal_quarter` | INT | 1–4. |
| `fiscal_year` | INT | 2024 or 2025. |
| `nrf_454_week_num` | INT | **Carries the same value as `fiscal_week_num` in all 731 rows.** No independent NRF numbering exists; prefer `fiscal_week_num`. |
| `is_holiday` | BOOLEAN | True on 32 days across 24 distinct calendar dates (US retail holidays including Easter, Black Friday and the Christmas / New Year stretch). |

The day a fiscal week starts shifts between years because the fiscal year
start date moves: FY2024 weeks begin on **Saturday**, FY2025 weeks begin on
**Monday**. Never assume a fixed week-start weekday.

### `dim_promo_calendar` — marketing calendar (47 rows)

| Column | Type | Notes |
|---|---|---|
| `promo_calendar_key` | SERIAL PK | |
| `promo_cycle_id` | VARCHAR(50), unique | e.g. `PROMO_2024_WK01_001`. |
| `promo_cycle_name` | VARCHAR(150) | e.g. `Easter & Spring Refresh - Phase 1`. |
| `promo_season_type` | VARCHAR(50) | 11 values, see below. |
| `cycle_start_date` | DATE | |
| `cycle_end_date` | DATE | Cycles run 8–21 days. |
| `is_major_event_cycle` | BOOLEAN | True for 24 of 47 cycles. |

Season types: Summer Grilling (8 cycles), Back to School (5), Memorial Day
Kickoff (5), Easter & Spring Refresh (4), Holiday Season (4), New Year New You
(4), Spring Cleaning (4), Thanksgiving Feast (4), Big Game & Valentine's (3),
Fall Harvest & Halloween (3), Labor Day Savings (3).

Only **26 of the 47 cycles** appear in `fact_promo_performance`; the rest have
no performance rows.

---

## 3. Conformed dimensions

### `dim_product` (200 rows)

17 departments, 33 categories, 48 sub-categories, 24 brands.

| Column | Type | Notes |
|---|---|---|
| `product_key` | SERIAL PK | |
| `sku_id` | VARCHAR(50), unique | Format `SKU1000nn`. |
| `upc_barcode` | VARCHAR(14) | Always 12 digits in practice, zero-padded. |
| `product_name` | VARCHAR(255) | Brand + item, e.g. `Bright Orchard Honey Nut Cereal`. |
| `brand_name` | VARCHAR(100) | 24 fictional brands. |
| `department_name` | VARCHAR(100) | Top level of the hierarchy. |
| `category_name` | VARCHAR(100) | Middle level. |
| `sub_category_name` | VARCHAR(100) | Lowest level. |
| `package_size_desc` | VARCHAR(50) | 15 values; **NULL for 25 products** whose name already carries a size. |
| `is_private_label` | BOOLEAN | True for 41 products. |

Hierarchy is strictly `department_name > category_name > sub_category_name`,
e.g. `Produce > Fresh Fruit > Apples`.

Departments and item counts: Dairy & Eggs (32), Produce (31), Pantry & Canned
Goods (16), Meat & Seafood (15), Bakery (13), Beverages (11), Deli (11),
Frozen Foods (11), Condiments & Sauces (10), Breakfast & Cereal (8), Snacks &
Candy (8), Household & Cleaning (7), Baby & Child (6), Baking Supplies (6),
Pet Care (6), Health & Beauty (5), Floral & Garden (4).

Private-label brands are exactly: **Everyday Basics, Homestead Select, Pantry
Essentials, ValueChoice**. The other 20 brands are national brands.

`package_size_desc` values: `12 oz`, `16 oz`, `24 oz`, `32 oz`, `6 oz`,
`8 oz`, `1 Lb`, `2 Lb`, `Sold by the Lb`, `Single`, `4-Pack`, `6-Pack`,
`8-Pack`, `12-Pack`, `24-Pack`.

**There is no vendor foreign key on `dim_product`.** Product-to-vendor
attribution exists only through `fact_vendor_allowances`.

### `dim_store` (10 rows)

| Column | Type | Notes |
|---|---|---|
| `store_key` | SERIAL PK | |
| `store_id` | VARCHAR(20), unique | `STR001`–`STR010`. |
| `store_name` | VARCHAR(150) | `<banner> - <city>`. |
| `banner_name` | VARCHAR(100) | 4 banners. |
| `street_address`, `city`, `state_code`, `postal_code` | | `state_code` is CHAR(2). |
| `square_footage` | INT | 11,462–45,639. |
| `layout_type_desc` | VARCHAR(50) | 4 values, correlated with banner. |

| Store | Banner | City, State | Sq ft | Layout |
|---|---|---|---|---|
| STR001 | Thrift & Table | Ashland, KY | 36,711 | Value/Discount |
| STR002 | Corner Fresh Grocers | Salem, OR | 29,113 | Neighborhood Market |
| STR003 | Corner Fresh Grocers | Riverside, CA | 27,921 | Neighborhood Market |
| STR004 | Thrift & Table | Madison, WI | 25,317 | Value/Discount |
| STR005 | Corner Fresh Grocers | Madison, WI | 19,423 | Neighborhood Market |
| STR006 | Metro Express Foods | Bristol, CT | 11,462 | Urban Small Format |
| STR007 | Thrift & Table | Auburn, AL | 37,761 | Value/Discount |
| STR008 | Heritage Provisions Co-op | Fairview, NC | 35,380 | Traditional Supermarket |
| STR009 | Metro Express Foods | Georgetown, TX | 12,985 | Urban Small Format |
| STR010 | Heritage Provisions Co-op | Bristol, CT | 45,639 | Traditional Supermarket |

### `dim_vendor` (30 rows)

`vendor_key`, `vendor_id` (unique), `vendor_name`, `payment_terms_desc`.
Payment terms in use: Net 60 (8 vendors), Net 30 (7), 1/15 Net 45 (7),
2/10 Net 30 (5), Net 45 (3).

### `dim_allowance_type` (7 rows)

| Code | Name |
|---|---|
| `SCAN_BACK` | Scan-Back Allowance |
| `SLOTTING` | Slotting Fee |
| `SPOILAGE` | Spoilage Allowance |
| `VOLUME_REBATE` | Volume Rebate |
| `ADVERTISING` | Cooperative Advertising Allowance |
| `NEW_ITEM` | New Item Introduction Allowance |
| `DISPLAY` | Display Allowance |

### `dim_geography` (8 rows)

Syndicated market regions, each with a fictional `DMA-nnn` code:
Tristate Metro (DMA-335), Southeast Coastal (DMA-990), Piedmont (DMA-381),
New England (DMA-740), Gulf Coast (DMA-749), Central Plains (DMA-212),
Pacific Northwest (DMA-144), Mountain West (DMA-182).

Regions are **not** linked to `dim_store`; they exist only for market-share
reporting.

### `dim_competitor` (5 rows)

| ID | Name / Banner | Positioning | Prices vs ours |
|---|---|---|---|
| COMP01 | Foothill Fresh | Premium/Specialty | ~115% |
| COMP02 | QuickStop Grocery | Convenience/Small Format | ~116% |
| COMP03 | Heritage Fine Foods | Premium/Specialty | ~115% |
| COMP04 | Bulk Barn Wholesale Club | Club/Warehouse | ~84% |
| COMP05 | ValuMax Foods | Value/Discount | ~91% |

`competitor_name` and `banner_name` are identical for all five rows.

### `dim_promotion` (40 rows)

`promotion_key`, `promotion_id` (unique), `promotion_name`, `mechanic_type`,
`min_purchase_requirement`.

| Mechanic | Promotions | `min_purchase_requirement` |
|---|---|---|
| Multi-Buy | 12 | 2–5 |
| BOGO | 7 | 2 |
| Loyalty Price Drop | 7 | 1 |
| Mix-and-Match | 7 | 3–5 |
| Percent Off | 5 | 1 |
| Dollar Off | 2 | 1 |

### `dim_ad_channel` (7 rows)

| ID | Channel type | Medium |
|---|---|---|
| CH01 | Print Flyer | Sunday Paper Insert |
| CH02 | Print Flyer | Weekly Circular |
| CH03 | Digital Mailer | Email Newsletter |
| CH04 | Paid Social | Instagram |
| CH05 | Paid Social | Facebook |
| CH06 | In-App Push | Mobile App |
| CH07 | Website Banner | Homepage |

### `dim_ad_placement` (90 rows)

`ad_placement_key`, `ad_id` (unique), `ad_channel_key` (FK to
`dim_ad_channel`), `ad_theme_name` (64 distinct), `creative_version_code`
(9 distinct, for A/B testing), `page_number_slot` (10 distinct),
`is_front_page_feature` (true for 13 of 90).

Slots: Page 1 - Full, Page 1 - Top Left, Page 2 - Top Right, Center Spread,
Back Page, Email Header, Homepage Hero, In-Feed Card 1, Mobile Banner 1,
Search Result Top.

---

## 4. Sales and cost domain

### `fact_pos_retail_sales` (1,291,781 rows)

Register line items. **Note the date column is named `sales_date_key`, not
`date_key`.**

- Grain: one row per date + product + store + basket line.
- 731 dates, 258,308 baskets, 10 stores, 200 products.
- `basket_id` VARCHAR(64), a degenerate dimension, format `BSK0000000001`.
- `quantity_sold` NUMERIC(12,3): 0.400–4.000. 291,531 rows carry a fractional
  quantity, representing weight-sold items such as produce and deli.
- `gross_sales_amt` NUMERIC(12,2): 0.27–107.00.
- `markdown_discount_amt` NUMERIC(12,2): greater than zero on 4.6% of rows.
- `net_sales_amt` NUMERIC(12,2).

**Verified invariant:** `net_sales_amt = gross_sales_amt -
markdown_discount_amt` holds for every row with zero exceptions.
`gross_sales_amt / quantity_sold` reproduces that week's
`regular_retail_price` on full-price lines.

### `fact_item_cogs` (41,400 rows)

- Grain: **monthly**, despite a daily-looking key. 24 `date_key` values, each
  the first day of a fiscal month.
- `base_cost` NUMERIC(12,4): 0.4660–18.0268 — supplier invoice cost per unit.
- `freight_cost` NUMERIC(12,4): 0.0051–0.8681 — allocated logistics cost.
- `net_item_cost` NUMERIC(12,4) = `base_cost + freight_cost`, each rounded
  independently, so the sum can differ from the stored value by up to 0.0001.
  10,460 of 41,400 rows show that one-unit-in-the-last-place difference; it is
  rounding, not a data error.

### `fact_vendor_allowances` (32,350 rows)

Trade funding credited by suppliers.

- Grain: **monthly**, 24 `date_key` values, all 30 vendors represented.
- `allowance_rate_per_unit` NUMERIC(12,4): 0.0079–1.8267.
- `total_allowance_amt` NUMERIC(12,2): 0.76–912.45.
- Requires both `vendor_key` and `allowance_type_key` in the key, so a single
  product/store/month can carry several allowance rows of different types.

---

## 5. Shared pricing bridge

### `fact_item_prices` (179,400 rows)

The price of record for every stocked item at every store.

- Grain: **weekly**. 104 `date_key` values, each the first day of a fiscal
  week. The price holds for the remainder of that week.
- Covers **1,725 of the 2,000 possible product-store pairs** — stores carry
  75–100% of the catalog, so absence of a row means the store does not stock
  the item.
- `regular_retail_price` NUMERIC(12,2): 0.67–26.77.
- `base_promo_price` NUMERIC(12,2): **NULL on 93.3% of rows**, meaning no
  promotional price that week. NULL means "not on promotion", never zero.
  When present it is always strictly below `regular_retail_price`.

---

## 6. Competition and market share domain

### `fact_competitor_pricing` (54,146 rows)

Field-collected rival shelf prices.

- Grain: **weekly**, 104 dates.
- Covers only the **4-store audit panel**: STR003, STR005, STR009, STR010.
- Covers only the **70 tracked products**, not the full 200-item catalog.
- `store_key` is *our* anchor store defining the competitive radius, not a
  competitor's store. `dim_competitor` has no store-level rows at all.
- `comp_regular_price` NUMERIC(12,2) NOT NULL.
- `comp_promo_price` NUMERIC(12,2): populated on 24.6% of rows.

### `fact_market_share_weekly` (291,200 rows)

Syndicated regional share estimates. **Note the date column is named
`week_key`, not `date_key`**, though it still references `dim_date.date_key`.

- Grain: one row per week + product + region + competitor.
- `week_key` is the **first day of the fiscal week** (verified for all 104
  values), not the week-ending date.
- 8 regions × 70 tracked products × 5 competitors × 104 weeks.
- `grocer_sales_amount` — our sales in that region/product/week.
- `competitor_sales_amount` — that one competitor's sales.
- `total_market_sales_amount` — the whole market, all retailers.

**Critical fan-out:** each (week, product, region) cell has exactly five rows,
one per competitor. `grocer_sales_amount` and `total_market_sales_amount`
repeat identically across all five. Summing either column directly
**multiplies that total by five** (32,052,502,955 naive vs 6,410,500,591
de-duplicated). A *ratio* of the two survives, since both inflate equally —
share is 21.48% either way — but any absolute figure is five times too large.
The relationship that actually holds is:

```
total_market_sales_amount = grocer_sales_amount + SUM(competitor_sales_amount over all 5 competitors)
```

---

## 7. Promotions and ads domain

### `fact_promo_performance` (73,057 rows)

- Grain: daily, per cycle + product + store + promotion. 397 dates.
- Joins to **both** calendars: `date_key` → `dim_date` and
  `promo_calendar_key` → `dim_promo_calendar`.
- All 40 promotions appear; only 26 of 47 promo cycles do.
- `promo_quantity_sold` NUMERIC(12,3): 3.256–97.203.
- `promo_quantity_lift` NUMERIC(12,3): 0.495–43.514. **Always positive** in
  this dataset; there are no negative-lift rows.

### `fact_ad_performance` (18,872 rows)

- Grain: daily, per placement + product + store. 505 dates, all 90 placements.
- Metric ranges differ by channel type:

| Channel type | Daily spend | Impressions | Max clicks/clips |
|---|---|---|---|
| Print Flyer | 20.01–150.00 | 5,000–39,980 | 800 |
| Paid Social | 15.00–119.98 | 3,002–24,991 | 900 |
| Website Banner | 10.03–89.98 | 2,014–19,967 | 600 |
| Digital Mailer | 10.04–79.87 | 2,002–14,979 | 1,200 |
| In-App Push | 5.00–40.00 | 1,000–8,000 | 500 |

`clicks_or_coupon_clips_count` means link clicks for digital channels and
coupon clips for print/loyalty channels.

---

## 8. Business metric definitions

| Term | Definition |
|---|---|
| Gross sales | `SUM(gross_sales_amt)` — quantity × regular shelf price, before discounts. |
| Markdown | `SUM(markdown_discount_amt)` — register-level discounts. |
| Net sales | `SUM(net_sales_amt)` — actual revenue. Use this for "sales" unless the question says gross. |
| Units sold | `SUM(quantity_sold)`. Fractional for weight items. |
| Basket count | `COUNT(DISTINCT basket_id)`. |
| Average basket value | `SUM(net_sales_amt) / COUNT(DISTINCT basket_id)`. |
| COGS | `SUM(net_item_cost)` from `fact_item_cogs` (monthly grain). |
| Gross margin | Net sales − COGS. Requires reconciling daily sales to monthly cost. |
| Trade funding | `SUM(total_allowance_amt)` from `fact_vendor_allowances`. |
| Promo lift | `SUM(promo_quantity_lift)` — incremental units attributed to a promotion. |
| Market share | `grocer_sales_amount / total_market_sales_amount`, de-duplicated first (see fan-out). |
| Price index vs competitor | `comp_regular_price / regular_retail_price`. |
| Private label penetration | Share of sales where `dim_product.is_private_label` is true. |
| Ad CTR | `clicks_or_coupon_clips_count / impressions_count`. |

---

## 9. Known traps

1. **Date column names differ.** `sales_date_key` in POS sales, `week_key` in
   market share, `date_key` everywhere else. All reference `dim_date.date_key`.
2. **Fiscal year is not calendar year.** FY2024 starts 2023-04-01.
3. **Mixed grains.** Joining monthly COGS or allowances to daily sales on
   `date_key` matches almost nothing. Join on fiscal month instead.
4. **Weekly prices.** To price a sale, join to the `fact_item_prices` row for
   the first day of that sale's fiscal week.
5. **Market-share fan-out.** Five rows per cell; de-duplicate before summing
   `grocer_sales_amount` or `total_market_sales_amount`. Ratios survive the
   fan-out; absolute totals come out 5x too large.
6. **`nrf_454_week_num` is a copy** of `fiscal_week_num`.
7. **NULL `base_promo_price` means no promotion**, not a zero price.
8. **Partial coverage.** Competitor pricing covers 4 stores and 70 products;
   market share covers 70 products; not every store stocks every product.
9. **No product-vendor link** outside `fact_vendor_allowances`.
10. **Competitor pricing `store_key` is our store**, not theirs.
