"""Exercise the command entry point with real input, mapping and EEX files."""

import pandas as pd
import pytest

from vwaps.cli import main
from vwaps.fill import CurveFiller
from vwaps.log import get_logger
from vwaps.mapping import COLUMNS


DAYS = ("2026-09-29", "2026-09-30")


def read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def command(config, verb="refill", day=DAYS[-1]):
    arguments = [verb]
    if verb == "daily":
        arguments += ["--date", day]
    else:
        arguments += ["--from", DAYS[0], "--to", DAYS[-1]]
    return main(arguments, config)


@pytest.fixture(autouse=True)
def close_command_logs():
    # Each command installs its own file handler. Restore the test process's
    # logger and close those files so Windows can remove temporary directories.
    logger = get_logger()
    original = logger.handlers[:]
    level, propagate = logger.level, logger.propagate
    yield
    for handler in logger.handlers:
        if handler not in original:
            handler.close()
    logger.handlers[:] = original
    logger.setLevel(level)
    logger.propagate = propagate


@pytest.fixture
def project(tmp_path):
    def create(name, only_unmapped=False):
        root = tmp_path / name
        root.mkdir()
        config = root / "config.toml"
        config.write_text(
            '[paths]\n'
            'vwap_input = "input.csv"\n'
            'mapping = "products.csv"\n'
            'eex_curves_dir = "curves"\n'
            'output_dir = "output"\n'
            '[targets]\n'
            'tenors = ["M+1", "M+2", "M+3"]\n'
            '[layers]\n'
            'local = true\n'
            'hist = "on"\n'
            'correlation = false\n'
            'cross = false\n'
            'arbitrage = false\n'
            '[vwap_columns]\n'
            'tenor = "tenor2"\n'
            'volume = "total_volume"\n',
            encoding="utf-8",
        )
        rows = [
            ("29/09/2026", "DE_Base load", "M+1", "100,00", "10", "001", "NA"),
            ("29/09/2026", "DE_Base load", "M+1", "110,00", "10", "002", "duplicate"),
            ("29/09/2026", "DE_Base load", "M+2", "", "", "003", "empty VWAP"),
            ("29/09/2026", "DE_Base load", "D+4", "80", "2", "004", "outside targets"),
            ("29/09/2026", "UNMAPPED", "unrecognised", "-5", "1", "005", "original unknown"),
            ("29/09/2026", "GB_Off", "M+1", "0", "1", "006", "original off"),
            ("30/09/2026", "DE_Base load", "M+1", "106", "10", "007", "next day"),
        ]
        if only_unmapped:
            rows = [row for row in rows if row[1] == "UNMAPPED"]
        raw = pd.DataFrame(rows, columns=[
            "reference_date", "product", "tenor2", "vwap", "total_volume", "trade_id", "note",
        ])
        raw["unit"] = "EUR/MWh"
        raw["region"] = "original region"
        raw.to_csv(root / "input.csv", index=False, encoding="utf-8-sig")
        maps = [] if only_unmapped else [
            dict(product="DE_Base load", use="fill", area="DE", profile="Base", eex_file="DE.csv",
                 hours="Base", timezone="Europe/Berlin", comment="", region="original region", unit="EUR/MWh"),
            dict(product="GB_Off", use="off", area="GB", profile="Base", eex_file="",
                 hours="Base", timezone="Europe/London", comment="", region="original region", unit="EUR/MWh"),
        ]
        pd.DataFrame(maps, columns=COLUMNS).to_csv(root / "products.csv", index=False)
        (root / "curves").mkdir()
        settlements = [
            (day, "Month", delivery, price)
            for day in DAYS
            for delivery, price in (("2026-10-01", 100), ("2026-11-01", 110), ("2026-12-01", 120))
        ]
        pd.DataFrame(settlements, columns=[
            "tradeDate", "maturityType", "deliveryStart", "settlPx",
        ]).to_csv(root / "curves" / "DE.csv", index=False)
        return config, raw

    return create


