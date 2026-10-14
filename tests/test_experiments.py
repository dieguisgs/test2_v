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
    cfg = replace(load_config(path), tenors=["M+1", "M+2"], tau_log=10.0,
                  shrink_k=0.01, layer_hist="off")
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


def test_tracking_matches_real_engine_selection_reports_and_single_holdout(dataset, tmp_path, tracking, monkeypatch):
    cfg, own, maps, books, days = dataset
    before = own.copy(deep=True)
    plain = tune_parameters(cfg, own, maps, books, days[0], days[-1],
                            {"basis_mode": ["auto", "ratio", "additive"]}, 2, 20)
    calls = []
    computed = []
    original = CurveFiller.run

    def count(self, start, end, loo=False):
        calls.append((self.cfg.basis_mode, start, end))
        result = original(self, start, end, loo=loo)
        computed.append(result.filled.copy(deep=True))
        return result

    monkeypatch.setattr(CurveFiller, "run", count)
    tracked = _run(dataset, tmp_path)
    assert tracked.tuning.selected_config == plain.selected_config
    assert tracked.tuning.metadata == plain.metadata
    pd.testing.assert_frame_equal(tracked.tuning.calibration_report, plain.calibration_report)
    pd.testing.assert_frame_equal(tracked.tuning.validation_report, plain.validation_report)
    pd.testing.assert_frame_equal(own, before)
    assert calls == [(mode, days[0], days[-3]) for mode in ("auto", "ratio", "additive")] + [
        (plain.selected_config["basis_mode"], days[-2], days[-1]),
    ]
    assert len(tracking.runs) == 4
    assert {run["status"] for run in tracking.runs.values()} == {"FINISHED"}
    children = [run for run in tracking.runs.values() if "mlflow.parentRunId" in run["tags"]]
    assert sum(any(key.startswith("validation.") for key in child["metrics"]) for child in children) == 1
    assert all(child["tags"]["mlflow.parentRunId"] == tracked.run_id for child in children)
    assert all(run["params"]["eex_offset_days"] == "0" for run in tracking.runs.values())
    assert all(("predictions", "calibration_paired_predictions.csv") in child["artifacts"] for child in children)
    assert all(run["tags"]["vwaps.curve_outputs"] == "enabled" for run in tracking.runs.values())
    winner = plain.metadata["selected_trial_id"]
    for number, child in enumerate(children, 1):
        artifact = child["artifacts"]["curves", "calibration_filled.csv"]
        saved = pd.read_csv(BytesIO(artifact))
        expected = pd.read_csv(StringIO(computed[number - 1].to_csv(index=False)))
        pd.testing.assert_frame_equal(saved, expected)
        assert (tracked.output_dir / f"trial_{number:03d}" / "calibration_filled.csv").read_bytes() == artifact
        assert saved.reference_date.max() <= days[-3].isoformat()
        assert (("curves", "validation_filled.csv") in child["artifacts"]) == (number == winner)
    heldout = pd.read_csv(BytesIO(children[winner - 1]["artifacts"]["curves", "validation_filled.csv"]))
    pd.testing.assert_frame_equal(heldout, pd.read_csv(StringIO(computed[-1].to_csv(index=False))))
    assert set(heldout.reference_date) == {day.isoformat() for day in days[-2:]}
    assert children[winner - 1]["tags"]["vwaps.curve_outputs_kind"] == "full_refill_with_originals"
    assert tracked.run_url == f"http://127.0.0.1:5000/#/experiments/7/runs/{tracked.run_id}"
    assert tracked.output_dir.name == tracked.run_id


def test_absolute_errors_are_logged_only_per_unit_and_units_never_collide(dataset, tmp_path, tracking):
    tracked = _run(dataset, tmp_path)
    for run in tracking.runs.values():
        assert not any("overall.mae" in key or "overall.rmse" in key or "overall.bias" in key
                       for key in run["metrics"])
    parent = tracking.runs[tracked.run_id]
    maes = {key: value for key, value in parent["metrics"].items() if key.endswith("mae_eex")}
    assert set(maes.values()) == {10.0, 20.0}
    assert experiments._unit_key("EUR/MWh") != experiments._unit_key("EUR_MWh")
    states = json.loads((tracked.output_dir / "validation_metric_states.json").read_text())
    assert set(states["unit_keys"].values()) == {"EUR/MWh", "GBP/MWh"}


