"""Architecture-level regressions for the evidence-first v3 pipeline."""

import hashlib
import json
import math
import sqlite3
from pathlib import Path

import pytest
from osgeo import ogr

from cad2gis_v3.calibration import GroundControlPoint, ValidationSettings, fit_calibration
from cad2gis_v3.config import MappingRegistry, SourceProfile
from cad2gis_v3.evidence import (
    _span_dimension_statuses,
    _write_calibration_evidence,
    write_evidence,
)
from cad2gis_v3.georef import (
    DeliveryTransformer,
    DirectTransformer,
    enrich_delivery_metrics,
    feature_adjustment_records,
)
from cad2gis_v3.model import CadStyle, Feature, Relation, SourceEntity
from cad2gis_v3 import pipeline as pipeline_module
from cad2gis_v3.pipeline import _enforce_geometry_policy, _publish_run_bundle
from cad2gis_v3.ports import build_port_candidates
from cad2gis_v3.semantics import _assign_family_annotations, _registry_attributes
from cad2gis_v3.styles import write_styles
from cad2gis_v3.topology import build_topology
from cad2gis_v3.warehouse import LAYER_ORDER, write_delivery


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_source_profile.json"
REGISTRY = ROOT / "config" / "apd_mapping_registry.json"


def test_complete_run_bundle_is_published_as_one_directory(tmp_path):
    destination = tmp_path / "run"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    staged = tmp_path / ".run.stage.test"
    staged.mkdir()
    (staged / "run_manifest.json").write_text("complete", encoding="utf-8")

    _publish_run_bundle(staged, destination)

    assert not staged.exists()
    assert not (destination / "old.txt").exists()
    assert (destination / "run_manifest.json").read_text(encoding="utf-8") == "complete"


def test_run_bundle_publish_restores_previous_directory_on_swap_failure(
    tmp_path, monkeypatch,
):
    destination = tmp_path / "run"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    staged = tmp_path / ".run.stage.test"
    staged.mkdir()
    (staged / "new.txt").write_text("new", encoding="utf-8")
    real_replace = pipeline_module.os.replace

    def fail_new_bundle(source, target):
        if Path(source).resolve() == staged.resolve() and Path(target).resolve() == destination.resolve():
            raise PermissionError("synthetic publication lock")
        return real_replace(source, target)

    monkeypatch.setattr(pipeline_module.os, "replace", fail_new_bundle)
    with pytest.raises(PermissionError, match="synthetic publication lock"):
        _publish_run_bundle(staged, destination)

    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert (staged / "new.txt").read_text(encoding="utf-8") == "new"


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


