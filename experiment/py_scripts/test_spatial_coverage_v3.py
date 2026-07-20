"""Drawing-relative GCP distribution is a hard numeric contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cad2gis_v3.config import SourceProfile, SpatialCoveragePolicy
from cad2gis_v3.model import SourceEntity
from cad2gis_v3.pipeline import _calibration_spatial_coverage
from cad2gis_v3.spatial_coverage import (
    evaluate_spatial_coverage,
    source_entity_drawing_points,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_source_profile.json"


def _policy() -> SpatialCoveragePolicy:
    return SpatialCoveragePolicy(
        min_training_extent_x_ratio=0.6,
        min_training_extent_y_ratio=0.6,
        min_training_hull_area_ratio=0.2,
        max_drawing_vertices_outside_training_bbox_ratio=0.05,
        max_drawing_vertices_outside_training_hull_ratio=0.05,
        min_check_baseline_to_drawing_diagonal_ratio=0.25,
        min_check_hull_area_ratio=0.05,
    )


def _source_entity(
    entity_key: str,
    points,
    *,
    layout: str = "Model",
    layout_role: str = "model",
    cad_role: str = "model",
    dwg_type: str = "LWPOLYLINE",
) -> SourceEntity:
    return SourceEntity.from_record({
        "entity_key": entity_key,
        "handle": entity_key,
        "layout": layout,
        "layout_role": layout_role,
        "cad_role": cad_role,
        "dwg_type_name": dwg_type,
        "layer": "UNCLASSIFIED",
        "points": points,
    })


def test_well_distributed_training_and_holdout_controls_pass():
    drawing = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    result = evaluate_spatial_coverage(
        drawing,
        drawing,
        ((0.0, 0.0), (100.0, 100.0), (100.0, 0.0)),
        _policy(),
    )

    assert result["passed"] is True
    assert result["failures"] == []
    assert result["training_hull_to_drawing_bbox_area_ratio"] == 1.0
    assert result["drawing_vertices_outside_training_bbox_ratio"] == 0.0
    assert result["check_baseline_to_drawing_diagonal_ratio"] == 1.0
    assert result["check_hull_to_drawing_bbox_area_ratio"] == 0.5
    assert result["drawing_vertices_outside_training_hull_ratio"] == 0.0
    assert result["training_controls_outside_drawing_bbox"] == 0
    assert result["check_controls_outside_drawing_bbox"] == 0


def test_unclassified_model_edge_expands_extent_and_exposes_coverage_failure():
    classified_feature_points = (
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 100.0),
        (0.0, 100.0),
    )
    unclassified_edge = (200.0, 50.0)
    entities = (
        _source_entity("classified", classified_feature_points),
        _source_entity(
            "unclassified-edge",
            (unclassified_edge,),
            dwg_type="LINE",
        ),
    )
    training = classified_feature_points
    checks = ((0.0, 0.0), (100.0, 100.0), (100.0, 0.0))

    feature_only = evaluate_spatial_coverage(
        classified_feature_points, training, checks, _policy(),
    )
    complete_source = evaluate_spatial_coverage(
        source_entity_drawing_points(entities), training, checks, _policy(),
    )

    assert feature_only["passed"] is True
    assert complete_source["drawing_bbox"]["max_easting"] == 200.0
    assert complete_source["passed"] is False
    assert complete_source["training_extent_coverage_x_ratio"] == 0.5
    assert complete_source["drawing_vertices_outside_training_hull_ratio"] == 0.2
    assert any(
        "training_extent_coverage_x_ratio" in failure
        for failure in complete_source["failures"]
    )


def test_source_extent_uses_only_valid_model_space_geometry_deterministically():
    model = _source_entity("model", ((2.0, 2.0), (1.0, 1.0)))
    non_geometric = _source_entity("empty-model", ())
    paper = _source_entity(
        "paper", ((1000.0, 1000.0),),
        layout="Layout 1", layout_role="layout", cad_role="layout",
    )
    definition = _source_entity(
        "definition", ((-1000.0, -1000.0),),
        layout="BLOCKDEF:TEST", layout_role="block_definition",
        cad_role="block_definition",
    )

    forward = source_entity_drawing_points(
        (model, non_geometric, paper, definition),
    )
    reverse = source_entity_drawing_points(
        (definition, paper, non_geometric, model),
    )

    assert forward == reverse == ((1.0, 1.0), (2.0, 2.0))


def test_source_extent_excludes_style_legend_geometry_from_model_space():
    plan = _source_entity("plan", ((100.0, 100.0), (200.0, 200.0)))
    legend = _source_entity(
        "legend",
        ((2800.0, 100.0), (2900.0, 200.0)),
        cad_role="style_legend",
    )

    assert source_entity_drawing_points((plan, legend)) == (
        (100.0, 100.0),
        (200.0, 200.0),
    )


def test_source_extent_excludes_hatch_sentinel_by_entity_type_not_coordinate():
    valid_origin_line = _source_entity(
        "origin-line",
        ((0.0, 0.0), (10.0, 10.0)),
        dwg_type="LINE",
    )
    non_materialized_hatch = _source_entity(
        "hatch",
        ((0.0, 0.0),),
        dwg_type="HATCH",
    )

    assert source_entity_drawing_points(
        (valid_origin_line, non_materialized_hatch)
    ) == ((0.0, 0.0), (10.0, 10.0))


def test_invalid_model_space_geometry_fails_closed():
    invalid = _source_entity("invalid", ((float("nan"), 1.0),))

    try:
        source_entity_drawing_points((invalid,))
    except ValueError as exc:
        assert "non-finite coordinate" in str(exc)
    else:
        raise AssertionError("invalid model-space geometry was accepted")


def test_disjoint_check_hull_fails_source_control_containment():
    drawing = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    checks = ((1000.0, 1000.0), (1100.0, 1000.0), (1000.0, 1100.0))

    result = evaluate_spatial_coverage(
        drawing, drawing, checks, _policy(),
    )

    assert result["check_baseline_to_drawing_diagonal_ratio"] == 1.0
    assert result["check_hull_to_drawing_bbox_area_ratio"] == 0.5
    assert result["check_controls_outside_drawing_bbox"] == 3
    assert result["passed"] is False
    assert any(
        "check_controls_outside_drawing_bbox 3 > allowed 0" in failure
        for failure in result["failures"]
    )


def test_training_hull_must_cover_drawing_not_only_its_bounding_box():
    drawing = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    triangular_training = (
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 100.0),
        (25.0, 25.0),
    )
    checks = ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0))

    result = evaluate_spatial_coverage(
        drawing, triangular_training, checks, _policy(),
    )

    assert result["training_extent_coverage_x_ratio"] == 1.0
    assert result["training_extent_coverage_y_ratio"] == 1.0
    assert result["training_hull_to_drawing_bbox_area_ratio"] == 0.5
    assert result["drawing_vertices_outside_training_bbox_ratio"] == 0.0
    assert result["drawing_vertices_outside_training_hull_ratio"] == 0.25
    assert result["passed"] is False
    assert any(
        "drawing_vertices_outside_training_hull_ratio 0.250000 > allowed 0.050000"
        in failure
        for failure in result["failures"]
    )


def test_corner_cluster_fails_even_when_a_human_review_flag_could_be_true():
    drawing = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    result = evaluate_spatial_coverage(
        drawing,
        ((0.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0)),
        ((0.0, 0.0), (10.0, 10.0), (10.0, 0.0)),
        _policy(),
    )

    assert result["passed"] is False
    assert any("training_extent_coverage_x_ratio" in item for item in result["failures"])
    assert any("training_hull_to_drawing_bbox_area_ratio" in item for item in result["failures"])
    assert any("check_baseline_to_drawing_diagonal_ratio" in item for item in result["failures"])


def test_collinear_holdout_controls_fail_two_dimensional_coverage():
    drawing = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    result = evaluate_spatial_coverage(
        drawing,
        drawing,
        ((0.0, 0.0), (50.0, 50.0), (100.0, 100.0)),
        _policy(),
    )

    assert result["passed"] is False
    assert result["check_hull_to_drawing_bbox_area_ratio"] == 0.0
    assert any(
        "check_hull_to_drawing_bbox_area_ratio" in item
        for item in result["failures"]
    )


def test_finite_extreme_inputs_fail_when_derived_metrics_overflow():
    extreme = 1e308
    result = evaluate_spatial_coverage(
        ((-extreme, -extreme), (extreme, -extreme), (extreme, extreme)),
        ((-extreme, -extreme), (extreme, -extreme), (0.0, extreme)),
        ((-extreme, 0.0), (extreme, 0.0), (0.0, extreme)),
        _policy(),
    )

    assert result["passed"] is False
    assert result["drawing_bbox_area_m2"] is None
    assert result["drawing_diagonal_m"] is None
    assert result["failures"]


def test_robust_inliers_must_retain_the_reviewed_training_coverage():
    drawing = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    outer = drawing
    central = ((40.0, 40.0), (60.0, 40.0), (40.0, 60.0), (60.0, 60.0))
    checks = ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0))
    controls = []
    for index, point in enumerate(outer + central):
        controls.append(SimpleNamespace(
            point_id=f"T{index}", cad_point=point, role="train",
        ))
    for index, point in enumerate(checks):
        controls.append(SimpleNamespace(
            point_id=f"C{index}", cad_point=point, role="check",
        ))
    profile = SimpleNamespace(
        active_controls=tuple(controls),
        validation=SimpleNamespace(
            spatial_distribution_reviewed=True,
            spatial_distribution_review_source="review REV-POST-INLIER",
        ),
    )
    transformer = SimpleNamespace(point=lambda point: point)

    prefit = _calibration_spatial_coverage(
        profile, transformer, drawing, _policy(),
    )
    retained = _calibration_spatial_coverage(
        profile,
        transformer,
        drawing,
        _policy(),
        training_point_ids={f"T{index}" for index in range(4, 8)},
    )

    assert prefit["passed"] is True
    assert prefit["training_scope"] == "active_reviewed_controls"
    assert retained["passed"] is False
    assert retained["training_scope"] == "accepted_robust_inliers"
    assert retained["training_extent_coverage_x_ratio"] == 0.2
    assert retained["training_hull_to_drawing_bbox_area_ratio"] == 0.04


def test_source_profile_binds_numeric_coverage_policy():
    profile = SourceProfile.load(PROFILE)
    assert profile.schema_version == "cad2gis-source-profile-v4"
    assert profile.spatial_coverage_policy == _policy()
    assert profile.spatial_coverage_policy.to_dict()["min_training_extent_x_ratio"] == 0.6


def test_v2_source_profile_remains_readable_but_cannot_enable_affine_coverage(
    tmp_path,
):
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    value["schema_version"] = "cad2gis-source-profile-v2"
    value["spatial_coverage_policy"].pop("min_check_hull_area_ratio")
    value["spatial_coverage_policy"].pop(
        "max_drawing_vertices_outside_training_hull_ratio"
    )
    path = tmp_path / "source-profile-v2.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    profile = SourceProfile.load(path)
    assert profile.spatial_coverage_policy.min_check_hull_area_ratio is None
    result = evaluate_spatial_coverage(
        ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
        ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0)),
        ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0)),
        profile.spatial_coverage_policy,
    )
    assert result["passed"] is False
    assert any("source profile v3 is required" in item for item in result["failures"])


def test_v3_source_profile_is_readable_but_new_hull_gate_fails_closed(tmp_path):
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    value["schema_version"] = "cad2gis-source-profile-v3"
    value["spatial_coverage_policy"].pop(
        "max_drawing_vertices_outside_training_hull_ratio"
    )
    path = tmp_path / "source-profile-v3.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    profile = SourceProfile.load(path)
    result = evaluate_spatial_coverage(
        ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
        ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0)),
        ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0)),
        profile.spatial_coverage_policy,
    )

    assert result["passed"] is False
    assert any("source profile v4 is required" in item for item in result["failures"])
