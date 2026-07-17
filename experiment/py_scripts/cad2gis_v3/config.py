"""Versioned APD source profile and reviewed mapping registry loaders."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _load_json(path: str | Path) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).resolve()
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {resolved}")
    return resolved, value


@dataclass(frozen=True)
class SourceProfile:
    path: Path
    schema_version: str
    source_sha256: str
    dwg_cgeocs: str
    dwg_insunits: int
    source_crs: str
    target_crs: str
    drawing_units: str
    expected_census: dict[str, int]

    @classmethod
    def load(cls, path: str | Path) -> "SourceProfile":
        resolved, value = _load_json(path)
        required = {
            "schema_version", "source_sha256", "dwg_cgeocs", "dwg_insunits", "source_crs",
            "target_crs", "drawing_units", "expected_census",
        }
        unknown = set(value) - required
        missing = required - set(value)
        if missing or unknown:
            raise ValueError(f"Invalid source profile keys; missing={sorted(missing)}, unknown={sorted(unknown)}")
        if value["schema_version"] != "cad2gis-source-profile-v1":
            raise ValueError(f"Unsupported source profile: {value['schema_version']}")
        return cls(
            path=resolved,
            schema_version=value["schema_version"],
            source_sha256=str(value["source_sha256"]).lower(),
            dwg_cgeocs=str(value["dwg_cgeocs"]),
            dwg_insunits=int(value["dwg_insunits"]),
            source_crs=str(value["source_crs"]),
            target_crs=str(value["target_crs"]),
            drawing_units=str(value["drawing_units"]),
            expected_census={str(key): int(item) for key, item in value["expected_census"].items()},
        )

    def validate_source(self, source: str | Path) -> str:
        resolved = Path(source).resolve()
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if digest != self.source_sha256:
            raise ValueError(f"Source hash mismatch: expected {self.source_sha256}, got {digest}")
        return digest


@dataclass(frozen=True)
class AnnotationFamily:
    """A source-reviewed, mutually isolated annotation assignment domain."""

    family_id: str
    target_class: str
    text_pattern: str
    source_layer_pattern: str
    target_layer_pattern: str
    require_same_layer: bool
    max_distance_native_m: float
    rule_id: str
    provenance: str


@dataclass(frozen=True)
class MappingRegistry:
    path: Path
    source_sha256: str
    block_families: dict[str, tuple[str, ...]]
    layers: dict[str, tuple[str, ...]]
    positive_route_layer_regex: str
    field_rules: dict[str, dict[str, dict[str, Any]]]
    display_label_rules: dict[str, dict[str, Any]]
    annotation_families: tuple[AnnotationFamily, ...]
    decision_rules: dict[str, dict[str, str]]
    labels: dict[str, str]
    thresholds: dict[str, float]
    policy: dict[str, bool]

    @classmethod
    def load(cls, path: str | Path, source_sha256: str) -> "MappingRegistry":
        resolved, value = _load_json(path)
        expected = {
            "schema_version", "source_sha256", "block_families", "layers",
            "positive_route_layer_regex", "field_rules", "display_label_rules",
            "annotation_families", "decision_rules", "labels",
            "thresholds_native_m", "policy",
        }
        missing, unknown = expected - set(value), set(value) - expected
        if missing or unknown:
            raise ValueError(f"Invalid mapping registry keys; missing={sorted(missing)}, unknown={sorted(unknown)}")
        if value["schema_version"] != "cad2gis-mapping-registry-v1":
            raise ValueError(f"Unsupported mapping registry: {value['schema_version']}")
        bound_hash = str(value["source_sha256"]).lower()
        if bound_hash != source_sha256.lower():
            raise ValueError("Mapping registry is stale or bound to a different DWG")
        all_rules = [
            rule
            for feature_rules in value["field_rules"].values()
            for rule in feature_rules.values()
        ] + list(value["display_label_rules"].values())
        if any(not rule.get("rule_id") or not rule.get("kind") or not rule.get("provenance") for rule in all_rules):
            raise ValueError("Every reviewed field/display rule requires rule_id, kind, and provenance")
        rule_ids = [str(rule["rule_id"]) for rule in all_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Reviewed mapping rule_id values must be unique")

        raw_annotation_families = value["annotation_families"]
        if not isinstance(raw_annotation_families, list) or not raw_annotation_families:
            raise ValueError("annotation_families must be a non-empty JSON array")
        annotation_family_keys = {
            "family_id", "target_class", "text_pattern", "source_layer_pattern",
            "target_layer_pattern", "require_same_layer", "max_distance_native_m",
            "rule_id", "provenance",
        }
        annotation_families = []
        family_ids = []
        annotation_rule_ids = []
        for index, raw_family in enumerate(raw_annotation_families):
            if not isinstance(raw_family, dict):
                raise ValueError(f"annotation_families[{index}] must be an object")
            missing_family = annotation_family_keys - set(raw_family)
            unknown_family = set(raw_family) - annotation_family_keys
            if missing_family or unknown_family:
                raise ValueError(
                    f"Invalid annotation family keys at index {index}; "
                    f"missing={sorted(missing_family)}, unknown={sorted(unknown_family)}"
                )
            string_fields = (
                "family_id", "target_class", "text_pattern", "source_layer_pattern",
                "target_layer_pattern", "rule_id", "provenance",
            )
            if any(type(raw_family[field]) is not str for field in string_fields):
                raise ValueError(
                    f"annotation_families[{index}] required string fields must be strings"
                )
            normalized = {field: raw_family[field].strip() for field in string_fields}
            if any(not normalized[field] for field in string_fields):
                raise ValueError(f"annotation_families[{index}] contains an empty required string")
            if re.fullmatch(r"[a-z][a-z0-9_]*", normalized["family_id"]) is None:
                raise ValueError(
                    f"Invalid annotation family_id: {normalized['family_id']!r}"
                )
            if normalized["target_class"] not in value["block_families"]:
                raise ValueError(
                    f"Annotation family {normalized['family_id']} targets unknown class "
                    f"{normalized['target_class']}"
                )
            if type(raw_family["require_same_layer"]) is not bool:
                raise ValueError(
                    f"Annotation family {normalized['family_id']} require_same_layer must be boolean"
                )
            threshold = raw_family["max_distance_native_m"]
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                raise ValueError(
                    f"Annotation family {normalized['family_id']} max_distance_native_m "
                    "must be a finite positive number"
                )
            threshold = float(threshold)
            if not math.isfinite(threshold) or threshold <= 0.0:
                raise ValueError(
                    f"Annotation family {normalized['family_id']} max_distance_native_m "
                    "must be a finite positive number"
                )
            for field in ("text_pattern", "source_layer_pattern", "target_layer_pattern"):
                try:
                    compiled = re.compile(normalized[field])
                except re.error as exc:
                    raise ValueError(
                        f"Invalid {field} for annotation family {normalized['family_id']}: {exc}"
                    ) from exc
                if compiled.fullmatch(""):
                    raise ValueError(
                        f"Annotation family {normalized['family_id']} {field} must not match empty text"
                    )
            if normalized["rule_id"] not in normalized["provenance"]:
                raise ValueError(
                    f"Annotation family {normalized['family_id']} provenance must cite its rule_id"
                )
            family_ids.append(normalized["family_id"])
            annotation_rule_ids.append(normalized["rule_id"])
            annotation_families.append(AnnotationFamily(
                family_id=normalized["family_id"],
                target_class=normalized["target_class"],
                text_pattern=normalized["text_pattern"],
                source_layer_pattern=normalized["source_layer_pattern"],
                target_layer_pattern=normalized["target_layer_pattern"],
                require_same_layer=raw_family["require_same_layer"],
                max_distance_native_m=threshold,
                rule_id=normalized["rule_id"],
                provenance=normalized["provenance"],
            ))
        if len(family_ids) != len({family_id.casefold() for family_id in family_ids}):
            raise ValueError("annotation family_id values must be unique")
        if len(annotation_rule_ids) != len(set(annotation_rule_ids)):
            raise ValueError("annotation family rule_id values must be unique")
        required_decisions = {
            "annotation_assignment", "span_segment_measurement",
            "fdt_layout_identification", "fat_layout_sequence",
        }
        if set(value["decision_rules"]) != required_decisions:
            raise ValueError(
                f"Invalid decision rules: expected {sorted(required_decisions)}, "
                f"got {sorted(value['decision_rules'])}"
            )
        decision_rule_ids = []
        for name, rule in value["decision_rules"].items():
            if not rule.get("rule_id") or not rule.get("method") or not rule.get("provenance"):
                raise ValueError(f"Decision rule {name} requires rule_id, method, and provenance")
            decision_rule_ids.append(str(rule["rule_id"]))
        all_rule_id_groups = (rule_ids, decision_rule_ids, annotation_rule_ids)
        flattened_rule_ids = [item for group in all_rule_id_groups for item in group]
        if len(flattened_rule_ids) != len(set(flattened_rule_ids)):
            raise ValueError("All reviewed rule_id values must be globally unique")
        expected_labels = {
            "site", "suspected_asset_id", "fdt_id_attribute", "fat_sequence_attribute",
        }
        if set(value["labels"]) != expected_labels:
            raise ValueError(
                f"Invalid labels keys: expected {sorted(expected_labels)}, "
                f"got {sorted(value['labels'])}"
            )
        if any(type(value["labels"][key]) is not str for key in expected_labels):
            raise ValueError("All labels values must be strings")
        for key in ("site", "suspected_asset_id"):
            try:
                pattern = re.compile(value["labels"][key])
            except re.error as exc:
                raise ValueError(f"Invalid labels.{key} pattern: {exc}") from exc
            if pattern.fullmatch(""):
                raise ValueError(f"labels.{key} must not match empty text")
        for key in ("fdt_id_attribute", "fat_sequence_attribute"):
            if not value["labels"][key].strip():
                raise ValueError(f"labels.{key} must not be empty")
        expected_policy = {
            "source_geometry_immutable", "crossing_is_connection",
            "support_is_optical_node", "force_route_components_connected",
            "generic_line_is_cable", "dimension_is_cable_geometry",
        }
        if set(value["policy"]) != expected_policy:
            raise ValueError(
                f"Invalid policy keys: expected {sorted(expected_policy)}, "
                f"got {sorted(value['policy'])}"
            )
        return cls(
            path=resolved,
            source_sha256=bound_hash,
            block_families={
                str(feature_class): tuple(str(name).upper() for name in names)
                for feature_class, names in value["block_families"].items()
            },
            layers={
                str(kind): tuple(str(name).upper() for name in names)
                for kind, names in value["layers"].items()
            },
            positive_route_layer_regex=str(value["positive_route_layer_regex"]),
            field_rules={
                str(feature_class): {
                    str(field_name): dict(rule)
                    for field_name, rule in rules.items()
                }
                for feature_class, rules in value["field_rules"].items()
            },
            display_label_rules={
                str(feature_class): dict(rule)
                for feature_class, rule in value["display_label_rules"].items()
            },
            annotation_families=tuple(annotation_families),
            decision_rules={
                str(name): {str(key): str(item) for key, item in rule.items()}
                for name, rule in value["decision_rules"].items()
            },
            labels={str(key): str(item) for key, item in value["labels"].items()},
            thresholds={str(key): float(item) for key, item in value["thresholds_native_m"].items()},
            policy={str(key): bool(item) for key, item in value["policy"].items()},
        )
