"""Architecture-level regressions for the evidence-first v3 pipeline."""

import hashlib
import json
from pathlib import Path

import pytest

from cad2gis_v3.config import MappingRegistry, SourceProfile
from cad2gis_v3.evidence import _span_dimension_statuses
from cad2gis_v3.georef import DirectTransformer, enrich_delivery_metrics
from cad2gis_v3.model import CadStyle, Feature, Relation, SourceEntity
from cad2gis_v3.pipeline import _enforce_geometry_policy
from cad2gis_v3.ports import build_port_candidates
from cad2gis_v3.semantics import _assign_family_annotations, _registry_attributes
from cad2gis_v3.styles import write_styles
from cad2gis_v3.topology import build_topology
from cad2gis_v3.warehouse import LAYER_ORDER, write_delivery


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_source_profile.json"
REGISTRY = ROOT / "config" / "apd_mapping_registry.json"


def _feature(key, feature_class, points, role="SOURCE_ASSET"):
    return Feature(
        feature_key=key,
        feature_class=feature_class,
        geometry_kind="LineString" if feature_class == "CABLE" else "Point",
        native_points=list(points),
        source_entity_key=f"entity-{key}",
        source_handle=key,
        source_layer="Cable Line A (FO Cable 24C_2T)" if feature_class == "CABLE" else feature_class,
        geometry_role=role,
        style=CadStyle(aci_color=3),
        attributes={"CODE": key},
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )


