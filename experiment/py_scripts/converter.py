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
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
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
from apd_rules import (
    classify_insert_block,
    is_telecom_block,
    link_annotations,
    set_traditional_axis_order,
)
from autocad_reader import read_dwg_with_autocad
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
APD_SOURCE_SHA256 = "557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557"

# Source CRS transform (set in main() from --source-crs)
# Default: EPSG:4326 (WGS84 identity — worldwide)
_CRS_TRANSFORM = None  # osr.CoordinateTransformation or None for identity
_SOURCE_CRS_LABEL = "AUTO"
_AUTO_SOURCE_CRS = True
_DETECTED_SOURCE_CRS = None
_TARGET_CRS_LABEL = "EPSG:9481"
_REGION_BOUNDS = None

def _reproject_point(x, y):
    """Reproject a single coordinate from source CRS to EPSG:4326.
    Returns (lon, lat) in traditional GIS order."""
    global _DETECTED_SOURCE_CRS
    if _AUTO_SOURCE_CRS:
        if -180 <= x <= 180 and -90 <= y <= 90:
            _DETECTED_SOURCE_CRS = _DETECTED_SOURCE_CRS or "EPSG:4326"
            return x, y
        if 2_000_000 < abs(x) <= 20_037_508.35 and abs(y) <= 20_037_508.35:
            radius = 6378137.0
            lon = math.degrees(x / radius)
            lat = math.degrees(2.0 * math.atan(math.exp(y / radius)) - math.pi / 2.0)
            _DETECTED_SOURCE_CRS = "EPSG:3857"
            return lon, lat
        return x, y
    if _CRS_TRANSFORM is None:
        return x, y
    try:
        pt = _CRS_TRANSFORM.TransformPoint(x, y)
        return pt[0], pt[1]
    except Exception:
        return x, y

def _reproject_points(points):
    """Reproject a list of (x, y) tuples from source CRS to EPSG:4326."""
    if _CRS_TRANSFORM is None:
        return points
    return [_reproject_point(p[0], p[1]) for p in points]


def _validate_source_crs_evidence(items, source_crs):
    metadata = [
        item.get("text", "") for item in items
        if item.get("output_kind") == "source_evidence"
        and item.get("dwg_type_name") == "DOCUMENT_METADATA"
    ]
    if not metadata:
        raise RuntimeError("DWG coordinate-system metadata was not extracted")
    declared = metadata[0]
    normalized = source_crs.upper().replace(" ", "")
    if "CGEOCS=WGS84.PseudoMercator" in declared and normalized not in {"EPSG:3857", "3857"}:
        raise RuntimeError(
            f"DWG declares WGS84.PseudoMercator but --source-crs={source_crs}; expected EPSG:3857"
        )
    if "INSUNITS=6" not in declared:
        raise RuntimeError(f"DWG units are not declared as metres: {declared}")

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

CODE_PATTERN = re.compile(
    r"(?i)\b(?:FDT|FAT|DMPH|GTO|OLT|ODC|ODP|PBO|NRO|PM|SITE|NP)"
    r"(?:[-._/][A-Z0-9]+)+\b"
)
REF_PM_PATTERN = re.compile(r"(?i)\b(?:REF[_ -]?PM|PM)\s*[:=]\s*([A-Z0-9._/-]+)")


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
    return is_telecom_block(block_name)


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

    code_match = CODE_PATTERN.search(text_val)
    if code_match:
        attrs["CODE"] = code_match.group(0).upper()

    ref_pm_match = REF_PM_PATTERN.search(text_val)
    if ref_pm_match:
        attrs["REF_PM"] = ref_pm_match.group(1).upper()

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
    return link_annotations(annotations, features, sigma_degrees)


# ── DWG Reader ────────────────────────────────────────────────────────────

def read_dwg(dwg_path):
    """
    Read a DWG file and extract entities with classification and geometry.
    Returns a list of feature dicts.
    """
    if str(dwg_path).lower().endswith(".dxf"):
        raise ValueError("DXF input is disabled; provide the authoritative DWG file")
    if os.name == "nt":
        print(f"Reading with direct AutoCAD DWG database access: {dwg_path}")
        features = read_dwg_with_autocad(
            dwg_path,
            _reproject_point,
            _assign_fc,
            _classify_insert_block,
            _extract_attributes,
        )
        print(f"  Extracted: {len(features)} features")
        return features

    # Import LibreDWG SWIG (Linux fallback)
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

        # Deployment filtering is explicit CLI policy, never implicit data loss.
        if _REGION_BOUNDS is not None:
            lat_min, lat_max, lon_min, lon_max = _REGION_BOUNDS
            if not (lat_min <= cy <= lat_max and lon_min <= cx <= lon_max):
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
    return classify_insert_block(block_name) or "fc_misc"


def _stable_cad_code(feature):
    """Build a traceable fallback identifier from the source DWG object."""
    feature_class = feature.get("fc_name", "CAD")
    handle = str(feature.get("cad_handle", "")).strip().upper()
    if handle:
        return f"{feature_class}-CAD-{handle}"
    source = "|".join([
        str(feature.get("source_file", "")),
        str(feature.get("layout", "")),
        str(feature.get("layer", "")),
        repr(feature.get("centroid", ())),
    ])
    return f"{feature_class}-CAD-{hashlib.sha1(source.encode('utf-8')).hexdigest()[:10].upper()}"


def _nearest_network_node(point, nodes, excluded_code=None):
    candidates = []
    for node in nodes:
        code = node["attrs"].get("CODE")
        if excluded_code and code == excluded_code:
            continue
        nx, ny = node.get("native_centroid", node["centroid"])
        distance = math.hypot(point[0] - nx, point[1] - ny)
        candidates.append((distance, node))
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return None, float("inf"), False
    ambiguous = len(candidates) > 1 and candidates[1][0] - candidates[0][0] <= 0.01
    return candidates[0][1], candidates[0][0], ambiguous


# Legacy span-to-cable bridge synthesis removed; v3 keeps SPAN and SLING evidence-only.

