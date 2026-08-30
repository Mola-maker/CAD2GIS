from __future__ import annotations

import hashlib
import math
import re

import pytest

from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.plan_domain import (
    PLAN_DOMAIN_SCHEMA_VERSION,
    PlanDomainError,
    build_plan_domain,
)


SOURCE_HASH = "a" * 64


def _entity(
    key: str,
    *,
    kind: str,
    layout: str = "Model",
    layout_role: str = "model",
    cad_role: str = "model",
    block_name: str = "",
    layer: str = "FIBER_ROUTE",
    points: tuple[tuple[float, float], ...] = ((0.0, 0.0),),
    raw_properties: dict | None = None,
    curve_facts: dict | None = None,
    native_length: float | None = None,
) -> SourceEntity:
    return SourceEntity.from_record({
        "entity_key": key,
        "source_sha256": SOURCE_HASH,
        "source_file": "fixture.dwg",
        "handle": key,
        "layout": layout,
        "layout_role": layout_role,
        "cad_role": cad_role,
        "layer": layer,
        "object_name": f"ACDB{kind}",
        "dwg_type_name": kind,
        "points": points,
        "centroid": points[0] if points else (0.0, 0.0),
        "closed": False,
        "text": "",
        "block_name": block_name,
        "block_attributes": {},
        "raw_properties": raw_properties or {},
        "curve_facts": curve_facts or {},
        "native_length": native_length,
    })


def _transform(
    insertion: tuple[float, float, float],
    *,
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    rotation: float = 0.0,
) -> dict:
    return {
        "transform_facts": {
            "insertion_point": insertion,
            "block_base_point": (0.0, 0.0, 0.0),
            "scale": scale,
            "rotation": rotation,
            "normal": (0.0, 0.0, 1.0),
            "extrusion": (0.0, 0.0, 1.0),
        }
    }


def test_plan_domain_materializes_fallback_block_without_mutating_inventory() -> None:
    root = _entity(
        "root",
        kind="INSERT",
        cad_role="style_legend",
        block_name="NETWORK",
        points=((100.0, 200.0),),
        raw_properties=_transform(
            (100.0, 200.0, 0.0),
            scale=(2.0, 2.0, 1.0),
            rotation=math.pi / 2.0,
        ),
    )
    definition = _entity(
        "definition-line",
        kind="LINE",
        layout="BLOCKDEF:NETWORK",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (10.0, 0.0)),
        native_length=10.0,
    )
    inventory = [root, definition]

    first = build_plan_domain(inventory)
    second = build_plan_domain(list(reversed(inventory)))

    assert first.diagnostics["schema_version"] == PLAN_DOMAIN_SCHEMA_VERSION
    assert first.diagnostics["selection_mode"] == "layout-role-fallback"
    assert first.diagnostics["status"] == "PASS"
    assert first.diagnostics["derived_entity_count"] == 1
    assert len(first.entities) == 2
    derived = next(entity for entity in first.entities if entity.entity_key != "root")
    assert derived.points[0] == pytest.approx((100.0, 200.0))
    assert derived.points[1] == pytest.approx((100.0, 220.0))
    assert derived.native_length == pytest.approx(20.0)
    assert derived.cad_role == "model"
    assert derived.raw_properties["plan_domain"]["definition_entity_key"] == (
        "definition-line"
    )
    assert [entity.entity_key for entity in first.entities] == [
        entity.entity_key for entity in second.entities
    ]
    assert definition.points == ((0.0, 0.0), (10.0, 0.0))


