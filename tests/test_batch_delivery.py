import hashlib
import json
import sqlite3
import zipfile
import xml.etree.ElementTree as ET

import pytest

from cad2gis.batch import SCHEMA, load_contract, run_batch
from cad2gis.delivery import package_delivery


def contract(tmp_path, drawings):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"schema_version": SCHEMA, "drawings": drawings}))
    return path


def item(identifier="drawing-01", source="one.dwg"):
    return {"id": identifier, "source": source, "project": "reviewed", "source_sha256": hashlib.sha256(b"input").hexdigest()}


@pytest.mark.parametrize("change", [{"id": "../escape"}, {"source": "../secret.dwg"}, {"source": "C:/input.dwg"}, {"project": "../reviewed"}, {"source_sha256": "missing"}])
def test_contract_rejects_nonportable_or_unbound_inputs(tmp_path, change):
    with pytest.raises(ValueError):
        load_contract(contract(tmp_path, [{**item(), **change}]))


def test_duplicate_ids_rejected_before_output(tmp_path):
    with pytest.raises(ValueError):
        run_batch(contract(tmp_path, [item(), item()]), tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize('fonts', ['fonts', ['../outside'], ['/outside'], ['C:/fonts']])
def test_svg_fonts_cannot_escape_input_bundle(tmp_path, fonts):
    with pytest.raises(ValueError):
        load_contract(contract(tmp_path, [{**item(), 'svg_mode': 'candidate', 'svg_font_dirs': fonts}]))


def test_failures_remain_visible_and_batch_is_append_only(tmp_path):
    path = contract(tmp_path, [item(), item("drawing-02", "two.dwg")])
    output = tmp_path / "output"
    result = run_batch(path, output)
    assert result["status"] == "FAILED"
    assert len(result["drawings"]) == 2
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "drawing-01" in page and "drawing-02" in page
    assert (output / "drawing-02" / "result.json").is_file()
    with pytest.raises(FileExistsError):
        run_batch(path, output)


def test_source_hash_mismatch_never_calls_conversion(tmp_path, monkeypatch):
    (tmp_path / "one.dwg").write_bytes(b"changed")
    monkeypatch.setattr("cad2gis.batch.subprocess.run", lambda *a, **k: pytest.fail("Unbound source executed"))
    result = run_batch(contract(tmp_path, [item()]), tmp_path / "output")
    assert "SHA256 mismatch" in result["drawings"][0]["error"]


@pytest.mark.parametrize("run_status", ["CONDITIONAL", "UNSAFE", "FAILED", "VERIFIED"])
def test_portable_package_preserves_database_and_attributes(tmp_path, run_status):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({"run_status": run_status, "source": {"sha256": "a" * 64}}))
    with sqlite3.connect(run / "delivery.gpkg") as db:
        db.executescript('''CREATE TABLE gpkg_geometry_columns(table_name,column_name,geometry_type_name,srs_id);
          INSERT INTO gpkg_geometry_columns VALUES('CABLE','geom','LINESTRING',3857);
          CREATE TABLE gpkg_contents(table_name,min_x,min_y,max_x,max_y);
          INSERT INTO gpkg_contents VALUES('CABLE',0,0,1,1);
          CREATE TABLE gpkg_spatial_ref_sys(srs_id,definition);
          INSERT INTO gpkg_spatial_ref_sys VALUES(3857,'');
          CREATE TABLE CABLE(fid INTEGER,geom BLOB,display_label TEXT,length_value_m REAL,length_source TEXT);
          INSERT INTO CABLE VALUES(1,NULL,'',12.5,'dwg_dimension');''')
    before = (run / "delivery.gpkg").read_bytes()
    manifest = json.loads((run / "run_manifest.json").read_text())
    manifest["artifacts"] = {"delivery": {"sha256": hashlib.sha256(before).hexdigest()}}
    candidates = b'{"mode":"candidate-only","accepted":false}'
    (run / "geometry_repair_candidates.json").write_bytes(candidates)
    manifest["artifacts"]["geometry_repair_candidates"] = {"sha256": hashlib.sha256(candidates).hexdigest()}
    manifest["semantic_revision"] = {"revision": 1, "accepted_run_id": None}
    (run / "run_manifest.json").write_text(json.dumps(manifest))
    visual = tmp_path / "visual"
    visual.mkdir()
    (visual / "report.json").write_text('{"absolute_accuracy_verified": false}')
    package_delivery(run, tmp_path / "delivery", audit_dir=visual)
    with zipfile.ZipFile(tmp_path / "delivery.zip") as archive:
        assert archive.read("delivery.gpkg") == before
        assert b"12.5" in archive.read("CABLE.csv")
        assert "delivery.qgz" in archive.namelist()
        assert archive.read("visual/report.json") == (visual / "report.json").read_bytes()
        assert archive.read("geometry_repair_candidates.json") == candidates
        assert json.loads(archive.read("delivery-manifest.json"))["semantic_revision"]["revision"] == 1
        assert json.loads(archive.read("delivery-manifest.json"))["run_status"] == run_status
        assert f"Run status: **{run_status}**" in archive.read("README.md").decode()
    with zipfile.ZipFile(tmp_path / "delivery" / "delivery.qgz") as archive:
        xml = archive.read("delivery.qgs").decode()
        assert "./delivery.gpkg|layername=CABLE" in xml
        assert str(tmp_path) not in xml
        root = ET.fromstring(xml)
        assert root.find('ProjectViewSettings/DefaultViewExtent/spatialrefsys') is not None
        assert root.find('ProjectViewSettings/DefaultViewExtent/crs') is None
    (run / "geometry_repair_candidates.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Geometry repair candidates"):
        package_delivery(run, tmp_path / "tampered-package")