def _dimension(key, start, end):
    return SourceEntity(
        entity_key=key, source_sha256="x", source_file="a.dwg", handle=key,
        layout="Model", layout_role="model", cad_role="model", layer="SPAN CABLE",
        object_name="ACDBDIMENSION", dwg_type="DIMENSION", points=(start, end),
        centroid=((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), closed=False,
        text="", block_name="", block_attributes={}, style=CadStyle(),
        dimension_value=10.0,
    )


def _annotation(key, text, point):
    return SourceEntity(
        entity_key=key, source_sha256="x", source_file="a.dwg", handle=key,
        layout="Model", layout_role="model", cad_role="model", layer="LABEL",
        object_name="ACDBTEXT", dwg_type="TEXT", points=(point,), centroid=point,
        closed=False, text=text, block_name="", block_attributes={}, style=CadStyle(),
    )


def test_registry_binds_policy_to_source_profile():
    profile = SourceProfile.load(PROFILE)
    registry = MappingRegistry.load(REGISTRY, profile.source_sha256)
    assert registry.policy["source_geometry_immutable"] is True
    assert registry.policy["force_route_components_connected"] is False
    assert registry.policy["dimension_is_cable_geometry"] is False
    assert profile.dwg_insunits == 6
    with pytest.raises(ValueError, match="stale"):
        MappingRegistry.load(REGISTRY, "0" * 64)


def test_span_dimensions_never_become_cable_or_force_components():
    profile = SourceProfile.load(PROFILE)
    registry = MappingRegistry.load(REGISTRY, profile.source_sha256)
    supports = [
        _feature("P0", "PTECH", [(0.0, 0.0)]),
        _feature("P1", "PTECH", [(10.0, 0.0)]),
        _feature("P2", "PTECH", [(20.0, 0.0)]),
        _feature("P3", "PTECH", [(30.0, 0.0)]),
    ]
    routes = [
        _feature("R1", "CABLE", [(0.0, 0.0), (10.0, 0.0)], "SOURCE_ROUTE"),
        _feature("R2", "CABLE", [(20.0, 0.0), (30.0, 0.0)], "SOURCE_ROUTE"),
    ]
    source_geometry = [list(route.native_points) for route in routes]
    entities = [_dimension("D1", (10.0, 0.0), (20.0, 0.0))]
    relations, unresolved, diagnostics = build_topology(
        entities, supports + routes, registry, [], [],
    )
    assert diagnostics["source_route_components"] == 2
    assert diagnostics["accepted_span_dimensions"] == 1
    assert [route.native_points for route in routes] == source_geometry
    assert len([feature for feature in supports + routes if feature.feature_class == "CABLE"]) == 2
    assert all(relation.relation_kind != "connects" for relation in relations)
    assert any(relation.method == "unique-dimension-endpoint-support" for relation in relations)


def test_direct_crs_roundtrip_avoids_intermediate_geometry():
    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    points = [(13681914.403, 69386.445), (13683236.666, 68765.958)]
    assert transformer.roundtrip_error(points) < 1e-6
    assert transformer.engine_crosscheck_error(points) < 1e-6
    assert transformer.operation_metadata(points[0])["declared_accuracy_m"] == 1.2


def test_global_annotation_assignment_resolves_greedy_target_collision():
    targets = [
        _feature("T1", "PTECH", [(0.0, 0.0)]),
        _feature("T2", "PTECH", [(10.0, 0.0)]),
    ]
    annotations = [
        _annotation("A1", "MR.DMPH.P001", (0.0, 0.0)),
        _annotation("A2", "MR.DMPH.P002", (0.4, 0.0)),
    ]
    forward, failures, _ = _assign_family_annotations(annotations, targets, 15.0)
    reverse, reverse_failures, _ = _assign_family_annotations(
        list(reversed(annotations)), list(reversed(targets)), 15.0,
    )
    assert failures == reverse_failures == []
    expected = {"A1": "T1", "A2": "T2"}
    assert {annotation.entity_key: target.source_handle for annotation, target, _ in forward} == expected
    assert {annotation.entity_key: target.source_handle for annotation, target, _ in reverse} == expected


def test_reviewed_registry_executes_field_rules_with_rule_provenance():
    profile = SourceProfile.load(PROFILE)
    registry = MappingRegistry.load(REGISTRY, profile.source_sha256)
    entity = SourceEntity(
        entity_key="P", source_sha256="x", source_file="a.dwg", handle="P",
        layout="Model", layout_role="model", cad_role="model", layer="NEW POLE",
        object_name="ACDBBLOCKREFERENCE", dwg_type="INSERT", points=((0.0, 0.0),),
        centroid=(0.0, 0.0), closed=False, text="", block_name="*U13",
        block_attributes={}, style=CadStyle(),
    )
    attributes, provenance = _registry_attributes(entity, "PTECH", registry)
    assert attributes == {"TYPE": "APPUI", "STATUT": "EN PROJET"}
    assert provenance == {
        "TYPE": "DWG_DERIVED:APD-PTECH-TYPE-001",
        "STATUT": "DWG_DERIVED:APD-PTECH-STATUS-001",
    }


def test_projection_enrichment_is_shared_and_provenanced():
    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    point = _feature("P", "PTECH", [(13681914.403, 69386.445)])
    route = _feature(
        "R", "CABLE",
        [(13681914.403, 69386.445), (13681924.403, 69386.445)],
        "SOURCE_ROUTE",
    )
    enrich_delivery_metrics([point, route], transformer)
    assert point.field_provenance["X"] == "DWG_DERIVED:direct-CRS-transform"
    assert point.field_provenance["Y"] == "DWG_DERIVED:direct-CRS-transform"
    assert route.attributes["LONGUEUR"] == route.attributes["delivery_grid_length_m"]
    assert route.field_provenance["LONGUEUR"] == "DWG_DERIVED:EPSG9481-geometry-length"
    assert route.field_provenance["geodesic_length_m"] == "DWG_DERIVED:WGS84-geodesic"


def test_geometry_policy_rejects_source_route_mutation():
    profile = SourceProfile.load(PROFILE)
    registry = MappingRegistry.load(REGISTRY, profile.source_sha256)
    source = SourceEntity(
        entity_key="entity-R", source_sha256="x", source_file="a.dwg", handle="R",
        layout="Model", layout_role="model", cad_role="model",
        layer="Cable Line A (FO Cable 24C_2T)", object_name="ACDBPOLYLINE",
        dwg_type="LWPOLYLINE", points=((0.0, 0.0), (10.0, 0.0)),
        centroid=(5.0, 0.0), closed=False, text="", block_name="",
        block_attributes={}, style=CadStyle(),
    )
    route = _feature("R", "CABLE", source.points, "SOURCE_ROUTE")
    assert _enforce_geometry_policy(
        [source], [route], [], registry, {"synthetic_route_vertices": 0},
    )["cable_sources_checked"] == 1
    route.native_points.append((11.0, 0.0))
    with pytest.raises(RuntimeError, match="displaced or re-vertexed"):
        _enforce_geometry_policy(
            [source], [route], [], registry, {"synthetic_route_vertices": 0},
        )


def test_dimension_evidence_uses_least_certain_endpoint_status():
    relations = [
        Relation("r0", "supported_by", "D1:endpoint:0", "P0", "accepted", "unique"),
        Relation("r1", "supported_by", "D1:endpoint:1", "P1", "candidate", "review"),
        Relation("r2", "supported_by", "D2:endpoint:0", "P0", "accepted", "unique"),
        Relation("r3", "supported_by", "D2:endpoint:1", "P1", "accepted", "unique"),
    ]
    assert _span_dimension_statuses(relations) == {
        "D1": "candidate_endpoint",
        "D2": "accepted_endpoints",
    }


def test_connection_port_uses_transformed_block_geometry_without_moving_route():
    profile = SourceProfile.load(PROFILE)
    registry = MappingRegistry.load(REGISTRY, profile.source_sha256)
    definition = SourceEntity(
        entity_key="def", source_sha256="x", source_file="a.dwg", handle="1",
        layout="BLOCKDEF:*U99", layout_role="block_definition", cad_role="block_definition",
        layer="0", object_name="ACDBLINE", dwg_type="LINE",
        points=((0.0, 0.0), (1.0, 0.0)), centroid=(0.5, 0.0), closed=False,
        text="", block_name="", block_attributes={}, style=CadStyle(),
    )
    instance = SourceEntity(
        entity_key="instance", source_sha256="x", source_file="a.dwg", handle="2",
        layout="Model", layout_role="model", cad_role="model", layer="PTECH",
        object_name="ACDBBLOCKREFERENCE", dwg_type="INSERT", points=((10.0, 0.0),),
        centroid=(10.0, 0.0), closed=False, text="", block_name="*U99",
        block_attributes={}, style=CadStyle(), scale=(2.0, 2.0, 1.0),
    )
    support = _feature("SUPPORT", "PTECH", [(10.0, 0.0)])
    support.source_entity_key = "instance"
    route = _feature("ROUTE", "CABLE", [(12.0, 0.0), (20.0, 0.0)], "SOURCE_ROUTE")
    source_geometry = list(route.native_points)
    candidates = build_port_candidates([definition, instance], [support, route], registry)
    assert candidates[0]["port_point_native"] == [12.0, 0.0]
    assert candidates[0]["status"] == "on_symbol_geometry"
    assert route.native_points == source_geometry


def test_delivery_contains_exactly_eight_business_layers(tmp_path):
    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    path = tmp_path / "delivery.gpkg"
    counts = write_delivery(path, [], transformer)
    style_manifest_path = write_styles(tmp_path / "styles", [], path)
    assert tuple(counts) == LAYER_ORDER
    assert sum(counts.values()) == 0
    import sqlite3
    with sqlite3.connect(path) as connection:
        layers = {
            row[0] for row in connection.execute(
                "SELECT table_name FROM gpkg_contents WHERE data_type='features'"
            )
        }
        style_count, default_count = connection.execute(
            "SELECT COUNT(*), SUM(useAsDefault) FROM layer_styles"
        ).fetchone()
        labels_enabled = connection.execute(
            "SELECT COUNT(*) FROM layer_styles WHERE styleQML LIKE '%labelsEnabled=\"1\"%'"
        ).fetchone()[0]
        rotation_enabled = connection.execute(
            "SELECT COUNT(*) FROM layer_styles WHERE styleQML LIKE '%LabelRotation%' "
            "AND styleQML LIKE '%style_qgis_rotation_deg%'"
        ).fetchone()[0]
        registered_styles = connection.execute(
            "SELECT COUNT(*) FROM gpkg_contents WHERE table_name='layer_styles' AND data_type='attributes'"
        ).fetchone()[0]
        ogr_style_count = connection.execute(
            "SELECT feature_count FROM gpkg_ogr_contents WHERE table_name='layer_styles'"
        ).fetchone()[0]
        cable_fields = {
            row[1] for row in connection.execute('PRAGMA table_info("CABLE")')
        }
    assert layers == set(LAYER_ORDER)
    assert (style_count, default_count, labels_enabled, rotation_enabled) == (8, 8, 8, 8)
    assert (registered_styles, ogr_style_count) == (1, 8)
    assert {"style_rotation_deg", "style_qgis_rotation_deg", "style_render_key"} <= cable_fields
    style_manifest = json.loads(style_manifest_path.read_text(encoding="utf-8"))
    for item in style_manifest["layers"].values():
        qml = style_manifest_path.parent / item["qml"]
        assert hashlib.sha256(qml.read_bytes()).hexdigest() == item["qml_sha256"]
