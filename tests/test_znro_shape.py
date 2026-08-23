"""ZNRO parent-zone geometry must span disjoint ZPM parcels without a convex hull."""

from __future__ import annotations

import math

from shapely.geometry import MultiPoint, Polygon

from cad2gis.cad2gis_v3.znro_shape import alpha_shape_union


def test_alpha_shape_bridges_narrow_gaps_without_convex_hull_overreach():
    # An L-shaped parcel group leaves a large empty corner that a convex hull
    # would swallow; the alpha shape must keep that concavity.
    polygons = [
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        [(40.0, 0.0), (50.0, 0.0), (50.0, 10.0), (40.0, 10.0)],
        [(40.0, 20.0), (50.0, 20.0), (50.0, 30.0), (40.0, 30.0)],
    ]
    result = alpha_shape_union(polygons)
    sources = [Polygon(polygon) for polygon in polygons]

    assert result.geom_type == "Polygon"
    assert result.is_valid
    assert all(polygon.covered_by(result) for polygon in sources)
    assert result.area > sum(polygon.area for polygon in sources)
    hull = MultiPoint([
        point for polygon in polygons for point in polygon
    ]).convex_hull
    assert result.area < hull.area


def test_alpha_shape_degenerate_inputs_fall_back_to_a_single_polygon():
    collinear = [
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        [(5.0, 0.0), (6.0, 0.0), (6.0, 1.0), (5.0, 1.0)],
    ]
    result = alpha_shape_union(collinear)
    assert result.geom_type == "Polygon"
    assert result.is_valid
    assert result.area > 2.0
    assert math.isfinite(result.area)


def test_conservative_znro_keeps_isolated_parcels_and_merges_contiguous_group():
    from cad2gis.cad2gis_v3.znro_shape import conservative_znro_polygons

    isolated = [(100.0, 0.0), (110.0, 0.0), (110.0, 10.0), (100.0, 10.0)]
    contiguous = [
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        [(12.0, 0.0), (22.0, 0.0), (22.0, 10.0), (12.0, 10.0)],
        [(12.0, 12.0), (22.0, 12.0), (22.0, 22.0), (12.0, 22.0)],
    ]
    results = conservative_znro_polygons([isolated, *contiguous], gap_bridge_m=8.0)
    assert len(results) == 2
    areas = sorted(round(polygon.area, 3) for polygon in results)
    assert areas == [100.0, 344.063]
