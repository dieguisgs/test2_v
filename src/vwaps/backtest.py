"""Backtest summaries."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from vwaps.dates import parse_reference_dates
from vwaps.identity import IDENTITY_COLUMNS, normalize_identity


def _with_identity(frame: pd.DataFrame) -> pd.DataFrame:
    identity = normalize_identity(frame)
    return frame.assign(**{column: identity[column] for column in IDENTITY_COLUMNS})


def _mean(values: pd.Series) -> float:
    """Average finite errors without overflowing an intermediate sum."""
    numbers = values.dropna().to_numpy(dtype=float)
    if not len(numbers):
        return math.nan
    if not np.isfinite(numbers).all():
        raise ValueError("Backtest errors must be finite")
    scale = float(np.abs(numbers).max())
    return scale * float(np.mean(numbers / scale)) if scale else 0.0


def _rmse(values: pd.Series) -> float:
    """Compute RMS with the same scaling convention used by tuning."""
    numbers = values.dropna().to_numpy(dtype=float)
    if not len(numbers):
        return math.nan
    if not np.isfinite(numbers).all():
        raise ValueError("Backtest errors must be finite")
    scale = float(np.abs(numbers).max())
    return scale * math.sqrt(float(np.mean((numbers / scale) ** 2))) if scale else 0.0


def _round_report(frame: pd.DataFrame) -> pd.DataFrame:
    """Round display values without NumPy's large-value decimal overflow."""
    result = frame.copy()
    for column in result.select_dtypes(include="floating"):
        result[column] = result[column].map(lambda value: round(float(value), 4))
    return result


def summarize_loo(loo: pd.DataFrame) -> pd.DataFrame:
    """Errors by unit/method/group and improvement over EEX on matching cases.

    A positive mae_improvement means the method improves on EEX. Columns
    ending in *_paired exclude cases where either prediction is unavailable.
    """
    if loo.empty:
        return loo
    loo = _with_identity(loo)
    report_keys = ["unit", "method", "group"]
    g = loo.groupby(report_keys)["error"]
    out = pd.DataFrame({
        "n": g.size(),
        "mae": g.apply(lambda e: _mean(e.abs())),
        "bias": g.apply(_mean),
        "rmse": g.apply(_rmse),
    }).reset_index()
    tot = loo.groupby(["unit", "method"])["error"]
    allg = pd.DataFrame({
        "group": "ALL", "n": tot.size(), "mae": tot.apply(lambda e: _mean(e.abs())),
        "bias": tot.apply(_mean), "rmse": tot.apply(_rmse),
    }).reset_index()
    report = pd.concat([allg, out], ignore_index=True)
    keys = ["reference_date", *IDENTITY_COLUMNS, "tenor"]
    if "scenario" in loo.columns:
        keys.append("scenario")
    baseline = loo.loc[loo["method"] == "eex", [*keys, "error"]].rename(
        columns={"error": "error_eex"})
    if baseline.duplicated(keys).any():
        raise ValueError("Backtest: multiple EEX predictions for the same observation")
    paired = loo.merge(baseline, on=keys, how="inner", validate="many_to_one")
    paired = paired.dropna(subset=["error", "error_eex"])
    paired = paired.assign(abs_error=paired["error"].abs(), abs_eex=paired["error_eex"].abs())
    paired = pd.concat([paired, paired.assign(group="ALL")], ignore_index=True)
    paired_report = paired.groupby(report_keys).agg(
        n_paired=("error", "size"), mae_paired=("abs_error", _mean),
        mae_eex_paired=("abs_eex", _mean),
    ).reset_index()
    paired_report["mae_improvement"] = paired_report["mae_eex_paired"] - paired_report["mae_paired"]
    report = report.merge(paired_report, on=report_keys, how="left", validate="one_to_one")
    report["n_paired"] = report["n_paired"].fillna(0).astype(int)
    return _round_report(report.sort_values(["unit", "group", "mae"]))


def _period_metadata(frame: pd.DataFrame, label: str) -> tuple[pd.DataFrame, bool]:
    """Normalize optional absolute periods; absent legacy metadata stays absent."""
    columns = ("delivery_start", "delivery_end")
    present = [name in frame.columns for name in columns]
    if not any(present):
        return frame, False
    if not all(present):
        raise ValueError(f"{label}: both delivery_start and delivery_end are required when period metadata is supplied")
    result = frame.copy()
    for name in columns:
        result[name] = parse_reference_dates(result[name]).dt.date
        if result[name].isna().any():
            raise ValueError(f"{label}: invalid or missing {name} in period metadata")
    if (result["delivery_start"] >= result["delivery_end"]).any():
        raise ValueError(f"{label}: delivery periods must have positive length")
    return result, True


