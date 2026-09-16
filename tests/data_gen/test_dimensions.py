"""Generators for dim_* tables: cardinality, key uniqueness, and that every
categorical value they emit actually comes from reference_data.
"""

from __future__ import annotations


from datagen import dimensions
from datagen import reference_data as ref
from datagen.config import Config

# ---------------------------------------------------------------------------
# dim_product / vendor assignment
# ---------------------------------------------------------------------------


def test_dim_product_row_count_and_pk(built, tiny_config):
    df = built["dim_product"]
    assert len(df) == tiny_config.n_products
    assert df["product_key"].tolist() == list(range(1, tiny_config.n_products + 1))
    assert df["sku_id"].is_unique


def test_dim_product_hierarchy_values_come_from_reference_data(built):
    df = built["dim_product"]
    valid_triples = {
        (dept, cat, subcat)
        for dept, cats in ref.PRODUCT_HIERARCHY.items()
        for cat, subs in cats.items()
        for subcat in subs
    }
    actual_triples = set(
        zip(df["department_name"], df["category_name"], df["sub_category_name"])
    )
    assert actual_triples <= valid_triples


def test_dim_product_weight_items_are_sold_by_the_pound(built):
    df = built["dim_product"]
    weight_rows = df[df["_unit_kind"] == "weight"]
    assert not weight_rows.empty
    assert (weight_rows["package_size_desc"] == "Sold by the Lb").all()


def test_dim_product_vendor_assignment_is_in_range(built, tiny_config):
    df = built["dim_product"]
    assert df["_vendor_key"].between(1, tiny_config.n_vendors).all()
    assert df["_vendor_key"].notna().all()


# ---------------------------------------------------------------------------
# dim_store
# ---------------------------------------------------------------------------


def test_dim_store_row_count_and_pk(built, tiny_config):
    df = built["dim_store"]
    assert len(df) == tiny_config.n_stores
    assert df["store_key"].tolist() == list(range(1, tiny_config.n_stores + 1))
    assert df["store_id"].is_unique


def test_dim_store_state_codes_are_known(built):
    df = built["dim_store"]
    known_states = {state for _, state in ref.US_STATE_CITIES}
    assert set(df["state_code"]) <= known_states


def test_dim_store_square_footage_is_within_any_banner_range(built):
    df = built["dim_store"]
    all_los = [lo for _, (_, (lo, _)) in ref.STORE_BANNERS.items()]
    all_his = [hi for _, (_, (_, hi)) in ref.STORE_BANNERS.items()]
    assert df["square_footage"].between(min(all_los), max(all_his)).all()


# ---------------------------------------------------------------------------
# dim_competitor / dim_geography (sampled without replacement from a pool)
# ---------------------------------------------------------------------------


def test_dim_competitor_row_count_and_uniqueness(built, tiny_config):
    df = built["dim_competitor"]
    assert len(df) == tiny_config.n_competitors
    assert df["competitor_name"].is_unique
    assert set(df["competitor_name"]) <= {name for name, _ in ref.COMPETITORS}


def test_dim_competitor_clamps_to_pool_size_when_config_asks_for_more():
    config = Config(n_competitors=10_000)
    df = dimensions.gen_dim_competitor(config)
    assert len(df) == len(ref.COMPETITORS)


def test_dim_geography_row_count_and_uniqueness(built, tiny_config):
    df = built["dim_geography"]
    assert len(df) == tiny_config.n_market_regions
    assert df["region_name"].is_unique
    assert set(df["region_name"]) <= set(ref.MARKET_REGIONS)
    assert df["syndicated_market_code"].str.match(r"^DMA-\d{3}$").all()


# ---------------------------------------------------------------------------
# dim_promotion
# ---------------------------------------------------------------------------


def test_dim_promotion_row_count_and_pk(built, tiny_config):
    df = built["dim_promotion"]
    assert len(df) == tiny_config.n_promotions
    assert df["promotion_key"].tolist() == list(range(1, tiny_config.n_promotions + 1))


def test_dim_promotion_mechanic_and_min_purchase_are_consistent(built):
    df = built["dim_promotion"]
    known_mechanics = {m for m, _ in ref.PROMO_MECHANICS}
    assert set(df["mechanic_type"]) <= known_mechanics

    for _, row in df.iterrows():
        if row["mechanic_type"] == "BOGO":
            assert row["min_purchase_requirement"] == 2
        elif row["mechanic_type"] in ("Mix-and-Match", "Multi-Buy"):
            assert 2 <= row["min_purchase_requirement"] <= 5
        else:
            assert row["min_purchase_requirement"] == 1


# ---------------------------------------------------------------------------
# dim_ad_channel / dim_ad_placement
# ---------------------------------------------------------------------------


def test_dim_ad_channel_is_a_deterministic_prefix_of_the_pool(built, tiny_config):
    df = built["dim_ad_channel"]
    n = min(tiny_config.n_ad_channels, len(ref.AD_CHANNELS))
    assert len(df) == n
    assert list(zip(df["channel_type"], df["medium_platform"])) == ref.AD_CHANNELS[:n]


def test_dim_ad_placement_row_count_and_fk(built, tiny_config):
    df = built["dim_ad_placement"]
    assert len(df) == tiny_config.n_ad_placements
    valid_channel_keys = set(built["dim_ad_channel"]["ad_channel_key"])
    assert set(df["ad_channel_key"]) <= valid_channel_keys


def test_dim_ad_placement_slot_and_creative_code_format(built):
    df = built["dim_ad_placement"]
    valid_slots = set(ref.AD_PAGE_SLOTS_PRINT) | set(ref.AD_PAGE_SLOTS_DIGITAL)
    assert set(df["page_number_slot"]) <= valid_slots
    assert df["creative_version_code"].str.match(r"^V[1-3]-[ABC]$").all()
    assert df["ad_id"].is_unique


# ---------------------------------------------------------------------------
# dim_vendor / dim_allowance_type
# ---------------------------------------------------------------------------


def test_dim_vendor_row_count_and_id_format(built, tiny_config):
    df = built["dim_vendor"]
    assert len(df) == tiny_config.n_vendors
    assert df["vendor_id"].tolist() == [f"VEND{i:04d}" for i in range(1, tiny_config.n_vendors + 1)]
    assert set(df["payment_terms_desc"]) <= set(ref.PAYMENT_TERMS)


def test_dim_allowance_type_mirrors_reference_data_exactly(built):
    df = built["dim_allowance_type"]
    assert len(df) == len(ref.ALLOWANCE_TYPES)
    assert list(zip(df["allowance_type_code"], df["allowance_type_name"])) == ref.ALLOWANCE_TYPES
    assert df["allowance_type_key"].tolist() == list(range(1, len(ref.ALLOWANCE_TYPES) + 1))
