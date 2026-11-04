from __future__ import annotations

import hashlib
import json

import pytest

from cad2gis.native_runtime import ensure_osgeo_runtime


def _run(tmp_path):
    ensure_osgeo_runtime()
    from osgeo import ogr, osr

    run = tmp_path / "run"
    run.mkdir()
    delivery = run / "delivery.gpkg"
    dataset = ogr.GetDriverByName("GPKG").CreateDataSource(str(delivery))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    for name, kind, wkt in (("PTECH", ogr.wkbPoint, "POINT (12543779.6304409 -821633.178768857)"),
                            ("CABLE", ogr.wkbLineString, "LINESTRING (0.125 0.5,10.25 3.75,20 7)")):
        layer = dataset.CreateLayer(name, srs, kind)
        layer.CreateField(ogr.FieldDefn("CODE", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("length_m", ogr.OFTReal))
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetField("CODE", f"{name}-1")
        feature.SetField("length_m", 12.3456789)
        feature.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        layer.CreateFeature(feature)
    dataset = None
    digest = hashlib.sha256(delivery.read_bytes()).hexdigest()
    (run / "run_manifest.json").write_text(json.dumps({
        "run_status": "CONDITIONAL",
        "artifacts": {"delivery": {"path": str(delivery), "sha256": digest}},
    }), encoding="utf-8")
    return run, delivery


def test_flatgeobuf_export_is_lossless_and_hash_bound(tmp_path):
    from cad2gis.delivery_export import export_delivery

    run, delivery = _run(tmp_path)
    from osgeo import ogr

    manifest = export_delivery(run, tmp_path / "out", "flatgeobuf")
    assert manifest["layer_feature_counts"] == {"CABLE": 1, "PTECH": 1}
    assert set(manifest["files"]) == {"CABLE.fgb", "PTECH.fgb"}
    assert json.loads((tmp_path / "out/export_manifest.json").read_text(encoding="utf-8"))["format"] == "flatgeobuf"
    source = ogr.Open(str(delivery))
    for name in ("PTECH", "CABLE"):
        exported = ogr.Open(str(tmp_path / "out" / f"{name}.fgb"))
        a = source.GetLayerByName(name).GetNextFeature()
        b = exported.GetLayer(0).GetNextFeature()
        assert a.GetGeometryRef().ExportToWkb() == b.GetGeometryRef().ExportToWkb()
        assert (a.GetField("CODE"), a.GetField("length_m")) == (b.GetField("CODE"), b.GetField("length_m"))


def test_export_refuses_tampered_inputs_and_unsafe_outputs(tmp_path):
    from cad2gis.delivery_export import export_delivery

    run, delivery = _run(tmp_path)
    with pytest.raises(ValueError, match="format must be"):
        export_delivery(run, tmp_path / "out", "parquet")
    with pytest.raises(ValueError, match="outside the immutable run"):
        export_delivery(run, run / "exports", "geojson")
    export_delivery(run, tmp_path / "out", "geojson")
    with pytest.raises(FileExistsError):
        export_delivery(run, tmp_path / "out", "geojson")
    delivery.write_bytes(delivery.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="does not match"):
        export_delivery(run, tmp_path / "again", "flatgeobuf")
