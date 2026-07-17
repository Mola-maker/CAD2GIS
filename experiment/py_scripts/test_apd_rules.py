"""Focused regressions for APD semantic and ingestion rules."""

import json
import re
import sqlite3
import sys
import types
import xml.etree.ElementTree as ET

from apd_rules import (
    classify_annotation_target, classify_insert_block, link_annotations,
    link_apd_annotations, set_traditional_axis_order,
)
from autocad_reader import (
    build_items_from_records,
    classify_layout_role,
    extract_com_entity,
    partition_plan_roles,
    partition_model_legend,
    _feature_item,
)
from schema_config import LAYER_PATTERN_MAP

try:
    import osgeo  # noqa: F401 - prefer the real bindings when the suite has GDAL
except ImportError:
    fake_osgeo = types.ModuleType("osgeo")
    fake_osgeo.ogr = types.SimpleNamespace()
    fake_osgeo.osr = types.SimpleNamespace()
    sys.modules["osgeo"] = fake_osgeo

import converter as converter_module
from converter import (
    _build_network_topology, _embed_qgis_styles, _qgis_style_qml,
    _validate_source_crs_evidence,
)


def test_apd_blocks_use_positive_evidence():
    assert classify_insert_block("*U7") == "SITE"
    assert classify_insert_block("*U11") == "BOITE"
    assert classify_insert_block("*U13") == "PTECH"
    assert classify_insert_block("FDT-01") == "SITE"
    assert classify_insert_block("FAT DISTRIBUTION") == "BOITE"
    assert classify_insert_block("FDT Info") is None
    assert classify_insert_block("ETIKET EMR-NEW 2026") is None
    assert classify_insert_block("anonymous decoration") is None


def test_annotation_linking_records_annotations_and_features():
    features = [
        {"centroid": (0.0, 0.0), "attrs": {}, "annotation_text": ""},
        {"centroid": (10.0, 0.0), "attrs": {}, "annotation_text": ""},
    ]
    annotations = [
        {"centroid": (0.1, 0.0), "attrs": {"CODE": "FAT-01"}, "text": "FAT-01"},
        {"centroid": (0.2, 0.0), "attrs": {"CODE": "FAT-02"}, "text": "FAT-02"},
        {"centroid": (10.1, 0.0), "attrs": {"CODE": "FDT-01"}, "text": "FDT-01"},
    ]
    leftovers = link_annotations(annotations, features, sigma=1.0)
    assert [item["text"] for item in leftovers] == ["FAT-02"]
    assert features[0]["attrs"]["CODE"] == "FAT-01"
    assert features[1]["attrs"]["CODE"] == "FDT-01"


def test_axis_order_helper_uses_available_gdal_strategy():
    class FakeReference:
        def __init__(self):
            self.strategy = None

        def SetAxisMappingStrategy(self, strategy):
            self.strategy = strategy

    class FakeOsr:
        OAMS_TRADITIONAL_GIS_ORDER = 7

    reference = FakeReference()
    set_traditional_axis_order(reference, FakeOsr)
    assert reference.strategy == 7


def test_false_positive_layers_have_no_feature_mapping():
    prohibited = ["Line", "SERVICE CORE", "EXPANSION CORE", "SLING WIRE"]
    for layer in prohibited:
        matches = [feature_class for pattern, feature_class, _ in LAYER_PATTERN_MAP if re.search(pattern, layer)]
        assert matches == []


def test_direct_com_reader_preserves_apd_anonymous_insert():
    class Entity:
        ObjectName = "AcDbBlockReference"
        EffectiveName = "*U7"
        InsertionPoint = (122.9, 0.62, 0.0)
        Handle = "A7"
        Layer = "FDT"
        Color = 1
        Linetype = "Continuous"
        Lineweight = 25
        Rotation = 0.5
        HasAttributes = False

    record = extract_com_entity(Entity(), "Model", "model")
    features = build_items_from_records(
        [record],
        "apd.dwg",
        lambda x, y: (x, y),
        lambda layer, text: ("fc_misc", None, 0.0, "unclassified"),
        lambda block_name: classify_insert_block(block_name) or "fc_misc",
        lambda text, feature_class: {},
    )

    spatial = [item for item in features if item.get("output_kind") == "feature"]
    assert len(spatial) == 1
    assert spatial[0]["fc_name"] == "SITE"
    assert spatial[0]["classification_method"] == "apd_block_family"
    assert spatial[0]["linetype"] == "Continuous"


def test_layout_roles_separate_topology_and_equipment_layouts():
    assert classify_layout_role("FDT-01 TOPOLOGY") == "topology"
    assert classify_layout_role("SPLICING FDT") == "topology"
    assert classify_layout_role("FDT LAYOUT") == "equipment_layout"
    assert classify_layout_role("FDT-ALL") == "plan"


