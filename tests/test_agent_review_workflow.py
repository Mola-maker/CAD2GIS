"""Owned MCP review services must prove the displayed run and respect process ownership."""

import asyncio
import hashlib
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from cad2gis import agent_mcp, agent_review
from cad2gis.agent_mcp import MCPServiceError


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def fixture_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CAD2GIS_REVIEW_POSTGIS_DSN", raising=False)
    monkeypatch.setattr(agent_review, "_SERVERS", {})
    run = tmp_path / "run"
    run.mkdir()
    source = run / "fixture.dwg"
    source.write_bytes(b"owned-review-test-source")
    delivery = run / "delivery.gpkg"
    delivery.write_bytes(b"unused delivery for preflight rejection tests")
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4", "run_status": "CONDITIONAL",
        "source": {"path": "fixture.dwg", "sha256": _hash(source)},
        "delivery_counts": {"PTECH": 1},
        "artifacts": {"delivery": {"path": str(delivery), "sha256": _hash(delivery)}},
        "crs": {"source_crs": "EPSG:3857", "target_crs": "EPSG:3857"},
    }
    (run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    yield run, manifest
    for record in agent_review._SERVERS.values():
        agent_review._stop(record, failed_start=True)


def _real_delivery(run, manifest):
    from cad2gis.native_runtime import ensure_osgeo_runtime

    if not ensure_osgeo_runtime().get("available"):
        pytest.skip("Real review lifecycle requires the GDAL/OGR runtime")
    from osgeo import ogr, osr

    delivery = run / "delivery.gpkg"
    delivery.unlink()
    dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(delivery))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(3857)
    layer = dataset.CreateLayer("PTECH", srs=crs, geom_type=ogr.wkbPoint)
    feature = ogr.Feature(layer.GetLayerDefn())
    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint_2D(1000, 2000)
    feature.SetGeometry(point)
    layer.CreateFeature(feature)
    feature = layer = dataset = None
    manifest["artifacts"]["delivery"]["sha256"] = _hash(delivery)
    svg = run / "svg-candidates"
    svg.mkdir()
    (svg / "symbols").mkdir()
    (svg / "correspondence.json").write_text(json.dumps({
        "source_sha256": manifest["source"]["sha256"], "all_points_accounted_for": 1,
        "dispositions": {"SOURCE_INSTANCE_ONLY": 1},
    }))
    (svg / "correspondence.html").write_text('<img src="symbols/point.svg">')
    (svg / "symbols/point.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    manifest["svg_candidates"] = {"mode": "candidate", "status": "completed", "files": {
        path.relative_to(svg).as_posix(): _hash(path) for path in svg.rglob("*") if path.is_file()
    }}
    (run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _http(url):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=3) as response:
        return response.read()


def test_owned_service_serves_live_run_and_svg_then_stops(fixture_run, tmp_path):
    run, manifest = fixture_run
    _real_delivery(run, manifest)
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    port = _free_port()
    result = agent_mcp.start_review_server(str(run), port=port)
    assert result["status"] == "ready" and result["identity_verified"] is True
    assert result["manifest_sha256"] == _hash(run / "run_manifest.json")
    assert result["source_sha256"] == manifest["source"]["sha256"]
    assert result["browser_opened"] is False and result["layers_available"] is True
    assert _http(result["url"] + "/").strip().lower().startswith(b"<!doctype html>")
    assert b'point.svg' in _http(result["url"] + result["svg_candidates"]["report_url"])
    svg_url = result["svg_candidates"]["report_url"].rsplit("/", 1)[0] + "/symbols/point.svg"
    assert b"<svg" in _http(result["url"] + svg_url)
    with pytest.raises(urllib.error.HTTPError) as denied:
        _http(result["url"] + "/api/agent-review")
    assert denied.value.code == 404
    record = agent_review._SERVERS[result["session_id"]]
    assert record.owner_token not in json.dumps(result) + Path(result["log_path"]).read_text()
    assert agent_mcp.start_review_server(str(run), port=port)["session_id"] == result["session_id"]
    assert agent_mcp.get_review_server_status(result["session_id"])["status"] == "ready"
    assert agent_mcp.stop_review_server(result["session_id"])["status"] == "stopped"
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


