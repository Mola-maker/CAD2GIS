"""Publish a reviewed OSM matching revision as a combined SVG + OSM delivery.

A refined revision lives inside its review workspace.  The portable package is
published beside the project's standard delivery as ``delivery/r<N>/`` so every
reviewed revision keeps its own QGZ, partition scope and cached OSM reference,
while the conversion run and the review workspace stay free of delivery
projects.  QGIS work happens in a separate interpreter that can import
``qgis.core``; this module only orchestrates it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from .delivery import package_delivery

SCHEMA = "cad2gis.published-match-delivery.v1"
DELIVERY_FILES = (
    "delivery.gpkg",
    "delivery.qgz",
    "delivery.review.json",
    "osm-basemap.gpkg",
    "view-with-source-SVG.qgz",
    "view-with-source-SVG.review.json",
)
MAIN_SCOPE = "main"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_USER_AGENT = "cad2gis-osm-road-review/1.0"
_OVERPASS_ATTEMPTS = 6
_OVERPASS_RETRY_DELAY_S = 10.0
_EXTENT_MARGIN_RATIO = 0.06
_EXTENT_MARGIN_MIN_DEGREES = 0.001
_LOG_PREFIX = "cad2gis publish-match: "


class PublishError(RuntimeError):
    """The reviewed revision cannot be published as a delivery."""


def _revision_directory(revision: int) -> str:
    if type(revision) is not int or revision < 1:
        raise PublishError("Matching revision must be a positive integer")
    return f"r{revision}"


def _revision(manifest: Mapping[str, Any]) -> int:
    record = manifest.get("osm_matching")
    if not isinstance(record, Mapping):
        raise PublishError("Run manifest has no OSM matching record; nothing was reviewed")
    revision = record.get("revision")
    if type(revision) is not int or revision < 1:
        raise PublishError("OSM matching record has no valid revision")
    return revision


def _delivery_root(run: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    # <project>/run.review/matching/r<N>-<uuid>/run
    if (run.parent.parent.name == "matching"
            and run.parent.parent.parent.name == "run.review"
            and run.name == "run"):
        return run.parent.parent.parent.parent / "delivery"
    raise PublishError(
        "Cannot infer the project delivery directory; pass --delivery-root"
    )


def _parse_osm_data(values: Sequence[str] | None) -> dict[str, Path]:
    """Map ``[SCOPE=]PATH`` snapshots onto delivery scopes."""
    specs: dict[str, Path] = {}
    for value in values or ():
        scope, separator, path = str(value).partition("=")
        if not separator:
            scope, path = MAIN_SCOPE, str(value)
        scope = scope.strip() or MAIN_SCOPE
        if not re.fullmatch(r"[A-Za-z0-9_-]+", scope):
            raise PublishError(f"Unsafe OSM snapshot scope: {scope!r}")
        snapshot = Path(path).expanduser().resolve()
        if not snapshot.is_file():
            raise PublishError(f"OSM snapshot does not exist: {snapshot}")
        if scope in specs:
            raise PublishError(f"Duplicate OSM snapshot for scope {scope}")
        specs[scope] = snapshot
    return specs


def _log(message: str) -> None:
    print(_LOG_PREFIX + message, file=sys.stderr, flush=True)


def _resolve_qgis_python(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        os.environ.get("CAD2GIS_QGIS_PYTHON"),
        sys.executable,
        "/usr/bin/python3" if os.name == "posix" else None,
    ]
    seen: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(candidate).expanduser()
        if str(path) in seen:
            continue
        seen.append(str(path))
        if not path.is_file():
            continue
        try:
            probe = subprocess.run(
                [str(path), "-c", "import qgis.core"],
                capture_output=True, timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return path
    raise PublishError(
        "No Python runtime with QGIS was found; pass --qgis-python or set CAD2GIS_QGIS_PYTHON"
    )


def _run_qgis(python: Path, module: str, arguments: Sequence[str]) -> None:
    import cad2gis

    environment = dict(os.environ)
    package_parent = str(Path(cad2gis.__file__).resolve().parent.parent)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        package_parent + os.pathsep + existing if existing else package_parent
    )
    environment["QT_QPA_PLATFORM"] = "offscreen"
    command = [str(python), "-m", module, *arguments]
    _log("run " + " ".join(command))
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else "no output"
        raise PublishError(f"{module} failed ({result.returncode}): {tail}")


def _gpkg_bounds(gpkg: Path) -> list[float]:
    """Return (west, south, east, north) in EPSG:4326 covering every feature."""
    from .native_runtime import ensure_osgeo_runtime

    ensure_osgeo_runtime()
    from osgeo import ogr
    from pyproj import Transformer

    dataset = ogr.Open(str(gpkg), 0)
    if dataset is None:
        raise PublishError(f"Cannot open delivery GeoPackage: {gpkg}")
    extent: list[float] | None = None
    spatial = None
    for index in range(dataset.GetLayerCount()):
        layer = dataset.GetLayer(index)
        if layer is None or layer.GetFeatureCount() == 0:
            continue
        current = layer.GetExtent()
        if current == (0.0, 0.0, 0.0, 0.0):
            continue
        extent = list(current) if extent is None else [
            min(extent[0], current[0]), max(extent[1], current[1]),
            min(extent[2], current[2]), max(extent[3], current[3]),
        ]
        if spatial is None:
            spatial = layer.GetSpatialRef()
    if extent is None or spatial is None:
        raise PublishError(f"Delivery GeoPackage has no located features: {gpkg}")
    transformer = Transformer.from_crs(spatial.ExportToWkt(), "EPSG:4326", always_xy=True)
    west, south = transformer.transform(extent[0], extent[2])
    east, north = transformer.transform(extent[1], extent[3])
    return [min(west, east), min(south, north), max(west, east), max(south, north)]


def _search_bbox(bounds: Sequence[float]) -> dict[str, float]:
    west, south, east, north = (float(value) for value in bounds)
    dx = (east - west) * _EXTENT_MARGIN_RATIO + _EXTENT_MARGIN_MIN_DEGREES
    dy = (north - south) * _EXTENT_MARGIN_RATIO + _EXTENT_MARGIN_MIN_DEGREES
    return {"west": west - dx, "south": south - dy, "east": east + dx, "north": north + dy}


def _fetch_review_snapshot(
    bbox: Mapping[str, float], *, attempts: int = _OVERPASS_ATTEMPTS,
) -> dict[str, Any]:
    """Fetch the roads-only Overpass snapshot used as the cached review basemap."""
    query = (
        "[out:json][timeout:60];"
        f"way[highway]({bbox['south']:.6f},{bbox['west']:.6f},"
        f"{bbox['north']:.6f},{bbox['east']:.6f});"
        "out tags geom 2500;"
    )
    url = os.environ.get("CAD2GIS_OVERPASS_URL", _OVERPASS_URL).strip()
    user_agent = os.environ.get("CAD2GIS_OSM_USER_AGENT", _OVERPASS_USER_AGENT)
    last = "no attempt"
    for attempt in range(1, max(1, attempts) + 1):
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
            headers={"User-Agent": user_agent,
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last = f"attempt {attempt}: {exc}"
            if attempt < attempts:
                time.sleep(_OVERPASS_RETRY_DELAY_S)
            continue
        ways = [element for element in payload.get("elements", ())
                if isinstance(element, Mapping) and element.get("geometry")]
        if not ways:
            raise PublishError(
                "Overpass returned no road geometry for the review extent; "
                "supply --osm-data for this scope"
            )
        return payload
    raise PublishError(f"Cannot fetch review OSM data from Overpass ({last})")


def publish(
    run_dir: str | Path,
    *,
    delivery_root: str | Path | None = None,
    osm_data: Sequence[str] | None = None,
    qgis_python: str | Path | None = None,
) -> dict[str, Any]:
    if not __debug__:
        raise PublishError("SVG delivery checks require assertions enabled; do not use python -O")
    run = Path(run_dir).expanduser().resolve()
    manifest_path = run / "run_manifest.json"
    if not manifest_path.is_file():
        raise PublishError(f"Run manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    revision = _revision(manifest)
    root = _delivery_root(run, Path(delivery_root) if delivery_root is not None else None)
    directory = _revision_directory(revision)
    target = root / directory
    if target.exists():
        raise PublishError(f"Revision delivery already exists: {target}")
    snapshots = _parse_osm_data(osm_data)
    python = _resolve_qgis_python(Path(qgis_python) if qgis_python is not None else None)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{directory}.partial-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix="cad2gis-publish-") as work:
            work_path = Path(work)
            baseline = work_path / "package"
            _log(f"package {run}")
            report = package_delivery(run, baseline)
            svg_root = work_path / "svg"
            _run_qgis(python, "cad2gis.svg_delivery", [
                "--baseline", str(baseline),
                "--assets", str(run / "svg-candidates"),
                "--output", str(svg_root),
            ])
            review_root = work_path / "review"
            scopes = [
                _publish_scope(entry, baseline, svg_root, review_root, staging, snapshots, python)
                for entry in report["deliveries"]
            ]
            published = {
                "schema_version": SCHEMA,
                "source_sha256": report["source_sha256"],
                "run_status": report["run_status"],
                "revision": revision,
                "run_dir": str(run),
                "delivery_root": str(root),
                "directory": directory,
                "absolute_accuracy_verified": False,
                "scopes": scopes,
            }
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    published["path"] = str(target)
    published["files"] = sorted(
        path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
    )
    return published


def _publish_scope(
    entry: Mapping[str, Any],
    baseline: Path,
    svg_root: Path,
    review_root: Path,
    staging: Path,
    snapshots: Mapping[str, Path],
    python: Path,
) -> dict[str, Any]:
    scope = str(entry["scope"])
    relative = Path(str(entry["project"]))
    group = relative.parent
    source_gpkg = baseline / group / "delivery.gpkg"
    snapshot = snapshots.get(scope)
    if snapshot is None:
        bbox = _search_bbox(_gpkg_bounds(source_gpkg))
        _log(f"fetch cached OSM review data for {scope} "
             f"({bbox['south']:.6f},{bbox['west']:.6f},{bbox['north']:.6f},{bbox['east']:.6f})")
        snapshot = staging / f".osm-{scope}.json"
        snapshot.write_text(
            json.dumps(_fetch_review_snapshot(bbox), ensure_ascii=False), encoding="utf-8"
        )
    svg_project = svg_root / relative
    if not svg_project.is_file():
        raise PublishError(f"SVG review project is missing for scope {scope}: {svg_project}")
    review_project = review_root / relative
    review_project.parent.mkdir(parents=True, exist_ok=True)
    _run_qgis(python, "cad2gis.qgis_review", [
        "--project", str(svg_project),
        "--osm-data", str(snapshot),
        "--output", str(review_project),
    ])
    standalone = review_project.parent / "view-with-source-SVG.qgz"
    _run_qgis(python, "cad2gis.qgis_review", [
        "--project", str(review_project),
        "--osm-data", str(snapshot),
        "--output", str(standalone),
        "--standalone",
    ])
    destination = staging if scope == MAIN_SCOPE else staging / group
    destination.mkdir(parents=True, exist_ok=True)
    copies = {
        "delivery.gpkg": source_gpkg,
        "delivery.qgz": review_project,
        "delivery.review.json": review_project.with_suffix(".review.json"),
        "osm-basemap.gpkg": review_project.parent / "osm-basemap.gpkg",
        "view-with-source-SVG.qgz": standalone,
        "view-with-source-SVG.review.json": standalone.with_suffix(".review.json"),
    }
    for name, source in copies.items():
        if not source.is_file():
            raise PublishError(f"Published scope {scope} is missing {name}")
        shutil.copy2(source, destination / name)
    if snapshot.parent == staging:
        snapshot.unlink()
    review_receipt = json.loads((destination / "delivery.review.json").read_text(encoding="utf-8"))
    return {
        "scope": scope,
        "directory": "." if scope == MAIN_SCOPE else group.as_posix(),
        "gpkg_sha256": entry["gpkg_sha256"],
        "svg_bindings": len(review_receipt.get("svg_bindings", ())),
        "osm_layers": review_receipt.get("osm_layers"),
        "geometry_and_attributes_unchanged": review_receipt.get("geometry_and_attributes_unchanged"),
    }


def register(commands: argparse._SubParsersAction[Any]) -> None:
    publish_match = commands.add_parser(
        "publish-match",
        help="Publish a reviewed OSM matching revision as delivery/r<N>/ with SVG and cached OSM data.",
    )
    publish_match.add_argument("run_dir", type=Path, metavar="RUN_DIR",
                               help="Refined run directory inside its review workspace.")
    publish_match.add_argument("--delivery-root", type=Path,
                               help="Project delivery directory; inferred from a review workspace path.")
    publish_match.add_argument("--osm-data", action="append", default=[], metavar="[SCOPE=]PATH",
                               help="Reviewed Overpass JSON snapshot; repeat per partition scope.")
    publish_match.add_argument("--qgis-python", type=Path,
                               help="Python interpreter that can import qgis.core.")
    publish_match.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    publish_match.set_defaults(handler=_publish_command)


def _publish_command(args: argparse.Namespace) -> tuple[Any, int]:
    report = publish(
        args.run_dir,
        delivery_root=args.delivery_root,
        osm_data=tuple(args.osm_data),
        qgis_python=args.qgis_python,
    )
    return report, 0