def test_legend_and_title_regions_are_not_plan_features():
    records = [
        {"points": [(0, 0), (100, 100)], "centroid": (50, 50), "text": "", "cad_role": "plan"},
        {"points": [(88, 90)], "centroid": (88, 90), "text": "LEGEND", "cad_role": "plan"},
        {"points": [(88, 40)], "centroid": (88, 40), "text": "CABLE TYPE", "cad_role": "plan"},
        {"points": [(92, 80), (98, 80)], "centroid": (95, 80), "text": "", "cad_role": "plan"},
        {"points": [(92, 10), (98, 10)], "centroid": (95, 10), "text": "DRAWING NO", "cad_role": "plan"},
    ]
    partition_plan_roles(records)
    assert records[3]["cad_role"] == "style_legend"
    assert records[4]["cad_role"] == "title_block"


def test_legacy_topology_keeps_source_geometry_and_only_records_candidates():
    items = [
        {"output_kind": "feature", "fc_name": "SITE", "cad_handle": "10", "centroid": (0.0, 0.0), "native_centroid": (0.0, 0.0), "attrs": {}},
        {"output_kind": "feature", "fc_name": "BOITE", "cad_handle": "20", "centroid": (0.0001, 0.0), "native_centroid": (10.0, 0.0), "attrs": {}},
        {
            "output_kind": "feature", "fc_name": "CABLE", "cad_handle": "30",
            "centroid": (0.00005, 0.0), "points": [(0.00001, 0.0), (0.00009, 0.0)],
            "native_points": [(1.0, 0.0), (9.0, 0.0)],
            "layer": "FIBER DISTRIBUTION", "text": "", "annotation_text": "", "attrs": {},
        },
    ]
    stats = _build_network_topology(items, snap_tolerance_metres=20.0)
    cable = items[2]
    assert stats["nodes"] == 2
    assert stats["cables"] == 1
    assert stats["resolved_cables"] == 0
    assert stats["dimension_promoted_cables"] == 0
    assert "ORIGINE" not in cable["attrs"]
    assert "EXTREMITE" not in cable["attrs"]
    assert cable["attrs"]["TYPE_CABLE"] == "DISTRIBUTION"
    assert cable["points"] == [(0.00001, 0.0), (0.00009, 0.0)]
    assert {item["status"] for item in json.loads(cable["topology_displacements"])} == {"candidate"}


def test_span_dimension_never_promotes_or_joins_source_routes():
    supports = [
        {
            "output_kind": "feature", "fc_name": "PTECH", "cad_handle": f"P{index}",
            "centroid": (index / 1000.0, 0.0), "native_centroid": (index * 10.0, 0.0),
            "points": [(index / 1000.0, 0.0)], "native_points": [(index * 10.0, 0.0)],
            "layer": "NEW POLE 7-3", "block_name": "*U13", "text": "",
            "annotation_text": "", "attrs": {},
        }
        for index in range(4)
    ]
    routes = [
        {
            "output_kind": "feature", "fc_name": "CABLE", "cad_handle": handle,
            "centroid": ((start + end) / 2000.0, 0.0),
            "points": [(start / 1000.0, 0.0), (end / 1000.0, 0.0)],
            "native_points": [(start * 10.0, 0.0), (end * 10.0, 0.0)],
            "layer": "APD - Cable Line A (FO Cable 24C_2T) - AE",
            "text": "", "annotation_text": "", "attrs": {}, "aci_color": 3,
        }
        for handle, start, end in (("R1", 0, 1), ("R2", 2, 3))
    ]
    dimensions = [
        {
            "output_kind": "source_evidence", "dwg_type_name": "DIMENSION",
            "layer": "SPAN CABLE", "cad_role": "model", "layout": "Model",
            "handle": "D1", "entity_key": "dimension-D1", "source_file": "apd.dwg",
            "native_points": [(10.0, 0.0), (20.0, 0.0)], "dimension_value": 10.0,
        },
        {
            "output_kind": "source_evidence", "dwg_type_name": "DIMENSION",
            "layer": "SPAN CABLE", "cad_role": "model", "layout": "Model",
            "handle": "D2", "entity_key": "dimension-D2", "source_file": "apd.dwg",
            "native_points": [(0.0, 0.0), (10.0, 0.0)], "dimension_value": 10.0,
        },
    ]
    items = supports + routes + dimensions

    stats = _build_network_topology(items)
    bridges = [
        item for item in items
        if item.get("fc_name") == "CABLE" and item.get("geometry_role") == "TOPOLOGY_BRIDGE"
    ]

    assert stats["route_components_before"] == 2
    assert stats["route_components_after"] == 2
    assert stats["dimension_promoted_cables"] == 0
    assert bridges == []
    assert [route["points"] for route in routes] == [
        [(0.0, 0.0), (0.001, 0.0)],
        [(0.002, 0.0), (0.003, 0.0)],
    ]


