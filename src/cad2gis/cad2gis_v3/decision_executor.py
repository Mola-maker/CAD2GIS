"""Deterministic executor for source-bound decision operations.

Every executable operation is simulated first and independently checked for
geometry, topology, and native-length closure.  Unsupported geometry and
georeference operations remain quarantined instead of accepting model-created
coordinates or measurements.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .decision_validation import (
    AutoDecision,
    AutoDecisionPolicy,
    GeometrySnapshot,
    LengthSnapshot,
    TopologySnapshot,
    ValidationReport,
    validate_geometry,
    validate_lengths,
    validate_topology,
)
from .evidence_graph import EvidenceGraph
from .geometry_repairs import (
    GeometryRepairError,
    GeometryRepairSimulation,
    simulate_geometry_operation,
)
from .repair_decisions import DecisionPack, RepairOperation


DECISION_EXECUTION_SCHEMA = "cad2gis.decision_execution.v1"
_SEMANTIC_OPERATIONS = frozenset({"attach_existing_label", "register_style"})
_GEOMETRY_OPERATIONS = frozenset({
    "join_observed_endpoints",
    "materialize_native_curve",
    "merge_collinear_fragments",
    "split_at_observed_intersection",
})
_EXECUTABLE_OPERATIONS = _SEMANTIC_OPERATIONS | _GEOMETRY_OPERATIONS


class DecisionExecutionError(ValueError):
    """A supposedly deterministic operation cannot be materialized safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _relation_dict(relation: Any) -> dict[str, Any]:
    return {
        "relation_key": str(relation.relation_key),
        "relation_kind": str(relation.relation_kind),
        "source_key": str(relation.source_key),
        "target_key": str(relation.target_key),
        "status": str(relation.status),
        "method": str(relation.method),
        "distance_native_m": relation.distance_native_m,
        "evidence_keys": list(relation.evidence_keys),
    }


def _geometry_fingerprint(feature: Any) -> str:
    payload = {
        "feature_key": str(feature.feature_key),
        "geometry_kind": str(feature.geometry_kind),
        "native_points": [[float(x), float(y)] for x, y in feature.native_points],
        "source_entity_key": str(feature.source_entity_key),
        "geometry_role": str(feature.geometry_role),
        "curve_materialization": feature.attributes.get("curve_materialization"),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _feature_length(feature: Any, source_by_key: dict[str, Any]) -> float:
    source = source_by_key.get(str(feature.source_entity_key))
    native_length = getattr(source, "native_length", None)
    if native_length is not None:
        value = float(native_length)
        if not math.isfinite(value) or value < 0.0:
            raise DecisionExecutionError("source native length is invalid")
        return value
    points = list(feature.native_points)
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:])
    )


def _snapshots(
    graph: EvidenceGraph,
    features: Iterable[Any],
    relations: Iterable[Any],
    source_by_key: dict[str, Any],
    *,
    max_deviation_native: dict[str, float] | None = None,
):
    feature_values = list(features)
    graph_nodes = graph.logical_index
    feature_node_ids: dict[str, str] = {}
    for feature in feature_values:
        feature_key = str(feature.feature_key)
        node = graph_nodes.get(feature_key)
        if node is None or node.kind != "feature":
            raise DecisionExecutionError(
                f"Feature {feature_key!r} is not bound to the evidence graph"
            )
        feature_node_ids[feature_key] = node.node_id
    geometry = GeometrySnapshot.create({
        feature_node_ids[str(feature.feature_key)]: _geometry_fingerprint(feature)
        for feature in feature_values
    }, max_deviation_native=max_deviation_native)
    topology = TopologySnapshot.create(
        (str(relation.source_key), str(relation.target_key)) for relation in relations
    )
    lengths = LengthSnapshot.create({
        feature_node_ids[str(feature.feature_key)]: _feature_length(feature, source_by_key)
        for feature in feature_values
        if str(feature.geometry_kind).upper() in {"LINE", "LINESTRING", "POLYLINE"}
    })
    return geometry, topology, lengths


def _selected_objects(
    graph: EvidenceGraph,
    operation: RepairOperation,
    feature_by_key: dict[str, Any],
    source_by_key: dict[str, Any],
) -> tuple[Any, Any]:
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    selected = [nodes_by_id[node_id] for node_id in operation.entity_node_ids]
    feature_nodes = [node for node in selected if node.kind == "feature"]
    source_nodes = [node for node in selected if node.kind == "source_entity"]
    if len(feature_nodes) != 1 or len(source_nodes) != 1:
        raise DecisionExecutionError(
            f"{operation.operation} requires exactly one feature and one source entity"
        )
    feature = feature_by_key.get(feature_nodes[0].logical_id)
    source = source_by_key.get(source_nodes[0].logical_id)
    if feature is None or source is None:
        raise DecisionExecutionError("Selected graph objects are not present in the stage bundle")
    return feature, source


