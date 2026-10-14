from datetime import date, timedelta

import pandas as pd
import pytest

from vwaps.hours import hours_fn, period_hours
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.pricer import Pricer


TZ = "Europe/Berlin"


def test_peak_day_and_weekend_hours_depend_on_contract_kind():
    sat, sun, mon = date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28)
    hours = hours_fn("Peak", TZ)
    assert hours(sat, sun, "Day") == 12
    assert hours(sat, mon, "Weekend") == 24
    assert hours(sat, mon, "BOW") == 0
    assert hours(sat, mon) == 0  # Backward-compatible calendar when kind is omitted.
    assert period_hours(sat, mon, "Peak", TZ, "Weekend") == 24


def test_peak_weekend_prices_from_days_but_week_ignores_weekend_prices():
    mon = date(2026, 9, 21)
    sat, sun, end = mon + timedelta(days=5), mon + timedelta(days=6), mon + timedelta(days=7)
    prices = {(mon + timedelta(days=i), mon + timedelta(days=i + 1)): 100.0 for i in range(5)}
    prices.update({(sat, sun): 20.0, (sun, end): 40.0})
    pricer = Pricer(prices, hours_fn("Peak", TZ))
    assert pricer.price(sat, end, "Weekend") == (30.0, "strip")
    assert pricer.price(mon, end, "Week") == (100.0, "strip")


def test_peak_zero_hour_target_does_not_reuse_weekend_quote():
    sat, mon = date(2026, 9, 26), date(2026, 9, 28)
    pricer = Pricer({(sat, mon): 90.76}, hours_fn("Peak", TZ))
    assert pricer.price(sat, mon, "Weekend") == (90.76, "exact")
    assert pricer.price(sat, mon, "BOW") is None
    assert pricer.price(sat, mon) is None


def test_peak_residual_allows_weekend_parent_but_not_month_parent():
    sat, sun, mon = date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28)
    pricer = Pricer({(sat, mon): 30.0, (sat, sun): 20.0}, hours_fn("Peak", TZ))
    assert pricer.price(sun, mon, "Day") == (40.0, "residual")
    start, tail, end = date(2026, 9, 1), date(2026, 9, 30), date(2026, 10, 1)
    prices = {(start, end): 100.0}
    for i in range(29):
        day = start + timedelta(days=i)
        prices[(day, day + timedelta(days=1))] = 90.0
    pricer = Pricer(prices, hours_fn("Peak", TZ))
    assert pricer.price(tail, end, "Day") is None
    price, method = pricer.price(tail, end, "BOM")
    assert method == "residual"
    assert price == pytest.approx(310.0)  # 22 weekdays: 21 at 90 and one at 310.


def test_pricer_keeps_legacy_two_argument_hours_callable():
    a, b, c = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)
    pricer = Pricer({(a, b): 20.0, (b, c): 40.0}, lambda s, e: 24.0 * (e - s).days)
    assert pricer.price(a, c) == (30.0, "strip")
    assert pricer.price(a, c, "BOM") == (30.0, "strip")


def test_realized_day_uses_latest_publication_asof_without_future_leakage():
    delivery = date(2026, 9, 6)
    end = delivery + timedelta(days=1)
    sep = date(2026, 9, 1)
    rows = [
        (date(2026, 9, 4), "Day", delivery, 105.65),
        (date(2026, 9, 7), "Day", delivery, 140.01),
        (date(2026, 9, 8), "Month", sep, 150.0),
        (date(2026, 9, 10), "Day", delivery, 160.0),
        (date(2026, 9, 11), "Month", sep, 150.0),
    ]
    # Reverse rows to avoid relying on their physical order in the CSV.
    book = EexBook(pd.DataFrame(rows[::-1], columns=REQUIRED))
    assert book.quotes(date(2026, 9, 11), None)[0][delivery, end] == 160.0
    assert book.quotes(date(2026, 9, 8), None)[0][delivery, end] == 140.01
    assert book.quotes(date(2026, 9, 7), None)[0][delivery, end] == 140.01
    assert book.quotes(date(2026, 9, 4), None)[0][delivery, end] == 105.65
    quotes, asof = book.quotes(date(2026, 9, 9), None)
    assert asof == date(2026, 9, 8)
    assert quotes[delivery, end] == 140.01
    assert book.quotes(date(2026, 9, 9), 0) == ({}, None)


def test_bom_residual_retains_late_day_fixing_after_publication_day():
    sep, octo = date(2026, 9, 1), date(2026, 10, 1)
    reference = date(2026, 9, 8)
    rows = [(reference, "Month", sep, 150.0)]
    for i in range(8):
        day = sep + timedelta(days=i)
        rows.append((day, "Day", day, 100.0))
    rows.append((date(2026, 9, 7), "Day", date(2026, 9, 6), 140.0))
    rows.append((date(2026, 9, 10), "Day", date(2026, 9, 6), 999.0))
    book = EexBook(pd.DataFrame(rows, columns=REQUIRED))
    quotes, _ = book.quotes(reference, None)
    price, method = Pricer(quotes, hours_fn("Base", TZ)).price(date(2026, 9, 9), octo, "BOM")
    assert method == "residual"
    assert price == pytest.approx((150.0 * 30 - (7 * 100 + 140)) / 22)
