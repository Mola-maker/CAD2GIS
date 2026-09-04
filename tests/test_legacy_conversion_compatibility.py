"""Compatibility and import boundaries for the isolated experiment converter."""

from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _record(handle, *, points=((10.0, 20.0),), **changes):
    record = {
        "handle": handle,
        "layout": "Model",
        "cad_role": "model",
        "object_name": "ACDBPOINT",
        "dwg_type_name": "POINT",
        "layer": "misc",
        "text": "",
        "block_name": "",
        "block_attributes": {},
        "points": list(points),
        "centroid": tuple(sum(point[index] for point in points) / len(points) for index in (0, 1)),
        "closed": False,
        "aci_color": 256,
        "true_color": "",
        "linetype": "ByLayer",
        "lineweight": -1,
        "rotation": 0.0,
    }
    record.update(changes)
    return record


def _assign_fc(layer, text):
    return {
        "cable": ("CABLE", "LineString", 0.9, "layer"),
        "zone": ("ZNRO", "Polygon", 0.9, "layer"),
        "home number": ("IMB", "Point", 0.9, "layer"),
    }.get(layer, ("fc_misc", "Point", 0.0, "unclassified"))


def _attributes(text, family):
    return {"CODE": text} if text.startswith("DMPH") else {}


def _convert(records):
    from cad2gis.apd_rules import classify_insert_block
    from cad2gis.reader.autocad import build_items_from_records

    return build_items_from_records(
        records, "legacy.dwg", lambda x, y: (x, y), _assign_fc,
        classify_insert_block, _attributes,
    )


def test_legacy_public_entry_point_signatures_are_preserved():
    from cad2gis.reader.autocad import build_items_from_records, read_dwg_with_autocad

    assert str(inspect.signature(build_items_from_records)) == (
        "(records, source_name, reproject_point, assign_fc, classify_block, extract_attributes)"
    )
    assert str(inspect.signature(read_dwg_with_autocad)) == (
        "(dwg_path, reproject_point, assign_fc, classify_block, extract_attributes, "
        "*, accoreconsole=None, timeout=None, compatibility_policy='strict')"
    )


def test_legacy_conversion_preserves_features_labels_and_evidence():
    records = [
        _record("cable", points=((10, 20), (11, 21)), layer="cable", object_name="ACDBLINE"),
        _record("zone", points=((10, 20), (11, 20), (11, 21)), layer="zone", closed=True),
        _record("pole", object_name="ACDBBLOCKREFERENCE", block_name="*U13"),
        _record("label", object_name="ACDBTEXT", text="DMPH-P1"),
        _record("loose", object_name="ACDBTEXT", text="Unmatched note"),
        _record("home", object_name="ACDBTEXT", layer="home number", text="42"),
        _record("topology", cad_role="topology"),
        _record("legend", cad_role="style_legend"),
        _record("summary", cad_role="design_summary", text="Summary"),
        _record("dimension", object_name="ACDBDIMENSION", dimension_value=2.0),
        _record("paper", cad_role="layout"),
        _record("decoration", object_name="ACDBBLOCKREFERENCE", block_name="title"),
        _record("open-zone", points=((10, 20), (11, 20), (11, 21)), layer="zone"),
        _record("outlier", points=((200, 100),), object_name="ACDBBLOCKREFERENCE", block_name="*U13"),
    ]

    items = _convert(records)

    features = {item["cad_handle"]: item for item in items if item["output_kind"] == "feature"}
    assert set(features) == {"cable", "zone", "pole", "home"}
    assert features["cable"]["wkt"] == "LINESTRING (10 20, 11 21)"
    assert features["zone"]["wkt"] == "POLYGON ((10 20, 11 20, 11 21, 10 20))"
    assert features["pole"]["wkt"] == "POINT (10 20)"
    assert features["pole"]["attrs"] == {"CODE": "DMPH-P1"}
    assert features["pole"]["label_method"] == "DWG_DERIVED:apd-family-nearest-15m"
    assert features["home"]["attrs"] == {"CODE": "42"}
    assert features["home"]["label_method"] == "DWG_DIRECT"
    assert {
        item["handle"]: item["terminal_disposition"]
        for item in items if item["output_kind"] == "source_evidence"
    } == {
        "cable": "mapped", "zone": "mapped", "pole": "mapped",
        "label": "annotation", "loose": "annotation", "home": "mapped",
        "topology": "annotation", "legend": "legend", "summary": "annotation",
        "dimension": "annotation", "paper": "out_of_scope",
        "decoration": "graphic_only", "open-zone": "graphic_only", "outlier": "graphic_only",
    }
    assert [
        item["output_kind"] for item in items
        if item["output_kind"] not in {"source_evidence", "feature"}
    ] == [
        "topology_evidence", "style_evidence", "summary_evidence",
        "dimension_evidence", "annotation_evidence",
    ]
    assert items[-1]["text"] == "Unmatched note"


