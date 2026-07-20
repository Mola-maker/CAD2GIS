"""Precision and independent-validation gates for experiment calibration v3."""

from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

import cad2gis_v3.calibration as calibration
from cad2gis_v3.calibration import (
    GCPProfile,
    GroundControlPoint,
    ModelSelectionSettings,
    ResidualMetrics,
    RobustSettings,
    TransformLimitsSettings,
    ValidationSettings,
    fit_calibration,
    fit_profile,
)
from cad2gis_v3.config import SpatialCoveragePolicy
from cad2gis_v3.pipeline import _calibration_candidate_coverage_failures


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_gcp_profile.json"
SOURCE_SHA256 = "557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557"


class SyntheticNominalTransformer:
    source_crs = "EPSG:3857"
    target_crs = "EPSG:9481"

    def point(self, point):
        return float(point[0]) + 500_000.0, float(point[1]) + 100_000.0


TRANSFORMER = SyntheticNominalTransformer()


def _control(point_id, cad_point, target_point, *, role="train"):
    return GroundControlPoint(
        point_id=point_id,
        cad_point=tuple(cad_point),
        target_point=tuple(target_point),
        target_crs="EPSG:9481",
        role=role,
        source="precision gate fixture",
        accuracy_m=1.0,
        weight=1.0,
    )


def _mapping(point_id, cad_point, target_point, *, role="train"):
    return {
        "point_id": point_id,
        "cad_x": cad_point[0],
        "cad_y": cad_point[1],
        "target_easting": target_point[0],
        "target_northing": target_point[1],
        "target_crs": "EPSG:9481",
        "role": role,
        "source": "precision gate fixture",
        "accuracy_m": 1.0,
        "weight": 1.0,
        "enabled": True,
    }


def _enabled_profile_value(requested_model, controls):
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    value["enabled"] = True
    value["requested_model"] = requested_model
    value["controls"] = controls
    value["robust"] = {
        "enabled": False,
        "max_iterations": 20,
        "outlier_threshold_m": None,
    }
    value["validation"] = {
        "max_check_rmse_m": 0.01,
        "max_check_p95_m": 0.01,
        "max_check_error_m": 0.01,
        "min_check_points": 2,
        "affine_min_improvement_ratio": 0.2,
        "spatial_distribution_reviewed": True,
        "spatial_distribution_review_source": "precision review REV-002",
    }
    value["transform_limits"] = {
        "max_pivot_shift_m": 1_000_000.0,
        "max_abs_rotation_deg": 45.0,
        "max_scale_deviation_ratio": 2.0,
        "max_affine_condition_number": 100.0,
    }
    value["model_selection"]["affine_gate"]["spatial_structure_reviewed"] = True
    return value


def test_normalized_geometry_rejects_near_coincident_and_ill_conditioned_points():
    base = 1_000_000_000.0
    near_points = ((base, base), (base + 1e-6, base + 1e-6))
    near_controls = tuple(
        _control(f"N{index}", point, TRANSFORMER.point(point))
        for index, point in enumerate(near_points)
    )
    with pytest.raises(ValueError, match="near-coincident"):
        fit_calibration(near_controls, TRANSFORMER, model="similarity")

    skinny_points = (
        (base, base),
        (base + 1_000.0, base),
        (base, base + 1e-4),
        (base + 1_000.0, base + 1e-4),
    )
    skinny_controls = tuple(
        _control(f"I{index}", point, TRANSFORMER.point(point))
        for index, point in enumerate(skinny_points)
    )
    with pytest.raises(ValueError, match="ill-conditioned.*scale normalization"):
        fit_calibration(skinny_controls, TRANSFORMER, model="affine")


def test_normalized_geometry_keeps_well_spread_large_coordinate_fixture():
    base = 1_000_000_000.0
    points = (
        (base, base),
        (base + 1_000.0, base),
        (base, base + 800.0),
        (base + 1_000.0, base + 800.0),
    )

    def target(point):
        easting, northing = TRANSFORMER.point(point)
        return (
            1.02 * easting + 0.03 * northing + 25.0,
            -0.04 * easting + 0.98 * northing - 10.0,
        )

    controls = tuple(
        _control(f"L{index}", point, target(point))
        for index, point in enumerate(points)
    )
    result = fit_calibration(controls, TRANSFORMER, model="affine")

    assert result.parameters["matrix_ee"] == pytest.approx(1.02, abs=1e-9)
    assert result.parameters["matrix_en"] == pytest.approx(0.03, abs=1e-9)
    assert result.parameters["matrix_ne"] == pytest.approx(-0.04, abs=1e-9)
    assert result.parameters["matrix_nn"] == pytest.approx(0.98, abs=1e-9)


