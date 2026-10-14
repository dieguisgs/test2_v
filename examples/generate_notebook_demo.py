"""Generate a complete, deterministic synthetic output for the curve notebook.

Run from any working directory with the project's Python environment:
    python examples/generate_notebook_demo.py

The script never reads real VWAP or EEX files. It creates three deliberately
artificial curve identities and seven reference dates spanning a month/quarter
roll. Every configured target is included, plus M+0 through M+15 so that the
next calendar year has all twelve component months. The default configuration
therefore provides all nine supported families and Q+1 through Q+8.

Synthetic reference quotes are aggregated from one daily delivery-price path,
using Base delivery hours, including daylight-saving changes. Monthly,
quarterly, seasonal and annual EEX references are consequently coherent before
rounding. Sparse own observations have deliberately different adjustments by
family. The actual CurveFiller computes the missing prices and the optional
shape layer, so plotted differences are real engine results on invented data.

Outputs are restricted to output/notebook_validation/. Existing real output
is refused. The generated CSVs and manifest are ignored by Git. This example
illustrates the UI and engine behavior; it is not an accuracy backtest and the
synthetic GBP curve does not represent a currency conversion.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.hours import period_hours
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.tenors import Period, resolve_tenor


START = date(2026, 9, 28)
END = date(2026, 10, 6)
PUBLICATION_START = date(2026, 9, 14)
PRODUCT = "SYNTHETIC_POWER"
SUPPORTED_KINDS = {"Day", "Weekend", "BOW", "Week", "BOM", "Month", "Quarter", "Season", "Year"}


def weekdays(start: date, end: date) -> list[date]:
    """Return inclusive Monday-Friday dates without a holiday calendar."""
    return [start + timedelta(days=n) for n in range((end - start).days + 1)
            if (start + timedelta(days=n)).weekday() < 5]


@lru_cache(maxsize=None)
def delivery_value(start: date, end: date) -> float:
    """Aggregate an invented daily curve using actual Berlin Base hours."""
    numerator = denominator = 0.0
    delivery_day = start
    while delivery_day < end:
        next_day = delivery_day + timedelta(days=1)
        hours = period_hours(delivery_day, next_day, "Base", "Europe/Berlin")
        annual_cycle = 16 * math.cos(2 * math.pi * (delivery_day.timetuple().tm_yday - 15) / 365.25)
        weekly_cycle = 2.5 * math.cos(2 * math.pi * delivery_day.weekday() / 7)
        price = 105 + annual_cycle + weekly_cycle + 0.008 * (delivery_day - START).days
        numerator += hours * price
        denominator += hours
        delivery_day = next_day
    return numerator / denominator


def reference_value(period: Period, publication: date, curve_index: int) -> float:
    """Shift the same delivery shape by publication date and curve identity."""
    elapsed = (publication - START).days
    level = 0.35 * elapsed + 0.8 * math.sin(elapsed / 3)
    return delivery_value(period.start, period.end) + level + 8 * curve_index


def make_book(targets: list[str], curve_index: int) -> EexBook:
    """Build synthetic reference history, including daily quotes for BOM/BOW."""
    # Keep the same absolute contracts quoted across publication dates. This
    # supplies complete fallback windows without confusing M+n after a roll.
    periods: dict[tuple, Period] = {}
    for reference in weekdays(PUBLICATION_START, END):
        for label in targets:
            period = resolve_tenor(label, reference)
            if period is not None and period.kind not in ("BOM", "BOW"):
                periods[period.key] = period
    for offset in range((date(2026, 12, 1) - date(2026, 9, 1)).days):
        day = date(2026, 9, 1) + timedelta(days=offset)
        period = Period("Day", day, day + timedelta(days=1))
        periods[period.key] = period
    rows = [(publication, period.kind, period.start,
             reference_value(period, publication, curve_index))
            for publication in weekdays(PUBLICATION_START, END)
            for period in periods.values()]
    return EexBook(pd.DataFrame(rows, columns=REQUIRED))


def prepare_output_directory() -> Path:
    """Only overwrite the named synthetic artifacts inside this repository."""
    output_root = (ROOT / "output").resolve()
    output = (output_root / "notebook_validation").resolve()
    if not output_root.is_relative_to(ROOT) or not output.is_relative_to(output_root):
        raise ValueError("The demo output folder must remain inside this repository's output folder")
    for name in ("filled_history.csv", "consistency_history.csv", "demo_manifest.json"):
        if not (output / name).resolve().is_relative_to(output):
            raise ValueError(f"Refusing output file outside the demo folder: {name}")
    existing = output / "filled_history.csv"
    if existing.exists():
        products = pd.read_csv(existing, usecols=["product"])["product"]
        if not products.eq(PRODUCT).all():
            raise ValueError(f"Refusing to overwrite a CSV with non-demo products: {existing}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def main() -> None:
    """Write genuine engine output generated entirely from synthetic inputs."""
    output = prepare_output_directory()
    base = load_config(ROOT / "config.toml")
    targets = list(dict.fromkeys([*base.tenors, *(f"M+{n}" for n in range(16))]))
    config = replace(
        base, tenors=targets, day_convention="calendar", weekend_offset=0,
        basis_mode="additive", layer_hist="off", layer_local=True,
        layer_correlation=False, layer_cross=False, layer_arbitrage=False,
        min_volume=0, max_anchor_dev=0, warmup_days=0,
        shape_mode="adjust", shape_adjust_originals=True,
        shape_original_weight=1.0, shape_max_abs_adjustment=10.0,
        shape_smoothness_weight=1.0, shape_coherence_weight=10.0,
    )
    maps = [ProductMap(PRODUCT, "fill", region, "Base", "synthetic.csv", "Base",
                       "Europe/Berlin", comment="Invented demonstration data; not a market quote",
                       region=region, unit=unit)
            for region, unit in (("DE", "EUR/MWh"), ("FR", "EUR/MWh"), ("DE", "GBP/MWh"))]
    books = {mapping.key: make_book(targets, index) for index, mapping in enumerate(maps)}
    observations = []
    anchor_adjustments = {"D+1": 5.0, "W+1": 3.0, "M+1": 2.0,
                          "M+5": -4.0, "Q+1": 8.0, "Cal+1": -2.0}
    for index, mapping in enumerate(maps):
        for reference in weekdays(START, END):
            for label, adjustment in anchor_adjustments.items():
                period = resolve_tenor(label, reference)
                own = reference_value(period, reference, index) + adjustment
                observations.append({"date": reference, "product": mapping.product,
                                     "region": mapping.region, "unit": mapping.unit,
                                     "tenor": label, "vwap": own, "volume": 100.0})
    result = CurveFiller(config, pd.DataFrame(observations), maps, books).run(START, END)
    if result.errors:
        raise RuntimeError(f"Synthetic demo failed: {result.errors}")
    filled = result.filled
    if set(filled.kind) != SUPPORTED_KINDS:
        raise RuntimeError(f"Expected every supported family; got {sorted(set(filled.kind))}")
    if filled.shape_status.eq("solver_failed").any():
        raise RuntimeError("The synthetic demo shape solver did not converge")
    filled.to_csv(output / "filled_history.csv", index=False)
    result.consistency.to_csv(output / "consistency_history.csv", index=False)
    summary = {
        "synthetic_only": True,
        "purpose": "Notebook demonstration, not evidence of real-market accuracy",
        "reference_dates": [day.isoformat() for day in weekdays(START, END)],
        "identities": [list(mapping.key) for mapping in maps],
        "targets": targets,
        "rows": len(filled),
        "rows_by_kind": {kind: int(count) for kind, count in filled.kind.value_counts().items()},
        "shape_mode": config.shape_mode,
        "shape_adjust_originals": config.shape_adjust_originals,
        "originals_note": "Only synthetic final prices may change; own_vwap retains the invented observation",
        "missing_prices": int(filled.price.isna().sum()),
        "currency_note": "DE/GBP is an invented independent curve, not converted from DE/EUR",
        "empty_tenors_note": "BOM has no remaining delivery on September 30 and is omitted on that date",
    }
    (output / "demo_manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(filled)} synthetic rows to {output / 'filled_history.csv'}")
    print(f"Reference dates: {len(summary['reference_dates'])}; curve identities: {len(maps)}")
    print("Rows per family:", summary["rows_by_kind"])
    print("Notebook OUTPUT_PATH = 'output/notebook_validation/filled_history.csv'")


if __name__ == "__main__":
    main()
