# SQL Snippets

Pieces of PostgreSQL the agent's queries are built from, each paired with what
it means in the words a question would use: how two tables join, what a phrase
filters to, how a measure is calculated, what a period or a group is called.
The SQL Generator is shown the snippets that match a question, beside the
worked examples in [`translated_questions.md`](translated_questions.md) rather
than instead of them -- a golden pair is a whole question and its whole answer,
and a snippet is one part an answer is made of.

Every snippet here was run against the `nl2sql_retail` database before it was
written down, inside a query in which it plays its part: a join joined, a
filter filtering, a measure measured, a dimension grouped by. The curation
interface does the same before it will save one.

This file is the source of truth. `rag/07_load_snippets.py` loads it into the
snippet store, and deletes from the store any snippet no longer here; the
stack loads it on start whenever the store is behind it.

## How to read a snippet

Each snippet is one `##` section, `S` and a number, with these parts:

- **meta** - its `kind` (join, filter, measure or dimension), the `tables` it
  touches, and `keywords`: the phrasings a question uses for it, which the
  keyword half of retrieval searches.
- **Means** - what it is and which questions it is for, in plain words. This
  is what a question is compared with by meaning.
- **Applies to** - the FROM clause the snippet is written against, with the
  aliases it uses.
- **SQL** - the snippet: a JOIN clause, a WHERE condition, an aggregate
  expression, or an expression to group by.
- **Note** - where it came from, or the mistake it exists to prevent.

A snippet with a literal in it -- a fiscal year, a department -- shows one
value; the generator puts in the one the question asks for.

# Joins

## S01 - Sales on the fiscal calendar

```meta
chunk_id: snippet:s01
kind: join
tables: fact_pos_retail_sales, dim_date
keywords: sales in fiscal year, sales by fiscal month, sales by fiscal quarter, sales by fiscal week, monthly sales, weekly sales, quarterly sales, sales over time, sales trend, sales on holidays, sales on weekends
```

**Means:** Point-of-sale lines placed on the fiscal calendar, for any sales question that filters or groups by fiscal year, quarter, month, week, holiday or weekday.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
JOIN dim_date d ON d.date_key = f.sales_date_key
```

**Note:** The sales fact names its date column sales_date_key; joining on f.date_key fails, and every other fact but market share does use date_key.

## S02 - Sales to the product hierarchy

```meta
chunk_id: snippet:s02
kind: join
tables: fact_pos_retail_sales, dim_product
keywords: sales by product, sales by department, sales by category, sales by sub-category, sales by brand, top products, best-selling products, SKU sales, department sales, category sales, private label sales
```

**Means:** Point-of-sale lines with the product sold, for sales by product, brand, department, category or sub-category, or split by private label.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
JOIN dim_product p ON p.product_key = f.product_key
```

**Note:** One product row per sale line, so no fan-out; the hierarchy is department > category > sub-category on dim_product itself.

## S03 - Sales to stores

```meta
chunk_id: snippet:s03
kind: join
tables: fact_pos_retail_sales, dim_store
keywords: sales by store, store sales, sales by banner, sales by city, sales by state, sales by store format, revenue by store, best store, top stores
```

**Means:** Point-of-sale lines with the store that rang them up, for sales by store, banner, city, state or store format.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
JOIN dim_store s ON s.store_key = f.store_key
```

**Note:** dim_store carries the store's own location; dim_geography is market regions for market share and maps to no store.

## S04 - Market share on the fiscal calendar

```meta
chunk_id: snippet:s04
kind: join
tables: fact_market_share_weekly, dim_date
keywords: market share by period, market share in fiscal year, weekly market share, market share by week, market share over time
```

**Means:** Weekly market-share rows placed on the fiscal calendar, for market share in a fiscal year, quarter, month or week.

**Applies to:**

```sql
fact_market_share_weekly m
```

**SQL:**

```sql
JOIN dim_date d ON d.date_key = m.week_key
```

**Note:** The market-share fact names its date column week_key, the first day of the fiscal week.

## S05 - Ads to their channel

```meta
chunk_id: snippet:s05
kind: join
tables: fact_ad_performance, dim_ad_placement, dim_ad_channel
keywords: ad channel, channel type, advertising channel, print flyer, digital mailer, paid social, in-app push, website banner, advertising by channel
```

**Means:** Daily ad results with the placement and channel they ran in, for spend, impressions or clicks by channel or channel type.

**Applies to:**

```sql
fact_ad_performance a
```

**SQL:**

```sql
JOIN dim_ad_placement pl ON pl.ad_placement_key = a.ad_placement_key
JOIN dim_ad_channel ch ON ch.ad_channel_key = pl.ad_channel_key
```

**Note:** The channel is two hops away; the ad fact has no channel key of its own.

## S06 - Promotion results to their campaign

```meta
chunk_id: snippet:s06
kind: join
tables: fact_promo_performance, dim_promo_calendar
keywords: promo cycle, promotional campaign, campaign, promo season, season type, holiday season, by campaign, promotional calendar
```

**Means:** Daily promotion results with the marketing campaign they ran under, for lift or promoted units by campaign, cycle or season type.

**Applies to:**

```sql
fact_promo_performance pp
```

**SQL:**

```sql
JOIN dim_promo_calendar pc ON pc.promo_calendar_key = pp.promo_calendar_key
```

**Note:** Promotion results join to both calendars; this is the campaign one, and dim_date through pp.date_key is the fiscal one.

## S07 - Our shelf price beside a competitor's

```meta
chunk_id: snippet:s07
kind: join
tables: fact_competitor_pricing, fact_item_prices
keywords: price comparison, price index, cheaper than us, more expensive than us, competitor price, undercut, priced lowest, prices relative to us
```

**Means:** Each observed competitor price beside our own regular price for the same product, store and week, for price indexes and who is cheaper.

**Applies to:**

```sql
fact_competitor_pricing cp
```

**SQL:**

```sql
JOIN fact_item_prices ip
  ON ip.date_key = cp.date_key
 AND ip.product_key = cp.product_key
 AND ip.store_key = cp.store_key