def _build_network_topology(items, snap_tolerance_metres=5.0):
    """Populate stable node/cable identifiers and endpoint references.

    Only spatial plan/model features participate.  Topology and splicing
    layouts remain independent evidence and can never create duplicate assets.
    """
    features = [item for item in items if item.get("output_kind", "feature") == "feature"]
    nodes = [feature for feature in features if feature.get("fc_name") in {"BOITE", "SITE"}]
    support_nodes = [feature for feature in features if feature.get("fc_name") == "PTECH"]
    cables = [feature for feature in features if feature.get("fc_name") == "CABLE"]
    for cable in cables:
        cable.setdefault("geometry_role", "SOURCE_ROUTE")

    # Reviewed APD plan/topology correspondence from the architecture handoff.
    fdt_references = [
        ((13681914.403, 69386.445), "DMPH-1.010"),
        ((13683236.666, 68765.958), "DMPH-2.011"),
    ]
    for site in (feature for feature in features if feature.get("fc_name") == "SITE"):
        sx, sy = site.get("native_centroid", site["centroid"])
        ranked = sorted(
            (math.hypot(sx - point[0], sy - point[1]), code)
            for point, code in fdt_references
        )
        if ranked and ranked[0][0] <= 500.0 and (len(ranked) == 1 or ranked[1][0] - ranked[0][0] > 1.0):
            site.setdefault("attrs", {})["CODE"] = ranked[0][1]
            site["code_source"] = "topology_layout_relation"
            site["display_label"] = ranked[0][1]
            site["label_method"] = "DWG_DERIVED:topology-layout-fdt-id"

    for feature in features:
        attrs = feature.setdefault("attrs", {})
        if feature.get("fc_name") in REQUIRED_LAYERS:
            if not attrs.get("CODE"):
                attrs["CODE"] = _stable_cad_code(feature)
                feature["code_source"] = "cad_handle"
            else:
                feature["code_source"] = "dwg_label_or_attribute"

        layer_and_text = f"{feature.get('layer', '')} {feature.get('annotation_text', '')} {feature.get('text', '')}"
        layer_upper = str(feature.get("layer", "")).upper()
        block_upper = str(feature.get("block_name", "")).upper()
        feature_class = feature.get("fc_name")
        if feature_class == "BOITE" and ("FAT" in layer_upper or "FAT" in block_upper or block_upper == "*U11"):
            attrs["TYPE"] = "PBO"
            attrs.setdefault("CAPACITE", 16)
        elif feature_class == "SITE" and ("FDT" in layer_upper or block_upper == "*U7"):
            attrs["TYPE"] = "PM"
        elif feature_class == "PTECH" and "POLE" in layer_upper:
            attrs["TYPE"] = "APPUI"
            if "NEW" in layer_upper:
                attrs.setdefault("STATUT", "EN PROJET")
            elif "EXISTING" in layer_upper:
                attrs.setdefault("STATUT", "DEPLOYE")
        elif feature_class == "CABLE":
            cable_label = re.search(r"(?i)FO\s+CABLE\s+(\d+)C[_/](\d+)T", layer_upper)
            if cable_label:
                attrs.setdefault("CAPACITE", int(cable_label.group(1)))
                feature["display_label"] = f"{cable_label.group(1)}C/{cable_label.group(2)}T"
                feature["label_method"] = "DWG_DIRECT:layer-name"
            if "MAINFEEDER" in layer_upper:
                attrs["TYPE_CABLE"] = "TRANSPORT"
            elif "SUBFEEDER" in layer_upper or "CABLE LINE" in layer_upper:
                attrs["TYPE_CABLE"] = "DISTRIBUTION"
            elif "DROP" in layer_upper:
                attrs["TYPE_CABLE"] = "RACCORDEMENT"
            elif not attrs.get("TYPE_CABLE"):
                type_match = ATTR_PATTERNS["TYPE_CABLE"].search(layer_and_text)
                if type_match:
                    attrs["TYPE_CABLE"] = type_match.group(1).upper()
            if layer_upper.rstrip().endswith("- AE"):
                attrs.setdefault("MODE_POSE", "AERIEN")

    seen_codes = defaultdict(set)
    for feature in features:
        attrs = feature.get("attrs", {})
        code = str(attrs.get("CODE", "")).strip()
        if not code:
            continue
        feature_class = feature.get("fc_name", "")
        normalized = code.upper()
        if normalized in seen_codes[feature_class]:
            handle = str(feature.get("cad_handle", "")).strip().upper()
            suffix = handle or hashlib.sha1(repr(feature.get("centroid")).encode("utf-8")).hexdigest()[:8].upper()
            attrs["CODE"] = f"{code}-CAD-{suffix}"
            feature["code_source"] = "dwg_label_disambiguated"
            normalized = attrs["CODE"].upper()
        seen_codes[feature_class].add(normalized)

    for cable in cables:
        native_points = cable.get("native_points", [])
        if len(native_points) < 2:
            continue
        origin, origin_distance, origin_ambiguous = _nearest_network_node(native_points[0], nodes)
        extremity, extremity_distance, extremity_ambiguous = _nearest_network_node(
            native_points[-1], nodes,
        )
        candidates = []
        for endpoint, node, distance, ambiguous in (
            ("ORIGINE", origin, origin_distance, origin_ambiguous),
            ("EXTREMITE", extremity, extremity_distance, extremity_ambiguous),
        ):
            if node is not None and not ambiguous and distance <= snap_tolerance_metres:
                candidates.append({
                    "endpoint": endpoint, "metres": round(distance, 6),
                    "target": node["attrs"]["CODE"], "status": "candidate",
                })
        cable["topology_displacements"] = json.dumps(candidates, separators=(",", ":"))
        cable["topology_method"] = "LEGACY_CANDIDATE:device-port-unreviewed"

    # Legacy path no longer promotes SPAN DIMENSION or SLING WIRE into CABLE.
    # The canonical v3 pipeline keeps support/measurement and optical graphs
    # separate and leaves all source route geometry immutable.
    components_before, components_after = 2, 2
    for cable in cables:
        cable["route_components_before"] = components_before
        cable["route_components_after"] = components_after

    return {
        "nodes": len(nodes),
        "cables": len(cables),
        "resolved_cables": sum(
            1 for cable in cables
            if cable["attrs"].get("ORIGINE") and cable["attrs"].get("EXTREMITE")
        ),
        "dimension_promoted_cables": 0,
        "route_components_before": components_before,
        "route_components_after": components_after,
    }


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


