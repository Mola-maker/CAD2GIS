"""Deterministic, fail-closed verification matrix evaluation.

The verification matrix is deliberately a *record of evidence*, rather than a
second converter or a score inferred from the output GeoPackage.  A matrix may
contain one or more run manifests.  :func:`evaluate_matrix` reads that record
without modifying it and derives the small set of claims that the evidence
supports.

The evaluator accepts both the current ``cad2gis-verification-matrix-v1``
shape and a run manifest (``cad2gis-run-manifest-*``).  This is useful during
onboarding: an operator can run the checker before building a curated matrix.
Unknown or missing evidence is never promoted to ``PASS``.  In particular,
absolute accuracy requires reviewed surveyed check GCPs and a single source
hash can never establish cross-CAD generalisation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .claims import strongest_allowed_claim


MATRIX_SCHEMA_VERSION = "cad2gis-verification-matrix-v1"
REPORT_SCHEMA_VERSION = "cad2gis-verification-report-v1"
SUPPORTED_MATRIX_SCHEMAS = frozenset(
    {
        MATRIX_SCHEMA_VERSION,
        "cad2gis-verification-matrix-v0",
        "cad2gis-verification-matrix-v2",
    }
)

# Keep these names stable.  Downstream gates and the public claims helper use
# the same keys, while the report also contains a compact overall status.
DIMENSIONS = (
    "geometry",
    "topology",
    "semantics",
    "style",
    "length",
    "nominal_crs",
    "absolute_accuracy",
)
STATUSES = frozenset({"PASS", "WATCH", "FAIL"})
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class VerificationMatrixError(ValueError):
    """Raised when a matrix cannot be parsed as a JSON evidence record."""


def _status(value: Any) -> str | None:
    """Normalize a status-like value without treating arbitrary truth as pass."""

    if isinstance(value, Mapping):
        if "status" in value:
            return _status(value.get("status"))
        if "passed" in value:
            return _status(value.get("passed"))
        if "verified" in value:
            return _status(value.get("verified"))
        return None
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "PASS" if value == 1 else "FAIL" if value == 0 else None
    if isinstance(value, str):
        token = value.strip().upper().replace("-", "_").replace(" ", "_")
        if token in STATUSES:
            return token
        if token in {"TRUE", "YES", "VERIFIED", "PASSED", "COMPLETE", "COMPLETED"}:
            return "PASS"
        if token in {"FALSE", "NO", "FAILED", "ERROR", "INVALID"}:
            return "FAIL"
        if token in {"UNKNOWN", "UNVERIFIED", "NOT_VERIFIED", "PENDING", "PARTIAL"}:
            return "WATCH"
    return None


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(_HASH_RE.fullmatch(value.strip()))


def _as_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return max(0, int(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    if isinstance(value, Mapping):
        for key in ("count", "n", "total", "size", "feature_count"):
            if key in value:
                count = _as_count(value[key])
                if count is not None:
                    return count
    return None


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _copy_json(value: Any) -> Any:
    """Copy input data so an evaluation can never mutate the caller's object."""

    try:
        return deepcopy(value)
    except Exception:  # pragma: no cover - exotic custom Mapping fallback
        return value


