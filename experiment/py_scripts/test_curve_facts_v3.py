"""Contracts for the versioned, loss-aware native CAD curve boundary."""

from __future__ import annotations

import json
import math
import sqlite3
from types import SimpleNamespace

import pytest

from autocad_reader import _AUTOLISP_EXTRACTOR, _record_from_bulk_row, extract_com_entity
from cad2gis_v3.curve_geometry import (
    CableGeometryMaterializationError,
    delivery_segments,
    materialize_cable_features,
    validate_cable_geometry_materialization,
)
from cad2gis_v3.evidence import write_evidence
from cad2gis_v3.georef import DirectTransformer
from cad2gis_v3.ingest import ingest
from cad2gis_v3.model import (
    CadStyle,
    CURVE_FACTS_SCHEMA,
    Feature,
    SourceEntity,
    canonical_curve_facts,
    canonical_curve_fingerprint,
)
from cad2gis_v3.pipeline import _enforce_geometry_policy


def _bulk_curve_row(*, kind="LWPOLYLINE", points="100,200;110,210"):
    columns = [
        kind, "C1", "CABLE", "Model", "256", "-1", "ByLayer", "-1",
        "0", "0", "", "", "", points, "0", "0", "0",
        "7", "-1", "Continuous", "25", "1", "1", "1",
        "OWNER", "", "", "", "", "14.25",
        CURVE_FACTS_SCHEMA,
        "100,200,7;110,210,7",
        "0.5,0",
        "7",
        "0,0,1",
        "0,0,1",
        kind,
        '{"constant_width":0.25,"source":"DXF"}',
    ]
    assert len(columns) == 38
    return columns


def _with_identity(record, key="ENTITY-C1"):
    result = dict(record)
    result.update(
        entity_key=key,
        source_sha256="source-sha",
        source_file="source.dwg",
    )
    return result


def _cable_registry():
    return SimpleNamespace(
        policy={
            "source_geometry_immutable": True,
            "crossing_is_connection": False,
            "support_is_optical_node": False,
            "force_route_components_connected": False,
            "generic_line_is_cable": False,
            "dimension_is_cable_geometry": False,
        },
        positive_route_layer_regex=r"^CABLE$",
    )


def _assert_unreadable_cable_fails_closed(record, reason):
    assert record is not None
    assert record["curve_facts"] == {}
    assert record["curve_facts_status"] == "unreadable"
    assert reason in record["raw_properties"]["unsupported_reasons"]
    source = SourceEntity.from_record(_with_identity(record, "ENTITY-UNREADABLE"))
    route = Feature(
        feature_key="CABLE-UNREADABLE",
        feature_class="CABLE",
        geometry_kind="LINESTRING",
        native_points=list(source.points),
        source_entity_key=source.entity_key,
        source_handle=source.handle,
        source_layer=source.layer,
        geometry_role="SOURCE_ROUTE",
        style=source.style,
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )
    with pytest.raises(RuntimeError, match="missing canonical source curve facts"):
        _enforce_geometry_policy(
            [source], [route], [], _cable_registry(),
            {"synthetic_route_vertices": 0}, require_curve_facts=True,
        )


def test_canonical_curve_fingerprint_is_order_independent_and_shape_bound():
    facts = {
        "schema_version": CURVE_FACTS_SCHEMA,
        "coordinate_system": "WCS",
        "primitive_type": "LWPOLYLINE",
        "vertices_wcs": [[1, 2, 3], [4, 5, 6]],
        "bulges": [0.25, 0],
        "elevation": 3,
        "normal": [0, 0, 1],
        "extrusion": [0, 0, 1],
        "closed": False,
        "primitive_parameters": {"width": 2, "flags": {"b": 2, "a": 1}},
        "native_length": 8.5,
        "native_length_source": "autocad_curve_distance",
    }
    reordered = dict(reversed(list(facts.items())))
    reordered["primitive_parameters"] = {"flags": {"a": 1, "b": 2}, "width": 2}

    fingerprint = canonical_curve_fingerprint(facts)
    assert len(fingerprint) == 64
    assert fingerprint == canonical_curve_fingerprint(reordered)
    assert canonical_curve_facts({**facts, "bulges": None})["bulges"] == [0.0, 0.0]

    with pytest.raises(ValueError, match="one value per WCS vertex"):
        canonical_curve_facts({**facts, "bulges": [0.25]})
    with pytest.raises(ValueError, match="non-finite"):
        canonical_curve_facts({
            **facts,
            "primitive_parameters": {"radius": float("nan")},
        })


