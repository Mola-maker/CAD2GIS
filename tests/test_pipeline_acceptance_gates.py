"""Synthetic acceptance checks: missing evidence must not become VERIFIED."""

from types import SimpleNamespace

import pytest

from cad2gis.cad2gis_v3 import pipeline
from cad2gis.cad2gis_v3.config import DiagnosticGate, _legacy_apd_expectations
from cad2gis.cad2gis_v3.run_status import RunStatus
from cad2gis.verify.matrix import _normalise_dimension_evidence


def tolerance(value=0.25, operator="le"):
    return DiagnosticGate(("span_measurement_max_abs_error_m",), operator, value)


def dimensions(measured=4, unmeasured=0, error=0.1):
    return {
        "route_segments_with_span_dimension": measured,
        "route_segments_without_span_dimension": unmeasured,
        "span_measurement_max_abs_error_m": error,
    }


def status(validation):
    return pipeline._derive_conversion_status(
        entities=[object()], ingest_diagnostics={}, semantic_diagnostics={},
        style_coverage={}, unresolved=[], terminal_accounting={},
        georeference_diagnostics={}, validation_summary=validation,
    )


def complete_validation():
    return {domain: {"passed": True} for domain in (
        "source_geometry", "topology", "segment_delivery", "measurements",
        "coordinate_accuracy", "reviewer_acceptance", "engineering_review",
    )}


def test_empty_reviewer_gates_and_counts_are_unverified():
    gate = pipeline._evaluate_diagnostic_gates("topology", (), {"passed": True})
    counts = pipeline._validate_declared_counts("delivery", {}, {"CABLE": 5})
    for result in (gate, counts):
        assert result["passed"] is None
        assert result["status"] == "not_verified"
    summary = pipeline._reviewer_acceptance_summary({}, counts)
    assert summary["passed"] is None
    assert "delivery" in summary["unverified_domains"]
    validation = complete_validation()
    validation["reviewer_acceptance"] = summary
    assert status(validation) == RunStatus.CONDITIONAL


def test_declared_gates_and_source_conservation_still_fail_closed():
    with pytest.raises(RuntimeError, match="contract gates failed"):
        pipeline._evaluate_diagnostic_gates("measurements", [tolerance()], dimensions(error=2.0))
    with pytest.raises(RuntimeError, match="census mismatch"):
        pipeline._validate_exact_counts("source", {"model_entities": 3}, {"model_entities": 2})
    with pytest.raises(RuntimeError, match="census mismatch"):
        pipeline._validate_declared_counts("delivery", {"CABLE": 3}, {"CABLE": 2})


@pytest.mark.parametrize("diagnostics,gates,reason", [
    (dimensions(measured=0, unmeasured=33, error=0), [tolerance()], "no_span_dimensions"),
    (dimensions(), [], "no_reviewer_declared_measurement_tolerance"),
    (dimensions(unmeasured=28), [tolerance()], "unmeasured_route_segments"),
    (dimensions(), [tolerance(0, "ge")], "no_reviewer_declared_measurement_tolerance"),
    ({}, [], "missing_or_invalid_dimension_coverage"),
    ({"route_segments_with_span_dimension": 4, "route_segments_without_span_dimension": 0},
     [tolerance()], "missing_measurement_diagnostics"),
])
def test_unverified_measurements_do_not_inherit_length_closure(diagnostics, gates, reason):
    measurements = pipeline._measurement_validation_summary(diagnostics, gates)
    assert measurements["passed"] is None
    assert measurements["status"] == "not_verified"
    assert reason in measurements["reason_codes"]
    validation = complete_validation()
    validation["measurements"] = measurements
    assert status(validation) == RunStatus.CONDITIONAL
    # The read-only acceptance matrix must not let a separate closure PASS
    # erase unverified engineering measurements.
    matrix_status, _ = _normalise_dimension_evidence(
        {"validation": validation}, "length",
    )
    assert matrix_status == "WATCH"


@pytest.mark.parametrize("error,passed", [(0.2, True), (0.25, True), (0.25001, False), (float("nan"), False)])
def test_explicit_tolerance_uses_actual_dimension_diagnostics(error, passed):
    summary = pipeline._measurement_validation_summary(dimensions(error=error), [tolerance()])
    assert summary["passed"] is passed
    validation = complete_validation()
    validation["measurements"] = summary
    assert status(validation) == (RunStatus.VERIFIED if passed else RunStatus.UNSAFE)


@pytest.mark.parametrize("error", [True, -0.01, "0.1"])
def test_invalid_measurement_metrics_cannot_pass_a_declared_gate(error):
    summary = pipeline._measurement_validation_summary(dimensions(error=error), [tolerance(1, "eq")])
    assert summary["passed"] is False


