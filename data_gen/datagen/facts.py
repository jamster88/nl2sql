"""Generators for every fact_* table.

Generation is sequenced so later facts stay consistent with earlier ones:

1. store assortment (which products each store carries)
2. promotion "events" (a promotion running in one promo_calendar cycle,
   over a subset of products/stores) and their daily participation rows
3. ad "flights" (an ad placement running over a subset of products/
   stores) and their daily participation rows
4. fact_item_prices (weekly regular price, promo price during events)
5. fact_pos_retail_sales (priced off of #4, boosted by #2's participation)
6. fact_item_cogs, fact_vendor_allowances (cost side)
7. fact_competitor_pricing, fact_market_share_weekly (competitive side)
8. fact_promo_performance, fact_ad_performance (from #2/#3 participation)

Everything downstream of numpy random calls uses a single seeded
Generator per concern so runs are reproducible given the same Config.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config

MARKET_POSITIONING_PRICE_MULTIPLIER = {
    "Premium/Specialty": (1.08, 1.22),
    "Mainstream Conventional": (0.95, 1.08),
    "Value/Discount": (0.85, 0.97),
    "Club/Warehouse": (0.75, 0.92),
    "Hard Discount": (0.68, 0.85),
    "Convenience/Small Format": (1.05, 1.28),
}

MECHANIC_DISCOUNT_RANGE = {
    "BOGO": (0.45, 0.55),
    "Percent Off": (0.10, 0.40),
    "Dollar Off": (0.08, 0.25),
    "Mix-and-Match": (0.15, 0.35),
    "Multi-Buy": (0.15, 0.35),
    "Loyalty Price Drop": (0.05, 0.20),
}

AD_METRIC_RANGES = {
    "Print Flyer": {"impressions": (5000, 40000), "clicks": (50, 800), "spend": (20, 150)},
    "Digital Mailer": {"impressions": (2000, 15000), "clicks": (100, 1200), "spend": (10, 80)},
    "Paid Social": {"impressions": (3000, 25000), "clicks": (50, 900), "spend": (15, 120)},
    "In-App Push": {"impressions": (1000, 8000), "clicks": (40, 500), "spend": (5, 40)},
    "Website Banner": {"impressions": (2000, 20000), "clicks": (30, 600), "spend": (10, 90)},
    "In-Store Display": {"impressions": (500, 5000), "clicks": (20, 300), "spend": (5, 60)},
    "Search": {"impressions": (1000, 10000), "clicks": (100, 1000), "spend": (20, 150)},
}
_DEFAULT_AD_METRICS = {"impressions": (1000, 10000), "clicks": (50, 500), "spend": (10, 100)}


def _rng(config: Config, salt: int) -> np.random.Generator:
    return np.random.default_rng(config.seed + salt)


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def _weekly_snapshots(dim_date: pd.DataFrame) -> pd.DataFrame:
    g = dim_date.groupby(["fiscal_year", "fiscal_week_num"], as_index=False).agg(
        date_key=("date_key", "min"), calendar_date=("calendar_date", "min")
    )
    return g


def _monthly_snapshots(dim_date: pd.DataFrame) -> pd.DataFrame:
    g = dim_date.groupby(["fiscal_year", "fiscal_month_num"], as_index=False).agg(
        date_key=("date_key", "min"), calendar_date=("calendar_date", "min")
    )
    return g


# ---------------------------------------------------------------------------
# Store assortment
# ---------------------------------------------------------------------------

def build_store_assortment(config: Config, dim_store: pd.DataFrame, dim_product: pd.DataFrame) -> pd.DataFrame:
    rng = _rng(config, 100)
    lo, hi = config.store_assortment_range
    product_keys = dim_product["product_key"].to_numpy()

    rows = []
    for store_key in dim_store["store_key"]:
        frac = rng.uniform(lo, hi)
        n_carry = max(1, int(round(frac * len(product_keys))))
        carried = rng.choice(product_keys, size=n_carry, replace=False)
        rows.append(pd.DataFrame({"store_key": store_key, "product_key": carried}))

    return pd.concat(rows, ignore_index=True)


# ---------------------------------------------------------------------------
# Promotion events & daily participation
# ---------------------------------------------------------------------------

def build_promo_participation(
    config: Config,
    dim_date: pd.DataFrame,
    dim_promo_calendar: pd.DataFrame,
    dim_promotion: pd.DataFrame,
    dim_product: pd.DataFrame,
    dim_store: pd.DataFrame,
) -> pd.DataFrame:
    rng = _rng(config, 200)
    product_keys = dim_product["product_key"].to_numpy()
    store_keys = dim_store["store_key"].to_numpy()
    date_lookup = dim_date[["date_key", "calendar_date", "fiscal_year", "fiscal_week_num"]]

    lo_p, hi_p = config.promo_products_per_event_range
    events = []
    for _, promo in dim_promotion.iterrows():
        cycle = dim_promo_calendar.iloc[int(rng.integers(0, len(dim_promo_calendar)))]

        n_products = min(len(product_keys), int(rng.integers(lo_p, hi_p + 1)))
        event_products = rng.choice(product_keys, size=n_products, replace=False)

        store_frac = rng.uniform(0.5, 1.0)
        n_stores = max(1, int(round(store_frac * len(store_keys))))
        event_stores = rng.choice(store_keys, size=n_stores, replace=False)

        lo_disc, hi_disc = MECHANIC_DISCOUNT_RANGE.get(promo["mechanic_type"], (0.10, 0.30))
        discount_pct = float(rng.uniform(lo_disc, hi_disc))

        mask = (date_lookup["calendar_date"] >= cycle["cycle_start_date"]) & (
            date_lookup["calendar_date"] <= cycle["cycle_end_date"]
        )
        event_dates = date_lookup.loc[mask]
        if event_dates.empty:
            continue

        events.append(
            {
                "promotion_key": promo["promotion_key"],
                "promo_calendar_key": cycle["promo_calendar_key"],
                "products": event_products,
                "stores": event_stores,
                "dates": event_dates,
                "discount_pct": discount_pct,
            }
        )

    frames = []
    for ev in events:
        n_p, n_s, n_d = len(ev["products"]), len(ev["stores"]), len(ev["dates"])
        idx = pd.MultiIndex.from_product(
            [ev["products"], ev["stores"]], names=["product_key", "store_key"]
        )
        base = idx.to_frame(index=False)
        expanded = base.merge(ev["dates"], how="cross")
        expanded["promotion_key"] = ev["promotion_key"]
        expanded["promo_calendar_key"] = ev["promo_calendar_key"]
        expanded["discount_pct"] = ev["discount_pct"]
        frames.append(expanded)

    if not frames:
        return pd.DataFrame(
            columns=[
                "promotion_key", "promo_calendar_key", "product_key", "store_key",
                "date_key", "calendar_date", "fiscal_year", "fiscal_week_num", "discount_pct",
            ]
        )

    result = pd.concat(frames, ignore_index=True)
    # A product/store can only carry one active promo price at a time --
    # keep the deepest discount when events overlap.
    result = result.sort_values("discount_pct", ascending=False).drop_duplicates(
        subset=["product_key", "store_key", "date_key"], keep="first"
    )
    return result.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Ad flights & daily participation
# ---------------------------------------------------------------------------

def build_ad_participation(
    config: Config,
    dim_date: pd.DataFrame,
    dim_ad_placement: pd.DataFrame,
    dim_product: pd.DataFrame,
    dim_store: pd.DataFrame,
) -> pd.DataFrame:
    rng = _rng(config, 300)
    product_keys = dim_product["product_key"].to_numpy()
    store_keys = dim_store["store_key"].to_numpy()
    date_lookup = dim_date[["date_key", "calendar_date"]]
    start, end = dim_date["calendar_date"].min(), dim_date["calendar_date"].max()

    lo_len, hi_len = config.ad_flight_length_days_range
    lo_p, hi_p = config.ad_products_per_placement_range

    frames = []
    for _, placement in dim_ad_placement.iterrows():
        length = int(rng.integers(lo_len, hi_len + 1))
        max_start_offset = max(0, (end - start).days - length)
        flight_start = start + pd.Timedelta(days=int(rng.integers(0, max_start_offset + 1)))
        flight_end = min(end, flight_start + pd.Timedelta(days=length - 1))

        n_products = min(len(product_keys), int(rng.integers(lo_p, hi_p + 1)))
        flight_products = rng.choice(product_keys, size=n_products, replace=False)

        store_frac = rng.uniform(0.4, 1.0)
        n_stores = max(1, int(round(store_frac * len(store_keys))))
        flight_stores = rng.choice(store_keys, size=n_stores, replace=False)

        mask = (date_lookup["calendar_date"] >= flight_start) & (date_lookup["calendar_date"] <= flight_end)
        flight_dates = date_lookup.loc[mask]
        if flight_dates.empty:
            continue

        idx = pd.MultiIndex.from_product(
            [flight_products, flight_stores], names=["product_key", "store_key"]
        )
        base = idx.to_frame(index=False)
        expanded = base.merge(flight_dates, how="cross")
        expanded["ad_placement_key"] = placement["ad_placement_key"]
        frames.append(expanded)

    if not frames:
        return pd.DataFrame(columns=["ad_placement_key", "product_key", "store_key", "date_key", "calendar_date"])

    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# fact_item_prices
# ---------------------------------------------------------------------------

def gen_fact_item_prices(
    config: Config,
    dim_date: pd.DataFrame,
    dim_product: pd.DataFrame,
    dim_store: pd.DataFrame,
    store_assortment: pd.DataFrame,
    promo_participation: pd.DataFrame,
) -> pd.DataFrame:
    rng = _rng(config, 400)
    weeks = _weekly_snapshots(dim_date).reset_index(drop=True)
    n_weeks = len(weeks)

    store_index = store_assortment.merge(
        dim_product[["product_key", "_price_anchor"]], on="product_key", how="left"
    )
    n_pairs = len(store_index)

    store_price_index = rng.normal(1.0, 0.05, size=dim_store["store_key"].max() + 1)
    store_price_index = np.clip(store_price_index, 0.85, 1.15)

    steps = rng.normal(0, 0.01, size=(n_pairs, n_weeks))
    drift = np.clip(np.cumsum(steps, axis=1), -0.12, 0.15)

    anchors = store_index["_price_anchor"].to_numpy()[:, None]
    store_mult = store_price_index[store_index["store_key"].to_numpy()][:, None]
    prices = anchors * store_mult * (1 + drift)
    prices = np.round(prices, 2)

    pair_repeat = np.repeat(store_index[["product_key", "store_key"]].to_numpy(), n_weeks, axis=0)
    week_tile = np.tile(weeks[["date_key", "fiscal_year", "fiscal_week_num"]].to_numpy(), (n_pairs, 1))

    df = pd.DataFrame(
        {
            "product_key": pair_repeat[:, 0],
            "store_key": pair_repeat[:, 1],
            "date_key": week_tile[:, 0],
            "fiscal_year": week_tile[:, 1],
            "fiscal_week_num": week_tile[:, 2],
            "regular_retail_price": prices.ravel(),
        }
    )

    promo_weeks = (
        promo_participation[["product_key", "store_key", "fiscal_year", "fiscal_week_num", "discount_pct"]]
        .drop_duplicates(subset=["product_key", "store_key", "fiscal_year", "fiscal_week_num"])
    )
    df = df.merge(promo_weeks, on=["product_key", "store_key", "fiscal_year", "fiscal_week_num"], how="left")
    df["base_promo_price"] = np.where(
        df["discount_pct"].notna(),
        np.round(df["regular_retail_price"] * (1 - df["discount_pct"]), 2),
        np.nan,
    )
    df = df.drop(columns=["discount_pct", "fiscal_year", "fiscal_week_num"])
    return df


# ---------------------------------------------------------------------------
# fact_pos_retail_sales
# ---------------------------------------------------------------------------

def _sample_baskets_products(
    rng: np.random.Generator, assortment: np.ndarray, k_array: np.ndarray
) -> np.ndarray:
    """For each basket i, return up to k_array[i] distinct product_keys
    drawn from `assortment`, as a (len(k_array), max_k) matrix padded with
    -1. Uses vectorized slot-by-slot sampling with a handful of collision
    retries rather than a per-basket Python loop.
    """
    b = len(k_array)
    max_k = int(k_array.max()) if b else 0
    a = len(assortment)
    result = np.full((b, max_k), -1, dtype=np.int64)

    for slot in range(max_k):
        need = k_array > slot
        idx = np.where(need)[0]
        if len(idx) == 0:
            # Unreachable, and kept only as a guard against the loop bounds
            # changing: the loop runs to max(k_array), so the basket whose k
            # equals that maximum satisfies k > slot for every slot the loop
            # visits, and `idx` therefore always holds at least that one.
            continue  # pragma: no cover
        result[idx, slot] = assortment[rng.integers(0, a, size=len(idx))]
        for _ in range(6):
            if slot == 0:
                break
            dup = np.zeros(len(idx), dtype=bool)
            for prev in range(slot):
                dup |= result[idx, prev] == result[idx, slot]
            if not dup.any():
                break
            resample_idx = idx[dup]
            result[resample_idx, slot] = assortment[rng.integers(0, a, size=len(resample_idx))]

    return result


def gen_fact_pos_retail_sales(
    config: Config,
    dim_date: pd.DataFrame,
    dim_product: pd.DataFrame,
    dim_store: pd.DataFrame,
    store_assortment: pd.DataFrame,
    fact_item_prices: pd.DataFrame,
) -> pd.DataFrame:
    rng = _rng(config, 500)
    lo_b, hi_b = config.baskets_per_store_day_range
    lo_i, hi_i = config.items_per_basket_range

    stores = dim_store["store_key"].to_numpy()
    days = dim_date[["date_key", "calendar_date", "fiscal_year", "fiscal_week_num", "is_holiday"]].copy()
    days["dow"] = pd.to_datetime(days["calendar_date"]).dt.dayofweek

    n_stores, n_days = len(stores), len(days)
    base_counts = rng.integers(lo_b, hi_b + 1, size=(n_stores, n_days))
    weekend_mask = (days["dow"].to_numpy() >= 5)[None, :]
    holiday_mask = days["is_holiday"].to_numpy()[None, :]
    mult = np.ones((n_stores, n_days))
    mult = np.where(weekend_mask, mult * config.weekend_basket_multiplier, mult)
    mult = np.where(holiday_mask, mult * config.holiday_basket_multiplier, mult)
    counts = np.round(base_counts * mult).astype(int)

    store_rep = np.repeat(stores, n_days)
    day_rep = np.tile(days["date_key"].to_numpy(), n_stores)
    counts_flat = counts.ravel()

    baskets = pd.DataFrame(
        {
            "store_key": np.repeat(store_rep, counts_flat),
            "date_key": np.repeat(day_rep, counts_flat),
        }
    )
    n_baskets = len(baskets)
    baskets["basket_id"] = [f"BSK{i:010d}" for i in range(1, n_baskets + 1)]
    baskets["k"] = rng.integers(lo_i, hi_i + 1, size=n_baskets)

    assortment_by_store = store_assortment.groupby("store_key")["product_key"].apply(
        lambda s: s.to_numpy()
    )

    line_frames = []
    for store_key, grp in baskets.groupby("store_key", sort=False):
        assortment = assortment_by_store.loc[store_key]
        k_array = grp["k"].to_numpy()
        result = _sample_baskets_products(rng, assortment, k_array)
        max_k = result.shape[1] if result.size else 0
        mask = np.arange(max_k)[None, :] < k_array[:, None]

        product_keys_flat = result[mask]
        basket_id_flat = np.repeat(grp["basket_id"].to_numpy(), k_array)
        date_key_flat = np.repeat(grp["date_key"].to_numpy(), k_array)

        valid = product_keys_flat >= 0
        line_frames.append(
            pd.DataFrame(
                {
                    "sales_date_key": date_key_flat[valid],
                    "store_key": store_key,
                    "basket_id": basket_id_flat[valid],
                    "product_key": product_keys_flat[valid],
                }
            )
        )

    lines = pd.concat(line_frames, ignore_index=True)
    lines = lines.drop_duplicates(subset=["sales_date_key", "product_key", "store_key", "basket_id"])

    lines = lines.merge(
        dim_date[["date_key", "fiscal_year", "fiscal_week_num"]],
        left_on="sales_date_key", right_on="date_key", how="left",
    ).drop(columns=["date_key"])

    price_weeks = fact_item_prices.merge(
        dim_date[["date_key", "fiscal_year", "fiscal_week_num"]], on="date_key", how="left"
    ).drop(columns=["date_key"])

    lines = lines.merge(
        price_weeks, on=["product_key", "store_key", "fiscal_year", "fiscal_week_num"], how="left"
    )

    lines = lines.merge(dim_product[["product_key", "_unit_kind"]], on="product_key", how="left")

    n = len(lines)
    is_weight = (lines["_unit_kind"] == "weight").to_numpy()
    qty = np.where(
        is_weight,
        np.round(rng.uniform(0.4, 3.5, size=n), 3),
        rng.choice([1, 2, 3, 4], size=n, p=[0.70, 0.15, 0.10, 0.05]).astype(float),
    )

    has_promo = lines["base_promo_price"].notna().to_numpy()
    adopts_promo = has_promo & (rng.random(n) < 0.55)

    regular = lines["regular_retail_price"].to_numpy()
    promo = lines["base_promo_price"].fillna(0).to_numpy()

    gross = np.round(regular * qty, 2)

    is_perishable_clearance = (~adopts_promo) & is_weight & (rng.random(n) < 0.04)
    clearance_pct = rng.uniform(0.10, 0.30, size=n)

    markdown = np.where(
        adopts_promo,
        np.round((regular - promo) * qty, 2),
        np.where(is_perishable_clearance, np.round(regular * clearance_pct * qty, 2), 0.0),
    )
    net = np.round(gross - markdown, 2)

    out = pd.DataFrame(
        {
            "sales_date_key": lines["sales_date_key"].to_numpy(),
            "product_key": lines["product_key"].to_numpy(),
            "store_key": lines["store_key"].to_numpy(),
            "basket_id": lines["basket_id"].to_numpy(),
            "quantity_sold": qty,
            "gross_sales_amt": gross,
            "markdown_discount_amt": markdown,
            "net_sales_amt": net,
        }
    )
    return out


# ---------------------------------------------------------------------------
# fact_item_cogs
# ---------------------------------------------------------------------------

def gen_fact_item_cogs(
    config: Config, dim_date: pd.DataFrame, dim_product: pd.DataFrame, store_assortment: pd.DataFrame
) -> pd.DataFrame:
    rng = _rng(config, 600)
    months = _monthly_snapshots(dim_date).reset_index(drop=True)
    n_months = len(months)

    pairs = store_assortment.merge(
        dim_product[["product_key", "_price_anchor"]], on="product_key", how="left"
    )
    n_pairs = len(pairs)

    margin = rng.uniform(0.55, 0.75, size=n_pairs)[:, None]
    steps = rng.normal(0, 0.008, size=(n_pairs, n_months))
    drift = np.clip(np.cumsum(steps, axis=1), -0.1, 0.1)

    anchors = pairs["_price_anchor"].to_numpy()[:, None]
    base_cost = anchors * margin * (1 + drift)
    freight_cost = base_cost * rng.uniform(0.01, 0.05, size=(n_pairs, n_months))
    net_cost = base_cost + freight_cost

    pair_repeat = np.repeat(pairs[["product_key", "store_key"]].to_numpy(), n_months, axis=0)
    month_tile = np.tile(months["date_key"].to_numpy(), n_pairs)

    return pd.DataFrame(
        {
            "date_key": month_tile,
            "product_key": pair_repeat[:, 0],
            "store_key": pair_repeat[:, 1],
            "base_cost": np.round(base_cost.ravel(), 4),
            "freight_cost": np.round(freight_cost.ravel(), 4),
            "net_item_cost": np.round(net_cost.ravel(), 4),
        }
    )


# ---------------------------------------------------------------------------
# fact_vendor_allowances
# ---------------------------------------------------------------------------

def gen_fact_vendor_allowances(
    config: Config,
    dim_date: pd.DataFrame,
    dim_product: pd.DataFrame,
    dim_store: pd.DataFrame,
    dim_allowance_type: pd.DataFrame,
) -> pd.DataFrame:
    rng = _rng(config, 700)
    months = _monthly_snapshots(dim_date).reset_index(drop=True)
    store_keys = dim_store["store_key"].to_numpy()
    allowance_keys = dim_allowance_type["allowance_type_key"].to_numpy()

    rows = []
    for _, month in months.iterrows():
        active = dim_product.sample(frac=rng.uniform(0.45, 0.65), random_state=int(rng.integers(0, 2**31)))
        for _, prod in active.iterrows():
            n_types = 1 if rng.random() < 0.8 else 2
            types = rng.choice(allowance_keys, size=n_types, replace=False)
            for allowance_type_key in types:
                rate = round(float(prod["_price_anchor"] * rng.uniform(0.01, 0.08)), 4)
                approx_units_per_store = rng.uniform(50, 500)
                for store_key in store_keys:
                    total = round(rate * approx_units_per_store * rng.uniform(0.8, 1.2), 2)
                    rows.append(
                        {
                            "date_key": month["date_key"],
                            "product_key": prod["product_key"],
                            "store_key": store_key,
                            "vendor_key": int(prod["_vendor_key"]),
                            "allowance_type_key": int(allowance_type_key),
                            "allowance_rate_per_unit": rate,
                            "total_allowance_amt": total,
                        }
                    )

    return pd.DataFrame(rows)


def select_tracked_products(config: Config, dim_product: pd.DataFrame) -> np.ndarray:
    """The curated subset of SKUs that competitive-intelligence and
    syndicated market-share reporting cover -- real programs track key
    items, not the full catalog.
    """
    rng = _rng(config, 50000)
    product_keys = dim_product["product_key"].to_numpy()
    n_tracked = max(1, int(round(config.market_share_product_fraction * len(product_keys))))
    return rng.choice(product_keys, size=n_tracked, replace=False)


# ---------------------------------------------------------------------------
# fact_competitor_pricing
# ---------------------------------------------------------------------------

def gen_fact_competitor_pricing(
    config: Config,
    dim_date: pd.DataFrame,
    dim_product: pd.DataFrame,
    dim_store: pd.DataFrame,
    dim_competitor: pd.DataFrame,
    fact_item_prices: pd.DataFrame,
    tracked_products: np.ndarray,
) -> pd.DataFrame:
    rng = _rng(config, 800)
    weeks = _weekly_snapshots(dim_date).reset_index(drop=True)

    n_audit = max(1, int(round(config.audit_store_fraction * len(dim_store))))
    audit_stores = rng.choice(dim_store["store_key"].to_numpy(), size=n_audit, replace=False)

    competitor_rows = dim_competitor.set_index("competitor_key")["market_positioning"]

    visit_frames = []
    for store_key in audit_stores:
        for _, wk in weeks.iterrows():
            n_visit = int(rng.integers(1, min(3, len(dim_competitor)) + 1))
            visitors = rng.choice(dim_competitor["competitor_key"].to_numpy(), size=n_visit, replace=False)
            visit_frames.append(
                pd.DataFrame(
                    {
                        "store_key": store_key,
                        "date_key": wk["date_key"],
                        "competitor_key": visitors,
                    }
                )
            )
    visits = pd.concat(visit_frames, ignore_index=True)

    products_df = pd.DataFrame({"product_key": tracked_products})
    combos = visits.merge(products_df, how="cross")

    our_prices = fact_item_prices[["date_key", "product_key", "store_key", "regular_retail_price"]]
    combos = combos.merge(our_prices, on=["date_key", "product_key", "store_key"], how="inner")

    n = len(combos)
    positioning = combos["competitor_key"].map(competitor_rows)
    lo = positioning.map(lambda p: MARKET_POSITIONING_PRICE_MULTIPLIER.get(p, (0.9, 1.1))[0]).to_numpy()
    hi = positioning.map(lambda p: MARKET_POSITIONING_PRICE_MULTIPLIER.get(p, (0.9, 1.1))[1]).to_numpy()
    mult = rng.uniform(lo, hi)

    comp_regular = np.round(combos["regular_retail_price"].to_numpy() * mult, 2)
    has_promo = rng.random(n) < 0.25
    comp_promo = np.where(has_promo, np.round(comp_regular * rng.uniform(0.75, 0.92, size=n), 2), np.nan)

    return pd.DataFrame(
        {
            "date_key": combos["date_key"].to_numpy(),
            "product_key": combos["product_key"].to_numpy(),
            "competitor_key": combos["competitor_key"].to_numpy(),
            "store_key": combos["store_key"].to_numpy(),
            "comp_regular_price": comp_regular,
            "comp_promo_price": comp_promo,
        }
    ).drop_duplicates(subset=["date_key", "product_key", "competitor_key", "store_key"])


# ---------------------------------------------------------------------------
# fact_market_share_weekly
# ---------------------------------------------------------------------------

def gen_fact_market_share_weekly(
    config: Config,
    dim_date: pd.DataFrame,
    dim_geography: pd.DataFrame,
    dim_competitor: pd.DataFrame,
    tracked_products: np.ndarray,
) -> pd.DataFrame:
    rng = _rng(config, 900)
    weeks = _weekly_snapshots(dim_date).reset_index(drop=True)
    regions = dim_geography["market_region_key"].to_numpy()
    competitors = dim_competitor["competitor_key"].to_numpy()
    n_comp = len(competitors)

    idx = pd.MultiIndex.from_product(
        [weeks["date_key"], tracked_products, regions], names=["week_key", "product_key", "market_region_key"]
    )
    base = idx.to_frame(index=False)
    n = len(base)

    total_market = rng.uniform(20000, 200000, size=n)
    grocer_share = rng.uniform(0.08, 0.35, size=n)
    grocer_amount = total_market * grocer_share
    remaining = total_market - grocer_amount

    dirichlet_weights = rng.dirichlet(np.ones(n_comp), size=n)
    competitor_amounts = remaining[:, None] * dirichlet_weights

    base = base.loc[base.index.repeat(n_comp)].reset_index(drop=True)
    base["competitor_key"] = np.tile(competitors, n)
    base["grocer_sales_amount"] = np.round(np.repeat(grocer_amount, n_comp), 2)
    base["competitor_sales_amount"] = np.round(competitor_amounts.ravel(), 2)
    base["total_market_sales_amount"] = np.round(np.repeat(total_market, n_comp), 2)

    return base.rename(columns={})


# ---------------------------------------------------------------------------
# fact_promo_performance
# ---------------------------------------------------------------------------

def gen_fact_promo_performance(config: Config, promo_participation: pd.DataFrame, dim_store: pd.DataFrame) -> pd.DataFrame:
    if promo_participation.empty:
        return pd.DataFrame(
            columns=[
                "date_key", "promo_calendar_key", "product_key", "store_key",
                "promotion_key", "promo_quantity_sold", "promo_quantity_lift",
            ]
        )

    rng = _rng(config, 1000)
    n = len(promo_participation)

    sqft = dim_store.set_index("store_key")["square_footage"]
    size_factor = (promo_participation["store_key"].map(sqft) / sqft.mean()).to_numpy()

    baseline_daily_qty = rng.uniform(8, 60, size=n) * size_factor
    lift_frac = rng.uniform(0.15, 0.45, size=n)

    promo_qty = np.round(baseline_daily_qty, 3)
    lift = np.round(baseline_daily_qty * lift_frac, 3)

    return pd.DataFrame(
        {
            "date_key": promo_participation["date_key"].to_numpy(),
            "promo_calendar_key": promo_participation["promo_calendar_key"].to_numpy(),
            "product_key": promo_participation["product_key"].to_numpy(),
            "store_key": promo_participation["store_key"].to_numpy(),
            "promotion_key": promo_participation["promotion_key"].to_numpy(),
            "promo_quantity_sold": promo_qty,
            "promo_quantity_lift": lift,
        }
    )


# ---------------------------------------------------------------------------
# fact_ad_performance
# ---------------------------------------------------------------------------

def gen_fact_ad_performance(
    config: Config,
    ad_participation: pd.DataFrame,
    dim_ad_placement: pd.DataFrame,
    dim_ad_channel: pd.DataFrame,
) -> pd.DataFrame:
    if ad_participation.empty:
        return pd.DataFrame(
            columns=[
                "date_key", "ad_placement_key", "product_key", "store_key",
                "ad_spend_amount", "impressions_count", "clicks_or_coupon_clips_count",
            ]
        )

    rng = _rng(config, 1100)
    placement_channel = dim_ad_placement.set_index("ad_placement_key")["ad_channel_key"]
    channel_type = dim_ad_channel.set_index("ad_channel_key")["channel_type"]

    merged = ad_participation.copy()
    merged["channel_type"] = merged["ad_placement_key"].map(placement_channel).map(channel_type)

    n = len(merged)
    impressions = np.empty(n)
    clicks = np.empty(n)
    spend = np.empty(n)

    for ctype, sub_idx in merged.groupby("channel_type", sort=False).groups.items():
        pos = merged.index.get_indexer(sub_idx)
        ranges = AD_METRIC_RANGES.get(ctype, _DEFAULT_AD_METRICS)
        impr_lo, impr_hi = ranges["impressions"]
        click_lo, click_hi = ranges["clicks"]
        spend_lo, spend_hi = ranges["spend"]
        impressions[pos] = rng.integers(impr_lo, impr_hi + 1, size=len(pos))
        clicks[pos] = rng.integers(click_lo, click_hi + 1, size=len(pos))
        spend[pos] = np.round(rng.uniform(spend_lo, spend_hi, size=len(pos)), 2)

    clicks = np.minimum(clicks, impressions)

    return pd.DataFrame(
        {
            "date_key": merged["date_key"].to_numpy(),
            "ad_placement_key": merged["ad_placement_key"].to_numpy(),
            "product_key": merged["product_key"].to_numpy(),
            "store_key": merged["store_key"].to_numpy(),
            "ad_spend_amount": spend,
            "impressions_count": impressions.astype(int),
            "clicks_or_coupon_clips_count": clicks.astype(int),
        }
    )