def test_readonly_sqlite_without_extension_api(tmp_path, monkeypatch):
    from cad2gis.cad2gis_v3 import source_query

    class Connection:
        def __init__(self):
            self.commands = []

        def execute(self, sql):
            self.commands.append(sql)

    connection = Connection()
    monkeypatch.setattr(source_query.sqlite3, "connect", lambda *a, **k: connection)
    assert source_query._readonly(tmp_path / "source.sqlite") is connection
    assert connection.commands == ["PRAGMA query_only=ON"]


def test_numeric_thread_budget_preserves_operator_override(monkeypatch):
    import os
    from cad2gis.runtime import configure_numeric_threads
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.setenv("MKL_NUM_THREADS", "4")
    configure_numeric_threads()
    assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
    assert os.environ["OMP_NUM_THREADS"] == "1"
    assert os.environ["MKL_NUM_THREADS"] == "4"


def test_debug_runtime_does_not_call_pythonpath_checkout_an_installed_wheel(tmp_path, monkeypatch):
    from cad2gis import runtime
    source = tmp_path / "src"
    location = str(source / "cad2gis/cad2gis_v3/__init__.py")
    monkeypatch.setattr(runtime, "_configured_roots", lambda: iter(()))
    monkeypatch.setattr(runtime, "_editable_backend_root", lambda: source)
    monkeypatch.setattr(runtime, "_importable_backend_location", lambda: location)
    assert runtime.backend_deployment() == {"mode": "editable_checkout", "location": location}


def test_qgis_labels_have_positions_and_coverage_does_not_hide_assets():
    from cad2gis.cad2gis_v3.styles import _qml
    area = ET.fromstring(_qml("SITE", "Polygon", []))
    cable = ET.fromstring(_qml("CABLE", "LineString", []))
    assert area.find(".//rendering").get("obstacle") == "0"
    assert int(cable.find(".//placement").get("placementFlags")) & 2


@pytest.mark.parametrize("raw, expected", [(None, ""), (0, "#000000"),
    (0xC28000A0, "#8000A0"), (-0x3D7FFF60, "#8000A0"), (0xC86432, "#C86432")])
