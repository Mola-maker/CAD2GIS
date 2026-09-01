from __future__ import annotations

import asyncio
import os
import runpy
import sys
from pathlib import Path

import pytest


mcp = pytest.importorskip("mcp")


def test_plugin_launcher_does_not_pin_project_root_to_plugin_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    launcher = (
        project_root
        / "plugins"
        / "cad2gis-agent"
        / "scripts"
        / "cad2gis_mcp.py"
    )
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOTS", raising=False)

    runpy.run_path(str(launcher), run_name="cad2gis_plugin_launcher_test")

    assert "CAD2GIS_PROJECT_ROOT" not in os.environ
    assert "CAD2GIS_PROJECT_ROOTS" not in os.environ


def test_mcp_stdio_lists_cad2gis_tools() -> None:
    from cad2gis.contracts import MCP_TOOL_NAMES
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["CAD2GIS_PROJECT_ROOT"] = str(project_root)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(project_root / "src"),
        environment.get("PYTHONPATH", ""),
    )))

    async def exercise() -> dict[str, str | None]:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "cad2gis.agent_mcp"],
            env=environment,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                response = await session.list_tools()
                return {tool.name: tool.description for tool in response.tools}

    tools = asyncio.run(exercise())
    assert set(tools) == set(MCP_TOOL_NAMES)
    assert all(description and description.strip() for description in tools.values())


def test_mcp_runtime_status_completes_without_native_import_deadlock() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["CAD2GIS_PROJECT_ROOT"] = str(project_root)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(project_root / "src"), environment.get("PYTHONPATH", "")))
    )

    async def exercise() -> bool:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "cad2gis.agent_mcp"],
            env=environment,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                response = await asyncio.wait_for(
                    session.call_tool("get_runtime_status", {}), timeout=20
                )
                return not bool(response.isError)

    assert asyncio.run(exercise()) is True


def test_apply_ai_onboarding_mcp_response_uses_persisted_detail_paging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cad2gis import agent_mcp, pipeline

    source = tmp_path / "source.dwg"
    source.write_bytes(b"dwg")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))

    monkeypatch.setattr(
        pipeline,
        "apply_ai_onboarding",
        lambda **_kwargs: {
            "schema_version": "cad2gis.ai_onboarding_compile_result.v1",
            "status": "auto_accepted",
            "project_dir": str(project),
            "semantic_coverage": {
                "status": "WATCH",
                "records": [{"source_entity_key": str(i)} for i in range(1000)],
            },
            "plan_domain": {"status": "PASS", "issues": [{"code": "fixture"}]},
        },
    )

    result = agent_mcp.apply_ai_onboarding(
        str(source),
        str(project),
        {},
    )

    assert "records" not in result["semantic_coverage"]
    assert "issues" not in result["plan_domain"]
    assert result["detail_artifact"]["semantic_coverage_record_count"] == 1000
    assert result["detail_artifact"]["plan_domain_issue_count"] == 1
    assert Path(result["detail_artifact"]["path"]).parts[-2:] == (
        "review",
        "ai_onboarding_result.json",
    )
