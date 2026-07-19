#!/usr/bin/env python3
"""
Graded Topology Builder — FTTH CAD-to-GIS Pipeline
===================================================
Self-contained topology repair module operating on a written GeoPackage.

Implements the three-state graded snapping policy (spec component B):
  1. SNAP       endpoint distance <= snap_tol
                -> endpoint geometry moved onto the node, ORIGINE/EXTREMITE
                   filled with the node CODE.
  2. ATTR_ONLY  snap_tol < distance <= isolation_threshold
                -> geometry untouched, ORIGINE/EXTREMITE filled with the
                   nearest node CODE, feature listed in quarantine_review.
  3. FLOATING   distance > isolation_threshold
                -> ORIGINE/EXTREMITE left empty, feature listed in
                   quarantine_review, counted as FLOATING_CABLE.

Also:
  - Generates unique node CODEs for SITE/BOITE/PTECH (prefers attached
    annotation text found in configurable source fields, falls back to a
    deterministic {FC}-{seq:04d} code).
  - Flags ISOLATED_NODE (node referenced by no edge endpoint, guide Agent 4).
  - Writes the QUARANTINE review list as an attribute-only GPKG table
    (default name: quarantine_review) with FC/FID/reason/distance/suggestion.
  - Recomputes LONGUEUR for edges whose geometry was modified (CRS aware:
    planar length for projected CRS, haversine metres for geographic CRS).

Tolerances are expressed in CRS units: metres under EPSG:3857 (default
pipeline CRS), degrees under EPSG:4326.

Usage:
  python topology_builder.py --gpkg output/FILE.gpkg \
      [--snap-tol 5.0] [--isolation-threshold 30.0] \
      [--metrics topology_metrics.json] [--code-fields NOM,COMMENT]
"""

import argparse
import heapq
import json
import math
import sys
from collections import defaultdict

from osgeo import ogr

from shapely.geometry import Point
from shapely.strtree import STRtree

ogr.UseExceptions()

NODE_LAYERS = ("PTECH",)
EDGE_LAYERS = ("CABLE", "INFRASTRUCTURE")

DEFAULT_SNAP_TOL = 5.0
DEFAULT_ISOLATION_THRESHOLD = 30.0
DEFAULT_CODE_SOURCE_FIELDS = ("NOM", "COMMENT")
QUARANTINE_TABLE = "quarantine_review"

EARTH_RADIUS_M = 6371008.8


# ── Layer / field helpers ─────────────────────────────────────────────────────

def _normalize_layer_name(name):
    name = name.strip().upper()
    for ch in ('_', '-', '.'):
        if name.startswith(ch):
            name = name[1:]
        if name.endswith(ch):
            name = name[:-1]
    return name


def _find_layer(ds, canonical):
    """Find a layer whose normalized name equals or ends with the canonical name."""
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayerByIndex(i)
        base = _normalize_layer_name(lyr.GetName())
        if base == canonical or base.endswith(f"_{canonical}") or base.endswith(canonical):
            return lyr
    return None


def _field_idx(lyr, field_name):
    return lyr.GetLayerDefn().GetFieldIndex(field_name)


# ── CRS-aware measurement ─────────────────────────────────────────────────────

