"""Operator-only ground-control capture, diagnosis, and profile export.

This tool is deliberately outside the production conversion pipeline.  It
never edits or publishes business geometry.  Its three commands form a
reviewed hand-off boundary::

    prepare  -> editable QGIS capture GeoPackage
    diagnose -> draft model/residual JSON report only
    export   -> strict cad2gis-gcp-profile-v1 JSON

OpenStreetMap controls are accepted only when explicitly classified as
``relative_osm_reference``.  They remain labelled as relative visual
references in every exported control source and never become a claim of
survey-grade absolute accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from osgeo import ogr, osr

from apd_rules import set_traditional_axis_order
from cad2gis_v3.calibration import (
    GCPProfile,
    GroundControlPoint,
    RobustSettings,
    THEORETICAL_MINIMUM_CONTROLS,
    fit_calibration,
    fit_profile,
)
from cad2gis_v3.georef import DirectTransformer


CAPTURE_SCHEMA_VERSION = "cad2gis-gcp-capture-v1"
CAPTURE_LAYER = "gcp_controls"
SESSION_LAYER = "gcp_session"
DEFAULT_CANDIDATE_LAYERS = ("PTECH", "BOITE", "SITE")
REFERENCE_KINDS = {
    "surveyed_control",
    "authoritative_control",
    "relative_osm_reference",
}
RELATIVE_OSM_PREFIX = "RELATIVE_OSM_REFERENCE_ONLY"
RELATIVE_OSM_WARNING = (
    "OpenStreetMap is a relative visual reference only; it is not surveyed "
    "ground truth and must not be used to claim absolute positional accuracy."
)

_SAFE_LAYER = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OSM_SOURCE = re.compile(r"(?:\bOSM\b|OPEN\s*STREET\s*MAP)", re.IGNORECASE)

CAPTURE_FIELDS: tuple[tuple[str, int, int | None], ...] = (
    ("candidate_id", ogr.OFTString, 80),
    ("point_id", ogr.OFTString, 80),
    ("feature_class", ogr.OFTString, 32),
    ("source_entity_key", ogr.OFTString, 80),
    ("source_handle", ogr.OFTString, 32),
    ("source_layer", ogr.OFTString, 128),
    ("display_label", ogr.OFTString, 254),
    ("cad_x", ogr.OFTReal, None),
    ("cad_y", ogr.OFTReal, None),
    ("nominal_easting", ogr.OFTReal, None),
    ("nominal_northing", ogr.OFTReal, None),
    ("target_easting", ogr.OFTReal, None),
    ("target_northing", ogr.OFTReal, None),
    ("target_crs", ogr.OFTString, 64),
    ("role", ogr.OFTString, 16),
    ("control_source", ogr.OFTString, 254),
    ("reference_kind", ogr.OFTString, 40),
    ("accuracy_m", ogr.OFTReal, None),
    ("weight", ogr.OFTReal, None),
    ("enabled", ogr.OFTInteger, None),
    ("review_status", ogr.OFTString, 24),
    ("notes", ogr.OFTString, 254),
)


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _positive(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _read_json_object(path: str | Path, context: str) -> dict[str, Any]:
    resolved = Path(path).resolve()
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object: {resolved}")
    return value


def _write_json_atomic(
    path: str | Path,
    value: Mapping[str, Any],
    *,
    replace_existing: bool,
) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not replace_existing:
        raise FileExistsError(f"Refusing to overwrite existing file: {destination}")
    staging = destination.with_name(f".{destination.name}.staging")
    if staging.exists():
        staging.unlink()
    try:
        staging.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        os.replace(staging, destination)
    finally:
        if staging.exists():
            staging.unlink()
    return destination


def _artifact_hash(manifest: Mapping[str, Any], name: str) -> str:
    try:
        digest = str(manifest["artifacts"][name]["sha256"]).lower()
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Manifest lacks artifacts.{name}.sha256") from exc
    if not _SHA256.fullmatch(digest):
        raise ValueError(f"Manifest artifacts.{name}.sha256 is invalid")
    return digest


def _load_complete_manifest(
    manifest_path: str | Path,
    *,
    delivery_path: str | Path,
    evidence_path: str | Path,
) -> dict[str, Any]:
    resolved = Path(manifest_path).resolve()
    manifest = _read_json_object(resolved, "Run manifest")
    if manifest.get("schema_version") != "cad2gis-run-manifest-v3":
        raise ValueError("Unsupported or missing cad2gis v3 run manifest")
    if (manifest.get("publication") or {}).get("status") != "complete":
        raise ValueError("GCP capture requires a completely published source run")
    source = manifest.get("source")
    crs = manifest.get("crs")
    if not isinstance(source, Mapping) or not isinstance(crs, Mapping):
        raise ValueError("Manifest lacks source or CRS metadata")
    source_sha256 = str(source.get("sha256", "")).lower()
    if not _SHA256.fullmatch(source_sha256):
        raise ValueError("Manifest source SHA-256 is invalid")
    source_crs = str(crs.get("source_crs", "")).strip()
    target_crs = str(crs.get("target_crs", "")).strip()
    if not source_crs or not target_crs:
        raise ValueError("Manifest source_crs and target_crs are required")
    # Construction validates both CRS definitions before any capture is made.
    DirectTransformer(source_crs, target_crs)
    for name, path in (("delivery", delivery_path), ("evidence", evidence_path)):
        resolved_artifact = Path(path).resolve()
        if not resolved_artifact.is_file():
            raise FileNotFoundError(resolved_artifact)
        expected = _artifact_hash(manifest, name)
        actual = _sha256_file(resolved_artifact)
        if actual != expected:
            raise ValueError(
                f"{name} SHA-256 does not match the complete run manifest; "
                "refusing a mixed or stale capture bundle"
            )
    manifest["_resolved_path"] = str(resolved)
    manifest["_sha256"] = _sha256_file(resolved)
    return manifest


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _delivery_candidate_index(
    delivery_path: str | Path,
    layers: Sequence[str],
) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    with sqlite3.connect(Path(delivery_path)) as connection:
        geometry_types = {
            str(row[0]): str(row[1]).upper()
            for row in connection.execute(
                "SELECT table_name, geometry_type_name FROM gpkg_geometry_columns"
            )
        }
        for layer in layers:
            if not _SAFE_LAYER.fullmatch(layer):
                raise ValueError(f"Unsafe or invalid candidate layer name: {layer!r}")
            if geometry_types.get(layer) != "POINT":
                raise ValueError(f"Candidate layer {layer} is not a GeoPackage POINT layer")
            columns = _table_columns(connection, layer)
            required = {"source_entity_key", "source_handle"}
            if not required.issubset(columns):
                raise ValueError(f"Delivery layer {layer} lacks source lineage fields")
            label_expression = (
                "COALESCE(display_label, CODE, '')"
                if {"display_label", "CODE"}.issubset(columns)
                else "COALESCE(display_label, '')"
                if "display_label" in columns
                else "COALESCE(CODE, '')"
                if "CODE" in columns
                else "''"
            )
            rows = connection.execute(
                f'SELECT source_entity_key, source_handle, {label_expression} '
                f'FROM "{layer}" ORDER BY source_entity_key'
            )
            for entity_key, handle, label in rows:
                key = str(entity_key or "").strip()
                if not key:
                    raise ValueError(f"Delivery layer {layer} contains an empty source_entity_key")
                if key in index:
                    raise ValueError(f"Duplicate delivery source_entity_key: {key}")
                index[key] = {
                    "feature_class": layer,
                    "source_handle": str(handle or ""),
                    "display_label": str(label or ""),
                }
    return index


def _evidence_candidates(
    evidence_path: str | Path,
    layers: Sequence[str],
    delivery_index: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in layers)
    query = f"""
        SELECT f.feature_key, f.feature_class, f.source_entity_key,
               f.source_handle, f.source_layer, f.display_label, c.native_points
          FROM feature_candidates AS f
          JOIN cad_entities AS c ON c.entity_key = f.source_entity_key
         WHERE f.geometry_kind = 'Point'
           AND f.feature_class IN ({placeholders})
         ORDER BY f.feature_class, f.feature_key
    """
    candidates: list[dict[str, Any]] = []
    seen_entities: set[str] = set()
    with sqlite3.connect(Path(evidence_path)) as connection:
        for row in connection.execute(query, tuple(layers)):
            (
                feature_key,
                feature_class,
                source_entity_key,
                source_handle,
                source_layer,
                evidence_label,
                native_points_json,
            ) = row
            entity_key = str(source_entity_key or "").strip()
            delivered = delivery_index.get(entity_key)
            if delivered is None:
                continue
            if str(feature_class) != delivered["feature_class"]:
                raise ValueError(f"Delivery/evidence class mismatch for {entity_key}")
            try:
                native_points = json.loads(str(native_points_json))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid native_points for {entity_key}") from exc
            if not isinstance(native_points, list) or len(native_points) != 1:
                raise ValueError(
                    f"Point candidate {entity_key} must have exactly one immutable CAD point"
                )
            point = native_points[0]
            if not isinstance(point, list) or len(point) < 2:
                raise ValueError(f"Invalid CAD point for {entity_key}")
            candidate_id = str(feature_key or "").strip()
            if not candidate_id:
                raise ValueError(f"Empty feature_key for {entity_key}")
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "point_id": f"GCP-{feature_class}-{source_handle}",
                    "feature_class": str(feature_class),
                    "source_entity_key": entity_key,
                    "source_handle": str(source_handle or delivered["source_handle"]),
                    "source_layer": str(source_layer or ""),
                    "display_label": str(delivered["display_label"] or evidence_label or ""),
                    "cad_x": _finite(point[0], f"{entity_key} cad_x"),
                    "cad_y": _finite(point[1], f"{entity_key} cad_y"),
                }
            )
            seen_entities.add(entity_key)
    missing = sorted(set(delivery_index) - seen_entities)
    if missing:
        raise ValueError(
            f"Evidence lacks immutable CAD point lineage for {len(missing)} delivered candidates"
        )
    if not candidates:
        raise ValueError("No point candidates were found for the requested layers")
    return candidates


def _spatial_reference(crs: str) -> osr.SpatialReference:
    reference = osr.SpatialReference()
    if reference.SetFromUserInput(crs) != 0:
        raise ValueError(f"Invalid target CRS: {crs}")
    set_traditional_axis_order(reference, osr)
    return reference


def _create_field(layer: ogr.Layer, name: str, field_type: int, width: int | None) -> None:
    definition = ogr.FieldDefn(name, field_type)
    if width is not None:
        definition.SetWidth(width)
    if layer.CreateField(definition) != ogr.OGRERR_NONE:
        raise RuntimeError(f"Could not create capture field {name}")


def _write_capture_gpkg(
    output_path: Path,
    candidates: Sequence[Mapping[str, Any]],
    transformer: DirectTransformer,
    metadata: Mapping[str, Any],
) -> None:
    driver = ogr.GetDriverByName("GPKG")
    if driver is None:
        raise RuntimeError("GDAL GeoPackage driver is unavailable")
    dataset = driver.CreateDataSource(str(output_path))
    if dataset is None:
        raise RuntimeError(f"Could not create GCP capture: {output_path}")
    try:
        layer = dataset.CreateLayer(
            CAPTURE_LAYER,
            srs=_spatial_reference(transformer.target_crs),
            geom_type=ogr.wkbPoint,
            options=["SPATIAL_INDEX=YES"],
        )
        if layer is None:
            raise RuntimeError("Could not create gcp_controls layer")
        for name, field_type, width in CAPTURE_FIELDS:
            _create_field(layer, name, field_type, width)
        layer.StartTransaction()
        try:
            definition = layer.GetLayerDefn()
            for candidate in candidates:
                cad_point = (candidate["cad_x"], candidate["cad_y"])
                nominal = transformer.point(cad_point)
                feature = ogr.Feature(definition)
                for name in (
                    "candidate_id",
                    "point_id",
                    "feature_class",
                    "source_entity_key",
                    "source_handle",
                    "source_layer",
                    "display_label",
                    "cad_x",
                    "cad_y",
                ):
                    feature.SetField(name, candidate[name])
                feature.SetField("nominal_easting", nominal[0])
                feature.SetField("nominal_northing", nominal[1])
                feature.SetField("target_crs", transformer.target_crs)
                feature.SetField("enabled", 0)
                feature.SetField("review_status", "candidate")
                geometry = ogr.Geometry(ogr.wkbPoint)
                geometry.AddPoint_2D(*nominal)
                feature.SetGeometry(geometry)
                if layer.CreateFeature(feature) != ogr.OGRERR_NONE:
                    raise RuntimeError(f"Could not write candidate {candidate['candidate_id']}")
            layer.CommitTransaction()
        except Exception:
            layer.RollbackTransaction()
            raise

        session = dataset.CreateLayer(SESSION_LAYER, geom_type=ogr.wkbNone)
        if session is None:
            raise RuntimeError("Could not create gcp_session layer")
        _create_field(session, "key", ogr.OFTString, 80)
        _create_field(session, "value", ogr.OFTString, 0)
        session_definition = session.GetLayerDefn()
        for key in sorted(metadata):
            feature = ogr.Feature(session_definition)
            feature.SetField("key", str(key))
            raw_value = metadata[key]
            value = raw_value if isinstance(raw_value, str) else json.dumps(
                raw_value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            feature.SetField("value", value)
            if session.CreateFeature(feature) != ogr.OGRERR_NONE:
                raise RuntimeError(f"Could not write capture metadata {key}")
    finally:
        dataset = None


def prepare_capture(
    *,
    delivery_path: str | Path,
    evidence_path: str | Path,
    manifest_path: str | Path,
    output_path: str | Path,
    candidate_layers: Sequence[str] = DEFAULT_CANDIDATE_LAYERS,
    force: bool = False,
) -> dict[str, Any]:
    """Create an editable, non-authoritative GCP candidate GeoPackage."""

    layers = tuple(dict.fromkeys(str(layer).strip().upper() for layer in candidate_layers))
    if not layers:
        raise ValueError("At least one candidate point layer is required")
    manifest = _load_complete_manifest(
        manifest_path,
        delivery_path=delivery_path,
        evidence_path=evidence_path,
    )
    delivery_index = _delivery_candidate_index(delivery_path, layers)
    candidates = _evidence_candidates(evidence_path, layers, delivery_index)
    source_crs = str(manifest["crs"]["source_crs"])
    target_crs = str(manifest["crs"]["target_crs"])
    transformer = DirectTransformer(source_crs, target_crs)
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing capture: {destination}")
    staging = destination.with_name(f".{destination.name}.staging.gpkg")
    if staging.exists():
        staging.unlink()
    metadata = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "manifest_path": str(Path(manifest_path).resolve()),
        "manifest_sha256": manifest["_sha256"],
        "source_sha256": str(manifest["source"]["sha256"]),
        "source_crs": source_crs,
        "target_crs": target_crs,
        "delivery_path": str(Path(delivery_path).resolve()),
        "delivery_sha256": _artifact_hash(manifest, "delivery"),
        "evidence_path": str(Path(evidence_path).resolve()),
        "evidence_sha256": _artifact_hash(manifest, "evidence"),
        "candidate_layers": list(layers),
        "candidate_count": len(candidates),
        "geometry_semantics": (
            "Candidate geometry is the nominal target-grid position. Enter reviewed "
            "target_easting/target_northing explicitly; geometry alone is not a control."
        ),
        "relative_osm_warning": RELATIVE_OSM_WARNING,
    }
    try:
        _write_capture_gpkg(staging, candidates, transformer, metadata)
        connection = sqlite3.connect(staging)
        try:
            connection.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS "ux_{CAPTURE_LAYER}_candidate" '
                f'ON "{CAPTURE_LAYER}" (candidate_id)'
            )
            connection.execute(
                f'CREATE UNIQUE INDEX IF NOT EXISTS "ux_{SESSION_LAYER}_key" '
                f'ON "{SESSION_LAYER}" (key)'
            )
            connection.commit()
        finally:
            connection.close()
        os.replace(staging, destination)
    finally:
        if staging.exists():
            staging.unlink()
    return {
        "capture": str(destination),
        "capture_sha256": _sha256_file(destination),
        "candidate_count": len(candidates),
        "candidate_layers": list(layers),
        "source_crs": source_crs,
        "target_crs": target_crs,
        "publication_changed": False,
    }


def _field_value(feature: ogr.Feature, name: str) -> Any:
    index = feature.GetFieldIndex(name)
    if index < 0 or not feature.IsFieldSetAndNotNull(index):
        return None
    return feature.GetField(index)


def _read_capture(path: str | Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    resolved = Path(path).resolve()
    dataset = ogr.Open(str(resolved), 0)
    if dataset is None:
        raise ValueError(f"Could not open GCP capture: {resolved}")
    try:
        session_layer = dataset.GetLayerByName(SESSION_LAYER)
        control_layer = dataset.GetLayerByName(CAPTURE_LAYER)
        if session_layer is None or control_layer is None:
            raise ValueError("Capture lacks gcp_session or gcp_controls")
        session: dict[str, str] = {}
        for feature in session_layer:
            key = str(_field_value(feature, "key") or "")
            if not key or key in session:
                raise ValueError("Capture session metadata contains an empty or duplicate key")
            session[key] = str(_field_value(feature, "value") or "")
        if session.get("schema_version") != CAPTURE_SCHEMA_VERSION:
            raise ValueError("Unsupported GCP capture schema")
        required_fields = {item[0] for item in CAPTURE_FIELDS}
        definition = control_layer.GetLayerDefn()
        available_fields = {
            definition.GetFieldDefn(index).GetName()
            for index in range(definition.GetFieldCount())
        }
        missing = required_fields - available_fields
        if missing:
            raise ValueError(f"Capture fields are incomplete: {sorted(missing)}")
        records: list[dict[str, Any]] = []
        for feature in control_layer:
            record = {name: _field_value(feature, name) for name in required_fields}
            geometry = feature.GetGeometryRef()
            if geometry is not None and not geometry.IsEmpty():
                record["geometry_easting"] = float(geometry.GetX())
                record["geometry_northing"] = float(geometry.GetY())
            else:
                record["geometry_easting"] = None
                record["geometry_northing"] = None
            records.append(record)
    finally:
        dataset = None
    required_session = {
        "source_sha256",
        "source_crs",
        "target_crs",
        "manifest_path",
        "manifest_sha256",
        "delivery_path",
        "delivery_sha256",
        "evidence_path",
        "evidence_sha256",
        "candidate_layers",
    }
    missing_session = required_session - set(session)
    if missing_session:
        raise ValueError(f"Capture session metadata is incomplete: {sorted(missing_session)}")
    if not _SHA256.fullmatch(session["source_sha256"].lower()):
        raise ValueError("Capture source_sha256 is invalid")
    manifest = _load_complete_manifest(
        session["manifest_path"],
        delivery_path=session["delivery_path"],
        evidence_path=session["evidence_path"],
    )
    binding_checks = {
        "manifest_sha256": manifest["_sha256"],
        "source_sha256": str(manifest["source"]["sha256"]),
        "source_crs": str(manifest["crs"]["source_crs"]),
        "target_crs": str(manifest["crs"]["target_crs"]),
        "delivery_sha256": _artifact_hash(manifest, "delivery"),
        "evidence_sha256": _artifact_hash(manifest, "evidence"),
    }
    mismatches = sorted(
        key for key, expected in binding_checks.items() if session.get(key) != expected
    )
    if mismatches:
        raise ValueError(
            "Capture binding differs from its immutable source run: " + ", ".join(mismatches)
        )
    _validate_immutable_capture_candidates(session, records)
    return session, records


def _validate_immutable_capture_candidates(
    session: Mapping[str, str], records: Sequence[Mapping[str, Any]]
) -> None:
    """Reject edits to source lineage while allowing only review/target fields."""

    try:
        layers_value = json.loads(session["candidate_layers"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Capture candidate_layers metadata is invalid") from exc
    if not isinstance(layers_value, list) or not all(
        isinstance(layer, str) for layer in layers_value
    ):
        raise ValueError("Capture candidate_layers metadata must be a string array")
    layers = tuple(layers_value)
    delivery_index = _delivery_candidate_index(session["delivery_path"], layers)
    expected_rows = _evidence_candidates(
        session["evidence_path"], layers, delivery_index
    )
    expected_by_id = {row["candidate_id"]: row for row in expected_rows}
    captured_ids = [str(record.get("candidate_id") or "") for record in records]
    if len(captured_ids) != len(set(captured_ids)):
        raise ValueError("Capture contains duplicate candidate_id values")
    if set(captured_ids) != set(expected_by_id):
        raise ValueError("Capture candidate census differs from its immutable source run")
    transformer = DirectTransformer(session["source_crs"], session["target_crs"])
    for record in records:
        candidate_id = str(record["candidate_id"])
        expected = expected_by_id[candidate_id]
        for field in (
            "feature_class",
            "source_entity_key",
            "source_handle",
            "source_layer",
        ):
            if str(record.get(field) or "") != str(expected[field]):
                raise ValueError(f"Candidate {candidate_id}: immutable {field} was edited")
        for field in ("cad_x", "cad_y"):
            if not math.isclose(
                _finite(record.get(field), f"Candidate {candidate_id} {field}"),
                float(expected[field]),
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                raise ValueError(f"Candidate {candidate_id}: immutable {field} was edited")
        nominal = transformer.point((expected["cad_x"], expected["cad_y"]))
        for field, expected_value in zip(
            ("nominal_easting", "nominal_northing"), nominal
        ):
            if not math.isclose(
                _finite(record.get(field), f"Candidate {candidate_id} {field}"),
                expected_value,
                rel_tol=0.0,
                abs_tol=1e-8,
            ):
                raise ValueError(f"Candidate {candidate_id}: immutable {field} was edited")
        if str(record.get("target_crs") or "") != session["target_crs"]:
            raise ValueError(f"Candidate {candidate_id}: immutable target_crs was edited")


def _normalized_control_source(reference_kind: str, source: str, point_id: str) -> str:
    text = source.strip()
    if not text:
        raise ValueError(f"Control {point_id}: control_source is required")
    mentions_osm = _OSM_SOURCE.search(text) is not None
    if reference_kind == "relative_osm_reference":
        if not mentions_osm:
            raise ValueError(
                f"Control {point_id}: relative_osm_reference source must identify "
                "OpenStreetMap/OSM and its snapshot provenance"
            )
        if text.upper().startswith(RELATIVE_OSM_PREFIX):
            return text
        return f"{RELATIVE_OSM_PREFIX} | {text}"
    if mentions_osm:
        raise ValueError(
            f"Control {point_id}: OpenStreetMap/OSM must be classified as "
            "relative_osm_reference"
        )
    prefix = (
        "SURVEYED_CONTROL"
        if reference_kind == "surveyed_control"
        else "AUTHORITATIVE_CONTROL"
    )
    if text.upper().startswith(prefix):
        return text
    return f"{prefix} | {text}"


def _reviewed_controls(
    session: Mapping[str, str],
    records: Sequence[Mapping[str, Any]],
) -> tuple[tuple[GroundControlPoint, ...], dict[str, str], list[str]]:
    controls: list[GroundControlPoint] = []
    reference_by_id: dict[str, str] = {}
    excluded_candidates: list[str] = []
    for record in records:
        candidate_id = str(record.get("candidate_id") or "").strip()
        status = str(record.get("review_status") or "candidate").strip().lower()
        enabled_raw = record.get("enabled")
        if status not in {"candidate", "accepted", "rejected"}:
            raise ValueError(
                f"Candidate {candidate_id}: review_status must be candidate, accepted, or rejected"
            )
        if enabled_raw not in {None, 0, 1}:
            raise ValueError(f"Candidate {candidate_id}: enabled must be 0 or 1")
        enabled = enabled_raw == 1
        if enabled and status != "accepted":
            raise ValueError(
                f"Candidate {candidate_id}: enabled controls must have review_status=accepted"
            )
        if status != "accepted":
            excluded_candidates.append(candidate_id)
            continue
        point_id = str(record.get("point_id") or "").strip()
        if not point_id:
            raise ValueError(f"Candidate {candidate_id}: point_id is required")
        role = str(record.get("role") or "").strip().lower()
        reference_kind = str(record.get("reference_kind") or "").strip().lower()
        if reference_kind not in REFERENCE_KINDS:
            raise ValueError(
                f"Control {point_id}: reference_kind must be one of "
                f"{sorted(REFERENCE_KINDS)}"
            )
        source = _normalized_control_source(
            reference_kind,
            str(record.get("control_source") or ""),
            point_id,
        )
        record_target_crs = str(record.get("target_crs") or "").strip()
        if record_target_crs != session["target_crs"]:
            raise ValueError(f"Control {point_id}: target_crs differs from capture session")
        control = GroundControlPoint(
            point_id=point_id,
            cad_point=(
                _finite(record.get("cad_x"), f"Control {point_id} cad_x"),
                _finite(record.get("cad_y"), f"Control {point_id} cad_y"),
            ),
            target_point=(
                _finite(record.get("target_easting"), f"Control {point_id} target_easting"),
                _finite(record.get("target_northing"), f"Control {point_id} target_northing"),
            ),
            target_crs=session["target_crs"],
            role=role,
            source=source,
            accuracy_m=_positive(record.get("accuracy_m"), f"Control {point_id} accuracy_m"),
            weight=_positive(record.get("weight"), f"Control {point_id} weight"),
            enabled=enabled,
        )
        controls.append(control)
        reference_by_id[point_id] = reference_kind
    point_ids = [control.point_id for control in controls]
    if len(point_ids) != len(set(point_ids)):
        raise ValueError("Accepted capture controls contain duplicate point_id values")
    if not controls:
        raise ValueError("Capture has no accepted controls")
    return tuple(controls), reference_by_id, excluded_candidates


def _reference_scope(reference_kinds: Iterable[str]) -> str:
    kinds = tuple(reference_kinds)
    relative = sum(kind == "relative_osm_reference" for kind in kinds)
    if relative == len(kinds) and kinds:
        return "relative_to_osm_snapshot_only_not_absolute_ground_truth"
    if relative:
        return "mixed_controls_include_relative_osm_not_absolute_ground_truth"
    return "survey_or_authoritative_as_declared_by_operator_not_independently_verified"


def _control_set_sha256(
    controls: Sequence[GroundControlPoint], reference_by_id: Mapping[str, str]
) -> str:
    value = []
    for control in sorted(controls, key=lambda item: item.point_id):
        row = _control_mapping(control)
        row["reference_kind"] = reference_by_id[control.point_id]
        value.append(row)
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _bbox(points: Sequence[tuple[float, float]]) -> dict[str, float] | None:
    if not points:
        return None
    return {
        "min_easting": min(point[0] for point in points),
        "min_northing": min(point[1] for point in points),
        "max_easting": max(point[0] for point in points),
        "max_northing": max(point[1] for point in points),
    }


def _bbox_ratio(
    drawing: Mapping[str, float] | None,
    controls: Mapping[str, float] | None,
    axis: str,
) -> float | None:
    if drawing is None or controls is None:
        return None
    low = f"min_{axis}"
    high = f"max_{axis}"
    denominator = drawing[high] - drawing[low]
    if denominator <= 0.0:
        return None
    return (controls[high] - controls[low]) / denominator


def _convex_hull(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted(set(points))
    if len(ordered) <= 1:
        return ordered

    def cross(origin, left, right):
        return (
            (left[0] - origin[0]) * (right[1] - origin[1])
            - (left[1] - origin[1]) * (right[0] - origin[0])
        )

    lower: list[tuple[float, float]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _polygon_area(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    return abs(
        math.fsum(
            start[0] * end[1] - end[0] * start[1]
            for start, end in zip(points, points[1:] + points[:1])
        )
    ) / 2.0


def _baseline_metrics(points: Sequence[tuple[float, float]]) -> tuple[float | None, float | None]:
    distances = [
        math.dist(left, right)
        for index, left in enumerate(points)
        for right in points[index + 1 :]
    ]
    return (min(distances), max(distances)) if distances else (None, None)


def _coverage_diagnostics(
    records: Sequence[Mapping[str, Any]],
    controls: Sequence[GroundControlPoint],
    transformer: DirectTransformer,
) -> dict[str, Any]:
    drawing_points = [
        (
            _finite(record.get("nominal_easting"), "candidate nominal_easting"),
            _finite(record.get("nominal_northing"), "candidate nominal_northing"),
        )
        for record in records
    ]
    train_points = [
        transformer.point(control.cad_point)
        for control in controls
        if control.enabled and control.role == "train"
    ]
    check_points = [
        transformer.point(control.cad_point)
        for control in controls
        if control.enabled and control.role == "check"
    ]
    drawing_bbox = _bbox(drawing_points)
    train_bbox = _bbox(train_points)
    check_bbox = _bbox(check_points)
    hull = _convex_hull(train_points)
    hull_area = _polygon_area(hull)
    drawing_bbox_area = 0.0
    if drawing_bbox is not None:
        drawing_bbox_area = (
            (drawing_bbox["max_easting"] - drawing_bbox["min_easting"])
            * (drawing_bbox["max_northing"] - drawing_bbox["min_northing"])
        )
    minimum_baseline, maximum_baseline = _baseline_metrics(train_points)
    farthest = None
    if drawing_points and train_points:
        farthest = max(
            min(math.dist(candidate, training) for training in train_points)
            for candidate in drawing_points
        )
    warnings: list[str] = []
    if len(train_points) < 3:
        warnings.append("fewer than 3 active training controls")
    if len(check_points) < 2:
        warnings.append("fewer than 2 independent active check controls")
    if len(hull) < 3 or hull_area <= 0.0:
        warnings.append("training controls are collinear or lack two-dimensional coverage")
    return {
        "drawing_candidate_count": len(drawing_points),
        "training_control_count": len(train_points),
        "check_control_count": len(check_points),
        "drawing_bbox": drawing_bbox,
        "training_bbox": train_bbox,
        "check_bbox": check_bbox,
        "training_extent_coverage_x_ratio": _bbox_ratio(drawing_bbox, train_bbox, "easting"),
        "training_extent_coverage_y_ratio": _bbox_ratio(drawing_bbox, train_bbox, "northing"),
        "training_convex_hull_area_m2": hull_area,
        "drawing_bbox_area_m2": drawing_bbox_area,
        "training_hull_to_drawing_bbox_area_ratio": (
            None if drawing_bbox_area <= 0.0 else hull_area / drawing_bbox_area
        ),
        "training_min_baseline_m": minimum_baseline,
        "training_max_baseline_m": maximum_baseline,
        "max_candidate_distance_to_training_m": farthest,
        "warnings": warnings,
    }


def _raw_offsets(
    controls: Sequence[GroundControlPoint], transformer: DirectTransformer
) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    rows: list[dict[str, Any]] = []
    enabled = [control for control in controls if control.enabled]
    for control in enabled:
        nominal = transformer.point(control.cad_point)
        dx = control.target_point[0] - nominal[0]
        dy = control.target_point[1] - nominal[1]
        rows.append(
            {
                "point_id": control.point_id,
                "role": control.role,
                "nominal_easting": nominal[0],
                "nominal_northing": nominal[1],
                "target_easting": control.target_point[0],
                "target_northing": control.target_point[1],
                "delta_e_m": dx,
                "delta_n_m": dy,
                "distance_m": math.hypot(dx, dy),
                "bearing_clockwise_from_grid_north_deg": (
                    math.degrees(math.atan2(dx, dy)) % 360.0
                ),
            }
        )
    if not rows:
        return rows, {
            "mean_delta_e_m": None,
            "mean_delta_n_m": None,
            "max_deviation_from_mean_shift_m": None,
        }
    mean_dx = math.fsum(row["delta_e_m"] for row in rows) / len(rows)
    mean_dy = math.fsum(row["delta_n_m"] for row in rows) / len(rows)
    deviation = max(
        math.hypot(row["delta_e_m"] - mean_dx, row["delta_n_m"] - mean_dy)
        for row in rows
    )
    return rows, {
        "mean_delta_e_m": mean_dx,
        "mean_delta_n_m": mean_dy,
        "mean_shift_m": math.hypot(mean_dx, mean_dy),
        "max_deviation_from_mean_shift_m": deviation,
    }


def diagnose_capture(
    *,
    capture_path: str | Path,
    report_path: str | Path,
    robust_outlier_threshold_m: float | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fit draft candidates and write diagnostics without publishing geometry."""

    session, records = _read_capture(capture_path)
    controls, references, excluded = _reviewed_controls(session, records)
    transformer = DirectTransformer(session["source_crs"], session["target_crs"])
    robust = (
        RobustSettings(False, 1, None)
        if robust_outlier_threshold_m is None
        else RobustSettings(True, 512, _positive(
            robust_outlier_threshold_m, "robust_outlier_threshold_m"
        ))
    )
    train_count = sum(control.enabled and control.role == "train" for control in controls)
    check_count = sum(control.enabled and control.role == "check" for control in controls)
    candidate_reports: list[dict[str, Any]] = []
    for model in ("translation", "similarity", "affine"):
        minimum = THEORETICAL_MINIMUM_CONTROLS[model]
        if train_count < minimum:
            candidate_reports.append(
                {
                    "model": model,
                    "available": False,
                    "reason": f"requires at least {minimum} active training controls",
                    "result": None,
                }
            )
            continue
        try:
            result = fit_calibration(
                controls,
                transformer,
                model=model,
                robust=robust,
                expected_source_crs=session["source_crs"],
                expected_target_crs=session["target_crs"],
            )
        except ValueError as exc:
            candidate_reports.append(
                {"model": model, "available": False, "reason": str(exc), "result": None}
            )
            continue
        result_value = result.to_dict()
        parameters = result_value["parameters"]
        scale_ppm = None
        if model == "similarity" and "scale" in parameters:
            scale_ppm = (float(parameters["scale"]) - 1.0) * 1_000_000.0
        candidate_reports.append(
            {
                "model": model,
                "available": True,
                "reason": None,
                "derived": {
                    "pivot_shift_m": math.hypot(
                        float(parameters["pivot_shift_e_m"]),
                        float(parameters["pivot_shift_n_m"]),
                    ),
                    "scale_deviation_ppm": scale_ppm,
                },
                "result": result_value,
            }
        )
    rankable = [
        item
        for item in candidate_reports
        if item["available"]
        and item["result"]["check_metrics"]["rmse_m"] is not None
    ]
    ranked = sorted(
        (
            {
                "model": item["model"],
                "check_rmse_m": item["result"]["check_metrics"]["rmse_m"],
            }
            for item in rankable
        ),
        key=lambda item: (
            item["check_rmse_m"],
            ("translation", "similarity", "affine").index(item["model"]),
        ),
    )
    offsets, offset_summary = _raw_offsets(controls, transformer)
    active_reference_kinds = [
        references[control.point_id] for control in controls if control.enabled
    ]
    report = {
        "schema_version": "cad2gis-gcp-diagnostic-report-v1",
        "diagnostic_only": True,
        "publication_changed": False,
        "selection_status": (
            "diagnostic_ranking_only; no model is authorized or published; "
            "export --enable must pass reviewed profile gates"
        ),
        "capture": {
            "path": str(Path(capture_path).resolve()),
            "sha256": _sha256_file(capture_path),
            "source_sha256": session["source_sha256"],
            "manifest_sha256": session["manifest_sha256"],
            "delivery_sha256": session["delivery_sha256"],
            "evidence_sha256": session["evidence_sha256"],
            "source_crs": session["source_crs"],
            "target_crs": session["target_crs"],
        },
        "controls": {
            "accepted_count": len(controls),
            "active_count": sum(control.enabled for control in controls),
            "active_train_count": train_count,
            "active_check_count": check_count,
            "control_set_sha256": _control_set_sha256(controls, references),
            "unreviewed_or_rejected_candidate_count": len(excluded),
            "reference_scope": _reference_scope(active_reference_kinds),
            "relative_osm_warning": (
                RELATIVE_OSM_WARNING
                if "relative_osm_reference" in active_reference_kinds
                else None
            ),
        },
        "raw_nominal_offsets": offsets,
        "raw_offset_summary": offset_summary,
        "spatial_coverage": _coverage_diagnostics(records, controls, transformer),
        "candidate_models": candidate_reports,
        "diagnostic_rank_by_check_rmse": ranked,
    }
    destination = _write_json_atomic(report_path, report, replace_existing=force)
    return {
        "report": str(destination),
        "report_sha256": _sha256_file(destination),
        "active_train_count": train_count,
        "active_check_count": check_count,
        "available_models": [item["model"] for item in candidate_reports if item["available"]],
        "diagnostic_only": True,
        "publication_changed": False,
        "reference_scope": _reference_scope(active_reference_kinds),
    }