@pytest.mark.parametrize(
    ("translation", "scale", "rotation"),
    [
        ((13.25, -8.5), 0.25, 0.0),
        ((-400.0, 90.0), 1.0, math.pi / 7.0),
        ((0.0, 0.0), 3.5, -math.pi / 3.0),
        ((1.0e6, -2.0e6), 0.001, math.pi),
    ],
)
def test_plan_domain_is_source_agnostic_across_affine_facts(
    translation: tuple[float, float],
    scale: float,
    rotation: float,
) -> None:
    """No APD/Lamteh coordinate, layer, count, or block-name rule is needed."""

    suffix = hashlib.sha256(
        repr((translation, scale, rotation)).encode("ascii")
    ).hexdigest()[:10]
    name = f"VENDOR_BLOCK_{suffix}"
    root = _entity(
        f"root-{suffix}",
        kind="INSERT",
        cad_role="frame",
        block_name=name,
        raw_properties=_transform(
            (translation[0], translation[1], 0.0),
            scale=(scale, scale, 1.0),
            rotation=rotation,
        ),
    )
    definition = _entity(
        f"definition-{suffix}",
        kind="LINE",
        layout=f"BLOCKDEF:{name}",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((1.25, -2.5), (8.0, 4.0)),
    )

    view = build_plan_domain([definition, root])
    derived = next(
        entity for entity in view.entities if entity.entity_key.startswith("plan:")
    )
    cosine, sine = math.cos(rotation), math.sin(rotation)
    expected = tuple(
        (
            translation[0] + scale * (cosine * x - sine * y),
            translation[1] + scale * (sine * x + cosine * y),
        )
        for x, y in definition.points
    )
    for observed, wanted in zip(derived.points, expected, strict=True):
        assert observed == pytest.approx(wanted)
    assert view.diagnostics["raw_entity_count"] == 2
    assert view.diagnostics["derived_entity_count"] == 1


def test_plan_domain_composes_nested_insert_transforms() -> None:
    root = _entity(
        "root-a",
        kind="INSERT",
        cad_role="design_summary",
        block_name="A",
        raw_properties=_transform((10.0, 20.0, 0.0)),
    )
    nested = _entity(
        "nested-b",
        kind="INSERT",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        block_name="B",
        raw_properties=_transform(
            (5.0, 0.0, 0.0),
            rotation=math.pi / 2.0,
        ),
    )
    leaf = _entity(
        "leaf-b",
        kind="LINE",
        layout="BLOCKDEF:B",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((1.0, 0.0), (3.0, 0.0)),
    )

    view = build_plan_domain([leaf, root, nested])
    derived = next(
        entity for entity in view.entities if entity.entity_key.startswith("plan:")
    )
    assert derived.points[0] == pytest.approx((15.0, 21.0))
    assert derived.points[1] == pytest.approx((15.0, 23.0))
    assert view.diagnostics["expanded_insert_count"] == 2


def test_plan_domain_fallback_fails_closed_on_missing_transform() -> None:
    root = _entity(
        "root",
        kind="INSERT",
        cad_role="title_block",
        block_name="NETWORK",
    )
    definition = _entity(
        "definition-line",
        kind="LINE",
        layout="BLOCKDEF:NETWORK",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
    )

    with pytest.raises(PlanDomainError) as error:
        build_plan_domain([root, definition])

    diagnostics = error.value.diagnostics
    assert diagnostics["status"] == "FAIL"
    assert diagnostics["issues"][0]["code"] == (
        "missing_or_invalid_insert_transform"
    )
    assert diagnostics["issues"][0]["blocking"] is True


def test_plan_domain_preferred_partition_preserves_legacy_conversion_boundary() -> None:
    line = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (1.0, 0.0)),
    )
    unresolved_insert = _entity(
        "model-insert",
        kind="INSERT",
        block_name="MISSING",
        points=((1.0, 0.0),),
    )

    view = build_plan_domain([line, unresolved_insert])

    assert view.diagnostics["selection_mode"] == "cad-role-partition"
    assert view.diagnostics["status"] == "WATCH"
    assert view.diagnostics["issues"][0]["blocking"] is False
    assert {entity.entity_key for entity in view.entities} == {
        "model-line",
        "model-insert",
    }


def test_plan_domain_rejects_nonuniform_curved_block_projection() -> None:
    root = _entity(
        "root",
        kind="INSERT",
        cad_role="style_legend",
        block_name="CURVED",
        raw_properties=_transform(
            (0.0, 0.0, 0.0),
            scale=(2.0, 1.0, 1.0),
        ),
    )
    curve_facts = {
        "schema_version": "cad2gis-curve-facts-v1",
        "coordinate_system": "WCS",
        "primitive_type": "LWPOLYLINE",
        "vertices_wcs": [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
        "bulges": [0.5, 0.0],
        "elevation": 0.0,
        "normal": [0.0, 0.0, 1.0],
        "extrusion": [0.0, 0.0, 1.0],
        "closed": False,
        "primitive_parameters": {},
        "native_length": 2.318238045,
        "native_length_source": "fixture",
    }
    definition = _entity(
        "definition-curve",
        kind="LWPOLYLINE",
        layout="BLOCKDEF:CURVED",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (2.0, 0.0)),
        curve_facts=curve_facts,
        native_length=2.318238045,
    )

    with pytest.raises(PlanDomainError) as error:
        build_plan_domain([root, definition])

    assert any(
        issue["code"] == "unsupported_block_geometry_transform"
        for issue in error.value.diagnostics["issues"]
    )


