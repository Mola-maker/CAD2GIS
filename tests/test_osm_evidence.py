"""Topology/nearest-neighbour failures that can falsify visual map evidence."""

import copy

import numpy as np
import pytest

from cad2gis.cad2gis_v3.osm_evidence import RoadSegmentIndex, build_match_evidence, cad_topology, osm_topology
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


@pytest.mark.parametrize("reverse_roads", [False, True])
@pytest.mark.parametrize("reverse_vertices", [False, True])
def test_bounded_inspection_recovers_parallel_road_hidden_by_nearer_crossing(reverse_roads, reverse_vertices):
    roads = [road(10, [[-100, 5], [100, 5]]), road(11, [[0, -100], [0, 100]])]
    if reverse_vertices:
        for item in roads:
            item["points"].reverse()
    if reverse_roads:
        roads.reverse()
    original = copy.deepcopy(roads)
    index = RoadSegmentIndex(roads)
    points, directions = np.asarray([[1.0, 0.0]]), np.asarray([[1.0, 0.0]])
    nearest = index.inspect(points, directions)
    assert index.references[int(nearest["segment_ids"][0])]["osm_way_id"] == 11
    result = index.inspect(points, directions, max_distance=25.0)
    assert index.references[int(result["segment_ids"][0])]["osm_way_id"] == 10
    assert result["distances"].tolist() == [5.0]
    assert result["feet"].tolist() == [[1.0, 5.0]]
    assert roads == original


@pytest.mark.parametrize("reverse_roads", [False, True])
def test_bounded_equidistant_compatible_roads_use_stable_way_identity(reverse_roads):
    roads = [road(20, [[-10, -5], [10, -5]]), road(10, [[-10, 5], [10, 5]])]
    if reverse_roads:
        roads.reverse()
    index = RoadSegmentIndex(roads)
    result = index.inspect([[0, 0]], [[1, 0]], max_distance=5.0)
    assert index.references[int(result["segment_ids"][0])]["osm_way_id"] == 10
    assert result["distances"].tolist() == [5.0]


def test_bounded_inspection_falls_back_per_sample_without_inventing_compatible_support():
    index = RoadSegmentIndex([road(10, [[-100, 26], [100, 26]]),
                              road(11, [[0, -100], [0, 100]])])
    result = index.inspect([[1, 0], [1, 10], [1000, 0]], [[1, 0]] * 3, max_distance=25.0)
    selected = [index.references[int(item)]["osm_way_id"] for item in result["segment_ids"]]
    assert selected == [11, 10, 10]
    assert result["distances"][:2].tolist() == [1.0, 16.0]
    assert result["distances"][2] > 25.0
    alignment = np.abs(result["directions"][:, 0])
    assert ((result["distances"] <= 25.0) & (alignment >= 0.7)).tolist() == [False, True, False]


@pytest.mark.parametrize("reverse_roads", [False, True])
def test_bounded_incompatible_fallback_ties_use_stable_way_identity(reverse_roads):
    roads = [road(20, [[-1, -10], [-1, 10]]), road(10, [[1, -10], [1, 10]])]
    if reverse_roads:
        roads.reverse()
    index = RoadSegmentIndex(roads)
    result = index.inspect([[0, 0]], [[1, 0]], max_distance=25.0)
    assert index.references[int(result["segment_ids"][0])]["osm_way_id"] == 10
    assert result["distances"].tolist() == [1.0]
    assert result["directions"][0, 0] == 0.0


def test_bounded_inspection_uses_circular_segment_distance_and_inclusive_radius():
    # The farther road's bounding box is inside the search square, but the
    # segment itself lies outside the circular radius.
    index = RoadSegmentIndex([road(10, [[24, 24], [30, 24]]),
                              road(11, [[-100, 25], [100, 25]]),
                              road(12, [[0, -100], [0, 100]])])
    result = index.inspect([[0, 0]], [[1, 0]], max_distance=25.0)
    assert index.references[int(result["segment_ids"][0])]["osm_way_id"] == 11
    assert result["distances"].tolist() == [25.0]
    result = index.inspect([[0, 0]], [[1, 0]], max_distance=24.99)
    assert index.references[int(result["segment_ids"][0])]["osm_way_id"] == 12


@pytest.mark.parametrize("kwargs", [
    {"max_distance": -1.0}, {"max_distance": float("nan")}, {"max_distance": float("inf")},
    {"max_distance": 25.0, "min_direction_consistency": -0.1},
    {"max_distance": 25.0, "min_direction_consistency": 1.1},
    {"max_distance": 25.0, "min_direction_consistency": float("nan")},
])
def test_bounded_inspection_rejects_invalid_search_bounds(kwargs):
    index = RoadSegmentIndex([road(10, [[-10, 0], [10, 0]])])
    with pytest.raises(ValueError, match="distance|direction"):
        index.inspect([[0, 0]], [[1, 0]], **kwargs)


def test_bounded_inspection_requires_directions():
    index = RoadSegmentIndex([road(10, [[-10, 0], [10, 0]])])
    with pytest.raises(ValueError, match="directions"):
        index.inspect([[0, 0]], max_distance=25.0)


