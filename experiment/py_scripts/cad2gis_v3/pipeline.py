"""Canonical v3 orchestrator: ingest -> semantics -> topology -> CRS -> artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import MappingRegistry, SourceProfile
from .evidence import write_evidence
from .georef import DirectTransformer, enrich_delivery_metrics
from .ingest import ingest
from .semantics import classify_entities
from .styles import write_styles
from .topology import build_topology
from .warehouse import write_delivery


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _implementation_digest():
    package_dir = Path(__file__).resolve().parent
    files = sorted(package_dir.glob("*.py")) + [
        package_dir.parent / "autocad_reader.py",
        package_dir.parent / "apd_rules.py",
        package_dir.parent / "schema_config.py",
        package_dir.parent / "convert_v3.py",
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class ConversionRequest:
    source: Path
    run_dir: Path
    source_profile: Path
    mapping_registry: Path


@dataclass(frozen=True)
class ConversionResult:
    evidence_path: Path
    delivery_path: Path
    style_manifest_path: Path
    run_manifest_path: Path
    counts: dict[str, int]
    diagnostics: dict


def _write_manifest(path, payload):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _enforce_geometry_policy(entities, features, relations, registry, topology_diagnostics):
    required_policy = {
        "source_geometry_immutable": True,
        "crossing_is_connection": False,
        "support_is_optical_node": False,
        "force_route_components_connected": False,
        "generic_line_is_cable": False,
        "dimension_is_cable_geometry": False,
    }
    if registry.policy != required_policy:
        raise RuntimeError(
            f"This pipeline requires the reviewed APD geometry policy: {required_policy}; "
            f"got {registry.policy}"
        )
    source_by_key = {entity.entity_key: entity for entity in entities}
    feature_class_by_key = {feature.feature_key: feature.feature_class for feature in features}
    route_pattern = re.compile(registry.positive_route_layer_regex)
    cables = [feature for feature in features if feature.feature_class == "CABLE"]
    failures = []
    for cable in cables:
        source = source_by_key.get(cable.source_entity_key)
        if source is None:
            failures.append(f"{cable.feature_key}: missing source entity")
            continue
        if source.dwg_type not in {"LWPOLYLINE", "POLYLINE"}:
            failures.append(f"{cable.feature_key}: invalid source type {source.dwg_type}")
        if source.cad_role != "model" or not route_pattern.search(source.layer):
            failures.append(f"{cable.feature_key}: source is not a reviewed model route")
        if cable.geometry_role != "SOURCE_ROUTE":
            failures.append(f"{cable.feature_key}: geometry role is {cable.geometry_role}")
        if tuple(cable.native_points) != tuple(source.points):
            failures.append(f"{cable.feature_key}: source geometry was displaced or re-vertexed")
        if not cable.lineage or any(
            item.get("operation") != "identity" or float(item.get("max_displacement_m", 0.0)) != 0.0
            for item in cable.lineage
        ):
            failures.append(f"{cable.feature_key}: non-identity cable lineage")
    if topology_diagnostics.get("synthetic_route_vertices") != 0:
        failures.append("synthetic route vertices are present")
    for relation in relations:
        if relation.relation_kind != "connects":
            continue
        if (
            feature_class_by_key.get(relation.source_key) == "PTECH"
            or feature_class_by_key.get(relation.target_key) == "PTECH"
        ):
            failures.append(f"{relation.relation_key}: support promoted to optical node")
        if "crossing" in relation.method.casefold():
            failures.append(f"{relation.relation_key}: crossing promoted to connection")
    if failures:
        raise RuntimeError("Geometry policy enforcement failed: " + "; ".join(failures))
    return {
        "cable_sources_checked": len(cables),
        "source_geometry_immutable": True,
        "dimension_or_sling_promotions": 0,
        "synthetic_route_vertices": 0,
        "support_optical_promotions": 0,
        "crossing_connections": 0,
    }


def convert(request: ConversionRequest) -> ConversionResult:
    source = Path(request.source).resolve()
    run_dir = Path(request.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    profile = SourceProfile.load(request.source_profile)
    source_hash = profile.validate_source(source)
    registry = MappingRegistry.load(request.mapping_registry, source_hash)

    entities, ingest_diagnostics = ingest(source, profile)
    features, relations, unresolved, semantic_diagnostics = classify_entities(entities, registry)
    relations, unresolved, topology_diagnostics = build_topology(
        entities, features, registry, relations, unresolved,
    )
    policy_diagnostics = _enforce_geometry_policy(
        entities, features, relations, registry, topology_diagnostics,
    )
    feature_counts = Counter(feature.feature_class for feature in features)
    expected_keys = {
        "PTECH": "plan_poles", "BOITE": "plan_fat", "SITE": "plan_fdt",
        "IMB": "homepass_labels", "CABLE": "positive_cable_routes",
    }
    for feature_class, expected_key in expected_keys.items():
        expected = profile.expected_census[expected_key]
        actual = feature_counts[feature_class]
        if actual != expected:
            raise RuntimeError(f"Semantic census mismatch for {feature_class}: expected {expected}, got {actual}")
    expected_annotations = {
        "BOITE": profile.expected_census["direct_fat_annotations"],
        "PTECH": profile.expected_census["direct_pole_annotations"],
    }
    actual_annotations = semantic_diagnostics["annotation_assignments"]
    for feature_class, expected in expected_annotations.items():
        actual = actual_annotations.get(feature_class, {})
        source_count = actual.get("source_annotations", 0)
        assigned_count = actual.get("assigned", 0)
        if source_count != expected or assigned_count != expected:
            raise RuntimeError(
                f"Annotation assignment regression for {feature_class}: "
                f"expected source/assigned={expected}/{expected}, "
                f"got {source_count}/{assigned_count}"
            )
    expected_components = profile.expected_census["source_route_components"]
    if topology_diagnostics["source_route_components"] != expected_components:
        raise RuntimeError(
            f"APD source route component regression: expected {expected_components}, "
            f"got {topology_diagnostics['source_route_components']}"
        )
    expected_span_roles = {
        "cable_route_span": profile.expected_census["cable_route_span_dimensions"],
        "sling_wire_span": profile.expected_census["sling_wire_span_dimensions"],
    }
    if topology_diagnostics["span_roles"] != expected_span_roles:
        raise RuntimeError(
            f"SPAN role conservation mismatch: expected {expected_span_roles}, "
            f"got {topology_diagnostics['span_roles']}"
        )
    expected_route_memberships = {
        "route_segments_with_span_dimension": profile.expected_census["route_segments_with_span_dimension"],
        "route_segments_without_span_dimension": profile.expected_census["route_segments_without_span_dimension"],
    }
    if any(topology_diagnostics[key] != value for key, value in expected_route_memberships.items()):
        raise RuntimeError(
            f"Route/span membership regression: expected {expected_route_memberships}, got "
            f"{ {key: topology_diagnostics[key] for key in expected_route_memberships} }"
        )
    if topology_diagnostics["span_measurement_max_abs_error_m"] > 1e-6:
        raise RuntimeError(
            "SPAN measurement/source-segment residual exceeds 1e-6 native metres: "
            f"{topology_diagnostics['span_measurement_max_abs_error_m']}"
        )
    graph_expected = {
        "unique_nodes": profile.expected_census["source_route_nodes"],
        "unique_edges": profile.expected_census["source_route_edges"],
        "components": expected_components,
    }
    graph_actual = topology_diagnostics["source_route_graph"]
    if any(graph_actual[key] != value for key, value in graph_expected.items()):
        raise RuntimeError(f"Source route graph mismatch: expected {graph_expected}, got {graph_actual}")
    support_expected = {
        "accepted": profile.expected_census["route_vertices_exact_support"],
        "candidate": profile.expected_census["route_vertices_near_support_candidates"],
        "unresolved": profile.expected_census["route_vertices_unresolved"],
    }
    if topology_diagnostics["route_vertex_support"] != support_expected:
        raise RuntimeError(
            f"Route/support evidence mismatch: expected {support_expected}, "
            f"got {topology_diagnostics['route_vertex_support']}"
        )
    span_support_expected = {
        "accepted_span_dimensions": profile.expected_census["accepted_span_dimensions"],
        "candidate_span_dimensions": profile.expected_census["candidate_span_dimensions"],
        "unique_span_edges": profile.expected_census["accepted_unique_span_edges"],
        "unique_span_edges_all": profile.expected_census["all_unique_span_edges"],
    }
    if any(topology_diagnostics[key] != value for key, value in span_support_expected.items()):
        raise RuntimeError(
            f"SPAN/support evidence mismatch: expected {span_support_expected}, got "
            f"{ {key: topology_diagnostics[key] for key in span_support_expected} }"
        )

    transformer = DirectTransformer(profile.source_crs, profile.target_crs)
    all_points = [point for feature in features for point in feature.native_points]
    roundtrip_error = transformer.roundtrip_error(all_points)
    if roundtrip_error > 1e-6:
        raise RuntimeError(f"CRS round-trip error exceeds 1e-6 source metres: {roundtrip_error}")
    engine_crosscheck = transformer.engine_crosscheck_error(all_points)
    if engine_crosscheck > 1e-6:
        raise RuntimeError(f"OSR/PROJ target-coordinate disagreement exceeds 1e-6 m: {engine_crosscheck}")
    enrich_delivery_metrics(features, transformer)
    diagnostics = {
        "ingest": ingest_diagnostics,
        "semantics": semantic_diagnostics,
        "topology": topology_diagnostics,
        "policy_enforcement": policy_diagnostics,
        "georeference": {
            "source_crs": profile.source_crs,
            "target_crs": profile.target_crs,
            "operation": "direct source-to-target; no EPSG:4326 intermediate geometry",
            "roundtrip_max_source_m": roundtrip_error,
            "engine_crosscheck_max_target_m": engine_crosscheck,
            "coordinate_operation": transformer.operation_metadata(all_points[0] if all_points else None),
        },
    }

    evidence_path = run_dir / "apd_evidence.gpkg"
    delivery_path = run_dir / "apd_delivery.gpkg"
    styles_dir = run_dir / "qgis" / "styles"
    write_evidence(
        evidence_path, entities, features, relations, unresolved,
        diagnostics, transformer.source,
    )
    counts = write_delivery(delivery_path, features, transformer)
    style_manifest_path = write_styles(styles_dir, features, delivery_path)
    manifest_path = run_dir / "run_manifest.json"
    manifest = {
        "schema_version": "cad2gis-run-manifest-v3",
        "pipeline": "experiment-direct-dwg-evidence-first-v3",
        "implementation_sha256": _implementation_digest(),
        "source": {"path": str(source), "sha256": source_hash},
        "profiles": {
            "source_profile": {"path": str(profile.path), "sha256": _sha256(profile.path)},
            "mapping_registry": {"path": str(registry.path), "sha256": _sha256(registry.path)},
        },
        "crs": diagnostics["georeference"],
        "artifacts": {
            "evidence": {"path": str(evidence_path), "sha256": _sha256(evidence_path)},
            "delivery": {"path": str(delivery_path), "sha256": _sha256(delivery_path)},
            "styles": {"path": str(style_manifest_path), "sha256": _sha256(style_manifest_path)},
        },
        "delivery_counts": counts,
        "source_route_components": topology_diagnostics["source_route_components"],
        "unresolved_count": len(unresolved),
        "policy": dict(registry.policy),
    }
    _write_manifest(manifest_path, manifest)
    return ConversionResult(
        evidence_path=evidence_path,
        delivery_path=delivery_path,
        style_manifest_path=style_manifest_path,
        run_manifest_path=manifest_path,
        counts=counts,
        diagnostics=diagnostics,
    )
