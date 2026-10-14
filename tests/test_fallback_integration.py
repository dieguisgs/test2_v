"""Verify the fallback policy through configuration, production CLI and audit output."""

import json
import math
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from vwaps.cli import main
from vwaps.config import load_config, validate_fallback_config
from vwaps.log import get_logger
from vwaps.mapping import COLUMNS


@pytest.fixture(autouse=True)
def restore_logging():
    logger = get_logger()
    handlers, level, propagate = logger.handlers[:], logger.level, logger.propagate
    yield
    for handler in logger.handlers:
        if handler not in handlers:
            handler.close()
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate


@pytest.fixture
def project(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[paths]\nvwap_input = "input.csv"\nmapping = "products.csv"\n'
        'eex_curves_dir = "curves"\noutput_dir = "output"\n'
        '[targets]\ntenors = ["M+1", "M+2", "Q+1"]\n'
        '[layers]\nlocal = false\nhist = "off"\narbitrage = true\n', encoding="utf-8",
    )
    days = pd.bdate_range("2026-09-01", periods=9).date
    pd.DataFrame([
        dict(reference_date=days[-1], product="P", region="DE", unit="EUR/MWh",
             tenor=tenor, vwap=value, volume=10, note="preserve")
        for tenor, value in (("M+1", ""), ("M+2", ""), ("D+9", "77"))
    ]).to_csv(tmp_path / "input.csv", index=False)
    pd.DataFrame([dict(product="P", region="DE", unit="EUR/MWh", use="fill", area="DE",
                       profile="Base", eex_file="DE.csv", hours="Base", timezone="Europe/Berlin",
                       comment="")], columns=COLUMNS).to_csv(tmp_path / "products.csv", index=False)
    (tmp_path / "curves").mkdir()
    pd.DataFrame([
        (day, kind, start, price)
        for i, day in enumerate(days)
        for kind, start, price in (("Month", "2026-10-01", 100 + i),
                                   ("Month", "2026-11-01", 102 + 2 * i),
                                   ("Quarter", "2026-10-01", 160 + i))
    ], columns=["tradeDate", "maturityType", "deliveryStart", "settlPx"]).to_csv(
        tmp_path / "curves" / "DE.csv", index=False,
    )
    return config, days


def engine(config):
    return pd.read_csv(config.parent / "output" / "filled_history.csv")


def test_cli_simple_prices_spreads_and_enriched_audit(project):
    config, days = project
    before = config.read_bytes()
    assert main(["daily", "--date", str(days[-1]), "--eex-price-method", "simple",
                 "--eex-price-window", "5", "--eex-spread-window", "9",
                 "--eex-anchor-months", "2"], config) == 0
    assert config.read_bytes() == before
    rows = engine(config).set_index("tenor")
    assert set(rows.source) == {"eex+smooth"}
    assert rows.loc["M+1", "price"] == pytest.approx(106)
    assert rows.loc["M+2", "price"] == pytest.approx(112)
    assert rows.loc["Q+1", "price"] == pytest.approx(166)
    assert rows.loc["M+1", "eex_settle"] == 108
    assert rows.loc["M+2", "eex_settle"] == 118
    assert rows.loc["M+1", "estimation_method"] == "eex_price_simple"
    assert rows.loc["M+2", "estimation_method"] == "eex_month_cascade_simple"
    assert rows.basis.isna().all() and rows.local_weight.isna().all()
    trace = json.loads(rows.loc["M+2", "eex_fallback_trace"])
    assert trace["price_window"] == 5 and trace["spread_window"] == 9
    assert len(trace["price_average"]["observations"]) == 5
    assert trace["spread_steps"][0]["mean_spread"] == pytest.approx(6)
    assert len(trace["spread_steps"][0]["observations"]) == 9
    assert all(observation["weight"] == pytest.approx(0.2)
               for observation in trace["price_average"]["observations"])
    enriched = pd.read_csv(config.parent / "output" / "enriched_history.csv", keep_default_na=False)
    own = enriched[enriched.tenor == "D+9"].iloc[0]
    assert own.data_origin == "original" and float(own.curve_price) == 77
    missing_original = enriched[enriched.tenor == "M+1"].iloc[0]
    assert missing_original.vwap == "" and missing_original.data_origin == "estimated"
    assert json.loads(missing_original.curve_eex_fallback_trace)["price_method"] == "simple"


