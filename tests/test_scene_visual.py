from __future__ import annotations

import json

from cad2gis.cad2gis_v3.cad_scene_graph import build_cad_scene_graph
from cad2gis.cad2gis_v3.model import CadStyle, SourceEntity
from cad2gis.cad2gis_v3.scene_visual import build_scene_visual_bundle


SOURCE_SHA = "c" * 64


def _entity(
    key: str,
    *,
    layout: str,
    role: str,
    point: tuple[float, float],
    text: str = "",
    kind: str = "TEXT",
) -> SourceEntity:
    return SourceEntity(
        entity_key=key,
        source_sha256=SOURCE_SHA,
        source_file="multi-layout.dwg",
        handle=key,
        layout=layout,
        layout_role=role,
        cad_role=role,
        layer="ANNOTATION" if text else "NETWORK",
        object_name=f"AcDb{kind.title()}",
        dwg_type=kind,
        points=(point,),
        centroid=point,
        closed=False,
        text=text,
        block_name="",
        block_attributes={},
        style=CadStyle(aci_color=3),
    )


def _fixture():
    entities = [
        _entity(
            f"model-{index}",
            layout="Model",
            role="model",
            point=(float(index % 30), float(index // 30)),
            kind="POINT",
        )
        for index in range(300)
    ]
    entities.extend((
        _entity(
            "paper-title",
            layout="Layout1",
            role="paper",
            point=(10.0, 10.0),
            text="PROJECT TITLE",
        ),
        _entity(
            "definition-label",
            layout="BLOCKDEF:POLE",
            role="block_definition",
            point=(0.0, 0.0),
            text="POLE TAG",
        ),
    ))
    graph = build_cad_scene_graph(
        source_sha256=SOURCE_SHA,
        source_entities=entities,
        plan_entities=entities[:300],
    )
    return graph, entities


def test_scene_visual_covers_all_layouts_and_is_deterministic(tmp_path) -> None:
    graph, entities = _fixture()

    first = build_scene_visual_bundle(graph=graph, entities=entities)
    second = build_scene_visual_bundle(graph=graph, entities=entities)

    assert first.render_conserved is True
    assert first.layout_count == 3
    assert first.region_count > first.layout_count
    assert [(item.relative_path, item.sha256) for item in first.files] == [
        (item.relative_path, item.sha256) for item in second.files
    ]
    manifest_path = first.write(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["cad_scene_graph_sha256"] == graph.graph_sha256
    assert manifest["render_conserved"] is True
    assert {item["layout"] for item in manifest["layouts"]} == {
        "Model", "Layout1", "BLOCKDEF:POLE",
    }
    paper_region = next(
        region for region in manifest["regions"]
        if region["layout"] == "Layout1"
    )
    region_context = json.loads(
        (tmp_path / paper_region["context_path"]).read_text(encoding="utf-8")
    )
    layout_context = json.loads(
        (tmp_path / paper_region["layout_context_path"]).read_text(encoding="utf-8")
    )
    assert region_context["entity_node_ids"] == [
        layout_context["entities"][0]["node_id"]
    ]
    assert layout_context["entities"][0]["text_values"][0]["value"] == (
        "PROJECT TITLE"
    )


def test_scene_visual_manifest_keeps_candidates_advisory() -> None:
    graph, entities = _fixture()
    bundle = build_scene_visual_bundle(
        graph=graph,
        entities=entities,
        scene_candidates={
            "status": "CANDIDATES_ONLY",
            "candidate_entity_keys": ["paper-title"],
            "automatic_exclusion_applied": False,
        },
    )

    manifest = json.loads(bundle.manifest_file.content)
    assert manifest["scene_candidates"]["automatic_exclusion_applied"] is False
    assert manifest["model_contract"]["scene_role_does_not_delete_source_entities"] is True
