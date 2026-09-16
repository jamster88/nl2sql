"""Generators for every dim_* table.

Each function returns a pandas DataFrame. Product-related frames carry a
few extra "working" columns beyond the official ddl.sql schema (e.g. a
price anchor, unit-of-sale kind, primary vendor) that downstream fact
generators need to keep numbers consistent; writer.py strips these back
down to the official column list before anything is persisted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from faker import Faker

from .config import Config
from . import reference_data as ref


def _rng(config: Config, salt: int) -> np.random.Generator:
    return np.random.default_rng(config.seed + salt)


def gen_dim_product(config: Config) -> pd.DataFrame:
    rng = _rng(config, 10)

    # Flatten the hierarchy into one row per subcategory, weighted by how
    # many base item names it has (richer subcategories get more SKUs).
    subcats = []
    for dept, cats in ref.PRODUCT_HIERARCHY.items():
        for cat, subs in cats.items():
            for subcat, (names, unit_kind, price_range) in subs.items():
                subcats.append((dept, cat, subcat, names, unit_kind, price_range))

    weights = np.array([len(s[3]) for s in subcats], dtype=float)
    weights /= weights.sum()
    picks = rng.choice(len(subcats), size=config.n_products, p=weights)

    pack_sizes_each = [
        "6 oz", "8 oz", "12 oz", "16 oz", "24 oz", "32 oz", "1 Lb", "2 Lb",
        "4-Pack", "6-Pack", "8-Pack", "12-Pack", "24-Pack", "Single",
    ]

    rows = []
    for i, subcat_idx in enumerate(picks, start=1):
        dept, cat, subcat, names, unit_kind, (lo, hi) = subcats[subcat_idx]
        base_name = names[rng.integers(0, len(names))]

        is_private_label = rng.random() < 0.18
        brand = (
            ref.PRIVATE_LABEL_BRANDS[rng.integers(0, len(ref.PRIVATE_LABEL_BRANDS))]
            if is_private_label
            else ref.NATIONAL_BRANDS[rng.integers(0, len(ref.NATIONAL_BRANDS))]
        )

        if unit_kind == "weight":
            package_size = "Sold by the Lb"
        elif any(ch.isdigit() for ch in base_name) or "Pack" in base_name or "Box" in base_name:
            # Base name already carries its own size/count (e.g. "Cola
            # 12-Pack", "Diapers Size 3 Box") -- don't add a conflicting one.
            package_size = None
        else:
            package_size = pack_sizes_each[rng.integers(0, len(pack_sizes_each))]

        price_anchor = round(float(rng.uniform(lo, hi)), 2)

        rows.append(
            {
                "product_key": i,
                "sku_id": f"SKU{100000 + i}",
                "upc_barcode": f"{rng.integers(0, 10**12):012d}",
                "product_name": f"{brand} {base_name}",
                "brand_name": brand,
                "department_name": dept,
                "category_name": cat,
                "sub_category_name": subcat,
                "package_size_desc": package_size,
                "is_private_label": bool(is_private_label),
                # working columns, stripped before export
                "_unit_kind": unit_kind,
                "_price_anchor": price_anchor,
                "_vendor_key": None,  # filled once dim_vendor exists
            }
        )

    return pd.DataFrame(rows)


def assign_product_vendors(config: Config, dim_product: pd.DataFrame, n_vendors: int) -> pd.DataFrame:
    rng = _rng(config, 11)
    dim_product = dim_product.copy()
    dim_product["_vendor_key"] = rng.integers(1, n_vendors + 1, size=len(dim_product))
    return dim_product


def gen_dim_store(config: Config) -> pd.DataFrame:
    rng = _rng(config, 20)
    banners = list(ref.STORE_BANNERS.items())
    cities = ref.US_STATE_CITIES

    rows = []
    for i in range(1, config.n_stores + 1):
        banner_name, (layout_type, sqft_range) = banners[rng.integers(0, len(banners))]
        city, state = cities[rng.integers(0, len(cities))]
        rows.append(
            {
                "store_key": i,
                "store_id": f"STR{i:03d}",
                "store_name": f"{banner_name} - {city}",
                "banner_name": banner_name,
                "street_address": f"{int(rng.integers(100, 9999))} {['Main St','Oak Ave','Market St','Commerce Dr','Elm St'][rng.integers(0,5)]}",
                "city": city,
                "state_code": state,
                "postal_code": f"{int(rng.integers(10000, 99999))}",
                "square_footage": int(rng.integers(sqft_range[0], sqft_range[1] + 1)),
                "layout_type_desc": layout_type,
            }
        )
    return pd.DataFrame(rows)


def gen_dim_competitor(config: Config) -> pd.DataFrame:
    rng = _rng(config, 30)
    pool = ref.COMPETITORS
    n = min(config.n_competitors, len(pool))
    idx = rng.choice(len(pool), size=n, replace=False)

    rows = []
    for i, pick in enumerate(idx, start=1):
        name, positioning = pool[pick]
        rows.append(
            {
                "competitor_key": i,
                "competitor_id": f"COMP{i:02d}",
                "competitor_name": name,
                "banner_name": name,
                "market_positioning": positioning,
            }
        )
    return pd.DataFrame(rows)


def gen_dim_promotion(config: Config) -> pd.DataFrame:
    rng = _rng(config, 40)
    mechanics = [m for m, _ in ref.PROMO_MECHANICS]
    weights = np.array([w for _, w in ref.PROMO_MECHANICS], dtype=float)
    weights /= weights.sum()

    theme_words = [
        "Savings", "Value Days", "Deal Days", "Bonus Buy", "Special", "Blast",
        "Bundle", "Flash Sale", "Weekend Deal", "Shopper Special",
    ]

    rows = []
    for i in range(1, config.n_promotions + 1):
        mechanic = mechanics[rng.choice(len(mechanics), p=weights)]
        season = list(ref.SEASON_BY_MONTH.values())[rng.integers(0, 12)]
        name = f"{season} {theme_words[rng.integers(0, len(theme_words))]}"

        if mechanic == "BOGO":
            min_purchase = 2
        elif mechanic in ("Mix-and-Match", "Multi-Buy"):
            min_purchase = int(rng.integers(2, 6))
        else:
            min_purchase = 1

        rows.append(
            {
                "promotion_key": i,
                "promotion_id": f"PROMO{i:04d}",
                "promotion_name": name,
                "mechanic_type": mechanic,
                "min_purchase_requirement": min_purchase,
            }
        )
    return pd.DataFrame(rows)


def gen_dim_ad_channel(config: Config) -> pd.DataFrame:
    pool = ref.AD_CHANNELS
    n = min(config.n_ad_channels, len(pool))
    rows = []
    for i, (channel_type, platform) in enumerate(pool[:n], start=1):
        rows.append(
            {
                "ad_channel_key": i,
                "channel_id": f"CH{i:02d}",
                "channel_type": channel_type,
                "medium_platform": platform,
            }
        )
    return pd.DataFrame(rows)


def gen_dim_ad_placement(config: Config, dim_ad_channel: pd.DataFrame) -> pd.DataFrame:
    rng = _rng(config, 50)
    channel_keys = dim_ad_channel["ad_channel_key"].to_numpy()
    channel_types = dim_ad_channel.set_index("ad_channel_key")["channel_type"]

    theme_subjects = [
        "Ribeye Extravaganza", "Snack Time Savings", "Fresh Produce Roundup",
        "Weeknight Dinner Solutions", "Breakfast Favorites", "Grilling Season",
        "Holiday Entertaining", "Back to School Lunches", "Game Day Spread",
        "Farm Fresh Dairy", "Pantry Stock-Up", "Sweet Treats",
    ]
    seasons = list(ref.SEASON_BY_MONTH.values())

    rows = []
    for i in range(1, config.n_ad_placements + 1):
        channel_key = int(channel_keys[rng.integers(0, len(channel_keys))])
        channel_type = channel_types.loc[channel_key]
        is_print_or_instore = channel_type in ("Print Flyer", "In-Store Display")
        slot_pool = ref.AD_PAGE_SLOTS_PRINT if is_print_or_instore else ref.AD_PAGE_SLOTS_DIGITAL

        season = seasons[rng.integers(0, len(seasons))]
        subject = theme_subjects[rng.integers(0, len(theme_subjects))]

        rows.append(
            {
                "ad_placement_key": i,
                "ad_id": f"AD{i:05d}",
                "ad_channel_key": channel_key,
                "ad_theme_name": f"{season}: {subject}",
                "creative_version_code": f"V{rng.integers(1, 4)}-{'ABC'[rng.integers(0, 3)]}",
                "page_number_slot": slot_pool[rng.integers(0, len(slot_pool))],
                "is_front_page_feature": bool(rng.random() < 0.12),
            }
        )
    return pd.DataFrame(rows)


def gen_dim_vendor(config: Config) -> pd.DataFrame:
    rng = _rng(config, 60)
    faker = Faker()
    faker.seed_instance(config.seed + 60)

    rows = []
    for i in range(1, config.n_vendors + 1):
        base = faker.last_name()
        suffix = ref.VENDOR_SUFFIXES[rng.integers(0, len(ref.VENDOR_SUFFIXES))]
        rows.append(
            {
                "vendor_key": i,
                "vendor_id": f"VEND{i:04d}",
                "vendor_name": f"{base} {suffix}",
                "payment_terms_desc": ref.PAYMENT_TERMS[rng.integers(0, len(ref.PAYMENT_TERMS))],
            }
        )
    return pd.DataFrame(rows)


def gen_dim_allowance_type(config: Config) -> pd.DataFrame:
    rows = []
    for i, (code, name) in enumerate(ref.ALLOWANCE_TYPES, start=1):
        rows.append(
            {
                "allowance_type_key": i,
                "allowance_type_code": code,
                "allowance_type_name": name,
            }
        )
    return pd.DataFrame(rows)


def gen_dim_geography(config: Config) -> pd.DataFrame:
    rng = _rng(config, 70)
    pool = ref.MARKET_REGIONS
    n = min(config.n_market_regions, len(pool))
    idx = rng.choice(len(pool), size=n, replace=False)

    rows = []
    for i, pick in enumerate(idx, start=1):
        rows.append(
            {
                "market_region_key": i,
                "region_id": f"RGN{i:02d}",
                "region_name": pool[pick],
                "syndicated_market_code": f"DMA-{int(rng.integers(100, 999))}",
            }
        )
    return pd.DataFrame(rows)