def _original_rows(frame: pd.DataFrame) -> pd.Series:
    """Identify observed contracts even when shape changed their final origin."""
    original = frame["source"].isin(("own", "own+shape"))
    for name, value in (("data_origin", "original"), ("data_origin_before_shape", "original"),
                        ("source_before_shape", "own")):
        if name in frame:
            original = original | frame[name].eq(value)
    return original


def compare_truth(filled: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    """Score unobserved contracts against synthetic truth, by unit/source.

    Shape-adjusted originals remain observed and are excluded. With absolute
    period metadata in either input, aliases count once per physical delivery
    contract and conflicting alias prices/sources are rejected. Legacy inputs
    without period metadata retain the historical per-tenor comparison; their
    aliases cannot be resolved reliably without delivery conventions.
    """
    t = truth.rename(columns={"date": "reference_date"})
    for label, frame, required in (
        ("Synthetic truth", t, ("reference_date", "product", "tenor", "truth")),
        ("Filled curve", filled, ("reference_date", "product", "tenor", "source", "price")),
    ):
        missing = [name for name in required if name not in frame]
        if missing:
            raise ValueError(f"{label} lacks required comparison columns: {missing}")
    t = _with_identity(t)
    filled = _with_identity(filled)
    t, truth_periods = _period_metadata(t, "Synthetic truth")
    filled, filled_periods = _period_metadata(filled, "Filled curve")
    keys = ["reference_date", *IDENTITY_COLUMNS, "tenor"]
    duplicate = t.duplicated(keys, keep=False)
    if duplicate.any():
        example = t.loc[duplicate, keys].iloc[0].to_dict()
        raise ValueError(
            f"Synthetic truth has duplicate keys ({example}). Older CSV files may "
            "contain conflicting truths for W+2; regenerate synthetic data in a new "
            "directory and use the VWAPs and truth from that same generation together.")
    period_keys = ["reference_date", *IDENTITY_COLUMNS, "delivery_start", "delivery_end"]
    if truth_periods:
        if (t.groupby(period_keys)["truth"].nunique(dropna=False) > 1).any():
            raise ValueError("Inconsistent synthetic truth: aliases for one delivery period have different prices")
    if filled.duplicated(keys).any():
        raise ValueError("Filled curve has duplicate date, product, region, unit and tenor keys")
    filled = filled.assign(_observed_contract=_original_rows(filled))
    truth_columns = [*keys, "truth"] + (["delivery_start", "delivery_end"] if truth_periods else [])
    m = filled.merge(t[truth_columns], on=keys, how="inner", validate="one_to_one", suffixes=("", "_truth"))
    if truth_periods and filled_periods:
        for name in ("delivery_start", "delivery_end"):
            if not m[name].equals(m[f"{name}_truth"]):
                raise ValueError("Synthetic truth and filled curve disagree on delivery periods for matching tenor keys")
    if truth_periods or filled_periods:
        if filled_periods:
            # An observed alias may be absent from the truth's tenor labels.
            # Its matching physical period is still an observed contract.
            observed = filled.loc[filled["_observed_contract"], period_keys].drop_duplicates()
            if not observed.empty:
                matched = pd.MultiIndex.from_frame(m[period_keys]).isin(pd.MultiIndex.from_frame(observed))
                m["_observed_contract"] = m["_observed_contract"] | matched
        m["_observed_contract"] = m.groupby(period_keys)["_observed_contract"].transform("any")
    m = m[~m["_observed_contract"] & m["source"].ne("missing")].dropna(subset=["price", "truth"])
    if m.empty:
        return m
    if truth_periods or filled_periods:
        grouped = m.groupby(period_keys)
        for field in ("price", "truth", "source"):
            if (grouped[field].nunique(dropna=False) > 1).any():
                raise ValueError(f"Inconsistent synthetic comparison: aliases for one delivery period have different {field} values")
        m = m.drop_duplicates(period_keys).copy()
    m["error"] = m["price"] - m["truth"]
    if not np.isfinite(m["error"]).all():
        raise ValueError("Synthetic comparison errors must be finite")
    g = m.groupby(["unit", "source"])["error"]
    return _round_report(pd.DataFrame({
        "n": g.size(), "mae": g.apply(lambda e: _mean(e.abs())), "bias": g.apply(_mean),
    }).reset_index())
