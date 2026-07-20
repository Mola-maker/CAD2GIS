"""Focused regressions for deterministic post-projection GCP calibration."""

from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path

import pytest

from cad2gis_v3.calibration import (
    GCPProfile,
    GroundControlPoint,
    ResidualTransform,
    RobustSettings,
    TransformLimitsSettings,
    ValidationSettings,
    fit_calibration,
    fit_profile,
)
from cad2gis_v3.georef import DirectTransformer
from cad2gis_v3.pipeline import _calibration_observations


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "apd_gcp_profile.json"
SOURCE_SHA256 = "557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557"


class SyntheticNominalTransformer:
    """A deterministic stand-in that proves projection precedes calibration."""

    source_crs = "EPSG:3857"
    target_crs = "EPSG:9481"

    def point(self, point):
        return float(point[0]) + 500_000.0, float(point[1]) + 100_000.0


TRANSFORMER = SyntheticNominalTransformer()


class NonFiniteTransformer(SyntheticNominalTransformer):
    def point(self, point):
        return math.nan, 100_000.0


def _control(
    point_id,
    cad_point,
    target_point,
    *,
    role="train",
    accuracy=1.0,
    weight=1.0,
):
    return GroundControlPoint(
        point_id=point_id,
        cad_point=tuple(cad_point),
        target_point=tuple(target_point),
        target_crs="EPSG:9481",
        role=role,
        source="synthetic surveyed fixture",
        accuracy_m=accuracy,
        weight=weight,
    )


def _nominal(cad_point):
    return TRANSFORMER.point(cad_point)


def _control_mapping(
    point_id,
    cad_point,
    target_point,
    *,
    role="train",
):
    return {
        "point_id": point_id,
        "cad_x": cad_point[0],
        "cad_y": cad_point[1],
        "target_easting": target_point[0],
        "target_northing": target_point[1],
        "target_crs": "EPSG:9481",
        "role": role,
        "source": "synthetic surveyed fixture",
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
        "enabled": True,
        "max_iterations": 512,
        "outlier_threshold_m": 1.0,
    }
    value["validation"] = {
        "max_check_rmse_m": 0.01,
        "max_check_p95_m": 0.01,
        "max_check_error_m": 0.01,
        "min_check_points": 2,
        "affine_min_improvement_ratio": 0.1,
        "spatial_distribution_reviewed": True,
        "spatial_distribution_review_source": "synthetic review record REV-001",
    }
    value["transform_limits"] = {
        "max_pivot_shift_m": 1_000_000.0,
        "max_abs_rotation_deg": 45.0,
        "max_scale_deviation_ratio": 2.0,
        "max_affine_condition_number": 100.0,
    }
    return value


def _apply_similarity(point, scale, angle_deg, translation):
    angle = math.radians(angle_deg)
    a = scale * math.cos(angle)
    b = scale * math.sin(angle)
    return (
        a * point[0] - b * point[1] + translation[0],
        b * point[0] + a * point[1] + translation[1],
    )


def _apply_affine(point):
    return (
        1.05 * point[0] + 0.12 * point[1] + 30.0,
        -0.08 * point[0] + 0.97 * point[1] - 20.0,
    )


def test_disabled_profile_loads_with_null_gates_and_keeps_identity():
    profile = GCPProfile.load(PROFILE, expected_source_sha256=SOURCE_SHA256)
    assert profile.enabled is False
    assert profile.robust.outlier_threshold_m is None
    assert profile.validation.max_check_rmse_m is None
    assert profile.validation.spatial_distribution_review_source == ""
    assert profile.transform_limits.max_pivot_shift_m is None
    result = fit_profile(profile, DirectTransformer(profile.source_crs, profile.target_crs))
    point = (13_681_914.403, 69_386.445)
    nominal = DirectTransformer(profile.source_crs, profile.target_crs).point(point)
    assert result.selected_model == "disabled"
    assert result.project_native(point, DirectTransformer(profile.source_crs, profile.target_crs)) == pytest.approx(
        nominal, abs=1e-9
    )


