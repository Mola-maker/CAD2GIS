"""User-facing adapter for the reviewed ground-control workflow.

This module deliberately contains no calibration mathematics.  It wraps the
operator-only implementation in ``experiment/py_scripts/gcp_tool.py`` and
normalises its results for the canonical CLI.  In particular:

* capture coordinates, accuracy values, and weights are never invented here;
* diagnostics never authorise publication or absolute-accuracy claims;
* OpenStreetMap references remain relative visual references; and
* a project is verified only after a published manifest records an accepted
  calibration with independent checks and auditable absolute control sources.

The experiment implementation is loaded lazily so that lightweight commands
such as ``cad2gis gcp status`` do not require GDAL.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import sqlite3
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence


PathLike = str | os.PathLike[str]

DEFAULT_CANDIDATE_LAYERS = ("PTECH", "BOITE", "SITE")
MIN_ACTIVE_TRAIN_CONTROLS = 3
MIN_ACTIVE_CHECK_CONTROLS = 2

ABSOLUTE_ACCURACY_VERIFIED = "verified"
ABSOLUTE_ACCURACY_NOT_VERIFIED = "not_verified"

RELATIVE_OSM_WARNING = (
    "OpenStreetMap is a relative visual reference only; it is not surveyed "
    "ground truth and cannot establish absolute positional accuracy."
)

_SURVEYED_KIND = "surveyed_control"
_AUTHORITATIVE_KIND = "authoritative_control"
_RELATIVE_OSM_KIND = "relative_osm_reference"
_ABSOLUTE_KINDS = frozenset({_SURVEYED_KIND, _AUTHORITATIVE_KIND})
_OSM_SOURCE = re.compile(r"(?:\bOSM\b|OPEN\s*STREET\s*MAP)", re.IGNORECASE)
_BACKEND_MODULE_NAME = "_cad2gis_operator_gcp_tool"
_BACKEND_LOCK = threading.Lock()


__all__ = [
    "ABSOLUTE_ACCURACY_NOT_VERIFIED",
    "ABSOLUTE_ACCURACY_VERIFIED",
    "DEFAULT_CANDIDATE_LAYERS",
    "DiagnoseCaptureRequest",
    "DiagnoseRequest",
    "ExportProfileRequest",
    "ExportReviewedProfileRequest",
    "GCPWorkflowResult",
    "PrepareCaptureRequest",
    "PrepareRequest",
    "RELATIVE_OSM_WARNING",
    "diagnose",
    "diagnose_capture",
    "export_profile",
    "export_reviewed_profile",
    "prepare",
    "prepare_capture",
    "status_project",
]


def _path(value: PathLike, field_name: str) -> Path:
    if isinstance(value, os.PathLike):
        result = Path(value)
    elif isinstance(value, str) and value.strip():
        result = Path(value)
    else:
        raise ValueError(f"{field_name} must be a non-empty path")
    return result.expanduser()


@dataclass(frozen=True, slots=True)
class PrepareCaptureRequest:
    """Inputs for creating an editable, non-authoritative GCP capture."""

    delivery_path: Path
    evidence_path: Path
    manifest_path: Path
    output_path: Path
    candidate_layers: tuple[str, ...] = DEFAULT_CANDIDATE_LAYERS
    force: bool = False

    def __post_init__(self) -> None:
        for name in ("delivery_path", "evidence_path", "manifest_path", "output_path"):
            object.__setattr__(self, name, _path(getattr(self, name), name))
        layers = tuple(
            dict.fromkeys(str(layer).strip().upper() for layer in self.candidate_layers)
        )
        if not layers or any(not layer for layer in layers):
            raise ValueError("candidate_layers must contain at least one non-empty layer")
        object.__setattr__(self, "candidate_layers", layers)

    def backend_kwargs(self) -> dict[str, Any]:
        return {
            "delivery_path": self.delivery_path,
            "evidence_path": self.evidence_path,
            "manifest_path": self.manifest_path,
            "output_path": self.output_path,
            "candidate_layers": self.candidate_layers,
            "force": self.force,
        }


@dataclass(frozen=True, slots=True)
class DiagnoseCaptureRequest:
    """Inputs for diagnostic fitting; this operation never publishes geometry."""

    capture_path: Path
    report_path: Path
    robust_outlier_threshold_m: float | None = None
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "capture_path", _path(self.capture_path, "capture_path"))
        object.__setattr__(self, "report_path", _path(self.report_path, "report_path"))
        if self.robust_outlier_threshold_m is not None:
            value = _positive_number(
                self.robust_outlier_threshold_m, "robust_outlier_threshold_m"
            )
            object.__setattr__(self, "robust_outlier_threshold_m", value)

    def backend_kwargs(self) -> dict[str, Any]:
        return {
            "capture_path": self.capture_path,
            "report_path": self.report_path,
            "robust_outlier_threshold_m": self.robust_outlier_threshold_m,
            "force": self.force,
        }


@dataclass(frozen=True, slots=True)
class ExportReviewedProfileRequest:
    """Inputs for exporting a reviewed profile from operator-entered controls."""

    capture_path: Path
    template_profile_path: Path
    output_path: Path
    diagnostic_report_path: Path | None = None
    enable: bool = False
    requested_model: str | None = None
    spatial_review_source: str | None = None
    max_check_error_m: float | None = None
    max_pivot_shift_m: float | None = None
    max_abs_rotation_deg: float | None = None
    max_scale_deviation_ratio: float | None = None
    max_affine_condition_number: float | None = None
    robust_outlier_threshold_m: float | None = None
    disable_robust: bool = False
    affine_min_improvement_ratio: float | None = None
    affine_structure_reviewed: bool = False
    allow_relative_osm: bool = False
    force: bool = False

    def __post_init__(self) -> None:
        for name in ("capture_path", "template_profile_path", "output_path"):
            object.__setattr__(self, name, _path(getattr(self, name), name))
        if self.diagnostic_report_path is not None:
            object.__setattr__(
                self,
                "diagnostic_report_path",
                _path(self.diagnostic_report_path, "diagnostic_report_path"),
            )
        if self.requested_model is not None:
            model = str(self.requested_model).strip().lower()
            if model not in {"auto", "translation", "similarity", "affine"}:
                raise ValueError(
                    "requested_model must be auto, translation, similarity, or affine"
                )
            object.__setattr__(self, "requested_model", model)
        if self.spatial_review_source is not None:
            source = str(self.spatial_review_source).strip()
            if not source:
                raise ValueError("spatial_review_source must not be empty")
            object.__setattr__(self, "spatial_review_source", source)
        for name in (
            "max_check_error_m",
            "max_pivot_shift_m",
            "max_abs_rotation_deg",
            "max_scale_deviation_ratio",
            "max_affine_condition_number",
            "robust_outlier_threshold_m",
        ):
            raw = getattr(self, name)
            if raw is not None:
                object.__setattr__(self, name, _positive_number(raw, name))
        if self.disable_robust and self.robust_outlier_threshold_m is not None:
            raise ValueError(
                "disable_robust and robust_outlier_threshold_m are mutually exclusive"
            )
        if self.affine_min_improvement_ratio is not None:
            ratio = _finite_number(
                self.affine_min_improvement_ratio, "affine_min_improvement_ratio"
            )
            if not 0.0 <= ratio < 1.0:
                raise ValueError("affine_min_improvement_ratio must be in [0, 1)")
            object.__setattr__(self, "affine_min_improvement_ratio", ratio)

    def backend_kwargs(self) -> dict[str, Any]:
        return {
            "capture_path": self.capture_path,
            "template_profile_path": self.template_profile_path,
            "output_path": self.output_path,
            "diagnostic_report_path": self.diagnostic_report_path,
            "enable": self.enable,
            "requested_model": self.requested_model,
            "spatial_review_source": self.spatial_review_source,
            "max_check_error_m": self.max_check_error_m,
            "max_pivot_shift_m": self.max_pivot_shift_m,
            "max_abs_rotation_deg": self.max_abs_rotation_deg,
            "max_scale_deviation_ratio": self.max_scale_deviation_ratio,
            "max_affine_condition_number": self.max_affine_condition_number,
            "robust_outlier_threshold_m": self.robust_outlier_threshold_m,
            "disable_robust": self.disable_robust,
            "affine_min_improvement_ratio": self.affine_min_improvement_ratio,
            "affine_structure_reviewed": self.affine_structure_reviewed,
            "allow_relative_osm": self.allow_relative_osm,
            "force": self.force,
        }


# Short aliases are convenient for CLI adapters while the longer names make the
# capture/review boundary explicit to library users.
PrepareRequest = PrepareCaptureRequest
DiagnoseRequest = DiagnoseCaptureRequest
ExportProfileRequest = ExportReviewedProfileRequest


@dataclass(frozen=True, slots=True)
class GCPWorkflowResult:
    """Normalised, JSON-safe result returned by every workflow operation."""

    operation: str
    status: str
    absolute_accuracy_validation: str
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    operator_actions: tuple[str, ...] = ()
    artifacts: Mapping[str, Any] | None = None
    authority: Mapping[str, Any] | None = None
    backend_result: Mapping[str, Any] | None = None
    error: Mapping[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a stable mapping suitable for JSON CLI output."""

        value: dict[str, Any] = dict(_json_safe(self.backend_result or {}))
        value.update(
            {
                "operation": self.operation,
                "status": self.status,
                "absolute_accuracy_validation": self.absolute_accuracy_validation,
                "blockers": list(self.blockers),
                "warnings": list(self.warnings),
                "next_actions": list(self.next_actions),
                "operator_actions": list(self.operator_actions),
                "artifacts": _json_safe(self.artifacts or {}),
                "authority": _json_safe(self.authority or {}),
                "input_policy": _input_policy(),
            }
        )
        if self.error is not None:
            value["error"] = _json_safe(self.error)
        return value


