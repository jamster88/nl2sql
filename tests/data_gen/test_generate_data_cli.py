"""End-to-end smoke tests for generate_data.py as a script: the way it's
actually invoked, by docker/Dockerfile and by hand per data_gen/README.md.
"""

from __future__ import annotations

import filecmp
import sqlite3
import subprocess
import sys
from pathlib import Path

from datagen.schema_columns import TABLE_ORDER

DATA_GEN_DIR = Path(__file__).resolve().parent.parent.parent / "data_gen"

# Kept tiny so the whole suite still runs in well under a second per case.
SMALL_ARGS = [
    "--seed", "3", "--stores", "3", "--products", "20", "--competitors", "2",
    "--vendors", "4", "--market-regions", "2", "--promotions", "3",
    "--ad-channels", "2", "--ad-placements", "4", "--fiscal-years", "1", "--quiet",
]


def run_generator(output_dir: Path, *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "generate_data.py", *SMALL_ARGS, "--output-dir", str(output_dir), *extra_args],
        cwd=DATA_GEN_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_runs_clean_and_writes_every_csv(tmp_path: Path):
    out = tmp_path / "out"
    result = run_generator(out, "--no-sqlite")
    assert result.returncode == 0, result.stderr
    for name in TABLE_ORDER:
        csv_path = out / f"{name}.csv"
        assert csv_path.exists()
        assert csv_path.stat().st_size > 0


def test_cli_writes_sqlite_by_default(tmp_path: Path):
    out = tmp_path / "out"
    result = run_generator(out)
    assert result.returncode == 0, result.stderr
    db_path = out / "nl2sql_retail.db"
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        for name in TABLE_ORDER:
            (count,) = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
            assert count > 0
    finally:
        conn.close()


def test_cli_no_sqlite_flag_skips_the_database_file(tmp_path: Path):
    out = tmp_path / "out"
    result = run_generator(out, "--no-sqlite")
    assert result.returncode == 0, result.stderr
    assert not (out / "nl2sql_retail.db").exists()


def test_cli_same_seed_produces_byte_identical_output(tmp_path: Path):
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    result_a = run_generator(out_a, "--no-sqlite")
    result_b = run_generator(out_b, "--no-sqlite")
    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr
    for name in TABLE_ORDER:
        assert filecmp.cmp(out_a / f"{name}.csv", out_b / f"{name}.csv", shallow=False)


def test_cli_different_seed_changes_output(tmp_path: Path):
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    result_a = run_generator(out_a, "--no-sqlite")

    args_b = list(SMALL_ARGS)
    args_b[args_b.index("--seed") + 1] = "99"
    result_b = subprocess.run(
        [sys.executable, "generate_data.py", *args_b, "--output-dir", str(out_b), "--no-sqlite"],
        cwd=DATA_GEN_DIR, capture_output=True, text=True, timeout=60,
    )
    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr
    assert not filecmp.cmp(out_a / "dim_store.csv", out_b / "dim_store.csv", shallow=False)


def test_cli_scale_flag_multiplies_cardinality_counts(tmp_path: Path):
    out = tmp_path / "out"
    result = run_generator(out, "--no-sqlite", "--scale", "2")
    assert result.returncode == 0, result.stderr
    # --stores 3 --scale 2 -> round(3*2) = 6 (see Config.scaled/s_int).
    lines = (out / "dim_store.csv").read_text().splitlines()
    assert len(lines) - 1 == 6


def test_cli_reports_row_totals_when_not_quiet(tmp_path: Path):
    out = tmp_path / "out"
    args = [a for a in SMALL_ARGS if a != "--quiet"]
    result = subprocess.run(
        [sys.executable, "generate_data.py", *args, "--output-dir", str(out), "--no-sqlite"],
        cwd=DATA_GEN_DIR, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Done in" in result.stdout
    assert "total rows across" in result.stdout


def test_cli_honors_a_custom_sqlite_filename(tmp_path: Path):
    out = tmp_path / "out"
    result = run_generator(out, "--sqlite-filename", "retail_snapshot.db")
    assert result.returncode == 0, result.stderr
    assert (out / "retail_snapshot.db").exists()
    assert not (out / "nl2sql_retail.db").exists()


def test_cli_labels_the_fiscal_year_it_was_asked_for(tmp_path: Path):
    """Fiscal year Y starts April 1 of Y-1, so the label is not derivable from
    the dates -- it comes straight from the flag, and getting it wrong shifts
    every fiscal-year question the agent is asked.
    """
    import csv

    out = tmp_path / "out"
    result = run_generator(out, "--no-sqlite", "--first-fiscal-year", "2019")
    assert result.returncode == 0, result.stderr

    with (out / "dim_date.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert {row["fiscal_year"] for row in rows} == {"2019"}

    dates = sorted(row["calendar_date"] for row in rows)
    assert dates[0].startswith("2018-04"), dates[0]
