import base64
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET

import pytest

from cad2gis.symbol_assets import _plain_svg, export_qml, extract


def source_selection(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    doc = ezdxf.new()
    circle = doc.modelspace().add_circle((100.123456789, 200.987654321), 2.5)
    line = doc.modelspace().add_line((98, 201), (102, 201))
    source = tmp_path / "source.dxf"
    doc.saveas(source)
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                    "symbols": [{"symbol_id": "pole", "label": "Pole <source>",
                                                 "handles": [circle.dxf.handle, line.dxf.handle]}]}))
    return source, selection


def test_independent_store_and_portable_qml_preserve_source(tmp_path):
    source, selection = source_selection(tmp_path)
    before = source.read_bytes()
    result = extract(source, selection, tmp_path / "assets")
    store = tmp_path / "assets/symbols.sqlite3"
    store_before = store.read_bytes()
    qml = tmp_path / "candidate.qml"
    receipt = export_qml(store, "pole", qml)
    assert receipt["auto_applied"] is False and result["symbols"] == 1
    assert source.read_bytes() == before and store.read_bytes() == store_before
    assert not list(tmp_path.rglob("*.gpkg"))
    root = ET.parse(qml).getroot()
    assert root.find(".//layer").get("class") == "SvgMarker"
    data = root.find('.//Option[@name="name"]').get("value")
    assert base64.b64decode(data.removeprefix("base64:")) == (tmp_path / "assets/symbols/pole.svg").read_bytes()
    svg_root = ET.fromstring(base64.b64decode(data.removeprefix("base64:")))
    assert max(map(float, svg_root.get("viewBox").split())) <= 10000
    background = svg_root.find('{http://www.w3.org/2000/svg}rect')
    assert background is None or float(background.get('fill-opacity', '1')) == 0
    with sqlite3.connect(store) as db:
        facts = json.loads(db.execute("SELECT source_entities_json FROM symbols").fetchone()[0])
        assert "100.123456789" in json.dumps(facts)
    assert "Pole &lt;source&gt;" in (tmp_path / "assets/index.html").read_text()
    with pytest.raises(FileExistsError):
        extract(source, selection, tmp_path / "assets")


@pytest.mark.parametrize("failure", ["source_hash", "missing_handle", "duplicate", "path_id"])
def test_invalid_selection_creates_no_library(tmp_path, failure):
    source, selection = source_selection(tmp_path)
    data = json.loads(selection.read_text())
    if failure == "source_hash":
        data["source_sha256"] = "wrong"
    elif failure == "missing_handle":
        data["symbols"][0]["handles"] = ["FFFFF"]
    elif failure == "duplicate":
        data["symbols"] *= 2
    else:
        data["symbols"][0]["symbol_id"] = "../../bad"
    selection.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        extract(source, selection, tmp_path / "assets")
    assert not (tmp_path / "assets").exists()


def test_modified_or_incomplete_symbol_cannot_be_exported(tmp_path):
    source, selection = source_selection(tmp_path)
    extract(source, selection, tmp_path / "assets")
    store = tmp_path / "assets/symbols.sqlite3"
    with sqlite3.connect(store) as db:
        db.execute("UPDATE symbols SET svg=svg || ' '")
    with pytest.raises(ValueError, match="modified"):
        export_qml(store, "pole", tmp_path / "bad.qml")
    assert not (tmp_path / "bad.qml").exists()


