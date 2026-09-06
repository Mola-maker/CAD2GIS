"""MCP adapter over the canonical CAD2GIS evidence and conversion services."""
# ruff: noqa: E402 -- native thread limits must be set before GIS imports.

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, cast

from . import __version__
from .runtime import configure_numeric_threads

configure_numeric_threads()

from .contracts import (
    AGENT_PROMPT_CONTRACT_VERSION,
    MCP_TOOL_NAMES,
    PLUGIN_CONTRACT_VERSION,
    SKILL_CONTRACT_VERSION,
    SKILL_CONTRACTS,
    mcp_tool_contract,
)
from .cad2gis_v3.cad_scene_graph import CadSceneGraph
from .cad2gis_v3.artifact_io import read_json_object
from .cad2gis_v3.evidence_graph import EvidenceGraph, EvidenceNode
from .cad2gis_v3.evidence_index import (
    EvidenceIndexError,
    get_indexed_evidence_node,
    index_path_for_graph,
    indexed_nodes_by_kind,
    page_evidence_nodes,
    resolve_evidence_index,
)
from .cad2gis_v3.geometry_repairs import (
    COLLINEAR_MERGE_POLICY_IDS,
    CURVE_MATERIALIZER_ID,
    ENDPOINT_JOIN_POLICIES,
    GeometryRepairError,
    INTERSECTION_SPLIT_POLICY_IDS,
    endpoint_pair_candidates_from_graph,
)
from .cad2gis_v3.label_candidates import generate_label_candidates
from .cad2gis_v3.model import CadStyle, Feature, SourceEntity
from .cad2gis_v3.scene_partition import detect_legend_candidates
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

    def __init__(self, message: str, *, code: str = "VALIDATION_FAILED"):
        super().__init__(message)
        self.code = code


MAX_EVIDENCE_GRAPH_BYTES = 256 * 1024 * 1024


def get_capabilities() -> dict[str, Any]:
    """Describe the stable cross-agent protocol and accuracy boundaries."""

    return {
        "schema_version": "cad2gis.mcp_capabilities.v1",
        "package_version": __version__,
        "plugin_version": PLUGIN_CONTRACT_VERSION,
        "skill_contract_version": SKILL_CONTRACT_VERSION,
        "skill_contracts": dict(SKILL_CONTRACTS),
        "tool_contract": mcp_tool_contract(),
        "protocol": "Model Context Protocol",
        "transports": ["stdio", "streamable-http"],
        "http_endpoint": "/mcp",
        "conversion_engine": "canonical deterministic cad2gis pipeline",
        "host_ai_role": (
            "inventory interpretation and typed proposal generation; source "
            "geometry, fitting, validation, and delivery remain deterministic"
        ),
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
        "filesystem_roots": [str(root) for root in _roots()],
        "runtime": _runtime_status(),
        "workflow": [
            "inspect_source",
            "bootstrap_project",
            "prepare_ai_onboarding",
            "apply_ai_onboarding",
            "validate_project",
            "run_conversion",
            "inspect_run",
            "audit_run",
        ],
        "review_contract": (
            "review edits are revisioned separately; registration export "
            "returns a command that creates a new immutable run"
        ),
        "semantic_database": {
            "workflow": ["export_source", "query_source_entities", "get_entity_context_batch",
                         "prepare_semantic_batches", "query_relationship_candidates",
                         "initialize_semantic_store", "preview_semantic_patch",
                         "commit_semantic_patch", "compile_semantic_revision"],
            "status_tool": "inspect_semantic_store",
            "write_authority": "SQLite revision CAS; immutable source facts",
            "patch_fields": "observed IDs, registered operations and policy IDs only",
            "output_role": "native-coordinate semantic candidate; no automatic delivery promotion",
            "redis": "optional, not configured; SQLite jobs and outbox are durable authority",
        },
        "prompt_contract": {
            "version": AGENT_PROMPT_CONTRACT_VERSION,
            "proposal_mode": "typed JSON tool arguments",
            "identity_rule": (
                "select only source-observed layer, block, entity, evidence, "
                "endpoint, and candidate IDs"
            ),
            "failure_rule": (
                "leave unsupported or ambiguous entities unresolved; never "
                "invent coordinates, geometry, length, CRS, or source facts"
            ),
            "required_claims": [
                "source_geometry",
                "topology",
                "length",
                "coordinate_accuracy",
            ],
        },
    }


