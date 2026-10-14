"""Behavioral coverage for per-target mode selection and separate histories."""

import math
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest

from vwaps.cli import main
from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.log import get_logger
from vwaps.mapping import COLUMNS, ProductMap
from vwaps.tenors import resolve_tenor


DAY = date(2026, 9, 21)
PRODUCT = "DE_Base load"
REGION = "North"
UNIT = "EUR/MWh"


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    return replace(
        load_config(path), basis_mode="auto", tenors=["M+1", "M+2", "M+3"],
        layer_hist="off", layer_arbitrage=False, layer_correlation=False, layer_cross=False,
        warmup_days=0,
    )


def observed(day=DAY, tenor="M+1", price=110.0, product=PRODUCT):
    return {"date": day, "product": product, "region": REGION, "unit": UNIT,
            "tenor": tenor, "vwap": price, "volume": 10.0}


def settlements(schedule):
    rows = []
    for day, quotes in schedule.items():
        for tenor, price in quotes.items():
            period = resolve_tenor(tenor, day)
            rows.append((day, period.kind, period.start, price))
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def run(cfg, observations, schedule, start=DAY, end=DAY, loo=False):
    maps = [ProductMap(PRODUCT, "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin", region=REGION, unit=UNIT)]
    frame = pd.DataFrame(observations, columns=["date", "product", "region", "unit", "tenor", "vwap", "volume"])
    result = CurveFiller(cfg, frame, maps, {maps[0].key: settlements(schedule)}).run(start, end, loo=loo)
    assert not result.errors
    return result


def target(result, tenor="M+2", day=DAY, product=PRODUCT):
    selected = result.filled[
        (result.filled["reference_date"] == day)
        & (result.filled["product"] == product)
        & (result.filled["tenor"] == tenor)
    ]
    assert len(selected) == 1
    return selected.iloc[0]


@pytest.mark.parametrize("low_eex", [0.0, 0.5, -0.5])
def test_auto_selects_additive_for_small_target_and_ratio_for_normal_target(cfg, low_eex):
    schedule = {DAY: {"M+1": 100.0, "M+2": low_eex, "M+3": 120.0}}
    observations = [observed()]
    automatic = run(cfg, observations, schedule)
    additive = run(replace(cfg, basis_mode="additive"), observations, schedule)
    ratio = run(replace(cfg, basis_mode="ratio"), observations, schedule)
    low = target(automatic)
    normal = target(automatic, "M+3")
    assert low["basis_mode"] == "additive"
    assert "auto_additive_low_eex" in low["flag"]
    assert low["estimation_method"] == "additive_local"
    assert low["price"] == pytest.approx(target(additive)["price"])
    assert normal["basis_mode"] == "ratio"
    assert normal["estimation_method"] == "ratio_local"
    assert normal["price"] == pytest.approx(target(ratio, "M+3")["price"])
    assert target(automatic, "M+1")["price"] == 110.0


@pytest.mark.parametrize("anchor_eex,own_price", [
    (0.0, 5.0), (0.5, 5.0), (100.0, -10.0), (100.0, 0.0), (100.0, 400.0),
])
def test_auto_uses_additive_anchors_when_ratios_are_unusable_and_preserves_own(cfg, anchor_eex, own_price):
    schedule = {DAY: {"M+1": anchor_eex, "M+2": 120.0, "M+3": 130.0}}
    observations = [observed(price=own_price)]
    automatic = run(cfg, observations, schedule)
    additive = run(replace(cfg, basis_mode="additive"), observations, schedule)
    filled = target(automatic)
    own = target(automatic, "M+1")
    assert own["price"] == own_price and own["source"] == "own"
    assert own["data_origin"] == "original" and own["estimation_method"] == "none"
    assert filled["basis_mode"] == "additive"
    assert "auto_additive_no_ratio_anchors" in filled["flag"]
    assert filled["estimation_method"] == "additive_local"
    assert filled["price"] == pytest.approx(target(additive)["price"])


def test_both_negative_prices_are_valid_ratio_anchors(cfg):
    schedule = {DAY: {"M+1": -100.0, "M+2": -120.0, "M+3": 130.0}}
    observations = [observed(price=-110.0)]
    automatic = run(cfg, observations, schedule)
    ratio = run(replace(cfg, basis_mode="ratio"), observations, schedule)
    row = target(automatic)
    assert row["basis_mode"] == "ratio"
    assert row["estimation_method"] == "ratio_local"
    assert row["price"] == pytest.approx(target(ratio)["price"])
    assert row["price"] < -120.0


@pytest.mark.parametrize("target_eex", [-1.0, 1.0])
def test_ratio_floor_boundary_is_inclusive(cfg, target_eex):
    row = target(run(cfg, [observed()], {DAY: {"M+1": 100.0, "M+2": target_eex}}))
    assert row["basis_mode"] == "ratio"
    assert "auto_additive_low_eex" not in row["flag"]


