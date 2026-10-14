"""Reject malformed user settings before they can change calculation behavior."""

from dataclasses import replace
from pathlib import Path

import pytest

from vwaps import cli
from vwaps.config import load_config, validate_config


@pytest.mark.parametrize("section,field,literal", [
    ("layers", "local", '"false"'), ("layers", "correlation", "1"),
    ("layers", "cross", "0"), ("layers", "arbitrage", '"off"'),
    ("layers", "hist", "false"),
    ("targets", "tenors", '"M+1"'), ("targets", "tenors", '["M+1", 2]'),
    ("targets", "tenors", '["Months+2"]'), ("targets", "tenors", '["Win+0"]'),
    ("method", "tau_log", "nan"), ("method", "tau_log", '"0.5"'),
    ("method", "shrink_k", "inf"), ("method", "other_kind_weight", "-1"),
    ("method", "min_volume", "-1"), ("method", "max_anchor_dev", "true"),
    ("method", "hist_auto_min_obs", "-1"), ("method", "ewma_halflife_days", "0"),
    ("method", "ratio_eex_floor", "0"), ("method", "max_ratio_deviation", "inf"),
    ("method", "hist_max_age_days", "0"), ("method", "hist_max_age_days", "1.5"),
    ("correlation", "halflife_days", "nan"), ("correlation", "prior_obs", "-1"),
    ("cross", "min_corr", "1.1"), ("cross", "min_corr", "nan"),
    ("cross", "min_obs", "-1"), ("cross", "halflife_days", "0"),
    ("eex", "max_stale_days", "-3"), ("eex", "max_stale_days", "false"),
    ("eex", "warn_stale_days", "-1"), ("run", "warmup_days", "1.5"),
    ("conventions", "weekend_offset", "1.5"), ("timezones", "default", "3"),
    ("vwap_columns", "tenor", "false"), ("paths", "output_dir", "false"),
])
def test_invalid_types_and_bounds_are_rejected_with_field_context(tmp_path, section, field, literal):
    path = tmp_path / "config.toml"
    path.write_text(f"[{section}]\n{field}={literal}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=section):
        load_config(path)


def test_valid_boundaries_keep_existing_semantics(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[eex]\nmax_stale_days=0\nwarn_stale_days=0\n'
                    '[method]\nother_kind_weight=2\nhist_auto_min_obs=0\n'
                    '[correlation]\nprior_obs=0\n[cross]\nmin_corr=-1\nmin_obs=0\n'
                    '[conventions]\nweekend_offset=-1\n'
                    '[targets]\ntenors=["M+0", "M+1", "BOM", "Win+1"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.max_stale_days is None
    assert cfg.other_kind_weight == 2 and cfg.weekend_offset == -1
    assert cfg.cross_min_corr == -1


def test_direct_configuration_validation_rejects_nonfinite_replacements(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="tau_log"):
        validate_config(replace(load_config(path), tau_log=float("nan")))


def test_invalid_configuration_returns_command_error_before_dispatch(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[targets]\ntenors="M+1"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "cmd_daily", lambda *args: pytest.fail("Invalid config reached engine"))
    assert cli.main(["daily"], path) == 1


def test_shipped_configuration_is_valid():
    validate_config(load_config(Path(__file__).resolve().parents[1] / "config.toml"))