def test_calibration_observations_preserve_controls_excluded_by_review(tmp_path):
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    active = _control_mapping(
        "ACTIVE-TRAIN", (1.0, 2.0), (500_004.0, 99_998.0),
    )
    excluded = _control_mapping(
        "EXCLUDED-CHECK", (5.0, 6.0), (500_009.0, 100_003.0), role="check",
    )
    excluded["enabled"] = False
    excluded["source"] = "review record REV-EXCLUDED-001"
    value["controls"] = [active, excluded]
    path = tmp_path / "profile_with_excluded_control.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    profile = GCPProfile.load(path, expected_source_sha256=SOURCE_SHA256)
    result = fit_profile(profile, TRANSFORMER)
    observations = _calibration_observations(profile, result, TRANSFORMER)

    assert [item["point_id"] for item in observations] == [
        "ACTIVE-TRAIN", "EXCLUDED-CHECK",
    ]
    excluded_observation = observations[1]
    assert excluded_observation["status"] == "excluded_by_review"
    assert excluded_observation["enabled"] is False
    assert excluded_observation["role"] == "check"
    assert excluded_observation["source"] == "review record REV-EXCLUDED-001"
    assert (excluded_observation["cad_x"], excluded_observation["cad_y"]) == (5.0, 6.0)
    assert (
        excluded_observation["nominal_easting"],
        excluded_observation["nominal_northing"],
    ) == pytest.approx(_nominal((5.0, 6.0)), abs=1e-12)
    assert (
        excluded_observation["observed_easting"],
        excluded_observation["observed_northing"],
    ) == (500_009.0, 100_003.0)
    for field in (
        "predicted_easting", "predicted_northing", "residual_dx_m",
        "residual_dy_m", "residual_m", "inlier",
    ):
        assert excluded_observation[field] is None


def test_profile_is_bound_to_the_unique_dwg_and_has_strict_keys(tmp_path):
    with pytest.raises(ValueError, match="different DWG"):
        GCPProfile.load(PROFILE, expected_source_sha256="0" * 64)
    value = json.loads(PROFILE.read_text(encoding="utf-8"))
    value["unexpected"] = True
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match=r"unknown=\['unexpected'\]"):
        GCPProfile.load(invalid, expected_source_sha256=SOURCE_SHA256)


def test_profile_hash_is_the_immutable_hash_of_parsed_bytes(tmp_path):
    copied = tmp_path / "gcp_profile.json"
    original_bytes = PROFILE.read_bytes()
    copied.write_bytes(original_bytes)
    profile = GCPProfile.load(copied, expected_source_sha256=SOURCE_SHA256)
    expected = hashlib.sha256(original_bytes).hexdigest()
    copied.write_text("{}", encoding="utf-8")
    assert profile.profile_sha256 == expected
    assert profile.sha256 == expected


def test_direct_settings_construction_rejects_non_finite_thresholds():
    with pytest.raises(ValueError, match="finite"):
        RobustSettings(True, 10, math.nan)
    with pytest.raises(ValueError, match="finite"):
        ValidationSettings(math.inf, 1.0, 1.0, 2, 0.1, True, "review REV-001")
    with pytest.raises(ValueError, match="non-empty review source"):
        ValidationSettings(1.0, 1.0, 1.0, 2, 0.1, True, "")
    with pytest.raises(ValueError, match="finite"):
        TransformLimitsSettings(math.nan, 1.0, 0.1, 10.0)


