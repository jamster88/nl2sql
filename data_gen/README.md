# Synthetic Retail Data Generator

A Python generator that produces a synthetic dataset for the nl2sql grocery
retail data model. It builds every table defined in [`ddl.sql`](ddl.sql)
(11 dimensions, 8 facts) with plausible, internally-consistent values, checks
that the result actually satisfies the schema's primary key and foreign key
constraints, and writes the result out as CSV files plus a ready-to-query
SQLite database.

See [`data_schema.md`](data_schema.md) and
[`data_model_detail.md`](data_model_detail.md) for the narrative
description of the data model this generator implements.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r data_gen/requirements.txt

python data_gen/generate_data.py
```

Defaults (2 fiscal years, 10 stores, 200 products) finish in a few seconds
and produce roughly 2M rows across all 19 tables, written to `output/`:
one CSV per table plus `nl2sql_retail.db` (SQLite, foreign keys enforced).

Useful flags:

```bash
python data_gen/generate_data.py --scale 3 --output-dir output_large   # 3x every volume knob
python data_gen/generate_data.py --seed 7 --fiscal-years 3 --stores 25 --products 500
python data_gen/generate_data.py --no-sqlite                            # CSVs only
python data_gen/generate_data.py --help                                 # full flag list
```

## Why it's built this way

Real POS/merchandising fact tables aren't independent random noise -- a
price cut in `fact_item_prices` should show up as a markdown in
`fact_pos_retail_sales`; a promotion's cost basis should mean something
relative to COGS; a competitor's price should track the banner's market
positioning. The generator is sequenced so that later tables stay
consistent with earlier ones instead of being sampled in isolation:

1. **Store assortment** -- which products each store actually carries
   (70-100% of the catalog, chosen per store), so nothing gets sold,
   priced, or costed at a store that doesn't stock it.
2. **Promotion events** -- each `dim_promotion` row is assigned to one
   `dim_promo_calendar` cycle, a subset of products, and a subset of
   stores, then expanded into a daily participation table. This single
   table drives three different facts: it punches the discounted
   `base_promo_price` into `fact_item_prices`, it's the basis for
   `fact_promo_performance`, and it raises the odds that a POS line for
   that product/store/week rings up at the promo price.
3. **Ad flights** -- similarly, each `dim_ad_placement` gets a flight
   window, a handful of featured products, and a store footprint,
   expanded into daily participation rows that become
   `fact_ad_performance`.
4. **Weekly prices** (`fact_item_prices`) -- a per-store/per-product price
   anchor that random-walks week to week, with promo weeks overlaid from
   step 2.
5. **POS sales** (`fact_pos_retail_sales`) -- basket lines are priced
   directly off that week's `fact_item_prices` row, so gross/markdown/net
   are always mutually consistent and match what the price fact says was
   in effect that week.
6. **Cost side** (`fact_item_cogs`, `fact_vendor_allowances`) -- cost is
   derived as a margin off the same price anchor, so cost never exceeds
   price.
7. **Competitive intelligence** (`fact_competitor_pricing`,
   `fact_market_share_weekly`) -- competitor prices are our regular price
   times a multiplier keyed off `market_positioning` (a discounter prices
   below us, a premium banner prices above), and both tables restrict
   themselves to a "tracked" subset of SKUs rather than the whole catalog,
   matching how real competitive-intel and syndicated market-share panels
   are scoped.

Every fact table is generated with vectorized numpy/pandas operations
(grid construction, `cross` merges, slot-based rejection sampling for
distinct basket contents) rather than per-row Python loops, which is what
keeps the default run under 5 seconds despite ~2M output rows.

### Fiscal calendar

Per `data_schema.md`, fiscal year `Y` starts April 1 of year `Y-1` (e.g.
FY2030 begins 2029-04-01). Within a year, weeks are laid out in a
standard NRF-style 4-4-5 pattern per quarter (4 weeks, 4 weeks, 5 weeks =
13 weeks/quarter = 52 weeks/year), with the calendar's holiday flag
computed from actual US retail holidays (New Year's, MLK Day, Presidents
Day, Easter, Memorial Day, Juneteenth, July 4th, Labor Day, Columbus Day,
Halloween, Veterans Day, Thanksgiving + Black Friday, and the Christmas /
New Year's Eve stretch).

The independent `dim_promo_calendar` is generated separately, as
variable-length (10-21 day) marketing cycles themed by month (e.g. "Back
to School", "Summer Grilling", "Holiday Season"), per the dual-calendar
design in `data_model_detail.md`.

### Fictional content only

All names -- brands, banners, competitors, vendors, regions -- are
hand-authored and fictional. Nothing here references a real company,
product, or place; see `datagen/reference_data.py` for the full lookup
tables.

## Layout

```
data_gen/
├── README.md              (this file)
├── ddl.sql                 Authoritative Postgres schema this generator implements
├── data_schema.md          Domain/table grouping overview
├── data_model_detail.md    Narrative model description (dual-calendar design, etc.)
├── old_ddl.sql             Superseded prior schema draft, kept for reference
├── requirements.txt
├── generate_data.py       CLI entry point
└── datagen/
    ├── config.py           Scale/volume knobs (Config dataclass)
    ├── reference_data.py   Fictional names, product hierarchy, price ranges
    ├── calendar_gen.py     dim_date (fiscal calendar) + dim_promo_calendar
    ├── dimensions.py       All 11 dim_* generators
    ├── facts.py            All 8 fact_* generators + cross-fact consistency
    ├── schema_columns.py   Authoritative column order/load order from ddl.sql
    ├── sqlite_schema.py    SQLite-flavored CREATE TABLE statements
    ├── writer.py           CSV + SQLite output
    └── validate.py         Post-generation PK/FK integrity checks
```

`generate_data.py` orchestrates these in dependency order (calendars →
dimensions → assortment/promo/ad participation → prices → sales → cost →
competitive → promo/ad performance), runs `validate.validate()` -- which
raises immediately if any table has a duplicate primary key or a foreign
key pointing at a row that doesn't exist -- and only then writes output.

## Scaling

Every cardinality and volume knob lives in `datagen/config.py`
(`Config`). `--scale N` multiplies all of them at once (stores, products,
promotions, baskets/store/day, etc.); individual `--stores`,
`--products`, `--fiscal-years`, and similar flags override specific
knobs. Date range, ratios (assortment fraction, audit-store fraction,
etc.) and per-item ranges are left alone by `--scale` since those aren't
"more data," just different data.

Approximate row counts at default scale (2 fiscal years, 10 stores, 200
products):

| Table | Rows |
|---|---:|
| `fact_pos_retail_sales` | ~1.3M |
| `fact_market_share_weekly` | ~290K |
| `fact_item_prices` | ~180K |
| `fact_promo_performance` | ~73K |
| `fact_competitor_pricing` | ~54K |
| `fact_item_cogs` | ~41K |
| `fact_vendor_allowances` | ~32K |
| `fact_ad_performance` | ~19K |
| `dim_date` | ~731 |
| everything else | tens to low hundreds |
