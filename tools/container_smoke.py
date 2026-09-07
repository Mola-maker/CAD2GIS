"""Exercise installed CAD decoding, GeoPackage and SVG without project data."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def main():
    import ezdxf
    from cad2gis.symbol_assets import extract, export_qml
    with tempfile.TemporaryDirectory(prefix='cad2gis-container-') as temporary:
        root = Path(temporary)
        document = ezdxf.new('R2000')
        document.header['$INSUNITS'] = 6
        model = document.modelspace()
        model.add_line((100.123456789, 200), (103.123456789, 204))
        circle = model.add_circle((100, 200), 2)
        label = model.add_text('NP7 4"', dxfattribs={'height': 1, 'insert': (100, 200)})
        source = root / 'synthetic.dxf'
        document.saveas(source)
        from cad2gis.reader.libredwg_cli import discover_libredwg_cli
        decoder, _ = discover_libredwg_cli()
        if decoder is None:
            raise RuntimeError('LibreDWG runtime missing')
        # dwgadd generates a valid synthetic source without the dxf2dwg encoder's
        # null-handle round-trip failure. Grammar: LibreDWG examples/dwgadd.example.
        writer = decoder.with_name('dwgadd' + decoder.suffix)
        instructions = root / 'source.add'
        instructions.write_text('line (100.123456789 200 0) (103.123456789 204 0)\n'
                                'circle (100 200 0) 2\ntext "NP7" (100 200 0) 1\n')
        dwg = root / 'synthetic.dwg'
        conversion = subprocess.run([str(writer), '-o', str(dwg), str(instructions)], capture_output=True)
        if conversion.returncode or not dwg.is_file():
            raise RuntimeError('Synthetic DWG creation failed: ' + conversion.stderr.decode(errors='replace'))
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        for arguments in [
            ['export-source', str(dwg), '--run-dir', str(root / 'run'), '--source-crs', 'EPSG:3857', '--json'],
            ['index-source', str(root / 'run'), '--json'],
        ]:
            result = subprocess.run([sys.executable, '-m', 'cad2gis', *arguments], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr + result.stdout)
        gpkg = next((root / 'run').rglob('*.gpkg'))
        with closing(sqlite3.connect(gpkg.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Invalid GeoPackage')
            if not db.execute('SELECT table_name FROM gpkg_geometry_columns').fetchall():
                raise RuntimeError('No exported source geometry')
        selection = root / 'selection.json'
        selection.write_text(json.dumps({'source_sha256': source_sha, 'symbols': [
            {'symbol_id': 'circle', 'label': 'Synthetic source circle and label',
             'handles': [circle.dxf.handle, label.dxf.handle]}]}))
        extract(source, selection, root / 'symbols')
        export_qml(root / 'symbols/symbols.sqlite3', 'circle', root / 'circle.qml')
        if source_sha != hashlib.sha256(source.read_bytes()).hexdigest():
            raise RuntimeError('Source mutated')
        print(json.dumps({'status': 'passed', 'synthetic_dwg_export': True, 'source_index': True,
                          'sqlite_integrity': True, 'svg_qml': True, 'source_unchanged': True,
                          'qgis_render_verified': False}))


if __name__ == '__main__':
    main()