def _apply_registered_operation(
    graph: EvidenceGraph,
    pack: DecisionPack,
    operation: RepairOperation,
    features: list[Any],
    entities: Iterable[Any],
) -> None:
    feature_by_key = {str(feature.feature_key): feature for feature in features}
    source_by_key = {str(entity.entity_key): entity for entity in entities}
    feature, source = _selected_objects(
        graph, operation, feature_by_key, source_by_key,
    )
    if operation.operation == "attach_existing_label":
        label = str(source.text).strip()
        if not label:
            raise DecisionExecutionError("Selected source entity has no explicit text")
        feature.display_label = label
        feature.label_provenance = (
            f"decision_pack:{pack.pack_sha256}:source_entity:{source.entity_key}"
        )
        feature.lineage.append({
            "operation": operation.operation,
            "operation_id": operation.operation_id,
            "decision_pack_sha256": pack.pack_sha256,
            "source_entity_key": source.entity_key,
        })
        return
    if operation.operation == "register_style":
        feature.style = source.style
        feature.lineage.append({
            "operation": operation.operation,
            "operation_id": operation.operation_id,
            "decision_pack_sha256": pack.pack_sha256,
            "source_entity_key": source.entity_key,
        })
        return
    raise DecisionExecutionError(f"No deterministic executor for {operation.operation!r}")


@dataclass(frozen=True)
class OperationExecution:
    operation_id: str
    disposition: str
    applied: bool
    reasons: tuple[str, ...]
    report_sha256s: tuple[str, ...] = ()
    decision_sha256: str = ""
    simulation_sha256: str = ""
    simulation_summary: tuple[tuple[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "disposition": self.disposition,
            "applied": self.applied,
            "reasons": list(self.reasons),
            "report_sha256s": list(self.report_sha256s),
            "decision_sha256": self.decision_sha256,
            "simulation_sha256": self.simulation_sha256,
            "simulation_summary": dict(self.simulation_summary),
        }


@dataclass(frozen=True)
class DecisionExecutionResult:
    pack_sha256: str
    evidence_graph_sha256: str
    features: tuple[Any, ...]
    relations: tuple[Any, ...]
    executions: tuple[OperationExecution, ...]
    reports: tuple[ValidationReport, ...]
    execution_sha256: str

    @property
    def applied_count(self) -> int:
        return sum(item.applied for item in self.executions)

    @property
    def unresolved_count(self) -> int:
        return sum(not item.applied for item in self.executions)

    def manifest_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DECISION_EXECUTION_SCHEMA,
            "execution_sha256": self.execution_sha256,
            "applied_count": self.applied_count,
            "unresolved_count": self.unresolved_count,
            "operations": [item.to_dict() for item in self.executions],
        }

    def receipt_dict(self) -> dict[str, Any]:
        derived_relations = tuple(
            relation for relation in self.relations
            if str(relation.relation_kind).startswith("derived_")
        )
        return {
            "schema_version": DECISION_EXECUTION_SCHEMA,
            "execution_sha256": self.execution_sha256,
            "pack_sha256": self.pack_sha256,
            "evidence_graph_sha256": self.evidence_graph_sha256,
            "operations": [item.to_dict() for item in self.executions],
            "reports": [item.to_dict() for item in self.reports],
            "derived_relations": [
                _relation_dict(relation) for relation in derived_relations
            ],
            "source_geometry_mutated": False,
        }

    def derived_network_dict(self) -> dict[str, Any]:
        relations = tuple(
            relation for relation in self.relations
            if str(relation.relation_kind).startswith("derived_")
        )
        return {
            "schema_version": "cad2gis.derived_network.v1",
            "execution_sha256": self.execution_sha256,
            "pack_sha256": self.pack_sha256,
            "evidence_graph_sha256": self.evidence_graph_sha256,
            "source_geometry_mutated": False,
            "native_lengths_mutated": False,
            "relation_count": len(relations),
            "relations": [_relation_dict(relation) for relation in relations],
        }