```

**Note:** Both facts are weekly on the same week-start keys, so they join directly; competitor prices cover 4 stores and 70 products only.

## S08 - Vendor allowances to their type

```meta
chunk_id: snippet:s08
kind: join
tables: fact_vendor_allowances, dim_allowance_type
keywords: allowance type, allowances by type, trade funding by type, scan-back, slotting fee, spoilage allowance, volume rebate, display allowance
```

**Means:** Monthly vendor allowances with the kind of allowance each one is, for trade funding by allowance type.

**Applies to:**

```sql
fact_vendor_allowances va
```

**SQL:**

```sql
JOIN dim_allowance_type aty ON aty.allowance_type_key = va.allowance_type_key
```

**Note:** One product, store and month can carry several allowance rows, one per vendor and type, so always aggregate.

# Filters

## S09 - A fiscal year

```meta
chunk_id: snippet:s09
kind: filter
tables: dim_date
keywords: fiscal year, FY, FY2024, FY2025, in 2024, in 2025
```

**Means:** Restricts to one fiscal year, which runs April 1 of the year before through March 31 of the year named; "2024" in a question means fiscal 2024.

**Applies to:**

```sql
dim_date d
```

**SQL:**

```sql
d.fiscal_year = 2025
```

**Note:** Only FY2024 and FY2025 exist. Use calendar_date only when the question says calendar year.

## S10 - The latest fiscal year

```meta
chunk_id: snippet:s10
kind: filter
tables: dim_date
keywords: last year, latest year, most recent year, current year, past year, latest fiscal year
```

**Means:** Restricts to the most recent fiscal year in the data, for questions that say last year, this year or the latest year without naming one.

**Applies to:**

```sql
dim_date d
```

**SQL:**

```sql
d.fiscal_year = (SELECT max(fiscal_year) FROM dim_date)
```

**Note:** Derived from the calendar table rather than written as a number, so it stays right when the data moves on.

## S11 - Holidays

```meta
chunk_id: snippet:s11
kind: filter
tables: dim_date
keywords: holiday, holidays, public holiday, holiday sales, on holidays
```

**Means:** Restricts to the days the calendar marks as holidays.

**Applies to:**

```sql
dim_date d
```

**SQL:**

```sql
d.is_holiday
```

**Note:** 32 of the 731 days are holidays.

## S12 - Weekends

```meta
chunk_id: snippet:s12
kind: filter
tables: dim_date
keywords: weekend, weekends, Saturday and Sunday, weekend sales
```

**Means:** Restricts to Saturdays and Sundays.

**Applies to:**

```sql
dim_date d
```

**SQL:**

```sql
d.day_of_week_name IN ('Saturday', 'Sunday')
```

**Note:** Day names are spelled out in full and capitalised.

## S13 - Store brands

```meta
chunk_id: snippet:s13
kind: filter
tables: dim_product
keywords: private label, store brand, store brands, house brand, own-brand products
```

**Means:** Restricts to the private-label products, which store brand, own brand and own label all mean.

**Applies to:**

```sql
dim_product p
```

**SQL:**

```sql
p.is_private_label
```

**Note:** 41 of the 200 products, under the brands Everyday Basics, Homestead Select, Pantry Essentials and ValueChoice.

## S14 - National brands

```meta
chunk_id: snippet:s14
kind: filter
tables: dim_product
keywords: national brand, national brands, name brand, manufacturer brand
```

**Means:** Restricts to the national-brand products: everything that is not a store brand.

**Applies to:**

```sql
dim_product p
```

**SQL:**

```sql
NOT p.is_private_label
```

**Note:** The complement of the store-brand filter.

## S15 - Marked-down sale lines

```meta
chunk_id: snippet:s15
kind: filter
tables: fact_pos_retail_sales
keywords: marked down, markdown lines, discounted at the register, sold at a discount, register discount
```

**Means:** Restricts to the sale lines that were discounted at the register.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
f.markdown_discount_amt > 0
```

