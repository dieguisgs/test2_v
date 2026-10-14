"""Preserve original rows and append newly created curve points.

``curve_price`` contains the usable price even when an original row has an
empty or invalid VWAP. Original columns are never overwritten in those rows;
``curve_row_type`` distinguishes that case from a newly added row.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

import pandas as pd

from vwaps.config import Config
from vwaps.dates import parse_reference_dates
from vwaps.identity import CurveKey, curve_keys
from vwaps.log import get_logger
from vwaps.mapping import ProductMap
from vwaps.product_config import effective_config
from vwaps.tenors import resolve_tenor


PERIODICITY = {
    "Day": "Daily", "Weekend": "Weekend", "BOW": "BOW", "Week": "Weekly",
    "BOM": "BOM", "Month": "Monthly", "Quarter": "Quarterly",
    "Season": "Seasonal", "Year": "Annual",
}
# Copy product attributes only, never trade identifiers, prices, volumes or
# other transaction fields merely because they happen to be constant.
PRODUCT_METADATA = ("country", "classification", "currency", "area", "profile")


def _number(value) -> float:
    try:
        number = float(str(value).strip().replace(",", "."))
        return number if math.isfinite(number) else math.nan
    except (TypeError, ValueError):
        return math.nan


def _text(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _dates(values: pd.Series, label: str) -> pd.Series:
    try:
        parsed = parse_reference_dates(values).dt.date
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid dates") from exc
    if parsed.isna().any():
        raise ValueError(f"{label}: empty or invalid dates")
    return parsed


def _date_formatter(values: pd.Series):
    sample = next((v for v in values if not pd.isna(v)), None)
    if isinstance(sample, str):
        pattern = "%d/%m/%Y" if re.match(r"^\s*\d{1,2}/\d{1,2}/\d{4}", sample) else "%Y-%m-%d"
        return lambda d: d.strftime(pattern)
    if isinstance(sample, (pd.Timestamp, datetime)):
        return pd.Timestamp
    return lambda d: d


def _method(row: dict, cfg: Config) -> str:
    source = _text(row.get("source", "missing"))
    if source == "own":
        return "own_equivalent_period"
    explicit = _text(row.get("estimation_method", ""))
    if explicit:
        return explicit
    if source == "missing":
        return "none"
    if source in ("eex", "arbitrage"):
        return source
    mode = _text(row.get("basis_mode", "")) or cfg.basis_mode
    weight = _number(row.get("local_weight"))
    components = []
    if not math.isnan(_number(row.get("basis_local"))) and weight > 0:
        components.append("local")
    if not math.isnan(_number(row.get("basis_hist"))) and (math.isnan(weight) or weight < 1):
        components.append("history")
    if not math.isnan(_number(row.get("cross_adj"))) and (math.isnan(weight) or weight < 1):
        components.append("cross")
    if not components:
        components = [{"eex+local": "local", "eex+hist": "history", "eex+cross": "cross"}.get(source, source)]
    return "_".join([mode, *components])


def enrich_input(
    raw: pd.DataFrame, filled: pd.DataFrame, cfg: Config,
    maps: list[ProductMap], start: date, end: date,
) -> pd.DataFrame:
    """Preserve originals in [start, end] and append missing engine keys.

    Keys are date/product/region/unit/tenor label, rather than delivery period.
    Preserve duplicates, unmapped curves and tenors outside the targets.
    ``data_origin`` describes the final price: original, estimated or missing.
    Engine provenance uses the ``curve_`` prefix to protect input columns.
    """
    if start > end:
        raise ValueError("The start date must be on or before the end date")
    if not raw.columns.is_unique:
        raise ValueError("The input contains duplicate column names")
    collisions = [c for c in raw if c in ("data_origin", "estimation_method") or str(c).startswith("curve_")]
    if collisions:
        raise ValueError(f"Reserved enrichment columns found in the input: {collisions}")
    col = {key: cfg.vwap_columns.get(key, key)
           for key in ("reference_date", "product", "region", "unit", "tenor", "vwap", "volume")}
    required = [col[key] for key in ("reference_date", "product", "tenor", "vwap")]
    missing = [name for name in required if name not in raw]
    if missing:
        raise ValueError(f"Missing original columns required for enrichment: {missing}")
    dates = _dates(raw[col["reference_date"]], "input")
    curves = curve_keys(raw, cfg)
    tenors = raw[col["tenor"]].map(_text)
    keys = [(day, *curve, tenor) for day, curve, tenor in zip(dates, curves, tenors)]
    format_date = _date_formatter(raw[col["reference_date"]])

    engine: dict[tuple, dict] = {}
    if not filled.empty:
        needed = ["reference_date", "product", "tenor", "price", "source"]
        absent = [name for name in needed if name not in filled]
        if absent:
            raise ValueError(f"Missing engine columns: {absent}")
        engine_dates = _dates(filled["reference_date"], "engine")
        for day, curve, record in zip(engine_dates, curve_keys(filled), filled.to_dict("records")):
            if not start <= day <= end:
                continue
            key = (day, *curve, _text(record["tenor"]))
            if key in engine:
                raise ValueError(f"The engine returned a duplicate key: {key}")
            engine[key] = record

    metadata: dict[CurveKey, dict] = {}
    ambiguous: dict[CurveKey, set] = {}
    for curve, frame in raw.groupby(curves, sort=False):
        metadata[curve] = {}
        ambiguous[curve] = set()
        for name in PRODUCT_METADATA:
            if name not in frame:
                continue
            values = frame[name].dropna()
            values = values[values.map(lambda value: bool(_text(value)))].drop_duplicates()
            if len(values) == 1:
                metadata[curve][name] = values.iloc[0]
            elif len(values) > 1:
                ambiguous[curve].add(name)
    known = {m.key: m for m in maps}
    settings = {m.key: effective_config(cfg, m) for m in maps if m.active}
    all_trace = ["reference_date", "product", "region", "unit", "tenor", "price", "source", "row_type", "flags"]
    all_trace += [c for c in filled if c not in ("data_origin", "estimation_method") and c not in all_trace]
    extra_cols = ["data_origin", "estimation_method", *[f"curve_{c}" for c in all_trace]]

    def decorate(record: dict, key: tuple, row_type: str, original: bool) -> dict:
        model = engine.get(key, {})
        for name in all_trace:
            record[f"curve_{name}"] = model.get(name, math.nan)
        day, product, region, unit, tenor = key
        record.update(curve_reference_date=day, curve_product=product,
                      curve_region=region, curve_unit=unit,
                      curve_tenor=tenor, curve_row_type=row_type)
        flags = [_text(model.get("flag", ""))]
        m = known.get((product, region, unit))
        curve_cfg = settings.get((product, region, unit), cfg)
        if m is None:
            flags.append("unmapped_product")
        elif m.use == "off":
            flags.append("mapping_off")
        elif not m.eex_file:
            flags.append("mapping_unassigned")
        elif m.use != "fill":
            flags.append(f"mapping_{m.use}")
        if original:
            original_price = _number(record[col["vwap"]])
            record.update(data_origin="original", estimation_method="none",
                          curve_price=original_price, curve_source="own")
            if "curve_own_vwap" in record:
                record["curve_own_vwap"] = original_price
            if "curve_own_volume" in record:
                record["curve_own_volume"] = _number(record.get(col["volume"]))
            if "price_before_shape" in model:
                # The engine works with one volume-aggregated price per period.
                # Apply its common additive change to each physical input row,
                # preserving duplicates and every original input column.
                proposed_delta = _number(model.get("shape_proposed_adjustment"))
                proposed_delta = proposed_delta if math.isfinite(proposed_delta) else 0.0
                record.update(curve_price_before_shape=original_price,
                              curve_source_before_shape="own",
                              curve_estimation_method_before_shape="none",
                              curve_data_origin_before_shape="original",
                              curve_shape_proposed_price=original_price + proposed_delta)
                if (curve_cfg.shape_mode == "adjust" and curve_cfg.shape_adjust_originals
                        and model.get("shape_original_modified") is True):
                    delta = _number(model.get("shape_adjustment"))
                    final_price = original_price + delta
                    if not math.isfinite(final_price):
                        raise ValueError("Shape adjustment exceeds the finite range of an original row")
                    record.update(data_origin="estimated",
                                  estimation_method="shape_adjusted_original",
                                  curve_price=final_price, curve_source="own+shape")
        else:
            price = _number(model.get("price"))
            available = not math.isnan(price) and model.get("source") != "missing"
            record.update(data_origin="estimated" if available else "missing",
                          estimation_method=_method(model, curve_cfg) if available else "none",
                          curve_price=price if available else math.nan,
                          curve_source=model.get("source", "missing"))
            if row_type == "original_invalid":
                flags.append("original_vwap_missing_or_invalid")
        record["curve_flags"] = ";".join(flag for flag in flags if flag)
        return record

    rows = []
    present = set()
    for key, record in zip(keys, raw.to_dict("records")):
        if start <= key[0] <= end:
            present.add(key)
            valid = not math.isnan(_number(record[col["vwap"]]))
            rows.append(decorate(record, key, "original" if valid else "original_invalid", valid))
    unit_warnings = set()
    for key, model in engine.items():
        if key in present:
            continue
        day, product, region, unit, tenor = key
        curve = (product, region, unit)
        record = {name: math.nan for name in raw.columns}
        record.update(metadata.get(curve, {}))
        record.update({col["reference_date"]: format_date(day), col["product"]: product,
                       col["region"]: region, col["unit"]: unit,
                       col["tenor"]: tenor, col["vwap"]: _number(model.get("price"))})
        if "weekday" in raw:
            record["weekday"] = day.strftime("%A")
        if "periodicity_2" in raw:
            per = resolve_tenor(tenor, day, cfg.day_convention, cfg.weekend_offset)
            record["periodicity_2"] = PERIODICITY.get(per.kind, math.nan) if per else math.nan
        record = decorate(record, key, "added", False)
        # Newly added rows always use curve_price in the VWAP column as well.
        record[col["vwap"]] = record["curve_price"]
        flags = [record["curve_flags"]]
        if not unit:
            flags.append("unit_missing")
            unit_warnings.add(curve)
        flags += [f"metadata_ambiguous:{name}" for name in sorted(ambiguous.get(curve, set()))]
        record["curve_flags"] = ";".join(flag for flag in flags if flag)
        rows.append(record)
    if unit_warnings:
        get_logger().warning("Unit unavailable in new rows; left empty: %s",
                             "; ".join(map(str, sorted(unit_warnings))))
    output = pd.DataFrame(rows, columns=[*raw.columns, *extra_cols])
    if "curve_eex_offset_days" in output:
        # Unmapped/raw-only rows have no engine policy. Nullable integers keep
        # CSV values identical in daily and multi-day runs despite those blanks.
        output["curve_eex_offset_days"] = pd.array(output["curve_eex_offset_days"], dtype="Int64")
    return output