def _haversine_m(x1, y1, x2, y2):
    """Great-circle distance in metres between two lon/lat points."""
    phi1, phi2 = math.radians(y1), math.radians(y2)
    dphi = phi2 - phi1
    dlmb = math.radians(x2 - x1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _line_length(coords, is_geographic):
    total = 0.0
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        if is_geographic:
            total += _haversine_m(x1, y1, x2, y2)
        else:
            total += math.hypot(x2 - x1, y2 - y1)
    return total


WEBMERC_RADIUS_M = 6378137.0
_DEGREE_M = math.pi * WEBMERC_RADIUS_M / 180.0


def _metric_scale(ds, layers=EDGE_LAYERS + NODE_LAYERS):
    """Per-axis factors converting CRS coordinate deltas to ground metres.

    Tolerances are specified in metres and every distance comparison runs in
    ground-metric space, so chaining/snapping decisions come out identical
    whether the GeoPackage is EPSG:4326 (anisotropic degrees) or EPSG:3857
    (latitude-inflated metres)."""
    for fc in layers:
        lyr = _find_layer(ds, fc)
        if lyr is None or lyr.GetSpatialRef() is None:
            continue
        if lyr.GetFeatureCount() == 0:
            continue
        srs = lyr.GetSpatialRef()
        try:
            _minx, _maxx, miny, maxy = lyr.GetExtent()
        except Exception:
            continue
        y_mid = (miny + maxy) / 2.0
        if srs.IsGeographic():
            return _DEGREE_M * math.cos(math.radians(y_mid)), _DEGREE_M
        if (srs.GetAuthorityCode(None) or "") == "3857":
            lat0 = 2.0 * math.atan(math.exp(y_mid / WEBMERC_RADIUS_M)) - math.pi / 2.0
            s = math.cos(lat0)
            return s, s
        return 1.0, 1.0
    return 1.0, 1.0


# ── Node CODE generation ──────────────────────────────────────────────────────

def _sanitize_code(text):
    if text is None:
        return None
    code = str(text).strip().upper()
    code = "-".join(code.split())
    code = "".join(c for c in code if c.isalnum() or c in "-_")
    return code[:40] or None


def _unique_code(candidate, used):
    if candidate not in used:
        return candidate
    n = 2
    while f"{candidate}-{n}" in used:
        n += 1
    return f"{candidate}-{n}"


def generate_node_codes(ds, node_layers=NODE_LAYERS, code_source_fields=DEFAULT_CODE_SOURCE_FIELDS):
    """
    Ensure every node feature has a unique CODE (unique across all node layers,
    since CABLE.ORIGINE/EXTREMITE resolve against the merged BOITE+SITE code space).

    Returns (nodes, stats) where nodes is a list of dicts
    {fc, fid, code, x, y} and stats counts generated/annotation-derived codes.
    """
    nodes = []
    used = set()
    stats = {"codes_existing": defaultdict(int),
             "codes_from_annotation": defaultdict(int),
             "codes_generated": defaultdict(int),
             "nodes_centroid_fallback": defaultdict(int),
             "nodes_no_geometry": defaultdict(int)}
    no_geom_entries = []

    # First pass: register all pre-existing codes so generation never collides.
    for fc in node_layers:
        lyr = _find_layer(ds, fc)
        if lyr is None:
            continue
        code_idx = _field_idx(lyr, "CODE")
        if code_idx < 0:
            continue
        lyr.ResetReading()
        for feat in lyr:
            val = feat.GetField(code_idx)
            if val is not None and str(val).strip() != '':
                used.add(str(val).strip().upper())

    for fc in node_layers:
        lyr = _find_layer(ds, fc)
        if lyr is None:
            continue
        code_idx = _field_idx(lyr, "CODE")
        if code_idx < 0:
            continue
        src_idxs = [(f, _field_idx(lyr, f)) for f in code_source_fields]
        src_idxs = [(f, i) for f, i in src_idxs if i >= 0]

        pending = []
        lyr.ResetReading()
        for feat in lyr:
            fid = feat.GetFID()
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty():
                stats["nodes_no_geometry"][fc] += 1
                no_geom_entries.append({
                    "fc_name": fc, "feature_fid": fid, "feature_code": None,
                    "reason": "NODE_NO_GEOMETRY", "distance": None, "nearest_code": None,
                    "suggestion": "Node feature has no geometry; excluded from topology.",
                })
                continue
            if ogr.GT_Flatten(geom.GetGeometryType()) == ogr.wkbPoint:
                x, y = geom.GetX(), geom.GetY()
            else:
                # Legacy data may carry linework/polygons in point layers; use centroid.
                centroid = geom.Centroid()
                x, y = centroid.GetX(), centroid.GetY()
                stats["nodes_centroid_fallback"][fc] += 1
            existing = feat.GetField(code_idx)
            if existing is not None and str(existing).strip() != '':
                code = str(existing).strip().upper()
                stats["codes_existing"][fc] += 1
                nodes.append({"fc": fc, "fid": fid, "code": code, "x": x, "y": y})
                continue
            annotation = None
            for _fname, i in src_idxs:
                cand = _sanitize_code(feat.GetField(i))
                if cand:
                    annotation = cand
                    break
            pending.append((fid, x, y, annotation))

        seq = 0
        for fid, x, y, annotation in sorted(pending, key=lambda p: p[0]):
            if annotation:
                code = _unique_code(annotation, used)
                stats["codes_from_annotation"][fc] += 1
            else:
                seq += 1
                candidate = f"{fc}-{seq:04d}"
                code = _unique_code(candidate, used)
                stats["codes_generated"][fc] += 1
            used.add(code)
            feat = lyr.GetFeature(fid)
            feat.SetField(code_idx, code)
            lyr.SetFeature(feat)
            nodes.append({"fc": fc, "fid": fid, "code": code, "x": x, "y": y})

    stats = {k: dict(v) for k, v in stats.items()}
    return nodes, stats, no_geom_entries


# ── Graded endpoint repair ────────────────────────────────────────────────────

def classify_endpoint(distance, snap_tol, isolation_threshold):
    """Three-state policy: SNAP / ATTR_ONLY / FLOATING."""
    if distance is None:
        return "FLOATING"
    if distance <= snap_tol:
        return "SNAP"
    if distance <= isolation_threshold:
        return "ATTR_ONLY"
    return "FLOATING"


def repair_edges(ds, nodes, snap_tol, isolation_threshold,
                 edge_layers=EDGE_LAYERS):
    """
    Apply graded snapping to every edge (CABLE/INFRASTRUCTURE) endpoint.

    snap_tol / isolation_threshold are ground metres (see _metric_scale).

    Returns (metrics, quarantine_entries, referenced_codes).
    """
    sx, sy = _metric_scale(ds, layers=tuple(edge_layers) + NODE_LAYERS)
    node_points = [Point(n["x"] * sx, n["y"] * sy) for n in nodes]
    tree = STRtree(node_points) if node_points else None

    quarantine = []
    referenced = set()
    counters = {"snapped": 0, "attr_only": 0, "floating": 0, "total": 0}
    edge_counts = {}
    floating_edges = 0
    self_loops = 0
    attr_overwrites = 0

    is_geographic = False
    for fc in edge_layers:
        lyr = _find_layer(ds, fc)
        if lyr is not None and lyr.GetSpatialRef() is not None:
            is_geographic = bool(lyr.GetSpatialRef().IsGeographic())
            break

    def nearest_node(x, y):
        if tree is None:
            return None, None
        idx = int(tree.nearest(Point(x * sx, y * sy)))
        n = nodes[idx]
        return n, math.hypot((n["x"] - x) * sx, (n["y"] - y) * sy)

    for fc in edge_layers:
        lyr = _find_layer(ds, fc)
        if lyr is None:
            edge_counts[fc] = 0
            continue
        edge_counts[fc] = lyr.GetFeatureCount()
        orig_idx = _field_idx(lyr, "ORIGINE")
        ext_idx = _field_idx(lyr, "EXTREMITE")
        code_idx = _field_idx(lyr, "CODE")
        long_idx = _field_idx(lyr, "LONGUEUR")

        lyr.ResetReading()
        fids = [feat.GetFID() for feat in lyr]
        for fid in fids:
            feat = lyr.GetFeature(fid)
            feat_code = feat.GetField(code_idx) if code_idx >= 0 else None
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty():
                quarantine.append({
                    "fc_name": fc, "feature_fid": fid, "feature_code": feat_code,
                    "reason": "EDGE_NO_GEOMETRY", "distance": None, "nearest_code": None,
                    "suggestion": "Edge feature has no geometry; skipped.",
                })
                continue
            gtype = ogr.GT_Flatten(geom.GetGeometryType())
            if gtype != ogr.wkbLineString:
                quarantine.append({
                    "fc_name": fc, "feature_fid": fid, "feature_code": feat_code,
                    "reason": "UNSUPPORTED_GEOMETRY", "distance": None, "nearest_code": None,
                    "suggestion": f"Geometry type {ogr.GeometryTypeToName(gtype)} not handled; "
                                  "expected LineString.",
                })
                continue

            coords = [(p[0], p[1]) for p in geom.GetPoints()]
            if len(coords) < 2:
                quarantine.append({
                    "fc_name": fc, "feature_fid": fid, "feature_code": feat_code,
                    "reason": "DEGENERATE_GEOMETRY", "distance": None, "nearest_code": None,
                    "suggestion": "LineString has fewer than 2 vertices; skipped.",
                })
                continue

            geom_dirty = False
            edge_floating = False
            assigned = {}

            for endpoint, field_name, field_idx, pos in (
                (coords[0], "ORIGINE", orig_idx, 0),
                (coords[-1], "EXTREMITE", ext_idx, len(coords) - 1),
            ):
                counters["total"] += 1
                node, dist = nearest_node(*endpoint)
                if node is None or dist is None or \
                        classify_endpoint(dist, snap_tol, isolation_threshold) == "FLOATING":
                    counters["floating"] += 1
                    edge_floating = True
                    quarantine.append({
                        "fc_name": fc, "feature_fid": fid, "feature_code": feat_code,
                        "reason": f"FLOATING_ENDPOINT_{field_name}",
                        "distance": round(dist, 6) if dist is not None else None,
                        "nearest_code": node["code"] if node else None,
                        "suggestion": "No node within isolation threshold: "
                                      f"{field_name} left empty. Digitize the missing node "
                                      "or correct the cable endpoint.",
                    })
                    continue

                action = classify_endpoint(dist, snap_tol, isolation_threshold)
                if action == "SNAP":
                    other = coords[-1] if pos == 0 else coords[0]
                    # 1e-3 m: co-located distinct nodes (e.g. a BOITE on a
                    # pole) can sit ~1e-7 m apart after CRS transforms; a
                    # 2-point segment snapped onto both would round its
                    # LONGUEUR to 0.000
                    if len(coords) == 2 and \
                            math.hypot((node["x"] - other[0]) * sx,
                                       (node["y"] - other[1]) * sy) < 1e-3:
                        # moving this endpoint onto the node would collapse the
                        # 2-point segment to zero length; keep geometry, treat
                        # as attribute-only (LONGUEUR must stay non-zero)
                        counters["attr_only"] += 1
                        quarantine.append({
                            "fc_name": fc, "feature_fid": fid, "feature_code": feat_code,
                            "reason": f"SNAP_WOULD_COLLAPSE_{field_name}",
                            "distance": round(dist, 6), "nearest_code": node["code"],
                            "suggestion": "Both endpoints resolve to the same node; "
                                          "geometry left untouched to keep a non-zero "
                                          "length. Review the stub segment.",
                        })
                    else:
                        counters["snapped"] += 1
                        if dist > 0:
                            coords[pos] = (node["x"], node["y"])
                            geom_dirty = True
                else:  # ATTR_ONLY
                    counters["attr_only"] += 1
                    quarantine.append({
                        "fc_name": fc, "feature_fid": fid, "feature_code": feat_code,
                        "reason": f"ENDPOINT_BEYOND_SNAP_TOL_{field_name}",
                        "distance": round(dist, 6), "nearest_code": node["code"],
                        "suggestion": "Endpoint beyond snap tolerance but within isolation "
                                      "threshold: attribute assigned, geometry left untouched. "
                                      "Review endpoint placement.",
                    })

                if field_idx >= 0:
                    old = feat.GetField(field_idx)
                    if old is not None and str(old).strip() != '' and \
                            str(old).strip().upper() != node["code"]:
                        attr_overwrites += 1
                        quarantine.append({
                            "fc_name": fc, "feature_fid": fid, "feature_code": feat_code,
                            "reason": f"ATTR_CONFLICT_OVERWRITTEN_{field_name}",
                            "distance": round(dist, 6), "nearest_code": node["code"],
                            "suggestion": f"Existing {field_name}='{old}' replaced by nearest "
                                          f"node code '{node['code']}'. Verify reference.",
                        })
                    feat.SetField(field_idx, node["code"])
                    assigned[field_name] = node["code"]
                    referenced.add(node["code"])

            if assigned.get("ORIGINE") and assigned.get("ORIGINE") == assigned.get("EXTREMITE"):
                self_loops += 1
                quarantine.append({
                    "fc_name": fc, "feature_fid": fid, "feature_code": feat_code,
                    "reason": "SELF_LOOP", "distance": None,
                    "nearest_code": assigned["ORIGINE"],
                    "suggestion": "ORIGINE equals EXTREMITE after repair (rule 6.6a). "
                                  "Both endpoints resolve to the same node; review geometry.",
                })

            if edge_floating:
                floating_edges += 1

            if geom_dirty:
                new_geom = ogr.Geometry(ogr.wkbLineString)
                for x, y in coords:
                    new_geom.AddPoint_2D(x, y)
                feat.SetGeometry(new_geom)
                if long_idx >= 0:
                    feat.SetField(long_idx, round(_line_length(coords, is_geographic), 3))
            lyr.SetFeature(feat)

    metrics = {
        "endpoints": counters,
        "edges": {**edge_counts, "total": sum(edge_counts.values())},
        "repairs": {"SNAP": counters["snapped"]},
        "floating_cables": floating_edges,
        "self_loops": self_loops,
        "attr_overwrites": attr_overwrites,
    }
    return metrics, quarantine, referenced


def flag_isolated_nodes(ds, nodes, referenced, edge_layers=EDGE_LAYERS):
    """
    ISOLATED_NODE (guide Agent 4): node with no incident edge, i.e. its CODE is
    referenced by no edge ORIGINE/EXTREMITE (pre-existing references included).
    """
    for fc in edge_layers:
        lyr = _find_layer(ds, fc)
        if lyr is None:
            continue
        for field_name in ("ORIGINE", "EXTREMITE"):
            idx = _field_idx(lyr, field_name)
            if idx < 0:
                continue
            lyr.ResetReading()
            for feat in lyr:
                val = feat.GetField(idx)
                if val is not None and str(val).strip() != '':
                    referenced.add(str(val).strip().upper())

    isolated_by_layer = defaultdict(int)
    entries = []
    for n in nodes:
        if n["code"] not in referenced:
            isolated_by_layer[n["fc"]] += 1
            entries.append({
                "fc_name": n["fc"], "feature_fid": n["fid"], "feature_code": n["code"],
                "reason": "ISOLATED_NODE", "distance": None, "nearest_code": None,
                "suggestion": "Node has no incident CABLE/INFRASTRUCTURE edge "
                              "(guide Agent 4 ISOLATED_NODE). Review connectivity.",
            })
    return dict(isolated_by_layer), entries


# ── Quarantine table ──────────────────────────────────────────────────────────

def write_quarantine_table(ds, entries, table_name=QUARANTINE_TABLE):
    """(Re)write the quarantine review list as an attribute-only GPKG table."""
    for i in range(ds.GetLayerCount()):
        if ds.GetLayerByIndex(i).GetName() == table_name:
            ds.DeleteLayer(i)
            break

    lyr = ds.CreateLayer(table_name, geom_type=ogr.wkbNone)
    lyr.CreateField(ogr.FieldDefn("fc_name", ogr.OFTString))
    fid_field = ogr.FieldDefn("feature_fid", ogr.OFTInteger64)
    lyr.CreateField(fid_field)
    lyr.CreateField(ogr.FieldDefn("feature_code", ogr.OFTString))
    lyr.CreateField(ogr.FieldDefn("reason", ogr.OFTString))
    lyr.CreateField(ogr.FieldDefn("distance", ogr.OFTReal))
    lyr.CreateField(ogr.FieldDefn("nearest_code", ogr.OFTString))
    lyr.CreateField(ogr.FieldDefn("suggestion", ogr.OFTString))

    defn = lyr.GetLayerDefn()
    for e in entries:
        feat = ogr.Feature(defn)
        for key in ("fc_name", "feature_code", "reason", "nearest_code", "suggestion"):
            if e.get(key) is not None:
                feat.SetField(key, str(e[key]))
        if e.get("feature_fid") is not None:
            feat.SetField("feature_fid", int(e["feature_fid"]))
        if e.get("distance") is not None:
            feat.SetField("distance", float(e["distance"]))
        lyr.CreateFeature(feat)
        feat = None
    return len(entries)


# ── Orchestrator ──────────────────────────────────────────────────────────────

def repair_gpkg(gpkg_path,
                snap_tol=DEFAULT_SNAP_TOL,
                isolation_threshold=DEFAULT_ISOLATION_THRESHOLD,
                node_layers=NODE_LAYERS,
                edge_layers=EDGE_LAYERS,
                code_source_fields=DEFAULT_CODE_SOURCE_FIELDS,
                quarantine_table=QUARANTINE_TABLE,
                extra_quarantine_entries=None,
                metrics_path=None):
    """
    Full graded-topology pass over a GeoPackage (in place).

    extra_quarantine_entries lets the caller (converter pipeline) merge its own
    review items (e.g. empty-source-layer reports like ZNRO) into the same table.

    Returns the metrics dict.
    """
    if snap_tol > isolation_threshold:
        raise ValueError("snap_tol must be <= isolation_threshold")

    ds = ogr.Open(gpkg_path, 1)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage for update: {gpkg_path}")

    crs_code = None
    is_geographic = None
    for fc in tuple(node_layers) + tuple(edge_layers):
        lyr = _find_layer(ds, fc)
        if lyr is not None and lyr.GetSpatialRef() is not None:
            srs = lyr.GetSpatialRef()
            crs_code = srs.GetAuthorityCode(None) or srs.GetAuthorityCode("PROJCS") \
                or srs.GetAuthorityCode("GEOGCS")
            is_geographic = bool(srs.IsGeographic())
            break

    ds.StartTransaction()
    try:
        nodes, code_stats, node_quarantine = generate_node_codes(
            ds, node_layers=node_layers, code_source_fields=code_source_fields)

        edge_metrics, edge_quarantine, referenced = repair_edges(
            ds, nodes, snap_tol, isolation_threshold, edge_layers=edge_layers)

        isolated_by_layer, isolated_quarantine = flag_isolated_nodes(
            ds, nodes, referenced, edge_layers=edge_layers)

        quarantine = node_quarantine + edge_quarantine + isolated_quarantine
        if extra_quarantine_entries:
            quarantine.extend(extra_quarantine_entries)
        n_quarantine = write_quarantine_table(ds, quarantine, table_name=quarantine_table)
        ds.CommitTransaction()
    except Exception:
        ds.RollbackTransaction()
        raise
    finally:
        ds = None

    node_counts = defaultdict(int)
    for n in nodes:
        node_counts[n["fc"]] += 1

    metrics = {
        "gpkg": gpkg_path,
        "crs": f"EPSG:{crs_code}" if crs_code else None,
        "crs_is_geographic": is_geographic,
        "tolerance_units": "metres (ground distance, CRS-aware)",
        "snap_tol": snap_tol,
        "isolation_threshold": isolation_threshold,
        "nodes": {**dict(node_counts), "total": len(nodes)},
        "node_codes": code_stats,
        "edges": edge_metrics["edges"],
        "endpoints": edge_metrics["endpoints"],
        "repairs": edge_metrics["repairs"],
        "network": {
            "floating_cables": edge_metrics["floating_cables"],
            "isolated_nodes": sum(isolated_by_layer.values()),
            "isolated_by_layer": isolated_by_layer,
            "self_loops": edge_metrics["self_loops"],
        },
        "attr_overwrites": edge_metrics["attr_overwrites"],
        "quarantine_entries": n_quarantine,
        "quarantine_table": quarantine_table,
    }

    if metrics_path:
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
    return metrics


# ── Cable chaining (spec B2): merge fragments into logical segments ──────────

CHAIN_SOURCE_FIELD = "source_fragments"
FDT_FIELD = "FDT_ID"
FDT_LINK_VALUE = "LINK"
DEFAULT_BRIDGE_MIN_COS = math.cos(math.radians(30.0))
BRIDGE_LAYER_FIELD = "dwg_layer"


def _ensure_field(lyr, name, ogr_type=None):
    if lyr.GetLayerDefn().GetFieldIndex(name) < 0:
        lyr.CreateField(ogr.FieldDefn(name, ogr_type if ogr_type is not None
                                      else ogr.OFTString))
    return lyr.GetLayerDefn().GetFieldIndex(name)


def _collect_node_points(ds, node_layers):
    pts = []
    for fc in node_layers:
        lyr = _find_layer(ds, fc)
        if lyr is None:
            continue
        lyr.ResetReading()
        for feat in lyr:
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty():
                continue
            if ogr.GT_Flatten(geom.GetGeometryType()) == ogr.wkbPoint:
                pts.append((geom.GetX(), geom.GetY()))
            else:
                c = geom.Centroid()
                pts.append((c.GetX(), c.GetY()))
    return pts


class _Clusters:
    """Union-find endpoint clustering on a tolerance grid."""

    def __init__(self, tol):
        self.tol = max(tol, 1e-9)
        self.parent = {}
        self.grid = defaultdict(list)

    def _find(self, k):
        while self.parent.setdefault(k, k) != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def _union(self, a, b):
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self.parent[ra] = rb

    def add(self, x, y):
        key = (round(x, 9), round(y, 9))
        if key not in self.parent:
            self.parent[key] = key
            gx, gy = math.floor(x / self.tol), math.floor(y / self.tol)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for ox, oy, okey in self.grid[(gx + dx, gy + dy)]:
                        if math.hypot(ox - x, oy - y) <= self.tol:
                            self._union(key, okey)
            self.grid[(gx, gy)].append((x, y, key))
        return self._find(key)

    def root(self, x, y):
        return self._find((round(x, 9), round(y, 9)))


def _split_coords_at_nodes(coords, nodes_nearby, tol, min_spacing):
    """Cut a polyline where it passes within tol of a node; returns pieces.

    Cuts happen at the projection of the node onto the line (geometry is
    never moved onto the node here — the graded snapper decides that)."""
    cum = [0.0]
    for (ax, ay), (bx, by) in zip(coords[:-1], coords[1:]):
        cum.append(cum[-1] + math.hypot(bx - ax, by - ay))
    total = cum[-1]
    if total <= 2 * min_spacing:
        return [coords]
    cuts = []
    for nx, ny in nodes_nearby:
        best = None  # (dist, measure, point)
        for i, ((ax, ay), (bx, by)) in enumerate(zip(coords[:-1], coords[1:])):
            dx, dy = bx - ax, by - ay
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((nx - ax) * dx + (ny - ay) * dy) / seg2))
            px, py = ax + t * dx, ay + t * dy
            d = math.hypot(nx - px, ny - py)
            if best is None or d < best[0]:
                best = (d, cum[i] + t * math.sqrt(seg2), (px, py))
        if best is not None and best[0] <= tol \
                and min_spacing < best[1] < total - min_spacing:
            cuts.append((best[1], best[2]))
    if not cuts:
        return [coords]
    cuts.sort()
    dedup = []
    for m, p in cuts:
        if not dedup or m - dedup[-1][0] >= min_spacing:
            dedup.append((m, p))

    def _push(piece, pt):
        if not piece or math.hypot(piece[-1][0] - pt[0], piece[-1][1] - pt[1]) > 1e-9:
            piece.append(pt)

    pieces, cur, ci = [], [coords[0]], 0
    for i in range(len(coords) - 1):
        while ci < len(dedup) and dedup[ci][0] <= cum[i + 1]:
            _push(cur, dedup[ci][1])
            if len(cur) >= 2:
                pieces.append(cur)
            cur = [dedup[ci][1]]
            ci += 1
        _push(cur, coords[i + 1])
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces if pieces else [coords]


