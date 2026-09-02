"""Source-bound recovery contracts for route entities outside default roots."""

from __future__ import annotations

import re
from types import SimpleNamespace

from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.plan_domain import build_plan_domain
from cad2gis.cad2gis_v3.semantics import classify_entities


SOURCE_HASH = "c" * 64


def _entity(key: str, *, kind: str, **overrides) -> SourceEntity:
    record = {
        "entity_key": key,
        "source_sha256": SOURCE_HASH,
        "source_file": "fixture.dwg",
        "handle": key,
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "model",
        "layer": "0",
        "object_name": f"ACDB{kind}",
        "dwg_type_name": kind,
        "points": ((0.0, 0.0), (1.0, 0.0)),
        "centroid": (0.0, 0.0),
        "closed": False,
        "text": "",
        "block_name": "",
        "block_attributes": {},
        "raw_properties": {},
    }
    record.update(overrides)
    return SourceEntity.from_record(record)


def _registry(route_regex: str = r"(?i)^CABLE ROUTE$") -> SimpleNamespace:
    return SimpleNamespace(
        positive_route_layer_regex=route_regex,
        block_families={},
        insert_layer_families={},
        layers={},
        field_rules={},
        display_label_rules={},
    )


def _cables(view) -> list:
    features, _, _, _ = classify_entities(
        list(view.entities),
        _registry(),
        coverage_policy="warn",
        coverage_allowlist=[],
    )
    return [feature for feature in features if feature.feature_class == "CABLE"]


def _orphan_route(*, layer: str = "CABLE ROUTE") -> SourceEntity:
    return _entity(
        "orphan-route",
        kind="LWPOLYLINE",
        layout="BLOCKDEF:ORPHAN_NETWORK",
        layout_role="block_definition",
        cad_role="block_definition",
        layer=layer,
        points=((10.0, 20.0), (13.0, 24.0)),
        centroid=(11.5, 22.0),
        native_length=5.0,
        raw_properties={
            "transform_facts": {"block_base_point": (10.0, 20.0, 0.0)}
        },
    )


def test_orphan_route_requires_explicit_profile_declaration():
    anchor = _entity("model-anchor", kind="LINE")
    orphan = _orphan_route()

    default_view = build_plan_domain([anchor, orphan])
    reviewed_view = build_plan_domain(
        [anchor, orphan],
        route_layer_pattern=re.compile(r"(?i)^CABLE ROUTE$"),
        include_orphan_blocks=("ORPHAN_NETWORK",),
        plan_domain_authority="reviewed_source_profile",
    )

    assert _cables(default_view) == []
    cables = _cables(reviewed_view)
    assert len(cables) == 1
    recovered = next(
        entity
        for entity in reviewed_view.entities
        if entity.entity_key.startswith("plan:")
    )
    assert recovered.points == orphan.points
    assert recovered.raw_properties["provenance"][
        "orphan_block_recovery"
    ] == {
        "block_name": "ORPHAN_NETWORK",
        "authority": "reviewed_source_profile",
    }


def test_recovered_non_route_block_geometry_stays_evidence_only():
    view = build_plan_domain(
        [_entity("model-anchor", kind="LINE"), _orphan_route(layer="LEGEND")],
        route_layer_pattern=re.compile(r"(?i)^CABLE ROUTE$"),
        include_orphan_blocks=("ORPHAN_NETWORK",),
        plan_domain_authority="reviewed_source_profile",
    )

    assert _cables(view) == []


def test_reviewed_paper_layout_is_admitted_without_global_layout_rewrite():
    anchor = _entity("model-anchor", kind="LINE")
    paper_route = _entity(
        "paper-route",
        kind="LINE",
        layout="APD - SF",
        layout_role="layout",
        cad_role="layout",
        layer="CABLE ROUTE",
    )

    view = build_plan_domain(
        [anchor, paper_route],
        route_layer_pattern=re.compile(r"(?i)^CABLE ROUTE$"),
        plan_layouts=("APD - SF",),
        plan_domain_authority="reviewed_source_profile",
    )

    assert len(_cables(view)) == 1
    assert view.diagnostics["plan_layouts"]["admitted"] == ["APD - SF"]


def test_unreviewed_plan_domain_declarations_remain_evidence_only():
    anchor = _entity("model-anchor", kind="LINE")
    orphan = _orphan_route()
    paper_route = _entity(
        "paper-route",
        kind="LINE",
        layout="APD - SF",
        layout_role="layout",
        cad_role="layout",
        layer="CABLE ROUTE",
    )

    view = build_plan_domain(
        [anchor, orphan, paper_route],
        route_layer_pattern=re.compile(r"(?i)^CABLE ROUTE$"),
        plan_layouts=("APD - SF",),
        include_orphan_blocks=("ORPHAN_NETWORK",),
    )

    assert _cables(view) == []
    assert view.diagnostics["plan_layouts"]["admitted"] == []
    assert any(
        issue["code"] == "unreviewed_plan_domain_declaration"
        for issue in view.diagnostics["issues"]
    )


def test_reviewed_route_layer_can_restore_reader_reclassified_root():
    anchor = _entity("model-anchor", kind="LINE")
    route = _entity(
        "reclassified-route",
        kind="LINE",
        cad_role="style_legend",
        layer="CABLE ROUTE",
        raw_properties={
            "cad_role_original": "model",
            "role_reclassification": {
                "rule": "fixture",
                "from": "model",
                "to": "style_legend",
            },
        },
    )

    view = build_plan_domain(
        [anchor, route],
        route_layer_pattern=re.compile(r"(?i)^CABLE ROUTE$"),
    )

    assert len(_cables(view)) == 1
    assert view.diagnostics["route_layer_exemption"]["exempted_count"] == 1
