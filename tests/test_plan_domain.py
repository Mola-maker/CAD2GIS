from __future__ import annotations

import hashlib
import math

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
        "layer": "FIBER_ROUTE",
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
    assert view.diagnostics["expanded_insert_count"] == 1
    assert view.diagnostics["expanded_nested_insert_count"] == 2


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
