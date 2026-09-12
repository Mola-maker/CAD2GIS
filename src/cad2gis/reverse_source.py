"""Explicit native source-snapshot primitive reproduction, separate from GIS edits."""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from .cad2gis_v3.artifact_io import inherit_output_permissions
from .reverse_models import ReverseConversionError
from .reverse_source_reader import (
    MODE,
    SUPPORTED,
    assert_snapshot,
    close,
    digest,
    fail,
    json_value,
    load_snapshot,
    primitive_spec,
    select_records,
    snapshot_paths as snapshot_paths,
)

SCHEMA = "cad2gis.source-reproduction.v1"
APPID = "CAD2GIS_SOURCE"
LIMITATIONS = [
    "Reproduces selected native source primitives; GIS edits are not applied.",
    "Selected source-layout geometry is placed in a new drawing modelspace; layouts and block instances are not reconstructed.",
    "Polyline widths, thickness, custom linetype patterns, fonts, dimensions, hatches and other absent facts are not reconstructed.",
    "Layer lineweights reported as BYLAYER/BYBLOCK are represented by the DXF layer default; original metadata is retained in attributes.jsonl.",
    "INSUNITS is preserved as insertion-unit metadata; native coordinate scale and absolute accuracy are not inferred.",
]


def _color(value: Any) -> int | None:
    if value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        fail("Source true-color metadata must be explicit #RRGGBB or empty.")
    return int(value[1:], 16)


def _styles(row: dict) -> tuple[dict, dict]:
    from ezdxf.lldxf.validator import is_valid_lineweight

    result = []
    for prefix in ("entity_", "layer_"):
        color = row.get(prefix + "aci_color")
        lineweight = row.get(prefix + "lineweight")
        if (
            isinstance(color, bool)
            or not isinstance(color, int)
            or not 0 <= color <= (256 if prefix == "entity_" else 255)
        ):
            fail("Source ACI color metadata is missing or invalid.")
        if prefix == "layer_" and color == 0:
            fail("A source layer must have an explicit nonzero ACI color.")
        if (
            isinstance(lineweight, bool)
            or not isinstance(lineweight, int)
            or not is_valid_lineweight(lineweight)
        ):
            fail("Source lineweight metadata is missing or invalid.")
        true_color = _color(row.get(prefix + "true_color"))
        attributes = {
            "color": color,
            "lineweight": -3
            if prefix == "layer_" and lineweight in {-1, -2}
            else lineweight,
        }
        if true_color is not None:
            attributes["true_color"] = true_color
        source_linetype = row.get(prefix + "linetype")
        if not isinstance(source_linetype, str) or not source_linetype:
            fail("Source linetype name metadata is missing.")
        # Source snapshots preserve names but not custom pattern definitions.
        # Keep missing patterns in the sidecar, with an explicit rendering limit.
        attributes["linetype"] = (
            source_linetype
            if source_linetype.upper() in {"BYLAYER", "BYBLOCK", "CONTINUOUS"}
            else "Continuous"
        )
        result.append(attributes)
    return result[0], result[1]


def inspect_source_snapshot(manifest: str | Path) -> dict[str, Any]:
    path, source, records, units = load_snapshot(manifest)
    source_hash = digest(path)
    capabilities = []
    for row in records:
        try:
            spec = primitive_spec(row)
            _styles(row)
            reason, supported = None, True
            output_type = spec["type"]
        except (ValueError, TypeError, KeyError) as exc:
            reason, supported, output_type = str(exc), False, None
        capabilities.append(
            {
                "entity_key": row["entity_key"],
                "entity_type": row.get("dwg_type_name"),
                "layout": row.get("layout"),
                "supported": supported,
                "output_type": output_type,
                "reason": reason,
            }
        )
    assert_snapshot(path, source_hash, source)
    return {
        "schema": SCHEMA,
        "status": "inspected",
        "mode": MODE,
        "source_manifest": str(path),
        "source_manifest_sha256": source_hash,
        "snapshot_sha256": source["snapshot_sha256"],
        "source_dwg_sha256": source["source"]["sha256"],
        "source_entity_count": len(records),
        "source_layout_counts": dict(Counter(row.get("layout") for row in records)),
        "supported_entity_count": sum(item["supported"] for item in capabilities),
        "entities": capabilities,
        "units": units,
        "original_cad_restored": False,
        "absolute_accuracy_verified": False,
        "GIS_edits_applied": False,
        "limitations": LIMITATIONS,
    }


