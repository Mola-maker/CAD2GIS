"""G8 network-model tests — connectivity graph + dangling-end QC."""
from __future__ import annotations

import pytest

pytest.importorskip("shapely")
from shapely.geometry import LineString, Point, Polygon  # noqa: E402

from cad2gis.model import Feature, FeatureCollection  # noqa: E402
from cad2gis.network import build_network  # noqa: E402


def test_clean_network_no_dangling():
    c = FeatureCollection()
    for x, y in [(60, 5), (120, 5), (180, 5)]:
        c.add(Feature(Point(x, y), "manhole"))
    c.add(Feature(LineString([(60, 5), (120, 5), (180, 5)]), "cable"))
    qc = build_network(c, snap_tol=1.0).qc()
    assert qc.n_nodes == 3 and qc.n_edges == 1
    assert qc.dangling_ends == 0
    assert qc.connectivity_ratio == 1.0


def test_dangling_end_detected():
    c = FeatureCollection()
    c.add(Feature(Point(0, 0), "manhole"))
    c.add(Feature(LineString([(0, 0), (500, 500)]), "cable"))
    qc = build_network(c, snap_tol=1.0).qc()
    assert qc.dangling_ends == 1
    assert qc.connectivity_ratio == 0.5


def test_room_polygon_becomes_node():
    c = FeatureCollection()
    c.add(Feature(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), "room"))
    net = build_network(c)
    assert any(n.feature_class == "room" for n in net.nodes)
