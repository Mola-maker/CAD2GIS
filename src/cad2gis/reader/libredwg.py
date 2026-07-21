"""LibreDWG dev-reader: Linux-only DWG inventory for the v3 reader contract.

This module is intentionally isolated from ``autocad_reader.py`` and the
Windows canonical path.  It implements ``extract_dwg_records`` so that the
v3 ``ingest()`` boundary can be exercised on Linux using LibreDWG.

Ctypes bridge provenance:
- ``_init_libredwg`` / ``_layer_name`` / ``_lwpoline_points`` are adapted
  from the newmodel legacy ``experiment/py_scripts/converter.py``
  (:244, :251 and the lazy loader).
- ``_entity_utf8_text`` / ``_parse_dwg_color`` / ``_resolve_effective_color``
  / ``_extract_dimension`` are ported from main branch
  ``experiment/py_scripts/converter.py``
  (:101-117, :291-311, :314-333, :529-547).
- The ACI-to-RGB table is ported from main branch
  ``experiment/py_scripts/schema_config.py`` (:2638-2680).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

# ── LibreDWG SWIG location (installed system-wide on the Linux dev box) ────
_LIBREDWG_PKG = "/usr/local/lib/python3.12/dist-packages"
if _LIBREDWG_PKG not in os.environ.get("PYTHONPATH", ""):
    # Try a soft insert; do not mutate caller's sys.path permanently.
    import sys

    if _LIBREDWG_PKG not in sys.path:
        sys.path.insert(0, _LIBREDWG_PKG)

try:
    import LibreDWG  # noqa: F401
    from LibreDWG import (
        Dwg_Data,
        Dwg_Object_Array_getitem,
        DWG_SUPERTYPE_ENTITY,
        DWG_TYPE_BLOCK_HEADER,
        DWG_TYPE_LAYER,
        DWG_TYPE_LINE,
        DWG_TYPE_LWPOLYLINE,
        DWG_TYPE_CIRCLE,
        DWG_TYPE_ARC,
        DWG_TYPE_TEXT,
        DWG_TYPE_MTEXT,
        DWG_TYPE_INSERT,
        DWG_TYPE_POINT,
        DWG_TYPE_DIMENSION_ALIGNED,
        DWG_TYPE_DIMENSION_LINEAR,
        DWG_TYPE_DIMENSION_ANG2LN,
        DWG_TYPE_DIMENSION_ANG3PT,
        DWG_TYPE_DIMENSION_DIAMETER,
        DWG_TYPE_DIMENSION_ORDINATE,
        DWG_TYPE_DIMENSION_RADIUS,
        DWG_TYPE_DIMENSION_r11,
        DWG_TYPE_HATCH,
        DWG_TYPE_SPLINE,
        DWG_TYPE_ELLIPSE,
        DWG_TYPE_POLYLINE_2D,
        DWG_TYPE_POLYLINE_3D,
        DWG_TYPE_SEQEND,
        new_Dwg_Object_Array,
        dwg_read_file,
    )
except ImportError as exc:  # pragma: no cover - runtime guard for non-Linux
    raise RuntimeError(
        "LibreDWG Python bindings not found; "
        "expected at /usr/local/lib/python3.12/dist-packages"
    ) from exc


# ── Ctypes bridge to LibreDWG ──────────────────────────────────────────────
_libdwg = None
_libc = None


def _init_libredwg():
    """Lazy-init the LibreDWG ctypes bridge. Returns (libdwg, libc)."""
    global _libdwg, _libc
    if _libdwg is not None:
        return _libdwg, _libc
    try:
        _libdwg = ctypes.CDLL("/usr/local/lib/libredwg.so")
    except OSError as exc:
        raise RuntimeError(
            "LibreDWG shared library not found at /usr/local/lib/libredwg.so"
        ) from exc
    _libc = ctypes.CDLL("libc.so.6")

    _libdwg.dwg_ent_get_layer_name.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    _libdwg.dwg_ent_get_layer_name.restype = ctypes.c_char_p

    _libdwg.dwg_ent_lwpline_get_numpoints.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    _libdwg.dwg_ent_lwpline_get_numpoints.restype = ctypes.c_int
    _libdwg.dwg_ent_lwpline_get_points.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    _libdwg.dwg_ent_lwpline_get_points.restype = ctypes.c_void_p

    _libdwg.dwg_dynapi_entity_utf8text.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_void_p,
    ]
    _libdwg.dwg_dynapi_entity_utf8text.restype = ctypes.c_bool
    _libc.free.argtypes = [ctypes.c_void_p]
    return _libdwg, _libc


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


def _entity_utf8_text(struct_ptr, entity_name, field_name):
    """Read a string field via dynapi, converting UTF-16 (TU) to UTF-8."""
    _libdwg, _libc = _init_libredwg()
    out = ctypes.c_char_p(None)
    isnew = ctypes.c_int(0)
    ok = _libdwg.dwg_dynapi_entity_utf8text(
        struct_ptr,
        entity_name.encode(),
        field_name.encode(),
        ctypes.byref(out),
        ctypes.byref(isnew),
        None,
    )
    if not ok or out.value is None:
        return ""
    try:
        return out.value.decode("utf-8", errors="replace")
    finally:
        if isnew.value:
            _libc.free(ctypes.cast(out, ctypes.c_void_p))


def _parse_dwg_color(color):
    """Decode a LibreDWG Dwg_Color struct → (aci, truecolor_rgb or None)."""
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


def _resolve_effective_color(
    entity_aci, entity_tc, entity_linetype, layer_name, layer_style_table
):
    """Resolve entity colour with ByLayer/ByBlock fallback to layer table."""
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


def _extract_dimension(dim_struct, union_name):
    """Extract measurement and geometry from a DIMENSION entity struct."""
    d = dim_struct
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


# ── AutoCAD ACI palette (ported from main schema_config.py) ────────────────
def _hsv_bytes(hue_deg, sat, val):
    """HSV → RGB with AutoCAD's floor rounding (val is 0..255)."""
    c = val * sat
    hp = (hue_deg / 60.0) % 6
    x = c * (1 - abs(hp % 2 - 1))
    m = val - c
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][
        int(hp)
    ]
    return int(r + m), int(g + m), int(b + m)


