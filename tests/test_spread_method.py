"""Spread methodology: own anchors + smoothed EEX spreads (blue), smoothed
EEX prices without anchors (orange), causal windows under eex.offset_days."""

import json
import math
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest

from vwaps.config import load_config, validate_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.product_config import effective_config, parse_parameter_cells
from vwaps.spread import log_ttm
from vwaps.tenors import resolve_tenor
from vwaps.tuning import tune_parameters

DAY = date(2026, 9, 30)  # Wednesday
PUBLICATIONS = [date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29), DAY]
OCT, NOV, DEC, JAN = date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)


def configured(tmp_path, extra=""):
    path = tmp_path / "config.toml"
    path.write_text('[targets]\ntenors = ["M+1", "M+2", "M+3"]\n' + extra, encoding="utf-8")
    return load_config(path)


def mapping():
    return ProductMap("P", "fill", "DE", "Base", "fixture.csv", "Base", "Europe/Berlin",
                      region="North", unit="EUR/MWh")


def prices(i):
    """Oct, Nov, Dec settlements on the i-th publication (spreads widen daily)."""
    return {OCT: 100.0 + i, NOV: 110.0 + 2 * i, DEC: 120.0 + 3 * i}


def book(publications=PUBLICATIONS):
    rows = [(day, "Month", start, price)
            for i, day in enumerate(publications) for start, price in prices(i).items()]
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def own(observations, day=DAY):
    return pd.DataFrame([dict(date=day, product="P", region="North", unit="EUR/MWh",
                              tenor=tenor, vwap=price, volume=10.0)
                         for tenor, price in observations])


def ewma(values, halflife=2.0):
    n = len(values)
    weights = [2.0 ** (-(n - 1 - i) / halflife) for i in range(n)]
    return sum(w * v for w, v in zip(weights, values)) / sum(weights)


def run(cfg, observations, eex=None, start=DAY, end=DAY, **kw):
    curve = mapping()
    result = CurveFiller(cfg, observations, [curve], {curve.key: eex or book()}).run(start, end, **kw)
    assert not result.errors
    return result


def at(result, day=DAY):
    return result.filled[result.filled["reference_date"] == day].set_index("tenor")


def test_ewma_weights_favour_recent_publications():
    raw = [2.0 ** (-(4 - i) / 2.0) for i in range(5)]
    weights = [w / sum(raw) for w in raw]
    assert [round(w, 3) for w in weights] == [0.089, 0.126, 0.178, 0.252, 0.356]


def test_blue_is_own_anchor_plus_smoothed_spread_between_absolute_contracts(tmp_path):
    out = at(run(configured(tmp_path), own([("M+1", 105.0)])))
    assert out.loc["M+1", "source"] == "own" and out.loc["M+1", "price"] == 105.0
    nov_oct = [prices(i)[NOV] - prices(i)[OCT] for i in range(5)]  # 10, 11, 12, 13, 14
    dec_oct = [prices(i)[DEC] - prices(i)[OCT] for i in range(5)]
    m2, m3 = out.loc["M+2"], out.loc["M+3"]
    assert m2["source"] == "eex_spread" and m2["estimation_method"] == "spread_nearest"
    assert m2["anchors"] == "M+1" and m2["anchor_price"] == 105.0
    assert m2["price"] == pytest.approx(105.0 + ewma(nov_oct))
    assert m3["price"] == pytest.approx(105.0 + ewma(dec_oct))
    assert m2["window_obs"] == 5
    trace = json.loads(m2["spread_trace"])
    assert [o["value"] for o in trace["parts"][0]["observations"]] == pytest.approx(nov_oct)


def test_blue_equals_smoothed_eex_plus_local_level(tmp_path):
    """own + EWMA(EEX_t - EEX_a) == EWMA(EEX_t) + (own - EWMA(EEX_a))."""
    out = at(run(configured(tmp_path), own([("M+1", 105.0)])))
    smoothed_nov = ewma([prices(i)[NOV] for i in range(5)])
    smoothed_oct = ewma([prices(i)[OCT] for i in range(5)])
    assert out.loc["M+2", "price"] == pytest.approx(smoothed_nov + (105.0 - smoothed_oct))


@pytest.mark.parametrize("no_anchor,expected", [
    ("eex_ewma", ewma([prices(i)[NOV] for i in range(5)])),
    ("eex_level", prices(4)[NOV]),
])
def test_orange_without_own_anchor(tmp_path, no_anchor, expected):
    cfg = configured(tmp_path, f'[spread]\nno_anchor = "{no_anchor}"\n')
    out = at(run(cfg, own([])))
    assert out.loc["M+2", "source"] == no_anchor
    assert out.loc["M+2", "price"] == pytest.approx(expected)
    assert out.loc["M+2", "confidence"] == pytest.approx(0.4)


