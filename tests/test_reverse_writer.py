from pathlib import Path

import ezdxf
import pytest
import shapely
from shapely.geometry import (
    GeometryCollection, LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon,
)

from cad2gis.reverse_models import GISFeature, ReverseConversionError
from cad2gis.reverse_writer import write_dxf


def feature(geometry, *, name="roads", fid="1", **properties):
    return GISFeature(feature_id=fid, layer=name, geometry=geometry, properties=properties)


def export(tmp_path: Path, features, **options):
    destination = tmp_path / "export.dxf"
    report = write_dxf(features, destination, units=6, **options)
    document = ezdxf.readfile(destination)
    assert not document.audit().errors
    return document, report


def test_points_and_lines_keep_dimensions_and_source_handles(tmp_path):
    document, report = export(tmp_path, [
        feature(Point(10, 20, 30), fid="p"),
        feature(LineString([(0, 0), (2, 3), (4, 5)]), fid="xy"),
        feature(LineString([(1, 2, 3), (4, 5, 6), (7, 8, 9)]), fid="xyz"),
    ])
    assert document.dxfversion == "AC1032"
    assert document.units == 6
    point, xy, xyz = list(document.modelspace())
    assert tuple(point.dxf.location) == (10, 20, 30)
    assert xy.dxftype() == "LWPOLYLINE"
    assert list(xy.get_points("xy")) == [(0, 0), (2, 3), (4, 5)]
    assert xyz.is_3d_polyline
    assert [tuple(vertex.dxf.location) for vertex in xyz.vertices] == [(1, 2, 3), (4, 5, 6), (7, 8, 9)]
    assert report["entity_count"] == report["geometry_entity_count"] == 3
    assert report["label_count"] == 0
    assert [record["feature_id"] for record in report["feature_entities"]] == ["p", "xy", "xyz"]
    assert {item["handle"] for record in report["feature_entities"] for item in record["entities"]} == {entity.dxf.handle for entity in document.modelspace()}


@pytest.mark.parametrize("z", [None, 17])
def test_polygon_exports_closed_outer_and_hole_boundaries(tmp_path, z):
    def coords(points):
        return points if z is None else [(x, y, z) for x, y in points]
    polygon = Polygon(coords([(0, 0), (10, 0), (10, 10), (0, 10)]),
                      [coords([(2, 2), (2, 4), (4, 4), (4, 2)])])
    document, report = export(tmp_path, [feature(polygon)])
    outer, hole = list(document.modelspace())
    assert report["geometry_entity_count"] == 2
    for boundary in (outer, hole):
        assert boundary.is_closed if z is not None else boundary.closed
        if z is not None:
            assert boundary.is_3d_polyline
            assert all(vertex.dxf.location.z == z for vertex in boundary.vertices)
            assert len(boundary.vertices) == 4
        else:
            assert boundary.dxftype() == "LWPOLYLINE"
            assert len(boundary) == 4
    if z is None:
        assert list(hole.get_points("xy")) == [(2, 2), (2, 4), (4, 4), (4, 2)]


def test_multipart_and_nested_collection(tmp_path):
    geometry = GeometryCollection([
        MultiPoint([(1, 2), (3, 4)]),
        GeometryCollection([MultiLineString([[(0, 0), (1, 1)], [(2, 2), (3, 3)]])]),
        MultiPolygon([Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])]),
    ])
    document, report = export(tmp_path, [feature(geometry)])
    assert report["geometry_entity_count"] == 5
    assert len(document.modelspace().query("POINT")) == 2
    assert len(document.modelspace().query("LWPOLYLINE")) == 3


def test_labels_are_opt_in_literal_mtext_with_line_midpoint(tmp_path):
    text = "中文 {literal} \\C1;red\\Ptest\nnext %%d ^I"
    line = LineString([(0, 0, 2), (10, 0, 6)])
    _, no_labels = export(tmp_path, [feature(line, title=text)])
    assert no_labels["label_count"] == 0
    document, report = export(tmp_path, [feature(line, title=text)], label_field="title", label_height=3)
    label = document.modelspace().query("MTEXT")[0]
    assert label.text == "中文 \\{literal\\} \\\\C1;red\\\\Ptest\\Pnext %{}%{}d ^ I"
    assert label.plain_text(fast=False) == text
    assert tuple(label.dxf.insert) == (5, 0, 4)
    assert label.dxf.char_height == 3
    assert report["label_count"] == 1
    assert report["feature_entities"][0]["entities"][-1]["role"] == "label"


def test_label_repeated_percent_codes_and_fields_remain_literal(tmp_path):
    text = "%%%d %<\\AcVar;something>% %%p %%%%c"
    document, _ = export(tmp_path, [feature(Point(0, 0), title=text)], label_field="title")
    label = document.modelspace().query("MTEXT")[0]
    assert label.plain_text(fast=False) == text
    assert "%%" not in label.text
    assert "%<" not in label.text


