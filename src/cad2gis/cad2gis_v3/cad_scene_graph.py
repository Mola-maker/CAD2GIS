"""Source-bound structural CAD scene graph for multimodal reasoning.

The scene graph is built before semantic classification.  It preserves the
reader inventory and the deterministic plan-domain projection as separate
views, then adds only structural relations that can be derived without a
domain mapping or model guess.  AI clients may rank scene and semantic
candidates over these IDs; they may not replace the numeric source facts.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


CAD_SCENE_GRAPH_SCHEMA = "cad2gis.cad_scene_graph.v1"
CAD_SCENE_NODE_SCHEMA = "cad2gis.cad_scene_node.v1"
CAD_SCENE_EDGE_SCHEMA = "cad2gis.cad_scene_edge.v1"

_NODE_KINDS = frozenset({
    "layout",
    "block_definition",
    "style",
    "source_entity",
    "plan_entity",
})
_BLOCK_LAYOUT_PREFIX = "BLOCKDEF:"


class CadSceneGraphError(ValueError):
    """A CAD scene graph invariant was violated."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_sha256(value: Any, path: str) -> str:
    text = str(value)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise CadSceneGraphError(f"{path} must be a lowercase SHA-256 digest")
    return text


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CadSceneGraphError(f"{path} must be a non-empty string")
    return value.strip()


