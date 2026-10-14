"""Check filled-curve consistency: Q against its months, Season against its
quarters, and Cal against its quarters. Report discrepancies without
enforcing consistency: VWAPs need not be arbitrage-free."""

from __future__ import annotations

import pandas as pd

from vwaps.identity import CurveKey, curve_keys
from vwaps.pricer import Pricer

FINER = {
    "Quarter": ("Month",),
    "Season": ("Quarter",),
    "Year": ("Quarter",),
}


def check_day(rows: list[dict], hfn) -> list[dict]:
    out = []
    if not rows:
        return out
    curves: dict[CurveKey, list[dict]] = {}
    for key, row in zip(curve_keys(pd.DataFrame(rows)), rows):
        if row["source"] != "missing":
            curves.setdefault(key, []).append(row)
    for key, priced in curves.items():
        for kind, finer in FINER.items():
            parts = {(r["delivery_start"], r["delivery_end"]): r["price"]
                     for r in priced if r["kind"] in finer}
            if not parts:
                continue
            pr = Pricer(parts, hfn)
            for r in priced:
                if r["kind"] != kind:
                    continue
                res = pr.price(r["delivery_start"], r["delivery_end"])
                if res is None or res[1] != "strip":
                    continue
                out.append({
                    "reference_date": r["reference_date"], "product": key[0],
                    "region": key[1], "unit": key[2],
                    "tenor": r["tenor"], "period": r["period"], "price": r["price"],
                    "source": r["source"], "from_parts": res[0], "deviation": r["price"] - res[0],
                })
    return out