def _source_for_feature(feature):
    return SourceEntity(
        entity_key=feature.source_entity_key,
        source_sha256="x",
        source_file="a.dwg",
        handle=feature.source_handle,
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer=feature.source_layer,
        object_name="ACDBBLOCKREFERENCE",
        dwg_type="INSERT",
        points=tuple(feature.native_points),
        centroid=feature.native_centroid,
        closed=False,
        text="",
        block_name="PTECH",
        block_attributes={},
        style=CadStyle(),
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


def test_accepted_gcp_transform_updates_metrics_without_editing_native_geometry():
    nominal = DirectTransformer("EPSG:3857", "EPSG:9481")
    cad_points = [
        (13_680_900.0, 68_440.0), (13_683_500.0, 68_500.0),
        (13_680_950.0, 70_200.0), (13_683_450.0, 70_150.0),
        (13_682_000.0, 69_300.0),
    ]
    pivot = nominal.point(cad_points[0])
    angle = math.radians(0.25)
    scale = 1.0002

    def surveyed(cad_point):
        easting, northing = nominal.point(cad_point)
        x, y = easting - pivot[0], northing - pivot[1]
        return (
            pivot[0] + scale * (math.cos(angle) * x - math.sin(angle) * y) + 3.0,
            pivot[1] + scale * (math.sin(angle) * x + math.cos(angle) * y) - 2.0,
        )

    controls = tuple(
        GroundControlPoint(
            point_id=f"GCP-{index}", cad_point=point, target_point=surveyed(point),
            target_crs="EPSG:9481", role="check" if index == 4 else "train",
            source="synthetic integration fixture", accuracy_m=0.1, weight=1.0,
        )
        for index, point in enumerate(cad_points)
    )
    result = fit_calibration(
        controls, nominal, model="similarity",
        validation=ValidationSettings(
            max_check_rmse_m=1e-5,
            max_check_p95_m=1e-5,
            max_check_error_m=1e-5,
            min_check_points=1,
            affine_min_improvement_ratio=None,
            spatial_distribution_reviewed=True,
            spatial_distribution_review_source="synthetic integration fixture",
        ),
    )
    assert result.validation_passed is True
    delivery = DeliveryTransformer(nominal, result)
    point = _feature("point", "PTECH", [cad_points[0]])
    route = _feature("route", "CABLE", cad_points[:3], role="SOURCE_ROUTE")
    original_point = list(point.native_points)
    original_route = list(route.native_points)
    enrich_delivery_metrics([point, route], delivery)

    assert (point.attributes["X"], point.attributes["Y"]) == pytest.approx(
        delivery.point(cad_points[0]), abs=1e-8,
    )
    delivery_route = delivery.points(cad_points[:3])
    assert route.attributes["delivery_grid_length_m"] == pytest.approx(
        sum(math.dist(a, b) for a, b in zip(delivery_route, delivery_route[1:])),
        abs=1e-8,
    )
    assert "GCP-similarity" in point.field_provenance["X"]
    assert "delivery_style_qgis_rotation_deg" in point.attributes
    assert point.native_points == original_point
    assert route.native_points == original_route

    lineage = feature_adjustment_records([route], nominal, delivery)[0]
    assert json.loads(lineage["native_points_json"]) == [list(item) for item in cad_points[:3]]
    assert json.loads(lineage["nominal_points_json"]) == [
        list(item) for item in nominal.points(cad_points[:3])
    ]
    assert json.loads(lineage["adjusted_points_json"]) == [
        list(item) for item in delivery_route
    ]
    for coordinate_space in ("native", "nominal", "adjusted"):
        payload = lineage[f"{coordinate_space}_points_json"]
        assert lineage[f"{coordinate_space}_fingerprint"] == hashlib.sha256(
            payload.encode("ascii")
        ).hexdigest()


def test_calibration_audit_tables_keep_target_residuals_out_of_delivery(tmp_path):
    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    path = tmp_path / "calibration_evidence.gpkg"
    dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    native_points_json = "[[1.0,2.0],[3.0,4.0]]"
    nominal_points_json = "[[10.0,20.0],[30.0,40.0]]"
    adjusted_points_json = "[[12.0,19.0],[32.0,39.0]]"
    audit = {
        "profile_path": "reviewed.json",
        "profile_sha256": "a" * 64,
        "source_sha256": "b" * 64,
        "status": "accepted",
        "result": {
            "selected_model": "translation",
            "parameters": {"pivot_shift_e_m": 2.0, "pivot_shift_n_m": -1.0},
            "train_metrics": {"count": 3, "rmse_m": 0.1, "p95_m": 0.1, "max_m": 0.1},
            "check_metrics": {"count": 1, "rmse_m": 0.2, "p95_m": 0.2, "max_m": 0.2},
            "validation": {"passed": True, "failures": []},
        },
        "observations": [{
            "point_id": "CHECK-1", "role": "check", "source": "fixture",
            "accuracy_m": 0.1, "weight": 1.0, "enabled": True, "inlier": None,
            "cad_x": 1.0, "cad_y": 2.0,
            "nominal_easting": 10.0, "nominal_northing": 20.0,
            "predicted_easting": 12.0, "predicted_northing": 19.0,
            "observed_easting": 12.1, "observed_northing": 19.1,
            "residual_dx_m": 0.1, "residual_dy_m": 0.1,
            "residual_m": math.sqrt(0.02), "status": "independent_check",
        }],
        "feature_displacements": [{
            "feature_key": "CABLE-1", "feature_class": "CABLE",
            "source_entity_key": "entity-CABLE-1",
            "native_points_json": native_points_json,
            "nominal_points_json": nominal_points_json,
            "adjusted_points_json": adjusted_points_json,
            "native_fingerprint": hashlib.sha256(native_points_json.encode("ascii")).hexdigest(),
            "nominal_fingerprint": hashlib.sha256(nominal_points_json.encode("ascii")).hexdigest(),
            "adjusted_fingerprint": hashlib.sha256(adjusted_points_json.encode("ascii")).hexdigest(),
            "model": "translation",
            "nominal_centroid_easting": 20.0, "nominal_centroid_northing": 30.0,
            "adjusted_centroid_easting": 22.0, "adjusted_centroid_northing": 29.0,
            "centroid_dx_m": 2.0, "centroid_dy_m": -1.0,
            "mean_displacement_m": math.sqrt(5.0),
            "max_displacement_m": math.sqrt(5.0),
        }],
    }
    _write_calibration_evidence(dataset, audit, transformer.target)
    dataset.Close()
    dataset = None
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM georef_models").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM gcp_observations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM gcp_residual_vectors").fetchone()[0] == 1
        assert connection.execute(
            "SELECT srs_id FROM gpkg_geometry_columns WHERE table_name='gcp_residual_vectors'"
        ).fetchone()[0] == 9481
        lineage = connection.execute(
            "SELECT native_points_json, nominal_points_json, adjusted_points_json, "
            "native_fingerprint, nominal_fingerprint, adjusted_fingerprint "
            "FROM georef_feature_lineage"
        ).fetchone()
        assert lineage[:3] == (
            native_points_json, nominal_points_json, adjusted_points_json,
        )
        assert lineage[3:] == tuple(
            hashlib.sha256(payload.encode("ascii")).hexdigest()
            for payload in (native_points_json, nominal_points_json, adjusted_points_json)
        )
    finally:
        connection.close()