def test_refill_preserves_input_and_completes_each_reference_curve(project):
    config, raw = project("refill")
    assert command(config) == 0
    output = config.parent / "output"
    enriched = read_csv(output / "enriched_history.csv")

    assert list(enriched.columns[: len(raw.columns)]) == list(raw.columns)
    originals = enriched[enriched["curve_row_type"] != "added"]
    pd.testing.assert_frame_equal(originals[raw.columns].reset_index(drop=True), raw)
    assert len(enriched) == len(raw) + 3
    assert list(originals["trade_id"]) == ["001", "002", "003", "004", "005", "006", "007"]
    assert originals.iloc[0]["note"] == "NA"
    assert list(originals.iloc[:2]["curve_price"].astype(float)) == [100, 110]
    assert originals.iloc[3]["curve_tenor"] == "D+4"
    assert "unmapped_product" in originals.iloc[4]["curve_flags"]
    assert "mapping_off" in originals.iloc[5]["curve_flags"]
    assert originals.iloc[5]["data_origin"] == "original"
    assert float(originals.iloc[5]["curve_price"]) == 0

    blank = originals[originals["trade_id"] == "003"].iloc[0]
    assert blank["vwap"] == ""
    assert blank["curve_row_type"] == "original_invalid"
    assert blank["data_origin"] == "estimated"
    assert 110 < float(blank["curve_price"]) < 115.5
    assert blank["estimation_method"].startswith("ratio_local")

    added = enriched[enriched["curve_row_type"] == "added"]
    assert set(zip(added["curve_reference_date"], added["tenor2"])) == {
        (DAYS[0], "M+3"), (DAYS[1], "M+2"), (DAYS[1], "M+3"),
    }
    assert (added["vwap"] == added["curve_price"]).all()
    assert (added["data_origin"] == "estimated").all()
    assert (added["unit"] == "EUR/MWh").all()
    assert (added["region"] == "original region").all()
    assert (added[["trade_id", "total_volume", "note"]] == "").all().all()
    for day in DAYS:
        daily = read_csv(output / "enriched" / f"{day}.csv")
        expected = enriched[enriched["curve_reference_date"] == day].reset_index(drop=True)
        pd.testing.assert_frame_equal(daily, expected)
        for tenor in ("M+1", "M+2", "M+3"):
            assert ((daily["product"] == "DE_Base load") & (daily["tenor2"] == tenor)).any()


def test_excel_override_preserves_originals_and_completes_missing_prices(project):
    config, raw = project("excel")
    raw.loc[raw["trade_id"] == "007", "note"] = "Excel-only original"
    workbook = config.parent / "real_input.xlsx"
    # Match a pandas spreadsheet export with an unnamed index column.
    raw.to_excel(workbook, index=True)
    assert main([
        "refill", "--vwap", str(workbook), "--from", DAYS[0], "--to", DAYS[-1],
    ], config) == 0
    enriched = read_csv(config.parent / "output" / "enriched_history.csv")
    originals = enriched[enriched["curve_row_type"] != "added"].reset_index(drop=True)
    assert list(enriched.columns[: len(raw.columns) + 1]) == ["Unnamed: 0", *raw.columns]
    pd.testing.assert_frame_equal(originals[raw.columns], raw)
    assert pd.to_numeric(originals["Unnamed: 0"]).tolist() == list(raw.index)

    first = originals.iloc[0]
    assert first["trade_id"] == "001" and first["note"] == "NA"
    assert first["vwap"] == "100,00" and float(first["curve_price"]) == 100
    assert first["data_origin"] == "original" and first["estimation_method"] == "none"
    blank = originals[originals["trade_id"] == "003"].iloc[0]
    assert blank["vwap"] == ""
    assert blank["data_origin"] == "estimated"
    assert 110 < float(blank["curve_price"]) < 115.5
    assert blank["estimation_method"].startswith("ratio_local")

    added = enriched[enriched["curve_row_type"] == "added"]
    assert len(added) == 3
    assert (added["data_origin"] == "estimated").all()
    assert (added["vwap"] == added["curve_price"]).all()
    assert (added["Unnamed: 0"] == "").all()


