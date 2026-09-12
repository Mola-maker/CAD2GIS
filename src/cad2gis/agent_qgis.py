"""Authorized MCP adapters for the canonical QGIS delivery and audit tools."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .batch import digest
from . import match_delivery


def _output(value: str) -> Path:
    from .agent_mcp import MCPServiceError, _path

    path = _path(value, must_exist=False)
    for parent in (path, *path.parents):
        if any((parent / name).is_file() for name in ("run_manifest.json", "source_manifest.json", "partition_manifest.json")):
            raise MCPServiceError("Output must be outside immutable conversion and source runs")
    if path.exists():
        raise MCPServiceError("Output already exists; choose a new path", code="OUTPUT_EXISTS")
    return path


def _project_inputs(project: Path, *, cad_hashes: set[str] | None = None) -> dict[str, str]:
    """Admit local GeoPackages or embedded attachments before QGIS reads them."""
    from .agent_mcp import MCPServiceError, _path
    from .qgis_review import OSM_URL, OSM_REVIEW_GROUP

    inputs = {str(project): digest(project)}
    with zipfile.ZipFile(project) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or any(
            Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name
            for name in names
        ):
            raise MCPServiceError("Unsafe QGZ archive entries")
        documents = [name for name in names if name.endswith(".qgs")]
        if len(documents) != 1:
            raise MCPServiceError("QGZ must contain one project document")
        document = ET.fromstring(archive.read(documents[0]))
        osm_ids = {layer.get("id") for group in document.iter("layer-tree-group")
                   if group.get("name") == OSM_REVIEW_GROUP for layer in group.iter("layer-tree-layer")}
        for layer in document.findall("projectlayers/maplayer"):
            source = layer.findtext("datasource") or ""
            provider = layer.findtext("provider")
            if provider == "wms" and unquote(source) == "type=xyz&url=" + OSM_URL + "&zmin=0&zmax=19":
                continue  # Canonical optional OSM tiles; offline verification hides them.
            if provider != "ogr" or "|layername=" not in source:
                raise MCPServiceError("QGIS review accepts only local GeoPackage layers")
            database = source.split("|", 1)[0]
            if database.startswith("attachment:"):
                attachment = database.removeprefix("attachment:")
                if attachment not in names or not attachment.endswith(".gpkg"):
                    raise MCPServiceError("Missing QGZ GeoPackage attachment")
                with archive.open(attachment) as stream:
                    database_sha = hashlib.file_digest(stream, "sha256").hexdigest()
            else:
                supplied = (project.parent / database).absolute()
                if any(path.is_symlink() or getattr(path, "is_junction", lambda: False)()
                       for path in (supplied, *supplied.parents)):
                    raise MCPServiceError("QGIS data sources must not use symlinks or junctions")
                path = _path(supplied)
                if path.suffix.lower() != ".gpkg":
                    raise MCPServiceError("QGIS review accepts only local GeoPackage layers")
                database_sha = digest(path)
                inputs[str(path)] = database_sha
            if cad_hashes is not None and layer.findtext("id") not in osm_ids:
                cad_hashes.add(database_sha)
    return inputs


def _unchanged(inputs: dict[str, str]) -> None:
    from .agent_mcp import MCPServiceError

    if any(digest(Path(path)) != expected for path, expected in inputs.items()):
        raise MCPServiceError("Input artifacts changed during QGIS processing")


def build_source_svg_delivery(
    baseline_dir: str, assets_dir: str, output_dir: str,
) -> dict[str, Any]:
    """Apply hash-bound source-instance SVGs to a new canonical review package.

    This preserves CAD database bytes and produces candidate review projects;
    it does not accept a semantic interpretation or an OSM matching revision.
    """
    from .agent_mcp import MCPServiceError, _path, _qgis_source_inventory

    baseline, assets, output = _path(baseline_dir), _path(assets_dir), _output(output_dir)
    if output.is_relative_to(baseline) or output.is_relative_to(assets):
        raise MCPServiceError("SVG output must be outside its immutable inputs")
    try:
        manifest = json.loads(_path(baseline / "delivery-manifest.json").read_text(encoding="utf-8"))
        correspondence = json.loads(_path(assets / "correspondence.json").read_text(encoding="utf-8"))
        source_sha = manifest.get("source_sha256")
        if not source_sha or correspondence.get("source_sha256") != source_sha:
            raise MCPServiceError("SVG assets and baseline have different source bindings")
        before = {str(base / relative): sha for base in (baseline, assets)
                  for relative, sha in _qgis_source_inventory(base).items()}
        for entry in manifest["deliveries"]:
            project = _path(baseline / entry["project"])
            if not project.is_relative_to(baseline):
                raise MCPServiceError("Baseline project escapes its package")
            _project_inputs(project)
        match_delivery._run_qgis(match_delivery._resolve_qgis_python(None), "cad2gis.svg_delivery",
            ["--baseline", str(baseline), "--assets", str(assets), "--output", str(output)])
        report_path = _path(output / "svg-delivery.json")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        checksums = json.loads(_path(output / "checksums.json").read_text(encoding="utf-8"))
        actual = _qgis_source_inventory(output)
        if set(actual) != {*checksums, "checksums.json"} or any(actual[name] != sha for name, sha in checksums.items()):
            raise MCPServiceError("SVG delivery checksum inventory does not match its files")
        _unchanged(before)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, match_delivery.PublishError) as exc:
        if isinstance(exc, MCPServiceError):
            raise
        raise MCPServiceError(f"Source SVG delivery failed: {exc}") from exc
    return {"status": "completed", "output_dir": str(output), "source_sha256": source_sha,
            "report_path": str(report_path), "report": report, "files_sha256": actual,
            "input_artifacts_unchanged": True, "independent_qgis_render_verified": False,
            "absolute_accuracy_verified": False}


def publish_matched_delivery(
    run_dir: str,
    delivery_root: str,
    osm_snapshots: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Publish matched scopes as six-file SVG/OSM deliveries using frozen snapshots.

    Every populated scope must be matched and have its own snapshot. This does
    not accept the candidate or establish survey accuracy. QGIS is discovered
    locally or configured with CAD2GIS_QGIS_PYTHON; no shell command is accepted.
    """
    from .agent_mcp import MCPServiceError, _path, _qgis_source_inventory

    run = _path(run_dir)
    root = _path(delivery_root, must_exist=False)
    if root.is_relative_to(run):
        raise MCPServiceError("Delivery must be outside the immutable run")
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    try:
        scopes = match_delivery._matching_scopes(manifest)
        revision = match_delivery._revision(manifest)
        _output(str(root / f"r{revision}"))
        if not isinstance(osm_snapshots, dict) or set(osm_snapshots) != set(scopes):
            raise MCPServiceError("Provide one frozen OSM snapshot for every populated scope")
        snapshots = {scope: _path(path) for scope, path in osm_snapshots.items()}
        snapshot_hashes = {str(path): digest(path) for path in snapshots.values()}
        before = _qgis_source_inventory(run)
        result = match_delivery.publish(run, delivery_root=root,
            osm_data=[f"{scope}={path}" for scope, path in snapshots.items()])
        if _qgis_source_inventory(run) != before:
            raise MCPServiceError("Source run changed during delivery publication")
        _unchanged(snapshot_hashes)
        if len(result["scopes"]) != len(scopes) or {scope["scope"] for scope in result["scopes"]} != set(scopes):
            raise MCPServiceError("Published result does not contain every matching scope")
        target = _path(result["path"])
        expected = set()
        for scope in result["scopes"]:
            directory = _path(target / scope["directory"])
            if not directory.is_relative_to(target):
                raise MCPServiceError("Published scope escapes its delivery directory")
            expected.update((directory / name).relative_to(target).as_posix() for name in match_delivery.DELIVERY_FILES)
            if digest(directory / "delivery.gpkg") != scope["gpkg_sha256"]:
                raise MCPServiceError("Published database hash differs from the canonical delivery")
            for name in ("delivery.review.json", "view-with-source-SVG.review.json"):
                receipt = json.loads((directory / name).read_text(encoding="utf-8"))
                if receipt.get("osm_source_sha256") != snapshot_hashes[str(snapshots[scope["scope"]])]:
                    raise MCPServiceError("Published review does not match the frozen OSM snapshot")
        inventory = _qgis_source_inventory(target)
        if set(inventory) != expected:
            raise MCPServiceError("Published scopes violate the standard six-file contract")
    except (OSError, ValueError, match_delivery.PublishError) as exc:
        if isinstance(exc, MCPServiceError):
            raise
        raise MCPServiceError(f"Matched delivery publication failed: {exc}") from exc
    return {**result, "status": "completed", "files_sha256": inventory,
            "frozen_osm_sha256": {scope: snapshot_hashes[str(path)] for scope, path in snapshots.items()},
            "source_run_unchanged": True, "independent_qgis_render_verified": False}


