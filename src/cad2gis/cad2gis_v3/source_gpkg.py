"""Deterministic, source-fidelity GeoPackage materialization."""

from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..native_runtime import ensure_osgeo_runtime
from .accounting import EntityAccounting, account_entities
from .gpkg_metadata import normalize_geopackage_metadata
from .model import SourceEntity

ensure_osgeo_runtime()
from osgeo import ogr, osr  # noqa: E402

SOURCE_GPKG_SCHEMA_VERSION = "cad2gis-source-gpkg-v1"

MATERIALIZATION_LAYERS = (
    "source_points",
    "source_lines",
    "source_polygons",
    "source_text",
    "source_blocks",
    "source_metadata",
)

_TEXT_TYPES = {
    "ATTDEF",
    "ATTRIB",
    "DIMENSION",
    "MLEADER",
    "MTEXT",
    "MULTILEADER",
    "TABLE",
    "TABLE_CELL",
    "TEXT",
}
_BLOCK_TYPES = {"INSERT"}
_POINT_TYPES = {"POINT"}
_LINE_TYPES = {
    "ARC",
    "CIRCLE",
    "ELLIPSE",
    "HELIX",
    "LEADER",
    "LINE",
    "LWPOLYLINE",
    "MLINE",
    "POLYLINE",
    "POLYLINE_2D",
    "POLYLINE_3D",
    "RAY",
    "SPLINE",
    "XLINE",
}
_POLYGON_TYPES = {"3DFACE", "HATCH", "REGION", "SOLID", "TRACE", "WIPEOUT"}
_CLOSED_POLYLINE_TYPES = {"LWPOLYLINE", "POLYLINE", "POLYLINE_2D", "POLYLINE_3D"}

_ENTITY_FIELDS = (
    ("entity_key", ogr.OFTString),
    ("source_sha256", ogr.OFTString),
    ("source_file", ogr.OFTString),
    ("cad_handle", ogr.OFTString),
    ("cad_layout", ogr.OFTString),
    ("layout_role", ogr.OFTString),
    ("cad_role", ogr.OFTString),
    ("dwg_layer", ogr.OFTString),
    ("object_name", ogr.OFTString),
    ("dwg_type", ogr.OFTString),
    ("native_points", ogr.OFTString),
    ("native_centroid", ogr.OFTString),
    ("closed", ogr.OFTInteger),
    ("text", ogr.OFTString),
    ("block_name", ogr.OFTString),
    ("block_attributes", ogr.OFTString),
    ("dimension_value", ogr.OFTReal),
    ("scale_x", ogr.OFTReal),
    ("scale_y", ogr.OFTReal),
    ("scale_z", ogr.OFTReal),
    ("owner_handle", ogr.OFTString),
    ("dimension_text_override", ogr.OFTString),
    ("native_length", ogr.OFTReal),
    ("raw_properties", ogr.OFTString),
    ("curve_facts_schema", ogr.OFTString),
    ("curve_facts", ogr.OFTString),
    ("curve_fingerprint", ogr.OFTString),
    ("extraction_backend", ogr.OFTString),
    ("reader_backend_status", ogr.OFTString),
    ("aci_color", ogr.OFTInteger),
    ("true_color", ogr.OFTString),
    ("linetype", ogr.OFTString),
    ("lineweight", ogr.OFTInteger),
    ("rotation", ogr.OFTReal),
    ("entity_aci_color", ogr.OFTInteger),
    ("layer_aci_color", ogr.OFTInteger),
    ("entity_true_color", ogr.OFTString),
    ("layer_true_color", ogr.OFTString),
    ("entity_linetype", ogr.OFTString),
    ("layer_linetype", ogr.OFTString),
    ("entity_lineweight", ogr.OFTInteger),
    ("layer_lineweight", ogr.OFTInteger),
    ("legend_flag", ogr.OFTString),
    ("materialization_layer", ogr.OFTString),
    ("routing_reason", ogr.OFTString),
    ("terminal_state", ogr.OFTString),
    ("terminal_reasons", ogr.OFTString),
)

