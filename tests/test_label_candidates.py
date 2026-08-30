"""Tests for deterministic label-attachment candidate generation (v2)."""

from __future__ import annotations

import hashlib
import json

import pytest

from cad2gis.agent_mcp import (
    MCPServiceError,
    list_label_candidates,
    list_legend_catalog_candidates,
)
from cad2gis.cad2gis_v3.evidence_graph import build_stage_evidence_graph
from cad2gis.cad2gis_v3.label_candidates import (
    LABEL_CANDIDATES_SCHEMA_VERSION,
    generate_label_candidates,
)
from cad2gis.cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis.cad2gis_v3.scene_partition import LEGEND_CANDIDATES_SCHEMA_VERSION


SOURCE_SHA = "d" * 64


def _entity(
    key: str,
    point: tuple[float, float],
    *,
    dwg_type: str = "TEXT",
    text: str = "LABEL",
    layer: str = "LABELS",
    block_name: str = "",
    points: tuple[tuple[float, float], ...] | None = None,
) -> SourceEntity:
    return SourceEntity(
        entity_key=key,
        source_sha256=SOURCE_SHA,
        source_file="label-fixture.dwg",
        handle=key,
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer=layer,
        object_name=f"AcDb{dwg_type.title()}",
        dwg_type=dwg_type,
        points=points if points is not None else (point,),
        centroid=point,
        closed=False,
        text=text,
        block_name=block_name,
        block_attributes={},
        style=CadStyle(),
    )


def _feature(
    key: str,
    point: tuple[float, float],
    *,
    feature_class: str = "POLE",
    geometry_kind: str = "Point",
    source_entity_key: str = "entity:symbol",
) -> Feature:
    return Feature(
        feature_key=key,
        feature_class=feature_class,
        geometry_kind=geometry_kind,
        native_points=[point],
        source_entity_key=source_entity_key,
        source_handle="handle",
        source_layer="SYMBOLS",
        geometry_role="SOURCE",
        style=CadStyle(),
    )


def _span_line() -> SourceEntity:
    """Far-apart line that fixes the drawing span near 1000 (eps ~ 14)."""
    return _entity(
        "entity:span-line",
        (500.0, 0.0),
        dwg_type="LINE",
        text="",
        layer="ROUTES",
        points=((0.0, 0.0), (1000.0, 0.0)),
    )


def _candidate(result: dict, feature_key: str) -> dict:
    return next(
        item for item in result["candidates"] if item["feature_key"] == feature_key
    )


def test_self_text_pairs_only_with_own_feature() -> None:
    own = _feature(
        "feature:homepass", (0.0, 0.0), source_entity_key="entity:self-label",
    )
    other = _feature(
        "feature:pole", (2.0, 0.0), source_entity_key="entity:symbol-b",
    )
    self_label = _entity("entity:self-label", (0.0, 0.0), text="IMB-001")
    free_text = _entity("entity:free", (0.5, 0.0), text="FREE")

    result = generate_label_candidates(
        [own, other], [self_label, free_text, _span_line()],
    )

    # The self-mapped text never enters the free pool.
    assert result["stats"]["text_entity_count"] == 1
    own_options = _candidate(result, "feature:homepass")["options"]
    assert own_options[0] == {
        "rank": 0,
        "text_entity_key": "entity:self-label",
        "text": "IMB-001",
        "text_layer": "LABELS",
        "distance": 0.0,
        "self_text": True,
        "feature_is_text_nearest": True,
        "mutual_nearest": True,
    }
    assert [option["text_entity_key"] for option in own_options[1:]] == [
        "entity:free",
    ]
    other_keys = [
        option["text_entity_key"]
        for option in _candidate(result, "feature:pole")["options"]
    ]
    assert "entity:self-label" not in other_keys
    assert other_keys == ["entity:free"]
    assert {
        (pair["feature_key"], pair["text_entity_key"])
        for pair in result["mutual_pairs"]
    } == {
        ("feature:homepass", "entity:self-label"),
        ("feature:homepass", "entity:free"),
    }


