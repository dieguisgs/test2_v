"""Co-movement of "surprises" (enhancements 1 and 2).

surprise(day, key) = today's basis - historical basis (EWMA through yesterday)

This measures how far today's VWAPs depart from their usual relationship
with EEX. When two products or contract types depart together, one can
inform the other's adjustment. Use centered moments and exponential weights.
"""

from __future__ import annotations

import math
from datetime import date


class EWCov:
    def __init__(self, halflife_days: float):
        self.lam = 0.5 ** (1.0 / max(halflife_days, 1e-9))
        self.sxy = self.sxx = self.syy = 0.0
        self.mean_x = self.mean_y = 0.0
        self.n = 0.0  # Effective number of overlapping observation days.
        self.last_day: date | None = None

    def update(self, x: float, y: float, day: date | None = None) -> None:
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("EWCov surprises must be finite")
        lam = self.lam
        if day is not None and self.last_day is not None:
            if day < self.last_day:
                raise ValueError("EWCov requires dates in chronological order")
            lam = self.lam ** (day - self.last_day).days
        # Weighted Welford avoids subtracting E[x^2] - E[x]^2 when means
        # are large relative to the variation between observations.
        old_n = lam * self.n
        n = old_n + 1.0
        if old_n == 0.0:
            self.mean_x, self.mean_y = x, y
            self.sxy = self.sxx = self.syy = 0.0
            self.n = n
            if day is not None:
                self.last_day = day
            return
        dx, dy = x - self.mean_x, y - self.mean_y
        weight = old_n / n
        mean_x, mean_y = self.mean_x + dx / n, self.mean_y + dy / n
        sxx = max(0.0, lam * self.sxx + weight * dx * dx)
        syy = max(0.0, lam * self.syy + weight * dy * dy)
        sxy = lam * self.sxy + weight * dx * dy
        if not all(math.isfinite(v) for v in (mean_x, mean_y, sxx, syy, sxy)):
            raise ValueError("Surprises exceed the numerical range of EWCov")
        self.mean_x, self.mean_y = mean_x, mean_y
        self.sxx, self.syy, self.sxy, self.n = sxx, syy, sxy, n
        if day is not None:
            self.last_day = day

    @property
    def corr(self) -> float | None:
        if self.sxx <= 0.0 or self.syy <= 0.0:
            return None
        corr = (self.sxy / math.sqrt(self.sxx)) / math.sqrt(self.syy)
        return max(-1.0, min(1.0, corr)) if math.isfinite(corr) else None

    @property
    def beta(self) -> float | None:
        """Slope of y on x: the change in y for each unit change in x."""
        if self.sxx <= 0.0 or self.syy <= 0.0:
            return None
        beta = self.sxy / self.sxx
        return beta if math.isfinite(beta) else None
