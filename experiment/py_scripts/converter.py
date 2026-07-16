#!/usr/bin/env python3
"""
GeoFormer Phase 2: DWG-to-GeoPackage Converter
==============================================
Reads DWG files with native WGS84 coordinates (EPSG:4326) and writes a single
GeoPackage with 8 FTTH feature class layers.

Strict identity transform — no coordinate offset, no reprojection.
Implements the simplified GeoFormer pipeline: DWG Reader, INSERT handler,
two-tier classification, geometry reconstruction, attribute extraction,
and GeoPackage writer.

Input:  DWG X = longitude, DWG Y = latitude (WGS84 native)
Output: Single GeoPackage (EPSG:4326), one layer per FTTH feature class
"""

import argparse
import ctypes
import hashlib
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
        DOMAIN_VOCABULARIES,
        FIELD_NAME_CROSSWALK,
        BOITE, CABLE, PTECH, INFRASTRUCTURE_FC, SITE, ZNRO, ZPM, IMB,
        FEATURE_CLASS_BY_NAME,
    )
except ImportError:
    sys.exit(
        "ERROR: Cannot import schema_config. Ensure this file is in the same "
        "directory as schema_config.py."
    )

from domain_vocab import validate_domain_value
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
    _libc.free.argtypes = [ctypes.c_void_p]
    return _libdwg, _libc


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
# Worldwide WGS84 geographic bounds (no region-specific filtering)
WORLD_LAT_MIN, WORLD_LAT_MAX = -90.0, 90.0
WORLD_LON_MIN, WORLD_LON_MAX = -180.0, 180.0

# Minimum EPSG:3857 X coordinate for real geographic data (~1° longitude at equator).
# Entities with |X| below this in EPSG:3857 are paper-space/layout artifacts.
EPSG3857_MIN_REAL_X = 100000.0

# Geographic outlier flag threshold — entities outside their deployment region
# are flagged for review but never discarded. Region bounds may be set via
# --region-bounds lat_min,lat_max,lon_min,lon_max CLI flag at runtime.
DEFAULT_REGION_BOUNDS = None  # None = no region filtering; worldwide

# Source CRS transform (set in main() from --source-crs)
# Default: EPSG:4326 (WGS84 identity — worldwide)
_CRS_TRANSFORM = None  # osr.CoordinateTransformation or None for identity

def _reproject_point(x, y):
    """Reproject a single coordinate from source CRS to EPSG:4326.
    Returns (lon, lat) in traditional GIS order."""
    if _CRS_TRANSFORM is None:
        return x, y
    try:
        pt = _CRS_TRANSFORM.TransformPoint(x, y)
        # GDAL 3+ returns (lat, lon) for EPSG:4326 per official axis order.
        # The converter uses traditional GIS (lon, lat). Swap if needed.
        lon, lat = pt[0], pt[1]
        if abs(lat) > 90:  # lat > 90 means axis order is (lat, lon) — swap
            lon, lat = lat, lon
        return lon, lat
    except Exception:
        return x, y

def _reproject_points(points):
    """Reproject a list of (x, y) tuples from source CRS to EPSG:4326."""
    if _CRS_TRANSFORM is None:
        return points
    return [_reproject_point(p[0], p[1]) for p in points]

# DWG type constants (from LibreDWG SWIG)
DWG_TYPE_LINE = 0
DWG_TYPE_LWPOLYLINE = 19
DWG_TYPE_CIRCLE = 8
DWG_TYPE_ARC = 7
DWG_TYPE_TEXT = 1
DWG_TYPE_MTEXT = 44
DWG_TYPE_INSERT = 7   # Will be corrected after LibreDWG import
DWG_TYPE_POINT = 2
DWG_TYPE_CIRCLE_R11 = 3    # Legacy R11 circle
DWG_TYPE_POINT_R11 = 2     # Legacy R11 point (same code as modern POINT)
DWG_TYPE_TEXT_R11 = 7      # Legacy R11 text (same code as modern INSERT)
DWG_SUPERTYPE_ENTITY = 0

# Telecom block name patterns for INSERT recognition (English, worldwide)
INSERT_BLOCK_PATTERN = re.compile(
    r"(?i)(chamber|box|fat|fdt|closure|manhole|handhole|nro|pm|shelter|cabinet|"
    r"pedestal|vault|splice|splitter|patch|panel|terminal|pole|anchor|guy)",
)

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


