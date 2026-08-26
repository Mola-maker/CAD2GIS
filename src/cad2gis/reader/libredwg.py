"""LibreDWG cross-platform reader: DWG inventory for the v3 reader contract.

This module is the primary cross-platform reader, replacing the legacy
Windows-only AutoCAD canonical path.  It implements ``extract_dwg_records``
so that the v3 ``ingest()`` boundary can be exercised on Linux, Windows,
and macOS using LibreDWG.

Ctypes bridge provenance:
- ``_init_libredwg`` / ``_layer_name`` / ``_lwpoline_points`` are adapted
  from the newmodel legacy ``converter.py``
  (:244, :251 and the lazy loader).
- ``_entity_utf8_text`` / ``_parse_dwg_color`` / ``_resolve_effective_color``
  / ``_extract_dimension`` are ported from main branch
  ``converter.py``
  (:101-117, :291-311, :314-333, :529-547).
- The ACI-to-RGB table is ported from main branch
  ``schema_config.py`` (:2638-2680).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib
import _ctypes
import hashlib
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from .contracts import ReaderCapability, ReaderUnavailableError

_PYTHON_PATH_ENV = "CAD2GIS_LIBREDWG_PYTHON_PATH"
_LIBRARY_ENV = "CAD2GIS_LIBREDWG_LIBRARY"
_LEGACY_LIBRARY_ENV = "CAD2GIS_LIBREDWG_DLL"

_BINDING_EXPORTS = (
    "Dwg_Data",
    "Dwg_Object_Array_getitem",
    "DWG_SUPERTYPE_ENTITY",
    "DWG_TYPE_BLOCK_HEADER",
    "DWG_TYPE_LAYER",
    "DWG_TYPE_LINE",
    "DWG_TYPE_LWPOLYLINE",
    "DWG_TYPE_CIRCLE",
    "DWG_TYPE_ARC",
    "DWG_TYPE_TEXT",
    "DWG_TYPE_MTEXT",
    "DWG_TYPE_INSERT",
    "DWG_TYPE_POINT",
    "DWG_TYPE_DIMENSION_ALIGNED",
    "DWG_TYPE_DIMENSION_LINEAR",
    "DWG_TYPE_DIMENSION_ANG2LN",
    "DWG_TYPE_DIMENSION_ANG3PT",
    "DWG_TYPE_DIMENSION_DIAMETER",
    "DWG_TYPE_DIMENSION_ORDINATE",
    "DWG_TYPE_DIMENSION_RADIUS",
    "DWG_TYPE_DIMENSION_r11",
    "DWG_TYPE_HATCH",
    "DWG_TYPE_SPLINE",
    "DWG_TYPE_ELLIPSE",
    "DWG_TYPE_POLYLINE_2D",
    "DWG_TYPE_POLYLINE_3D",
    "DWG_TYPE_SEQEND",
    "DWG_TYPE_ATTRIB",
    "new_Dwg_Object_Array",
    "dwg_read_file",
)

LibreDWG: Any | None = None
Dwg_Data: Any | None = None
Dwg_Object_Array_getitem: Any | None = None
DWG_SUPERTYPE_ENTITY: Any | None = None
DWG_TYPE_BLOCK_HEADER: Any | None = None
DWG_TYPE_LAYER: Any | None = None
DWG_TYPE_LINE: Any | None = None
DWG_TYPE_LWPOLYLINE: Any | None = None
DWG_TYPE_CIRCLE: Any | None = None
DWG_TYPE_ARC: Any | None = None
DWG_TYPE_TEXT: Any | None = None
DWG_TYPE_MTEXT: Any | None = None
DWG_TYPE_INSERT: Any | None = None
DWG_TYPE_POINT: Any | None = None
DWG_TYPE_DIMENSION_ALIGNED: Any | None = None
DWG_TYPE_DIMENSION_LINEAR: Any | None = None
DWG_TYPE_DIMENSION_ANG2LN: Any | None = None
DWG_TYPE_DIMENSION_ANG3PT: Any | None = None
DWG_TYPE_DIMENSION_DIAMETER: Any | None = None
DWG_TYPE_DIMENSION_ORDINATE: Any | None = None
DWG_TYPE_DIMENSION_RADIUS: Any | None = None
DWG_TYPE_DIMENSION_r11: Any | None = None
DWG_TYPE_HATCH: Any | None = None
DWG_TYPE_SPLINE: Any | None = None
DWG_TYPE_ELLIPSE: Any | None = None
DWG_TYPE_POLYLINE_2D: Any | None = None
DWG_TYPE_POLYLINE_3D: Any | None = None
DWG_TYPE_SEQEND: Any | None = None
DWG_TYPE_ATTRIB: Any | None = None
new_Dwg_Object_Array: Any | None = None
dwg_read_file: Any | None = None

_reader_lock = threading.RLock()
_python_bindings = None
_python_bindings_error: BaseException | None = None


def _python_binding_paths() -> tuple[str, ...]:
    """Return explicit binding paths; normal imports use this interpreter."""
    configured = os.environ.get(_PYTHON_PATH_ENV)
    values = (
        []
        if configured is None
        else [item for item in configured.split(os.pathsep) if item]
    )
    normalized: list[str] = []
    for value in values:
        path = Path(value).expanduser()
        if path.is_file():
            path = path.parent
        normalized.append(str(path))
    return tuple(normalized)


def _load_python_bindings():
    """Load the SWIG module and install its exports into module globals."""
    global LibreDWG, _python_bindings, _python_bindings_error
    with _reader_lock:
        if _python_bindings is not None:
            return _python_bindings

        paths = _python_binding_paths()
        for path in reversed(paths):
            if path not in sys.path:
                sys.path.insert(0, path)

        configured = os.environ.get(_PYTHON_PATH_ENV)
        previous_module = None
        module = None
        configured_module = configured is not None
        try:
            if configured_module:
                if not paths:
                    raise ModuleNotFoundError(
                        "CAD2GIS_LIBREDWG_PYTHON_PATH did not configure a search path"
                    )
                spec = PathFinder.find_spec("LibreDWG", list(paths))
                if spec is None or spec.loader is None:
                    raise ModuleNotFoundError(
                        "LibreDWG Python bindings were not found in the configured path"
                    )
                previous_module = sys.modules.pop("LibreDWG", None)
                module = module_from_spec(spec)
                sys.modules["LibreDWG"] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop("LibreDWG", None)
                    if previous_module is not None:
                        sys.modules["LibreDWG"] = previous_module
                    raise
            else:
                module = importlib.import_module("LibreDWG")
            exports = {name: getattr(module, name) for name in _BINDING_EXPORTS}
        except Exception as exc:
            if configured_module and (
                module is None or sys.modules.get("LibreDWG") is module
            ):
                if module is not None:
                    sys.modules.pop("LibreDWG", None)
                if previous_module is not None:
                    sys.modules["LibreDWG"] = previous_module
            _python_bindings_error = exc
            return None

        LibreDWG = module
        for name, value in exports.items():
            globals()[name] = value
        _python_bindings = module
        _python_bindings_error = None
        _rebuild_type_maps()
        return module


def _library_candidates() -> tuple[str, ...]:
    """Return the explicit library override or platform defaults."""
    configured = os.environ.get(_LIBRARY_ENV)
    if configured is None:
        configured = os.environ.get(_LEGACY_LIBRARY_ENV)
    if configured is not None:
        return (configured,)
    if os.name == "nt":
        return (
            "C:/Program Files/LibreDWG/libredwg.dll",
            "C:/libredwg/libredwg.dll",
            "libredwg.dll",
        )
    if sys.platform == "darwin":
        return (
            "/usr/local/lib/libredwg.dylib",
            "/opt/homebrew/lib/libredwg.dylib",
            "libredwg.dylib",
        )
    return (
        "/usr/local/lib/libredwg.so",
        "/usr/lib/libredwg.so",
        "libredwg.so",
    )


def _discover_library() -> str | None:
    """Find a library name without loading or initializing it."""
    configured = os.environ.get(_LIBRARY_ENV)
    if configured is None:
        configured = os.environ.get(_LEGACY_LIBRARY_ENV)
    if configured is not None:
        return configured or None
    for candidate in _library_candidates():
        if Path(candidate).is_file():
            return candidate
        if os.name == "nt" and candidate == "libredwg.dll":
            return candidate
        if Path(candidate).name == candidate:
            found = ctypes.util.find_library("redwg")
            if found:
                return found
    return None


def _explicit_library_platform_mismatch(library: str) -> bool:
    """Reject an explicitly configured library for a different OS.

    Passing an ELF ``.so`` to the Windows loader (or a PE ``.dll`` to a
    Unix loader) can block in native loader error handling before Python can
    report an actionable capability result.  Only recognized, contradictory
    suffixes are rejected; extensionless test doubles and platform loader
    names remain supported.
    """

    configured = os.environ.get(_LIBRARY_ENV)
    if configured is None:
        configured = os.environ.get(_LEGACY_LIBRARY_ENV)
    if configured is None or configured != library:
        return False
    name = Path(library).name.casefold()
    if os.name == "nt":
        return ".so" in name or name.endswith(".dylib")
    if sys.platform == "darwin":
        return name.endswith(".dll") or ".so" in name
    return name.endswith(".dll") or name.endswith(".dylib")


def libredwg_capability() -> ReaderCapability:
    with _reader_lock:
        return _libredwg_capability()


def _libredwg_capability() -> ReaderCapability:
    """Return actionable diagnostics for the optional LibreDWG backend."""
    if _load_python_bindings() is None:
        error_name = (
            type(_python_bindings_error).__name__
            if _python_bindings_error
            else "unknown error"
        )
        return ReaderCapability(
            backend="libredwg",
            available=False,
            detail=f"LibreDWG Python bindings are unavailable ({error_name}).",
            remediation=(
                "Install the LibreDWG Python bindings, optionally set "
                "CAD2GIS_LIBREDWG_PYTHON_PATH, or use "
                "CAD2GIS_READER_BACKEND=autocad."
            ),
        )
    if _libdwg is None or _libc is None:
        try:
            _init_libredwg()
        except AttributeError as exc:
            return ReaderCapability(
                backend="libredwg",
                available=False,
                detail=(
                    "LibreDWG shared library is loadable but missing required "
                    f"symbols ({type(exc).__name__})."
                ),
                remediation=(
                    "Install a matching LibreDWG shared library, optionally set "
                    "CAD2GIS_LIBREDWG_LIBRARY, or use "
                    "CAD2GIS_READER_BACKEND=autocad."
                ),
            )
        except Exception as exc:
            return ReaderCapability(
                backend="libredwg",
                available=False,
                detail=(
                    "LibreDWG shared library initialization failed "
                    f"({type(exc).__name__})."
                ),
                remediation=(
                    "Install a loadable LibreDWG shared library, optionally set "
                    "CAD2GIS_LIBREDWG_LIBRARY, or use "
                    "CAD2GIS_READER_BACKEND=autocad."
                ),
            )
    if _libdwg is None or _libc is None:
        return ReaderCapability(
            backend="libredwg",
            available=False,
            detail="LibreDWG native handles are unavailable after initialization.",
            remediation=(
                "Install LibreDWG or use CAD2GIS_READER_BACKEND=autocad."
            ),
        )
    return ReaderCapability(
        backend="libredwg",
        available=True,
        detail="LibreDWG Python bindings and shared library are discoverable.",
        remediation="No remediation required.",
    )


# ── Ctypes bridge to LibreDWG ──────────────────────────────────────────────
_libdwg = None
_libc = None


def _release_native_handle(handle) -> None:
    """Release a native loader handle that was opened during failed setup."""
    with _reader_lock:
        raw_handle = getattr(handle, "_handle", None)
        if not raw_handle:
            return
        try:
            if os.name == "nt":
                free_library = getattr(_ctypes, "FreeLibrary", None)
                if free_library is not None:
                    free_library(raw_handle)
            else:
                _ctypes.dlclose(raw_handle)
        except Exception:
            pass


def _require_libredwg() -> None:
    with _reader_lock:
        _require_libredwg_unlocked()


def _require_libredwg_unlocked() -> None:
    """Guard extraction behind an actionable native-runtime check."""
    capability = libredwg_capability()
    if not capability.available:
        message = (
            f"LibreDWG reader backend unavailable (backend={capability.backend}): "
            f"{capability.detail} {capability.remediation}"
        )
        if "CAD2GIS_READER_BACKEND=autocad" not in message:
            message += " Recovery choice: CAD2GIS_READER_BACKEND=autocad."
        raise ReaderUnavailableError(message)
    try:
        _init_libredwg()
    except Exception as exc:
        raise ReaderUnavailableError(
            "LibreDWG reader backend unavailable: shared library initialization "
            f"failed ({type(exc).__name__}). Set CAD2GIS_READER_BACKEND=autocad."
        ) from exc


def _allocator_library() -> str | None:
    if os.name == "nt":
        return "msvcrt"
    return ctypes.util.find_library("c")


def _init_libredwg():
    with _reader_lock:
        return _init_libredwg_unlocked()


def _init_libredwg_unlocked():
    """Lazy-init the LibreDWG ctypes bridge. Returns (libdwg, libc)."""
    global _libdwg, _libc
    if _libdwg is not None and _libc is not None:
        return _libdwg, _libc
    stale_native = _libdwg
    stale_allocator = _libc
    _libdwg = None
    _libc = None
    _release_native_handle(stale_native)
    if stale_allocator is not stale_native:
        _release_native_handle(stale_allocator)
    if _load_python_bindings() is None:
        raise RuntimeError("LibreDWG Python bindings are unavailable")
    library = _discover_library()
    if library is None:
        raise RuntimeError("LibreDWG shared library is unavailable")
    if _explicit_library_platform_mismatch(library):
        raise RuntimeError(
            "Configured LibreDWG shared library format does not match this platform"
        )

    native_handle = None
    allocator_handle = None
    allocator_name = None
    try:
        native_handle = ctypes.CDLL(library)
    except OSError as exc:
        raise RuntimeError("LibreDWG shared library could not be loaded") from exc

    try:
        allocator_name = _allocator_library()
        allocator_handle = ctypes.CDLL(allocator_name or None)
        native_handle.dwg_ent_get_layer_name.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        native_handle.dwg_ent_get_layer_name.restype = ctypes.c_char_p

        native_handle.dwg_ent_lwpline_get_numpoints.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        native_handle.dwg_ent_lwpline_get_numpoints.restype = ctypes.c_int
        native_handle.dwg_ent_lwpline_get_points.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        native_handle.dwg_ent_lwpline_get_points.restype = ctypes.c_void_p

        native_handle.dwg_dynapi_entity_utf8text.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_void_p,
        ]
        native_handle.dwg_dynapi_entity_utf8text.restype = ctypes.c_bool
        allocator_handle.free.argtypes = [ctypes.c_void_p]
    except Exception:
        _release_native_handle(native_handle)
        if allocator_name is not None and allocator_handle is not native_handle:
            _release_native_handle(allocator_handle)
        raise

    _libdwg = native_handle
    _libc = allocator_handle
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


_DIMENSION_DISPLAY_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _dimension_display_value(text: str) -> float | None:
    """Parse the rendered DIMENSION text (e.g. ``{\\H0.75x;50m}``) to metres.

    ``<>`` means "render the default measurement", so the raw
    ``act_measurement`` remains authoritative for those entities.  Custom
    display text may carry formatting codes whose last numeric token is the
    displayed value (AutoCAD never places a format-code number after it).
    """
    if not text or "<>" in text:
        return None
    tokens = _DIMENSION_DISPLAY_NUMBER_RE.findall(text)
    if not tokens:
        return None
    try:
        value = float(tokens[-1])
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0.0 else None


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
_DIM_TYPES: set[Any] = set()

_DIMENSION_TYPE_SPECS = (
    ("DWG_TYPE_DIMENSION_ALIGNED", "DIMENSION_ALIGNED"),
    ("DWG_TYPE_DIMENSION_LINEAR", "DIMENSION_LINEAR"),
    ("DWG_TYPE_DIMENSION_ANG2LN", "DIMENSION_ANG2LN"),
    ("DWG_TYPE_DIMENSION_ANG3PT", "DIMENSION_ANG3PT"),
    ("DWG_TYPE_DIMENSION_DIAMETER", "DIMENSION_DIAMETER"),
    ("DWG_TYPE_DIMENSION_ORDINATE", "DIMENSION_ORDINATE"),
    ("DWG_TYPE_DIMENSION_RADIUS", "DIMENSION_RADIUS"),
    ("DWG_TYPE_DIMENSION_r11", "DIMENSION_r11"),
)
_TYPE_NAME_SPECS = (
    ("DWG_TYPE_LINE", "LINE"),
    ("DWG_TYPE_LWPOLYLINE", "LWPOLYLINE"),
    ("DWG_TYPE_CIRCLE", "CIRCLE"),
    ("DWG_TYPE_ARC", "ARC"),
    ("DWG_TYPE_TEXT", "TEXT"),
    ("DWG_TYPE_MTEXT", "MTEXT"),
    ("DWG_TYPE_INSERT", "INSERT"),
    ("DWG_TYPE_POINT", "POINT"),
    ("DWG_TYPE_POLYLINE_2D", "POLYLINE_2D"),
    ("DWG_TYPE_POLYLINE_3D", "POLYLINE_3D"),
    ("DWG_TYPE_HATCH", "HATCH"),
    ("DWG_TYPE_SPLINE", "SPLINE"),
    ("DWG_TYPE_ELLIPSE", "ELLIPSE"),
    ("DWG_TYPE_SEQEND", "SEQEND"),
)


def _rebuild_type_maps() -> None:
    with _reader_lock:
        _rebuild_type_maps_unlocked()


def _rebuild_type_maps_unlocked() -> None:
    """Rebuild constant maps after the optional SWIG module is loaded."""
    global _DIM_TYPES, _DWG_TYPE_NAME_MAP, _DIM_STRUCT_NAMES
    _DIM_TYPES = {
        value
        for name, _ in _DIMENSION_TYPE_SPECS
        if (value := globals().get(name)) is not None
    }
    _DWG_TYPE_NAME_MAP = {
        value: type_name
        for name, type_name in _TYPE_NAME_SPECS
        if (value := globals().get(name)) is not None
    }
    _DWG_TYPE_NAME_MAP.update(
        {
            value: "DIMENSION"
            for name, _ in _DIMENSION_TYPE_SPECS
            if (value := globals().get(name)) is not None
        }
    )
    _DIM_STRUCT_NAMES = {
        value: struct_name
        for name, struct_name in _DIMENSION_TYPE_SPECS
        if (value := globals().get(name)) is not None
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
    # Named paper-space layout tabs (e.g. "APD - SF", the subfeeder plan
    # layout, or "Layout2") carry plan geometry and must not be discarded as
    # paper space.
    if name and name.casefold() != "paper":
        return "model"
    return "layout"


def _flush_cursor(diagnostics: dict, path: Path) -> None:
    try:
        path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    except Exception:
        pass


def _read_block_header_metadata(
    data,
    anon_fallback: dict[int, str] | None = None,
) -> tuple[dict[int, str], dict[int, tuple[float, float, float]], dict[int, str]]:
    """Map block header handles to names, base points, and read statuses.

    The dynapi BLOCK_HEADER field is ``base_pt`` (probed on all four baseline
    APD / As Plan Drawing R2018 files; see
    ``tools/diagnostics/libredwg_insert_probe.py``).  Anonymous
    headers (*U/*D) decode without the numeric suffix via dynapi on this R2018
    file; ``anon_fallback`` (dwgread JSON side channel) supplies the full
    numbered name keyed by block header handle.

    Returns ``(names, base_points, base_point_statuses)`` keyed by block
    header handle.  A missing/unreadable base point is recorded with
    ``base_point_status == "unavailable"`` and a ``None`` value is never
    invented.
    """
    names: dict[int, str] = {}
    base_points: dict[int, tuple[float, float, float]] = {}
    base_point_statuses: dict[int, str] = {}
    for i in range(data.num_objects):
        try:
            obj = Dwg_Object_Array_getitem(data.object, i)
        except Exception:
            continue
        if obj.type != DWG_TYPE_BLOCK_HEADER:
            continue
        handle = obj.handle.value
        try:
            bh = obj.tio.object.tio.BLOCK_HEADER
            ptr = int(bh.this)
            name = _entity_utf8_text(ptr, "BLOCK_HEADER", "name")
            if anon_fallback:
                name = anon_fallback.get(handle, name)
            names[handle] = name
        except Exception:
            names[handle] = ""
        try:
            base = bh.base_pt
            base_points[handle] = (
                float(base.x),
                float(base.y),
                float(base.z),
            )
            base_point_statuses[handle] = "available"
        except Exception:
            base_point_statuses[handle] = "unavailable"
    return names, base_points, base_point_statuses


def _read_block_header_names(data, anon_fallback: dict[int, str] | None = None) -> dict[int, str]:
    """Backward-compatible wrapper around :func:`_read_block_header_metadata`."""
    names, _base_points, _statuses = _read_block_header_metadata(data, anon_fallback)
    return names


_ANON_NAME_RE = re.compile(r"^\*[UD]\d+$")


def _read_anon_block_names_json(
    source: Path, source_sha256: str,
) -> tuple[dict[int, str], dict[int, str]]:
    """Resolve anonymous block names and DIMENSION display text via dwgread JSON.

    LibreDWG dynapi decodes anonymous BLOCK_HEADER names without the numeric
    suffix on this R2018 file.  The ``dwgread -O json`` side channel (see wiki
    libredwg-swig-utf-16-r2018-dwg) carries each bare BLOCK_HEADER plus a
    following companion entry holding the full numbered name; pairing is
    order-preserving on handle value (validated against the canonical AutoCAD
    INSERT census for the APD / As Plan Drawing corpus).  The same document
    also links each DIMENSION to its anonymous text block, whose MTEXT is the
    rendered display value (the rounded integer shown in CAD, not the raw
    ``act_measurement``).
    """
    fd, cache = tempfile.mkstemp(prefix="libredwg_blocks_", suffix=".json")
    os.close(fd)
    cache = Path(cache)
    if cache.stat().st_size == 0:
        try:
            proc = subprocess.run(
                ["dwgread", "-O", "json", str(source)],
                capture_output=True,
                timeout=600,
                check=False,
            )
        except Exception:
            cache.unlink(missing_ok=True)
            return {}, {}
        if proc.returncode != 0 or not proc.stdout:
            cache.unlink(missing_ok=True)
            return {}, {}
        cache.write_bytes(proc.stdout)
        cache.chmod(0o600)
    try:
        doc = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}

    bare: list[int] = []
    numbered: list[tuple[int, str]] = []
    dimension_blocks: dict[int, int] = {}
    block_texts: dict[int, str] = {}

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            entity = node.get("entity")
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
            if (
                isinstance(entity, str)
                and entity.startswith("DIMENSION")
                and isinstance(handle, list)
                and len(handle) >= 3
                and isinstance(handle[-1], int)
            ):
                block = node.get("block")
                if (
                    isinstance(block, list)
                    and len(block) >= 3
                    and isinstance(block[-1], int)
                ):
                    dimension_blocks[handle[-1]] = block[-1]
            if entity in {"MTEXT", "TEXT"} and isinstance(node.get("text"), str):
                owner = node.get("ownerhandle")
                if (
                    isinstance(owner, list)
                    and len(owner) >= 3
                    and isinstance(owner[-1], int)
                ):
                    block_texts.setdefault(owner[-1], node["text"])
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
    dimension_texts = {
        handle: block_texts[block]
        for handle, block in dimension_blocks.items()
        if block in block_texts
    }
    return mapping, dimension_texts


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


def _read_entity_layout_map_json(source: Path, source_sha256: str) -> dict[int, str]:
    """Map block_header handles to named layout tab names via dwgread JSON.

    LibreDWG dynapi truncates LAYOUT names to a single character on R2018
    files.  The dwgread JSON side channel carries the full ``layout_name``
    and the associated ``block_header`` handle.
    """
    fd, cache = tempfile.mkstemp(prefix="libredwg_layouts_", suffix=".json")
    os.close(fd)
    cache = Path(cache)
    if cache.stat().st_size == 0:
        try:
            proc = subprocess.run(
                ["dwgread", "-O", "json", str(source)],
                capture_output=True,
                timeout=600,
                check=False,
            )
        except Exception:
            cache.unlink(missing_ok=True)
            return {}
        if proc.returncode != 0 or not proc.stdout:
            cache.unlink(missing_ok=True)
            return {}
        cache.write_bytes(proc.stdout)
        cache.chmod(0o600)
    try:
        doc = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return {}

    # Phase 1: LAYOUT → block_header handle
    layout_bh: dict[int, str] = {}

    def _walk_layouts(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("object") == "LAYOUT":
                name = node.get("layout_name")
                bh = node.get("block_header")
                if (
                    isinstance(name, str)
                    and isinstance(bh, list)
                    and len(bh) >= 3
                    and isinstance(bh[-1], int)
                ):
                    layout_bh[bh[-1]] = name.strip()
            for value in node.values():
                _walk_layouts(value)
        elif isinstance(node, list):
            for value in node:
                _walk_layouts(value)

    _walk_layouts(doc)

    # Phase 2: BLOCK_HEADER.entities → layout name
    entity_layout: dict[int, str] = {}

    def _walk_block_entities(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("object") == "BLOCK_HEADER":
                entities = node.get("entities")
                bh = node.get("handle")
                if (
                    isinstance(entities, list)
                    and isinstance(bh, list)
                    and len(bh) >= 3
                    and isinstance(bh[-1], int)
                ):
                    bh_handle = bh[-1]
                    layout_name = layout_bh.get(bh_handle)
                    if layout_name:
                        for ent_ref in entities:
                            if (
                                isinstance(ent_ref, list)
                                and len(ent_ref) >= 3
                                and isinstance(ent_ref[-1], int)
                            ):
                                entity_layout[ent_ref[-1]] = layout_name
            for value in node.values():
                _walk_block_entities(value)
        elif isinstance(node, list):
            for value in node:
                _walk_block_entities(value)

    _walk_block_entities(doc)
    return entity_layout


def _resolve_layout(
    entity, block_headers: dict[int, str],
    layout_names: dict[int, str] | None = None,
    entity_handle: int | None = None,
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
        # The ctypes bridge returns ownerhandle=None for paper-space
        # entities.  Use the dwgread JSON entity→layout mapping keyed
        # by the entity's own handle instead.
        if layout_names is not None and entity_handle is not None:
            lname = layout_names.get(entity_handle)
            if lname and lname.casefold() not in ("model", "paper"):
                layout = lname
            else:
                layout = "Paper"
        else:
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
    block_base_points: dict[int, tuple[float, float, float]] | None = None,
    block_base_point_statuses: dict[int, str] | None = None,
    owner_attribs: dict[int, list] | None = None,
    dimension_display_texts: dict[int, str] | None = None,
) -> dict[str, Any] | None:
    """Build one v3-compatible record from a LibreDWG entity."""
    if dwg_type_name in _CONTROL_TYPE_NAMES:
        return None
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
    owned_attribute_texts: list[str] = []
    dimension_value: float | None = None
    dimension_text_override = ""
    native_length: float | None = None
    scale_x, scale_y, scale_z = 1.0, 1.0, 1.0
    rotation = 0.0
    owner_handle = ""
    curve_facts: dict[str, Any] | None = None
    transform_facts: dict[str, Any] = {}
    transform_facts_provenance: dict[str, str] = {}
    insertion_point: list[float] | None = None
    insertion_point_status = "not_applicable"
    block_base_point: list[float] | None = None
    block_base_point_status = "not_applicable"
    insert_scale: list[float] | None = None
    scale_status = "not_applicable"
    rotation_status = "not_applicable"
    insert_normal: list[float] | None = None
    normal_status = "not_applicable"
    insert_extrusion: list[float] | None = None
    extrusion_status = "not_applicable"
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
            x = float(ent.ins_pt.x)
            y = float(ent.ins_pt.y)
            points = [(x, y)]
            centroid = (x, y)
            try:
                insertion_point = [x, y, float(ent.ins_pt.z)]
                insertion_point_status = "available"
            except Exception:
                insertion_point_status = "unavailable"
                reasons.append("libredwg_insert_insertion_z_unavailable")

            try:
                insert_scale = [
                    float(ent.scale.x),
                    float(ent.scale.y),
                    float(ent.scale.z),
                ]
                scale_x, scale_y, scale_z = insert_scale
                scale_status = "available"
            except Exception:
                insert_scale = None
                scale_status = "unavailable"
                reasons.append("libredwg_insert_scale_unavailable")

            try:
                rotation = float(ent.rotation)
                rotation_status = "available"
            except Exception:
                rotation = 0.0
                rotation_status = "unavailable"
                reasons.append("libredwg_insert_rotation_unavailable")

            try:
                ext = ent.extrusion
                insert_extrusion = [float(ext.x), float(ext.y), float(ext.z)]
                insert_normal = list(insert_extrusion)
                extrusion_status = "available"
                normal_status = "available"
            except Exception:
                insert_extrusion = None
                insert_normal = None
                extrusion_status = "unavailable"
                normal_status = "unavailable"
                reasons.append("libredwg_insert_extrusion_unavailable")

            # Block name from block header reference (INSERT.block_name is empty).
            bh_handle: int | None = None
            bh_ref = ent.block_header
            if bh_ref and bh_ref.obj and bh_ref.obj.type == DWG_TYPE_BLOCK_HEADER:
                bh = bh_ref.obj.tio.object.tio.BLOCK_HEADER
                block_name = _entity_utf8_text(int(bh.this), "BLOCK_HEADER", "name")
                try:
                    bh_handle = int(bh_ref.obj.handle.value)
                except Exception:
                    bh_handle = None
                if anon_block_names and bh_handle is not None:
                    # Anonymous headers decode without the numeric suffix via
                    # dynapi; the dwgread JSON side channel carries the full
                    # numbered/effective name keyed by block header handle.
                    block_name = anon_block_names.get(bh_handle, block_name)
            if not block_name:
                reasons.append("libredwg_insert_block_name_unreadable")

            if bh_handle is not None:
                base = (block_base_points or {}).get(bh_handle)
                base_status = (block_base_point_statuses or {}).get(
                    bh_handle, "unavailable"
                )
                if base is not None and base_status == "available":
                    block_base_point = list(base)
                    block_base_point_status = "available"
                else:
                    block_base_point_status = base_status or "unavailable"
                    if block_base_point_status != "available":
                        reasons.append("libredwg_insert_block_base_unavailable")
            else:
                block_base_point_status = "unavailable"
                reasons.append("libredwg_insert_block_base_unavailable")

            transform_facts = {
                "schema_version": "cad2gis.reader-transform-facts.v1",
                "insertion_point": insertion_point,
                "insertion_point_status": insertion_point_status,
                "block_base_point": block_base_point,
                "block_base_point_status": block_base_point_status,
                "scale": insert_scale,
                "scale_status": scale_status,
                "rotation": rotation,
                "rotation_status": rotation_status,
                "normal": insert_normal,
                "normal_status": normal_status,
                "extrusion": insert_extrusion,
                "extrusion_status": extrusion_status,
            }
            transform_facts_provenance = {
                "insertion_point": "DWG_DIRECT:LibreDWG:INSERT.ins_pt",
                "block_base_point": "DWG_DIRECT:LibreDWG:BLOCK_HEADER.base_pt",
                "scale": "DWG_DIRECT:LibreDWG:INSERT.scale",
                "rotation": "DWG_DIRECT:LibreDWG:INSERT.rotation",
                "normal": "DWG_DIRECT:LibreDWG:INSERT.extrusion",
                "extrusion": "DWG_DIRECT:LibreDWG:INSERT.extrusion",
            }
            geometry_status = "available"
            if owner_attribs:
                attrs = owner_attribs.get(handle, [])
                for aobj in attrs:
                    try:
                        attr = aobj.tio.entity.tio.ATTRIB
                        attr_ptr = int(attr.this)
                        tag = str(_entity_utf8_text(
                            attr_ptr, "ATTRIB", "tag"
                        ) or "").strip()
                        value = str(_entity_utf8_text(
                            attr_ptr, "ATTRIB", "text_value"
                        ) or "").strip()
                        if value:
                            owned_attribute_texts.append(value)
                        if tag:
                            block_attributes[tag] = value
                    except Exception:
                        pass
                if not block_attributes and not owned_attribute_texts:
                    reasons.append("libredwg_block_attributes_unread")
            else:
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
            dimension_display_text = (
                str((dimension_display_texts or {}).get(handle, "") or "")
            )
            displayed_value = _dimension_display_value(dimension_display_text)
            dimension_value = (
                displayed_value
                if displayed_value is not None
                else dim["measurement"]
            )
            # The span dimension geometry is the xline1→xline2 witness line,
            # not def_pt→xline2.  Downstream exact segment matching requires
            # exactly those two endpoints; emitting three points made every
            # reviewed DIMENSION abstain as ``no_exact_span_dimension``.
            if dim["xline1"] and dim["xline2"]:
                points = [dim["xline1"], dim["xline2"]]
            else:
                points = [dim["def_pt"]]
                if dim["xline1"]:
                    points.append(dim["xline1"])
                if dim["xline2"]:
                    points.append(dim["xline2"])
            centroid = _centroid(points)
            if struct_ptr and not dimension_display_text:
                dimension_text_override = _entity_utf8_text(
                    struct_ptr, struct_name, "text_value"
                )
            elif dimension_display_text:
                dimension_text_override = dimension_display_text
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
        "owned_attribute_texts": owned_attribute_texts,
        "dynamic_block_properties": {},
        "dynamic_block_properties_status": "not_applicable",
        "dimension_measurement": dimension_value,
        "dimension_text_override": dimension_text_override,
        "native_length": native_length,
        "native_length_source": "libredwg_chord_length" if native_length is not None else "",
        "curve_facts": curve_facts or {},
        "curve_fingerprint": "",
        "insertion_point": insertion_point,
        "insertion_point_wcs": insertion_point,
        "insertion_point_status": insertion_point_status,
        "block_base_point": block_base_point,
        "block_base_point_status": block_base_point_status,
        "normal": insert_normal,
        "normal_status": normal_status,
        "extrusion": insert_extrusion,
        "extrusion_status": extrusion_status,
        "container_block_name": "",
        "nesting_context": "drawing_space",
        "block_definition_handle": "",
        "block_flags": None,
        "external_reference_path": "",
        "external_reference_status": "not_external",
        "geometry_status": geometry_status,
        "inventory_support_status": inventory_support_status,
        "transform_facts": transform_facts,
        "transform_facts_provenance": transform_facts_provenance,
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


_DWG_TYPE_NAME_MAP: dict[int, str] = {}
_DIM_STRUCT_NAMES: dict[int, str] = {}
_DIM_TYPE_NAMES = frozenset({"DIMENSION"})
_rebuild_type_maps()



def extract_dwg_records(source_path, *, layout_filter: str | None = None) -> DWGRecordInventory:
    if layout_filter is None:
        layout_filter = os.environ.get("CAD2GIS_LAYOUT") or None
    """Return a complete DWG record inventory using LibreDWG.

    The returned object is a list-like ``DWGRecordInventory`` with a
    ``.diagnostics`` dict containing extraction_backend, skipped_rows,
    inventory_complete, metadata_evidence, and unsupported_reason_counts.

    When ``layout_filter`` is set, only entities from the named layout and
    block-definition entities are included.
    """
    _require_libredwg()
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

    anon_block_names, dimension_display_texts = _read_anon_block_names_json(
        source, source_sha256,
    )
    entity_layout_map = _read_entity_layout_map_json(source, source_sha256)
    block_headers, block_base_points, block_base_point_statuses = (
        _read_block_header_metadata(data, anon_fallback=anon_block_names)
    )
    layer_styles = _read_layer_styles(data)
    fd, cursor_path = tempfile.mkstemp(prefix="libredwg_reader_", suffix=".json")
    os.close(fd)
    cursor_path = Path(cursor_path)

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
        "layout_names_resolved": len(entity_layout_map),
        "block_base_points_resolved": sum(
            1 for status in block_base_point_statuses.values()
            if status == "available"
        ),
    }

    # Pre-index ATTRIB entities by ownerhandle for INSERT attribute traversal.
    owner_attribs: dict[int, list] = {}
    if DWG_TYPE_ATTRIB is not None:
        from collections import defaultdict
        _oa = defaultdict(list)
        for i in range(data.num_objects):
            try:
                obj = Dwg_Object_Array_getitem(data.object, i)
            except Exception:
                continue
            if obj.supertype != DWG_SUPERTYPE_ENTITY:
                continue
            if obj.type != DWG_TYPE_ATTRIB:
                continue
            try:
                oh = obj.tio.entity.ownerhandle
                if oh is not None:
                    _oa[oh.absolute_ref].append(obj)
            except Exception:
                pass
        owner_attribs = dict(_oa)

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
                    entity, block_headers,
                    layout_names=entity_layout_map,
                    entity_handle=obj.handle.value,
                )

            # ── Layout filter ─────────────────────────────────────────
            if layout_filter is not None and layout_role != "control":
                if layout != layout_filter and not layout.startswith("BLOCKDEF:"):
                    continue

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
                block_base_points=block_base_points,
                block_base_point_statuses=block_base_point_statuses,
                owner_attribs=owner_attribs,
                dimension_display_texts=dimension_display_texts,
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

    # Spatial filtering is deferred to the semantic pipeline stage.
    # The reader is intentionally free of module-level re-entrancy risk.

    return DWGRecordInventory(records, diagnostics=diagnostics)
