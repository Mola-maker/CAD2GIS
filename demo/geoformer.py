#!/usr/bin/env python3
"""
GeoFormer — Multi-Stage Spatial-Semantic CAD-to-GIS Pipeline
DWG → GeoPackage (EPSG:3857) with parallel tile processing.

8-stage pipeline:
  Stage 1: Ingestion & Tiling       — Parse DWG, spatial tile decomposition
  Stage 2: CRS Regime Classification — DBSCAN clustering, regime voting
  Stage 3: Geometry Normalizer       — Coordinate transformation, WKT reconstruction
  Stage 4: Topology Surgeon          — Node snapping, dedup, closure, sliver elimination
  Stage 5: Semantic Weaver           — TEXT-to-geometry spatial-semantic linking
  Stage 6: Schema Alchemist          — Layer to feature class schema mapping
  Stage 7: Spatial Assembler         — Tile merge, GeoPackage write
  Stage 8: Quality Sentinel          — Metrics computation, quality report
"""

# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTS & ENVIRONMENT
# ═══════════════════════════════════════════════════════════════════════════════
import ctypes
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import argparse
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# ═══════════════════════════════════════════════════════════════════════════════
# CTYPES BRIDGE TO LIBREDWG  (exact copy from converter_3857.py lines 18-68)
# ═══════════════════════════════════════════════════════════════════════════════
_lib = ctypes.CDLL("/usr/local/lib/libredwg.so")
_libc = ctypes.CDLL("libc.so.6")

_lib.dwg_ent_get_layer_name.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_lib.dwg_ent_get_layer_name.restype = ctypes.c_char_p
_lib.dwg_ent_lwpline_get_numpoints.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_lib.dwg_ent_lwpline_get_numpoints.restype = ctypes.c_int
_lib.dwg_ent_lwpline_get_points.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
_lib.dwg_ent_lwpline_get_points.restype = ctypes.c_void_p
_libc.free.argtypes = [ctypes.c_void_p]


def _cstr(raw):
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.hex()


def _layer_name(entity_ptr):
    err = ctypes.c_int(0)
    return _cstr(_lib.dwg_ent_get_layer_name(entity_ptr, ctypes.byref(err)))


def _lwpoline_points(entity):
    try:
        lw_ptr = int(entity.tio.LWPOLYLINE.this)
    except Exception:
        return []
    err = ctypes.c_int(0)
    npts = _lib.dwg_ent_lwpline_get_numpoints(lw_ptr, ctypes.byref(err))
    if err.value or npts < 2:
        return []
    pts_ptr = _lib.dwg_ent_lwpline_get_points(lw_ptr, ctypes.byref(err))
    if err.value or not pts_ptr:
        return []
    pts = []
    for j in range(npts):
        off = j * 16
        pts.append((
            ctypes.c_double.from_address(pts_ptr + off).value,
            ctypes.c_double.from_address(pts_ptr + off + 8).value,
        ))
    _libc.free(pts_ptr)
    return pts


# ═══════════════════════════════════════════════════════════════════════════════
# LIBREDWG SWIG IMPORTS  (exact copy from converter_3857.py lines 72-84)
# ═══════════════════════════════════════════════════════════════════════════════
sys.path.insert(0, "/usr/local/lib/python3.12/dist-packages")
from LibreDWG import (  # noqa: E402
    Dwg_Data, dwg_read_file, new_Dwg_Object_Array, Dwg_Object_Array_getitem,
    DWG_SUPERTYPE_ENTITY,
    DWG_TYPE_LINE, DWG_TYPE_LWPOLYLINE, DWG_TYPE_CIRCLE, DWG_TYPE_ARC,
    DWG_TYPE_TEXT, DWG_TYPE_MTEXT, DWG_TYPE_INSERT, DWG_TYPE_POINT,
)

TYPE_NAMES = {}
import LibreDWG  # noqa: E402
for name in dir(LibreDWG):
    if name.startswith("DWG_TYPE_"):
        TYPE_NAMES[getattr(LibreDWG, name)] = name[9:]

# ═══════════════════════════════════════════════════════════════════════════════
# QGIS  (import only; QgsApplication.initQgis() called in main / Stage 7)
# ═══════════════════════════════════════════════════════════════════════════════
from qgis.core import (  # noqa: E402
    QgsApplication, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsFields, QgsVectorFileWriter, QgsCoordinateReferenceSystem,
)
from qgis.PyQt.QtCore import QVariant  # noqa: E402

# pyproj — used in Stage 3 (workers) and Stage 2 hypothesis testing
from pyproj import Transformer  # noqa: E402

# sklearn — optional, fallback provided
try:
    from sklearn.cluster import DBSCAN as SklearnDBSCAN
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Coordinate regime translation offsets (DWG → UTM 48N)
OX_A, OY_A = 292539, -405       # Regime A
OX_B, OY_B = 589239, 3203295    # Regime B

# Dongxi Town UTM 48N bounding box for regime hypothesis testing
UTM48N_BBOX = {
    "x_min": 659000, "x_max": 664000,
    "y_min": 3181000, "y_max": 3187000,
}

# Paper-space Y threshold: centroids with Y < PAPERSPACE_Y_MIN are discarded
PAPERSPACE_Y_MIN = -100000

# Paper-space X filter: entities with |X| beyond this are layout INSERTS (sheet space)
PAPERSPACE_X_ABS_MAX = 2000000

# Regime hint threshold: Y > REGIME_HINT_THRESHOLD → "A", else → "B"
REGIME_HINT_THRESHOLD = 100000

# Regime uncertainty boundary: entities within UNCERTAINTY_MARGIN of threshold → "UNKNOWN"
UNCERTAINTY_MARGIN = 1000

# DBSCAN skip threshold: tiles with more than this many entities skip clustering
# and use Y-threshold regime classification directly (O(n²) fallback is too slow)
DBSCAN_MAX_ENTITIES = 10000

# DBSCAN parameters
DBSCAN_EPS = 500       # DWG units
DBSCAN_MIN_SAMPLES = 5

# Topology parameters
SNAP_TOLERANCE = 0.05          # metres (EPSG:3857)
OVERSHOOT_CLIP_RANGE = 0.5     # metres
HAUSDORFF_THRESHOLD = 0.001    # metres (duplicate arc removal)
SLIVER_AREA_THRESHOLD = 0.1    # sq metres
SLIVER_ASPECT_RATIO = 100.0    # ratio

# Semantic weaving
LINKAGE_CONFIDENCE_THRESHOLD = 0.6
SPATIAL_SIGMA_FACTOR = 1.0     # multiplier for adaptive sigma
TEXT_SEARCH_RADIUS_FACTOR = 5.0  # search radius = sigma * factor

# Tile boundary merge
TILE_BOUNDARY_MERGE_RANGE = 5.0  # metres

# Uncertainty threshold for human review
UNCERTAINTY_THRESHOLD_FRACTION = 0.05

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def _setup_logging():
    logger = logging.getLogger("geoformer")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.DEBUG)
        fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


log = _setup_logging()


def stage_log(stage, msg, *args):
    if args:
        msg = msg % args
    log.info("[STAGE%d] %s", stage, msg)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG LOADING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_SCHEMA_MAPPING = {
    "DGX":    {"fc": "fc_contours",      "geom": "LineString", "attrs": {"contour_value_m": "float", "annotation_text": "string"}},
    "DMTZ":   {"fc": "fc_terrain_soil",  "geom": "Polygon",    "attrs": {"terrain_type": "string", "annotation_text": "string"}},
    "GCD":    {"fc": "fc_elevation_pts", "geom": "Point",      "attrs": {"elevation_m": "float", "annotation_text": "string"}},
    "JMD":    {"fc": "fc_residential",   "geom": "Polygon",    "attrs": {"building_name": "string", "annotation_text": "string"}},
    "DLSS":   {"fc": "fc_roads",         "geom": "LineString", "attrs": {"road_number": "string", "road_name": "string", "annotation_text": "string"}},
    "GXYZ":   {"fc": "fc_pipelines",     "geom": "LineString", "attrs": {"pipeline_type": "string", "annotation_text": "string"}},
    "SXSS":   {"fc": "fc_water",         "geom": "LineString", "attrs": {"waterway_name": "string", "annotation_text": "string"}},
    "ZBTZ":   {"fc": "fc_vegetation",    "geom": "Polygon",    "attrs": {"vegetation_type": "string"}},
    "comm_civil": {"fc": "fc_comm_civil","geom": "Polygon",    "attrs": {"civil_type": "string", "annotation_text": "string"}},
    "comm_line":  {"fc": "fc_comm_lines","geom": "LineString", "attrs": {"line_id": "string", "length_m": "float"}},
    "elec_pipe":  {"fc": "fc_power_pipe","geom": "LineString", "attrs": {"pipe_type": "string"}},
    "drainage":   {"fc": "fc_drainage",  "geom": "LineString", "attrs": {"drainage_id": "string"}},
}

DEFAULT_TOPOLOGY_RULES = {
    "DGX":  {"must_not_overlap": True, "snap_tolerance": 0.05},
    "JMD":  {"must_be_closed_polygon": True, "sliver_area_threshold": 0.5},
    "DLSS": {"must_not_have_undershoots": True, "snap_tolerance": 0.1},
    "GXYZ": {"must_not_have_undershoots": True, "snap_tolerance": 0.05},
}

DEFAULT_LAYER_VOCAB = {
    "DLSS": {"expected": ["road numbers", "road names", "speed limits"],
             "patterns": [r"^[Gg]\d+", r"^[Ss]\d+", r"^\d+", r"国道", r"省道", r"路", r"大道", r"街"]},
    "GCD":  {"expected": ["elevation values"],
             "patterns": [r"^-?\d+\.?\d*$"]},
    "DGX":  {"expected": ["contour intervals"],
             "patterns": [r"^\d+$"]},
    "JMD":  {"expected": ["building names", "house numbers"],
             "patterns": [r"[一-鿿]", r"^\d+号"]},
    "GXYZ": {"expected": ["pipeline IDs"],
             "patterns": [r"^[A-Za-z]+\d+", r"管", r"线"]},
    "SXSS": {"expected": ["waterway names"],
             "patterns": [r"河", r"溪", r"沟", r"渠", r"水"]},
}


def _sanitize_str(s):
    """Remove/replace lone surrogates and other JSON-unsafe characters."""
    if not isinstance(s, str):
        return s
    return s.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")


def _sanitize_entity(ent):
    """Recursively sanitize string values in an entity dict for JSON serialization."""
    if isinstance(ent, dict):
        return {k: _sanitize_entity(v) for k, v in ent.items()}
    if isinstance(ent, list):
        return [_sanitize_entity(v) for v in ent]
    if isinstance(ent, str):
        return _sanitize_str(ent)
    return ent


