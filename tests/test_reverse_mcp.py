"""Real MCP reverse conversion, filesystem admission and write uncertainty."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys

import ezdxf
import pytest

from cad2gis import agent_mcp
from cad2gis.contracts import MCP_TOOL_NAMES
from cad2gis.mcp_diagnostics import MUTATING_TOOLS
from cad2gis.reverse_models import ReverseConversionError


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    path = tmp_path / "survey.geojson"
    path.write_text(json.dumps({
        "type": "FeatureCollection", "name": "survey",
        "crs": {"type": "name", "properties": {"name": "EPSG:32618"}},
        "features": [
            {"type": "Feature", "id": "pole-1",
             "properties": {"label": "测量点", "cad_layer": "poles", "surveyed": False},
             "geometry": {"type": "Point", "coordinates": [500000, 100, 12.5]}},
            {"type": "Feature", "id": "line-1",
             "properties": {"label": "L1", "cad_layer": "cables", "nested": {"value": 3}},
             "geometry": {"type": "LineString", "coordinates": [[500000, 100], [500003, 104]]}},
        ],
    }), encoding="utf-8")
    return path


@pytest.fixture
def server():
    pytest.importorskip("mcp")
    return agent_mcp.create_server()


@pytest.fixture
def native_registration(source, tmp_path):
    from test_reverse_registration import direct_manifest

    data = json.loads(source.read_text(encoding="utf-8"))
    data["crs"]["properties"]["name"] = "EPSG:3857"
    source.write_text(json.dumps(data), encoding="utf-8")
    manifest = direct_manifest()
    manifest.update(schema_version="cad2gis-run-manifest-v4", pipeline="cad2gis-reviewed-project-evidence-first-v4")
    manifest["source"] = {"path": "original.dwg", "sha256": "a" * 64}
    manifest["artifacts"] = {"delivery": {"path": "main.gpkg", "sha256": "b" * 64}}
    manifest["delivery_partitions"] = {
        "SECOND": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
    }
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _structured(result):
    if isinstance(result, tuple):
        return result[1]
    if isinstance(result, dict):
        return result
    return json.loads(next(part.text for part in result if part.type == "text"))


def test_reverse_handlers_execute_real_conversion_without_gdal(source, tmp_path, monkeypatch):
    from cad2gis import native_runtime

    def unavailable():
        raise AssertionError("GIS-to-DXF must not require GDAL")

    monkeypatch.setattr(native_runtime, "ensure_osgeo_runtime", unavailable)
    before = source.read_bytes()
    inspected = agent_mcp.reverse_inspect(str(source), layers=["survey"])
    assert inspected["feature_count"] == 2
    assert list(tmp_path.iterdir()) == [source]
    result = agent_mcp.reverse_convert(
        str(source), run_dir=str(tmp_path / "run"), layers=["survey"],
        source_crs="EPSG:32618", target_crs="EPSG:32618", label_field="label",
        layer_field="cad_layer", label_height=3.25,
    )
    drawing = ezdxf.readfile(tmp_path / "run" / "drawing.dxf")
    assert drawing.dxfversion == "AC1032"
    assert tuple(next(iter(drawing.modelspace().query("POINT"))).dxf.location) == (500000, 100, 12.5)
    assert {e.dxf.layer for e in drawing.modelspace()} == {"poles", "cables"}
    assert all(e.dxf.char_height == 3.25 for e in drawing.modelspace().query("MTEXT"))
    assert result["feature_count"] == 2 and result["entity_count"] == 4
    assert result["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert result["original_cad_restored"] is False
    assert result["absolute_accuracy_verified"] is False
    assert source.read_bytes() == before
    with pytest.raises(ReverseConversionError) as failure:
        agent_mcp.reverse_convert(str(source), run_dir=str(tmp_path / "run"))
    assert failure.value.code == "REVERSE_OUTPUT_EXISTS"


def test_reverse_capability_is_separate_from_forward_runtime(source, monkeypatch):
    monkeypatch.setattr(agent_mcp, "_runtime_status", lambda: {"conversion_ready": False})
    result = agent_mcp.get_capabilities()
    reverse = result["gis_to_cad"]
    assert result["runtime"]["conversion_ready"] is False
    assert reverse["workflow"] == ["reverse_inspect", "reverse_convert", "reverse_verify"]
    assert reverse["input_formats"] == ["GeoPackage", "GeoJSON"]
    assert reverse["output_formats"] == ["DXF", "DWG"] and reverse["dxf_version"] == "R2018"
    assert reverse["dwg_output"] is True and reverse["requires_gdal"] is False
    assert "AutoCAD Core Console" in reverse["dwg_requires"]
    assert reverse["requires_pillow"] is False
    assert reverse["native_coordinate_recovery"]["supported"] is True
    assert reverse["native_coordinate_recovery"]["original_cad_restored"] is False
    assert reverse["original_cad_restored"] is False
    assert reverse["absolute_accuracy_verified"] is False


def test_mcp_converts_sqlite_geopackage_with_declared_layer(source, tmp_path):
    from pyproj import CRS
    from shapely import to_wkb
    from shapely.geometry import Point

    path = tmp_path / "survey.gpkg"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY, data_type TEXT, srs_id INTEGER);
            CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT, srs_id INTEGER, z INTEGER, m INTEGER);
            CREATE TABLE gpkg_spatial_ref_sys (srs_id INTEGER PRIMARY KEY, organization TEXT, organization_coordsys_id INTEGER, definition TEXT);
            CREATE TABLE poles (fid INTEGER PRIMARY KEY, label TEXT, geom BLOB);
            INSERT INTO gpkg_contents VALUES ('poles', 'features', 32618);
            INSERT INTO gpkg_geometry_columns VALUES ('poles', 'geom', 'POINT', 32618, 1, 0);
        """)
        connection.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (32618, 'EPSG', 32618, ?)",
                           (CRS.from_epsg(32618).to_wkt(),))
        geometry = b"GP\x00\x01" + struct.pack("<i", 32618) + to_wkb(Point(500000, 100, 12.5), flavor="iso")
        connection.execute("INSERT INTO poles VALUES (1, 'P1', ?)", (geometry,))
    inspected = agent_mcp.reverse_inspect(str(path), layers=["poles"])
    assert inspected["feature_count"] == 1
    result = agent_mcp.reverse_convert(str(path), run_dir=str(tmp_path / "gpkg-run"), layers=["poles"])
    assert result["feature_count"] == 1
    point = next(iter(ezdxf.readfile(tmp_path / "gpkg-run/drawing.dxf").modelspace()))
    assert tuple(point.dxf.location) == (500000, 100, 12.5)


