"""Write validated GIS geometry to an audited, modern DXF document.

Polygon rings are exported as closed boundary polylines, including interior
rings. They are not CAD surfaces or filled hatches. Attributes are retained by
the caller's sidecar; this writer returns the handles needed to associate them.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from pathlib import Path
from typing import Any, Sequence

import ezdxf
from ezdxf.lldxf import validator
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from .reverse_models import GISFeature, ReverseConversionError


def _parts(geometry: BaseGeometry):
    if geometry.geom_type in {"MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection"}:
        for part in geometry.geoms:
            yield from _parts(part)
    else:
        yield geometry


def _coordinates(geometry: BaseGeometry):
    for part in _parts(geometry):
        if part.geom_type == "Polygon":
            yield from part.exterior.coords
            for ring in part.interiors:
                yield from ring.coords
        else:
            yield from part.coords


def _validate_feature(feature: GISFeature) -> None:
    geometry = feature.geometry
    context = f"Feature {feature.feature_id!r} in layer {feature.layer!r}"
    if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
        raise ReverseConversionError(f"{context} has missing or empty geometry")
    if getattr(geometry, "has_m", False):
        raise ReverseConversionError(f"{context} has M coordinates; DXF measure export is unsupported")
    for part in _parts(geometry):
        if part.is_empty:
            raise ReverseConversionError(f"{context} contains an empty geometry part")
        if part.geom_type not in {"Point", "LineString", "LinearRing", "Polygon"}:
            raise ReverseConversionError(f"{context} has unsupported geometry {part.geom_type}")
    if any(len(coord) not in {2, 3} or not all(math.isfinite(v) for v in coord)
           for coord in _coordinates(geometry)):
        raise ReverseConversionError(f"{context} has non-finite or unsupported coordinates")
    if not geometry.is_valid:
        raise ReverseConversionError(f"{context} has invalid geometry: {explain_validity(geometry)}")


def _requested_layer(feature: GISFeature, layer_field: str | None) -> str:
    value = feature.layer
    if layer_field is not None:
        if layer_field not in feature.properties or feature.properties[layer_field] is None:
            raise ReverseConversionError(
                f"Feature {feature.feature_id!r} has no layer field {layer_field!r}"
            )
        value = feature.properties[layer_field]
        if isinstance(value, (dict, list, tuple, set)):
            raise ReverseConversionError(f"Layer field {layer_field!r} must contain scalar values")
    name = str(value)
    if not name.strip():
        raise ReverseConversionError(f"Feature {feature.feature_id!r} has an empty CAD layer name")
    return name


def _layer_names(requested: Sequence[str]) -> dict[str, str]:
    """Allocate in sorted order so feature traversal cannot change CAD names."""
    result: dict[str, str] = {}
    used = {"0", "defpoints"}
    invalid = validator.INVALID_LAYER_NAME_CHARACTERS | {"|"}
    for name in sorted(set(requested), key=lambda item: (item.casefold(), item)):
        base = "".join(
            "_" if char in invalid or unicodedata.category(char).startswith("C") else char
            for char in unicodedata.normalize("NFC", name)
        ).strip() or "GIS"
        base = base[:255]
        candidate = base
        if candidate.casefold() in used:
            digest = hashlib.sha256(name.encode("utf-8", errors="surrogatepass")).hexdigest()[:10]
            candidate = f"{base[:243]}__{digest}"
            suffix = 1
            while candidate.casefold() in used:
                candidate = f"{base[:230]}__{digest}_{suffix}"
                suffix += 1
        if not validator.is_valid_layer_name(candidate):
            raise ReverseConversionError(f"Unable to construct a valid CAD layer for {name!r}")
        used.add(candidate.casefold())
        result[name] = candidate
    return result


def _style(properties: dict[str, Any]) -> dict[str, int]:
    attributes: dict[str, int] = {}
    for key, cad_key, check in (
        ("style_aci", "color", lambda value: 0 <= value <= 256),
        ("style_truecolor", "true_color", lambda value: 0 <= value <= 0xFFFFFF),
        ("style_lineweight", "lineweight", validator.is_valid_lineweight),
    ):
        value = properties.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and check(value):
            attributes[cad_key] = value
    return attributes


def _literal_mtext(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        raise ReverseConversionError("Label fields must contain scalar values")
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if any(unicodedata.category(char) == "Cs" or
           (unicodedata.category(char) == "Cc" and char not in "\n\t") for char in text):
        raise ReverseConversionError("Label text contains unsupported control characters")
    # Escape user formatting before introducing our own paragraph and grouping
    # codes. Empty groups after percent symbols prevent legacy %%d/%%p/%%c
    # substitution and field expressions. '^ ' is DXF's literal caret notation.
    return (text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            .replace("^", "^ ").replace("%", "%{}")
            .replace("\t", "    ").replace("\n", "\\P"))


def _anchor(geometry: BaseGeometry) -> tuple[float, ...]:
    parts = list(_parts(geometry))
    part = max(parts, key=lambda value: (value.area, value.length))
    if part.geom_type == "Point":
        return tuple(part.coords[0])
    if part.geom_type in {"LineString", "LinearRing"}:
        return tuple(part.interpolate(0.5, normalized=True).coords[0])
    point = part.representative_point()
    coord = tuple(point.coords[0])
    # The representative point is computed in XY; for a 3D boundary use the
    # first ring vertex's elevation for annotation, without altering geometry.
    first = part.exterior.coords[0]
    return (*coord, first[2]) if len(first) == 3 else coord


def write_dxf(
    features: Sequence[GISFeature],
    destination: Path,
    *,
    units: int,
    label_field: str | None = None,
    layer_field: str | None = None,
    label_height: float = 2.5,
) -> dict[str, Any]:
    """Write R2018 DXF, reopen it, and reject audit errors or audit repairs.

    Coordinates must already be in the caller's selected output coordinate
    system. Z is retained; M is explicitly unsupported. All returned handles
    identify top-level modelspace entities, excluding POLYLINE's VERTEX records.
    """
    if isinstance(units, bool) or not isinstance(units, int) or not 0 <= units <= 24:
        raise ReverseConversionError("DXF units must be an integer between 0 and 24")
    if isinstance(label_height, bool) or not isinstance(label_height, (int, float)) or not math.isfinite(label_height) or label_height <= 0:
        raise ReverseConversionError("Label height must be a positive finite number")
    features = list(features)
    if not features:
        raise ReverseConversionError("No GIS features are available for DXF export")
    requested = []
    labels = []
    for feature in features:
        _validate_feature(feature)
        requested.append(_requested_layer(feature, layer_field))
        if label_field is not None and label_field not in feature.properties:
            raise ReverseConversionError(
                f"Feature {feature.feature_id!r} has no label field {label_field!r}"
            )
        label = feature.properties.get(label_field) if label_field is not None else None
        labels.append(None if label is None or label == "" else _literal_mtext(label))
    layer_map = _layer_names(requested)
    document = ezdxf.new("R2018")
    document.units = units
    for name in layer_map.values():
        document.layers.new(name)
    modelspace = document.modelspace()
    feature_entities = []
    geometry_count = 0
    label_count = 0

    def add_boundary(coords, attributes, *, closed=False):
        points = [tuple(coord) for coord in coords]
        if closed and points[0] == points[-1]:
            points.pop()
        if len(points[0]) == 3:
            return modelspace.add_polyline3d(points, close=closed, dxfattribs=attributes)
        return modelspace.add_lwpolyline(points, format="xy", close=closed, dxfattribs=attributes)

    for feature, requested_name, label in zip(features, requested, labels):
        cad_layer = layer_map[requested_name]
        attributes = {"layer": cad_layer, **_style(feature.properties)}
        handles = []
        for part in _parts(feature.geometry):
            if part.geom_type == "Point":
                entities = [modelspace.add_point(part.coords[0], dxfattribs=attributes)]
            elif part.geom_type in {"LineString", "LinearRing"}:
                entities = [add_boundary(part.coords, attributes, closed=part.geom_type == "LinearRing")]
            else:
                entities = [add_boundary(ring.coords, attributes, closed=True)
                            for ring in (part.exterior, *part.interiors)]
            for entity in entities:
                handles.append({"handle": entity.dxf.handle, "role": "geometry"})
                geometry_count += 1
        if label is not None:
            anchor = _anchor(feature.geometry)
            if not all(math.isfinite(value) for value in anchor):
                raise ReverseConversionError(
                    f"Feature {feature.feature_id!r} has no finite label anchor"
                )
            entity = modelspace.add_mtext(label, dxfattribs={
                **attributes, "insert": anchor, "char_height": label_height,
            })
            handles.append({"handle": entity.dxf.handle, "role": "label"})
            label_count += 1
        feature_entities.append({
            "input_layer": feature.layer, "feature_id": feature.feature_id,
            "cad_layer": cad_layer, "entities": handles,
        })
    try:
        document.saveas(str(destination))
        reopened = ezdxf.readfile(str(destination))
        audit = reopened.audit()
        if audit.errors or audit.fixes:
            raise ReverseConversionError(
                f"Written DXF failed audit ({len(audit.errors)} errors, {len(audit.fixes)} repairs)",
                code="REVERSE_OUTPUT_INVALID",
            )
        if len(reopened.modelspace()) != geometry_count + label_count:
            raise ReverseConversionError("Written DXF entity count changed after reopening", code="REVERSE_OUTPUT_INVALID")
        for record in feature_entities:
            for item in record["entities"]:
                if reopened.entitydb.get(item["handle"]) is None:
                    raise ReverseConversionError("Written DXF lost a source entity handle", code="REVERSE_OUTPUT_INVALID")
    except (OSError, ezdxf.DXFError) as error:
        raise ReverseConversionError(f"Could not write or validate DXF: {error}", code="REVERSE_OUTPUT_FAILED") from error
    return {
        "entity_count": geometry_count + label_count,
        "geometry_entity_count": geometry_count,
        "label_count": label_count,
        "layer_map": layer_map,
        "feature_entities": feature_entities,
    }
