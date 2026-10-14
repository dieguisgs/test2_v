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
                   basis_mode="auto", tenors=["M+1", "M+2"], layer_hist="off",
                   layer_cross=False, layer_correlation=False, layer_arbitrage=False,
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


def test_same_product_curves_keep_separate_books_anchors_and_loo(cfg):
    maps = [mapping(), mapping("South"), mapping(unit="GBP/MWh")]
    prices = [100.0, 200.0, 300.0]
    frame = own([observation(m, price=price + 10.0) for m, price in zip(maps, prices)])
    # The hidden sole anchor must use its own book's price history.
    publications = list(pd.bdate_range(end=DAY, periods=cfg.fallback_price_window).date)
    books = {m.key: book([(day, {"M+1": price, "M+2": price}) for day in publications])
             for m, price in zip(maps, prices)}
    result = CurveFiller(cfg, frame, maps, books).run(DAY, DAY, loo=True)
    assert not result.errors
    isolated_filled, isolated_loo = [], []
    for m in maps:
        isolated = CurveFiller(cfg, frame.loc[curve_keys(frame) == m.key], [m], books).run(DAY, DAY, loo=True)
        isolated_filled.append(isolated.filled)
        isolated_loo.append(isolated.loo)
    pd.testing.assert_frame_equal(sorted_output(result.filled),
                                  sorted_output(pd.concat(isolated_filled, ignore_index=True)))
    sort_loo = ["reference_date", *IDENTITY_COLUMNS, "tenor", "method"]
    pd.testing.assert_frame_equal(result.loo.sort_values(sort_loo).reset_index(drop=True),
                                  pd.concat(isolated_loo).sort_values(sort_loo).reset_index(drop=True))
    assert set(curve_keys(result.filled)) == {m.key for m in maps}
    deployed = result.loo[result.loo.method == "pipeline_configured"]
    assert list(deployed.sort_values("pred").pred) == pytest.approx(prices)
    report = summarize_loo(result.loo)
    assert set(report.unit) == {"EUR/MWh", "GBP/MWh"}


def test_same_product_histories_and_daily_replay_are_isolated(cfg):
    cfg = replace(cfg, layer_hist="on", layer_local=False)
    maps = [mapping(), mapping("South"), mapping(unit="GBP/MWh")]
    next_day = DAY + timedelta(days=1)
    frame = own([observation(m, price=price) for m, price in zip(maps, [110.0, 120.0, 130.0])])
    books = {m.key: book([(day, {"M+1": 100.0, "M+2": 100.0}) for day in (DAY, next_day)])
             for m in maps}
    engine = CurveFiller(cfg, frame, maps, books)
    refill = engine.run(DAY, next_day)
    daily = engine.run(next_day, next_day)
    assert not refill.errors and not daily.errors
    expected = refill.filled[refill.filled.reference_date == next_day]
    pd.testing.assert_frame_equal(sorted_output(daily.filled), sorted_output(expected))
    output = daily.filled[daily.filled.tenor == "M+2"].set_index(IDENTITY_COLUMNS)
    for m, expected_price in zip(maps, [110.0, 120.0, 130.0]):
        assert output.loc[m.key, "price"] == pytest.approx(expected_price)
        assert output.loc[m.key, "source"] == "eex+hist"


def test_blank_region_is_literal_and_nonblank_curve_cannot_use_product_book_fallback(cfg):
    blank, north = mapping(""), mapping("North")
    frame = own([observation(north, price=120.0)])
    publications = list(pd.bdate_range(end=DAY, periods=cfg.fallback_price_window).date)
    quotes = book([(day, {"M+1": 100.0, "M+2": 100.0}) for day in publications])
    result = CurveFiller(cfg, frame, [blank, north], {blank.key: quotes, north.key: quotes}).run(DAY, DAY)
    first = result.filled[result.filled.tenor == "M+1"].set_index("region")
    assert first.loc["", "price"] == pytest.approx(100.0)
    assert first.loc["", "source"] == "eex+smooth"
    assert first.loc["", "estimation_method"] == "eex_price_ewma"
    assert first.loc["North", "price"] == 120.0
    unsupported = CurveFiller(cfg, frame, [north], {north.product: quotes}).run(DAY, DAY)
    output = unsupported.filled.set_index("tenor")
    assert output.loc["M+1", "source"] == "own"
    assert output.loc["M+2", "source"] == "missing"


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


def test_cross_relations_distinguish_same_product_regions_and_units(cfg):
    cfg = replace(cfg, basis_mode="additive", tenors=["M+1"], layer_hist="on",
                  layer_cross=True, cross_min_obs=1.0)
    target, helper = mapping(), mapping("South", "GBP/MWh")
    days = [DAY + timedelta(days=i) for i in range(4)]
    target_schedule = [(day, {"M+1": 80.0 * step}) for step, day in enumerate(days, 1)]
    helper_schedule = [(day, {"M+1": 160.0 * step}) for step, day in enumerate(days, 1)]
    rows = [observation(target, day, price=90.0 * step) for step, day in enumerate(days[:-1], 1)]
    rows += [observation(helper, day, price=180.0 * step) for step, day in enumerate(days, 1)]
    engine = CurveFiller(cfg, own(rows), [target, helper],
                         {target.key: book(target_schedule), helper.key: book(helper_schedule)})
    result = engine.run(days[-1], days[-1])
    assert not result.errors
    output = result.filled.set_index(IDENTITY_COLUMNS)
    assert output.loc[target.key, "source"] == "eex+cross"
    assert output.loc[target.key, "price"] == pytest.approx(360.0)
    assert helper.label in output.loc[target.key, "cross_from"]
    assert output.loc[helper.key, "price"] == 720.0
    assert engine.cross["additive", target.key, helper.key, "month"].beta == pytest.approx(0.5)
    assert ("additive", helper.key, target.key, "month") in engine.cross


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


def test_consistency_never_uses_parts_from_another_curve(cfg):
    cfg = replace(cfg, tenors=["M+1", "M+2", "M+3", "Q+1"])
    maps = [mapping(), mapping(unit="GBP/MWh")]
    publications = list(pd.bdate_range(
        end=DAY, periods=max(cfg.fallback_price_window, cfg.fallback_spread_window)).date)
    books = {m.key: book([(day, {"M+1": price, "M+2": price, "M+3": price, "Q+1": price * 1.01})
                         for day in publications])
             for m, price in zip(maps, [100.0, 200.0])}
    result = CurveFiller(cfg, own([]), maps, books).run(DAY, DAY)
    assert not result.errors
    assert result.filled["source"].eq("eex+smooth").all()
    combined = pd.DataFrame(check_day(result.filled.to_dict("records"), hours_fn("Base", "Europe/Berlin")))
    for report in (result.consistency, combined):
        output = report.set_index(IDENTITY_COLUMNS)
        assert output.loc[maps[0].key, "deviation"] == pytest.approx(1.0)
        assert output.loc[maps[1].key, "deviation"] == pytest.approx(2.0)


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
