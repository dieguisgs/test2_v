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


def test_individual_configuration_inherits_unspecified_values_without_mutation(cfg):
    cfg = replace(cfg, configuration_mode="individual", tau_log=0.3)
    product = mapping(basis_mode="additive", tau_log=0.9, tenors=["M+2", "Cal+1"])
    resolved = effective_config(cfg, product)
    assert resolved.basis_mode == "additive" and resolved.tau_log == 0.9
    assert resolved.shrink_k == cfg.shrink_k
    assert cfg.tau_log == 0.3 and cfg.basis_mode == "auto"
    resolved.tenors.append("M+3")
    resolved.timezones["default"] = "UTC"
    assert product.parameter_overrides["tenors"] == ["M+2", "Cal+1"]
    assert cfg.timezones["default"] != "UTC"


def test_global_mode_ignores_even_invalid_individual_overrides(cfg):
    product = mapping(basis_mode="not-a-mode", non_existing_parameter="bad")
    assert effective_config(cfg, product).basis_mode == cfg.basis_mode
    write_mapping(cfg, basis_mode="not-a-mode", misspelled_parameter="bad")
    loaded = load_mapping(cfg)[0]
    assert loaded.parameter_overrides == {}
    assert effective_config(cfg, loaded).basis_mode == cfg.basis_mode


@pytest.mark.parametrize("mode", ["global", "individual"])
def test_runtime_overlay_is_last_and_independent_from_production_table(cfg, mode):
    cfg = replace(cfg, configuration_mode=mode)
    product = mapping(tau_log=0.7)
    resolved = effective_config(cfg, product, {"tau_log": 1.2, "layer_hist": "off"})
    assert resolved.tau_log == 1.2 and resolved.layer_hist == "off"
    assert product.parameter_overrides == {"tau_log": 0.7}
    assert cfg.tau_log != 1.2


def test_all_model_fields_are_supported_and_operational_fields_explicitly_excluded(cfg):
    excluded = {
        "base_dir", "vwap_input", "mapping_file", "eex_curves_dir", "output_dir",
        "max_stale_days", "warn_stale_days", "timezones", "day_convention",
        "weekend_offset", "warmup_days", "vwap_columns", "eex_offset_days", "configuration_mode",
        "command_overrides",
    }
    assert set(MODEL_PARAMETER_FIELDS) == {f.name for f in fields(Config)} - excluded
    payload = model_parameter_payload(cfg)
    resolved = effective_config(replace(cfg, configuration_mode="individual"), mapping(**payload))
    assert model_parameter_payload(resolved) == payload
    assert len(payload) == 34


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


def test_cell_parser_handles_fixed_types_and_blank_inheritance():
    parsed = parse_parameter_cells({
        "layer_local": " FALSE ", "tau_log": " 0.25 ", "fallback_price_window": "7",
        "shape_mode": " Audit ", "tenors": '["M+1", "Q+1"]',
        "shrink_k": " ", "layer_hist": None, "comment": "ignored metadata",
    })
    assert parsed == {
        "layer_local": False, "tau_log": 0.25, "fallback_price_window": 7,
        "shape_mode": "audit", "tenors": ["M+1", "Q+1"],
    }


@pytest.mark.parametrize("name,value", [
    ("layer_local", "off"), ("layer_local", "0"), ("tau_log", "nan"),
    ("tau_log", "inf"), ("tau_log", "0,4"), ("fallback_price_window", "3.5"),
    ("fallback_price_window", "3.0"), ("tenors", "M+1;Q+1"),
    ("tenors", '{"M+1": 1}'), ("tenors", '["M+1", 2]'),
])
def test_cell_parser_does_not_guess_ambiguous_values(name, value):
    with pytest.raises(ValueError, match=name):
        parse_parameter_cells({name: value})


@pytest.mark.parametrize("extension", [".csv", ".xlsx"])
def test_mapping_reads_parameter_columns_in_csv_and_excel(cfg, extension):
    cfg = replace(cfg, mapping_file=cfg.mapping_file.with_suffix(extension), configuration_mode="individual")
    write_mapping(cfg, use="fill", eex_file="DE/Base.csv", basis_mode="additive",
                  fallback_price_window=7, layer_local=False,
                  tenors='["M+1", "Q+1"]', tau_log="")
    products = load_mapping(cfg)
    assert len(products) == 1
    product = products[0]
    assert product.key == ("Power", "North", "EUR/MWh")
    assert product.parameter_overrides == {
        "basis_mode": "additive", "fallback_price_window": 7,
        "layer_local": False, "tenors": ["M+1", "Q+1"],
    }
    assert effective_config(cfg, product).tau_log == cfg.tau_log


def test_unknown_column_in_individual_mode_reports_possible_typo(cfg):
    cfg = replace(cfg, configuration_mode="individual")
    write_mapping(cfg, fallback_price_windw=7)
    with pytest.raises(ValueError, match="unknown individual.*fallback_price_windw"):
        load_mapping(cfg)


def test_bad_cell_error_identifies_row_curve_and_parameter(cfg):
    cfg = replace(cfg, configuration_mode="individual")
    write_mapping(cfg, tau_log="not-a-number")
    with pytest.raises(ValueError, match="row 2.*Power.*North.*tau_log"):
        load_mapping(cfg)


def test_wrong_mapping_extension_is_rejected(cfg):
    cfg = replace(cfg, mapping_file=cfg.mapping_file.with_suffix(".json"))
    cfg.mapping_file.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=".csv or .xlsx"):
        read_mapping_table(cfg.mapping_file)


def test_provenance_contains_effective_values_and_hashes_them_stably(cfg):
    cfg = replace(cfg, configuration_mode="individual")
    product = mapping(basis_mode="additive", tau_log=1)
    original = configuration_record(cfg, product)
    equivalent = configuration_record(replace(cfg, base_dir=Path("elsewhere")),
                                      mapping(tau_log=1.0, basis_mode="additive"))
    assert original == equivalent
    payload = json.loads(original["configuration_parameters"])
    assert payload == model_parameter_payload(effective_config(cfg, product))
    assert original["configuration_mode"] == "individual"
    assert len(original["configuration_id"]) == 64
    assert "base_dir" not in payload and "eex_offset_days" not in payload
    changed = configuration_record(cfg, product, {"tau_log": 2})
    assert changed["configuration_id"] != original["configuration_id"]


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


def test_explicit_command_parameters_override_table_but_trial_values_apply_last(cfg):
    cfg = replace(cfg, configuration_mode="individual",
                  command_overrides={"shape_adjust_originals": False, "tau_log": 0.7})
    product = mapping(shape_adjust_originals=True, tau_log=0.9)
    resolved = effective_config(cfg, product)
    assert resolved.shape_adjust_originals is False and resolved.tau_log == 0.7
    trial = effective_config(cfg, product, {"tau_log": 1.3})
    assert trial.tau_log == 1.3 and trial.shape_adjust_originals is False
    assert cfg.command_overrides == {"shape_adjust_originals": False, "tau_log": 0.7}


def test_operational_fields_are_not_valid_command_model_overrides(cfg):
    with pytest.raises(ValueError, match="eex_offset_days"):
        validate_config(replace(cfg, command_overrides={"eex_offset_days": -1}))