def build_qgis_review(
    project_path: str,
    osm_data: str,
    output_path: str,
    standalone: bool = False,
    matching_context_path: str = "",
) -> dict[str, Any]:
    """Build a new offline QGIS review; preserve matched or uncorrected identity.

    Uses the canonical QGIS subprocess. An absent registration stays explicit;
    it is never interpreted as successful matching. Existing SVG bindings are
    preserved, while new semantic symbol assignments are not invented.
    """
    from .agent_mcp import MCPServiceError, _path

    project = _path(project_path)
    snapshot = _path(osm_data)
    output = _output(output_path)
    _output(str(output.with_suffix(".review.json")))
    if output.suffix.lower() != ".qgz" or type(standalone) is not bool:
        raise MCPServiceError("Output must be .qgz and standalone must be boolean")
    if not standalone:
        _output(str(output.parent / "osm-basemap.gpkg"))
    try:
        expected_cad: set[str] = set()
        inputs = _project_inputs(project, cad_hashes=expected_cad)
        inputs[str(snapshot)] = digest(snapshot)
        arguments = ["--project", str(project), "--osm-data", str(snapshot), "--output", str(output)]
        if standalone:
            arguments.append("--standalone")
        if matching_context_path:
            context = _path(matching_context_path)
            inputs[str(context)] = digest(context)
            arguments.extend(["--matching-context", str(context)])
        match_delivery._run_qgis(match_delivery._resolve_qgis_python(None), "cad2gis.qgis_review", arguments)
        receipt_path = _path(output.with_suffix(".review.json"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["sha256"] != digest(output) or receipt["input_project_sha256"] != inputs[str(project)]:
            raise MCPServiceError("QGIS review receipt does not match its input and output")
        if set(receipt.get("cad_database_hashes", [])) != expected_cad or receipt.get("osm_source_sha256") != inputs[str(snapshot)]:
            raise MCPServiceError("QGIS review database or frozen OSM source binding changed")
        _unchanged(inputs)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, match_delivery.PublishError) as exc:
        if isinstance(exc, MCPServiceError):
            raise
        raise MCPServiceError(f"QGIS review failed: {exc}") from exc
    return {"status": "completed", "project_path": str(output), "receipt_path": str(receipt_path),
            "receipt": receipt, "input_artifacts_unchanged": True,
            "independent_qgis_render_verified": False, "absolute_accuracy_verified": False}


def verify_qgis_review(
    project_path: str, image_path: str, title: str = "CAD2GIS review",
) -> dict[str, Any]:
    """Independently reopen and render a hash-bound review QGZ in QGIS."""
    from .agent_mcp import MCPServiceError, _path

    project = _path(project_path)
    image = _output(image_path)
    result_path = _output(str(image.with_suffix(".json")))
    if image.suffix.lower() != ".png":
        raise MCPServiceError("Render output must be a new PNG image")
    try:
        inputs = _project_inputs(project)
        receipt_path = _path(project.with_suffix(".review.json"))
        inputs[str(receipt_path)] = digest(receipt_path)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["sha256"] != inputs[str(project)]:
            raise MCPServiceError("Review QGZ hash mismatch")
        match_delivery._run_qgis(match_delivery._resolve_qgis_python(None), "cad2gis.qgis_review_verify",
            ["--project", str(project), "--image", str(image), "--title", title])
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if (result["project_sha256"] != inputs[str(project)] or result["snapshot_sha256"] != digest(image)
                or not all(result.get(key) is True for key in ("qgis_read", "all_layers_valid", "cad_bytes_unchanged"))):
            raise MCPServiceError("QGIS verification receipt or artifact binding failed")
        font_runtime, font_qa = result.get("font_runtime"), result.get("label_font_qa")
        if (not isinstance(font_runtime, dict) or type(font_runtime.get("available_family_count")) is not int
                or font_runtime["available_family_count"] < 1 or not font_runtime.get("resolved_family")
                or not isinstance(font_qa, dict) or font_qa.get("all_rendered_glyphs_available") is not True
                or not font_qa.get("fonts") or font_qa.get("annotations_checked") != 3):
            raise MCPServiceError("QGIS verification is missing valid rendered font evidence")
        _unchanged(inputs)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, match_delivery.PublishError) as exc:
        if isinstance(exc, MCPServiceError):
            raise
        raise MCPServiceError(f"QGIS verification failed: {exc}") from exc
    return {**result, "status": "completed", "image_path": str(image), "report_path": str(result_path),
            "input_artifacts_unchanged": True, "independent_qgis_render_verified": True}


