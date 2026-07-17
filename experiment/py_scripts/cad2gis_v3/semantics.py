"""Reviewed APD semantic candidates and evidence-bound annotation linking."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict

from .config import MappingRegistry
from .model import Feature, Relation, SourceEntity


def _feature_key(entity: SourceEntity, feature_class: str) -> str:
    return hashlib.sha256(f"{entity.entity_key}|{feature_class}".encode("utf-8")).hexdigest()


def _generated_code(feature_class: str, handle: str) -> str:
    return f"{feature_class}-CAD-{handle.upper()}"


def _minimum_cost_assignment(costs):
    """Rectangular Hungarian assignment; rows must not outnumber columns."""
    if not costs:
        return []
    row_count, column_count = len(costs), len(costs[0])
    if row_count > column_count or any(len(row) != column_count for row in costs):
        raise ValueError("Invalid rectangular assignment matrix")
    row_potential = [0] * (row_count + 1)
    column_potential = [0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    predecessor = [0] * (column_count + 1)
    for row_index in range(1, row_count + 1):
        matched_row[0] = row_index
        current_column = 0
        minimum = [math.inf] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[current_column] = True
            current_row = matched_row[current_column]
            delta, next_column = math.inf, 0
            for column_index in range(1, column_count + 1):
                if used[column_index]:
                    continue
                reduced = (
                    costs[current_row - 1][column_index - 1]
                    - row_potential[current_row]
                    - column_potential[column_index]
                )
                if reduced < minimum[column_index]:
                    minimum[column_index] = reduced
                    predecessor[column_index] = current_column
                if minimum[column_index] < delta:
                    delta, next_column = minimum[column_index], column_index
            for column_index in range(column_count + 1):
                if used[column_index]:
                    row_potential[matched_row[column_index]] += delta
                    column_potential[column_index] -= delta
                else:
                    minimum[column_index] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            next_column = predecessor[current_column]
            matched_row[current_column] = matched_row[next_column]
            current_column = next_column
            if current_column == 0:
                break
    assignment = [-1] * row_count
    for column_index in range(1, column_count + 1):
        if matched_row[column_index]:
            assignment[matched_row[column_index] - 1] = column_index - 1
    return assignment


def _assign_family_annotations(annotations, targets, tolerance):
    """Maximum-cardinality, minimum-distance one-to-one annotation matching."""
    annotations = sorted(annotations, key=lambda item: (item.text.casefold(), item.entity_key))
    targets = sorted(
        (target for target in targets if not target.display_label),
        key=lambda item: (item.source_handle, item.feature_key),
    )
    candidate_records, eligible, failures = [], [], []
    distances = {}
    for annotation in annotations:
        ranked = sorted(
            (math.dist(annotation.centroid, target.native_centroid), target.feature_key, target)
            for target in targets
        )
        within = [item for item in ranked if item[0] <= tolerance]
        for distance, _, target in within:
            distances[(annotation.entity_key, target.feature_key)] = distance
            candidate_records.append({
                "annotation_key": annotation.entity_key,
                "text": annotation.text.strip(),
                "target_key": target.feature_key,
                "target_handle": target.source_handle,
                "distance_native_m": distance,
                "selected": False,
                "status": "candidate",
            })
        if not within:
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "text": annotation.text, "status": "outside_tolerance",
            })
        elif len(within) > 1 and within[1][0] - within[0][0] <= 0.01:
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "text": annotation.text, "status": "multiple_optima",
            })
            for record in candidate_records:
                if record["annotation_key"] == annotation.entity_key:
                    record["status"] = "ambiguous"
        else:
            eligible.append(annotation)

    if not eligible:
        return [], failures, candidate_records
    scale = 1_000_000
    unmatched_penalty = (len(eligible) + 1) * (math.ceil(tolerance * scale) + 1)
    invalid_cost = (len(eligible) + 1) * unmatched_penalty
    costs = []
    for annotation in eligible:
        real = [
            (
                int(round(distances[(annotation.entity_key, target.feature_key)] * scale))
                if (annotation.entity_key, target.feature_key) in distances
                else invalid_cost
            )
            for target in targets
        ]
        costs.append(real + [unmatched_penalty] * len(eligible))
    column_assignment = _minimum_cost_assignment(costs)
    assignments = []
    selected_pairs = set()
    for row_index, column_index in enumerate(column_assignment):
        annotation = eligible[row_index]
        if column_index < len(targets) and costs[row_index][column_index] < invalid_cost:
            target = targets[column_index]
            distance = distances[(annotation.entity_key, target.feature_key)]
            assignments.append((annotation, target, distance))
            selected_pairs.add((annotation.entity_key, target.feature_key))
        else:
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "text": annotation.text, "status": "assignment_conflict",
            })
    for record in candidate_records:
        if (record["annotation_key"], record["target_key"]) in selected_pairs:
            record["selected"] = True
            record["status"] = "selected"
    return assignments, failures, candidate_records


def _field_rule_value(entity: SourceEntity, rule: dict):
    kind = rule["kind"]
    if kind == "constant":
        return rule.get("value")
    if kind == "entity-text":
        return entity.text.strip() or None
    if kind == "block-attribute-integer":
        value = entity.block_attributes.get(str(rule["attribute"]).upper())
        try:
            return int(value) if value not in {None, ""} else None
        except ValueError:
            return None
    if kind == "layer-regex-integer":
        match = re.search(str(rule["pattern"]), entity.layer)
        return int(match.group(int(rule["group"]))) if match else None
    if kind == "layer-keyword-map":
        layer = entity.layer.upper()
        for keyword, value in rule["mapping"].items():
            if str(keyword).upper() in layer:
                return value
        return None
    if kind == "layer-suffix":
        return rule.get("value") if entity.layer.upper().rstrip().endswith(str(rule["suffix"]).upper()) else None
    raise ValueError(f"Unsupported reviewed field rule kind: {kind}")


def _registry_attributes(entity, feature_class, registry):
    attributes, provenance = {}, {}
    for field_name, rule in registry.field_rules.get(feature_class, {}).items():
        value = _field_rule_value(entity, rule)
        if value is not None and value != "":
            attributes[field_name] = value
            provenance[field_name] = str(rule["provenance"])
    return attributes, provenance


def _registry_display_label(feature_class, attributes, registry):
    rule = registry.display_label_rules.get(feature_class)
    if not rule:
        return "", "UNAVAILABLE"
    if rule["kind"] == "attribute-field":
        value = attributes.get(str(rule["field"]))
    elif rule["kind"] == "attribute-format":
        required = [str(field) for field in rule.get("required_fields", ())]
        value = (
            str(rule["template"]).format_map(attributes)
            if all(field in attributes for field in required)
            else None
        )
    else:
        raise ValueError(f"Unsupported reviewed display-label rule kind: {rule['kind']}")
    return (str(value), str(rule["provenance"])) if value not in {None, ""} else ("", "UNAVAILABLE")


def classify_entities(entities: list[SourceEntity], registry: MappingRegistry):
    features: list[Feature] = []
    unresolved: list[dict] = []
    mapped_entities: set[str] = set()
    reverse_blocks = {
        block_name: feature_class
        for feature_class, names in registry.block_families.items()
        for block_name in names
    }
    route_pattern = re.compile(registry.positive_route_layer_regex)
    home_layers = set(registry.layers["homepass"])
    model_entities = [entity for entity in entities if entity.cad_role == "model"]

    for entity in model_entities:
        feature_class = None
        geometry_kind = "Point"
        geometry_role = "SOURCE_ASSET"
        if entity.dwg_type == "INSERT":
            feature_class = reverse_blocks.get(entity.block_name.upper())
        elif entity.dwg_type in {"LWPOLYLINE", "POLYLINE"} and route_pattern.search(entity.layer):
            feature_class, geometry_kind, geometry_role = "CABLE", "LineString", "SOURCE_ROUTE"
        elif entity.dwg_type in {"TEXT", "MTEXT"} and entity.layer.upper() in home_layers:
            feature_class, geometry_role = "IMB", "SOURCE_HOME_LABEL"
        if not feature_class or not entity.points:
            continue

        attributes = {"CODE": _generated_code(feature_class, entity.handle)}
        provenance = {"CODE": "DWG_DERIVED:stable-handle-id"}
        reviewed_attributes, reviewed_provenance = _registry_attributes(
            entity, feature_class, registry,
        )
        attributes.update(reviewed_attributes)
        provenance.update(reviewed_provenance)
        display_label, label_provenance = _registry_display_label(
            feature_class, attributes, registry,
        )

        features.append(Feature(
            feature_key=_feature_key(entity, feature_class),
            feature_class=feature_class,
            geometry_kind=geometry_kind,
            native_points=list(entity.points),
            source_entity_key=entity.entity_key,
            source_handle=entity.handle,
            source_layer=entity.layer,
            geometry_role=geometry_role,
            style=entity.style,
            attributes={key: value for key, value in attributes.items() if value is not None},
            display_label=display_label,
            label_provenance=label_provenance,
            field_provenance=provenance,
            lineage=[{"operation": "identity", "source_entity_key": entity.entity_key, "max_displacement_m": 0.0}],
        ))
        mapped_entities.add(entity.entity_key)

    relations: list[Relation] = []
    by_class = defaultdict(list)
    for feature in features:
        by_class[feature.feature_class].append(feature)
    label_specs = [
        (re.compile(registry.labels["fat"]), "BOITE"),
        (re.compile(registry.labels["pole"]), "PTECH"),
    ]
    annotations_by_class = defaultdict(list)
    for entity in model_entities:
        if entity.dwg_type not in {"TEXT", "MTEXT"} or not entity.text.strip():
            continue
        target_class = next((feature_class for pattern, feature_class in label_specs if pattern.fullmatch(entity.text.strip())), None)
        if target_class is None:
            continue
        annotations_by_class[target_class].append(entity)

    annotation_candidates, annotation_assignments = [], {}
    annotation_rule = registry.decision_rules["annotation_assignment"]
    tolerance = registry.thresholds["annotation_to_asset"]
    for target_class, annotations in sorted(annotations_by_class.items()):
        assignments, failures, candidates = _assign_family_annotations(
            annotations, by_class[target_class], tolerance,
        )
        for item in failures:
            unresolved.append({**item, "target_class": target_class})
        for candidate in candidates:
            annotation_candidates.append({
                **candidate, "target_class": target_class,
                "rule_id": annotation_rule["rule_id"],
            })
        for entity, target, distance in assignments:
            target.attributes["CODE"] = entity.text.strip()
            target.field_provenance["CODE"] = annotation_rule["provenance"]
            target.display_label = entity.text.strip()
            target.label_provenance = annotation_rule["provenance"]
            relation_key = hashlib.sha256(
                f"{entity.entity_key}|labels|{target.feature_key}".encode()
            ).hexdigest()
            relations.append(Relation(
                relation_key=relation_key, relation_kind="labels",
                source_key=entity.entity_key, target_key=target.feature_key,
                status="accepted",
                method=f"{annotation_rule['rule_id']}:{annotation_rule['method']}",
                distance_native_m=distance,
                evidence_keys=(entity.entity_key, target.source_entity_key),
            ))
            mapped_entities.add(entity.entity_key)
        annotation_assignments[target_class] = {
            "source_annotations": len(annotations),
            "assigned": len(assignments),
            "unresolved": len(failures),
            "total_distance_native_m": sum(item[2] for item in assignments),
        }

    diagnostics = {
        "candidate_counts": {
            feature_class: len(items) for feature_class, items in sorted(by_class.items())
        },
        "mapped_entity_keys": sorted(mapped_entities),
        "annotation_assignments": annotation_assignments,
        "annotation_candidates": annotation_candidates,
    }
    return features, relations, unresolved, diagnostics
