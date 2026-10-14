from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tenors import add_months


DAY = date(2026, 9, 30)


@pytest.fixture
def case():
    cfg = replace(
        load_config(Path(__file__).resolve().parents[1] / "config.toml"),
        tenors=["M+1", "M+2", "M+3", "M+4", "M+5", "M+6", "Q+2"],
        layer_local=False, layer_hist="off", layer_cross=False, layer_arbitrage=True,
        basis_mode="auto", warmup_days=0, max_anchor_dev=0, min_volume=0,
        fallback_price_method="simple", fallback_price_window=5,
        fallback_spread_window=2, fallback_anchor_months=2,
    )
    rows = []
    for i, publication in enumerate(pd.bdate_range(end=DAY, periods=5).date):
        rows.append((publication, "Month", date(2026, 10, 1), 110.0))
        if i >= 3:
            rows.extend((publication, "Month", add_months(date(2026, 10, 1), j), 110.0 + 10 * j)
                        for j in range(1, 6))
    book = EexBook(pd.DataFrame(rows, columns=REQUIRED))
    mapping = ProductMap("TEST", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin")
    return cfg, mapping, book


def own(price):
    return pd.DataFrame([(DAY, "TEST", "Q+2", price, 10.0)],
                        columns=["date", "product", "tenor", "vwap", "volume"])


def run(case, observations, loo=False):
    cfg, mapping, book = case
    result = CurveFiller(cfg, observations, [mapping], {mapping.key: book}).run(DAY, DAY, loo=loo)
    assert not result.errors
    return result


def test_configured_loo_reconstructs_from_other_smoothed_targets_like_a_real_masked_run(case):
    observations = own(155.0)
    masked = run(case, observations.iloc[:0])
    quarter = masked.filled.set_index("tenor").loc["Q+2"]
    assert set(masked.filled[masked.filled.kind == "Month"].source) == {"eex+smooth"}
    assert quarter.source == "arbitrage"
    assert quarter.estimation_method == "contract_strip"
    assert "eex_fallback_unavailable" in quarter.flag
    assert quarter.price == pytest.approx(149.99536822603056)

    evaluated = run(case, observations, loo=True)
    prediction = evaluated.loo[evaluated.loo.method == "pipeline_configured"]
    assert len(prediction) == 1
    assert prediction.iloc[0].tenor == "Q+2"
    assert prediction.iloc[0].pred == pytest.approx(quarter.price)
    assert prediction.iloc[0].error == pytest.approx(quarter.price - 155.0)


def test_changing_held_out_original_does_not_change_reconstruction_prediction(case):
    predictions = []
    for observed in (155.0, 999.0, -25.0):
        result = run(case, own(observed), loo=True)
        published_own = result.filled.set_index("tenor").loc["Q+2"]
        assert published_own.source == "own"
        assert published_own.price == observed
        prediction = result.loo[result.loo.method == "pipeline_configured"]
        assert len(prediction) == 1
        assert prediction.iloc[0].own == observed
        predictions.append(prediction.iloc[0].pred)
    assert predictions == pytest.approx([149.99536822603056] * 3)
