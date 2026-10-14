"""Output-only comparisons keep identities, delivery periods and paired dates aligned."""

from datetime import date

import pandas as pd
import pytest

from vwaps.visualization import (
    KIND_ORDER, available_curves, contract_evolution, coverage_metrics, curve_plot_points, find_project_root,
    load_output, paired_means, resolve_output_path, select_curve,
)


KEY = ("Power", "North", "EUR/MWh")


def row(**changes):
    return dict(reference_date="2026-09-30", product=KEY[0], region=KEY[1], unit=KEY[2],
                tenor="M+2", kind="Month", delivery_start="2026-11-01", delivery_end="2026-12-01",
                price=120, eex_settle=110, eex_asof="2026-09-29", source="eex+local") | changes


def save(tmp_path, rows):
    path = tmp_path / "filled.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return load_output(path)


def test_paths_work_from_copied_root_notebooks_and_alternate_config(tmp_path):
    (tmp_path / "src" / "vwaps").mkdir(parents=True)
    (tmp_path / "notebooks").mkdir()
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text('[paths]\noutput_dir="saved"\nvwap_input="NOT_AVAILABLE.csv"\n'
                      'eex_curves_dir="NOT_AVAILABLE"\n', encoding="utf-8")
    assert find_project_root(tmp_path / "notebooks") == tmp_path
    assert resolve_output_path(tmp_path) == tmp_path / "saved" / "filled_history.csv"
    assert resolve_output_path(tmp_path, output_path="portable.csv") == tmp_path / "portable.csv"
    assert resolve_output_path(tmp_path, config_path="missing.toml", output_path="archive") == (
        tmp_path / "archive" / "filled_history.csv")
    other = tmp_path / "configurations"
    other.mkdir()
    (other / "view.toml").write_text('[paths]\noutput_dir="../results"\n', encoding="utf-8")
    assert resolve_output_path(tmp_path, config_path="configurations/view.toml") == (
        tmp_path / "results" / "filled_history.csv")


def test_shape_off_output_accepts_missing_optional_columns(tmp_path):
    data = save(tmp_path, [row()])
    assert "price_before_shape" not in data
    assert data.loc[0, "eex_age_days"] == 1
    assert coverage_metrics(data)["n_estimated"] == 1


def test_shape_output_preserves_original_and_prior_price_for_comparison(tmp_path):
    data = save(tmp_path, [row(price=116, own_vwap=120, price_before_shape=120,
                               shape_adjustment=-4, shape_status="adjusted", source="own+shape",
                               data_origin="estimated")])
    assert data.loc[0, "own_vwap"] == data.loc[0, "price_before_shape"] == 120
    assert data.loc[0, "price"] == 116 and data.loc[0, "shape_status"] == "adjusted"
    metrics = coverage_metrics(data)
    assert metrics["n_adjusted_original"] == metrics["n_estimated"] == 1
    assert metrics["n_original"] == 0


@pytest.mark.parametrize("reference", ["2026-09-01", "01/09/2026", date(2026, 9, 1)])
def test_reference_dates_do_not_swap_iso_month_and_day(tmp_path, reference):
    data = save(tmp_path, [row(reference_date=reference, eex_asof="2026-09-01")])
    assert data.loc[0, "reference_date"] == pd.Timestamp("2026-09-01")


@pytest.mark.parametrize("field,value", [
    ("reference_date", "31/02/2026"), ("delivery_start", "bad"), ("eex_asof", "bad"),
    ("delivery_end", "2026-10-01"), ("price", "bad"),
])
def test_invalid_output_is_reported_instead_of_silently_plotting(tmp_path, field, value):
    with pytest.raises(ValueError):
        save(tmp_path, [row(**{field: value})])


