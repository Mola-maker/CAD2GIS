"""Fresh-process validation and actual QGIS rendering of an OSM review package."""

import argparse
import json
import math
import os
from pathlib import Path

from .qgis_review import OSM_CREDIT, _svg_bindings, sha


def verify(project_path, image_path, *, title="CAD2GIS OSM review"):
    from qgis.core import (
        QgsProject,
        QgsVectorLayer,
        QgsCoordinateTransform,
        QgsMapSettings,
        QgsMapRendererParallelJob,
    )
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor, QPainter, QFont

    project_path = Path(project_path).resolve()
    receipt = json.loads(
        project_path.with_suffix(".review.json").read_text(encoding="utf-8")
    )
    if receipt["sha256"] != sha(project_path):
        raise ValueError("Review QGZ hash mismatch")
    project = QgsProject()
    QgsProject.setInstance(project)
    if not project.read(str(project_path)):
        raise ValueError("Cannot reopen review project")
    by_name = {layer.name(): layer for layer in project.mapLayers().values()}
    if len(by_name) != len(project.mapLayers()) or not all(
        layer.isValid() for layer in by_name.values()
    ):
        raise ValueError("Invalid or duplicate reopened layers")
    cad = [by_name[name] for name in receipt["cad_layers"]]
    cad_paths = {Path(layer.source().split("|", 1)[0]).resolve() for layer in cad}
    if sorted(sha(p) for p in cad_paths) != receipt["cad_database_hashes"]:
        raise ValueError("Canonical geometry/attributes changed")
    if not receipt["standalone"] and any(
        not p.is_relative_to(project_path.parent) for p in cad_paths
    ):
        raise ValueError("Editable review still depends on its original directory")
    for name, count in {**receipt["cad_layers"], **receipt["osm_layers"]}.items():
        if (
            not isinstance(by_name[name], QgsVectorLayer)
            or by_name[name].featureCount() != count
        ):
            raise ValueError("Layer feature count changed: " + name)
    osm_paths = {
        Path(by_name[name].source().split("|", 1)[0]).resolve()
        for name in receipt["osm_layers"]
    }
    if {sha(p) for p in osm_paths} != {receipt["osm_database_sha256"]}:
        raise ValueError("OSM database hash mismatch")
    if _svg_bindings(cad) != receipt["svg_bindings"]:
        raise ValueError("SVG-to-feature bindings changed")
    map_samples = {}
    for layer in cad:
        transform = QgsCoordinateTransform(layer.crs(), project.crs(), project)
        for feature in sorted(
            layer.getFeatures(), key=lambda f: str(f["source_handle"])
        )[:10]:
            if not feature.geometry().isEmpty():
                point = transform.transform(feature.geometry().boundingBox().center())
                map_samples[layer.name() + ":" + str(feature["source_handle"])] = [
                    point.x(),
                    point.y(),
                ]
    correction = receipt["coordinate_review"]
    stored, present = project.readEntry("CAD2GISReview", "coordinateCorrection")
    if not present or json.loads(stored) != correction:
        raise ValueError("Project lost its coordinate-review provenance")
    residuals = []
    if correction:
        for control in correction["controls"]:
            layer = by_name[control["layer"]]
            feature = next(
                f
                for f in layer.getFeatures()
                if str(f["source_handle"]) == control["source_handle"]
            )
            point = QgsCoordinateTransform(
                layer.crs(), project.crs(), project
            ).transform(feature.geometry().asPoint())
            expected = [
                control["nominal_map_point"][0] + correction["dx"],
                control["nominal_map_point"][1] + correction["dy"],
            ]
            if math.dist((point.x(), point.y()), expected) > 1e-6:
                raise ValueError(
                    "Reopened project lost or changed its coordinate operation"
                )
            residuals.append(
                math.dist((point.x(), point.y()), control["target_map_point"])
            )
    visible = [
        layer
        for layer in project.layerTreeRoot().layerOrder()
        if project.layerTreeRoot().findLayer(layer.id()).isVisible()
    ]
    if not any(layer.name() == "OSM roads" for layer in visible) or any(
        not isinstance(layer, QgsVectorLayer) for layer in visible
    ):
        raise ValueError(
            "Snapshot must use offline OSM vectors with no remote raster request"
        )
    settings = QgsMapSettings()
    settings.setDestinationCrs(project.crs())
    settings.setTransformContext(project.transformContext())
    settings.setExtent(project.viewSettings().defaultViewExtent())
    settings.setLayers(visible)
    settings.setOutputSize(QSize(2000, 1400))
    settings.setBackgroundColor(QColor("#f4f3ef"))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    if job.errors():
        raise ValueError(
            "QGIS render errors: "
            + repr([(e.layerID, e.message) for e in job.errors()])
        )
    image = job.renderedImage()
    # Check actual map pixels before adding the title/footer (which could hide a blank map).
    colors = {
        image.pixelColor(x, y).name()
        for x in range(20, 1980, 9)
        for y in range(60, 1330, 9)
    }
    if len(colors) < 12:
        raise ValueError("Rendered map appears blank")
    painter = QPainter(image)
    painter.fillRect(0, 0, 2000, 48, QColor("white"))
    painter.fillRect(0, 1350, 2000, 50, QColor("white"))
    painter.setPen(QColor("#182b35"))
    painter.setFont(QFont("Arial", 14))
    painter.drawText(20, 32, title)
    painter.setFont(QFont("Arial", 10))
    painter.drawText(
        20,
        1372,
        OSM_CREDIT + " | Reference map; independent survey accuracy unverified",
    )
    status = (
        "CAD coordinate-label translation; original database unchanged"
        if correction
        else "Converted CAD coordinates retained; original database unchanged"
    )
    painter.drawText(20, 1392, status)
    painter.end()
    image_path = Path(image_path)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(image_path)):
        raise ValueError("Cannot save snapshot")
    result = dict(
        schema="cad2gis.qgis-osm-verification.v1",
        project_sha256=receipt["sha256"],
        qgis_read=True,
        all_layers_valid=True,
        cad_bytes_unchanged=True,
        svg_bindings_verified=len(receipt["svg_bindings"]),
        osm_features=sum(receipt["osm_layers"].values()),
        control_residuals_map_m=residuals,
        snapshot_sha256=sha(image_path),
        sampled_map_colors=len(colors),
        map_samples=map_samples,
        absolute_accuracy_verified=False,
    )
    image_path.with_suffix(".json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    project.clear()
    return result


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import QgsApplication
    from qgis.PyQt.QtGui import QFontDatabase

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--title", default="CAD2GIS OSM review")
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    if args.font and QFontDatabase.addApplicationFont(str(args.font)) < 0:
        raise ValueError("Cannot load requested font")
    print(json.dumps(verify(args.project, args.image, title=args.title)), flush=True)


if __name__ == "__main__":
    main()
