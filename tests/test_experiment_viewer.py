"""Saved experiment inspection preserves trial identity and never recomputes curves."""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from vwaps import experiment_viewer as viewer


URI = "http://127.0.0.1:5000"


def run(identifier, *, parent=None, selected=False, experiment="7", status="FINISHED"):
    tags = ({"mlflow.parentRunId": parent, "vwaps.trial_id": identifier[-1],
             "vwaps.selected": str(selected).lower()} if parent else
            {"vwaps.protocol": viewer.PROTOCOL, "mlflow.runName": "Synthetic grid",
             "vwaps.data_label": "synthetic"})
    return SimpleNamespace(
        info=SimpleNamespace(run_id=identifier, experiment_id=experiment, status=status,
                             start_time=1_700_000_000_000, artifact_uri="mlflow-artifacts:/7/artifacts"),
        data=SimpleNamespace(tags=tags, params={"eex_offset_days": "-1", "basis_mode": "additive"},
                             metrics={"calibration.overall.score": 0.6, "calibration.overall.coverage": 0.9}),
    )


class Page(list):
    def __init__(self, values, token=None):
        super().__init__(values)
        self.token = token


class Reader:
    def __init__(self):
        self.runs = {"parent": run("parent"), "trial1": run("trial1", parent="parent"),
                     "trial2": run("trial2", parent="parent", selected=True),
                     "trial3": run("trial3", parent="parent", status="FAILED")}
        self.paths = {"trial1": ["curves/calibration_filled.csv"],
                      "trial2": list(viewer.CURVE_ARTIFACTS.values()), "trial3": []}
        self.rows = [dict(reference_date="2026-10-07", product="Power", region="DE", unit="EUR/MWh",
                         tenor="M+1", kind="Month", delivery_start="2026-11-01", delivery_end="2026-12-01",
                         price=-5, eex_settle=0, eex_asof="2026-10-06", eex_offset_days=-1,
                         eex_cutoff_date="2026-10-06", source="eex+local")]
        self.calls, self.downloads = [], []
        self.pages = False

    def get_experiment_by_name(self, name):
        self.calls.append(("experiment", name))
        return None if name == "absent" else SimpleNamespace(experiment_id="7")

    def get_run(self, identifier):
        self.calls.append(("run", identifier))
        return self.runs[identifier]

    def search_runs(self, experiments, filter_string, **kwargs):
        self.calls.append(("search", experiments, filter_string, kwargs))
        if "vwaps.protocol" in filter_string:
            return Page([self.runs["parent"]])
        if self.pages:
            return Page([self.runs["trial1"]], "next") if kwargs["page_token"] is None else Page(
                [self.runs["trial2"], self.runs["trial3"]])
        return Page([self.runs[name] for name in ("trial3", "trial2", "trial1")])

    def list_artifacts(self, identifier, path):
        self.calls.append(("list", identifier, path))
        return [SimpleNamespace(path=name, is_dir=False) for name in self.paths[identifier]]

    def download_artifacts(self, identifier, path, dst_path):
        target = Path(dst_path) / path
        target.parent.mkdir(parents=True)
        pd.DataFrame(self.rows).to_csv(target, index=False)
        self.downloads.append((identifier, path, target))
        return str(target)


@pytest.fixture
def reader(monkeypatch):
    client = Reader()
    monkeypatch.setattr(viewer, "_mlflow_api", lambda uri: (client, None))
    return client


def test_parent_catalog_is_read_only_and_reports_date_policy(reader):
    parents = viewer.list_experiment_runs(URI, "grid", limit=20)
    assert list(parents.run_id) == ["parent"]
    assert parents.iloc[0].eex_offset_days == "-1"
    assert parents.iloc[0].start_time.tzinfo is not None
    assert reader.calls[-1][-1] == {"order_by": ["attributes.start_time DESC"], "max_results": 20}
    assert viewer.list_experiment_runs(URI, "absent").empty


def test_trial_catalog_paginates_and_distinguishes_winner_legacy_and_failed_runs(reader):
    reader.pages = True
    trials = viewer.list_trial_runs(URI, "parent")
    assert list(trials.run_id) == ["trial1", "trial2", "trial3"]
    assert list(trials.calibration_curves) == [True, True, False]
    assert list(trials.validation_curves) == [False, True, False]
    assert trials.iloc[2].status == "FAILED"
    assert trials.iloc[1].parameters["basis_mode"] == "additive"
    assert reader.downloads == []