def test_partition_service_preserves_scope_and_uses_workspace_outside_parent_run(fixture_run):
    run, manifest = fixture_run
    _real_delivery(run, manifest)
    partition = run / "PANEL"
    partition.mkdir()
    (partition / "delivery.gpkg").write_bytes((run / "delivery.gpkg").read_bytes())
    manifest["delivery_partitions"] = {"PANEL": {
        "path": str(partition / "delivery.gpkg"), "sha256": _hash(partition / "delivery.gpkg"),
        "delivery_counts": {"PTECH": 1},
    }}
    (run / "run_manifest.json").write_text(json.dumps(manifest))
    (partition / "partition_manifest.json").write_text(json.dumps({"region_id": "PANEL", "delivery_counts": {"PTECH": 1}}))
    result = agent_mcp.start_review_server(str(partition), port=_free_port())
    assert result["run_dir"] == str(partition) and result["partition"] == "PANEL"
    assert result["manifest_sha256"] == _hash(run / "run_manifest.json")
    assert result["partition_manifest_sha256"] == _hash(partition / "partition_manifest.json")
    assert not Path(result["workspace_dir"]).is_relative_to(run)
    # Closing the owner pipe is also how a crashed or exiting MCP parent releases its child.
    record = agent_review._SERVERS[result["session_id"]]
    record.process.stdin.close()
    record.process.wait(timeout=10)
    assert agent_mcp.get_review_server_status(result["session_id"])["status"] == "stopped"


def test_large_run_metadata_is_ready_but_default_response_budget_remains_bounded(fixture_run):
    run, manifest = fixture_run
    manifest["reasoning"] = {"source_evidence": "x" * 1_600_000}
    _real_delivery(run, manifest)
    manifest_before = (run / "run_manifest.json").read_bytes()
    result = agent_mcp.start_review_server(str(run), port=_free_port())
    assert result["status"] == "ready" and result["identity_verified"] is True
    assert result["layers_available"] is True
    raw = _http(result["url"] + "/api/run")
    assert 1024 * 1024 < len(raw) < 2 * 1024 * 1024
    assert len(json.loads(raw)["reasoning"]["source_evidence"]) == 1_600_000
    with pytest.raises(ValueError, match="exceeds its byte budget"):
        agent_review._json_url(result["url"] + "/api/run")
    with pytest.raises(ValueError, match="exceeds its byte budget"):
        agent_review._json_url(result["url"] + "/api/run", max_bytes=128)
    assert agent_mcp.get_review_server_status(result["session_id"])["status"] == "ready"
    assert agent_mcp.stop_review_server(result["session_id"])["status"] == "stopped"
    assert (run / "run_manifest.json").read_bytes() == manifest_before


