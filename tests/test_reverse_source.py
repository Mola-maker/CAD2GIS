"""Native primitive reproduction is source-bound, explicit, and independently checked."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import ezdxf
import pytest

from cad2gis import reverse_source as api
from cad2gis.reverse_source_reader import primitive_spec
from cad2gis.reverse_models import ReverseConversionError
from cad2gis.cad2gis_v3 import source_export
from cad2gis.cad2gis_v3.model import canonical_curve_facts, canonical_curve_fingerprint
from cad2gis.reader.libredwg_cli import DWGRecordInventory


def facts(
    kind,
    vertices=(),
    *,
    normal=None,
    closed=False,
    parameters=None,
    bulges=None,
    elevation=None,
):
    return canonical_curve_facts(
        {
            "schema_version": "cad2gis-curve-facts-v1",
            "coordinate_system": "WCS",
            "primitive_type": kind,
            "vertices_wcs": list(vertices),
            "bulges": list(bulges) if bulges is not None else [0.0] * len(vertices),
            "normal": normal,
            "extrusion": normal,
            "closed": closed,
            "primitive_parameters": parameters or {},
            "elevation": elevation,
        }
    )


def snapshot(tmp_path, monkeypatch, *, include_text=False):
    source = tmp_path / "fixture.dwg"
    source.write_bytes(b"explicit native reader simulation")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    records = []
    values = [
        ("LINE", facts("LINE", [[0, 0, 2], [3, 4, 7]])),
        (
            "LWPOLYLINE",
            facts(
                "LWPOLYLINE",
                [[0, 0, 2], [5, 0, 2], [5, 5, 2]],
                normal=[0, 0, 1],
                bulges=[0.5, -0.25, 0],
                elevation=2,
                parameters={"bulge_coordinate_system": "OCS"},
            ),
        ),
        (
            "CIRCLE",
            facts(
                "CIRCLE",
                normal=[0, 0, 1],
                closed=True,
                parameters={"center_wcs": [2, 3, 4], "radius": 2},
            ),
        ),
        (
            "ARC",
            facts(
                "ARC",
                normal=[0, 0, 1],
                parameters={
                    "center_wcs": [1, 2, 3],
                    "radius": 4,
                    "start_angle": 0.25,
                    "end_angle": 2.5,
                },
            ),
        ),
        (
            "ELLIPSE",
            facts(
                "ELLIPSE",
                normal=[0, 0, -1],
                parameters={
                    "center_wcs": [10, 20, 30],
                    "major_axis": [-5, 0, 0],
                    "radius_ratio": 0.5,
                    "start_parameter": 0,
                    "end_parameter": math.tau,
                },
            ),
        ),
        ("DOCUMENT_METADATA", {}),
    ]
    if include_text:
        values.append(("TEXT", {}))
    for index, (kind, curve) in enumerate(values):
        layout = "DOCUMENT" if kind == "DOCUMENT_METADATA" else "Model"
        handle = format(index + 10, "X")
        points = [point[:2] for point in curve.get("vertices_wcs", [])]
        record = {
            "entity_key": hashlib.sha256(
                f"{source_hash}|{handle}|{layout}".encode()
            ).hexdigest(),
            "source_sha256": source_hash,
            "source_file": source.name,
            "handle": handle,
            "dwg_type_name": kind,
            "object_name": "ACDB" + kind,
            "layout": layout,
            "layout_role": "model",
            "layer": "source",
            "cad_role": "model",
            "points": points,
            "centroid": points[0] if points else [0, 0],
            "closed": curve.get("closed", False),
            "curve_facts": curve,
            "curve_fingerprint": canonical_curve_fingerprint(curve),
            "text": "INSUNITS=6;CGEOCS="
            if kind == "DOCUMENT_METADATA"
            else "label"
            if kind == "TEXT"
            else "",
            "aci_color": 3,
            "true_color": "#123456",
            "lineweight": 30,
            "rotation": 0,
            "entity_aci_color": 3,
            "entity_true_color": "#123456",
            "entity_lineweight": 30,
            "entity_linetype": "Continuous",
            "layer_aci_color": 7,
            "layer_true_color": "",
            "layer_lineweight": -1,
            "layer_linetype": "Continuous",
        }
        records.append(record)
    native = DWGRecordInventory(
        records,
        diagnostics={
            "inventory_complete": True,
            "skipped_rows": 0,
            "backend": "test_native_port",
        },
    )
    monkeypatch.setattr(source_export, "_extract_records", lambda _: native)
    root = tmp_path / "snapshot"
    source_export.export_source(source=source, run_dir=root)
    return root / "source_manifest.json", records


def reseal_source(path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"].values():
        artifact["sha256"] = api.digest(Path(artifact["path"]))
    manifest["snapshot_sha256"] = source_export.snapshot_digest(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def reseal_output(root):
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for name, artifact in manifest["artifacts"].items():
        artifact["sha256"] = api.digest(root / name)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_real_dxf_native_primitive_parameters_xyz_styles_and_sidecar(
    tmp_path, monkeypatch
):
    manifest, records = snapshot(tmp_path, monkeypatch)
    report = api.reproduce_source_snapshot(manifest, run_dir=tmp_path / "output")
    verified = api.verify_source_reproduction(tmp_path / "output")
    assert verified["status"] == "verified"
    assert verified["entity_count"] == 5
    assert set(verified["entity_types"]) == {
        "LINE",
        "LWPOLYLINE",
        "ARC",
        "CIRCLE",
        "ELLIPSE",
    }
    assert verified["original_cad_restored"] is False
    assert verified["GIS_edits_applied"] is False
    assert report["selection"]["excluded_entity_count"] == 1
    assert len(report["style_omissions"]) == 5
    doc = ezdxf.readfile(tmp_path / "output/drawing.dxf")
    line = next(iter(doc.modelspace().query("LINE")))
    assert tuple(line.dxf.end) == pytest.approx((3, 4, 7))
    assert line.dxf.true_color == 0x123456
    assert len(api.snapshot_paths(manifest)) == 7


def test_unsupported_model_inventory_requires_explicit_selection(tmp_path, monkeypatch):
    manifest, records = snapshot(tmp_path, monkeypatch, include_text=True)
    inspection = api.inspect_source_snapshot(manifest)
    assert inspection["supported_entity_count"] == 5
    with pytest.raises(ReverseConversionError, match="Select entity_types"):
        api.reproduce_source_snapshot(manifest, run_dir=tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
    report = api.reproduce_source_snapshot(
        manifest, run_dir=tmp_path / "selected", entity_types=["LINE"]
    )
    assert report["selection"]["selected_entity_count"] == 1
    assert report["selection"]["excluded_entity_count"] == 6


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("LINE", "end", (3, 4, 7.1)),
        ("CIRCLE", "radius", 2.1),
        ("ARC", "end_angle", 15),
        ("ELLIPSE", "ratio", 0.6),
        ("LINE", "thickness", 4),
    ],
)
def test_resealed_native_geometry_tampering_fails(
    tmp_path, monkeypatch, kind, field, value
):
    manifest, _ = snapshot(tmp_path, monkeypatch)
    output = tmp_path / "output"
    api.reproduce_source_snapshot(manifest, run_dir=output)
    doc = ezdxf.readfile(output / "drawing.dxf")
    entity = next(iter(doc.modelspace().query(kind)))
    setattr(entity.dxf, field, value)
    doc.saveas(output / "drawing.dxf")
    reseal_output(output)
    with pytest.raises(ReverseConversionError):
        api.verify_source_reproduction(output)


@pytest.mark.parametrize("tamper", ["bulge", "entity_key", "layer_metadata", "sidecar"])
def test_resealed_bindings_bulges_and_metadata_are_checked(
    tmp_path, monkeypatch, tamper
):
    manifest, _ = snapshot(tmp_path, monkeypatch)
    output = tmp_path / "output"
    api.reproduce_source_snapshot(manifest, run_dir=output)
    if tamper == "sidecar":
        path = output / "attributes.jsonl"
        rows = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
        rows[0]["source_record"]["layer"] = "changed"
        rows[0]["source_record_sha256"] = api._record_hash(rows[0]["source_record"])
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    else:
        doc = ezdxf.readfile(output / "drawing.dxf")
        if tamper == "bulge":
            entity = next(iter(doc.modelspace().query("LWPOLYLINE")))
            points = list(entity.get_points("xyb"))
            points[0] = (points[0][0], points[0][1], 0.7)
            entity.set_points(points, format="xyb")
        elif tamper == "entity_key":
            entity = next(iter(doc.modelspace()))
            entity.set_xdata(api.APPID, [(1000, "changed"), (1000, "f" * 64)])
        else:
            doc.layers.get("source").dxf.color = 1
        doc.saveas(output / "drawing.dxf")
    reseal_output(output)
    with pytest.raises(ReverseConversionError):
        api.verify_source_reproduction(output)


@pytest.mark.parametrize(
    "tamper",
    [
        "cad_handle",
        "record_boolean",
        "invisible",
        "layer_hidden",
        "layer_true_color",
        "layer_transparency",
    ],
)
def test_resealed_source_presentation_and_exact_sidecar_identity(
    tmp_path, monkeypatch, tamper
):
    manifest, _ = snapshot(tmp_path, monkeypatch)
    output = tmp_path / "output"
    api.reproduce_source_snapshot(manifest, run_dir=output)
    if tamper in {"cad_handle", "record_boolean"}:
        sidecar = output / "attributes.jsonl"
        rows = [
            json.loads(line)
            for line in sidecar.read_text(encoding="utf-8").splitlines()
        ]
        if tamper == "cad_handle":
            rows[0]["cad_handle"] = "FFFF"
        else:
            rows[0]["source_record"]["closed"] = (
                0  # False == 0 must not satisfy exact facts.
            )
        sidecar.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    else:
        drawing = ezdxf.readfile(output / "drawing.dxf")
        layer = drawing.layers.get("source")
        if tamper == "invisible":
            next(iter(drawing.modelspace())).dxf.invisible = 1
        elif tamper == "layer_hidden":
            layer.off()
        elif tamper == "layer_true_color":
            layer.dxf.true_color = 0xFF0000
        else:
            layer.transparency = 0.5
        drawing.saveas(output / "drawing.dxf")
    reseal_output(output)
    with pytest.raises(ReverseConversionError):
        api.verify_source_reproduction(output)


@pytest.mark.parametrize(
    "tamper", ["curve", "key", "units", "fingerprint", "entity_style", "layer_style"]
)
def test_resealed_snapshot_requires_reader_gpkg_consistency(
    tmp_path, monkeypatch, tamper
):
    manifest, _ = snapshot(tmp_path, monkeypatch)
    path = manifest.parent / "reader_records.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if tamper == "curve":
        rows[0]["curve_facts"]["vertices_wcs"][1][2] += 1
        rows[0]["curve_fingerprint"] = canonical_curve_fingerprint(
            rows[0]["curve_facts"]
        )
    elif tamper == "key":
        rows[0]["entity_key"] = "f" * 64
    elif tamper == "units":
        rows[-1]["text"] = "INSUNITS=2"
    elif tamper == "entity_style":
        rows[0]["entity_aci_color"] = 1
    elif tamper == "layer_style":
        rows[0]["layer_lineweight"] = 30
    else:
        rows[0]["curve_fingerprint"] = "f" * 64
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    reseal_source(manifest)
    with pytest.raises(ValueError):
        api.reproduce_source_snapshot(manifest, run_dir=tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "field",
    ["normal", "elevation", "bulges", "vertices_wcs", "closed", "primitive_parameters"],
)
def test_missing_primitive_facts_are_never_guessed(field):
    curve = facts(
        "LWPOLYLINE",
        [[0, 0, 1], [1, 0, 1]],
        normal=[0, 0, 1],
        elevation=1,
        bulges=[1, 0],
    )
    curve.pop(field)
    if field == "normal":
        curve.pop("extrusion")
    try:
        fingerprint = canonical_curve_fingerprint(curve)
    except ValueError:
        fingerprint = "f" * 64
    row = {
        "dwg_type_name": "LWPOLYLINE",
        "curve_facts": curve,
        "curve_fingerprint": fingerprint,
    }
    with pytest.raises(ReverseConversionError):
        primitive_spec(row)


def test_negative_ocs_bulge_and_tilted_line_xyz_are_preserved(tmp_path, monkeypatch):
    curve = facts(
        "LWPOLYLINE",
        [[0, 0, -2], [-3, 0, -2]],
        normal=[0, 0, -1],
        elevation=2,
        bulges=[0.5, 0],
        parameters={"bulge_coordinate_system": "OCS"},
    )
    spec = primitive_spec(
        {
            "dwg_type_name": "LWPOLYLINE",
            "curve_facts": curve,
            "curve_fingerprint": canonical_curve_fingerprint(curve),
        }
    )
    assert spec["bulges"] == [0.5, 0]
    assert spec["ocs_vertices"] == [[0, 0], [3, 0]]
    curve["primitive_parameters"] = {}
    with pytest.raises(ReverseConversionError, match="ambiguous"):
        primitive_spec(
            {
                "dwg_type_name": "LWPOLYLINE",
                "curve_facts": curve,
                "curve_fingerprint": canonical_curve_fingerprint(curve),
            }
        )


def test_unsafe_snapshot_artifact_path_rejected_before_hash_read(tmp_path, monkeypatch):
    manifest, _ = snapshot(tmp_path, monkeypatch)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["artifacts"]["reader_records"]["path"] = str(tmp_path / "outside.jsonl")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReverseConversionError, match="fixed path"):
        api.snapshot_paths(manifest)


def test_existing_output_and_acl_failure_protection(tmp_path, monkeypatch):
    manifest, _ = snapshot(tmp_path, monkeypatch)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("keep")
    with pytest.raises(ReverseConversionError):
        api.reproduce_source_snapshot(manifest, run_dir=existing)
    assert (existing / "keep").read_text() == "keep"

    def denied(_):
        raise PermissionError("ACL denied")

    monkeypatch.setattr(api, "inherit_output_permissions", denied)
    with pytest.raises(PermissionError):
        api.reproduce_source_snapshot(manifest, run_dir=tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".failed.reverse-source-*"))


def test_native_process_failure_preserves_workspace_and_typed_error(
    tmp_path, monkeypatch
):
    from cad2gis import reverse_dwg

    manifest, _ = snapshot(tmp_path, monkeypatch)
    monkeypatch.setattr(reverse_dwg, "resolve_runtime", lambda: Path("runtime"))

    def failed(source, output):
        (output.parent / "live-native-marker").write_text("running")
        error = ReverseConversionError(
            "process still running", code="REVERSE_DWG_TIMEOUT"
        )
        error.details = {"process_may_be_running": True}
        raise error

    monkeypatch.setattr(reverse_dwg, "convert_dxf_to_dwg", failed)
    with pytest.raises(ReverseConversionError) as caught:
        api.reproduce_source_snapshot(
            manifest, run_dir=tmp_path / "failed", output_format="DWG"
        )
    assert caught.value.code == "REVERSE_DWG_TIMEOUT"
    assert caught.value.details["run_published"] is False
    assert (caught.value.retained_artifact_path / "live-native-marker").is_file()
    assert not (tmp_path / "failed").exists()


def test_ambiguous_angle_units_and_conflicting_arc_endpoints_fail():
    curve = facts(
        "ARC",
        normal=[0, 0, 1],
        parameters={
            "center_wcs": [0, 0, 0],
            "radius": 1,
            "start_angle_degrees": 0,
            "end_angle_degrees": 90,
            "start_angle": 0,
            "end_angle": math.pi / 2,
        },
    )
    row = {
        "dwg_type_name": "ARC",
        "curve_facts": curve,
        "curve_fingerprint": canonical_curve_fingerprint(curve),
    }
    with pytest.raises(ReverseConversionError, match="angle-unit"):
        primitive_spec(row)
    del curve["primitive_parameters"]["start_angle"]
    del curve["primitive_parameters"]["end_angle"]
    curve["primitive_parameters"]["source_endpoints_wcs"] = [[0, 0, 0], [0, 1, 0]]
    row["curve_fingerprint"] = canonical_curve_fingerprint(curve)
    with pytest.raises(ReverseConversionError, match="endpoints"):
        primitive_spec(row)


def test_custom_style_omissions_are_explicit_per_entity(tmp_path, monkeypatch):
    manifest, records = snapshot(tmp_path, monkeypatch)
    row = copy.deepcopy(records[0])
    row["entity_linetype"] = "CUSTOM_PATTERN"
    omissions = api._style_omissions([row])
    assert {item["field"] for item in omissions} == {
        "entity_linetype",
        "layer_lineweight",
    }
    assert (
        next(item for item in omissions if item["field"] == "entity_linetype")[
            "source_value"
        ]
        == "CUSTOM_PATTERN"
    )


def test_source_changes_during_output_publication_are_rejected(tmp_path, monkeypatch):
    manifest, _ = snapshot(tmp_path, monkeypatch)
    original = api._write_drawing

    def change_source(*args, **kwargs):
        result = original(*args, **kwargs)
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + " ", encoding="utf-8"
        )
        return result

    monkeypatch.setattr(api, "_write_drawing", change_source)
    with pytest.raises(ReverseConversionError, match="changed"):
        api.reproduce_source_snapshot(manifest, run_dir=tmp_path / "output")
    assert not (tmp_path / "output").exists()


def simulated_dwg_output(tmp_path, monkeypatch):
    import shutil
    from cad2gis import reverse_dwg

    manifest, _ = snapshot(tmp_path, monkeypatch)
    monkeypatch.setattr(reverse_dwg, "resolve_runtime", lambda: Path("runtime"))

    def convert(source, output):
        output.write_bytes(b"AC1032" + b"simulation" * 40)
        return {"test_simulation": True}

    def readback(source, output):
        shutil.copyfile(source.parent / "drawing.dxf", output)
        return {"test_simulation": True}

    monkeypatch.setattr(reverse_dwg, "convert_dxf_to_dwg", convert)
    monkeypatch.setattr(reverse_dwg, "read_dwg_as_dxf", readback)
    output = tmp_path / "output"
    api.reproduce_source_snapshot(manifest, run_dir=output, output_format="DWG")
    return output


def test_retained_native_readback_is_semantically_checked(tmp_path, monkeypatch):
    output = simulated_dwg_output(tmp_path, monkeypatch)
    doc = ezdxf.readfile(output / "native_readback.dxf")
    next(iter(doc.modelspace().query("CIRCLE"))).dxf.radius = 9
    doc.saveas(output / "native_readback.dxf")
    reseal_output(output)
    with pytest.raises(ReverseConversionError, match="parameters changed"):
        api.verify_source_reproduction(output)


def test_verifier_retains_live_native_failure_inside_selected_scratch_root(
    tmp_path, monkeypatch
):
    from cad2gis import reverse_dwg

    output = simulated_dwg_output(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def live(source, destination):
        (destination.parent / "live-native-marker").write_text("running")
        error = ReverseConversionError("live child", code="REVERSE_DWG_TIMEOUT")
        error.details = {"process_may_be_running": True}
        raise error

    monkeypatch.setattr(reverse_dwg, "read_dwg_as_dxf", live)
    with pytest.raises(ReverseConversionError) as caught:
        api.verify_source_reproduction(output, scratch_parent=scratch)
    assert caught.value.retained_artifact_path.is_relative_to(scratch)
    assert (caught.value.retained_artifact_path / "live-native-marker").is_file()


def test_source_cleanup_failure_does_not_mask_original_conversion_error(
    tmp_path, monkeypatch
):
    manifest, _ = snapshot(tmp_path, monkeypatch)
    expected = ReverseConversionError(
        "source geometry failed", code="REVERSE_SOURCE_FACTS_INVALID"
    )

    def fail_write(path, *args):
        path.write_text("unverified partial output", encoding="utf-8")
        raise expected

    def fail_cleanup(path):
        raise PermissionError("owned artifact is still locked")

    monkeypatch.setattr(api, "_write_drawing", fail_write)
    monkeypatch.setattr(api.shutil, "rmtree", fail_cleanup)
    with pytest.raises(ReverseConversionError) as failure:
        api.reproduce_source_snapshot(manifest, run_dir=tmp_path / "failed")
    assert failure.value is expected
    assert (failure.value.retained_artifact_path / "drawing.dxf").is_file()
    assert not (tmp_path / "failed").exists()
