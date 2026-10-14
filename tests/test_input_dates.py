from dataclasses import asdict
from datetime import date, datetime

import pandas as pd
import pytest

from vwaps import cli
from vwaps.config import load_config
from vwaps.enrich import enrich_input
from vwaps.io_vwap import load_input
from vwaps.mapping import ProductMap


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[paths]\nvwap_input="own.csv"\nmapping="mapping.csv"\n'
        'eex_curves_dir="curves"\noutput_dir="output"\n'
        '[targets]\ntenors=["M+1"]\n', encoding="utf-8")
    return load_config(path)


def original(values):
    return pd.DataFrame({
        "reference_date": pd.Series(values, dtype=object),
        "product": "TEST", "region": "DE", "unit": "EUR/MWh",
        "tenor": "M+1", "vwap": 100.0, "volume": 20.0,
    })


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_mixed_european_iso_and_excel_dates_retain_the_actual_calendar_date(cfg, suffix):
    values = ["01/09/2026", "2026-09-01", datetime(2026, 9, 1),
              "02/01/2026", "2026-01-02", datetime(2026, 1, 2)]
    raw = original(values)
    path = cfg.base_dir / f"own{suffix}"
    if suffix == ".xlsx":
        raw.to_excel(path, index=False)
    else:
        raw.to_csv(path, index=False)
    normalized, preserved = load_input(path, cfg)
    expected = [date(2026, 9, 1)] * 3 + [date(2026, 1, 2)] * 3
    assert normalized.date.tolist() == expected
    enriched = enrich_input(preserved, pd.DataFrame(), cfg, [], date(2026, 1, 1), date(2026, 12, 31))
    assert enriched.curve_reference_date.tolist() == expected
    assert enriched.reference_date.tolist() == preserved.reference_date.tolist()


def test_iso_engine_dates_join_original_european_dates_without_duplicate_added_rows(cfg):
    raw = original(["01/09/2026", "02/01/2026"])
    filled = pd.DataFrame([
        dict(reference_date=day, product="TEST", region="DE", unit="EUR/MWh",
             tenor="M+1", price=100.0, source="own")
        for day in ("2026-09-01", "2026-01-02")
    ])
    enriched = enrich_input(raw, filled, cfg, [], date(2026, 1, 1), date(2026, 12, 31))
    assert len(enriched) == 2
    assert enriched.curve_row_type.tolist() == ["original", "original"]
    assert enriched.curve_reference_date.tolist() == [date(2026, 9, 1), date(2026, 1, 2)]
    assert enriched.reference_date.tolist() == raw.reference_date.tolist()


@pytest.mark.parametrize("invalid", ["not-a-date", "", "2026-02-30", "31/09/2026"])
def test_invalid_dates_are_rejected_by_loading_and_enrichment(cfg, invalid):
    raw = original(["2026-09-01", invalid])
    path = cfg.base_dir / "own.csv"
    raw.to_csv(path, index=False)
    with pytest.raises(ValueError, match="invalid|empty"):
        load_input(path, cfg)
    with pytest.raises(ValueError, match="invalid|empty"):
        enrich_input(raw, pd.DataFrame(), cfg, [], date(2026, 1, 1), date(2026, 12, 31))


def test_catchup_iso_input_starts_in_september_and_preserves_own_price(cfg):
    original(["2026-09-01"]).to_csv(cfg.base_dir / "own.csv", index=False)
    mapping = ProductMap("TEST", "fill", "DE", "Base", "assigned.csv", "Base", "Europe/Berlin",
                         region="DE", unit="EUR/MWh")
    pd.DataFrame([asdict(mapping)]).to_csv(cfg.mapping_file, index=False)
    assert cli.main(["catchup", "--to", "2026-09-01"], cfg.base_dir / "config.toml") == 0
    filled = pd.read_csv(cfg.output_dir / "filled_history.csv")
    enriched = pd.read_csv(cfg.output_dir / "enriched_history.csv")
    assert filled.reference_date.tolist() == ["2026-09-01"]
    assert filled.source.tolist() == ["own"]
    assert filled.price.tolist() == [100.0]
    assert enriched.reference_date.tolist() == ["2026-09-01"]
    assert enriched.curve_reference_date.tolist() == ["2026-09-01"]


@pytest.mark.parametrize("value", [45931, 45931.0, 20260928, "20260928",
                                  "2026-10-07T12:00:00+02:00", "2026-10-07T10:00:00Z"])
def test_ambiguous_numeric_or_timezone_dates_are_invalid_instead_of_becoming_1970(cfg, value):
    raw = original([value])
    path = cfg.base_dir / "ambiguous.xlsx"
    raw.to_excel(path, index=False)
    with pytest.raises(ValueError, match="invalid|empty"):
        load_input(path, cfg)
    with pytest.raises(ValueError, match="invalid|empty"):
        enrich_input(raw, pd.DataFrame(), cfg, [], date(1969, 1, 1), date(2027, 12, 31))
