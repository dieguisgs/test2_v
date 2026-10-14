from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tenors import resolve_tenor


@pytest.fixture
def cfg():
    return replace(
        load_config(Path(__file__).resolve().parents[1] / "config.toml"),
        tenors=["M+1", "M+2"], warmup_days=0, layer_hist="on",
        basis_mode="ratio", max_anchor_dev=0, min_volume=0, max_ratio_deviation=0.5,
    )


def _mapping(product):
    return ProductMap(product, "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin")


def _book(observations):
    rows = []
    for day, prices in observations:
        for tenor, price in prices.items():
            period = resolve_tenor(tenor, day)
            rows.append((day, period.kind, period.start, price))
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def _own(rows):
    return pd.DataFrame(rows, columns=["date", "product", "tenor", "vwap", "volume"])


def test_daily_and_refill_replay_all_original_history_and_reset_on_reuse(cfg):
    cfg = replace(cfg, hist_max_age_days=1000)
    start, middle, previous, target = (
        date(2026, 1, 5), date(2026, 6, 1), date(2026, 9, 29), date(2026, 9, 30),
    )
    book = _book((day, {"M+1": 100.0, "M+2": 100.0})
                 for day in (start, middle, previous, target))
    own = _own([(day, "TEST", "M+1", price, 20.0)
                for day, price in [(start, 105.0), (middle, 115.0), (previous, 120.0)]])
    engine = CurveFiller(cfg, own, [_mapping("TEST")], {"TEST": book})
    refill = engine.run(start, target)
    daily = engine.run(target, target)
    fresh_daily = CurveFiller(cfg, own, [_mapping("TEST")], {"TEST": book}).run(target, target)
    assert not refill.errors and not daily.errors and not fresh_daily.errors
    expected = refill.filled[refill.filled.reference_date == target].reset_index(drop=True)
    pd.testing.assert_frame_equal(daily.filled, expected)
    pd.testing.assert_frame_equal(fresh_daily.filled, expected)
    assert set(daily.filled.source) == {"eex+hist"}
    assert daily.filled.price.between(105.0, 120.0, inclusive="neither").all()


@pytest.mark.parametrize("own_price,eex_price", [(0.0, 10.0), (10.0, 0.0), (10.0, 0.1),
                                                  (10.0, -10.0), (30.0, 10.0)])
def test_unstable_ratio_preserves_original_without_using_it_as_anchor(cfg, own_price, eex_price):
    day = date(2026, 9, 30)
    own = _own([(day, "TEST", "M+1", own_price, 20.0)])
    book = _book([(day, {"M+1": eex_price, "M+2": 100.0})])
    result = CurveFiller(cfg, own, [_mapping("TEST")], {"TEST": book}).run(day, day, loo=True)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert output.loc["M+1", "price"] == own_price
    assert output.loc["M+1", "source"] == "own"
    assert output.loc["M+1", "flag"] == "anchor_excluded"
    assert output.loc["M+2", "source"] == "missing"
    assert pd.isna(output.loc["M+2", "price"])
    assert pd.isna(output.loc["M+2", "basis_local"])
    assert "eex_fallback_unavailable" in output.loc["M+2", "flag"]
    assert np.isfinite(result.loo[["pred", "error"]].to_numpy()).all()
    assert "eex" in set(result.loo.method)  # Raw EEX remains an evaluation baseline.
    assert "pipeline_configured" not in set(result.loo.method)


def test_ratio_accepts_same_sign_negative_anchor(cfg):
    day = date(2026, 9, 30)
    own = _own([(day, "TEST", "M+1", -10.0, 20.0)])
    book = _book([(day, {"M+1": -12.0, "M+2": 100.0})])
    result = CurveFiller(cfg, own, [_mapping("TEST")], {"TEST": book}).run(day, day)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert output.loc["M+1", "price"] == -10.0
    assert output.loc["M+1", "source"] == "own"
    assert output.loc["M+1", "flag"] == ""
    assert output.loc["M+2", "source"] == "eex+local"
    assert output.loc["M+2", "basis_local"] == pytest.approx(-10.0 / -12.0 - 1.0)
    assert 100.0 * (-10.0 / -12.0) < output.loc["M+2", "price"] < 100.0


@pytest.mark.parametrize("target_eex", [0.0, 0.1, -0.1])
def test_ratio_does_not_apply_anchor_floor_or_additive_fallback_to_target(cfg, target_eex):
    day = date(2026, 9, 30)
    own = _own([(day, "TEST", "M+1", 120.0, 20.0)])
    book = _book([(day, {"M+1": 100.0, "M+2": target_eex})])
    result = CurveFiller(cfg, own, [_mapping("TEST")], {"TEST": book}).run(day, day)
    assert not result.errors
    target = result.filled.set_index("tenor").loc["M+2"]
    assert abs(target_eex) < cfg.ratio_eex_floor
    assert target["source"] == "eex+local"
    assert target["basis_mode"] == "ratio"
    assert 0.0 < target["basis"] < 0.2
    assert target["price"] == pytest.approx(target_eex * (1.0 + target["basis"]))
    assert target["flag"] == ""
    if target_eex == 0.0:
        assert target["price"] == 0.0
    else:
        assert abs(target_eex) < abs(target["price"]) < cfg.ratio_eex_floor
        assert target["price"] * target_eex > 0


