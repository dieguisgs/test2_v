"""Reject malformed user settings before they can change calculation behavior."""

from dataclasses import replace
from pathlib import Path

import pytest

from vwaps import cli
from vwaps.config import load_config, validate_config


def test_invalid_configuration_returns_command_error_before_dispatch(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[targets]\ntenors="M+1"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "cmd_daily", lambda *args: pytest.fail("Invalid config reached engine"))
    assert cli.main(["daily"], path) == 1


def test_shipped_configuration_is_valid():
    validate_config(load_config(Path(__file__).resolve().parents[1] / "config.toml"))
