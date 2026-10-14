"""EEX availability offsets preserve reference dates and causal delayed learning."""

from dataclasses import replace
from datetime import date, timedelta
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.eex_fallback import EexFallback
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap


@pytest.fixture
def cfg():
    return replace(load_config(Path(__file__).resolve().parents[1] / "config.toml"),
                   tenors=["M+2"], basis_mode="additive", layer_hist="on", layer_local=False,
                   layer_correlation=False, layer_cross=False, layer_arbitrage=False,
                   shape_mode="off", warmup_days=0, eex_offset_days=0,
                   fallback_price_window=2, fallback_spread_window=2)


def _mapping(product="P", use="fill"):
    return ProductMap(product, use, "DE", "Base", "synthetic.csv", "Base", "Europe/Berlin",
                      region="North", unit="EUR/MWh")


def _own(rows):
    return pd.DataFrame([
        dict(date=day, product=product, region="North", unit="EUR/MWh",
             tenor=tenor, vwap=price, volume=100.0)
        for day, product, tenor, price in rows
    ], columns=["date", "product", "region", "unit", "tenor", "vwap", "volume"])


def _book(rows):
    return EexBook(pd.DataFrame([
        (day, "Month", date(2026, month, 1), price)
        for day, prices in rows for month, price in prices.items()
    ], columns=REQUIRED))


def _engine(cfg, own, book):
    mapping = _mapping()
    return CurveFiller(cfg, own, [mapping], {mapping.key: book})


def test_zero_offset_preserves_training_after_prediction_and_same_day_lookup(cfg):
    first, second, third = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)
    own = _own([(first, "P", "M+1", 110.0), (second, "P", "M+1", 130.0)])
    book = _book([(day, {10: 100.0, 11: 100.0}) for day in (first, second, third)])
    result = _engine(cfg, own, book).run(first, third)
    assert not result.errors
    rows = result.filled.set_index("reference_date")
    assert rows.loc[first, "source"] == "missing"
    assert rows.loc[second, "price"] == pytest.approx(110.0)
    alpha = 1 - 0.5 ** (1 / cfg.ewma_halflife_days)
    assert rows.loc[third, "price"] == pytest.approx(100 + (1 - alpha) * 10 + alpha * 30)
    assert rows["eex_asof"].tolist() == [first, second, third]
    assert rows["eex_cutoff_date"].tolist() == [first, second, third]
    assert rows["eex_offset_days"].eq(0).all()


def test_negative_offset_releases_same_day_pairs_later_and_audits_loo(cfg):
    cfg = replace(cfg, eex_offset_days=-1)
    first, second, third = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)
    own = _own([(first, "P", "M+1", 110.0), (second, "P", "M+1", 230.0)])
    book = _book([(first, {10: 100.0, 11: 100.0}),
                  (second, {10: 200.0, 11: 200.0}), (third, {10: 9000.0, 11: 9000.0})])
    result = _engine(cfg, own, book).run(second, third, loo=True)
    assert not result.errors
    rows = result.filled.set_index("reference_date")
    assert rows.loc[second, "price"] == pytest.approx(110.0)
    alpha = 1 - 0.5 ** (1 / cfg.ewma_halflife_days)
    assert rows.loc[third, "price"] == pytest.approx(200 + (1 - alpha) * 10 + alpha * 30)
    assert rows["eex_asof"].tolist() == [first, second]
    assert rows["eex_cutoff_date"].tolist() == [first, second]
    assert rows["eex_offset_days"].eq(-1).all()
    assert result.loo.eex_asof.eq(first).all()
    assert result.loo.eex_cutoff_date.eq(first).all()
    assert result.loo.eex_offset_days.eq(-1).all()
    baseline = result.loo.query("method == 'eex'").iloc[0]
    deployed = result.loo.query("method == 'pipeline_configured'").iloc[0]
    assert baseline["pred"] == 100.0 and baseline["own"] == 230.0
    assert deployed["pred"] == pytest.approx(110.0)


@pytest.mark.parametrize("offset", [-1, -3])
def test_daily_refill_and_reused_engine_match_with_delayed_training(cfg, offset):
    cfg = replace(cfg, eex_offset_days=offset)
    days = list(pd.bdate_range("2026-09-01", periods=12).date)
    own = _own([(day, "P", "M+1", 105.0 + index) for index, day in enumerate(days)])
    book = _book([(day, {10: 100.0, 11: 200.0}) for day in days])
    engine = _engine(cfg, own, book)
    refill = engine.run(days[3], days[-1], loo=True)
    daily = engine.run(days[-1], days[-1], loo=True)
    fresh = _engine(cfg, own, book).run(days[-1], days[-1], loo=True)
    assert not refill.errors and not daily.errors and not fresh.errors
    expected = refill.filled[refill.filled.reference_date.eq(days[-1])].reset_index(drop=True)
    expected_loo = refill.loo[refill.loo.reference_date.eq(days[-1])].reset_index(drop=True)
    pd.testing.assert_frame_equal(daily.filled, expected)
    pd.testing.assert_frame_equal(fresh.filled, expected)
    pd.testing.assert_frame_equal(daily.loo, expected_loo)


