"""Experiment widgets inspect saved artifacts without rerunning models."""

import json
from pathlib import Path

import pandas as pd
import pytest

widgets = pytest.importorskip("ipywidgets")

import vwaps.experiment_browser as browser


@pytest.fixture
def backend(monkeypatch):
    calls = []
    parents = pd.DataFrame([
        dict(run_id="parent_new", run_name="new run", status="FINISHED", start_time="2026-10-07",
             data_label="synthetic", eex_offset_days=-1),
        dict(run_id="parent_old", run_name="old run", status="FAILED", start_time="2026-10-06",
             data_label="real", eex_offset_days=0),
    ])
    trials = pd.DataFrame([
        dict(run_id="trial_1", trial_id=1, selected=False, status="FINISHED", parameters={"basis_mode": "ratio"},
             calibration_curves=True, validation_curves=False, calibration_score=0.8, calibration_coverage=1),
        dict(run_id="trial_2", trial_id=2, selected=True, status="FINISHED", parameters={"basis_mode": "additive"},
             calibration_curves=True, validation_curves=True, calibration_score=0.7, calibration_coverage=1),
    ])
    def list_runs(uri, experiment):
        calls.append(("runs", uri, experiment))
        return parents.copy()
    def list_trials(uri, parent):
        calls.append(("trials", uri, parent))
        return trials.copy()
    def load(uri, parent, trial, stage, *, cache_dir):
        calls.append(("load", uri, parent, trial, stage, cache_dir))
        return pd.DataFrame({"saved_only": [1, 2]})
    monkeypatch.setattr(browser, "list_experiment_runs", list_runs)
    monkeypatch.setattr(browser, "list_trial_runs", list_trials)
    monkeypatch.setattr(browser, "load_trial_curves", load)
    monkeypatch.setattr(browser, "create_curve_viewer", lambda frame: widgets.HTML(f"Saved rows: {len(frame)}"))
    return calls, trials


def controls(viewer):
    explanation, experiment_controls, parent, trial, stage, load, status, details, curves = viewer.children
    return experiment_controls, parent, trial, stage, load, status, details, curves


def test_browses_previous_runs_and_loads_only_when_requested(tmp_path, backend):
    calls, _ = backend
    viewer = browser.create_experiment_browser(
        "http://127.0.0.1:5000", experiment_name="curves", selected_parent_run_id="parent_old", cache_dir=tmp_path,
    )
    experiment_controls, parent, trial, stage, load, status, details, curves = controls(viewer)
    assert parent.value == "parent_old"
    assert trial.value == "trial_2"
    assert stage.value == "calibration"
    assert [value for _, value in stage.options] == ["calibration", "validation"]
    assert not any(call[0] == "load" for call in calls)
    stage.value = "validation"
    load.click()
    assert calls[-1] == ("load", "http://127.0.0.1:5000", "parent_old", "trial_2", "validation", tmp_path)
    assert curves.children[0].value == "Saved rows: 2"
    assert "Own observations are visible" in status.value
    assert "additive" in details.value


def test_nonwinner_has_only_calibration_and_switching_trials_clears_previous_plot(tmp_path, backend):
    calls, _ = backend
    viewer = browser.create_experiment_browser("local", experiment_name="curves", cache_dir=tmp_path)
    _, _, trial, stage, load, _, _, curves = controls(viewer)
    load.click()
    assert curves.children
    trial.value = "trial_1"
    assert curves.children == ()
    assert stage.options == (("Calibration (all trials)", "calibration"),)
    assert not load.disabled
    load.click()
    assert calls[-1][3:5] == ("trial_1", "calibration")


def test_legacy_or_privacy_disabled_run_explains_missing_snapshots(tmp_path, backend):
    calls, trials = backend
    trials["calibration_curves"] = False
    trials["validation_curves"] = False
    viewer = browser.create_experiment_browser("local", experiment_name="curves", cache_dir=tmp_path)
    _, _, _, stage, load, status, _, curves = controls(viewer)
    assert load.disabled and stage.value is None
    assert "LOG_PREDICTIONS=True" in status.value
    assert "never reconstructs" in status.value
    assert not any(call[0] == "load" for call in calls)
    assert curves.children == ()


def test_refresh_changes_experiment_and_download_failure_is_actionable(tmp_path, backend, monkeypatch):
    calls, _ = backend
    viewer = browser.create_experiment_browser("local", experiment_name="first", cache_dir=tmp_path)
    experiment_controls, _, _, _, load, status, _, curves = controls(viewer)
    experiment_controls.children[0].value = "other"
    experiment_controls.children[1].click()
    assert ("runs", "local", "other") in calls
    def fail(*args, **kwargs):
        raise ValueError("Saved artifact unavailable; run a new experiment with LOG_PREDICTIONS=True")
    monkeypatch.setattr(browser, "load_trial_curves", fail)
    load.click()
    assert "Cannot load saved curves" in status.value
    assert "LOG_PREDICTIONS=True" in status.value
    assert not load.disabled and curves.children == ()


def test_no_runs_has_an_explicit_message_and_disabled_loading(tmp_path, backend, monkeypatch):
    monkeypatch.setattr(browser, "list_experiment_runs", lambda *_: pd.DataFrame())
    viewer = browser.create_experiment_browser("local", experiment_name="unknown", cache_dir=tmp_path)
    _, parent, _, _, load, status, _, _ = controls(viewer)
    assert parent.value is None and load.disabled
    assert "No tracked tuning runs" in status.value


def test_saved_viewer_notebook_cell_can_run_after_settings_without_tuning(tmp_path, backend, monkeypatch):
    from types import SimpleNamespace
    import IPython.display
    import vwaps.mlflow_server
    monkeypatch.setattr(IPython.display, "display", lambda *_: None)
    monkeypatch.setattr(vwaps.mlflow_server, "start_mlflow_server", lambda *_args, **_kwargs: SimpleNamespace(url="local"))
    path = Path(__file__).resolve().parents[1] / "notebooks" / "backtest_mlflow.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    cells = {cell["id"]: "".join(cell["source"]) for cell in notebook["cells"]}
    scope = {}
    exec(cells["editable-settings"], scope)
    scope.update(PROJECT_ROOT=str(path.parents[1]), MLFLOW_STORAGE=str(tmp_path))
    exec(cells["saved-curve-viewer"], scope)
    exec(cells["optional-stop"], scope)
    assert "EXPERIMENT_BROWSER" in scope
    assert "dataset" not in scope and "result" not in scope
    assert not any(call[0] == "load" for call in backend[0])
