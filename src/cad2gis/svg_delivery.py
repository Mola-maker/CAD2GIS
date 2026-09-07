"""Add optional source-instance SVG review views to a preserved delivery baseline.

Run with QGIS Python, then run verify_qgis_standalone in separate processes.
"""
import argparse
import copy
import csv
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from cad2gis.qgis_package import package
from cad2gis.presentation import csv_value


def build(baseline, assets, output):
    if not __debug__:
        raise RuntimeError('SVG delivery checks require assertions enabled; do not use python -O')
    baseline, assets, output = map(lambda p: Path(p).resolve(), (baseline, assets, output))
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((baseline / 'delivery-manifest.json').read_text(encoding='utf-8'))
    correspondence = json.loads((assets / 'correspondence.json').read_text(encoding='utf-8'))
    if manifest['source_sha256'] != correspondence['source_sha256']:
        raise ValueError('Source-bound correspondence belongs to another drawing')
    with closing(sqlite3.connect((assets / 'symbols.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        statuses = dict(db.execute('SELECT symbol_id,status FROM symbols'))
    shutil.copytree(baseline, output)
    # Old checksums are superseded by a new complete seal after independent verification.
    report = dict(source_sha256=manifest['source_sha256'], mode='source-instance-svg-review',
                  complete_legend_correspondence_verified=False, projects=[])
    for entry in manifest['deliveries']:
        relative = Path(entry['project'])
        folder = output / relative.parent
        original = baseline / relative
        scope = 'delivery' if relative.parent == Path('.') else relative.parent.name
        selected = [dict(layer=b['layer'], source_handle=b['source_handle'], symbol_id=b['symbol_id'])
                    for b in correspondence['source_instance_bindings']
                    if b['database_scope'] == scope and statuses[b['symbol_id']] == 'candidate']
        standalone = folder / 'view-with-source-SVG.qgz'
        options = dict(store=assets / 'symbols.sqlite3', bindings=selected,
                       delivery_manifest=baseline / 'delivery-manifest.json') if selected else {}
        receipt = package(original, standalone, **options)
        with zipfile.ZipFile(standalone) as z:
            root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.qgs'))))
        for node in root.findall('.//projectlayers/maplayer/datasource'):
            node.text = './delivery.gpkg|' + node.text.split('|', 1)[1]
        for node in root.findall('.//layer-tree-layer'):
            node.set('source', './delivery.gpkg|' + node.get('source').split('|', 1)[1])
        # Complete editable project uses its adjacent authoritative delivery copy.
        with zipfile.ZipFile(folder / 'delivery.qgz', 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('delivery.qgs', ET.tostring(root, encoding='utf-8', xml_declaration=True))
        for node in root.findall('.//projectlayers/maplayer'):
            style = ET.Element('qgis', version=root.get('version', '3.44'), styleCategories='AllStyleCategories')
            for child in node:
                if child.tag not in {'datasource', 'id', 'layername', 'provider'}:
                    style.append(copy.deepcopy(child))
            (folder / 'styles' / (node.findtext('layername') + '.qml')).write_bytes(ET.tostring(style, encoding='utf-8', xml_declaration=True))
        assert hashlib.sha256((folder / 'delivery.gpkg').read_bytes()).hexdigest() == entry['gpkg_sha256']
        with closing(sqlite3.connect((folder / 'delivery.gpkg').as_uri() + '?mode=ro', uri=True)) as db:
            assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            lengths = dict(db.execute('SELECT length_source,COUNT(*) FROM CABLE GROUP BY length_source'))
            for table, geometry in db.execute('SELECT table_name,column_name FROM gpkg_geometry_columns'):
                quoted = '"' + table.replace('"', '""') + '"'
                columns = [row[1] for row in db.execute(f'PRAGMA table_info({quoted})') if row[1] != geometry]
                projection = ','.join('"' + c.replace('"', '""') + '"' for c in columns)
                with (folder / (table + '.csv')).open('w', newline='', encoding='utf-8-sig') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(columns)
                    writer.writerows([csv_value(v) for v in row] for row in db.execute(f'SELECT {projection} FROM {quoted}'))
        report['projects'].append(dict(project=relative.as_posix(), standalone=standalone.relative_to(output).as_posix(),
                                       layers=receipt['layers'], svg_features=len(selected),
                                       gpkg_sha256=entry['gpkg_sha256'], database_bytes_unchanged=True,
                                       length_sources=lengths))
    (output / 'svg-delivery.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (output / 'checksums.json').write_text(json.dumps({
        p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.rglob('*')) if p.is_file() and p != output / 'checksums.json'
    }, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
    return report


def main():
    from qgis.core import QgsApplication
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    build(args.baseline, args.assets, args.output)


if __name__ == '__main__':
    main()