def _prepare(records, entity_types, layout):
    from .reverse_writer import _layer_names

    selected, selection = select_records(records, entity_types, layout)
    specs = []
    for row in selected:
        try:
            specs.append(primitive_spec(row))
            _styles(row)
        except (ValueError, KeyError, TypeError) as exc:
            fail(
                f"Selected entity {row['entity_key']} ({row.get('dwg_type_name')}) cannot be reproduced: {exc}. Select entity_types explicitly to exclude unsupported inventory."
            )
    names = [row.get("layer") for row in selected]
    if any(not isinstance(name, str) or not name.strip() for name in names):
        fail("Every selected primitive requires its explicit CAD layer name.")
    layer_map = _layer_names(names)
    layer_styles = {}
    for row in selected:
        style = _styles(row)[1]
        if row["layer"] in layer_styles and layer_styles[row["layer"]] != style:
            fail("Source layer style facts conflict between selected entities.")
        layer_styles[row["layer"]] = style
    return selected, specs, selection, layer_map, layer_styles


def _write_drawing(path, selected, specs, units, layer_map, layer_styles):
    import ezdxf
    from ezdxf.math import OCS

    document = ezdxf.new("R2018")
    document.units = units["dxf_insunits"]
    document.appids.new(APPID)
    for name, mapped in layer_map.items():
        document.layers.new(mapped, dxfattribs=dict(layer_styles[name]))
    space = document.modelspace()
    sidecar = []
    for row, spec in zip(selected, specs, strict=True):
        attributes = {**_styles(row)[0], "layer": layer_map[row["layer"]]}
        kind = spec["type"]
        if kind == "LINE":
            entity = space.add_line(*spec["vertices"], dxfattribs=attributes)
        elif kind == "LWPOLYLINE":
            entity = space.add_lwpolyline(
                [
                    (*point, bulge)
                    for point, bulge in zip(
                        spec["ocs_vertices"], spec["bulges"], strict=True
                    )
                ],
                format="xyb",
                close=spec["closed"],
                dxfattribs={
                    **attributes,
                    "elevation": spec["elevation"],
                    "extrusion": spec["normal"],
                },
            )
        elif kind in {"CIRCLE", "ARC"}:
            center = OCS(spec["normal"]).from_wcs(spec["center"])
            attributes["extrusion"] = spec["normal"]
            if kind == "CIRCLE":
                entity = space.add_circle(center, spec["radius"], dxfattribs=attributes)
            else:
                entity = space.add_arc(
                    center,
                    spec["radius"],
                    *spec["angles_degrees"],
                    dxfattribs=attributes,
                )
        else:
            entity = space.add_ellipse(
                spec["center"],
                major_axis=spec["major_axis"],
                ratio=spec["ratio"],
                start_param=spec["start_param"],
                end_param=spec["end_param"],
                dxfattribs={**attributes, "extrusion": spec["normal"]},
            )
        entity.set_xdata(
            APPID, [(1000, row["entity_key"]), (1000, row["curve_fingerprint"])]
        )
        sidecar.append(
            {
                "entity_key": row["entity_key"],
                "source_handle": row["handle"],
                "cad_handle": entity.dxf.handle,
                "source_record": row,
                "source_record_sha256": _record_hash(row),
                "output_entity_type": kind,
            }
        )
    document.saveas(path)
    return sidecar


