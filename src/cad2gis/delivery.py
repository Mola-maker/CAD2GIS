"""Portable QGIS delivery: exact GeoPackages, QML, QGZ, field tables and checksums."""
from __future__ import annotations

import copy
import csv
import json
import shutil
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .batch import digest, write_json

LENGTH_EXPRESSION = ('CASE WHEN "length_value_m" IS NOT NULL THEN '
                     'format_number("length_value_m", 2) || \' m [\' || '
                     'CASE WHEN "length_source" = \'dwg_dimension\' THEN \'CAD\' '
                     'WHEN "length_source" = \'dwg_curve_geometry\' THEN \'GEOM\' ELSE \'?\' END || \']\' END')


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def qgis_project(gpkg: Path, styles: Path, destination: Path) -> list[dict]:
    root = ET.Element("qgis", version="3.40.0", projectname=destination.stem)
    ET.SubElement(root, "title").text = destination.stem
    tree = ET.SubElement(root, "layer-tree-group", name="", checked="Qt::Checked")
    layers = ET.SubElement(root, "projectlayers")
    report = []
    extent = None
    with sqlite3.connect(gpkg.as_uri() + "?mode=ro", uri=True) as db:
        for name, geometry, srs in db.execute("SELECT table_name,geometry_type_name,srs_id FROM gpkg_geometry_columns ORDER BY CASE WHEN geometry_type_name LIKE '%POINT%' THEN 0 WHEN geometry_type_name LIKE '%LINE%' THEN 1 ELSE 2 END, table_name"):
            identifier = "cad2gis_" + name
            qml_path = styles / (name + ".qml")
            style = ET.parse(qml_path).getroot() if qml_path.exists() else ET.Element("qgis")
            field_names = [row[1] for row in db.execute(f"PRAGMA table_info({_quote(name)})")]
            text = style.find(".//labeling/settings/text-style")
            if name == "CABLE" and text is not None and "length_value_m" in field_names:
                text.set("fieldName", LENGTH_EXPRESSION)
                text.set("isExpression", "1")
            if text is not None and text.find("text-buffer") is None:
                ET.SubElement(text, "text-buffer", bufferDraw="1", bufferSize="0.7", bufferSizeUnits="MM",
                              bufferColor="255,255,255,255", bufferOpacity="1", bufferNoFill="1")
            style.set("labelsEnabled", "1")
            rendering = style.find(".//labeling/settings/rendering")
            if rendering is not None and "POLYGON" in geometry.upper():
                rendering.set("obstacle", "0")
            placement = style.find(".//labeling/settings/placement")
            if placement is not None and "LINE" in geometry.upper():
                placement.set("placementFlags", "10")
            ET.ElementTree(style).write(qml_path, encoding="utf-8", xml_declaration=True)
            layer = ET.SubElement(layers, "maplayer", type="vector", geometry=geometry.title(), labelsEnabled="1")
            ET.SubElement(layer, "id").text = identifier
            ET.SubElement(layer, "layername").text = name
            ET.SubElement(layer, "datasource").text = f"./{gpkg.name}|layername={name}"
            ET.SubElement(layer, "provider", encoding="UTF-8").text = "ogr"
            wkt = db.execute("SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id=?", (srs,)).fetchone()[0]
            layer_crs = ET.SubElement(ET.SubElement(layer, "srs"), "spatialrefsys", nativeFormat="Wkt")
            ET.SubElement(layer_crs, "wkt").text = wkt
            ET.SubElement(layer_crs, "authid").text = f"EPSG:{srs}"
            for child in style:
                if child.tag not in {"id", "layername", "datasource", "provider"}:
                    layer.append(copy.deepcopy(child))
            ET.SubElement(tree, "layer-tree-layer", id=identifier, name=name, checked="Qt::Checked",
                          source=f"./{gpkg.name}|layername={name}", providerKey="ogr")
            count = db.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
            blank = db.execute(f'SELECT COUNT(*) FROM {_quote(name)} WHERE display_label IS NULL OR trim(display_label)=\'\'').fetchone()[0] if "display_label" in field_names else count
            fields = list(db.execute(f"PRAGMA table_info({_quote(name)})"))
            report.append({"layer": name, "features": count, "blank_display_labels": blank,
                           "srs_id": srs, "fields": [{"name": f[1], "type": f[2]} for f in fields]})
            geom_col = db.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name=?", (name,)).fetchone()[0]
            columns = [f for f in field_names if f != geom_col]
            with (destination.parent / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows(db.execute(f'SELECT {",".join(map(_quote, columns))} FROM {_quote(name)}'))
            bounds = db.execute("SELECT min_x,min_y,max_x,max_y FROM gpkg_contents WHERE table_name=?", (name,)).fetchone()
            if count and bounds and all(v is not None for v in bounds):
                extent = list(bounds) if extent is None else [min(extent[0], bounds[0]), min(extent[1], bounds[1]), max(extent[2], bounds[2]), max(extent[3], bounds[3])]
            if root.find("projectCrs") is None:
                wkt = db.execute("SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id=?", (srs,)).fetchone()[0]
                spatial = ET.SubElement(ET.SubElement(root, "projectCrs"), "spatialrefsys", nativeFormat="Wkt")
                ET.SubElement(spatial, "wkt").text = wkt
                ET.SubElement(spatial, "srsid").text = "0"
                ET.SubElement(spatial, "srid").text = str(srs)
                ET.SubElement(spatial, "authid").text = f"EPSG:{srs}"
                ET.SubElement(spatial, "geographicflag").text = "true" if srs == 4326 else "false"
    canvas = ET.SubElement(root, "mapcanvas")
    ET.SubElement(canvas, "projections").text = "1"
    project_crs = root.find("projectCrs/spatialrefsys")
    if project_crs is not None:
        ET.SubElement(canvas, "destinationsrs").append(copy.deepcopy(project_crs))
    if extent:
        node = ET.SubElement(canvas, "extent")
        for key, value in zip(("xmin", "ymin", "xmax", "ymax"), extent):
            ET.SubElement(node, key).text = str(value)
        view = ET.SubElement(root, "ProjectViewSettings", UseProjectScales="0", rotation="0")
        default_extent = ET.SubElement(view, "DefaultViewExtent")
        for key, value in zip(("xmin", "ymin", "xmax", "ymax"), extent):
            ET.SubElement(default_extent, key).text = str(value)
        crs_node = root.find("projectCrs/spatialrefsys")
        if crs_node is not None:
            ET.SubElement(default_extent, "crs").append(copy.deepcopy(crs_node))
    properties = ET.SubElement(root, "properties")
    ET.SubElement(ET.SubElement(properties, "SpatialRefSys"), "ProjectionsEnabled", type="int").text = "1"
    ET.SubElement(ET.SubElement(properties, "Paths"), "Absolute", type="bool").text = "false"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(destination.stem + ".qgs", ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return report


def package_delivery(run: Path, output: Path, *, audit_dir: Path | None = None) -> dict:
    run, output = run.resolve(), output.resolve()
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    scopes = [("main", run)]
    scopes.extend((p.parent.name, p.parent) for p in sorted(run.glob("*/delivery.gpkg")))
    deliveries = []
    for scope, origin in scopes:
        folder = output if scope == "main" else output / scope
        folder.mkdir(parents=True, exist_ok=True)
        source = origin / "delivery.gpkg"
        artifact = manifest.get("artifacts", {}).get("delivery", {}) if scope == "main" else manifest.get("delivery_partitions", {}).get(scope, {})
        if artifact.get("sha256") != digest(source):
            raise ValueError(f"GeoPackage does not match the canonical manifest: {scope}")
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError(f"SQLite integrity check failed: {scope}")
        shutil.copy2(source, folder / "delivery.gpkg")
        styles = folder / "styles"
        styles.mkdir()
        for qml in (origin / "qgis" / "styles").glob("*.qml"):
            shutil.copy2(qml, styles / qml.name)
        layers = qgis_project(folder / "delivery.gpkg", styles, folder / "delivery.qgz")
        deliveries.append({"scope": scope, "gpkg_sha256": digest(source), "layers": layers,
                           "project": (folder / "delivery.qgz").relative_to(output).as_posix()})
    report = {"schema_version": "cad2gis.delivery-package.v1", "source_sha256": manifest.get("source", {}).get("sha256"),
              "run_manifest_sha256": digest(run / "run_manifest.json"), "run_status": manifest.get("run_status", "UNKNOWN"),
              "absolute_accuracy_verified": False, "deliveries": deliveries,
              "source_dwg_included": False, "full_source_evidence_included": False}
    if audit_dir is not None:
        shutil.copytree(audit_dir, output / "visual")
        report["visual_audit_included"] = True
    write_json(output / "delivery-manifest.json", report)
    (output / "README.md").write_text("# CAD2GIS QGIS delivery\n\nUnzip the entire package; open delivery.qgz. Keep delivery.gpkg and styles beside it.\n\n"
        "GeoPackage is SQLite. CSV files contain every non-geometry attribute. CABLE labels display selected length in metres and its source; grid/geodesic/CAD measurements remain distinct. Empty labels are not invented.\n\n"
        "CONDITIONAL: no independent surveyed GCP acceptance. A QGIS project and visual overlay do not certify absolute accuracy. Partitions are separate projects and may share assets with the main project.\n\n"
        "Original GeoPackage bytes are preserved. QML label settings are presentation overrides. This portable delivery excludes raw DWG and full source/evidence databases; AI source-bound edits require the original reviewed run and project.\n", encoding="utf-8")
    seal_delivery(output)
    return report


def seal_delivery(output: Path, *, replace: bool = False) -> None:
    write_json(output / "checksums.json", {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob("*")) if p.is_file() and p.name != "checksums.json"})
    with zipfile.ZipFile(output.with_suffix(".zip"), "w" if replace else "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output).as_posix())