def test_affine_upgrade_requires_same_check_ids_and_all_three_improvements():
    points = (
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 100.0),
        (25.0, 30.0),
        (70.0, 80.0),
        (80.0, 20.0),
    )
    controls = tuple(
        _control(
            f"P{index}",
            point,
            TRANSFORMER.point(point),
            role="check" if index >= 3 else "train",
        )
        for index, point in enumerate(points)
    )
    similarity = fit_calibration(controls, TRANSFORMER, model="similarity")
    affine = fit_calibration(controls, TRANSFORMER, model="affine")
    similarity = replace(
        similarity,
        check_metrics=ResidualMetrics(3, 10.0, 10.0, 10.0),
    )
    affine_rmse_only = replace(
        affine,
        check_metrics=ResidualMetrics(3, 7.0, 9.0, 9.0),
    )
    profile = SimpleNamespace(
        validation=ValidationSettings(
            100.0,
            100.0,
            100.0,
            2,
            0.2,
            True,
            "precision review REV-003",
        ),
        model_selection=SimpleNamespace(
            require_spatially_structured_similarity_residuals=True,
            spatial_structure_reviewed=True,
            require_holdout_improvement=True,
        ),
    )

    failures = calibration._affine_gate_failures(
        affine_rmse_only, similarity, profile
    )
    assert not any("check RMSE improvement" in failure for failure in failures)
    assert any("check p95 improvement" in failure for failure in failures)
    assert any("check max improvement" in failure for failure in failures)

    affine_all_metrics = replace(
        affine,
        check_metrics=ResidualMetrics(3, 7.0, 7.0, 7.0),
    )
    assert calibration._affine_gate_failures(
        affine_all_metrics, similarity, profile
    ) == ()

    mismatched_residuals = tuple(
        replace(residual, point_id="DIFFERENT-CHECK-ID")
        if residual.point_id == "P3"
        else residual
        for residual in similarity.residuals
    )
    mismatched_similarity = replace(
        similarity,
        residuals=mismatched_residuals,
    )
    mismatch_failures = calibration._affine_gate_failures(
        affine_all_metrics, mismatched_similarity, profile
    )
    assert any("same check point-id set" in failure for failure in mismatch_failures)


def test_affine_safeguards_cannot_be_disabled_or_set_to_zero_improvement():
    selection = json.loads(PROFILE.read_text(encoding="utf-8"))["model_selection"]
    for flag in (
        "require_spatially_structured_similarity_residuals",
        "require_holdout_improvement",
    ):
        unsafe = json.loads(json.dumps(selection))
        unsafe["affine_gate"][flag] = False
        with pytest.raises(ValueError, match="Affine promotion must require"):
            ModelSelectionSettings.from_mapping(unsafe)

    with pytest.raises(ValueError, match=r"must be in \(0, 1\)"):
        ValidationSettings(
            1.0, 1.0, 1.0, 3, 0.0, True, "precision review REV-004"
        )


def test_auto_raises_when_all_candidates_fail_but_explicit_model_is_auditable(tmp_path):
    train_points = (
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 100.0),
        (100.0, 100.0),
        (30.0, 70.0),
        (80.0, 20.0),
    )
    check_points = ((25.0, 40.0), (60.0, 85.0))
    controls = [
        _mapping(f"T{index}", point, TRANSFORMER.point(point))
        for index, point in enumerate(train_points)
    ]
    controls.extend(
        _mapping(
            f"C{index}",
            point,
            (
                TRANSFORMER.point(point)[0] + 100.0,
                TRANSFORMER.point(point)[1],
            ),
            role="check",
        )
        for index, point in enumerate(check_points)
    )
    value = _enabled_profile_value("auto", controls)
    path = tmp_path / "auto_all_failed.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    profile = GCPProfile.load(path, expected_source_sha256=SOURCE_SHA256)
    with pytest.raises(
        ValueError,
        match="^No calibration model passed independent validation$",
    ):
        fit_profile(profile, TRANSFORMER)

    value["requested_model"] = "translation"
    path.write_text(json.dumps(value), encoding="utf-8")
    explicit = fit_profile(
        GCPProfile.load(path, expected_source_sha256=SOURCE_SHA256),
        TRANSFORMER,
    )
    assert explicit.selected_model == "translation"
    assert explicit.validation_passed is False
    assert any("check_rmse_m" in failure for failure in explicit.validation_failures)


