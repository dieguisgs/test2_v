"""Validate optional shape controls and preserve transformed sources in CLI reports."""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from vwaps import cli
from vwaps.config import load_config, validate_shape_config


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[paths]\noutput_dir = "output"\n', encoding="utf-8")
    return path


def write_shape(path, text):
    path.write_text('[paths]\noutput_dir = "output"\n[shape]\n' + text, encoding="utf-8")


def test_absent_shape_section_preserves_safe_defaults(config_path):
    cfg = load_config(config_path)
    assert cfg.shape_mode == "off"
    assert cfg.shape_adjust_originals is False
    assert cfg.shape_coherence_tolerance == 0.01
    assert (cfg.shape_smoothness_weight, cfg.shape_coherence_weight,
            cfg.shape_max_abs_adjustment, cfg.shape_original_weight) == (1.0, 10.0, 10.0, 10.0)


@pytest.mark.parametrize("mode", ["off", "audit", "adjust"])
@pytest.mark.parametrize("allow_originals", [False, True])
def test_shape_toml_accepts_modes_booleans_and_boundary_weights(config_path, mode, allow_originals):
    write_shape(config_path, f'mode="{mode}"\nadjust_originals={str(allow_originals).lower()}\n'
                'smoothness_weight=0\ncoherence_weight=0.0\nmax_abs_adjustment=0.01\noriginal_weight=1\n')
    cfg = load_config(config_path)
    assert cfg.shape_mode == mode and cfg.shape_adjust_originals is allow_originals
    assert cfg.shape_smoothness_weight == cfg.shape_coherence_weight == 0
    assert cfg.shape_max_abs_adjustment == 0.01 and cfg.shape_original_weight == 1


@pytest.mark.parametrize("field", [
    "smoothness_weight", "coherence_weight", "max_abs_adjustment", "original_weight", "coherence_tolerance",
])
@pytest.mark.parametrize("literal", ['"2"', "true", "false", "nan", "inf", "-inf", "-1"])
def test_shape_toml_rejects_invalid_numeric_types_and_values(config_path, field, literal):
    write_shape(config_path, f"{field}={literal}\n")
    with pytest.raises(ValueError, match=f"shape.{field}"):
        load_config(config_path)


@pytest.mark.parametrize("field,literal", [
    ("mode", '"on"'), ("mode", "true"),
    ("adjust_originals", '"false"'), ("adjust_originals", '"true"'),
    ("adjust_originals", "0"), ("adjust_originals", "1"),
    ("max_abs_adjustment", "0"), ("original_weight", "0"), ("original_weight", "0.5"),
])
def test_shape_toml_rejects_invalid_modes_permissions_and_limits(config_path, field, literal):
    write_shape(config_path, f"{field}={literal}\n")
    with pytest.raises(ValueError, match=f"shape.{field}"):
        load_config(config_path)


def test_direct_shape_validation_rejects_string_permissions(config_path):
    cfg = replace(load_config(config_path), shape_adjust_originals="off")
    with pytest.raises(ValueError, match="shape.adjust_originals"):
        validate_shape_config(cfg)


@pytest.mark.parametrize("command", ["daily", "refill", "catchup", "backtest", "tune"])
def test_cli_dispatch_receives_all_shape_overrides_without_changing_toml(config_path, monkeypatch, command):
    captured = []
    before = config_path.read_bytes()
    monkeypatch.setattr(cli, "setup_logging", lambda *args: None)

    def dispatch(cfg, args):
        captured.append(cfg)
        return 19

    monkeypatch.setattr(cli, f"cmd_{command}", dispatch)
    assert cli.main([
        command, "--shape-mode", "adjust", "--shape-adjust-originals", "on",
        "--shape-smoothness-weight", "0", "--shape-coherence-weight", "2.5",
        "--shape-max-abs-adjustment", "3.25", "--shape-original-weight", "4",
        "--shape-coherence-tolerance", "0.05",
    ], config_path) == 19
    assert len(captured) == 1
    cfg = captured[0]
    assert cfg.shape_mode == "adjust" and cfg.shape_adjust_originals is True
    assert cfg.shape_coherence_tolerance == 0.05
    assert (cfg.shape_smoothness_weight, cfg.shape_coherence_weight,
            cfg.shape_max_abs_adjustment, cfg.shape_original_weight) == (0, 2.5, 3.25, 4)
    assert config_path.read_bytes() == before