def test_polygon_label_anchor_is_inside_and_null_labels_are_skipped(tmp_path):
    polygon = Polygon([(0, 0), (8, 0), (8, 8), (0, 8)],
                      [[(2, 2), (2, 6), (6, 6), (6, 2)]])
    document, report = export(tmp_path, [feature(polygon, title=42), feature(Point(10, 10), title=None)],
                              label_field="title")
    label = document.modelspace().query("MTEXT")[0]
    assert label.plain_text() == "42"
    assert polygon.contains(Point(label.dxf.insert.x, label.dxf.insert.y))
    assert report["label_count"] == 1


def test_unicode_layer_names_collisions_reserved_names_and_determinism(tmp_path):
    names = ["a/b", "a:b", "A_B", "地形", "0", "Defpoints", "defPOINTS", "x" * 300]
    features = [feature(Point(index, 0), name=name, fid=str(index)) for index, name in enumerate(names)]
    document, report = export(tmp_path, features)
    _, reversed_report = export(tmp_path, features[::-1])
    assert report["layer_map"] == reversed_report["layer_map"]
    layers = report["layer_map"]
    assert len({name.casefold() for name in layers.values()}) == len(names)
    assert not {"0", "defpoints"}.intersection(name.casefold() for name in layers.values())
    assert layers["地形"] == "地形"
    assert all(len(name) <= 255 and name in document.layers for name in layers.values())


def test_surrounding_whitespace_preserves_distinct_source_layer_identity(tmp_path):
    document, report = export(tmp_path, [
        feature(Point(1, 2), name=" A ", fid="padded"),
        feature(Point(3, 4), name="A", fid="plain"),
    ])
    layers = report["layer_map"]
    assert set(layers) == {" A ", "A"}
    assert layers[" A "].casefold() != layers["A"].casefold()
    padded, plain = list(document.modelspace())
    assert padded.dxf.layer == layers[" A "]
    assert plain.dxf.layer == layers["A"]
    assert tuple(padded.dxf.location) == (1, 2, 0)
    assert tuple(plain.dxf.location) == (3, 4, 0)
    assert [record["input_layer"] for record in report["feature_entities"]] == [" A ", "A"]


def test_layer_field_and_supported_style_attributes(tmp_path):
    document, report = export(tmp_path, [feature(
        Point(0, 0), target="测量:点", style_aci=3, style_truecolor=0x123456, style_lineweight=25,
        style_linetype="UNKNOWN_PATTERN",
    )], layer_field="target")
    entity = list(document.modelspace())[0]
    assert entity.dxf.layer == report["layer_map"]["测量:点"]
    assert entity.dxf.color == 3
    assert entity.dxf.true_color == 0x123456
    assert entity.dxf.lineweight == 25
    assert entity.dxf.linetype == "BYLAYER"


@pytest.mark.parametrize("geometry,match", [
    (None, "missing or empty"),
    (Point(), "missing or empty"),
    (Point(float("inf"), 0), "non-finite"),
    (LineString([(0, 0, 1), (1, 1, float("nan"))]), "non-finite"),
    (Polygon([(0, 0), (2, 2), (2, 0), (0, 2)]), "invalid geometry"),
    (shapely.from_wkt("POINT M (1 2 3)"), "M coordinates"),
    (shapely.from_wkt("POINT ZM (1 2 3 4)"), "M coordinates"),
    (GeometryCollection([Point(), Point(1, 2)]), "empty geometry part"),
])
def test_invalid_geometry_fails_without_replacing_destination(tmp_path, geometry, match):
    destination = tmp_path / "protected.dxf"
    destination.write_text("existing result")
    with pytest.raises(ReverseConversionError, match=match):
        write_dxf([feature(geometry)], destination, units=6)
    assert destination.read_text() == "existing result"


@pytest.mark.parametrize("options,match", [
    ({"label_field": "missing"}, "no label field"),
    ({"layer_field": "missing"}, "no layer field"),
    ({"label_height": 0}, "Label height"),
])
def test_invalid_configuration_is_actionable(tmp_path, options, match):
    with pytest.raises(ReverseConversionError, match=match):
        write_dxf([feature(Point(1, 2))], tmp_path / "bad.dxf", units=6, **options)


def test_output_io_error_has_stable_code(tmp_path):
    with pytest.raises(ReverseConversionError) as caught:
        write_dxf([feature(Point(0, 0))], tmp_path / "missing" / "bad.dxf", units=6)
    assert caught.value.code == "REVERSE_OUTPUT_FAILED"
