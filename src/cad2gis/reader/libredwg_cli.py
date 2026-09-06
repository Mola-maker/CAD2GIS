"""LibreDWG CLI reader using a transient DXF and :mod:`ezdxf`.

LibreDWG's official command-line releases are substantially more portable
than its SWIG Python extension.  This adapter preserves the existing reader
record contract while keeping AutoCAD out of the default path:

``DWG --dwg2dxf--> temporary DXF --ezdxf--> immutable source records``.

The intermediate file never becomes a delivery artifact and is removed when
the extraction call ends.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..native_runtime import discover_libredwg_cli
from ..cad2gis_v3.geodata import (
    GEODATA_REGISTRATION_SCHEMA,
    normalize_geodata_registration,
)
from .contracts import ReaderCapability, ReaderUnavailableError

_RAW_PROPERTIES_SCHEMA = "cad2gis-raw-properties-v1"
_CURVE_FACTS_SCHEMA = "cad2gis-curve-facts-v1"
_MAX_GEODATA_JSON_BYTES = 256 * 1024 * 1024
_SURROGATE_PATTERN = re.compile("[\ud800-\udfff]")
_TEXT_PRESERVATION_SCHEMA = "cad2gis.reader-text-preservation.v1"

_OBJECT_NAMES = {
    "LINE": "ACDBLINE",
    "LWPOLYLINE": "ACDBLWPOLYLINE",
    "POLYLINE": "ACDBPOLYLINE",
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
    "DIMENSION": "ACDBDIMENSION",
    "HATCH": "ACDBHATCH",
}


class DWGRecordInventory(list):
    """Flat record inventory with reader-protocol diagnostics attached."""

    def __init__(self, values=(), *, diagnostics=None):
        super().__init__(values)
        self.diagnostics = dict(diagnostics or {})


def _escaped_string_map(records: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Allocate collision-free display strings across the whole reader inventory."""
    ordinary: set[str] = set()
    undecodable: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, str):
            (undecodable if _SURROGATE_PATTERN.search(value) else ordinary).add(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                collect(key)
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    for record in records:
        collect(record)
    def normalized_keys(value: str) -> set[str]:
        # Block/port consumers normalize identifiers this way before lookup.
        # Exact-string uniqueness alone can still merge distinct definitions.
        stripped = value.strip()
        return {stripped.upper(), stripped.casefold()}

    occupied = {key for value in ordinary for key in normalized_keys(value)}
    replacements = {}
    for value in sorted(undecodable, key=lambda item: (len(item), item)):
        if value in replacements:
            continue
        escaped = _SURROGATE_PATTERN.sub(lambda match: f"\\u{ord(match[0]):04x}", value)
        candidate = escaped
        suffix = 0
        # BLOCKDEF:<name> is a structured cross-record reference used by the
        # scene/instance compiler. Its escaped name must match INSERT.name.
        block_layout = f"BLOCKDEF:{value}"
        has_layout_alias = block_layout in undecodable
        while (normalized_keys(candidate) & occupied
               or (has_layout_alias and normalized_keys(f"BLOCKDEF:{candidate}") & occupied)):
            suffix += 1
            candidate = f"{escaped}~cad2gis-string-{suffix}"
        replacements[value] = candidate
        ordinary.add(candidate)
        occupied.update(normalized_keys(candidate))
        if has_layout_alias:
            replacements[block_layout] = f"BLOCKDEF:{candidate}"
            ordinary.add(f"BLOCKDEF:{candidate}")
            occupied.update(normalized_keys(f"BLOCKDEF:{candidate}"))
    return replacements


def _preserve_undecodable_strings(
    record: dict[str, Any], document: Any, replacements: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Make reader strings UTF-8 safe without interpreting undecodable units.

    ezdxf can retain undecodable DXF bytes as lone surrogate code units. They
    cannot enter SQLite/GDAL/UTF-8 JSON directly. Escape only those units for
    display, and retain exact reader-string recovery evidence separately. The
    hex value encodes the *reader string*, not original DWG bytes; surrogatepass
    also preserves explicit surrogate pairs that JSON decoders may combine.
    """
    changes: list[dict[str, Any]] = []
    key_collisions = 0
    replacements = _escaped_string_map([record]) if replacements is None else replacements
    if not replacements:
        return record

    def escaped(value: str) -> str:
        return replacements[value]

    def preserve(value: str, path: list[str | int], kind: str) -> None:
        changes.append({
            "path": path, "kind": kind,
            "original_json": json.dumps(value, ensure_ascii=True),
            "original_utf8_surrogatepass_hex": value.encode("utf-8", errors="surrogatepass").hex(),
            "surrogate_code_units": [
                {"index": index, "code_unit": ord(char)}
                for index, char in enumerate(value) if 0xD800 <= ord(char) <= 0xDFFF
            ],
        })

    def visit(value: Any, path: list[str | int]) -> Any:
        nonlocal key_collisions
        if isinstance(value, str):
            if not _SURROGATE_PATTERN.search(value):
                return value
            preserve(value, path, "value")
            return escaped(value)
        if isinstance(value, dict):
            result = {}
            # Reserve ordinary keys first so a literal "\\udcb0" key cannot
            # be overwritten by the escaped spelling of a surrogate key.
            reserved = set(value)
            for key, item in value.items():
                safe_key = key
                if isinstance(key, str) and _SURROGATE_PATTERN.search(key):
                    safe_key = escaped(key)
                    suffix = 0
                    while safe_key in reserved:
                        suffix += 1
                        safe_key = f"{escaped(key)}~cad2gis-key-{suffix}"
                    plain_escape = _SURROGATE_PATTERN.sub(lambda match: f"\\u{ord(match[0]):04x}", key)
                    key_collisions += int(safe_key != plain_escape)
                    reserved.add(safe_key)
                    preserve(key, [*path, safe_key], "key")
                result[safe_key] = visit(item, [*path, safe_key])
            return result
        if isinstance(value, (list, tuple)):
            values = [visit(item, [*path, index]) for index, item in enumerate(value)]
            return tuple(values) if isinstance(value, tuple) else values
        return value

    safe_record = visit(record, [])
    if not changes:
        return record
    raw = safe_record["raw_properties"]
    if "text_encoding_preservation" in raw:
        raise ValueError("Reader text preservation evidence would overwrite existing facts")
    raw["text_encoding_preservation"] = {
        "schema_version": _TEXT_PRESERVATION_SCHEMA,
        "status": "escaped_undecodable_code_units",
        "display_encoding": "literal_backslash_u_four_hex_digits; collision suffix when needed; original_json is authoritative",
        "recovery_encoding": "bytes.fromhex(original_utf8_surrogatepass_hex).decode('utf-8', 'surrogatepass')",
        "authority": "LibreDWG transient DXF / ezdxf reader strings; no codepage inferred",
        "encoding_hints_json": json.dumps({
            "dxf_version": str(getattr(document, "dxfversion", "")),
            "declared_dxf_codepage": str(document.header.get("$DWGCODEPAGE", "")),
            "ezdxf_encoding": str(getattr(document, "encoding", "")),
        }, ensure_ascii=True, sort_keys=True),
        "affected_fields": len(changes),
        "surrogate_code_units": sum(len(change["surrogate_code_units"]) for change in changes),
        "key_collisions": key_collisions,
        "fields": changes,
    }
    return safe_record


def libredwg_cli_capability() -> ReaderCapability:
    executable, source = discover_libredwg_cli()
    try:
        ezdxf_available = importlib.util.find_spec("ezdxf") is not None
    except (ImportError, AttributeError, ValueError):
        ezdxf_available = False
    if executable is None:
        return ReaderCapability(
            backend="libredwg-cli",
            available=False,
            detail=f"LibreDWG dwg2dxf is unavailable ({source}).",
            remediation="Run `cad2gis runtime install` or install dwg2dxf on PATH.",
        )
    if not ezdxf_available:
        return ReaderCapability(
            backend="libredwg-cli",
            available=False,
            detail=f"LibreDWG CLI is available at {executable}, but ezdxf is missing.",
            remediation="Install the CAD2GIS agent extra: pip install 'cad2gis[agent]'.",
        )
    return ReaderCapability(
        backend="libredwg-cli",
        available=True,
        detail=f"LibreDWG CLI is available at {executable} ({source}); ezdxf is available.",
        remediation="No remediation required.",
    )


def _xy(value: Any) -> tuple[float, float]:
    return float(value[0]), float(value[1])


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _polyline_length(
    points: list[tuple[float, float]], bulges: list[float], *, closed: bool
) -> float | None:
    if len(points) < 2:
        return None
    total = 0.0
    segment_count = len(points) if closed else len(points) - 1
    for index in range(segment_count):
        start = points[index]
        end = points[(index + 1) % len(points)]
        chord = math.hypot(end[0] - start[0], end[1] - start[1])
        bulge = bulges[index] if index < len(bulges) else 0.0
        if chord and abs(bulge) > 1e-15:
            angle = 4.0 * math.atan(abs(bulge))
            radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
            total += radius * angle
        else:
            total += chord
    return total


def _curve_facts(
    primitive_type: str,
    points: list[tuple[float, float]],
    *,
    closed: bool,
    bulges: list[float] | None,
    native_length: float | None,
    elevation: float | None = None,
    primitive_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_bulges = list(bulges or [0.0] * len(points))
    normalized_bulges = (normalized_bulges + [0.0] * len(points))[: len(points)]
    return {
        "schema_version": _CURVE_FACTS_SCHEMA,
        "coordinate_system": "WCS",
        "primitive_type": primitive_type,
        "vertices_wcs": [[x, y, elevation or 0.0] for x, y in points],
        "bulges": normalized_bulges,
        "elevation": elevation,
        "normal": [0.0, 0.0, 1.0],
        "extrusion": None,
        "closed": closed,
        "primitive_parameters": dict(primitive_parameters or {}),
        "native_length": native_length,
        "native_length_source": "libredwg_cli_dxf",
    }


def _true_color_hex(value: Any) -> str:
    """DXF 420 contains 24-bit RGB; discard signed/packed reader high bits."""
    return f"#{int(value) & 0xFFFFFF:06X}" if value is not None else ""


def _layer_style(document: Any, name: str) -> dict[str, Any]:
    try:
        layer = document.layers.get(name)
    except Exception:
        return {"aci": 7, "truecolor": "", "linetype": "Continuous", "lineweight": -1}
    true_color = getattr(layer.dxf, "true_color", None)
    return {
        "aci": int(getattr(layer.dxf, "color", 7) or 7),
        "truecolor": _true_color_hex(true_color),
        "linetype": str(getattr(layer.dxf, "linetype", "Continuous") or "Continuous"),
        "lineweight": int(getattr(layer.dxf, "lineweight", -1) or -1),
    }


def _block_base_point(document: Any, block_name: str) -> list[float] | None:
    """Return the referenced DXF BLOCK base point without inventing a default."""

    if not block_name:
        return None
    try:
        value = document.blocks.get(block_name).base_point
        return [float(value[0]), float(value[1]), float(value[2])]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


def _entity_geometry(document: Any, entity: Any, type_name: str) -> dict[str, Any]:
    points: list[tuple[float, float]] = []
    bulges: list[float] = []
    closed = False
    native_length: float | None = None
    curve_facts: dict[str, Any] = {}
    text = ""
    block_name = ""
    block_attributes: dict[str, str] = {}
    dimension_value: float | None = None
    dimension_text_override = ""
    scale = (1.0, 1.0, 1.0)
    rotation = 0.0
    reasons: list[str] = []
    transform_facts: dict[str, Any] = {}
    transform_provenance: dict[str, str] = {}

    try:
        if type_name == "LINE":
            points = [_xy(entity.dxf.start), _xy(entity.dxf.end)]
            native_length = math.dist(points[0], points[1])
            curve_facts = _curve_facts(
                "line", points, closed=False, bulges=None, native_length=native_length
            )
        elif type_name == "LWPOLYLINE":
            values = list(entity.get_points("xyb"))
            points = [(float(value[0]), float(value[1])) for value in values]
            bulges = [float(value[2]) for value in values]
            closed = bool(entity.closed)
            native_length = _polyline_length(points, bulges, closed=closed)
            elevation = float(getattr(entity.dxf, "elevation", 0.0) or 0.0)
            curve_facts = _curve_facts(
                "lwpolyline",
                points,
                closed=closed,
                bulges=bulges,
                native_length=native_length,
                elevation=elevation,
            )
        elif type_name == "POLYLINE":
            points = [_xy(vertex.dxf.location) for vertex in entity.vertices]
            closed = bool(entity.is_closed)
            bulges = [float(getattr(vertex.dxf, "bulge", 0.0) or 0.0) for vertex in entity.vertices]
            native_length = _polyline_length(points, bulges, closed=closed)
            curve_facts = _curve_facts(
                "polyline",
                points,
                closed=closed,
                bulges=bulges,
                native_length=native_length,
            )
        elif type_name in {"TEXT", "ATTRIB", "ATTDEF"}:
            points = [_xy(entity.dxf.insert)]
            text = str(getattr(entity.dxf, "text", "") or "")
            rotation = math.radians(float(getattr(entity.dxf, "rotation", 0.0) or 0.0))
        elif type_name == "MTEXT":
            points = [_xy(entity.dxf.insert)]
            text = str(getattr(entity, "text", "") or "")
            rotation = math.radians(float(getattr(entity.dxf, "rotation", 0.0) or 0.0))
        elif type_name == "INSERT":
            insertion = entity.dxf.insert
            points = [_xy(insertion)]
            block_name = str(entity.dxf.name or "")
            block_attributes = {
                str(attribute.dxf.tag).upper(): str(attribute.dxf.text or "")
                for attribute in entity.attribs
                if str(attribute.dxf.tag or "").strip()
            }
            scale = (
                float(getattr(entity.dxf, "xscale", 1.0) or 1.0),
                float(getattr(entity.dxf, "yscale", 1.0) or 1.0),
                float(getattr(entity.dxf, "zscale", 1.0) or 1.0),
            )
            rotation = math.radians(float(getattr(entity.dxf, "rotation", 0.0) or 0.0))
            insertion_3d = [float(insertion[0]), float(insertion[1]), float(insertion[2])]
            extrusion = getattr(entity.dxf, "extrusion", (0.0, 0.0, 1.0))
            normal = [float(extrusion[0]), float(extrusion[1]), float(extrusion[2])]
            block_base_point = _block_base_point(document, block_name)
            transform_facts = {
                "schema_version": "cad2gis.reader-transform-facts.v1",
                "insertion_point": insertion_3d,
                "insertion_point_status": "available",
                "block_base_point": block_base_point,
                "block_base_point_status": (
                    "available" if block_base_point is not None else "unavailable"
                ),
                "scale": list(scale),
                "scale_status": "available",
                "rotation": rotation,
                "rotation_status": "available",
                "normal": normal,
                "normal_status": "available",
                "extrusion": normal,
                "extrusion_status": "available",
            }
            transform_provenance = {
                "insertion_point": "DWG_DIRECT:LibreDWG-CLI:DXF:INSERT.insert",
                "block_base_point": "DWG_DIRECT:LibreDWG-CLI:DXF:BLOCK.base_point",
                "scale": "DWG_DIRECT:LibreDWG-CLI:DXF:INSERT.scale",
                "rotation": "DWG_DIRECT:LibreDWG-CLI:DXF:INSERT.rotation",
                "normal": "DWG_DIRECT:LibreDWG-CLI:DXF:INSERT.extrusion",
                "extrusion": "DWG_DIRECT:LibreDWG-CLI:DXF:INSERT.extrusion",
            }
            if block_base_point is None:
                reasons.append("libredwg_cli_block_base_unavailable")
        elif type_name == "LEADER":
            points = [_xy(value) for value in entity.vertices]
            native_length = _polyline_length(points, [], closed=False)
        elif type_name in {"MLEADER", "MULTILEADER"}:
            context = entity.context
            for leader in context.leaders:
                for line in leader.lines:
                    points.extend(_xy(value) for value in line.vertices)
                last_point = getattr(leader, "last_leader_point", None)
                if last_point is not None:
                    points.append(_xy(last_point))
            if not points:
                points.append(_xy(context.base_point))
            native_length = _polyline_length(points, [], closed=False)
            mtext = getattr(context, "mtext", None)
            if mtext is not None:
                text = str(getattr(mtext, "default_content", "") or "")
        elif type_name == "DIMENSION":
            first = getattr(entity.dxf, "defpoint2", None)
            second = getattr(entity.dxf, "defpoint3", None)
            if first is not None and second is not None:
                points = [_xy(first), _xy(second)]
            else:
                points = [_xy(entity.dxf.defpoint)]
            try:
                actual_measurement = getattr(entity.dxf, "actual_measurement", None)
                if actual_measurement is None:
                    dimension_value = float(entity.get_measurement())
                else:
                    dimension_value = float(actual_measurement)
                if not math.isfinite(dimension_value) or dimension_value < 0.0:
                    raise ValueError("invalid DIMENSION measurement")
            except Exception:
                dimension_value = None
                reasons.append("libredwg_cli_dimension_measurement_unavailable")
            dimension_text_override = str(getattr(entity.dxf, "text", "") or "")
        elif type_name == "POINT":
            points = [_xy(entity.dxf.location)]
        elif type_name == "CIRCLE":
            points = [_xy(entity.dxf.center)]
            radius = float(entity.dxf.radius)
            native_length = 2.0 * math.pi * radius
            curve_facts = _curve_facts(
                "circle",
                points,
                closed=True,
                bulges=[0.0],
                native_length=native_length,
                primitive_parameters={"center": list(points[0]), "radius": radius},
            )
        elif type_name == "ARC":
            points = [_xy(entity.start_point), _xy(entity.end_point)]
            radius = float(entity.dxf.radius)
            span = math.radians((float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360.0)
            native_length = radius * span
            curve_facts = _curve_facts(
                "arc",
                points,
                closed=False,
                bulges=[math.tan(span / 4.0), 0.0],
                native_length=native_length,
                primitive_parameters={
                    "center": list(_xy(entity.dxf.center)),
                    "radius": radius,
                    "start_angle_degrees": float(entity.dxf.start_angle),
                    "end_angle_degrees": float(entity.dxf.end_angle),
                },
            )
        elif type_name == "SPLINE":
            values = list(entity.fit_points) or list(entity.control_points)
            points = [_xy(value) for value in values]
            reasons.append("libredwg_cli_spline_length_unavailable")
        elif type_name == "ELLIPSE":
            points = [_xy(entity.dxf.center)]
            reasons.append("libredwg_cli_ellipse_length_unavailable")
        else:
            reasons.append("libredwg_cli_inventory_only_type")
    except Exception as exc:
        reasons.append(f"libredwg_cli_geometry_error[{type(exc).__name__}]")

    return {
        "points": points,
        "centroid": _centroid(points),
        "closed": closed,
        "text": text,
        "block_name": block_name,
        "block_attributes": block_attributes,
        "dimension_value": dimension_value,
        "dimension_text_override": dimension_text_override,
        "native_length": native_length,
        "curve_facts": curve_facts,
        "scale": scale,
        "rotation": rotation,
        "transform_facts": transform_facts,
        "transform_facts_provenance": transform_provenance,
        "reasons": sorted(set(reasons)),
    }


def _record(
    *,
    document: Any,
    entity: Any,
    source: Path,
    source_sha256: str,
    layout: str,
    layout_role: str,
    cad_role: str,
) -> dict[str, Any]:
    type_name = str(entity.dxftype()).upper()
    if type_name.startswith("DIMENSION"):
        type_name = "DIMENSION"
    handle = str(getattr(entity.dxf, "handle", "") or "")
    layer = str(getattr(entity.dxf, "layer", "0") or "0")
    geometry = _entity_geometry(document, entity, type_name)
    reasons = list(geometry["reasons"])
    layer_style = _layer_style(document, layer)
    entity_aci = int(getattr(entity.dxf, "color", 256) or 256)
    entity_true_color_value = getattr(entity.dxf, "true_color", None)
    entity_true_color = _true_color_hex(entity_true_color_value)
    effective_aci = layer_style["aci"] if entity_aci in {0, 256} else entity_aci
    effective_true_color = entity_true_color or str(layer_style["truecolor"])
    entity_linetype = str(getattr(entity.dxf, "linetype", "ByLayer") or "ByLayer")
    entity_lineweight = int(getattr(entity.dxf, "lineweight", -1) or -1)
    inventory_status = "full" if geometry["points"] else "inventory_only"
    if inventory_status == "inventory_only" and "geometry_unavailable" not in reasons:
        reasons.append("geometry_unavailable")
    reasons = sorted(set(reasons))
    entity_key = hashlib.sha256(
        f"{source_sha256}|{handle}|{layout}".encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    owner_handle = str(getattr(entity.dxf, "owner", "") or "")
    scale_x, scale_y, scale_z = geometry["scale"]
    raw_properties = {
        "schema_version": _RAW_PROPERTIES_SCHEMA,
        "extraction_backend": "libredwg_cli_dxf",
        "reader_backend_status": "supported" if inventory_status == "full" else "unsupported",
        "object_name": _OBJECT_NAMES.get(type_name, f"ACDB{type_name}"),
        "dwg_type_name": type_name,
        "handle": handle,
        "owner_handle": owner_handle,
        "layout": layout,
        "layer": layer,
        "block_name": geometry["block_name"],
        "block_effective_name": geometry["block_name"],
        "block_reference_name": geometry["block_name"],
        "text": geometry["text"],
        "raw_text": geometry["text"],
        "text_source": "entity_text" if type_name in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"} else "",
        "attribute_tags": sorted(geometry["block_attributes"]),
        "block_attributes": geometry["block_attributes"],
        "owned_attribute_texts": list(geometry["block_attributes"].values()),
        "dynamic_block_properties": {},
        "dynamic_block_properties_status": "unavailable" if type_name == "INSERT" else "not_applicable",
        "dimension_measurement": geometry["dimension_value"],
        "dimension_text_override": geometry["dimension_text_override"],
        "native_length": geometry["native_length"],
        "native_length_source": "libredwg_cli_dxf" if geometry["native_length"] is not None else "",
        "curve_facts": geometry["curve_facts"],
        "curve_fingerprint": "",
        "geometry_status": "available" if geometry["points"] else "unavailable",
        "inventory_support_status": inventory_status,
        "transform_facts": geometry["transform_facts"],
        "transform_facts_provenance": geometry["transform_facts_provenance"],
        "scale_x": scale_x,
        "scale_y": scale_y,
        "scale_z": scale_z,
        "rotation": geometry["rotation"],
        "entity_rotation": geometry["rotation"],
        "aci_color": effective_aci,
        "true_color": effective_true_color,
        "linetype": entity_linetype,
        "lineweight": entity_lineweight,
        "entity_aci_color": entity_aci,
        "layer_aci_color": layer_style["aci"],
        "entity_true_color": entity_true_color,
        "layer_true_color": layer_style["truecolor"],
        "entity_linetype": entity_linetype,
        "layer_linetype": layer_style["linetype"],
        "entity_lineweight": entity_lineweight,
        "layer_lineweight": layer_style["lineweight"],
        "unsupported_reason": ";".join(reasons),
        "unsupported_reasons": reasons,
    }
    return {
        "entity_key": entity_key,
        "source_sha256": source_sha256,
        "source_file": str(source),
        "handle": handle,
        "layout": layout,
        "layout_role": layout_role,
        "cad_role": cad_role,
        "layer": layer,
        "object_name": raw_properties["object_name"],
        "dwg_type_name": type_name,
        "points": geometry["points"],
        "centroid": geometry["centroid"],
        "closed": geometry["closed"],
        "text": geometry["text"],
        "block_name": geometry["block_name"],
        "block_attributes": geometry["block_attributes"],
        "dimension_value": geometry["dimension_value"],
        "scale_x": scale_x,
        "scale_y": scale_y,
        "scale_z": scale_z,
        "owner_handle": owner_handle,
        "dimension_text_override": geometry["dimension_text_override"],
        "native_length": geometry["native_length"],
        "raw_properties": raw_properties,
        "curve_facts": geometry["curve_facts"],
        "curve_fingerprint": "",
        "aci_color": effective_aci,
        "true_color": effective_true_color,
        "linetype": entity_linetype,
        "lineweight": entity_lineweight,
        "rotation": geometry["rotation"],
        "entity_aci_color": entity_aci,
        "layer_aci_color": layer_style["aci"],
        "entity_true_color": entity_true_color,
        "layer_true_color": layer_style["truecolor"],
        "entity_linetype": entity_linetype,
        "layer_linetype": layer_style["linetype"],
        "entity_lineweight": entity_lineweight,
        "layer_lineweight": layer_style["lineweight"],
        "inventory_support_status": inventory_status,
    }


def _contexts(document: Any) -> Iterable[tuple[Any, str, str, str]]:
    yield document.modelspace(), "Model", "model", "model"
    paper_layouts = sorted(
        (layout for layout in document.layouts if layout.name.casefold() != "model"),
        key=lambda layout: layout.name.casefold(),
    )
    for layout in paper_layouts:
        yield layout, layout.name, "layout", "layout"
    blocks = sorted(document.blocks, key=lambda block: block.name.casefold())
    for block in blocks:
        if block.name.casefold().startswith(("*model_space", "*paper_space")):
            continue
        yield block, f"BLOCKDEF:{block.name}", "block_definition", "block_definition"


def _geodata_reader(executable: Path) -> Path | None:
    # Keep the companion lookup tied to the selected dwg2dxf distribution,
    # not only to the host OS.  This also supports Windows tool bundles that
    # are mounted or exercised from a non-Windows host (for example in CI or
    # through Wine) without weakening the sibling-only trust boundary.
    names = ["dwgread.exe"] if executable.suffix.casefold() == ".exe" else []
    names.append("dwgread.exe" if os.name == "nt" else "dwgread")
    names.append("dwgread" if names[-1] == "dwgread.exe" else "dwgread.exe")
    for name in dict.fromkeys(names):
        candidate = executable.with_name(name)
        if candidate.is_file():
            return candidate
    return None


def _read_geodata_registration(
    executable: Path, source: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    reader = _geodata_reader(executable)
    diagnostics: dict[str, Any] = {
        "status": "unavailable",
        "reader": None if reader is None else str(reader),
    }
    if reader is None:
        diagnostics["detail"] = "dwgread is not installed beside dwg2dxf"
        return None, diagnostics
    try:
        diagnostics["reader_sha256"] = hashlib.sha256(reader.read_bytes()).hexdigest()
        process = subprocess.run(
            [str(reader), "-O", "minJSON", str(source)],
            capture_output=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        diagnostics["detail"] = f"dwgread GEODATA probe failed ({type(exc).__name__})"
        return None, diagnostics
    diagnostics["exit_code"] = int(process.returncode)
    diagnostics["stdout_bytes"] = len(process.stdout)
    diagnostics["stderr_sha256"] = hashlib.sha256(process.stderr).hexdigest()
    if process.returncode != 0:
        diagnostics["detail"] = "dwgread GEODATA probe returned a non-zero exit code"
        return None, diagnostics
    if len(process.stdout) > _MAX_GEODATA_JSON_BYTES:
        diagnostics["detail"] = "dwgread GEODATA JSON exceeds the safety limit"
        return None, diagnostics
    try:
        payload = json.loads(process.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        diagnostics["detail"] = f"dwgread GEODATA JSON is invalid ({type(exc).__name__})"
        return None, diagnostics
    if not isinstance(payload, dict):
        diagnostics["detail"] = "dwgread GEODATA JSON root is not an object"
        return None, diagnostics
    objects = payload.get("OBJECTS", payload.get("objects", ()))
    if not isinstance(objects, list):
        diagnostics["detail"] = "dwgread GEODATA JSON has no object inventory"
        return None, diagnostics
    values = [
        item for item in objects
        if isinstance(item, dict)
        and str(item.get("object", item.get("dxfname", ""))).upper() == "GEODATA"
    ]
    diagnostics["geodata_object_count"] = len(values)
    if len(values) != 1:
        diagnostics["detail"] = f"expected one GEODATA object, found {len(values)}"
        return None, diagnostics
    value = values[0]
    definition = str(value.get("coord_system_def", ""))
    coordinate_match = re.search(
        r"<ProjectedCoordinateSystem\s+id=[\"']([^\"']+)[\"']",
        definition,
        flags=re.IGNORECASE,
    )
    epsg_match = re.search(
        r"<Alias\s+id=[\"'](\d+)[\"']\s+type=[\"']CoordinateSystem[\"']",
        definition,
        flags=re.IGNORECASE,
    )
    if coordinate_match is None or epsg_match is None:
        diagnostics["detail"] = "GEODATA coordinate-system definition has no EPSG alias"
        return None, diagnostics
    try:
        registration = normalize_geodata_registration({
            "schema_version": GEODATA_REGISTRATION_SCHEMA,
            "coordinate_system_id": coordinate_match.group(1),
            "target_crs": f"EPSG:{epsg_match.group(1)}",
            "design_point": value.get("design_pt"),
            "reference_point": value.get("ref_pt"),
            "horizontal_unit_scale": value.get("unit_scale_horiz", 1.0),
            "user_scale_factor": value.get("user_scale_factor", 1.0),
            "north_direction": value.get("north_dir"),
            "authority": "DWG_DIRECT:GEODATA",
        })
    except (TypeError, ValueError, OverflowError) as exc:
        diagnostics["detail"] = f"GEODATA registration facts are invalid ({exc})"
        return None, diagnostics
    diagnostics["status"] = "available"
    diagnostics["detail"] = "authoritative GEODATA registration extracted"
    diagnostics["coordinate_system_id"] = registration["coordinate_system_id"]
    diagnostics["target_crs"] = registration["target_crs"]
    return registration, diagnostics


def _metadata_record(
    source: Path,
    source_sha256: str,
    document: Any,
    *,
    geodata_registration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    insunits = int(document.header.get("$INSUNITS", 0) or 0)
    cgeocs = str(document.header.get("$CGEOCS", "") or "").strip()
    if not cgeocs and geodata_registration is not None:
        cgeocs = str(geodata_registration["coordinate_system_id"])
    metadata_text = f"INSUNITS={insunits}"
    if cgeocs:
        metadata_text += f";CGEOCS={cgeocs}"
    entity_key = hashlib.sha256(
        f"{source_sha256}|DOCUMENT_METADATA|".encode("utf-8")
    ).hexdigest()
    raw = {
        "schema_version": _RAW_PROPERTIES_SCHEMA,
        "extraction_backend": "libredwg_cli_dxf",
        "reader_backend_status": "supported",
        "object_name": "DOCUMENT_METADATA",
        "dwg_type_name": "DOCUMENT_METADATA",
        "handle": "DOCUMENT_METADATA",
        "text": metadata_text,
        "metadata_evidence": "reader" if cgeocs else "partial",
        "geodata_registration": geodata_registration,
        "unsupported_reasons": [],
        "unsupported_reason": "",
        "geometry_status": "unavailable",
        "inventory_support_status": "full",
    }
    return {
        "entity_key": entity_key,
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
        "raw_properties": raw,
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


def extract_dwg_records(
    source_path: str | Path, *, layout_filter: str | None = None
) -> DWGRecordInventory:
    capability = libredwg_cli_capability()
    if not capability.available:
        raise ReaderUnavailableError(
            f"LibreDWG CLI reader unavailable: {capability.detail} {capability.remediation}"
        )
    from ezdxf import readfile

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if layout_filter is None:
        layout_filter = os.environ.get("CAD2GIS_LAYOUT") or None
    executable, _source = discover_libredwg_cli()
    assert executable is not None
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    with tempfile.TemporaryDirectory(prefix="cad2gis-libredwg-dxf-") as temp_name:
        output = Path(temp_name) / f"{source.stem}.dxf"
        process = subprocess.run(
            [str(executable), "-y", "-o", str(output), str(source)],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if process.returncode != 0 or not output.is_file():
            stderr = process.stderr.decode("utf-8", errors="replace")[-2000:]
            raise ReaderUnavailableError(
                "LibreDWG dwg2dxf failed "
                f"(exit={process.returncode}): {stderr.strip()}"
            )
        document = readfile(output)

        dxf_cgeocs = str(document.header.get("$CGEOCS", "") or "").strip()
        geodata_registration = None
        geodata_diagnostics: dict[str, Any] = {
            "status": "not_required",
            "detail": "DXF header retained CGEOCS",
        }
        if not dxf_cgeocs:
            geodata_registration, geodata_diagnostics = _read_geodata_registration(
                Path(executable), source,
            )
        records: list[dict[str, Any]] = [
            _metadata_record(
                source,
                source_sha256,
                document,
                geodata_registration=geodata_registration,
            )
        ]
        unsupported = Counter()
        entity_count = 0
        attribute_count = 0
        for context, layout, layout_role, cad_role in _contexts(document):
            if layout_filter is not None and layout_role not in {"block_definition"}:
                if layout != layout_filter:
                    continue
            for entity in context:
                record = _record(
                    document=document,
                    entity=entity,
                    source=source,
                    source_sha256=source_sha256,
                    layout=layout,
                    layout_role=layout_role,
                    cad_role=cad_role,
                )
                records.append(record)
                entity_count += 1
                unsupported.update(record["raw_properties"]["unsupported_reasons"])
                if entity.dxftype().upper() == "INSERT":
                    for attribute in entity.attribs:
                        attribute_record = _record(
                            document=document,
                            entity=attribute,
                            source=source,
                            source_sha256=source_sha256,
                            layout=layout,
                            layout_role="attribute",
                            cad_role="attribute",
                        )
                        records.append(attribute_record)
                        attribute_count += 1
                        unsupported.update(
                            attribute_record["raw_properties"]["unsupported_reasons"]
                        )

        replacements = _escaped_string_map(records)
        if replacements:
            records = [_preserve_undecodable_strings(record, document, replacements) for record in records]
        stderr_text = process.stderr.decode("utf-8", errors="replace")
        text_preservation = [
            record["raw_properties"]["text_encoding_preservation"] for record in records
            if "text_encoding_preservation" in record["raw_properties"]
        ]
        diagnostics = {
            "backend": "libredwg_cli_dxf",
            "extraction_backend": "libredwg_cli_dxf",
            "skipped_rows": 0,
            "inventory_complete": True,
            "metadata_evidence": records[0]["raw_properties"]["metadata_evidence"],
            "geodata": geodata_diagnostics,
            "unsupported_reason_counts": dict(sorted(unsupported.items())),
            "source_entity_count": entity_count,
            "attribute_entity_count": attribute_count,
            "returned_records": len(records),
            "parsed_rows": len(records),
            "total_rows": len(records),
            "completion_rows": len(records),
            "libredwg_cli": str(executable),
            "libredwg_exit_code": process.returncode,
            "libredwg_diagnostic_line_count": len(stderr_text.splitlines()),
            "libredwg_diagnostics_sha256": hashlib.sha256(process.stderr).hexdigest(),
            "intermediate_format": "DXF",
            "intermediate_persisted": False,
            "text_encoding_preservation": {
                "schema_version": _TEXT_PRESERVATION_SCHEMA,
                "status": "escaped_undecodable_code_units" if text_preservation else "not_required",
                "affected_records": len(text_preservation),
                "affected_fields": sum(item["affected_fields"] for item in text_preservation),
                "surrogate_code_units": sum(item["surrogate_code_units"] for item in text_preservation),
                "key_collisions": sum(item["key_collisions"] for item in text_preservation),
                "counting_scope": "all reader record fields including duplicated raw_properties values",
                "intermediate_dxf_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "recovery_evidence": "per-record raw_properties.text_encoding_preservation.fields",
            },
        }
    return DWGRecordInventory(records, diagnostics=diagnostics)


__all__ = [
    "DWGRecordInventory",
    "extract_dwg_records",
    "libredwg_cli_capability",
]