def test_no_font_environment_rejects_text_before_publication(tmp_path, monkeypatch):
    ezdxf = pytest.importorskip("ezdxf")
    from ezdxf.fonts import fonts
    source, selection = source_selection(tmp_path)
    doc = ezdxf.readfile(source)
    text = doc.modelspace().add_text('NP7', dxfattribs={'height': 1})
    doc.saveas(source)
    data = json.loads(selection.read_text())
    data['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    data['symbols'][0]['handles'].append(text.dxf.handle)
    selection.write_text(json.dumps(data))

    def missing():
        raise fonts.FontNotFoundError('no fonts')

    monkeypatch.setattr(fonts.font_manager, 'fallback_font_name', missing)
    with pytest.raises(ValueError, match='No fonts available'):
        extract(source, selection, tmp_path/'assets')
    assert not (tmp_path/'assets').exists()


def test_explicitly_selected_hidden_entity_is_not_silently_complete(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    source, selection = source_selection(tmp_path)
    doc = ezdxf.readfile(source)
    data = json.loads(selection.read_text())
    doc.entitydb[data['symbols'][0]['handles'][1]].dxf.invisible = 1
    doc.saveas(source)
    data['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    selection.write_text(json.dumps(data))
    extract(source, selection, tmp_path/'assets')
    with sqlite3.connect(tmp_path/'assets/symbols.sqlite3') as db:
        assert db.execute('SELECT status FROM symbols').fetchone()[0] == 'incomplete'
    with pytest.raises(ValueError, match='Incomplete'):
        export_qml(tmp_path/'assets/symbols.sqlite3', 'pole', tmp_path/'hidden.qml')


@pytest.mark.parametrize("payload", ['<svg><script>alert(1)</script></svg>',
                                   '<svg><path fill="url(https://example.com)"/></svg>',
                                   '<svg><path onclick="run()"/></svg>'])
def test_no_external_or_active_svg(payload):
    with pytest.raises(ValueError):
        _plain_svg(payload)


def test_png_preview_keeps_aspect_ratio_center_and_blocks_external_content():
    import io
    import pytest
    from PIL import Image, ImageChops
    from cad2gis.symbol_assets import rasterize_svg
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><rect width="200" height="100" fill="black"/></svg>'
    png = rasterize_svg(svg)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert image.size == (512, 512)
    bounds = ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
    assert bounds == (0, 128, 512, 384)
    assert rasterize_svg(svg) == png
    with pytest.raises(ValueError):
        rasterize_svg('<svg><image href="https://example.com/private.png"/></svg>')


@pytest.mark.parametrize('failure', ['missing_identity', 'different_layout'])
def test_render_override_cannot_hide_invalid_identity_or_layout(tmp_path, failure):
    ezdxf = pytest.importorskip('ezdxf')
    source, selection = source_selection(tmp_path)
    data = json.loads(selection.read_text())
    item = data['symbols'][0]
    item['render_handles'] = [item['handles'][0]]
    if failure == 'missing_identity':
        item['handles'] = ['FFFFF']
    else:
        document = ezdxf.readfile(source)
        point = document.layouts.new('Review').add_point((0, 0))
        document.saveas(source)
        data['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        item['handles'] = [point.dxf.handle]
    selection.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='identity|layouts'):
        extract(source, selection, tmp_path / 'assets')
    assert not (tmp_path / 'assets').exists()


def test_single_invisible_symbol_is_audited_and_cannot_export(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    source, selection = source_selection(tmp_path)
    document = ezdxf.readfile(source)
    data = json.loads(selection.read_text())
    data['symbols'][0]['handles'] = data['symbols'][0]['handles'][:1]
    document.entitydb[data['symbols'][0]['handles'][0]].dxf.invisible = 1
    document.saveas(source)
    data['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    selection.write_text(json.dumps(data))
    extract(source, selection, tmp_path / 'assets')
    with sqlite3.connect(tmp_path / 'assets/symbols.sqlite3') as db:
        status, diagnostics = db.execute('SELECT status,diagnostics_json FROM symbols').fetchone()
    assert status == 'incomplete'
    assert any(item['reason'] == 'invisible' for item in json.loads(diagnostics)['skipped'])
    with pytest.raises(ValueError, match='Incomplete'):
        export_qml(tmp_path / 'assets/symbols.sqlite3', 'pole', tmp_path / 'invalid.qml')


def test_source_layer_byblock_and_bylayer_colors_survive_nested_blocks(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    document = ezdxf.new()
    document.layers.new('INSERT_COLOR', dxfattribs={'color': 3})
    document.layers.new('MEMBER_COLOR', dxfattribs={'color': 5})
    inner = document.blocks.new('INNER')
    inner.add_circle((0, 0), 1, dxfattribs={'layer': '0', 'color': 0})
    inner.add_line((-1, 0), (1, 0), dxfattribs={'layer': 'MEMBER_COLOR', 'color': 256})
    outer = document.blocks.new('OUTER')
    outer.add_blockref('INNER', (10, 0), dxfattribs={'layer': '0', 'color': 0})
    instance = document.layouts.new('Sheet').add_blockref(
        'OUTER', (0, 0), dxfattribs={'layer': 'INSERT_COLOR', 'xscale': -2, 'yscale': 1})
    source = tmp_path / 'source.dxf'
    document.saveas(source)
    selection = tmp_path / 'selection.json'
    selection.write_text(json.dumps({'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                     'symbols': [{'symbol_id': 'nested', 'handles': [instance.dxf.handle]}]}))
    extract(source, selection, tmp_path / 'assets')
    with sqlite3.connect(tmp_path / 'assets/symbols.sqlite3') as db:
        svg, facts, diagnostics = db.execute('SELECT svg,source_entities_json,diagnostics_json FROM symbols').fetchone()
    assert '#00ff00' in svg and '#0000ff' in svg
    assert json.loads(diagnostics)['source_layout'] == 'Sheet'
    assert any(fact['source_layer'] == 'MEMBER_COLOR' for fact in json.loads(facts))
    assert any(fact['attributes'].get('xscale') == -2 for fact in json.loads(facts))


def test_source_redraw_order_is_independent_of_handle_selection_order(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    document = ezdxf.new()
    msp = document.modelspace()
    blue = msp.add_solid([(0, 0), (3, 0), (0, 3), (3, 3)], dxfattribs={'color': 5})
    red = msp.add_solid([(1, 1), (2, 1), (1, 2), (2, 2)], dxfattribs={'color': 1})
    msp.set_redraw_order([(blue.dxf.handle, '20'), (red.dxf.handle, '10')])
    source = tmp_path / 'source.dxf'
    document.saveas(source)
    selection = tmp_path / 'selection.json'
    selection.write_text(json.dumps({'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                     'symbols': [{'symbol_id': 'stack', 'handles': [blue.dxf.handle, red.dxf.handle]}]}))
    extract(source, selection, tmp_path / 'assets')
    svg = (tmp_path / 'assets/symbols/stack.svg').read_text()
    assert svg.index('#ff0000') < svg.index('#0000ff')


@pytest.mark.parametrize('nested', [False, True])
def test_real_insert_attributes_keep_layout_text_and_change_source_fingerprint(tmp_path, nested):
    ezdxf = pytest.importorskip('ezdxf')
    from cad2gis.symbol_assets import _closure, _json, _source_layout

    document = ezdxf.new()
    inner = document.blocks.new('LABELLED_POLE')
    inner.add_circle((0, 0), 2, dxfattribs={'color': 3})
    sheet = document.layouts.new('Survey sheet')
    if nested:
        outer = document.blocks.new('OUTER_ASSEMBLY')
        label_owner = outer.add_blockref('LABELLED_POLE', (4, 0))
        selected = sheet.add_blockref('OUTER_ASSEMBLY', (20, 10))
        selected.add_attrib('SITE', 'SITE-001', (20, 14), dxfattribs={'height': 1})
        expected_layout = 'OUTER_ASSEMBLY'
    else:
        selected = label_owner = sheet.add_blockref('LABELLED_POLE', (20, 10))
        expected_layout = 'Survey sheet'
    attribute = label_owner.add_attrib('CAPACITY', 'FAT-16', (5, 0), dxfattribs={'height': 1, 'color': 1})
    # LibreDWG/DWG attributes use the owning INSERT, not its BLOCK_RECORD.
    attribute.dxf.owner = label_owner.dxf.handle
    source = tmp_path / 'with-attributes.dxf'
    document.saveas(source)
    document = ezdxf.readfile(source)
    attribute = document.entitydb[attribute.dxf.handle]
    selected = document.entitydb[selected.dxf.handle]
    assert document.entitydb[attribute.dxf.owner].dxftype() == 'INSERT'
    assert _source_layout(document, attribute).name == expected_layout
    before = hashlib.sha256(_json(_closure(document, [selected])).encode()).hexdigest()
    selection = tmp_path / 'selection.json'

    def extract_current(output):
        selection.write_text(json.dumps({'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                         'symbols': [{'symbol_id': 'attached', 'handles': [selected.dxf.handle]}]}))
        extract(source, selection, output)
        with sqlite3.connect(output / 'symbols.sqlite3') as db:
            status, svg_hash, facts, diagnostics = db.execute('SELECT status,svg_sha256,source_entities_json,diagnostics_json FROM symbols').fetchone()
        assert status == 'candidate'
        assert json.loads(diagnostics)['source_layout'] == 'Survey sheet'
        facts = json.loads(facts)
        found = next(fact for fact in facts if fact['handle'] == attribute.dxf.handle)
        assert found['type'] == 'ATTRIB' and found['source_layout'] == expected_layout
        assert found['attributes']['text'] == attribute.dxf.text
        if nested:
            assert any(fact['type'] == 'ATTRIB' and fact['attributes']['text'] == 'SITE-001' for fact in facts)
        return svg_hash, facts

    original_hash, original_facts = extract_current(tmp_path / 'first')
    original_bytes = source.read_bytes()
    attribute.dxf.text = 'FDT-144'
    after = hashlib.sha256(_json(_closure(document, [selected])).encode()).hexdigest()
    assert before != after
    document.saveas(source)
    changed_hash, changed_facts = extract_current(tmp_path / 'second')
    assert original_hash != changed_hash
    assert original_facts != changed_facts
    assert original_bytes != source.read_bytes()


def test_unlinked_child_inherits_layout_without_discarding_attribute_facts():
    ezdxf = pytest.importorskip('ezdxf')
    from cad2gis.symbol_assets import _closure

    document = ezdxf.new()
    block = document.blocks.new('POLE')
    block.add_circle((0, 0), 1)
    instance = document.layouts.new('Sheet').add_blockref('POLE', (0, 0))
    first = instance.add_attrib('FIRST', 'first value')
    second = instance.add_attrib('SECOND', 'second value')
    # Detached copies have no handle/owner, but remain explicit INSERT children.
    first.dxf.discard('handle')
    first.dxf.discard('owner')
    second.dxf.discard('handle')
    second.dxf.discard('owner')
    facts = [fact for fact in _closure(document, [instance]) if fact['type'] == 'ATTRIB']
    assert {fact['attributes']['text'] for fact in facts} == {'first value', 'second value'}
    assert all(fact['source_layout'] == 'Sheet' for fact in facts)


@pytest.mark.parametrize('owner_problem', ['self_cycle', 'parent_cycle', 'missing'])
def test_invalid_attribute_owner_chain_fails_explicitly_without_skipping(owner_problem):
    ezdxf = pytest.importorskip('ezdxf')
    from cad2gis.symbol_assets import _closure

    document = ezdxf.new()
    block = document.blocks.new('POLE')
    block.add_circle((0, 0), 1)
    instance = document.modelspace().add_blockref('POLE', (0, 0))
    attribute = instance.add_attrib('LABEL', 'must remain evidence')
    attribute.dxf.owner = attribute.dxf.handle if owner_problem == 'self_cycle' else instance.dxf.handle
    if owner_problem == 'parent_cycle':
        instance.dxf.owner = attribute.dxf.handle
    elif owner_problem == 'missing':
        attribute.dxf.owner = 'FFFFFE'
    with pytest.raises(ValueError, match='Cyclic CAD owner|Missing CAD owner'):
        _closure(document, [instance])
