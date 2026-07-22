#!/usr/bin/env python3
"""
CAD Common Library — DWG parsing, geometry extraction, colour resolution, CRS
transformation.  Contains ZERO FTTH domain symbols (BOITE, CABLE, PTECH, FAT,
FDT, DMPH, NRO, PM, ZNRO, IMB etc.).  Reusable for any DWG-to-GIS pipeline.

Domain-agnostic layers (L1-L2 from converter.py):
  L1 — DWG type constants, ctypes bridge, geometry reconstruction, dimension
       extraction, spatial clustering (grid + union-find)
  L2 — CRS parameterisation, coordinate transforms, colour parsing (ACI→RGB,
       ByLayer resolution), haversine / geodesy helpers

Mutable globals (DWG_TYPE_*, DIMENSION_TYPE_UNION, CONTROL_TYPES) are
PLACEHOLDER values until patched by read_dwg() in the downstream converter.
Call init_crs() before any CRS-dependent function.
"""

import ctypes
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

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

# ── CRS state — initialised by init_crs() ────────────────────────────────
_CRS_TRANSFORM = None        # source → target osr.CoordinateTransformation, or None (identity)
_TO_WGS84 = None             # target → EPSG:4326, or None if target IS 4326
_SOURCE_IS_GEOGRAPHIC = False
_TARGET_IS_GEOGRAPHIC = False
_TARGET_IS_WEBMERC = True
_SOURCE_CRS_LABEL = "EPSG:3857"
_TARGET_CRS_LABEL = "EPSG:3857"
_CRS_INITIALIZED = False


def init_crs(source_crs_label, target_crs_label):
    """Initialise CRS state.  Must be called before any CRS-dependent function
    (_reproject_point, _to_wgs84, _valid_coord, _in_region_bounds,
     _meters_to_units, _line_length_m, _adaptive_chord_tolerance).

    Raises RuntimeError if either CRS label is unrecognised by PROJ.
    """
    global _CRS_TRANSFORM, _TO_WGS84, _SOURCE_IS_GEOGRAPHIC, _TARGET_IS_GEOGRAPHIC
    global _TARGET_IS_WEBMERC, _SOURCE_CRS_LABEL, _TARGET_CRS_LABEL, _CRS_INITIALIZED

    src = osr.SpatialReference()
    if src.SetFromUserInput(source_crs_label) != 0:
        raise RuntimeError(f"Unrecognised source CRS: {source_crs_label}")
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    dst = osr.SpatialReference()
    if dst.SetFromUserInput(target_crs_label) != 0:
        raise RuntimeError(f"Unrecognised target CRS: {target_crs_label}")
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    _SOURCE_IS_GEOGRAPHIC = bool(src.IsGeographic())
    _TARGET_IS_GEOGRAPHIC = bool(dst.IsGeographic())
    _TARGET_IS_WEBMERC = dst.GetAuthorityCode(None) == "3857"
    _SOURCE_CRS_LABEL = source_crs_label
    _TARGET_CRS_LABEL = target_crs_label

    if src.IsSame(dst):
        _CRS_TRANSFORM = None
    else:
        _CRS_TRANSFORM = osr.CoordinateTransformation(src, dst)

    _TO_WGS84 = None if dst.IsSame(wgs84) else osr.CoordinateTransformation(dst, wgs84)
    _CRS_INITIALIZED = True


def _reproject_point(x, y):
    """Reproject a single coordinate from source CRS to target CRS.
    Axis order is traditional GIS (x=lon/easting, y=lat/northing)."""
    if not _CRS_INITIALIZED:
        raise RuntimeError("CRS not initialised — call init_crs() first")
    if _CRS_TRANSFORM is None:
        return x, y
    try:
        pt = _CRS_TRANSFORM.TransformPoint(x, y)
        return pt[0], pt[1]
    except Exception:
        return x, y


def _to_wgs84(x, y):
    """Convert a target-CRS coordinate to EPSG:4326 (lon, lat)."""
    if not _CRS_INITIALIZED:
        raise RuntimeError("CRS not initialised — call init_crs() first")
    if _TO_WGS84 is None:
        return x, y
    try:
        pt = _TO_WGS84.TransformPoint(x, y)
        return pt[0], pt[1]
    except Exception:
        return x, y


def _valid_coord(x, y):
    """CRS-aware coordinate sanity check in target-CRS units."""
    if not _CRS_INITIALIZED:
        raise RuntimeError("CRS not initialised — call init_crs() first")
    if _TARGET_IS_GEOGRAPHIC:
        return abs(x) <= 180 and abs(y) <= 90
    if _TARGET_IS_WEBMERC:
        return abs(x) <= WEBMERC_MAX_X and abs(y) <= WEBMERC_MAX_Y
    return abs(x) <= 1e8 and abs(y) <= 1e8


def _in_region_bounds(x, y):
    """Check a target-CRS coordinate against the deployment region (4326)."""
    if not _CRS_INITIALIZED:
        raise RuntimeError("CRS not initialised — call init_crs() first")
    lon, lat = _to_wgs84(x, y)
    lat_min, lat_max, lon_min, lon_max = REGION_BOUNDS_WGS84
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _meters_to_units(meters):
    """Convert a metre tolerance to target-CRS units."""
    if not _CRS_INITIALIZED:
        raise RuntimeError("CRS not initialised — call init_crs() first")
    if _TARGET_IS_GEOGRAPHIC:
        return meters / METERS_PER_DEGREE
    return meters


