import copy
import json
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from vwaps.shape import apply_shape
from vwaps.tenors import add_months


def _cfg(**changes):
    values = dict(shape_mode="adjust", shape_adjust_originals=False,
                  shape_smoothness_weight=1.0, shape_coherence_weight=10.0,
                  shape_coherence_tolerance=0.01,
                  shape_max_abs_adjustment=10.0, shape_original_weight=10.0)
    values.update(changes)
    return SimpleNamespace(**values)


def _row(month, price, *, kind="Month", original=False, eex=150.0, **extra):
    start = date(2027, month, 1)
    end = add_months(start, {"Month": 1, "Quarter": 3, "Year": 12}.get(kind, 1))
    row = dict(reference_date=date(2026, 9, 30), product="POWER", region="DE",
               unit="EUR/MWh", profile="Base", area="DE", kind=kind,
               delivery_start=start, delivery_end=end, hours=(end-start).days * 24,
               price=float(price), source="own" if original else "eex+local",
               data_origin="original" if original else "estimated", tenor=f"{kind}_{month}",
               estimation_method="none" if original else "local_additive",
               eex_settle=eex, eex_asof=date(2026, 9, 30), confidence=1.0 if original else 0.8)
    row.update(extra)
    return row


def _quarter():
    return [_row(1, 132.24), _row(2, 128.57), _row(3, 130.90, hours=743),
            _row(1, 120, kind="Quarter", original=True)]


def _gap(rows):
    return np.average([row["price"] for row in rows[:3]],
                      weights=[row["hours"] for row in rows[:3]]) - rows[3]["price"]


def test_off_preserves_schema_and_does_not_mutate_input():
    rows = _quarter()
    original = copy.deepcopy(rows)
    result = apply_shape(rows, _cfg(shape_mode="off"))
    assert result == rows == original
    assert result is not rows
    assert all(a is not b for a, b in zip(rows, result))
    assert not any(key.startswith("shape_") for key in result[0])


def test_original_protected_soft_coherence_reduces_gap_without_claiming_equality():
    rows = _quarter()
    original = copy.deepcopy(rows)
    cfg = _cfg(shape_smoothness_weight=0, shape_max_abs_adjustment=100)
    result = apply_shape(rows, cfg)
    assert rows == original
    assert result[3]["price"] == 120
    assert result[3]["source"] == "own"
    assert result[3]["shape_status"] == "original_preserved"
    assert 0 < _gap(result) < _gap(rows)
    # Independently solve the rank-one, unconstrained coherence objective.
    hours = np.array([row["hours"] for row in rows[:3]], dtype=float)
    weights = hours / hours.sum()
    expected = np.array([row["price"] for row in rows[:3]])
    expected -= 10 * weights * _gap(rows) / (1 + 10 * (weights @ weights))
    assert [row["price"] for row in result[:3]] == pytest.approx(expected, abs=1e-7)
    trace = json.loads(result[0]["shape_trace"])
    assert trace["solver"]["converged"]
    assert trace["objective_after"] < trace["objective_before"]
    assert trace["constraints"][0]["type"] == "month_aggregate"
    assert all(np.isnan(row["confidence"]) for row in result[:3])
    assert all(row["source"] == "eex+local+shape" for row in result[:3])


def test_audit_reports_same_proposals_but_preserves_prices_and_provenance():
    rows = _quarter()
    audit = apply_shape(rows, _cfg(shape_mode="audit"))
    adjusted = apply_shape(rows, _cfg())
    for raw, proposed, applied in zip(rows, audit, adjusted):
        for field in ("price", "source", "data_origin", "estimation_method", "confidence"):
            assert proposed[field] == raw[field]
        assert proposed["shape_proposed_price"] == applied["price"]
        assert proposed["shape_adjustment"] == 0
        assert not proposed["shape_original_modified"]
    assert audit[0]["shape_status"] == "audit_proposed"


