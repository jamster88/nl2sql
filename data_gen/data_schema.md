# Data Schema for NL2SQL

This is synthetic example data used for testing and demostration purposes for this project.  It simulates data from a fictional grocery retail chain and covers three main domians:
* Sales and Cost Data
* Competition and Market Share Data
* Promotions and Ads data

With the exception of a handful of shared tables, each domain is stand-alone.

All table and column names below match the live database exactly; see
[`ddl.sql`](ddl.sql) for the authoritative definitions.

## Shared Tables

### Shared Dims

We have the following shared dimension tables:
* dim_product - dimension table for all items carried
* dim_store - dimension table for all stores in the chain
* dim_date - dimension table for the fiscal calendar (FYs start April 1 of the year before a given fiscal year, e.g. FY 2030 starts on April 1, 2029)
* dim_vendor - dimension table for all vendors used by the chain
* dim_allowance_type - dimension table for the vendor allowance/trade-funding types
* dim_geography - dimension table for the syndicated market regions

### Shared Fact

* fact_item_prices - shared item pricing fact table, one row per item/store/fiscal week

## Sales and Cost Tables

For sales and cost, we have the following tables:

* fact_pos_retail_sales - fact table containing all sales by the retailer
* fact_item_cogs - fact table containing the Cost of Goods (COGS) for each item
* fact_vendor_allowances - fact table containing the vendor allowances for each item

## Competition and Market Share

For competition and market share, we have the following fact tables:

* fact_competitor_pricing - fact table containing surveyed prices at the retailer's competitors
* fact_market_share_weekly - fact table containing estimated weekly shares of markets for the retailer and its competitors.

As well as the following dim table:

* dim_competitor - dimension table for competitors

## Promotions and Ads

For promotions and ads, we have the following tables:

* fact_promo_performance - fact table for promotion performance
* fact_ad_performance - fact table for ad performance

As well as the following dim tables:

* dim_promotion - dimension table for promotions
* dim_ad_channel - dimension for advertising channels
* dim_ad_placement - dimension table for structural metadata about an advertisement
* dim_promo_calendar - dimension table for the promotions calendar