def test_extended_bulk_curve_protocol_preserves_3d_facts_without_changing_2d_points():
    record = _record_from_bulk_row(_bulk_curve_row())

    assert record["points"] == [(100.0, 200.0), (110.0, 210.0)]
    assert record["curve_facts"] == {
        "schema_version": CURVE_FACTS_SCHEMA,
        "coordinate_system": "WCS",
        "primitive_type": "LWPOLYLINE",
        "vertices_wcs": [[100.0, 200.0, 7.0], [110.0, 210.0, 7.0]],
        "bulges": [0.5, 0.0],
        "elevation": 7.0,
        "normal": [0.0, 0.0, 1.0],
        "extrusion": [0.0, 0.0, 1.0],
        "closed": False,
        "primitive_parameters": {"constant_width": 0.25, "source": "DXF"},
        "native_length": 14.25,
        "native_length_source": "autocad_curve_distance",
    }
    assert record["curve_fingerprint"] == canonical_curve_fingerprint(
        record["curve_facts"]
    )
    assert record["raw_properties"]["curve_facts"] == record["curve_facts"]
    assert record["raw_properties"]["curve_fingerprint"] == record["curve_fingerprint"]
    assert "(list point normal 0)" in _AUTOLISP_EXTRACTOR
    assert "cad2gis-curve-facts-v1" in _AUTOLISP_EXTRACTOR
    assert '((= kind "POLYLINE")' in _AUTOLISP_EXTRACTOR
    assert '"flags=" (itoa (c2g-get 70 data 0))' in _AUTOLISP_EXTRACTOR


def test_com_lwpolyline_uses_real_active_x_name_and_xy_stride():
    class Polyline:
        # AcadLWPolyline.ObjectName is AcDbPolyline in AutoCAD ActiveX.
        ObjectName = "AcDbPolyline"
        Coordinates = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        Elevation = 7.0
        Normal = (0.0, 0.0, 1.0)
        Closed = True
        Length = 9.75
        Handle = "C2"

        @staticmethod
        def GetBulge(index):
            return (0.125, 0.0, 0.0)[index]

    record = extract_com_entity(Polyline(), "Model", "model")

    assert record["points"] == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    facts = record["curve_facts"]
    assert facts["vertices_wcs"] == [
        [1.0, 2.0, 7.0], [3.0, 4.0, 7.0], [5.0, 6.0, 7.0]
    ]
    assert facts["bulges"] == [0.125, 0.0, 0.0]
    assert facts["elevation"] == pytest.approx(7.0)
    assert facts["normal"] == [0.0, 0.0, 1.0]
    assert facts["closed"] is True
    assert facts["native_length"] == pytest.approx(9.75)


def test_com_cable_malformed_coordinates_are_unreadable_and_fail_closed():
    class Polyline:
        ObjectName = "AcDbPolyline"
        Coordinates = (1.0, 2.0, 3.0)  # odd XY cardinality
        Elevation = 7.0
        Normal = (0.0, 0.0, 1.0)
        # Production APD routes use a descriptive layer name rather than the
        # canonical output class name; the reader must still take the strict
        # COM curve path without importing the classifier.
        Layer = "APD - Cable Line A (FO Cable 24C_2T) - AE"
        Length = 5.0
        Handle = "BAD-COORDS"

        @staticmethod
        def GetBulge(_index):
            return 0.0

    record = extract_com_entity(Polyline(), "Model", "model")
    _assert_unreadable_cable_fails_closed(
        record, "curve_coordinates_stride_or_cardinality_invalid_in_com_backend"
    )


def test_com_cable_bulge_read_failure_is_unreadable_and_fail_closed():
    class Polyline:
        ObjectName = "AcDbPolyline"
        Coordinates = (1.0, 2.0, 3.0, 4.0)
        Elevation = 7.0
        Normal = (0.0, 0.0, 1.0)
        Layer = "CABLE"
        Length = 5.0
        Handle = "BAD-BULGE"

        @staticmethod
        def GetBulge(_index):
            raise RuntimeError("COM GetBulge failed")

    record = extract_com_entity(Polyline(), "Model", "model")
    _assert_unreadable_cable_fails_closed(
        record, "curve_bulges_unreadable_in_com_backend"
    )


