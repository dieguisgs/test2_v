"""Tuning holds EEX availability fixed and checks the same causal baseline."""

from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tuning import _candidates, _check_holdout_coverage, _paired, tune_parameters


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    cfg = replace(load_config(path), tenors=["M+1", "M+2"], layer_hist="off", basis_mode="additive")
    mapping = ProductMap("P", "fill", "DE", "Base", "eex.csv", "Base", "Europe/Berlin",
                         region="DE", unit="EUR/MWh")
    days = list(pd.bdate_range("2026-09-21", periods=5).date)
    own = pd.DataFrame([
        dict(date=day, product="P", region="DE", unit="EUR/MWh", tenor=f"M+{month}",
             vwap=100 * month + i + 10, volume=100)
        for i, day in enumerate(days) for month in (1, 2)
    ])
    publications = [date(2026, 9, 18), *days]
    book = EexBook(pd.DataFrame([
        (day, "Month", date(2026, 9 + month, 1), 100 * month + i)
        for i, day in enumerate(publications) for month in (1, 2)
    ], columns=REQUIRED))
    return cfg, own, [mapping], {mapping.key: book}, days


def test_holdout_coverage_respects_cutoff_and_actual_age(dataset):
    cfg, own, maps, books, days = dataset
    delayed = replace(cfg, eex_offset_days=-1, max_stale_days=2)
    # Monday's cutoff is Sunday; Friday's quote is three days old at reference Monday.
    with pytest.raises(ValueError, match="No holdout observations"):
        _check_holdout_coverage(delayed, own, maps, books, [days[0]])
    _check_holdout_coverage(replace(delayed, max_stale_days=3), own, maps, books, [days[0]])
    _check_holdout_coverage(replace(cfg, max_stale_days=0), own, maps, books, [days[0]])


@pytest.mark.parametrize("extended", [False, True])
def test_eex_offset_is_not_a_tunable_model_parameter(dataset, extended):
    with pytest.raises(ValueError, match="Unsupported tuning parameters"):
        _candidates(dataset[0], {"eex_offset_days": [0, -1]}, 2, extended_grid=extended)


def test_tuning_records_fixed_offset_and_uses_its_baseline(dataset):
    cfg, own, maps, books, days = dataset
    captured = []
    def observer(event, payload):
        if event == "calibration_evaluated":
            captured.append(payload["available"])
    result = tune_parameters(replace(cfg, eex_offset_days=-1), own, maps, books, days[0], days[-1],
                             {"basis_mode": ["additive"]}, 1, 1, observer=observer)
    first = captured[0].loc[captured[0].reference_date.eq(days[0])].set_index("tenor")
    assert first.loc["M+1", "eex_pred"] == 100
    assert first.loc["M+2", "eex_pred"] == 200
    assert first.loc["M+1", "eex_asof"] == date(2026, 9, 18)
    assert first.loc["M+1", "eex_cutoff_date"] == date(2026, 9, 20)
    assert first.loc["M+1", "eex_offset_days"] == -1
    assert result.metadata["eex_offset_days"] == -1
    assert result.metadata["eex_staleness_origin"] == "reference_date"
    assert "eex_offset_days" not in result.selected_config


def test_paired_predictions_retain_available_baseline_policy_fields():
    day = date(2026, 9, 21)
    key = dict(reference_date=day, product="P", region="DE", unit="EUR/MWh", tenor="M+1", own=110)
    baseline = dict(**key, method="eex", pred=100, eex_asof=date(2026, 9, 18),
                    eex_cutoff_date=date(2026, 9, 20), eex_offset_days=-1)
    model = dict(**key, method="pipeline_configured", pred=109)
    paired = _paired(pd.DataFrame([baseline, model]), [day], "test")
    assert paired.loc[0, "eex_asof"] == date(2026, 9, 18)
    assert paired.loc[0, "eex_cutoff_date"] == date(2026, 9, 20)
    assert paired.loc[0, "eex_offset_days"] == -1
    assert paired.loc[0, "error_model"] == -1
    assert paired.loc[0, "error_eex"] == -10


def test_changing_holdout_truth_does_not_change_selection_under_offset(dataset):
    cfg, own, maps, books, days = dataset
    cfg = replace(cfg, eex_offset_days=-1)
    args = (maps, books, days[0], days[-1], {"basis_mode": ["ratio", "additive"]}, 1, 2)
    before = tune_parameters(cfg, own, *args)
    changed = own.copy()
    changed.loc[changed.date.eq(days[-1]), "vwap"] += 20
    after = tune_parameters(cfg, changed, *args)
    assert before.selected_config == after.selected_config
    pd.testing.assert_frame_equal(before.calibration_report, after.calibration_report)
