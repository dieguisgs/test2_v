"""Reusable English curve browser for normalized, saved output snapshots.

This module does not load production inputs, refit models or start a service.
Optional notebook dependencies are imported only when creating a widget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from vwaps.visualization import (
    KIND_ORDER, available_curves, contract_evolution, coverage_metrics,
    curve_plot_points, paired_means, select_curve,
)

if TYPE_CHECKING:
    from ipywidgets import Widget


def create_curve_viewer(frame: pd.DataFrame) -> Widget:
    """Return controls for a normalized frame produced by load_output().

    The caller displays the returned widget. Plots and tables are rendered
    inside its output areas. All curve identities, contract families, date
    slices, paired range means and fixed-delivery history remain selectable.
    The frame is read without modification.
    """
    try:
        import ipywidgets as widgets
        import plotly.graph_objects as go
        from IPython.display import clear_output, display
    except ImportError as exc:
        raise ImportError(
            "Missing curve viewer dependencies; run: uv sync --group notebook"
        ) from exc
    if frame.empty:
        return widgets.HTML("No saved curve rows are available for this selection.")

    METRIC_LABELS = {
        "n_observations": "Unique rows", "n_dates": "Dates", "n_price": "With price",
        "n_eex": "With EEX", "n_paired": "Price/EEX pairs", "n_missing_price": "Without price",
        "n_original": "Final originals", "n_estimated": "Final estimates",
        "n_adjusted_original": "Adjusted originals (subset)", "price_coverage": "Row coverage",
    }

    def show_metrics(frame):
        metrics = coverage_metrics(frame)
        metrics["price_coverage"] = (
            f"{100 * metrics['price_coverage']:.1f}%" if len(frame) else "No data"
        )
        display(pd.DataFrame([metrics]).rename(columns=METRIC_LABELS))

    def show_details(frame):
        fields = [
            "reference_date", "tenor", "tenor_aliases", "kind", "delivery_start", "delivery_end",
            "price", "eex_settle", "eex_asof", "eex_age_days", "eex_cutoff_date", "eex_offset_days", "source", "data_origin",
            "own_vwap", "price_before_shape", "shape_mode", "shape_status", "shape_adjustment", "flag",
            "configuration_mode", "configuration_id", "configuration_context_id", "configuration_parameters",
        ]
        display(frame[[name for name in fields if name in frame]].reset_index(drop=True))

    def add_price_traces(figure, frame, x_column, label="", show_before=False, show_own=False, connect=True):
        mode = "lines+markers" if connect else "markers"
        specifications = [
            ("price", "Final price", "#1261a0", "solid", mode),
            ("eex_settle", "EEX reference", "#777777", "dash", mode),
        ]
        if show_before and "price_before_shape" in frame:
            specifications.append(("price_before_shape", "Before shape adjustment", "#d68100", "dot", mode))
        if show_own and "own_vwap" in frame:
            specifications.append(("own_vwap", "Own observed price", "#258a45", "solid", "markers"))
        hover = [
            f"{row.tenor} · {row.kind} · {row.delivery_start:%Y-%m-%d} / {row.delivery_end:%Y-%m-%d}"
            for row in frame.itertuples()
        ]
        for column, name, color, dash, trace_mode in specifications:
            if not frame[column].notna().any():
                continue
            figure.add_trace(go.Scatter(
                x=frame[x_column], y=frame[column], name=f"{label}{name}",
                mode=trace_mode, connectgaps=False, legendgroup=column,
                showlegend=not any(trace.legendgroup == column for trace in figure.data),
                line={"color": color, "dash": dash},
                marker={"symbol": "x" if column == "own_vwap" else "circle", "size": 8},
                text=hover, hovertemplate=("%{text}<br>" + ("%{x}<br>" if x_column == "reference_date" else "")
                                           + "%{y:.6f}<extra>%{fullData.name}</extra>"),
            ))

    def finish_figure(figure, title, unit, x_title, points=None):
        figure.update_layout(
            title=title, template="plotly_white", height=600,
            xaxis_title=x_title, yaxis_title=unit or "Unit not provided",
            hovermode="closest", legend={"orientation": "h", "y": -0.2},
            margin={"b": 160},
        )
        if points is not None:
            figure.update_xaxes(
                type="category", categoryorder="array", categoryarray=points["plot_x"].tolist(),
                tickmode="array", tickvals=points["plot_x"].tolist(),
                ticktext=points["plot_label"].tolist(), tickangle=-60, automargin=True,
                range=[-0.5, len(points) - 0.5],
            )
            kinds = points["kind"].tolist()
            for index in range(1, len(kinds)):
                if kinds[index] != kinds[index - 1]:
                    figure.add_shape(
                        type="line", x0=index - 0.5, x1=index - 0.5, y0=0, y1=1,
                        xref="x", yref="paper", line={"color": "#dddddd", "dash": "dot"},
                    )
        display(figure)

    ALL_KINDS = "__all__"


    def build_browser(data):
        identities = available_curves(data)
        identity = widgets.Dropdown(
            options=[(f"{p} | region={r!r} | unit={u!r}", (p, r, u)) for p, r, u in identities],
            description="Curve:", layout=widgets.Layout(width="95%"),
        )
        kind = widgets.Dropdown(description="View:", layout=widgets.Layout(width="95%"))
        reference = widgets.Dropdown(description="Date:")
        show_before = widgets.Checkbox(value=False, description="Before shape adjustment", disabled="price_before_shape" not in data)
        show_own = widgets.Checkbox(value=True, description="Own observed prices", disabled="own_vwap" not in data)
        start = widgets.DatePicker(description="From:")
        end = widgets.DatePicker(description="To:")
        alignment = widgets.Dropdown(
            options=[("Absolute delivery (recommended)", "delivery"), ("Relative tenor: mixes delivery periods", "tenor")],
            value="delivery", description="Align by:", layout=widgets.Layout(width="480px"),
        )
        compare = widgets.Button(description="Compare range", button_style="primary")
        contract = widgets.Dropdown(description="Delivery:", layout=widgets.Layout(width="95%"))
        daily_output, range_output, evolution_output = widgets.Output(), widgets.Output(), widgets.Output()
        state = {"updating": False}

        def selected_kind():
            return None if kind.value == ALL_KINDS else kind.value

        def selected_scope():
            return select_curve(data, identity.value, kind=selected_kind())

        def daily_view(change=None):
            if state["updating"]:
                return
            with daily_output:
                clear_output(wait=True)
                if reference.value is None:
                    print("No dates are available for this selection.")
                    return
                selected = select_curve(data, identity.value, kind=selected_kind(), reference_date=reference.value)
                if selected.empty:
                    print("No rows are available for that date.")
                    return
                print("Delivery: [start, end). Coverage counts saved rows, not contracts absent from the CSV.")
                selected = curve_plot_points(selected)
                counts = selected.groupby("kind", sort=False).size()
                print(f"{len(selected)} saved contracts: " + ", ".join(f"{name}: {count}" for name, count in counts.items()))
                print("Each position is a contract; horizontal spacing does not represent time. Hover to see dates.")
                if kind.value == ALL_KINDS:
                    print("Full curve: all contract types together, with separate lines for each type.")
                if "eex_asof" in selected:
                    stale = selected["eex_age_days"].gt(0).sum()
                    unknown = selected["eex_asof"].isna().sum()
                    print(f"EEX from an earlier date: {stale} rows · EEX date not provided: {unknown}")
                show_metrics(selected)
                figure = go.Figure()
                for name, group in selected.groupby("kind", sort=False):
                    add_price_traces(
                        figure, group, "plot_x", show_before=show_before.value, show_own=show_own.value,
                    )
                finish_figure(
                    figure, f"{identity.value[0]} · {reference.value}", identity.value[2],
                    "Contracts by type and maturity", points=selected,
                )
                show_details(selected)

        def range_view(change=None):
            if state["updating"]:
                return
            with range_output:
                clear_output(wait=True)
                if start.value is None or end.value is None:
                    print("Select both dates for the range.")
                    return
                if start.value > end.value:
                    print("The start date must be on or before the end date.")
                    return
                selected = select_curve(data, identity.value, kind=selected_kind(), start=start.value, end=end.value)
                if selected.empty:
                    print("No observations are available in this range.")
                    return
                if alignment.value == "tenor":
                    print("WARNING: M+1, Q+1, etc. change delivery periods when they roll. This view mixes contracts.")
                print("Means use equal daily weights. Each point uses only matching dates with finite price AND EEX values.")
                print("n_paired shows the dates used; n_price/n_eex also include unpaired observations.")
                print("Different points may have different dates and sample sizes.")
                show_metrics(selected)
                means = paired_means(
                    data, identity.value, kind=selected_kind(), start=start.value, end=end.value, alignment=alignment.value,
                )
                if means.empty:
                    print("No comparable points.")
                    return
                means = curve_plot_points(means, alignment=alignment.value)
                x_column = "plot_x"
                figure = go.Figure()
                mode = "lines+markers"
                for name, group in means.groupby("kind", sort=False):
                    for column, title, color, dash in (
                        ("price_mean", "Mean price", "#1261a0", "solid"),
                        ("eex_mean", "Paired mean EEX", "#777777", "dash"),
                    ):
                        figure.add_trace(go.Scatter(
                            x=group[x_column], y=group[column], name=title, mode=mode,
                            connectgaps=False, line={"color": color, "dash": dash},
                            legendgroup=column, text=group["plot_label"],
                            showlegend=not any(trace.legendgroup == column for trace in figure.data),
                            customdata=group[["n_paired", "n_observations", "n_delivery_periods"]].to_numpy(),
                            hovertemplate=(
                                "%{text}<br>%{y:.6f}<br>Paired dates: %{customdata[0]}"
                                "<br>Observations: %{customdata[1]}<br>Delivery periods: %{customdata[2]}"
                                "<extra>%{fullData.name}</extra>"
                            ),
                        ))
                finish_figure(
                    figure, f"Means · {start.value} to {end.value}", identity.value[2],
                    "Contracts by absolute delivery" if alignment.value == "delivery" else "Relative tenor",
                    points=means,
                )
                display(means.reset_index(drop=True))

        def evolution_view(change=None):
            if state["updating"]:
                return
            with evolution_output:
                clear_output(wait=True)
                if contract.value is None:
                    print("No delivery periods are available.")
                    return
                contract_kind, delivery_start, delivery_end = contract.value
                selected = contract_evolution(data, identity.value, contract_kind, delivery_start, delivery_end)
                print("The same absolute delivery period is used across all dates; its relative label may change.")
                print("EEX values may come from earlier publications: check eex_asof and eex_age_days.")
                show_metrics(selected)
                figure = go.Figure()
                add_price_traces(figure, selected, "reference_date", show_before=show_before.value, show_own=show_own.value)
                finish_figure(
                    figure, f"{contract_kind} · {delivery_start:%Y-%m-%d} / {delivery_end:%Y-%m-%d}",
                    identity.value[2], "Reference date",
                )
                show_details(selected)

        def refresh_controls(change=None, refresh_kinds=False):
            state["updating"] = True
            previous_date, previous_start, previous_end = reference.value, start.value, end.value
            previous_contract = contract.value
            if refresh_kinds:
                ranks = {name: index for index, name in enumerate(KIND_ORDER)}
                kinds = sorted(select_curve(data, identity.value)["kind"].unique(), key=lambda name: (ranks.get(name, len(ranks)), name))
                kind.options = [("Full curve (all contract types)", ALL_KINDS)] + [(name, name) for name in kinds]
                kind.value = ALL_KINDS
            selected = selected_scope()
            dates = sorted(selected["reference_date"].dt.date.unique())
            reference.options = [(str(day), day) for day in dates]
            reference.value = previous_date if previous_date in dates else dates[-1] if dates else None
            start.value = max(dates[0], min(previous_start, dates[-1])) if dates and previous_start else dates[0] if dates else None
            end.value = max(dates[0], min(previous_end, dates[-1])) if dates and previous_end else dates[-1] if dates else None
            periods = selected[["kind", "delivery_start", "delivery_end"]].drop_duplicates().sort_values(
                ["kind", "delivery_start", "delivery_end"]
            )
            contract.options = [
                (f"{name} · {first:%Y-%m-%d} / {last:%Y-%m-%d}", (name, first, last))
                for name, first, last in periods.itertuples(index=False, name=None)
            ]
            contracts = [value for _, value in contract.options]
            contract.value = previous_contract if previous_contract in contracts else contracts[0] if contracts else None
            state["updating"] = False
            daily_view()
            range_view()
            evolution_view()

        identity.observe(lambda change: refresh_controls(refresh_kinds=True), names="value")
        kind.observe(lambda change: None if state["updating"] else refresh_controls(), names="value")
        reference.observe(daily_view, names="value")
        show_before.observe(daily_view, names="value")
        show_own.observe(daily_view, names="value")
        show_before.observe(evolution_view, names="value")
        show_own.observe(evolution_view, names="value")
        contract.observe(evolution_view, names="value")
        compare.on_click(range_view)
        alignment.observe(range_view, names="value")
        tabs = widgets.Tab(children=[
            widgets.VBox([reference, daily_output]),
            widgets.VBox([widgets.HBox([start, end]), alignment, compare, range_output]),
            widgets.VBox([contract, evolution_output]),
        ])
        for i, title in enumerate(("Single date", "Range means", "Fixed delivery evolution")):
            tabs.set_title(i, title)
        controls = widgets.VBox([identity, kind, widgets.HBox([show_before, show_own]), tabs])
        refresh_controls(refresh_kinds=True)
        return controls

    return build_browser(frame)
