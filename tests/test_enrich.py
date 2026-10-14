from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.enrich import enrich_input
from vwaps.mapping import ProductMap


DAY = date(2026, 9, 30)


@pytest.fixture
def cfg():
    return load_config(Path(__file__).resolve().parents[1] / "config.toml")


def mapping(product="DE_Base load", use="fill", region="original region", unit=None, eex_file="fixture.csv"):
    unit = unit if unit is not None else "GBP/MWh" if product.startswith("GB") else "EUR/MWh"
    return ProductMap(product, use, product[:2], "Base", eex_file, "Base", "Europe/Berlin", region=region, unit=unit)


def own(product="DE_Base load", tenor="M+1", vwap=100.0, region="original region", unit=None, **extra):
    unit = unit if unit is not None else "GBP/MWh" if product.startswith("GB") else "EUR/MWh"
    return dict(reference_date="30/09/2026", weekday="Wednesday", product=product,
                country=product[:2], region=region, classification="Base load", unit=unit,
                periodicity_2="Monthly", tenor2=tenor, vwap=vwap,
                total_volume=10, n_trades=2, **extra)


def calculated(product="DE_Base load", tenor="M+2", price=110.0, source="eex+local",
               region="original region", unit=None, **extra):
    unit = unit if unit is not None else "GBP/MWh" if product.startswith("GB") else "EUR/MWh"
    return dict(reference_date=DAY, product=product, region=region, unit=unit, tenor=tenor, price=price,
                source=source, eex_method="exact", **extra)


def test_preserves_every_original_value_order_duplicate_and_unknown(cfg):
    raw = pd.DataFrame([
        own(vwap="100,01", trade_id="trade A"),
        own(vwap="120,02", trade_id="trade B"),
        own(tenor="D+4", vwap=80, trade_id="trade C"),
        own(product="GB_Other_Block_1_2", vwap=-5, trade_id="trade D"),
    ])
    snapshot = raw.copy(deep=True)
    filled = pd.DataFrame([calculated(tenor="M+1", price=115, source="own"), calculated()])
    output = enrich_input(raw, filled, cfg, [mapping()], DAY, DAY)
    pd.testing.assert_frame_equal(raw, snapshot)
    pd.testing.assert_frame_equal(output.loc[:3, raw.columns], raw, check_dtype=False)
    assert list(output.iloc[:4].data_origin) == ["original"] * 4
    assert list(output.iloc[:4].curve_price) == [100.01, 120.02, 80, -5]
    assert output.iloc[3].curve_flags == "unmapped_product"
    assert len(output) == 5
    added = output.iloc[4]
    assert added.tenor2 == "M+2" and added.vwap == 110
    assert added.data_origin == "estimated"
    assert added.unit == "EUR/MWh" and added.region == "original region"
    assert added.reference_date == "30/09/2026"
    assert pd.isna(added.total_volume) and pd.isna(added.n_trades) and pd.isna(added.trade_id)


def test_original_invalid_is_preserved_and_estimate_has_separate_price(cfg):
    raw = pd.DataFrame([own(vwap=None), own(tenor="M+3", vwap="bad"), own(tenor="M+4", vwap=0)])
    filled = pd.DataFrame([calculated(tenor="M+1", price=111), calculated(tenor="M+3", price=float("nan"), source="missing")])
    out = enrich_input(raw, filled, cfg, [mapping()], DAY, DAY)
    assert len(out) == 3
    assert pd.isna(out.iloc[0].vwap) and out.iloc[0].curve_price == 111
    assert out.iloc[0].data_origin == "estimated"
    assert out.iloc[0].curve_row_type == "original_invalid"
    assert out.iloc[1].vwap == "bad" and pd.isna(out.iloc[1].curve_price)
    assert out.iloc[1].data_origin == "missing"
    assert out.iloc[2].vwap == 0 and out.iloc[2].data_origin == "original"


def test_same_product_regions_and_units_have_independent_prices_and_metadata(cfg):
    identities = [("North", "EUR/MWh"), ("South", "EUR/MWh"), ("North", "GBP/MWh")]
    raw = pd.DataFrame([
        own(region=region, unit=unit, vwap="", currency=f"metadata {i}")
        for i, (region, unit) in enumerate(identities)
    ])
    filled = pd.DataFrame([
        calculated(region=region, unit=unit, tenor=tenor, price=100 * (i + 1))
        for i, (region, unit) in enumerate(identities) for tenor in ("M+1", "M+2")
    ])
    maps = [mapping(region=region, unit=unit) for region, unit in identities]
    out = enrich_input(raw, filled, cfg, maps, DAY, DAY)
    assert len(out) == 6
    pd.testing.assert_frame_equal(out.loc[:2, raw.columns], raw, check_dtype=False)
    for i, (region, unit) in enumerate(identities):
        curve = out[(out.curve_region == region) & (out.curve_unit == unit)]
        assert len(curve) == 2
        assert list(curve.curve_price) == [100 * (i + 1)] * 2
        added = curve[curve.curve_row_type == "added"].iloc[0]
        assert added.unit == unit and added.region == region
        assert added.currency == f"metadata {i}"
        assert added.curve_flags == ""


