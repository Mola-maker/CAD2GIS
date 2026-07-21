"""Focused contract tests for the installable canonical package and CLI."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cad2gis import cli, doctor, pipeline, runtime  # noqa: E402


def _minimal_project(tmp_path: Path, *, prefix: str = "client") -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    config = project / "config"
    config.mkdir(parents=True)
    source_profile = config / f"{prefix}_source_profile.json"
    mapping_registry = config / f"{prefix}_mapping_registry.json"
    source_profile.write_text("{}", encoding="utf-8")
    mapping_registry.write_text("{}", encoding="utf-8")
    return project, source_profile, mapping_registry


def test_public_convert_signature_is_stable() -> None:
    signature = inspect.signature(pipeline.convert_project)
    assert tuple(signature.parameters) == (
        "source",
        "run_dir",
        "project_dir",
        "source_profile",
        "mapping_registry",
        "gcp_profile",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )


def test_generic_project_discovery_has_no_customer_name(tmp_path: Path) -> None:
    project, source_profile, mapping_registry = _minimal_project(tmp_path, prefix="delta")

    resolved = pipeline.resolve_project_configuration(project_dir=project)

    assert resolved.source_profile == source_profile.resolve()
    assert resolved.mapping_registry == mapping_registry.resolve()
    assert resolved.gcp_profile is None
    assert "apd" not in repr(pipeline._CONFIG_PATTERNS).lower()


def test_project_manifest_selects_explicit_relative_configs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config = project / "profiles"
    config.mkdir(parents=True)
    source_profile = config / "source.json"
    mapping_registry = config / "mapping.json"
    source_profile.write_text("{}", encoding="utf-8")
    mapping_registry.write_text("{}", encoding="utf-8")
    (project / "cad2gis-project.json").write_text(
        json.dumps(
            {
                "config": {
                    "source_profile": "profiles/source.json",
                    "mapping_registry": "profiles/mapping.json",
                }
            }
        ),
        encoding="utf-8",
    )

    resolved = pipeline.resolve_project_configuration(project_dir=project)

    assert resolved.source_profile == source_profile.resolve()
    assert resolved.mapping_registry == mapping_registry.resolve()


def test_convert_resolves_inputs_before_calling_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, source_profile, mapping_registry = _minimal_project(tmp_path)
    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"dwg")
    run_dir = tmp_path / "run"
    calls: list[dict[str, object]] = []
    expected = object()

    def fake_backend(**kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(runtime, "call_conversion_backend", fake_backend)

    result = pipeline.convert_project(
        source=source,
        run_dir=run_dir,
        project_dir=project,
    )

    assert result is expected
    assert calls == [
        {
            "source": source.resolve(),
            "run_dir": run_dir.resolve(),
            "source_profile": source_profile.resolve(),
            "mapping_registry": mapping_registry.resolve(),
            "gcp_profile": None,
        }
    ]


def test_wheel_runtime_does_not_search_arbitrary_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_runtime = tmp_path / "site-packages" / "cad2gis" / "runtime.py"
    fake_runtime.parent.mkdir(parents=True)
    fake_runtime.write_text("", encoding="utf-8")
    accidental = tmp_path / "work" / "src" / "cad2gis" / "cad2gis_v3"
    accidental.mkdir(parents=True)
    (accidental / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "work")
    monkeypatch.delenv(runtime.BACKEND_PATH_ENV, raising=False)
    monkeypatch.setattr(runtime, "__file__", str(fake_runtime))
    monkeypatch.setattr(runtime, "_importable_backend_location", lambda: None)

    assert runtime.backend_deployment() == {"mode": "missing", "location": None}


def test_explicit_backend_path_is_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "backend"
    package = backend_root / "cad2gis" / "cad2gis_v3"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv(runtime.BACKEND_PATH_ENV, str(backend_root))
    monkeypatch.setattr(runtime, "_importable_backend_location", lambda: None)
    monkeypatch.setattr(runtime, "_editable_backend_root", lambda: None)

    assert runtime.backend_deployment() == {
        "mode": "external_path",
        "location": str(package.resolve()),
    }


def test_invalid_explicit_backend_path_does_not_fall_back_to_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing-backend"
    monkeypatch.setenv(runtime.BACKEND_PATH_ENV, str(missing))
    monkeypatch.setattr(runtime, "_importable_backend_location", lambda: None)
    monkeypatch.setattr(runtime, "_editable_backend_root", lambda: ROOT)

    assert runtime.backend_deployment() == {
        "mode": "invalid_external_path",
        "location": None,
    }
    with pytest.raises(runtime.BackendUnavailable, match="does not contain a valid"):
        runtime._prepare_backend_import()


def test_doctor_report_is_structured_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor,
        "backend_contract",
        lambda: {
            "selected_mode": "missing",
            "location": None,
            "package": "cad2gis_v3",
            "environment_variable": "CAD2GIS_BACKEND_PATH",
            "supported_modes": (),
            "external_path_requirement": "test",
            "wheel_bundles_backend": False,
        },
    )
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    report = doctor.build_report()

    assert report["schema_version"] == "cad2gis.doctor.v1"
    assert report["status"] == "limited"
    assert report["conversion_ready"] is False
    assert report["capabilities"]["cli"] is True
    assert any(
        check["name"] == "backend" and check["status"] == "missing"
        for check in report["checks"]
    )


def test_module_help_does_not_import_gis_modules(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(
        """