def test_display_label_is_written_to_field_provenance(tmp_path):
    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    feature = _feature("P-LABEL", "PTECH", [(13_681_914.403, 69_386.445)])
    feature.display_label = "MR.DMPH.P001"
    feature.label_provenance = "DWG_DERIVED:annotation-assignment-A1"
    feature.field_provenance["CODE"] = "DWG_DERIVED:test-fixture"
    path = tmp_path / "label_evidence.gpkg"

    write_evidence(
        path,
        [_source_for_feature(feature)],
        [feature],
        [],
        [],
        {},
        transformer.source,
    )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT field_value, provenance FROM field_provenance "
            "WHERE feature_key=? AND field_name='display_label'",
            (feature.feature_key,),
        ).fetchone() == (feature.display_label, feature.label_provenance)


@pytest.mark.parametrize("label_provenance", ["", "UNAVAILABLE", " unavailable "])
def test_nonempty_display_label_without_provenance_fails_closed(
    tmp_path, label_provenance,
):
    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    feature = _feature("P-BAD-LABEL", "PTECH", [(13_681_914.403, 69_386.445)])
    feature.display_label = "MR.DMPH.P001"
    feature.label_provenance = label_provenance
    feature.field_provenance["CODE"] = "DWG_DERIVED:test-fixture"

    with pytest.raises(RuntimeError, match="display_label lacks explicit label provenance"):
        write_evidence(
            tmp_path / "bad_label_evidence.gpkg",
            [_source_for_feature(feature)],
            [feature],
            [],
            [],
            {},
            transformer.source,
        )


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