def test_eex_after_cutoff_and_future_originals_cannot_change_prediction_or_loo(cfg):
    cfg = replace(cfg, eex_offset_days=-1)
    days = list(pd.bdate_range("2026-09-01", periods=5).date)
    own = _own([(day, "P", "M+1", 110.0 + index) for index, day in enumerate(days)])
    quotes = [(day, {10: 100.0, 11: 200.0}) for day in days]
    target = days[2]
    first = _engine(cfg, own, _book(quotes)).run(target, target, loo=True)
    altered = own.copy()
    altered.loc[altered.date > target, "vwap"] = 1e6
    later_eex = [(day, {10: 1e7, 11: 2e7} if day >= target else prices) for day, prices in quotes]
    second = _engine(cfg, altered, _book(later_eex)).run(target, target, loo=True)
    pd.testing.assert_frame_equal(first.filled, second.filled)
    pd.testing.assert_frame_equal(first.loo, second.loo)


def test_prediction_expiry_does_not_erase_history_before_delayed_observation_is_released(cfg, monkeypatch):
    cfg = replace(cfg, eex_offset_days=-5, hist_max_age_days=6)
    first, later = date(2026, 9, 1), date(2026, 9, 4)
    start, middle, end = date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)
    own = _own([(first, "P", "M+1", 110.0), (later, "P", "M+1", 130.0)])
    days = list(pd.bdate_range(first, end).date)
    book = _book([(day, {10: 100.0, 11: 100.0}) for day in days])
    released = []
    original = CurveFiller._update

    def capture(self, series, day, preps):
        released.append(day)
        return original(self, series, day, preps)

    monkeypatch.setattr(CurveFiller, "_update", capture)
    result = _engine(cfg, own, book).run(start, end)
    assert not result.errors
    rows = result.filled.set_index("reference_date")
    assert rows.loc[start, "basis_hist"] == pytest.approx(10.0)
    assert math.isnan(rows.loc[middle, "basis_hist"])
    alpha = 1 - 0.5 ** (1 / cfg.ewma_halflife_days)
    assert rows.loc[end, "basis_hist"] == pytest.approx((1 - alpha) * 10 + alpha * 30)
    assert released == [first, later]  # Released once, using original observation dates.


def test_missing_same_day_eex_never_trains_own_against_previous_eex(cfg):
    cfg = replace(cfg, eex_offset_days=-1)
    first, missing, later, target = (date(2026, 9, day) for day in (1, 2, 3, 4))
    own = _own([(missing, "P", "M+1", 900.0)])
    book = _book([(first, {10: 100.0, 11: 100.0}), (later, {10: 100.0, 11: 100.0})])
    result = _engine(cfg, own, book).run(target, target)
    row = result.filled.iloc[0]
    assert not result.errors
    assert math.isnan(row["basis_hist"])
    assert row["source"] == "eex+smooth"
    assert row["price"] == pytest.approx(100.0)


def test_limited_warmup_bounds_historical_dates_before_delayed_release(cfg):
    first, later, target = date(2026, 9, 1), date(2026, 9, 4), date(2026, 9, 7)
    cfg = replace(cfg, eex_offset_days=-1, warmup_days=4)
    own = _own([(first, "P", "M+1", 150.0), (later, "P", "M+1", 110.0)])
    book = _book([(first, {10: 100.0, 11: 100.0}), (later, {10: 100.0, 11: 100.0})])
    bounded = _engine(cfg, own, book).run(target, target)
    unlimited = _engine(replace(cfg, warmup_days=0), own, book).run(target, target)
    assert not bounded.errors and not unlimited.errors
    assert bounded.filled.iloc[0]["basis_hist"] == 10.0
    assert unlimited.filled.iloc[0]["basis_hist"] > 10.0


def test_age_limit_is_measured_from_reference_date_even_with_delayed_history(cfg):
    cfg = replace(cfg, eex_offset_days=-3, max_stale_days=2, tenors=["M+1", "M+2"])
    historical, target = date(2026, 9, 1), date(2026, 9, 4)
    own = _own([(historical, "P", "M+1", 110.0), (target, "P", "M+1", 500.0)])
    book = _book([(historical, {10: 100.0, 11: 100.0}), (target, {10: 200.0, 11: 200.0})])
    result = _engine(cfg, own, book).run(target, target)
    assert not result.errors
    rows = result.filled.set_index("tenor")
    assert rows.eex_asof.isna().all()
    assert rows.eex_cutoff_date.eq(historical).all()
    assert rows.loc["M+1", "price"] == 500.0 and rows.loc["M+1", "source"] == "own"
    assert rows.loc["M+2", "source"] == "missing"


