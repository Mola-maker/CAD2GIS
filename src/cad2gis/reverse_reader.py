"""Read source GIS features without GDAL or writes to the source dataset.

The importer deliberately fails on a feature it cannot preserve: null/empty,
invalid, measured, nonfinite, or unsupported geometry is never silently skipped.
"""

from __future__ import annotations

import base64
import json
import math
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from pyproj import CRS
from shapely import from_wkb
from shapely.geometry import shape
from shapely.validation import explain_validity

from .reverse_models import GISDataset, GISFeature, ReverseConversionError

_GEOMETRY_TYPES = {
    "Point", "LineString", "Polygon", "MultiPoint", "MultiLineString",
    "MultiPolygon", "GeometryCollection",
}
_COORDINATE_DEPTH = {
    "Point": 0, "LineString": 1, "Polygon": 2, "MultiPoint": 1,
    "MultiLineString": 2, "MultiPolygon": 3,
}


def _fail(message: str) -> NoReturn:
    raise ReverseConversionError(message)


def _crs(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{context}: CRS must be a nonempty name or WKT definition")
    try:
        return CRS.from_user_input(value).to_string()
    except Exception as exc:
        raise ReverseConversionError(f"{context}: invalid or unsupported CRS {value!r}") from exc


def _selected(available: list[str], layers: Sequence[str] | None) -> list[str]:
    if layers is None:
        return available
    if isinstance(layers, (str, bytes)):
        _fail("Layer selection must be a sequence of layer names")
    selected = list(layers)
    if not selected or any(not isinstance(item, str) or not item for item in selected):
        _fail("Layer selection must contain nonempty layer names")
    if len(set(selected)) != len(selected):
        _fail("Layer selection contains duplicate names")
    missing = [item for item in selected if item not in available]
    if missing:
        _fail(f"Requested layers do not exist: {missing!r}; available layers: {available!r}")
    return selected


def _metadata(source: Path, kind: str, available: list[str], selected: list[str], count: int) -> dict[str, Any]:
    return {
        "source_type": kind,
        "source_path": str(source.resolve()),
        "available_layers": available,
        "selected_layers": selected,
        "layer_count": len(selected),
        "feature_count": count,
        "empty_geometry_count": 0,
        "null_geometry_count": 0,
    }


def _validate_geometry(geometry: Any, context: str) -> None:
    if geometry.is_empty:
        _fail(f"{context}: empty geometry cannot be converted")
    if geometry.geom_type not in _GEOMETRY_TYPES:
        _fail(f"{context}: unsupported geometry type {geometry.geom_type!r}")
    if getattr(geometry, "has_m", False):
        _fail(f"{context}: measured (M) coordinates cannot be preserved")
    if not geometry.is_valid:
        _fail(f"{context}: invalid geometry: {explain_validity(geometry)}")
    if hasattr(geometry, "geoms"):
        for index, child in enumerate(geometry.geoms):
            _validate_geometry(child, f"{context}, part {index + 1}")


def _positions(value: Any, depth: int, context: str) -> set[int]:
    if not isinstance(value, list) or not value:
        _fail(f"{context}: empty or malformed coordinate array")
    if depth:
        dimensions: set[int] = set()
        for child in value:
            dimensions.update(_positions(child, depth - 1, context))
        return dimensions
    if len(value) not in (2, 3):
        _fail(f"{context}: positions must have 2 or 3 ordinates; measured/4D coordinates are unsupported")
    for ordinate in value:
        if isinstance(ordinate, bool) or not isinstance(ordinate, (int, float)):
            _fail(f"{context}: coordinate ordinates must be numbers")
        try:
            finite = math.isfinite(ordinate)
        except (OverflowError, TypeError):
            finite = False
        if not finite:
            _fail(f"{context}: nonfinite coordinate ordinate")
    return {len(value)}


def _geojson_geometry(value: Any, context: str, depth: int = 0) -> Any:
    if value is None:
        _fail(f"{context}: null geometry cannot be converted")
    if depth > 64 or not isinstance(value, dict):
        _fail(f"{context}: malformed or excessively nested geometry")
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in _GEOMETRY_TYPES:
        _fail(f"{context}: unsupported geometry type {kind!r}")
    if "crs" in value:
        _fail(f"{context}: per-geometry CRS is unsupported; declare the document CRS")
    if kind == "GeometryCollection":
        children = value.get("geometries")
        if not isinstance(children, list) or not children:
            _fail(f"{context}: empty or malformed GeometryCollection")
        for index, child in enumerate(children):
            _geojson_geometry(child, f"{context}, part {index + 1}", depth + 1)
    else:
        coordinates = value.get("coordinates")
        dimensions = _positions(coordinates, _COORDINATE_DEPTH[kind], context)
        if len(dimensions) != 1:
            _fail(f"{context}: mixed coordinate dimensions cannot be preserved")
        polygons = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
        for polygon in polygons:
            for ring in polygon:
                if len(ring) < 4 or ring[0] != ring[-1]:
                    _fail(f"{context}: polygon rings must be closed and contain at least four positions")
    try:
        geometry = shape(value)
    except Exception as exc:
        raise ReverseConversionError(f"{context}: malformed geometry: {exc}") from exc
    _validate_geometry(geometry, context)
    return geometry


def _read_geojson(source: Path, layers: Sequence[str] | None) -> GISDataset:
    def reject_constant(value: str) -> None:
        _fail(f"GeoJSON contains nonfinite JSON number {value}")

    def unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(f"GeoJSON object contains duplicate member {key!r}")
            result[key] = value
        return result

    try:
        with source.open("r", encoding="utf-8-sig") as stream:
            document = json.load(stream, parse_constant=reject_constant, object_pairs_hook=unique_members)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReverseConversionError(f"Cannot read GeoJSON {source}: {exc}") from exc
    if not isinstance(document, dict):
        _fail("GeoJSON root must be a FeatureCollection or Feature object")
    kind = document.get("type")
    if kind == "FeatureCollection":
        records = document.get("features")
        if not isinstance(records, list):
            _fail("GeoJSON FeatureCollection.features must be an array")
    elif kind == "Feature":
        records = [document]
    else:
        _fail("GeoJSON root must be a FeatureCollection or Feature object")
    layer = document.get("name", source.stem)
    if not isinstance(layer, str) or not layer:
        _fail("GeoJSON layer name must be a nonempty string")
    selected = _selected([layer], layers)
    source_crs = "OGC:CRS84"
    if "crs" in document:
        legacy = document["crs"]
        if not isinstance(legacy, dict) or legacy.get("type") != "name" or not isinstance(legacy.get("properties"), dict):
            _fail("GeoJSON legacy CRS must use type='name' and properties.name")
        source_crs = _crs(legacy["properties"].get("name"), "GeoJSON")
    features: list[GISFeature] = []
    seen: set[str] = set()
    for index, record in enumerate(records, 1):
        context = f"GeoJSON layer {layer!r}, feature {index}"
        if not isinstance(record, dict) or record.get("type") != "Feature":
            _fail(f"{context}: expected a Feature object")
        if "crs" in record and record is not document:
            _fail(f"{context}: per-feature CRS is unsupported; declare the collection CRS")
        identifier = record.get("id", f"feature-{index}")
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int, float)) or identifier == "":
            _fail(f"{context}: id must be a nonempty string or finite number")
        if isinstance(identifier, float) and not math.isfinite(identifier):
            _fail(f"{context}: id must be finite")
        feature_id = str(identifier)
        if feature_id in seen:
            _fail(f"{context}: duplicate feature id {feature_id!r}")
        seen.add(feature_id)
        properties = record.get("properties")
        if properties is None:
            properties = {}
        if not isinstance(properties, dict):
            _fail(f"{context}: properties must be an object or null")
        # JSON exponent overflow (1e999) is not passed to parse_constant.
        try:
            json.dumps(properties, allow_nan=False)
        except (ValueError, OverflowError, RecursionError) as exc:
            raise ReverseConversionError(f"{context}: properties contain nonfinite numbers") from exc
        geometry = _geojson_geometry(record.get("geometry"), context)
        features.append(GISFeature(feature_id, layer, geometry, properties))
    return GISDataset(features, {layer: source_crs}, _metadata(source, "geojson", [layer], selected, len(features)))


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _validate_wkb(payload: bytes, context: str) -> tuple[str, bool]:
    """Validate the complete ISO WKB stream before GEOS can discard M or bytes."""
    position = 0
    names = {1: "Point", 2: "LineString", 3: "Polygon", 4: "MultiPoint", 5: "MultiLineString", 6: "MultiPolygon", 7: "GeometryCollection"}

    def unpack(fmt: str) -> tuple[Any, ...]:
        nonlocal position
        size = struct.calcsize(fmt)
        if position + size > len(payload):
            _fail(f"{context}: truncated WKB geometry")
        result = struct.unpack_from(fmt, payload, position)
        position += size
        return result

    def parse(depth: int = 0, expected: int | None = None) -> tuple[str, bool]:
        nonlocal position
        if depth > 64:
            _fail(f"{context}: excessively nested WKB geometry")
        byte_order = unpack("B")[0]
        if byte_order not in (0, 1):
            _fail(f"{context}: invalid WKB byte order")
        endian = "<" if byte_order else ">"
        encoded = unpack(endian + "I")[0]
        dimension, kind = divmod(encoded, 1000)
        if dimension in (2, 3):
            _fail(f"{context}: measured (M) coordinates cannot be preserved")
        if dimension not in (0, 1) or kind not in names:
            _fail(f"{context}: unsupported WKB geometry type {encoded}")
        if expected is not None and kind != expected:
            _fail(f"{context}: invalid WKB multi-geometry member type")
        ordinates = 3 if dimension else 2

        def points(count: int) -> None:
            nonlocal position
            size = count * ordinates * 8
            if position + size > len(payload):
                _fail(f"{context}: truncated WKB coordinates")
            for point in struct.iter_unpack(endian + "d" * ordinates, memoryview(payload)[position:position + size]):
                if not all(math.isfinite(value) for value in point):
                    _fail(f"{context}: nonfinite or empty WKB coordinates")
            position += size

        if kind == 1:
            points(1)
        elif kind == 2:
            count = unpack(endian + "I")[0]
            if count < 2:
                _fail(f"{context}: empty or malformed WKB LineString")
            points(count)
        elif kind == 3:
            count = unpack(endian + "I")[0]
            if not count or count > (len(payload) - position) // 4:
                _fail(f"{context}: malformed WKB ring count")
            for _ in range(count):
                ring_count = unpack(endian + "I")[0]
                if ring_count < 4:
                    _fail(f"{context}: empty or malformed WKB polygon ring")
                points(ring_count)
        else:
            count = unpack(endian + "I")[0]
            if not count or count > (len(payload) - position) // 5:
                _fail(f"{context}: malformed WKB member count")
            for _ in range(count):
                _, child_z = parse(depth + 1, kind - 3 if kind in (4, 5, 6) else None)
                if child_z != bool(dimension):
                    _fail(f"{context}: mixed WKB coordinate dimensions")
        return names[kind], bool(dimension)

    result = parse()
    if position != len(payload):
        _fail(f"{context}: trailing bytes after WKB geometry")
    return result


