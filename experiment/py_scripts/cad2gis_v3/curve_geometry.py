"""Deterministic, loss-aware materialisation of CAD cable curves.

The reader's ordered WCS vertices remain the immutable source geometry.  This
module derives a separate delivery path and preserves a one-to-one mapping
between every CAD source segment and its (possibly multi-vertex) delivery
geometry.  Native curve length is never inferred from the delivery chords.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .model import Feature, SourceEntity, canonical_curve_fingerprint


MATERIALIZATION_SCHEMA_VERSION = "cad2gis.cable_curve_materialization.v1"
MATERIALIZATION_POLICY_VERSION = "cad2gis.bulge_tessellation.v1"

DEFAULT_MATERIALIZATION_POLICY: dict[str, Any] = {
    "policy_version": MATERIALIZATION_POLICY_VERSION,
    # Values are deliberately expressed in the reviewed CAD drawing unit.
    # Unit conversion belongs to the units/CRS contract, not curve parsing.
    "max_sagitta_native": 0.01,
    "max_chord_native": 1.0,
    "point_tolerance_native": 1.0e-9,
    "native_length_abs_tolerance": 1.0e-6,
    "native_length_rel_tolerance": 1.0e-9,
    "max_vertices_per_source_segment": 100_000,
}

_POLYLINE_PRIMITIVES = {"LINE", "LWPOLYLINE", "POLYLINE", "2DPOLYLINE", "3DPOLYLINE"}
_READER_MATERIALIZED_PRIMITIVES = {
    "SPLINE", "ELLIPSE", "ARC", "CIRCLE", "3DPOLYLINE",
    "FITTED_POLYLINE", "MESH", "POLYFACE",
}


class CableGeometryMaterializationError(RuntimeError):
    """Fail-closed error carrying machine-readable curve issues."""

    def __init__(self, message: str, *, diagnostics: Mapping[str, Any], issues: Sequence[Mapping[str, Any]]):
        super().__init__(message)
        self.diagnostics = dict(diagnostics)
        self.issues = tuple(dict(issue) for issue in issues)


def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    if positive and number <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    if nonnegative and number < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return number


def _policy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(DEFAULT_MATERIALIZATION_POLICY)
    if value is not None:
        if not isinstance(value, Mapping):
            raise ValueError("curve materialization policy must be an object")
        unknown = sorted(set(value) - set(result))
        if unknown:
            raise ValueError(f"unknown curve materialization policy fields: {unknown}")
        result.update(value)
    if str(result["policy_version"]) != MATERIALIZATION_POLICY_VERSION:
        raise ValueError(
            f"unsupported curve materialization policy: {result['policy_version']!r}; "
            f"expected {MATERIALIZATION_POLICY_VERSION!r}"
        )
    for key in ("max_sagitta_native", "max_chord_native"):
        result[key] = _finite(result[key], key, positive=True)
    for key in (
        "point_tolerance_native", "native_length_abs_tolerance",
        "native_length_rel_tolerance",
    ):
        result[key] = _finite(result[key], key, nonnegative=True)
    maximum = result["max_vertices_per_source_segment"]
    if isinstance(maximum, bool):
        raise ValueError("max_vertices_per_source_segment must be an integer >= 2")
    try:
        maximum = int(maximum)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_vertices_per_source_segment must be an integer >= 2") from exc
    if maximum < 2 or float(maximum) != float(result["max_vertices_per_source_segment"]):
        raise ValueError("max_vertices_per_source_segment must be an integer >= 2")
    result["max_vertices_per_source_segment"] = maximum
    return result


def _issue(code: str, feature: Feature, source: SourceEntity | None, detail: str, **facts: Any) -> dict[str, Any]:
    return {
        "code": code,
        "feature_key": feature.feature_key,
        "source_entity_key": feature.source_entity_key,
        "source_handle": "" if source is None else source.handle,
        "detail": detail,
        "facts": facts,
    }


def _points_close(left: Sequence[float], right: Sequence[float], tolerance: float) -> bool:
    return len(left) >= 2 and len(right) >= 2 and math.dist(left[:2], right[:2]) <= tolerance


def _ordered_xy(points: Any, name: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(points, (list, tuple)):
        raise ValueError(f"{name} must be an ordered array")
    result: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            raise ValueError(f"{name}[{index}] must contain at least x and y")
        result.append((
            _finite(point[0], f"{name}[{index}].x"),
            _finite(point[1], f"{name}[{index}].y"),
        ))
    return tuple(result)


def _polyline_length(points: Sequence[Sequence[float]]) -> float:
    return math.fsum(math.dist(start[:2], end[:2]) for start, end in zip(points, points[1:]))


def _length_close(left: float, right: float, policy: Mapping[str, Any]) -> bool:
    tolerance = max(
        float(policy["native_length_abs_tolerance"]),
        float(policy["native_length_rel_tolerance"]) * max(abs(left), abs(right)),
    )
    return abs(left - right) <= tolerance


def _bulge_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    bulge: float,
    policy: Mapping[str, Any],
) -> tuple[tuple[tuple[float, float], ...], float, str]:
    """Return delivery points, exact native length, and source kind."""
    chord = math.dist(start, end)
    point_tolerance = float(policy["point_tolerance_native"])
    if abs(bulge) <= point_tolerance:
        if chord <= point_tolerance:
            raise ValueError("zero-length line source segment")
        return (start, end), chord, "line"
    if chord <= point_tolerance:
        raise ValueError("nonzero bulge has a zero-length chord")

    theta = 4.0 * math.atan(bulge)
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    native_length = radius * abs(theta)
    unit_x = (end[0] - start[0]) / chord
    unit_y = (end[1] - start[1]) / chord
    centre_offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
    centre = (
        (start[0] + end[0]) * 0.5 - unit_y * centre_offset,
        (start[1] + end[1]) * 0.5 + unit_x * centre_offset,
    )
    start_angle = math.atan2(start[1] - centre[1], start[0] - centre[0])

    sagitta = float(policy["max_sagitta_native"])
    chord_limit = float(policy["max_chord_native"])
    if sagitta >= 2.0 * radius:
        sagitta_angle = math.pi * 2.0
    else:
        cosine = max(-1.0, min(1.0, 1.0 - sagitta / radius))
        sagitta_angle = 2.0 * math.acos(cosine)
    chord_angle = 2.0 * math.asin(min(1.0, chord_limit / (2.0 * radius)))
    step_angle = min(sagitta_angle, chord_angle)
    if not math.isfinite(step_angle) or step_angle <= 0.0:
        raise ValueError("curve tessellation tolerance cannot produce a finite step")
    interval_count = max(1, int(math.ceil(abs(theta) / step_angle)))
    vertex_count = interval_count + 1
    if vertex_count > int(policy["max_vertices_per_source_segment"]):
        raise ValueError(
            "curve tessellation exceeds max_vertices_per_source_segment: "
            f"{vertex_count} > {policy['max_vertices_per_source_segment']}"
        )
    points = [start]
    for index in range(1, interval_count):
        angle = start_angle + theta * (index / interval_count)
        points.append((
            centre[0] + radius * math.cos(angle),
            centre[1] + radius * math.sin(angle),
        ))
    points.append(end)
    return tuple(points), native_length, "bulge_arc"


def _reader_materialized_segments(
    feature: Feature,
    source: SourceEntity,
    parameters: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    raw_segments = parameters.get("delivery_segments_wcs")
    if not isinstance(raw_segments, (list, tuple)) or not raw_segments:
        raise ValueError(
            "complete reader facts require non-empty primitive_parameters."
            "delivery_segments_wcs"
        )
    result = []
    previous_end: tuple[float, float] | None = None
    tolerance = float(policy["point_tolerance_native"])
    for expected_index, raw in enumerate(raw_segments):
        if not isinstance(raw, Mapping):
            raise ValueError(f"delivery_segments_wcs[{expected_index}] must be an object")
        index = raw.get("source_segment_index")
        if isinstance(index, bool) or not isinstance(index, int) or index != expected_index:
            raise ValueError("reader-materialized source segment indices must be contiguous and ordered")
        points = _ordered_xy(raw.get("points_wcs"), f"delivery_segments_wcs[{expected_index}].points_wcs")
        if len(points) < 2:
            raise ValueError(f"delivery_segments_wcs[{expected_index}] requires at least two points")
        if previous_end is not None and not _points_close(previous_end, points[0], tolerance):
            raise ValueError("reader-materialized source segments are not endpoint-contiguous")
        native_length = _finite(
            raw.get("native_length"),
            f"delivery_segments_wcs[{expected_index}].native_length",
            positive=True,
        )
        native_length_source = str(raw.get("native_length_source", "")).strip()
        if not native_length_source:
            raise ValueError(
                f"delivery_segments_wcs[{expected_index}].native_length_source is required"
            )
        kind = str(raw.get("source_segment_kind", "")).strip()
        if not kind:
            raise ValueError(
                f"delivery_segments_wcs[{expected_index}].source_segment_kind is required"
            )
        start_index = raw.get("source_start_vertex_index")
        end_index = raw.get("source_end_vertex_index")
        for value, name in ((start_index, "source_start_vertex_index"), (end_index, "source_end_vertex_index")):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"delivery_segments_wcs[{expected_index}].{name} is invalid")
        result.append({
            "source_segment_index": expected_index,
            "source_segment_kind": kind,
            "source_start_vertex_index": start_index,
            "source_end_vertex_index": end_index,
            "source_native_length": native_length,
            "native_length_source": native_length_source,
            "delivery_native_points": [list(point) for point in points],
            "delivery_chord_length_native": _polyline_length(points),
        })
        previous_end = points[-1]
    return tuple(result)


def _materialize_route(
    feature: Feature,
    source: SourceEntity,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    facts = source.curve_facts
    if not facts or not source.curve_fingerprint:
        raise ValueError("canonical source curve facts and fingerprint are required")
    if canonical_curve_fingerprint(facts) != source.curve_fingerprint:
        raise ValueError("source curve facts changed after ingestion")
    vertices3 = facts.get("vertices_wcs", ())
    vertices = _ordered_xy(vertices3, "curve_facts.vertices_wcs")
    if len(vertices) < 2:
        raise ValueError("curve facts require at least two ordered WCS vertices")
    tolerance = float(policy["point_tolerance_native"])
    if len(vertices) != len(source.points) or any(
        not _points_close(left, right, tolerance)
        for left, right in zip(vertices, source.points)
    ):
        raise ValueError("ordered curve WCS vertices do not match the source entity")
    if len(vertices) != len(feature.native_points) or any(
        not _points_close(left, right, tolerance)
        for left, right in zip(vertices, feature.native_points)
    ):
        raise ValueError("feature source vertices do not match immutable curve facts")
    if bool(facts.get("closed")) or bool(source.closed):
        raise ValueError("closed cable routes are outside the open-route delivery contract")

    primitive_type = str(facts.get("primitive_type", "")).upper()
    parameters = facts.get("primitive_parameters", {})
    reader_segments = parameters.get("delivery_segments_wcs") if isinstance(parameters, Mapping) else None
    z_values = [_finite(point[2], f"curve_facts.vertices_wcs[{index}].z") for index, point in enumerate(vertices3)]
    normal = facts.get("normal") or facts.get("extrusion")
    if reader_segments is None:
        if z_values and max(z_values) - min(z_values) > tolerance:
            raise ValueError(
                "non-planar 3D cable curve requires complete reader materialization "
                "(delivery_segments_wcs)"
            )
        if normal is not None and (
            abs(float(normal[0])) > tolerance or abs(float(normal[1])) > tolerance
            or abs(abs(float(normal[2])) - 1.0) > tolerance
        ):
            raise ValueError(
                "non-planar curve normal requires complete reader materialization "
                "(delivery_segments_wcs)"
            )
    bulges = tuple(float(value) for value in facts.get("bulges", ()))
    if len(bulges) != len(vertices):
        raise ValueError("curve bulge/vertex cardinality mismatch")
    if reader_segments is not None:
        segments = _reader_materialized_segments(feature, source, parameters, policy)
        materialization_method = "reader-complete-curve-facts"
    else:
        if primitive_type not in _POLYLINE_PRIMITIVES:
            expected = sorted(_READER_MATERIALIZED_PRIMITIVES)
            raise ValueError(
                f"{primitive_type or 'UNKNOWN'} requires complete reader materialization "
                f"(delivery_segments_wcs); supported complete-facts primitives={expected}"
            )
        if primitive_type == "POLYLINE":
            flags = parameters.get("flags") if isinstance(parameters, Mapping) else None
            if flags is None:
                raise ValueError("legacy POLYLINE flags are required")
            if int(flags) & 126:
                raise ValueError(
                    "fitted, spline, 3D, mesh, or polyface POLYLINE requires complete "
                    "reader materialization (delivery_segments_wcs)"
                )
        if primitive_type == "2DPOLYLINE":
            fit_type = parameters.get("polyline_type") if isinstance(parameters, Mapping) else None
            if fit_type is None:
                raise ValueError("2D POLYLINE fit type is required")
            if int(fit_type) != 0:
                raise ValueError(
                    "fitted or spline 2D POLYLINE requires complete reader "
                    "materialization (delivery_segments_wcs)"
                )
        if primitive_type == "3DPOLYLINE" and any(abs(value - z_values[0]) > tolerance for value in z_values):
            raise ValueError("non-planar 3D POLYLINE is outside the 2D delivery contract")
        if abs(float((normal or (0.0, 0.0, 1.0))[2]) + 1.0) <= tolerance and any(
            abs(value) > tolerance for value in bulges[:-1]
        ):
            raise ValueError(
                "negative extrusion bulge orientation requires complete reader materialization"
            )
        if abs(bulges[-1]) > tolerance:
            raise ValueError("open curve has a trailing bulge without a source segment")

        built_segments = []
        for index, (start, end, bulge) in enumerate(zip(vertices, vertices[1:], bulges)):
            points, native_length, kind = _bulge_segment(start, end, bulge, policy)
            built_segments.append({
                "source_segment_index": index,
                "source_segment_kind": kind,
                "source_start_vertex_index": index,
                "source_end_vertex_index": index + 1,
                "source_native_length": native_length,
                "native_length_source": (
                    "analytic_bulge_arc" if kind == "bulge_arc" else "ordered_wcs_vertices"
                ),
                "delivery_native_points": [list(point) for point in points],
                "delivery_chord_length_native": _polyline_length(points),
            })
        segments = tuple(built_segments)
        materialization_method = "analytic-bulge-and-line"

    if not segments:
        raise ValueError("curve materialization produced no source segments")
    native_sum = math.fsum(float(segment["source_native_length"]) for segment in segments)
    facts_length = facts.get("native_length")
    source_length = source.native_length
    if facts_length is None or source_length is None:
        raise ValueError("both curve-facts and source AutoCAD native lengths are required")
    facts_length = _finite(facts_length, "curve_facts.native_length", nonnegative=True)
    source_length = _finite(source_length, "source.native_length", nonnegative=True)
    if not str(facts.get("native_length_source", "")).strip():
        raise ValueError("curve facts native-length provenance is required")
    if not _length_close(facts_length, source_length, policy):
        raise ValueError(
            f"curve/source native-length mismatch: facts={facts_length}, source={source_length}"
        )
    if not _length_close(native_sum, source_length, policy):
        raise ValueError(
            "source-segment native-length closure failed: "
            f"segment_sum={native_sum}, AutoCAD={source_length}"
        )

    joined: list[list[float]] = []
    for segment in segments:
        points = segment["delivery_native_points"]
        if joined and not _points_close(joined[-1], points[0], tolerance):
            raise ValueError("materialized source segments are not endpoint-contiguous")
        joined.extend(points if not joined else points[1:])
    payload: dict[str, Any] = {
        "schema_version": MATERIALIZATION_SCHEMA_VERSION,
        "policy": dict(policy),
        "policy_version": policy["policy_version"],
        "source_curve_fingerprint": source.curve_fingerprint,
        "source_primitive_type": primitive_type,
        "source_native_unit": "cad_drawing_unit",
        "source_native_length": source_length,
        "source_segment_native_length_sum": native_sum,
        "native_length_closure_delta": source_length - native_sum,
        "materialization_method": materialization_method,
        "source_segment_count": len(segments),
        "delivery_vertex_count": len(joined),
        "delivery_native_points": joined,
        "source_segments": list(segments),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload["materialization_fingerprint"] = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    return payload


def _diagnostics(policy: Mapping[str, Any], cables_total: int) -> dict[str, Any]:
    return {
        "schema_version": MATERIALIZATION_SCHEMA_VERSION,
        "policy_version": policy["policy_version"],
        "policy": dict(policy),
        "cables_total": cables_total,
        "cables_materialized": 0,
        "straight_routes": 0,
        "curved_routes": 0,
        "source_segments_total": 0,
        "line_segments": 0,
        "arc_segments": 0,
        "reader_materialized_segments": 0,
        "delivery_vertices_total": 0,
        "unsupported_count": 0,
        "unsupported": [],
        "native_length_closure_max_abs_error": 0.0,
    }


def _accumulate(diagnostics: dict[str, Any], payload: Mapping[str, Any]) -> None:
    segments = payload["source_segments"]
    diagnostics["cables_materialized"] += 1
    diagnostics["source_segments_total"] += len(segments)
    diagnostics["delivery_vertices_total"] += int(payload["delivery_vertex_count"])
    diagnostics["line_segments"] += sum(segment["source_segment_kind"] == "line" for segment in segments)
    diagnostics["arc_segments"] += sum(segment["source_segment_kind"] == "bulge_arc" for segment in segments)
    diagnostics["reader_materialized_segments"] += sum(
        segment["source_segment_kind"] not in {"line", "bulge_arc"} for segment in segments
    )
    if any(segment["source_segment_kind"] != "line" for segment in segments):
        diagnostics["curved_routes"] += 1
    else:
        diagnostics["straight_routes"] += 1
    diagnostics["native_length_closure_max_abs_error"] = max(
        diagnostics["native_length_closure_max_abs_error"],
        abs(float(payload["native_length_closure_delta"])),
    )


def materialize_cable_features(
    entities: Sequence[SourceEntity],
    features: Sequence[Feature],
    *,
    policy: Mapping[str, Any] | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Attach deterministic delivery geometry to every CABLE atomically.

    ``Feature.native_points`` and lineage are intentionally untouched.  In
    strict mode any unsupported/incomplete route prevents all staged updates.
    """
    resolved_policy = _policy(policy)
    cables = sorted(
        (feature for feature in features if feature.feature_class == "CABLE"),
        key=lambda feature: feature.feature_key,
    )
    diagnostics = _diagnostics(resolved_policy, len(cables))
    sources = {entity.entity_key: entity for entity in entities}
    staged: list[tuple[Feature, dict[str, Any]]] = []
    issues: list[dict[str, Any]] = []
    for feature in cables:
        source = sources.get(feature.source_entity_key)
        if source is None:
            issues.append(_issue("MISSING_SOURCE_ENTITY", feature, None, "source entity is absent"))
            continue
        try:
            payload = _materialize_route(feature, source, resolved_policy)
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_issue(
                "UNSUPPORTED_OR_INCOMPLETE_CURVE_FACTS", feature, source, str(exc),
                primitive_type=str(source.curve_facts.get("primitive_type", "")),
                curve_fingerprint=source.curve_fingerprint,
            ))
            continue
        staged.append((feature, payload))
        _accumulate(diagnostics, payload)
    diagnostics["unsupported"] = issues
    diagnostics["unsupported_count"] = len(issues)
    if issues and strict:
        raise CableGeometryMaterializationError(
            "Cable geometry materialization failed closed: "
            + "; ".join(
                f"{item['feature_key']}[{item['code']}]: {item['detail']}" for item in issues
            ),
            diagnostics=diagnostics,
            issues=issues,
        )
    for feature, payload in staged:
        feature.attributes["curve_materialization"] = payload
        feature.attributes["curve_materialization_schema_version"] = MATERIALIZATION_SCHEMA_VERSION
        feature.attributes["curve_materialization_policy_version"] = MATERIALIZATION_POLICY_VERSION
        feature.attributes["curve_source_segment_count"] = payload["source_segment_count"]
        feature.attributes["curve_delivery_vertex_count"] = payload["delivery_vertex_count"]
        feature.field_provenance.update({
            "curve_materialization": "DWG_DERIVED:versioned-loss-aware-curve-materialization",
            "curve_materialization_schema_version": "DWG_DERIVED:versioned-curve-contract",
            "curve_materialization_policy_version": "DWG_DERIVED:versioned-tessellation-policy",
            "curve_source_segment_count": "DWG_DIRECT:ordered-source-curve-segments",
            "curve_delivery_vertex_count": "DWG_DERIVED:bounded-tessellation",
        })
    return diagnostics