def _write_jsonl(filepath, entities):
    """Write entity list as JSONL, sanitizing strings for JSON compatibility."""
    with open(filepath, "w", encoding="utf-8") as f:
        for ent in entities:
            f.write(json.dumps(_sanitize_entity(ent), ensure_ascii=False) + "\n")


def load_json_config(path, default=None):
    """Load a JSON config file, returning default if missing or unreadable."""
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load config %s: %s — using defaults", path, e)
    return default if default is not None else {}


# ═══════════════════════════════════════════════════════════════════════════════
# GEOMETRY UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def centroid_of_geometry(geom):
    """Compute centroid from geometry dict. Returns (x, y) or (0, 0)."""
    gtype = geom.get("type", "")
    try:
        if gtype == "LINE":
            coords = geom["coords"]
            return ((coords[0][0] + coords[1][0]) / 2, (coords[0][1] + coords[1][1]) / 2)
        elif gtype in ("LWPOLYLINE", "ARC_APPROX"):
            coords = geom["coords"]
            if not coords:
                return (0, 0)
            sx = sum(c[0] for c in coords) / len(coords)
            sy = sum(c[1] for c in coords) / len(coords)
            return (sx, sy)
        elif gtype == "CIRCLE":
            c = geom["center"]
            return (c[0], c[1])
        elif gtype in ("POINT",):
            c = geom["coords"][0]
            return (c[0], c[1])
    except (KeyError, IndexError, TypeError, ZeroDivisionError):
        pass
    return (0, 0)


def euclidean_distance(p1, p2):
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def hausdorff_distance(coords_a, coords_b):
    """Compute Hausdorff distance between two coordinate lists."""
    def _point_set_dist(a, b):
        return max(min(euclidean_distance(pa, pb) for pb in b) for pa in a)
    return max(_point_set_dist(coords_a, coords_b), _point_set_dist(coords_b, coords_a))


def polygon_area(coords):
    """Shoelace formula for polygon area (coords must be closed or not)."""
    n = len(coords)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += coords[i][0] * coords[j][1]
        area -= coords[j][0] * coords[i][1]
    return abs(area) / 2.0


def polygon_aspect_ratio(coords):
    """Compute aspect ratio (max extent / min extent) of a polygon's bounding box."""
    if len(coords) < 3:
        return 1.0
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    if dx < 1e-12 and dy < 1e-12:
        return 1.0
    if dx < 1e-12:
        return float("inf")
    if dy < 1e-12:
        return float("inf")
    return max(dx, dy) / max(min(dx, dy), 1e-12)


# ═══════════════════════════════════════════════════════════════════════════════
# SIMPLE DBSCAN FALLBACK (connected components in eps-neighbourhood)
# ═══════════════════════════════════════════════════════════════════════════════

def simple_dbscan(points, eps, min_samples):
    """
    Simple distance-based clustering fallback.
    Returns (labels, n_clusters) where labels[i] is cluster ID or -1 for noise.
    """
    n = len(points)
    if n == 0:
        return [], 0
    visited = [False] * n
    labels = [-1] * n
    cluster_id = 0

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        # Find initial neighbours
        neighbours = []
        xi, yi = points[i]
        for j in range(n):
            if i == j:
                continue
            dx = xi - points[j][0]
            dy = yi - points[j][1]
            if dx * dx + dy * dy <= eps * eps:
                neighbours.append(j)

        if len(neighbours) < min_samples:
            labels[i] = -1
        else:
            # Expand cluster
            cluster = [i]
            idx = 0
            while idx < len(cluster):
                k = cluster[idx]
                idx += 1
                if visited[k]:
                    continue
                visited[k] = True
                xk, yk = points[k]
                sub = []
                for j in range(n):
                    if j == k:
                        continue
                    dx = xk - points[j][0]
                    dy = yk - points[j][1]
                    if dx * dx + dy * dy <= eps * eps:
                        sub.append(j)
                if len(sub) >= min_samples:
                    for s in sub:
                        if s not in cluster:
                            cluster.append(s)
            for cidx in cluster:
                labels[cidx] = cluster_id
            cluster_id += 1

    return labels, cluster_id


# ═══════════════════════════════════════════════════════════════════════════════
# WKT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def coords_to_wkt_linestring(coords):
    """Build LINESTRING WKT from coordinate list (EPSG:3857 coords)."""
    pts = ", ".join(f"{x:.6f} {y:.6f}" for x, y in coords)
    return f"LINESTRING({pts})"


def coords_to_wkt_polygon(coords):
    """Build POLYGON WKT from coordinate list (closed ring expected)."""
    pts = ", ".join(f"{x:.6f} {y:.6f}" for x, y in coords)
    return f"POLYGON(({pts}))"


def coords_to_wkt_point(x, y):
    """Build POINT WKT."""
    return f"POINT({x:.6f} {y:.6f})"


# ═══════════════════════════════════════════════════════════════════════════════
# COORDINATE TRANSFORMATION  (DWG → UTM 48N → EPSG:3857)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_transformer():
    return Transformer.from_crs("EPSG:32648", "EPSG:3857")


def dwg_to_3857(x, y, regime, ox_a=None, oy_a=None, ox_b=None, oy_b=None,
                 transformer=None):
    """Convert single DWG coordinate pair to EPSG:3857."""
    ox_a = ox_a if ox_a is not None else OX_A
    oy_a = oy_a if oy_a is not None else OY_A
    ox_b = ox_b if ox_b is not None else OX_B
    oy_b = oy_b if oy_b is not None else OY_B
    if transformer is None:
        transformer = _make_transformer()
    if regime == "A":
        ex, ey = x + ox_a, y + oy_a
    else:
        ex, ey = x + ox_b, y + oy_b
    mx, my = transformer.transform(ex, ey)
    return mx, my


def test_regime_hypothesis(centroid_x, centroid_y, regime,
                           ox_a=None, oy_a=None, ox_b=None, oy_b=None,
                           bbox=None):
    """
    Test whether applying the regime offset puts coordinates within
    the known Dongxi Town UTM 48N bounding box (no pyproj needed).
    """
    ox_a = ox_a if ox_a is not None else OX_A
    oy_a = oy_a if oy_a is not None else OY_A
    ox_b = ox_b if ox_b is not None else OX_B
    oy_b = oy_b if oy_b is not None else OY_B
    bbox = bbox if bbox is not None else UTM48N_BBOX

    if regime == "A":
        ux, uy = centroid_x + ox_a, centroid_y + oy_a
    else:
        ux, uy = centroid_x + ox_b, centroid_y + oy_b

    return (bbox["x_min"] <= ux <= bbox["x_max"] and
            bbox["y_min"] <= uy <= bbox["y_max"])


# ═══════════════════════════════════════════════════════════════════════════════
# ADAPTIVE TILE SUBDIVISION
# ═══════════════════════════════════════════════════════════════════════════════

def _subdivide_oversized(tile_entities, xmin, xmax, ymin, ymax, dx, dy, n_rows, n_cols, max_per_tile):
    """Recursively split tiles exceeding max_per_tile into 2×2 sub-tiles."""
    max_iters = 10  # safety cap
    for _ in range(max_iters):
        # Build current tile list
        oversized = []
        for tile_id, elist in list(tile_entities.items()):
            if len(elist) > max_per_tile:
                oversized.append(tile_id)
        if not oversized:
            break

        for tile_id in oversized:
            elist = tile_entities.pop(tile_id)
            if len(elist) <= max_per_tile:
                tile_entities[tile_id] = elist
                continue
            # Compute this tile's bounding box from its entities
            txs = [e["centroid_x"] for e in elist]
            tys = [e["centroid_y"] for e in elist]
            txmin, txmax = min(txs), max(txs)
            tymin, tymax = min(tys), max(tys)
            sub_dx = (txmax - txmin) / 2 if txmax > txmin else 1.0
            sub_dy = (tymax - tymin) / 2 if tymax > tymin else 1.0
            for sr in range(2):
                for sc in range(2):
                    sub_id = f"{tile_id}_{sr}{sc}"
                    sub_sxmin = txmin + sc * sub_dx
                    sub_sxmax = sub_sxmin + sub_dx
                    sub_symin = tymin + sr * sub_dy
                    sub_symax = sub_symin + sub_dy
                    sub_entities = []
                    for e in elist:
                        cx, cy = e["centroid_x"], e["centroid_y"]
                        in_x = (sub_sxmin <= cx <= sub_sxmax) if sc == 1 else (sub_sxmin <= cx < sub_sxmax)
                        in_y = (sub_symin <= cy <= sub_symax) if sr == 1 else (sub_symin <= cy < sub_symax)
                        if in_x and in_y:
                            sub_entities.append(e)
                    seen = set()
                    sub_entities = [e for e in sub_entities if e["entity_id"] not in seen and not seen.add(e["entity_id"])]
                    tile_entities[sub_id] = sub_entities
                    for e in sub_entities:
                        e["tile_id"] = sub_id

    # Rebuild stats
    tile_stats = defaultdict(lambda: {"regime_A": 0, "regime_B": 0, "regime_UNKNOWN": 0})
    for tile_id, elist in tile_entities.items():
        for e in elist:
            tile_stats[tile_id][f"regime_{e['regime_hint']}"] += 1
    return tile_entities, tile_stats


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 1: INGESTION & TILING
# ═══════════════════════════════════════════════════════════════════════════════