def test_qgis_style_contains_cad_categories_and_labels():
    qml = _qgis_style_qml("CABLE", "LineString", [("#FF0000", "#FF0000", "DASHED", 25)])
    root = ET.fromstring(qml)
    assert root.find("./renderer-v2/categories/category").get("value") == "#FF0000|DASHED|25"
    symbol_layer = root.find("./renderer-v2/symbols/symbol/layer")
    assert symbol_layer.get("class") == "SimpleLine"
    text_style = root.find("./labeling/settings/text-style")
    assert text_style.get("isExpression") == "1"
    assert text_style.get("fieldName") == '"display_label"'


def test_qgis_styles_are_embedded_as_defaults(tmp_path):
    path = tmp_path / "styles.gpkg"
    sqlite3.connect(path).close()
    _embed_qgis_styles(path, [{
        "output_kind": "feature", "fc_name": "CABLE", "true_color": "#FF0000",
        "aci_color": 1, "linetype": "DASHED", "lineweight": 25,
    }])
    with sqlite3.connect(path) as connection:
        count, default_count = connection.execute(
            "SELECT COUNT(*), SUM(useAsDefault) FROM layer_styles"
        ).fetchone()
    assert count == 8
    assert default_count == 8


def test_auto_crs_detects_apd_web_mercator_coordinates():
    original_auto = converter_module._AUTO_SOURCE_CRS
    original_detected = converter_module._DETECTED_SOURCE_CRS
    try:
        converter_module._AUTO_SOURCE_CRS = True
        converter_module._DETECTED_SOURCE_CRS = None
        lon, lat = converter_module._reproject_point(13684000.0, 69000.0)
        assert 122.0 < lon < 124.0
        assert 0.0 < lat < 1.0
        assert converter_module._DETECTED_SOURCE_CRS == "EPSG:3857"
    finally:
        converter_module._AUTO_SOURCE_CRS = original_auto
        converter_module._DETECTED_SOURCE_CRS = original_detected


def test_zpm_requires_a_real_closed_ring():
    base = {
        "object_name": "ACDBLWPOLYLINE", "layer": "FAT AREA FDT 1", "text": "",
        "block_attributes": {}, "block_name": "", "layout": "Model", "cad_role": "model",
        "handle": "AA", "dwg_type_name": "LWPOLYLINE", "aci_color": 3,
        "true_color": "", "linetype": "Continuous", "lineweight": 25, "rotation": 0.0,
        "centroid": (0.5, 0.5), "closed": False,
    }
    assign = lambda layer, text: ("ZPM", "Polygon", 0.9, "tier1_layer_pattern")
    closed = dict(base, points=[(0, 0), (1, 0), (1, 1), (0, 0)])
    open_line = dict(base, points=[(0, 0), (1, 0)])
    feature = _feature_item(closed, "apd.dwg", lambda x, y: (x, y), assign, lambda n: None, lambda t, f: {})
    assert feature["wkt"].startswith("POLYGON")
    assert _feature_item(open_line, "apd.dwg", lambda x, y: (x, y), assign, lambda n: None, lambda t, f: {}) is None


def test_apd_label_family_prevents_pole_label_on_fat():
    assert classify_annotation_target("MR.DMPH.P057") == "PTECH"
    features = [
        {"fc_name": "BOITE", "native_centroid": (0.0, 0.0), "centroid": (0, 0), "attrs": {}, "annotation_text": ""},
        {"fc_name": "PTECH", "native_centroid": (5.0, 0.0), "centroid": (5, 0), "attrs": {}, "annotation_text": ""},
    ]
    leftovers = link_apd_annotations([
        {"text": "MR.DMPH.P057", "native_centroid": (0.1, 0.0), "centroid": (0.1, 0), "attrs": {"CODE": "DMPH.P057"}},
    ], features)
    assert leftovers == []
    assert features[0].get("display_label") is None
    assert features[1]["display_label"] == "MR.DMPH.P057"


def test_isolated_model_cluster_becomes_legend_evidence():
    records = [
        {"object_name": "ACDBBLOCKREFERENCE", "points": [(float(i), 0.0)], "centroid": (float(i), 0.0), "cad_role": "model"}
        for i in range(30)
    ] + [
        {"object_name": "ACDBBLOCKREFERENCE", "points": [(1000.0 + i, 0.0)], "centroid": (1000.0 + i, 0.0), "cad_role": "model"}
        for i in range(8)
    ]
    partition_model_legend(records)
    assert all(record["cad_role"] == "style_legend" for record in records[-8:])
    assert all(record["cad_role"] == "model" for record in records[:-8])


def test_source_crs_must_match_dwg_cgeocs():
    items = [{
        "output_kind": "source_evidence", "dwg_type_name": "DOCUMENT_METADATA",
        "text": "CGEOCS=WGS84.PseudoMercator;INSUNITS=6",
    }]
    _validate_source_crs_evidence(items, "EPSG:3857")
    try:
        _validate_source_crs_evidence(items, "EPSG:4326")
    except RuntimeError as exc:
        assert "expected EPSG:3857" in str(exc)
    else:
        raise AssertionError("mismatched source CRS must fail closed")
