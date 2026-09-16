#!/usr/bin/env python3
"""Generate a synthetic dataset for the nl2sql grocery retail data model.

Builds every table defined in ddl.sql (dims + facts) with plausible,
internally-consistent values -- store assortments, weekly price
snapshots, promo/ad flight windows, POS sales, etc. -- validates primary
key and foreign key integrity, then writes the result to CSV files and
(by default) a ready-to-query SQLite database.

Examples (run from the repo root, or drop the data_gen/ prefix if run
from inside data_gen/):
    python data_gen/generate_data.py
    python data_gen/generate_data.py --scale 3 --output-dir output_large
    python data_gen/generate_data.py --seed 7 --fiscal-years 3 --stores 25 --products 500
    python data_gen/generate_data.py --no-sqlite
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from datagen import calendar_gen, dimensions, facts, validate, writer
from datagen.config import Config


def parse_args() -> Config:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    p.add_argument("--scale", type=float, default=1.0, help="multiply every volume/cardinality knob (default: 1.0)")
    p.add_argument("--first-fiscal-year", type=int, default=2024, help="label of the first fiscal year generated")
    p.add_argument("--fiscal-years", type=int, default=2, help="number of fiscal years to generate")
    p.add_argument("--stores", type=int, default=10, help="number of stores")
    p.add_argument("--products", type=int, default=200, help="number of products")
    p.add_argument("--competitors", type=int, default=5, help="number of competitors")
    p.add_argument("--vendors", type=int, default=30, help="number of vendors")
    p.add_argument("--market-regions", type=int, default=8, help="number of market regions")
    p.add_argument("--promotions", type=int, default=40, help="number of promotions")
    p.add_argument("--ad-channels", type=int, default=7, help="number of ad channels")
    p.add_argument("--ad-placements", type=int, default=90, help="number of ad placements")
    p.add_argument("--output-dir", type=str, default="output", help="directory for CSV/SQLite output")
    p.add_argument("--no-sqlite", action="store_true", help="skip building the SQLite database")
    p.add_argument("--sqlite-filename", type=str, default="nl2sql_retail.db")
    p.add_argument("--quiet", action="store_true", help="suppress per-table progress/validation output")
    args = p.parse_args()

    base = Config(
        seed=args.seed,
        first_fiscal_year=args.first_fiscal_year,
        num_fiscal_years=args.fiscal_years,
        n_stores=args.stores,
        n_products=args.products,
        n_competitors=args.competitors,
        n_vendors=args.vendors,
        n_market_regions=args.market_regions,
        n_promotions=args.promotions,
        n_ad_channels=args.ad_channels,
        n_ad_placements=args.ad_placements,
        output_dir=args.output_dir,
        write_sqlite=not args.no_sqlite,
        sqlite_filename=args.sqlite_filename,
    )
    config = base.scaled(args.scale) if args.scale != 1.0 else base
    config._quiet = args.quiet  # type: ignore[attr-defined]
    return config


def log(config: Config, msg: str) -> None:
    if not getattr(config, "_quiet", False):
        print(msg)


def main() -> None:
    config = parse_args()
    t0 = time.time()

    log(config, "Building calendars...")
    dim_date = calendar_gen.build_dim_date(config)
    dim_promo_calendar = calendar_gen.build_dim_promo_calendar(config, dim_date)

    log(config, "Building dimensions...")
    dim_product = dimensions.gen_dim_product(config)
    dim_store = dimensions.gen_dim_store(config)
    dim_competitor = dimensions.gen_dim_competitor(config)
    dim_promotion = dimensions.gen_dim_promotion(config)
    dim_ad_channel = dimensions.gen_dim_ad_channel(config)
    dim_ad_placement = dimensions.gen_dim_ad_placement(config, dim_ad_channel)
    dim_vendor = dimensions.gen_dim_vendor(config)
    dim_product = dimensions.assign_product_vendors(config, dim_product, len(dim_vendor))
    dim_allowance_type = dimensions.gen_dim_allowance_type(config)
    dim_geography = dimensions.gen_dim_geography(config)

    log(config, "Building store assortments and promo/ad participation...")
    store_assortment = facts.build_store_assortment(config, dim_store, dim_product)
    promo_participation = facts.build_promo_participation(
        config, dim_date, dim_promo_calendar, dim_promotion, dim_product, dim_store
    )
    ad_participation = facts.build_ad_participation(config, dim_date, dim_ad_placement, dim_product, dim_store)
    tracked_products = facts.select_tracked_products(config, dim_product)

    log(config, "Generating fact_item_prices...")
    fact_item_prices = facts.gen_fact_item_prices(
        config, dim_date, dim_product, dim_store, store_assortment, promo_participation
    )

    log(config, "Generating fact_pos_retail_sales...")
    fact_pos_retail_sales = facts.gen_fact_pos_retail_sales(
        config, dim_date, dim_product, dim_store, store_assortment, fact_item_prices
    )

    log(config, "Generating fact_item_cogs...")
    fact_item_cogs = facts.gen_fact_item_cogs(config, dim_date, dim_product, store_assortment)

    log(config, "Generating fact_vendor_allowances...")
    fact_vendor_allowances = facts.gen_fact_vendor_allowances(
        config, dim_date, dim_product, dim_store, dim_allowance_type
    )

    log(config, "Generating fact_competitor_pricing...")
    fact_competitor_pricing = facts.gen_fact_competitor_pricing(
        config, dim_date, dim_product, dim_store, dim_competitor, fact_item_prices, tracked_products
    )

    log(config, "Generating fact_market_share_weekly...")
    fact_market_share_weekly = facts.gen_fact_market_share_weekly(
        config, dim_date, dim_geography, dim_competitor, tracked_products
    )

    log(config, "Generating fact_promo_performance...")
    fact_promo_performance = facts.gen_fact_promo_performance(config, promo_participation, dim_store)

    log(config, "Generating fact_ad_performance...")
    fact_ad_performance = facts.gen_fact_ad_performance(config, ad_participation, dim_ad_placement, dim_ad_channel)

    tables = {
        "dim_date": dim_date,
        "dim_promo_calendar": dim_promo_calendar,
        "dim_product": dim_product,
        "dim_store": dim_store,
        "dim_competitor": dim_competitor,
        "dim_promotion": dim_promotion,
        "dim_ad_channel": dim_ad_channel,
        "dim_ad_placement": dim_ad_placement,
        "dim_vendor": dim_vendor,
        "dim_allowance_type": dim_allowance_type,
        "dim_geography": dim_geography,
        "fact_pos_retail_sales": fact_pos_retail_sales,
        "fact_item_cogs": fact_item_cogs,
        "fact_vendor_allowances": fact_vendor_allowances,
        "fact_item_prices": fact_item_prices,
        "fact_competitor_pricing": fact_competitor_pricing,
        "fact_market_share_weekly": fact_market_share_weekly,
        "fact_promo_performance": fact_promo_performance,
        "fact_ad_performance": fact_ad_performance,
    }

    log(config, "Validating primary key and foreign key integrity...")
    validate.validate(tables, verbose=not getattr(config, "_quiet", False))

    output_dir = Path(config.output_dir)
    log(config, f"Writing CSVs to {output_dir}/ ...")
    writer.write_csvs(tables, output_dir)

    if config.write_sqlite:
        db_path = output_dir / config.sqlite_filename
        log(config, f"Writing SQLite database to {db_path} ...")
        writer.write_sqlite(tables, db_path)

    elapsed = time.time() - t0
    total_rows = sum(len(df) for df in tables.values())
    log(config, f"Done in {elapsed:.1f}s -- {total_rows:,} total rows across {len(tables)} tables.")


if __name__ == "__main__":
    main()
