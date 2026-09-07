"""Fresh-process verification: run only AFTER the packaging process exits.

Loads a single QGZ in an isolated directory, using its saved initial view.
Run with QGIS Python. Emits JSON evidence and an actual QGIS render.
"""
import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def verify(path, output):
    if not __debug__:
        raise RuntimeError('QGIS verification requires assertions enabled; do not use python -O')
    from qgis.core import (QgsProject, QgsMapSettings, QgsMapRendererParallelJob,
                           QgsRenderContext, QgsExpressionContextUtils, QgsApplication)
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor, QFontDatabase
    available_fonts = QFontDatabase.families()
    if not available_fonts:
        raise RuntimeError('QGIS has no fonts: provide --font with a local TTF/OTF file before visual verification')
    path, output = Path(path).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt = json.loads(path.with_suffix('.verification.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == receipt['sha256'], 'Verification receipt belongs to different QGZ'
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.qgs'))))
        sources = [n.text for n in root.findall('.//projectlayers/maplayer/datasource')]
        assert sources and all(s.startswith('attachment:') for s in sources)
        assert all(n.get('source', '').startswith('attachment:')
                   for n in root.findall('.//layer-tree-layer'))
        for source in sources:
            assert source.split('|')[0].removeprefix('attachment:') in z.namelist()
        hashes = {hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.endswith('.gpkg')}
    with tempfile.TemporaryDirectory(prefix='cad2gis-fresh-qgis-') as temp:
        moved = Path(temp) / 'relocated.qgz'
        shutil.copyfile(path, moved)
        # A same-named but invalid sidecar must never be loaded.
        (Path(temp) / 'delivery.gpkg').write_bytes(b'WRONG DATABASE')
        project = QgsProject()
        QgsProject.setInstance(project)
        assert project.read(str(moved)), 'Project failed to open'
        layers = list(project.mapLayers().values())
        assert all(layer.isValid() for layer in layers), 'Missing layer'
        assert {layer.name(): layer.featureCount() for layer in layers} == receipt['layers']
        for layer in layers:
            for index, field in enumerate(layer.fields()):
                if any(t in field.typeName().lower() for t in ('real', 'double', 'float', 'numeric', 'decimal')):
                    setup = layer.editorWidgetSetup(index)
                    assert setup.type() == 'Range' and setup.config().get('Precision') == 2
            if layer.name() == 'CABLE' and layer.labeling():
                expression = layer.labeling().settings().fieldName
                assert "'dwg_dimension' THEN ''" in expression and '[CAD curve]' in expression
        assert {hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in project.attachedFiles()
                if p.endswith('.gpkg')} == hashes
        attached = {str(Path(p).resolve()) for p in project.attachedFiles()}
        assert all(str(Path(layer.source().split('|')[0]).resolve()) in attached for layer in layers)
        extent = project.viewSettings().defaultViewExtent()
        assert not extent.isEmpty() and extent.crs().isValid(), 'Saved first-open view is empty'
        svg_count = 0
        for binding in receipt['svg_bindings']:
            layer = next(item for item in layers if item.name() == binding['layer'])
            context = QgsRenderContext()
            context.expressionContext().appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            renderer = layer.renderer()
            renderer.startRender(context, layer.fields())
            try:
                feature = next(f for f in layer.getFeatures() if f['source_handle'] == binding['source_handle'])
                context.expressionContext().setFeature(feature)
                symbol = renderer.symbolForFeature(feature, context)
                marker = symbol.symbolLayer(0)
                assert marker.layerType() == 'SvgMarker' and marker.path().startswith('base64:')
                assert QgsApplication.svgCache().svgContent(marker.path(), 64, QColor('black'), QColor('black'), 1, 1)
                svg_count += 1
            finally:
                renderer.stopRender(context)
        settings = QgsMapSettings()
        settings.setLayers(project.layerTreeRoot().layerOrder())
        settings.setDestinationCrs(extent.crs())
        settings.setExtent(extent)
        settings.setOutputSize(QSize(1500, 1000))
        settings.setBackgroundColor(QColor('white'))
        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        assert not job.errors(), str(job.errors())
        image = job.renderedImage()
        nonwhite = sum(image.pixelColor(x, y) != QColor('white')
                       for y in range(0, image.height(), 4) for x in range(0, image.width(), 4))
        assert nonwhite > 20, 'First-open render is blank'
        assert image.save(str(output / 'first-open.png'))
        # Read/save portability must survive QGIS's own serializer too.
        resaved = Path(temp) / 'resaved.qgz'
        assert project.write(str(resaved))
        with zipfile.ZipFile(resaved) as z:
            saved = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.qgs'))))
            assert all((n.text or '').startswith('attachment:')
                       for n in saved.findall('.//projectlayers/maplayer/datasource'))
        result = dict(schema='cad2gis-fresh-qgis-check-v1', qgz_sha256=digest,
                      layers=receipt['layers'], svg_features=svg_count,
                      internal_attachments_only=True, wrong_sidecar_ignored=True,
                      saved_extent=extent.toString(), default_view_nonwhite_samples=nonwhite,
                      fresh_process_verified=True, available_font_families=list(available_fonts))
        project.clear()
    (output / 'verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


def main():
    from qgis.core import QgsApplication
    from qgis.PyQt.QtGui import QFontDatabase
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--font', type=Path)
    args = p.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    if args.font:
        if QFontDatabase.addApplicationFont(str(args.font)) < 0:
            raise RuntimeError(f'Cannot load verification font: {args.font}')
    verify(args.project, args.output)


if __name__ == '__main__':
    main()
