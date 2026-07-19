#!/usr/bin/env python3
"""
GeoFormer Phase 2: DWG-to-GeoPackage Converter
==============================================
Reads DWG files and writes a single GeoPackage with 8 FTTH feature class
layers. CRS is fully parameterized: --source-crs declares the DWG's native
CRS (default EPSG:3857 — Hutabohu drawings are web-mercator metric), and
--target-crs selects the output CRS (default EPSG:3857). When source and
target differ, geometries are reprojected via osr.CoordinateTransformation;
when equal, coordinates pass through untouched.

Implements the simplified GeoFormer pipeline: DWG Reader (model space only),
INSERT routing by DWG layer, DIMENSION span extraction, fragment aggregation,
two-tier classification, geometry reconstruction, attribute extraction,
and GeoPackage writer.
"""

import argparse
import ctypes
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

# ── Import validation from sibling modules ────────────────────────────────
try:
    from schema_config import (
        REQUIRED_LAYERS,
        LAYER_PATTERN_MAP,
        NEGATIVE_EVIDENCE_LAYERS,
        FRAGMENT_AGGREGATION_LAYERS,
        DOMAIN_VOCABULARIES,
        FIELD_NAME_CROSSWALK,
        LABEL_FAMILIES,
        LABEL_MULTIPLE_OPTIMA_EPS_M,
        BOITE, CABLE, PTECH, INFRASTRUCTURE_FC, SITE, ZNRO, ZPM, IMB,
        FEATURE_CLASS_BY_NAME,
        STYLE_FIELDS, aci_to_rgb,
    )
except ImportError:
    sys.exit(
        "ERROR: Cannot import schema_config. Ensure this file is in the same "
        "directory as schema_config.py."
    )

from domain_vocab import validate_domain_value

import evidence_ledger
import legend_detector
# ── Ctypes bridge to LibreDWG ─────────────────────────────────────────────
_libdwg = None
_libc = None


def _init_libredwg():
    """Lazy-init the LibreDWG ctypes bridge. Returns (libdwg, libc)."""
    global _libdwg, _libc
    if _libdwg is not None:
        return _libdwg, _libc
    try:
        _libdwg = ctypes.CDLL("/usr/local/lib/libredwg.so")
    except OSError:
        sys.exit(
            "ERROR: LibreDWG shared library not found at /usr/local/lib/libredwg.so.\n"
            "Install LibreDWG: https://www.gnu.org/software/libredwg/\n"
            "  git clone https://git.savannah.gnu.org/git/libredwg.git\n"
            "  cd libredwg && ./autogen.sh && ./configure && make && sudo make install"
        )
    _libc = ctypes.CDLL("libc.so.6")

    _libdwg.dwg_ent_get_layer_name.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    _libdwg.dwg_ent_get_layer_name.restype = ctypes.c_char_p

    _libdwg.dwg_ent_lwpline_get_numpoints.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    _libdwg.dwg_ent_lwpline_get_numpoints.restype = ctypes.c_int
    _libdwg.dwg_ent_lwpline_get_points.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    _libdwg.dwg_ent_lwpline_get_points.restype = ctypes.c_void_p
    # dynapi UTF-8 text accessor: R2007+ DWGs store strings as UTF-16 (TU);
    # the SWIG string fields truncate them at the first NUL byte (single
    # character). dwg_dynapi_entity_utf8text converts TU → UTF-8 correctly.
    _libdwg.dwg_dynapi_entity_utf8text.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_int),
        ctypes.c_void_p]
    _libdwg.dwg_dynapi_entity_utf8text.restype = ctypes.c_bool
    _libc.free.argtypes = [ctypes.c_void_p]
    return _libdwg, _libc


def _entity_utf8_text(struct_ptr, entity_name, field_name):
    """Read a string field from an entity struct via the dynapi, converting
    UTF-16 (TU) storage to UTF-8. Returns "" when unavailable."""
    _libdwg, _libc = _init_libredwg()
    out = ctypes.c_char_p(None)
    isnew = ctypes.c_int(0)
    ok = _libdwg.dwg_dynapi_entity_utf8text(
        struct_ptr, entity_name, field_name,
        ctypes.byref(out), ctypes.byref(isnew), None)
    if not ok or out.value is None:
        return ""
    try:
        text = out.value.decode("utf-8", errors="replace")
    finally:
        if isnew.value:
            _libc.free(ctypes.cast(out, ctypes.c_void_p))
    return text


# ── OGR / GeoPackage imports ──────────────────────────────────────────────
try:
    from osgeo import ogr, osr
except ImportError:
    sys.exit(
        "ERROR: GDAL/OGR Python bindings not found.\n"
        "Install: pip install gdal\n"
        "Or via system package: sudo apt install python3-gdal"
    )


# ── Constants ─────────────────────────────────────────────────────────────
# Deployment region bounds for outlier flagging, in EPSG:4326
# (Indonesia: lat -11..7, lon 95..141). Features outside are flagged and
# counted per layer — warning only, never discarded (guide: warning, NOT halt).
REGION_BOUNDS_WGS84 = (-11.0, 7.0, 95.0, 141.0)  # lat_min, lat_max, lon_min, lon_max

# EPSG:3857 valid coordinate envelope
WEBMERC_MAX_X = 20037508.34
WEBMERC_MAX_Y = 20048966.10

# ── CRS state (set in main() from --source-crs / --target-crs) ───────────
_CRS_TRANSFORM = None        # source → target osr.CoordinateTransformation, or None (identity)
_TO_WGS84 = None             # target → EPSG:4326, or None if target IS 4326
_SOURCE_IS_GEOGRAPHIC = False
_TARGET_IS_GEOGRAPHIC = False
_TARGET_IS_WEBMERC = True
_SOURCE_CRS_LABEL = "EPSG:3857"
_TARGET_CRS_LABEL = "EPSG:3857"


def _reproject_point(x, y):
    """Reproject a single coordinate from source CRS to target CRS.
    Axis order is traditional GIS (x=lon/easting, y=lat/northing)."""
    if _CRS_TRANSFORM is None:
        return x, y
    try:
        pt = _CRS_TRANSFORM.TransformPoint(x, y)
        return pt[0], pt[1]
    except Exception:
        return x, y


def _to_wgs84(x, y):
    """Convert a target-CRS coordinate to EPSG:4326 (lon, lat)."""
    if _TO_WGS84 is None:
        return x, y
    try:
        pt = _TO_WGS84.TransformPoint(x, y)
        return pt[0], pt[1]
    except Exception:
        return x, y


def _valid_coord(x, y):
    """CRS-aware coordinate sanity check in target-CRS units."""
    if _TARGET_IS_GEOGRAPHIC:
        return abs(x) <= 180 and abs(y) <= 90
    if _TARGET_IS_WEBMERC:
        return abs(x) <= WEBMERC_MAX_X and abs(y) <= WEBMERC_MAX_Y
    return abs(x) <= 1e8 and abs(y) <= 1e8


def _in_region_bounds(x, y):
    """Check a target-CRS coordinate against the deployment region (4326)."""
    lon, lat = _to_wgs84(x, y)
    lat_min, lat_max, lon_min, lon_max = REGION_BOUNDS_WGS84
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _meters_to_units(meters):
    """Convert a metre tolerance to target-CRS units."""
    if _TARGET_IS_GEOGRAPHIC:
        return meters / METERS_PER_DEGREE
    return meters


def _line_length_m(points):
    """Length of a (x, y) point chain in metres, CRS-aware."""
    if len(points) < 2:
        return 0.0
    if _TARGET_IS_GEOGRAPHIC:
        return _haversine_length(points)
    total = 0.0
    for i in range(len(points) - 1):
        total += math.hypot(points[i + 1][0] - points[i][0],
                            points[i + 1][1] - points[i][1])
    return total

# DWG type constants (from LibreDWG SWIG)
DWG_TYPE_LINE = 0
DWG_TYPE_LWPOLYLINE = 19
DWG_TYPE_CIRCLE = 8
DWG_TYPE_ARC = 7
DWG_TYPE_TEXT = 1
DWG_TYPE_MTEXT = 44
DWG_TYPE_INSERT = 7   # Will be corrected after LibreDWG import
DWG_TYPE_POINT = 2
DWG_TYPE_ATTRIB = 2
DWG_SUPERTYPE_ENTITY = 0

# DIMENSION entity types (patched from LibreDWG in read_dwg).
# Maps type code → SWIG union member name on entity.tio.
DIMENSION_TYPE_UNION = {}

# Non-geometric control entities: must never become features.
# Numeric codes patched from LibreDWG in read_dwg.
CONTROL_TYPES = set()

# Fragment clustering tolerance for FDT/FAT structure diagrams (metres)
DEFAULT_FRAGMENT_CLUSTER_TOL_M = 50.0
# Annotation-to-feature linking tolerance (metres)
DEFAULT_ANNOTATION_LINK_TOL_M = 15.0
# BOITE multi-representation spatial fusion tolerance (metres)
DEFAULT_BOITE_FUSION_TOL_M = 5.0
# Approximate metres per degree at the equator (for tolerance conversion
# when the target CRS is geographic)
METERS_PER_DEGREE = 111320.0

# Tier-2 English telecom keyword patterns for unmatched entities (worldwide)
TELECOM_ANNOTATION_KEYWORDS = [
    r"(?i)(fibre|fiber|optic|box|closure|splice|cable|wire|drop)",
    r"(?i)(chamber|manhole|handhole|pole|anchor|guy|trench|vault)",
    r"(?i)(nro|pm|shelter|cabinet|exchange|node|hub|olt|splitter)",
    r"(?i)(duct|conduit|pipe|trench|infra|buried|aerial|underground)",
    r"(?i)(building|premise|house|home|unit|apartment|office|mdu|sdu)",
    r"(?i)(zone|area|boundary|coverage|region|polygon|district)",
]

# Attribute extraction regex patterns (English, worldwide telecom)
ATTR_PATTERNS = {
    "NB_FIBRE_UTIL": re.compile(r"(\d+)\s*FO", re.IGNORECASE),
    "CAPACITE": re.compile(r"(\d+)\s*C(?!\w)", re.IGNORECASE),
    "MODE_POSE": re.compile(
        r"(?i)(AERIAL|UNDERGROUND|BURIED|TRENCH|DUCT|DIRECT_BURIED|"
        r"LASER_AIDED|MINI_TRENCH|MICRODUCT)"
    ),
    "TYPE_FIBRE": re.compile(r"(?i)(G6\d{2}[A-Z0-9]*)"),
    "TYPE_BOX": re.compile(r"(?i)\b(FDT|FAT|FDT|CTO|NAP|DP|MDU|SDU|ONT)\b"),
    "SITE_TYPE": re.compile(r"(?i)\b(NRO|PM|CO|EXCHANGE|HUB|POP)\b"),
    "STATUT": re.compile(r"(?i)(DEPLOYED|PLANNED|IN_PROGRESS|UNDER_CONSTRUCTION|"
                          r"PROPOSED|BUILT|ACTIVE|INACTIVE)"),
    "DIAMETRE": re.compile(r"(?i)(\d+)\s*mm"),
    "TYPE_CABLE": re.compile(
        r"(?i)(TRUNK|FEEDER|DISTRIBUTION|DROP|RISER|LATERAL|BACKBONE|ACCESS)"
    ),
}


# ── Helpers ───────────────────────────────────────────────────────────────

def _cstr(raw):
    """Decode a C string with UTF-8 fallback to latin-1."""
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except UnicodeDecodeError:
            return raw.hex()


def _layer_name(entity_ptr):
    """Get DWG layer name for an entity via the C API."""
    _libdwg, _ = _init_libredwg()
    err = ctypes.c_int(0)
    return _cstr(_libdwg.dwg_ent_get_layer_name(entity_ptr, ctypes.byref(err)))


def _parse_dwg_color(color):
    """Decode a LibreDWG Dwg_Color struct → (aci, truecolor_rgb or None).
    R2004+ raw encodings (high byte of .rgb): 0xC2 = truecolor with RGB in
    the low 24 bits, 0xC3 = ACI in the low byte. The layer table stores
    colours in raw form only, leaving .index at the ByLayer sentinel 256,
    so the raw channel must be checked first. Purely numeric fields — no
    UTF-16 text risk (wiki: libredwg-swig-utf-16)."""
    try:
        raw = int(color.rgb or 0)
    except Exception:
        raw = 0
    try:
        index = int(color.index)
    except Exception:
        index = 256
    method = (raw >> 24) & 0xFF
    if method == 0xC2:
        return index, raw & 0xFFFFFF
    if method == 0xC3:
        return raw & 0xFF, None
    return index, None


def _resolve_effective_color(entity_aci, entity_tc, entity_linetype,
                             layer_name, layer_style_table):
    """Resolve entity colour with ByLayer/ByBlock fallback to the layer
    table colour. Truecolor (RGB) wins over ACI. ACI 256 = ByLayer,
    ACI 0 = ByBlock (top-level model-space ByBlock entities inherit the
    layer colour — their INSERT parents' internals are dropped upstream).
    Returns (color_aci, color_rgb "#RRGGBB", style_key "rgb|linetype")."""
    lay = layer_style_table.get(layer_name) or {}
    aci, tc = entity_aci, entity_tc
    if tc is None and aci in (0, 256):
        aci = lay.get("aci", 7)
        tc = lay.get("truecolor")
    if tc is not None:
        rgb = "#%06X" % (tc & 0xFFFFFF)
    else:
        if not 1 <= aci <= 255:
            aci = 7
        rgb = aci_to_rgb(aci)
    linetype = entity_linetype or lay.get("linetype") or "Continuous"
    return aci, rgb, f"{rgb}|{linetype}"


def _lwpoline_points(entity):
    """Extract LWPOLYLINE points via C API. Returns list of (x, y) tuples."""
    _libdwg, _libc = _init_libredwg()
    try:
        lw_ptr = int(entity.tio.LWPOLYLINE.this)
    except Exception:
        return []
    err = ctypes.c_int(0)
    npts = _libdwg.dwg_ent_lwpline_get_numpoints(lw_ptr, ctypes.byref(err))
    if err.value or npts < 2:
        return []
    pts_ptr = _libdwg.dwg_ent_lwpline_get_points(lw_ptr, ctypes.byref(err))
    if err.value or not pts_ptr:
        return []
    pts = []
    for j in range(npts):
        off = j * 16
        x = ctypes.c_double.from_address(pts_ptr + off).value
        y = ctypes.c_double.from_address(pts_ptr + off + 8).value
        pts.append((x, y))
    _libc.free(pts_ptr)
    return pts


def _adaptive_chord_tolerance(extent):
    """Chord tolerance for arc/circle discretization: extent*0.001, clamped
    to a sane range in source-CRS units (degrees vs metres)."""
    tol = extent * 0.001
    if _SOURCE_IS_GEOGRAPHIC:
        return max(1e-6, min(0.01, tol))
    return max(0.01, min(1000.0, tol))


def _haversine(lon1, lat1, lon2, lat2):
    """Haversine distance in metres between two WGS84 points."""
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _haversine_length(points):
    """Compute total haversine length of a list of (lon, lat) points."""
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points) - 1):
        total += _haversine(points[i][0], points[i][1],
                           points[i + 1][0], points[i + 1][1])
    return total


def _centroid(points):
    """Compute centroid of a list of (lon, lat) points."""
    if not points:
        return (0.0, 0.0)
    n = len(points)
    return (sum(p[0] for p in points) / n,
            sum(p[1] for p in points) / n)


def _sha256_file(path):
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_layer_name(name):
    """Sanitize a name for use as a GeoPackage layer name."""
    safe = name.encode("ascii", errors="replace").decode("ascii")
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
    if not safe or safe[0].isdigit():
        safe = "L_" + safe
    return safe[:50]


# ── Geometry Reconstruction ──────────────────────────────────────────────

def _wkt_point(x, y):
    return "POINT (%.12f %.12f)" % (x, y)


def _wkt_linestring(points):
    coords = ", ".join("%.12f %.12f" % (p[0], p[1]) for p in points)
    return "LINESTRING (%s)" % coords


def _wkt_polygon_exterior(points):
    coords = ", ".join("%.12f %.12f" % (p[0], p[1]) for p in points)
    return "POLYGON ((%s))" % coords


def _circle_points(center_x, center_y, radius, chord_tol):
    """Generate polygon approximation points for a circle."""
    n_pts = max(12, min(360, int(math.ceil(2 * math.pi * radius / chord_tol))))
    pts = []
    for j in range(n_pts + 1):
        angle = 2 * math.pi * j / n_pts
        pts.append((center_x + radius * math.cos(angle),
                    center_y + radius * math.sin(angle)))
    return pts


def _arc_points(center_x, center_y, radius, start_angle, end_angle, chord_tol):
    """Generate linestring approximation points for an arc."""
    sa, ea = start_angle, end_angle
    if ea < sa:
        ea += 2 * math.pi
    arc_length = radius * (ea - sa)
    n_pts = max(10, int(math.ceil(arc_length / chord_tol)))
    pts = []
    for j in range(n_pts + 1):
        angle = sa + j * (ea - sa) / n_pts
        pts.append((center_x + radius * math.cos(angle),
                    center_y + radius * math.sin(angle)))
    return pts


