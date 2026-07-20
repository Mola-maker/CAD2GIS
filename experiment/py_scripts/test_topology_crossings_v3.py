"""Focused source-route crossing and component diagnostics."""

from pathlib import Path

from cad2gis_v3.config import MappingRegistry, SourceProfile
from cad2gis_v3.model import CadStyle, Feature
from cad2gis_v3.topology import build_topology


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_source_profile.json"
REGISTRY = ROOT / "config" / "apd_mapping_registry.json"


def _registry():
    profile = SourceProfile.load(PROFILE)
    return MappingRegistry.load(REGISTRY, profile.source_sha256)


def _route(key, points):
    return Feature(
        feature_key=key,
        feature_class="CABLE",
        geometry_kind="LineString",
        native_points=list(points),
        source_entity_key=f"entity-{key}",
        source_handle=key,
        source_layer="Cable Line A (FO Cable 24C_2T)",
        geometry_role="SOURCE_ROUTE",
        style=CadStyle(aci_color=3),
        attributes={"CODE": key},
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )


def test_proper_crossing_is_candidate_evidence_not_a_connection():
    routes = [
        _route("R-A", [(0.0, 0.0), (10.0, 10.0)]),
        _route("R-B", [(0.0, 10.0), (10.0, 0.0)]),
    ]
    source_points = [tuple(route.native_points) for route in routes]

    relations, _, diagnostics = build_topology(
        [], routes, _registry(), [], [],
    )

    assert [tuple(route.native_points) for route in routes] == source_points
    assert diagnostics["synthetic_route_vertices"] == 0
    assert diagnostics["source_route_components"] == 2
    assert diagnostics["source_route_graph"]["components"] == 2
    assert diagnostics["source_route_component_diagnostics"] == {
        "route_group_components": 2,
        "source_segment_graph_components": 2,
        "status": "consistent",
    }
    assert diagnostics["route_segment_intersection_counts"] == {
        "proper_interior_crossing": 1,
        "shared_source_segment_endpoint": 0,
        "source_endpoint_on_segment": 0,
        "collinear_overlap": 0,
        "collinear_endpoint_on_segment": 0,
    }
    assert len(diagnostics["route_crossing_candidates"]) == 1
    candidate = diagnostics["route_crossing_candidates"][0]
    assert candidate["classification"] == "proper_interior_crossing"
    assert candidate["status"] == "candidate_not_connection"
    assert candidate["intersection_native"] == [5.0, 5.0]
    assert candidate["route_a_key"] == "R-A"
    assert candidate["route_b_key"] == "R-B"
    assert diagnostics["route_shared_segment_endpoints"] == []

    assert not [
        relation for relation in relations
        if relation.relation_kind == "connects"
    ]
    crossing_relations = [
        relation for relation in relations
        if relation.relation_kind == "crossing_candidate"
    ]
    assert len(crossing_relations) == 1
    assert crossing_relations[0].status == "candidate"
    assert candidate["relation_key"] == crossing_relations[0].relation_key
    assert crossing_relations[0].evidence_keys == (
        "entity-R-A", "entity-R-B",
    )

    reversed_relations, _, reversed_diagnostics = build_topology(
        [],
        [
            _route("R-B", [(0.0, 10.0), (10.0, 0.0)]),
            _route("R-A", [(0.0, 0.0), (10.0, 10.0)]),
        ],
        _registry(), [], [],
    )
    assert reversed_diagnostics["route_crossing_candidates"] == [candidate]
    assert [
        relation.relation_key for relation in reversed_relations
        if relation.relation_kind == "crossing_candidate"
    ] == [crossing_relations[0].relation_key]


def test_shared_route_endpoint_is_not_reported_as_proper_crossing():
    routes = [
        _route("R-A", [(0.0, 0.0), (5.0, 5.0)]),
        _route("R-B", [(5.0, 5.0), (10.0, 0.0)]),
    ]

    relations, _, diagnostics = build_topology(
        [], routes, _registry(), [], [],
    )

    assert diagnostics["source_route_components"] == 1
    assert diagnostics["source_route_graph"]["components"] == 1
    assert diagnostics["route_crossing_candidates"] == []
    assert diagnostics["route_segment_intersection_counts"] == {
        "proper_interior_crossing": 0,
        "shared_source_segment_endpoint": 1,
        "source_endpoint_on_segment": 0,
        "collinear_overlap": 0,
        "collinear_endpoint_on_segment": 0,
    }
    shared = diagnostics["route_shared_segment_endpoints"][0]
    assert shared["classification"] == "shared_source_segment_endpoint"
    assert shared["intersection_native"] == [5.0, 5.0]
    assert not [
        relation for relation in relations
        if relation.relation_kind in {"connects", "crossing_candidate"}
    ]


