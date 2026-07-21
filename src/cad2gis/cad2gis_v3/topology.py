"""Separated support, source-geometry, and optical-relation topology stages."""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections import Counter, defaultdict

from .curve_geometry import delivery_points, delivery_segments
from .model import Relation
from .ports import build_port_candidates


def _relation(kind, source, target, status, method, distance=None, evidence=()):
    key = hashlib.sha256(f"{kind}|{source}|{target}|{method}".encode()).hexdigest()
    return Relation(key, kind, source, target, status, method, distance, tuple(evidence))


def _nearest_unique(point, features, tolerance):
    ranked = sorted(
        (math.dist(point, feature.native_centroid), feature.feature_key, feature)
        for feature in features
    )
    if not ranked or ranked[0][0] > tolerance:
        return None, None, "outside_tolerance"
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] <= 0.01:
        return None, None, "multiple_optima"
    return ranked[0][2], ranked[0][0], "unique"


def _project_to_segment(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    squared = dx * dx + dy * dy
    if squared <= 1e-18:
        return start, 0.0
    fraction = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / squared))
    return (start[0] + fraction * dx, start[1] + fraction * dy), fraction


def _line_distance_measure(point, line):
    best = (math.inf, math.inf)
    traversed = 0.0
    for start, end in zip(line, line[1:]):
        projected, fraction = _project_to_segment(point, start, end)
        distance = math.dist(point, projected)
        segment_length = math.dist(start, end)
        candidate = (distance, traversed + fraction * segment_length)
        if candidate < best:
            best = candidate
        traversed += segment_length
    return best


def _quantized_point(point, precision):
    return (round(point[0] / precision), round(point[1] / precision))


def _segment_key(start, end, precision):
    left, right = _quantized_point(start, precision), _quantized_point(end, precision)
    return tuple(sorted((left, right)))


def _segment_index(items, precision):
    index = defaultdict(list)
    for owner_key, points in items:
        for segment_index, (start, end) in enumerate(zip(points, points[1:])):
            if math.dist(start, end) <= precision:
                continue
            index[_segment_key(start, end, precision)].append((owner_key, segment_index))
    return index


def _source_graph_stats(routes, precision):
    nodes, edges = set(), set()
    segment_occurrences, self_loops = 0, 0
    for route in routes:
        for start, end in zip(route.native_points, route.native_points[1:]):
            segment_occurrences += 1
            left, right = _quantized_point(start, precision), _quantized_point(end, precision)
            nodes.update((left, right))
            if left == right:
                self_loops += 1
            else:
                edges.add(tuple(sorted((left, right))))
    adjacency = defaultdict(set)
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    seen, components = set(), 0
    for node in nodes:
        if node in seen:
            continue
        components += 1
        stack = [node]
        seen.add(node)
        while stack:
            current = stack.pop()
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
    return {
        "segment_occurrences": segment_occurrences,
        "unique_nodes": len(nodes),
        "unique_edges": len(edges),
        "self_loops": self_loops,
        "duplicate_or_reverse_occurrences": segment_occurrences - self_loops - len(edges),
        "components": components,
    }


def _cross_2d(left, right):
    return left[0] * right[1] - left[1] * right[0]


def _segment_position(fraction, segment_length, tolerance):
    if fraction * segment_length <= tolerance:
        return "start"
    if (1.0 - fraction) * segment_length <= tolerance:
        return "end"
    return "interior"


