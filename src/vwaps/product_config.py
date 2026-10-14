"""Resolve explicit per-curve model settings over a shared configuration.

Production table cells describe one fixed configuration, never a search grid.
Operational input paths, publication availability and calendar conventions stay
global. Runtime overrides are a separate final layer used by controlled trials.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from vwaps.config import Config, validate_config
from vwaps.identity import CurveKey

if TYPE_CHECKING:
    from vwaps.mapping import ProductMap


MODEL_PARAMETER_FIELDS = (
    "tenors",
    "layer_local", "layer_correlation", "layer_cross", "layer_hist", "layer_arbitrage",
    "basis_mode", "min_volume", "tau_log", "other_kind_weight", "shrink_k",
    "ewma_halflife_days", "max_anchor_dev", "hist_auto_min_obs", "ratio_eex_floor",
    "max_ratio_deviation", "hist_max_age_days",
    "corr_halflife_days", "corr_prior_obs",
    "cross_min_corr", "cross_min_obs", "cross_halflife_days",
    "fallback_price_method", "fallback_price_window", "fallback_ewma_halflife",
    "fallback_spread_window", "fallback_anchor_months",
    "shape_mode", "shape_adjust_originals", "shape_smoothness_weight",
    "shape_coherence_weight", "shape_max_abs_adjustment", "shape_original_weight",
    "shape_coherence_tolerance",
)

_BOOL_FIELDS = frozenset((
    "layer_local", "layer_correlation", "layer_cross", "layer_arbitrage",
    "shape_adjust_originals",
))
_INT_FIELDS = frozenset((
    "hist_max_age_days", "fallback_price_window", "fallback_spread_window",
    "fallback_anchor_months",
))
_STRING_FIELDS = frozenset((
    "layer_hist", "basis_mode", "fallback_price_method", "shape_mode",
))
_FLOAT_FIELDS = frozenset(MODEL_PARAMETER_FIELDS) - (
    _BOOL_FIELDS | _INT_FIELDS | _STRING_FIELDS | {"tenors"})


def validate_parameter_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a typed parameter overlay, rejecting unknown keys and coercion.

    Range and cross-field validation happen against the resulting full Config
    in :func:`effective_config`. A JSON scalar is a fixed value; lists are only
    valid for ``tenors``. A parameter grid is handled by the tuning API instead.
    """
    if not isinstance(overrides, Mapping):
        raise ValueError("Model parameter overrides must be an object of parameter names and values")
    unknown = [name for name in overrides if name not in MODEL_PARAMETER_FIELDS]
    if unknown:
        raise ValueError(f"Unknown or non-model parameter override: {unknown}")
    parsed = {}
    for name, value in overrides.items():
        valid = (
            (name in _BOOL_FIELDS and isinstance(value, bool))
            or (name in _INT_FIELDS and isinstance(value, int) and not isinstance(value, bool))
            or (name in _STRING_FIELDS and isinstance(value, str))
            or (name in _FLOAT_FIELDS and isinstance(value, (int, float))
                and not isinstance(value, bool) and math.isfinite(value))
            or (name == "tenors" and isinstance(value, list)
                and all(isinstance(label, str) for label in value))
        )
        if not valid:
            raise ValueError(f"Invalid type or non-finite value for model parameter {name}: {value!r}")
        parsed[name] = float(value) if name in _FLOAT_FIELDS else copy.deepcopy(value)
    return parsed


def parse_parameter_cells(row: Mapping[str, Any]) -> dict[str, Any]:
    """Parse optional CSV/Excel model cells; empty cells inherit the global value.

    Booleans use true/false, integers use whole-number text, and tenor lists use
    JSON, for example ``[\"M+1\", \"Q+1\"]``. No Python code is evaluated.
    """
    overrides = {}
    for name in MODEL_PARAMETER_FIELDS:
        raw = row.get(name, "")
        if raw is None or isinstance(raw, str) and not raw.strip():
            continue
        if not isinstance(raw, str):
            overrides[name] = raw
            continue
        value = raw.strip()
        try:
            if name in _BOOL_FIELDS:
                if value.lower() not in ("true", "false"):
                    raise ValueError("use true or false")
                parsed = value.lower() == "true"
            elif name in _INT_FIELDS:
                if re.fullmatch(r"[+-]?\d+", value) is None:
                    raise ValueError("use an integer without a decimal part")
                parsed = int(value)
            elif name in _FLOAT_FIELDS:
                parsed = float(value)
            elif name == "tenors":
                parsed = json.loads(value)
            else:
                parsed = value.lower()
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"Invalid mapping cell {name}={raw!r}: {exc}") from exc
        overrides[name] = parsed
    return validate_parameter_overrides(overrides)


