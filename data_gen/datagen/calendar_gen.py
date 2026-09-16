"""Builds dim_date (corporate fiscal calendar) and dim_promo_calendar
(independent marketing calendar), per the dual-calendar design in
data_model_detail.md.

Fiscal year Y is defined (per data_schema.md) to start April 1 of year
Y-1, e.g. FY2030 starts 2029-04-01. Within a fiscal year we lay out a
standard NRF-style 4-4-5 week pattern per quarter (4 weeks, 4 weeks, 5
weeks = 13 weeks/quarter = 52 weeks/year), with any trailing leap days
folded into the final week of the year.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .config import Config
from .reference_data import MAJOR_SEASONS, SEASON_BY_MONTH

# weeks per fiscal month, repeated for 4 quarters -> 12 months, 52 weeks
_MONTH_WEEK_PATTERN = [4, 4, 5] * 4


def _fiscal_year_for(d: date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def _fiscal_year_start(fy: int) -> date:
    return date(fy - 1, 4, 1)


def _build_week_to_month_map() -> dict[int, tuple[int, int]]:
    """Map fiscal week number (1-based) -> (fiscal_month_num, fiscal_quarter)."""
    mapping: dict[int, tuple[int, int]] = {}
    week = 1
    for month_idx, weeks_in_month in enumerate(_MONTH_WEEK_PATTERN, start=1):
        quarter = (month_idx - 1) // 3 + 1
        for _ in range(weeks_in_month):
            mapping[week] = (month_idx, quarter)
            week += 1
    return mapping


_WEEK_TO_MONTH = _build_week_to_month_map()
_LAST_MONTH, _LAST_QUARTER = _WEEK_TO_MONTH[52]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-indexed) occurrence of `weekday` (Mon=0) in year/month."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    d = next_month_first - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _holidays_for_year(year: int) -> set[date]:
    holidays = {
        date(year, 1, 1),  # New Year's Day
        _nth_weekday(year, 1, 0, 3),  # MLK Day
        _nth_weekday(year, 2, 0, 3),  # Presidents Day
        _easter_sunday(year),
        _last_weekday(year, 5, 0),  # Memorial Day
        date(year, 6, 19),  # Juneteenth
        date(year, 7, 4),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus Day
        date(year, 10, 31),  # Halloween
        date(year, 11, 11),  # Veterans Day
    }
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    holidays.add(thanksgiving)
    holidays.add(thanksgiving + timedelta(days=1))  # Black Friday
    holidays.update({date(year, 12, 24), date(year, 12, 25), date(year, 12, 31)})
    return holidays


def build_dim_date(config: Config) -> pd.DataFrame:
    start, end = config.start_date, config.end_date
    n_days = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(n_days)]

    holiday_cache: dict[int, set[date]] = {}

    rows = []
    for d in dates:
        fy = _fiscal_year_for(d)
        fy_start = _fiscal_year_start(fy)
        day_offset = (d - fy_start).days
        fiscal_week_num = min(day_offset // 7 + 1, 52)
        fiscal_month_num, fiscal_quarter = _WEEK_TO_MONTH.get(
            fiscal_week_num, (_LAST_MONTH, _LAST_QUARTER)
        )

        if d.year not in holiday_cache:
            holiday_cache[d.year] = _holidays_for_year(d.year)
        is_holiday = d in holiday_cache[d.year]

        rows.append(
            {
                "date_key": int(d.strftime("%Y%m%d")),
                "calendar_date": d,
                "day_of_week_name": d.strftime("%A"),
                "fiscal_week_num": fiscal_week_num,
                "fiscal_month_num": fiscal_month_num,
                "fiscal_quarter": fiscal_quarter,
                "fiscal_year": fy,
                "nrf_454_week_num": fiscal_week_num,
                "is_holiday": is_holiday,
            }
        )

    return pd.DataFrame(rows)


def build_dim_promo_calendar(config: Config, dim_date: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(config.seed + 1)
    lo, hi = config.promo_cycle_length_days_range

    start, end = config.start_date, config.end_date
    cursor = start
    cycles = []
    seq_by_fy: dict[int, int] = {}
    phase_by_season_fy: dict[tuple[int, str], int] = {}

    while cursor <= end:
        length = int(rng.integers(lo, hi + 1))
        cycle_end = min(cursor + timedelta(days=length - 1), end)

        fy = _fiscal_year_for(cursor)
        seq_by_fy[fy] = seq_by_fy.get(fy, 0) + 1
        week_num = min((cursor - _fiscal_year_start(fy)).days // 7 + 1, 52)

        season_type = SEASON_BY_MONTH[cursor.month]
        phase_key = (fy, season_type)
        phase_by_season_fy[phase_key] = phase_by_season_fy.get(phase_key, 0) + 1
        phase = phase_by_season_fy[phase_key]

        cycle_id = f"PROMO_{fy}_WK{week_num:02d}_{seq_by_fy[fy]:03d}"
        cycle_name = f"{season_type} - Phase {phase}"

        cycles.append(
            {
                "promo_cycle_id": cycle_id,
                "promo_cycle_name": cycle_name,
                "promo_season_type": season_type,
                "cycle_start_date": cursor,
                "cycle_end_date": cycle_end,
                "is_major_event_cycle": season_type in MAJOR_SEASONS,
            }
        )
        cursor = cycle_end + timedelta(days=1)

    df = pd.DataFrame(cycles)
    df.insert(0, "promo_calendar_key", np.arange(1, len(df) + 1))
    return df
