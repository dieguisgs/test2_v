"""Spread methodology: own anchors + smoothed EEX spreads.

    black   original VWAP                                   (source = own)
    blue    own_anchor + EWMA_N(EEX_target - EEX_anchor)     (source = eex_spread)
    orange  EWMA_N(EEX_target), when no own anchor exists   (source = eex_ewma)
            or, with no_anchor = eex_level, the latest allowed
            EEX price without smoothing                     (source = eex_level)

Spreads are taken between absolute delivery contracts (for example Nov-26
minus Oct-26), never between rolling relative labels, so a month roll
cannot mix different contracts inside one window. Smoothing a direct
anchor-to-target spread equals chaining smoothed consecutive spreads
(M1->M2->M3...) whenever every intermediate contract is quoted, because
the weighted mean is linear; the direct pair is used because it needs only
the two contracts involved.

Only EEX publications allowed by the reference date's cutoff (eex.offset_days)
enter a window. The method learns nothing from previous outputs.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

from vwaps.io_eex import EexBook
from vwaps.pricer import Pricer
from vwaps.tenors import Period


@dataclass
class Anchor:
    tenor: str
    period: Period
    own: float
    volume: float
    ltm: float  # log(1 + days to the middle of delivery)


def log_ttm(p: Period, ref: date) -> float:
    mid = (p.start - ref).days + (p.end - p.start).days / 2.0
    return math.log1p(max(mid, 0.5))


@dataclass(frozen=True)
class Smoothed:
    value: float
    observations: list[dict]

    @property
    def n_obs(self) -> int:
        return len(self.observations)


class EexWindow:
    """Finite, causal window of EEX publications for one curve."""

    def __init__(self, book: EexBook, hours, window: int, weighting: str, halflife: float,
                 min_obs: int, max_stale_days: int | None, offset_days: int):
        self.book = book
        self.hours = hours
        self.window = window
        self.weighting = weighting
        self.halflife = halflife
        self.min_obs = min_obs
        self.max_stale_days = max_stale_days
        self.offset_days = offset_days
        self._dates = tuple(book.trade_dates)
        self._pricers: dict[date, Pricer] = {}
        self._prices: dict[tuple, float | None] = {}

    def days(self, reference_date: date) -> tuple[date, ...]:
        """Latest `window` publications allowed for the reference date (oldest first)."""
        asof = self.book.available_asof(reference_date, self.max_stale_days, self.offset_days)
        if asof is None:
            return ()
        end = bisect_right(self._dates, asof)
        return self._dates[max(0, end - self.window):end]

    def price(self, publication: date, period: Period) -> float | None:
        key = (publication, period.kind, period.start, period.end)
        if key not in self._prices:
            if publication not in self._pricers:
                quotes, asof = self.book.quotes(publication, 0)
                self._pricers[publication] = Pricer(quotes if asof == publication else {}, self.hours)
            quote = self._pricers[publication].price(period.start, period.end, kind=period.kind)
            self._prices[key] = quote[0] if quote is not None and math.isfinite(quote[0]) else None
        return self._prices[key]

    def _weights(self, days: tuple[date, ...]) -> dict[date, float]:
        n = len(days)
        if self.weighting == "simple":
            return {d: 1.0 for d in days}
        # age 0 = latest publication of the window
        return {d: 2.0 ** (-(n - 1 - i) / self.halflife) for i, d in enumerate(days)}

    def _smooth(self, days: tuple[date, ...], values: dict[date, float]) -> Smoothed | None:
        if len(values) < self.min_obs or not values:
            return None
        raw = self._weights(days)
        total = math.fsum(raw[d] for d in values)
        obs = [{"trade_date": d.isoformat(), "value": values[d], "weight": raw[d] / total}
               for d in days if d in values]
        value = math.fsum(o["value"] * o["weight"] for o in obs)
        return Smoothed(value, obs) if math.isfinite(value) else None

    def outright(self, reference_date: date, period: Period) -> Smoothed | None:
        days = self.days(reference_date)
        values = {d: p for d in days if (p := self.price(d, period)) is not None}
        return self._smooth(days, values)

    def level(self, reference_date: date, period: Period) -> Smoothed | None:
        """Unsmoothed price on the latest allowed publication (no_anchor = eex_level)."""
        days = self.days(reference_date)
        if not days:
            return None
        p = self.price(days[-1], period)
        if p is None:
            return None
        return Smoothed(p, [{"trade_date": days[-1].isoformat(), "value": p, "weight": 1.0}])

    def spread(self, reference_date: date, anchor: Period, target: Period) -> Smoothed | None:
        """EWMA of (target - anchor) over days where EEX prices both contracts."""
        days = self.days(reference_date)
        values = {}
        for d in days:
            a, t = self.price(d, anchor), self.price(d, target)
            if a is not None and t is not None:
                values[d] = t - a
        return self._smooth(days, values)


def spread_estimate(window: EexWindow, day: date, target: Period, anchors: list[Anchor],
                    rule: str, scope: str) -> dict | None:
    """Blue price from own anchors. Returns the price and its audit trail."""
    eligible = [a for a in anchors if a.period.key != target.key
                and (scope == "any" or a.period.kind == target.kind)]
    if not eligible:
        return None
    lt = log_ttm(target, day)

    def one(a: Anchor) -> dict | None:
        s = window.spread(day, a.period, target)
        if s is None:
            return None
        return {"anchor": a, "spread": s, "price": a.own + s.value, "distance": abs(lt - a.ltm)}

    ordered = sorted(eligible, key=lambda a: (abs(lt - a.ltm), a.period.start))
    if rule == "nearest":
        for a in ordered:
            est = one(a)
            if est is not None:
                return {"price": est["price"], "parts": [est], "rule": "nearest"}
        return None
    # interpolate: closest usable anchor on each side of the target, weighted
    # by inverse distance in log time to delivery
    left = [a for a in ordered if a.period.start < target.start]
    right = [a for a in ordered if a.period.start > target.start]
    parts = []
    for side in (left, right):
        for a in side:
            est = one(a)
            if est is not None:
                parts.append(est)
                break
    if not parts:
        return None
    if len(parts) == 1:
        return {"price": parts[0]["price"], "parts": parts, "rule": "interpolate_one_side"}
    dl, dr = parts[0]["distance"], parts[1]["distance"]
    wl = 0.5 if dl + dr == 0 else dr / (dl + dr)
    for p, w in zip(parts, (wl, 1.0 - wl)):
        p["weight"] = w
    return {"price": wl * parts[0]["price"] + (1 - wl) * parts[1]["price"], "parts": parts,
            "rule": "interpolate"}


def trace_parts(parts: list[dict]) -> list[dict]:
    return [{"anchor_tenor": p["anchor"].tenor, "anchor_price": p["anchor"].own,
             "anchor_period": f"{p['anchor'].period.start}..{p['anchor'].period.end}",
             "smoothed_spread": p["spread"].value, "weight": p.get("weight", 1.0),
             "observations": p["spread"].observations} for p in parts]
