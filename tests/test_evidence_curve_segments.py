"""Real evidence spatial spans share delivery curve points and metric units."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest
from pyproj import Transformer

from cad2gis.native_runtime import ensure_osgeo_runtime

ensure_osgeo_runtime()

from osgeo import ogr  # noqa: E402
from cad2gis.cad2gis_v3.curve_geometry import materialize_cable_features  # noqa: E402
from cad2gis.cad2gis_v3.evidence import write_evidence  # noqa: E402
from cad2gis.cad2gis_v3.georef import DirectTransformer, enrich_delivery_metrics  # noqa: E402
from cad2gis.cad2gis_v3.model import CadStyle, Feature, SourceEntity  # noqa: E402
from cad2gis.cad2gis_v3.osm_matching import match_osm  # noqa: E402
from cad2gis.cad2gis_v3.warehouse import write_delivery  # noqa: E402


@pytest.mark.parametrize("target_crs", ["EPSG:3857", "EPSG:2263"])
def test_real_evidence_preserves_each_curved_span_and_target_axis_units(tmp_path, target_crs):
    origin = (-8_236_050.0, 4_975_250.0)
    points = [(origin[0] + x, origin[1]) for x in (0.0, 10.0, 20.0)]
    length = 10.0 + 6.25 * 4.0 * math.atan(0.5)
    source = SourceEntity.from_record({
        "entity_key": "route:1", "source_sha256": "b" * 64,
        "source_file": "fixture.dwg", "handle": "1", "layout": "Model",
        "cad_role": "model", "layer": "route", "dwg_type_name": "LWPOLYLINE",
        "points": points, "centroid": points[1], "native_length": length,
        "curve_facts": {
            "schema_version": "cad2gis-curve-facts-v1", "coordinate_system": "WCS",
            "primitive_type": "LWPOLYLINE", "vertices_wcs": [[*point, 0] for point in points],
            "bulges": [0, 0.5, 0], "closed": False, "native_length": length,
            "native_length_source": "analytic-test-arc",
        },
    })
    feature = Feature("cable:1", "CABLE", "LineString", list(points),
                      source.entity_key, source.handle, source.layer, "SOURCE_ROUTE", CadStyle())
    feature.field_provenance["source_cad_length_m"] = "DWG_DIRECT:source-native-length"
    feature.field_provenance["source_segment_sum_m"] = "DWG_DERIVED:materialized-source-segment-sum"
    materialize_cable_features([source], [feature], strict=True)
    nominal = DirectTransformer("EPSG:3857", target_crs)
    geographic = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    road_points = [geographic.transform(x + 100.0, y + 200.0) for x, y in (points[0], points[-1])]
    snapshot = tmp_path / "osm.json"
    snapshot.write_text(json.dumps({"elements": [{"type": "way", "id": 1,
        "tags": {"highway": "residential"},
        "geometry": [{"lon": lon, "lat": lat} for lon, lat in road_points]}]}), encoding="utf-8")
    matched, record = match_osm(
        source=tmp_path / "fixture.dwg", source_sha256="b" * 64,
        features=[SimpleNamespace(feature_class="PTECH", native_points=points)],
        nominal=nominal, coordinate_domain={"passed": False}, osm_data=snapshot,
    )
    assert record["status"] == "coarse"
    assert math.dist(matched.point(points[1]), nominal.point(points[1])) > 100.0
    enrich_delivery_metrics([feature], matched)
    evidence_path = tmp_path / "evidence.gpkg"
    write_evidence(evidence_path, [source], [feature], [], [], {}, nominal.source,
                   delivery_transformer=nominal,
                   delivery_transformers={feature.feature_key: matched})
    delivery_path = tmp_path / "delivery.gpkg"
    write_delivery(delivery_path, [feature], matched)

    def spans(path, layer_name):
        dataset = ogr.Open(str(path), 0)
        layer = dataset.GetLayerByName(layer_name)
        result = {}
        for row in layer:
            geometry = row.GetGeometryRef()
            result[row.GetField("segment_index")] = {
                "points": [geometry.GetPoint_2D(index) for index in range(geometry.GetPointCount())],
                "length": row.GetField("delivery_grid_length_m"),
                "geometry_length": geometry.Length(),
            }
        layer = None
        dataset.Close()
        return result

    evidence = spans(evidence_path, "cable_span_segments")
    delivery = spans(delivery_path, "CABLE")
    assert len(evidence) == len(delivery) == 2
    assert len(evidence[0]["points"]) == 2
    assert len(evidence[1]["points"]) > 2
    for index, segment in enumerate(feature.attributes["curve_materialization"]["source_segments"]):
        expected = matched.points(segment["delivery_native_points"])
        assert evidence[index]["points"] == delivery[index]["points"] == expected
        assert evidence[index]["length"] == pytest.approx(delivery[index]["length"], abs=1e-6)
        assert evidence[index]["length"] == pytest.approx(matched.grid_length_m(expected), abs=1e-6)
        if target_crs == "EPSG:2263":
            assert evidence[index]["length"] == pytest.approx(
                evidence[index]["geometry_length"] * (1200.0 / 3937.0), abs=1e-6,
            )