def _record_hash(record):
    import hashlib

    return hashlib.sha256(
        json.dumps(
            record,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _style_omissions(records):
    omissions = []
    for row in records:
        for field in ("entity_linetype", "layer_linetype"):
            value = row[field]
            if value.upper() not in {"BYLAYER", "BYBLOCK", "CONTINUOUS"}:
                omissions.append(
                    {
                        "entity_key": row["entity_key"],
                        "field": field,
                        "source_value": value,
                        "output_value": "Continuous",
                        "reason": "custom_pattern_definition_not_available",
                    }
                )
        if row["layer_lineweight"] in {-1, -2}:
            omissions.append(
                {
                    "entity_key": row["entity_key"],
                    "field": "layer_lineweight",
                    "source_value": row["layer_lineweight"],
                    "output_value": -3,
                    "reason": "BYLAYER_BYBLOCK_not_valid_for_layer_table",
                }
            )
    return omissions


def _published_paths(value, stage, output):
    if isinstance(value, str) and value.startswith(str(stage)):
        return str(output) + value[len(str(stage)) :]
    if isinstance(value, dict):
        return {
            key: _published_paths(item, stage, output) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_published_paths(item, stage, output) for item in value]
    return value


def _compare_drawing(path, selected, specs, sidecar, units, layer_map, layer_styles):
    """Compare native DXF fields/OCS coordinates with independent source facts."""
    import ezdxf

    document = ezdxf.readfile(path)
    audit = document.audit()
    if (
        audit.errors
        or audit.fixes
        or document.units != units["dxf_insunits"]
        or document.dxfversion != "AC1032"
    ):
        fail(
            "Native reproduction has invalid structure or changed insertion-unit metadata."
        )
    if any(len(space) for space in document.layouts if space.name != "Model"):
        fail("Native reproduction contains unexpected paper-space entities.")
    entities = {}
    for entity in document.modelspace():
        if not entity.has_xdata(APPID):
            fail("Native reproduction contains an unbound CAD entity.")
        tags = entity.get_xdata(APPID)
        if (
            len(tags) != 2
            or any(tag.code != 1000 for tag in tags)
            or tags[0].value in entities
        ):
            fail("CAD source entity bindings are invalid or duplicated.")
        entities[tags[0].value] = (entity, tags[1].value)
    if len(sidecar) != len(selected) or set(entities) != {
        row["entity_key"] for row in selected
    }:
        fail(
            "Native reproduction entity/sidecar accounting differs from the source selection."
        )
    for name, style in layer_styles.items():
        table = document.layers.get(layer_map[name])
        if (
            table.is_off()
            or table.is_frozen()
            or table.dxf.get("plot", 1) != 1
            or table.transparency != 0
            or table.dxf.get("true_color", None) != style.get("true_color")
        ):
            fail(
                "Native reproduction layer visibility or true color differs from source facts."
            )
        for key, value in style.items():
            actual = getattr(table.dxf, key)
            if (str(actual).upper() if key == "linetype" else actual) != (
                str(value).upper() if key == "linetype" else value
            ):
                fail(
                    f"Native reproduction layer metadata differs from source facts: {name}/{key}: {actual!r} != {value!r}."
                )
    for row, spec, link in zip(selected, specs, sidecar, strict=True):
        if (
            link.get("entity_key") != row["entity_key"]
            or _record_hash(link.get("source_record")) != _record_hash(row)
            or link.get("source_record_sha256") != _record_hash(row)
            or link.get("source_handle") != row["handle"]
            or link.get("output_entity_type") != spec["type"]
        ):
            fail("Native reproduction sidecar differs from source records.")
        entity, fingerprint = entities[row["entity_key"]]
        if link.get("cad_handle") != entity.dxf.handle:
            fail("Native reproduction CAD handle differs from its sidecar binding.")
        if (
            fingerprint != row["curve_fingerprint"]
            or entity.dxftype() != spec["type"]
            or entity.dxf.layer != layer_map[row["layer"]]
        ):
            fail(
                "Native CAD type, layer, or curve fingerprint differs from its source binding."
            )
        expected_style = _styles(row)[0]
        if (
            entity.dxf.get("invisible", 0) != 0
            or entity.dxf.get("ltscale", 1) != 1
            or entity.dxf.get("transparency", None) is not None
            or entity.dxf.get("true_color", None) != expected_style.get("true_color")
        ):
            fail("Native entity visibility or true color differs from source facts.")
        for key, value in expected_style.items():
            actual = getattr(entity.dxf, key)
            if (str(actual).upper() if key == "linetype" else actual) != (
                str(value).upper() if key == "linetype" else value
            ):
                fail("Native entity style differs from source facts.")
        if entity.dxf.is_supported("thickness") and entity.dxf.get("thickness", 0) != 0:
            fail("Native entity introduced unrecorded thickness.")

        def same(a, b):
            if not close(a, b):
                fail(
                    f"Native parameters changed for {row['entity_key']} ({spec['type']})."
                )

        kind = spec["type"]
        if kind == "LINE":
            same(entity.dxf.start, spec["vertices"][0])
            same(entity.dxf.end, spec["vertices"][1])
        elif kind == "LWPOLYLINE":
            vertices = list(entity.vertices_in_wcs())
            if (
                len(vertices) != len(spec["vertices"])
                or entity.closed != spec["closed"]
            ):
                fail("Native polyline vertex count/closure changed.")
            for actual, expected in zip(vertices, spec["vertices"], strict=True):
                same(actual, expected)
            same([item[2] for item in entity.get_points("xyb")], spec["bulges"])
            same(entity.dxf.extrusion, spec["normal"])
            same([entity.dxf.elevation], [spec["elevation"]])
            if entity.dxf.get("const_width", 0) != 0 or any(
                a or b for a, b in entity.get_points("se")
            ):
                fail("Native polyline introduced unrecorded widths.")
        elif kind in {"CIRCLE", "ARC"}:
            same(entity.ocs().to_wcs(entity.dxf.center), spec["center"])
            same([entity.dxf.radius], [spec["radius"]])
            same(entity.dxf.extrusion, spec["normal"])
            if kind == "ARC":
                same(
                    [entity.dxf.start_angle, entity.dxf.end_angle],
                    spec["angles_degrees"],
                )
        else:
            same(entity.dxf.center, spec["center"])
            same(entity.dxf.major_axis, spec["major_axis"])
            same(entity.dxf.extrusion, spec["normal"])
            same(
                [entity.dxf.ratio, entity.dxf.start_param, entity.dxf.end_param],
                [spec["ratio"], spec["start_param"], spec["end_param"]],
            )
    return {
        "status": "verified",
        "native_parameters_verified": True,
        "entity_count": len(selected),
        "entity_types": dict(Counter(spec["type"] for spec in specs)),
        "coordinate_tolerance": 1e-8,
        "absolute_accuracy_verified": False,
        "original_cad_restored": False,
        "GIS_edits_applied": False,
    }


def reproduce_source_snapshot(
    manifest: str | Path,
    *,
    run_dir: str | Path,
    entity_types=None,
    layout: str = "Model",
    output_format: str = "DXF",
) -> dict:
    output = Path(run_dir).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ReverseConversionError(
            f"Output already exists: {output}", code="REVERSE_OUTPUT_EXISTS"
        )
    if not isinstance(output_format, str) or output_format.upper() not in {
        "DXF",
        "DWG",
    }:
        fail("output_format must be DXF or DWG.")
    output_format = output_format.upper()
    path, source, records, units = load_snapshot(manifest)
    source_hash = digest(path)
    selected, specs, selection, layer_map, layer_styles = _prepare(
        records, entity_types, layout
    )
    if output_format == "DWG":
        from .reverse_dwg import resolve_runtime

        resolve_runtime()
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / f".{output.name}.reverse-source.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ReverseConversionError(
            "Another source reproduction owns this output.", code="REVERSE_OUTPUT_BUSY"
        ) from exc
    os.close(descriptor)
    stage = None
    pending_error = None
    try:
        if output.exists() or output.is_symlink():
            raise ReverseConversionError(
                "Output appeared during preparation.", code="REVERSE_OUTPUT_EXISTS"
            )
        stage = Path(
            tempfile.mkdtemp(
                prefix=f".{output.name}.reverse-source-", dir=output.parent
            )
        )
        links = _write_drawing(
            stage / "drawing.dxf", selected, specs, units, layer_map, layer_styles
        )
        (stage / "attributes.jsonl").write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False)
                + "\n"
                for item in links
            ),
            encoding="utf-8",
        )
        verification = _compare_drawing(
            stage / "drawing.dxf",
            selected,
            specs,
            links,
            units,
            layer_map,
            layer_styles,
        )
        names = ["drawing.dxf", "attributes.jsonl"]
        native_backend = None
        if output_format == "DWG":
            from .reverse_dwg import convert_dxf_to_dwg, read_dwg_as_dxf

            native_backend = {
                "write": convert_dxf_to_dwg(
                    stage / "drawing.dxf", stage / "drawing.dwg"
                ),
                "readback": read_dwg_as_dxf(
                    stage / "drawing.dwg", stage / "native_readback.dxf"
                ),
            }
            verification = _compare_drawing(
                stage / "native_readback.dxf",
                selected,
                specs,
                links,
                units,
                layer_map,
                layer_styles,
            )
            names.extend(["drawing.dwg", "native_readback.dxf"])
            names.extend(path.name for path in sorted(stage.glob("*.log")))
        assert_snapshot(path, source_hash, source)
        report = {
            "schema": SCHEMA,
            "status": "converted",
            "mode": MODE,
            "run_dir": str(output),
            "output_format": output_format,
            "source_manifest": str(path),
            "source_manifest_sha256": source_hash,
            "snapshot_sha256": source["snapshot_sha256"],
            "source_dwg_sha256": source["source"]["sha256"],
            "source_gpkg_sha256": source["artifacts"]["source_gpkg"]["sha256"],
            "source_reader_provenance": source["reader_provenance"],
            "selection": selection,
            "units": units,
            "layer_map": layer_map,
            "layer_metadata": layer_styles,
            "verification": verification,
            "native_backend": _published_paths(native_backend, stage, output),
            "limitations": LIMITATIONS,
            "style_omissions": _style_omissions(selected),
            "layout_policy": "selected_source_layout_geometry_in_new_modelspace",
            "original_cad_restored": False,
            "absolute_accuracy_verified": False,
            "GIS_edits_applied": False,
            "artifacts": {
                name: {"path": str(output / name), "sha256": digest(stage / name)}
                for name in names
            },
        }
        (stage / "manifest.json").write_text(
            json.dumps(
                report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
            )
            + "\n",
            encoding="utf-8",
        )
        inherit_output_permissions(stage)
        if output.exists() or output.is_symlink():
            raise ReverseConversionError(
                "Output appeared before publication.", code="REVERSE_OUTPUT_EXISTS"
            )
        os.rename(stage, output)
        stage = None
        return report
    except BaseException as exc:
        pending_error = exc
        if stage is not None and str(getattr(exc, "code", "")).startswith(
            "REVERSE_DWG_"
        ):
            exc.retained_artifact_path = stage
            exc.details = {
                **getattr(exc, "details", {}),
                "run_published": False,
                "retained_stage": str(stage),
            }
            stage = (
                None  # Preserve the owned native-process workspace and its evidence.
            )
        raise
    finally:
        if stage is not None:
            try:
                shutil.rmtree(stage)
            except OSError as cleanup_error:
                if pending_error is None:
                    raise
                pending_error.retained_artifact_path = stage
                pending_error.add_note(
                    f"Unverified source workspace retained at {stage}: {cleanup_error}"
                )
        try:
            lock.unlink(missing_ok=True)
        except OSError as exc:
            if pending_error is not None:
                pending_error.cleanup_warning = str(exc)
            elif output.exists():
                exc.retained_artifact_path = output
                raise
            else:
                raise


