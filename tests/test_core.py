from datetime import date

import pytest

from vwaps.basis import apply_basis, to_basis
from vwaps.hours import hours_fn, period_hours
from vwaps.pricer import Pricer
from vwaps.tenors import period_from_eex, resolve_tenor

TUE = date(2026, 9, 29)
TZ = "Europe/Berlin"


@pytest.mark.parametrize("label,kind,start,end", [
    ("D+1", "Day", date(2026, 9, 30), date(2026, 10, 1)),
    ("WE", "Weekend", date(2026, 10, 3), date(2026, 10, 5)),
    ("WE+3", "Weekend", date(2026, 10, 24), date(2026, 10, 26)),
    ("BOW", "BOW", date(2026, 9, 30), date(2026, 10, 5)),
    ("W+1", "Week", date(2026, 10, 5), date(2026, 10, 12)),
    ("BOM", "BOM", date(2026, 9, 30), date(2026, 10, 1)),
    ("M+1", "Month", date(2026, 10, 1), date(2026, 11, 1)),
    ("Q+1", "Quarter", date(2026, 10, 1), date(2027, 1, 1)),
    ("Win+1", "Season", date(2026, 10, 1), date(2027, 4, 1)),
    ("Sum+1", "Season", date(2027, 4, 1), date(2027, 10, 1)),
    ("Cal+1", "Year", date(2027, 1, 1), date(2028, 1, 1)),
])
def test_resolve(label, kind, start, end):
    p = resolve_tenor(label, TUE)
    assert (p.kind, p.start, p.end) == (kind, start, end)


def test_seasons_in_january():
    # 03/01/2025 (screenshot): Sum+1 = Sum-25, Win+1 = Win-25/26.
    d = date(2025, 1, 3)
    assert resolve_tenor("Sum+1", d).start == date(2025, 4, 1)
    assert resolve_tenor("Win+1", d).start == date(2025, 10, 1)


def test_business_days_and_empty_tails():
    fri = date(2026, 10, 2)
    assert resolve_tenor("D+1", fri, "business").start == date(2026, 10, 5)
    assert resolve_tenor("BOM", date(2026, 9, 30)) is None
    assert resolve_tenor("BOW", date(2026, 10, 4)) is None


def test_eex_period_matches_own():
    assert period_from_eex("Weekend", date(2026, 10, 3)).key == resolve_tenor("WE", TUE).key
    assert period_from_eex("Quarter", date(2026, 10, 1)).key == resolve_tenor("Q+1", TUE).key


def test_hours_dst():
    assert period_hours(date(2026, 10, 25), date(2026, 10, 26), "Base", TZ) == 25
    assert period_hours(date(2026, 3, 29), date(2026, 3, 30), "Base", TZ) == 23
    assert period_hours(date(2026, 10, 5), date(2026, 10, 12), "Peak", TZ) == 60


def test_strip_quarter_from_months():
    # 2026-09-29 DE: missing EEX Q4-26 comes from Oct/Nov/Dec, weighted by hours.
    h = hours_fn("Base", TZ)
    px = {(date(2026, 10, 1), date(2026, 11, 1)): 157.70,
          (date(2026, 11, 1), date(2026, 12, 1)): 163.61,
          (date(2026, 12, 1), date(2027, 1, 1)): 159.66}
    p, how = Pricer(px, h).price(date(2026, 10, 1), date(2027, 1, 1))
    exp = sum(v * h(*k) for k, v in px.items()) / h(date(2026, 10, 1), date(2027, 1, 1))
    assert how == "strip" and p == pytest.approx(exp)


def test_residual_bom():
    h = hours_fn("Base", TZ)
    m = (date(2026, 9, 1), date(2026, 10, 1))
    px = {m: 150.0}
    for d in range(1, 21):
        px[(date(2026, 9, d), date(2026, 9, d + 1))] = 140.0
    p, how = Pricer(px, h).price(date(2026, 9, 21), date(2026, 10, 1))
    assert how == "residual"
    # 20 days at 140 + 10 days at p = 30 days at 150  ->  p = 170.
    assert p == pytest.approx(170.0)


def test_ratio_equals_anchor_times_eex_ratio():
    own_m1, ex_m1, ex_m2 = 158.5, 157.7, 163.61
    b = to_basis(own_m1, ex_m1, "ratio")
    assert apply_basis(ex_m2, b, "ratio") == pytest.approx(own_m1 * ex_m2 / ex_m1)
    b = to_basis(own_m1, ex_m1, "additive")
    assert apply_basis(ex_m2, b, "additive") == pytest.approx(own_m1 + ex_m2 - ex_m1)


def test_ewcov_beta_and_corr():
    from vwaps.comove import EWCov
    c = EWCov(20)
    for x in (0.01, -0.02, 0.015, -0.005, 0.02):
        c.update(x, 0.8 * x)  # y changes by 0.8 for each unit change in x.
    assert c.beta == pytest.approx(0.8)
    assert c.corr == pytest.approx(1.0)


def test_mapping_guess(tmp_path):
    from vwaps.config import load_config
    from vwaps.mapping import guess_row
    (tmp_path / "curves" / "ES").mkdir(parents=True)
    for f in ("Base", "PeakMo-Su"):
        (tmp_path / "curves" / "ES" / f"{f}.csv").write_text("tradeDate\n")
    (tmp_path / "config.toml").write_text('[paths]\neex_curves_dir = "curves"\n')
    cfg = load_config(tmp_path / "config.toml")
    assert guess_row("ES_Base load", cfg)["eex_file"] == "ES/Base.csv"
    es_peak = guess_row("ES_Peak load", cfg)
    assert es_peak["eex_file"] == "ES/PeakMo-Su.csv" and es_peak["hours"] == "Peak7"
    assert guess_row("GB_Other_Block_1_2", cfg)["use"] == "off"
    assert guess_row("BE_Peak load", cfg)["eex_file"] == ""
