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
from .spatial_filter import is_placeholder_text, is_pole_identifier_shape


_ANNOTATION_CARRIER_TYPES = frozenset({
    "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "MLEADER", "MULTILEADER",
    "TABLE", "TABLE_CELL",
})

SEMANTIC_COVERAGE_SCHEMA_VERSION = "cad2gis-semantic-coverage-v1"
OBSERVABILITY_POLICIES = frozenset({"warn", "abstain", "fail"})
_ROUTE_ENTITY_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "POLYLINE_2D", "POLYLINE_3D"})


def _is_placeholder_block(entity: SourceEntity) -> bool:
    """Annotation/info-card INSERTs carry only documentation placeholders.

    Real deployed devices (FAT cabinets, FDT closures) carry an effective
    attribute value (e.g. F=4, F=7, F=1 — a numeric capacity/sequence);
    info cards carry D/F/L documentation fields whose values are
    single-character placeholders ("-", "X", "K", "C", ...).  The check is
    value-shape based (numeric vs placeholder), never layer/block-name
    hardcoded.
    """
    attributes = getattr(entity, "block_attributes", {}) or {}
    if not attributes:
        return True
    return all(
        _is_placeholder_attribute_value(value)
        for value in attributes.values()
    )


def _is_placeholder_attribute_value(value: Any) -> bool:
    """A real device attribute carries a non-zero numeric value; everything
    else ("-", "X", "C", "0", free-form docs) is a documentation placeholder."""
    text = str(value).strip()
    if not text:
        return True
    return not (text.isdigit() and int(text) != 0)


def _is_polygon_ring(points: Sequence[tuple[float, float]]) -> bool:
    """ZPM boundary polylines are closed into polygons at the warehouse.

    Reviewed zpm_boundary layers carry zone outlines drawn as LWPOLYLINEs;
    the warehouse materialiser closes the ring regardless of the DWG closed
    flag, matching the reviewed reference converter (all ≥3-point outlines
    on the boundary layer become polygons).
    """
    return len(points) >= 3
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


_GENERATED_CODE_PROVENANCE = "DWG_DERIVED:stable-handle-id"


