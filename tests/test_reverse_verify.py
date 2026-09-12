"""Corruption probes that remain invalid even when artifact hashes are resealed."""

from __future__ import annotations

import hashlib
import json
import shutil

import ezdxf
import pytest

from cad2gis import cli, reverse
from cad2gis.reverse_models import ReverseConversionError
from cad2gis.reverse_verify import verify_reverse


@pytest.fixture
def exported(tmp_path):
    source = tmp_path / "source.geojson"
    geometries = [
        {"type": "Point", "coordinates": [1, 2, 9]},
        {"type": "LineString", "coordinates": [[0, 0], [3, 4], [6, 4]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                                                 [[2, 2], [2, 4], [4, 4], [4, 2], [2, 2]]]},
        {"type": "GeometryCollection", "geometries": [
            {"type": "Point", "coordinates": [20, 20]},
            {"type": "LineString", "coordinates": [[21, 20, 3], [22, 21, 7]]},
        ]},
    ]
    source.write_text(json.dumps({"type": "FeatureCollection", "name": "assets",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [{"type": "Feature", "id": str(i), "properties": {
            "text": "中文 {literal} \\ ^ %%d\nsecond", "style_aci": i + 1,
            "group": " A " if i % 2 else "A", "nested": {"verified": False, "value": i}},
            "geometry": geometry} for i, geometry in enumerate(geometries)]}), encoding="utf-8")
    run = tmp_path / "run"
    reverse.reverse_convert(source, run_dir=run, label_field="text", layer_field="group")
    return source, run


def _manifest(run):
    return json.loads((run / "manifest.json").read_text(encoding="utf-8"))


def _reseal(run, mutate=None):
    manifest = _manifest(run)
    for name, artifact in manifest["artifacts"].items():
        artifact["sha256"] = hashlib.sha256((run / name).read_bytes()).hexdigest()
    if mutate:
        mutate(manifest)
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _attributes(run):
    return [json.loads(line) for line in (run / "attributes.jsonl").read_text(encoding="utf-8").splitlines()]


def _save_attributes(run, records):
    (run / "attributes.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")


