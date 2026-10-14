"""Input and editable mapping contracts for independent curve identities."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from vwaps.cli import cmd_mapping
from vwaps.config import load_config
from vwaps.identity import curve_keys
from vwaps.io_vwap import load_input
from vwaps.mapping import COLUMNS, guess_row, load_mapping, migrate_legacy_mapping


@pytest.fixture
def cfg(tmp_path):
    original = load_config(Path(__file__).resolve().parents[1] / "config.toml")
    return replace(original, base_dir=tmp_path, vwap_input="input.csv",
                   mapping_file=tmp_path / "mapping.csv", eex_curves_dir=tmp_path / "eex",
                   output_dir=tmp_path / "output")


def input_rows():
    return pd.DataFrame([
        dict(reference_date="30/09/2026", product="P", region=r, unit=u,
             tenor2="M+1", vwap=p, total_volume=10)
        for r, u, p in [("North", "EUR/MWh", 100), ("South", "EUR/MWh", 200),
                        ("North", "GBP/MWh", 300), ("", "EUR/MWh", 400)]
    ])


def test_input_keeps_three_components_and_original_text(cfg):
    raw = input_rows()
    raw.loc[0, "region"] = " North "
    raw.to_csv(cfg.resolve(cfg.vwap_input), index=False)
    normalized, original = load_input(cfg.vwap_input, cfg)
    assert len(set(curve_keys(normalized))) == 4
    assert original.iloc[0].region == " North "
    assert normalized.iloc[0].region == "North"
    assert normalized.iloc[3].region == ""


@pytest.mark.parametrize("column", ["product", "region", "unit"])
def test_file_input_requires_identity_columns(cfg, column):
    input_rows().drop(columns=column).to_csv(cfg.resolve(cfg.vwap_input), index=False)
    with pytest.raises(ValueError, match="missing columns"):
        load_input(cfg.vwap_input, cfg)


def test_mapping_drafts_each_identity_off_then_preserves_manual_choices(cfg):
    input_rows().to_csv(cfg.resolve(cfg.vwap_input), index=False)
    assert cmd_mapping(cfg, SimpleNamespace(vwap=None)) == 0
    draft = pd.read_csv(cfg.mapping_file, keep_default_na=False)
    assert len(draft) == 4 and draft.use.eq("off").all()
    draft.loc[0, ["use", "eex_file", "comment"]] = ["fill", "custom/EUR.csv", "manual decision"]
    draft.to_csv(cfg.mapping_file, index=False)
    cmd_mapping(cfg, SimpleNamespace(vwap=None))
    actual = pd.read_csv(cfg.mapping_file, keep_default_na=False)
    pd.testing.assert_frame_equal(actual, draft)
    assert len({m.key for m in load_mapping(cfg)}) == 4


def test_matching_eex_still_requires_manual_activation(cfg):
    folder = cfg.eex_curves_dir / "DE"
    folder.mkdir(parents=True)
    (folder / "Base.csv").write_text("tradeDate\n", encoding="utf-8")
    row = guess_row("DE_Base load", cfg, "North", "EUR/MWh")
    assert row["eex_file"] == "DE/Base.csv"
    assert row["use"] == "off"


def test_mapping_duplicate_identity_rejected_but_same_product_allowed(cfg):
    rows = [guess_row("P", cfg, "North", "EUR/MWh"), guess_row("P", cfg, "South", "EUR/MWh")]
    pd.DataFrame(rows, columns=COLUMNS).to_csv(cfg.mapping_file, index=False)
    assert len(load_mapping(cfg)) == 2
    pd.DataFrame([*rows, rows[0]], columns=COLUMNS).to_csv(cfg.mapping_file, index=False)
    with pytest.raises(ValueError, match="Duplicate curve identities"):
        load_mapping(cfg)


def test_unique_legacy_mapping_migration_preserves_manual_values(cfg):
    frame = pd.DataFrame([guess_row("P", cfg)]).drop(columns=["region", "unit"])
    frame.loc[0, ["use", "eex_file", "comment"]] = ["fill", "manual/file.csv", "keep me"]
    migrated = migrate_legacy_mapping(frame, input_rows().iloc[:1])
    assert migrated.iloc[0][["product", "region", "unit"]].tolist() == ["P", "North", "EUR/MWh"]
    pd.testing.assert_frame_equal(migrated[frame.columns], frame)


def test_ambiguous_legacy_mapping_command_leaves_file_unchanged(cfg):
    input_rows().to_csv(cfg.resolve(cfg.vwap_input), index=False)
    frame = pd.DataFrame([guess_row("P", cfg)]).drop(columns=["region", "unit"])
    frame.to_csv(cfg.mapping_file, index=False)
    before = cfg.mapping_file.read_bytes()
    with pytest.raises(ValueError, match="4 input curve identities"):
        cmd_mapping(cfg, SimpleNamespace(vwap=None))
    assert cfg.mapping_file.read_bytes() == before


def test_blank_and_na_regions_are_literal_not_wildcards(cfg):
    rows = [guess_row("P", cfg, "", "EUR/MWh"), guess_row("P", cfg, "NA", "EUR/MWh")]
    pd.DataFrame(rows, columns=COLUMNS).to_csv(cfg.mapping_file, index=False)
    assert {m.key for m in load_mapping(cfg)} == {("P", "", "EUR/MWh"), ("P", "NA", "EUR/MWh")}
