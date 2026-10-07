"""One way to the engine, and nothing left behind on a pooled connection (V6-22)."""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent.parent / "agent" / "nl2sql_agent"
SOURCES = sorted(PACKAGE.rglob("*.py"))


def test_no_setting_outlives_the_transaction_it_was_made_for():
    """A plain `SET` stays on the pooled connection, for the next borrower:
    a retriever's two-second timeout became the planner's, and a role set
    for one person would be the next caller's."""
    found = [
        f"{path.relative_to(PACKAGE)}:{number}"
        for path in SOURCES
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"['\"]SET (?!LOCAL|TRANSACTION)", line)
    ]
    assert found == [], f"session-level SET: {found}"


def test_nothing_reaches_past_database_for_its_private_engine():
    found = [
        str(path.relative_to(PACKAGE))
        for path in SOURCES
        if path.name != "database.py" and re.search(r"\b(database|db|self\._database)\._engine\b", path.read_text())
    ]
    assert found == [], f"use Database.engine: {found}"