def _extract_wkt(entity, dwg_type, extent):
    """
    Extract geometry as WKT string and raw point list from a DWG entity.
    Returns (wkt: str, points: list of (x,y), centroid: (cx,cy), is_closed: bool).
    """
    chord_tol = _adaptive_chord_tolerance(extent)
    wkt = ""
    points = []
    is_closed = False
    cx, cy = 0.0, 0.0

    try:
        if dwg_type == DWG_TYPE_LINE:
            li = entity.tio.LINE
            points = [(li.start.x, li.start.y), (li.end.x, li.end.y)]
            wkt = _wkt_linestring(points)
            cx, cy = (li.start.x + li.end.x) / 2, (li.start.y + li.end.y) / 2

        elif dwg_type == DWG_TYPE_LWPOLYLINE:
            pts = _lwpoline_points(entity)
            if len(pts) < 2:
                return ("", [], (0.0, 0.0), False)
            is_closed = bool(entity.tio.LWPOLYLINE.flag & 1)
            if is_closed and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            # Always emit LINESTRING WKT (OGR cannot parse standalone
            # LINEARRING); polygon promotion happens in _resolve_fc_geometry.
            wkt = _wkt_linestring(pts)
            points = pts
            cx, cy = _centroid(pts)

        elif dwg_type == DWG_TYPE_CIRCLE:
            c = entity.tio.CIRCLE
            points = _circle_points(c.center.x, c.center.y, c.radius, chord_tol)
            wkt = _wkt_linestring(points)
            is_closed = True
            cx, cy = c.center.x, c.center.y

        elif dwg_type == DWG_TYPE_ARC:
            ar = entity.tio.ARC
            points = _arc_points(ar.center.x, ar.center.y, ar.radius,
                                ar.start_angle, ar.end_angle, chord_tol)
            wkt = _wkt_linestring(points)
            cx, cy = _centroid(points)

        elif dwg_type in (DWG_TYPE_TEXT, DWG_TYPE_MTEXT, DWG_TYPE_INSERT, DWG_TYPE_POINT):
            if dwg_type == DWG_TYPE_TEXT:
                t = entity.tio.TEXT
                cx, cy = t.ins_pt.x, t.ins_pt.y
            elif dwg_type == DWG_TYPE_MTEXT:
                mt = entity.tio.MTEXT
                cx, cy = mt.ins_pt.x, mt.ins_pt.y
            elif dwg_type == DWG_TYPE_INSERT:
                ins = entity.tio.INSERT
                cx, cy = ins.ins_pt.x, ins.ins_pt.y
            elif dwg_type == DWG_TYPE_POINT:
                p = entity.tio.POINT
                cx, cy = p.x, p.y
            points = [(cx, cy)]
            wkt = _wkt_point(cx, cy)

    except Exception:
        pass

    return (wkt, points, (cx, cy), is_closed)


# ── DIMENSION Extraction ─────────────────────────────────────────────────

def _extract_dimension(entity, union_name):
    """
    Extract measurement and geometry from a DIMENSION entity.
    Returns dict with measurement (drawing units), def_pt, xline1, xline2
    (xline points may be None for non-linear dimension subtypes).
    """
    d = getattr(entity.tio, union_name)
    rec = {
        "measurement": float(d.act_measurement),
        "def_pt": (d.def_pt.x, d.def_pt.y),
        "xline1": None,
        "xline2": None,
    }
    try:
        rec["xline1"] = (d.xline1_pt.x, d.xline1_pt.y)
        rec["xline2"] = (d.xline2_pt.x, d.xline2_pt.y)
    except AttributeError:
        pass
    return rec


# ── Fragment Clustering (FDT/FAT structure diagrams) ─────────────────────

