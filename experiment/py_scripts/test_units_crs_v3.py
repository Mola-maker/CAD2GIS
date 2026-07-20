"""Unit/CRS contract regressions for cross-CAD conversion."""

from __future__ import annotations

import math

import pytest
from osgeo import ogr

from cad2gis_v3.georef import DirectTransformer, enrich_delivery_metrics
from cad2gis_v3.model import CadStyle, Feature
from cad2gis_v3.warehouse import write_delivery
from cad2gis_v3.units import (
    UNIT_CRS_CONTRACT_SCHEMA_VERSION,
    UnitCrsContractError,
    build_unit_crs_contract,
    resolve_insunits,
)


@pytest.mark.parametrize(
    ("code", "name", "symbol", "scale"),
    (
        (1, "inch", "in", 0.0254),
        (2, "foot", "ft", 0.3048),
        (4, "millimetre", "mm", 0.001),
        (5, "centimetre", "cm", 0.01),
        (6, "metre", "m", 1.0),
    ),
)
def test_supported_insunits_are_explicit(code, name, symbol, scale):
    unit = resolve_insunits(code)
    assert (unit.insunits, unit.name, unit.symbol) == (code, name, symbol)
    assert unit.metres_per_unit == pytest.approx(scale)
    assert unit.scale_to_m == unit.meters_per_unit == unit.metres_per_unit


@pytest.mark.parametrize("code", (0, 3, 7, 20, -1, True, "6", None))
def test_unitless_unknown_or_untyped_insunits_fail_closed(code):
    with pytest.raises(UnitCrsContractError, match="dwg_insunits|Unsupported or unitless"):
        resolve_insunits(code)


def test_apd_metric_direct_contract_preserves_legacy_projection():
    contract = build_unit_crs_contract(6, "EPSG:3857", "EPSG:9481")
    explicit = DirectTransformer(
        "EPSG:3857", "EPSG:9481", unit_contract=contract,
    )
    legacy = DirectTransformer("EPSG:3857", "EPSG:9481")
    point = (13_681_914.403, 69_386.445)

    assert explicit.point(point) == pytest.approx(legacy.point(point), abs=1e-9)
    assert explicit.target_to_source_point(explicit.point(point)) == pytest.approx(
        point, abs=1e-6,
    )
    assert contract.source_to_crs_axis_factor == 1.0
    assert contract.source_coordinate_scale_to_m == 1.0
    assert contract.target_coordinate_scale_to_m == 1.0
    assert contract.coordinate_mode == "direct_crs"

    metadata = explicit.operation_metadata(point)
    unit_manifest = metadata["unit_crs_contract"]
    assert unit_manifest["schema_version"] == UNIT_CRS_CONTRACT_SCHEMA_VERSION
    assert unit_manifest["source_geometry_unit"]["insunits"] == 6
    assert unit_manifest["source_crs_axis_unit"]["metres_per_unit"] == 1.0
    assert unit_manifest["target_crs_axis_unit"]["metres_per_unit"] == 1.0
    assert unit_manifest["provenance"]["dwg_insunits"] == "DWG_DIRECT:$INSUNITS"


@pytest.mark.parametrize(
    ("code", "scale", "native", "metres"),
    (
        (1, 0.0254, 12.0, 0.3048),
        (2, 0.3048, 10.0, 3.048),
        (4, 0.001, 1_000.0, 1.0),
        (5, 0.01, 100.0, 1.0),
    ),
)
def test_reviewed_non_metre_cad_is_scaled_before_crs_operation(
    code, scale, native, metres,
):
    contract = build_unit_crs_contract(
        code,
        "EPSG:3857",
        "EPSG:3857",
        source_coordinate_scale_to_m=scale,
        source_coordinate_scale_reviewed=True,
    )
    transformer = DirectTransformer(
        "EPSG:3857", "EPSG:3857", unit_contract=contract,
    )

    assert transformer.point((native, native * 2.0)) == pytest.approx(
        (metres, metres * 2.0), abs=1e-12,
    )
    assert transformer.target_to_source_point((metres, metres * 2.0)) == pytest.approx(
        (native, native * 2.0), abs=1e-9,
    )
    assert transformer.source_length_to_m(native) == pytest.approx(metres)
    assert transformer.roundtrip_error(((native, native * 2.0),)) < 1e-9


