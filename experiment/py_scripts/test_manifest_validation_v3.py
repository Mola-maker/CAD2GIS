"""Release manifest exposes the architecture gates without querying GPKG."""

from __future__ import annotations

from types import SimpleNamespace

from cad2gis_v3.pipeline import _manifest_validation_summary


def test_manifest_validation_summary_binds_curve_topology_and_georef_gates():
    entity = SimpleNamespace(entity_key="E1", curve_fingerprint="a" * 64)
    feature = SimpleNamespace(
        feature_class="CABLE",
        source_entity_key="E1",
        native_points=[(0.0, 0.0), (3.0, 0.0)],
        attributes={
            "span_metrics": [{
                "segment_index": 0,
                "source_native_length_m": 3.0,
                "dimension_entity_key": "D1",
                "measurement_native_m": 3.0,
                "measurement_delta_m": 0.0,
                "delivery_grid_length_m": 2.9,
                "geodesic_length_m": 3.0,
                "status": "measured",
            }],
            "span_count": 1,
            "measured_span_count": 1,
            "unmeasured_span_count": 0,
            "delivery_grid_length_m": 2.9,
            "geodesic_length_m": 3.0,
        },
    )
    policy = {
        "cable_sources_checked": 1,
        "curve_facts_checked": 1,
        "source_geometry_immutable": True,
        "synthetic_route_vertices": 0,
        "support_optical_promotions": 0,
        "crossing_connections": 0,
        "validation_domains": {
            "source_geometry": {"passed": True},
            "topology": {"passed": True},
        },
    }
    topology = {
        "source_route_components": 2,
        "source_route_component_diagnostics": {
            "status": "consistent",
            "route_group_components": 2,
            "source_segment_graph_components": 2,
        },
        "route_segment_intersection_counts": {
            "proper_interior_crossing": 0,
            "shared_source_segment_endpoint": 0,
            "source_endpoint_on_segment": 0,
            "collinear_overlap": 0,
            "collinear_endpoint_on_segment": 0,
        },
        "source_routes": 1,
        "source_route_graph": {"unique_nodes": 2, "unique_edges": 1},
        "source_route_native_lengths": 1,
        "source_route_native_length_max_abs_delta_m": 0.0,
        "route_segments_with_span_dimension": 1,
        "route_segments_without_span_dimension": 0,
        "span_measurement_max_abs_error_m": 0.0,
    }
    georeference = {
        "calibration": {
            "status": "disabled",
            "spatial_coverage": {"passed": False},
        },
        "lineage": {"model": "identity_residual", "feature_count": 1},
        "coordinate_operation": {
            "absolute_accuracy_validation": "not independently verified"
        },
    }

    summary = _manifest_validation_summary(
        [entity], [feature], policy, topology, georeference,
        {"CABLE_SEGMENT": 1},
    )

    assert summary["source_geometry"]["passed"] is True
    assert summary["source_geometry"]["curve_facts_checked"] == 1
    assert len(
        summary["source_geometry"]["cable_curve_fingerprint_set_sha256"]
    ) == 64
    assert summary["source_geometry"]["source_route_native_lengths"] == 1
    assert summary["topology"]["source_route_nodes"] == 2
    assert summary["topology"]["source_route_edges"] == 1
    assert summary["topology"]["source_route_component_diagnostics"][
        "status"
    ] == "consistent"
    assert summary["topology"]["route_segment_intersection_counts"][
        "proper_interior_crossing"
    ] == 0
    assert summary["measurements"]["route_segments_with_span_dimension"] == 1
    assert summary["segment_delivery"] == {
        "count": 1,
        "measured": 1,
        "unmeasured": 0,
        "schema_version": "cad2gis.cable_segment.v1",
        "unit": "m",
        "schema_passed": True,
        "unit_passed": True,
        "index_passed": True,
        "geometry_length_closure_passed": True,
        "total_length_closure_passed": True,
        "closure_passed": True,
        "passed": True,
    }
    assert summary["coordinate_accuracy"] == {
        "calibration_status": "disabled",
        "spatial_coverage_passed": False,
        "lineage_model": "identity_residual",
        "lineage_feature_count": 1,
        "absolute_accuracy_validation": "not independently verified",
    }
