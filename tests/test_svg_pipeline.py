"""SVG extraction participates in the same published bundle as real GIS output."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from cad2gis import symbol_workflow
from cad2gis.cad2gis_v3 import pipeline, styles, units
from cad2gis.svg_delivery import _project_bindings
from test_crosscad_contracts import _run_fake_conversion


def _convert_svg_fixture(tmp_path, monkeypatch, *, fail_svg=False, partition_id="PANEL"):
    # Bound reader/classifier fixtures feed real delivery writers and the real
    # DXF -> SVG renderer. This does not replace an external DWG reader test.
    ezdxf = pytest.importorskip("ezdxf")
    source = tmp_path / "input.dxf"
    document = ezdxf.new()
    block = document.blocks.new("POLE")
    block.add_circle((0, 0), 1)
    points = [(12_600_000, -800_000), (12_605_000, -800_000)]
    handles = [document.modelspace().add_blockref("POLE", point).dxf.handle for point in points]
    document.saveas(source)
    originals = {name: getattr(pipeline, name) for name in (
        "DirectTransformer", "feature_adjustment_records", "enrich_delivery_metrics",
        "write_delivery", "write_styles", "write_source_gpkg", "_manifest_validation_summary",
    )}
    build_units, analyze_styles = units.build_unit_crs_contract, styles.analyze_style_coverage

    def setup(module, model, drawing):
        for name, value in originals.items():
            monkeypatch.setattr(module, name, value)
        monkeypatch.setattr(units, "build_unit_crs_contract", build_units)
        monkeypatch.setattr(styles, "analyze_style_coverage", analyze_styles)
        module.SourceProfile.load(drawing.parent / "source_profile.json").expectations.feature_counts = {"PTECH": 2}
        (drawing.parent / "delivery_partitions.json").write_text(json.dumps({"partitions": [
            {"region_id": partition_id, "label": "Independent panel", "bbox": [12_604_900, -800_100, 12_605_100, -799_900]},
        ]}), encoding="utf-8")
        features = [model.Feature(handle, "PTECH", "Point", [point], "source-line", handle, "FIBER",
            "source_observed", model.CadStyle(aci_color=1), display_label=handle,
            label_provenance="DWG_DIRECT:fixture") for handle, point in zip(handles, points)]
        monkeypatch.setattr(module, "classify_entities", lambda *_args, **_kwargs: (
            features, [], [], {"annotation_assignments_by_family": {}, "coverage": {"conversion_allowed": True}},
        ))
        return {"svg_mode": "candidate"}

    if fail_svg:
        def failed_inventory(_source, _databases, output, **_kwargs):
            output.mkdir()
            (output / "partial.svg").write_text("partial", encoding="utf-8")
            raise RuntimeError("SVG renderer failure fixture")
        monkeypatch.setattr(symbol_workflow, "_prepare", failed_inventory)
    return _run_fake_conversion(tmp_path, monkeypatch, configure=setup,
        source_bytes=source.read_bytes(), source_name="fixture.dxf")


@pytest.mark.parametrize("partition_id", ["PANEL", "fixture"])
def test_svg_bundle_binds_published_main_and_partition_databases(tmp_path, monkeypatch, partition_id):
    result, run_dir = _convert_svg_fixture(tmp_path, monkeypatch, partition_id=partition_id)
    manifest = json.loads(result.run_manifest_path.read_text(encoding="utf-8"))
    receipt = manifest["svg_candidates"]
    assert receipt == result.diagnostics["svg_candidates"]
    assert receipt["mode"] == "candidate" and receipt["status"] == "completed"
    assert receipt["auto_applied"] is False
    assert receipt["all_points_accounted_for"] == 2
    assert any(stage["stage"] == "svg_candidates" for stage in manifest["stage_contracts"]["stages"])
    output = run_dir / "svg-candidates"
    for relative, digest in receipt["files"].items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == digest
    for kind in ("report", "html", "store"):
        artifact = manifest["artifacts"][f"svg_candidates_{kind}"]
        assert artifact["path"] == receipt[kind]
        assert hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]
    report = json.loads(Path(receipt["report"]).read_text(encoding="utf-8"))
    databases = [run_dir / "delivery.gpkg", run_dir / partition_id / "delivery.gpkg"]
    assert set(report["input_databases_sha256"]) == {str(path) for path in databases}
    scopes = set(report["database_scopes"].values())
    assert len(scopes) == 2
    assert {point["database"] for point in report["points"]} == scopes
    assert ".stage." not in json.dumps(report)
    with sqlite3.connect(receipt["store"]) as db:
        statuses = dict(db.execute("SELECT symbol_id, status FROM symbols"))
        assert len(statuses) == 2
        assert {row[0] for row in db.execute("SELECT DISTINCT database_scope FROM correspondence")} == scopes
    for path in databases:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert report["input_databases_sha256"][str(path)] == digest
        assert len(_project_bindings(report, {"gpkg_sha256": digest}, statuses)) == 1


def test_svg_failure_does_not_publish_partial_gis_or_candidate_bundle(tmp_path, monkeypatch):
    previous = tmp_path / "runs" / "previous"
    previous.mkdir(parents=True)
    original = previous / "delivery.gpkg"
    original.write_bytes(b"previous immutable delivery")
    alias = previous.parent / "latest_verified.json"
    alias.write_text('{"previous":true}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="SVG renderer failure"):
        _convert_svg_fixture(tmp_path, monkeypatch, fail_svg=True)
    assert original.read_bytes() == b"previous immutable delivery"
    assert alias.read_text(encoding="utf-8") == '{"previous":true}'
    assert not (previous.parent / "fixture" / "run_manifest.json").exists()
    assert not list(previous.parent.glob(".fixture.stage.*"))
    assert not list(previous.parent.rglob("partial.svg"))