@pytest.mark.parametrize(
    "kwargs, message",
    (
        ({}, "require explicit"),
        ({"source_coordinate_scale_to_m": 0.001}, "explicitly reviewed"),
        (
            {
                "source_coordinate_scale_to_m": 1.0,
                "source_coordinate_scale_reviewed": True,
            },
            "does not match",
        ),
    ),
)
def test_non_metre_cad_scale_missing_unreviewed_or_mismatched_fails(kwargs, message):
    with pytest.raises(UnitCrsContractError, match=message):
        build_unit_crs_contract(4, "EPSG:3857", "EPSG:9481", **kwargs)


@pytest.mark.parametrize("source_crs", (None, "EPSG:4326"))
def test_missing_or_geographic_source_crs_cannot_be_guessed(source_crs):
    with pytest.raises(UnitCrsContractError, match="cannot be guessed"):
        build_unit_crs_contract(6, source_crs, "EPSG:9481")


def test_reviewed_authoritative_local_registration_is_separate_from_direct_crs():
    contract = build_unit_crs_contract(
        6,
        None,
        "EPSG:9481",
        local_registration_strategy="surveyed-control-similarity",
        local_registration_reviewed=True,
    )
    manifest = contract.to_manifest_dict()

    assert contract.coordinate_mode == "reviewed_authoritative_registration"
    assert contract.can_direct_transform is False
    assert manifest["status"] == "registration_required"
    assert manifest["source_crs_axis_unit"] is None
    assert manifest["local_registration_strategy"] == "surveyed-control-similarity"
    with pytest.raises(UnitCrsContractError, match="cannot apply a local registration"):
        DirectTransformer("EPSG:3857", "EPSG:9481", unit_contract=contract)


def test_registration_name_and_review_must_be_supplied_together():
    with pytest.raises(UnitCrsContractError, match="requires local_registration_reviewed"):
        build_unit_crs_contract(
            6, None, "EPSG:9481", local_registration_strategy="similarity",
        )
    with pytest.raises(UnitCrsContractError, match="requires local_registration_strategy"):
        build_unit_crs_contract(
            6, None, "EPSG:9481", local_registration_reviewed=True,
        )
    with pytest.raises(UnitCrsContractError, match="only valid when source_crs"):
        build_unit_crs_contract(
            6,
            "EPSG:3857",
            "EPSG:9481",
            local_registration_strategy="similarity",
            local_registration_reviewed=True,
        )


def test_target_axis_unit_is_recorded_separately_and_lengths_are_metric():
    # EPSG:2227 uses US survey feet; it is intentionally distinct from both
    # the CAD drawing unit and the source CRS metre axis.
    contract = build_unit_crs_contract(6, "EPSG:3857", "EPSG:2227")
    transformer = DirectTransformer(
        "EPSG:3857", "EPSG:2227", unit_contract=contract,
    )
    target_factor = contract.target_crs_axis_unit.metres_per_unit

    assert math.isclose(target_factor, 0.30480060960121924, rel_tol=1e-12)
    assert contract.cad_unit.metres_per_unit == 1.0
    assert contract.source_crs_axis_unit.metres_per_unit == 1.0
    assert transformer.grid_length_m(((0.0, 0.0), (10.0, 0.0))) == pytest.approx(
        10.0 * target_factor,
    )


def test_contract_crs_mismatch_is_rejected_before_transform():
    contract = build_unit_crs_contract(6, "EPSG:3857", "EPSG:9481")
    with pytest.raises(UnitCrsContractError, match="target_crs does not match"):
        DirectTransformer("EPSG:3857", "EPSG:3857", unit_contract=contract)


def test_geographic_target_is_rejected_for_metric_delivery_fields():
    with pytest.raises(UnitCrsContractError, match="target_crs must be a projected CRS"):
        build_unit_crs_contract(6, "EPSG:3857", "EPSG:4326")


def _materialized_cable(*, native_points, source_length, delivery_points, measurement=None):
    delta = None if measurement is None else measurement - source_length
    return Feature(
        feature_key="CABLE-UNIT-TEST",
        feature_class="CABLE",
        geometry_kind="LineString",
        native_points=list(native_points),
        source_entity_key="ENTITY-UNIT-TEST",
        source_handle="UNIT-TEST",
        source_layer="CABLE",
        geometry_role="SOURCE_ROUTE",
        style=CadStyle(),
        attributes={
            "curve_materialization": {
                "source_segments": [{
                    "source_segment_index": 0,
                    "source_segment_kind": "bulge_arc" if len(delivery_points) > 2 else "line",
                    "source_start_vertex_index": 0,
                    "source_end_vertex_index": 1,
                    "source_native_length": source_length,
                    "native_length_source": (
                        "analytic_bulge_arc" if len(delivery_points) > 2
                        else "ordered_wcs_vertices"
                    ),
                    "delivery_native_points": delivery_points,
                    "delivery_chord_length_native": sum(
                        math.dist(start, end)
                        for start, end in zip(delivery_points, delivery_points[1:])
                    ),
                }],
            },
            "span_metrics": [{
                "segment_index": 0,
                "source_native_length_m": source_length,
                "dimension_entity_key": "DIM-1" if measurement is not None else None,
                "measurement_native_m": measurement,
                "measurement_delta_m": delta,
                "status": "measured" if measurement is not None else "unmeasured_no_dimension",
            }],
        },
    )


