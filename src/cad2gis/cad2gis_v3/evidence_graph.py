"""Content-addressed evidence graph for model-assisted CAD review.

The graph is the only context an automated reasoner may reference.  It keeps
reader facts immutable, binds every node to one source hash, and gives visual
or language models stable IDs instead of allowing them to describe or invent
coordinates.  The production converter remains deterministic: a later
decision pack can only select graph IDs and registered operations.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EVIDENCE_GRAPH_SCHEMA = "cad2gis.evidence_graph.v1"
EVIDENCE_NODE_SCHEMA = "cad2gis.evidence_node.v1"
EVIDENCE_EDGE_SCHEMA = "cad2gis.evidence_edge.v1"

_NODE_KINDS = frozenset({
    "source_entity",
    "feature",
    "unresolved",
    "external_reference",
    "render_region",
    "external_candidate",
    "measurement",
})


class EvidenceGraphError(ValueError):
    """A source-bound graph contract was violated."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_indented_object(
    file: Any,
    serialized: str,
    indent: int,
) -> None:
    """Write ``serialized`` as one indented nested JSON object.

    ``serialized`` must be the output of ``json.dumps(..., indent=2)`` for one
    object.  Every line receives ``indent`` additional spaces so it can be
    embedded at the exact nesting depth produced by a monolithic
    ``json.dumps(..., indent=2)`` call.
    """
    lines = serialized.split("\n")
    prefix = " " * indent
    file.write(prefix + lines[0])
    for line in lines[1:]:
        file.write("\n" + prefix + line)


def _graph_sha256(
    source: str,
    nodes: Iterable[EvidenceNode],
    edges: Iterable[EvidenceEdge],
) -> str:
    """Compute the canonical graph digest as a single-pass streamed hash.

    Equivalent to ``_sha256_bytes(_canonical_json(identity).encode("utf-8"))``
    over the canonical graph identity, but avoids materialising the full
    identity dict and full JSON string.
    """
    digest = hashlib.sha256()
    digest.update(b'{"edges":[')
    for index, edge in enumerate(edges):
        if index:
            digest.update(b",")
        digest.update(edge.canonical_json().encode("utf-8"))
    digest.update(b'],"nodes":[')
    for index, node in enumerate(nodes):
        if index:
            digest.update(b",")
        digest.update(node.canonical_json().encode("utf-8"))
    digest.update(b'],"schema_version":')
    digest.update(_json_scalar(EVIDENCE_GRAPH_SCHEMA).encode("utf-8"))
    digest.update(b',"source_sha256":')
    digest.update(_json_scalar(source).encode("utf-8"))
    digest.update(b"}")
    return digest.hexdigest()


def _require_sha256(value: Any, path: str) -> str:
    text = str(value)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise EvidenceGraphError(f"{path} must be a lowercase SHA-256 digest")
    return text


def _require_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceGraphError(f"{path} must be a non-empty string")
    return value.strip()


