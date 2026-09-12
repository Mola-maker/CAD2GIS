"""Source-bound, bounded parallel orchestration of canonical reverse conversion."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Sequence

from .cad2gis_v3.artifact_io import inherit_output_permissions
from .reverse_models import GISDataset, ReverseConversionError


SCHEMA = "cad2gis.reverse-batch.v1"
REPORT_SCHEMA = "cad2gis.reverse-batch-report.v1"
_DEFAULTS = {"layers": None, "source_crs": None, "target_crs": None,
             "label_field": None, "layer_field": None, "label_height": 2.5,
             "registration_manifest": None, "partition": None, "output_format": "DXF"}
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED = {"con", "prn", "aux", "nul", "clock$", *[f"com{i}" for i in range(1, 10)],
             *[f"lpt{i}" for i in range(1, 10)]}


def _invalid(message: str, *, code: str = "REVERSE_BATCH_INVALID") -> None:
    raise ReverseConversionError(message, code=code)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _options(value: Any, *, from_plan: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(_DEFAULTS):
        _invalid("Batch options must contain only documented reverse conversion options.")
    result = {**_DEFAULTS, **copy.deepcopy(value)}
    if not isinstance(result["output_format"], str) or result["output_format"].upper() not in ("DXF", "DWG"):
        _invalid("output_format must be DXF or DWG.")
    result["output_format"] = result["output_format"].upper()
    layers = result["layers"]
    if layers is not None and (not isinstance(layers, list) or not layers
            or any(not isinstance(v, str) or not v.strip() or "\0" in v for v in layers)
            or len(set(layers)) != len(layers)):
        _invalid("layers must be a nonempty list of unique layer names or null.")
    for key in ("source_crs", "target_crs", "label_field", "layer_field", "partition"):
        item = result[key]
        if item is not None and (not isinstance(item, str) or not item.strip() or "\0" in item):
            _invalid(f"{key} must be a nonempty string or null.")
    height = result["label_height"]
    if isinstance(height, bool) or not isinstance(height, (float, int)) or not math.isfinite(height) or height <= 0:
        _invalid("label_height must be finite and positive.")
    registration = result["registration_manifest"]
    if registration is not None:
        if not isinstance(registration, str if from_plan else (str, Path)) or not str(registration).strip():
            _invalid("registration_manifest must be a file path or null.")
        path = Path(registration).expanduser()
        if from_plan and not path.is_absolute():
            _invalid("Plan registration_manifest paths must be absolute.")
        result["registration_manifest"] = str(path.resolve())
        if result["source_crs"] is not None or result["target_crs"] is not None:
            _invalid("Native coordinate recovery cannot be combined with CRS overrides.")
    elif result["partition"] is not None:
        _invalid("partition requires registration_manifest.")
    return result


def _assert_hashes(job: dict[str, Any]) -> None:
    from .reverse import _check_source_idle

    source = Path(job["source"])
    if not source.is_file():
        _invalid(f"GIS source does not exist: {source}", code="SOURCE_NOT_FOUND")
    _check_source_idle(source)
    if _sha256(source) != job["source_sha256"]:
        _invalid(f"GIS source changed since batch preparation: {source}", code="REVERSE_SOURCE_CHANGED")
    manifest = job["options"]["registration_manifest"]
    if manifest is not None:
        path = Path(manifest)
        if not path.is_file() or _sha256(path) != job["registration_manifest_sha256"]:
            _invalid(f"Registration manifest changed or is missing: {path}", code="REVERSE_SOURCE_CHANGED")


def _inspect_job(job: dict[str, Any], *, validate_coordinates: bool) -> dict[str, Any]:
    from . import reverse

    _assert_hashes(job)
    options = job["options"]
    inspection = reverse.inspect_gis(job["source"], layers=options["layers"])
    if inspection["source_sha256"] != job["source_sha256"]:
        _invalid("GIS source changed while inspecting batch job.", code="REVERSE_SOURCE_CHANGED")
    if not inspection["feature_count"]:
        _invalid("Selected GIS layers contain no features.", code="REVERSE_EMPTY_INPUT")
    if validate_coordinates:
        if options["output_format"] == "DWG":
            from .reverse_dwg import resolve_runtime

            resolve_runtime()
        dataset = None
        if any(options[key] is not None for key in ("registration_manifest", "label_field", "layer_field")):
            dataset, digest = reverse._load(Path(job["source"]), options["layers"])
            if digest != job["source_sha256"]:
                _invalid("GIS source changed during option preflight.", code="REVERSE_SOURCE_CHANGED")
        if options["label_field"] is not None or options["layer_field"] is not None:
            from .reverse_writer import _literal_mtext, _requested_layer

            for feature in dataset.features:
                _requested_layer(feature, options["layer_field"])
                field = options["label_field"]
                if field is not None:
                    if field not in feature.properties:
                        _invalid(f"Feature {feature.feature_id!r} has no label field {field!r}.")
                    label = feature.properties[field]
                    if label is not None and label != "":
                        _literal_mtext(label)
        if options["registration_manifest"] is None:
            crs_dataset = GISDataset([], {layer["name"]: layer["crs"] for layer in inspection["layers"]})
            target, _ = reverse._target(crs_dataset, options["source_crs"], options["target_crs"])
            reverse._units(target)
        else:
            # Native recovery requires actual source-bound registration facts,
            # including a validated reversible operation and explicit partition.
            from .reverse_registration import load_registration

            load_registration(Path(options["registration_manifest"]), Path(job["source"]), dataset,
                              partition=options["partition"])
    _assert_hashes(job)
    return inspection


def prepare_batch(sources: Sequence[Path], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inspect all sources and return a JSON-serializable plan without writing files."""
    if isinstance(sources, (str, bytes, Path)) or not isinstance(sources, Sequence) or not sources:
        _invalid("Provide at least one GIS source path.")
    normalized = _options({} if options is None else options)
    jobs = []
    for index, value in enumerate(sources, 1):
        if not isinstance(value, (str, Path)) or not str(value).strip():
            _invalid("Each source must be a file path.")
        source = Path(value).expanduser().resolve()
        if not source.is_file():
            _invalid(f"GIS source does not exist: {source}", code="SOURCE_NOT_FOUND")
        slug = re.sub(r"[^a-z0-9_-]+", "-", source.stem.lower()).strip("-_")[:40] or "source"
        job = {"job_id": f"job-{index:04d}-{slug}", "source": str(source),
               "source_sha256": _sha256(source), "options": copy.deepcopy(normalized)}
        manifest = normalized["registration_manifest"]
        if manifest is not None:
            if not Path(manifest).is_file():
                _invalid(f"Registration manifest does not exist: {manifest}")
            job["registration_manifest_sha256"] = _sha256(Path(manifest))
        jobs.append(job)
    plan = {"schema": SCHEMA, "jobs": jobs}
    _validate_plan(plan)
    return plan


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid(f"Duplicate batch plan JSON member: {key}")
        result[key] = value
    return result


