"""The CAD view must retain feature identity, segment boundaries and arcs."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3

import pytest

from cad2gis.native_runtime import ensure_osgeo_runtime

ensure_osgeo_runtime()

from osgeo import ogr, osr  # noqa: E402
from cad2gis.cad2gis_v3.curve_geometry import materialize_cable_features  # noqa: E402
from cad2gis.cad2gis_v3.model import CadStyle, Feature, SourceEntity  # noqa: E402
from cad2gis.review_server import GeoPackageProvider, ReviewServerError  # noqa: E402


def _curve():
    points = [[0, 0], [10, 0], [20, 0]]
    length = 10 + 6.25 * 4 * math.atan(0.5)
    source = SourceEntity.from_record({
        "entity_key": "route:1", "source_sha256": "b" * 64,
        "source_file": "fixture.dwg", "handle": "1", "layout": "Model",
        "cad_role": "model", "layer": "route", "dwg_type_name": "LWPOLYLINE",
        "points": points, "centroid": [10, 0], "native_length": length,
        "curve_facts": {
            "schema_version": "cad2gis-curve-facts-v1", "coordinate_system": "WCS",
            "primitive_type": "LWPOLYLINE", "vertices_wcs": [[*p, 0] for p in points],
            "bulges": [0, 0.5, 0], "elevation": 0, "normal": [0, 0, 1],
            "extrusion": [0, 0, 1], "closed": False, "primitive_parameters": {},
            "native_length": length, "native_length_source": "analytic-test-arc",
        },
    })
    feature = Feature("cable:1", "CABLE", "LineString", [tuple(p) for p in points],
                      source.entity_key, source.handle, source.layer, "SOURCE_ROUTE", CadStyle())
    materialize_cable_features([source], [feature], strict=True)
    return source, feature


def _fixture(tmp_path, *, materialized=True, segment=True, feature_class="CABLE"):
    source, feature = _curve()
    materialization = feature.attributes["curve_materialization"]
    delivery = tmp_path / "delivery.gpkg"
    dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(delivery))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    name = "CABLE_SEGMENT" if segment else feature_class
    layer = dataset.CreateLayer(name, srs, ogr.wkbLineString)
    for field, field_type in (("source_entity_key", ogr.OFTString), ("route_key", ogr.OFTString)):
        layer.CreateField(ogr.FieldDefn(field, field_type))
    if segment:
        layer.CreateField(ogr.FieldDefn("segment_index", ogr.OFTInteger))
    expected = ([item["delivery_native_points"] for item in materialization["source_segments"]]
                if segment else [materialization["delivery_native_points"]])
    if feature_class != "CABLE":
        expected = [feature.native_points]
    for index, points in enumerate(expected):
        row = ogr.Feature(layer.GetLayerDefn())
        row.SetField("source_entity_key", source.entity_key)
        row.SetField("route_key", feature.feature_key)
        if segment:
            row.SetField("segment_index", index)
        row.SetGeometry(ogr.CreateGeometryFromJson(json.dumps({"type": "LineString", "coordinates": points})))
        assert layer.CreateFeature(row) == 0
        row = None
    layer = None
    dataset = None
    evidence = tmp_path / "evidence.gpkg"
    with sqlite3.connect(evidence) as connection:
        connection.execute("CREATE TABLE cad_entities(entity_key TEXT, native_points TEXT, cad_role TEXT, curve_facts TEXT)")
        connection.execute("INSERT INTO cad_entities VALUES(?,?,?,?)", (
            source.entity_key, json.dumps(source.points), "model", json.dumps(source.curve_facts),
        ))
        if materialized:
            connection.execute("CREATE TABLE feature_candidates(feature_key TEXT, feature_class TEXT, source_entity_key TEXT, attributes TEXT, native_points TEXT)")
            connection.execute("INSERT INTO feature_candidates VALUES(?,?,?,?,?)", (
                feature.feature_key, feature_class, source.entity_key, json.dumps(feature.attributes),
                json.dumps(materialization["delivery_native_points"]),
            ))
    (tmp_path / "run_manifest.json").write_text(json.dumps({"artifacts": {"evidence": {
        "path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
    }}}), encoding="utf-8")
    return GeoPackageProvider(delivery), name, expected


@pytest.mark.parametrize("segment", [True, False])
def test_native_review_matches_canonical_curve_and_each_source_segment(tmp_path, segment):
    provider, name, expected = _fixture(tmp_path, segment=segment)
    actual = provider.geojson(name, native_coordinates=True)
    actual_points = [item["geometry"]["coordinates"] for item in actual["features"]]
    assert len(actual_points) == len(expected)
    for actual_line, expected_line in zip(actual_points, expected, strict=True):
        assert len(actual_line) == len(expected_line)
        for actual_point, expected_point in zip(actual_line, expected_line, strict=True):
            assert actual_point == pytest.approx(expected_point, rel=0, abs=1e-12)
    assert any(len(points) > 3 for points in expected)
    if segment:
        assert expected[0] != expected[1]


def test_legacy_curved_evidence_fails_closed_instead_of_displaying_a_chord(tmp_path):
    provider, name, _ = _fixture(tmp_path, materialized=False)
    with pytest.raises(ReviewServerError, match="curve materialization is missing"):
        provider.geojson(name, native_coordinates=True)


def _legacy_lineage(provider, *, straight=False):
    source, feature = _curve()
    if straight:
        source.curve_facts["bulges"] = [0, 0, 0]
        feature.native_points[0] = (1, 1)
    evidence = provider.path.parent / "evidence.gpkg"
    with sqlite3.connect(evidence) as connection:
        connection.execute("ALTER TABLE feature_candidates DROP COLUMN native_points")
        connection.execute("UPDATE feature_candidates SET attributes = '{}' ")
        connection.execute("UPDATE cad_entities SET curve_facts = ?", (json.dumps(source.curve_facts),))
        connection.execute("CREATE TABLE georef_feature_lineage(feature_key TEXT, native_points_json TEXT)")
        connection.execute("INSERT INTO georef_feature_lineage VALUES(?, ?)", (
            feature.feature_key, json.dumps(feature.native_points),
        ))
    manifest = provider.path.parent / "run_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["artifacts"]["evidence"]["sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return feature


@pytest.mark.parametrize("segment", [True, False])
def test_legacy_curve_lineage_vertices_cannot_bypass_materialization_guard(tmp_path, segment):
    provider, name, _ = _fixture(tmp_path, segment=segment)
    _legacy_lineage(provider)
    with pytest.raises(ReviewServerError, match="curve materialization is missing"):
        provider.geojson(name, native_coordinates=True)


def test_legacy_straight_segment_retains_reviewed_feature_endpoint(tmp_path):
    provider, name, _ = _fixture(tmp_path)
    feature = _legacy_lineage(provider, straight=True)
    dataset = ogr.Open(str(provider.path), 1)
    layer = dataset.GetLayerByName(name)
    for row in layer:
        index = row.GetField("segment_index")
        row.SetGeometry(ogr.CreateGeometryFromJson(json.dumps({
            "type": "LineString", "coordinates": feature.native_points[index:index + 2],
        })))
        assert layer.SetFeature(row) == 0
    row = layer = dataset = None
    actual = provider.geojson(name, native_coordinates=True)
    assert actual["features"][0]["geometry"]["coordinates"] == [[1, 1], [10, 0]]
    assert actual["features"][1]["geometry"]["coordinates"] == [[10, 0], [20, 0]]


def test_legacy_non_cable_feature_lineage_is_already_delivery_geometry(tmp_path):
    provider, name, _ = _fixture(tmp_path, segment=False, feature_class="INFRASTRUCTURE")
    _legacy_lineage(provider)
    actual = provider.geojson(name, native_coordinates=True)
    assert actual["features"][0]["geometry"]["coordinates"] == [[0, 0], [10, 0], [20, 0]]