def test_auto_keeps_the_simplest_independently_passing_model(tmp_path):
    train_points = (
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 100.0),
        (100.0, 100.0),
        (30.0, 70.0),
        (80.0, 20.0),
    )
    check_points = ((25.0, 40.0), (60.0, 85.0))
    controls = [
        _mapping(f"ST{index}", point, TRANSFORMER.point(point))
        for index, point in enumerate(train_points)
    ]
    controls.extend(
        _mapping(f"SC{index}", point, TRANSFORMER.point(point), role="check")
        for index, point in enumerate(check_points)
    )
    value = _enabled_profile_value("auto", controls)
    path = tmp_path / "auto_translation_passes.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    result = fit_profile(
        GCPProfile.load(path, expected_source_sha256=SOURCE_SHA256),
        TRANSFORMER,
    )
    assert result.selected_model == "translation"
    assert result.validation_passed is True


def test_auto_continues_after_translation_fails_post_inlier_coverage():
    drawing = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
    outer_training = drawing
    central_training = (
        (45.0, 45.0),
        (55.0, 45.0),
        (45.0, 55.0),
        (55.0, 55.0),
    )
    checks = ((1.0, 1.0), (99.0, 1.0), (1.0, 99.0))
    pivot = TRANSFORMER.point((50.0, 50.0))
    angle = math.radians(0.8)
    cosine, sine = math.cos(angle), math.sin(angle)

    def rotated_target(point):
        easting, northing = TRANSFORMER.point(point)
        delta_e = easting - pivot[0]
        delta_n = northing - pivot[1]
        return (
            pivot[0] + cosine * delta_e - sine * delta_n,
            pivot[1] + sine * delta_e + cosine * delta_n,
        )

    controls = tuple(
        _control(f"PT{index}", point, rotated_target(point))
        for index, point in enumerate(outer_training + central_training)
    ) + tuple(
        _control(f"PC{index}", point, rotated_target(point), role="check")
        for index, point in enumerate(checks)
    )
    base_profile = GCPProfile.load(
        PROFILE, expected_source_sha256=SOURCE_SHA256
    )
    profile = replace(
        base_profile,
        enabled=True,
        requested_model="auto",
        controls=controls,
        model_selection=replace(
            base_profile.model_selection,
            spatial_structure_reviewed=True,
        ),
        robust=RobustSettings(True, 512, 0.3),
        validation=ValidationSettings(
            1.5,
            1.5,
            1.5,
            3,
            0.1,
            True,
            "post-inlier coverage review REV-005",
        ),
        transform_limits=TransformLimitsSettings(1_000.0, 5.0, 0.1, 10.0),
    )
    policy = SpatialCoveragePolicy(
        min_training_extent_x_ratio=0.6,
        min_training_extent_y_ratio=0.6,
        min_training_hull_area_ratio=0.2,
        max_drawing_vertices_outside_training_bbox_ratio=0.05,
        min_check_baseline_to_drawing_diagonal_ratio=0.25,
        min_check_hull_area_ratio=0.05,
        max_drawing_vertices_outside_training_hull_ratio=0.05,
    )

    # Residual gates alone accept translation: only its four central training
    # controls survive robust fitting, while the independent check errors stay
    # below the reviewed 1.5 m threshold.
    residual_only = fit_profile(profile, TRANSFORMER)
    assert residual_only.selected_model == "translation"
    assert residual_only.validation_passed is True
    assert {
        residual.point_id
        for residual in residual_only.residuals
        if residual.role == "train" and residual.inlier is True
    } == {"PT4", "PT5", "PT6", "PT7"}

    def coverage_failures(candidate):
        return _calibration_candidate_coverage_failures(
            profile,
            TRANSFORMER,
            drawing,
            policy,
            candidate,
        )

    selected = fit_profile(
        profile,
        TRANSFORMER,
        candidate_validation=coverage_failures,
    )
    assert selected.selected_model == "similarity"
    summaries = {summary.model: summary for summary in selected.candidates}
    assert summaries["translation"].validation_passed is False
    assert any(
        "accepted-inlier spatial coverage" in failure
        for failure in summaries["translation"].failures
    )
    assert summaries["similarity"].validation_passed is True

    explicit = fit_profile(
        replace(profile, requested_model="translation"),
        TRANSFORMER,
        candidate_validation=coverage_failures,
    )
    assert explicit.validation_passed is False
    assert any(
        "accepted-inlier spatial coverage" in failure
        for failure in explicit.validation_failures
    )

    def broken_coverage_gate(_candidate):
        raise ValueError("candidate coverage evaluation failed")

    with pytest.raises(ValueError, match="candidate coverage evaluation failed"):
        fit_profile(
            profile,
            TRANSFORMER,
            candidate_validation=broken_coverage_gate,
        )
