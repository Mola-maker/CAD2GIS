"""MCP QGZ packaging uses the real portable delivery writer and verifies outputs."""

import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from cad2gis import agent_mcp, delivery
from cad2gis.agent_mcp import MCPServiceError, package_qgis_delivery


@pytest.fixture
def qgis_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    run = tmp_path / "conversion"
    run.mkdir()
    with closing(sqlite3.connect(run / "delivery.gpkg")) as db, db:
        db.executescript("""CREATE TABLE gpkg_geometry_columns(table_name,column_name,geometry_type_name,srs_id);
          INSERT INTO gpkg_geometry_columns VALUES('CABLE','geom','LINESTRING',3857);
          CREATE TABLE gpkg_contents(table_name,min_x,min_y,max_x,max_y);
          INSERT INTO gpkg_contents VALUES('CABLE',0,0,10,20);
          CREATE TABLE gpkg_spatial_ref_sys(srs_id,definition);
          INSERT INTO gpkg_spatial_ref_sys VALUES(3857,'');
          CREATE TABLE CABLE(fid INTEGER,geom BLOB,display_label TEXT,length_value_m REAL,length_source TEXT);
          INSERT INTO CABLE VALUES(1,NULL,'12.5m',12.5,'dwg_dimension');""")
    (run / "run_manifest.json").write_text(json.dumps({
        "run_status": "CONDITIONAL", "source": {"sha256": "a" * 64},
        "delivery_counts": {"CABLE": 1},
        "artifacts": {"delivery": {"sha256": hashlib.sha256((run / "delivery.gpkg").read_bytes()).hexdigest()}},
    }))
    return run


def test_qgis_tool_creates_verified_actual_qgz_and_complete_zip(qgis_run, tmp_path):
    before = {path.name: path.read_bytes() for path in qgis_run.iterdir()}
    result = package_qgis_delivery(str(qgis_run), str(tmp_path / "QGIS results"))
    assert result["status"] == "completed" and result["run_status"] == "CONDITIONAL"
    assert result["source_run_unchanged"] is True
    assert result["standalone_qgz"] is False and result["download_complete_zip"] is True
    assert result["absolute_accuracy_verified"] is False
    assert result["verification"]["qgis_render_verified"] is False
    assert result["verification"]["zip_contents_verified"] is True
    assert result["projects"][0]["layers"] == {"CABLE": 1}
    project = Path(result["projects"][0]["path"])
    assert project.suffix == ".qgz" and project.is_file()
    with zipfile.ZipFile(project) as qgz:
        assert b"./delivery.gpkg|layername=CABLE" in qgz.read("delivery.qgs")
    archive = Path(result["archive"]["path"])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["archive"]["sha256"]
    with zipfile.ZipFile(archive) as package:
        assert package.read("delivery.gpkg") == before["delivery.gpkg"]
        assert package.read("delivery.qgz") == project.read_bytes()
        assert "checksums.json" in package.namelist()
    assert {path.name: path.read_bytes() for path in qgis_run.iterdir()} == before


@pytest.mark.parametrize("conflict", ["directory", "archive"])
def test_qgis_tool_never_overwrites_existing_output(qgis_run, tmp_path, conflict):
    output = tmp_path / "delivery"
    if conflict == "directory":
        output.mkdir()
        sentinel = output / "keep"
    else:
        sentinel = output.with_suffix(".zip")
    sentinel.write_bytes(b"existing user output")
    with pytest.raises(MCPServiceError, match="already exists") as error:
        package_qgis_delivery(str(qgis_run), str(output))
    assert error.value.code == "OUTPUT_EXISTS"
    assert sentinel.read_bytes() == b"existing user output"


def test_qgis_tool_rejects_unapproved_and_immutable_output_paths(qgis_run, tmp_path):
    with pytest.raises(MCPServiceError, match="outside configured"):
        package_qgis_delivery(str(qgis_run), str(tmp_path.parent / "unapproved-qgis-output"))
    with pytest.raises(MCPServiceError, match="outside configured"):
        package_qgis_delivery(str(tmp_path.parent), str(tmp_path / "output"))
    with pytest.raises(MCPServiceError, match="outside the immutable run"):
        package_qgis_delivery(str(qgis_run), str(qgis_run / "qgis-output"))
    assert not (qgis_run / "qgis-output").exists()


def test_qgis_tool_rejects_source_symlinks_before_packaging(qgis_run, tmp_path, monkeypatch):
    foreign = tmp_path / "other-data"
    foreign.write_bytes(b"not part of run")
    try:
        (qgis_run / "linked-data").symlink_to(foreign)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    monkeypatch.setattr(delivery, "package_delivery", lambda *args: pytest.fail("Redirected source reached package writer"))
    with pytest.raises(MCPServiceError, match="symlinks"):
        package_qgis_delivery(str(qgis_run), str(tmp_path / "output"))


def test_qgis_tool_rejects_unbound_delivery_bytes(qgis_run, tmp_path):
    with closing(sqlite3.connect(qgis_run / "delivery.gpkg")) as db, db:
        db.execute("UPDATE CABLE SET length_value_m=99")
    with pytest.raises(MCPServiceError, match="does not match the canonical manifest"):
        package_qgis_delivery(str(qgis_run), str(tmp_path / "output"))
    assert not (tmp_path / "output.zip").exists()


def test_qgis_tool_independently_rejects_changed_packaged_files(qgis_run, tmp_path, monkeypatch):
    writer = delivery.package_delivery

    def damaged(run, output):
        result = writer(run, output)
        (output / "delivery.qgz").write_bytes(b"not the verified project")
        return result

    monkeypatch.setattr(delivery, "package_delivery", damaged)
    with pytest.raises(MCPServiceError, match="checksum inventory"):
        package_qgis_delivery(str(qgis_run), str(tmp_path / "output"))


def test_qgis_tool_is_advertised_with_real_output_and_verification_boundaries(qgis_run):
    from cad2gis.contracts import MCP_TOOL_NAMES

    server = agent_mcp.create_server()
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert "package_qgis_delivery" in MCP_TOOL_NAMES
    assert set(tools) == set(MCP_TOOL_NAMES)
    contract = tools["package_qgis_delivery"]
    assert contract.inputSchema["required"] == ["run_dir", "output_dir"]
    assert "complete ZIP" in contract.description and "not an actual QGIS render" in contract.description
    capabilities = agent_mcp.get_capabilities()
    assert capabilities["tool_contract"]["tool_count"] == len(tools)
    assert capabilities["qgis_delivery"]["tool"] == "package_qgis_delivery"
    assert capabilities["qgis_delivery"]["qgis_render_verified"] is False
