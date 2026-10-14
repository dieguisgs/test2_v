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
                   tenors=["M+1", "M+2"])


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


def test_negative_offset_individual_predictions_ignore_future_eex_and_own(cfg):
    cfg = replace(cfg, eex_offset_days=-1)
    days = list(pd.bdate_range("2026-09-01", periods=5).date)
    maps = [mapping("short"), mapping("long")]
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


@pytest.mark.parametrize("filter_parameters", [{"min_volume": 1000.0}, {"max_anchor_dev": .01}])
def test_frozen_evaluation_keeps_candidate_rejected_truth_in_exam(cfg, filter_parameters):
    cfg = replace(cfg)
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