def test_plan_domain_referenced_block_is_not_orphan() -> None:
    root = _entity(
        "root",
        kind="INSERT",
        block_name="NETWORK",
        raw_properties=_transform((0.0, 0.0, 0.0)),
    )
    definition = _entity(
        "definition-line",
        kind="LINE",
        layout="BLOCKDEF:NETWORK",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
    )

    view = build_plan_domain([root, definition])

    assert view.diagnostics["status"] == "PASS"
    assert view.diagnostics["orphan_blocks"] == []
    assert view.diagnostics["orphan_member_entity_keys"] == []
    assert not any(
        issue["code"] == "orphan_block_definition"
        for issue in view.diagnostics["issues"]
    )


def test_plan_domain_unreferenced_block_is_orphan_with_watch() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member_a = _entity(
        "orphan-member-a",
        kind="LINE",
        layout="BLOCKDEF:ORPHAN",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
        layer="CABLE ROUTE",
    )
    member_b = _entity(
        "orphan-member-b",
        kind="LINE",
        layout="BLOCKDEF:ORPHAN",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 1.0), (1.0, 1.0)),
    )

    view = build_plan_domain([root, member_a, member_b])

    assert view.diagnostics["status"] == "WATCH"
    assert view.diagnostics["orphan_blocks"] == [{
        "block_name": "ORPHAN",
        "member_count": 2,
        "layer_distribution": {"CABLE ROUTE": 1, "FIBER_ROUTE": 1},
        "nested_insert_count": 0,
    }]
    assert view.diagnostics["orphan_member_entity_keys"] == [
        "orphan-member-a",
        "orphan-member-b",
    ]
    issues = [
        issue
        for issue in view.diagnostics["issues"]
        if issue["code"] == "orphan_block_definition"
    ]
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    assert issues[0]["blocking"] is False
    assert issues[0]["block_name"] == "ORPHAN"
    assert issues[0]["member_count"] == 2


def test_plan_domain_nested_orphan_is_not_double_counted() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member_a = _entity(
        "line-a",
        kind="LINE",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
    )
    nested_insert = _entity(
        "insert-b",
        kind="INSERT",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        block_name="B",
        raw_properties=_transform((5.0, 5.0, 0.0)),
    )
    member_b = _entity(
        "line-b",
        kind="LINE",
        layout="BLOCKDEF:B",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (2.0, 0.0)),
    )

    view = build_plan_domain([root, member_a, nested_insert, member_b])

    assert view.diagnostics["orphan_blocks"] == [{
        "block_name": "A",
        "member_count": 3,
        "layer_distribution": {"FIBER_ROUTE": 3},
        "nested_insert_count": 1,
    }]
    assert view.diagnostics["orphan_member_entity_keys"] == [
        "insert-b",
        "line-a",
        "line-b",
    ]
    orphan_issues = [
        issue
        for issue in view.diagnostics["issues"]
        if issue["code"] == "orphan_block_definition"
    ]
    assert [issue["block_name"] for issue in orphan_issues] == ["A"]


def test_plan_domain_empty_block_is_not_orphan() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    block_record = _entity(
        "empty-record",
        kind="BLOCK_RECORD",
        layout="BLOCKDEF:EMPTY",
        layout_role="block_definition",
        cad_role="block_definition",
    )

    view = build_plan_domain([root, block_record])

    assert view.diagnostics["status"] == "PASS"
    assert view.diagnostics["orphan_blocks"] == []
    assert view.diagnostics["orphan_member_entity_keys"] == []


