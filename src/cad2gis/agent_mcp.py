"""MCP adapter over the canonical CAD2GIS evidence and conversion services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, cast

from .cad2gis_v3.cad_scene_graph import CadSceneGraph
from .cad2gis_v3.evidence_graph import EvidenceGraph
from .cad2gis_v3.scene_interpretation import (
    SceneInterpretationError,
    SceneInterpretationPlan,
    SceneRoleAssignment,
    scene_interpretation_json_schema,
)
from .cad2gis_v3.label_candidates import generate_label_candidates
from .cad2gis_v3.model import CadStyle, Feature, SourceEntity
from .cad2gis_v3.scene_partition import detect_legend_candidates
from .cad2gis_v3.geometry_repairs import (
    COLLINEAR_MERGE_POLICY_IDS,
    CURVE_MATERIALIZER_ID,
    ENDPOINT_JOIN_POLICIES,
    GeometryRepairError,
    INTERSECTION_SPLIT_POLICY_IDS,
    endpoint_pair_candidates_from_graph,
)
from .cad2gis_v3.curve_geometry import MATERIALIZATION_POLICY_VERSION
from .cad2gis_v3.repair_decisions import (
    OPERATION_REGISTRY,
    DecisionPack,
    RepairOperation,
    load_decision_pack,
    write_decision_pack_atomic,
)


class MCPServiceError(ValueError):
    """An MCP request escaped its configured project roots or schema."""


def get_capabilities() -> dict[str, Any]:
    """Describe the stable cross-agent protocol and accuracy boundaries."""

    return {
        "schema_version": "cad2gis.mcp_capabilities.v2",
        "protocol": "Model Context Protocol",
        "transports": ["stdio", "streamable-http"],
        "http_endpoint": "/mcp",
        "conversion_engine": "canonical deterministic cad2gis pipeline",
        "host_ai_role": (
            "CAD Scene Graph interpretation, drawing-local ontology and typed "
            "candidate ranking; source geometry, fitting, validation, and "
            "delivery remain deterministic"
        ),
        "reasoning_graphs": {
            "pre_semantic": "cad2gis.cad_scene_graph.v1",
            "pre_semantic_visual": "cad2gis.scene_visual_bundle.v1",
            "scene_role_plan": "cad2gis.scene_interpretation_plan.v1",
            "post_semantic": "cad2gis.evidence_graph.v1",
        },
        "semantic_compiler": {
            "prepare": "cad2gis.semantic_prepare.v2",
            "decisions": "cad2gis.semantic_decisions.v1",
            "output": "cad2gis.semantic_gpkg.v1",
            "authority": "select_observed_ids_only",
            "source_fact_writes": False,
            "unmentioned_entities": "UNRESOLVED",
            "relationship_evidence": [
                "source_endpoint_connectivity",
                "nearby_business_nodes",
                "source_legend_style_matches",
            ],
            "feature_decisions_require_observed_evidence_ids": True,
            "network_length_policy": "positive_source_native_length_required",
        },
        "model_authority": "rank_existing_graph_ids_only",
        "feedback_iteration": {
            "schema": "cad2gis.iteration_session.v1",
            "inputs": ["language_evidence", "content_bound_visual_evidence"],
            "adaptation_scope": "project_interpretation_and_configuration_only",
            "immutable_candidate_runs": True,
            "automatic_promotion": False,
            "user_confirmation_required": True,
            "source_bound_learning": "suggestions_only",
        },
        "local_software_adapters": {
            "cad_reader": {
                "primary_backend": "libredwg",
                "fallback_backend": "autocad_core_console",
                "fallback_activation": (
                    "explicit_after_classified_libredwg_failure_or_parallel_verification"
                ),
                "silent_fallback": False,
                "autocad_internal_fallback": "read_only_com_when_explicitly_enabled",
                "arbitrary_code_execution": False,
            },
            "qgis_desktop_session": {
                "usage_scope": "post_conversion_review_styling_and_fine_tuning_only",
                "conversion_authority": False,
                "transport": "token_authenticated_loopback",
                "host": "127.0.0.1",
                "typed_commands_only": True,
                "arbitrary_python_execution": False,
                "commands": [
                    "start",
                    "status",
                    "open_project",
                    "load_dataset",
                    "load_run",
                    "set_layer_visibility",
                    "zoom_full_extent",
                    "export_view",
                    "stop",
                ],
            },
        },
        "accuracy_contracts": [
            "source_geometry",
            "topology",
            "length",
            "coordinate_accuracy",
        ],
        "absolute_accuracy_rule": (
            "OSM or visual controls are relative references only; surveyed "
            "controls are required to verify absolute positional accuracy"
        ),
    }


def _roots() -> tuple[Path, ...]:
    values: list[str] = []
    for name in ("CAD2GIS_PROJECT_ROOTS", "CAD2GIS_PROJECT_ROOT"):
        raw = os.environ.get(name, "")
        values.extend(part for part in raw.split(os.pathsep) if part.strip())
    if not values:
        values.append(str(Path.cwd()))
    roots: list[Path] = []
    for value in values:
        root = Path(value).expanduser().resolve()
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _path(value: str | Path, *, must_exist: bool = True) -> Path:
    path = Path(value).expanduser().resolve()
    if not any(path == root or root in path.parents for root in _roots()):
        raise MCPServiceError(f"Path is outside configured CAD2GIS project roots: {path}")
    if must_exist and not path.exists():
        raise MCPServiceError(f"Path does not exist: {path}")
    return path


def _json_object(path: Path, *, max_bytes: int = 64 * 1024 * 1024) -> dict[str, Any]:
    if path.stat().st_size > max_bytes:
        raise MCPServiceError(f"JSON artifact exceeds {max_bytes} bytes: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise MCPServiceError(f"JSON artifact root must be an object: {path.name}")
    return payload


def inspect_run(run_dir: str) -> dict[str, Any]:
    """Return the immutable manifest summary for one conversion run."""

    directory = _path(run_dir)
    if not directory.is_dir():
        raise MCPServiceError(f"Run path is not a directory: {directory}")
    manifest_path = directory / "run_manifest.json"
    manifest = _json_object(_path(manifest_path))
    reasoning = manifest.get("reasoning")
    return {
        "run_dir": str(directory),
        "schema_version": manifest.get("schema_version"),
        "run_status": manifest.get("run_status"),
        "source": manifest.get("source"),
        "modes": manifest.get("modes"),
        "delivery_counts": manifest.get("delivery_counts"),
        "plan_domain": manifest.get("plan_domain"),
        "reasoning": reasoning,
        "artifacts": manifest.get("artifacts"),
        "validation": manifest.get("validation"),
    }


def start_feedback_iteration(
    base_run_dir: str,
    *,
    session_dir: str = "",
    max_iterations: int = 3,
) -> dict[str, Any]:
    """Start a bounded, auditable retry loop over one immutable conversion run."""

    from .cad2gis_v3.iteration import start_feedback_iteration as start

    return start(
        _path(base_run_dir),
        session_dir=None if not session_dir else _path(session_dir, must_exist=False),
        max_iterations=max_iterations,
    )


def inspect_iteration(session_path: str) -> dict[str, Any]:
    """Inspect iteration state, budget, pending decisions, and the next action."""

    from .cad2gis_v3.iteration import inspect_iteration as inspect

    return inspect(_path(session_path))


def record_iteration_feedback(
    session_path: str,
    feedback_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind user language and visual evidence to the active source/run hashes."""

    from .cad2gis_v3.iteration import record_iteration_feedback as record

    normalized: list[dict[str, Any]] = []
    for item in feedback_items:
        value = dict(item)
        visual_refs = []
        for reference in value.get("visual_refs") or []:
            visual = dict(reference)
            if visual.get("kind") == "user_image" and visual.get("path"):
                visual["path"] = str(_path(str(visual["path"])))
            visual_refs.append(visual)
        value["visual_refs"] = visual_refs
        normalized.append(value)
    return record(_path(session_path), normalized)