def _json_safe(value: Any) -> Any:
    """Normalise adapter values before handing them to a JSON CLI.

    The operator backend normally returns strings and primitive values, but
    library callers may pass ``Path`` objects or tuples in a result.  Keeping
    this conversion at the result boundary prevents a late ``json.dumps``
    traceback and does not alter any source/control values.
    """

    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _input_policy() -> dict[str, Any]:
    return {
        "coordinates": "operator_or_authoritative_input_only",
        "accuracy_and_weight": "operator_or_authoritative_input_only",
        "adapter_generates_control_values": False,
        "relative_osm_is_absolute_ground_truth": False,
    }


def _finite_number(value: Any, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _positive_number(value: Any, name: str) -> float:
    result = _finite_number(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _quote_cli_path(path: PathLike) -> str:
    value = str(Path(path).resolve())
    return '"' + value.replace('"', '\\"') + '"'


def _project_command(project_dir: Path, command: str, *arguments: str) -> str:
    suffix = "" if not arguments else " " + " ".join(arguments)
    return f"cad2gis gcp {command} --project {_quote_cli_path(project_dir)}{suffix}"


def _conversion_command(
    project_dir: Path,
    profile_path: Path,
    *,
    source_path: Path | None = None,
) -> str:
    """Build a safe, non-overwriting conversion command for operators.

    Conversion requires an explicit source and a new run directory.  A GCP
    workflow must never guess either value or silently reuse a published run,
    so status/export use editable placeholders when the source is not bound to
    the request.  When a manifest has an authoritative source path we include
    that path while retaining a fresh ``<NEW_RUN_DIR>`` placeholder.
    """

    source = _quote_cli_path(source_path) if source_path is not None else '"<SOURCE.dwg>"'
    return (
        f"cad2gis convert {source} --run-dir \"<NEW_RUN_DIR>\""
        f" --project {_quote_cli_path(project_dir)}"
        f" --gcp-profile {_quote_cli_path(profile_path)}"
    )


def _backend_path() -> Path:
    override = os.environ.get("CAD2GIS_GCP_TOOL_PATH")
    if override:
        return Path(override).expanduser().resolve()
    repository_root = Path(__file__).resolve().parents[2]
    return repository_root / "experiment" / "py_scripts" / "gcp_tool.py"


@lru_cache(maxsize=1)
def _load_backend() -> ModuleType:
    """Load the reviewed experiment tool without making GDAL a status dependency."""

    path = _backend_path()
    if not path.is_file():
        raise ImportError(f"GCP operator backend was not found at {path}")
    scripts_dir = str(path.parent)
    with _BACKEND_LOCK:
        cached = sys.modules.get(_BACKEND_MODULE_NAME)
        if cached is not None:
            return cached
        spec = importlib.util.spec_from_file_location(_BACKEND_MODULE_NAME, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load GCP operator backend from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_BACKEND_MODULE_NAME] = module
        inserted = scripts_dir not in sys.path
        if inserted:
            sys.path.insert(0, scripts_dir)
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(_BACKEND_MODULE_NAME, None)
            raise
        finally:
            if inserted:
                try:
                    sys.path.remove(scripts_dir)
                except ValueError:
                    pass
        return module


def _blocked_result(
    operation: str,
    error: Exception,
    project_dir: Path,
    *,
    retry_command: str,
) -> dict[str, Any]:
    message = str(error).strip() or error.__class__.__name__
    actions = [_project_command(project_dir, retry_command)]
    operator_actions: list[str] = []
    lower = message.lower()
    if "no accepted controls" in lower or "training control" in lower or "check control" in lower:
        operator_actions.append(
            "Review the capture in QGIS and enter train/check target coordinates, "
            "accuracy, and weight from surveyed or approved authoritative records."
        )
    if "sha-256" in lower or "stale" in lower or "immutable" in lower:
        actions = [_project_command(project_dir, "prepare", "--force")]
        operator_actions.append(
            "Rebuild the capture from the current manifest and artifacts; do not edit "
            "immutable CAD or nominal-coordinate fields."
        )
    if "relative osm" in lower or "openstreetmap" in lower:
        operator_actions.append(RELATIVE_OSM_WARNING)
    return GCPWorkflowResult(
        operation=operation,
        status="blocked",
        absolute_accuracy_validation=ABSOLUTE_ACCURACY_NOT_VERIFIED,
        blockers=(message,),
        next_actions=tuple(actions),
        warnings=(RELATIVE_OSM_WARNING,) if "osm" in lower else (),
        operator_actions=tuple(operator_actions),
        error={"type": error.__class__.__name__, "message": message},
    ).to_dict()


def prepare_capture(
    request: PrepareCaptureRequest | None = None,
    *,
    delivery_path: PathLike | None = None,
    evidence_path: PathLike | None = None,
    manifest_path: PathLike | None = None,
    output_path: PathLike | None = None,
    candidate_layers: Sequence[str] = DEFAULT_CANDIDATE_LAYERS,
    force: bool = False,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Prepare a capture and return a fail-closed, CLI-friendly result."""

    if request is None:
        missing = [
            name
            for name, value in (
                ("delivery_path", delivery_path),
                ("evidence_path", evidence_path),
                ("manifest_path", manifest_path),
                ("output_path", output_path),
            )
            if value is None
        ]
        if missing:
            raise TypeError("Missing required arguments: " + ", ".join(missing))
        request = PrepareCaptureRequest(
            delivery_path=_path(delivery_path, "delivery_path"),  # type: ignore[arg-type]
            evidence_path=_path(evidence_path, "evidence_path"),  # type: ignore[arg-type]
            manifest_path=_path(manifest_path, "manifest_path"),  # type: ignore[arg-type]
            output_path=_path(output_path, "output_path"),  # type: ignore[arg-type]
            candidate_layers=tuple(candidate_layers),
            force=force,
        )
    elif any(value is not None for value in (delivery_path, evidence_path, manifest_path, output_path)):
        raise TypeError("Pass either a PrepareCaptureRequest or keyword paths, not both")
    project_dir = request.output_path.parent
    try:
        raw = _load_backend().prepare_capture(**request.backend_kwargs())
    # The operator backend is deliberately kept behind a friendly boundary.
    # Its dependencies (GDAL/OGR) and input files are external to this
    # lightweight package, so a malformed capture may raise e.g. a sqlite or
    # backend-specific exception.  Convert *all ordinary exceptions* to a
    # structured blocked result; callers can opt into the original exception
    # with ``raise_on_error=True`` for debugging.
    except Exception as exc:
        if raise_on_error:
            raise
        return _blocked_result("prepare", exc, project_dir, retry_command="prepare")
    capture = Path(str(raw.get("capture", request.output_path)))
    return GCPWorkflowResult(
        operation="prepare",
        status="blocked",
        absolute_accuracy_validation=ABSOLUTE_ACCURACY_NOT_VERIFIED,
        blockers=(
            "No reviewed surveyed/authoritative training and independent check "
            "controls have been diagnosed yet.",
        ),
        warnings=(
            "Candidate geometry is nominal only and is not a control coordinate.",
        ),
        operator_actions=(
            "Open the capture in QGIS; enter target coordinates, accuracy, and weight "
            "only from manual surveyed or approved authoritative input, then accept "
            "and enable frozen train/check controls.",
        ),
        next_actions=(_project_command(project_dir, "diagnose"),),
        artifacts={"capture": str(capture), "capture_sha256": raw.get("capture_sha256")},
        authority=_empty_authority(),
        backend_result=raw,
    ).to_dict()


def diagnose_capture(
    request: DiagnoseCaptureRequest | None = None,
    *,
    capture_path: PathLike | None = None,
    report_path: PathLike | None = None,
    robust_outlier_threshold_m: float | None = None,
    force: bool = False,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Run diagnostics without treating model ranking as publication evidence."""

    if request is None:
        if capture_path is None or report_path is None:
            raise TypeError("capture_path and report_path are required")
        request = DiagnoseCaptureRequest(
            capture_path=_path(capture_path, "capture_path"),
            report_path=_path(report_path, "report_path"),
            robust_outlier_threshold_m=robust_outlier_threshold_m,
            force=force,
        )
    elif capture_path is not None or report_path is not None:
        raise TypeError("Pass either a DiagnoseCaptureRequest or keyword paths, not both")
    project_dir = request.report_path.parent
    try:
        raw = _load_backend().diagnose_capture(**request.backend_kwargs())
    except Exception as exc:
        if raise_on_error:
            raise
        return _blocked_result("diagnose", exc, project_dir, retry_command="diagnose")

    train_count = _nonnegative_int(raw.get("active_train_count"))
    check_count = _nonnegative_int(raw.get("active_check_count"))
    reference_scope = str(raw.get("reference_scope") or "unknown")
    relative = "relative_osm" in reference_scope.lower()
    blockers: list[str] = []
    if train_count < MIN_ACTIVE_TRAIN_CONTROLS:
        blockers.append(
            f"At least {MIN_ACTIVE_TRAIN_CONTROLS} active reviewed training controls "
            f"are required; found {train_count}."
        )
    if check_count < MIN_ACTIVE_CHECK_CONTROLS:
        blockers.append(
            f"At least {MIN_ACTIVE_CHECK_CONTROLS} independent active check controls "
            f"are required; found {check_count}."
        )
    if relative:
        blockers.append(RELATIVE_OSM_WARNING)
    if not raw.get("available_models"):
        blockers.append("No calibration model is currently diagnosable.")
    # A missing model is a hard diagnostic blocker even when the control
    # counts happen to meet the theoretical minimum (for example, collinear
    # controls).  A relative OSM reference is represented as not_verified,
    # not as an operational failure, when diagnostics themselves succeeded.
    hard_blocked = (
        train_count < MIN_ACTIVE_TRAIN_CONTROLS
        or check_count < MIN_ACTIVE_CHECK_CONTROLS
        or not raw.get("available_models")
    )
    if hard_blocked:
        status = "blocked"
    else:
        status = "not_verified"
        blockers.append(
            "Diagnostics rank draft models only; no reviewed profile has been applied "
            "to a published delivery."
        )
    return GCPWorkflowResult(
        operation="diagnose",
        status=status,
        absolute_accuracy_validation=ABSOLUTE_ACCURACY_NOT_VERIFIED,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=(RELATIVE_OSM_WARNING,) if relative else (),
        operator_actions=(
            "Review residuals, spatial coverage, frozen train/check roles, provenance, "
            "and project-specific numeric limits before profile export.",
        ),
        next_actions=(_project_command(project_dir, "export"),),
        artifacts={"report": raw.get("report"), "report_sha256": raw.get("report_sha256")},
        authority={
            **_empty_authority(),
            "active_train_count": train_count,
            "active_check_count": check_count,
            "reference_scope": reference_scope,
            "contains_relative_osm": relative,
        },
        backend_result=raw,
    ).to_dict()


def export_reviewed_profile(
    request: ExportReviewedProfileRequest | None = None,
    *,
    capture_path: PathLike | None = None,
    template_profile_path: PathLike | None = None,
    output_path: PathLike | None = None,
    diagnostic_report_path: PathLike | None = None,
    enable: bool = False,
    requested_model: str | None = None,
    spatial_review_source: str | None = None,
    max_check_error_m: float | None = None,
    max_pivot_shift_m: float | None = None,
    max_abs_rotation_deg: float | None = None,
    max_scale_deviation_ratio: float | None = None,
    max_affine_condition_number: float | None = None,
    robust_outlier_threshold_m: float | None = None,
    disable_robust: bool = False,
    affine_min_improvement_ratio: float | None = None,
    affine_structure_reviewed: bool = False,
    allow_relative_osm: bool = False,
    force: bool = False,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Export a reviewed profile while keeping delivery verification separate."""

    if request is None:
        if capture_path is None or template_profile_path is None or output_path is None:
            raise TypeError(
                "capture_path, template_profile_path, and output_path are required"
            )
        request = ExportReviewedProfileRequest(
            capture_path=_path(capture_path, "capture_path"),
            template_profile_path=_path(template_profile_path, "template_profile_path"),
            output_path=_path(output_path, "output_path"),
            diagnostic_report_path=(
                None
                if diagnostic_report_path is None
                else _path(diagnostic_report_path, "diagnostic_report_path")
            ),
            enable=enable,
            requested_model=requested_model,
            spatial_review_source=spatial_review_source,
            max_check_error_m=max_check_error_m,
            max_pivot_shift_m=max_pivot_shift_m,
            max_abs_rotation_deg=max_abs_rotation_deg,
            max_scale_deviation_ratio=max_scale_deviation_ratio,
            max_affine_condition_number=max_affine_condition_number,
            robust_outlier_threshold_m=robust_outlier_threshold_m,
            disable_robust=disable_robust,
            affine_min_improvement_ratio=affine_min_improvement_ratio,
            affine_structure_reviewed=affine_structure_reviewed,
            allow_relative_osm=allow_relative_osm,
            force=force,
        )
    elif any(
        value is not None
        for value in (capture_path, template_profile_path, output_path, diagnostic_report_path)
    ):
        raise TypeError(
            "Pass either an ExportReviewedProfileRequest or keyword paths, not both"
        )
    project_dir = request.output_path.parent
    try:
        raw = _load_backend().export_profile(**request.backend_kwargs())
    except Exception as exc:
        if raise_on_error:
            raise
        return _blocked_result("export", exc, project_dir, retry_command="export")

    profile_path = Path(str(raw.get("profile", request.output_path)))
    profile: Mapping[str, Any] = {}
    profile_error: str | None = None
    try:
        profile = _read_json_object(profile_path, "reviewed GCP profile")
    except (OSError, ValueError) as exc:
        profile_error = str(exc)
    authority = _authority_summary(profile.get("controls", ()))
    reference_scope = str(raw.get("reference_scope") or authority["reference_scope"])
    relative = bool(authority["contains_relative_osm"]) or "relative_osm" in reference_scope
    blockers = list(authority["issues"])
    if profile_error:
        blockers.append(profile_error)
    enabled = raw.get("enabled") is True
    validation_passed = raw.get("validation_passed") is True
    if relative:
        blockers.append(RELATIVE_OSM_WARNING)
    if not enabled:
        blockers.append("The exported profile is disabled and cannot adjust delivery coordinates.")
        status = "not_verified"
    elif not validation_passed:
        blockers.append("The enabled profile did not pass all reviewed calibration gates.")
        status = "blocked"
    elif not authority["absolute_train_and_check_ready"] or authority["unclassified_count"]:
        blockers.append(
            "Enabled controls lack complete surveyed/authoritative train/check provenance."
        )
        status = "blocked"
    elif relative:
        status = "not_verified"
    else:
        status = "ready_for_conversion"
        blockers.append(
            "The reviewed profile passed, but the project delivery has not yet been "
            "rerun and published with this profile."
        )
    convert_command = _conversion_command(project_dir, profile_path)
    return GCPWorkflowResult(
        operation="export",
        status=status,
        absolute_accuracy_validation=ABSOLUTE_ACCURACY_NOT_VERIFIED,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=(RELATIVE_OSM_WARNING,) if relative else (),
        operator_actions=(
            "Treat allow-relative-osm only as acknowledgement of visual alignment, "
            "never as an absolute-accuracy approval.",
        ) if relative else (),
        next_actions=(convert_command, _project_command(project_dir, "status")),
        artifacts={
            "profile": str(profile_path),
            "profile_sha256": raw.get("profile_sha256"),
            "diagnostic_report_sha256": raw.get("diagnostic_report_sha256"),
        },
        authority=authority,
        backend_result=raw,
    ).to_dict()


# Public short forms used by CLI code and compatibility name matching the
# experiment backend.  All three still return the normalised dictionary.
prepare = prepare_capture
diagnose = diagnose_capture
export_profile = export_reviewed_profile


def status_project(project_dir: str | Path) -> dict[str, Any]:
    """Inspect GCP workflow artifacts without loading GDAL or fitting a model.

    A ``verified`` result is intentionally hard to obtain.  It requires an
    accepted calibration in the published run manifest, successful independent
    check metrics, a hash-matched enabled profile, and active train/check
    controls whose provenance is surveyed or authoritative.  Relative OSM or
    unclassified controls always keep absolute accuracy ``not_verified``.
    """

    project = _path(project_dir, "project_dir").resolve()
    artifacts: dict[str, Any] = {"project_dir": str(project)}
    blockers: list[str] = []
    warnings: list[str] = []
    operator_actions: list[str] = []
    if not project.is_dir():
        return GCPWorkflowResult(
            operation="status",
            status="blocked",
            absolute_accuracy_validation=ABSOLUTE_ACCURACY_NOT_VERIFIED,
            blockers=(f"Project directory does not exist: {project}",),
            next_actions=(_project_command(project, "prepare"),),
            artifacts=artifacts,
            authority=_empty_authority(),
        ).to_dict()

    manifest_path = _discover_one(
        project,
        preferred=("run_manifest.json",),
        patterns=("*run*manifest*.json",),
    )
    manifest: Mapping[str, Any] = {}
    if manifest_path is not None:
        artifacts["manifest"] = str(manifest_path)
        try:
            artifacts["manifest_sha256"] = _sha256_file(manifest_path)
            manifest = _read_json_object(manifest_path, "run manifest")
        except (OSError, ValueError) as exc:
            blockers.append(str(exc))
    else:
        blockers.append("No published run_manifest.json was found in the project directory.")

    profile_path = _profile_path_from_manifest(manifest, manifest_path, project)
    if profile_path is None:
        profile_path = _discover_one(
            project,
            preferred=("reviewed_gcp_profile.json", "gcp_profile.json"),
            patterns=("*gcp*profile*.json", "*reviewed*profile*.json"),
        )
    profile: Mapping[str, Any] = {}
    profile_hash: str | None = None
    if profile_path is not None and profile_path.is_file():
        artifacts["profile"] = str(profile_path)
        try:
            profile_hash = _sha256_file(profile_path)
            artifacts["profile_sha256"] = profile_hash
            profile = _read_json_object(profile_path, "GCP profile")
        except (OSError, ValueError) as exc:
            blockers.append(str(exc))

    capture_path = _discover_one(
        project,
        preferred=("gcp_capture.gpkg", "capture.gpkg"),
        patterns=("*gcp*capture*.gpkg",),
    )
    if capture_path is not None:
        artifacts["capture"] = str(capture_path)
    diagnostic_path = _discover_one(
        project,
        preferred=("gcp_diagnostic_report.json", "gcp_diagnostic.json", "diagnostic.json"),
        patterns=("*gcp*diagnostic*.json", "*diagnostic*report*.json"),
    )
    diagnostic: Mapping[str, Any] = {}
    if diagnostic_path is not None:
        artifacts["diagnostic_report"] = str(diagnostic_path)
        try:
            diagnostic = _read_json_object(diagnostic_path, "GCP diagnostic report")
        except (OSError, ValueError) as exc:
            blockers.append(str(exc))

    if profile:
        authority = _authority_summary(profile.get("controls", ()))
    elif capture_path is not None:
        try:
            authority = _capture_authority_summary(capture_path)
        except (sqlite3.Error, ValueError) as exc:
            blockers.append(f"Could not inspect GCP capture: {exc}")
            authority = _empty_authority()
    else:
        authority = _empty_authority()

    for issue in authority["issues"]:
        blockers.append(str(issue))
    if authority["contains_relative_osm"]:
        blockers.append(RELATIVE_OSM_WARNING)
        warnings.append(RELATIVE_OSM_WARNING)
        operator_actions.append(
            "Replace relative OSM controls with independently surveyed or approved "
            "authoritative controls before making any absolute-accuracy claim."
        )

    calibration = _manifest_calibration(manifest)
    artifacts["calibration_status"] = calibration["status"]
    _check_profile_bindings(
        manifest,
        calibration,
        profile,
        profile_hash,
        blockers,
    )

    accepted = calibration["status"] == "accepted"
    validation_passed = calibration["validation_passed"] is True
    manifest_train = calibration["train_count"]
    manifest_check = calibration["check_count"]
    metrics_ready = (
        manifest_train >= MIN_ACTIVE_TRAIN_CONTROLS
        and manifest_check >= MIN_ACTIVE_CHECK_CONTROLS
    )
    authority_ready = bool(authority["absolute_train_and_check_ready"])
    controls_clean = (
        authority["unclassified_count"] == 0
        and not authority["contains_relative_osm"]
        and not authority["issues"]
    )
    profile_enabled = profile.get("enabled") is True

    verified = all(
        (
            accepted,
            validation_passed,
            metrics_ready,
            profile_enabled,
            authority_ready,
            controls_clean,
            profile_hash is not None,
        )
    )
    if verified:
        status = "verified"
        absolute_accuracy = ABSOLUTE_ACCURACY_VERIFIED
        next_actions: tuple[str, ...] = ()
        blockers = []
    else:
        absolute_accuracy = ABSOLUTE_ACCURACY_NOT_VERIFIED
        if accepted and authority["contains_relative_osm"]:
            status = "not_verified"
        elif profile_enabled and validation_passed and authority_ready and controls_clean:
            status = "ready_for_conversion"
            blockers.append(
                "The reviewed enabled profile is not recorded as an accepted calibration "
                "in this project's published manifest."
            )
        else:
            status = "blocked"
        if not accepted:
            blockers.append("The published manifest does not record an accepted GCP calibration.")
        if accepted and not validation_passed:
            blockers.append("Independent GCP validation is not recorded as passed.")
        if accepted and not metrics_ready:
            blockers.append(
                "Published calibration metrics do not contain sufficient training and "
                "independent check controls."
            )
        if not profile:
            blockers.append("No readable reviewed GCP profile was found.")
        elif not profile_enabled:
            blockers.append("The GCP profile is disabled.")
        if not authority_ready:
            blockers.append(
                "No complete surveyed/authoritative train and independent check set is active."
            )
    next_actions = _status_next_actions(
        project,
        capture_path=capture_path,
        diagnostic_path=diagnostic_path,
        diagnostic=diagnostic,
        profile_path=profile_path if profile else None,
        profile_enabled=profile_enabled,
        relative=bool(authority["contains_relative_osm"]),
        source_path=_manifest_source_path(manifest, manifest_path, project),
    )

    return GCPWorkflowResult(
        operation="status",
        status=status,
        absolute_accuracy_validation=absolute_accuracy,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        operator_actions=tuple(operator_actions),
        next_actions=next_actions,
        artifacts=artifacts,
        authority=authority,
        backend_result={
            "project_dir": str(project),
            "calibration": calibration,
        },
    ).to_dict()


def _status_next_actions(
    project: Path,
    *,
    capture_path: Path | None,
    diagnostic_path: Path | None,
    diagnostic: Mapping[str, Any],
    profile_path: Path | None,
    profile_enabled: bool,
    relative: bool,
    source_path: Path | None,
) -> tuple[str, ...]:
    if relative:
        return (_project_command(project, "prepare", "--force"),)
    if capture_path is None:
        return (_project_command(project, "prepare"),)
    if diagnostic_path is None or diagnostic.get("diagnostic_only") is not True:
        return (_project_command(project, "diagnose"),)
    if profile_path is None or not profile_enabled:
        return (_project_command(project, "export"),)
    return (
        _conversion_command(project, profile_path, source_path=source_path),
        _project_command(project, "status"),
    )


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, result)


def _read_json_object(path: Path, description: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{description} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} must contain a JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discover_one(
    directory: Path,
    *,
    preferred: Sequence[str],
    patterns: Sequence[str],
) -> Path | None:
    for name in preferred:
        candidate = directory / name
        if candidate.is_file():
            return candidate.resolve()
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(path for path in directory.glob(pattern) if path.is_file())
    unique = sorted(set(matches), key=lambda path: (path.name.lower(), str(path)))
    return unique[0].resolve() if unique else None


def _profile_path_from_manifest(
    manifest: Mapping[str, Any],
    manifest_path: Path | None,
    project: Path,
) -> Path | None:
    profiles = manifest.get("profiles")
    if not isinstance(profiles, Mapping):
        return None
    entry = profiles.get("gcp_profile")
    if not isinstance(entry, Mapping):
        return None
    raw = entry.get("path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    supplied = Path(raw).expanduser()
    candidates = [supplied]
    if not supplied.is_absolute():
        if manifest_path is not None:
            candidates.append(manifest_path.parent / supplied)
        candidates.append(project / supplied)
    candidates.append(project / supplied.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return supplied.resolve() if supplied.is_absolute() else None


def _manifest_source_path(
    manifest: Mapping[str, Any], manifest_path: Path | None, project: Path
) -> Path | None:
    """Resolve a manifest-bound source path for a review command.

    A source path is advisory command context, not a replacement for the
    immutable source hash checked by conversion.  If the manifest contains a
    relative path that no longer exists, return ``None`` so the caller gets an
    explicit editable placeholder instead of a stale-looking command.
    """

    source = manifest.get("source")
    raw = source.get("path") if isinstance(source, Mapping) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    supplied = Path(raw).expanduser()
    candidates = [supplied]
    if not supplied.is_absolute():
        if manifest_path is not None:
            candidates.append(manifest_path.parent / supplied)
        candidates.append(project / supplied)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _source_kind(control: Mapping[str, Any]) -> str:
    explicit = str(control.get("reference_kind") or "").strip().lower()
    source = str(control.get("source") or control.get("control_source") or "").strip()
    upper = source.upper()
    if explicit == _RELATIVE_OSM_KIND or upper.startswith("RELATIVE_OSM_REFERENCE_ONLY"):
        return _RELATIVE_OSM_KIND
    if _OSM_SOURCE.search(source):
        return _RELATIVE_OSM_KIND
    if explicit == _SURVEYED_KIND or upper.startswith("SURVEYED_CONTROL"):
        return _SURVEYED_KIND
    if explicit == _AUTHORITATIVE_KIND or upper.startswith("AUTHORITATIVE_CONTROL"):
        return _AUTHORITATIVE_KIND
    return "unclassified"


def _empty_authority() -> dict[str, Any]:
    return {
        "active_control_count": 0,
        "active_train_count": 0,
        "active_check_count": 0,
        "surveyed_train_count": 0,
        "surveyed_check_count": 0,
        "authoritative_train_count": 0,
        "authoritative_check_count": 0,
        "relative_osm_count": 0,
        "unclassified_count": 0,
        "contains_relative_osm": False,
        "absolute_train_and_check_ready": False,
        "reference_scope": "no_active_controls",
        "issues": [],
    }


def _authority_summary(controls: Any) -> dict[str, Any]:
    summary = _empty_authority()
    if not isinstance(controls, Sequence) or isinstance(controls, (str, bytes, bytearray)):
        summary["issues"] = ["GCP controls must be a JSON array."]
        return summary
    issues: list[str] = []
    for index, raw in enumerate(controls):
        if not isinstance(raw, Mapping):
            issues.append(f"Control at index {index} is not an object.")
            continue
        if raw.get("enabled") is not True and raw.get("enabled") != 1:
            continue
        summary["active_control_count"] += 1
        point_id = str(raw.get("point_id") or f"index {index}")
        role = str(raw.get("role") or "").strip().lower()
        if role not in {"train", "check"}:
            issues.append(f"Control {point_id} has no valid frozen train/check role.")
        else:
            summary[f"active_{role}_count"] += 1
        kind = _source_kind(raw)
        if kind == _RELATIVE_OSM_KIND:
            summary["relative_osm_count"] += 1
        elif kind in _ABSOLUTE_KINDS and role in {"train", "check"}:
            summary[f"{kind.removesuffix('_control')}_{role}_count"] += 1
        else:
            summary["unclassified_count"] += 1
            issues.append(
                f"Control {point_id} lacks surveyed/authoritative source provenance."
            )
        for name in (
            "cad_x",
            "cad_y",
            "target_easting",
            "target_northing",
            "accuracy_m",
            "weight",
        ):
            try:
                value = _finite_number(raw.get(name), f"Control {point_id} {name}")
            except ValueError as exc:
                issues.append(str(exc))
                continue
            if name in {"accuracy_m", "weight"} and value <= 0.0:
                issues.append(f"Control {point_id} {name} must be greater than zero.")
    absolute_train = summary["surveyed_train_count"] + summary["authoritative_train_count"]
    absolute_check = summary["surveyed_check_count"] + summary["authoritative_check_count"]
    summary["contains_relative_osm"] = summary["relative_osm_count"] > 0
    summary["absolute_train_and_check_ready"] = (
        absolute_train >= MIN_ACTIVE_TRAIN_CONTROLS
        and absolute_check >= MIN_ACTIVE_CHECK_CONTROLS
    )
    if summary["contains_relative_osm"] and absolute_train + absolute_check:
        summary["reference_scope"] = "mixed_controls_include_relative_osm_not_absolute_ground_truth"
    elif summary["contains_relative_osm"]:
        summary["reference_scope"] = "relative_to_osm_snapshot_only_not_absolute_ground_truth"
    elif summary["active_control_count"]:
        summary["reference_scope"] = "surveyed_or_authoritative_as_declared_by_operator"
    summary["issues"] = list(dict.fromkeys(issues))
    return summary


def _capture_authority_summary(path: Path) -> dict[str, Any]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row[1]) for row in connection.execute('PRAGMA table_info("gcp_controls")')
        }
        required = {
            "point_id",
            "cad_x",
            "cad_y",
            "target_easting",
            "target_northing",
            "target_crs",
            "role",
            "control_source",
            "reference_kind",
            "accuracy_m",
            "weight",
            "enabled",
            "review_status",
        }
        missing = sorted(required - columns)
        if missing:
            raise ValueError("gcp_controls is missing fields: " + ", ".join(missing))
        rows = connection.execute(
            "SELECT point_id, cad_x, cad_y, target_easting, target_northing, "
            "target_crs, role, control_source, reference_kind, accuracy_m, weight, "
            "enabled, review_status FROM gcp_controls"
        ).fetchall()
    controls = []
    for row in rows:
        value = dict(row)
        value["source"] = value.pop("control_source")
        value["enabled"] = (
            value.get("enabled") == 1
            and str(value.get("review_status") or "").strip().lower() == "accepted"
        )
        controls.append(value)
    return _authority_summary(controls)


def _manifest_calibration(manifest: Mapping[str, Any]) -> dict[str, Any]:
    crs = manifest.get("crs")
    calibration = crs.get("calibration") if isinstance(crs, Mapping) else None
    if not isinstance(calibration, Mapping):
        return {
            "status": "not_provided",
            "validation_passed": False,
            "train_count": 0,
            "check_count": 0,
            "selected_model": None,
            "profile_sha256": None,
        }
    result = calibration.get("result")
    if not isinstance(result, Mapping):
        result = {}
    validation = result.get("validation")
    train_metrics = result.get("train_metrics")
    check_metrics = result.get("check_metrics")
    return {
        "status": str(calibration.get("status") or "unknown"),
        "validation_passed": (
            validation.get("passed") is True if isinstance(validation, Mapping) else False
        ),
        "train_count": _nonnegative_int(
            train_metrics.get("count") if isinstance(train_metrics, Mapping) else None
        ),
        "check_count": _nonnegative_int(
            check_metrics.get("count") if isinstance(check_metrics, Mapping) else None
        ),
        "selected_model": result.get("selected_model"),
        "profile_sha256": calibration.get("profile_sha256"),
    }


def _check_profile_bindings(
    manifest: Mapping[str, Any],
    calibration: Mapping[str, Any],
    profile: Mapping[str, Any],
    profile_hash: str | None,
    blockers: list[str],
) -> None:
    if not profile or profile_hash is None:
        return
    expected_hashes: list[tuple[str, Any]] = [
        ("calibration", calibration.get("profile_sha256")),
    ]
    profiles = manifest.get("profiles")
    if isinstance(profiles, Mapping):
        entry = profiles.get("gcp_profile")
        if isinstance(entry, Mapping):
            expected_hashes.append(("manifest profile", entry.get("sha256")))
    for label, expected in expected_hashes:
        if isinstance(expected, str) and expected and expected != profile_hash:
            blockers.append(
                f"GCP profile SHA-256 differs from the {label} binding in run_manifest.json."
            )
    source = manifest.get("source")
    manifest_source_hash = source.get("sha256") if isinstance(source, Mapping) else None
    profile_source_hash = profile.get("source_sha256")
    if (
        isinstance(manifest_source_hash, str)
        and isinstance(profile_source_hash, str)
        and manifest_source_hash != profile_source_hash
    ):
        blockers.append("GCP profile source_sha256 differs from the published source DWG.")