def _swap_geom_coords(geom):
    """Swap x/y on an OGR geometry in-place to convert (lat,lon) → (lon,lat)."""
    gname = geom.GetGeometryName()
    if gname in ('POINT',):
        x, y, z = geom.GetX(), geom.GetY(), geom.GetZ()
        geom.SetPoint(0, y, x, z)
    elif gname in ('LINESTRING', 'LINEARRING'):
        for j in range(geom.GetPointCount()):
            x, y, z = geom.GetX(j), geom.GetY(j), geom.GetZ(j)
            geom.SetPoint(j, y, x, z)
    elif gname in ('POLYGON',):
        for k in range(geom.GetGeometryCount()):
            _swap_geom_coords(geom.GetGeometryRef(k))
    elif gname in ('MULTIPOLYGON', 'MULTILINESTRING', 'MULTIPOINT', 'GEOMETRYCOLLECTION'):
        for k in range(geom.GetGeometryCount()):
            _swap_geom_coords(geom.GetGeometryRef(k))


def _adaptive_chord_tolerance(extent):
    """Clamp chord tolerance: extent*0.001, bounded to [1e-6, 0.01] degrees."""
    tol = extent * 0.001
    return max(1e-6, min(0.01, tol))


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


def _wkt_linearring(points):
    coords = ", ".join("%.12f %.12f" % (p[0], p[1]) for p in points)
    return "LINEARRING (%s)" % coords


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
                pts_closed = pts + [pts[0]]
                wkt = _wkt_linearring(pts_closed)
            else:
                wkt = _wkt_linestring(pts)
            points = pts
            cx, cy = _centroid(pts)

        elif dwg_type == DWG_TYPE_CIRCLE:
            c = entity.tio.CIRCLE
            points = _circle_points(c.center.x, c.center.y, c.radius, chord_tol)
            wkt = _wkt_polygon_exterior(points)
            is_closed = True
            cx, cy = c.center.x, c.center.y

        elif dwg_type == DWG_TYPE_CIRCLE_R11:
            c = entity.tio.CIRCLE
            points = _circle_points(c.center.x, c.center.y, c.radius, chord_tol)
            wkt = _wkt_polygon_exterior(points)
            is_closed = True
            cx, cy = c.center.x, c.center.y

        elif dwg_type == DWG_TYPE_ARC:
            ar = entity.tio.ARC
            points = _arc_points(ar.center.x, ar.center.y, ar.radius,
                                ar.start_angle, ar.end_angle, chord_tol)
            wkt = _wkt_linestring(points)
            cx, cy = _centroid(points)

        elif dwg_type in (DWG_TYPE_TEXT, DWG_TYPE_MTEXT, DWG_TYPE_INSERT, DWG_TYPE_POINT, DWG_TYPE_POINT_R11):
            if dwg_type == DWG_TYPE_TEXT:
                t = entity.tio.TEXT
                cx, cy = t.ins_pt.x, t.ins_pt.y
            elif dwg_type == DWG_TYPE_MTEXT:
                mt = entity.tio.MTEXT
                cx, cy = mt.ins_pt.x, mt.ins_pt.y
            elif dwg_type == DWG_TYPE_INSERT:
                # Type 7: try INSERT first, fall back to TEXT_r11
                try:
                    ins = entity.tio.INSERT
                    cx, cy = ins.ins_pt.x, ins.ins_pt.y
                except Exception:
                    t = entity.tio.TEXT
                    cx, cy = t.ins_pt.x, t.ins_pt.y
            elif dwg_type in (DWG_TYPE_POINT, DWG_TYPE_POINT_R11):
                p = entity.tio.POINT
                cx, cy = p.x, p.y
            points = [(cx, cy)]
            wkt = _wkt_point(cx, cy)

    except Exception:
        pass

    return (wkt, points, (cx, cy), is_closed)


# ── INSERT / BLOCK Handling ──────────────────────────────────────────────

def _get_block_name(entity):
    """Extract block name from an INSERT entity."""
    try:
        block_header = entity.tio.INSERT.block_header
        if block_header and block_header.name:
            return _cstr(block_header.name)
    except Exception:
        pass
    return ""