def test_component_diagnostics_flag_route_group_and_segment_graph_mismatch():
    routes = [
        _route("R-A", [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]),
        _route("R-B", [(5.0, 0.0), (5.0, 5.0)]),
    ]

    _, unresolved, diagnostics = build_topology(
        [], routes, _registry(), [], [],
    )

    assert diagnostics["source_route_components"] == 2
    assert diagnostics["source_route_graph"]["components"] == 1
    assert diagnostics["source_route_component_diagnostics"] == {
        "route_group_components": 2,
        "source_segment_graph_components": 1,
        "status": "mismatch",
    }
    assert {
        "kind": "source_route_components",
        "status": "route_group_source_segment_graph_mismatch",
        "route_group_components": 2,
        "source_segment_graph_components": 1,
    } in unresolved


def test_partial_collinear_overlap_is_non_connection_evidence():
    routes = [
        _route("R-A", [(0.0, 0.0), (10.0, 0.0)]),
        _route("R-B", [(5.0, 0.0), (15.0, 0.0)]),
    ]
    source_points = [tuple(route.native_points) for route in routes]

    relations, _, diagnostics = build_topology(
        [], routes, _registry(), [], [],
    )

    assert [tuple(route.native_points) for route in routes] == source_points
    assert diagnostics["synthetic_route_vertices"] == 0
    assert diagnostics["source_route_components"] == 2
    assert diagnostics["source_route_graph"]["components"] == 2
    assert diagnostics["route_segment_intersection_counts"] == {
        "proper_interior_crossing": 0,
        "shared_source_segment_endpoint": 0,
        "source_endpoint_on_segment": 0,
        "collinear_overlap": 1,
        "collinear_endpoint_on_segment": 2,
    }
    overlap = diagnostics["route_collinear_overlaps"][0]
    assert overlap["classification"] == "collinear_overlap"
    assert overlap["status"] == "observed_not_connection"
    assert overlap["route_a_segment_key"] == "R-A:segment:0"
    assert overlap["route_b_segment_key"] == "R-B:segment:0"
    assert overlap["overlap_start_native"] == [5.0, 0.0]
    assert overlap["overlap_end_native"] == [10.0, 0.0]
    assert overlap["overlap_length_native_m"] == 5.0
    assert overlap["route_a_fraction_interval"] == [0.5, 1.0]
    assert overlap["route_b_fraction_interval"] == [0.0, 0.5]
    assert len(overlap["observation_key"]) == 64
    assert {
        (
            contact["route_a_position"],
            contact["route_b_position"],
            tuple(contact["intersection_native"]),
        )
        for contact in diagnostics["route_collinear_endpoint_on_segment"]
    } == {
        ("end", "interior", (10.0, 0.0)),
        ("interior", "start", (5.0, 0.0)),
    }
    assert not [
        relation for relation in relations
        if relation.relation_kind in {"connects", "crossing_candidate"}
    ]

    _, _, reversed_diagnostics = build_topology(
        [],
        [
            _route("R-B", [(5.0, 0.0), (15.0, 0.0)]),
            _route("R-A", [(0.0, 0.0), (10.0, 0.0)]),
        ],
        _registry(), [], [],
    )
    assert reversed_diagnostics["route_collinear_overlaps"] == [overlap]
    assert (
        reversed_diagnostics["route_collinear_endpoint_on_segment"]
        == diagnostics["route_collinear_endpoint_on_segment"]
    )


def test_collinear_endpoint_on_interior_survives_shared_endpoint_classification():
    routes = [
        _route("R-A", [(0.0, 0.0), (10.0, 0.0)]),
        _route("R-B", [(5.0, 0.0), (10.0, 0.0)]),
    ]

    relations, _, diagnostics = build_topology(
        [], routes, _registry(), [], [],
    )

    assert diagnostics["source_route_components"] == 1
    assert diagnostics["source_route_graph"]["components"] == 1
    assert diagnostics["route_segment_intersection_counts"] == {
        "proper_interior_crossing": 0,
        "shared_source_segment_endpoint": 1,
        "source_endpoint_on_segment": 0,
        "collinear_overlap": 1,
        "collinear_endpoint_on_segment": 1,
    }
    contact = diagnostics["route_collinear_endpoint_on_segment"][0]
    assert contact["classification"] == "collinear_endpoint_on_segment"
    assert contact["status"] == "observed_not_connection"
    assert contact["intersection_native"] == [5.0, 0.0]
    assert contact["route_a_position"] == "interior"
    assert contact["route_b_position"] == "start"
    assert len(contact["observation_key"]) == 64
    assert diagnostics["route_shared_segment_endpoints"][0][
        "intersection_native"
    ] == [10.0, 0.0]
    assert not [
        relation for relation in relations
        if relation.relation_kind in {"connects", "crossing_candidate"}
    ]