def _line_length_m(points):
    """Length of a (x, y) point chain in metres, CRS-aware."""
    if not _CRS_INITIALIZED:
        raise RuntimeError("CRS not initialised — call init_crs() first")
    if len(points) < 2:
        return 0.0
    if _TARGET_IS_GEOGRAPHIC:
        return _haversine_length(points)
    total = 0.0
    for i in range(len(points) - 1):
        total += math.hypot(points[i + 1][0] - points[i][0],
                            points[i + 1][1] - points[i][1])
    return total


# ── DWG type constants — PLACEHOLDERS patched by read_dwg() downstream ───
# Values marked "Will be corrected after LibreDWG import" are reassigned by
# the downstream converter's read_dwg() via cad_common.DWG_TYPE_* = L_* style
# mutation.  These initial values are LibreDWG-independent placeholders.
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

# ── Ctypes bridge to LibreDWG ─────────────────────────────────────────────
import ctypes.util
_libdwg = None
_libc = None

# Portable LibreDWG search paths (tried in order; env var overrides all)
_LIBREDWG_SEARCH = [
    os.environ.get("LIBREDWG_SO", ""),
    "/usr/local/lib/libredwg.so",
    "/usr/lib/libredwg.so",
    os.path.join(os.path.dirname(__file__), "libredwg.so"),
]


def _find_libredwg():
    for path in _LIBREDWG_SEARCH:
        if path and os.path.isfile(path):
            return path
    return None


def _init_libredwg():
    global _libdwg, _libc
    if _libdwg is not None:
        return _libdwg, _libc
    so_path = _find_libredwg()
    if so_path is None:
        sys.exit(
            "ERROR: LibreDWG shared library not found.\n"
            "Searched: " + ", ".join(p for p in _LIBREDWG_SEARCH if p) + "\n"
            "Set LIBREDWG_SO=/path/to/libredwg.so or install LibreDWG."
        )
    try:
        _libdwg = ctypes.CDLL(so_path)
    except OSError:
        sys.exit(f"ERROR: Cannot load LibreDWG from {so_path}")
    _libc_path = ctypes.util.find_library("c") or "libc.so.6"
    _libc = ctypes.CDLL(_libc_path)

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


def _entity_utf8_text(dwg, entity_ptr):
    """Extract TEXT / MTEXT / ATTRIB content as UTF-8 via dynapi.

    Converts LibreDWG's TU (UTF-16) strings to UTF-8, avoiding the SWIG
    NUL-byte truncation bug that loses all text after the first NUL.
    Returns "" on failure.
    """
    _libdwg, _ = _init_libredwg()
    out = ctypes.c_char_p()
    is_malloc = ctypes.c_int(0)
    ok = _libdwg.dwg_dynapi_entity_utf8text(
        ctypes.c_void_p(int(dwg)),
        "text".encode("ascii"),
        ctypes.c_char_p(None),
        ctypes.byref(out),
        ctypes.byref(is_malloc),
        ctypes.c_void_p(None)
    )
    if not ok or not out.value:
        return ""
    text = out.value.decode("utf-8", errors="replace")
    if is_malloc.value:
        _libc.free(out)
    return text


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
    if not _CRS_INITIALIZED:
        raise RuntimeError("CRS not initialised — call init_crs() first")
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


# ── Spatial Clustering (grid + union-find) ───────────────────────────────

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


# ── ACI → RGB colour table ───────────────────────────────────────────────
# Extracted from schema_config.py (self-contained: no package imports needed).

def _hsv_bytes(h, s, v):
    """HSV(h:0-360, s:0-1, v:0-1) → (r, g, b) bytes."""
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)


def _generate_aci_table():
    """Build the 256-entry AutoCAD Colour Index → '#RRGGBB' lookup table."""
    tbl = {}
    # First 9 standard colours
    standard = [
        (0, 0, 0), (255, 0, 0), (255, 255, 0), (0, 255, 0),
        (0, 255, 255), (0, 0, 255), (255, 0, 255), (255, 255, 255), (128, 128, 128)
    ]
    for i, (r, g, b) in enumerate(standard):
        tbl[i + 1] = "#%02X%02X%02X" % (r, g, b)
    # Greys (ACI 250-255)
    for i in range(6):
        v = int(40 + i * 35)
        tbl[250 + i] = "#%02X%02X%02X" % (v, v, v)
    # Hue-based colours
    hue_groups = [
        (10, 0), (20, 4), (30, 8), (40, 12), (50, 16),
        (60, 20), (70, 24), (80, 28), (90, 32), (100, 36), (110, 40),
        (120, 44), (130, 48), (140, 52), (150, 56), (160, 60),
        (170, 64), (180, 68), (190, 72), (200, 76), (210, 80),
        (220, 84), (230, 88), (240, 92),
    ]
    for base, start in hue_groups:
        for k in range(4):
            aci = start + k + 1
            if aci < 10 or aci > 249:
                continue
            h = base + k * 30
            s_vals = [1.0, 0.75, 0.5, 0.25] if k < 4 else [1.0]
            s = s_vals[min(k, len(s_vals) - 1)]
            if k == 0:
                v = 0.8
            elif k == 1:
                v = 1.0
            elif k == 2:
                v = 0.6
            else:
                v = 0.4
            r, g, b = _hsv_bytes(h % 360, s, v)
            tbl[aci] = "#%02X%02X%02X" % (r, g, b)
    return tbl


ACI_TO_RGB = _generate_aci_table()
DEFAULT_COLOR_RGB = "#404040"


def aci_to_rgb(aci):
    """Map an AutoCAD Colour Index (1-255) to '#RRGGBB'. Unknown → DEFAULT_COLOR_RGB."""
    return ACI_TO_RGB.get(aci, DEFAULT_COLOR_RGB)
