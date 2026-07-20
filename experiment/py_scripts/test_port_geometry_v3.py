from __future__ import annotations

import math
from types import SimpleNamespace

from cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis_v3.ports import build_port_candidates


class _Registry:
    thresholds = {
        "device_to_support_candidate": 0.5,
        "dimension_to_support": 0.5,
        "exact": 1.0e-6,
    }


def _entity(
    key: str,
    *,
    layout: str,
    kind: str,
    points=(),
    block_name: str = "",
    closed: bool = False,
    raw: dict | None = None,
    scale=(1.0, 1.0, 1.0),
    rotation: float = 0.0,
) -> SourceEntity:
    points = tuple(tuple(point) for point in points)
    return SourceEntity(
        entity_key=key,
        source_sha256="source",
        source_file="source.dwg",
        handle=key,
        layout=layout,
        layout_role="block_definition" if layout.upper().startswith("BLOCKDEF:") else "model",
        cad_role="block_definition" if layout.upper().startswith("BLOCKDEF:") else "model",
        layer="0",
        object_name=f"ACDB{kind}",
        dwg_type=kind,
        points=points,
        centroid=(points[0] if points else (0.0, 0.0)),
        closed=closed,
        text="",
        block_name=block_name,
        block_attributes={},
        style=CadStyle(rotation=rotation),
        scale=scale,
        raw_properties=raw or {},
    )


def _feature(key: str, feature_class: str, points, source_entity_key: str) -> Feature:
    return Feature(
        feature_key=key,
        feature_class=feature_class,
        geometry_kind="LineString" if feature_class == "CABLE" else "Point",
        native_points=list(points),
        source_entity_key=source_entity_key,
        source_handle=source_entity_key,
        source_layer=feature_class,
        geometry_role="SOURCE_ROUTE" if feature_class == "CABLE" else "SOURCE_ASSET",
        style=CadStyle(),
    )


def _transform_facts(*, insertion=(0.0, 0.0, 0.0), base=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=0.0):
    return {
        "transform_facts": {
            "insertion_point": insertion,
            "block_base_point": base,
            "scale": scale,
            "rotation": rotation,
            "normal": (0.0, 0.0, 1.0),
            "extrusion": (0.0, 0.0, 1.0),
        }
    }


def test_explicit_nonzero_base_rotation_nonuniform_negative_scale_is_exact_and_route_immutable():
    definition = _entity(
        "def",
        layout="BLOCKDEF:SYMBOL",
        kind="LINE",
        points=((10.0, 0.0), (11.0, 0.0)),
    )
    instance = _entity(
        "instance",
        layout="Model",
        kind="INSERT",
        block_name="SYMBOL",
        raw=_transform_facts(
            insertion=(100.0, 200.0, 0.0),
            base=(10.0, 0.0, 0.0),
            scale=(-2.0, 3.0, 1.0),
            rotation=math.pi / 2.0,
        ),
    )
    support = _feature("support", "PTECH", [(100.0, 200.0)], "instance")
    route = _feature("route", "CABLE", [(100.0, 200.0), (110.0, 200.0)], "route")
    original = list(route.native_points)

    candidates = build_port_candidates([definition, instance], [support, route], _Registry())

    assert len(candidates) == 1
    assert candidates[0]["status"] == "on_symbol_geometry"
    assert candidates[0]["port_point_native"] == [100.0, 200.0]
    assert candidates[0]["transform_fact_provenance"]["block_base_point"] == "reader_raw_properties"
    assert route.native_points == original


def test_missing_explicit_base_abstains_instead_of_assuming_zero():
    definition = _entity("def", layout="BLOCKDEF:SYMBOL", kind="LINE", points=((0.0, 0.0), (1.0, 0.0)))
    facts = _transform_facts(insertion=(10.0, 20.0, 0.0))
    del facts["transform_facts"]["block_base_point"]
    instance = _entity("instance", layout="Model", kind="INSERT", block_name="SYMBOL", raw=facts)
    support = _feature("support", "PTECH", [(10.0, 20.0)], "instance")
    route = _feature("route", "CABLE", [(10.0, 20.0), (11.0, 20.0)], "route")

    candidate = build_port_candidates([definition, instance], [support, route], _Registry())[0]

    assert candidate["status"] == "abstain_block_footprint"
    assert candidate["port_point_native"] is None
    transform_reasons = candidate["diagnostic"]["transform_reasons"]
    assert transform_reasons[0]["code"] == "missing_block_transform_facts"
    assert "block_base_point" in transform_reasons[0]["missing_facts"]