def test_reader_true_color_is_portable_rgb(raw, expected):
    from cad2gis.reader.libredwg_cli import _true_color_hex, _layer_style
    from types import SimpleNamespace
    assert _true_color_hex(raw) == expected
    doc = SimpleNamespace(layers=SimpleNamespace(get=lambda _: SimpleNamespace(dxf=SimpleNamespace(true_color=raw))))
    assert _layer_style(doc, "test")["truecolor"] == expected


@pytest.mark.parametrize("partition_failure", [False, True])
def test_batch_subprocesses_follow_real_cli_source_contract(tmp_path, monkeypatch, partition_failure):
    from types import SimpleNamespace
    from cad2gis.cli import _parser, _source
    (tmp_path / "one.dwg").write_bytes(b"input")
    (tmp_path / "reviewed").mkdir()
    output = tmp_path / "output"
    seen = []

    def execute(command, **kwargs):
        if command[2] == "cad2gis":
            args = _parser().parse_args(command[3:])
            if args.command != "index-source":
                assert _source(args) == tmp_path / "one.dwg"
            if args.command == "convert":
                assert str(output) in str(args.project_dir)
                assert args.source_run == args.run_dir.parent / "source"
                args.run_dir.mkdir(parents=True)
                (args.run_dir / "run_manifest.json").write_text(json.dumps({"delivery_partitions": {"EMR28560": {}}}))
            seen.append(args.command)
        progress = json.loads((output / "batch-report.json").read_text(encoding="utf-8"))
        assert len(progress["drawings"]) == 2
        return SimpleNamespace(returncode=1 if partition_failure and "--partition" in command else 0)

    monkeypatch.setattr("cad2gis.batch.subprocess.run", execute)
    monkeypatch.setattr("cad2gis.delivery.package_delivery", lambda *a, **kw: {"run_status": "CONDITIONAL"})
    result = run_batch(contract(tmp_path, [item(), item("drawing-02")]), output)
    assert result["status"] == ("FAILED" if partition_failure else "COMPLETED")
    assert result["drawings"][0]["partition_audits"]["EMR28560"] == ("FAILED" if partition_failure else "EXECUTED")
    assert seen == ["export-source", "index-source", "convert", "export-source", "index-source", "convert"]


def test_existing_run_report_copies_previews_and_preserves_source(tmp_path):
    from cad2gis.batch import report_runs
    run = tmp_path / "drawing" / "run"
    run.mkdir(parents=True)
    original = json.dumps({"run_status": "CONDITIONAL", "source": {"sha256": "a" * 64}}).encode()
    (run / "run_manifest.json").write_bytes(original)
    audit = run.parent / "audit"
    audit.mkdir()
    (audit / "report.json").write_text('{"status":"PASS"}')
    (audit / "source-delivery-focus.png").write_bytes(b"focus-preview")
    (audit / "source-delivery-overlay.png").write_bytes(b"full-preview")
    symbols = run / "svg-candidates" / "symbols"
    symbols.mkdir(parents=True)
    (symbols / "one.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>')
    output = tmp_path / "review"
    result = report_runs([run], output)
    item = result["drawings"][0]
    assert item["preview"].endswith("source-delivery-focus.png")
    assert item["png_count"] == 1
    assert item["status"] == "CONDITIONAL"
    assert result["absolute_accuracy_verified"] is False
    assert (output / "01-drawing/svg-candidates/symbols/one.png").read_bytes().startswith(b"\x89PNG")
    assert (run / "run_manifest.json").read_bytes() == original
    assert not (symbols / "one.png").exists()
    with pytest.raises(FileExistsError):
        report_runs([run], output)


def test_all_committed_baseline_configuration_is_valid_json():
    from pathlib import Path
    for path in (Path(__file__).parents[1] / "baselines").glob("*/config/*.json"):
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict), path
