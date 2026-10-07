"""nl2sql_common.values, urls and vectors: small things said once."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from nl2sql_common.urls import redacted
from nl2sql_common.values import json_safe
from nl2sql_common.vectors import vector_literal


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None), (True, True), (3, 3), ("x", "x"), (1.5, 1.5),
        (math.inf, "inf"), (Decimal("719279.97"), "719279.97"),
        (date(2026, 10, 4), "2026-10-04"), (datetime(2026, 10, 4, 1, 2), "2026-10-04T01:02:00"),
        (time(3, 4), "03:04:00"), (b"\x01\xff", "\\x01ff"), (memoryview(b"\x02"), "\\x02"),
        ((1, Decimal("2")), [1, "2"]), ({1: Decimal("3")}, {"1": "3"}),
        (timedelta(hours=1), "1:00:00"), (object.__new__(type("Odd", (), {"__str__": lambda s: "odd"})), "odd"),
    ],
)
def test_a_cell_keeps_what_it_said(value, expected):
    assert json_safe(value) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql://u:secret@h:5/db", "postgresql://u:***@h:5/db"),
        ("postgresql://:secret@h/db", "postgresql://h/db"),
        ("postgresql://h/db", "postgresql://h/db"),
        ("u:p@h", "u:***@h"),
    ],
)
def test_a_password_never_survives_into_a_printed_url(url, expected):
    assert redacted(url) == expected


def test_a_missing_url_reads_as_the_caller_says():
    assert redacted(None) == "" and redacted("", missing="(not set)") == "(not set)"


def test_a_vector_is_pgvectors_text():
    assert vector_literal([0.5, 1, -2]) == "[0.5,1.0,-2.0]"
