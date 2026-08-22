from __future__ import annotations

from cad2gis.cad2gis_v3.config import SpatialCoveragePolicy
from cad2gis.cad2gis_v3.spatial_coverage import (
    evaluate_corridor_coverage,
    evaluate_spatial_coverage,
)


def policy(**overrides):
    values = {
        "min_training_extent_x_ratio": 0.2,
        "min_training_extent_y_ratio": 0.2,
        "min_training_hull_area_ratio": 0.04,
        "max_drawing_vertices_outside_training_bbox_ratio": 0.55,
        "min_check_baseline_to_drawing_diagonal_ratio": 0.05,
        "min_check_hull_area_ratio": 0.01,
        "max_drawing_vertices_outside_training_hull_ratio": 0.70,
        "corridor_min_cable_length_to_diagonal_ratio": 1.0,
        "corridor_min_cable_arc_coverage_ratio": 0.70,
        "corridor_max_device_to_training_along_cable_m": 150.0,
    }
    values.update(overrides)
    return SpatialCoveragePolicy.from_mapping(values)


def test_corridor_coverage_accepts_route_controls() -> None:
    line = [(0.0, 0.0), (100.0, 0.0), (200.0, 50.0), (400.0, 50.0)]
    devices = [(0.0, 0.0), (100.0, 0.0), (200.0, 50.0), (400.0, 50.0)]
    training = [(0.0, 0.0), (200.0, 50.0), (400.0, 50.0)]
    result = evaluate_corridor_coverage([line], devices, training, policy())
    assert result["corridor_detected"] is True
    assert result["passed"] is True


def test_corridor_coverage_rejects_long_extrapolation() -> None:
    line = [(0.0, 0.0), (100.0, 0.0), (200.0, 50.0), (400.0, 50.0)]
    devices = [(0.0, 0.0), (400.0, 50.0)]
    training = [(200.0, 50.0)]
    result = evaluate_corridor_coverage([line], devices, training, policy())
    assert result["corridor_detected"] is True
    assert result["passed"] is False
    assert any("corridor_max_device_to_training_along_cable_m" in f for f in result["failures"])


def test_bbox_containment_tolerates_small_manual_pick_offset() -> None:
    drawing = [(0.0, 0.0), (100.0, 100.0)]
    training = [(-0.08, 0.0), (100.0, 100.0), (50.0, 0.0), (0.0, 100.0)]
    checks = [(0.0, 0.0), (100.0, 100.0), (50.0, 50.0)]
    result = evaluate_spatial_coverage(drawing, training, checks, policy())
    assert result["training_controls_outside_drawing_bbox"] == 0