def _route_segment_intersections(routes, tolerance):
    """Classify source-segment intersections without changing route geometry.

    Proper crossings are intersections in the strict interior of both source
    segments.  Endpoint contacts and collinear overlap remain separate
    observations so none can be mistaken for optical connectivity.
    """
    crossings, shared_endpoints, endpoint_on_segment = [], [], []
    collinear_overlaps, collinear_endpoint_on_segment = [], []
    route_pairs = itertools.combinations(sorted(
        routes,
        key=lambda item: (
            item.feature_key, item.source_handle, item.source_entity_key,
        ),
    ), 2)
    for left, right in route_pairs:
        for left_index, (left_start, left_end) in enumerate(
            zip(left.native_points, left.native_points[1:])
        ):
            left_vector = (
                left_end[0] - left_start[0], left_end[1] - left_start[1],
            )
            left_length = math.hypot(*left_vector)
            if left_length <= tolerance:
                continue
            for right_index, (right_start, right_end) in enumerate(
                zip(right.native_points, right.native_points[1:])
            ):
                right_vector = (
                    right_end[0] - right_start[0], right_end[1] - right_start[1],
                )
                right_length = math.hypot(*right_vector)
                if right_length <= tolerance:
                    continue

                common = {
                    "route_a_key": left.feature_key,
                    "route_a_source_entity_key": left.source_entity_key,
                    "route_a_source_handle": left.source_handle,
                    "route_a_segment_index": left_index,
                    "route_a_segment_key": (
                        f"{left.feature_key}:segment:{left_index}"
                    ),
                    "route_b_key": right.feature_key,
                    "route_b_source_entity_key": right.source_entity_key,
                    "route_b_source_handle": right.source_handle,
                    "route_b_segment_index": right_index,
                    "route_b_segment_key": (
                        f"{right.feature_key}:segment:{right_index}"
                    ),
                }
                endpoint_matches = []
                for left_position, left_point in (
                    ("start", left_start), ("end", left_end),
                ):
                    for right_position, right_point in (
                        ("start", right_start), ("end", right_end),
                    ):
                        distance = math.dist(left_point, right_point)
                        if distance <= tolerance:
                            endpoint_matches.append((
                                distance, left_position, right_position,
                                (
                                    (left_point[0] + right_point[0]) / 2.0,
                                    (left_point[1] + right_point[1]) / 2.0,
                                ),
                            ))
                if endpoint_matches:
                    for _, left_position, right_position, point in sorted(
                        endpoint_matches,
                        key=lambda item: (item[1], item[2], item[0], item[3]),
                    ):
                        shared_endpoints.append({
                            **common,
                            "classification": "shared_source_segment_endpoint",
                            "intersection_native": [point[0], point[1]],
                            "route_a_position": left_position,
                            "route_b_position": right_position,
                        })

                denominator = _cross_2d(left_vector, right_vector)
                parallel_limit = tolerance * max(left_length, right_length)
                left_epsilon = tolerance / left_length
                right_epsilon = tolerance / right_length
                if abs(denominator) <= parallel_limit:
                    right_start_offset = (
                        right_start[0] - left_start[0],
                        right_start[1] - left_start[1],
                    )
                    right_end_offset = (
                        right_end[0] - left_start[0],
                        right_end[1] - left_start[1],
                    )
                    if max(
                        abs(_cross_2d(right_start_offset, left_vector))
                        / left_length,
                        abs(_cross_2d(right_end_offset, left_vector))
                        / left_length,
                    ) > tolerance:
                        continue

                    left_unit = (
                        left_vector[0] / left_length,
                        left_vector[1] / left_length,
                    )
                    right_measures_on_left = (
                        right_start_offset[0] * left_unit[0]
                        + right_start_offset[1] * left_unit[1],
                        right_end_offset[0] * left_unit[0]
                        + right_end_offset[1] * left_unit[1],
                    )
                    overlap_start_measure = max(
                        0.0, min(right_measures_on_left),
                    )
                    overlap_end_measure = min(
                        left_length, max(right_measures_on_left),
                    )
                    overlap_length = max(
                        0.0, overlap_end_measure - overlap_start_measure,
                    )
                    if overlap_length > tolerance:
                        overlap_start = (
                            left_start[0]
                            + overlap_start_measure * left_unit[0],
                            left_start[1]
                            + overlap_start_measure * left_unit[1],
                        )
                        overlap_end = (
                            left_start[0]
                            + overlap_end_measure * left_unit[0],
                            left_start[1]
                            + overlap_end_measure * left_unit[1],
                        )
                        right_squared = right_length * right_length
                        right_fractions = sorted((
                            (
                                (overlap_start[0] - right_start[0])
                                * right_vector[0]
                                + (overlap_start[1] - right_start[1])
                                * right_vector[1]
                            ) / right_squared,
                            (
                                (overlap_end[0] - right_start[0])
                                * right_vector[0]
                                + (overlap_end[1] - right_start[1])
                                * right_vector[1]
                            ) / right_squared,
                        ))
                        right_fractions = [
                            max(0.0, min(1.0, fraction))
                            for fraction in right_fractions
                        ]
                        observation_key = hashlib.sha256(
                            (
                                "collinear-overlap|"
                                f"{common['route_a_segment_key']}|"
                                f"{common['route_b_segment_key']}"
                            ).encode("utf-8")
                        ).hexdigest()
                        collinear_overlaps.append({
                            **common,
                            "observation_key": observation_key,
                            "classification": "collinear_overlap",
                            "status": "observed_not_connection",
                            "method": (
                                "deterministic-collinear-source-segment-overlap"
                            ),
                            "overlap_start_native": [
                                overlap_start[0], overlap_start[1],
                            ],
                            "overlap_end_native": [
                                overlap_end[0], overlap_end[1],
                            ],
                            "overlap_length_native_m": overlap_length,
                            "route_a_fraction_interval": [
                                overlap_start_measure / left_length,
                                overlap_end_measure / left_length,
                            ],
                            "route_b_fraction_interval": right_fractions,
                        })

                    right_squared = right_length * right_length
                    for left_fraction, left_position, point in (
                        (0.0, "start", left_start),
                        (1.0, "end", left_end),
                    ):
                        right_fraction = (
                            (point[0] - right_start[0]) * right_vector[0]
                            + (point[1] - right_start[1]) * right_vector[1]
                        ) / right_squared
                        if not (
                            -right_epsilon
                            <= right_fraction
                            <= 1.0 + right_epsilon
                        ):
                            continue
                        right_fraction = max(
                            0.0, min(1.0, right_fraction),
                        )
                        projected = (
                            right_start[0] + right_fraction * right_vector[0],
                            right_start[1] + right_fraction * right_vector[1],
                        )
                        if (
                            math.dist(point, projected) > tolerance
                            or _segment_position(
                                right_fraction, right_length, tolerance,
                            ) != "interior"
                        ):
                            continue
                        observation_key = hashlib.sha256(
                            (
                                "collinear-endpoint-on-segment|"
                                f"{common['route_a_segment_key']}|"
                                f"{common['route_b_segment_key']}|"
                                f"route_a:{left_position}"
                            ).encode("utf-8")
                        ).hexdigest()
                        collinear_endpoint_on_segment.append({
                            **common,
                            "observation_key": observation_key,
                            "classification": "collinear_endpoint_on_segment",
                            "status": "observed_not_connection",
                            "method": (
                                "deterministic-collinear-source-endpoint-on-segment"
                            ),
                            "intersection_native": [point[0], point[1]],
                            "route_a_fraction": left_fraction,
                            "route_b_fraction": right_fraction,
                            "route_a_position": left_position,
                            "route_b_position": "interior",
                        })

                    left_squared = left_length * left_length
                    for right_fraction, right_position, point in (
                        (0.0, "start", right_start),
                        (1.0, "end", right_end),
                    ):
                        left_fraction = (
                            (point[0] - left_start[0]) * left_vector[0]
                            + (point[1] - left_start[1]) * left_vector[1]
                        ) / left_squared
                        if not (
                            -left_epsilon
                            <= left_fraction
                            <= 1.0 + left_epsilon
                        ):
                            continue
                        left_fraction = max(
                            0.0, min(1.0, left_fraction),
                        )
                        projected = (
                            left_start[0] + left_fraction * left_vector[0],
                            left_start[1] + left_fraction * left_vector[1],
                        )
                        if (
                            math.dist(point, projected) > tolerance
                            or _segment_position(
                                left_fraction, left_length, tolerance,
                            ) != "interior"
                        ):
                            continue
                        observation_key = hashlib.sha256(
                            (
                                "collinear-endpoint-on-segment|"
                                f"{common['route_a_segment_key']}|"
                                f"{common['route_b_segment_key']}|"
                                f"route_b:{right_position}"
                            ).encode("utf-8")
                        ).hexdigest()
                        collinear_endpoint_on_segment.append({
                            **common,
                            "observation_key": observation_key,
                            "classification": "collinear_endpoint_on_segment",
                            "status": "observed_not_connection",
                            "method": (
                                "deterministic-collinear-source-endpoint-on-segment"
                            ),
                            "intersection_native": [point[0], point[1]],
                            "route_a_fraction": left_fraction,
                            "route_b_fraction": right_fraction,
                            "route_a_position": "interior",
                            "route_b_position": right_position,
                        })
                    continue
                if endpoint_matches:
                    continue
                offset = (
                    right_start[0] - left_start[0],
                    right_start[1] - left_start[1],
                )
                left_fraction = _cross_2d(offset, right_vector) / denominator
                right_fraction = _cross_2d(offset, left_vector) / denominator
                if not (
                    -left_epsilon <= left_fraction <= 1.0 + left_epsilon
                    and -right_epsilon <= right_fraction <= 1.0 + right_epsilon
                ):
                    continue
                left_fraction = max(0.0, min(1.0, left_fraction))
                right_fraction = max(0.0, min(1.0, right_fraction))
                point = (
                    left_start[0] + left_fraction * left_vector[0],
                    left_start[1] + left_fraction * left_vector[1],
                )
                left_position = _segment_position(
                    left_fraction, left_length, tolerance,
                )
                right_position = _segment_position(
                    right_fraction, right_length, tolerance,
                )
                record = {
                    **common,
                    "intersection_native": [point[0], point[1]],
                    "route_a_fraction": left_fraction,
                    "route_b_fraction": right_fraction,
                    "route_a_position": left_position,
                    "route_b_position": right_position,
                }
                if left_position == right_position == "interior":
                    candidate_key = hashlib.sha256(
                        (
                            "proper-interior-crossing|"
                            f"{left.feature_key}|{left_index}|"
                            f"{right.feature_key}|{right_index}"
                        ).encode("utf-8")
                    ).hexdigest()
                    crossings.append({
                        **record,
                        "candidate_key": candidate_key,
                        "classification": "proper_interior_crossing",
                        "status": "candidate_not_connection",
                        "method": "deterministic-proper-interior-source-segment-crossing",
                    })
                else:
                    endpoint_on_segment.append({
                        **record,
                        "classification": "source_endpoint_on_segment",
                    })
    return (
        crossings, shared_endpoints, endpoint_on_segment,
        collinear_overlaps, collinear_endpoint_on_segment,
    )


