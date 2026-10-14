"""Regressions for order-independent evidence and contract reconstruction."""

from dataclasses import replace
from datetime import date
from itertools import permutations
import math
from pathlib import Path

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.hours import hours_fn
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.pricer import Pricer
from vwaps.tenors import resolve_tenor


DAY = date(2026, 9, 30)
MAPPING = ProductMap("TEST", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin")


@pytest.fixture
def cfg():
    return replace(load_config(Path(__file__).resolve().parents[1] / "config.toml"),
                   tenors=["M+1", "M+2"], basis_mode="additive", layer_hist="off",
                   layer_local=True, layer_arbitrage=False, min_volume=0, max_anchor_dev=0)


def _own(observations, reference=DAY):
    return pd.DataFrame([(reference, "TEST", tenor, price, volume)
                         for tenor, price, volume in observations],
                        columns=["date", "product", "tenor", "vwap", "volume"])


def _book(prices, reference=DAY):
    rows = []
    for label, price in prices.items():
        period = resolve_tenor(label, reference)
        rows.append((reference, period.kind, period.start, price))
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


@pytest.mark.parametrize("setting,value,error", [
    ("layer_local", "false", "layers.local"),
    ("layer_arbitrage", 1, "layers.arbitrage"),
    ("tau_log", math.nan, "method.tau_log"),
    ("min_volume", math.inf, "method.min_volume"),
    ("shrink_k", True, "method.shrink_k"),
    ("fallback_price_window", 1, "eex_fallback.price_window"),
])
def test_direct_engine_configuration_fails_before_calculations(cfg, setting, value, error):
    with pytest.raises(ValueError, match=error):
        CurveFiller(replace(cfg, **{setting: value}), _own([]), [MAPPING], {MAPPING.key: None})


@pytest.mark.parametrize("volumes,expected,expected_volume", [
    ((math.nan, math.nan, math.nan), 100.0, math.nan),
    ((0.0, 0.0, 0.0), 100.0, 0.0),
    ((10.0, math.nan, 20.0), 3200.0 / 31, 30.0),
    ((10.0, 0.0, 20.0), 3200.0 / 31, 30.0),
    ((10.0, 30.0, 20.0), 6100.0 / 60, 60.0),
    ((-1.0, math.inf, -math.inf), 100.0, math.nan),
])
def test_duplicate_aggregation_and_refill_do_not_depend_on_row_order(
        cfg, volumes, expected, expected_volume):
    observations = list(zip((90.0, 100.0, 110.0), volumes))
    reference = _book({"M+1": 100.0, "M+2": 100.0})
    predictions = []
    for ordering in permutations(observations):
        own = _own([("M+1", price, volume) for price, volume in ordering])
        snapshot = own.copy(deep=True)
        result = CurveFiller(cfg, own, [MAPPING], {MAPPING.key: reference}).run(DAY, DAY)
        assert not result.errors
        rows = result.filled.set_index("tenor")
        assert rows.loc["M+1", "price"] == pytest.approx(expected)
        volume = rows.loc["M+1", "own_volume"]
        assert math.isnan(volume) if math.isnan(expected_volume) else volume == expected_volume
        predictions.append(rows.loc["M+2", "price"])
        pd.testing.assert_frame_equal(own, snapshot)
    assert all(value == predictions[0] for value in predictions)


def test_unknown_duplicate_volume_remains_eligible_under_min_volume(cfg):
    cfg = replace(cfg, min_volume=5)
    own = _own([("M+1", 110.0, math.nan), ("M+1", 120.0, math.nan)])
    result = CurveFiller(cfg, own, [MAPPING], {
        MAPPING.key: _book({"M+1": 100.0, "M+2": 100.0}),
    }).run(DAY, DAY)
    rows = result.filled.set_index("tenor")
    assert not result.errors
    assert math.isnan(rows.loc["M+1", "own_volume"])
    assert rows.loc["M+1", "price"] == pytest.approx(115.0)
    assert rows.loc["M+1", "flag"] == ""
    assert rows.loc["M+2", "source"] == "eex+local"
    assert rows.loc["M+2", "basis_local"] == pytest.approx(15.0)


@pytest.mark.parametrize("prices,expected", [
    ((-100.0, 0.0, 100.0), 0.0),
    ((-1e308, 0.0, 1e308), 0.0),
    ((1e308, 1e308, 1e308), 1e308),
])
def test_aggregation_accepts_zero_negative_and_large_finite_prices(cfg, prices, expected):
    own = _own([("M+1", price, 1e300) for price in prices])
    engine = CurveFiller(cfg, own, [MAPPING], {MAPPING.key: None})
    quotes = engine._own_quotes(DAY, own, hours_fn("Base", "Europe/Berlin"))
    quote = quotes[resolve_tenor("M+1", DAY).key]
    assert quote.vwap == pytest.approx(expected)
    assert math.isfinite(quote.vwap)
    assert quote.volume == pytest.approx(3e300)


@pytest.mark.parametrize("reference,labels,target,canonical_kind", [
    (date(2026, 9, 29), ["D+1", "BOM"], "D+2", "Day"),
    (date(2026, 9, 25), ["WE", "BOW"], "WE+1", "Weekend"),
])
def test_alias_family_and_history_are_independent_of_row_order(
        cfg, reference, labels, target, canonical_kind):
    cfg = replace(cfg, tenors=[target])
    book = _book({labels[0]: 100.0, target: 100.0}, reference)
    outputs = []
    for ordering in permutations(labels):
        own = _own([(label, 110.0, 10.0) for label in ordering], reference)
        engine = CurveFiller(cfg, own, [MAPPING], {MAPPING.key: book})
        series = engine._build_series()[0]
        prep = engine._prep(series, reference)
        assert len(prep.anchors) == 1
        anchor = prep.anchors[0]
        assert anchor.period.kind == canonical_kind
        assert anchor.volume == 20.0
        assert anchor.tenor == ",".join(sorted(labels))
        outputs.append(engine._fill(series, reference, {series.key: prep}))
        engine._update([series], reference, {series.key: prep})
        assert set(series.hist["additive"].values) == {canonical_kind, "short", "all"}
    for field in ("price", "local_weight", "basis_local", "anchors"):
        assert outputs[0][0][field] == outputs[1][0][field]


def test_repeated_alias_label_is_listed_once_without_discarding_observations(cfg):
    own = _own([("M+1", 90.0, 10.0), ("M+1", 110.0, 10.0), ("m + 1", 100.0, 10.0)])
    engine = CurveFiller(cfg, own, [MAPPING], {MAPPING.key: None})
    quotes = engine._own_quotes(DAY, own, hours_fn("Base", "Europe/Berlin"))
    quote = quotes[resolve_tenor("M+1", DAY).key]
    assert quote.tenors == ["M+1", "m + 1"]
    assert quote.vwap == pytest.approx(100.0)
    assert quote.volume == 30.0


def test_loo_scores_the_canonical_kind_when_residual_alias_sorts_first(cfg):
    reference = date(2026, 9, 29)
    cfg = replace(cfg, tenors=["D+1", "BOM", "D+2"])
    book = _book({"D+1": 100.0, "D+2": 100.0}, reference)
    own = _own([("BOM", 110.0, 10.0), ("D+1", 110.0, 10.0),
                ("D+2", 130.0, 20.0)], reference)
    result = CurveFiller(cfg, own, [MAPPING], {MAPPING.key: book}).run(reference, reference, loo=True)
    assert not result.errors
    score = result.loo[(result.loo.tenor == "BOM,D+1")
                       & (result.loo.method == "pipeline_configured")].iloc[0]
    hidden = own.loc[own.tenor.eq("D+2")]
    actual = CurveFiller(cfg, hidden, [MAPPING], {MAPPING.key: book}).run(reference, reference)
    rows = actual.filled.set_index("tenor")
    assert score["kind"] == "Day"
    assert score["pred"] == rows.loc["D+1", "price"]
    assert rows.loc["BOM", "price"] != rows.loc["D+1", "price"]


@pytest.mark.parametrize("rejection", ["volume", "deviation", "ratio"])
def test_arbitrage_cannot_reuse_rejected_original_by_adding_it_to_targets(cfg, rejection):
    cfg = replace(cfg, layer_arbitrage=True, layer_local=False,
                  min_volume=5 if rejection == "volume" else 0,
                  max_anchor_dev=0.5 if rejection == "deviation" else 0,
                  basis_mode="ratio" if rejection == "ratio" else "additive")
    own = _own([("M+1", 100.0, 20.0), ("M+2", 900.0, 1.0), ("M+3", 100.0, 20.0)])
    book = _book({"M+1": 100.0, "M+2": 100.0, "M+3": 100.0})
    for labels in (["Q+1"], ["M+1", "M+2", "M+3", "Q+1"]):
        result = CurveFiller(replace(cfg, tenors=labels), own, [MAPPING], {
            MAPPING.key: book,
        }).run(DAY, DAY)
        assert not result.errors
        rows = result.filled.set_index("tenor")
        assert rows.loc["Q+1", "source"] == "missing"
        assert math.isnan(rows.loc["Q+1", "price"])
        if "M+2" in labels:
            assert rows.loc["M+2", "price"] == 900.0
            assert rows.loc["M+2", "source"] == "own"
            assert rows.loc["M+2", "data_origin"] == "original"
            assert rows.loc["M+2", "flag"] == "anchor_excluded"


@pytest.mark.parametrize("volume,eligible", [(1.0, False), (0.0, False), (5.0, True), (math.nan, True)])
@pytest.mark.parametrize("include_original_targets", [False, True])
def test_arbitrage_respects_minimum_volume_without_any_eex(cfg, volume, eligible, include_original_targets):
    targets = ["M+1", "M+2", "M+3", "Q+1"] if include_original_targets else ["Q+1"]
    cfg = replace(cfg, layer_arbitrage=True, min_volume=5, tenors=targets)
    own = _own([("M+1", 100.0, 20.0), ("M+2", 100.0, volume), ("M+3", 100.0, 20.0)])
    result = CurveFiller(cfg, own, [MAPPING], {MAPPING.key: None}).run(DAY, DAY)
    assert not result.errors
    rows = result.filled.set_index("tenor")
    assert rows.loc["Q+1", "source"] == ("arbitrage" if eligible else "missing")
    if eligible:
        assert rows.loc["Q+1", "price"] == pytest.approx(100.0)
    else:
        assert math.isnan(rows.loc["Q+1", "price"])
    if include_original_targets:
        assert rows.loc["M+2", "price"] == 100.0
        assert rows.loc["M+2", "data_origin"] == "original"
        assert rows.loc["M+2", "flag"] == ("" if eligible else "anchor_excluded")


@pytest.mark.parametrize("tail", [date(2026, 8, 2), date(2026, 8, 3)])
def test_peak_residual_with_zero_hour_head_retains_parent_price(tail):
    start, end = date(2026, 8, 1), date(2026, 9, 1)
    hours = hours_fn("Peak", "Europe/Berlin")
    pricer = Pricer({(start, end): 100.0}, hours)
    assert hours(start, tail, "BOM") == 0
    assert hours(tail, end, "BOM") == hours(start, end, "Month") == 252.0
    assert pricer.price(tail, end, "BOM") == (100.0, "residual")


def test_zero_hour_head_fix_does_not_invent_missing_base_or_peak7_head_prices():
    start, tail, end = date(2026, 8, 1), date(2026, 8, 3), date(2026, 9, 1)
    for profile in ("Base", "Peak7"):
        pricer = Pricer({(start, end): 100.0}, hours_fn(profile, "Europe/Berlin"))
        assert pricer.price(tail, end, "BOM") is None
    # A positive-hour Monday has also been delivered, but no fixing is available.
    pricer = Pricer({(start, end): 100.0}, hours_fn("Peak", "Europe/Berlin"))
    assert pricer.price(date(2026, 8, 4), end, "BOM") is None


def test_zero_hour_head_fix_keeps_incompatible_peak_month_to_day_blocked():
    start, tail, end = date(2026, 8, 1), date(2026, 8, 31), date(2026, 9, 1)
    pricer = Pricer({(start, end): 100.0}, hours_fn("Peak", "Europe/Berlin"))
    assert pricer.price(tail, end, "Day") is None
