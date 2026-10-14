"""Read-only notebook controls for saved MLflow trial curve snapshots."""

from __future__ import annotations

from html import escape
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from vwaps.curve_viewer import create_curve_viewer
from vwaps.experiment_viewer import list_experiment_runs, list_trial_runs, load_trial_curves

if TYPE_CHECKING:
    from ipywidgets import Widget


def create_experiment_browser(
    tracking_uri: str, *, experiment_name: str,
    cache_dir: str | Path, selected_parent_run_id: str | None = None,
) -> Widget:
    """Return selectors for previous runs, trials and their saved curve outputs.

    Only explicit ``Load saved curves`` downloads a snapshot into cache_dir.
    Loading does not refit a model, read current input files or alter a run.
    Calibration is available for every saved trial; validation is restricted
    to the selected trial. The caller displays the returned widget.
    """
    try:
        import ipywidgets as widgets
    except ImportError as exc:
        raise ImportError(
            "Missing experiment viewer dependencies; run: uv sync --group notebook --group experiment"
        ) from exc

    experiment = widgets.Text(value=experiment_name, description="Experiment:",
                              layout=widgets.Layout(width="75%"))
    refresh = widgets.Button(description="Refresh runs", button_style="info")
    parent = widgets.Dropdown(description="Run:", layout=widgets.Layout(width="95%"))
    trial = widgets.Dropdown(description="Trial:", layout=widgets.Layout(width="95%"))
    stage = widgets.Dropdown(description="Stage:", layout=widgets.Layout(width="95%"))
    load = widgets.Button(description="Load saved curves", button_style="primary", disabled=True)
    status = widgets.HTML()
    details = widgets.HTML()
    curves = widgets.VBox()
    explanation = widgets.HTML(
        "<b>Saved full curves:</b> observed own prices remain visible. "
        "These plots inspect curve shape and coverage; they do not measure prediction accuracy. "
        "Use the hidden-observation backtest reports for accuracy. "
        "Validation snapshots exist only for the selected trial."
    )
    state = {"updating": False, "trials": pd.DataFrame()}

    def message(text: str) -> None:
        status.value = escape(text)

    def clear_curves() -> None:
        def close_tree(widget) -> None:
            for child in getattr(widget, "children", ()):
                close_tree(child)
            widget.close()
        for widget in curves.children:
            close_tree(widget)
        curves.children = ()

    def selected_trial() -> dict | None:
        rows = state["trials"]
        if trial.value is None or rows.empty:
            return None
        match = rows.loc[rows["run_id"].eq(trial.value)]
        return None if match.empty else match.iloc[0].to_dict()

    def number(value) -> str:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "unavailable"
        return "unavailable" if math.isnan(value) else f"{value:.5g}"

    def trial_changed(change=None) -> None:
        if state["updating"]:
            return
        clear_curves()
        row = selected_trial()
        state["updating"] = True
        try:
            options = []
            if row is not None and row["calibration_curves"]:
                options.append(("Calibration (all trials)", "calibration"))
            if row is not None and row["selected"] and row["validation_curves"]:
                options.append(("Validation (selected trial only)", "validation"))
            stage.options = options
            stage.value = options[0][1] if options else None
            load.disabled = not options
        finally:
            state["updating"] = False
        if row is None:
            details.value = ""
            message("Select an experiment run and trial.")
        else:
            parameters = json.dumps(row["parameters"], indent=2, sort_keys=True, default=str)
            details.value = (
                f"<b>Trial {escape(str(row['trial_id']))}</b> "
                f"| Status: {escape(str(row['status']))} "
                f"| Selected: {bool(row['selected'])} "
                f"| Calibration score: {escape(number(row['calibration_score']))} "
                f"| Coverage: {escape(number(row['calibration_coverage']))}"
                f"<pre>{escape(parameters)}</pre>"
            )
            if options:
                message("Choose a saved stage, then click Load saved curves. No model will be rerun.")
            else:
                message(
                    "No full-curve snapshots are saved for this trial. Older runs, disabled "
                    "LOG_PREDICTIONS or incomplete runs may lack them. To create snapshots, "
                    "run a new experiment with LOG_PREDICTIONS=True. This viewer never reconstructs "
                    "old results from current inputs."
                )

    def parent_changed(change=None) -> None:
        if state["updating"]:
            return
        clear_curves()
        details.value = ""
        state["updating"] = True
        try:
            state["trials"] = pd.DataFrame()
            trial.options = []
            stage.options = []
            load.disabled = True
            if parent.value is None:
                message("No experiment run is selected.")
                return
            rows = list_trial_runs(tracking_uri, parent.value)
            state["trials"] = rows
            if rows.empty:
                message("This run has no recorded trials. Refresh after the experiment starts evaluating candidates.")
                return
            options = [
                (f"Trial {row['trial_id']} | {'selected | ' if row['selected'] else ''}"
                 f"{row['status']} | score={number(row['calibration_score'])} | "
                 f"coverage={number(row['calibration_coverage'])} | {row['run_id'][:8]}", row["run_id"])
                for row in rows.to_dict("records")
            ]
            trial.options = options
            winners = rows.loc[rows["selected"], "run_id"]
            trial.value = winners.iloc[0] if len(winners) else options[0][1]
        except Exception as exc:
            message(f"Cannot list saved trials: {exc}")
            return
        finally:
            state["updating"] = False
        trial_changed()

    def refresh_runs(change=None) -> None:
        previous = parent.value
        state["updating"] = True
        clear_curves()
        details.value = ""
        try:
            parent.options = []
            trial.options = []
            stage.options = []
            load.disabled = True
            rows = list_experiment_runs(tracking_uri, experiment.value.strip())
            if rows.empty:
                message("No tracked tuning runs were found. Check the experiment name and click Refresh runs.")
                return
            options = [
                (f"{row['start_time']} | {row['run_name']} | {row['status']} | "
                 f"{row['data_label']} | EEX offset={row['eex_offset_days']} | "
                 f"{('Campaign ' + row.get('campaign_state', '') + ' | ') if row.get('campaign_id') else ''}"
                 f"{row['run_id'][:8]}", row["run_id"])
                for row in rows.to_dict("records")
            ]
            parent.options = options
            values = set(rows["run_id"])
            preferred = previous if previous in values else selected_parent_run_id
            parent.value = preferred if preferred in values else options[0][1]
        except Exception as exc:
            message(f"Cannot list experiment runs: {exc}")
            return
        finally:
            state["updating"] = False
        parent_changed()

    def stage_changed(change=None) -> None:
        if not state["updating"]:
            clear_curves()
            message("Click Load saved curves to display this stage.")

    def load_curves(change=None) -> None:
        if parent.value is None or trial.value is None or stage.value is None:
            message("Select a run, trial and saved stage first.")
            return
        clear_curves()
        load.disabled = True
        try:
            frame = load_trial_curves(
                tracking_uri, parent.value, trial.value, stage.value, cache_dir=cache_dir,
            )
            curves.children = (create_curve_viewer(frame),)
            message(
                f"Loaded {len(frame):,} saved curve rows for trial {trial.value}, stage {stage.value}. "
                "Own observations are visible; use backtest metrics to assess accuracy."
            )
        except Exception as exc:
            message(f"Cannot load saved curves: {exc}")
        finally:
            load.disabled = stage.value is None

    refresh.on_click(refresh_runs)
    parent.observe(parent_changed, names="value")
    trial.observe(trial_changed, names="value")
    stage.observe(stage_changed, names="value")
    load.on_click(load_curves)
    browser = widgets.VBox([
        explanation, widgets.HBox([experiment, refresh]), parent, trial, stage,
        load, status, details, curves,
    ])
    refresh_runs()
    return browser