import builtins
_original = builtins.__import__
def _blocked(name, *args, **kwargs):
    if name.split('.', 1)[0] in {'osgeo', 'pyproj', 'ezdxf'}:
        raise RuntimeError('blocked heavy import: ' + name)
    return _original(name, *args, **kwargs)
builtins.__import__ = _blocked
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(blocker), str(SRC)))

    completed = subprocess.run(
        [sys.executable, "-m", "cad2gis", "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0
    assert "Deterministic CAD-to-GIS" in completed.stdout
    assert "blocked heavy import" not in completed.stderr


def test_default_error_is_friendly_and_debug_reraises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.dwg"
    arguments = [
        "convert",
        str(missing),
        "--run-dir",
        str(tmp_path / "run"),
        "--source-profile",
        str(tmp_path / "source.json"),
        "--mapping-registry",
        str(tmp_path / "mapping.json"),
    ]

    assert cli.main(arguments) == 2
    captured = capsys.readouterr()
    assert "source drawing does not exist" in captured.err
    assert "Traceback" not in captured.err

    with pytest.raises(FileNotFoundError):
        cli.main([*arguments, "--debug"])


def test_gcp_status_is_lazy_and_uses_public_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import cad2gis.gcp_workflow as workflow

    project = tmp_path / "project"
    project.mkdir()
    calls: list[Path] = []

    def fake_status(path):
        calls.append(Path(path))
        return {"operation": "status", "status": "blocked"}

    monkeypatch.setattr(workflow, "status_project", fake_status)

    assert cli.main(["gcp", "status", "--project", str(project), "--json"]) == 0
    assert calls == [project]
    assert json.loads(capsys.readouterr().out)["operation"] == "status"


def test_verify_uses_public_read_only_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import cad2gis.verify as verify

    matrix = tmp_path / "matrix.json"
    matrix.write_text("{}", encoding="utf-8")
    calls: list[Path] = []

    def fake_evaluate(path):
        calls.append(Path(path))
        return {"schema_version": "report", "samples": []}

    monkeypatch.setattr(verify, "evaluate_matrix", fake_evaluate)
    monkeypatch.setattr(verify, "strongest_allowed_claim", lambda report: "inventory only")

    assert cli.main(["verify", str(matrix), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == [matrix.resolve()]
    assert payload["strongest_allowed_claim"] == "inventory only"


def test_packaging_declares_only_src_public_package() -> None:
    import tomllib

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["scripts"]["cad2gis"] == "cad2gis.cli:main"
    assert metadata["tool"]["setuptools"]["package-dir"] == {"": "src"}
    assert metadata["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert runtime.backend_contract()["wheel_bundles_backend"] is False
