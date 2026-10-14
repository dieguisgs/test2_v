"""Context provenance detects changes to causal CROSS dependencies."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json

import pandas as pd
import pytest

from vwaps.config import load_config
from vwaps.fill import CurveFiller
from vwaps.io_eex import EexBook, REQUIRED
from vwaps.mapping import ProductMap
from vwaps.product_config import configuration_context_records, configuration_record
from vwaps.tenors import resolve_tenor


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    return replace(load_config(path), configuration_mode="individual", tenors=["M+1"],
                   basis_mode="additive", layer_local=False, layer_hist="on", layer_cross=True,
                   cross_min_obs=1.0, warmup_days=0)


def mapping(product, use="fill", **parameters):
    return ProductMap(product, use, "DE", "Base", "synthetic.csv", "Base", "Europe/Berlin",
                      region="North", unit="EUR/MWh", parameter_overrides=parameters)


def cross_data():
    target, helper = mapping("P"), mapping("H", "helper")
    days = [date(2026, 9, 21) + timedelta(days=i) for i in range(4)]
    rows, books = [], {}
    for m, scale in ((target, 1), (helper, 2)):
        publications = []
        for step, day in enumerate(days, 1):
            period = resolve_tenor("M+1", day)
            publications.append((day, period.kind, period.start, 80.0 * scale * step))
            if m == helper or day != days[-1]:
                rows.append(dict(date=day, product=m.product, region=m.region, unit=m.unit,
                                 tenor="M+1", vwap=90.0 * scale * step, volume=20.0))
        books[m.key] = EexBook(pd.DataFrame(publications, columns=REQUIRED))
    return target, helper, days, pd.DataFrame(rows), books


def test_changed_helper_can_change_receiver_price_and_is_detected_by_context(cfg):
    target, helper, days, own, books = cross_data()
    original = CurveFiller(cfg, own, [target, helper], books).run(days[-1], days[-1])
    changed_helper = replace(helper, parameter_overrides={"min_volume": 1000.0})
    altered = CurveFiller(cfg, own, [target, changed_helper], books).run(days[-1], days[-1])
    assert not original.errors and not altered.errors
    first, second = original.filled.iloc[0], altered.filled.iloc[0]
    assert first.price == pytest.approx(360.0)
    assert second.price == pytest.approx(331.9641644516707)
    assert first.configuration_id == second.configuration_id
    assert first.configuration_context_id != second.configuration_context_id
    document = altered.configuration_contexts[second.configuration_context_id]
    assert document["cross_helpers"][0]["model_parameters"]["min_volume"] == 1000.0
    assert document["cross_helpers"][0]["product"] == "H"


def test_context_ignores_other_curves_when_receiver_cross_is_disabled(cfg):
    target, helper = mapping("P", layer_cross=False), mapping("H", "helper")
    first = configuration_context_records(cfg, [target, helper])[target.key]
    changed = replace(helper, parameter_overrides={"min_volume": 1000.0}, hours="Peak")
    second = configuration_context_records(cfg, [target, changed])[target.key]
    isolated = configuration_context_records(cfg, [target])[target.key]
    assert first == second == isolated
    assert first["document"]["cross_helpers"] == []


def test_context_excludes_receiver_parameters_but_contains_effective_runtime_helpers(cfg):
    target, helper = mapping("P"), mapping("H", "helper", min_volume=10.0)
    initial = configuration_context_records(cfg, [target, helper])[target.key]
    expanded = replace(target, parameter_overrides={"tenors": ["M+1", "M+2"], "tau_log": .2})
    assert configuration_context_records(cfg, [expanded, helper])[target.key] == initial
    overrides = {helper.key: {"min_volume": 500.0}}
    actual = configuration_context_records(cfg, [target, helper], overrides)[target.key]
    assert actual["configuration_context_id"] != initial["configuration_context_id"]
    assert actual["document"]["cross_helpers"][0]["model_parameters"]["min_volume"] == 500.0
    overrides[helper.key]["min_volume"] = 1.0
    assert actual["document"]["cross_helpers"][0]["model_parameters"]["min_volume"] == 500.0


def test_global_context_ignores_mapping_parameter_cells_but_honors_candidate_overrides(cfg):
    cfg = replace(cfg, configuration_mode="global")
    target, helper = mapping("P"), mapping("H", "helper")
    altered = replace(helper, parameter_overrides={"min_volume": 1000.0})
    baseline = configuration_context_records(cfg, [target, helper])[target.key]
    assert configuration_context_records(cfg, [target, altered])[target.key] == baseline
    candidate = configuration_context_records(cfg, [target, helper],
                                               {helper.key: {"min_volume": 1000.0}})[target.key]
    assert candidate != baseline


@pytest.mark.parametrize("parameters", [
    {"eex_offset_days": -1}, {"max_stale_days": 3}, {"day_convention": "business"},
    {"weekend_offset": 1}, {"warmup_days": 20},
])
def test_operational_policy_changes_context_without_changing_model_id(cfg, parameters):
    target = mapping("P", layer_cross=False)
    changed = replace(cfg, **parameters)
    assert configuration_record(cfg, target)["configuration_id"] == configuration_record(changed, target)["configuration_id"]
    assert configuration_context_records(cfg, [target]) != configuration_context_records(changed, [target])


@pytest.mark.parametrize("parameters", [
    {"eex_file": "another.csv"}, {"hours": "Peak"}, {"timezone": "Europe/London"},
])
def test_receiver_delivery_mapping_changes_context(cfg, parameters):
    target = mapping("P", layer_cross=False)
    changed = replace(target, **parameters)
    assert configuration_context_records(cfg, [target])[target.key] != configuration_context_records(cfg, [changed])[target.key]


def test_context_documents_are_deterministic_and_exclude_inactive_curves(cfg):
    target, first, second = mapping("P"), mapping("A", "helper"), mapping("B")
    disabled = mapping("OFF", "off", min_volume=1000.0)
    maps = [target, first, second]
    snapshot = deepcopy(maps)
    records = configuration_context_records(cfg, maps)
    assert records == configuration_context_records(cfg, [second, disabled, first, target])
    assert maps == snapshot
    document = records[target.key]["document"]
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert hashlib.sha256(encoded.encode("utf-8")).hexdigest() == records[target.key]["configuration_context_id"]
    assert [helper["product"] for helper in document["cross_helpers"]] == ["A", "B"]


@pytest.mark.parametrize("offset", [0, -1])
def test_output_loo_and_context_artifact_refer_to_forecast_policy_not_trainer(cfg, offset):
    cfg = replace(cfg, eex_offset_days=offset)
    target, helper, days, own, books = cross_data()
    engine = CurveFiller(cfg, own, [target, helper], books)
    result = engine.run(days[-2], days[-1], loo=True)
    assert not result.errors and not result.loo.empty
    expected = configuration_context_records(cfg, [target, helper])[target.key]
    expected_id = expected["configuration_context_id"]
    assert result.filled.configuration_context_id.eq(expected_id).all()
    assert result.loo.configuration_context_id.eq(expected_id).all()
    assert result.configuration_contexts == {expected_id: expected["document"]}
    assert result.configuration_contexts[expected_id]["operations"]["eex_offset_days"] == offset
    result.configuration_contexts[expected_id]["operations"]["eex_offset_days"] = -99
    repeated = engine.run(days[-2], days[-1], loo=True)
    assert repeated.configuration_contexts[expected_id] == expected["document"]