@pytest.mark.parametrize("offset", [0, -1, -2])
def test_daily_matches_refill_and_repeated_commands_preserve_history(project, offset):
    refill_config, _ = project("refill")
    daily_config, raw = project("daily")
    for path in (refill_config, daily_config):
        with path.open("a", encoding="utf-8") as file:
            file.write(f"\n[eex]\noffset_days = {offset}\n")
    assert command(refill_config) == 0
    daily_output = daily_config.parent / "output"
    for day in DAYS:
        assert command(daily_config, "daily", day) == 0
        expected = read_csv(refill_config.parent / "output" / "enriched" / f"{day}.csv")
        actual = read_csv(daily_output / "enriched" / f"{day}.csv")
        pd.testing.assert_frame_equal(actual, expected)

    history_before = read_csv(daily_output / "enriched_history.csv")
    earlier_before = (daily_output / "enriched" / f"{DAYS[0]}.csv").read_bytes()
    for _ in range(2):
        assert command(daily_config, "daily") == 0
        pd.testing.assert_frame_equal(read_csv(daily_output / "enriched_history.csv"), history_before)
        assert (daily_output / "enriched" / f"{DAYS[0]}.csv").read_bytes() == earlier_before

    # Updating one day's observations replaces that day's rows while retaining
    # older originals, their duplicates, leading zeroes and literal NA values.
    raw.loc[raw["trade_id"] == "007", "vwap"] = "108"
    raw.to_csv(daily_config.parent / "input.csv", index=False, encoding="utf-8-sig")
    assert command(daily_config, "daily") == 0
    updated = read_csv(daily_output / "enriched_history.csv")
    assert len(updated) == len(history_before)
    pd.testing.assert_frame_equal(
        updated[updated["curve_reference_date"] == DAYS[0]].reset_index(drop=True),
        history_before[history_before["curve_reference_date"] == DAYS[0]].reset_index(drop=True),
    )
    assert updated.loc[updated["trade_id"] == "007", "vwap"].tolist() == ["108"]
    assert float(updated.loc[updated["trade_id"] == "007", "curve_price"].iloc[0]) == 108

    # A repeated range also replaces rows instead of multiplying duplicate
    # observations or previously synthesized cells.
    assert command(refill_config) == 0
    assert command(refill_config) == 0
    repeated = read_csv(refill_config.parent / "output" / "enriched_history.csv")
    keys = ["curve_reference_date", "curve_product", "curve_region", "curve_unit", "curve_tenor", "trade_id"]
    pd.testing.assert_frame_equal(
        repeated.sort_values(keys).reset_index(drop=True),
        history_before.sort_values(keys).reset_index(drop=True),
    )


@pytest.mark.parametrize("verb", ["refill", "daily"])
def test_only_unmapped_input_produces_enriched_file_without_engine_rows(project, verb):
    config, raw = project("unmapped", only_unmapped=True)
    arguments = [verb] if verb == "refill" else [verb, "--date", DAYS[0]]
    assert main(arguments, config) == 0
    result = read_csv(config.parent / "output" / "enriched_history.csv")
    pd.testing.assert_frame_equal(result[raw.columns], raw)
    assert result["curve_flags"].tolist() == ["unmapped_product"]
    assert result["data_origin"].tolist() == ["original"]
    assert not (config.parent / "output" / "filled_history.csv").exists()