def test_missing_file_empty_output_and_missing_identity_are_distinct(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_output(tmp_path / "absent.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert load_output(empty).empty
    path = tmp_path / "legacy.csv"
    pd.DataFrame([row()]).drop(columns=["region", "unit"]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="region.*unit"):
        load_output(path)


def test_aliases_count_once_and_keep_labels(tmp_path):
    data = save(tmp_path, [row(), row(tenor="M2")])
    assert len(data) == 1 and data.attrs["alias_rows_removed"] == 1
    assert data.loc[0, "tenor"] == "M+2" and data.loc[0, "tenor_aliases"] == "M+2, M2"
    assert paired_means(data, KEY).iloc[0]["n_paired"] == 1


@pytest.mark.parametrize("field,value", [
    ("price", 121), ("price", None), ("eex_settle", 111), ("eex_asof", "2026-09-30"),
])
def test_conflicting_alias_values_cannot_be_averaged_away(tmp_path, field, value):
    with pytest.raises(ValueError, match="Conflicting output aliases"):
        save(tmp_path, [row(), row(tenor="M2", **{field: value})])


def test_same_product_regions_and_currencies_remain_separate(tmp_path):
    data = save(tmp_path, [row(), row(region="South", price=220), row(unit="GBP/MWh", price=320)])
    assert len(available_curves(data)) == 3
    assert paired_means(data, KEY).iloc[0]["price_mean"] == 120
    assert paired_means(data, ("Power", "South", "EUR/MWh")).iloc[0]["price_mean"] == 220
    assert paired_means(data, ("Power", "North", "GBP/MWh")).iloc[0]["price_mean"] == 320


def roll_rows():
    return [
        row(), row(tenor="M2"),
        row(tenor="M+1", delivery_start="2026-10-01", delivery_end="2026-11-01",
            price=100, eex_settle=90, source="own"),
        row(reference_date="2026-10-01", tenor="M+1", price=124, eex_settle=112),
        row(reference_date="2026-10-02", tenor="M+1", price=0, eex_settle=114, source="missing"),
        row(reference_date="2026-10-03", tenor="M+1", price=130, eex_settle=None),
    ]


def test_absolute_means_follow_fixed_contract_and_use_only_paired_dates(tmp_path):
    data = save(tmp_path, roll_rows())
    result = paired_means(data, KEY, start=date(2026, 9, 30), end=date(2026, 10, 3))
    november = result[result.delivery_start == pd.Timestamp("2026-11-01")].iloc[0]
    assert november.price_mean == 122 and november.eex_mean == 111
    assert november.mean_spread == 11
    assert november.n_observations == 4 and november.n_paired == 2
    assert november.n_price == november.n_eex == 3 and november.n_missing_price == 1
    assert november.n_delivery_periods == 1


def test_relative_means_expose_delivery_roll_and_do_not_duplicate_aliases(tmp_path):
    data = save(tmp_path, roll_rows())
    result = paired_means(data, KEY, alignment="tenor").set_index("tenor")
    assert result.loc["M+1", "n_delivery_periods"] == 2
    assert result.loc["M+1", "price_mean"] == 112  # October 100 and November 124, paired dates only.
    assert result.loc["M+1", "eex_mean"] == 101
    assert result.loc["M+1", "n_observations"] == 4
    assert result.loc["M+2", "n_paired"] == 1


def test_zero_negative_and_nonfinite_prices_have_correct_paired_counts(tmp_path):
    data = save(tmp_path, [row(reference_date=f"2026-09-{day}", price=price, eex_settle=eex)
                           for day, price, eex in ((28, 0, 0), (29, -10, -12), (30, float("inf"), 8))])
    result = paired_means(data, KEY).iloc[0]
    assert result.n_paired == 2 and result.n_observations == 3
    assert result.price_mean == -5 and result.eex_mean == -6


def test_unpaired_point_has_missing_means_and_exposed_count(tmp_path):
    result = paired_means(save(tmp_path, [row(eex_settle=None)]), KEY).iloc[0]
    assert result.n_paired == 0 and result.n_price == 1
    assert pd.isna(result.price_mean) and pd.isna(result.eex_mean)


def test_evolution_follows_absolute_contract_not_m1_label(tmp_path):
    data = save(tmp_path, roll_rows())
    evolution = contract_evolution(data, KEY, "Month", "2026-11-01", "2026-12-01")
    assert list(evolution.tenor) == ["M+2", "M+1", "M+1", "M+1"]
    assert evolution.reference_date.is_monotonic_increasing
    assert list(evolution.price.dropna()) == [120, 124, 130]
    assert len(select_curve(data, KEY, reference_date=date(2026, 9, 30))) == 2
    with pytest.raises(ValueError, match="start date"):
        paired_means(data, KEY, start="2026-10-02", end="2026-10-01")


def test_complete_plot_preserves_all_families_and_every_saved_quarter(tmp_path):
    rows = [row(kind=kind, tenor=f"contract-{kind}") for kind in reversed(KIND_ORDER)]
    rows += [
        row(kind="Quarter", tenor="Q+3", delivery_start="2027-04-01", delivery_end="2027-07-01"),
        row(kind="Quarter", tenor="Q+8", delivery_start="2028-07-01", delivery_end="2028-10-01"),
        row(kind="ZOther", tenor="z"), row(kind="AOther", tenor="a"),
    ]
    data = save(tmp_path, rows)
    points = curve_plot_points(data)
    assert len(points) == len(data) == 13
    assert points["plot_x"].is_unique
    assert list(points["kind"].drop_duplicates()) == [*KIND_ORDER, "AOther", "ZOther"]
    quarters = points.loc[points["kind"].eq("Quarter")]
    assert list(quarters["plot_label"]) == ["contract-Quarter", "Q+3", "Q+8"]
    assert "plot_x" not in data  # Preparing a chart never modifies the saved observations.


def test_month_quarter_year_starting_together_have_distinct_plot_positions(tmp_path):
    data = save(tmp_path, [
        row(kind="Month", tenor="M+4", delivery_start="2027-01-01", delivery_end="2027-02-01"),
        row(kind="Quarter", tenor="Q+2", delivery_start="2027-01-01", delivery_end="2027-04-01"),
        row(kind="Year", tenor="Cal+1", delivery_start="2027-01-01", delivery_end="2028-01-01"),
    ])
    points = curve_plot_points(data)
    assert points["delivery_start"].nunique() == 1
    assert points["plot_x"].nunique() == 3
    assert list(points["plot_label"]) == ["M+4", "Q+2", "Cal+1"]
    means = curve_plot_points(paired_means(data, KEY, kind=None))
    assert list(means["plot_label"]) == ["2027-01", "2027-Q1", "Cal-2027"]
    assert means["plot_x"].nunique() == 3


def test_absolute_plot_orders_delivery_dates_instead_of_lexicographic_tenors(tmp_path):
    data = save(tmp_path, [
        row(tenor="M+10", delivery_start="2027-07-01", delivery_end="2027-08-01"),
        row(tenor="M+2"),
    ])
    assert list(curve_plot_points(data)["plot_label"]) == ["M+2", "M+10"]


def test_relative_plot_orders_numeric_offsets_and_distinguishes_season_bases():
    means = pd.DataFrame({
        "kind": ["Month", "Month", "Season", "Season", "Season", "Season"],
        "tenor": ["M+10", "M+2", "Win+2", "Sum+1", "Win+1", "Sum+2"],
        "price_mean": [10, 2, 22, 11, 21, 12],
    })
    points = curve_plot_points(means, alignment="tenor")
    assert list(points["plot_label"]) == ["M+2", "M+10", "Sum+1", "Win+1", "Sum+2", "Win+2"]
    assert list(points["price_mean"]) == [2, 10, 11, 21, 12, 22]
    assert points["plot_x"].is_unique


def test_plot_keeps_missing_contract_without_inventing_later_quarters(tmp_path):
    data = save(tmp_path, [
        row(kind="Quarter", tenor="Q+1", delivery_start="2026-10-01", delivery_end="2027-01-01",
            source="missing", price=None, eex_settle=0),
        row(kind="Quarter", tenor="Q+2", delivery_start="2027-01-01", delivery_end="2027-04-01",
            price=-10, eex_settle=-8),
    ])
    points = curve_plot_points(data)
    assert list(points["plot_label"]) == ["Q+1", "Q+2"]
    assert pd.isna(points.loc[0, "price"]) and points.loc[0, "eex_settle"] == 0
    assert points.loc[1, "price"] == -10


def test_plot_rejects_repeated_contracts_until_dates_are_aggregated(tmp_path):
    data = save(tmp_path, [row(), row(reference_date="2026-10-01", tenor="M+1")])
    with pytest.raises(ValueError, match="aggregate dates first"):
        curve_plot_points(data)
    relative = pd.DataFrame({"kind": ["Month", "Month"], "tenor": ["M+1", "M+1"]})
    with pytest.raises(ValueError, match="aggregate dates first"):
        curve_plot_points(relative, alignment="tenor")


def test_plot_empty_frame_and_invalid_alignment():
    points = curve_plot_points(pd.DataFrame())
    assert points.empty and list(points.columns) == ["plot_x", "plot_label"]
    with pytest.raises(ValueError, match="alignment"):
        curve_plot_points(pd.DataFrame(), alignment="unknown")
