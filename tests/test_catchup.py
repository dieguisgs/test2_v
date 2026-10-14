import json
from dataclasses import asdict
from datetime import date, timedelta

import pandas as pd
import pytest

from vwaps import cli
from vwaps.io_eex import REQUIRED
from vwaps.mapping import COLUMNS, ProductMap
from vwaps.tenors import resolve_tenor


TODAY = date.today()
DAY = TODAY - timedelta(days=TODAY.weekday() + 7)


def project(root, regions=("North",), days=(DAY,), own_days=None, targets=("M+1", "M+2"), quotes=True):
    root.mkdir(parents=True, exist_ok=True)
    own_days = days if own_days is None else own_days
    config = ('[paths]\nvwap_input = "own.csv"\nmapping = "mapping.csv"\n'
              'eex_curves_dir = "curves"\noutput_dir = "output"\n'
              '[targets]\ntenors = ' + json.dumps(list(targets)) + '\n'
              '[layers]\nhist = "on"\ncross = false\n'
              '[vwap_columns]\ntenor = "tenor2"\nvolume = "total_volume"\n')
    (root / "config.toml").write_text(config, encoding="utf-8")
    rows = [dict(reference_date=day.isoformat(), product="SHARED", region=region, unit="EUR/MWh",
                 tenor2="M+1", vwap=110.0 + 10 * ri + 5 * di, total_volume=20.0, country="DE")
            for ri, region in enumerate(regions) for di, day in enumerate(own_days)]
    pd.DataFrame(rows, columns=["reference_date", "product", "region", "unit", "tenor2",
                                "vwap", "total_volume", "country"]).to_csv(root / "own.csv", index=False)
    maps = [ProductMap("SHARED", "fill", "DE", "Base", "DE/Base.csv", "Base", "Europe/Berlin",
                       region=region, unit="EUR/MWh") for region in regions]
    pd.DataFrame([asdict(m) for m in maps], columns=COLUMNS).to_csv(root / "mapping.csv", index=False)
    if quotes:
        frame = []
        for day in days:
            for i in range(1, 4):
                period = resolve_tenor(f"M+{i}", day)
                frame.append((day, period.kind, period.start, 90.0 + i * 10))
        path = root / "curves" / "DE" / "Base.csv"
        path.parent.mkdir(parents=True)
        pd.DataFrame(frame, columns=REQUIRED).to_csv(path, index=False)
    return root


def invoke(root, *args):
    return cli.main(list(args), root / "config.toml")


def catchup(root, start=DAY, end=DAY):
    return invoke(root, "catchup", "--from", start.isoformat(), "--to", end.isoformat())


def history(root, name="filled_history.csv"):
    return pd.read_csv(root / "output" / name, keep_default_na=False)


def test_empty_histories_are_filled_and_complete_rerun_is_a_noop(tmp_path, monkeypatch):
    root = project(tmp_path)
    assert catchup(root) == 0
    filled, enriched = history(root), history(root, "enriched_history.csv")
    assert set(filled.tenor) == {"M+1", "M+2"}
    assert set(enriched.curve_tenor) == {"M+1", "M+2"}
    paths = [path for path in (root / "output").rglob("*.csv")]
    snapshot = {path: path.read_bytes() for path in paths}

    def no_engine_run(*args, **kwargs):
        raise AssertionError("A complete catchup must not rerun the engine")

    monkeypatch.setattr(cli.CurveFiller, "run", no_engine_run)
    assert catchup(root) == 0
    assert {path: path.read_bytes() for path in paths} == snapshot


def test_partial_identity_updates_only_pending_curve_even_if_complete_input_changes(tmp_path):
    root = project(tmp_path, regions=("North", "South"))
    assert catchup(root) == 0
    enriched_path = root / "output" / "enriched_history.csv"
    enriched = history(root, "enriched_history.csv")
    enriched.loc[~((enriched.curve_region == "South") & (enriched.curve_tenor == "M+2"))].to_csv(
        enriched_path, index=False)
    raw = pd.read_csv(root / "own.csv")
    raw.loc[raw.region == "North", "vwap"] = 999.0
    raw.to_csv(root / "own.csv", index=False)
    assert catchup(root) == 0
    filled, enriched = history(root), history(root, "enriched_history.csv")
    assert filled.loc[(filled.region == "North") & (filled.tenor == "M+1"), "price"].iloc[0] == 110.0
    north = enriched[(enriched.curve_region == "North") & (enriched.curve_tenor == "M+1")]
    assert north.iloc[0].vwap == 110.0
    assert len(enriched[enriched.curve_region == "South"]) == 2
    assert len(filled) == 4


def test_processed_missing_price_is_not_pending(tmp_path, monkeypatch):
    root = project(tmp_path, quotes=False)
    assert catchup(root) == 0
    assert history(root).set_index("tenor").loc["M+2", "source"] == "missing"
    monkeypatch.setattr(cli.CurveFiller, "run", lambda *args, **kwargs: pytest.fail("missing is already processed"))
    assert catchup(root) == 0