def test_additive_mode_can_learn_from_zero_eex_without_ratio_division(cfg):
    cfg = replace(cfg, basis_mode="additive")
    day = date(2026, 9, 30)
    own = _own([(day, "TEST", "M+1", 10.0, 20.0)])
    book = _book([(day, {"M+1": 0.0, "M+2": 100.0})])
    result = CurveFiller(cfg, own, [_mapping("TEST")], {"TEST": book}).run(day, day, loo=True)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert output.loc["M+1", "price"] == 10.0
    assert output.loc["M+2", "source"] == "eex+local"
    assert 100.0 < output.loc["M+2", "price"] < 110.0
    assert np.isfinite(result.loo[["pred", "error"]].to_numpy()).all()


def test_stale_eex_does_not_train_history_and_old_basis_expires(cfg):
    cfg = replace(cfg, max_ratio_deviation=2.0, hist_max_age_days=3)
    first = date(2026, 9, 21)
    stale, next_day, expired = first + timedelta(days=1), first + timedelta(days=2), first + timedelta(days=4)
    book = _book((day, {"M+1": 100.0, "M+2": 100.0}) for day in (first, next_day, expired))
    own = _own([(first, "TEST", "M+1", 110.0, 20.0), (stale, "TEST", "M+1", 200.0, 20.0)])
    result = CurveFiller(cfg, own, [_mapping("TEST")], {"TEST": book}).run(first, expired)
    assert not result.errors
    output = result.filled.set_index(["reference_date", "tenor"])
    assert output.loc[(stale, "M+1"), "price"] == 200.0
    assert output.loc[(next_day, "M+2"), "price"] == pytest.approx(110.0)
    assert output.loc[(expired, "M+2"), "source"] == "missing"
    assert pd.isna(output.loc[(expired, "M+2"), "basis_hist"])
    assert pd.isna(output.loc[(expired, "M+2"), "price"])
    assert "eex_fallback_unavailable" in output.loc[(expired, "M+2"), "flag"]


def test_filler_covariances_decay_by_calendar_days_between_observations(cfg):
    cfg = replace(cfg, tenors=["M+1", "Q+1"], corr_halflife_days=10.0, cross_halflife_days=10.0)
    days = (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 12))
    maps = [_mapping("A"), _mapping("B")]
    book = _book((day, {"M+1": 100.0, "Q+1": 100.0}) for day in days)
    own = _own([(day, product, tenor, 100.0 + step * scale, 20.0)
                for step, day in enumerate(days, 1)
                for product, scale in (("A", 1.0), ("B", 2.0))
                for tenor in cfg.tenors])
    engine = CurveFiller(cfg, own, maps, {m.product: book for m in maps})
    series = engine._build_series()
    for day in days:
        preps = {s.key: engine._prep(s, day) for s in series}
        engine._update(series, day, preps)
    # The first date initializes history; the remaining two supply surprises.
    covariances = [*series[0].gcov.values(), *series[1].gcov.values(), *engine.cross.values()]
    assert len(covariances) == 16  # Eight independent covariance states per mode.
    for covariance in covariances:
        assert covariance.last_day == days[-1]
        assert covariance.n == pytest.approx(1.5)


def test_auto_keeps_history_skill_and_cross_covariances_in_separate_units(cfg):
    cfg = replace(cfg, basis_mode="auto", tenors=["M+1", "Q+1"], layer_cross=True,
                  layer_correlation=True, cross_min_obs=1.0)
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(4)]
    maps = [_mapping("A"), _mapping("B")]
    observations = {m.product: [] for m in maps}
    own_rows = []
    for step, day in enumerate(days, 1):
        for product, scale in (("A", 80.0), ("B", 160.0)):
            prices = {"M+1": scale * step, "Q+1": 2 * scale * step}
            if day == days[-1] and product == "A":
                prices["M+1"] = 0.0
            else:
                own_rows.extend((day, product, tenor, price * 1.125, 20.0)
                                for tenor, price in prices.items())
            observations[product].append((day, prices))
    books = {product: _book(values) for product, values in observations.items()}
    engine = CurveFiller(cfg, _own(own_rows), maps, books)
    series = engine._build_series()
    for day in days[:-1]:
        preps = {s.key: engine._prep(s, day) for s in series}
        engine._update(series, day, preps)

    period = resolve_tenor("M+1", days[-1])
    for s in series:
        assert s.hist["ratio"].get(period) == pytest.approx(0.125)
        assert s.hist["additive"].get(period) > 10.0
        assert s.skill["ratio"].stats["month"][0] == pytest.approx(0.0)
        assert s.skill["additive"].stats["month"][0] > 0.0
        assert s.gcov["ratio", "month", "quarter"].corr is None
        assert s.gcov["additive", "month", "quarter"].corr == pytest.approx(1.0)
    assert engine.cross["ratio", ("A", "", ""), ("B", "", ""), "month"].corr is None
    assert engine.cross["additive", ("A", "", ""), ("B", "", ""), "month"].beta == pytest.approx(0.5)

    preps = {s.key: engine._prep(s, days[-1]) for s in series}
    output = pd.DataFrame(engine._fill(series[0], days[-1], preps)).set_index("tenor")
    # Additive shocks covary even though the ratio is constant in both products.
    assert output.loc["M+1", "basis_mode"] == "additive"
    assert output.loc["M+1", "source"] == "eex+cross"
    assert output.loc["M+1", "price"] == pytest.approx(40.0)
    assert output.loc["M+1", "cross_adj"] > 0.0
    assert output.loc["Q+1", "basis_mode"] == "ratio"
    assert output.loc["Q+1", "source"] == "eex+hist"
    assert pd.isna(output.loc["Q+1", "cross_adj"])


