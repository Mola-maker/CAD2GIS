"""Independently compare reopened CAD entities with source-bound GIS features."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

from .reverse_models import GISFeature, ReverseConversionError

VERIFY_SCHEMA = "cad2gis.reverse-verification.v1"


def _fail(message: str) -> None:
    raise ReverseConversionError(message, code="REVERSE_VERIFICATION_FAILED")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"Duplicate JSON member: {key}")
        result[key] = value
    return result


def _json(text: str) -> Any:
    try:
        result = json.loads(text, object_pairs_hook=_object,
                            parse_constant=lambda value: _fail(f"Non-finite JSON value: {value}"))
        json.dumps(result, allow_nan=False)
        return result
    except (ValueError, RecursionError) as exc:
        _fail(f"Malformed verification JSON: {exc}")


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _member(directory: Path, name: str) -> Path:
    path = directory / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory):
        _fail(f"Missing or unsafe run artifact: {name}")
    return path


def _parts(geometry: Any):
    if hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _parts(part)
    else:
        yield geometry


def _primitives(geometry: Any):
    for part in _parts(geometry):
        if part.geom_type == "Point":
            yield "POINT", list(part.coords), False
        elif part.geom_type in ("LineString", "LinearRing"):
            coords = list(part.coords)
            closed = part.geom_type == "LinearRing"
            if closed and coords[0] == coords[-1]:
                coords.pop()
            yield "POLYLINE" if part.has_z else "LWPOLYLINE", coords, closed
        elif part.geom_type == "Polygon":
            for ring in (part.exterior, *part.interiors):
                coords = list(ring.coords)
                if coords[0] == coords[-1]:
                    coords.pop()
                yield "POLYLINE" if ring.has_z else "LWPOLYLINE", coords, True
        else:
            _fail(f"Unsupported expected geometry: {part.geom_type}")


def _xyz(point: Sequence[float]) -> tuple[float, float, float]:
    return float(point[0]), float(point[1]), float(point[2]) if len(point) > 2 else 0.0


def _error(actual: Sequence[float], expected: Sequence[float], tolerance: float, context: str) -> float:
    a, b = _xyz(actual), _xyz(expected)
    if not all(math.isfinite(value) for value in (*a, *b)):
        _fail(f"{context}: non-finite coordinate")
    delta = max(abs(x - y) for x, y in zip(a, b, strict=True))
    if delta > tolerance:
        _fail(f"{context}: coordinate error {delta:.12g} exceeds {tolerance:.12g}")
    return delta


def _label_anchor(geometry: Any) -> tuple[float, ...]:
    part = max(_parts(geometry), key=lambda value: (value.area, value.length))
    if part.geom_type == "Point":
        return tuple(part.coords[0])
    if part.geom_type in ("LineString", "LinearRing"):
        return tuple(part.interpolate(part.length / 2).coords[0])
    anchor = tuple(part.representative_point().coords[0])
    vertex = part.exterior.coords[0]
    return (*anchor, vertex[2]) if len(vertex) == 3 else anchor


def verify_drawing(
    drawing: Path, attributes: Path, *, source_features: Sequence[GISFeature],
    cad_features: Sequence[GISFeature], units: int, layer_map: dict[str, str],
    label_field: str | None, layer_field: str | None, label_height: float,
    tolerance: float = 1e-8,
) -> dict[str, Any]:
    """Verify persisted entities and sidecars, independently of DXF writer helpers."""
    import ezdxf
    from ezdxf.lldxf.validator import is_valid_lineweight

    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        _fail("Verification tolerance must be finite and nonnegative")
    try:
        document = ezdxf.readfile(drawing)
        auditor = document.audit()
        if auditor.has_errors or auditor.has_fixes:
            _fail("Reopened DXF requires audit repairs or has errors")
        if document.dxfversion != "AC1032" or document.units != units:
            _fail("DXF version or insertion units differ from the declared export")
        records = [_json(line) for line in attributes.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, ezdxf.DXFError) as exc:
        _fail(f"Cannot read CAD verification artifacts: {exc}")
    if len(records) != len(source_features) or len(cad_features) != len(source_features):
        _fail("Source, CAD and attribute feature counts differ")
    if not isinstance(layer_map, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in layer_map.items()):
        _fail("Invalid CAD layer mapping")
    if len({name.casefold() for name in layer_map.values()}) != len(layer_map):
        _fail("CAD layer mapping merges distinct source layer names")
    expected_keys = {(feature.layer, feature.feature_id) for feature in source_features}
    if len(expected_keys) != len(source_features):
        _fail("Source feature identities are not unique")
    by_key = {}
    for record in records:
        if not isinstance(record, dict):
            _fail("Attribute record must be an object")
        key = record.get("input_layer"), record.get("feature_id")
        if not all(isinstance(value, str) for value in key) or key in by_key:
            _fail("Missing or duplicate attribute feature identity")
        by_key[key] = record
    if set(by_key) != expected_keys:
        _fail("Attribute feature identities do not match source")
    modelspace = {entity.dxf.handle: entity for entity in document.modelspace()}
    if any(len(layout) for layout in document.layouts if layout.name != "Model"):
        _fail("DXF contains unaccounted paper-space geometry")
    used: set[str] = set()
    requested_layers: set[str] = set()
    geometry_count = label_count = vertex_count = 0
    max_error = 0.0
    for original, feature in zip(source_features, cad_features, strict=True):
        key = original.layer, original.feature_id
        if (feature.layer, feature.feature_id) != key:
            _fail("Projected feature identity differs from source")
        record = by_key[key]
        context = f"{original.layer}/{original.feature_id}"
        for name, expected in (("properties", original.properties),
                               ("source_geometry_type", original.geometry.geom_type),
                               ("source_geometry_sha256", hashlib.sha256(original.geometry.wkb).hexdigest()),
                               ("cad_geometry_sha256", hashlib.sha256(feature.geometry.wkb).hexdigest())):
            # JSON comparison also distinguishes booleans from numeric values.
            if json.dumps(record.get(name), sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
                _fail(f"{context}: {name} differs from source-derived evidence")
        if layer_field is not None and original.properties.get(layer_field) is None:
            _fail(f"{context}: missing configured CAD layer field")
        requested = str(original.properties[layer_field]) if layer_field is not None else original.layer
        requested_layers.add(requested)
        cad_layer = layer_map.get(requested)
        if not cad_layer or record.get("cad_layer") != cad_layer or cad_layer not in document.layers:
            _fail(f"{context}: CAD layer differs from requested source layer")
        layer = document.layers.get(cad_layer)
        if layer.is_off() or layer.is_frozen() or layer.dxf.get("plot", 1) != 1:
            _fail(f"{context}: CAD layer visibility differs")
        if (layer.dxf.color != 7 or layer.dxf.linetype.upper() != "CONTINUOUS"
                or layer.dxf.get("lineweight", -3) != -3 or layer.dxf.get("true_color", None) is not None
                or layer.transparency != 0):
            _fail(f"{context}: CAD layer presentation differs from the export defaults")
        entities = record.get("entities")
        if not isinstance(entities, list) or any(not isinstance(item, dict) for item in entities):
            _fail(f"{context}: invalid entity references")
        if any(item.get("role") not in ("geometry", "label") for item in entities):
            _fail(f"{context}: unknown entity role")
        geometry_refs = [item for item in entities if item["role"] == "geometry"]
        label_refs = [item for item in entities if item["role"] == "label"]
        expected_parts = list(_primitives(feature.geometry))
        if len(geometry_refs) != len(expected_parts):
            _fail(f"{context}: geometry part or polygon ring count differs")
        for item in entities:
            handle = item.get("handle")
            if not isinstance(handle, str) or handle in used or handle not in modelspace:
                _fail(f"{context}: missing, duplicated or non-modelspace entity handle")
            used.add(handle)
            entity = modelspace[handle]
            if entity.dxf.layer != cad_layer:
                _fail(f"{context}: entity is on the wrong CAD layer")
            if (entity.dxf.get("invisible", 0) != 0 or entity.dxf.get("linetype", "BYLAYER").upper() != "BYLAYER"
                    or entity.dxf.get("ltscale", 1) != 1 or entity.dxf.get("transparency", None) is not None):
                _fail(f"{context}: unrequested entity visibility or line presentation")
            for field, attribute, default, valid in (
                ("style_aci", "color", 256, lambda v: 0 <= v <= 256),
                ("style_truecolor", "true_color", None, lambda v: 0 <= v <= 0xFFFFFF),
                ("style_lineweight", "lineweight", -1, is_valid_lineweight),
            ):
                value = original.properties.get(field)
                expected = value if isinstance(value, int) and not isinstance(value, bool) and valid(value) else default
                if entity.dxf.get(attribute, default) != expected:
                    _fail(f"{context}: entity {attribute} differs from source style")
        for item, (kind, coordinates, closed) in zip(geometry_refs, expected_parts, strict=True):
            entity = modelspace[item["handle"]]
            if entity.dxftype() != kind:
                _fail(f"{context}: CAD entity type differs from source geometry")
            if (entity.dxf.get("thickness", 0) != 0
                    or tuple(entity.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1)):
                _fail(f"{context}: unrequested entity thickness or extrusion")
            if kind == "POINT":
                actual = [tuple(entity.dxf.location)]
            elif kind == "LWPOLYLINE":
                if bool(entity.closed) != closed or any(p[2] or p[3] or p[4] for p in entity.get_points("xyseb")):
                    _fail(f"{context}: closure, width or bulge changed")
                if entity.dxf.get("const_width", 0) != 0 or entity.dxf.get("thickness", 0) != 0:
                    _fail(f"{context}: unrequested CAD width or thickness")
                actual = [tuple(point) for point in entity.vertices_in_wcs()]
            else:
                if not entity.is_3d_polyline or entity.is_polygon_mesh or entity.is_poly_face_mesh or bool(entity.is_closed) != closed:
                    _fail(f"{context}: 3D polyline mode or closure differs")
                if (entity.dxf.flags != (9 if closed else 8)
                        or tuple(entity.dxf.get("elevation", (0, 0, 0))) != (0, 0, 0)
                        or entity.dxf.get("default_start_width", 0) != 0
                        or entity.dxf.get("default_end_width", 0) != 0
                        or entity.dxf.get("smooth_type", 0) != 0
                        or any(vertex.dxf.flags != 32 for vertex in entity.vertices)):
                    _fail(f"{context}: unrequested 3D polyline elevation, width or curve fitting")
                if any(vertex.dxf.get("bulge", 0) or vertex.dxf.get("start_width", 0)
                       or vertex.dxf.get("end_width", 0) for vertex in entity.vertices):
                    _fail(f"{context}: unrequested 3D polyline width or bulge")
                actual = [tuple(vertex.dxf.location) for vertex in entity.vertices]
            if len(actual) != len(coordinates):
                _fail(f"{context}: vertex count differs")
            for a, b in zip(actual, coordinates, strict=True):
                max_error = max(max_error, _error(a, b, tolerance, context))
                vertex_count += 1
            geometry_count += 1
        if label_field is not None and label_field not in original.properties:
            _fail(f"{context}: missing configured label field")
        value = original.properties.get(label_field) if label_field is not None else None
        label = None if value is None or value == "" else str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
        if len(label_refs) != (0 if label is None else 1):
            _fail(f"{context}: label count differs from explicit label field")
        if label is not None:
            entity = modelspace[label_refs[0]["handle"]]
            if entity.dxftype() != "MTEXT" or entity.plain_text() != label:
                _fail(f"{context}: label text differs from source field")
            escaped = {"\\": "\\\\", "{": "\\{", "}": "\\}", "^": "^ ", "%": "%{}", "\n": "\\P"}
            if entity.text != "".join(escaped.get(char, char) for char in label):
                _fail(f"{context}: label contains unrequested inline formatting")
            max_error = max(max_error, _error(entity.dxf.insert, _label_anchor(feature.geometry), tolerance, context))
            if not math.isclose(entity.dxf.char_height, label_height, rel_tol=0, abs_tol=tolerance):
                _fail(f"{context}: label height differs")
            if entity.dxf.get("rotation", 0) != 0 or tuple(entity.dxf.get("text_direction", (1, 0, 0))) != (1, 0, 0):
                _fail(f"{context}: label orientation differs")
            if (entity.dxf.get("attachment_point", 1) != 1 or entity.dxf.get("width", 0) != 0
                    or entity.dxf.get("style", "Standard") != "Standard"
                    or tuple(entity.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1)):
                _fail(f"{context}: label attachment, wrapping, text style or plane differs")
            label_count += 1
    if used != set(modelspace):
        _fail("DXF contains unaccounted modelspace entities")
    if requested_layers != set(layer_map):
        _fail("CAD layer mapping contains missing or unaccounted source layers")
    return {"schema": VERIFY_SCHEMA, "status": "verified", "feature_count": len(source_features),
            "geometry_entity_count": geometry_count, "label_count": label_count,
            "entity_count": len(modelspace), "vertex_count": vertex_count,
            "max_coordinate_error": max_error, "coordinate_tolerance": tolerance,
            "source_geometry_verified": True, "attributes_verified": True,
            "labels_verified": True, "absolute_accuracy_verified": False}


def verify_reverse(run_dir: str | Path, *, source: str | Path | None = None,
                   tolerance: float = 1e-8, registration_manifest: str | Path | None = None,
                   scratch_parent: str | Path | None = None) -> dict[str, Any]:
    """Reopen and verify an existing run without modifying it; allow relocation."""
    from .reverse import SCHEMA, _check_source_idle, _load, prepare_coordinates

    directory = Path(run_dir).expanduser().resolve()
    manifest_path = _member(directory, "manifest.json")
    manifest_hash = _hash(manifest_path)
    manifest = _json(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or manifest.get("status") != "converted":
        _fail("Unsupported reverse conversion manifest")
    options = manifest.get("export_options")
    if not isinstance(options, dict) or any(key not in options for key in ("layers", "label_field", "layer_field", "label_height")):
        _fail("Manifest lacks export options required for source verification; regenerate this legacy run")
    if (not isinstance(options["layers"], list) or not options["layers"]
            or any(not isinstance(name, str) or not name for name in options["layers"])
            or any(value is not None and not isinstance(value, str)
                   for value in (options["label_field"], options["layer_field"], options.get("partition"),
                                 options.get("registration_manifest"), manifest.get("source_crs_override")))
            or isinstance(options["label_height"], bool) or not isinstance(options["label_height"], (int, float))
            or not math.isfinite(options["label_height"]) or options["label_height"] <= 0):
        _fail("Invalid manifest export options")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        _fail("Manifest lacks artifact identities")
    output_format = manifest.get("output_format")
    if output_format not in ("DXF", "DWG") or options.get("output_format", output_format) != output_format:
        _fail("Unsupported or inconsistent CAD output format")
    names = ["drawing.dxf", "attributes.jsonl"]
    if output_format == "DWG":
        names.extend(["drawing.dwg", "dwg_readback.dxf"])
    paths = {name: _member(directory, name) for name in names}
    hashes = {name: _hash(path) for name, path in paths.items()}
    for name, digest in hashes.items():
        if not isinstance(artifacts.get(name), dict) or artifacts[name].get("sha256") != digest:
            _fail(f"Artifact hash differs: {name}")
    source_value = source if source is not None else manifest.get("source")
    if not isinstance(source_value, (str, Path)) or not str(source_value):
        _fail("Manifest lacks source path; pass source explicitly")
    source_path = Path(source_value).expanduser().resolve()
    dataset, digest = _load(source_path, options["layers"])
    if digest != manifest.get("source_sha256"):
        _fail("Source GIS hash differs from the exported snapshot")
    native = manifest.get("coordinate_mode") == "native_registered"
    registration_path = options.get("registration_manifest")
    if native != bool(registration_path) or manifest.get("coordinate_mode") not in ("projected", "native_registered"):
        _fail("Manifest coordinate mode lacks matching registration evidence")
    if registration_manifest is not None:
        if not native:
            _fail("Projected export cannot accept a registration manifest override")
        registration_path = registration_manifest
    coordinate_metadata, features, units = prepare_coordinates(
        dataset, source_path, source_crs=manifest.get("source_crs_override"),
        target_crs=None if native else manifest.get("target_wkt"),
        registration_manifest=registration_path, partition=options.get("partition"),
    )
    if (manifest.get("units") != coordinate_metadata["units"]
            or manifest.get("declared_layer_crs") != dataset.layer_crs
            or manifest.get("native_coordinates_restored") != native):
        _fail("Manifest CRS or units differ from source-derived evidence")
    if native:
        recorded = manifest.get("registration")
        if not isinstance(recorded, dict) or {k: v for k, v in recorded.items() if k != "manifest_path"} != {
            k: v for k, v in coordinate_metadata["registration"].items() if k != "manifest_path"
        }:
            _fail("Registration evidence differs from the recorded coordinate recovery")
    result = verify_drawing(paths["drawing.dxf"], paths["attributes.jsonl"],
                            source_features=dataset.features, cad_features=features,
                            units=units, layer_map=manifest.get("layer_map"),
                            label_field=options["label_field"], layer_field=options["layer_field"],
                            label_height=options["label_height"], tolerance=tolerance)
    if output_format == "DWG":
        from .reverse_dwg import native_readback_workspace, read_dwg_as_dxf

        # A retained readback and matching hashes cannot establish the current
        # DWG's geometry. Extract the binary again and compare it to the source.
        with native_readback_workspace(prefix="cad2gis-dwg-verify-",
                                       parent=Path(scratch_parent) if scratch_parent is not None else directory.parent) as temporary:
            fresh = temporary / "fresh.dxf"
            read_dwg_as_dxf(paths["drawing.dwg"], fresh)
            for label, candidate in (("retained_readback_verification", paths["dwg_readback.dxf"]),
                                     ("native_readback_verification", fresh)):
                native_result = verify_drawing(candidate, paths["attributes.jsonl"], source_features=dataset.features,
                                               cad_features=features, units=units, layer_map=manifest.get("layer_map"),
                                               label_field=options["label_field"], layer_field=options["layer_field"],
                                               label_height=options["label_height"], tolerance=tolerance)
                result[label] = native_result
                result["max_coordinate_error"] = max(result["max_coordinate_error"], native_result["max_coordinate_error"])
        result["dwg_content_verified"] = True
    for name in ("feature_count", "entity_count", "geometry_entity_count", "label_count"):
        if manifest.get(name) != result[name]:
            _fail(f"Manifest {name} differs from verified result")
    if manifest.get("absolute_accuracy_verified") is not False or manifest.get("original_cad_restored") is not False:
        _fail("Manifest claims unsupported absolute accuracy or original CAD restoration")
    if _hash(manifest_path) != manifest_hash or any(_hash(paths[name]) != value for name, value in hashes.items()):
        _fail("Run artifacts changed during verification")
    _check_source_idle(source_path)
    if _hash(source_path) != digest:
        _fail("Source GIS changed during verification")
    return {**result, "run_dir": str(directory), "source": str(source_path),
            "source_sha256": digest, "manifest_sha256": manifest_hash, "artifact_sha256": hashes}