**Note:** About one line in twenty carries a markdown.

## S16 - On promotion that week

```meta
chunk_id: snippet:s16
kind: filter
tables: fact_item_prices
keywords: on promotion, on promo, promo price, promotional price, promoted items, promoted products
```

**Means:** Restricts weekly prices to the product, store and week combinations that had a promotional price.

**Applies to:**

```sql
fact_item_prices ip
```

**SQL:**

```sql
ip.base_promo_price IS NOT NULL
```

**Note:** A NULL promo price means not on promotion that week; it is never a zero price.

## S17 - Front-page ad placements

```meta
chunk_id: snippet:s17
kind: filter
tables: dim_ad_placement
keywords: front page, front-page feature, premium placement, featured ad
```

**Means:** Restricts to the premium front-page ad placements.

**Applies to:**

```sql
dim_ad_placement pl
```

**SQL:**

```sql
pl.is_front_page_feature
```

**Note:** 13 of the 90 placements.

## S18 - Major promotional events

```meta
chunk_id: snippet:s18
kind: filter
tables: dim_promo_calendar
keywords: major event, major promotion, major campaign, big event, tentpole event
```

**Means:** Restricts to the promotional cycles marked as major events.

**Applies to:**

```sql
dim_promo_calendar pc
```

**SQL:**

```sql
pc.is_major_event_cycle
```

**Note:** 24 of the 47 promo cycles.

# Measures

## S19 - Net sales

```meta
chunk_id: snippet:s19
kind: measure
tables: fact_pos_retail_sales
keywords: sales, net sales, revenue, takings, turnover, sales dollars, total sales
```

**Means:** Revenue actually collected, after register discounts: what sales or revenue means when a question does not qualify it.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
SUM(f.net_sales_amt)
```

**Note:** net_sales_amt = gross_sales_amt - markdown_discount_amt on every row.

## S20 - Gross sales

```meta
chunk_id: snippet:s20
kind: measure
tables: fact_pos_retail_sales
keywords: gross sales, sales before discount, sales at full price, gross revenue
```

**Means:** Sales at regular shelf price, before register discounts.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
SUM(f.gross_sales_amt)
```

**Note:** Use only when the question asks for gross; unqualified sales are net.

## S21 - Transactions

```meta
chunk_id: snippet:s21
kind: measure
tables: fact_pos_retail_sales
keywords: transactions, baskets, receipts, shopping trips, number of transactions, transaction count
```

**Means:** The number of shopping baskets, which is what transactions, receipts or trips count.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
COUNT(DISTINCT f.basket_id)
```

**Note:** A basket spans one row per item, so COUNT(*) counts sale lines, not transactions.

## S22 - Average basket value

```meta
chunk_id: snippet:s22
kind: measure
tables: fact_pos_retail_sales
keywords: average basket, average basket value, average transaction value, average spend per trip, basket size, average ticket
```

**Means:** Net sales per shopping basket.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
SUM(f.net_sales_amt) / NULLIF(COUNT(DISTINCT f.basket_id), 0)
```

**Note:** Divides by baskets, not by sale lines.

## S23 - Units sold

```meta
chunk_id: snippet:s23
kind: measure
tables: fact_pos_retail_sales
keywords: units, units sold, quantity sold, items sold, unit sales
```

