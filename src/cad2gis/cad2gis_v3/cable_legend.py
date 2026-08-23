"""Reusable Indonesian FTTH DWG colour/legend knowledge.

The legend is extracted from the reviewed APD drawings (see
``tmp.screenshot/cabletype.png`` and ``tmp.screenshot/ptechtype.png``).  It
maps the drawing colours to cable core-count specifications and pole types.
The mapping is domain knowledge, not a per-project patch: all current
validation drawings are Indonesian FTTH APD sheets and share these symbols.
"""
from __future__ import annotations

import re
from typing import Any

CABLE_SPEC_COLORS = {
    "12": "#E06900",
    "24": "#00E000",
    "36": "#E000A0",
    "48": "#8000A0",
    "72": "#804000",
    "96": "#E00000",
    "144": "#A0E000",
    "288": "#E06900",
    "DROP DUCT": "#0000E0",
    "SLING WIRE": "#00E0E0",
}

# ACI colours observed in the DWG reader for the same legend swatches.
_ACI_TO_SPEC = {
    30: "12",   # orange
    3: "24",    # green
    6: "36",    # magenta
    200: "48",  # purple
    52: "72",   # brown
    1: "96",    # red
    2: "144",   # yellow-green
    4: "SLING WIRE",  # cyan
    5: "DROP DUCT",   # blue
}

_ROUTE_SPEC_RE = re.compile(r"(?i)FO\s*(?:CABLE\s*)?(\d+)\s*(?:CORE|C(?=[_\s-]|$))")


def cable_spec_name(source_layer: str, style: Any) -> str | None:
    """Return the reviewed legend name for a cable colour/layer combination."""
    layer = str(source_layer or "").strip()
    match = _ROUTE_SPEC_RE.search(layer)
    if match:
        return match.group(1)
    upper = layer.upper()
    if "SLING" in upper:
        return "SLING WIRE"
    if "DROP" in upper and "DUCT" in upper:
        return "DROP DUCT"
    if "PATCHCORD" in upper or upper == "ONT-MDU":
        return "PATCHCORD OUTDOOR"
    spec = _ACI_TO_SPEC.get(int(getattr(style, "aci_color", 256) or 256))
    if spec:
        return spec
    return None


def cable_spec_color(spec_name: str | None) -> str:
    """Canonical legend colour for a cable specification name."""
    if not spec_name:
        return ""
    return CABLE_SPEC_COLORS.get(spec_name, "")


# PTECH pole-legend colours observed in the reviewed drawings (the same
# five categories recur across the Indonesian FTTH APD sheets).
_PTECH_COLOR_TO_TYPE = {
    1: "EXISTING",       # red
    3: 'NP7 4"',          # green
    4: 'NP7 3"',          # cyan
    6: 'NP7 2.5"',        # magenta
    200: 'NP7 2.5"',       # purple (taipa)
    212: 'NP7 2.5"',       # purple (lamteh/tinggar FDT sheets)
}
_PTECH_TRUECOLOR_TO_TYPE = {
    "#FF0000": "EXISTING",
    "#7F0000": "EXISTING",
    "#00FF00": 'NP7 4"',
    "#00E000": 'NP7 4"',
    "#00FFFF": 'NP7 3"',
    "#FF00FF": 'NP7 2.5"',
    "#BF00FF": 'NP7 2.5"',
    "#CC00CC": 'NP7 2.5"',
}


# ── PTECH legend names ───────────────────────────────────────────────────────
def ptech_type_name(feature: Any) -> str:
    """Return the reviewed pole-legend category for a PTECH feature."""
    layer = str(getattr(feature, "source_layer", "") or "").upper()
    if "9M" in layer or "9 M" in layer:
        return 'NP9 4"'
    if "4 INCH" in layer or "4INCH" in layer or "(4 INCHES)" in layer:
        return 'NP7 4"'
    if " 4'" in layer or "-4" in layer or layer.endswith(" 4"):
        return 'NP7 4"'
    if "3 INCH" in layer or "3INCH" in layer or "(3 INCHES)" in layer:
        return 'NP7 3"'
    if "-3" in layer or layer.endswith(" 3"):
        return 'NP7 3"'
    if "2.5" in layer or "2'5" in layer or "2.5INCH" in layer:
        return 'NP7 2.5"'
    if "EXT" in layer or "EXISTING" in layer or "BASIC MAP" in layer:
        return "EXISTING"
    # Some FDT sheets label the pole layer without a size (e.g.
    # ``NEW POLE FDT 1``).  The reviewed drawings keep one colour per pole
    # type, so the CAD colour is the documented fallback for the legend.
    style = getattr(feature, "style", None)
    aci = int(getattr(style, "aci_color", 256) or 256)
    true = str(getattr(style, "true_color", "") or "").strip().upper()
    by_color = _PTECH_TRUECOLOR_TO_TYPE.get(true)
    if by_color is None:
        by_color = _PTECH_COLOR_TO_TYPE.get(aci)
    return by_color or "EXISTING"


def cable_spec_from_style(aci_color: int | None, true_color: str = "") -> str | None:
    """Map a render style to the cable legend specification name."""
    true = str(true_color or "").strip().upper().lstrip("#")
    # ezdxf returns indexed ACI colours as their nearest truecolour, which
    # is frequently one notch brighter than the legend swatch (e.g. ACI 3
    # reads back as #00FF00 rather than #00E000).  Accept both families.
    true_aliases = {
        "#00FF00": "24", "#00E000": "24",
        "#FF00FF": "36", "#E000A0": "36",
        "#BF00FF": "48", "#720099": "48", "#6600CC": "48", "#8000A0": "48",
        "#FF0000": "96", "#E00000": "96",
        "#00FFFF": "SLING WIRE", "#00E0E0": "SLING WIRE",
        "#0000FF": "DROP DUCT", "#0000E0": "DROP DUCT",
    }
    for spec, color in CABLE_SPEC_COLORS.items():
        if true and true == color.lstrip("#"):
            return spec
    alias = true_aliases.get(true)
    if alias:
        return alias
    return _ACI_TO_SPEC.get(int(aci_color or 256))
