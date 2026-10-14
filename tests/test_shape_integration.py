"""Exercise shape adjustment through production filling, enrichment and LOO."""

import json
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.enrich import enrich_input
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tenors import resolve_tenor


DAY = date(2026, 9, 28)
TENORS = [f"M+{i}" for i in range(1, 7)] + ["Q+2"]


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    cfg = replace(load_config(path), tenors=TENORS, basis_mode="additive",
                  layer_hist="off", layer_cross=False, layer_correlation=False,
                  layer_arbitrage=False, shape_mode="adjust")
    mapping = ProductMap("P", "fill", "DE", "Base", "eex.csv", "Base", "Europe/Berlin",
                         region="DE", unit="EUR/MWh")
    prices = dict(zip(TENORS, [100, 110, 120, 150, 150, 150, 150]))
    eex = [(day, resolve_tenor(tenor, day).kind, resolve_tenor(tenor, day).start, price)
           for day in (DAY, DAY + timedelta(days=1)) for tenor, price in prices.items()]
    book = EexBook(pd.DataFrame(eex, columns=REQUIRED))
    own = pd.DataFrame([
        dict(date=DAY, product="P", region="DE", unit="EUR/MWh",
             tenor=tenor, vwap=price, volume=100)
        for tenor, price in (("M+1", 110), ("Q+2", 120))
    ])
    return cfg, mapping, book, own


def run(setup, cfg=None, own=None, start=DAY, end=DAY, loo=False):
    default, mapping, book, data = setup
    result = CurveFiller(cfg or default, data if own is None else own,
                         [mapping], {mapping.key: book}).run(start, end, loo=loo)
    assert not result.errors
    return result


def test_adjust_reduces_quarter_discrepancy_and_audit_preserves_published_prices(setup):
    cfg, _, _, _ = setup
    baseline = run(setup, replace(cfg, shape_mode="off"))
    adjusted = run(setup)
    audit = run(setup, replace(cfg, shape_mode="audit"))
    base = baseline.filled.set_index("tenor")
    actual = adjusted.filled.set_index("tenor")
    proposed = audit.filled.set_index("tenor")
    pd.testing.assert_series_equal(actual.price_before_shape, base.price, check_names=False)
    pd.testing.assert_frame_equal(proposed[["price", "source", "data_origin"]],
                                  base[["price", "source", "data_origin"]])
    assert proposed.shape_proposed_price.tolist() == pytest.approx(actual.price.tolist())
    assert actual.loc["Q+2", "price"] == 120
    assert actual.loc["M+1", "price"] == 110
    assert actual.loc["Q+2", "data_origin"] == "original"
    assert (actual.shape_adjustment.abs() <= cfg.shape_max_abs_adjustment + 1e-8).all()
    assert actual.shape_adjustment.abs().max() > 0.1
    before = adjusted.consistency.query("shape_stage == 'before'").iloc[0].deviation
    after = adjusted.consistency.query("shape_stage == 'after'").iloc[0].deviation
    assert abs(after) < abs(before)
    assert set(audit.consistency.shape_stage) == {"before", "after", "proposed"}
    assert not any(name.startswith("shape_") for name in base.columns)


def test_allow_originals_preserves_input_and_applies_shared_delta_to_duplicate_rows(setup):
    cfg, mapping, _, own = setup
    cfg = replace(cfg, shape_adjust_originals=True, shape_original_weight=1.0)
    own = pd.concat([own.iloc[:1], own.iloc[1:].assign(vwap=118),
                     own.iloc[1:].assign(vwap=122)], ignore_index=True)
    snapshot = own.copy(deep=True)
    result = run(setup, cfg, own)
    q = result.filled.set_index("tenor").loc["Q+2"]
    assert q.shape_original_modified
    assert q.data_origin == "estimated" and q.source == "own+shape"
    assert q.estimation_method == "shape_adjusted_original"
    assert q.price_before_shape == q.own_vwap == 120
    assert pd.isna(q.confidence)
    raw = own.rename(columns={"date": "reference_date"})
    raw["trade_id"] = ["001", "002", "003"]
    enriched = enrich_input(raw, result.filled, cfg, [mapping], DAY, DAY)
    pd.testing.assert_frame_equal(own, snapshot)
    pd.testing.assert_frame_equal(enriched.loc[:2, raw.columns], raw, check_dtype=False)
    q_rows = enriched[enriched.tenor == "Q+2"]
    assert q_rows.vwap.tolist() == [118, 122]
    assert q_rows.curve_price.tolist() == pytest.approx([118 + q.shape_adjustment,
                                                        122 + q.shape_adjustment])
    assert q_rows.curve_price_before_shape.tolist() == [118, 122]
    assert q_rows.curve_own_vwap.tolist() == [118, 122]
    assert q_rows.data_origin.eq("estimated").all()
    assert all(json.loads(trace) for trace in q_rows.curve_shape_trace)


def test_observed_quarter_outside_targets_still_guides_shape_and_reaches_enrichment(setup):
    cfg, mapping, _, own = setup
    cfg = replace(cfg, tenors=["M+4", "M+5", "M+6"], shape_adjust_originals=True,
                  shape_original_weight=1.0)
    result = run(setup, cfg)
    assert set(result.filled.tenor) == {"M+1", "M+4", "M+5", "M+6", "Q+2"}
    q = result.filled.set_index("tenor").loc["Q+2"]
    assert q.shape_original_modified
    raw = own.rename(columns={"date": "reference_date"})
    output = enrich_input(raw, result.filled, cfg, [mapping], DAY, DAY)
    assert output[output.tenor == "Q+2"].iloc[0].curve_price == pytest.approx(q.price)


def test_shape_never_trains_history_and_daily_matches_refill(setup):
    cfg, _, _, own = setup
    cfg = replace(cfg, layer_hist="on", shape_adjust_originals=True,
                  shape_original_weight=1.0)
    later = DAY + timedelta(days=1)
    baseline = run(setup, replace(cfg, shape_mode="off"), end=later)
    full = run(setup, cfg, end=later)
    daily = run(setup, cfg, start=later, end=later)
    base_later = baseline.filled[baseline.filled.reference_date == later].set_index("tenor")
    shaped_later = full.filled[full.filled.reference_date == later].set_index("tenor")
    assert shaped_later.price_before_shape.tolist() == pytest.approx(base_later.price.tolist())
    pd.testing.assert_frame_equal(full.filled[full.filled.reference_date == later].reset_index(drop=True),
                                  daily.filled.reset_index(drop=True))


@pytest.mark.parametrize("adjust_originals", [False, True])
def test_loo_hides_period_from_shape_constraints_and_scores_final_pipeline(setup, adjust_originals):
    cfg, _, _, own = setup
    cfg = replace(cfg, shape_adjust_originals=adjust_originals)
    own = pd.concat([own, own.iloc[[0]].assign(tenor="M+4", vwap=132),
                    own.iloc[[1]].assign(tenor="Q + 2")], ignore_index=True)
    evaluated = run(setup, cfg, own, loo=True)
    observed = evaluated.loo.query("method == 'pipeline_configured'")
    for row in observed.itertuples():
        # LOO identifies aggregated aliases with their comma-joined labels.
        label = row.tenor.split(",")[0]
        hidden_period = resolve_tenor(label, DAY).key
        retained = own[[resolve_tenor(label, DAY).key != hidden_period for label in own.tenor]]
        replay = run(setup, replace(cfg, tenors=[*TENORS, label]), retained)
        expected = replay.filled.set_index("tenor").loc[label]
        assert row.pred == pytest.approx(expected.price)
        assert expected.data_origin_before_shape != "original"
