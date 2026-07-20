"""Canonical, dependency-light CAD2GIS command line interface."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from . import __version__


class CLIUsageError(ValueError):
    """A command is syntactically valid but lacks an operational input."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cad2gis",
        description=(
            "Deterministic CAD-to-GIS conversion, project onboarding, ground-control, "
            "and verification."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show Python tracebacks for diagnostics (accepted before or after a command).",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    doctor = commands.add_parser("doctor", help="Check runtime and backend readiness.")
    doctor.add_argument("--json", action="store_true", help="Emit the structured report.")
    doctor.add_argument(
        "--deep", action="store_true", help="Import native modules and the backend."
    )
    doctor.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero status when configured conversion is not ready.",
    )
    doctor.set_defaults(handler=_doctor)

    inspect = commands.add_parser(
        "inspect", help="Inventory a source and propose an unreviewed project profile."
    )
    _add_source(inspect)
    inspect.add_argument("--project", dest="project_dir", type=Path)
    _add_json(inspect)
    inspect.set_defaults(handler=_inspect)

    bootstrap = commands.add_parser(
        "bootstrap", help="Create a draft project configuration for operator review."
    )
    _add_source(bootstrap)
    bootstrap.add_argument("project_dir", nargs="?", type=Path, metavar="PROJECT_DIR")
    bootstrap.add_argument("--project", dest="project_option", type=Path)
    bootstrap.add_argument("--force", action="store_true")
    _add_json(bootstrap)
    bootstrap.set_defaults(handler=_bootstrap)

    validate = commands.add_parser(
        "validate", help="Validate that a project configuration is reviewed and usable."
    )
    validate.add_argument("project_dir", nargs="?", type=Path, metavar="PROJECT_DIR")
    validate.add_argument("--project", dest="project_option", type=Path)
    _add_json(validate)
    validate.set_defaults(handler=_validate)

    convert = commands.add_parser(
        "convert", help="Run the canonical architecture-v3 conversion."
    )
    _add_source(convert)
    convert.add_argument("--run-dir", required=True, type=Path, help="New run directory.")
    convert.add_argument(
        "--project", dest="project_dir", type=Path, help="Reviewed project/config directory."
    )
    convert.add_argument("--source-profile", type=Path)
    convert.add_argument("--mapping-registry", type=Path)
    convert.add_argument("--gcp-profile", type=Path)
    _add_json(convert)
    convert.set_defaults(handler=_convert)

    gcp = commands.add_parser(
        "gcp", help="Prepare and review operator-supplied ground control."
    )
    gcp_commands = gcp.add_subparsers(
        dest="gcp_command", metavar="GCP_COMMAND", required=True
    )
    _add_gcp_status(gcp_commands)
    _add_gcp_prepare(gcp_commands)
    _add_gcp_diagnose(gcp_commands)
    _add_gcp_export(gcp_commands)

    verify = commands.add_parser(
        "verify", help="Evaluate a versioned multi-CAD verification matrix."
    )
    verify.add_argument("matrix", type=Path, help="Verification matrix JSON path.")
    _add_json(verify)
    verify.set_defaults(handler=_verify)
    return parser


def _add_source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", nargs="?", type=Path, metavar="SOURCE")
    parser.add_argument(
        "--input", dest="source_option", type=Path, help="Compatibility alias for SOURCE."
    )


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def _add_gcp_status(commands: argparse._SubParsersAction[Any]) -> None:
    command = commands.add_parser("status", help="Report ground-control readiness.")
    command.add_argument("--project", required=True, type=Path)
    _add_json(command)
    command.set_defaults(handler=_gcp_status)


def _add_gcp_prepare(commands: argparse._SubParsersAction[Any]) -> None:
    command = commands.add_parser("prepare", help="Create an editable GCP capture.")
    command.add_argument("--project", type=Path)
    command.add_argument("--delivery", type=Path)
    command.add_argument("--evidence", type=Path)
    command.add_argument("--manifest", type=Path)
    command.add_argument("--output", type=Path)
    command.add_argument(
        "--candidate-layer",
        action="append",
        dest="candidate_layers",
        metavar="LAYER",
        help="Repeat for each candidate point layer.",
    )
    command.add_argument("--force", action="store_true")
    _add_json(command)
    command.set_defaults(handler=_gcp_prepare)


