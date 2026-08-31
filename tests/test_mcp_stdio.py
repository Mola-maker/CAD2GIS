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
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    project_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["CAD2GIS_PROJECT_ROOT"] = str(project_root)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(project_root / "src"),
        environment.get("PYTHONPATH", ""),
    )))

    async def exercise() -> set[str]:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "cad2gis.agent_mcp"],
            env=environment,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                response = await session.list_tools()
                return {tool.name for tool in response.tools}

    names = asyncio.run(exercise())
    assert names == {
        "apply_ai_onboarding",
        "audit_run",
        "auto_onboard_and_convert",
        "bootstrap_project",
        "create_decision_pack",
            "get_capabilities",
            "get_evidence_node",
            "get_runtime_status",
            "inspect_source",
            "inspect_run",
            "install_runtime",
        "list_endpoint_join_candidates",
        "list_evidence_nodes",
        "list_network_repair_candidates",
        "list_registered_operations",
        "list_visual_regions",
        "prepare_review_workspace",
        "prepare_ai_onboarding",
        "resolve_visual_hit",
        "run_conversion",
        "validate_decision_pack",
        "validate_project",
    }


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
