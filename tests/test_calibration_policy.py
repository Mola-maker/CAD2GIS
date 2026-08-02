from __future__ import annotations

import pytest

from cad2gis.cad2gis_v3.calibration import ModelSelectionSettings


def _settings(policy: str, order: list[str]) -> dict:
    return {
        "candidate_order": order,
        "policy": policy,
        "minimum_training_controls": {
            "translation": 3,
            "similarity": 4,
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
    }


def test_shape_preserving_policy_prioritizes_similarity_then_translation() -> None:
    settings = ModelSelectionSettings.from_mapping(_settings(
        "select_shape_preserving_model_with_independent_validation",
        ["similarity", "translation", "affine"],
    ))

    assert settings.candidate_order == (
        "similarity", "translation", "affine",
    )
    assert settings.minimums["similarity"] == 4


def test_legacy_simplest_policy_remains_source_profile_compatible() -> None:
    settings = ModelSelectionSettings.from_mapping(_settings(
        "select_the_simplest_model_that_passes_independent_validation",
        ["translation", "similarity", "affine"],
    ))

    assert settings.candidate_order[0] == "translation"


def test_model_selection_policy_rejects_mismatched_candidate_order() -> None:
    with pytest.raises(ValueError, match="does not match policy"):
        ModelSelectionSettings.from_mapping(_settings(
            "select_shape_preserving_model_with_independent_validation",
            ["translation", "similarity", "affine"],
        ))
