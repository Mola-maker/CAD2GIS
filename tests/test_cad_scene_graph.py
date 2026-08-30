from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cad2gis.cad2gis_v3.cad_scene_graph import (
    CadSceneGraph,
    CadSceneGraphError,
    build_cad_scene_graph,
)
from cad2gis.cad2gis_v3.model import CadStyle, SourceEntity


SOURCE_SHA256 = "b" * 64


def _entity(
    key: str,
    *,
    handle: str,
    layout: str = "Model",
    layout_role: str = "model",
    cad_role: str = "model",
    dwg_type: str = "LINE",
    block_name: str = "",
    owner_handle: str = "",
    text: str = "",
    raw_properties: dict | None = None,
) -> SourceEntity:
    return SourceEntity(
        entity_key=key,
        source_sha256=SOURCE_SHA256,
        source_file="unseen-vendor.dwg",
        handle=handle,
        layout=layout,
        layout_role=layout_role,
        cad_role=cad_role,
        layer="NETWORK",
        object_name=f"AcDb{dwg_type.title()}",
        dwg_type=dwg_type,
        points=((1.0, 2.0), (3.0, 4.0)),
        centroid=(2.0, 3.0),
        closed=False,
        text=text,
        block_name=block_name,
        block_attributes={},
        style=CadStyle(aci_color=3),
        owner_handle=owner_handle,
        raw_properties=dict(raw_properties or {}),
    )


def _graph() -> CadSceneGraph:
    root = _entity(
        "root-insert",
        handle="10",
        dwg_type="INSERT",
        block_name="CABINET",
        raw_properties={"block_reference_name": "CABINET"},
    )
    definition = _entity(
        "definition-line",
        handle="20",
        layout="BLOCKDEF:CABINET",
        layout_role="block_definition",
        cad_role="block_definition",
    )
    attribute = _entity(
        "attribute",
        handle="30",
        dwg_type="ATTRIB",
        owner_handle="10",
        text="CAB-01",
    )
    derived = replace(
        definition,
        entity_key="derived-line",
        layout="Model",
        layout_role="plan",
        cad_role="model",
        raw_properties={
            **definition.raw_properties,
            "plan_domain": {
                "schema_version": "cad2gis-plan-domain-v1",
                "materialization": "nested-insert-affine",
                "root_entity_key": "root-insert",
                "definition_entity_key": "definition-line",
                "instance_path": ["root-insert"],
                "affine": {
                    "m11": 1.0,
                    "m12": 0.0,
                    "m21": 0.0,
                    "m22": 1.0,
                    "tx": 100.0,
                    "ty": 200.0,
                },
            },
        },
    )
    return build_cad_scene_graph(
        source_sha256=SOURCE_SHA256,
        source_entities=[root, definition, attribute],
        plan_entities=[root, attribute, derived],
    )


def test_scene_graph_conserves_source_and_plan_views() -> None:
    graph = _graph()

    assert graph.diagnostics["source_entity_count"] == 3
    assert graph.diagnostics["source_entity_node_count"] == 3
    assert graph.diagnostics["source_conserved"] is True
    assert graph.diagnostics["plan_entity_count"] == 3
    assert graph.diagnostics["plan_entity_node_count"] == 3
    assert graph.diagnostics["plan_conserved"] is True
    assert graph.diagnostics["authority"]["semantic_status"] == "unclassified"
    assert graph.diagnostics["authority"]["ai_may_rank_existing_ids_only"] is True


def test_scene_graph_preserves_definition_instance_and_owner_relations() -> None:
    graph = _graph()
    kinds = [edge.kind for edge in graph.edges]

    assert "definition_contains" in kinds
    assert "instantiates" in kinds
    assert "owner_contains" in kinds
    assert "materializes_to_plan" in kinds
    assert "root_context_for" in kinds
    assert "instance_path_member" in kinds


def test_scene_graph_is_order_independent_and_round_trips() -> None:
    graph = _graph()
    rebuilt = CadSceneGraph.from_dict(graph.to_dict())

    assert rebuilt.graph_sha256 == graph.graph_sha256
    assert rebuilt.to_dict() == graph.to_dict()


def test_scene_graph_rejects_tampered_fact() -> None:
    payload = _graph().to_dict()
    payload["nodes"][0]["facts"]["tampered"] = True

    with pytest.raises(CadSceneGraphError, match="content address"):
        CadSceneGraph.from_dict(payload)


def test_scene_graph_rejects_duplicate_source_keys() -> None:
    entity = _entity("duplicate", handle="1")

    with pytest.raises(CadSceneGraphError, match="duplicate entity keys"):
        build_cad_scene_graph(
            source_sha256=SOURCE_SHA256,
            source_entities=[entity, entity],
            plan_entities=[entity],
        )


def test_mcp_pages_and_reads_scene_nodes(tmp_path, monkeypatch) -> None:
    from cad2gis import agent_mcp

    graph = _graph()
    graph_path = tmp_path / "cad_scene_graph.json"
    graph_path.write_text(
        json.dumps(graph.to_dict()),
        encoding="utf-8",
    )
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOTS", raising=False)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))

    page = agent_mcp.list_cad_scene_nodes(
        str(graph_path), kind="source_entity", limit=2,
    )

    assert page["total"] == 3
    assert len(page["nodes"]) == 2
    assert page["next_cursor"] == 2
    node = agent_mcp.get_cad_scene_node(
        str(graph_path), page["nodes"][0]["node_id"],
    )
    assert node["facts_sha256"] == page["nodes"][0]["facts_sha256"]