def _roots() -> tuple[Path, ...]:
    values: list[str] = []
    for name in ("CAD2GIS_PROJECT_ROOTS", "CAD2GIS_PROJECT_ROOT"):
        raw = os.environ.get(name, "")
        values.extend(part for part in raw.split(os.pathsep) if part.strip())
    claude_project = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if claude_project:
        values.append(claude_project)
    if not values:
        values.append(str(Path.cwd()))
    roots: list[Path] = []
    for value in values:
        root = Path(value).expanduser().resolve()
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _runtime_status() -> dict[str, Any]:
    from .native_runtime import portable_runtime_status
    from .reader.resolver import (
        configured_reader,
        reader_capabilities,
        selected_reader_capability,
    )

    portable = portable_runtime_status()
    selected_reader = configured_reader()
    capabilities = reader_capabilities()
    reader_ready = selected_reader_capability().available
    gdal = portable.get("gdal", {})
    conversion_ready = bool(
        isinstance(gdal, dict)
        and gdal.get("available") is True
        and reader_ready
    )
    return {
        **portable,
        "status": "ready" if conversion_ready else "limited",
        "conversion_ready": conversion_ready,
        "selected_reader": selected_reader,
        "readers": {
            name: capability.to_dict()
            for name, capability in sorted(capabilities.items())
        },
    }


def get_runtime_status() -> dict[str, Any]:
    """Report portable reader/GIS readiness without opening a drawing."""

    return _runtime_status()


def install_runtime() -> dict[str, Any]:
    """Install the checksum-pinned official LibreDWG CLI in the user cache."""

    from .native_runtime import install_portable_runtime

    return install_portable_runtime()


def _path(value: str | Path, *, must_exist: bool = True) -> Path:
    path = Path(value).expanduser().resolve()
    if not any(path == root or root in path.parents for root in _roots()):
        raise MCPServiceError(
            f"Path is outside configured CAD2GIS project roots: {path}",
            code="PATH_OUTSIDE_ROOT",
        )
    if must_exist and not path.exists():
        raise MCPServiceError(f"Path does not exist: {path}")
    return path


def _json_object(path: Path, *, max_bytes: int = 64 * 1024 * 1024) -> dict[str, Any]:
    try:
        return read_json_object(path, max_uncompressed_bytes=max_bytes)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise MCPServiceError(str(exc)) from exc


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


