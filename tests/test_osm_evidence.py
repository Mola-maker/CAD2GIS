"""Topology/nearest-neighbour failures that can falsify visual map evidence."""

import copy

import numpy as np
import pytest

from cad2gis.cad2gis_v3.osm_evidence import RoadSegmentIndex, cad_topology, osm_topology
from cad2gis.cad2gis_v3.osm_matching import _normalise_roads, _parse_roads


def road(way_id, points, node_ids=None, **tags):
    return {"osm_way_id": way_id, "points": points, "node_ids": node_ids or [], **tags}


def test_osm_shared_interior_node_has_real_adjacency_and_degree():
    roads = [road(10, [[-10, 0], [0, 0], [10, 0]], [1, 2, 3]),
             road(11, [[0, 0], [0, 10]], [2, 4])]
    topology = osm_topology(roads)
    junction = next(node for node in topology["nodes"] if node["node_id"] == 2)
    assert junction["degree"] == 3
    assert junction["adjacent_node_ids"] == [1, 3, 4]
    assert junction["osm_way_ids"] == [10, 11]
    assert junction["junction"] is True
    assert topology["geometric_crossings_inferred"] is False


def test_bridge_crossing_and_coincident_distinct_ids_do_not_invent_connections():
    roads = [road(10, [[-10, 0], [0, 0], [10, 0]], [1, 2, 3], layer="0"),
             road(11, [[0, -10], [0, 0], [0, 10]], [4, 5, 6], layer="1", bridge="yes")]
    original = copy.deepcopy(roads)
    topology = osm_topology(roads)
    assert len(topology["nodes"]) == 6
    assert not any(node["junction"] for node in topology["nodes"])
    assert roads == original
    normalised = _normalise_roads(roads)
    assert normalised[1]["bridge"] == "yes"
    assert normalised[1]["layer"] == "1"
    assert normalised[1]["node_ids"] == [4, 5, 6]


def test_geometry_only_roads_are_not_promoted_to_topological_evidence():
    topology = osm_topology([road(10, [[-10, 0], [10, 0]]), road(11, [[0, -10], [0, 10]])])
    assert topology["nodes"] == []
    assert topology["geometry_only_way_count"] == 2
    assert topology["ways_with_node_ids"] == 0


def test_repeated_osm_way_edge_does_not_inflate_junction_degree():
    topology = osm_topology([road(10, [[0, 0], [10, 0], [0, 0]], [1, 2, 1]),
                             road(11, [[0, 0], [10, 0]], [1, 2])])
    assert [node["degree"] for node in topology["nodes"]] == [1, 1]


def test_shared_osm_id_with_conflicting_positions_is_rejected():
    with pytest.raises(ValueError, match="conflicting coordinates"):
        osm_topology([road(10, [[0, 0], [10, 0]], [1, 2]), road(11, [[0, 1], [0, 10]], [1, 3])])


def test_overpass_keeps_node_vertex_alignment_when_distinct_nodes_coincide():
    payload = {"elements": [{"type": "way", "id": 10, "nodes": [1, 2, 3],
                             "tags": {"highway": "residential", "layer": "1", "bridge": "yes"},
                             "geometry": [{"lon": 0, "lat": 0}, {"lon": 0, "lat": 0}, {"lon": 0.001, "lat": 0}]}]}
    roads = _parse_roads(payload)
    assert roads[0]["node_ids"] == [1, 2, 3]
    assert len(roads[0]["points"]) == 3
    assert roads[0]["bridge"] == "yes"
    assert len(RoadSegmentIndex(roads).references) == 1


@pytest.mark.parametrize("nodes,geometry", [
    ([1, 2, 3], [{"lon": 0, "lat": 0}, {"lon": 0.001, "lat": 0}]),
    ([1, "2"], [{"lon": 0, "lat": 0}, {"lon": 0.001, "lat": 0}]),
    ([1, 2], [{"lon": 0, "lat": 0}, None]),
])
def test_incomplete_or_misaligned_osm_topology_is_not_silently_repaired(nodes, geometry):
    with pytest.raises(ValueError, match="OSM"):
        _parse_roads({"elements": [{"type": "way", "id": 10, "nodes": nodes, "geometry": geometry,
                                    "tags": {"highway": "residential"}}]})


def test_cad_topology_uses_explicit_vertices_without_inferring_physical_connectivity():
    routes = [[[-10, 0], [10, 0]], [[0, -10], [0, 10]]]
    original = copy.deepcopy(routes)
    topology = cad_topology(routes)
    assert len(topology["nodes"]) == 4
    assert not any(node["junction"] for node in topology["nodes"])
    assert topology["physical_connectivity_verified"] is False
    assert routes == original


@pytest.mark.parametrize("reversed_order", [False, True])
def test_nearest_tie_at_intersection_prefers_compatible_direction(reversed_order):
    roads = [road(10, [[-10, 0], [10, 0]]), road(11, [[0, -10], [0, 10]])]
    if reversed_order:
        roads.reverse()
    index = RoadSegmentIndex(roads)
    result = index.inspect(np.asarray([[0.0, 0.0]]), np.asarray([[1.0, 0.0]]))
    selected = index.references[int(result["segment_ids"][0])]
    assert selected["osm_way_id"] == 10
    assert result["distances"][0] == 0.0