def test_prediction_opt_out_writes_and_uploads_no_price_level_prediction_files(dataset, tmp_path, tracking):
    tracked = _run(dataset, tmp_path, log_predictions=False)
    assert not list(tracked.output_dir.rglob("*paired_predictions*"))
    assert not list(tracked.output_dir.rglob("*_filled.csv"))
    assert not any(path in ("predictions", "curves") for run in tracking.runs.values() for path, _ in run["artifacts"])
    assert all(run["tags"]["vwaps.curve_outputs"] == "disabled" for run in tracking.runs.values())
    assert (tracked.output_dir / "calibration_report.csv").is_file()
    assert (tracked.output_dir / "metadata.json").is_file()


def test_full_curves_include_observed_originals_filled_gaps_and_days_without_observations(dataset, tmp_path, tracking):
    cfg, own, maps, books, days = dataset
    own = own.loc[~(own.date.eq(days[0]) & own.tenor.eq("M+2")) & ~own.date.eq(days[1])].copy()
    tracked = _run((cfg, own, maps, books, days), tmp_path, grid={"basis_mode": ["ratio"]})
    child = tracking.runs["run0002"]
    full = pd.read_csv(BytesIO(child["artifacts"]["curves", "calibration_filled.csv"]))
    original = full.loc[full.reference_date.eq(str(days[0])) & full.tenor.eq("M+1") & full.unit.eq("EUR/MWh")].iloc[0]
    assert original.price == original.own_vwap == 110.0
    assert original.source == "own" and original.data_origin == "original"
    estimated = full.loc[full.reference_date.eq(str(days[0])) & full.tenor.eq("M+2")]
    assert len(estimated) == 2 and estimated.price.notna().all()
    assert estimated.data_origin.eq("estimated").all()
    assert str(days[1]) in set(full.reference_date)  # No LOO truth exists on this weekday.
    paired = pd.read_csv(BytesIO(child["artifacts"]["predictions", "calibration_paired_predictions.csv"]))
    assert str(days[1]) not in set(paired.reference_date)
    common = full.merge(paired, on=["reference_date", "product", "region", "unit", "tenor"])
    assert (common.loc[common.model_pred.notna(), "price"] != common.loc[common.model_pred.notna(), "model_pred"]).any()
    assert full.reference_date.max() < tracked.tuning.metadata["validation_start"]


def test_curve_opt_out_does_not_request_full_output_from_the_tuner(dataset, tmp_path, tracking, monkeypatch):
    original = CurveFiller.run

    def without_filled(self, start, end, loo=False):
        result = original(self, start, end, loo=loo)
        return SimpleNamespace(errors=result.errors, loo=result.loo)

    monkeypatch.setattr(CurveFiller, "run", without_filled)
    tracked = _run(dataset, tmp_path, grid={"basis_mode": ["ratio"]}, log_predictions=False)
    assert tracked.tuning.metadata["n_trials"] == 1
    assert not list(tracked.output_dir.rglob("*_filled.csv"))


def test_curve_upload_failure_preserves_local_curves_and_marks_run_failed(dataset, tmp_path, tracking, monkeypatch):
    original = tracking.log_artifact

    def fail_curve(run_id, path, artifact_path=None):
        if artifact_path == "curves":
            raise RuntimeError("curve artifact upload failed")
        return original(run_id, path, artifact_path)

    monkeypatch.setattr(tracking, "log_artifact", fail_curve)
    with pytest.raises(RuntimeError, match="curve artifact upload failed"):
        _run(dataset, tmp_path)
    assert {run["status"] for run in tracking.runs.values()} == {"FAILED"}
    assert (tmp_path / "tracked" / "run0001" / "trial_001" / "calibration_filled.csv").is_file()
    assert not list((tmp_path / "tracked").rglob("validation_filled.csv"))


def test_each_invocation_has_an_independent_parent_directory(dataset, tmp_path, tracking):
    first = _run(dataset, tmp_path, log_predictions=False)
    second = _run(dataset, tmp_path, log_predictions=False)
    assert first.run_id != second.run_id and first.output_dir != second.output_dir
    assert (first.output_dir / "metadata.json").is_file()
    assert (second.output_dir / "metadata.json").is_file()


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


def test_remote_tracking_is_outside_the_local_notebook_workflow(dataset, tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, "_mlflow_api", lambda uri: pytest.fail("Must validate location first"))
    with pytest.raises(ValueError, match="local SQLite/file store or loopback"):
        _run(dataset, tmp_path, tracking_uri="https://remote.example.org")


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