def test_no_anchors_or_sufficient_history_leaves_target_missing_in_ratio_mode(cfg):
    row = target(run(cfg, [], {DAY: {"M+2": 120.0}}))
    assert row["basis_mode"] == "ratio"
    assert math.isnan(row["price"]) and row["source"] == "missing"
    assert row["eex_settle"] == 120.0
    assert row["estimation_method"] == "unavailable"
    assert row["data_origin"] == "missing"
    assert "eex_fallback_unavailable" in row["flag"]


@pytest.mark.parametrize("hist_layer,max_age,expected_mode", [
    ("on", 60, "additive"), ("auto", 60, "additive"),
    ("off", 60, "ratio"), ("on", 1, "ratio"),
])
def test_additive_only_history_is_used_only_when_available_and_enabled(cfg, hist_layer, max_age, expected_mode):
    later = DAY + timedelta(days=3)
    cfg = replace(cfg, layer_hist=hist_layer, hist_max_age_days=max_age)
    schedule = {DAY: {"M+1": 0.5, "M+2": 100.0}, later: {"M+2": 120.0}}
    row = target(run(cfg, [observed(price=10.0)], schedule, later, later), day=later)
    assert row["basis_mode"] == expected_mode
    if expected_mode == "additive":
        assert row["price"] == pytest.approx(129.5)
        assert row["estimation_method"] == "additive_history"
        assert "auto_additive_history_only" in row["flag"]
    else:
        assert math.isnan(row["price"]) and math.isnan(row["basis_hist"])
        assert row["source"] == "missing"
        assert row["estimation_method"] == "unavailable"
        assert "auto_additive_history_only" not in row["flag"]
        assert "eex_fallback_unavailable" in row["flag"]


def test_ratio_history_is_not_overwritten_by_additive_only_observations(cfg):
    middle, last = DAY + timedelta(days=1), DAY + timedelta(days=2)
    cfg = replace(cfg, layer_hist="on")
    observations = [observed(), observed(middle, price=-10.0)]
    schedule = {
        DAY: {"M+1": 100.0}, middle: {"M+1": 100.0},
        last: {"M+2": 120.0, "M+3": 0.5},
    }
    result = run(cfg, observations, schedule, last, last)
    ratio = target(result, day=last)
    additive = target(result, "M+3", last)
    alpha = 1 - 0.5 ** (1 / cfg.ewma_halflife_days)
    expected_additive = alpha * -110.0 + (1 - alpha) * 10.0
    assert ratio["basis_mode"] == "ratio"
    assert ratio["basis_hist"] == pytest.approx(0.1)
    assert ratio["price"] == pytest.approx(132.0)
    assert additive["basis_mode"] == "additive"
    assert additive["basis_hist"] == pytest.approx(expected_additive)
    assert additive["price"] == pytest.approx(0.5 + expected_additive)


def test_auto_history_skill_evaluates_ratio_and_additive_independently(cfg):
    middle, last = DAY + timedelta(days=1), DAY + timedelta(days=2)
    cfg = replace(cfg, layer_hist="auto", hist_auto_min_obs=1)
    observations = [observed(price=20.0), observed(middle, price=1010.0)]
    schedule = {
        DAY: {"M+1": 10.0}, middle: {"M+1": 1000.0},
        last: {"M+2": 100.0, "M+3": 0.5},
    }
    result = run(cfg, observations, schedule, last, last)
    normal = target(result, day=last)
    additive = target(result, "M+3", last)
    ratio = target(run(replace(cfg, basis_mode="ratio"), observations, schedule, last, last), day=last)
    # The historical ratio badly overpredicts day two, while the additive
    # difference remains exactly ten. The two policies must reach different decisions.
    assert ratio["basis_mode"] == "ratio"
    assert math.isnan(ratio["basis_hist"]) and math.isnan(ratio["price"])
    assert ratio["source"] == "missing"
    assert "eex_fallback_unavailable" in ratio["flag"]
    assert normal["basis_mode"] == "additive"
    assert normal["price"] == pytest.approx(110.0)
    assert "auto_additive_history_only" in normal["flag"]
    assert additive["basis_mode"] == "additive"
    assert additive["basis_hist"] == pytest.approx(10.0)
    assert additive["price"] == pytest.approx(10.5)


@pytest.mark.parametrize("hist_layer", ["on", "auto"])
def test_daily_replays_same_history_as_refill_through_auto_mode_transitions(cfg, hist_layer):
    days = [DAY + timedelta(days=i) for i in range(4)]
    cfg = replace(cfg, layer_hist=hist_layer)
    observations = [observed(days[0]), observed(days[1], price=-10.0)]
    schedule = {
        day: {"M+1": 100.0, "M+2": 0.5 if i == 2 else 120.0, "M+3": 130.0}
        for i, day in enumerate(days)
    }
    complete = run(cfg, observations, schedule, days[0], days[-1]).filled
    assert set(complete["basis_mode"]) == {"ratio", "additive"}
    for day in days:
        daily = run(cfg, observations, schedule, day, day).filled.reset_index(drop=True)
        expected = complete[complete["reference_date"] == day].reset_index(drop=True)
        pd.testing.assert_frame_equal(daily, expected)