@pytest.mark.parametrize(
    ("property_name", "reason"),
    [
        ("Normal", "curve_normal_unreadable_in_com_backend"),
        ("Elevation", "curve_elevation_unreadable_in_com_backend"),
    ],
)
def test_com_cable_normal_or_elevation_read_failure_is_unreadable_and_fail_closed(
    property_name, reason,
):
    class Polyline:
        ObjectName = "AcDbPolyline"
        Coordinates = (1.0, 2.0, 3.0, 4.0)
        Layer = "CABLE"
        Length = 5.0
        Handle = "BAD-CURVE-PROPERTY"

        @staticmethod
        def GetBulge(_index):
            return 0.0

        @property
        def Normal(self):
            if property_name == "Normal":
                raise RuntimeError("COM Normal failed")
            return (0.0, 0.0, 1.0)

        @property
        def Elevation(self):
            if property_name == "Elevation":
                raise RuntimeError("COM Elevation failed")
            return 7.0

    record = extract_com_entity(Polyline(), "Model", "model")
    _assert_unreadable_cable_fails_closed(record, reason)


def test_com_nondefault_extrusion_is_preserved_and_rejected_by_2d_cable_gate():
    class Polyline:
        ObjectName = "AcDbPolyline"
        Coordinates = (1.0, 2.0, 4.0, 5.0)
        Elevation = 3.0
        Normal = (0.0, 1.0, 0.0)
        Layer = "CABLE"
        Closed = False
        Length = 5.0
        Handle = "OCS1"

        @staticmethod
        def GetBulge(_index):
            return 0.0

    record = extract_com_entity(Polyline(), "Model", "model")
    assert record["points"] == [(1.0, 2.0), (4.0, 5.0)]
    assert record["curve_facts"]["vertices_wcs"] == [
        [-1.0, 3.0, 2.0], [-4.0, 3.0, 5.0]
    ]

    source = SourceEntity.from_record(_with_identity(record, "ENTITY-OCS1"))
    route = Feature(
        feature_key="CABLE-OCS1",
        feature_class="CABLE",
        geometry_kind="LINESTRING",
        native_points=list(source.points),
        source_entity_key=source.entity_key,
        source_handle=source.handle,
        source_layer=source.layer,
        geometry_role="SOURCE_ROUTE",
        style=source.style,
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )
    registry = SimpleNamespace(
        policy={
            "source_geometry_immutable": True,
            "crossing_is_connection": False,
            "support_is_optical_node": False,
            "force_route_components_connected": False,
            "generic_line_is_cable": False,
            "dimension_is_cable_geometry": False,
        },
        positive_route_layer_regex=r"^CABLE$",
    )

    with pytest.raises(RuntimeError, match="differ from ordered WCS curve facts"):
        _enforce_geometry_policy(
            [source], [route], [], registry,
            {"synthetic_route_vertices": 0},
            require_curve_facts=True,
        )


def test_com_3d_polyline_is_rejected_by_2d_cable_delivery_contract():
    class Polyline3D:
        ObjectName = "AcDb3dPolyline"
        Coordinates = (1.0, 2.0, 7.0, 4.0, 5.0, 9.0)
        Layer = "CABLE"
        Closed = False
        Length = 5.0
        Handle = "3D1"

    record = extract_com_entity(Polyline3D(), "Model", "model")
    source = SourceEntity.from_record(_with_identity(record, "ENTITY-3D1"))
    route = Feature(
        feature_key="CABLE-3D1",
        feature_class="CABLE",
        geometry_kind="LINESTRING",
        native_points=list(source.points),
        source_entity_key=source.entity_key,
        source_handle=source.handle,
        source_layer=source.layer,
        geometry_role="SOURCE_ROUTE",
        style=source.style,
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )
    registry = SimpleNamespace(
        policy={
            "source_geometry_immutable": True,
            "crossing_is_connection": False,
            "support_is_optical_node": False,
            "force_route_components_connected": False,
            "generic_line_is_cable": False,
            "dimension_is_cable_geometry": False,
        },
        positive_route_layer_regex=r"^CABLE$",
    )

    with pytest.raises(CableGeometryMaterializationError) as captured:
        materialize_cable_features([source], [route])
    assert captured.value.issues[0]["code"] == "UNSUPPORTED_OR_INCOMPLETE_CURVE_FACTS"
    assert "non-planar 3D" in captured.value.issues[0]["detail"]


def test_com_2d_polyline_uses_entity_elevation_not_ignored_coordinate_z():
    class Polyline2D:
        ObjectName = "AcDb2dPolyline"
        Coordinates = (1.0, 2.0, 999.0, 3.0, 4.0, 888.0)
        Elevation = 7.0
        Normal = (0.0, 0.0, 1.0)
        Type = 0
        Closed = False
        Length = 4.0
        Handle = "C3"

    record = extract_com_entity(Polyline2D(), "Model", "model")

    assert record["dwg_type_name"] == "POLYLINE"
    assert record["points"] == [(1.0, 2.0), (3.0, 4.0)]
    assert record["curve_facts"]["vertices_wcs"] == [
        [1.0, 2.0, 7.0], [3.0, 4.0, 7.0]
    ]
    assert record["curve_facts"]["primitive_type"] == "2DPOLYLINE"
    assert record["curve_facts"]["primitive_parameters"] == {
        "polyline_type": 0
    }


