"""Protect published history against failed loads, serialization and replacement."""

from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from vwaps import cli, publication
from vwaps.config import load_config
from vwaps.fill import RunResult
from vwaps.io_eex import REQUIRED
from vwaps.mapping import ProductMap
from vwaps.publication import CsvBatch, publication_lock


DAY = date(2026, 9, 25)
SATURDAY = date(2026, 9, 26)


@pytest.fixture
def project(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[paths]\nvwap_input="own.csv"\nmapping="mapping.csv"\n'
                      'eex_curves_dir="curves"\noutput_dir="output"\n'
                      '[targets]\ntenors=["M+1", "M+2"]\n[layers]\nhist="on"\n', encoding="utf-8")
    pd.DataFrame([dict(reference_date=DAY, product="TEST", region="DE", unit="EUR/MWh",
                       tenor="M+1", vwap=110.0, volume=100)]).to_csv(tmp_path / "own.csv", index=False)
    mapping = ProductMap("TEST", "fill", "DE", "Base", "eex.csv", "Base", "Europe/Berlin",
                         region="DE", unit="EUR/MWh")
    pd.DataFrame([asdict(mapping)]).to_csv(tmp_path / "mapping.csv", index=False)
    source = tmp_path / "curves" / "eex.csv"
    source.parent.mkdir()
    pd.DataFrame([(DAY, "Month", date(2026, 10, 1), 100),
                  (DAY, "Month", date(2026, 11, 1), 120)], columns=REQUIRED).to_csv(source, index=False)
    return config


def snapshot(output):
    return {p.relative_to(output): p.read_bytes() for p in output.rglob("*.csv")}


def engine_result(price, day=DAY):
    row = dict(reference_date=day, product="TEST", region="DE", unit="EUR/MWh",
               tenor="M+1", delivery_start="2026-10-01", price=price, source="own")
    enriched = {f"curve_{key}": value for key, value in row.items()}
    return RunResult(pd.DataFrame([row]), pd.DataFrame()), pd.DataFrame([enriched])