_GEOMETRY_TYPES = {
    "source_points": ogr.wkbPoint,
    "source_lines": ogr.wkbLineString,
    "source_polygons": ogr.wkbPolygon,
    "source_text": ogr.wkbPoint,
    "source_blocks": ogr.wkbPoint,
    "source_metadata": ogr.wkbNone,
}


@dataclass(frozen=True)
class SourceGeoPackageResult:
    path: Path
    entity_count: int
    layer_counts: dict[str, int]
    logical_sha256: str
    byte_sha256: str


@dataclass(frozen=True)
class _RoutedEntity:
    entity: SourceEntity
    accounting: EntityAccounting
    layer_name: str
    routing_reason: str
    geometry_points: tuple[tuple[float, float], ...]
    legend_flag: str = ""


def _lossless_json_value(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, dict):
        return {key: _lossless_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_lossless_json_value(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(
        _lossless_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _valid_points(
    values: Sequence[Sequence[float]],
) -> tuple[tuple[float, float], ...] | None:
    points: list[tuple[float, float]] = []
    try:
        for value in values:
            if len(value) != 2:
                return None
            x_coordinate = float(value[0])
            y_coordinate = float(value[1])
            if not math.isfinite(x_coordinate) or not math.isfinite(y_coordinate):
                return None
            points.append((x_coordinate, y_coordinate))
    except (TypeError, ValueError, OverflowError):
        return None
    return tuple(points)


def _valid_line(points: tuple[tuple[float, float], ...]) -> bool:
    return len(points) >= 2 and len(set(points)) >= 2


def _polygon_points(
    points: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...] | None:
    if len(points) < 3 or len(set(points)) < 3:
        return None
    closed_points = points if points[0] == points[-1] else (*points, points[0])
    twice_area = sum(
        start[0] * end[1] - end[0] * start[1]
        for start, end in zip(closed_points, closed_points[1:])
    )
    if twice_area == 0.0:
        return None
    return closed_points


def _route_entity(
    entity: SourceEntity,
    accounting: EntityAccounting,
    legend_flag_map: dict[str, str] | None = None,
) -> _RoutedEntity:
    legend_flag = (
        (legend_flag_map or {}).get(entity.entity_key, "")
        if legend_flag_map
        else ""
    )
    points = _valid_points(entity.points)
    if points is None:
        return _RoutedEntity(
            entity,
            accounting,
            "source_metadata",
            "invalid_source_points",
            (),
            legend_flag=legend_flag,
        )

    entity_type = entity.dwg_type.strip().upper()
    if entity_type in _TEXT_TYPES:
        if points:
            return _RoutedEntity(
                entity,
                accounting,
                "source_text",
                "source_text_anchor",
                (points[0],),
                legend_flag=legend_flag,
            )
        reason = "missing_text_anchor"
    elif entity_type in _BLOCK_TYPES:
        if points:
            return _RoutedEntity(
                entity,
                accounting,
                "source_blocks",
                "source_block_insertion_point",
                (points[0],),
                legend_flag=legend_flag,
            )
        reason = "missing_block_insertion_point"
    elif entity_type in _POINT_TYPES:
        if len(points) == 1:
            return _RoutedEntity(
                entity,
                accounting,
                "source_points",
                "source_point",
                points,
                legend_flag=legend_flag,
            )
        reason = "invalid_point_geometry"
    elif entity_type in _POLYGON_TYPES or (
        entity.closed and entity_type in _CLOSED_POLYLINE_TYPES
    ):
        polygon_points = _polygon_points(points)
        if polygon_points is not None:
            return _RoutedEntity(
                entity,
                accounting,
                "source_polygons",
                "source_closed_boundary",
                polygon_points,
                legend_flag=legend_flag,
            )
        reason = "invalid_polygon_geometry"
    elif entity_type in _LINE_TYPES:
        if _valid_line(points):
            return _RoutedEntity(
                entity,
                accounting,
                "source_lines",
                "source_linear_vertices",
                points,
                legend_flag=legend_flag,
            )
        reason = "invalid_line_geometry"
    else:
        reason = "unsupported_entity_kind"

    return _RoutedEntity(
        entity, accounting, "source_metadata", reason, (),
        legend_flag=legend_flag,
    )


def _entity_values(routed: _RoutedEntity) -> dict[str, Any]:
    entity = routed.entity
    style = entity.style
    return {
        "entity_key": entity.entity_key,
        "source_sha256": entity.source_sha256,
        "source_file": entity.source_file,
        "cad_handle": entity.handle,
        "cad_layout": entity.layout,
        "layout_role": entity.layout_role,
        "cad_role": entity.cad_role,
        "dwg_layer": entity.layer,
        "object_name": entity.object_name,
        "dwg_type": entity.dwg_type,
        "native_points": _json(entity.points),
        "native_centroid": _json(entity.centroid),
        "closed": int(entity.closed),
        "text": entity.text,
        "block_name": entity.block_name,
        "block_attributes": _json(entity.block_attributes),
        "dimension_value": entity.dimension_value,
        "scale_x": entity.scale[0],
        "scale_y": entity.scale[1],
        "scale_z": entity.scale[2],
        "owner_handle": entity.owner_handle,
        "dimension_text_override": entity.dimension_text_override,
        "native_length": entity.native_length,
        "raw_properties": _json(entity.raw_properties),
        "curve_facts_schema": entity.curve_schema_version,
        "curve_facts": _json(entity.curve_facts),
        "curve_fingerprint": entity.curve_fingerprint,
        "extraction_backend": entity.extraction_backend,
        "reader_backend_status": entity.reader_backend_status,
        "aci_color": style.aci_color,
        "true_color": style.true_color,
        "linetype": style.linetype,
        "lineweight": style.lineweight,
        "rotation": style.rotation,
        "entity_aci_color": style.entity_aci_color,
        "layer_aci_color": style.layer_aci_color,
        "entity_true_color": style.entity_true_color,
        "layer_true_color": style.layer_true_color,
        "entity_linetype": style.entity_linetype,
        "layer_linetype": style.layer_linetype,
        "entity_lineweight": style.entity_lineweight,
        "layer_lineweight": style.layer_lineweight,
        "materialization_layer": routed.layer_name,
        "routing_reason": routed.routing_reason,
        "terminal_state": routed.accounting.state.value,
        "terminal_reasons": _json(routed.accounting.reasons),
        "legend_flag": routed.legend_flag,
    }


def _source_srs(source_crs: str):
    if not str(source_crs).strip():
        raise ValueError("source_crs must not be blank")
    osr.DontUseExceptions()
    spatial_reference = osr.SpatialReference()
    if spatial_reference.SetFromUserInput(str(source_crs)) != 0:
        raise ValueError(f"Invalid source CRS: {source_crs!r}")
    if hasattr(spatial_reference, "SetAxisMappingStrategy"):
        spatial_reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return spatial_reference


def _create_layer(dataset, name: str, spatial_reference):
    geometry_type = _GEOMETRY_TYPES[name]
    layer = dataset.CreateLayer(
        name,
        None if geometry_type == ogr.wkbNone else spatial_reference,
        geometry_type,
    )
    if layer is None:
        raise RuntimeError(f"Could not create source GeoPackage layer: {name}")
    for field_name, field_type in _ENTITY_FIELDS:
        if layer.CreateField(ogr.FieldDefn(field_name, field_type)) != 0:
            raise RuntimeError(f"Could not create {name}.{field_name}")
    return layer


def _geometry(routed: _RoutedEntity):
    if routed.layer_name == "source_metadata":
        return None
    if routed.layer_name in {"source_points", "source_text", "source_blocks"}:
        geometry = ogr.Geometry(ogr.wkbPoint)
        geometry.AddPoint_2D(*routed.geometry_points[0])
        return geometry
    if routed.layer_name == "source_lines":
        geometry = ogr.Geometry(ogr.wkbLineString)
        for point in routed.geometry_points:
            geometry.AddPoint_2D(*point)
        return geometry
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for point in routed.geometry_points:
        ring.AddPoint_2D(*point)
    geometry = ogr.Geometry(ogr.wkbPolygon)
    geometry.AddGeometry(ring)
    return geometry


def _write_staged(
    path: Path,
    routed_entities: Sequence[_RoutedEntity],
    spatial_reference,
) -> None:
    driver = ogr.GetDriverByName("GPKG")
    if driver is None:
        raise RuntimeError("GDAL GeoPackage driver is unavailable")
    dataset = driver.CreateDataSource(str(path))
    if dataset is None:
        raise RuntimeError(f"Could not create source GeoPackage: {path}")
    transaction_started = False
    transaction_committed = False
    layers: dict[str, Any] = {}
    layer = None
    feature = None
    geometry = None
    try:
        if dataset.StartTransaction() != 0:
            raise RuntimeError(f"Could not start source GeoPackage transaction: {path}")
        transaction_started = True
        layers = {
            name: _create_layer(dataset, name, spatial_reference)
            for name in MATERIALIZATION_LAYERS
        }
        for routed in routed_entities:
            layer = layers[routed.layer_name]
            feature = ogr.Feature(layer.GetLayerDefn())
            for field_name, value in _entity_values(routed).items():
                if value is not None:
                    feature.SetField(field_name, value)
            geometry = _geometry(routed)
            if geometry is not None:
                feature.SetGeometry(geometry)
            if layer.CreateFeature(feature) != 0:
                raise RuntimeError(
                    f"Could not materialize source entity: {routed.entity.entity_key}"
                )
        if dataset.CommitTransaction() != 0:
            raise RuntimeError("Could not commit source GeoPackage transaction")
        transaction_committed = True
        dataset.FlushCache()
    finally:
        geometry = None
        feature = None
        layer = None
        layers.clear()
        gc.collect()
        if transaction_started and not transaction_committed:
            try:
                dataset.RollbackTransaction()
            except Exception:
                pass
        dataset.Close()
        dataset = None
        gc.collect()


def _write_reconciliation_tables(
    path: Path,
    routed_entities: Sequence[_RoutedEntity],
    layer_counts: Mapping[str, int],
) -> None:
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(
                """CREATE TABLE source_entity_accounting (
                    entity_key TEXT PRIMARY KEY NOT NULL,
                    materialization_layer TEXT NOT NULL,
                    terminal_state TEXT NOT NULL,
                    terminal_reasons TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE source_conservation_ledger (
                    materialization_layer TEXT PRIMARY KEY NOT NULL,
                    entity_count INTEGER NOT NULL CHECK (entity_count >= 0)
                )"""
            )
            connection.executemany(
                "INSERT INTO source_entity_accounting VALUES (?, ?, ?, ?)",
                (
                    (
                        routed.entity.entity_key,
                        routed.layer_name,
                        routed.accounting.state.value,
                        _json(routed.accounting.reasons),
                    )
                    for routed in routed_entities
                ),
            )
            connection.executemany(
                "INSERT INTO source_conservation_ledger VALUES (?, ?)",
                (
                    (layer_name, int(layer_counts[layer_name]))
                    for layer_name in MATERIALIZATION_LAYERS
                ),
            )
    finally:
        connection.close()


def _validate_staged(
    path: Path,
    routed_entities: Sequence[_RoutedEntity],
    expected_counts: Mapping[str, int],
) -> None:
    expected_routes = {
        routed.entity.entity_key: routed.layer_name for routed in routed_entities
    }
    expected_states = {
        routed.entity.entity_key: routed.accounting.state.value
        for routed in routed_entities
    }
    connection = sqlite3.connect(path)
    try:
        normalize_geopackage_metadata(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(
                f"Source GeoPackage validation failed: integrity={integrity}"
            )

        contents = dict(
            connection.execute(
                "SELECT table_name, data_type FROM gpkg_contents ORDER BY table_name"
            )
        )
        expected_contents = {
            layer_name: (
                "attributes" if layer_name == "source_metadata" else "features"
            )
            for layer_name in MATERIALIZATION_LAYERS
        }
        if contents != expected_contents:
            raise RuntimeError(
                "Source GeoPackage validation failed: unexpected registered layers "
                f"{contents!r}"
            )

        actual_placements: Counter[str] = Counter()
        actual_routes: dict[str, str] = {}
        actual_counts: dict[str, int] = {}
        for layer_name in MATERIALIZATION_LAYERS:
            keys = [
                str(row[0])
                for row in connection.execute(
                    f'SELECT entity_key FROM "{layer_name}" ORDER BY entity_key'
                )
            ]
            actual_counts[layer_name] = len(keys)
            for entity_key in keys:
                actual_placements[entity_key] += 1
                actual_routes[entity_key] = layer_name

        accounting_rows = list(
            connection.execute(
                "SELECT entity_key, materialization_layer, terminal_state "
                "FROM source_entity_accounting ORDER BY entity_key"
            )
        )
        accounting_routes = {str(row[0]): str(row[1]) for row in accounting_rows}
        accounting_states = {str(row[0]): str(row[2]) for row in accounting_rows}
        ledger_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT materialization_layer, entity_count "
                "FROM source_conservation_ledger ORDER BY materialization_layer"
            )
        }
    finally:
        connection.close()

    expected_keys = set(expected_routes)
    if (
        actual_counts != dict(expected_counts)
        or ledger_counts != dict(expected_counts)
        or set(actual_placements) != expected_keys
        or any(actual_placements[key] != 1 for key in expected_keys)
        or actual_routes != expected_routes
        or accounting_routes != expected_routes
        or accounting_states != expected_states
        or len(accounting_rows) != len(routed_entities)
        or sum(ledger_counts.values()) != len(routed_entities)
    ):
        raise RuntimeError(
            "Source GeoPackage validation failed: conservation/accounting mismatch"
        )


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _logical_sha256(
    source_crs: str,
    routed_entities: Sequence[_RoutedEntity],
    layer_counts: Mapping[str, int],
) -> str:
    payload = {
        "schema_version": SOURCE_GPKG_SCHEMA_VERSION,
        "source_crs": str(source_crs),
        "layer_counts": dict(layer_counts),
        "entities": [_entity_values(routed) for routed in routed_entities],
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def write_source_gpkg(
    path: str | Path,
    entities: Iterable[SourceEntity],
    source_crs: str,
    legend_flag_map: dict[str, str] | None = None,
) -> SourceGeoPackageResult:
    """Atomically write a deterministic generic source GeoPackage."""
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    entity_values = tuple(entities)
    accounting = account_entities(entity_values)
    accounting_by_key = {record.entity_key: record for record in accounting}
    routed_entities = tuple(
        _route_entity(
            entity,
            accounting_by_key[entity.entity_key],
            legend_flag_map=legend_flag_map,
        )
        for entity in sorted(entity_values, key=lambda item: item.entity_key)
    )
    layer_counts = {
        layer_name: sum(routed.layer_name == layer_name for routed in routed_entities)
        for layer_name in MATERIALIZATION_LAYERS
    }
    spatial_reference = _source_srs(source_crs)
    logical_sha256 = _logical_sha256(source_crs, routed_entities, layer_counts)

    file_descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp.gpkg",
        dir=destination.parent,
    )
    os.close(file_descriptor)
    staged = Path(staged_name)
    staged.unlink()
    try:
        _write_staged(staged, routed_entities, spatial_reference)
        _write_reconciliation_tables(staged, routed_entities, layer_counts)
        _validate_staged(staged, routed_entities, layer_counts)
        byte_sha256 = _sha256_path(staged)
        os.replace(staged, destination)
        return SourceGeoPackageResult(
            path=destination,
            entity_count=len(routed_entities),
            layer_counts=layer_counts,
            logical_sha256=logical_sha256,
            byte_sha256=byte_sha256,
        )
    finally:
        staged.unlink(missing_ok=True)
        Path(f"{staged}-journal").unlink(missing_ok=True)
        Path(f"{staged}-shm").unlink(missing_ok=True)
        Path(f"{staged}-wal").unlink(missing_ok=True)