def _validate_plan(value: dict[str, Any] | Path) -> dict[str, Any]:
    if isinstance(value, (str, Path)):
        try:
            value = json.loads(Path(value).read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (OSError, json.JSONDecodeError) as exc:
            _invalid(f"Cannot read batch plan: {exc}")
    if not isinstance(value, dict) or set(value) != {"schema", "jobs"} or value["schema"] != SCHEMA:
        _invalid(f"Batch plan must use schema {SCHEMA} with schema and jobs members.")
    jobs = value["jobs"]
    if not isinstance(jobs, list) or not jobs:
        _invalid("Batch plan requires a nonempty jobs list.")
    plan = copy.deepcopy(value)
    identifiers, sources, file_ids = set(), set(), set()
    for job in plan["jobs"]:
        required = {"job_id", "source", "source_sha256", "options"}
        if not isinstance(job, dict) or not required <= set(job) or set(job) - required - {"registration_manifest_sha256"}:
            _invalid("Each job requires job_id, source, source_sha256 and options.")
        identifier = job["job_id"]
        if (not isinstance(identifier, str) or not _JOB_ID.fullmatch(identifier)
                or identifier.casefold() in _RESERVED or identifier.casefold() in identifiers):
            _invalid(f"Invalid or duplicate batch job_id: {identifier!r}")
        identifiers.add(identifier.casefold())
        if not isinstance(job["source"], str) or not Path(job["source"]).is_absolute():
            _invalid("Plan source paths must be absolute strings.")
        source = Path(job["source"]).resolve()
        if str(source).casefold() in sources:
            _invalid(f"Duplicate batch source path: {source}")
        sources.add(str(source).casefold())
        if not isinstance(job["source_sha256"], str) or not _DIGEST.fullmatch(job["source_sha256"]):
            _invalid("Each source_sha256 must be a lowercase SHA-256 digest.")
        job["source"] = str(source)
        job["options"] = _options(job["options"], from_plan=True)
        registration_hash = job.get("registration_manifest_sha256")
        if job["options"]["registration_manifest"] is not None:
            if not isinstance(registration_hash, str) or not _DIGEST.fullmatch(registration_hash):
                _invalid("Native registration jobs require registration_manifest_sha256.")
        elif "registration_manifest_sha256" in job:
            _invalid("registration_manifest_sha256 requires registration_manifest.")
        _assert_hashes(job)
        stat = source.stat()
        identity = (stat.st_dev, stat.st_ino)
        if stat.st_ino and identity in file_ids:
            _invalid(f"Duplicate batch source file (possibly a hard link): {source}")
        file_ids.add(identity)
    # Complete structural validation first, then inspect every input read-only.
    for job in plan["jobs"]:
        _inspect_job(job, validate_coordinates=True)
    return plan


def _write_json(path: Path, payload: dict[str, Any], *, exclusive: bool = False) -> None:
    """Publish complete JSON with inherited ACLs; exclusive mode cannot overwrite."""
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        inherit_output_permissions(temporary)
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _execute_job(job: dict[str, Any], output: Path) -> dict[str, Any]:
    from .reverse import reverse_convert
    from .reverse_verify import verify_reverse

    result = {**job, "run_dir": str(output), "status": "failed", "output": None,
              "verification": None, "error": None, "retained_artifacts": []}
    try:
        result["inspection"] = _inspect_job(job, validate_coordinates=False)
        options = job["options"]
        # Explicit arguments prevent plans from injecting unvalidated converter kwargs.
        converted = reverse_convert(job["source"], run_dir=output, layers=options["layers"],
            source_crs=options["source_crs"], target_crs=options["target_crs"],
            label_field=options["label_field"], layer_field=options["layer_field"],
            label_height=options["label_height"], registration_manifest=options["registration_manifest"],
            partition=options["partition"], output_format=options["output_format"])
        result["output"] = converted
        if converted["source_sha256"] != job["source_sha256"]:
            _invalid("Converted source differs from the batch plan.", code="REVERSE_SOURCE_CHANGED")
        if options["registration_manifest"] is not None and (
                converted.get("registration", {}).get("manifest_sha256") != job["registration_manifest_sha256"]):
            _invalid("Converted registration differs from the batch plan.", code="REVERSE_SOURCE_CHANGED")
        _assert_hashes(job)
        verified = verify_reverse(output, source=Path(job["source"]))
        if verified.get("status") != "verified":
            _invalid("Canonical reverse verifier did not verify this job.", code="REVERSE_VERIFICATION_FAILED")
        _assert_hashes(job)
        result["verification"] = verified
        result["status"] = "verified"
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", "REVERSE_BATCH_JOB_FAILED"),
                           "message": str(exc)}
        retained = getattr(exc, "retained_artifact_path", None)
        if retained is not None:
            result["retained_artifacts"].append(str(retained))
        details = getattr(exc, "details", {})
        if isinstance(details, dict):
            result["error"]["details"] = {key: details[key] for key in (
                "unverified_workspace", "workspace", "run_published", "process_may_be_running", "log_path", "pid"
            ) if key in details}
    result["retained_artifacts"].extend(str(path) for path in sorted(output.glob("*")) if path.is_file())
    lock = output.parent / f".{output.name}.reverse.lock"
    if lock.exists():
        result["retained_artifacts"].append(str(lock))
    return result