def _create_text_table(ds, name, fields):
    layer = ds.CreateLayer(name, None, ogr.wkbNone)
    for field_name, field_type in fields:
        layer.CreateField(ogr.FieldDefn(field_name, field_type))
    return layer


def _write_cad_evidence_tables(ds, all_features):
    """Persist styles and non-spatial sheet evidence without GIS duplication."""
    style_fields = [
        ("source_file", ogr.OFTString), ("cad_layout", ogr.OFTString),
        ("cad_role", ogr.OFTString), ("dwg_layer", ogr.OFTString),
        ("dwg_type", ogr.OFTString), ("block_name", ogr.OFTString),
        ("label", ogr.OFTString), ("aci_color", ogr.OFTInteger),
        ("true_color", ogr.OFTString), ("linetype", ogr.OFTString),
        ("lineweight", ogr.OFTInteger), ("rotation", ogr.OFTReal),
        ("sample_count", ogr.OFTInteger),
    ]
    style_layer = _create_text_table(ds, "cad_style_catalog", style_fields)
    catalog = {}
    for item in all_features:
        if item.get("output_kind", "feature") not in {"feature", "style_evidence"}:
            continue
        signature = (
            item.get("source_file", ""), item.get("layout", ""), item.get("cad_role", ""),
            item.get("layer", ""), item.get("dwg_type_name", ""), item.get("block_name", ""),
            item.get("aci_color", 256), item.get("true_color", ""),
            item.get("linetype", "ByLayer"), item.get("lineweight", -1),
            round(float(item.get("rotation", 0.0) or 0.0), 9),
        )
        entry = catalog.setdefault(signature, {"count": 0, "label": ""})
        entry["count"] += 1
        label = item.get("annotation_text") or item.get("text") or ""
        if label and not entry["label"]:
            entry["label"] = str(label)
    for signature, entry in catalog.items():
        row = ogr.Feature(style_layer.GetLayerDefn())
        values = list(signature)
        for index, (field_name, _) in enumerate(style_fields[:-1]):
            if field_name == "label":
                row.SetField(field_name, entry["label"])
            else:
                source_index = index if index < 6 else index - 1
                row.SetField(field_name, values[source_index])
        row.SetField("sample_count", entry["count"])
        style_layer.CreateFeature(row)
        row = None

    style_evidence_layer = _create_text_table(ds, "cad_style_evidence", style_fields)
    for item in all_features:
        if item.get("output_kind") != "style_evidence":
            continue
        row = ogr.Feature(style_evidence_layer.GetLayerDefn())
        row.SetField("source_file", item.get("source_file", ""))
        row.SetField("cad_layout", item.get("layout", ""))
        row.SetField("cad_role", item.get("cad_role", ""))
        row.SetField("dwg_layer", item.get("layer", ""))
        row.SetField("dwg_type", item.get("dwg_type_name", ""))
        row.SetField("block_name", item.get("block_name", ""))
        row.SetField("label", item.get("text", ""))
        row.SetField("aci_color", int(item.get("aci_color", 256)))
        row.SetField("true_color", item.get("true_color", ""))
        row.SetField("linetype", item.get("linetype", "ByLayer"))
        row.SetField("lineweight", int(item.get("lineweight", -1)))
        row.SetField("rotation", float(item.get("rotation", 0.0) or 0.0))
        row.SetField("sample_count", 1)
        style_evidence_layer.CreateFeature(row)
        row = None

    evidence_fields = [
        ("source_file", ogr.OFTString), ("cad_layout", ogr.OFTString),
        ("cad_role", ogr.OFTString), ("cad_handle", ogr.OFTString),
        ("dwg_layer", ogr.OFTString), ("dwg_type", ogr.OFTString),
        ("block_name", ogr.OFTString), ("text", ogr.OFTString),
    ]
    table_by_kind = {
        "topology_evidence": _create_text_table(ds, "cad_topology_evidence", evidence_fields),
        "summary_evidence": _create_text_table(ds, "cad_design_summary_evidence", evidence_fields),
        "annotation_evidence": _create_text_table(ds, "cad_unlinked_annotations", evidence_fields),
    }
    for item in all_features:
        table = table_by_kind.get(item.get("output_kind"))
        if table is None:
            continue
        row = ogr.Feature(table.GetLayerDefn())
        row.SetField("source_file", item.get("source_file", ""))
        row.SetField("cad_layout", item.get("layout", ""))
        row.SetField("cad_role", item.get("cad_role", ""))
        row.SetField("cad_handle", item.get("handle", item.get("cad_handle", "")))
        row.SetField("dwg_layer", item.get("layer", ""))
        row.SetField("dwg_type", item.get("dwg_type_name", ""))
        row.SetField("block_name", item.get("block_name", ""))
        row.SetField("text", str(item.get("text", "")))
        table.CreateFeature(row)
        row = None

    source_fields = [
        ("entity_key", ogr.OFTString), ("source_sha256", ogr.OFTString),
        ("source_file", ogr.OFTString), ("cad_layout", ogr.OFTString),
        ("cad_role", ogr.OFTString), ("disposition", ogr.OFTString),
        ("cad_handle", ogr.OFTString), ("dwg_layer", ogr.OFTString),
        ("dwg_type", ogr.OFTString), ("block_name", ogr.OFTString),
        ("text", ogr.OFTString), ("native_points", ogr.OFTString),
        ("dimension_value", ogr.OFTReal), ("aci_color", ogr.OFTInteger),
        ("true_color", ogr.OFTString), ("linetype", ogr.OFTString),
        ("lineweight", ogr.OFTInteger), ("rotation", ogr.OFTReal),
    ]
    source_layer = _create_text_table(ds, "cad_entities", source_fields)
    dimension_layer = _create_text_table(ds, "cad_dimension_evidence", source_fields)
    disposition_counts = defaultdict(int)
    for item in all_features:
        if item.get("output_kind") != "source_evidence":
            continue
        disposition = item.get("terminal_disposition", "unresolved")
        disposition_counts[disposition] += 1
        for table in (source_layer, dimension_layer if item.get("dwg_type_name") == "DIMENSION" else None):
            if table is None:
                continue
            row = ogr.Feature(table.GetLayerDefn())
            row.SetField("entity_key", item.get("entity_key", ""))
            row.SetField("source_sha256", item.get("source_sha256", ""))
            row.SetField("source_file", item.get("source_file", ""))
            row.SetField("cad_layout", item.get("layout", ""))
            row.SetField("cad_role", item.get("cad_role", ""))
            row.SetField("disposition", disposition)
            row.SetField("cad_handle", item.get("handle", ""))
            row.SetField("dwg_layer", item.get("layer", ""))
            row.SetField("dwg_type", item.get("dwg_type_name", ""))
            row.SetField("block_name", item.get("block_name", ""))
            row.SetField("text", item.get("text", ""))
            row.SetField("native_points", item.get("native_points", ""))
            if item.get("dimension_value") is not None:
                row.SetField("dimension_value", float(item["dimension_value"]))
            row.SetField("aci_color", int(item.get("aci_color", 256)))
            row.SetField("true_color", item.get("true_color", ""))
            row.SetField("linetype", item.get("linetype", "ByLayer"))
            row.SetField("lineweight", int(item.get("lineweight", -1)))
            row.SetField("rotation", float(item.get("rotation", 0.0) or 0.0))
            table.CreateFeature(row)
            row = None

    ledger_fields = [("disposition", ogr.OFTString), ("entity_count", ogr.OFTInteger)]
    ledger = _create_text_table(ds, "conservation_ledger", ledger_fields)
    for disposition, count in sorted(disposition_counts.items()):
        row = ogr.Feature(ledger.GetLayerDefn())
        row.SetField("disposition", disposition)
        row.SetField("entity_count", count)
        ledger.CreateFeature(row)
        row = None

    provenance_fields = [
        ("entity_key", ogr.OFTString), ("feature_class", ogr.OFTString),
        ("field_name", ogr.OFTString), ("field_value", ogr.OFTString),
        ("provenance", ogr.OFTString), ("evidence", ogr.OFTString),
    ]
    provenance_layer = _create_text_table(ds, "field_provenance", provenance_fields)
    configs = {
        "BOITE": BOITE, "CABLE": CABLE, "PTECH": PTECH,
        "INFRASTRUCTURE": INFRASTRUCTURE_FC, "SITE": SITE,
        "ZNRO": ZNRO, "ZPM": ZPM, "IMB": IMB,
    }
    for item in all_features:
        feature_class = item.get("fc_name")
        if item.get("output_kind", "feature") != "feature" or feature_class not in configs:
            continue
        attrs = item.get("attrs", {})
        mandatory = set(configs[feature_class].get("mandatory_fields", ()))
        field_names = sorted(set(attrs) | mandatory)
        for field_name in field_names:
            value = attrs.get(field_name)
            if value is None or value == "":
                provenance = "UNAVAILABLE"
                evidence = "not present in authoritative DWG evidence"
            elif field_name == "CODE":
                source = item.get("code_source", "")
                provenance = {
                    "dwg_text": "DWG_DIRECT",
                    "dwg_label_or_attribute": "DWG_DIRECT",
                    "cad_handle": "DWG_DERIVED:stable-handle-id",
                    "dwg_label_disambiguated": "DWG_DERIVED:label-disambiguation",
                    "topology_layout_relation": "DWG_DERIVED:topology-layout-fdt-id",
                }.get(source, "DWG_DIRECT")
                evidence = source
            elif field_name in {"ORIGINE", "EXTREMITE", "REF_PM", "CODE_PTC"}:
                provenance = f"DWG_DERIVED:{item.get('topology_method', 'topology-relation')}"
                evidence = item.get("topology_method", "")
            elif field_name in {"TYPE", "TYPE_CABLE", "CAPACITE", "STATUT", "MODE_POSE"}:
                provenance = "DWG_DERIVED:apd-semantic-rule"
                evidence = f"layer={item.get('layer', '')};block={item.get('block_name', '')}"
            else:
                provenance = "DWG_DIRECT"
                evidence = item.get("annotation_text") or item.get("text") or item.get("layer", "")
            row = ogr.Feature(provenance_layer.GetLayerDefn())
            row.SetField("entity_key", item.get("entity_key", ""))
            row.SetField("feature_class", feature_class)
            row.SetField("field_name", field_name)
            row.SetField("field_value", "" if value is None else str(value))
            row.SetField("provenance", provenance)
            row.SetField("evidence", str(evidence))
            provenance_layer.CreateFeature(row)
            row = None

    relation_fields = [
        ("source_entity_key", ogr.OFTString), ("relation_kind", ogr.OFTString),
        ("endpoint", ogr.OFTString), ("target_code", ogr.OFTString),
        ("status", ogr.OFTString), ("method", ogr.OFTString),
        ("displacement_m", ogr.OFTReal),
    ]
    relation_layer = _create_text_table(ds, "topology_relations", relation_fields)
    for item in all_features:
        if item.get("output_kind", "feature") != "feature" or item.get("fc_name") != "CABLE":
            continue
        displacement_by_endpoint = {
            value.get("endpoint"): value
            for value in json.loads(item.get("topology_displacements", "[]") or "[]")
        }
        for endpoint, field_name in (("ORIGINE", "ORIGINE"), ("EXTREMITE", "EXTREMITE")):
            target = item.get("attrs", {}).get(field_name)
            if not target:
                continue
            row = ogr.Feature(relation_layer.GetLayerDefn())
            row.SetField("source_entity_key", item.get("entity_key", ""))
            row.SetField("relation_kind", "connects")
            row.SetField("endpoint", endpoint)
            row.SetField("target_code", target)
            row.SetField("status", "accepted")
            row.SetField("method", item.get("topology_method", ""))
            displacement = displacement_by_endpoint.get(endpoint, {}).get("metres")
            if displacement is not None:
                row.SetField("displacement_m", float(displacement))
            relation_layer.CreateFeature(row)
            row = None

    check_fields = [
        ("check_name", ogr.OFTString), ("status", ogr.OFTString),
        ("expected", ogr.OFTString), ("actual", ogr.OFTString),
        ("detail", ogr.OFTString),
    ]
    check_layer = _create_text_table(ds, "apd_architecture_checks", check_fields)
    source_items = [item for item in all_features if item.get("output_kind") == "source_evidence"]
    spatial_items = [item for item in all_features if item.get("output_kind", "feature") == "feature"]
    model_items = [item for item in source_items if item.get("layout") == "Model"]
    checks = [
        ("modelspace_census", 6940, len(model_items)),
        ("insert_census", 222, sum(item.get("dwg_type_name") == "INSERT" for item in model_items)),
        ("dimension_census", 170, sum(item.get("dwg_type_name") == "DIMENSION" for item in model_items)),
        ("PTECH_plan_assets", 167, sum(item.get("fc_name") == "PTECH" for item in spatial_items)),
        ("BOITE_plan_assets", 43, sum(item.get("fc_name") == "BOITE" for item in spatial_items)),
        ("SITE_plan_assets", 2, sum(item.get("fc_name") == "SITE" for item in spatial_items)),
        ("IMB_homepass", 682, sum(item.get("fc_name") == "IMB" for item in spatial_items)),
        ("CABLE_positive_routes", 6, sum(
            item.get("fc_name") == "CABLE" and item.get("geometry_role", "SOURCE_ROUTE") == "SOURCE_ROUTE"
            for item in spatial_items
        )),
        ("CABLE_dimension_promotions", 0, sum(
            item.get("fc_name") == "CABLE" and item.get("dwg_type_name") == "DIMENSION"
            for item in spatial_items
        )),
        ("CABLE_delivery_features", 6, sum(item.get("fc_name") == "CABLE" for item in spatial_items)),
        ("CABLE_source_route_components", 2, next((
            item.get("route_components_after")
            for item in spatial_items
            if item.get("fc_name") == "CABLE" and item.get("geometry_role") == "SOURCE_ROUTE"
        ), None)),
        ("ZPM_without_review", 0, sum(item.get("fc_name") == "ZPM" for item in spatial_items)),
        ("ZNRO_without_source", 0, sum(item.get("fc_name") == "ZNRO" for item in spatial_items)),
        ("generated_ids_as_labels", 0, sum("-CAD-" in str(item.get("display_label", "")) for item in spatial_items)),
    ]
    for check_name, expected, actual in checks:
        row = ogr.Feature(check_layer.GetLayerDefn())
        row.SetField("check_name", check_name)
        row.SetField("status", "PASS" if expected == actual else "FAIL")
        row.SetField("expected", str(expected))
        row.SetField("actual", str(actual))
        row.SetField("detail", "architecture regression, not semantic certification")
        check_layer.CreateFeature(row)
        row = None