def _control_mapping(control: GroundControlPoint) -> dict[str, Any]:
    return {
        "point_id": control.point_id,
        "cad_x": control.cad_point[0],
        "cad_y": control.cad_point[1],
        "target_easting": control.target_point[0],
        "target_northing": control.target_point[1],
        "target_crs": control.target_crs,
        "role": control.role,
        "source": control.source,
        "accuracy_m": control.accuracy_m,
        "weight": control.weight,
        "enabled": control.enabled,
    }


def _validate_diagnostic_report(
    report_path: str | Path,
    *,
    session: Mapping[str, str],
    controls: Sequence[GroundControlPoint],
    references: Mapping[str, str],
) -> str:
    report = _read_json_object(report_path, "GCP diagnostic report")
    if report.get("schema_version") != "cad2gis-gcp-diagnostic-report-v1":
        raise ValueError("Enabled export requires a cad2gis v1 diagnostic report")
    if report.get("diagnostic_only") is not True:
        raise ValueError("Diagnostic report must retain diagnostic_only=true")
    capture = report.get("capture")
    if not isinstance(capture, Mapping):
        raise ValueError("Diagnostic report lacks capture binding")
    expected = {
        "source_sha256": session["source_sha256"],
        "manifest_sha256": session["manifest_sha256"],
        "delivery_sha256": session["delivery_sha256"],
        "evidence_sha256": session["evidence_sha256"],
        "source_crs": session["source_crs"],
        "target_crs": session["target_crs"],
    }
    mismatches = sorted(
        name for name, expected_value in expected.items()
        if capture.get(name) != expected_value
    )
    if mismatches:
        raise ValueError(
            "Diagnostic report is stale or bound to another capture: "
            + ", ".join(mismatches)
        )
    report_controls = report.get("controls")
    if not isinstance(report_controls, Mapping) or report_controls.get(
        "control_set_sha256"
    ) != _control_set_sha256(controls, references):
        raise ValueError(
            "Diagnostic report is stale: accepted controls or frozen train/check roles changed"
        )
    return _sha256_file(report_path)


