"""validate.validate(): PK uniqueness / FK integrity checks, plus the
schema-consistency of the PRIMARY_KEYS / FOREIGN_KEYS maps themselves.
"""

from __future__ import annotations

import pandas as pd
import pytest
from datagen import validate
from datagen.schema_columns import TABLE_ORDER


def test_generated_dataset_passes_validation(official_tables):
    # Should not raise.
    validate.validate(official_tables, verbose=False)


def test_duplicate_primary_key_is_caught(official_tables):
    tables = dict(official_tables)
    dim_store = tables["dim_store"]
    tables["dim_store"] = pd.concat([dim_store, dim_store.iloc[[0]]], ignore_index=True)
    with pytest.raises(AssertionError, match="duplicate rows on PK"):
        validate.validate(tables, verbose=False)


def test_orphan_foreign_key_is_caught(official_tables):
    tables = dict(official_tables)
    sales = tables["fact_pos_retail_sales"].copy()
    bogus_store_key = int(tables["dim_store"]["store_key"].max()) + 1000
    sales.loc[sales.index[0], "store_key"] = bogus_store_key
    tables["fact_pos_retail_sales"] = sales
    with pytest.raises(AssertionError, match="reference a missing"):
        validate.validate(tables, verbose=False)


def test_every_foreign_key_table_and_referenced_table_is_declared():
    known_tables = set(validate.PRIMARY_KEYS)
    for table, _fk_col, ref_table, _ref_col in validate.FOREIGN_KEYS:
        assert table in known_tables, f"{table} has a declared FK but no PRIMARY_KEYS entry"
        assert ref_table in known_tables, f"{ref_table} is referenced but has no PRIMARY_KEYS entry"


def test_primary_keys_cover_exactly_the_tables_in_table_order():
    assert set(validate.PRIMARY_KEYS) == set(TABLE_ORDER)


def test_foreign_key_referenced_table_always_loads_before_the_referencing_table():
    """TABLE_ORDER is what COPY/insert order relies on for FK safety. Every
    referenced table in FOREIGN_KEYS must therefore appear earlier in
    TABLE_ORDER than the table that references it.
    """
    position = {name: i for i, name in enumerate(TABLE_ORDER)}
    for table, fk_col, ref_table, ref_col in validate.FOREIGN_KEYS:
        assert position[ref_table] < position[table], (
            f"{ref_table} (referenced by {table}.{fk_col}) must precede {table} in TABLE_ORDER"
        )


def test_verbose_mode_reports_every_table_and_the_foreign_key_summary(official_tables, capsys):
    """generate_data.py calls validate() with verbose=True by default, so the
    reporting path ships even though the quiet path is what other tests use.
    A bad format spec in those lines would only ever surface at runtime.
    """
    validate.validate(official_tables, verbose=True)
    out = capsys.readouterr().out

    for table in validate.PRIMARY_KEYS:
        assert f"OK  {table}:" in out, f"{table} missing from the verbose report"
    assert f"all {len(validate.FOREIGN_KEYS)} foreign key relationships intact" in out
    # Row counts are thousands-separated and right-aligned; a wrong spec here
    # is exactly the kind of thing only executing the line catches.
    assert not any(line.strip().endswith("rows,") for line in out.splitlines())
