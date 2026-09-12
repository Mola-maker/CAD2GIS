from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from pathlib import Path

import pytest
from pyproj import CRS
from shapely import to_wkb
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

from cad2gis.reverse_models import ReverseConversionError
from cad2gis.reverse_reader import read_gis


def _geojson(tmp_path: Path, features=None, **fields) -> Path:
    path = tmp_path / "source.geojson"
    if features is None:
        features = [{"type": "Feature", "id": "one", "properties": {"label": "道路"}, "geometry": {"type": "Point", "coordinates": [100, 20]}}]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features, **fields}), encoding="utf-8")
    return path


def _blob(geometry, *, flags=1, srs_id=3857, envelope=(), byte_order="<"):
    return b"GP\x00" + bytes([flags]) + struct.pack(byte_order + "i", srs_id) + struct.pack(byte_order + "d" * len(envelope), *envelope) + to_wkb(geometry, flavor="iso")


def _gpkg(tmp_path: Path, *, rows=None, layer="roads", declared="GEOMETRY", z=0, m=0, srs_id=3857, extra=False) -> Path:
    path = tmp_path / "测试 # % source.gpkg"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY, data_type TEXT, srs_id INTEGER);
        CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT, srs_id INTEGER, z INTEGER, m INTEGER);
        CREATE TABLE gpkg_spatial_ref_sys (srs_id INTEGER PRIMARY KEY, organization TEXT, organization_coordsys_id INTEGER, definition TEXT);
    """)
    quoted = '"' + layer.replace('"', '""') + '"'
    connection.execute(f"CREATE TABLE {quoted} (fid INTEGER PRIMARY KEY, label TEXT, attributes BLOB, geom BLOB)")
    connection.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?, 'EPSG', ?, ?)", (srs_id, srs_id, CRS.from_epsg(3857).to_wkt()))
    connection.execute("INSERT INTO gpkg_contents VALUES (?, 'features', ?)", (layer, srs_id))
    connection.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, ?, ?)", (layer, declared, srs_id, z, m))
    if rows is None:
        rows = [(9, "后", b"\x00\xff", _blob(Point(3, 4), srs_id=srs_id)), (2, "前", None, _blob(LineString([(0, 0), (2, 2)]), srs_id=srs_id))]
    connection.executemany(f"INSERT INTO {quoted} VALUES (?, ?, ?, ?)", rows)
    if extra:
        connection.executescript("""
            CREATE TABLE empty_layer (fid INTEGER PRIMARY KEY, geom BLOB);
            INSERT INTO gpkg_contents VALUES ('empty_layer', 'features', 3857);
            INSERT INTO gpkg_geometry_columns VALUES ('empty_layer', 'geom', 'POINT', 3857, 0, 0);
        """)
    connection.commit()
    connection.close()
    return path


def test_geojson_default_crs_attributes_and_layer_name(tmp_path):
    source = _geojson(tmp_path, name="测量")
    result = read_gis(source)
    assert result.layer_crs == {"测量": "OGC:CRS84"}
    assert result.features[0].feature_id == "one"
    assert result.features[0].properties == {"label": "道路"}
    assert result.features[0].geometry.equals(Point(100, 20))
    assert result.metadata["feature_count"] == 1
    assert result.metadata["empty_geometry_count"] == 0


def test_geojson_holes_and_z_preserved(tmp_path):
    geometry = {"type": "Polygon", "coordinates": [
        [[0, 0, 7], [10, 0, 7], [10, 10, 7], [0, 10, 7], [0, 0, 7]],
        [[2, 2, 7], [2, 4, 7], [4, 4, 7], [4, 2, 7], [2, 2, 7]],
    ]}
    source = _geojson(tmp_path, [{"type": "Feature", "properties": {"nested": {"a": [1, None]}}, "geometry": geometry}])
    feature = read_gis(source).features[0]
    assert feature.feature_id == "feature-1"
    assert feature.geometry.area == 96
    assert len(feature.geometry.interiors) == 1
    assert feature.geometry.exterior.coords[0][2] == 7
    assert feature.properties["nested"] == {"a": [1, None]}


def test_geojson_feature_and_legacy_crs(tmp_path):
    source = tmp_path / "single.json"
    source.write_text(json.dumps({"type": "Feature", "crs": {"type": "name", "properties": {"name": "EPSG:3857"}}, "geometry": {"type": "Point", "coordinates": [500000, 4000000]}, "properties": None}), encoding="utf-8")
    result = read_gis(source, layers=["single"])
    assert result.layer_crs == {"single": "EPSG:3857"}
    assert result.features[0].properties == {}


@pytest.mark.parametrize("crs", [None, {"type": "link"}, {"type": "name", "properties": {"name": "nonsense"}}])
def test_geojson_rejects_unresolved_crs(tmp_path, crs):
    with pytest.raises(ReverseConversionError, match="CRS"):
        read_gis(_geojson(tmp_path, crs=crs))


@pytest.mark.parametrize("geometry, message", [
    (None, "null geometry"),
    ({"type": "Point", "coordinates": []}, "empty"),
    ({"type": "Point", "coordinates": [1, 2, 3, 4]}, "measured/4D"),
    ({"type": "Point", "coordinates": [True, 2]}, "numbers"),
    ({"type": "Point", "coordinates": [1e999, 2]}, "nonfinite"),
    ({"type": "CircularString", "coordinates": [[0, 0], [1, 1]]}, "unsupported"),
    ({"type": ["Point"], "coordinates": [1, 2]}, "unsupported"),
    ({"type": "Point", "coordinates": [1, 2], "crs": {"type": "name", "properties": {"name": "EPSG:3857"}}}, "per-geometry CRS"),
    ({"type": "LineString", "coordinates": [[1, 2], [3, 4, 5]]}, "mixed coordinate"),
    ({"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]]}, "invalid geometry"),
    ({"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [0, 2]]]}, "must be closed"),
    ({"type": "GeometryCollection", "geometries": []}, "empty"),
    ({"type": "GeometryCollection", "geometries": [{"type": "LineString", "coordinates": []}]}, "part 1"),
])
def test_geojson_rejects_unpreservable_features(tmp_path, geometry, message):
    source = _geojson(tmp_path, [{"type": "Feature", "properties": {}, "geometry": geometry}])
    with pytest.raises(ReverseConversionError, match=message):
        read_gis(source)


def test_geojson_rejects_overflow_json_properties(tmp_path):
    source = _geojson(tmp_path)
    source.write_text(source.read_text(encoding="utf-8").replace('"label": "\\u9053\\u8def"', '"value": 1e999'), encoding="utf-8")
    with pytest.raises(ReverseConversionError, match="properties contain nonfinite"):
        read_gis(source)


def test_geojson_rejects_duplicate_object_members(tmp_path):
    source = _geojson(tmp_path)
    source.write_text('{"type":"FeatureCollection","features":[],"features":[]}', encoding="utf-8")
    with pytest.raises(ReverseConversionError, match="duplicate member"):
        read_gis(source)


@pytest.mark.parametrize("ids", [["same", "same"], [1, "1"], [None, "feature-1"]])
def test_geojson_rejects_duplicate_ids(tmp_path, ids):
    records = []
    for identifier in ids:
        feature = {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}}
        if identifier is not None:
            feature["id"] = identifier
        records.append(feature)
    with pytest.raises(ReverseConversionError, match="duplicate feature id"):
        read_gis(_geojson(tmp_path, records))


def test_empty_geojson_layer_is_retained(tmp_path):
    result = read_gis(_geojson(tmp_path, []))
    assert result.features == []
    assert result.layer_crs == {"source": "OGC:CRS84"}


@pytest.mark.parametrize("selection", [["missing"], [], ["source", "source"], "source"])
def test_geojson_layer_selection_errors(tmp_path, selection):
    with pytest.raises(ReverseConversionError):
        read_gis(_geojson(tmp_path), layers=selection)


def test_geopackage_order_blob_properties_and_source_unchanged(tmp_path):
    source = _gpkg(tmp_path)
    before = (hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns, set(tmp_path.iterdir()))
    result = read_gis(source)
    after = (hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns, set(tmp_path.iterdir()))
    assert after == before
    assert result.layer_crs == {"roads": "EPSG:3857"}
    assert [feature.feature_id for feature in result.features] == ["2", "9"]
    assert result.features[1].properties == {"fid": 9, "label": "后", "attributes": {"encoding": "base64", "data": "AP8="}}
    assert result.metadata["source_type"] == "geopackage"
    json.dumps(result.features[1].properties, allow_nan=False)


def test_geopackage_connection_is_read_only_and_transactional(tmp_path, monkeypatch):
    source = _gpkg(tmp_path)
    original = sqlite3.connect
    calls = []

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            calls.append(sql)
            if sql == "BEGIN":
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    super().execute("CREATE TABLE unwanted (value INTEGER)")
            return super().execute(sql, *args, **kwargs)

    def connect(database, **kwargs):
        assert database.endswith("?mode=ro")
        assert "%23" in database and "%25" in database
        assert kwargs["uri"] is True
        return original(database, factory=Connection, **kwargs)

    monkeypatch.setattr("cad2gis.reverse_reader.sqlite3.connect", connect)
    assert len(read_gis(source).features) == 2
    assert "BEGIN" in calls


def test_geopackage_selection_keeps_empty_layers(tmp_path):
    source = _gpkg(tmp_path, extra=True)
    result = read_gis(source, layers=["empty_layer"])
    assert result.features == []
    assert result.layer_crs == {"empty_layer": "EPSG:3857"}
    assert result.metadata["available_layers"] == ["empty_layer", "roads"]
    with pytest.raises(ReverseConversionError, match="do not exist"):
        read_gis(source, layers=["absent"])


def test_geopackage_quoted_layer_name(tmp_path):
    layer = 'roads " ; DROP TABLE gpkg_contents; --'
    assert len(read_gis(_gpkg(tmp_path, layer=layer), layers=[layer]).features) == 2


def test_geopackage_generated_properties_preserved(tmp_path):
    source = _gpkg(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE roads ADD COLUMN doubled INTEGER GENERATED ALWAYS AS (fid * 2) VIRTUAL")
    assert read_gis(source).features[0].properties["doubled"] == 4


def test_geopackage_hole_z_and_big_endian_header(tmp_path):
    polygon = Polygon([(0, 0, 8), (10, 0, 8), (10, 10, 8), (0, 10, 8)], [[(2, 2, 8), (2, 4, 8), (4, 4, 8), (4, 2, 8)]])
    value = _blob(polygon, flags=4, envelope=(0, 10, 0, 10, 8, 8), byte_order=">")
    source = _gpkg(tmp_path, rows=[(1, "area", None, value)], declared="POLYGON", z=1)
    geometry = read_gis(source).features[0].geometry
    assert geometry.area == 96
    assert geometry.exterior.coords[0][2] == 8
    assert len(geometry.interiors) == 1


@pytest.mark.parametrize("srs_id", [-1, 0])
def test_geopackage_undefined_crs(tmp_path, srs_id):
    assert read_gis(_gpkg(tmp_path, srs_id=srs_id)).layer_crs == {"roads": None}


def test_geopackage_custom_wkt_crs(tmp_path):
    source = _gpkg(tmp_path, srs_id=99999)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE gpkg_spatial_ref_sys SET organization = 'CUSTOM'")
    assert CRS.from_user_input(read_gis(source).layer_crs["roads"]).equals(CRS.from_epsg(3857))


def test_geopackage_wkt2_extension_overrides_legacy_authority_and_wkt1(tmp_path):
    source = _gpkg(tmp_path)
    expected = CRS.from_epsg(32650)
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE gpkg_spatial_ref_sys ADD COLUMN definition_12_063 TEXT")
        connection.execute("UPDATE gpkg_spatial_ref_sys SET definition_12_063 = ?", (expected.to_wkt(version="WKT2_2019"),))
    actual = CRS.from_user_input(read_gis(source).layer_crs["roads"])
    assert actual.equals(expected)
    assert not actual.equals(CRS.from_epsg(3857))


@pytest.mark.parametrize("wkt2", [None, "", "   ", "undefined", " UNDEFINED "])
@pytest.mark.parametrize("organization", ["EPSG", "CUSTOM"])
def test_geopackage_undefined_wkt2_extension_falls_back(tmp_path, wkt2, organization):
    source = _gpkg(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE gpkg_spatial_ref_sys ADD COLUMN definition_12_063 TEXT")
        connection.execute("UPDATE gpkg_spatial_ref_sys SET definition_12_063 = ?, organization = ?", (wkt2, organization))
    assert CRS.from_user_input(read_gis(source).layer_crs["roads"]).equals(CRS.from_epsg(3857))


def test_geopackage_malformed_defined_wkt2_does_not_fall_back(tmp_path):
    source = _gpkg(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE gpkg_spatial_ref_sys ADD COLUMN definition_12_063 TEXT")
        connection.execute("UPDATE gpkg_spatial_ref_sys SET definition_12_063 = 'not a valid CRS'")
    with pytest.raises(ReverseConversionError, match="definition_12_063.*invalid or unsupported CRS"):
        read_gis(source)


@pytest.mark.parametrize("mutation, message", [
    (lambda blob: b"XX" + blob[2:], "header"),
    (lambda blob: blob[:2] + b"\x01" + blob[3:], "version"),
    (lambda blob: blob[:3] + b"\x21" + blob[4:], "flags"),
    (lambda blob: blob[:3] + b"\x81" + blob[4:], "flags"),
    (lambda blob: blob[:3] + b"\x0b" + blob[4:], "flags"),
    (lambda blob: blob[:3] + b"\x11" + blob[4:], "empty"),
    (lambda blob: blob[:3] + b"\x07" + blob[4:], "measured"),
    (lambda blob: blob[:4] + struct.pack("<i", 4326) + blob[8:], "differs from layer SRS"),
    (lambda blob: blob[:3] + b"\x03" + blob[4:8], "truncated"),
    (lambda blob: blob + b"trailing", "trailing bytes"),
    (lambda blob: blob[:-1], "truncated"),
    (lambda blob: blob[:8] + b"\x02" + blob[9:], "byte order"),
    (lambda blob: blob[:9] + struct.pack("<I", 8) + blob[13:], "unsupported WKB"),
    (lambda blob: blob[:9] + struct.pack("<I", 2001) + blob[13:] + struct.pack("<d", 7), "measured"),
])
def test_geopackage_rejects_invalid_headers_and_wkb(tmp_path, mutation, message):
    source = _gpkg(tmp_path, rows=[(3, "bad", None, mutation(_blob(Point(1, 2))))])
    with pytest.raises(ReverseConversionError, match=message) as error:
        read_gis(source)
    assert "roads" in str(error.value) and "'3'" in str(error.value)


@pytest.mark.parametrize("value, message", [
    (None, "null geometry"),
    (_blob(Point()), "nonfinite or empty"),
    (_blob(LineString()), "empty or malformed WKB"),
    (_blob(GeometryCollection([Point(1, 2), LineString()])), "empty or malformed WKB"),
    (_blob(Polygon([(0, 0), (2, 2), (0, 2), (2, 0)])), "invalid geometry"),
    (_blob(Point(1, 2), flags=3, envelope=(3, 2, 0, 2)), "invalid.*envelope"),
    (_blob(Point(1, 2), flags=3, envelope=(0, float("nan"), 0, 2)), "invalid.*envelope"),
])
def test_geopackage_rejects_null_empty_invalid_geometries(tmp_path, value, message):
    source = _gpkg(tmp_path, rows=[(1, "bad", None, value)])
    with pytest.raises(ReverseConversionError, match=message):
        read_gis(source)


@pytest.mark.parametrize("declared,z,m,geometry,message", [
    ("POLYGON", 0, 0, Point(1, 2), "declared geometry type"),
    ("POINT", 0, 0, Point(1, 2, 3), "Z dimension"),
    ("POINT", 1, 0, Point(1, 2), "Z dimension"),
    ("POINT", 0, 1, Point(1, 2), "measured"),
])
def test_geopackage_enforces_geometry_declaration(tmp_path, declared, z, m, geometry, message):
    source = _gpkg(tmp_path, declared=declared, z=z, m=m, rows=[(1, "bad", None, _blob(geometry))])
    with pytest.raises(ReverseConversionError, match=message):
        read_gis(source)


@pytest.mark.parametrize("statement,message", [
    ("UPDATE gpkg_contents SET srs_id = 4326", "inconsistent SRS"),
    ("DELETE FROM gpkg_spatial_ref_sys", "missing from"),
    ("UPDATE gpkg_geometry_columns SET column_name = 'missing'", "column does not exist"),
    ("INSERT INTO gpkg_geometry_columns SELECT * FROM gpkg_geometry_columns", "exactly one"),
])
def test_geopackage_rejects_inconsistent_metadata(tmp_path, statement, message):
    source = _gpkg(tmp_path)
    with sqlite3.connect(source) as connection:
        connection.execute(statement)
    with pytest.raises(ReverseConversionError, match=message):
        read_gis(source)


def test_source_errors_are_actionable(tmp_path):
    with pytest.raises(ReverseConversionError, match="does not exist"):
        read_gis(tmp_path / "missing.geojson")
    source = tmp_path / "broken.gpkg"
    source.write_bytes(b"not sqlite")
    with pytest.raises(ReverseConversionError, match="Cannot read GeoPackage"):
        read_gis(source)
    source = tmp_path / "not_supported.shp"
    source.write_bytes(b"not supported")
    with pytest.raises(ReverseConversionError, match="Unsupported GIS source format"):
        read_gis(source)