def test_new_curve_uses_engine_identity_without_borrowing_other_curve_metadata(cfg):
    raw = pd.DataFrame([own(region="North", unit="EUR/MWh", currency="EUR")])
    filled = pd.DataFrame([calculated(region="South", unit="GBP/MWh")])
    out = enrich_input(raw, filled, cfg, [mapping(region="South", unit="GBP/MWh")], DAY, DAY)
    assert out.iloc[0].curve_flags == "unmapped_product"
    added = out.iloc[-1]
    assert added.region == added.curve_region == "South"
    assert added.unit == added.curve_unit == "GBP/MWh"
    assert pd.isna(added.currency) and pd.isna(added.country)
    assert added.curve_flags == ""


def test_missing_identity_dimensions_use_empty_keys_without_product_fallback(cfg):
    raw = pd.DataFrame([own(vwap=None)]).drop(columns=["region", "unit"])
    filled = pd.DataFrame([calculated(tenor="M+1", price=111)])
    out = enrich_input(raw, filled, cfg, [mapping()], DAY, DAY)
    assert len(out) == 2
    assert out.iloc[0].curve_region == out.iloc[0].curve_unit == ""
    assert out.iloc[0].data_origin == "missing"
    assert "unmapped_product" in out.iloc[0].curve_flags
    assert out.iloc[1].curve_region == "original region"
    assert out.iloc[1].curve_unit == "EUR/MWh"


def test_legacy_empty_identity_still_matches_and_marks_missing_units(cfg):
    raw = pd.DataFrame([own()]).drop(columns=["region", "unit"])
    filled = pd.DataFrame([calculated()]).drop(columns=["region", "unit"])
    out = enrich_input(raw, filled, cfg, [mapping(region="", unit="")], DAY, DAY)
    assert len(out) == 2
    assert (out.curve_region == "").all() and (out.curve_unit == "").all()
    assert out.iloc[0].curve_flags == ""
    assert out.iloc[1].curve_flags == "unit_missing"


def test_mapping_status_applies_to_full_curve_identity(cfg):
    raw = pd.DataFrame([own(region="North"), own(region="South"), own(region="West")])
    out = enrich_input(raw, pd.DataFrame(), cfg,
                       [mapping(region="North"), mapping(region="South", use="off")], DAY, DAY)
    assert list(out.curve_flags) == ["", "mapping_off", "unmapped_product"]


@pytest.mark.parametrize("use", ["fill", "helper"])
def test_mapping_without_eex_assignment_preserves_original_and_marks_missing(cfg, use):
    raw = pd.DataFrame([own(), own(tenor="M+2", vwap="")])
    out = enrich_input(raw, pd.DataFrame(), cfg, [mapping(use=use, eex_file="")], DAY, DAY)
    pd.testing.assert_frame_equal(out[raw.columns], raw)
    assert len(out) == 2
    assert out.iloc[0].curve_flags == "mapping_unassigned"
    assert out.iloc[0].curve_price == 100 and out.iloc[0].data_origin == "original"
    assert "mapping_unassigned" in out.iloc[1].curve_flags
    assert out.iloc[1].data_origin == "missing" and pd.isna(out.iloc[1].curve_price)


def test_identity_whitespace_is_normalized_without_changing_original_cells(cfg):
    raw = pd.DataFrame([own(region=" North ", unit=" EUR/MWh ", vwap="")])
    filled = pd.DataFrame([
        calculated(region="North", tenor="M+1", price=101),
        calculated(region="North", tenor="M+2", price=102),
    ])
    out = enrich_input(raw, filled, cfg, [mapping(region="North")], DAY, DAY)
    assert len(out) == 2
    assert out.iloc[0].region == " North " and out.iloc[0].unit == " EUR/MWh "
    assert out.iloc[0].vwap == "" and out.iloc[0].curve_price == 101
    assert out.iloc[0].curve_region == "North" and out.iloc[0].curve_unit == "EUR/MWh"
    assert out.iloc[1].region == "North" and out.iloc[1].unit == "EUR/MWh"
    assert "unmapped_product" not in out.iloc[0].curve_flags