**Means:** The quantity sold, in units.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
SUM(f.quantity_sold)
```

**Note:** Fractional quantities are normal: produce, meat and deli are sold by weight.

## S24 - Markdown rate

```meta
chunk_id: snippet:s24
kind: measure
tables: fact_pos_retail_sales
keywords: markdown rate, discount rate, markdown percentage, markdown as a share of sales
```

**Means:** Register discounts as a share of gross sales.

**Applies to:**

```sql
fact_pos_retail_sales f
```

**SQL:**

```sql
SUM(f.markdown_discount_amt) / NULLIF(SUM(f.gross_sales_amt), 0)
```

**Note:** A fraction between 0 and 1; multiply by 100 for a percentage.

## S25 - Store-brand share of sales

```meta
chunk_id: snippet:s25
kind: measure
tables: fact_pos_retail_sales, dim_product
keywords: private label share, private label penetration, store brand share, share of sales from store brands, private label percentage
```

**Means:** The share of net sales that came from private-label products: private-label penetration.

**Applies to:**

```sql
fact_pos_retail_sales f
JOIN dim_product p ON p.product_key = f.product_key
```

**SQL:**

```sql
SUM(f.net_sales_amt) FILTER (WHERE p.is_private_label) / NULLIF(SUM(f.net_sales_amt), 0)
```

**Note:** Penetration is a share of sales, not a count of private-label items.

## S26 - Click-through rate

```meta
chunk_id: snippet:s26
kind: measure
tables: fact_ad_performance
keywords: click-through rate, CTR, clicks per impression, engagement rate, coupon clip rate
```

**Means:** Clicks or coupon clips per impression.

**Applies to:**

```sql
fact_ad_performance a
```

**SQL:**

```sql
SUM(a.clicks_or_coupon_clips_count)::numeric / NULLIF(SUM(a.impressions_count), 0)
```

**Note:** Both counts are integers, so without the cast to numeric the division truncates to zero.

## S27 - Our share of the market

```meta
chunk_id: snippet:s27
kind: measure
tables: fact_market_share_weekly, dim_competitor
keywords: market share, share of market, share of the total market, grocer share, total market
```

**Means:** Our sales as a share of total market sales.

**Applies to:**

```sql
fact_market_share_weekly m
```

**SQL:**

```sql
SUM(m.grocer_sales_amount) FILTER (WHERE m.competitor_key = (SELECT min(competitor_key) FROM dim_competitor))
  / NULLIF(SUM(m.total_market_sales_amount) FILTER (WHERE m.competitor_key = (SELECT min(competitor_key) FROM dim_competitor)), 0)
```

**Note:** Every market cell repeats our sales and the market total on five rows, one per competitor; keeping one competitor's rows counts each cell once.

## S28 - Promotional lift

```meta
chunk_id: snippet:s28
kind: measure
tables: fact_promo_performance
keywords: lift, promo lift, incremental units, incremental lift, incremental volume, promotional uplift
```

**Means:** Units sold above the non-promotional baseline because of a promotion.

**Applies to:**

```sql
fact_promo_performance pp
```

**SQL:**

```sql
SUM(pp.promo_quantity_lift)
```

**Note:** Lift is positive on every row in this data, so a promotion that lost volume cannot be found from it.

# Dimensions

## S29 - Fiscal quarter, labelled

```meta
chunk_id: snippet:s29
kind: dimension
tables: dim_date
keywords: by quarter, quarterly, fiscal quarter, quarter by quarter, each fiscal quarter
```

**Means:** A fiscal-quarter label such as FY2025 Q1, to group or show results by quarter across years.

**Applies to:**

```sql
dim_date d
```

**SQL:**

```sql
'FY' || d.fiscal_year || ' Q' || d.fiscal_quarter
```

**Note:** Fiscal Q1 is April to June. Group by the label, or by fiscal_year and fiscal_quarter to sort in order.

## S30 - Fiscal month, labelled

```meta
chunk_id: snippet:s30
kind: dimension
tables: dim_date
keywords: by month, monthly, fiscal month, month by month, each fiscal month
```

**Means:** A fiscal-month label such as FY2025 M01, to group or show results by month across years in an order that sorts.

**Applies to:**

```sql
dim_date d
```

**SQL:**

```sql
'FY' || d.fiscal_year || ' M' || lpad(d.fiscal_month_num::text, 2, '0')
```

**Note:** Fiscal month 1 is April, so fiscal month 8 is not August.

## S31 - Weekday or weekend

```meta
chunk_id: snippet:s31
kind: dimension
tables: dim_date
keywords: weekday versus weekend, weekend vs weekday, weekdays and weekends, weekend compared to weekday
```

**Means:** Splits days into weekday and weekend, to compare the two.

**Applies to:**

```sql
dim_date d
```

**SQL:**

```sql
CASE WHEN d.day_of_week_name IN ('Saturday', 'Sunday') THEN 'Weekend' ELSE 'Weekday' END
```

**Note:** The same days as the weekends filter.

## S32 - Store brand or national brand

```meta
chunk_id: snippet:s32
kind: dimension
tables: dim_product
keywords: private label versus national brand, store brand vs national brand, brand type, private label compared to national
```

**Means:** Splits products into private label and national brand, to compare the two.

**Applies to:**

```sql
dim_product p
```

**SQL:**

```sql
CASE WHEN p.is_private_label THEN 'Private label' ELSE 'National brand' END
```

**Note:** The same split as the store-brand and national-brand filters.
