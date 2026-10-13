import hashlib

import ezdxf
import pytest


def source_dxf(tmp_path):
    source = tmp_path / 'synthetic.dxf'
    doc = ezdxf.new('R2010')
    doc.header['$INSUNITS'] = 6
    line = doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={'layer': 'CABLE'})
    block = doc.blocks.new('BOX')
    block.add_circle((0, 0), 1)
    insert = doc.modelspace().add_blockref('BOX', (5, 5))
    insert.add_attrib('ID', 'BOX-1')
    doc.saveas(source)
    return source, line.dxf.handle


def test_native_dxf_keeps_handles_blocks_attributes_and_hash(tmp_path):
    from cad2gis.reader.dxf import extract_dxf_records
    source, handle = source_dxf(tmp_path)
    records = extract_dxf_records(source)
    assert records.diagnostics['backend'] == 'native_dxf'
    assert records.diagnostics['inventory_complete'] is True
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert all(r['source_sha256'] == sha for r in records)
    line = next(r for r in records if r['handle'] == handle)
    assert line['points'] == [(0.0, 0.0), (10.0, 0.0)]
    insert = next(r for r in records if r['dwg_type_name'] == 'INSERT')
    assert insert['block_attributes']['ID'] == 'BOX-1'
    assert any(r['dwg_type_name'] == 'ATTRIB' and r['text'] == 'BOX-1' for r in records)
    assert all(r['raw_properties']['extraction_backend'] == 'native_dxf' for r in records)


def test_native_dxf_needs_no_libredwg(tmp_path, monkeypatch):
    from cad2gis.reader.resolver import extract_records
    from cad2gis.reader import libredwg, libredwg_cli
    source, _ = source_dxf(tmp_path)
    monkeypatch.setattr(libredwg, 'libredwg_capability', lambda: pytest.fail('native bindings probed'))
    monkeypatch.setattr(libredwg_cli, 'libredwg_cli_capability', lambda: pytest.fail('DWG converter probed'))
    assert extract_records(source).diagnostics['backend'] == 'native_dxf'


def test_dxf_source_export_is_native_and_replayable(tmp_path):
    from cad2gis.cad2gis_v3.source_export import export_source
    from cad2gis.cad2gis_v3.source_replay import load_native_snapshot
    source, _ = source_dxf(tmp_path)
    root = tmp_path / 'snapshot'
    manifest = export_source(source=source, run_dir=root)
    assert manifest['reader_provenance']['mode'] == 'native_reader'
    assert manifest['reader_provenance']['diagnostics']['backend'] == 'native_dxf'
    records, receipt = load_native_snapshot(root, source)
    assert receipt['reader_invoked'] is False
    assert len(records) == manifest['entity_count']