@pytest.mark.parametrize("destination", ["filled_history.csv", "enriched_history.csv"])
def test_serialization_failure_preserves_every_existing_output(project, monkeypatch, destination):
    cfg = load_config(project)
    result, enriched = engine_result(110)
    cli._write(cfg, result, enriched)
    before = snapshot(cfg.output_dir)
    real = pd.DataFrame.to_csv

    def broken(frame, path, *args, **kwargs):
        if Path(path).name.startswith(f".{destination}."):
            Path(path).write_text("partial serialization", encoding="utf-8")
            raise OSError("Injected serialization failure")
        return real(frame, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", broken)
    result, enriched = engine_result(115)
    with pytest.raises(OSError, match="serialization failure"):
        cli._write(cfg, result, enriched)
    assert snapshot(cfg.output_dir) == before
    assert not list(cfg.output_dir.rglob("*.tmp"))


@pytest.mark.parametrize("new_day", [False, True])
def test_replacement_failure_rolls_back_existing_and_new_outputs(project, monkeypatch, new_day):
    cfg = load_config(project)
    result, enriched = engine_result(110)
    cli._write(cfg, result, enriched)
    before = snapshot(cfg.output_dir)
    real = publication.os.replace
    injected = False

    def broken(source, destination):
        nonlocal injected
        if Path(destination).name == "enriched_history.csv" and not injected:
            injected = True
            raise OSError("Injected replacement failure")
        return real(source, destination)

    monkeypatch.setattr(publication.os, "replace", broken)
    result, enriched = engine_result(115, SATURDAY if new_day else DAY)
    with pytest.raises(OSError, match="previous output files were restored"):
        cli._write(cfg, result, enriched)
    assert injected
    assert snapshot(cfg.output_dir) == before
    assert not list(cfg.output_dir.rglob("*.tmp"))


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("new_day", [False, True])
def test_interrupt_immediately_after_successful_replace_rolls_back_and_is_preserved(
    project, monkeypatch, interruption_type, new_day,
):
    cfg = load_config(project)
    result, enriched = engine_result(110)
    cli._write(cfg, result, enriched)
    before = snapshot(cfg.output_dir)
    real = publication.os.replace
    interruption = interruption_type("Injected interrupt immediately after OS rename")
    injected = False

    def interrupted(source, destination):
        nonlocal injected
        real(source, destination)
        if not injected:
            injected = True
            raise interruption

    monkeypatch.setattr(publication.os, "replace", interrupted)
    result, enriched = engine_result(115, SATURDAY if new_day else DAY)
    with pytest.raises(interruption_type) as caught:
        cli._write(cfg, result, enriched)
    assert caught.value is interruption
    assert snapshot(cfg.output_dir) == before
    assert not list(cfg.output_dir.rglob("*.tmp"))
    with publication_lock(cfg.output_dir):
        pass


def test_cleanup_failure_does_not_mask_original_serialization_error(project, monkeypatch):
    cfg = load_config(project)
    result, enriched = engine_result(110)
    cli._write(cfg, result, enriched)
    before = snapshot(cfg.output_dir)
    original_error = ValueError("Injected serialization error")
    real_unlink = Path.unlink

    def broken_write(*args, **kwargs):
        raise original_error

    def broken_cleanup(path, *args, **kwargs):
        if path.name.endswith(".stage.tmp"):
            raise PermissionError("Injected cleanup error")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", broken_write)
    monkeypatch.setattr(Path, "unlink", broken_cleanup)
    result, enriched = engine_result(115)
    with pytest.raises(ValueError, match="serialization error") as caught:
        cli._write(cfg, result, enriched)
    assert caught.value is original_error
    assert any("cleanup error" in note for note in original_error.__notes__)
    assert snapshot(cfg.output_dir) == before


def test_cleanup_failure_after_success_preserves_publication_and_recovery_files(project, monkeypatch):
    cfg = load_config(project)
    result, enriched = engine_result(110)
    cli._write(cfg, result, enriched)
    real_unlink = Path.unlink

    def broken_cleanup(path, *args, **kwargs):
        if path.name.endswith(".recovery.tmp"):
            raise PermissionError("Injected backup cleanup error")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", broken_cleanup)
    result, enriched = engine_result(115)
    cli._write(cfg, result, enriched)
    assert pd.read_csv(cfg.output_dir / "filled_history.csv").price.tolist() == [115]
    assert list(cfg.output_dir.rglob("*.recovery.tmp"))


def test_rollback_failure_keeps_recovery_copy(project, monkeypatch):
    cfg = load_config(project)
    result, enriched = engine_result(110)
    cli._write(cfg, result, enriched)
    original = (cfg.output_dir / "filled" / f"{DAY}.csv").read_bytes()
    real = publication.os.replace

    def broken(source, destination):
        if Path(destination).name == "filled_history.csv" or ".recovery.tmp" in str(source):
            raise OSError("Injected persistent replacement failure")
        return real(source, destination)

    monkeypatch.setattr(publication.os, "replace", broken)
    result, enriched = engine_result(115)
    with pytest.raises(OSError, match="rollback was incomplete.*recovery files"):
        cli._write(cfg, result, enriched)
    recovery = list(cfg.output_dir.rglob("*.recovery.tmp"))
    assert len(recovery) == 1
    assert recovery[0].read_bytes() == original


def test_writer_lock_prevents_simultaneous_merge_and_releases_on_exception(tmp_path):
    with pytest.raises(RuntimeError, match="leave"):
        with publication_lock(tmp_path):
            with pytest.raises(ValueError, match="Another writer"):
                with publication_lock(tmp_path):
                    pytest.fail("The same output directory must exclude concurrent writers")
            raise RuntimeError("leave")
    with publication_lock(tmp_path):
        pass


@pytest.mark.parametrize("corruption", ["missing_columns", "all_nonfinite", "unsupported_contracts"])
def test_corrupt_present_eex_aborts_before_any_results_are_overwritten(project, corruption):
    cfg = load_config(project)
    args = ["daily", "--date", DAY.isoformat()]
    assert cli.main(args, project) == 0
    before = snapshot(cfg.output_dir)
    path = cfg.eex_curves_dir / "eex.csv"
    if corruption == "missing_columns":
        path.write_text("wrong_header\nbroken\n", encoding="utf-8")
    else:
        frame = pd.read_csv(path)
        if corruption == "all_nonfinite":
            frame["settlPx"] = ["inf", "invalid"]
        else:
            frame["maturityType"] = "unsupported"
        frame.to_csv(path, index=False)
    assert cli.main(args, project) == 1
    assert snapshot(cfg.output_dir) == before


def test_cli_write_failure_returns_error_without_replacing_results(project, monkeypatch):
    cfg = load_config(project)
    args = ["daily", "--date", DAY.isoformat()]
    assert cli.main(args, project) == 0
    before = snapshot(cfg.output_dir)
    real = pd.DataFrame.to_csv

    def broken(frame, path, *args, **kwargs):
        if Path(path).name.startswith(".enriched_history.csv."):
            raise OSError("Injected final serialization failure")
        return real(frame, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", broken)
    assert cli.main(args, project) == 1
    assert snapshot(cfg.output_dir) == before


def test_text_serialization_failure_does_not_publish_earlier_staged_csv(tmp_path, monkeypatch):
    csv = tmp_path / "report.csv"
    audit = tmp_path / "selected.json"
    csv.write_text("old report", encoding="utf-8")
    audit.write_text('{"version":"old"}', encoding="utf-8")
    real = Path.write_text

    def broken(path, *args, **kwargs):
        if path.name.startswith(".selected.json."):
            raise OSError("Injected JSON write failure")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", broken)
    with pytest.raises(OSError, match="JSON write failure"):
        with CsvBatch() as batch:
            batch.stage(csv, pd.DataFrame({"new": [1]}))
            batch.stage_text(audit, '{"version":"new"}')
    assert csv.read_text(encoding="utf-8") == "old report"
    assert audit.read_text(encoding="utf-8") == '{"version":"old"}'


@pytest.mark.parametrize("invalid", ["missing_columns", "bad_date", "bad_price"])
def test_invalid_optional_truth_preserves_previous_backtest_reports(project, invalid):
    cfg = load_config(project)
    assert cli.main(["backtest"], project) == 0
    before = snapshot(cfg.output_dir)
    truth = pd.DataFrame([dict(date=DAY.isoformat(), product="TEST", region="DE",
                               unit="EUR/MWh", tenor="M+2", truth=125.0)])
    if invalid == "missing_columns":
        truth = truth.drop(columns="region")
    elif invalid == "bad_date":
        truth["date"] = "invalid"
    else:
        truth["truth"] = "invalid"
    truth.to_csv(project.parent / "bad_truth.csv", index=False)
    assert cli.main(["backtest", "--truth", "bad_truth.csv"], project) == 1
    assert snapshot(cfg.output_dir) == before


def test_explicit_weekend_daily_uses_available_history_and_eex(project):
    cfg = load_config(project)
    assert cli.main(["daily", "--date", SATURDAY.isoformat()], project) == 0
    filled = pd.read_csv(cfg.output_dir / "filled_history.csv")
    assert set(filled.reference_date) == {SATURDAY.isoformat()}
    assert filled.price.notna().all()
    assert set(filled.source) == {"eex+hist"}


def test_refill_includes_invalid_original_weekend_and_matches_catchup(project):
    cfg = load_config(project)
    raw = pd.read_csv(cfg.resolve(cfg.vwap_input))
    weekend = raw.assign(reference_date=SATURDAY.isoformat(), vwap="invalid")
    pd.concat([raw, weekend], ignore_index=True).to_csv(cfg.resolve(cfg.vwap_input), index=False)
    assert cli.main(["refill", "--from", DAY.isoformat(), "--to", SATURDAY.isoformat()], project) == 0
    filled = pd.read_csv(cfg.output_dir / "filled_history.csv")
    assert set(filled.reference_date) == {DAY.isoformat(), SATURDAY.isoformat()}
    assert filled.price.notna().all()
    before = snapshot(cfg.output_dir)
    assert cli.main(["catchup", "--from", DAY.isoformat(), "--to", SATURDAY.isoformat()], project) == 0
    assert snapshot(cfg.output_dir) == before
