"""Deterministic ground-control calibration after the nominal CRS transform.

The source CAD coordinates remain immutable.  Every control point is first
projected by the existing :class:`~cad2gis_v3.georef.DirectTransformer`; this
module then fits a small residual transform in delivery coordinates.  No CAD
geometry is edited and no probabilistic/LLM component is used.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from pyproj import CRS

from .units import UnitCrsContract


Point = tuple[float, float]

CONTROL_FIELDS = (
    "point_id",
    "cad_x",
    "cad_y",
    "target_easting",
    "target_northing",
    "target_crs",
    "role",
    "source",
    "accuracy_m",
    "weight",
    "enabled",
)
SUPPORTED_MODELS = ("disabled", "translation", "similarity", "affine")
REQUESTED_MODELS = ("auto", "translation", "similarity", "affine")
THEORETICAL_MINIMUM_CONTROLS = {
    "translation": 1,
    "similarity": 2,
    "affine": 3,
}

# Geometry gates are deliberately evaluated in delivery metres after centring.
# A one-micrometre RMS radius is below useful GCP precision, while the ULP
# multiplier rejects spreads represented by only a few dozen binary64 steps at
# large eastings/northings.  After RMS scale normalisation, a design condition
# cap of 1e6 limits the corresponding normal-equation condition to about 1e12;
# this is a permissive numerical-safety gate, not a substitute for the reviewed
# spatial-distribution gate in the profile.
_DESIGN_MIN_RMS_SPREAD_M = 1e-6
_DESIGN_MIN_SPREAD_ULPS = 64.0
_DESIGN_RANK_RELATIVE_TOLERANCE = 1e-12
_DESIGN_MAX_CONDITION_NUMBER = 1e6


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not boolean")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _positive_float(value: Any, name: str) -> float:
    result = _finite_float(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _optional_positive_float(value: Any, name: str) -> float | None:
    return None if value is None else _positive_float(value, name)


def _strict_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing or unknown:
        raise ValueError(
            f"Invalid {context} keys; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


def _crs_equal(left: str, right: str) -> bool:
    try:
        return CRS.from_user_input(left) == CRS.from_user_input(right)
    except Exception as exc:  # pyproj raises several input-specific subclasses
        raise ValueError(f"Invalid CRS comparison: {left!r}, {right!r}") from exc


def _metric_projected_crs(
    value: str,
    name: str,
    *,
    require_metre: bool = True,
) -> CRS:
    """Validate a projected CRS and, when requested, its linear unit.

    Source CRS axes are allowed to be feet (or another reviewed linear unit)
    because :class:`~cad2gis_v3.units.UnitCrsContract` explicitly bridges CAD
    drawing units to the CRS axis.  Residual calibration itself remains a
    target-grid metric operation, so target CRS validation keeps the historical
    metre-only gate by leaving ``require_metre=True``.
    """
    try:
        crs = CRS.from_user_input(value)
    except Exception as exc:
        raise ValueError(f"{name} is not a valid CRS: {value!r}") from exc
    if not crs.is_projected:
        raise ValueError(f"{name} must be a projected CRS")
    axes = crs.axis_info
    if len(axes) < 2:
        raise ValueError(f"{name} must expose two projected coordinate axes")
    factors = []
    for axis in axes[:2]:
        factor = axis.unit_conversion_factor
        if factor is None or not math.isfinite(factor) or factor <= 0.0:
            raise ValueError(f"{name} axes must declare a finite positive linear unit")
        factors.append(float(factor))
    if not math.isclose(factors[0], factors[1], rel_tol=1e-12, abs_tol=0.0):
        raise ValueError(f"{name} horizontal axes use different linear units")
    if require_metre and not math.isclose(
        factors[0], 1.0, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError(f"{name} axes must use metres")
    return crs


def _validate_transformer_unit_contract(
    transformer: Any,
    source_crs: str,
    target_crs: str,
) -> float:
    """Validate unit/CRS evidence and return target-axis metres per unit.

    The nominal transformer consumes CAD coordinates and emits target CRS
    coordinates.  A reviewed ``UnitCrsContract`` is therefore mandatory for a
    non-metre source axis (the common State Plane/US-foot case).  The legacy
    synthetic transformer used by the v3 calibration unit tests is retained for
    projected metre axes, but it cannot silently authorize a non-metric or
    unreviewed source conversion.  Target GCP residuals are always reported in
    metres and consequently reject non-metre target axes even when the nominal
    CRS transformer can represent them.
    """

    source = _metric_projected_crs(source_crs, "Transformer source_crs", require_metre=False)
    target = _metric_projected_crs(target_crs, "Transformer target_crs", require_metre=True)
    source_factor = float(source.axis_info[0].unit_conversion_factor)
    target_factor = float(target.axis_info[0].unit_conversion_factor)

    contract = getattr(transformer, "unit_contract", None)
    if contract is None:
        # Keep the historical test/dummy path usable only where no unit bridge
        # is necessary.  State Plane feet without the explicit contract must
        # fail closed instead of treating CAD coordinates as CRS coordinates.
        if not math.isclose(source_factor, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(
                "Transformer source_crs uses a non-metre axis; a reviewed "
                "transformer.unit_contract is required"
            )
        return target_factor

    if not isinstance(contract, UnitCrsContract):
        raise TypeError("transformer.unit_contract must be a UnitCrsContract")
    if not contract.can_direct_transform:
        raise ValueError(
            "Transformer unit_crs_contract is not a direct reviewed CRS contract"
        )
    if not contract.source_crs or not _crs_equal(str(contract.source_crs), source_crs):
        raise ValueError("Transformer unit_crs_contract source_crs does not match")
    if not _crs_equal(str(contract.target_crs), target_crs):
        raise ValueError("Transformer unit_crs_contract target_crs does not match")
    contract_source_axis = contract.source_crs_axis_unit
    if contract_source_axis is None:
        raise ValueError(
            "Transformer unit_crs_contract lacks a reviewed source CRS axis unit"
        )
    contract_source_factor = _positive_float(
        contract_source_axis.metres_per_unit,
        "transformer.unit_contract.source_crs_axis_unit.metres_per_unit",
    )
    if not math.isclose(contract_source_factor, source_factor, rel_tol=1e-12, abs_tol=0.0):
        raise ValueError(
            "Transformer unit_crs_contract source CRS axis unit does not match CRS"
        )
    source_scale = _positive_float(
        contract.source_coordinate_scale_to_m,
        "transformer.unit_contract.source_coordinate_scale_to_m",
    )
    cad_scale = _positive_float(
        contract.cad_unit.metres_per_unit,
        "transformer.unit_contract.cad_unit.metres_per_unit",
    )
    if not math.isclose(cad_scale, source_scale, rel_tol=1e-12, abs_tol=0.0):
        raise ValueError(
            "Transformer unit_crs_contract CAD drawing unit scale is inconsistent"
        )
    if not math.isclose(cad_scale, 1.0, rel_tol=0.0, abs_tol=1e-12) and not contract.source_coordinate_scale_reviewed:
        raise ValueError(
            "Non-metre CAD drawing requires a reviewed source coordinate scale"
        )
    source_to_axis = contract.source_to_crs_axis_factor
    if source_to_axis is None or not math.isfinite(float(source_to_axis)) or float(source_to_axis) <= 0.0:
        raise ValueError(
            "Transformer unit_crs_contract lacks a reviewed CAD-to-source-axis scale"
        )
    expected_source_to_axis = source_scale / contract_source_factor
    if not math.isclose(float(source_to_axis), expected_source_to_axis, rel_tol=1e-12, abs_tol=0.0):
        raise ValueError(
            "Transformer unit_crs_contract CAD-to-source-axis scale is inconsistent"
        )
    contract_target_factor = _positive_float(
        contract.target_crs_axis_unit.metres_per_unit,
        "transformer.unit_contract.target_crs_axis_unit.metres_per_unit",
    )
    if not math.isclose(contract_target_factor, target_factor, rel_tol=1e-12, abs_tol=0.0):
        raise ValueError(
            "Transformer unit_crs_contract target CRS axis unit does not match CRS"
        )
    if not math.isclose(contract_target_factor, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(
            "Calibration target CRS axes must use metres for GCP residuals"
        )
    return contract_target_factor


def _reject_duplicate_control_coordinates(
    controls: Sequence["GroundControlPoint"],
) -> None:
    cad_seen: dict[Point, str] = {}
    target_seen: dict[Point, str] = {}
    for control in controls:
        for point, seen, label in (
            (control.cad_point, cad_seen, "cad_point"),
            (control.target_point, target_seen, "target_point"),
        ):
            previous = seen.get(point)
            if previous is not None:
                raise ValueError(
                    f"GCP controls {previous!r} and {control.point_id!r} duplicate {label}"
                )
            seen[point] = control.point_id


def _validate_sha256(value: Any, name: str = "source_sha256") -> str:
    digest = str(value).lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{name} must be a 64-character hexadecimal SHA-256 digest")
    return digest


@dataclass(frozen=True)
class GroundControlPoint:
    """One reviewed CAD-to-ground observation.

    ``weight`` is the human-reviewed relative weight.  The fitting weight is
    ``weight / accuracy_m**2``, so reported source accuracy remains explicit.
    """

    point_id: str
    cad_point: Point
    target_point: Point
    target_crs: str
    role: str
    source: str
    accuracy_m: float
    weight: float
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.point_id, str) or not self.point_id.strip():
            raise ValueError("GCP point_id must not be empty")
        if self.role not in {"train", "check"}:
            raise ValueError(f"GCP {self.point_id}: role must be train or check")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError(f"GCP {self.point_id}: source provenance must not be empty")
        if not isinstance(self.enabled, bool):
            raise ValueError(f"GCP {self.point_id}: enabled must be boolean")
        for name, point in (("cad_point", self.cad_point), ("target_point", self.target_point)):
            if len(point) != 2:
                raise ValueError(f"GCP {self.point_id}: {name} must contain two coordinates")
            _finite_float(point[0], f"GCP {self.point_id} {name}[0]")
            _finite_float(point[1], f"GCP {self.point_id} {name}[1]")
        _positive_float(self.accuracy_m, f"GCP {self.point_id} accuracy_m")
        _positive_float(self.weight, f"GCP {self.point_id} weight")
        if not math.isfinite(self.fitting_weight) or self.fitting_weight <= 0.0:
            raise ValueError(f"GCP {self.point_id}: derived fitting weight must be finite and positive")

    @property
    def fitting_weight(self) -> float:
        return self.weight / (self.accuracy_m * self.accuracy_m)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        expected_target_crs: str,
    ) -> "GroundControlPoint":
        if not isinstance(value, Mapping):
            raise ValueError("Every GCP control must be a JSON object")
        _strict_keys(value, set(CONTROL_FIELDS), "GCP control")
        if not isinstance(value["point_id"], str):
            raise ValueError("GCP point_id must be a string")
        point_id = value["point_id"].strip()
        if not isinstance(value["source"], str):
            raise ValueError(f"GCP {point_id or '<unknown>'}: source provenance must be a string")
        source = value["source"].strip()
        role = str(value["role"]).strip().lower()
        target_crs = str(value["target_crs"]).strip()
        if not point_id:
            raise ValueError("GCP point_id must not be empty")
        if not source:
            raise ValueError(f"GCP {point_id}: source provenance must not be empty")
        if role not in {"train", "check"}:
            raise ValueError(f"GCP {point_id}: role must be train or check")
        if not _crs_equal(target_crs, expected_target_crs):
            raise ValueError(
                f"GCP {point_id}: target_crs {target_crs!r} does not match "
                f"profile target_crs {expected_target_crs!r}"
            )
        if not isinstance(value["enabled"], bool):
            raise ValueError(f"GCP {point_id}: enabled must be boolean")
        return cls(
            point_id=point_id,
            cad_point=(
                _finite_float(value["cad_x"], f"GCP {point_id} cad_x"),
                _finite_float(value["cad_y"], f"GCP {point_id} cad_y"),
            ),
            target_point=(
                _finite_float(value["target_easting"], f"GCP {point_id} target_easting"),
                _finite_float(value["target_northing"], f"GCP {point_id} target_northing"),
            ),
            target_crs=target_crs,
            role=role,
            source=source,
            accuracy_m=_positive_float(value["accuracy_m"], f"GCP {point_id} accuracy_m"),
            weight=_positive_float(value["weight"], f"GCP {point_id} weight"),
            enabled=value["enabled"],
        )


@dataclass(frozen=True)
class RobustSettings:
    enabled: bool
    max_iterations: int
    outlier_threshold_m: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("robust.enabled must be boolean")
        if (
            isinstance(self.max_iterations, bool)
            or not isinstance(self.max_iterations, int)
            or self.max_iterations <= 0
        ):
            raise ValueError("robust.max_iterations must be a positive integer")
        if self.outlier_threshold_m is not None:
            _positive_float(self.outlier_threshold_m, "robust.outlier_threshold_m")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RobustSettings":
        if not isinstance(value, Mapping):
            raise ValueError("robust must be a JSON object")
        _strict_keys(value, {"enabled", "max_iterations", "outlier_threshold_m"}, "robust")
        if not isinstance(value["enabled"], bool):
            raise ValueError("robust.enabled must be boolean")
        if isinstance(value["max_iterations"], bool):
            raise ValueError("robust.max_iterations must be a positive integer")
        maximum = int(value["max_iterations"])
        if maximum <= 0 or maximum != value["max_iterations"]:
            raise ValueError("robust.max_iterations must be a positive integer")
        return cls(
            enabled=value["enabled"],
            max_iterations=maximum,
            outlier_threshold_m=_optional_positive_float(
                value["outlier_threshold_m"], "robust.outlier_threshold_m"
            ),
        )


@dataclass(frozen=True)
class ValidationSettings:
    max_check_rmse_m: float | None
    max_check_p95_m: float | None
    max_check_error_m: float | None
    min_check_points: int
    affine_min_improvement_ratio: float | None
    spatial_distribution_reviewed: bool
    spatial_distribution_review_source: str

    def __post_init__(self) -> None:
        for name, value in (
            ("max_check_rmse_m", self.max_check_rmse_m),
            ("max_check_p95_m", self.max_check_p95_m),
            ("max_check_error_m", self.max_check_error_m),
        ):
            if value is not None:
                _positive_float(value, f"validation.{name}")
        if (
            isinstance(self.min_check_points, bool)
            or not isinstance(self.min_check_points, int)
            or self.min_check_points < 0
        ):
            raise ValueError("validation.min_check_points must be a non-negative integer")
        if self.affine_min_improvement_ratio is not None:
            ratio = _finite_float(
                self.affine_min_improvement_ratio,
                "validation.affine_min_improvement_ratio",
            )
            if not 0.0 < ratio < 1.0:
                raise ValueError("validation.affine_min_improvement_ratio must be in (0, 1)")
        if not isinstance(self.spatial_distribution_reviewed, bool):
            raise ValueError("validation.spatial_distribution_reviewed must be boolean")
        if not isinstance(self.spatial_distribution_review_source, str):
            raise ValueError("validation.spatial_distribution_review_source must be a string")
        if (
            self.spatial_distribution_reviewed
            and not self.spatial_distribution_review_source.strip()
        ):
            raise ValueError(
                "Reviewed spatial distribution requires a non-empty review source"
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ValidationSettings":
        if not isinstance(value, Mapping):
            raise ValueError("validation must be a JSON object")
        expected = {
            "max_check_rmse_m",
            "max_check_p95_m",
            "max_check_error_m",
            "min_check_points",
            "affine_min_improvement_ratio",
            "spatial_distribution_reviewed",
            "spatial_distribution_review_source",
        }
        _strict_keys(value, expected, "validation")
        if isinstance(value["min_check_points"], bool):
            raise ValueError("validation.min_check_points must be a non-negative integer")
        minimum = int(value["min_check_points"])
        if minimum < 0 or minimum != value["min_check_points"]:
            raise ValueError("validation.min_check_points must be a non-negative integer")
        ratio_value = value["affine_min_improvement_ratio"]
        ratio = None if ratio_value is None else _finite_float(
            ratio_value, "validation.affine_min_improvement_ratio"
        )
        if ratio is not None and not 0.0 < ratio < 1.0:
            raise ValueError("validation.affine_min_improvement_ratio must be in (0, 1)")
        return cls(
            max_check_rmse_m=_optional_positive_float(
                value["max_check_rmse_m"], "validation.max_check_rmse_m"
            ),
            max_check_p95_m=_optional_positive_float(
                value["max_check_p95_m"], "validation.max_check_p95_m"
            ),
            max_check_error_m=_optional_positive_float(
                value["max_check_error_m"], "validation.max_check_error_m"
            ),
            min_check_points=minimum,
            affine_min_improvement_ratio=ratio,
            spatial_distribution_reviewed=value["spatial_distribution_reviewed"],
            spatial_distribution_review_source=value["spatial_distribution_review_source"],
        )


@dataclass(frozen=True)
class TransformLimitsSettings:
    max_pivot_shift_m: float | None
    max_abs_rotation_deg: float | None
    max_scale_deviation_ratio: float | None
    max_affine_condition_number: float | None

    def __post_init__(self) -> None:
        for name, value in (
            ("max_pivot_shift_m", self.max_pivot_shift_m),
            ("max_abs_rotation_deg", self.max_abs_rotation_deg),
            ("max_scale_deviation_ratio", self.max_scale_deviation_ratio),
            ("max_affine_condition_number", self.max_affine_condition_number),
        ):
            if value is not None:
                _positive_float(value, f"transform_limits.{name}")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TransformLimitsSettings":
        if not isinstance(value, Mapping):
            raise ValueError("transform_limits must be a JSON object")
        keys = {
            "max_pivot_shift_m",
            "max_abs_rotation_deg",
            "max_scale_deviation_ratio",
            "max_affine_condition_number",
        }
        _strict_keys(value, keys, "transform_limits")
        return cls(
            max_pivot_shift_m=_optional_positive_float(
                value["max_pivot_shift_m"], "transform_limits.max_pivot_shift_m"
            ),
            max_abs_rotation_deg=_optional_positive_float(
                value["max_abs_rotation_deg"], "transform_limits.max_abs_rotation_deg"
            ),
            max_scale_deviation_ratio=_optional_positive_float(
                value["max_scale_deviation_ratio"],
                "transform_limits.max_scale_deviation_ratio",
            ),
            max_affine_condition_number=_optional_positive_float(
                value["max_affine_condition_number"],
                "transform_limits.max_affine_condition_number",
            ),
        )


@dataclass(frozen=True)
class ModelSelectionSettings:
    candidate_order: tuple[str, ...]
    policy: str
    minimum_training_controls: tuple[tuple[str, int], ...]
    require_spatially_structured_similarity_residuals: bool
    spatial_structure_reviewed: bool
    require_holdout_improvement: bool
    nonlinear_models_enabled: bool
    nonlinear_models_reason: str

    @property
    def minimums(self) -> dict[str, int]:
        return dict(self.minimum_training_controls)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelSelectionSettings":
        if not isinstance(value, Mapping):
            raise ValueError("model_selection must be a JSON object")
        _strict_keys(
            value,
            {"candidate_order", "policy", "minimum_training_controls", "affine_gate", "nonlinear_models"},
            "model_selection",
        )
        order = tuple(str(item) for item in value["candidate_order"])
        if order != ("translation", "similarity", "affine"):
            raise ValueError(
                "model_selection.candidate_order must be translation, similarity, affine"
            )
        policy = str(value["policy"]).strip()
        if policy != "select_the_simplest_model_that_passes_independent_validation":
            raise ValueError("Unsupported model_selection.policy")
        minimums_value = value["minimum_training_controls"]
        if not isinstance(minimums_value, Mapping):
            raise ValueError("model_selection.minimum_training_controls must be an object")
        _strict_keys(
            minimums_value,
            {"translation", "similarity", "affine"},
            "model_selection.minimum_training_controls",
        )
        minimums: list[tuple[str, int]] = []
        for model in order:
            raw_minimum = minimums_value[model]
            if isinstance(raw_minimum, bool):
                raise ValueError(f"minimum training controls for {model} must be an integer")
            minimum = int(raw_minimum)
            if minimum < THEORETICAL_MINIMUM_CONTROLS[model] or minimum != raw_minimum:
                raise ValueError(
                    f"minimum training controls for {model} must be an integer >= "
                    f"{THEORETICAL_MINIMUM_CONTROLS[model]}"
                )
            minimums.append((model, minimum))
        affine_gate = value["affine_gate"]
        if not isinstance(affine_gate, Mapping):
            raise ValueError("model_selection.affine_gate must be an object")
        _strict_keys(
            affine_gate,
            {
                "require_spatially_structured_similarity_residuals",
                "spatial_structure_reviewed",
                "require_holdout_improvement",
            },
            "model_selection.affine_gate",
        )
        nonlinear = value["nonlinear_models"]
        if not isinstance(nonlinear, Mapping):
            raise ValueError("model_selection.nonlinear_models must be an object")
        _strict_keys(nonlinear, {"enabled", "reason"}, "model_selection.nonlinear_models")
        boolean_values = (
            affine_gate["require_spatially_structured_similarity_residuals"],
            affine_gate["spatial_structure_reviewed"],
            affine_gate["require_holdout_improvement"],
            nonlinear["enabled"],
        )
        if not all(isinstance(item, bool) for item in boolean_values):
            raise ValueError("model-selection gate flags must be boolean")
        if affine_gate["require_spatially_structured_similarity_residuals"] is not True:
            raise ValueError(
                "Affine promotion must require reviewed spatial structure in "
                "similarity residuals"
            )
        if affine_gate["require_holdout_improvement"] is not True:
            raise ValueError(
                "Affine promotion must require independent holdout improvement"
            )
        reason = str(nonlinear["reason"]).strip()
        if nonlinear["enabled"] or not reason:
            raise ValueError("Nonlinear calibration must remain disabled with an explicit reason")
        return cls(
            candidate_order=order,
            policy=policy,
            minimum_training_controls=tuple(minimums),
            require_spatially_structured_similarity_residuals=affine_gate[
                "require_spatially_structured_similarity_residuals"
            ],
            spatial_structure_reviewed=affine_gate["spatial_structure_reviewed"],
            require_holdout_improvement=affine_gate["require_holdout_improvement"],
            nonlinear_models_enabled=nonlinear["enabled"],
            nonlinear_models_reason=reason,
        )


@dataclass(frozen=True)
class ControlSchema:
    description: str
    required_fields: tuple[str, ...]
    field_descriptions: tuple[tuple[str, str], ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ControlSchema":
        if not isinstance(value, Mapping):
            raise ValueError("control_schema must be a JSON object")
        _strict_keys(value, {"description", "required_fields", "fields"}, "control_schema")
        description = str(value["description"]).strip()
        required = tuple(str(item) for item in value["required_fields"])
        fields = value["fields"]
        if not description:
            raise ValueError("control_schema.description must not be empty")
        if required != CONTROL_FIELDS:
            raise ValueError("control_schema.required_fields does not match the runtime contract")
        if not isinstance(fields, Mapping):
            raise ValueError("control_schema.fields must be an object")
        _strict_keys(fields, set(CONTROL_FIELDS), "control_schema.fields")
        descriptions = tuple((name, str(fields[name]).strip()) for name in CONTROL_FIELDS)
        if any(not text for _, text in descriptions):
            raise ValueError("Every control_schema field requires a description")
        return cls(description, required, descriptions)


@dataclass(frozen=True)
class GCPProfile:
    path: Path
    profile_sha256: str
    schema_version: str
    enabled: bool
    source_sha256: str
    source_crs: str
    target_crs: str
    requested_model: str
    controls: tuple[GroundControlPoint, ...]
    control_schema: ControlSchema
    model_selection: ModelSelectionSettings
    robust: RobustSettings
    validation: ValidationSettings
    transform_limits: TransformLimitsSettings

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        expected_source_sha256: str | None = None,
    ) -> "GCPProfile":
        resolved = Path(path).resolve()
        profile_bytes = resolved.read_bytes()
        profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
        value = json.loads(profile_bytes.decode("utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError(f"Expected a JSON object: {resolved}")
        expected = {
            "schema_version",
            "enabled",
            "source_sha256",
            "source_crs",
            "target_crs",
            "requested_model",
            "controls",
            "control_schema",
            "model_selection",
            "robust",
            "validation",
            "transform_limits",
        }
        _strict_keys(value, expected, "GCP profile")
        if value["schema_version"] != "cad2gis-gcp-profile-v1":
            raise ValueError(f"Unsupported GCP profile: {value['schema_version']}")
        if not isinstance(value["enabled"], bool):
            raise ValueError("GCP profile enabled must be boolean")
        source_sha256 = _validate_sha256(value["source_sha256"])
        if expected_source_sha256 is not None:
            expected_hash = _validate_sha256(expected_source_sha256, "expected_source_sha256")
            if source_sha256 != expected_hash:
                raise ValueError("GCP profile is stale or bound to a different DWG")
        source_crs = str(value["source_crs"]).strip()
        target_crs = str(value["target_crs"]).strip()
        # Source axes may be feet (for example State Plane) when the delivery
        # transformer carries the reviewed UnitCrsContract.  The target remains
        # metric because all GCP residual fields are explicitly ``*_m``.
        _metric_projected_crs(source_crs, "GCP profile source_crs", require_metre=False)
        _metric_projected_crs(target_crs, "GCP profile target_crs")
        requested_model = str(value["requested_model"]).strip().lower()
        if requested_model not in REQUESTED_MODELS:
            raise ValueError(f"Unsupported requested_model: {requested_model}")
        if not isinstance(value["controls"], list):
            raise ValueError("controls must be a JSON array")
        controls = tuple(
            GroundControlPoint.from_mapping(item, expected_target_crs=target_crs)
            for item in value["controls"]
        )
        point_ids = [control.point_id for control in controls]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("GCP point_id values must be unique")
        _reject_duplicate_control_coordinates(
            tuple(control for control in controls if control.enabled)
        )
        profile = cls(
            path=resolved,
            profile_sha256=profile_sha256,
            schema_version=value["schema_version"],
            enabled=value["enabled"],
            source_sha256=source_sha256,
            source_crs=source_crs,
            target_crs=target_crs,
            requested_model=requested_model,
            controls=controls,
            control_schema=ControlSchema.from_mapping(value["control_schema"]),
            model_selection=ModelSelectionSettings.from_mapping(value["model_selection"]),
            robust=RobustSettings.from_mapping(value["robust"]),
            validation=ValidationSettings.from_mapping(value["validation"]),
            transform_limits=TransformLimitsSettings.from_mapping(value["transform_limits"]),
        )
        profile._validate_activation()
        return profile

    @property
    def sha256(self) -> str:
        """Hash of the exact profile bytes parsed by :meth:`load`."""
        return self.profile_sha256

    @property
    def active_controls(self) -> tuple[GroundControlPoint, ...]:
        return tuple(control for control in self.controls if control.enabled)

    def validate_source_hash(self, source_sha256: str) -> None:
        if self.source_sha256 != _validate_sha256(source_sha256):
            raise ValueError("GCP profile is stale or bound to a different DWG")

    def validate_transformer(self, transformer: Any) -> None:
        source_crs = getattr(transformer, "source_crs", None)
        target_crs = getattr(transformer, "target_crs", None)
        if source_crs is None or target_crs is None or not callable(getattr(transformer, "point", None)):
            raise TypeError("transformer must expose source_crs, target_crs, and point()")
        _validate_transformer_unit_contract(transformer, str(source_crs), str(target_crs))
        if not _crs_equal(str(source_crs), self.source_crs):
            raise ValueError("GCP profile source_crs does not match DirectTransformer")
        if not _crs_equal(str(target_crs), self.target_crs):
            raise ValueError("GCP profile target_crs does not match DirectTransformer")

    def _validate_activation(self) -> None:
        if not self.enabled:
            return
        active = self.active_controls
        train_count = sum(control.role == "train" for control in active)
        check_count = sum(control.role == "check" for control in active)
        if not active:
            raise ValueError("An enabled GCP profile requires active controls")
        minimums = self.model_selection.minimums
        minimum_model = (
            self.model_selection.candidate_order[0]
            if self.requested_model == "auto"
            else self.requested_model
        )
        if train_count < minimums[minimum_model]:
            raise ValueError(
                f"Enabled GCP profile requires at least {minimums[minimum_model]} "
                f"training controls for {minimum_model}"
            )
        if check_count < self.validation.min_check_points:
            raise ValueError(
                f"Enabled GCP profile requires at least {self.validation.min_check_points} check controls"
            )
        if not self.validation.spatial_distribution_reviewed:
            raise ValueError(
                "Enabled GCP profile requires reviewed train/check spatial distribution"
            )
        limit_values = (
            self.transform_limits.max_pivot_shift_m,
            self.transform_limits.max_abs_rotation_deg,
            self.transform_limits.max_scale_deviation_ratio,
            self.transform_limits.max_affine_condition_number,
        )
        if any(value is None for value in limit_values):
            raise ValueError("Enabled GCP profile requires all reviewed transform limits")
        gates = (
            self.validation.max_check_rmse_m,
            self.validation.max_check_p95_m,
            self.validation.max_check_error_m,
        )
        if any(item is None for item in gates):
            raise ValueError("Enabled GCP profile requires all check-error validation thresholds")
        if self.robust.enabled and self.robust.outlier_threshold_m is None:
            raise ValueError("Enabled robust fitting requires robust.outlier_threshold_m")
        if (
            self.requested_model in {"auto", "affine"}
            and self.model_selection.require_holdout_improvement
            and self.validation.affine_min_improvement_ratio is None
        ):
            raise ValueError("Affine holdout gate requires affine_min_improvement_ratio")


def _matrix_diagnostics(matrix: tuple[Point, Point]) -> dict[str, float]:
    a, b = matrix[0]
    c, d = matrix[1]
    determinant = a * d - b * c
    energy = a * a + b * b + c * c + d * d
    discriminant = max(energy * energy - 4.0 * determinant * determinant, 0.0)
    root = math.sqrt(discriminant)
    singular_max = math.sqrt(max((energy + root) / 2.0, 0.0))
    singular_min = math.sqrt(max((energy - root) / 2.0, 0.0))
    if singular_min <= 0.0:
        condition = math.inf
    else:
        condition = singular_max / singular_min
    values = {
        "rotation_deg": math.degrees(math.atan2(c - b, a + d)),
        "singular_value_max": singular_max,
        "singular_value_min": singular_min,
        "max_scale_deviation_ratio": max(
            abs(singular_max - 1.0), abs(singular_min - 1.0)
        ),
        "condition_number": condition,
    }
    for name, value in values.items():
        _finite_float(value, f"transform {name}")
    return values


@dataclass(frozen=True)
class ResidualTransform:
    """Immutable 2-D transform represented about stable local origins."""

    model: str
    nominal_origin: Point
    adjusted_origin: Point
    matrix: tuple[Point, Point]

    def __post_init__(self) -> None:
        if self.model not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported residual transform model: {self.model}")
        if len(self.nominal_origin) != 2 or len(self.adjusted_origin) != 2:
            raise ValueError("Residual-transform origins must be 2-D")
        if len(self.matrix) != 2 or any(len(row) != 2 for row in self.matrix):
            raise ValueError("Residual-transform matrix must be 2x2")
        values = (
            *self.nominal_origin,
            *self.adjusted_origin,
            *self.matrix[0],
            *self.matrix[1],
        )
        for index, value in enumerate(values):
            _finite_float(value, f"residual transform coefficient {index}")
        if self.model == "affine":
            determinant = (
                self.matrix[0][0] * self.matrix[1][1]
                - self.matrix[0][1] * self.matrix[1][0]
            )
            if not math.isfinite(determinant) or determinant <= 1e-15:
                raise ValueError("Affine residual transform must have a positive determinant")

    def point(self, point: Sequence[float]) -> Point:
        if len(point) != 2:
            raise ValueError("Calibration input point must contain two coordinates")
        input_e = _finite_float(point[0], "calibration input easting")
        input_n = _finite_float(point[1], "calibration input northing")
        x = input_e - self.nominal_origin[0]
        y = input_n - self.nominal_origin[1]
        result = (
            self.adjusted_origin[0] + self.matrix[0][0] * x + self.matrix[0][1] * y,
            self.adjusted_origin[1] + self.matrix[1][0] * x + self.matrix[1][1] * y,
        )
        return (
            _finite_float(result[0], "adjusted easting"),
            _finite_float(result[1], "adjusted northing"),
        )

    @property
    def parameters(self) -> dict[str, float | str]:
        a, b = self.matrix[0]
        d, e = self.matrix[1]
        intercept_e = self.adjusted_origin[0] - a * self.nominal_origin[0] - b * self.nominal_origin[1]
        intercept_n = self.adjusted_origin[1] - d * self.nominal_origin[0] - e * self.nominal_origin[1]
        result: dict[str, float | str] = {
            "model": self.model,
            "nominal_origin_e": self.nominal_origin[0],
            "nominal_origin_n": self.nominal_origin[1],
            "adjusted_origin_e": self.adjusted_origin[0],
            "adjusted_origin_n": self.adjusted_origin[1],
            "matrix_ee": a,
            "matrix_en": b,
            "matrix_ne": d,
            "matrix_nn": e,
            "intercept_e_m": intercept_e,
            "intercept_n_m": intercept_n,
            "pivot_shift_e_m": self.adjusted_origin[0] - self.nominal_origin[0],
            "pivot_shift_n_m": self.adjusted_origin[1] - self.nominal_origin[1],
        }
        if self.model == "similarity":
            diagnostics = _matrix_diagnostics(self.matrix)
            result["scale"] = math.hypot(a, d)
            result["rotation_deg"] = diagnostics["rotation_deg"]
            result["max_scale_deviation_ratio"] = diagnostics[
                "max_scale_deviation_ratio"
            ]
        if self.model == "affine":
            diagnostics = _matrix_diagnostics(self.matrix)
            result["determinant"] = a * e - b * d
            result["rotation_deg"] = diagnostics["rotation_deg"]
            result["singular_value_max"] = diagnostics["singular_value_max"]
            result["singular_value_min"] = diagnostics["singular_value_min"]
            result["max_scale_deviation_ratio"] = diagnostics[
                "max_scale_deviation_ratio"
            ]
            result["condition_number"] = diagnostics["condition_number"]
        return result


@dataclass(frozen=True)
class ResidualMetrics:
    count: int
    rmse_m: float | None
    p95_m: float | None
    max_m: float | None

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0:
            raise ValueError("Residual metric count must be a non-negative integer")
        values = (self.rmse_m, self.p95_m, self.max_m)
        if self.count == 0:
            if any(value is not None for value in values):
                raise ValueError("Empty residual metrics must use null metric values")
            return
        if any(value is None for value in values):
            raise ValueError("Non-empty residual metrics require RMSE, P95, and max")
        for name, value in zip(("RMSE", "P95", "max"), values):
            finite = _finite_float(value, f"residual {name}")
            if finite < 0.0:
                raise ValueError(f"residual {name} must be non-negative")

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "count": self.count,
            "rmse_m": self.rmse_m,
            "p95_m": self.p95_m,
            "max_m": self.max_m,
        }


@dataclass(frozen=True)
class PointResidual:
    point_id: str
    role: str
    source: str
    cad_point: Point
    nominal_point: Point
    target_point: Point
    adjusted_point: Point
    nominal_error_m: float
    residual_e_m: float
    residual_n_m: float
    error_m: float
    accuracy_m: float
    reviewed_weight: float
    fitting_weight: float
    effective_weight: float
    inlier: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "role": self.role,
            "source": self.source,
            "cad_x": self.cad_point[0],
            "cad_y": self.cad_point[1],
            "nominal_easting": self.nominal_point[0],
            "nominal_northing": self.nominal_point[1],
            "target_easting": self.target_point[0],
            "target_northing": self.target_point[1],
            "adjusted_easting": self.adjusted_point[0],
            "adjusted_northing": self.adjusted_point[1],
            "nominal_error_m": self.nominal_error_m,
            "residual_e_m": self.residual_e_m,
            "residual_n_m": self.residual_n_m,
            "error_m": self.error_m,
            "accuracy_m": self.accuracy_m,
            "reviewed_weight": self.reviewed_weight,
            "fitting_weight": self.fitting_weight,
            "effective_weight": self.effective_weight,
            "inlier": self.inlier,
        }


@dataclass(frozen=True)
class CandidateSummary:
    model: str
    available: bool
    validation_passed: bool
    train_metrics: ResidualMetrics | None
    check_metrics: ResidualMetrics | None
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "available": self.available,
            "validation_passed": self.validation_passed,
            "train_metrics": None if self.train_metrics is None else self.train_metrics.to_dict(),
            "check_metrics": None if self.check_metrics is None else self.check_metrics.to_dict(),
            "failures": list(self.failures),
        }


@dataclass(frozen=True)
class CalibrationResult:
    requested_model: str
    selected_model: str
    source_crs: str
    target_crs: str
    transform: ResidualTransform
    residuals: tuple[PointResidual, ...]
    train_metrics: ResidualMetrics
    check_metrics: ResidualMetrics
    robust_enabled: bool
    robust_iterations: int
    validation_passed: bool | None
    validation_failures: tuple[str, ...] = ()
    candidates: tuple[CandidateSummary, ...] = ()

    @property
    def parameters(self) -> dict[str, float | str]:
        return self.transform.parameters

    def project_native(self, point: Sequence[float], transformer: Any) -> Point:
        """Nominally project one immutable CAD point, then apply calibration."""
        return self.transform.point(transformer.point(point))

    def project_native_points(
        self, points: Iterable[Sequence[float]], transformer: Any
    ) -> tuple[Point, ...]:
        return tuple(self.project_native(point, transformer) for point in points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_model": self.requested_model,
            "selected_model": self.selected_model,
            "source_crs": self.source_crs,
            "target_crs": self.target_crs,
            "parameters": self.parameters,
            "robust": {
                "enabled": self.robust_enabled,
                "iterations": self.robust_iterations,
            },
            "validation": {
                "passed": self.validation_passed,
                "failures": list(self.validation_failures),
            },
            "train_metrics": self.train_metrics.to_dict(),
            "check_metrics": self.check_metrics.to_dict(),
            "residuals": [item.to_dict() for item in self.residuals],
            "candidates": [item.to_dict() for item in self.candidates],
        }


@dataclass(frozen=True)
class _ProjectedControl:
    control: GroundControlPoint
    nominal_point: Point


def _weighted_centroid(points: Sequence[Point], weights: Sequence[float]) -> Point:
    total = sum(weights)
    if total <= 0.0 or not math.isfinite(total):
        raise ValueError("Fitting weights must have a finite positive sum")
    return (
        math.fsum(weight * point[0] for point, weight in zip(points, weights)) / total,
        math.fsum(weight * point[1] for point, weight in zip(points, weights)) / total,
    )


def _identity_transform(model: str = "disabled") -> ResidualTransform:
    return ResidualTransform(model, (0.0, 0.0), (0.0, 0.0), ((1.0, 0.0), (0.0, 1.0)))


def _validated_normalized_design_geometry(
    points: Sequence[Point],
    centered: Sequence[Point],
    weights: Sequence[float],
    model: str,
) -> tuple[list[Point], float]:
    """Return centred/RMS-normalised source geometry after numeric gates.

    Similarity uses its two-parameter rotation/scale design, which remains
    full-rank for two distinct locations.  Affine uses the two coordinate
    columns directly, so its design must span both axes and be well-conditioned.
    """

    if not (len(points) == len(centered) == len(weights)):
        raise ValueError("Control geometry and fitting weights must have equal lengths")
    active = [
        (point, offset, weight)
        for point, offset, weight in zip(points, centered, weights)
        if weight > 0.0
    ]
    minimum = THEORETICAL_MINIMUM_CONTROLS[model]
    if len(active) < minimum:
        raise ValueError(
            f"{model} calibration requires at least {minimum} positive-weight training controls"
        )
    if any(not math.isfinite(weight) for _, _, weight in active):
        raise ValueError("Fitting weights must be finite")

    total_weight = math.fsum(weight for _, _, weight in active)
    maximum_radius = max(math.hypot(offset[0], offset[1]) for _, offset, _ in active)
    if not math.isfinite(maximum_radius) or maximum_radius <= 0.0:
        raise ValueError(
            f"{model.capitalize()} controls are near-coincident in nominal delivery coordinates"
        )
    scaled_energy = math.fsum(
        weight
        * (
            (offset[0] / maximum_radius) ** 2
            + (offset[1] / maximum_radius) ** 2
        )
        for _, offset, weight in active
    )
    rms_spread = maximum_radius * math.sqrt(scaled_energy / total_weight)
    coordinate_ulp = max(
        math.ulp(coordinate)
        for point, _, _ in active
        for coordinate in point
    )
    minimum_spread = max(
        _DESIGN_MIN_RMS_SPREAD_M,
        _DESIGN_MIN_SPREAD_ULPS * coordinate_ulp,
    )
    if not math.isfinite(rms_spread) or rms_spread <= minimum_spread:
        raise ValueError(
            f"{model.capitalize()} controls are near-coincident: centered RMS spread "
            f"{rms_spread!r} m <= numeric floor {minimum_spread!r} m"
        )

    normalized = [
        (offset[0] / rms_spread, offset[1] / rms_spread)
        for offset in centered
    ]
    if model == "similarity":
        # Rows (x, -y) and (y, x) form the centred similarity design.
        # Its Gram matrix is energy * identity when the spread is non-zero.
        energy = math.fsum(
            weight * (point[0] * point[0] + point[1] * point[1])
            for point, weight in zip(normalized, weights)
            if weight > 0.0
        )
        design_xx, design_xy, design_yy = energy, 0.0, energy
    else:
        design_xx = math.fsum(
            weight * point[0] * point[0]
            for point, weight in zip(normalized, weights)
            if weight > 0.0
        )
        design_xy = math.fsum(
            weight * point[0] * point[1]
            for point, weight in zip(normalized, weights)
            if weight > 0.0
        )
        design_yy = math.fsum(
            weight * point[1] * point[1]
            for point, weight in zip(normalized, weights)
            if weight > 0.0
        )

    trace = design_xx + design_yy
    discriminant = math.hypot(design_xx - design_yy, 2.0 * design_xy)
    eigenvalue_max = (trace + discriminant) / 2.0
    determinant = design_xx * design_yy - design_xy * design_xy
    eigenvalue_min = (
        max(determinant / eigenvalue_max, 0.0)
        if eigenvalue_max > 0.0
        else 0.0
    )
    singular_max = math.sqrt(max(eigenvalue_max, 0.0))
    singular_min = math.sqrt(eigenvalue_min)
    if (
        singular_max <= 0.0
        or singular_min <= _DESIGN_RANK_RELATIVE_TOLERANCE * singular_max
    ):
        if model == "affine":
            raise ValueError(
                "Affine controls must contain at least three non-collinear locations "
                "(normalized design geometry is rank deficient)"
            )
        raise ValueError("Similarity control design geometry is rank deficient")
    condition_number = singular_max / singular_min
    if (
        not math.isfinite(condition_number)
        or condition_number > _DESIGN_MAX_CONDITION_NUMBER
    ):
        raise ValueError(
            f"{model.capitalize()} control geometry is ill-conditioned after centering "
            f"and scale normalization: design condition number {condition_number!r} > "
            f"{_DESIGN_MAX_CONDITION_NUMBER}"
        )
    return normalized, rms_spread


def _fit_transform(
    controls: Sequence[_ProjectedControl], model: str, weights: Sequence[float]
) -> ResidualTransform:
    if model == "disabled":
        return _identity_transform()
    minimum = THEORETICAL_MINIMUM_CONTROLS.get(model)
    if minimum is None:
        raise ValueError(f"Unsupported calibration model: {model}")
    if len(controls) < minimum:
        raise ValueError(f"{model} calibration requires at least {minimum} training controls")
    if len(weights) != len(controls):
        raise ValueError("Control geometry and fitting weights must have equal lengths")
    if any(not math.isfinite(weight) or weight < 0.0 for weight in weights):
        raise ValueError("Fitting weights must be finite and non-negative")
    maximum_weight = max(weights, default=0.0)
    if maximum_weight <= 0.0:
        raise ValueError("Fitting weights must contain a positive value")
    # A common weight factor does not change WLS.  Removing it prevents the
    # normalized design Gram matrix from inheriting an arbitrary weight scale.
    weights = tuple(weight / maximum_weight for weight in weights)
    nominal = [item.nominal_point for item in controls]
    target = [item.control.target_point for item in controls]
    nominal_origin = _weighted_centroid(nominal, weights)
    target_origin = _weighted_centroid(target, weights)
    if model == "translation":
        return ResidualTransform(
            model,
            nominal_origin,
            target_origin,
            ((1.0, 0.0), (0.0, 1.0)),
        )
    centered = [
        (point[0] - nominal_origin[0], point[1] - nominal_origin[1])
        for point in nominal
    ]
    target_centered = [
        (point[0] - target_origin[0], point[1] - target_origin[1])
        for point in target
    ]
    normalized_centered, design_scale = _validated_normalized_design_geometry(
        nominal, centered, weights, model
    )
    if model == "similarity":
        denominator = math.fsum(
            weight * (point[0] * point[0] + point[1] * point[1])
            for point, weight in zip(normalized_centered, weights)
        )
        a = math.fsum(
            weight * (point[0] * ground[0] + point[1] * ground[1])
            for point, ground, weight in zip(
                normalized_centered, target_centered, weights
            )
        ) / denominator / design_scale
        rotation_term = math.fsum(
            weight * (point[0] * ground[1] - point[1] * ground[0])
            for point, ground, weight in zip(
                normalized_centered, target_centered, weights
            )
        ) / denominator / design_scale
        if math.hypot(a, rotation_term) <= 1e-15:
            raise ValueError("Similarity calibration produced a degenerate scale")
        return ResidualTransform(
            model,
            nominal_origin,
            target_origin,
            ((a, -rotation_term), (rotation_term, a)),
        )
    sxx = math.fsum(
        weight * point[0] * point[0]
        for point, weight in zip(normalized_centered, weights)
    )
    sxy = math.fsum(
        weight * point[0] * point[1]
        for point, weight in zip(normalized_centered, weights)
    )
    syy = math.fsum(
        weight * point[1] * point[1]
        for point, weight in zip(normalized_centered, weights)
    )
    determinant = sxx * syy - sxy * sxy

    def solve(component: int) -> tuple[float, float]:
        rx = math.fsum(
            weight * point[0] * ground[component]
            for point, ground, weight in zip(
                normalized_centered, target_centered, weights
            )
        )
        ry = math.fsum(
            weight * point[1] * ground[component]
            for point, ground, weight in zip(
                normalized_centered, target_centered, weights
            )
        )
        return (
            (rx * syy - ry * sxy) / determinant / design_scale,
            (ry * sxx - rx * sxy) / determinant / design_scale,
        )

    row_e = solve(0)
    row_n = solve(1)
    matrix_determinant = row_e[0] * row_n[1] - row_e[1] * row_n[0]
    if matrix_determinant <= 1e-15:
        raise ValueError("Affine calibration produced a reflection or singular transform")
    return ResidualTransform(model, nominal_origin, target_origin, (row_e, row_n))


def _errors(
    transform: ResidualTransform,
    controls: Sequence[_ProjectedControl],
    target_axis_to_m: float = 1.0,
) -> list[float]:
    result = []
    for item in controls:
        error = target_axis_to_m * math.dist(
            transform.point(item.nominal_point), item.control.target_point,
        )
        result.append(_finite_float(error, f"GCP {item.control.point_id} residual error"))
    return result


def _fit_with_optional_robustness(
    controls: Sequence[_ProjectedControl],
    model: str,
    robust: RobustSettings,
    target_axis_to_m: float = 1.0,
) -> tuple[ResidualTransform, list[float], int]:
    base_weights = [item.control.fitting_weight for item in controls]
    if model == "disabled" or not robust.enabled:
        return _fit_transform(controls, model, base_weights), base_weights, 0
    if robust.outlier_threshold_m is None:
        raise ValueError("Robust fitting requires outlier_threshold_m")
    factors = [1.0] * len(controls)
    iterations = 0
    irls_converged = False
    for iteration in range(1, robust.max_iterations + 1):
        weights = [base * factor for base, factor in zip(base_weights, factors)]
        transform = _fit_transform(controls, model, weights)
        errors = _errors(transform, controls, target_axis_to_m)
        threshold = robust.outlier_threshold_m
        next_factors = [1.0 if error <= threshold else threshold / error for error in errors]
        iterations = iteration
        difference = max(
            (abs(current - updated) for current, updated in zip(factors, next_factors)),
            default=0.0,
        )
        factors = next_factors
        if difference <= 1e-10:
            irls_converged = True
            break
    if not irls_converged:
        raise ValueError(
            f"Robust {model} IRLS did not converge within {robust.max_iterations} iterations"
        )
    # IRLS is used only to seed a deterministic hard classification.  Hard
    # inlier WLS is then iterated until the classification itself is stable;
    # otherwise an accepted point could exceed the threshold after refitting.
    irls_weights = [base * factor for base, factor in zip(base_weights, factors)]
    irls_transform = _fit_transform(controls, model, irls_weights)
    accepted = tuple(
        error <= robust.outlier_threshold_m
        for error in _errors(irls_transform, controls)
    )
    minimum = THEORETICAL_MINIMUM_CONTROLS[model]
    seen: set[tuple[bool, ...]] = set()
    for hard_iteration in range(1, robust.max_iterations + 1):
        if accepted in seen:
            raise ValueError(f"Robust {model} hard-inlier classification entered a cycle")
        seen.add(accepted)
        accepted_count = sum(accepted)
        if accepted_count < minimum:
            raise ValueError(
                f"Robust {model} calibration retained {accepted_count} controls; "
                f"at least {minimum} are required"
            )
        final_weights = [
            base if is_accepted else 0.0
            for base, is_accepted in zip(base_weights, accepted)
        ]
        final_transform = _fit_transform(controls, model, final_weights)
        final_errors = _errors(final_transform, controls)
        updated = tuple(error <= robust.outlier_threshold_m for error in final_errors)
        if updated == accepted:
            if any(
                is_accepted != (error <= robust.outlier_threshold_m)
                for is_accepted, error in zip(accepted, final_errors)
            ):
                raise AssertionError("Robust inlier classification is inconsistent")
            return final_transform, final_weights, iterations + hard_iteration
        accepted = updated
    raise ValueError(f"Robust {model} hard-inlier classification did not converge")


def _percentile_95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = 0.95 * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _metrics(residuals: Sequence[PointResidual]) -> ResidualMetrics:
    errors = [
        _finite_float(item.error_m, f"GCP {item.point_id} metric error")
        for item in residuals
    ]
    if not errors:
        return ResidualMetrics(0, None, None, None)
    return ResidualMetrics(
        count=len(errors),
        rmse_m=math.sqrt(math.fsum(error * error for error in errors) / len(errors)),
        p95_m=_percentile_95(errors),
        max_m=max(errors),
    )


def _validation_failures(
    metrics: ResidualMetrics, validation: ValidationSettings
) -> tuple[str, ...]:
    failures: list[str] = []
    if metrics.count < validation.min_check_points:
        failures.append(
            f"check_count {metrics.count} < required {validation.min_check_points}"
        )
    gates = (
        ("check_rmse_m", metrics.rmse_m, validation.max_check_rmse_m),
        ("check_p95_m", metrics.p95_m, validation.max_check_p95_m),
        ("check_error_m", metrics.max_m, validation.max_check_error_m),
    )
    for name, observed, threshold in gates:
        if observed is not None and not math.isfinite(observed):
            failures.append(f"{name} is not finite")
        elif threshold is not None and (observed is None or observed > threshold):
            failures.append(f"{name} {observed!r} > allowed {threshold}")
    return tuple(failures)


def _transform_limit_failures(
    transform: ResidualTransform,
    limits: TransformLimitsSettings,
) -> tuple[str, ...]:
    if transform.model == "disabled":
        return ()
    failures: list[str] = []
    pivot_shift = _finite_float(
        math.dist(transform.nominal_origin, transform.adjusted_origin),
        "transform pivot shift",
    )
    if limits.max_pivot_shift_m is not None and pivot_shift > limits.max_pivot_shift_m:
        failures.append(
            f"pivot_shift_m {pivot_shift} > allowed {limits.max_pivot_shift_m}"
        )
    if transform.model in {"similarity", "affine"}:
        diagnostics = _matrix_diagnostics(transform.matrix)
        absolute_rotation = abs(diagnostics["rotation_deg"])
        if (
            limits.max_abs_rotation_deg is not None
            and absolute_rotation > limits.max_abs_rotation_deg
        ):
            failures.append(
                f"abs_rotation_deg {absolute_rotation} > allowed "
                f"{limits.max_abs_rotation_deg}"
            )
        scale_deviation = diagnostics["max_scale_deviation_ratio"]
        if (
            limits.max_scale_deviation_ratio is not None
            and scale_deviation > limits.max_scale_deviation_ratio
        ):
            failures.append(
                f"max_scale_deviation_ratio {scale_deviation} > allowed "
                f"{limits.max_scale_deviation_ratio}"
            )
        if transform.model == "affine":
            condition = diagnostics["condition_number"]
            if (
                limits.max_affine_condition_number is not None
                and condition > limits.max_affine_condition_number
            ):
                failures.append(
                    f"affine_condition_number {condition} > allowed "
                    f"{limits.max_affine_condition_number}"
                )
    return tuple(failures)


def fit_calibration(
    controls: Sequence[GroundControlPoint],
    transformer: Any,
    *,
    model: str,
    robust: RobustSettings | None = None,
    validation: ValidationSettings | None = None,
    transform_limits: TransformLimitsSettings | None = None,
    expected_source_crs: str | None = None,
    expected_target_crs: str = "EPSG:9481",
) -> CalibrationResult:
    """Fit one explicit residual model using train controls only.

    Check controls are never included in the fit.  The returned transform maps
    nominal delivery coordinates to adjusted delivery coordinates.
    """

    model = str(model).lower()
    if model not in SUPPORTED_MODELS:
        raise ValueError(f"Unsupported calibration model: {model}")
    source_crs = str(getattr(transformer, "source_crs", ""))
    target_crs = str(getattr(transformer, "target_crs", ""))
    if not source_crs or not target_crs or not callable(getattr(transformer, "point", None)):
        raise TypeError("transformer must expose source_crs, target_crs, and point()")
    target_axis_to_m = _validate_transformer_unit_contract(
        transformer, source_crs, target_crs,
    )
    if expected_source_crs is not None and not _crs_equal(source_crs, expected_source_crs):
        raise ValueError("Transformer source CRS does not match the GCP profile")
    if not _crs_equal(target_crs, expected_target_crs):
        raise ValueError("Transformer target CRS does not match the GCP target CRS")
    active = tuple(control for control in controls if control.enabled)
    point_ids = [control.point_id for control in active]
    if len(point_ids) != len(set(point_ids)):
        raise ValueError("Active GCP point_id values must be unique")
    _reject_duplicate_control_coordinates(active)
    for control in active:
        if not _crs_equal(control.target_crs, expected_target_crs):
            raise ValueError(f"GCP {control.point_id} target CRS does not match calibration target")
    projected_items: list[_ProjectedControl] = []
    for control in active:
        nominal = transformer.point(control.cad_point)
        if len(nominal) < 2:
            raise ValueError(f"GCP {control.point_id}: transformer.point() must return X and Y")
        projected_items.append(
            _ProjectedControl(
                control,
                (
                    _finite_float(nominal[0], f"GCP {control.point_id} nominal easting"),
                    _finite_float(nominal[1], f"GCP {control.point_id} nominal northing"),
                ),
            )
        )
    projected = tuple(projected_items)
    training = tuple(item for item in projected if item.control.role == "train")
    robust = robust or RobustSettings(False, 1, None)
    transform, effective_training_weights, iterations = _fit_with_optional_robustness(
        training, model, robust, target_axis_to_m,
    )
    effective_by_id = {
        item.control.point_id: weight
        for item, weight in zip(training, effective_training_weights)
    }
    residuals: list[PointResidual] = []
    threshold = robust.outlier_threshold_m if robust.enabled else None
    for item in projected:
        control = item.control
        adjusted = transform.point(item.nominal_point)
        residual_e = _finite_float(
            target_axis_to_m * (adjusted[0] - control.target_point[0]),
            f"GCP {control.point_id} easting residual",
        )
        residual_n = _finite_float(
            target_axis_to_m * (adjusted[1] - control.target_point[1]),
            f"GCP {control.point_id} northing residual",
        )
        error = _finite_float(
            math.hypot(residual_e, residual_n),
            f"GCP {control.point_id} residual error",
        )
        nominal_error = _finite_float(
            target_axis_to_m * math.dist(item.nominal_point, control.target_point),
            f"GCP {control.point_id} nominal error",
        )
        is_training = control.role == "train"
        effective_weight = effective_by_id.get(control.point_id, 0.0)
        residuals.append(
            PointResidual(
                point_id=control.point_id,
                role=control.role,
                source=control.source,
                cad_point=control.cad_point,
                nominal_point=item.nominal_point,
                target_point=control.target_point,
                adjusted_point=adjusted,
                nominal_error_m=nominal_error,
                residual_e_m=residual_e,
                residual_n_m=residual_n,
                error_m=error,
                accuracy_m=control.accuracy_m,
                reviewed_weight=control.weight,
                fitting_weight=control.fitting_weight,
                effective_weight=effective_weight,
                inlier=(
                    None
                    if not is_training
                    else True if threshold is None else effective_weight > 0.0
                ),
            )
        )
    train_residuals = tuple(item for item in residuals if item.role == "train")
    check_residuals = tuple(item for item in residuals if item.role == "check")
    train_metrics = _metrics(train_residuals)
    check_metrics = _metrics(check_residuals)
    failures: tuple[str, ...] = ()
    if transform_limits is not None:
        failures += _transform_limit_failures(transform, transform_limits)
    if validation is not None:
        failures += _validation_failures(check_metrics, validation)
    validation_evaluated = validation is not None or transform_limits is not None
    return CalibrationResult(
        requested_model=model,
        selected_model=model,
        source_crs=source_crs,
        target_crs=target_crs,
        transform=transform,
        residuals=tuple(residuals),
        train_metrics=train_metrics,
        check_metrics=check_metrics,
        robust_enabled=robust.enabled and model != "disabled",
        robust_iterations=iterations,
        validation_passed=None if not validation_evaluated else not failures,
        validation_failures=failures,
    )


def _summary(result: CalibrationResult, failures: tuple[str, ...] | None = None) -> CandidateSummary:
    actual_failures = result.validation_failures if failures is None else failures
    return CandidateSummary(
        model=result.selected_model,
        available=True,
        validation_passed=not actual_failures,
        train_metrics=result.train_metrics,
        check_metrics=result.check_metrics,
        failures=actual_failures,
    )


def _improvement_ratio(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None:
        return None
    if baseline <= 1e-15:
        return 0.0 if candidate <= baseline + 1e-15 else -math.inf
    return (baseline - candidate) / baseline


def _affine_gate_failures(
    affine: CalibrationResult,
    similarity: CalibrationResult | None,
    profile: GCPProfile,
) -> tuple[str, ...]:
    failures = list(affine.validation_failures)
    minimum = profile.validation.affine_min_improvement_ratio
    if not profile.model_selection.require_spatially_structured_similarity_residuals:
        failures.append("affine spatial-structure gate is disabled")
    elif not profile.model_selection.spatial_structure_reviewed:
        failures.append(
            "affine spatial structure has not been explicitly reviewed"
        )
    check_sets_match = False
    if similarity is None:
        failures.append(
            "affine gate lacks a similarity baseline for check point-id confirmation"
        )
    else:
        similarity_check_ids = frozenset(
            residual.point_id
            for residual in similarity.residuals
            if residual.role == "check"
        )
        affine_check_ids = frozenset(
            residual.point_id
            for residual in affine.residuals
            if residual.role == "check"
        )
        check_sets_match = similarity_check_ids == affine_check_ids
        if not check_sets_match:
            failures.append(
                "affine and similarity must use the same check point-id set; "
                f"similarity={sorted(similarity_check_ids)!r}, "
                f"affine={sorted(affine_check_ids)!r}"
            )
    if not profile.model_selection.require_holdout_improvement:
        failures.append("affine independent-holdout improvement gate is disabled")
    if similarity is None or minimum is None or not 0.0 < minimum < 1.0:
        failures.append(
            "affine gate lacks a positive similarity holdout improvement threshold"
        )
    elif check_sets_match:
        check_ids = {
            residual.point_id
            for residual in affine.residuals
            if residual.role == "check"
        }
        if len(check_ids) < 3:
            failures.append(
                "affine promotion requires at least 3 independent check points"
            )
        check_metrics = (
            ("RMSE", similarity.check_metrics.rmse_m, affine.check_metrics.rmse_m),
            ("p95", similarity.check_metrics.p95_m, affine.check_metrics.p95_m),
            ("max", similarity.check_metrics.max_m, affine.check_metrics.max_m),
        )
        for name, baseline, candidate in check_metrics:
            check_improvement = _improvement_ratio(baseline, candidate)
            if check_improvement is None or check_improvement < minimum:
                failures.append(
                    f"affine check {name} improvement {check_improvement!r} "
                    f"< required {minimum}"
                )
    return tuple(failures)


def fit_profile(
    profile: GCPProfile,
    transformer: Any,
    *,
    candidate_validation: Callable[[CalibrationResult], Sequence[str]] | None = None,
) -> CalibrationResult:
    """Fit a reviewed profile and apply deterministic model selection.

    ``enabled=false`` produces an auditable identity result.  Production
    callers may simply skip this function for a disabled profile.  A fitted
    result can intentionally carry ``validation_passed=False`` so its evidence
    remains inspectable; delivery callers must fail closed unless that value is
    exactly ``True`` for an enabled profile.  ``candidate_validation`` lets a
    delivery orchestrator contribute deterministic post-fit gates (for example,
    retained-inlier spatial coverage) before auto selection is finalized.
    """

    profile._validate_activation()
    profile.validate_transformer(transformer)
    if not profile.enabled:
        return fit_calibration(
            profile.active_controls,
            transformer,
            model="disabled",
            expected_source_crs=profile.source_crs,
            expected_target_crs=profile.target_crs,
        )
    active = profile.active_controls
    train_count = sum(control.role == "train" for control in active)
    minimums = profile.model_selection.minimums

    def fit(model: str) -> CalibrationResult:
        result = fit_calibration(
            active,
            transformer,
            model=model,
            robust=profile.robust,
            validation=profile.validation,
            transform_limits=profile.transform_limits,
            expected_source_crs=profile.source_crs,
            expected_target_crs=profile.target_crs,
        )
        accepted_count = sum(
            residual.role == "train" and residual.inlier is True
            for residual in result.residuals
        )
        required_count = minimums[model]
        if accepted_count < required_count:
            failures = result.validation_failures + (
                f"accepted_train_count {accepted_count} < reviewed minimum {required_count} for {model}",
            )
            result = replace(
                result,
                validation_passed=False,
                validation_failures=failures,
            )
        return result

    def apply_candidate_validation(result: CalibrationResult) -> CalibrationResult:
        if candidate_validation is None:
            return result
        # This call intentionally sits outside the auto-mode fit exception
        # handler.  A gate implementation error must abort selection instead
        # of being mistaken for one unavailable model.
        raw_failures = candidate_validation(result)
        if isinstance(raw_failures, (str, bytes)):
            raise TypeError(
                "candidate_validation must return a sequence of failure strings"
            )
        try:
            returned_failures = tuple(raw_failures)
        except TypeError as exc:
            raise TypeError(
                "candidate_validation must return a sequence of failure strings"
            ) from exc
        external_failures: list[str] = []
        for failure in returned_failures:
            if not isinstance(failure, str) or not failure.strip():
                raise TypeError(
                    "candidate_validation must return non-empty failure strings"
                )
            external_failures.append(failure.strip())
        if not external_failures:
            return result
        failures = result.validation_failures + tuple(external_failures)
        return replace(
            result,
            validation_passed=False,
            validation_failures=failures,
        )

    if profile.requested_model != "auto":
        model = profile.requested_model
        if train_count < minimums[model]:
            raise ValueError(
                f"{model} requires {minimums[model]} reviewed training controls in this profile"
            )
        result = apply_candidate_validation(fit(model))
        candidates: list[CandidateSummary] = []
        if model == "affine":
            similarity = (
                apply_candidate_validation(fit("similarity"))
                if train_count >= minimums["similarity"]
                else None
            )
            failures = _affine_gate_failures(result, similarity, profile)
            result = replace(
                result,
                validation_passed=not failures,
                validation_failures=failures,
            )
            if similarity is not None:
                candidates.append(_summary(similarity))
        candidates.append(_summary(result))
        return replace(result, requested_model=model, candidates=tuple(candidates))

    fitted: dict[str, CalibrationResult] = {}
    summaries: list[CandidateSummary] = []
    selected: CalibrationResult | None = None
    for model in profile.model_selection.candidate_order:
        if train_count < minimums[model]:
            summaries.append(
                CandidateSummary(
                    model=model,
                    available=False,
                    validation_passed=False,
                    train_metrics=None,
                    check_metrics=None,
                    failures=(f"train_count {train_count} < required {minimums[model]}",),
                )
            )
            continue
        try:
            candidate = fit(model)
        except ValueError as exc:
            summaries.append(
                CandidateSummary(model, False, False, None, None, (str(exc),))
            )
            continue
        candidate = apply_candidate_validation(candidate)
        fitted[model] = candidate
        failures = candidate.validation_failures
        if model == "affine":
            failures = _affine_gate_failures(candidate, fitted.get("similarity"), profile)
            candidate = replace(
                candidate,
                validation_passed=not failures,
                validation_failures=failures,
            )
            fitted[model] = candidate
        summaries.append(_summary(candidate))
        if selected is None and candidate.validation_passed:
            selected = candidate
    if selected is None:
        raise ValueError("No calibration model passed independent validation")
    return replace(selected, requested_model="auto", candidates=tuple(summaries))


__all__ = [
    "CalibrationResult",
    "CandidateSummary",
    "ControlSchema",
    "GCPProfile",
    "GroundControlPoint",
    "ModelSelectionSettings",
    "PointResidual",
    "ResidualMetrics",
    "ResidualTransform",
    "RobustSettings",
    "TransformLimitsSettings",
    "ValidationSettings",
    "fit_calibration",
    "fit_profile",
]