def _source_record(sample: Mapping[str, Any]) -> dict[str, Any]:
    source = _dict(_first(sample, "source", "input", "drawing", default={}))
    profiles = _dict(sample.get("profiles"))
    source_profile = _dict(
        _first(sample, "source_profile", default=profiles.get("source_profile", {}))
    )

    source_sha = _first(
        sample,
        "input_sha256",
        "source_sha256",
        "source_hash",
        "sha256",
        default=_first(source, "sha256", "hash", "source_sha256"),
    )
    source_path = _first(sample, "source_path", "input_path", default=_first(source, "path"))
    units = _first(
        sample,
        "units",
        "source_units",
        default=_first(source, "units", "unit", "insunits", "drawing_units"),
    )
    if units is None:
        units = _first(source_profile, "units", "source_units", "dwg_insunits", "insunits")
    crs = _first(
        sample,
        "source_crs",
        default=_first(source, "crs", "source_crs"),
    )
    if crs is None:
        crs = _dict(sample.get("crs")).get("source_crs")
    version = _first(
        sample,
        "source_version",
        "drawing_version",
        default=_first(source, "version", "schema_version", "release"),
    )
    if version is None:
        version = _first(sample, "version", "pipeline_version", default=sample.get("schema_version"))
    vendor = _first(
        sample,
        "vendor",
        "cad_vendor",
        default=_first(source, "vendor", "cad_vendor", "application"),
    )
    if vendor is None:
        runtime = _dict(sample.get("runtime"))
        vendor = _first(runtime, "cad_vendor", "vendor", "application")

    result: dict[str, Any] = {
        "path": source_path,
        "sha256": source_sha.strip().lower() if isinstance(source_sha, str) else source_sha,
        "version": version,
        "vendor": vendor,
        "units": units,
        "crs": crs,
    }
    # Preserve optional CAD identity facts required for audit/reproducibility.
    for key in ("format", "layout", "entity_count", "modified_at"):
        if key in source and key not in result:
            result[key] = _copy_json(source[key])
    return result


