"""Generate synthetic VWAPs from EEX to test without the real input file.

truth(day, series, contract) = EEX * (1 + b(day, series, group) + noise)  (ratio mode)
                             = EEX + b(day, series, group) + noise      (additive mode)

  b = 0.7*market(day) + 0.5*group(day, group) + 0.5*own(day, series, group)  (scaled)
    market  common factor (market movement between the VWAP and the close)
    group   common to all series in that group (Days, Months...)
    own     specific to each series
  Each factor follows an AR(1) process with memory across days.
  noise    independent for each contract (unpredictable by any method).

This creates co-movement between groups and areas that the methods can use.
Some series also have days without any observed VWAPs.

The two outputs are VWAP observations using the real input schema and complete
ground truth, which is used to measure errors in filled prices.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from vwaps.config import Config
from vwaps.hours import hours_fn
from vwaps.identity import CurveKey
from vwaps.io_eex import EexBook
from vwaps.mapping import ProductMap
from vwaps.pricer import Pricer
from vwaps.tenors import Key, resolve_tenor

OBS_PROB = {"Day": 0.85, "Weekend": 0.5, "BOW": 0.1, "Week": 0.6, "BOM": 0.3,
            "Month": 0.7, "Quarter": 0.45, "Season": 0.15, "Year": 0.6}
PERIODICITY = {"Day": "Daily", "Weekend": "Weekend", "BOW": "BOW", "Week": "Weekly",
               "BOM": "BOM", "Month": "Monthly", "Quarter": "Quarterly", "Season": "Seasonal",
               "Year": "Annual"}
EXTRA = ["D+4", "W+2", "WE+4"]
GROUPS = ("short", "month", "quarter", "long")
LOAD_MARKET, LOAD_GROUP, LOAD_OWN = 0.7, 0.5, 0.5


def make_synthetic(
    cfg: Config, maps: list[ProductMap], books: dict[CurveKey | str, EexBook], start: date, end: date,
    seed: int = 7, mode: str = "ratio", phi: float = 0.85, p_empty_day: float = 0.15,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    # ratio: basis ~0.6 % and noise ~0.3 % of price | additive: 0.8 and 0.4 in the curve's unit
    sigma_basis, sigma_noise = (0.006, 0.003) if mode == "ratio" else (0.8, 0.4)
    norm = math.sqrt(LOAD_MARKET**2 + LOAD_GROUP**2 + LOAD_OWN**2)
    rng = np.random.default_rng(seed)
    shock = math.sqrt(1 - phi**2)

    def ar(x: float) -> float:
        return phi * x + shock * rng.standard_normal()

    def curve_book(m: ProductMap) -> EexBook:
        if m.key in books:
            return books[m.key]
        if not m.region and not m.unit:
            return books[m.product]
        raise KeyError(f"No EEX book for {m.label}")

    curve_books = {m.key: curve_book(m) for m in maps}
    market = 0.0
    group = {g: 0.0 for g in GROUPS}
    own = {(m.key, g): 0.0 for m in maps for g in GROUPS}
    labels = list(dict.fromkeys([*cfg.tenors, *EXTRA]))
    days = sorted({d for m in maps for d in curve_books[m.key].trade_dates
                   if start <= d <= end and d.weekday() < 5})
    vw_rows, truth_rows = [], []
    for day in days:
        market = ar(market)
        group = {g: ar(v) for g, v in group.items()}
        own = {k: ar(v) for k, v in own.items()}
        for m in maps:
            quotes, asof = curve_books[m.key].quotes(day, 0)
            if asof is None:
                continue
            pr = Pricer(quotes, hours_fn(m.hours, m.timezone))
            empty_day = rng.random() < p_empty_day
            truths: dict[Key, float] = {}
            for label in labels:
                per = resolve_tenor(label, day, cfg.day_convention, cfg.weekend_offset)
                if per is None:
                    continue
                r = pr.price(per.start, per.end, kind=per.kind)
                if r is None:
                    continue
                is_new_period = per.key not in truths
                if is_new_period:
                    g = per.group
                    b = sigma_basis * (LOAD_MARKET * market + LOAD_GROUP * group[g]
                                       + LOAD_OWN * own[(m.key, g)]) / norm
                    eps = sigma_noise * rng.standard_normal()
                    truths[per.key] = (round(r[0] * (1 + b + eps), 3) if mode == "ratio"
                                       else round(r[0] + b + eps, 3))
                true = truths[per.key]
                truth_rows.append({"date": day, "product": m.product,
                                   "region": m.region, "unit": m.unit, "tenor": label,
                                   "period": per.name, "delivery_start": per.start,
                                   "delivery_end": per.end, "truth": true, "eex": r[0]})
                # Aliases (e.g. D+1 = BOM on the month's penultimate day)
                # share their truth and a single opportunity to be observed.
                if not is_new_period:
                    continue
                n = int("".join(ch for ch in label if ch.isdigit()) or 0)
                p = OBS_PROB[per.kind] * (0.85 ** max(n - 1, 0))
                if not empty_day and rng.random() < p:
                    vol = int(max(2, round(rng.lognormal(4.0, 1.2))))
                    vw_rows.append({
                        "reference_date": day.strftime("%d/%m/%Y"), "weekday": day.strftime("%A"),
                        "product": m.product, "country": m.area, "region": m.region,
                        "classification": f"{m.profile} load", "unit": m.unit,
                        "periodicity_2": PERIODICITY[per.kind], "tenor2": label,
                        "vwap": true, "total_volume": vol, "n_trades": max(1, vol // 25),
                    })
    return pd.DataFrame(vw_rows), pd.DataFrame(truth_rows)