def test_opt_in_original_changes_use_larger_weight_and_explicit_provenance():
    rows = _quarter()
    light = apply_shape(rows, _cfg(shape_adjust_originals=True, shape_original_weight=1))
    heavy = apply_shape(rows, _cfg(shape_adjust_originals=True, shape_original_weight=100))
    assert abs(heavy[3]["price"] - 120) < abs(light[3]["price"] - 120)
    assert light[3]["source"] == "own+shape"
    assert light[3]["data_origin"] == "estimated"
    assert light[3]["estimation_method"] == "shape_adjusted_original"
    assert light[3]["data_origin_before_shape"] == "original"
    assert light[3]["shape_original_modified"]
    assert np.isnan(light[3]["confidence"])


def test_all_movable_prices_respect_box_including_originals():
    rows = [_row(1, 200), _row(2, -200), _row(3, 200),
            _row(1, 0, kind="Quarter", original=True)]
    output = apply_shape(rows, _cfg(shape_adjust_originals=True, shape_max_abs_adjustment=0.5))
    assert all(abs(row["price"] - raw["price"]) <= 0.5 + 1e-10
               for raw, row in zip(rows, output))
    trace = json.loads(output[0]["shape_trace"])
    assert any(node["bound_hit"] for node in trace["nodes"])
    assert trace["solver"]["converged"]


def test_smoothness_regularizes_basis_and_preserves_eex_seasonality():
    rows = [_row(1, 10, eex=0), _row(2, 110, eex=100), _row(3, 0, eex=-10)]
    output = apply_shape(rows, _cfg())
    assert [row["price"] for row in output] == [10, 110, 0]
    assert all(row["shape_status"] == "unchanged" for row in output)
    assert all(row["source"] == "eex+local" for row in output)


def test_zero_negative_reference_and_prices_are_supported_without_division():
    rows = [_row(1, -10, eex=0), _row(2, -40, eex=-5), _row(3, 0, eex=0)]
    output = apply_shape(rows, _cfg(shape_max_abs_adjustment=100))
    before = rows[0]["price"] - 2*(rows[1]["price"]+5) + rows[2]["price"]
    after = output[0]["price"] - 2*(output[1]["price"]+5) + output[2]["price"]
    assert abs(after) < abs(before)
    assert all(np.isfinite(row["price"]) for row in output)


def test_duplicate_aliases_do_not_add_evidence_and_input_order_does_not_matter():
    rows = _quarter()
    plain = apply_shape(rows, _cfg())
    aliased = rows + [dict(rows[0], tenor="another_month_alias"),
                      dict(rows[3], tenor="another_quarter_alias")]
    extended = apply_shape(list(reversed(aliased)), _cfg())
    by_tenor = {row["tenor"]: row for row in extended}
    for row in plain:
        assert by_tenor[row["tenor"]]["price"] == row["price"]
    assert by_tenor["another_month_alias"]["price"] == plain[0]["price"]
    assert by_tenor["another_quarter_alias"]["price"] == plain[3]["price"]


def test_original_alias_protects_entire_contract():
    rows = [_row(1, 100), _row(2, 150), _row(3, 100),
            _row(2, 150, original=True, tenor="own_alias")]
    output = apply_shape(rows, _cfg())
    assert output[1]["price"] == output[3]["price"] == 150
    assert output[1]["shape_status"] == "original_preserved"


def test_conflicting_aliases_fail_group_closed():
    rows = _quarter() + [_row(1, 777, tenor="bad_alias")]
    output = apply_shape(rows, _cfg())
    assert [row["price"] for row in output] == [row["price"] for row in rows]
    assert {row["shape_status"] for row in output} == {"alias_conflict"}


def test_missing_month_does_not_bridge_smoothness_or_reconstruct_aggregate():
    rows = [_row(1, 100), _row(3, 200), _row(4, 50),
            _row(1, 130, kind="Quarter", original=True)]
    output = apply_shape(rows, _cfg())
    assert [row["price"] for row in output] == [row["price"] for row in rows]
    assert output[-1]["shape_status"] == "incomplete_period"
    assert not json.loads(output[0]["shape_trace"])["constraints"]


