"""dim_date and dim_promo_calendar: the fiscal/NRF-454 calendar and the
independent promo cycle calendar built on top of it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from datagen import calendar_gen
from datagen.config import Config
from datagen.reference_data import MAJOR_SEASONS


@pytest.fixture(scope="module")
def config() -> Config:
    return Config(seed=1, first_fiscal_year=2024, num_fiscal_years=1)


@pytest.fixture(scope="module")
def dim_date(config: Config):
    return calendar_gen.build_dim_date(config)


@pytest.fixture(scope="module")
def dim_promo_calendar(config: Config, dim_date):
    return calendar_gen.build_dim_promo_calendar(config, dim_date)


def test_dim_date_covers_the_full_configured_span_with_no_gaps(config, dim_date):
    expected_days = (config.end_date - config.start_date).days + 1
    assert len(dim_date) == expected_days
    dates = dim_date["calendar_date"].tolist()
    assert dates[0] == config.start_date
    assert dates[-1] == config.end_date
    assert all(b - a == timedelta(days=1) for a, b in zip(dates, dates[1:]))


def test_date_key_is_a_unique_yyyymmdd_int(dim_date):
    assert dim_date["date_key"].is_unique
    for date_key, calendar_date in zip(dim_date["date_key"], dim_date["calendar_date"]):
        assert date_key == int(calendar_date.strftime("%Y%m%d"))


def test_day_of_week_name_matches_calendar_date(dim_date):
    sample = dim_date.sample(n=30, random_state=0)
    for _, row in sample.iterrows():
        assert row["day_of_week_name"] == row["calendar_date"].strftime("%A")


def test_first_and_last_day_belong_to_the_configured_fiscal_year(config, dim_date):
    assert dim_date.iloc[0]["fiscal_year"] == config.first_fiscal_year
    assert dim_date.iloc[-1]["fiscal_year"] == config.first_fiscal_year


def test_fiscal_week_month_quarter_stay_in_range(dim_date):
    assert dim_date["fiscal_week_num"].between(1, 52).all()
    assert dim_date["fiscal_month_num"].between(1, 12).all()
    assert dim_date["fiscal_quarter"].between(1, 4).all()
    # nrf_454_week_num is currently a straight alias of fiscal_week_num.
    assert (dim_date["nrf_454_week_num"] == dim_date["fiscal_week_num"]).all()


def test_known_fixed_holidays_are_flagged(dim_date):
    by_date = dim_date.set_index("calendar_date")["is_holiday"]
    assert bool(by_date.loc[date(2023, 7, 4)]) is True
    assert bool(by_date.loc[date(2023, 12, 25)]) is True
    assert bool(by_date.loc[date(2024, 1, 1)]) is True


def test_ordinary_weekday_is_not_a_holiday(dim_date):
    by_date = dim_date.set_index("calendar_date")["is_holiday"]
    # An arbitrary non-holiday Tuesday in the middle of the range.
    assert bool(by_date.loc[date(2023, 6, 6)]) is False


def test_promo_calendar_cycles_tile_the_full_span_with_no_gap_or_overlap(config, dim_promo_calendar):
    assert dim_promo_calendar.iloc[0]["cycle_start_date"] == config.start_date
    assert dim_promo_calendar.iloc[-1]["cycle_end_date"] == config.end_date
    starts = dim_promo_calendar["cycle_start_date"].tolist()
    ends = dim_promo_calendar["cycle_end_date"].tolist()
    for prev_end, next_start in zip(ends, starts[1:]):
        assert next_start == prev_end + timedelta(days=1)
    assert (dim_promo_calendar["cycle_end_date"] >= dim_promo_calendar["cycle_start_date"]).all()


def test_promo_calendar_key_is_sequential_from_one(dim_promo_calendar):
    assert dim_promo_calendar["promo_calendar_key"].tolist() == list(range(1, len(dim_promo_calendar) + 1))


def test_promo_cycle_id_format(dim_promo_calendar):
    pattern = r"^PROMO_\d{4}_WK\d{2}_\d{3}$"
    assert dim_promo_calendar["promo_cycle_id"].str.match(pattern).all()
    assert dim_promo_calendar["promo_cycle_id"].is_unique


def test_is_major_event_cycle_matches_season_type(dim_promo_calendar):
    expected = dim_promo_calendar["promo_season_type"].isin(MAJOR_SEASONS)
    assert (dim_promo_calendar["is_major_event_cycle"] == expected).all()
    # Sanity: MAJOR_SEASONS isn't vacuously matching everything or nothing.
    assert dim_promo_calendar["is_major_event_cycle"].any()
    assert not dim_promo_calendar["is_major_event_cycle"].all()


def test_dim_date_is_deterministic_given_seed_and_span():
    a = calendar_gen.build_dim_date(Config(seed=5, first_fiscal_year=2024, num_fiscal_years=1))
    b = calendar_gen.build_dim_date(Config(seed=5, first_fiscal_year=2024, num_fiscal_years=1))
    assert a.equals(b)


def test_promo_calendar_is_deterministic_given_seed():
    cfg = Config(seed=5, first_fiscal_year=2024, num_fiscal_years=1)
    dim_date = calendar_gen.build_dim_date(cfg)
    a = calendar_gen.build_dim_promo_calendar(cfg, dim_date)
    b = calendar_gen.build_dim_promo_calendar(cfg, dim_date)
    assert a.equals(b)
