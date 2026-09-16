# Data Schema for NL2SQL

This is synthetic example data used for testing and demostration purposes for this project.  It simulates data from a fictional grocery retail chain and covers three main domians:
* Sales and Cost Data
* Competition and Market Share Data
* Promotions and Ads data

With the exception of a handful of shared tables, each domain is stand-alone.


## Shared Tables

### Share Dims

We have the following shared dimension tables:
* item_dim - dimension table for all items carried
* store_dim - dimension table for all stores in the chain
* fiscal_cal_dim - dimension table for the fiscal calendar (FYs start April 1 of the year before a given fiscal year, e.g. FY 2030 starts on April 1, 2029)

### Shared Fact

* item_price_fact - shared item pricing fact table

## Sales and Cost Tables

For sales and cost, we have the following tables:

* sales_fact - fact table containing all sales by the retailer
* item_cogs_fact - fact table containing the Cost of Goods (COGS) for each item
* vendor_allowances_fact - fact table containing the vendor allowances for each item

## Competition and Market Share

For competition and market share, we have the following fact tables:

* competitor_pricing_fact - fact table containing surveyed prices at the retailer's competitors
* market_share_weekly_fact - fact table containing estimated weekly shares of markets for the retailer and its competitors.

As well as the following dim table:

* competitor_dim - dimension table for competitors

## Promotions and Ads

For promotions and ads, we have the following tables:

* promo_performance_fact - fact table for promotion performance
* ad_performance_fact - fact table for ad performance

As well as the following dim tables:

* promotion_dim - dimension table for promotions
* ad_channel_dim - dimension for advertising channels
* ad_placement_dim - dimension table for structural metadata about an advertisement
* promo_cal_dim - dimension table for the promotions calendar