def _route_groups(routes, exact_tolerance):
    adjacency = defaultdict(set)
    for left, right in itertools.combinations(routes, 2):
        endpoints_left = (left.native_points[0], left.native_points[-1])
        endpoints_right = (right.native_points[0], right.native_points[-1])
        if min(math.dist(a, b) for a in endpoints_left for b in endpoints_right) <= exact_tolerance:
            adjacency[left.feature_key].add(right.feature_key)
            adjacency[right.feature_key].add(left.feature_key)
    by_key = {route.feature_key: route for route in routes}
    seen, groups = set(), []
    for route in sorted(routes, key=lambda item: item.source_handle):
        if route.feature_key in seen:
            continue
        stack, group = [route.feature_key], []
        seen.add(route.feature_key)
        while stack:
            key = stack.pop()
            group.append(by_key[key])
            for neighbour in adjacency[key]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        groups.append(sorted(group, key=lambda item: item.source_handle))
    return groups


def _layout_facts(entities, registry):
    fdt_attr = str(registry.labels.get("fdt_id_attribute", "")).upper()
    fat_attr = str(registry.labels.get("fat_sequence_attribute", "")).upper()
    if not fdt_attr or not fat_attr:
        return {}
    facts = defaultdict(lambda: {"fdt_ids": set(), "sequences": set(), "evidence": set()})
    for entity in entities:
        match = re.match(r"(?i)^(FDT-\d+)", entity.layout.strip())
        if not match:
            continue
        layout_key = match.group(1).upper()
        if entity.block_attributes.get(fdt_attr):
            facts[layout_key]["fdt_ids"].add(entity.block_attributes[fdt_attr])
            facts[layout_key]["evidence"].add(entity.entity_key)
        if entity.block_attributes.get(fat_attr):
            facts[layout_key]["sequences"].add(entity.block_attributes[fat_attr])
            facts[layout_key]["evidence"].add(entity.entity_key)
    result = {}
    for layout, value in facts.items():
        if len(value["fdt_ids"]) != 1 or not value["sequences"]:
            continue
        result[layout] = {
            "fdt_id": next(iter(value["fdt_ids"])),
            "sequences": sorted(value["sequences"]),
            "evidence": sorted(value["evidence"]),
        }
    return result