def test_loads_exact_saved_artifact_and_cleans_temporary_copy(reader, tmp_path):
    frame = viewer.load_trial_curves(URI, "parent", "trial1", "calibration", cache_dir=tmp_path)
    assert frame.iloc[0].price == -5 and frame.iloc[0].eex_settle == 0
    assert frame.iloc[0].eex_age_days == 1
    assert frame.attrs["trial_run_id"] == "trial1"
    assert frame.attrs["curve_output_kind"] == "full_refill_with_originals"
    assert reader.downloads[0][:2] == ("trial1", "curves/calibration_filled.csv")
    assert not reader.downloads[0][2].exists() and list(tmp_path.iterdir()) == []
    # A later click loads the saved artifact again instead of silently reusing a
    # stale local cache when a running experiment has completed more work.
    reader.rows[0]["price"] = 8
    assert viewer.load_trial_curves(URI, "parent", "trial1", "calibration", cache_dir=tmp_path).iloc[0].price == 8


def test_validation_artifact_is_readable_only_for_winner(reader, tmp_path):
    assert not viewer.load_trial_curves(URI, "parent", "trial2", "validation", cache_dir=tmp_path).empty
    reader.paths["trial1"].append("curves/validation_filled.csv")
    with pytest.raises(ValueError, match="only for the selected"):
        viewer.load_trial_curves(URI, "parent", "trial1", "validation", cache_dir=tmp_path)
    assert not viewer.list_trial_runs(URI, "parent").iloc[0].validation_curves


@pytest.mark.parametrize("mismatch", ["parent", "experiment"])
def test_rejects_trial_from_another_parent_or_experiment_before_download(reader, tmp_path, mismatch):
    if mismatch == "parent":
        reader.runs["trial1"].data.tags["mlflow.parentRunId"] = "other"
    else:
        reader.runs["trial1"].info.experiment_id = "8"
    with pytest.raises(ValueError, match="does not belong"):
        viewer.load_trial_curves(URI, "parent", "trial1", "calibration", cache_dir=tmp_path)
    assert reader.downloads == []


def test_missing_legacy_or_disabled_artifact_never_attempts_reconstruction(reader, tmp_path):
    with pytest.raises(FileNotFoundError, match="LOG_PREDICTIONS=True"):
        viewer.load_trial_curves(URI, "parent", "trial3", "calibration", cache_dir=tmp_path)
    assert reader.downloads == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("run_id", ["../parent", "x' OR 1=1", "", None])
def test_run_identifiers_cannot_change_queries_or_local_paths(reader, run_id):
    with pytest.raises(ValueError, match="identifier"):
        viewer.list_trial_runs(URI, run_id)
    assert reader.calls == []


def test_rejects_unrelated_parent_and_remote_tracking_before_reading_data(reader):
    with pytest.raises(ValueError, match="parent run"):
        viewer.list_trial_runs(URI, "trial1")
    reader.calls.clear()
    with pytest.raises(ValueError, match="local SQLite/file"):
        viewer.list_experiment_runs("https://remote.example.org", "grid")
    assert reader.calls == []


def test_remote_artifact_location_is_not_followed(reader, tmp_path):
    reader.runs["trial1"].info.artifact_uri = "s3://foreign-bucket/artifacts"
    with pytest.raises(ValueError, match="artifact proxy"):
        viewer.load_trial_curves(URI, "parent", "trial1", "calibration", cache_dir=tmp_path)
    assert reader.downloads == []


def test_download_cannot_substitute_a_file_outside_the_requested_folder(reader, tmp_path, monkeypatch):
    outside = tmp_path / "unrelated.csv"
    outside.write_text("do not read or remove", encoding="utf-8")
    monkeypatch.setattr(reader, "download_artifacts", lambda *args, **kwargs: str(outside))
    with pytest.raises(ValueError, match="outside"):
        viewer.load_trial_curves(URI, "parent", "trial1", "calibration", cache_dir=tmp_path / "cache")
    assert outside.read_text(encoding="utf-8") == "do not read or remove"


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5])
def test_catalog_limit_is_bounded(reader, limit):
    with pytest.raises(ValueError, match="limit"):
        viewer.list_experiment_runs(URI, "grid", limit=limit)
    assert reader.calls == []
