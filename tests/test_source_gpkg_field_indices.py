"""Guard field binding, scalar fidelity and rollback without timing thresholds."""
from __future__ import annotations

import sqlite3

import pytest

from cad2gis.cad2gis_v3 import source_gpkg
from cad2gis.cad2gis_v3.model import SourceEntity


def entities():
    common = {
        "source_sha256": "d" * 64, "source_file": "field-binding.dwg",
        "layout": "Model", "layout_role": "model", "cad_role": "model",
        "layer": "SOURCE", "raw_properties": {"extraction_backend": "fixture"},
    }
    return [
        SourceEntity.from_record({
            **common, "entity_key": "line", "handle": "A1", "dwg_type_name": "LINE",
            "object_name": "AcDbLine", "points": [[0.0, 0.0], [3.0, 4.0]],
            "native_length": 5.0, "dimension_value": 12.5, "scale_x": 1.25,
            "aci_color": 3, "text": "管线 A-01", "owner_handle": "OWNER-1",
        }),
        SourceEntity.from_record({
            **common, "entity_key": "point", "handle": "B2", "dwg_type_name": "POINT",
            "object_name": "AcDbPoint", "points": [[10.0, 20.0]],
            "dimension_value": None, "native_length": None, "text": "",
        }),
    ]


def test_source_writer_avoids_name_scans_and_preserves_values(tmp_path, monkeypatch):
    def reject_name_scan(*_args):
        raise AssertionError("Per-feature Python field-name scan regressed")

    monkeypatch.setattr(source_gpkg.ogr.Feature, "_getfieldindex", reject_name_scan)
    output = tmp_path / "source.gpkg"
    result = source_gpkg.write_source_gpkg(output, entities(), None)
    assert result.entity_count == 2
    with sqlite3.connect(output) as db:
        line = db.execute(
            "SELECT entity_key, cad_handle, dimension_value, native_length, scale_x, "
            "closed, text, aci_color, owner_handle, typeof(dimension_value), typeof(closed) "
            "FROM source_lines"
        ).fetchone()
        assert line == ("line", "A1", 12.5, 5.0, 1.25, 0, "管线 A-01", 3, "OWNER-1", "real", "integer")
        point = db.execute(
            "SELECT entity_key, dimension_value, native_length, text, "
            "typeof(dimension_value), typeof(native_length) FROM source_points"
        ).fetchone()
        assert point == ("point", None, None, "", "null", "null")
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_field_indices_follow_actual_definition_order():
    dataset = source_gpkg.ogr.GetDriverByName("Memory").CreateDataSource("field-order")
    try:
        layer = dataset.CreateLayer("source_points", geom_type=source_gpkg.ogr.wkbNone)
        fields = list(reversed(source_gpkg._ENTITY_FIELDS))
        for name, field_type in fields:
            assert layer.CreateField(source_gpkg.ogr.FieldDefn(name, field_type)) == 0
        indices = source_gpkg._bind_field_indices(layer.GetLayerDefn(), "source_points")
        assert indices == {name: index for index, (name, _field_type) in enumerate(fields)}
    finally:
        dataset.Close()


@pytest.mark.parametrize("mismatch", ["missing", "case_changed", "wrong_type"])
def test_field_binding_rejects_schema_mismatches(mismatch):
    dataset = source_gpkg.ogr.GetDriverByName("Memory").CreateDataSource("field-mismatch")
    try:
        layer = dataset.CreateLayer("source_points", geom_type=source_gpkg.ogr.wkbNone)
        for name, field_type in source_gpkg._ENTITY_FIELDS:
            if name == "text":
                if mismatch == "missing":
                    continue
                if mismatch == "case_changed":
                    name = "TEXT"
                if mismatch == "wrong_type":
                    field_type = source_gpkg.ogr.OFTInteger
            assert layer.CreateField(source_gpkg.ogr.FieldDefn(name, field_type)) == 0
        with pytest.raises(RuntimeError, match="source_points.text"):
            source_gpkg._bind_field_indices(layer.GetLayerDefn(), "source_points")
    finally:
        dataset.Close()


def test_binding_failure_keeps_existing_output_and_closes_transaction(tmp_path, monkeypatch):
    create_layer = source_gpkg._create_layer

    def omit_field(dataset, name, spatial_reference):
        declared = source_gpkg._ENTITY_FIELDS
        try:
            if name == "source_points":
                source_gpkg._ENTITY_FIELDS = tuple(field for field in declared if field[0] != "text")
            return create_layer(dataset, name, spatial_reference)
        finally:
            source_gpkg._ENTITY_FIELDS = declared

    monkeypatch.setattr(source_gpkg, "_create_layer", omit_field)
    output = tmp_path / "source.gpkg"
    output.write_bytes(b"existing artifact")
    with pytest.raises(RuntimeError, match="source_points.text"):
        source_gpkg.write_source_gpkg(output, entities(), None)
    assert output.read_bytes() == b"existing artifact"
    # An open GDAL/SQLite handle leaves staging files behind on Windows.
    assert list(tmp_path.iterdir()) == [output]