def _assign_assets_to_routes(assets, routes, tolerance, unresolved):
    result = defaultdict(list)
    measures = {}
    for asset in assets:
        ranked = sorted(
            (
                *_line_distance_measure(
                    asset.native_centroid,
                    delivery_points(route, require_materialized=False),
                ),
                route.feature_key,
                route,
            )
            for route in routes
        )
        if not ranked or ranked[0][0] > tolerance:
            unresolved.append({"kind": "route_asset", "asset": asset.feature_key, "status": "outside_tolerance"})
            continue
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] <= 0.01:
            unresolved.append({"kind": "route_asset", "asset": asset.feature_key, "status": "multiple_optima"})
            continue
        distance, measure, _, route = ranked[0]
        result[route.feature_key].append(asset)
        measures[(route.feature_key, asset.feature_key)] = (distance, measure)
    return result, measures


def _match_groups_to_layouts(groups, route_assets, layout_facts):
    layouts = sorted(layout_facts)
    if len(groups) != len(layouts):
        return None
    scored = []
    for permutation in itertools.permutations(layouts):
        score = 0
        for group, layout in zip(groups, permutation):
            asset_count = sum(len(route_assets[route.feature_key]) for route in group)
            score += abs(asset_count - len(layout_facts[layout]["sequences"]))
        scored.append((score, permutation))
    scored.sort()
    if not scored or scored[0][0] != 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return {index: layout for index, layout in enumerate(scored[0][1])}


def _assign_branch_labels(group, facts, route_assets, measures, unresolved, rule):
    branch_sequences = defaultdict(list)
    for sequence in facts["sequences"]:
        match = re.search(r"\.([A-Z])([0-9]{2})$", sequence)
        if match:
            branch_sequences[match.group(1)].append(sequence)
    branches = sorted(branch_sequences)
    if len(group) != len(branches):
        return 0
    candidates = []
    for permutation in itertools.permutations(branches):
        score, valid = 0, True
        for route, branch in zip(group, permutation):
            assets = route_assets[route.feature_key]
            score += abs(len(assets) - len(branch_sequences[branch]))
            observed = {
                match.group(1)
                for asset in assets
                if asset.display_label
                for match in [re.search(r"\.([A-Z])[0-9]{2}$", asset.display_label)]
                if match
            }
            if observed and observed != {branch}:
                valid = False
                break
        if valid:
            candidates.append((score, permutation))
    candidates.sort()
    if not candidates or candidates[0][0] != 0 or (len(candidates) > 1 and candidates[1][0] == 0):
        unresolved.append({"kind": "fat_branch_mapping", "fdt_id": facts["fdt_id"], "status": "not_unique"})
        return 0
    assigned = 0
    for route, branch in zip(group, candidates[0][1]):
        assets = sorted(
            route_assets[route.feature_key],
            key=lambda asset: (measures[(route.feature_key, asset.feature_key)][1], asset.source_handle),
        )
        sequences = branch_sequences[branch]
        if len(assets) != len(sequences):
            continue
        if any(asset.display_label and asset.display_label != expected for asset, expected in zip(assets, sequences)):
            unresolved.append({"kind": "fat_sequence", "route": route.feature_key, "status": "direct_label_conflict"})
            continue
        for asset, sequence in zip(assets, sequences):
            if not asset.display_label:
                asset.attributes["CODE"] = sequence
                asset.field_provenance["CODE"] = rule["provenance"]
                asset.display_label = sequence
                asset.label_provenance = rule["provenance"]
                assigned += 1
    return assigned


