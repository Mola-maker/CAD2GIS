"""Typed, hash-bound AI scene-role proposals over existing CAD graph IDs."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .cad_scene_graph import CadSceneGraph


SCENE_INTERPRETATION_SCHEMA = "cad2gis.scene_interpretation_plan.v1"
SCENE_ASSIGNMENT_SCHEMA = "cad2gis.scene_role_assignment.v1"
SCENE_ROLES = frozenset({
    "plan_content",
    "legend_catalog",
    "title_block",
    "schedule_or_summary",
    "overview_or_schematic",
    "annotation_only",
    "unknown_scene",
})
_TARGET_NODE_KINDS = frozenset({
    "layout", "block_definition", "source_entity", "plan_entity",
})


class SceneInterpretationError(ValueError):
    """A model proposal escaped the scene-understanding authority boundary."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SceneInterpretationError("Proposal contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise SceneInterpretationError("Proposal contains a non-string key")
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    raise SceneInterpretationError(
        f"Proposal contains unsupported type {type(value).__name__}"
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_sha(value: Any, name: str) -> str:
    text = str(value)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise SceneInterpretationError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise SceneInterpretationError(
            f"{name} keys must be exactly {sorted(expected)}; got {sorted(actual)}"
        )


@dataclass(frozen=True)
class SceneRoleAssignment:
    target_node_id: str
    role: str
    confidence: float
    evidence_region_ids: tuple[str, ...]
    rationale: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SceneRoleAssignment":
        _exact_keys(value, {
            "schema_version",
            "action",
            "target_node_id",
            "role",
            "confidence",
            "evidence_region_ids",
            "rationale",
        }, "scene role assignment")
        if value.get("schema_version") != SCENE_ASSIGNMENT_SCHEMA:
            raise SceneInterpretationError("Unsupported scene assignment schema")
        if value.get("action") != "rank_scene_role":
            raise SceneInterpretationError("Scene assignment action must rank_scene_role")
        node_id = str(value.get("target_node_id", "")).strip()
        if not node_id:
            raise SceneInterpretationError("target_node_id must be non-empty")
        role = str(value.get("role", ""))
        if role not in SCENE_ROLES:
            raise SceneInterpretationError(f"Unsupported scene role: {role!r}")
        confidence_value = value.get("confidence")
        if isinstance(confidence_value, bool) or not isinstance(
            confidence_value, (int, float)
        ):
            raise SceneInterpretationError("confidence must be a number")
        confidence = float(confidence_value)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise SceneInterpretationError("confidence must be between 0 and 1")
        raw_regions = value.get("evidence_region_ids")
        if not isinstance(raw_regions, list):
            raise SceneInterpretationError("evidence_region_ids must be an array")
        regions = tuple(str(item).strip() for item in raw_regions)
        if any(not item for item in regions) or len(regions) != len(set(regions)):
            raise SceneInterpretationError(
                "evidence_region_ids must contain unique non-empty IDs"
            )
        rationale = str(value.get("rationale", "")).strip()
        if not rationale:
            raise SceneInterpretationError("rationale must be non-empty")
        return cls(node_id, role, confidence, regions, rationale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCENE_ASSIGNMENT_SCHEMA,
            "action": "rank_scene_role",
            "target_node_id": self.target_node_id,
            "role": self.role,
            "confidence": self.confidence,
            "evidence_region_ids": list(self.evidence_region_ids),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class SceneInterpretationPlan:
    source_sha256: str
    cad_scene_graph_sha256: str
    scene_visual_manifest_sha256: str
    assignments: tuple[SceneRoleAssignment, ...]
    producer: dict[str, Any]
    plan_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sha256: str,
        cad_scene_graph_sha256: str,
        scene_visual_manifest_sha256: str,
        assignments: Iterable[SceneRoleAssignment],
        producer: Mapping[str, Any] | None = None,
    ) -> "SceneInterpretationPlan":
        source = _require_sha(source_sha256, "source_sha256")
        graph = _require_sha(cad_scene_graph_sha256, "cad_scene_graph_sha256")
        visual = _require_sha(
            scene_visual_manifest_sha256, "scene_visual_manifest_sha256"
        )
        ordered = tuple(sorted(assignments, key=lambda item: item.target_node_id))
        targets = [item.target_node_id for item in ordered]
        if len(targets) != len(set(targets)):
            raise SceneInterpretationError(
                "A scene node may receive at most one role assignment"
            )
        canonical_producer = _canonical(dict(producer or {}))
        identity = {
            "schema_version": SCENE_INTERPRETATION_SCHEMA,
            "source_sha256": source,
            "cad_scene_graph_sha256": graph,
            "scene_visual_manifest_sha256": visual,
            "assignments": [item.to_dict() for item in ordered],
            "producer": canonical_producer,
            "authority": {
                "operation": "candidate_ranking_only",
                "unassigned_default": "unknown_scene",
                "source_entities_deleted": False,
            },
        }
        plan_sha = _sha256(_canonical_bytes(identity))
        return cls(source, graph, visual, ordered, canonical_producer, plan_sha)

    def validate_against(
        self,
        graph: CadSceneGraph,
        visual_manifest: Mapping[str, Any],
        *,
        visual_manifest_sha256: str,
    ) -> dict[str, Any]:
        if self.source_sha256 != graph.source_sha256:
            raise SceneInterpretationError("Scene plan source hash mismatch")
        if self.cad_scene_graph_sha256 != graph.graph_sha256:
            raise SceneInterpretationError("Scene plan graph hash mismatch")
        if self.scene_visual_manifest_sha256 != _require_sha(
            visual_manifest_sha256, "visual_manifest_sha256"
        ):
            raise SceneInterpretationError("Scene plan visual manifest hash mismatch")
        if visual_manifest.get("source_sha256") != graph.source_sha256:
            raise SceneInterpretationError("Scene visual source hash mismatch")
        if visual_manifest.get("cad_scene_graph_sha256") != graph.graph_sha256:
            raise SceneInterpretationError("Scene visual graph hash mismatch")
        regions = {
            str(item.get("region_id"))
            for item in visual_manifest.get("regions", ())
            if isinstance(item, Mapping) and item.get("region_id")
        }
        nodes = {node.node_id: node for node in graph.nodes}
        assigned_source_nodes = 0
        for assignment in self.assignments:
            node = nodes.get(assignment.target_node_id)
            if node is None:
                raise SceneInterpretationError(
                    f"Unknown scene target node ID: {assignment.target_node_id}"
                )
            if node.kind not in _TARGET_NODE_KINDS:
                raise SceneInterpretationError(
                    f"Scene role cannot target node kind {node.kind!r}"
                )
            missing_regions = set(assignment.evidence_region_ids) - regions
            if missing_regions:
                raise SceneInterpretationError(
                    f"Unknown evidence region IDs: {sorted(missing_regions)}"
                )
            assigned_source_nodes += node.kind == "source_entity"
        source_node_count = sum(node.kind == "source_entity" for node in graph.nodes)
        return {
            "schema_version": "cad2gis.scene_interpretation_validation.v1",
            "valid": True,
            "plan_sha256": self.plan_sha256,
            "assignment_count": len(self.assignments),
            "assigned_source_entity_count": assigned_source_nodes,
            "unassigned_source_entity_count": source_node_count - assigned_source_nodes,
            "unassigned_role": "unknown_scene",
            "source_entities_deleted": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCENE_INTERPRETATION_SCHEMA,
            "source_sha256": self.source_sha256,
            "cad_scene_graph_sha256": self.cad_scene_graph_sha256,
            "scene_visual_manifest_sha256": self.scene_visual_manifest_sha256,
            "assignments": [item.to_dict() for item in self.assignments],
            "producer": self.producer,
            "authority": {
                "operation": "candidate_ranking_only",
                "unassigned_default": "unknown_scene",
                "source_entities_deleted": False,
            },
            "plan_sha256": self.plan_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SceneInterpretationPlan":
        _exact_keys(value, {
            "schema_version",
            "source_sha256",
            "cad_scene_graph_sha256",
            "scene_visual_manifest_sha256",
            "assignments",
            "producer",
            "authority",
            "plan_sha256",
        }, "scene interpretation plan")
        if value.get("schema_version") != SCENE_INTERPRETATION_SCHEMA:
            raise SceneInterpretationError("Unsupported scene interpretation schema")
        raw_assignments = value.get("assignments")
        if not isinstance(raw_assignments, list):
            raise SceneInterpretationError("assignments must be an array")
        producer = value.get("producer")
        if not isinstance(producer, Mapping):
            raise SceneInterpretationError("producer must be an object")
        plan = cls.create(
            source_sha256=str(value.get("source_sha256", "")),
            cad_scene_graph_sha256=str(value.get("cad_scene_graph_sha256", "")),
            scene_visual_manifest_sha256=str(
                value.get("scene_visual_manifest_sha256", "")
            ),
            assignments=(
                SceneRoleAssignment.from_dict(item) for item in raw_assignments
            ),
            producer=producer,
        )
        if value.get("authority") != plan.to_dict()["authority"]:
            raise SceneInterpretationError("Scene plan authority contract mismatch")
        if value.get("plan_sha256") != plan.plan_sha256:
            raise SceneInterpretationError("Scene plan content address mismatch")
        return plan


def scene_interpretation_json_schema() -> dict[str, Any]:
    """Return a provider-neutral strict JSON schema for a scene-role proposal."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "source_sha256", "cad_scene_graph_sha256",
            "scene_visual_manifest_sha256", "assignments", "producer",
            "authority", "plan_sha256",
        ],
        "properties": {
            "schema_version": {"const": SCENE_INTERPRETATION_SCHEMA},
            "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "cad_scene_graph_sha256": {
                "type": "string", "pattern": "^[0-9a-f]{64}$",
            },
            "scene_visual_manifest_sha256": {
                "type": "string", "pattern": "^[0-9a-f]{64}$",
            },
            "assignments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "schema_version", "action", "target_node_id", "role",
                        "confidence", "evidence_region_ids", "rationale",
                    ],
                    "properties": {
                        "schema_version": {"const": SCENE_ASSIGNMENT_SCHEMA},
                        "action": {"const": "rank_scene_role"},
                        "target_node_id": {"type": "string", "minLength": 1},
                        "role": {"enum": sorted(SCENE_ROLES)},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "evidence_region_ids": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "uniqueItems": True,
                        },
                        "rationale": {"type": "string", "minLength": 1},
                    },
                },
            },
            "producer": {"type": "object"},
            "authority": {
                "const": {
                    "operation": "candidate_ranking_only",
                    "unassigned_default": "unknown_scene",
                    "source_entities_deleted": False,
                },
            },
            "plan_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    }


__all__ = [
    "SCENE_ASSIGNMENT_SCHEMA",
    "SCENE_INTERPRETATION_SCHEMA",
    "SCENE_ROLES",
    "SceneInterpretationError",
    "SceneInterpretationPlan",
    "SceneRoleAssignment",
    "scene_interpretation_json_schema",
]
