"""End-to-end contracts for GIS-to-CAD publication and coordinate safety."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cad2gis import cli, reverse  # noqa: E402
from cad2gis.reverse_models import ReverseConversionError  # noqa: E402


def _source(tmp_path: Path, *, crs: str | None = None) -> Path:
    data = {
        "type": "FeatureCollection", "name": "assets",
        "features": [
            {"type": "Feature", "id": "pole-1", "properties": {"label": "光缆 A", "survey": False},
             "geometry": {"type": "Point", "coordinates": [-75, 0, 12.5]}},
            {"type": "Feature", "id": "cable-1", "properties": {"label": "C1", "length": 111.0},
             "geometry": {"type": "LineString", "coordinates": [[-75, 0], [-75, 0.001]]}},
        ],
    }
    if crs:
        data["crs"] = {"type": "name", "properties": {"name": crs}}
    path = tmp_path / "source.geojson"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_real_cli_reprojects_reopens_and_preserves_attributes(tmp_path, capsys):
    source = _source(tmp_path)
    original = source.read_bytes()
    output = tmp_path / "run"
    assert cli.main(["reverse-convert", str(source), "--run-dir", str(output),
                     "--target-crs", "EPSG:32618", "--label-field", "label", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    doc = ezdxf.readfile(output / "drawing.dxf")
    point = list(doc.modelspace().query("POINT"))[0]
    assert tuple(point.dxf.location) == pytest.approx((500000, 0, 12.5), abs=1e-7)
    assert doc.units == 6
    assert not doc.audit().has_errors
    assert report["feature_count"] == 2
    assert report["label_count"] == 2
    assert report["absolute_accuracy_verified"] is False
    assert report["original_cad_restored"] is False
    assert report["source_sha256"] == hashlib.sha256(original).hexdigest()
    for name, artifact in report["artifacts"].items():
        assert artifact["sha256"] == hashlib.sha256((output / name).read_bytes()).hexdigest()
    attributes = [json.loads(line) for line in (output / "attributes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert attributes[0]["properties"] == {"label": "光缆 A", "survey": False}
    for feature in attributes:
        assert all(doc.entitydb.get(entity["handle"]) is not None for entity in feature["entities"])
    assert source.read_bytes() == original
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8")) == report


def test_inspect_has_no_output_side_effects(tmp_path, capsys):
    source = _source(tmp_path)
    assert cli.main(["reverse-inspect", str(source), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["feature_count"] == 2
    assert report["layers"][0]["name"] == "assets"
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("target", [None, "EPSG:4326", "EPSG:4978", "EPSG:999999"])
def test_unsafe_or_invalid_target_creates_no_run(tmp_path, target):
    source = _source(tmp_path)
    with pytest.raises(ReverseConversionError):
        reverse.reverse_convert(source, run_dir=tmp_path / "run", target_crs=target)
    assert not (tmp_path / "run").exists()


def test_existing_output_survives(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    (output / "drawing.dxf").write_text("keep", encoding="utf-8")
    with pytest.raises(ReverseConversionError) as error:
        reverse.reverse_convert(source, run_dir=output, target_crs="EPSG:32618")
    assert error.value.code == "REVERSE_OUTPUT_EXISTS"
    assert (output / "drawing.dxf").read_text() == "keep"


def test_write_failure_cleans_staging_and_reservation(tmp_path, monkeypatch):
    from cad2gis import reverse_writer

    source = _source(tmp_path)

    def fail(features, destination, **kwargs):
        destination.write_bytes(b"incomplete")
        raise OSError("simulated disk failure")

    monkeypatch.setattr(reverse_writer, "write_dxf", fail)
    with pytest.raises(OSError, match="disk failure"):
        reverse.reverse_convert(source, run_dir=tmp_path / "run", target_crs="EPSG:32618")
    assert list(tmp_path.iterdir()) == [source]


def test_busy_output_retains_other_publisher_lock(tmp_path):
    source = _source(tmp_path)
    lock = tmp_path / ".run.reverse.lock"
    lock.write_bytes(b"other publisher")
    with pytest.raises(ReverseConversionError) as error:
        reverse.reverse_convert(source, run_dir=tmp_path / "run", target_crs="EPSG:32618")
    assert error.value.code == "REVERSE_OUTPUT_BUSY"
    assert lock.read_bytes() == b"other publisher"
    assert not (tmp_path / "run").exists()


def test_source_change_rejected(tmp_path, monkeypatch):
    source = _source(tmp_path)
    digests = iter(["before", "after"])
    monkeypatch.setattr(reverse, "_sha256", lambda path: next(digests))
    with pytest.raises(ReverseConversionError) as error:
        reverse.inspect_gis(source)
    assert error.value.code == "REVERSE_SOURCE_CHANGED"


def test_active_geopackage_journal_rejected_before_read(tmp_path):
    source = tmp_path / "source.gpkg"
    source.write_bytes(b"database")
    Path(str(source) + "-wal").write_bytes(b"uncheckpointed transaction")
    with pytest.raises(ReverseConversionError) as error:
        reverse.inspect_gis(source)
    assert error.value.code == "REVERSE_SOURCE_BUSY"


def test_projected_input_defaults_to_declared_crs(tmp_path):
    source = _source(tmp_path, crs="EPSG:32618")
    report = reverse.reverse_convert(source, run_dir=tmp_path / "run")
    assert report["target_crs"] == "EPSG:32618"
    point = list(ezdxf.readfile(tmp_path / "run/drawing.dxf").modelspace().query("POINT"))[0]
    assert tuple(point.dxf.location) == pytest.approx((-75, 0, 12.5))


def test_source_override_is_recorded(tmp_path):
    source = _source(tmp_path)
    report = reverse.reverse_convert(source, run_dir=tmp_path / "run", source_crs="EPSG:32618")
    assert report["source_crs_override"] == "EPSG:32618"
    assert report["declared_layer_crs"]["assets"] != "EPSG:32618"


def test_feet_units_are_not_mislabeled_as_metres():
    from pyproj import CRS

    code, report = reverse._units(CRS.from_epsg(2263))
    assert code == 21
    assert report["metres_per_unit"] == pytest.approx(1200 / 3937)


def test_cli_error_is_structured_and_actionable(tmp_path, capsys):
    source = _source(tmp_path)
    assert cli.main(["reverse-convert", str(source), "--run-dir", str(tmp_path / "run"), "--json"]) == 2
    result = capsys.readouterr()
    assert not result.out
    error = json.loads(result.err)["error"]
    assert error["error_code"] == "REVERSE_CRS_REQUIRED"
    assert error["stage"] == "reverse-convert"
    assert error["recovery_command"] == "cad2gis reverse-convert --help"
    assert "Traceback" not in result.err


def test_cli_help_does_not_import_gis_libraries():
    import os
    import subprocess

    code = ("from cad2gis.cli import _parser; import sys; _parser(); "
            "assert not ({'osgeo','ezdxf','shapely','pyproj'} & set(sys.modules))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert result.returncode == 0, result.stderr


def test_mixed_dimensional_collection_keeps_each_members_z(tmp_path):
    source = tmp_path / "mixed.geojson"
    source.write_text(json.dumps({"type": "Feature", "properties": {}, "geometry": {
        "type": "GeometryCollection", "geometries": [
            {"type": "Point", "coordinates": [-75, 0]},
            {"type": "Point", "coordinates": [-75, 0.001, 42]},
        ],
    }}), encoding="utf-8")
    reverse.reverse_convert(source, run_dir=tmp_path / "run", target_crs="EPSG:32618")
    points = list(ezdxf.readfile(tmp_path / "run/drawing.dxf").modelspace().query("POINT"))
    assert len(points) == 2
    assert points[0].dxf.location.z == 0
    assert points[1].dxf.location.z == 42


def test_acl_failure_aborts_publication(tmp_path, monkeypatch):
    source = _source(tmp_path)

    def fail(stage):
        assert (stage / "drawing.dxf").is_file()
        raise PermissionError("cannot inherit output ACL")

    monkeypatch.setattr(reverse, "inherit_output_permissions", fail)
    with pytest.raises(PermissionError, match="output ACL"):
        reverse.reverse_convert(source, run_dir=tmp_path / "run", target_crs="EPSG:32618")
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL regression")
def test_published_files_inherit_windows_parent_permissions(tmp_path):
    import ctypes
    import subprocess

    name = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong(len(name))
    assert ctypes.windll.advapi32.GetUserNameW(name, ctypes.byref(size))
    subprocess.run(["icacls.exe", str(tmp_path), "/grant", f"{name.value}:(OI)(CI)(RX)"],
                   capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    source = _source(tmp_path)
    output = tmp_path / "run"
    reverse.reverse_convert(source, run_dir=output, target_crs="EPSG:32618")
    for path in [output, *output.iterdir()]:
        result = subprocess.run(["icacls.exe", str(path)], capture_output=True, text=True,
                                encoding="oem", check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert any(name.value.casefold() in line.casefold() and "(I)" in line
                   for line in result.stdout.splitlines()), result.stdout


def test_post_publication_cleanup_failure_reports_retained_run(tmp_path, monkeypatch, capsys):
    source = _source(tmp_path)
    output = tmp_path / "run"
    real_unlink = Path.unlink

    def deny_lock_unlink(path, *args, **kwargs):
        if path.name == ".run.reverse.lock":
            raise PermissionError("simulated reservation cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_lock_unlink)
    assert cli.main(["reverse-convert", str(source), "--run-dir", str(output),
                     "--target-crs", "EPSG:32618", "--json"]) == 2
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["error_code"] == "REVERSE_CLEANUP_FAILED"
    assert error["artifact_status"] == str(output)
    assert "published" in error["message"]
    assert (output / "manifest.json").is_file()
    assert not ezdxf.readfile(output / "drawing.dxf").audit().has_errors
