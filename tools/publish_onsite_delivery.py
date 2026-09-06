"""Prepare an explicitly authorized nine-drawing derived release; never copy DWGs."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
from pathlib import Path

from cad2gis.batch import digest, write_index, write_json
from cad2gis.delivery import package_delivery, seal_delivery
from cad2gis.native_runtime import ensure_osgeo_runtime

ensure_osgeo_runtime()
from osgeo import ogr, osr  # noqa: E402


def public(value):
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if k not in {"run_dir", "source_run", "delivery_gpkg_path", "source_path"}}
    if isinstance(value, list):
        return [public(v) for v in value]
    if isinstance(value, str) and (":\\" in value or ":/" in value or value.startswith(("/home/", "/Users/"))):
        return Path(value.replace("\\", "/")).name
    return value


def build(selection: Path, output: Path):
    spec = importlib.util.spec_from_file_location("fixture_export", Path(__file__).with_name("export_webdemo_fixture.py"))
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    output.mkdir(parents=True, exist_ok=False)
    assets = output / "assets"
    assets.mkdir()
    projects, records = [], []
    selected = json.loads(selection.read_text(encoding="utf-8"))
    if len(selected) != 9:
        raise ValueError("This release requires exactly nine selected drawings")
    for item in selected:
        run = Path(item["run_dir"])
        identifier = item["id"]
        manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
        name = Path(manifest["source"]["path"]).stem
        destination = output / identifier
        delivery = package_delivery(run, destination)
        fixture_path = assets / (identifier + ".json")
        exporter.export_fixture(run, fixture_path, project_id=identifier, project_name=name)
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        dataset = ogr.Open(str(run / "delivery.gpkg"))
        geographic = {}
        for layer_name, collection in fixture["layers"].items():
            layer = dataset.GetLayerByName(layer_name)
            source = layer.GetSpatialRef().Clone()
            source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            target = osr.SpatialReference()
            target.ImportFromEPSG(4326)
            target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            transform = osr.CoordinateTransformation(source, target)
            # Preserve every geometry; native and geographic representations are explicitly separate.
            by_key = {str(f["id"]): f for f in collection["features"]}
            native, world = [], []
            for row in layer:
                geometry = row.GetGeometryRef()
                if geometry is None or geometry.IsEmpty():
                    raise ValueError(f"Empty delivery geometry: {identifier}/{layer_name}/{row.GetFID()}")
                key = str(row.GetField("source_entity_key"))
                feature = by_key[key]
                feature["geometry"] = json.loads(geometry.ExportToJson(options=["COORDINATE_PRECISION=15"]))
                native.append(feature)
                positioned = geometry.Clone()
                if positioned.Transform(transform) != 0:
                    raise ValueError("Geographic projection failed")
                world.append({**feature, "geometry": json.loads(positioned.ExportToJson(options=["COORDINATE_PRECISION=15"]))})
            collection["features"] = native
            geographic[layer_name] = {"type": "FeatureCollection", "features": world}
            if len(native) != item["delivery_counts"][layer_name]:
                raise ValueError("Fixture feature count differs from selected delivery")
        fixture["geographic_layers"] = geographic
        fixture["run"]["demo"]["has_geographic_layers"] = True
        fixture["run"]["source_blocker"] = "静态演示不运行 DWG 转换；完整 QGIS 派生成果可从九图交付总览下载。"
        fixture["run"]["demo"]["publication_boundary"] = "授权发布九图派生成果；不含 DWG；绝对 GCP 精度未验收。"
        fixture["provenance"]["publication_boundary"] = fixture["run"]["demo"]["publication_boundary"]
        write_json(fixture_path, public(fixture))
        evidence = destination / "process"
        evidence.mkdir()
        audit = Path(item["audit_report"])
        for path in audit.parent.iterdir():
            if path.suffix in {".png", ".csv"}:
                shutil.copy2(path, evidence / path.name)
        write_json(evidence / "audit.json", public(json.loads(audit.read_text(encoding="utf-8"))))
        write_json(evidence / "run-manifest.json", public(manifest))
        write_json(evidence / "stages.json", {"source_sha256": item["source_sha256"], "canonical_version": run.parents[1].name,
            "stages": ["DWG source extraction", "source-bound semantic review", "canonical conversion", "independent visual audit", "portable QGIS packaging"],
            "status": item["status"], "absolute_accuracy_verified": False,
            "note": "Historical recorded results; not a live or newly executed Linux conversion."})
        seal_delivery(destination, replace=True)
        projects.append({"id": identifier, "display_name": name, "short_name": identifier,
            "fixture": identifier + ".json", "source_sha256": item["source_sha256"],
            "delivery_feature_count": sum(item["delivery_counts"].values()), "source_entity_count": manifest.get("source_entity_count"),
            "map_reference": manifest.get("crs", {}).get("target_crs", "unknown"),
            "map_precision": "nominal_crs_no_surveyed_gcp", "accuracy_note": "CONDITIONAL；未提供实测 GCP 或独立检查点验收。",
            "description": "九图现场转换历史成果，含完整 QGIS 项目、字段表与视觉审查过程。"})
        records.append({"id": identifier, "name": name, "preview": identifier + "/process/source-delivery-overlay.png", "status": delivery["run_status"], "links": {
            "交付 ZIP": identifier + ".zip", "QGIS 项目": identifier + "/delivery.qgz",
            "审查叠图": identifier + "/process/source-delivery-overlay.png",
            "审查报告": identifier + "/process/audit.json", "转换阶段": identifier + "/process/stages.json",
            "Web 转化台": "../workspace/?demo=1&project=" + identifier}})
    write_json(assets / "catalog.json", {"projects": projects})
    report = {"schema_version": "cad2gis.batch-report.v1", "status": "HISTORICAL_CONDITIONAL", "drawings": records}
    write_json(output / "batch-report.json", report)
    write_index(output, report)
    write_json(output / "publication.json", {"schema_version": "cad2gis.authorized-derived-release.v1",
        "authorization": "user-request-2026-09-06-nine-drawing-qgis-pages", "absolute_accuracy_verified": False,
        "raw_dwg_included": False, "drawing_count": 9,
        "files": {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob("*")) if p.is_file()}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.selection, args.output)
