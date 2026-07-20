"""Fail-closed public claim language for verification-matrix reports.

The strings in this module are deliberately conservative.  In particular,
"cross-CAD" means that verification completed for at least two distinct,
content-verified input SHA-256 values.  Repeated paths or copies of one drawing
never increase that scope.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


CORE_FIDELITY_DIMENSIONS = (
    "geometry",
    "topology",
    "semantics",
    "style",
    "length",
)

CLAIM_INVENTORY_ONLY = (
    "Inventory only: no conversion-quality or accuracy claim is supported."
)
CLAIM_DIMENSION_ONLY = (
    "Dimension-level results only: no end-to-end source-fidelity or accuracy "
    "claim is supported."
)
CLAIM_SINGLE_FIDELITY = (
    "Single-input source-fidelity verified; cross-CAD generalisation and "
    "absolute accuracy are not established."
)
CLAIM_SINGLE_NOMINAL_CRS = (
    "Single-input source-fidelity and nominal CRS transform verified; "
    "cross-CAD generalisation and absolute accuracy are not established."
)
CLAIM_SINGLE_ABSOLUTE = (
    "Single-input survey-validated absolute accuracy verified; no cross-CAD "
    "generalisation claim is supported."
)
CLAIM_CROSS_FIDELITY = (
    "Cross-CAD source-fidelity verified across distinct input hashes; absolute "
    "accuracy is not established."
)
CLAIM_CROSS_NOMINAL_CRS = (
    "Cross-CAD source-fidelity and nominal CRS transform verified across "
    "distinct input hashes; absolute accuracy is not established."
)
CLAIM_CROSS_ABSOLUTE = (
    "Cross-CAD survey-validated absolute accuracy verified across distinct "
    "input hashes."
)


def _status(value: Any) -> str:
    """Return a normalized status from a report value, or ``FAIL``.

    ``evaluate_matrix`` emits strings, but accepting ``{"status": ...}``
    keeps this helper useful for callers that retain dimension details.
    Anything unknown fails closed.
    """

    if isinstance(value, Mapping):
        value = value.get("status")
    if isinstance(value, str):
        normalized = value.upper()
        if normalized in {"PASS", "WATCH", "FAIL"}:
            return normalized
    return "FAIL"


def _eligible_samples(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    samples = report.get("samples", [])
    if not isinstance(samples, list):
        return []
    eligible: list[Mapping[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, Mapping):
            continue
        if sample.get("inventory_only") is True:
            continue
        # Inventory/unreviewed rows are intentionally retained in the matrix
        # for onboarding, but they must never contribute to a quality claim.
        row_status = sample.get("status")
        if isinstance(row_status, str) and row_status.strip().upper() in {
            "INVENTORY",
            "INVENTORY_ONLY",
            "UNREVIEWED",
            "NOT_EVALUATED",
        }:
            continue
        if sample.get("evaluated") is not True:
            continue
        if sample.get("input_verified") is not True:
            continue
        sha256 = sample.get("input_sha256")
        if not isinstance(sha256, str) or len(sha256) != 64:
            continue
        eligible.append(sample)
    return eligible


def _all_pass(samples: list[Mapping[str, Any]], dimensions: tuple[str, ...]) -> bool:
    if not samples:
        return False
    for sample in samples:
        values = sample.get("dimensions")
        if not isinstance(values, Mapping):
            return False
        if any(_status(values.get(name)) != "PASS" for name in dimensions):
            return False
    return True


def strongest_allowed_claim(report: Mapping[str, Any]) -> str:
    """Return the strongest claim justified by an evaluated matrix report.

    The function intentionally recomputes scope from sample-level results.  It
    does not trust a caller-supplied summary count or claim string, which keeps
    duplicate copies of one source drawing from becoming a cross-CAD claim.
    """

    samples = _eligible_samples(report)
    if not samples:
        return CLAIM_INVENTORY_ONLY

    unique_hashes = {str(sample["input_sha256"]).lower() for sample in samples}
    cross_cad = len(unique_hashes) >= 2
    core_pass = _all_pass(samples, CORE_FIDELITY_DIMENSIONS)
    nominal_crs_pass = _all_pass(samples, ("nominal_crs",))
    absolute_pass = _all_pass(samples, ("absolute_accuracy",))

    # Absolute status has already passed the surveyed, independent check-GCP
    # gate in evaluate_matrix.  Requiring geometry, length and nominal CRS here
    # prevents a positional claim from surviving an incomplete spatial result.
    spatial_pass = _all_pass(
        samples, ("geometry", "length", "nominal_crs", "absolute_accuracy")
    )
    if spatial_pass:
        return CLAIM_CROSS_ABSOLUTE if cross_cad else CLAIM_SINGLE_ABSOLUTE
    if core_pass and nominal_crs_pass:
        return CLAIM_CROSS_NOMINAL_CRS if cross_cad else CLAIM_SINGLE_NOMINAL_CRS
    if core_pass:
        return CLAIM_CROSS_FIDELITY if cross_cad else CLAIM_SINGLE_FIDELITY
    if absolute_pass:
        # This is defensive: malformed external reports must not obtain an
        # absolute claim without the prerequisite spatial dimensions.
        return CLAIM_DIMENSION_ONLY
    return CLAIM_DIMENSION_ONLY


__all__ = [
    "CLAIM_CROSS_ABSOLUTE",
    "CLAIM_CROSS_FIDELITY",
    "CLAIM_CROSS_NOMINAL_CRS",
    "CLAIM_DIMENSION_ONLY",
    "CLAIM_INVENTORY_ONLY",
    "CLAIM_SINGLE_ABSOLUTE",
    "CLAIM_SINGLE_FIDELITY",
    "CLAIM_SINGLE_NOMINAL_CRS",
    "CORE_FIDELITY_DIMENSIONS",
    "strongest_allowed_claim",
]