def prepare_iteration_context(session_path: str) -> dict[str, Any]:
    """Create the constrained evidence/routing pack for the next retry."""

    from .cad2gis_v3.iteration import prepare_iteration_context as prepare

    return prepare(_path(session_path))


def evaluate_iteration_candidate(
    session_path: str,
    candidate_run_dir: str,
    addressed_feedback_ids: list[str],
    change_summary: str,
    changed_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    """Gate a new run against the active run without promoting it."""

    from .cad2gis_v3.iteration import evaluate_iteration_candidate as evaluate

    artifacts = [_path(item) for item in (changed_artifacts or [])]
    return evaluate(
        _path(session_path),
        _path(candidate_run_dir),
        addressed_feedback_ids=addressed_feedback_ids,
        change_summary=change_summary,
        changed_artifacts=artifacts,
    )


def decide_iteration_candidate(
    session_path: str,
    candidate_id: str,
    verdict: str,
    rationale: str,
    user_confirmed: bool = False,
) -> dict[str, Any]:
    """Accept/reject/revise a gated candidate; accept needs user confirmation."""

    from .cad2gis_v3.iteration import decide_iteration_candidate as decide

    return decide(
        _path(session_path),
        candidate_id,
        verdict=verdict,
        rationale=rationale,
        user_confirmed=user_confirmed,
    )


def export_iteration_learning(
    session_path: str,
    output_path: str,
) -> dict[str, Any]:
    """Export accepted lessons as source-bound onboarding suggestions."""

    from .cad2gis_v3.iteration import export_iteration_learning as export

    return export(
        _path(session_path),
        _path(output_path, must_exist=False),
    )


def inspect_source(source: str) -> dict[str, Any]:
    """Read and summarize a new DWG without reusing another source's mapping."""

    from .pipeline import inspect_source as inspect_source_service

    source_path = _path(source)
    inventory = inspect_source_service(source=source_path)
    counts = inventory.get("counts", {})
    return {
        "schema_version": "cad2gis-source-inspection-v1",
        "source": {
            "path": str(source_path),
            **dict(inventory.get("source", {})),
        },
        "inspection_status": inventory.get("inspection_status"),
        "reader": inventory.get("reader_protocol", {}),
        "inventory": {
            "entity_count": counts.get("records"),
            "layer_count": len(inventory.get("layers", {})),
            "block_instances": counts.get("block_instances"),
            "annotation_entities": counts.get("annotation_entities"),
            "annotation_entities_with_text": counts.get(
                "annotation_entities_with_text"
            ),
            "native_length_entities": counts.get("native_length_entities"),
            "curve_facts_entities": counts.get("curve_facts_entities"),
            "style_variants": counts.get("style_variants"),
        },
        "plan_domain": inventory.get("plan_domain"),
        "onboarding": inventory.get("onboarding"),
        "inventory_sha256": inventory.get("inventory_sha256"),
    }


def export_source(
    source: str,
    run_dir: str,
    source_crs: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Export authoritative CAD facts and stop before semantic/GIS conversion."""

    from .pipeline import export_source as export_source_service

    return dict(
        export_source_service(
            source=_path(source),
            run_dir=_path(run_dir, must_exist=False),
            source_crs=source_crs,
            force=force,
        )
    )


def prepare_semantic_batches(
    source_run: str,
    output_dir: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Create source-bound semantic candidates for paged AI interpretation."""

    from .pipeline import prepare_semantics

    return dict(prepare_semantics(
        source_run=_path(source_run),
        output_dir=None if not output_dir else _path(output_dir, must_exist=False),
        force=force,
    ))


def list_semantic_candidates(
    prepare_manifest: str,
    cursor: int = 0,
    limit: int = 100,
    batch_id: str = "",
) -> dict[str, Any]:
    """Page exact semantic candidates; never return invented entities."""

    from .cad2gis_v3.semantic_stage import list_semantic_candidates as page

    return page(
        prepare_manifest=_path(prepare_manifest),
        cursor=cursor,
        limit=limit,
        batch_id=batch_id or None,
    )


def list_semantic_batches(
    prepare_manifest: str,
    cursor: int = 0,
    limit: int = 100,
    cad_role: str = "",
) -> dict[str, Any]:
    """Page semantic batches before requesting individual candidates."""

    from .cad2gis_v3.semantic_stage import list_semantic_batches as page

    return page(
        prepare_manifest=_path(prepare_manifest),
        cursor=cursor,
        limit=limit,
        cad_role=cad_role or None,
    )


def summarize_semantic_batch(
    prepare_manifest: str,
    batch_id: str,
) -> dict[str, Any]:
    """Summarize observed layers, types, blocks, labels, and lengths for one batch."""

    from .cad2gis_v3.semantic_stage import summarize_semantic_candidates

    return summarize_semantic_candidates(
        prepare_manifest=_path(prepare_manifest), batch_id=batch_id
    )


def create_semantic_decision_pack(
    prepare_manifest: str,
    output: str,
    decisions: list[dict[str, Any]],
    batch_decisions: list[dict[str, Any]] | None,
    host: str,
    model: str,
    force: bool = False,
) -> dict[str, Any]:
    """Bind typed decisions to the exact source and candidate digests."""

    from .cad2gis_v3.semantic_stage import write_semantic_decision_pack

    return write_semantic_decision_pack(
        prepare_manifest=_path(prepare_manifest),
        output=_path(output, must_exist=False),
        decisions=decisions,
        batch_decisions=batch_decisions or [],
        host=host,
        model=model,
        force=force,
    )


def compile_semantic_layers(
    source_run: str,
    prepare_manifest: str,
    decision_pack: str,
    output: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Compile constrained decisions without allowing AI geometry writes."""

    from .pipeline import compile_semantics

    return dict(compile_semantics(
        source_run=_path(source_run),
        prepare_manifest=_path(prepare_manifest),
        decision_pack=_path(decision_pack),
        output=None if not output else _path(output, must_exist=False),
        force=force,
    ))


def inspect_semantic_coverage(semantic_gpkg: str) -> dict[str, Any]:
    """Validate four-state entity conservation and semantic package integrity."""

    from .pipeline import validate_semantics

    return dict(validate_semantics(semantic_gpkg=_path(semantic_gpkg)))


def bootstrap_project(
    source: str,
    project_dir: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Create a source-bound draft project pack for one previously inspected DWG."""

    from .pipeline import bootstrap_project as bootstrap_project_service

    return bootstrap_project_service(
        source=_path(source),
        project_dir=_path(project_dir, must_exist=False),
        force=force,
    )


def validate_project(project_dir: str) -> dict[str, Any]:
    """Validate source, inventory, mapping, unit, CRS, and review admission gates."""

    from .pipeline import validate_project as validate_project_service

    return validate_project_service(project_dir=_path(project_dir))


def prepare_ai_onboarding(
    project_dir: str,
    learning_registry: str = "",
) -> dict[str, Any]:
    """Return source-bound evidence and a strict proposal schema to the host AI."""

    from .pipeline import prepare_ai_onboarding as prepare_service

    bundle = prepare_service(project_dir=_path(project_dir))
    if learning_registry:
        from .cad2gis_v3.iteration import learning_context_for_bundle

        learning = learning_context_for_bundle(
            _path(learning_registry),
            bundle,
        )
        bundle["iteration_learning"] = learning
        bundle["host_context_sha256"] = hashlib.sha256(
            json.dumps(
                {
                    "bundle_sha256": bundle.get("bundle_sha256"),
                    "learning_registry_sha256": learning["registry_sha256"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    return bundle


def apply_ai_onboarding(
    source: str,
    project_dir: str,
    proposal: dict[str, Any],
    *,
    proposer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a host/provider AI proposal and derive exact validation gates."""

    from .pipeline import apply_ai_onboarding as apply_service

    return apply_service(
        source=_path(source),
        project_dir=_path(project_dir),
        proposal=proposal,
        proposer=dict(proposer or {"provider": "host-agent"}),
    )


def auto_onboard_and_convert(
    source: str,
    project_dir: str,
    run_dir: str,
    *,
    provider: str = "deepseek",
    force_bootstrap: bool = False,
) -> dict[str, Any]:
    """Run provider-backed onboarding, deterministic admission, and conversion."""

    from .pipeline import auto_onboard_project, convert_project

    source_path = _path(source)
    project_path = _path(project_dir, must_exist=not force_bootstrap)
    run_path = _path(run_dir, must_exist=False)
    onboarding = auto_onboard_project(
        source=source_path,
        project_dir=project_path,
        provider=provider,
        force_bootstrap=force_bootstrap,
    )
    result = convert_project(
        source=source_path,
        run_dir=run_path,
        project_dir=project_path,
        llm="off",
    )
    return {
        "schema_version": "cad2gis.auto_onboard_and_convert.v1",
        "onboarding": onboarding,
        "run_status": result.run_status,
        "run_manifest": str(result.run_manifest_path),
        "source": str(result.source_path),
        "evidence": str(result.evidence_path),
        "delivery": str(result.delivery_path),
        "styles": str(result.style_manifest_path),
        "counts": result.counts,
    }


def _load_graph(graph_path: str) -> EvidenceGraph:
    return EvidenceGraph.from_dict(_json_object(_path(graph_path)))


def _load_cad_scene_graph(graph_path: str) -> CadSceneGraph:
    return CadSceneGraph.from_dict(_json_object(_path(graph_path)))


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_artifact(manifest_path: Path, relative_path: str) -> Path:
    relative = Path(str(relative_path))
    if relative.is_absolute() or ".." in relative.parts:
        raise MCPServiceError("Artifact path must be a safe relative path")
    for ancestor in manifest_path.parents:
        candidate = (ancestor / relative).resolve()
        if candidate.is_file():
            return _path(candidate)
    raise MCPServiceError(
        f"Artifact referenced by manifest does not exist: {relative_path}"
    )


def list_cad_scene_nodes(
    graph_path: str,
    *,
    kind: str = "",
    cursor: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Page through immutable pre-semantic CAD scene nodes."""

    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise MCPServiceError("cursor must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise MCPServiceError("limit must be between 1 and 200")
    graph = _load_cad_scene_graph(graph_path)
    values = [node for node in graph.nodes if not kind or node.kind == kind]
    page = values[cursor:cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "schema_version": "cad2gis.cad_scene_nodes_page.v1",
        "graph_sha256": graph.graph_sha256,
        "source_sha256": graph.source_sha256,
        "authority": graph.diagnostics.get("authority"),
        "total": len(values),
        "cursor": cursor,
        "next_cursor": next_cursor if next_cursor < len(values) else None,
        "nodes": [
            {
                "node_id": node.node_id,
                "logical_id": node.logical_id,
                "kind": node.kind,
                "facts_sha256": node.facts_sha256,
            }
            for node in page
        ],
    }


def get_cad_scene_node(graph_path: str, node_id: str) -> dict[str, Any]:
    """Read one exact pre-semantic node with its source-bound facts."""

    graph = _load_cad_scene_graph(graph_path)
    for node in graph.nodes:
        if node.node_id == node_id:
            return node.to_dict()
    raise MCPServiceError(f"Unknown CAD scene node ID: {node_id}")


def list_scene_visual_regions(
    manifest_path: str,
    *,
    layout: str = "",
    cursor: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """Page through layout-aware visual regions bound to a CAD Scene Graph."""

    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise MCPServiceError("cursor must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise MCPServiceError("limit must be between 1 and 100")
    path = _path(manifest_path)
    manifest = _json_object(path)
    if manifest.get("schema_version") != "cad2gis.scene_visual_bundle.v1":
        raise MCPServiceError("Unsupported scene visual manifest schema")
    values = [
        dict(item) for item in manifest.get("regions", ())
        if isinstance(item, dict) and (not layout or item.get("layout") == layout)
    ]
    page = values[cursor:cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "schema_version": "cad2gis.scene_visual_regions_page.v1",
        "source_sha256": manifest.get("source_sha256"),
        "cad_scene_graph_sha256": manifest.get("cad_scene_graph_sha256"),
        "scene_visual_manifest_sha256": _file_sha256(path),
        "authority": manifest.get("authority"),
        "total": len(values),
        "cursor": cursor,
        "next_cursor": next_cursor if next_cursor < len(values) else None,
        "regions": page,
    }


def get_scene_visual_region_context(
    manifest_path: str,
    region_id: str,
    *,
    cursor: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Read one visual region's CAD facts without sending all drawing nodes."""

    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise MCPServiceError("cursor must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise MCPServiceError("limit must be between 1 and 200")
    path = _path(manifest_path)
    manifest = _json_object(path)
    region = next(
        (
            item for item in manifest.get("regions", ())
            if isinstance(item, dict) and item.get("region_id") == region_id
        ),
        None,
    )
    if region is None:
        raise MCPServiceError(f"Unknown scene visual region ID: {region_id}")
    context_path = _relative_artifact(path, str(region.get("context_path", "")))
    if _file_sha256(context_path) != region.get("context_sha256"):
        raise MCPServiceError("Scene region context digest mismatch")
    context = _json_object(context_path)
    node_ids = context.get("entity_node_ids")
    if not isinstance(node_ids, list):
        raise MCPServiceError("Scene region context has no entity_node_ids array")
    layout_context_path = _relative_artifact(
        path, str(region.get("layout_context_path", ""))
    )
    if _file_sha256(layout_context_path) != region.get("layout_context_sha256"):
        raise MCPServiceError("Scene layout context digest mismatch")
    layout_context = _json_object(layout_context_path)
    layout_entities = layout_context.get("entities")
    if not isinstance(layout_entities, list):
        raise MCPServiceError("Scene layout context has no entities array")
    by_node_id = {
        str(item.get("node_id")): item
        for item in layout_entities if isinstance(item, dict) and item.get("node_id")
    }
    missing = set(str(item) for item in node_ids) - set(by_node_id)
    if missing:
        raise MCPServiceError(
            f"Scene region references nodes absent from layout context: {sorted(missing)}"
        )
    entities = [by_node_id[str(node_id)] for node_id in node_ids]
    page = entities[cursor:cursor + limit]
    next_cursor = cursor + len(page)
    render_path = _relative_artifact(path, str(region["render_path"]))
    hit_map_path = _relative_artifact(path, str(region["hit_map_path"]))
    if _file_sha256(render_path) != region.get("render_sha256"):
        raise MCPServiceError("Scene visual render digest mismatch")
    if _file_sha256(hit_map_path) != region.get("hit_map_sha256"):
        raise MCPServiceError("Scene visual hit-map digest mismatch")
    return {
        "schema_version": "cad2gis.scene_region_context_page.v1",
        "region": region,
        "render_path": str(render_path),
        "hit_map_path": str(hit_map_path),
        "total": len(entities),
        "cursor": cursor,
        "next_cursor": next_cursor if next_cursor < len(entities) else None,
        "entities": page,
    }


def create_scene_interpretation_plan(
    graph_path: str,
    visual_manifest_path: str,
    assignments: list[dict[str, Any]],
    *,
    producer: dict[str, Any] | None = None,
    output_path: str = "",
) -> dict[str, Any]:
    """Bind host-AI scene-role rankings to immutable graph and visual hashes."""

    graph = _load_cad_scene_graph(graph_path)
    manifest_path = _path(visual_manifest_path)
    manifest = _json_object(manifest_path)
    try:
        plan = SceneInterpretationPlan.create(
            source_sha256=graph.source_sha256,
            cad_scene_graph_sha256=graph.graph_sha256,
            scene_visual_manifest_sha256=_file_sha256(manifest_path),
            assignments=(SceneRoleAssignment.from_dict(item) for item in assignments),
            producer=dict(producer or {"provider": "host-agent"}),
        )
        validation = plan.validate_against(
            graph,
            manifest,
            visual_manifest_sha256=_file_sha256(manifest_path),
        )
    except SceneInterpretationError as exc:
        raise MCPServiceError(str(exc)) from exc
    written_path = None
    if output_path:
        destination = _path(output_path, must_exist=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        written_path = str(destination)
    return {
        "plan": plan.to_dict(),
        "validation": validation,
        "output_path": written_path,
        "proposal_schema": scene_interpretation_json_schema(),
    }


def validate_scene_interpretation_plan(
    graph_path: str,
    visual_manifest_path: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Validate a persisted AI scene plan against current immutable evidence."""

    graph = _load_cad_scene_graph(graph_path)
    manifest_path = _path(visual_manifest_path)
    manifest = _json_object(manifest_path)
    try:
        parsed = SceneInterpretationPlan.from_dict(plan)
        return parsed.validate_against(
            graph,
            manifest,
            visual_manifest_sha256=_file_sha256(manifest_path),
        )
    except SceneInterpretationError as exc:
        raise MCPServiceError(str(exc)) from exc


def list_evidence_nodes(
    graph_path: str,
    *,
    kind: str = "",
    cursor: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Page through source-bound evidence nodes without changing any facts."""

    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise MCPServiceError("cursor must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise MCPServiceError("limit must be between 1 and 200")
    graph = _load_graph(graph_path)
    values = [node for node in graph.nodes if not kind or node.kind == kind]
    page = values[cursor:cursor + limit]
    next_cursor = cursor + len(page)
    return {
        "graph_sha256": graph.graph_sha256,
        "source_sha256": graph.source_sha256,
        "total": len(values),
        "cursor": cursor,
        "next_cursor": next_cursor if next_cursor < len(values) else None,
        "nodes": [
            {
                "node_id": node.node_id,
                "logical_id": node.logical_id,
                "kind": node.kind,
                "facts_sha256": node.facts_sha256,
            }
            for node in page
        ],
    }


def get_evidence_node(graph_path: str, node_id: str) -> dict[str, Any]:
    """Read one exact evidence node, including immutable reader facts."""

    graph = _load_graph(graph_path)
    for node in graph.nodes:
        if node.node_id == node_id:
            return node.to_dict()
    raise MCPServiceError(f"Unknown evidence node ID: {node_id}")


def list_visual_regions(graph_path: str) -> dict[str, Any]:
    """List multi-scale render and hit-map artifacts bound to the graph."""

    graph = _load_graph(graph_path)
    regions = [
        {
            "node_id": node.node_id,
            "logical_id": node.logical_id,
            **node.facts,
        }
        for node in graph.nodes if node.kind == "render_region"
    ]
    return {
        "schema_version": "cad2gis.visual_regions.v1",
        "evidence_graph_sha256": graph.graph_sha256,
        "authority": "secondary_visual_evidence_only",
        "regions": regions,
    }


def resolve_visual_hit(hit_index_path: str, rgb_hex: str) -> dict[str, Any]:
    """Resolve one six-digit RGB hit-map value to its source entity node."""

    color = str(rgb_hex).strip().lstrip("#").upper()
    if len(color) != 6 or any(char not in "0123456789ABCDEF" for char in color):
        raise MCPServiceError("rgb_hex must be a six-digit hexadecimal RGB value")
    payload = _json_object(_path(hit_index_path))
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise MCPServiceError("Visual hit index has no entries object")
    result = entries.get(color)
    return {
        "schema_version": payload.get("schema_version"),
        "region_id": payload.get("region_id"),
        "rgb_hex": color,
        "hit": result is not None,
        "entity": result,
    }


def list_registered_operations() -> dict[str, Any]:
    """Describe the only repair operations a model may propose."""

    return {
        "schema_version": "cad2gis.repair_operation_registry.v1",
        "operations": {
            name: {
                "risk": spec.risk,
                "min_entities": spec.min_entities,
                "min_evidence": spec.min_evidence,
                "allowed_parameters": sorted(spec.allowed_parameters),
                "required_parameters": sorted(spec.required_parameters),
                "changes_geometry": spec.changes_geometry,
            }
            for name, spec in sorted(OPERATION_REGISTRY.items())
        },
        "registered_geometry_tools": {
            "endpoint_join_policy_ids": sorted(ENDPOINT_JOIN_POLICIES),
            "curve_materializer_ids": [CURVE_MATERIALIZER_ID],
            "curve_materialization_policy_ids": [
                MATERIALIZATION_POLICY_VERSION,
            ],
            "intersection_split_policy_ids": sorted(
                INTERSECTION_SPLIT_POLICY_IDS,
            ),
            "collinear_merge_policy_ids": sorted(
                COLLINEAR_MERGE_POLICY_IDS,
            ),
        },
    }


def list_endpoint_join_candidates(
    graph_path: str,
    left_feature_node_id: str,
    right_feature_node_id: str,
) -> dict[str, Any]:
    """List source-derived endpoint pair IDs selectable by a decision pack."""

    graph = _load_graph(graph_path)
    try:
        candidates = endpoint_pair_candidates_from_graph(
            graph, left_feature_node_id, right_feature_node_id,
        )
    except GeometryRepairError as exc:
        raise MCPServiceError(str(exc)) from exc
    return {
        "schema_version": "cad2gis.endpoint_join_candidates.v1",
        "evidence_graph_sha256": graph.graph_sha256,
        "left_feature_node_id": left_feature_node_id,
        "right_feature_node_id": right_feature_node_id,
        "registered_policy_ids": sorted(ENDPOINT_JOIN_POLICIES),
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def list_network_repair_candidates(graph_path: str) -> dict[str, Any]:
    """List graph-bound crossing and collinear candidates for derived repairs."""

    graph = _load_graph(graph_path)
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    kind_map = {
        "crossing_candidate": {
            "operation": "split_at_observed_intersection",
            "parameter": "intersection_evidence_id",
            "policy_ids": sorted(INTERSECTION_SPLIT_POLICY_IDS),
        },
        "collinear_overlap_candidate": {
            "operation": "merge_collinear_fragments",
            "parameter": "group_id",
            "policy_ids": sorted(COLLINEAR_MERGE_POLICY_IDS),
        },
    }
    candidates = []
    for edge in graph.edges:
        contract = kind_map.get(edge.kind)
        if contract is None:
            continue
        candidates.append({
            "candidate_edge_id": edge.edge_id,
            "kind": edge.kind,
            "source_segment_key": nodes_by_id[edge.source_node_id].logical_id,
            "target_segment_key": nodes_by_id[edge.target_node_id].logical_id,
            "evidence_node_ids": list(edge.evidence_node_ids),
            **contract,
        })
    return {
        "schema_version": "cad2gis.network_repair_candidates.v1",
        "evidence_graph_sha256": graph.graph_sha256,
        "candidates": candidates,
    }


def _stage_objects_from_graph(
    graph: EvidenceGraph,
) -> tuple[list[Feature], list[SourceEntity]]:
    """Rebuild stage-boundary objects from immutable evidence node facts.

    Source-entity and feature nodes carry the full stage facts (centroid,
    points, layer, block, dwg_type, text, geometry kind), so the graph alone
    is sufficient evidence for the deterministic candidate generators.
    """

    features: list[Feature] = []
    entities: list[SourceEntity] = []
    for node in graph.nodes:
        facts = node.facts
        if node.kind == "source_entity":
            style = facts.get("style")
            entities.append(SourceEntity.from_record({
                "entity_key": node.logical_id,
                "source_sha256": graph.source_sha256,
                "handle": facts.get("handle", ""),
                "layout": facts.get("layout", ""),
                "layout_role": facts.get("layout_role", ""),
                "cad_role": facts.get("cad_role", ""),
                "layer": facts.get("layer", ""),
                "object_name": facts.get("object_name", ""),
                "dwg_type_name": facts.get("dwg_type", ""),
                "points": facts.get("points", ()),
                "centroid": facts.get("centroid", (0.0, 0.0)),
                "closed": facts.get("closed", False),
                "text": facts.get("text", ""),
                "block_name": facts.get("block_name", ""),
                "block_attributes": facts.get("block_attributes", {}),
                "native_length": facts.get("native_length"),
                **(style if isinstance(style, dict) else {}),
            }))
        elif node.kind == "feature":
            features.append(Feature(
                feature_key=node.logical_id,
                feature_class=str(facts.get("feature_class", "")),
                geometry_kind=str(facts.get("geometry_kind", "")),
                native_points=[
                    (float(point[0]), float(point[1]))
                    for point in facts.get("native_points", ())
                ],
                source_entity_key=str(facts.get("source_entity_key", "")),
                source_handle=str(facts.get("source_handle", "")),
                source_layer=str(facts.get("source_layer", "")),
                geometry_role=str(facts.get("geometry_role", "")),
                style=CadStyle(),
            ))
    return features, entities


def list_legend_catalog_candidates(
    graph_path: str,
    route_regex: str = "",
) -> dict[str, Any]:
    """List deterministic legend/symbol-sample candidate groups.

    The host AI may only select from the returned candidates (for example to
    declare reviewed legend exclusions in the source profile); the
    candidates themselves change no facts.  ``route_regex`` optionally
    supplies the route-layer pattern used by the cluster rule when the
    graph cannot provide one.
    """

    graph = _load_graph(graph_path)
    pattern = None
    if str(route_regex).strip():
        try:
            pattern = re.compile(str(route_regex))
        except re.error as exc:
            raise MCPServiceError(f"Invalid route_regex: {exc}") from exc
    _, entities = _stage_objects_from_graph(graph)
    result = detect_legend_candidates(entities, route_pattern=pattern)
    return {
        **result,
        "evidence_graph_sha256": graph.graph_sha256,
    }


def list_label_candidates(graph_path: str) -> dict[str, Any]:
    """List deterministic text-to-point-feature label candidates.

    The host AI may only select from the returned candidates (for example as
    input to the registered ``attach_existing_label`` operation); the
    candidates themselves change no facts.
    """

    graph = _load_graph(graph_path)
    features, entities = _stage_objects_from_graph(graph)
    result = generate_label_candidates(features, entities)
    return {
        **result,
        "evidence_graph_sha256": graph.graph_sha256,
    }


def validate_decision_pack(graph_path: str, decision_pack_path: str) -> dict[str, Any]:
    """Verify source binding, content addresses, and all graph references."""

    graph = _load_graph(graph_path)
    pack = load_decision_pack(_path(decision_pack_path))
    pack.validate_against(graph)
    return {
        "valid": True,
        "source_sha256": pack.source_sha256,
        "evidence_graph_sha256": pack.evidence_graph_sha256,
        "pack_sha256": pack.pack_sha256,
        "operation_count": len(pack.operations),
        "operations": [
            {
                "operation_id": item.operation_id,
                "operation": item.operation,
                "risk": item.risk,
                "changes_geometry": item.changes_geometry,
            }
            for item in pack.operations
        ],
    }


def create_decision_pack(
    graph_path: str,
    output_path: str,
    policy_id: str,
    proposer: dict[str, Any],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a typed pack from ID-only proposals and validate it locally."""

    graph = _load_graph(graph_path)
    operation_values = [RepairOperation.create(
        operation=str(item.get("operation", "")),
        entity_node_ids=item.get("entity_node_ids", ()),
        evidence_node_ids=item.get("evidence_node_ids", ()),
        parameters=item.get("parameters", {}),
        confidence=cast(float, item.get("confidence")),
        agreement_count=cast(int, item.get("agreement_count")),
        rationale_sha256=str(item.get("rationale_sha256", "")),
    ) for item in operations]
    pack = DecisionPack.create(
        source_sha256=graph.source_sha256,
        evidence_graph_sha256=graph.graph_sha256,
        policy_id=policy_id,
        proposer=proposer,
        operations=operation_values,
    )
    pack.validate_against(graph)
    destination = _path(output_path, must_exist=False)
    write_decision_pack_atomic(destination, pack)
    return {
        "path": str(destination),
        "pack_sha256": pack.pack_sha256,
        "operation_count": len(pack.operations),
    }


def run_conversion(
    source: str,
    run_dir: str,
    project_dir: str,
    *,
    llm: str = "off",
    decision_pack: str = "",
) -> dict[str, Any]:
    """Run the canonical pipeline; no conversion logic is duplicated here."""

    from .pipeline import convert_project

    result = convert_project(
        source=_path(source),
        run_dir=_path(run_dir, must_exist=False),
        project_dir=_path(project_dir),
        llm=llm,
        decision_pack=None if not decision_pack else _path(decision_pack),
    )
    return {
        "run_status": result.run_status,
        "run_manifest": str(result.run_manifest_path),
        "source": str(result.source_path),
        "evidence": str(result.evidence_path),
        "delivery": str(result.delivery_path),
        "styles": str(result.style_manifest_path),
        "counts": result.counts,
    }


def prepare_review_workspace(
    run_dir: str,
    *,
    workspace_dir: str = "",
    port: int = 8765,
) -> dict[str, Any]:
    """Create the separate edit store and return a local review launch command."""

    from .review_server import SQLiteReviewStore

    directory = _path(run_dir)
    if not directory.is_dir():
        raise MCPServiceError(f"Run path is not a directory: {directory}")
    manifest = _json_object(_path(directory / "run_manifest.json"))
    source = manifest.get("source")
    source_sha = str(source.get("sha256", "")) if isinstance(source, dict) else ""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise MCPServiceError("port must be an integer between 1 and 65535")
    workspace = (
        _path(workspace_dir, must_exist=False)
        if workspace_dir
        else _path(
            directory.parent / f"{directory.name}.review",
            must_exist=False,
        )
    )
    workspace.mkdir(parents=True, exist_ok=True)
    session_id = f"{directory.name}:{source_sha[:16]}"
    database = workspace / "review.sqlite3"
    SQLiteReviewStore(database, session_id=session_id)
    return {
        "schema_version": "cad2gis.review_workspace.v1",
        "run_dir": str(directory),
        "workspace_dir": str(workspace),
        "database": str(database),
        "session_id": session_id,
        "url": f"http://127.0.0.1:{port}/workspace",
        "landing_url": f"http://127.0.0.1:{port}/",
        "install_url": f"http://127.0.0.1:{port}/install",
        "launch_command": (
            f'cad2gis review "{directory}" --workspace "{workspace}" --port {port}'
        ),
        "immutable_delivery": True,
    }


def start_qgis_desktop_session(
    session_dir: str,
    *,
    project_path: str = "",
    startup_timeout: float = 45.0,
) -> dict[str, Any]:
    """Launch a visible, typed QGIS session restricted to configured roots."""

    from .qgis_session import QgisSessionError, start_qgis_session

    try:
        return start_qgis_session(
            _path(session_dir, must_exist=False),
            allowed_roots=_roots(),
            project_path=None if not project_path else _path(project_path),
            startup_timeout=startup_timeout,
        )
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def inspect_qgis_desktop_session(session_file: str) -> dict[str, Any]:
    """Return QGIS version, active project, canvas extent, and layer state."""

    from .qgis_session import QgisSessionError, inspect_qgis_session

    try:
        return inspect_qgis_session(_path(session_file))
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def open_qgis_desktop_project(
    session_file: str, project_path: str
) -> dict[str, Any]:
    """Open one .qgs/.qgz file in a managed QGIS desktop session."""

    from .qgis_session import QgisSessionError, open_qgis_project

    try:
        return open_qgis_project(_path(session_file), _path(project_path))
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def load_qgis_conversion_run(session_file: str, run_dir: str) -> dict[str, Any]:
    """Load a run's delivery GeoPackage and matching QML into QGIS."""

    from .qgis_session import QgisSessionError, load_qgis_run

    try:
        return load_qgis_run(_path(session_file), _path(run_dir))
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def load_qgis_dataset(
    session_file: str, dataset_path: str, styles_dir: str = ""
) -> dict[str, Any]:
    """Load one allowed vector/raster dataset through the real QGIS providers."""

    from .qgis_session import QgisSessionError, load_qgis_layers

    try:
        return load_qgis_layers(
            _path(session_file),
            _path(dataset_path),
            styles_dir=None if not styles_dir else _path(styles_dir),
        )
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def set_qgis_desktop_layer_visibility(
    session_file: str, layer: str, visible: bool
) -> dict[str, Any]:
    """Show or hide one uniquely identified QGIS layer by ID or name."""

    from .qgis_session import QgisSessionError, set_qgis_layer_visibility

    try:
        return set_qgis_layer_visibility(_path(session_file), layer, visible)
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def zoom_qgis_desktop_full_extent(session_file: str) -> dict[str, Any]:
    """Zoom the managed QGIS map canvas to the full visible layer extent."""

    from .qgis_session import QgisSessionError, zoom_qgis_full_extent

    try:
        return zoom_qgis_full_extent(_path(session_file))
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def export_qgis_desktop_view(
    session_file: str,
    output_path: str,
    *,
    width: int = 1600,
    height: int = 1000,
) -> dict[str, Any]:
    """Render the current managed QGIS canvas to a verified PNG artifact."""

    from .qgis_session import QgisSessionError, export_qgis_view

    try:
        return export_qgis_view(
            _path(session_file),
            _path(output_path, must_exist=False),
            width=width,
            height=height,
        )
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def stop_qgis_desktop_session(session_file: str) -> dict[str, Any]:
    """Stop only the dedicated QGIS process represented by this descriptor."""

    from .qgis_session import QgisSessionError, stop_qgis_session

    try:
        return stop_qgis_session(_path(session_file))
    except QgisSessionError as exc:
        raise MCPServiceError(str(exc)) from exc


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8768,
    stateless_http: bool = False,
):
    """Create the optional FastMCP server for stdio or Streamable HTTP."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise RuntimeError(
            "The MCP extra is not installed. Install with: pip install -e .[mcp]"
        ) from exc

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise MCPServiceError(
            "The bundled unauthenticated MCP server is local-only"
        )
    if isinstance(port, bool) or not 1 <= int(port) <= 65535:
        raise MCPServiceError("port must be between 1 and 65535")
    server = FastMCP(
        "cad2gis-agent",
        instructions=(
            "CAD2GIS exposes source-bound CAD evidence, deterministic conversion, and "
            "a typed local QGIS session. Call get_capabilities first. Restrict every "
            "path to configured project roots; inspect before mutation; never invent "
            "CAD geometry, CRS, GCPs, graph IDs, or lengths. Use LibreDWG as the "
            "primary reader. Select the AutoCAD reader only after a classified "
            "LibreDWG failure or for an explicitly requested parallel verification; "
            "never switch readers silently. QGIS tools are only for post-conversion "
            "review, styling, and fine-tuning. They may control only the dedicated "
            "token-authenticated loopback session and do not permit arbitrary Python "
            "execution."
        ),
        host=host,
        port=int(port),
        streamable_http_path="/mcp",
        stateless_http=bool(stateless_http),
    )
    server.tool()(get_capabilities)
    server.tool()(inspect_source)
    server.tool()(export_source)
    server.tool()(prepare_semantic_batches)
    server.tool()(list_semantic_candidates)
    server.tool()(list_semantic_batches)
    server.tool()(summarize_semantic_batch)
    server.tool()(create_semantic_decision_pack)
    server.tool()(compile_semantic_layers)
    server.tool()(inspect_semantic_coverage)
    server.tool()(bootstrap_project)
    server.tool()(validate_project)
    server.tool()(prepare_ai_onboarding)
    server.tool()(apply_ai_onboarding)
    server.tool()(auto_onboard_and_convert)
    server.tool()(inspect_run)
    server.tool()(start_feedback_iteration)
    server.tool()(inspect_iteration)
    server.tool()(record_iteration_feedback)
    server.tool()(prepare_iteration_context)
    server.tool()(evaluate_iteration_candidate)
    server.tool()(decide_iteration_candidate)
    server.tool()(export_iteration_learning)
    server.tool()(list_cad_scene_nodes)
    server.tool()(get_cad_scene_node)
    server.tool()(list_scene_visual_regions)
    server.tool()(get_scene_visual_region_context)
    server.tool()(create_scene_interpretation_plan)
    server.tool()(validate_scene_interpretation_plan)
    server.tool()(list_evidence_nodes)
    server.tool()(get_evidence_node)
    server.tool()(list_visual_regions)
    server.tool()(resolve_visual_hit)
    server.tool()(list_registered_operations)
    server.tool()(list_endpoint_join_candidates)
    server.tool()(list_network_repair_candidates)
    server.tool()(list_legend_catalog_candidates)
    server.tool()(list_label_candidates)
    server.tool()(validate_decision_pack)
    server.tool()(create_decision_pack)
    server.tool()(run_conversion)
    server.tool()(prepare_review_workspace)
    server.tool()(start_qgis_desktop_session)
    server.tool()(inspect_qgis_desktop_session)
    server.tool()(open_qgis_desktop_project)
    server.tool()(load_qgis_dataset)
    server.tool()(load_qgis_conversion_run)
    server.tool()(set_qgis_desktop_layer_visibility)
    server.tool()(zoom_qgis_desktop_full_extent)
    server.tool()(export_qgis_desktop_view)
    server.tool()(stop_qgis_desktop_session)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cad2gis-agent-mcp",
        description="CAD2GIS MCP server for local agent hosts.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument(
        "--stateless-http",
        action="store_true",
        help="Use stateless Streamable HTTP sessions.",
    )
    args = parser.parse_args(argv)
    create_server(
        host=args.host,
        port=args.port,
        stateless_http=args.stateless_http,
    ).run(transport=args.transport)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "MCPServiceError",
    "apply_ai_onboarding",
    "auto_onboard_and_convert",
    "bootstrap_project",
    "create_decision_pack",
    "create_scene_interpretation_plan",
    "create_server",
    "export_source",
    "prepare_semantic_batches",
    "list_semantic_candidates",
    "list_semantic_batches",
    "summarize_semantic_batch",
    "create_semantic_decision_pack",
    "compile_semantic_layers",
    "inspect_semantic_coverage",
    "get_cad_scene_node",
    "get_scene_visual_region_context",
    "get_evidence_node",
    "get_capabilities",
    "inspect_run",
    "inspect_qgis_desktop_session",
    "inspect_source",
    "list_cad_scene_nodes",
    "list_evidence_nodes",
    "list_endpoint_join_candidates",
    "list_label_candidates",
    "list_legend_catalog_candidates",
    "list_registered_operations",
    "list_scene_visual_regions",
    "list_network_repair_candidates",
    "list_visual_regions",
    "load_qgis_conversion_run",
    "load_qgis_dataset",
    "main",
    "prepare_review_workspace",
    "prepare_ai_onboarding",
    "resolve_visual_hit",
    "run_conversion",
    "open_qgis_desktop_project",
    "export_qgis_desktop_view",
    "set_qgis_desktop_layer_visibility",
    "start_qgis_desktop_session",
    "stop_qgis_desktop_session",
    "validate_decision_pack",
    "validate_scene_interpretation_plan",
    "validate_project",
    "zoom_qgis_desktop_full_extent",
]