def _add_gcp_diagnose(commands: argparse._SubParsersAction[Any]) -> None:
    command = commands.add_parser("diagnose", help="Fit diagnostic-only GCP candidates.")
    command.add_argument("--project", type=Path)
    command.add_argument("--capture", type=Path)
    command.add_argument("--report", type=Path)
    command.add_argument("--robust-outlier-threshold-m", type=float)
    command.add_argument("--force", action="store_true")
    _add_json(command)
    command.set_defaults(handler=_gcp_diagnose)


def _add_gcp_export(commands: argparse._SubParsersAction[Any]) -> None:
    command = commands.add_parser(
        "export", help="Export a reviewed profile without claiming publication accuracy."
    )
    command.add_argument("--project", type=Path)
    command.add_argument("--capture", type=Path)
    command.add_argument("--template-profile", type=Path)
    command.add_argument("--output", type=Path)
    command.add_argument("--diagnostic-report", type=Path)
    command.add_argument(
        "--model", choices=("auto", "translation", "similarity", "affine")
    )
    command.add_argument("--spatial-review-source")
    command.add_argument("--max-check-error-m", type=float)
    command.add_argument("--max-pivot-shift-m", type=float)
    command.add_argument("--max-abs-rotation-deg", type=float)
    command.add_argument("--max-scale-deviation-ratio", type=float)
    command.add_argument("--max-affine-condition-number", type=float)
    command.add_argument("--robust-outlier-threshold-m", type=float)
    command.add_argument("--disable-robust", action="store_true")
    command.add_argument("--affine-min-improvement-ratio", type=float)
    command.add_argument("--affine-structure-reviewed", action="store_true")
    command.add_argument("--allow-relative-osm", action="store_true")
    command.add_argument("--enable", action="store_true")
    command.add_argument("--force", action="store_true")
    _add_json(command)
    command.set_defaults(handler=_gcp_export)


def _exclusive_path(positional: Path | None, option: Path | None, name: str) -> Path:
    if positional is not None and option is not None:
        raise CLIUsageError(f"pass {name} either positionally or by option, not both")
    value = positional if positional is not None else option
    if value is None:
        raise CLIUsageError(f"{name} is required")
    return value


def _source(args: argparse.Namespace) -> Path:
    return _exclusive_path(args.source, args.source_option, "source")


def _doctor(args: argparse.Namespace) -> tuple[Any, int]:
    from .doctor import render_report

    report, rendered = render_report(as_json=args.json, deep=args.deep)
    print(rendered)
    return None, 2 if args.strict and not report["conversion_ready"] else 0


def _inspect(args: argparse.Namespace) -> tuple[Any, int]:
    from .pipeline import inspect_source

    result = inspect_source(source=_source(args), project_dir=args.project_dir)
    return result, 0


def _bootstrap(args: argparse.Namespace) -> tuple[Any, int]:
    from .pipeline import bootstrap_project

    project = _exclusive_path(args.project_dir, args.project_option, "project directory")
    result = bootstrap_project(source=_source(args), project_dir=project, force=args.force)
    return result, 0


def _validate(args: argparse.Namespace) -> tuple[Any, int]:
    from .pipeline import validate_project

    project = _exclusive_path(args.project_dir, args.project_option, "project directory")
    result = validate_project(project_dir=project)
    payload = _jsonable(result)
    valid = not (
        isinstance(payload, Mapping)
        and (payload.get("valid") is False or payload.get("status") in {"blocked", "invalid"})
    )
    return result, 0 if valid else 2


def _convert(args: argparse.Namespace) -> tuple[Any, int]:
    from .pipeline import convert_project

    result = convert_project(
        source=_source(args),
        run_dir=args.run_dir,
        project_dir=args.project_dir,
        source_profile=args.source_profile,
        mapping_registry=args.mapping_registry,
        gcp_profile=args.gcp_profile,
    )
    return _conversion_payload(result), 0


def _project_directory(value: Path | None) -> Path | None:
    if value is None:
        return None
    project = value.expanduser().resolve()
    if not project.is_dir():
        raise CLIUsageError(f"project directory does not exist: {project}")
    return project


