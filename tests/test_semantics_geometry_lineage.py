"""Source/delivery differences must survive classification as review evidence."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon

from cad2gis.cad2gis_v3.model import CadStyle, SourceEntity
from cad2gis.cad2gis_v3.semantics import classify_entities


def _entity(handle, points, *, layer, kind="LWPOLYLINE", block_name="", red=False):
    return SourceEntity(
        entity_key=f"entity:{handle}", source_sha256="a" * 64,
        source_file="lineage.dwg", handle=handle, layout="Model",
        layout_role="model", cad_role="model", layer=layer,
        object_name=kind, dwg_type=kind, points=points, centroid=points[0],
        closed=points[0] == points[-1], text="", block_name=block_name,
        block_attributes={}, style=CadStyle(aci_color=1 if red else 7),
    )


def _classify(entities, **options):
    return classify_entities(
        entities,
        SimpleNamespace(
            layers={"zpm_boundary": ("PARCEL",)},
            positive_route_layer_regex="^CABLE$",
            block_families={"PTECH": ("POLE",)},
        ),
        coverage_policy="abstain",
        **options,
    )


def test_candidate_only_keeps_route_and_infrastructure_at_source_vertices():
    points = ((0.0, 0.0), (50.0, 0.0), (100.0, 0.0))
    route = _entity("route", points, layer="CABLE")
    pole = _entity("pole", ((0.0, 2.2),), layer="POLES", kind="INSERT", block_name="POLE")
    candidates = []
    features, _, _, _ = _classify([route, pole], apply_geometry_repairs=False, geometry_candidates=candidates)
    for feature in features:
        if feature.feature_class in {"CABLE", "INFRASTRUCTURE"}:
            assert feature.native_points == list(points)
            assert feature.geometry_role == "SOURCE_ROUTE"
    candidate, = candidates
    assert candidate["operation"] == "bridge_cable_endpoint_to_pole"
    assert candidate["source_endpoint"] == [0.0, 0.0]
    assert candidate["target_endpoint"] == [0.0, 2.2]
    assert candidate["applied"] is False


def test_candidate_only_retains_box_insertion_point_in_topology():
    from cad2gis.cad2gis_v3.model import Feature
    from cad2gis.cad2gis_v3.topology import build_topology
    box = Feature("box", "BOITE", "Point", [(0., 12.)], "box-source", "b", "BOX", "SOURCE_INSERT", CadStyle())
    pole = Feature("pole", "PTECH", "Point", [(0., 0.)], "pole-source", "p", "POLE", "SOURCE_INSERT", CadStyle())
    registry = SimpleNamespace(thresholds={"exact": .01, "device_to_support_candidate": 8., "dimension_to_support": .2},
                               layers={}, labels={}, decision_rules={}, policy={})
    candidates = []
    build_topology([], [box, pole], registry, [], [], apply_geometry_repairs=False, geometry_candidates=candidates)
    assert box.native_points == [(0., 12.)]
    assert not box.lineage
    candidate, = candidates
    assert candidate["source_points"] == [(0., 12.)]
    assert candidate["candidate_points"] == [(0., 0.)]
    assert candidate["max_displacement_native"] == 12.


def test_candidate_only_withholds_invalid_boundary_and_preserves_repair_comparison():
    points = ((0., 0.), (100., 0.), (100., 100.), (50., 100.),
              (50., 200.), (50., 100.), (0., 100.), (0., 0.))
    boundary = _entity("boundary", points, layer="RED BOUNDARY", red=True)
    cable = _entity("route", ((10., 10.), (60., 10.)), layer="CABLE")
    candidates = []
    features, _, unresolved, _ = _classify([boundary, cable], apply_geometry_repairs=False, geometry_candidates=candidates)
    assert not any(f.geometry_role == "DERIVED_BOUNDARY_REPAIR" for f in features)
    candidate, = [item for item in candidates if item["operation"] == "repair_boundary_polygon"]
    assert candidate["source_points"] == list(points)
    assert candidate["candidate_points"] != list(points)
    assert candidate["applied"] is False
    assert any(item.get("status") == "withheld_pending_review" for item in unresolved)


@pytest.mark.parametrize("spike,area_delta", [
    (((50.0, 100.0), (50.0, 200.0), (50.0, 100.0)), 0.0),
    (((50.0, 100.0), (50.0, 200.0), (50.1, 100.0), (50.0, 100.0)), 5.0),
])
def test_repaired_boundary_reports_removed_spike_and_requires_review(spike, area_delta):
    points = (
        (0.0, 0.0), (100.0, 0.0), (100.0, 100.0),
        *spike, (0.0, 100.0), (0.0, 0.0),
    )
    boundary = _entity("boundary", points, layer="RED BOUNDARY", red=True)
    parcel = _entity(
        "parcel", ((10.0, 10.0), (20.0, 10.0), (20.0, 20.0),
                   (10.0, 20.0), (10.0, 10.0)), layer="PARCEL",
    )
    original = deepcopy(boundary)
    features, _, unresolved, diagnostics = _classify([boundary, parcel])
    feature, = [item for item in features if item.feature_class == "ZNRO"]

    assert boundary == original
    assert feature.geometry_role == "DERIVED_BOUNDARY_REPAIR"
    assert Polygon(feature.native_points).is_valid
    repair, = feature.lineage
    assert repair["operation"] == "repair_boundary_polygon"
    assert repair["source_entity_key"] == boundary.entity_key
    assert repair["lossy"] and repair["geometry_changed"]
    assert repair["review_status"] == "required"
    assert "Self-intersection" in repair["source_validity"]
    assert repair["max_displacement_m"] == pytest.approx(100.0)
    assert repair["area_delta_m2"] == pytest.approx(area_delta)
    assert repair["source_shoelace_area_m2"] == pytest.approx(10000.0 - area_delta)
    assert repair["result_area_m2"] == pytest.approx(10000.0)
    assert repair["source_vertex_count"] == len(points)
    assert repair["result_vertex_count"] < len(points)
    assert repair["discarded_interior_ring_count"] == 0
    review, = diagnostics["source_boundary_repairs"]
    assert review["feature_key"] == feature.feature_key
    assert review["status"] == "review_required"
    assert review in unresolved


def test_valid_boundary_keeps_exact_source_vertex_order_and_identity():
    points = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0),
              (0.0, 100.0), (0.0, 0.0))
    boundary = _entity("boundary", points, layer="RED BOUNDARY", red=True)
    cable = _entity("route", ((10.0, 10.0), (60.0, 10.0), (90.0, 90.0)), layer="CABLE")
    features, _, unresolved, diagnostics = _classify([boundary, cable])
    feature, = [item for item in features if item.feature_class == "ZNRO"]
    assert feature.native_points == list(points)
    assert feature.geometry_role == "SOURCE_BOUNDARY"
    assert feature.lineage == [{
        "operation": "identity", "source_entity_key": boundary.entity_key,
        "max_displacement_m": 0.0,
    }]
    assert not diagnostics["source_boundary_repairs"]
    assert not any(item.get("kind") == "source_boundary_repair" for item in unresolved)


@pytest.mark.parametrize("gap", [0.0, 2.2])
def test_infrastructure_preserves_cable_endpoint_bridge_lineage_and_source_facts(gap):
    points = ((0.0, 0.0), (50.0, 0.0), (100.0, 0.0))
    route = _entity("route", points, layer="CABLE")
    pole = _entity("pole", ((0.0, gap),), layer="POLES", kind="INSERT", block_name="POLE")
    original = deepcopy(route)
    features, _, _, _ = _classify([route, pole])
    cable, = [item for item in features if item.feature_class == "CABLE"]
    infra, = [item for item in features if item.feature_class == "INFRASTRUCTURE"]

    assert route == original
    assert infra.native_points == cable.native_points
    assert tuple(infra.native_points[0]) == (0.0, gap)
    assert infra.geometry_role == cable.geometry_role == ("DERIVED_ROUTE" if gap else "SOURCE_ROUTE")
    assert infra.lineage == cable.lineage
    assert infra.source_entity_key == route.entity_key
    if gap:
        assert infra.lineage[-1] == {
            "operation": "bridge_cable_endpoint_to_pole",
            "source_entity_key": pole.entity_key,
            "route_source_entity_key": route.entity_key,
            "support_feature_key": next(item.feature_key for item in features if item.feature_class == "PTECH"),
            "support_handle": pole.handle,
            "endpoint_index": 0,
            "source_endpoint": [0.0, 0.0],
            "target_endpoint": [0.0, gap],
            "max_displacement_m": pytest.approx(gap),
        }
        # The same evidence must persist, without aliasing mutable feature state.
        infra.native_points[0][0] = 999.0
        assert cable.native_points[0][0] == 0.0
    infra.lineage[0]["max_displacement_m"] = 999.0
    assert cable.lineage[0]["max_displacement_m"] == 0.0


@pytest.mark.parametrize("tamper", [None, "interior", "target", "distance", "missing", "role", "curve", "index", "old", "second", "identity", "bulge", "support", "duplicate_identity", "support_handle"])
def test_source_geometry_gate_replays_only_evidenced_endpoint_derivation(tamper):
    from cad2gis.cad2gis_v3.pipeline import _validate_source_geometry
    route = _entity("route", ((0.0, 0.0), (50.0, 0.0), (100.0, 0.0)), layer="CABLE")
    pole = _entity("pole", ((0.0, 2.2),), layer="POLES", kind="INSERT", block_name="POLE")
    features, _, _, _ = _classify([route, pole])
    cable = next(item for item in features if item.feature_class == "CABLE")
    if tamper == "interior":
        cable.native_points[1] = (50.0, 10.0)
    elif tamper == "target":
        cable.lineage[-1]["target_endpoint"] = [1.0, 2.2]
    elif tamper == "distance":
        cable.lineage[-1]["max_displacement_m"] = 0.0
    elif tamper == "missing":
        del cable.lineage[-1]["endpoint_index"]
    elif tamper == "role":
        cable.geometry_role = "SOURCE_ROUTE"
    elif tamper == "index":
        cable.lineage[-1]["endpoint_index"] = 1
    elif tamper == "old":
        cable.lineage[-1]["source_endpoint"] = [1.0, 0.0]
    elif tamper == "second":
        cable.lineage.append(deepcopy(cable.lineage[-1]))
    elif tamper == "identity":
        cable.lineage[0]["source_entity_key"] = "foreign"
    elif tamper == "bulge":
        route.curve_facts["bulges"] = [0.5, 0.0, 0.0]
    elif tamper == "support":
        support = next(item for item in features if item.feature_class == "PTECH")
        support.native_points[0] = (0.0, 3.0)
        cable.native_points[0] = (0.0, 3.0)
        cable.lineage[-1]["target_endpoint"] = [0.0, 3.0]
        cable.lineage[-1]["max_displacement_m"] = 3.0
    elif tamper == "duplicate_identity":
        cable.lineage.insert(0, deepcopy(cable.lineage[0]))
    elif tamper == "support_handle":
        cable.lineage[-1]["support_handle"] = "foreign"
    registry = SimpleNamespace(positive_route_layer_regex="^CABLE$", layers={})
    if tamper:
        with pytest.raises(RuntimeError, match="Source geometry validation failed"):
            _validate_source_geometry([route, pole], features, registry, require_curve_facts=tamper == "curve")
    else:
        assert _validate_source_geometry([route, pole], features, registry)["source_geometry_immutable"]


def test_curved_route_is_not_bridged_or_flattened():
    from dataclasses import replace
    from cad2gis.cad2gis_v3.curve_geometry import materialize_cable_features, CableGeometryMaterializationError
    route = _entity("route", ((0.0, 0.0), (50.0, 0.0), (100.0, 0.0)), layer="CABLE")
    curved = replace(route, curve_facts={
        "schema_version": "cad2gis-curve-facts-v1", "primitive_type": "LWPOLYLINE",
        "vertices_wcs": [[0.0, 0.0, 0.0], [50.0, 0.0, 0.0], [100.0, 0.0, 0.0]],
        "bulges": [0.5, 0.0, 0.0], "closed": False,
    })
    pole = _entity("pole", ((0.0, 2.2),), layer="POLES", kind="INSERT", block_name="POLE")
    features, _, _, _ = _classify([curved, pole])
    cable = next(item for item in features if item.feature_class == "CABLE")
    assert cable.geometry_role == "SOURCE_ROUTE"
    assert cable.native_points == list(curved.points)
    cable.native_points[0] = (0.0, 2.2)
    cable.lineage.append({"operation": "bridge_cable_endpoint_to_pole"})
    with pytest.raises(CableGeometryMaterializationError, match="flatten curved"):
        materialize_cable_features([curved, pole], features)
