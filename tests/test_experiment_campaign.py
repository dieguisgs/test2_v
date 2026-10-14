"""Individual research isolates candidate changes and verifies frozen winners."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import vwaps.experiment_campaign as campaigns
import vwaps.experiments as experiments
from vwaps.config import load_config
from vwaps.experiment_data import make_demo_dataset
from vwaps.experiment_viewer import list_experiment_runs, list_trial_runs, load_trial_curves
from vwaps.fill import CurveFiller
from vwaps.product_config import model_parameter_payload
from vwaps.tuning import tune_parameters


@pytest.fixture
def sample(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("", encoding="utf-8")
    data = make_demo_dataset(load_config(config_file), periods=7)
    data.config = replace(data.config, configuration_mode="individual")
    data.maps = [replace(mapping, parameter_overrides={"tau_log": 0.4 if index == 0 else 2.0,
                                                       "layer_hist": "off"})
                 for index, mapping in enumerate(data.maps)]
    return data


@pytest.fixture
def fake_tracking(monkeypatch):
    # Reuse the focused recording backend already exercised by adapter tests.
    from test_experiments import FakeClient
    client = FakeClient()
    entities = SimpleNamespace(
        Metric=lambda key, value, timestamp, step: SimpleNamespace(key=key, value=value),
        Param=lambda key, value: SimpleNamespace(key=key, value=value))
    api = lambda uri: (client, entities)
    monkeypatch.setattr(experiments, "_mlflow_api", api)
    monkeypatch.setattr(campaigns, "_mlflow_api", api)
    return client


def run(sample, tmp_path, **kwargs):
    return campaigns.run_tracked_campaign(
        sample.config, sample.vwaps, sample.maps, sample.books, sample.start, sample.end,
        kwargs.pop("grid", {"tau_log": [0.7, 1.5]}), 2, 12,
        tracking_uri=kwargs.pop("tracking_uri", "http://127.0.0.1:5000"),
        experiment_name="individual-tests", output_dir=tmp_path / "campaigns",
        **kwargs)


@pytest.mark.parametrize("options", [
    {"search_scope": "typo"}, {"selected_curves": []},
    {"selected_curves": [{"product": "SYNTHETIC_POWER", "region": "DE"}]},
    {"selected_curves": [["UNKNOWN", "", ""]]},
    {"product_grids": {}},
    {"product_grids": [{"product": "SYNTHETIC_POWER", "region": "DE", "unit": "EUR/MWh",
                         "parameter_grid": {"tau_log": [0.5]}}]},
])
def test_invalid_plan_rejected_before_tracking(sample, tmp_path, monkeypatch, options):
    monkeypatch.setattr(experiments, "_mlflow_api", lambda uri: pytest.fail("Invalid plan reached tracking"))
    with pytest.raises(ValueError):
        run(sample, tmp_path, **options)
    assert not (tmp_path / "campaigns").exists()


def test_campaign_upload_failure_marks_campaign_failed_without_masking_original(sample, tmp_path, fake_tracking, monkeypatch):
    original = fake_tracking.log_artifact

    def upload(run_id, path, artifact_path=None):
        if artifact_path == "campaign" and Path(path).name == "summary.csv":
            raise RuntimeError("campaign summary upload failed")
        return original(run_id, path, artifact_path)

    monkeypatch.setattr(fake_tracking, "log_artifact", upload)
    with pytest.raises(RuntimeError, match="campaign summary upload failed"):
        run(sample, tmp_path, grid={})
    manifest = json.loads(next((tmp_path / "campaigns").rglob("campaign.json")).read_text())
    assert manifest["state"] == "FAILED"
    parent = next(item for item in fake_tracking.runs.values() if "vwaps.protocol" in item["tags"])
    assert parent["tags"]["vwaps.campaign_state"] == "FAILED"
    uploaded = json.loads(parent["artifacts"]["campaign", "campaign.json"])
    assert uploaded["state"] == "FAILED"


def test_real_mlflow_campaign_curves_survive_new_clients_and_do_not_refit(sample, tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    database = tmp_path / "campaign.sqlite"
    uri = "sqlite:///" + database.as_posix()
    result = run(sample, tmp_path, search_scope="individual", grid={"basis_mode": ["additive"]},
                 tracking_uri=uri)
    monkeypatch.setattr(CurveFiller, "run", lambda *args, **kwargs: pytest.fail("Viewing must not rerun models"))
    catalog = list_experiment_runs(uri, "individual-tests")
    assert len(catalog) == 3
    for tracked in [*result.runs.values(), result.combined]:
        trials = list_trial_runs(uri, tracked.run_id)
        assert len(trials) == 1 and trials.validation_curves.all()
        frame = load_trial_curves(uri, tracked.run_id, trials.run_id.iloc[0], "validation",
                                  cache_dir=tmp_path / "downloads")
        assert frame.price.notna().any()
        assert "configuration_id" in frame
        contexts = json.loads((tracked.output_dir / "trial_001" / "configuration_contexts.json").read_text())
        assert set(frame.configuration_context_id).issubset(contexts)
        assert contexts
