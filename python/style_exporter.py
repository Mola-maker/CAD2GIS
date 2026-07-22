#!/usr/bin/env python3
"""
Three-track QGIS styling from the effective-CAD-colour fields (S component):
  1. sidecar categorized QML per delivery layer + style_manifest.json
  2. layer_styles table embedded in the GeoPackage (useAsDefault=1) so
     dragging the .gpkg alone into QGIS applies the CAD colours
  3. .qgz project with every delivery layer styled plus FDT-01/FDT-02/LINK
     filter groups (subsetString on FDT_ID) and span labels enabled

Categorisation attribute is `style_key` ("#RRGGBB|linetype") written by
converter.py; colours therefore reproduce the source DWG exactly.

Usage:
  python style_builder.py --gpkg delivery.gpkg [--skip-project]
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict

from osgeo import ogr

FC_LAYERS = ["BOITE", "CABLE", "PTECH", "INFRASTRUCTURE",
             "SITE", "ZNRO", "ZPM", "IMB"]
SPAN_LAYERS = ["span_records", "span_annotations"]

STYLE_NAME = "CAD2GIS DWG style"

# Layers that carry FDT_ID and participate in the per-domain filter groups
FDT_GROUP_LAYERS = ["CABLE", "BOITE", "PTECH", "SITE"]
FDT_DOMAINS = ["FDT-01", "FDT-02", "LINK"]

_MARKERS = {
    "BOITE": ("square", 3.2),
    "SITE": ("diamond", 4.0),
    "PTECH": ("circle", 3.2),
    "IMB": ("circle", 0.8),
}

_LINE_WIDTHS = {"CABLE": 0.6}

_GEOM_KIND = {ogr.wkbPoint: "Point", ogr.wkbLineString: "LineString",
              ogr.wkbPolygon: "Polygon"}


def _rgba(rgb_hex):
    """"#RRGGBB" → "R,G,B,255" (QGIS colour option format)."""
    value = (rgb_hex or "").lstrip("#")
    try:
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        r, g, b = 64, 64, 64
    return f"{r},{g},{b},255"


def _pen_style(linetype):
    name = (linetype or "Continuous").upper()
    if "DASHDOT" in name or "CENTER" in name or "PHANTOM" in name:
        return "dash dot"
    if "DOT" in name:
        return "dot"
    if "DASH" in name or "HIDDEN" in name:
        return "dash"
    return "solid"


def _option(parent, name, value):
    ET.SubElement(parent, "Option", name=name, value=str(value), type="QString")


def _qml(layer_name, geom_kind, categories, label_field,
         label_is_expression=False):
    """Categorized QML on style_key — same shell pattern as BOITE.qml/v3."""
    root = ET.Element(
        "qgis", version="3.40.0", styleCategories="AllStyleCategories",
        labelsEnabled="1", simplifyDrawingHints="1")
    renderer = ET.SubElement(root, "renderer-v2", type="categorizedSymbol",
                             attr="style_key")
    cats_el = ET.SubElement(renderer, "categories")
    syms_el = ET.SubElement(renderer, "symbols")
    for index, (style_key, rgb, linetype, count) in enumerate(categories):
        ET.SubElement(cats_el, "category", value=style_key,
                      label=f"{style_key} ({count})", symbol=str(index),
                      render="true")
        symbol = ET.SubElement(
            syms_el, "symbol", name=str(index), alpha="1",
            type={"Point": "marker", "LineString": "line",
                  "Polygon": "fill"}[geom_kind])
        color = _rgba(rgb)
        if geom_kind == "Point":
            marker_name, marker_size = _MARKERS.get(layer_name, ("circle", 2.4))
            lyr = ET.SubElement(symbol, "layer",
                                **{"class": "SimpleMarker", "enabled": "1"})
            options = ET.SubElement(lyr, "Option", type="Map")
            if layer_name == "BOITE":
                _option(options, "color", "0,0,0,0")
                _option(options, "outline_color", "255,0,0,255")
                _option(options, "outline_style", "solid")
                _option(options, "outline_width", "1.0")
                _option(options, "outline_width_unit", "MM")
            else:
                _option(options, "color", color)
                _option(options, "outline_color", "20,20,20,255")
            _option(options, "name", marker_name)
            _option(options, "size", marker_size)
            _option(options, "size_unit", "MM")
        elif geom_kind == "LineString":
            lyr = ET.SubElement(symbol, "layer",
                                **{"class": "SimpleLine", "enabled": "1"})
            options = ET.SubElement(lyr, "Option", type="Map")
            _option(options, "line_color", color)
            _option(options, "line_style", _pen_style(linetype))
            _option(options, "line_width", _LINE_WIDTHS.get(layer_name, 0.35))
            _option(options, "line_width_unit", "MM")
        else:
            lyr = ET.SubElement(symbol, "layer",
                                **{"class": "SimpleFill", "enabled": "1"})
            options = ET.SubElement(lyr, "Option", type="Map")
            _option(options, "color", color.rsplit(",", 1)[0] + ",70")
            _option(options, "outline_color", color)
            _option(options, "outline_style", _pen_style(linetype))
            _option(options, "outline_width", 0.35)
            _option(options, "outline_width_unit", "MM")
    labeling = ET.SubElement(root, "labeling", type="simple")
    settings = ET.SubElement(labeling, "settings")
    ET.SubElement(settings, "text-style", fieldName=label_field or "",
                  isExpression="1" if label_is_expression else "0",
                  fontFamily="Arial", fontSize="8",
                  textColor="0,0,0,255")
    ET.SubElement(settings, "placement", placement="0", dist="1",
                  offsetUnits="MM")
    ET.SubElement(settings, "rendering",
                  drawLabels="1" if label_field else "0",
                  obstacle="1", scaleVisibility="0")
    ET.SubElement(root, "layerGeometryType").text = \
        {"Point": "0", "LineString": "1", "Polygon": "2"}[geom_kind]
    return ET.tostring(root, encoding="unicode")


def _collect_layer_styles(gpkg_path):
    """Read per-layer style_key distribution and geometry kind from the
    delivery GeoPackage. Returns OrderedDict layer → info dict."""
    ds = ogr.Open(gpkg_path, 0)
    if ds is None:
        raise RuntimeError(f"Cannot open GeoPackage: {gpkg_path}")
    layers = OrderedDict()
    try:
        for name in FC_LAYERS + SPAN_LAYERS:
            lyr = ds.GetLayerByName(name)
            if lyr is None:
                continue
            geom_kind = _GEOM_KIND.get(ogr.GT_Flatten(lyr.GetGeomType()))
            if geom_kind is None:
                continue
            defn = lyr.GetLayerDefn()
            field_names = {defn.GetFieldDefn(i).GetName()
                           for i in range(defn.GetFieldCount())}
            if "style_key" not in field_names:
                continue
            observed = OrderedDict()
            lyr.ResetReading()
            for feat in lyr:
                key = feat.GetField("style_key")
                rgb = feat.GetField("color_rgb")
                if not key:
                    key, rgb = "#404040|Continuous", "#404040"
                linetype = key.split("|", 1)[1] if "|" in key else "Continuous"
                entry = observed.setdefault(key, [key, rgb, linetype, 0])
                entry[3] += 1
            categories = [tuple(v) for v in
                          sorted(observed.values(), key=lambda v: (-v[3], v[0]))]
            if not categories:
                categories = [("#404040|Continuous", "#404040",
                               "Continuous", 0)]
            label_is_expression = False
            if name == "BOITE" and "CODE" in field_names:
                has_fdt = "fdt_value" in field_names
                val_expr = ('coalesce("fdt_value", "fat_value", \'\')'
                            if has_fdt else 'coalesce("fat_value", \'\')')
                label_field = ('"CODE" || \'\\n\' || ' + val_expr)
                label_is_expression = True
            elif "display_label" in field_names:
                label_field = "display_label"
            elif "SPAN_M" in field_names:
                label_field = "SPAN_M"
            else:
                label_field = ""
            layers[name] = {
                "geom_kind": geom_kind,
                "categories": categories,
                "label_field": label_field,
                "label_is_expression": label_is_expression,
                "feature_count": lyr.GetFeatureCount(),
                "has_fdt_id": "FDT_ID" in field_names,
            }
    finally:
        ds = None
    return layers


def write_sidecar_qml(gpkg_path, layers, styles_dir):
    os.makedirs(styles_dir, exist_ok=True)
    qml_by_layer = {}
    manifest = {
        "schema_version": "cad2gis-qgis-style-manifest-v2",
        "source_gpkg": os.path.basename(gpkg_path),
        "categorized_by": "style_key (effective CAD colour | linetype)",
        "embedded_default_styles": True,
        "layers": {},
    }
    for name, info in layers.items():
        qml = _qml(name, info["geom_kind"], info["categories"],
                   info["label_field"],
                   info.get("label_is_expression", False))
        ET.fromstring(qml)  # well-formedness gate before anything is written
        qml_path = os.path.join(styles_dir, f"{name}.qml")
        with open(qml_path, "w", encoding="utf-8") as f:
            f.write(qml)
        qml_by_layer[name] = qml
        manifest["layers"][name] = {
            "qml": os.path.basename(qml_path),
            "qml_sha256": hashlib.sha256(qml.encode("utf-8")).hexdigest(),
            "feature_count": info["feature_count"],
            "label_field": info["label_field"],
            "categories": [
                {"style_key": key, "color_rgb": rgb,
                 "linetype": lt, "count": count}
                for key, rgb, lt, count in info["categories"]],
        }
    return qml_by_layer, manifest


def embed_layer_styles(gpkg_path, qml_by_layer):
    """Write each layer's QML into the GeoPackage layer_styles table with
    useAsDefault=1 and register the table, so the .gpkg is self-styling.
    Validates by read-back (v3 styles.py pattern)."""
    connection = sqlite3.connect(os.path.abspath(gpkg_path))
    try:
        with connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS layer_styles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    f_table_catalog TEXT, f_table_schema TEXT,
                    f_table_name TEXT, f_geometry_column TEXT,
                    styleName TEXT, styleQML TEXT, styleSLD TEXT,
                    useAsDefault INTEGER, description TEXT,
                    owner TEXT, ui TEXT,
                    update_time DATETIME DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    )
                )""")
            for layer_name, qml in qml_by_layer.items():
                connection.execute(
                    "UPDATE layer_styles SET useAsDefault=0 "
                    "WHERE f_table_name=?", (layer_name,))
                connection.execute(
                    "DELETE FROM layer_styles WHERE f_table_name=? "
                    "AND styleName=?", (layer_name, STYLE_NAME))
                connection.execute(
                    """INSERT INTO layer_styles (
                        f_table_catalog, f_table_schema, f_table_name,
                        f_geometry_column, styleName, styleQML, styleSLD,
                        useAsDefault, description, owner, ui
                    ) VALUES ('', '', ?, 'geom', ?, ?, '', 1, ?, 'CAD2GIS', '')""",
                    (layer_name, STYLE_NAME, qml,
                     "Effective DWG colours (ByLayer-resolved)"))
            connection.execute(
                """INSERT OR REPLACE INTO gpkg_contents (
                    table_name, data_type, identifier, description, last_change,
                    min_x, min_y, max_x, max_y, srs_id
                ) VALUES (
                    'layer_styles', 'attributes', 'layer_styles', '',
                    strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    NULL, NULL, NULL, NULL, 0
                )""")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS gpkg_ogr_contents (
                    table_name TEXT NOT NULL PRIMARY KEY,
                    feature_count INTEGER DEFAULT NULL
                )""")
            connection.execute(
                """INSERT OR REPLACE INTO gpkg_ogr_contents
                (table_name, feature_count)
                VALUES ('layer_styles', (SELECT COUNT(*) FROM layer_styles))""")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        registered = connection.execute(
            """SELECT COUNT(*) FROM gpkg_contents
            WHERE table_name='layer_styles' AND data_type='attributes'"""
        ).fetchone()[0]
        defaults = connection.execute(
            "SELECT COUNT(*) FROM layer_styles WHERE useAsDefault=1"
        ).fetchone()[0]
        if integrity != "ok" or registered != 1 \
                or defaults != len(qml_by_layer):
            raise RuntimeError(
                "layer_styles embed validation failed: "
                f"integrity={integrity}, registered={registered}, "
                f"defaults={defaults}/{len(qml_by_layer)}")
    finally:
        connection.close()
    return len(qml_by_layer)


def build_qgz(gpkg_path, layers, styles_dir, qgz_path):
    """Build the .qgz project via headless pyqgis: all delivery layers with
    their QML styles + FDT-01/FDT-02/LINK filter groups on FDT_ID."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import (QgsApplication, QgsProject, QgsVectorLayer,
                           QgsLayerTreeGroup)

    app = QgsApplication([], False)
    app.initQgis()
    try:
        project = QgsProject.instance()
        project.clear()
        project.setTitle("Hutabohu FTTH delivery (CAD colours)")
        gpkg_abs = os.path.abspath(gpkg_path)

        def make_layer(name, display_name, subset=None):
            lyr = QgsVectorLayer(f"{gpkg_abs}|layername={name}",
                                 display_name, "ogr")
            if not lyr.isValid():
                raise RuntimeError(f".qgz: layer {name} failed to load")
            qml_path = os.path.join(styles_dir, f"{name}.qml")
            if os.path.exists(qml_path):
                lyr.loadNamedStyle(qml_path)
            if subset:
                lyr.setSubsetString(subset)
            return lyr

        root = project.layerTreeRoot()
        first = next(iter(layers))
        crs_probe = make_layer(first, first)
        project.setCrs(crs_probe.crs())

        # Span layer(s) on top — labels come from the QML (drawLabels=1)
        for name in SPAN_LAYERS:
            if name in layers:
                lyr = make_layer(name, name)
                project.addMapLayer(lyr, False)
                root.addLayer(lyr)

        # FDT domain filter groups
        for domain in FDT_DOMAINS:
            group = root.addGroup(domain)
            for name in FDT_GROUP_LAYERS:
                if name not in layers or not layers[name]["has_fdt_id"]:
                    continue
                lyr = make_layer(name, f"{name} [{domain}]",
                                 subset=f"\"FDT_ID\" = '{domain}'")
                project.addMapLayer(lyr, False)
                group.addLayer(lyr)

        # Full unfiltered delivery set (hidden by default to avoid
        # double-rendering over the domain groups)
        all_group = root.addGroup("All features")
        for name in FC_LAYERS:
            if name not in layers:
                continue
            lyr = make_layer(name, name)
            project.addMapLayer(lyr, False)
            all_group.addLayer(lyr)
        all_group.setItemVisibilityChecked(False)

        if not project.write(os.path.abspath(qgz_path)):
            raise RuntimeError(f".qgz write failed: {qgz_path}")
    finally:
        app.exitQgis()
    return qgz_path