@pytest.mark.parametrize("verb", ["refill", "daily"])
def test_internal_engine_failure_returns_error_and_preserves_all_outputs(project, monkeypatch, verb):
    config, raw = project("failure")
    assert command(config) == 0
    output = config.parent / "output"
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*.csv")}
    raw.loc[raw["trade_id"] == "001", "vwap"] = "105"
    raw.to_csv(config.parent / "input.csv", index=False, encoding="utf-8-sig")
    original_prep = CurveFiller._prep

    def fail_on_last_day(self, series, day):
        if day.isoformat() == DAYS[-1]:
            raise RuntimeError("injected internal calculation failure")
        return original_prep(self, series, day)

    monkeypatch.setattr(CurveFiller, "_prep", fail_on_last_day)
    assert command(config, verb) == 1
    after = {p.relative_to(output): p.read_bytes() for p in output.rglob("*.csv")}
    assert after == before
    logs = "\n".join(path.read_text(encoding="utf-8") for path in (output / "_logs").glob("*.log"))
    assert "injected internal calculation failure" in logs
    assert "No results written" in logs


@pytest.mark.parametrize("use_aliases", [False, True])
def test_same_product_curves_fill_and_upsert_independently(project, use_aliases):
    config, _ = project("multiple_curves")
    product = "DE_Base load"
    identities = [("North", "EUR/MWh"), ("South", "EUR/MWh"), ("North", "GBP/MWh")]
    rows, maps = [], []
    for i, (region, unit) in enumerate(identities):
        base = 100 * (i + 1)
        for tenor, price, suffix in (("M+1", str(base + 10), "own"), ("M+2", "", "blank")):
            rows.append(dict(reference_date="29/09/2026", product=product, region=region, unit=unit,
                             tenor2=tenor, vwap=price, total_volume="10", trade_id=f"00{i}-{suffix}"))
        filename = f"curve_{i}.csv"
        maps.append(dict(product=product, region=region, unit=unit, use="fill", area="DE", profile="Base",
                         eex_file=filename, hours="Base", timezone="Europe/Berlin", comment=""))
        pd.DataFrame([
            (DAYS[0], "Month", "2026-10-01", base),
            (DAYS[0], "Month", "2026-11-01", base + 20),
            (DAYS[0], "Month", "2026-12-01", base + 30),
        ], columns=["tradeDate", "maturityType", "deliveryStart", "settlPx"]).to_csv(
            config.parent / "curves" / filename, index=False)
    raw = pd.DataFrame(rows)
    if use_aliases:
        raw = raw.rename(columns={"region": "market_area", "unit": "price_unit"})
        config.write_text(config.read_text(encoding="utf-8")
                          + 'region = "market_area"\nunit = "price_unit"\n', encoding="utf-8")
    raw.to_csv(config.parent / "input.csv", index=False)
    pd.DataFrame(maps, columns=COLUMNS).to_csv(config.parent / "products.csv", index=False)
    assert command(config, "daily", DAYS[0]) == 0
    output = config.parent / "output"
    enriched = read_csv(output / "enriched_history.csv")
    originals = enriched[enriched["curve_row_type"] != "added"].reset_index(drop=True)
    pd.testing.assert_frame_equal(originals[raw.columns], raw)
    assert len(enriched) == 9
    for i, (region, unit) in enumerate(identities):
        curve = enriched[(enriched["curve_region"] == region) & (enriched["curve_unit"] == unit)]
        assert set(curve["curve_tenor"]) == {"M+1", "M+2", "M+3"}
        assert len(curve) == 3
        blank = curve[curve["trade_id"] == f"00{i}-blank"].iloc[0]
        assert blank["vwap"] == "" and blank["data_origin"] == "estimated"
        assert blank["estimation_method"].startswith("ratio_local")
        assert 100 * (i + 1) + 20 < float(blank["curve_price"]) < 100 * (i + 1) + 35
        added = curve[curve["curve_row_type"] == "added"].iloc[0]
        assert added["market_area" if use_aliases else "region"] == region
        assert added["price_unit" if use_aliases else "unit"] == unit
        assert "unit_ambiguous" not in added["curve_flags"]

    files = [output / "enriched_history.csv", output / "enriched" / f"{DAYS[0]}.csv",
             output / "filled_history.csv", output / "filled" / f"{DAYS[0]}.csv"]
    before = {path: read_csv(path) for path in files}
    # Recalculate only North/EUR. The same product's other region/currency
    # combinations must survive in both daily files and cumulative files.
    replacement = raw[raw["trade_id"].str.startswith("000-")].copy()
    replacement.loc[replacement["trade_id"] == "000-own", "vwap"] = "115"
    replacement.to_csv(config.parent / "input.csv", index=False)
    pd.DataFrame(maps[:1], columns=COLUMNS).to_csv(config.parent / "products.csv", index=False)
    assert command(config, "daily", DAYS[0]) == 0
    for path in files:
        previous, updated = before[path], read_csv(path)
        assert len(updated) == len(previous) == 9
        region_col = "curve_region" if "curve_region" in updated else "region"
        unit_col = "curve_unit" if "curve_unit" in updated else "unit"
        previous_other = previous[(previous[region_col] != "North") | (previous[unit_col] != "EUR/MWh")]
        updated_other = updated[(updated[region_col] != "North") | (updated[unit_col] != "EUR/MWh")]
        pd.testing.assert_frame_equal(updated_other.reset_index(drop=True), previous_other.reset_index(drop=True))
    updated = read_csv(output / "enriched_history.csv")
    assert updated.loc[updated["trade_id"] == "000-own", "vwap"].tolist() == ["115"]