def verify_source_reproduction(
    run_dir: str | Path,
    *,
    source_manifest: str | Path | None = None,
    scratch_parent: str | Path | None = None,
) -> dict:
    directory = Path(run_dir).expanduser().resolve()
    report_path = directory / "manifest.json"
    if report_path.is_symlink():
        fail("Source reproduction manifest symlinks are forbidden.")
    report_hash = digest(report_path)
    report = json_value(report_path.read_text(encoding="utf-8"))
    if (
        report.get("schema") != SCHEMA
        or report.get("status") != "converted"
        or report.get("mode") != MODE
    ):
        fail("Unsupported source reproduction manifest.")
    if any(
        report.get(key) is not False
        for key in (
            "original_cad_restored",
            "absolute_accuracy_verified",
            "GIS_edits_applied",
        )
    ):
        fail("Source reproduction manifest makes unsupported restoration claims.")
    path, source, records, units = load_snapshot(
        source_manifest or report["source_manifest"]
    )
    source_hash = digest(path)
    if (
        source_hash != report.get("source_manifest_sha256")
        or source["snapshot_sha256"] != report.get("snapshot_sha256")
        or source["source"]["sha256"] != report.get("source_dwg_sha256")
        or source["artifacts"]["source_gpkg"]["sha256"]
        != report.get("source_gpkg_sha256")
    ):
        fail("Source snapshot differs from the reproduction's recorded source.")
    if (
        report.get("source_reader_provenance") != source["reader_provenance"]
        or report.get("layout_policy")
        != "selected_source_layout_geometry_in_new_modelspace"
        or report.get("limitations") != LIMITATIONS
    ):
        fail(
            "Reproduction provenance or declared fidelity boundaries differ from the source contract."
        )
    selection = report["selection"]
    selected, specs, expected, layer_map, layer_styles = _prepare(
        records, selection["entity_types"], selection["layout"]
    )
    if (
        expected != selection
        or units != report.get("units")
        or layer_map != report.get("layer_map")
        or layer_styles != report.get("layer_metadata")
        or _style_omissions(selected) != report.get("style_omissions")
    ):
        fail(
            "Source reproduction selection, units, or layer metadata differs from source facts."
        )
    names = {"drawing.dxf", "attributes.jsonl"}
    if report.get("output_format") == "DWG":
        names.update({"drawing.dwg", "native_readback.dxf"})
    elif report.get("output_format") != "DXF":
        fail("Unsupported reproduction output format.")
    artifacts = report.get("artifacts")
    if (
        not isinstance(artifacts, dict)
        or not names <= set(artifacts)
        or any(
            not re.fullmatch(
                r"(?:drawing\.dwg|native_readback\.dxf)\.autocad-[0-9a-f]+\.log", name
            )
            for name in set(artifacts) - names
        )
    ):
        fail("Source reproduction artifact set is invalid.")
    names = set(artifacts)
    for name in names:
        candidate = directory / name
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or digest(candidate) != artifacts[name].get("sha256")
        ):
            fail(f"Reproduction artifact hash/path differs: {name}.")
    links = [
        json_value(line)
        for line in (directory / "attributes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    result = _compare_drawing(
        directory / "drawing.dxf",
        selected,
        specs,
        links,
        units,
        layer_map,
        layer_styles,
    )
    if report.get("verification") != result:
        fail("Recorded native verification metrics differ from the observed drawing.")
    if report["output_format"] == "DWG":
        from .reverse_dwg import read_dwg_as_dxf, native_readback_workspace

        _compare_drawing(
            directory / "native_readback.dxf",
            selected,
            specs,
            links,
            units,
            layer_map,
            layer_styles,
        )
        with native_readback_workspace(
            prefix="cad2gis-source-dwg-verify-",
            parent=Path(scratch_parent)
            if scratch_parent is not None
            else directory.parent,
        ) as temporary:
            fresh = temporary / "fresh.dxf"
            backend = read_dwg_as_dxf(directory / "drawing.dwg", fresh)
            result = {
                **_compare_drawing(
                    fresh, selected, specs, links, units, layer_map, layer_styles
                ),
                "native_readback": {
                    key: value
                    for key, value in backend.items()
                    if key not in {"output_path", "log_path"}
                },
                "fresh_readback_artifacts_retained": False,
            }
    assert_snapshot(path, source_hash, source)
    if digest(report_path) != report_hash or any(
        digest(directory / name) != artifacts[name]["sha256"] for name in names
    ):
        fail("Reproduction artifacts changed during verification.")
    return {
        **result,
        "schema": SCHEMA,
        "mode": MODE,
        "run_dir": str(directory),
        "manifest_sha256": report_hash,
        "source_manifest_sha256": source_hash,
        "snapshot_sha256": source["snapshot_sha256"],
    }


def register(commands: Any) -> None:
    inspect = commands.add_parser(
        "reverse-source-inspect",
        help="Inspect strict native source snapshot primitive capabilities.",
    )
    inspect.add_argument("manifest", type=Path)
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(
        handler=lambda args: (inspect_source_snapshot(args.manifest), 0)
    )
    convert = commands.add_parser(
        "reverse-source-convert",
        help="Reproduce explicitly selected native source primitives without applying GIS edits.",
    )
    convert.add_argument("manifest", type=Path)
    convert.add_argument("--run-dir", type=Path, required=True)
    convert.add_argument(
        "--entity-type", dest="entity_types", action="append", choices=sorted(SUPPORTED)
    )
    convert.add_argument("--layout", default="Model")
    convert.add_argument("--output-format", choices=("DXF", "DWG"), default="DXF")
    convert.add_argument("--json", action="store_true")
    convert.set_defaults(
        handler=lambda args: (
            reproduce_source_snapshot(
                args.manifest,
                run_dir=args.run_dir,
                entity_types=args.entity_types,
                layout=args.layout,
                output_format=args.output_format,
            ),
            0,
        )
    )
    verify = commands.add_parser(
        "reverse-source-verify",
        help="Independently verify native primitive parameters against the recorded snapshot.",
    )
    verify.add_argument("run_dir", type=Path)
    verify.add_argument("--source-manifest", type=Path)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(
        handler=lambda args: (
            verify_source_reproduction(
                args.run_dir, source_manifest=args.source_manifest
            ),
            0,
        )
    )