def test_missing_eex_blocks_smoothness_but_not_complete_coherence():
    rows = _quarter()
    rows[1]["eex_settle"] = np.nan
    output = apply_shape(rows, _cfg())
    trace = json.loads(output[0]["shape_trace"])
    assert {constraint["type"] for constraint in trace["constraints"]} == {"month_aggregate"}
    assert abs(_gap(output)) < abs(_gap(rows))
    output = apply_shape(rows[:3], _cfg())
    assert output[1]["shape_status"] == "missing_reference"
    assert [row["price"] for row in output] == [row["price"] for row in rows[:3]]


def test_different_eex_asof_does_not_create_mixed_snapshot_smoothness():
    rows = [_row(1, 100), _row(2, 150), _row(3, 100)]
    rows[1]["eex_asof"] = date(2026, 9, 29)
    output = apply_shape(rows, _cfg())
    assert [row["price"] for row in output] == [100, 150, 100]


@pytest.mark.parametrize("field,value", [("product", "OTHER"), ("region", "FR"),
                                        ("unit", "GBP/MWh"), ("profile", "Peak"),
                                        ("reference_date", date(2026, 10, 1))])
def test_different_curves_profiles_dates_cannot_be_combined(field, value):
    rows = [_row(1, 100), _row(2, 150), _row(3, 100, **{field: value})]
    output = apply_shape(rows, _cfg())
    assert [row["price"] for row in output] == [100, 150, 100]


def test_missing_and_unsupported_rows_are_preserved():
    rows = [_row(1, np.nan, source="missing", data_origin="missing"),
            _row(1, 10, kind="BOM"), _row(1, 20, hours=0)]
    output = apply_shape(rows, _cfg())
    assert np.isnan(output[0]["price"])
    assert output[0]["shape_status"] == "missing"
    assert output[1]["price"] == 10
    assert output[1]["shape_status"] == "out_of_scope"
    assert output[2]["shape_status"] == "incomplete_period"


def test_full_year_uses_twelve_months_and_preserves_fixed_originals():
    rows = [_row(month, 110) for month in range(1, 13)]
    rows.append(_row(1, 100, kind="Year", original=True))
    output = apply_shape(rows, _cfg(shape_smoothness_weight=0))
    assert output[-1]["price"] == 100
    assert all(100 < row["price"] < 110 for row in output[:-1])
    incomplete = apply_shape(rows[1:], _cfg(shape_smoothness_weight=0))
    assert incomplete[-1]["shape_status"] == "incomplete_period"


def test_nonconvergent_solver_fails_closed_without_publishing_partial_adjustments(monkeypatch):
    def fail(hessian, gradient, bound):
        return np.full(len(gradient), 0.01), False, 123, 1.0
    monkeypatch.setattr("vwaps.shape._solve_bounded", fail)
    rows = _quarter()
    output = apply_shape(rows, _cfg(shape_adjust_originals=True))
    assert [row["price"] for row in output] == [row["price"] for row in rows]
    assert {row["shape_status"] for row in output} == {"solver_failed"}
    assert all(row["shape_adjustment"] == row["shape_proposed_adjustment"] == 0 for row in output)
    assert not json.loads(output[0]["shape_trace"])["solver"]["converged"]


def test_fixed_incompatible_originals_are_reported_without_changes():
    rows = [_row(month, 140, original=True) for month in range(1, 4)]
    rows.append(_row(1, 100, kind="Quarter", original=True))
    output = apply_shape(rows, _cfg())
    assert [row["price"] for row in output] == [140, 140, 140, 100]
    assert {row["shape_status"] for row in output} == {"original_preserved"}
    constraint = json.loads(output[0]["shape_trace"])["constraints"][-1]
    assert constraint["residual_before"] == pytest.approx(40)
    assert constraint["residual_after"] == pytest.approx(40)


def test_zero_bound_is_rejected_and_zero_weights_produce_no_changes():
    rows = _quarter()
    with pytest.raises(ValueError, match="max_abs_adjustment"):
        apply_shape(rows, _cfg(shape_max_abs_adjustment=0))
    output = apply_shape(rows, _cfg(shape_smoothness_weight=0, shape_coherence_weight=0))
    assert {row["shape_status"] for row in output} == {"no_constraints"}