def test_an_empty_pass_object_cannot_satisfy_reviewer_acceptance():
    gates = {domain: {"passed": True, "gates": []} for domain in (
        "source_inventory", "features", "source_geometry", "topology", "segments",
    )}
    assert pipeline._reviewer_acceptance_summary(gates, {"passed": True, "counts": {}})["passed"] is None


def test_explicit_complete_review_contracts_can_pass():
    count_gate = pipeline._validate_exact_counts("source", {"model_entities": 1}, {"model_entities": 1})
    gate = pipeline._evaluate_diagnostic_gates("topology", [DiagnosticGate(("unresolved",), "eq", 0)], {"unresolved": 0})
    gates = {"source_inventory": count_gate, "features": count_gate,
             "source_geometry": gate, "topology": gate, "segments": gate}
    delivery = pipeline._validate_declared_counts("delivery", {"CABLE": 1}, {"CABLE": 1})
    assert pipeline._reviewer_acceptance_summary(gates, delivery)["passed"] is True


def test_explicit_zero_feature_count_is_a_reviewed_declaration():
    gate = pipeline._validate_exact_counts("features", {"PTECH": 0}, {})
    assert gate["expected"] == {}
    assert gate["declared_counts"] == {"PTECH": 0}
    assert pipeline._reviewer_acceptance_summary({"features": gate}, {})["domain_passed"]["features"] is True
    empty = pipeline._validate_exact_counts("features", {}, {})
    assert pipeline._reviewer_acceptance_summary({"features": empty}, {})["domain_passed"]["features"] is None
    with pytest.raises(RuntimeError, match="census mismatch"):
        pipeline._validate_exact_counts("features", {"PTECH": 0}, {"PTECH": 1})


def test_derived_geometry_and_failed_then_selected_annotations_remain_pending():
    feature = SimpleNamespace(feature_key="derived-boundary", lineage=[{
        "operation": "repair_boundary_polygon", "lossy": True,
        "max_displacement_m": 97.7, "review_status": "required",
    }])
    unresolved = [{"kind": "annotation", "entity_key": "annotation-1", "status": "no_candidate"}]
    semantic = {"annotation_candidates": [
        {"annotation_key": "annotation-1", "selected": True, "status": "selected"},
    ]}
    summary = pipeline._engineering_review_summary([feature], unresolved, semantic)
    assert summary["passed"] is None
    assert summary["geometry_change_feature_count"] == 1
    assert summary["geometry_lineage_operation_counts"] == {"repair_boundary_polygon": 1}
    assert summary["annotation_failed_then_selected_keys"] == ["annotation-1"]
    assert summary["unresolved_count"] == 1
    assert unresolved == [{"kind": "annotation", "entity_key": "annotation-1", "status": "no_candidate"}]
    validation = complete_validation()
    validation["engineering_review"] = summary
    assert status(validation) == RunStatus.CONDITIONAL


def test_non_annotation_unresolved_row_cannot_be_counted_as_annotation_fallback():
    unresolved = [
        {"kind": "source_boundary_repair", "entity_key": "shared-key"},
        {"kind": "annotation", "entity_key": "annotation-key"},
    ]
    semantic = {"annotation_candidates": [
        {"annotation_key": "shared-key", "selected": True},
        {"annotation_key": "annotation-key", "selected": True},
    ]}
    summary = pipeline._engineering_review_summary([], unresolved, semantic)
    assert summary["annotation_failed_then_selected_keys"] == ["annotation-key"]
    assert summary["annotation_failed_then_selected_count"] == 1
    assert summary["unresolved_count"] == 2
    assert summary["passed"] is None


def test_geometry_review_blocks_verified_even_without_unresolved_rows():
    feature = SimpleNamespace(feature_key="cabinet", lineage=[{
        "operation": "collocate_with_support", "max_displacement_m": 12.3,
    }])
    summary = pipeline._engineering_review_summary([feature], [], {})
    assert summary["passed"] is None
    validation = complete_validation()
    validation["engineering_review"] = summary
    assert status(validation) == RunStatus.CONDITIONAL


def test_missing_acceptance_sections_cannot_upgrade_to_verified():
    assert status({}) == RunStatus.CONDITIONAL


def test_legacy_profile_does_not_invent_measurement_tolerance():
    census = {name: 0 for name in (
        "model_entities", "model_inserts", "model_dimensions", "plan_poles",
        "plan_fat", "plan_fdt", "direct_fat_annotations", "direct_new_pole_annotations",
        "direct_existing_pole_annotations", "homepass_labels", "positive_cable_routes",
    )}
    profile = _legacy_apd_expectations(census)
    assert not any(gate.path == tolerance().path for gate in profile.segment_gates)
    profile = _legacy_apd_expectations({**census, "span_measurement_max_abs_error_m": 0.4})
    assert next(gate.value for gate in profile.segment_gates if gate.path == tolerance().path) == 0.4
