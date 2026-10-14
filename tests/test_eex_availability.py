"""Availability scenarios share one cutoff across snapshots and fallback windows."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from vwaps.config import load_config, validate_config
from vwaps.hours import hours_fn
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.tenors import Period


ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 10, 7)
NOV = date(2026, 11, 1)
DEC = date(2026, 12, 1)
JAN = date(2027, 1, 1)


def book(rows):
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


@pytest.mark.parametrize("offset,publication,expected", [
    (0, date(2026, 10, 7), 130),
    (-1, date(2026, 10, 6), 120),
    (-2, date(2026, 10, 5), 110),
    (-3, date(2026, 10, 2), 100),
])
def test_select_latest_publication_at_or_before_calendar_cutoff(offset, publication, expected):
    market = book([(date(2026, 10, d), "Month", NOV, p)
                   for d, p in ((2, 100), (5, 110), (6, 120), (7, 130))])
    quotes, asof = market.available_quotes(DAY, None, offset)
    assert asof == publication
    assert quotes[(NOV, DEC)] == expected


def test_default_zero_uses_latest_if_same_day_is_absent_and_does_not_mutate_book():
    market = book([(date(2026, 10, 5), "Month", NOV, 110)])
    assert market.available_quotes(DAY, None) == market.quotes(DAY, None)
    assert market.available_quotes(DAY, None, -2) == market.quotes(DAY, None)
    assert market.available_quotes(DAY, None, -3) == ({}, None)
    assert market.quotes(date(2026, 10, 5), 0)[1] == date(2026, 10, 5)


def test_weekend_age_is_measured_from_reference_date_not_cutoff():
    friday, monday = date(2026, 10, 9), date(2026, 10, 12)
    market = book([(friday, "Month", NOV, 110)])
    assert market.cutoff_date(monday, -1) == date(2026, 10, 11)
    assert market.available_asof(monday, 2, -1) is None
    assert market.available_quotes(monday, 2, -1) == ({}, None)
    assert market.available_asof(monday, 3, -1) == friday


def test_snapshot_and_delivered_fixings_respect_cutoff_without_contract_carryforward():
    delivered = date(2026, 10, 4)
    market = book([
        (date(2026, 10, 5), "Month", NOV, 110),
        (date(2026, 10, 5), "Day", delivered, 40),
        (date(2026, 10, 6), "Month", DEC, 130),
        (DAY, "Day", delivered, 999),
        (DAY, "Month", NOV, 999),
    ])
    quotes, asof = market.available_quotes(DAY, None, -1)
    assert asof == date(2026, 10, 6)
    assert (NOV, DEC) not in quotes
    assert quotes[(DEC, JAN)] == 130
    assert quotes[(delivered, date(2026, 10, 5))] == 40


@pytest.mark.parametrize("value", [1, True, -1.5, "-1", None])
def test_offset_rejects_positive_and_noninteger_configuration(value):
    with pytest.raises(ValueError, match="eex.offset_days"):
        validate_config(replace(load_config(ROOT / "config.toml"), eex_offset_days=value))
    with pytest.raises(ValueError, match="eex.offset_days"):
        EexBook.cutoff_date(DAY, value)


def test_toml_offset_is_optional_and_can_be_negative(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    assert load_config(path).eex_offset_days == 0
    path.write_text("[eex]\noffset_days = -2\n", encoding="utf-8")
    assert load_config(path).eex_offset_days == -2


def test_cutoff_underflow_has_configuration_error_context():
    with pytest.raises(ValueError, match="eex.offset_days.*supported date range"):
        EexBook.cutoff_date(date.min, -1)
