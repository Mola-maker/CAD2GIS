"""Application-local fonts and glyph evidence for headless QGIS snapshots."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def _qt():
    # Keep ordinary Python/MCP imports independent of the native QGIS runtime.
    from qgis.PyQt.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetrics

    return QFont, QFontDatabase, QFontInfo, QFontMetrics


def _fallback_paths():
    if os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        return [fonts / "arial.ttf", fonts / "segoeui.ttf"]
    return [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]


def ensure_runtime_fonts(font=None):
    """Load only into this application; reject an empty/unusable Qt font database."""
    QFont, database, info, _ = _qt()
    loaded = []

    def load(path):
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError(f"QGIS font file does not exist: {path}")
        identifier = database.addApplicationFont(str(path))
        if identifier < 0:
            raise ValueError(f"Cannot load requested QGIS font: {path}")
        families = list(database.applicationFontFamilies(identifier))
        if not families:
            raise ValueError(f"QGIS font has no usable families: {path}")
        loaded.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "families": families})

    requested = font or os.environ.get("CAD2GIS_QGIS_FONT")
    if requested:
        load(requested)
    if not database.families() or not info(QFont("Arial", 10)).family():
        for candidate in _fallback_paths():
            if candidate.is_file():
                load(candidate)
                if database.families() and info(QFont("Arial", 10)).family():
                    break
    resolved = info(QFont("Arial", 10)).family()
    if not database.families() or not resolved:
        raise ValueError("QGIS has no usable runtime font; set CAD2GIS_QGIS_FONT or --font to an existing font file")
    return {"available_family_count": len(database.families()), "requested_family": "Arial",
            "resolved_family": resolved, "loaded_application_fonts": loaded}


def verify_rendered_text(labels, annotations, *, expected_label_layers=()):
    """Check glyph support of the actual placed labels, including dynamic fonts.

    This is evidence for text rendered in this snapshot, not a claim that every
    feature label was placed or that label collision/layout has been reviewed.
    """
    _, _, info, metrics = _qt()
    groups = {}
    rendered_labels = 0
    for label in labels:
        if label.isDiagram or label.isUnplaced:
            continue
        rendered_labels += 1
        _collect_text(groups, label.labelFont, label.labelText)
    for font, text in annotations:
        _collect_text(groups, font, text)
    if expected_label_layers and not rendered_labels:
        raise ValueError("Enabled QGIS label layers produced no placed labels; glyph verification is incomplete: "
                         + ", ".join(expected_label_layers))
    rows = []
    for font, characters in groups.values():
        resolved = info(font).family()
        if not resolved:
            raise ValueError(f"Rendered QGIS text has no resolved font: {font.family()}")
        font_metrics = metrics(font)
        missing = sorted(ord(character) for character in characters
                         if not font_metrics.inFontUcs4(ord(character)))
        if missing:
            points = ", ".join(f"U+{point:04X}" for point in missing)
            raise ValueError(f"QGIS font {font.family()!r} (resolved {resolved!r}) lacks rendered glyphs: {points}")
        rows.append({"requested_family": font.family(), "resolved_family": resolved, "font_description": font.toString(),
                     "unique_characters_checked": len(characters), "missing_codepoints": []})
    return {"scope": "rendered_labels_and_snapshot_annotations", "rendered_labels_checked": rendered_labels,
            "enabled_label_layers": list(expected_label_layers),
            "annotations_checked": len(annotations), "fonts": rows, "all_rendered_glyphs_available": True}


def _collect_text(groups, font, text):
    entry = groups.setdefault(font.toString(), (font, set()))
    entry[1].update(character for character in text if character.isprintable() and not character.isspace())