def test_fastmcp_registration_schemas_and_mutation_annotations(server):
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert tuple(sorted(tools)) == MCP_TOOL_NAMES
    expected = {
        "reverse_inspect": ({"source"}, {"source", "layers"}),
        "reverse_convert": ({"source", "run_dir"}, {
            "source", "run_dir", "layers", "source_crs", "target_crs", "label_field",
            "layer_field", "label_height", "registration_manifest", "partition", "output_format",
        }),
        "reverse_verify": ({"run_dir"}, {"run_dir", "source", "tolerance", "registration_manifest"}),
    }
    for name, (required, properties) in expected.items():
        schema = tools[name].inputSchema
        assert set(schema["required"]) == required
        assert set(schema["properties"]) == properties
        assert tools[name].annotations.readOnlyHint is (name != "reverse_convert")
        assert tools[name].annotations.idempotentHint is (name != "reverse_convert")
        assert tools[name].annotations.destructiveHint is False
        assert tools[name].annotations.openWorldHint is False
        assert (name in MUTATING_TOOLS) is (name == "reverse_convert")


def test_fastmcp_invokes_real_inspect_convert_verify(server, source, tmp_path):
    async def run():
        inspected = _structured(await server.call_tool("reverse_inspect", {"source": str(source)}))
        assert inspected["feature_count"] == 2
        converted = _structured(await server.call_tool("reverse_convert", {
            "source": str(source), "run_dir": str(tmp_path / "run"), "label_field": "label",
        }))
        assert converted["label_count"] == 2
        return _structured(await server.call_tool("reverse_verify", {"run_dir": str(tmp_path / "run")}))

    verified = asyncio.run(run())
    assert verified["status"] == "verified"
    assert verified["absolute_accuracy_verified"] is False


