"""Keep explicit OSM topology separate from geometric intersections."""
from __future__ import annotations

import io
import json
import urllib.parse

from pyproj import Transformer
import pytest

from cad2gis.cad2gis_v3 import osm_anchor


@pytest.mark.parametrize("lon,lat", [(110.2, -7.3), (179.5, 80.0), (-123.0, 60.0), (0.0, 0.0)])
def test_osm_projection_matches_proj_without_rounded_world_constant(lon, lat):
    expected = Transformer.from_crs(4326, 3857, always_xy=True).transform(lon, lat)
    actual = osm_anchor._wgs84_to_3857(lon, lat)
    assert actual == pytest.approx(expected, abs=1e-8, rel=0)
    assert osm_anchor._epsg3857_to_wgs84(*actual) == pytest.approx((lon, lat), abs=1e-12, rel=0)


def test_overpass_retains_real_nodes_and_grade_separation(monkeypatch):
    def way(number, nodes, coords, **tags):
        return {"type": "way", "id": number, "nodes": nodes,
                "tags": {"highway": "residential", **tags},
                "geometry": [{"lon": x, "lat": y} for x, y in coords]}
    payload = {"elements": [
        way(1, [10, 11, 10], [(110, -7), (110.001, -7), (110, -7)]),
        way(2, [10, 12], [(110, -7), (110, -7.001)], bridge="yes", layer="1"),
        # Geometry alone does not establish node identity.
        way(3, [], [(110, -7), (110.001, -7.001)], tunnel="yes", layer="-1"),
        way(4, [True, "bad"], [(110, -7), (110.001, -7.001)]),
    ]}
    def response(request, **_kwargs):
        query = urllib.parse.parse_qs(request.data.decode())["data"][0]
        assert "out body geom" in query
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(osm_anchor.urllib.request, "urlopen", response)
    roads = osm_anchor.query_osm_roads({"south": -7.01, "west": 109.99, "north": -6.99, "east": 110.01})
    assert roads[0]["node_ids"] == [10, 11, 10]
    assert roads[0]["endpoint_degree"] == 1
    assert roads[1]["bridge"] == "yes" and roads[1]["layer"] == "1"
    assert roads[1]["endpoint_degree"] == 1
    assert "node_ids" not in roads[2] and "node_ids" not in roads[3]
    assert roads[2]["endpoint_degree"] == 0
