"""Contract reconstruction is opt-in; EEX reference pricing stays available."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap


DAY = date(2026, 9, 30)


def configured(tmp_path, layers=""):
    path = tmp_path / "config.toml"
    path.write_text('[targets]\ntenors = ["M+1", "M+2", "M+3", "Q+1"]\n' + layers,
                    encoding="utf-8")
    return load_config(path)


def mapping():
    # A reviewed, assigned curve remains active even when its book is absent.
    return ProductMap("P", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin",
                      region="North", unit="EUR/MWh")


def observations(tenors):
    return pd.DataFrame([
        dict(date=DAY, product="P", region="North", unit="EUR/MWh",
             tenor=tenor, vwap=120.0, volume=20.0)
        for tenor in tenors
    ])


@pytest.mark.parametrize("contents", ["", '[layers]\nlocal = true\nhist = "on"\n'])
def test_omitting_arbitrage_disables_it_with_or_without_layers(tmp_path, contents):
    path = tmp_path / "config.toml"
    path.write_text(contents, encoding="utf-8")
    assert load_config(path).layer_arbitrage is False


@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_arbitrage_setting_is_respected(tmp_path, enabled):
    cfg = configured(tmp_path, f"[layers]\narbitrage = {str(enabled).lower()}\n")
    assert cfg.layer_arbitrage is enabled


def test_repository_configuration_disables_arbitrage():
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
    assert cfg.layer_arbitrage is False


@pytest.mark.parametrize("enabled", [False, True])
def test_quarter_reconstruction_from_own_months_requires_explicit_enablement(tmp_path, enabled):
    layers = "[layers]\narbitrage = true\n" if enabled else ""
    cfg = configured(tmp_path, layers)
    curve = mapping()
    own = observations(["M+1", "M+2", "M+3"])
    result = CurveFiller(cfg, own, [curve], {curve.key: None}).run(DAY, DAY)
    assert not result.errors
    assert cfg.layer_arbitrage is enabled
    output = result.filled.set_index("tenor")
    months = output.loc[["M+1", "M+2", "M+3"]]
    assert months["price"].eq(120.0).all()
    assert months["source"].eq("own").all()
    assert months["data_origin"].eq("original").all()
    assert months["estimation_method"].eq("none").all()
    quarter = output.loc["Q+1"]
    if enabled:
        assert quarter["price"] == pytest.approx(120.0)
        assert quarter["source"] == "arbitrage"
        assert quarter["data_origin"] == "estimated"
        assert quarter["estimation_method"] == "contract_strip"
    else:
        assert pd.isna(quarter["price"])
        assert quarter["source"] == quarter["data_origin"] == "missing"
        assert quarter["estimation_method"] == "unavailable"


def test_disabled_arbitrage_still_prices_eex_quarter_from_months_for_local_adjustment(tmp_path):
    cfg = configured(tmp_path)
    assert cfg.layer_arbitrage is False
    curve = mapping()
    # There is no EEX quarterly quote. The EEX reference pricer must still
    # build Q4 from its three monthly settlements before applying own basis.
    book = EexBook(pd.DataFrame([
        (DAY, "Month", date(2026, month, 1), 100.0) for month in (10, 11, 12)
    ], columns=REQUIRED))
    result = CurveFiller(cfg, observations(["M+1"]), [curve], {curve.key: book}).run(DAY, DAY)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert output.loc["M+1", "price"] == 120.0
    assert output.loc["M+1", "source"] == "own"
    quarter = output.loc["Q+1"]
    assert quarter["eex_method"] == "strip"
    assert quarter["eex_settle"] == pytest.approx(100.0)
    assert quarter["source"] == "eex+local"
    assert quarter["data_origin"] == "estimated"
    assert quarter["estimation_method"] == "ratio_local"
    assert 100.0 < quarter["price"] < 120.0