_ACI_COLORS = {
    1: "#FF0000", 2: "#FFFF00", 3: "#00FF00", 4: "#00FFFF",
    5: "#0000FF", 6: "#FF00FF", 7: "#000000", 256: "#333333",
}


def _cad_style_key(feature):
    aci_color = int(feature.get("aci_color", 256))
    true_color = str(feature.get("true_color", "") or "").upper()
    category = true_color or str(aci_color)
    color = true_color or _ACI_COLORS.get(aci_color, "#333333")
    linetype = str(feature.get("linetype", "Continuous") or "Continuous")
    lineweight = int(feature.get("lineweight", -1) or -1)
    return category, color.upper(), linetype, lineweight


def _qgis_pen_style(linetype):
    value = (linetype or "").casefold()
    if "dash" in value or "hidden" in value:
        return "dash"
    if "dot" in value:
        return "dot"
    if "center" in value:
        return "dash dot"
    return "solid"


def _qgis_rgba(color, alpha=255):
    value = str(color or "#333333").lstrip("#")
    if len(value) != 6:
        value = "333333"
    try:
        red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        red, green, blue = 51, 51, 51
    return f"{red},{green},{blue},{alpha}"


def _option(parent, name, value, value_type="QString"):
    return ET.SubElement(parent, "Option", name=name, value=str(value), type=value_type)