def test_solver_numeric_exception_restores_whole_curve(monkeypatch):
    def fail(*args, **kwargs):
        raise FloatingPointError("synthetic numeric failure")
    monkeypatch.setattr("vwaps.shape._solve_bounded", fail)
    rows = _quarter()
    output = apply_shape(rows, _cfg(shape_adjust_originals=True))
    assert [row["price"] for row in output] == [row["price"] for row in rows]
    assert [row["source"] for row in output] == [row["source"] for row in rows]
    assert {row["shape_status"] for row in output} == {"solver_failed"}
    assert all(row["shape_proposed_adjustment"] == 0 for row in output)
    assert all("shape_coherence_outside_tolerance" in row["flag"] for row in output)
    assert json.loads(output[0]["shape_trace"])["coherence_within_tolerance_after"] is False


def test_large_finite_prices_do_not_escape_bounds_or_break_json_traces():
    rows = [_row(1, -1e308, eex=1e308), _row(2, 1e308, eex=-1e308),
            _row(3, -1e308, eex=1e308)]
    output = apply_shape(rows, _cfg())
    assert all(np.isfinite(row["price"]) for row in output)
    assert [row["price"] for row in output] == [row["price"] for row in rows]
    for row in output:
        json.loads(row["shape_trace"], parse_constant=lambda value: pytest.fail(value))


def test_unconstrained_joint_fit_matches_independent_dense_linear_solve():
    rows = _quarter()
    smooth = np.array([1.0, -2.0, 1.0, 0.0])
    hours = np.array([row["hours"] for row in rows[:3]], dtype=float)
    aggregate = np.append(hours / hours.sum(), -1.0)
    fidelity = np.diag([1.0, 1.0, 1.0, 10.0])
    hessian = fidelity + np.outer(smooth, smooth) + 10*np.outer(aggregate, aggregate)
    raw = np.array([row["price"] for row in rows])
    expected = np.linalg.solve(hessian, fidelity @ raw)
    output = apply_shape(rows, _cfg(shape_adjust_originals=True, shape_max_abs_adjustment=100))
    assert [row["price"] for row in output] == pytest.approx(expected, abs=1e-7)


def test_bounded_solver_satisfies_independent_kkt_conditions():
    from vwaps.shape import _solve_bounded
    hessian = np.array([[4.0, 1.0, -0.5], [1.0, 3.0, 0.0], [-0.5, 0.0, 2.0]])
    gradient = np.array([10.0, -4.0, 0.1])
    delta, converged, _, _ = _solve_bounded(hessian, gradient, 0.5)
    assert converged
    derivative = hessian @ delta + gradient
    for price, slope in zip(delta, derivative):
        if price == -0.5:
            assert slope >= -1e-9
        elif price == 0.5:
            assert slope <= 1e-9
        else:
            assert abs(slope) < 1e-9


def test_nonparticipating_huge_contract_cannot_change_month_solution():
    rows = [_row(1, 100), _row(2, 150), _row(3, 100)]
    expected = apply_shape(rows, _cfg())
    output = apply_shape(rows + [_row(7, 1e200, kind="Quarter", original=True)], _cfg())
    assert [row["price"] for row in output[:3]] == [row["price"] for row in expected]


def test_small_independent_block_is_not_ignored_beside_large_block():
    rows = [_row(1, 100), _row(2, 150), _row(3, 100)]
    distant = [_row(7, 1e12), _row(8, 2e12), _row(9, 1e12)]
    expected = apply_shape(rows, _cfg())
    output = apply_shape(rows + distant, _cfg())
    assert [row["price"] for row in output[:3]] == pytest.approx(
        [row["price"] for row in expected], abs=1e-7)
    assert all(row["shape_status"] != "solver_failed" for row in output)
    assert [row["shape_adjustment"] for row in output[3:]] == [10.0, -10.0, 10.0]


@pytest.mark.parametrize("changes", [dict(shape_original_weight=0.5),
                                      dict(shape_coherence_weight="1"),
                                      dict(shape_smoothness_weight=True)])
def test_direct_api_uses_same_strict_validation_as_config(changes):
    with pytest.raises(ValueError):
        apply_shape(_quarter(), _cfg(**changes))


