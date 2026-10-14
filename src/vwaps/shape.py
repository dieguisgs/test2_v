"""Optional bounded, auditable adjustment of completed monthly curves.

The reference for smoothness is each month's current ``eex_settle``. This
module does not substitute the temporal EEX fallback curve for that reference.
It uses no historical observations and never creates a price for a missing row.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np

from vwaps.config import Config, validate_shape_config
from vwaps.tenors import add_months


@dataclass
class _Node:
    key: tuple[str, date, date]
    rows: list[int]
    price: float
    hours: float
    original: bool
    reference: float | None
    asof: str


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _token(value) -> str:
    return "" if value is None or str(value) in ("nan", "NaT", "<NA>") else str(value)


def _is_original(row: dict) -> bool:
    return row.get("data_origin") == "original" or row.get("source") == "own"


def _period_key(row: dict) -> tuple[str, date, date] | None:
    kind = row.get("kind")
    start, end = _date(row.get("delivery_start")), _date(row.get("delivery_end"))
    if kind not in ("Month", "Quarter", "Year") or start is None or end is None:
        return None
    length = {"Month": 1, "Quarter": 3, "Year": 12}[kind]
    if (start.day != 1 or end != add_months(start, length)
            or kind == "Quarter" and start.month not in (1, 4, 7, 10)
            or kind == "Year" and start.month != 1):
        return None
    return kind, start, end


def _label(node: _Node) -> str:
    return f"{node.key[0]}:{node.key[1].isoformat()}/{node.key[2].isoformat()}"


def _solve_bounded(
    hessian: np.ndarray, gradient: np.ndarray, bound: float,
    *, max_iterations: int = 20000,
) -> tuple[np.ndarray, bool, int, float]:
    """Solve a strictly convex box problem by deterministic coordinate descent.

    The objective is ``d.T @ H @ d + 2*g.T @ d``. A projected gradient
    residual checks the box KKT conditions before declaring convergence.
    """
    size = len(gradient)
    delta = np.zeros(size, dtype=float)
    if not size:
        return delta, True, 0, 0.0
    diagonal = np.diag(hessian)
    if (not np.isfinite(hessian).all() or not np.isfinite(gradient).all()
            or not np.isfinite(diagonal).all() or np.any(diagonal <= 0)
            or not math.isfinite(bound) or bound < 0):
        return delta, False, 0, math.inf
    residual = math.inf
    for iteration in range(1, max_iterations + 1):
        for index in range(size):
            derivative = float(hessian[index] @ delta + gradient[index])
            delta[index] = np.clip(delta[index] - derivative / diagonal[index], -bound, bound)
        derivative = hessian @ delta + gradient
        # Components of the gradient that point outside a bound satisfy KKT.
        projected = derivative.copy()
        projected[(delta <= -bound) & (derivative >= 0)] = 0.0
        projected[(delta >= bound) & (derivative <= 0)] = 0.0
        residual = float(np.max(np.abs(projected)))
        if not np.isfinite(delta).all() or not math.isfinite(residual):
            return np.zeros(size), False, iteration, math.inf
        # A separate relative check per coordinate avoids ignoring a small
        # independent block when another block contains much larger prices.
        tolerance = 1e-11 * (np.abs(hessian) @ np.abs(delta) + np.abs(gradient))
        if np.all(np.abs(projected) <= tolerance):
            return delta, True, iteration, residual
    return delta, False, max_iterations, residual


def _json(trace: dict) -> str:
    return json.dumps(trace, allow_nan=False, sort_keys=True, separators=(",", ":"))


def apply_shape(rows: list[dict], cfg: Config) -> list[dict]:
    """Return copied rows with an optional audit or bounded shape adjustment.

    ``off`` preserves the schema exactly. ``audit`` records proposals while
    preserving prices and provenance. ``adjust`` applies successful proposals.
    Missing prices are never inferred here. Original contracts are fixed unless
    ``shape_adjust_originals`` explicitly enables their adjustment.
    """
    result = [dict(row) for row in rows]
    mode = getattr(cfg, "shape_mode", "off")
    if mode == "off":
        return result
    validate_shape_config(cfg)
    params = {
        "mode": mode,
        "adjust_originals": getattr(cfg, "shape_adjust_originals", False),
        "smoothness_weight": getattr(cfg, "shape_smoothness_weight", 1.0),
        "coherence_weight": getattr(cfg, "shape_coherence_weight", 10.0),
        "coherence_tolerance": getattr(cfg, "shape_coherence_tolerance", 0.01),
        "max_abs_adjustment": getattr(cfg, "shape_max_abs_adjustment", 10.0),
        "original_weight": getattr(cfg, "shape_original_weight", 10.0),
    }
    for name in ("smoothness_weight", "coherence_weight", "coherence_tolerance",
                 "max_abs_adjustment", "original_weight"):
        params[name] = float(params[name])

    groups: dict[tuple, list[int]] = {}
    for index, row in enumerate(result):
        price = _number(row.get("price"))
        row.update(
            price_before_shape=row.get("price", math.nan),
            source_before_shape=row.get("source", ""),
            estimation_method_before_shape=row.get("estimation_method", ""),
            data_origin_before_shape=row.get("data_origin", ""),
            shape_mode=mode, shape_status="no_constraints", shape_adjustment=0.0,
            shape_proposed_price=price if price is not None else math.nan,
            shape_proposed_adjustment=0.0, shape_original_modified=False,
            shape_trace="",
        )
        identity = tuple(_token(row.get(field)) for field in (
            "reference_date", "product", "region", "unit", "profile", "area"))
        groups.setdefault(identity, []).append(index)
    for indices in groups.values():
        before = {index: dict(result[index]) for index in indices}
        try:
            _apply_group(result, indices, params)
        except (ArithmeticError, ValueError, np.linalg.LinAlgError):
            # A numerical failure must never leave half of a curve adjusted.
            trace = {"parameters": params, "reference": "eex_settle",
                     "coherence_within_tolerance_before": None,
                     "coherence_within_tolerance_after": None,
                     "solver": {"converged": False, "reason": "numeric_failure"}}
            for index in indices:
                status = result[index].get("shape_status", "no_constraints")
                result[index].clear()
                result[index].update(before[index])
                result[index]["shape_status"] = (
                    status if status in ("missing", "out_of_scope", "incomplete_period") else "solver_failed")
            _attach_trace(result, indices, trace)
    return result


def _apply_group(rows: list[dict], indices: list[int], params: dict) -> None:
    aliases: dict[tuple, list[int]] = {}
    for index in indices:
        row = rows[index]
        if _number(row.get("price")) is None or row.get("source") == "missing":
            row["shape_status"] = "missing"
        elif row.get("kind") not in ("Month", "Quarter", "Year"):
            row["shape_status"] = "out_of_scope"
        elif _period_key(row) is None or (_number(row.get("hours")) or 0) <= 0:
            row["shape_status"] = "incomplete_period"
        else:
            aliases.setdefault(_period_key(row), []).append(index)
    trace = {
        "parameters": params, "reference": "eex_settle",
        "reference_description": "Current EEX reference, not the temporal fallback curve",
        "original_policy": "movable_with_penalty" if params["adjust_originals"] else "fixed",
        "smoothness_grid": "three consecutive calendar months, uniform month index",
        "coherence_within_tolerance_before": None,
        "coherence_within_tolerance_after": None,
        "nodes": [], "constraints": [], "solver": {"converged": True, "iterations": 0},
    }
    nodes = []
    for key in sorted(aliases):
        members = aliases[key]
        prices = [_number(rows[i].get("price")) for i in members]
        hours = [_number(rows[i].get("hours")) for i in members]
        if len(set(prices)) != 1 or len(set(hours)) != 1:
            for member_list in aliases.values():
                for index in member_list:
                    rows[index]["shape_status"] = "alias_conflict"
            trace["solver"] = {"converged": False, "reason": "conflicting_alias_prices_or_hours"}
            _attach_trace(rows, indices, trace)
            return
        references = [_number(rows[i].get("eex_settle")) for i in members]
        asofs = {_token(rows[i].get("eex_asof")) for i in members}
        reference = references[0] if None not in references and len(set(references)) == 1 else None
        if len(asofs) != 1:
            reference = None
        nodes.append(_Node(key, members, prices[0], hours[0],
                           any(_is_original(rows[i]) for i in members), reference,
                           next(iter(asofs)) if len(asofs) == 1 else ""))
    monthly = {node.key[1]: i for i, node in enumerate(nodes) if node.key[0] == "Month"}
    terms: list[tuple[str, dict[int, float], float, float]] = []
    if params["smoothness_weight"] > 0:
        for start, first in sorted(monthly.items()):
            following = [monthly.get(add_months(start, offset)) for offset in (1, 2)]
            if any(index is None for index in following):
                continue
            triple = [first, *following]
            if any(nodes[i].reference is None for i in triple):
                continue
            if not nodes[triple[0]].asof or len({nodes[i].asof for i in triple}) != 1:
                continue
            # Store the reference vector separately to avoid subtracting large
            # unscaled prices while constructing the objective.
            terms.append(("basis_second_difference", dict(zip(triple, (1.0, -2.0, 1.0))),
                          params["smoothness_weight"], 0.0))
    incomplete = set()
    if params["coherence_weight"] > 0:
        for index, node in enumerate(nodes):
            if node.key[0] == "Month":
                continue
            count = 3 if node.key[0] == "Quarter" else 12
            children = [monthly.get(add_months(node.key[1], offset)) for offset in range(count)]
            if any(child is None for child in children):
                incomplete.add(index)
                continue
            # Normalize by max first so a finite hours input cannot overflow.
            hour_scale = max(nodes[child].hours for child in children)
            total = math.fsum(nodes[child].hours / hour_scale for child in children)
            coefficients = {child: nodes[child].hours / hour_scale / total for child in children}
            coefficients[index] = -1.0
            terms.append(("month_aggregate", coefficients, params["coherence_weight"], 0.0))
    involved = {index for _, coefficients, _, _ in terms for index in coefficients}
    for index, node in enumerate(nodes):
        if index not in involved:
            status = ("incomplete_period" if index in incomplete else
                      "missing_reference" if node.key[0] == "Month" and node.reference is None else
                      "no_constraints")
            for member in node.rows:
                rows[member]["shape_status"] = status
    if not terms:
        trace["nodes"] = [{"period": _label(node), "price_before": node.price,
                           "price_proposed": node.price, "eex_reference": node.reference,
                           "original": node.original, "alias_count": len(node.rows),
                           "bound_hit": False} for node in nodes]
        trace["solver"]["reason"] = "no_applicable_constraints"
        _attach_trace(rows, indices, trace)
        return

    raw = np.array([node.price for node in nodes], dtype=float)
    scale = max(1.0, *(abs(nodes[i].price) for i in involved),
                *(abs(nodes[i].reference) for i in involved if nodes[i].reference is not None))
    weight_scale = max(1.0, params["original_weight"],
                       params["smoothness_weight"], params["coherence_weight"])
    movable = [i for i in sorted(involved) if params["adjust_originals"] or not nodes[i].original]
    position = {node: pos for pos, node in enumerate(movable)}
    fidelity = np.array([params["original_weight"] if nodes[i].original else 1.0
                         for i in movable]) / weight_scale
    hessian = np.diag(fidelity)
    gradient = np.zeros(len(movable))
    constraints = []
    baseline = raw / scale
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        for kind, coefficients, weight, _ in terms:
            residual = math.fsum(coefficient * baseline[i] for i, coefficient in coefficients.items())
            if kind == "basis_second_difference":
                residual -= math.fsum(coefficient * (nodes[i].reference / scale)
                                      for i, coefficient in coefficients.items())
            vector = np.zeros(len(movable))
            for index, coefficient in coefficients.items():
                if index in position:
                    vector[position[index]] = coefficient
            normalized_weight = weight / weight_scale
            hessian += normalized_weight * np.outer(vector, vector)
            gradient += normalized_weight * residual * vector
            constraints.append((kind, coefficients, normalized_weight, residual, vector))
        try:
            delta, converged, iterations, residual = _solve_bounded(
                hessian, gradient, params["max_abs_adjustment"] / scale)
        except (ArithmeticError, ValueError, np.linalg.LinAlgError):
            # Preserve the constraints so failed solves can still diagnose the
            # unchanged curve's aggregate discrepancies.
            delta, converged, iterations, residual = np.zeros(len(movable)), False, 0, math.inf
        before_objective = math.fsum(weight * value ** 2 for _, _, weight, value, _ in constraints)
        after_objective = float(np.sum(fidelity * delta ** 2)) + math.fsum(
            weight * (value + float(vector @ delta)) ** 2
            for _, _, weight, value, vector in constraints)
        proposed = raw.copy()
        for pos, index in enumerate(movable):
            proposed[index] = raw[index] + delta[pos] * scale
    valid = (converged and np.isfinite(proposed).all() and math.isfinite(after_objective)
             and after_objective <= before_objective + 1e-9 * max(1.0, before_objective))
    if valid:
        # Verify the public price movement as well as the solver's scaled box.
        valid = all(abs(proposed[i] - raw[i]) <= params["max_abs_adjustment"]
                    + 1e-9 * max(1.0, params["max_abs_adjustment"]) for i in movable)
    trace["solver"] = {
        "method": "bounded_coordinate_descent", "converged": bool(valid),
        "iterations": iterations, "kkt_residual": residual if math.isfinite(residual) else None,
    }
    trace["objective_before"] = before_objective
    trace["objective_after"] = after_objective if math.isfinite(after_objective) else None
    trace["objective_price_scale"] = scale
    trace["objective_weight_scale"] = weight_scale
    trace["objective_units"] = "objective divided by price_scale^2 and weight_scale"
    if not valid:
        proposed = raw.copy()
        trace["solver"]["reason"] = "nonconvergence_or_failed_numeric_validation"
    for index, node in enumerate(nodes):
        difference = float(proposed[index] - raw[index])
        changed = difference != 0.0 and abs(difference) > max(1e-10, 4 * math.ulp(float(raw[index])))
        if not changed:
            proposed[index], difference = raw[index], 0.0
        hit_bound = (index in position and params["max_abs_adjustment"] > 0
                     and math.isclose(abs(difference), params["max_abs_adjustment"], rel_tol=1e-8, abs_tol=1e-9))
        trace["nodes"].append({
            "period": _label(node), "price_before": node.price, "price_proposed": float(proposed[index]),
            "eex_reference": node.reference, "original": node.original,
            "alias_count": len(node.rows), "bound_hit": hit_bound,
        })
        for member in node.rows:
            row = rows[member]
            row["shape_proposed_price"] = float(proposed[index])
            row["shape_proposed_adjustment"] = difference
            if index not in involved:
                continue
            row["shape_status"] = ("solver_failed" if not valid else
                                   "original_preserved" if node.original and not params["adjust_originals"] else
                                   "audit_proposed" if changed and params["mode"] == "audit" else
                                   "adjusted" if changed else "unchanged")
            if not valid or not changed or params["mode"] == "audit":
                continue
            original = _is_original(row)
            row.update(price=float(proposed[index]), shape_adjustment=difference,
                       shape_original_modified=original, data_origin="estimated", confidence=math.nan)
            if original:
                row.update(source="own+shape", estimation_method="shape_adjusted_original")
            else:
                row["source"] = f"{row.get('source', '')}+shape"
                row["estimation_method"] = f"{row.get('estimation_method', '')}+shape"

    # Diagnose the actual published proposals, including no-op suppression and
    # fail-closed recovery. The tolerance is a reporting threshold, never an
    # optimization constraint or a promise that every aggregate can be matched.
    after_values = []
    coherence_before, coherence_after = [], []
    for kind, coefficients, weight, value, _ in constraints:
        after = math.fsum(coefficient * (proposed[i] / scale)
                          for i, coefficient in coefficients.items())
        if kind == "basis_second_difference":
            after -= math.fsum(coefficient * (nodes[i].reference / scale)
                              for i, coefficient in coefficients.items())
        after_values.append(weight * after ** 2)
        before_price, after_price = _number(value * scale), _number(after * scale)
        constraint = {
            "type": kind, "coefficients": {_label(nodes[i]): c for i, c in coefficients.items()},
            "weight": weight * weight_scale,
            "residual_before": before_price, "residual_after": after_price,
        }
        if kind == "month_aggregate":
            threshold = params["coherence_tolerance"] + 1e-10
            within_before = before_price is not None and abs(before_price) <= threshold
            within_after = after_price is not None and abs(after_price) <= threshold
            constraint.update(within_tolerance_before=within_before, within_tolerance_after=within_after)
            coherence_before.append(within_before)
            coherence_after.append(within_after)
            if not within_after:
                flag = ("shape_proposal_outside_coherence_tolerance" if params["mode"] == "audit" else
                        "shape_coherence_outside_tolerance")
                for index in coefficients:
                    for member in nodes[index].rows:
                        flags = [item for item in _token(rows[member].get("flag")).split(";") if item]
                        if flag not in flags:
                            flags.append(flag)
                        rows[member]["flag"] = ";".join(flags)
        trace["constraints"].append(constraint)
    actual_delta = np.array([(proposed[i] - raw[i]) / scale for i in movable])
    trace["objective_after"] = float(np.sum(fidelity * actual_delta ** 2)) + math.fsum(after_values)
    if coherence_before:
        trace["coherence_within_tolerance_before"] = all(coherence_before)
        trace["coherence_within_tolerance_after"] = all(coherence_after)
    _attach_trace(rows, indices, trace)


def _attach_trace(rows: list[dict], indices: list[int], trace: dict) -> None:
    encoded = _json(trace)
    for index in indices:
        rows[index]["shape_trace"] = encoded