def test_booleans_and_non_string_identity_fields_are_rejected():
    with pytest.raises(ValueError, match="not boolean"):
        _control("BOOL", (True, 0.0), (500_000.0, 100_000.0))
    mapping = _control_mapping("TEXT", (0.0, 0.0), (500_000.0, 100_000.0))
    mapping["point_id"] = 7
    with pytest.raises(ValueError, match="point_id must be a string"):
        GroundControlPoint.from_mapping(mapping, expected_target_crs="EPSG:9481")
    mapping["point_id"] = "TEXT"
    mapping["source"] = None
    with pytest.raises(ValueError, match="source provenance must be a string"):
        GroundControlPoint.from_mapping(mapping, expected_target_crs="EPSG:9481")


def test_duplicate_controls_non_metric_crs_and_affine_reflection_fail_closed():
    nominal = _nominal((0.0, 0.0))
    duplicate = (
        _control("D0", (0.0, 0.0), nominal),
        _control("D1", (0.0, 0.0), (nominal[0] + 1.0, nominal[1] + 1.0)),
    )
    with pytest.raises(ValueError, match="duplicate cad_point"):
        fit_calibration(duplicate, TRANSFORMER, model="translation")

    class GeographicTargetTransformer(SyntheticNominalTransformer):
        target_crs = "EPSG:4326"

    with pytest.raises(ValueError, match="projected CRS"):
        fit_calibration(
            (_control("G", (0.0, 0.0), nominal),),
            GeographicTargetTransformer(),
            model="translation",
            expected_target_crs="EPSG:4326",
        )

    reflected = tuple(
        _control(
            f"F{index}",
            point,
            (-_nominal(point)[0], _nominal(point)[1]),
        )
        for index, point in enumerate(((0.0, 0.0), (10.0, 0.0), (0.0, 10.0)))
    )
    with pytest.raises(ValueError, match="reflection"):
        fit_calibration(reflected, TRANSFORMER, model="affine")
    with pytest.raises(ValueError, match="positive determinant"):
        ResidualTransform(
            "affine", (0.0, 0.0), (0.0, 0.0), ((-1.0, 0.0), (0.0, 1.0))
        )


def test_non_finite_nominal_projection_is_rejected_before_validation():
    control = _control("NAN", (0.0, 0.0), (500_000.0, 100_000.0))
    with pytest.raises(ValueError, match="nominal easting must be a finite number"):
        fit_calibration((control,), NonFiniteTransformer(), model="translation")


def test_translation_uses_only_training_controls_and_preserves_inputs():
    shift = (3.25, -4.5)
    cad_points = [(0.0, 0.0), (20.0, 0.0), (0.0, 30.0), (9.0, 11.0)]
    controls = tuple(
        _control(
            f"G{index}",
            point,
            (
                _nominal(point)[0] + shift[0],
                _nominal(point)[1] + shift[1],
            ),
            role="check" if index == 3 else "train",
        )
        for index, point in enumerate(cad_points)
    )
    before = tuple((item.cad_point, item.target_point) for item in controls)
    result = fit_calibration(
        controls,
        TRANSFORMER,
        model="translation",
        validation=ValidationSettings(
            0.001, 0.001, 0.001, 1, None, True, "review REV-001"
        ),
    )
    assert result.parameters["pivot_shift_e_m"] == pytest.approx(shift[0], abs=1e-10)
    assert result.parameters["pivot_shift_n_m"] == pytest.approx(shift[1], abs=1e-10)
    assert result.train_metrics.rmse_m == pytest.approx(0.0, abs=1e-10)
    assert result.check_metrics.rmse_m == pytest.approx(0.0, abs=1e-10)
    assert result.validation_passed is True
    assert tuple((item.cad_point, item.target_point) for item in controls) == before