def stage1_ingestion(dwg_path, temp_dir, tile_size=3000, no_qgis=False):
    """
    Parse a DWG file, extract valid entities, and perform spatial tile
    decomposition.  Writes tile manifest JSON and per-tile JSONL files.

    Returns (manifest, tile_entities_map) where:
      - manifest is a dict with file stats, tile metadata
      - tile_entities_map is {tile_id: [entity_dict, ...]} for downstream use
    """
    stage_log(1, "Opening DWG: %s", dwg_path)
    data = Dwg_Data()
    data.object = new_Dwg_Object_Array(500000)
    err = dwg_read_file(dwg_path, data)
    stage_log(1, "LibreDWG error code: %s, total objects: %d", err, data.num_objects)

    VALID_TYPES = {
        DWG_TYPE_LINE, DWG_TYPE_LWPOLYLINE, DWG_TYPE_CIRCLE, DWG_TYPE_ARC,
        DWG_TYPE_TEXT, DWG_TYPE_MTEXT, DWG_TYPE_INSERT, DWG_TYPE_POINT,
    }

    entities = []
    filtered_paperspace = 0
    skipped_unknown = 0

    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue
        if obj.supertype != DWG_SUPERTYPE_ENTITY:
            continue

        dwg_type = obj.type
        if dwg_type not in VALID_TYPES:
            skipped_unknown += 1
            continue

        entity = obj.tio.entity
        entity_ptr = int(entity.this)
        layer = _layer_name(entity_ptr)
        text_content = ""

        # --- Extract raw geometry ---
        geom = None
        centroid_x, centroid_y = 0.0, 0.0
        try:
            if dwg_type == DWG_TYPE_LINE:
                li = entity.tio.LINE
                geom = {"type": "LINE", "coords": [[li.start.x, li.start.y],
                                                    [li.end.x, li.end.y]]}
                centroid_x = (li.start.x + li.end.x) / 2
                centroid_y = (li.start.y + li.end.y) / 2

            elif dwg_type == DWG_TYPE_LWPOLYLINE:
                pts = _lwpoline_points(entity)
                if len(pts) < 2:
                    continue
                is_closed = bool(entity.tio.LWPOLYLINE.flag & 1)
                coord_list = [[px, py] for px, py in pts]
                if is_closed and len(coord_list) >= 3:
                    coord_list.append(coord_list[0][:])
                geom = {"type": "LWPOLYLINE", "coords": coord_list, "closed": is_closed}
                centroid_x = sum(p[0] for p in pts) / len(pts)
                centroid_y = sum(p[1] for p in pts) / len(pts)

            elif dwg_type == DWG_TYPE_CIRCLE:
                c = entity.tio.CIRCLE
                geom = {"type": "CIRCLE", "center": [c.center.x, c.center.y],
                        "radius": c.radius}
                centroid_x = c.center.x
                centroid_y = c.center.y

            elif dwg_type == DWG_TYPE_ARC:
                ar = entity.tio.ARC
                geom = {"type": "ARC", "center": [ar.center.x, ar.center.y],
                        "radius": ar.radius,
                        "start_angle": ar.start_angle, "end_angle": ar.end_angle}
                centroid_x = ar.center.x
                centroid_y = ar.center.y

            elif dwg_type == DWG_TYPE_TEXT:
                t = entity.tio.TEXT
                geom = {"type": "POINT", "coords": [[t.ins_pt.x, t.ins_pt.y]]}
                centroid_x = t.ins_pt.x
                centroid_y = t.ins_pt.y
                try:
                    text_content = t.text_value or ""
                except Exception:
                    text_content = ""

            elif dwg_type == DWG_TYPE_MTEXT:
                mt = entity.tio.MTEXT
                geom = {"type": "POINT", "coords": [[mt.ins_pt.x, mt.ins_pt.y]]}
                centroid_x = mt.ins_pt.x
                centroid_y = mt.ins_pt.y
                try:
                    text_content = mt.text or ""
                except Exception:
                    text_content = ""

            elif dwg_type == DWG_TYPE_INSERT:
                ins = entity.tio.INSERT
                geom = {"type": "POINT", "coords": [[ins.ins_pt.x, ins.ins_pt.y]]}
                centroid_x = ins.ins_pt.x
                centroid_y = ins.ins_pt.y

            elif dwg_type == DWG_TYPE_POINT:
                p = entity.tio.POINT
                geom = {"type": "POINT", "coords": [[p.x, p.y]]}
                centroid_x = p.x
                centroid_y = p.y

        except Exception:
            continue

        # Filter paper-space artifacts
        if centroid_y < PAPERSPACE_Y_MIN:
            filtered_paperspace += 1
            continue
        if abs(centroid_x) > PAPERSPACE_X_ABS_MAX:
            filtered_paperspace += 1
            continue

        # Regime hint
        if centroid_y > REGIME_HINT_THRESHOLD + UNCERTAINTY_MARGIN:
            hint = "A"
        elif centroid_y < REGIME_HINT_THRESHOLD - UNCERTAINTY_MARGIN:
            hint = "B"
        else:
            hint = "UNKNOWN"

        entities.append({
            "entity_id": len(entities),
            "dwg_type": TYPE_NAMES.get(dwg_type, f"T{dwg_type}"),
            "dwg_type_id": dwg_type,
            "geometry": geom,
            "centroid_x": centroid_x,
            "centroid_y": centroid_y,
            "layer": layer,
            "text_content": text_content if isinstance(text_content, str) else str(text_content or ""),
            "regime_hint": hint,
        })

        if (i + 1) % 50000 == 0:
            stage_log(1, "... processed %d objects, %d valid entities", i + 1, len(entities))

    stage_log(1, "Total valid entities: %d, paper-space filtered: %d, skipped unknown type: %d",
              len(entities), filtered_paperspace, skipped_unknown)

    if not entities:
        stage_log(1, "WARNING: No valid entities found in DWG file.")
        manifest = {
            "file": os.path.basename(dwg_path),
            "total_entities_raw": data.num_objects,
            "entities_filtered_paperspace": filtered_paperspace,
            "entities_valid": 0,
            "tile_grid": {"rows": 0, "cols": 0},
            "tiles": [],
        }
        return manifest, {}

    # --- Spatial tiling ---
    xs = [e["centroid_x"] for e in entities]
    ys = [e["centroid_y"] for e in entities]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    stage_log(1, "Entity centroid bbox: X[%.1f, %.1f] Y[%.1f, %.1f]", xmin, xmax, ymin, ymax)

    total = len(entities)
    # Compute grid dimensions so each cell ≈ tile_size
    n_cols = max(1, int(math.ceil(math.sqrt(total / tile_size * (xmax - xmin) / max(ymax - ymin, 1)))))
    n_rows = max(1, int(math.ceil(math.sqrt(total / tile_size * (ymax - ymin) / max(xmax - xmin, 1)))))
    # Ensure at least one tile per dimension; cap to reasonable max
    n_cols = max(1, min(n_cols, 50))
    n_rows = max(1, min(n_rows, 50))

    dx = (xmax - xmin) / n_cols if n_cols > 1 else (xmax - xmin + 1)
    dy = (ymax - ymin) / n_rows if n_rows > 1 else (ymax - ymin + 1)
    stage_log(1, "Tile grid: %d rows x %d cols, target ~%d entities/tile", n_rows, n_cols, tile_size)

    # Expand bbox slightly to avoid edge misses
    pad_x = dx * 0.01
    pad_y = dy * 0.01

    tile_entities = defaultdict(list)
    tile_stats = defaultdict(lambda: {"regime_A": 0, "regime_B": 0, "regime_UNKNOWN": 0})

    for ent in entities:
        cx, cy = ent["centroid_x"], ent["centroid_y"]
        col = min(int((cx - xmin) / dx), n_cols - 1) if dx > 0 else 0
        row = min(int((cy - ymin) / dy), n_rows - 1) if dy > 0 else 0
        col = max(0, col)
        row = max(0, row)
        tile_id = f"T{row}_{col}"
        ent["tile_id"] = tile_id
        tile_entities[tile_id].append(ent)
        if ent["regime_hint"] == "A":
            tile_stats[tile_id]["regime_A"] += 1
        elif ent["regime_hint"] == "B":
            tile_stats[tile_id]["regime_B"] += 1
        else:
            tile_stats[tile_id]["regime_UNKNOWN"] += 1

    # Adaptive subdivision: split any tile exceeding max_per_tile
    # into sub-tiles using a 2D grid within that tile's bounding box
    max_per_tile = DBSCAN_MAX_ENTITIES
    tile_entities, tile_stats = _subdivide_oversized(tile_entities, xmin, xmax, ymin, ymax, dx, dy, n_rows, n_cols, max_per_tile)

    # Write tile manifest
    tiles_meta = []
    for tile_id in sorted(tile_entities.keys()):
        elist = tile_entities.get(tile_id, [])
        ts = tile_stats.get(tile_id, {"regime_A": 0, "regime_B": 0, "regime_UNKNOWN": 0})
        # Compute tile bbox from entity centroids
        if elist:
            txs = [e["centroid_x"] for e in elist]
            tys = [e["centroid_y"] for e in elist]
            t_xmin, t_xmax = min(txs) - pad_x, max(txs) + pad_x
            t_ymin, t_ymax = min(tys) - pad_y, max(tys) + pad_y
        else:
            t_xmin = t_xmax = t_ymin = t_ymax = 0.0
        tiles_meta.append({
            "tile_id": tile_id,
            "entity_count": len(elist),
            "bbox": [t_xmin, t_ymin, t_xmax, t_ymax],
            "regime_A_count": ts["regime_A"],
            "regime_B_count": ts["regime_B"],
            "regime_UNKNOWN_count": ts["regime_UNKNOWN"],
        })

    manifest = {
        "file": os.path.basename(dwg_path),
        "total_entities_raw": data.num_objects,
        "entities_filtered_paperspace": filtered_paperspace,
        "entities_valid": total,
        "tile_grid": {"rows": n_rows, "cols": n_cols},
        "tiles": tiles_meta,
    }
    manifest_path = os.path.join(temp_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    stage_log(1, "Manifest written: %s", manifest_path)

    # Write per-tile JSONL files
    tile_files = {}
    for tile_id, elist in tile_entities.items():
        jl_path = os.path.join(temp_dir, f"tile_{tile_id}.jsonl")
        with open(jl_path, "w", encoding="utf-8") as f:
            for ent in elist:
                f.write(json.dumps(_sanitize_entity(ent), ensure_ascii=False) + "\n")
        tile_files[tile_id] = jl_path

    stage_log(1, "%d tiles written to %s", len(tile_files), temp_dir)
    return manifest, tile_files


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 2: CRS REGIME CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def stage2_regime(input_jsonl, output_jsonl, report_path,
                  ox_a=None, oy_a=None, ox_b=None, oy_b=None,
                  bbox=None, dbscan_eps=DBSCAN_EPS,
                  dbscan_min_samples=DBSCAN_MIN_SAMPLES,
                  uncertainty_threshold=UNCERTAINTY_THRESHOLD_FRACTION):
    """
    Load entity JSONL, cluster centroids via DBSCAN, vote regime per cluster,
    test affine hypothesis, assign regime_final and regime_confidence.

    Returns (n_entities, regime_report dict).
    """
    ox_a = ox_a if ox_a is not None else OX_A
    oy_a = oy_a if oy_a is not None else OY_A
    ox_b = ox_b if ox_b is not None else OX_B
    oy_b = oy_b if oy_b is not None else OY_B
    bbox = bbox if bbox is not None else UTM48N_BBOX

    entities = []
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entities.append(json.loads(line))

    n = len(entities)
    if n == 0:
        report = {"tile_id": os.path.basename(input_jsonl), "entities": 0,
                  "regime_A": 0, "regime_B": 0, "uncertain": 0,
                  "mean_confidence": 0, "clusters": 0, "human_review": False}
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return 0, report

    # Extract centroids for clustering
    centroids = [(e["centroid_x"], e["centroid_y"]) for e in entities]

    # For large tiles, skip DBSCAN — use regime_hint directly (O(n²) fallback too slow)
    if n > DBSCAN_MAX_ENTITIES:
        labels = [-1] * n  # all noise: falls through to regime_hint path below
        n_clusters = 0
        cluster_votes = {}
        cluster_hypo = {}
    elif HAS_SKLEARN:
        import numpy as np
        arr = np.array(centroids, dtype=float)
        db = SklearnDBSCAN(eps=dbscan_eps, min_samples=dbscan_min_samples)
        labels = db.labels_
        n_clusters = len(set(l for l in labels if l >= 0))
    else:
        labels, n_clusters = simple_dbscan(centroids, dbscan_eps, dbscan_min_samples)

    # Build cluster info
    cluster_entities = defaultdict(list)
    for idx, lbl in enumerate(labels):
        cluster_entities[lbl].append(idx)

    cluster_votes = {}
    cluster_hypo = {}
    for cid, member_indices in cluster_entities.items():
        if cid == -1:
            continue  # noise
        member_ys = [entities[i]["centroid_y"] for i in member_indices]
        median_y = sorted(member_ys)[len(member_ys) // 2]
        vote = "A" if median_y > REGIME_HINT_THRESHOLD else "B"
        vote_strength = sum(1 for y in member_ys
                            if (y > REGIME_HINT_THRESHOLD) == (vote == "A")) / len(member_ys)
        cluster_votes[cid] = {"vote": vote, "strength": vote_strength, "size": len(member_indices)}

        # Affine hypothesis test: use median entity centroid
        median_idx = member_indices[len(member_indices) // 2]
        cx = entities[median_idx]["centroid_x"]
        cy = entities[median_idx]["centroid_y"]
        hypo_a = test_regime_hypothesis(cx, cy, "A", ox_a, oy_a, ox_b, oy_b, bbox)
        hypo_b = test_regime_hypothesis(cx, cy, "B", ox_a, oy_a, ox_b, oy_b, bbox)
        cluster_hypo[cid] = {"A": hypo_a, "B": hypo_b}

    # Assign regime_final and confidence per entity
    uncertain_count = 0
    for idx, ent in enumerate(entities):
        lbl = labels[idx]
        if lbl == -1 or lbl not in cluster_votes:
            # Noise point: fall back to simple Y-threshold hint rather than UNCERTAIN
            hint = ent.get("regime_hint", "B")
            if hint in ("A", "B"):
                ent["regime_final"] = hint
                ent["regime_confidence"] = 0.5
            else:
                ent["regime_final"] = "UNCERTAIN"
                ent["regime_confidence"] = 0.3
                uncertain_count += 1
            ent["cluster_id"] = -1
            ent["hypothesis_A_pass"] = False
            ent["hypothesis_B_pass"] = False
        else:
            vote_info = cluster_votes[lbl]
            hypo = cluster_hypo[lbl]
            ent["cluster_id"] = int(lbl)
            ent["hypothesis_A_pass"] = hypo["A"]
            ent["hypothesis_B_pass"] = hypo["B"]

            if vote_info["strength"] > 0.95 and hypo.get(vote_info["vote"], False):
                ent["regime_final"] = vote_info["vote"]
                ent["regime_confidence"] = 1.0
            elif vote_info["strength"] > 0.8:
                ent["regime_final"] = vote_info["vote"]
                ent["regime_confidence"] = 0.7
            elif vote_info["strength"] > 0.5:
                ent["regime_final"] = vote_info["vote"]
                ent["regime_confidence"] = 0.5
            else:
                ent["regime_final"] = "UNCERTAIN"
                ent["regime_confidence"] = 0.3
                uncertain_count += 1

    # Write augmented JSONL
    with open(output_jsonl, "w", encoding="utf-8") as f:
        for ent in entities:
            f.write(json.dumps(ent, ensure_ascii=False) + "\n")

    # Regime report
    regime_a = sum(1 for e in entities if e["regime_final"] == "A")
    regime_b = sum(1 for e in entities if e["regime_final"] == "B")
    mean_conf = sum(e["regime_confidence"] for e in entities) / n if n > 0 else 0
    human_review_flag = (uncertain_count / n) > uncertainty_threshold if n > 0 else False

    report = {
        "tile_id": os.path.basename(input_jsonl).replace("tile_", "").replace(".jsonl", ""),
        "entities": n,
        "regime_A": regime_a,
        "regime_B": regime_b,
        "uncertain": uncertain_count,
        "mean_confidence": round(mean_conf, 4),
        "clusters": n_clusters,
        "noise_points": sum(1 for l in labels if l == -1),
        "human_review": human_review_flag,
        "cluster_details": {
            str(k): {"vote": v["vote"], "strength": round(v["strength"], 3), "size": v["size"],
                     "hypo_A": cluster_hypo.get(k, {}).get("A", False),
                     "hypo_B": cluster_hypo.get(k, {}).get("B", False)}
            for k, v in cluster_votes.items()
        },
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return n, report


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 3: GEOMETRY NORMALIZER
# ═══════════════════════════════════════════════════════════════════════════════

def stage3_normalize(input_jsonl, output_jsonl, report_path,
                     ox_a=None, oy_a=None, ox_b=None, oy_b=None,
                     gcp_data=None):
    """
    Apply coordinate transform (DWG → offset → UTM 48N → EPSG:3857) to all
    entities with known regime.  Reconstructs WKT in EPSG:3857.

    Returns (n_transformed, n_skipped, report dict).
    """
    ox_a = ox_a if ox_a is not None else OX_A
    oy_a = oy_a if oy_a is not None else OY_A
    ox_b = ox_b if ox_b is not None else OX_B
    oy_b = oy_b if oy_b is not None else OY_B

    transformer = _make_transformer()

    entities = []
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entities.append(json.loads(line))

    n_skipped = 0
    n_transformed = 0
    precision_status = "PASS"

    # GCP validation
    gcp_residuals = []
    if gcp_data:
        for gcp in gcp_data:
            reg = gcp.get("regime", "B")
            tx, ty = dwg_to_3857(gcp["dwg_x"], gcp["dwg_y"], reg,
                                 ox_a, oy_a, ox_b, oy_b, transformer)
            if "epsg3857_x" in gcp and "epsg3857_y" in gcp:
                resid = math.sqrt((tx - gcp["epsg3857_x"]) ** 2 +
                                  (ty - gcp["epsg3857_y"]) ** 2)
                gcp_residuals.append(resid)

        if gcp_residuals:
            max_r = max(gcp_residuals)
            mean_r = sum(gcp_residuals) / len(gcp_residuals)
            if max_r > 0.0015:
                precision_status = "FAIL"
            elif max_r > 0.0012:
                precision_status = "WARN"
    else:
        precision_status = "PRECISION_COARSE"
        max_r = None
        mean_r = None

    for ent in entities:
        regime = ent.get("regime_final", "UNCERTAIN")
        if regime == "UNCERTAIN":
            ent["geometry_wkt_epsg3857"] = ""
            ent["transformation_applied"] = "NONE"
            ent["precision_status"] = precision_status
            ent["scale_factor_applied"] = None
            n_skipped += 1
            continue

        try:
            geom = ent["geometry"]
            gtype = geom.get("type", "")

            if gtype == "LINE":
                coords = geom["coords"]
                x1, y1 = dwg_to_3857(coords[0][0], coords[0][1], regime,
                                     ox_a, oy_a, ox_b, oy_b, transformer)
                x2, y2 = dwg_to_3857(coords[1][0], coords[1][1], regime,
                                     ox_a, oy_a, ox_b, oy_b, transformer)
                new_coords = [[x1, y1], [x2, y2]]
                wkt = coords_to_wkt_linestring(new_coords)
                ent["geometry"] = {"type": "LineString", "coords_3857": new_coords}
                ent["geometry_wkt_epsg3857"] = wkt
                ent["scale_factor_applied"] = None

            elif gtype == "LWPOLYLINE":
                coords = geom["coords"]
                new_coords = []
                for px, py in coords:
                    mx, my = dwg_to_3857(px, py, regime, ox_a, oy_a, ox_b, oy_b, transformer)
                    new_coords.append([mx, my])
                wkt = coords_to_wkt_linestring(new_coords)
                ent["geometry"] = {"type": "LineString", "coords_3857": new_coords,
                                   "closed": geom.get("closed", False)}
                ent["geometry_wkt_epsg3857"] = wkt
                ent["scale_factor_applied"] = None

            elif gtype == "CIRCLE":
                cx_raw, cy_raw = geom["center"]
                radius_raw = geom["radius"]
                cx_t, cy_t = dwg_to_3857(cx_raw, cy_raw, regime,
                                         ox_a, oy_a, ox_b, oy_b, transformer)
                # Compute transformed radius: transform (cx+radius, cy)
                px_t, py_t = dwg_to_3857(cx_raw + radius_raw, cy_raw, regime,
                                         ox_a, oy_a, ox_b, oy_b, transformer)
                radius_t = math.sqrt((px_t - cx_t) ** 2 + (py_t - cy_t) ** 2)
                scale_factor = radius_t / max(radius_raw, 1e-12)
                n_pts = 72
                circle_coords = []
                for j in range(n_pts + 1):
                    angle = 2 * math.pi * j / n_pts
                    dx_l = radius_raw * math.cos(angle)
                    dy_l = radius_raw * math.sin(angle)
                    mx, my = dwg_to_3857(cx_raw + dx_l, cy_raw + dy_l, regime,
                                         ox_a, oy_a, ox_b, oy_b, transformer)
                    circle_coords.append([mx, my])
                wkt = coords_to_wkt_polygon(circle_coords)
                ent["geometry"] = {"type": "Polygon", "coords_3857": circle_coords,
                                   "center_3857": [cx_t, cy_t], "radius_3857": radius_t}
                ent["geometry_wkt_epsg3857"] = wkt
                ent["scale_factor_applied"] = scale_factor

            elif gtype == "ARC":
                cx_raw, cy_raw = geom["center"]
                radius_raw = geom["radius"]
                sa = geom["start_angle"]
                ea = geom["end_angle"]
                if ea < sa:
                    ea += 2 * math.pi
                n_pts = max(10, int(36 * (ea - sa) / (2 * math.pi)))
                arc_coords = []
                for j in range(n_pts + 1):
                    angle = sa + j * (ea - sa) / n_pts
                    dx_l = radius_raw * math.cos(angle)
                    dy_l = radius_raw * math.sin(angle)
                    mx, my = dwg_to_3857(cx_raw + dx_l, cy_raw + dy_l, regime,
                                         ox_a, oy_a, ox_b, oy_b, transformer)
                    arc_coords.append([mx, my])
                wkt = coords_to_wkt_linestring(arc_coords)
                ent["geometry"] = {"type": "LineString", "coords_3857": arc_coords}
                ent["geometry_wkt_epsg3857"] = wkt
                ent["scale_factor_applied"] = None

            elif gtype == "POINT":
                px, py = geom["coords"][0]
                mx, my = dwg_to_3857(px, py, regime, ox_a, oy_a, ox_b, oy_b, transformer)
                wkt = coords_to_wkt_point(mx, my)
                ent["geometry"] = {"type": "Point", "coords_3857": [[mx, my]]}
                ent["geometry_wkt_epsg3857"] = wkt
                ent["scale_factor_applied"] = None

            else:
                ent["geometry_wkt_epsg3857"] = ""
                ent["transformation_applied"] = "NONE"
                ent["precision_status"] = precision_status
                ent["scale_factor_applied"] = None
                n_skipped += 1
                continue

            ent["transformation_applied"] = regime
            ent["precision_status"] = precision_status
            n_transformed += 1

        except Exception:
            ent["geometry_wkt_epsg3857"] = ""
            ent["transformation_applied"] = "NONE"
            ent["precision_status"] = "FAIL"
            ent["scale_factor_applied"] = None
            n_skipped += 1

    with open(output_jsonl, "w", encoding="utf-8") as f:
        for ent in entities:
            f.write(json.dumps(ent, ensure_ascii=False) + "\n")

    report = {
        "tile_id": os.path.basename(input_jsonl).replace("tile_", "").replace("_s2.jsonl", "").replace(".jsonl", ""),
        "entities_transformed": n_transformed,
        "entities_skipped_uncertain": n_skipped,
        "precision_status": precision_status,
        "gcp_validation": {
            "gcp_count": len(gcp_residuals) if gcp_residuals else 0,
            "max_residual_meters": max(gcp_residuals) if gcp_residuals else None,
            "mean_residual_meters": sum(gcp_residuals) / len(gcp_residuals) if gcp_residuals else None,
        },
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return n_transformed, n_skipped, report


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 4: TOPOLOGY SURGEON
# ═══════════════════════════════════════════════════════════════════════════════

def stage4_topology(input_jsonl, output_jsonl, report_path,
                    snap_tolerance=SNAP_TOLERANCE,
                    hausdorff_threshold=HAUSDORFF_THRESHOLD,
                    sliver_area=SLIVER_AREA_THRESHOLD,
                    sliver_aspect=SLIVER_ASPECT_RATIO,
                    topology_rules=None):
    """
    Topology repair operations:
      1. Node snapping: snap line endpoints within snap_tolerance
      2. Duplicate arc removal: remove near-identical geometries on same layer
      3. Polygon closure: close LWPOLYLINE where start/end < snap_tolerance
      4. Sliver elimination: remove tiny sliver polygons

    Returns (entities_in, entities_out, repairs, manual_review, report dict).
    """
    topology_rules = topology_rules or {}
    entities = []
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entities.append(json.loads(line))

    n_in = len(entities)
    if n_in == 0:
        report = {"entities_in": 0, "entities_out": 0, "repairs": {},
                  "manual_review": 0, "automation_rate": 1.0}
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return 0, 0, {}, 0, report

    repair_log = []
    manual_review_count = 0

    # Separate line/polygon entities (with WKT) from point-only entities
    line_entities = []  # (index, coords_3857)
    point_entities = []  # (index)
    for idx, ent in enumerate(entities):
        if not ent.get("geometry_wkt_epsg3857"):
            point_entities.append(idx)
            continue
        geom = ent.get("geometry", {})
        coords = geom.get("coords_3857", [])
        if len(coords) >= 2:
            line_entities.append((idx, coords))

    # --- Node Snapping ---
    # Build endpoint set: (ex, ey) → [(entity_idx, endpoint_idx)]
    endpoints = defaultdict(list)
    for eidx, coords in line_entities:
        if len(coords) >= 2:
            p0 = (coords[0][0], coords[0][1])
            pn = (coords[-1][0], coords[-1][1])
            endpoints[p0].append((eidx, 0))
            endpoints[pn].append((eidx, len(coords) - 1))

    snap_count = 0
    ep_list = list(endpoints.items())
    for i, (pos, refs) in enumerate(ep_list):
        for j in range(i + 1, len(ep_list)):
            pos2, refs2 = ep_list[j]
            dist = euclidean_distance(pos, pos2)
            layer_rule = topology_rules.get(entities[refs[0][0]].get("layer", ""), {})
            tol = layer_rule.get("snap_tolerance", snap_tolerance)
            if dist > 0 and dist <= tol:
                # Snap pos2 to pos (move first endpoint's position)
                for eidx, ep_idx in refs2:
                    entities[eidx]["geometry"]["coords_3857"][ep_idx] = [pos[0], pos[1]]
                    # Rebuild WKT
                    new_coords = entities[eidx]["geometry"]["coords_3857"]
                    entities[eidx]["geometry_wkt_epsg3857"] = coords_to_wkt_linestring(new_coords)
                snap_count += 1
                repair_log.append({
                    "repair_type": "SNAP",
                    "entity_ids": [refs[0][0], refs2[0][0]],
                    "position_from": list(pos2),
                    "position_to": list(pos),
                    "delta_meters": round(dist, 6),
                })

    # --- Duplicate Arc Removal ---
    dedup_removed = set()
    for i in range(len(line_entities)):
        if line_entities[i][0] in dedup_removed:
            continue
        coords_i = line_entities[i][1]
        layer_i = entities[line_entities[i][0]].get("layer", "")
        for j in range(i + 1, len(line_entities)):
            if line_entities[j][0] in dedup_removed:
                continue
            layer_j = entities[line_entities[j][0]].get("layer", "")
            if layer_i != layer_j:
                continue
            coords_j = line_entities[j][1]
            hd = hausdorff_distance(coords_i, coords_j)
            if hd < hausdorff_threshold:
                # Keep the one with more attributes (prefer text-annotated)
                ent_i = entities[line_entities[i][0]]
                ent_j = entities[line_entities[j][0]]
                has_text_i = bool(ent_i.get("text_content") or ent_i.get("annotation_text"))
                has_text_j = bool(ent_j.get("text_content") or ent_j.get("annotation_text"))
                if has_text_j and not has_text_i:
                    dedup_removed.add(line_entities[i][0])
                else:
                    dedup_removed.add(line_entities[j][0])
                repair_log.append({
                    "repair_type": "DEDUP",
                    "entity_ids": [line_entities[i][0], line_entities[j][0]],
                    "hausdorff_distance": round(hd, 6),
                    "kept": line_entities[j][0] if (has_text_j and not has_text_i) else line_entities[i][0],
                })

    # --- Polygon Closure ---
    closure_count = 0
    for idx, ent in enumerate(entities):
        g = ent.get("geometry", {})
        if g.get("closed"):
            continue  # already closed
        coords = g.get("coords_3857", [])
        if len(coords) < 3:
            continue
        layer = ent.get("layer", "")
        rule = topology_rules.get(layer, {})
        if not rule.get("must_be_closed_polygon"):
            continue
        dist_se = euclidean_distance(coords[0], coords[-1])
        tol = rule.get("snap_tolerance", snap_tolerance)
        if dist_se > 0 and dist_se <= tol:
            coords.append(coords[0][:])
            ent["geometry"]["coords_3857"] = coords
            ent["geometry"]["closed"] = True
            ent["geometry_wkt_epsg3857"] = coords_to_wkt_linestring(coords)
            closure_count += 1
            repair_log.append({
                "repair_type": "CLOSE",
                "entity_ids": [idx],
                "start_end_distance": round(dist_se, 6),
            })

    # --- Sliver Elimination ---
    sliver_removed = set()
    for idx, ent in enumerate(entities):
        g = ent.get("geometry", {})
        if g.get("type") != "Polygon":
            continue
        coords = g.get("coords_3857", [])
        if len(coords) < 4:
            continue
        area = polygon_area(coords)
        aspect = polygon_aspect_ratio(coords)
        layer = ent.get("layer", "")
        rule = topology_rules.get(layer, {})
        area_threshold = rule.get("sliver_area_threshold", sliver_area)
        if area < area_threshold and aspect > sliver_aspect:
            sliver_removed.add(idx)
            repair_log.append({
                "repair_type": "SLIVER",
                "entity_ids": [idx],
                "area_sqm": round(area, 6),
                "aspect_ratio": round(aspect, 1),
            })

    # --- Remove entities marked for deletion ---
    all_removed = dedup_removed | sliver_removed
    out_entities = [e for i, e in enumerate(entities) if i not in all_removed]

    with open(output_jsonl, "w", encoding="utf-8") as f:
        for ent in out_entities:
            ent["topology_repaired"] = ent.get("entity_id", -1) in {
                r.get("entity_ids", [None])[0] for r in repair_log
                if r["repair_type"] == "SNAP"
            }
            f.write(json.dumps(ent, ensure_ascii=False) + "\n")

    n_out = len(out_entities)
    repairs_summary = {
        "snap": snap_count,
        "dedup": len(dedup_removed),
        "closure": closure_count,
        "sliver": len(sliver_removed),
    }
    ar = (n_in - manual_review_count) / n_in if n_in > 0 else 1.0

    report = {
        "entities_in": n_in,
        "entities_out": n_out,
        "repairs": repairs_summary,
        "repair_log": repair_log,
        "manual_review": manual_review_count,
        "automation_rate": round(ar, 4),
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return n_in, n_out, repairs_summary, manual_review_count, report


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 5: SEMANTIC WEAVER
# ═══════════════════════════════════════════════════════════════════════════════

def _semantic_match_score(text, layer, layer_vocab):
    """Heuristic semantic score: check if text matches expected patterns for layer."""
    if not text or not layer:
        return 0.0
    vocab = layer_vocab.get(layer, {})
    patterns = vocab.get("patterns", [])
    if not patterns:
        return 0.3  # neutral score when no vocab
    matches = 0
    for pat in patterns:
        try:
            if re.search(pat, text):
                matches += 1
        except re.error:
            continue
    if matches == 0:
        return 0.1
    return min(1.0, 0.3 + 0.7 * matches / len(patterns))


def _is_numeric_text(text):
    """Check if text is a numeric value (possibly with decimal)."""
    if not text:
        return False
    try:
        float(text.replace(",", "").replace(" ", ""))
        return True
    except ValueError:
        return False


def stage5_semantic(input_jsonl, output_jsonl, report_path,
                    linkage_threshold=LINKAGE_CONFIDENCE_THRESHOLD,
                    layer_vocab=None,
                    sigma_spatial=None):
    """
    Link TEXT/MTEXT entities to nearby geometry entities via spatial-semantic scoring.

    Scoring: w_spatial * spatial_score + w_semantic * semantic_score
    spatial_score = exp(-distance / sigma)
    """
    W_SPATIAL = 0.7
    W_SEMANTIC = 0.3

    layer_vocab = layer_vocab or DEFAULT_LAYER_VOCAB

    entities = []
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entities.append(json.loads(line))

    n = len(entities)
    if n == 0:
        report = {"text_total": 0, "linked": 0, "unlinked": 0, "linkage_rate": 0,
                  "mean_confidence": 0, "elevation_annotated": 0, "contour_annotated": 0}
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report

    # Separate text and geometry entities
    text_entities = []
    geom_entities = []
    for idx, ent in enumerate(entities):
        g = ent.get("geometry", {})
        coords = g.get("coords_3857", [])
        if not coords:
            continue
        centroid_3857 = None
        if g.get("type") == "Point":
            centroid_3857 = (coords[0][0], coords[0][1])
        elif len(coords) >= 1:
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            centroid_3857 = (sum(xs) / len(xs), sum(ys) / len(ys))

        if centroid_3857 is None:
            continue

        tt = ent.get("text_content", "")
        is_text = ent.get("dwg_type", "") in ("TEXT", "MTEXT") and bool(tt)
        if is_text:
            text_entities.append((idx, centroid_3857, tt))
        else:
            geom_entities.append((idx, centroid_3857))

    n_text = len(text_entities)
    if n_text == 0:
        # No text entities: just write through
        with open(output_jsonl, "w", encoding="utf-8") as f:
            for ent in entities:
                f.write(json.dumps(_sanitize_entity(ent), ensure_ascii=False) + "\n")
        report = {"text_total": 0, "linked": 0, "unlinked": 0, "linkage_rate": 1.0,
                  "mean_confidence": 0, "elevation_annotated": 0, "contour_annotated": 0}
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report

    # Compute adaptive sigma from geometry entity pairwise distances (sample)
    if sigma_spatial is None and len(geom_entities) >= 2:
        sample_size = min(1000, len(geom_entities))
        sample = random.sample(geom_entities, sample_size)
        dists = []
        for _ in range(min(500, sample_size * (sample_size - 1) // 2)):
            a, b = random.sample(sample, 2)
            dists.append(euclidean_distance(a[1], b[1]))
        if dists:
            sigma_spatial = sorted(dists)[len(dists) // 2] * SPATIAL_SIGMA_FACTOR
    if sigma_spatial is None or sigma_spatial <= 0:
        sigma_spatial = 100.0  # default fallback

    search_radius = sigma_spatial * TEXT_SEARCH_RADIUS_FACTOR

    linked_count = 0
    unlinked_count = 0
    link_confidences = []
    elevation_count = 0
    contour_count = 0

    for tidx, (t_centroid_x, t_centroid_y), txt in text_entities:
        candidates = []
        for gidx, (g_centroid_x, g_centroid_y) in geom_entities:
            dist = euclidean_distance((t_centroid_x, t_centroid_y),
                                      (g_centroid_x, g_centroid_y))
            if dist <= search_radius:
                candidates.append((gidx, dist))

        if not candidates:
            entities[tidx]["annotation_linked"] = False
            entities[tidx]["annotation_confidence"] = 0.0
            unlinked_count += 1
            continue

        # Score each candidate
        best_score = -1
        best_gidx = -1
        for gidx, dist in candidates:
            spatial_score = math.exp(-dist / sigma_spatial)
            g_layer = entities[gidx].get("layer", "")
            semantic_score = _semantic_match_score(txt, g_layer, layer_vocab)
            score = W_SPATIAL * spatial_score + W_SEMANTIC * semantic_score
            if score > best_score:
                best_score = score
                best_gidx = gidx

        if best_score >= linkage_threshold and best_gidx >= 0:
            # Link text to geometry
            entities[best_gidx]["annotation_text"] = txt
            entities[best_gidx]["annotation_confidence"] = round(best_score, 4)
            entities[tidx]["annotation_linked"] = True
            entities[tidx]["linked_to_entity_id"] = entities[best_gidx].get("entity_id", -1)
            entities[tidx]["annotation_confidence"] = round(best_score, 4)
            linked_count += 1
            link_confidences.append(best_score)

            # Special handling for numeric annotations
            layer = entities[best_gidx].get("layer", "")
            if _is_numeric_text(txt):
                val = float(txt.replace(",", "").replace(" ", ""))
                if layer == "GCD":
                    entities[best_gidx]["elevation_m"] = val
                    elevation_count += 1
                elif layer == "DGX":
                    entities[best_gidx]["contour_value_m"] = val
                    contour_count += 1
        else:
            entities[tidx]["annotation_linked"] = False
            entities[tidx]["annotation_confidence"] = round(best_score, 4)
            unlinked_count += 1

    with open(output_jsonl, "w", encoding="utf-8") as f:
        for ent in entities:
            f.write(json.dumps(ent, ensure_ascii=False) + "\n")

    mean_conf = sum(link_confidences) / len(link_confidences) if link_confidences else 0.0
    report = {
        "text_total": n_text,
        "linked": linked_count,
        "unlinked": unlinked_count,
        "linkage_rate": round(linked_count / n_text, 4) if n_text else 0,
        "mean_confidence": round(mean_conf, 4),
        "elevation_annotated": elevation_count,
        "contour_annotated": contour_count,
        "sigma_spatial": round(sigma_spatial, 1),
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 6: SCHEMA ALCHEMIST
# ═══════════════════════════════════════════════════════════════════════════════

def _geom_type_from_wkt(wkt):
    """Extract geometry type name from WKT string."""
    if not wkt:
        return "Geometry"
    wkt_upper = wkt.strip().upper()
    if wkt_upper.startswith("POINT"):
        return "Point"
    if wkt_upper.startswith("LINESTRING"):
        return "LineString"
    if wkt_upper.startswith("POLYGON"):
        return "Polygon"
    if wkt_upper.startswith("MULTILINESTRING"):
        return "MultiLineString"
    if wkt_upper.startswith("MULTIPOLYGON"):
        return "MultiPolygon"
    return "Geometry"


def stage6_schema(input_jsonl, output_jsonl, report_path, schema_mapping=None):
    """
    Map DWG layers to GIS feature classes, validate geometry types,
    assign FME semantic tags.

    schema_mapping: dict keyed by DWG layer name → {fc, geom, attrs}
    """
    schema_mapping = schema_mapping or DEFAULT_SCHEMA_MAPPING

    entities = []
    with open(input_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entities.append(json.loads(line))

    n = len(entities)
    fc_counts = defaultdict(int)
    geometry_mismatches = 0
    unmapped_layers = set()

    for ent in entities:
        layer = ent.get("layer", "")
        mapping = schema_mapping.get(layer)
        wkt = ent.get("geometry_wkt_epsg3857", "")
        actual_geom_type = _geom_type_from_wkt(wkt)

        if mapping:
            expected_geom = mapping["geom"]
            fc_name = mapping["fc"]
            if actual_geom_type != "Geometry" and actual_geom_type != expected_geom:
                # Geometry type mismatch
                if actual_geom_type == "Point" and bool(ent.get("text_content")):
                    # Point on LineString/Polygon layer: likely a label, keep as Point
                    fc_name = mapping["fc"] + "_labels"
                else:
                    fc_name = "fc_misc"
                    geometry_mismatches += 1
                ent["schema_confidence"] = 0.3
            else:
                ent["schema_confidence"] = 1.0
        else:
            fc_name = "fc_misc"
            ent["layer_original"] = layer
            ent["schema_confidence"] = 0.5
            if layer:
                unmapped_layers.add(layer)

        ent["fc_name"] = fc_name

        # FME semantic tag
        semantic_transform = "DIRECT"
        if ent.get("dwg_type", "") in ("TEXT", "MTEXT") and ent.get("annotation_linked"):
            semantic_transform = "ANNOTATION_MERGE"
        elif actual_geom_type == "Polygon" and ent.get("geometry", {}).get("closed"):
            semantic_transform = "RING_CLOSE"
        ent["fme_semantic_tag"] = {
            "source_type": f"CAD_{actual_geom_type.upper()}",
            "destination_type": f"GIS_{actual_geom_type.upper()}",
            "semantic_transform": semantic_transform,
            "fme_workspace_hint": "dwg2gis_v2.fmw",
        }

        fc_counts[fc_name] += 1

    with open(output_jsonl, "w", encoding="utf-8") as f:
        for ent in entities:
            f.write(json.dumps(ent, ensure_ascii=False) + "\n")

    # Compute mean schema confidence
    mean_conf = sum(e.get("schema_confidence", 0) for e in entities) / n if n else 0

    report = {
        "entities_total": n,
        "fc_counts": dict(fc_counts),
        "geometry_type_mismatches": geometry_mismatches,
        "unmapped_layers": list(unmapped_layers),
        "mean_schema_confidence": round(mean_conf, 4),
        "entities_fc_misc": fc_counts.get("fc_misc", 0),
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# TILE PIPELINE WORKER  (Stages 2-6 for a single tile; parallelizable)
# ═══════════════════════════════════════════════════════════════════════════════

def process_tile_pipeline(tile_jsonl_path, temp_dir, tile_id, stages_to_run,
                          ox_a, oy_a, ox_b, oy_b, bbox,
                          gcp_data, topology_rules, layer_vocab, schema_mapping):
    """
    Run Stages 2-6 sequentially for a single tile.
    All input is serializable (paths, dicts, lists).

    Returns dict with tile_id, status, and paths to stage outputs + reports.
    """
    result = {"tile_id": tile_id, "status": "COMPLETE", "error": None,
              "reports": {}}

    current_jsonl = tile_jsonl_path
    try:
        stages_map = {
            2: ("_s2.jsonl", "_s2_report.json", "stage2"),
            3: ("_s3.jsonl", "_s3_report.json", "stage3"),
            4: ("_s4.jsonl", "_s4_report.json", "stage4"),
            5: ("_s5.jsonl", "_s5_report.json", "stage5"),
            6: ("_s6.jsonl", "_s6_report.json", "stage6"),
        }

        for stage_num in sorted(stages_to_run):
            if stage_num not in stages_map:
                continue
            out_suffix, report_suffix, label = stages_map[stage_num]
            out_path = os.path.join(temp_dir, f"tile_{tile_id}{out_suffix}")
            rpt_path = os.path.join(temp_dir, f"tile_{tile_id}{report_suffix}")

            if stage_num == 2:
                _, report = stage2_regime(current_jsonl, out_path, rpt_path,
                                          ox_a, oy_a, ox_b, oy_b, bbox)
                result["reports"]["stage2"] = report
                if report.get("human_review"):
                    result["flags"] = result.get("flags", []) + ["HUMAN_REVIEW"]

            elif stage_num == 3:
                n_trans, n_skip, report = stage3_normalize(current_jsonl, out_path, rpt_path,
                                                           ox_a, oy_a, ox_b, oy_b, gcp_data)
                result["reports"]["stage3"] = report
                if report.get("precision_status") == "FAIL":
                    result["flags"] = result.get("flags", []) + ["PRECISION_WARN"]

            elif stage_num == 4:
                _, _, _, _, report = stage4_topology(current_jsonl, out_path, rpt_path,
                                                     topology_rules=topology_rules)
                result["reports"]["stage4"] = report

            elif stage_num == 5:
                report = stage5_semantic(current_jsonl, out_path, rpt_path,
                                         layer_vocab=layer_vocab)
                result["reports"]["stage5"] = report

            elif stage_num == 6:
                report = stage6_schema(current_jsonl, out_path, rpt_path,
                                       schema_mapping=schema_mapping)
                result["reports"]["stage6"] = report

            current_jsonl = out_path

        result["final_jsonl"] = current_jsonl

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 7: SPATIAL ASSEMBLER
# ═══════════════════════════════════════════════════════════════════════════════

def stage7_assemble(all_entities, gpkg_path, temp_dir):
    """
    Merge all processed entities across tiles, write per-feature-class layers
    to a GeoPackage (EPSG:3857) using OGR API directly.

    all_entities: list of entity dicts (merged from all tile final JSONL outputs).
    Returns (layer_count, feature_count).
    """
    from osgeo import ogr, osr

    if os.path.exists(gpkg_path):
        os.remove(gpkg_path)

    # Group by fc_name
    fc_groups = defaultdict(list)
    for ent in all_entities:
        fc = ent.get("fc_name", "fc_misc")
        wkt = ent.get("geometry_wkt_epsg3857", "")
        if not wkt:
            continue
        fc_groups[fc].append(ent)

    driver = ogr.GetDriverByName("GPKG")
    ds = driver.CreateDataSource(gpkg_path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)

    layer_count = 0
    feat_count = 0
    total_groups = len(fc_groups)

    for idx, (fc_name, feat_list) in enumerate(sorted(fc_groups.items())):
        # Sanitize name
        safe = fc_name.encode("ascii", errors="replace").decode("ascii")
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
        if not safe or safe[0].isdigit():
            safe = "L_" + safe
        safe = safe[:50]

        # Determine geometry type from first valid WKT
        geom_type = ogr.wkbUnknown
        sample_wkt = ""
        for fdata in feat_list:
            w = fdata.get("geometry_wkt_epsg3857", "")
            if w:
                sample_wkt = w
                break
        if sample_wkt:
            g = ogr.CreateGeometryFromWkt(sample_wkt)
            if g:
                geom_type = g.GetGeometryType()

        if geom_type == ogr.wkbUnknown:
            continue

        # Check for annotation/elevation/contour fields
        has_annotation = any(f.get("annotation_text") for f in feat_list)
        has_elevation = any("elevation_m" in f for f in feat_list)
        has_contour = any("contour_value_m" in f for f in feat_list)

        try:
            layer = ds.CreateLayer(safe, srs, geom_type)
            layer.CreateField(ogr.FieldDefn("dwg_type", ogr.OFTString))
            layer.CreateField(ogr.FieldDefn("layer", ogr.OFTString))
            layer.CreateField(ogr.FieldDefn("fc_name", ogr.OFTString))
            layer.CreateField(ogr.FieldDefn("schema_conf", ogr.OFTReal))
            if has_annotation:
                layer.CreateField(ogr.FieldDefn("annotation_text", ogr.OFTString))
                layer.CreateField(ogr.FieldDefn("annotation_conf", ogr.OFTReal))
            if has_elevation:
                layer.CreateField(ogr.FieldDefn("elevation_m", ogr.OFTReal))
            if has_contour:
                layer.CreateField(ogr.FieldDefn("contour_value_m", ogr.OFTReal))

            layer_defn = layer.GetLayerDefn()
            written = 0
            for fdata in feat_list:
                wkt = fdata.get("geometry_wkt_epsg3857", "")
                if not wkt:
                    continue
                geom = ogr.CreateGeometryFromWkt(wkt)
                if not geom or geom.IsEmpty():
                    continue

                feat = ogr.Feature(layer_defn)
                feat.SetGeometry(geom)
                feat.SetField("dwg_type", str(fdata.get("dwg_type", "")))
                feat.SetField("layer", str(fdata.get("layer", "")))
                feat.SetField("fc_name", str(fc_name))
                feat.SetField("schema_conf", float(fdata.get("schema_confidence", 0)))
                if has_annotation:
                    feat.SetField("annotation_text", str(fdata.get("annotation_text", "")))
                    feat.SetField("annotation_conf", float(fdata.get("annotation_confidence", 0)))
                if has_elevation:
                    feat.SetField("elevation_m", float(fdata.get("elevation_m", 0)))
                if has_contour:
                    feat.SetField("contour_value_m", float(fdata.get("contour_value_m", 0)))

                if layer.CreateFeature(feat) == 0:
                    written += 1
                feat = None

            layer.SyncToDisk()
            layer_count += 1
            feat_count += written
            stage_log(7, "[%d/%d] %s: %d features", layer_count, total_groups, safe, written)

        except Exception as e:
            stage_log(7, "[%d/%d] %s: unexpected error: %s", idx + 1, total_groups, safe, e)

    ds = None  # close

    stage_log(7, "Done: %d layers, %d features → %s", layer_count, feat_count, gpkg_path)
    return layer_count, feat_count


# ═══════════════════════════════════════════════════════════════════════════════
# STAGE 8: QUALITY SENTINEL
# ═══════════════════════════════════════════════════════════════════════════════

def stage8_quality(entities_in, entities_out, all_reports, gpkg_path, output_report_path):
    """
    Compute quality metrics Q1-Q5 and overall automation rate.
    Writes quality report JSON.
    """
    # Q1: Geometric completeness
    q1 = (len(entities_out) / len(entities_in)) if len(entities_in) > 0 else 0

    # Q2: Topological integrity from stage 4 reports
    total_repairs = 0
    total_in_4 = 0
    for rpt in all_reports:
        s4 = rpt.get("stage4", {})
        if isinstance(s4, dict):
            repairs = s4.get("repairs", {})
            if isinstance(repairs, dict):
                total_repairs += sum(repairs.values())
            total_in_4 += s4.get("entities_in", 0)
    q2_violation_rate = total_repairs / total_in_4 if total_in_4 > 0 else 0

    # Q3: Coordinate precision from stage 3 reports
    gcp_max_residuals = []
    for rpt in all_reports:
        s3 = rpt.get("stage3", {})
        if isinstance(s3, dict):
            gcp_val = s3.get("gcp_validation", {})
            if isinstance(gcp_val, dict):
                max_r = gcp_val.get("max_residual_meters")
                if max_r is not None:
                    gcp_max_residuals.append(max_r)
    q3_max_residual = max(gcp_max_residuals) if gcp_max_residuals else None

    # Q4: Semantic coverage from stage 5 reports
    total_text = 0
    total_linked = 0
    for rpt in all_reports:
        s5 = rpt.get("stage5", {})
        if isinstance(s5, dict):
            total_text += s5.get("text_total", 0)
            total_linked += s5.get("linked", 0)
    q4 = total_linked / total_text if total_text > 0 else 0

    # Q5: Schema conformance
    misc_count = sum(1 for e in entities_out if e.get("fc_name") == "fc_misc")
    q5 = 1.0 - (misc_count / len(entities_out)) if len(entities_out) > 0 else 1.0

    # Automation rate: entities that passed without human review
    human_review_count = 0
    for rpt in all_reports:
        s2 = rpt.get("stage2", {})
        if isinstance(s2, dict) and s2.get("human_review"):
            human_review_count += s2.get("entities", 0)
    ar = (len(entities_in) - human_review_count) / len(entities_in) if len(entities_in) > 0 else 1.0

    # Determine precision status
    precision_met = (q3_max_residual is not None and q3_max_residual <= 0.0012)
    if q3_max_residual is None:
        precision_status = "PRECISION_COARSE"
    elif q3_max_residual <= 0.0012:
        precision_status = "PASS"
    elif q3_max_residual <= 0.0015:
        precision_status = "WARN"
    else:
        precision_status = "FAIL"

    report = {
        "gpkg_file": gpkg_path,
        "metrics": {
            "Q1_geometric_completeness": round(q1, 4),
            "Q1_target_0.95": q1 >= 0.95,
            "Q2_topology_violation_rate": round(q2_violation_rate, 4),
            "Q2_target_0.02": q2_violation_rate <= 0.02,
            "Q3_max_gcp_residual_m": q3_max_residual,
            "Q3_precision_status": precision_status,
            "Q3_target_0.0012m": precision_met,
            "Q4_semantic_linkage_rate": round(q4, 4),
            "Q4_target_0.70": q4 >= 0.70,
            "Q5_schema_conformance": round(q5, 4),
            "Q5_target_0.80": q5 >= 0.80,
        },
        "automation": {
            "entities_in": len(entities_in),
            "entities_out": len(entities_out),
            "human_review_entities": human_review_count,
            "automation_rate": round(ar, 4),
            "target_0.90": ar >= 0.90,
        },
        "benchmarks_met": {
            "benchmark_1_automation": ar >= 0.90,
            "benchmark_2_precision": precision_met,
        },
    }
    with open(output_report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    stage_log(8, "Quality report written: %s", output_report_path)
    stage_log(8, "  Q1 (completeness): %.2f%% [%s]", q1 * 100, "PASS" if q1 >= 0.95 else "WARN")
    stage_log(8, "  Q2 (topology violation): %.4f [%s]", q2_violation_rate, "PASS" if q2_violation_rate <= 0.02 else "WARN")
    stage_log(8, "  Q3 (precision): %s [%s]", q3_max_residual if q3_max_residual else "NO_GCP", precision_status)
    stage_log(8, "  Q4 (semantic): %.2f%% [%s]", q4 * 100, "PASS" if q4 >= 0.70 else "WARN")
    stage_log(8, "  Q5 (schema): %.2f%% [%s]", q5 * 100, "PASS" if q5 >= 0.80 else "WARN")
    stage_log(8, "  Automation rate: %.2f%% [%s]", ar * 100,
              "PASS" if ar >= 0.90 else "BELOW TARGET")

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="GeoFormer — Multi-Stage CAD-to-GIS Pipeline (DWG → GeoPackage EPSG:3857)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dwg", required=True, help="Path to input DWG file")
    parser.add_argument("--output", required=True, help="Path to output GeoPackage file")
    parser.add_argument("--config-dir", default="./config", help="Path to config directory (default: ./config)")
    parser.add_argument("--tile-size", type=int, default=3000, help="Target entities per spatial tile (default: 3000)")
    parser.add_argument("--workers", type=int, default=multiprocessing.cpu_count(),
                        help="Number of parallel workers (default: cpu_count)")
    parser.add_argument("--no-parallel", action="store_true", help="Disable multiprocessing (debug mode)")
    parser.add_argument("--stages", default="1,2,3,4,5,6,7,8",
                        help="Comma-separated stages to run, e.g. '1,2,3' (default: all)")
    parser.add_argument("--ox-a", type=float, default=OX_A, help=f"Override Regime A OX (default: {OX_A})")
    parser.add_argument("--oy-a", type=float, default=OY_A, help=f"Override Regime A OY (default: {OY_A})")
    parser.add_argument("--ox-b", type=float, default=OX_B, help=f"Override Regime B OX (default: {OX_B})")
    parser.add_argument("--oy-b", type=float, default=OY_B, help=f"Override Regime B OY (default: {OY_B})")
    parser.add_argument("--temp-dir", default=None, help="Temp directory for tile files (default: system temp)")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temp files on exit (for debugging)")
    parser.add_argument("--no-qgis", action="store_true", help="Skip QGIS init (for Stage 1-only dry runs)")

    args = parser.parse_args()

    # Parse stages
    stages_to_run = set()
    for part in args.stages.split(","):
        part = part.strip()
        if part.isdigit():
            stages_to_run.add(int(part))
    if not stages_to_run:
        stages_to_run = {1, 2, 3, 4, 5, 6, 7, 8}

    ox_a, oy_a = args.ox_a, args.oy_a
    ox_b, oy_b = args.ox_b, args.oy_b
    bbox = UTM48N_BBOX

    # Validate inputs
    if not os.path.isfile(args.dwg):
        log.error("DWG file not found: %s", args.dwg)
        sys.exit(1)

    output_dir = os.path.dirname(os.path.abspath(args.output))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # QGIS initialization (required for Stages 1 and 7)
    qgs_app = None
    if not args.no_qgis:
        QgsApplication.setPrefixPath("/usr", True)
        qgs_app = QgsApplication([], False)
        qgs_app.initQgis()

    # Temp directory
    temp_dir = args.temp_dir if args.temp_dir else tempfile.mkdtemp(prefix="geoformer_")
    if not os.path.isdir(temp_dir):
        os.makedirs(temp_dir, exist_ok=True)
    log.info("Temp directory: %s", temp_dir)

    # Config files
    config_dir = args.config_dir
    gcp_path = os.path.join(config_dir, "gcp_dongxi.json")
    schema_path = os.path.join(config_dir, "schema_mapping.json")
    topo_path = os.path.join(config_dir, "topology_rules.json")
    vocab_path = os.path.join(config_dir, "layer_vocab.json")

    gcp_raw = load_json_config(gcp_path, default={})
    schema_raw = load_json_config(schema_path, default={})
    topo_raw = load_json_config(topo_path, default={})
    vocab_raw = load_json_config(vocab_path, default={})

    # Normalize config formats: JSON files have wrapper keys; internal code uses flat dicts
    # schema_mapping: unwrap layer_mappings, map fc_name→fc, geometry_type→geom, attributes→attrs
    if "layer_mappings" in schema_raw:
        schema_mapping = {}
        for layer, info in schema_raw["layer_mappings"].items():
            schema_mapping[layer] = {
                "fc": info.get("fc_name", "fc_misc"),
                "geom": info.get("geometry_type", "Geometry"),
                "attrs": info.get("attributes", {}),
            }
    elif schema_raw:
        schema_mapping = schema_raw
    else:
        schema_mapping = DEFAULT_SCHEMA_MAPPING

    # topology_rules: unwrap layer_rules, merge top-level defaults
    if "layer_rules" in topo_raw:
        topology_rules = dict(topo_raw["layer_rules"])
        for k in ("snap_tolerance_default_m", "overshoot_clip_range_m",
                  "duplicate_hausdorff_threshold_m", "sliver_area_threshold_sqm"):
            if k in topo_raw:
                topology_rules["_" + k] = topo_raw[k]
    elif topo_raw:
        topology_rules = topo_raw
    else:
        topology_rules = DEFAULT_TOPOLOGY_RULES

    # layer_vocab: unwrap layer_vocabularies
    if "layer_vocabularies" in vocab_raw:
        layer_vocab = vocab_raw["layer_vocabularies"]
    elif vocab_raw:
        layer_vocab = vocab_raw
    else:
        layer_vocab = DEFAULT_LAYER_VOCAB

    # gcp_data: flatten regime_A.gcps + regime_B.gcps into list with regime tags
    gcp_data = []
    if "regime_A" in gcp_raw:
        for g in gcp_raw["regime_A"].get("gcps", []):
            gcp_data.append({**g, "regime": "A"})
    if "regime_B" in gcp_raw:
        for g in gcp_raw["regime_B"].get("gcps", []):
            gcp_data.append({**g, "regime": "B"})
    if not gcp_data and isinstance(gcp_raw, list):
        gcp_data = gcp_raw

    if not gcp_data:
        log.warning("No GCP file found at %s — precision validation will be PRECISION_COARSE", gcp_path)
    else:
        log.info("Loaded %d GCPs", len(gcp_data))

    entities_out = []
    all_reports = []
    status = "SUCCESS"

    try:
        # ── Stage 1: Ingestion & Tiling ───────────────────────────────────────
        if 1 in stages_to_run:
            manifest, tile_files = stage1_ingestion(args.dwg, temp_dir, args.tile_size)
            with open(os.path.join(temp_dir, "manifest.json"), "r", encoding="utf-8") as f:
                manifest = json.load(f)
            # Rebuild tile_files dict
            tile_files = {}
            for t in manifest.get("tiles", []):
                tid = t["tile_id"]
                jl_path = os.path.join(temp_dir, f"tile_{tid}.jsonl")
                if os.path.isfile(jl_path):
                    tile_files[tid] = jl_path
        else:
            # Load existing manifest
            manifest_path = os.path.join(temp_dir, "manifest.json")
            if not os.path.isfile(manifest_path):
                log.error("No manifest found and Stage 1 skipped. Run Stage 1 first.")
                sys.exit(1)
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            tile_files = {}
            for t in manifest.get("tiles", []):
                tid = t["tile_id"]
                jl_path = os.path.join(temp_dir, f"tile_{tid}.jsonl")
                if os.path.isfile(jl_path):
                    tile_files[tid] = jl_path

        total_entities_in = manifest.get("entities_valid", 0)
        log.info("Stage 1 complete: %d entities in %d tiles", total_entities_in, len(tile_files))

        if not tile_files:
            log.warning("No tiles to process. Exiting.")
            return

        # ── Stages 2-6: Per-tile parallel processing ──────────────────────────
        tile_stages = stages_to_run & {2, 3, 4, 5, 6}
        if tile_stages:
            if args.no_parallel or len(tile_files) == 1:
                # Serial mode
                log.info("Running tile pipeline SERIALLY (%d tiles)", len(tile_files))
                tile_results = []
                for tile_id, jl_path in sorted(tile_files.items()):
                    r = process_tile_pipeline(jl_path, temp_dir, tile_id, tile_stages,
                                              ox_a, oy_a, ox_b, oy_b, bbox,
                                              gcp_data, topology_rules, layer_vocab, schema_mapping)
                    tile_results.append(r)
                    all_reports.append(r.get("reports", {}))
                    log.info("  Tile %s: %s", tile_id, r["status"])
            else:
                # Parallel mode
                n_workers = min(args.workers, len(tile_files))
                log.info("Running tile pipeline with %d workers (%d tiles)", n_workers, len(tile_files))
                tile_results = []
                with ProcessPoolExecutor(max_workers=n_workers) as executor:
                    futures = {}
                    for tile_id, jl_path in sorted(tile_files.items()):
                        fut = executor.submit(process_tile_pipeline, jl_path, temp_dir,
                                              tile_id, tile_stages,
                                              ox_a, oy_a, ox_b, oy_b, bbox,
                                              gcp_data, topology_rules, layer_vocab, schema_mapping)
                        futures[fut] = tile_id
                    for fut in as_completed(futures):
                        tile_id = futures[fut]
                        try:
                            r = fut.result()
                        except Exception as e:
                            r = {"tile_id": tile_id, "status": "FAILED", "error": str(e), "reports": {}}
                            log.error("  Tile %s: EXCEPTION %s", tile_id, e)
                        tile_results.append(r)
                        all_reports.append(r.get("reports", {}))
                        log.info("  Tile %s: %s", tile_id, r["status"])

            # Collect processed entities from all tiles
            for r in tile_results:
                if r["status"] == "COMPLETE" and "final_jsonl" in r:
                    jl_path = r["final_jsonl"]
                    if os.path.isfile(jl_path):
                        try:
                            with open(jl_path, "r", encoding="utf-8") as f:
                                for line in f:
                                    line = line.strip()
                                    if line:
                                        entities_out.append(json.loads(line))
                        except Exception as e:
                            log.error("Failed to read tile output %s: %s", jl_path, e)

            failed_tiles = [r for r in tile_results if r["status"] not in ("COMPLETE",)]
            if failed_tiles:
                log.warning("%d tiles failed or flagged for review: %s",
                            len(failed_tiles),
                            ", ".join(r["tile_id"] for r in failed_tiles))
                status = "PARTIAL"
        else:
            log.info("Skipping Stages 2-6")

        # ── Stage 7: Spatial Assembler ───────────────────────────────────────
        if 7 in stages_to_run:
            if not entities_out:
                log.warning("No processed entities for Stage 7 — skipping GeoPackage write")
            else:
                stage7_assemble(entities_out, args.output, temp_dir)
        else:
            log.info("Skipping Stage 7 (GeoPackage write)")

        # ── Stage 8: Quality Sentinel ────────────────────────────────────────
        if 8 in stages_to_run:
            report_path = os.path.splitext(args.output)[0] + "_report.json"
            entities_in_list = [{"placeholder": True}] * total_entities_in  # dummy list for count
            qa_report = stage8_quality(entities_in_list, entities_out, all_reports,
                                       args.output, report_path)
        else:
            log.info("Skipping Stage 8 (Quality report)")

    finally:
        # Clean up temp files (unless --keep-temp)
        if not args.keep_temp and temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            log.info("Cleaned up temp directory: %s", temp_dir)
        elif args.keep_temp:
            log.info("Temp directory kept: %s", temp_dir)

        if qgs_app and not args.no_qgis:
            qgs_app.exitQgis()

    log.info("GeoFormer pipeline %s: %s", status, args.output)


if __name__ == "__main__":
    main()
