"""Deterministic, drawing-relative GCP spatial coverage gates.

These checks are intentionally independent from residual fitting.  A model can
have tiny residuals while all controls occupy one street corner; such a model
does not validate the rest of the drawing.  Coverage therefore uses immutable
nominal target-grid coordinates and a source-profile policy.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from .config import SpatialCoveragePolicy
from .model import SourceEntity

Point = tuple[float, float]

_PLAN_CAD_ROLES = frozenset({"model", "plan"})
_NON_MATERIALIZED_GEOMETRY_TYPES = frozenset({"HATCH"})


def _points(values: Sequence[Sequence[float]], name: str) -> tuple[Point, ...]:
    result: list[Point] = []
    for index, value in enumerate(values):
        try:
            coordinate_count = len(value)
        except TypeError as exc:
            raise ValueError(
                f"{name}[{index}] must contain exactly X and Y"
            ) from exc
        if coordinate_count != 2:
            raise ValueError(f"{name}[{index}] must contain exactly X and Y")
        if isinstance(value[0], bool) or isinstance(value[1], bool):
            raise ValueError(f"{name}[{index}] contains an invalid coordinate")
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name}[{index}] contains an invalid coordinate"
            ) from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(f"{name}[{index}] contains a non-finite coordinate")
        result.append((x, y))
    return tuple(result)


def source_entity_drawing_points(
    entities: Sequence[SourceEntity],
) -> tuple[Point, ...]:
    """Return all finite immutable model-space source vertices.

    Coverage is a property of the geospatial plan, not of the classified output
    subset.  Paper/layout and block-definition coordinates are separate CAD
    spaces, while model-space legends and title material are non-plan roles.
    HATCH records are also excluded because the current reader exposes only a
    placement sentinel, not their boundary loops.  A contradictory layout
    classification or invalid plan coordinate is rejected rather than silently
    shrinking the required coverage extent.
    """
    if isinstance(entities, (str, bytes)) or not isinstance(entities, Sequence):
        raise TypeError("source entities must be a sequence of SourceEntity objects")
    drawing: list[Point] = []
    for entity_index, entity in enumerate(entities):
        if not isinstance(entity, SourceEntity):
            raise TypeError(
                f"source_entities[{entity_index}] must be a SourceEntity"
            )
        layout_is_model = entity.layout.strip().casefold() == "model"
        role_is_model = entity.layout_role.strip().casefold() == "model"
        if layout_is_model != role_is_model:
            raise ValueError(
                f"source_entities[{entity_index}] has inconsistent model-space "
                f"layout metadata: layout={entity.layout!r}, "
                f"layout_role={entity.layout_role!r}"
            )
        cad_role = entity.cad_role.strip().casefold()
        if (
            not role_is_model
            or cad_role not in _PLAN_CAD_ROLES
            or not entity.points
            or entity.dwg_type.strip().upper()
            in _NON_MATERIALIZED_GEOMETRY_TYPES
        ):
            continue
        drawing.extend(
            _points(
                entity.points,
                f"source_entities[{entity_index}].points",
            )
        )
    if not drawing:
        raise ValueError("source drawing has no geometric model-space vertices")
    # Coverage metrics are order-independent; canonical ordering also makes
    # direct helper output stable if an upstream reader changes record order.
    return tuple(sorted(drawing))


def _bbox(points: Sequence[Point]) -> dict[str, float] | None:
    if not points:
        return None
    return {
        "min_easting": min(point[0] for point in points),
        "min_northing": min(point[1] for point in points),
        "max_easting": max(point[0] for point in points),
        "max_northing": max(point[1] for point in points),
    }


def _span(bbox: dict[str, float] | None, axis: str) -> float | None:
    if bbox is None:
        return None
    low, high = (
        ("min_easting", "max_easting")
        if axis == "x"
        else ("min_northing", "max_northing")
    )
    result = bbox[high] - bbox[low]
    return result if math.isfinite(result) else None


def _extent_ratio(
    drawing_bbox: dict[str, float] | None,
    control_bbox: dict[str, float] | None,
    axis: str,
) -> float | None:
    drawing_span = _span(drawing_bbox, axis)
    control_span = _span(control_bbox, axis)
    if drawing_span is None or control_span is None or drawing_span <= 0.0:
        return None
    result = control_span / drawing_span
    return result if math.isfinite(result) else None


def _cross(origin: Point, left: Point, right: Point) -> float:
    return (
        (left[0] - origin[0]) * (right[1] - origin[1])
        - (left[1] - origin[1]) * (right[0] - origin[0])
    )


def _convex_hull(points: Sequence[Point]) -> tuple[Point, ...]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return tuple(unique)
    lower: list[Point] = []
    for point in unique:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[Point] = []
    for point in reversed(unique):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


def _polygon_area(points: Sequence[Point]) -> float | None:
    if len(points) < 3:
        return 0.0
    try:
        result = abs(math.fsum(
            start[0] * end[1] - end[0] * start[1]
            for start, end in zip(points, points[1:] + points[:1])
        )) / 2.0
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _maximum_baseline(points: Sequence[Point]) -> float | None:
    if len(points) < 2:
        return None
    result = max(
        math.dist(left, right)
        for index, left in enumerate(points)
        for right in points[index + 1 :]
    )
    return result if math.isfinite(result) else None


def _bbox_numeric_epsilon(bbox: dict[str, float] | None) -> float | None:
    """Return a scale-aware floating-point tolerance, never a policy margin."""
    if bbox is None:
        return None
    scale = max(1.0, *(abs(value) for value in bbox.values()))
    epsilon = 64.0 * math.ulp(scale)
    return epsilon if math.isfinite(epsilon) else None


def _point_in_bbox(
    point: Point,
    bbox: dict[str, float],
    epsilon: float,
) -> bool:
    return (
        bbox["min_easting"] - epsilon
        <= point[0]
        <= bbox["max_easting"] + epsilon
        and bbox["min_northing"] - epsilon
        <= point[1]
        <= bbox["max_northing"] + epsilon
    )


def _point_in_convex_hull(
    point: Point,
    hull: Sequence[Point],
    epsilon: float,
) -> bool | None:
    """Return inclusive convex-hull containment with numeric tolerance only."""
    if not hull:
        return False
    if len(hull) == 1:
        distance = math.dist(point, hull[0])
        return distance <= epsilon if math.isfinite(distance) else None
    if len(hull) == 2:
        cross = _cross(hull[0], hull[1], point)
        edge_length = math.dist(hull[0], hull[1])
        if not math.isfinite(cross) or not math.isfinite(edge_length):
            return None
        return (
            abs(cross) <= epsilon * max(1.0, edge_length)
            and min(hull[0][0], hull[1][0]) - epsilon
            <= point[0]
            <= max(hull[0][0], hull[1][0]) + epsilon
            and min(hull[0][1], hull[1][1]) - epsilon
            <= point[1]
            <= max(hull[0][1], hull[1][1]) + epsilon
        )
    for start, end in zip(hull, hull[1:] + hull[:1]):
        cross = _cross(start, end, point)
        edge_length = math.dist(start, end)
        if not math.isfinite(cross) or not math.isfinite(edge_length):
            return None
        if cross < -(epsilon * max(1.0, edge_length)):
            return False
    return True


def _below(
    failures: list[str], metric_name: str, value: float | None, minimum: float,
) -> None:
    if value is None:
        failures.append(f"{metric_name} is unavailable")
    elif not math.isfinite(value):
        failures.append(f"{metric_name} is non-finite")
    elif value < minimum:
        failures.append(f"{metric_name} {value:.6f} < required {minimum:.6f}")


def evaluate_spatial_coverage(
    drawing_points: Sequence[Sequence[float]],
    training_points: Sequence[Sequence[float]],
    check_points: Sequence[Sequence[float]],
    policy: SpatialCoveragePolicy,
) -> dict[str, Any]:
    """Return stable metrics plus hard-gate failures for one control set."""
    drawing = _points(drawing_points, "drawing_points")
    training = _points(training_points, "training_points")
    checks = _points(check_points, "check_points")
    drawing_bbox = _bbox(drawing)
    training_bbox = _bbox(training)
    check_bbox = _bbox(checks)
    drawing_x = _span(drawing_bbox, "x")
    drawing_y = _span(drawing_bbox, "y")
    drawing_area = (
        None
        if drawing_x is None or drawing_y is None
        else drawing_x * drawing_y
    )
    if drawing_area is not None and not math.isfinite(drawing_area):
        drawing_area = None
    drawing_diagonal = (
        None
        if drawing_x is None or drawing_y is None
        else math.hypot(drawing_x, drawing_y)
    )
    if drawing_diagonal is not None and not math.isfinite(drawing_diagonal):
        drawing_diagonal = None
    training_hull = _convex_hull(training)
    training_hull_area = _polygon_area(training_hull)
    check_hull = _convex_hull(checks)
    check_hull_area = _polygon_area(check_hull)
    hull_ratio = (
        None
        if drawing_area is None or drawing_area <= 0.0
        else None if training_hull_area is None else training_hull_area / drawing_area
    )
    check_hull_ratio = (
        None
        if drawing_area is None or drawing_area <= 0.0
        else None if check_hull_area is None else check_hull_area / drawing_area
    )
    containment_epsilon = _bbox_numeric_epsilon(drawing_bbox)
    outside_count = None
    outside_ratio = None
    if (
        drawing
        and training_bbox is not None
        and containment_epsilon is not None
    ):
        outside_count = sum(
            not _point_in_bbox(point, training_bbox, containment_epsilon)
            for point in drawing
        )
        outside_ratio = outside_count / len(drawing)
    outside_training_hull_count = None
    outside_training_hull_ratio = None
    if drawing and training_hull and containment_epsilon is not None:
        hull_containment = tuple(
            _point_in_convex_hull(point, training_hull, containment_epsilon)
            for point in drawing
        )
        if all(value is not None for value in hull_containment):
            outside_training_hull_count = sum(
                value is False for value in hull_containment
            )
            outside_training_hull_ratio = (
                outside_training_hull_count / len(drawing)
            )
    training_outside_drawing_count = None
    training_outside_drawing_ratio = None
    check_outside_drawing_count = None
    check_outside_drawing_ratio = None
    if drawing_bbox is not None and containment_epsilon is not None:
        training_outside_drawing_count = sum(
            not _point_in_bbox(point, drawing_bbox, containment_epsilon)
            for point in training
        )
        training_outside_drawing_ratio = (
            None
            if not training
            else training_outside_drawing_count / len(training)
        )
        check_outside_drawing_count = sum(
            not _point_in_bbox(point, drawing_bbox, containment_epsilon)
            for point in checks
        )
        check_outside_drawing_ratio = (
            None if not checks else check_outside_drawing_count / len(checks)
        )
    check_baseline = _maximum_baseline(checks)
    check_baseline_ratio = (
        None
        if check_baseline is None or drawing_diagonal is None or drawing_diagonal <= 0.0
        else check_baseline / drawing_diagonal
    )
    training_x_ratio = _extent_ratio(drawing_bbox, training_bbox, "x")
    training_y_ratio = _extent_ratio(drawing_bbox, training_bbox, "y")

    failures: list[str] = []
    if not drawing:
        failures.append("drawing has no vertices")
    if len(training) < 3:
        failures.append("fewer than 3 active training controls")
    if len(checks) < 3:
        failures.append("fewer than 3 independent active check controls")
    _below(
        failures, "training_extent_coverage_x_ratio", training_x_ratio,
        policy.min_training_extent_x_ratio,
    )
    _below(
        failures, "training_extent_coverage_y_ratio", training_y_ratio,
        policy.min_training_extent_y_ratio,
    )
    _below(
        failures, "training_hull_to_drawing_bbox_area_ratio", hull_ratio,
        policy.min_training_hull_area_ratio,
    )
    if outside_ratio is None:
        failures.append("drawing_vertices_outside_training_bbox_ratio is unavailable")
    elif outside_ratio > policy.max_drawing_vertices_outside_training_bbox_ratio:
        failures.append(
            "drawing_vertices_outside_training_bbox_ratio "
            f"{outside_ratio:.6f} > allowed "
            f"{policy.max_drawing_vertices_outside_training_bbox_ratio:.6f}"
        )
    if policy.max_drawing_vertices_outside_training_hull_ratio is None:
        failures.append(
            "drawing_vertices_outside_training_hull_ratio policy is unavailable; "
            "source profile v4 is required for enabled calibration"
        )
    elif outside_training_hull_ratio is None:
        failures.append(
            "drawing_vertices_outside_training_hull_ratio is unavailable"
        )
    elif (
        outside_training_hull_ratio
        > policy.max_drawing_vertices_outside_training_hull_ratio
    ):
        failures.append(
            "drawing_vertices_outside_training_hull_ratio "
            f"{outside_training_hull_ratio:.6f} > allowed "
            f"{policy.max_drawing_vertices_outside_training_hull_ratio:.6f}"
        )
    if training_outside_drawing_count is None:
        failures.append("training_controls_outside_drawing_bbox is unavailable")
    elif training_outside_drawing_count:
        failures.append(
            "training_controls_outside_drawing_bbox "
            f"{training_outside_drawing_count} > allowed 0"
        )
    if check_outside_drawing_count is None:
        failures.append("check_controls_outside_drawing_bbox is unavailable")
    elif check_outside_drawing_count:
        failures.append(
            "check_controls_outside_drawing_bbox "
            f"{check_outside_drawing_count} > allowed 0"
        )
    _below(
        failures, "check_baseline_to_drawing_diagonal_ratio", check_baseline_ratio,
        policy.min_check_baseline_to_drawing_diagonal_ratio,
    )
    if policy.min_check_hull_area_ratio is None:
        failures.append(
            "check_hull_to_drawing_bbox_area_ratio policy is unavailable; "
            "source profile v3 is required for enabled calibration"
        )
    else:
        _below(
            failures, "check_hull_to_drawing_bbox_area_ratio", check_hull_ratio,
            policy.min_check_hull_area_ratio,
        )

    return {
        "schema_version": "cad2gis-spatial-coverage-v2",
        "policy": policy.to_dict(),
        "drawing_vertex_count": len(drawing),
        "training_control_count": len(training),
        "check_control_count": len(checks),
        "drawing_bbox": drawing_bbox,
        "training_bbox": training_bbox,
        "check_bbox": check_bbox,
        "drawing_bbox_area_m2": drawing_area,
        "drawing_diagonal_m": drawing_diagonal,
        "drawing_bbox_containment_epsilon_m": containment_epsilon,
        "training_extent_coverage_x_ratio": training_x_ratio,
        "training_extent_coverage_y_ratio": training_y_ratio,
        "training_convex_hull_area_m2": training_hull_area,
        "training_hull_to_drawing_bbox_area_ratio": hull_ratio,
        "drawing_vertices_outside_training_bbox": outside_count,
        "drawing_vertices_outside_training_bbox_ratio": outside_ratio,
        "drawing_vertices_outside_training_hull": outside_training_hull_count,
        "drawing_vertices_outside_training_hull_ratio": (
            outside_training_hull_ratio
        ),
        "training_controls_outside_drawing_bbox": (
            training_outside_drawing_count
        ),
        "training_controls_outside_drawing_bbox_ratio": (
            training_outside_drawing_ratio
        ),
        "check_max_baseline_m": check_baseline,
        "check_baseline_to_drawing_diagonal_ratio": check_baseline_ratio,
        "check_convex_hull_area_m2": check_hull_area,
        "check_hull_to_drawing_bbox_area_ratio": check_hull_ratio,
        "check_controls_outside_drawing_bbox": check_outside_drawing_count,
        "check_controls_outside_drawing_bbox_ratio": (
            check_outside_drawing_ratio
        ),
        "passed": not failures,
        "failures": failures,
    }


__all__ = ["evaluate_spatial_coverage", "source_entity_drawing_points"]