def test_transform_limits_reject_excessive_translation_similarity_and_affine():
    translation_controls = tuple(
        _control(
            f"LT{index}",
            point,
            (_nominal(point)[0] + 3.0, _nominal(point)[1] + 4.0),
        )
        for index, point in enumerate(((0.0, 0.0), (10.0, 0.0), (0.0, 10.0)))
    )
    translation = fit_calibration(
        translation_controls,
        TRANSFORMER,
        model="translation",
        transform_limits=TransformLimitsSettings(4.0, 10.0, 0.1, 10.0),
    )
    assert translation.validation_passed is False
    assert any("pivot_shift_m" in item for item in translation.validation_failures)

    similarity_controls = tuple(
        _control(
            f"LS{index}",
            point,
            _apply_similarity(_nominal(point), 1.02, 2.0, (0.0, 0.0)),
        )
        for index, point in enumerate(
            ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0))
        )
    )
    similarity = fit_calibration(
        similarity_controls,
        TRANSFORMER,
        model="similarity",
        transform_limits=TransformLimitsSettings(1e9, 1.0, 0.01, 10.0),
    )
    assert similarity.validation_passed is False
    assert any("abs_rotation_deg" in item for item in similarity.validation_failures)
    assert any("max_scale_deviation_ratio" in item for item in similarity.validation_failures)

    def conditioned_affine(point):
        return 2.0 * point[0], 0.5 * point[1]

    affine_controls = tuple(
        _control(f"LA{index}", point, conditioned_affine(_nominal(point)))
        for index, point in enumerate(
            ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0))
        )
    )
    affine = fit_calibration(
        affine_controls,
        TRANSFORMER,
        model="affine",
        transform_limits=TransformLimitsSettings(1e9, 10.0, 2.0, 3.0),
    )
    assert affine.validation_passed is False
    assert any("affine_condition_number" in item for item in affine.validation_failures)


def test_check_point_never_influences_weighted_least_squares():
    nominal_a = _nominal((0.0, 0.0))
    nominal_b = _nominal((10.0, 0.0))
    nominal_check = _nominal((5.0, 5.0))
    controls = (
        _control("A", (0.0, 0.0), nominal_a, weight=1.0),
        _control("B", (10.0, 0.0), (nominal_b[0] + 10.0, nominal_b[1]), weight=3.0),
        _control(
            "CHECK",
            (5.0, 5.0),
            (nominal_check[0] + 10_000.0, nominal_check[1] - 10_000.0),
            role="check",
        ),
    )
    result = fit_calibration(controls, TRANSFORMER, model="translation")
    assert result.parameters["pivot_shift_e_m"] == pytest.approx(7.5)
    assert result.parameters["pivot_shift_n_m"] == pytest.approx(0.0)
    check = next(item for item in result.residuals if item.role == "check")
    assert check.effective_weight == 0.0
    assert check.error_m > 10_000.0


def test_accuracy_is_part_of_the_weighted_least_squares_weight():
    first = _nominal((0.0, 0.0))
    second = _nominal((10.0, 0.0))
    controls = (
        _control("PRECISE", (0.0, 0.0), first, accuracy=1.0),
        _control(
            "COARSE",
            (10.0, 0.0),
            (second[0] + 10.0, second[1]),
            accuracy=2.0,
        ),
    )
    result = fit_calibration(controls, TRANSFORMER, model="translation")
    # Relative fitting weights are 1 and 1/4, hence the fitted shift is 2 m.
    assert result.parameters["pivot_shift_e_m"] == pytest.approx(2.0)


def test_similarity_recovers_scale_rotation_and_holdout():
    scale, angle, translation = 1.0004, 0.35, (21.0, -13.0)
    cad_points = [
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 80.0),
        (90.0, 70.0),
        (35.0, 45.0),
    ]
    controls = tuple(
        _control(
            f"S{index}",
            point,
            _apply_similarity(_nominal(point), scale, angle, translation),
            role="check" if index == len(cad_points) - 1 else "train",
        )
        for index, point in enumerate(cad_points)
    )
    result = fit_calibration(controls, TRANSFORMER, model="similarity")
    assert result.parameters["scale"] == pytest.approx(scale, abs=1e-12)
    assert result.parameters["rotation_deg"] == pytest.approx(angle, abs=1e-10)
    assert result.train_metrics.max_m == pytest.approx(0.0, abs=1e-8)
    assert result.check_metrics.max_m == pytest.approx(0.0, abs=1e-8)