@pytest.mark.parametrize("through_server", [False, True])
def test_native_mcp_recovers_bound_partition_and_verifies(
    server, source, native_registration, tmp_path, through_server,
):
    output = tmp_path / "native-run"
    arguments = {
        "source": str(source), "run_dir": str(output),
        "registration_manifest": str(native_registration), "partition": "SECOND",
        "label_field": "label",
    }
    if through_server:
        report = _structured(asyncio.run(server.call_tool("reverse_convert", arguments)))
        verified = _structured(asyncio.run(server.call_tool("reverse_verify", {"run_dir": str(output)})))
    else:
        report = agent_mcp.reverse_convert(**arguments)
        verified = agent_mcp.reverse_verify(str(output))
    assert report["coordinate_mode"] == "native_registered"
    assert report["native_coordinates_restored"] is True
    assert report["original_cad_restored"] is False
    assert report["absolute_accuracy_verified"] is False
    assert report["registration"]["partition"] == "SECOND"
    assert report["registration"]["manifest_sha256"] == hashlib.sha256(native_registration.read_bytes()).hexdigest()
    point = next(iter(ezdxf.readfile(output / "drawing.dxf").modelspace().query("POINT")))
    assert tuple(point.dxf.location) == pytest.approx((499989, 107, 12.5))
    assert verified["status"] == "verified"
    assert verified["source_geometry_verified"] is True
    assert verified["absolute_accuracy_verified"] is False


@pytest.mark.parametrize("tool, argument", [
    ("reverse_inspect", "source"), ("reverse_convert", "source"),
    ("reverse_convert", "run_dir"), ("reverse_verify", "run_dir"),
    ("reverse_verify", "source"), ("reverse_convert", "registration_manifest"),
    ("reverse_verify", "registration_manifest"),
])
def test_reverse_handler_admission_rejects_outside_roots(source, tmp_path, tool, argument):
    arguments = {"source": str(source), "run_dir": str(tmp_path / "run")}
    if tool == "reverse_inspect":
        arguments.pop("run_dir")
    if tool == "reverse_verify":
        (tmp_path / "run").mkdir()
    arguments[argument] = str(tmp_path.parent / "unadmitted")
    with pytest.raises(agent_mcp.MCPServiceError) as failure:
        getattr(agent_mcp, tool)(**arguments)
    assert failure.value.code == "PATH_OUTSIDE_ROOT"


def test_reverse_verify_admits_recorded_source_before_reading(source, tmp_path):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({
        "source": str(tmp_path.parent / "private.geojson"),
    }), encoding="utf-8")
    with pytest.raises(agent_mcp.MCPServiceError) as failure:
        agent_mcp.reverse_verify(str(directory))
    assert failure.value.code == "PATH_OUTSIDE_ROOT"


@pytest.mark.parametrize("explicit_source", [False, True])
def test_reverse_verify_admits_implicit_registration_path(source, tmp_path, explicit_source):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({
        "source": str(source), "export_options": {
            "registration_manifest": str(tmp_path.parent / "private-registration.json"),
        },
    }), encoding="utf-8")
    with pytest.raises(agent_mcp.MCPServiceError) as failure:
        agent_mcp.reverse_verify(str(directory), **({"source": str(source)} if explicit_source else {}))
    assert failure.value.code == "PATH_OUTSIDE_ROOT"


