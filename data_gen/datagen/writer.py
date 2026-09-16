"""Persists generated tables to CSV (always) and optionally to a ready-to
-query SQLite database.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

import pandas as pd

from .schema_columns import TABLE_COLUMNS, TABLE_ORDER
from .sqlite_schema import CREATE_STATEMENTS, INDEX_STATEMENTS


def official_columns(table_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Slice a (possibly enriched) DataFrame down to its ddl.sql columns."""
    cols = TABLE_COLUMNS[table_name]
    return df[cols].copy()


def write_csvs(tables: dict[str, pd.DataFrame], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in TABLE_ORDER:
        df = official_columns(name, tables[name])
        df.to_csv(output_dir / f"{name}.csv", index=False)


def write_sqlite(tables: dict[str, pd.DataFrame], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")  # off during bulk load
        for name in TABLE_ORDER:
            conn.execute(CREATE_STATEMENTS[name])
        for stmt in INDEX_STATEMENTS:
            conn.execute(stmt)
        conn.commit()

        for name in TABLE_ORDER:
            df = official_columns(name, tables[name])
            for c in df.columns:
                if df[c].dtype == bool:
                    df[c] = df[c].astype(int)
                elif df[c].dtype == object and df[c].map(lambda v: isinstance(v, datetime.date)).any():
                    # Store dates as ISO text rather than relying on sqlite3's
                    # (deprecated) implicit datetime.date adapter.
                    df[c] = df[c].astype(str)
            df.to_sql(name, conn, if_exists="append", index=False)

        conn.commit()
    finally:
        conn.close()
