import json
import math
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from vwaps.eex_fallback import EexFallback
from vwaps.hours import hours_fn
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.tenors import Period, add_months


DAY = date(2026, 9, 30)
DAYS = tuple(pd.bdate_range(end=DAY, periods=9).date)
OCT = date(2026, 10, 1)
NOV = date(2026, 11, 1)
DEC = date(2026, 12, 1)


def config(**changes):
    values = dict(fallback_price_method="ewma", fallback_price_window=5,
                  fallback_ewma_halflife=2.0, fallback_spread_window=9,
                  fallback_anchor_months=2, max_stale_days=None)
    return SimpleNamespace(**(values | changes))


def month(start):
    return Period("Month", start, add_months(start, 1))


def book(rows):
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def fallback(rows, **changes):
    return EexFallback(book(rows), hours_fn("Base", "Europe/Berlin"), config(**changes))


def monthly_rows(days=DAYS):
    return [(day, "Month", start, base + factor * i)
            for i, day in enumerate(days)
            for start, base, factor in ((OCT, 100, 1), (NOV, 200, 2), (DEC, 300, 3))]


def test_finite_ewma_uses_last_five_publications_with_normalized_observation_weights():
    rows = [(day, "Month", OCT, value) for day, value in zip(DAYS, [999, 999, 999, 999, 10, 20, 30, 40, 50])]
    result = fallback(rows).price(DAY, month(OCT))
    weights = [2 ** (-age / 2) for age in (4, 3, 2, 1, 0)]
    expected = sum(value * weight for value, weight in zip([10, 20, 30, 40, 50], weights)) / sum(weights)
    assert result.price == pytest.approx(expected)
    assert result.method == "eex_price_ewma"
    observations = result.trace["price_average"]["observations"]
    assert [row["trade_date"] for row in observations] == [day.isoformat() for day in DAYS[-5:]]
    assert sum(row["weight"] for row in observations) == pytest.approx(1)
    assert observations[-1]["weight"] / observations[-3]["weight"] == pytest.approx(2)
    assert json.loads(json.dumps(result.trace, allow_nan=False))["price_method"] == "ewma"


@pytest.mark.parametrize("values,expected", [([10, 20, 30, 40, 50], 30),
                                           ([-2, -1, 0, 1, 2], 0), ([-5] * 5, -5)])
def test_simple_price_mean_preserves_zero_and_negative_values(values, expected):
    rows = [(day, "Month", OCT, value) for day, value in zip(DAYS[-5:], values)]
    result = fallback(rows, fallback_price_method="simple").price(DAY, month(OCT))
    assert result.price == pytest.approx(expected)
    assert result.method == "eex_price_simple"
    assert {row["weight"] for row in result.trace["price_average"]["observations"]} == {0.2}


def test_monthly_cascade_uses_raw_nine_day_spreads_and_only_last_anchor_mean():
    result = fallback(monthly_rows(), fallback_price_method="simple").price(DAY, month(DEC))
    assert result.method == "eex_month_cascade_simple"
    assert result.price == pytest.approx(314)  # 106 + mean(100+i) + mean(100+i), i=0..8.
    assert result.trace["price_average"]["period"]["delivery_start"] == OCT.isoformat()
    steps = result.trace["spread_steps"]
    assert [step["mean_spread"] for step in steps] == pytest.approx([104, 104])
    assert [step["price_after"] for step in steps] == pytest.approx([210, 314])
    assert steps[0]["observations"][-1]["from_price"] == 108
    assert steps[0]["observations"][-1]["to_price"] == 216
    assert steps[0]["observations"][-1]["spread"] == 108
    assert result.trace["spread_input"] == "raw_same_publication_prices"
    # No September contract was supplied: its independent anchor is not needed.
    assert json.loads(json.dumps(result.trace, allow_nan=False))["spread_steps"][-1]["price_after"] == pytest.approx(314)


def test_cascade_ewma_applies_exponential_weights_only_to_anchor_prices():
    result = fallback(monthly_rows()).price(DAY, month(DEC))
    weights = [2 ** (-age / 2) for age in (4, 3, 2, 1, 0)]
    anchor = sum(value * weight for value, weight in zip(range(104, 109), weights)) / sum(weights)
    assert result.price == pytest.approx(anchor + 104 + 104)
    assert result.method == "eex_month_cascade_ewma"
    assert {row["weight"] for row in result.trace["spread_steps"][0]["observations"]} == {1 / 9}


def test_future_publications_cannot_change_past_prices_or_windows():
    rows = monthly_rows()
    expected = fallback(rows).price(DAY, month(DEC))
    future = [(DAY + timedelta(days=1), "Month", start, 1e9) for start in (OCT, NOV, DEC)]
    actual = fallback(rows + future).price(DAY, month(DEC))
    assert actual == expected
    assert fallback(rows).price(DAYS[0] - timedelta(days=1), month(OCT)) is None


def test_windows_require_enough_distinct_publications_not_elapsed_days():
    sparse = [(DAY - timedelta(days=30 * i), "Month", OCT, 100 + i) for i in range(4)]
    assert fallback(sparse).price(DAY, month(OCT)) is None
    rows = monthly_rows(DAYS[-8:])
    smoother = fallback(rows)
    assert smoother.price(DAY, month(OCT)) is not None
    assert smoother.price(DAY, month(NOV)) is None