def _validate_qgz(qgz_path):
    """The .qgz must be a zip holding one well-formed .qgs project XML."""
    if not zipfile.is_zipfile(qgz_path):
        raise RuntimeError(f".qgz is not a valid zip archive: {qgz_path}")
    with zipfile.ZipFile(qgz_path) as z:
        qgs_names = [n for n in z.namelist() if n.endswith(".qgs")]
        if not qgs_names:
            raise RuntimeError(f".qgz contains no .qgs project: {qgz_path}")
        ET.fromstring(z.read(qgs_names[0]))


def _build_qgz_isolated(gpkg_path, qgz_path):
    """Run the pyqgis project build in a subprocess: headless QGIS reliably
    segfaults during Qt teardown AFTER the project file is written, which
    would otherwise kill the converter process. Success is judged by the
    artifact (valid zip + well-formed .qgs), not the exit code."""
    if os.path.exists(qgz_path):
        os.remove(qgz_path)
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__),
         "--gpkg", os.path.abspath(gpkg_path), "--project-only"],
        capture_output=True, text=True, timeout=600)
    try:
        _validate_qgz(qgz_path)
    except (RuntimeError, ET.ParseError, FileNotFoundError, OSError) as e:
        tail = (proc.stderr or "")[-500:]
        raise RuntimeError(
            f".qgz build failed (rc={proc.returncode}): {e}\n{tail}")
    return qgz_path


