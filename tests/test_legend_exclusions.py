"""Tests for legend candidate detection and declared legend exclusion."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.config import SourceProfile
from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.plan_domain import PlanDomainError, build_plan_domain
from cad2gis.cad2gis_v3.scene_partition import (
    LEGEND_CANDIDATES_SCHEMA_VERSION,
    detect_legend_candidates,
)


SOURCE_HASH = "c" * 64


def _entity(
    key: str,
    *,
    kind: str,
    layer: str,
    points: tuple[tuple[float, float], ...],
    block_name: str = "",
    cad_role: str = "model",
    layout: str = "Model",
    layout_role: str = "model",
    raw_properties: dict | None = None,
    native_length: float | None = None,
) -> SourceEntity:
    x = sum(point[0] for point in points) / len(points)
    y = sum(point[1] for point in points) / len(points)
    return SourceEntity.from_record({
        "entity_key": key,
        "source_sha256": SOURCE_HASH,
        "source_file": "legend-fixture.dwg",
        "handle": key,
        "layout": layout,
        "layout_role": layout_role,
        "cad_role": cad_role,
        "layer": layer,
        "object_name": f"ACDB{kind}",
        "dwg_type_name": kind,
        "points": points,
        "centroid": (x, y),
        "closed": False,
        "text": "",
        "block_name": block_name,
        "block_attributes": {},
        "raw_properties": raw_properties or {},
        "native_length": native_length,
    })


def _transform(insertion: tuple[float, float, float]) -> dict:
    return {
        "transform_facts": {
            "insertion_point": insertion,
            "block_base_point": (0.0, 0.0, 0.0),
            "scale": (1.0, 1.0, 1.0),
            "rotation": 0.0,
            "normal": (0.0, 0.0, 1.0),
            "extrusion": (0.0, 0.0, 1.0),
        }
    }


def _symbol_column(count: int, *, x: float = 10305.2) -> list[SourceEntity]:
    return [
        _entity(
            f"legend-symbol-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="FAT_Info",
            points=((x, 100.0 + index * 25.0),),
        )
        for index in range(count)
    ]


def test_legend_candidates_detect_same_symbol_column_subset() -> None:
    column = _symbol_column(5)
    route = _entity(
        "route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=((0.0, 0.0), (100.0, 0.0)),
        native_length=100.0,
    )

    result = detect_legend_candidates([*column, route])

    assert result["schema_version"] == LEGEND_CANDIDATES_SCHEMA_VERSION
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    keys = sorted(entity.entity_key for entity in column)
    assert candidate["kind"] == "same_symbol_column_subset"
    assert candidate["axis"] == "vertical"
    assert candidate["axis_range"] == pytest.approx(0.0)
    assert candidate["axis_range_ratio"] == pytest.approx(0.0)
    assert candidate["entity_keys"] == keys
    assert candidate["member_count"] == 5
    assert candidate["block_name"] == "FAT_Info"
    assert candidate["layer"] == "SYMBOL SAMPLES"
    assert candidate["group_id"] == hashlib.sha256(
        "|".join(keys).encode("utf-8")
    ).hexdigest()[:16]
    assert candidate["cluster_extent"]["min_x"] == pytest.approx(10305.2)
    assert candidate["cluster_diameter"] == pytest.approx(100.0)
    assert "align in a column" in candidate["review_hint"]
    assert result["candidate_entity_keys"] == keys


def test_legend_candidates_find_column_subset_inside_large_bucket() -> None:
    column = _symbol_column(5)
    scattered = [
        _entity(
            f"network-symbol-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="FAT_Info",
            points=(point,),
        )
        for index, point in enumerate([
            (1000.0, 5000.0),
            (3000.0, 1000.0),
            (6000.0, 8000.0),
            (9000.0, 3000.0),
        ])
    ]

    result = detect_legend_candidates([*column, *scattered])

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "same_symbol_column_subset"
    assert candidate["member_count"] == 5
    assert candidate["entity_keys"] == sorted(
        entity.entity_key for entity in column
    )
    assert result["candidate_entity_keys"] == candidate["entity_keys"]


def _loose_column() -> list[SourceEntity]:
    return [
        _entity(
            f"loose-column-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="FAT_Info",
            points=((490.0 + index * 5.0, 200.0 + index * 200.0),),
        )
        for index in range(4)
    ]


def test_legend_candidates_detect_loosely_aligned_column() -> None:
    column = _loose_column()
    route = _entity(
        "route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=((0.0, 0.0), (1000.0, 0.0)),
        native_length=1000.0,
    )

    result = detect_legend_candidates(
        [*column, route],
        route_pattern=re.compile(r"(?i)CABLE"),
    )

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "same_symbol_column_subset"
    assert candidate["axis"] == "vertical"
    assert candidate["member_count"] == 4
    assert candidate["axis_range"] == pytest.approx(15.0)
    assert candidate["axis_range_ratio"] == pytest.approx(
        15.0 / candidate["stats"]["coordinate_span"]
    )
    assert candidate["min_route_distance"] > 0.01 * (
        candidate["stats"]["coordinate_span"]
    )


def test_legend_candidates_outliers_do_not_inflate_span() -> None:
    column = _symbol_column(5, x=500.0)
    route = _entity(
        "route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=tuple((index * 10.0, 0.0) for index in range(96)),
        native_length=950.0,
    )
    stray = _entity(
        "stray-paper-entity",
        kind="TEXT",
        layer="PAPER NOTES",
        points=((4.2e6, -4.2e6),),
    )

    result = detect_legend_candidates(
        [*column, route, stray],
        route_pattern=re.compile(r"(?i)CABLE"),
    )

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "same_symbol_column_subset"
    assert candidate["member_count"] == 5
    assert candidate["stats"]["coordinate_span"] < 1000.0


def test_legend_candidates_reject_loose_column_next_to_route() -> None:
    column = _loose_column()
    nearby_route = _entity(
        "nearby-route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=((492.0, 202.0), (1000.0, 0.0)),
        native_length=1005.0,
    )

    result = detect_legend_candidates(
        [*column, nearby_route],
        route_pattern=re.compile(r"(?i)CABLE"),
    )

    assert result["candidates"] == []
    assert result["candidate_entity_keys"] == []


def test_legend_candidates_report_duplicate_stack_separately() -> None:
    stack = [
        _entity(
            f"stack-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="POLE",
            points=((209.7, -706.9),),
        )
        for index in range(4)
    ]
    route = _entity(
        "route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=((0.0, 0.0), (1000.0, 0.0)),
        native_length=1000.0,
    )

    result = detect_legend_candidates(
        [*stack, route],
        route_pattern=re.compile(r"(?i)CABLE"),
    )

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "duplicate_stack"
    assert candidate["axis"] is None
    assert candidate["axis_range"] is None
    assert candidate["member_count"] == 4
    assert candidate["entity_keys"] == sorted(
        entity.entity_key for entity in stack
    )
    assert candidate["cluster_diameter"] == pytest.approx(0.0)
    assert "same insertion point" in candidate["review_hint"]


def test_legend_candidates_stack_and_column_subset_are_mutually_exclusive() -> None:
    stack = [
        _entity(
            f"stack-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="POLE",
            points=((0.0, 0.0),),
        )
        for index in range(3)
    ]
    column = [
        _entity(
            f"column-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="POLE",
            points=((500.0, 100.0 + index * 50.0),),
        )
        for index in range(5)
    ]

    result = detect_legend_candidates([*stack, *column])

    kinds = {candidate["kind"] for candidate in result["candidates"]}
    assert kinds == {"duplicate_stack", "same_symbol_column_subset"}
    stack_candidate = next(
        candidate for candidate in result["candidates"]
        if candidate["kind"] == "duplicate_stack"
    )
    column_candidate = next(
        candidate for candidate in result["candidates"]
        if candidate["kind"] == "same_symbol_column_subset"
    )
    assert stack_candidate["member_count"] == 3
    assert column_candidate["member_count"] == 5
    assert not set(stack_candidate["entity_keys"]) & set(
        column_candidate["entity_keys"]
    )
    assert result["candidate_entity_keys"] == sorted(
        stack_candidate["entity_keys"] + column_candidate["entity_keys"]
    )


def test_legend_candidates_detect_same_symbol_cluster_away_from_routes() -> None:
    cluster = [
        _entity(
            f"legend-cluster-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="FAT_Info",
            points=((9000.0 + (index % 2) * 10.0, 9000.0 + (index // 2) * 10.0),),
        )
        for index in range(4)
    ]
    route = _entity(
        "route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=((0.0, 0.0), (5000.0, 0.0)),
        native_length=5000.0,
    )

    result = detect_legend_candidates(
        [*cluster, route],
        route_pattern=re.compile(r"(?i)CABLE"),
    )

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "same_symbol_cluster"
    assert candidate["axis"] is None
    assert candidate["min_route_distance"] > 0.01 * (
        candidate["stats"]["coordinate_span"]
    )
    assert candidate["cluster_diameter"] <= 0.02 * (
        candidate["stats"]["coordinate_span"]
    )


def test_legend_candidates_reject_cluster_next_to_route_geometry() -> None:
    cluster = [
        _entity(
            f"near-route-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="FAT_Info",
            points=((9000.0 + (index % 2) * 10.0, 9000.0 + (index // 2) * 10.0),),
        )
        for index in range(4)
    ]
    nearby_route = _entity(
        "nearby-route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=((8995.0, 8995.0), (5000.0, 0.0)),
        native_length=10061.0,
    )

    result = detect_legend_candidates(
        [*cluster, nearby_route],
        route_pattern=re.compile(r"(?i)CABLE"),
    )

    assert result["candidates"] == []
    assert result["candidate_entity_keys"] == []


def test_legend_candidates_without_route_pattern_skip_route_constraint() -> None:
    cluster = [
        _entity(
            f"near-route-{index}",
            kind="INSERT",
            layer="SYMBOL SAMPLES",
            block_name="FAT_Info",
            points=((9000.0 + (index % 2) * 10.0, 9000.0 + (index // 2) * 10.0),),
        )
        for index in range(4)
    ]
    nearby_route = _entity(
        "nearby-route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=((8995.0, 8995.0), (5000.0, 0.0)),
        native_length=10061.0,
    )

    result = detect_legend_candidates([*cluster, nearby_route])

    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "same_symbol_cluster"
    assert candidate["min_route_distance"] is None
    assert candidate["stats"]["route_entity_count"] == 0


def test_legend_candidates_require_three_identical_symbols() -> None:
    pair = _symbol_column(2)
    route = _entity(
        "route",
        kind="LWPOLYLINE",
        layer="CABLE ROUTE",
        points=((0.0, 0.0), (100.0, 0.0)),
        native_length=100.0,
    )

    result = detect_legend_candidates([*pair, route])

    assert result["candidates"] == []


def test_legend_candidates_ignore_mixed_block_layer_alignment() -> None:
    mixed = [
        _entity(
            f"mixed-{index}",
            kind="INSERT",
            layer=f"layer-{index}",
            block_name=f"block-{index}",
            points=((42.0, 100.0 + index * 25.0),),
        )
        for index in range(5)
    ]

    result = detect_legend_candidates(mixed)

    assert result["candidates"] == []


def _network_fixture() -> list[SourceEntity]:
    legend_root = _entity(
        "legend-root",
        kind="INSERT",
        layer="SYMBOL SAMPLES",
        block_name="LEGEND",
        points=((500.0, 500.0),),
        raw_properties=_transform((500.0, 500.0, 0.0)),
    )
    legend_member = _entity(
        "legend-member",
        kind="LINE",
        layer="SYMBOL SAMPLES",
        layout="BLOCKDEF:LEGEND",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (10.0, 0.0)),
        native_length=10.0,
    )
    network_line = _entity(
        "network-line",
        kind="LINE",
        layer="CABLE ROUTE",
        points=((0.0, 0.0), (100.0, 0.0)),
        native_length=100.0,
    )
    return [legend_root, legend_member, network_line]


def test_plan_domain_declared_legend_exclusion_removes_root_and_derived() -> None:
    view = build_plan_domain(
        _network_fixture(),
        excluded_legend_entity_keys=("legend-root",),
    )

    assert {entity.entity_key for entity in view.entities} == {"network-line"}
    legend = view.diagnostics["legend_exclusions"]
    assert legend["declared"] == ["legend-root"]
    assert legend["declared_count"] == 1
    assert "legend-root" in legend["excluded"]
    assert any(
        key.startswith("plan:") for key in legend["excluded"]
    )
    assert view.diagnostics["status"] == "PASS"


def test_plan_domain_legend_exclusion_deduplicates_declared_keys() -> None:
    view = build_plan_domain(
        _network_fixture(),
        excluded_legend_entity_keys=("legend-root", "legend-root"),
    )

    assert view.diagnostics["legend_exclusions"]["declared"] == ["legend-root"]
    assert view.diagnostics["legend_exclusions"]["declared_count"] == 1


def test_plan_domain_legend_exclusion_rejects_non_insert_root() -> None:
    with pytest.raises(PlanDomainError) as error:
        build_plan_domain(
            _network_fixture(),
            excluded_legend_entity_keys=("network-line",),
        )

    diagnostics = error.value.diagnostics
    assert diagnostics["status"] == "FAIL"
    issues = [
        issue
        for issue in diagnostics["issues"]
        if issue["code"] == "legend_exclusion_not_insert_root"
    ]
    assert len(issues) == 1
    assert issues[0]["blocking"] is True
    assert issues[0]["entity_key"] == "network-line"


def test_plan_domain_legend_exclusion_rejects_unknown_key() -> None:
    with pytest.raises(PlanDomainError) as error:
        build_plan_domain(
            _network_fixture(),
            excluded_legend_entity_keys=("ghost-key",),
        )

    assert any(
        issue["code"] == "legend_exclusion_not_insert_root"
        and issue["entity_key"] == "ghost-key"
        for issue in error.value.diagnostics["issues"]
    )


def test_plan_domain_legend_exclusion_rejects_route_layer_root() -> None:
    with pytest.raises(PlanDomainError) as error:
        build_plan_domain(
            [
                _entity(
                    "route-insert",
                    kind="INSERT",
                    layer="CABLE ROUTE",
                    block_name="LEGEND",
                    points=((500.0, 500.0),),
                    raw_properties=_transform((500.0, 500.0, 0.0)),
                ),
                *_network_fixture(),
            ],
            route_layer_pattern=re.compile(r"(?i)CABLE"),
            excluded_legend_entity_keys=("route-insert",),
        )

    issues = [
        issue
        for issue in error.value.diagnostics["issues"]
        if issue["code"] == "legend_exclusion_route_layer"
    ]
    assert len(issues) == 1
    assert issues[0]["blocking"] is True
    assert issues[0]["entity_key"] == "route-insert"
    assert issues[0]["layer"] == "CABLE ROUTE"


def test_plan_domain_without_legend_declaration_adds_no_diagnostics_key() -> None:
    view = build_plan_domain(_network_fixture())

    assert "legend_exclusions" not in view.diagnostics
    assert len(view.entities) == 3


def test_plan_domain_empty_legend_declaration_is_inert() -> None:
    view = build_plan_domain(
        _network_fixture(),
        excluded_legend_entity_keys=(),
    )

    assert view.diagnostics["legend_exclusions"] == {
        "declared": [],
        "excluded": [],
        "declared_count": 0,
    }
    assert len(view.entities) == 3


def _profile_payload(plan_domain=None) -> dict:
    payload = {
        "schema_version": "cad2gis-project-profile-v1",
        "project_id": "legend-exclusion-fixture",
        "review": {
            "status": "draft",
            "reviewed_by": "",
            "reviewed_at": "",
            "provenance": "",
        },
        "source_binding": {
            "source_sha256": "a" * 64,
            "source_size_bytes": 0,
            "inventory_sha256": "b" * 64,
        },
        "drawing": {
            "dwg_cgeocs": None,
            "dwg_insunits": None,
            "drawing_units": None,
        },
        "crs": {"source_crs": None, "target_crs": None},
        "spatial_coverage_policy": None,
        "expectations": {
            "source_inventory": {},
            "feature_counts": {},
            "annotation_families": {},
            "source_geometry_gates": {},
            "topology_gates": {},
            "segment_gates": {},
            "delivery_counts": {},
        },
    }
    if plan_domain is not None:
        payload["plan_domain"] = plan_domain
    return payload


def _load_profile(tmp_path: Path, plan_domain=None) -> SourceProfile:
    path = tmp_path / "source_profile.json"
    path.write_text(
        json.dumps(_profile_payload(plan_domain)), encoding="utf-8",
    )
    return SourceProfile.load(path)


def test_profile_defaults_to_no_legend_exclusions(tmp_path: Path) -> None:
    profile = _load_profile(tmp_path)

    assert profile.excluded_legend_entity_keys == ()


def test_profile_loads_legend_exclusion_keys(tmp_path: Path) -> None:
    profile = _load_profile(
        tmp_path,
        {
            "include_orphan_blocks": [],
            "excluded_legend_entity_keys": [" root-a ", "root-b", "root-a"],
        },
    )

    assert profile.excluded_legend_entity_keys == ("root-a", "root-b")


def test_profile_legend_exclusion_keys_are_optional(tmp_path: Path) -> None:
    profile = _load_profile(tmp_path, {"include_orphan_blocks": []})

    assert profile.excluded_legend_entity_keys == ()


def test_profile_rejects_legend_keys_without_orphan_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid plan_domain keys"):
        _load_profile(
            tmp_path,
            {"excluded_legend_entity_keys": ["root-a"]},
        )


@pytest.mark.parametrize(
    "keys",
    ["root-a", 42, ["root-a", ""], ["root-a", "  "], ["root-a", 7]],
)
def test_profile_rejects_invalid_legend_key_values(tmp_path: Path, keys) -> None:
    with pytest.raises(ValueError, match="excluded_legend_entity_keys"):
        _load_profile(
            tmp_path,
            {
                "include_orphan_blocks": [],
                "excluded_legend_entity_keys": keys,
            },
        )
