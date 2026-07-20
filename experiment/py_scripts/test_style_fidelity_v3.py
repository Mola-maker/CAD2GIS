"""QGIS rendering must preserve the visible CAD vertex chain."""

from __future__ import annotations

import json
import sqlite3
from xml.etree import ElementTree as ET

import pytest

from cad2gis_v3.georef import DirectTransformer
from cad2gis_v3.model import CadStyle, Feature
from cad2gis_v3.semantics import CoverageGateError
from cad2gis_v3.styles import write_styles
from cad2gis_v3.warehouse import LAYER_ORDER, write_delivery


def test_default_qgis_styles_disable_geometry_simplification(tmp_path):
    manifest_path = write_styles(tmp_path / "styles", [])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["geometry_simplification"] == "disabled_for_source_fidelity"
    assert all(
        layer["geometry_simplification"] == "disabled_for_source_fidelity"
        for layer in manifest["layers"].values()
    )

    assert set(manifest["layers"]) == set(LAYER_ORDER)
    for layer in manifest["layers"].values():
        qml_path = manifest_path.parent / layer["qml"]
        root = ET.fromstring(qml_path.read_text(encoding="utf-8"))
        assert root.attrib["simplifyDrawingHints"] == "0"
        text_style = root.find(".//text-style")
        assert text_style is not None
        expected_field = (
            "length_label"
            if layer["qml"] == "CABLE_SEGMENT.qml"
            else "display_label"
        )
        assert text_style.attrib["fieldName"] == expected_field
        assert layer["label_field"] == expected_field
        placement = root.find(".//placement")
        assert placement is not None
        if layer["qml"] in {"CABLE.qml", "CABLE_SEGMENT.qml", "INFRASTRUCTURE.qml"}:
            assert placement.attrib["placement"] == "2"
            assert root.find(".//dd_properties") is None
        else:
            assert placement.attrib["placement"] == "0"
            assert root.find(".//dd_properties") is not None


def test_embedded_default_styles_disable_geometry_simplification(tmp_path):
    delivery = tmp_path / "delivery.gpkg"
    write_delivery(
        delivery, [], DirectTransformer("EPSG:3857", "EPSG:9481"),
    )
    write_styles(tmp_path / "styles", [], delivery)

    with sqlite3.connect(delivery) as connection:
        rows = connection.execute(
            "SELECT f_table_name, styleQML, useAsDefault FROM layer_styles"
        ).fetchall()
    assert len(rows) == len(LAYER_ORDER)
    assert {row[0] for row in rows} == set(LAYER_ORDER)
    assert all(row[2] == 1 for row in rows)
    assert all(
        ET.fromstring(row[1]).attrib["simplifyDrawingHints"] == "0"
        for row in rows
    )
    segment_qml = next(row[1] for row in rows if row[0] == "CABLE_SEGMENT")
    segment_root = ET.fromstring(segment_qml)
    assert segment_root.find(".//text-style").attrib["fieldName"] == "length_label"
    assert segment_root.find(".//placement").attrib["placement"] == "2"
    assert segment_root.find(".//dd_properties") is None


def _feature(style):
    return Feature(
        feature_key="feature",
        feature_class="CABLE",
        geometry_kind="LineString",
        native_points=[(0.0, 0.0), (1.0, 0.0)],
        source_entity_key="entity",
        source_handle="10",
        source_layer="VENDOR ROUTE",
        geometry_role="SOURCE_ROUTE",
        style=style,
    )


def test_unknown_style_facts_are_visible_and_never_silently_solid(tmp_path):
    feature = _feature(CadStyle(
        aci_color=256,
        true_color="NOT-A-COLOR",
        linetype="VENDOR_COMPLEX_PATTERN",
        lineweight=999,
    ))
    manifest_path = write_styles(
        tmp_path / "styles", [feature], coverage_policy="abstain",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "cad2gis-qgis-style-manifest-v3"
    assert manifest["coverage"]["status"] == "WATCH"
    assert manifest["coverage"]["by_reason"] == {
        "invalid_true_color": 1,
        "unsupported_linetype": 1,
        "unsupported_lineweight": 1,
    }
    assert manifest["unsupported_records"] == manifest["coverage"]["records"]
    assert all(
        set((
            "source_entity_key", "reason", "candidate_class", "source_layer",
            "dwg_type", "action", "allowlisted",
        )).issubset(record)
        for record in manifest["unsupported_records"]
    )

    qml = ET.fromstring((manifest_path.parent / "CABLE.qml").read_text(encoding="utf-8"))
    options = {
        option.attrib.get("name"): option.attrib.get("value")
        for option in qml.findall(".//Option")
        if option.attrib.get("name")
    }
    assert options["line_style"] == "dash dot dot"
    assert options["line_color"] == "255,0,255,255"


def test_style_policy_fail_raises_auditable_gate_payload(tmp_path):
    feature = _feature(CadStyle(
        aci_color=256, linetype="ByLayer", lineweight=-1,
    ))

    with pytest.raises(CoverageGateError) as captured:
        write_styles(tmp_path / "styles", [feature], coverage_policy="fail")

    assert captured.value.domain == "styles"
    assert captured.value.coverage["status"] == "FAIL"
    assert captured.value.coverage["by_reason"] == {
        "unresolved_aci_color": 1,
        "unresolved_linetype": 1,
    }
    assert not (tmp_path / "styles").exists()
