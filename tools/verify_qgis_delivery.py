"""Run with QGIS Python to reopen relocated delivery projects and render evidence."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from qgis.core import QgsApplication, QgsProject, QgsMapSettings, QgsMapRendererParallelJob, Qgis  # noqa: E402
from qgis.PyQt.QtCore import QSize  # noqa: E402
from qgis.PyQt.QtGui import QFontDatabase, QColor  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()
args.root = args.root.resolve()
args.report = args.report.resolve()
app = QgsApplication([], False)
app.initQgis()
if Path("C:/Windows/Fonts/arial.ttf").exists():
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
results = []
for path in sorted(args.root.rglob("delivery.qgz")):
    project = QgsProject()
    assert project.read(str(path)), str(path)
    layers = list(project.mapLayers().values())
    with sqlite3.connect((path.parent / "delivery.gpkg").as_uri() + "?mode=ro", uri=True) as db:
        expected = dict(db.execute('SELECT table_name, (SELECT 0) FROM gpkg_geometry_columns'))
        for name in expected:
            expected[name] = db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
    assert {layer.name(): layer.featureCount() for layer in layers} == expected, str(path)
    assert all(layer.isValid() for layer in layers), str(path)
    assert project.crs().isValid(), (str(path), [(layer.name(), layer.crs().authid()) for layer in layers])
    values = []
    for layer in layers:
        assert Path(layer.source().split("|", 1)[0]).resolve().parent == path.parent.resolve(), layer.source()
        assert layer.crs().isValid(), (path, layer.name(), "invalid layer CRS")
        assert layer.labelsEnabled(), (path, layer.name(), "labels disabled")
        label = layer.labeling().settings()
        if layer.name() == "CABLE":
            assert label.isExpression and "length_value_m" in label.fieldName
        values.append({"layer": layer.name(), "features": layer.featureCount(), "label": label.fieldName})
    results.append({"project": path.relative_to(args.root).as_posix(), "crs": project.crs().authid(), "layers": values})
    cable = next(layer for layer in layers if layer.name() == "CABLE")
    settings = QgsMapSettings()
    settings.setLayers(sorted(layers, key=lambda layer: layer.geometryType()))
    settings.setDestinationCrs(project.crs())
    extent = cable.extent()
    extent.scale(1.1)
    settings.setExtent(extent)
    settings.setOutputSize(QSize(1400, 1000))
    settings.setBackgroundColor(QColor("white"))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    image_path = args.report.parent / (path.relative_to(args.root).as_posix().replace("/", "-") + ".png")
    assert job.renderedImage().save(str(image_path))
    if path.parent.name == "drawing-02":
        extent.scale(0.25)
        settings.setExtent(extent)
        detail_job = QgsMapRendererParallelJob(settings)
        detail_job.start()
        detail_job.waitForFinished()
        assert detail_job.renderedImage().save(str(args.report.parent / "lamteh-delivery-detail.png"))
    project.clear()
args.report.write_text(json.dumps({"qgis": Qgis.QGIS_VERSION, "projects": results}, indent=2), encoding="utf-8")
print(json.dumps({"verified_projects": len(results), "report": str(args.report)}))
