"""Separated support, source-geometry, and optical-relation topology stages."""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections import Counter, defaultdict

from .config import MappingRegistry
from .model import Feature, Relation, SourceEntity
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
    fdt_attr = registry.labels["fdt_id_attribute"].upper()
    fat_attr = registry.labels["fat_sequence_attribute"].upper()
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
            (*_line_distance_measure(asset.native_centroid, route.native_points), route.feature_key, route)
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
    by_class = defaultdict(list)
    for feature in features:
        by_class[feature.feature_class].append(feature)
    supports, boxes, sites = by_class["PTECH"], by_class["BOITE"], by_class["SITE"]
    routes = by_class["CABLE"]
    exact = registry.thresholds["exact"]
    support_tolerance = registry.thresholds["device_to_support_candidate"]

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
        (route.feature_key, index): math.dist(start, end)
        for route in routes
        for index, (start, end) in enumerate(zip(route.native_points, route.native_points[1:]))
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
        relation_status = "accepted" if distance <= registry.thresholds["dimension_to_support"] else "candidate"
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
                point, supports, registry.thresholds["dimension_to_support"],
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

    span_dimensions = [
        entity for entity in entities
        if entity.cad_role == "model" and entity.dwg_type == "DIMENSION"
        and entity.layer.upper() in registry.layers["span_dimension"] and len(entity.points) == 2
    ]
    span_edges, span_edges_all, span_nodes, accepted_dimensions, candidate_dimensions = set(), set(), set(), 0, 0
    span_role_counts = Counter()
    route_dimension_sums = Counter()
    measured_cable_segments = set()
    span_measurement_max_abs_error = 0.0
    span_rule = registry.decision_rules["span_segment_measurement"]
    for dimension in span_dimensions:
        signature = _segment_key(dimension.points[0], dimension.points[1], exact)
        cable_matches = cable_segments.get(signature, ())
        sling_matches = sling_segments.get(signature, ())
        if len(cable_matches) == 1 and not sling_matches:
            owner_key, segment_index = cable_matches[0]
            span_role = "cable_route_span"
            target_key = f"{owner_key}:segment:{segment_index}"
            measured_cable_segments.add((owner_key, segment_index))
            route_dimension_sums[owner_key] += float(dimension.dimension_value or 0.0)
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
            "accepted" if match[1] <= registry.thresholds["dimension_to_support"] else "candidate"
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
        route.attributes["source_cad_length_m"] = sum(
            math.dist(start, end) for start, end in zip(route.native_points, route.native_points[1:])
        )
        route.attributes["dimension_length_m"] = route_dimension_sums.get(route.feature_key)
        route.field_provenance["source_cad_length_m"] = "DWG_DIRECT:polyline-geometry"
        route.field_provenance["dimension_length_m"] = "DWG_DIRECT:SPAN-CABLE-measurements"

    all_cable_segments = {
        (route.feature_key, segment_index)
        for route in routes
        for segment_index in range(max(0, len(route.native_points) - 1))
    }
    unmeasured_cable_segments = sorted(all_cable_segments - measured_cable_segments)
    for route_key, segment_index in unmeasured_cable_segments:
        unresolved.append({
            "kind": "route_span_membership", "route": route_key,
            "segment_index": segment_index, "status": "no_exact_span_dimension",
        })

    groups = _route_groups(routes, exact)
    route_assets, measures = _assign_assets_to_routes(
        boxes, routes, registry.thresholds["route_to_asset"], unresolved,
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
            group_start = group[0].native_points[0]
            site, distance, status = _nearest_unique(group_start, sites, support_tolerance)
            if site is None:
                unresolved.append({"kind": "fdt_component", "layout": layout, "status": status})
            else:
                fdt_rule = registry.decision_rules["fdt_layout_identification"]
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
                registry.decision_rules["fat_layout_sequence"],
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
        "source_route_graph": _source_graph_stats(routes, exact),
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
