"""writer.py: slicing generator output down to the official ddl.sql column
list, and persisting it to CSV and (optionally) SQLite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from datagen import writer
from datagen.schema_columns import TABLE_COLUMNS, TABLE_ORDER


def test_official_columns_strips_working_columns_and_matches_ddl_order(built):
    dim_product = built["dim_product"]
    assert "_price_anchor" in dim_product.columns  # sanity: the working column exists to strip
    sliced = writer.official_columns("dim_product", dim_product)
    assert list(sliced.columns) == TABLE_COLUMNS["dim_product"]
    assert "_price_anchor" not in sliced.columns
    assert "_unit_kind" not in sliced.columns
    assert "_vendor_key" not in sliced.columns


def test_write_csvs_produces_one_correctly_headed_file_per_table(official_tables, tmp_path: Path):
    writer.write_csvs(official_tables, tmp_path)
    for name in TABLE_ORDER:
        csv_path = tmp_path / f"{name}.csv"
        assert csv_path.exists()
        lines = csv_path.read_text().splitlines()
        header = lines[0].split(",")
        assert header == TABLE_COLUMNS[name]
        assert len(lines) - 1 == len(official_tables[name])


def test_write_sqlite_round_trips_every_table(official_tables, tmp_path: Path):
    db_path = tmp_path / "nl2sql.db"
    writer.write_sqlite(official_tables, db_path)
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        for name in TABLE_ORDER:
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({name})")]
            assert cols == TABLE_COLUMNS[name]
            (count,) = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
            assert count == len(official_tables[name])
    finally:
        conn.close()


def test_write_sqlite_stores_booleans_as_ints_and_dates_as_iso_text(official_tables, tmp_path: Path):
    db_path = tmp_path / "nl2sql.db"
    writer.write_sqlite(official_tables, db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT calendar_date, is_holiday FROM dim_date ORDER BY date_key LIMIT 1"
        ).fetchone()
        calendar_date, is_holiday = row
        assert calendar_date == str(official_tables["dim_date"].iloc[0]["calendar_date"])
        assert is_holiday in (0, 1)
    finally:
        conn.close()


def test_write_sqlite_can_be_called_twice_without_erroring(official_tables, tmp_path: Path):
    db_path = tmp_path / "nl2sql.db"
    writer.write_sqlite(official_tables, db_path)
    writer.write_sqlite(official_tables, db_path)  # must overwrite cleanly, not append/duplicate
    conn = sqlite3.connect(db_path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM dim_store").fetchone()
        assert count == len(official_tables["dim_store"])
    finally:
        conn.close()
