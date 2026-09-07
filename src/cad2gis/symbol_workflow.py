"""Optional source-instance SVG inventory; geometry and semantic stores are read-only.

Candidate correspondence is evidence for review, never implicit legend approval.
Every delivered point receives a disposition, including unsupported primitives.
"""
from __future__ import annotations

import hashlib
import html
import json
import sqlite3
from contextlib import closing
import subprocess
import tempfile
from pathlib import Path


def preflight(output: Path, *, font_dirs=()) -> None:
    """Reject predictable optional-stage failures before conversion publication."""
    import importlib.util
    if importlib.util.find_spec('ezdxf') is None:
        raise RuntimeError('SVG candidates require pip install cad2gis[symbols]')
    if Path(output).exists():
        raise FileExistsError(output)
    for directory in font_dirs:
        if not Path(directory).is_dir():
            raise ValueError(f'SVG font directory does not exist: {directory}')


def prepare(source: Path, databases: list[Path], output: Path, *, font_dirs=()) -> dict:
    """Publish an independent inventory atomically, never a partial candidate."""
    output = Path(output).resolve()
    preflight(output, font_dirs=font_dirs)
    if len({Path(p).parent.name for p in databases}) != len(databases):
        raise ValueError('Delivery database scopes must be unique')
    source_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    for database in databases:
        for parent in list(Path(database).resolve().parents)[:3]:
            manifest = parent / 'run_manifest.json'
            if manifest.is_file():
                value = json.loads(manifest.read_text(encoding='utf-8'))
                if value.get('source', {}).get('sha256') != source_hash:
                    raise ValueError('SVG source does not match delivery run manifest')
                break
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.svg-stage-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'inventory'
        report = _prepare(source, databases, staged, font_dirs=font_dirs)
        from .cad2gis_v3.artifact_io import inherit_output_permissions
        inherit_output_permissions(Path(temporary))
        staged.rename(output)
    return report


