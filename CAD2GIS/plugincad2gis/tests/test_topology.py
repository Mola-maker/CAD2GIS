"""G5/G7 topology-cleaning tests — the 3 injected fixture errors must be fixed, guarded."""
from __future__ import annotations

import pytest

pytest.importorskip("shapely")
from shapely.geometry import LineString, Point, Polygon  # noqa: E402

from cad2gis.model import Feature, FeatureCollection  # noqa: E402
from cad2gis.topology import clean_collection, close_unclosed_lines_to_polygons  # noqa: E402


def _dirty() -> FeatureCollection:
    c = FeatureCollection()
    c.add(Feature(LineString([(60, 5), (120, 5), (180, 5)]), "cable"))   # true cable
    c.add(Feature(LineString([(180, 5), (188, 5)]), "cable"))           # overshoot dangle
    c.add(Feature(LineString([(60, 5), (120, 5)]), "cable"))            # duplicate sub-segment
    c.add(Feature(Point(60, 5), "manhole"))
    c.add(Feature(Point(60.2, 5), "manhole"))                          # near-duplicate point
    c.add(Feature(LineString([(80, 60), (110, 60), (110, 80), (80, 80), (80, 60.5)]), "room"))  # unclosed
    return c


def test_all_injected_errors_fixed():
    c = close_unclosed_lines_to_polygons(_dirty(), close_gap_max=2.0)
    cleaned, rep = clean_collection(c, point_tol=0.5, dangle_max_len=10.0, dup_tol=0.6)
    cnt = cleaned.counts_by_class()
    assert rep.dangles_trimmed >= 1
    assert rep.lines_removed_duplicate >= 1
    assert rep.points_deduped >= 1
    assert cnt.get("cable") == 1
    assert cnt.get("room") == 1
    assert any(f.geometry.geom_type == "Polygon" for f in cleaned.features if f.feature_class == "room")


def test_over_aggressive_repair_rejected():
    c = FeatureCollection()
    c.add(Feature(Polygon([(0, 0), (4, 4), (0, 4), (4, 0)]), "room"))  # self-intersecting bowtie
    _, rep = clean_collection(c, max_area_delta=0.01)
    assert rep.repairs_rejected == 1
    assert rep.polygons_repaired == 0