def _find_mapping(sample: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = sample.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    for container_key in ("validation", "evidence", "metrics", "quality", "artifacts"):
        container = sample.get(container_key)
        if isinstance(container, Mapping):
            for key in keys:
                value = container.get(key)
                if isinstance(value, Mapping):
                    return dict(value)
    return {}


def _find_value(sample: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in sample:
            return sample[key]
    for container_key in ("validation", "evidence", "metrics", "quality", "artifacts"):
        container = sample.get(container_key)
        if isinstance(container, Mapping):
            for key in keys:
                if key in container:
                    return container[key]
    return None


def _is_inventory(sample: Mapping[str, Any]) -> bool:
    if sample.get("inventory_only") is True:
        return True
    for key in ("status", "evaluation_status", "scope", "claim_scope", "mode"):
        value = sample.get(key)
        if isinstance(value, str) and value.strip().lower() in {
            "inventory",
            "inventory_only",
            "inventory-only",
            "unreviewed",
            "not_evaluated",
            "not-evaluated",
        }:
            return True
    # A named APD/AGA/demo row without evaluation evidence is an inventory row,
    # not a precision benchmark.  Explicit ``evaluated=true`` always wins.
    if sample.get("evaluated") is not True:
        identity = " ".join(
            str(sample.get(key, "")) for key in ("sample_id", "project_id", "project", "name")
        ).strip().lower()
        if identity in {"apd", "aga", "demo"} or identity.startswith(("apd ", "aga ", "demo ")):
            return True
    return False


def _source_hash_verified(sample: Mapping[str, Any], source: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    digest = source.get("sha256")
    if not _valid_hash(digest):
        reasons.append("source SHA-256 is missing or malformed")
        return False, reasons

    # An explicit content-verification flag is accepted only when it is true;
    # false is authoritative.  A path, when supplied and readable, is checked
    # directly so stale manifests cannot silently pass.
    if sample.get("input_verified") is False or source.get("hash_verified") is False or source.get("content_verified") is False:
        reasons.append("source SHA-256 is explicitly unverified")
        return False, reasons
    if (
        sample.get("input_verified") is True
        or sample.get("content_verified") is True
        or source.get("hash_verified") is True
        or source.get("content_verified") is True
    ):
        return True, reasons

    path = source.get("path")
    if isinstance(path, str) and path:
        try:
            file_path = Path(path)
            if file_path.is_file():
                actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
                if actual.lower() == str(digest).lower():
                    return True, reasons
                reasons.append("source SHA-256 does not match the source file")
                return False, reasons
            reasons.append("source path is not a readable file")
            return False, reasons
        except OSError as exc:
            reasons.append(f"source file hash could not be checked: {exc.__class__.__name__}")
            return False, reasons
    reasons.append("content verification flag/path is absent")
    return False, reasons


def _normalise_dimension_evidence(sample: Mapping[str, Any], dimension: str) -> tuple[str, list[str]]:
    """Derive one dimension without trusting a bare self-reported PASS."""

    if dimension == "absolute_accuracy":
        return _absolute_accuracy(sample)

    aliases = {
        "geometry": ("geometry", "source_geometry", "geometry_fidelity", "source_geometry_fidelity"),
        "topology": ("topology", "topology_fidelity", "network_topology"),
        "semantics": ("semantics", "semantic_coverage", "semantic_fidelity", "classification", "mapping", "mapping_coverage"),
        "style": ("style", "styles", "style_fidelity", "style_manifest", "style_coverage"),
        "length": ("length", "measurements", "segment_delivery", "span_metrics", "length_fidelity"),
        "nominal_crs": ("nominal_crs", "crs", "coordinate_accuracy", "georeference", "georef"),
    }
    evidence: list[Any] = []
    explicit_dimensions = sample.get("dimensions")
    if isinstance(explicit_dimensions, Mapping) and dimension in explicit_dimensions:
        evidence.append(explicit_dimensions[dimension])
    for alias in aliases[dimension]:
        value = _find_value(sample, alias)
        if value is not None:
            evidence.append(value)
    statuses = [_status(value) for value in evidence]
    statuses = [value for value in statuses if value]
    reasons: list[str] = []

    # Explicit FAIL is always honoured.  A conflicting PASS cannot hide a
    # failing independent validation object.
    if "FAIL" in statuses:
        reasons.append(f"{dimension} evidence contains a failing check")
        return "FAIL", reasons

    supporting = False
    # A nested dimension record is only supporting when it contains an
    # evidence marker in addition to its status.  A bare ``PASS`` is a claim,
    # not an independent check.
    if isinstance(explicit_dimensions, Mapping):
        explicit = explicit_dimensions.get(dimension)
        if isinstance(explicit, Mapping) and any(
            key in explicit for key in ("evidence", "evidence_ref", "verified_by", "check_id")
        ):
            supporting = _status(explicit) == "PASS"
    if dimension == "nominal_crs":
        crs_value = _dict(sample.get("crs"))
        source_crs = _first(sample, "source_crs", default=crs_value.get("source_crs"))
        target_crs = _first(sample, "target_crs", default=crs_value.get("target_crs"))
        operation = _first(sample, "coordinate_operation", default=crs_value.get("coordinate_operation"))
        operation_text = str(_first(crs_value, "operation", "description", default=operation or "")).lower()
        if source_crs and target_crs and operation_text and not any(
            token in operation_text for token in ("disabled", "unknown", "guess", "unverified")
        ):
            supporting = True
            if not statuses:
                statuses.append("PASS")
    for alias in aliases[dimension]:
        candidate = _find_mapping(sample, alias)
        if not candidate:
            continue
        if candidate.get("passed") is True or candidate.get("verified") is True:
            supporting = True
        if _status(candidate.get("status")) == "PASS":
            supporting = True
        # Metrics that are specifically marked immutable/closure-checked are
        # independent evidence for source geometry and length.
        if dimension == "geometry" and candidate.get("source_geometry_immutable") is True:
            supporting = True
        if dimension == "length" and any(
            candidate.get(key) is True
            for key in ("schema_passed", "unit_passed", "index_passed", "closure_passed", "total_length_closure_passed")
        ):
            supporting = True

    # A known unsupported/unknown count downgrades an otherwise optimistic
    # explicit status.  Do not turn a row into FAIL merely because evidence is
    # incomplete; FAIL is reserved for a stated failed check or a hard gate.
    unsupported = _unsupported_count(sample, dimension)
    if unsupported:
        reasons.append(f"{unsupported} unsupported/unmatched {dimension} item(s)")
        if statuses and all(value == "PASS" for value in statuses):
            return "WATCH", reasons

    if statuses and all(value == "PASS" for value in statuses) and supporting:
        return "PASS", reasons
    if "WATCH" in statuses or statuses or supporting:
        if not supporting:
            reasons.append(f"{dimension} has no independent supporting evidence")
        return "WATCH", reasons
    reasons.append(f"{dimension} evidence is absent")
    return "WATCH", reasons


def _unsupported_count(sample: Mapping[str, Any], dimension: str) -> int | None:
    names = {
        "geometry": ("unsupported_geometry_count", "unmaterialized_curve_count"),
        "topology": ("unresolved_topology_count", "topology_unresolved_count"),
        "semantics": ("unknown_count", "unmatched_count", "unresolved_count", "unclassified_count", "unknown", "unmatched", "unresolved"),
        "style": ("unknown_style_count", "unmatched_style_count", "style_unresolved_count", "unknown_style", "unmatched_style"),
        "length": ("unmeasured_count", "segments_without_span_dimension", "route_segments_without_span_dimension"),
        "nominal_crs": ("crs_unknown_count", "georef_unresolved_count"),
    }
    for name in names.get(dimension, ()):
        value = _find_value(sample, name)
        count = _as_count(value)
        if count and count > 0:
            return count
    return None


def _absolute_accuracy(sample: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    gcp = _find_mapping(sample, "gcp", "gcp_profile", "calibration", "absolute_accuracy", "coordinate_accuracy")
    crs = _dict(sample.get("crs"))
    if not gcp:
        gcp = _dict(crs.get("calibration"))
    validation_text = " ".join(
        str(value).lower()
        for value in (
            _find_value(sample, "absolute_accuracy_validation"),
            gcp.get("absolute_accuracy_validation"),
        )
        if value is not None
    )
    surveyed = any(
        value is True
        for value in (
            gcp.get("surveyed"),
            gcp.get("surveyed_gcp"),
            gcp.get("surveyed_controls"),
            gcp.get("independent_survey"),
            gcp.get("reviewed_survey"),
        )
    )
    if "no surveyed" in validation_text or "not independently verified" in validation_text:
        surveyed = False
    if not surveyed:
        return "FAIL", ["absolute accuracy requires reviewed surveyed GCPs"]

    train = _first(sample, "train", "training", default=gcp.get("train", gcp.get("training")))
    check = _first(sample, "check", "check_controls", default=gcp.get("check", gcp.get("check_controls")))
    train_count = _as_count(train)
    check_count = _as_count(check)
    if train_count is None:
        train_count = _as_count(gcp.get("training_control_count"))
    if check_count is None:
        check_count = _as_count(gcp.get("check_control_count"))
    if train_count is None or train_count < 3:
        reasons.append("fewer than three independent training controls")
    if check_count is None or check_count < 3:
        reasons.append("fewer than three independent check controls")

    check_evidence = _dict(check)
    check_status = _status(
        _first(
            check_evidence,
            "status",
            "passed",
            "verified",
            default=_first(gcp, "check_status", "check_passed"),
        )
    )
    metrics = _dict(gcp.get("check_metrics"))
    if check_status is None:
        check_status = _status(metrics)
    if check_status == "FAIL":
        reasons.append("independent check-control residual validation failed")
        return "FAIL", reasons
    reviewed = gcp.get("reviewed") is True or gcp.get("review_source") not in (None, "")
    if train_count is not None and train_count >= 3 and check_count is not None and check_count >= 3 and check_status == "PASS" and reviewed:
        return "PASS", reasons
    reasons.append("surveyed GCP evidence is incomplete or not reviewed")
    return "WATCH", reasons


def _normalise_sample(raw: Mapping[str, Any], index: int, root: Mapping[str, Any]) -> dict[str, Any]:
    sample = dict(raw)
    source = _source_record(sample)
    reasons: list[str] = []
    inventory = _is_inventory(sample)
    input_verified, hash_reasons = _source_hash_verified(sample, source)
    reasons.extend(hash_reasons)

    validation = _dict(sample.get("validation"))
    evaluated = sample.get("evaluated") is True
    if not evaluated:
        evaluated = bool(validation) or sample.get("status") in {"PASS", "WATCH", "FAIL"}
    if inventory:
        evaluated = False

    dims: dict[str, str] = {}
    dim_reasons: dict[str, list[str]] = {}
    for dimension in DIMENSIONS:
        if inventory:
            dims[dimension] = "WATCH"
            dim_reasons[dimension] = ["inventory-only row is not a quality evaluation"]
        else:
            status, detail = _normalise_dimension_evidence(sample, dimension)
            dims[dimension] = status
            dim_reasons[dimension] = detail
            reasons.extend(detail)

    if not input_verified:
        reasons.append("input content is not verified")
    if not evaluated and not inventory:
        reasons.append("sample has no evaluated validation record")

    if inventory:
        status = "INVENTORY_ONLY"
    elif any(value == "FAIL" for value in dims.values()) or not input_verified:
        status = "FAIL"
    elif any(value == "WATCH" for value in dims.values()) or not evaluated:
        status = "WATCH"
    else:
        status = "PASS"

    def raw_or_empty(*keys: str) -> Any:
        value = _first(sample, *keys)
        return _copy_json(value) if value is not None else None

    profile = raw_or_empty("profile", "source_profile", "project_profile", "profiles")
    gold = raw_or_empty("gold", "golden", "gold_dataset", "benchmark")
    gcp = raw_or_empty("gcp", "gcp_profile", "calibration")
    if gcp is None:
        crs_calibration = _dict(_dict(sample.get("crs")).get("calibration"))
        gcp = crs_calibration or None
    train = raw_or_empty("train", "training", "training_controls")
    check = raw_or_empty("check", "check_controls", "check_points")
    if isinstance(gcp, Mapping):
        if train is None:
            train_count = _first(gcp, "training_control_count", "training_count")
            if train_count is not None:
                train = {"count": _copy_json(train_count), "source": "gcp"}
        if check is None:
            check_count = _first(gcp, "check_control_count", "check_count")
            if check_count is not None:
                check = {"count": _copy_json(check_count), "source": "gcp"}
    layouts = raw_or_empty("layouts", "layout", "paperspace_layouts")
    blocks = raw_or_empty("blocks", "block_inventory")
    curves = raw_or_empty("curves", "curve_inventory", "curve_facts")

    result = {
        "sample_id": _first(sample, "sample_id", "id", "project_id", "project", "name", default=f"sample-{index + 1}"),
        "inventory_only": inventory,
        "evaluated": evaluated,
        "input_verified": input_verified,
        "input_sha256": source.get("sha256"),
        "source": source,
        "layouts": layouts,
        "blocks": blocks,
        "curves": curves,
        "profile": profile,
        "gold": gold,
        "gcp": gcp,
        "train": train,
        "check": check,
        "status": status,
        "dimensions": dims,
        "dimension_reasons": dim_reasons,
        "reasons": sorted(set(str(reason) for reason in reasons if reason)),
    }
    # Include stable source/schema metadata without exposing arbitrary mutable
    # objects.  The details are intentionally useful to a reviewer but are not
    # consumed by the claim gate as evidence by themselves.
    for key in ("schema_version", "pipeline", "implementation_sha256"):
        if key in sample:
            result[key] = _copy_json(sample[key])
    return result


def _sample_rows(payload: Any) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    if isinstance(payload, list):
        return {}, [row for row in payload if isinstance(row, Mapping)]
    if not isinstance(payload, Mapping):
        raise VerificationMatrixError("matrix root must be a JSON object or array")
    root = dict(payload)
    nested = root.get("matrix")
    if isinstance(nested, Mapping):
        root = dict(nested)
    rows = root.get("samples")
    if rows is None:
        rows = root.get("entries")
    if rows is None:
        rows = root.get("runs")
    if rows is None:
        # A normal run manifest is itself one sample.
        rows = [root]
    if not isinstance(rows, list):
        raise VerificationMatrixError("samples/entries/runs must be a JSON array")
    return root, [row for row in rows if isinstance(row, Mapping)]


def _overall_status(samples: Sequence[Mapping[str, Any]]) -> str:
    if not samples:
        return "FAIL"
    statuses = {str(sample.get("status", "FAIL")).upper() for sample in samples}
    evaluated = [sample for sample in samples if sample.get("evaluated") is True and not sample.get("inventory_only")]
    if not evaluated:
        return "WATCH"
    if "FAIL" in statuses:
        return "FAIL"
    if "WATCH" in statuses:
        return "WATCH"
    return "PASS"


def evaluate_matrix(path: str | Path) -> dict[str, Any]:
    """Read and evaluate a versioned matrix or a single run manifest.

    No files are created or changed.  ``path`` is intentionally the only input
    accepted by the stable API so a caller cannot accidentally pass generated
    output as a circular reference.  Invalid JSON/schema returns a deterministic
    ``FAIL`` report with the error text, while missing paths raise
    :class:`FileNotFoundError` for an actionable CLI error.
    """

    matrix_path = Path(path)
    raw_bytes = matrix_path.read_bytes()
    matrix_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "matrix_schema_version": None,
            "matrix_path": str(matrix_path),
            "matrix_sha256": matrix_sha256,
            "status": "FAIL",
            "samples": [],
            "summary": {"sample_count": 0, "evaluated_count": 0, "unique_input_hashes": 0, "cross_cad_eligible": False},
            "errors": [f"invalid JSON: {exc.__class__.__name__}"],
            "claim": "Inventory only: no conversion-quality or accuracy claim is supported.",
        }

    try:
        root, rows = _sample_rows(payload)
    except VerificationMatrixError as exc:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "matrix_schema_version": None,
            "matrix_path": str(matrix_path),
            "matrix_sha256": matrix_sha256,
            "status": "FAIL",
            "samples": [],
            "summary": {"sample_count": 0, "evaluated_count": 0, "unique_input_hashes": 0, "cross_cad_eligible": False},
            "errors": [str(exc)],
            "claim": "Inventory only: no conversion-quality or accuracy claim is supported.",
        }

    matrix_schema = _first(root, "schema_version", "matrix_schema_version")
    # Run manifests are valid input records even though they predate the matrix
    # schema.  Unsupported explicit matrix versions are reported, not guessed.
    explicit_matrix = matrix_schema is not None and "verification-matrix" in str(matrix_schema)
    errors: list[str] = []
    if explicit_matrix and str(matrix_schema) not in SUPPORTED_MATRIX_SCHEMAS:
        errors.append(f"unsupported matrix schema: {matrix_schema}")

    samples = [_normalise_sample(row, index, root) for index, row in enumerate(rows)]
    hashes = {
        str(sample.get("input_sha256")).lower()
        for sample in samples
        if sample.get("input_verified") is True and _valid_hash(sample.get("input_sha256")) and not sample.get("inventory_only")
    }
    evaluated = [sample for sample in samples if sample.get("evaluated") is True and not sample.get("inventory_only")]
    if len(rows) != len(samples):
        errors.append("one or more matrix rows were not JSON objects")
    if not rows:
        errors.append("matrix contains no samples")

    report_status = "FAIL" if errors else _overall_status(samples)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "matrix_schema_version": matrix_schema,
        "matrix_path": str(matrix_path),
        "matrix_sha256": matrix_sha256,
        "status": report_status,
        "samples": samples,
        "summary": {
            "sample_count": len(samples),
            "evaluated_count": len(evaluated),
            "inventory_count": sum(1 for sample in samples if sample.get("inventory_only")),
            "unique_input_hashes": len(hashes),
            "cross_cad_eligible": len(hashes) >= 2,
            "absolute_accuracy_verified_count": sum(
                1 for sample in evaluated if sample["dimensions"].get("absolute_accuracy") == "PASS"
            ),
        },
        "dimensions": {
            dimension: _aggregate_dimension(samples, dimension)
            for dimension in DIMENSIONS
        },
        "errors": errors,
    }
    # Claim calculation intentionally consumes only normalized sample evidence.
    report["claim"] = strongest_allowed_claim(report)
    return report


def _aggregate_dimension(samples: Sequence[Mapping[str, Any]], dimension: str) -> dict[str, Any]:
    values = [
        _status(_dict(sample.get("dimensions")).get(dimension))
        for sample in samples
        if sample.get("evaluated") is True and not sample.get("inventory_only")
    ]
    values = [value for value in values if value]
    if not values:
        return {"status": "WATCH", "evaluated_samples": 0}
    if "FAIL" in values:
        status = "FAIL"
    elif "WATCH" in values:
        status = "WATCH"
    else:
        status = "PASS"
    return {"status": status, "evaluated_samples": len(values), "pass_count": values.count("PASS"), "watch_count": values.count("WATCH"), "fail_count": values.count("FAIL")}


__all__ = [
    "DIMENSIONS",
    "MATRIX_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "SUPPORTED_MATRIX_SCHEMAS",
    "STATUSES",
    "VerificationMatrixError",
    "evaluate_matrix",
]
