"""Individual settings retain causal learning and isolated curve identities."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.identity import IDENTITY_COLUMNS, curve_keys
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tenors import resolve_tenor


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    return replace(load_config(path), configuration_mode="individual", warmup_days=0,
                   tenors=["M+1", "M+2"], basis_mode="additive", layer_hist="on",
                   layer_local=False, layer_correlation=False, layer_cross=False,
                   fallback_price_window=2, fallback_spread_window=2)


def mapping(region, **parameters):
    return ProductMap("POWER", "fill", "DE", "Base", "synthetic.csv", "Base", "Europe/Berlin",
                      region=region, unit="EUR/MWh", parameter_overrides=parameters)


def observations(rows):
    return pd.DataFrame([
        dict(date=day, product=m.product, region=m.region, unit=m.unit,
             tenor=tenor, vwap=price, volume=volume)
        for day, m, tenor, price, volume in rows
    ], columns=["date", *IDENTITY_COLUMNS, "tenor", "vwap", "volume"])


def book(schedule):
    return EexBook(pd.DataFrame([
        (day, resolve_tenor(tenor, day).kind, resolve_tenor(tenor, day).start, price)
        for day, prices in schedule for tenor, price in prices.items()
    ], columns=REQUIRED))


def sorted_frame(frame):
    return frame.sort_values(["reference_date", *IDENTITY_COLUMNS, "tenor"]).reset_index(drop=True)


@pytest.mark.parametrize("offset", [0, -1])
def test_each_curve_history_mode_and_memory_match_hand_calculation(cfg, offset):
    cfg = replace(cfg, eex_offset_days=offset)
    first, second, target = list(pd.bdate_range("2026-09-01", periods=3).date)
    additive = mapping("additive", basis_mode="additive", ewma_halflife_days=1.0)
    ratio = mapping("ratio", basis_mode="ratio", ewma_halflife_days=20.0)
    maps = [additive, ratio]
    own = observations([(day, m, "M+1", price, 100.0)
                        for m in maps for day, price in ((first, 110.0), (second, 130.0))])
    eex = book([(day, {"M+1": 100.0, "M+2": 200.0}) for day in (first, second, target)])
    result = CurveFiller(cfg, own, maps, {m.key: eex for m in maps}).run(target, target)
    assert not result.errors
    prices = result.filled.query("tenor == 'M+2'").set_index("region")
    assert prices.loc["additive", "price"] == pytest.approx(220.0)
    alpha = 1 - 0.5 ** (1 / 20.0)
    assert prices.loc["ratio", "price"] == pytest.approx(200.0 * (1 + (1 - alpha) * .1 + alpha * .3))
    assert prices.loc["additive", "configured_basis_mode"] == "additive"
    assert prices.loc["ratio", "configured_basis_mode"] == "ratio"
    assert prices.source.eq("eex+hist").all()


@pytest.mark.parametrize("offset", [0, -1])
def test_mixed_individual_curves_equal_isolated_runs_and_daily_refill(cfg, offset):
    cfg = replace(cfg, eex_offset_days=offset, tenors=[f"M+{i}" for i in range(1, 7)] + ["Q+2"])
    days = list(pd.bdate_range("2026-09-01", periods=12).date)
    maps = [
        mapping("A", basis_mode="ratio", layer_local=True, tau_log=.15,
                ewma_halflife_days=2.0, max_ratio_deviation=.2),
        mapping("B", basis_mode="additive", layer_local=True, other_kind_weight=.1,
                ewma_halflife_days=20.0, shape_mode="audit", shape_coherence_weight=50.0),
        mapping("C", layer_local=False, layer_hist="off", fallback_price_method="simple",
                fallback_price_window=2, fallback_anchor_months=2),
        mapping("D", layer_local=False, layer_hist="off", fallback_price_method="ewma",
                fallback_price_window=5, fallback_ewma_halflife=.5, fallback_anchor_months=2),
    ]
    own = observations([(day, m, tenor, price + i, 100.0)
                        for i, day in enumerate(days[:8]) for m in maps[:2]
                        for tenor, price in (("M+1", 110.0), ("Q+2", 120.0))])
    eex = book([(day, {**{f"M+{j}": 100.0 + j * 10 + i for j in range(1, 7)},
                       "Q+2": 150.0 + i}) for i, day in enumerate(days)])
    books = {m.key: eex for m in maps}
    before_cfg, before_maps = asdict(cfg), deepcopy(maps)
    engine = CurveFiller(cfg, own, maps, books)
    refill = engine.run(days[5], days[-1], loo=True)
    daily = engine.run(days[-1], days[-1], loo=True)
    assert not refill.errors and not daily.errors
    pd.testing.assert_frame_equal(sorted_frame(daily.filled),
                                  sorted_frame(refill.filled[refill.filled.reference_date == days[-1]]))
    for m in maps:
        isolated_cfg = replace(cfg, configuration_mode="global", **m.parameter_overrides)
        isolated = CurveFiller(isolated_cfg, own.loc[curve_keys(own) == m.key],
                               [replace(m, parameter_overrides={})], books).run(days[5], days[-1], loo=True)
        actual = refill.filled.loc[curve_keys(refill.filled) == m.key]
        # Columns introduced only by another curve's shape layer remain empty.
        extra = set(actual.columns) - set(isolated.filled.columns)
        assert all(actual[column].isna().all() for column in extra)
        pd.testing.assert_frame_equal(sorted_frame(actual[isolated.filled.columns]),
                                      sorted_frame(isolated.filled.assign(configuration_mode="individual")),
                                      check_dtype=False)
        if not isolated.loo.empty:
            sort = ["reference_date", "tenor", "method"]
            actual_loo = refill.loo.loc[curve_keys(refill.loo) == m.key]
            pd.testing.assert_frame_equal(actual_loo.sort_values(sort).reset_index(drop=True),
                                          isolated.loo.assign(configuration_mode="individual").sort_values(sort)
                                          .reset_index(drop=True))
    assert asdict(cfg) == before_cfg and maps == before_maps
    final = daily.filled.query("tenor == 'M+1'").set_index("region")
    assert final.loc["C", "estimation_method"] == "eex_price_simple"
    assert final.loc["D", "estimation_method"] == "eex_price_ewma"
    assert final.loc["C", "price"] != final.loc["D", "price"]


def test_global_ignores_mapping_and_explicit_candidate_targets_one_identity(cfg):
    cfg = replace(cfg, configuration_mode="global", layer_local=True, layer_hist="off")
    day = date(2026, 9, 1)
    first, second = mapping("North", basis_mode="ratio"), mapping("South", basis_mode="ratio")
    own = observations([(day, m, "M+1", 110.0, 100.0) for m in (first, second)])
    eex = book([(day, {"M+1": 100.0, "M+2": 200.0})])
    books = {m.key: eex for m in (first, second)}
    engine = CurveFiller(cfg, own, [first, second], books,
                         curve_parameters={first.key: {"basis_mode": "ratio"}})
    result = engine.run(day, day, loo=True)
    rows = result.filled.query("tenor == 'M+2'").set_index("region")
    assert rows.loc["North", "configured_basis_mode"] == "ratio"
    assert rows.loc["South", "configured_basis_mode"] == "additive"
    assert rows.loc["North", "price"] > rows.loc["South", "price"]
    assert cfg.basis_mode == "additive" and second.parameter_overrides == {"basis_mode": "ratio"}
    assert result.loo.query("region == 'North'").configured_basis_mode.eq("ratio").all()
    assert result.loo.query("region == 'South'").configured_basis_mode.eq("additive").all()


def test_individual_liquidity_filter_preserves_original_but_changes_anchor_eligibility(cfg):
    cfg = replace(cfg, layer_local=True, layer_hist="off")
    days = list(pd.bdate_range("2026-09-01", periods=2).date)
    first, second = mapping("filtered", min_volume=20.0), mapping("unfiltered", min_volume=0.0)
    own = observations([(days[-1], m, "M+1", 130.0, 10.0) for m in (first, second)])
    eex = book([(day, {"M+1": 100.0, "M+2": 200.0}) for day in days])
    result = CurveFiller(cfg, own, [first, second], {m.key: eex for m in (first, second)}).run(
        days[-1], days[-1])
    assert not result.errors
    assert result.filled.query("tenor == 'M+1'").price.eq(130.0).all()
    target = result.filled.query("tenor == 'M+2'").set_index("region")
    assert target.loc["filtered", "source"] == "eex+smooth"
    assert target.loc["unfiltered", "source"] == "eex+local"


@pytest.mark.parametrize("offset", [0, -1])
def test_cross_covariance_memory_belongs_to_receiver(cfg, offset):
    cfg = replace(cfg, eex_offset_days=offset, layer_cross=True, cross_min_obs=1.0)
    days = list(pd.bdate_range("2026-09-01", periods=5).date)
    first = mapping("short", ewma_halflife_days=2.0, cross_halflife_days=1.0)
    second = mapping("long", ewma_halflife_days=20.0, cross_halflife_days=30.0)
    own = observations([(day, m, "M+1", 100.0 + factor * increment, 100.0)
                        for day, increment in zip(days, (1, 2, 4, 7, 11))
                        for m, factor in ((first, 1), (second, 2))])
    eex = book([(day, {"M+1": 100.0, "M+2": 200.0}) for day in days])
    engine = CurveFiller(cfg, own, [first, second], {m.key: eex for m in (first, second)})
    result = engine.run(days[-1], days[-1])
    assert not result.errors
    forward = engine.cross["additive", first.key, second.key, "month"]
    reverse = engine.cross["additive", second.key, first.key, "month"]
    assert forward.lam == pytest.approx(.5)
    assert reverse.lam == pytest.approx(.5 ** (1 / 30.0))
    assert forward.n < reverse.n
    assert forward.last_day == reverse.last_day == days[-1 if offset == 0 else -2]


def test_negative_offset_individual_predictions_ignore_future_eex_and_own(cfg):
    cfg = replace(cfg, eex_offset_days=-1)
    days = list(pd.bdate_range("2026-09-01", periods=5).date)
    maps = [mapping("short", ewma_halflife_days=1.0), mapping("long", ewma_halflife_days=20.0)]
    own = observations([(day, m, "M+1", 110.0 + index, 100.0)
                        for index, day in enumerate(days) for m in maps])
    target = days[2]
    eex = book([(day, {"M+1": 100.0, "M+2": 200.0}) for day in days])
    original = CurveFiller(cfg, own, maps, {m.key: eex for m in maps}).run(target, target, loo=True)
    own.loc[own.date > target, "vwap"] = 1e6
    altered_eex = book([(day, {"M+1": 1e6, "M+2": 2e6} if day >= target
                         else {"M+1": 100.0, "M+2": 200.0}) for day in days])
    changed = CurveFiller(cfg, own, maps, {m.key: altered_eex for m in maps}).run(target, target, loo=True)
    pd.testing.assert_frame_equal(original.filled, changed.filled)
    pd.testing.assert_frame_equal(original.loo, changed.loo)


def test_unknown_candidate_identity_fails_before_run(cfg):
    first = mapping("North")
    with pytest.raises(ValueError, match="unmapped or inactive"):
        CurveFiller(cfg, observations([]), [first], {},
                    curve_parameters={("POWER", "wrong", "EUR/MWh"): {"tau_log": .25}})


def test_individual_target_lists_and_configuration_records_match_each_curve(cfg):
    day = date(2026, 9, 1)
    first = mapping("North", tenors=["M+1"])
    second = mapping("South", tenors=["M+2", "Q+1"])
    own = observations([(day, m, "M+1", 110.0, 100.0) for m in (first, second)])
    eex = book([(day, {"M+1": 100.0, "M+2": 200.0})])
    engine = CurveFiller(cfg, own, [first, second], {m.key: eex for m in (first, second)})
    result = engine.run(day, day)
    assert not result.errors
    assert set(result.filled.query("region == 'North'").tenor) == {"M+1"}
    assert set(result.filled.query("region == 'South'").tenor) == {"M+2", "Q+1"}
    assert result.filled.configuration_mode.eq("individual").all()
    assert result.filled.configuration_id.nunique() == 2
    for m in (first, second):
        rows = result.filled.loc[curve_keys(result.filled) == m.key]
        for column, value in engine.configuration_records[m.key].items():
            assert rows[column].eq(value).all()


def test_shape_original_permission_is_individual_and_never_changes_training(cfg):
    cfg = replace(cfg, layer_local=True, tenors=[f"M+{i}" for i in range(1, 7)] + ["Q+2"])
    days = list(pd.bdate_range("2026-09-01", periods=2).date)
    first = mapping("fixed", shape_mode="adjust", shape_adjust_originals=False,
                    shape_original_weight=1.0)
    second = mapping("movable", shape_mode="adjust", shape_adjust_originals=True,
                     shape_original_weight=1.0)
    own = observations([(days[0], m, tenor, price, 100.0) for m in (first, second)
                        for tenor, price in (("M+1", 110.0), ("Q+2", 120.0))])
    prices = dict(zip(cfg.tenors, [100, 110, 120, 150, 150, 150, 150]))
    eex = book([(day, prices) for day in days])
    result = CurveFiller(cfg, own, [first, second], {m.key: eex for m in (first, second)}).run(*days)
    assert not result.errors
    first_day = result.filled.query("reference_date == @days[0] and tenor == 'Q+2'").set_index("region")
    assert first_day.loc["fixed", "price"] == 120.0
    assert not first_day.loc["fixed", "shape_original_modified"]
    assert first_day.loc["movable", "shape_original_modified"]
    assert first_day.loc["movable", "price"] != 120.0
    next_day = result.filled.query("reference_date == @days[1] and tenor == 'Q+2'").set_index("region")
    assert next_day.loc["fixed", "basis_hist"] == pytest.approx(-30.0)
    assert next_day.loc["movable", "basis_hist"] == pytest.approx(-30.0)


@pytest.mark.parametrize("filter_parameters", [{"min_volume": 1000.0}, {"max_anchor_dev": .01}])
def test_frozen_evaluation_keeps_candidate_rejected_truth_in_exam(cfg, filter_parameters):
    cfg = replace(cfg, layer_local=True, layer_hist="off")
    m = mapping("North", **filter_parameters)
    days = list(pd.bdate_range("2026-09-01", periods=2).date)
    own = observations([(days[-1], m, "M+1", 130.0, 100.0),
                        (days[-1], m, "M+2", 230.0, 100.0)])
    eex = book([(day, {"M+1": 100.0, "M+2": 200.0}) for day in days])
    ordinary = CurveFiller(cfg, own, [m], {m.key: eex}).run(days[-1], days[-1], loo=True)
    frozen = CurveFiller(cfg, own, [m], {m.key: eex},
                         evaluation_configs={m.key: replace(cfg, min_volume=0.0, max_anchor_dev=0.0)})
    result = frozen.run(days[-1], days[-1], loo=True)
    assert not result.errors
    assert ordinary.loo.empty
    baseline = result.loo.query("method == 'eex'").set_index("tenor")
    assert baseline.loc["M+1", "own"] == 130.0
    assert baseline.loc["M+2", "own"] == 230.0
    predicted = result.loo.query("method == 'pipeline_configured'").set_index("tenor")
    # Both real observations fail the candidate's filter. Hidden observations
    # remain test truths while neither can serve as an anchor for the other.
    assert predicted.loc["M+1", "pred"] == pytest.approx(100.0)
    assert predicted.loc["M+2", "pred"] == pytest.approx(200.0)
    pd.testing.assert_frame_equal(result.filled, ordinary.filled)