def test_no_anchor_missing_leaves_gaps(tmp_path):
    cfg = configured(tmp_path, '[spread]\nno_anchor = "missing"\n')
    out = at(run(cfg, own([])))
    assert out["source"].eq("missing").all()


def test_simple_weighting_is_the_plain_mean(tmp_path):
    cfg = configured(tmp_path, '[spread]\nweighting = "simple"\n')
    out = at(run(cfg, own([("M+1", 105.0)])))
    assert out.loc["M+2", "price"] == pytest.approx(105.0 + 12.0)  # mean of 10..14


def test_offset_minus_one_excludes_the_reference_day_publication(tmp_path):
    cfg = configured(tmp_path, "[eex]\noffset_days = -1\n")
    out = at(run(cfg, own([("M+1", 105.0)])))
    # Only the four publications up to 29-Sep are allowed.
    assert str(out.loc["M+2", "eex_asof"]) == "2026-09-29"
    assert out.loc["M+2", "window_obs"] == 4
    nov_oct = [prices(i)[NOV] - prices(i)[OCT] for i in range(4)]
    assert out.loc["M+2", "price"] == pytest.approx(105.0 + ewma(nov_oct))


def test_publication_after_cutoff_cannot_change_the_result(tmp_path):
    cfg = configured(tmp_path, "[eex]\noffset_days = -1\n")
    with_today = at(run(cfg, own([("M+1", 105.0)])))
    without_today = at(run(cfg, own([("M+1", 105.0)]), eex=book(PUBLICATIONS[:-1])))
    assert with_today.loc["M+2", "price"] == pytest.approx(without_today.loc["M+2", "price"])


def test_min_obs_requires_enough_publications(tmp_path):
    cfg = configured(tmp_path, "[eex]\noffset_days = -1\n[spread]\nmin_obs = 5\n")
    out = at(run(cfg, own([("M+1", 105.0)])))
    assert out.loc["M+2", "source"] == "missing"  # 4 allowed publications < 5


def test_interpolate_weights_neighbours_by_log_distance(tmp_path):
    cfg = configured(tmp_path, '[spread]\nanchor_rule = "interpolate"\n')
    out = at(run(cfg, own([("M+1", 105.0), ("M+3", 130.0)])))
    oct_, nov, dec = (resolve_tenor(t, DAY) for t in ("M+1", "M+2", "M+3"))
    dl = abs(log_ttm(nov, DAY) - log_ttm(oct_, DAY))
    dr = abs(log_ttm(nov, DAY) - log_ttm(dec, DAY))
    from_left = 105.0 + ewma([prices(i)[NOV] - prices(i)[OCT] for i in range(5)])
    from_right = 130.0 + ewma([prices(i)[NOV] - prices(i)[DEC] for i in range(5)])
    wl = dr / (dl + dr)
    assert out.loc["M+2", "estimation_method"] == "spread_interpolate"
    assert out.loc["M+2", "price"] == pytest.approx(wl * from_left + (1 - wl) * from_right)


def test_nearest_anchor_wins_and_ties_are_deterministic(tmp_path):
    out = at(run(configured(tmp_path), own([("M+1", 105.0), ("M+3", 130.0)])))
    oct_, nov, dec = (resolve_tenor(t, DAY) for t in ("M+1", "M+2", "M+3"))
    nearer = "M+1" if abs(log_ttm(nov, DAY) - log_ttm(oct_, DAY)) <= abs(
        log_ttm(nov, DAY) - log_ttm(dec, DAY)) else "M+3"
    assert out.loc["M+2", "anchors"] == nearer


def test_daily_equals_the_same_day_inside_a_refill(tmp_path):
    cfg = configured(tmp_path)
    observations = pd.concat([own([("M+1", 104.0)], d) for d in PUBLICATIONS], ignore_index=True)
    single = at(run(cfg, observations))
    long = at(run(cfg, observations, start=PUBLICATIONS[0]))
    pd.testing.assert_frame_equal(single, long)


def test_loo_reports_every_variant_and_the_deployed_pipeline(tmp_path):
    result = run(configured(tmp_path), own([("M+1", 105.0), ("M+2", 118.0)]), loo=True)
    loo = result.loo
    hidden = loo[loo["tenor"] == "M+2"].set_index("method")
    assert set(hidden.index) == {"eex", "eex_ewma", "eex_level", "spread_nearest",
                                 "spread_interpolate", "pipeline_configured"}
    expected = 105.0 + ewma([prices(i)[NOV] - prices(i)[OCT] for i in range(5)])
    assert hidden.loc["pipeline_configured", "pred"] == pytest.approx(expected)
    assert hidden.loc["eex", "pred"] == prices(4)[NOV]
    assert hidden.loc["pipeline_configured", "error"] == pytest.approx(expected - 118.0)


