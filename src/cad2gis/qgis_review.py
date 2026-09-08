"""Portable QGIS/OSM review views with explicit, source-bound display registration.

The original GeoPackage bytes and CAD lengths are never rewritten. Coordinate
label corrections apply only to this review project's transform context, not
to the canonical conversion or its acceptance status. Requires QGIS Python.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from urllib.parse import unquote
from contextlib import closing
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_CREDIT = "© OpenStreetMap contributors | openstreetmap.org/copyright"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def coordinate_pair(text):
    """Read one explicit latitude/longitude pair, not CAD text formatting numbers."""
    pairs = re.findall(
        r"(?<![\d.])(-?\d{1,2}\.\d{4,})\s*°\s*,?\s*(\d{2,3}\.\d{4,})\s*°", text
    )
    if len(pairs) != 1:
        raise ValueError(
            "Coordinate evidence must contain exactly one explicit degree pair"
        )
    lat, lon = map(float, pairs[0])
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Coordinate evidence is outside geographic bounds")
    return lon, lat


def fit_translation(pairs):
    """Fit a shape-preserving translation; residuals are not independent accuracy."""
    if len(pairs) < 2:
        raise ValueError("Review translation requires at least two distinct controls")
    sources, targets = [], []
    for source, target in pairs:
        if (
            len(source) != 2
            or len(target) != 2
            or not all(math.isfinite(v) for v in (*source, *target))
        ):
            raise ValueError("Controls must contain finite two-dimensional points")
        sources.append(tuple(source))
        targets.append(tuple(target))
    if len(set(sources)) != len(sources) or len(set(targets)) != len(targets):
        raise ValueError("Duplicate control coordinates")
    dx = math.fsum(t[0] - s[0] for s, t in pairs) / len(pairs)
    dy = math.fsum(t[1] - s[1] for s, t in pairs) / len(pairs)
    return {
        "model": "translation",
        "dx": dx,
        "dy": dy,
        "scale": 1.0,
        "rotation_degrees": 0.0,
        "before_residuals_map_m": [math.dist(s, t) for s, t in pairs],
        "after_residuals_map_m": [
            math.dist((s[0] + dx, s[1] + dy), t) for s, t in pairs
        ],
        "independent_accuracy_verified": False,
        "scope": "project_display_only",
    }


def _evidence_controls(config, source_evidence):
    if (
        config.get("schema") != "cad2gis.qgis-coordinate-review.v1"
        or config.get("model") != "translation"
    ):
        raise ValueError("Unsupported coordinate review contract")
    if config.get("scope") != "project_display_only":
        raise ValueError("Coordinate review cannot change canonical acceptance")
    if sha(source_evidence) != config["source_evidence_sha256"]:
        raise ValueError("Source coordinate evidence hash mismatch")
    rows = []
    seen = set()
    with closing(
        sqlite3.connect(Path(source_evidence).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        for control in config["controls"]:
            key = (control["layer"], control["source_handle"])
            if key in seen:
                raise ValueError("Duplicate coordinate review target")
            seen.add(key)

            def text_record(handle):
                found = db.execute(
                    "SELECT text,owner_handle,source_sha256,cad_layout FROM source_text WHERE cad_handle=?",
                    (handle,),
                ).fetchall()
                if (
                    len(found) != 1
                    or found[0][2] != config["source_sha256"]
                    or found[0][3] != "Model"
                ):
                    raise ValueError(
                        "Coordinate evidence must uniquely belong to this source model"
                    )
                return found[0]

            coordinate = text_record(control["coordinate_text_handle"])
            identity = text_record(control["identity_text_handle"])
            if not coordinate[1] or coordinate[1] != identity[1]:
                raise ValueError(
                    "Coordinate and device identity must belong to the same CAD table"
                )
            if identity[0] != control["source_label"]:
                raise ValueError("CAD table device identity mismatch")
            lon, lat = coordinate_pair(coordinate[0])
            rows.append(
                {
                    **control,
                    "coordinate_text": coordinate[0],
                    "longitude": lon,
                    "latitude": lat,
                }
            )
    if len(rows) < 2:
        raise ValueError("At least two source-bound coordinate labels are required")
    return rows


def _osm_layers(data_path):
    from qgis.core import (
        QgsVectorLayer,
        QgsFeature,
        QgsField,
        QgsGeometry,
        QgsPointXY,
        QgsFillSymbol,
        QgsLineSymbol,
    )
    from qgis.PyQt.QtCore import QMetaType

    value = json.loads(Path(data_path).read_text(encoding="utf-8"))
    if value.get("remark") or not isinstance(value.get("elements"), list):
        raise ValueError("Incomplete or invalid Overpass response")
    specs = [
        (
            "OSM land use",
            "landuse",
            "Polygon",
            {"color": "228,236,220,255", "outline_style": "no"},
        ),
        (
            "OSM water",
            "water",
            "Polygon",
            {"color": "170,212,230,255", "outline_style": "no"},
        ),
        (
            "OSM buildings",
            "buildings",
            "Polygon",
            {
                "color": "220,214,206,255",
                "outline_color": "185,179,173,255",
                "outline_width": "0.1",
            },
        ),
        (
            "OSM roads",
            "roads",
            "LineString",
            {"line_color": "225,184,111,255", "line_width": "1.25"},
        ),
    ]
    result = []
    for name, table, kind, style in specs:
        layer = QgsVectorLayer(kind + "?crs=EPSG:4326", name, "memory")
        string_type = (
            QMetaType.Type.QString if hasattr(QMetaType, "Type") else QMetaType.QString
        )
        layer.dataProvider().addAttributes(
            [QgsField("osm_id", string_type), QgsField("name", string_type)]
        )
        layer.updateFields()
        features = []
        for way in value["elements"]:
            tags, geometry = way.get("tags", {}), way.get("geometry", [])
            if not (
                (table == "roads" and "highway" in tags)
                or (table == "buildings" and "building" in tags)
                or (table == "water" and tags.get("natural") == "water")
                or (table == "landuse" and "landuse" in tags)
            ):
                continue
            points = [
                QgsPointXY(p["lon"], p["lat"])
                for p in geometry
                if p and "lon" in p and "lat" in p
            ]
            if len(points) < 2 or (
                kind == "Polygon" and (len(points) < 4 or points[0] != points[-1])
            ):
                continue
            feature = QgsFeature(layer.fields())
            feature.setAttributes([str(way["id"]), tags.get("name", "")])
            feature.setGeometry(
                QgsGeometry.fromPolygonXY([points])
                if kind == "Polygon"
                else QgsGeometry.fromPolylineXY(points)
            )
            features.append(feature)
        layer.dataProvider().addFeatures(features)
        layer.updateExtents()
        layer.renderer().setSymbol(
            QgsFillSymbol.createSimple(style)
            if kind == "Polygon"
            else QgsLineSymbol.createSimple(style)
        )
        layer.setAbstract(
            OSM_CREDIT + "; cached public OSM vectors; not surveyed control"
        )
        result.append((layer, table))
    if not any(layer.featureCount() for layer, _ in result):
        raise ValueError("OSM review data is empty")
    return result


def _swap_source(layer, source):
    renderer = layer.renderer().clone()
    labeling = layer.labeling().clone() if layer.labeling() else None
    enabled = layer.labelsEnabled()
    layer.setDataSource(source, layer.name(), "ogr")
    layer.setRenderer(renderer)
    if labeling:
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(enabled)
    if not layer.isValid():
        raise ValueError("Relocated layer is invalid")


def _svg_bindings(layers):
    from qgis.core import QgsRenderContext, QgsExpressionContextUtils

    result = []
    for layer in layers:
        if layer.geometryType() != 0:
            continue
        context = QgsRenderContext()
        context.expressionContext().appendScopes(
            QgsExpressionContextUtils.globalProjectLayerScopes(layer)
        )
        renderer = layer.renderer()
        renderer.startRender(context, layer.fields())
        try:
            for feature in layer.getFeatures():
                context.expressionContext().setFeature(feature)
                symbol = renderer.symbolForFeature(feature, context)
                if symbol is None:
                    raise ValueError("Point has no selected renderer symbol")
                marker = symbol.symbolLayer(0)
                if marker.layerType() == "SvgMarker":
                    if not marker.path().startswith("base64:"):
                        raise ValueError("Review SVG depends on an external file")
                    payload = base64.b64decode(marker.path()[7:], validate=True)
                    result.append(
                        {
                            "layer": layer.name(),
                            "source_handle": str(feature["source_handle"]),
                            "svg_sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    )
        finally:
            renderer.stopRender(context)
    return sorted(result, key=lambda row: (row["layer"], row["source_handle"]))


def build(
    project_path,
    osm_data,
    output,
    *,
    registration=None,
    source_evidence=None,
    standalone=False,
):
    """Write a new review project, preserving all CAD database bytes and SVGs."""
    from qgis.core import (
        QgsProject,
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsReferencedRectangle,
        QgsRectangle,
        QgsVectorFileWriter,
        QgsVectorLayer,
        QgsRasterLayer,
    )
    from pyproj import Transformer

    output, project_path = Path(output).resolve(), Path(project_path).resolve()
    if output.exists():
        raise FileExistsError(output)
    project = QgsProject()
    QgsProject.setInstance(project)
    if not project.read(str(project_path)):
        raise ValueError("Cannot read input QGIS project")
    for layer in list(project.mapLayers().values()):
        if (
            isinstance(layer, QgsRasterLayer)
            and layer.providerType() == "wms"
            and OSM_URL in unquote(layer.source())
        ):
            project.removeMapLayer(layer.id())
    layers = list(project.mapLayers().values())
    if not layers or any(
        not layer.isValid() or not isinstance(layer, QgsVectorLayer) for layer in layers
    ):
        raise ValueError(
            "Input must contain valid canonical vector layers: "
            + repr([(layer.name(), layer.isValid(), layer.source()) for layer in layers])
        )
    if len({layer.name() for layer in layers}) != len(layers):
        raise ValueError("Ambiguous input layer names")
    bindings = _svg_bindings(layers)
    databases = {Path(layer.source().split("|", 1)[0]).resolve() for layer in layers}
    if any(path.suffix.lower() != ".gpkg" for path in databases):
        raise ValueError("Input layers must use GeoPackage")
    hashes = {path: sha(path) for path in databases}
    correction = None
    destination = QgsCoordinateReferenceSystem("EPSG:3857")
    if registration:
        config = json.loads(Path(registration).read_text(encoding="utf-8"))
        if set(hashes.values()) != {config["delivery_sha256"]} or not source_evidence:
            raise ValueError("Coordinate correction belongs to a different delivery")
        controls = _evidence_controls(config, source_evidence)
        pairs = []
        to_map = Transformer.from_crs(4326, 3857, always_xy=True)
        source_crs = None
        for control in controls:
            layer = next(
                (layer for layer in layers if layer.name() == control["layer"]), None
            )
            if layer is None or layer.geometryType() != 0:
                raise ValueError("Coordinate control requires a matching point layer")
            features = [
                f
                for f in layer.getFeatures()
                if str(f["source_handle"]) == control["source_handle"]
            ]
            if len(features) != 1:
                raise ValueError(
                    "Coordinate control must identify exactly one source feature"
                )
            if str(features[0]["display_label"]) != control["expected_display_label"]:
                raise ValueError(
                    "Coordinate control label does not match the bound delivery feature"
                )
            if control["source_label"].removeprefix("EXT.") != control[
                "expected_display_label"
            ].removeprefix("EXT."):
                raise ValueError(
                    "Source table and delivery refer to different device identities"
                )
            if source_crs is not None and layer.crs() != source_crs:
                raise ValueError("Review controls must use one source CRS")
            source_crs = layer.crs()
            point = features[0].geometry().asPoint()
            if (
                math.dist((point.x(), point.y()), control["expected_native_point"])
                > 1e-8
            ):
                raise ValueError(
                    "Control native coordinates differ from the reviewed source binding"
                )
            # Fit and apply the same explicit operation; saved projects can carry
            # different historical datum operations for an identical EPSG pair.
            native_to_map = Transformer.from_crs(
                source_crs.toWkt(), 3857, always_xy=True
            )
            nominal = native_to_map.transform(point.x(), point.y())
            target = to_map.transform(control["longitude"], control["latitude"])
            control.update(
                source_point=[point.x(), point.y()],
                nominal_map_point=list(nominal),
                target_map_point=list(target),
            )
            pairs.append((nominal, target))
        if source_crs == destination or any(
            layer.crs() != source_crs for layer in layers
        ):
            raise ValueError(
                "Display correction requires one distinct source CRS; do not relabel layers"
            )
        correction = {
            **fit_translation(pairs),
            "controls": controls,
            "source_sha256": config["source_sha256"],
            "source_evidence_sha256": config["source_evidence_sha256"],
        }
        operation = Transformer.from_crs(source_crs.toWkt(), 3857, always_xy=True)
        operation.transform(*controls[0]["source_point"])
        base = operation.get_last_used_operation().definition
        if not base.startswith("proj=pipeline "):
            raise ValueError("Expected an explicit normalized CRS pipeline")
        pipeline = (
            base
            + f" step proj=affine xoff={correction['dx']:.17g} yoff={correction['dy']:.17g}"
        )
        context = project.transformContext()
        if not context.addCoordinateOperation(source_crs, destination, pipeline, False):
            raise ValueError("QGIS rejected review coordinate operation")
        project.setTransformContext(context)
        for layer in layers:
            layer.setTransformContext(context)
        correction.update(
            proj_pipeline=pipeline,
            source_crs=source_crs.authid(),
            target_crs="EPSG:3857",
        )
        check = QgsCoordinateTransform(source_crs, destination, project)
        for control in controls:
            got = check.transform(*control["source_point"])
            expected = [
                control["nominal_map_point"][0] + correction["dx"],
                control["nominal_map_point"][1] + correction["dy"],
            ]
            if math.dist((got.x(), got.y()), expected) > 1e-6:
                raise ValueError(
                    f"QGIS did not apply the exact review correction: got={(got.x(), got.y())}, expected={expected}, pipeline={pipeline}"
                )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".osm-review-", dir=output.parent
    ) as temporary:
        temporary = Path(temporary)
        # The offline map is OSM vector data, not a redistributed public tile cache.
        backgrounds = _osm_layers(osm_data)
        osm_path = temporary / "osm-basemap.gpkg"
        for index, (layer, table) in enumerate(backgrounds):
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = table
            options.actionOnExistingFile = (
                QgsVectorFileWriter.CreateOrOverwriteFile
                if index == 0
                else QgsVectorFileWriter.CreateOrOverwriteLayer
            )
            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, str(osm_path), project.transformContext(), options
            )
            if result[0] != QgsVectorFileWriter.NoError:
                raise ValueError(f"Cannot persist OSM background: {result}")
        osm_sha = sha(osm_path)
        if standalone:
            for path in databases:
                attached = Path(project.createAttachedFile(path.name))
                shutil.copyfile(path, attached)
                for layer in layers:
                    if Path(layer.source().split("|", 1)[0]).resolve() == path:
                        _swap_source(
                            layer,
                            attached.as_posix() + "|" + layer.source().split("|", 1)[1],
                        )
            osm_destination = Path(project.createAttachedFile("osm-basemap.gpkg"))
            shutil.copyfile(osm_path, osm_destination)
        else:
            for path, digest in hashes.items():
                target = output.parent / path.name
                if target.exists() and sha(target) != digest:
                    raise ValueError("Refusing to replace different adjacent CAD data")
                if not target.exists():
                    shutil.copyfile(path, target)
                for layer in layers:
                    if Path(layer.source().split("|", 1)[0]).resolve() == path:
                        _swap_source(
                            layer,
                            target.as_posix() + "|" + layer.source().split("|", 1)[1],
                        )
            osm_destination = output.parent / "osm-basemap.gpkg"
            if osm_destination.exists():
                raise FileExistsError(osm_destination)
            shutil.copyfile(osm_path, osm_destination)
        group = project.layerTreeRoot().addGroup("OpenStreetMap — cached review data")
        osm_expected = {}
        for layer, table in reversed(backgrounds):
            _swap_source(layer, osm_destination.as_posix() + "|layername=" + table)
            project.addMapLayer(layer, False)
            group.addLayer(layer)
            osm_expected[layer.name()] = layer.featureCount()
        online = QgsRasterLayer(
            "type=xyz&url=" + OSM_URL + "&zmin=0&zmax=19",
            "OpenStreetMap — online (optional)",
            "wms",
        )
        if not online.isValid():
            raise ValueError("Cannot create OpenStreetMap XYZ layer")
        online.setAttribution("© OpenStreetMap contributors")
        online.setAttributionUrl("https://www.openstreetmap.org/copyright")
        project.addMapLayer(online, False)
        node = project.layerTreeRoot().addLayer(online)
        node.setItemVisibilityChecked(False)
        project.setCrs(destination)
        extent = QgsRectangle()
        for layer in layers:
            if layer.featureCount():
                extent.combineExtentWith(
                    QgsCoordinateTransform(
                        layer.crs(), destination, project
                    ).transformBoundingBox(layer.extent())
                )
            if layer.geometryType() == 2:
                layer.setOpacity(0.20)
        if extent.isEmpty():
            raise ValueError("Review extent is empty")
        extent.scale(1.12)
        project.viewSettings().setDefaultViewExtent(
            QgsReferencedRectangle(extent, destination)
        )
        project.setTitle(
            "CAD2GIS OSM review — "
            + (
                "CAD coordinate-label translation"
                if correction
                else "nominal CAD registration"
            )
        )
        project.writeEntry(
            "CAD2GISReview", "coordinateCorrection", json.dumps(correction)
        )
        project.writeEntry("CAD2GISReview", "absoluteAccuracyVerified", False)
        project.writeEntry("CAD2GISReview", "attribution", OSM_CREDIT)
        # A sibling staging file gives relative sidecars their final directory.
        staged = output.parent / (".review-" + uuid.uuid4().hex + ".qgz")
        try:
            if not project.write(str(staged)):
                raise ValueError("Cannot write review QGZ")
            staged.replace(output)
        finally:
            staged.unlink(missing_ok=True)
        receipt = {
            "schema": "cad2gis.qgis-osm-review.v1",
            "input_project_sha256": sha(project_path),
            "sha256": sha(output),
            "standalone": standalone,
            "cad_database_hashes": sorted(hashes.values()),
            "cad_layers": {layer.name(): layer.featureCount() for layer in layers},
            "svg_bindings": bindings,
            "osm_layers": osm_expected,
            "osm_database_sha256": osm_sha,
            "osm_source_sha256": sha(osm_data),
            "coordinate_review": correction,
            "absolute_accuracy_verified": False,
            "geometry_and_attributes_unchanged": True,
            "attribution": OSM_CREDIT,
        }
        project.clear()
    output.with_suffix(".review.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return receipt


def main():
    from qgis.core import QgsApplication
    from qgis.PyQt.QtGui import QFontDatabase

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--osm-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--source-evidence", type=Path)
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    if args.font and QFontDatabase.addApplicationFont(str(args.font)) < 0:
        raise ValueError("Cannot load requested font")
    result = build(
        args.project,
        args.osm_data,
        args.output,
        registration=args.registration,
        source_evidence=args.source_evidence,
        standalone=args.standalone,
    )
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