def test_new_mapping_rows_are_off_until_reviewed_even_when_eex_exists(project):
    config, raw = project("mapping_draft")
    mapping_file = config.parent / "products.csv"
    pd.DataFrame(columns=COLUMNS).to_csv(mapping_file, index=False)
    curve_dir = config.parent / "curves"
    (curve_dir / "DE").mkdir()
    (curve_dir / "DE" / "Base.csv").write_bytes((curve_dir / "DE.csv").read_bytes())

    assert main(["mapping"], config) == 0
    draft = read_csv(mapping_file)
    assert len(draft) == 3
    assert (draft["use"] == "off").all()
    assert draft.loc[draft["product"] == "DE_Base load", "eex_file"].tolist() == ["DE/Base.csv"]
    assert command(config) == 0
    output = config.parent / "output"
    enriched = read_csv(output / "enriched_history.csv")
    pd.testing.assert_frame_equal(enriched[raw.columns], raw)
    assert "added" not in set(enriched["curve_row_type"])
    assert enriched["curve_flags"].str.contains("mapping_off").all()
    assert not (output / "filled_history.csv").exists()

    # An existing reviewed decision is retained when a new region appears.
    selected = draft["product"] == "DE_Base load"
    draft.loc[selected, "use"] = "fill"
    draft.loc[selected, "comment"] = "manually reviewed"
    draft.to_csv(mapping_file, index=False)
    another_region = raw.iloc[[0]].assign(region="new region", trade_id="new curve")
    pd.concat([raw, another_region], ignore_index=True).to_csv(config.parent / "input.csv", index=False)
    assert main(["mapping"], config) == 0
    expanded = read_csv(mapping_file)
    reviewed = expanded[(expanded["product"] == "DE_Base load") & (expanded["region"] == "original region")]
    assert reviewed["use"].tolist() == ["fill"]
    assert reviewed["comment"].tolist() == ["manually reviewed"]
    assert expanded.loc[expanded["region"] == "new region", "use"].tolist() == ["off"]


def test_off_and_unmapped_curves_preserve_blanks_without_generating_tenors(project):
    config, raw = project("excluded")
    excluded = raw[raw["product"].isin(["GB_Off", "UNMAPPED"])].copy()
    blanks = excluded.assign(tenor2="M+2", vwap="", trade_id=lambda frame: frame["trade_id"] + "-blank")
    raw = pd.concat([raw, blanks], ignore_index=True)
    raw.to_csv(config.parent / "input.csv", index=False)
    assert command(config) == 0
    enriched = read_csv(config.parent / "output" / "enriched_history.csv")
    for product, flag in (("GB_Off", "mapping_off"), ("UNMAPPED", "unmapped_product")):
        expected = raw[raw["product"] == product].reset_index(drop=True)
        actual = enriched[enriched["curve_product"] == product].reset_index(drop=True)
        pd.testing.assert_frame_equal(actual[raw.columns], expected)
        assert "added" not in set(actual["curve_row_type"])
        assert actual["curve_flags"].str.contains(flag).all()
        blank = actual[actual["vwap"] == ""].iloc[0]
        assert blank["curve_price"] == "" and blank["data_origin"] == "missing"
        assert blank["estimation_method"] == "none"


