"""Create a standalone QGZ with attached GeoPackage; optional exact-source SVG.

Run with QGIS Python. This is an optional presentation step, not conversion.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def package(project_path, output, *, store=None, symbol_id=None, layer_name=None,
            source_handle=None, delivery_manifest=None, bindings=None):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.qgis-package-', dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        receipt = _package(project_path, staged, store=store, symbol_id=symbol_id,
                           layer_name=layer_name, source_handle=source_handle,
                           delivery_manifest=delivery_manifest, bindings=bindings)
        from .cad2gis_v3.artifact_io import inherit_output_permissions
        inherit_output_permissions(Path(temporary))
        staged.rename(output)
        receipt['output'] = str(output)
        output.with_suffix('.verification.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def _package(project_path, output, *, store=None, symbol_id=None, layer_name=None,
             source_handle=None, delivery_manifest=None, bindings=None):
    from qgis.core import (QgsProject, QgsVectorLayer, QgsRendererCategory, QgsRenderContext,
                          QgsExpressionContextUtils, QgsRectangle, QgsReferencedRectangle, QgsCoordinateTransform,
                          QgsEditorWidgetSetup)
    from cad2gis.symbol_assets import export_qml
    from cad2gis.presentation import LENGTH_EXPRESSION

    project_path, output = Path(project_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    options = [store, symbol_id, layer_name, source_handle, delivery_manifest]
    if bindings is not None:
        if not store or not delivery_manifest or any([symbol_id, layer_name, source_handle]) or not bindings:
            raise ValueError("Bindings require store and delivery-manifest, without individual binding flags")
    elif any(options) and not all(options):
        raise ValueError("SVG requires store, symbol-id, layer, source-handle and delivery-manifest together")
    project = QgsProject()
    QgsProject.setInstance(project)
    if not project.read(str(project_path)):
        raise ValueError(f"Cannot open project: {project_path}")
    layers = list(project.mapLayers().values())
    if not layers or any(not layer.isValid() for layer in layers):
        raise ValueError("Input project has unavailable layers; use the matching complete delivery")
    inputs = {}
    for layer in layers:
        filename = Path(layer.source().split("|", 1)[0]).resolve()
        if filename.suffix.lower() != ".gpkg" or not filename.is_relative_to(project_path.parent):
            raise ValueError("Only project-local GeoPackage layers can be attached")
        inputs[filename] = sha(filename)
    receipt = {"input_project_sha256": sha(project_path), "standalone": True, "svg_bindings": []}
    if store:
        requested = bindings if bindings is not None else [
            {"layer": layer_name, "source_handle": source_handle, "symbol_id": symbol_id}]
        identities = [(item["layer"], item["source_handle"]) for item in requested]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate SVG binding target")
        manifest = json.loads(Path(delivery_manifest).read_text(encoding="utf-8"))
        with closing(sqlite3.connect(Path(store).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            metadata = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM metadata")}
            stored_handles = dict(db.execute("SELECT symbol_id,source_handles_json FROM symbols"))
        if metadata.get("source_sha256") != manifest.get("source_sha256"):
            raise ValueError("SVG store and delivery belong to different CAD sources")
        allowed = {entry["gpkg_sha256"] for entry in manifest["deliveries"]}
        if any(digest not in allowed for digest in inputs.values()):
            raise ValueError("Delivery database does not match its manifest")
        conditions = {}
        def quote(text):
            return "'" + text.replace("'", "''") + "'"
        for item in requested:
            layer_name, source_handle, symbol_id = item["layer"], item["source_handle"], item["symbol_id"]
            if json.loads(stored_handles.get(symbol_id, "null")) != [source_handle]:
                raise ValueError("Only an SVG extracted from this exact source instance can be bound automatically")
            selected = [layer for layer in layers if layer.name() == layer_name]
            if len(selected) != 1:
                raise ValueError("SVG target layer is missing or ambiguous")
            layer = selected[0]
            if layer.geometryType() != 0:
                raise ValueError("SvgMarker requires a point layer")
            matched = [feature for feature in layer.getFeatures() if feature["source_handle"] == source_handle]
            if len(matched) != 1:
                raise ValueError("SVG source instance must resolve to exactly one feature")
            renderer = layer.renderer()
            if renderer.type() != "categorizedSymbol" or renderer.classAttribute() != "style_render_key":
                raise ValueError("This optional binding expects the canonical categorized CAD renderer")
            with tempfile.TemporaryDirectory(prefix="cad2gis-svg-style-") as temp:
                qml = Path(temp) / "symbol.qml"
                binding = export_qml(store, symbol_id, qml)
                candidate = QgsVectorLayer("Point", "candidate", "memory")
                message, ok = candidate.loadNamedStyle(str(qml))
                if not ok:
                    raise ValueError(message)
                symbol = candidate.renderer().symbol().clone()
            key = "cad2gis_svg_" + symbol_id
            conditions.setdefault(layer_name, []).append(
                f'WHEN "source_handle" = {quote(source_handle)} THEN {quote(key)}')
            renderer.addCategory(QgsRendererCategory(key, symbol, f"{symbol_id} (source {source_handle})"))
            binding.pop("qml", None)
            receipt["svg_bindings"].append({**binding, "layer": layer_name, "source_handle": source_handle,
                                            "feature_count": 1, "candidate_visual_review": True,
                                            "applied_to_project": True})
        for name, expressions in conditions.items():
            layer = next(layer for layer in layers if layer.name() == name)
            layer.renderer().setClassAttribute('CASE ' + ' '.join(expressions) + ' ELSE "style_render_key" END')
    attached = {}
    for filename, digest in inputs.items():
        path = Path(project.createAttachedFile(filename.name))
        shutil.copyfile(filename, path)
        if sha(path) != digest:
            raise ValueError("Attached database bytes differ")
        attached[filename] = path
    expected = {}
    extent = QgsRectangle()
    for layer in layers:
        source, suffix = layer.source().split("|", 1)
        # setDataSource keeps symbology when the geometry type is unchanged.
        renderer = layer.renderer().clone()
        labeling = layer.labeling().clone() if layer.labeling() else None
        if labeling and layer.name() == 'CABLE' and 'length_value_m' in layer.fields().names():
            settings = labeling.settings()
            settings.fieldName = LENGTH_EXPRESSION
            settings.isExpression = True
            labeling.setSettings(settings)
        count = layer.featureCount()
        # QGIS attachment path matching is a literal prefix comparison. Qt returns
        # forward slashes even on Windows; pathlib's native backslashes bypass it.
        layer.setDataSource(attached[Path(source).resolve()].as_posix() + "|" + suffix, layer.name(), "ogr")
        layer.setRenderer(renderer)
        for index, field in enumerate(layer.fields()):
            if any(token in field.typeName().lower() for token in ('real', 'double', 'float', 'numeric', 'decimal')):
                layer.setEditorWidgetSetup(index, QgsEditorWidgetSetup('Range',
                    dict(Precision=2, AllowNull=True, Style='SpinBox', Min=-1e100, Max=1e100, Step=0.01)))
        if labeling:
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)
        if not layer.isValid() or layer.featureCount() != count:
            raise ValueError("Layer changed while attaching database")
        expected[layer.name()] = count
        if count:
            bounds = QgsCoordinateTransform(layer.crs(), project.crs(), project).transformBoundingBox(layer.extent())
            extent.combineExtentWith(bounds)
    if extent.isEmpty() or not project.crs().isValid():
        raise ValueError("Standalone project needs a nonempty default extent and valid CRS")
    extent.scale(1.1)
    project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, project.crs()))
    output.parent.mkdir(parents=True, exist_ok=True)
    if not project.write(str(output)):
        raise ValueError("Unable to save standalone QGZ")
    with zipfile.ZipFile(output) as archive:
        xml = ET.fromstring(archive.read(next(name for name in archive.namelist() if name.endswith('.qgs'))))
        sources = [node.text or '' for node in xml.findall('.//projectlayers/maplayer/datasource')]
        if any(not value.startswith('attachment:') for value in sources):
            raise ValueError(f"Standalone QGZ still contains external data sources: {sources}")
    project.clear()
    # Reopen a single copied QGZ in an otherwise empty directory.
    with tempfile.TemporaryDirectory(prefix="cad2gis-single-file-proof-") as temp:
        moved = Path(temp) / output.name
        shutil.copyfile(output, moved)
        reopened = QgsProject()
        if not reopened.read(str(moved)):
            raise ValueError("Standalone relocation failed")
        found = list(reopened.mapLayers().values())
        if any(not item.isValid() for item in found) or {item.name(): item.featureCount() for item in found} != expected:
            raise ValueError("Standalone project lost a layer after relocation")
        if {sha(path) for path in reopened.attachedFiles() if Path(path).suffix.lower() == ".gpkg"} != set(inputs.values()):
            raise ValueError("Standalone database attachment hash mismatch")
        for binding in receipt["svg_bindings"]:
            target = next(item for item in found if item.name() == binding["layer"])
            svg_categories = [category for category in target.renderer().categories()
                              if category.value() == "cad2gis_svg_" + binding["symbol_id"]]
            if len(svg_categories) != 1 or svg_categories[0].symbol().symbolLayer(0).layerType() != "SvgMarker":
                raise ValueError("Standalone project lost SVG binding")
            if not svg_categories[0].symbol().symbolLayer(0).path().startswith("base64:"):
                raise ValueError("SVG still depends on an external file")
            context = QgsRenderContext()
            context.expressionContext().appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(target))
            renderer = target.renderer()
            renderer.startRender(context, target.fields())
            try:
                feature = next(item for item in target.getFeatures() if item["source_handle"] == binding["source_handle"])
                context.expressionContext().setFeature(feature)
                selected_symbol = renderer.symbolForFeature(feature, context)
                if selected_symbol is None or selected_symbol.symbolLayer(0).layerType() != "SvgMarker":
                    raise ValueError("Actual feature renderer does not select the embedded SVG")
            finally:
                renderer.stopRender(context)
        reopened.clear()
    if any(sha(path) != digest for path, digest in inputs.items()):
        raise ValueError("Original database was modified")
    receipt.update(output=str(output), sha256=sha(output), layers=expected, same_process_integrity_verified=True,
                   fresh_process_verified=False,
                   database_bytes_unchanged=True)
    output.with_suffix(".verification.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def main():
    from qgis.core import QgsApplication
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--symbol-id")
    parser.add_argument("--layer", dest="layer_name")
    parser.add_argument("--source-handle")
    parser.add_argument("--delivery-manifest", type=Path)
    parser.add_argument("--bindings", type=Path, help="Explicit layer/source_handle/symbol_id bindings JSON")
    args = parser.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    result = package(args.project, args.output, store=args.store, symbol_id=args.symbol_id,
                     layer_name=args.layer_name, source_handle=args.source_handle, delivery_manifest=args.delivery_manifest,
                     bindings=json.loads(args.bindings.read_text(encoding="utf-8")) if args.bindings else None)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
