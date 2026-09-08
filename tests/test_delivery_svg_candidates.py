"""Portable SVG candidates retain canonical checksums and review status."""
import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from cad2gis import delivery


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(run, manifest):
    (run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _seal_candidates(run, manifest):
    root = run / "svg-candidates"
    record = manifest["svg_candidates"]
    record["files"] = {path.relative_to(root).as_posix(): _sha(path)
                       for path in root.rglob("*") if path.is_file()}
    for name, relative in (("report", "correspondence.json"), ("html", "correspondence.html"),
                           ("store", "symbols.sqlite3")):
        record[name] = str(root / relative)
        manifest["artifacts"]["svg_candidates_" + name] = {
            "path": str(root / relative), "sha256": record["files"][relative]}
    _write_manifest(run, manifest)


@pytest.fixture
def candidate_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    with closing(sqlite3.connect(run / "delivery.gpkg")) as db, db:
        db.executescript("""CREATE TABLE gpkg_geometry_columns(table_name,column_name,geometry_type_name,srs_id);
          INSERT INTO gpkg_geometry_columns VALUES('POLE','geom','POINT',3857);
          CREATE TABLE gpkg_contents(table_name,min_x,min_y,max_x,max_y);
          INSERT INTO gpkg_contents VALUES('POLE',0,0,1,1);
          CREATE TABLE gpkg_spatial_ref_sys(srs_id,definition);
          INSERT INTO gpkg_spatial_ref_sys VALUES(3857,'');
          CREATE TABLE POLE(fid INTEGER,geom BLOB,source_handle TEXT);
          INSERT INTO POLE VALUES(1,NULL,'AB');""")
    root = run / "svg-candidates"
    (root / "symbols").mkdir(parents=True)
    (root / "symbols" / "instance-ab.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (root / "correspondence.html").write_text('<img src="symbols/instance-ab.svg">')
    (root / "symbols.sqlite3").write_bytes(b"source-bound candidate store")
    correspondence = {"source_sha256": "a" * 64, "auto_applied": False,
                      "complete_legend_correspondence_verified": False,
                      "all_points_accounted_for": 1, "dispositions": {"SOURCE_INSTANCE_ONLY": 1}}
    (root / "correspondence.json").write_text(json.dumps(correspondence))
    manifest = {"run_status": "CONDITIONAL", "source": {"sha256": "a" * 64},
                "artifacts": {"delivery": {"path": str(run / "delivery.gpkg"),
                                           "sha256": _sha(run / "delivery.gpkg")}},
                "svg_candidates": {"mode": "candidate", "status": "completed", "auto_applied": False,
                                   "output_dir": str(root)}}
    _seal_candidates(run, manifest)
    return run, manifest


def test_package_includes_verified_candidates_with_portable_receipt(candidate_run, tmp_path):
    run, manifest = candidate_run
    original = (run / "delivery.gpkg").read_bytes()
    output = tmp_path / "delivery"
    report = delivery.package_delivery(run, output)
    receipt = report["svg_candidates"]
    assert receipt["output_dir"] == "svg-candidates"
    assert receipt["report"] == "svg-candidates/correspondence.json"
    assert receipt["html"] == "svg-candidates/correspondence.html"
    assert receipt["store"] == "svg-candidates/symbols.sqlite3"
    assert receipt["files"] == manifest["svg_candidates"]["files"]
    assert receipt["source_sha256"] == manifest["source"]["sha256"]
    assert receipt["all_points_accounted_for"] == 1
    assert receipt["auto_applied"] is False
    assert receipt["complete_legend_correspondence_verified"] is False
    assert str(run) not in json.dumps(receipt)
    assert (run / "delivery.gpkg").read_bytes() == original
    with zipfile.ZipFile(output.with_suffix(".zip")) as archive:
        assert archive.read("delivery.gpkg") == original
        assert json.loads(archive.read("delivery-manifest.json"))["svg_candidates"] == receipt
        checksums = json.loads(archive.read("checksums.json"))
        for relative, expected_hash in receipt["files"].items():
            name = "svg-candidates/" + relative
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected_hash
            assert checksums[name] == expected_hash
    with zipfile.ZipFile(output / "delivery.qgz") as archive:
        assert b"instance-ab" not in archive.read("delivery.qgs")


def test_moved_run_packages_current_files_with_original_publication_paths(candidate_run, tmp_path, monkeypatch):
    original_run, manifest = candidate_run
    moved_run = tmp_path / "relocated-run"
    original_run.rename(moved_run)
    assert not original_run.exists()
    original_digest = delivery.digest

    def current_digest(path):
        assert not Path(path).is_relative_to(original_run), "A moved run must not read its old publication path"
        return original_digest(path)

    monkeypatch.setattr(delivery, "digest", current_digest)
    output = tmp_path / "delivery"
    report = delivery.package_delivery(moved_run, output)
    assert report["svg_candidates"]["files"] == manifest["svg_candidates"]["files"]
    assert str(original_run) not in json.dumps(report["svg_candidates"])
    with zipfile.ZipFile(output.with_suffix(".zip")) as archive:
        for relative, expected_hash in manifest["svg_candidates"]["files"].items():
            assert hashlib.sha256(archive.read("svg-candidates/" + relative)).hexdigest() == expected_hash


def test_moved_run_rejects_receipts_not_bound_to_original_delivery(candidate_run, tmp_path, monkeypatch):
    original_run, manifest = candidate_run
    moved_run = tmp_path / "relocated-run"
    original_run.rename(moved_run)
    manifest["artifacts"]["delivery"]["path"] = str(tmp_path / "unrelated" / "delivery.gpkg")
    _write_manifest(moved_run, manifest)
    monkeypatch.setattr(delivery, "digest", lambda path: pytest.fail(f"Unbound moved receipt was read: {path}"))
    with pytest.raises(ValueError, match="SVG candidate receipt path"):
        delivery.package_delivery(moved_run, tmp_path / "delivery")


@pytest.mark.parametrize("mode", ["legacy", "off"])
def test_old_and_disabled_runs_keep_existing_packaging(candidate_run, tmp_path, mode):
    run, manifest = candidate_run
    if mode == "legacy":
        del manifest["svg_candidates"]
    else:
        manifest["svg_candidates"] = {"status": "disabled", "mode": "off", "auto_applied": False}
    _write_manifest(run, manifest)
    report = delivery.package_delivery(run, tmp_path / "delivery")
    assert "svg_candidates" not in report
    with zipfile.ZipFile(tmp_path / "delivery.zip") as archive:
        assert not any(name.startswith("svg-candidates/") for name in archive.namelist())


@pytest.mark.parametrize("change", ["content", "missing", "extra", "artifact_hash", "incomplete", "applied"])
def test_candidate_integrity_failures_do_not_create_delivery(candidate_run, tmp_path, change):
    run, manifest = candidate_run
    svg = run / "svg-candidates" / "symbols" / "instance-ab.svg"
    if change == "content":
        svg.write_text("changed")
    elif change == "missing":
        svg.unlink()
    elif change == "extra":
        svg.with_name("unbound.svg").write_text("extra")
    elif change == "artifact_hash":
        manifest["artifacts"]["svg_candidates_store"]["sha256"] = "b" * 64
    elif change == "incomplete":
        manifest["svg_candidates"]["status"] = "failed"
    else:
        manifest["svg_candidates"]["auto_applied"] = True
    _write_manifest(run, manifest)
    output = tmp_path / "delivery"
    with pytest.raises(ValueError, match="SVG"):
        delivery.package_delivery(run, output)
    assert not output.exists()
    assert not output.with_suffix(".zip").exists()


@pytest.mark.parametrize("relative", ["../private.txt", "/private.txt", "C:/private.txt", "symbols\\private.txt",
                                      "symbols/../private.txt", "symbols//private.txt"])
def test_inventory_paths_cannot_escape_candidate_directory(candidate_run, tmp_path, monkeypatch, relative):
    run, manifest = candidate_run
    manifest["svg_candidates"]["files"][relative] = "b" * 64
    _write_manifest(run, manifest)
    monkeypatch.setattr(delivery, "digest", lambda path: pytest.fail(f"Unsafe inventory was read: {path}"))
    with pytest.raises(ValueError, match="SVG candidate file inventory"):
        delivery.package_delivery(run, tmp_path / "delivery")


@pytest.mark.parametrize("field", ["output_dir", "report", "artifact_path"])
def test_receipt_paths_cannot_redirect_reads(candidate_run, tmp_path, monkeypatch, field):
    run, manifest = candidate_run
    outside = tmp_path / "private.txt"
    outside.write_text("do not read")
    if field == "artifact_path":
        manifest["artifacts"]["svg_candidates_report"]["path"] = str(outside)
    else:
        manifest["svg_candidates"][field] = str(outside)
    _write_manifest(run, manifest)
    monkeypatch.setattr(delivery, "digest", lambda path: pytest.fail(f"Redirected receipt was read: {path}"))
    with pytest.raises(ValueError, match="SVG candidate receipt path"):
        delivery.package_delivery(run, tmp_path / "delivery")


@pytest.mark.parametrize("field,value", [("source_sha256", "b" * 64), ("auto_applied", True),
                                         ("complete_legend_correspondence_verified", True)])
def test_correspondence_remains_bound_and_unapproved(candidate_run, tmp_path, field, value):
    run, manifest = candidate_run
    path = run / "svg-candidates" / "correspondence.json"
    correspondence = json.loads(path.read_text())
    correspondence[field] = value
    path.write_text(json.dumps(correspondence))
    _seal_candidates(run, manifest)
    with pytest.raises(ValueError, match="source or candidate review contract"):
        delivery.package_delivery(run, tmp_path / "delivery")


def test_redirected_candidate_file_is_rejected_before_read(candidate_run, tmp_path, monkeypatch):
    run, manifest = candidate_run
    svg = run / "svg-candidates" / "symbols" / "instance-ab.svg"
    outside = tmp_path / "private.svg"
    outside.write_bytes(svg.read_bytes())
    svg.unlink()
    try:
        svg.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable in this Windows environment")
    monkeypatch.setattr(delivery, "digest", lambda path: pytest.fail(f"Redirected candidate was read: {path}"))
    with pytest.raises(ValueError, match="SVG candidate file is missing or redirects"):
        delivery.package_delivery(run, tmp_path / "delivery")


def test_change_during_copy_cannot_be_sealed(candidate_run, tmp_path, monkeypatch):
    run, _ = candidate_run
    original_copy = delivery.shutil.copy2

    def change_candidate(source, destination):
        result = original_copy(source, destination)
        if Path(destination).name == "instance-ab.svg":
            Path(destination).write_text("concurrent change")
        return result

    monkeypatch.setattr(delivery.shutil, "copy2", change_candidate)
    output = tmp_path / "delivery"
    with pytest.raises(ValueError, match="SVG candidate changed while packaging"):
        delivery.package_delivery(run, output)
    assert not output.with_suffix(".zip").exists()
    assert not (output / "delivery-manifest.json").exists()
