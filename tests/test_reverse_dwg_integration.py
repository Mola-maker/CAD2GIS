"""Exercise canonical format dispatch and binary readback gates with a test codec.

The codec is deliberately not a DWG implementation. Real AutoCAD qualification
and corpus runs live in the recorded external evidence directory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import asyncio

import ezdxf
import pytest

from cad2gis import agent_mcp, cli, reverse, reverse_batch, reverse_dwg
from cad2gis.reverse_models import ReverseConversionError
from cad2gis.reverse_verify import verify_reverse


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "name": "survey",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [{"type": "Feature", "id": "p1", "properties": {"label": "测点 A"},
                      "geometry": {"type": "Point", "coordinates": [1, 2, 3]}}]}), encoding="utf-8")
    return path


@pytest.fixture
def codec(monkeypatch):
    calls = []
    monkeypatch.setattr(reverse_dwg, "resolve_runtime", lambda: Path("test-only-runtime"))

    def write(source, output):
        calls.append(("write", Path(source), Path(output)))
        Path(output).write_bytes(b"TEST_ONLY_CODEC\n" + Path(source).read_bytes())
        return {"backend": "test_only", "output_path": str(output)}

    def read(source, output):
        calls.append(("read", Path(source), Path(output)))
        Path(output).write_bytes(Path(source).read_bytes().split(b"\n", 1)[1])
        return {"backend": "test_only", "output_path": str(output)}

    monkeypatch.setattr(reverse_dwg, "convert_dxf_to_dwg", write)
    monkeypatch.setattr(reverse_dwg, "read_dwg_as_dxf", read)
    return calls


def test_native_output_is_verified_before_publication_and_reread_later(source, codec, tmp_path):
    run = tmp_path / "run"
    report = reverse.reverse_convert(source, run_dir=run, output_format="dwg", label_field="label")
    assert report["output_format"] == "DWG"
    assert report["verification"]["dwg_content_verified"] is True
    assert report["dwg_backend"]["write"]["output_path"] == str(run / "drawing.dwg")
    assert set(report["artifacts"]) == {"drawing.dxf", "drawing.dwg", "attributes.jsonl", "dwg_readback.dxf"}
    assert [call[0] for call in codec] == ["write", "read"]
    before = {item.name: item.read_bytes() for item in run.iterdir()}
    assert verify_reverse(run)["dwg_content_verified"] is True
    assert [call[0] for call in codec] == ["write", "read", "read"]
    assert before == {item.name: item.read_bytes() for item in run.iterdir()}


def test_resealed_binary_mutation_cannot_hide_behind_old_readback(source, codec, tmp_path):
    run = tmp_path / "run"
    reverse.reverse_convert(source, run_dir=run, output_format="DWG")
    drawing = ezdxf.readfile(run / "drawing.dxf")
    next(iter(drawing.modelspace())).dxf.location = (9, 9, 9)
    changed = tmp_path / "changed.dxf"
    drawing.saveas(changed)
    binary = run / "drawing.dwg"
    binary.write_bytes(b"TEST_ONLY_CODEC\n" + changed.read_bytes())
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"]["drawing.dwg"]["sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReverseConversionError, match="coordinate"):
        verify_reverse(run)


def test_corrupt_native_readback_prevents_publication(source, codec, monkeypatch, tmp_path):
    original = reverse_dwg.read_dwg_as_dxf

    def corrupt(source, output):
        result = original(source, output)
        drawing = ezdxf.readfile(output)
        next(iter(drawing.modelspace())).dxf.thickness = 7
        drawing.saveas(output)
        return result

    monkeypatch.setattr(reverse_dwg, "read_dwg_as_dxf", corrupt)
    with pytest.raises(ReverseConversionError, match="thickness"):
        reverse.reverse_convert(source, run_dir=tmp_path / "run", output_format="DWG")
    assert not (tmp_path / "run").exists()
    assert not list(tmp_path.glob(".run.reverse*"))


def test_native_error_metric_is_not_replaced_by_exact_intermediate(source, codec, monkeypatch, tmp_path):
    original = reverse_dwg.read_dwg_as_dxf

    def shifted(source, output):
        result = original(source, output)
        drawing = ezdxf.readfile(output)
        next(iter(drawing.modelspace())).dxf.location = (1 + 1e-9, 2, 3)
        drawing.saveas(output)
        return result

    monkeypatch.setattr(reverse_dwg, "read_dwg_as_dxf", shifted)
    run = tmp_path / "run"
    converted = reverse.reverse_convert(source, run_dir=run, output_format="DWG")
    assert converted["verification"]["max_coordinate_error"] == pytest.approx(1e-9)
    verified = verify_reverse(run)
    assert verified["max_coordinate_error"] == pytest.approx(1e-9)
    assert verified["native_readback_verification"]["max_coordinate_error"] == pytest.approx(1e-9)


def test_runtime_unavailable_rejects_before_output_creation(source, monkeypatch, tmp_path):
    def missing():
        raise ReverseConversionError("missing runtime", code="REVERSE_DWG_RUNTIME_UNAVAILABLE")

    monkeypatch.setattr(reverse_dwg, "resolve_runtime", missing)
    with pytest.raises(ReverseConversionError) as error:
        reverse.reverse_convert(source, run_dir=tmp_path / "new" / "run", output_format="DWG")
    assert error.value.code == "REVERSE_DWG_RUNTIME_UNAVAILABLE"
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("uncertain", [False, True])
def test_native_failure_preserves_diagnostics_and_uncertain_process_workspace(source, codec, monkeypatch, tmp_path, uncertain):
    def fail(drawing, output):
        workspace = Path(output).parent / ".cad2gis-dwg-active"
        workspace.mkdir()
        (workspace / "process.log").write_text("owned native process diagnostic", encoding="utf-8")
        error = ReverseConversionError("native timeout", code="REVERSE_DWG_TIMEOUT")
        error.details = {"process_may_be_running": uncertain, "workspace": str(workspace)}
        raise error

    monkeypatch.setattr(reverse_dwg, "convert_dxf_to_dwg", fail)
    with pytest.raises(ReverseConversionError) as failure:
        reverse.reverse_convert(source, run_dir=tmp_path / "run", output_format="DWG")
    error = failure.value
    assert error.code == "REVERSE_DWG_TIMEOUT"
    assert error.details["process_may_be_running"] is uncertain
    assert error.details["run_published"] is False
    assert (error.retained_artifact_path / ".cad2gis-dwg-active/process.log").is_file()
    assert not (tmp_path / "run").exists()
    assert not (tmp_path / ".run.reverse.lock").exists()


def test_batch_discloses_retained_native_failure_directory(source, codec, monkeypatch, tmp_path):
    def fail(drawing, output):
        error = ReverseConversionError("native timeout", code="REVERSE_DWG_TIMEOUT")
        error.details = {"process_may_be_running": True}
        raise error

    monkeypatch.setattr(reverse_dwg, "convert_dxf_to_dwg", fail)
    plan = reverse_batch.prepare_batch([source], options={"output_format": "DWG"})
    report = reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    job = report["jobs"][0]
    assert report["status"] == "failed" and job["error"]["code"] == "REVERSE_DWG_TIMEOUT"
    assert job["error"]["details"]["process_may_be_running"] is True
    assert job["error"]["details"]["unverified_workspace"] in job["retained_artifacts"]
    assert Path(job["retained_artifacts"][0]).is_dir()


def test_mcp_native_verification_failure_retains_admitted_inspectable_evidence(source, codec, monkeypatch, tmp_path):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = tmp_path / "run"
    reverse.reverse_convert(source, run_dir=run, output_format="DWG")
    before = {item.name: item.read_bytes() for item in run.iterdir()}

    def fail(drawing, output):
        log = output.parent / "native.log"
        log.write_text("native timeout", encoding="utf-8")
        error = ReverseConversionError("native timeout", code="REVERSE_DWG_TIMEOUT")
        error.details = {"process_may_be_running": True, "log_path": str(log)}
        raise error

    monkeypatch.setattr(reverse_dwg, "read_dwg_as_dxf", fail)
    server = agent_mcp.create_server()
    with pytest.raises(Exception) as failure:
        asyncio.run(server.call_tool("reverse_verify", {"run_dir": str(run)}))
    text = str(failure.value)
    payload = json.loads(text[text.index("{"):])
    assert payload["error_code"] == "REVERSE_DWG_TIMEOUT"
    assert payload["details"]["process_may_be_running"] is True
    retained = agent_mcp._path(payload["artifact_status"])
    assert retained.is_relative_to(tmp_path) and (retained / "native.log").is_file()
    assert before == {item.name: item.read_bytes() for item in run.iterdir()}


def test_source_change_during_native_export_prevents_publication(source, codec, monkeypatch, tmp_path):
    original = reverse_dwg.convert_dxf_to_dwg

    def change(drawing, output):
        result = original(drawing, output)
        source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return result

    monkeypatch.setattr(reverse_dwg, "convert_dxf_to_dwg", change)
    with pytest.raises(ReverseConversionError) as error:
        reverse.reverse_convert(source, run_dir=tmp_path / "run", output_format="DWG")
    assert error.value.code == "REVERSE_SOURCE_CHANGED"
    assert not (tmp_path / "run").exists()


def test_cli_and_batch_dispatch_native_format(source, codec, tmp_path, capsys):
    assert cli.main(["reverse-convert", str(source), "--run-dir", str(tmp_path / "cli"),
                     "--output-format", "DWG", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["output_format"] == "DWG"
    plan = reverse_batch.prepare_batch([source], options={"output_format": "DWG"})
    report = reverse_batch.run_batch(plan, run_dir=tmp_path / "batch")
    assert report["status"] == "complete"
    assert report["jobs"][0]["verification"]["dwg_content_verified"] is True


@pytest.mark.parametrize("output_format", ["DGN", None, 2, []])
def test_unsupported_format_is_rejected_without_output(source, tmp_path, output_format):
    with pytest.raises(ReverseConversionError) as error:
        reverse.reverse_convert(source, run_dir=tmp_path / "run", output_format=output_format)
    assert error.value.code == "REVERSE_FORMAT_UNSUPPORTED"
    assert not (tmp_path / "run").exists()
