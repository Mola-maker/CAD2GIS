"""Deterministic ZNRO parent-zone geometry for reviewed FTTH APD drawings.

ZNRO is the parent service zone of the delivered ZPM polygons.  The drawing's
ZPM polygons are separate small parcels with narrow gaps between them; the
reviewed ZNRO is the single polygon that spans all of them and fills those
narrow gaps without swallowing the large empty regions a convex hull would
swallow.

The implementation uses the alpha-shape of the ZPM vertices: Delaunay
triangles whose circumradius fits inside ``alpha`` are kept.  ``alpha`` starts
at half of the longest minimum-spanning-tree edge between ZPM polygon
centroids (the minimum radius that can bridge the widest inter-polygon gap)
and grows by fixed steps until the kept triangles form one simple polygon
that covers every ZPM polygon.  All interior holes are then filled, so the
result is one maximum-spanning simple polygon.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy.spatial import Delaunay, QhullError
from scipy.spatial.distance import pdist, squareform
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union


def _unique_points(polygons: Sequence[Sequence[Sequence[float]]]) -> list[tuple[float, float]]:
    unique = {}
    for polygon in polygons:
        for raw_point in polygon:
            point = (float(raw_point[0]), float(raw_point[1]))
            unique[point] = point
    return list(unique.values())


def _prim_mst_max_edge(centroids: np.ndarray) -> float:
    """Return the longest edge of the minimum spanning tree (Prim)."""
    count = len(centroids)
    if count <= 1:
        return 0.0
    pairwise = squareform(pdist(centroids))
    visited = [False] * count
    visited[0] = True
    longest = 0.0
    for _ in range(count - 1):
        best = float("inf")
        best_node = -1
        for node in range(count):
            if visited[node]:
                continue
            for parent in range(count):
                if not visited[parent]:
                    continue
                if pairwise[parent][node] < best:
                    best = pairwise[parent][node]
                    best_node = node
        if best_node < 0 or not math.isfinite(best):
            break
        visited[best_node] = True
        longest = max(longest, best)
    return longest


def _triangle_circumradius(triangle: np.ndarray) -> float:
    a, b, c = triangle
    denominator = 2.0 * (
        a[0] * (b[1] - c[1])
        + b[0] * (c[1] - a[1])
        + c[0] * (a[1] - b[1])
    )
    if abs(denominator) < 1e-12:
        return float("inf")
    ux = (
        (a[0] * a[0] + a[1] * a[1]) * (b[1] - c[1])
        + (b[0] * b[0] + b[1] * b[1]) * (c[1] - a[1])
        + (c[0] * c[0] + c[1] * c[1]) * (a[1] - b[1])
    ) / denominator
    uy = (
        (a[0] * a[0] + a[1] * a[1]) * (c[0] - b[0])
        + (b[0] * b[0] + b[1] * b[1]) * (a[0] - c[0])
        + (c[0] * c[0] + c[1] * c[1]) * (b[0] - a[0])
    ) / denominator
    return float(math.hypot(a[0] - ux, a[1] - uy))


def _convex_hull(points: Sequence[Sequence[float]]) -> Polygon:
    return MultiPoint(points).convex_hull


def alpha_shape_union(
    polygons: Sequence[Sequence[Sequence[float]]],
    *,
    gap_fill_multiplier: float = 1.2,
) -> Polygon:
    """Return one simple polygon spanning all ZPM polygons with gaps filled.

    ``gap_fill_multiplier`` scales the bridging radius computed from the
    longest MST edge; values above 1.0 make the result slightly more generous
    while keeping it far tighter than the convex hull.
    """
    if not polygons:
        raise ValueError("alpha_shape_union requires at least one polygon")
    points = _unique_points(polygons)
    if len(points) < 4:
        return _convex_hull(points)
    source_polygons = [Polygon(polygon) for polygon in polygons]
    centroids = np.array([polygon.centroid.coords[0] for polygon in source_polygons])
    bridge = _prim_mst_max_edge(centroids)
    if bridge <= 0.0:
        bridge = max(
            math.dist(points[index], points[index + 1])
            for index in range(len(points) - 1)
        ) if len(points) > 1 else 0.0
    if bridge <= 0.0:
        bridge = 1.0

    points_array = np.array(points)
    try:
        triangulation = Delaunay(points_array)
    except (QhullError, ValueError):
        try:
            triangulation = Delaunay(points_array, qhull_options="QJ")
        except (QhullError, ValueError):
            return _convex_hull(points)

    multipliers = (0.5, 0.75, 1.0, 1.2, 1.5, 2.0, 4.0)
    for multiplier in multipliers:
        radius = bridge * 0.5 * multiplier * gap_fill_multiplier + 1e-9
        kept = [
            simplex for simplex in triangulation.simplices
            if _triangle_circumradius(points_array[simplex]) <= radius
        ]
        if not kept:
            continue
        union = unary_union([Polygon(points_array[simplex]) for simplex in kept])
        pieces = list(union.geoms) if hasattr(union, "geoms") else [union]
        polygons_only = [piece for piece in pieces if piece.geom_type == "Polygon"]
        if not polygons_only:
            continue
        filled = unary_union([
            Polygon(piece.exterior.coords) for piece in polygons_only
        ])
        if filled.geom_type != "Polygon":
            continue
        if all(polygon.covered_by(filled) for polygon in source_polygons):
            return filled
    return _convex_hull(points)