def _stored_materialization(feature: Feature) -> Mapping[str, Any] | None:
    value = feature.attributes.get("curve_materialization")
    return value if isinstance(value, Mapping) else None


def delivery_segments(
    feature: Feature,
    *,
    require_materialized: bool = True,
) -> tuple[dict[str, Any], ...]:
    """Return ordered source segments without converting native units to m."""
    payload = _stored_materialization(feature)
    if payload is None:
        if require_materialized and feature.feature_class == "CABLE":
            raise CableGeometryMaterializationError(
                f"CABLE {feature.feature_key} lacks curve materialization",
                diagnostics={"schema_version": MATERIALIZATION_SCHEMA_VERSION},
                issues=(_issue(
                    "MISSING_CURVE_MATERIALIZATION", feature, None,
                    "curve_materialization is absent",
                ),),
            )
        points = tuple((float(point[0]), float(point[1])) for point in feature.native_points)
        return tuple({
            "source_segment_index": index,
            "source_segment_kind": "line",
            "source_start_vertex_index": index,
            "source_end_vertex_index": index + 1,
            "source_native_length": math.dist(start, end),
            "native_length_source": "legacy_ordered_source_vertices",
            "delivery_native_points": (start, end),
            "delivery_chord_length_native": math.dist(start, end),
        } for index, (start, end) in enumerate(zip(points, points[1:])))
    raw_segments = payload.get("source_segments")
    if not isinstance(raw_segments, (list, tuple)):
        raise ValueError(f"CABLE {feature.feature_key} has invalid materialized source segments")
    result = []
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, Mapping) or raw.get("source_segment_index") != index:
            raise ValueError(f"CABLE {feature.feature_key} has unordered materialized source segments")
        points = _ordered_xy(raw.get("delivery_native_points"), "delivery_native_points")
        if len(points) < 2:
            raise ValueError(f"CABLE {feature.feature_key}:segment:{index} has fewer than two points")
        result.append({
            **dict(raw),
            "source_native_length": _finite(
                raw.get("source_native_length"), "source_native_length", positive=True,
            ),
            "delivery_chord_length_native": _finite(
                raw.get("delivery_chord_length_native"),
                "delivery_chord_length_native", positive=True,
            ),
            "delivery_native_points": points,
        })
    return tuple(result)