def _is_telecom_block(block_name):
    """Check if a block name matches a known telecom pattern."""
    return bool(INSERT_BLOCK_PATTERN.search(block_name))


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
        attrs["TYPE_CABLE"] = m.group(1).upper()

    return attrs


def _link_annotations_to_geometries(annotations, features, sigma_degrees=0.0001):
    """
    Link TEXT annotation entities to the nearest geometry entity within sigma distance.
    Merges annotation attributes into the target feature dict.
    sigma_degrees ~ 11m at equator — tunable.
    """
    linked = set()
    for ann in annotations:
        ax, ay = ann["centroid"]
        best_dist = float("inf")
        best_feat = None
        for idx, feat in enumerate(features):
            if idx in linked:
                continue
            fx, fy = feat["centroid"]
            dist = math.sqrt((ax - fx) ** 2 + (ay - fy) ** 2)
            if dist < best_dist and dist < sigma_degrees:
                best_dist = dist
                best_feat = feat
        if best_feat is not None:
            for k, v in ann["attrs"].items():
                if k not in best_feat["attrs"] or best_feat["attrs"][k] is None:
                    best_feat["attrs"][k] = v
            if ann["text"] and not best_feat.get("annotation_text"):
                best_feat["annotation_text"] = ann["text"]

    # Return any unlinked annotations as standalone features
    unlinked = [a for i, a in enumerate(annotations) if i not in linked]
    return unlinked


# ── DWG Reader ────────────────────────────────────────────────────────────