def chain_edges(ds, chain_tol, node_capture_tol, gap_bridge=False,
                bridge_tol=None, bridge_min_cos=None,
                edge_layers=("CABLE",), node_layers=NODE_LAYERS):
    """Cut cables at nodes, then merge fragments into logical segments.

    Runs BEFORE graded snapping. Three phases per edge layer:

      1. node split — a polyline passing within node_capture_tol of a
         SITE/BOITE/PTECH node is cut at the projection point, so segments
         terminate at nodes; route cables in this drawing run through
         poles/FATs mid-polyline, so without this cut the graded snapper
         never sees an endpoint at the node.
      2. weld + optional gap bridge — endpoints within chain_tol form one
         junction. Gap bridging is OFF by default (topology-fidelity-first,
         see guide/T_TOPOLOGY_REPAIR_ANALYSIS.md: on clean Hutabohu data
         bridging never fires, and on polluted data it manufactured 117
         cross-species misjoins). With gap_bridge=True, dangling ends pair
         greedily by ascending distance up to bridge_tol only when the
         continuation evidence beats the termination evidence (no node
         inside the snap band, partner end closer than the nearest node)
         AND the constrained-bridge filters pass: same dwg_layer and both
         end directions continue across the bridge with cosine >=
         bridge_min_cos (default cos 30°).
      3. chain walk — fragments join across pass-through junctions only
         (exactly two incident ends, no node within node_capture_tol), so
         no chain ever crosses a node or a branch point.

    Output segments record source-fragment lineage and get recomputed
    LONGUEUR. CODE uniqueness is preserved: extra features created by node
    splitting receive "-Sn"-suffixed codes.

    chain_tol / node_capture_tol / bridge_tol are ground metres; all
    distance work happens in a metric frame (see _metric_scale) so the
    results match across target CRSes. bridge_tol defaults to
    node_capture_tol when bridging is enabled.
    """
    sx, sy = _metric_scale(ds, layers=tuple(edge_layers) + tuple(node_layers))
    node_pts = [(x * sx, y * sy) for x, y in _collect_node_points(ds, node_layers)]
    node_tree = STRtree([Point(x, y) for x, y in node_pts]) if node_pts else None

    def node_distance(x, y):
        if node_tree is None:
            return float("inf")
        nx, ny = node_pts[int(node_tree.nearest(Point(x, y)))]
        return math.hypot(nx - x, ny - y)

    metrics = {"input_fragments": 0, "node_splits": 0, "parts_after_split": 0,
               "output_segments": 0, "chains_merged": 0,
               "fragments_absorbed": 0, "gap_bridges": 0,
               "gap_bridge_enabled": bool(gap_bridge),
               "bridge_rejected_species": 0, "bridge_rejected_angle": 0,
               "new_features_created": 0,
               "junctions": {"total": 0, "node_cut": 0, "degree_cut": 0,
                             "pass_through": 0},
               "longest_chain_fragments": 0}
    if bridge_min_cos is None:
        bridge_min_cos = DEFAULT_BRIDGE_MIN_COS

    for fc in edge_layers:
        lyr = _find_layer(ds, fc)
        if lyr is None:
            continue
        code_idx = _field_idx(lyr, "CODE")
        long_idx = _field_idx(lyr, "LONGUEUR")
        src_idx = _ensure_field(lyr, CHAIN_SOURCE_FIELD)
        code_idx = _field_idx(lyr, "CODE")
        layer_idx = _field_idx(lyr, BRIDGE_LAYER_FIELD)

        originals = {}
        used_codes = set()
        lyr.ResetReading()
        for feat in lyr:
            code = feat.GetField(code_idx) if code_idx >= 0 else None
            if code:
                used_codes.add(str(code))
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty() \
                    or ogr.GT_Flatten(geom.GetGeometryType()) != ogr.wkbLineString:
                continue
            coords = [(p[0] * sx, p[1] * sy) for p in geom.GetPoints()]
            if len(coords) < 2:
                continue
            dwg_layer = feat.GetField(layer_idx) if layer_idx >= 0 else None
            originals[feat.GetFID()] = {"coords": coords, "code": code,
                                        "layer": dwg_layer or ""}
        metrics["input_fragments"] += len(originals)

        # ── phase 1: node split ──
        parts = []  # {"parent", "coords", "length", "part_no", "n_parts"}
        min_spacing = max(chain_tol, 1e-6)
        for fid, orig in originals.items():
            coords = orig["coords"]
            xs = [p[0] for p in coords]
            ys = [p[1] for p in coords]
            pad = node_capture_tol
            nearby = [(nx, ny) for nx, ny in node_pts
                      if min(xs) - pad <= nx <= max(xs) + pad
                      and min(ys) - pad <= ny <= max(ys) + pad]
            pieces = _split_coords_at_nodes(coords, nearby, node_capture_tol,
                                            min_spacing)
            metrics["node_splits"] += len(pieces) - 1
            for k, piece in enumerate(pieces):
                parts.append({"parent": fid, "coords": piece,
                              "length": _line_length(piece, False),
                              "part_no": k + 1, "n_parts": len(pieces),
                              "layer": orig["layer"]})
        metrics["parts_after_split"] += len(parts)

        # ── phase 2: weld + gap bridge ──
        clusters = _Clusters(chain_tol)
        for part in parts:
            clusters.add(*part["coords"][0])
            clusters.add(*part["coords"][-1])

        def build_incident():
            part_ends, incident, cluster_xy = {}, defaultdict(list), {}
            for pid, part in enumerate(parts):
                ca = clusters.root(*part["coords"][0])
                cb = clusters.root(*part["coords"][-1])
                part_ends[pid] = (ca, cb)
                incident[ca].append((pid, 0))
                incident[cb].append((pid, 1))
                cluster_xy.setdefault(ca, part["coords"][0])
                cluster_xy.setdefault(cb, part["coords"][-1])
            return part_ends, incident, cluster_xy

        part_ends, incident, cluster_xy = build_incident()

        def end_direction(pid, end):
            c = parts[pid]["coords"]
            (ax, ay), (bx, by) = (c[0], c[1]) if end == 0 else (c[-1], c[-2])
            dx, dy = ax - bx, ay - by
            n = math.hypot(dx, dy)
            return (dx / n, dy / n) if n > 0 else (0.0, 0.0)

        eff_bridge_tol = None
        if gap_bridge:
            eff_bridge_tol = bridge_tol if bridge_tol is not None \
                else node_capture_tol
        if eff_bridge_tol and eff_bridge_tol > chain_tol:
            open_ends = []
            for cid, ends in incident.items():
                if len(ends) != 1:
                    continue
                x, y = cluster_xy[cid]
                nd = node_distance(x, y)
                if nd > node_capture_tol:
                    open_ends.append((cid, x, y, nd, ends[0][0], ends[0][1]))
            cell = eff_bridge_tol
            bgrid = defaultdict(list)
            for i, oe in enumerate(open_ends):
                bgrid[(math.floor(oe[1] / cell), math.floor(oe[2] / cell))].append(i)
            candidates = []
            for i, (ca, xa, ya, nda, pa, ea) in enumerate(open_ends):
                gx, gy = math.floor(xa / cell), math.floor(ya / cell)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for j in bgrid[(gx + dx, gy + dy)]:
                            if j <= i:
                                continue
                            cb, xb, yb, ndb, pb, eb = open_ends[j]
                            d = math.hypot(xb - xa, yb - ya)
                            if not (d <= eff_bridge_tol and d < nda and d < ndb):
                                continue
                            # constrained bridge: only continue a run of the
                            # same source layer, and only when the bridge is
                            # a straight continuation at both ends (a lateral
                            # weld of parallel cables has cos ~ 0)
                            if parts[pa]["layer"] != parts[pb]["layer"]:
                                metrics["bridge_rejected_species"] += 1
                                continue
                            if d > 0:
                                vx, vy = (xb - xa) / d, (yb - ya) / d
                                ua = end_direction(pa, ea)
                                ub = end_direction(pb, eb)
                                if ua[0] * vx + ua[1] * vy < bridge_min_cos \
                                        or -(ub[0] * vx + ub[1] * vy) < bridge_min_cos:
                                    metrics["bridge_rejected_angle"] += 1
                                    continue
                            # sort key quantized to 1 mm: the drawing has
                            # many exactly-equal gaps, and sub-micron CRS
                            # noise must not reorder those ties (greedy
                            # pairing would cascade differently between
                            # 4326 and 3857 runs); (i, j) breaks ties
                            # CRS-independently
                            candidates.append((round(d, 3), i, j))
            candidates.sort()
            used = set()
            for d, i, j in candidates:
                if i in used or j in used:
                    continue
                ca, cb = open_ends[i][0], open_ends[j][0]
                if incident[ca][0][0] == incident[cb][0][0]:
                    continue  # would close a fragment onto itself
                clusters._union(ca, cb)
                used.add(i)
                used.add(j)
                metrics["gap_bridges"] += 1
            if used:
                part_ends, incident, cluster_xy = build_incident()

        pass_through = set()
        for cid, ends in incident.items():
            metrics["junctions"]["total"] += 1
            if len(ends) != 2 or ends[0][0] == ends[1][0]:
                metrics["junctions"]["degree_cut"] += 1
                continue
            x, y = cluster_xy[cid]
            if node_distance(x, y) <= node_capture_tol:
                metrics["junctions"]["node_cut"] += 1
                continue
            pass_through.add(cid)
            metrics["junctions"]["pass_through"] += 1

        # ── phase 3: chain walk ──
        def other_end(pid, cid):
            ca, cb = part_ends[pid]
            return cb if cid == ca else ca

        visited = set()
        chains = []
        for pid in range(len(parts)):
            if pid in visited:
                continue
            visited.add(pid)
            chain = [pid]
            for direction in (1, 0):
                cid = part_ends[pid][direction]
                cur = pid
                while cid in pass_through:
                    a, b = incident[cid]
                    nxt = b[0] if a[0] == cur else a[0]
                    if nxt in visited:
                        break
                    visited.add(nxt)
                    if direction == 1:
                        chain.append(nxt)
                    else:
                        chain.insert(0, nxt)
                    cid = other_end(nxt, cid)
                    cur = nxt
            chains.append(chain)

        # ── write back ──
        def part_label(pid):
            part = parts[pid]
            base = str(originals[part["parent"]]["code"]
                       or f"fid:{part['parent']}")
            if part["n_parts"] > 1:
                return f"{base}#p{part['part_no']}"
            return base

        def merged_coords(chain):
            pts = None
            for i, pid in enumerate(chain):
                coords = parts[pid]["coords"]
                if i == 0:
                    if len(chain) > 1:
                        shared = None
                        for cid in part_ends[pid]:
                            if cid in part_ends[chain[1]]:
                                shared = cid
                                break
                        if shared is not None \
                                and clusters.root(*coords[-1]) != shared:
                            coords = list(reversed(coords))
                    pts = list(coords)
                    continue
                tail = pts[-1]
                d_first = math.hypot(coords[0][0] - tail[0],
                                     coords[0][1] - tail[1])
                d_last = math.hypot(coords[-1][0] - tail[0],
                                    coords[-1][1] - tail[1])
                if d_last < d_first:
                    coords = list(reversed(coords))
                    d_first = d_last
                pts.extend(coords[1:] if d_first <= chain_tol else coords)
            return pts

        def unique_split_code(base):
            n = 1
            candidate = f"{base}-S{n}"
            while candidate in used_codes:
                n += 1
                candidate = f"{base}-S{n}"
            used_codes.add(candidate)
            return candidate

        defn = lyr.GetLayerDefn()
        reused_fids = set()
        order = sorted(range(len(chains)),
                       key=lambda ci: -sum(parts[p]["length"] for p in chains[ci]))
        for ci in order:
            chain = chains[ci]
            pts = merged_coords(chain)
            longest_pid = max(chain, key=lambda p: parts[p]["length"])
            parent_fid = parts[longest_pid]["parent"]
            sources = ",".join(part_label(p) for p in chain)
            length = round(_line_length(pts, False), 3)

            new_geom = ogr.Geometry(ogr.wkbLineString)
            for x, y in pts:
                new_geom.AddPoint_2D(x / sx, y / sy)

            if parent_fid not in reused_fids:
                feat = lyr.GetFeature(parent_fid)
                feat.SetGeometry(new_geom)
                if long_idx >= 0:
                    feat.SetField(long_idx, length)
                feat.SetField(src_idx, sources)
                lyr.SetFeature(feat)
                reused_fids.add(parent_fid)
            else:
                parent = lyr.GetFeature(parent_fid)
                feat = ogr.Feature(defn)
                feat.SetFrom(parent)
                feat.SetFID(-1)
                feat.SetGeometry(new_geom)
                if code_idx >= 0:
                    base = originals[parent_fid]["code"] or f"{fc}{parent_fid}"
                    feat.SetField(code_idx, unique_split_code(str(base)))
                if long_idx >= 0:
                    feat.SetField(long_idx, length)
                feat.SetField(src_idx, sources)
                lyr.CreateFeature(feat)
                metrics["new_features_created"] += 1

            if len(chain) > 1:
                metrics["chains_merged"] += 1
                metrics["fragments_absorbed"] += len(chain) - 1
            metrics["output_segments"] += 1
            metrics["longest_chain_fragments"] = max(
                metrics["longest_chain_fragments"], len(chain))

        for fid in originals:
            if fid not in reused_fids:
                lyr.DeleteFeature(fid)

    return metrics


