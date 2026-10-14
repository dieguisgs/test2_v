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
    layer_arbitrage: bool
    # EEX
    max_stale_days: int | None
    warn_stale_days: int
    timezones: dict[str, str]
    tenors: list[str]
    day_convention: str
    weekend_offset: int
    # Anchors
    min_volume: float
    max_anchor_dev: float
    warmup_days: int
    vwap_columns: dict[str, str]
    # Spread method (see ALGORITHM.md)
    spread_window: int = 5           # EEX publications in each smoothing window
    spread_weighting: str = "ewma"   # ewma | simple
    spread_halflife: float = 2.0     # EWMA half-life, in publications
    spread_min_obs: int = 1          # minimum usable publications in a window
    anchor_rule: str = "nearest"     # nearest | interpolate
    anchor_scope: str = "same_kind"  # same_kind | any
    no_anchor: str = "eex_ewma"      # eex_ewma | eex_level | missing
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


def validate_spread_config(cfg: Config) -> None:
    """Validate the spread method's finite window and anchor rules."""
    for name, value, choices in (
        ("spread.weighting", cfg.spread_weighting, ("ewma", "simple")),
        ("spread.anchor_rule", cfg.anchor_rule, ("nearest", "interpolate")),
        ("spread.anchor_scope", cfg.anchor_scope, ("same_kind", "any")),
        ("spread.no_anchor", cfg.no_anchor, ("eex_ewma", "eex_level", "missing")),
    ):
        if value not in choices:
            raise ValueError(f"{name} must be one of {choices}")
    for name in ("spread_window", "spread_min_obs"):
        value = getattr(cfg, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"spread.{name.removeprefix('spread_')} must be an integer >= 1")
    if cfg.spread_min_obs > cfg.spread_window:
        raise ValueError("spread.min_obs cannot exceed spread.window")
    half = cfg.spread_halflife
    if (isinstance(half, bool) or not isinstance(half, (int, float))
            or not math.isfinite(half) or half <= 0):
        raise ValueError("spread.halflife must be a finite positive number")


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
    if not isinstance(cfg.layer_arbitrage, bool):
        raise ValueError("layers.arbitrage must be a boolean, not a quoted string or number")
    for name, value, choices in (
        ("run.configuration_mode", cfg.configuration_mode, ("global", "individual")),
        ("conventions.day", cfg.day_convention, ("calendar", "business")),
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
        "max_anchor_dev": ("method.max_anchor_dev", False),
    }
    for attribute, (label, positive) in numbers.items():
        value = getattr(cfg, attribute)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or (positive and value == 0)):
            raise ValueError(f"{label} must be a finite {'positive' if positive else 'nonnegative'} number")
    for attribute, label, minimum in (
        ("warn_stale_days", "eex.warn_stale_days", 0),
        ("warmup_days", "run.warmup_days", 0),
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
    for label, values in (("timezones", cfg.timezones), ("vwap_columns", cfg.vwap_columns)):
        if not isinstance(values, dict) or any(not isinstance(v, str) or not v.strip() for v in values.values()):
            raise ValueError(f"{label} must be a table of nonempty strings")
    validate_spread_config(cfg)
    validate_shape_config(cfg)


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    for section in ("paths", "layers", "method", "conventions", "eex", "spread",
                    "shape", "targets", "timezones", "run", "vwap_columns"):
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
    spread = raw.get("spread", {})
    shape = raw.get("shape", {})
    cfg = Config(
        base_dir=path.parent,
        vwap_input=paths.get("vwap_input", "data/vwaps.csv"),
        mapping_file=Path(), eex_curves_dir=Path(), output_dir=Path(),
        layer_arbitrage=layers.get("arbitrage", False),
        max_stale_days=eex.get("max_stale_days", 0),
        warn_stale_days=eex.get("warn_stale_days", 3),
        eex_offset_days=eex.get("offset_days", 0),
        timezones=dict(raw.get("timezones", {"default": "Europe/Berlin"})),
        tenors=raw.get("targets", {}).get("tenors", []),
        day_convention=conv.get("day", "calendar"),
        weekend_offset=conv.get("weekend_offset", 0),
        min_volume=method.get("min_volume", 0),
        max_anchor_dev=method.get("max_anchor_dev", 0),
        warmup_days=raw.get("run", {}).get("warmup_days", 0),
        configuration_mode=raw.get("run", {}).get("configuration_mode", "global"),
        vwap_columns=dict(raw.get("vwap_columns", {})),
        spread_window=spread.get("window", 5),
        spread_weighting=spread.get("weighting", "ewma"),
        spread_halflife=spread.get("halflife", 2.0),
        spread_min_obs=spread.get("min_obs", 1),
        anchor_rule=spread.get("anchor_rule", "nearest"),
        anchor_scope=spread.get("anchor_scope", "same_kind"),
        no_anchor=spread.get("no_anchor", "eex_ewma"),
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