def _gpkg_geometry(value: Any, srs_id: int, declared: str, z: int, m: int, context: str) -> Any:
    if value is None:
        _fail(f"{context}: null geometry cannot be converted")
    if not isinstance(value, bytes) or len(value) < 8 or value[:2] != b"GP":
        _fail(f"{context}: invalid GeoPackage geometry header")
    if value[2] != 0:
        _fail(f"{context}: unsupported GeoPackage geometry version {value[2]}")
    flags = value[3]
    envelope = (flags >> 1) & 7
    if flags & 0xC0 or flags & 0x20 or envelope > 4:
        _fail(f"{context}: unsupported GeoPackage geometry flags {flags:#x}")
    if flags & 0x10:
        _fail(f"{context}: empty GeoPackage geometry cannot be converted")
    if envelope in (3, 4) or m == 1:
        _fail(f"{context}: measured (M) coordinates cannot be preserved")
    endian = "<" if flags & 1 else ">"
    header_srs = struct.unpack_from(endian + "i", value, 4)[0]
    if header_srs != srs_id:
        _fail(f"{context}: geometry header SRS {header_srs} differs from layer SRS {srs_id}")
    envelope_count = (0, 4, 6, 6, 8)[envelope]
    offset = 8 + envelope_count * 8
    if len(value) <= offset:
        _fail(f"{context}: truncated GeoPackage geometry envelope or WKB")
    if envelope_count:
        values = struct.unpack_from(endian + "d" * envelope_count, value, 8)
        if not all(math.isfinite(item) for item in values) or any(values[index] > values[index + 1] for index in range(0, envelope_count, 2)):
            _fail(f"{context}: invalid GeoPackage geometry envelope")
    payload = value[offset:]
    kind, has_z = _validate_wkb(payload, context)
    if declared != "GEOMETRY" and kind.upper() != declared:
        _fail(f"{context}: WKB type {kind} differs from declared geometry type {declared}")
    if (z == 0 and has_z) or (z == 1 and not has_z) or (envelope == 2 and not has_z):
        _fail(f"{context}: geometry Z dimension conflicts with GeoPackage declaration")
    try:
        geometry = from_wkb(payload, on_invalid="raise")
    except Exception as exc:
        raise ReverseConversionError(f"{context}: malformed WKB geometry: {exc}") from exc
    _validate_geometry(geometry, context)
    return geometry


