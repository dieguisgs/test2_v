"""CLI availability overrides and safe reuse of saved EEX policies."""

from datetime import date

import pandas as pd
import pytest

from vwaps import cli
from vwaps.io_eex import REQUIRED
from vwaps.mapping import COLUMNS


DAY = date(2026, 9, 21)
CURVE = ("POWER", "DE", "EUR/MWh")


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch):
    monkeypatch.setattr(cli, "setup_logging", lambda *_: None)


@pytest.mark.parametrize("command", ["daily", "refill", "catchup", "backtest", "tune"])
@pytest.mark.parametrize("offset", [0, -1, -2])
def test_cli_override_is_applied_before_dispatch(tmp_path, monkeypatch, command, offset):
    path = tmp_path / "config.toml"
    path.write_text('[eex]\noffset_days=-3\n', encoding="utf-8")
    received = []
    monkeypatch.setattr(cli, f"cmd_{command}", lambda cfg, args: received.append(cfg.eex_offset_days) or 0)
    assert cli.main([command, "--eex-offset-days", str(offset)], path) == 0
    assert received == [offset]


@pytest.mark.parametrize("command", ["daily", "refill", "catchup", "backtest", "tune"])
def test_positive_cli_offset_is_rejected_before_dispatch(tmp_path, monkeypatch, command):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, f"cmd_{command}", lambda *_: pytest.fail("Invalid override reached command"))
    assert cli.main([command, "--eex-offset-days", "1"], path) == 1


def test_omitted_override_keeps_configured_offset(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[eex]\noffset_days=-2\n', encoding="utf-8")
    received = []
    monkeypatch.setattr(cli, "cmd_daily", lambda cfg, args: received.append(cfg.eex_offset_days) or 0)
    assert cli.main(["daily"], path) == 0
    assert received == [-2]


def _history(path, *, enriched=False, offset=None, day=DAY, tenor="M+1", region="DE"):
    prefix = "curve_" if enriched else ""
    row = dict(reference_date=day, product="POWER", region=region, unit="EUR/MWh", tenor=tenor)
    if offset is not None:
        row["eex_offset_days"] = offset
    pd.DataFrame([{prefix + key: value for key, value in row.items()}]).to_csv(path, index=False)


@pytest.mark.parametrize("enriched", [False, True])
@pytest.mark.parametrize("saved", [None, "", "0", "-2", "bad", "nan", "inf", "-1.5", "1"])
def test_catchup_rejects_conflicting_or_invalid_existing_policy(tmp_path, enriched, saved):
    path = tmp_path / "history.csv"
    _history(path, enriched=enriched, offset=saved)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Run refill.*--eex-offset-days -1"):
        cli._history_point_keys(path, enriched=enriched, eex_offset_days=-1,
                                required_points={(DAY, *CURVE, "M+1")})
    assert path.read_bytes() == before


@pytest.mark.parametrize("enriched", [False, True])
@pytest.mark.parametrize("saved, requested", [(None, 0), ("", 0), ("0", 0), ("-1", -1), ("-1.0", -1)])
def test_catchup_accepts_matching_policy_and_legacy_zero(tmp_path, enriched, saved, requested):
    path = tmp_path / "history.csv"
    _history(path, enriched=enriched, offset=saved)
    assert cli._history_point_keys(path, enriched=enriched, eex_offset_days=requested,
                                   required_points={(DAY, *CURVE, "M+1")}) == {(DAY, *CURVE, "M+1")}


@pytest.mark.parametrize("overrides", [dict(day=date(2026, 9, 18)), dict(tenor="M+2"), dict(region="FR")])
def test_catchup_policy_check_excludes_unrequested_dates_curves_and_tenors(tmp_path, overrides):
    path = tmp_path / "history.csv"
    _history(path, offset="invalid but outside requested scope", **overrides)
    cli._history_point_keys(path, eex_offset_days=-1, required_points={(DAY, *CURVE, "M+1")})


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[paths]\nvwap_input="input.csv"\nmapping="mapping.csv"\neex_curves_dir="."\n'
        'output_dir="output"\n[targets]\ntenors=["M+1","M+2"]\n'
        '[layers]\nhist="off"\n[method]\nbasis_mode="additive"\n', encoding="utf-8",
    )
    pd.DataFrame([dict(product="POWER", region="DE", unit="EUR/MWh", use="fill", area="DE",
                       profile="Base", eex_file="eex.csv", hours="Base", timezone="Europe/Berlin",
                       comment="test")], columns=COLUMNS).to_csv(tmp_path / "mapping.csv", index=False)
    pd.DataFrame([dict(reference_date=DAY, product="POWER", region="DE", unit="EUR/MWh",
                       tenor="M+1", vwap=115.0, volume=100)]).to_csv(tmp_path / "input.csv", index=False)
    pd.DataFrame([
        (publication, "Month", date(2026, 9 + month, 1), price * month)
        for publication, price in ((date(2026, 9, 18), 100.0), (DAY, 110.0)) for month in (1, 2)
    ], columns=REQUIRED).to_csv(tmp_path / "eex.csv", index=False)
    return path


def test_catchup_requires_explicit_refill_when_offset_changes(project):
    assert cli.main(["daily", "--date", str(DAY)], project) == 0
    folder = project.parent / "output"
    before = {path: path.read_bytes() for path in folder.rglob("*.csv")}
    args = ["catchup", "--from", str(DAY), "--to", str(DAY), "--eex-offset-days", "-1"]
    assert cli.main(args, project) == 1
    assert {path: path.read_bytes() for path in folder.rglob("*.csv")} == before
    assert cli.main(["refill", *args[1:]], project) == 0
    result = pd.read_csv(folder / "filled_history.csv")
    assert set(result.eex_offset_days) == {-1}
    assert set(result.eex_asof) == {"2026-09-18"}
    assert set(result.eex_cutoff_date) == {"2026-09-20"}
    before = {path: path.read_bytes() for path in folder.rglob("*.csv")}
    assert cli.main(args, project) == 0
    assert {path: path.read_bytes() for path in folder.rglob("*.csv")} == before


def test_enriched_policy_conflict_is_not_hidden_by_matching_filled_history(project):
    args = ["daily", "--date", str(DAY), "--eex-offset-days", "-1"]
    assert cli.main(args, project) == 0
    folder = project.parent / "output"
    path = folder / "enriched_history.csv"
    enriched = pd.read_csv(path)
    enriched["curve_eex_offset_days"] = 0
    enriched.to_csv(path, index=False)
    before = {path: path.read_bytes() for path in folder.rglob("*.csv")}
    assert cli.main(["catchup", "--from", str(DAY), "--to", str(DAY),
                     "--eex-offset-days", "-1"], project) == 1
    assert {path: path.read_bytes() for path in folder.rglob("*.csv")} == before
