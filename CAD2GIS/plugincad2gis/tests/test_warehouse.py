"""GeoPackage warehouse tests (story G10) — standardized入库 round-trips with schema + metadata.

Builds a tiny FeatureCollection, writes a GeoPackage, and reads it back to confirm: one layer per
class, published-schema fields present, provenance carried, metadata tables written, embedded QML
styles, and the G9 transform applied to geometry. Requires geopandas; skipped otherwise.
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("geopandas")
pytest.importorskip("shapely")

from shapely.geometry import LineString, Point  # noqa: E402

from cad2gis.gcp import GCP, fit_transform  # noqa: E402
from cad2gis.model import Feature, FeatureCollection, SourceRef  # noqa: E402
from cad2gis.warehouse import qml_for, write_geopackage  # noqa: E402


def _collection():
    coll = FeatureCollection(crs="local-engineering", source_file="t.dxf")
    coll.add(Feature(Point(10, 20), "manhole",
                     {"facility": "manhole", "discipline": "comms", "block": "末端井"},
                     SourceRef(file="t.dxf", layer="通信", block="末端井", handle="A1", entity_type="INSERT")))
    coll.add(Feature(LineString([(0, 0), (5, 0)]), "cable",
                     {"facility": "cable", "discipline": "comms"},
                     SourceRef(file="t.dxf", layer="通信", handle="A2", entity_type="LWPOLYLINE")))
    coll.add(Feature(Point(3, 3), "duct",
                     {"facility": "duct", "discipline": "comms", "block": "gc170"},
                     SourceRef(file="t.dxf", layer="ZBTZ", block="gc170", handle="A3", entity_type="INSERT")))
    return coll


def test_geopackage_roundtrip_layers_and_schema(tmp_path):
    import geopandas as gpd

    coll = _collection()
    out = str(tmp_path / "out.gpkg")
    rep = write_geopackage(coll, out, manifest={"stage": "test"}, qc={"n": 3})

    assert rep.layers_written == {"manhole": 1, "cable": 1, "duct": 1}
    mh = gpd.read_file(out, layer="manhole")
    assert len(mh) == 1
    # published-schema fields + provenance present
    for col in ("facility", "discipline", "src_layer", "src_block", "src_handle", "confidence"):
        assert col in mh.columns
    assert mh.iloc[0]["src_block"] == "末端井"       # CJK provenance preserved (UTF-8)
    assert mh.iloc[0]["src_layer"] == "通信"
    # attribute completeness on required fields is full
    assert rep.attribute_completeness["manhole"] == 1.0


def test_metadata_tables_and_styles_written(tmp_path):
    coll = _collection()
    out = str(tmp_path / "out.gpkg")
    gcps = [GCP(0, 0, 100, 200), GCP(5, 0, 105, 200), GCP(0, 5, 100, 205), GCP(5, 5, 105, 205)]
    fit = fit_transform(gcps, dst_crs="local-grid")
    rep = write_geopackage(coll, out, transform=fit, manifest={"stage": "test"},
                           qc={"n_manholes": 1}, source_path=str(tmp_path / "out.gpkg"))

    assert "cad2gis_manifest" in rep.metadata_tables
    assert "cad2gis_transform" in rep.metadata_tables
    con = sqlite3.connect(out)
    try:
        man = dict(con.execute("SELECT key, value FROM cad2gis_manifest").fetchall())
        assert man["rule_version"]
        assert man["n_features"] == "3"
        tr = dict(con.execute("SELECT key, value FROM cad2gis_transform").fetchall())
        assert tr["model"] in ("similarity", "affine")
        # embedded QML styles present for shipped classes
        styles = con.execute("SELECT f_table_name FROM layer_styles").fetchall()
        styled = {s[0] for s in styles}
        assert "manhole" in styled and "cable" in styled
    finally:
        con.close()


def test_transform_georeferences_geometry(tmp_path):
    import geopandas as gpd

    coll = _collection()
    out = str(tmp_path / "out.gpkg")
    # translate-only transform: X+100, Y+200 (fit from 4 corners)
    gcps = [GCP(0, 0, 100, 200), GCP(5, 0, 105, 200), GCP(0, 5, 100, 205), GCP(5, 5, 105, 205)]
    fit = fit_transform(gcps)
    write_geopackage(coll, out, transform=fit)
    mh = gpd.read_file(out, layer="manhole")
    # manhole was at (10,20) -> (110, 220) after +100/+200
    assert abs(mh.geometry.iloc[0].x - 110) < 1e-3
    assert abs(mh.geometry.iloc[0].y - 220) < 1e-3


def test_shipped_styles_exist():
    for cls in ("manhole", "cable", "duct", "annotation", "control_point"):
        assert qml_for(cls) is not None, f"missing shipped style for {cls}"