def run_batch(plan: dict[str, Any] | Path, *, run_dir: Path, workers: int = 2) -> dict[str, Any]:
    """Validate the whole plan, then run 1..8 independent inspect/convert/verify jobs."""
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
        _invalid("workers must be an integer between 1 and 8.")
    output = Path(run_dir).expanduser().absolute()
    if output.exists() or output.is_symlink():
        _invalid(f"Batch run directory already exists: {output}", code="REVERSE_OUTPUT_EXISTS")
    prepared = _validate_plan(plan)
    # Validate the final hashes once more after every job's preflight, before
    # making any output directory. Workers repeat this check at execution time.
    for job in prepared["jobs"]:
        _assert_hashes(job)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
    except FileExistsError:
        _invalid(f"Batch run directory already exists: {output}", code="REVERSE_OUTPUT_EXISTS")
    report = {"schema": REPORT_SCHEMA, "status": "running", "run_dir": str(output),
              "workers": workers, "job_count": len(prepared["jobs"]), "verified_count": 0,
              "failed_count": 0, "plan_path": str(output / "batch_plan.json"), "jobs": []}
    try:
        inherit_output_permissions(output)
        _write_json(output / "batch_plan.json", prepared, exclusive=True)
        report["plan_sha256"] = _sha256(output / "batch_plan.json")
        _write_json(output / "batch_report.json", report, exclusive=True)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cad2gis-reverse") as executor:
            futures = [executor.submit(_execute_job, job, output / job["job_id"]) for job in prepared["jobs"]]
            report["jobs"] = [future.result() for future in futures]
        report["verified_count"] = sum(job["status"] == "verified" for job in report["jobs"])
        report["failed_count"] = len(report["jobs"]) - report["verified_count"]
        report["status"] = ("complete" if not report["failed_count"] else
                            "partial_failed" if report["verified_count"] else "failed")
        _write_json(output / "batch_report.json", report)
    except Exception as exc:
        exc.retained_artifact_path = output
        raise
    return report


