"""Known-position adversarial registration cases; no survey accuracy claim."""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cad2gis.cad2gis_v3 import osm_matching as matching  # noqa: E402


ROUTES = [[(0, 0), (100, 0), (100, 90), (260, 90), (300, 130)],
          [(100, 0), (100, -70), (30, -130)]]
SHIFT = (1000.0, 2000.0)


def fragmented_roads(shifts=(SHIFT,)):
    roads = []
    for dx, dy in shifts:
        for route in ROUTES:
            for left, right in zip(route, route[1:]):
                count = math.ceil(math.dist(left, right) / 10.0)
                points = [(left[0] + (right[0] - left[0]) * i / count + dx,
                           left[1] + (right[1] - left[1]) * i / count + dy)
                          for i in range(count + 1)]
                for a, b in zip(points, points[1:]):
                    roads.append({"osm_way_id": len(roads) + 1, "points": [a, b],
                                  "highway": "residential"})
    return roads


def test_fragmented_reference_recovers_translation_far_from_initial_hint():
    roads = fragmented_roads()
    frozen = copy.deepcopy(roads)
    delta, quality = matching._match_routes(ROUTES, roads, 1.0, [4000.0, 6000.0])
    assert delta == pytest.approx(SHIFT, abs=0.01)
    assert quality["candidates"][0]["coverage"] == 1.0
    assert quality["landmark_search"]["selected_translation_bins"] > 0
    assert quality["landmark_search"]["topology_connections_inferred"] is False
    assert quality["search_complete"] is False
    assert roads == frozen


def test_fragmented_repeated_networks_abstain_even_when_initial_hint_is_correct():
    roads = fragmented_roads((SHIFT, (SHIFT[0] + 800, SHIFT[1] + 900)))
    delta, quality = matching._match_routes(ROUTES, roads, 1.0, SHIFT)
    assert delta is None
    assert quality["confidence_gap"] < 0.06
    placements = [c["translation_3857"] for c in quality["candidates"]]
    assert any(math.dist(p, SHIFT) < 0.01 for p in placements)
    assert any(math.dist(p, (1800, 2900)) < 0.01 for p in placements)


def test_fragmented_match_is_independent_of_way_order_and_vertex_direction():
    roads = fragmented_roads()
    expected, _ = matching._match_routes(ROUTES, roads, 1.0, [4000.0, 6000.0])
    reversed_roads = [{**road, "points": list(reversed(road["points"]))} for road in reversed(roads)]
    reversed_routes = [list(reversed(route)) for route in reversed(ROUTES)]
    actual, _ = matching._match_routes(reversed_routes, reversed_roads, 1.0, [4000.0, 6000.0])
    assert expected is not None
    assert actual == pytest.approx(expected, abs=0.01)


def test_parallel_fragment_endpoints_do_not_create_geometric_landmarks():
    assert matching._geometric_landmarks([[(0, 0), (10, 0)], [(10, 0), (20, 0)]]) == []


def test_near_parallel_landmark_axes_do_not_depend_on_input_order():
    routes = [[(0, 0), (100 * math.cos(math.radians(angle)), 100 * math.sin(math.radians(angle)))]
              for angle in (0, 0.6, 1.2, 24)]
    first = matching._geometric_landmarks(routes)
    second = matching._geometric_landmarks([list(reversed(routes[i])) for i in (1, 0, 2, 3)])
    assert len(first) == 1
    assert [item[0] for item in first] == [item[0] for item in second]
    for left, right in zip(first, second):
        assert left[1].tolist() == right[1].tolist()
        assert left[2] == pytest.approx(right[2])


@pytest.mark.parametrize("offset", [0.5, 1.0, 2.0, 4.0, 8.0])
def test_single_landmark_nearby_exact_placements_are_not_averaged(offset):
    route = [(0, 0), (100, 0), (100, 100)]
    roads = []
    for dx, dy in (SHIFT, (SHIFT[0] + offset, SHIFT[1] + offset)):
        for a, b in zip(route, route[1:]):
            points = [(a[0] + (b[0] - a[0]) * i / 10 + dx,
                       a[1] + (b[1] - a[1]) * i / 10 + dy) for i in range(11)]
            for left, right in zip(points, points[1:]):
                roads.append({"osm_way_id": len(roads) + 1, "points": [left, right]})
    delta, quality = matching._match_routes([route], roads, 1.0, [4000, 6000])
    assert delta is None
    assert quality["confidence_gap"] == pytest.approx(0.0, abs=1e-8)


def test_exhausted_landmark_budget_cannot_manufacture_confidence():
    route = [(0, 0), (100, 0), (100, 100)]
    roads = []
    for dx, dy in (SHIFT, (SHIFT[0] + 2, SHIFT[1] + 2)):
        for a, b in zip(route, route[1:]):
            points = [(a[0] + (b[0] - a[0]) * i / 10 + dx,
                       a[1] + (b[1] - a[1]) * i / 10 + dy) for i in range(11)]
            for left, right in zip(points, points[1:]):
                roads.append({"osm_way_id": len(roads) + 1, "points": [left, right]})
    for ix in range(16):
        for iy in range(16):
            x, y = 1100 - (ix + 1) * 0.06, 2000 - (iy + 1) * 0.06
            roads.append({"osm_way_id": len(roads) + 1,
                          "points": [(x - 0.01, y), (x, y), (x, y + 0.01)]})
    delta, quality = matching._match_routes([route], roads, 1.0, [4000, 6000])
    assert quality["landmark_search"]["seed_budget_exhausted"] is True
    assert delta is None
    assert quality["reason"] == "landmark_hypothesis_budget_exhausted"


def test_explicit_roads_are_not_displaced_by_parallel_offset_cables():
    """Mixing two offset source domains formerly accepted a 4.985 m wrong shift."""
    features = [SimpleNamespace(feature_class='ROAD', native_points=route) for route in ROUTES]
    features += [SimpleNamespace(feature_class='CABLE', native_points=[(x + 8, y + 6) for x, y in route])
                 for route in ROUTES]
    roads = [{'osm_way_id': index + 1, 'points': [(x + SHIFT[0], y + SHIFT[1]) for x, y in route]}
             for index, route in enumerate(ROUTES)]
    delta, quality = matching._match_routes(matching._native_routes(features), roads, 1.0, SHIFT)
    assert delta == pytest.approx(SHIFT, abs=0.01)
    assert quality['candidates'][0]['coverage'] == 1.0


def test_integer_coordinate_hypotheses_accept_fractional_refinement():
    roads = [{'osm_way_id': index + 1, 'points': [(x + 1000, y + 2000) for x, y in route]}
             for index, route in enumerate(ROUTES)]
    delta, _ = matching._match_routes(ROUTES, roads, 1.0, [1000, 2000])
    assert delta == pytest.approx(SHIFT, abs=0.01)