def test_cli_ewma_half_life_changes_only_price_average(project):
    config, days = project
    assert main(["daily", "--date", str(days[-1]), "--eex-ewma-halflife", "1"], config) == 0
    rows = engine(config).set_index("tenor")
    # Oldest to newest weights are 1, 2, 4, 8, 16, normalized by 31.
    expected = (104 + 105 * 2 + 106 * 4 + 107 * 8 + 108 * 16) / 31
    assert rows.loc["M+1", "price"] == pytest.approx(expected)
    assert rows.loc["M+2", "price"] == pytest.approx(expected + 6)
    assert rows.loc["M+1", "estimation_method"] == "eex_price_ewma"
    assert rows.loc["M+2", "estimation_method"] == "eex_month_cascade_ewma"
    trace = json.loads(rows.loc["M+2", "eex_fallback_trace"])
    assert trace["ewma_halflife"] == 1
    assert [row["weight"] for row in trace["price_average"]["observations"]] == pytest.approx(
        [1 / 31, 2 / 31, 4 / 31, 8 / 31, 16 / 31])
    assert all(row["weight"] == pytest.approx(1 / 9)
               for row in trace["spread_steps"][0]["observations"])


def test_insufficient_window_never_copies_raw_eex(project):
    config, days = project
    assert main(["daily", "--date", str(days[0])], config) == 0
    rows = engine(config)
    assert set(rows.source) == {"missing"}
    assert rows.price.isna().all()
    assert rows.eex_settle.notna().all()  # Reference remains visible for audit only.
    assert rows.flag.str.contains("eex_fallback_unavailable").all()


def test_missing_fallback_can_reconstruct_from_original_contracts(project):
    config, days = project
    text = config.read_text(encoding="utf-8").replace(
        'tenors = ["M+1", "M+2", "Q+1"]', 'tenors = ["Q+1"]')
    config.write_text(text, encoding="utf-8")
    pd.DataFrame([dict(reference_date=days[0], product="P", region="DE", unit="EUR/MWh",
                       tenor=tenor, vwap=120, volume=10)
                  for tenor in ("M+1", "M+2", "M+3")]).to_csv(config.parent / "input.csv", index=False)
    assert main(["daily", "--date", str(days[0])], config) == 0
    row = engine(config).iloc[0]
    assert row.price == pytest.approx(120)
    assert row.source == "arbitrage" and row.estimation_method == "contract_strip"
    assert row.eex_settle == 160 and row.eex_method == "exact"
    assert "eex_fallback_unavailable" in row.flag


def test_daily_refill_and_catchup_keep_same_transformation(project):
    config, days = project
    assert main(["refill", "--from", str(days[0]), "--to", str(days[-1])], config) == 0
    full = engine(config)
    last = full[full.reference_date == str(days[-1])].reset_index(drop=True)
    assert main(["daily", "--date", str(days[-1])], config) == 0
    repeated = engine(config)
    pd.testing.assert_frame_equal(last, repeated[repeated.reference_date == str(days[-1])].reset_index(drop=True))
    # Remove one group from both histories to exercise actual catchup calculation.
    for name, field in (("filled_history.csv", "reference_date"),
                        ("enriched_history.csv", "curve_reference_date")):
        path = config.parent / "output" / name
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        frame[frame[field] != str(days[-1])].to_csv(path, index=False)
    assert main(["catchup", "--from", str(days[0]), "--to", str(days[-1])], config) == 0
    caught = engine(config)
    pd.testing.assert_frame_equal(last, caught[caught.reference_date == str(days[-1])].reset_index(drop=True))
    assert "eex" not in set(caught.source)


@pytest.mark.parametrize("field,value", [
    ("fallback_price_method", "raw"), ("fallback_price_window", 1),
    ("fallback_price_window", 2.5), ("fallback_spread_window", True),
    ("fallback_spread_window", 0), ("fallback_anchor_months", 0),
    ("fallback_ewma_halflife", 0), ("fallback_ewma_halflife", math.nan),
    ("fallback_ewma_halflife", math.inf), ("fallback_ewma_halflife", "2"),
])
def test_invalid_config_rejected(project, field, value):
    config, _ = project
    with pytest.raises(ValueError, match="eex_fallback"):
        validate_fallback_config(replace(load_config(config), **{field: value}))


def test_toml_controls_and_invalid_cli_override(project):
    config, days = project
    with config.open("a", encoding="utf-8") as handle:
        handle.write('\n[eex_fallback]\nprice_method="simple"\nprice_window=3\n'
                     'spread_window=4\newma_halflife=1.5\nanchor_months=3\n')
    cfg = load_config(config)
    assert (cfg.fallback_price_method, cfg.fallback_price_window, cfg.fallback_spread_window,
            cfg.fallback_ewma_halflife, cfg.fallback_anchor_months) == ("simple", 3, 4, 1.5, 3)
    assert main(["daily", "--date", str(days[-1]), "--eex-price-window", "1"], config) == 1
    assert not (config.parent / "output" / "filled_history.csv").exists()
