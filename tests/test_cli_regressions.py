from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from vwaps import cli
from vwaps.config import load_config
from vwaps.fill import RunResult
from vwaps.identity import IDENTITY_COLUMNS
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap, guess_row
from vwaps.tenors import resolve_tenor


DAY = date(2026, 9, 21)


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    return replace(load_config(path), tenors=["M+1", "M+2"], layer_hist="off",
                   layer_cross=False, layer_arbitrage=False, warmup_days=0,
                   eex_curves_dir=tmp_path / "curves")


def mapping(region="North", unit="EUR/MWh"):
    return ProductMap("SHARED", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin",
                      region=region, unit=unit)


def engine_row(m, **extra):
    return {"reference_date": DAY, "product": m.product, "region": m.region, "unit": m.unit,
            "tenor": "Q+1", "source": "eex", "price": 101.0, **extra}


def quote_frame(prices):
    rows = []
    for tenor, price in prices.items():
        period = resolve_tenor(tenor, DAY)
        rows.append((DAY, period.kind, period.start, price))
    return pd.DataFrame(rows, columns=REQUIRED)


def test_recalculation_removes_stale_consistency_rows_but_preserves_other_curves(cfg):
    first, second = mapping(), mapping("South")
    rows = [engine_row(m) for m in (first, second)]
    consistency = [engine_row(m, from_parts=100.0, deviation=1.0) for m in (first, second)]
    cli._write(cfg, RunResult(pd.DataFrame(rows), pd.DataFrame(consistency)), pd.DataFrame())
    path = cfg.output_dir / "consistency_history.csv"

    # The first curve no longer produces any consistency comparison.
    first_run = RunResult(pd.DataFrame([engine_row(first)]), pd.DataFrame())
    cli._write(cfg, first_run, pd.DataFrame())
    remaining = pd.read_csv(path, keep_default_na=False)
    assert list(remaining.region) == ["South"]

    second_run = RunResult(pd.DataFrame([engine_row(second)]), pd.DataFrame())
    cli._write(cfg, second_run, pd.DataFrame())
    cli._write(cfg, second_run, pd.DataFrame())  # Repeating an empty report retains its schema.
    cleared = pd.read_csv(path)
    assert cleared.empty
    assert set(cli.KEYS).issubset(cleared.columns)


def test_empty_consistency_without_prior_file_does_not_create_headerless_csv(cfg):
    path = cfg.output_dir / "consistency_history.csv"
    cli._upsert(path, pd.DataFrame(), pd.DataFrame([engine_row(mapping())])[cli.KEYS])
    assert not path.exists()


def test_detect_conventions_reports_each_unit_separately(cfg, monkeypatch):
    maps = [mapping(unit="EUR/MWh"), mapping(unit="GBP/MWh")]
    rows = [dict(date=DAY, product=m.product, region=m.region, unit=m.unit,
                 tenor="D+1", vwap=price, volume=10.0) for m, price in zip(maps, [101.0, 110.0])]
    quotes = EexBook(quote_frame({"D+1": 100.0}))
    monkeypatch.setattr(cli, "_load", lambda *_: (pd.DataFrame(rows), None, maps,
                                                 {m.key: quotes for m in maps}))
    messages = []
    monkeypatch.setattr(cli, "_out", lambda message: messages.append(str(message)))
    assert cli.cmd_detect(cfg, SimpleNamespace(vwap=None)) == 0
    euros = [message for message in messages if "unit='EUR/MWh'" in message]
    pounds = [message for message in messages if "unit='GBP/MWh'" in message]
    assert len(euros) == len(pounds) == 4
    assert all("median=   1.00" in message for message in euros)
    assert all("median=  10.00" in message for message in pounds)


def test_synthetic_generation_activates_only_temporary_drafts(cfg):
    cfg = replace(cfg, tenors=["D+1"])
    source = cfg.eex_curves_dir / "DE" / "Base.csv"
    source.parent.mkdir(parents=True)
    quote_frame({"D+1": 100.0}).to_csv(source, index=False)
    assert guess_row("DE_Base load", cfg, unit="EUR/MWh")["use"] == "off"
    args = SimpleNamespace(areas="DE", profiles="Base", start=DAY, end=DAY,
                           seed=7, mode="ratio", out="synthetic")
    assert cli.cmd_synthetic(cfg, args) == 0
    truth = pd.read_csv(cfg.resolve(args.out) / "synthetic_truth.csv", keep_default_na=False)
    assert len(truth) == 1
    assert truth.loc[0, "eex"] == 100.0 and truth.loc[0, "unit"] == "EUR/MWh"
    assert not cfg.mapping_file.exists()
    assert guess_row("DE_Base load", cfg, unit="EUR/MWh")["use"] == "off"


def test_book_loader_ignores_enabled_but_unassigned_curves(cfg):
    unassigned = replace(mapping(), eex_file="")
    assert cli._books(cfg, [unassigned]) == {}


def test_status_keeps_numeric_looking_curve_identifiers_as_text(cfg, monkeypatch):
    cfg.output_dir.mkdir(parents=True)
    rows = [engine_row(mapping(region)) for region in ("001", "1")]
    pd.DataFrame(rows).to_csv(cfg.output_dir / "filled_history.csv", index=False)
    messages = []
    monkeypatch.setattr(cli, "_out", lambda message: messages.append(str(message)))
    assert cli.cmd_status(cfg, SimpleNamespace(last=10)) == 0
    assert "2 curves" in messages[0]


def test_backtest_truth_preserves_leading_zero_region_identifiers(cfg, monkeypatch):
    maps = [mapping("001"), mapping("1")]
    frame = pd.DataFrame([dict(date=DAY, product=m.product, region=m.region, unit=m.unit,
                               tenor="M+1", vwap=price, volume=10.0)
                          for m, price in zip(maps, [110.0, 120.0])])
    quotes = EexBook(quote_frame({"M+1": 100.0, "M+2": 100.0}))
    monkeypatch.setattr(cli, "_load", lambda *_: (frame, None, maps, {m.key: quotes for m in maps}))
    path = cfg.base_dir / "truth.csv"
    truth = pd.DataFrame([dict(date=DAY, product=m.product, region=m.region, unit=m.unit,
                               tenor="M+2", truth=100.0) for m in maps])
    truth.to_csv(path, index=False)
    args = SimpleNamespace(vwap=None, start=DAY, end=DAY, truth=str(path))
    assert cli.cmd_backtest(cfg, args) == 0
    loo = pd.read_csv(cfg.output_dir / "backtest_loo.csv", dtype={name: str for name in IDENTITY_COLUMNS})
    assert set(loo.region) == {"001", "1"}
