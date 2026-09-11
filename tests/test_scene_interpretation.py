from __future__ import annotations

import json

import pytest

from cad2gis.cad2gis_v3.cad_scene_graph import build_cad_scene_graph
from cad2gis.cad2gis_v3.model import CadStyle, SourceEntity
from cad2gis.cad2gis_v3.scene_interpretation import (
    SCENE_ASSIGNMENT_SCHEMA,
    SceneInterpretationError,
    SceneInterpretationPlan,
    SceneRoleAssignment,
)
from cad2gis.cad2gis_v3.scene_visual import build_scene_visual_bundle


SOURCE_SHA = "d" * 64


def _artifacts():
    entity = SourceEntity(
        entity_key="title",
        source_sha256=SOURCE_SHA,
        source_file="fixture.dwg",
        handle="10",
        layout="Layout1",
        layout_role="paper",
        cad_role="paper",
        layer="TITLE",
        object_name="AcDbText",
        dwg_type="TEXT",
        points=((10.0, 20.0),),
        centroid=(10.0, 20.0),
        closed=False,
        text="PROJECT TITLE",
        block_name="",
        block_attributes={},
        style=CadStyle(),
    )
    graph = build_cad_scene_graph(
        source_sha256=SOURCE_SHA,
        source_entities=[entity],
        plan_entities=[entity],
    )
    visual = build_scene_visual_bundle(graph=graph, entities=[entity])
    manifest = json.loads(visual.manifest_file.content)
    source_node = next(node for node in graph.nodes if node.kind == "source_entity")
    region_id = manifest["regions"][0]["region_id"]
    return graph, visual, manifest, source_node.node_id, region_id


def _plan():
    graph, visual, manifest, node_id, region_id = _artifacts()
    assignment = SceneRoleAssignment.from_dict({
        "schema_version": SCENE_ASSIGNMENT_SCHEMA,
        "action": "rank_scene_role",
        "target_node_id": node_id,
        "role": "title_block",
        "confidence": 0.98,
        "evidence_region_ids": [region_id],
        "rationale": "Paper-layout title text and region structure agree.",
    })
    plan = SceneInterpretationPlan.create(
        source_sha256=graph.source_sha256,
        cad_scene_graph_sha256=graph.graph_sha256,
        scene_visual_manifest_sha256=visual.manifest_file.sha256,
        assignments=[assignment],
        producer={"provider": "fixture"},
    )
    return graph, visual, manifest, plan


def test_scene_plan_is_hash_bound_and_validates_existing_ids() -> None:
    graph, visual, manifest, plan = _plan()

    validation = plan.validate_against(
        graph,
        manifest,
        visual_manifest_sha256=visual.manifest_file.sha256,
    )

    assert validation["valid"] is True
    assert validation["source_entities_deleted"] is False
    assert validation["unassigned_source_entity_count"] == 0
    assert SceneInterpretationPlan.from_dict(plan.to_dict()) == plan


def test_scene_plan_rejects_invented_ids_and_regions() -> None:
    graph, visual, manifest, plan = _plan()
    assignment = plan.assignments[0]
    bad_node = SceneInterpretationPlan.create(
        source_sha256=graph.source_sha256,
        cad_scene_graph_sha256=graph.graph_sha256,
        scene_visual_manifest_sha256=visual.manifest_file.sha256,
        assignments=[SceneRoleAssignment(
            "invented-node", assignment.role, assignment.confidence,
            assignment.evidence_region_ids, assignment.rationale,
        )],
    )
    with pytest.raises(SceneInterpretationError, match="Unknown scene target"):
        bad_node.validate_against(
            graph, manifest,
            visual_manifest_sha256=visual.manifest_file.sha256,
        )

    bad_region = SceneInterpretationPlan.create(
        source_sha256=graph.source_sha256,
        cad_scene_graph_sha256=graph.graph_sha256,
        scene_visual_manifest_sha256=visual.manifest_file.sha256,
        assignments=[SceneRoleAssignment(
            assignment.target_node_id, assignment.role, assignment.confidence,
            ("invented-region",), assignment.rationale,
        )],
    )
    with pytest.raises(SceneInterpretationError, match="Unknown evidence region"):
        bad_region.validate_against(
            graph, manifest,
            visual_manifest_sha256=visual.manifest_file.sha256,
        )


def test_scene_assignment_rejects_geometry_like_extra_fields() -> None:
    _, _, _, node_id, region_id = _artifacts()
    with pytest.raises(SceneInterpretationError, match="keys must be exactly"):
        SceneRoleAssignment.from_dict({
            "schema_version": SCENE_ASSIGNMENT_SCHEMA,
            "action": "rank_scene_role",
            "target_node_id": node_id,
            "role": "title_block",
            "confidence": 0.9,
            "evidence_region_ids": [region_id],
            "rationale": "Looks like a title.",
            "coordinates": [1, 2],
        })


def test_mcp_scene_understanding_round_trip(tmp_path, monkeypatch) -> None:
    from cad2gis import agent_mcp

    graph, visual, manifest, node_id, region_id = _artifacts()
    graph_path = tmp_path / "review" / "cad_scene_graph.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(json.dumps(graph.to_dict()), encoding="utf-8")
    manifest_path = visual.write(tmp_path)
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOTS", raising=False)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))

    regions = agent_mcp.list_scene_visual_regions(str(manifest_path))
    assert regions["regions"][0]["region_id"] == region_id
    context = agent_mcp.get_scene_visual_region_context(
        str(manifest_path), region_id,
    )
    assert context["entities"][0]["node_id"] == node_id

    output_path = tmp_path / "review" / "scene_interpretation_plan.json"
    created = agent_mcp.create_scene_interpretation_plan(
        str(graph_path),
        str(manifest_path),
        [{
            "schema_version": SCENE_ASSIGNMENT_SCHEMA,
            "action": "rank_scene_role",
            "target_node_id": node_id,
            "role": "title_block",
            "confidence": 0.97,
            "evidence_region_ids": [region_id],
            "rationale": "Visual title region and CAD text facts agree.",
        }],
        output_path=str(output_path),
    )
    assert created["validation"]["valid"] is True
    assert output_path.is_file()
    assert agent_mcp.validate_scene_interpretation_plan(
        str(graph_path), str(manifest_path), created["plan"],
    )["valid"] is True
