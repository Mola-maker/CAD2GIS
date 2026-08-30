from __future__ import annotations

import json

from cad2gis.agent_mcp import list_visual_regions, resolve_visual_hit
from cad2gis.cad2gis_v3.evidence_graph import build_stage_evidence_graph
from cad2gis.cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis.cad2gis_v3.visual_evidence import (
    build_visual_evidence_bundle,
)


SOURCE_SHA = "e" * 64


def _fixture():
    model_line = SourceEntity(
        entity_key="entity:model-line",
        source_sha256=SOURCE_SHA,
        source_file="fixture.dwg",
        handle="10",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="CABLE",
        object_name="AcDbLine",
        dwg_type="LINE",
        points=((0.0, 0.0), (10.0, 10.0)),
        centroid=(5.0, 5.0),
        closed=False,
        text="",
        block_name="",
        block_attributes={},
        style=CadStyle(aci_color=3),
        native_length=14.142135623730951,
    )
    model_label = SourceEntity(
        entity_key="entity:model-label",
        source_sha256=SOURCE_SHA,
        source_file="fixture.dwg",
        handle="11",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="LABEL",
        object_name="AcDbText",
        dwg_type="TEXT",
        points=((5.0, 6.0),),
        centroid=(5.0, 6.0),
        closed=False,
        text="MR.DMPH.P104",
        block_name="",
        block_attributes={},
        style=CadStyle(aci_color=1),
    )
    paper_title = SourceEntity(
        entity_key="entity:paper-title",
        source_sha256=SOURCE_SHA,
        source_file="fixture.dwg",
        handle="12",
        layout="Layout1",
        layout_role="paper",
        cad_role="paper",
        layer="TITLE",
        object_name="AcDbText",
        dwg_type="TEXT",
        points=((1000.0, 1000.0),),
        centroid=(1000.0, 1000.0),
        closed=False,
        text="DRAWING TITLE",
        block_name="",
        block_attributes={},
        style=CadStyle(),
    )
    feature = Feature(
        feature_key="feature:model-line",
        feature_class="CABLE",
        geometry_kind="LINESTRING",
        native_points=list(model_line.points),
        source_entity_key=model_line.entity_key,
        source_handle=model_line.handle,
        source_layer=model_line.layer,
        geometry_role="SOURCE_ROUTE",
        style=model_line.style,
    )
    entities = [model_line, model_label, paper_title]
    graph = build_stage_evidence_graph(
        source_sha256=SOURCE_SHA,
        entities=entities,
        features=[feature],
        relations=[],
        unresolved=[],
    )
    return graph, entities, [feature]


def test_visual_bundle_is_deterministic_and_excludes_paper_space(tmp_path) -> None:
    graph, entities, features = _fixture()
    first = build_visual_evidence_bundle(
        graph=graph, entities=entities, features=features,
    )
    second = build_visual_evidence_bundle(
        graph=graph, entities=entities, features=features,
    )

    assert first.graph.graph_sha256 == second.graph.graph_sha256
    assert first.region_count == 5
    assert [(item.relative_path, item.sha256) for item in first.files] == [
        (item.relative_path, item.sha256) for item in second.files
    ]
    assert sum(
        node.kind == "render_region" for node in first.graph.nodes
    ) == 5

    manifest_path = first.write(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["model_space_only"] is True
    assert manifest["paper_space_excluded"] is True
    overview = json.loads(
        (tmp_path / "reasoning/visual/overview.hit-index.json").read_text(
            encoding="utf-8",
        )
    )
    logical_ids = {
        item["logical_id"] for item in overview["entries"].values()
    }
    assert "entity:model-line" in logical_ids
    assert "entity:model-label" in logical_ids
    assert "entity:paper-title" not in logical_ids


def test_mcp_visual_tools_list_regions_and_resolve_hit(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOTS", raising=False)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    graph, entities, features = _fixture()
    bundle = build_visual_evidence_bundle(
        graph=graph, entities=entities, features=features,
    )
    bundle.write(tmp_path)
    graph_path = tmp_path / "reasoning/evidence_graph.json"
    graph_path.write_text(
        json.dumps(bundle.graph.to_dict()), encoding="utf-8",
    )

    regions = list_visual_regions(str(graph_path))
    assert len(regions["regions"]) == 5
    overview = next(
        item for item in regions["regions"] if item["region_id"] == "overview"
    )
    assert overview["authority"] == "secondary_visual_evidence_only"

    index_path = tmp_path / overview["hit_index_path"]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    color = next(iter(index["entries"]))
    resolved = resolve_visual_hit(str(index_path), color)
    assert resolved["hit"] is True
    assert resolved["entity"]["node_id"].startswith("evn_")
