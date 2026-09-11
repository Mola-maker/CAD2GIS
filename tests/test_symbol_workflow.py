import sqlite3
import json

import pytest

from cad2gis.symbol_workflow import prepare


def test_candidates_account_for_unresolved_points_without_changing_delivery(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    doc = ezdxf.new()
    block = doc.blocks.new('POLE')
    block.add_circle((0, 0), 1, dxfattribs={'color': 0})
    msp = doc.modelspace()
    first = msp.add_blockref('POLE', (0, 0))
    second = msp.add_blockref('POLE', (10, 0), dxfattribs={'color': 3})
    primitive = msp.add_point((20, 0))
    source = tmp_path / 'source.dxf'
    doc.saveas(source)
    gpkg = tmp_path / 'delivery.gpkg'
    with sqlite3.connect(gpkg) as db:
        db.execute('CREATE TABLE gpkg_geometry_columns(table_name,geometry_type_name)')
        db.execute("INSERT INTO gpkg_geometry_columns VALUES('PTECH','POINT')")
        db.execute('CREATE TABLE PTECH(source_handle)')
        db.executemany('INSERT INTO PTECH VALUES(?)', [(e.dxf.handle,) for e in [first, second, primitive]])
    original = (source.read_bytes(), gpkg.read_bytes())
    report = prepare(source, [gpkg], tmp_path / 'assets')
    assert report['all_points_accounted_for'] == 3
    assert report['dispositions'] == {'SOURCE_INSTANCE_ONLY': 2, 'UNRESOLVED_NON_BLOCK': 1}
    assert not report['complete_legend_correspondence_verified']
    assert not report['auto_applied']
    assert len(report['source_instance_bindings']) == 2
    assert original == (source.read_bytes(), gpkg.read_bytes())
    with sqlite3.connect(tmp_path / 'assets/symbols.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM correspondence').fetchone()[0] == 3
        points = report['points']
        assert points[0]['normalized_svg_sha256'] != points[1]['normalized_svg_sha256']
    with pytest.raises(FileExistsError):
        prepare(source, [gpkg], tmp_path / 'assets')


def test_curated_overrides_bind_polyline_and_composed_symbols(tmp_path):
    """A project may declare what to render for features that are not block inserts."""
    ezdxf = pytest.importorskip('ezdxf')
    document = ezdxf.new()
    msp = document.modelspace()
    frame = msp.add_lwpolyline([(0, 0), (4, 0), (4, 3), (0, 3)], close=True,
                               dxfattribs={'layer': 'FAT', 'color': 40})
    anchor = msp.add_point((10, 0))
    pole_block = document.blocks.new('POLE7')
    pole_block.add_circle((0, 0), 1, dxfattribs={'color': 3})
    pole = msp.add_blockref('POLE7', (12, 0))
    source = tmp_path / 'source.dxf'
    document.saveas(source)
    database = tmp_path / 'delivery.gpkg'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE gpkg_geometry_columns(table_name,geometry_type_name)')
        db.execute("INSERT INTO gpkg_geometry_columns VALUES('BOITE','POINT')")
        db.execute('CREATE TABLE BOITE(source_handle)')
        db.executemany('INSERT INTO BOITE VALUES(?)', [(frame.dxf.handle,), (anchor.dxf.handle,)])
    overrides = tmp_path / 'svg_symbols.json'
    overrides.write_text(json.dumps({
        'schema_version': 'cad2gis.svg-symbol-overrides.v1',
        'instance_sources': [
            {'feature_class': 'BOITE', 'mode': 'self',
             'overlay_text': {'text': 'FAT', 'fill': '#ff0000', 'outline': '#ffffff'}},
            {'feature_class': 'BOITE', 'source_handle': anchor.dxf.handle, 'mode': 'handles',
             'render_handles': [pole.dxf.handle], 'label': 'BOITE / borrowed pole'},
        ],
        'extra_legend': [{'symbol_id': 'legend-green', 'render_handles': [pole.dxf.handle]}],
    }), encoding='utf-8')
    report = prepare(source, [database], tmp_path / 'assets', overrides=overrides)
    # The own-frame handle renders itself; the anchor renders the pole block instead.
    assert report['dispositions'] == {'CURATED_OVERRIDE': 2}
    assert report['overrides']['rule_count'] == 2 and report['overrides']['legend_count'] == 1
    frame_symbol = f"instance-{frame.dxf.handle.lower()}"
    anchor_symbol = f"instance-{anchor.dxf.handle.lower()}"
    bindings = {binding['symbol_id']: binding['source_handle'] for binding in report['source_instance_bindings']}
    assert bindings == {frame_symbol: frame.dxf.handle, anchor_symbol: anchor.dxf.handle}
    with sqlite3.connect(tmp_path / 'assets/symbols.sqlite3') as db:
        statuses = dict(db.execute('SELECT symbol_id,status FROM symbols'))
        handles = dict(db.execute('SELECT symbol_id,source_handles_json FROM symbols'))
        svgs = dict(db.execute('SELECT symbol_id,svg FROM symbols'))
        diagnostics = json.loads(db.execute('SELECT diagnostics_json FROM symbols WHERE symbol_id=?',
                                            (frame_symbol,)).fetchone()[0])
    assert statuses[frame_symbol] == statuses[anchor_symbol] == statuses['legend-green'] == 'candidate'
    # The curated label is rendered to vector outlines inside the frame: white halo behind
    # the coloured glyph, and never a <text> element (the rasteriser only accepts geometry).
    assert '<text' not in svgs[frame_symbol]
    assert '#ff0000' in svgs[frame_symbol] and '#ffffff' in svgs[frame_symbol]
    assert svgs[frame_symbol].count('<path') > svgs[anchor_symbol].count('<path')
    assert diagnostics['overlay_text']['text'] == 'FAT'
    # A rule without overlay_text renders geometry only.
    assert 'overlay_text' not in json.loads(
        db.execute('SELECT diagnostics_json FROM symbols WHERE symbol_id=?', (anchor_symbol,)).fetchone()[0]) \
        or json.loads(db.execute('SELECT diagnostics_json FROM symbols WHERE symbol_id=?',
                                 (anchor_symbol,)).fetchone()[0])['overlay_text'] is None
    # Binding identity stays one-to-one with the delivered handle, while the rendered
    # geometry of the anchor is recorded as the pole block it borrows.
    assert json.loads(handles[anchor_symbol]) == [anchor.dxf.handle]
    assert json.loads(handles['legend-green']) == [pole.dxf.handle]


def test_failed_inventory_has_no_published_directory(tmp_path, monkeypatch):
    import cad2gis.symbol_workflow as workflow
    source = tmp_path / 'source.dxf'
    source.write_bytes(b'source')
    def fail(source, databases, output, **kwargs):
        output.mkdir()
        (output / 'partial.json').write_text('{}')
        raise ValueError('late render failure')
    monkeypatch.setattr(workflow, '_prepare', fail)
    with pytest.raises(ValueError, match='late render'):
        prepare(source, [], tmp_path / 'published')
    assert not (tmp_path / 'published').exists()
    assert not list(tmp_path.glob('.svg-stage-*'))


def test_mismatched_drawing_rejected_before_render(tmp_path, monkeypatch):
    import cad2gis.symbol_workflow as workflow
    source = tmp_path / 'source.dxf'
    source.write_bytes(b'source')
    (tmp_path / 'run_manifest.json').write_text(json.dumps({'source': {'sha256': 'wrong'}}))
    monkeypatch.setattr(workflow, '_prepare', lambda *a, **k: pytest.fail('Unbound drawing rendered'))
    with pytest.raises(ValueError, match='does not match'):
        prepare(source, [tmp_path / 'delivery.gpkg'], tmp_path / 'published')
    assert not (tmp_path / 'published').exists()


@pytest.mark.parametrize('has_point', [False, True])
def test_empty_symbol_library_remains_usable_by_svg_delivery(tmp_path, has_point):
    import hashlib
    from cad2gis.svg_delivery import _project_bindings
    ezdxf = pytest.importorskip('ezdxf')
    document = ezdxf.new()
    primitive = document.modelspace().add_point((20, 0))
    source = tmp_path / 'source.dxf'
    document.saveas(source)
    database = tmp_path / 'delivery.gpkg'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE gpkg_geometry_columns(table_name,geometry_type_name)')
        db.execute("INSERT INTO gpkg_geometry_columns VALUES('PTECH','POINT')")
        db.execute('CREATE TABLE PTECH(source_handle)')
        if has_point:
            db.execute('INSERT INTO PTECH VALUES(?)', (primitive.dxf.handle,))
    report = prepare(source, [database], tmp_path / 'assets')
    assert report['all_points_accounted_for'] == int(has_point)
    with sqlite3.connect(tmp_path / 'assets/symbols.sqlite3') as db:
        statuses = dict(db.execute('SELECT symbol_id,status FROM symbols'))
        assert statuses == {}
        assert json.loads(db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]) == 'cad2gis.symbol-assets.v1'
    assert _project_bindings(report, {'gpkg_sha256': hashlib.sha256(database.read_bytes()).hexdigest()}, statuses) == []
