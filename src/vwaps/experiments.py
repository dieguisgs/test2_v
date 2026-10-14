"""Optional MLflow tracking for the tuning procedure and safe model controls.

Tracking never changes the calibration comparison sample, ranking or holdout
protocol. This workflow supports local stores and loopback tracking servers,
including their artifact proxy. Prediction artifacts can be disabled entirely.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import importlib.metadata
import json
import math
import os
import platform
from pathlib import Path
import re
import subprocess
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse

import pandas as pd

from vwaps.config import Config, validate_config
from vwaps.io_eex import EexBook
from vwaps.mapping import ProductMap
from vwaps.identity import CurveKey
from vwaps.product_config import effective_config, model_parameter_payload
from vwaps.tuning import EXPERIMENT_TUNABLE_FIELDS, TuneResult, _candidates, tune_parameters


_TERMINATION_ENV_LOCK = threading.RLock()


@contextmanager
def _quiet_mlflow_termination():
    """Temporarily suppress SDK emoji URLs that fail on Windows cp1252 stdout.

    The adapter prints its own plain-text progress. Serialize this small scoped
    environment change and restore the caller's original setting on every exit.
    No stdout encoding or global MLflow tracking/active-run state is changed.
    """
    name = "MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"
    with _TERMINATION_ENV_LOCK:
        previous = os.environ.get(name)
        os.environ[name] = "true"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


@dataclass
class TrackedTuneResult:
    tuning: TuneResult
    run_id: str
    experiment_id: str
    run_url: str
    output_dir: Path


def _mlflow_api(tracking_uri: str):
    """Import optional dependencies only when tracked tuning is requested."""
    try:
        from mlflow import MlflowClient
        from mlflow.entities import Metric, Param
    except ImportError as exc:
        raise ImportError("Tracked tuning requires MLflow; run: uv sync --group notebook --group experiment") from exc
    return MlflowClient(tracking_uri=tracking_uri), SimpleNamespace(Metric=Metric, Param=Param)


def _json_document(value: dict) -> dict:
    """Write standard JSON, retaining nonfinite states alongside null values."""
    states = {}

    def clean(item, path):
        if isinstance(item, dict):
            return {str(key): clean(val, f"{path}/{key}") for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(val, f"{path}/{index}") for index, val in enumerate(item)]
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Path):
            return str(item)
        if hasattr(item, "item"):
            item = item.item()
        if isinstance(item, float) and not math.isfinite(item):
            states[path] = "nan" if math.isnan(item) else "positive_infinity" if item > 0 else "negative_infinity"
            return None
        return item

    output = clean(value, "")
    if states:
        output["_nonfinite_values"] = states
    return output


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_document(value), ensure_ascii=False, sort_keys=True,
                               indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _hash_json(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _frame_fingerprint(frame: pd.DataFrame) -> dict:
    digest = hashlib.sha256()

    class HashWriter:
        def write(self, text):
            digest.update(text.encode("utf-8"))
            return len(text)

    frame.to_csv(HashWriter(), index=False, lineterminator="\n", float_format="%.17g")
    return {"sha256": digest.hexdigest(), "rows": len(frame), "columns": list(frame.columns),
            "representation": "ordered normalized input CSV; UTF-8; LF; float %.17g"}


def _fingerprints(cfg: Config, vw: pd.DataFrame, maps: list[ProductMap], books: dict) -> dict:
    """Hash evaluated in-memory evidence independently of configured disk paths."""
    eex = []
    for key, book in sorted(books.items(), key=lambda item: repr(item[0])):
        if book is None:
            eex.append({"curve_key": key, "state": "absent"})
            continue
        digest = hashlib.sha256()
        rows = 0
        for publication in book.trade_dates:
            quotes, _ = book.quotes(publication, 0)
            for (start, end), value in sorted(quotes.items()):
                digest.update(json.dumps([publication.isoformat(), start.isoformat(), end.isoformat(), value],
                                         separators=(",", ":"), allow_nan=False).encode("utf-8"))
                digest.update(b"\n")
                rows += 1
        eex.append({"curve_key": key, "state": "present", "sha256": digest.hexdigest(),
                    "publications": len(book.trade_dates), "quoted_periods": rows,
                    "representation": "causal quote snapshots, including available realized-day fixings"})
    return {
        "normalized_input": _frame_fingerprint(vw),
        "mapping": {"sha256": _hash_json([asdict(mapping) for mapping in maps]), "rows": len(maps)},
        "eex_books": eex,
        "configured_paths_not_read": {"vwap_input": str(cfg.vwap_input), "mapping_file": str(cfg.mapping_file),
                                      "eex_curves_dir": str(cfg.eex_curves_dir)},
        "authority": "only evaluated in-memory evidence is fingerprinted; configured data files are never opened",
    }


def _provenance() -> dict:
    root = Path(__file__).resolve().parents[2]

    def git(*arguments):
        try:
            result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, timeout=5, check=False)
            return result.stdout if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    revision = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    diff = git("diff", "HEAD", "--")
    versions = {}
    for package in ("numpy", "pandas", "mlflow", "mlflow-skinny"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    sources = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted((root / "src" / "vwaps").glob("*.py"))}
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": versions,
            "git_commit": revision.decode().strip() if revision is not None else None,
            "git_dirty": bool(status) if status is not None else None,
            "git_diff_sha256": hashlib.sha256(diff).hexdigest() if diff is not None else None,
            "source_file_sha256": sources}


def _local_artifact_uri(uri: str) -> bool:
    parsed = urlparse(uri)
    return parsed.scheme == "file" and parsed.netloc.lower() in ("", "localhost")


def _local_tracking_uri(uri: str) -> bool:
    parsed = urlparse(uri)
    if parsed.scheme in ("http", "https"):
        return parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme in ("sqlite", "sqlite+pysqlite"):
        return not parsed.netloc
    return _local_artifact_uri(uri) or not parsed.scheme or bool(Path(uri).drive)


def _unit_key(unit: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_-]", "_", unit).strip("_")[:60] or "unspecified"
    return f"{readable}_{hashlib.sha256(unit.encode('utf-8')).hexdigest()[:8]}"


def _report_metrics(report: pd.DataFrame, stage: str) -> tuple[dict, dict]:
    metrics, states, units = {}, {}, {}
    dimensionless = ("score", "normalized_skill", "coverage", "n_paired", "n_curves", "n_baseline",
                     "n_available", "n_missing", "n_paired_dates")
    absolute = ("mae_model", "mae_eex", "rmse_model", "rmse_eex", "bias_model", "bias_eex")
    for row in report.to_dict("records"):
        unit = str(row["unit"])
        key = "overall" if row["scope"] == "overall" else f"unit.{_unit_key(unit)}"
        if row["scope"] == "unit":
            units[key] = unit
        for name in (*dimensionless, *(absolute if row["scope"] == "unit" else ())):
            value = float(row[name])
            name = f"{stage}.{key}.{name}"
            if math.isfinite(value):
                metrics[name] = value
            else:
                states[name] = "nan" if math.isnan(value) else "positive_infinity" if value > 0 else "negative_infinity"
    return metrics, {"nonfinite_metrics_not_logged": states, "unit_keys": units}


class _Tracker:
    def __init__(self, client, entities, experiment_id, parent_id, folder, cfg, total, log_predictions,
                 *, maps=(), target_curve=None, curve_parameters=None):
        self.client, self.entities = client, entities
        self.experiment_id, self.parent_id, self.folder = experiment_id, parent_id, folder
        self.cfg, self.total, self.log_predictions = cfg, total, log_predictions
        self.maps, self.target_curve, self.curve_parameters = maps, target_curve, curve_parameters
        self.children = {}
        self.open_runs = {parent_id}
        self.started = time.monotonic()

    def json(self, run_id, folder, filename, value):
        path = folder / filename
        _write_json(path, value)
        self.client.log_artifact(run_id, str(path), artifact_path="audit")

    def report(self, run_id, folder, stage, report):
        path = folder / f"{stage}_report.csv"
        report.to_csv(path, index=False, encoding="utf-8-sig")
        self.client.log_artifact(run_id, str(path), artifact_path="reports")
        metrics, states = _report_metrics(report, stage)
        timestamp = int(time.time() * 1000)
        self.client.log_batch(run_id, metrics=[self.entities.Metric(key, value, timestamp, 0)
                                              for key, value in metrics.items()], synchronous=True)
        self.json(run_id, folder, f"{stage}_metric_states.json", states)

    def predictions(self, child, stage, frame):
        if not self.log_predictions:
            return
        path = child["folder"] / f"{stage}_paired_predictions.csv"
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        self.client.log_artifact(child["id"], str(path), artifact_path="predictions")

    def curves(self, child, stage, frame):
        """Persist the full refill, including originals, separately from LOO."""
        if not self.log_predictions:
            return
        path = child["folder"] / f"{stage}_filled.csv"
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        self.client.log_artifact(child["id"], str(path), artifact_path="curves")

    def terminate(self, run_id, status):
        with _quiet_mlflow_termination():
            self.client.set_terminated(run_id, status=status)
        self.open_runs.discard(run_id)

    def __call__(self, event, payload):
        if event == "started":
            self.json(self.parent_id, self.folder, "split.json", {
                "calibration_dates": payload["calibration_dates"],
                "validation_dates": payload["validation_dates"],
                "protocol": "select_on_calibration_only_then_evaluate_one_fixed_winner",
            })
            return
        if event == "calibration_complete":
            self.json(self.parent_id, self.folder, "selected_parameters.json", {
                "trial_id": payload["selected_trial_id"], "parameters": payload["selected_config"],
                "selection_data": "calibration_only",
            })
            path = self.folder / "calibration_report.csv"
            payload["report"].to_csv(path, index=False, encoding="utf-8-sig")
            self.client.log_artifact(self.parent_id, str(path), artifact_path="reports")
            return
        trial_id = payload["trial_id"]
        if event == "trial_started":
            print(f"Calibration trial {trial_id}/{self.total}: {payload['parameters']}", flush=True)
            run = self.client.create_run(self.experiment_id, tags={
                "mlflow.parentRunId": self.parent_id, "mlflow.runName": f"calibration-{trial_id:03d}",
                "vwaps.trial_id": str(trial_id), "vwaps.stage": "calibration_running",
            })
            run_id = run.info.run_id
            self.open_runs.add(run_id)
            folder = self.folder / f"trial_{trial_id:03d}"
            folder.mkdir()
            child = self.children[trial_id] = {"id": run_id, "folder": folder}
            self.client.set_tag(run_id, "vwaps.predictions", "disabled" if not self.log_predictions else "enabled")
            self.client.set_tag(run_id, "vwaps.curve_outputs", "enabled" if self.log_predictions else "disabled")
            self.client.set_tag(run_id, "vwaps.curve_outputs_kind", "full_refill_with_originals")
            parameters = {"eex_offset_days": self.cfg.eex_offset_days, **payload["parameters"]}
            self.client.log_batch(run_id, params=[self.entities.Param(key, str(value))
                                                 for key, value in parameters.items()], synchronous=True)
            trial_config = (effective_config(self.cfg,
                            next(mapping for mapping in self.maps if mapping.key == self.target_curve),
                            payload["parameters"]) if self.target_curve is not None else
                            replace(self.cfg, **payload["parameters"]))
            self.json(run_id, folder, "configuration.json", asdict(trial_config))
            contexts = []
            for mapping in self.maps:
                if not mapping.active:
                    continue
                if self.curve_parameters is not None:
                    base, overlay = self.cfg, self.curve_parameters.get(mapping.key)
                elif self.target_curve is not None:
                    base = self.cfg
                    overlay = payload["parameters"] if mapping.key == self.target_curve else None
                else:
                    base, overlay = replace(self.cfg, **payload["parameters"]), None
                contexts.append({"product": mapping.product, "region": mapping.region,
                                 "unit": mapping.unit,
                                 "use": "helper" if self.target_curve is not None and mapping.key != self.target_curve
                                 else mapping.use,
                                 "parameters": model_parameter_payload(effective_config(base, mapping, overlay))})
            self.json(run_id, folder, "effective_curve_configurations.json", {"curves": contexts})
            self.json(run_id, folder, "trial.json", {"trial_id": trial_id, "run_id": run_id,
                                                     "parameters": payload["parameters"]})
            return
        child = self.children[trial_id]
        run_id, folder = child["id"], child["folder"]
        if event == "configuration_evaluated":
            self.json(run_id, folder, "configuration_contexts.json", payload["configuration_contexts"])
        elif event == "curves_evaluated":
            self.curves(child, payload["stage"], payload["filled"])
        elif event == "calibration_evaluated":
            self.predictions(child, "calibration", payload["available"])
            self.client.set_tag(run_id, "vwaps.stage", "calibration_evaluated_awaiting_common_sample")
        elif event == "calibration_scored":
            self.report(run_id, folder, "calibration", payload["report"])
            self.predictions(child, "calibration", payload["available"])
            self.client.set_tag(run_id, "vwaps.selected", str(payload["selected"]).lower())
            self.client.set_tag(run_id, "vwaps.stage", "calibration_scored")
            if not payload["selected"]:
                self.terminate(run_id, "FINISHED")
        elif event == "validation_started":
            print(f"Validation: evaluating selected trial {trial_id}; no holdout ranking.", flush=True)
            self.client.set_tag(run_id, "vwaps.stage", "validation_running")
        elif event == "validation_scored":
            self.report(run_id, folder, "validation", payload["report"])
            self.predictions(child, "validation", payload["available"])
            self.client.set_tag(run_id, "vwaps.stage", "validation_scored")


def run_tracked_tuning(
    cfg: Config, vw: pd.DataFrame, maps: list[ProductMap], books: dict,
    start: date, end: date, parameter_grid: dict[str, list], validation_days: int, max_trials: int,
    *, tracking_uri: str, experiment_name: str, output_dir: str | Path,
    data_label: str = "real", log_predictions: bool = True,
    target_curve: CurveKey | None = None,
    curve_parameters: dict[CurveKey, dict] | None = None,
    date_split: tuple[list[date], list[date]] | None = None,
    run_tags: dict[str, str] | None = None,
    fixed_evaluation: bool = False,
) -> TrackedTuneResult:
    """Track the existing tuner without changing its calculations or selection.

    Reports, configuration, fingerprints and code provenance are logged to the
    explicitly configured local tracking service. ``log_predictions`` controls
    both paired LOO predictions and full refill curves, which retain observed
    originals and cannot be interpreted as withheld accuracy predictions.
    Full curves come from the existing engine calls: calibration for every
    candidate and validation for the selected candidate only. Price artifacts
    are written locally and logged through the service's artifact store, including
    a loopback server's artifact proxy. SQLite/file tracking gets a local artifact location when this function
    creates an experiment. An existing experiment retains its artifact store.

    Each call creates a unique parent-run output folder. No production config
    is overwritten. A localhost run URL for direct database/file tracking is
    a suggested UI address; this function does not start a tracking server.

    A normal call searches one shared global configuration, ignoring production
    table overrides. ``target_curve`` instead changes only the exact selected
    curve and preserves the effective helper settings. ``curve_parameters``
    with an empty grid verifies one frozen collection of product settings; it
    does not optimize a global winner. Campaigns supply a common ``date_split``
    and ``fixed_evaluation=True`` to keep filter-independent truth cases. Full
    effective configurations and immutable engine context documents are audit
    artifacts even when price-level logging is disabled.
    """
    cfg = (cfg if target_curve is not None or curve_parameters is not None else
           replace(cfg, **cfg.command_overrides, command_overrides={}, configuration_mode="global"))
    validate_config(cfg)
    if curve_parameters is not None and (target_curve is not None or parameter_grid):
        raise ValueError("Fixed curve_parameters require an empty grid and no target_curve")
    target_maps = [mapping for mapping in maps if mapping.active and mapping.use == "fill"
                   and mapping.key == target_curve]
    if target_curve is not None and len(target_maps) != 1:
        raise ValueError("target_curve must identify exactly one active fill mapping")
    candidate_base = effective_config(cfg, target_maps[0]) if target_curve is not None else cfg
    candidates = ([{}] if curve_parameters is not None else
                  _candidates(candidate_base, parameter_grid, max_trials, extended_grid=True))
    if run_tags is not None and (not isinstance(run_tags, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) or not k.startswith("vwaps.")
                   or k in {"vwaps.protocol", "vwaps.curve_outputs", "vwaps.curve_outputs_kind"}
                   for k, v in run_tags.items())):
        raise ValueError("run_tags must contain string vwaps.* metadata without reserved protocol tags")
    if start > end or cfg.warmup_days != 0:
        raise ValueError("Tracked tuning requires start <= end and run.warmup_days = 0")
    if isinstance(validation_days, bool) or not isinstance(validation_days, int) or validation_days < 1:
        raise ValueError("validation_days must be a positive integer")
    if not isinstance(log_predictions, bool):
        raise ValueError("log_predictions must be a boolean")
    for name, value in (("tracking_uri", tracking_uri), ("experiment_name", experiment_name), ("data_label", data_label)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
    if not _local_tracking_uri(tracking_uri):
        raise ValueError("This notebook tracking workflow requires a local SQLite/file store or loopback MLflow server")
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    client, entities = _mlflow_api(tracking_uri)
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        scheme = urlparse(tracking_uri).scheme
        location = ((root / "_mlflow_artifacts" / hashlib.sha256(experiment_name.encode()).hexdigest()[:16]).as_uri()
                    if scheme in ("", "file", "sqlite", "sqlite+pysqlite") else None)
        try:
            experiment_id = client.create_experiment(experiment_name, artifact_location=location)
        except Exception:
            experiment = client.get_experiment_by_name(experiment_name)
            if experiment is None:
                raise
            experiment_id = experiment.experiment_id
    else:
        experiment_id = experiment.experiment_id
    if experiment is not None and getattr(experiment, "lifecycle_stage", "active") != "active":
        raise ValueError(f"Experiment {experiment_name!r} is not active")
    scope = "individual" if target_curve is not None else "combined_verification" if curve_parameters is not None else "global"
    label = " | ".join(target_curve) if target_curve is not None else scope
    parent = client.create_run(experiment_id, tags={
        "mlflow.runName": f"vwaps-{label}-{data_label}", "vwaps.data_label": data_label,
        "vwaps.protocol": "calibration_grid_then_single_winner_holdout",
        "vwaps.tunable_fields": ",".join(EXPERIMENT_TUNABLE_FIELDS),
        "vwaps.curve_outputs": "enabled" if log_predictions else "disabled",
        "vwaps.curve_outputs_kind": "full_refill_with_originals",
        "vwaps.search_scope": scope,
        **({f"vwaps.{name}": value for name, value in zip(("product", "region", "unit"), target_curve)}
           if target_curve is not None else {}),
        **(run_tags or {}),
    })
    run_id = parent.info.run_id
    folder = root / run_id
    tracker = _Tracker(client, entities, experiment_id, run_id, folder, cfg, len(candidates), log_predictions,
                       maps=maps, target_curve=target_curve, curve_parameters=curve_parameters)
    folder_initialized = False
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
            raise ValueError("Tracking service returned an invalid run identifier")
        folder.mkdir(exist_ok=False)
        folder_initialized = True
        print(f"Tracking {len(candidates)} calibration trials. Local results: {folder}", flush=True)
        client.log_batch(run_id, params=[entities.Param(key, str(value)) for key, value in {
            "n_trials": len(candidates), "validation_days": validation_days, "max_trials": max_trials,
            "start": start.isoformat(), "end": end.isoformat(), "data_label": data_label,
            "eex_offset_days": cfg.eex_offset_days,
        }.items()], synchronous=True)
        tracker.json(run_id, folder, "configuration.json", asdict(cfg))
        tracker.json(run_id, folder, "search_context.json", {
            "scope": scope, "target_curve": target_curve,
            "production_configuration_mode": cfg.configuration_mode,
            "mapping": [asdict(mapping) for mapping in maps],
            "fixed_curve_parameters": [] if curve_parameters is None else [
                {"curve_key": key, "parameters": value} for key, value in curve_parameters.items()],
            "date_split": date_split, "tags": run_tags or {},
        })
        tracker.json(run_id, folder, "grid.json", {"parameter_grid": parameter_grid, "candidates": candidates,
                                                 "validation_days": validation_days, "max_trials": max_trials})
        tracker.json(run_id, folder, "fingerprints.json", _fingerprints(cfg, vw, maps, books))
        tracker.json(run_id, folder, "provenance.json", _provenance())
        tracker.json(run_id, folder, "run.json", {"run_id": run_id, "experiment_id": experiment_id,
                                                "data_label": data_label, "start": start, "end": end,
                                                "log_predictions": log_predictions, "state": "RUNNING"})
        result = tune_parameters(cfg, vw, maps, books, start, end, parameter_grid, validation_days, max_trials,
                                 observer=tracker, extended_grid=True, include_curve_outputs=log_predictions,
                                 target_curve=target_curve, curve_parameters=curve_parameters, date_split=date_split,
                                 fixed_evaluation=fixed_evaluation)
        tracker.json(run_id, folder, "metadata.json", result.metadata)
        tracker.report(run_id, folder, "validation", result.validation_report)
        selected_id = result.metadata["selected_trial_id"]
        selected_calibration = result.calibration_report[result.calibration_report.trial_id.eq(selected_id)]
        # Keep the full-grid CSV already written by the calibration observer.
        metrics, states = _report_metrics(selected_calibration, "calibration")
        timestamp = int(time.time() * 1000)
        client.log_batch(run_id, metrics=[entities.Metric(key, value, timestamp, 0)
                                         for key, value in metrics.items()], synchronous=True)
        tracker.json(run_id, folder, "calibration_metric_states.json", states)
        client.log_batch(run_id, params=[entities.Param(f"selected.{key}", str(value))
                                         for key, value in result.selected_config.items()], synchronous=True)
        child_runs = {str(number): child["id"] for number, child in tracker.children.items()}
        tracker.json(run_id, folder, "run.json", {"run_id": run_id, "experiment_id": experiment_id,
                                                "data_label": data_label, "state": "FINISHED",
                                                "child_runs": child_runs,
                                                "elapsed_seconds": time.monotonic() - tracker.started})
        for active in list(tracker.open_runs - {run_id}):
            tracker.terminate(active, "FINISHED")
        tracker.terminate(run_id, "FINISHED")
    except BaseException as exc:
        state = "KILLED" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "FAILED"
        failures = []
        if folder_initialized:
            try:
                failure = {"state": state, "error_type": type(exc).__name__,
                           "message": str(exc), "run_id": run_id,
                           "child_runs": {str(n): c["id"] for n, c in tracker.children.items()}}
                _write_json(folder / "failure.json", failure)
                _write_json(folder / "run.json", {key: value for key, value in failure.items() if key != "message"})
            except Exception as cleanup_error:
                failures.append(str(cleanup_error))
            try:
                # Replace a previously uploaded FINISHED manifest if final
                # backend termination failed. This is independent of cleanup.
                client.log_artifact(run_id, str(folder / "run.json"), artifact_path="audit")
            except Exception as cleanup_error:
                failures.append(str(cleanup_error))
        for active in list(tracker.open_runs):
            try:
                client.set_tag(active, "vwaps.failure_type", type(exc).__name__)
            except Exception as cleanup_error:
                failures.append(str(cleanup_error))
            try:
                tracker.terminate(active, state)
            except Exception as cleanup_error:
                failures.append(str(cleanup_error))
        if failures:
            exc.add_note("Tracking cleanup could not complete: " + "; ".join(failures))
        raise
    ui = tracking_uri.rstrip("/") if urlparse(tracking_uri).scheme in ("http", "https") else "http://127.0.0.1:5000"
    url = f"{ui}/#/experiments/{experiment_id}/runs/{run_id}"
    print(f"Tracked tuning finished. Selected trial: {selected_id}. Run: {run_id}", flush=True)
    return TrackedTuneResult(result, run_id, str(experiment_id), url, folder)