def _annotation_target_eligible(feature: Feature) -> bool:
    """A target may receive a DWG text label only when its current label is
    not semantic evidence.

    ``classify_entities`` always pre-populates ``CODE`` with a stable
    handle-derived fallback (``PTECH-CAD-1A2B3C``) so targets would otherwise
    look permanently labelled and the annotation matcher would skip every
    real DWG text.  Eligibility is provenance-based, not label-content based:
    UNAVAILABLE and stable-handle-derived labels are replaceable; any label
    already carried by a DWG text/attribute/decision-pack assignment is not.
    """
    if feature.label_provenance == "UNAVAILABLE":
        return True
    code_provenance = str(feature.field_provenance.get("CODE", "") or "")
    return code_provenance == _GENERATED_CODE_PROVENANCE


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
        (target for target in targets if _annotation_target_eligible(target)),
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
    reverse_insert_layers = {
        layer_name: feature_class
        for feature_class, names in getattr(
            registry, "insert_layer_families", {}
        ).items()
        for layer_name in names
    }
    route_regex = str(getattr(registry, "positive_route_layer_regex", "") or "")
    route_pattern = re.compile(route_regex) if route_regex else None
    home_layers = set(getattr(registry, "layers", {}).get("homepass", ()))
    zpm_layers = set(getattr(registry, "layers", {}).get("zpm_boundary", ()))
    sling_layers = set(getattr(registry, "layers", {}).get("sling_wire", ()))
    model_entities = [entity for entity in entities if entity.cad_role == "model"]
    entity_by_key = {entity.entity_key: entity for entity in model_entities}

    for entity in model_entities:
        feature_class = None
        geometry_kind = "Point"
        geometry_role = "SOURCE_ASSET"
        dwg_type = entity.dwg_type.upper()
        if dwg_type == "INSERT":
            feature_class = (
                reverse_blocks.get(entity.block_name.upper())
                or reverse_insert_layers.get(entity.layer.upper())
            )
            if feature_class is None:
                coverage_records.append(_coverage_record(
                    entity, "unknown_insert_block", "UNMAPPED_INSERT",
                ))
            elif feature_class in {"BOITE", "SITE"} and _is_placeholder_block(entity):
                # Annotation/info-card blocks carry only documentation
                # placeholders (D/F/L = "-"/"X"/"K"/...), never a real
                # deployed device; they must not reach delivery.
                coverage_records.append(_coverage_record(
                    entity, "placeholder_annotation_block", feature_class,
                    block_name=entity.block_name,
                ))
                feature_class = None
        elif (
            dwg_type in _ROUTE_ENTITY_TYPES
            and entity.layer.upper() in zpm_layers
            and _is_polygon_ring(entity.points)
        ):
            feature_class, geometry_kind, geometry_role = "ZPM", "Polygon", "SOURCE_BOUNDARY"
        elif (
            dwg_type in _ROUTE_ENTITY_TYPES
            and route_pattern is not None
            and route_pattern.search(entity.layer)
        ):
            feature_class, geometry_kind, geometry_role = "CABLE", "LineString", "SOURCE_ROUTE"
        elif (
            dwg_type in _ROUTE_ENTITY_TYPES
            and entity.layer.upper() in sling_layers
        ):
            # SLING WIRE carries the aerial cable route (reviewed with the
            # green/purple FO core layers); every sling span is one CABLE.
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
    unclaimed_pole_annotations: list[SourceEntity] = []
    annotation_discovery_failures = []
    label_rules = getattr(registry, "labels", {})
    suspected_pattern = str(label_rules.get("suspected_asset_id", "") or "")
    site_pattern = str(label_rules.get("site", "") or "")
    suspected_asset_id = re.compile(suspected_pattern) if suspected_pattern else None
    known_non_assignment_labels = [re.compile(site_pattern)] if site_pattern else []
    # Reviewed semantic fallback for pole identifiers: text follows the
    # reviewed annotation-family patterns (target_class=PTECH) and the
    # layer carries POLE semantics.  It activates only for annotations and
    # targets that no reviewed exact family claimed, so reviewed families
    # keep priority.  Patterns come from the registry — never hardcoded.
    POLE_LABEL_FAMILY_ID = "pole_label_semantic"
    reviewed_pole_patterns = [
        re.compile(family.text_pattern)
        for family in annotation_families
        if family.target_class == "PTECH"
    ]
    _pole_bodies = []
    for _pattern in reviewed_pole_patterns:
        _body = _pattern.pattern
        # Hoist a leading (?i) global flag to the joined expression.
        while _body.startswith("(?i)"):
            _body = _body[4:]
        _pole_bodies.append(_body)
    pole_label_text = (
        re.compile("(?i)(?:" + "|".join(_pole_bodies) + ")")
        if _pole_bodies
        else re.compile(r"(?!)")
    )
    pole_label_layer = re.compile(r"(?i)POLE")
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
            if text_pattern.fullmatch(text)
            and source_layer_pattern.fullmatch(entity.layer.strip())
            and (
                family.aci_color is None
                or family.aci_color == entity.style.aci_color
            )
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
        elif (
            not text_matches
            and pole_label_text.fullmatch(text)
            and pole_label_layer.search(entity.layer)
        ):
            # Reviewed family did not claim this pole label (e.g. its
            # source layer was named after a legend sample).  The semantic
            # fallback carries the same reviewed assignment contract.
            annotations_by_family[POLE_LABEL_FAMILY_ID].append(entity)
            continue
        elif (
            not text_matches
            and pole_label_layer.search(entity.layer)
            and is_pole_identifier_shape(text)
            and not is_placeholder_text(text)
        ):
            # The reviewed registry families miss this project's actual
            # POLE-semantic label shape (observed on lamteh POLE ID and
            # kletek EXT POLE).  Claim it for the same semantic pole-label
            # fallback instead of silently leaving the PTECH CODE as a
            # generated handle.
            unclaimed_pole_annotations.append(entity)
            continue
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
    # Target membership follows the family's effective layer semantics.  A
    # reviewed require_same_layer family owns only targets on its source
    # layer; a permissive "(?i).+" target pattern must not claim every
    # target of the class (which would overlap-exclude them all).
    target_memberships = defaultdict(list)
    for family, _, source_layer_pattern, target_layer_pattern in compiled_families:
        for target in by_class[family.target_class]:
            target_layer = target.source_layer.strip()
            if family.require_same_layer:
                matched = bool(source_layer_pattern.fullmatch(target_layer))
            else:
                matched = bool(target_layer_pattern.fullmatch(target_layer))
            if matched:
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
    fallback_annotations = annotations_by_family.pop(POLE_LABEL_FAMILY_ID, [])
    fallback_annotations.extend(unclaimed_pole_annotations)
    fallback_targets: list[Feature] = []
    fallback_failure_annotations: list[SourceEntity] = []
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
            if (
                source_entity is not None
                and pole_label_text.fullmatch(str(item.get("text", "")).strip())
                and pole_label_layer.search(str(source_entity.layer))
            ):
                fallback_failure_annotations.append(source_entity)
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

    # Semantic pole-label fallback: assign POLE-semantic labels to PTECH
    # targets that no reviewed family labelled (permissive same-layer match).
    if fallback_annotations or fallback_failure_annotations:
        _seen_fallback: set[str] = set()
        _merged_fallback: list[SourceEntity] = []
        for _entity in fallback_annotations + fallback_failure_annotations:
            if _entity.entity_key in _seen_fallback:
                continue
            _seen_fallback.add(_entity.entity_key)
            _merged_fallback.append(_entity)
        fallback_annotations = _merged_fallback
        family_targets = [
            target
            for target in by_class["PTECH"]
            if _annotation_target_eligible(target)
        ]
        if family_targets:
            fallback_rule = (
                getattr(registry, "decision_rules", {}).get("annotation_assignment")
                or {"rule_id": "SEMANTIC-POLE-LABEL-001", "method": "global-minimum-cost-family-assignment"}
            )
            assignments, failures, candidates = _assign_family_annotations(
                fallback_annotations,
                family_targets,
                30.0,
                family_id=POLE_LABEL_FAMILY_ID,
                require_same_layer=False,
            )
            for item in failures:
                unresolved.append({**item, "target_class": "PTECH"})
                source_entity = entity_by_key.get(item.get("entity_key"))
                if source_entity is not None:
                    coverage_records.append(_coverage_record(
                        source_entity,
                        f"annotation_{item['status']}",
                        "PTECH",
                        family_id=POLE_LABEL_FAMILY_ID,
                        text=str(item.get("text", "")),
                    ))
            for candidate in candidates:
                annotation_candidates.append({
                    **candidate,
                    "target_class": "PTECH",
                    "rule_id": "SEMANTIC-POLE-LABEL-001",
                    "provenance": (
                        "DWG_DERIVED:pole-label-semantic|"
                        f"RULE:{fallback_rule['rule_id']}"
                    ),
                })
            for entity, target, distance in assignments:
                assignment_provenance = (
                    "DWG_DERIVED:pole-label-semantic|"
                    f"RULE:{fallback_rule['rule_id']}"
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
                        f"{POLE_LABEL_FAMILY_ID}:SEMANTIC-POLE-LABEL-001:"
                        f"{fallback_rule['rule_id']}:{fallback_rule['method']}"
                    ),
                    distance_native_m=distance,
                    evidence_keys=(entity.entity_key, target.source_entity_key),
                ))
                mapped_entities.add(entity.entity_key)
            annotation_assignments_by_family[POLE_LABEL_FAMILY_ID] = {
                "target_class": "PTECH",
                "target_assets": len(family_targets),
                "source_annotations": len(fallback_annotations),
                "assigned": len(assignments),
                "missing": len(fallback_annotations) - len(assignments),
                "unresolved": len(failures),
                "cross_layer_assignments": sum(
                    entity.layer.strip().casefold() != target.source_layer.strip().casefold()
                    for entity, target, _ in assignments
                ),
                "total_distance_native_m": sum(item[2] for item in assignments),
                "max_distance_native_m": 15.0,
                "require_same_layer": False,
            }
            aggregate = annotation_assignments["PTECH"]
            for key in (
                "source_annotations", "assigned", "missing", "unresolved",
                "cross_layer_assignments", "total_distance_native_m",
            ):
                aggregate[key] += annotation_assignments_by_family[POLE_LABEL_FAMILY_ID][key]

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

    # Legend core detection: a drawing legend lays one sample per cable
    # type on the sheet edge — the sample cables repeat the same length
    # (each type drawn at the standard stub length), cluster in one column
    # (x-same-line), and span several distinct layers.  The core region
    # around those samples (points and lines alike) is legend material and
    # must not reach delivery or inflate the calibration coverage envelope.
    # Pure geometry/frequency — no layer names, labels, or coordinates are
    # hardcoded.
    _LEGEND_GROUP_MIN_MEMBERS = 3
    _LEGEND_GROUP_X_SPAN_M = 200.0
    _LEGEND_GROUP_Y_SPAN_M = 500.0
    _LEGEND_GROUP_MIN_LAYERS = 3
    _LEGEND_CORE_PADDING_M = 100.0
    # Density-connected legend core: the padding box around the sample
    # cables can also cover real deployments drawn close to the legend
    # column.  Only entities connected to a sample by a chain of
    # < _LEGEND_CLUSTER_EPS_M steps are legend material; isolated real
    # infrastructure (poles, ZPM boundaries, sling spans) survives.
    _LEGEND_CLUSTER_EPS_M = 30.0

    cable_features = [
        feature
        for feature in features
        if feature.feature_class == "CABLE" and feature.native_points
    ]
    length_groups: dict[int, list[Feature]] = defaultdict(list)
    for feature in cable_features:
        points = feature.native_points
        length = sum(
            math.dist(points[i], points[i + 1])
            for i in range(len(points) - 1)
        )
        length_groups[round(length)].append(feature)

    legend_sample_keys: set[str] = set()
    for group in length_groups.values():
        if len(group) < _LEGEND_GROUP_MIN_MEMBERS:
            continue
        starts = [feature.native_points[0] for feature in group]
        x_span = max(point[0] for point in starts) - min(point[0] for point in starts)
        y_span = max(point[1] for point in starts) - min(point[1] for point in starts)
        layer_count = len({feature.source_layer for feature in group})
        if (
            x_span <= _LEGEND_GROUP_X_SPAN_M
            and y_span <= _LEGEND_GROUP_Y_SPAN_M
            and layer_count >= _LEGEND_GROUP_MIN_LAYERS
        ):
            legend_sample_keys.update(
                feature.feature_key for feature in group
            )

    legend_core_keys: set[str] = set()
    if legend_sample_keys:
        sample_points = [
            feature.native_points[0]
            for feature in features
            if feature.feature_key in legend_sample_keys
        ]
        core_min_x = min(point[0] for point in sample_points) - _LEGEND_CORE_PADDING_M
        core_max_x = max(point[0] for point in sample_points) + _LEGEND_CORE_PADDING_M
        core_min_y = min(point[1] for point in sample_points) - _LEGEND_CORE_PADDING_M
        core_max_y = max(point[1] for point in sample_points) + _LEGEND_CORE_PADDING_M
        in_core: list[Feature] = [
            feature
            for feature in features
            if feature.feature_class != "IMB"
            and feature.native_points
            and core_min_x <= feature.native_points[0][0] <= core_max_x
            and core_min_y <= feature.native_points[0][1] <= core_max_y
        ]
        sample_key_set = frozenset(feature.feature_key for feature in features
                                   if feature.feature_key in legend_sample_keys)
        # Union-find over candidate starts + sample starts; a candidate is
        # excluded only when density-connected to a legend sample.
        parent: dict[int, int] = {}
        candidates = in_core + [
            feature for feature in features if feature.feature_key in sample_key_set
        ]

        def _find(node: int) -> int:
            while parent.get(node, node) != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def _union(left: int, right: int) -> None:
            lroot, rroot = _find(left), _find(right)
            if lroot != rroot:
                parent[rroot] = lroot

        for index in range(len(candidates)):
            parent.setdefault(index, index)
        for left in range(len(candidates)):
            for right in range(left + 1, len(candidates)):
                if math.dist(candidates[left].native_points[0],
                             candidates[right].native_points[0]) <= _LEGEND_CLUSTER_EPS_M:
                    _union(left, right)
        sample_indices = {
            index for index, feature in enumerate(candidates)
            if feature.feature_key in sample_key_set
        }
        if sample_indices:
            legend_core_keys = set(legend_sample_keys)
            legend_core_keys.update(
                feature.feature_key
                for index, feature in enumerate(candidates)
                if index not in sample_indices
                and any(_find(index) == _find(sample_index) for sample_index in sample_indices)
            )

    # Unlabelled asset sample: a deployed asset INSERT carries its
    # identifier label nearby; a legend specimen (one per type on the sheet
    # edge) has none.  The identifier patterns come from the reviewed
    # annotation families (target_class=PTECH) — never hardcoded here.
    reviewed_label_patterns = [
        re.compile(family.text_pattern)
        for family in annotation_families
        if family.target_class == "PTECH"
    ]
    identifier_texts = [
        entity.centroid
        for entity in model_entities
        if entity.dwg_type in _ANNOTATION_CARRIER_TYPES
        and entity.text.strip()
        and not is_placeholder_text(entity.text)
        and any(
            pattern.fullmatch(entity.text.strip())
            for pattern in reviewed_label_patterns
        )
    ]
    if reviewed_label_patterns and identifier_texts:
        _LEGEND_ASSET_LABEL_RADIUS_M = 50.0
        for feature in features:
            if feature.feature_class not in {"PTECH", "BOITE", "SITE"}:
                continue
            source = entity_by_key.get(feature.source_entity_key)
            if (
                source is not None
                and source.dwg_type.upper() == "INSERT"
                and not any(
                    math.dist(source.centroid, text_centroid) <= _LEGEND_ASSET_LABEL_RADIUS_M
                    for text_centroid in identifier_texts
                )
            ):
                legend_core_keys.add(feature.feature_key)

    # Legend sample columns: a legend draws one specimen per pole/box type,
    # arranged in a strictly vertical (same x) or horizontal (same y)
    # equally-spaced column.  Real deployments follow roads, so their x/y
    # and spacing vary.  Pure geometry — independent of reviewed label
    # patterns, which an AI onboarding pass may mis-generate as placeholder
    # matches (MR.XXX.Pxxx) and thereby silently disable label-based rules.
    _LEGEND_COLUMN_MIN_MEMBERS = 3
    _LEGEND_COLUMN_TOLERANCE_FRACTION = 0.0005
    _LEGEND_COLUMN_TOLERANCE_MIN_M = 5.0
    _LEGEND_SPACING_CV_MAX = 0.2
    asset_inserts = [
        feature
        for feature in features
        if feature.feature_class in {"PTECH", "BOITE", "SITE"}
        and feature.geometry_kind == "Point"
        and feature.native_points
        and feature.feature_key not in legend_core_keys
        and entity_by_key.get(feature.source_entity_key) is not None
        and entity_by_key[feature.source_entity_key].dwg_type.upper() == "INSERT"
    ]
    if len(asset_inserts) >= _LEGEND_COLUMN_MIN_MEMBERS:
        _LEGEND_COLUMN_CABLE_RADIUS_M = 50.0
        _xs = [f.native_points[0][0] for f in asset_inserts]
        _ys = [f.native_points[0][1] for f in asset_inserts]
        _tol_x = max(_LEGEND_COLUMN_TOLERANCE_MIN_M,
                     _LEGEND_COLUMN_TOLERANCE_FRACTION * (max(_xs) - min(_xs)))
        _tol_y = max(_LEGEND_COLUMN_TOLERANCE_MIN_M,
                     _LEGEND_COLUMN_TOLERANCE_FRACTION * (max(_ys) - min(_ys)))

        def _regular_spacing(values: Sequence[float]) -> bool:
            ordered = sorted(values)
            spacings = [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)]
            mean = sum(spacings) / len(spacings)
            if mean <= 0.0:
                return False
            variance = sum((s - mean) ** 2 for s in spacings) / len(spacings)
            return (variance ** 0.5) / mean <= _LEGEND_SPACING_CV_MAX

        # Real deployments hug the cable route; a legend column sits far
        # from it.  A row/column whose members lie on a cable polyline is
        # poles strung along a straight span — geometrically identical to a
        # specimen column — and must be kept.
        cable_paths = [
            feature.native_points
            for feature in features
            if feature.feature_class == "CABLE" and feature.native_points
        ]

        def _on_cable(point: tuple[float, float]) -> bool:
            for path in cable_paths:
                for left, right in zip(path, path[1:]):
                    dx, dy = right[0] - left[0], right[1] - left[1]
                    seg_sq = dx * dx + dy * dy
                    if seg_sq <= 0.0:
                        continue
                    t = max(
                        0.0,
                        min(1.0, ((point[0] - left[0]) * dx
                                  + (point[1] - left[1]) * dy) / seg_sq),
                    )
                    px, py = left[0] + t * dx, left[1] + t * dy
                    if math.hypot(point[0] - px, point[1] - py) <= _LEGEND_COLUMN_CABLE_RADIUS_M:
                        return True
            return False

        def _is_specimen_column(column: Sequence[Feature]) -> bool:
            if len(column) < _LEGEND_COLUMN_MIN_MEMBERS:
                return False
            if any(
                _on_cable(feature.native_points[0]) for feature in column
            ):
                return False
            return _regular_spacing([
                f.native_points[0][1 - _axis] for f in column
            ])

        for _axis in (0, 1):
            ordered = sorted(
                asset_inserts,
                key=lambda f: f.native_points[0][_axis],
            )
            group: list[Feature] = []
            for feature in ordered:
                if group and abs(
                    feature.native_points[0][_axis]
                    - group[-1].native_points[0][_axis]
                ) > (_tol_x if _axis == 0 else _tol_y):
                    if _is_specimen_column(group):
                        legend_core_keys.update(f.feature_key for f in group)
                    group = []
                group.append(feature)
            if _is_specimen_column(group):
                legend_core_keys.update(f.feature_key for f in group)

    retained: list[Feature] = []
    for feature in features:
        if feature.feature_key in legend_core_keys:
            source = entity_by_key.get(feature.source_entity_key)
            coverage_records.append(_coverage_record(
                source, "legend_core_sample", feature.feature_class,
            ))
            continue
        retained.append(feature)
    if len(retained) != len(features):
        features = retained
        feature_by_key = {feature.feature_key: feature for feature in features}

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
