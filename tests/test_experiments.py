"""Tracked tuning retains the real engine's scoring and failure semantics."""

from dataclasses import replace
from datetime import date
from io import BytesIO, StringIO
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tuning import tune_parameters
import vwaps.experiments as experiments


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    cfg = replace(load_config(path), tenors=["M+1", "M+2"])
    days = list(pd.bdate_range("2026-09-01", periods=8).date)
    maps, books, observations = [], {}, []
    for unit, scale in (("EUR/MWh", 1), ("GBP/MWh", 2)):
        mapping = ProductMap("P", "fill", "DE", "Base", f"{scale}.csv", "Base", "Europe/Berlin",
                             region="North", unit=unit)
        maps.append(mapping)
        settlements = []
        for i, day in enumerate(days):
            for n in (1, 2):
                eex = scale * n * (100.0 + i)
                observations.append(dict(date=day, product="P", region="North", unit=unit,
                                         tenor=f"M+{n}", vwap=eex + 10 * scale, volume=100.0))
                settlements.append((day, "Month", date(2026, 9 + n, 1), eex))
        books[mapping.key] = EexBook(pd.DataFrame(settlements, columns=REQUIRED))
    return cfg, pd.DataFrame(observations), maps, books, days


class FakeClient:
    def __init__(self):
        self.runs = {}
        self.experiment = None
        self.artifact_uri = "mlflow-artifacts:/test/artifacts"

    def get_experiment_by_name(self, name):
        return self.experiment

    def create_experiment(self, name, artifact_location=None):
        self.experiment = SimpleNamespace(experiment_id="7", lifecycle_stage="active", name=name)
        return "7"

    def create_run(self, experiment_id, tags):
        run_id = f"run{len(self.runs) + 1:04d}"
        self.runs[run_id] = dict(tags=dict(tags), metrics={}, params={}, artifacts={}, status="RUNNING")
        return SimpleNamespace(info=SimpleNamespace(run_id=run_id, artifact_uri=self.artifact_uri))

    def set_tag(self, run_id, key, value):
        self.runs[run_id]["tags"][key] = value

    def log_batch(self, run_id, metrics=(), params=(), synchronous=True):
        assert synchronous
        for metric in metrics:
            assert math.isfinite(metric.value)
            self.runs[run_id]["metrics"][metric.key] = metric.value
        for parameter in params:
            self.runs[run_id]["params"][parameter.key] = parameter.value

    def log_artifact(self, run_id, path, artifact_path=None):
        path = Path(path)
        self.runs[run_id]["artifacts"][(artifact_path, path.name)] = path.read_bytes()

    def set_terminated(self, run_id, status):
        self.runs[run_id]["status"] = status


@pytest.fixture
def tracking(monkeypatch):
    client = FakeClient()
    entity = SimpleNamespace(
        Metric=lambda key, value, timestamp, step: SimpleNamespace(key=key, value=value),
        Param=lambda key, value: SimpleNamespace(key=key, value=value),
    )
    monkeypatch.setattr(experiments, "_mlflow_api", lambda uri: (client, entity))
    return client


def _run(dataset, tmp_path, **kwargs):
    cfg, own, maps, books, days = dataset
    return experiments.run_tracked_tuning(
        cfg, own, maps, books, days[0], days[-1],
        kwargs.pop("grid", {"basis_mode": ["auto", "ratio", "additive"]}), 2, 20,
        tracking_uri=kwargs.pop("tracking_uri", "http://127.0.0.1:5000"),
        experiment_name="test", output_dir=tmp_path / "tracked", **kwargs,
    )


@pytest.mark.parametrize("grid", [{"tenors": [["M+1"]]}, {"tau_log": [0]}, {"basis_mode": ["bad"]},
                                  {"shape_mode": ["bad"]}, {"shape_adjust_originals": ["false"]},
                                  {"fallback_price_window": [1]}, {"min_volume": [-1]}])
def test_invalid_grid_fails_before_tracking_or_engine_work(dataset, tmp_path, monkeypatch, grid):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid grids must fail before tracking or engine work")

    monkeypatch.setattr(experiments, "_mlflow_api", forbidden)
    monkeypatch.setattr(CurveFiller, "run", forbidden)
    with pytest.raises(ValueError):
        _run(dataset, tmp_path, grid=grid)


