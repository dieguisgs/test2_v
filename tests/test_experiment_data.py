"""Notebook inputs preserve production semantics and need no real data for a demo."""

from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.experiment_data import load_experiment_dataset, make_demo_dataset
from vwaps.hours import period_hours
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import COLUMNS, ProductMap
from vwaps.tenors import resolve_tenor
from vwaps.tuning import tune_parameters


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    return load_config(path)


def test_demo_is_deterministic_independent_of_files_and_does_not_mutate_config(config):
    before = asdict(config)
    first = make_demo_dataset(config, periods=5)
    second = make_demo_dataset(config, periods=5)
    other_seed = make_demo_dataset(config, seed=8, periods=5)
    pd.testing.assert_frame_equal(first.vwaps, second.vwaps)
    assert asdict(config) == before
    assert first.config is not config
    assert not first.vwaps["vwap"].equals(other_seed.vwaps["vwap"])
    assert first.vwaps["date"].nunique() == 5
    assert set(first.vwaps["region"]) == {"DE", "FR"}
    assert all("SYNTHETIC" in mapping.product for mapping in first.maps)
    assert set(config.base_dir.iterdir()) == {config.base_dir / "config.toml"}


def test_demo_reference_months_and_quarter_are_hours_coherent_across_roll(config):
    data = make_demo_dataset(config)
    day = date(2026, 9, 1)
    quotes, asof = data.books[data.maps[0].key].quotes(day, None)
    months = [resolve_tenor(f"M+{i}", day) for i in (1, 2, 3)]
    quarter = resolve_tenor("Q+1", day)
    weights = [period_hours(p.start, p.end, "Base", "Europe/Berlin") for p in months]
    weighted = sum(quotes[p.key] * weight for p, weight in zip(months, weights)) / sum(weights)
    assert weighted == pytest.approx(quotes[quarter.key], abs=1e-10)
    assert asof == day
    assert data.start.month == 8 and data.end.month == 10
    previous, _ = data.books[data.maps[0].key].quotes(date(2026, 8, 31), None)
    assert all(p.key in previous for p in months)


@pytest.mark.parametrize("kwargs", [{"periods": True}, {"periods": 2}, {"periods": 3.5},
                                    {"seed": True}, {"seed": -1}])
def test_invalid_demo_options_rejected(config, kwargs):
    with pytest.raises(ValueError):
        make_demo_dataset(config, **kwargs)


def test_demo_runs_actual_tuning_pipeline(config):
    data = make_demo_dataset(config, periods=5)
    result = tune_parameters(data.config, data.vwaps, data.maps, data.books, data.start, data.end,
                             {"basis_mode": ["ratio", "additive"]}, 1, 2)
    assert result.metadata["n_trials"] == 2
    assert result.metadata["validation_days"] == 1
    assert result.metadata["calibration_n_paired"] > 0
    assert result.metadata["validation_coverage"] == 1


@pytest.fixture
def real_config(config):
    cfg = replace(config, mapping_file=config.base_dir / "mapping.csv",
                  eex_curves_dir=config.base_dir, vwap_input="input.csv", tenors=["M+1"])
    maps = [
        ProductMap("POWER", use, region, "Base", "settlements.csv", "Base", "Europe/Berlin",
                   region=region, unit="EUR/MWh")
        for use, region in (("fill", "DE"), ("helper", "FR"), ("off", "IT"))
    ]
    pd.DataFrame([asdict(m) for m in maps], columns=COLUMNS).to_csv(cfg.mapping_file, index=False)
    rows = [dict(reference_date=day, product="POWER", region=region, unit="EUR/MWh",
                 tenor="M+1", vwap=110, volume=50)
            for day, region in (("2026-09-01", "DE"), ("2026-09-02", "DE"),
                                ("2030-01-01", "FR"), ("2040-01-01", "IT"),
                                ("2050-01-01", "UNMAPPED"))]
    pd.DataFrame(rows).to_csv(cfg.base_dir / "input.csv", index=False)
    pd.DataFrame([(date(2026, 9, 1), "Month", date(2026, 10, 1), 100)],
                 columns=REQUIRED).to_csv(cfg.base_dir / "settlements.csv", index=False)
    return cfg


def test_real_loader_keeps_helpers_but_only_fill_dates_define_range(real_config, monkeypatch):
    count = []
    original = EexBook.from_file

    def recorded(path):
        count.append(path)
        return original(path)

    monkeypatch.setattr(EexBook, "from_file", recorded)
    data = load_experiment_dataset(real_config)
    assert data.start == date(2026, 9, 1) and data.end == date(2026, 9, 2)
    assert set(data.vwaps["region"]) == {"DE", "FR"}
    assert len(count) == 1
    assert data.books[("POWER", "DE", "EUR/MWh")] is data.books[("POWER", "FR", "EUR/MWh")]
    assert not real_config.output_dir.exists()


def test_real_loader_override_and_missing_input_are_explicit(real_config):
    data = load_experiment_dataset(replace(real_config, vwap_input="absent.csv"), vwap_path="input.csv")
    assert not data.vwaps.empty
    with pytest.raises(FileNotFoundError, match="No VWAP input"):
        load_experiment_dataset(real_config, vwap_path="absent.csv")


def test_real_loader_rejects_corrupt_eex_without_writing_output(real_config):
    (real_config.base_dir / "settlements.csv").write_text("broken\nfile\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read EEX"):
        load_experiment_dataset(real_config)
    assert not real_config.output_dir.exists()


def test_real_loader_missing_eex_is_explicitly_unavailable(real_config):
    maps = pd.read_csv(real_config.mapping_file, keep_default_na=False)
    maps["eex_file"] = "absent.csv"
    maps.to_csv(real_config.mapping_file, index=False)
    assert all(book is None for book in load_experiment_dataset(real_config).books.values())


def test_real_loader_requires_enabled_fill_mapping(real_config):
    maps = pd.read_csv(real_config.mapping_file, keep_default_na=False)
    maps["use"] = "off"
    maps.to_csv(real_config.mapping_file, index=False)
    with pytest.raises(ValueError, match="No active fill"):
        load_experiment_dataset(real_config)


def test_real_loader_requires_finite_supported_observations(real_config):
    path = real_config.base_dir / "input.csv"
    frame = pd.read_csv(path)
    frame.loc[frame["region"] == "DE", "vwap"] = float("nan")
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="No finite own"):
        load_experiment_dataset(real_config)