def test_affine_recovers_anisotropic_transform_and_holdout():
    cad_points = [
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 100.0),
        (100.0, 100.0),
        (40.0, 70.0),
    ]
    controls = tuple(
        _control(
            f"A{index}",
            point,
            _apply_affine(_nominal(point)),
            role="check" if index == len(cad_points) - 1 else "train",
        )
        for index, point in enumerate(cad_points)
    )
    result = fit_calibration(controls, TRANSFORMER, model="affine")
    assert result.parameters["matrix_ee"] == pytest.approx(1.05, abs=1e-12)
    assert result.parameters["matrix_en"] == pytest.approx(0.12, abs=1e-12)
    assert result.parameters["matrix_ne"] == pytest.approx(-0.08, abs=1e-12)
    assert result.parameters["matrix_nn"] == pytest.approx(0.97, abs=1e-12)
    assert result.check_metrics.max_m == pytest.approx(0.0, abs=1e-8)


def test_fixed_threshold_robust_fit_deterministically_downweights_outlier():
    shift = (2.0, -3.0)
    cad_points = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0), (5.0, 4.0)]
    controls = []
    for index, point in enumerate(cad_points):
        nominal = _nominal(point)
        controls.append(
            _control(
                f"R{index}",
                point,
                (nominal[0] + shift[0], nominal[1] + shift[1]),
            )
        )
    outlier_point = (20.0, 20.0)
    outlier_nominal = _nominal(outlier_point)
    controls.append(
        _control(
            "OUTLIER",
            outlier_point,
            (outlier_nominal[0] + 52.0, outlier_nominal[1] + 47.0),
        )
    )
    check_point = (7.0, 8.0)
    check_nominal = _nominal(check_point)
    controls.append(
        _control(
            "CHECK",
            check_point,
            (check_nominal[0] + shift[0], check_nominal[1] + shift[1]),
            role="check",
        )
    )
    plain = fit_calibration(tuple(controls), TRANSFORMER, model="translation")
    settings = RobustSettings(True, 512, 1.0)
    robust_a = fit_calibration(tuple(controls), TRANSFORMER, model="translation", robust=settings)
    robust_b = fit_calibration(tuple(reversed(controls)), TRANSFORMER, model="translation", robust=settings)
    assert robust_a.check_metrics.rmse_m < 0.25
    assert robust_a.check_metrics.rmse_m < plain.check_metrics.rmse_m
    assert robust_a.parameters["pivot_shift_e_m"] == pytest.approx(
        robust_b.parameters["pivot_shift_e_m"], abs=1e-12
    )
    assert robust_a.parameters["pivot_shift_n_m"] == pytest.approx(
        robust_b.parameters["pivot_shift_n_m"], abs=1e-12
    )
    outlier = next(item for item in robust_a.residuals if item.point_id == "OUTLIER")
    assert outlier.inlier is False
    assert outlier.effective_weight == 0.0


def test_irls_iteration_limit_fails_closed_before_hard_classification():
    first = _nominal((0.0, 0.0))
    second = _nominal((10.0, 0.0))
    controls = (
        _control("I0", (0.0, 0.0), first),
        _control("I1", (10.0, 0.0), (second[0] + 10.0, second[1])),
    )
    with pytest.raises(ValueError, match="IRLS did not converge within 1 iterations"):
        fit_calibration(
            controls,
            TRANSFORMER,
            model="translation",
            robust=RobustSettings(True, 1, 1.0),
        )