def chain_edges_gpkg(gpkg_path, chain_tol, node_capture_tol, gap_bridge=False,
                     bridge_tol=None, bridge_min_cos=None,
                     edge_layers=("CABLE",), node_layers=NODE_LAYERS):
    ds = ogr.Open(gpkg_path, 1)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage for update: {gpkg_path}")
    ds.StartTransaction()
    try:
        metrics = chain_edges(ds, chain_tol, node_capture_tol,
                              gap_bridge=gap_bridge, bridge_tol=bridge_tol,
                              bridge_min_cos=bridge_min_cos,
                              edge_layers=edge_layers, node_layers=node_layers)
        ds.CommitTransaction()
    except Exception:
        ds.RollbackTransaction()
        raise
    finally:
        ds = None
    return metrics


# ── FDT domain tagging (spec B3): FDT-01 / FDT-02 / LINK ─────────────────────

def tag_fdt_domains(ds, domain_prefixes, endpoint_tol,
                    edge_layers=("CABLE",), node_layers=NODE_LAYERS,
                    label_fields=("CODE", "display_label"),
                    link_value=FDT_LINK_VALUE):
    """Attribute-only domain decoupling: write FDT_ID on nodes and edges.

    domain_prefixes: {"FDT-01": "DMPH-1.010", ...} from layout facts.
    Seed nodes carry a label matching a domain prefix. Unlabeled vertices
    form components (after conceptually removing seeds); a component
    touching exactly one domain inherits it, touching several becomes the
    inter-domain LINK, touching none stays empty. Edges resolve from their
    endpoint verdicts (any LINK or cross-domain pair -> LINK). Geometry is
    never modified, so connectivity cannot change.

    endpoint_tol is ground metres (endpoint clustering runs in the metric
    frame from _metric_scale).
    """
    prefixes = {dom: p.strip().upper() for dom, p in domain_prefixes.items() if p}
    sx, sy = _metric_scale(ds, layers=tuple(edge_layers) + tuple(node_layers))

    def label_domain(*labels):
        for lab in labels:
            if lab is None:
                continue
            lab = str(lab).strip().upper()
            if not lab:
                continue
            for dom, pref in prefixes.items():
                if lab == pref or lab.startswith(pref + "."):
                    return dom
        return None

    # vertices: node codes; geometric clusters for endpoints without codes
    node_vertex = {}   # code -> vertex key
    seed_domain = {}   # vertex key -> domain
    node_feats = []    # (fc, fid, vertex_key)

    # Also scan BOITE for DMPH-prefixed FAT label seeds even though BOITE
    # is excluded from NODE_LAYERS (it supplies domain seeds, not routing).
    seed_layers = list(node_layers)
    if 'BOITE' not in seed_layers:
        seed_layers.append('BOITE')

    for fc in seed_layers:
        lyr = _find_layer(ds, fc)
        if lyr is None:
            continue
        _ensure_field(lyr, FDT_FIELD)
        idxs = [(_field_idx(lyr, f)) for f in label_fields]
        code_idx = _field_idx(lyr, "CODE")
        lyr.ResetReading()
        for feat in lyr:
            fid = feat.GetFID()
            code = feat.GetField(code_idx) if code_idx >= 0 else None
            code = str(code).strip().upper() if code else f"{fc}:{fid}"
            key = ("n", code)
            node_vertex[code] = key
            labels = [feat.GetField(i) for i in idxs if i >= 0]
            dom = label_domain(*labels)
            if dom:
                seed_domain[key] = dom
            node_feats.append((fc, fid, key))

    clusters = _Clusters(endpoint_tol)
    edges = []  # (fc, fid, vkey_a, vkey_b, length)
    for fc in edge_layers:
        lyr = _find_layer(ds, fc)
        if lyr is None:
            continue
        _ensure_field(lyr, FDT_FIELD)
        srs = lyr.GetSpatialRef()
        is_geographic = bool(srs.IsGeographic()) if srs is not None else False
        oi, ei = _field_idx(lyr, "ORIGINE"), _field_idx(lyr, "EXTREMITE")
        lyr.ResetReading()
        rows = []
        for feat in lyr:
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty() \
                    or ogr.GT_Flatten(geom.GetGeometryType()) != ogr.wkbLineString:
                continue
            pts = geom.GetPoints()
            ends = []
            for pos, idx in ((0, oi), (-1, ei)):
                code = feat.GetField(idx) if idx >= 0 else None
                code = str(code).strip().upper() if code and str(code).strip() else None
                ends.append((code, pts[pos][0], pts[pos][1]))
            length = _line_length([(p[0], p[1]) for p in pts], is_geographic)
            rows.append((feat.GetFID(), ends, length))
        for fid, ends, length in rows:
            vkeys = []
            for code, x, y in ends:
                if code and code in node_vertex:
                    vkeys.append(node_vertex[code])
                else:
                    vkeys.append(("g", clusters.add(x * sx, y * sy)))
            edges.append((fc, fid, vkeys[0], vkeys[1], length))
    # geometric cluster roots may have shifted during add(); re-resolve
    edges = [(fc, fid, a if a[0] == "n" else ("g", clusters._find(a[1])),
              b if b[0] == "n" else ("g", clusters._find(b[1])), length)
             for fc, fid, a, b, length in edges]

    # multi-source Dijkstra from labeled seeds: every vertex inherits the
    # domain of its metrically nearest seed, so hub-and-spur pole runs get
    # the domain they actually serve; LINK stays confined to the frontier
    # where the two floods meet (the inter-domain bridge), instead of
    # flooding the whole unlabeled pole network.
    adjacency = defaultdict(list)
    for _, _, a, b, length in edges:
        w = max(length, 1e-6)
        adjacency[a].append((b, w))
        adjacency[b].append((a, w))

    vertex_domain = dict(seed_domain)
    heap = [(0.0, str(v), v, dom) for v, dom in seed_domain.items()]
    heap.sort(key=lambda t: (t[0], t[1]))
    heapq.heapify(heap)
    dist = {v: 0.0 for v in seed_domain}
    while heap:
        d, _, v, dom = heapq.heappop(heap)
        if d > dist.get(v, float("inf")):
            continue
        vertex_domain[v] = dom
        for nb, w in adjacency[v]:
            nd = d + w
            if nd < dist.get(nb, float("inf")):
                dist[nb] = nd
                heapq.heappush(heap, (nd, str(nb), nb, dom))

    def vertex_verdict(v):
        return vertex_domain.get(v)

    def edge_verdict(a, b):
        va, vb = vertex_verdict(a), vertex_verdict(b)
        if link_value in (va, vb):
            return link_value
        if va and vb:
            return va if va == vb else link_value
        return va or vb

    stats = defaultdict(lambda: defaultdict(int))
    for fc, fid, a, b, _length in edges:
        lyr = _find_layer(ds, fc)
        idx = _field_idx(lyr, FDT_FIELD)
        verdict = edge_verdict(a, b)
        feat = lyr.GetFeature(fid)
        feat.SetField(idx, verdict if verdict else None)
        lyr.SetFeature(feat)
        stats[fc][verdict or "<empty>"] += 1

    for fc, fid, key in node_feats:
        lyr = _find_layer(ds, fc)
        idx = _field_idx(lyr, FDT_FIELD)
        verdict = vertex_verdict(key)
        feat = lyr.GetFeature(fid)
        feat.SetField(idx, verdict if verdict else None)
        lyr.SetFeature(feat)
        stats[fc][verdict or "<empty>"] += 1

    # structural connectivity (attribute-only pass, reported for the
    # "decoupling must not add components" acceptance check)
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, _, a, b, _length in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    components = len({find(a) for _, _, a, b, _length in edges})

    return {
        "domains": sorted(prefixes),
        "seeds": len(seed_domain),
        "fdt_id_distribution": {fc: dict(v) for fc, v in stats.items()},
        "edge_components": components,
    }


