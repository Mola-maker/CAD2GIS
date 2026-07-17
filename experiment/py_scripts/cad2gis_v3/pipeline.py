"""Canonical v3 orchestrator: ingest -> semantics -> topology -> CRS -> artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .calibration import GCPProfile, fit_profile
from .config import MappingRegistry, SourceProfile
from .evidence import write_evidence
from .georef import (
    DeliveryTransformer,
    DirectTransformer,
    enrich_delivery_metrics,
    feature_adjustment_records,
)
from .ingest import ingest
from .implementation import implementation_manifest_fields, production_conversion_provenance
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
    """Compatibility wrapper for callers of the former scalar-only helper."""
    return production_conversion_provenance()["sha256"]


@dataclass(frozen=True)
class ConversionRequest:
    source: Path
    run_dir: Path
    source_profile: Path
    mapping_registry: Path
    gcp_profile: Path | None = None


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


def _publish_run_bundle(staged_run_dir: Path, destination_run_dir: Path) -> None:
    """Publish one complete run directory with rollback on replacement failure.

    All GeoPackages, styles and the manifest are closed and validated inside a
    same-volume staging directory before this function is called.  An existing
    destination is first renamed as one unit.  On Windows this also provides a
    fail-fast lock check: if QGIS holds a file without delete sharing, the
    rename fails before any current artifact is replaced.
    """
    staged = Path(staged_run_dir).resolve()
    destination = Path(destination_run_dir).resolve()
    if staged.parent != destination.parent:
        raise ValueError("Run bundle staging and destination must share a parent directory")
    if not staged.is_dir():
        raise ValueError(f"Staged run directory does not exist: {staged}")

    backup = destination.with_name(
        f".{destination.name}.backup.{os.getpid()}.{time.time_ns()}"
    )
    had_existing = destination.exists()
    moved_existing = False
    try:
        if had_existing:
            os.replace(destination, backup)
            moved_existing = True
        try:
            os.replace(staged, destination)
        except Exception:
            if moved_existing and backup.exists() and not destination.exists():
                os.replace(backup, destination)
                moved_existing = False
            raise
    except Exception:
        if moved_existing and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    else:
        if backup.exists():
            try:
                shutil.rmtree(backup)
            except OSError:
                # Publication is already complete.  A consumer may still hold
                # the retired bundle; leave the hidden backup for later cleanup.
                pass


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


def _calibration_observations(profile, result, transformer):
    """Normalize every reviewed control, including explicitly excluded points."""
    residuals = {residual.point_id: residual for residual in result.residuals}
    active_ids = {control.point_id for control in profile.active_controls}
    if set(residuals) != active_ids:
        raise RuntimeError(
            "GCP result/control mismatch: "
            f"missing={sorted(active_ids - set(residuals))}, "
            f"unexpected={sorted(set(residuals) - active_ids)}"
        )
    observations = []
    for control in profile.controls:
        if not control.enabled:
            nominal = transformer.point(control.cad_point)
            observations.append({
                "point_id": control.point_id,
                "role": control.role,
                "source": control.source,
                "accuracy_m": control.accuracy_m,
                "weight": control.weight,
                "enabled": False,
                "inlier": None,
                "cad_x": control.cad_point[0],
                "cad_y": control.cad_point[1],
                "nominal_easting": nominal[0],
                "nominal_northing": nominal[1],
                "predicted_easting": None,
                "predicted_northing": None,
                "observed_easting": control.target_point[0],
                "observed_northing": control.target_point[1],
                "residual_dx_m": None,
                "residual_dy_m": None,
                "residual_m": None,
                "status": "excluded_by_review",
            })
            continue

        residual = residuals[control.point_id]
        observations.append({
            "point_id": control.point_id,
            "role": control.role,
            "source": control.source,
            "accuracy_m": control.accuracy_m,
            "weight": control.weight,
            "enabled": True,
            "inlier": residual.inlier,
            "cad_x": control.cad_point[0],
            "cad_y": control.cad_point[1],
            "nominal_easting": residual.nominal_point[0],
            "nominal_northing": residual.nominal_point[1],
            "predicted_easting": residual.adjusted_point[0],
            "predicted_northing": residual.adjusted_point[1],
            "observed_easting": control.target_point[0],
            "observed_northing": control.target_point[1],
            # Residual vector points from the prediction to the observed control.
            "residual_dx_m": residual.target_point[0] - residual.adjusted_point[0],
            "residual_dy_m": residual.target_point[1] - residual.adjusted_point[1],
            "residual_m": residual.error_m,
            "status": (
                "independent_check" if control.role == "check"
                else "inlier" if residual.inlier else "outlier"
            ),
        })
    return observations


def _bbox(points):
    if not points:
        return None
    return {
        "min_easting": min(point[0] for point in points),
        "min_northing": min(point[1] for point in points),
        "max_easting": max(point[0] for point in points),
        "max_northing": max(point[1] for point in points),
    }


def _calibration_spatial_coverage(profile, transformer, drawing_native_points):
    """Record deterministic coverage diagnostics; approval remains explicit."""
    drawing = [transformer.point(point) for point in drawing_native_points]
    active = list(profile.active_controls)
    train = [transformer.point(control.cad_point) for control in active if control.role == "train"]
    check = [transformer.point(control.cad_point) for control in active if control.role == "check"]
    drawing_bbox = _bbox(drawing)
    train_bbox = _bbox(train)

    def coverage(axis):
        if drawing_bbox is None or train_bbox is None:
            return None
        low, high = ("min_easting", "max_easting") if axis == "x" else (
            "min_northing", "max_northing",
        )
        denominator = drawing_bbox[high] - drawing_bbox[low]
        if denominator <= 0.0:
            return None
        return (train_bbox[high] - train_bbox[low]) / denominator

    outside = None
    if train_bbox is not None:
        outside = sum(
            not (
                train_bbox["min_easting"] <= point[0] <= train_bbox["max_easting"]
                and train_bbox["min_northing"] <= point[1] <= train_bbox["max_northing"]
            )
            for point in drawing
        )
    return {
        "reviewed": profile.validation.spatial_distribution_reviewed,
        "review_source": getattr(
            profile.validation, "spatial_distribution_review_source", "",
        ),
        "drawing_vertex_count": len(drawing),
        "training_control_count": len(train),
        "check_control_count": len(check),
        "drawing_bbox": drawing_bbox,
        "training_bbox": train_bbox,
        "check_bbox": _bbox(check),
        "training_extent_coverage_x_ratio": coverage("x"),
        "training_extent_coverage_y_ratio": coverage("y"),
        "drawing_vertices_outside_training_bbox": outside,
    }


def convert(request: ConversionRequest) -> ConversionResult:
    source = Path(request.source).resolve()
    run_dir = Path(request.run_dir).resolve()
    run_dir.parent.mkdir(parents=True, exist_ok=True)
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
        missing_count = actual.get("missing", source_count - assigned_count)
        if source_count != expected or assigned_count != expected or missing_count != 0:
            raise RuntimeError(
                f"Annotation assignment regression for {feature_class}: "
                f"expected source/assigned/missing={expected}/{expected}/0, "
                f"got {source_count}/{assigned_count}/{missing_count}"
            )
    expected_annotation_families = {
        "fat": profile.expected_census["direct_fat_annotations"],
        "pole_new": profile.expected_census["direct_new_pole_annotations"],
        "pole_existing": profile.expected_census["direct_existing_pole_annotations"],
    }
    family_configs = {
        family.family_id: family for family in registry.annotation_families
    }
    actual_families = semantic_diagnostics["annotation_assignments_by_family"]
    if set(actual_families) != set(expected_annotation_families):
        raise RuntimeError(
            "Annotation family census mismatch: "
            f"expected {sorted(expected_annotation_families)}, "
            f"got {sorted(actual_families)}"
        )
    for family_id, expected in expected_annotation_families.items():
        actual = actual_families[family_id]
        observed = (
            actual.get("source_annotations", 0),
            actual.get("target_assets", 0),
            actual.get("assigned", 0),
            actual.get("missing", 0),
            actual.get("unresolved", 0),
        )
        required = (expected, expected, expected, 0, 0)
        if observed != required:
            raise RuntimeError(
                f"Annotation family regression for {family_id}: "
                "expected source/target/assigned/missing/unresolved="
                f"{required}, got {observed}"
            )
        if (
            family_configs[family_id].require_same_layer
            and actual.get("cross_layer_assignments", 0) != 0
        ):
            raise RuntimeError(
                f"Annotation family isolation regression for {family_id}: "
                "cross_layer_assignments must be 0"
            )
    ptech_cross_layer = actual_annotations["PTECH"].get("cross_layer_assignments", 0)
    if ptech_cross_layer != 0:
        raise RuntimeError(
            "PTECH annotation family isolation regression: "
            f"expected cross_layer_assignments=0, got {ptech_cross_layer}"
        )
    discovery_failures = semantic_diagnostics.get(
        "annotation_discovery_failure_counts", {}
    )
    if discovery_failures:
        raise RuntimeError(
            "Annotation discovery failures require reviewed mapping rules: "
            f"{discovery_failures}"
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
    expected_native_lengths = profile.expected_census["source_route_native_lengths"]
    if topology_diagnostics["source_route_native_lengths"] != expected_native_lengths:
        raise RuntimeError(
            "CABLE AutoCAD native-length conservation mismatch: "
            f"expected {expected_native_lengths}, got "
            f"{topology_diagnostics['source_route_native_lengths']}"
        )
    if topology_diagnostics["source_route_native_length_max_abs_delta_m"] > 1e-6:
        raise RuntimeError(
            "CABLE AutoCAD native length differs from source-segment sum by more "
            "than 1e-6 m; curved/bulged segment extraction requires review: "
            f"{topology_diagnostics['source_route_native_length_max_abs_delta_m']}"
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
    selected_transformer = transformer
    calibration_audit = None
    calibration_diagnostics = {"status": "not_provided"}
    gcp_profile = None
    if request.gcp_profile is not None:
        gcp_profile = GCPProfile.load(
            request.gcp_profile, expected_source_sha256=source_hash,
        )
        gcp_profile.validate_transformer(transformer)
        calibration_result = fit_profile(gcp_profile, transformer)
        result_payload = calibration_result.to_dict()
        if gcp_profile.enabled:
            if calibration_result.validation_passed is not True:
                raise RuntimeError(
                    "GCP calibration failed independent validation: "
                    + "; ".join(calibration_result.validation_failures)
                )
            selected_transformer = DeliveryTransformer(
                transformer, calibration_result, profile_sha256=gcp_profile.sha256,
            )
            if not selected_transformer.calibration_active:
                raise RuntimeError("Enabled GCP profile did not produce an active calibration model")
            status = "accepted"
            feature_displacements = feature_adjustment_records(
                features, transformer, selected_transformer,
            )
        else:
            status = "disabled"
            feature_displacements = []
        calibration_diagnostics = {
            "status": status,
            "profile_path": str(gcp_profile.path),
            "profile_sha256": gcp_profile.sha256,
            "source_sha256": gcp_profile.source_sha256,
            "spatial_coverage": _calibration_spatial_coverage(
                gcp_profile, transformer, all_points,
            ),
            "result": result_payload,
        }
        calibration_audit = {
            **calibration_diagnostics,
            "observations": _calibration_observations(
                gcp_profile, calibration_result, transformer,
            ),
            "feature_displacements": feature_displacements,
        }

    enrich_delivery_metrics(features, selected_transformer)
    operation_metadata = transformer.operation_metadata(all_points[0] if all_points else None)
    if calibration_diagnostics["status"] == "accepted":
        operation_metadata["absolute_accuracy_validation"] = (
            "nominal CRS operation only; accepted GCP validation is recorded in calibration"
        )
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
            "coordinate_operation": operation_metadata,
            "calibration": calibration_diagnostics,
        },
    }

    evidence_path = run_dir / "apd_evidence.gpkg"
    delivery_path = run_dir / "apd_delivery.gpkg"
    style_manifest_path = run_dir / "qgis" / "styles" / "style_manifest.json"
    manifest_path = run_dir / "run_manifest.json"
    staged_run_dir = Path(tempfile.mkdtemp(
        prefix=f".{run_dir.name}.stage.", dir=run_dir.parent,
    )).resolve()
    try:
        staged_evidence_path = staged_run_dir / evidence_path.name
        staged_delivery_path = staged_run_dir / delivery_path.name
        staged_styles_dir = staged_run_dir / "qgis" / "styles"
        write_evidence(
            staged_evidence_path, entities, features, relations, unresolved,
            diagnostics, transformer.source,
            calibration_audit=calibration_audit, target_srs=transformer.target,
            delivery_transformer=selected_transformer,
        )
        counts = write_delivery(staged_delivery_path, features, selected_transformer)
        staged_style_manifest_path = write_styles(
            staged_styles_dir, features, staged_delivery_path,
        )
        implementation = production_conversion_provenance()
        manifest = {
            "schema_version": "cad2gis-run-manifest-v3",
            "pipeline": "experiment-direct-dwg-evidence-first-v3",
            **implementation_manifest_fields(implementation),
            "publication": {
                "status": "complete",
                "strategy": "same-volume-staged-run-directory-swap",
            },
            "source": {"path": str(source), "sha256": source_hash},
            "profiles": {
                "source_profile": {"path": str(profile.path), "sha256": _sha256(profile.path)},
                "mapping_registry": {"path": str(registry.path), "sha256": _sha256(registry.path)},
            },
            "crs": diagnostics["georeference"],
            "artifacts": {
                "evidence": {
                    "path": str(evidence_path), "sha256": _sha256(staged_evidence_path),
                },
                "delivery": {
                    "path": str(delivery_path), "sha256": _sha256(staged_delivery_path),
                },
                "styles": {
                    "path": str(style_manifest_path),
                    "sha256": _sha256(staged_style_manifest_path),
                },
            },
            "delivery_counts": counts,
            "source_route_components": topology_diagnostics["source_route_components"],
            "unresolved_count": len(unresolved),
            "policy": dict(registry.policy),
        }
        if gcp_profile is not None:
            manifest["profiles"]["gcp_profile"] = {
                "path": str(gcp_profile.path), "sha256": gcp_profile.sha256,
            }
        _write_manifest(staged_run_dir / manifest_path.name, manifest)
        _publish_run_bundle(staged_run_dir, run_dir)
    finally:
        if staged_run_dir.exists():
            shutil.rmtree(staged_run_dir, ignore_errors=True)
    return ConversionResult(
        evidence_path=evidence_path,
        delivery_path=delivery_path,
        style_manifest_path=style_manifest_path,
        run_manifest_path=manifest_path,
        counts=counts,
        diagnostics=diagnostics,
    )