def _input_path(
    *,
    explicit: Path | None,
    project: Path | None,
    label: str,
    preferred: Sequence[str],
    patterns: Sequence[str],
) -> Path:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise CLIUsageError(f"{label} does not exist: {path}")
        return path
    if project is None:
        raise CLIUsageError(f"--{label.replace('_', '-')} or --project is required")
    for name in preferred:
        candidate = project / name
        if candidate.is_file():
            return candidate.resolve()
    matches = sorted(
        {
            candidate.resolve()
            for pattern in patterns
            for candidate in project.glob(pattern)
            if candidate.is_file()
        },
        key=lambda path: (path.name.casefold(), str(path).casefold()),
    )
    if not matches:
        raise CLIUsageError(f"could not discover {label} under {project}")
    if len(matches) > 1:
        rendered = ", ".join(str(path) for path in matches)
        raise CLIUsageError(f"{label} is ambiguous under {project}: {rendered}")
    return matches[0]


def _output_path(
    explicit: Path | None, project: Path | None, default_name: str, label: str
) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    if project is None:
        raise CLIUsageError(f"--{label.replace('_', '-')} or --project is required")
    return (project / default_name).resolve()


def _gcp_status(args: argparse.Namespace) -> tuple[Any, int]:
    from .gcp_workflow import status_project

    return status_project(args.project), 0


def _gcp_prepare(args: argparse.Namespace) -> tuple[Any, int]:
    from .gcp_workflow import DEFAULT_CANDIDATE_LAYERS, PrepareRequest, prepare_capture

    project = _project_directory(args.project)
    delivery = _input_path(
        explicit=args.delivery,
        project=project,
        label="delivery",
        preferred=("delivery.gpkg", "apd_delivery.gpkg"),
        patterns=("*_delivery.gpkg",),
    )
    evidence = _input_path(
        explicit=args.evidence,
        project=project,
        label="evidence",
        preferred=("evidence.gpkg", "apd_evidence.gpkg"),
        patterns=("*_evidence.gpkg",),
    )
    manifest = _input_path(
        explicit=args.manifest,
        project=project,
        label="manifest",
        preferred=("run_manifest.json",),
        patterns=("*_run_manifest.json", "*run*manifest*.json"),
    )
    output = _output_path(args.output, project, "gcp_capture.gpkg", "output")
    request = PrepareRequest(
        delivery_path=delivery,
        evidence_path=evidence,
        manifest_path=manifest,
        output_path=output,
        candidate_layers=tuple(args.candidate_layers or DEFAULT_CANDIDATE_LAYERS),
        force=args.force,
    )
    return prepare_capture(request, raise_on_error=args.debug), 0


def _gcp_diagnose(args: argparse.Namespace) -> tuple[Any, int]:
    from .gcp_workflow import DiagnoseRequest, diagnose_capture

    project = _project_directory(args.project)
    capture = _input_path(
        explicit=args.capture,
        project=project,
        label="capture",
        preferred=("gcp_capture.gpkg", "capture.gpkg"),
        patterns=("*gcp*capture*.gpkg",),
    )
    report = _output_path(
        args.report, project or capture.parent, "gcp_diagnostic_report.json", "report"
    )
    request = DiagnoseRequest(
        capture_path=capture,
        report_path=report,
        robust_outlier_threshold_m=args.robust_outlier_threshold_m,
        force=args.force,
    )
    return diagnose_capture(request, raise_on_error=args.debug), 0