def _base_point(value: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> dict:
    return {"transform_facts": {"block_base_point": value}}


def test_plan_domain_orphan_recovery_expands_with_identity_transform() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member = _entity(
        "orphan-member",
        kind="LINE",
        layout="BLOCKDEF:ORPHAN",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((5.0, 5.0), (8.0, 9.0)),
        raw_properties=_base_point(),
    )

    view = build_plan_domain([root, member], include_orphan_blocks=("orphan",))

    derived = [
        entity for entity in view.entities if entity.entity_key.startswith("plan:")
    ]
    assert len(derived) == 1
    assert derived[0].points == ((5.0, 5.0), (8.0, 9.0))
    assert derived[0].cad_role == "model"
    assert derived[0].raw_properties["provenance"] == {
        "orphan_block_recovery": "ORPHAN",
    }
    assert view.diagnostics["orphan_recovery"] == {
        "configured": ["ORPHAN"],
        "recovered": ["ORPHAN"],
        "skipped": [],
    }
    assert view.diagnostics["derived_entity_count"] == 1
    assert view.diagnostics["orphan_member_entity_keys"] == []
    assert view.diagnostics["status"] == "WATCH"


def test_plan_domain_orphan_recovery_recurses_nested_inserts() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member_a = _entity(
        "line-a",
        kind="LINE",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
        raw_properties=_base_point(),
    )
    nested_insert = _entity(
        "insert-b",
        kind="INSERT",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        block_name="B",
        raw_properties=_transform((10.0, 0.0, 0.0)),
    )
    member_b = _entity(
        "line-b",
        kind="LINE",
        layout="BLOCKDEF:B",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((1.0, 0.0), (3.0, 0.0)),
        raw_properties=_base_point(),
    )

    view = build_plan_domain(
        [root, member_a, nested_insert, member_b],
        include_orphan_blocks="*",
    )

    derived = {
        entity.points: entity
        for entity in view.entities if entity.entity_key.startswith("plan:")
    }
    assert set(derived) == {
        ((0.0, 0.0), (1.0, 0.0)),
        ((11.0, 0.0), (13.0, 0.0)),
    }
    assert all(
        entity.raw_properties["provenance"] == {"orphan_block_recovery": "A"}
        for entity in derived.values()
    )
    assert view.diagnostics["orphan_recovery"] == {
        "configured": ["A"],
        "recovered": ["A"],
        "skipped": [],
    }
    assert view.diagnostics["orphan_member_entity_keys"] == []


def test_plan_domain_orphan_recovery_default_is_inert() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member = _entity(
        "orphan-member",
        kind="LINE",
        layout="BLOCKDEF:ORPHAN",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((5.0, 5.0), (8.0, 9.0)),
        raw_properties=_base_point(),
    )

    view = build_plan_domain([root, member])

    assert "orphan_recovery" not in view.diagnostics
    assert view.diagnostics["derived_entity_count"] == 0
    assert view.diagnostics["orphan_member_entity_keys"] == ["orphan-member"]


def test_plan_domain_orphan_recovery_unknown_block_is_skipped() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )

    view = build_plan_domain([root], include_orphan_blocks=("GHOST",))

    assert view.diagnostics["orphan_recovery"] == {
        "configured": ["GHOST"],
        "recovered": [],
        "skipped": [{"block_name": "GHOST", "reason": "unknown_block_definition"}],
    }
    issues = [
        issue
        for issue in view.diagnostics["issues"]
        if issue["code"] == "orphan_block_recovery_skipped"
    ]
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    assert issues[0]["blocking"] is False
    assert view.diagnostics["derived_entity_count"] == 0
    assert view.diagnostics["status"] == "WATCH"


def test_plan_domain_orphan_recovery_rejects_nonuniform_curved_geometry() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    nested_insert = _entity(
        "insert-curved",
        kind="INSERT",
        layout="BLOCKDEF:OUTER",
        layout_role="block_definition",
        cad_role="block_definition",
        block_name="CURVED",
        raw_properties=_transform((0.0, 0.0, 0.0), scale=(2.0, 1.0, 1.0)),
    )
    curve_facts = {
        "schema_version": "cad2gis-curve-facts-v1",
        "coordinate_system": "WCS",
        "primitive_type": "LWPOLYLINE",
        "vertices_wcs": [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
        "bulges": [0.5, 0.0],
        "elevation": 0.0,
        "normal": [0.0, 0.0, 1.0],
        "extrusion": [0.0, 0.0, 1.0],
        "closed": False,
        "primitive_parameters": {},
        "native_length": 2.318238045,
        "native_length_source": "fixture",
    }
    curve = _entity(
        "curve",
        kind="LWPOLYLINE",
        layout="BLOCKDEF:CURVED",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (2.0, 0.0)),
        raw_properties=_base_point(),
        curve_facts=curve_facts,
        native_length=2.318238045,
    )

    view = build_plan_domain(
        [root, nested_insert, curve],
        include_orphan_blocks=("OUTER",),
    )

    assert view.diagnostics["orphan_recovery"]["skipped"] == [
        {"block_name": "OUTER", "reason": "unsupported_block_geometry_transform"}
    ]
    assert view.diagnostics["orphan_recovery"]["recovered"] == []
    assert any(
        issue["code"] == "unsupported_block_geometry_transform"
        and issue["blocking"] is False
        for issue in view.diagnostics["issues"]
    )
    assert view.diagnostics["derived_entity_count"] == 0
    assert view.diagnostics["orphan_member_entity_keys"] == [
        "curve",
        "insert-curved",
    ]