def audit_run(run_dir: str) -> dict[str, Any]:
    """Verify one immutable run's artifacts and delivered layer census."""

    from .native_runtime import ensure_osgeo_runtime
    from .review_server import (
        GeoPackageProvider,
        ReviewServerError,
        _artifact_path,
        _sha256_file,
        _source_path,
    )

    ensure_osgeo_runtime()

    directory = _path(run_dir)
    if not directory.is_dir():
        raise MCPServiceError(f"Run path is not a directory: {directory}")
    manifest = _json_object(_path(directory / "run_manifest.json"))
    artifact_records = manifest.get("artifacts")
    artifact_records = (
        dict(artifact_records) if isinstance(artifact_records, dict) else {}
    )
    artifact_checks: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for name, record in sorted(artifact_records.items()):
        expected_sha = (
            str(record.get("sha256", "")).strip().casefold()
            if isinstance(record, dict) else ""
        )
        try:
            artifact = _artifact_path(manifest, name, run_dir=directory)
            exists = artifact.is_file()
            actual_sha = _sha256_file(artifact) if exists else None
            passed = bool(exists and expected_sha and actual_sha == expected_sha)
            detail = None
        except ReviewServerError as exc:
            artifact = None
            exists = False
            actual_sha = None
            passed = False
            detail = str(exc)
        artifact_checks[name] = {
            "passed": passed,
            "path": str(artifact) if artifact is not None else None,
            "exists": exists,
            "expected_sha256": expected_sha or None,
            "actual_sha256": actual_sha,
            "detail": detail,
        }
        if not passed:
            failures.append(f"artifact:{name}")

    expected_counts_raw = manifest.get("delivery_counts")
    expected_counts = {
        str(name): int(count)
        for name, count in (
            expected_counts_raw.items()
            if isinstance(expected_counts_raw, dict) else ()
        )
    }
    actual_counts: dict[str, int] = {}
    layer_failures: dict[str, dict[str, int | None]] = {}
    delivery_check = artifact_checks.get("delivery", {})
    delivery_path = delivery_check.get("path")
    if delivery_check.get("passed") and isinstance(delivery_path, str):
        try:
            descriptors = GeoPackageProvider(delivery_path).layers()
            actual_counts = {
                str(item["name"]): int(item["feature_count"])
                for item in descriptors
            }
        except ReviewServerError as exc:
            failures.append("delivery:open")
            delivery_check["detail"] = str(exc)
    else:
        failures.append("delivery:unavailable")
    for name in sorted(set(expected_counts) | set(actual_counts)):
        expected = expected_counts.get(name)
        actual = actual_counts.get(name)
        if expected != actual:
            layer_failures[name] = {"expected": expected, "actual": actual}
    if layer_failures:
        failures.append("delivery:layer_census")

    source_path, source_blocker = _source_path(manifest, run_dir=directory)
    warnings = [] if source_path is not None else ["source:not_replayable"]
    return {
        "schema_version": "cad2gis.run_audit.v1",
        "run_dir": str(directory),
        "run_status": manifest.get("run_status"),
        "audit_status": "PASS" if not failures else "FAIL",
        "artifacts": artifact_checks,
        "delivery": {
            "expected_counts": expected_counts,
            "actual_counts": actual_counts,
            "mismatches": layer_failures,
        },
        "source_replay": {
            "available": source_path is not None,
            "path": str(source_path) if source_path is not None else None,
            "blocker": source_blocker,
        },
        "failures": sorted(set(failures)),
        "warnings": warnings,
    }


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


def prepare_ai_onboarding(project_dir: str) -> dict[str, Any]:
    """Return source-bound evidence and a strict proposal schema to the host AI."""

    from .pipeline import prepare_ai_onboarding as prepare_service

    return prepare_service(project_dir=_path(project_dir))


