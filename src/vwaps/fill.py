"""Curve filling engine shared by `daily` and `refill`: a single day is a
one-day refill with previous days replayed as warmup.

Each series is one mapped (product, region, unit) curve. All series are
processed together, day by day, to share information (enhancement 2).

Cascade per (day, curve identity, target tenor); see ALGORITMO.md for details.
Each layer is enabled or disabled in config.toml [layers]:
  own        Original VWAP; min_volume controls anchor eligibility.
  eex+local  EEX adjusted with today's anchors                    [layers] local
  eex+cross  EEX adjusted with history and other products' shocks [layers] cross
  eex+hist   EEX adjusted with history (no anchors today)         [layers] hist
  eex+smooth EEX price averaging and monthly spread reconstruction
  arbitrage  Without an EEX estimate: use available contracts     [layers] arbitrage
  missing    No estimate is available.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from itertools import combinations

import pandas as pd

from vwaps.basis import (
    Anchor, BasisHistory, KindFactor, apply_basis, daily_basis, distance_kind_factor,
    local_basis, log_ttm,
)
from vwaps.comove import EWCov
from vwaps.config import Config, validate_config
from vwaps.eex_fallback import EexFallback
from vwaps.consistency import check_day
from vwaps.hours import hours_fn
from vwaps.identity import CurveKey, curve_keys
from vwaps.io_eex import EexBook
from vwaps.log import get_logger
from vwaps.mapping import ProductMap
from vwaps.pricer import Pricer
from vwaps.product_config import configuration_context_records, configuration_record, effective_config
from vwaps.shape import apply_shape
from vwaps.tenors import Key, Period, resolve_tenor

MODES = ("additive", "ratio")
GROUPS = ("short", "month", "quarter", "long")
# Equal delivery intervals can have both full-contract and residual labels.
# Their evidence kind must not depend on the original CSV row order.
OWN_KIND_ORDER = {kind: index for index, kind in enumerate((
    "Day", "Weekend", "Week", "Month", "Quarter", "Season", "Year", "BOW", "BOM",
))}


@dataclass
class OwnQuote:
    period: Period
    tenors: list[str]
    vwap: float
    volume: float


class HistSkill:
    """For layers.hist = "auto", compare historical adjustments with raw EEX
    on days WITH anchors, using each anchor as the observed price.
    Track exponentially weighted mean errors by group (Days, Months...)."""

    def __init__(self, halflife_days: float):
        self.lam = 0.5 ** (1.0 / max(halflife_days, 1e-9))
        self.stats: dict[str, list[float]] = {}  # group -> [err_hist, err_eex, n]

    def update(self, errors: dict[str, list[tuple[float, float]]]) -> None:
        for g, pairs in errors.items():
            eh = sum(abs(a) for a, _ in pairs) / len(pairs)
            ee = sum(abs(b) for _, b in pairs) / len(pairs)
            s = self.stats.setdefault(g, [0.0, 0.0, 0.0])
            s[0] = self.lam * s[0] + eh
            s[1] = self.lam * s[1] + ee
            s[2] = self.lam * s[2] + 1.0

    def use_hist(self, group: str, min_obs: float) -> bool:
        s = self.stats.get(group)
        if s is None or s[2] < min_obs:
            return True  # Not enough evidence yet: use history.
        return s[0] <= s[1]


@dataclass
class Series:
    m: ProductMap
    book: EexBook | None
    own_by_day: dict
    hfn: object
    hist: dict[str, BasisHistory]
    skill: dict[str, HistSkill]
    gcov: dict[tuple[str, str, str], EWCov] = field(default_factory=dict)  # (mode, group, group)
    fallback: EexFallback | None = None

    @property
    def key(self) -> CurveKey:
        return self.m.key

    @property
    def name(self) -> str:
        return self.m.label

    @property
    def output(self) -> bool:
        return self.m.use == "fill"


@dataclass
class DayPrep:
    asof: date | None
    eex: Pricer
    own: dict[Key, OwnQuote]
    anchors: list[Anchor]
    surprises: dict[str, float]  # today's basis - historical basis, by group and "all"
    rejected: set = field(default_factory=set)
    mode_anchors: dict[str, list[Anchor]] = field(default_factory=dict)
    evaluation_anchors: list[Anchor] = field(default_factory=list)
    mode_surprises: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class RunResult:
    filled: pd.DataFrame
    consistency: pd.DataFrame
    loo: pd.DataFrame = field(default_factory=pd.DataFrame)
    errors: list[dict] = field(default_factory=list)
    configuration_contexts: dict[str, dict] = field(default_factory=dict)


class CurveFiller:
    """Refill curves using immutable effective settings for each full identity.

    Mapping overrides apply in individual mode. Explicit ``curve_parameters``
    are final candidate overrides used by tuning, and also apply in global
    mode. The run calendar and EEX availability policy remain shared.
    Optional ``evaluation_configs`` freeze LOO truth eligibility independently
    of candidate anchor filters; they never change production predictions.
    """

    def __init__(self, cfg: Config, vwaps: pd.DataFrame, maps: list[ProductMap],
                 books: dict[CurveKey | str, EexBook | None], *,
                 curve_parameters: dict[CurveKey, dict] | None = None,
                 evaluation_configs: dict[CurveKey, Config] | None = None):
        validate_config(cfg)
        self.cfg = cfg
        self.vwaps = vwaps
        self.maps = [m for m in maps if m.active]
        self.curve_parameters = deepcopy(curve_parameters or {})
        unknown = set(self.curve_parameters) - {m.key for m in self.maps}
        if unknown:
            raise ValueError(f"Curve parameters contain unmapped or inactive curve identities: {sorted(unknown)}")
        self.curve_configs = {
            m.key: effective_config(cfg, m, self.curve_parameters.get(m.key)) for m in self.maps
        }
        self.configuration_records = {
            m.key: configuration_record(cfg, m, self.curve_parameters.get(m.key)) for m in self.maps
        }
        context_records = configuration_context_records(cfg, self.maps, self.curve_parameters)
        self.configuration_contexts = {}
        for key, context in context_records.items():
            context_id = context["configuration_context_id"]
            self.configuration_records[key]["configuration_context_id"] = context_id
            self.configuration_contexts[context_id] = context["document"]
        self.evaluation_configs = deepcopy(evaluation_configs or {})
        unknown = set(self.evaluation_configs) - {m.key for m in self.maps}
        if unknown:
            raise ValueError(f"Evaluation configurations contain inactive or unmapped curve identities: {sorted(unknown)}")
        for evaluation_cfg in self.evaluation_configs.values():
            validate_config(evaluation_cfg)
        self.books = books
        self._curve_labels = {m.key: m.label for m in self.maps}
        self.cross: dict[tuple, EWCov] = {}  # (mode, curve, other curve, group) -> co-movement

    # ------------------------------------------------------------------ run
    def _build_series(self) -> list[Series]:
        by_curve = {key: frame for key, frame in self.vwaps.groupby(curve_keys(self.vwaps), sort=False)}
        out = []
        for m in self.maps:
            cfg = self.curve_configs[m.key]
            g = by_curve.get(m.key)
            legacy_book = self.books.get(m.product) if not m.region and not m.unit else None
            out.append(Series(
                m, self.books.get(m.key, legacy_book),
                {d: x for d, x in g.groupby("date")} if g is not None else {},
                hours_fn(m.hours, m.timezone),
                {md: BasisHistory(md, cfg.ewma_halflife_days) for md in MODES},
                {md: HistSkill(cfg.ewma_halflife_days) for md in MODES},
            ))
        return out

    def run(self, start: date, end: date, loo: bool = False,
            *, output_keys: set[tuple[date, CurveKey]] | None = None) -> RunResult:
        cfg = self.cfg
        log = get_logger()
        series = self._build_series()
        self.cross = {}
        if start > end:
            raise ValueError("The start date cannot be later than the end date")
        warm = start - timedelta(days=cfg.warmup_days) if cfg.warmup_days else date.min
        EexBook.cutoff_date(start, cfg.eex_offset_days)
        log.info("Engine %s -> %s | configuration=%s | EEX offset=%d | warmup=%s",
                 start, end, cfg.configuration_mode, cfg.eex_offset_days,
                 f"{cfg.warmup_days} days (limited history)" if cfg.warmup_days else "all original history")
        if cfg.warmup_days:
            log.warning("Limited warmup: daily and a longer refill may use different history")
        days: set[date] = set()
        for s in series:
            days |= {d for d in s.own_by_day if warm <= d <= end}
            if s.book is not None:
                days |= {d for d in s.book.trade_dates if warm <= d <= end}
        d = start
        while d <= end:  # Include every weekday in the range, even without data.
            if d.weekday() < 5:
                days.add(d)
            d += timedelta(days=1)
        if output_keys is not None:
            days.update(day for day, _ in output_keys if start <= day <= end)

        no_eex: dict[str, list[date]] = {}
        filled, cons, loo_rows, errors = [], [], [], []

        def prepare(engine: CurveFiller, current_series: list[Series], day: date) -> dict:
            preps = {}
            for s in current_series:  # A curve failure does not stop the other curves.
                try:
                    preps[s.key] = engine._prep(s, day)
                except Exception:
                    errors.append({"reference_date": day, "product": s.m.product,
                                   "region": s.m.region, "unit": s.m.unit, "phase": "prepare"})
                    log.exception("%s %s: error preparing the day; skipping this curve", day, s.name)
            return preps

        trainer = None
        training_index = 0
        training_days = []
        if cfg.eex_offset_days < 0:
            # The training clock is the observation's original date h. Release
            # each h once, only when same-date EEX could be known under today's
            # cutoff. Never train own_T against an older EEX snapshot.
            trainer = CurveFiller(replace(cfg, eex_offset_days=0), self.vwaps, self.maps, self.books,
                                  curve_parameters=self.curve_parameters)
            trainer.cross = self.cross
            training_days = sorted({day for s in series for day in s.own_by_day if warm <= day <= end})
            for s in series:
                if s.book is not None:
                    # Prediction clones share this read-through cache; cloning
                    # must not rebuild smoothing windows for every output day.
                    s.fallback = EexFallback(s.book, s.hfn, self.curve_configs[s.key])
        for day in sorted(days):
            if trainer is not None and day < start:
                continue
            cutoff = EexBook.cutoff_date(day, cfg.eex_offset_days)
            if trainer is not None:
                while (training_index < len(training_days)
                       and training_days[training_index] <= cutoff and training_days[training_index] < day):
                    historical_day = training_days[training_index]
                    training_preps = prepare(trainer, series, historical_day)
                    trainer._update(series, historical_day, training_preps)
                    training_index += 1
                # Expiry for a forecast uses T, while delayed training retains
                # its own historical clock h. Skill and covariance are read-only
                # during prediction; only histories require independent copies.
                current_series = [replace(s, hist=deepcopy(s.hist)) for s in series]
            else:
                current_series = series
            preps = prepare(self, current_series, day)
            for s in current_series:
                if not s.output or day < start or s.key not in preps:
                    continue
                if output_keys is not None and (day, s.key) not in output_keys:
                    continue
                try:
                    rows = self._fill(s, day, preps)
                    curve_cfg = self.curve_configs[s.key]
                    if curve_cfg.shape_mode == "off":
                        cons += check_day(rows, s.hfn)
                    else:
                        before = [{**r, "price": r["price_before_shape"],
                                   "source": r["source_before_shape"]} for r in rows]
                        cons += [{**r, "shape_stage": "before"} for r in check_day(before, s.hfn)]
                        cons += [{**r, "shape_stage": "after"} for r in check_day(rows, s.hfn)]
                        if curve_cfg.shape_mode == "audit":
                            proposed = [{**r, "price": r["shape_proposed_price"]} for r in rows]
                            cons += [{**r, "shape_stage": "proposed"}
                                     for r in check_day(proposed, s.hfn)]
                    if loo:
                        loo_rows += self._loo(s, day, preps)
                except Exception:
                    errors.append({"reference_date": day, "product": s.m.product,
                                   "region": s.m.region, "unit": s.m.unit, "phase": "fill"})
                    log.exception("%s %s: error filling the curve; skipping this curve", day, s.name)
                    continue
                filled += rows
                log.info("%s %s | anchors=%d | EEX cutoff=%s offset=%d latest=%s | methods=%s",
                         day, s.name, len(preps[s.key].anchors), cutoff, cfg.eex_offset_days, preps[s.key].asof,
                         dict(Counter(r["estimation_method"] for r in rows)))
                asof = preps[s.key].asof
                if s.book is not None and asof is None:
                    no_eex.setdefault(s.name, []).append(day)
                elif asof is not None and asof != day:
                    age = (day - asof).days
                    lvl = log.error if age >= cfg.warn_stale_days else log.warning
                    if cfg.eex_offset_days < 0:
                        lvl("%s %s: EEX cutoff %s (offset %d); latest allowed publication %s "
                            "is %d calendar days before the reference date",
                            day, s.name, cutoff, cfg.eex_offset_days, asof, age)
                    else:
                        lvl("%s %s: EEX for %s is unavailable; using EEX from %s (%d days earlier)",
                            day, s.name, day, asof, age)
            if trainer is None:
                self._update(series, day, preps)
        for name, ds in no_eex.items():
            missing_cfg = next(self.curve_configs[s.key] for s in series if s.name == name)
            log.warning("%s: no EEX settlement within cutoff/age limits from %s to %s (%d days): "
                        "using own VWAPs (contract reconstruction enabled: %s)",
                        name, ds[0], ds[-1], len(ds), missing_cfg.layer_arbitrage)
        context_ids = {row["configuration_context_id"] for row in filled}
        context_ids.update(row["configuration_context_id"] for row in loo_rows)
        contexts = {context_id: deepcopy(self.configuration_contexts[context_id]) for context_id in sorted(context_ids)}
        return RunResult(pd.DataFrame(filled), pd.DataFrame(cons), pd.DataFrame(loo_rows), errors, contexts)

    # ------------------------------------------------------- prepare the day
    def _own_quotes(self, day: date, g: pd.DataFrame | None, hfn, *,
                    cfg: Config | None = None) -> dict[Key, OwnQuote]:
        """Aggregate equal delivery intervals independently of input row order.

        Each positive finite volume weights its price; zero or unknown volume
        uses unit weight. These effective weights are separate from reported
        volume, which sums known nonnegative values and stays unknown when all
        volumes are unknown. Invalid/negative volumes are treated as unknown.

        Equivalent aliases use a deterministic evidence kind: full contracts
        (Day, Weekend, Week, Month, Quarter, Season, Year) precede BOW and BOM.
        This prevents CSV row order from changing local weights or history.
        Original rows remain untouched; alias labels are sorted and unique.
        """
        if g is None:
            return {}
        cfg = self.cfg if cfg is None else cfg
        grouped: dict[Key, list[tuple[Period, str, float, float]]] = {}
        for tenor, vwap, vol in g[["tenor", "vwap", "volume"]].itertuples(index=False):
            per = resolve_tenor(tenor, day, cfg.day_convention, cfg.weekend_offset)
            if per is None or not math.isfinite(float(vwap)):
                continue
            # Peak BOW/BOM may share dates with WE/Day but have no delivery
            # hours. They are not aliases of the quoted contract.
            if hfn(per.start, per.end, kind=per.kind) <= 0:
                continue
            vol = float(vol)
            if not math.isfinite(vol) or vol < 0:
                vol = math.nan
            grouped.setdefault(per.key, []).append((per, str(tenor), float(vwap), vol))
        out: dict[Key, OwnQuote] = {}
        for key, observations in grouped.items():
            period = min((row[0] for row in observations),
                         key=lambda per: (OWN_KIND_ORDER[per.kind], per.kind))
            weights = [row[3] if math.isfinite(row[3]) and row[3] > 0 else 1.0
                       for row in observations]
            weight_scale = max(weights)
            weights = [weight / weight_scale for weight in weights]
            total = math.fsum(weights)
            price_scale = max(abs(row[2]) for row in observations)
            if price_scale:
                scaled_price = math.fsum((row[2] / price_scale) * (weight / total)
                                         for row, weight in zip(observations, weights))
                # A convex average cannot leave the range of its inputs.
                scaled_price = max(-1.0, min(1.0, scaled_price))
                price = price_scale * scaled_price
            else:
                price = 0.0
            known_volumes = [row[3] for row in observations if math.isfinite(row[3])]
            volume = math.fsum(known_volumes) if known_volumes else math.nan
            out[key] = OwnQuote(period, sorted({row[1] for row in observations}), price, volume)
        return out

    def _prep(self, s: Series, day: date) -> DayPrep:
        cfg = self.curve_configs[s.key]
        for hist in s.hist.values():
            hist.expire(day, cfg.hist_max_age_days)
        quotes, asof = (s.book.available_quotes(day, cfg.max_stale_days, cfg.eex_offset_days)
                       if s.book is not None else ({}, None))
        eex = Pricer(quotes, s.hfn)
        own = self._own_quotes(day, s.own_by_day.get(day), s.hfn, cfg=cfg)
        candidates, bad, rejected = [], [], set()
        for q in own.values():
            # Liquidity is observable without EEX. Apply it before reference
            # matching so reconstruction cannot bypass a configured minimum.
            if math.isfinite(q.volume) and q.volume < cfg.min_volume:
                bad.append(f"{','.join(q.tenors)} volume {q.volume:g} < {cfg.min_volume:g}")
                rejected.add(q.period.key)
                continue
            r = eex.price(q.period.start, q.period.end, kind=q.period.kind)
            if r is None or not math.isfinite(r[0]):
                continue
            deviation = abs(q.vwap - r[0]) / max(abs(r[0]), cfg.ratio_eex_floor)
            if cfg.max_anchor_dev > 0 and deviation > cfg.max_anchor_dev:
                bad.append(f"{','.join(q.tenors)} {q.vwap:g} vs EEX {r[0]:g}")
                rejected.add(q.period.key)
                continue
            candidates.append(Anchor(",".join(q.tenors), q.period, q.vwap, r[0], q.volume,
                                  log_ttm(q.period, day)))
        by_mode = {md: [a for a in candidates if self._usable_anchor(a, md, cfg=cfg)] for md in MODES}
        # Auto can use every additive anchor; each target later selects its mode.
        summary_mode = "additive" if cfg.basis_mode == "auto" else cfg.basis_mode
        anchors = by_mode[summary_mode]
        rejected.update(a.period.key for a in candidates if not self._usable_anchor(a, summary_mode, cfg=cfg))
        if bad:
            get_logger().warning("%s %s: anchors excluded by volume/deviation; originals preserved: %s",
                                 day, s.name, "; ".join(bad))
        unstable = len(candidates) - len(anchors)
        if unstable:
            get_logger().warning("%s %s: %d anchors excluded by unstable ratios; originals preserved",
                                 day, s.name, unstable)
        mode_surprises: dict[str, dict[str, float]] = {md: {} for md in MODES}
        if asof == day:
            for md in MODES:
                h = s.hist[md].values
                for k, v in daily_basis(by_mode[md], md).items():
                    if (k in GROUPS or k == "all") and k in h:
                        mode_surprises[md][k] = v - h[k]
        evaluation_candidates = candidates
        if s.key in self.evaluation_configs:
            # A candidate's liquidity/deviation filter must not remove difficult
            # observations from the exam. Tuning can freeze truth eligibility
            # independently while retaining candidate filters for predictions.
            evaluation_cfg = self.evaluation_configs[s.key]
            evaluation_candidates = []
            for q in own.values():
                if math.isfinite(q.volume) and q.volume < evaluation_cfg.min_volume:
                    continue
                r = eex.price(q.period.start, q.period.end, kind=q.period.kind)
                if r is None or not math.isfinite(r[0]):
                    continue
                deviation = abs(q.vwap - r[0]) / max(abs(r[0]), evaluation_cfg.ratio_eex_floor)
                if evaluation_cfg.max_anchor_dev > 0 and deviation > evaluation_cfg.max_anchor_dev:
                    continue
                evaluation_candidates.append(Anchor(",".join(q.tenors), q.period, q.vwap, r[0], q.volume,
                                                    log_ttm(q.period, day)))
        return DayPrep(asof, eex, own, anchors, mode_surprises[summary_mode],
                       rejected, by_mode, evaluation_candidates, mode_surprises)

    def _usable_anchor(self, a: Anchor, mode: str, *, cfg: Config | None = None) -> bool:
        cfg = self.cfg if cfg is None else cfg
        if not (math.isfinite(a.own) and math.isfinite(a.eex)):
            return False
        if mode == "additive":
            return True
        if abs(a.eex) < cfg.ratio_eex_floor:
            return False
        factor = a.own / a.eex
        return factor > 0 and abs(factor - 1) <= cfg.max_ratio_deviation

    # -------------------------------------------------- enhancements 1 and 2
    def _kind_factor(self, s: Series, use_corr: bool, mode: str) -> KindFactor:
        cfg = self.curve_configs[s.key]
        base = distance_kind_factor(cfg.other_kind_weight)
        if not use_corr:
            return base

        def f(anchor: Period, target: Period) -> float:
            if anchor.kind == target.kind or anchor.group == target.group:
                return base(anchor, target)
            c = s.gcov.get((mode, *sorted((anchor.group, target.group))))
            if c is None or c.corr is None:
                return cfg.other_kind_weight
            a = c.n / (c.n + cfg.corr_prior_obs)
            return a * max(c.corr, 0.0) + (1 - a) * cfg.other_kind_weight
        return f

    def _cross_adj(self, s: Series, per: Period, preps: dict, mode: str) -> tuple[float, list[str]] | None:
        """Today's correlated surprises in the selected mode, scaled by beta."""
        cfg = self.curve_configs[s.key]
        num = den = 0.0
        used = []
        for key, p in preps.items():
            surprises = p.mode_surprises.get(mode, {})
            if key == s.key or not surprises:
                continue
            k = per.group if per.group in surprises else "all"
            if k not in surprises:
                continue
            c = self.cross.get((mode, s.key, key, k))
            if c is None or c.n < cfg.cross_min_obs or c.corr is None or c.corr < cfg.cross_min_corr or c.beta is None:
                continue
            w = c.corr ** 2
            num += w * c.beta * surprises[k]
            den += w
            used.append(f"{self._curve_labels[key]}({c.corr:.2f})")
        return (num / den, used) if den > 0 else None

    def _hist_for(self, s: Series, per: Period, mode: str) -> float | None:
        cfg = self.curve_configs[s.key]
        if cfg.layer_hist == "off":
            return None
        if cfg.layer_hist == "auto" and not s.skill[mode].use_hist(per.group, cfg.hist_auto_min_obs):
            return None
        return s.hist[mode].get(per)

    def _select_mode(self, s: Series, per: Period, eex: float | None, p: DayPrep) -> tuple[str, str]:
        """Select a target's mode from visible anchors and usable past history."""
        cfg = self.curve_configs[s.key]
        if cfg.basis_mode != "auto":
            return cfg.basis_mode, ""
        if eex is not None and abs(eex) < cfg.ratio_eex_floor:
            return "additive", "auto_additive_low_eex"
        ratio_anchors = p.mode_anchors.get("ratio", [])
        additive_anchors = p.mode_anchors.get("additive", [])
        if not ratio_anchors and additive_anchors:
            return "additive", "auto_additive_no_ratio_anchors"
        if not ratio_anchors and not additive_anchors:
            if self._hist_for(s, per, "ratio") is None and self._hist_for(s, per, "additive") is not None:
                return "additive", "auto_additive_history_only"
        return "ratio", ""

    # ------------------------------------------------------------ fill
    def _fill(self, s: Series, day: date, preps: dict, labels: list[str] | None = None) -> list[dict]:
        cfg = self.curve_configs[s.key]
        p = preps[s.key]
        kind_factors = {md: self._kind_factor(s, cfg.layer_correlation, md) for md in MODES}
        rows, pending = [], []
        requested = list(labels if labels is not None else cfg.tenors)
        if cfg.shape_mode != "off":
            # Include observed periods as shape context, including their aliases.
            # In LOO p.own already excludes the entire held-out delivery period.
            requested.extend(label for q in p.own.values()
                             if q.period.kind in ("Month", "Quarter", "Year")
                             for label in q.tenors)
        for label in dict.fromkeys(requested):
            per = resolve_tenor(label, day, cfg.day_convention, cfg.weekend_offset)
            if per is None:
                continue
            delivery_hours = s.hfn(per.start, per.end, kind=per.kind)
            q = p.own.get(per.key) if delivery_hours > 0 else None
            r = p.eex.price(per.start, per.end, kind=per.kind)
            mode, auto_reason = self._select_mode(s, per, r[0] if r is not None else None, p)
            row = {
                **self.configuration_records[s.key],
                "reference_date": day, "product": s.m.product,
                "region": s.m.region, "unit": s.m.unit, "area": s.m.area,
                "profile": s.m.profile, "tenor": label, "kind": per.kind, "period": per.name,
                "delivery_start": per.start, "delivery_end": per.end,
                "hours": delivery_hours,
                "price": math.nan, "source": "missing", "confidence": 0.0,
                "data_origin": "missing", "estimation_method": "unavailable", "basis_mode": mode,
                "configured_basis_mode": cfg.basis_mode,
                "own_vwap": q.vwap if q else math.nan, "own_volume": q.volume if q else math.nan,
                "eex_settle": r[0] if r else math.nan, "eex_method": r[1] if r else "",
                "eex_asof": p.asof, "eex_cutoff_date": EexBook.cutoff_date(day, cfg.eex_offset_days),
                "eex_offset_days": cfg.eex_offset_days, "basis": math.nan, "basis_local": math.nan,
                "basis_hist": math.nan, "cross_adj": math.nan, "local_weight": math.nan,
                "anchors": "", "cross_from": "",
                "eex_fallback_trace": "",
                "flag": "anchor_excluded" if per.key in p.rejected else "",
            }
            if delivery_hours <= 0:
                row["flag"] = ";".join(filter(None, [row["flag"], "zero_delivery_hours"]))
                rows.append(row)
                continue
            if q is not None:
                row.update(price=q.vwap, source="own", confidence=1.0,
                           data_origin="original", estimation_method="none")
            elif r is not None:
                if auto_reason:
                    row["flag"] = ";".join(filter(None, [row["flag"], auto_reason]))
                if cfg.layer_local:
                    b_loc, W, used = local_basis(per, day, p.mode_anchors[mode], mode,
                                               cfg.tau_log, kind_factors[mode])
                else:
                    b_loc, W, used = None, 0.0, []
                b_hist = self._hist_for(s, per, mode)
                cross = None
                if cfg.layer_cross and s.hist[mode].get(per) is not None:
                    cross = self._cross_adj(s, per, preps, mode)
                prior = (b_hist or 0.0) + cross[0] if cross else b_hist
                b, w, src = _blend(b_loc, W, prior, cfg.shrink_k)
                if src == "eex":
                    if s.fallback is None and s.book is not None:
                        s.fallback = EexFallback(s.book, s.hfn, cfg)
                    smoothed = s.fallback.price(day, per) if s.fallback is not None else None
                    if smoothed is None:
                        row["flag"] = ";".join(filter(None, [row["flag"], "eex_fallback_unavailable"]))
                        pending.append(row)
                    else:
                        confidence = 0.4 * (0.85 if r[1] != "exact" else 1.0)
                        if p.asof != day:
                            confidence *= 0.9
                        row.update(
                            price=smoothed.price, source="eex+smooth", confidence=round(confidence, 3),
                            data_origin="estimated", estimation_method=smoothed.method,
                            eex_fallback_trace=json.dumps(smoothed.trace, allow_nan=False, separators=(",", ":")),
                        )
                    rows.append(row)
                    continue
                if mode == "ratio" and abs(b) > cfg.max_ratio_deviation:
                    b = max(-cfg.max_ratio_deviation, min(cfg.max_ratio_deviation, b))
                    row["flag"] = ";".join(filter(None, [row["flag"], "ratio_adjustment_limited"]))
                if cross and src == "eex+hist":
                    src = "eex+cross"
                conf = 0.5 + 0.4 * w if src != "eex" else 0.4
                if r[1] != "exact":
                    conf *= 0.85
                if p.asof != day:
                    conf *= 0.9
                row.update(
                    price=apply_basis(r[0], b, mode), source=src, confidence=round(conf, 3),
                    basis=b, basis_local=b_loc if b_loc is not None else math.nan,
                    basis_hist=b_hist if b_hist is not None else math.nan,
                    cross_adj=cross[0] if cross else math.nan, local_weight=w,
                    anchors=",".join(used), cross_from=",".join(cross[1]) if cross else "",
                    data_origin="estimated",
                    estimation_method=_estimation_method(mode, b_loc, b_hist, cross),
                )
            else:
                pending.append(row)
            rows.append(row)

        if pending and cfg.layer_arbitrage:  # Filled contracts plus VWAPs outside the target list.
            known = {(x["delivery_start"], x["delivery_end"]): x["price"]
                     for x in rows if x["source"] != "missing" and x["hours"] > 0
                     and (x["delivery_start"], x["delivery_end"]) not in p.rejected}
            for k, q in p.own.items():
                if k not in p.rejected:
                    known.setdefault(k, q.vwap)
            arb = Pricer(known, s.hfn)
            for row in pending:
                r = arb.price(row["delivery_start"], row["delivery_end"], kind=row["kind"])
                if r is not None:
                    row.update(price=r[0], source="arbitrage", confidence=0.4,
                               eex_method=row["eex_method"] or r[1],
                               data_origin="estimated", estimation_method=f"contract_{r[1]}")
        for row in rows:
            if row["source"] != "missing" and not math.isfinite(row["price"]):
                raise ValueError(f"{s.name} {day} {row['tenor']}: computed price must be finite")
        return apply_shape(rows, cfg)

    # ------------------------------------------------ update history
    def _update(self, series: list[Series], day: date, preps: dict) -> None:
        """Use only real VWAPs and same-day EEX: stale EEX would mix market
        movements into the estimated basis."""
        series = [s for s in series if s.key in preps and preps[s.key].asof == day]
        for s in series:
            cfg = self.curve_configs[s.key]
            p = preps[s.key]
            for mode in MODES:
                # Each mode has its own historical skill and covariance units.
                errs: dict[str, list[tuple[float, float]]] = {}
                for a in p.mode_anchors[mode]:
                    bh = s.hist[mode].get(a.period)
                    if bh is not None:
                        errs.setdefault(a.period.group, []).append(
                            (apply_basis(a.eex, bh, mode) - a.own, a.eex - a.own))
                s.skill[mode].update(errs)
                sur = p.mode_surprises[mode]
                for g1, g2 in combinations(GROUPS, 2):
                    if g1 in sur and g2 in sur:
                        s.gcov.setdefault((mode, *sorted((g1, g2))), EWCov(cfg.corr_halflife_days)).update(
                            sur[g1], sur[g2], day)
                for other in series:
                    if other is s:
                        continue
                    so = preps[other.key].mode_surprises[mode]
                    for k in set(sur) & set(so):
                        self.cross.setdefault((mode, s.key, other.key, k), EWCov(cfg.cross_halflife_days)).update(
                            so[k], sur[k], day)  # x = the other curve, y = this curve
        for s in series:
            for md in MODES:
                s.hist[md].update(preps[s.key].mode_anchors.get(md, []), day)

    # ------------------------------------------------------------ backtest
    def _loo(self, s: Series, day: date, preps: dict) -> list[dict]:
        """Hide each anchor and predict it with each method:
          eex, local_*, hist_*, blend_*   (in ratio and additive modes)
          local_corr, blend_corr          (enhancement 1, both modes in auto)
          hist_cross                      (enhancement 2: no own anchors today)"""
        cfg = self.curve_configs[s.key]
        p = preps[s.key]
        kf_dist = distance_kind_factor(cfg.other_kind_weight)
        kf_corr = {md: self._kind_factor(s, True, md) for md in MODES}
        out = []
        for a in p.evaluation_anchors:
            others = [x for x in p.anchors if x.period.key != a.period.key]
            others_by_mode = {md: [x for x in p.mode_anchors[md] if x.period.key != a.period.key]
                              for md in MODES}
            base = {**self.configuration_records[s.key],
                    "reference_date": day, "product": s.m.product,
                    "region": s.m.region, "unit": s.m.unit, "tenor": a.tenor,
                    "kind": a.period.kind, "group": a.period.group, "own": a.own,
                    "volume": a.volume, "n_other_anchors": len(others),
                    "configured_basis_mode": cfg.basis_mode,
                    "eex_asof": p.asof, "eex_cutoff_date": EexBook.cutoff_date(day, cfg.eex_offset_days),
                    "eex_offset_days": cfg.eex_offset_days}
            preds = {"eex": a.eex}
            applied_modes = {"eex": ""}
            for m in MODES:
                others_md = others_by_mode[m]
                b_loc, W, _ = local_basis(a.period, day, others_md, m, cfg.tau_log, kf_dist)
                b_hist = s.hist[m].get(a.period)
                if b_loc is not None:
                    preds[f"local_{m}"] = apply_basis(a.eex, b_loc, m)
                if b_hist is not None:
                    preds[f"hist_{m}"] = apply_basis(a.eex, b_hist, m)
                preds[f"blend_{m}"] = apply_basis(a.eex, _blend(b_loc, W, b_hist, cfg.shrink_k)[0], m)
                if cfg.basis_mode in ("auto", m):
                    b_loc, W, _ = local_basis(a.period, day, others_md, m, cfg.tau_log, kf_corr[m])
                    if b_loc is not None:
                        preds[f"local_corr_{m}"] = apply_basis(a.eex, b_loc, m)
                    preds[f"blend_corr_{m}"] = apply_basis(a.eex, _blend(b_loc, W, b_hist, cfg.shrink_k)[0], m)
                    if b_hist is not None:
                        cross = self._cross_adj(s, a.period, preps, m)
                        preds[f"hist_cross_{m}"] = apply_basis(a.eex, b_hist + (cross[0] if cross else 0.0), m)
                applied_modes.update({method: m for method in preds if method.endswith(f"_{m}")})
            # Hide the observation from every mode before auto selects a mode.
            hidden = replace(p, own={k: q for k, q in p.own.items() if k != a.period.key},
                             anchors=others, mode_anchors=others_by_mode,
                             rejected=p.rejected - {a.period.key})
            # Score the same canonical contract kind used by the observation's
            # evidence and diagnostic group, even if a residual alias sorts first.
            label = next(label for label in p.own[a.period.key].tenors
                         if resolve_tenor(label, day, cfg.day_convention,
                                          cfg.weekend_offset).kind == a.period.kind)
            # Contract reconstruction can depend on other estimated targets.
            # Replay the production target set with this whole period hidden.
            deployed = self._fill(s, day, {**preps, s.key: hidden}, labels=[*cfg.tenors, label])
            prediction = next((row for row in deployed if row["tenor"] == label), None)
            if prediction is not None and math.isfinite(prediction["price"]):
                preds["pipeline_configured"] = prediction["price"]
                applied_modes["pipeline_configured"] = prediction["basis_mode"]
            for method, pr in preds.items():
                out.append({**base, "method": method, "basis_mode": applied_modes[method],
                            "pred": pr, "error": pr - a.own})
        return out


def _blend(b_loc: float | None, W: float, b_hist: float | None, k: float) -> tuple[float, float, str]:
    """Blend local and historical basis: w = W / (W + k). Without history,
    shrink toward 0 (unadjusted EEX)."""
    if b_loc is None or W <= 0:
        if b_hist is not None:
            return b_hist, 0.0, "eex+hist"
        return 0.0, 0.0, "eex"
    w = W / (W + k)
    prior = b_hist if b_hist is not None else 0.0
    b = w * b_loc + (1 - w) * prior
    if w >= 0.5 or b_hist is None:
        return b, w, "eex+local"
    return b, w, "eex+hist"


def _estimation_method(mode: str, local: float | None, hist: float | None, cross) -> str:
    parts = []
    if local is not None:
        parts.append("local")
    if hist is not None:
        parts.append("history")
    if cross is not None:
        parts.append("cross")
    return f"{mode}_" + "_".join(parts) if parts else "eex"
