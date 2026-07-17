"""Portable QGIS sidecars using effective CAD layer/entity styles."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from ezdxf.colors import aci2rgb

from .model import Feature
from .warehouse import LAYER_CONFIGS, LAYER_ORDER, _contract_geometry_kind

ACI = {
    1: "255,0,0,255", 2: "255,255,0,255", 3: "0,255,0,255",
    4: "0,255,255,255", 5: "0,0,255,255", 6: "255,0,255,255",
    7: "0,0,0,255",
}


def _rgba(feature):
    value = feature.style.true_color.strip().lstrip("#")
    if len(value) == 6:
        try:
            red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
            return f"{red},{green},{blue},255"
        except ValueError:
            pass
    if feature.style.aci_color in ACI:
        return ACI[feature.style.aci_color]
    try:
        color = aci2rgb(feature.style.aci_color)
        return f"{color.r},{color.g},{color.b},255"
    except (IndexError, ValueError):
        return "64,64,64,255"


def _option(parent, name, value):
    ET.SubElement(parent, "Option", name=name, value=str(value), type="QString")


def _qgis_pen_style(linetype):
    name = (linetype or "Continuous").upper()
    if "DASHDOT" in name or "CENTER" in name:
        return "dash dot"
    if "DOT" in name:
        return "dot"
    if "DASH" in name or "HIDDEN" in name:
        return "dash"
    return "solid"


def _line_width(layer_name, lineweight):
    if lineweight is not None and int(lineweight) > 0:
        return max(0.1, min(2.0, int(lineweight) / 100.0))
    return 0.6 if layer_name == "CABLE" else 0.35


def _marker_properties(layer_name):
    return {
        "BOITE": ("square", 3.2),
        "SITE": ("diamond", 4.0),
        "PTECH": ("circle", 3.2),
        "IMB": ("circle", 0.8),
    }.get(layer_name, ("circle", 2.4))


def _add_label_rotation(settings):
    data_defined = ET.SubElement(settings, "dd_properties")
    collection = ET.SubElement(data_defined, "Option", type="Map")
    ET.SubElement(collection, "Option", name="name", type="QString", value="")
    properties = ET.SubElement(collection, "Option", name="properties", type="Map")
    rotation = ET.SubElement(properties, "Option", name="LabelRotation", type="Map")
    ET.SubElement(rotation, "Option", name="active", type="bool", value="true")
    ET.SubElement(rotation, "Option", name="field", type="QString", value="style_qgis_rotation_deg")
    ET.SubElement(rotation, "Option", name="type", type="int", value="2")
    ET.SubElement(collection, "Option", name="type", type="QString", value="collection")


def _qml(layer_name, geometry_kind, styles):
    root = ET.Element(
        "qgis", version="3.40.0", styleCategories="AllStyleCategories",
        labelsEnabled="1", simplifyDrawingHints="1",
    )
    renderer = ET.SubElement(root, "renderer-v2", type="categorizedSymbol", attr="style_render_key")
    categories = ET.SubElement(renderer, "categories")
    symbols = ET.SubElement(renderer, "symbols")
    for index, (render_key, aci, color, linetype, lineweight, rotation_degrees) in enumerate(styles):
        ET.SubElement(
            categories, "category", value=render_key,
            label=render_key, symbol=str(index), render="true",
        )
        symbol = ET.SubElement(symbols, "symbol", type={"Point": "marker", "LineString": "line", "Polygon": "fill"}[geometry_kind], name=str(index), alpha="1")
        if geometry_kind == "Point":
            layer = ET.SubElement(symbol, "layer", **{"class": "SimpleMarker", "enabled": "1"})
            options = ET.SubElement(layer, "Option", type="Map")
            marker_name, marker_size = _marker_properties(layer_name)
            _option(options, "color", color)
            _option(options, "outline_color", "20,20,20,255")
            _option(options, "name", marker_name)
            _option(options, "size", marker_size)
            _option(options, "size_unit", "MM")
            _option(options, "angle", f"{rotation_degrees:.9f}")
        elif geometry_kind == "LineString":
            layer = ET.SubElement(symbol, "layer", **{"class": "SimpleLine", "enabled": "1"})
            options = ET.SubElement(layer, "Option", type="Map")
            _option(options, "line_color", color)
            _option(options, "line_style", _qgis_pen_style(linetype))
            _option(options, "line_width", _line_width(layer_name, lineweight))
            _option(options, "line_width_unit", "MM")
        else:
            layer = ET.SubElement(symbol, "layer", **{"class": "SimpleFill", "enabled": "1"})
            options = ET.SubElement(layer, "Option", type="Map")
            _option(options, "color", color.rsplit(",", 1)[0] + ",70")
            _option(options, "outline_color", color)
            _option(options, "outline_style", _qgis_pen_style(linetype))
            _option(options, "outline_width", _line_width(layer_name, lineweight))
            _option(options, "outline_width_unit", "MM")
    labeling = ET.SubElement(root, "labeling", type="simple")
    settings = ET.SubElement(labeling, "settings")
    ET.SubElement(
        settings, "text-style", fieldName="display_label", isExpression="0",
        fontFamily="Arial", fontSize="8", textColor="0,0,0,255",
    )
    ET.SubElement(
        settings, "placement", placement="0", dist="1", offsetUnits="MM",
        rotationUnit="AngleDegrees",
    )
    ET.SubElement(settings, "rendering", drawLabels="1", obstacle="1", scaleVisibility="0")
    _add_label_rotation(settings)
    ET.SubElement(root, "layerGeometryType").text = {"Point": "0", "LineString": "1", "Polygon": "2"}[geometry_kind]
    return ET.tostring(root, encoding="unicode")


def _embed_default_styles(delivery_path, qml_by_layer):
    """Register portable defaults so dragging the GeoPackage into QGIS is styled."""
    connection = sqlite3.connect(Path(delivery_path).resolve())
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
                )"""
            )
            for layer_name, qml in qml_by_layer.items():
                connection.execute(
                    "UPDATE layer_styles SET useAsDefault=0 WHERE f_table_name=?",
                    (layer_name,),
                )
                connection.execute(
                    "DELETE FROM layer_styles WHERE f_table_name=? AND styleName=?",
                    (layer_name, "CAD2GIS v3 DWG style"),
                )
                connection.execute(
                    """INSERT INTO layer_styles (
                        f_table_catalog, f_table_schema, f_table_name,
                        f_geometry_column, styleName, styleQML, styleSLD,
                        useAsDefault, description, owner, ui
                    ) VALUES ('', '', ?, 'geom', ?, ?, '', 1, ?, 'CAD2GIS', '')""",
                    (
                        layer_name, "CAD2GIS v3 DWG style", qml,
                        "Effective DWG colours and source-backed labels",
                    ),
                )
            connection.execute(
                """INSERT OR REPLACE INTO gpkg_contents (
                    table_name, data_type, identifier, description, last_change,
                    min_x, min_y, max_x, max_y, srs_id
                ) VALUES (
                    'layer_styles', 'attributes', 'layer_styles', '',
                    strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    NULL, NULL, NULL, NULL, 0
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS gpkg_ogr_contents (
                    table_name TEXT NOT NULL PRIMARY KEY,
                    feature_count INTEGER DEFAULT NULL
                )"""
            )
            connection.execute(
                """INSERT OR REPLACE INTO gpkg_ogr_contents (table_name, feature_count)
                VALUES ('layer_styles', (SELECT COUNT(*) FROM layer_styles))"""
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        registered = connection.execute(
            """SELECT COUNT(*) FROM gpkg_contents
            WHERE table_name='layer_styles' AND data_type='attributes'"""
        ).fetchone()[0]
        style_count = connection.execute("SELECT COUNT(*) FROM layer_styles").fetchone()[0]
        ogr_count = connection.execute(
            "SELECT feature_count FROM gpkg_ogr_contents WHERE table_name='layer_styles'"
        ).fetchone()[0]
        if integrity != "ok" or registered != 1 or style_count != len(qml_by_layer) or ogr_count != style_count:
            raise RuntimeError(
                "Styled delivery GeoPackage validation failed: "
                f"integrity={integrity}, registered={registered}, "
                f"styles={style_count}, ogr_count={ogr_count}"
            )
    finally:
        connection.close()


def write_styles(output_dir, features, delivery_path=None):
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    by_class = defaultdict(list)
    for feature in features:
        by_class[feature.feature_class].append(feature)
    manifest = {
        "schema_version": "cad2gis-qgis-style-manifest-v2",
        "embedded_default_styles": delivery_path is not None,
        "layers": {},
    }
    qml_by_layer = {}
    for layer_name in LAYER_ORDER:
        observed = {}
        for feature in by_class[layer_name]:
            key = str(feature.attributes.get("delivery_style_render_key", feature.style.render_key))
            qgis_rotation = float(feature.attributes.get(
                "delivery_style_qgis_rotation_deg", feature.style.qgis_rotation_degrees,
            ))
            observed.setdefault(key, (
                key, feature.style.aci_color, _rgba(feature), feature.style.linetype,
                feature.style.lineweight, qgis_rotation,
            ))
        styles = [observed[key] for key in sorted(observed)] or [
            ("ACI:7|LT:Continuous|LW:-1|ROT_QGIS:0.000000000", 7, "0,0,0,255", "Continuous", -1, 0.0)
        ]
        qml_path = destination / f"{layer_name}.qml"
        qml = _qml(
            layer_name, _contract_geometry_kind(LAYER_CONFIGS[layer_name]["geometry_type"]), styles,
        )
        qml_path.write_text(qml, encoding="utf-8")
        qml_by_layer[layer_name] = qml
        manifest["layers"][layer_name] = {
            "qml": qml_path.name,
            "qml_sha256": hashlib.sha256(qml.encode("utf-8")).hexdigest(),
            "aci_categories": sorted({item[1] for item in styles}),
            "render_categories": [item[0] for item in styles],
            "embedded_as_default": delivery_path is not None,
        }
    if delivery_path is not None:
        _embed_default_styles(delivery_path, qml_by_layer)
    manifest_path = destination / "style_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