def execute_decision_pack(
    *,
    graph: EvidenceGraph,
    pack: DecisionPack,
    entities: Iterable[Any],
    features: Iterable[Any],
    relations: Iterable[Any],
    policy: AutoDecisionPolicy | None = None,
) -> DecisionExecutionResult:
    """Simulate, independently validate, then apply registered operations."""

    pack.validate_against(graph)
    policy_value = policy or AutoDecisionPolicy(policy_id=pack.policy_id)
    entity_values = tuple(entities)
    relation_values = tuple(relations)
    source_by_key = {str(entity.entity_key): entity for entity in entity_values}
    working_features = copy.deepcopy(list(features))
    working_relations = list(relation_values)
    executions: list[OperationExecution] = []
    reports: list[ValidationReport] = []

    for operation in pack.operations:
        if operation.operation not in _EXECUTABLE_OPERATIONS:
            executions.append(OperationExecution(
                operation_id=operation.operation_id,
                disposition="QUARANTINED",
                applied=False,
                reasons=(
                    f"deterministic executor is not registered for {operation.operation}",
                ),
            ))
            continue
        before = _snapshots(
            graph, working_features, working_relations, source_by_key,
        )
        simulated_features = copy.deepcopy(working_features)
        simulated_relations = list(working_relations)
        simulation: GeometryRepairSimulation | None = None
        try:
            if operation.operation in _SEMANTIC_OPERATIONS:
                _apply_registered_operation(
                    graph, pack, operation, simulated_features, entity_values,
                )
                declared_added_edges: tuple[tuple[str, str], ...] = ()
                declared_removed_edges: tuple[tuple[str, str], ...] = ()
                max_deviation_native: dict[str, float] = {}
            else:
                simulation = simulate_geometry_operation(
                    graph=graph,
                    pack=pack,
                    operation=operation,
                    entities=entity_values,
                    features=working_features,
                    relations=working_relations,
                )
                simulated_features = list(simulation.features)
                simulated_relations = list(simulation.relations)
                declared_added_edges = simulation.declared_added_edges
                declared_removed_edges = simulation.declared_removed_edges
                max_deviation_native = simulation.deviation_map
            after = _snapshots(
                graph,
                simulated_features,
                simulated_relations,
                source_by_key,
                max_deviation_native=max_deviation_native,
            )
            operation_reports = (
                validate_geometry(pack, operation, before[0], after[0]),
                validate_topology(
                    pack,
                    operation,
                    before[1],
                    after[1],
                    declared_added_edges=declared_added_edges,
                    declared_removed_edges=declared_removed_edges,
                ),
                validate_lengths(
                    pack, operation, before[2], after[2], tolerance_native=1e-9,
                ),
            )
            reports.extend(operation_reports)
            decision: AutoDecision = policy_value.evaluate(
                pack, operation, operation_reports,
            )
        except (DecisionExecutionError, GeometryRepairError, ValueError) as exc:
            executions.append(OperationExecution(
                operation_id=operation.operation_id,
                disposition="REJECTED",
                applied=False,
                reasons=(str(exc),),
            ))
            continue
        applied = decision.disposition == "AUTO_ACCEPTED"
        if applied:
            working_features = simulated_features
            working_relations = simulated_relations
        executions.append(OperationExecution(
            operation_id=operation.operation_id,
            disposition=decision.disposition,
            applied=applied,
            reasons=decision.reasons,
            report_sha256s=decision.report_sha256s,
            decision_sha256=decision.decision_sha256,
            simulation_sha256=(
                "" if simulation is None else simulation.simulation_sha256
            ),
            simulation_summary=(
                () if simulation is None else simulation.summary
            ),
        ))

    execution_payload = {
        "schema_version": DECISION_EXECUTION_SCHEMA,
        "pack_sha256": pack.pack_sha256,
        "evidence_graph_sha256": graph.graph_sha256,
        "operations": [item.to_dict() for item in executions],
        "reports": [item.to_dict() for item in reports],
        "derived_relations": [
            _relation_dict(relation)
            for relation in working_relations
            if str(relation.relation_kind).startswith("derived_")
        ],
    }
    execution_sha256 = hashlib.sha256(
        _canonical_json(execution_payload).encode("utf-8")
    ).hexdigest()
    return DecisionExecutionResult(
        pack_sha256=pack.pack_sha256,
        evidence_graph_sha256=graph.graph_sha256,
        features=tuple(working_features),
        relations=tuple(working_relations),
        executions=tuple(executions),
        reports=tuple(reports),
        execution_sha256=execution_sha256,
    )


__all__ = [
    "DECISION_EXECUTION_SCHEMA",
    "DecisionExecutionError",
    "DecisionExecutionResult",
    "OperationExecution",
    "execute_decision_pack",
]
