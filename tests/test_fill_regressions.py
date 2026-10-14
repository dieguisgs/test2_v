from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tenors import resolve_tenor


@pytest.fixture
def cfg():
    return replace(
        load_config(Path(__file__).resolve().parents[1] / "config.toml"),
        tenors=["M+1", "M+2"], warmup_days=0, max_anchor_dev=0, min_volume=0, )


def _mapping(product):
    return ProductMap(product, "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin")


def _book(observations):
    rows = []
    for day, prices in observations:
        for tenor, price in prices.items():
            period = resolve_tenor(tenor, day)
            rows.append((day, period.kind, period.start, price))
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def _own(rows):
    return pd.DataFrame(rows, columns=["date", "product", "tenor", "vwap", "volume"])


def test_peak_weekend_quote_does_not_populate_zero_hour_bow(cfg):
    cfg = replace(cfg, tenors=["WE", "BOW"])
    day = date(2026, 9, 25)  # Friday: WE and BOW share dates, not delivery hours.
    own = _own([(day, "TEST", "WE", 90.0, 20.0)])
    mapping = replace(_mapping("TEST"), profile="Peak", hours="Peak")
    result = CurveFiller(cfg, own, [mapping], {"TEST": None}).run(day, day)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert output.loc["WE", "hours"] == 24.0
    assert output.loc["WE", "price"] == 90.0
    assert output.loc["WE", "source"] == "own"
    assert output.loc["BOW", "hours"] == 0.0
    assert output.loc["BOW", "source"] == "missing"
    assert pd.isna(output.loc["BOW", "price"])
    assert pd.isna(output.loc["BOW", "own_vwap"])
    assert output.loc["BOW", "flag"] == "zero_delivery_hours"


def test_zero_hour_own_contract_cannot_supply_peak_weekend_or_residual(cfg):
    cfg = replace(cfg, tenors=["WE", "BOW", "D+2"])
    day = date(2026, 9, 25)
    # A BOW quote must not become a Weekend parent for Sunday's residual.
    own = _own([(day, "TEST", "BOW", 30.0, 20.0),
                (day, "TEST", "D+1", 20.0, 20.0)])
    snapshot = own.copy(deep=True)
    mapping = replace(_mapping("TEST"), profile="Peak", hours="Peak")
    result = CurveFiller(cfg, own, [mapping], {"TEST": None}).run(day, day)
    assert not result.errors
    assert result.filled.source.eq("missing").all()
    assert result.filled.price.isna().all()
    pd.testing.assert_frame_equal(own, snapshot)


@pytest.mark.parametrize("reverse", [False, True])
def test_zero_hour_alias_does_not_change_valid_peak_weekend_quote(cfg, reverse):
    cfg = replace(cfg, tenors=["WE", "BOW"])
    day = date(2026, 9, 25)
    rows = [(day, "TEST", "BOW", 200.0, 200.0), (day, "TEST", "WE", 90.0, 20.0)]
    own = _own(rows[::-1] if reverse else rows)
    mapping = replace(_mapping("TEST"), profile="Peak", hours="Peak")
    result = CurveFiller(cfg, own, [mapping], {"TEST": None}).run(day, day)
    assert not result.errors
    output = result.filled.set_index("tenor")
    assert output.loc["WE", "price"] == 90.0
    assert output.loc["WE", "own_volume"] == 20.0
    assert output.loc["WE", "source"] == "own"
    assert output.loc["BOW", "source"] == "missing"