def test_plan_domain_orphan_recovery_records_cyclic_issue() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member = _entity(
        "line-c",
        kind="LINE",
        layout="BLOCKDEF:CYCLIC",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
        raw_properties=_base_point(),
    )
    self_insert = _entity(
        "self-insert",
        kind="INSERT",
        layout="BLOCKDEF:CYCLIC",
        layout_role="block_definition",
        cad_role="block_definition",
        block_name="CYCLIC",
        raw_properties=_transform((1.0, 1.0, 0.0)),
    )

    view = build_plan_domain(
        [root, member, self_insert],
        include_orphan_blocks=("CYCLIC",),
    )

    assert view.diagnostics["orphan_recovery"]["recovered"] == ["CYCLIC"]
    assert any(
        issue["code"] == "cyclic_nested_block_definition"
        and issue["blocking"] is False
        for issue in view.diagnostics["issues"]
    )
    assert view.diagnostics["derived_entity_count"] == 1


def test_plan_domain_orphan_recovery_skips_non_origin_base_point() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member = _entity(
        "orphan-member",
        kind="LINE",
        layout="BLOCKDEF:ORPHAN",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((5.0, 5.0), (8.0, 9.0)),
        raw_properties=_base_point((5.0, 0.0, 0.0)),
    )

    view = build_plan_domain([root, member], include_orphan_blocks=("ORPHAN",))

    assert view.diagnostics["orphan_recovery"]["skipped"] == [
        {"block_name": "ORPHAN", "reason": "block_base_point_not_origin"}
    ]
    assert view.diagnostics["derived_entity_count"] == 0
    assert view.diagnostics["orphan_member_entity_keys"] == ["orphan-member"]


def test_plan_domain_orphan_recovery_skips_unavailable_base_point() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member = _entity(
        "orphan-member",
        kind="LINE",
        layout="BLOCKDEF:ORPHAN",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((5.0, 5.0), (8.0, 9.0)),
    )

    view = build_plan_domain([root, member], include_orphan_blocks=("ORPHAN",))

    assert view.diagnostics["orphan_recovery"]["skipped"] == [
        {"block_name": "ORPHAN", "reason": "block_base_point_unavailable"}
    ]
    assert view.diagnostics["derived_entity_count"] == 0
    assert view.diagnostics["orphan_member_entity_keys"] == ["orphan-member"]


def test_plan_domain_orphan_recovery_rejects_referenced_block() -> None:
    root = _entity(
        "root",
        kind="INSERT",
        block_name="NETWORK",
        raw_properties=_transform((0.0, 0.0, 0.0)),
    )
    member = _entity(
        "definition-line",
        kind="LINE",
        layout="BLOCKDEF:NETWORK",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
        raw_properties=_base_point(),
    )

    view = build_plan_domain([root, member], include_orphan_blocks=("NETWORK",))

    assert view.diagnostics["orphan_recovery"]["skipped"] == [
        {"block_name": "NETWORK", "reason": "not_orphan_block_definition"}
    ]
    assert view.diagnostics["orphan_recovery"]["recovered"] == []
    assert view.diagnostics["derived_entity_count"] == 1


