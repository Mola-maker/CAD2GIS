"""Source-instance SVG inventory; geometry and semantic stores are read-only.

Candidate correspondence is evidence for review, never implicit legend approval.
Every delivered point receives a disposition, including unsupported primitives.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import csv
from contextlib import closing
import subprocess
import tempfile
from pathlib import Path

OVERRIDE_SCHEMA = 'cad2gis.svg-symbol-overrides.v1'
# Entity types whose own geometry is a meaningful symbol outline. POINT carries no
# outline and TEXT glyphs are annotation, so neither may be bound as self geometry.
SELF_GEOMETRY_TYPES = frozenset({'LWPOLYLINE', 'POLYLINE', 'LINE', 'CIRCLE', 'ARC', 'ELLIPSE', 'SPLINE'})
GROUP_GEOMETRY_TYPES = SELF_GEOMETRY_TYPES | {'INSERT', 'HATCH', 'SOLID', 'TRACE', '3DFACE'}
GROUP_ANNOTATION_TYPES = frozenset({'TEXT', 'MTEXT'})


def _source_groups(document):
    """Index explicit CAD GROUP membership, never infer membership by proximity."""
    groups = {}
    for name, group in document.groups:
        members = list(group)
        record = dict(name=name, handle=group.dxf.handle,
                      member_handles=[member.dxf.handle for member in members])
        for member in members:
            groups.setdefault(member.dxf.handle, []).append(record)
    return groups


def _group_source(document, entity, groups):
    """Return conservative render evidence for one non-block grouped feature."""
    candidates = groups.get(entity.dxf.handle, [])
    if not candidates:
        return None
    evidence = dict(groups=candidates, basis='explicit_dxf_group_membership')
    if len(candidates) != 1:
        return dict(status='UNRESOLVED_GROUP_AMBIGUOUS', **evidence)
    handles = candidates[0]['member_handles']
    members = [document.entitydb.get(handle) for handle in handles]
    if len(members) > 64:
        return dict(status='UNRESOLVED_GROUP_UNSUPPORTED', reason='group_exceeds_64_members', **evidence)
    if any(member is None or not member.is_alive or member.dxf.owner != entity.dxf.owner
           for member in members):
        return dict(status='UNRESOLVED_GROUP_UNSUPPORTED', reason='unresolved_or_different_layout', **evidence)
    allowed = GROUP_GEOMETRY_TYPES | GROUP_ANNOTATION_TYPES | {'POINT'}
    if any(member.dxftype() not in allowed for member in members):
        return dict(status='UNRESOLVED_GROUP_UNSUPPORTED', reason='unsupported_group_entity', **evidence)
    if not any(member.dxftype() in GROUP_GEOMETRY_TYPES for member in members):
        return dict(status='UNRESOLVED_GROUP_UNSUPPORTED', reason='group_has_no_symbol_geometry', **evidence)
    # POINT is semantic identity, not a pole outline. Its header-controlled marker
    # is deliberately excluded; group text, frames and block geometry stay intact.
    return dict(status='SOURCE_GROUP_CANDIDATE',
                render_handles=[member.dxf.handle for member in members if member.dxftype() != 'POINT'],
                **evidence)


def _coverage_report(points):
    """Count delivered feature instances, including repeated database scopes."""
    def count(rows):
        rendered = [point for point in rows if point['symbol_id']
                    and not point['status'].startswith('UNRESOLVED_')]
        return dict(delivered=len(rows), source_present=sum(point['source_type'] != 'missing' for point in rows),
                    rendered=len(rendered), unresolved=len(rows) - len(rendered),
                    grouped=sum(point.get('geometry_basis') == 'explicit_dxf_group' for point in rendered),
                    curated_alternate_geometry=sum(point.get('geometry_basis') == 'curated_handle_mapping'
                                                   for point in rendered),
                    presentation_overlays=sum(bool(point.get('presentation_overlay')) for point in rendered))
    return dict(schema='cad2gis.svg-coverage.v1', grain='delivered_point_per_database_scope_and_layer',
                counts_are_accuracy_claims=False, total=count(points),
                by_layer={layer: count([point for point in points if point['layer'] == layer])
                          for layer in sorted({point['layer'] for point in points})},
                by_database_scope={scope: count([point for point in points if point['database'] == scope])
                                   for scope in sorted({point['database'] for point in points})})


def _write_review_queue(output, points):
    """Export source locators for unresolved geometry and curated presentation."""
    fields = ['database_scope', 'feature_class', 'source_handle', 'source_type', 'source_layer',
              'source_layout', 'status', 'review_reason', 'source_x', 'source_y', 'source_z',
              'coordinate_basis', 'render_handles', 'symbol_id']
    records = []
    for point in points:
        reason = ('unresolved' if point['status'].startswith('UNRESOLVED_') else
                  'curated_alternate_geometry' if point.get('geometry_basis') == 'curated_handle_mapping' else
                  'curated_text_overlay' if point.get('presentation_overlay') else None)
        if reason is None:
            continue
        anchor = point.get('source_anchor') or {}
        coordinates = anchor.get('coordinates', (None, None, None))
        records.append(dict(database_scope=point['database'], feature_class=point['layer'],
                            **{key: point.get(key) for key in ('source_handle', 'source_type', 'source_layer',
                                                             'source_layout', 'status', 'symbol_id')},
                            review_reason=reason, source_x=coordinates[0], source_y=coordinates[1],
                            source_z=coordinates[2], coordinate_basis=anchor.get('basis'),
                            render_handles=';'.join(point['render_handles'])))
    with (output / 'svg-review-queue.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    return dict(path='svg-review-queue.csv', count=len(records),
                coordinates='original CAD coordinates when available; not registered GIS or surveyed coordinates')


def load_overrides(path) -> dict | None:
    """Read a project's curated SVG symbol overrides, if the file exists.

    The inventory binds source geometry and explicit CAD groups. A drawing may draw a
    delivered feature as a closed polyline and its visible pole symbol as a block that
    is inserted at the origin, so a project can declare what to render per feature.
    Every rule is explicit and recorded in the correspondence report.
    """
    if path is None:
        return None
    path = Path(path)
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding='utf-8'))
    if document.get('schema_version') != OVERRIDE_SCHEMA:
        raise ValueError(f'Unsupported SVG symbol override schema: {document.get("schema_version")!r}')
    rules = []
    for entry in document.get('instance_sources', ()):  # 每个规则控制一类（或一个）已交付要素
        feature_class = str(entry.get('feature_class', '')).strip()
        mode = str(entry.get('mode', 'handles'))
        if not feature_class or mode not in {'self', 'handles'}:
            raise ValueError('SVG symbol overrides require feature_class and mode self|handles')
        render_handles = [str(handle).upper() for handle in entry.get('render_handles', ())]
        if mode == 'handles' and not render_handles:
            raise ValueError(f'SVG symbol override for {feature_class} needs render_handles')
        overlay = entry.get('overlay_text')
        if overlay is not None:
            if not isinstance(overlay, dict) or not str(overlay.get('text', '')).strip():
                raise ValueError(f'SVG symbol override for {feature_class} has an invalid overlay_text block')
            rationalised = {
                'text': str(overlay['text']).strip(),
                'fill': str(overlay.get('fill', '#ff0000')),
                'outline': str(overlay.get('outline', '#ffffff')),
                'font_family': str(overlay.get('font_family', 'Arial, Helvetica, Liberation Sans, sans-serif')),
                'font_weight': str(overlay.get('font_weight', 'bold')),
                'height_ratio': float(overlay.get('height_ratio', 0.52)),
            }
            if not 0 < rationalised['height_ratio'] <= 1:
                raise ValueError('overlay_text height_ratio must be within (0, 1]')
            overlay = rationalised
        rules.append(dict(
            feature_class=feature_class, mode=mode,
            source_handle=str(entry['source_handle']).upper() if entry.get('source_handle') else None,
            render_handles=render_handles, label=entry.get('label'), overlay_text=overlay,
            rationale=str(entry.get('rationale', '')),
        ))
    legend = []
    for entry in document.get('extra_legend', ()):
        symbol_id = str(entry.get('symbol_id', '')).strip().lower()
        render_handles = [str(handle).upper() for handle in entry.get('render_handles', ())]
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', symbol_id) or not render_handles:
            raise ValueError('Legend overrides need a filename-safe symbol_id and render_handles')
        legend.append(dict(symbol_id=symbol_id, render_handles=render_handles,
                           label=entry.get('label') or symbol_id))
    identities = [(rule['feature_class'], rule['source_handle']) for rule in rules]
    if len(identities) != len(set(identities)):
        raise ValueError('Ambiguous SVG symbol overrides for the same class and source handle')
    return {'rules': rules, 'legend': legend, 'path': str(path)}


def _override_rule(overrides: dict | None, feature_class: str, handle: str) -> dict | None:
    """Return the rule for one delivered handle; an exact handle beats a class rule."""
    if not overrides:
        return None
    upper = str(handle).upper()
    fallback = None
    for rule in overrides['rules']:
        if rule['feature_class'] != feature_class:
            continue
        if rule['source_handle'] is not None:
            if rule['source_handle'] == upper:
                return rule
            continue
        if fallback is None:
            fallback = rule
    return fallback


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


def prepare(source: Path, databases: list[Path], output: Path, *, font_dirs=(),
            database_paths=None, expected_source_sha256=None, overrides=None) -> dict:
    """Publish an independent inventory atomically, never a partial candidate."""
    output = Path(output).resolve()
    preflight(output, font_dirs=font_dirs)
    databases = [Path(path).resolve() for path in databases]
    published_paths = ({Path(path).resolve(): Path(published).resolve()
                        for path, published in database_paths.items()}
                       if database_paths is not None else {path: path for path in databases})
    if set(published_paths) != set(databases) or len(set(databases)) != len(databases):
        raise ValueError('SVG published database paths must cover each input exactly once')
    source_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    if expected_source_sha256 is not None and source_hash != expected_source_sha256:
        raise ValueError('SVG source does not match conversion source SHA-256')
    for database in databases if expected_source_sha256 is None else ():
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
        report = _prepare(source, databases, staged, font_dirs=font_dirs,
                          database_paths=published_paths, overrides=overrides)
        from .cad2gis_v3.artifact_io import inherit_output_permissions
        inherit_output_permissions(Path(temporary))
        staged.rename(output)
    return report


def _prepare(source: Path, databases: list[Path], output: Path, *, font_dirs=(), database_paths=None,
             overrides=None) -> dict:
    from .runtime import configure_numeric_threads
    configure_numeric_threads()
    import ezdxf
    from ezdxf.fonts import fonts
    if font_dirs:
        fonts.font_manager.build([str(Path(p).resolve()) for p in font_dirs])
    from .symbol_assets import SCHEMA, extract, _render, _create_symbol_schema
    from .cad2gis_v3.legend_detector import detect_legend_clusters

    overrides = load_overrides(overrides)

    source, output = Path(source).resolve(), Path(output).resolve()
    if not databases:
        raise ValueError('SVG correspondence requires at least one delivery database')
    if output.exists():
        raise FileExistsError(output)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in databases}
    published_paths = database_paths or {path: path for path in databases}
    published_hashes = {str(published_paths[path]): before[str(path)] for path in databases}
    scope_names = {path: published_paths[path].parent.name for path in databases}
    if len(set(scope_names.values())) != len(databases):
        scope_names = {path: f'{index}/{published_paths[path].parent.name}'
                       for index, path in enumerate(databases)}
    points = []
    for database in databases:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for table, in db.execute("SELECT table_name FROM gpkg_geometry_columns WHERE geometry_type_name IN ('POINT','MULTIPOINT')"):
                quoted = '"' + table.replace('"', '""') + '"'
                fields = {r[1] for r in db.execute(f'PRAGMA table_info({quoted})')}
                if 'source_handle' not in fields:
                    raise ValueError(f'Point layer {table} lacks source lineage')
                for handle, in db.execute(f'SELECT source_handle FROM {quoted}'):
                    points.append(dict(database=scope_names[database], layer=table, source_handle=str(handle)))
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
        groups = _source_groups(document)
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
                   clone.dxf.zscale, tuple(clone.dxf.extrusion),
                   clone.dxf.row_count, clone.dxf.column_count,
                   clone.dxf.row_spacing, clone.dxf.column_spacing,
                   entity.get_layout().name if entity.get_layout() is not None else None,
                   tuple((a.dxf.tag, a.dxf.text) for a in entity.attribs))
            if key not in fingerprint_cache:
                # Attached attributes have world coordinates; avoid pretending these normalize.
                if entity.attribs:
                    fingerprint_cache[key] = None
                else:
                    try:
                        rendered, skipped = _render(document, [clone], source_layout=entity.get_layout())
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
            point.update(source_layer=entity.dxf.layer if entity is not None else None,
                         source_layout=(entity.get_layout().name if entity is not None and entity.get_layout()
                                        else None), render_handles=[])
            if entity is not None and entity.dxftype() == 'POINT':
                point['source_anchor'] = dict(coordinates=list(entity.dxf.location), basis='source_point_wcs')
            rule = _override_rule(overrides, point['layer'], handle)
            if rule is not None:
                # Curated rule wins over the block-instance default.
                if rule['mode'] == 'self':
                    render_handles = [handle] if entity is not None and entity.dxftype() in SELF_GEOMETRY_TYPES else []
                else:
                    render_handles = list(rule['render_handles'])
                if render_handles:
                    symbol_id = 'instance-' + handle.lower()
                    item = dict(symbol_id=symbol_id,
                                label=rule['label'] or f"{point['layer']} / {handle}",
                                handles=[handle], render_handles=render_handles)
                    if rule.get('overlay_text'):
                        item['overlay_text'] = rule['overlay_text']
                    selected[symbol_id] = item
                    point.update(symbol_id=symbol_id,
                                 source_type=entity.dxftype() if entity is not None else 'missing',
                                 legend_handles=[], status='CURATED_OVERRIDE',
                                 render_handles=render_handles,
                                 geometry_basis=('source_entity' if render_handles == [handle]
                                                 else 'curated_handle_mapping'),
                                 presentation_overlay=bool(rule.get('overlay_text')),
                                 source_instance_fidelity_verified=False,
                                 override=dict(mode=rule['mode'], render_handles=render_handles,
                                               rationale=rule['rationale']))
                    continue
            if entity is not None and entity.dxftype() != 'INSERT':
                grouped = _group_source(document, entity, groups)
                if grouped is not None:
                    point.update(group_evidence=grouped)
                    if grouped['status'].startswith('UNRESOLVED_'):
                        point.update(status=grouped['status'], source_type=entity.dxftype(),
                                     legend_handles=[], symbol_id=None)
                        continue
                if grouped is not None or entity.dxftype() in SELF_GEOMETRY_TYPES:
                    symbol_id = 'instance-' + handle.lower()
                    render_handles = grouped['render_handles'] if grouped else [handle]
                    selected[symbol_id] = dict(symbol_id=symbol_id, label=f"{point['layer']} / {handle}",
                                               handles=[handle], render_handles=render_handles)
                    point.update(symbol_id=symbol_id, source_type=entity.dxftype(), legend_handles=[],
                                 render_handles=render_handles,
                                 geometry_basis='explicit_dxf_group' if grouped else 'source_entity',
                                 status='SOURCE_GROUP_CANDIDATE' if grouped else 'SOURCE_GEOMETRY_ONLY',
                                 source_instance_fidelity_verified=False)
                    continue
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
                         render_handles=[handle], geometry_basis='source_entity',
                         source_instance_fidelity_verified=False,
                         legend_handles=matches,
                         status='LEGEND_MATCH_CANDIDATE' if matches else 'SOURCE_INSTANCE_ONLY')
        # Include detected margin samples as separate assets, so correspondence can be compared visually.
        for handle in sorted({h for p in points for h in p['legend_handles']}):
            key = 'legend-' + handle.lower()
            selected[key] = dict(symbol_id=key, label='Legend candidate / ' + handle, handles=[handle])
        # Curated legend samples named by the project (for example a coloured pole type
        # whose model instances are not block inserts).
        for item in (overrides or {}).get('legend', ()):
            selected[item['symbol_id']] = dict(symbol_id=item['symbol_id'], label=item['label'],
                                               handles=list(item['render_handles']),
                                               render_handles=list(item['render_handles']))
        selection = Path(temp) / 'selection.json'
        selection.write_text(json.dumps(dict(source_sha256=digest, symbols=list(selected.values()))), encoding='utf-8')
        if selected:
            # Automatic inventory is bounded by the observed source handles,
            # rather than the smaller manual-selection interface's limit.
            extract(source, selection, output, font_dirs=font_dirs,
                    max_symbols=max(5000, len(selected)))
        else:
            output.mkdir(parents=True)
        with closing(sqlite3.connect(output / 'symbols.sqlite3')) as db, db:
            _create_symbol_schema(db)
            if selected:
                statuses = dict(db.execute('SELECT symbol_id,status FROM symbols'))
                for point in points:
                    if point['symbol_id'] and statuses[point['symbol_id']] != 'candidate':
                        point['status'] = 'UNRESOLVED_RENDER_INCOMPLETE'
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('schema_version', json.dumps(SCHEMA)))
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
        coverage = _coverage_report(points)
        review_queue = _write_review_queue(output, points)
        report = dict(schema='cad2gis.svg-correspondence.v1', source_sha256=digest, mode='candidate',
                      all_points_accounted_for=len(points), dispositions=counts,
                      coverage=coverage, review_queue=review_queue,
                      complete_legend_correspondence_verified=False, auto_applied=False,
                      source_instance_bindings=bindings, points=points, legend_clusters=clusters,
                      inventory_blocks=len(inserts), input_databases_sha256=published_hashes,
                      overrides=(None if overrides is None else dict(
                          path=overrides['path'], rule_count=len(overrides['rules']),
                          legend_count=len(overrides['legend']),
                          overlay_text_rules=[rule['feature_class'] + (f" / {rule['source_handle']}" if rule['source_handle'] else '')
                                              for rule in overrides['rules'] if rule.get('overlay_text')])),
                      database_scopes={str(published_paths[path]): scope_names[path] for path in databases})
        (output / 'correspondence.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        summary_rows = ''.join('<tr><td>' + html.escape(layer) + '</td>' + ''.join(
            f'<td>{values[key]}</td>' for key in ('delivered', 'source_present', 'rendered',
                                                'curated_alternate_geometry', 'unresolved')) + '</tr>'
            for layer, values in coverage['by_layer'].items())
        rows = ''.join('<tr><td>' + html.escape(p['layer'] + ' / ' + p['source_handle']) + '</td><td>' +
                       (f'<a href="symbols/{p["symbol_id"]}.svg"><img src="symbols/{p["symbol_id"]}.png" alt="Source instance"></a>' if p['symbol_id'] else '保留原样式') + '</td><td>' +
                       ''.join(f'<a href="symbols/legend-{h.lower()}.svg"><img src="symbols/legend-{h.lower()}.png" alt="Legend candidate"></a>{h}' for h in p['legend_handles']) +
                       '</td><td>' + p['status'] + '</td></tr>' for p in points)
        (output / 'correspondence.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width"><title>SVG 对应复核</title>'
            '<style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:1rem}img{width:110px;height:110px;object-fit:contain}</style>'
            '<h1>源实体与图例候选对应复核</h1><p>每个交付点均列出处理状态。候选尚未获得完整图例验收；未确认项保留原样式。'
            '归一化形状相同是对应证据，不替代动态状态和原图字体核验。</p><p><a href="symbols.sqlite3">独立 SQLite</a> · '
            '<a href="correspondence.json">全部源句柄、检测范围与覆盖率</a> · '
            '<a href="svg-review-queue.csv">下载缺失与人工替代复核清单</a></p>'
            '<p>数量按交付数据库内的要素实例统计，分区重复实例分别计数。人工替代表示显式指定了不同的渲染句柄，'
            '不代表原图符号已恢复。CSV 坐标如有提供，是源 CAD POINT 坐标，不是 GCP 校验后的 GIS 坐标。</p>'
            '<table><tr><th>要素类别</th><th>交付数</th><th>源存在</th><th>已渲染候选</th><th>人工替代几何</th><th>未解决</th></tr>'
            + summary_rows + '</table>'
            '<table><tr><th>地图实体</th><th>源实体 SVG</th><th>边缘图例候选</th><th>状态</th></tr>' + rows + '</table></html>', encoding='utf-8')
    if before != {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in databases}:
        raise ValueError('SVG workflow changed the delivery databases')
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError('CAD source changed during correspondence inventory')
    return report