def _fake_loo(start, end, *, omit_predictions=False):
    rows = []
    for day in pd.bdate_range(start, end).date:
        for unit in ("EUR/MWh", "GBP/MWh"):
            for method, prediction in (("eex", 90.0), ("pipeline_configured", 98.0)):
                if omit_predictions and method == "pipeline_configured":
                    continue
                rows.append(dict(reference_date=day, product="P", region="North", unit=unit,
                                 tenor="M+1", method=method, pred=prediction, own=100.0))
    return pd.DataFrame(rows)


def _fake_filled(start, end):
    """Represent full observed refill rows separately from fake LOO scores."""
    frame = _fake_loo(start, end).query("method == 'eex'").drop(columns=["method", "pred"])
    return frame.rename(columns={"own": "price"}).assign(source="own", data_origin="original")


def test_fingerprints_describe_actual_evidence_even_when_configured_files_are_absent(dataset):
    cfg, own, maps, books, _ = dataset
    first = experiments._fingerprints(cfg, own, maps, books)
    changed = own.copy()
    changed.loc[0, "vwap"] += 1
    second = experiments._fingerprints(cfg, changed, maps, books)
    assert first["normalized_input"]["sha256"] != second["normalized_input"]["sha256"]
    assert first["mapping"]["sha256"] == second["mapping"]["sha256"]
    assert len(first["eex_books"]) == 2 and all(len(book["sha256"]) == 64 for book in first["eex_books"])
    assert first["configured_paths_not_read"]["vwap_input"] == str(cfg.vwap_input)


def test_synthetic_fingerprints_never_read_or_stat_configured_real_data_paths(dataset, monkeypatch):
    cfg, own, maps, books, _ = dataset
    cfg = replace(cfg, vwap_input="Z:/private/real_prices*.xlsx",
                  mapping_file=Path("Z:/private/mapping.csv"), eex_curves_dir=Path("Z:/private/eex"))

    def forbidden(*args, **kwargs):
        pytest.fail("In-memory synthetic fingerprinting must not inspect configured data files")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    result = experiments._fingerprints(cfg, own, maps, books)
    assert len(result["normalized_input"]["sha256"]) == 64
    assert len(result["eex_books"]) == 2


@pytest.mark.parametrize("log_predictions", [False, True])
def test_real_mlflow_sqlite_tracks_runs_without_changing_active_global_context(dataset, tmp_path, log_predictions):
    mlflow = pytest.importorskip("mlflow")
    from vwaps.experiment_viewer import list_experiment_runs, list_trial_runs, load_trial_curves
    active_before = mlflow.active_run()
    tracking_before = mlflow.get_tracking_uri()
    uri = "sqlite:///" + (tmp_path / "tracking.db").as_posix()
    tracked = _run(dataset, tmp_path, tracking_uri=uri, grid={"basis_mode": ["additive"]},
                   log_predictions=log_predictions)
    client = mlflow.MlflowClient(tracking_uri=uri)
    parent = client.get_run(tracked.run_id)
    assert parent.info.status == "FINISHED"
    assert parent.data.metrics["validation.overall.coverage"] == 1.0
    children = client.search_runs([tracked.experiment_id], f"tags.`mlflow.parentRunId` = '{tracked.run_id}'")
    assert len(children) == 1 and children[0].info.status == "FINISHED"
    assert list_experiment_runs(uri, "test").run_id.tolist() == [tracked.run_id]
    trials = list_trial_runs(uri, tracked.run_id)
    assert bool(trials.iloc[0].calibration_curves) == log_predictions
    assert bool(trials.iloc[0].validation_curves) == log_predictions
    if log_predictions:
        saved = load_trial_curves(uri, tracked.run_id, children[0].info.run_id,
                                  "calibration", cache_dir=tmp_path / "viewer")
        assert not saved.empty and saved.source.eq("own").all()
        assert saved.reference_date.max().date() < dataset[-1][-2]
    assert mlflow.active_run() is active_before
    assert mlflow.get_tracking_uri() == tracking_before
