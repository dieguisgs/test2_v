"""Global or per-product research grids, independent of production table cells.

Every search keeps its existing MLflow parent/trial structure. A campaign ID
links searches without making saved-curve inspection depend on a notebook's
memory. Individual winners are also checked together with frozen parameters
on the same chronological split; this is verification, not another search.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd

from vwaps.config import Config
from vwaps.experiments import TrackedTuneResult, _mlflow_api, _write_json, run_tracked_tuning
from vwaps.identity import CurveKey
from vwaps.mapping import ProductMap
from vwaps.product_config import effective_config, model_parameter_payload
from vwaps.tuning import _candidates, _check_holdout_coverage, _observation_dates


@dataclass
class TrackedCampaignResult:
    campaign_id: str
    search_scope: str
    runs: dict[CurveKey | None, TrackedTuneResult]
    combined: TrackedTuneResult | None
    summary: pd.DataFrame
    output_dir: Path


def _curve_key(value) -> CurveKey:
    if isinstance(value, dict):
        missing = {"product", "region", "unit"} - value.keys()
        if missing:
            raise ValueError(f"Curve identity requires explicit product, region and unit: missing {sorted(missing)}")
        value = tuple(value[name] for name in ("product", "region", "unit"))
    if (not isinstance(value, (tuple, list)) or len(value) != 3
            or any(not isinstance(part, str) for part in value) or not value[0].strip()):
        raise ValueError("Curve identity must contain product, region and unit strings")
    return tuple(part.strip() for part in value)


def load_search_plan(path: str | Path) -> dict:
    """Read a strict JSON research plan; no code, production files or paths run.

    Supported keys are search_scope, parameter_grid, selected_curves and
    product_grids. Product entries carry exact identity plus parameter_grid.
    Each product grid replaces the common grid for that product; it does not
    silently merge extra axes. Omitted parameters remain at their fixed base.
    """
    def unique_object(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError(f"Duplicate JSON key: {key}")
            output[key] = value
        return output

    document = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object)
    allowed = {"search_scope", "parameter_grid", "selected_curves", "product_grids"}
    if not isinstance(document, dict) or set(document) - allowed:
        raise ValueError(f"Search plan must be an object using only {sorted(allowed)}")
    if "parameter_grid" not in document:
        raise ValueError("Search plan requires parameter_grid, which may be an empty object")
    return document


def prepare_search_plan(
    cfg: Config, maps: list[ProductMap], parameter_grid: dict[str, list], max_trials: int,
    *, search_scope: str = "global", selected_curves=None, product_grids=None,
) -> tuple[list[ProductMap], list[tuple[CurveKey | None, dict]]]:
    """Validate the complete finite plan before creating any tracked run."""
    if search_scope not in ("global", "individual"):
        raise ValueError("search_scope must be global or individual")
    active = {mapping.key: mapping for mapping in maps if mapping.active and mapping.use == "fill"}
    if len(active) != sum(mapping.active and mapping.use == "fill" for mapping in maps):
        raise ValueError("Duplicate active curve identities in mapping")
    if selected_curves is None:
        selected = list(active)
    elif isinstance(selected_curves, list):
        selected = [_curve_key(item) for item in selected_curves]
    else:
        raise ValueError("selected_curves must be None or a list of exact curve identities")
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("Select at least one active fill curve, without duplicates")
    if set(selected) - active.keys():
        raise ValueError(f"Selected curves are not active fill mappings: {sorted(set(selected) - active.keys())}")
    selected_maps = [replace(mapping, use="helper") if mapping.active and mapping.key not in selected
                     else mapping for mapping in maps]
    if product_grids is None:
        product_grids = []
    if not isinstance(product_grids, list):
        raise ValueError("product_grids must be a list of identity and parameter_grid objects")
    overrides = {}
    for item in product_grids:
        if not isinstance(item, dict) or set(item) != {"product", "region", "unit", "parameter_grid"}:
            raise ValueError("Each product grid requires exactly product, region, unit and parameter_grid")
        key = _curve_key(item)
        if key in overrides or key not in selected:
            raise ValueError(f"Duplicate or unselected product grid: {key}")
        overrides[key] = item["parameter_grid"]
    if search_scope == "global":
        if overrides:
            raise ValueError("Global search cannot accept product_grids; choose individual search")
        _candidates(replace(cfg, configuration_mode="global"), parameter_grid, max_trials, extended_grid=True)
        return selected_maps, [(None, parameter_grid)]
    # Validate the common grid too, even if all targets use an explicit override.
    _candidates(cfg, parameter_grid, max_trials, extended_grid=True)
    searches = []
    for key in selected:
        grid = overrides.get(key, parameter_grid)
        _candidates(effective_config(cfg, active[key]), grid, max_trials, extended_grid=True)
        searches.append((key, grid))
    return selected_maps, searches


def run_tracked_campaign(
    cfg: Config, vw: pd.DataFrame, maps: list[ProductMap], books: dict,
    start: date, end: date, parameter_grid: dict[str, list], validation_days: int, max_trials: int,
    *, tracking_uri: str, experiment_name: str, output_dir: str | Path,
    search_scope: str = "global", selected_curves=None, product_grids=None,
    data_label: str = "real", log_predictions: bool = True,
) -> TrackedCampaignResult:
    """Search globally or independently per exact curve, then verify winners.

    ``max_trials`` caps each product's Cartesian grid. Individual searches use
    one shared date cutoff and keep other curves at their effective production
    settings. The final one-candidate run activates every selected winner
    together, exposing interactions such as CROSS. Its held-out dates remain
    outside all candidate selection, but are reused for this diagnostic check:
    it is not a second independent test set. Nothing is promoted to production.
    """
    selected_maps, searches = prepare_search_plan(
        cfg, maps, parameter_grid, max_trials, search_scope=search_scope,
        selected_curves=selected_curves, product_grids=product_grids)
    if start > end or cfg.warmup_days != 0:
        raise ValueError("Campaign requires start <= end and warmup_days = 0")
    if isinstance(validation_days, bool) or not isinstance(validation_days, int) or validation_days < 1:
        raise ValueError("validation_days must be a positive integer")
    dates = _observation_dates(cfg, vw, selected_maps, start, end)
    if len(dates) < validation_days + 2:
        raise ValueError("Campaign needs at least two calibration dates plus validation_days")
    split = (dates[:-validation_days], dates[-validation_days:])
    for key, _ in searches:
        trial_maps = [replace(mapping, use="helper") if key is not None and mapping.active and mapping.key != key
                      else mapping for mapping in selected_maps]
        observed = _observation_dates(cfg, vw, trial_maps, start, end)
        if len(set(observed).intersection(split[0])) < 2 or not set(observed).intersection(split[1]):
            raise ValueError(f"Curve {key}: requires two calibration observation dates and a holdout observation")
        _check_holdout_coverage(cfg, vw, trial_maps, books, split[1])

    campaign_id = uuid4().hex
    folder = Path(output_dir).expanduser().resolve() / f"campaign_{campaign_id}"
    folder.mkdir(parents=True, exist_ok=False)
    plan = {"campaign_id": campaign_id, "search_scope": search_scope,
            "production_configuration_mode": cfg.configuration_mode,
            "calibration_dates": split[0], "validation_dates": split[1],
            "max_trials_per_search": max_trials,
            "searches": [{"curve_key": key, "parameter_grid": grid} for key, grid in searches],
            "joint_check": "fixed_combined_winners_on_same_reserved_dates" if search_scope == "individual" else None,
            "state": "RUNNING", "completed_runs": []}
    _write_json(folder / "campaign.json", plan)
    runs, winners, rows = {}, {}, []
    combined = None
    try:
        for key, grid in searches:
            tracked = run_tracked_tuning(
                cfg, vw, selected_maps, books, start, end, grid, validation_days, max_trials,
                tracking_uri=tracking_uri, experiment_name=experiment_name, output_dir=folder,
                data_label=data_label, log_predictions=log_predictions, target_curve=key,
                date_split=split, fixed_evaluation=True,
                run_tags={"vwaps.campaign_id": campaign_id, "vwaps.campaign_state": "RUNNING"})
            runs[key] = tracked
            meta = tracked.tuning.metadata
            row = {"product": key[0] if key else "ALL", "region": key[1] if key else "",
                   "unit": key[2] if key else "", "run_id": tracked.run_id, "run_url": tracked.run_url,
                   "selected_trial_id": meta["selected_trial_id"],
                   "calibration_score": meta["calibration_score"], "validation_score": meta["validation_score"],
                   "calibration_coverage": meta["calibration_coverage"],
                   "validation_coverage": meta["validation_coverage"],
                   "parameters": json.dumps(tracked.tuning.selected_config, sort_keys=True)}
            rows.append(row)
            plan["completed_runs"].append({"curve_key": key, "run_id": tracked.run_id})
            _write_json(folder / "campaign.json", plan)
            if key is not None:
                mapping = next(mapping for mapping in selected_maps if mapping.key == key)
                winners[key] = model_parameter_payload(effective_config(cfg, mapping, tracked.tuning.selected_config))
        if search_scope == "individual":
            combined = run_tracked_tuning(
                cfg, vw, selected_maps, books, start, end, {}, validation_days, 1,
                tracking_uri=tracking_uri, experiment_name=experiment_name, output_dir=folder,
                data_label=data_label, log_predictions=log_predictions,
                curve_parameters=winners, date_split=split, fixed_evaluation=True,
                run_tags={"vwaps.campaign_id": campaign_id,
                          "vwaps.campaign_state": "RUNNING",
                          "vwaps.verification": "combined_frozen_winners_no_new_selection"})
            plan["combined_run_id"] = combined.run_id
        summary = pd.DataFrame(rows)
        summary.to_csv(folder / "summary.csv", index=False, encoding="utf-8-sig")
        _write_json(folder / "proposed_product_parameters.json", {
            "applied_to_production": False,
            "products": [{"product": key[0], "region": key[1], "unit": key[2], "parameters": parameters}
                         for key, parameters in winners.items()]})
        plan["state"] = "FINISHED"
        _write_json(folder / "campaign.json", plan)
        client, _ = _mlflow_api(tracking_uri)
        for tracked in [*runs.values(), *([combined] if combined is not None else [])]:
            for filename in ("campaign.json", "summary.csv", "proposed_product_parameters.json"):
                client.log_artifact(tracked.run_id, str(folder / filename), artifact_path="campaign")
        for tracked in [*runs.values(), *([combined] if combined is not None else [])]:
            client.set_tag(tracked.run_id, "vwaps.campaign_state", "FINISHED")
    except BaseException as exc:
        plan.update(state="KILLED" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "FAILED",
                    error_type=type(exc).__name__, error=str(exc))
        cleanup_failures = []
        try:
            _write_json(folder / "campaign.json", plan)
        except Exception as cleanup_error:
            cleanup_failures.append(str(cleanup_error))
        try:
            client, _ = _mlflow_api(tracking_uri)
            for tracked in [*runs.values(), *([combined] if combined is not None else [])]:
                try:
                    client.set_tag(tracked.run_id, "vwaps.campaign_state", plan["state"])
                    client.log_artifact(tracked.run_id, str(folder / "campaign.json"), artifact_path="campaign")
                except Exception as cleanup_error:
                    cleanup_failures.append(str(cleanup_error))
        except Exception as cleanup_error:
            cleanup_failures.append(str(cleanup_error))
        if cleanup_failures:
            exc.add_note("Campaign cleanup could not complete: " + "; ".join(cleanup_failures))
        raise
    return TrackedCampaignResult(campaign_id, search_scope, runs, combined, summary, folder)
