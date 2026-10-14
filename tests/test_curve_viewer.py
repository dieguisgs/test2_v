"""Shared notebook controls retain all families and compare saved data only."""

import pandas as pd
import pytest

widgets = pytest.importorskip("ipywidgets")
go = pytest.importorskip("plotly.graph_objects")

from vwaps.curve_viewer import create_curve_viewer
from vwaps.visualization import KIND_ORDER, load_output


@pytest.fixture
def plotted(monkeypatch):
    import IPython.display
    rendered = []
    monkeypatch.setattr(IPython.display, "display", lambda value: rendered.append(value))
    monkeypatch.setattr(IPython.display, "clear_output", lambda **kwargs: None)
    return rendered


@pytest.fixture
def curves(tmp_path):
    contracts = [
        ("Day", "D+1", "2026-10-02", "2026-10-03"),
        ("Weekend", "WE+1", "2026-10-03", "2026-10-05"),
        ("BOW", "BOW", "2026-10-02", "2026-10-05"),
        ("Week", "W+1", "2026-10-05", "2026-10-12"),
        ("BOM", "BOM", "2026-10-02", "2026-11-01"),
        ("Month", "M+1", "2026-11-01", "2026-12-01"),
        ("Month", "M+2", "2026-12-01", "2027-01-01"),
        ("Month", "M+3", "2027-01-01", "2027-02-01"),
        ("Quarter", "Q+1", "2027-01-01", "2027-04-01"),
        ("Quarter", "Q+2", "2027-04-01", "2027-07-01"),
        ("Quarter", "Q+3", "2027-07-01", "2027-10-01"),
        ("Season", "S+1", "2027-04-01", "2027-10-01"),
        ("Year", "CAL+1", "2027-01-01", "2028-01-01"),
    ]
    rows = [
        dict(reference_date=day, product="POWER", region=region, unit=unit,
             tenor=tenor, kind=kind, delivery_start=start, delivery_end=end,
             price=90 + index, eex_settle=85 + index, eex_asof=day,
             source="eex+local", data_origin="estimated", price_before_shape=91 + index,
             own_vwap=95 + index if kind == "Month" else float("nan"),
             eex_offset_days=0, eex_cutoff_date=day)
        for day in ("2026-09-30", "2026-10-01")
        for region, unit in (("DE", "EUR/MWh"), ("GB", "GBP/MWh"))
        for index, (kind, tenor, start, end) in enumerate(contracts)
    ]
    path = tmp_path / "filled.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return load_output(path)


def figures(rendered):
    return [item for item in rendered if isinstance(item, go.Figure)]


def test_full_curve_defaults_to_all_saved_contracts_and_preserves_data(curves, plotted):
    original = curves.copy(deep=True)
    viewer = create_curve_viewer(curves)
    identity, kind, toggles, tabs = viewer.children
    assert kind.value == "__all__"
    assert kind.options[0] == ("Full curve (all contract types)", "__all__")
    assert [value for _, value in kind.options[1:]] == list(KIND_ORDER)
    assert [tabs.get_title(index) for index in range(3)] == [
        "Single date", "Range means", "Fixed delivery evolution",
    ]
    daily, means, evolution = figures(plotted)
    assert len(daily.layout.xaxis.ticktext) == 13
    assert sum(len(trace.x) for trace in daily.data if trace.legendgroup == "price") == 13
    assert daily.layout.yaxis.title.text == "EUR/MWh"
    assert len(evolution.data[0].x) == 2
    assert not any(item is viewer for item in plotted)  # The caller displays the root widget.
    pd.testing.assert_frame_equal(curves, original)


@pytest.mark.parametrize("family, expected", [("Quarter", 3), ("Month", 3), ("Day", 1)])
def test_family_selection_shows_every_saved_contract(curves, plotted, family, expected):
    viewer = create_curve_viewer(curves)
    plotted.clear()
    viewer.children[1].value = family
    daily = figures(plotted)[0]
    assert len(daily.layout.xaxis.ticktext) == expected
    assert len(daily.data[0].x) == expected


def test_identity_dates_range_alignment_and_optional_traces_remain_interactive(curves, plotted):
    viewer = create_curve_viewer(curves)
    identity, kind, toggles, tabs = viewer.children
    identity.value = ("POWER", "GB", "GBP/MWh")
    assert figures(plotted)[-3].layout.yaxis.title.text == "GBP/MWh"
    kind.value = "Quarter"
    reference = tabs.children[0].children[0]
    reference.value = reference.options[0][1]
    assert str(reference.value) in figures(plotted)[-1].layout.title.text
    plotted.clear()
    toggles.children[0].value = True
    assert any(trace.name == "Before shape adjustment" for trace in figures(plotted)[0].data)
    alignment = tabs.children[1].children[1]
    alignment.value = "tenor"
    assert figures(plotted)[-1].layout.xaxis.title.text == "Relative tenor"
    fixed_contract = tabs.children[2].children[0]
    assert len(fixed_contract.options) == 3
    fixed_contract.value = fixed_contract.options[-1][1]
    assert len(figures(plotted)[-1].data[0].x) == 2


def test_empty_saved_frame_has_an_explicit_message_without_display_side_effects(plotted):
    widget = create_curve_viewer(pd.DataFrame())
    assert isinstance(widget, widgets.HTML)
    assert "No saved curve rows" in widget.value
    assert plotted == []