def test_curved_cable_grid_length_uses_materialized_path_not_endpoint_chord(tmp_path):
    route = _materialized_cable(
        native_points=((0.0, 0.0), (10.0, 0.0)),
        source_length=5.0 * math.pi,
        delivery_points=((0.0, 0.0), (5.0, 5.0), (10.0, 0.0)),
    )
    transformer = DirectTransformer("EPSG:3857", "EPSG:3857")

    enrich_delivery_metrics([route], transformer)

    metric = route.attributes["span_metrics"][0]
    assert metric["source_native_length_m"] == pytest.approx(5.0 * math.pi)
    assert metric["delivery_grid_length_m"] == pytest.approx(2.0 * math.sqrt(50.0))
    assert metric["delivery_grid_length_m"] > math.dist(*route.native_points)
    assert route.attributes["delivery_grid_length_m"] == pytest.approx(
        metric["delivery_grid_length_m"]
    )
    assert metric["source_segment_kind"] == "bulge_arc"
    assert metric["native_length_source"] == "analytic_bulge_arc"

    delivery = tmp_path / "curved-cable.gpkg"
    counts = write_delivery(delivery, [route], transformer)
    dataset = ogr.Open(str(delivery), 0)
    segment = dataset.GetLayerByName("CABLE_SEGMENT").GetNextFeature()
    geometry = segment.GetGeometryRef()
    assert counts["CABLE"] == counts["CABLE_SEGMENT"] == 1
    assert geometry.GetPointCount() == 3
    assert geometry.Length() == pytest.approx(metric["delivery_grid_length_m"])
    dataset = None


def test_millimetre_cable_span_and_dimension_are_normalized_to_metres_once(tmp_path):
    route = _materialized_cable(
        native_points=((0.0, 0.0), (1_000.0, 0.0)),
        source_length=1_000.0,
        delivery_points=((0.0, 0.0), (1_000.0, 0.0)),
        measurement=1_000.0,
    )
    contract = build_unit_crs_contract(
        4,
        "EPSG:3857",
        "EPSG:3857",
        source_coordinate_scale_to_m=0.001,
        source_coordinate_scale_reviewed=True,
    )
    transformer = DirectTransformer(
        "EPSG:3857", "EPSG:3857", unit_contract=contract,
    )

    enrich_delivery_metrics([route], transformer)
    first = dict(route.attributes["span_metrics"][0])
    enrich_delivery_metrics([route], transformer)
    second = route.attributes["span_metrics"][0]

    assert first == second
    assert second["source_native_length_m"] == pytest.approx(1.0)
    assert second["measurement_native_m"] == pytest.approx(1.0)
    assert second["measurement_delta_m"] == pytest.approx(0.0)
    assert second["delivery_grid_length_m"] == pytest.approx(1.0)
    assert route.attributes["source_cad_length_m"] == pytest.approx(1.0)
    assert route.attributes["dimension_length_m"] == pytest.approx(1.0)
    assert route.attributes["span_unit"] == "m"
    assert route.field_provenance["dimension_length_m"] == (
        "DWG_DERIVED:reviewed-unit-CRS-contract-to-metres"
    )

    delivery = tmp_path / "millimetre-cable.gpkg"
    counts = write_delivery(delivery, [route], transformer)
    dataset = ogr.Open(str(delivery), 0)
    segment = dataset.GetLayerByName("CABLE_SEGMENT").GetNextFeature()
    assert counts["CABLE_SEGMENT"] == 1
    assert segment.GetField("source_native_length_m") == pytest.approx(1.0)
    assert segment.GetField("measurement_native_m") == pytest.approx(1.0)
    assert segment.GetGeometryRef().Length() == pytest.approx(1.0)
    dataset = None