def test_bounded_inspection_only_queries_incompatible_nearest_samples(monkeypatch):
    index = RoadSegmentIndex([road(10, [[-100, 5], [100, 5]]),
                              road(11, [[0, -100], [0, 100]])])
    query = index.tree.query
    queried_centres = []

    def capture(boxes):
        queried_centres.extend([(item.centroid.x, item.centroid.y) for item in boxes])
        return query(boxes)

    monkeypatch.setattr(index.tree, "query", capture)
    # Rows 1 and 3 require a direction-aware query; the others already have a
    # compatible nearest road, including the final out-of-radius fallback.
    points = [[50, 0], [1, 0], [50, 10], [1, 10], [1000, 0]]
    result = index.inspect(points, [[1, 0]] * len(points), max_distance=25.0)
    assert queried_centres == [(1.0, 0.0), (1.0, 10.0)]
    assert [index.references[int(item)]["osm_way_id"] for item in result["segment_ids"]] == [10] * 5
    assert result["feet"].tolist() == [[50, 5], [1, 5], [50, 5], [1, 5], [100, 5]]


def test_bounded_inspection_skips_neighbourhood_queries_when_all_nearest_are_compatible(monkeypatch):
    index = RoadSegmentIndex([road(10, [[-10000, 5], [10000, 5]]),
                              road(11, [[0, -100], [0, 100]])])
    monkeypatch.setattr(index.tree, "query", lambda *_: pytest.fail("Compatible nearest needs no local query"))
    points = np.column_stack((np.arange(1000) + 20.0, np.zeros(1000)))
    result = index.inspect(points, np.tile([1, 0], (len(points), 1)), max_distance=25.0)
    assert result["distances"] == pytest.approx(np.full(1000, 5.0))
    assert result["segment_ids"].tolist() == [0] * 1000


@pytest.mark.parametrize("reverse_roads", [False, True])
def test_bounded_fast_path_agrees_with_exhaustive_correspondence(reverse_roads):
    roads = [road(10, [[-100, 5], [100, 5]]), road(20, [[-100, -5], [100, -5]]),
             road(30, [[0, -100], [0, 100]]), road(40, [[20, -100], [20, 100]])]
    if reverse_roads:
        roads.reverse()
    index = RoadSegmentIndex(roads)
    points = np.asarray([[50, 0], [1, 0], [50, 10], [1, 10], [1000, 0], [0, 5], [19, 0]], dtype=float)
    directions = np.asarray([[1, 0], [1, 0], [0, 1], [1, 0], [1, 0], [1, 0], [0, 1]], dtype=float)
    expected_ids, expected_feet = [], []
    for point, direction in zip(points, directions):
        candidates = []
        for segment_id, (start, end) in enumerate(index.endpoints):
            vector = end - start
            fraction = np.clip(np.dot(point - start, vector) / np.dot(vector, vector), 0.0, 1.0)
            foot = start + fraction * vector
            distance = np.linalg.norm(point - foot)
            alignment = abs(np.dot(direction, vector / np.linalg.norm(vector)))
            candidates.append((distance, -alignment, index.identity_ranks[segment_id], segment_id, foot))
        compatible = [item for item in candidates if item[0] <= 25.0 and -item[1] >= 0.7]
        selected = min(compatible or candidates, key=lambda item: item[:3])
        expected_ids.append(selected[3])
        expected_feet.append(selected[4])
    actual = index.inspect(points, directions, max_distance=25.0)
    assert actual["segment_ids"].tolist() == expected_ids
    assert actual["feet"] == pytest.approx(np.asarray(expected_feet))


def test_evidence_uses_the_same_bounded_compatible_correspondence():
    roads = [road(10, [[-100, 5], [100, 5]]), road(11, [[0, -100], [0, 100]])]
    record = {"source_sha256": "a" * 64, "osm_snapshot": {"roads": roads, "sha256": "b" * 64},
              "status": "matched", "base": {"mode": "local", "source_unit_to_m": 1.0,
                                               "reference_latitude": 0.0},
              "revision": 1, "translation_3857": [0, 0], "source_crs": "local"}
    evidence = build_match_evidence(record, [[[0, 0], [10, 0]]], [[[0, 0], [10, 0]]],
                                    samples=np.asarray([[1.0, 0.0]]), directions=np.asarray([[1.0, 0.0]]),
                                    route_ids=np.asarray([0]))
    assert evidence["residuals"][0]["osm_way_id"] == 10
    assert evidence["residuals"][0]["distance_m_approx"] == 5.0
    assert evidence["residuals"][0]["supported"] is True
    assert evidence["correspondence_method"] == "nearest_direction_compatible_segment_within_corridor"


@pytest.mark.parametrize("offset,expected_way,supported", [(40.0, 10, True), (51.0, 11, False)])
def test_evidence_search_radius_converts_ground_corridor_to_projected_units(offset, expected_way, supported):
    roads = [road(10, [[-100, offset], [100, offset]]), road(11, [[0, -100], [0, 100]])]
    record = {"source_sha256": "a" * 64, "osm_snapshot": {"roads": roads, "sha256": "b" * 64},
              "status": "matched", "base": {"mode": "local", "source_unit_to_m": 1.0,
                                               "reference_latitude": 60.0},
              "revision": 1, "translation_3857": [0, 0], "source_crs": "local"}
    evidence = build_match_evidence(record, [[[0, 0], [10, 0]]], [[[0, 0], [10, 0]]],
                                    samples=np.asarray([[1.0, 0.0]]), directions=np.asarray([[1.0, 0.0]]),
                                    route_ids=np.asarray([0]))
    assert evidence["residuals"][0]["osm_way_id"] == expected_way
    assert evidence["residuals"][0]["supported"] is supported