def test_nested_insert_composes_parent_and_child_transforms():
    child_definition = _entity("child-def", layout="BLOCKDEF:CHILD", kind="LINE", points=((0.0, 0.0), (1.0, 0.0)))
    nested = _entity(
        "nested",
        layout="BLOCKDEF:ROOT",
        kind="INSERT",
        block_name="CHILD",
        raw=_transform_facts(insertion=(5.0, 7.0, 0.0), base=(0.0, 0.0, 0.0), scale=(2.0, 2.0, 1.0)),
    )
    root_instance = _entity(
        "root-instance",
        layout="Model",
        kind="INSERT",
        block_name="ROOT",
        raw=_transform_facts(insertion=(100.0, 200.0, 0.0), base=(0.0, 0.0, 0.0)),
    )
    support = _feature("support", "PTECH", [(105.0, 207.0)], "root-instance")
    route = _feature("route", "CABLE", [(105.0, 207.0), (120.0, 207.0)], "route")

    candidate = build_port_candidates(
        [child_definition, nested, root_instance], [support, route], _Registry(),
    )[0]

    assert candidate["status"] == "on_symbol_geometry"
    assert candidate["port_point_native"] == [105.0, 207.0]


def test_block_record_metadata_does_not_block_linear_footprint():
    block_record = _entity(
        "block-record",
        layout="BLOCKDEF:SYMBOL",
        kind="BLOCK_RECORD",
    )
    definition = _entity(
        "def",
        layout="BLOCKDEF:SYMBOL",
        kind="LINE",
        points=((0.0, 0.0), (1.0, 0.0)),
    )
    instance = _entity(
        "instance",
        layout="Model",
        kind="INSERT",
        block_name="SYMBOL",
        raw=_transform_facts(insertion=(10.0, 20.0, 0.0)),
    )
    support = _feature("support", "PTECH", [(10.0, 20.0)], "instance")
    route = _feature("route", "CABLE", [(10.0, 20.0), (11.0, 20.0)], "route")

    candidate = build_port_candidates(
        [block_record, definition, instance], [support, route], _Registry(),
    )[0]

    assert candidate["status"] == "on_symbol_geometry"
    assert candidate["port_point_native"] == [10.0, 20.0]


def test_curved_block_footprint_is_unsupported_not_chorded():
    curved = _entity(
        "curve",
        layout="BLOCKDEF:SYMBOL",
        kind="ARC",
        points=((0.0, 0.0), (1.0, 1.0)),
    )
    instance = _entity(
        "instance",
        layout="Model",
        kind="INSERT",
        block_name="SYMBOL",
        raw=_transform_facts(insertion=(10.0, 10.0, 0.0)),
    )
    support = _feature("support", "PTECH", [(10.0, 10.0)], "instance")
    route = _feature("route", "CABLE", [(10.0, 10.0), (20.0, 10.0)], "route")

    candidate = build_port_candidates([curved, instance], [support, route], _Registry())[0]

    assert candidate["status"] == "unsupported_curved_footprint"
    assert candidate["port_point_native"] is None
    assert "curved_block_footprint_not_exactly_supported" in candidate["diagnostic"]["reason_codes"]


def test_authoritative_null_scale_cannot_fall_back_to_legacy_scalars():
    definition = _entity("def", layout="BLOCKDEF:SYMBOL", kind="LINE", points=((0.0, 0.0), (1.0, 0.0)))
    facts = _transform_facts(insertion=(10.0, 20.0, 0.0))
    facts["transform_facts"]["scale"] = None
    facts["transform_facts"]["scale_status"] = "unavailable"
    facts["scale_x"] = 2.0
    facts["scale_y"] = 2.0
    facts["scale_z"] = 1.0
    instance = _entity("instance", layout="Model", kind="INSERT", block_name="SYMBOL", raw=facts)
    support = _feature("support", "PTECH", [(10.0, 20.0)], "instance")
    route = _feature("route", "CABLE", [(10.0, 20.0), (11.0, 20.0)], "route")

    candidate = build_port_candidates([definition, instance], [support, route], _Registry())[0]

    assert candidate["status"] == "abstain_block_footprint"
    assert candidate["port_point_native"] is None
    assert "scale" in candidate["diagnostic"]["transform_reasons"][0]["missing_facts"]


def test_authoritative_null_rotation_cannot_fall_back_to_style_rotation():
    definition = _entity("def", layout="BLOCKDEF:SYMBOL", kind="LINE", points=((0.0, 0.0), (1.0, 0.0)))
    facts = _transform_facts(insertion=(10.0, 20.0, 0.0))
    facts["transform_facts"]["rotation"] = None
    facts["transform_facts"]["rotation_status"] = "unavailable"
    instance = _entity(
        "instance",
        layout="Model",
        kind="INSERT",
        block_name="SYMBOL",
        raw=facts,
        rotation=math.pi / 2.0,
    )
    support = _feature("support", "PTECH", [(10.0, 20.0)], "instance")
    route = _feature("route", "CABLE", [(10.0, 20.0), (11.0, 20.0)], "route")

    candidate = build_port_candidates([definition, instance], [support, route], _Registry())[0]

    assert candidate["status"] == "abstain_block_footprint"
    assert candidate["port_point_native"] is None
    assert "rotation" in candidate["diagnostic"]["transform_reasons"][0]["missing_facts"]