@pytest.mark.parametrize("invalid_path", [1, {}, ""])
def test_reverse_verify_rejects_invalid_registration_path(source, tmp_path, invalid_path):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({
        "source": str(source), "export_options": {"registration_manifest": invalid_path},
    }), encoding="utf-8")
    with pytest.raises(agent_mcp.MCPServiceError, match="registration_manifest"):
        agent_mcp.reverse_verify(str(directory))


def test_fastmcp_native_path_rejection_preserves_root_boundary(server, source, native_registration, tmp_path):
    from mcp.server.fastmcp.exceptions import ToolError

    denied = str(tmp_path.parent / "private-registration.json")
    with pytest.raises(ToolError, match="PATH_OUTSIDE_ROOT"):
        asyncio.run(server.call_tool("reverse_convert", {
            "source": str(source), "run_dir": str(tmp_path / "new-run"),
            "registration_manifest": denied, "partition": "SECOND",
        }))
    assert not (tmp_path / "new-run").exists()
    agent_mcp.reverse_convert(str(source), run_dir=str(tmp_path / "run"),
        registration_manifest=str(native_registration), partition="SECOND")
    manifest_path = tmp_path / "run/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["export_options"]["registration_manifest"] = denied
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ToolError, match="PATH_OUTSIDE_ROOT"):
        asyncio.run(server.call_tool("reverse_verify", {"run_dir": str(tmp_path / "run")}))


def test_native_verifier_uses_frozen_admitted_registration_path(source, native_registration, tmp_path, monkeypatch):
    agent_mcp.reverse_convert(str(source), run_dir=str(tmp_path / "run"),
        registration_manifest=str(native_registration), partition="SECOND")
    real_read = agent_mcp._json_object
    denied = str(tmp_path.parent / "private-registration.json")

    def change_after_admission(path, **kwargs):
        manifest = real_read(path, **kwargs)
        changed = json.loads(json.dumps(manifest))
        changed["export_options"]["registration_manifest"] = denied
        path.write_text(json.dumps(changed), encoding="utf-8")
        return manifest

    monkeypatch.setattr(agent_mcp, "_json_object", change_after_admission)
    result = agent_mcp.reverse_verify(str(tmp_path / "run"))
    assert result["status"] == "verified"
    assert result["source_geometry_verified"] is True


@pytest.mark.parametrize("through_server", [False, True])
def test_native_verifier_admits_relocated_registration_override(
    server, source, native_registration, tmp_path, through_server,
):
    agent_mcp.reverse_convert(str(source), run_dir=str(tmp_path / "run"),
        registration_manifest=str(native_registration), partition="SECOND")
    relocated = tmp_path / "relocated-registration.json"
    native_registration.rename(relocated)
    arguments = {"run_dir": str(tmp_path / "run"), "registration_manifest": str(relocated)}
    if through_server:
        result = _structured(asyncio.run(server.call_tool("reverse_verify", arguments)))
    else:
        result = agent_mcp.reverse_verify(**arguments)
    assert result["status"] == "verified"
    assert result["source_geometry_verified"] is True


def test_native_mcp_core_runs_without_pillow_or_gdal(source, native_registration, tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc
import sys
class BlockOptionalRenderModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'PIL', 'osgeo'}:
            raise ImportError('Optional render/GDAL import forbidden: ' + fullname)
sys.meta_path.insert(0, BlockOptionalRenderModules())
from cad2gis import agent_mcp
report = agent_mcp.reverse_convert(sys.argv[1], run_dir=sys.argv[3],
    registration_manifest=sys.argv[2], partition='SECOND')
