"""Reader coordinates, analytic geometry and source display must agree."""
from __future__ import annotations

import math
from copy import deepcopy

import ezdxf
import pytest
from shapely.geometry import LineString

from cad2gis.cad2gis_v3.curve_geometry import (
    CableGeometryMaterializationError,
    _bulge_segment,
    _policy,
    materialize_cable_features,
    source_display_points,
)
from cad2gis.cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis.cad2gis_v3.ports import resolve_insert_affine
from cad2gis.reader.libredwg_cli import _record


def source_entity(document, entity):
    from pathlib import Path
    return SourceEntity.from_record(_record(
        document=document, entity=entity, source=Path("coordinate-fixture.dwg"),
        source_sha256="a" * 64, layout="Model", layout_role="model", cad_role="model",
    ))


def feature_for(source):
    return Feature(
        "route:" + source.handle, "CABLE", "LineString", list(source.points),
        source.entity_key, source.handle, source.layer, "SOURCE_ROUTE", CadStyle(),
    )


@pytest.mark.parametrize("kind", ["LWPOLYLINE", "POLYLINE"])
def test_inverted_polyline_converts_ocs_and_preserves_native_facts(kind):
    document = ezdxf.new()
    msp = document.modelspace()
    if kind == "LWPOLYLINE":
        entity = msp.add_lwpolyline([(2, 3, 0.5), (12, 3, 0)], format="xyb",
            dxfattribs={"elevation": 7, "extrusion": (0, 0, -1)})
    else:
        entity = msp.add_polyline2d([(2, 3), (12, 3)],
            dxfattribs={"elevation": (0, 0, 7), "extrusion": (0, 0, -1)})
        entity.vertices[0].dxf.bulge = 0.5
    source = source_entity(document, entity)
    assert source.points == ((-2.0, 3.0), (-12.0, 3.0))
    assert source.curve_facts["vertices_wcs"] == [[-2, 3, -7], [-12, 3, -7]]
    assert source.curve_facts["normal"] == [0, 0, -1]
    assert source.curve_facts["bulges"] == [0.5, 0]
    assert source.native_length == pytest.approx(25 * math.atan(0.5))
    before = deepcopy(source.curve_facts)
    feature = feature_for(source)
    materialize_cable_features([source], [feature], strict=True)
    path = feature.attributes["curve_materialization"]["delivery_native_points"]
    # The original OCS arc is below its chord. Reflection changes X, not Y.
    assert min(p[1] for p in path) == pytest.approx(0.5, abs=0.01)
    assert max(p[1] for p in path) <= 3.0
    assert source.curve_facts == before
    assert source_display_points(source) == tuple(tuple(p) for p in path)


def test_tilted_elevation_is_not_lost_or_mislabelled_as_horizontal():
    document = ezdxf.new()
    entity = document.modelspace().add_lwpolyline([(2, 3), (12, 5)],
        dxfattribs={"elevation": 7, "extrusion": (0, 1, 1)})
    source = source_entity(document, entity)
    expected = list(entity.vertices_in_wcs())
    for actual, wanted in zip(source.curve_facts["vertices_wcs"], expected):
        assert actual == pytest.approx(tuple(wanted))
    assert source.points[0] == pytest.approx((-2, 4 / math.sqrt(2)))
    assert source.native_length == pytest.approx(math.sqrt(104))
    with pytest.raises(CableGeometryMaterializationError, match="non-planar"):
        materialize_cable_features([source], [feature_for(source)], strict=True)
    with pytest.raises(ValueError, match="non-planar|tilted"):
        source_display_points(source)


def test_line_keeps_real_z_and_true_3d_length():
    document = ezdxf.new()
    source = source_entity(document, document.modelspace().add_line((1, 2, 3), (4, 6, 15)))
    assert source.curve_facts["vertices_wcs"] == [[1, 2, 3], [4, 6, 15]]
    assert source.native_length == pytest.approx(13)
    with pytest.raises(CableGeometryMaterializationError, match="non-planar"):
        materialize_cable_features([source], [feature_for(source)], strict=True)


def test_legacy_2d_polyline_flags_allow_plain_open_routes():
    document = ezdxf.new()
    source = source_entity(document, document.modelspace().add_polyline2d([(0, 0), (3, 4)]))
    assert source.curve_facts["primitive_parameters"]["flags"] == 0
    feature = feature_for(source)
    materialize_cable_features([source], [feature], strict=True)
    assert feature.attributes["curve_materialization"]["source_segment_native_length_sum"] == 5


def test_inverted_insert_agrees_with_ezdxf_matrix_including_base_point():
    document = ezdxf.new()
    document.blocks.new("SYMBOL", base_point=(3, 4, 0))
    entity = document.modelspace().add_blockref("SYMBOL", (20, 30, 7), dxfattribs={
        "extrusion": (0, 0, -1), "rotation": 35, "xscale": 2, "yscale": 3,
    })
    source = source_entity(document, entity)
    assert source.points == ((-20, 30),)
    assert source.raw_properties["transform_facts"]["insertion_point"] == [-20, 30, -7]
    assert source.raw_properties["transform_facts"]["insertion_point_ocs"] == [20, 30, 7]
    affine, diagnostics = resolve_insert_affine(source)
    assert affine is not None, diagnostics
    for point in [(3, 4), (4, 8), (-2, 1)]:
        expected = entity.matrix44().transform((*point, 0))
        assert affine.apply(point) == pytest.approx((expected.x, expected.y))


