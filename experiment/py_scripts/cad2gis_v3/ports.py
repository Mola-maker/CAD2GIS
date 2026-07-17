"""Block-footprint connection-port candidates; never mutates source routes."""

from __future__ import annotations

import math
from collections import defaultdict


def _transform(point, insertion, scale, rotation):
    local_x, local_y = point[0] * scale[0], point[1] * scale[1]
    cosine, sine = math.cos(rotation), math.sin(rotation)
    return (
        insertion[0] + local_x * cosine - local_y * sine,
        insertion[1] + local_x * sine + local_y * cosine,
    )


def _project(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    squared = dx * dx + dy * dy
    if squared <= 1e-18:
        return start
    fraction = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / squared))
    return start[0] + fraction * dx, start[1] + fraction * dy


def _definition_segments(definition_entities, instance):
    segments = []
    for entity in definition_entities:
        if entity.dwg_type not in {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC"}:
            continue
        points = [_transform(point, instance.centroid, instance.scale, instance.style.rotation) for point in entity.points]
        segments.extend(zip(points, points[1:]))
        if entity.closed and len(points) > 2 and points[0] != points[-1]:
            segments.append((points[-1], points[0]))
    return segments


def _nearest_asset(point, assets, tolerance):
    ranked = sorted(
        (math.dist(point, asset.native_centroid), asset.feature_key, asset)
        for asset in assets
    )
    if not ranked or ranked[0][0] > tolerance:
        return None
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] <= 0.01:
        return None
    return ranked[0][2]


def build_port_candidates(entities, features, registry):
    by_source = {entity.entity_key: entity for entity in entities}
    definitions = defaultdict(list)
    for entity in entities:
        if entity.layout.upper().startswith("BLOCKDEF:"):
            definitions[entity.layout.split(":", 1)[1].upper()].append(entity)
    routes = [feature for feature in features if feature.feature_class == "CABLE"]
    supports = [feature for feature in features if feature.feature_class == "PTECH"]
    optical = [feature for feature in features if feature.feature_class in {"BOITE", "SITE"}]
    tolerance = registry.thresholds["device_to_support_candidate"]
    exact = registry.thresholds["exact"]
    candidates, seen = [], set()

    def add_candidate(route, vertex_index, asset, attachment_kind):
        key = (route.feature_key, vertex_index, asset.feature_key, attachment_kind)
        if key in seen:
            return
        seen.add(key)
        instance = by_source.get(asset.source_entity_key)
        if instance is None:
            return
        segments = _definition_segments(definitions.get(instance.block_name.upper(), ()), instance)
        if not segments:
            return
        route_point = route.native_points[vertex_index]
        ranked = sorted(
            (math.dist(route_point, projected), projected)
            for start, end in segments
            for projected in [_project(route_point, start, end)]
        )
        footprint_distance, port_point = ranked[0]
        status = (
            "on_symbol_geometry" if footprint_distance <= exact
            else "near_symbol_geometry" if footprint_distance <= registry.thresholds["dimension_to_support"]
            else "footprint_too_far"
        )
        candidates.append({
            "route_key": route.feature_key,
            "route_source_handle": route.source_handle,
            "vertex_index": vertex_index,
            "asset_key": asset.feature_key,
            "asset_source_handle": asset.source_handle,
            "block_name": instance.block_name,
            "attachment_kind": attachment_kind,
            "route_point_native": list(route_point),
            "port_point_native": list(port_point),
            "center_distance_m": math.dist(route_point, asset.native_centroid),
            "footprint_distance_m": footprint_distance,
            "status": status,
            "transform_basis": "insert+scale+rotation; block base assumed (0,0)",
        })

    for route in routes:
        for vertex_index, point in enumerate(route.native_points):
            support = _nearest_asset(point, supports, tolerance)
            if support is not None:
                add_candidate(route, vertex_index, support, "support_port")
        for vertex_index in (0, len(route.native_points) - 1):
            asset = _nearest_asset(route.native_points[vertex_index], optical, tolerance)
            if asset is not None:
                add_candidate(route, vertex_index, asset, "optical_device_port")
    return sorted(
        candidates,
        key=lambda item: (item["route_source_handle"], item["vertex_index"], item["attachment_kind"]),
    )