@pytest.mark.parametrize("phase,method", [("prepare", "_prep"), ("fill", "_fill")])
def test_product_failure_is_reported_without_losing_healthy_product(cfg, monkeypatch, phase, method):
    day = date(2026, 9, 30)
    maps = [_mapping("BAD"), _mapping("GOOD")]
    book = _book([(day, {"M+1": 100.0, "M+2": 100.0})])
    observations = _own([(day, "GOOD", "M+1", 110.0, 20.0)])
    engine = CurveFiller(cfg, observations, maps, {m.product: book for m in maps})
    original = getattr(engine, method)

    def fail_one(series, *args, **kwargs):
        if series.key == ("BAD", "", ""):
            raise ValueError("invalid product data")
        return original(series, *args, **kwargs)

    monkeypatch.setattr(engine, method, fail_one)
    result = engine.run(day, day)
    assert result.errors == [{"reference_date": day, "product": "BAD", "region": "", "unit": "",
                              "phase": phase}]
    assert set(result.filled["product"]) == {"GOOD"}
    output = result.filled.set_index("tenor")
    assert output.loc["M+1", "source"] == "own"
    assert output.loc["M+1", "price"] == 110.0
    assert output.loc["M+2", "source"] == "eex+local"
    assert 100.0 < output.loc["M+2", "price"] < 110.0


def test_peak_weekend_quote_does_not_populate_zero_hour_bow(cfg):
    cfg = replace(cfg, tenors=["WE", "BOW"])
    day = date(2026, 9, 25)  # Friday: WE and BOW share dates, not delivery hours.
    own = _own([(day, "TEST", "WE", 90.0, 20.0)])
    mapping = replace(_mapping("TEST"), profile="Peak", hours="Peak")
    result = CurveFiller(cfg, own, [mapping], {"TEST": None}).run(day, day)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert output.loc["WE", "hours"] == 24.0
    assert output.loc["WE", "price"] == 90.0
    assert output.loc["WE", "source"] == "own"
    assert output.loc["BOW", "hours"] == 0.0
    assert output.loc["BOW", "source"] == "missing"
    assert pd.isna(output.loc["BOW", "price"])
    assert pd.isna(output.loc["BOW", "own_vwap"])
    assert output.loc["BOW", "flag"] == "zero_delivery_hours"


def test_zero_hour_own_contract_cannot_supply_peak_weekend_or_residual(cfg):
    cfg = replace(cfg, tenors=["WE", "BOW", "D+2"])
    day = date(2026, 9, 25)
    # A BOW quote must not become a Weekend parent for Sunday's residual.
    own = _own([(day, "TEST", "BOW", 30.0, 20.0),
                (day, "TEST", "D+1", 20.0, 20.0)])
    snapshot = own.copy(deep=True)
    mapping = replace(_mapping("TEST"), profile="Peak", hours="Peak")
    result = CurveFiller(cfg, own, [mapping], {"TEST": None}).run(day, day)
    assert not result.errors
    assert result.filled.source.eq("missing").all()
    assert result.filled.price.isna().all()
    pd.testing.assert_frame_equal(own, snapshot)


@pytest.mark.parametrize("reverse", [False, True])
def test_zero_hour_alias_does_not_change_valid_peak_weekend_quote(cfg, reverse):
    cfg = replace(cfg, tenors=["WE", "BOW"])
    day = date(2026, 9, 25)
    rows = [(day, "TEST", "BOW", 200.0, 200.0), (day, "TEST", "WE", 90.0, 20.0)]
    own = _own(rows[::-1] if reverse else rows)
    mapping = replace(_mapping("TEST"), profile="Peak", hours="Peak")
    result = CurveFiller(cfg, own, [mapping], {"TEST": None}).run(day, day)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert output.loc["WE", "price"] == 90.0
    assert output.loc["WE", "own_volume"] == 20.0
    assert output.loc["WE", "source"] == "own"
    assert output.loc["BOW", "source"] == "missing"