def test_holdout_abstention_logs_zero_coverage_but_no_invented_error_score(dataset, tmp_path, tracking, monkeypatch):
    days = dataset[-1]

    def missing_holdout(self, start, end, loo=False):
        return SimpleNamespace(errors=[], loo=_fake_loo(start, end, omit_predictions=start == days[-2]),
                               filled=_fake_filled(start, end))

    monkeypatch.setattr(CurveFiller, "run", missing_holdout)
    tracked = _run(dataset, tmp_path, grid={"basis_mode": ["auto"]})
    parent = tracking.runs[tracked.run_id]
    assert parent["metrics"]["validation.overall.coverage"] == 0.0
    assert "validation.overall.score" not in parent["metrics"]
    assert not any(key.startswith("validation.") and key.endswith("mae_model") for key in parent["metrics"])
    metadata = json.loads((tracked.output_dir / "metadata.json").read_text())
    assert metadata["validation_score"] is None
    assert metadata["_nonfinite_values"]["/validation_score"] == "nan"
    states = json.loads((tracked.output_dir / "validation_metric_states.json").read_text())
    assert states["nonfinite_metrics_not_logged"]["validation.overall.score"] == "nan"


def test_infinite_baseline_relative_score_retains_its_state(dataset, tmp_path, tracking, monkeypatch):
    def perfect_baseline(self, start, end, loo=False):
        rows = _fake_loo(start, end)
        rows.loc[rows.method.eq("eex"), "pred"] = 100.0
        return SimpleNamespace(errors=[], loo=rows, filled=_fake_filled(start, end))

    monkeypatch.setattr(CurveFiller, "run", perfect_baseline)
    tracked = _run(dataset, tmp_path, grid={"basis_mode": ["auto"]})
    assert math.isinf(tracked.tuning.metadata["validation_score"])
    states = json.loads((tracked.output_dir / "validation_metric_states.json").read_text())
    assert states["nonfinite_metrics_not_logged"]["validation.overall.score"] == "positive_infinity"
    assert "validation.overall.score" not in tracking.runs[tracked.run_id]["metrics"]


def test_later_trial_failure_keeps_earlier_artifacts_and_terminates_open_runs(dataset, tmp_path, tracking, monkeypatch):
    original = CurveFiller.run

    def fail_second(self, start, end, loo=False):
        if self.cfg.basis_mode == "ratio":
            raise ValueError("deliberate second candidate failure")
        return original(self, start, end, loo=loo)

    monkeypatch.setattr(CurveFiller, "run", fail_second)
    with pytest.raises(ValueError, match="second candidate failure"):
        _run(dataset, tmp_path)
    assert {run["status"] for run in tracking.runs.values()} == {"FAILED"}
    first = tracking.runs["run0002"]
    assert ("predictions", "calibration_paired_predictions.csv") in first["artifacts"]
    assert ("curves", "calibration_filled.csv") in first["artifacts"]
    assert ("curves", "validation_filled.csv") not in first["artifacts"]
    assert not first["metrics"]  # The common comparison sample was never finalized.
    assert (tmp_path / "tracked" / "run0001" / "failure.json").is_file()


@pytest.mark.parametrize("failure", [RuntimeError("tracking callback failed"), KeyboardInterrupt("cancelled")])
def test_tracking_callback_failure_propagates_and_marks_runs(dataset, tmp_path, tracking, monkeypatch, failure):
    original = tracking.set_tag

    def fail_after_prediction(run_id, key, value):
        if key == "vwaps.stage" and value == "calibration_evaluated_awaiting_common_sample":
            raise failure
        return original(run_id, key, value)

    monkeypatch.setattr(tracking, "set_tag", fail_after_prediction)
    with pytest.raises(type(failure)):
        _run(dataset, tmp_path)
    expected = "KILLED" if isinstance(failure, KeyboardInterrupt) else "FAILED"
    assert {run["status"] for run in tracking.runs.values()} == {expected}
    assert (tmp_path / "tracked" / "run0001" / "trial_001" / "calibration_paired_predictions.csv").is_file()
    state = json.loads((tmp_path / "tracked" / "run0001" / "run.json").read_text())
    assert state["state"] == expected


def test_cleanup_still_terminates_runs_when_failure_tag_logging_also_fails(dataset, tmp_path, tracking, monkeypatch):
    def fail_tags(*args, **kwargs):
        raise RuntimeError("tag service unavailable")

    monkeypatch.setattr(tracking, "set_tag", fail_tags)
    with pytest.raises(RuntimeError, match="tag service unavailable") as error:
        _run(dataset, tmp_path)
    assert "cleanup could not complete" in error.value.__notes__[0]
    assert {run["status"] for run in tracking.runs.values()} == {"FAILED"}