def test_com_fitted_2d_polyline_is_rejected_instead_of_chordized():
    class FittedPolyline2D:
        ObjectName = "AcDb2dPolyline"
        Coordinates = (1.0, 2.0, 7.0, 3.0, 4.0, 7.0)
        Elevation = 7.0
        Normal = (0.0, 0.0, 1.0)
        Type = 1
        Layer = "CABLE"
        Closed = False
        Length = 4.0
        Handle = "FIT2D"

    record = extract_com_entity(FittedPolyline2D(), "Model", "model")
    source = SourceEntity.from_record(_with_identity(record, "ENTITY-FIT2D"))
    route = Feature(
        feature_key="CABLE-FIT2D",
        feature_class="CABLE",
        geometry_kind="LINESTRING",
        native_points=list(source.points),
        source_entity_key=source.entity_key,
        source_handle=source.handle,
        source_layer=source.layer,
        geometry_role="SOURCE_ROUTE",
        style=source.style,
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )
    registry = SimpleNamespace(
        policy={
            "source_geometry_immutable": True,
            "crossing_is_connection": False,
            "support_is_optical_node": False,
            "force_route_components_connected": False,
            "generic_line_is_cable": False,
            "dimension_is_cable_geometry": False,
        },
        positive_route_layer_regex=r"^CABLE$",
    )

    with pytest.raises(CableGeometryMaterializationError) as captured:
        materialize_cable_features([source], [route])
    assert captured.value.issues[0]["code"] == "UNSUPPORTED_OR_INCOMPLETE_CURVE_FACTS"
    assert "complete reader materialization" in captured.value.issues[0]["detail"]


def test_source_model_accepts_legacy_missing_facts_and_rejects_fingerprint_tampering():
    legacy = SourceEntity.from_record({
        "entity_key": "legacy",
        "points": [(1, 2), (3, 4)],
        "centroid": (2, 3),
    })
    assert legacy.curve_facts == {}
    assert legacy.curve_fingerprint == ""

    record = _with_identity(_record_from_bulk_row(_bulk_curve_row()))
    record["curve_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="does not match"):
        SourceEntity.from_record(record)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (float("nan"), "finite number"),
        (float("inf"), "finite number"),
        (-1.0, "non-negative"),
    ],
)
def test_source_model_rejects_invalid_native_lengths(value, message):
    record = _with_identity(_record_from_bulk_row(_bulk_curve_row()))
    record["native_length"] = value

    with pytest.raises(ValueError, match=message):
        SourceEntity.from_record(record)


@pytest.mark.parametrize("missing_side", ["source", "facts"])
def test_cable_curve_gate_requires_both_native_lengths(missing_side):
    record = _with_identity(_record_from_bulk_row(_bulk_curve_row()))
    if missing_side == "source":
        record["native_length"] = None
        expected = "source entity lacks AutoCAD native length"
    else:
        record["curve_facts"] = {
            **record["curve_facts"],
            "native_length": None,
        }
        record["curve_fingerprint"] = canonical_curve_fingerprint(
            record["curve_facts"]
        )
        expected = "curve facts lack AutoCAD native length"
    source = SourceEntity.from_record(record)
    route = Feature(
        feature_key="CABLE-C1",
        feature_class="CABLE",
        geometry_kind="LINESTRING",
        native_points=list(source.points),
        source_entity_key=source.entity_key,
        source_handle=source.handle,
        source_layer=source.layer,
        geometry_role="SOURCE_ROUTE",
        style=CadStyle(),
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )
    registry = SimpleNamespace(
        policy={
            "source_geometry_immutable": True,
            "crossing_is_connection": False,
            "support_is_optical_node": False,
            "force_route_components_connected": False,
            "generic_line_is_cable": False,
            "dimension_is_cable_geometry": False,
        },
        positive_route_layer_regex=r"^CABLE$",
    )

    with pytest.raises(RuntimeError, match=expected):
        _enforce_geometry_policy(
            [source], [route], [], registry,
            {"synthetic_route_vertices": 0},
            require_curve_facts=True,
        )