def test_plan_domain_orphan_recovery_nested_orphan_configured_directly() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member_a = _entity(
        "line-a",
        kind="LINE",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
        raw_properties=_base_point(),
    )
    nested_insert = _entity(
        "insert-b",
        kind="INSERT",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        block_name="B",
        raw_properties=_transform((10.0, 0.0, 0.0)),
    )
    member_b = _entity(
        "line-b",
        kind="LINE",
        layout="BLOCKDEF:B",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((1.0, 0.0), (3.0, 0.0)),
        raw_properties=_base_point(),
    )

    view = build_plan_domain(
        [root, member_a, nested_insert, member_b],
        include_orphan_blocks=("B",),
    )

    assert view.diagnostics["orphan_recovery"]["recovered"] == ["B"]
    assert view.diagnostics["derived_entity_count"] == 1
    assert view.diagnostics["orphan_member_entity_keys"] == [
        "insert-b",
        "line-a",
    ]


def test_plan_domain_orphan_recovery_skips_covered_nested_orphan() -> None:
    root = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    member_a = _entity(
        "line-a",
        kind="LINE",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (1.0, 0.0)),
        raw_properties=_base_point(),
    )
    nested_insert = _entity(
        "insert-b",
        kind="INSERT",
        layout="BLOCKDEF:A",
        layout_role="block_definition",
        cad_role="block_definition",
        block_name="B",
        raw_properties=_transform((10.0, 0.0, 0.0)),
    )
    member_b = _entity(
        "line-b",
        kind="LINE",
        layout="BLOCKDEF:B",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((1.0, 0.0), (3.0, 0.0)),
        raw_properties=_base_point(),
    )

    view = build_plan_domain(
        [root, member_a, nested_insert, member_b],
        include_orphan_blocks=("A", "B"),
    )

    assert view.diagnostics["orphan_recovery"]["recovered"] == ["A"]
    assert view.diagnostics["orphan_recovery"]["skipped"] == [
        {"block_name": "B", "reason": "covered_by_recovered_root"}
    ]
    assert view.diagnostics["orphan_member_entity_keys"] == []


def test_plan_domain_route_layer_exemption_restores_reclassified_root() -> None:
    rescued = _entity(
        "cable-route",
        kind="LINE",
        layer="CABLE ROUTE",
        cad_role="style_legend",
        points=((0.0, 0.0), (5.0, 0.0)),
        raw_properties={"cad_role_original": "model"},
    )
    plain = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 1.0), (5.0, 1.0)),
    )

    view = build_plan_domain(
        [rescued, plain],
        route_layer_pattern=re.compile(r"(?i)CABLE"),
    )

    assert view.diagnostics["selection_mode"] == "cad-role-partition"
    restored = next(
        entity for entity in view.entities if entity.entity_key == "cable-route"
    )
    assert restored.cad_role == "model"
    plan_domain_record = restored.raw_properties["plan_domain"]
    assert plan_domain_record["materialization"] == (
        "layout-root-role-normalization"
    )
    assert plan_domain_record["route_layer_exemption"] == {
        "cad_role_original": "model",
    }
    assert view.diagnostics["route_layer_exemption"] == {
        "exempted_count": 1,
        "route_layer_excluded_count": 0,
    }

    default_view = build_plan_domain([rescued, plain])
    assert "route_layer_exemption" not in default_view.diagnostics
    assert {entity.entity_key for entity in default_view.entities} == {
        "model-line"
    }


def test_plan_domain_route_exemption_does_not_apply_off_route_layer() -> None:
    reclassified = _entity(
        "legend-note",
        kind="LINE",
        layer="LEGEND NOTES",
        cad_role="style_legend",
        points=((0.0, 0.0), (5.0, 0.0)),
        raw_properties={"cad_role_original": "model"},
    )
    plain = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 1.0), (5.0, 1.0)),
    )

    view = build_plan_domain(
        [reclassified, plain],
        route_layer_pattern=re.compile(r"(?i)CABLE"),
    )

    assert {entity.entity_key for entity in view.entities} == {"model-line"}
    assert view.diagnostics["route_layer_exemption"] == {
        "exempted_count": 0,
        "route_layer_excluded_count": 0,
    }


def test_plan_domain_route_exemption_requires_original_provenance() -> None:
    legacy_record = _entity(
        "cable-route",
        kind="LINE",
        layer="CABLE ROUTE",
        cad_role="style_legend",
        points=((0.0, 0.0), (5.0, 0.0)),
    )
    plain = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 1.0), (5.0, 1.0)),
    )

    view = build_plan_domain(
        [legacy_record, plain],
        route_layer_pattern=re.compile(r"(?i)CABLE"),
    )

    assert {entity.entity_key for entity in view.entities} == {"model-line"}
    assert view.diagnostics["route_layer_exemption"] == {
        "exempted_count": 0,
        "route_layer_excluded_count": 1,
    }


