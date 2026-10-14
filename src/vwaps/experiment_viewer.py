"""Read saved experiment curves without rerunning models or opening production inputs.

Full refill snapshots contain visible originals. They complement, but never
replace, the held-out predictions and metrics used to select a candidate.
"""

from __future__ import annotations

from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

import pandas as pd

from vwaps.experiments import _local_artifact_uri, _local_tracking_uri, _mlflow_api
from vwaps.visualization import load_output


PROTOCOL = "calibration_grid_then_single_winner_holdout"
RUN_COLUMNS = ["run_id", "run_name", "status", "start_time", "data_label", "eex_offset_days",
               "search_scope", "campaign_id", "campaign_state"]
TRIAL_COLUMNS = ["run_id", "trial_id", "selected", "status", "parameters",
                 "calibration_curves", "validation_curves", "calibration_score", "calibration_coverage"]
CURVE_ARTIFACTS = {stage: f"curves/{stage}_filled.csv" for stage in ("calibration", "validation")}


def _identifier(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
        raise ValueError("Invalid MLflow run identifier")
    return value


def _client(tracking_uri: str):
    if not isinstance(tracking_uri, str) or not tracking_uri.strip() or not _local_tracking_uri(tracking_uri):
        raise ValueError("Curve inspection requires a local SQLite/file store or loopback MLflow server")
    return _mlflow_api(tracking_uri)[0]


def _parent(client, run_id: str):
    run = client.get_run(_identifier(run_id))
    if run.data.tags.get("vwaps.protocol") != PROTOCOL or run.data.tags.get("mlflow.parentRunId"):
        raise ValueError("Select a VWAPS tuning parent run")
    return run


def _check_artifact_location(run) -> None:
    uri = run.info.artifact_uri
    parsed = urlparse(uri)
    proxy = (parsed.scheme == "mlflow-artifacts"
             and parsed.hostname in (None, "localhost", "127.0.0.1", "::1"))
    if not (_local_artifact_uri(uri) or proxy):
        raise ValueError("Curve inspection requires local files or the local MLflow artifact proxy")


def list_experiment_runs(tracking_uri: str, experiment_name: str, *, limit: int = 100) -> pd.DataFrame:
    """List recent tuning parents, including explicit statuses for interrupted runs.

    A missing experiment returns an empty catalog; inspecting never creates one.
    The limit bounds the dropdown size and defaults to the most recent 100 runs.
    """
    if not isinstance(experiment_name, str) or not experiment_name.strip():
        raise ValueError("experiment_name must be a nonempty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000")
    client = _client(tracking_uri)
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return pd.DataFrame(columns=RUN_COLUMNS)
    runs = client.search_runs([experiment.experiment_id],
                              filter_string=f"tags.`vwaps.protocol` = '{PROTOCOL}'",
                              order_by=["attributes.start_time DESC"], max_results=limit)
    rows = []
    for run in runs:
        if run.data.tags.get("mlflow.parentRunId"):
            continue
        rows.append({
            "run_id": run.info.run_id,
            "run_name": run.data.tags.get("mlflow.runName", run.info.run_id),
            "status": run.info.status,
            "start_time": pd.to_datetime(run.info.start_time, unit="ms", utc=True),
            "data_label": run.data.tags.get("vwaps.data_label", ""),
            "eex_offset_days": run.data.params.get("eex_offset_days", "0"),
            "search_scope": run.data.tags.get("vwaps.search_scope", "global"),
            "campaign_id": run.data.tags.get("vwaps.campaign_id", ""),
            "campaign_state": run.data.tags.get("vwaps.campaign_state", ""),
        })
    return pd.DataFrame(rows, columns=RUN_COLUMNS)


def list_trial_runs(tracking_uri: str, parent_run_id: str) -> pd.DataFrame:
    """List all child trials and artifact availability without downloading curves.

    Older runs and disabled price logging remain visible with unavailable curve
    flags. Validation can be inspected only for the selected candidate.
    """
    client = _client(tracking_uri)
    parent = _parent(client, parent_run_id)
    rows, token = [], None
    while True:
        page = client.search_runs([parent.info.experiment_id],
                                  filter_string=f"tags.`mlflow.parentRunId` = '{parent_run_id}'",
                                  max_results=1000, page_token=token)
        for run in page:
            _check_artifact_location(run)
            artifacts = {item.path for item in client.list_artifacts(run.info.run_id, "curves")
                         if not item.is_dir}
            selected = run.data.tags.get("vwaps.selected") == "true"
            raw_trial = run.data.tags.get("vwaps.trial_id", "")
            trial_id = int(raw_trial) if raw_trial.isdigit() else None
            rows.append({
                "run_id": run.info.run_id, "trial_id": trial_id,
                "selected": selected, "status": run.info.status,
                "parameters": dict(run.data.params),
                "calibration_curves": CURVE_ARTIFACTS["calibration"] in artifacts,
                "validation_curves": selected and CURVE_ARTIFACTS["validation"] in artifacts,
                "calibration_score": run.data.metrics.get("calibration.overall.score", float("nan")),
                "calibration_coverage": run.data.metrics.get("calibration.overall.coverage", float("nan")),
            })
        token = getattr(page, "token", None)
        if not token:
            break
    return (pd.DataFrame(rows, columns=TRIAL_COLUMNS)
            .sort_values(["trial_id", "run_id"], na_position="last").reset_index(drop=True))


def load_trial_curves(tracking_uri: str, parent_run_id: str, trial_run_id: str, stage: str,
                      *, cache_dir: str | Path) -> pd.DataFrame:
    """Download and normalize one saved full-curve snapshot from its exact child.

    Missing snapshots require a new tracked experiment with price logging on.
    Current input files are never substituted for an old run's unavailable data.
    The temporary download is removed after loading; the authoritative artifact
    remains in MLflow. No existing production output or tracking record is written.
    """
    if stage not in CURVE_ARTIFACTS:
        raise ValueError("stage must be calibration or validation")
    _identifier(trial_run_id)
    client = _client(tracking_uri)
    parent = _parent(client, parent_run_id)
    trial = client.get_run(trial_run_id)
    if (trial.info.experiment_id != parent.info.experiment_id
            or trial.data.tags.get("mlflow.parentRunId") != parent_run_id):
        raise ValueError("The selected trial does not belong to this tuning parent")
    if stage == "validation" and trial.data.tags.get("vwaps.selected") != "true":
        raise ValueError("Validation curves are available only for the selected candidate")
    _check_artifact_location(trial)
    artifact = CURVE_ARTIFACTS[stage]
    available = {item.path for item in client.list_artifacts(trial_run_id, "curves") if not item.is_dir}
    if artifact not in available:
        raise FileNotFoundError(
            f"This trial has no saved {stage} curves. Older runs or LOG_PREDICTIONS=False do not "
            "contain these snapshots. Run a new tracked experiment with LOG_PREDICTIONS=True; "
            "the viewer never rebuilds an old run using current inputs."
        )
    root = Path(cache_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="curve-view-", dir=root) as destination:
        path = Path(client.download_artifacts(trial_run_id, artifact, dst_path=destination)).resolve()
        if not path.is_relative_to(Path(destination).resolve()):
            raise ValueError("The artifact download returned a path outside its requested directory")
        frame = load_output(path)
    frame.attrs.update(parent_run_id=parent_run_id, trial_run_id=trial_run_id,
                       stage=stage, artifact_path=artifact,
                       curve_output_kind="full_refill_with_originals")
    return frame
