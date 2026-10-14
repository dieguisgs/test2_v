"""Curve filling engine shared by `daily` and `refill` (spread methodology).

Each series is one mapped (product, region, unit) curve and is filled on its
own; see ALGORITHM.md / ALGORITMO.md for the method.

Cascade per (day, curve identity, target tenor):
  own         Original VWAP (black).
  eex_spread  Own anchor + EWMA of the EEX spread target - anchor (blue).
  eex_ewma    No usable anchor: EWMA of the EEX target price (orange).
  eex_level   Same, unsmoothed latest allowed EEX price (orange variant).
  arbitrage   Optional: rebuild from own/already estimated contracts.
  missing     No estimate is available.

Only EEX publications allowed by eex.offset_days enter a window, and the
method learns nothing from earlier outputs, so a single day computed by
`daily` equals the same day inside a longer `refill`.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import pandas as pd

from vwaps.config import Config, validate_config
from vwaps.consistency import check_day
from vwaps.hours import hours_fn
from vwaps.identity import CurveKey, curve_keys
from vwaps.io_eex import EexBook
from vwaps.log import get_logger
from vwaps.mapping import ProductMap
from vwaps.pricer import Pricer
from vwaps.product_config import configuration_context_records, configuration_record, effective_config
from vwaps.shape import apply_shape
from vwaps.spread import Anchor, EexWindow, log_ttm, spread_estimate, trace_parts
from vwaps.tenors import Key, Period, resolve_tenor

GROUPS = ("short", "month", "quarter", "long")
# Equal delivery intervals can have both full-contract and residual labels.
# Their evidence kind must not depend on the original CSV row order.
OWN_KIND_ORDER = {kind: index for index, kind in enumerate((
    "Day", "Weekend", "Week", "Month", "Quarter", "Season", "Year", "BOW", "BOM",
))}
NO_ANCHOR_SOURCE = {"eex_ewma": "eex_ewma", "eex_level": "eex_level"}


@dataclass
class OwnQuote:
    period: Period
    tenors: list[str]
    vwap: float
    volume: float


@dataclass
class Series:
    m: ProductMap
    book: EexBook | None
    own_by_day: dict
    hfn: object
    window: EexWindow | None = None

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
    rejected: set = field(default_factory=set)
    evaluation_anchors: list[Anchor] = field(default_factory=list)


@dataclass
class RunResult:
    filled: pd.DataFrame
    consistency: pd.DataFrame
    loo: pd.DataFrame = field(default_factory=pd.DataFrame)
    errors: list[dict] = field(default_factory=list)
    configuration_contexts: dict[str, dict] = field(default_factory=dict)


class CurveFiller:
    """Fill curves using immutable effective settings for each full identity.

    Mapping overrides apply in individual mode. Explicit ``curve_parameters``
    are final candidate overrides used by tuning. Optional
    ``evaluation_configs`` freeze LOO truth eligibility independently of
    candidate anchor filters; they never change production predictions.
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

    # ------------------------------------------------------------------ run
    def _build_series(self) -> list[Series]:
        by_curve = {key: frame for key, frame in self.vwaps.groupby(curve_keys(self.vwaps), sort=False)}
        out = []
        for m in self.maps:
            cfg = self.curve_configs[m.key]
            g = by_curve.get(m.key)
            legacy_book = self.books.get(m.product) if not m.region and not m.unit else None
            book = self.books.get(m.key, legacy_book)
            hfn = hours_fn(m.hours, m.timezone)
            window = None
            if book is not None:
                window = EexWindow(book, hfn, cfg.spread_window, cfg.spread_weighting,
                                   cfg.spread_halflife, cfg.spread_min_obs,
                                   cfg.max_stale_days, cfg.eex_offset_days)
            out.append(Series(m, book, {d: x for d, x in g.groupby("date")} if g is not None else {},
                              hfn, window))
        return out

    def run(self, start: date, end: date, loo: bool = False,
            *, output_keys: set[tuple[date, CurveKey]] | None = None) -> RunResult:
        cfg = self.cfg
        log = get_logger()
        series = self._build_series()
        if start > end:
            raise ValueError("The start date cannot be later than the end date")
        EexBook.cutoff_date(start, cfg.eex_offset_days)
        log.info("Engine %s -> %s | configuration=%s | EEX offset=%d | spread window=%d (%s)",
                 start, end, cfg.configuration_mode, cfg.eex_offset_days,
                 cfg.spread_window, cfg.spread_weighting)
        # Nothing is learned from earlier days: only the requested days are computed.
        days: set[date] = set()
        for s in series:
            days |= {d for d in s.own_by_day if start <= d <= end}
            if s.book is not None:
                days |= {d for d in s.book.trade_dates if start <= d <= end}
        d = start
        while d <= end:  # Include every weekday in the range, even without data.
            if d.weekday() < 5:
                days.add(d)
            d += timedelta(days=1)
        if output_keys is not None:
            days.update(day for day, _ in output_keys if start <= day <= end)

        no_eex: dict[str, list[date]] = {}
        filled, cons, loo_rows, errors = [], [], [], []
        for day in sorted(days):
            cutoff = EexBook.cutoff_date(day, cfg.eex_offset_days)
            for s in series:
                if not s.output:
                    continue
                if output_keys is not None and (day, s.key) not in output_keys:
                    continue
                try:
                    prep = self._prep(s, day)
                except Exception:
                    errors.append({"reference_date": day, "product": s.m.product,
                                   "region": s.m.region, "unit": s.m.unit, "phase": "prepare"})
                    log.exception("%s %s: error preparing the day; skipping this curve", day, s.name)
                    continue
                try:
                    rows = self._fill(s, day, prep)
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
                        loo_rows += self._loo(s, day, prep)
                except Exception:
                    errors.append({"reference_date": day, "product": s.m.product,
                                   "region": s.m.region, "unit": s.m.unit, "phase": "fill"})
                    log.exception("%s %s: error filling the curve; skipping this curve", day, s.name)
                    continue
                filled += rows
                log.info("%s %s | anchors=%d | EEX cutoff=%s offset=%d latest=%s | methods=%s",
                         day, s.name, len(prep.anchors), cutoff, cfg.eex_offset_days, prep.asof,
                         dict(Counter(r["estimation_method"] for r in rows)))
                asof = prep.asof
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
        for name, ds in no_eex.items():
            log.warning("%s: no EEX settlement within cutoff/age limits from %s to %s (%d days): "
                        "only own VWAPs are available", name, ds[0], ds[-1], len(ds))
        context_ids = {row["configuration_context_id"] for row in filled}
        context_ids.update(row["configuration_context_id"] for row in loo_rows)
        contexts = {context_id: deepcopy(self.configuration_contexts[context_id]) for context_id in sorted(context_ids)}
        return RunResult(pd.DataFrame(filled), pd.DataFrame(cons), pd.DataFrame(loo_rows), errors, contexts)

    # ------------------------------------------------------- prepare the day
    def _own_quotes(self, day: date, g: pd.DataFrame | None, hfn, *,
                    cfg: Config | None = None) -> dict[Key, OwnQuote]:
        """Aggregate equal delivery intervals independently of input row order.

        Each positive finite volume weights its price; zero or unknown volume
        uses unit weight. Reported volume sums known nonnegative values and
        stays unknown when all volumes are unknown. Equivalent aliases use a
        deterministic evidence kind: full contracts precede BOW and BOM.
        """
        if g is None:
            return {}
        cfg = self.cfg if cfg is None else cfg
        grouped: dict[Key, list[tuple[Period, str, float, float]]] = {}
        for tenor, vwap, vol in g[["tenor", "vwap", "volume"]].itertuples(index=False):
            per = resolve_tenor(tenor, day, cfg.day_convention, cfg.weekend_offset)
            if per is None or not math.isfinite(float(vwap)):
                continue
            # Peak BOW/BOM may share dates with WE/Day but have no delivery hours.
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
                price = price_scale * max(-1.0, min(1.0, scaled_price))
            else:
                price = 0.0
            known_volumes = [row[3] for row in observations if math.isfinite(row[3])]
            volume = math.fsum(known_volumes) if known_volumes else math.nan
            out[key] = OwnQuote(period, sorted({row[1] for row in observations}), price, volume)
        return out

    def _eligible(self, own: dict[Key, OwnQuote], eex: Pricer, day: date, cfg: Config,
                  bad: list[str] | None = None, rejected: set | None = None) -> list[Anchor]:
        anchors = []
        for q in own.values():
            if math.isfinite(q.volume) and q.volume < cfg.min_volume:
                if bad is not None:
                    bad.append(f"{','.join(q.tenors)} volume {q.volume:g} < {cfg.min_volume:g}")
                    rejected.add(q.period.key)
                continue
            if cfg.max_anchor_dev > 0:
                r = eex.price(q.period.start, q.period.end, kind=q.period.kind)
                if r is not None and abs(q.vwap - r[0]) / max(abs(r[0]), 1.0) > cfg.max_anchor_dev:
                    if bad is not None:
                        bad.append(f"{','.join(q.tenors)} {q.vwap:g} vs EEX {r[0]:g}")
                        rejected.add(q.period.key)
                    continue
            anchors.append(Anchor(",".join(q.tenors), q.period, q.vwap, q.volume,
                                  log_ttm(q.period, day)))
        return anchors

    def _prep(self, s: Series, day: date) -> DayPrep:
        cfg = self.curve_configs[s.key]
        quotes, asof = (s.book.available_quotes(day, cfg.max_stale_days, cfg.eex_offset_days)
                        if s.book is not None else ({}, None))
        eex = Pricer(quotes, s.hfn)
        own = self._own_quotes(day, s.own_by_day.get(day), s.hfn, cfg=cfg)
        bad, rejected = [], set()
        anchors = self._eligible(own, eex, day, cfg, bad, rejected)
        if bad:
            get_logger().warning("%s %s: anchors excluded by volume/deviation; originals preserved: %s",
                                 day, s.name, "; ".join(bad))
        evaluation = anchors
        if s.key in self.evaluation_configs:
            # Candidate filters must not remove difficult observations from the exam.
            evaluation = self._eligible(own, eex, day, self.evaluation_configs[s.key])
        return DayPrep(asof, eex, own, anchors, rejected, evaluation)

    # ------------------------------------------------------------ fill
    def _estimate(self, s: Series, day: date, per: Period, anchors: list[Anchor],
                  cfg: Config) -> dict | None:
        """Blue from anchors, else orange; None when neither is available."""
        if s.window is None:
            return None
        blue = spread_estimate(s.window, day, per, anchors, cfg.anchor_rule, cfg.anchor_scope)
        if blue is not None:
            parts = blue["parts"]
            return {
                "price": blue["price"], "source": "eex_spread",
                "estimation_method": f"spread_{blue['rule']}",
                "anchors": ",".join(p["anchor"].tenor for p in parts),
                "anchor_price": parts[0]["anchor"].own if len(parts) == 1 else math.nan,
                "spread_smoothed": parts[0]["spread"].value if len(parts) == 1 else math.nan,
                "window_obs": float(min(p["spread"].n_obs for p in parts)),
                "trace": {"rule": blue["rule"], "parts": trace_parts(parts)},
                "confidence": 0.8 if len(parts) == 1 else 0.85,
            }
        if cfg.no_anchor == "missing":
            return None
        smoothed = (s.window.outright(day, per) if cfg.no_anchor == "eex_ewma"
                    else s.window.level(day, per))
        if smoothed is None:
            return None
        return {
            "price": smoothed.value, "source": NO_ANCHOR_SOURCE[cfg.no_anchor],
            "estimation_method": cfg.no_anchor, "anchors": "", "anchor_price": math.nan,
            "spread_smoothed": math.nan, "window_obs": float(smoothed.n_obs),
            "trace": {"rule": cfg.no_anchor, "observations": smoothed.observations},
            "confidence": 0.4,
        }

    def _fill(self, s: Series, day: date, p: DayPrep, labels: list[str] | None = None) -> list[dict]:
        cfg = self.curve_configs[s.key]
        rows, pending = [], []
        requested = list(labels if labels is not None else cfg.tenors)
        if cfg.shape_mode != "off":
            # Include observed periods as shape context, including their aliases.
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
            row = {
                **self.configuration_records[s.key],
                "reference_date": day, "product": s.m.product,
                "region": s.m.region, "unit": s.m.unit, "area": s.m.area,
                "profile": s.m.profile, "tenor": label, "kind": per.kind, "period": per.name,
                "delivery_start": per.start, "delivery_end": per.end,
                "hours": delivery_hours,
                "price": math.nan, "source": "missing", "confidence": 0.0,
                "data_origin": "missing", "estimation_method": "unavailable",
                "own_vwap": q.vwap if q else math.nan, "own_volume": q.volume if q else math.nan,
                "eex_settle": r[0] if r else math.nan, "eex_method": r[1] if r else "",
                "eex_asof": p.asof, "eex_cutoff_date": EexBook.cutoff_date(day, cfg.eex_offset_days),
                "eex_offset_days": cfg.eex_offset_days,
                "anchors": "", "anchor_price": math.nan, "spread_smoothed": math.nan,
                "window_obs": math.nan, "spread_trace": "",
                "flag": "anchor_excluded" if per.key in p.rejected else "",
            }
            if delivery_hours <= 0:
                row["flag"] = ";".join(filter(None, [row["flag"], "zero_delivery_hours"]))
                rows.append(row)
                continue
            if q is not None:
                row.update(price=q.vwap, source="own", confidence=1.0,
                           data_origin="original", estimation_method="none")
            else:
                est = self._estimate(s, day, per, p.anchors, cfg)
                if est is None:
                    pending.append(row)
                else:
                    confidence = est["confidence"] * (0.9 if p.asof != day else 1.0)
                    row.update(
                        price=est["price"], source=est["source"], confidence=round(confidence, 3),
                        data_origin="estimated", estimation_method=est["estimation_method"],
                        anchors=est["anchors"], anchor_price=est["anchor_price"],
                        spread_smoothed=est["spread_smoothed"], window_obs=est["window_obs"],
                        spread_trace=json.dumps(est["trace"], allow_nan=False, separators=(",", ":")),
                    )
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

    # ------------------------------------------------------------ backtest
    def _loo(self, s: Series, day: date, p: DayPrep) -> list[dict]:
        """Hide each anchor and predict it with:
          eex                 raw EEX of the day (baseline shared with VWAPS)
          eex_ewma, eex_level orange variants (no anchor)
          spread_nearest, spread_interpolate  blue variants (configured scope)
          pipeline_configured the deployed configuration, whole curve replayed"""
        cfg = self.curve_configs[s.key]
        out = []
        for a in p.evaluation_anchors:
            r = p.eex.price(a.period.start, a.period.end, kind=a.period.kind)
            if r is None:
                continue  # Same exam as VWAPS: observations without same-cutoff EEX are not scored.
            others = [x for x in p.anchors if x.period.key != a.period.key]
            base = {**self.configuration_records[s.key],
                    "reference_date": day, "product": s.m.product,
                    "region": s.m.region, "unit": s.m.unit, "tenor": a.tenor,
                    "kind": a.period.kind, "group": a.period.group, "own": a.own,
                    "volume": a.volume, "n_other_anchors": len(others),
                    "eex_asof": p.asof, "eex_cutoff_date": EexBook.cutoff_date(day, cfg.eex_offset_days),
                    "eex_offset_days": cfg.eex_offset_days}
            preds = {"eex": r[0]}
            if s.window is not None:
                for name, fn in (("eex_ewma", s.window.outright), ("eex_level", s.window.level)):
                    sm = fn(day, a.period)
                    if sm is not None:
                        preds[name] = sm.value
                for rule in ("nearest", "interpolate"):
                    est = spread_estimate(s.window, day, a.period, others, rule, cfg.anchor_scope)
                    if est is not None:
                        preds[f"spread_{rule}"] = est["price"]
            hidden = replace(p, own={k: q for k, q in p.own.items() if k != a.period.key},
                             anchors=others, rejected=p.rejected - {a.period.key})
            label = next(label for label in p.own[a.period.key].tenors
                         if resolve_tenor(label, day, cfg.day_convention,
                                          cfg.weekend_offset).kind == a.period.kind)
            deployed = self._fill(s, day, hidden, labels=[*cfg.tenors, label])
            prediction = next((row for row in deployed if row["tenor"] == label), None)
            if prediction is not None and math.isfinite(prediction["price"]):
                preds["pipeline_configured"] = prediction["price"]
            for method, pr in preds.items():
                out.append({**base, "method": method, "pred": pr, "error": pr - a.own})
        return out