def read_dwg(dwg_path):
    """
    Read a DWG file and extract entities with classification and geometry.
    Returns a list of feature dicts.
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
            DWG_TYPE_CIRCLE_r11 as L_CIRCLE_R11,
            DWG_TYPE_POINT_r11 as L_POINT_R11,
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
    global DWG_TYPE_CIRCLE_R11, DWG_TYPE_POINT_R11
    global DWG_SUPERTYPE_ENTITY
    DWG_TYPE_LINE = L_LINE
    DWG_TYPE_LWPOLYLINE = L_LWPOLYLINE
    DWG_TYPE_CIRCLE = L_CIRCLE
    DWG_TYPE_ARC = L_ARC
    DWG_TYPE_TEXT = L_TEXT
    DWG_TYPE_MTEXT = L_MTEXT
    DWG_TYPE_INSERT = L_INSERT
    DWG_TYPE_POINT = L_POINT
    DWG_TYPE_CIRCLE_R11 = L_CIRCLE_R11
    DWG_TYPE_POINT_R11 = L_POINT_R11
    DWG_SUPERTYPE_ENTITY = L_SUPERTYPE_ENTITY

    # Build type names map
    import LibreDWG
    type_names = {}
    for name in dir(LibreDWG):
        if name.startswith("DWG_TYPE_"):
            type_names[getattr(LibreDWG, name)] = name[9:]

    geo_types = {DWG_TYPE_LINE, DWG_TYPE_LWPOLYLINE, DWG_TYPE_CIRCLE, DWG_TYPE_ARC, DWG_TYPE_CIRCLE_R11}
    point_types = {DWG_TYPE_TEXT, DWG_TYPE_MTEXT, DWG_TYPE_INSERT, DWG_TYPE_POINT, DWG_TYPE_POINT_R11}

    print(f"Reading: {dwg_path}")
    data = Dwg_Data()
    data.object = new_Dwg_Object_Array(500000)
    err = dwg_read_file(dwg_path, data)
    print(f"  LibreDWG exit code: {err}, objects: {data.num_objects}")

    # Compute global extent for chord tolerance
    global_x_min, global_x_max = float("inf"), float("-inf")
    global_y_min, global_y_max = float("inf"), float("-inf")
    raw_features = []
    annotations = []

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

        # Paper-space filter via first-pass coordinate check
        skip = False
        if dwg_type in geo_types:
            pass
        elif dwg_type in point_types:
            cx, cy = 0.0, 0.0
            try:
                if dwg_type == DWG_TYPE_TEXT:
                    cx, cy = entity.tio.TEXT.ins_pt.x, entity.tio.TEXT.ins_pt.y
                elif dwg_type == DWG_TYPE_MTEXT:
                    cx, cy = entity.tio.MTEXT.ins_pt.x, entity.tio.MTEXT.ins_pt.y
                elif dwg_type == DWG_TYPE_INSERT:
                    # Type 7: could be modern INSERT or legacy TEXT_r11
                    try:
                        cx, cy = entity.tio.INSERT.ins_pt.x, entity.tio.INSERT.ins_pt.y
                    except Exception:
                        cx, cy = entity.tio.TEXT.ins_pt.x, entity.tio.TEXT.ins_pt.y
                elif dwg_type == DWG_TYPE_POINT:
                    cx, cy = entity.tio.POINT.x, entity.tio.POINT.y
                elif dwg_type == DWG_TYPE_POINT_R11:
                    cx, cy = entity.tio.POINT.x, entity.tio.POINT.y
            except Exception:
                continue
            # Reproject before paper-space check
            rcx, rcy = _reproject_point(cx, cy)
            if abs(rcx) > 180 or abs(rcy) > 90:
                skip = True
        if skip:
            continue

        # Extract text
        text_val = ""
        try:
            if dwg_type == DWG_TYPE_TEXT:
                text_val = entity.tio.TEXT.text_value or ""
            elif dwg_type == DWG_TYPE_MTEXT:
                text_val = entity.tio.MTEXT.text or ""
            elif dwg_type == DWG_TYPE_INSERT:
                # Type 7: try TEXT_r11 text first (legacy), fall back to INSERT attribute
                try:
                    tv = entity.tio.TEXT.text_value
                    if tv:
                        text_val = tv
                except Exception:
                    pass
        except Exception:
            pass

        # INSERT/BLOCK handling
        is_insert_node = False
        is_text_r11 = False
        if dwg_type == DWG_TYPE_INSERT:
            # Type 7: distinguish INSERT from TEXT_r11
            # TEXT_r11 has text content; INSERT has block_header
            if text_val:
                is_text_r11 = True
            else:
                block_name = _get_block_name(entity)
                if _is_telecom_block(block_name):
                    is_insert_node = True
                else:
                    continue  # skip unrecognized blocks

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
            "is_insert_node": is_insert_node,
            "is_text_r11": is_text_r11,
            "block_name": _get_block_name(entity) if dwg_type == DWG_TYPE_INSERT and not is_text_r11 else "",
        })

    # Compute extent for chord tolerance
    if global_x_min == float("inf"):
        extent = 1.0
    else:
        extent = max(global_x_max - global_x_min, global_y_max - global_y_min, 0.001)

    # Process geometries
    features = []
    for i, rf in enumerate(raw_features):
        wkt, pts, (cx, cy), is_closed = _extract_wkt(
            rf["entity"], rf["dwg_type"], extent
        )
        if not wkt:
            continue

        # Reproject from source CRS → EPSG:4326 using OGR transform
        if _CRS_TRANSFORM is not None and wkt:
            try:
                geom = ogr.CreateGeometryFromWkt(wkt)
                if geom:
                    geom.Transform(_CRS_TRANSFORM)
                    # GDAL 3+ returns EPSG:4326 in (lat, lon) axis order.
                    # Swap to traditional GIS (lon, lat) for the converter.
                    _swap_geom_coords(geom)
                    wkt = geom.ExportToWkt()
                    # Update pts and centroid from transformed geometry
                    if geom.GetGeometryName() in ('POINT',):
                        cx, cy = geom.GetX(), geom.GetY()
                        pts = [(cx, cy)]
                    elif geom.GetGeometryName() in ('LINESTRING', 'LINEARRING'):
                        pts = [(geom.GetX(j), geom.GetY(j))
                               for j in range(geom.GetPointCount())]
                        cx = sum(p[0] for p in pts) / len(pts)
                        cy = sum(p[1] for p in pts) / len(pts)
                    elif geom.GetGeometryName() in ('POLYGON', 'MULTIPOLYGON'):
                        ring = (geom.GetGeometryRef(0) if geom.GetGeometryName() == 'POLYGON'
                                else geom.GetGeometryRef(0).GetGeometryRef(0))
                        pts = [(ring.GetX(j), ring.GetY(j))
                               for j in range(ring.GetPointCount())]
                        c = geom.Centroid()
                        cx, cy = c.GetX(), c.GetY()
                    else:
                        c = geom.Centroid()
                        cx, cy = c.GetX(), c.GetY()
            except Exception:
                pass

        # Geo-coordinate filter (applied AFTER reprojection)
        if abs(cx) > 180 or abs(cy) > 90:
            continue

        # Suspicious-span filter: entities spanning >1.0 degree (~111km) are
        # almost certainly DWG sheet borders/frames, not real telecom features
        if pts and len(pts) >= 2:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            span_x = max(xs) - min(xs)
            span_y = max(ys) - min(ys)
            span_deg = max(span_x, span_y)
            rf["suspicious_span"] = span_deg > 1.0
        else:
            rf["suspicious_span"] = False

        # Geographic bounds filter: discard features outside Indonesia region.
        # Indonesia spans lat -11° to 7°N, lon 95° to 141°E in EPSG:4326.
        # cx=lon, cy=lat after reprojection.
        if not (-11 <= cy <= 7 and 95 <= cx <= 141):
            continue

        # Classification: TEXT entities → annotation (not geometry)
        # TEXT_r11 has type 7 (same as INSERT) distinguished by is_text_r11 flag
        is_text_entity = (rf["dwg_type"] in (DWG_TYPE_TEXT, DWG_TYPE_MTEXT) or rf.get("is_text_r11"))
        if is_text_entity and not rf["is_insert_node"]:
            attrs = _extract_attributes(rf["text"], None)
            annotations.append({
                "text": rf["text"],
                "centroid": (cx, cy),
                "attrs": attrs,
                "layer": rf["layer"],
            })
            continue

        # Tier 1 + Tier 2 classification
        fc_name, fc_geom_type, confidence, method = _assign_fc(rf["layer"], rf["text"])

        # INSERT nodes always classified as their matching FC
        if rf["is_insert_node"]:
            fc_name = _classify_insert_block(rf["block_name"])

        feat = {
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
            "text": rf["text"],
            "annotation_text": "",
            "attrs": _extract_attributes(rf["text"], fc_name),
            "is_insert_node": rf["is_insert_node"],
            "geographic_outlier": not (
                WORLD_LAT_MIN <= cy <= WORLD_LAT_MAX and
                WORLD_LON_MIN <= cx <= WORLD_LON_MAX
            ),
        }
        features.append(feat)

        if (i + 1) % 50000 == 0:
            print(f"  ... {i + 1} objects processed")

    # Link annotations to geometry features
    unlinked = _link_annotations_to_geometries(annotations, features)

    # Add unlinked annotations as fc_misc features
    for ann in unlinked:
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
            "classification_method": "unlinked_annotation",
            "text": ann["text"],
            "annotation_text": "",
            "attrs": ann["attrs"],
            "is_insert_node": False,
            "geographic_outlier": not (
                WORLD_LAT_MIN <= ay <= WORLD_LAT_MAX and
                WORLD_LON_MIN <= ax <= WORLD_LON_MAX
            ),
        })

    print(f"  Extracted: {len(features)} features, {len(annotations)} annotations")
    return features


def _classify_insert_block(block_name):
    """Map INSERT block name to FTTH feature class (English, worldwide)."""
    bl = block_name.lower()
    if any(k in bl for k in ("chamber", "manhole", "handhole", "pole", "anchor",
                              "guy", "vault", "trench")):
        return "PTECH"
    if any(k in bl for k in ("box", "closure", "fat", "fdt", "cto", "nap",
                              "dp", "mdu", "sdu", "ont", "pedestal", "cabinet",
                              "splice", "splitter", "patch", "panel", "terminal")):
        return "BOITE"
    if any(k in bl for k in ("nro", "pm", "co", "exchange", "hub", "pop",
                              "shelter", "node")):
        return "SITE"
    return "PTECH"


# ── Feature Class Geometry Resolution ─────────────────────────────────────

# Maps FC name to geometry resolution rules
FC_GEOM_RESOLVE = {
    "BOITE": ("Point", False),
    "PTECH": ("Point", False),
    "SITE": ("Point", False),
    "CABLE": ("LineString", False),
    "INFRASTRUCTURE": ("LineString", False),
    "ZNRO": ("Polygon", False),
    "ZPM": ("Polygon", False),
    "IMB": ("Polygon", False),  # Point → Polygon conversion below
}


def _resolve_fc_geometry(feat):
    """
    Resolve the output geometry type for a feature based on its assigned FC.
    Handles IMB special case: Point annotations become polygon candidates.
    """
    fc = feat["fc_name"]
    if fc not in FC_GEOM_RESOLVE:
        return feat["wkt"], feat["points"], feat["is_closed"]

    target_geom, _ = FC_GEOM_RESOLVE[fc]

    # IMB: points that could be polygons (closed LWPOLYLINE, CIRCLE)
    if fc == "IMB":
        if feat["is_closed"] and len(feat["points"]) >= 3:
            pts_closed = feat["points"]
            if pts_closed[0] != pts_closed[-1]:
                pts_closed = pts_closed + [pts_closed[0]]
            return _wkt_polygon_exterior(pts_closed), pts_closed, True
        # Point geometry stays as point
        return feat["wkt"], feat["points"], False

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
    """Compute haversine length for CABLE.LONGUEUR and INFRASTRUCTURE.LONGUEUR."""
    if fc_name in ("CABLE", "INFRASTRUCTURE"):
        return _haversine_length(points_list)
    return 0.0


def write_geopackage(output_path, all_features, source_files):
    """Write all features to a single GeoPackage with 8 FTTH layers + metadata."""
    driver = ogr.GetDriverByName("GPKG")
    if os.path.exists(output_path):
        driver.DeleteDataSource(output_path)
    ds = driver.CreateDataSource(output_path)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)

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

        feat_list = fc_features.get(fc_name, [])
        count = 0
        for fdata in feat_list:
            wkt_resolved, pts_resolved, _ = _resolve_fc_geometry(fdata)
            if not wkt_resolved:
                continue

            geom = ogr.CreateGeometryFromWkt(wkt_resolved)
            if geom is None:
                continue

            # Skip DWG border/sheet artifacts: features whose centroid is
            # exactly at sheet origin (0,0) or that form large axis-aligned
            # rectangles (>50° span) — these are layout frames, not telecom
            pts = fdata.get("points", [])
            if pts and len(pts) >= 2:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                span_x = max(xs) - min(xs)
                span_y = max(ys) - min(ys)
                # Border frames: span >50° in any axis
                if max(span_x, span_y) > 50.0:
                    continue
                # Sheet origin artifacts: feature endpoint at exactly (0.0, 0.0)
                # and span >10° — real telecom cables don't start at sheet origin
                has_origin = any(abs(p[0]) < 1e-9 and abs(p[1]) < 1e-9 for p in pts)
                if has_origin and max(span_x, span_y) > 10.0:
                    continue

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
                length_m = _haversine_length(fdata["points"])
                feature.SetField("LONGUEUR", length_m)

            # Metadata
            feature.SetField("source_file", fdata.get("source_file", ""))
            feature.SetField("dwg_layer", fdata.get("layer", ""))
            feature.SetField("dwg_type", fdata.get("dwg_type_name", ""))
            feature.SetField("classification_method", fdata.get("classification_method", ""))

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
    mf.SetField("crs", "EPSG:4326")
    mf.SetField("total_features", sum(layer_stats.values()))
    mf.SetField("misc_features", misc_count)
    manifest_layer.CreateFeature(mf)
    mf = None

    # transform_record
    transform_layer = ds.CreateLayer("transform_record", srs, ogr.wkbNone)
    transform_layer.CreateField(ogr.FieldDefn("operation", ogr.OFTString))
    transform_layer.CreateField(ogr.FieldDefn("detail", ogr.OFTString))

    for op_name, detail in [
        ("coordinate_offset", "None (identity, EPSG:4326 native)"),
        ("reprojection", "None (identity pass-through)"),
        ("crs_output", "EPSG:4326"),
        ("coordinate_filter", "|lat|<=90, |lon|<=180"),
        ("geographic_bounds_check", f"lat [{WORLD_LAT_MIN},{WORLD_LAT_MAX}], lon [{WORLD_LON_MIN},{WORLD_LON_MAX}]"),
        ("paper_space_filter", "|centroid_lat|>90 OR |centroid_lon|>180 → discard"),
        ("block_insert_handling", "Recognized telecom blocks → POINT; unrecognized → skip"),
        ("geometry_reconstruction", "Adaptive chord tolerance, WKT to OGR"),
        ("attribute_extraction", "English telecom keyword regex patterns"),
        ("classification", "Two-tier: layer regex + annotation keywords"),
    ]:
        tr = ogr.Feature(transform_layer.GetLayerDefn())
        tr.SetField("operation", op_name)
        tr.SetField("detail", detail)
        transform_layer.CreateFeature(tr)
        tr = None

    # qc_summary
    qc_layer = ds.CreateLayer("qc_summary", srs, ogr.wkbNone)
    qc_layer.CreateField(ogr.FieldDefn("layer", ogr.OFTString))
    qc_layer.CreateField(ogr.FieldDefn("feature_count", ogr.OFTInteger))
    qc_layer.CreateField(ogr.FieldDefn("geometry_type", ogr.OFTString))
    qc_layer.CreateField(ogr.FieldDefn("geographic_outliers", ogr.OFTInteger))
    qc_layer.CreateField(ogr.FieldDefn("classification_confidence_mean", ogr.OFTReal))

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
        qc_layer.CreateFeature(qr)
        qr = None

    ds = None
    return layer_stats, misc_count


# ── Main CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GeoFormer P2: DWG-to-GeoPackage converter (FTTH domain, EPSG:4326 native)",
    )
    parser.add_argument("--input", "-i", nargs="+", required=True,
                       help="One or more DWG files to convert")
    parser.add_argument("--output", "-o", required=True,
                       help="Output GeoPackage path (.gpkg)")
    parser.add_argument("--temp-dir", default="/tmp/geoformer",
                       help="Temporary directory (default: /tmp/geoformer)")
    parser.add_argument("--config", default=None,
                       help="Optional JSON config (not used; schema from schema_config.py)")
    parser.add_argument("--source-crs", default="EPSG:4326",
                       help="Source DWG CRS (default: EPSG:4326 = WGS84 identity). "
                            "Use 'EPSG:32629' for UTM zone 29N (Morocco legacy).")
    args = parser.parse_args()

    # Set up CRS transform
    global _CRS_TRANSFORM
    if args.source_crs and args.source_crs.upper() not in ("EPSG:4326", "4326", "NONE", "IDENTITY"):
        src = osr.SpatialReference()
        src.SetFromUserInput(args.source_crs)
        dst = osr.SpatialReference()
        dst.ImportFromEPSG(4326)
        _CRS_TRANSFORM = osr.CoordinateTransformation(src, dst)
        print(f"  Source CRS: {args.source_crs} → EPSG:4326 (reprojection enabled)")
    else:
        _CRS_TRANSFORM = None

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
    print(f"  CRS:    EPSG:4326 (identity transform)")
    print("=" * 60)

    all_features = []
    warnings_log = []

    for dwg_path in args.input:
        print(f"\nProcessing: {dwg_path}")
        features = read_dwg(dwg_path)
        all_features.extend(features)

        # Log warnings per file
        file_outliers = sum(1 for f in features if f.get("geographic_outlier"))
        file_misc = sum(1 for f in features if f["fc_name"] == "fc_misc")
        file_geom_outliers = sum(
            1 for f in features
            if abs(f["centroid"][0]) > 180 or abs(f["centroid"][1]) > 90
        )
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

    print(f"\nTotal features: {len(all_features)}")

    # Print warnings
    for w in warnings_log:
        print(f"  WARNING: {w}")

    # Write GeoPackage
    print(f"\nWriting GeoPackage: {args.output}")
    stats, misc_count = write_geopackage(args.output, all_features, args.input)

    print("\n" + "=" * 60)
    print("Conversion Summary:")
    for fc_name in ["BOITE", "CABLE", "PTECH", "INFRASTRUCTURE",
                     "SITE", "ZNRO", "ZPM", "IMB"]:
        print(f"  {fc_name:20s}: {stats.get(fc_name, 0):5d} features")
    print(f"  {'fc_misc':20s}: {misc_count:5d} features (unclassified)")
    print(f"  {'TOTAL':20s}: {sum(stats.values()) + misc_count:5d} features")
    print(f"\nOutput: {args.output}")
    print(f"  SHA256: {_sha256_file(args.output)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