def build_styles(gpkg_path, skip_project=False):
    """Orchestrate the three style tracks. Returns the manifest path."""
    layers = _collect_layer_styles(gpkg_path)
    if not layers:
        raise RuntimeError(
            "No delivery layers with style_key fields found — run the "
            "converter (S-1 colour extraction) first")
    out_dir = os.path.dirname(os.path.abspath(gpkg_path))
    styles_dir = os.path.join(out_dir, "qgis", "styles")

    qml_by_layer, manifest = write_sidecar_qml(gpkg_path, layers, styles_dir)
    print(f"  QML sidecars: {len(qml_by_layer)} layer(s) → {styles_dir}")

    embedded = embed_layer_styles(gpkg_path, qml_by_layer)
    print(f"  layer_styles embedded: {embedded} default style(s) in "
          f"{os.path.basename(gpkg_path)}")

    qgz_path = os.path.join(
        out_dir, os.path.splitext(os.path.basename(gpkg_path))[0] + ".qgz")
    if skip_project:
        manifest["project"] = None
        print("  .qgz project: skipped")
    else:
        _build_qgz_isolated(gpkg_path, qgz_path)
        manifest["project"] = {
            "qgz": os.path.basename(qgz_path),
            "fdt_groups": FDT_DOMAINS,
            "fdt_group_layers": FDT_GROUP_LAYERS,
        }
        print(f"  .qgz project: {qgz_path}")

    manifest_path = os.path.join(styles_dir, "style_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  style manifest: {manifest_path}")
    return manifest_path


def main():
    parser = argparse.ArgumentParser(
        description="Three-track QGIS styling from CAD colour fields")
    parser.add_argument("--gpkg", required=True, help="Delivery GeoPackage")
    parser.add_argument("--skip-project", action="store_true",
                        help="Skip the .qgz project track")
    parser.add_argument("--project-only", action="store_true",
                        help=argparse.SUPPRESS)  # internal subprocess mode
    args = parser.parse_args()
    if args.project_only:
        layers = _collect_layer_styles(args.gpkg)
        out_dir = os.path.dirname(os.path.abspath(args.gpkg))
        styles_dir = os.path.join(out_dir, "qgis", "styles")
        qgz_path = os.path.join(
            out_dir,
            os.path.splitext(os.path.basename(args.gpkg))[0] + ".qgz")
        build_qgz(args.gpkg, layers, styles_dir, qgz_path)
        sys.stdout.flush()
        # Hard-exit: headless Qt/QGIS teardown segfaults after a successful
        # write; the parent validates the artifact instead of the exit path.
        os._exit(0)
    build_styles(args.gpkg, skip_project=args.skip_project)


if __name__ == "__main__":
    main()