@pytest.mark.parametrize("use", ["fill", "helper"])
def test_curve_without_eex_assignment_is_preserved_without_generated_targets(project, use):
    config, raw = project("unassigned")
    unassigned = raw.iloc[[0, 2]].copy()
    unassigned["region"] = "Unassigned region"
    unassigned["unit"] = "GBP/MWh"
    unassigned["trade_id"] = ["unassigned-own", "unassigned-blank"]
    unassigned["vwap"] = ["150", ""]
    pd.concat([raw, unassigned], ignore_index=True).to_csv(config.parent / "input.csv", index=False)
    mapping_file = config.parent / "products.csv"
    mapping = read_csv(mapping_file)
    added_map = mapping[mapping["product"] == "DE_Base load"].copy()
    added_map["region"] = "Unassigned region"
    added_map["unit"] = "GBP/MWh"
    added_map["use"] = use
    added_map["eex_file"] = ""
    pd.concat([mapping, added_map], ignore_index=True).to_csv(mapping_file, index=False)

    assert command(config) == 0
    output = config.parent / "output"
    enriched = read_csv(output / "enriched_history.csv")
    preserved = enriched[enriched["curve_region"] == "Unassigned region"].reset_index(drop=True)
    pd.testing.assert_frame_equal(preserved[raw.columns], unassigned.reset_index(drop=True))
    assert len(preserved) == 2
    assert "added" not in set(preserved["curve_row_type"])
    assert preserved["curve_flags"].str.contains("mapping_unassigned").all()
    assert preserved.iloc[0]["data_origin"] == "original"
    assert float(preserved.iloc[0]["curve_price"]) == 150
    assert preserved.iloc[1]["data_origin"] == "missing"
    assert preserved.iloc[1]["curve_price"] == ""
    filled = read_csv(output / "filled_history.csv")
    assert set(filled["region"]) == {"original region"}
    assert set(filled["unit"]) == {"EUR/MWh"}
    anchor = filled[(filled["reference_date"] == DAYS[0]) & (filled["tenor"] == "M+1")]
    assert anchor["price"].astype(float).tolist() == [105.0]


@pytest.mark.parametrize("legacy_name", [
    "filled_history.csv", "consistency_history.csv", "enriched_history.csv",
    f"filled/{DAYS[-1]}.csv", f"enriched/{DAYS[-1]}.csv",
])
@pytest.mark.parametrize("verb", ["daily", "refill"])
def test_legacy_output_missing_curve_identity_prevents_all_result_writes(project, legacy_name, verb):
    config, _ = project("legacy_output")
    output = config.parent / "output"
    legacy = output / legacy_name
    legacy.parent.mkdir(parents=True, exist_ok=True)
    if legacy_name.startswith("enriched"):
        content = "curve_reference_date,curve_product,curve_tenor,curve_price\n2026-09-30,DE_Base load,M+1,999\n"
    else:
        content = "reference_date,product,tenor,price\n2026-09-30,DE_Base load,M+1,999\n"
    legacy.write_text(content, encoding="utf-8")
    before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*.csv")}
    assert command(config, verb) == 1
    after = {path.relative_to(output): path.read_bytes() for path in output.rglob("*.csv")}
    assert after == before
    log_text = "\n".join(path.read_text(encoding="utf-8") for path in (output / "_logs").glob("*.log"))
    assert "Legacy output" in log_text
    assert "region" in log_text and "unit" in log_text
    assert "No result files written" in log_text
