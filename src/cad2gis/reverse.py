"""Traceable GIS-to-DXF conversion with explicit CRS and atomic run publication."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Sequence

from .reverse_models import GISDataset, GISFeature, ReverseConversionError
from .cad2gis_v3.artifact_io import inherit_output_permissions


SCHEMA = "cad2gis.reverse-conversion.v1"
LIMITATIONS = [
    "Creates new CAD geometry in the declared projected CRS; does not restore original CAD coordinates or handles.",
    "Original blocks, dimensions, layouts, curves and QGIS renderer rules cannot be recovered from GIS geometry alone.",
    "Polygon exterior and interior rings are closed CAD boundaries, not filled surfaces.",
    "Z values are preserved numerically; no vertical datum or elevation-unit conversion is performed.",
    "CRS reprojection does not verify absolute positional accuracy or undo OSM registration.",
    "DWG R2018 requires an available AutoCAD Core Console and verified native readback; DXF R2018 is portable.",
]


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check_source_idle(source: Path) -> None:
    if source.suffix.lower() == ".gpkg":
        for suffix in ("-wal", "-journal"):
            sidecar = Path(str(source) + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise ReverseConversionError(
                    f"GeoPackage has an active journal: {sidecar}. Close its writer and checkpoint it first.",
                    code="REVERSE_SOURCE_BUSY",
                )


def _load(source: Path, layers: Sequence[str] | None) -> tuple[GISDataset, str]:
    from .reverse_reader import read_gis

    if not source.is_file():
        raise ReverseConversionError(f"GIS source does not exist: {source}", code="SOURCE_NOT_FOUND")
    _check_source_idle(source)
    before = _sha256(source)
    dataset = read_gis(source, layers=layers)
    _check_source_idle(source)
    if _sha256(source) != before:
        raise ReverseConversionError("GIS source changed during import; retry with a closed snapshot.",
                                     code="REVERSE_SOURCE_CHANGED")
    return dataset, before


def inspect_gis(source: str | Path, *, layers: Sequence[str] | None = None) -> dict[str, Any]:
    """Read and validate a GIS snapshot without creating any artifacts."""
    path = Path(source).expanduser().resolve()
    dataset, digest = _load(path, layers)
    counts = Counter(feature.layer for feature in dataset.features)
    return {
        "schema": SCHEMA, "status": "inspected", "source": str(path), "source_sha256": digest,
        "feature_count": len(dataset.features),
        "layers": [{"name": name, "crs": crs, "feature_count": counts[name]}
                   for name, crs in dataset.layer_crs.items()],
        "geometry_types": dict(Counter(feature.geometry.geom_type for feature in dataset.features)),
        "metadata": dataset.metadata,
    }


def _crs(value: str, context: str) -> Any:
    from pyproj import CRS

    try:
        result = CRS.from_user_input(value)
    except Exception as exc:
        raise ReverseConversionError(f"Invalid {context} CRS: {value}", code="REVERSE_CRS_INVALID") from exc
    if not (result.is_projected or result.is_geographic) or len(result.axis_info) != 2:
        raise ReverseConversionError(f"{context} requires a two-dimensional geographic or projected CRS.",
                                     code="REVERSE_CRS_UNSUPPORTED")
    return result


def _target(dataset: GISDataset, source_crs: str | None, target_crs: str | None) -> tuple[Any, dict[str, Any]]:
    sources = {}
    for name, declared in dataset.layer_crs.items():
        value = source_crs or declared
        if not value:
            raise ReverseConversionError(f"Layer {name!r} has no CRS; provide --source-crs explicitly.",
                                         code="REVERSE_CRS_REQUIRED")
        sources[name] = _crs(value, f"source layer {name!r}")
    if not sources:
        raise ReverseConversionError("No vector layers found.", code="REVERSE_EMPTY_INPUT")
    target = _crs(target_crs, "target") if target_crs else next(iter(sources.values()))
    if not target_crs and any(not target.equals(value, ignore_axis_order=True) for value in sources.values()):
        raise ReverseConversionError("Selected layers use different CRSs; provide --target-crs.",
                                     code="REVERSE_CRS_REQUIRED")
    if not target.is_projected:
        raise ReverseConversionError("CAD output requires a projected CRS with linear units; provide --target-crs.",
                                     code="REVERSE_CRS_REQUIRED")
    return target, sources


def _units(target: Any) -> tuple[int, dict[str, Any]]:
    axes = target.axis_info
    factor = axes[0].unit_conversion_factor
    if not math.isfinite(factor) or not math.isclose(factor, axes[1].unit_conversion_factor, rel_tol=1e-12):
        raise ReverseConversionError("Target CRS axes have incompatible linear units.", code="REVERSE_UNITS_UNSUPPORTED")
    for value, code in ((1.0, 6), (0.3048, 2), (1200 / 3937, 21), (0.001, 4), (0.01, 5), (1000.0, 7)):
        if math.isclose(factor, value, rel_tol=1e-12):
            return code, {"name": axes[0].unit_name, "metres_per_unit": factor, "dxf_insunits": code}
    raise ReverseConversionError(f"Unsupported DXF linear unit: {axes[0].unit_name}", code="REVERSE_UNITS_UNSUPPORTED")


def _project(dataset: GISDataset, sources: dict[str, Any], target: Any) -> tuple[list[GISFeature], list[dict[str, Any]]]:
    from pyproj import Transformer
    from shapely.ops import transform
    import shapely

    transformers = {}
    for layer, source in sources.items():
        try:
            transformers[layer] = Transformer.from_crs(source, target, always_xy=True,
                                                       allow_ballpark=False, only_best=True)
        except Exception as exc:
            raise ReverseConversionError(f"Cannot create CRS transform for {layer!r}: {exc}",
                                         code="REVERSE_TRANSFORM_FAILED") from exc
    projected = []
    for feature in dataset.features:
        transformer = transformers[feature.layer]

        def xy(x: Any, y: Any, z: Any = None) -> tuple[Any, ...]:
            tx, ty = transformer.transform(x, y, errcheck=True)
            return (tx, ty) if z is None else (tx, ty, z)

        try:
            geometry = transform(xy, feature.geometry)
            # The reader has already validated every supplied Z and xy() keeps
            # it unchanged. Check transformed XY only: a mixed XY/XYZ collection
            # legitimately has no Z on its two-dimensional members.
            coordinates = shapely.get_coordinates(geometry)
            if not all(math.isfinite(float(value)) for row in coordinates for value in row):
                raise ValueError("Non-finite transformed coordinate")
            if geometry.is_empty or not geometry.is_valid:
                raise ValueError("Empty or invalid transformed geometry")
        except Exception as exc:
            raise ReverseConversionError(f"CRS transform failed for {feature.layer}/{feature.feature_id}: {exc}",
                                         code="REVERSE_TRANSFORM_FAILED") from exc
        projected.append(replace(feature, geometry=geometry))
    lineage = [{"layer": name, "source_crs": sources[name].to_string(),
                "source_wkt": sources[name].to_wkt(), "operation": transformer.description,
                "pipeline": transformer.definition, "accuracy_m": transformer.accuracy,
                "always_xy": True, "allow_ballpark": False, "z_policy": "preserve_numeric_value"}
               for name, transformer in transformers.items()]
    return projected, lineage


def reverse_convert(
    source: str | Path, *, run_dir: str | Path, layers: Sequence[str] | None = None,
    source_crs: str | None = None, target_crs: str | None = None,
    label_field: str | None = None, layer_field: str | None = None, label_height: float = 2.5,
    registration_manifest: str | Path | None = None, partition: str | None = None,
    output_format: str = "DXF",
) -> dict[str, Any]:
    """Publish drawing.dxf, attributes.jsonl and manifest.json in a new run directory."""
    from .reverse_writer import write_dxf
    from .reverse_verify import verify_drawing

    if not isinstance(output_format, str) or output_format.upper() not in ("DXF", "DWG"):
        raise ReverseConversionError("output_format must be DXF or DWG.", code="REVERSE_FORMAT_UNSUPPORTED")
    output_format = output_format.upper()
    if output_format == "DWG":
        from .reverse_dwg import resolve_runtime

        resolve_runtime()
    source_path = Path(source).expanduser().resolve()
    output = Path(run_dir).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ReverseConversionError(f"Run directory already exists: {output}", code="REVERSE_OUTPUT_EXISTS")
    if not math.isfinite(label_height) or label_height <= 0:
        raise ReverseConversionError("Label height must be finite and positive.")
    dataset, digest = _load(source_path, layers)
    if not dataset.features:
        raise ReverseConversionError("Selected GIS layers contain no features.", code="REVERSE_EMPTY_INPUT")
    coordinate_metadata, features, units = prepare_coordinates(
        dataset, source_path, source_crs=source_crs, target_crs=target_crs,
        registration_manifest=registration_manifest, partition=partition,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    # An exclusive reservation coordinates concurrent publishers of this run.
    lock = output.parent / f".{output.name}.reverse.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ReverseConversionError(f"Another reverse conversion owns {output}; lock: {lock}",
                                     code="REVERSE_OUTPUT_BUSY") from exc
    os.close(descriptor)
    stage: Path | None = None
    published = False
    pending_error: BaseException | None = None
    try:
        if output.exists() or output.is_symlink():
            raise ReverseConversionError(f"Run directory already exists: {output}", code="REVERSE_OUTPUT_EXISTS")
        stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.reverse-", dir=output.parent))
        drawing = stage / "drawing.dxf"
        stats = write_dxf(features, drawing, units=units, label_field=label_field,
                          layer_field=layer_field, label_height=label_height)
        links = stats.pop("feature_entities")
        if len(links) != len(features):
            raise ReverseConversionError("DXF feature accounting mismatch.", code="REVERSE_VERIFICATION_FAILED")
        with (stage / "attributes.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for original, projected, link in zip(dataset.features, features, links, strict=True):
                record = {**link, "properties": original.properties,
                          "source_geometry_type": original.geometry.geom_type,
                          "source_geometry_sha256": hashlib.sha256(original.geometry.wkb).hexdigest(),
                          "cad_geometry_sha256": hashlib.sha256(projected.geometry.wkb).hexdigest()}
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        verification = verify_drawing(
            drawing, stage / "attributes.jsonl", source_features=dataset.features,
            cad_features=features, units=units, layer_map=stats["layer_map"],
            label_field=label_field, layer_field=layer_field, label_height=label_height,
        )
        artifact_names = ["drawing.dxf", "attributes.jsonl"]
        dwg_backend = None
        if output_format == "DWG":
            from .reverse_dwg import convert_dxf_to_dwg, read_dwg_as_dxf

            written = convert_dxf_to_dwg(drawing, stage / "drawing.dwg")
            readback = read_dwg_as_dxf(stage / "drawing.dwg", stage / "dwg_readback.dxf")
            dwg_verification = verify_drawing(
                stage / "dwg_readback.dxf", stage / "attributes.jsonl", source_features=dataset.features,
                cad_features=features, units=units, layer_map=stats["layer_map"],
                label_field=label_field, layer_field=layer_field, label_height=label_height,
            )
            dwg_backend = {"write": written, "readback": readback, "verification": dwg_verification}
            artifact_names.extend(["drawing.dwg", "dwg_readback.dxf"])
            verification = {**verification, "dwg_content_verified": True,
                            "max_coordinate_error": max(verification["max_coordinate_error"], dwg_verification["max_coordinate_error"]),
                            "native_readback_verification": dwg_verification}
        _check_source_idle(source_path)
        if _sha256(source_path) != digest:
            raise ReverseConversionError("GIS source changed during export.", code="REVERSE_SOURCE_CHANGED")
        if registration_manifest is not None and _sha256(Path(registration_manifest)) != coordinate_metadata["registration"]["manifest_sha256"]:
            raise ReverseConversionError("Registration manifest changed during export.", code="REVERSE_SOURCE_CHANGED")
        report = {
            "schema": SCHEMA, "status": "converted", "source": str(source_path), "source_sha256": digest,
            "run_dir": str(output), "output_format": output_format, "dxf_version": "R2018",
            "feature_count": len(features), "source_metadata": dataset.metadata,
            "source_crs_override": source_crs, "declared_layer_crs": dataset.layer_crs,
            **coordinate_metadata, "absolute_accuracy_verified": False,
            "original_cad_restored": False, **stats, "verification": verification,
            "export_options": {"layers": list(dataset.layer_crs), "label_field": label_field,
                               "layer_field": layer_field, "label_height": label_height,
                               "registration_manifest": str(Path(registration_manifest).resolve()) if registration_manifest else None,
                               "partition": partition, "output_format": output_format},
            "artifacts": {name: {"path": str(output / name), "sha256": _sha256(stage / name)}
                          for name in artifact_names},
        }
        if dwg_backend is not None:
            report["dwg_backend"] = _published_paths(dwg_backend, stage, output)
        (stage / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                       sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        inherit_output_permissions(stage)
        if output.exists() or output.is_symlink():
            raise ReverseConversionError(f"Run directory already exists: {output}", code="REVERSE_OUTPUT_EXISTS")
        os.rename(stage, output)
        stage = None
        published = True
        return report
    except BaseException as exc:
        pending_error = exc
        raise
    finally:
        if stage is not None:
            details = getattr(pending_error, "details", {})
            native_failure = str(getattr(pending_error, "code", "")).startswith("REVERSE_DWG_")
            if native_failure or (isinstance(details, dict) and details.get("process_may_be_running")):
                # A native process may still own files here. Keep its workspace
                # and logs available; this directory is not a published run.
                pending_error.retained_artifact_path = stage
                if isinstance(details, dict):
                    details["unverified_workspace"] = str(stage)
                    details["run_published"] = False
            else:
                try:
                    shutil.rmtree(stage)
                except OSError as cleanup_error:
                    if pending_error is None:
                        raise
                    pending_error.retained_artifact_path = stage
                    pending_error.add_note(f"Unverified workspace retained at {stage}: {cleanup_error}")
        try:
            lock.unlink(missing_ok=True)
        except OSError as exc:
            if published:
                error = ReverseConversionError(
                    f"CAD run was published at {output}, but its reservation could not be removed: {lock}. {exc}",
                    code="REVERSE_CLEANUP_FAILED",
                )
                error.retained_artifact_path = output
                raise error from exc
            if pending_error is None:
                raise
            pending_error.add_note(f"Reservation cleanup also failed at {lock}: {exc}")


def _published_paths(value: Any, stage: Path, output: Path) -> Any:
    """Keep backend provenance paths usable after atomic directory publication."""
    if isinstance(value, dict):
        return {key: _published_paths(item, stage, output) for key, item in value.items()}
    if isinstance(value, list):
        return [_published_paths(item, stage, output) for item in value]
    if isinstance(value, str):
        try:
            return str(output / Path(value).relative_to(stage))
        except ValueError:
            pass
    return value


def prepare_coordinates(
    dataset: GISDataset, source_path: Path, *, source_crs: str | None = None,
    target_crs: str | None = None, registration_manifest: str | Path | None = None,
    partition: str | None = None,
) -> tuple[dict[str, Any], list[GISFeature], int]:
    """Prepare the explicitly chosen coordinate mode for export and revalidation."""
    if registration_manifest is not None:
        from .reverse_registration import load_registration

        if source_crs is not None or target_crs is not None:
            raise ReverseConversionError("Native coordinate recovery cannot be combined with CRS overrides.",
                                         code="REVERSE_REGISTRATION_INVALID")
        registration = load_registration(Path(registration_manifest), source_path, dataset, partition=partition)
        features = [replace(feature, geometry=registration.transform_geometry(feature.geometry))
                    for feature in dataset.features]
        return {"coordinate_mode": "native_registered", "target_crs": "native_cad", "target_wkt": None,
                "units": registration.unit_report, "transforms": [], "registration": registration.metadata,
                "native_coordinates_restored": True,
                "limitations": ["Recovers the registered native coordinate frame; does not reconstruct original CAD entities or undo GIS geometry edits.",
                                *LIMITATIONS[1:4], "Numerical inverse closure does not verify absolute positional accuracy.",
                                LIMITATIONS[5]]}, features, registration.units
    if partition is not None:
        raise ReverseConversionError("--partition requires --registration-manifest.", code="REVERSE_REGISTRATION_INVALID")
    target, sources = _target(dataset, source_crs, target_crs)
    units, unit_report = _units(target)
    features, transforms = _project(dataset, sources, target)
    return {"coordinate_mode": "projected", "target_crs": target.to_string(), "target_wkt": target.to_wkt(),
            "units": unit_report, "transforms": transforms, "native_coordinates_restored": False,
            "limitations": LIMITATIONS}, features, units


def register(commands: Any) -> None:
    """Register lightweight parsers; GIS dependencies load only during execution."""
    inspect = commands.add_parser("reverse-inspect", help="Validate GeoPackage/GeoJSON layers for CAD export.")
    convert = commands.add_parser("reverse-convert", help="Convert GIS geometry to a traceable DXF or DWG run.")
    verify = commands.add_parser("reverse-verify", help="Compare a saved CAD run against its source GIS.")
    verify.add_argument("run_dir", type=Path)
    verify.add_argument("--source", type=Path, help="Relocated source GIS, whose hash must match the manifest.")
    verify.add_argument("--registration-manifest", type=Path, help="Relocated forward manifest, whose hash must match the recorded evidence.")
    verify.add_argument("--tolerance", type=float, default=1e-8, help="Maximum coordinate error in CAD units.")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(handler=_verify_command)
    for command in (inspect, convert):
        command.add_argument("source", type=Path)
        command.add_argument("--layer", dest="layers", action="append", help="Select a vector layer; repeatable.")
        command.add_argument("--json", action="store_true", help="Emit structured JSON.")
    inspect.set_defaults(handler=_inspect_command)
    convert.add_argument("--run-dir", type=Path, required=True, help="New output directory (must not exist).")
    convert.add_argument("--output-format", choices=("DXF", "DWG"), default="DXF")
    convert.add_argument("--source-crs", help="Explicitly override all selected source layer CRSs.")
    convert.add_argument("--target-crs", help="Projected CAD coordinate system, e.g. EPSG:32648.")
    convert.add_argument("--registration-manifest", type=Path, help="Verified forward run manifest to recover native CAD coordinates.")
    convert.add_argument("--partition", help="Explicit delivery partition for native coordinate recovery.")
    convert.add_argument("--label-field", help="Property to export as MTEXT labels (opt-in).")
    convert.add_argument("--layer-field", help="Property to use as the CAD layer name.")
    convert.add_argument("--label-height", type=float, default=2.5, help="Label height in target CRS units.")
    convert.set_defaults(handler=_convert_command)


def _inspect_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    return inspect_gis(args.source, layers=args.layers), 0


def _convert_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    return reverse_convert(args.source, run_dir=args.run_dir, layers=args.layers,
                           source_crs=args.source_crs, target_crs=args.target_crs,
                           label_field=args.label_field, layer_field=args.layer_field,
                           label_height=args.label_height, registration_manifest=args.registration_manifest,
                           partition=args.partition, output_format=args.output_format), 0


def _verify_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from .reverse_verify import verify_reverse

    return verify_reverse(args.run_dir, source=args.source, tolerance=args.tolerance,
                          registration_manifest=args.registration_manifest), 0