def test_changed_manifest_is_not_reported_as_healthy(fixture_run):
    run, manifest = fixture_run
    _real_delivery(run, manifest)
    result = agent_mcp.start_review_server(str(run), port=_free_port())
    manifest["run_status"] = "UNSAFE"
    (run / "run_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(MCPServiceError, match="immutable run identity"):
        agent_mcp.get_review_server_status(result["session_id"])


def test_foreign_listener_is_never_adopted_or_stopped(fixture_run, tmp_path):
    run, _ = fixture_run
    workspace = tmp_path / "new-review"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(MCPServiceError, match="occupied") as error:
            agent_mcp.start_review_server(str(run), str(workspace), listener.getsockname()[1])
        assert error.value.code == "REVIEW_PORT_IN_USE"
        assert listener.fileno() >= 0
    assert not workspace.exists() and not agent_review._SERVERS
    with pytest.raises(MCPServiceError, match="Unknown review session"):
        agent_mcp.stop_review_server("pid-1")


@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_invalid_port_fails_before_workspace_creation(fixture_run, tmp_path, port):
    run, _ = fixture_run
    workspace = tmp_path / "rejected-review"
    with pytest.raises(MCPServiceError, match="port"):
        agent_mcp.start_review_server(str(run), str(workspace), port)
    assert not workspace.exists()


def test_review_workspace_cannot_write_inside_immutable_run_or_outside_roots(fixture_run, tmp_path):
    run, _ = fixture_run
    with pytest.raises(MCPServiceError, match="outside the immutable"):
        agent_mcp.start_review_server(str(run), str(run / "review"))
    with pytest.raises(MCPServiceError) as error:
        agent_mcp.start_review_server(str(run), str(tmp_path.parent / "not-authorized"))
    assert error.value.code == "PATH_OUTSIDE_ROOT"
    other_run = tmp_path / "other-immutable-run"
    other_run.mkdir()
    (other_run / "source_manifest.json").write_text("{}")
    with pytest.raises(MCPServiceError, match="outside immutable"):
        agent_mcp.start_review_server(str(run), str(other_run / "review"))
    assert not (other_run / "review").exists()


@pytest.mark.parametrize("damage", ["delivery", "artifacts", "source"])
def test_unbound_run_is_rejected_before_starting_or_writing(fixture_run, tmp_path, damage):
    run, manifest = fixture_run
    if damage == "delivery":
        (run / "delivery.gpkg").write_bytes(b"changed database")
    else:
        manifest[damage] = ["not an authoritative binding"]
        (run / "run_manifest.json").write_text(json.dumps(manifest))
    workspace = tmp_path / "rejected"
    with pytest.raises(MCPServiceError):
        agent_mcp.start_review_server(str(run), str(workspace))
    assert not workspace.exists() and not agent_review._SERVERS


def test_start_failure_returns_safe_log_recovery_through_mcp(fixture_run, tmp_path, monkeypatch, capsys):
    from cad2gis.mcp_diagnostics import traced_tool

    run, _ = fixture_run
    private_detail = "private-owner-token-never-returned"
    def denied_spawn(*args, **kwargs):
        raise OSError(private_detail)
    # Do not replace the stdlib Popen class globally: MCP imports may still
    # evaluate Popen[...] annotations when this is the first tool failure.
    monkeypatch.setattr(agent_review, "subprocess", SimpleNamespace(
        Popen=denied_spawn, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
        CREATE_NO_WINDOW=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ))
    server = SimpleNamespace(get_context=lambda: SimpleNamespace(request_id="review-failure"))
    wrapped = traced_tool("start_review_server", agent_mcp.start_review_server, server)
    with pytest.raises(Exception) as error:
        wrapped(str(run), str(tmp_path / "failed-workspace"), _free_port())
    payload = json.loads(str(error.value))
    assert payload["error_code"] == "REVIEW_START_FAILED" and payload["retryable"] is False
    recovery = payload["review_recovery"]
    assert set(recovery) == {"session_id", "log_path"}
    assert recovery["session_id"].startswith("review-")
    assert Path(recovery["log_path"]).is_file()
    assert private_detail not in str(error.value) + capsys.readouterr().err
    assert not agent_review._SERVERS


def test_graceful_stop_does_not_interrupt_inflight_review_actions():
    class BusyProcess:
        stdin = None
        def poll(self):
            return None
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("confirmed review action", timeout)
        def terminate(self):
            pytest.fail("A confirmed review action must not be interrupted")
    record = SimpleNamespace(process=BusyProcess(), stopping=False)
    agent_review._stop(record)
    assert record.stopping is True


def test_registered_workflow_schemas_and_mutation_contract_are_complete():
    from cad2gis.contracts import MCP_TOOL_NAMES
    from cad2gis.mcp_diagnostics import MUTATING_TOOLS

    tools = {tool.name: tool for tool in asyncio.run(agent_mcp.create_server().list_tools())}
    assert set(tools) == set(MCP_TOOL_NAMES)
    expected = {"publish_matched_delivery", "build_qgis_review", "build_source_svg_delivery", "verify_qgis_review", "audit_visual_conversion",
                "start_review_server", "get_review_server_status", "stop_review_server"}
    assert expected <= set(tools)
    assert expected - {"get_review_server_status"} <= MUTATING_TOOLS
    assert "get_review_server_status" not in MUTATING_TOOLS
    assert tools["start_review_server"].inputSchema["required"] == ["run_dir"]
    assert tools["stop_review_server"].inputSchema["required"] == ["session_id"]
    assert tools["get_review_server_status"].inputSchema["required"] == ["session_id"]
    assert tools["build_source_svg_delivery"].inputSchema["required"] == ["baseline_dir", "assets_dir", "output_dir"]
    assert tools["publish_matched_delivery"].inputSchema["required"] == ["run_dir", "delivery_root"]
    assert tools["build_qgis_review"].inputSchema["required"] == ["project_path", "osm_data", "output_path"]
    assert tools["audit_visual_conversion"].inputSchema["properties"]["partition"]["default"] == ""
    assert "gcp_profile" in tools["run_conversion"].inputSchema["properties"]
    assert "geometry_repairs" in tools["apply_ai_onboarding"].inputSchema["properties"]
    capabilities = agent_mcp.get_capabilities()
    assert capabilities["matched_delivery"]["tool"] == "publish_matched_delivery"
    assert capabilities["matched_delivery"]["reference_workflow"] == [
        "package_qgis_delivery", "build_source_svg_delivery", "build_qgis_review", "verify_qgis_review",
    ]
    assert capabilities["review_server"]["start_tool"] == "start_review_server"


def test_run_conversion_forwards_only_authorized_gcp_profile(fixture_run, tmp_path, monkeypatch):
    from cad2gis import pipeline

    run, _ = fixture_run
    gcp = tmp_path / "reviewed-gcp.json"
    gcp.write_text("{}")
    calls = []
    def converted(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(run_status="CONDITIONAL", run_manifest_path=run / "run_manifest.json",
                               source_path=run / "fixture.dwg", evidence_path=run / "evidence.gpkg",
                               delivery_path=run / "delivery.gpkg", style_manifest_path=run / "styles.json",
                               counts={}, diagnostics={})
    monkeypatch.setattr(pipeline, "convert_project", converted)
    agent_mcp.run_conversion(str(run / "fixture.dwg"), str(tmp_path / "next-run"), str(tmp_path), gcp_profile=str(gcp))
    assert calls[0]["gcp_profile"] == gcp
    with pytest.raises(MCPServiceError) as error:
        agent_mcp.run_conversion(str(run / "fixture.dwg"), str(tmp_path / "other-run"), str(tmp_path),
                                 gcp_profile=str(tmp_path.parent / "not-authorized.json"))
    assert error.value.code == "PATH_OUTSIDE_ROOT" and len(calls) == 1
@pytest.mark.parametrize("invalid", ["empty_spatial", "wrong_count"])
def test_review_rejects_real_layers_that_disagree_with_manifest(fixture_run, invalid):
    run, manifest = fixture_run
    _real_delivery(run, manifest)
    if invalid == "empty_spatial":
        from osgeo import ogr

        delivery = run / "delivery.gpkg"
        delivery.unlink()
        dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(delivery))
        dataset.CreateLayer("metadata", geom_type=ogr.wkbNone)
        dataset = None
        manifest["artifacts"]["delivery"]["sha256"] = _hash(delivery)
    else:
        manifest["delivery_counts"]["PTECH"] = 2
    (run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(MCPServiceError, match="authoritative delivery counts"):
        agent_mcp.start_review_server(str(run), port=_free_port())
    assert all(record.process.poll() is not None for record in agent_review._SERVERS.values())
