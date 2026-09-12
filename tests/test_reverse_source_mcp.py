"""Source primitive MCP workflows share canonical facts and project admission."""
from __future__ import annotations

import asyncio
import json

import ezdxf
import pytest

from cad2gis import agent_mcp, cli, reverse_source
from cad2gis.contracts import MCP_TOOL_NAMES
from cad2gis.mcp_diagnostics import MUTATING_TOOLS
from cad2gis.reverse_models import ReverseConversionError
from test_reverse_source import snapshot


@pytest.fixture
def admitted(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return snapshot(tmp_path, monkeypatch, include_text=True)[0]


def test_actual_mcp_source_inspect_select_convert_and_verify(admitted, tmp_path):
    server = agent_mcp.create_server()

    async def exercise():
        inspected = await server.call_tool("reverse_source_inspect", {"source_manifest": str(admitted), "max_entities": 2})
        converted = await server.call_tool("reverse_source_convert", {"source_manifest": str(admitted),
            "run_dir": str(tmp_path / "run"), "entity_types": ["LINE", "CIRCLE"]})
        verified = await server.call_tool("reverse_source_verify", {"run_dir": str(tmp_path / "run")})
        return inspected, converted, verified

    def payload(value):
        # FastMCP local calls return (content, structuredContent).
        if isinstance(value, tuple):
            return value[1]
        return value.structuredContent

    inspected, converted, verified = map(payload, asyncio.run(exercise()))
    assert inspected["supported_entity_count"] == 5
    assert inspected["returned_entity_count"] == 2 and inspected["truncated"]
    assert converted["selection"]["selected_entity_count"] == 2
    assert converted["selection"]["excluded_entity_count"] == 5
    assert verified["status"] == "verified" and verified["entity_count"] == 2
    assert verified["GIS_edits_applied"] is False and verified["original_cad_restored"] is False
    assert {entity.dxftype() for entity in ezdxf.readfile(tmp_path / "run/drawing.dxf").modelspace()} == {"LINE", "CIRCLE"}


def test_source_cli_exposes_selection_and_real_parameter_verification(admitted, tmp_path, capsys):
    assert cli.main(["reverse-source-convert", str(admitted), "--run-dir", str(tmp_path / "cli-run"),
                     "--entity-type", "ELLIPSE", "--json"]) == 0
    converted = json.loads(capsys.readouterr().out)
    assert converted["selection"]["selected_entity_count"] == 1
    assert cli.main(["reverse-source-verify", str(tmp_path / "cli-run"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["entity_types"] == {"ELLIPSE": 1}


def test_source_tools_declared_schema_and_write_annotations():
    tools = {tool.name: tool for tool in asyncio.run(agent_mcp.create_server().list_tools())}
    assert tuple(sorted(tools)) == MCP_TOOL_NAMES
    for name in ("reverse_source_inspect", "reverse_source_convert", "reverse_source_verify"):
        mutating = name == "reverse_source_convert"
        assert tools[name].annotations.readOnlyHint is not mutating
        assert tools[name].annotations.idempotentHint is not mutating
        assert (name in MUTATING_TOOLS) is mutating
    assert set(tools["reverse_source_convert"].inputSchema["properties"]) == {
        "source_manifest", "run_dir", "entity_types", "layout", "output_format"}


def test_source_missing_facts_remain_explicit_failure(admitted, tmp_path):
    with pytest.raises(ReverseConversionError, match="Select entity_types"):
        agent_mcp.reverse_source_convert(str(admitted), run_dir=str(tmp_path / "refused"))
    assert not (tmp_path / "refused").exists()


def test_source_manifest_and_implicit_verify_source_obey_project_roots(admitted, tmp_path, monkeypatch):
    run = tmp_path / "run"
    agent_mcp.reverse_source_convert(str(admitted), run_dir=str(run), entity_types=["LINE"])
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(run))
    with pytest.raises(agent_mcp.MCPServiceError) as error:
        agent_mcp.reverse_source_inspect(str(admitted))
    assert error.value.code == "PATH_OUTSIDE_ROOT"
    with pytest.raises(agent_mcp.MCPServiceError) as error:
        agent_mcp.reverse_source_verify(str(run))
    assert error.value.code == "PATH_OUTSIDE_ROOT"


def test_artifact_path_escape_rejected_before_referenced_bytes_are_read(admitted, tmp_path):
    value = json.loads(admitted.read_text(encoding="utf-8"))
    value["artifacts"]["reader_records"]["path"] = str(tmp_path.parent / "outside-secret.jsonl")
    admitted.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ReverseConversionError, match="fixed path"):
        agent_mcp.reverse_source_inspect(str(admitted))


def test_verify_freezes_admitted_implicit_manifest(admitted, tmp_path, monkeypatch):
    run = tmp_path / "run"
    agent_mcp.reverse_source_convert(str(admitted), run_dir=str(run), entity_types=["ARC"])
    actual = reverse_source.verify_source_reproduction

    def race(directory, **kwargs):
        saved = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        saved["source_manifest"] = str(tmp_path.parent / "outside/source_manifest.json")
        (run / "manifest.json").write_text(json.dumps(saved), encoding="utf-8")
        assert kwargs["source_manifest"] == admitted
        return actual(directory, **kwargs)

    monkeypatch.setattr(reverse_source, "verify_source_reproduction", race)
    assert agent_mcp.reverse_source_verify(str(run))["status"] == "verified"


@pytest.mark.parametrize("limit", [0, 201, True, "50"])
def test_inspect_detail_limit_is_bounded(admitted, limit):
    with pytest.raises(agent_mcp.MCPServiceError, match="max_entities"):
        agent_mcp.reverse_source_inspect(str(admitted), max_entities=limit)
