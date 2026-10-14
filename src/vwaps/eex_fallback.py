"""Causal EEX price averages and monthly spread reconstruction.

Only publication snapshots enter these calculations. Own observations and
previous estimates never enter this fallback. Every requested window must
be complete on the book's latest publication dates; missing contracts are
not forward-filled or replaced by older observations.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

from vwaps.config import Config, validate_fallback_config
from vwaps.io_eex import EexBook
from vwaps.pricer import Pricer
from vwaps.tenors import Period, add_months


@dataclass(frozen=True)
class FallbackResult:
    price: float
    method: str
    trace: dict


def _period_trace(period: Period) -> dict:
    return {"kind": period.kind, "delivery_start": period.start.isoformat(),
            "delivery_end": period.end.isoformat()}


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("EEX fallback arithmetic produced a nonfinite price or spread")
    return value


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    scale = max(abs(value) for value in values)
    if scale == 0:
        return 0.0
    return _finite(scale * math.fsum((value / scale) * weight
                                   for value, weight in zip(values, weights)))


class EexFallback:
    """Average one book without changing its prices or learning from outputs.

    EWMA is a finite, normalized exponential average. Its half-life is in
    publication observations, not elapsed calendar days. Monthly targets
    beyond the configured anchor months use raw same-date monthly spreads;
    the latest spread observation is not replaced by a smoothed spread.
    """

    def __init__(self, book: EexBook, hours, cfg: Config):
        validate_fallback_config(cfg)
        self.book = book
        self.hours = hours
        self.price_method = cfg.fallback_price_method
        self.price_window = cfg.fallback_price_window
        self.halflife = cfg.fallback_ewma_halflife
        self.spread_window = cfg.fallback_spread_window
        self.anchor_months = cfg.fallback_anchor_months
        self.max_stale_days = cfg.max_stale_days
        self.eex_offset_days = getattr(cfg, "eex_offset_days", 0)
        self._dates = tuple(book.trade_dates)
        self._pricers: dict[date, Pricer] = {}
        self._quotes: dict[tuple, tuple[float, str] | None] = {}
        self._averages: dict[tuple, dict | None] = {}
        self._spreads: dict[tuple, dict | None] = {}
        self._results: dict[tuple, FallbackResult | None] = {}

    def _window(self, asof: date, size: int) -> tuple[date, ...] | None:
        end = bisect_right(self._dates, asof)
        return self._dates[end - size:end] if end >= size else None

    def _quote(self, publication: date, period: Period) -> tuple[float, str] | None:
        key = (publication, period.kind, period.start, period.end)
        if key not in self._quotes:
            if publication not in self._pricers:
                quotes, asof = self.book.quotes(publication, 0)
                if asof != publication:
                    self._quotes[key] = None
                    return None
                self._pricers[publication] = Pricer(quotes, self.hours)
            quote = self._pricers[publication].price(period.start, period.end, kind=period.kind)
            if quote is not None:
                _finite(quote[0])
            self._quotes[key] = quote
        return self._quotes[key]

    def _average(self, asof: date, period: Period) -> dict | None:
        key = (asof, period.kind, period.start, period.end)
        if key in self._averages:
            return self._averages[key]
        days = self._window(asof, self.price_window)
        if days is None:
            self._averages[key] = None
            return None
        quotes = [self._quote(day, period) for day in days]
        if any(quote is None for quote in quotes):
            self._averages[key] = None
            return None
        raw_weights = ([1.0] * len(days) if self.price_method == "simple" else
                       [2.0 ** (-(len(days) - 1 - i) / self.halflife) for i in range(len(days))])
        total = math.fsum(raw_weights)
        weights = [weight / total for weight in raw_weights]
        value = _weighted_mean([quote[0] for quote in quotes], weights)
        result = {"period": _period_trace(period), "value": value,
                  "observations": [
                      {"trade_date": day.isoformat(), "price": quote[0],
                       "eex_method": quote[1], "weight": weight}
                      for day, quote, weight in zip(days, quotes, weights)]}
        self._averages[key] = result
        return result

    def _spread(self, asof: date, previous: Period, current: Period) -> dict | None:
        key = (asof, previous.start, current.start)
        if key in self._spreads:
            return self._spreads[key]
        days = self._window(asof, self.spread_window)
        if days is None:
            self._spreads[key] = None
            return None
        observations = []
        for day in days:
            left, right = self._quote(day, previous), self._quote(day, current)
            if left is None or right is None:
                self._spreads[key] = None
                return None
            observations.append({
                "trade_date": day.isoformat(), "from_price": left[0], "to_price": right[0],
                "from_eex_method": left[1], "to_eex_method": right[1],
                "spread": _finite(right[0] - left[0]), "weight": 1.0 / len(days),
            })
        result = {"from_period": _period_trace(previous), "to_period": _period_trace(current),
                  "mean_spread": _weighted_mean([row["spread"] for row in observations],
                                                 [row["weight"] for row in observations]),
                  "observations": observations}
        self._spreads[key] = result
        return result

    def price(self, day: date, period: Period) -> FallbackResult | None:
        """Return a complete causal estimate, or None when evidence is missing.

        Anchor months are counted from the calendar month containing day.
        Non-monthly periods are averaged directly using each publication's
        exact/strip/residual EEX price under the target's delivery calendar.
        """
        key = (day, period.kind, period.start, period.end)
        if key not in self._results:
            self._results[key] = self._price(day, period)
        return self._results[key]

    def _price(self, day: date, period: Period) -> FallbackResult | None:
        cutoff = self.book.cutoff_date(day, self.eex_offset_days)
        asof = self.book.available_asof(day, self.max_stale_days, self.eex_offset_days)
        if asof is None:
            return None
        first = day.replace(day=1)
        offset = (period.start.year - first.year) * 12 + period.start.month - first.month
        cascade = period.kind == "Month" and offset >= self.anchor_months
        anchor_start = add_months(first, self.anchor_months - 1)
        anchor = Period("Month", anchor_start, add_months(anchor_start, 1)) if cascade else period
        average = self._average(asof, anchor)
        if average is None:
            return None
        value, steps = average["value"], []
        previous = anchor
        if cascade:
            for i in range(self.anchor_months, offset + 1):
                start = add_months(first, i)
                current = Period("Month", start, add_months(start, 1))
                spread = self._spread(asof, previous, current)
                if spread is None:
                    return None
                after = _finite(value + spread["mean_spread"])
                steps.append({**spread, "price_before": value, "price_after": after})
                value, previous = after, current
        trace = {
            "reference_date": day.isoformat(), "eex_asof": asof.isoformat(),
            "eex_cutoff_date": cutoff.isoformat(), "eex_offset_days": self.eex_offset_days,
            "target": _period_trace(period), "price_method": self.price_method,
            "price_window": self.price_window, "ewma_halflife": self.halflife,
            "spread_window": self.spread_window, "anchor_months": self.anchor_months,
            "spread_input": "raw_same_publication_prices", "price_average": average,
            "spread_steps": steps,
        }
        method = f"eex_month_cascade_{self.price_method}" if cascade else f"eex_price_{self.price_method}"
        return FallbackResult(value, method, trace)