def test_missing_contract_on_one_global_window_date_is_not_skipped_or_forward_filled():
    rows = monthly_rows()
    rows = [row for row in rows if not (row[0] == DAYS[-3] and row[2] == OCT)]
    assert fallback(rows).price(DAY, month(OCT)) is None
    rows = monthly_rows()
    rows = [row for row in rows if not (row[0] == DAYS[0] and row[2] == NOV)]
    smoother = fallback(rows)
    assert smoother.price(DAY, month(OCT)) is not None
    assert smoother.price(DAY, month(DEC)) is None


def test_stale_limit_applies_without_recounting_old_settlements():
    rows = monthly_rows()
    allowed = fallback(rows, max_stale_days=2)
    tomorrow = allowed.price(DAY + timedelta(days=1), month(OCT))
    assert tomorrow is not None
    assert tomorrow.trace["eex_asof"] == DAY.isoformat()
    assert len(tomorrow.trace["price_average"]["observations"]) == 5
    assert allowed.price(DAY + timedelta(days=3), month(OCT)) is None
    assert fallback(rows, max_stale_days=0).price(DAY + timedelta(days=1), month(OCT)) is None


def test_calendar_month_roll_keeps_absolute_contract_history_and_moves_anchor_boundary():
    smoother = fallback(monthly_rows(), fallback_price_method="simple")
    before = smoother.price(DAY, month(DEC))
    after = smoother.price(DAY + timedelta(days=1), month(DEC))
    assert before.price == pytest.approx(314)
    assert after.price == pytest.approx(316)  # November's own mean 212 + December spread 104.
    assert before.trace["price_average"]["period"]["delivery_start"] == OCT.isoformat()
    assert after.trace["price_average"]["period"]["delivery_start"] == NOV.isoformat()
    assert before.trace["target"] == after.trace["target"]


def test_anchor_count_and_windows_are_configurable():
    smoother = fallback(monthly_rows(), fallback_anchor_months=3,
                        fallback_price_method="simple", fallback_price_window=2,
                        fallback_spread_window=3)
    direct = smoother.price(DAY, month(NOV))
    result = smoother.price(DAY, month(DEC))
    assert direct.price == pytest.approx(215)
    assert direct.method == "eex_price_simple"
    assert result.price == pytest.approx(322)  # 215 + mean(106,107,108).


def test_non_month_contracts_average_priced_snapshots_with_delivery_hour_weights():
    target = Period("Quarter", OCT, date(2027, 1, 1))
    rows = [(day, "Month", start, price) for day in DAYS[-5:]
            for start, price in ((OCT, 110), (NOV, 120), (DEC, 130))]
    result = fallback(rows, fallback_price_method="simple").price(DAY, target)
    assert result.price == pytest.approx((110 * 745 + 120 * 720 + 130 * 744) / (745 + 720 + 744))
    assert result.method == "eex_price_simple"
    assert {row["eex_method"] for row in result.trace["price_average"]["observations"]} == {"strip"}
    assert result.trace["spread_steps"] == []


def test_direct_exact_and_residual_non_month_prices_use_same_causal_snapshot():
    start, head_end, end = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 12)
    rows = [(day, kind, delivery, price) for day in DAYS[-5:]
            for kind, delivery, price in (("Week", start, 100), ("Day", start, 40))]
    smoother = fallback(rows, fallback_price_method="simple")
    exact = smoother.price(DAY, Period("Week", start, end))
    residual = smoother.price(DAY, Period("BOW", head_end, end))
    assert exact.price == 100
    assert residual.price == pytest.approx(110)
    assert {row["eex_method"] for row in residual.trace["price_average"]["observations"]} == {"residual"}


def test_zero_delivery_hours_do_not_get_a_fallback_price():
    saturday = date(2026, 10, 3)
    rows = [(day, "Day", saturday, 100) for day in DAYS[-5:]]
    smoother = EexFallback(book(rows), hours_fn("Peak", "Europe/Berlin"), config())
    assert smoother.price(DAY, Period("BOW", saturday, saturday + timedelta(days=1))) is None
    assert smoother.price(DAY, Period("Day", saturday, saturday + timedelta(days=1))).price == pytest.approx(100)


def test_extreme_finite_prices_average_without_overflow_but_nonfinite_spread_is_rejected():
    rows = [(day, "Month", OCT, 1e308) for day in DAYS]
    result = fallback(rows, fallback_price_method="simple").price(DAY, month(OCT))
    assert math.isfinite(result.price) and result.price == pytest.approx(1e308)
    rows += [(day, "Month", NOV, -1e308) for day in DAYS]
    with pytest.raises(ValueError, match="nonfinite"):
        fallback(rows).price(DAY, month(NOV))


def test_repeated_queries_reuse_results_and_never_mutate_book_quotes(monkeypatch):
    source = book(monthly_rows())
    snapshot, asof = source.quotes(DAY, 0)
    original = snapshot.copy()
    calls = []
    original_quotes = source.quotes

    def counted(day, stale):
        calls.append(day)
        return original_quotes(day, stale)

    monkeypatch.setattr(source, "quotes", counted)
    smoother = EexFallback(source, hours_fn("Base", "Europe/Berlin"), config())
    result = smoother.price(DAY, month(DEC))
    count = len(calls)
    assert smoother.price(DAY, month(DEC)) is result
    assert len(calls) == count == 9
    assert source.quotes(DAY, 0) == (original, asof)


@pytest.mark.parametrize("changes", [dict(fallback_price_window=1), dict(fallback_spread_window=1),
                                    dict(fallback_anchor_months=0), dict(fallback_price_method="median"),
                                    dict(fallback_ewma_halflife=0), dict(fallback_ewma_halflife=float("nan"))])
def test_invalid_fallback_parameters_are_rejected(changes):
    with pytest.raises(ValueError):
        fallback(monthly_rows(), **changes)