@pytest.mark.parametrize("fallback", [False, True])
def test_legacy_read_reuses_reader_backends_and_preserves_identity(tmp_path, monkeypatch, fallback):
    from cad2gis.apd_rules import classify_insert_block
    from cad2gis.legacy import autocad_conversion
    from cad2gis.reader import autocad

    source = tmp_path / "legacy.dwg"
    source.write_bytes(b"legacy compatibility source")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    grouped = [
        ("Model", "model", [_record("pole", object_name="ACDBBLOCKREFERENCE", block_name="*U13")]),
        ("Plan", "plan", [_record("paper", cad_role="plan", layout="Plan")]),
    ]
    events = []

    def bulk(path, **options):
        assert path == source.resolve()
        assert options == {"accoreconsole": "configured.exe", "timeout": 7, "compatibility_policy": "strict"}
        events.append("bulk")
        if fallback:
            raise RuntimeError("bulk unavailable")
        return grouped

    def authorize(error):
        assert str(error) == "bulk unavailable"
        events.append("authorize")

    def collect(database, **options):
        assert database is sentinel
        assert options == {"assign_fc": _assign_fc, "reader_backend_status": "fallback_after_core_console_failure"}
        events.append("collect")
        return grouped

    sentinel = object()
    opened = SimpleNamespace(Close=lambda save: events.append(("close", save)))
    application = SimpleNamespace(Quit=lambda: events.append("quit"))
    pythoncom = SimpleNamespace(CoUninitialize=lambda: events.append("uninitialize"))
    monkeypatch.setattr(autocad_conversion, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(autocad, "_extract_records_with_core_console", bulk)
    monkeypatch.setattr(autocad, "_authorize_com_fallback", authorize)
    monkeypatch.setattr(autocad, "_open_autocad_database", lambda _: (pythoncom, application, True, sentinel, opened))
    monkeypatch.setattr(autocad, "_collect_records", collect)

    items = autocad.read_dwg_with_autocad(
        source, lambda x, y: (x, y), _assign_fc, classify_insert_block, _attributes,
        accoreconsole="configured.exe", timeout=7,
    )

    assert len(items) == 3
    assert items[-1]["cad_role"] == "layout"
    assert items[-1]["terminal_disposition"] == "out_of_scope"
    assert all(item["source_sha256"] == source_hash for item in items)
    assert items[0]["entity_key"] == items[1]["entity_key"] == hashlib.sha256(
        f"{source_hash}|pole|Model".encode("utf-8")
    ).hexdigest()
    assert events == (
        ["bulk", "authorize", "collect", ("close", False), "quit", "uninitialize"]
        if fallback else ["bulk"]
    )


def test_axis_helper_and_legacy_public_rules_remain_compatible():
    from cad2gis import apd_rules
    from cad2gis.coordinate_runtime import set_traditional_axis_order
    from cad2gis.legacy import apd_rules as legacy

    for name in apd_rules.__all__:
        if name != "set_traditional_axis_order":
            assert getattr(apd_rules, name) is getattr(legacy, name)
    assert apd_rules.set_traditional_axis_order is set_traditional_axis_order
    assert apd_rules.classify_insert_block("*U7") == "SITE"
    assert apd_rules.classify_annotation_target("DMPH-P1") == "PTECH"
    seen = []
    spatial_reference = SimpleNamespace(SetAxisMappingStrategy=seen.append)
    strategy = SimpleNamespace(OAMS_TRADITIONAL_GIS_ORDER=0)
    set_traditional_axis_order(spatial_reference, strategy)
    set_traditional_axis_order(object(), strategy)
    set_traditional_axis_order(spatial_reference, object())
    assert seen == [0]


@pytest.mark.parametrize("entry", ["reader", "georef", "axis_compatibility"])
def test_active_imports_do_not_load_single_drawing_rules(entry):
    script = r'''
import importlib.abc
import sys
from types import ModuleType, SimpleNamespace

class RejectLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("cad2gis.legacy"):
            raise AssertionError("legacy rules imported: " + fullname)
        if fullname == "cad2gis.apd_rules" and sys.argv[1] != "axis_compatibility":
            raise AssertionError("old APD module imported")

sys.meta_path.insert(0, RejectLegacy())
if sys.argv[1] == "reader":
    from cad2gis.reader import autocad, resolver
    records = [{"cad_role": "model", "layer": "generic", "points": [(0, 0)]}]
    assert autocad.partition_model_legend(records) is records
elif sys.argv[1] == "georef":
    from cad2gis import native_runtime
    native_runtime.ensure_osgeo_runtime = lambda: {}
    osgeo = ModuleType("osgeo")
    osgeo.osr = SimpleNamespace()
    sys.modules["osgeo"] = osgeo
    from cad2gis.cad2gis_v3 import georef
    from cad2gis.coordinate_runtime import set_traditional_axis_order
    assert georef.set_traditional_axis_order is set_traditional_axis_order
else:
    from cad2gis.apd_rules import set_traditional_axis_order
    set_traditional_axis_order(object(), object())
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, entry],
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), os.environ.get("PYTHONPATH", "")))},
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