def _gcp_export(args: argparse.Namespace) -> tuple[Any, int]:
    from .gcp_workflow import ExportReviewedProfileRequest, export_reviewed_profile

    project = _project_directory(args.project)
    capture = _input_path(
        explicit=args.capture,
        project=project,
        label="capture",
        preferred=("gcp_capture.gpkg", "capture.gpkg"),
        patterns=("*gcp*capture*.gpkg",),
    )
    template = _input_path(
        explicit=args.template_profile,
        project=project,
        label="template_profile",
        preferred=("gcp_profile.json",),
        patterns=("*_gcp_profile.json", "*gcp*profile*.json"),
    )
    output = _output_path(
        args.output, project or capture.parent, "reviewed_gcp_profile.json", "output"
    )
    diagnostic: Path | None = args.diagnostic_report
    if diagnostic is None and project is not None:
        candidate = project / "gcp_diagnostic_report.json"
        if candidate.is_file():
            diagnostic = candidate
    if diagnostic is not None:
        diagnostic = diagnostic.expanduser().resolve()
        if not diagnostic.is_file():
            raise CLIUsageError(f"diagnostic report does not exist: {diagnostic}")
    request = ExportReviewedProfileRequest(
        capture_path=capture,
        template_profile_path=template,
        output_path=output,
        diagnostic_report_path=diagnostic,
        enable=args.enable,
        requested_model=args.model,
        spatial_review_source=args.spatial_review_source,
        max_check_error_m=args.max_check_error_m,
        max_pivot_shift_m=args.max_pivot_shift_m,
        max_abs_rotation_deg=args.max_abs_rotation_deg,
        max_scale_deviation_ratio=args.max_scale_deviation_ratio,
        max_affine_condition_number=args.max_affine_condition_number,
        robust_outlier_threshold_m=args.robust_outlier_threshold_m,
        disable_robust=args.disable_robust,
        affine_min_improvement_ratio=args.affine_min_improvement_ratio,
        affine_structure_reviewed=args.affine_structure_reviewed,
        allow_relative_osm=args.allow_relative_osm,
        force=args.force,
    )
    return export_reviewed_profile(request, raise_on_error=args.debug), 0


def _verify(args: argparse.Namespace) -> tuple[Any, int]:
    # Delayed import keeps help/doctor independent of verification internals.
    from .verify import evaluate_matrix, strongest_allowed_claim

    matrix = args.matrix.expanduser().resolve()
    if not matrix.is_file():
        raise CLIUsageError(f"verification matrix does not exist: {matrix}")
    report = evaluate_matrix(matrix)
    payload = dict(report)
    payload["strongest_allowed_claim"] = strongest_allowed_claim(report)
    return payload, 0


def _conversion_payload(result: Any) -> Any:
    if isinstance(result, Mapping):
        return result
    names = {
        "evidence": "evidence_path",
        "delivery": "delivery_path",
        "styles": "style_manifest_path",
        "manifest": "run_manifest_path",
    }
    payload: dict[str, Any] = {"status": "success"}
    found = False
    for output_name, attribute in names.items():
        if hasattr(result, attribute):
            payload[output_name] = getattr(result, attribute)
            found = True
    if hasattr(result, "counts"):
        payload["counts"] = getattr(result, "counts")
        found = True
    diagnostics = getattr(result, "diagnostics", None)
    if isinstance(diagnostics, Mapping):
        topology = diagnostics.get("topology")
        if isinstance(topology, Mapping):
            payload["topology"] = {
                key: value
                for key, value in topology.items()
                if key != "connection_port_candidates"
            }
            found = True
    return payload if found else result


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted((_jsonable(item) for item in value), key=repr)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    return str(value)


def _emit(value: Any, *, compact: bool = False) -> None:
    if value is None:
        return
    kwargs = {"ensure_ascii": False, "sort_keys": True}
    if compact:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = 2
    print(json.dumps(_jsonable(value), **kwargs))


def _extract_debug(argv: Sequence[str]) -> tuple[list[str], bool]:
    debug = False
    filtered: list[str] = []
    for argument in argv:
        if argument == "--debug":
            debug = True
        else:
            filtered.append(argument)
    return filtered, debug


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; ordinary failures never print a traceback by default."""

    raw = list(sys.argv[1:] if argv is None else argv)
    filtered, debug = _extract_debug(raw)
    parser = _parser()
    args = parser.parse_args(filtered)
    args.debug = bool(args.debug or debug)
    handler: Callable[[argparse.Namespace], tuple[Any, int]] = args.handler
    try:
        value, status = handler(args)
        if value is not None:
            _emit(value, compact=False)
        return status
    except BrokenPipeError:
        return 0
    except Exception as exc:
        if args.debug:
            raise
        payload = {
            "status": "error",
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"cad2gis: error: {exc}", file=sys.stderr)
            print("Run with --debug for a traceback.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
