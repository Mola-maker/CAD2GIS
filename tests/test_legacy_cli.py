"""Legacy module invocation retains its flags while using canonical behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from cad2gis import pipeline
from cad2gis.cad2gis_v3 import cli as legacy_cli


def test_legacy_flags_preserve_success_payload(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.dwg"
    source_profile = tmp_path / "source_profile.json"
    registry = tmp_path / "mapping_registry.json"
    gcp = tmp_path / "gcp_profile.json"
    run = tmp_path / "run"
    calls = []

    def convert(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            evidence_path=run / "evidence.gpkg",
            delivery_path=run / "delivery.gpkg",
            style_manifest_path=run / "style_manifest.json",
            run_manifest_path=run / "run_manifest.json",
            counts={"CABLE": 3},
            diagnostics={"topology": {
                "components": 2, "connection_port_candidates": [{"private": True}],
            }},
        )

    monkeypatch.setattr(pipeline, "convert_project", convert)
    status = legacy_cli.main([
        "--input", str(source), "--run-dir", str(run),
        "--source-profile", str(source_profile),
        "--mapping-registry", str(registry), "--gcp-profile", str(gcp),
    ])
    assert status == 0
    assert len(calls) == 1
    assert {key: calls[0][key] for key in (
        "source", "run_dir", "source_profile", "mapping_registry", "gcp_profile",
    )} == {
        "source": source, "run_dir": run, "source_profile": source_profile,
        "mapping_registry": registry, "gcp_profile": gcp,
    }
    assert json.loads(capsys.readouterr().out) == {
        "status": "success", "evidence": str(run / "evidence.gpkg"),
        "delivery": str(run / "delivery.gpkg"),
        "styles": str(run / "style_manifest.json"),
        "manifest": str(run / "run_manifest.json"), "counts": {"CABLE": 3},
        "topology": {"components": 2},
    }


def test_legacy_failure_uses_canonical_machine_readable_error(tmp_path, capsys):
    status = legacy_cli.main([
        "--input", str(tmp_path / "missing.dwg"),
        "--run-dir", str(tmp_path / "run"),
        "--source-profile", str(tmp_path / "profile.json"),
        "--mapping-registry", str(tmp_path / "registry.json"),
    ])
    output = capsys.readouterr()
    assert status == 2
    assert not output.out
    error = json.loads(output.err)["error"]
    assert error["error_code"] == "SOURCE_NOT_FOUND"
    assert error["stage"] == "convert"
    assert error["artifact_status"] == "not_created"


def test_legacy_module_help_stays_available_without_gis_imports():
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    code = """
import sys
from cad2gis.cad2gis_v3 import cli
assert 'cad2gis.cad2gis_v3.pipeline' not in sys.modules
try:
    cli.main(['--help'])
except SystemExit as exc:
    assert exc.code == 0
else:
    raise AssertionError('help did not exit')
assert not any(name in sys.modules for name in ('osgeo', 'pyproj', 'shapely'))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=environment,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--input" in result.stdout


def test_editable_backend_fallback_uses_the_real_import_root(monkeypatch):
    from cad2gis import runtime

    root = Path(__file__).resolve().parents[1]
    monkeypatch.delenv(runtime.BACKEND_PATH_ENV, raising=False)
    monkeypatch.setattr(runtime, "_importable_backend_location", lambda: None)
    import_root = runtime._editable_backend_root()
    assert import_root == root / "src"
    assert runtime._valid_backend_root(import_root) == import_root
    deployment = runtime.backend_deployment()
    assert deployment == {
        "mode": "editable_checkout",
        "location": str(root / "src" / "cad2gis" / "cad2gis_v3"),
    }
    assert (Path(deployment["location"]) / "__init__.py").is_file()