def test_plan_domain_without_route_pattern_adds_no_diagnostics_key() -> None:
    line = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (1.0, 0.0)),
    )

    view = build_plan_domain([line])

    assert "route_layer_exemption" not in view.diagnostics


def test_plan_domain_declared_paper_layout_is_admitted() -> None:
    model_line = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    paper_line = _entity(
        "paper-line",
        kind="LINE",
        layout="APD - SF",
        layout_role="layout",
        cad_role="layout",
        points=((1.0, 1.0), (2.0, 1.0)),
    )

    view = build_plan_domain(
        [model_line, paper_line],
        plan_layouts=("apd - sf",),
    )

    admitted = next(
        entity for entity in view.entities if entity.entity_key == "paper-line"
    )
    assert admitted.cad_role == "model"
    plan_domain_record = admitted.raw_properties["plan_domain"]
    assert plan_domain_record["materialization"] == (
        "layout-root-role-normalization"
    )
    assert plan_domain_record["declared_layout"] == "APD - SF"
    assert view.diagnostics["plan_layouts"] == {
        "declared": ["apd - sf"],
        "admitted": ["APD - SF"],
        "undeclared": {},
    }
    assert view.diagnostics["status"] == "PASS"


def test_plan_domain_declared_paper_layout_insert_expands_without_strict_fail() -> None:
    model_line = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    paper_insert = _entity(
        "paper-insert",
        kind="INSERT",
        layout="APD - SF",
        layout_role="layout",
        cad_role="layout",
        block_name="NETWORK",
        raw_properties=_transform((100.0, 200.0, 0.0)),
    )
    definition = _entity(
        "definition-line",
        kind="LINE",
        layout="BLOCKDEF:NETWORK",
        layout_role="block_definition",
        cad_role="block_definition",
        points=((0.0, 0.0), (10.0, 0.0)),
        native_length=10.0,
    )

    view = build_plan_domain(
        [model_line, paper_insert, definition],
        plan_layouts=("APD - SF",),
    )

    derived = [
        entity for entity in view.entities if entity.entity_key.startswith("plan:")
    ]
    assert len(derived) == 1
    assert derived[0].points[0] == pytest.approx((100.0, 200.0))
    assert derived[0].points[1] == pytest.approx((110.0, 200.0))
    assert view.diagnostics["plan_layouts"]["admitted"] == ["APD - SF"]


def test_plan_domain_undeclared_paper_layout_produces_aggregate_watch() -> None:
    model_line = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (10.0, 0.0)),
    )
    sheet_a = _entity(
        "sheet-a",
        kind="LINE",
        layout="Sheet2",
        layout_role="layout",
        cad_role="layout",
        points=((0.0, 0.0), (1.0, 0.0)),
    )
    sheet_b = _entity(
        "sheet-b",
        kind="TEXT",
        layout="Sheet2",
        layout_role="layout",
        cad_role="layout",
    )

    view = build_plan_domain([model_line, sheet_a, sheet_b])

    assert view.diagnostics["status"] == "WATCH"
    assert view.diagnostics["plan_layouts"] == {
        "declared": [],
        "admitted": [],
        "undeclared": {"Sheet2": 2},
    }
    issues = [
        issue
        for issue in view.diagnostics["issues"]
        if issue["code"] == "undeclared_layout_entities"
    ]
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    assert issues[0]["blocking"] is False
    assert issues[0]["layouts"] == {"Sheet2": 2}
    assert issues[0]["entity_count"] == 2
    assert {entity.entity_key for entity in view.entities} == {"model-line"}


def test_plan_domain_without_paper_layouts_has_empty_plan_layouts_summary() -> None:
    line = _entity(
        "model-line",
        kind="LINE",
        points=((0.0, 0.0), (1.0, 0.0)),
    )

    view = build_plan_domain([line])

    assert view.diagnostics["plan_layouts"] == {
        "declared": [],
        "admitted": [],
        "undeclared": {},
    }
    assert not any(
        issue["code"] == "undeclared_layout_entities"
        for issue in view.diagnostics["issues"]
    )
    assert view.diagnostics["status"] == "PASS"