def _generate_aci_table():
    """Standard AutoCAD 255-colour palette as {aci: '#RRGGBB'}."""
    table = {
        1: (255, 0, 0),
        2: (255, 255, 0),
        3: (0, 255, 0),
        4: (0, 255, 255),
        5: (0, 0, 255),
        6: (255, 0, 255),
        7: (0, 0, 0),
        8: (65, 65, 65),
        9: (128, 128, 128),
        250: (51, 51, 51),
        251: (91, 91, 91),
        252: (132, 132, 132),
        253: (173, 173, 173),
        254: (214, 214, 214),
        255: (255, 255, 255),
    }
    value_levels = {0: 255, 2: 204, 4: 153, 6: 127, 8: 76}
    for aci in range(10, 250):
        hue = ((aci - 10) // 10) * 15
        offset = (aci - 10) % 10
        val = value_levels[offset - offset % 2]
        sat = 0.5 if offset % 2 else 1.0
        table[aci] = _hsv_bytes(hue, sat, val)
    return {aci: "#%02X%02X%02X" % rgb for aci, rgb in table.items()}


ACI_TO_RGB = _generate_aci_table()
DEFAULT_COLOR_RGB = "#404040"


def aci_to_rgb(aci):
    """Map an ACI index to '#RRGGBB'; out-of-range → neutral gray."""
    return ACI_TO_RGB.get(aci, DEFAULT_COLOR_RGB)


# ── Constants and type helpers ─────────────────────────────────────────────
_DIM_TYPES = {
    DWG_TYPE_DIMENSION_ALIGNED,
    DWG_TYPE_DIMENSION_LINEAR,
    DWG_TYPE_DIMENSION_ANG2LN,
    DWG_TYPE_DIMENSION_ANG3PT,
    DWG_TYPE_DIMENSION_DIAMETER,
    DWG_TYPE_DIMENSION_ORDINATE,
    DWG_TYPE_DIMENSION_RADIUS,
    DWG_TYPE_DIMENSION_r11,
}

_OBJECT_NAMES = {
    "LINE": "ACDBLINE",
    "LWPOLYLINE": "ACDBLWPOLYLINE",
    "POLYLINE": "ACDBPOLYLINE",
    "POLYLINE_2D": "ACDBPOLYLINE",
    "POLYLINE_3D": "ACDBPOLYLINE",
    "CIRCLE": "ACDBCIRCLE",
    "ARC": "ACDBARC",
    "SPLINE": "ACDBSPLINE",
    "ELLIPSE": "ACDBELLIPSE",
    "POINT": "ACDBPOINT",
    "INSERT": "ACDBBLOCKREFERENCE",
    "TEXT": "ACDBTEXT",
    "MTEXT": "ACDBMTEXT",
    "ATTRIB": "ACDBATTRIBUTE",
    "ATTDEF": "ACDBATTRIBUTEDEFINITION",
    "MLEADER": "ACDBMLEADER",
    "MULTILEADER": "ACDBMLEADER",
    "TABLE": "ACDBTABLE",
    "DIMENSION_ALIGNED": "ACDBDIMENSION",
    "DIMENSION_LINEAR": "ACDBDIMENSION",
    "DIMENSION_ANG2LN": "ACDBDIMENSION",
    "DIMENSION_ANG3PT": "ACDBDIMENSION",
    "DIMENSION_DIAMETER": "ACDBDIMENSION",
    "DIMENSION_ORDINATE": "ACDBDIMENSION",
    "DIMENSION_RADIUS": "ACDBDIMENSION",
    "DIMENSION_r11": "ACDBDIMENSION",
    "HATCH": "ACDBHATCH",
    "SEQEND": "ACDBSEQEND",
}

_RAW_PROPERTIES_SCHEMA = "cad2gis-raw-properties-v1"
_CURVE_FACTS_SCHEMA = "cad2gis-curve-facts-v1"

_SYNTHETIC_METADATA_MARKER = "__CAD2GIS_SYNTHETIC_METADATA_EVIDENCE_7f3a9c__"

# Control records (BLOCK/ENDBLK/SEQEND) stay in inventory but are not model-space
# drawable entities — the AutoCAD canonical census (6,940) excludes them.
_CONTROL_TYPE_NAMES = {"BLOCK", "ENDBLK", "SEQEND"}


class DWGRecordInventory(list):
    """Flat record inventory with reader-protocol diagnostics attached."""

    def __init__(self, values=(), *, diagnostics=None):
        super().__init__(values)
        self.diagnostics = dict(diagnostics or {})


def _type_name(dwg_type: int) -> str:
    """Map a LibreDWG type constant back to its DWG_TYPE_* name suffix."""
    for attr in dir(LibreDWG):
        if attr.startswith("DWG_TYPE_") and getattr(LibreDWG, attr) == dwg_type:
            return attr[9:]
    return f"TYPE_{dwg_type}"


def _acdb_object_name(type_name: str) -> str:
    return _OBJECT_NAMES.get(type_name, f"ACDB{type_name}")


def _chord_length(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    total = 0.0
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _classify_layout_role(layout_name: str) -> str:
    name = (layout_name or "").strip()
    if name.upper().startswith("BLOCKDEF:"):
        return "block_definition"
    if name.casefold() == "model":
        return "model"
    return "layout"


def _flush_cursor(diagnostics: dict, path: Path) -> None:
    try:
        path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    except Exception:
        pass


def _read_block_header_names(data, anon_fallback: dict[int, str] | None = None) -> dict[int, str]:
    """Map block header handle values to UTF-8 block names.

    Anonymous headers (*U/*D) decode without the numeric suffix via dynapi on
    this R2018 file; ``anon_fallback`` (dwgread JSON side channel) supplies the
    full numbered name keyed by block header handle.
    """
    headers: dict[int, str] = {}
    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue
        if obj.type != DWG_TYPE_BLOCK_HEADER:
            continue
        try:
            bh = obj.tio.object.tio.BLOCK_HEADER
            ptr = int(bh.this)
            name = _entity_utf8_text(ptr, "BLOCK_HEADER", "name")
            if anon_fallback:
                name = anon_fallback.get(obj.handle.value, name)
            headers[obj.handle.value] = name
        except Exception:
            continue
    return headers


_ANON_NAME_RE = re.compile(r"^\*[UD]\d+$")


def _read_anon_block_names_json(source: Path, source_sha256: str) -> dict[int, str]:
    """Resolve anonymous block effective names (*U##/*D##) via dwgread JSON.

    LibreDWG dynapi decodes anonymous BLOCK_HEADER names without the numeric
    suffix on this R2018 file.  The ``dwgread -O json`` side channel (see wiki
    libredwg-swig-utf-16-r2018-dwg) carries each bare BLOCK_HEADER plus a
    following companion entry holding the full numbered name; pairing is
    order-preserving on handle value (validated against the canonical AutoCAD
    INSERT census for APD).  Results are cached under /tmp by source hash.
    """
    cache = Path(tempfile.gettempdir()) / f"libredwg_blocks_{source_sha256[:16]}.json"
    if not cache.exists():
        try:
            proc = subprocess.run(
                ["dwgread", "-O", "json", str(source)],
                capture_output=True,
                timeout=600,
                check=False,
            )
        except Exception:
            return {}
        if proc.returncode != 0 or not proc.stdout:
            return {}
        cache.write_bytes(proc.stdout)
    try:
        doc = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return {}

    bare: list[int] = []
    numbered: list[tuple[int, str]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            name = node.get("name")
            handle = node.get("handle")
            if (
                isinstance(name, str)
                and isinstance(handle, list)
                and len(handle) >= 3
                and isinstance(handle[-1], int)
            ):
                hv = handle[-1]
                if node.get("object") == "BLOCK_HEADER" and name in ("*U", "*D"):
                    bare.append(hv)
                elif _ANON_NAME_RE.match(name):
                    numbered.append((hv, name))
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(doc)
    bare.sort()
    numbered.sort()
    mapping: dict[int, str] = {}
    j = 0
    for hv in bare:
        while j < len(numbered) and numbered[j][0] <= hv:
            j += 1
        if j < len(numbered) and numbered[j][0] - hv <= 5:
            mapping[hv] = numbered[j][1]
            j += 1
    return mapping


def _read_layer_styles(data) -> dict[str, dict[str, Any]]:
    """Build a minimal layer style table (color only; linetype defaults)."""
    styles: dict[str, dict[str, Any]] = {}
    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue
        if obj.type != DWG_TYPE_LAYER:
            continue
        try:
            layer = obj.tio.object.tio.LAYER
            ptr = int(layer.this)
            name = _entity_utf8_text(ptr, "LAYER", "name")
            aci, tc = _parse_dwg_color(layer.color)
            styles[name] = {
                "aci": aci,
                "truecolor": tc,
                "linetype": "Continuous",
                "lineweight": -1,
            }
        except Exception:
            continue
    return styles


def _resolve_layout(
    entity, block_headers: dict[int, str]
) -> tuple[str, str, str, list[str]]:
    """Return (layout, layout_role, cad_role, reasons) for an entity.

    entmode is authoritative when present:
      2 = model space, 1 = paper space, 0 = block definition.
    Owner handle supplies the block name for block-definition entities.
    """
    reasons: list[str] = []
    entmode = getattr(entity, "entmode", None)
    if entmode == 2:
        layout = "Model"
    elif entmode == 1:
        layout = "Paper"
    elif entmode == 0:
        oh = entity.ownerhandle
        if oh is not None:
            try:
                owner = oh.absolute_ref
                name = block_headers.get(owner, "")
            except Exception:
                name = ""
                reasons.append("libredwg_owner_handle_unreadable")
        else:
            name = ""
        if name:
            layout = f"BLOCKDEF:{name}"
        else:
            layout = "BLOCKDEF:"
            reasons.append("libredwg_block_name_unreadable")
    else:
        # Fallback to owner-handle heuristic for unexpected entmode values.
        oh = entity.ownerhandle
        if oh is None:
            layout = "Model"
        else:
            try:
                owner = oh.absolute_ref
                name = block_headers.get(owner, "")
            except Exception:
                name = ""
                reasons.append("libredwg_owner_handle_unreadable")
            if name == "*Paper_Space" or name.startswith("*Paper_Space"):
                layout = "Paper"
            elif name == "*Model_Space":
                layout = "Model"
            elif name.startswith("*"):
                layout = f"Special:{name}"
                reasons.append("libredwg_special_space_owner")
            elif name:
                layout = f"BLOCKDEF:{name}"
            else:
                layout = "Unknown"
                reasons.append("libredwg_unknown_layout")
    role = _classify_layout_role(layout)
    cad_role = role
    return layout, role, cad_role, reasons


def _curve_facts(
    primitive_type: str,
    points: list[tuple[float, float]],
    closed: bool,
    elevation: float | None,
    normal: tuple[float, float, float] | None,
    bulges: list[float] | None,
    native_length: float | None,
) -> dict[str, Any]:
    vertices = [[x, y, 0.0] for (x, y) in points]
    if bulges is None:
        bulges = [0.0] * len(vertices)
    elif len(bulges) != len(vertices):
        bulges = (bulges + [0.0] * len(vertices))[: len(vertices)]
    facts = {
        "schema_version": _CURVE_FACTS_SCHEMA,
        "coordinate_system": "WCS",
        "primitive_type": primitive_type,
        "vertices_wcs": vertices,
        "bulges": bulges,
        "elevation": elevation,
        "normal": list(normal) if normal else None,
        "extrusion": None,
        "closed": closed,
        "primitive_parameters": {},
        "native_length": native_length,
        "native_length_source": "libredwg_chord_length",
    }
    return facts


def _build_record(
    *,
    source_path: Path,
    source_sha256: str,
    obj,
    entity,
    entity_ptr: int,
    dwg_type_name: str,
    object_name: str,
    layout: str,
    layout_role: str,
    cad_role: str,
    layer_styles: dict[str, dict[str, Any]],
    reasons: list[str],
    anon_block_names: dict[int, str] | None = None,
) -> dict[str, Any] | None:
    """Build one v3-compatible record from a LibreDWG entity."""
    handle = obj.handle.value
    handle_hex = f"{handle:X}"
    entity_key = hashlib.sha256(
        f"{source_sha256}|{handle_hex}|{layout}".encode("utf-8")
    ).hexdigest()

    layer = _layer_name(entity_ptr) or "0"
    entity_aci, entity_tc = _parse_dwg_color(entity.color)
    # Entity ltype reference is not dereferenced in this dev reader; default.
    entity_linetype = "ByLayer"
    entity_lineweight = int(getattr(entity, "linewt", -1) or -1)

    color_aci, color_rgb, _style_key = _resolve_effective_color(
        entity_aci, entity_tc, entity_linetype, layer, layer_styles
    )

    points: list[tuple[float, float]] = []
    centroid: tuple[float, float] = (0.0, 0.0)
    closed = False
    text = ""
    block_name = ""
    block_attributes: dict[str, str] = {}
    dimension_value: float | None = None
    dimension_text_override = ""
    native_length: float | None = None
    scale_x, scale_y, scale_z = 1.0, 1.0, 1.0
    rotation = 0.0
    owner_handle = ""
    curve_facts: dict[str, Any] | None = None
    geometry_status = "unavailable"
    inventory_support_status = "full"

    try:
        oh = entity.ownerhandle
        if oh is not None:
            owner_handle = f"{oh.absolute_ref:X}"
    except Exception:
        owner_handle = ""

    struct_name = _DIM_STRUCT_NAMES.get(obj.type, dwg_type_name)
    try:
        struct_ptr = int(getattr(entity.tio, struct_name).this)
    except Exception:
        struct_ptr = None

    # ── geometry extraction per type ─────────────────────────────────────
    if dwg_type_name == "LWPOLYLINE":
        try:
            ent = entity.tio.LWPOLYLINE
            points = _lwpoline_points(entity)
            closed = bool(ent.flag & 1)
            centroid = _centroid(points)
            native_length = _chord_length(points)
            elevation = float(ent.elevation) if hasattr(ent, "elevation") else None
            extrusion = getattr(ent, "extrusion", None)
            if extrusion is not None:
                normal = (extrusion.x, extrusion.y, extrusion.z)
                if normal == (0.0, 0.0, 0.0):
                    normal = (0.0, 0.0, 1.0)
            else:
                normal = (0.0, 0.0, 1.0)
            bulges: list[float] | None = None
            if hasattr(ent, "bulges") and ent.num_bulges:
                bulges = []
                try:
                    # Best-effort: dynapi path is fragile, so default to zeros.
                    bulges = [0.0] * len(points)
                    reasons.append("libredwg_bulge_array_unread")
                except Exception:
                    pass
            curve_facts = _curve_facts(
                "lwpolyline", points, closed, elevation, normal, bulges, native_length
            )
            geometry_status = "available"
        except Exception as exc:
            reasons.append(f"libredwg_lwpoline_geometry_error[{type(exc).__name__}]")

    elif dwg_type_name == "LINE":
        try:
            ent = entity.tio.LINE
            start = (ent.start.x, ent.start.y)
            end = (ent.end.x, ent.end.y)
            points = [start, end]
            centroid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            native_length = math.hypot(end[0] - start[0], end[1] - start[1])
            curve_facts = _curve_facts(
                "line", points, False, None, (0.0, 0.0, 1.0), None, native_length
            )
            geometry_status = "available"
        except Exception as exc:
            reasons.append(f"libredwg_line_geometry_error[{type(exc).__name__}]")

    elif dwg_type_name in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
        try:
            ent = getattr(entity.tio, dwg_type_name)
            x, y = ent.ins_pt.x, ent.ins_pt.y
            points = [(x, y)]
            centroid = (x, y)
            if struct_ptr:
                text = _entity_utf8_text(struct_ptr, dwg_type_name, "text_value")
                if not text and dwg_type_name == "MTEXT":
                    text = _entity_utf8_text(struct_ptr, "MTEXT", "text")
            geometry_status = "available"
        except Exception as exc:
            reasons.append(f"libredwg_text_geometry_error[{type(exc).__name__}]")

    elif dwg_type_name == "INSERT":
        try:
            ent = entity.tio.INSERT
            x, y = ent.ins_pt.x, ent.ins_pt.y
            points = [(x, y)]
            centroid = (x, y)
            scale_x = float(ent.scale.x)
            scale_y = float(ent.scale.y)
            scale_z = float(ent.scale.z)
            rotation = float(ent.rotation)
            # Block name from block header reference (INSERT.block_name is empty).
            bh_ref = ent.block_header
            if bh_ref and bh_ref.obj and bh_ref.obj.type == DWG_TYPE_BLOCK_HEADER:
                bh = bh_ref.obj.tio.object.tio.BLOCK_HEADER
                block_name = _entity_utf8_text(int(bh.this), "BLOCK_HEADER", "name")
                if anon_block_names:
                    # Anonymous headers decode without the numeric suffix via
                    # dynapi; the dwgread JSON side channel carries the full
                    # numbered/effective name keyed by block header handle.
                    block_name = anon_block_names.get(
                        bh_ref.obj.handle.value, block_name
                    )
            if not block_name:
                reasons.append("libredwg_insert_block_name_unreadable")
            geometry_status = "available"
            # Attributes are not traversed in this dev reader.
            block_attributes = {}
            reasons.append("libredwg_block_attributes_unread")
        except Exception as exc:
            reasons.append(f"libredwg_insert_geometry_error[{type(exc).__name__}]")

    elif dwg_type_name == "POINT":
        try:
            ent = entity.tio.POINT
            points = [(ent.x, ent.y)]
            centroid = (ent.x, ent.y)
            geometry_status = "available"
        except Exception as exc:
            reasons.append(f"libredwg_point_geometry_error[{type(exc).__name__}]")

    elif dwg_type_name == "DIMENSION":
        try:
            dim_struct = getattr(entity.tio, struct_name)
            dim = _extract_dimension(dim_struct, struct_name)
            dimension_value = dim["measurement"]
            pts = [dim["def_pt"]]
            if dim["xline1"]:
                pts.append(dim["xline1"])
            if dim["xline2"]:
                pts.append(dim["xline2"])
            points = pts
            centroid = _centroid(points)
            if struct_ptr:
                dimension_text_override = _entity_utf8_text(
                    struct_ptr, struct_name, "text_value"
                )
            geometry_status = "available"
        except Exception as exc:
            reasons.append(f"libredwg_dimension_error[{type(exc).__name__}]")

    elif dwg_type_name == "HATCH":
        reasons.append("libredwg_hatch_reader")
        inventory_support_status = "inventory_only"

    else:
        reasons.append("libredwg_unsupported_type")
        inventory_support_status = "inventory_only"

    if geometry_status != "available" and inventory_support_status != "inventory_only":
        inventory_support_status = "inventory_only"
        reasons.append("geometry_unavailable")

    reasons = sorted({r for r in reasons if r})

    raw_properties = {
        "schema_version": _RAW_PROPERTIES_SCHEMA,
        "extraction_backend": "libredwg",
        "reader_backend_status": "supported" if inventory_support_status == "full" else "unsupported",
        "object_name": object_name,
        "dwg_type_name": dwg_type_name,
        "handle": handle_hex,
        "owner_handle": owner_handle,
        "layout": layout,
        "layer": layer,
        "block_name": block_name,
        "block_effective_name": block_name,
        "block_reference_name": block_name,
        "text": text,
        "raw_text": text,
        "text_source": "entity_text" if dwg_type_name in ("TEXT", "MTEXT") else "",
        "attribute_tags": sorted(block_attributes),
        "block_attributes": block_attributes,
        "dynamic_block_properties": {},
        "dynamic_block_properties_status": "not_applicable",
        "dimension_measurement": dimension_value,
        "dimension_text_override": dimension_text_override,
        "native_length": native_length,
        "native_length_source": "libredwg_chord_length" if native_length is not None else "",
        "curve_facts": curve_facts or {},
        "curve_fingerprint": "",
        "insertion_point": None,
        "insertion_point_wcs": None,
        "insertion_point_status": "not_applicable",
        "block_base_point": None,
        "block_base_point_status": "not_applicable",
        "normal": None,
        "normal_status": "not_applicable",
        "extrusion": None,
        "extrusion_status": "not_applicable",
        "container_block_name": "",
        "nesting_context": "drawing_space",
        "block_definition_handle": "",
        "block_flags": None,
        "external_reference_path": "",
        "external_reference_status": "not_external",
        "geometry_status": geometry_status,
        "inventory_support_status": inventory_support_status,
        "transform_facts": {},
        "scale_x": scale_x,
        "scale_y": scale_y,
        "scale_z": scale_z,
        "rotation": rotation,
        "entity_rotation": rotation,
        "aci_color": color_aci,
        "true_color": color_rgb,
        "linetype": entity_linetype,
        "lineweight": entity_lineweight,
        "entity_aci_color": entity_aci,
        "layer_aci_color": layer_styles.get(layer, {}).get("aci", 7),
        "entity_true_color": "#%06X" % entity_tc if entity_tc is not None else "",
        "layer_true_color": "",
        "entity_linetype": entity_linetype,
        "layer_linetype": layer_styles.get(layer, {}).get("linetype", "Continuous"),
        "entity_lineweight": entity_lineweight,
        "layer_lineweight": layer_styles.get(layer, {}).get("lineweight", -1),
        "unsupported_reason": ";".join(reasons),
        "unsupported_reasons": reasons,
    }

    record = {
        "entity_key": entity_key,
        "source_sha256": source_sha256,
        "source_file": str(source_path),
        "handle": handle_hex,
        "layout": layout,
        "layout_role": layout_role,
        "cad_role": cad_role,
        "layer": layer,
        "object_name": object_name,
        "dwg_type_name": dwg_type_name,
        "points": points,
        "centroid": centroid,
        "closed": closed,
        "text": text,
        "block_name": block_name,
        "block_attributes": block_attributes,
        "dimension_value": dimension_value,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "scale_z": scale_z,
        "owner_handle": owner_handle,
        "dimension_text_override": dimension_text_override,
        "native_length": native_length,
        "raw_properties": raw_properties,
        "curve_facts": curve_facts or {},
        "curve_fingerprint": "",
        # Style fields that from_record consumes directly.
        "aci_color": color_aci,
        "true_color": color_rgb,
        "linetype": entity_linetype,
        "lineweight": entity_lineweight,
        "rotation": rotation,
        "entity_aci_color": entity_aci,
        "layer_aci_color": layer_styles.get(layer, {}).get("aci", 7),
        "entity_true_color": "#%06X" % entity_tc if entity_tc is not None else "",
        "layer_true_color": "",
        "entity_linetype": entity_linetype,
        "layer_linetype": layer_styles.get(layer, {}).get("linetype", "Continuous"),
        "entity_lineweight": entity_lineweight,
        "layer_lineweight": layer_styles.get(layer, {}).get("lineweight", -1),
        "inventory_support_status": inventory_support_status,
    }
    return record


_DWG_TYPE_NAME_MAP: dict[int, str] = {
    DWG_TYPE_LINE: "LINE",
    DWG_TYPE_LWPOLYLINE: "LWPOLYLINE",
    DWG_TYPE_CIRCLE: "CIRCLE",
    DWG_TYPE_ARC: "ARC",
    DWG_TYPE_TEXT: "TEXT",
    DWG_TYPE_MTEXT: "MTEXT",
    DWG_TYPE_INSERT: "INSERT",
    DWG_TYPE_POINT: "POINT",
    DWG_TYPE_POLYLINE_2D: "POLYLINE_2D",
    DWG_TYPE_POLYLINE_3D: "POLYLINE_3D",
    DWG_TYPE_HATCH: "HATCH",
    DWG_TYPE_SPLINE: "SPLINE",
    DWG_TYPE_ELLIPSE: "ELLIPSE",
    DWG_TYPE_SEQEND: "SEQEND",
    # Canonical reader collapses all dimension subtypes to "DIMENSION".
    DWG_TYPE_DIMENSION_ALIGNED: "DIMENSION",
    DWG_TYPE_DIMENSION_LINEAR: "DIMENSION",
    DWG_TYPE_DIMENSION_ANG2LN: "DIMENSION",
    DWG_TYPE_DIMENSION_ANG3PT: "DIMENSION",
    DWG_TYPE_DIMENSION_DIAMETER: "DIMENSION",
    DWG_TYPE_DIMENSION_ORDINATE: "DIMENSION",
    DWG_TYPE_DIMENSION_RADIUS: "DIMENSION",
    DWG_TYPE_DIMENSION_r11: "DIMENSION",
}
_DIM_STRUCT_NAMES: dict[int, str] = {
    DWG_TYPE_DIMENSION_ALIGNED: "DIMENSION_ALIGNED",
    DWG_TYPE_DIMENSION_LINEAR: "DIMENSION_LINEAR",
    DWG_TYPE_DIMENSION_ANG2LN: "DIMENSION_ANG2LN",
    DWG_TYPE_DIMENSION_ANG3PT: "DIMENSION_ANG3PT",
    DWG_TYPE_DIMENSION_DIAMETER: "DIMENSION_DIAMETER",
    DWG_TYPE_DIMENSION_ORDINATE: "DIMENSION_ORDINATE",
    DWG_TYPE_DIMENSION_RADIUS: "DIMENSION_RADIUS",
    DWG_TYPE_DIMENSION_r11: "DIMENSION_r11",
}
_DIM_TYPE_NAMES = frozenset({"DIMENSION"})


def extract_dwg_records(source_path) -> DWGRecordInventory:
    """Return a complete DWG record inventory using LibreDWG.

    The returned object is a list-like ``DWGRecordInventory`` with a
    ``.diagnostics`` dict containing extraction_backend, skipped_rows,
    inventory_complete, metadata_evidence, and unsupported_reason_counts.
    """
    source = Path(source_path).resolve()
    source_bytes = source.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    data = Dwg_Data()
    data.object = new_Dwg_Object_Array(500000)
    read_err = dwg_read_file(str(source), data)
    if read_err != 0:
        # LibreDWG may return warnings (e.g. 68 = classes not found) and still
        # populate the file; only treat hard errors as incomplete.
        hard_errors = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
        if read_err in hard_errors:
            raise RuntimeError(
                f"LibreDWG failed to read DWG (error {read_err}): {source}"
            )

    anon_block_names = _read_anon_block_names_json(source, source_sha256)
    block_headers = _read_block_header_names(data, anon_fallback=anon_block_names)
    layer_styles = _read_layer_styles(data)
    cursor_path = Path(tempfile.gettempdir()) / f"libredwg_reader_{source_sha256[:16]}.json"

    diagnostics: dict[str, Any] = {
        "extraction_backend": "libredwg",
        "skipped_rows": 0,
        "inventory_complete": True,
        "metadata_evidence": "reader",
        "libredwg_read_error": read_err,
        "total_objects": int(data.num_objects),
        "unsupported_reason_counts": {},
        "cursor_path": str(cursor_path),
        "anon_block_names_resolved": len(anon_block_names),
    }

    records: list[dict[str, Any]] = []
    unsupported_reason_counts: Counter = Counter()
    processed = 0
    crash_count = 0

    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue
        if obj.supertype != DWG_SUPERTYPE_ENTITY:
            continue

        # Per-entity crash isolation: catch Python exceptions and keep going.
        try:
            entity = obj.tio.entity
            entity_ptr = int(entity.this)
            dwg_type = obj.type
            dwg_type_name = _DWG_TYPE_NAME_MAP.get(dwg_type, _type_name(dwg_type))
            object_name = _acdb_object_name(dwg_type_name)

            if dwg_type_name in _CONTROL_TYPE_NAMES:
                layout, layout_role, cad_role, layout_reasons = (
                    "Control",
                    "control",
                    "control",
                    [],
                )
            else:
                layout, layout_role, cad_role, layout_reasons = _resolve_layout(
                    entity, block_headers
                )

            record = _build_record(
                source_path=source,
                source_sha256=source_sha256,
                obj=obj,
                entity=entity,
                entity_ptr=entity_ptr,
                dwg_type_name=dwg_type_name,
                object_name=object_name,
                layout=layout,
                layout_role=layout_role,
                cad_role=cad_role,
                layer_styles=layer_styles,
                reasons=list(layout_reasons),
                anon_block_names=anon_block_names,
            )
            if record is None:
                continue

            records.append(record)
            for reason in record["raw_properties"]["unsupported_reasons"]:
                unsupported_reason_counts[reason] += 1

        except Exception as exc:
            crash_count += 1
            unsupported_reason_counts[f"libredwg_reader_crash[{type(exc).__name__}]"] += 1
            diagnostics["inventory_complete"] = False
            continue

        processed += 1
        if processed % 500 == 0:
            diagnostics["unsupported_reason_counts"] = dict(
                sorted(unsupported_reason_counts.items())
            )
            diagnostics["processed_entities"] = processed
            diagnostics["crash_count"] = crash_count
            _flush_cursor(diagnostics, cursor_path)

    # Try to read header metadata evidence; fall back to synthetic on failure.
    metadata_text = ""
    try:
        insunits = int(data.header_vars.INSUNITS)
        metadata_text = f"INSUNITS={insunits}"
    except Exception:
        pass
    # CGEOCS is not exposed by LibreDWG in this DWG, so use the synthetic path.
    if "CGEOCS=" not in metadata_text:
        metadata_text += (
            f";CGEOCS=WGS84.PseudoMercator;{_SYNTHETIC_METADATA_MARKER}"
        )
        diagnostics["metadata_evidence"] = "synthetic"
    else:
        diagnostics["metadata_evidence"] = "reader"

    # Prepend a synthetic DOCUMENT_METADATA record.
    metadata_record = {
        "entity_key": hashlib.sha256(
            f"{source_sha256}|DOCUMENT_METADATA|".encode("utf-8")
        ).hexdigest(),
        "source_sha256": source_sha256,
        "source_file": str(source),
        "handle": "DOCUMENT_METADATA",
        "layout": "",
        "layout_role": "",
        "cad_role": "",
        "layer": "0",
        "object_name": "DOCUMENT_METADATA",
        "dwg_type_name": "DOCUMENT_METADATA",
        "points": [],
        "centroid": (0.0, 0.0),
        "closed": False,
        "text": metadata_text,
        "block_name": "",
        "block_attributes": {},
        "dimension_value": None,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "scale_z": 1.0,
        "owner_handle": "",
        "dimension_text_override": "",
        "native_length": None,
        "raw_properties": {
            "schema_version": _RAW_PROPERTIES_SCHEMA,
            "extraction_backend": "libredwg",
            "reader_backend_status": "supported",
            "object_name": "DOCUMENT_METADATA",
            "dwg_type_name": "DOCUMENT_METADATA",
            "handle": "DOCUMENT_METADATA",
            "text": metadata_text,
            "unsupported_reasons": [],
            "unsupported_reason": "",
            "geometry_status": "unavailable",
            "inventory_support_status": "full",
        },
        "curve_facts": {},
        "curve_fingerprint": "",
        "aci_color": 256,
        "true_color": "",
        "linetype": "ByLayer",
        "lineweight": -1,
        "rotation": 0.0,
        "entity_aci_color": 256,
        "layer_aci_color": 7,
        "entity_true_color": "",
        "layer_true_color": "",
        "entity_linetype": "ByLayer",
        "layer_linetype": "Continuous",
        "entity_lineweight": -1,
        "layer_lineweight": -1,
        "inventory_support_status": "full",
    }
    records.insert(0, metadata_record)

    diagnostics["unsupported_reason_counts"] = dict(
        sorted(unsupported_reason_counts.items())
    )
    diagnostics["processed_entities"] = processed
    diagnostics["crash_count"] = crash_count
    diagnostics["returned_records"] = len(records)
    _flush_cursor(diagnostics, cursor_path)

    return DWGRecordInventory(records, diagnostics=diagnostics)
