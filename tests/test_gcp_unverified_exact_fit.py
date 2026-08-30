"""Tests for the reviewed allow_unverified_exact_fit GCP allowance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3 import pipeline
from cad2gis.cad2gis_v3.calibration import (
    GCPProfile,
    ValidationSettings,
    fit_profile,
)
from cad2gis.cad2gis_v3.config import SpatialCoveragePolicy
from cad2gis.cad2gis_v3.georef import DirectTransformer
from cad2gis.cad2gis_v3.run_status import RunStatus
from cad2gis.cad2gis_v3.spatial_coverage import evaluate_spatial_coverage


def _validation_mapping(**overrides) -> dict:
    value = {
        "max_check_rmse_m": 30.0,
        "max_check_p95_m": 30.0,
        "max_check_error_m": 45.0,
        "min_check_points": 0,
        "affine_min_improvement_ratio": None,
        "spatial_distribution_reviewed": True,
        "spatial_distribution_review_source": "review:hlbe-operator",
    }
    value.update(overrides)
    return value


def test_allow_unverified_exact_fit_defaults_to_off() -> None:
    settings = ValidationSettings.from_mapping(_validation_mapping())

    assert settings.allow_unverified_exact_fit is False


def test_allow_unverified_exact_fit_accepts_explicit_true() -> None:
    settings = ValidationSettings.from_mapping(
        _validation_mapping(allow_unverified_exact_fit=True)
    )

    assert settings.allow_unverified_exact_fit is True


def test_allow_unverified_exact_fit_rejects_non_boolean() -> None:
    with pytest.raises(ValueError, match="allow_unverified_exact_fit"):
        ValidationSettings.from_mapping(
            _validation_mapping(allow_unverified_exact_fit="yes")
        )


def test_validation_keys_stay_strict() -> None:
    with pytest.raises(ValueError, match="Invalid validation keys"):
        ValidationSettings.from_mapping(_validation_mapping(unknown_key=True))


def _policy() -> SpatialCoveragePolicy:
    return SpatialCoveragePolicy(
        min_training_extent_x_ratio=0.0,
        min_training_extent_y_ratio=0.0,
        min_training_hull_area_ratio=0.0,
        max_drawing_vertices_outside_training_bbox_ratio=1.0,
        min_check_baseline_to_drawing_diagonal_ratio=0.0,
        min_check_hull_area_ratio=0.0,
        max_drawing_vertices_outside_training_hull_ratio=1.0,
    )


DRAWING = [(0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0)]
TRAINING = [(20.0, 20.0), (80.0, 80.0)]


def test_sparse_controls_fail_closed_by_default() -> None:
    result = evaluate_spatial_coverage(DRAWING, TRAINING, [], _policy())

    assert result["passed"] is False
    assert "fewer than 3 active training controls" in result["failures"]
    assert "fewer than 3 independent active check controls" in result["failures"]
    assert result["allow_unverified_exact_fit"] is False


def test_sparse_controls_downgrade_to_warnings_with_allowance() -> None:
    result = evaluate_spatial_coverage(
        DRAWING, TRAINING, [], _policy(),
        allow_unverified_exact_fit=True,
    )

    assert result["passed"] is True
    assert result["failures"] == []
    assert "fewer than 3 active training controls" in result["warnings"]
    assert "fewer than 3 independent active check controls" in result["warnings"]
    assert (
        "exact_fit_not_independently_verified: "
        "fewer than 3 independent check controls"
    ) in result["warnings"]
    assert result["allow_unverified_exact_fit"] is True


def test_geometric_red_flags_stay_blocking_with_allowance() -> None:
    outside_training = [(20.0, 20.0), (800.0, 80.0)]

    result = evaluate_spatial_coverage(
        DRAWING, outside_training, [], _policy(),
        allow_unverified_exact_fit=True,
    )

    assert result["passed"] is False
    assert any(
        "training_controls_outside_drawing_bbox" in failure
        for failure in result["failures"]
    )


def _control(
    point_id: str,
    cad: tuple[float, float],
    target: tuple[float, float],
    role: str,
) -> dict:
    return {
        "point_id": point_id,
        "cad_x": cad[0],
        "cad_y": cad[1],
        "target_easting": target[0],
        "target_northing": target[1],
        "target_crs": "EPSG:3857",
        "role": role,
        "source": "in-drawing-latlon-annotation",
        "accuracy_m": 5.0,
        "weight": 1.0,
        "enabled": True,
    }


def _gcp_profile(
    tmp_path: Path,
    controls: list[dict],
    *,
    allow_unverified_exact_fit: bool,
) -> GCPProfile:
    payload = {
        "schema_version": "cad2gis-gcp-profile-v1",
        "enabled": True,
        "source_sha256": "a" * 64,
        "source_crs": "EPSG:3857",
        "target_crs": "EPSG:3857",
        "requested_model": "similarity",
        "controls": controls,
        "control_schema": {
            "description": "In-drawing authoritative lat/lon annotations.",
            "required_fields": [
                "point_id", "cad_x", "cad_y", "target_easting",
                "target_northing", "target_crs", "role", "source",
                "accuracy_m", "weight", "enabled",
            ],
            "fields": {
                name: f"Reviewed {name}."
                for name in (
                    "point_id", "cad_x", "cad_y", "target_easting",
                    "target_northing", "target_crs", "role", "source",
                    "accuracy_m", "weight", "enabled",
                )
            },
        },
        "model_selection": {
            "candidate_order": ["similarity", "translation", "affine"],
            "policy": "select_shape_preserving_model_with_independent_validation",
            "minimum_training_controls": {
                "translation": 2,
                "similarity": 2,
                "affine": 6,
            },
            "affine_gate": {
                "require_spatially_structured_similarity_residuals": True,
                "spatial_structure_reviewed": False,
                "require_holdout_improvement": True,
            },
            "nonlinear_models": {
                "enabled": False,
                "reason": "Dense independent controls are not available.",
            },
        },
        "robust": {
            "enabled": False,
            "max_iterations": 1,
            "outlier_threshold_m": None,
        },
        "validation": _validation_mapping(
            allow_unverified_exact_fit=allow_unverified_exact_fit
        ),
        "transform_limits": {
            "max_pivot_shift_m": 1.0e9,
            "max_abs_rotation_deg": 180.0,
            "max_scale_deviation_ratio": 0.9999,
            "max_affine_condition_number": 1.0e12,
        },
    }
    path = tmp_path / "gcp_profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return GCPProfile.load(path, expected_source_sha256="a" * 64)


# Two train controls related to the target grid by an exact similarity:
# target = cad * 0.001 + (400000, 700000).
TRAIN_CONTROLS = [
    _control("GCP-1", (100000.0, 200000.0), (500000.0, 900000.0), "train"),
    _control("GCP-2", (160000.0, 200000.0), (500060.0, 900000.0), "train"),
]


def test_two_control_exact_fit_passes_with_allowance(tmp_path: Path) -> None:
    profile = _gcp_profile(
        tmp_path, TRAIN_CONTROLS, allow_unverified_exact_fit=True,
    )
    transformer = DirectTransformer("EPSG:3857", "EPSG:3857")

    result = fit_profile(profile, transformer)

    assert result.validation_passed is True
    assert result.selected_model == "similarity"
    assert result.check_metrics.count == 0
    assert result.check_metrics.unavailable_reason == (
        "no independent check controls observed"
    )
    assert result.to_dict()["check_metrics"]["unavailable_reason"] == (
        "no independent check controls observed"
    )
    assert result.train_metrics.rmse_m == pytest.approx(0.0, abs=1e-6)


def test_two_control_exact_fit_fails_without_allowance(tmp_path: Path) -> None:
    profile = _gcp_profile(
        tmp_path, TRAIN_CONTROLS, allow_unverified_exact_fit=False,
    )
    transformer = DirectTransformer("EPSG:3857", "EPSG:3857")

    result = fit_profile(profile, transformer)

    assert result.validation_passed is False
    assert any(
        "check_rmse_m" in failure for failure in result.validation_failures
    )


def test_mismatched_check_control_still_fails_with_allowance(
    tmp_path: Path,
) -> None:
    controls = [
        *TRAIN_CONTROLS,
        # Correct target would be (500030.0, 900000.0); 100 m off.
        _control(
            "GCP-3", (130000.0, 200000.0), (500130.0, 900000.0), "check",
        ),
    ]
    profile = _gcp_profile(
        tmp_path, controls, allow_unverified_exact_fit=True,
    )
    transformer = DirectTransformer("EPSG:3857", "EPSG:3857")

    result = fit_profile(profile, transformer)

    assert result.validation_passed is False
    assert result.check_metrics.count == 1
    assert any(
        "check_error_m" in failure for failure in result.validation_failures
    )


def _status_with_calibration(calibration: dict) -> RunStatus:
    return pipeline._derive_conversion_status(
        entities=[object()],
        ingest_diagnostics={},
        semantic_diagnostics={},
        style_coverage={},
        unresolved=[],
        terminal_accounting={},
        validation_summary={},
        georeference_diagnostics={"calibration": calibration},
        diagnostics={},
        plan_domain_diagnostics=None,
    )


def test_unverified_exact_fit_run_status_is_conditional_never_verified() -> None:
    status = _status_with_calibration({
        "status": "accepted",
        "spatial_coverage": {
            "allow_unverified_exact_fit": True,
            "check_control_count": 1,
        },
    })

    assert status is RunStatus.CONDITIONAL


def test_verified_requires_three_check_controls_even_with_allowance() -> None:
    status = _status_with_calibration({
        "status": "accepted",
        "spatial_coverage": {
            "allow_unverified_exact_fit": True,
            "check_control_count": 3,
        },
    })

    assert status is RunStatus.VERIFIED


def test_accepted_calibration_without_allowance_keeps_verified() -> None:
    status = _status_with_calibration({
        "status": "accepted",
        "spatial_coverage": {
            "allow_unverified_exact_fit": False,
            "check_control_count": 1,
        },
    })

    assert status is RunStatus.VERIFIED
