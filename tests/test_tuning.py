"""Parameter selection uses comparable earlier observations, never holdout fit."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tuning import tune_parameters


@pytest.fixture
def dataset(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    cfg = replace(load_config(config), tenors=["M+1", "M+2", "M+3"], layer_arbitrage=False)
    days = list(pd.bdate_range("2026-09-01", periods=12).date)
    mapping = ProductMap("P", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin",
                         region="North", unit="EUR/MWh")
    own, settlements = [], []
    for i, day in enumerate(days):
        for n in (1, 2, 3):
            eex = n * (100.0 + i)
            own.append(dict(date=day, product="P", region="North", unit="EUR/MWh",
                            tenor=f"M+{n}", vwap=eex + 10.0, volume=100.0))
            settlements.append((day, "Month", date(2026, 9 + n, 1), eex))
    frame = pd.DataFrame(own)
    books = {mapping.key: EexBook(pd.DataFrame(settlements, columns=REQUIRED))}
    return cfg, frame, [mapping], books, days


def tune(dataset, grid=None, **kwargs):
    cfg, own, maps, books, days = dataset
    return tune_parameters(cfg, own, maps, books, days[0], days[-1],
                           grid or {"basis_mode": ["auto", "ratio", "additive"]},
                           kwargs.get("validation_days", 3), kwargs.get("max_trials", 50))


def test_limited_warmup_is_rejected_without_mutating_config(dataset):
    cfg, *rest = dataset
    limited = replace(cfg, warmup_days=5)
    with pytest.raises(ValueError, match="warmup_days = 0"):
        tune((limited, *rest))
    assert limited.warmup_days == 5


def fake_loo(start, end, model_error, baseline_error, units=("EUR/MWh",)):
    rows = []
    for day in pd.bdate_range(start, end).date:
        for unit in units:
            for method, error in (("pipeline_configured", model_error), ("eex", baseline_error)):
                rows.append(dict(reference_date=day, product="P", region="North", unit=unit,
                                 tenor="M+1", method=method, own=0.0, pred=error))
    return pd.DataFrame(rows)


@pytest.mark.parametrize("failure", ["engine", "duplicate", "different_cases", "different_truth", "nonfinite"])
def test_candidate_failure_aborts_instead_of_selecting_a_partial_winner(dataset, monkeypatch, failure):
    def broken(self, start, end, loo=False):
        rows = fake_loo(start, end, 1.0, 2.0)
        errors = []
        if self.cfg.basis_mode == "ratio":
            if failure == "engine":
                errors = [dict(product="P", phase="prepare")]
            elif failure == "duplicate":
                rows = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)
            elif failure == "different_cases":
                rows = rows[rows["reference_date"] != start].copy()
            elif failure == "different_truth":
                rows.loc[0, "own"] = 123.0
            else:
                rows.loc[0, "pred"] = float("inf")
        return SimpleNamespace(errors=errors, loo=rows)

    monkeypatch.setattr(CurveFiller, "run", broken)
    with pytest.raises(ValueError):
        tune(dataset, {"basis_mode": ["auto", "ratio"]})