def export_profile(
    *,
    capture_path: str | Path,
    template_profile_path: str | Path,
    output_path: str | Path,
    diagnostic_report_path: str | Path | None = None,
    enable: bool = False,
    requested_model: str | None = None,
    spatial_review_source: str | None = None,
    max_check_error_m: float | None = None,
    max_pivot_shift_m: float | None = None,
    max_abs_rotation_deg: float | None = None,
    max_scale_deviation_ratio: float | None = None,
    max_affine_condition_number: float | None = None,
    robust_outlier_threshold_m: float | None = None,
    disable_robust: bool = False,
    affine_min_improvement_ratio: float | None = None,
    affine_structure_reviewed: bool = False,
    allow_relative_osm: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Export a strict profile; activation additionally requires all gates to pass."""

    session, records = _read_capture(capture_path)
    controls, references, _ = _reviewed_controls(session, records)
    template_path = Path(template_profile_path).resolve()
    GCPProfile.load(template_path, expected_source_sha256=session["source_sha256"])
    value = _read_json_object(template_path, "GCP profile template")
    if value.get("source_crs") != session["source_crs"]:
        raise ValueError("Template source_crs differs from the capture")
    if value.get("target_crs") != session["target_crs"]:
        raise ValueError("Template target_crs differs from the capture")
    active_relative = any(
        control.enabled and references[control.point_id] == "relative_osm_reference"
        for control in controls
    )
    if enable and active_relative and not allow_relative_osm:
        raise ValueError(
            "Enabled export contains relative OSM references; pass --allow-relative-osm "
            "to acknowledge that this is visual alignment, not absolute ground truth"
        )
    diagnostic_report_sha256 = None
    if enable:
        if diagnostic_report_path is None:
            raise ValueError(
                "Enabled export requires --diagnostic-report to freeze train/check roles "
                "before activation"
            )
        diagnostic_report_sha256 = _validate_diagnostic_report(
            diagnostic_report_path,
            session=session,
            controls=controls,
            references=references,
        )
    value["enabled"] = bool(enable)
    value["controls"] = [_control_mapping(control) for control in controls]
    if requested_model is not None:
        value["requested_model"] = str(requested_model).lower()
    if spatial_review_source is not None:
        source = str(spatial_review_source).strip()
        if not source:
            raise ValueError("spatial_review_source must not be empty")
        value["validation"]["spatial_distribution_reviewed"] = True
        value["validation"]["spatial_distribution_review_source"] = source
    if max_check_error_m is not None:
        threshold = _positive(max_check_error_m, "max_check_error_m")
        value["validation"]["max_check_rmse_m"] = threshold
        value["validation"]["max_check_p95_m"] = threshold
        value["validation"]["max_check_error_m"] = threshold
    limit_overrides = {
        "max_pivot_shift_m": max_pivot_shift_m,
        "max_abs_rotation_deg": max_abs_rotation_deg,
        "max_scale_deviation_ratio": max_scale_deviation_ratio,
        "max_affine_condition_number": max_affine_condition_number,
    }
    for name, raw_value in limit_overrides.items():
        if raw_value is not None:
            value["transform_limits"][name] = _positive(raw_value, name)
    if disable_robust and robust_outlier_threshold_m is not None:
        raise ValueError("disable_robust and robust_outlier_threshold_m are mutually exclusive")
    if disable_robust:
        value["robust"]["enabled"] = False
        value["robust"]["outlier_threshold_m"] = None
    elif robust_outlier_threshold_m is not None:
        value["robust"]["enabled"] = True
        value["robust"]["outlier_threshold_m"] = _positive(
            robust_outlier_threshold_m, "robust_outlier_threshold_m"
        )
    if affine_min_improvement_ratio is not None:
        ratio = _finite(affine_min_improvement_ratio, "affine_min_improvement_ratio")
        if not 0.0 <= ratio < 1.0:
            raise ValueError("affine_min_improvement_ratio must be in [0, 1)")
        value["validation"]["affine_min_improvement_ratio"] = ratio
    if affine_structure_reviewed:
        value["model_selection"]["affine_gate"]["spatial_structure_reviewed"] = True

    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing profile: {destination}")
    staging = destination.with_name(f".{destination.name}.staging")
    if staging.exists():
        staging.unlink()
    calibration_result = None
    try:
        staging.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        profile = GCPProfile.load(staging, expected_source_sha256=session["source_sha256"])
        if enable:
            transformer = DirectTransformer(profile.source_crs, profile.target_crs)
            calibration_result = fit_profile(profile, transformer)
            if calibration_result.validation_passed is not True:
                raise ValueError(
                    "Enabled profile failed reviewed calibration gates: "
                    + "; ".join(calibration_result.validation_failures)
                )
        os.replace(staging, destination)
    finally:
        if staging.exists():
            staging.unlink()
    active_references = [
        references[control.point_id] for control in controls if control.enabled
    ]
    return {
        "profile": str(destination),
        "profile_sha256": _sha256_file(destination),
        "enabled": enable,
        "selected_model": (
            None if calibration_result is None else calibration_result.selected_model
        ),
        "validation_passed": (
            None if calibration_result is None else calibration_result.validation_passed
        ),
        "active_control_count": sum(control.enabled for control in controls),
        "reference_scope": _reference_scope(active_references),
        "relative_osm_warning": RELATIVE_OSM_WARNING if active_relative else None,
        "diagnostic_report_sha256": diagnostic_report_sha256,
        "publication_changed": False,
    }


def _layers(value: str) -> tuple[str, ...]:
    result = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    if not result:
        raise argparse.ArgumentTypeError("At least one comma-separated layer is required")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operator-only deterministic GCP capture/diagnosis/profile tool",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="build an editable QGIS GCP capture")
    prepare.add_argument("--delivery", required=True, type=Path)
    prepare.add_argument("--evidence", required=True, type=Path)
    prepare.add_argument("--manifest", required=True, type=Path)
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument(
        "--layers", type=_layers, default=DEFAULT_CANDIDATE_LAYERS,
        help="comma-separated delivered point layers (default: PTECH,BOITE,SITE)",
    )
    prepare.add_argument("--force", action="store_true")

    diagnose = commands.add_parser(
        "diagnose", help="fit draft candidates and write a report; never publish geometry"
    )
    diagnose.add_argument("--capture", required=True, type=Path)
    diagnose.add_argument("--report", required=True, type=Path)
    diagnose.add_argument("--robust-outlier-threshold-m", type=float)
    diagnose.add_argument("--force", action="store_true")

    export = commands.add_parser("export", help="export a strict reviewed GCP profile")
    export.add_argument("--capture", required=True, type=Path)
    export.add_argument("--template-profile", required=True, type=Path)
    export.add_argument("--diagnostic-report", type=Path)
    export.add_argument("--out", required=True, type=Path)
    export.add_argument("--enable", action="store_true")
    export.add_argument(
        "--requested-model", choices=("auto", "translation", "similarity", "affine")
    )
    export.add_argument("--spatial-review-source")
    export.add_argument("--max-check-error-m", type=float)
    export.add_argument("--max-pivot-shift-m", type=float)
    export.add_argument("--max-abs-rotation-deg", type=float)
    export.add_argument("--max-scale-deviation-ratio", type=float)
    export.add_argument("--max-affine-condition-number", type=float)
    export.add_argument("--robust-outlier-threshold-m", type=float)
    export.add_argument("--disable-robust", action="store_true")
    export.add_argument("--affine-min-improvement-ratio", type=float)
    export.add_argument("--affine-structure-reviewed", action="store_true")
    export.add_argument("--allow-relative-osm", action="store_true")
    export.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        result = prepare_capture(
            delivery_path=args.delivery,
            evidence_path=args.evidence,
            manifest_path=args.manifest,
            output_path=args.out,
            candidate_layers=args.layers,
            force=args.force,
        )
    elif args.command == "diagnose":
        result = diagnose_capture(
            capture_path=args.capture,
            report_path=args.report,
            robust_outlier_threshold_m=args.robust_outlier_threshold_m,
            force=args.force,
        )
    else:
        result = export_profile(
            capture_path=args.capture,
            template_profile_path=args.template_profile,
            output_path=args.out,
            diagnostic_report_path=args.diagnostic_report,
            enable=args.enable,
            requested_model=args.requested_model,
            spatial_review_source=args.spatial_review_source,
            max_check_error_m=args.max_check_error_m,
            max_pivot_shift_m=args.max_pivot_shift_m,
            max_abs_rotation_deg=args.max_abs_rotation_deg,
            max_scale_deviation_ratio=args.max_scale_deviation_ratio,
            max_affine_condition_number=args.max_affine_condition_number,
            robust_outlier_threshold_m=args.robust_outlier_threshold_m,
            disable_robust=args.disable_robust,
            affine_min_improvement_ratio=args.affine_min_improvement_ratio,
            affine_structure_reviewed=args.affine_structure_reviewed,
            allow_relative_osm=args.allow_relative_osm,
            force=args.force,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
