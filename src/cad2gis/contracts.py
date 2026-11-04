"""Versioned package/plugin/skill/MCP compatibility contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any


PLUGIN_CONTRACT_VERSION = "0.8.0"
SKILL_CONTRACT_VERSION = "cad2gis.convert_skill.v3"
ITERATION_SKILL_CONTRACT_VERSION = "cad2gis.iterate_skill.v1"
SKILL_CONTRACTS = {
    "convert-cad-to-gis": SKILL_CONTRACT_VERSION,
    "iterate-cad-to-gis": ITERATION_SKILL_CONTRACT_VERSION,
}
AGENT_PROMPT_CONTRACT_VERSION = "cad2gis.agent_prompt.v3"
MCP_TOOL_CONTRACT_SCHEMA = "cad2gis.mcp_tool_contract.v1"


MCP_TOOL_NAMES = (
    'apply_ai_onboarding',
    'audit_run',
    'audit_visual_conversion',
    'auto_onboard_and_convert',
    'bootstrap_project',
    'build_qgis_review',
    'build_source_svg_delivery',
    'call_cad_bundle_tool',
    'cancel_compile_job',
    'cancel_conversion_job',
    'commit_semantic_patch',
    'compile_semantic_revision',
    'create_decision_pack',
    'create_scene_interpretation_plan',
    'debug_mcp',
    'decide_iteration_candidate',
    'evaluate_iteration_candidate',
    'export_delivery',
    'export_iteration_learning',
    'export_source',
    'get_cad_bundle_status',
    'get_cad_scene_node',
    'get_capabilities',
    'get_conversion_job',
    'get_entity_context_batch',
    'get_evidence_node',
    'get_review_server_status',
    'get_runtime_status',
    'get_scene_visual_region_context',
    'initialize_semantic_store',
    'inspect_iteration',
    'inspect_run',
    'inspect_semantic_store',
    'inspect_source',
    'install_runtime',
    'list_cad_bundle_tools',
    'list_cad_scene_nodes',
    'list_endpoint_join_candidates',
    'list_evidence_nodes',
    'list_geometry_changes',
    'list_label_candidates',
    'list_legend_catalog_candidates',
    'list_network_repair_candidates',
    'list_registered_operations',
    'list_scene_visual_regions',
    'list_visual_regions',
    'package_qgis_delivery',
    'prepare_ai_onboarding',
    'prepare_cad_bundle_workspace',
    'prepare_iteration_context',
    'prepare_review_workspace',
    'prepare_semantic_batches',
    'preview_semantic_patch',
    'publish_matched_delivery',
    'query_relationship_candidates',
    'query_source_entities',
    'reconcile_compile_jobs',
    'record_geometry_decision',
    'record_iteration_feedback',
    'resolve_visual_hit',
    'run_conversion',
    'start_conversion_job',
    'start_feedback_iteration',
    'start_review_server',
    'stop_review_server',
    'validate_decision_pack',
    'validate_project',
    'validate_scene_interpretation_plan',
    'verify_qgis_review',
)


# Opt-in grouping for clients that want a smaller tool list. Every tool is in
# exactly one group; CAD2GIS_MCP_TOOLSETS selects groups ("all" by default).
MCP_TOOLSETS = {
    "core": (
        "get_capabilities", "get_runtime_status", "install_runtime", "debug_mcp",
        "inspect_source", "bootstrap_project", "validate_project",
        "prepare_ai_onboarding", "apply_ai_onboarding", "auto_onboard_and_convert",
        "run_conversion", "start_conversion_job", "get_conversion_job",
        "cancel_conversion_job", "inspect_run", "audit_run",
        "package_qgis_delivery", "prepare_review_workspace",
    ),
    "review": (
        "start_review_server", "get_review_server_status", "stop_review_server",
        "audit_visual_conversion", "build_qgis_review", "verify_qgis_review",
        "build_source_svg_delivery", "publish_matched_delivery",
        "list_geometry_changes", "record_geometry_decision", "export_delivery",
    ),
    "evidence": (
        "list_evidence_nodes", "get_evidence_node", "list_visual_regions",
        "resolve_visual_hit", "list_registered_operations",
        "list_endpoint_join_candidates", "list_network_repair_candidates",
        "validate_decision_pack", "create_decision_pack", "list_label_candidates",
        "list_legend_catalog_candidates",
    ),
    "scene": (
        "list_cad_scene_nodes", "get_cad_scene_node", "list_scene_visual_regions",
        "get_scene_visual_region_context", "create_scene_interpretation_plan",
        "validate_scene_interpretation_plan",
    ),
    "semantic": (
        "export_source", "query_source_entities", "get_entity_context_batch",
        "prepare_semantic_batches", "query_relationship_candidates",
        "initialize_semantic_store", "inspect_semantic_store",
        "preview_semantic_patch", "commit_semantic_patch",
        "compile_semantic_revision", "cancel_compile_job", "reconcile_compile_jobs",
    ),
    "iteration": (
        "start_feedback_iteration", "inspect_iteration", "record_iteration_feedback",
        "prepare_iteration_context", "evaluate_iteration_candidate",
        "decide_iteration_candidate", "export_iteration_learning",
    ),
    "cad_bundle": (
        "get_cad_bundle_status", "prepare_cad_bundle_workspace",
        "list_cad_bundle_tools", "call_cad_bundle_tool",
    ),
}


def enabled_mcp_tools(selection: str | None) -> tuple[str, ...]:
    """Resolve a comma-separated toolset selection; unknown names fail closed."""

    names = [part.strip() for part in (selection or "all").split(",") if part.strip()]
    if not names or "all" in names:
        return MCP_TOOL_NAMES
    unknown = sorted(set(names) - set(MCP_TOOLSETS))
    if unknown:
        raise ValueError(
            f"Unknown CAD2GIS_MCP_TOOLSETS entries {unknown}; choose from "
            f"{sorted(MCP_TOOLSETS)} or 'all'"
        )
    selected = {"core", *names}
    return tuple(sorted(tool for group in selected for tool in MCP_TOOLSETS[group]))

def mcp_tool_contract() -> dict[str, Any]:
    """Return a deterministic digest over the exact advertised tool surface."""

    identity = {
        "schema_version": MCP_TOOL_CONTRACT_SCHEMA,
        "tools": list(MCP_TOOL_NAMES),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return {
        **identity,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "tool_count": len(MCP_TOOL_NAMES),
    }


__all__ = [
    "AGENT_PROMPT_CONTRACT_VERSION",
    "ITERATION_SKILL_CONTRACT_VERSION",
    "MCP_TOOL_CONTRACT_SCHEMA",
    "MCP_TOOL_NAMES",
    "MCP_TOOLSETS",
    "PLUGIN_CONTRACT_VERSION",
    "SKILL_CONTRACT_VERSION",
    "SKILL_CONTRACTS",
    "enabled_mcp_tools",
    "mcp_tool_contract",
]
