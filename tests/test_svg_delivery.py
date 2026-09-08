import hashlib
import sqlite3

import pytest

from cad2gis.svg_delivery import _project_bindings


def binding(scope, handle='A1', symbol='instance-a1'):
    return dict(database_scope=scope, layer='PTECH', source_handle=handle, symbol_id=symbol)


@pytest.mark.parametrize('source_path', [
    r'E:\review\kletek-verified\delivery.gpkg',
    '/data/review/kletek-verified/delivery.gpkg',
])
def test_matches_extraction_scope_after_delivery_directory_is_renamed(source_path):
    correspondence = {
        'input_databases_sha256': {source_path: 'main-hash', '/run/PANEL/delivery.gpkg': 'other-hash'},
        'source_instance_bindings': [binding('kletek-verified'), binding('PANEL', 'B2', 'instance-b2')],
    }
    selected = _project_bindings(correspondence, {'project': 'delivery.qgz', 'gpkg_sha256': 'main-hash'},
                                 {'instance-a1': 'candidate', 'instance-b2': 'candidate'})
    assert selected == [dict(layer='PTECH', source_handle='A1', symbol_id='instance-a1')]


def test_hash_mismatch_cannot_silently_publish_a_project_without_svg():
    correspondence = {'input_databases_sha256': {'/run/delivery.gpkg': 'different-hash'},
                      'source_instance_bindings': [binding('delivery')]}
    with pytest.raises(ValueError, match='no matching delivery database hash'):
        _project_bindings(correspondence, {'project': 'delivery.qgz', 'gpkg_sha256': 'expected-hash'},
                          {'instance-a1': 'candidate'})


def test_identical_database_copies_deduplicate_bindings_and_exclude_incomplete_svg():
    correspondence = {
        'input_databases_sha256': {'/run/a/delivery.gpkg': 'same-hash', '/copy/b/delivery.gpkg': 'same-hash'},
        'source_instance_bindings': [binding('a'), binding('b'), binding('a', 'B2', 'instance-b2')],
    }
    selected = _project_bindings(correspondence, {'project': 'delivery.qgz', 'gpkg_sha256': 'same-hash'},
                                 {'instance-a1': 'candidate', 'instance-b2': 'incomplete'})
    assert selected == [dict(layer='PTECH', source_handle='A1', symbol_id='instance-a1')]


@pytest.mark.parametrize(('project', 'scope'), [('delivery.qgz', 'delivery'), ('PANEL/delivery.qgz', 'PANEL')])
def test_legacy_inventory_keeps_main_and_partition_scope_selection(project, scope):
    selected = _project_bindings({'source_instance_bindings': [binding(scope), binding('unrelated', 'B2', 'instance-b2')]},
                                 {'project': project}, {'instance-a1': 'candidate', 'instance-b2': 'candidate'})
    assert selected == [dict(layer='PTECH', source_handle='A1', symbol_id='instance-a1')]


def test_real_extraction_connects_to_renamed_qgis_delivery(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    from cad2gis.symbol_workflow import prepare

    doc = ezdxf.new()
    doc.blocks.new('POLE').add_circle((0, 0), 1)
    source_entity = doc.modelspace().add_blockref('POLE', (0, 0))
    source = tmp_path / 'source.dxf'
    doc.saveas(source)
    run = tmp_path / 'reviewed-run-123'
    run.mkdir()
    database = run / 'delivery.gpkg'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE gpkg_geometry_columns(table_name, geometry_type_name)')
        db.execute("INSERT INTO gpkg_geometry_columns VALUES ('PTECH', 'POINT')")
        db.execute('CREATE TABLE PTECH(source_handle)')
        db.execute('INSERT INTO PTECH VALUES (?)', (source_entity.dxf.handle,))
    correspondence = prepare(source, [database], tmp_path / 'assets')
    selected = _project_bindings(correspondence, {
        'project': 'delivery.qgz', 'gpkg_sha256': hashlib.sha256(database.read_bytes()).hexdigest(),
    }, {b['symbol_id']: 'candidate' for b in correspondence['source_instance_bindings']})
    assert len(selected) == 1
    assert selected[0]['source_handle'] == source_entity.dxf.handle
