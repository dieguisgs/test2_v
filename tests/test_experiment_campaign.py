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
    data.config = replace(data.config, configuration_mode="individual", layer_hist="off")
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


def test_individual_changes_only_target_and_then_verifies_winners_together(sample, tmp_path, fake_tracking, monkeypatch):
    calls = []
    original = CurveFiller.run

    def observe(self, start, end, loo=False):
        calls.append(([(mapping.key, mapping.use) for mapping in self.maps],
                      {key: model_parameter_payload(cfg) for key, cfg in self.curve_configs.items()}, start, end))
        return original(self, start, end, loo=loo)

    monkeypatch.setattr(CurveFiller, "run", observe)
    result = run(sample, tmp_path, search_scope="individual")
    keys = [mapping.key for mapping in sample.maps]
    assert set(result.runs) == set(keys)
    assert len(calls) == 8  # Two candidates + one holdout per product, then fixed calibration + holdout.
    for index, key in enumerate(keys):
        other = keys[1 - index]
        for call, value in zip(calls[index * 3:index * 3 + 2], (0.7, 1.5)):
            roles, settings, _, _ = call
            assert dict(roles)[key] == "fill" and dict(roles)[other] == "helper"
            assert settings[key]["tau_log"] == value
            assert settings[other]["tau_log"] == (2.0 if index == 0 else 0.4)
        tuned = result.runs[key].tuning
        assert tuned.validation_report.query("scope == 'overall'").n_curves.item() == 1
        assert tuned.metadata["evaluation_filters"]["min_volume"] == 0
    assert len({tracked.tuning.metadata["validation_start"] for tracked in result.runs.values()}) == 1
    assert result.combined is not None
    assert result.combined.tuning.selected_config == {}
    assert result.combined.tuning.metadata["optimization"] is False
    for key in keys:
        assert calls[-1][1][key]["tau_log"] == result.runs[key].tuning.selected_config["tau_log"]
    assert result.combined.tuning.validation_report.query("scope == 'overall'").n_curves.item() == 2
    assert len(result.summary) == 2
    manifest = json.loads((result.output_dir / "campaign.json").read_text())
    assert manifest["state"] == "FINISHED" and manifest["combined_run_id"] == result.combined.run_id
    proposed = json.loads((result.output_dir / "proposed_product_parameters.json").read_text())
    assert proposed["applied_to_production"] is False and len(proposed["products"]) == 2
    assert sample.config.tau_log == 0.5  # Research never modifies input configs/tables.
    for tracked in result.runs.values():
        parent = fake_tracking.runs[tracked.run_id]
        assert parent["tags"]["vwaps.search_scope"] == "individual"
        assert ("campaign", "summary.csv") in parent["artifacts"]


def test_global_ignores_mapping_overrides_and_grid_supersedes_command_base(sample, tmp_path, fake_tracking, monkeypatch):
    sample.config = replace(sample.config, command_overrides={"tau_log": 99.0})
    calls = []
    original = CurveFiller.run

    def observe(self, start, end, loo=False):
        calls.append({cfg.tau_log for cfg in self.curve_configs.values()})
        return original(self, start, end, loo=loo)

    monkeypatch.setattr(CurveFiller, "run", observe)
    result = run(sample, tmp_path, search_scope="global")
    assert calls[:2] == [{0.7}, {1.5}]
    assert set(result.runs) == {None} and result.combined is None


def test_explicit_product_grid_replaces_common_and_unselected_remains_helper(sample, tmp_path, fake_tracking):
    key = sample.maps[0].key
    identity = dict(zip(("product", "region", "unit"), key))
    result = run(sample, tmp_path, search_scope="individual", selected_curves=[identity],
                 product_grids=[{**identity, "parameter_grid": {"basis_mode": ["additive"]}}])
    tuned = result.runs[key]
    assert tuned.tuning.metadata["n_trials"] == 1
    assert tuned.tuning.selected_config["tau_log"] == 0.4
    context = json.loads((tuned.output_dir / "trial_001" / "effective_curve_configurations.json").read_text())
    settings = {row["region"]: row["parameters"] for row in context["curves"]}
    assert settings["DE"]["tau_log"] == 0.4 and settings["FR"]["tau_log"] == 2.0


def test_volume_and_deviation_grid_keeps_difficult_truths_in_identical_exam(sample):
    result = tune_parameters(sample.config, sample.vwaps, sample.maps, sample.books,
                             sample.start, sample.end,
                             {"min_volume": [0.0, 100000.0], "max_anchor_dev": [0.0, 0.000001]},
                             2, 4, target_curve=sample.maps[0].key, extended_grid=True,
                             fixed_evaluation=True)
    rows = result.calibration_report.query("scope == 'overall'")
    assert len(rows) == 4 and rows.n_baseline.nunique() == 1
    assert rows.n_baseline.min() >= 20


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


def test_json_plan_is_strict_and_example_resolves(sample, tmp_path):
    plan = campaigns.load_search_plan(Path(__file__).parents[1] / "notebooks/search_plan.example.json")
    _, searches = campaigns.prepare_search_plan(sample.config, sample.maps, max_trials=24, **plan)
    assert len(searches) == 2 and len(searches[0][1]["basis_mode"]) == 2
    path = tmp_path / "invalid.json"
    path.write_text('{"parameter_grid": {}, "parameter_grid": {}}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        campaigns.load_search_plan(path)


def test_price_logging_opt_out_applies_to_every_search_and_combined_check(sample, tmp_path, fake_tracking):
    result = run(sample, tmp_path, search_scope="individual", grid={}, log_predictions=False)
    assert not list(result.output_dir.rglob("*_filled.csv"))
    assert not list(result.output_dir.rglob("*paired_predictions.csv"))
    assert not any(group in ("curves", "predictions") for item in fake_tracking.runs.values()
                   for group, _ in item["artifacts"])
    assert all(item["tags"].get("vwaps.campaign_state") == "FINISHED"
               for item in fake_tracking.runs.values() if "vwaps.protocol" in item["tags"])


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


def test_sparse_target_fails_before_any_campaign_is_created(sample, tmp_path, fake_tracking):
    latest = sample.vwaps.date.max()
    sample.vwaps = sample.vwaps.loc[~(sample.vwaps.region.eq("FR") & sample.vwaps.date.ge(latest))]
    previous = sorted(sample.vwaps.date.unique())[-2]
    sample.vwaps = sample.vwaps.loc[~(sample.vwaps.region.eq("FR") & sample.vwaps.date.ge(previous))]
    with pytest.raises(ValueError, match="holdout observation"):
        run(sample, tmp_path, search_scope="individual")
    assert not fake_tracking.runs


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