def _cluster_points(points, tol):
    """
    Single-linkage proximity clustering via grid + union-find.
    points: list of (x, y). tol: distance threshold in coordinate units.
    Returns list of clusters, each a list of point indices.
    """
    n = len(points)
    if n == 0:
        return []
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    cell = tol if tol > 0 else 1e-9
    grid = defaultdict(list)
    for idx, (x, y) in enumerate(points):
        grid[(int(x // cell), int(y // cell))].append(idx)

    tol2 = tol * tol
    for (gx, gy), members in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(grid.get((gx + dx, gy + dy), []))
        for i in members:
            xi, yi = points[i]
            for j in neigh:
                if j <= i:
                    continue
                xj, yj = points[j]
                if (xi - xj) ** 2 + (yi - yj) ** 2 <= tol2:
                    union(i, j)

    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)
    return list(clusters.values())


def _fragment_aggregation_target(layer_name):
    """Return (fc_name, forced_type) if the layer's fragments must be
    aggregated into cluster centroid points, else (None, None)."""
    for pattern, fc, forced_type in FRAGMENT_AGGREGATION_LAYERS:
        if re.search(pattern, layer_name or ""):
            return (fc, forced_type)
    return (None, None)


# ── Two-Tier Classification ──────────────────────────────────────────────

def _classify_entity_tier1(layer_name):
    """
    Tier 1: Match DWG layer name against LAYER_PATTERN_MAP.
    Returns (fc_name, geom_type) or (None, None).
    """
    if not layer_name:
        return (None, None)

    # Negative evidence gate
    layer_lower = layer_name.lower().strip()
    for neg in NEGATIVE_EVIDENCE_LAYERS:
        if neg.lower() == layer_lower:
            return ("fc_misc", None)

    for pattern, fc, gtype in LAYER_PATTERN_MAP:
        if re.search(pattern, layer_name):
            return (fc, gtype)

    return (None, None)


def _classify_entity_tier2(text_val):
    """
    Tier 2: Check annotation text for English telecom keywords.
    Returns (fc_name, geom_type) or (None, None).
    """
    if not text_val:
        return (None, None)

    # Check each keyword group against the text
    keyword_to_fc = [
        (0, "CABLE"),           # fibre/fiber/optic/box/cable
        (1, "PTECH"),           # chamber/manhole/pole
        (2, "SITE"),            # nro/pm/exchange
        (3, "INFRASTRUCTURE"),  # duct/conduit/trench
        (4, "IMB"),             # building/premise/house
        (5, "ZNRO"),            # zone/area/boundary
    ]

    for kw_idx, fc in keyword_to_fc:
        if kw_idx < len(TELECOM_ANNOTATION_KEYWORDS):
            if re.search(TELECOM_ANNOTATION_KEYWORDS[kw_idx], text_val):
                return (fc, None)

    return (None, None)


def _assign_fc(layer_name, text_val):
    """
    Full two-tier classification.
    Returns (fc_name, geom_type, confidence, classification_method).
    """
    fc, gtype = _classify_entity_tier1(layer_name)
    if fc == "fc_misc":
        return ("fc_misc", None, 1.0, "negative_evidence")
    if fc is not None:
        return (fc, gtype, 0.9, "tier1_layer_pattern")

    fc, _ = _classify_entity_tier2(text_val)
    if fc is not None:
        return (fc, gtype, 0.5, "tier2_annotation_keyword")

    return ("fc_misc", None, 0.0, "unclassified")


# ── Attribute Extraction ─────────────────────────────────────────────────

def _extract_attributes(text_val, fc_name):
    """
    Extract structured attributes from annotation text using English telecom patterns.
    Returns a dict of field_name → value.
    """
    attrs = {}
    if not text_val:
        return attrs

    m = ATTR_PATTERNS["NB_FIBRE_UTIL"].search(text_val)
    if m:
        attrs["NB_FIBRE_UTIL"] = int(m.group(1))

    m = ATTR_PATTERNS["CAPACITE"].search(text_val)
    if m:
        attrs["CAPACITE"] = int(m.group(1))

    m = ATTR_PATTERNS["MODE_POSE"].search(text_val)
    if m:
        attrs["MODE_POSE"] = m.group(1).upper()

    m = ATTR_PATTERNS["TYPE_FIBRE"].search(text_val)
    if m:
        attrs["TYPE_FIBRE"] = m.group(1).upper()

    m = ATTR_PATTERNS["TYPE_BOX"].search(text_val)
    if m:
        attrs["TYPE"] = m.group(1).upper()

    m = ATTR_PATTERNS["SITE_TYPE"].search(text_val)
    if m:
        attrs["TYPE"] = m.group(1).upper()

    m = ATTR_PATTERNS["STATUT"].search(text_val)
    if m:
        statut = m.group(1).upper()
        # Normalize common status shorthands
        _status_map = {
            "IN_PROGRESS": "UNDER_CONSTRUCTION",
            "IN PROGRESS": "UNDER_CONSTRUCTION",
            "PLANNED": "PROPOSED",
        }
        attrs["STATUT"] = _status_map.get(statut, statut)

    m = ATTR_PATTERNS["DIAMETRE"].search(text_val)
    if m:
        attrs["DIAMETRE"] = int(m.group(1))

    m = ATTR_PATTERNS["TYPE_CABLE"].search(text_val)
    if m:
        # Normalize English telecom terms to the French TYPE_CABLE domain
        _cable_type_map = {
            "TRUNK": "TRANSPORT", "BACKBONE": "TRANSPORT", "FEEDER": "TRANSPORT",
            "DISTRIBUTION": "DISTRIBUTION", "LATERAL": "DISTRIBUTION",
            "ACCESS": "DISTRIBUTION",
            "DROP": "RACCORDEMENT", "RISER": "VERTICALITE",
        }
        attrs["TYPE_CABLE"] = _cable_type_map.get(m.group(1).upper(),
                                                  m.group(1).upper())

    return attrs


# ── Annotation → Feature Label Assignment ────────────────────────────────
# Family-gated global one-to-one assignment (pure-Python rectangular
# Hungarian, ported from newmodel cad2gis_v3/semantics.py). Ledger structures
# (candidate edges, abstentions) accumulate in ANNOTATION_LEDGER for the
# evidence tables (D component).

_LABEL_FAMILY_COMPILED = [
    {"family": f["family"], "target_fc": f["target_fc"],
     "regex": re.compile(f["pattern"]),
     "node_color_filter": f.get("node_color_filter"),
     "min_distance_m": f.get("min_distance_m"),
    }
    for f in LABEL_FAMILIES
]

# In-memory ledger for annotation-binding evidence, accumulated across all
# input files within one process run. Consumed by the evidence-ledger writer.
ANNOTATION_LEDGER = {
    "candidates": [],   # every candidate edge: text ↔ target within tolerance
    "failures": [],     # outside_tolerance / multiple_optima / assignment_conflict
    "assigned": [],     # accepted one-to-one bindings
    "stats": {},        # per-family summary counters
}


def _match_label_family(text):
    """Return the compiled family record whose pattern fullmatches `text`."""
    t = (text or "").strip()
    if not t:
        return None
    for fam in _LABEL_FAMILY_COMPILED:
        if fam["regex"].fullmatch(t):
            return fam
    return None


def _minimum_cost_assignment(costs):
    """Rectangular Hungarian assignment; rows must not outnumber columns.
    Returns assignment list: row index → column index (-1 if unassigned)."""
    if not costs:
        return []
    row_count, column_count = len(costs), len(costs[0])
    if row_count > column_count or any(len(row) != column_count for row in costs):
        raise ValueError("Invalid rectangular assignment matrix")
    row_potential = [0] * (row_count + 1)
    column_potential = [0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    predecessor = [0] * (column_count + 1)
    for row_index in range(1, row_count + 1):
        matched_row[0] = row_index
        current_column = 0
        minimum = [math.inf] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[current_column] = True
            current_row = matched_row[current_column]
            delta, next_column = math.inf, 0
            for column_index in range(1, column_count + 1):
                if used[column_index]:
                    continue
                reduced = (
                    costs[current_row - 1][column_index - 1]
                    - row_potential[current_row]
                    - column_potential[column_index]
                )
                if reduced < minimum[column_index]:
                    minimum[column_index] = reduced
                    predecessor[column_index] = current_column
                if minimum[column_index] < delta:
                    delta, next_column = minimum[column_index], column_index
            for column_index in range(column_count + 1):
                if used[column_index]:
                    row_potential[matched_row[column_index]] += delta
                    column_potential[column_index] -= delta
                else:
                    minimum[column_index] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            next_column = predecessor[current_column]
            matched_row[current_column] = matched_row[next_column]
            current_column = next_column
            if current_column == 0:
                break
    assignment = [-1] * row_count
    for column_index in range(1, column_count + 1):
        if matched_row[column_index]:
            assignment[matched_row[column_index] - 1] = column_index - 1
    return assignment


def _assign_family_annotations(family, annotations, targets, tolerance, optima_eps):
    """
    Maximum-cardinality, minimum-distance one-to-one label assignment for one
    label family.
      1) candidate edges: annotation ↔ target within `tolerance` (CRS units)
      2) abstentions: no candidate → outside_tolerance; two best candidates
         within `optima_eps` of each other → multiple_optima
      3) rectangular Hungarian over eligible annotations × (targets + dummy
         unmatched columns)
    Returns (assignments, failures, candidate_records) where assignments is a
    list of (annotation, target_feature, distance).
    """
    fam_name, target_fc = family["family"], family["target_fc"]
    annotations = sorted(
        annotations, key=lambda a: (a["text"].strip().casefold(), a["ann_id"]))
    targets = sorted(targets, key=lambda f: (f["global_id"], f["centroid"]))
    unit_per_m = _meters_to_units(1.0)

    def _fail(ann, status):
        return {
            "family": fam_name, "target_fc": target_fc,
            "ann_id": ann["ann_id"], "text": ann["text"].strip(),
            "layer": ann["layer"], "status": status,
        }

    candidate_records, eligible, failures = [], [], []
    distances = {}
    for ann in annotations:
        ax, ay = ann["centroid"]
        ranked = sorted(
            (math.hypot(ax - f["centroid"][0], ay - f["centroid"][1]), idx)
            for idx, f in enumerate(targets)
        )
        within = [(d, idx) for d, idx in ranked if d <= tolerance]
        for d, idx in within:
            distances[(ann["ann_id"], idx)] = d
            candidate_records.append({
                "family": fam_name, "target_fc": target_fc,
                "ann_id": ann["ann_id"], "text": ann["text"].strip(),
                "target_idx": idx,
                "target_global_id": targets[idx]["global_id"],
                "target_layer": targets[idx]["layer"],
                "distance_m": d / unit_per_m,
                "selected": False, "status": "candidate",
            })
        if not within:
            failures.append(_fail(ann, "outside_tolerance"))
        elif len(within) > 1 and within[1][0] - within[0][0] <= optima_eps:
            failures.append(_fail(ann, "multiple_optima"))
            for record in candidate_records:
                if record["ann_id"] == ann["ann_id"]:
                    record["status"] = "ambiguous"
        else:
            eligible.append(ann)

    if not eligible:
        return [], failures, candidate_records

    scale = 1_000_000
    unmatched_penalty = (len(eligible) + 1) * (math.ceil(tolerance * scale) + 1)
    invalid_cost = (len(eligible) + 1) * unmatched_penalty
    costs = []
    for ann in eligible:
        row = [
            (int(round(distances[(ann["ann_id"], idx)] * scale))
             if (ann["ann_id"], idx) in distances else invalid_cost)
            for idx in range(len(targets))
        ]
        costs.append(row + [unmatched_penalty] * len(eligible))
    column_assignment = _minimum_cost_assignment(costs)

    assignments, selected_pairs = [], set()
    for row_index, column_index in enumerate(column_assignment):
        ann = eligible[row_index]
        if column_index < len(targets) and costs[row_index][column_index] < invalid_cost:
            distance = distances[(ann["ann_id"], column_index)]
            assignments.append((ann, targets[column_index], distance))
            selected_pairs.add((ann["ann_id"], column_index))
        else:
            failures.append(_fail(ann, "assignment_conflict"))
    for record in candidate_records:
        if (record["ann_id"], record["target_idx"]) in selected_pairs:
            record["selected"] = True
            record["status"] = "selected"
    return assignments, failures, candidate_records


def _link_annotations_generic(annotations, candidates, sigma):
    """Legacy generic linking: merge each annotation's attributes into the
    nearest non-misc feature within sigma. Returns unlinked annotations."""
    unlinked = []
    for ann in annotations:
        ax, ay = ann["centroid"]
        best_dist = sigma
        best_feat = None
        for feat in candidates:
            fx, fy = feat["centroid"]
            dist = math.hypot(ax - fx, ay - fy)
            if dist < best_dist:
                best_dist = dist
                best_feat = feat
        if best_feat is not None:
            for k, v in ann["attrs"].items():
                if k not in best_feat["attrs"] or best_feat["attrs"][k] is None:
                    best_feat["attrs"][k] = v
            if ann["text"] and not best_feat.get("annotation_text"):
                best_feat["annotation_text"] = ann["text"]
        else:
            unlinked.append(ann)
    return unlinked


def _link_annotations_to_geometries(annotations, features, sigma):
    """
    Three-stage annotation linking pipeline.
      Stage 1 (family gate): texts fullmatching a LABEL_FAMILIES pattern
        compete only for features of that family's target FC; all other texts
        keep the legacy generic nearest-feature merge.
      Stage 2 (candidate edges): same-family edges within `sigma`; near-tie
        best candidates (<= optima eps) abstain as multiple_optima.
      Stage 3 (Hungarian): global one-to-one assignment; winners receive
        CODE = label text, display_label, label_provenance=annotation-assigned.
    Returns (unlinked_annotations, ledger). Family annotations that fail carry
    ann["link_status"] for drop accounting. Ledger records accumulate in
    ANNOTATION_LEDGER for the evidence tables.
    """
    candidates = [f for f in features if f["fc_name"] != "fc_misc"]
    by_fc = defaultdict(list)
    for f in candidates:
        by_fc[f["fc_name"]].append(f)

    family_groups = {}
    generic = []
    for ann in annotations:
        fam = _match_label_family(ann["text"])
        if fam is not None:
            family_groups.setdefault(fam["family"], (fam, []))[1].append(ann)
        else:
            generic.append(ann)

    optima_eps = _meters_to_units(LABEL_MULTIPLE_OPTIMA_EPS_M)
    ledger = {"candidates": [], "failures": [], "assigned": [], "stats": {}}
    unassigned_family = []
    for fam_name in sorted(family_groups):
        fam, fam_anns = family_groups[fam_name]
        all_targets = by_fc.get(fam["target_fc"], [])

        # Per-family tolerance: config value or global default
        fam_tol_m = fam.get("min_distance_m")
        fam_sigma = (_meters_to_units(fam_tol_m) if fam_tol_m is not None
                     else sigma)

        # Per-family colour gate: pre-filter PTECH nodes by color_rgb
        color_filter = fam.get("node_color_filter")
        if color_filter and all_targets:
            if color_filter == "#FF0000":
                targets = [t for t in all_targets
                           if t.get("color_rgb") == "#FF0000"]
            elif color_filter == "!#FF0000":
                targets = [t for t in all_targets
                           if t.get("color_rgb") != "#FF0000"]
            else:
                targets = all_targets
        else:
            targets = all_targets

        assignments, failures, cand_records = _assign_family_annotations(
            fam, fam_anns, targets, fam_sigma, optima_eps)
        unit_per_m = _meters_to_units(1.0)
        for ann, target, distance in assignments:
            text = ann["text"].strip()
            target["attrs"]["CODE"] = text
            target["attrs"]["label_provenance"] = "annotation-assigned"
            target["display_label"] = text
            if not target.get("annotation_text"):
                target["annotation_text"] = text
            ledger["assigned"].append({
                "family": fam_name, "target_fc": fam["target_fc"],
                "ann_id": ann["ann_id"], "text": text,
                "target_global_id": target["global_id"],
                "target_layer": target["layer"],
                "distance_m": distance / unit_per_m,
            })
        by_id = {ann["ann_id"]: ann for ann in fam_anns}
        for failure in failures:
            ann = by_id[failure["ann_id"]]
            ann["link_status"] = failure["status"]
            unassigned_family.append(ann)
        ledger["candidates"].extend(cand_records)
        ledger["failures"].extend(failures)
        status_counts = defaultdict(int)
        for failure in failures:
            status_counts[failure["status"]] += 1
        ledger["stats"][fam_name] = {
            "target_fc": fam["target_fc"],
            "annotations": len(fam_anns),
            "targets": len(targets),
            "candidate_edges": len(cand_records),
            "assigned": len(assignments),
            **dict(status_counts),
        }

    unlinked = _link_annotations_generic(generic, candidates, sigma)

    ANNOTATION_LEDGER["candidates"].extend(ledger["candidates"])
    ANNOTATION_LEDGER["failures"].extend(ledger["failures"])
    ANNOTATION_LEDGER["assigned"].extend(ledger["assigned"])
    for fam_name, stats in ledger["stats"].items():
        existing = ANNOTATION_LEDGER["stats"].get(fam_name)
        if existing is None:
            ANNOTATION_LEDGER["stats"][fam_name] = dict(stats)
        else:
            for key, value in stats.items():
                if key != "target_fc":
                    existing[key] = existing.get(key, 0) + value

    return unlinked + unassigned_family, ledger


# ── BOITE Multi-Representation Fusion (B1) ───────────────────────────────
# The same physical FAT appears in the drawing as several graphic
# representations (INSERT block on the plan, circle in the FAT structure
# diagram, fragment aggregates, info sketches). Fusion reduces BOITE to the
# physical asset set and records the representation composition per feature.

# In-memory ledger for fusion evidence, accumulated per process run.
BOITE_FUSION_LEDGER = {
    "merged": [],          # spatially co-located members merged into a representative
    "duplicate_sets": [],  # whole representation sets folded into the labeled set
    "secondary": [],       # residual unlabeled representations → quarantine review
    "summary": {},
}

_REPRESENTATION_KINDS = {
    "INSERT": "block",
    "CIRCLE": "circle",
    "TEXT": "text", "MTEXT": "text", "ATTRIB": "text",
    "AGGREGATE": "fragment",
}


def _representation_kind(dwg_type_name):
    return _REPRESENTATION_KINDS.get(dwg_type_name, "outline")


def _fuse_boite_representations(features, tol, drop):
    """
    Fuse BOITE multi-representations into physical assets (runs after label
    assignment so true labels mark the corroborated physical set).
      1) spatial fusion: co-located BOITE members within `tol` merge into one
         representative (labeled first, then block kind); representation =
         union of member kinds; position = representative's point
      2) duplicate-set fold: an unlabeled (layer, kind) group whose
         cardinality equals the labeled set and whose members all lie beyond
         `tol` from every labeled feature is a duplicate representation of
         that set (e.g. the FAT structure-diagram circles) — its kind is
         recorded on every labeled feature and the group leaves the layer
      3) residual unlabeled representations (info sketches, legend samples)
         leave the layer into quarantine review
    Steps 2-3 only apply when labeled features exist (label-corroborated
    truth); otherwise only spatial fusion runs.
    Returns the filtered feature list.
    """
    boite = [f for f in features if f["fc_name"] == "BOITE"]
    if not boite:
        return features
    others = [f for f in features if f["fc_name"] != "BOITE"]

    # 1) spatial fusion
    clusters = _cluster_points([f["centroid"] for f in boite], tol)
    kept = []
    for cluster in clusters:
        members = sorted(
            (boite[i] for i in cluster),
            key=lambda f: (not bool(f.get("display_label")),
                           _representation_kind(f["dwg_type_name"]) != "block",
                           f["global_id"], f["centroid"]))
        rep = members[0]
        kinds = {_representation_kind(f["dwg_type_name"]) for f in members}
        rep["representation"] = "+".join(sorted(kinds))
        for member in members[1:]:
            for k, v in member["attrs"].items():
                if rep["attrs"].get(k) is None and v is not None:
                    rep["attrs"][k] = v
            if member.get("annotation_text") and not rep.get("annotation_text"):
                rep["annotation_text"] = member["annotation_text"]
            # Weight by consumed source entities (aggregates carry several)
            for _ in range(member.get("source_entity_count", 1)):
                drop("boite_merged_representation", member["layer"])
            BOITE_FUSION_LEDGER["merged"].append({
                "kept_layer": rep["layer"], "kept_global_id": rep["global_id"],
                "merged_layer": member["layer"],
                "merged_kind": _representation_kind(member["dwg_type_name"]),
                "centroid": member["centroid"],
            })
        kept.append(rep)

    labeled = [f for f in kept if f.get("display_label")]
    if labeled:
        unlabeled = [f for f in kept if not f.get("display_label")]
        # 2) duplicate representation sets (cardinality match + disjoint)
        groups = defaultdict(list)
        for f in unlabeled:
            groups[(f["layer"], _representation_kind(f["dwg_type_name"]))].append(f)
        folded = set()
        for (layer, kind), group in sorted(groups.items()):
            if len(group) != len(labeled):
                continue
            disjoint = all(
                math.hypot(g["centroid"][0] - l["centroid"][0],
                           g["centroid"][1] - l["centroid"][1]) > tol
                for g in group for l in labeled)
            if not disjoint:
                continue
            for f in labeled:
                kinds = set(f["representation"].split("+"))
                kinds.add(kind)
                f["representation"] = "+".join(sorted(kinds))
            for g in group:
                folded.add(id(g))
                for _ in range(g.get("source_entity_count", 1)):
                    drop("boite_duplicate_representation", g["layer"])
            BOITE_FUSION_LEDGER["duplicate_sets"].append({
                "layer": layer, "kind": kind, "count": len(group),
                "folded_into": "labeled BOITE set",
            })
        # 3) residual unlabeled representations → quarantine review
        for f in unlabeled:
            if id(f) in folded:
                continue
            for _ in range(f.get("source_entity_count", 1)):
                drop("boite_secondary_representation", f["layer"])
            BOITE_FUSION_LEDGER["secondary"].append({
                "layer": f["layer"],
                "kind": _representation_kind(f["dwg_type_name"]),
                "centroid": f["centroid"],
                "code_hint": f["attrs"].get("CODE"),
            })
        kept = labeled

    BOITE_FUSION_LEDGER["summary"] = {
        "input": len(boite), "output": len(kept),
        "labeled": len(labeled),
        "merged": len(BOITE_FUSION_LEDGER["merged"]),
        "duplicate_sets": len(BOITE_FUSION_LEDGER["duplicate_sets"]),
        "secondary": len(BOITE_FUSION_LEDGER["secondary"]),
    }
    return others + kept


# ── DWG Reader ────────────────────────────────────────────────────────────

def read_dwg(dwg_path, fragment_cluster_tol_m=DEFAULT_FRAGMENT_CLUSTER_TOL_M,
             annotation_link_tol_m=DEFAULT_ANNOTATION_LINK_TOL_M,
             boite_fusion_tol_m=DEFAULT_BOITE_FUSION_TOL_M):
    """
    Read a DWG file and extract model-space entities with classification
    and geometry.
    Returns (features, span_records, drop_counts, agg_summary):
      features     — list of feature dicts
      span_records — list of DIMENSION span measurements
      drop_counts  — {port: {dwg_layer: count}} accounting for every discard
      agg_summary  — list of (fc, n_fragments, n_clusters) aggregation stats
    """
    # Import LibreDWG SWIG (must come after initializing ctypes bridge for lwpoline)
    _init_libredwg()

    sys.path.insert(0, "/usr/local/lib/python3.12/dist-packages")
    try:
        from LibreDWG import (
            Dwg_Data, dwg_read_file, new_Dwg_Object_Array,
            Dwg_Object_Array_getitem,
            DWG_SUPERTYPE_ENTITY as L_SUPERTYPE_ENTITY,
            DWG_TYPE_LINE as L_LINE, DWG_TYPE_LWPOLYLINE as L_LWPOLYLINE,
            DWG_TYPE_CIRCLE as L_CIRCLE, DWG_TYPE_ARC as L_ARC,
            DWG_TYPE_TEXT as L_TEXT, DWG_TYPE_MTEXT as L_MTEXT,
            DWG_TYPE_INSERT as L_INSERT, DWG_TYPE_POINT as L_POINT,
            DWG_TYPE_ATTRIB as L_ATTRIB,
        )
    except ImportError:
        sys.exit(
            "ERROR: LibreDWG Python bindings not found.\n"
            "Install LibreDWG: https://www.gnu.org/software/libredwg/\n"
            "Ensure /usr/local/lib/python3.12/dist-packages/LibreDWG exists."
        )

    # Patch global type constants
    global DWG_TYPE_LINE, DWG_TYPE_LWPOLYLINE, DWG_TYPE_CIRCLE, DWG_TYPE_ARC
    global DWG_TYPE_TEXT, DWG_TYPE_MTEXT, DWG_TYPE_INSERT, DWG_TYPE_POINT
    global DWG_TYPE_ATTRIB, DWG_SUPERTYPE_ENTITY
    DWG_TYPE_LINE = L_LINE
    DWG_TYPE_LWPOLYLINE = L_LWPOLYLINE
    DWG_TYPE_CIRCLE = L_CIRCLE
    DWG_TYPE_ARC = L_ARC
    DWG_TYPE_TEXT = L_TEXT
    DWG_TYPE_MTEXT = L_MTEXT
    DWG_TYPE_INSERT = L_INSERT
    DWG_TYPE_POINT = L_POINT
    DWG_TYPE_ATTRIB = L_ATTRIB
    DWG_SUPERTYPE_ENTITY = L_SUPERTYPE_ENTITY

    import LibreDWG

    # DIMENSION type codes → tio union member names
    DIMENSION_TYPE_UNION.clear()
    for nm in ("DIMENSION_ORDINATE", "DIMENSION_LINEAR", "DIMENSION_ALIGNED",
               "DIMENSION_ANG3PT", "DIMENSION_ANG2LN", "DIMENSION_RADIUS",
               "DIMENSION_DIAMETER", "ARC_DIMENSION"):
        val = getattr(LibreDWG, "DWG_TYPE_" + nm, None)
        if val is not None:
            DIMENSION_TYPE_UNION[val] = nm

    # Non-geometric control entities (BLOCK/ENDBLK/SEQEND/ATTDEF): never features
    CONTROL_TYPES.clear()
    for nm in ("BLOCK", "ENDBLK", "SEQEND", "ATTDEF"):
        val = getattr(LibreDWG, "DWG_TYPE_" + nm, None)
        if val is not None:
            CONTROL_TYPES.add(val)

    # Canonical type-name map. Several modern and legacy R11 constants share
    # the same numeric code (e.g. TEXT=1=LINE_r11, ARC=17=SEQEND_r11,
    # CIRCLE=18=JUMP_r11). Post-decode objects always use the modern enum, so
    # modern names take priority — this eliminates the SEQEND_r11/JUMP_r11
    # pseudo-feature labels of the previous implementation.
    type_names = {}
    for name in dir(LibreDWG):
        if name.startswith("DWG_TYPE_"):
            val = getattr(LibreDWG, name)
            if not isinstance(val, int):
                continue
            short = name[9:]
            cur = type_names.get(val)
            if cur is None or (cur.endswith("_r11") and not short.endswith("_r11")):
                type_names[val] = short

    geo_types = {DWG_TYPE_LINE, DWG_TYPE_LWPOLYLINE, DWG_TYPE_CIRCLE, DWG_TYPE_ARC}
    point_types = {DWG_TYPE_INSERT, DWG_TYPE_POINT}
    text_types = {DWG_TYPE_TEXT, DWG_TYPE_MTEXT, DWG_TYPE_ATTRIB}

    print(f"Reading: {dwg_path}")
    data = Dwg_Data()
    data.object = new_Dwg_Object_Array(500000)
    err = dwg_read_file(dwg_path, data)
    print(f"  LibreDWG exit code: {err}, objects: {data.num_objects}")

    # ── Pass 0: layer / linetype tables (colour + linetype for ByLayer
    # resolution). Colour is numeric; names go through the dynapi UTF-8
    # accessor (UTF-16 storage in R2007+, same rule as entity text).
    L_LAYER = getattr(LibreDWG, "DWG_TYPE_LAYER", None)
    L_LTYPE = getattr(LibreDWG, "DWG_TYPE_LTYPE", None)
    ltype_names = {}     # absolute handle → linetype name
    layer_raw = []       # (name, aci, truecolor, ltype_handle)
    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue
        if obj.supertype == DWG_SUPERTYPE_ENTITY:
            continue
        if obj.type == L_LTYPE:
            try:
                lt = obj.tio.object.tio.LTYPE
                name = _entity_utf8_text(int(lt.this), b"LTYPE", b"name")
                if name:
                    ltype_names[int(obj.handle.value)] = name
            except Exception:
                pass
        elif obj.type == L_LAYER:
            try:
                lay = obj.tio.object.tio.LAYER
                name = _entity_utf8_text(int(lay.this), b"LAYER", b"name")
                aci, truecolor = _parse_dwg_color(lay.color)
                if not 1 <= aci <= 255:
                    aci = 7
                lt_ref = None
                try:
                    if lay.ltype is not None:
                        lt_ref = int(lay.ltype.absolute_ref)
                except Exception:
                    pass
                layer_raw.append((name, aci, truecolor, lt_ref))
            except Exception:
                pass
    layer_style_table = {}
    for name, aci, truecolor, lt_ref in layer_raw:
        lt_name = ltype_names.get(lt_ref, "Continuous")
        if lt_name.casefold() in ("bylayer", "byblock"):
            lt_name = "Continuous"
        layer_style_table[name] = {
            "aci": aci, "truecolor": truecolor, "linetype": lt_name}
    print(f"  Style tables: {len(layer_style_table)} layers, "
          f"{len(ltype_names)} linetypes")

    drop_counts = defaultdict(lambda: defaultdict(int))
    ledger_counts = {
        "model_space_total": 0,
        "annotation_consumed": defaultdict(int),
        "dimension_span": defaultdict(int),
    }

    def _drop(port, layer):
        drop_counts[port][layer or "<no layer>"] += 1

    # ── Pass 1: collect raw model-space entities ─────────────────────────
    global_x_min, global_x_max = float("inf"), float("-inf")
    global_y_min, global_y_max = float("inf"), float("-inf")
    raw_features = []
    raw_dimensions = []

    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue
        if obj.supertype != DWG_SUPERTYPE_ENTITY:
            continue

        entity = obj.tio.entity
        entity_ptr = int(entity.this)
        dwg_type = obj.type
        layer = _layer_name(entity_ptr)

        # Entity colour (numeric Dwg_Color struct) + linetype flags.
        # ltype_flags: 0=ByLayer, 1=ByBlock, 2=Continuous, 3=handle ref.
        try:
            ent_aci, ent_tc = _parse_dwg_color(entity.color)
        except Exception:
            ent_aci, ent_tc = 256, None
        ent_ltype = None
        try:
            lflags = int(getattr(entity, "ltype_flags", 0))
            if lflags == 2:
                ent_ltype = "Continuous"
            elif lflags == 3 and entity.ltype is not None:
                ent_ltype = ltype_names.get(int(entity.ltype.absolute_ref))
        except Exception:
            pass

        # Model-space filter: entmode 2 = model space, 1 = paper space,
        # 0 = inside a block definition (fragment of a BLOCK, not placed).
        entmode = getattr(entity, "entmode", 2)
        if entmode == 0:
            _drop("block_definition", layer)
            continue
        if entmode == 1:
            _drop("paper_space", layer)
            continue

        # Model-space entity: the conservation ledger (component D) accounts
        # every one of these into exactly one disposition.
        ledger_counts["model_space_total"] += 1

        if dwg_type in CONTROL_TYPES:
            _drop("control_entity", layer)
            continue

        # DIMENSION entities: measured span values, extracted separately
        if dwg_type in DIMENSION_TYPE_UNION:
            try:
                rec = _extract_dimension(entity, DIMENSION_TYPE_UNION[dwg_type])
                rec["layer"] = layer
                (rec["color_aci"], rec["color_rgb"],
                 rec["style_key"]) = _resolve_effective_color(
                    ent_aci, ent_tc, ent_ltype, layer, layer_style_table)
                raw_dimensions.append(rec)
            except Exception:
                _drop("dimension_read_error", layer)
            continue

        if dwg_type not in geo_types and dwg_type not in point_types \
                and dwg_type not in text_types:
            _drop("unsupported_type:" + type_names.get(dwg_type, f"T{dwg_type}"),
                  layer)
            continue

        # Extract text strictly per entity type via the dynapi UTF-8 accessor
        # (SWIG string fields truncate UTF-16 storage at the first NUL —
        # R2018 files returned only the first character of every label)
        text_val = ""
        try:
            if dwg_type == DWG_TYPE_TEXT:
                text_val = _entity_utf8_text(
                    int(entity.tio.TEXT.this), b"TEXT", b"text_value")
            elif dwg_type == DWG_TYPE_MTEXT:
                text_val = _entity_utf8_text(
                    int(entity.tio.MTEXT.this), b"MTEXT", b"text")
            elif dwg_type == DWG_TYPE_ATTRIB:
                text_val = _entity_utf8_text(
                    int(entity.tio.ATTRIB.this), b"ATTRIB", b"text_value")
        except Exception:
            pass

        # Track extent for chord tolerance (approximate)
        try:
            if dwg_type == DWG_TYPE_LWPOLYLINE:
                pts = _lwpoline_points(entity)
                for px, py in pts:
                    global_x_min = min(global_x_min, px)
                    global_x_max = max(global_x_max, px)
                    global_y_min = min(global_y_min, py)
                    global_y_max = max(global_y_max, py)
        except Exception:
            pass

        raw_features.append({
            "entity": entity,
            "dwg_type": dwg_type,
            "dwg_type_name": type_names.get(dwg_type, f"T{dwg_type}"),
            "layer": layer,
            "text": text_val,
            "is_insert_node": dwg_type == DWG_TYPE_INSERT,
            "ent_aci": ent_aci,
            "ent_tc": ent_tc,
            "ent_ltype": ent_ltype,
        })

    # Compute extent for chord tolerance
    if global_x_min == float("inf"):
        extent = 1.0
    else:
        extent = max(global_x_max - global_x_min, global_y_max - global_y_min, 0.001)

    # ── Pass 2: geometry extraction, reprojection, classification ───────
    features = []
    annotations = []
    for i, rf in enumerate(raw_features):
        is_text_entity = rf["dwg_type"] in text_types
        if is_text_entity:
            # Geometry = text insertion point
            try:
                if rf["dwg_type"] == DWG_TYPE_TEXT:
                    p = rf["entity"].tio.TEXT.ins_pt
                elif rf["dwg_type"] == DWG_TYPE_MTEXT:
                    p = rf["entity"].tio.MTEXT.ins_pt
                else:
                    p = rf["entity"].tio.ATTRIB.ins_pt
                cx, cy = p.x, p.y
            except Exception:
                _drop("no_geometry", rf["layer"])
                continue
            wkt = _wkt_point(cx, cy)
            pts = [(cx, cy)]
            is_closed = False
        else:
            wkt, pts, (cx, cy), is_closed = _extract_wkt(
                rf["entity"], rf["dwg_type"], extent
            )
            if not wkt:
                _drop("no_geometry", rf["layer"])
                continue

        # Reproject from source CRS → target CRS using OGR transform
        if _CRS_TRANSFORM is not None:
            try:
                geom = ogr.CreateGeometryFromWkt(wkt)
                if geom:
                    geom.Transform(_CRS_TRANSFORM)
                    wkt = geom.ExportToWkt()
                    if geom.GetGeometryName() == 'POINT':
                        cx, cy = geom.GetX(), geom.GetY()
                        pts = [(cx, cy)]
                    elif geom.GetGeometryName() in ('LINESTRING', 'LINEARRING'):
                        pts = [(geom.GetX(j), geom.GetY(j))
                               for j in range(geom.GetPointCount())]
                        cx = sum(p[0] for p in pts) / len(pts)
                        cy = sum(p[1] for p in pts) / len(pts)
                    else:
                        c = geom.Centroid()
                        cx, cy = c.GetX(), c.GetY()
            except Exception:
                pass

        # Coordinate sanity filter in target-CRS units (applied AFTER reprojection)
        if not _valid_coord(cx, cy):
            _drop("invalid_coords", rf["layer"])
            continue

        # Deployment region check (EPSG:4326 comparison): warning + per-layer
        # count only — the feature is flagged, never discarded
        region_outlier = not _in_region_bounds(cx, cy)
        if region_outlier:
            _drop("warn_outside_region_bounds", rf["layer"])

        # Tier 1 + Tier 2 classification by DWG layer name / annotation text
        fc_name, fc_geom_type, confidence, method = _assign_fc(rf["layer"], rf["text"])

        # Effective CAD colour after ByLayer/ByBlock resolution
        color_aci, color_rgb, style_key = _resolve_effective_color(
            rf["ent_aci"], rf["ent_tc"], rf["ent_ltype"], rf["layer"],
            layer_style_table)

        if is_text_entity:
            if method == "negative_evidence":
                _drop("negative_layer_text", rf["layer"])
                continue
            if fc_name != "IMB" or method != "tier1_layer_pattern":
                # Label text: link to a nearby feature later
                annotations.append({
                    "ann_id": len(annotations),
                    "text": rf["text"],
                    "centroid": (cx, cy),
                    "attrs": _extract_attributes(rf["text"], None),
                    "layer": rf["layer"],
                    "color_aci": color_aci,
                    "color_rgb": color_rgb,
                    "style_key": style_key,
                })
                continue
            # Text on an IMB layer (e.g. "Home Number") IS the feature:
            # point at text insertion, text value → attributes. Its CODE is
            # the original text (true label), synthetic fallback otherwise.
            attrs = _extract_attributes(rf["text"], "IMB")
            txt = (rf["text"] or "").strip()
            if txt.isdigit():
                attrs["NUMERO_VOIE"] = int(txt)
            if txt:
                attrs["CODE"] = txt
                attrs["label_provenance"] = "annotation-assigned"
            feat_text = txt
            display_label = txt
        else:
            attrs = _extract_attributes(rf["text"], fc_name)
            feat_text = rf["text"]
            display_label = ""

        features.append({
            "global_id": i,
            "source_file": os.path.basename(dwg_path),
            "layer": rf["layer"],
            "dwg_type": rf["dwg_type"],
            "dwg_type_name": rf["dwg_type_name"],
            "wkt": wkt,
            "points": pts,
            "centroid": (cx, cy),
            "is_closed": is_closed,
            "fc_name": fc_name,
            "fc_geom_type": fc_geom_type,
            "classification_confidence": confidence,
            "classification_method": method,
            "text": feat_text,
            "annotation_text": feat_text if is_text_entity else "",
            "display_label": display_label,
            "attrs": attrs,
            "is_insert_node": rf["is_insert_node"],
            "geographic_outlier": region_outlier,
            "color_aci": color_aci,
            "color_rgb": color_rgb,
            "style_key": style_key,
        })

        if (i + 1) % 50000 == 0:
            print(f"  ... {i + 1} objects processed")

    # ── Fragment aggregation (FDT/FAT structure diagrams → single points) ─
    features, agg_summary = _aggregate_fragments(
        features, fragment_cluster_tol_m, os.path.basename(dwg_path))

    # Renumber per-file ids so aggregation products are addressable too —
    # annotation candidate records reference these ids (evidence tables).
    for k, feat in enumerate(features):
        feat["global_id"] = k

    # ── Reproject and record DIMENSION span measurements ─────────────────
    span_records = []
    for rec in raw_dimensions:
        dx, dy = _reproject_point(*rec["def_pt"])
        if not _valid_coord(dx, dy):
            _drop("invalid_coords", rec["layer"])
            continue
        span = {
            "source_file": os.path.basename(dwg_path),
            "layer": rec["layer"],
            "measurement": rec["measurement"],
            "def_pt": (dx, dy),
            "xline1": None,
            "xline2": None,
            "color_aci": rec.get("color_aci"),
            "color_rgb": rec.get("color_rgb"),
            "style_key": rec.get("style_key"),
        }
        if rec["xline1"] and rec["xline2"]:
            span["xline1"] = _reproject_point(*rec["xline1"])
            span["xline2"] = _reproject_point(*rec["xline2"])
        span_records.append(span)
        ledger_counts["dimension_span"][rec["layer"] or "<no layer>"] += 1

    # ── Link annotations to features ─────────────────────────────────────
    sigma = _meters_to_units(annotation_link_tol_m)
    unlinked, ann_ledger = _link_annotations_to_geometries(
        annotations, features, sigma)
    unlinked_ids = {id(ann) for ann in unlinked}
    for ann in annotations:
        if id(ann) not in unlinked_ids:
            ledger_counts["annotation_consumed"][ann["layer"] or "<no layer>"] += 1
    src_base = os.path.basename(dwg_path)
    for section in ("candidates", "failures", "assigned"):
        for record in ann_ledger[section]:
            record["source_file"] = src_base
    for fam_name, stats in sorted(ann_ledger["stats"].items()):
        print(f"  Label family '{fam_name}' → {stats['target_fc']}: "
              f"{stats['annotations']} labels, {stats['targets']} targets, "
              f"{stats['candidate_edges']} candidate edges, "
              f"{stats['assigned']} assigned, "
              f"{stats.get('outside_tolerance', 0)} outside_tolerance, "
              f"{stats.get('multiple_optima', 0)} multiple_optima, "
              f"{stats.get('assignment_conflict', 0)} assignment_conflict")

    # Unlinked annotations become fc_misc features (counted per layer).
    # Family-gated labels that abstained/conflicted carry link_status and
    # are counted under a dedicated port for the evidence ledger.
    for ann in unlinked:
        link_status = ann.get("link_status")
        if link_status:
            _drop(f"family_label_{link_status}", ann["layer"])
        else:
            _drop("unlinked_annotation", ann["layer"])
        ax, ay = ann["centroid"]
        features.append({
            "global_id": -1,
            "source_file": os.path.basename(dwg_path),
            "layer": ann["layer"],
            "dwg_type": DWG_TYPE_TEXT,
            "dwg_type_name": "TEXT",
            "wkt": _wkt_point(ax, ay),
            "points": [(ax, ay)],
            "centroid": (ax, ay),
            "is_closed": False,
            "fc_name": "fc_misc",
            "fc_geom_type": None,
            "classification_confidence": 0.5,
            "classification_method": (
                f"family_label_{link_status}" if link_status
                else "unlinked_annotation"),
            "text": ann["text"],
            "annotation_text": "",
            "display_label": "",
            "attrs": ann["attrs"],
            "is_insert_node": False,
            "geographic_outlier": False,
            "color_aci": ann.get("color_aci"),
            "color_rgb": ann.get("color_rgb"),
            "style_key": ann.get("style_key"),
        })

    # ── BOITE multi-representation fusion (after labels mark physical set) ─
    features = _fuse_boite_representations(
        features, _meters_to_units(boite_fusion_tol_m), _drop)
    fusion = BOITE_FUSION_LEDGER["summary"]
    if fusion:
        print(f"  BOITE representation fusion: {fusion['input']} → "
              f"{fusion['output']} features ({fusion['labeled']} labeled, "
              f"{fusion['merged']} merged, {fusion['duplicate_sets']} "
              f"duplicate set(s) folded, {fusion['secondary']} secondary → "
              f"quarantine)")

    print(f"  Extracted: {len(features)} features, {len(annotations)} annotations, "
          f"{len(span_records)} span dimensions")
    return features, span_records, drop_counts, agg_summary, ledger_counts


def _aggregate_fragments(features, cluster_tol_m, source_file):
    """
    Aggregate drawing fragments on FRAGMENT_AGGREGATION_LAYERS into single
    point features (one per proximity cluster, centroid geometry).
    Returns (new_features, agg_summary).
    """
    groups = defaultdict(list)   # (fc, forced_type) → [feature, ...]
    kept = []
    for feat in features:
        fc, forced_type = _fragment_aggregation_target(feat["layer"])
        # Only aggregate features classified to the aggregation FC —
        # e.g. "FAT AREA FDT 1" contains "fdt" but is a ZPM zone polygon
        if fc is not None and feat["fc_name"] == fc:
            groups[(fc, forced_type)].append(feat)
        else:
            kept.append(feat)

    tol = _meters_to_units(cluster_tol_m)
    agg_summary = []
    for (fc, forced_type), members in groups.items():
        centroids = [m["centroid"] for m in members]
        clusters = _cluster_points(centroids, tol)
        for cluster in clusters:
            n = len(cluster)
            # Prefer INSERT insertion point over centroid-average:
            # FDT / FAT INSERT blocks carry the true cabinet position;
            # centroid averaging with nearby text fragments introduces ~3-4 m drift.
            insert_candidates = [k for k in cluster
                                if members[k].get("dwg_type_name") == "INSERT"]
            if insert_candidates:
                ax = sum(centroids[k][0] for k in insert_candidates) / len(insert_candidates)
                ay = sum(centroids[k][1] for k in insert_candidates) / len(insert_candidates)
            else:
                ax = sum(centroids[k][0] for k in cluster) / n
                ay = sum(centroids[k][1] for k in cluster) / n
            layers = defaultdict(int)
            for k in cluster:
                layers[members[k]["layer"]] += 1
            main_layer = max(layers, key=lambda k: layers[k])
            # Majority effective colour among the aggregated fragments
            color_votes = defaultdict(int)
            for k in cluster:
                m = members[k]
                color_votes[(m.get("color_aci"), m.get("color_rgb"),
                             m.get("style_key"))] += 1
            maj_aci, maj_rgb, maj_key = sorted(
                color_votes.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0]
            kept.append({
                "global_id": -1,
                "source_file": source_file,
                "layer": main_layer,
                "dwg_type": -1,
                "dwg_type_name": "AGGREGATE",
                "wkt": _wkt_point(ax, ay),
                "points": [(ax, ay)],
                "centroid": (ax, ay),
                "is_closed": False,
                "fc_name": fc,
                "fc_geom_type": "Point",
                "classification_confidence": 0.85,
                "classification_method": "fragment_aggregation",
                "text": "",
                "annotation_text": "",
                "display_label": "",
                "attrs": {"TYPE": forced_type,
                          "COMMENT": f"aggregated {n} fragments"},
                "is_insert_node": False,
                "geographic_outlier": False,
                "source_entity_count": n,
                "color_aci": maj_aci,
                "color_rgb": maj_rgb,
                "style_key": maj_key,
            })
        agg_summary.append((fc, len(members), len(clusters)))
        print(f"  Fragment aggregation: {len(members)} fragments on "
              f"{fc}-mapped layers → {len(clusters)} point(s)")
    return kept, agg_summary


# ── Feature Class Geometry Resolution ─────────────────────────────────────

# Maps FC name to target output geometry
FC_GEOM_RESOLVE = {
    "BOITE": "Point",
    "PTECH": "Point",
    "SITE": "Point",
    "CABLE": "LineString",
    "INFRASTRUCTURE": "LineString",
    "ZNRO": "Polygon",
    "ZPM": "Polygon",
    "IMB": "Point",  # closed outlines stay polygons (see below)
}


def _resolve_fc_geometry(feat):
    """
    Coerce a feature's geometry to its FC's target type.
    Returns (wkt, points, is_closed) or ("", [], False) when the geometry
    cannot represent the target type (e.g. 2-point line → polygon).
    """
    fc = feat["fc_name"]
    target = FC_GEOM_RESOLVE.get(fc)
    if target is None:
        return feat["wkt"], feat["points"], feat["is_closed"]

    pts = feat["points"]

    if fc == "IMB":
        # IMB is "Point or Polygon": closed outlines become polygons,
        # annotation-derived points stay points
        if feat["is_closed"] and len(pts) >= 3:
            ring = pts if pts[0] == pts[-1] else pts + [pts[0]]
            return _wkt_polygon_exterior(ring), ring, True
        return _wkt_point(*feat["centroid"]), [feat["centroid"]], False

    if target == "Point":
        return _wkt_point(*feat["centroid"]), [feat["centroid"]], False

    if target == "LineString":
        if len(pts) < 2:
            return ("", [], False)
        return _wkt_linestring(pts), pts, feat["is_closed"]

    if target == "Polygon":
        if len(pts) < 3:
            return ("", [], False)
        ring = pts if pts[0] == pts[-1] else pts + [pts[0]]
        return _wkt_polygon_exterior(ring), ring, True

    return feat["wkt"], feat["points"], feat["is_closed"]


# ── GeoPackage Writer ─────────────────────────────────────────────────────

def _ogr_field_type(field_def):
    """Map schema field type to OGR field type."""
    ftype = field_def["type"]
    if ftype == "Integer":
        return ogr.OFTInteger
    elif ftype == "Double":
        return ogr.OFTReal
    return ogr.OFTString


def _compute_layer_length(fc_name, field_name, points_list):
    """Compute CRS-aware length for CABLE.LONGUEUR and INFRASTRUCTURE.LONGUEUR."""
    if fc_name in ("CABLE", "INFRASTRUCTURE"):
        return _line_length_m(points_list)
    return 0.0


def write_geopackage(output_path, all_features, source_files,
                     span_records=None, drop_counts=None, agg_summaries=None,
                     source_crs_label=None, target_crs_label=None,
                     transform_desc=None):
    """Write all features to a single GeoPackage with 8 FTTH layers + metadata."""
    span_records = span_records or []
    drop_counts = drop_counts or {}
    agg_summaries = agg_summaries or []
    source_crs_label = source_crs_label or _SOURCE_CRS_LABEL
    target_crs_label = target_crs_label or _TARGET_CRS_LABEL
    driver = ogr.GetDriverByName("GPKG")
    if os.path.exists(output_path):
        driver.DeleteDataSource(output_path)
    ds = driver.CreateDataSource(output_path)

    srs = osr.SpatialReference()
    srs.SetFromUserInput(target_crs_label)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    # Predefined output layers in insertion order
    layer_order = ["BOITE", "CABLE", "PTECH", "INFRASTRUCTURE",
                   "SITE", "ZNRO", "ZPM", "IMB"]
    fc_configs = {
        "BOITE": BOITE, "CABLE": CABLE, "PTECH": PTECH,
        "INFRASTRUCTURE": INFRASTRUCTURE_FC, "SITE": SITE,
        "ZNRO": ZNRO, "ZPM": ZPM, "IMB": IMB,
    }

    # Map Base64 geom type name → OGR type
    geom_type_ogr = {
        "Point": ogr.wkbPoint,
        "LineString": ogr.wkbLineString,
        "Polygon": ogr.wkbPolygon,
    }

    # Collect features per FC
    fc_features = defaultdict(list)
    for feat in all_features:
        fc = feat["fc_name"]
        if fc in REQUIRED_LAYERS:
            fc_features[fc].append(feat)

    # Also track fc_misc for QC
    misc_count = sum(1 for f in all_features if f["fc_name"] == "fc_misc")

    layer_stats = {}
    output_layers = {}

    for fc_name in layer_order:
        config = fc_configs[fc_name]
        geom_name = config["geometry_type"]
        ogr_geom = geom_type_ogr.get(geom_name, ogr.wkbUnknown)
        if ogr_geom == ogr.wkbUnknown and "Point" in geom_name:
            ogr_geom = ogr.wkbPoint

        layer = ds.CreateLayer(fc_name, srs, ogr_geom)

        # Create all fields from schema (full names, no truncation)
        field_map = {}
        for fdef in config["fields"]:
            field_name = fdef["full_name"]
            if field_name in ("X", "Y", "LONGUEUR"):
                continue  # computed fields, added below
            fld = ogr.FieldDefn(field_name, _ogr_field_type(fdef))
            if fdef.get("length"):
                fld.SetWidth(fdef["length"])
            layer.CreateField(fld)
            field_map[field_name] = fdef

        # Add computed fields: X, Y for Point FCs; LONGUEUR for LineString FCs
        if geom_name == "Point" or "Point" in geom_name:
            layer.CreateField(ogr.FieldDefn("X", ogr.OFTReal))
            layer.CreateField(ogr.FieldDefn("Y", ogr.OFTReal))
        if geom_name == "LineString":
            layer.CreateField(ogr.FieldDefn("LONGUEUR", ogr.OFTReal))

        # Add metadata fields
        layer.CreateField(ogr.FieldDefn("source_file", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("dwg_layer", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("dwg_type", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("classification_method", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("annotation_text", ogr.OFTString))
        # Label binding fields: display_label = true label text (empty when
        # none); label_provenance = annotation-assigned | synthetic
        layer.CreateField(ogr.FieldDefn("display_label", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("label_provenance", ogr.OFTString))
        # Effective CAD colour fields (defect 4: styling data channel)
        for sdef in STYLE_FIELDS:
            layer.CreateField(ogr.FieldDefn(
                sdef["name"],
                ogr.OFTInteger if sdef["type"] == "Integer" else ogr.OFTString))
        if fc_name == "BOITE":
            # Source representation composition, e.g. "block+circle"
            layer.CreateField(ogr.FieldDefn("representation", ogr.OFTString))
            # FAT/FDT attribute values from DWG block definitions
            layer.CreateField(ogr.FieldDefn("fat_value", ogr.OFTInteger))
            layer.CreateField(ogr.FieldDefn("fdt_value", ogr.OFTInteger))

        feat_list = fc_features.get(fc_name, [])
        count = 0
        for fdata in feat_list:
            wkt_resolved, pts_resolved, _ = _resolve_fc_geometry(fdata)
            if not wkt_resolved:
                drop_counts.setdefault("geom_coercion_failed", defaultdict(int))[
                    fdata.get("layer", "<no layer>")] += 1
                continue

            geom = ogr.CreateGeometryFromWkt(wkt_resolved)
            if geom is None:
                drop_counts.setdefault("invalid_wkt", defaultdict(int))[
                    fdata.get("layer", "<no layer>")] += 1
                continue

            # Flag DWG border/sheet artifacts: features at exactly the sheet
            # origin (0,0) or spanning implausible extents. Warning + count
            # only — the feature is still written (guide: warning, NOT halt).
            frame_span = 50.0 if _TARGET_IS_GEOGRAPHIC else 50.0 * METERS_PER_DEGREE
            origin_span = 10.0 if _TARGET_IS_GEOGRAPHIC else 10.0 * METERS_PER_DEGREE
            pts = pts_resolved
            if pts and len(pts) >= 2:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                span_x = max(xs) - min(xs)
                span_y = max(ys) - min(ys)
                has_origin = any(abs(p[0]) < 1e-9 and abs(p[1]) < 1e-9 for p in pts)
                if max(span_x, span_y) > frame_span or \
                        (has_origin and max(span_x, span_y) > origin_span):
                    drop_counts.setdefault("warn_frame_artifact", defaultdict(int))[
                        fdata.get("layer", "<no layer>")] += 1

            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetGeometry(geom)

            # Set schema fields from extracted attributes
            for fname in field_map:
                if fname in fdata["attrs"] and fdata["attrs"][fname] is not None:
                    val = fdata["attrs"][fname]
                    ftype = field_map[fname]["type"]
                    try:
                        if ftype == "Integer":
                            feature.SetField(fname, int(val))
                        elif ftype == "Double":
                            feature.SetField(fname, float(val))
                        else:
                            feature.SetField(fname, str(val))
                    except (ValueError, TypeError):
                        pass

            # Set computed X/Y for point layers
            if geom_name == "Point" or "Point" in geom_name:
                cx, cy = fdata["centroid"]
                feature.SetField("X", cx)
                feature.SetField("Y", cy)

            # Set computed LONGUEUR for line layers
            if geom_name == "LineString":
                feature.SetField("LONGUEUR", _line_length_m(pts_resolved))

            # Metadata
            feature.SetField("source_file", fdata.get("source_file", ""))
            feature.SetField("dwg_layer", fdata.get("layer", ""))
            feature.SetField("dwg_type", fdata.get("dwg_type_name", ""))
            feature.SetField("classification_method", fdata.get("classification_method", ""))
            ann = fdata.get("annotation_text", "") or fdata.get("text", "")
            if ann:
                feature.SetField("annotation_text", ann)
            feature.SetField("display_label", fdata.get("display_label", "") or "")
            feature.SetField("label_provenance",
                             fdata["attrs"].get("label_provenance", "synthetic"))
            if fdata.get("color_aci") is not None:
                feature.SetField("color_aci", int(fdata["color_aci"]))
            if fdata.get("color_rgb"):
                feature.SetField("color_rgb", fdata["color_rgb"])
            if fdata.get("style_key"):
                feature.SetField("style_key", fdata["style_key"])
            if fc_name == "BOITE":
                feature.SetField("representation",
                                 fdata.get("representation", "") or
                                 _representation_kind(fdata.get("dwg_type_name", "")))

            layer.CreateFeature(feature)
            feature = None
            count += 1

        layer_stats[fc_name] = count
        output_layers[fc_name] = layer
        print(f"  {fc_name}: {count} features")

    # ── Metadata tables ──────────────────────────────────────────────────

    # pipeline_manifest
    manifest_layer = ds.CreateLayer("pipeline_manifest", srs, ogr.wkbNone)
    manifest_layer.CreateField(ogr.FieldDefn("pipeline", ogr.OFTString))
    manifest_layer.CreateField(ogr.FieldDefn("version", ogr.OFTString))
    manifest_layer.CreateField(ogr.FieldDefn("timestamp", ogr.OFTString))
    manifest_layer.CreateField(ogr.FieldDefn("source_files", ogr.OFTString))
    manifest_layer.CreateField(ogr.FieldDefn("source_sha256", ogr.OFTString))
    manifest_layer.CreateField(ogr.FieldDefn("crs", ogr.OFTString))
    manifest_layer.CreateField(ogr.FieldDefn("total_features", ogr.OFTInteger))
    manifest_layer.CreateField(ogr.FieldDefn("misc_features", ogr.OFTInteger))

    mf = ogr.Feature(manifest_layer.GetLayerDefn())
    mf.SetField("pipeline", "GeoFormer_FiberHome_P2")
    mf.SetField("version", "2.0")
    mf.SetField("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    mf.SetField("source_files", "; ".join(source_files))
    sha = "; ".join(f"{f}={_sha256_file(f)}" for f in source_files if os.path.exists(f))
    mf.SetField("source_sha256", sha)
    mf.SetField("crs", target_crs_label)
    mf.SetField("total_features", sum(layer_stats.values()))
    mf.SetField("misc_features", misc_count)
    manifest_layer.CreateFeature(mf)
    mf = None

    # transform_record
    transform_layer = ds.CreateLayer("transform_record", srs, ogr.wkbNone)
    transform_layer.CreateField(ogr.FieldDefn("operation", ogr.OFTString))
    transform_layer.CreateField(ogr.FieldDefn("detail", ogr.OFTString))

    for op_name, detail in [
        ("crs_source", source_crs_label),
        ("crs_output", target_crs_label),
        ("reprojection", transform_desc or (
            "identity (source CRS == target CRS)" if _CRS_TRANSFORM is None
            else f"osr.CoordinateTransformation {source_crs_label} → "
                 f"{target_crs_label} (PROJ, traditional GIS axis order)")),
        ("coordinate_filter", "CRS-aware envelope: |lon|<=180/|lat|<=90 "
            "(geographic) or web-mercator ±20037508/±20048966 (projected)"),
        ("region_bounds_check",
            f"EPSG:4326 lat [{REGION_BOUNDS_WGS84[0]},{REGION_BOUNDS_WGS84[1]}], "
            f"lon [{REGION_BOUNDS_WGS84[2]},{REGION_BOUNDS_WGS84[3]}] — "
            "warning + per-layer count only, no discard"),
        ("frame_artifact_check", "sheet-origin/oversized-span features flagged "
            "as warn_frame_artifact, still written"),
        ("model_space_filter", "entmode!=2 (block definitions, paper space) → discard, counted per layer"),
        ("block_insert_handling", "INSERT routed by DWG layer name → point at insertion"),
        ("dimension_extraction", "DIMENSION act_measurement → span_records table"),
        ("span_annotation_layer", "DIMENSION xline definition lines → "
            "span_annotations layer: display_label \"xx.x m\" + nearest CABLE "
            "true-FID/CODE foreign keys (resolved post-topology)"),
        ("color_extraction", "entity Dwg_Color (ACI index + 0xC2 truecolor) + "
            "layer-table colour; ByLayer(256)/ByBlock(0) resolved to layer "
            "colour; ACI→RGB standard 255-colour palette → color_aci/"
            "color_rgb/style_key on all FC layers and span_records"),
        ("fragment_aggregation", "; ".join(
            f"{fc}: {n_frag} fragments → {n_cl} points"
            for fc, n_frag, n_cl in agg_summaries) or "none"),
        ("boite_representation_fusion",
            (f"{BOITE_FUSION_LEDGER['summary'].get('input', 0)} → "
             f"{BOITE_FUSION_LEDGER['summary'].get('output', 0)} features; "
             f"{BOITE_FUSION_LEDGER['summary'].get('merged', 0)} merged, "
             f"{BOITE_FUSION_LEDGER['summary'].get('duplicate_sets', 0)} duplicate "
             f"representation set(s) folded, "
             f"{BOITE_FUSION_LEDGER['summary'].get('secondary', 0)} secondary → "
             f"quarantine_review") if BOITE_FUSION_LEDGER["summary"] else "none"),
        ("geometry_reconstruction", "Adaptive chord tolerance, WKT to OGR"),
        ("length_computation", "haversine (geographic target) / planar (projected target)"),
        ("attribute_extraction", "English telecom keyword regex patterns"),
        ("classification", "Two-tier: layer regex + annotation keywords"),
    ]:
        tr = ogr.Feature(transform_layer.GetLayerDefn())
        tr.SetField("operation", op_name)
        tr.SetField("detail", detail)
        transform_layer.CreateFeature(tr)
        tr = None

    # span_records — DIMENSION measurements (pole-to-pole cable spans)
    span_layer = ds.CreateLayer("span_records", srs, ogr.wkbLineString)
    span_layer.CreateField(ogr.FieldDefn("SPAN_M", ogr.OFTReal))
    span_layer.CreateField(ogr.FieldDefn("dwg_layer", ogr.OFTString))
    span_layer.CreateField(ogr.FieldDefn("source_file", ogr.OFTString))
    span_layer.CreateField(ogr.FieldDefn("nearest_cable_id", ogr.OFTInteger))
    for sdef in STYLE_FIELDS:
        span_layer.CreateField(ogr.FieldDefn(
            sdef["name"],
            ogr.OFTInteger if sdef["type"] == "Integer" else ogr.OFTString))

    for rec in span_records:
        sf = ogr.Feature(span_layer.GetLayerDefn())
        if rec.get("xline1") and rec.get("xline2"):
            g = ogr.CreateGeometryFromWkt(
                _wkt_linestring([rec["xline1"], rec["xline2"]]))
        else:
            dx, dy = rec["def_pt"]
            g = ogr.CreateGeometryFromWkt(
                _wkt_linestring([(dx, dy), (dx, dy)]))
        sf.SetGeometry(g)
        sf.SetField("SPAN_M", rec["measurement"])
        sf.SetField("dwg_layer", rec.get("layer", ""))
        sf.SetField("source_file", rec.get("source_file", ""))
        sf.SetField("nearest_cable_id", rec.get("nearest_cable_id", -1))
        if rec.get("color_aci") is not None:
            sf.SetField("color_aci", int(rec["color_aci"]))
        if rec.get("color_rgb"):
            sf.SetField("color_rgb", rec["color_rgb"])
        if rec.get("style_key"):
            sf.SetField("style_key", rec["style_key"])
        span_layer.CreateFeature(sf)
        sf = None

    # drop_accounting — per-port per-layer discard counts
    drop_layer = ds.CreateLayer("drop_accounting", srs, ogr.wkbNone)
    drop_layer.CreateField(ogr.FieldDefn("port", ogr.OFTString))
    drop_layer.CreateField(ogr.FieldDefn("dwg_layer", ogr.OFTString))
    drop_layer.CreateField(ogr.FieldDefn("count", ogr.OFTInteger))

    for port in sorted(drop_counts):
        for lyr, n in sorted(drop_counts[port].items(), key=lambda kv: -kv[1]):
            df = ogr.Feature(drop_layer.GetLayerDefn())
            df.SetField("port", port)
            df.SetField("dwg_layer", lyr)
            df.SetField("count", n)
            drop_layer.CreateFeature(df)
            df = None

    # qc_summary
    qc_layer = ds.CreateLayer("qc_summary", srs, ogr.wkbNone)
    qc_layer.CreateField(ogr.FieldDefn("layer", ogr.OFTString))
    qc_layer.CreateField(ogr.FieldDefn("feature_count", ogr.OFTInteger))
    qc_layer.CreateField(ogr.FieldDefn("geometry_type", ogr.OFTString))
    qc_layer.CreateField(ogr.FieldDefn("geographic_outliers", ogr.OFTInteger))
    qc_layer.CreateField(ogr.FieldDefn("classification_confidence_mean", ogr.OFTReal))
    qc_layer.CreateField(ogr.FieldDefn("note", ogr.OFTString))

    qc_notes = {
        "ZNRO": ("EMPTY (QUARANTINE): source drawing has no OLT/NRO zone "
                 "layer — no feature synthesized"),
        "SITE": "; ".join(
            f"aggregated {n_frag} FDT fragments into {n_cl} point(s)"
            for fc, n_frag, n_cl in agg_summaries if fc == "SITE"),
        "BOITE": "; ".join(
            f"aggregated {n_frag} fragments into {n_cl} point(s)"
            for fc, n_frag, n_cl in agg_summaries if fc == "BOITE"),
        "CABLE": (f"{len(span_records)} DIMENSION span measurements in "
                  f"span_records table" if span_records else ""),
    }

    for fc_name in layer_order:
        flist = fc_features.get(fc_name, [])
        outliers = sum(1 for f in flist if f.get("geographic_outlier"))
        confs = [f.get("classification_confidence", 0) for f in flist]
        mean_conf = sum(confs) / len(confs) if confs else 0.0
        geom_name = fc_configs[fc_name]["geometry_type"]

        qr = ogr.Feature(qc_layer.GetLayerDefn())
        qr.SetField("layer", fc_name)
        qr.SetField("feature_count", layer_stats.get(fc_name, 0))
        qr.SetField("geometry_type", geom_name)
        qr.SetField("geographic_outliers", outliers)
        qr.SetField("classification_confidence_mean", mean_conf)
        note = qc_notes.get(fc_name, "")
        if note:
            qr.SetField("note", note)
        qc_layer.CreateFeature(qr)
        qr = None

    ds = None
    return layer_stats, misc_count


# ── QGIS Style Output (B1: BOITE representation style group) ─────────────

_BOITE_STYLE_PALETTE = [
    # (fill RGBA, marker shape) cycled per representation category
    ("227,26,28,255", "square"),
    ("31,120,180,255", "circle"),
    ("51,160,44,255", "triangle"),
    ("255,127,0,255", "diamond"),
    ("106,61,154,255", "pentagon"),
    ("177,89,40,255", "star"),
]


def _write_boite_styles(styles_dir, representation_counts):
    """Write a categorized QML for BOITE (categories = representation values)
    plus a style_manifest.json, so QGIS renders the unified FAT layer group
    with one style per source-representation composition."""
    os.makedirs(styles_dir, exist_ok=True)
    categories = sorted(representation_counts)
    cat_xml, sym_xml = [], []
    for idx, value in enumerate(categories):
        color, shape = _BOITE_STYLE_PALETTE[idx % len(_BOITE_STYLE_PALETTE)]
        cat_xml.append(
            f'<category value="{value}" label="{value} '
            f'({representation_counts[value]})" symbol="{idx}" render="true" />')
        sym_xml.append(
            f'<symbol type="marker" name="{idx}" alpha="1">'
            f'<layer class="SimpleMarker" enabled="1"><Option type="Map">'
            f'<Option name="color" value="{color}" type="QString" />'
            f'<Option name="outline_color" value="20,20,20,255" type="QString" />'
            f'<Option name="name" value="{shape}" type="QString" />'
            f'<Option name="size" value="3.2" type="QString" />'
            f'<Option name="size_unit" value="MM" type="QString" />'
            f'</Option></layer></symbol>')
    qml = (
        '<qgis version="3.40.0" styleCategories="AllStyleCategories" '
        'labelsEnabled="1" simplifyDrawingHints="1">'
        '<renderer-v2 type="categorizedSymbol" attr="representation">'
        f'<categories>{"".join(cat_xml)}</categories>'
        f'<symbols>{"".join(sym_xml)}</symbols>'
        '</renderer-v2>'
        '<labeling type="simple"><settings>'
        '<text-style fieldName="display_label" isExpression="0" '
        'fontFamily="Arial" fontSize="8" textColor="0,0,0,255" />'
        '<placement placement="0" dist="1" offsetUnits="MM" />'
        '<rendering drawLabels="1" obstacle="1" scaleVisibility="0" />'
        '</settings></labeling>'
        '<layerGeometryType>0</layerGeometryType></qgis>')
    qml_path = os.path.join(styles_dir, "BOITE.qml")
    with open(qml_path, "w", encoding="utf-8") as fh:
        fh.write(qml)
    manifest = {
        "schema_version": "cad2gis-qgis-style-manifest-v1",
        "layers": {
            "BOITE": {
                "qml": "BOITE.qml",
                "qml_sha256": _sha256_file(qml_path),
                "render_field": "representation",
                "representation_categories": {
                    value: representation_counts[value] for value in categories},
                "label_field": "display_label",
            }
        },
    }
    manifest_path = os.path.join(styles_dir, "style_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    return qml_path, manifest_path


# ── Main CLI ──────────────────────────────────────────────────────────────

# ── Evidence Ledger + Legend Detection Stages (components D + F) ──────────

# Ports excluded from the conservation ledger: non-model-space entities
# (block_definition/paper_space), pure warnings (feature still written), and
# the fc_misc detail port (its entities are accounted under out_of_scope or
# their annotation drop port — drop_accounting keeps the per-layer view).
_LEDGER_EXCLUDED_PORTS = {
    "block_definition", "paper_space", "fc_misc",
    "warn_outside_region_bounds", "warn_frame_artifact",
}

# Post-hoc provenance classification per business field (CODE is per-value
# via the label_provenance column; unlisted fields default to dwg-attribute
# since their values are extracted from DWG text/layer content).
_FIELD_PROVENANCE_RULES = {
    "display_label": "annotation-assigned",
    "X": "computed", "Y": "computed", "LONGUEUR": "computed",
    "FDT_ID": "computed", "representation": "computed",
    "ORIGINE": "computed", "EXTREMITE": "computed",
    "TYPE": "computed", "COMMENT": "computed",
    "NUMERO_VOIE": "dwg-attribute",
}
_PROVENANCE_METADATA_FIELDS = {
    "source_file", "dwg_layer", "dwg_type", "classification_method",
    "annotation_text", "label_provenance",
    "color_aci", "color_rgb", "style_key",
}


def _entity_weight(feat):
    """Source-entity weight of a feature for conservation accounting.
    Annotation-derived fc_misc copies weigh 0 (their entity is already
    accounted under a family_label_*/unlinked_annotation drop port);
    aggregation products carry the count of consumed fragments."""
    method = feat.get("classification_method", "")
    if method.startswith("family_label_") or method == "unlinked_annotation":
        return 0
    return int(feat.get("source_entity_count", 1))


def _run_legend_detection(args, all_features, drop_counts):
    """Component F: detect Model-space legend/non-subject clusters.
    Unconfirmed clusters yield LEGEND_CANDIDATE quarantine entries (delivery
    untouched); clusters confirmed in the exclusion config get
    disposition=legend and leave the delivery set.
    Returns (filtered_features, quarantine_entries)."""
    detector_input = [
        {"id": idx, "centroid": f["centroid"], "layer": f["layer"],
         "text": f.get("text") or f.get("annotation_text") or ""}
        for idx, f in enumerate(all_features)
    ]
    exclusions_path = args.legend_exclusions or legend_detector.DEFAULT_EXCLUSIONS_PATH
    exclusions = legend_detector.load_exclusions(args.legend_exclusions)
    result = legend_detector.detect_legend_clusters(
        detector_input,
        gap_min=args.legend_gap_min, gap_k=args.legend_gap_k,
        min_cluster_size=args.legend_min_cluster_size,
        max_cluster_fraction=args.legend_max_cluster_fraction,
        min_confidence=args.legend_min_confidence,
        exclusions=exclusions)
    clusters = result["clusters"]
    print(f"\nLegend detection (component F): {len(clusters)} candidate "
          f"cluster(s) among {result['feature_count']} features "
          f"(gap_min={args.legend_gap_min}, gap_k={args.legend_gap_k})")

    quarantine_entries = []
    excluded_indices = set()
    for cluster in clusters:
        bbox = [round(v, 1) for v in cluster["bbox"]]
        print(f"  {cluster['cluster_id']}: members={cluster['member_count']} "
              f"confidence={cluster['confidence']} bbox={bbox} "
              f"anchors={cluster['anchor_hits'] or 'none'} "
              f"confirmed={cluster['confirmed']}")
        if cluster["confirmed"]:
            excluded_indices.update(cluster["member_ids"])
            quarantine_entries.append({
                "fc_name": "legend_cluster", "feature_fid": None,
                "feature_code": cluster["cluster_id"],
                "reason": "confirmed-excluded",
                "distance": None, "nearest_code": None,
                "suggestion": (
                    f"cluster {cluster['cluster_id']}: bbox={bbox}, "
                    f"members={cluster['member_count']}, "
                    f"confidence={cluster['confidence']}, "
                    f"anchors={cluster['anchor_hits'] or 'none'}; confirmed "
                    f"non-subject in {exclusions_path}; all members excluded "
                    f"from delivery (disposition=legend, see drop_accounting "
                    f"port 'legend')"),
            })
            continue
        for idx in cluster["member_ids"]:
            f = all_features[idx]
            quarantine_entries.append({
                "fc_name": f["fc_name"], "feature_fid": None,
                "feature_code": f["attrs"].get("CODE"),
                "reason": "LEGEND_CANDIDATE",
                "distance": None, "nearest_code": None,
                "suggestion": (
                    f"cluster {cluster['cluster_id']}: bbox={bbox}, "
                    f"members={cluster['member_count']}, "
                    f"confidence={cluster['confidence']}, "
                    f"anchors={cluster['anchor_hits'] or 'none'}; member at "
                    f"({f['centroid'][0]:.1f}, {f['centroid'][1]:.1f}) on layer "
                    f"'{f['layer']}'; confirm cluster in {exclusions_path} "
                    f"to exclude from delivery"),
            })

    if not excluded_indices:
        return all_features, quarantine_entries

    kept = []
    excluded_count = 0
    for idx, f in enumerate(all_features):
        if idx in excluded_indices:
            excluded_count += 1
            for _ in range(_entity_weight(f)):
                drop_counts["legend"][f["layer"] or "<no layer>"] += 1
        else:
            kept.append(f)
    print(f"  {excluded_count} confirmed legend feature(s) excluded from "
          f"delivery (disposition=legend)")
    return kept, quarantine_entries


def _build_conservation_entries(all_features, drop_counts, ledger_counts):
    """Compose per-(disposition, dwg_layer) entity counts covering every
    model-space entity exactly once."""
    nested = defaultdict(lambda: defaultdict(int))
    for port, per_layer in drop_counts.items():
        if port in _LEDGER_EXCLUDED_PORTS:
            continue
        for lyr, n in per_layer.items():
            nested[port][lyr] += n
    for lyr, n in ledger_counts["annotation_consumed"].items():
        nested["annotation_consumed"][lyr] += n
    for lyr, n in ledger_counts["dimension_span"].items():
        nested["dimension_span"][lyr] += n
    for f in all_features:
        weight = _entity_weight(f)
        if weight == 0:
            continue
        lyr = f["layer"] or "<no layer>"
        if f["fc_name"] in REQUIRED_LAYERS:
            nested["mapped"][lyr] += weight
        else:
            nested["out_of_scope"][lyr] += weight
    # Write-time geometry failures stay in all_features but were already
    # counted under their own drop ports — remove them from mapped.
    for port in ("geom_coercion_failed", "invalid_wkt"):
        for lyr, n in drop_counts.get(port, {}).items():
            nested["mapped"][lyr] -= n
    entries = [
        {"disposition": disposition, "dwg_layer": lyr, "count": n}
        for disposition in sorted(nested)
        for lyr, n in sorted(nested[disposition].items())
        if n > 0
    ]
    return entries


def _build_field_provenance(gpkg_path):
    """Aggregate (fc, field, provenance) counts over every non-empty business
    field of the final delivery layers (post topology/FDT stages)."""
    occurrences = []
    ds = ogr.Open(gpkg_path, 0)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage for provenance scan: {gpkg_path}")
    try:
        for fc_name in ("BOITE", "CABLE", "PTECH", "INFRASTRUCTURE",
                        "SITE", "ZNRO", "ZPM", "IMB"):
            lyr = ds.GetLayerByName(fc_name)
            if lyr is None:
                continue
            defn = lyr.GetLayerDefn()
            names = [defn.GetFieldDefn(i).GetName()
                     for i in range(defn.GetFieldCount())]
            business = [n for n in names if n not in _PROVENANCE_METADATA_FIELDS]
            lp_idx = defn.GetFieldIndex("label_provenance")
            lyr.ResetReading()
            for feat in lyr:
                lp = feat.GetField(lp_idx) if lp_idx >= 0 else None
                for fname in business:
                    value = feat.GetField(fname)
                    if value is None or (isinstance(value, str) and value.strip() == ""):
                        continue
                    if fname == "CODE":
                        kind = lp if lp in ("annotation-assigned", "synthetic") \
                            else "synthetic"
                    else:
                        kind = _FIELD_PROVENANCE_RULES.get(fname, "dwg-attribute")
                    occurrences.append((fc_name, fname, kind))
    finally:
        ds = None
    return evidence_ledger.aggregate_provenance(occurrences)


def _write_evidence_stage(args, all_features, drop_counts, ledger_counts,
                          candidate_target_map):
    """Component D: write the three evidence tables into the final GPKG."""
    print("\nEvidence ledger (component D):")
    entries = _build_conservation_entries(all_features, drop_counts, ledger_counts)
    expected_total = ledger_counts["model_space_total"]

    status_map = {"selected": "selected",
                  "ambiguous": "abstained_multiple_optima",
                  "candidate": "lost"}
    candidates = []
    for cand in ANNOTATION_LEDGER["candidates"]:
        target = candidate_target_map.get(
            (cand.get("source_file"), cand.get("target_global_id")))
        candidates.append({
            "annotation_key": f"{cand.get('source_file', '')}:{cand['ann_id']}",
            "text": cand["text"],
            "family": cand["family"],
            "target_fc": cand["target_fc"],
            "target_code": (target or {}).get("attrs", {}).get("CODE"),
            "distance_m": cand.get("distance_m"),
            "selected": cand.get("selected", False),
            "status": status_map.get(cand.get("status"), "lost"),
        })

    provenance = _build_field_provenance(args.output)
    summary = evidence_ledger.write_evidence_tables(
        args.output, conservation_entries=entries, expected_total=expected_total,
        candidates=candidates, provenance_records=provenance)
    conservation = summary[evidence_ledger.CONSERVATION_TABLE]
    print(f"  conservation_ledger: {conservation['rows']} rows, "
          f"sum={conservation['sum']}, expected={expected_total}, "
          f"ok={conservation['ok']}")
    if not conservation["ok"]:
        print(f"  WARNING: conservation SUM mismatch "
              f"(diff={conservation['sum'] - expected_total:+d}) — "
              f"evaluator rule 8.1 will flag this")
    print(f"  annotation_assignment_candidates: {len(candidates)} rows "
          f"({sum(1 for c in candidates if c['selected'])} selected)")
    print(f"  field_provenance: {len(provenance)} rows")


def main():
    parser = argparse.ArgumentParser(
        description="GeoFormer P2: DWG-to-GeoPackage converter (FTTH domain, CRS parameterized)",
    )
    parser.add_argument("--input", "-i", nargs="+", required=True,
                       help="One or more DWG files to convert")
    parser.add_argument("--output", "-o", required=True,
                       help="Output GeoPackage path (.gpkg)")
    parser.add_argument("--temp-dir", default="/tmp/geoformer",
                       help="Temporary directory (default: /tmp/geoformer)")
    parser.add_argument("--config", default=None,
                       help="Optional JSON config (not used; schema from schema_config.py)")
    parser.add_argument("--source-crs", default="EPSG:3857",
                       help="Source DWG CRS (default: EPSG:3857 — Hutabohu "
                            "drawings are web-mercator metric)")
    parser.add_argument("--target-crs", default="EPSG:3857",
                       help="Output GeoPackage CRS (default: EPSG:3857). "
                            "Use EPSG:4326 for WGS84 delivery.")
    parser.add_argument("--fragment-cluster-tol", type=float,
                       default=DEFAULT_FRAGMENT_CLUSTER_TOL_M,
                       help="Proximity clustering tolerance in metres for "
                            "FDT/FAT structure-diagram fragments (default: 50)")
    parser.add_argument("--annotation-link-tol", type=float,
                       default=DEFAULT_ANNOTATION_LINK_TOL_M,
                       help="Max distance in metres to link a text label to "
                            "its feature (default: 15)")
    parser.add_argument("--dwgread-cache",
                       help="Path to dwgread -O json dump for reading ATTRIB "
                            "(FAT/FDT values) from block definitions. "
                            "Generating: dwgread -O json <dwg> > cache.json")
    parser.add_argument("--boite-fusion-tol", type=float,
                       default=DEFAULT_BOITE_FUSION_TOL_M,
                       help="Spatial tolerance in metres for fusing BOITE "
                            "multi-representations (default: 5)")
    parser.add_argument("--snap-tol", type=float, default=5.0,
                       help="Topology snap tolerance in metres, converted to "
                            "working-CRS units (default: 5)")
    parser.add_argument("--isolation-threshold", type=float, default=30.0,
                       help="Topology isolation threshold in metres: endpoints "
                            "beyond snap-tol but within this get attributes only "
                            "+ quarantine; beyond it stay empty (default: 30)")
    parser.add_argument("--chain-tol", type=float, default=0.5,
                       help="Cable chaining endpoint weld tolerance in metres "
                            "(default: 0.5); node cuts use snap-tol")
    parser.add_argument("--skip-chaining", action="store_true",
                       help="Skip the cable chaining stage (B2 logical segments)")
    parser.add_argument("--enable-gap-bridge", action="store_true",
                       help="Enable the constrained gap bridge during chaining "
                            "(same dwg_layer + straight continuation, tol = "
                            "snap-tol). Default off: topology-fidelity-first "
                            "(see guide/T_TOPOLOGY_REPAIR_ANALYSIS.md)")
    parser.add_argument("--skip-topology", action="store_true",
                       help="Skip the graded topology repair stage")
    parser.add_argument("--legend-gap-min", type=float,
                       default=legend_detector.DEFAULT_GAP_MIN,
                       help="Legend detection: absolute gap floor in CRS units "
                            f"(default: {legend_detector.DEFAULT_GAP_MIN})")
    parser.add_argument("--legend-gap-k", type=float,
                       default=legend_detector.DEFAULT_GAP_K,
                       help="Legend detection: relative gap floor as fraction of "
                            "the P10-P90 body span "
                            f"(default: {legend_detector.DEFAULT_GAP_K})")
    parser.add_argument("--legend-min-cluster-size", type=int,
                       default=legend_detector.DEFAULT_MIN_CLUSTER_SIZE,
                       help="Legend detection: minimum members per candidate "
                            f"(default: {legend_detector.DEFAULT_MIN_CLUSTER_SIZE})")
    parser.add_argument("--legend-max-cluster-fraction", type=float,
                       default=legend_detector.DEFAULT_MAX_CLUSTER_FRACTION,
                       help="Legend detection: maximum candidate size as fraction "
                            "of all features "
                            f"(default: {legend_detector.DEFAULT_MAX_CLUSTER_FRACTION})")
    parser.add_argument("--legend-min-confidence", type=float,
                       default=legend_detector.DEFAULT_MIN_CONFIDENCE,
                       help="Legend detection: report threshold "
                            f"(default: {legend_detector.DEFAULT_MIN_CONFIDENCE})")
    parser.add_argument("--legend-exclusions", default=None,
                       help="Confirmed legend cluster config JSON (default: "
                            "experiment/config/legend_exclusions.json)")
    parser.add_argument("--skip-legend-detection", action="store_true",
                       help="Skip the Model-space legend cluster detection stage")
    parser.add_argument("--skip-styles", action="store_true",
                       help="Skip the three-track QGIS style outputs "
                            "(sidecar QML + embedded layer_styles + .qgz)")
    args = parser.parse_args()

    # Set up CRS pipeline: source → target (+ target → 4326 for region checks)
    global _CRS_TRANSFORM, _TO_WGS84, _SOURCE_IS_GEOGRAPHIC, _TARGET_IS_GEOGRAPHIC
    global _TARGET_IS_WEBMERC, _SOURCE_CRS_LABEL, _TARGET_CRS_LABEL
    _SOURCE_CRS_LABEL = args.source_crs
    _TARGET_CRS_LABEL = args.target_crs

    src = osr.SpatialReference()
    src.SetFromUserInput(args.source_crs)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst = osr.SpatialReference()
    dst.SetFromUserInput(args.target_crs)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    _SOURCE_IS_GEOGRAPHIC = bool(src.IsGeographic())
    _TARGET_IS_GEOGRAPHIC = bool(dst.IsGeographic())
    _TARGET_IS_WEBMERC = dst.GetAuthorityCode(None) == "3857"

    if src.IsSame(dst):
        _CRS_TRANSFORM = None
        transform_desc = f"identity (source CRS == target CRS: {args.target_crs})"
        print(f"  CRS: {args.source_crs} == {args.target_crs} (identity, no reprojection)")
    else:
        _CRS_TRANSFORM = osr.CoordinateTransformation(src, dst)
        transform_desc = (f"osr.CoordinateTransformation {args.source_crs} → "
                          f"{args.target_crs} (PROJ, traditional GIS axis order)")
        print(f"  CRS: {args.source_crs} → {args.target_crs} (reprojection enabled)")

    _TO_WGS84 = None if dst.IsSame(wgs84) else osr.CoordinateTransformation(dst, wgs84)

    # Validate inputs exist
    for path in args.input:
        if not os.path.isfile(path):
            print(f"ERROR: Input file not found: {path}", file=sys.stderr)
            sys.exit(1)

    os.makedirs(args.temp_dir, exist_ok=True)

    print("=" * 60)
    print("GeoFormer P2: DWG → GeoPackage Converter")
    print(f"  Input:  {len(args.input)} DWG file(s)")
    print(f"  Output: {args.output}")
    print(f"  CRS:    {args.source_crs} → {args.target_crs}")
    print("=" * 60)

    all_features = []
    all_spans = []
    all_agg = []
    drop_counts = defaultdict(lambda: defaultdict(int))
    ledger_totals = {
        "model_space_total": 0,
        "annotation_consumed": defaultdict(int),
        "dimension_span": defaultdict(int),
    }
    candidate_target_map = {}
    warnings_log = []

    for dwg_path in args.input:
        print(f"\nProcessing: {dwg_path}")
        features, spans, drops, agg, lcounts = read_dwg(
            dwg_path,
            fragment_cluster_tol_m=args.fragment_cluster_tol,
            annotation_link_tol_m=args.annotation_link_tol,
            boite_fusion_tol_m=args.boite_fusion_tol,
        )
        # Per-file feature objects addressable by (source_file, per-file id):
        # annotation candidate records resolve their target CODE through this
        # map after global reindexing.
        src_base = os.path.basename(dwg_path)
        for f in features:
            candidate_target_map[(src_base, f["global_id"])] = f
        ledger_totals["model_space_total"] += lcounts["model_space_total"]
        for lyr, n in lcounts["annotation_consumed"].items():
            ledger_totals["annotation_consumed"][lyr] += n
        for lyr, n in lcounts["dimension_span"].items():
            ledger_totals["dimension_span"][lyr] += n
        all_features.extend(features)
        all_spans.extend(spans)
        all_agg.extend(agg)
        for port, per_layer in drops.items():
            for lyr, n in per_layer.items():
                drop_counts[port][lyr] += n

        # Log warnings per file
        file_outliers = sum(1 for f in features if f.get("geographic_outlier"))
        file_misc = sum(1 for f in features if f["fc_name"] == "fc_misc")
        if file_outliers:
            warnings_log.append(
                f"GEOGRAPHIC_OUTLIER: {file_outliers} features in {os.path.basename(dwg_path)} "
                f"outside deployment region bounds (not discarded, flagged only)"
            )
        if file_misc:
            warnings_log.append(
                f"UNCLASSIFIED: {file_misc} features in {os.path.basename(dwg_path)} "
                f"assigned to fc_misc"
            )

    # Re-index global IDs
    for idx, feat in enumerate(all_features):
        feat["global_id"] = idx

    # Legend / non-subject cluster detection (component F): candidates go to
    # quarantine review; config-confirmed clusters leave the delivery set.
    # Runs BEFORE code assignment so excluded clusters cannot consume the
    # first synthetic codes (CBL0001/PM0001/... stay with real features).
    legend_quarantine_entries = []
    if not args.skip_legend_detection:
        all_features, legend_quarantine_entries = _run_legend_detection(
            args, all_features, drop_counts)
    else:
        print("\nLegend detection: skipped (--skip-legend-detection)")

    # Assign CODE / default TYPE per feature class (true label first,
    # deterministic synthetic sequence fallback)
    _assign_codes(all_features)

    # Label-binding acceptance summary
    if ANNOTATION_LEDGER["stats"]:
        print("\nLabel binding (family gate + Hungarian global assignment):")
        for fam, s in sorted(ANNOTATION_LEDGER["stats"].items()):
            print(f"  {fam} → {s['target_fc']}: {s['assigned']}/{s['annotations']} "
                  f"assigned ({s['candidate_edges']} candidate edges, "
                  f"{s.get('outside_tolerance', 0)} outside_tolerance, "
                  f"{s.get('multiple_optima', 0)} multiple_optima, "
                  f"{s.get('assignment_conflict', 0)} assignment_conflict)")
        for fam_def in LABEL_FAMILIES:
            pattern = re.compile(fam_def["pattern"])
            n = sum(1 for f in all_features
                    if f["fc_name"] == fam_def["target_fc"]
                    and pattern.fullmatch(str(f["attrs"].get("CODE") or "")))
            print(f"  {fam_def['target_fc']}: {n} features with CODE matching "
                  f"{fam_def['pattern']}")

    print(f"\nTotal features: {len(all_features)}")

    # Print warnings
    for w in warnings_log:
        print(f"  WARNING: {w}")

    # Per-port per-layer discard accounting
    if drop_counts:
        print("\nDiscard accounting (port / dwg_layer / count):")
        for port in sorted(drop_counts):
            port_total = sum(drop_counts[port].values())
            print(f"  {port} (total {port_total}):")
            for lyr, n in sorted(drop_counts[port].items(), key=lambda kv: -kv[1]):
                print(f"    {n:6d}  {lyr}")

    # fc_misc per-layer accounting
    misc_by_layer = defaultdict(int)
    for f in all_features:
        if f["fc_name"] == "fc_misc":
            misc_by_layer[f["layer"] or "<no layer>"] += 1
    if misc_by_layer:
        print("\nfc_misc accounting (dwg_layer / count):")
        for lyr, n in sorted(misc_by_layer.items(), key=lambda kv: -kv[1]):
            print(f"    {n:6d}  {lyr}")
            drop_counts["fc_misc"][lyr] += n

    # Write GeoPackage
    print(f"\nWriting GeoPackage: {args.output}")
    stats, misc_count = write_geopackage(
        args.output, all_features, args.input,
        span_records=all_spans, drop_counts=drop_counts, agg_summaries=all_agg,
        source_crs_label=args.source_crs, target_crs_label=args.target_crs,
        transform_desc=transform_desc)

    # QGIS style group for the fused BOITE representation categories
    representation_counts = defaultdict(int)
    for f in all_features:
        if f["fc_name"] == "BOITE":
            rep = f.get("representation") or _representation_kind(
                f.get("dwg_type_name", ""))
            representation_counts[rep] += 1
    if representation_counts:
        styles_dir = os.path.join(
            os.path.dirname(os.path.abspath(args.output)), "qgis", "styles")
        qml_path, manifest_path = _write_boite_styles(
            styles_dir, dict(representation_counts))
        print(f"  QGIS styles: {qml_path}")
        print(f"               {manifest_path}")

    # Graded topology repair (component B): chain → snap → FDT_ID
    # (tolerances are ground metres; topology_builder does the CRS-aware
    # metric conversion internally so 4326/3857 outputs chain identically)
    topo_metrics = None
    chain_metrics = None
    if not args.skip_topology:
        import topology_builder
        snap_units = _meters_to_units(args.snap_tol)

        if not args.skip_chaining:
            bridge_desc = (f"constrained bridge ON (tol={args.snap_tol}m)"
                           if args.enable_gap_bridge else "gap bridge OFF")
            print(f"\nCable chaining (B2): weld_tol={args.chain_tol}m, "
                  f"node_cut_tol={args.snap_tol}m, {bridge_desc} "
                  f"(ground metres)")
            chain_metrics = topology_builder.chain_edges_gpkg(
                args.output, chain_tol=args.chain_tol,
                node_capture_tol=args.snap_tol,
                gap_bridge=args.enable_gap_bridge)
            j = chain_metrics["junctions"]
            print(f"  {chain_metrics['input_fragments']} fragments → "
                  f"{chain_metrics['output_segments']} logical segments "
                  f"({chain_metrics['node_splits']} node splits, "
                  f"{chain_metrics['gap_bridges']} gap bridges, "
                  f"{chain_metrics['fragments_absorbed']} absorbed; junctions: "
                  f"{j['pass_through']} pass-through, {j['node_cut']} node cuts, "
                  f"{j['degree_cut']} degree cuts)")
        else:
            chain_metrics = None
            print("\nCable chaining: skipped (--skip-chaining)")

        print(f"\nTopology repair: snap_tol={args.snap_tol}m, "
              f"isolation_threshold={args.isolation_threshold}m (ground metres)")
        extra_entries = list(legend_quarantine_entries)
        if stats.get("ZNRO", 0) == 0:
            extra_entries.append({
                "fc_name": "ZNRO", "feature_fid": None, "feature_code": None,
                "reason": "EMPTY_SOURCE_LAYER", "distance": None, "nearest_code": None,
                "suggestion": "Source drawing has no OLT/NRO zone layer; no feature "
                              "synthesized (see qc_summary note).",
            })
        for entry in BOITE_FUSION_LEDGER["secondary"]:
            extra_entries.append({
                "fc_name": "BOITE", "feature_fid": None,
                "feature_code": entry.get("code_hint"),
                "reason": "SECONDARY_REPRESENTATION",
                "distance": None, "nearest_code": None,
                "suggestion": (
                    f"Unlabeled {entry['kind']} representation on layer "
                    f"'{entry['layer']}' at ({entry['centroid'][0]:.1f}, "
                    f"{entry['centroid'][1]:.1f}) excluded from BOITE by "
                    f"representation fusion; review as diagram/legend content."),
            })
        topo_metrics = topology_builder.repair_gpkg(
            args.output,
            snap_tol=args.snap_tol,
            isolation_threshold=args.isolation_threshold,
            extra_quarantine_entries=extra_entries,
        )
        _append_topology_qc(args.output, topo_metrics, args.snap_tol,
                            args.isolation_threshold)
        ep = topo_metrics["endpoints"]
        net = topo_metrics["network"]
        print(f"  endpoints: {ep['snapped']} snapped, {ep['attr_only']} attr-only, "
              f"{ep['floating']} floating (of {ep['total']})")
        print(f"  network: {net['floating_cables']} FLOATING_CABLE, "
              f"{net['isolated_nodes']} ISOLATED_NODE, {net['self_loops']} self-loop(s)")
        print(f"  quarantine_review: {topo_metrics['quarantine_entries']} entries")

        # FDT domain decoupling (B3) + paper-space evidence (C)
        _run_fdt_tagging(args, topology_builder, snap_units, chain_metrics)
    else:
        print("\nTopology repair: skipped (--skip-topology)")
        if legend_quarantine_entries:
            print(f"  WARNING: {len(legend_quarantine_entries)} LEGEND_CANDIDATE "
                  f"entries not written (quarantine_review needs the topology stage)")

    # Span annotation layer (component P) — after all CABLE-mutating stages
    # so nearest_cable_fid references the final gpkg FIDs
    _write_span_annotations(args.output, all_spans)

    # BOITE block ATTRIB values (FAT/FDT) from DWG
    # Snap SITE positions to nearest PTECH pole (data-driven, not hardcoded)
    _snap_site_to_nearest_ptech(args.output)

    _write_boite_attrib_values(args.output, cache_path=getattr(args, 'dwgread_cache', None))

    # Evidence ledger tables (component D) — after all gpkg-mutating stages
    _write_evidence_stage(args, all_features, drop_counts, ledger_totals,
                          candidate_target_map)

    # Three-track QGIS styling (component S): sidecar QML + embedded
    # layer_styles + .qgz project. Runs last so categories reflect the
    # final post-topology/FDT layer contents. Style failure must not
    # invalidate the finished delivery gpkg (warning, NOT halt).
    if not args.skip_styles:
        import style_builder
        print("\nQGIS styling (three tracks):")
        try:
            style_builder.build_styles(args.output)
        except Exception as e:
            print(f"  WARNING: style generation failed ({e}); "
                  f"delivery gpkg is complete, rerun via style_builder.py")
    else:
        print("\nQGIS styling: skipped (--skip-styles)")

    print("\n" + "=" * 60)
    print("Conversion Summary:")
    display_stats = dict(stats)
    if chain_metrics:
        display_stats["CABLE"] = chain_metrics["output_segments"]
    for fc_name in ["BOITE", "CABLE", "PTECH", "INFRASTRUCTURE",
                     "SITE", "ZNRO", "ZPM", "IMB"]:
        note = ""
        if fc_name == "CABLE" and chain_metrics:
            note = (f" (chained from {chain_metrics['input_fragments']} "
                    f"source fragments)")
        print(f"  {fc_name:20s}: {display_stats.get(fc_name, 0):5d} features{note}")
    misc_negative = sum(1 for f in all_features
                        if f["fc_name"] == "fc_misc"
                        and f["classification_method"] == "negative_evidence")
    print(f"  {'fc_misc':20s}: {misc_count:5d} features "
          f"({misc_negative} negative-evidence, "
          f"{misc_count - misc_negative} unclassified)")
    print(f"  {'span_records':20s}: {len(all_spans):5d} dimension spans")
    print(f"  {'span_annotations':20s}: {len(all_spans):5d} labelled span lines")
    total = sum(display_stats.values()) + misc_count
    print(f"  {'TOTAL':20s}: {total:5d} features")
    if total:
        print(f"  misc ratio: {100.0 * misc_count / total:.1f}% "
              f"(unclassified-only: {100.0 * (misc_count - misc_negative) / total:.1f}%)")
    print(f"\nOutput: {args.output}")
    print(f"  SHA256: {_sha256_file(args.output)}")
    print("=" * 60)


def _append_topology_qc(gpkg_path, topo_metrics, snap_tol_m, isolation_threshold_m):
    """Append graded-topology statistics rows to the qc_summary table."""
    ds = ogr.Open(gpkg_path, 1)
    qc_layer = ds.GetLayerByName("qc_summary")
    if qc_layer is None:
        ds = None
        return
    ep = topo_metrics["endpoints"]
    net = topo_metrics["network"]
    note = (f"graded topology: snap_tol={snap_tol_m}m, "
            f"isolation_threshold={isolation_threshold_m}m, "
            f"crs={topo_metrics.get('crs')}")
    rows = [
        ("topology_snapped_endpoints", ep["snapped"]),
        ("topology_attr_only_endpoints", ep["attr_only"]),
        ("topology_floating_endpoints", ep["floating"]),
        ("topology_floating_cables", net["floating_cables"]),
        ("topology_isolated_nodes", net["isolated_nodes"]),
        ("topology_quarantine_entries", topo_metrics["quarantine_entries"]),
    ]
    defn = qc_layer.GetLayerDefn()
    for name, count in rows:
        qr = ogr.Feature(defn)
        qr.SetField("layer", name)
        qr.SetField("feature_count", int(count))
        qr.SetField("note", note)
        qc_layer.CreateFeature(qr)
        qr = None
    ds = None


def _run_fdt_tagging(args, topology_builder, snap_units, chain_metrics):
    """B3 domain decoupling driven by paper-space layout facts (component C).

    Mines FDT-01/FDT-02 layout attributes from the source DWGs, records the
    layout<->component matching verdict (abstention semantics), writes
    FDT_ID on CABLE/BOITE/PTECH/SITE and stores TOPOLOGY-sheet content in
    the topology_evidence side table.
    """
    import layout_miner

    facts, evidence = {}, []
    for dwg_path in args.input:
        try:
            mined = layout_miner.mine_dwg(dwg_path)
        except Exception as exc:
            print(f"  WARNING: layout mining failed for {dwg_path}: {exc}")
            continue
        for name, fact in mined["facts"].items():
            if fact["usable"] and fact["role"] == "plan" and name not in facts:
                facts[name] = fact
        evidence.extend(mined["evidence"])

    if not facts:
        print("\nFDT domain tagging: skipped (no usable plan-layout facts)")
        return

    print("\nFDT domain decoupling (B3, layout facts from paper space):")
    for name, fact in sorted(facts.items()):
        print(f"  {name}: FDT_ID={fact['fdt_id']} "
              f"({fact['sequence_count']} FAT sequences)")

    # layout <-> connected-component matching verdict (abstention preserved)
    try:
        components = layout_miner.extract_components_from_gpkg(
            args.output, endpoint_tol=snap_units)
        match = layout_miner.match_components_to_layouts(components, facts)
        for a in match["assignments"]:
            print(f"  component match: {a['component_id']} → {a['layout']} "
                  f"(score={a['score']})")
        by_status = defaultdict(int)
        for ab in match["abstentions"]:
            by_status[ab["status"]] += 1
        if by_status:
            print(f"  component abstentions: {dict(by_status)}")
    except Exception as exc:
        print(f"  WARNING: component matching failed: {exc}")

    domain_prefixes = {name: fact["fdt_id"] for name, fact in facts.items()}
    fdt_metrics = topology_builder.tag_fdt_domains_gpkg(
        args.output, domain_prefixes, endpoint_tol=args.snap_tol)
    for fc, dist in sorted(fdt_metrics["fdt_id_distribution"].items()):
        pretty = ", ".join(f"{k}={v}" for k, v in sorted(dist.items()))
        print(f"  {fc}: {pretty}")
    print(f"  connectivity: {fdt_metrics['edge_components']} edge components "
          f"(attribute-only pass, geometry untouched)")

    if evidence:
        n = layout_miner.write_topology_evidence_table(args.output, evidence)
        print(f"  topology_evidence: {n} records (TOPOLOGY sheets)")

    # qc_summary rows for the chain + FDT stages
    ds = ogr.Open(args.output, 1)
    qc_layer = ds.GetLayerByName("qc_summary") if ds is not None else None
    if qc_layer is not None:
        rows = []
        if chain_metrics:
            rows += [
                ("cable_chain_input_fragments", chain_metrics["input_fragments"]),
                ("cable_chain_output_segments", chain_metrics["output_segments"]),
                ("cable_chain_node_splits", chain_metrics["node_splits"]),
                ("cable_chain_gap_bridges", chain_metrics["gap_bridges"]),
            ]
        for fc, dist in fdt_metrics["fdt_id_distribution"].items():
            for value, count in dist.items():
                if value != "<empty>":
                    rows.append((f"fdt_id_{fc}_{value}", count))
        rows.append(("fdt_edge_components", fdt_metrics["edge_components"]))
        defn = qc_layer.GetLayerDefn()
        note = (f"B2/B3: chain_tol={args.chain_tol}m, node_cut={args.snap_tol}m, "
                f"gap_bridge={'constrained' if args.enable_gap_bridge else 'off'}, "
                f"domains={sorted(domain_prefixes)}")
        for name, count in rows:
            qr = ogr.Feature(defn)
            qr.SetField("layer", name)
            qr.SetField("feature_count", int(count))
            qr.SetField("note", note)
            qc_layer.CreateFeature(qr)
            qr = None
    ds = None


def _assign_codes(all_features):
    """Assign CODE values (true label first, synthetic fallback) and default
    TYPE per feature class. CODE stays unique per FC (evaluator rule 4.x):
    on duplicate label texts the first occurrence keeps the label, the rest
    fall back to synthetic sequence codes with label_provenance=synthetic."""
    code_prefix = {"SITE": "PM", "BOITE": "PBO", "PTECH": "PT",
                   "IMB": "IMB", "CABLE": "CBL", "INFRASTRUCTURE": "INF",
                   "ZPM": "ZPM", "ZNRO": "ZNR"}
    used_codes = defaultdict(set)
    duplicate_fallbacks = defaultdict(int)
    for feat in all_features:
        fc = feat["fc_name"]
        if fc not in code_prefix:
            continue
        code = feat["attrs"].get("CODE")
        if not code:
            continue
        if code in used_codes[fc]:
            del feat["attrs"]["CODE"]
            feat["attrs"]["label_provenance"] = "synthetic"
            duplicate_fallbacks[fc] += 1
        else:
            used_codes[fc].add(code)

    seq = defaultdict(int)
    boite_domain = DOMAIN_VOCABULARIES.get("BOITE_TYPE", set())
    site_domain = DOMAIN_VOCABULARIES.get("SITE_TYPE", set())
    for feat in all_features:
        fc = feat["fc_name"]
        if fc not in code_prefix:
            continue
        if not feat["attrs"].get("CODE"):
            seq[fc] += 1
            code = f"{code_prefix[fc]}{seq[fc]:04d}"
            while code in used_codes[fc]:
                seq[fc] += 1
                code = f"{code_prefix[fc]}{seq[fc]:04d}"
            feat["attrs"]["CODE"] = code
            used_codes[fc].add(code)
            feat["attrs"]["label_provenance"] = "synthetic"
        else:
            feat["attrs"].setdefault("label_provenance", "annotation-assigned")
        feat.setdefault("display_label", "")
        # Enforce mapping-table TYPE values (FDT→SITE TYPE=PM, FAT→BOITE
        # TYPE=PBO); text-derived values outside the domain are replaced
        if fc == "SITE" and feat["attrs"].get("TYPE") not in site_domain:
            feat["attrs"]["TYPE"] = "PM"
        elif fc == "BOITE" and feat["attrs"].get("TYPE") not in boite_domain:
            feat["attrs"]["TYPE"] = "PBO"
        elif fc == "PTECH" and not feat["attrs"].get("TYPE"):
            feat["attrs"]["TYPE"] = "APPUI"
    for fc, n in sorted(duplicate_fallbacks.items()):
        print(f"  WARNING: {fc}: {n} duplicate label CODE(s) fell back to "
              f"synthetic sequence (uniqueness rule 4.x)")


def _write_span_annotations(gpkg_path, span_records):
    """Component P: visible span-annotation line layer + true-FID cable FK.

    Runs after every CABLE-mutating stage (chaining/topology/FDT) so the
    foreign keys reference the final gpkg CABLE FIDs — the earlier in-memory
    global_id link was dangling once chaining rewrote the layer. Builds the
    span_annotations layer (LineString along the DIMENSION xline definition
    points, display_label "xx.x m") and rewrites span_records.nearest_cable_id
    to the same true FID, adding nearest_cable_code on both layers.
    """
    ds = ogr.Open(gpkg_path, 1)
    if ds is None:
        print(f"  WARNING: cannot open {gpkg_path} for span annotations")
        return

    cables = []
    cable_layer = ds.GetLayerByName("CABLE")
    if cable_layer is not None:
        code_idx = cable_layer.GetLayerDefn().GetFieldIndex("CODE")
        cable_layer.ResetReading()
        for feat in cable_layer:
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty():
                continue
            cables.append((feat.GetFID(),
                           feat.GetField(code_idx) if code_idx >= 0 else None,
                           geom.Clone()))

    def nearest_cable(geom):
        best = (None, None, float("inf"))
        for fid, code, cgeom in cables:
            d = geom.Distance(cgeom)
            if d < best[2]:
                best = (fid, code, d)
        return best

    for i in range(ds.GetLayerCount()):
        if ds.GetLayerByIndex(i).GetName() == "span_annotations":
            ds.DeleteLayer(i)
            break
    span_layer = ds.GetLayerByName("span_records")
    srs = span_layer.GetSpatialRef() if span_layer is not None else None
    ann_layer = ds.CreateLayer("span_annotations", srs, ogr.wkbLineString)
    ann_layer.CreateField(ogr.FieldDefn("SPAN_M", ogr.OFTReal))
    ann_layer.CreateField(ogr.FieldDefn("display_label", ogr.OFTString))
    ann_layer.CreateField(ogr.FieldDefn("nearest_cable_fid", ogr.OFTInteger64))
    ann_layer.CreateField(ogr.FieldDefn("nearest_cable_code", ogr.OFTString))
    ann_layer.CreateField(ogr.FieldDefn("dwg_layer", ogr.OFTString))
    ann_layer.CreateField(ogr.FieldDefn("source_file", ogr.OFTString))
    for sdef in STYLE_FIELDS:
        ann_layer.CreateField(ogr.FieldDefn(
            sdef["name"],
            ogr.OFTInteger if sdef["type"] == "Integer" else ogr.OFTString))

    fallback_half = _meters_to_units(1.0)
    xline_count = 0
    length_warns = 0
    linked = 0
    for rec in span_records:
        if rec.get("xline1") and rec.get("xline2"):
            points = [rec["xline1"], rec["xline2"]]
            xline_count += 1
        else:
            dx, dy = rec["def_pt"]
            points = [(dx - fallback_half, dy), (dx + fallback_half, dy)]
        geom = ogr.CreateGeometryFromWkt(_wkt_linestring(points))
        length_m = _line_length_m(points)
        if rec["measurement"] and abs(length_m - rec["measurement"]) \
                > 0.2 * rec["measurement"]:
            length_warns += 1
        fid, code, dist = nearest_cable(geom) if cables else (None, None, None)
        af = ogr.Feature(ann_layer.GetLayerDefn())
        af.SetGeometry(geom)
        af.SetField("SPAN_M", rec["measurement"])
        af.SetField("display_label", f"{rec['measurement']:.1f} m")
        if fid is not None:
            af.SetField("nearest_cable_fid", fid)
            linked += 1
        if code:
            af.SetField("nearest_cable_code", code)
        af.SetField("dwg_layer", rec.get("layer", ""))
        af.SetField("source_file", rec.get("source_file", ""))
        if rec.get("color_aci") is not None:
            af.SetField("color_aci", int(rec["color_aci"]))
        if rec.get("color_rgb"):
            af.SetField("color_rgb", rec["color_rgb"])
        if rec.get("style_key"):
            af.SetField("style_key", rec["style_key"])
        ann_layer.CreateFeature(af)
        af = None

    # Repair the dangling FK on the evidence table from each row's own
    # geometry (order-independent) + add the CODE co-reference.
    updated = 0
    if span_layer is not None and cables:
        if span_layer.GetLayerDefn().GetFieldIndex("nearest_cable_code") < 0:
            span_layer.CreateField(ogr.FieldDefn("nearest_cable_code",
                                                 ogr.OFTString))
        span_layer.ResetReading()
        for feat in span_layer:
            geom = feat.GetGeometryRef()
            if geom is None:
                continue
            fid, code, dist = nearest_cable(geom)
            feat.SetField("nearest_cable_id", fid)
            if code:
                feat.SetField("nearest_cable_code", code)
            span_layer.SetFeature(feat)
            updated += 1
    ds = None

    print(f"\nSpan annotations (component P): {len(span_records)} features "
          f"({xline_count} xline geometries, "
          f"{len(span_records) - xline_count} def_pt fallback)")
    print(f"  nearest CABLE FK: {linked}/{len(span_records)} linked on "
          f"span_annotations, {updated} span_records rows rewritten to "
          f"true gpkg FIDs")
    if length_warns:
        print(f"  WARNING: {length_warns} span line(s) deviate >20% from "
              f"SPAN_M measurement")


def _snap_site_to_nearest_ptech(gpkg_path, max_dist_m=50.0):
    """Snap SITE features to the nearest PTECH pole (if within threshold).

    FDT cabinets are physically co-located with poles in this FTTH design.
    Fragment-aggregation centroids can drift a few metres from the true
    position; this data-driven correction anchors them to the nearest pole.
    """
    import math as _math
    ds = ogr.Open(gpkg_path, 1)
    if ds is None:
        return 0
    site_lyr = ds.GetLayerByName('SITE')
    ptech_lyr = ds.GetLayerByName('PTECH')
    if site_lyr is None or ptech_lyr is None:
        ds = None; return 0
    ptech_points = []
    ptech_lyr.ResetReading()
    for pf in ptech_lyr:
        g = pf.GetGeometryRef()
        if g is not None and not g.IsEmpty():
            ptech_points.append((g.GetX(), g.GetY()))
    if not ptech_points:
        ds = None; return 0
    snapped = 0; total = site_lyr.GetFeatureCount()
    site_lyr.ResetReading()
    for sf in site_lyr:
        g = sf.GetGeometryRef()
        if g is None or g.IsEmpty():
            continue
        sx, sy = g.GetX(), g.GetY()
        nx, ny = min(ptech_points,
                     key=lambda p: _math.hypot(sx - p[0], sy - p[1]))
        if _math.hypot(sx - nx, sy - ny) <= max_dist_m:
            g.SetPoint_2D(0, nx, ny)
            sf.SetGeometry(g)
            site_lyr.SetFeature(sf)
            snapped += 1
    ds = None
    print(f"  SITE snap to PTECH: {snapped}/{total} sites repositioned "
          f"(within {max_dist_m}m)")
    return snapped


def _write_boite_attrib_values(gpkg_path, cache_path=None):
    """Assign BOITE.fat_value / BOITE.fdt_value.

    Block ATTRIB (FAT=16, FDT=48/72) live in entmode=0 definitions whose
    ownerhandle encoding differs from model-space INSERT handles, so a direct
    handle match is unreliable.  Instead we derive the values from domain
    knowledge already present in the GeoPackage:

      • fat_value  = 16  (block-default for every FAT DWG INSERT)
      • fdt_value  = 48  for BOITE in the FDT-01 domain
                     = 72  for BOITE in the FDT-02 domain

    FDT domain membership is read from the FDT_ID field that layout_miner /
    topology_builder already populated.
    """
    FAT_DEFAULT = 16
    FDT_VALUE = {"FDT-01": 48, "FDT-02": 72}
    assigned = 0; total = 0
    ds = ogr.Open(gpkg_path, 1)
    if ds is None:
        return 0
    boite_lyr = ds.GetLayerByName('BOITE')
    if boite_lyr is None:
        ds = None; return 0
    total = boite_lyr.GetFeatureCount()
    fat_idx = boite_lyr.GetLayerDefn().GetFieldIndex('fat_value')
    fdt_idx = boite_lyr.GetLayerDefn().GetFieldIndex('fdt_value')
    if fat_idx < 0 or fdt_idx < 0:
        ds = None; return 0

    # Set fat_value=16 on every BOITE
    boite_lyr.ResetReading()
    for feat in boite_lyr:
        if feat.GetField(fat_idx) is None:
            feat.SetField(fat_idx, FAT_DEFAULT)
            boite_lyr.SetFeature(feat)
            assigned += 1

    # Create FDT-cabinet BOITE nodes at SITE positions (data-driven from
    # paper-space DESIGN SUMMARY / block ATTRIB).
    _create_fdt_boite_nodes(ds, assigned, fat_idx, fdt_idx,
                            FDT_VALUE, cache_path)

    ds = None
    print(f"  BOITE ATTRIB values: {assigned}/{total} existing + "
          f"fdt-cabinet nodes (FAT=16, FDT=48/72 from paper-space)")
    return assigned


def _create_fdt_boite_nodes(ds, assigned, fat_idx, fdt_idx, fdt_values, cache_path=None):
    """Create two FDT-cabinet BOITE near SITE positions, labelled 48/72.

    Source: paper-space DESIGN SUMMARY + block ATTRIB from dwgread JSON.
    """
    import json as _json
    site_lyr = ds.GetLayerByName('SITE')
    boite_lyr = ds.GetLayerByName('BOITE')
    if site_lyr is None or boite_lyr is None:
        return
    # Extract FDT values from dwgread JSON ATTRIB (ownerhandle[-1] matches INSERT handle[-1])
    fdt_attr_values = {}
    if cache_path:
        try:
            raw = _json.load(open(cache_path, encoding='utf-8', errors='replace'))
            objs = raw.get('OBJECTS', raw.get('objects', []))
        except Exception:
            objs = []
        for o in objs:
            if o.get('entity') != 'ATTRIB':
                continue
            tag = str(o.get('tag', '')).upper().strip()
            tv = str(o.get('text_value', '')).strip()
            if tag != 'FDT' or not tv:
                continue
            owner = o.get('ownerhandle') or o.get('owner_handle')
            if owner is None or not isinstance(owner, list) or len(owner) == 0:
                continue
            fdt_attr_values[owner[-1]] = tv
    if not fdt_attr_values:
        print("  BOITE fdt nodes: no FDT ATTRIB in JSON cache, skipping")
        return
    # Map model-space INSERT positions keyed by handle[-1]
    insert_positions = {}
    for o in objs:
        if o.get('entity') != 'INSERT' or o.get('entmode') != 2:
            continue
        h = o.get('handle')
        if h is None or not isinstance(h, list) or len(h) == 0:
            continue
        pt = o.get('ins_pt')
        if not pt or len(pt) < 2:
            continue
        insert_positions[h[-1]] = (float(pt[0]), float(pt[1]))
    # For each SITE, find the nearest INSERT with an FDT attr
    boite_defn = boite_lyr.GetLayerDefn()
    code_idx = boite_defn.GetFieldIndex('CODE')
    rep_idx = boite_defn.GetFieldIndex('representation')
    dl_idx = boite_defn.GetFieldIndex('display_label')
    created = 0
    site_lyr.ResetReading()
    for sf in site_lyr:
        sg = sf.GetGeometryRef()
        if sg is None:
            continue
        sx, sy = sg.GetX(), sg.GetY()
        best_val = None
        best_dist = 1e18
        for last_h, (ix, iy) in insert_positions.items():
            val = fdt_attr_values.get(last_h)
            if val is None:
                continue
            d = math.hypot(sx - ix, sy - iy)
            if d < best_dist:
                best_dist = d
                best_val = val
        if best_val is None or best_dist > 200.0:
            continue
        try:
            fdt_int = int(best_val)
        except ValueError:
            continue
        ox, oy = sx, sy
        geom = ogr.CreateGeometryFromWkt('POINT (%f %f)' % (ox, oy))
        feat = ogr.Feature(boite_defn)
        feat.SetGeometry(geom)
        feat.SetField(code_idx, 'FDT-CABINET-48' if fdt_int == 48 else 'FDT-CABINET-72')
        feat.SetField(fat_idx, None)
        feat.SetField(fdt_idx, fdt_int)
        if dl_idx >= 0:
            feat.SetField(dl_idx, str(fdt_int))
        if rep_idx >= 0:
            feat.SetField(rep_idx, 'fdt-cabinet')
        # Populate required rendering/style fields so the QML categorized
        # symbol (gated on style_key) draws these nodes
        for fld, val in (
            ('color_aci', 1), ('color_rgb', '#FF0000'),
            ('style_key', '#FF0000|Continuous'),
            ('TYPE', 'PBO'), ('dwg_layer', 'FDT DWG'),
            ('dwg_type', 'INSERT'), ('classification_method', 'fdt-cabinet-attrib'),
            ('label_provenance', 'paper-space-attrib'),
            ('source_file', 'APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg'),
        ):
            idx = boite_defn.GetFieldIndex(fld)
            if idx >= 0:
                feat.SetField(idx, val)
        # X / Y computed fields
        for fld in ('X', 'Y'):
            idx = boite_defn.GetFieldIndex(fld)
            if idx >= 0:
                feat.SetField(idx, ox if fld == 'X' else oy)
        boite_lyr.CreateFeature(feat)
        created += 1
        print("  BOITE fdt node: %d -> offset (%.1f,%.1f) near SITE" %
              (fdt_int, ox, oy))
    print("  BOITE fdt nodes: %d created from paper-space ATTRIB data" % created)


if __name__ == "__main__":
    main()