def _gpkg_crs(connection: sqlite3.Connection, srs_id: int, context: str) -> str | None:
    row = connection.execute(
        "SELECT organization, organization_coordsys_id, definition FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (srs_id,),
    ).fetchone()
    if row is None:
        _fail(f"{context}: SRS {srs_id} is missing from gpkg_spatial_ref_sys")
    if srs_id in (-1, 0):
        return None
    columns = connection.execute("PRAGMA table_info(gpkg_spatial_ref_sys)").fetchall()
    if any(column[1].lower() == "definition_12_063" for column in columns):
        wkt2 = connection.execute(
            "SELECT definition_12_063 FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (srs_id,),
        ).fetchone()[0]
        # The WKT2 extension is authoritative when defined. An invalid explicit
        # definition must not silently fall back to an older WKT1/authority row.
        if wkt2 is not None and (not isinstance(wkt2, str) or wkt2.strip().lower() not in ("", "undefined")):
            return _crs(wkt2, f"{context}, definition_12_063")
    organization, code, definition = row
    if isinstance(organization, str) and organization.upper() == "EPSG":
        return _crs(f"EPSG:{code}", context)
    return _crs(definition, context)


def _property(value: Any, context: str) -> Any:
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f"{context}: nonfinite property value cannot be preserved as JSON")
    return value


