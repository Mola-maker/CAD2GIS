"""MCP adapter over the canonical CAD2GIS evidence and conversion services."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

from .cad2gis_v3.evidence_graph import EvidenceGraph
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


AGENT_PROMPT_CONTRACT_VERSION = "cad2gis.agent_prompt.v2"


def get_capabilities() -> dict[str, Any]:
    """Describe the stable cross-agent protocol and accuracy boundaries."""

    return {
        "schema_version": "cad2gis.mcp_capabilities.v1",
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


def audit_run(run_dir: str) -> dict[str, Any]:
    """Verify one immutable run's artifacts and delivered layer census."""

    from .review_server import (
        GeoPackageProvider,
        ReviewServerError,
        _artifact_path,
        _sha256_file,
        _source_path,
    )

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
    server.tool()(get_capabilities)
    server.tool()(inspect_source)
    server.tool()(bootstrap_project)
    server.tool()(validate_project)
    server.tool()(prepare_ai_onboarding)
    server.tool()(apply_ai_onboarding)
    server.tool()(auto_onboard_and_convert)
    server.tool()(inspect_run)
    server.tool()(audit_run)
    server.tool()(list_evidence_nodes)
    server.tool()(get_evidence_node)
    server.tool()(list_visual_regions)
    server.tool()(resolve_visual_hit)
    server.tool()(list_registered_operations)
    server.tool()(list_endpoint_join_candidates)
    server.tool()(list_network_repair_candidates)
    server.tool()(validate_decision_pack)
    server.tool()(create_decision_pack)
    server.tool()(run_conversion)
    server.tool()(prepare_review_workspace)
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
    "inspect_run",
    "inspect_source",
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