def test_configured_loo_selects_mode_after_removing_hidden_anchor(cfg):
    observations = [observed(), observed(tenor="M+2", price=-20.0)]
    schedule = {DAY: {"M+1": 100.0, "M+2": 120.0, "M+3": 130.0}}
    result = run(cfg, observations, schedule, loo=True)
    predictions = result.loo[result.loo["method"] == "pipeline_configured"].set_index("tenor")
    for hidden in ("M+1", "M+2"):
        visible = [row for row in observations if row["tenor"] != hidden]
        independently_hidden = target(run(cfg, visible, schedule), hidden)
        assert predictions.loc[hidden, "pred"] == pytest.approx(independently_hidden["price"])
        expected_mode = "additive" if hidden == "M+1" else "ratio"
        assert independently_hidden["basis_mode"] == expected_mode


def test_cross_adjustments_use_the_effective_modes_units(cfg):
    helper = "FR_Base load"
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(8)]
    deltas = [0.0, 4.0, -3.0, 8.0, -2.0, 5.0, 9.0, 15.0]
    cfg = replace(
        cfg, layer_hist="on", layer_cross=True, cross_min_obs=2, cross_min_corr=0.1,
        cross_halflife_days=3, ewma_halflife_days=3,
    )
    observations = [observed(day, price=100.0 + delta) for day, delta in zip(days[:-1], deltas[:-1])]
    observations += [observed(day, price=200.0 + 2 * delta, product=helper) for day, delta in zip(days, deltas)]
    frame = pd.DataFrame(observations)
    maps = [
        ProductMap(PRODUCT, "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin", region=REGION, unit=UNIT),
        ProductMap(helper, "helper", "FR", "Base", "helper.csv", "Base", "Europe/Paris", region=REGION, unit=UNIT),
    ]
    books = {
        maps[0].key: settlements({day: {"M+1": 100.0, "M+2": 120.0, "M+3": 0.5} for day in days}),
        maps[1].key: settlements({day: {"M+1": 200.0} for day in days}),
    }
    results = {}
    for mode in ("auto", "ratio", "additive"):
        result = CurveFiller(replace(cfg, basis_mode=mode), frame, maps, books).run(days[-1], days[-1])
        assert not result.errors
        results[mode] = result
    for tenor, mode in (("M+2", "ratio"), ("M+3", "additive")):
        automatic = target(results["auto"], tenor, days[-1])
        explicit = target(results[mode], tenor, days[-1])
        assert automatic["basis_mode"] == mode
        assert automatic["source"] == explicit["source"] == "eex+cross"
        assert automatic["estimation_method"].startswith(f"{mode}_")
        assert automatic["cross_adj"] == pytest.approx(explicit["cross_adj"])
        assert automatic["price"] == pytest.approx(explicit["price"])


def test_cli_defaults_to_auto_when_method_is_not_configured(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[paths]\nvwap_input = "input.csv"\nmapping = "products.csv"\n'
        'eex_curves_dir = "."\noutput_dir = "output"\n'
        '[targets]\ntenors = ["M+1", "M+2"]\n',
        encoding="utf-8",
    )
    assert load_config(config).basis_mode == "auto"
    pd.DataFrame([{
        "reference_date": DAY.isoformat(), "product": PRODUCT, "tenor": "M+1",
        "vwap": 5.0, "volume": 10.0, "region": REGION, "unit": UNIT,
    }]).to_csv(tmp_path / "input.csv", index=False)
    pd.DataFrame([
        dict(product=PRODUCT, use="fill", area="DE", profile="Base", eex_file="eex.csv", hours="Base",
             timezone="Europe/Berlin", comment="", region=REGION, unit=UNIT),
    ], columns=COLUMNS).to_csv(tmp_path / "products.csv", index=False)
    pd.DataFrame([
        (DAY, "Month", date(2026, 10, 1), 0.5),
        (DAY, "Month", date(2026, 11, 1), 100.0),
    ], columns=REQUIRED).to_csv(tmp_path / "eex.csv", index=False)
    logger = get_logger()
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    try:
        assert main(["daily", "--date", DAY.isoformat()], config) == 0
    finally:
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
    enriched = pd.read_csv(tmp_path / "output" / "enriched_history.csv")
    filled = enriched[enriched["curve_tenor"] == "M+2"].iloc[0]
    assert filled["curve_basis_mode"] == "additive"
    assert filled["data_origin"] == "estimated"
    assert filled["estimation_method"] == "additive_local"
    assert 100.0 < filled["curve_price"] < 104.5
