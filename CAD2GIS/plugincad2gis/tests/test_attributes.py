"""Structured attribute extraction tests (story G11c) — duct specs + point IDs parse to fields."""
from __future__ import annotations

from cad2gis.attributes import parse_duct_spec, parse_point_id, enrich_feature
from cad2gis.model import Feature, SourceRef


def test_parse_duct_spec_holes_material_diameter():
    d = parse_duct_spec("3孔PVC110")
    assert d["holes"] == 3 and d["material"] == "PVC" and d["diameter_mm"] == 110
    assert d["spec"] == "3孔PVC110"


def test_parse_duct_spec_bd_and_dn():
    assert parse_duct_spec("12孔BD100")["holes"] == 12
    dn = parse_duct_spec("DN400-L=19.37m-i=18‰")
    assert dn["dn_mm"] == 400 and dn["run_length_m"] == 19.37 and dn["slope_permille"] == 18.0


def test_parse_duct_spec_empty_on_junk():
    assert parse_duct_spec("地砖") == {}
    assert parse_duct_spec(None) == {}


def test_parse_point_id():
    assert parse_point_id("WC-108") == {"point_id": "WC-108"}
    assert parse_point_id("T131") == {"point_id": "T131"}
    assert parse_point_id("DX027") == {"point_id": "DX027"}
    assert parse_point_id("random text") == {}


def test_enrich_feature_duct_from_evidence():
    from shapely.geometry import Point

    f = Feature(Point(0, 0), "duct",
                {"facility": "duct", "_map_evidence": {"matched_label": "6孔PVC110"}},
                SourceRef(entity_type="INSERT"))
    n = enrich_feature(f)
    assert n >= 3
    assert f.attributes["holes"] == 6 and f.attributes["material"] == "PVC" and f.attributes["diameter_mm"] == 110