def test_month_roll_resolves_target_on_reference_date_and_historical_anchors_on_their_dates(cfg):
    cfg = replace(cfg, eex_offset_days=-1, tenors=["M+1"])
    first, second, target = date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)
    own = _own([(first, "P", "M+1", 110.0), (second, "P", "M+1", 121.0)])
    book = _book([(first, {10: 100.0, 11: 200.0}), (second, {10: 101.0, 11: 201.0})])
    row = _engine(cfg, own, book).run(target, target).filled.iloc[0]
    assert row["delivery_start"] == date(2026, 11, 1)
    assert row["eex_asof"] == second
    assert row["eex_settle"] == 201.0
    alpha = 1 - 0.5 ** (1 / cfg.ewma_halflife_days)
    assert row["basis_hist"] == pytest.approx((1 - alpha) * 10 + alpha * 20)
    assert row["price"] == pytest.approx(201 + row["basis_hist"])


def test_helpers_and_covariances_learn_once_with_historical_timestamps_but_no_stale_shocks(cfg):
    cfg = replace(cfg, eex_offset_days=-1, layer_cross=True, cross_min_obs=1.0)
    days = list(pd.bdate_range("2026-09-01", periods=4).date)
    mappings = [_mapping("P"), _mapping("HELPER", "helper")]
    own = _own([(day, product, "M+1", 100.0 + scale * increase)
                for day, increase in zip(days, (1, 2, 4, 50))
                for product, scale in (("P", 1.0), ("HELPER", 2.0))])
    book = _book([(day, {10: 100.0, 11: 100.0}) for day in days])
    books = {mapping.key: book for mapping in mappings}
    delayed_engine = CurveFiller(cfg, own, mappings, books)
    result = delayed_engine.run(days[-1], days[-1])
    historical_engine = CurveFiller(replace(cfg, eex_offset_days=0), own, mappings, books)
    historical_engine.run(days[0], days[-2])
    assert delayed_engine.cross and delayed_engine.cross.keys() == historical_engine.cross.keys()
    for key, state in delayed_engine.cross.items():
        expected = historical_engine.cross[key]
        assert state.last_day == expected.last_day == days[-2]
        assert state.n == expected.n
        assert state.corr == expected.corr
        assert state.beta == expected.beta
    assert result.filled["cross_adj"].isna().all()
    assert result.filled["source"].eq("eex+hist").all()
    assert set(result.filled["product"]) == {"P"}


def test_negative_offset_fallback_uses_cutoff_and_keeps_one_cache_per_series(cfg, monkeypatch):
    cfg = replace(cfg, eex_offset_days=-1, layer_hist="off", tenors=["M+1"])
    days = list(pd.bdate_range("2026-09-01", periods=7).date)
    book = _book([(day, {10: 100.0 + index, 11: 200.0 + index}) for index, day in enumerate(days)])
    constructions = []
    original = EexFallback.__init__

    def counted(self, source, hours, settings):
        constructions.append(settings.eex_offset_days)
        original(self, source, hours, settings)

    monkeypatch.setattr(EexFallback, "__init__", counted)
    result = _engine(cfg, _own([]), book).run(days[3], days[-1])
    assert not result.errors and constructions == [-1]
    for row in result.filled.to_dict("records"):
        trace = json.loads(row["eex_fallback_trace"])
        cutoff = row["reference_date"] + timedelta(days=-1)
        assert row["eex_cutoff_date"] == cutoff
        assert row["eex_asof"] <= cutoff
        assert all(date.fromisoformat(item["trade_date"]) <= cutoff
                   for item in trace["price_average"]["observations"])


def test_explicit_weekend_outputs_do_not_duplicate_friday_training(cfg, monkeypatch):
    cfg = replace(cfg, eex_offset_days=-1)
    friday, saturday, sunday = date(2026, 9, 4), date(2026, 9, 5), date(2026, 9, 6)
    own = _own([(friday, "P", "M+1", 110.0)])
    book = _book([(friday, {10: 100.0, 11: 100.0})])
    released = []
    original = CurveFiller._update

    def capture(self, series, day, preps):
        released.append(day)
        return original(self, series, day, preps)

    monkeypatch.setattr(CurveFiller, "_update", capture)
    keys = {(day, _mapping().key) for day in (saturday, sunday)}
    result = _engine(cfg, own, book).run(saturday, sunday, output_keys=keys)
    assert not result.errors and released == [friday]
    assert result.filled.price.eq(110.0).all()
    assert result.filled.eex_asof.eq(friday).all()