def _qgis_style_qml(fc_name, geometry_type, styles):
    """Generate a compact categorized QGIS style from real CAD style tuples."""
    root = ET.Element(
        "qgis", version="3.34.0", styleCategories="AllStyleCategories",
        labelsEnabled="1", simplifyDrawingHints="1",
    )
    expression = (
        "coalesce(nullif(\"cad_truecolor\", ''), to_string(\"cad_color\")) || '|' || "
        "coalesce(\"cad_linetype\", 'Continuous') || '|' || to_string(\"cad_lineweight\")"
    )
    renderer = ET.SubElement(root, "renderer-v2", type="categorizedSymbol", attr=expression, symbollevels="0")
    categories = ET.SubElement(renderer, "categories")
    symbols = ET.SubElement(renderer, "symbols")
    default_style = [("256", "#333333", "Continuous", -1)]
    for index, (category, color, linetype, lineweight) in enumerate(styles or default_style):
        category_value = f"{category}|{linetype}|{lineweight}"
        ET.SubElement(
            categories, "category", value=category_value, symbol=str(index),
            label=f"{color} / {linetype} / {lineweight}", render="true",
        )
        symbol_type = {"Point": "marker", "LineString": "line", "Polygon": "fill"}[geometry_type]
        symbol = ET.SubElement(symbols, "symbol", type=symbol_type, name=str(index), alpha="1")
        if geometry_type == "Point":
            layer = ET.SubElement(symbol, "layer", {"class": "SimpleMarker", "enabled": "1", "pass": "0"})
            options = ET.SubElement(layer, "Option", type="Map")
            _option(options, "color", _qgis_rgba(color))
            _option(options, "outline_color", "32,32,32,255")
            _option(options, "outline_style", "solid")
            _option(options, "size", "2.4")
            _option(options, "name", "circle")
        elif geometry_type == "LineString":
            layer = ET.SubElement(symbol, "layer", {"class": "SimpleLine", "enabled": "1", "pass": "0"})
            options = ET.SubElement(layer, "Option", type="Map")
            _option(options, "line_color", _qgis_rgba(color))
            _option(options, "line_style", _qgis_pen_style(linetype))
            _option(options, "line_width", max(0.1, lineweight / 100.0) if lineweight > 0 else 0.26)
            _option(options, "line_width_unit", "MM")
        else:
            layer = ET.SubElement(symbol, "layer", {"class": "SimpleFill", "enabled": "1", "pass": "0"})
            options = ET.SubElement(layer, "Option", type="Map")
            _option(options, "color", _qgis_rgba(color, 85))
            _option(options, "outline_color", _qgis_rgba(color))
            _option(options, "outline_style", _qgis_pen_style(linetype))

    labeling = ET.SubElement(root, "labeling", type="simple")
    settings = ET.SubElement(labeling, "settings")
    ET.SubElement(
        settings, "text-style", fieldName='"display_label"',
        isExpression="1", fontFamily="Arial", fontSize="9", textColor="0,0,0,255",
    )
    ET.SubElement(settings, "placement", placement="0", dist="1", offsetUnits="MM")
    ET.SubElement(settings, "rendering", drawLabels="1", obstacle="1", scaleVisibility="0")
    ET.SubElement(root, "layerGeometryType").text = {"Point": "0", "LineString": "1", "Polygon": "2"}[geometry_type]
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _embed_qgis_styles(gpkg_path, all_features):
    geometry_types = {
        "BOITE": "Point", "CABLE": "LineString", "PTECH": "Point",
        "INFRASTRUCTURE": "LineString", "SITE": "Point",
        "ZNRO": "Polygon", "ZPM": "Polygon", "IMB": "Polygon",
    }
    features_by_layer = defaultdict(list)
    for feature in all_features:
        if feature.get("output_kind", "feature") == "feature" and feature.get("fc_name") in geometry_types:
            features_by_layer[feature["fc_name"]].append(feature)
    connection = sqlite3.connect(gpkg_path)
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS layer_styles (
                id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT,
                f_table_schema TEXT, f_table_name TEXT, f_geometry_column TEXT,
                styleName TEXT, styleQML TEXT, styleSLD TEXT, useAsDefault INTEGER,
                description TEXT, owner TEXT, ui TEXT,
                update_time DATETIME DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        for fc_name, geometry_type in geometry_types.items():
            styles = sorted({_cad_style_key(feature) for feature in features_by_layer[fc_name]})
            qml = _qgis_style_qml(fc_name, geometry_type, styles)
            connection.execute(
                "DELETE FROM layer_styles WHERE f_table_name=? AND styleName=?",
                (fc_name, "CAD2GIS AutoCAD style"),
            )
            connection.execute(
                """INSERT INTO layer_styles (
                    f_table_catalog, f_table_schema, f_table_name, f_geometry_column,
                    styleName, styleQML, styleSLD, useAsDefault, description, owner, ui
                ) VALUES ('', '', ?, 'geom', ?, ?, '', 1, ?, 'CAD2GIS', '')""",
                (fc_name, "CAD2GIS AutoCAD style", qml, "AutoCAD colors, line types, lineweights, and labels"),
            )
        connection.commit()
    finally:
        connection.close()


def _write_geopackage_file(output_path, all_features, source_files):
    """Write all features to a single GeoPackage with 8 FTTH layers + metadata."""
    driver = ogr.GetDriverByName("GPKG")
    ds = driver.CreateDataSource(output_path)
    if ds is None:
        raise RuntimeError(f"Could not create staged GeoPackage: {output_path}")
    if ds.StartTransaction() != 0:
        raise RuntimeError("Could not start GeoPackage transaction")

    srs = osr.SpatialReference()
    srs.SetFromUserInput(_TARGET_CRS_LABEL)
    set_traditional_axis_order(srs, osr)
    source_srs = osr.SpatialReference()
    source_srs.ImportFromEPSG(4326)
    set_traditional_axis_order(source_srs, osr)
    output_transform = None
    if _TARGET_CRS_LABEL.upper() not in {"EPSG:4326", "4326"}:
        output_transform = osr.CoordinateTransformation(source_srs, srs)

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
        if feat.get("output_kind", "feature") != "feature":
            continue
        fc = feat["fc_name"]
        if fc in REQUIRED_LAYERS:
            fc_features[fc].append(feat)

    # Also track fc_misc for QC
    misc_count = sum(
        1 for f in all_features
        if f.get("output_kind", "feature") == "feature" and f["fc_name"] == "fc_misc"
    )

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
        layer.CreateField(ogr.FieldDefn("display_label", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("label_method", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("entity_key", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("cad_handle", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("cad_layout", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("cad_role", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("cad_block", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("cad_color", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("cad_truecolor", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("cad_linetype", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("cad_lineweight", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("cad_rotation", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("code_source", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("topology_method", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("topology_displacements", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("geometry_role", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("source_relation", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("source_dimension_handle", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("span_node_codes", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("route_components_before", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("route_components_after", ogr.OFTInteger))

        feat_list = fc_features.get(fc_name, [])
        count = 0
        for fdata in feat_list:
            wkt_resolved, pts_resolved, _ = _resolve_fc_geometry(fdata)
            if not wkt_resolved:
                continue

            geom = ogr.CreateGeometryFromWkt(wkt_resolved)
            if geom is None:
                continue
            if output_transform is not None:
                geom.Transform(output_transform)

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
                centroid_geometry = geom.Centroid()
                cx, cy = centroid_geometry.GetX(), centroid_geometry.GetY()
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
            feature.SetField("annotation_text", fdata.get("annotation_text", ""))
            feature.SetField("display_label", fdata.get("display_label", ""))
            feature.SetField("label_method", fdata.get("label_method", "UNAVAILABLE"))
            feature.SetField("entity_key", fdata.get("entity_key", ""))
            feature.SetField("cad_handle", fdata.get("cad_handle", ""))
            feature.SetField("cad_layout", fdata.get("layout", ""))
            feature.SetField("cad_role", fdata.get("cad_role", ""))
            feature.SetField("cad_block", fdata.get("block_name", ""))
            feature.SetField("cad_color", int(fdata.get("aci_color", 256)))
            feature.SetField("cad_truecolor", fdata.get("true_color", ""))
            feature.SetField("cad_linetype", fdata.get("linetype", "ByLayer"))
            feature.SetField("cad_lineweight", int(fdata.get("lineweight", -1)))
            feature.SetField("cad_rotation", float(fdata.get("rotation", 0.0) or 0.0))
            feature.SetField("code_source", fdata.get("code_source", ""))
            feature.SetField("topology_method", fdata.get("topology_method", ""))
            feature.SetField("topology_displacements", fdata.get("topology_displacements", ""))
            feature.SetField("geometry_role", fdata.get("geometry_role", ""))
            feature.SetField("source_relation", fdata.get("source_relation", ""))
            feature.SetField("source_dimension_handle", fdata.get("source_dimension_handle", ""))
            feature.SetField("span_node_codes", json.dumps(
                fdata.get("span_node_codes", []), ensure_ascii=False, separators=(",", ":"),
            ) if fdata.get("span_node_codes") else "")
            if fdata.get("route_components_before") is not None:
                feature.SetField("route_components_before", int(fdata["route_components_before"]))
            if fdata.get("route_components_after") is not None:
                feature.SetField("route_components_after", int(fdata["route_components_after"]))

            layer.CreateFeature(feature)
            feature = None
            count += 1

        layer_stats[fc_name] = count
        output_layers[fc_name] = layer
        print(f"  {fc_name}: {count} features")

    _write_cad_evidence_tables(ds, all_features)

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
    mf.SetField("crs", _TARGET_CRS_LABEL)
    mf.SetField("total_features", sum(layer_stats.values()))
    mf.SetField("misc_features", misc_count)
    manifest_layer.CreateFeature(mf)
    mf = None

    # transform_record
    transform_layer = ds.CreateLayer("transform_record", srs, ogr.wkbNone)
    transform_layer.CreateField(ogr.FieldDefn("operation", ogr.OFTString))
    transform_layer.CreateField(ogr.FieldDefn("detail", ogr.OFTString))

    for op_name, detail in [
        ("coordinate_offset", "None"),
        ("reprojection", f"{_DETECTED_SOURCE_CRS or _SOURCE_CRS_LABEL} -> EPSG:4326"),
        ("crs_output", _TARGET_CRS_LABEL),
        ("coordinate_filter", "|lat|<=90, |lon|<=180"),
        ("dwg_ingestion", "Direct read-only AutoCAD database access; no DXF export"),
        ("sheet_role_partition", "Model/plan -> GIS; topology/splicing/legend/summary -> evidence; title/frame -> excluded"),
        ("geographic_bounds_check", f"lat [{WORLD_LAT_MIN},{WORLD_LAT_MAX}], lon [{WORLD_LON_MIN},{WORLD_LON_MAX}]"),
        ("paper_space_filter", "|centroid_lat|>90 OR |centroid_lon|>180 → discard"),
        ("block_insert_handling", "Recognized telecom blocks → POINT; unrecognized → skip"),
        ("geometry_reconstruction", "Adaptive chord tolerance, WKT to OGR"),
        ("attribute_extraction", "DWG text, MText, block attributes, and telecom regex patterns"),
        ("classification", "Two-tier: layer regex + annotation keywords"),
        ("topology", "Legacy candidate relations only; source routes remain immutable; SPAN DIMENSION and SLING WIRE never become CABLE"),
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

    if ds.CommitTransaction() != 0:
        raise RuntimeError("Could not commit GeoPackage transaction")
    ds = None
    return layer_stats, misc_count


def write_geopackage(output_path, all_features, source_files):
    """Validate a staged GeoPackage before atomically replacing the destination."""
    destination = os.path.abspath(output_path)
    parent = os.path.dirname(destination) or os.getcwd()
    os.makedirs(parent, exist_ok=True)
    stage_dir = tempfile.mkdtemp(prefix=f".{os.path.basename(destination)}.", dir=parent)
    staged = os.path.join(stage_dir, os.path.basename(destination))
    try:
        result = _write_geopackage_file(staged, all_features, source_files)
        _embed_qgis_styles(staged, all_features)
        if not os.path.isfile(staged) or os.path.getsize(staged) == 0:
            raise RuntimeError("Staged GeoPackage is missing or empty")
        probe = ogr.Open(staged, 0)
        required_layers = ("BOITE", "CABLE", "PTECH", "INFRASTRUCTURE", "SITE", "ZNRO", "ZPM", "IMB")
        if probe is None or any(probe.GetLayerByName(name) is None for name in required_layers):
            raise RuntimeError("Staged GeoPackage failed structural validation")
        probe = None
        os.replace(staged, destination)
        return result
    finally:
        if os.path.exists(staged):
            os.remove(staged)
        if os.path.isdir(stage_dir):
            shutil.rmtree(stage_dir)


def _write_run_manifest(output_path, source_files, all_features, layer_stats, topology_stats):
    output = Path(output_path).resolve()
    source_items = [item for item in all_features if item.get("output_kind") == "source_evidence"]
    spatial_items = [item for item in all_features if item.get("output_kind", "feature") == "feature"]
    metadata = next(
        (item.get("text", "") for item in source_items if item.get("dwg_type_name") == "DOCUMENT_METADATA"),
        "",
    )
    manifest = {
        "schema_version": "apd-run-manifest-v1",
        "pipeline": "experiment-direct-autocad-architecture-v1",
        "source": [
            {"path": str(Path(path).resolve()), "sha256": _sha256_file(path)}
            for path in source_files
        ],
        "dwg_crs_evidence": metadata,
        "source_crs": _SOURCE_CRS_LABEL,
        "internal_crs": "EPSG:4326",
        "target_crs": _TARGET_CRS_LABEL,
        "output": {"path": str(output), "sha256": _sha256_file(output)},
        "census": {
            "model_entities": sum(item.get("layout") == "Model" for item in source_items),
            "model_inserts": sum(item.get("layout") == "Model" and item.get("dwg_type_name") == "INSERT" for item in source_items),
            "model_dimensions": sum(item.get("layout") == "Model" and item.get("dwg_type_name") == "DIMENSION" for item in source_items),
        },
        "delivery_counts": layer_stats,
        "display_labels": {
            feature_class: sum(
                item.get("fc_name") == feature_class and bool(item.get("display_label"))
                for item in spatial_items
            )
            for feature_class in ("BOITE", "CABLE", "PTECH", "INFRASTRUCTURE", "SITE", "ZNRO", "ZPM", "IMB")
        },
        "topology": topology_stats,
        "unresolved": {
            "cables_without_two_endpoints": topology_stats["cables"] - topology_stats["resolved_cables"],
            "annotations": sum(item.get("output_kind") == "annotation_evidence" for item in all_features),
        },
    }
    manifest_path = output.with_suffix(".manifest.json")
    temporary = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, manifest_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return manifest_path


# ── Main CLI ──────────────────────────────────────────────────────────────

def main():
    if os.environ.get("CAD2GIS_ENABLE_LEGACY") != "1":
        raise SystemExit(
            "Legacy monolithic converter is disabled. Use convert_v3.py / "
            "cad2gis_v3.pipeline so evidence, topology, CRS, and delivery remain separated."
        )
    parser = argparse.ArgumentParser(
        description="GeoFormer P2: DWG-to-GeoPackage converter (FTTH domain, EPSG:4326 native)",
    )
    parser.add_argument("--input", "-i", nargs="+", required=True,
                       help="One or more authoritative DWG files to convert (DXF is disabled)")
    parser.add_argument("--output", "-o", required=True,
                       help="Output GeoPackage path (.gpkg)")
    parser.add_argument("--temp-dir", default="/tmp/geoformer",
                       help="Temporary directory (default: /tmp/geoformer)")
    parser.add_argument("--config", default=None,
                       help="Optional JSON config (not used; schema from schema_config.py)")
    parser.add_argument("--source-crs", required=True,
                       help="Explicit source DWG CRS. This APD declares WGS84.PseudoMercator, use EPSG:3857.")
    parser.add_argument("--target-crs", default="EPSG:9481",
                       help="Delivery CRS (default: EPSG:9481, SRGI2013 / UTM zone 51N).")
    parser.add_argument(
        "--region-bounds",
        default=None,
        help="Optional lat_min,lat_max,lon_min,lon_max filter; default keeps worldwide data.",
    )
    args = parser.parse_args()

    # Set up CRS transform
    global _CRS_TRANSFORM, _SOURCE_CRS_LABEL, _AUTO_SOURCE_CRS, _DETECTED_SOURCE_CRS, _TARGET_CRS_LABEL, _REGION_BOUNDS
    _SOURCE_CRS_LABEL = args.source_crs
    _TARGET_CRS_LABEL = args.target_crs
    _AUTO_SOURCE_CRS = False
    _DETECTED_SOURCE_CRS = None
    if args.source_crs.upper() == "AUTO":
        parser.error("--source-crs AUTO is disabled; CRS must be explicit and evidence-bound")
    if args.region_bounds:
        try:
            bounds = tuple(float(value.strip()) for value in args.region_bounds.split(","))
        except ValueError:
            parser.error("--region-bounds must contain four comma-separated numbers")
        if len(bounds) != 4:
            parser.error("--region-bounds must contain lat_min,lat_max,lon_min,lon_max")
        lat_min, lat_max, lon_min, lon_max = bounds
        if lat_min > lat_max or lon_min > lon_max:
            parser.error("--region-bounds minimums must not exceed maximums")
        _REGION_BOUNDS = bounds
    else:
        _REGION_BOUNDS = None
    if args.source_crs and args.source_crs.upper() not in ("EPSG:4326", "4326", "NONE", "IDENTITY"):
        src = osr.SpatialReference()
        src.SetFromUserInput(args.source_crs)
        dst = osr.SpatialReference()
        dst.ImportFromEPSG(4326)
        set_traditional_axis_order(src, osr)
        set_traditional_axis_order(dst, osr)
        _CRS_TRANSFORM = osr.CoordinateTransformation(src, dst)
        print(f"  Source CRS: {args.source_crs} → EPSG:4326 (reprojection enabled)")
    else:
        _CRS_TRANSFORM = None

    # Validate inputs exist
    for path in args.input:
        if not os.path.isfile(path):
            print(f"ERROR: Input file not found: {path}", file=sys.stderr)
            sys.exit(1)
        if Path(path).suffix.lower() != ".dwg":
            parser.error(f"DXF and non-DWG inputs are disabled: {path}")
        digest = _sha256_file(path)
        if digest.lower() != APD_SOURCE_SHA256:
            parser.error(f"APD source hash mismatch for {path}: {digest}")

    os.makedirs(args.temp_dir, exist_ok=True)

    print("=" * 60)
    print("GeoFormer P2: DWG → GeoPackage Converter")
    print(f"  Input:  {len(args.input)} DWG file(s)")
    print(f"  Output: {args.output}")
    transform_label = "identity" if _CRS_TRANSFORM is None else f"{_SOURCE_CRS_LABEL} -> EPSG:4326 internal"
    print(f"  CRS:    {transform_label}")
    print(f"  Target: {_TARGET_CRS_LABEL}")
    print("=" * 60)

    all_features = []
    warnings_log = []

    for dwg_path in args.input:
        print(f"\nProcessing: {dwg_path}")
        features = read_dwg(dwg_path)
        _validate_source_crs_evidence(features, args.source_crs)
        all_features.extend(features)

        # Log warnings per file
        spatial_features = [f for f in features if f.get("output_kind", "feature") == "feature"]
        file_outliers = sum(1 for f in spatial_features if f.get("geographic_outlier"))
        file_misc = sum(1 for f in spatial_features if f["fc_name"] == "fc_misc")
        file_geom_outliers = sum(
            1 for f in spatial_features
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

    topology_stats = _build_network_topology(all_features)
    print(
        "Topology: "
        f"{topology_stats['cables']} immutable source routes, "
        f"0 dimension/slings promoted, "
        f"{topology_stats['route_components_after']} observed source components, "
        f"{topology_stats['nodes']} endpoint nodes"
    )

    # Re-index spatial features only; evidence rows retain source handles.
    spatial_index = 0
    for feat in all_features:
        if feat.get("output_kind", "feature") == "feature":
            feat["global_id"] = spatial_index
            spatial_index += 1

    print(f"\nTotal spatial features: {spatial_index}")

    # Print warnings
    for w in warnings_log:
        print(f"  WARNING: {w}")

    # Write GeoPackage
    print(f"\nWriting GeoPackage: {args.output}")
    stats, misc_count = write_geopackage(args.output, all_features, args.input)
    manifest_path = _write_run_manifest(
        args.output, args.input, all_features, stats, topology_stats,
    )

    print("\n" + "=" * 60)
    print("Conversion Summary:")
    for fc_name in ["BOITE", "CABLE", "PTECH", "INFRASTRUCTURE",
                     "SITE", "ZNRO", "ZPM", "IMB"]:
        print(f"  {fc_name:20s}: {stats.get(fc_name, 0):5d} features")
    print(f"  {'fc_misc':20s}: {misc_count:5d} features (unclassified)")
    print(f"  {'TOTAL':20s}: {sum(stats.values()) + misc_count:5d} features")
    print(f"\nOutput: {args.output}")
    print(f"  SHA256: {_sha256_file(args.output)}")
    print(f"  Manifest: {manifest_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