def test_options_are_distance_ranked_and_capped() -> None:
    feature = _feature("feature:pole", (0.0, 0.0))
    texts = [
        _entity(f"entity:text-{index}", (float(index), 0.0), text=f"T{index}")
        for index in range(1, 6)
    ]

    result = generate_label_candidates([feature], [*texts, _span_line()])

    assert result["candidate_count"] == 1
    options = _candidate(result, "feature:pole")["options"]
    assert result["stats"]["max_options"] == 6
    assert [option["rank"] for option in options] == [0, 1, 2, 3, 4]
    assert [option["distance"] for option in options] == pytest.approx(
        [1.0, 2.0, 3.0, 4.0, 5.0],
    )
    assert [option["text_entity_key"] for option in options] == [
        "entity:text-1",
        "entity:text-2",
        "entity:text-3",
        "entity:text-4",
        "entity:text-5",
    ]
    assert all(not option["self_text"] for option in options)
    assert result["stats"]["eps"] == pytest.approx(
        result["stats"]["coordinate_span"] * 0.015,
    )


def test_mutual_nearest_flags_track_both_directions() -> None:
    near = _feature(
        "feature:near", (0.0, 0.0), source_entity_key="entity:symbol-a",
    )
    far = _feature(
        "feature:far", (1.0, 0.0), source_entity_key="entity:symbol-b",
    )
    text = _entity("entity:label", (0.4, 0.0), text="ID-1")

    result = generate_label_candidates([near, far], [text, _span_line()])

    near_option = _candidate(result, "feature:near")["options"][0]
    assert near_option["text_entity_key"] == "entity:label"
    assert near_option["feature_is_text_nearest"] is True
    assert near_option["mutual_nearest"] is True
    far_option = _candidate(result, "feature:far")["options"][0]
    assert far_option["text_entity_key"] == "entity:label"
    assert far_option["feature_is_text_nearest"] is False
    assert far_option["mutual_nearest"] is False
    assert result["mutual_pairs"] == [
        {"feature_key": "feature:near", "text_entity_key": "entity:label"},
    ]


def test_mutual_nearest_requires_rank_zero() -> None:
    feature = _feature("feature:pole", (0.0, 0.0))
    nearest = _entity("entity:nearest", (1.0, 0.0), text="NEAR")
    second = _entity("entity:second", (2.0, 0.0), text="SECOND")

    result = generate_label_candidates(
        [feature], [nearest, second, _span_line()],
    )

    options = _candidate(result, "feature:pole")["options"]
    # Both texts point at the only feature, but only rank 0 is mutual.
    assert [option["feature_is_text_nearest"] for option in options] == [
        True,
        True,
    ]
    assert [option["mutual_nearest"] for option in options] == [True, False]
    assert result["mutual_pairs"] == [
        {"feature_key": "feature:pole", "text_entity_key": "entity:nearest"},
    ]


def test_text_beyond_eps_produces_no_candidate() -> None:
    feature = _feature("feature:pole", (0.0, 0.0))
    distant = _entity("entity:distant", (20.0, 0.0), text="TOO-FAR")

    result = generate_label_candidates([feature], [distant, _span_line()])

    assert 0.0 < result["stats"]["eps"] < 20.0
    assert result["candidates"] == []
    assert result["candidate_count"] == 0
    assert result["mutual_pairs"] == []


def test_blank_text_and_non_text_types_are_skipped() -> None:
    feature = _feature("feature:pole", (0.0, 0.0))
    blank = _entity("entity:blank", (1.0, 0.0), text="   ")
    line = _entity(
        "entity:labelled-line",
        (1.0, 0.0),
        dwg_type="LINE",
        text="NOT-A-LABEL-CARRIER",
    )

    result = generate_label_candidates([feature], [blank, line, _span_line()])

    assert result["candidates"] == []
    assert result["stats"]["text_entity_count"] == 0


