"""Deterministic, source-preserving geometry repair simulations.

The repair layer never accepts coordinates from a model.  A model may select
only a content-addressed candidate and a registered policy.  Simulators derive
all coordinates, distances, and delivery geometry from the frozen CAD stage
bundle.

Endpoint joins deliberately create a derived network relation instead of
moving or extending source cable geometry.  This keeps CAD geometry and native
length immutable while allowing downstream topology consumers to represent an
observed connection.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from .curve_geometry import (
    MATERIALIZATION_POLICY_VERSION,
    materialize_cable_features,
    validate_cable_geometry_materialization,
)
from .evidence_graph import EvidenceGraph, EvidenceNode
from .model import Relation
from .repair_decisions import DecisionPack, RepairOperation


GEOMETRY_REPAIR_SCHEMA = "cad2gis.geometry_repair_simulation.v1"
CURVE_MATERIALIZER_ID = "cad2gis.curve-materializer.v1"


class GeometryRepairError(ValueError):
    """A geometry operation cannot be proven from the selected evidence."""


@dataclass(frozen=True)
class EndpointJoinPolicy:
    policy_id: str
    max_gap_native_m: float


ENDPOINT_JOIN_POLICIES: Mapping[str, EndpointJoinPolicy] = {
    # Backward-compatible registered name used by early decision-pack fixtures.
    "snap-v1": EndpointJoinPolicy("snap-v1", 0.50),
    "endpoint-gap-conservative-v1": EndpointJoinPolicy(
        "endpoint-gap-conservative-v1", 0.25,
    ),
    "endpoint-exact-v1": EndpointJoinPolicy("endpoint-exact-v1", 1e-6),
}
INTERSECTION_SPLIT_POLICY_IDS = frozenset({"intersection-connect-v1"})
COLLINEAR_MERGE_POLICY_IDS = frozenset({"collinear-group-v1"})


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _linear_feature(feature: Any) -> bool:
    return (
        str(feature.feature_class).upper() == "CABLE"
        and str(feature.geometry_kind).upper()
        in {"LINE", "LINESTRING", "POLYLINE"}
        and len(feature.native_points) >= 2
    )


def _node_maps(graph: EvidenceGraph) -> tuple[dict[str, EvidenceNode], dict[str, EvidenceNode]]:
    return (
        {node.node_id: node for node in graph.nodes},
        {node.logical_id: node for node in graph.nodes},
    )


def _selected_features(
    graph: EvidenceGraph,
    operation: RepairOperation,
    feature_by_key: Mapping[str, Any],
    *,
    expected_count: int,
) -> tuple[Any, ...]:
    nodes_by_id, _ = _node_maps(graph)
    try:
        selected_nodes = tuple(nodes_by_id[node_id] for node_id in operation.entity_node_ids)
    except KeyError as exc:
        raise GeometryRepairError(f"Unknown selected evidence node: {exc.args[0]}") from exc
    feature_nodes = tuple(node for node in selected_nodes if node.kind == "feature")
    if len(feature_nodes) != expected_count or len(selected_nodes) != expected_count:
        raise GeometryRepairError(
            f"{operation.operation} requires exactly {expected_count} feature node(s)"
        )
    features = tuple(feature_by_key.get(node.logical_id) for node in feature_nodes)
    if any(feature is None for feature in features):
        raise GeometryRepairError("Selected feature is absent from the stage bundle")
    return features


def _require_source_evidence(
    graph: EvidenceGraph,
    operation: RepairOperation,
    features: Iterable[Any],
) -> None:
    _, nodes_by_logical = _node_maps(graph)
    selected_evidence = set(operation.evidence_node_ids)
    missing: list[str] = []
    for feature in features:
        source_node = nodes_by_logical.get(str(feature.source_entity_key))
        if source_node is None or source_node.node_id not in selected_evidence:
            missing.append(str(feature.source_entity_key))
    if missing:
        raise GeometryRepairError(
            f"Selected features lack their source-entity evidence: {sorted(missing)}"
        )


def _selected_candidate_edge(
    graph: EvidenceGraph,
    operation: RepairOperation,
    *,
    parameter_name: str,
    expected_kind: str,
) -> Any:
    candidate_id = operation.parameter_map[parameter_name]
    edge = next(
        (item for item in graph.edges if item.edge_id == candidate_id),
        None,
    )
    if edge is None or edge.kind != expected_kind:
        raise GeometryRepairError(
            f"{parameter_name} is not a {expected_kind} graph edge"
        )
    missing_evidence = set(edge.evidence_node_ids) - set(
        operation.evidence_node_ids
    )
    if missing_evidence:
        raise GeometryRepairError(
            f"Candidate edge evidence was not selected: {sorted(missing_evidence)}"
        )
    return edge


def _segment_feature_key(logical_id: str) -> str:
    marker = ":segment:"
    if marker not in logical_id:
        raise GeometryRepairError(
            f"Candidate endpoint is not a source segment: {logical_id!r}"
        )
    return logical_id.rsplit(marker, 1)[0]


@dataclass(frozen=True)
class EndpointPairCandidate:
    candidate_id: str
    left_feature_key: str
    left_endpoint: str
    right_feature_key: str
    right_endpoint: str
    distance_native_m: float

    @property
    def left_node_key(self) -> str:
        return f"{self.left_feature_key}:endpoint:{self.left_endpoint}"

    @property
    def right_node_key(self) -> str:
        return f"{self.right_feature_key}:endpoint:{self.right_endpoint}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "left_feature_key": self.left_feature_key,
            "left_endpoint": self.left_endpoint,
            "right_feature_key": self.right_feature_key,
            "right_endpoint": self.right_endpoint,
            "distance_native_m": self.distance_native_m,
        }


def endpoint_pair_candidates(
    graph: EvidenceGraph,
    left_feature: Any,
    right_feature: Any,
) -> tuple[EndpointPairCandidate, ...]:
    """Enumerate the four source-observed endpoint pairs in stable order."""

    if not _linear_feature(left_feature) or not _linear_feature(right_feature):
        raise GeometryRepairError("Endpoint joins require two linear CABLE features")
    ordered = sorted(
        (left_feature, right_feature), key=lambda feature: str(feature.feature_key),
    )
    endpoint_values = (
        ("start", tuple(map(float, ordered[0].native_points[0]))),
        ("end", tuple(map(float, ordered[0].native_points[-1]))),
    )
    other_values = (
        ("start", tuple(map(float, ordered[1].native_points[0]))),
        ("end", tuple(map(float, ordered[1].native_points[-1]))),
    )
    result: list[EndpointPairCandidate] = []
    for left_name, left_point in endpoint_values:
        for right_name, right_point in other_values:
            identity = {
                "schema_version": GEOMETRY_REPAIR_SCHEMA,
                "evidence_graph_sha256": graph.graph_sha256,
                "left_feature_key": str(ordered[0].feature_key),
                "left_endpoint": left_name,
                "right_feature_key": str(ordered[1].feature_key),
                "right_endpoint": right_name,
            }
            result.append(EndpointPairCandidate(
                candidate_id=f"epc_{_sha256_payload(identity)}",
                left_feature_key=str(ordered[0].feature_key),
                left_endpoint=left_name,
                right_feature_key=str(ordered[1].feature_key),
                right_endpoint=right_name,
                distance_native_m=math.dist(left_point, right_point),
            ))
    return tuple(sorted(result, key=lambda item: item.candidate_id))


def endpoint_pair_candidates_from_graph(
    graph: EvidenceGraph,
    left_feature_node_id: str,
    right_feature_node_id: str,
) -> tuple[EndpointPairCandidate, ...]:
    """Expose selectable endpoint IDs without requiring mutable stage objects."""

    nodes_by_id, _ = _node_maps(graph)
    selected = []
    for node_id in (left_feature_node_id, right_feature_node_id):
        node = nodes_by_id.get(str(node_id))
        if node is None or node.kind != "feature":
            raise GeometryRepairError(
                f"Endpoint candidate input is not a feature node: {node_id!r}"
            )
        facts = node.facts
        selected.append(SimpleNamespace(
            feature_key=node.logical_id,
            feature_class=facts.get("feature_class", ""),
            geometry_kind=facts.get("geometry_kind", ""),
            native_points=facts.get("native_points", ()),
        ))
    return endpoint_pair_candidates(graph, selected[0], selected[1])


@dataclass(frozen=True)
class GeometryRepairSimulation:
    features: tuple[Any, ...]
    relations: tuple[Relation, ...]
    declared_added_edges: tuple[tuple[str, str], ...] = ()
    declared_removed_edges: tuple[tuple[str, str], ...] = ()
    max_deviation_native: tuple[tuple[str, float], ...] = ()
    simulation_sha256: str = ""
    summary: tuple[tuple[str, Any], ...] = ()

    @property
    def deviation_map(self) -> dict[str, float]:
        return dict(self.max_deviation_native)

    @property
    def summary_dict(self) -> dict[str, Any]:
        return dict(self.summary)


def _simulation(
    *,
    operation: RepairOperation,
    features: Iterable[Any],
    relations: Iterable[Relation],
    declared_added_edges: Iterable[tuple[str, str]] = (),
    declared_removed_edges: Iterable[tuple[str, str]] = (),
    max_deviation_native: Mapping[str, float] | None = None,
    summary: Mapping[str, Any],
) -> GeometryRepairSimulation:
    added = tuple(sorted((str(left), str(right)) for left, right in declared_added_edges))
    removed = tuple(sorted((str(left), str(right)) for left, right in declared_removed_edges))
    deviations = tuple(sorted(
        (str(key), float(value))
        for key, value in (max_deviation_native or {}).items()
    ))
    summary_values = tuple(sorted(summary.items()))
    digest = _sha256_payload({
        "schema_version": GEOMETRY_REPAIR_SCHEMA,
        "operation_id": operation.operation_id,
        "added_edges": added,
        "removed_edges": removed,
        "max_deviation_native": deviations,
        "summary": dict(summary_values),
    })
    return GeometryRepairSimulation(
        tuple(features), tuple(relations), added, removed, deviations, digest,
        summary_values,
    )


def _simulate_endpoint_join(
    *,
    graph: EvidenceGraph,
    pack: DecisionPack,
    operation: RepairOperation,
    entities: Iterable[Any],
    features: Iterable[Any],
    relations: Iterable[Relation],
) -> GeometryRepairSimulation:
    del entities
    feature_values = tuple(features)
    feature_by_key = {
        str(feature.feature_key): feature for feature in feature_values
    }
    selected = _selected_features(
        graph, operation, feature_by_key, expected_count=2,
    )
    _require_source_evidence(graph, operation, selected)
    params = operation.parameter_map
    policy = ENDPOINT_JOIN_POLICIES.get(params["policy_id"])
    if policy is None:
        raise GeometryRepairError(
            f"Unregistered endpoint join policy: {params['policy_id']!r}"
        )
    candidates = {
        candidate.candidate_id: candidate
        for candidate in endpoint_pair_candidates(graph, selected[0], selected[1])
    }
    candidate = candidates.get(params["endpoint_pair_id"])
    if candidate is None:
        raise GeometryRepairError(
            "endpoint_pair_id is not a source-observed candidate for the selected features"
        )
    if candidate.distance_native_m > policy.max_gap_native_m:
        raise GeometryRepairError(
            f"Endpoint gap {candidate.distance_native_m:.9g} exceeds registered "
            f"policy {policy.policy_id} ({policy.max_gap_native_m:.9g})"
        )
    source_by_feature = {
        str(feature.feature_key): str(feature.source_entity_key)
        for feature in selected
    }
    source_key = candidate.left_node_key
    target_key = candidate.right_node_key
    relation_identity = {
        "kind": "derived_endpoint_connection",
        "source": source_key,
        "target": target_key,
        "operation_id": operation.operation_id,
        "decision_pack_sha256": pack.pack_sha256,
        "candidate_id": candidate.candidate_id,
        "policy_id": policy.policy_id,
    }
    relation = Relation(
        relation_key=_sha256_payload(relation_identity),
        relation_kind="derived_endpoint_connection",
        source_key=source_key,
        target_key=target_key,
        status="accepted",
        method=(
            f"decision_pack:{pack.pack_sha256}:"
            f"{policy.policy_id}:{candidate.candidate_id}"
        ),
        distance_native_m=candidate.distance_native_m,
        evidence_keys=tuple(sorted(source_by_feature.values())),
    )
    relation_values = tuple(relations)
    if any(item.relation_key == relation.relation_key for item in relation_values):
        raise GeometryRepairError("The selected endpoint connection already exists")
    return _simulation(
        operation=operation,
        features=feature_values,
        relations=(*relation_values, relation),
        declared_added_edges=((source_key, target_key),),
        summary={
            "operation": operation.operation,
            "candidate_id": candidate.candidate_id,
            "policy_id": policy.policy_id,
            "distance_native_m": candidate.distance_native_m,
            "source_geometry_changed": False,
            "native_length_changed": False,
            "derived_relation_key": relation.relation_key,
        },
    )


def _simulate_curve_materialization(
    *,
    graph: EvidenceGraph,
    operation: RepairOperation,
    entities: Iterable[Any],
    features: Iterable[Any],
    relations: Iterable[Relation],
) -> GeometryRepairSimulation:
    feature_values = copy.deepcopy(tuple(features))
    feature_by_key = {
        str(feature.feature_key): feature for feature in feature_values
    }
    selected = _selected_features(
        graph, operation, feature_by_key, expected_count=1,
    )
    feature = selected[0]
    if not _linear_feature(feature):
        raise GeometryRepairError(
            "Native curve materialization requires one linear CABLE feature"
        )
    _require_source_evidence(graph, operation, selected)
    params = operation.parameter_map
    if params["materializer_id"] != CURVE_MATERIALIZER_ID:
        raise GeometryRepairError(
            f"Unregistered curve materializer: {params['materializer_id']!r}"
        )
    if params["policy_id"] != MATERIALIZATION_POLICY_VERSION:
        raise GeometryRepairError(
            f"Unregistered curve policy: {params['policy_id']!r}"
        )
    source_by_key = {str(entity.entity_key): entity for entity in entities}
    source = source_by_key.get(str(feature.source_entity_key))
    if source is None:
        raise GeometryRepairError("Selected CABLE source entity is absent")
    before_fingerprint = str(
        feature.attributes.get("curve_materialization", {}).get(
            "materialization_fingerprint", "",
        )
    )
    materialize_cable_features([source], [feature], strict=True)
    diagnostics = validate_cable_geometry_materialization(
        [source], [feature], require_all=True,
    )
    after_fingerprint = str(
        feature.attributes["curve_materialization"]["materialization_fingerprint"]
    )
    feature_node_id = graph.logical_index[str(feature.feature_key)].node_id
    return _simulation(
        operation=operation,
        features=feature_values,
        relations=tuple(relations),
        max_deviation_native=(
            {feature_node_id: 0.0}
            if before_fingerprint != after_fingerprint else {}
        ),
        summary={
            "operation": operation.operation,
            "materializer_id": CURVE_MATERIALIZER_ID,
            "policy_id": MATERIALIZATION_POLICY_VERSION,
            "before_materialization_fingerprint": before_fingerprint,
            "after_materialization_fingerprint": after_fingerprint,
            "validated": bool(diagnostics.get("validated")),
            "source_geometry_changed": False,
            "native_length_changed": False,
        },
    )


def _simulate_intersection_split(
    *,
    graph: EvidenceGraph,
    pack: DecisionPack,
    operation: RepairOperation,
    entities: Iterable[Any],
    features: Iterable[Any],
    relations: Iterable[Relation],
) -> GeometryRepairSimulation:
    del entities
    feature_values = tuple(features)
    feature_by_key = {
        str(feature.feature_key): feature for feature in feature_values
    }
    selected = _selected_features(
        graph, operation, feature_by_key, expected_count=2,
    )
    if any(not _linear_feature(feature) for feature in selected):
        raise GeometryRepairError(
            "Intersection splitting requires two linear CABLE features"
        )
    _require_source_evidence(graph, operation, selected)
    policy_id = operation.parameter_map["policy_id"]
    if policy_id not in INTERSECTION_SPLIT_POLICY_IDS:
        raise GeometryRepairError(
            f"Unregistered intersection split policy: {policy_id!r}"
        )
    edge = _selected_candidate_edge(
        graph,
        operation,
        parameter_name="intersection_evidence_id",
        expected_kind="crossing_candidate",
    )
    nodes_by_id, _ = _node_maps(graph)
    edge_feature_keys = {
        _segment_feature_key(nodes_by_id[edge.source_node_id].logical_id),
        _segment_feature_key(nodes_by_id[edge.target_node_id].logical_id),
    }
    selected_feature_keys = {str(feature.feature_key) for feature in selected}
    if edge_feature_keys != selected_feature_keys:
        raise GeometryRepairError(
            "Intersection candidate does not bind the selected CABLE features"
        )
    intersection_key = f"derived_intersection:{edge.edge_id}"
    source_keys = (
        nodes_by_id[edge.source_node_id].logical_id,
        nodes_by_id[edge.target_node_id].logical_id,
    )
    existing_relations = tuple(relations)
    additions: list[Relation] = []
    for source_key in sorted(source_keys):
        identity = {
            "kind": "derived_intersection_incidence",
            "source": source_key,
            "target": intersection_key,
            "operation_id": operation.operation_id,
            "decision_pack_sha256": pack.pack_sha256,
            "candidate_edge_id": edge.edge_id,
            "policy_id": policy_id,
        }
        additions.append(Relation(
            relation_key=_sha256_payload(identity),
            relation_kind="derived_intersection_incidence",
            source_key=source_key,
            target_key=intersection_key,
            status="accepted",
            method=(
                f"decision_pack:{pack.pack_sha256}:{policy_id}:{edge.edge_id}"
            ),
            distance_native_m=0.0,
            evidence_keys=tuple(sorted(
                str(feature.source_entity_key) for feature in selected
            )),
        ))
    existing_keys = {relation.relation_key for relation in existing_relations}
    if any(relation.relation_key in existing_keys for relation in additions):
        raise GeometryRepairError(
            "The selected derived intersection split already exists"
        )
    added_edges = tuple(
        (relation.source_key, relation.target_key) for relation in additions
    )
    return _simulation(
        operation=operation,
        features=feature_values,
        relations=(*existing_relations, *additions),
        declared_added_edges=added_edges,
        summary={
            "operation": operation.operation,
            "candidate_edge_id": edge.edge_id,
            "policy_id": policy_id,
            "derived_intersection_key": intersection_key,
            "derived_incidence_count": len(additions),
            "source_geometry_changed": False,
            "native_length_changed": False,
        },
    )


def _simulate_collinear_merge(
    *,
    graph: EvidenceGraph,
    pack: DecisionPack,
    operation: RepairOperation,
    entities: Iterable[Any],
    features: Iterable[Any],
    relations: Iterable[Relation],
) -> GeometryRepairSimulation:
    del entities
    feature_values = tuple(features)
    feature_by_key = {
        str(feature.feature_key): feature for feature in feature_values
    }
    selected = _selected_features(
        graph, operation, feature_by_key, expected_count=2,
    )
    if any(not _linear_feature(feature) for feature in selected):
        raise GeometryRepairError(
            "Collinear grouping requires two linear CABLE features"
        )
    _require_source_evidence(graph, operation, selected)
    policy_id = operation.parameter_map["policy_id"]
    if policy_id not in COLLINEAR_MERGE_POLICY_IDS:
        raise GeometryRepairError(
            f"Unregistered collinear merge policy: {policy_id!r}"
        )
    edge = _selected_candidate_edge(
        graph,
        operation,
        parameter_name="group_id",
        expected_kind="collinear_overlap_candidate",
    )
    nodes_by_id, _ = _node_maps(graph)
    edge_feature_keys = {
        _segment_feature_key(nodes_by_id[edge.source_node_id].logical_id),
        _segment_feature_key(nodes_by_id[edge.target_node_id].logical_id),
    }
    selected_feature_keys = {str(feature.feature_key) for feature in selected}
    if edge_feature_keys != selected_feature_keys:
        raise GeometryRepairError(
            "Collinear candidate does not bind the selected CABLE features"
        )
    group_key = f"derived_collinear_group:{edge.edge_id}"
    existing_relations = tuple(relations)
    additions: list[Relation] = []
    for feature in sorted(selected, key=lambda item: str(item.feature_key)):
        identity = {
            "kind": "derived_collinear_group_member",
            "source": group_key,
            "target": str(feature.feature_key),
            "operation_id": operation.operation_id,
            "decision_pack_sha256": pack.pack_sha256,
            "candidate_edge_id": edge.edge_id,
            "policy_id": policy_id,
        }
        additions.append(Relation(
            relation_key=_sha256_payload(identity),
            relation_kind="derived_collinear_group_member",
            source_key=group_key,
            target_key=str(feature.feature_key),
            status="accepted",
            method=(
                f"decision_pack:{pack.pack_sha256}:{policy_id}:{edge.edge_id}"
            ),
            distance_native_m=0.0,
            evidence_keys=(str(feature.source_entity_key),),
        ))
    existing_keys = {relation.relation_key for relation in existing_relations}
    if any(relation.relation_key in existing_keys for relation in additions):
        raise GeometryRepairError(
            "The selected derived collinear group already exists"
        )
    added_edges = tuple(
        (relation.source_key, relation.target_key) for relation in additions
    )
    return _simulation(
        operation=operation,
        features=feature_values,
        relations=(*existing_relations, *additions),
        declared_added_edges=added_edges,
        summary={
            "operation": operation.operation,
            "candidate_edge_id": edge.edge_id,
            "policy_id": policy_id,
            "derived_group_key": group_key,
            "member_count": len(additions),
            "source_geometry_changed": False,
            "native_length_changed": False,
        },
    )


def simulate_geometry_operation(
    *,
    graph: EvidenceGraph,
    pack: DecisionPack,
    operation: RepairOperation,
    entities: Iterable[Any],
    features: Iterable[Any],
    relations: Iterable[Relation],
) -> GeometryRepairSimulation:
    """Run one registered geometry simulator without mutating its inputs."""

    if operation.operation == "join_observed_endpoints":
        return _simulate_endpoint_join(
            graph=graph, pack=pack, operation=operation, entities=entities,
            features=features, relations=relations,
        )
    if operation.operation == "materialize_native_curve":
        return _simulate_curve_materialization(
            graph=graph, operation=operation, entities=entities,
            features=features, relations=relations,
        )
    if operation.operation == "split_at_observed_intersection":
        return _simulate_intersection_split(
            graph=graph, pack=pack, operation=operation, entities=entities,
            features=features, relations=relations,
        )
    if operation.operation == "merge_collinear_fragments":
        return _simulate_collinear_merge(
            graph=graph, pack=pack, operation=operation, entities=entities,
            features=features, relations=relations,
        )
    raise GeometryRepairError(
        f"No deterministic geometry simulator for {operation.operation!r}"
    )


__all__ = [
    "CURVE_MATERIALIZER_ID",
    "COLLINEAR_MERGE_POLICY_IDS",
    "ENDPOINT_JOIN_POLICIES",
    "GEOMETRY_REPAIR_SCHEMA",
    "EndpointJoinPolicy",
    "EndpointPairCandidate",
    "GeometryRepairError",
    "GeometryRepairSimulation",
    "INTERSECTION_SPLIT_POLICY_IDS",
    "endpoint_pair_candidates",
    "endpoint_pair_candidates_from_graph",
    "simulate_geometry_operation",
]