def test_adding_a_target_triggers_only_the_current_date_curve_group(tmp_path):
    root = project(tmp_path)
    assert catchup(root) == 0
    path = root / "config.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('["M+1", "M+2"]', '["M+1", "M+2", "M+3"]'),
                    encoding="utf-8")
    assert catchup(root) == 0
    assert set(history(root).tenor) == {"M+1", "M+2", "M+3"}
    assert set(history(root, "enriched_history.csv").curve_tenor) == {"M+1", "M+2", "M+3"}


def test_catchup_replays_original_history_and_matches_refill_and_daily(tmp_path):
    days = (DAY, DAY + timedelta(days=1), DAY + timedelta(days=2))
    roots = {mode: project(tmp_path / mode, days=days, own_days=days[:-1])
             for mode in ("catchup", "refill", "daily")}
    root = roots["catchup"]
    assert catchup(root, days[0], days[1]) == 0
    assert catchup(root, days[0], days[-1]) == 0
    assert invoke(roots["refill"], "refill", "--from", days[0].isoformat(), "--to", days[-1].isoformat()) == 0
    assert invoke(roots["daily"], "daily", "--date", days[-1].isoformat()) == 0
    expected = pd.read_csv(roots["refill"] / "output" / "filled_history.csv", dtype=str, keep_default_na=False)
    actual = pd.read_csv(root / "output" / "filled_history.csv", dtype=str, keep_default_na=False)
    daily = pd.read_csv(roots["daily"] / "output" / "filled_history.csv", dtype=str, keep_default_na=False)
    pd.testing.assert_frame_equal(actual, expected)
    expected_last = expected[expected.reference_date == days[-1].isoformat()].reset_index(drop=True)
    pd.testing.assert_frame_equal(daily, expected_last)
    assert set(expected_last.source) == {"eex+hist"}


def test_observed_weekend_with_invalid_vwap_is_forced_into_the_schedule(tmp_path):
    saturday = DAY + timedelta(days=5)
    root = project(tmp_path, days=(DAY,), own_days=(saturday,))
    raw = pd.read_csv(root / "own.csv")
    raw["vwap"] = "invalid"
    raw.to_csv(root / "own.csv", index=False)
    assert catchup(root, saturday, saturday) == 0
    assert set(history(root).reference_date) == {saturday.isoformat()}
    enriched = history(root, "enriched_history.csv")
    assert enriched.loc[enriched.curve_row_type == "original_invalid", "vwap"].iloc[0] == "invalid"


def test_default_range_ends_today_and_daily_keeps_its_today_default(tmp_path, monkeypatch):
    final = DAY + timedelta(days=2)

    class FixedDate(date):
        @classmethod
        def today(cls):
            return final

    monkeypatch.setattr(cli, "date", FixedDate)
    root = project(tmp_path / "catchup", days=(DAY,), own_days=(DAY,))
    assert invoke(root, "catchup") == 0
    assert set(history(root).reference_date) == {(DAY + timedelta(days=i)).isoformat() for i in range(3)}
    daily = project(tmp_path / "daily", days=(final,))
    assert invoke(daily, "daily") == 0
    assert set(history(daily).reference_date) == {final.isoformat()}


def test_legacy_histories_are_rejected_before_engine_or_result_writes(tmp_path, monkeypatch):
    root = project(tmp_path)
    path = root / "output" / "filled_history.csv"
    path.parent.mkdir()
    path.write_text("reference_date,product,tenor\n2020-01-01,SHARED,M+1\n", encoding="utf-8")
    snapshot = path.read_bytes()
    monkeypatch.setattr(cli.CurveFiller, "run", lambda *args, **kwargs: pytest.fail("legacy history must fail first"))
    assert catchup(root) == 1
    assert path.read_bytes() == snapshot
    assert not (root / "output" / "enriched_history.csv").exists()


def test_future_and_inverted_ranges_are_rejected(tmp_path):
    root = project(tmp_path)
    assert catchup(root, DAY, TODAY + timedelta(days=1)) == 1
    assert catchup(root, DAY + timedelta(days=1), DAY) == 1
    assert not (root / "output" / "filled_history.csv").exists()


def test_no_active_curves_is_a_noop_and_empty_dates_require_from(tmp_path):
    root = project(tmp_path / "off")
    maps = pd.read_csv(root / "mapping.csv", keep_default_na=False)
    maps["use"] = "off"
    maps.to_csv(root / "mapping.csv", index=False)
    assert catchup(root) == 0
    assert not (root / "output" / "filled_history.csv").exists()
    empty = project(tmp_path / "empty", days=(), own_days=(), quotes=False)
    assert invoke(empty, "catchup", "--to", DAY.isoformat()) == 1
    assert catchup(empty) == 0
    assert set(history(empty).source) == {"missing"}


def test_nonfinite_computed_price_aborts_catchup_before_publishing(tmp_path):
    root = project(tmp_path, targets=("Q+1",))
    path = root / "curves" / "DE" / "Base.csv"
    quotes = pd.read_csv(path)
    quotes["settlPx"] = 1e308  # Finite inputs overflow the weighted strip arithmetic.
    quotes.to_csv(path, index=False)
    assert catchup(root) == 1
    assert not (root / "output" / "filled_history.csv").exists()
    assert not (root / "output" / "enriched_history.csv").exists()