def _canonical(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceGraphError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvidenceGraphError(f"{path} contains a non-string key")
            result[key] = _canonical(item, f"{path}.{key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        canonical_items = [_canonical(item, f"{path}[]") for item in value]
        return sorted(canonical_items, key=_canonical_json)
    raise EvidenceGraphError(f"{path} contains unsupported type {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_scalar(value: Any) -> str:
    """Serialize one already-validated scalar/list with canonical settings."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class EvidenceNode:
    """One immutable fact object with a source-bound content address."""

    node_id: str
    source_sha256: str
    logical_id: str
    kind: str
    facts_sha256: str
    _facts_json: str

    @classmethod
    def create(
        cls,
        *,
        source_sha256: str,
        logical_id: str,
        kind: str,
        facts: Mapping[str, Any],
    ) -> "EvidenceNode":
        source = _require_sha256(source_sha256, "source_sha256")
        logical = _require_id(logical_id, "logical_id")
        if kind not in _NODE_KINDS:
            raise EvidenceGraphError(f"Unsupported evidence node kind: {kind!r}")
        facts_json = _canonical_json(facts)
        facts_sha256 = _sha256_bytes(facts_json.encode("utf-8"))
        identity = {
            "schema_version": EVIDENCE_NODE_SCHEMA,
            "source_sha256": source,
            "logical_id": logical,
            "kind": kind,
            "facts_sha256": facts_sha256,
        }
        node_id = f"evn_{_sha256_bytes(_canonical_json(identity).encode('utf-8'))}"
        return cls(node_id, source, logical, kind, facts_sha256, facts_json)

    @property
    def facts(self) -> dict[str, Any]:
        return json.loads(self._facts_json)

    def canonical_json(self) -> str:
        """Return the sorted-key canonical node JSON without re-serializing facts.

        This is exactly the string ``_canonical_json(self.to_dict())`` would
        produce; the cached ``_facts_json`` segment is embedded verbatim so no
        full node/facts dict copy is made for graph digest computation.
        """
        return (
            '{"facts":' + self._facts_json
            + ',"facts_sha256":' + _json_scalar(self.facts_sha256)
            + ',"kind":' + _json_scalar(self.kind)
            + ',"logical_id":' + _json_scalar(self.logical_id)
            + ',"node_id":' + _json_scalar(self.node_id)
            + ',"schema_version":' + _json_scalar(EVIDENCE_NODE_SCHEMA)
            + ',"source_sha256":' + _json_scalar(self.source_sha256)
            + "}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EVIDENCE_NODE_SCHEMA,
            "node_id": self.node_id,
            "source_sha256": self.source_sha256,
            "logical_id": self.logical_id,
            "kind": self.kind,
            "facts_sha256": self.facts_sha256,
            "facts": self.facts,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceNode":
        if payload.get("schema_version") != EVIDENCE_NODE_SCHEMA:
            raise EvidenceGraphError("Unsupported evidence node schema")
        rebuilt = cls.create(
            source_sha256=str(payload.get("source_sha256", "")),
            logical_id=str(payload.get("logical_id", "")),
            kind=str(payload.get("kind", "")),
            facts=payload.get("facts") if isinstance(payload.get("facts"), Mapping) else {},
        )
        if payload.get("node_id") != rebuilt.node_id:
            raise EvidenceGraphError("Evidence node content address does not match payload")
        if payload.get("facts_sha256") != rebuilt.facts_sha256:
            raise EvidenceGraphError("Evidence node facts digest does not match payload")
        return rebuilt


@dataclass(frozen=True)
class EvidenceEdge:
    """A typed relation between existing evidence nodes."""

    edge_id: str
    source_sha256: str
    kind: str
    source_node_id: str
    target_node_id: str
    evidence_node_ids: tuple[str, ...]
    facts_sha256: str
    _facts_json: str

    @classmethod
    def create(
        cls,
        *,
        source_sha256: str,
        kind: str,
        source_node_id: str,
        target_node_id: str,
        evidence_node_ids: Iterable[str] = (),
        facts: Mapping[str, Any] | None = None,
    ) -> "EvidenceEdge":
        source = _require_sha256(source_sha256, "source_sha256")
        relation_kind = _require_id(kind, "kind")
        source_id = _require_id(source_node_id, "source_node_id")
        target_id = _require_id(target_node_id, "target_node_id")
        evidence_ids = tuple(sorted({_require_id(item, "evidence_node_ids[]") for item in evidence_node_ids}))
        facts_json = _canonical_json(dict(facts or {}))
        facts_sha256 = _sha256_bytes(facts_json.encode("utf-8"))
        identity = {
            "schema_version": EVIDENCE_EDGE_SCHEMA,
            "source_sha256": source,
            "kind": relation_kind,
            "source_node_id": source_id,
            "target_node_id": target_id,
            "evidence_node_ids": list(evidence_ids),
            "facts_sha256": facts_sha256,
        }
        edge_id = f"eve_{_sha256_bytes(_canonical_json(identity).encode('utf-8'))}"
        return cls(
            edge_id, source, relation_kind, source_id, target_id,
            evidence_ids, facts_sha256, facts_json,
        )

    @property
    def facts(self) -> dict[str, Any]:
        return json.loads(self._facts_json)

    def canonical_json(self) -> str:
        """Return the sorted-key canonical edge JSON without re-serializing facts."""
        return (
            '{"edge_id":' + _json_scalar(self.edge_id)
            + ',"evidence_node_ids":' + _json_scalar(list(self.evidence_node_ids))
            + ',"facts":' + self._facts_json
            + ',"facts_sha256":' + _json_scalar(self.facts_sha256)
            + ',"kind":' + _json_scalar(self.kind)
            + ',"schema_version":' + _json_scalar(EVIDENCE_EDGE_SCHEMA)
            + ',"source_node_id":' + _json_scalar(self.source_node_id)
            + ',"source_sha256":' + _json_scalar(self.source_sha256)
            + ',"target_node_id":' + _json_scalar(self.target_node_id)
            + "}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EVIDENCE_EDGE_SCHEMA,
            "edge_id": self.edge_id,
            "source_sha256": self.source_sha256,
            "kind": self.kind,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "evidence_node_ids": list(self.evidence_node_ids),
            "facts_sha256": self.facts_sha256,
            "facts": self.facts,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceEdge":
        if payload.get("schema_version") != EVIDENCE_EDGE_SCHEMA:
            raise EvidenceGraphError("Unsupported evidence edge schema")
        raw_evidence_ids = payload.get("evidence_node_ids", ())
        if not isinstance(raw_evidence_ids, (list, tuple)):
            raise EvidenceGraphError("evidence_node_ids must be an array")
        rebuilt = cls.create(
            source_sha256=str(payload.get("source_sha256", "")),
            kind=str(payload.get("kind", "")),
            source_node_id=str(payload.get("source_node_id", "")),
            target_node_id=str(payload.get("target_node_id", "")),
            evidence_node_ids=raw_evidence_ids,
            facts=payload.get("facts") if isinstance(payload.get("facts"), Mapping) else {},
        )
        if payload.get("edge_id") != rebuilt.edge_id:
            raise EvidenceGraphError("Evidence edge content address does not match payload")
        if payload.get("facts_sha256") != rebuilt.facts_sha256:
            raise EvidenceGraphError("Evidence edge facts digest does not match payload")
        return rebuilt


@dataclass(frozen=True)
class EvidenceGraph:
    """Canonical graph snapshot supplied to reasoning and repair tools."""

    source_sha256: str
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]
    graph_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sha256: str,
        nodes: Iterable[EvidenceNode],
        edges: Iterable[EvidenceEdge] = (),
    ) -> "EvidenceGraph":
        source = _require_sha256(source_sha256, "source_sha256")
        ordered_nodes = tuple(sorted(nodes, key=lambda item: item.node_id))
        ordered_edges = tuple(sorted(edges, key=lambda item: item.edge_id))
        node_ids = {node.node_id for node in ordered_nodes}
        if len(node_ids) != len(ordered_nodes):
            raise EvidenceGraphError("Evidence graph contains duplicate node IDs")
        logical_ids = {node.logical_id for node in ordered_nodes}
        if len(logical_ids) != len(ordered_nodes):
            raise EvidenceGraphError("Evidence graph contains duplicate logical IDs")
        if len({edge.edge_id for edge in ordered_edges}) != len(ordered_edges):
            raise EvidenceGraphError("Evidence graph contains duplicate edge IDs")
        for node in ordered_nodes:
            if node.source_sha256 != source:
                raise EvidenceGraphError("Evidence node belongs to another source")
        for edge in ordered_edges:
            if edge.source_sha256 != source:
                raise EvidenceGraphError("Evidence edge belongs to another source")
            referenced = {edge.source_node_id, edge.target_node_id, *edge.evidence_node_ids}
            missing = referenced - node_ids
            if missing:
                raise EvidenceGraphError(f"Evidence edge references missing nodes: {sorted(missing)}")
        digest = _graph_sha256(source, ordered_nodes, ordered_edges)
        return cls(source, ordered_nodes, ordered_edges, digest)

    @property
    def node_ids(self) -> frozenset[str]:
        return frozenset(node.node_id for node in self.nodes)

    @property
    def logical_index(self) -> dict[str, EvidenceNode]:
        return {node.logical_id: node for node in self.nodes}

    def write_json(self, path: str | Path) -> Path:
        """Stream the graph to ``path`` without materialising a graph dict.

        The emitted bytes are identical to
        ``json.dumps(self.to_dict(), ensure_ascii=False, indent=2)`` (which is
        how ``pipeline._write_manifest`` serialises this artifact).  Nodes and
        edges are serialised one at a time in their stored sorted order.
        """
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as file:
                file.write("{\n")
                file.write("  " + _json_scalar("schema_version") + ": ")
                file.write(_json_scalar(EVIDENCE_GRAPH_SCHEMA) + ",\n")
                file.write("  " + _json_scalar("source_sha256") + ": ")
                file.write(_json_scalar(self.source_sha256) + ",\n")
                file.write("  " + _json_scalar("graph_sha256") + ": ")
                file.write(_json_scalar(self.graph_sha256) + ",\n")
                file.write('  "nodes": [\n')
                for index, node in enumerate(self.nodes):
                    if index:
                        file.write(",\n")
                    serialized = json.dumps(
                        node.to_dict(), ensure_ascii=False, indent=2,
                    )
                    _write_indented_object(file, serialized, 4)
                file.write("\n  ],\n")
                file.write('  "edges": [\n')
                for index, edge in enumerate(self.edges):
                    if index:
                        file.write(",\n")
                    serialized = json.dumps(
                        edge.to_dict(), ensure_ascii=False, indent=2,
                    )
                    _write_indented_object(file, serialized, 4)
                file.write("\n  ]\n")
                file.write("}")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EVIDENCE_GRAPH_SCHEMA,
            "source_sha256": self.source_sha256,
            "graph_sha256": self.graph_sha256,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceGraph":
        if payload.get("schema_version") != EVIDENCE_GRAPH_SCHEMA:
            raise EvidenceGraphError("Unsupported evidence graph schema")
        raw_nodes = payload.get("nodes")
        raw_edges = payload.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise EvidenceGraphError("Evidence graph nodes and edges must be arrays")
        graph = cls.create(
            source_sha256=str(payload.get("source_sha256", "")),
            nodes=(EvidenceNode.from_dict(item) for item in raw_nodes),
            edges=(EvidenceEdge.from_dict(item) for item in raw_edges),
        )
        if payload.get("graph_sha256") != graph.graph_sha256:
            raise EvidenceGraphError("Evidence graph digest does not match payload")
        return graph


def _style_facts(style: Any) -> dict[str, Any]:
    return {
        name: getattr(style, name)
        for name in (
            "aci_color", "true_color", "linetype", "lineweight", "rotation",
            "entity_aci_color", "layer_aci_color", "entity_true_color",
            "layer_true_color", "entity_linetype", "layer_linetype",
            "entity_lineweight", "layer_lineweight",
        )
        if hasattr(style, name)
    }


def build_stage_evidence_graph(
    *,
    source_sha256: str,
    entities: Iterable[Any],
    features: Iterable[Any],
    relations: Iterable[Any],
    unresolved: Iterable[Mapping[str, Any]] = (),
) -> EvidenceGraph:
    """Build a stable graph from the existing deterministic stage boundary."""

    source = _require_sha256(source_sha256, "source_sha256")
    nodes: list[EvidenceNode] = []
    node_by_logical: dict[str, EvidenceNode] = {}

    def add_node(logical_id: str, kind: str, facts: Mapping[str, Any]) -> EvidenceNode:
        node = EvidenceNode.create(
            source_sha256=source, logical_id=logical_id, kind=kind, facts=facts,
        )
        if logical_id in node_by_logical:
            raise EvidenceGraphError(f"Duplicate stage logical ID: {logical_id!r}")
        node_by_logical[logical_id] = node
        nodes.append(node)
        return node

    entity_order = sorted(entities, key=lambda item: str(item.entity_key))
    for entity in entity_order:
        add_node(entity.entity_key, "source_entity", {
            "handle": entity.handle,
            "layout": entity.layout,
            "layout_role": entity.layout_role,
            "cad_role": entity.cad_role,
            "layer": entity.layer,
            "object_name": entity.object_name,
            "dwg_type": entity.dwg_type,
            "points": entity.points,
            "centroid": entity.centroid,
            "closed": entity.closed,
            "text": entity.text,
            "block_name": entity.block_name,
            "block_attributes": entity.block_attributes,
            "style": _style_facts(entity.style),
            "dimension_value": entity.dimension_value,
            "dimension_text_override": entity.dimension_text_override,
            "native_length": entity.native_length,
            "owner_handle": entity.owner_handle,
            "scale": entity.scale,
            "curve_facts": entity.curve_facts,
            "curve_fingerprint": entity.curve_fingerprint,
            "raw_properties": entity.raw_properties,
        })
    del entity_order

    feature_source_pairs: list[tuple[str, str]] = []
    feature_order = sorted(features, key=lambda item: str(item.feature_key))
    for feature in feature_order:
        add_node(feature.feature_key, "feature", {
            "feature_class": feature.feature_class,
            "geometry_kind": feature.geometry_kind,
            "native_points": feature.native_points,
            "source_entity_key": feature.source_entity_key,
            "source_handle": feature.source_handle,
            "source_layer": feature.source_layer,
            "geometry_role": feature.geometry_role,
            "style": _style_facts(feature.style),
            "attributes": feature.attributes,
            "display_label": feature.display_label,
            "label_provenance": feature.label_provenance,
            "field_provenance": feature.field_provenance,
            "lineage": feature.lineage,
        })
        feature_source_pairs.append((feature.feature_key, feature.source_entity_key))
    del feature_order

    unresolved_nodes: list[EvidenceNode] = []
    for index, record in enumerate(unresolved):
        logical_id = f"unresolved:{index}:{_sha256_bytes(_canonical_json(record).encode('utf-8'))[:16]}"
        unresolved_nodes.append(add_node(logical_id, "unresolved", record))

    def reference_node(logical_id: str) -> EvidenceNode:
        existing = node_by_logical.get(logical_id)
        if existing is not None:
            return existing
        return add_node(
            logical_id,
            "external_reference",
            {"status": "unresolved_reference", "logical_id": logical_id},
        )

    edges: list[EvidenceEdge] = []
    for feature_key, source_entity_key in feature_source_pairs:
        feature_node = reference_node(feature_key)
        entity_node = reference_node(source_entity_key)
        edges.append(EvidenceEdge.create(
            source_sha256=source,
            kind="derived_from",
            source_node_id=feature_node.node_id,
            target_node_id=entity_node.node_id,
            evidence_node_ids=(entity_node.node_id,),
        ))

    for relation in sorted(relations, key=lambda item: str(item.relation_key)):
        source_node = reference_node(str(relation.source_key))
        target_node = reference_node(str(relation.target_key))
        evidence_nodes = [reference_node(str(item)).node_id for item in relation.evidence_keys]
        edges.append(EvidenceEdge.create(
            source_sha256=source,
            kind=str(relation.relation_kind),
            source_node_id=source_node.node_id,
            target_node_id=target_node.node_id,
            evidence_node_ids=evidence_nodes,
            facts={
                "relation_key": relation.relation_key,
                "status": relation.status,
                "method": relation.method,
                "distance_native_m": relation.distance_native_m,
            },
        ))

    # Unresolved records remain nodes because their shape is source/profile
    # specific; they are selectable as evidence but never executable targets.
    _ = unresolved_nodes
    # Build-time duplicate lookup is complete: release the logical-id index and
    # consumed sort/pair tables before EvidenceGraph.create computes the
    # streamed graph digest, so they never coexist with digest-time work.
    node_by_logical.clear()
    del node_by_logical, feature_source_pairs, unresolved_nodes
    return EvidenceGraph.create(source_sha256=source, nodes=nodes, edges=edges)


__all__ = [
    "EVIDENCE_EDGE_SCHEMA",
    "EVIDENCE_GRAPH_SCHEMA",
    "EVIDENCE_NODE_SCHEMA",
    "EvidenceEdge",
    "EvidenceGraph",
    "EvidenceGraphError",
    "EvidenceNode",
    "build_stage_evidence_graph",
]