def test_remaining_quarter_gap_flags_only_affected_months_and_quarter():
    rows = _quarter() + [_row(7, 100, original=True)]
    output = apply_shape(rows, _cfg())
    flag = "shape_coherence_outside_tolerance"
    assert all(flag in row["flag"] for row in output[:4])
    assert flag not in output[4].get("flag", "")
    trace = json.loads(output[0]["shape_trace"])
    assert trace["parameters"]["coherence_tolerance"] == 0.01
    assert trace["coherence_within_tolerance_before"] is False
    assert trace["coherence_within_tolerance_after"] is False
    constraint = next(item for item in trace["constraints"] if item["type"] == "month_aggregate")
    assert constraint["within_tolerance_before"] is False
    assert constraint["within_tolerance_after"] is False
    assert constraint["residual_after"] == pytest.approx(_gap(output[:4]))
    assert output[3]["shape_status"] == "original_preserved"


def test_coherence_tolerance_is_diagnostic_and_does_not_change_solution():
    rows = _quarter()
    strict = apply_shape(rows, _cfg())
    loose = apply_shape(rows, _cfg(shape_coherence_tolerance=3.0))
    assert [row["price"] for row in strict] == [row["price"] for row in loose]
    assert all("shape_coherence_outside_tolerance" not in row.get("flag", "") for row in loose)
    trace = json.loads(loose[0]["shape_trace"])
    assert trace["coherence_within_tolerance_before"] is False
    assert trace["coherence_within_tolerance_after"] is True


def test_near_coherence_passes_default_tolerance():
    rows = [_row(month, 120.005) for month in range(1, 4)]
    rows.append(_row(1, 120, kind="Quarter", original=True))
    output = apply_shape(rows, _cfg())
    trace = json.loads(output[0]["shape_trace"])
    assert trace["coherence_within_tolerance_before"] is True
    assert trace["coherence_within_tolerance_after"] is True
    assert all("shape_coherence_outside_tolerance" not in row.get("flag", "") for row in output)


def test_no_aggregate_reports_null_coherence_diagnostics():
    rows = [_row(1, 100), _row(2, 150), _row(3, 100)]
    output = apply_shape(rows, _cfg())
    trace = json.loads(output[0]["shape_trace"])
    assert trace["coherence_within_tolerance_before"] is None
    assert trace["coherence_within_tolerance_after"] is None
    assert all("coherence_tolerance" not in row.get("flag", "") for row in output)


def test_audit_flags_proposal_gap_while_preserving_actual_prices():
    rows = _quarter()
    rows[0]["flag"] = "existing_warning"
    output = apply_shape(rows, _cfg(shape_mode="audit"))
    assert [row["price"] for row in output] == [row["price"] for row in rows]
    assert all("shape_proposal_outside_coherence_tolerance" in row["flag"] for row in output)
    assert output[0]["flag"].startswith("existing_warning;")
    trace = json.loads(output[0]["shape_trace"])
    proposals = [dict(row, price=row["shape_proposed_price"]) for row in output]
    constraint = next(item for item in trace["constraints"] if item["type"] == "month_aggregate")
    assert constraint["residual_after"] == pytest.approx(_gap(proposals))
    assert trace["coherence_within_tolerance_after"] is False


def test_failed_solver_flags_unadjusted_aggregate_and_reports_actual_objective(monkeypatch):
    def fail(hessian, gradient, bound):
        return np.full(len(gradient), 0.01), False, 123, 1.0
    monkeypatch.setattr("vwaps.shape._solve_bounded", fail)
    rows = _quarter()
    output = apply_shape(rows, _cfg())
    assert [row["price"] for row in output] == [row["price"] for row in rows]
    assert all("shape_coherence_outside_tolerance" in row["flag"] for row in output)
    trace = json.loads(output[0]["shape_trace"])
    constraint = next(item for item in trace["constraints"] if item["type"] == "month_aggregate")
    assert constraint["residual_after"] == pytest.approx(_gap(rows))
    assert trace["objective_after"] == pytest.approx(trace["objective_before"])