def _prepare(source: Path, databases: list[Path], output: Path, *, font_dirs=()) -> dict:
    from .runtime import configure_numeric_threads
    configure_numeric_threads()
    import ezdxf
    from ezdxf.fonts import fonts
    if font_dirs:
        fonts.font_manager.build([str(Path(p).resolve()) for p in font_dirs])
    from .symbol_assets import extract, _render
    from .cad2gis_v3.legend_detector import detect_legend_clusters

    source, output = Path(source).resolve(), Path(output).resolve()
    if not databases:
        raise ValueError('SVG correspondence requires at least one delivery database')
    if output.exists():
        raise FileExistsError(output)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in databases}
    points = []
    for database in databases:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for table, in db.execute("SELECT table_name FROM gpkg_geometry_columns WHERE geometry_type_name IN ('POINT','MULTIPOINT')"):
                quoted = '"' + table.replace('"', '""') + '"'
                fields = {r[1] for r in db.execute(f'PRAGMA table_info({quoted})')}
                if 'source_handle' not in fields:
                    raise ValueError(f'Point layer {table} lacks source lineage')
                for handle, in db.execute(f'SELECT source_handle FROM {quoted}'):
                    points.append(dict(database=database.parent.name, layer=table, source_handle=str(handle)))
    with tempfile.TemporaryDirectory(prefix='cad2gis-legend-inventory-') as temp:
        dxf = source
        if source.suffix.lower() == '.dwg':
            from .reader.libredwg_cli import discover_libredwg_cli
            executable, _ = discover_libredwg_cli()
            if executable is None:
                raise RuntimeError('SVG candidates require LibreDWG and the symbols optional dependency')
            dxf = Path(temp) / 'source.dxf'
            process = subprocess.run([str(executable), '-y', '-o', str(dxf), str(source)],
                                     capture_output=True, timeout=600)
            if process.returncode or not dxf.is_file():
                raise RuntimeError('LibreDWG failed during optional SVG inventory')
        document = ezdxf.readfile(dxf)
        features, inserts = [], {}
        for layout in document.layouts:
            for entity in layout:
                kind = entity.dxftype()
                if kind not in {'INSERT', 'TEXT', 'MTEXT'}:
                    continue
                position = entity.dxf.insert
                handle = entity.dxf.handle
                text = entity.dxf.get('text', '') if kind == 'TEXT' else entity.plain_text() if kind == 'MTEXT' else ''
                features.append(dict(id=handle, centroid=(position.x, position.y), text=text,
                                     layer=entity.dxf.layer, layout=layout.name))
                if kind == 'INSERT':
                    inserts[handle] = entity
        # Detect independently in each layout; paper coordinates must not mix with model coordinates.
        clusters = []
        for layout_name in sorted({f['layout'] for f in features}):
            found = detect_legend_clusters([f for f in features if f['layout'] == layout_name])
            clusters.extend(dict(c, layout=layout_name) for c in found['clusters'])
        map_handles = {p['source_handle'] for p in points}
        legend_handles = {str(h) for c in clusters for h in c['member_ids'] if str(h) in inserts} - map_handles
        # Complete inventory includes unassigned blocks. They are visible to the reviewer,
        # but a repeated block or a sheet edge alone does not make it a confirmed legend.
        fingerprint_cache = {}
        def fingerprint(entity):
            clone = entity.copy()
            clone.dxf.insert = (0, 0, 0)
            clone.dxf.rotation = 0
            # Preserve non-uniform scale and mirror state. Ignore uniform size only.
            scale = abs(float(clone.dxf.get('xscale', 1))) or 1
            for axis in ('xscale', 'yscale', 'zscale'):
                setattr(clone.dxf, axis, float(clone.dxf.get(axis, 1)) / scale)
            key = (clone.dxf.name, clone.dxf.layer, clone.dxf.get('color', 256),
                   clone.dxf.get('true_color', None), clone.dxf.xscale, clone.dxf.yscale,
                   tuple((a.dxf.tag, a.dxf.text) for a in entity.attribs))
            if key not in fingerprint_cache:
                # Attached attributes have world coordinates; avoid pretending these normalize.
                if entity.attribs:
                    fingerprint_cache[key] = None
                else:
                    try:
                        rendered, skipped = _render(document, [clone])
                        fingerprint_cache[key] = (hashlib.sha256(rendered.encode()).hexdigest()
                                                  if not any(s['reason'] != 'invisible' for s in skipped) else None)
                    except (ValueError, TypeError, KeyError):
                        fingerprint_cache[key] = None
            return fingerprint_cache[key]
        legend_signatures = {}
        for handle in sorted(legend_handles):
            signature = fingerprint(inserts[handle])
            if signature:
                legend_signatures.setdefault(signature, []).append(handle)
        selected = {}
        for point in points:
            handle = point['source_handle']
            entity = document.entitydb.get(handle)
            if entity is None or entity.dxftype() != 'INSERT':
                point.update(status=('UNRESOLVED_NESTED_HANDLE' if '/' in handle else
                                     'UNRESOLVED_MISSING_HANDLE' if entity is None else 'UNRESOLVED_NON_BLOCK'),
                             source_type=entity.dxftype() if entity else 'missing',
                             legend_handles=[], symbol_id=None)
                continue
            symbol_id = 'instance-' + handle.lower()
            selected[symbol_id] = dict(symbol_id=symbol_id, label=f"{point['layer']} / {handle}", handles=[handle])
            signature = fingerprint(entity)
            matches = legend_signatures.get(signature, []) if signature else []
            point.update(symbol_id=symbol_id, source_type='INSERT', normalized_svg_sha256=signature,
                         legend_handles=matches,
                         status='LEGEND_MATCH_CANDIDATE' if matches else 'SOURCE_INSTANCE_ONLY')
        # Include detected margin samples as separate assets, so correspondence can be compared visually.
        for handle in sorted({h for p in points for h in p['legend_handles']}):
            key = 'legend-' + handle.lower()
            selected[key] = dict(symbol_id=key, label='Legend candidate / ' + handle, handles=[handle])
        selection = Path(temp) / 'selection.json'
        selection.write_text(json.dumps(dict(source_sha256=digest, symbols=list(selected.values()))), encoding='utf-8')
        if selected:
            extract(source, selection, output, font_dirs=font_dirs)
        else:
            output.mkdir(parents=True)
        with closing(sqlite3.connect(output / 'symbols.sqlite3')) as db, db:
            if selected:
                statuses = dict(db.execute('SELECT symbol_id,status FROM symbols'))
                for point in points:
                    if point['symbol_id'] and statuses[point['symbol_id']] != 'candidate':
                        point['status'] = 'UNRESOLVED_RENDER_INCOMPLETE'
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('source_sha256', json.dumps(digest)))
            db.execute('CREATE TABLE correspondence (feature_index INTEGER PRIMARY KEY, database_scope TEXT, layer TEXT, source_handle TEXT, symbol_id TEXT, status TEXT, evidence_json TEXT)')
            db.execute('CREATE INDEX correspondence_source ON correspondence(source_handle, layer)')
            for i, point in enumerate(points):
                db.execute('INSERT INTO correspondence VALUES (?,?,?,?,?,?,?)',
                           (i, point['database'], point['layer'], point['source_handle'], point['symbol_id'],
                            point['status'], json.dumps(point)))
            db.execute('CREATE TABLE legend_inventory (source_handle TEXT PRIMARY KEY, block_name TEXT, layout TEXT, is_cluster_candidate INTEGER, is_map_instance INTEGER)')
            locations = {f['id']: f['layout'] for f in features}
            db.executemany('INSERT INTO legend_inventory VALUES (?,?,?,?,?)',
                           [(h, e.dxf.name, locations[h], int(h in legend_handles), int(h in map_handles)) for h, e in inserts.items()])
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('workflow', json.dumps('candidate-review-required')))
        bindings = [dict(layer=p['layer'], source_handle=p['source_handle'], symbol_id=p['symbol_id'],
                         database_scope=p['database']) for p in points
                    if p['symbol_id'] and p['status'] != 'UNRESOLVED_RENDER_INCOMPLETE']
        counts = {s: sum(p['status'] == s for p in points) for s in sorted({p['status'] for p in points})}
        report = dict(schema='cad2gis.svg-correspondence.v1', source_sha256=digest, mode='candidate',
                      all_points_accounted_for=len(points), dispositions=counts,
                      complete_legend_correspondence_verified=False, auto_applied=False,
                      source_instance_bindings=bindings, points=points, legend_clusters=clusters,
                      inventory_blocks=len(inserts), input_databases_sha256=before)
        (output / 'correspondence.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        rows = ''.join('<tr><td>' + html.escape(p['layer'] + ' / ' + p['source_handle']) + '</td><td>' +
                       (f'<img src="symbols/{p["symbol_id"]}.svg">' if p['symbol_id'] else '保留原样式') + '</td><td>' +
                       ''.join(f'<img src="symbols/legend-{h.lower()}.svg">{h}' for h in p['legend_handles']) +
                       '</td><td>' + p['status'] + '</td></tr>' for p in points)
        (output / 'correspondence.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width"><title>SVG 对应复核</title>'
            '<style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:1rem}img{width:110px;height:110px}</style>'
            '<h1>源实体与图例候选对应复核</h1><p>每个交付点均列出处理状态。候选尚未获得完整图例验收；未确认项保留原样式。'
            '归一化形状相同是对应证据，不替代动态状态和原图字体核验。</p><p><a href="symbols.sqlite3">独立 SQLite</a> · '
            '<a href="correspondence.json">全部源句柄、检测范围与覆盖率</a></p>'
            '<table><tr><th>地图实体</th><th>源实体 SVG</th><th>边缘图例候选</th><th>状态</th></tr>' + rows + '</table></html>', encoding='utf-8')
    if before != {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in databases}:
        raise ValueError('SVG workflow changed the delivery databases')
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError('CAD source changed during correspondence inventory')
    return report