def test_spread_configuration_is_validated(tmp_path):
    cfg = configured(tmp_path, '[spread]\nwindow = 3\nhalflife = 1.5\nanchor_scope = "any"\n')
    assert (cfg.spread_window, cfg.spread_halflife, cfg.anchor_scope) == (3, 1.5, "any")
    for bad in (dict(spread_window=0), dict(spread_window=2.5), dict(spread_halflife=0.0),
                dict(spread_halflife=float("nan")), dict(anchor_rule="mean"),
                dict(anchor_scope="all"), dict(no_anchor="eex"), dict(spread_weighting="linear"),
                dict(spread_min_obs=6)):
        with pytest.raises(ValueError, match="spread"):
            validate_config(replace(cfg, **bad))


def test_spread_parameters_work_per_product_in_individual_mode(tmp_path):
    cfg = replace(configured(tmp_path, '[run]\nconfiguration_mode = "individual"\n'))
    cells = parse_parameter_cells({"spread_window": "3", "anchor_rule": " Interpolate ",
                                   "spread_halflife": "", "no_anchor": "eex_level"})
    assert cells == {"spread_window": 3, "anchor_rule": "interpolate", "no_anchor": "eex_level"}
    curve = replace(mapping(), parameter_overrides=cells)
    resolved = effective_config(cfg, curve)
    assert (resolved.spread_window, resolved.anchor_rule, resolved.no_anchor) == (3, "interpolate", "eex_level")
    assert resolved.spread_halflife == cfg.spread_halflife
    with pytest.raises(ValueError):
        parse_parameter_cells({"spread_window": "3.5"})


def test_cli_tune_exports_spread_selection_without_changing_config(tmp_path):
    from vwaps import cli
    days = [DAY + timedelta(days=n) for n in range(-14, 1) if (DAY + timedelta(days=n)).weekday() < 5]
    config = tmp_path / "config.toml"
    config.write_text(
        '[paths]\nvwap_input="own.csv"\nmapping="mapping.csv"\neex_curves_dir="."\noutput_dir="output"\n'
        '[targets]\ntenors=["M+1", "M+2"]\n'
        '[vwap_columns]\ntenor="tenor"\nvolume="volume"\n', encoding="utf-8")
    before = config.read_text(encoding="utf-8")
    pd.DataFrame([dict(product="P", region="North", unit="EUR/MWh", use="fill", area="DE",
                       profile="Base", eex_file="eex.csv", hours="Base", timezone="Europe/Berlin",
                       comment="")]).to_csv(tmp_path / "mapping.csv", index=False)
    pd.DataFrame([dict(reference_date=str(day), product="P", region="North", unit="EUR/MWh",
                       tenor=tenor, vwap=price + 0.2 * i, volume=10.0)
                  for i, day in enumerate(days) for tenor, price in (("M+1", 101.0), ("M+2", 112.0))]
                 ).to_csv(tmp_path / "own.csv", index=False)
    pd.DataFrame([dict(tradeDate=str(day), maturityType="Month", deliveryStart=str(start),
                       settlPx=price + i * 0.3)
                  for i, day in enumerate(days) for start, price in ((OCT, 100.0), (NOV, 111.0), (DEC, 122.0))]
                 ).to_csv(tmp_path / "eex.csv", index=False)
    assert cli.main(["tune", "--anchor-rules", "nearest,interpolate", "--validation-days", "2"], config) == 0
    selected = json.loads((tmp_path / "output" / "tuning_selected.json").read_text(encoding="utf-8"))
    spread = selected["selected_parameters"]["spread"]
    assert spread["anchor_rule"] in ("nearest", "interpolate") and spread["window"] == 5
    assert config.read_text(encoding="utf-8") == before


def test_real_engine_tuning_over_spread_parameters(tmp_path):
    cfg = configured(tmp_path)
    days = [DAY + timedelta(days=n) for n in range(-21, 1) if (DAY + timedelta(days=n)).weekday() < 5]
    rows = []
    for i, day in enumerate(days):
        for start, price in {OCT: 100.0 + math.sin(i), NOV: 112.0 + 1.5 * math.cos(i),
                             DEC: 125.0 + 0.5 * i % 3}.items():
            rows.append((day, "Month", start, price))
    eex = EexBook(pd.DataFrame(rows, columns=REQUIRED))
    observations = pd.concat([own([("M+1", 101.0 + 0.1 * i), ("M+2", 113.0 - 0.1 * i)], day)
                              for i, day in enumerate(days)], ignore_index=True)
    curve = mapping()
    result = tune_parameters(cfg, observations, [curve], {curve.key: eex}, days[0], days[-1],
                             {"anchor_rule": ["nearest", "interpolate"], "spread_window": [2, 5]}, 4, 10)
    assert result.selected_config["anchor_rule"] in ("nearest", "interpolate")
    assert result.selected_config["spread_window"] in (2, 5)
    assert not result.validation_report.empty
