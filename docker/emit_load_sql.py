#!/usr/bin/env python3
"""Emit the COPY script that bulk-loads the generated CSVs into Postgres.

Load order comes from datagen.schema_columns.TABLE_ORDER so it stays
foreign-key safe if the schema ever gains or reorders tables.

Usage: emit_load_sql.py <csv_dir_as_seen_by_postgres> <output_sql_path>
"""

from __future__ import annotations

import sys
from pathlib import Path

from datagen.schema_columns import TABLE_ORDER


def main() -> None:
    csv_dir, out_path = sys.argv[1], Path(sys.argv[2])
    statements = [
        f"COPY {table} FROM '{csv_dir}/{table}.csv' WITH (FORMAT csv, HEADER true);"
        for table in TABLE_ORDER
    ]
    out_path.write_text("\n".join(statements) + "\n")


if __name__ == "__main__":
    main()