def test_non_point_features_are_ignored() -> None:
    line_feature = _feature(
        "feature:cable",
        (0.0, 0.0),
        feature_class="CABLE",
        geometry_kind="LineString",
        source_entity_key="entity:cable-line",
    )
    text = _entity("entity:near", (1.0, 0.0), text="NEAR")

    result = generate_label_candidates([line_feature], [text, _span_line()])

    assert result["candidates"] == []
    assert result["stats"]["point_feature_count"] == 0
    assert result["stats"]["text_entity_count"] == 1


def test_candidates_sorted_and_content_addressed() -> None:
    first = _feature(
        "feature:b", (0.0, 0.0), feature_class="POLE",
        source_entity_key="entity:symbol-b",
    )
    second = _feature(
        "feature:a", (0.0, 0.0), feature_class="FAT",
        source_entity_key="entity:symbol-a",
    )
    text = _entity("entity:label", (0.5, 0.0), text="ID")

    result = generate_label_candidates(
        [first, second], [text, _span_line()],
    )

    assert [
        (item["feature_class"], item["feature_key"])
        for item in result["candidates"]
    ] == [("FAT", "feature:a"), ("POLE", "feature:b")]
    assert result["candidates"][0]["candidate_id"] == hashlib.sha256(
        b"feature:a"
    ).hexdigest()[:16]
    assert result["candidates"][0]["feature_entity_key"] == "entity:symbol-a"


def _mcp_fixture() -> tuple[list[SourceEntity], list[Feature]]:
    entities = [
        _entity("entity:pole-id", (1.0, 0.0), text="MR.KLK5.P027"),
        _span_line(),
        *[
            _entity(
                f"entity:legend-{index}",
                (500.0, 500.0 + index * 0.1),
                dwg_type="INSERT",
                text="",
                layer="SYMBOL SAMPLES",
                block_name="FAT_Info",
            )
            for index in range(3)
        ],
    ]
    features = [_feature("feature:pole", (0.0, 0.0))]
    return entities, features


def _write_graph(tmp_path, monkeypatch) -> str:
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOTS", raising=False)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    entities, features = _mcp_fixture()
    graph = build_stage_evidence_graph(
        source_sha256=SOURCE_SHA,
        entities=entities,
        features=features,
        relations=[],
        unresolved=[],
    )
    graph_path = tmp_path / "reasoning" / "evidence_graph.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps(graph.to_dict()), encoding="utf-8")
    return str(graph_path)


def test_mcp_list_label_candidates_from_evidence_graph(
    tmp_path, monkeypatch,
) -> None:
    graph_path = _write_graph(tmp_path, monkeypatch)

    result = list_label_candidates(graph_path)

    assert result["schema_version"] == LABEL_CANDIDATES_SCHEMA_VERSION
    assert result["schema_version"] == "cad2gis-label-candidates-v2"
    assert "evidence_graph_sha256" in result
    assert result["candidate_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["feature_key"] == "feature:pole"
    option = candidate["options"][0]
    assert option["rank"] == 0
    assert option["text"] == "MR.KLK5.P027"
    assert option["self_text"] is False
    assert option["mutual_nearest"] is True
    assert result["mutual_pairs"] == [
        {"feature_key": "feature:pole", "text_entity_key": "entity:pole-id"},
    ]


def test_mcp_list_legend_catalog_candidates_from_evidence_graph(
    tmp_path, monkeypatch,
) -> None:
    graph_path = _write_graph(tmp_path, monkeypatch)

    result = list_legend_catalog_candidates(graph_path)

    assert result["schema_version"] == LEGEND_CANDIDATES_SCHEMA_VERSION
    assert "evidence_graph_sha256" in result
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "same_symbol_column_subset"
    assert candidate["member_count"] == 3


def test_mcp_list_legend_catalog_candidates_rejects_bad_regex(
    tmp_path, monkeypatch,
) -> None:
    graph_path = _write_graph(tmp_path, monkeypatch)

    with pytest.raises(MCPServiceError, match="route_regex"):
        list_legend_catalog_candidates(graph_path, route_regex="(")