@pytest.mark.parametrize("kind", ["TEXT", "ATTRIB", "ATTDEF"])
def test_text_anchors_are_wcs_even_with_inverted_ocs(kind):
    document = ezdxf.new()
    attributes = {"insert": (2, 3, 7), "extrusion": (0, 0, -1), "rotation": 30, "text": "label"}
    if kind != "TEXT":
        attributes["tag"] = "LABEL"
    entity = document.modelspace().new_entity(kind, attributes)
    source = source_entity(document, entity)
    assert source.points == ((-2, 3),)
    assert source.style.rotation == pytest.approx(math.radians(150))
    assert "glyph_orientation_requires_review" in source.raw_properties["unsupported_reason"]


@pytest.mark.parametrize("normal", [(0, 0, 1), (0, 0, -1)])
def test_arc_wraparound_display_matches_ezdxf_geometry(normal):
    document = ezdxf.new()
    entity = document.modelspace().add_arc((10, 20, 7), 8, 330, 30,
        dxfattribs={"extrusion": normal})
    source = source_entity(document, entity)
    path = source_display_points(source)
    expected = [tuple(point)[:2] for point in entity.flattening(0.001)]
    assert LineString(path).hausdorff_distance(LineString(expected)) <= 0.011
    assert source.native_length == pytest.approx(8 * math.pi / 3)
    assert source.curve_facts["vertices_wcs"][0][2] == 7 * normal[2]
    materialize_cable_features([source], [feature_for(source)], strict=True)


def test_circle_display_keeps_original_centre_but_draws_a_closed_ring():
    document = ezdxf.new()
    source = source_entity(document, document.modelspace().add_circle((10, 20, 7), 8,
        dxfattribs={"extrusion": (0, 0, -1)}))
    assert source.points == ((-10, 20),)
    assert source.closed
    path = source_display_points(source)
    assert len(path) > 16 and path[0] == path[-1]
    assert all(math.dist(point, (-10, 20)) == pytest.approx(8) for point in path)
    assert source.curve_facts["vertices_wcs"] == [[-10, 20, -7]]


def test_full_revolution_arc_is_not_reduced_to_zero_length():
    document = ezdxf.new()
    source = source_entity(document, document.modelspace().add_arc((10, 20, 7), 8, 0, 360))
    assert source.native_length == pytest.approx(16 * math.pi)
    assert source.closed and source.dwg_type == "ARC"
    assert source.curve_facts["primitive_parameters"]["end_angle_degrees"] == 360
    path = source_display_points(source)
    assert len(path) > 16 and path[0] == path[-1]
    assert all(math.dist(point, (10, 20)) == pytest.approx(8) for point in path)


def test_open_trailing_bulge_is_preserved_without_creating_a_segment():
    document = ezdxf.new()
    source = source_entity(document, document.modelspace().add_lwpolyline(
        [(0, 0, 0), (3, 4, 1)], format="xyb"))
    feature = feature_for(source)
    materialize_cable_features([source], [feature], strict=True)
    payload = feature.attributes["curve_materialization"]
    assert payload["source_segment_count"] == 1
    assert payload["source_segment_native_length_sum"] == 5
    assert payload["ignored_open_trailing_bulge"] == 1
    assert source.curve_facts["bulges"] == [0, 1]


def test_shallow_arc_uses_geometric_error_instead_of_dimensionless_bulge_threshold():
    # A tiny bulge can still be visually significant on a long source chord.
    policy = _policy({"max_sagitta_native": 0.001, "max_chord_native": 1e9})
    points, length, kind = _bulge_segment((0, 0), (1e9, 0), 1e-10, policy)
    assert kind == "bulge_arc" and len(points) > 2
    assert min(point[1] for point in points) == pytest.approx(-0.05, abs=0.001)
    assert length == pytest.approx(1e9)
    assert points[0] == (0, 0) and points[-1] == (1e9, 0)


def complete_arc_source(*, shifted=False):
    offset = 1 if shifted else 0
    return SourceEntity.from_record({
        "entity_key": "complete-arc", "handle": "A", "dwg_type_name": "ARC",
        "points": [(0, 0), (10, 0)], "native_length": 5 * math.pi,
        "curve_facts": {
            "schema_version": "cad2gis-curve-facts-v1", "coordinate_system": "WCS",
            "primitive_type": "ARC", "vertices_wcs": [[0, 0, 0], [10, 0, 0]],
            "bulges": [1, 0], "closed": False, "native_length": 5 * math.pi,
            "native_length_source": "analytic semicircle", "primitive_parameters": {
                "delivery_segments_wcs": [{
                    "source_segment_index": 0, "source_segment_kind": "arc",
                    "points_wcs": [[offset, 0, 0], [5 + offset, -5, 0], [10 + offset, 0, 0]],
                    "native_length": 5 * math.pi, "native_length_source": "analytic semicircle",
                }],
            },
        },
    })


def test_complete_reader_curve_materializes_without_uninitialized_audit_state():
    source = complete_arc_source()
    feature = feature_for(source)
    materialize_cable_features([source], [feature], strict=True)
    payload = feature.attributes["curve_materialization"]
    assert payload["materialization_method"] == "reader-complete-curve-facts"
    assert payload["skipped_zero_length_segments"] == []
    assert payload["source_segment_native_length_sum"] == pytest.approx(5 * math.pi)


def test_complete_reader_curve_cannot_pass_closure_at_another_location():
    source = complete_arc_source(shifted=True)
    with pytest.raises(CableGeometryMaterializationError, match="endpoints"):
        materialize_cable_features([source], [feature_for(source)], strict=True)
    with pytest.raises(ValueError, match="endpoints"):
        source_display_points(source)
