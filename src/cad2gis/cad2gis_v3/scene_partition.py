"""Geometry-first separation of plan content from CAD style catalog samples.

Some DWGs keep their legend in Model space beside the actual network.  Layer
names alone cannot distinguish a real route from the short coloured route
samples in that legend.  This module therefore detects only high-confidence
catalog arrangements:

* four or more translated copies of the same open geometry, with diverse
  layers/styles, whose centroids are precisely aligned; or
* five or more precisely aligned INSERTs representing diverse block/layer
  combinations.

The rules use relative geometry and layout regularity.  They contain no
project names, coordinates, telecom layer names, or test-corpus counts.

``detect_legend_candidates`` complements the high-confidence catalog
detector with high-recall *candidates*: subsets of identical symbols that
look like a legend column/row, a duplicate stack of copies on one
insertion point, or an off-network symbol sample cluster.  Real network
symbols cannot always be separated from legend copies by geometry
alone, so these candidates are advisory only — exclusion must be declared
through the reviewed source profile (fail-closed), never inferred here.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from .model import SourceEntity


SCENE_PARTITION_SCHEMA_VERSION = "cad2gis-scene-partition-v1"
LEGEND_CANDIDATES_SCHEMA_VERSION = "cad2gis-legend-candidates-v2"

# Legend-candidate thresholds, all relative to a robust reference span:
# the percentile-trimmed span of the route-layer geometry when a route
# pattern identifies route entities, otherwise of the whole drawing.
# Scaling with the reviewed network (instead of raw drawing extents)
# keeps the ratios meaningful even when title blocks or paper-space strays
# dwarf the plan content.
#
# ``_LEGEND_AXIS_LINK_BAND_RATIO`` (2%) is the single-linkage band that
# chains same-symbol INSERTs sharing nearly the same axis coordinate into
# one column/row subset: large enough to absorb the loose, hand-drawn
# spacing observed in real legend columns (adjacent copies up to a few
# percent of the span apart), small enough that distinct network symbols
# (which sit far apart on both axes) never chain.
# ``_LEGEND_AXIS_RANGE_RATIO`` (8%) caps the total axis spread of one
# subset: observed legend columns are loosely drawn (several percent of
# the span), while real same-symbol network groups span the whole drawing.
# ``_LEGEND_CLUSTER_DIAMETER_RATIO`` (2%) bounds a tight sample cluster.
# ``_LEGEND_ROUTE_CLEARANCE_RATIO`` (1%) is the minimum clearance from
# route-layer geometry: legend samples are drawn beside the network, never
# on it.
_LEGEND_AXIS_LINK_BAND_RATIO = 0.02
_LEGEND_AXIS_RANGE_RATIO = 0.08
_LEGEND_CLUSTER_DIAMETER_RATIO = 0.02
_LEGEND_ROUTE_CLEARANCE_RATIO = 0.01


def _coordinate_span(entities: Sequence[SourceEntity]) -> float:
    coordinates = [
        value
        for entity in entities
        for point in (entity.points or (entity.centroid,))
        for value in point[:2]
        if math.isfinite(float(value))
    ]
    if not coordinates:
        return 0.0
    return max(coordinates) - min(coordinates)


def _drawing_tolerance(entities: Sequence[SourceEntity]) -> float:
    span = _coordinate_span(entities)
    if span == 0.0:
        return 1.0e-7
    return max(1.0e-7, abs(span) * 1.0e-9)


def _percentile(sorted_values: Sequence[float], quantile: float) -> float:
    """Linear-interpolation percentile of an ascending sequence."""

    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    fraction = position - lower
    return (
        sorted_values[lower] * (1.0 - fraction)
        + sorted_values[upper] * fraction
    )


def _robust_coordinate_span(entities: Sequence[SourceEntity]) -> float:
    """Percentile-trimmed drawing span, immune to stray far-away entities.

    Real drawings occasionally hold a few paper-space or stray entities far
    outside the actual plan area; the raw min/max span then explodes and
    every span-relative legend threshold becomes meaningless.  Trimming each
    axis at the 1st/99th percentile keeps the span representative of the
    bulk of the drawing while staying purely geometric.  Degenerate input
    (no coordinates, or a zero trimmed span) falls back to ``1e-7``.
    """

    xs: list[float] = []
    ys: list[float] = []
    for entity in entities:
        for point in (entity.points or (entity.centroid,)):
            x, y = float(point[0]), float(point[1])
            if math.isfinite(x):
                xs.append(x)
            if math.isfinite(y):
                ys.append(y)
    if not xs or not ys:
        return 1.0e-7
    xs.sort()
    ys.sort()
    span = max(
        _percentile(xs, 0.99) - _percentile(xs, 0.01),
        _percentile(ys, 0.99) - _percentile(ys, 0.01),
    )
    if span <= 0.0 or not math.isfinite(span):
        return 1.0e-7
    return span


def _aligned(
    entities: Sequence[SourceEntity],
    *,
    tolerance: float,
) -> tuple[bool, str]:
    if len(entities) < 2:
        return False, ""
    xs = [float(entity.centroid[0]) for entity in entities]
    ys = [float(entity.centroid[1]) for entity in entities]
    if max(xs) - min(xs) <= tolerance:
        return True, "vertical"
    if max(ys) - min(ys) <= tolerance:
        return True, "horizontal"
    return False, ""


def _shape_signature(entity: SourceEntity) -> tuple[Any, ...] | None:
    if entity.closed or len(entity.points) < 2:
        return None
    length = entity.native_length
    if length is None or not math.isfinite(length) or length <= 0.0:
        return None
    origin = entity.points[0]
    relative = tuple(
        (
            round((float(point[0]) - float(origin[0])) / length, 7),
            round((float(point[1]) - float(origin[1])) / length, 7),
        )
        for point in entity.points
    )
    reversed_relative = tuple(
        (
            round((float(point[0]) - float(entity.points[-1][0])) / length, 7),
            round((float(point[1]) - float(entity.points[-1][1])) / length, 7),
        )
        for point in reversed(entity.points)
    )
    canonical = min(relative, reversed_relative)
    return (
        entity.dwg_type.upper(),
        len(entity.points),
        round(math.log10(length), 6),
        canonical,
    )


def detect_style_catalog_entities(
    entities: Iterable[SourceEntity],
    *,
    exempt=None,
) -> tuple[frozenset[str], dict[str, Any]]:
    """Return high-confidence style-catalog roots and auditable diagnostics.

    ``exempt`` is an optional predicate; entities it accepts are left out of
    the catalog candidate pools (route layers reviewed as real network content
    must never vote for, or be removed by, a catalog signature).
    """

    candidates = tuple(entities)
    tolerance = _drawing_tolerance(candidates)
    excluded: set[str] = set()
    groups: list[dict[str, Any]] = []
    if exempt is None:
        pool = candidates
    else:
        pool = tuple(entity for entity in candidates if not exempt(entity))
    exempted_entity_count = len(candidates) - len(pool)

    shapes: dict[tuple[Any, ...], list[SourceEntity]] = defaultdict(list)
    for entity in pool:
        signature = _shape_signature(entity)
        if signature is not None:
            shapes[signature].append(entity)
    for signature, members in sorted(shapes.items(), key=lambda item: repr(item[0])):
        if len(members) < 4:
            continue
        diversity = {
            (entity.layer.casefold(), entity.style.render_key)
            for entity in members
        }
        aligned, axis = _aligned(members, tolerance=tolerance)
        if len(diversity) < 4 or not aligned:
            continue
        keys = sorted(entity.entity_key for entity in members)
        excluded.update(keys)
        groups.append({
            "kind": "translated_shape_catalog",
            "axis": axis,
            "entity_count": len(keys),
            "distinct_style_layer_count": len(diversity),
            "entity_keys": keys,
            "shape_signature": repr(signature),
        })

    inserts = [
        entity for entity in pool if entity.dwg_type.upper() == "INSERT"
    ]
    aligned_buckets: dict[tuple[str, int], list[SourceEntity]] = defaultdict(list)
    for entity in inserts:
        x, y = (float(entity.centroid[0]), float(entity.centroid[1]))
        aligned_buckets[("vertical", round(x / tolerance))].append(entity)
        aligned_buckets[("horizontal", round(y / tolerance))].append(entity)
    claimed_insert_keys: set[str] = set()
    for (axis, _), members in sorted(aligned_buckets.items()):
        members = [
            entity for entity in members
            if entity.entity_key not in claimed_insert_keys
        ]
        if len(members) < 5:
            continue
        diversity = {
            (entity.block_name.casefold(), entity.layer.casefold())
            for entity in members
        }
        aligned, verified_axis = _aligned(members, tolerance=tolerance)
        if len(diversity) < 5 or not aligned or verified_axis != axis:
            continue
        keys = sorted(entity.entity_key for entity in members)
        claimed_insert_keys.update(keys)
        excluded.update(keys)
        groups.append({
            "kind": "aligned_symbol_catalog",
            "axis": axis,
            "entity_count": len(keys),
            "distinct_block_layer_count": len(diversity),
            "entity_keys": keys,
        })

    diagnostics = {
        "schema_version": SCENE_PARTITION_SCHEMA_VERSION,
        "status": "CATALOG_EXCLUDED" if excluded else "NO_CATALOG_DETECTED",
        "coordinate_tolerance": tolerance,
        "excluded_entity_count": len(excluded),
        "groups": groups,
    }
    if exempt is not None:
        diagnostics["exempted_entity_count"] = exempted_entity_count
    return frozenset(excluded), diagnostics


def _min_route_distance(
    members: Sequence[SourceEntity],
    route_entities: Sequence[SourceEntity],
) -> float | None:
    """Nearest member-centroid to route-geometry distance, if measurable."""

    if not route_entities:
        return None
    best: float | None = None
    for member in members:
        point = (float(member.centroid[0]), float(member.centroid[1]))
        for route in route_entities:
            targets = route.points or (route.centroid,)
            for target in targets:
                distance = math.dist(point, (float(target[0]), float(target[1])))
                if best is None or distance < best:
                    best = distance
    return best


def _cluster_extent(
    members: Sequence[SourceEntity],
) -> dict[str, float]:
    xs = [float(entity.centroid[0]) for entity in members]
    ys = [float(entity.centroid[1]) for entity in members]
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
    }


def _cluster_diameter(members: Sequence[SourceEntity]) -> float:
    points = [
        (float(entity.centroid[0]), float(entity.centroid[1]))
        for entity in members
    ]
    diameter = 0.0
    for index, left in enumerate(points):
        for right in points[index + 1:]:
            diameter = max(diameter, math.dist(left, right))
    return diameter


def _route_isolated(min_route_distance: float | None, span: float) -> bool:
    """A group is isolated when no route geometry lies within the clearance."""

    return (
        min_route_distance is None
        or min_route_distance > _LEGEND_ROUTE_CLEARANCE_RATIO * span
    )


def _duplicate_stacks(
    members: Sequence[SourceEntity],
    *,
    tolerance: float,
) -> list[list[SourceEntity]]:
    """Groups of same-symbol INSERTs stacked on one insertion point."""

    cells: dict[tuple[int, int], list[SourceEntity]] = defaultdict(list)
    for entity in members:
        cells[(
            round(float(entity.centroid[0]) / tolerance),
            round(float(entity.centroid[1]) / tolerance),
        )].append(entity)
    stacks = []
    for cell in sorted(cells):
        group = sorted(cells[cell], key=lambda entity: entity.entity_key)
        if len(group) >= 2 and _cluster_diameter(group) <= tolerance:
            stacks.append(group)
    return stacks


def _axis_subsets(
    members: Sequence[SourceEntity],
    *,
    axis_index: int,
    band: float,
) -> list[list[SourceEntity]]:
    """Single-linkage subsets of members chained along one axis coordinate."""

    ordered = sorted(
        members,
        key=lambda entity: (float(entity.centroid[axis_index]), entity.entity_key),
    )
    subsets: list[list[SourceEntity]] = []
    current = [ordered[0]]
    for entity in ordered[1:]:
        gap = (
            float(entity.centroid[axis_index])
            - float(current[-1].centroid[axis_index])
        )
        if gap <= band:
            current.append(entity)
        else:
            subsets.append(current)
            current = [entity]
    subsets.append(current)
    return subsets


def _axis_range(members: Sequence[SourceEntity], axis_index: int) -> float:
    values = [float(entity.centroid[axis_index]) for entity in members]
    return max(values) - min(values)


def _legend_candidate(
    *,
    kind: str,
    members: Sequence[SourceEntity],
    block_name: str,
    layer: str,
    axis: str | None,
    axis_range: float | None,
    span: float,
    tolerance: float,
    min_route_distance: float | None,
    route_entity_count: int,
    review_hint: str,
) -> dict[str, Any]:
    keys = sorted(entity.entity_key for entity in members)
    diameter = _cluster_diameter(members)
    return {
        "group_id": hashlib.sha256(
            "|".join(keys).encode("utf-8")
        ).hexdigest()[:16],
        "kind": kind,
        "entity_keys": keys,
        "member_count": len(keys),
        "block_name": block_name,
        "layer": layer,
        "axis": axis,
        "axis_range": axis_range,
        "axis_range_ratio": (
            (axis_range / span) if axis_range is not None and span > 0.0 else None
        ),
        "min_route_distance": min_route_distance,
        "cluster_extent": _cluster_extent(members),
        "cluster_diameter": diameter,
        "review_hint": review_hint,
        "stats": {
            "coordinate_span": span,
            "drawing_tolerance": tolerance,
            "diameter_span_ratio": (diameter / span) if span > 0.0 else 0.0,
            "route_entity_count": route_entity_count,
        },
    }


def detect_legend_candidates(
    entities: Iterable[SourceEntity],
    *,
    route_pattern=None,
) -> dict[str, Any]:
    """Return high-recall legend/symbol-sample candidate groups.

    Three purely geometric candidate shapes are reported per identical
    ``(block_name, layer)`` INSERT bucket:

    * ``duplicate_stack`` — two or more INSERTs whose centroids coincide
      within the drawing tolerance (copies stacked on one insertion point,
      not an arranged legend);
    * ``same_symbol_column_subset`` — a *subset* of three or more bucket
      members chained along one axis coordinate (single linkage, band of
      two percent of the robust drawing span) whose total axis spread is at
      most eight percent of the span, elongated perpendicular to that axis,
      and isolated from route geometry (nearest route-layer entity farther
      than one percent of the span).  Legend columns live inside large
      buckets of real network symbols, so the whole bucket is never
      required to align; and
    * ``same_symbol_cluster`` — three or more remaining members forming a
      tight cluster (diameter at most two percent of the span) isolated
      from route geometry.

    Stacks take priority over column subsets, and column subsets over
    clusters; each entity appears in at most one candidate.  When
    ``route_pattern`` is ``None`` the route-isolation constraint is not
    evaluated.  All span-relative thresholds use a robust reference span:
    the percentile-trimmed span (``_robust_coordinate_span``) of the
    route-layer geometry when ``route_pattern`` identifies route entities,
    otherwise of the whole drawing.  Legend samples are laid out relative
    to the network they annotate, so the reviewed route geometry — never
    title blocks or paper-space strays, which can outnumber plan content
    and defeat plain percentile trimming — sets the scale.

    Candidates are advisory evidence for review (``review_hint`` states
    why each group was flagged).  Nothing is excluded here: legend
    exclusion must be declared explicitly through the reviewed source
    profile, which fails closed on undeclared or ineligible keys.
    """

    pool = tuple(entities)
    tolerance = _drawing_tolerance(pool)
    route_entities: tuple[SourceEntity, ...] = ()
    if route_pattern is not None:
        route_entities = tuple(
            entity for entity in pool if route_pattern.search(entity.layer)
        )
    span = _robust_coordinate_span(route_entities or pool)

    buckets: dict[tuple[str, str], list[SourceEntity]] = defaultdict(list)
    for entity in pool:
        if entity.dwg_type.upper() == "INSERT":
            buckets[(entity.block_name, entity.layer)].append(entity)

    candidates: list[dict[str, Any]] = []
    for (block_name, layer), grouped in sorted(buckets.items()):
        members = sorted(grouped, key=lambda entity: entity.entity_key)
        if len(members) < 2:
            continue
        claimed: set[str] = set()

        for stack in _duplicate_stacks(members, tolerance=tolerance):
            claimed.update(entity.entity_key for entity in stack)
            candidates.append(_legend_candidate(
                kind="duplicate_stack",
                members=stack,
                block_name=block_name,
                layer=layer,
                axis=None,
                axis_range=None,
                span=span,
                tolerance=tolerance,
                min_route_distance=_min_route_distance(stack, route_entities),
                route_entity_count=len(route_entities),
                review_hint=(
                    f"{len(stack)} INSERTs of the same block/layer share the "
                    "same insertion point; a duplicate stack of copies, not "
                    "an arranged legend column."
                ),
            ))

        for axis_index, axis_name in ((0, "vertical"), (1, "horizontal")):
            remaining = [
                entity for entity in members
                if entity.entity_key not in claimed
            ]
            if len(remaining) < 3:
                break
            for subset in _axis_subsets(
                remaining,
                axis_index=axis_index,
                band=_LEGEND_AXIS_LINK_BAND_RATIO * span,
            ):
                if len(subset) < 3:
                    continue
                axis_range = _axis_range(subset, axis_index)
                if axis_range > _LEGEND_AXIS_RANGE_RATIO * span:
                    continue
                perpendicular_range = _axis_range(subset, 1 - axis_index)
                if perpendicular_range <= axis_range:
                    # Equally spread on both axes: a blob, not a column/row.
                    continue
                min_route_distance = _min_route_distance(subset, route_entities)
                if not _route_isolated(min_route_distance, span):
                    continue
                claimed.update(entity.entity_key for entity in subset)
                direction = "column" if axis_name == "vertical" else "row"
                candidates.append(_legend_candidate(
                    kind="same_symbol_column_subset",
                    members=subset,
                    block_name=block_name,
                    layer=layer,
                    axis=axis_name,
                    axis_range=axis_range,
                    span=span,
                    tolerance=tolerance,
                    min_route_distance=min_route_distance,
                    route_entity_count=len(route_entities),
                    review_hint=(
                        f"{len(subset)} INSERTs of the same block/layer "
                        f"align in a {direction} (axis spread "
                        f"{(axis_range / span) if span > 0.0 else 0.0:.2%} "
                        "of the drawing span), isolated from the route "
                        "network; typical legend arrangement."
                    ),
                ))

        remainder = [
            entity for entity in members if entity.entity_key not in claimed
        ]
        if len(remainder) >= 3:
            diameter = _cluster_diameter(remainder)
            min_route_distance = _min_route_distance(remainder, route_entities)
            if (
                diameter <= _LEGEND_CLUSTER_DIAMETER_RATIO * span
                and _route_isolated(min_route_distance, span)
            ):
                candidates.append(_legend_candidate(
                    kind="same_symbol_cluster",
                    members=remainder,
                    block_name=block_name,
                    layer=layer,
                    axis=None,
                    axis_range=None,
                    span=span,
                    tolerance=tolerance,
                    min_route_distance=min_route_distance,
                    route_entity_count=len(route_entities),
                    review_hint=(
                        f"{len(remainder)} INSERTs of the same block/layer "
                        "form a tight cluster away from the route network; "
                        "possible off-network symbol sample group."
                    ),
                ))

    candidates.sort(
        key=lambda candidate: (
            candidate["kind"],
            candidate["layer"],
            candidate["block_name"],
            candidate["group_id"],
        )
    )
    return {
        "schema_version": LEGEND_CANDIDATES_SCHEMA_VERSION,
        "candidates": candidates,
        "candidate_entity_keys": sorted(
            key for candidate in candidates for key in candidate["entity_keys"]
        ),
    }


__all__ = [
    "LEGEND_CANDIDATES_SCHEMA_VERSION",
    "SCENE_PARTITION_SCHEMA_VERSION",
    "detect_legend_candidates",
    "detect_style_catalog_entities",
]
