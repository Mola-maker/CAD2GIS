"""Typed, reversible repair proposals and content-addressed decision packs.

Language or vision models may choose only registered operations and evidence
graph IDs.  They cannot submit coordinates, WKT, geometry, lengths, GCP
values, or arbitrary attributes.  Registered deterministic tools materialize
the chosen operation later and independent validators decide whether it is
eligible for automatic acceptance.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .evidence_graph import EvidenceGraph


REPAIR_OPERATION_SCHEMA = "cad2gis.repair_operation.v1"
DECISION_PACK_SCHEMA = "cad2gis.decision_pack.v1"

Risk = Literal["semantic", "geometry", "georeference"]


class RepairDecisionError(ValueError):
    """A model proposal escaped the registered decision boundary."""


@dataclass(frozen=True)
class OperationSpec:
    risk: Risk
    min_entities: int
    min_evidence: int
    allowed_parameters: frozenset[str]
    required_parameters: frozenset[str] = frozenset()
    changes_geometry: bool = False


OPERATION_REGISTRY: dict[str, OperationSpec] = {
    "attach_existing_label": OperationSpec(
        "semantic", 2, 1, frozenset({"policy_id"}), frozenset({"policy_id"}),
    ),
    "bind_existing_dimension": OperationSpec(
        "semantic", 2, 1, frozenset({"policy_id"}), frozenset({"policy_id"}),
    ),
    "select_semantic_class": OperationSpec(
        "semantic", 1, 1, frozenset({"candidate_id", "policy_id"}),
        frozenset({"candidate_id", "policy_id"}),
    ),
    "register_style": OperationSpec(
        "semantic", 2, 1, frozenset({"policy_id"}), frozenset({"policy_id"}),
    ),
    "materialize_native_curve": OperationSpec(
        "geometry", 1, 1, frozenset({"materializer_id", "policy_id"}),
        frozenset({"materializer_id", "policy_id"}), True,
    ),
    "join_observed_endpoints": OperationSpec(
        "geometry", 2, 2, frozenset({"endpoint_pair_id", "policy_id"}),
        frozenset({"endpoint_pair_id", "policy_id"}), True,
    ),
    "split_at_observed_intersection": OperationSpec(
        "geometry", 2, 2, frozenset({"intersection_evidence_id", "policy_id"}),
        frozenset({"intersection_evidence_id", "policy_id"}), True,
    ),
    "merge_collinear_fragments": OperationSpec(
        "geometry", 2, 2, frozenset({"group_id", "policy_id"}),
        frozenset({"group_id", "policy_id"}), True,
    ),
    "reverse_edge_direction": OperationSpec(
        "geometry", 1, 1, frozenset({"policy_id"}), frozenset({"policy_id"}), True,
    ),
    "select_crs_candidate": OperationSpec(
        "georeference", 1, 2, frozenset({"candidate_id", "policy_id"}),
        frozenset({"candidate_id", "policy_id"}),
    ),
    "fit_transform_candidate": OperationSpec(
        "georeference", 1, 3,
        frozenset({"model_family", "policy_id", "gcp_binding_set_id"}),
        frozenset({"model_family", "policy_id", "gcp_binding_set_id"}),
    ),
}

_ALLOWED_MODEL_FAMILIES = frozenset({"identity", "similarity", "affine", "projective", "tps"})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_sha256(value: Any, path: str) -> str:
    text = str(value)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise RepairDecisionError(f"{path} must be a lowercase SHA-256 digest")
    return text


def _require_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepairDecisionError(f"{path} must be a non-empty string")
    if len(value) > 512:
        raise RepairDecisionError(f"{path} is too long")
    return value.strip()


def _id_tuple(value: Iterable[Any], path: str, *, minimum: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise RepairDecisionError(f"{path} must be an array of IDs")
    result = tuple(sorted({_require_id(item, f"{path}[]") for item in value}))
    if len(result) < minimum:
        raise RepairDecisionError(f"{path} must contain at least {minimum} unique IDs")
    return result


def _canonical(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RepairDecisionError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RepairDecisionError(f"{path} contains a non-string key")
            result[key] = _canonical(item, f"{path}.{key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item, f"{path}[]") for item in value]
    raise RepairDecisionError(f"{path} contains unsupported type {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _validate_parameters(operation: str, parameters: Mapping[str, Any]) -> dict[str, str]:
    spec = OPERATION_REGISTRY[operation]
    actual = set(parameters)
    unknown = actual - spec.allowed_parameters
    missing = spec.required_parameters - actual
    if unknown or missing:
        raise RepairDecisionError(
            f"{operation} parameters mismatch; unknown={sorted(unknown)}, "
            f"missing={sorted(missing)}"
        )
    result: dict[str, str] = {}
    for key, value in parameters.items():
        # Operation parameters select a registered candidate/policy/tool.  A
        # number here would be an untrusted measurement or threshold.
        result[key] = _require_id(value, f"parameters.{key}")
    family = result.get("model_family")
    if family is not None and family not in _ALLOWED_MODEL_FAMILIES:
        raise RepairDecisionError(f"Unsupported transform model family: {family!r}")
    return {key: result[key] for key in sorted(result)}


@dataclass(frozen=True)
class RepairOperation:
    operation_id: str
    operation: str
    entity_node_ids: tuple[str, ...]
    evidence_node_ids: tuple[str, ...]
    parameters: tuple[tuple[str, str], ...]
    confidence: float
    agreement_count: int
    rationale_sha256: str

    @classmethod
    def create(
        cls,
        *,
        operation: str,
        entity_node_ids: Iterable[str],
        evidence_node_ids: Iterable[str],
        parameters: Mapping[str, Any],
        confidence: float,
        agreement_count: int,
        rationale_sha256: str,
    ) -> "RepairOperation":
        if operation not in OPERATION_REGISTRY:
            raise RepairDecisionError(f"Unregistered repair operation: {operation!r}")
        spec = OPERATION_REGISTRY[operation]
        entities = _id_tuple(entity_node_ids, "entity_node_ids", minimum=spec.min_entities)
        evidence = _id_tuple(evidence_node_ids, "evidence_node_ids", minimum=spec.min_evidence)
        params = _validate_parameters(operation, parameters)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise RepairDecisionError("confidence must be a finite number")
        confidence_value = float(confidence)
        if not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0:
            raise RepairDecisionError("confidence must be between 0 and 1")
        if isinstance(agreement_count, bool) or not isinstance(agreement_count, int):
            raise RepairDecisionError("agreement_count must be an integer")
        if agreement_count < 1:
            raise RepairDecisionError("agreement_count must be at least 1")
        rationale = _require_sha256(rationale_sha256, "rationale_sha256")
        identity = {
            "schema_version": REPAIR_OPERATION_SCHEMA,
            "operation": operation,
            "entity_node_ids": list(entities),
            "evidence_node_ids": list(evidence),
            "parameters": params,
            "confidence": confidence_value,
            "agreement_count": agreement_count,
            "rationale_sha256": rationale,
        }
        operation_id = f"rop_{_sha256(_canonical_json(identity).encode('utf-8'))}"
        return cls(
            operation_id, operation, entities, evidence, tuple(params.items()),
            confidence_value, agreement_count, rationale,
        )

    @property
    def risk(self) -> Risk:
        return OPERATION_REGISTRY[self.operation].risk

    @property
    def changes_geometry(self) -> bool:
        return OPERATION_REGISTRY[self.operation].changes_geometry

    @property
    def parameter_map(self) -> dict[str, str]:
        return dict(self.parameters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REPAIR_OPERATION_SCHEMA,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "entity_node_ids": list(self.entity_node_ids),
            "evidence_node_ids": list(self.evidence_node_ids),
            "parameters": self.parameter_map,
            "confidence": self.confidence,
            "agreement_count": self.agreement_count,
            "rationale_sha256": self.rationale_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RepairOperation":
        if payload.get("schema_version") != REPAIR_OPERATION_SCHEMA:
            raise RepairDecisionError("Unsupported repair operation schema")
        entity_ids = payload.get("entity_node_ids")
        evidence_ids = payload.get("evidence_node_ids")
        parameters = payload.get("parameters")
        if not isinstance(entity_ids, list) or not isinstance(evidence_ids, list):
            raise RepairDecisionError("Operation entity/evidence IDs must be arrays")
        if not isinstance(parameters, Mapping):
            raise RepairDecisionError("Operation parameters must be an object")
        operation = cls.create(
            operation=str(payload.get("operation", "")),
            entity_node_ids=entity_ids,
            evidence_node_ids=evidence_ids,
            parameters=parameters,
            confidence=payload.get("confidence"),
            agreement_count=payload.get("agreement_count"),
            rationale_sha256=str(payload.get("rationale_sha256", "")),
        )
        if payload.get("operation_id") != operation.operation_id:
            raise RepairDecisionError("Repair operation content address does not match payload")
        return operation


@dataclass(frozen=True)
class DecisionPack:
    """Frozen proposal input consumed by deterministic simulation tools."""

    source_sha256: str
    evidence_graph_sha256: str
    policy_id: str
    proposer: tuple[tuple[str, str], ...]
    operations: tuple[RepairOperation, ...]
    pack_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sha256: str,
        evidence_graph_sha256: str,
        policy_id: str,
        proposer: Mapping[str, Any],
        operations: Iterable[RepairOperation],
    ) -> "DecisionPack":
        source = _require_sha256(source_sha256, "source_sha256")
        graph = _require_sha256(evidence_graph_sha256, "evidence_graph_sha256")
        policy = _require_id(policy_id, "policy_id")
        allowed_proposer = {
            "provider", "model", "protocol", "request_sha256", "response_sha256",
        }
        unknown = set(proposer) - allowed_proposer
        missing = {"provider", "model", "protocol", "request_sha256", "response_sha256"} - set(proposer)
        if unknown or missing:
            raise RepairDecisionError(
                f"proposer fields mismatch; unknown={sorted(unknown)}, missing={sorted(missing)}"
            )
        proposer_map = {key: _require_id(value, f"proposer.{key}") for key, value in proposer.items()}
        for key in ("request_sha256", "response_sha256"):
            _require_sha256(proposer_map[key], f"proposer.{key}")
        ordered_operations = tuple(sorted(operations, key=lambda item: item.operation_id))
        if not ordered_operations:
            raise RepairDecisionError("Decision pack must contain at least one operation")
        if len({item.operation_id for item in ordered_operations}) != len(ordered_operations):
            raise RepairDecisionError("Decision pack contains duplicate operations")
        identity = {
            "schema_version": DECISION_PACK_SCHEMA,
            "source_sha256": source,
            "evidence_graph_sha256": graph,
            "policy_id": policy,
            "proposer": proposer_map,
            "operations": [item.to_dict() for item in ordered_operations],
        }
        digest = _sha256(_canonical_json(identity).encode("utf-8"))
        return cls(
            source, graph, policy, tuple(sorted(proposer_map.items())),
            ordered_operations, digest,
        )

    @property
    def proposer_map(self) -> dict[str, str]:
        return dict(self.proposer)

    def validate_against(self, graph: EvidenceGraph) -> None:
        if graph.source_sha256 != self.source_sha256:
            raise RepairDecisionError("Decision pack belongs to another source")
        if graph.graph_sha256 != self.evidence_graph_sha256:
            raise RepairDecisionError("Decision pack belongs to another evidence graph")
        graph_ids = graph.node_ids
        for operation in self.operations:
            missing = set(operation.entity_node_ids + operation.evidence_node_ids) - graph_ids
            if missing:
                raise RepairDecisionError(
                    f"Operation {operation.operation_id} references unknown graph nodes: "
                    f"{sorted(missing)}"
                )
            target_kinds = {
                node.kind for node in graph.nodes if node.node_id in operation.entity_node_ids
            }
            if "unresolved" in target_kinds or "external_reference" in target_kinds:
                raise RepairDecisionError(
                    f"Operation {operation.operation_id} targets non-executable evidence"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DECISION_PACK_SCHEMA,
            "source_sha256": self.source_sha256,
            "evidence_graph_sha256": self.evidence_graph_sha256,
            "policy_id": self.policy_id,
            "proposer": self.proposer_map,
            "operations": [item.to_dict() for item in self.operations],
            "pack_sha256": self.pack_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionPack":
        if payload.get("schema_version") != DECISION_PACK_SCHEMA:
            raise RepairDecisionError("Unsupported decision pack schema")
        proposer = payload.get("proposer")
        operations = payload.get("operations")
        if not isinstance(proposer, Mapping) or not isinstance(operations, list):
            raise RepairDecisionError("Decision pack proposer/operations have invalid shape")
        pack = cls.create(
            source_sha256=str(payload.get("source_sha256", "")),
            evidence_graph_sha256=str(payload.get("evidence_graph_sha256", "")),
            policy_id=str(payload.get("policy_id", "")),
            proposer=proposer,
            operations=(RepairOperation.from_dict(item) for item in operations),
        )
        if payload.get("pack_sha256") != pack.pack_sha256:
            raise RepairDecisionError("Decision pack digest does not match payload")
        return pack


def write_decision_pack_atomic(path: str | Path, pack: DecisionPack) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        pack.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def load_decision_pack(path: str | Path) -> DecisionPack:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RepairDecisionError("Decision pack root must be an object")
    return DecisionPack.from_dict(payload)


__all__ = [
    "DECISION_PACK_SCHEMA",
    "OPERATION_REGISTRY",
    "REPAIR_OPERATION_SCHEMA",
    "DecisionPack",
    "OperationSpec",
    "RepairDecisionError",
    "RepairOperation",
    "load_decision_pack",
    "write_decision_pack_atomic",
]
