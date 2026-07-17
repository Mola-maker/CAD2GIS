"""Focused CABLE per-source-segment length closure regressions."""

import json
import math
import sqlite3
from pathlib import Path

import pytest
from osgeo import ogr

from cad2gis_v3.config import MappingRegistry, SourceProfile
from cad2gis_v3.evidence import write_evidence
from cad2gis_v3.georef import DirectTransformer, enrich_delivery_metrics
from cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis_v3.topology import build_topology
from cad2gis_v3.warehouse import write_delivery


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_source_profile.json"
REGISTRY = ROOT / "config" / "apd_mapping_registry.json"


def _route(points):
    native_length = sum(math.dist(start, end) for start, end in zip(points, points[1:]))
    return Feature(
        feature_key="CABLE-R1",
        feature_class="CABLE",
        geometry_kind="LineString",
        native_points=list(points),
        source_entity_key="entity-CABLE-R1",
        source_handle="R1",
        source_layer="Cable Line A (FO Cable 24C_2T)",
        geometry_role="SOURCE_ROUTE",
        style=CadStyle(aci_color=3),
        attributes={
            "CODE": "CABLE-R1",
            "source_autocad_native_length_m": native_length,
        },
        field_provenance={
            "CODE": "DWG_DIRECT:test-fixture",
            "source_autocad_native_length_m": "DWG_DIRECT:AutoCAD-curve-distance",
        },
    )


def _route_entity(points):
    return SourceEntity(
        entity_key="entity-CABLE-R1",
        source_sha256="x",
        source_file="a.dwg",
        handle="R1",
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
    )


def _dimension(start, end, measurement):
    return SourceEntity(
        entity_key="DIM-1",
        source_sha256="x",
        source_file="a.dwg",
        handle="D1",
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


def test_cable_spans_keep_dimension_and_delivery_length_closure(tmp_path):
    profile = SourceProfile.load(PROFILE)
    registry = MappingRegistry.load(REGISTRY, profile.source_sha256)
    points = [
        (13_681_914.403, 69_386.445),
        (13_681_926.903, 69_386.445),
        (13_681_926.903, 69_393.695),
    ]
    route = _route(points)
    route_entity = _route_entity(points)
    dimension = _dimension(points[0], points[1], 12.5)
    original_points = list(route.native_points)
    relations, unresolved, diagnostics = build_topology(
        [route_entity, dimension], [route], registry, [], [],
    )

    assert len([relation for relation in relations if relation.relation_kind == "measures"]) == 1
    assert route.native_points == original_points
    assert route.attributes["span_count"] == 2
    assert route.attributes["measured_span_count"] == 1
    assert route.attributes["unmeasured_span_count"] == 1
    assert route.attributes["dimension_length_m"] == pytest.approx(12.5)
    assert diagnostics["route_segments_with_span_dimension"] == 1
    assert diagnostics["route_segments_without_span_dimension"] == 1
    assert diagnostics["source_route_native_lengths"] == 1
    assert diagnostics["source_route_native_length_max_abs_delta_m"] == pytest.approx(0.0)
    assert route.attributes["source_native_length_delta_m"] == pytest.approx(0.0)

    native_metrics = route.attributes["span_metrics"]
    assert [metric["segment_index"] for metric in native_metrics] == [0, 1]
    assert [metric["source_native_length_m"] for metric in native_metrics] == pytest.approx(
        [12.5, 7.25]
    )
    assert native_metrics[0] == {
        "segment_index": 0,
        "source_native_length_m": 12.5,
        "dimension_entity_key": "DIM-1",
        "measurement_native_m": 12.5,
        "measurement_delta_m": 0.0,
        "status": "measured",
    }
    assert native_metrics[1]["dimension_entity_key"] is None
    assert native_metrics[1]["measurement_native_m"] is None
    assert native_metrics[1]["measurement_delta_m"] is None
    assert native_metrics[1]["status"] == "unmeasured_no_dimension"
    assert all(metric["source_native_length_m"] != 15.0 for metric in native_metrics)

    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    enrich_delivery_metrics([route], transformer)
    enriched = route.attributes["span_metrics"]
    assert route.native_points == original_points
    assert sum(metric["delivery_grid_length_m"] for metric in enriched) == pytest.approx(
        route.attributes["delivery_grid_length_m"], abs=1e-9,
    )
    assert sum(metric["geodesic_length_m"] for metric in enriched) == pytest.approx(
        route.attributes["geodesic_length_m"], abs=1e-9,
    )
    assert all(math.isfinite(metric["delivery_grid_length_m"]) for metric in enriched)
    assert all(math.isfinite(metric["geodesic_length_m"]) for metric in enriched)
    assert route.attributes["dimension_measured_sum_m"] == pytest.approx(12.5)
    assert route.attributes["dimension_measurement_status"] == "partial"
    assert route.attributes["dimension_coverage_ratio"] == pytest.approx(0.5)
    assert route.attributes["span_schema_version"] == "cad2gis.cable_span_metrics.v1"
    assert route.attributes["span_unit"] == "m"

    delivery = tmp_path / "delivery.gpkg"
    counts = write_delivery(delivery, [route], transformer)
    assert counts["CABLE"] == 1
    with sqlite3.connect(delivery) as connection:
        row = connection.execute(
            'SELECT span_count, measured_span_count, unmeasured_span_count, '
            'dimension_measured_sum_m, dimension_measurement_status, '
            'dimension_coverage_ratio, span_schema_version, span_unit, '
            'span_metrics_json FROM "CABLE"'
        ).fetchone()
    assert row[:3] == (2, 1, 1)
    assert row[3] == pytest.approx(12.5)
    assert row[4] == "partial"
    assert row[5] == pytest.approx(0.5)
    assert row[6] == "cad2gis.cable_span_metrics.v1"
    assert row[7] == "m"
    written_metrics = json.loads(row[8])
    assert written_metrics == enriched
    assert written_metrics[0]["measurement_native_m"] == 12.5
    assert written_metrics[1]["measurement_native_m"] is None

    evidence = tmp_path / "evidence.gpkg"
    write_evidence(
        evidence,
        [route_entity, dimension],
        [route],
        relations,
        unresolved,
        {"semantics": {}, "topology": diagnostics},
        transformer.source,
        target_srs=transformer.target,
        delivery_transformer=transformer,
    )
    with sqlite3.connect(evidence) as connection:
        rows = connection.execute(
            "SELECT segment_index, source_native_length_m, dimension_entity_key, "
            "measurement_native_m, delivery_grid_length_m, geodesic_length_m, status "
            "FROM cable_span_metrics ORDER BY segment_index"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0:4] == (0, 12.5, "DIM-1", 12.5)
    assert rows[0][-1] == "measured"
    assert rows[1][0] == 1
    assert rows[1][2] is None
    assert rows[1][3] is None
    assert rows[1][-1] == "unmeasured_no_dimension"
    dataset = ogr.Open(str(evidence), 0)
    span_layer = dataset.GetLayerByName("cable_span_segments")
    assert span_layer is not None
    assert span_layer.GetFeatureCount() == 2
    assert span_layer.GetSpatialRef().IsSame(transformer.target)
    spatial_rows = list(span_layer)
    assert spatial_rows[0].GetField("length_label") == "12.500 m"
    assert spatial_rows[1].GetField("status") == "unmeasured_no_dimension"
    assert spatial_rows[1].GetGeometryRef().Length() == pytest.approx(
        enriched[1]["delivery_grid_length_m"], abs=1e-6,
    )
    dataset = None