@pytest.mark.parametrize("previous", [None, "false", "true"])
def test_termination_suppresses_sdk_emoji_urls_and_restores_environment(
        dataset, tmp_path, tracking, monkeypatch, previous):
    name = "MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"
    if previous is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, previous)
    original = tracking.set_terminated

    def cp1252_backend(run_id, status):
        if os.environ.get(name) != "true":
            raise UnicodeEncodeError("charmap", "\U0001f3c3", 0, 1, "cannot print SDK URL")
        return original(run_id, status)

    monkeypatch.setattr(tracking, "set_terminated", cp1252_backend)
    _run(dataset, tmp_path, grid={"basis_mode": ["additive"]}, log_predictions=False)
    assert os.environ.get(name) == previous
    assert {run["status"] for run in tracking.runs.values()} == {"FINISHED"}


def test_failed_finalization_corrects_uploaded_manifest_and_preserves_primary_error(
        dataset, tmp_path, tracking, monkeypatch):
    original = tracking.set_terminated
    failed = False

    def fail_once(run_id, status):
        nonlocal failed
        if status == "FINISHED" and not failed:
            failed = True
            raise RuntimeError("finalization failed")
        return original(run_id, status)

    monkeypatch.setattr(tracking, "set_terminated", fail_once)
    with pytest.raises(RuntimeError, match="finalization failed"):
        _run(dataset, tmp_path, grid={"basis_mode": ["additive"]})
    manifest = json.loads(tracking.runs["run0001"]["artifacts"]["audit", "run.json"])
    assert manifest["state"] == "FAILED"
    assert {run["status"] for run in tracking.runs.values()} == {"FAILED"}


@pytest.mark.parametrize("include_curves", [False, True])
def test_observer_payload_mutation_cannot_change_selection_or_scores(dataset, include_curves):
    cfg, own, maps, books, days = dataset
    args = (cfg, own, maps, books, days[0], days[-1], {"basis_mode": ["auto", "additive"]}, 2, 20)
    plain = tune_parameters(*args)
    events = []

    def mutate(event, payload):
        events.append(event)
        if "parameters" in payload:
            payload["parameters"]["basis_mode"] = "corrupted"
        if "available" in payload:
            payload["available"]["model_pred"] = 1e9
        if "report" in payload:
            payload["report"]["score"] = -1e9
        if "filled" in payload:
            payload["filled"]["price"] = 1e9

    observed = tune_parameters(*args, observer=mutate, include_curve_outputs=include_curves)
    assert observed.selected_config == plain.selected_config
    assert observed.metadata == plain.metadata
    pd.testing.assert_frame_equal(observed.calibration_report, plain.calibration_report)
    assert events.index("calibration_scored") > max(i for i, event in enumerate(events) if event == "calibration_evaluated")
    assert events.count("validation_started") == events.count("validation_scored") == 1
    assert events.count("curves_evaluated") == (3 if include_curves else 0)


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


def test_extended_model_grid_is_opt_in_and_preserves_untracked_scoring(dataset, tmp_path, tracking):
    cfg, own, maps, books, days = dataset
    grid = {"basis_mode": ["additive"], "other_kind_weight": [0.2, 0.6], "shape_mode": ["off", "audit"]}
    args = (cfg, own, maps, books, days[0], days[-1], grid, 2, 20)
    with pytest.raises(ValueError, match="Unsupported tuning parameters"):
        tune_parameters(*args)
    plain = tune_parameters(*args, extended_grid=True)
    tracked = _run(dataset, tmp_path, grid=grid)
    assert tracked.tuning.selected_config == plain.selected_config
    pd.testing.assert_frame_equal(tracked.tuning.calibration_report, plain.calibration_report)
    pd.testing.assert_frame_equal(tracked.tuning.validation_report, plain.validation_report)
    assert plain.metadata["n_trials"] == 4
    assert set(plain.selected_config) == {*experiments._candidates(cfg, {}, 1)[0],
                                        "other_kind_weight", "shape_mode"}


def test_extended_layer_switch_changes_real_engine_coverage_and_is_ranked_honestly(dataset, tmp_path, tracking):
    cfg, own, maps, books, days = dataset
    cfg = replace(cfg, fallback_price_window=2, fallback_spread_window=2)
    tracked = _run((cfg, own, maps, books, days), tmp_path,
                   grid={"basis_mode": ["additive"], "layer_local": [True, False]})
    rows = tracked.tuning.calibration_report.query("scope == 'overall'").set_index("layer_local")
    assert rows.loc[True, "coverage"] == 1.0
    assert rows.loc[False, "coverage"] < 1.0
    assert rows.loc[False, "n_missing"] > 0
    assert rows["n_paired"].nunique() == 1  # Accuracy uses the shared sample.
    assert tracked.tuning.selected_config["layer_local"] is True


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