def test_cli_can_disable_toml_permission_and_preserves_unspecified_controls(config_path, monkeypatch):
    write_shape(config_path, 'mode="audit"\nadjust_originals=true\noriginal_weight=7\n')
    captured = []
    monkeypatch.setattr(cli, "setup_logging", lambda *args: None)
    monkeypatch.setattr(cli, "cmd_daily", lambda cfg, args: captured.append(cfg) or 0)
    assert cli.main(["daily", "--shape-mode", "off", "--shape-adjust-originals", "off"], config_path) == 0
    assert captured[0].shape_mode == "off" and captured[0].shape_adjust_originals is False
    assert captured[0].shape_original_weight == 7


@pytest.mark.parametrize("flag,value", [
    ("--shape-smoothness-weight", "nan"), ("--shape-smoothness-weight", "-1"),
    ("--shape-coherence-weight", "inf"), ("--shape-coherence-weight", "-1"),
    ("--shape-max-abs-adjustment", "0"), ("--shape-max-abs-adjustment", "inf"),
    ("--shape-original-weight", "0.5"), ("--shape-original-weight", "nan"),
])
def test_invalid_cli_numeric_override_stops_before_dispatch(config_path, monkeypatch, flag, value):
    called = []
    monkeypatch.setattr(cli, "setup_logging", lambda *args: None)
    monkeypatch.setattr(cli, "cmd_daily", lambda *args: called.append(True))
    assert cli.main(["daily", flag, value], config_path) == 1
    assert not called


@pytest.mark.parametrize("flag,value", [
    ("--shape-mode", "on"), ("--shape-adjust-originals", "true"),
    ("--shape-smoothness-weight", "false"),
])
def test_cli_rejects_unknown_shape_choices_and_nonnumeric_weights(config_path, flag, value):
    with pytest.raises(SystemExit) as exc:
        cli.main(["daily", flag, value], config_path)
    assert exc.value.code == 2


def transformed_rows():
    return pd.DataFrame([
        dict(reference_date="2026-09-30", product="P", region="DE", unit="EUR/MWh",
             tenor=f"M+{i}", source=source)
        for i, source in enumerate(("own", "own+shape", "eex+local+shape", "missing"), 1)
    ])


def test_summary_counts_shape_sources_in_missing_denominator(monkeypatch):
    tables, messages = [], []
    monkeypatch.setattr(cli, "_table", lambda table, **kwargs: tables.append(table.copy()) or "TABLE")
    monkeypatch.setattr(cli, "_out", lambda value: messages.append(value))
    cli._print_summary(transformed_rows())
    assert tables[0].iloc[0]["% missing"] == 25.0
    assert tables[0].iloc[0]["own+shape"] == tables[0].iloc[0]["eex+local+shape"] == 1
    assert "own+shape 25.0%" in messages[-1] and "eex+local+shape 25.0%" in messages[-1]


def test_status_retains_shape_source_columns(config_path, monkeypatch):
    cfg = load_config(config_path)
    cfg.output_dir.mkdir()
    transformed_rows().to_csv(cfg.output_dir / "filled_history.csv", index=False)
    tables = []
    monkeypatch.setattr(cli, "_table", lambda table, **kwargs: tables.append(table.copy()) or "TABLE")
    monkeypatch.setattr(cli, "_out", lambda value: None)
    assert cli.cmd_status(cfg, SimpleNamespace(last=10)) == 0
    assert tables[0].iloc[0]["own+shape"] == tables[0].iloc[0]["eex+local+shape"] == 1
