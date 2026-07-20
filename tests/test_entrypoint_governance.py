"""Governance tests for canonical, legacy, and QGIS conversion entrypoints."""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ENTRYPOINTS = (
    ROOT / "demo" / "converter.py",
    ROOT / "demo" / "converter_3857.py",
    ROOT / "demo" / "geoformer.py",
    ROOT / "official" / "validation" / "converter.py",
)


def test_convert_v3_delegates_only_to_canonical_cli() -> None:
    wrapper = ROOT / "experiment" / "py_scripts" / "convert_v3.py"
    source = wrapper.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(wrapper))

    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imports == {"cad2gis.cli"}
    assert "cad2gis_v3" not in source
    assert "pipeline" not in source


@pytest.mark.parametrize("entrypoint", LEGACY_ENTRYPOINTS, ids=lambda path: path.stem)
def test_legacy_direct_execution_is_disabled_by_default(entrypoint: Path) -> None:
    env = os.environ.copy()
    env.pop("CAD2GIS_ENABLE_LEGACY", None)

    completed = subprocess.run(
        [sys.executable, str(entrypoint)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    message = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert "DEPRECATED" in message
    assert "canonical `cad2gis` CLI" in message
    assert "CAD2GIS_ENABLE_LEGACY=1" in message


@pytest.mark.parametrize("entrypoint", LEGACY_ENTRYPOINTS, ids=lambda path: path.stem)
def test_legacy_opt_in_is_exact_and_precedes_native_imports(entrypoint: Path) -> None:
    source = entrypoint.read_text(encoding="utf-8")
    assert 'os.environ.get("CAD2GIS_ENABLE_LEGACY") != "1"' in source
    first_guard = source.index('if __name__ == "__main__":')
    first_nonstdlib_dependency = min(
        index
        for marker in ("ctypes.CDLL", "from qgis", "from osgeo")
        if (index := source.find(marker)) >= 0
    )
    assert first_guard < first_nonstdlib_dependency


def _import_adapter(monkeypatch: pytest.MonkeyPatch, canonical_call):
    cad2gis = ModuleType("cad2gis")
    cad2gis.__path__ = []  # type: ignore[attr-defined]
    pipeline = ModuleType("cad2gis.pipeline")
    pipeline.convert_project = canonical_call  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cad2gis", cad2gis)
    monkeypatch.setitem(sys.modules, "cad2gis.pipeline", pipeline)
    monkeypatch.syspath_prepend(str(ROOT / "qgis_plugin"))
    sys.modules.pop("cad2gis_plugin.adapter", None)
    sys.modules.pop("cad2gis_plugin", None)
    return importlib.import_module("cad2gis_plugin.adapter")


def test_qgis_adapter_delegates_explicit_project_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    expected = object()

    def canonical_convert_project(**kwargs):
        calls.append(kwargs)
        return expected

    adapter = _import_adapter(monkeypatch, canonical_convert_project)
    project = {
        "source": "input.dwg",
        "run_dir": "run",
        "project_dir": "project",
        "source_profile": "source.json",
        "mapping_registry": "mapping.json",
        "gcp_profile": "gcp.json",
    }

    assert adapter.convert_project(project) is expected
    assert calls == [project]
    with pytest.raises(ValueError, match="unsupported"):
        adapter.convert_project({**project, "pipeline_copy": True})


def test_qgis_adapter_loads_existing_gpkg_without_qgis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _import_adapter(monkeypatch, lambda **kwargs: kwargs)
    gpkg = ROOT / "official" / "validation" / "FiberHome_P2_FTTH.gpkg"

    calls: list[tuple[str, str, str]] = []

    def loader(uri: str, name: str, provider: str):
        calls.append((uri, name, provider))
        return name

    layers = adapter.load_geopackage(gpkg, layer_loader=loader)

    expected_names = (
        "BOITE",
        "CABLE",
        "IMB",
        "INFRASTRUCTURE",
        "PTECH",
        "SITE",
        "ZNRO",
        "ZPM",
    )
    assert layers == expected_names
    assert tuple(name for _, name, _ in calls) == expected_names
    assert all(provider == "ogr" for _, _, provider in calls)
    assert all("|layername=" in uri for uri, _, _ in calls)


def test_qgis_plugin_contains_no_conversion_pipeline_copy() -> None:
    plugin_root = ROOT / "qgis_plugin" / "cad2gis_plugin"
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in plugin_root.glob("*.py")
    }
    combined = "\n".join(sources.values())

    assert "from cad2gis.pipeline import convert_project" in sources["adapter.py"]
    for forbidden in (
        "cad2gis_v3",
        "read_dwg",
        "write_geopackage",
        "stage1_ingestion",
        "process_tile_pipeline",
        "LibreDWG",
        "ogr2ogr",
    ):
        assert forbidden not in combined