def test_hard_inlier_refit_iterates_until_final_residuals_match_classification(tmp_path):
    # The first robust classification accepts residual shifts 0, 1.9 and 1.9.
    # Their hard-WLS mean moves enough to reject 0, so a second classification
    # is required.  A one-shot hard refit leaves a false inlier.
    shifts = (-100.0, 0.0, 1.9, 1.9)
    training = []
    control_mappings = []
    for index, shift in enumerate(shifts):
        cad = (float(index) * 10.0, 0.0)
        nominal = _nominal(cad)
        target = (nominal[0] + shift, nominal[1])
        training.append(_control(f"H{index}", cad, target))
        control_mappings.append(_control_mapping(f"H{index}", cad, target))
    result = fit_calibration(
        tuple(training),
        TRANSFORMER,
        model="translation",
        robust=RobustSettings(True, 512, 1.0),
    )
    train_residuals = [item for item in result.residuals if item.role == "train"]
    assert {item.point_id for item in train_residuals if item.inlier} == {"H2", "H3"}
    assert all(item.inlier == (item.error_m <= 1.0) for item in train_residuals)

    for index, cad in enumerate(((5.0, 10.0), (15.0, 10.0))):
        nominal = _nominal(cad)
        target = (nominal[0] + 1.9, nominal[1])
        control_mappings.append(
            _control_mapping(f"HC{index}", cad, target, role="check")
        )
    profile_value = _enabled_profile_value("translation", control_mappings)
    path = tmp_path / "insufficient_inliers.json"
    path.write_text(json.dumps(profile_value), encoding="utf-8")
    profile = GCPProfile.load(path, expected_source_sha256=SOURCE_SHA256)
    profiled_result = fit_profile(profile, TRANSFORMER)
    assert profiled_result.validation_passed is False
    assert any("accepted_train_count 2 < reviewed minimum 3" in failure for failure in profiled_result.validation_failures)


def test_affine_requires_explicit_spatial_structure_review(tmp_path):
    train_points = (
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 100.0),
        (100.0, 100.0),
        (30.0, 70.0),
        (80.0, 20.0),
    )
    check_points = ((25.0, 40.0), (60.0, 85.0), (75.0, 15.0))
    mappings = []
    for index, point in enumerate(train_points):
        mappings.append(_control_mapping(f"AT{index}", point, _apply_affine(_nominal(point))))
    for index, point in enumerate(check_points):
        mappings.append(
            _control_mapping(
                f"AC{index}", point, _apply_affine(_nominal(point)), role="check"
            )
        )
    profile_value = _enabled_profile_value("affine", mappings)
    profile_value["robust"]["enabled"] = False
    profile_value["robust"]["outlier_threshold_m"] = None
    profile_value["model_selection"]["affine_gate"]["spatial_structure_reviewed"] = False
    path = tmp_path / "affine_unreviewed.json"
    path.write_text(json.dumps(profile_value), encoding="utf-8")
    result = fit_profile(
        GCPProfile.load(path, expected_source_sha256=SOURCE_SHA256), TRANSFORMER
    )
    assert result.check_metrics.max_m == pytest.approx(0.0, abs=1e-8)
    assert result.validation_passed is False
    assert "affine spatial structure has not been explicitly reviewed" in result.validation_failures

    profile_value["model_selection"]["affine_gate"]["spatial_structure_reviewed"] = True
    path.write_text(json.dumps(profile_value), encoding="utf-8")
    reviewed = fit_profile(
        GCPProfile.load(path, expected_source_sha256=SOURCE_SHA256), TRANSFORMER
    )
    assert reviewed.validation_passed is True


@pytest.mark.parametrize(
    ("model", "controls", "message"),
    [
        ("similarity", ((_control("P", (0.0, 0.0), _nominal((0.0, 0.0)))),), "at least 2"),
        (
            "affine",
            (
                _control("P0", (0.0, 0.0), _nominal((0.0, 0.0))),
                _control("P1", (1.0, 0.0), _nominal((1.0, 0.0))),
                _control("P2", (2.0, 0.0), _nominal((2.0, 0.0))),
            ),
            "non-collinear",
        ),
    ],
)
def test_degenerate_control_geometry_is_rejected(model, controls, message):
    with pytest.raises(ValueError, match=message):
        fit_calibration(controls, TRANSFORMER, model=model)