def test_verify_checks_real_vertices_holes_z_labels_and_attributes(exported, capsys):
    source, run = exported
    before = {file.name: file.read_bytes() for file in run.iterdir()}
    report = verify_reverse(run)
    assert report["status"] == "verified"
    assert report["feature_count"] == 4
    assert report["geometry_entity_count"] == 6
    assert report["label_count"] == 4
    assert report["vertex_count"] == 15
    assert report["max_coordinate_error"] == 0
    assert report["attributes_verified"] and report["labels_verified"]
    assert report["absolute_accuracy_verified"] is False
    assert before == {file.name: file.read_bytes() for file in run.iterdir()}
    assert cli.main(["reverse-verify", str(run), "--source", str(source), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"


@pytest.mark.parametrize("mutation", ["point_xy", "point_z", "line_vertex", "hole_open", "bulge", "width",
                                       "ocs", "layer", "label", "label_anchor", "label_height", "color", "polyline_z",
                                       "label_alignment", "label_wrap", "point_thickness", "point_extrusion",
                                       "polyline_thickness", "polyline_extrusion", "polyline_elevation",
                                       "polyline_fit", "vertex_fit", "invisible", "layer_hidden", "inline_font",
                                       "layer_color", "layer_linetype", "layer_lineweight", "layer_transparency"])
def test_resealed_cad_geometry_and_presentation_tampering_fails(exported, mutation):
    _, run = exported
    doc = ezdxf.readfile(run / "drawing.dxf")
    points = list(doc.modelspace().query("POINT"))
    lines = list(doc.modelspace().query("LWPOLYLINE"))
    labels = list(doc.modelspace().query("MTEXT"))
    if mutation == "point_xy":
        points[0].dxf.location = (101, 2, 9)
    elif mutation == "point_z":
        points[0].dxf.location = (1, 2, 0)
    elif mutation == "point_thickness":
        points[0].dxf.thickness = 7
    elif mutation == "point_extrusion":
        points[0].dxf.extrusion = (0, 1, 0)
    elif mutation.startswith("polyline_") and mutation != "polyline_z":
        polyline = list(doc.modelspace().query("POLYLINE"))[0]
        if mutation == "polyline_thickness":
            polyline.dxf.thickness = 7
        elif mutation == "polyline_extrusion":
            polyline.dxf.extrusion = (0, 1, 0)
        elif mutation == "polyline_elevation":
            polyline.dxf.elevation = (0, 0, 10)
        else:
            polyline.dxf.flags |= 2
    elif mutation == "vertex_fit":
        list(doc.modelspace().query("POLYLINE"))[0].vertices[0].dxf.flags |= 1
    elif mutation == "invisible":
        points[0].dxf.invisible = 1
    elif mutation == "layer_hidden":
        doc.layers.get(points[0].dxf.layer).off()
    elif mutation.startswith("layer_"):
        layer = doc.layers.get(points[0].dxf.layer)
        if mutation == "layer_color":
            layer.dxf.color = 1
        elif mutation == "layer_linetype":
            layer.dxf.linetype = "BYLAYER"
        elif mutation == "layer_lineweight":
            layer.dxf.lineweight = 50
        else:
            layer.transparency = 0.5
    elif mutation == "inline_font":
        labels[0].text = "\\H2x;" + labels[0].text
    elif mutation == "line_vertex":
        lines[0][1] = (4, 4, 0, 0, 0)
    elif mutation == "hole_open":
        lines[2].closed = False
    elif mutation == "bulge":
        lines[0][0] = (0, 0, 0, 0, 0.5)
    elif mutation == "width":
        lines[0].dxf.const_width = 2
    elif mutation == "ocs":
        lines[0].dxf.extrusion = (0, 0, -1)
    elif mutation == "layer":
        points[0].dxf.layer = "0"
    elif mutation == "label":
        labels[0].text = "tampered"
    elif mutation == "label_anchor":
        labels[0].dxf.insert = (100, 100, 0)
    elif mutation == "label_height":
        labels[0].dxf.char_height = 100
    elif mutation == "color":
        points[0].dxf.color = 7
    elif mutation == "label_alignment":
        labels[0].dxf.attachment_point = 9
    elif mutation == "label_wrap":
        labels[0].dxf.width = 1
    else:
        list(doc.modelspace().query("POLYLINE"))[0].vertices[0].dxf.location = (21, 20, 0)
    doc.saveas(run / "drawing.dxf")
    _reseal(run)
    with pytest.raises(ReverseConversionError) as error:
        verify_reverse(run)
    assert error.value.code == "REVERSE_VERIFICATION_FAILED"


@pytest.mark.parametrize("mutation", ["property", "hash", "handle_swap", "duplicate_handle", "duplicate_id", "drop_record"])
def test_resealed_sidecar_tampering_fails(exported, mutation):
    _, run = exported
    records = _attributes(run)
    if mutation == "property":
        records[0]["properties"]["nested"]["verified"] = 0
    elif mutation == "hash":
        records[0]["cad_geometry_sha256"] = "0" * 64
    elif mutation == "handle_swap":
        records[0]["entities"], records[1]["entities"] = records[1]["entities"], records[0]["entities"]
    elif mutation == "duplicate_handle":
        records[1]["entities"][0]["handle"] = records[0]["entities"][0]["handle"]
    elif mutation == "duplicate_id":
        records[1]["feature_id"] = records[0]["feature_id"]
    else:
        records.pop()
    _save_attributes(run, records)
    _reseal(run)
    with pytest.raises(ReverseConversionError):
        verify_reverse(run)


def test_unaccounted_entity_fails_even_with_resealed_counts(exported):
    _, run = exported
    doc = ezdxf.readfile(run / "drawing.dxf")
    doc.modelspace().add_point((1, 2, 3))
    doc.saveas(run / "drawing.dxf")
    _reseal(run, lambda manifest: manifest.update(entity_count=manifest["entity_count"] + 1))
    with pytest.raises(ReverseConversionError, match="unaccounted"):
        verify_reverse(run)


def test_unaccounted_paper_space_geometry_fails(exported):
    _, run = exported
    doc = ezdxf.readfile(run / "drawing.dxf")
    doc.layout().add_line((0, 0), (100, 100))
    doc.saveas(run / "drawing.dxf")
    _reseal(run)
    with pytest.raises(ReverseConversionError, match="paper-space"):
        verify_reverse(run)


def test_changed_source_with_resealed_hash_still_fails_attribute_comparison(exported):
    source, run = exported
    data = json.loads(source.read_text())
    data["features"][0]["properties"]["text"] = "new source data"
    source.write_text(json.dumps(data), encoding="utf-8")
    _reseal(run, lambda manifest: manifest.update(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    with pytest.raises(ReverseConversionError, match="properties differs"):
        verify_reverse(run)


def test_relocated_run_and_source_do_not_follow_old_artifact_paths(exported, tmp_path):
    source, run = exported
    moved = tmp_path / "moved"
    shutil.copytree(run, moved)
    relocated_source = tmp_path / "relocated.geojson"
    shutil.copyfile(source, relocated_source)
    # An artifact path is informational, never a read authorization.
    _reseal(moved, lambda manifest: manifest["artifacts"]["drawing.dxf"].update(path="C:/outside.dxf"))
    assert verify_reverse(moved, source=relocated_source)["status"] == "verified"


@pytest.mark.parametrize("value", [True, "2.5", None, -1, float("inf")])
def test_malformed_manifest_options_fail_with_stable_error(exported, value):
    _, run = exported
    _reseal(run, lambda manifest: manifest["export_options"].update(label_height=value))
    with pytest.raises(ReverseConversionError) as error:
        verify_reverse(run)
    assert error.value.code == "REVERSE_VERIFICATION_FAILED"


def test_fresh_export_verifies_before_publication(tmp_path, monkeypatch):
    from cad2gis import reverse_writer

    source = tmp_path / "point.geojson"
    source.write_text(json.dumps({"type": "Feature", "properties": {},
                                 "geometry": {"type": "Point", "coordinates": [1, 2]}}))
    real_writer = reverse_writer.write_dxf

    def corrupt(features, destination, **kwargs):
        report = real_writer(features, destination, **kwargs)
        doc = ezdxf.readfile(destination)
        list(doc.modelspace())[0].dxf.location = (100, 200, 0)
        doc.saveas(destination)
        return report

    monkeypatch.setattr(reverse_writer, "write_dxf", corrupt)
    with pytest.raises(ReverseConversionError, match="coordinate error"):
        reverse.reverse_convert(source, run_dir=tmp_path / "run", source_crs="EPSG:3857")
    assert list(tmp_path.iterdir()) == [source]
