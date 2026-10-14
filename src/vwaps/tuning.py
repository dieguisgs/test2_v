"""Select a bounded parameter grid on earlier dates and evaluate one holdout.

Selection first maximizes prediction coverage, then compares each curve's
model/EEX MAE ratio on observations predicted by every candidate, with equal
curve weight. Absolute errors are reported separately by unit. This module
never writes files or changes the caller's configuration or observations.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date
from itertools import product

import numpy as np
import pandas as pd

from vwaps.config import Config, validate_config
from vwaps.fill import CurveFiller
from vwaps.hours import hours_fn
from vwaps.identity import CurveKey, IDENTITY_COLUMNS, curve_keys, normalize_identity
from vwaps.io_eex import EexBook
from vwaps.mapping import ProductMap
from vwaps.product_config import MODEL_PARAMETER_FIELDS, effective_config
from vwaps.pricer import Pricer
from vwaps.tenors import resolve_tenor


TUNABLE_FIELDS = (
    "basis_mode", "tau_log", "shrink_k", "layer_hist", "layer_correlation", "layer_cross",
)
# Targets describe the task, not a competing model: changing them between
# candidates would compare different delivery contracts.
EXPERIMENT_TUNABLE_FIELDS = tuple(name for name in MODEL_PARAMETER_FIELDS if name != "tenors")
PAIR_KEYS = ["reference_date", *IDENTITY_COLUMNS, "tenor"]
EEX_POLICY_COLUMNS = ["eex_asof", "eex_cutoff_date", "eex_offset_days"]


@dataclass
class TuneResult:
    calibration_report: pd.DataFrame
    validation_report: pd.DataFrame
    selected_config: dict
    metadata: dict


def _validate_value(field: str, value) -> None:
    if field == "basis_mode" and value not in ("auto", "ratio", "additive"):
        raise ValueError("Tuning basis_mode values must be auto, ratio or additive")
    if field == "layer_hist" and value not in ("auto", "on", "off"):
        raise ValueError("Tuning layer_hist values must be auto, on or off")
    if field in ("layer_correlation", "layer_cross") and not isinstance(value, bool):
        raise ValueError(f"Tuning {field} values must be booleans")
    if field in ("tau_log", "shrink_k"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Tuning {field} values must be finite positive numbers")


def _candidates(cfg: Config, grid: dict[str, list], max_trials: int, *, extended_grid: bool = False) -> list[dict]:
    if isinstance(max_trials, bool) or not isinstance(max_trials, int) or max_trials < 1:
        raise ValueError("max_trials must be a positive integer")
    if not isinstance(extended_grid, bool):
        raise ValueError("extended_grid must be a boolean")
    if not isinstance(grid, dict):
        raise ValueError("parameter_grid must be a dictionary of parameter lists")
    unknown = set(grid) - set(EXPERIMENT_TUNABLE_FIELDS if extended_grid else TUNABLE_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported tuning parameters: {sorted(unknown)}")
    for field, values in grid.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"Tuning parameter {field} requires a nonempty list")
        for value in values:
            _validate_value(field, value)
    count = math.prod(len(values) for values in grid.values())
    if count > max_trials:
        raise ValueError(f"Parameter grid has {count} trials; max_trials is {max_trials}")
    base = {field: getattr(cfg, field) for field in TUNABLE_FIELDS}
    for field, value in base.items():
        _validate_value(field, value)
    names = list(grid)
    candidates = [{**base, **dict(zip(names, values))} for values in product(*(grid[name] for name in names))]
    for candidate in candidates:
        validate_config(replace(cfg, **candidate))
    return candidates


def _observation_dates(cfg: Config, vw: pd.DataFrame, maps: list[ProductMap], start: date, end: date) -> list[date]:
    needed = ["date", "product", "tenor", "vwap"]
    missing = [name for name in needed if name not in vw]
    if missing:
        raise ValueError(f"Tuning input lacks normalized columns {missing}")
    dates = pd.to_datetime(vw["date"], errors="coerce").dt.date
    prices = pd.to_numeric(vw["vwap"], errors="coerce")
    active = {m.key for m in maps if m.active and m.use == "fill"}
    valid = dates.notna() & np.isfinite(prices) & curve_keys(vw).isin(active)
    selected = set()
    for day, tenor in zip(dates[valid], vw.loc[valid, "tenor"]):
        if start <= day <= end and resolve_tenor(str(tenor), day, cfg.day_convention, cfg.weekend_offset) is not None:
            selected.add(day)
    return sorted(selected)


def _paired(loo: pd.DataFrame, dates: list[date], label: str) -> pd.DataFrame:
    """Retain the complete EEX evaluation universe, including abstentions."""
    required = [*PAIR_KEYS, "method", "pred", "own"]
    if loo.empty or any(name not in loo for name in required):
        raise ValueError(f"{label}: no complete paired pipeline/EEX backtest observations")
    identity = normalize_identity(loo)
    frame = loo.assign(**{name: identity[name] for name in IDENTITY_COLUMNS})
    frame = frame.copy()
    frame["reference_date"] = pd.to_datetime(frame["reference_date"], errors="raise").dt.date
    frame = frame[frame["reference_date"].isin(dates)]
    outputs = {}
    for method in ("eex", "pipeline_configured"):
        trace = [name for name in EEX_POLICY_COLUMNS if name in frame] if method == "eex" else []
        rows = frame[frame["method"] == method][[*PAIR_KEYS, "pred", "own", *trace]].copy()
        if (rows.empty and method == "eex") or rows.duplicated(PAIR_KEYS).any():
            raise ValueError(f"{label}: empty or duplicate {method} observation keys")
        for field in ("pred", "own"):
            rows[field] = pd.to_numeric(rows[field], errors="coerce")
            if not np.isfinite(rows[field]).all():
                raise ValueError(f"{label}: nonfinite {method} {field} values")
        outputs[method] = rows.set_index(PAIR_KEYS).sort_index()
    baseline, model = outputs["eex"], outputs["pipeline_configured"]
    if not model.index.isin(baseline.index).all():
        raise ValueError(f"{label}: pipeline observation keys lack an EEX baseline")
    if not baseline.loc[model.index, "own"].equals(model["own"]):
        raise ValueError(f"{label}: pipeline and EEX disagree on the held-out observations")
    paired = baseline.rename(columns={"pred": "eex_pred"}).copy()
    paired["model_pred"] = model["pred"]
    paired["error_eex"] = paired["eex_pred"] - paired["own"]
    paired["error_model"] = paired["model_pred"] - paired["own"]
    if (not np.isfinite(paired["error_eex"]).all()
            or not np.isfinite(paired.loc[paired["model_pred"].notna(), "error_model"]).all()):
        raise ValueError(f"{label}: backtest errors exceed the finite numeric range")
    return paired.reset_index()


def _check_holdout_coverage(cfg: Config, vw: pd.DataFrame, maps: list[ProductMap],
                           books: dict[CurveKey, EexBook | None], dates: list[date]) -> None:
    """Check contract coverage without fitting or scoring any holdout model."""
    active = {m.key: m for m in maps if m.active and m.use == "fill"}
    holdout = set(dates)
    prices = pd.to_numeric(vw["vwap"], errors="coerce")
    observed_dates = pd.to_datetime(vw["date"], errors="coerce").dt.date
    pricers = {}
    for day, curve, tenor, own in zip(observed_dates, curve_keys(vw), vw["tenor"], prices):
        if day not in holdout or curve not in active or not math.isfinite(own):
            continue
        mapping = active[curve]
        book = books.get(curve)
        if book is None:
            continue
        key = (day, curve)
        if key not in pricers:
            quotes, _ = book.available_quotes(day, cfg.max_stale_days, offset_days=cfg.eex_offset_days)
            pricers[key] = Pricer(quotes, hours_fn(mapping.hours, mapping.timezone))
        period = resolve_tenor(str(tenor), day, cfg.day_convention, cfg.weekend_offset)
        if period is not None:
            quote = pricers[key].price(period.start, period.end, kind=period.kind)
            if quote is not None and math.isfinite(quote[0]):
                return
    raise ValueError("No holdout observations match an available EEX delivery period; "
                     "check mapping, dates, tenors and EEX publication coverage")


def _score(paired: pd.DataFrame) -> tuple[float, int]:
    if paired.empty:
        return math.nan, 0
    ratios = []
    for _, rows in paired.groupby(IDENTITY_COLUMNS, sort=False):
        model = _mean(rows["error_model"].abs())
        baseline = _mean(rows["error_eex"].abs())
        ratios.append(model / baseline if baseline > 0 else (1.0 if model == 0 else math.inf))
    return (math.inf if any(math.isinf(value) for value in ratios) else _mean(pd.Series(ratios))), len(ratios)


def _mean(values: pd.Series) -> float:
    if values.empty:
        return math.nan
    numbers = values.to_numpy(dtype=float)
    scale = float(np.abs(numbers).max())
    return scale * float(np.mean(numbers / scale)) if scale else 0.0


def _rmse(values: pd.Series) -> float:
    if values.empty:
        return math.nan
    # Scaling avoids overflowing when squaring otherwise finite errors.
    absolute = values.abs().to_numpy(dtype=float)
    scale = float(absolute.max())
    return scale * math.sqrt(float(np.mean((absolute / scale) ** 2))) if scale else 0.0


def _report(paired: pd.DataFrame, available: pd.DataFrame, trial_id: int,
            parameters: dict, dates: list[date]) -> pd.DataFrame:
    rows = []
    groups = [("overall", "", paired, available), *[
        ("unit", unit, paired[paired["unit"] == unit], group)
        for unit, group in available.groupby("unit", sort=True)
    ]]
    for scope, unit, group, universe in groups:
        score, n_curves = _score(group)
        n_available = int(universe["model_pred"].notna().sum())
        row = dict(trial_id=trial_id, scope=scope, unit=unit, n_paired=len(group), n_curves=n_curves,
                   n_baseline=len(universe), n_available=n_available,
                   n_missing=len(universe) - n_available, coverage=n_available / len(universe),
                   n_paired_dates=group["reference_date"].nunique(),
                   score=score, normalized_skill=1.0 - score,
                   reference_date_from=dates[0].isoformat(), reference_date_to=dates[-1].isoformat(),
                   **parameters)
        for name, field in (("model", "error_model"), ("eex", "error_eex")):
            row[f"mae_{name}"] = _mean(group[field].abs()) if scope == "unit" else math.nan
            row[f"rmse_{name}"] = _rmse(group[field]) if scope == "unit" else math.nan
            row[f"bias_{name}"] = _mean(group[field]) if scope == "unit" else math.nan
        rows.append(row)
    return pd.DataFrame(rows)


def tune_parameters(
    cfg: Config, vw: pd.DataFrame, maps: list[ProductMap], books: dict[CurveKey, EexBook | None],
    start: date, end: date, parameter_grid: dict[str, list], validation_days: int, max_trials: int,
    *, observer: Callable[[str, dict], None] | None = None,
    extended_grid: bool = False,
    include_curve_outputs: bool = False,
    target_curve: CurveKey | None = None,
    curve_parameters: dict[CurveKey, dict] | None = None,
    date_split: tuple[list[date], list[date]] | None = None,
    fixed_evaluation: bool = False,
) -> TuneResult:
    """Choose on calibration dates only, then evaluate just the winner later.

    Dates are distinct reference dates with valid observations in active fill
    curves. At least two common calibration dates and one holdout date with
    EEX coverage are required. Coverage is optimized before accuracy; accuracy
    uses only the intersection of predictions from every candidate. Holdout
    abstentions are reported even when no prediction can be scored.
    Earlier original observations remain available for normal chronological
    warmup. As each holdout day passes, its originals can inform later days;
    the selected parameters never change using holdout results.

    An optional observer receives copied event payloads for external tracking.
    Calibration scores are emitted only after the common comparison sample is
    final. Observer failures propagate; they never silently alter selection or
    produce an apparently complete tracked experiment.
    With ``include_curve_outputs=True``, the observer additionally receives a
    ``curves_evaluated`` event for each completed engine call, containing its
    stage, trial ID and copied full ``filled`` output. These curves retain
    observed originals; they are not the withheld predictions used to score
    accuracy. No additional engine run or holdout candidate is evaluated.
    ``target_curve`` selects one exact product/region/unit identity. Only that
    curve receives candidate parameters; other active curves remain helpers
    under their original effective settings. Global selection ignores mapping
    overrides. Extended searches may vary anchor filters: their held-out truth
    universe then uses filters disabled, independently of candidate settings.
    Targets, identities, calendars and EEX availability remain fixed.
    ``fixed_evaluation=True`` disables minimum volume and maximum deviation
    only when constructing the truth sample; candidate anchor rules still
    apply. ``date_split`` lets related searches share a chronological cutoff.
    An empty grid plus ``curve_parameters`` evaluates fixed product settings
    together without selecting among alternatives.
    """
    def notify(event: str, **payload) -> None:
        if observer is not None:
            observer(event, deepcopy(payload))

    if start > end:
        raise ValueError("The tuning start date must be on or before the end date")
    if cfg.warmup_days != 0:
        raise ValueError("Tuning requires run.warmup_days = 0 to replay all supplied original history")
    if isinstance(validation_days, bool) or not isinstance(validation_days, int) or validation_days < 1:
        raise ValueError("validation_days must be a positive integer")
    if not isinstance(include_curve_outputs, bool):
        raise ValueError("include_curve_outputs must be a boolean")
    if curve_parameters is not None and (target_curve is not None or parameter_grid):
        raise ValueError("Fixed curve_parameters require an empty grid and no target_curve")
    engine_cfg = (cfg if target_curve is not None or curve_parameters is not None else
                  replace(cfg, **cfg.command_overrides, command_overrides={}, configuration_mode="global"))
    if target_curve is not None:
        targets = [mapping for mapping in maps if mapping.active and mapping.use == "fill"
                   and mapping.key == target_curve]
        if len(targets) != 1:
            raise ValueError("target_curve must identify exactly one active fill mapping")
        maps = [replace(mapping, use="helper") if mapping.active and mapping.key != target_curve
                else mapping for mapping in maps]
        cfg = effective_config(engine_cfg, targets[0])
    else:
        cfg = engine_cfg
    candidates = _candidates(cfg, parameter_grid, max_trials, extended_grid=extended_grid)
    if curve_parameters is not None:
        candidates = [{}]
    # Candidate filters affect available anchors, never which truths are scored.
    evaluation_configs = None
    if fixed_evaluation or {"min_volume", "max_anchor_dev", "ratio_eex_floor"}.intersection(parameter_grid):
        evaluation_configs = {
            mapping.key: replace(effective_config(engine_cfg, mapping), min_volume=0.0, max_anchor_dev=0.0)
            for mapping in maps if mapping.active and mapping.use == "fill"
        }
    dates = _observation_dates(cfg, vw, maps, start, end)
    if date_split is None and len(dates) < validation_days + 2:
        raise ValueError("Tuning needs at least two calibration observation dates plus the requested validation_days")
    calibration_dates, holdout_dates = dates[:-validation_days], dates[-validation_days:]
    if date_split is not None:
        calibration_dates, holdout_dates = (list(part) for part in date_split)
        joined = calibration_dates + holdout_dates
        if (not calibration_dates or not holdout_dates or joined != sorted(set(joined))
                or joined[0] < start or joined[-1] > end):
            raise ValueError("date_split requires ordered unique calibration then holdout dates inside the requested range")
        if len(set(dates).intersection(calibration_dates)) < 2 or not set(dates).intersection(holdout_dates):
            raise ValueError("Each selected curve needs two observed calibration dates and an observed holdout date")
    _check_holdout_coverage(cfg, vw, maps, books, holdout_dates)
    notify("started", candidates=candidates, calibration_dates=calibration_dates,
           validation_dates=holdout_dates)
    calibration_results = []
    baseline = None
    common_mask = None

    def evaluate(parameters: dict, days: list[date], label: str, *, trial_id: int, stage: str) -> pd.DataFrame:
        candidate_cfg = replace(cfg, **parameters)
        engine_options = {}
        if target_curve is not None:
            candidate_cfg = engine_cfg
            engine_options["curve_parameters"] = {target_curve: parameters}
        elif curve_parameters is not None:
            engine_options["curve_parameters"] = curve_parameters
        if evaluation_configs is not None:
            engine_options["evaluation_configs"] = evaluation_configs
        try:
            result = CurveFiller(candidate_cfg, vw, maps, books, **engine_options).run(days[0], days[-1], loo=True)
        except Exception as exc:
            raise ValueError(f"{label} failed: {exc}") from exc
        if result.errors:
            raise ValueError(f"{label} failed with {len(result.errors)} engine errors; no partial tuning result")
        if observer is not None:
            notify("configuration_evaluated", trial_id=trial_id, stage=stage,
                   configuration_contexts=getattr(result, "configuration_contexts", {}))
        if include_curve_outputs and observer is not None:
            notify("curves_evaluated", trial_id=trial_id, stage=stage, filled=result.filled)
        return _paired(result.loo, days, label)

    for trial_id, parameters in enumerate(candidates, 1):
        notify("trial_started", trial_id=trial_id, parameters=parameters)
        available = evaluate(parameters, calibration_dates, f"Calibration trial {trial_id}",
                             trial_id=trial_id, stage="calibration")
        notify("calibration_evaluated", trial_id=trial_id, parameters=parameters, available=available)
        trace = [name for name in EEX_POLICY_COLUMNS if name in available]
        evidence = available[[*PAIR_KEYS, "own", "eex_pred", *trace]].set_index(PAIR_KEYS).sort_index()
        if baseline is None:
            baseline = evidence
        elif not baseline.equals(evidence):
            raise ValueError("Calibration trials must use identical observation keys, truth and EEX baseline")
        mask = available["model_pred"].notna()
        common_mask = mask if common_mask is None else common_mask & mask
        calibration_results.append(available)

    common = calibration_results[0].loc[common_mask]
    if common["reference_date"].nunique() < 2:
        raise ValueError("Calibration requires at least two dates with paired pipeline/EEX observations "
                         "common to every candidate; check mapping, history, usable anchors and EEX contract coverage")
    max_available = max(int(frame["model_pred"].notna().sum()) for frame in calibration_results)
    calibration_reports = []
    best_score, selected_trial, selected = math.inf, None, None
    for trial_id, (parameters, available) in enumerate(zip(candidates, calibration_results), 1):
        report = _report(available.loc[common_mask], available, trial_id, parameters, calibration_dates)
        eligible = int(available["model_pred"].notna().sum()) == max_available
        report["eligible_for_selection"] = eligible
        score = float(report.loc[report["scope"] == "overall", "score"].iloc[0])
        if eligible and (selected is None or score < best_score):
            best_score, selected_trial, selected = score, trial_id, parameters.copy()
        calibration_reports.append(report)

    calibration_report = pd.concat(calibration_reports, ignore_index=True)
    calibration_report["selected"] = calibration_report["trial_id"].eq(selected_trial)
    if observer is not None:
        for trial_id, (parameters, available) in enumerate(zip(candidates, calibration_results), 1):
            notify("calibration_scored", trial_id=trial_id, parameters=parameters,
                   report=calibration_report[calibration_report["trial_id"].eq(trial_id)],
                   available=available.assign(accuracy_included=common_mask),
                   selected=trial_id == selected_trial)
    notify("calibration_complete", selected_trial_id=selected_trial,
           selected_config=selected, report=calibration_report)
    notify("validation_started", trial_id=selected_trial, parameters=selected)
    validation_available = evaluate(selected, holdout_dates, f"Validation of trial {selected_trial}",
                                    trial_id=selected_trial, stage="validation")
    validation = validation_available[validation_available["model_pred"].notna()]
    validation_report = _report(validation, validation_available, selected_trial, selected, holdout_dates)
    validation_report["selected"] = True
    if observer is not None:
        notify("validation_scored", trial_id=selected_trial, parameters=selected,
               report=validation_report,
               available=validation_available.assign(accuracy_included=validation_available["model_pred"].notna()))
    metadata = {
        "objective": "mean_per_curve_mae_model_over_mae_eex",
        "evaluated_method": "pipeline_configured", "baseline": "eex",
        "eex_offset_days": cfg.eex_offset_days,
        "eex_availability_policy": "latest_publication_on_or_before_reference_date_plus_calendar_day_offset",
        "eex_staleness_origin": "reference_date",
        "parameter_grid": {field: list(values) for field, values in parameter_grid.items()},
        "selection_scope": "maximum calibration coverage, then best common-case score within the supplied finite grid",
        "accuracy_scope": "intersection_of_predictions_from_all_calibration_candidates",
        "coverage_denominator": "identical_eex_baseline_observations_with_held_out_truth",
        "lower_score_is_better": True, "tie_break": "first_candidate_in_grid_order",
        "calibration_start": calibration_dates[0].isoformat(), "calibration_end": calibration_dates[-1].isoformat(),
        "validation_start": holdout_dates[0].isoformat(), "validation_end": holdout_dates[-1].isoformat(),
        "calibration_days": len(calibration_dates), "validation_days": len(holdout_dates),
        "n_trials": len(candidates), "selected_trial_id": selected_trial,
        "calibration_score": best_score,
        "validation_score": float(validation_report.loc[validation_report["scope"] == "overall", "score"].iloc[0]),
        "calibration_n_baseline": len(baseline), "validation_n_baseline": len(validation_available),
        "calibration_n_available": max_available, "validation_n_available": len(validation),
        "calibration_n_missing": len(baseline) - max_available,
        "validation_n_missing": len(validation_available) - len(validation),
        "calibration_coverage": max_available / len(baseline),
        "validation_coverage": len(validation) / len(validation_available),
        "calibration_n_paired": len(common), "validation_n_paired": len(validation),
        "calibration_paired_days": common["reference_date"].nunique(),
        "validation_paired_days": validation["reference_date"].nunique(),
        "calibration_paired_start": common["reference_date"].min().isoformat(),
        "calibration_paired_end": common["reference_date"].max().isoformat(),
        "validation_paired_start": None if validation.empty else validation["reference_date"].min().isoformat(),
        "validation_paired_end": None if validation.empty else validation["reference_date"].max().isoformat(),
        "warmup_days": 0,
        "validation_protocol": "fixed_selected_parameters_with_chronological_original_history_updates",
    }
    if target_curve is not None:
        metadata.update(search_scope="individual", target_curve=list(target_curve),
                        helper_configuration="fixed_original_effective_settings")
    elif curve_parameters is not None:
        metadata.update(search_scope="combined_verification", optimization=False,
                        curve_parameters=[{"product": key[0], "region": key[1], "unit": key[2],
                                           "parameters": values} for key, values in curve_parameters.items()])
    if evaluation_configs is not None:
        metadata["evaluation_filters"] = {"min_volume": 0.0, "max_anchor_dev": 0.0,
                                           "reason": "candidate_independent_truth_universe"}
    return TuneResult(calibration_report, validation_report, selected, metadata)