def audit_visual_conversion(
    source_run: str, run_dir: str, output_dir: str, partition: str = "",
) -> dict[str, Any]:
    """Audit source-bound geometry, fields and labels with the canonical visual audit."""
    from .agent_mcp import MCPServiceError, _path, _qgis_source_inventory

    source, run, output = _path(source_run), _path(run_dir), _output(output_dir)
    if output.is_relative_to(source) or output.is_relative_to(run):
        raise MCPServiceError("Visual audit output must be outside its immutable inputs")
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    if partition and partition not in manifest.get("delivery_partitions", {}):
        raise MCPServiceError("Unknown canonical delivery partition")
    before = {str(base / relative): sha for base in (source, run)
              for relative, sha in _qgis_source_inventory(base).items()}
    command = [sys.executable, "-I", "-m", "cad2gis.visual_audit", "--source-run", str(source),
               "--run", str(run), "--output", str(output)]
    if partition:
        command.extend(["--partition", partition])
    try:
        # Windows Python startup can block on the MCP transport's live stdin.
        process = subprocess.run(command, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, encoding="utf-8", timeout=600)
        if process.returncode:
            raise MCPServiceError("Visual audit failed: " + (process.stderr or process.stdout)[-4000:])
        report_path = _path(output / "report.json")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("immutable_artifacts_unchanged") is not True or report.get("source_artifacts_unchanged") is not True:
            raise MCPServiceError("Visual audit input immutability check failed")
        _unchanged(before)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if isinstance(exc, MCPServiceError):
            raise
        raise MCPServiceError(f"Visual audit failed: {exc}") from exc
    return {"status": "completed", "report_path": str(report_path), "report_sha256": digest(report_path),
            "output_dir": str(output), "source_sha256": manifest["source"]["sha256"],
            "partition": partition or None, "input_artifacts_unchanged": True,
            "absolute_accuracy_verified": False}
