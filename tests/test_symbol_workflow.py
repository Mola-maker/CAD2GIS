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
