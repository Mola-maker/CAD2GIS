from __future__ import annotations

import asyncio
import os
import runpy
import sys
from pathlib import Path

import pytest


mcp = pytest.importorskip("mcp")


def test_capabilities_keep_libredwg_primary_and_qgis_downstream() -> None:
    from cad2gis.agent_mcp import get_capabilities

    adapters = get_capabilities()["local_software_adapters"]
    cad_reader = adapters["cad_reader"]
    qgis = adapters["qgis_desktop_session"]

    assert cad_reader["primary_backend"] == "libredwg"
    assert cad_reader["fallback_backend"] == "autocad_core_console"
    assert cad_reader["silent_fallback"] is False
    assert qgis["usage_scope"] == "post_conversion_review_styling_and_fine_tuning_only"
    assert qgis["conversion_authority"] is False


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
        "auto_onboard_and_convert",
        "bootstrap_project",
        "create_decision_pack",
        "create_scene_interpretation_plan",
        "create_semantic_decision_pack",
        "compile_semantic_layers",
        "decide_iteration_candidate",
        "evaluate_iteration_candidate",
        "export_source",
        "export_iteration_learning",
        "export_qgis_desktop_view",
        "get_cad_scene_node",
        "get_scene_visual_region_context",
        "get_capabilities",
        "get_evidence_node",
        "inspect_source",
        "inspect_run",
        "inspect_iteration",
        "inspect_qgis_desktop_session",
        "inspect_semantic_coverage",
        "list_cad_scene_nodes",
        "list_endpoint_join_candidates",
        "list_evidence_nodes",
        "list_label_candidates",
        "list_legend_catalog_candidates",
        "list_network_repair_candidates",
        "list_registered_operations",
        "list_scene_visual_regions",
        "list_semantic_candidates",
        "list_semantic_batches",
        "list_visual_regions",
        "load_qgis_conversion_run",
        "load_qgis_dataset",
        "open_qgis_desktop_project",
        "summarize_semantic_batch",
        "prepare_review_workspace",
        "prepare_ai_onboarding",
        "prepare_iteration_context",
        "prepare_semantic_batches",
        "record_iteration_feedback",
        "resolve_visual_hit",
        "run_conversion",
        "set_qgis_desktop_layer_visibility",
        "start_feedback_iteration",
        "start_qgis_desktop_session",
        "stop_qgis_desktop_session",
        "validate_decision_pack",
        "validate_scene_interpretation_plan",
        "validate_project",
        "zoom_qgis_desktop_full_extent",
    }
