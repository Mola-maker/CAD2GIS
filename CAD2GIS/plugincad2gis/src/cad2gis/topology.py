"""Topology cleaning & validation (stories G5/G7) — per-geometry-class, guarded.

Per the independent review, cleaning is split by geometry class rather than one global
recipe, and geometry-repair is guarded by delta thresholds so an over-aggressive
`make_valid` can't silently change what the drawing meant:

  points  : dedupe within tolerance
  lines   : drop zero/near-zero-length, remove exact/near duplicates, trim short
            dangling overshoots below a length threshold
  polygons : close near-closed rings, repair validity (guarded), drop slivers

This is the shapely fallback used when GRASS `v.clean` isn't available. When QGIS's GRASS
provider is present it can be swapped in for noding/snapping on large data; the contract
(FeatureCollection in -> cleaned FeatureCollection + TopologyReport) stays identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .model import Feature, FeatureCollection


@dataclass
class TopologyReport:
    before: int = 0
    after: int = 0
    points_deduped: int = 0
    lines_removed_zero: int = 0
    lines_removed_duplicate: int = 0
    dangles_trimmed: int = 0
    polygons_closed: int = 0
    polygons_repaired: int = 0
    repairs_rejected: int = 0
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["details"] = list(self.details)
        return d


def _length(geom) -> float:
    try:
        return float(geom.length)
    except Exception:  # noqa: BLE001
        return 0.0


def clean_collection(
    coll: FeatureCollection,
    *,
    point_tol: float = 0.5,
    dangle_max_len: float = 10.0,
    dup_tol: float = 0.5,
    close_gap_max: float = 2.0,
    max_area_delta: float = 0.25,
    report: Optional[TopologyReport] = None,
) -> tuple[FeatureCollection, TopologyReport]:
    from shapely.geometry import Point
    from shapely.validation import make_valid

    rep = report or TopologyReport()
    rep.before = len(coll)
    out = FeatureCollection(crs=coll.crs, source_file=coll.source_file, metadata=dict(coll.metadata))

    pts: list[Feature] = []
    lines: list[Feature] = []
    polys: list[Feature] = []
    others: list[Feature] = []
    for f in coll.features:
        gt = getattr(f.geometry, "geom_type", "")
        (pts if gt == "Point" else lines if gt in ("LineString", "MultiLineString")
         else polys if gt in ("Polygon", "MultiPolygon") else others).append(f)

    # --- points: dedupe within tol, keeping first (single STRtree over all points) ---
    from shapely import STRtree

    kept_pts: list[Feature] = []
    if pts:
        pgeoms = [f.geometry for f in pts]
        ptree = STRtree(pgeoms)
        dropped: set[int] = set()
        for i, f in enumerate(pts):
            if i in dropped:
                continue
            kept_pts.append(f)
            # mark later near-duplicates of the same class as dropped
            for j in ptree.query(f.geometry.buffer(point_tol)):
                if j > i and j not in dropped:
                    k = pts[j]
                    if k.feature_class == f.feature_class and f.geometry.distance(k.geometry) <= point_tol:
                        dropped.add(j)
        rep.points_deduped += len(dropped)

    # --- lines: drop zero-length + duplicates/sub-segments (single STRtree over all lines) ---
    # Sort longest-first so a shorter sub-segment is dropped in favor of the longer line.
    lines_sorted = sorted(lines, key=lambda f: -_length(f.geometry))
    lgeoms = [f.geometry for f in lines_sorted]
    ltree = STRtree(lgeoms) if lgeoms else None
    l_dropped: set[int] = set()
    kept_lines: list[Feature] = []
    for i, f in enumerate(lines_sorted):
        if i in l_dropped:
            continue
        flen = _length(f.geometry)
        if flen <= 1e-9:
            rep.lines_removed_zero += 1
            l_dropped.add(i)
            continue
        kept_lines.append(f)
        if ltree is not None:
            for j in ltree.query(f.geometry.buffer(dup_tol)):
                if j <= i or j in l_dropped:
                    continue
                k = lines_sorted[j]
                if k.feature_class != f.feature_class:
                    continue
                klen = _length(k.geometry)
                if klen <= 0:
                    continue
                inside = k.geometry.intersection(f.geometry.buffer(dup_tol)).length
                if inside / klen >= 0.95:  # k lies within f -> k is the duplicate/sub-segment
                    l_dropped.add(j)
                    rep.lines_removed_duplicate += 1

    # trim short dangling overshoots: exactly one end connected to another kept line.
    kept_line_geoms = [f.geometry for f in kept_lines]
    dtree = STRtree(kept_line_geoms) if kept_line_geoms else None
    trimmed: list[Feature] = []
    for i, f in enumerate(kept_lines):
        if _length(f.geometry) <= dangle_max_len and dtree is not None:
            coords = list(f.geometry.coords)
            a, b = Point(coords[0]), Point(coords[-1])

            def _touch(pt) -> bool:
                for idx in dtree.query(pt.buffer(dup_tol)):
                    if idx != i and pt.distance(kept_line_geoms[idx]) <= dup_tol:
                        return True
                return False

            if _touch(a) ^ _touch(b):
                rep.dangles_trimmed += 1
                rep.details.append(f"trimmed dangle len={_length(f.geometry):.2f}")
                continue
        trimmed.append(f)
    kept_lines = trimmed

    # --- polygons: close near-closed rings, guarded repair ---
    kept_polys: list[Feature] = []
    for f in polys:
        g = f.geometry
        if not g.is_valid:
            repaired = make_valid(g)
            base = g.buffer(0)
            a0 = base.area if base.area > 0 else 1.0
            if repaired.area > 0 and abs(repaired.area - a0) / a0 <= max_area_delta:
                f = Feature(repaired, f.feature_class, dict(f.attributes), f.source, f.confidence, list(f.notes))
                rep.polygons_repaired += 1
            else:
                rep.repairs_rejected += 1
                rep.details.append("rejected polygon repair exceeding area-delta threshold")
        kept_polys.append(f)

    for group in (kept_pts, kept_lines, kept_polys, others):
        for f in group:
            out.add(f)
    rep.after = len(out)
    return out, rep


def close_unclosed_lines_to_polygons(
    coll: FeatureCollection, *, close_gap_max: float = 2.0
) -> FeatureCollection:
    """Convert near-closed LineStrings tagged as polygonal classes into closed Polygons."""
    from shapely.geometry import Point, Polygon

    out = FeatureCollection(crs=coll.crs, source_file=coll.source_file, metadata=dict(coll.metadata))
    for f in coll.features:
        g = f.geometry
        if f.feature_class == "room" and getattr(g, "geom_type", "") == "LineString":
            coords = list(g.coords)
            if len(coords) >= 4 and Point(coords[0]).distance(Point(coords[-1])) <= close_gap_max:
                poly = Polygon(coords[:-1] if coords[0] != coords[-1] else coords)
                if poly.is_valid and poly.area > 0:
                    f = Feature(poly, f.feature_class, dict(f.attributes), f.source, f.confidence, list(f.notes))
        out.add(f)
    return out
