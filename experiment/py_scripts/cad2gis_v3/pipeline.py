"""Canonical v3 orchestrator: ingest -> semantics -> topology -> CRS -> artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Mapping
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
from .model import canonical_curve_fingerprint
from .semantics import classify_entities
from .spatial_coverage import (
    evaluate_spatial_coverage,
    source_entity_drawing_points,
)
from .styles import write_styles
from .topology import build_topology
from .warehouse import (
    CABLE_SEGMENT_SCHEMA_VERSION,
    CABLE_SEGMENT_UNIT,
    write_delivery,
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(value) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


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


def _validate_reviewed_policy(registry):
    """Validate the immutable review contract before inspecting any geometry."""
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
            "Reviewed policy contract validation failed: "
            f"this pipeline requires {required_policy}; "
            f"got {registry.policy}"
        )


def _validate_source_geometry(
    entities, features, registry, *, require_curve_facts=False,
):
    """Validate immutable CAD route geometry without topology inference.

    This domain binds delivery CABLE features to their ordered native/WCS
    vertices and canonical curve facts.  It deliberately does not inspect
    graph components, support proximity, crossings, or coordinate transforms.
    """
    source_by_key = {entity.entity_key: entity for entity in entities}
    route_pattern = re.compile(registry.positive_route_layer_regex)
    cables = [feature for feature in features if feature.feature_class == "CABLE"]
    failures = []
    curve_facts_checked = 0
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
        if require_curve_facts:
            facts = source.curve_facts
            if not facts or not source.curve_fingerprint:
                failures.append(f"{cable.feature_key}: missing canonical source curve facts")
            else:
                curve_facts_checked += 1
                try:
                    current_fingerprint = canonical_curve_fingerprint(facts)
                except (TypeError, ValueError) as exc:
                    failures.append(
                        f"{cable.feature_key}: invalid mutable curve facts: {exc}"
                    )
                    current_fingerprint = ""
                if current_fingerprint != source.curve_fingerprint:
                    failures.append(
                        f"{cable.feature_key}: curve facts changed after ingestion"
                    )
                curve_xy = tuple(
                    (float(point[0]), float(point[1]))
                    for point in facts.get("vertices_wcs", ())
                )
                if curve_xy != tuple(source.points):
                    failures.append(
                        f"{cable.feature_key}: 2D source points differ from ordered WCS curve facts"
                    )
                if len(facts.get("bulges", ())) != len(curve_xy):
                    failures.append(
                        f"{cable.feature_key}: curve bulge/vertex cardinality mismatch"
                    )
                if bool(facts.get("closed")) != bool(source.closed):
                    failures.append(f"{cable.feature_key}: curve closed state mismatch")
                # Capability/primitive decisions belong to curve_geometry.
                # This validator only proves immutable source-fact lineage;
                # notably, a non-zero bulge is not evidence of corruption.
                facts_length = facts.get("native_length")
                if facts_length is None:
                    failures.append(
                        f"{cable.feature_key}: curve facts lack AutoCAD native length"
                    )
                if source.native_length is None:
                    failures.append(
                        f"{cable.feature_key}: source entity lacks AutoCAD native length"
                    )
                if not str(facts.get("native_length_source", "")).strip():
                    failures.append(
                        f"{cable.feature_key}: curve facts lack native-length provenance"
                    )
                if (
                    facts_length is not None
                    and source.native_length is not None
                    and abs(float(facts_length) - source.native_length) > 1e-9
                ):
                    failures.append(f"{cable.feature_key}: curve native length mismatch")
        if not cable.lineage or any(
            item.get("operation") != "identity" or float(item.get("max_displacement_m", 0.0)) != 0.0
            for item in cable.lineage
        ):
            failures.append(f"{cable.feature_key}: non-identity cable lineage")
    if failures:
        raise RuntimeError(
            "Source geometry validation failed: " + "; ".join(failures)
        )
    return {
        "cable_sources_checked": len(cables),
        "curve_facts_checked": curve_facts_checked,
        "source_geometry_immutable": True,
    }


def _validate_topology_policy(
    features, relations, topology_diagnostics,
):
    """Validate graph/connectivity decisions without rechecking CAD vertices."""
    feature_class_by_key = {
        feature.feature_key: feature.feature_class for feature in features
    }
    failures = []
    if topology_diagnostics.get("synthetic_route_vertices") != 0:
        failures.append("synthetic route vertices are present")
    component_diagnostics = topology_diagnostics.get(
        "source_route_component_diagnostics"
    )
    if (
        component_diagnostics is not None
        and component_diagnostics.get("status") != "consistent"
    ):
        failures.append(
            "route-group/source-segment component definitions disagree: "
            f"{component_diagnostics}"
        )
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
        raise RuntimeError(
            "Topology policy validation failed: " + "; ".join(failures)
        )
    return {
        "dimension_or_sling_promotions": 0,
        "synthetic_route_vertices": 0,
        "support_optical_promotions": 0,
        "crossing_connections": 0,
    }


def _enforce_geometry_policy(
    entities, features, relations, registry, topology_diagnostics, *,
    require_curve_facts=False,
):
    """Compatibility facade over the separately auditable validation domains."""
    _validate_reviewed_policy(registry)
    source_geometry = _validate_source_geometry(
        entities, features, registry, require_curve_facts=require_curve_facts,
    )
    topology = _validate_topology_policy(
        features, relations, topology_diagnostics,
    )
    return {
        **source_geometry,
        **topology,
        "validation_domains": {
            "source_geometry": {"passed": True},
            "topology": {"passed": True},
        },
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


def _calibration_spatial_coverage(
    profile, transformer, drawing_native_points, spatial_coverage_policy, *,
    training_point_ids=None,
):
    """Evaluate the source-bound numeric GCP distribution policy."""
    drawing = [transformer.point(point) for point in drawing_native_points]
    active = list(profile.active_controls)
    accepted_training_ids = (
        None if training_point_ids is None else frozenset(training_point_ids)
    )
    train = [
        transformer.point(control.cad_point)
        for control in active
        if control.role == "train"
        and (
            accepted_training_ids is None
            or control.point_id in accepted_training_ids
        )
    ]
    check = [transformer.point(control.cad_point) for control in active if control.role == "check"]
    result = evaluate_spatial_coverage(
        drawing, train, check, spatial_coverage_policy,
    )
    result.update({
        "reviewed": profile.validation.spatial_distribution_reviewed,
        "review_source": getattr(
            profile.validation, "spatial_distribution_review_source", "",
        ),
        "training_scope": (
            "active_reviewed_controls"
            if accepted_training_ids is None
            else "accepted_robust_inliers"
        ),
    })
    return result


def _calibration_candidate_coverage_failures(
    profile,
    transformer,
    drawing_native_points,
    spatial_coverage_policy,
    candidate,
):
    """Return post-inlier coverage failures for one fitted model candidate."""
    accepted_training_ids = {
        residual.point_id
        for residual in candidate.residuals
        if residual.role == "train" and residual.inlier is True
    }
    coverage = _calibration_spatial_coverage(
        profile,
        transformer,
        drawing_native_points,
        spatial_coverage_policy,
        training_point_ids=accepted_training_ids,
    )
    if coverage["passed"] is True:
        return ()
    return tuple(
        f"accepted-inlier spatial coverage: {failure}"
        for failure in coverage["failures"]
    )


def _nominal_lineage_audit(transformer, source_sha256, feature_displacements):
    """Build an evidence-compatible audit for the no-GCP delivery path.

    ``write_evidence`` predates always-on lineage and accepts the lineage
    payload through its calibration-audit argument.  This audit intentionally
    contains no controls or GCP observations: it records only the direct
    nominal CRS operation and the resulting native/nominal/delivery arrays.
    """
    empty_metrics = {"count": 0, "rmse_m": None, "p95_m": None, "max_m": None}
    return {
        "profile_path": "",
        "profile_sha256": "",
        "source_sha256": source_sha256,
        "status": "not_provided",
        "spatial_coverage": {},
        "result": {
            "requested_model": "nominal_direct",
            "selected_model": "nominal_direct",
            "source_crs": transformer.source_crs,
            "target_crs": transformer.target_crs,
            "parameters": {},
            "train_metrics": empty_metrics,
            "check_metrics": empty_metrics,
            "validation": {
                "passed": None,
                "failures": [],
            },
        },
        "observations": [],
        "feature_displacements": feature_displacements,
    }


def _segment_delivery_summary(features, delivery_counts=None):
    """Summarise the normalised CABLE_SEGMENT contract for the run manifest."""
    from .curve_geometry import delivery_segments

    metric_count = 0
    measured_count = 0
    unmeasured_count = 0
    index_passed = True
    geometry_passed = True
    total_passed = True
    schema_passed = True
    unit_passed = True
    for feature in features:
        if feature.feature_class != "CABLE":
            continue
        attributes = getattr(feature, "attributes", {}) or {}
        metrics = attributes.get("span_metrics", [])
        expected = len(delivery_segments(feature, require_materialized=False))
        if not isinstance(metrics, list) or len(metrics) != expected:
            index_passed = False
            continue
        route_grid = 0.0
        route_geodesic = 0.0
        route_measured = 0
        for segment_index, metric in enumerate(metrics):
            metric_count += 1
            if not isinstance(metric, dict) or metric.get("segment_index") != segment_index:
                index_passed = False
                continue
            status = str(metric.get("status", ""))
            if status not in {"measured", "unmeasured_no_dimension"}:
                index_passed = False
            if status == "measured":
                measured_count += 1
                route_measured += 1
                if (
                    metric.get("dimension_entity_key") in (None, "")
                    or metric.get("measurement_native_m") is None
                    or metric.get("measurement_delta_m") is None
                ):
                    index_passed = False
            else:
                unmeasured_count += 1
                if (
                    metric.get("dimension_entity_key") is not None
                    or metric.get("measurement_native_m") is not None
                    or metric.get("measurement_delta_m") is not None
                ):
                    index_passed = False
            if (
                metric.get("source_native_length_m") is None
                or metric.get("delivery_grid_length_m") is None
                or metric.get("geodesic_length_m") is None
            ):
                geometry_passed = False
                continue
            try:
                if any(
                    not math.isfinite(float(metric[name]))
                    for name in (
                        "source_native_length_m",
                        "delivery_grid_length_m",
                        "geodesic_length_m",
                    )
                ):
                    geometry_passed = False
                route_grid += float(metric["delivery_grid_length_m"])
                route_geodesic += float(metric["geodesic_length_m"])
                if status == "measured" and any(
                    not math.isfinite(float(metric[name]))
                    for name in ("measurement_native_m", "measurement_delta_m")
                ):
                    index_passed = False
                if status == "measured" and (
                    abs(
                        float(metric["measurement_delta_m"])
                        - (
                            float(metric["measurement_native_m"])
                            - float(metric["source_native_length_m"])
                        )
                    )
                    > 1e-9
                ):
                    index_passed = False
            except (TypeError, ValueError):
                geometry_passed = False
            # Enriched parent metrics predate the delivery schema and do not
            # carry these fields; the writer adds them to every segment.  If a
            # caller supplies either field, however, validate it strictly.
            if (
                "schema_version" in metric
                and metric.get("schema_version") != CABLE_SEGMENT_SCHEMA_VERSION
            ):
                schema_passed = False
            if "unit" in metric and metric.get("unit") != CABLE_SEGMENT_UNIT:
                unit_passed = False
        parent_grid = attributes.get("delivery_grid_length_m")
        parent_geodesic = attributes.get("geodesic_length_m")
        try:
            if parent_grid is None or abs(route_grid - float(parent_grid)) > 1e-6:
                total_passed = False
            if parent_geodesic is None or abs(route_geodesic - float(parent_geodesic)) > 1e-6:
                total_passed = False
        except (TypeError, ValueError):
            total_passed = False
        if attributes.get("measured_span_count") != route_measured:
            index_passed = False
        if attributes.get("unmeasured_span_count") != expected - route_measured:
            index_passed = False
    actual_count = metric_count
    if delivery_counts is not None and "CABLE_SEGMENT" in delivery_counts:
        actual_count = int(delivery_counts["CABLE_SEGMENT"])
        if actual_count != metric_count:
            index_passed = False
    closure_passed = bool(index_passed and geometry_passed and total_passed)
    return {
        "count": actual_count,
        "measured": measured_count,
        "unmeasured": unmeasured_count,
        "schema_version": CABLE_SEGMENT_SCHEMA_VERSION,
        "unit": CABLE_SEGMENT_UNIT,
        "schema_passed": bool(schema_passed),
        "unit_passed": bool(unit_passed),
        "index_passed": bool(index_passed),
        "geometry_length_closure_passed": bool(geometry_passed),
        "total_length_closure_passed": bool(total_passed),
        "closure_passed": closure_passed,
        "passed": bool(schema_passed and unit_passed and closure_passed),
    }


def _manifest_validation_summary(
    entities, features, policy_diagnostics, topology_diagnostics,
    georeference_diagnostics, delivery_counts=None,
):
    """Expose the production gates without requiring a GeoPackage query.

    The evidence artifact remains authoritative and is content-hashed by the
    manifest.  This bounded summary makes the most important curve, topology,
    length, span, and georeference outcomes visible to release tooling.
    """
    cable_source_keys = {
        feature.source_entity_key
        for feature in features
        if feature.feature_class == "CABLE"
    }
    cable_curve_fingerprints = sorted(
        entity.curve_fingerprint
        for entity in entities
        if entity.entity_key in cable_source_keys and entity.curve_fingerprint
    )
    calibration = georeference_diagnostics["calibration"]
    coverage = calibration.get("spatial_coverage") or {}
    lineage = georeference_diagnostics["lineage"]
    return {
        "schema_version": "cad2gis-validation-summary-v2",
        "source_geometry": {
            "passed": policy_diagnostics["validation_domains"][
                "source_geometry"
            ]["passed"],
            "cable_sources_checked": policy_diagnostics["cable_sources_checked"],
            "curve_facts_checked": policy_diagnostics["curve_facts_checked"],
            "cable_curve_fingerprint_count": len(cable_curve_fingerprints),
            "cable_curve_fingerprint_set_sha256": _canonical_json_sha256(
                cable_curve_fingerprints
            ),
            "source_geometry_immutable": policy_diagnostics[
                "source_geometry_immutable"
            ],
            "source_route_native_lengths": topology_diagnostics.get(
                "source_route_native_lengths", 0
            ),
            "source_route_native_length_max_abs_delta_m": topology_diagnostics.get(
                "source_route_native_length_max_abs_delta_m", 0.0
            ),
        },
        "topology": {
            "passed": policy_diagnostics["validation_domains"]["topology"][
                "passed"
            ],
            "source_route_components": topology_diagnostics.get(
                "source_route_components", 0
            ),
            "source_routes": topology_diagnostics.get("source_routes", 0),
            "source_route_nodes": topology_diagnostics.get(
                "source_route_graph", {}
            ).get("unique_nodes", 0),
            "source_route_edges": topology_diagnostics.get(
                "source_route_graph", {}
            ).get("unique_edges", 0),
            "source_route_component_diagnostics": topology_diagnostics.get(
                "source_route_component_diagnostics", {"status": "not_applicable"}
            ),
            "route_segment_intersection_counts": topology_diagnostics.get(
                "route_segment_intersection_counts", {}
            ),
            "synthetic_route_vertices": policy_diagnostics[
                "synthetic_route_vertices"
            ],
            "support_optical_promotions": policy_diagnostics[
                "support_optical_promotions"
            ],
            "crossing_connections": policy_diagnostics["crossing_connections"],
        },
        "measurements": {
            "passed": True,
            "route_segments_with_span_dimension": topology_diagnostics.get(
                "route_segments_with_span_dimension", 0
            ),
            "route_segments_without_span_dimension": topology_diagnostics.get(
                "route_segments_without_span_dimension", 0
            ),
            "span_measurement_max_abs_error_m": topology_diagnostics.get(
                "span_measurement_max_abs_error_m", 0.0
            ),
        },
        "segment_delivery": _segment_delivery_summary(features, delivery_counts),
        "coordinate_accuracy": {
            "calibration_status": calibration["status"],
            "spatial_coverage_passed": coverage.get("passed"),
            "lineage_model": lineage["model"],
            "lineage_feature_count": lineage["feature_count"],
            "absolute_accuracy_validation": georeference_diagnostics[
                "coordinate_operation"
            ]["absolute_accuracy_validation"],
        },
    }


_MISSING = object()


def _diagnostic_value(value, path):
    current = value
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _evaluate_diagnostic_gates(domain, gates, diagnostics):
    """Evaluate only reviewer-declared gates; no project names are implicit."""
    results = []
    failures = []
    for gate in gates:
        actual = _diagnostic_value(diagnostics, gate.path)
        if actual is _MISSING:
            passed = False
            rendered_actual = "<missing>"
        else:
            rendered_actual = actual
            try:
                if gate.operator == "eq":
                    passed = actual == gate.value
                elif gate.operator == "le":
                    passed = (
                        not isinstance(actual, bool)
                        and isinstance(actual, (int, float))
                        and math.isfinite(float(actual))
                        and float(actual) <= float(gate.value)
                    )
                elif gate.operator == "ge":
                    passed = (
                        not isinstance(actual, bool)
                        and isinstance(actual, (int, float))
                        and math.isfinite(float(actual))
                        and float(actual) >= float(gate.value)
                    )
                else:  # The config loader rejects this; keep this boundary fail-closed.
                    passed = False
            except (TypeError, ValueError, OverflowError):
                passed = False
        result = {
            "path": gate.dotted_path,
            "operator": gate.operator,
            "expected": gate.value,
            "actual": rendered_actual,
            "passed": bool(passed),
        }
        results.append(result)
        if not passed:
            failures.append(result)
    if failures:
        details = "; ".join(
            f"{item['path']} {item['operator']} {item['expected']!r}, "
            f"actual={item['actual']!r}"
            for item in failures
        )
        raise RuntimeError(f"{domain} contract gates failed: {details}")
    return {"domain": domain, "passed": True, "gates": results}


def _validate_exact_counts(domain, expected, actual):
    invalid = {
        str(key): value
        for key, value in actual.items()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0
    }
    if invalid:
        raise RuntimeError(f"{domain} emitted invalid counts: {invalid}")
    normalized_actual = {
        str(key): value for key, value in actual.items() if value != 0
    }
    normalized_expected = {
        str(key): int(value) for key, value in expected.items() if int(value) != 0
    }
    if normalized_actual != normalized_expected:
        raise RuntimeError(
            f"{domain} census mismatch: expected {normalized_expected}, "
            f"got {normalized_actual}"
        )
    return {
        "domain": domain,
        "passed": True,
        "expected": normalized_expected,
        "actual": normalized_actual,
    }


def _validate_declared_counts(domain, expected, actual):
    failures = []
    results = {}
    for key, expected_count in sorted(expected.items()):
        raw_actual = actual.get(key, 0)
        if (
            isinstance(raw_actual, bool)
            or not isinstance(raw_actual, int)
            or raw_actual < 0
        ):
            raise RuntimeError(
                f"{domain} emitted invalid count for {key}: {raw_actual!r}"
            )
        actual_count = raw_actual
        passed = actual_count == int(expected_count)
        results[key] = {
            "expected": int(expected_count),
            "actual": actual_count,
            "passed": passed,
        }
        if not passed:
            failures.append(key)
    if failures:
        raise RuntimeError(
            f"{domain} census mismatch for {failures}: "
            f"{ {key: results[key] for key in failures} }"
        )
    return {"domain": domain, "passed": True, "counts": results}


def _validate_annotation_families(expectations, semantic_diagnostics, registry):
    expected = {item.family_id: item.metrics for item in expectations}
    actual = semantic_diagnostics.get("annotation_assignments_by_family", {})
    if set(actual) != set(expected):
        raise RuntimeError(
            "Annotation family census mismatch: "
            f"expected={sorted(expected)}, actual={sorted(actual)}"
        )
    family_config = {family.family_id: family for family in registry.annotation_families}
    results = {}
    for family_id, metrics in sorted(expected.items()):
        observed = actual[family_id]
        metric_result = _validate_declared_counts(
            f"annotation family {family_id}", metrics, observed,
        )
        if (
            family_config.get(family_id) is not None
            and family_config[family_id].require_same_layer
            and int(observed.get("cross_layer_assignments", 0)) != 0
        ):
            raise RuntimeError(
                f"Annotation family {family_id} violates reviewed same-layer isolation"
            )
        results[family_id] = metric_result
    return {"domain": "annotation_families", "passed": True, "families": results}


def _require_coverage_allowed(domain, diagnostics):
    coverage = diagnostics.get("coverage")
    if not isinstance(coverage, Mapping):
        raise RuntimeError(f"{domain} did not emit the required coverage contract")
    if coverage.get("conversion_allowed") is not True:
        raise RuntimeError(f"{domain} coverage gate failed: {dict(coverage)}")
    return dict(coverage)


def _validate_project_bindings(profile, registry):
    profile.require_reviewed()
    registry.require_reviewed()
    if profile.project_id or registry.project_id:
        if not profile.project_id or profile.project_id != registry.project_id:
            raise ValueError(
                "Project profile and mapping registry project_id values do not match"
            )
    if profile.inventory_sha256 or registry.inventory_sha256:
        if (
            not profile.inventory_sha256
            or profile.inventory_sha256 != registry.inventory_sha256
        ):
            raise ValueError(
                "Project profile and mapping registry inventory bindings do not match"
            )


def convert(request: ConversionRequest) -> ConversionResult:
    source = Path(request.source).resolve()
    run_dir = Path(request.run_dir).resolve()
    from .implementation import (
        conversion_snapshot_manifest_fields,
        freeze_conversion_snapshot,
        verify_conversion_snapshot,
    )
    from .runtime_provenance import collect_runtime_provenance, runtime_manifest_fields
    startup_runtime = collect_runtime_provenance()
    try:
        conversion_snapshot = freeze_conversion_snapshot(
            source,
            request.source_profile,
            request.mapping_registry,
            request.gcp_profile,
            runtime=startup_runtime,
        )
    except FileNotFoundError:
        # Preserve the stronger, actionable review error for a draft pack even
        # when another required artifact has not yet been authored.  The
        # snapshot attempt still occurs first, and no reader/runtime stage is
        # entered.  A reviewed profile re-raises the missing-artifact failure.
        incomplete_profile = SourceProfile.load(request.source_profile)
        incomplete_profile.require_reviewed()
        raise
    profile = SourceProfile.load(request.source_profile)
    # This review gate is before unit/CRS work, AutoCAD startup, or ingestion.
    # Validation never upgrades a draft.
    profile.require_reviewed()
    source_hash = profile.validate_source(source)
    registry = MappingRegistry.load(request.mapping_registry, source_hash)
    _validate_project_bindings(profile, registry)

    from .units import build_unit_crs_contract

    unit_contract = build_unit_crs_contract(
        dwg_insunits=profile.dwg_insunits,
        source_crs=profile.source_crs,
        target_crs=profile.target_crs,
        source_coordinate_scale_to_m=profile.source_coordinate_scale_to_m,
        source_coordinate_scale_reviewed=profile.source_coordinate_scale_reviewed,
        local_registration_strategy=profile.local_registration_strategy,
        local_registration_reviewed=profile.local_registration_reviewed,
    )
    # DirectTransformer is the only implemented geometry transform at this
    # boundary.  Registration contracts must never masquerade as direct CRS.
    if unit_contract.coordinate_mode != "direct_crs":
        raise RuntimeError(
            "Conversion requires an implemented reviewed authoritative registration; "
            f"unit/CRS contract mode is {unit_contract.coordinate_mode!r}"
        )
    run_dir.parent.mkdir(parents=True, exist_ok=True)

    entities, ingest_diagnostics = ingest(source, profile)
    if profile.inventory_sha256:
        from .project_profile import build_source_inventory

        observed_inventory = build_source_inventory(
            source,
            entities,
            reader_protocol=ingest_diagnostics.get("reader_protocol"),
        )
        if observed_inventory["inventory_sha256"] != profile.inventory_sha256:
            raise RuntimeError(
                "Authoritative source inventory differs from the reviewed project pack: "
                f"expected {profile.inventory_sha256}, got "
                f"{observed_inventory['inventory_sha256']}"
            )
    inventory_gate = _validate_declared_counts(
        "source inventory",
        profile.expectations.source_inventory,
        ingest_diagnostics["census"],
    )
    features, relations, unresolved, semantic_diagnostics = classify_entities(
        entities,
        registry,
        coverage_policy=registry.semantic_coverage_policy,
        coverage_allowlist=list(registry.semantic_coverage_allowlist),
    )
    semantic_coverage = _require_coverage_allowed(
        "semantic classification", semantic_diagnostics,
    )
    feature_gate = _validate_exact_counts(
        "semantic feature",
        profile.expectations.feature_counts,
        Counter(feature.feature_class for feature in features),
    )
    annotation_gate = _validate_annotation_families(
        profile.expectations.annotation_families,
        semantic_diagnostics,
        registry,
    )

    from .curve_geometry import (
        materialize_cable_features,
        validate_cable_geometry_materialization,
    )

    curve_materialization = materialize_cable_features(
        entities, features, policy=None, strict=True,
    )
    source_policy = _validate_source_geometry(
        entities,
        features,
        registry,
        require_curve_facts=True,
    )
    source_geometry_gate = _evaluate_diagnostic_gates(
        "source geometry",
        profile.expectations.source_geometry_gates,
        {**source_policy, "curve_materialization": curve_materialization},
    )
    relations, unresolved, topology_diagnostics = build_topology(
        entities, features, registry, relations, unresolved,
    )
    curve_validation = validate_cable_geometry_materialization(
        entities, features, policy=None, require_all=True,
    )
    topology_policy = _validate_topology_policy(
        features, relations, topology_diagnostics,
    )
    policy_diagnostics = {
        **source_policy,
        **topology_policy,
        "curve_materialization": curve_materialization,
        "curve_validation": curve_validation,
        "validation_domains": {
            "source_geometry": {"passed": True},
            "topology": {"passed": True},
        },
    }
    topology_gate = _evaluate_diagnostic_gates(
        "topology", profile.expectations.topology_gates, topology_diagnostics,
    )
    segment_gate = _evaluate_diagnostic_gates(
        "segments", profile.expectations.segment_gates, topology_diagnostics,
    )
    transformer = DirectTransformer(
        profile.source_crs, profile.target_crs, unit_contract=unit_contract,
    )
    all_points = [point for feature in features for point in feature.native_points]
    roundtrip_error = transformer.roundtrip_error(all_points)
    if roundtrip_error > 1e-6:
        raise RuntimeError(f"CRS round-trip error exceeds 1e-6 source metres: {roundtrip_error}")
    engine_crosscheck = transformer.engine_crosscheck_error(all_points)
    if engine_crosscheck > 1e-6:
        raise RuntimeError(f"OSR/PROJ target-coordinate disagreement exceeds 1e-6 m: {engine_crosscheck}")
    selected_transformer = transformer
    calibration_audit = None
    lineage_audit = None
    calibration_diagnostics = {"status": "not_provided"}
    gcp_profile = None
    if request.gcp_profile is not None:
        gcp_profile = GCPProfile.load(
            request.gcp_profile, expected_source_sha256=source_hash,
        )
        if gcp_profile.enabled and profile.spatial_coverage_policy is None:
            raise RuntimeError(
                "An enabled GCP profile requires reviewed spatial_coverage_policy gates"
            )
        gcp_profile.validate_transformer(transformer)
        drawing_extent_points = source_entity_drawing_points(entities)
        prefit_spatial_coverage = (
            {
                "schema_version": "cad2gis-spatial-coverage-v1",
                "status": "not_required_disabled_profile",
                "passed": None,
                "failures": [],
            }
            if profile.spatial_coverage_policy is None
            else _calibration_spatial_coverage(
                gcp_profile,
                transformer,
                drawing_extent_points,
                profile.spatial_coverage_policy,
            )
        )
        if gcp_profile.enabled and prefit_spatial_coverage["passed"] is not True:
            raise RuntimeError(
                "GCP pre-fit spatial coverage failed source-profile gates: "
                + "; ".join(prefit_spatial_coverage["failures"])
            )
        calibration_result = fit_profile(
            gcp_profile,
            transformer,
            candidate_validation=lambda candidate: (
                _calibration_candidate_coverage_failures(
                    gcp_profile,
                    transformer,
                    drawing_extent_points,
                    profile.spatial_coverage_policy,
                    candidate,
                )
            ),
        )
        result_payload = calibration_result.to_dict()
        if gcp_profile.enabled:
            accepted_training_ids = {
                residual.point_id
                for residual in calibration_result.residuals
                if residual.role == "train" and residual.inlier
            }
            spatial_coverage = _calibration_spatial_coverage(
                gcp_profile,
                transformer,
                drawing_extent_points,
                profile.spatial_coverage_policy,
                training_point_ids=accepted_training_ids,
            )
            spatial_coverage["pre_fit"] = prefit_spatial_coverage
            if spatial_coverage["passed"] is not True:
                raise RuntimeError(
                    "GCP accepted-inlier spatial coverage failed source-profile "
                    "gates: " + "; ".join(spatial_coverage["failures"])
                )
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
            spatial_coverage = prefit_spatial_coverage
            # A disabled profile is an explicit review decision, not an
            # absence of lineage.  Keep delivery on the nominal CRS path and
            # record the identity residual step for every geometric feature.
            feature_displacements = feature_adjustment_records(
                features, transformer, selected_transformer,
                lineage_model="identity_residual",
            )
        calibration_diagnostics = {
            "status": status,
            "profile_path": str(gcp_profile.path),
            "profile_sha256": gcp_profile.sha256,
            "source_sha256": gcp_profile.source_sha256,
            "spatial_coverage": spatial_coverage,
            "result": result_payload,
        }
        calibration_audit = {
            **calibration_diagnostics,
            "observations": _calibration_observations(
                gcp_profile, calibration_result, transformer,
            ),
            "feature_displacements": feature_displacements,
        }
    else:
        # Lineage is always emitted, even when no GCP profile was supplied.
        # ``calibration_audit`` remains reserved for real profile evidence;
        # this independent audit lets the existing evidence writer persist
        # the lineage table without manufacturing a profile or controls.
        feature_displacements = feature_adjustment_records(
            features, transformer, selected_transformer,
            lineage_model="nominal_direct",
        )
        lineage_audit = _nominal_lineage_audit(
            transformer, source_hash, feature_displacements,
        )

    enrich_delivery_metrics(features, selected_transformer)
    from .styles import analyze_style_coverage

    style_coverage = analyze_style_coverage(
        features,
        policy=registry.style_coverage_policy,
        allowlist=list(registry.style_coverage_allowlist),
    )
    if style_coverage.get("conversion_allowed") is not True:
        raise RuntimeError(f"Style coverage gate failed: {style_coverage}")
    operation_metadata = transformer.operation_metadata(all_points[0] if all_points else None)
    if calibration_diagnostics["status"] == "accepted":
        operation_metadata["absolute_accuracy_validation"] = (
            "nominal CRS operation only; accepted GCP validation is recorded in calibration"
        )
    diagnostics = {
        "ingest": ingest_diagnostics,
        "semantics": semantic_diagnostics,
        "topology": topology_diagnostics,
        "styles": {"coverage": style_coverage},
        "policy_enforcement": policy_diagnostics,
        "project_contract": {
            "project_id": profile.project_id,
            "inventory_sha256": profile.inventory_sha256,
            "review": {
                "source_profile": profile.review.status,
                "mapping_registry": registry.review.status,
            },
            "gates": {
                "source_inventory": inventory_gate,
                "features": feature_gate,
                "annotation_families": annotation_gate,
                "source_geometry": source_geometry_gate,
                "topology": topology_gate,
                "segments": segment_gate,
            },
            "coverage": {
                "semantics": semantic_coverage,
                "styles": style_coverage,
            },
        },
        "georeference": {
            "source_crs": profile.source_crs,
            "target_crs": profile.target_crs,
            "operation": "direct source-to-target; no EPSG:4326 intermediate geometry",
            "unit_crs_contract": unit_contract.to_manifest_dict(),
            "spatial_coverage_policy": (
                None if profile.spatial_coverage_policy is None
                else profile.spatial_coverage_policy.to_dict()
            ),
            "roundtrip_max_source_m": roundtrip_error,
            "engine_crosscheck_max_target_m": engine_crosscheck,
            "coordinate_operation": operation_metadata,
            "calibration": calibration_diagnostics,
            "lineage": {
                "model": (
                    "nominal_direct" if lineage_audit is not None
                    else "identity_residual" if calibration_diagnostics["status"] == "disabled"
                    else calibration_diagnostics["result"].get("selected_model", "identity_residual")
                ),
                "feature_count": len(feature_displacements),
            },
        },
    }

    artifact_prefix = "apd_" if profile.is_legacy else ""
    evidence_path = run_dir / f"{artifact_prefix}evidence.gpkg"
    delivery_path = run_dir / f"{artifact_prefix}delivery.gpkg"
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
            calibration_audit=(calibration_audit or lineage_audit),
            target_srs=transformer.target,
            delivery_transformer=selected_transformer,
        )
        counts = write_delivery(staged_delivery_path, features, selected_transformer)
        delivery_gate = _validate_declared_counts(
            "delivery", profile.expectations.delivery_counts, counts,
        )
        staged_style_manifest_path = write_styles(
            staged_styles_dir, features, staged_delivery_path,
            coverage_policy=registry.style_coverage_policy,
            coverage_allowlist=list(registry.style_coverage_allowlist),
        )
        written_style_manifest = json.loads(
            staged_style_manifest_path.read_text(encoding="utf-8")
        )
        written_style_coverage = written_style_manifest.get("coverage")
        if (
            not isinstance(written_style_coverage, dict)
            or written_style_coverage.get("conversion_allowed") is not True
        ):
            raise RuntimeError(
                f"Written style manifest coverage gate failed: {written_style_coverage}"
            )
        validation_summary = _manifest_validation_summary(
            entities,
            features,
            policy_diagnostics,
            topology_diagnostics,
            diagnostics["georeference"],
            counts,
        )
        if validation_summary["segment_delivery"]["passed"] is not True:
            raise RuntimeError(
                "CABLE_SEGMENT manifest closure validation failed: "
                f"{validation_summary['segment_delivery']}"
            )
        implementation = production_conversion_provenance()
        reader_runtime_inventory = {
            **dict(ingest_diagnostics.get("reader_inventory") or {}),
            **dict(ingest_diagnostics.get("reader_protocol") or {}),
        }
        runtime_provenance = collect_runtime_provenance(
            reader_inventory=reader_runtime_inventory
        )
        manifest = {
            "schema_version": "cad2gis-run-manifest-v4",
            "pipeline": "cad2gis-reviewed-project-evidence-first-v4",
            **implementation_manifest_fields(implementation),
            **conversion_snapshot_manifest_fields(conversion_snapshot),
            **runtime_manifest_fields(runtime_provenance),
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
            "delivery_contract_gate": delivery_gate,
            "semantics": semantic_coverage,
            "style": written_style_coverage,
            "source_route_components": topology_diagnostics.get(
                "source_route_components", 0
            ),
            "unresolved_count": len(unresolved),
            "policy": dict(registry.policy),
            "validation": validation_summary,
        }
        if gcp_profile is not None:
            manifest["profiles"]["gcp_profile"] = {
                "path": str(gcp_profile.path), "sha256": gcp_profile.sha256,
            }
        _write_manifest(staged_run_dir / manifest_path.name, manifest)
        verify_conversion_snapshot(conversion_snapshot)
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
