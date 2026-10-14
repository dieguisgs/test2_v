from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from vwaps.backtest import compare_truth, summarize_loo
from vwaps.config import load_config
from vwaps.consistency import check_day
from vwaps.fill import CurveFiller
from vwaps.hours import hours_fn
from vwaps.identity import IDENTITY_COLUMNS, curve_keys
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.synthetic import OBS_PROB, make_synthetic
from vwaps.tenors import resolve_tenor


DAY = date(2026, 9, 21)


@pytest.fixture
def cfg():
    return replace(load_config(Path(__file__).resolve().parents[1] / "config.toml"),
                   tenors=["M+1", "M+2"], layer_arbitrage=False,
                   warmup_days=0, min_volume=0, max_anchor_dev=0)


def mapping(region="North", unit="EUR/MWh"):
    return ProductMap("SHARED", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin",
                      region=region, unit=unit)


def own(rows):
    return pd.DataFrame(rows, columns=["date", *IDENTITY_COLUMNS, "tenor", "vwap", "volume"])


def observation(m, day=DAY, tenor="M+1", price=110.0):
    return (day, m.product, m.region, m.unit, tenor, price, 20.0)


def book(schedule):
    rows = []
    for day, prices in schedule:
        for tenor, price in prices.items():
            per = resolve_tenor(tenor, day)
            rows.append((day, per.kind, per.start, price))
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def sorted_output(frame):
    return frame.sort_values(["reference_date", *IDENTITY_COLUMNS, "tenor"]).reset_index(drop=True)


def test_enabled_but_unassigned_mapping_does_not_enter_the_engine(cfg):
    unassigned = replace(mapping(), eex_file="")
    frame = own([observation(unassigned)])
    quotes = book([(DAY, {"M+1": 100.0, "M+2": 100.0})])
    result = CurveFiller(cfg, frame, [unassigned], {unassigned.key: quotes}).run(DAY, DAY, loo=True)
    assert result.filled.empty and result.consistency.empty and result.loo.empty
    assert not result.errors


def test_assigned_curve_without_available_book_retains_own_and_contract_reconstruction(cfg):
    cfg = replace(cfg, layer_arbitrage=True, tenors=["M+1", "M+2", "M+3", "Q+1"])
    assigned = mapping()
    frame = own([observation(assigned, tenor=f"M+{i}", price=100.0) for i in (1, 2, 3)])
    result = CurveFiller(cfg, frame, [assigned], {assigned.key: None}).run(DAY, DAY)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert list(output.loc[["M+1", "M+2", "M+3"], "source"]) == ["own"] * 3
    assert output.loc["Q+1", "source"] == "arbitrage"
    assert output.loc["Q+1", "price"] == 100.0


def test_errors_retain_full_curve_identity(cfg, monkeypatch):
    first, second = mapping(), mapping("South")
    quotes = book([(DAY, {"M+1": 100.0, "M+2": 100.0})])
    engine = CurveFiller(cfg, own([]), [first, second], {first.key: quotes, second.key: quotes})
    prepare = engine._prep

    def fail_one(series, day):
        if series.key == second.key:
            raise ValueError("bad curve")
        return prepare(series, day)

    monkeypatch.setattr(engine, "_prep", fail_one)
    result = engine.run(DAY, DAY)
    assert result.errors == [{"reference_date": DAY, "product": second.product,
                              "region": second.region, "unit": second.unit, "phase": "prepare"}]
    assert set(curve_keys(result.filled)) == {first.key}


def test_backtest_pairs_full_identity_and_keeps_error_units_separate():
    maps = [mapping(), mapping("South"), mapping(unit="GBP/MWh")]
    rows = []
    for m, baseline, prediction in zip(maps, [10.0, 100.0, 1000.0], [1.0, 20.0, 200.0]):
        for method, error in (("eex", baseline), ("pipeline_configured", prediction)):
            rows.append(dict(reference_date=DAY, product=m.product, region=m.region, unit=m.unit,
                             tenor="M+1", group="month", method=method, error=error))
    report = summarize_loo(pd.DataFrame(rows)).set_index(["unit", "method", "group"])
    euros = report.loc["EUR/MWh", "pipeline_configured", "ALL"]
    pounds = report.loc["GBP/MWh", "pipeline_configured", "ALL"]
    assert euros.n_paired == 2 and euros.mae_eex_paired == 55.0
    assert euros.mae == 10.5 and euros.mae_improvement == 44.5
    assert pounds.n_paired == 1 and pounds.mae_improvement == 800.0


def test_truth_comparison_matches_identity_without_merging_currencies():
    maps = [mapping(), mapping("South"), mapping(unit="GBP/MWh")]
    truth, filled = [], []
    period = resolve_tenor("M+1", DAY)
    for m, actual, estimated in zip(maps, [100.0, 200.0, 1000.0], [98.0, 190.0, 900.0]):
        identity = dict(product=m.product, region=m.region, unit=m.unit, tenor="M+1")
        truth.append(dict(date=DAY, **identity, delivery_start=period.start, delivery_end=period.end,
                          truth=actual))
        filled.append(dict(reference_date=DAY, **identity, source="eex+local", price=estimated))
    report = compare_truth(pd.DataFrame(filled), pd.DataFrame(truth)).set_index("unit")
    assert report.loc["EUR/MWh", "n"] == 2
    assert report.loc["EUR/MWh", "mae"] == 6.0
    assert report.loc["GBP/MWh", "n"] == 1
    assert report.loc["GBP/MWh", "mae"] == 100.0


def test_synthetic_uses_identity_specific_books_region_and_unit(cfg, monkeypatch):
    cfg = replace(cfg, tenors=["D+1"])
    maps = [mapping(), mapping("South"), mapping(unit="GBP/MWh")]
    books = {m.key: book([(DAY, {"D+1": price})]) for m, price in zip(maps, [100.0, 200.0, 300.0])}
    monkeypatch.setitem(OBS_PROB, "Day", 1.0)
    observations, truth = make_synthetic(cfg, maps, books, DAY, DAY, p_empty_day=0)
    assert set(curve_keys(observations)) == {m.key for m in maps}
    assert set(curve_keys(truth)) == {m.key for m in maps}
    assert not truth.duplicated(["date", *IDENTITY_COLUMNS, "tenor"]).any()
    actual = truth.set_index(IDENTITY_COLUMNS)
    observed = observations.set_index(IDENTITY_COLUMNS)
    for m, expected in zip(maps, [100.0, 200.0, 300.0]):
        assert actual.loc[m.key, "eex"] == expected
        assert observed.loc[m.key, "vwap"] == actual.loc[m.key, "truth"]