assert report['native_coordinates_restored'] is True
assert agent_mcp.reverse_verify(sys.argv[3])['status'] == 'verified'
assert not ({'PIL', 'osgeo'} & set(sys.modules))
"""
    result = subprocess.run([
        sys.executable, "-c", script, str(source), str(native_registration), str(tmp_path / "isolated-run"),
    ], env={**os.environ, "PYTHONPATH": str(root / "src")}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_reverse_conversion_rejects_dangling_output_symlink(source, tmp_path):
    output = tmp_path / "linked-run"
    destination = tmp_path / "new-target"
    try:
        output.symlink_to(destination, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Cannot create symbolic links on this host: {error}")
    with pytest.raises(agent_mcp.MCPServiceError) as failure:
        agent_mcp.reverse_convert(str(source), run_dir=str(output))
    assert failure.value.code == "REVERSE_OUTPUT_EXISTS"
    assert output.is_symlink()
    assert not destination.exists()


@pytest.mark.parametrize("invalid_source", [None, 1, {}, ""])
def test_reverse_verify_rejects_missing_or_invalid_manifest_source(source, tmp_path, invalid_source):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"source": invalid_source}), encoding="utf-8")
    with pytest.raises(agent_mcp.MCPServiceError, match="source path"):
        agent_mcp.reverse_verify(str(directory))


def test_fastmcp_boundary_preserves_stable_error_and_uncertain_write(server, source, tmp_path):
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as denied:
        asyncio.run(server.call_tool("reverse_convert", {
            "source": str(source), "run_dir": str(tmp_path.parent / "denied-run"),
        }))
    metadata = json.loads(str(denied.value)[str(denied.value).index("{"):])
    assert metadata["error_code"] == "PATH_OUTSIDE_ROOT"
    assert metadata["committed"] == "unknown"
    assert metadata["retryable"] is False
    assert not (tmp_path.parent / "denied-run").exists()


class _Session:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.response


def _bridge(session):
    pytest.importorskip("pydantic_ai")
    from cad2gis.agent_tool_bridge import MCPToolBridge, ToolAttemptBudget

    return MCPToolBridge(
        session, admitted=lambda value, must_exist: agent_mcp._path(value, must_exist=must_exist),
        clean=str, event=lambda *_: None, budget=ToolAttemptBudget(), provider="new-api",
    )


@pytest.mark.parametrize("name, field", [
    ("reverse_inspect", "source"), ("reverse_convert", "run_dir"),
    ("reverse_convert", "registration_manifest"),
    ("reverse_verify", "registration_manifest"),
    ("reverse_verify", "source"), ("reverse_verify", "run_dir"),
])
def test_reverse_bridge_admits_paths_before_rpc(source, tmp_path, name, field):
    from pydantic_ai import ModelRetry

    session = _Session()
    bridge = _bridge(session)
    with pytest.raises(ModelRetry, match="outside configured"):
        asyncio.run(bridge.call(name, {field: str(tmp_path.parent / "unadmitted")}))
    assert not session.calls
    assert not bridge.uncertain_mutations


@pytest.mark.parametrize("error", [TimeoutError("lost response"), asyncio.CancelledError()])
def test_reverse_bridge_does_not_replay_uncertain_conversion(source, tmp_path, error):
    from pydantic_ai import ModelRetry

    session = _Session(error=error)
    bridge = _bridge(session)
    arguments = {"source": str(source), "run_dir": str(tmp_path / "run")}
    with pytest.raises(type(error)):
        asyncio.run(bridge.call("reverse_convert", arguments))
    with pytest.raises(ModelRetry, match="reverse_verify"):
        asyncio.run(bridge.call("reverse_convert", dict(reversed(list(arguments.items())))))
    assert len(session.calls) == 1
    assert len(bridge.uncertain_mutations) == 1


@pytest.mark.parametrize("name", ["reverse_inspect", "reverse_verify"])
def test_reverse_bridge_read_error_does_not_mark_mutation(source, name):
    session = _Session(error=TimeoutError("lost response"))
    bridge = _bridge(session)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            asyncio.run(bridge.call(name, {"source": str(source)}))
    assert len(session.calls) == 2
    assert not bridge.uncertain_mutations


def test_reverse_mcp_import_does_not_load_gis_dependencies():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, "-c",
        "import cad2gis.agent_mcp, sys; "
        "assert not ({'osgeo', 'pyproj', 'shapely', 'ezdxf', 'PIL', 'cad2gis.reverse_verify'} & set(sys.modules))",
    ], env={**os.environ, "PYTHONPATH": str(root / "src")}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