def apply_ai_onboarding(
    source: str,
    project_dir: str,
    proposal: dict[str, Any],
    *,
    proposer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a host/provider AI proposal and derive exact validation gates."""

    from .pipeline import apply_ai_onboarding as apply_service

    result = apply_service(
        source=_path(source),
        project_dir=_path(project_dir),
        proposal=proposal,
        proposer=dict(proposer or {"provider": "host-agent"}),
    )
    # Keep the MCP response small enough for host agents to reason over in one
    # turn.  The complete immutable result, including per-entity coverage
    # records, is persisted by the service for later audit.
    summary = dict(result)
    coverage = dict(summary.get("semantic_coverage") or {})
    coverage_records = list(coverage.pop("records", ()) or ())
    summary["semantic_coverage"] = coverage
    plan_domain = dict(summary.get("plan_domain") or {})
    plan_issues = list(plan_domain.pop("issues", ()) or ())
    if plan_domain:
        plan_domain["issue_count"] = len(plan_issues)
        summary["plan_domain"] = plan_domain
    result_artifact = (
        Path(str(result["project_dir"]))
        / "review"
        / "ai_onboarding_result.json"
    )
    summary["detail_artifact"] = {
        "path": str(result_artifact),
        "semantic_coverage_record_count": len(coverage_records),
        "plan_domain_issue_count": len(plan_issues),
        "authority": "full_deterministic_onboarding_result",
    }
    return summary


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


def _load_cad_scene_graph(graph_path: str) -> CadSceneGraph:
    return CadSceneGraph.from_dict(_json_object(_path(graph_path)))


def export_source(source: str, run_dir: str, source_crs: str | None = None) -> dict[str, Any]:
    """Create a new immutable CAD snapshot; omit CRS unless authoritative evidence exists."""
    from .pipeline import export_source as export
    return export(source=_path(source), run_dir=_path(run_dir, must_exist=False),
                  source_crs=source_crs)


def query_source_entities(
    run_dir: str, *, layer: str | None = None, dwg_type: str | None = None,
    layout: str | None = None, terminal_state: str | None = None,
    text_query: str | None = None, bbox: list[float] | None = None,
    projection: list[str] | None = None, limit: int = 50, cursor: str | None = None,
    view: str = "source", timeout_ms: int = 2000, max_bytes: int = 65536,
) -> dict[str, Any]:
    """Query a snapshot using bounded SQL filters, stable keyset cursors and exact facts."""
    from .cad2gis_v3.source_query import query_source_entities as query
    return query(run_dir=_path(run_dir), layer=layer, dwg_type=dwg_type, layout=layout,
                 terminal_state=terminal_state, text_query=text_query, bbox=bbox,
                 projection=projection, limit=limit, cursor=cursor, view=view,
                 timeout_ms=timeout_ms, max_bytes=max_bytes)


def get_entity_context_batch(
    run_dir: str, entity_keys: list[str], *, fields: list[str] | None = None,
    view: str = "source", cursor: str | None = None, max_bytes: int = 65536,
    timeout_ms: int = 2000,
) -> dict[str, Any]:
    """Read bounded source or instance facts for observed IDs without changing them."""
    from .cad2gis_v3.source_query import get_entity_context_batch as batch
    return batch(run_dir=_path(run_dir), entity_keys=entity_keys, fields=fields,
                 view=view, cursor=cursor, max_bytes=max_bytes, timeout_ms=timeout_ms)


def prepare_semantic_batches(source_run: str, output_dir: str) -> dict[str, Any]:
    """Materialize source-bound semantic candidates in a separate new directory."""
    from .cad2gis_v3.semantic_stage import prepare_semantics
    return prepare_semantics(source_run=_path(source_run),
                             output_dir=_path(output_dir, must_exist=False))


def query_relationship_candidates(
    prepare_manifest: str, *, entity_ids: list[str] | None = None,
    relation_kind: str = "label", policy_id: str | None = None,
    cursor: str | None = None, limit: int = 50, max_bytes: int = 65536,
) -> dict[str, Any]:
    """Read only observed class, label or dimension candidates with registered policy IDs."""
    from .cad2gis_v3.semantic_stage import query_relationship_candidates as query
    return query(prepare_manifest=_path(prepare_manifest), entity_ids=entity_ids,
                 relation_kind=relation_kind, policy_id=policy_id, cursor=cursor,
                 limit=limit, max_bytes=max_bytes)


def initialize_semantic_store(
    source_run: str, prepare_manifest: str, semantic_store: str,
) -> dict[str, Any]:
    """Initialize a durable revision ledger bound to a verified source and candidate set."""
    from .cad2gis_v3.semantic_store import initialize_semantic_store as initialize
    return initialize(source_run=_path(source_run), prepare_manifest=_path(prepare_manifest),
                      semantic_store=_path(semantic_store, must_exist=False))


def inspect_semantic_store(
    semantic_store: str, job_id: str | None = None, idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Read the authoritative semantic revision and optional durable compile job state."""
    from .cad2gis_v3.semantic_store import inspect_semantic_store as inspect_store
    return inspect_store(semantic_store=_path(semantic_store), job_id=job_id,
                         idempotency_key=idempotency_key)


def cancel_compile_job(semantic_store: str, job_id: str) -> dict[str, Any]:
    """Durably cancel a compile job and fence its worker from publishing a result."""
    from .cad2gis_v3.semantic_store import cancel_compile_job as cancel
    return cancel(semantic_store=_path(semantic_store), job_id=job_id)


def reconcile_compile_jobs(
    source_run: str, prepare_manifest: str, semantic_store: str,
) -> dict[str, Any]:
    """Recover interrupted compile jobs; call only when their workers are known stopped."""
    from .cad2gis_v3.semantic_store import reconcile_compile_jobs as reconcile
    return reconcile(source_run=_path(source_run), prepare_manifest=_path(prepare_manifest),
                     semantic_store=_path(semantic_store))


def preview_semantic_patch(
    source_run: str, prepare_manifest: str, semantic_store: str, patch: dict[str, Any],
) -> dict[str, Any]:
    """Validate an ID-only semantic patch against frozen facts and its base revision."""
    from .cad2gis_v3.semantic_store import preview_semantic_patch as preview
    return preview(source_run=_path(source_run), prepare_manifest=_path(prepare_manifest),
                   semantic_store=_path(semantic_store), patch=patch)


def commit_semantic_patch(
    source_run: str, prepare_manifest: str, semantic_store: str, patch: dict[str, Any],
    preview_hash: str, idempotency_key: str,
) -> dict[str, Any]:
    """Atomically commit a validated semantic patch with revision CAS and idempotency."""
    from .cad2gis_v3.semantic_store import commit_semantic_patch as commit
    return commit(source_run=_path(source_run), prepare_manifest=_path(prepare_manifest),
                  semantic_store=_path(semantic_store, must_exist=False), patch=patch,
                  preview_hash=preview_hash, idempotency_key=idempotency_key)


def compile_semantic_revision(
    source_run: str, prepare_manifest: str, semantic_store: str, revision: int,
    output_dir: str, idempotency_key: str, retry_failed: bool = False,
) -> dict[str, Any]:
    """Compile an immutable source-coordinate semantic candidate, without auto promotion."""
    from .cad2gis_v3.semantic_store import compile_semantic_revision as compile_revision
    return compile_revision(source_run=_path(source_run), prepare_manifest=_path(prepare_manifest),
                            semantic_store=_path(semantic_store), revision=revision,
                            output_dir=_path(output_dir, must_exist=False),
                            idempotency_key=idempotency_key, retry_failed=retry_failed)


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


def _stage_objects_from_nodes(
    nodes: tuple[EvidenceNode, ...] | list[EvidenceNode],
    *,
    source_sha256: str,
) -> tuple[list[Feature], list[SourceEntity]]:
    """Rebuild candidate-generator inputs from immutable evidence facts."""

    features: list[Feature] = []
    entities: list[SourceEntity] = []
    for node in nodes:
        facts = node.facts
        if node.kind == "source_entity":
            style = facts.get("style")
            entities.append(SourceEntity.from_record({
                "entity_key": node.logical_id,
                "source_sha256": source_sha256,
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


def _stage_objects_from_graph(
    graph: EvidenceGraph,
) -> tuple[list[Feature], list[SourceEntity]]:
    return _stage_objects_from_nodes(list(graph.nodes), source_sha256=graph.source_sha256)


def list_label_candidates(graph_path: str) -> dict[str, Any]:
    """List advisory, distance-ranked labels without changing source facts."""

    index = _evidence_index(graph_path)
    if index is not None:
        try:
            nodes = tuple(indexed_nodes_by_kind(index, "source_entity")) + tuple(
                indexed_nodes_by_kind(index, "feature")
            )
            metadata = page_evidence_nodes(index, limit=1)
        except EvidenceIndexError as exc:
            raise MCPServiceError(str(exc)) from exc
        features, entities = _stage_objects_from_nodes(
            list(nodes), source_sha256=str(metadata["source_sha256"]),
        )
        result = generate_label_candidates(features, entities)
        return {
            **result,
            "evidence_graph_sha256": metadata["graph_sha256"],
            "query_backend": "sqlite-index",
        }
    graph = _load_graph(graph_path)
    features, entities = _stage_objects_from_graph(graph)
    result = generate_label_candidates(features, entities)
    return {
        **result,
        "evidence_graph_sha256": graph.graph_sha256,
        "query_backend": "canonical-json",
    }


def list_legend_catalog_candidates(
    graph_path: str,
    route_regex: str = "",
) -> dict[str, Any]:
    """List advisory legend/symbol-sample groups from source-bound facts."""

    pattern = None
    if str(route_regex).strip():
        try:
            pattern = re.compile(str(route_regex))
        except re.error as exc:
            raise MCPServiceError(f"Invalid route_regex: {exc}") from exc
    index = _evidence_index(graph_path)
    if index is not None:
        try:
            nodes = list(indexed_nodes_by_kind(index, "source_entity"))
            metadata = page_evidence_nodes(index, limit=1)
        except EvidenceIndexError as exc:
            raise MCPServiceError(str(exc)) from exc
        _, entities = _stage_objects_from_nodes(
            nodes, source_sha256=str(metadata["source_sha256"]),
        )
        result = detect_legend_candidates(entities, route_pattern=pattern)
        return {
            **result,
            "evidence_graph_sha256": metadata["graph_sha256"],
            "query_backend": "sqlite-index",
        }
    graph = _load_graph(graph_path)
    _, entities = _stage_objects_from_graph(graph)
    result = detect_legend_candidates(entities, route_pattern=pattern)
    return {
        **result,
        "evidence_graph_sha256": graph.graph_sha256,
        "query_backend": "canonical-json",
    }


def _load_graph(graph_path: str) -> EvidenceGraph:
    return EvidenceGraph.from_dict(
        _json_object(_path(graph_path), max_bytes=MAX_EVIDENCE_GRAPH_BYTES)
    )


def _evidence_index(graph_path: str, *, allow_standalone: bool = False) -> Path | None:
    graph = _path(graph_path)
    index_path = index_path_for_graph(graph)
    if index_path.exists():
        _path(index_path)
    try:
        binding = resolve_evidence_index(graph, allow_standalone=allow_standalone)
    except EvidenceIndexError as exc:
        raise MCPServiceError(str(exc), code="ARTIFACT_BINDING_INVALID") from exc
    return None if binding is None else binding.index_path


def list_evidence_nodes(
    graph_path: str,
    *,
    kind: str = "",
    cursor: int = 0,
    limit: int = 50,
    allow_standalone: bool = False,
) -> dict[str, Any]:
    """Page through source-bound evidence nodes without changing any facts."""

    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise MCPServiceError("cursor must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise MCPServiceError("limit must be between 1 and 200")
    index = _evidence_index(graph_path, allow_standalone=allow_standalone)
    if index is not None:
        try:
            result = page_evidence_nodes(
                index, kind=kind, cursor=cursor, limit=limit,
            )
            result["binding_status"] = (
                "manifest_bound" if (index.parent.parent / "run_manifest.json").is_file()
                else "standalone_unbound"
            )
            return result
        except EvidenceIndexError as exc:
            raise MCPServiceError(str(exc)) from exc
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
        "query_backend": "canonical-json",
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


def get_evidence_node(graph_path: str, node_id: str, *, allow_standalone: bool = False) -> dict[str, Any]:
    """Read one exact evidence node, including immutable reader facts."""

    index = _evidence_index(graph_path, allow_standalone=allow_standalone)
    if index is not None:
        try:
            node = get_indexed_evidence_node(index, node_id)
        except EvidenceIndexError as exc:
            raise MCPServiceError(str(exc)) from exc
        if node is not None:
            return node
        raise MCPServiceError(f"Unknown evidence node ID: {node_id}")
    graph = _load_graph(graph_path)
    for node in graph.nodes:
        if node.node_id == node_id:
            return node.to_dict()
    raise MCPServiceError(f"Unknown evidence node ID: {node_id}")


def list_visual_regions(graph_path: str) -> dict[str, Any]:
    """List multi-scale render and hit-map artifacts bound to the graph."""

    index = _evidence_index(graph_path)
    if index is not None:
        try:
            indexed = tuple(indexed_nodes_by_kind(index, "render_region"))
        except EvidenceIndexError as exc:
            raise MCPServiceError(str(exc)) from exc
        metadata = page_evidence_nodes(index, kind="render_region", limit=1)
        regions = [
            {
                "node_id": node.node_id,
                "logical_id": node.logical_id,
                **node.facts,
            }
            for node in indexed
        ]
        return {
            "schema_version": "cad2gis.visual_regions.v1",
            "evidence_graph_sha256": metadata["graph_sha256"],
            "authority": "secondary_visual_evidence_only",
            "query_backend": "sqlite-index",
            "regions": regions,
        }
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
        "query_backend": "canonical-json",
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

    from .cad2gis_v3.decision_executor import _EXECUTABLE_OPERATIONS

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
                "registered": True,
                "execution_status": (
                    "executable" if name in _EXECUTABLE_OPERATIONS else "quarantined"
                ),
                "required_validators": ["geometry", "topology", "native_length"],
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
        "url": f"http://127.0.0.1:{port}",
        "launch_command": (
            f'cad2gis review "{directory}" --workspace "{workspace}" --port {port}'
        ),
        "immutable_delivery": True,
    }


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8768,
    stateless_http: bool = False,
):
    """Create the optional FastMCP server for stdio or Streamable HTTP."""

    # Initialize an installed bundled GDAL runtime before FastMCP owns the
    # event loop or dispatches sync tools to worker threads. Native GDAL/PROJ
    # initialization during a live MCP request can deadlock on Windows.
    from .native_runtime import ensure_osgeo_runtime

    ensure_osgeo_runtime()

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
        host=host,
        port=int(port),
        streamable_http_path="/mcp",
        stateless_http=bool(stateless_http),
    )

    # Bundled GDAL activation must happen on the server event-loop thread.
    # Native GIS activation is explicit before our database tools dispatch to
    # worker threads; the SDK itself may run sync tools directly on its loop.
    async def runtime_capabilities_tool() -> dict[str, Any]:
        """Describe the stable CAD2GIS protocol and accuracy boundaries."""

        return get_capabilities()

    async def runtime_status_tool() -> dict[str, Any]:
        """Report portable reader and GIS runtime readiness."""

        return get_runtime_status()

    async def runtime_install_tool() -> dict[str, Any]:
        """Install the checksum-pinned portable LibreDWG runtime."""

        return install_runtime()

    async def debug_tool(
        scope: str = "identity", graph_path: str = "", run_dir: str = "",
        expected_identity: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Inspect code/schema identity, runtime or artifact/query binding without mutation."""
        from .mcp_diagnostics import diagnose

        schemas = [tool.model_dump(mode="json", exclude_none=True)
                   for tool in await server.list_tools()]
        report = diagnose(
            scope=scope, tool_schemas=schemas, expected_identity=expected_identity,
            graph_path=graph_path, run_dir=run_dir,
        )
        try:
            from mcp import types
            from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
            requested = server.get_context().session.client_params.protocolVersion
            # This is the SDK ServerSession initialization negotiation rule.
            report["protocol_version"] = (requested if requested in SUPPORTED_PROTOCOL_VERSIONS
                                          else types.LATEST_PROTOCOL_VERSION)
            report["protocol_note"] = "Negotiated by this initialized SDK ServerSession."
        except (AttributeError, LookupError, ValueError):
            pass
        report["transport"] = getattr(server, "cad2gis_transport", "in_process_or_host_selected")
        return report

    registrations = (
        ("get_capabilities", runtime_capabilities_tool),
        ("debug_mcp", debug_tool),
        ("export_source", export_source),
        ("query_source_entities", query_source_entities),
        ("get_entity_context_batch", get_entity_context_batch),
        ("prepare_semantic_batches", prepare_semantic_batches),
        ("query_relationship_candidates", query_relationship_candidates),
        ("initialize_semantic_store", initialize_semantic_store),
        ("inspect_semantic_store", inspect_semantic_store),
        ("preview_semantic_patch", preview_semantic_patch),
        ("commit_semantic_patch", commit_semantic_patch),
        ("compile_semantic_revision", compile_semantic_revision),
        ("cancel_compile_job", cancel_compile_job),
        ("reconcile_compile_jobs", reconcile_compile_jobs),
        ("get_runtime_status", runtime_status_tool),
        ("install_runtime", runtime_install_tool),
        ("inspect_source", inspect_source),
        ("bootstrap_project", bootstrap_project),
        ("validate_project", validate_project),
        ("prepare_ai_onboarding", prepare_ai_onboarding),
        ("apply_ai_onboarding", apply_ai_onboarding),
        ("auto_onboard_and_convert", auto_onboard_and_convert),
        ("inspect_run", inspect_run),
        ("audit_run", audit_run),
        ("list_evidence_nodes", list_evidence_nodes),
        ("get_evidence_node", get_evidence_node),
        ("list_visual_regions", list_visual_regions),
        ("resolve_visual_hit", resolve_visual_hit),
        ("list_registered_operations", list_registered_operations),
        ("list_endpoint_join_candidates", list_endpoint_join_candidates),
        ("list_network_repair_candidates", list_network_repair_candidates),
        ("validate_decision_pack", validate_decision_pack),
        ("create_decision_pack", create_decision_pack),
        ("decide_iteration_candidate", decide_iteration_candidate),
        ("evaluate_iteration_candidate", evaluate_iteration_candidate),
        ("export_iteration_learning", export_iteration_learning),
        ("get_cad_scene_node", get_cad_scene_node),
        ("inspect_iteration", inspect_iteration),
        ("list_cad_scene_nodes", list_cad_scene_nodes),
        ("list_label_candidates", list_label_candidates),
        ("list_legend_catalog_candidates", list_legend_catalog_candidates),
        ("prepare_iteration_context", prepare_iteration_context),
        ("record_iteration_feedback", record_iteration_feedback),
        ("run_conversion", run_conversion),
        ("start_feedback_iteration", start_feedback_iteration),
        ("prepare_review_workspace", prepare_review_workspace),
    )
    registered_names = tuple(sorted(name for name, _ in registrations))
    if registered_names != MCP_TOOL_NAMES:
        raise RuntimeError(
            "MCP tool registration does not match the versioned tool contract: "
            f"registered={registered_names!r}, contract={MCP_TOOL_NAMES!r}"
        )
    from .mcp_diagnostics import _code_identity, database_tool, traced_tool

    trace_identity = _code_identity()["source_files_sha256"]
    for name, handler in registrations:
        if name in {
            "export_source", "query_source_entities", "get_entity_context_batch",
            "prepare_semantic_batches", "query_relationship_candidates",
            "initialize_semantic_store", "inspect_semantic_store",
            "preview_semantic_patch", "commit_semantic_patch",
            "compile_semantic_revision", "cancel_compile_job", "reconcile_compile_jobs",
        }:
            handler = database_tool(name, handler)
        server.tool(name=name)(traced_tool(name, handler, server, trace_identity))
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
    server = create_server(
        host=args.host,
        port=args.port,
        stateless_http=args.stateless_http,
    )
    server.cad2gis_transport = args.transport
    server.run(transport=args.transport)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "AGENT_PROMPT_CONTRACT_VERSION",
    "MCPServiceError",
    "apply_ai_onboarding",
    "audit_run",
    "auto_onboard_and_convert",
    "bootstrap_project",
    "create_decision_pack",
    "create_server",
    "get_evidence_node",
    "get_capabilities",
    "get_runtime_status",
    "inspect_run",
    "inspect_source",
    "install_runtime",
    "list_evidence_nodes",
    "list_endpoint_join_candidates",
    "list_registered_operations",
    "list_network_repair_candidates",
    "list_visual_regions",
    "main",
    "prepare_review_workspace",
    "prepare_ai_onboarding",
    "resolve_visual_hit",
    "run_conversion",
    "validate_decision_pack",
    "validate_project",
]
