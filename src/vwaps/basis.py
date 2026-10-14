"""Basis = own - EEX (or own/EEX - 1 in ratio mode).

Filling with "anchor + EEX time spread" is equivalent to adding the anchor's
basis to EEX: Own_M2 = Own_M1 + (EX_M2 - EX_M1) = EX_M2 + (Own_M1 - EX_M1).
Estimate that basis for each target contract:
  - local: average basis of today's anchors, weighted by volume,
    product type and curve distance (log of days until delivery).
  - historical: EWMA of previous days' basis by product type.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from vwaps.tenors import Period


def to_basis(own: float, eex: float, mode: str) -> float:
    return own - eex if mode == "additive" else own / eex - 1.0


def apply_basis(eex: float, b: float, mode: str) -> float:
    return eex + b if mode == "additive" else eex * (1.0 + b)


def log_ttm(p: Period, ref: date) -> float:
    mid = (p.start - ref).days + (p.end - p.start).days / 2.0
    return math.log1p(max(mid, 0.5))


@dataclass
class Anchor:
    tenor: str
    period: Period
    own: float
    eex: float
    volume: float
    ltm: float

    @property
    def vol_weight(self) -> float:
        return 1.0 if not self.volume == self.volume or self.volume <= 0 else math.log1p(self.volume)


KindFactor = Callable[[Period, Period], float]


def distance_kind_factor(other_kind_weight: float) -> KindFactor:
    return lambda anchor, target: 1.0 if anchor.kind == target.kind else other_kind_weight


def local_basis(
    target: Period, ref: date, anchors: list[Anchor], mode: str, tau: float,
    kind_factor: KindFactor,
) -> tuple[float | None, float, list[str]]:
    """Return (basis, total weight W, most influential anchors)."""
    lt = log_ttm(target, ref)
    num = den = 0.0
    used: list[tuple[float, str]] = []
    for a in anchors:
        kw = kind_factor(a.period, target)
        w = a.vol_weight * kw * math.exp(-abs(lt - a.ltm) / tau)
        if w <= 0:
            continue
        num += w * to_basis(a.own, a.eex, mode)
        den += w
        used.append((w, a.tenor))
    if den == 0:
        return None, 0.0, []
    used.sort(reverse=True)
    return num / den, den, [t for _, t in used[:3]]


def daily_basis(anchors: list[Anchor], mode: str) -> dict[str, float]:
    """Daily basis by type, group and overall, using volume-based weights."""
    acc: dict[str, list[float]] = {}
    for a in anchors:
        b = to_basis(a.own, a.eex, mode)
        for k in (a.period.kind, a.period.group, "all"):
            s = acc.setdefault(k, [0.0, 0.0])
            s[0] += a.vol_weight * b
            s[1] += a.vol_weight
    return {k: num / den for k, (num, den) in acc.items()}


class BasisHistory:
    """EWMA of daily basis by product type (Month, Quarter...), with group
    (short/month/quarter/long) and overall fallbacks."""

    def __init__(self, mode: str, halflife_days: float):
        self.mode = mode
        self.alpha = 1.0 - 0.5 ** (1.0 / max(halflife_days, 1e-9))
        self.values: dict[str, float] = {}
        self.last_seen: dict[str, date] = {}

    def expire(self, day: date, max_age_days: int) -> None:
        for k, seen in list(self.last_seen.items()):
            if (day - seen).days > max_age_days:
                self.values.pop(k, None)
                del self.last_seen[k]

    def get(self, p: Period) -> float | None:
        for k in (p.kind, p.group, "all"):
            if k in self.values:
                return self.values[k]
        return None

    def update(self, anchors: list[Anchor], day: date | None = None) -> None:
        for k, x in daily_basis(anchors, self.mode).items():
            prev = self.values.get(k)
            self.values[k] = x if prev is None else self.alpha * x + (1 - self.alpha) * prev
            if day is not None:
                self.last_seen[k] = day
