"""Loss-aware cable curve and multi-vertex segment delivery regressions."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from osgeo import ogr

from cad2gis_v3.config import MappingRegistry, SourceProfile
from cad2gis_v3.curve_geometry import (
    CableGeometryMaterializationError,
    delivery_points,
    delivery_segments,
    materialize_cable_features,
    validate_cable_geometry_materialization,
)
from cad2gis_v3.georef import DirectTransformer, enrich_delivery_metrics
from cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis_v3.topology import build_topology
from cad2gis_v3.warehouse import write_delivery


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_source_profile.json"
REGISTRY = ROOT / "config" / "apd_mapping_registry.json"


def _curve_route(
    *,
    feature_key="CABLE-ARC",
    points=((13_681_914.403, 69_386.445), (13_681_924.403, 69_386.445)),
    bulges=(1.0, 0.0),
    native_length=5.0 * math.pi,
    primitive_type="LWPOLYLINE",
    primitive_parameters=None,
):
    facts = {
        "schema_version": "cad2gis-curve-facts-v1",
        "coordinate_system": "WCS",
        "primitive_type": primitive_type,
        "vertices_wcs": [[point[0], point[1], 0.0] for point in points],
        "bulges": list(bulges),
        "elevation": 0.0,
        "normal": [0.0, 0.0, 1.0],
        "extrusion": [0.0, 0.0, 1.0],
        "closed": False,
        "primitive_parameters": primitive_parameters or {},
        "native_length": native_length,
        "native_length_source": "autocad_curve_distance",
    }
    source = SourceEntity(
        entity_key=f"entity-{feature_key}",
        source_sha256="fixture",
        source_file="curve.dwg",
        handle=feature_key,
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="Cable Line A (FO Cable 24C_2T)",
        object_name="ACDBLWPOLYLINE",
        dwg_type="LWPOLYLINE",
        points=tuple(points),
        centroid=points[0],
        closed=False,
        text="",
        block_name="",
        block_attributes={},
        style=CadStyle(aci_color=3),
        native_length=native_length,
        raw_properties={"extraction_backend": "fixture"},
        curve_facts=facts,
    )
    feature = Feature(
        feature_key=feature_key,
        feature_class="CABLE",
        geometry_kind="LineString",
        native_points=list(points),
        source_entity_key=source.entity_key,
        source_handle=source.handle,
        source_layer=source.layer,
        geometry_role="SOURCE_ROUTE",
        style=source.style,
        attributes={
            "CODE": feature_key,
            "source_autocad_native_length_m": native_length,
        },
        field_provenance={
            "source_autocad_native_length_m": "DWG_DIRECT:AutoCAD-curve-distance",
        },
        lineage=[{
            "operation": "identity",
            "source_entity_key": source.entity_key,
            "max_displacement_m": 0.0,
        }],
    )
    return source, feature


def _dimension(start, end, measurement):
    return SourceEntity(
        entity_key="DIM-ARC",
        source_sha256="fixture",
        source_file="curve.dwg",
        handle="DIM-ARC",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="SPAN CABLE",
        object_name="ACDBDIMENSION",
        dwg_type="DIMENSION",
        points=(start, end),
        centroid=((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0),
        closed=False,
        text="",
        block_name="",
        block_attributes={},
        style=CadStyle(),
        dimension_value=measurement,
    )


def test_bulge_arc_materialization_is_deterministic_bounded_and_loss_aware():
    source, route = _curve_route()
    original = list(route.native_points)
    policy = {"max_sagitta_native": 0.05, "max_chord_native": 1.0}

    diagnostics = materialize_cable_features([source], [route], policy=policy)
    segment = delivery_segments(route)[0]
    points = segment["delivery_native_points"]

    assert route.native_points == original
    assert segment["source_segment_index"] == 0
    assert segment["source_segment_kind"] == "bulge_arc"
    assert segment["source_native_length"] == pytest.approx(5.0 * math.pi, abs=1e-12)
    assert len(points) > 2
    assert points[0] == original[0]
    assert points[-1] == original[-1]
    assert max(math.dist(a, b) for a, b in zip(points, points[1:])) <= 1.0 + 1e-12
    assert segment["delivery_chord_length_native"] < segment["source_native_length"]
    assert diagnostics["arc_segments"] == 1
    assert diagnostics["curved_routes"] == 1
    assert diagnostics["unsupported_count"] == 0

    validated = validate_cable_geometry_materialization(
        [source], [route], policy=policy,
    )
    assert validated["validated"] is True
    assert validated["native_length_closure_max_abs_error"] == pytest.approx(0.0)


def test_materialization_fails_atomically_with_structured_incomplete_curve_issue():
    valid_source, valid_route = _curve_route(feature_key="VALID")
    bad_source, bad_route = _curve_route(
        feature_key="SPLINE",
        primitive_type="SPLINE",
        bulges=(0.0, 0.0),
        native_length=10.0,
        primitive_parameters={"degree": 3},
    )

    with pytest.raises(CableGeometryMaterializationError) as captured:
        materialize_cable_features(
            [valid_source, bad_source], [valid_route, bad_route],
        )

    assert captured.value.issues[0]["code"] == "UNSUPPORTED_OR_INCOMPLETE_CURVE_FACTS"
    assert "delivery_segments_wcs" in captured.value.issues[0]["detail"]
    assert captured.value.diagnostics["unsupported_count"] == 1
    assert "curve_materialization" not in valid_route.attributes
    assert "curve_materialization" not in bad_route.attributes


def test_nonpolyline_curve_is_supported_only_with_complete_reader_segments():
    points = ((0.0, 0.0), (10.0, 0.0))
    source, route = _curve_route(
        points=points,
        primitive_type="SPLINE",
        bulges=(0.0, 0.0),
        native_length=12.0,
        primitive_parameters={
            "degree": 3,
            "delivery_segments_wcs": [{
                "source_segment_index": 0,
                "source_segment_kind": "spline",
                "source_start_vertex_index": None,
                "source_end_vertex_index": None,
                "native_length": 12.0,
                "native_length_source": "autocad_curve_distance",
                "points_wcs": [[0.0, 0.0, 0.0], [5.0, 2.5, 0.0], [10.0, 0.0, 0.0]],
            }],
        },
    )

    diagnostics = materialize_cable_features([source], [route])
    segment = delivery_segments(route)[0]
    assert segment["source_segment_kind"] == "spline"
    assert segment["source_native_length"] == pytest.approx(12.0)
    assert len(segment["delivery_native_points"]) == 3
    assert diagnostics["reader_materialized_segments"] == 1
    assert validate_cable_geometry_materialization([source], [route])["validated"] is True


def test_native_curve_length_closure_fails_instead_of_using_chord_length():
    source, route = _curve_route(native_length=10.0)
    with pytest.raises(CableGeometryMaterializationError) as captured:
        materialize_cable_features([source], [route])
    assert "source-segment native-length closure failed" in str(captured.value)
    assert captured.value.diagnostics["cables_materialized"] == 0


def test_curved_source_segment_keeps_native_length_and_multi_vertex_gpkg_geometry(tmp_path):
    profile = SourceProfile.load(PROFILE)
    registry = MappingRegistry.load(REGISTRY, profile.source_sha256)
    source, route = _curve_route()
    dimension = _dimension(route.native_points[0], route.native_points[1], 5.0 * math.pi)
    original = list(route.native_points)

    materialize_cable_features([source], [route])
    relations, unresolved, diagnostics = build_topology(
        [source, dimension], [route], registry, [], [],
    )
    assert route.native_points == original
    assert diagnostics["route_segment_occurrences"] == 1
    assert diagnostics["route_curve_materialization"]["curved_source_segments"] == 1
    assert route.attributes["span_metrics"][0]["source_native_length_m"] == pytest.approx(
        5.0 * math.pi,
    )
    assert route.attributes["span_metrics"][0]["source_segment_kind"] == "bulge_arc"
    assert route.attributes["span_metrics"][0]["measurement_delta_m"] == pytest.approx(0.0)
    assert len([item for item in relations if item.relation_kind == "measures"]) == 1

    transformer = DirectTransformer(profile.source_crs, profile.target_crs)
    enrich_delivery_metrics([route], transformer)
    delivery = tmp_path / "curved_delivery.gpkg"
    counts = write_delivery(delivery, [route], transformer)
    assert counts["CABLE"] == 1
    assert counts["CABLE_SEGMENT"] == 1

    dataset = ogr.Open(str(delivery), 0)
    cable = dataset.GetLayerByName("CABLE").GetNextFeature()
    segment = dataset.GetLayerByName("CABLE_SEGMENT").GetNextFeature()
    assert cable.GetGeometryRef().GetPointCount() == len(delivery_points(route))
    assert cable.GetField("curve_materialization_schema_version") == (
        "cad2gis.cable_curve_materialization.v1"
    )
    assert cable.GetField("curve_materialization_policy_version") == (
        "cad2gis.bulge_tessellation.v1"
    )
    assert segment.GetGeometryRef().GetPointCount() == len(
        delivery_segments(route)[0]["delivery_native_points"]
    )
    assert segment.GetGeometryRef().GetPointCount() > 2
    assert segment.GetField("source_segment_kind") == "bulge_arc"
    assert segment.GetField("measurement_state") == "measured"
    assert segment.GetField("source_native_length_m") == pytest.approx(5.0 * math.pi)
    assert segment.GetField("length_value_m") == pytest.approx(5.0 * math.pi)
    assert segment.GetField("delivery_vertex_count") > 2
    assert segment.GetField("materialization_policy_version") == (
        "cad2gis.bulge_tessellation.v1"
    )
    assert segment.GetField("delivery_grid_length_m") == pytest.approx(
        route.attributes["delivery_grid_length_m"], abs=1e-6,
    )
    dataset = None
