from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from vwaps.backtest import compare_truth, summarize_loo
from vwaps.comove import EWCov
from vwaps.io_eex import EexBook
from vwaps.mapping import ProductMap
from vwaps.synthetic import OBS_PROB, make_synthetic
from vwaps.tenors import resolve_tenor


def test_ewcov_matches_centered_exponentially_weighted_moments():
    x = np.array([12.0, 9.0, 13.0, 10.0, 15.0])
    y = np.array([24.0, 28.0, 22.0, 26.0, 19.0])
    cov = EWCov(3)
    for a, b in zip(x, y):
        cov.update(float(a), float(b))
    weights = cov.lam ** np.arange(len(x) - 1, -1, -1)
    dx, dy = x - np.average(x, weights=weights), y - np.average(y, weights=weights)
    expected_cov = np.sum(weights * dx * dy)
    vx, vy = np.sum(weights * dx**2), np.sum(weights * dy**2)
    assert cov.n == pytest.approx(weights.sum())
    assert cov.corr == pytest.approx(expected_cov / np.sqrt(vx * vy))
    assert cov.beta == pytest.approx(expected_cov / vx)


def test_ewcov_is_invariant_to_nonzero_mean():
    cov = EWCov(20)
    for x in (1.0, 4.0, -2.0, 3.0):
        cov.update(x + 100.0, 7.0 + 0.8 * x)
    assert cov.corr == pytest.approx(1.0)
    assert cov.beta == pytest.approx(0.8)


def test_ewcov_uses_elapsed_days_when_dates_are_supplied():
    cov = EWCov(10)
    start = date(2026, 9, 1)
    cov.update(1.0, 2.0, start)
    cov.update(3.0, 6.0, start + timedelta(days=10))
    assert cov.n == pytest.approx(1.5)
    assert cov.corr == pytest.approx(1.0)
    assert cov.beta == pytest.approx(2.0)
    assert cov.last_day == start + timedelta(days=10)
    with pytest.raises(ValueError, match="chronological"):
        cov.update(4.0, 8.0, start)
    assert cov.last_day == start + timedelta(days=10)


@pytest.mark.parametrize("points", [
    [(1.0, 1.0)] * 10,
    [(1.0, float(i)) for i in range(10)],
    [(float(i), 1.0) for i in range(10)],
    [(0.01, 0.02)],
])
def test_ewcov_does_not_claim_relationship_without_variance(points):
    cov = EWCov(20)
    for x, y in points:
        cov.update(x, y)
    assert cov.corr is None
    assert cov.beta is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_ewcov_rejects_invalid_observations_without_poisoning_state(bad):
    cov = EWCov(20)
    cov.update(1.0, 2.0)
    cov.update(2.0, 4.0)
    previous = cov.n, cov.corr, cov.beta
    with pytest.raises(ValueError, match="finite"):
        cov.update(bad, 3.0)
    assert (cov.n, cov.corr, cov.beta) == previous


def test_synthetic_deduplicates_labels_and_keeps_aliases_consistent(monkeypatch):
    day = date(2026, 9, 29)
    cfg = SimpleNamespace(tenors=["D+1", "BOM", "W+2", "W+2"],
                          day_convention="calendar", weekend_offset=0)
    product = ProductMap("TEST", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin")
    frame = pd.DataFrame([
        {"tradeDate": day, "maturityType": "Day", "deliveryStart": date(2026, 9, 30), "settlPx": 100.0},
        {"tradeDate": day, "maturityType": "Week", "deliveryStart": date(2026, 10, 12), "settlPx": 120.0},
    ])
    monkeypatch.setitem(OBS_PROB, "Day", 1.0)
    own, truth = make_synthetic(cfg, [product], {"TEST": EexBook(frame)}, day, day, p_empty_day=0)
    assert list(truth["tenor"]) == ["D+1", "BOM", "W+2"]
    assert not truth.duplicated(["date", "product", "tenor"]).any()
    prices = truth.set_index("tenor")["truth"]
    assert prices["D+1"] == prices["BOM"]
    periods = [resolve_tenor(label, day).key for label in own["tenor2"]]
    assert len(periods) == len(set(periods))
    assert "D+1" in set(own["tenor2"])
    for label, value in own[["tenor2", "vwap"]].itertuples(index=False):
        assert value == prices[label]


def _truth_inputs():
    day = date(2026, 9, 29)
    truth = pd.DataFrame([{"date": day, "product": "TEST", "tenor": "W+2", "truth": 110.0}])
    filled = pd.DataFrame([{"reference_date": day, "product": "TEST", "tenor": "W+2",
                            "source": "eex+local", "price": 108.0}])
    return filled, truth


def test_compare_truth_rejects_old_duplicate_truth_with_actionable_error():
    filled, truth = _truth_inputs()
    old = pd.concat([truth, truth.assign(truth=120.0)], ignore_index=True)
    with pytest.raises(ValueError, match="W\\+2.*new directory"):
        compare_truth(filled, old)


def test_compare_truth_rejects_conflicting_aliases():
    filled, truth = _truth_inputs()
    truth = truth.assign(delivery_start=date(2026, 10, 12), delivery_end=date(2026, 10, 19))
    aliases = pd.concat([truth, truth.assign(tenor="alias", truth=120.0)], ignore_index=True)
    with pytest.raises(ValueError, match="alias"):
        compare_truth(filled, aliases)


def test_compare_truth_requires_unique_output_and_reports_valid_error():
    filled, truth = _truth_inputs()
    with pytest.raises(ValueError, match="Filled curve"):
        compare_truth(pd.concat([filled, filled]), truth)
    report = compare_truth(filled, truth)
    assert report.iloc[0]["n"] == 1
    assert report.iloc[0]["mae"] == 2.0
    assert report.iloc[0]["bias"] == -2.0


def test_backtest_compares_eex_on_same_cases_and_reports_missing_pairs():
    day = date(2026, 9, 29)
    rows = [("M+1", "eex", 100.0), ("M+2", "eex", 10.0),
            ("M+2", "hist_ratio", 6.0), ("M+3", "only_without_eex", 1.0)]
    loo = pd.DataFrame([{"reference_date": day, "product": "TEST", "tenor": tenor,
                         "group": "month", "method": method, "error": error}
                        for tenor, method, error in rows])
    report = summarize_loo(loo).set_index(["method", "group"])
    hist = report.loc[("hist_ratio", "ALL")]
    assert hist["n"] == hist["n_paired"] == 1
    assert hist["mae"] == hist["mae_paired"] == 6.0
    assert hist["mae_eex_paired"] == 10.0
    assert hist["mae_improvement"] == 4.0
    assert report.loc[("eex", "ALL"), "mae"] == 55.0
    unmatched = report.loc[("only_without_eex", "month")]
    assert unmatched["n_paired"] == 0
    assert pd.isna(unmatched["mae_improvement"])


def test_backtest_pairs_within_scenario():
    day = date(2026, 9, 29)
    loo = pd.DataFrame([
        {"reference_date": day, "product": "TEST", "tenor": "M+1", "group": "month",
         "scenario": scenario, "method": method, "error": error}
        for scenario, method, error in [("single", "eex", 2.0), ("block", "eex", 10.0),
                                        ("block", "blend_ratio", 6.0)]
    ])
    report = summarize_loo(loo).set_index(["method", "group"])
    assert report.loc[("blend_ratio", "ALL"), "mae_eex_paired"] == 10.0
    assert report.loc[("blend_ratio", "ALL"), "mae_improvement"] == 4.0
