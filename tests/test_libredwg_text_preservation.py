"""Lossless reader-boundary handling of actual undecodable DXF text."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pytest

from cad2gis import native_runtime
from cad2gis.reader import libredwg_cli


def _restore(record):
    restored = copy.deepcopy(record)
    evidence = restored["raw_properties"].pop("text_encoding_preservation")
    for change in reversed(evidence["fields"]):
        original = bytes.fromhex(change["original_utf8_surrogatepass_hex"]).decode("utf-8", "surrogatepass")
        container = restored
        for part in change["path"][:-1]:
            container = container[part]
        key = change["path"][-1]
        if change["kind"] == "key":
            container[original] = container.pop(key)
        else:
            container[key] = original
    return restored


def test_ordinary_unicode_and_literal_escape_spellings_are_unchanged():
    original = {
        "text": "标注 ° 😀 " + r"\udcb0\P",
        "block_attributes": {r"\udcb0": "literal", "中文": "正常"},
        "points": [(1.25, 2.75)], "raw_properties": {},
    }
    before = json.dumps(original, ensure_ascii=False).encode("utf-8")
    actual = libredwg_cli._preserve_undecodable_strings(original, ezdxf.new())
    assert actual is original
    assert json.dumps(actual, ensure_ascii=False).encode("utf-8") == before


@pytest.mark.parametrize("value", [
    "Coordinate: 1.290732\udcb0 124.876540\udcb0" + r"\PNext",
    "high \ud800 / low \udfff / pair \ud83d\ude00 / actual 😀 / literal " + r"\udcb0",
])
def test_original_reader_code_units_are_exactly_recoverable(value):
    original = {"text": value, "points": [(1.25, 2.75), (4.25, 6.75)],
                "native_length": 5.0, "raw_properties": {"raw_text": value}}
    before = copy.deepcopy(original)
    actual = libredwg_cli._preserve_undecodable_strings(original, ezdxf.new())
    json.dumps(actual, ensure_ascii=False).encode("utf-8")
    assert original == before
    assert actual["points"] == original["points"]
    assert actual["native_length"] == original["native_length"]
    assert "\ufffd" not in actual["text"] and "°" not in actual["text"]
    assert _restore(actual) == original
    evidence = actual["raw_properties"]["text_encoding_preservation"]
    assert evidence["affected_fields"] == 2
    assert all(item["original_json"] == json.dumps(value, ensure_ascii=True) for item in evidence["fields"])
    assert libredwg_cli._preserve_undecodable_strings(actual, ezdxf.new()) is actual


def test_nested_key_escape_collisions_preserve_every_key_and_value():
    original = {
        "raw_properties": {},
        "block_attributes": {
            "\udcb0": {"\udc80": ["\ud800", "plain"]},
            r"\udcb0": "existing literal key",
            r"\udcb0~cad2gis-key-1": "existing suffix key",
        },
    }
    actual = libredwg_cli._preserve_undecodable_strings(original, ezdxf.new())
    assert len(actual["block_attributes"]) == 3
    assert actual["block_attributes"][r"\udcb0"] == "existing literal key"
    assert actual["block_attributes"][r"\udcb0~cad2gis-key-1"] == "existing suffix key"
    assert actual["raw_properties"]["text_encoding_preservation"]["key_collisions"] == 1
    assert _restore(actual) == original
    json.dumps(actual, ensure_ascii=False).encode("utf-8")


def test_record_identity_does_not_collide_with_literal_layout_escape(tmp_path):
    document = ezdxf.new()
    entity = document.modelspace().add_line((0, 0), (3, 4))
    common = {"document": document, "entity": entity, "source": tmp_path / "test.dwg",
              "source_sha256": "a" * 64, "layout_role": "layout", "cad_role": "layout"}
    damaged = libredwg_cli._record(**common, layout="Layout\udcb0")
    ordinary = libredwg_cli._record(**common, layout=r"Layout\udcb0")
    replacements = libredwg_cli._escaped_string_map([damaged, ordinary])
    damaged = libredwg_cli._preserve_undecodable_strings(damaged, document, replacements)
    assert damaged["layout"] != ordinary["layout"]
    assert damaged["entity_key"] != ordinary["entity_key"]
    expected = hashlib.sha256(f"{'a' * 64}|{entity.dxf.handle}|Layout\\udcb0".encode("utf-8")).hexdigest()
    assert ordinary["entity_key"] == expected
    assert _restore(damaged)["layout"] == "Layout\udcb0"
    assert damaged["points"] == ordinary["points"] == [(0.0, 0.0), (3.0, 4.0)]


def test_inventory_wide_string_mapping_preserves_cross_record_references():
    records = [
        {"layout": "BLOCKDEF:\udcb0", "block_name": "\udcb0", "raw_properties": {}},
        {"block_name": "\udcb0", "raw_properties": {}},
        {"block_name": r"\udcb0", "raw_properties": {}},
        {"block_name": r"\udcb0~cad2gis-string-1", "raw_properties": {}},
    ]
    replacements = libredwg_cli._escaped_string_map(records)
    actual = [libredwg_cli._preserve_undecodable_strings(record, ezdxf.new(), replacements) for record in records]
    assert actual[0]["block_name"] == actual[1]["block_name"]
    assert actual[0]["layout"] == "BLOCKDEF:" + actual[0]["block_name"]
    assert len({record["block_name"] for record in actual}) == 3
    assert actual[2] is records[2] and actual[3] is records[3]
    assert _restore(actual[0]) == records[0]
    assert _restore(actual[1]) == records[1]


@pytest.mark.parametrize("damaged,ordinary", [("\udcb0", r"\uDCB0"), (" \udcb0 ", r"\udcb0")])
def test_normalized_block_keys_keep_distinct_instance_geometry(damaged, ordinary):
    from cad2gis.cad2gis_v3.model import SourceEntity
    from cad2gis.cad2gis_v3.plan_domain import build_plan_domain

    def entity(key, kind, name, points, *, definition=False):
        raw = {} if definition else {"transform_facts": {
            "insertion_point": [*points[0], 0], "block_base_point": [0, 0, 0],
            "scale": [1, 1, 1], "rotation": 0, "normal": [0, 0, 1], "extrusion": [0, 0, 1]}}
        return {"entity_key": key, "source_sha256": "a" * 64, "handle": key,
                "dwg_type_name": kind, "layer": "0", "block_name": name,
                "layout": f"BLOCKDEF:{name}" if definition else "Model",
                "layout_role": "block_definition" if definition else "model",
                "cad_role": "block_definition" if definition else "style_legend",
                "points": points, "centroid": points[0], "raw_properties": raw}

    records = [entity("bad-line", "LINE", damaged, [[0, 0], [1, 0]], definition=True),
               entity("good-line", "LINE", ordinary, [[0, 0], [7, 0]], definition=True),
               entity("bad-insert", "INSERT", damaged, [[100, 0]]),
               entity("good-insert", "INSERT", ordinary, [[200, 0]])]
    replacements = libredwg_cli._escaped_string_map(records)
    safe = [libredwg_cli._preserve_undecodable_strings(r, ezdxf.new(), replacements) for r in records]
    assert safe[2]["block_name"].strip().upper() != safe[3]["block_name"].strip().upper()
    plan = build_plan_domain([SourceEntity.from_record(r) for r in safe])
    lines = sorted(tuple(tuple(p) for p in e.points) for e in plan.entities if e.dwg_type == "LINE")
    assert lines == [((100.0, 0.0), (101.0, 0.0)), ((200.0, 0.0), (207.0, 0.0))]
    assert _restore(safe[0]) == records[0]
    assert _restore(safe[2]) == records[2]


def test_extraction_preserves_real_invalid_utf8_byte_and_reports_counts(tmp_path, monkeypatch):
    source = tmp_path / "drawing.dwg"
    source.write_bytes(b"test-dwg")
    executable = tmp_path / "dwg2dxf.exe"
    executable.write_bytes(b"stub")
    monkeypatch.setenv(native_runtime.LIBREDWG_CLI_ENV, str(executable))

    def convert(arguments, **_kwargs):
        output = Path(arguments[arguments.index("-o") + 1])
        document = ezdxf.new("R2018")
        document.modelspace().add_mtext("Coordinate: 1.290732invalid-byte-marker", dxfattribs={"insert": (1, 2)})
        document.saveas(output)
        output.write_bytes(output.read_bytes().replace(b"invalid-byte-marker", b"\xb0"))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(libredwg_cli.subprocess, "run", convert)
    records = libredwg_cli.extract_dwg_records(source)
    text = next(record for record in records if record["dwg_type_name"] == "MTEXT")
    assert text["text"] == r"Coordinate: 1.290732\udcb0"
    assert _restore(text)["text"] == "Coordinate: 1.290732\udcb0"
    assert text["points"] == [(1.0, 2.0)]
    assert records.diagnostics["inventory_complete"] is True
    assert records.diagnostics["skipped_rows"] == 0
    diagnostic = records.diagnostics["text_encoding_preservation"]
    assert diagnostic["affected_records"] == 1
    assert diagnostic["affected_fields"] == 3
    assert diagnostic["surrogate_code_units"] == 3
    assert len(diagnostic["intermediate_dxf_sha256"]) == 64
    json.dumps(records, ensure_ascii=False).encode("utf-8")