def test_cable_curve_gate_materializes_bulge_without_chord_length_substitution():
    record = _with_identity(_record_from_bulk_row(_bulk_curve_row()))
    chord = math.dist(record["points"][0], record["points"][1])
    bulge = float(record["curve_facts"]["bulges"][0])
    theta = 4.0 * math.atan(bulge)
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    native_length = radius * abs(theta)
    record["native_length"] = native_length
    record["curve_facts"] = {
        **record["curve_facts"],
        "native_length": native_length,
    }
    record["curve_fingerprint"] = canonical_curve_fingerprint(record["curve_facts"])
    source = SourceEntity.from_record(record)
    route = Feature(
        feature_key="CABLE-CURVED",
        feature_class="CABLE",
        geometry_kind="LINESTRING",
        native_points=list(source.points),
        source_entity_key=source.entity_key,
        source_handle=source.handle,
        source_layer=source.layer,
        geometry_role="SOURCE_ROUTE",
        style=source.style,
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )
    registry = SimpleNamespace(
        policy={
            "source_geometry_immutable": True,
            "crossing_is_connection": False,
            "support_is_optical_node": False,
            "force_route_components_connected": False,
            "generic_line_is_cable": False,
            "dimension_is_cable_geometry": False,
        },
        positive_route_layer_regex=r"^CABLE$",
    )

    materialize_cable_features([source], [route])
    segment = delivery_segments(route)[0]
    assert segment["source_segment_kind"] == "bulge_arc"
    assert segment["source_native_length"] == pytest.approx(native_length)
    assert segment["delivery_chord_length_native"] < native_length
    assert len(segment["delivery_native_points"]) > 2
    assert validate_cable_geometry_materialization([source], [route])["validated"] is True
    _enforce_geometry_policy(
        [source], [route], [], registry,
        {"synthetic_route_vertices": 0},
        require_curve_facts=True,
    )

    # A frozen SourceEntity still contains nested mutable containers.  The
    # publication boundary must therefore rebind facts to their ingestion hash.
    source.curve_facts["primitive_parameters"]["constant_width"] = 99.0
    with pytest.raises(RuntimeError, match="curve facts changed after ingestion"):
        _enforce_geometry_policy(
            [source], [route], [], registry,
            {"synthetic_route_vertices": 0},
            require_curve_facts=True,
        )


def test_curve_inventory_and_cad_entities_evidence_persist_canonical_contract(
    tmp_path, monkeypatch,
):
    curve_record = _with_identity(_record_from_bulk_row(_bulk_curve_row()))
    metadata_record = {
        "entity_key": "METADATA",
        "source_sha256": "source-sha",
        "source_file": "source.dwg",
        "layout": "DOCUMENT",
        "layout_role": "document",
        "cad_role": "document",
        "dwg_type_name": "DOCUMENT_METADATA",
        "text": "CGEOCS=3857;INSUNITS=6",
        "points": [],
        "centroid": (0, 0),
    }
    monkeypatch.setattr(
        "cad2gis_v3.ingest.extract_dwg_records",
        lambda _source: [metadata_record, curve_record],
    )
    source = tmp_path / "source.dwg"
    source.write_bytes(b"fixture")
    profile = SimpleNamespace(
        validate_source=lambda _source: "source-sha",
        dwg_cgeocs="3857",
        dwg_insunits=6,
        drawing_units="metres",
        expected_census={},
    )
    entities, diagnostics = ingest(source, profile)
    curve = next(entity for entity in entities if entity.entity_key == "ENTITY-C1")
    inventory = diagnostics["reader_inventory"]
    assert inventory["curve_facts_entities"] == 1
    assert inventory["curve_fingerprint_entities"] == 1
    assert inventory["curve_facts_schema_versions"] == {CURVE_FACTS_SCHEMA: 1}
    assert inventory["curve_entities_with_nonzero_bulge"] == 1
    assert inventory["curve_entities_with_nonzero_elevation"] == 1

    path = tmp_path / "curve-evidence.gpkg"
    write_evidence(
        path, entities, [], [], [], diagnostics,
        DirectTransformer("EPSG:3857", "EPSG:9481").source,
    )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT curve_facts_schema, curve_facts, curve_fingerprint, native_points "
            "FROM cad_entities WHERE entity_key='ENTITY-C1'"
        ).fetchone()
    assert row[0] == CURVE_FACTS_SCHEMA
    assert json.loads(row[1]) == curve.curve_facts
    assert row[2] == curve.curve_fingerprint
    assert json.loads(row[3]) == [[100.0, 200.0], [110.0, 210.0]]