def build_topology(entities, features, registry, existing_relations, unresolved):
    relations = list(existing_relations)
    source_route_native_lengths = 0
    source_route_native_length_max_abs_delta = 0.0
    by_class = defaultdict(list)
    for feature in features:
        by_class[feature.feature_class].append(feature)
    supports, boxes, sites = by_class["PTECH"], by_class["BOITE"], by_class["SITE"]
    routes = by_class["CABLE"]
    route_source_segments = {
        route.feature_key: delivery_segments(route, require_materialized=False)
        for route in routes
    }
    exact_value = registry.thresholds.get("exact")
    if exact_value is None:
        raise RuntimeError("Topology requires a reviewed 'exact' distance threshold")
    exact = float(exact_value)
    support_tolerance_value = registry.thresholds.get("device_to_support_candidate")
    dimension_support_value = registry.thresholds.get("dimension_to_support")
    if support_tolerance_value is None:
        support_tolerance = exact
        if supports or boxes or sites or routes:
            unresolved.append({
                "kind": "topology_threshold",
                "status": "missing_reviewed_device_to_support_candidate",
                "effective_policy": "exact_matches_only",
            })
    else:
        support_tolerance = float(support_tolerance_value)
    if dimension_support_value is None:
        dimension_support_tolerance = exact
        if supports or boxes or sites or routes:
            unresolved.append({
                "kind": "topology_threshold",
                "status": "missing_reviewed_dimension_to_support",
                "effective_policy": "exact_matches_only",
            })
    else:
        dimension_support_tolerance = float(dimension_support_value)

    cable_segments = _segment_index(
        ((route.feature_key, route.native_points) for route in routes), exact,
    )
    sling_entities = [
        entity for entity in entities
        if entity.cad_role == "model"
        and entity.dwg_type in {"LWPOLYLINE", "POLYLINE"}
        and entity.layer.upper() in registry.layers.get("sling_wire", ())
    ]
    sling_segments = _segment_index((
        (entity.entity_key, entity.points)
        for entity in sling_entities
    ), exact)
    segment_lengths = {
        (route.feature_key, int(segment["source_segment_index"])): float(
            segment["source_native_length"]
        )
        for route in routes
        for segment in route_source_segments[route.feature_key]
    }
    segment_lengths.update({
        (entity.entity_key, index): math.dist(start, end)
        for entity in sling_entities
        for index, (start, end) in enumerate(zip(entity.points, entity.points[1:]))
    })

    for asset in boxes + sites:
        support, distance, status = _nearest_unique(asset.native_centroid, supports, support_tolerance)
        if support is None:
            unresolved.append({"kind": "supported_by", "asset": asset.feature_key, "status": status})
            continue
        relation_status = "accepted" if distance <= dimension_support_tolerance else "candidate"
        relations.append(_relation(
            "supported_by", asset.feature_key, support.feature_key, relation_status,
            "unique-native-device-support" if relation_status == "accepted" else "device-port-review-required",
            distance,
            (asset.source_entity_key, support.source_entity_key),
        ))

    route_vertex_support = Counter()
    for route in routes:
        for vertex_index, point in enumerate(route.native_points):
            support, distance, status = _nearest_unique(
                point, supports, dimension_support_tolerance,
            )
            if support is None:
                route_vertex_support["unresolved"] += 1
                unresolved.append({
                    "kind": "route_vertex_support", "route": route.feature_key,
                    "vertex": vertex_index, "status": status,
                })
                continue
            relation_status = "accepted" if distance <= exact else "candidate"
            route_vertex_support[relation_status] += 1
            relations.append(_relation(
                "supported_by", f"{route.feature_key}:vertex:{vertex_index}", support.feature_key,
                relation_status,
                "exact-route-vertex-support" if relation_status == "accepted" else "near-route-port-review-required",
                distance, (route.source_entity_key, support.source_entity_key),
            ))

    span_dimension_layers = set(registry.layers.get("span_dimension", ()))
    span_dimensions = [
        entity for entity in entities
        if entity.cad_role == "model" and entity.dwg_type == "DIMENSION"
        and entity.layer.upper() in span_dimension_layers and len(entity.points) == 2
    ]
    span_edges, span_edges_all, span_nodes, accepted_dimensions, candidate_dimensions = set(), set(), set(), 0, 0
    span_role_counts = Counter()
    route_segment_dimensions = defaultdict(list)
    measured_cable_segments = set()
    span_measurement_max_abs_error = 0.0
    span_rule = registry.decision_rules.get("span_segment_measurement")
    for dimension in span_dimensions:
        signature = _segment_key(dimension.points[0], dimension.points[1], exact)
        cable_matches = cable_segments.get(signature, ())
        sling_matches = sling_segments.get(signature, ())
        if len(cable_matches) == 1 and not sling_matches:
            owner_key, segment_index = cable_matches[0]
            span_role = "cable_route_span"
            target_key = f"{owner_key}:segment:{segment_index}"
            route_segment_dimensions[(owner_key, segment_index)].append(dimension)
        elif len(sling_matches) == 1 and not cable_matches:
            owner_key, segment_index = sling_matches[0]
            span_role = "sling_wire_span"
            target_key = f"{owner_key}:segment:{segment_index}"
        else:
            span_role = "unresolved_segment_role"
            target_key = ""
        span_role_counts[span_role] += 1
        if target_key:
            if dimension.dimension_value is not None:
                span_measurement_max_abs_error = max(
                    span_measurement_max_abs_error,
                    abs(float(dimension.dimension_value) - segment_lengths[(owner_key, segment_index)]),
                )
            if span_rule is None:
                unresolved.append({
                    "kind": "span_segment_measurement",
                    "entity_key": dimension.entity_key,
                    "status": "missing_reviewed_decision_rule",
                })
                if span_role == "cable_route_span":
                    route_segment_dimensions[(owner_key, segment_index)].remove(dimension)
            else:
                relations.append(_relation(
                    "measures", dimension.entity_key, target_key, "accepted",
                    f"{span_rule['rule_id']}:{span_rule['method']}", 0.0,
                    (dimension.entity_key, owner_key),
                ))
        else:
            unresolved.append({
                "kind": "span_segment_role", "entity_key": dimension.entity_key,
                "status": "ambiguous_or_unmatched", "cable_matches": len(cable_matches),
                "sling_matches": len(sling_matches),
            })
        matches = [_nearest_unique(point, supports, support_tolerance) for point in dimension.points]
        if any(match[0] is None for match in matches):
            unresolved.append({"kind": "span_dimension", "entity_key": dimension.entity_key, "status": "endpoint_unresolved"})
            continue
        endpoint_statuses = [
            "accepted" if match[1] <= dimension_support_tolerance else "candidate"
            for match in matches
        ]
        dimension_status = "accepted" if all(status == "accepted" for status in endpoint_statuses) else "candidate"
        if dimension_status == "accepted":
            accepted_dimensions += 1
        else:
            candidate_dimensions += 1
        support_pair = [matches[0][0], matches[1][0]]
        for endpoint, ((support, distance, _), endpoint_status) in enumerate(zip(matches, endpoint_statuses)):
            relations.append(_relation(
                "supported_by", f"{dimension.entity_key}:endpoint:{endpoint}", support.feature_key,
                endpoint_status,
                "unique-dimension-endpoint-support" if endpoint_status == "accepted" else "dimension-endpoint-review-required",
                distance,
                (dimension.entity_key, support.source_entity_key),
            ))
        if support_pair[0].feature_key != support_pair[1].feature_key:
            edge = tuple(sorted((support_pair[0].feature_key, support_pair[1].feature_key)))
            span_edges_all.add(edge)
            if dimension_status == "accepted":
                span_edges.add(edge)
            span_nodes.update(edge)

    for route in routes:
        span_metrics = []
        dimension_total = 0.0
        measured_count = 0
        for segment in route_source_segments[route.feature_key]:
            segment_index = int(segment["source_segment_index"])
            source_length = float(segment["source_native_length"])
            dimensions = sorted(
                route_segment_dimensions.get((route.feature_key, segment_index), ()),
                key=lambda item: item.entity_key,
            )
            dimension_key = None
            measurement = None
            measurement_delta = None
            if len(dimensions) == 1:
                dimension = dimensions[0]
                dimension_key = dimension.entity_key
                if dimension.dimension_value is None:
                    status = "unmeasured_missing_dimension_value"
                else:
                    measurement = float(dimension.dimension_value)
                    measurement_delta = measurement - source_length
                    status = "measured"
                    measured_count += 1
                    measured_cable_segments.add((route.feature_key, segment_index))
                    dimension_total += measurement
            elif len(dimensions) > 1:
                status = "unmeasured_ambiguous_dimensions"
                unresolved.append({
                    "kind": "route_span_membership",
                    "route": route.feature_key,
                    "segment_index": segment_index,
                    "status": "multiple_exact_span_dimensions",
                    "dimension_entity_keys": [item.entity_key for item in dimensions],
                })
            else:
                status = "unmeasured_no_dimension"
            span_metrics.append({
                "segment_index": segment_index,
                "source_native_length_m": source_length,
                "source_segment_kind": str(segment["source_segment_kind"]),
                "native_length_source": str(segment["native_length_source"]),
                "delivery_native_vertex_count": len(segment["delivery_native_points"]),
                "delivery_chord_length_native": float(
                    segment["delivery_chord_length_native"]
                ),
                "dimension_entity_key": dimension_key,
                "measurement_native_m": measurement,
                "measurement_delta_m": measurement_delta,
                "status": status,
            })

        span_count = len(span_metrics)
        source_segment_sum = sum(
            metric["source_native_length_m"] for metric in span_metrics
        )
        source_autocad_native_length = route.attributes.get(
            "source_autocad_native_length_m"
        )
        if source_autocad_native_length is not None:
            source_autocad_native_length = float(source_autocad_native_length)
            source_route_native_lengths += 1
            source_length_delta = source_autocad_native_length - source_segment_sum
            source_route_native_length_max_abs_delta = max(
                source_route_native_length_max_abs_delta, abs(source_length_delta),
            )
        else:
            source_autocad_native_length = source_segment_sum
            source_length_delta = None
        route.attributes.update({
            "source_cad_length_m": source_autocad_native_length,
            "source_segment_sum_m": source_segment_sum,
            "source_native_length_delta_m": source_length_delta,
            "dimension_length_m": dimension_total if measured_count else None,
            "span_count": span_count,
            "measured_span_count": measured_count,
            "unmeasured_span_count": span_count - measured_count,
            "span_metrics": span_metrics,
        })
        route.field_provenance.update({
            "source_cad_length_m": (
                "DWG_DIRECT:AutoCAD-curve-distance"
                if source_length_delta is not None
                else "DWG_DERIVED:source-segment-native-length-sum-fallback"
            ),
            "source_segment_sum_m": "DWG_DERIVED:source-segment-native-curve-length-sum",
            "source_native_length_delta_m": "DWG_DERIVED:AutoCAD-minus-segment-sum",
            "dimension_length_m": "DWG_DIRECT:SPAN-CABLE-measurements",
            "span_count": "DWG_DIRECT:ordered-source-curve-segment-count",
            "measured_span_count": (
                span_rule["provenance"] if span_rule else "UNAVAILABLE:no-reviewed-span-rule"
            ),
            "unmeasured_span_count": (
                span_rule["provenance"] if span_rule else "UNAVAILABLE:no-reviewed-span-rule"
            ),
            "span_metrics": (
                span_rule["provenance"] if span_rule else "UNAVAILABLE:no-reviewed-span-rule"
            ),
        })

    all_cable_segments = {
        (route.feature_key, segment_index)
        for route in routes
        for segment_index in range(len(route_source_segments[route.feature_key]))
    }
    unmeasured_cable_segments = sorted(all_cable_segments - measured_cable_segments)
    routes_by_key = {route.feature_key: route for route in routes}
    for route_key, segment_index in unmeasured_cable_segments:
        metric_status = routes_by_key[route_key].attributes["span_metrics"][segment_index]["status"]
        if metric_status == "unmeasured_ambiguous_dimensions":
            continue
        unresolved.append({
            "kind": "route_span_membership", "route": route_key,
            "segment_index": segment_index,
            "status": (
                "no_exact_span_dimension"
                if metric_status == "unmeasured_no_dimension"
                else "exact_span_dimension_without_measurement"
            ),
        })

    groups = _route_groups(routes, exact)
    source_route_graph = _source_graph_stats(routes, exact)
    component_diagnostics = {
        "route_group_components": len(groups),
        "source_segment_graph_components": source_route_graph["components"],
        "status": (
            "consistent"
            if len(groups) == source_route_graph["components"]
            else "mismatch"
        ),
    }
    if component_diagnostics["status"] == "mismatch":
        unresolved.append({
            "kind": "source_route_components",
            "status": "route_group_source_segment_graph_mismatch",
            "route_group_components": len(groups),
            "source_segment_graph_components": source_route_graph["components"],
        })
    (
        crossing_candidates,
        shared_segment_endpoints,
        endpoint_on_segment,
        collinear_overlaps,
        collinear_endpoint_on_segment,
    ) = _route_segment_intersections(routes, exact)
    for candidate in crossing_candidates:
        crossing_relation = _relation(
            "crossing_candidate",
            (
                f"{candidate['route_a_key']}:segment:"
                f"{candidate['route_a_segment_index']}"
            ),
            (
                f"{candidate['route_b_key']}:segment:"
                f"{candidate['route_b_segment_index']}"
            ),
            "candidate", candidate["method"], 0.0,
            (
                candidate["route_a_source_entity_key"],
                candidate["route_b_source_entity_key"],
            ),
        )
        candidate["relation_key"] = crossing_relation.relation_key
        relations.append(crossing_relation)
    route_to_asset_tolerance = registry.thresholds.get("route_to_asset")
    if boxes and route_to_asset_tolerance is None:
        route_assets, measures = defaultdict(list), {}
        unresolved.append({
            "kind": "route_asset",
            "status": "missing_reviewed_route_to_asset_threshold",
        })
    else:
        route_assets, measures = _assign_assets_to_routes(
            boxes, routes, float(route_to_asset_tolerance or exact), unresolved,
        )
    facts = _layout_facts(entities, registry)
    group_layouts = _match_groups_to_layouts(groups, route_assets, facts)
    inferred_fat_labels = 0
    if group_layouts is None:
        unresolved.append({"kind": "layout_component_mapping", "status": "not_unique"})
    else:
        for index, group in enumerate(groups):
            layout = group_layouts[index]
            group_facts = facts[layout]
            fdt_rule = registry.decision_rules.get("fdt_layout_identification")
            fat_rule = registry.decision_rules.get("fat_layout_sequence")
            if fdt_rule is None or fat_rule is None:
                unresolved.append({
                    "kind": "layout_component_mapping",
                    "layout": layout,
                    "status": "missing_reviewed_decision_rule",
                })
                continue
            group_start = group[0].native_points[0]
            site, distance, status = _nearest_unique(group_start, sites, support_tolerance)
            if site is None:
                unresolved.append({"kind": "fdt_component", "layout": layout, "status": status})
            else:
                site.attributes["CODE"] = group_facts["fdt_id"]
                site.field_provenance["CODE"] = fdt_rule["provenance"]
                site.display_label = group_facts["fdt_id"]
                site.label_provenance = fdt_rule["provenance"]
                relations.append(_relation(
                    "identifies", layout, site.feature_key, "accepted",
                    f"{fdt_rule['rule_id']}:{fdt_rule['method']}", distance,
                    tuple(group_facts["evidence"]) + (site.source_entity_key,),
                ))
            inferred_fat_labels += _assign_branch_labels(
                group, group_facts, route_assets, measures, unresolved,
                fat_rule,
            )

    optical_nodes = boxes + sites
    for route in routes:
        endpoints = (("ORIGINE", route.native_points[0]), ("EXTREMITE", route.native_points[-1]))
        for field_name, point in endpoints:
            target, distance, status = _nearest_unique(point, optical_nodes, support_tolerance)
            if target is None:
                unresolved.append({"kind": "route_endpoint", "route": route.feature_key, "endpoint": field_name, "status": status})
                continue
            relations.append(_relation(
                "connects", route.feature_key, target.feature_key, "candidate",
                f"device-port-review-required-{field_name.lower()}", distance,
                (route.source_entity_key, target.source_entity_key),
            ))

    port_candidates = build_port_candidates(entities, features, registry)
    diagnostics = {
        "source_route_components": len(groups),
        "source_routes": len(routes),
        "source_route_graph": source_route_graph,
        "source_route_component_diagnostics": component_diagnostics,
        "route_crossing_candidates": crossing_candidates,
        "route_shared_segment_endpoints": shared_segment_endpoints,
        "route_endpoint_on_segment_intersections": endpoint_on_segment,
        "route_collinear_overlaps": collinear_overlaps,
        "route_collinear_endpoint_on_segment": collinear_endpoint_on_segment,
        "route_segment_intersection_counts": {
            "proper_interior_crossing": len(crossing_candidates),
            "shared_source_segment_endpoint": len(shared_segment_endpoints),
            "source_endpoint_on_segment": len(endpoint_on_segment),
            "collinear_overlap": len(collinear_overlaps),
            "collinear_endpoint_on_segment": len(
                collinear_endpoint_on_segment
            ),
        },
        "route_vertex_support": dict(sorted(route_vertex_support.items())),
        "synthetic_route_vertices": 0,
        "connection_port_candidates": port_candidates,
        "connection_port_status_counts": dict(sorted(Counter(
            candidate["status"] for candidate in port_candidates
        ).items())),
        "span_dimensions": len(span_dimensions),
        "accepted_span_dimensions": accepted_dimensions,
        "candidate_span_dimensions": candidate_dimensions,
        "unique_span_edges": len(span_edges),
        "unique_span_edges_all": len(span_edges_all),
        "span_support_nodes": len(span_nodes),
        "span_roles": dict(sorted(span_role_counts.items())),
        "span_measurement_max_abs_error_m": span_measurement_max_abs_error,
        "source_route_native_lengths": source_route_native_lengths,
        "source_route_native_length_max_abs_delta_m": (
            source_route_native_length_max_abs_delta
        ),
        "route_curve_materialization": {
            "materialized_routes": sum(
                bool(route.attributes.get("curve_materialization")) for route in routes
            ),
            "source_segments": sum(
                len(route_source_segments[route.feature_key]) for route in routes
            ),
            "curved_source_segments": sum(
                segment["source_segment_kind"] != "line"
                for route in routes
                for segment in route_source_segments[route.feature_key]
            ),
            "delivery_vertices": sum(
                len(delivery_points(route, require_materialized=False)) for route in routes
            ),
        },
        "route_segment_occurrences": len(all_cable_segments),
        "route_segments_with_span_dimension": len(measured_cable_segments),
        "route_segments_without_span_dimension": len(unmeasured_cable_segments),
        "layout_facts": {
            key: {"fdt_id": value["fdt_id"], "fat_sequences": len(value["sequences"])}
            for key, value in sorted(facts.items())
        },
        "inferred_fat_labels": inferred_fat_labels,
        "fat_labels_total": sum(bool(feature.display_label) for feature in boxes),
        "policy": dict(registry.policy),
    }
    return relations, unresolved, diagnostics
