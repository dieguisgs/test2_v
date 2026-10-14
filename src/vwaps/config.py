"""Read and validate config.toml."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from vwaps.tenors import parse_tenor


@dataclass
class Config:
    base_dir: Path
    vwap_input: str
    mapping_file: Path
    eex_curves_dir: Path
    output_dir: Path
    # Layers
    layer_local: bool
    layer_correlation: bool
    layer_cross: bool
    layer_hist: str  # on | off | auto
    layer_arbitrage: bool
    # EEX
    max_stale_days: int | None
    warn_stale_days: int
    timezones: dict[str, str]
    tenors: list[str]
    day_convention: str
    weekend_offset: int
    # Method
    basis_mode: str
    min_volume: float
    tau_log: float
    other_kind_weight: float
    shrink_k: float
    ewma_halflife_days: float
    max_anchor_dev: float
    hist_auto_min_obs: float
    corr_halflife_days: float
    corr_prior_obs: float
    cross_min_corr: float
    cross_min_obs: float
    cross_halflife_days: float
    warmup_days: int
    vwap_columns: dict[str, str]
    ratio_eex_floor: float = 1.0
    max_ratio_deviation: float = 1.0
    hist_max_age_days: int = 60
    fallback_price_method: str = "ewma"
    fallback_price_window: int = 5
    fallback_ewma_halflife: float = 2.0
    fallback_spread_window: int = 9
    fallback_anchor_months: int = 2
    shape_mode: str = "off"
    shape_adjust_originals: bool = False
    shape_smoothness_weight: float = 1.0
    shape_coherence_weight: float = 10.0
    shape_max_abs_adjustment: float = 10.0
    shape_original_weight: float = 10.0
    shape_coherence_tolerance: float = 0.01
    eex_offset_days: int = 0
    configuration_mode: str = "global"
    command_overrides: dict = field(default_factory=dict)

    def tz(self, area: str) -> str:
        return self.timezones.get(area, self.timezones.get("default", "Europe/Berlin"))

    def resolve(self, p: str | Path) -> Path:
        p = Path(os.path.expandvars(os.path.expanduser(str(p))))
        return p if p.is_absolute() else (self.base_dir / p)


def validate_fallback_config(cfg: Config) -> None:
    """Validate the finite-window EEX transformation, including CLI overrides."""
    if cfg.fallback_price_method not in ("simple", "ewma"):
        raise ValueError("eex_fallback.price_method must be simple or ewma")
    for name, minimum in (("price_window", 2), ("spread_window", 2), ("anchor_months", 1)):
        value = getattr(cfg, f"fallback_{name}")
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"eex_fallback.{name} must be an integer >= {minimum}")
    half = cfg.fallback_ewma_halflife
    if (isinstance(half, bool) or not isinstance(half, (int, float))
            or not math.isfinite(half) or half <= 0):
        raise ValueError("eex_fallback.ewma_halflife must be a finite positive number")


def validate_shape_config(cfg: Config) -> None:
    """Validate optional shape controls without coercing strings or booleans."""
    if cfg.shape_mode not in ("off", "audit", "adjust"):
        raise ValueError("shape.mode must be off, audit or adjust")
    if not isinstance(cfg.shape_adjust_originals, bool):
        raise ValueError("shape.adjust_originals must be a boolean")
    for name in ("smoothness_weight", "coherence_weight", "max_abs_adjustment", "original_weight",
                 "coherence_tolerance"):
        value = getattr(cfg, f"shape_{name}")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            raise ValueError(f"shape.{name} must be a finite number")
        if name in ("smoothness_weight", "coherence_weight", "coherence_tolerance") and value < 0:
            raise ValueError(f"shape.{name} must be >= 0")
        if name == "max_abs_adjustment" and value <= 0:
            raise ValueError("shape.max_abs_adjustment must be > 0")
        if name == "original_weight" and value < 1:
            raise ValueError("shape.original_weight must be >= 1")


def validate_config(cfg: Config) -> None:
    """Validate user-controlled types and finite bounds before calculations."""
    # Import lazily because product_config also resolves and validates Config.
    from vwaps.product_config import validate_parameter_overrides
    validate_parameter_overrides(cfg.command_overrides)
    for name in ("local", "correlation", "cross", "arbitrage"):
        if not isinstance(getattr(cfg, f"layer_{name}"), bool):
            raise ValueError(f"layers.{name} must be a boolean, not a quoted string or number")
    for name, value, choices in (
        ("run.configuration_mode", cfg.configuration_mode, ("global", "individual")),
        ("method.basis_mode", cfg.basis_mode, ("auto", "additive", "ratio")),
        ("conventions.day", cfg.day_convention, ("calendar", "business")),
        ("layers.hist", cfg.layer_hist, ("on", "off", "auto")),
    ):
        if value not in choices:
            raise ValueError(f"{name} must be one of {choices}")
    if not isinstance(cfg.tenors, list) or any(not isinstance(label, str) for label in cfg.tenors):
        raise ValueError("targets.tenors must be a list of tenor strings")
    for label in cfg.tenors:
        parsed = parse_tenor(label)
        if parsed is None or (parsed[0] in ("Sum", "Win") and parsed[1] < 1):
            raise ValueError(f"targets.tenors contains an unsupported tenor: {label!r}")
    numbers = {
        "min_volume": ("method.min_volume", False),
        "tau_log": ("method.tau_log", True),
        "other_kind_weight": ("method.other_kind_weight", False),
        "shrink_k": ("method.shrink_k", True),
        "ewma_halflife_days": ("method.ewma_halflife_days", True),
        "max_anchor_dev": ("method.max_anchor_dev", False),
        "hist_auto_min_obs": ("method.hist_auto_min_obs", False),
        "ratio_eex_floor": ("method.ratio_eex_floor", True),
        "max_ratio_deviation": ("method.max_ratio_deviation", True),
        "corr_halflife_days": ("correlation.halflife_days", True),
        "corr_prior_obs": ("correlation.prior_obs", False),
        "cross_min_obs": ("cross.min_obs", False),
        "cross_halflife_days": ("cross.halflife_days", True),
    }
    for attribute, (label, positive) in numbers.items():
        value = getattr(cfg, attribute)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or (positive and value == 0)):
            raise ValueError(f"{label} must be a finite {'positive' if positive else 'nonnegative'} number")
    for attribute, label, minimum in (
        ("warn_stale_days", "eex.warn_stale_days", 0),
        ("warmup_days", "run.warmup_days", 0),
        ("hist_max_age_days", "method.hist_max_age_days", 1),
        ("weekend_offset", "conventions.weekend_offset", None),
    ):
        value = getattr(cfg, attribute)
        if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
            raise ValueError(f"{label} must be an integer" + (f" >= {minimum}" if minimum is not None else ""))
    if cfg.max_stale_days is not None and (
        isinstance(cfg.max_stale_days, bool) or not isinstance(cfg.max_stale_days, int) or cfg.max_stale_days < 0
    ):
        raise ValueError("eex.max_stale_days must be an integer >= 0 (0 means unlimited in TOML)")
    if (isinstance(cfg.eex_offset_days, bool) or not isinstance(cfg.eex_offset_days, int)
            or cfg.eex_offset_days > 0):
        raise ValueError("eex.offset_days must be an integer <= 0 (calendar days)")
    value = cfg.cross_min_corr
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not -1 <= value <= 1):
        raise ValueError("cross.min_corr must be a finite number between -1 and 1")
    for label, values in (("timezones", cfg.timezones), ("vwap_columns", cfg.vwap_columns)):
        if not isinstance(values, dict) or any(not isinstance(v, str) or not v.strip() for v in values.values()):
            raise ValueError(f"{label} must be a table of nonempty strings")
    validate_fallback_config(cfg)
    validate_shape_config(cfg)


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    for section in ("paths", "layers", "method", "conventions", "eex", "correlation", "cross",
                    "eex_fallback", "shape", "targets", "timezones", "run", "vwap_columns"):
        if section in raw and not isinstance(raw[section], dict):
            raise ValueError(f"{section} must be a TOML table")
    paths = raw.get("paths", {})
    for name, value in paths.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"paths.{name} must be a nonempty path string")
    layers = raw.get("layers", {})
    method = raw.get("method", {})
    conv = raw.get("conventions", {})
    eex = raw.get("eex", {})
    corr = raw.get("correlation", {})
    cross = raw.get("cross", {})
    fallback = raw.get("eex_fallback", {})
    shape = raw.get("shape", {})
    hist = layers.get("hist", "auto")
    cfg = Config(
        base_dir=path.parent,
        vwap_input=paths.get("vwap_input", "data/vwaps.csv"),
        mapping_file=Path(), eex_curves_dir=Path(), output_dir=Path(),
        layer_local=layers.get("local", True),
        layer_correlation=layers.get("correlation", False),
        layer_cross=layers.get("cross", False),
        layer_hist=hist.lower() if isinstance(hist, str) else hist,
        layer_arbitrage=layers.get("arbitrage", False),
        max_stale_days=eex.get("max_stale_days", 0),
        warn_stale_days=eex.get("warn_stale_days", 3),
        eex_offset_days=eex.get("offset_days", 0),
        timezones=dict(raw.get("timezones", {"default": "Europe/Berlin"})),
        tenors=raw.get("targets", {}).get("tenors", []),
        day_convention=conv.get("day", "calendar"),
        weekend_offset=conv.get("weekend_offset", 0),
        basis_mode=method.get("basis_mode", "auto"),
        min_volume=method.get("min_volume", 0),
        tau_log=method.get("tau_log", 0.5),
        other_kind_weight=method.get("other_kind_weight", 0.6),
        shrink_k=method.get("shrink_k", 1.0),
        ewma_halflife_days=method.get("ewma_halflife_days", 10),
        max_anchor_dev=method.get("max_anchor_dev", 0),
        hist_auto_min_obs=method.get("hist_auto_min_obs", 10),
        corr_halflife_days=corr.get("halflife_days", 20),
        corr_prior_obs=corr.get("prior_obs", 8),
        cross_min_corr=cross.get("min_corr", 0.5),
        cross_min_obs=cross.get("min_obs", 8),
        cross_halflife_days=cross.get("halflife_days", 20),
        warmup_days=raw.get("run", {}).get("warmup_days", 0),
        configuration_mode=raw.get("run", {}).get("configuration_mode", "global"),
        vwap_columns=dict(raw.get("vwap_columns", {})),
        ratio_eex_floor=method.get("ratio_eex_floor", 1.0),
        max_ratio_deviation=method.get("max_ratio_deviation", 1.0),
        hist_max_age_days=method.get("hist_max_age_days", 60),
        fallback_price_method=fallback.get("price_method", "ewma"),
        fallback_price_window=fallback.get("price_window", 5),
        fallback_ewma_halflife=fallback.get("ewma_halflife", 2.0),
        fallback_spread_window=fallback.get("spread_window", 9),
        fallback_anchor_months=fallback.get("anchor_months", 2),
        shape_mode=shape.get("mode", "off"),
        shape_adjust_originals=shape.get("adjust_originals", False),
        shape_smoothness_weight=shape.get("smoothness_weight", 1.0),
        shape_coherence_weight=shape.get("coherence_weight", 10.0),
        shape_max_abs_adjustment=shape.get("max_abs_adjustment", 10.0),
        shape_original_weight=shape.get("original_weight", 10.0),
        shape_coherence_tolerance=shape.get("coherence_tolerance", 0.01),
    )
    cfg.mapping_file = cfg.resolve(paths.get("mapping", "mappings/products.csv"))
    cfg.eex_curves_dir = cfg.resolve(paths.get("eex_curves_dir", "../eex_scraper/output/curves/POWER"))
    cfg.output_dir = cfg.resolve(paths.get("output_dir", "output"))
    validate_config(cfg)
    if cfg.max_stale_days == 0:
        cfg.max_stale_days = None
    return cfg