def _read_geopackage(source: Path, layers: Sequence[str] | None) -> GISDataset:
    features: list[GISFeature] = []
    layer_crs: dict[str, str | None] = {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        contents = connection.execute(
            "SELECT table_name, srs_id FROM gpkg_contents WHERE data_type = 'features' ORDER BY table_name",
        ).fetchall()
        available = [row[0] for row in contents]
        if not available:
            _fail("GeoPackage contains no feature layers")
        if any(not isinstance(name, str) or not name for name in available) or len(available) != len(set(available)):
            _fail("GeoPackage has invalid or duplicate feature layer names")
        selected = _selected(available, layers)
        content_srs = dict(contents)
        for layer in selected:
            context = f"GeoPackage layer {layer!r}"
            definitions = connection.execute(
                "SELECT column_name, geometry_type_name, srs_id, z, m FROM gpkg_geometry_columns WHERE table_name = ?", (layer,),
            ).fetchall()
            if len(definitions) != 1:
                _fail(f"{context}: expected exactly one geometry column declaration")
            column, declared, srs_id, z, m = definitions[0]
            if not isinstance(column, str) or not column or not isinstance(declared, str):
                _fail(f"{context}: invalid geometry column declaration")
            declared = declared.upper()
            if declared not in {name.upper() for name in _GEOMETRY_TYPES} | {"GEOMETRY"} or z not in (0, 1, 2) or m not in (0, 1, 2):
                _fail(f"{context}: unsupported geometry column declaration")
            if not isinstance(srs_id, int) or content_srs[layer] != srs_id:
                _fail(f"{context}: inconsistent SRS declarations")
            layer_crs[layer] = _gpkg_crs(connection, srs_id, context)
            columns = connection.execute(f"PRAGMA table_info({_identifier(layer)})").fetchall()
            primary_keys = [row for row in columns if row[5]]
            if len(primary_keys) != 1 or primary_keys[0][2].upper() != "INTEGER":
                _fail(f"{context}: feature table requires a single INTEGER primary key")
            key = primary_keys[0][1]
            cursor = connection.execute(f"SELECT * FROM {_identifier(layer)} ORDER BY {_identifier(key)}")
            names = [item[0] for item in cursor.description]
            if column not in names:
                _fail(f"{context}: declared geometry column does not exist")
            geometry_index = names.index(column)
            key_index = names.index(key)
            seen: set[str] = set()
            for record in cursor:
                feature_id = str(record[key_index])
                feature_context = f"{context}, feature {feature_id!r}"
                if record[key_index] is None or feature_id in seen:
                    _fail(f"{feature_context}: missing or duplicate primary key")
                seen.add(feature_id)
                geometry = _gpkg_geometry(record[geometry_index], srs_id, declared, z, m, feature_context)
                properties = {name: _property(record[index], feature_context) for index, name in enumerate(names) if index != geometry_index}
                features.append(GISFeature(feature_id, layer, geometry, properties))
        return GISDataset(features, layer_crs, _metadata(source, "geopackage", available, selected, len(features)))
    except sqlite3.Error as exc:
        raise ReverseConversionError(f"Cannot read GeoPackage {source}: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def read_gis(source: Path, *, layers: Sequence[str] | None = None) -> GISDataset:
    """Load GeoJSON or GeoPackage; selected layers and features retain stable order.

    GeoJSON uses its ``name`` or filename stem as layer name. Absent GeoJSON IDs
    become ``feature-1`` etc.; any resulting ID collision is rejected. GeoPackage
    IDs are its integer primary keys. Binary attributes use a JSON object with
    ``encoding='base64'`` and ``data`` fields. Undefined GeoPackage CRS is ``None``.
    """
    source = Path(source)
    if not source.is_file():
        _fail(f"GIS source file does not exist: {source}")
    suffix = source.suffix.lower()
    if suffix in (".geojson", ".json"):
        return _read_geojson(source, layers)
    if suffix == ".gpkg":
        return _read_geopackage(source, layers)
    _fail(f"Unsupported GIS source format {suffix!r}; use .geojson, .json, or .gpkg")