def delivery_points(
    feature: Feature,
    *,
    require_materialized: bool = True,
) -> tuple[tuple[float, float], ...]:
    segments = delivery_segments(feature, require_materialized=require_materialized)
    result: list[tuple[float, float]] = []
    for segment in segments:
        points = list(segment["delivery_native_points"])
        result.extend(points if not result else points[1:])
    return tuple(result)


def validate_cable_geometry_materialization(
    entities: Sequence[SourceEntity],
    features: Sequence[Feature],
    *,
    policy: Mapping[str, Any] | None = None,
    require_all: bool = True,
) -> dict[str, Any]:
    """Recompute and compare every versioned materialization fail-closed."""
    resolved_policy = _policy(policy)
    cables = sorted(
        (feature for feature in features if feature.feature_class == "CABLE"),
        key=lambda feature: feature.feature_key,
    )
    diagnostics = _diagnostics(resolved_policy, len(cables))
    sources = {entity.entity_key: entity for entity in entities}
    issues: list[dict[str, Any]] = []
    for feature in cables:
        source = sources.get(feature.source_entity_key)
        stored = _stored_materialization(feature)
        if source is None:
            issues.append(_issue("MISSING_SOURCE_ENTITY", feature, None, "source entity is absent"))
            continue
        if stored is None:
            if require_all:
                issues.append(_issue(
                    "MISSING_CURVE_MATERIALIZATION", feature, source,
                    "curve_materialization is absent",
                ))
            continue
        try:
            expected = _materialize_route(feature, source, resolved_policy)
            expected_json = json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stored_json = json.dumps(stored, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_issue(
                "INVALID_CURVE_MATERIALIZATION", feature, source, str(exc),
            ))
            continue
        if stored_json != expected_json:
            issues.append(_issue(
                "CURVE_MATERIALIZATION_MISMATCH", feature, source,
                "stored delivery geometry does not match deterministic recomputation",
                stored_fingerprint=str(stored.get("materialization_fingerprint", "")),
                expected_fingerprint=expected["materialization_fingerprint"],
            ))
            continue
        _accumulate(diagnostics, expected)
    diagnostics["unsupported"] = issues
    diagnostics["unsupported_count"] = len(issues)
    if issues:
        raise CableGeometryMaterializationError(
            "Cable geometry materialization validation failed closed: "
            + "; ".join(
                f"{item['feature_key']}[{item['code']}]: {item['detail']}" for item in issues
            ),
            diagnostics=diagnostics,
            issues=issues,
        )
    diagnostics["validated"] = True
    return diagnostics


__all__ = [
    "CableGeometryMaterializationError",
    "DEFAULT_MATERIALIZATION_POLICY",
    "MATERIALIZATION_POLICY_VERSION",
    "MATERIALIZATION_SCHEMA_VERSION",
    "delivery_points",
    "delivery_segments",
    "materialize_cable_features",
    "validate_cable_geometry_materialization",
]
