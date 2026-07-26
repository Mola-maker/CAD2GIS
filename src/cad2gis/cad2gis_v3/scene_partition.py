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
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from .model import SourceEntity


SCENE_PARTITION_SCHEMA_VERSION = "cad2gis-scene-partition-v1"


def _drawing_tolerance(entities: Sequence[SourceEntity]) -> float:
    coordinates = [
        value
        for entity in entities
        for point in (entity.points or (entity.centroid,))
        for value in point[:2]
        if math.isfinite(float(value))
    ]
    if not coordinates:
        return 1.0e-7
    span = max(coordinates) - min(coordinates)
    return max(1.0e-7, abs(span) * 1.0e-9)


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
) -> tuple[frozenset[str], dict[str, Any]]:
    """Return high-confidence style-catalog roots and auditable diagnostics."""

    candidates = tuple(entities)
    tolerance = _drawing_tolerance(candidates)
    excluded: set[str] = set()
    groups: list[dict[str, Any]] = []

    shapes: dict[tuple[Any, ...], list[SourceEntity]] = defaultdict(list)
    for entity in candidates:
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
        entity for entity in candidates if entity.dwg_type.upper() == "INSERT"
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
    return frozenset(excluded), diagnostics


__all__ = [
    "SCENE_PARTITION_SCHEMA_VERSION",
    "detect_style_catalog_entities",
]
