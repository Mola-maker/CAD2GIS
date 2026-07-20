"""Reviewed APD semantic candidates and evidence-bound annotation linking."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from fnmatch import fnmatchcase
from typing import Any, Iterable, Mapping, Sequence

from .config import MappingRegistry
from .model import Feature, Relation, SourceEntity


_ANNOTATION_CARRIER_TYPES = frozenset({
    "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "MLEADER", "MULTILEADER",
    "TABLE", "TABLE_CELL",
})

SEMANTIC_COVERAGE_SCHEMA_VERSION = "cad2gis-semantic-coverage-v1"
OBSERVABILITY_POLICIES = frozenset({"warn", "abstain", "fail"})
_ROUTE_ENTITY_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE"})
_ALLOWLIST_FIELDS = frozenset({
    "reason", "candidate_class", "source_layer", "dwg_type", "block_name",
})


class CoverageGateError(RuntimeError):
    """Fail-closed coverage error carrying the deterministic audit payload."""

    def __init__(self, domain: str, coverage: Mapping[str, Any]):
        self.domain = domain
        self.coverage = dict(coverage)
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in self.coverage.get("by_reason", {}).items()
        ) or "none"
        super().__init__(
            f"{domain} coverage gate failed: {reasons}; inspect coverage records"
        )


def normalize_observability_policy(policy: str | None, *, default: str) -> str:
    """Validate a coverage policy without guessing a permissive fallback."""
    selected = default if policy is None else str(policy).strip().casefold()
    if selected not in OBSERVABILITY_POLICIES:
        raise ValueError(
            "coverage policy must be one of warn, abstain, fail; "
            f"got {policy!r}"
        )
    return selected


def _normalize_allowlist(
    allowlist: Sequence[str | Mapping[str, Any]] | None,
) -> tuple[dict[str, str], ...]:
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(allowlist or ()):
        if isinstance(item, str):
            rule = {"reason": item}
        elif isinstance(item, Mapping):
            unknown = set(item) - _ALLOWLIST_FIELDS
            if unknown:
                raise ValueError(
                    f"coverage allowlist[{index}] has unknown keys: {sorted(unknown)}"
                )
            rule = {
                str(key): str(value)
                for key, value in item.items()
                if str(value).strip()
            }
        else:
            raise ValueError(
                f"coverage allowlist[{index}] must be a reason string or object"
            )
        if not rule.get("reason", "").strip():
            raise ValueError(
                f"coverage allowlist[{index}] requires a non-empty reason"
            )
        normalized.append(rule)
    return tuple(normalized)


def _matches_allowlist(record: Mapping[str, Any], rules: Iterable[Mapping[str, str]]) -> bool:
    for rule in rules:
        if all(
            fnmatchcase(
                str(record.get(field, "")).casefold(), pattern.casefold(),
            )
            for field, pattern in rule.items()
        ):
            return True
    return False


def build_coverage_report(
    records: Iterable[Mapping[str, Any]],
    *,
    schema_version: str,
    policy: str,
    allowlist: Sequence[str | Mapping[str, Any]] | None = None,
    inspected_count: int | None = None,
) -> dict[str, Any]:
    """Build the shared deterministic semantic/style coverage contract.

    Allowlisting is explicit and field-scoped.  Patterns use shell wildcards,
    are matched case-insensitively, and never change classification or style;
    they only acknowledge a reviewed unsupported case.
    """
    selected_policy = normalize_observability_policy(policy, default="fail")
    rules = _normalize_allowlist(allowlist)
    normalized_records: list[dict[str, Any]] = []
    for raw_record in records:
        record = {
            "source_entity_key": str(raw_record.get("source_entity_key", "")),
            "reason": str(raw_record.get("reason", "")),
            "candidate_class": str(raw_record.get("candidate_class", "")),
            "source_layer": str(raw_record.get("source_layer", "")),
            "dwg_type": str(raw_record.get("dwg_type", "")),
            **{
                str(key): value for key, value in raw_record.items()
                if key not in {
                    "source_entity_key", "reason", "candidate_class",
                    "source_layer", "dwg_type", "action", "allowlisted",
                }
            },
        }
        if not record["reason"]:
            raise ValueError("coverage record reason must be non-empty")
        is_allowlisted = _matches_allowlist(record, rules)
        record["allowlisted"] = is_allowlisted
        record["action"] = "allowlist" if is_allowlisted else selected_policy
        normalized_records.append(record)
    normalized_records.sort(key=lambda item: (
        item["reason"], item["source_layer"].casefold(), item["dwg_type"],
        item["source_entity_key"], item.get("candidate_class", ""),
    ))
    by_reason: dict[str, int] = {}
    for record in normalized_records:
        reason = record["reason"]
        by_reason[reason] = by_reason.get(reason, 0) + 1
    non_allowlisted = sum(not item["allowlisted"] for item in normalized_records)
    failed = sum(item["action"] == "fail" for item in normalized_records)
    abstained = sum(item["action"] == "abstain" for item in normalized_records)
    warned = sum(item["action"] == "warn" for item in normalized_records)
    status = "FAIL" if failed else "WATCH" if non_allowlisted else "PASS"
    counts = {
        "records": len(normalized_records),
        "allowlisted": len(normalized_records) - non_allowlisted,
        "non_allowlisted": non_allowlisted,
        "warned": warned,
        "abstained": abstained,
        "failed": failed,
    }
    if inspected_count is not None:
        counts["inspected"] = int(inspected_count)
    return {
        "schema_version": schema_version,
        "policy": selected_policy,
        "status": status,
        "passed": status == "PASS",
        "conversion_allowed": failed == 0,
        "counts": counts,
        "by_reason": dict(sorted(by_reason.items())),
        "records": normalized_records,
    }


def _coverage_record(
    entity: SourceEntity,
    reason: str,
    candidate_class: str = "",
    **detail: Any,
) -> dict[str, Any]:
    return {
        "source_entity_key": entity.entity_key,
        "reason": reason,
        "candidate_class": candidate_class,
        "source_layer": entity.layer,
        "dwg_type": entity.dwg_type,
        "source_handle": entity.handle,
        "block_name": entity.block_name,
        **detail,
    }


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


def _assign_family_annotations(
    annotations, targets, tolerance, *, family_id="", require_same_layer=False,
):
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
            if not require_same_layer
            or annotation.layer.strip().casefold() == target.source_layer.strip().casefold()
        )
        within = [item for item in ranked if item[0] <= tolerance]
        for distance, _, target in within:
            distances[(annotation.entity_key, target.feature_key)] = distance
            candidate_records.append({
                "annotation_key": annotation.entity_key,
                "family_id": family_id,
                "text": annotation.text.strip(),
                "source_layer": annotation.layer,
                "target_key": target.feature_key,
                "target_handle": target.source_handle,
                "target_layer": target.source_layer,
                "distance_native_m": distance,
                "selected": False,
                "status": "candidate",
            })
        if not within:
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "family_id": family_id, "text": annotation.text,
                "source_layer": annotation.layer,
                "status": "outside_tolerance",
            })
        elif len(within) > 1 and within[1][0] - within[0][0] <= 0.01:
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "family_id": family_id, "text": annotation.text,
                "source_layer": annotation.layer,
                "status": "multiple_optima",
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
                "family_id": family_id, "text": annotation.text,
                "source_layer": annotation.layer,
                "status": "assignment_conflict",
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
    for field_name, rule in getattr(registry, "field_rules", {}).get(feature_class, {}).items():
        value = _field_rule_value(entity, rule)
        if value is not None and value != "":
            attributes[field_name] = value
            provenance[field_name] = str(rule["provenance"])
    return attributes, provenance


def _registry_display_label(feature_class, attributes, registry):
    rule = getattr(registry, "display_label_rules", {}).get(feature_class)
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


def classify_entities(
    entities: list[SourceEntity],
    registry: MappingRegistry,
    *,
    coverage_policy: str | None = None,
    coverage_allowlist: Sequence[str | Mapping[str, Any]] | None = None,
):
    """Classify only reviewed semantic mappings and account for every abstention.

    ``coverage_policy`` and ``coverage_allowlist`` are stage-boundary inputs so
    a reviewed project profile can make its exceptions explicit.  A new/draft
    registry defaults to ``fail``.  Existing reviewed v1 registries default to
    ``warn`` for API compatibility, but production callers should always pass
    their reviewed policy explicitly.
    """
    features: list[Feature] = []
    unresolved: list[dict] = []
    coverage_records: list[dict[str, Any]] = []
    mapped_entities: set[str] = set()
    reverse_blocks = {
        block_name: feature_class
        for feature_class, names in getattr(registry, "block_families", {}).items()
        for block_name in names
    }
    route_regex = str(getattr(registry, "positive_route_layer_regex", "") or "")
    route_pattern = re.compile(route_regex) if route_regex else None
    home_layers = set(getattr(registry, "layers", {}).get("homepass", ()))
    model_entities = [entity for entity in entities if entity.cad_role == "model"]
    entity_by_key = {entity.entity_key: entity for entity in model_entities}

    for entity in model_entities:
        feature_class = None
        geometry_kind = "Point"
        geometry_role = "SOURCE_ASSET"
        dwg_type = entity.dwg_type.upper()
        if dwg_type == "INSERT":
            feature_class = reverse_blocks.get(entity.block_name.upper())
            if feature_class is None:
                coverage_records.append(_coverage_record(
                    entity, "unknown_insert_block", "UNMAPPED_INSERT",
                ))
        elif (
            dwg_type in _ROUTE_ENTITY_TYPES
            and route_pattern is not None
            and route_pattern.search(entity.layer)
        ):
            feature_class, geometry_kind, geometry_role = "CABLE", "LineString", "SOURCE_ROUTE"
        elif dwg_type in _ROUTE_ENTITY_TYPES:
            coverage_records.append(_coverage_record(
                entity, "unmatched_route_layer", "CABLE",
            ))
        elif (
            dwg_type in _ANNOTATION_CARRIER_TYPES
            and entity.layer.upper() in home_layers
        ):
            feature_class, geometry_role = "IMB", "SOURCE_HOME_LABEL"
        if not entity.points:
            coverage_records.append(_coverage_record(
                entity, "missing_geometry_points", feature_class or "UNMAPPED",
            ))
            continue
        if feature_class == "CABLE" and len(entity.points) < 2:
            coverage_records.append(_coverage_record(
                entity, "invalid_geometry_cardinality", "CABLE",
                point_count=len(entity.points),
            ))
            continue
        if not feature_class:
            continue

        attributes = {"CODE": _generated_code(feature_class, entity.handle)}
        provenance = {"CODE": "DWG_DERIVED:stable-handle-id"}
        reviewed_attributes, reviewed_provenance = _registry_attributes(
            entity, feature_class, registry,
        )
        attributes.update(reviewed_attributes)
        provenance.update(reviewed_provenance)
        if feature_class == "CABLE" and entity.native_length is not None:
            attributes["source_autocad_native_length_m"] = float(entity.native_length)
            provenance["source_autocad_native_length_m"] = (
                "DWG_DIRECT:AutoCAD-curve-distance"
            )
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
    annotation_families = tuple(getattr(registry, "annotation_families", ()))
    compiled_families = [
        (
            family,
            re.compile(family.text_pattern),
            re.compile(family.source_layer_pattern),
            re.compile(family.target_layer_pattern),
        )
        for family in annotation_families
    ]
    annotations_by_family = defaultdict(list)
    annotation_discovery_failures = []
    label_rules = getattr(registry, "labels", {})
    suspected_pattern = str(label_rules.get("suspected_asset_id", "") or "")
    site_pattern = str(label_rules.get("site", "") or "")
    suspected_asset_id = re.compile(suspected_pattern) if suspected_pattern else None
    known_non_assignment_labels = [re.compile(site_pattern)] if site_pattern else []
    for entity in model_entities:
        if entity.dwg_type not in _ANNOTATION_CARRIER_TYPES or not entity.text.strip():
            continue
        text = entity.text.strip()
        text_matches = [
            family
            for family, text_pattern, _, _ in compiled_families
            if text_pattern.fullmatch(text)
        ]
        exact_matches = [
            family
            for family, text_pattern, source_layer_pattern, _ in compiled_families
            if text_pattern.fullmatch(text) and source_layer_pattern.fullmatch(entity.layer.strip())
        ]
        if len(exact_matches) == 1:
            annotations_by_family[exact_matches[0].family_id].append(entity)
            continue
        if len(exact_matches) > 1:
            annotation_discovery_failures.append({
                "kind": "annotation",
                "entity_key": entity.entity_key,
                "family_id": "|".join(sorted(family.family_id for family in exact_matches)),
                "text": text,
                "source_layer": entity.layer,
                "status": "multiple_annotation_families",
            })
        elif text_matches:
            annotation_discovery_failures.append({
                "kind": "annotation",
                "entity_key": entity.entity_key,
                "family_id": "|".join(sorted(family.family_id for family in text_matches)),
                "text": text,
                "source_layer": entity.layer,
                "status": "source_layer_mismatch",
            })
        elif suspected_asset_id is not None and suspected_asset_id.fullmatch(text) and not any(
            pattern.fullmatch(text) for pattern in known_non_assignment_labels
        ):
            annotation_discovery_failures.append({
                "kind": "annotation",
                "entity_key": entity.entity_key,
                "family_id": "UNRECOGNIZED",
                "text": text,
                "source_layer": entity.layer,
                "status": "unrecognized_asset_id",
            })
        elif not compiled_families and suspected_asset_id is None:
            coverage_records.append(_coverage_record(
                entity, "unreviewed_annotation_carrier", "LABEL",
                text=text,
            ))

    annotation_candidates = []
    annotation_assignments_by_family = {}
    annotation_assignments = defaultdict(lambda: {
        "source_annotations": 0,
        "assigned": 0,
        "missing": 0,
        "unresolved": 0,
        "cross_layer_assignments": 0,
        "total_distance_native_m": 0.0,
    })
    annotation_rule = (
        getattr(registry, "decision_rules", {}).get("annotation_assignment")
        if compiled_families else None
    )
    if compiled_families and not annotation_rule:
        raise ValueError(
            "annotation_assignment decision rule is required when annotation_families are configured"
        )
    target_memberships = defaultdict(list)
    for family, _, _, target_layer_pattern in compiled_families:
        for target in by_class[family.target_class]:
            if target_layer_pattern.fullmatch(target.source_layer.strip()):
                target_memberships[target.feature_key].append(family.family_id)
    overlapping_target_keys = {
        target_key
        for target_key, family_ids in target_memberships.items()
        if len(family_ids) > 1
    }
    for target_key in sorted(overlapping_target_keys):
        target = next(
            feature for feature in features if feature.feature_key == target_key
        )
        annotation_discovery_failures.append({
            "kind": "annotation",
            "entity_key": target.source_entity_key,
            "family_id": "|".join(sorted(target_memberships[target_key])),
            "text": "",
            "source_layer": target.source_layer,
            "target_key": target_key,
            "status": "target_in_multiple_annotation_families",
        })
    unresolved.extend(annotation_discovery_failures)
    for failure in annotation_discovery_failures:
        source_entity = entity_by_key.get(failure.get("entity_key"))
        if source_entity is not None:
            coverage_records.append(_coverage_record(
                source_entity,
                f"annotation_{failure['status']}",
                str(failure.get("target_class", "LABEL")),
                family_id=str(failure.get("family_id", "")),
                text=str(failure.get("text", "")),
            ))
    for family, _, _, target_layer_pattern in compiled_families:
        annotations = annotations_by_family[family.family_id]
        family_targets = [
            target
            for target in by_class[family.target_class]
            if target_layer_pattern.fullmatch(target.source_layer.strip())
            and target.feature_key not in overlapping_target_keys
        ]
        assignments, failures, candidates = _assign_family_annotations(
            annotations,
            family_targets,
            family.max_distance_native_m,
            family_id=family.family_id,
            require_same_layer=family.require_same_layer,
        )
        for item in failures:
            unresolved.append({**item, "target_class": family.target_class})
            source_entity = entity_by_key.get(item.get("entity_key"))
            if source_entity is not None:
                coverage_records.append(_coverage_record(
                    source_entity,
                    f"annotation_{item['status']}",
                    family.target_class,
                    family_id=family.family_id,
                    text=str(item.get("text", "")),
                ))
        for candidate in candidates:
            assignment_provenance = (
                f"{family.provenance}|RULE:{annotation_rule['rule_id']}"
            )
            annotation_candidates.append({
                **candidate,
                "target_class": family.target_class,
                "rule_id": family.rule_id,
                "provenance": assignment_provenance,
            })
        for entity, target, distance in assignments:
            assignment_provenance = (
                f"{family.provenance}|RULE:{annotation_rule['rule_id']}"
            )
            target.attributes["CODE"] = entity.text.strip()
            target.field_provenance["CODE"] = assignment_provenance
            target.display_label = entity.text.strip()
            target.label_provenance = assignment_provenance
            relation_key = hashlib.sha256(
                f"{entity.entity_key}|labels|{target.feature_key}".encode()
            ).hexdigest()
            relations.append(Relation(
                relation_key=relation_key, relation_kind="labels",
                source_key=entity.entity_key, target_key=target.feature_key,
                status="accepted",
                method=(
                    f"{family.family_id}:{family.rule_id}:"
                    f"{annotation_rule['rule_id']}:{annotation_rule['method']}"
                ),
                distance_native_m=distance,
                evidence_keys=(entity.entity_key, target.source_entity_key),
            ))
            mapped_entities.add(entity.entity_key)
        cross_layer_assignments = sum(
            entity.layer.strip().casefold() != target.source_layer.strip().casefold()
            for entity, target, _ in assignments
        )
        family_diagnostics = {
            "target_class": family.target_class,
            "target_assets": len(family_targets),
            "source_annotations": len(annotations),
            "assigned": len(assignments),
            "missing": len(annotations) - len(assignments),
            "unresolved": len(failures),
            "cross_layer_assignments": cross_layer_assignments,
            "total_distance_native_m": sum(item[2] for item in assignments),
            "max_distance_native_m": family.max_distance_native_m,
            "require_same_layer": family.require_same_layer,
        }
        annotation_assignments_by_family[family.family_id] = family_diagnostics
        aggregate = annotation_assignments[family.target_class]
        for key in (
            "source_annotations", "assigned", "missing", "unresolved",
            "cross_layer_assignments", "total_distance_native_m",
        ):
            aggregate[key] += family_diagnostics[key]

    feature_by_key = {feature.feature_key: feature for feature in features}
    for target_key, family_ids in sorted(target_memberships.items()):
        target = feature_by_key[target_key]
        if not target.display_label:
            source_entity = entity_by_key.get(target.source_entity_key)
            if source_entity is not None:
                coverage_records.append(_coverage_record(
                    source_entity, "missing_reviewed_label", target.feature_class,
                    annotation_families=sorted(family_ids),
                ))

    selected_policy = normalize_observability_policy(
        coverage_policy,
        default="warn" if bool(getattr(registry, "is_reviewed", False)) else "fail",
    )
    coverage = build_coverage_report(
        coverage_records,
        schema_version=SEMANTIC_COVERAGE_SCHEMA_VERSION,
        policy=selected_policy,
        allowlist=coverage_allowlist,
        inspected_count=len(model_entities),
    )

    diagnostics = {
        "candidate_counts": {
            feature_class: len(items) for feature_class, items in sorted(by_class.items())
        },
        "mapped_entity_keys": sorted(mapped_entities),
        "annotation_assignments": {
            target_class: dict(values)
            for target_class, values in sorted(annotation_assignments.items())
        },
        "annotation_assignments_by_family": annotation_assignments_by_family,
        "unrecognized_suspected_asset_ids": sum(
            item["status"] == "unrecognized_asset_id"
            for item in annotation_discovery_failures
        ),
        "annotation_discovery_failure_counts": dict(sorted(
            (status, sum(item["status"] == status for item in annotation_discovery_failures))
            for status in {item["status"] for item in annotation_discovery_failures}
        )),
        "annotation_candidates": annotation_candidates,
        "coverage": coverage,
    }
    if not coverage["conversion_allowed"]:
        raise CoverageGateError("semantics", coverage)
    return features, relations, unresolved, diagnostics
