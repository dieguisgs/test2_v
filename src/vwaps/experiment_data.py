"""Prepare read-only real inputs or an entirely invented notebook demonstration."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from vwaps.config import Config, validate_config
from vwaps.hours import period_hours
from vwaps.identity import CurveKey, curve_keys
from vwaps.io_eex import EexBook, REQUIRED, load_eex_books
from vwaps.io_vwap import load_vwaps
from vwaps.log import get_logger
from vwaps.mapping import ProductMap, load_mapping
from vwaps.tenors import resolve_tenor


@dataclass
class ExperimentDataset:
    config: Config
    vwaps: pd.DataFrame
    maps: list[ProductMap]
    books: dict[CurveKey, EexBook | None]
    start: date
    end: date
    label: str


def load_experiment_dataset(cfg: Config, *, vwap_path: str | Path | None = None) -> ExperimentDataset:
    """Read production-format files without creating mappings or writing results.

    Explicit relative VWAP overrides follow Config.resolve (the TOML directory).
    The notebook resolves its own path overrides against the project first.
    Keep helpers for cross-product estimation, but derive the default date
    range only from finite, recognized observations in active fill curves.
    """
    validate_config(cfg)
    frame = load_vwaps(cfg.vwap_input if vwap_path is None else vwap_path, cfg)
    maps = load_mapping(cfg)
    active = {mapping.key for mapping in maps if mapping.active}
    fill = {mapping.key for mapping in maps if mapping.active and mapping.use == "fill"}
    if not fill:
        raise ValueError("No active fill mappings; review use=fill and eex_file in the product mapping")
    keys = curve_keys(frame)
    excluded = sorted(set(keys) - active)
    if excluded:
        get_logger().warning("Excluded unmapped, off or unassigned input curves: %s", excluded)
    frame = frame.loc[keys.isin(active)].reset_index(drop=True)
    eligible = frame.loc[curve_keys(frame).isin(fill) & np.isfinite(frame["vwap"])]
    days = [
        day for day, tenor in zip(eligible["date"], eligible["tenor"])
        if resolve_tenor(tenor, day, cfg.day_convention, cfg.weekend_offset) is not None
    ]
    if not days:
        raise ValueError("No finite own observations with supported tenors in active fill mappings")
    return ExperimentDataset(cfg, frame, maps, load_eex_books(cfg, maps), min(days), max(days), "real")


def make_demo_dataset(cfg: Config, *, seed: int = 7, periods: int = 30) -> ExperimentDataset:
    """Invent two power curves and daily reference quotes, with no external files.

    The DE curve has an additive own/reference basis; FR has a proportional
    basis. Both have slowly changing adjustments and independent observation
    noise. This deliberately favors those model families and is a workflow
    demonstration, not a neutral accuracy benchmark. Weekdays are observation
    dates, without a holiday calendar. All reference prices are invented.

    Quarters and months share an underlying daily delivery curve weighted by
    Berlin Base hours. Each absolute contract has earlier quotes across tenor
    rolls, so rolling relative labels do not substitute for contract history.
    """
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if isinstance(periods, bool) or not isinstance(periods, int) or periods < 3:
        raise ValueError("periods must be an integer >= 3")
    demo_cfg = replace(
        cfg, tenors=["D+1", "M+1", "M+2", "M+3", "Q+1", "Q+2"],
        day_convention="calendar", weekend_offset=0, min_volume=0.0,
        max_anchor_dev=0.0, warmup_days=0, layer_local=True,
        layer_hist="auto", layer_correlation=False, layer_cross=False,
        layer_arbitrage=False, shape_mode="off", shape_adjust_originals=False,
        basis_mode="auto", tau_log=0.5, shrink_k=1.0, other_kind_weight=0.6,
        ewma_halflife_days=10.0, hist_max_age_days=60, hist_auto_min_obs=10.0,
        ratio_eex_floor=1.0, max_ratio_deviation=1.0, max_stale_days=None,
        fallback_price_method="ewma", fallback_price_window=5,
        fallback_ewma_halflife=2.0, fallback_spread_window=9, fallback_anchor_months=2,
    )
    validate_config(demo_cfg)
    days = list(pd.bdate_range("2026-08-24", periods=periods).date)
    publication_days = list(pd.bdate_range(days[0] - timedelta(days=21), days[-1]).date)
    maps = [
        ProductMap("SYNTHETIC_POWER", "fill", area, "Base", f"synthetic/{area}.csv",
                   "Base", "Europe/Berlin", comment="Entirely invented notebook example",
                   region=area, unit="EUR/MWh")
        for area in ("DE", "FR")
    ]
    contracts = {}
    for day in publication_days:
        for tenor in demo_cfg.tenors:
            period = resolve_tenor(tenor, day)
            contracts[period.key] = period

    @lru_cache(maxsize=None)
    def delivery_level(start: date, end: date) -> float:
        numerator = denominator = 0.0
        day = start
        while day < end:
            next_day = day + timedelta(days=1)
            hours = period_hours(day, next_day, "Base", "Europe/Berlin")
            value = 90 + 22 * math.cos(2 * math.pi * (day.timetuple().tm_yday - 15) / 365.25)
            value += 0.01 * (day - days[0]).days
            numerator += value * hours
            denominator += hours
            day = next_day
        return numerator / denominator

    def reference_price(period, publication, curve_index):
        elapsed = (publication - days[0]).days
        return delivery_level(period.start, period.end) + 8 * curve_index + 0.12 * elapsed + math.sin(elapsed / 5)

    rng = np.random.default_rng(seed)
    rows, books = [], {}
    for curve_index, mapping in enumerate(maps):
        settlements = [
            (publication, period.kind, period.start, reference_price(period, publication, curve_index))
            for publication in publication_days for period in contracts.values()
        ]
        books[mapping.key] = EexBook(pd.DataFrame(settlements, columns=REQUIRED))
        for index, day in enumerate(days):
            basis = 7 + 1.5 * math.sin(index / 6)
            for tenor in demo_cfg.tenors:
                # Some observed inputs are absent before leave-one-out masking.
                if tenor == "M+2" and index % 5 == 0:
                    continue
                period = resolve_tenor(tenor, day)
                reference = reference_price(period, day, curve_index)
                adjustment = basis if curve_index == 0 else reference * basis / 100
                rows.append({
                    "date": day, "product": mapping.product, "region": mapping.region,
                    "unit": mapping.unit, "tenor": tenor,
                    "vwap": reference + adjustment + float(rng.normal(0, 0.4)),
                    "volume": float(rng.integers(20, 200)),
                })
    return ExperimentDataset(demo_cfg, pd.DataFrame(rows), maps, books, days[0], days[-1],
                             f"synthetic_seed_{seed}_dates_{periods}")
