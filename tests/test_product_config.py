"""Per-curve production settings remain explicit, validated and reproducible."""

from dataclasses import fields, replace
import json
from pathlib import Path

import pandas as pd
import pytest

from vwaps.config import Config, load_config, validate_config
from vwaps.mapping import COLUMNS, ProductMap, guess_row, load_mapping, read_mapping_table
from vwaps.product_config import (
    MODEL_PARAMETER_FIELDS, configuration_record, effective_config,
    model_parameter_payload, parse_parameter_cells, validate_parameter_overrides,
)


@pytest.fixture
def cfg(tmp_path):
    original = load_config(Path(__file__).resolve().parents[1] / "config.toml")
    return replace(original, base_dir=tmp_path, mapping_file=tmp_path / "products.csv")


def mapping(**overrides):
    return ProductMap("Power", "fill", "DE", "Base", "DE/Base.csv", "Base",
                      "Europe/Berlin", region="North", unit="EUR/MWh",
                      parameter_overrides=overrides)


def write_mapping(cfg, **cells):
    row = guess_row("Power", cfg, "North", "EUR/MWh")
    row.update(cells)
    frame = pd.DataFrame([row])
    if cfg.mapping_file.suffix == ".xlsx":
        frame.to_excel(cfg.mapping_file, index=False)
    else:
        frame.to_csv(cfg.mapping_file, index=False)


def test_existing_config_defaults_to_global_and_rejects_unknown_modes(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    assert load_config(path).configuration_mode == "global"
    path.write_text('[run]\nconfiguration_mode="individual"\n', encoding="utf-8")
    assert load_config(path).configuration_mode == "individual"
    path.write_text('[run]\nconfiguration_mode="automatic"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="configuration_mode"):
        load_config(path)


@pytest.mark.parametrize("overrides", [
    {"layer_local": "false"}, {"layer_local": 1}, {"tau_log": "0.2"},
    {"tau_log": float("nan")}, {"tau_log": float("inf")}, {"tau_log": True},
    {"fallback_price_window": 2.5}, {"fallback_price_window": True},
    {"tenors": "M+1"}, {"tenors": ["M+1", 2]}, {"layer_hist": False},
    {"eex_offset_days": -1}, {"output_dir": "wrong"}, {"unknown": 5},
])
def test_runtime_overlays_reject_coercion_and_operational_changes(cfg, overrides):
    with pytest.raises(ValueError):
        effective_config(cfg, mapping(), overrides)


@pytest.mark.parametrize("overrides", [
    {"tau_log": 0}, {"other_kind_weight": -1}, {"fallback_price_window": 1},
    {"basis_mode": "mean"}, {"cross_min_corr": 1.1}, {"shape_original_weight": 0.5},
    {"hist_max_age_days": 0}, {"tenors": ["invalid+1"]},
])
def test_full_validation_rejects_invalid_model_bounds(cfg, overrides):
    with pytest.raises(ValueError):
        effective_config(replace(cfg, configuration_mode="individual"), mapping(**overrides))


def test_wrong_mapping_extension_is_rejected(cfg):
    cfg = replace(cfg, mapping_file=cfg.mapping_file.with_suffix(".json"))
    cfg.mapping_file.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=".csv or .xlsx"):
        read_mapping_table(cfg.mapping_file)


def test_global_and_individual_with_equal_effective_values_have_equal_model_id(cfg):
    product = mapping()
    global_record = configuration_record(cfg, product)
    individual_record = configuration_record(replace(cfg, configuration_mode="individual"), product)
    assert global_record["configuration_id"] == individual_record["configuration_id"]
    assert global_record["configuration_parameters"] == individual_record["configuration_parameters"]
    assert global_record["configuration_mode"] != individual_record["configuration_mode"]


def test_positional_product_map_compatibility_and_independent_override_dicts():
    first = ProductMap("P", "off", "DE", "Base", "", "Base", "Europe/Berlin", "note", "R", "U")
    second = ProductMap("P", "off", "DE", "Base", "", "Base", "Europe/Berlin")
    first.parameter_overrides["tau_log"] = 0.9
    assert first.key == ("P", "R", "U") and second.parameter_overrides == {}


def test_operational_fields_are_not_valid_command_model_overrides(cfg):
    with pytest.raises(ValueError, match="eex_offset_days"):
        validate_config(replace(cfg, command_overrides={"eex_offset_days": -1}))
