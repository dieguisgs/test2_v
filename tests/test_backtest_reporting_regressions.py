"""Reporting scope, alias counting and finite-error arithmetic regressions."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from vwaps.backtest import compare_truth, summarize_loo


DAY = date(2026, 9, 29)
IDENTITY = dict(product="P", region="DE", unit="EUR/MWh")


def _case(with_periods=True):
    common = dict(reference_date=DAY, **IDENTITY, source="eex+local")
    filled = pd.DataFrame([
        dict(**common, tenor="D+1", price=108.0,
             delivery_start=date(2026, 9, 30), delivery_end=date(2026, 10, 1)),
        dict(**common, tenor="M+1", price=112.0,
             delivery_start=date(2026, 10, 1), delivery_end=date(2026, 11, 1)),
    ])
    truth = filled.rename(columns={"reference_date": "date"}).drop(columns=["source", "price"])
    truth = truth.assign(truth=[110.0, 112.0])
    if not with_periods:
        filled = filled.drop(columns=["delivery_start", "delivery_end"])
        truth = truth.drop(columns=["delivery_start", "delivery_end"])
    return filled, truth


@pytest.mark.parametrize("provenance", [
    dict(source="own"),
    dict(source="own+shape", data_origin="estimated"),
    dict(source="transformed", data_origin_before_shape="original"),
    dict(source="transformed", source_before_shape="own"),
    dict(source="transformed", data_origin="original"),
])
def test_original_provenance_excludes_observed_prices_after_transformation(provenance):
    filled, truth = _case()
    for key, value in provenance.items():
        filled.loc[0, key] = value
    report = compare_truth(filled, truth)
    assert report["n"].sum() == 1
    assert report.iloc[0]["source"] == "eex+local"
    assert report.iloc[0]["mae"] == 0.0


@pytest.mark.parametrize("metadata_in", ["both", "filled", "truth"])
def test_physical_aliases_count_once_when_either_input_has_periods(metadata_in):
    filled, truth = _case()
    filled = pd.concat([filled, filled.iloc[[0]].assign(tenor="BOM")], ignore_index=True)
    truth = pd.concat([truth, truth.iloc[[0]].assign(tenor="BOM")], ignore_index=True)
    if metadata_in == "filled":
        truth = truth.drop(columns=["delivery_start", "delivery_end"])
    elif metadata_in == "truth":
        filled = filled.drop(columns=["delivery_start", "delivery_end"])
    before_filled, before_truth = filled.copy(deep=True), truth.copy(deep=True)
    report = compare_truth(filled, truth)
    assert report.iloc[0]["n"] == 2
    assert report.iloc[0]["mae"] == 1.0
    reversed_report = compare_truth(filled.iloc[::-1], truth.iloc[::-1])
    pd.testing.assert_frame_equal(report, reversed_report)
    pd.testing.assert_frame_equal(filled, before_filled)
    pd.testing.assert_frame_equal(truth, before_truth)


@pytest.mark.parametrize("field,value", [("price", 109.0), ("source", "eex+hist")])
def test_conflicting_prediction_aliases_are_rejected(field, value):
    filled, truth = _case()
    alias = filled.iloc[[0]].assign(tenor="BOM", **{field: value})
    filled = pd.concat([filled, alias], ignore_index=True)
    truth = pd.concat([truth, truth.iloc[[0]].assign(tenor="BOM")], ignore_index=True)
    with pytest.raises(ValueError, match=f"aliases.*different {field}"):
        compare_truth(filled, truth)


def test_truth_alias_conflict_is_detected_using_filled_period_metadata():
    filled, truth = _case()
    filled = pd.concat([filled, filled.iloc[[0]].assign(tenor="BOM")], ignore_index=True)
    truth = pd.concat([truth, truth.iloc[[0]].assign(tenor="BOM", truth=120.0)], ignore_index=True)
    truth = truth.drop(columns=["delivery_start", "delivery_end"])
    with pytest.raises(ValueError, match="aliases.*different truth"):
        compare_truth(filled, truth)


def test_observed_alias_excludes_period_even_if_observed_label_has_no_truth_row():
    filled, truth = _case()
    observed = filled.iloc[[0]].assign(tenor="BOM", source="own+shape", price=115.0)
    filled = pd.concat([filled, observed], ignore_index=True)
    report = compare_truth(filled, truth)
    assert report.iloc[0]["n"] == 1
    assert report.iloc[0]["mae"] == 0.0


def test_observed_alias_excludes_period_using_truth_only_metadata():
    filled, truth = _case()
    filled = pd.concat([filled, filled.iloc[[0]].assign(tenor="BOM", source="own+shape")], ignore_index=True)
    filled = filled.drop(columns=["delivery_start", "delivery_end"])
    truth = pd.concat([truth, truth.iloc[[0]].assign(tenor="BOM")], ignore_index=True)
    assert compare_truth(filled, truth).iloc[0]["n"] == 1


def test_legacy_without_period_metadata_retains_explicit_per_tenor_counting():
    filled, truth = _case(with_periods=False)
    filled = pd.concat([filled, filled.iloc[[0]].assign(tenor="BOM")], ignore_index=True)
    truth = pd.concat([truth, truth.iloc[[0]].assign(tenor="BOM")], ignore_index=True)
    report = compare_truth(filled, truth)
    assert report.iloc[0]["n"] == 3
    assert report.iloc[0]["mae"] == pytest.approx(1.3333)


def test_csv_string_periods_match_engine_date_periods():
    filled, truth = _case()
    for name in ("delivery_start", "delivery_end"):
        truth[name] = truth[name].astype(str)
    assert compare_truth(filled, truth).iloc[0]["n"] == 2


def test_mismatched_delivery_periods_are_rejected_before_scoring():
    filled, truth = _case()
    truth.loc[0, "delivery_start"] = date(2026, 9, 28)
    with pytest.raises(ValueError, match="disagree on delivery periods"):
        compare_truth(filled, truth)


@pytest.mark.parametrize("bad", ["incomplete", "invalid", "missing", "reversed"])
def test_present_but_invalid_period_metadata_does_not_silently_use_legacy_path(bad):
    filled, truth = _case()
    if bad == "incomplete":
        truth = truth.drop(columns="delivery_end")
    elif bad == "invalid":
        truth.loc[0, "delivery_start"] = "not-a-date"
    elif bad == "missing":
        truth.loc[0, "delivery_start"] = None
    else:
        truth.loc[0, "delivery_start"] = date(2026, 11, 1)
    with pytest.raises(ValueError, match="metadata|positive length"):
        compare_truth(filled, truth)


@pytest.mark.parametrize("magnitude", [1e200, 1e308])
def test_large_finite_errors_have_finite_mean_rmse_and_display_rounding(magnitude):
    rows = [dict(reference_date=DAY, **IDENTITY, group="month", tenor=tenor,
                 method=method, error=error)
            for tenor in ("M+1", "M+2")
            for method, error in (("eex", magnitude), ("pipeline_configured", magnitude / 2))]
    report = summarize_loo(pd.DataFrame(rows)).set_index(["method", "group"])
    for group in ("month", "ALL"):
        baseline, model = report.loc[("eex", group)], report.loc[("pipeline_configured", group)]
        assert baseline["rmse"] == baseline["mae"] == baseline["mae_paired"] == magnitude
        assert model["rmse"] == model["mae"] == model["bias"] == magnitude / 2
        assert model["mae_eex_paired"] == magnitude
        assert model["mae_improvement"] == magnitude / 2
    assert np.isfinite(report.select_dtypes(include="number").to_numpy()).all()


def test_large_opposite_errors_cancel_bias_without_overflow():
    rows = [dict(reference_date=DAY, **IDENTITY, group="month", tenor=tenor,
                 method="eex", error=error)
            for tenor, error in (("M+1", 1e308), ("M+2", -1e308))]
    report = summarize_loo(pd.DataFrame(rows))
    assert (report["bias"] == 0.0).all()
    assert (report["rmse"] == 1e308).all()


def test_synthetic_report_uses_stable_finite_error_means():
    filled, truth = _case()
    filled["price"] = 1e308
    truth["truth"] = 0.0
    report = compare_truth(filled, truth)
    assert report.iloc[0]["mae"] == report.iloc[0]["bias"] == 1e308


def test_nonrepresentable_error_is_rejected_instead_of_printing_infinite_accuracy():
    filled, truth = _case()
    filled["price"] = 1e308
    truth["truth"] = -1e308
    with pytest.raises(ValueError, match="errors must be finite"):
        compare_truth(filled, truth)


@pytest.mark.parametrize("value", [45931, 20260928, "45931", "2026-09-30T00:00:00Z"])
@pytest.mark.parametrize("input_name", ["filled", "truth"])
def test_comparison_period_dates_share_numeric_and_timezone_rejection(value, input_name):
    filled, truth = _case()
    target = filled if input_name == "filled" else truth
    target.loc[0, "delivery_start"] = value
    with pytest.raises(ValueError, match="invalid or missing delivery_start"):
        compare_truth(filled, truth)


@pytest.mark.parametrize("input_name,column", [
    ("filled", "source"), ("filled", "price"), ("filled", "reference_date"),
    ("truth", "truth"), ("truth", "date"), ("truth", "tenor"),
])
def test_public_comparison_reports_missing_columns_with_context(input_name, column):
    filled, truth = _case()
    if input_name == "filled":
        filled = filled.drop(columns=column)
    else:
        truth = truth.drop(columns=column)
    with pytest.raises(ValueError, match="lacks required comparison columns"):
        compare_truth(filled, truth)