def _canonical(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CadSceneGraphError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CadSceneGraphError(f"{path} contains a non-string key")
            result[key] = _canonical(item, f"{path}.{key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [
            _canonical(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item, f"{path}[]") for item in value]
        return sorted(items, key=_canonical_json)
    raise CadSceneGraphError(
        f"{path} contains unsupported type {type(value).__name__}"
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _logical_id(prefix: str, value: str) -> str:
    digest = _sha256(value.encode("utf-8"))[:24]
    return f"{prefix}:{digest}"


@dataclass(frozen=True)
class CadSceneNode:
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
    ) -> "CadSceneNode":
        source = _require_sha256(source_sha256, "source_sha256")
        logical = _identifier(logical_id, "logical_id")
        if kind not in _NODE_KINDS:
            raise CadSceneGraphError(f"Unsupported scene node kind: {kind!r}")
        facts_json = _canonical_json(facts)
        facts_sha256 = _sha256(facts_json.encode("utf-8"))
        identity = {
            "schema_version": CAD_SCENE_NODE_SCHEMA,
            "source_sha256": source,
            "logical_id": logical,
            "kind": kind,
            "facts_sha256": facts_sha256,
        }
        node_id = f"csn_{_sha256(_canonical_json(identity).encode('utf-8'))}"
        return cls(node_id, source, logical, kind, facts_sha256, facts_json)

    @property
    def facts(self) -> dict[str, Any]:
        return json.loads(self._facts_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CAD_SCENE_NODE_SCHEMA,
            "node_id": self.node_id,
            "source_sha256": self.source_sha256,
            "logical_id": self.logical_id,
            "kind": self.kind,
            "facts_sha256": self.facts_sha256,
            "facts": self.facts,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CadSceneNode":
        if payload.get("schema_version") != CAD_SCENE_NODE_SCHEMA:
            raise CadSceneGraphError("Unsupported CAD scene node schema")
        facts = payload.get("facts")
        if not isinstance(facts, Mapping):
            raise CadSceneGraphError("CAD scene node facts must be an object")
        rebuilt = cls.create(
            source_sha256=str(payload.get("source_sha256", "")),
            logical_id=str(payload.get("logical_id", "")),
            kind=str(payload.get("kind", "")),
            facts=facts,
        )
        if payload.get("node_id") != rebuilt.node_id:
            raise CadSceneGraphError("CAD scene node content address mismatch")
        if payload.get("facts_sha256") != rebuilt.facts_sha256:
            raise CadSceneGraphError("CAD scene node facts digest mismatch")
        return rebuilt


@dataclass(frozen=True)
class CadSceneEdge:
    edge_id: str
    source_sha256: str
    kind: str
    source_node_id: str
    target_node_id: str
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
        facts: Mapping[str, Any] | None = None,
    ) -> "CadSceneEdge":
        source = _require_sha256(source_sha256, "source_sha256")
        relation_kind = _identifier(kind, "kind")
        source_node = _identifier(source_node_id, "source_node_id")
        target_node = _identifier(target_node_id, "target_node_id")
        facts_json = _canonical_json(dict(facts or {}))
        facts_sha256 = _sha256(facts_json.encode("utf-8"))
        identity = {
            "schema_version": CAD_SCENE_EDGE_SCHEMA,
            "source_sha256": source,
            "kind": relation_kind,
            "source_node_id": source_node,
            "target_node_id": target_node,
            "facts_sha256": facts_sha256,
        }
        edge_id = f"cse_{_sha256(_canonical_json(identity).encode('utf-8'))}"
        return cls(
            edge_id,
            source,
            relation_kind,
            source_node,
            target_node,
            facts_sha256,
            facts_json,
        )

    @property
    def facts(self) -> dict[str, Any]:
        return json.loads(self._facts_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CAD_SCENE_EDGE_SCHEMA,
            "edge_id": self.edge_id,
            "source_sha256": self.source_sha256,
            "kind": self.kind,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "facts_sha256": self.facts_sha256,
            "facts": self.facts,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CadSceneEdge":
        if payload.get("schema_version") != CAD_SCENE_EDGE_SCHEMA:
            raise CadSceneGraphError("Unsupported CAD scene edge schema")
        facts = payload.get("facts")
        if not isinstance(facts, Mapping):
            raise CadSceneGraphError("CAD scene edge facts must be an object")
        rebuilt = cls.create(
            source_sha256=str(payload.get("source_sha256", "")),
            kind=str(payload.get("kind", "")),
            source_node_id=str(payload.get("source_node_id", "")),
            target_node_id=str(payload.get("target_node_id", "")),
            facts=facts,
        )
        if payload.get("edge_id") != rebuilt.edge_id:
            raise CadSceneGraphError("CAD scene edge content address mismatch")
        if payload.get("facts_sha256") != rebuilt.facts_sha256:
            raise CadSceneGraphError("CAD scene edge facts digest mismatch")
        return rebuilt


@dataclass(frozen=True)
class CadSceneGraph:
    source_sha256: str
    nodes: tuple[CadSceneNode, ...]
    edges: tuple[CadSceneEdge, ...]
    diagnostics: dict[str, Any]
    graph_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sha256: str,
        nodes: Iterable[CadSceneNode],
        edges: Iterable[CadSceneEdge],
        diagnostics: Mapping[str, Any],
    ) -> "CadSceneGraph":
        source = _require_sha256(source_sha256, "source_sha256")
        ordered_nodes = tuple(sorted(nodes, key=lambda item: item.node_id))
        ordered_edges = tuple(sorted(edges, key=lambda item: item.edge_id))
        node_ids = {node.node_id for node in ordered_nodes}
        logical_ids = {node.logical_id for node in ordered_nodes}
        if len(node_ids) != len(ordered_nodes):
            raise CadSceneGraphError("CAD scene graph contains duplicate node IDs")
        if len(logical_ids) != len(ordered_nodes):
            raise CadSceneGraphError("CAD scene graph contains duplicate logical IDs")
        if len({edge.edge_id for edge in ordered_edges}) != len(ordered_edges):
            raise CadSceneGraphError("CAD scene graph contains duplicate edge IDs")
        for node in ordered_nodes:
            if node.source_sha256 != source:
                raise CadSceneGraphError("CAD scene node belongs to another source")
        for edge in ordered_edges:
            if edge.source_sha256 != source:
                raise CadSceneGraphError("CAD scene edge belongs to another source")
            missing = {edge.source_node_id, edge.target_node_id} - node_ids
            if missing:
                raise CadSceneGraphError(
                    f"CAD scene edge references missing nodes: {sorted(missing)}"
                )
        canonical_diagnostics = _canonical(dict(diagnostics))
        identity = {
            "schema_version": CAD_SCENE_GRAPH_SCHEMA,
            "source_sha256": source,
            "nodes": [node.to_dict() for node in ordered_nodes],
            "edges": [edge.to_dict() for edge in ordered_edges],
            "diagnostics": canonical_diagnostics,
        }
        graph_sha256 = _sha256(_canonical_json(identity).encode("utf-8"))
        return cls(
            source,
            ordered_nodes,
            ordered_edges,
            canonical_diagnostics,
            graph_sha256,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CAD_SCENE_GRAPH_SCHEMA,
            "source_sha256": self.source_sha256,
            "graph_sha256": self.graph_sha256,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CadSceneGraph":
        if payload.get("schema_version") != CAD_SCENE_GRAPH_SCHEMA:
            raise CadSceneGraphError("Unsupported CAD scene graph schema")
        raw_nodes = payload.get("nodes")
        raw_edges = payload.get("edges")
        diagnostics = payload.get("diagnostics")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise CadSceneGraphError("CAD scene graph nodes and edges must be arrays")
        if not isinstance(diagnostics, Mapping):
            raise CadSceneGraphError("CAD scene graph diagnostics must be an object")
        graph = cls.create(
            source_sha256=str(payload.get("source_sha256", "")),
            nodes=(CadSceneNode.from_dict(item) for item in raw_nodes),
            edges=(CadSceneEdge.from_dict(item) for item in raw_edges),
            diagnostics=diagnostics,
        )
        if payload.get("graph_sha256") != graph.graph_sha256:
            raise CadSceneGraphError("CAD scene graph digest mismatch")
        return graph


def _style_facts(style: Any) -> dict[str, Any]:
    return {
        name: getattr(style, name)
        for name in (
            "aci_color",
            "true_color",
            "linetype",
            "lineweight",
            "rotation",
            "entity_aci_color",
            "layer_aci_color",
            "entity_true_color",
            "layer_true_color",
            "entity_linetype",
            "layer_linetype",
            "entity_lineweight",
            "layer_lineweight",
        )
        if hasattr(style, name)
    }


def _definition_name(entity: Any) -> str:
    layout = str(entity.layout).strip()
    if layout.upper().startswith(_BLOCK_LAYOUT_PREFIX):
        return layout[len(_BLOCK_LAYOUT_PREFIX):].strip().upper()
    return str(entity.raw_properties.get("container_block_name", "")).strip().upper()


def _block_reference_name(entity: Any) -> str:
    return str(
        entity.raw_properties.get("block_effective_name")
        or entity.raw_properties.get("block_reference_name")
        or entity.block_name
        or ""
    ).strip().upper()


def _entity_facts(entity: Any, *, view: str) -> dict[str, Any]:
    plan_domain = entity.raw_properties.get("plan_domain")
    role_reclassification = entity.raw_properties.get("role_reclassification")
    return {
        "view": view,
        "entity_key": entity.entity_key,
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
        "owner_handle": entity.owner_handle,
        "dimension_value": entity.dimension_value,
        "dimension_text_override": entity.dimension_text_override,
        "native_length": entity.native_length,
        "curve_fingerprint": entity.curve_fingerprint,
        "style": _style_facts(entity.style),
        "plan_domain": plan_domain if isinstance(plan_domain, Mapping) else None,
        "role_reclassification": (
            role_reclassification
            if isinstance(role_reclassification, Mapping)
            else None
        ),
    }


def build_cad_scene_graph(
    *,
    source_sha256: str,
    source_entities: Iterable[Any],
    plan_entities: Iterable[Any],
) -> CadSceneGraph:
    """Build a deterministic pre-semantic scene graph with full conservation."""

    source = _require_sha256(source_sha256, "source_sha256")
    raw = tuple(sorted(source_entities, key=lambda item: str(item.entity_key)))
    plan = tuple(sorted(plan_entities, key=lambda item: str(item.entity_key)))
    raw_keys = [str(entity.entity_key) for entity in raw]
    plan_keys = [str(entity.entity_key) for entity in plan]
    if len(raw_keys) != len(set(raw_keys)):
        raise CadSceneGraphError("Source inventory contains duplicate entity keys")
    if len(plan_keys) != len(set(plan_keys)):
        raise CadSceneGraphError("Plan-domain view contains duplicate entity keys")
    if any(str(entity.source_sha256) != source for entity in (*raw, *plan)):
        raise CadSceneGraphError("Scene entity belongs to another source")

    nodes: list[CadSceneNode] = []
    edges: list[CadSceneEdge] = []
    edge_ids: set[str] = set()
    by_logical: dict[str, CadSceneNode] = {}

    def add_node(logical_id: str, kind: str, facts: Mapping[str, Any]) -> CadSceneNode:
        if logical_id in by_logical:
            return by_logical[logical_id]
        node = CadSceneNode.create(
            source_sha256=source,
            logical_id=logical_id,
            kind=kind,
            facts=facts,
        )
        by_logical[logical_id] = node
        nodes.append(node)
        return node

    def add_edge(
        kind: str,
        source_node: CadSceneNode,
        target_node: CadSceneNode,
        facts: Mapping[str, Any] | None = None,
    ) -> None:
        edge = CadSceneEdge.create(
            source_sha256=source,
            kind=kind,
            source_node_id=source_node.node_id,
            target_node_id=target_node.node_id,
            facts=facts,
        )
        if edge.edge_id not in edge_ids:
            edge_ids.add(edge.edge_id)
            edges.append(edge)

    layout_nodes: dict[str, CadSceneNode] = {}
    definition_nodes: dict[str, CadSceneNode] = {}
    style_nodes: dict[str, CadSceneNode] = {}
    raw_nodes: dict[str, CadSceneNode] = {}
    plan_nodes: dict[str, CadSceneNode] = {}

    for layout in sorted({str(entity.layout) for entity in raw}, key=str.casefold):
        layout_nodes[layout] = add_node(
            _logical_id("layout", layout),
            "layout",
            {"name": layout},
        )

    for entity in raw:
        definition = _definition_name(entity)
        if definition and definition not in definition_nodes:
            definition_nodes[definition] = add_node(
                _logical_id("definition", definition),
                "block_definition",
                {"name": definition},
            )

        style = _style_facts(entity.style)
        style_key = _sha256(_canonical_json(style).encode("utf-8"))
        style_node = style_nodes.get(style_key)
        if style_node is None:
            style_node = add_node(
                f"style:{style_key}",
                "style",
                style,
            )
            style_nodes[style_key] = style_node

        entity_node = add_node(
            f"source:{entity.entity_key}",
            "source_entity",
            _entity_facts(entity, view="source"),
        )
        raw_nodes[entity.entity_key] = entity_node
        add_edge("layout_contains", layout_nodes[entity.layout], entity_node)
        add_edge("has_style", entity_node, style_node)
        if definition:
            add_edge(
                "definition_contains",
                definition_nodes[definition],
                entity_node,
            )

    handle_index: dict[str, list[CadSceneNode]] = {}
    for entity in raw:
        if entity.handle:
            handle_index.setdefault(str(entity.handle).upper(), []).append(
                raw_nodes[entity.entity_key]
            )
    for entity in raw:
        if entity.owner_handle:
            owners = handle_index.get(str(entity.owner_handle).upper(), ())
            if len(owners) == 1:
                add_edge(
                    "owner_contains",
                    owners[0],
                    raw_nodes[entity.entity_key],
                    {"owner_handle": entity.owner_handle},
                )
        if str(entity.dwg_type).upper() == "INSERT":
            reference = _block_reference_name(entity)
            if reference in definition_nodes:
                add_edge(
                    "instantiates",
                    raw_nodes[entity.entity_key],
                    definition_nodes[reference],
                    {"block_name": reference},
                )

    for entity in plan:
        style = _style_facts(entity.style)
        style_key = _sha256(_canonical_json(style).encode("utf-8"))
        style_node = style_nodes.get(style_key)
        if style_node is None:
            style_node = add_node(
                f"style:{style_key}",
                "style",
                style,
            )
            style_nodes[style_key] = style_node
        plan_node = add_node(
            f"plan:{entity.entity_key}",
            "plan_entity",
            _entity_facts(entity, view="plan"),
        )
        plan_nodes[entity.entity_key] = plan_node
        add_edge("has_style", plan_node, style_node)

        raw_node = raw_nodes.get(entity.entity_key)
        if raw_node is not None:
            add_edge("projects_to_plan", raw_node, plan_node)

        materialization = entity.raw_properties.get("plan_domain")
        if not isinstance(materialization, Mapping):
            continue
        definition_key = str(materialization.get("definition_entity_key", ""))
        root_key = str(materialization.get("root_entity_key", ""))
        if definition_key in raw_nodes:
            add_edge(
                "materializes_to_plan",
                raw_nodes[definition_key],
                plan_node,
                {"affine": materialization.get("affine")},
            )
        if root_key in raw_nodes:
            add_edge("root_context_for", raw_nodes[root_key], plan_node)
        instance_path = materialization.get("instance_path")
        if isinstance(instance_path, (list, tuple)):
            for index, member_key in enumerate(instance_path):
                member_node = raw_nodes.get(str(member_key))
                if member_node is not None:
                    add_edge(
                        "instance_path_member",
                        member_node,
                        plan_node,
                        {"index": index},
                    )

    diagnostics = {
        "source_entity_count": len(raw),
        "source_entity_node_count": len(raw_nodes),
        "source_conserved": len(raw) == len(raw_nodes),
        "plan_entity_count": len(plan),
        "plan_entity_node_count": len(plan_nodes),
        "plan_conserved": len(plan) == len(plan_nodes),
        "layout_count": len(layout_nodes),
        "block_definition_count": len(definition_nodes),
        "style_count": len(style_nodes),
        "authority": {
            "semantic_status": "unclassified",
            "geometry": "immutable_reader_and_plan_domain_facts",
            "ai_may_rank_existing_ids_only": True,
        },
    }
    if not diagnostics["source_conserved"] or not diagnostics["plan_conserved"]:
        raise CadSceneGraphError("CAD scene graph conservation failed")
    return CadSceneGraph.create(
        source_sha256=source,
        nodes=nodes,
        edges=edges,
        diagnostics=diagnostics,
    )


__all__ = [
    "CAD_SCENE_EDGE_SCHEMA",
    "CAD_SCENE_GRAPH_SCHEMA",
    "CAD_SCENE_NODE_SCHEMA",
    "CadSceneEdge",
    "CadSceneGraph",
    "CadSceneGraphError",
    "CadSceneNode",
    "build_cad_scene_graph",
]
