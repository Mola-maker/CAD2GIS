"""Shared CAD/DWG utilities: geometry, colour, generic string and JSON helpers.

Layer-1/Layer-2 helpers reused by the LibreDWG cross-platform reader
(``cad2gis.reader.dwg_extractor``) and the legacy AutoCAD reader
(``cad2gis.reader.autocad``).  Zero FTTH/domaine symbols — these helpers
operate on plain ``(x, y)`` point lists, the AutoCAD ACI palette, and
generic C-string / JSON-dump concerns.  Project-specific rules
(tolerances, code_prefix, label_families, etc.) live in
``baselines/apd_hutabohu/config/project_rules.json`` and stay out of this
module.

Provenance:
- ``_cstr`` and ``_flush_cursor`` are generic byte/IO helpers used by both
  the LibreDWG ctypes bridge and the AutoCAD adapter.
- ``_hsv_bytes`` / ``_generate_aci_table`` / ``aci_to_rgb`` / ``ACI_TO_RGB``
  / ``DEFAULT_COLOR_RGB`` are the standard AutoCAD 255-colour palette
  generator, ported from main ``schema_config.py`` and used by both
  readers for ACI → ``#RRGGBB`` conversion.
- ``_chord_length`` and ``_centroid`` are plain ``(x, y)``-list geometry
  helpers used by curve_facts construction in both readers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path


# ── Generic C-string decoder ───────────────────────────────────────────────
def _cstr(raw):
    """Decode a C string with UTF-8 → latin-1 → hex fallback."""
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except UnicodeDecodeError:
            return raw.hex()


# ── AutoCAD ACI palette (HSV → RGB; 255-colour standard) ──────────────────
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


# ── Pure (x, y)-list geometry helpers ─────────────────────────────────────
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


# ── Generic JSON cursor dump ───────────────────────────────────────────────
def _flush_cursor(diagnostics: dict, path: Path) -> None:
    """Best-effort JSON dump of a diagnostics dict (reader cursor pattern)."""
    try:
        path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    except Exception:
        pass
