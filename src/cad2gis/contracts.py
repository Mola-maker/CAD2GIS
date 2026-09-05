"""Versioned package/plugin/skill/MCP compatibility contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any


PLUGIN_CONTRACT_VERSION = "0.4.0"
SKILL_CONTRACT_VERSION = "cad2gis.convert_skill.v3"
ITERATION_SKILL_CONTRACT_VERSION = "cad2gis.iterate_skill.v1"
SKILL_CONTRACTS = {
    "convert-cad-to-gis": SKILL_CONTRACT_VERSION,
    "iterate-cad-to-gis": ITERATION_SKILL_CONTRACT_VERSION,
}
AGENT_PROMPT_CONTRACT_VERSION = "cad2gis.agent_prompt.v3"
MCP_TOOL_CONTRACT_SCHEMA = "cad2gis.mcp_tool_contract.v1"


MCP_TOOL_NAMES = (
    "apply_ai_onboarding",
    "audit_run",
    "auto_onboard_and_convert",
    "bootstrap_project",
    "cancel_compile_job",
    "commit_semantic_patch",
    "compile_semantic_revision",
    "create_decision_pack",
    "debug_mcp",
    "decide_iteration_candidate",
    "evaluate_iteration_candidate",
    "export_iteration_learning",
    "export_source",
    "get_cad_scene_node",
    "get_capabilities",
    "get_entity_context_batch",
    "get_evidence_node",
    "get_runtime_status",
    "initialize_semantic_store",
    "inspect_iteration",
    "inspect_run",
    "inspect_semantic_store",
    "inspect_source",
    "install_runtime",
    "list_cad_scene_nodes",
    "list_endpoint_join_candidates",
    "list_evidence_nodes",
    "list_label_candidates",
    "list_legend_catalog_candidates",
    "list_network_repair_candidates",
    "list_registered_operations",
    "list_visual_regions",
    "prepare_ai_onboarding",
    "prepare_iteration_context",
    "prepare_review_workspace",
    "prepare_semantic_batches",
    "preview_semantic_patch",
    "query_relationship_candidates",
    "query_source_entities",
    "reconcile_compile_jobs",
    "record_iteration_feedback",
    "resolve_visual_hit",
    "run_conversion",
    "start_feedback_iteration",
    "validate_decision_pack",
    "validate_project",
)


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
    "PLUGIN_CONTRACT_VERSION",
    "SKILL_CONTRACT_VERSION",
    "SKILL_CONTRACTS",
    "mcp_tool_contract",
]
