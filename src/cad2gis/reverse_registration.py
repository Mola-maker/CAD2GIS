"""Recover a declared native coordinate frame from a hash-bound GIS delivery.

This reverses the recorded coordinate operation, not source geometry edits or
the source drawing's objects. Numerical closure is not positional accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Callable

from pyproj import CRS
from shapely.ops import transform

from .cad2gis_v3.units import build_unit_crs_contract, resolve_insunits
from .reverse_models import GISDataset, ReverseConversionError
from .visual_audit import inverse_operation


ERROR = "REVERSE_REGISTRATION_INVALID"
NUMERIC_TOLERANCE_M = 1e-5


def _invalid(message: str) -> ReverseConversionError:
    return ReverseConversionError(message, code=ERROR)


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise _invalid(f"{name} must be an exact lowercase SHA-256 digest.")
    return value


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(f"Missing or invalid {name} object.")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"Missing or invalid {name}.")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise _invalid(f"{name} must be a finite positive number.")
    return float(value)


def _same_crs(left: Any, right: Any) -> bool:
    if left == right:
        return True
    if left is None or right is None:
        return False
    return CRS.from_user_input(left).equals(CRS.from_user_input(right), ignore_axis_order=True)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid(f"Duplicate manifest key: {key!r}.")
        result[key] = value
    return result


def _delivery(manifest: dict[str, Any], source_sha: str, partition: str | None) -> tuple[dict, str | None]:
    artifacts = _object(manifest.get("artifacts"), "artifacts")
    primary = _object(artifacts.get("delivery"), "delivery artifact")
    partitions = _object(manifest.get("delivery_partitions", {}), "delivery partitions")
    scoped = manifest.get("delivery_partition")
    scoped_id = None
    if scoped is not None:
        scoped_id = _text(_object(scoped, "delivery partition").get("region_id"), "delivery partition region_id")
    if partition is not None:
        _text(partition, "partition")
    if scoped_id not in {None, "MAIN"}:
        if partition != scoped_id:
            raise _invalid(f"This delivery requires its explicit partition {scoped_id!r}.")
        selected = primary
    elif partition in {None, "MAIN"}:
        selected = primary
        if partition is None and any(isinstance(item, dict) and item.get("sha256") == source_sha for item in partitions.values()):
            raise _invalid("GIS content matches a partition delivery; select its exact partition explicitly.")
    else:
        selected = _object(partitions.get(partition), f"delivery partition {partition!r}")
    _text(selected.get("path"), "declared delivery path")
    if _sha(selected.get("sha256"), "Declared delivery SHA-256") != source_sha:
        raise _invalid("GIS source SHA-256 does not match the selected manifest delivery artifact.")
    operation_partition = None if partition in {None, "MAIN"} else partition
    selected_record = selected.get("osm_matching")
    if selected_record is not None:
        crs = _object(manifest.get("crs"), "crs")
        declared_record = (crs.get("osm_matching_by_region") or {}).get(operation_partition) if operation_partition else crs.get("osm_matching")
        if selected_record != declared_record:
            raise _invalid("Partition artifact and coordinate-operation OSM records disagree.")
    return selected, operation_partition


def _unit_contract(crs: dict[str, Any]) -> tuple[int, dict, float, float]:
    contract = _object(crs.get("unit_crs_contract"), "unit CRS contract")
    if contract.get("schema_version") != "cad2gis.unit-crs-contract.v1":
        raise _invalid("Unsupported unit CRS contract schema.")
    source_unit = _object(contract.get("source_geometry_unit"), "native insertion unit")
    canonical_unit = resolve_insunits(source_unit.get("insunits"))
    if source_unit != canonical_unit.to_manifest_dict():
        raise _invalid("Native insertion-unit metadata is inconsistent with its declared code.")
    scale = _number(contract.get("source_coordinate_scale_to_m"), "Native coordinate scale")
    if contract.get("source_coordinate_scale_reviewed") is not True:
        raise _invalid("Native coordinate scale must be explicitly reviewed.")
    rebuilt = build_unit_crs_contract(
        canonical_unit.insunits, crs.get("source_crs"), crs.get("target_crs"),
        source_coordinate_scale_to_m=scale, source_coordinate_scale_reviewed=True,
        local_registration_strategy=contract.get("local_registration_strategy"),
        local_registration_reviewed=contract.get("local_registration_reviewed", False),
    ).to_manifest_dict()
    for key in ("source_crs", "target_crs"):
        if not _same_crs(contract.get(key), rebuilt[key]):
            raise _invalid(f"Unit contract {key} differs from the declared coordinate operation.")
    for key in ("coordinate_mode", "source_crs_kind", "source_to_crs_axis_factor", "source_crs_axis_unit", "target_crs_axis_unit"):
        if contract.get(key) != rebuilt[key]:
            raise _invalid(f"Unit contract {key} is inconsistent with its reviewed CRS/coordinate scale.")
    registration = contract.get("coordinate_registration")
    if registration is not None and _object(registration, "GEODATA registration").get("schema_version") != "cad2gis.dwg_geodata_registration.v1":
        raise _invalid("Unsupported GEODATA registration schema.")
    # WCS units and insertion units can differ. Stamp units matching the actual
    # recovered numeric coordinates; preserve the original insertion hint below.
    for candidate_scale, code, name in ((1.0, 6, "metre"), (0.001, 4, "millimetre"), (0.01, 5, "centimetre"),
                                        (0.0254, 1, "inch"), (0.3048, 2, "foot"), (1200 / 3937, 21, "US survey foot"), (1000.0, 7, "kilometre")):
        if math.isclose(scale, candidate_scale, rel_tol=1e-12):
            return code, {"name": name, "metres_per_unit": scale, "dxf_insunits": code,
                          "basis": "reviewed_source_coordinate_scale", "source_geometry_unit": dict(source_unit),
                          "source_coordinate_scale_origin": contract.get("source_coordinate_scale_origin"),
                          "z_policy": "numeric_passthrough_no_vertical_unit_or_datum_conversion"}, scale, rebuilt["target_crs_axis_unit"]["metres_per_unit"]
    raise _invalid("Reviewed native coordinate scale has no supported DXF unit code.")


@dataclass
class Registration:
    """Manifest-bound XY inverse with numeric Z passthrough and closure evidence."""

    _inverse: Callable
    units: int
    unit_report: dict[str, Any]
    metadata: dict[str, Any]
    _source_scale: float
    _target_scale: float

    def _point(self, x: float, y: float, *, forward: bool) -> tuple[float, float]:
        point = float(x), float(y)
        if not all(math.isfinite(value) for value in point):
            raise _invalid("Registration input coordinates must be finite.")
        if forward:
            native, delivered = point, self._inverse.forward(*point)
        else:
            delivered, native = point, self._inverse(*point)
        closed_delivery = self._inverse.forward(*native)
        closed_native = self._inverse(*closed_delivery)
        native_error = math.dist(native, closed_native) * self._source_scale
        target_error = math.dist(delivered, closed_delivery) * self._target_scale
        if not all(math.isfinite(value) for value in (*native, *delivered, native_error, target_error)):
            raise _invalid("Registration produced a non-finite coordinate or residual.")
        metrics = self.metadata["roundtrip"]
        metrics["checked_point_count"] += 1
        metrics["max_native_residual_m"] = max(metrics["max_native_residual_m"], native_error)
        metrics["max_target_grid_residual_m"] = max(metrics["max_target_grid_residual_m"], target_error)
        if max(native_error, target_error) > NUMERIC_TOLERANCE_M:
            metrics["passed"] = False
            raise _invalid(f"Registration numerical roundtrip exceeds {NUMERIC_TOLERANCE_M:g} m: native={native_error:g}, target={target_error:g}.")
        metrics["passed"] = True
        return delivered if forward else native

    def _geometry(self, geometry: Any, *, forward: bool) -> Any:
        def xy(x: Any, y: Any, z: Any = None) -> tuple:
            if isinstance(x, (int, float)):
                result = self._point(x, y, forward=forward)
                if z is not None and not math.isfinite(z):
                    raise _invalid("Registration Z values must be finite.")
                return result if z is None else (*result, z)
            if len(x) != len(y) or (z is not None and len(x) != len(z)):
                raise _invalid("Registration coordinate arrays have different lengths.")
            result = [self._point(a, b, forward=forward) for a, b in zip(x, y)]
            if z is not None and not all(math.isfinite(value) for value in z):
                raise _invalid("Registration Z values must be finite.")
            columns = tuple(zip(*result)) if result else ((), ())
            return columns if z is None else (*columns, z)

        try:
            return transform(xy, geometry)
        except ReverseConversionError:
            raise
        except Exception as exc:
            raise _invalid(f"Declared coordinate operation cannot transform this geometry: {exc}") from exc

    def transform_geometry(self, geometry: Any) -> Any:
        """Return native CAD XY, preserving numeric Z exactly."""
        return self._geometry(geometry, forward=False)

    def forward_geometry(self, geometry: Any) -> Any:
        """Return delivery XY through the same declared operation."""
        return self._geometry(geometry, forward=True)


def load_registration(manifest_path: Path, source_gis: Path, dataset: GISDataset, *, partition: str | None = None) -> Registration:
    """Load only a supported canonical run bound to this exact GIS snapshot.

    The declared delivery path is retained as evidence, never searched or used
    to substitute a different GIS input. Source CAD identity is manifest-declared;
    the original drawing need not be present and is not claimed byte-verified.
    """
    try:
        manifest_path, source_gis = Path(manifest_path).resolve(), Path(source_gis).resolve()
        raw = manifest_path.read_bytes()
        manifest = _object(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object), "registration manifest")
        if manifest.get("schema_version") != "cad2gis-run-manifest-v4" or manifest.get("pipeline") != "cad2gis-reviewed-project-evidence-first-v4":
            raise _invalid("Native recovery requires a canonical registered run manifest v4; source-only exports are not registrations.")
        source = _object(manifest.get("source"), "origin source identity")
        source_cad_sha = _sha(source.get("sha256"), "Origin CAD SHA-256")
        source_cad_path = _text(source.get("path"), "origin CAD path")
        source_sha = _digest(source_gis)
        selected, operation_partition = _delivery(manifest, source_sha, partition)
        crs = _object(manifest.get("crs"), "crs")
        target = CRS.from_user_input(_text(crs.get("target_crs"), "delivery target CRS"))
        if not target.is_projected or len(target.axis_info) != 2:
            raise _invalid("Registration delivery CRS must have two projected linear axes.")
        if not dataset.layer_crs or not dataset.features:
            raise _invalid("Registration needs non-empty selected GIS layers and geometry.")
        for name, value in dataset.layer_crs.items():
            if not value or not _same_crs(value, target):
                raise _invalid(f"Selected GIS layer {name!r} CRS differs from the registered delivery CRS.")
        units, unit_report, source_scale, target_scale = _unit_contract(crs)
        operation = _object(crs.get("coordinate_operation"), "coordinate operation")
        record = (crs.get("osm_matching_by_region") or {}).get(operation_partition) if operation_partition else crs.get("osm_matching")
        local = isinstance(record, dict) and (record.get("base") or {}).get("mode") == "local"
        if not local:
            _text(operation.get("definition"), "exact recorded coordinate pipeline definition")
        inverse = inverse_operation(manifest, partition=operation_partition)
        metadata = {**inverse.metadata, "schema_version": "cad2gis.reverse-registration.v1",
                    "manifest_path": str(manifest_path), "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                    "manifest_schema": manifest["schema_version"], "source_gis_sha256": source_sha,
                    "source_cad_sha256": source_cad_sha, "source_cad_path": source_cad_path,
                    "source_cad_bytes_verified": False, "declared_delivery_path": selected["path"],
                    "partition": operation_partition, "source_crs": crs.get("source_crs"), "target_crs": target.to_string(),
                    "authority": "relative_osm" if isinstance(record, dict) and record.get("authority") == "relative_osm" else "manifest_declared_coordinate_operation",
                    "absolute_accuracy_verified": False, "original_cad_restored": False,
                    "geometry_edits_reversed": False, "z_policy": unit_report["z_policy"],
                    "roundtrip": {"checked_point_count": 0, "max_native_residual_m": 0.0,
                                  "max_target_grid_residual_m": 0.0, "numeric_tolerance_m": NUMERIC_TOLERANCE_M,
                                  "passed": False, "absolute_accuracy_verified": False}}
        result = Registration(inverse, units, unit_report, metadata, source_scale, target_scale)
        for feature in dataset.features:
            if feature.layer not in dataset.layer_crs:
                raise _invalid(f"Feature refers to an undeclared GIS layer: {feature.layer!r}.")
            result.transform_geometry(feature.geometry)
        if not metadata["roundtrip"]["checked_point_count"]:
            raise _invalid("Registration requires at least one non-empty finite coordinate.")
        if _digest(source_gis) != source_sha or manifest_path.read_bytes() != raw:
            raise _invalid("GIS source or registration manifest changed during registration validation.")
        return result
    except ReverseConversionError:
        raise
    except Exception as exc:
        raise _invalid(f"Cannot validate native coordinate registration: {exc}") from exc