def tag_fdt_domains_gpkg(gpkg_path, domain_prefixes, endpoint_tol,
                         edge_layers=("CABLE",), node_layers=NODE_LAYERS):
    ds = ogr.Open(gpkg_path, 1)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage for update: {gpkg_path}")
    ds.StartTransaction()
    try:
        metrics = tag_fdt_domains(ds, domain_prefixes, endpoint_tol,
                                  edge_layers=edge_layers,
                                  node_layers=node_layers)
        ds.CommitTransaction()
    except Exception:
        ds.RollbackTransaction()
        raise
    finally:
        ds = None
    return metrics


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Graded topology repair for FTTH GeoPackages "
                    "(SNAP / ATTR_ONLY+quarantine / FLOATING+quarantine)")
    parser.add_argument("--gpkg", required=True, help="GeoPackage to repair in place")
    parser.add_argument("--snap-tol", type=float, default=DEFAULT_SNAP_TOL,
                        help="Snap tolerance in metres, ground distance (default 5.0)")
    parser.add_argument("--isolation-threshold", type=float, default=DEFAULT_ISOLATION_THRESHOLD,
                        help="Isolation threshold in metres, ground distance (default 30.0)")
    parser.add_argument("--code-fields", default=",".join(DEFAULT_CODE_SOURCE_FIELDS),
                        help="Comma-separated fields scanned for annotation-derived node CODEs")
    parser.add_argument("--quarantine-table", default=QUARANTINE_TABLE,
                        help="Name of the quarantine review table (default quarantine_review)")
    parser.add_argument("--metrics", default=None, help="Write topology metrics JSON here")
    parser.add_argument("--chain-tol", type=float, default=None,
                        help="Enable cable chaining before repair: endpoint "
                             "merge tolerance in metres (ground distance)")
    parser.add_argument("--node-capture-tol", type=float, default=None,
                        help="Chaining node-cut tolerance in metres "
                             "(default: snap-tol)")
    parser.add_argument("--enable-gap-bridge", action="store_true",
                        help="Enable the constrained gap bridge during "
                             "chaining (same dwg_layer + straight "
                             "continuation only; default off — "
                             "topology-fidelity-first)")
    parser.add_argument("--bridge-min-cos-deg", type=float, default=30.0,
                        help="Constrained bridge: max deviation angle in "
                             "degrees for the continuation test "
                             "(default 30)")
    parser.add_argument("--fdt-prefix", action="append", default=[],
                        metavar="DOMAIN=PREFIX",
                        help="Enable FDT_ID tagging after repair, e.g. "
                             "FDT-01=DMPH-1.010 (repeatable)")
    args = parser.parse_args()

    if args.chain_tol is not None:
        chain_metrics = chain_edges_gpkg(
            args.gpkg, args.chain_tol,
            args.node_capture_tol if args.node_capture_tol is not None
            else args.snap_tol,
            gap_bridge=args.enable_gap_bridge,
            bridge_min_cos=math.cos(math.radians(args.bridge_min_cos_deg)))
        print("Chaining:", json.dumps(chain_metrics, ensure_ascii=False, indent=2))

    code_fields = tuple(f.strip() for f in args.code_fields.split(",") if f.strip())
    metrics = repair_gpkg(
        args.gpkg,
        snap_tol=args.snap_tol,
        isolation_threshold=args.isolation_threshold,
        code_source_fields=code_fields,
        quarantine_table=args.quarantine_table,
        metrics_path=args.metrics,
    )

    if args.fdt_prefix:
        prefixes = {}
        for spec in args.fdt_prefix:
            if "=" not in spec:
                parser.error(f"--fdt-prefix expects DOMAIN=PREFIX, got: {spec}")
            dom, pref = spec.split("=", 1)
            prefixes[dom.strip()] = pref.strip()
        fdt_metrics = tag_fdt_domains_gpkg(
            args.gpkg, prefixes, endpoint_tol=args.snap_tol)
        print("FDT tagging:", json.dumps(fdt_metrics, ensure_ascii=False, indent=2))

    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
