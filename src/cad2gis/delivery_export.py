"""Export a published delivery GeoPackage to other GIS formats.

The GeoPackage remains the authoritative delivery. Exports are derived copies
written outside the immutable run: every feature layer is translated with
GDAL in the delivery CRS (no reprojection), reopened, and its feature count
checked against the source layer before ``export_manifest.json`` records the
run, delivery and output hashes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

EXPORT_SCHEMA = "cad2gis.delivery_export.v1"

# format -> (GDAL driver, per-layer file suffix or None for one container)
FORMATS = {
    "flatgeobuf": ("FlatGeobuf", ".fgb"),
    "geojson": ("GeoJSON", ".geojson"),
    "shapefile": ("ESRI Shapefile", ".shp"),
    "filegdb": ("OpenFileGDB", None),
}
_LIMITATIONS = {
    "shapefile": "Field names are truncated to 10 characters and long text may be cut; "
                 "use the GeoPackage for review and acceptance.",
    "geojson": "Coordinates stay in the delivery CRS (not RFC 7946 WGS84); readers must honour the crs "
               "member. JSON-typed fields are written as JSON values: same content, different spacing.",
    "filegdb": "Coordinates are snapped to the FileGDB XY resolution grid (measured at most 0.1 mm on "
               "the reference corpus) and lines/polygons are stored as multi-part types.",
}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _tree_sha256(path: Path) -> dict[str, str]:
    if path.is_file():
        return {path.name: _sha256(path)}
    return {item.relative_to(path).as_posix(): _sha256(item)
            for item in sorted(path.rglob("*")) if item.is_file()}


def _feature_layers(delivery: Path) -> dict[str, int]:
    with sqlite3.connect(delivery.resolve().as_uri() + "?mode=ro", uri=True) as db:
        names = [row[0] for row in db.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type='features' ORDER BY table_name")]
        return {name: db.execute(f'SELECT COUNT(*) FROM "{name.replace(chr(34), chr(34) * 2)}"').fetchone()[0]
                for name in names}


def export_delivery(run_dir: str | Path, output_dir: str | Path, fmt: str) -> dict[str, Any]:
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {sorted(FORMATS)}")
    run = Path(run_dir).resolve()
    output = Path(output_dir).resolve()
    if output == run or run in output.parents:
        raise ValueError("Export output must be outside the immutable run")
    if output.exists():
        raise FileExistsError(f"Export output already exists: {output}")
    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    delivery = Path(manifest["artifacts"]["delivery"]["path"])
    if not delivery.is_file():
        delivery = run / delivery.name
    if _sha256(delivery) != manifest["artifacts"]["delivery"]["sha256"]:
        raise ValueError("delivery.gpkg does not match its run manifest hash")

    from .native_runtime import ensure_osgeo_runtime

    ensure_osgeo_runtime()
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    driver, suffix = FORMATS[fmt]
    layers = _feature_layers(delivery)
    output.mkdir(parents=True)
    targets: dict[str, Path] = {}
    container = output / "delivery.gdb" if suffix is None else None
    for name in layers:
        target = container if container is not None else output / f"{name}{suffix}"
        options = gdal.VectorTranslateOptions(
            format=driver, layers=[name], layerName=name,
            accessMode="update" if container is not None and container.exists() else None,
        )
        result = gdal.VectorTranslate(str(target), str(delivery), options=options)
        if result is None:
            raise RuntimeError(f"GDAL could not export layer {name} to {driver}")
        result = None  # close and flush
        targets[name] = target

    counts: dict[str, int] = {}
    for name, target in targets.items():
        dataset = ogr.Open(str(target))
        layer = dataset.GetLayerByName(name) if dataset is not None else None
        if layer is None and dataset is not None and dataset.GetLayerCount() == 1:
            layer = dataset.GetLayer(0)
        if layer is None:
            raise RuntimeError(f"Exported layer {name} cannot be reopened")
        counts[name] = layer.GetFeatureCount()
        dataset = None
        if counts[name] != layers[name]:
            raise RuntimeError(f"Exported layer {name} has {counts[name]} features, expected {layers[name]}")

    export_manifest = {
        "schema_version": EXPORT_SCHEMA,
        "format": fmt,
        "gdal_driver": driver,
        "gdal_version": gdal.__version__,
        "run_dir": str(run),
        "run_manifest_sha256": _sha256(manifest_path),
        "run_status": manifest.get("run_status"),
        "delivery_sha256": _sha256(delivery),
        "crs": "delivery CRS, no reprojection",
        "authority": "derived copy; the delivery GeoPackage remains authoritative",
        "layer_feature_counts": counts,
        "files": _tree_sha256(output),
        **({"limitation": _LIMITATIONS[fmt]} if fmt in _LIMITATIONS else {}),
    }
    (output / "export_manifest.json").write_text(
        json.dumps(export_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return export_manifest


__all__ = ["FORMATS", "export_delivery"]