def test_range_off_mapping_and_missing_estimates(cfg):
    earlier = own()
    earlier["reference_date"] = "29/09/2026"
    raw = pd.DataFrame([earlier, own(product="GB_Other_Block_1_2")])
    filled = pd.DataFrame([calculated(price=float("nan"), source="missing")])
    out = enrich_input(raw, filled, cfg, [mapping(), mapping("GB_Other_Block_1_2", "off")], DAY, DAY)
    assert len(out) == 2
    assert out.iloc[0].data_origin == "original" and out.iloc[0].curve_flags == "mapping_off"
    assert out.iloc[1].data_origin == "missing" and pd.isna(out.iloc[1].vwap)
    assert out.iloc[1].estimation_method == "none"


def test_keeps_explicit_method_and_eex_trace(cfg):
    raw = pd.DataFrame([own()])
    filled = pd.DataFrame([calculated(estimation_method="ratio_local_history_cross", basis_mode="ratio",
                                     basis_local=.02, basis_hist=.01, cross_adj=.001, local_weight=.6)])
    out = enrich_input(raw, filled, cfg, [mapping()], DAY, DAY)
    added = out.iloc[-1]
    assert added.estimation_method == "ratio_local_history_cross"
    assert added.curve_eex_method == "exact"
    assert added.curve_local_weight == .6 and added.curve_basis_mode == "ratio"
    assert out.iloc[0].estimation_method == "none"


def test_added_equivalent_period_identifies_reused_original(cfg):
    day = date(2026, 9, 29)  # D+1 and BOM both deliver on September 30.
    raw = pd.DataFrame([own(tenor="D+1")])
    raw.loc[0, "reference_date"] = "29/09/2026"
    filled = pd.DataFrame([calculated(tenor="BOM", price=100, source="own",
                                     estimation_method="none")])
    filled["reference_date"] = day
    out = enrich_input(raw, filled, cfg, [mapping()], day, day)
    assert out.iloc[0].data_origin == "original"
    assert out.iloc[0].estimation_method == "none"
    assert out.iloc[-1].data_origin == "estimated"
    assert out.iloc[-1].estimation_method == "own_equivalent_period"


@pytest.mark.parametrize("reserved", ["data_origin", "estimation_method", "curve_price"])
def test_reserved_columns_fail_without_overwriting(cfg, reserved):
    raw = pd.DataFrame([own()])
    raw[reserved] = "keep me"
    with pytest.raises(ValueError, match="Reserved"):
        enrich_input(raw, pd.DataFrame(), cfg, [mapping()], DAY, DAY)
    assert raw[reserved].iloc[0] == "keep me"


def test_custom_columns_iso_dates_and_periodicity(cfg):
    cfg.vwap_columns = dict(reference_date="asof", product="instrument", tenor="term", vwap="observed",
                            volume="quantity", region="market_area", unit="price_unit")
    raw = pd.DataFrame([dict(asof="2026-09-30", instrument="DE_Base load", term="M+1", observed=100,
                             quantity=5, price_unit="EUR/MWh", market_area="original region",
                             weekday="Wednesday", periodicity_2="Monthly")])
    out = enrich_input(raw, pd.DataFrame([calculated(tenor="Q+1")]), cfg, [mapping()], DAY, DAY)
    assert out.iloc[-1]["asof"] == "2026-09-30"
    assert out.iloc[-1].term == "Q+1" and out.iloc[-1].periodicity_2 == "Quarterly"
    assert out.iloc[-1].weekday == "Wednesday" and pd.isna(out.iloc[-1].quantity)
    assert out.iloc[-1].market_area == "original region" and out.iloc[-1].price_unit == "EUR/MWh"
    assert out.iloc[-1].curve_region == "original region" and out.iloc[-1].curve_unit == "EUR/MWh"
    assert "region" not in out and "unit" not in out


def test_invalid_dates_and_duplicate_engine_keys_fail(cfg):
    raw = pd.DataFrame([own()])
    with pytest.raises(ValueError, match="duplicate key"):
        enrich_input(raw, pd.DataFrame([calculated(), calculated()]), cfg, [mapping()], DAY, DAY)
    raw.loc[0, "reference_date"] = None
    with pytest.raises(ValueError, match="dates"):
        enrich_input(raw, pd.DataFrame(), cfg, [mapping()], DAY, DAY)
