"""Strict native snapshot bindings and explicit primitive parameter contracts."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any

from .reverse_models import ReverseConversionError

SUPPORTED = frozenset({"LINE", "LWPOLYLINE", "CIRCLE", "ARC", "ELLIPSE"})
MODE = "source_snapshot_primitive_reproduction"
SNAPSHOT_ARTIFACTS = {
    "source_gpkg": "source.gpkg",
    "reader_records": "reader_records.jsonl",
    "plan_entities": "plan_entities.jsonl",
    "source_inventory": "review/source_inventory.json",
    "cad_scene_graph": "review/cad_scene_graph.json",
    "scene_visual_manifest": "reasoning/scene_visual/manifest.json",
}


def fail(message: str) -> None:
    raise ReverseConversionError(message, code="REVERSE_SOURCE_FACTS_INVALID")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_value(value: str) -> Any:
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                fail(f"Duplicate JSON member: {key}")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=pairs)


def snapshot_paths(manifest: str | Path) -> list[Path]:
    """Admit fixed snapshot descendants before any artifact content is read."""
    path = Path(manifest).expanduser().absolute()
    if path.is_dir():
        path /= "source_manifest.json"
    if path.is_symlink() or path.name != "source_manifest.json":
        fail("A non-symlink canonical source_manifest.json is required.")
    root = path.parent.resolve()
    path = root / path.name
    value = json_value(path.read_text(encoding="utf-8"))
    artifacts = value.get("artifacts") if isinstance(value, dict) else None
    if not isinstance(artifacts, dict) or set(artifacts) != set(SNAPSHOT_ARTIFACTS):
        fail("Snapshot must bind exactly the canonical source artifact set.")
    paths = [path]
    for key, relative in SNAPSHOT_ARTIFACTS.items():
        item = artifacts[key]
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            fail("Snapshot artifact path is missing.")
        candidate = Path(item["path"])
        expected = root / relative
        if not candidate.is_absolute() or candidate.absolute() != expected:
            fail("Snapshot artifact must use its fixed path beneath the source run.")
        if (
            any(
                part.is_symlink()
                for part in (candidate, *candidate.parents)
                if part != root and part.is_relative_to(root)
            )
            or candidate.resolve() != expected
        ):
            fail("Snapshot artifact symlinks or path escapes are forbidden.")
        paths.append(candidate)
    return paths


def number(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        fail("Native primitive numbers must be explicitly finite.")
    return float(value)


def vector(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        fail("Native primitive coordinates require explicit XYZ.")
    return [number(item) for item in value]


def close(left, right, *, tolerance=1e-8) -> bool:
    return len(left) == len(right) and all(
        abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right)
    )


def _normal(facts: dict) -> list[float]:
    normal = facts.get("normal")
    extrusion = facts.get("extrusion")
    if normal is None and extrusion is None:
        fail("Curve plane normal/extrusion is missing.")
    result = vector(normal if normal is not None else extrusion)
    if abs(math.sqrt(sum(value * value for value in result)) - 1.0) > 1e-10:
        fail("Curve plane normal must be an explicit unit vector.")
    if extrusion is not None and not close(result, vector(extrusion), tolerance=1e-12):
        fail("Curve normal and extrusion disagree.")
    return result


def primitive_spec(record: dict) -> dict:
    """Validate primitive facts; canonicalization alone permits missing fields."""
    from .cad2gis_v3.model import canonical_curve_facts, canonical_curve_fingerprint
    from ezdxf.math import OCS

    raw = record.get("curve_facts")
    if not isinstance(raw, dict) or not raw or not record.get("curve_fingerprint"):
        fail("Complete canonical curve facts and an explicit fingerprint are required.")
    required = {
        "schema_version",
        "coordinate_system",
        "primitive_type",
        "vertices_wcs",
        "bulges",
        "closed",
        "primitive_parameters",
    }
    if not required <= set(raw):
        fail("Native curve facts lack required explicit fields.")
    facts = canonical_curve_facts(raw)
    if canonical_curve_fingerprint(facts) != record["curve_fingerprint"]:
        fail("Native curve fingerprint differs from canonical facts.")
    kind = facts["primitive_type"].upper()
    original_kind = record.get("dwg_type_name", "").upper()
    if kind not in SUPPORTED or (
        kind != original_kind
        and not (
            original_kind == "ARC" and kind == "CIRCLE" and facts["closed"] is True
        )
    ):
        fail(f"Unsupported or conflicting primitive type: {original_kind}/{kind}.")
    points = [vector(value) for value in raw["vertices_wcs"]]
    bulges = [number(value) for value in raw["bulges"]]
    if len(points) != len(bulges) or not isinstance(raw["closed"], bool):
        fail("Native curve vertex, bulge, or closure facts are invalid.")
    parameters = raw["primitive_parameters"]
    spec = {"type": kind}
    length = None
    if kind == "LINE":
        if (
            len(points) != 2
            or any(bulges)
            or facts["closed"]
            or close(points[0], points[1], tolerance=0)
        ):
            fail(
                "LINE requires two distinct XYZ endpoints, zero bulges, and open closure."
            )
        spec["vertices"] = points
        length = math.dist(*points)
    elif kind == "LWPOLYLINE":
        if len(points) < (3 if facts["closed"] else 2):
            fail("LWPOLYLINE has too few vertices.")
        normal = _normal(facts)
        ocs = OCS(normal)
        local = [list(ocs.from_wcs(point)) for point in points]
        elevation = local[0][2]
        if any(abs(point[2] - elevation) > 1e-8 for point in local):
            fail("LWPOLYLINE vertices are not in the declared OCS plane.")
        if (
            facts.get("elevation") is None
            or abs(number(facts["elevation"]) - elevation) > 1e-8
        ):
            fail("LWPOLYLINE elevation is missing or conflicts with its WCS vertices.")
        if any(bulges):
            system = parameters.get("bulge_coordinate_system")
            if system == "WCS" and close(normal, [0, 0, -1], tolerance=1e-12):
                bulges = [-value for value in bulges]
            elif system not in ("OCS", "WCS") and not close(
                normal, [0, 0, 1], tolerance=1e-12
            ):
                fail("Bulge orientation is ambiguous without an explicit OCS contract.")
            elif system == "WCS" and not close(normal, [0, 0, 1], tolerance=1e-12):
                fail("Tilted WCS bulge orientation is unsupported.")
        spec.update(
            vertices=points,
            ocs_vertices=[point[:2] for point in local],
            bulges=bulges,
            elevation=elevation,
            normal=normal,
            closed=facts["closed"],
        )
        length = 0.0
        segment_count = len(points) if facts["closed"] else len(points) - 1
        for index in range(segment_count):
            chord = math.dist(points[index], points[(index + 1) % len(points)])
            bulge = bulges[index]
            if chord == 0 and bulge:
                fail("A bulged segment has coincident endpoints.")
            length += (
                chord
                if not bulge
                else chord * (1 + bulge * bulge) / abs(bulge) * abs(math.atan(bulge))
            )
    elif kind in {"CIRCLE", "ARC"}:
        normal = _normal(facts)
        center = vector(parameters.get("center_wcs"))
        if "center" in parameters and not close(
            center, vector(parameters["center"]), tolerance=1e-12
        ):
            fail("Curve center aliases disagree.")
        radius = number(parameters.get("radius"))
        if radius <= 0:
            fail("Circle/arc radius must be positive.")
        spec.update(center=center, radius=radius, normal=normal)
        if kind == "CIRCLE":
            if not facts["closed"]:
                fail("CIRCLE must be declared closed.")
            length = math.tau * radius
        else:
            degrees = {"start_angle_degrees", "end_angle_degrees"}
            radians = {"start_angle", "end_angle"}
            if degrees <= set(parameters):
                angles = [
                    number(parameters[name])
                    for name in ("start_angle_degrees", "end_angle_degrees")
                ]
                if radians & set(parameters):
                    fail("ARC mixes angle-unit contracts.")
            elif radians <= set(parameters) and not degrees & set(parameters):
                angles = [
                    math.degrees(number(parameters[name]))
                    for name in ("start_angle", "end_angle")
                ]
            else:
                fail("ARC requires explicit start/end angles with one unit convention.")
            span = (angles[1] - angles[0]) % 360
            if span == 0 or facts["closed"]:
                fail("ARC requires a nonzero open angular span.")
            spec["angles_degrees"] = angles
            length = radius * math.radians(span)
            expected_endpoints = []
            local_center = OCS(normal).from_wcs(center)
            for angle in angles:
                t = math.radians(angle)
                expected_endpoints.append(
                    list(
                        OCS(normal).to_wcs(
                            [
                                local_center.x + radius * math.cos(t),
                                local_center.y + radius * math.sin(t),
                                local_center.z,
                            ]
                        )
                    )
                )
            anchors = parameters.get("source_endpoints_wcs", points)
            if anchors and (
                len(anchors) != 2
                or any(
                    not close(vector(a), b) for a, b in zip(anchors, expected_endpoints)
                )
            ):
                fail("ARC angle/radius facts conflict with its source endpoints.")
    else:
        normal = _normal(facts)
        center = vector(parameters.get("center_wcs"))
        axis = vector(parameters.get("major_axis"))
        ratio = number(parameters.get("radius_ratio"))
        start, end = (
            number(parameters.get("start_parameter")),
            number(parameters.get("end_parameter")),
        )
        magnitude = math.sqrt(sum(value * value for value in axis))
        if (
            magnitude <= 0
            or abs(sum(a * b for a, b in zip(axis, normal))) > 1e-10 * magnitude
            or not 0 < ratio <= 1
            or not 0 < end - start <= math.tau + 1e-12
        ):
            fail("ELLIPSE has invalid axis, plane, ratio, or parameter span.")
        spec.update(
            center=center,
            major_axis=axis,
            ratio=ratio,
            start_param=start,
            end_param=end,
            normal=normal,
        )
    if facts.get("native_length") is not None:
        reported = number(facts["native_length"])
        if (
            reported < 0
            or length is not None
            and abs(length - reported) > max(1e-6, 1e-9 * max(length, reported))
        ):
            fail("Native length conflicts with primitive parameters.")
    return spec


def load_snapshot(manifest: str | Path) -> tuple[Path, dict, list[dict], dict]:
    from .cad2gis_v3.source_query import validate_source_snapshot
    from .cad2gis_v3.source_replay import reader_identity, REPLAY_SCHEMA
    from .cad2gis_v3.model import SourceEntity, canonical_curve_fingerprint
    from .cad2gis_v3.units import resolve_insunits
    from .reverse import _check_source_idle

    path = snapshot_paths(manifest)[0]
    value = json_value(path.read_text(encoding="utf-8"))
    before = digest(path)
    root, checked = validate_source_snapshot(path.parent)
    if checked != value or value.get("pipeline_boundary") != "source_facts_only":
        fail("Source manifest changed or crosses the source-facts boundary.")
    provenance = value["reader_provenance"]
    if (
        provenance.get("mode") != "native_reader"
        or provenance.get("authority") != "reader_complete"
        or provenance.get("replay_schema") != REPLAY_SCHEMA
        or provenance.get("reader_identity") != reader_identity()
        or provenance.get("diagnostics", {}).get("inventory_complete") is not True
        or provenance.get("diagnostics", {}).get("skipped_rows", 0)
    ):
        fail(
            "A current complete native-reader snapshot is required; export-source again."
        )
    if value.get("coordinate_reference", {}).get("transformed") is not False:
        fail("Source snapshot native coordinates were transformed.")
    records = [
        json_value(line)
        for line in Path(value["artifacts"]["reader_records"]["path"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    if len(records) != value["entity_count"] or any(
        not isinstance(row, dict)
        or not row.get("entity_key")
        or row.get("source_sha256") != value["source"]["sha256"]
        for row in records
    ):
        fail("Source snapshot row count or entity source binding is invalid.")
    if len({row["entity_key"] for row in records}) != len(records):
        fail("Source entity keys are duplicated.")
    gpkg = Path(value["artifacts"]["source_gpkg"]["path"])
    _check_source_idle(gpkg)
    connection = sqlite3.connect(gpkg.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        materialized = {}
        for table in (
            "source_points",
            "source_lines",
            "source_polygons",
            "source_text",
            "source_blocks",
            "source_metadata",
        ):
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                if row["entity_key"] in materialized:
                    fail("Source GeoPackage repeats an entity key.")
                materialized[row["entity_key"]] = dict(row)
        if set(materialized) != {row["entity_key"] for row in records}:
            fail("Source GeoPackage and reader inventory entity keys disagree.")
        for raw in records:
            row = materialized[raw["entity_key"]]
            entity = SourceEntity.from_record(raw)
            for key, expected in {
                "source_sha256": entity.source_sha256,
                "cad_handle": entity.handle,
                "cad_layout": entity.layout,
                "dwg_layer": entity.layer,
                "dwg_type": entity.dwg_type,
                "curve_fingerprint": entity.curve_fingerprint,
                "text": entity.text,
                "aci_color": entity.style.aci_color,
                "true_color": entity.style.true_color,
                "lineweight": entity.style.lineweight,
                "rotation": entity.style.rotation,
                "entity_aci_color": entity.style.entity_aci_color,
                "layer_aci_color": entity.style.layer_aci_color,
                "entity_true_color": entity.style.entity_true_color,
                "layer_true_color": entity.style.layer_true_color,
                "entity_lineweight": entity.style.entity_lineweight,
                "layer_lineweight": entity.style.layer_lineweight,
                "entity_linetype": entity.style.entity_linetype,
                "layer_linetype": entity.style.layer_linetype,
            }.items():
                if row[key] != expected:
                    fail(
                        f"Source reader/GeoPackage facts disagree for {raw['entity_key']}: {key}."
                    )
            if (
                canonical_curve_fingerprint(json_value(row["curve_facts"]))
                != entity.curve_fingerprint
            ):
                fail("GeoPackage curve facts disagree with canonical reader facts.")
            # Native reader JSONL can omit its fingerprint; source_gpkg is the
            # canonical ingestion artifact that explicitly stores the computed
            # fingerprint. Require that existing field, then expose it together
            # with the independently compared reader facts, never invent it.
            if entity.curve_facts and not row["curve_fingerprint"]:
                fail("Canonical source GeoPackage curve fingerprint is missing.")
            raw["curve_fingerprint"] = row["curve_fingerprint"]
    finally:
        connection.close()
    declarations = []
    for record in records:
        if record.get("dwg_type_name") == "DOCUMENT_METADATA":
            declarations.extend(
                int(value)
                for value in re.findall(
                    r"(?:^|;)INSUNITS=([0-9]+)(?=;|$)", record.get("text", "")
                )
            )
    if not declarations or len(set(declarations)) != 1 or declarations[0] == 0:
        fail("Native snapshot has missing, conflicting, or unitless INSUNITS evidence.")
    insertion = resolve_insunits(declarations[0]).to_manifest_dict()
    units = {
        "dxf_insunits": declarations[0],
        "insertion_unit": insertion,
        "coordinate_scale_inferred": False,
        "numeric_coordinate_policy": "preserve_native_xyz",
        "authority": "source_DOCUMENT_METADATA_INSUNITS",
    }
    assert_snapshot(path, before, value)
    return path, value, records, units


def assert_snapshot(path: Path, before: str, manifest: dict) -> None:
    from .reverse import _check_source_idle

    if digest(path) != before:
        fail("Source snapshot manifest changed during reproduction/verification.")
    for artifact in manifest["artifacts"].values():
        candidate = Path(artifact["path"])
        _check_source_idle(candidate)
        if digest(candidate) != artifact["sha256"]:
            fail("Source snapshot artifact changed during reproduction/verification.")


def select_records(
    records: list[dict], entity_types, layout: str
) -> tuple[list[dict], dict]:
    if (
        not isinstance(layout, str)
        or not layout.strip()
        or layout not in {row.get("layout") for row in records}
    ):
        fail("Select an existing source CAD layout explicitly.")
    if layout.upper().startswith("BLOCKDEF:"):
        fail(
            "Block definition frames require block hierarchy reconstruction; choose a drawing layout."
        )
    selected_types = None
    if entity_types is not None:
        if (
            isinstance(entity_types, (str, bytes))
            or not isinstance(entity_types, (list, tuple))
            or not entity_types
            or any(not isinstance(value, str) for value in entity_types)
        ):
            fail("entity_types must be a nonempty list of supported primitive names.")
        selected_types = [value.upper() for value in entity_types]
        if (
            len(set(selected_types)) != len(selected_types)
            or set(selected_types) - SUPPORTED
        ):
            fail("entity_types contains duplicate or unsupported primitive names.")
    selected = [
        row
        for row in records
        if row.get("layout") == layout
        and (
            selected_types is None
            or row.get("dwg_type_name", "").upper() in selected_types
        )
    ]
    if not selected:
        fail("The explicit source selection is empty.")
    selected_keys = {row["entity_key"] for row in selected}
    exclusions = [
        {
            "entity_key": row["entity_key"],
            "entity_type": row.get("dwg_type_name"),
            "layout": row.get("layout"),
            "reason": "outside_selected_layout"
            if row.get("layout") != layout
            else "outside_explicit_entity_types",
        }
        for row in records
        if row["entity_key"] not in selected_keys
    ]
    return selected, {
        "layout": layout,
        "entity_types": selected_types,
        "source_entity_count": len(records),
        "selected_entity_count": len(selected),
        "excluded_entity_count": len(exclusions),
        "excluded_entities": exclusions,
        "source_layout_counts": dict(Counter(row.get("layout") for row in records)),
        "selected_type_counts": dict(
            Counter(row.get("dwg_type_name") for row in selected)
        ),
    }
