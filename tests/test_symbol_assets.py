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