def register(commands: Any) -> None:
    command = commands.add_parser("reverse-batch", help="Prepare and execute a source-bound parallel GIS-to-CAD plan.")
    actions = command.add_subparsers(dest="reverse_batch_command", required=True)
    prepare = actions.add_parser("prepare", help="Inspect inputs and save a new source-hash-bound plan.")
    prepare.add_argument("sources", type=Path, nargs="+")
    prepare.add_argument("--plan", type=Path, required=True, help="New plan JSON path; never overwritten.")
    prepare.add_argument("--layer", dest="layers", action="append")
    for option in ("source-crs", "target-crs", "label-field", "layer-field", "partition"):
        prepare.add_argument(f"--{option}")
    prepare.add_argument("--registration-manifest", type=Path)
    prepare.add_argument("--label-height", type=float, default=2.5)
    prepare.add_argument("--output-format", choices=("DXF", "DWG"), default="DXF")
    prepare.add_argument("--json", action="store_true")
    prepare.set_defaults(handler=_prepare_command)
    run = actions.add_parser("run", help="Run every job through inspect, convert and source verification.")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--workers", type=int, default=2)
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=_run_command)


def _prepare_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    destination = args.plan.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        _invalid(f"Batch plan already exists: {destination}", code="REVERSE_OUTPUT_EXISTS")
    plan = prepare_batch(args.sources, options={key: getattr(args, key) for key in _DEFAULTS})
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_json(destination, plan, exclusive=True)
    except FileExistsError:
        _invalid(f"Batch plan already exists: {destination}", code="REVERSE_OUTPUT_EXISTS")
    return {"schema": SCHEMA, "status": "prepared", "plan_path": str(destination),
            "plan_sha256": _sha256(destination), "job_count": len(plan["jobs"])}, 0


def _run_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    report = run_batch(args.plan, run_dir=args.run_dir, workers=args.workers)
    return report, 0 if report["status"] == "complete" else 2