def effective_config(
    cfg: Config,
    mapping: ProductMap,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """Return a separate validated configuration for one exact curve identity.

    Global mode deliberately ignores all mapping parameter values. Individual
    mode applies nonempty mapping cells. Explicit command-line values supersede
    the table. Runtime overrides apply last in either mode, allowing a trial to
    vary the intended target curve without changing the production table or the
    shared configuration.
    """
    if cfg.configuration_mode not in ("global", "individual"):
        raise ValueError("run.configuration_mode must be global or individual")
    parameters = {}
    if cfg.configuration_mode == "individual":
        parameters.update(validate_parameter_overrides(mapping.parameter_overrides))
    parameters.update(validate_parameter_overrides(cfg.command_overrides))
    if overrides is not None:
        parameters.update(validate_parameter_overrides(overrides))
    resolved = replace(copy.deepcopy(cfg), **parameters)
    validate_config(resolved)
    return resolved


def model_parameter_payload(cfg: Config) -> dict[str, Any]:
    """Return canonical, independent model values suitable for JSON and hashing."""
    return validate_parameter_overrides({name: getattr(cfg, name) for name in MODEL_PARAMETER_FIELDS})


def configuration_record(
    cfg: Config,
    mapping: ProductMap,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Describe the effective model without local paths or unstable profile names.

    The SHA-256 identifier fingerprints model parameters only. Publication-date
    policy remains in the existing EEX provenance columns and run configuration.
    Equal model settings therefore share an identifier across products, modes
    and filesystem locations. The complete JSON preserves inherited values too.
    """
    resolved = effective_config(cfg, mapping, overrides)
    encoded = json.dumps(model_parameter_payload(resolved), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return {
        "configuration_mode": cfg.configuration_mode,
        "configuration_id": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "configuration_parameters": encoded,
    }


def configuration_context_records(
    cfg: Config,
    maps: list[ProductMap],
    curve_parameters: dict[CurveKey, dict] | None = None,
) -> dict[CurveKey, dict[str, Any]]:
    """Fingerprint the operational and helper context of each active receiver.

    ``configuration_id`` already describes the receiver's own model settings.
    This complementary identifier covers its delivery mapping, the shared
    availability/calendar policy, and every other active curve's mapping and
    effective model parameters when CROSS is enabled for the receiver. A
    potential helper is included even if it contributes no shock on one date:
    changing its filters or memory can alter learned cross relationships.

    Receiver parameters are deliberately absent, so expanding its target list
    does not change its context fingerprint. With CROSS disabled, unrelated
    curves do not affect this identifier. Input prices and absolute local paths
    are not data-versioned here; the document records calculation settings.
    Returned documents are independent of caller-owned mutable values.
    """
    validate_config(cfg)
    active = [mapping for mapping in maps if mapping.active]
    keys = [mapping.key for mapping in active]
    if len(set(keys)) != len(keys):
        raise ValueError("Configuration contexts require unique active curve identities")
    overlays = curve_parameters or {}
    unknown = set(overlays) - set(keys)
    if unknown:
        raise ValueError(f"Context parameter overrides contain inactive or unmapped curves: {sorted(unknown)}")
    settings = {mapping.key: effective_config(cfg, mapping, overlays.get(mapping.key)) for mapping in active}

    def mapping_payload(mapping: ProductMap) -> dict[str, str]:
        return {name: getattr(mapping, name) for name in (
            "product", "region", "unit", "use", "area", "profile", "eex_file", "hours", "timezone")}

    operations = {name: getattr(cfg, name) for name in (
        "eex_offset_days", "max_stale_days", "day_convention", "weekend_offset", "warmup_days")}
    helpers = {mapping.key: {**mapping_payload(mapping),
                            "model_parameters": model_parameter_payload(settings[mapping.key])}
               for mapping in active}
    records = {}
    for mapping in active:
        document = {
            "schema_version": 1,
            "receiver": mapping_payload(mapping),
            "operations": dict(operations),
            "cross_helpers": [copy.deepcopy(helpers[key]) for key in sorted(helpers) if key != mapping.key]
                if settings[mapping.key].layer_cross else [],
        }
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
        records[mapping.key] = {
            "configuration_context_id": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "document": document,
        }
    return records
