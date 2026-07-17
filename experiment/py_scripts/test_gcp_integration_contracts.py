"""Independent integration contracts for target-space GCP calibration.

The expected coordinates in this module are calculated from closed-form test
fixtures.  They intentionally never call ``DeliveryTransformer`` (or the
fitted residual transform), so a projection-order regression cannot validate
itself circularly.
"""

from __future__ import annotations

from dataclasses import replace
import math

import pytest
from osgeo import ogr, osr
from pyproj import Geod

from cad2gis_v3.calibration import (
    GroundControlPoint,
    ValidationSettings,
    fit_calibration,
)
from cad2gis_v3.georef import DeliveryTransformer, enrich_delivery_metrics
from cad2gis_v3.model import CadStyle, Feature
from cad2gis_v3.warehouse import LAYER_ORDER, write_delivery


_SOURCE_CRS = "EPSG:3857"
_TARGET_CRS = "EPSG:9481"
_PIVOT = (500_000.0, 70_000.0)
_SCALE = 1.0004
_ANGLE_RAD = math.radians(0.35)
_SHIFT = (4.25, -2.75)


def _manual_nominal(native_point):
    """Synthetic native-to-nominal CRS step with hand-checkable coefficients."""

    x, y = map(float, native_point)
    return (
        _PIVOT[0] + 1.20 * x + 0.15 * y,
        _PIVOT[1] - 0.20 * x + 0.90 * y,
    )


def _manual_target_calibration(nominal_point):
    """Synthetic surveyed truth: similarity adjustment in target space."""

    easting, northing = map(float, nominal_point)
    x = easting - _PIVOT[0]
    y = northing - _PIVOT[1]
    cosine = math.cos(_ANGLE_RAD)
    sine = math.sin(_ANGLE_RAD)
    return (
        _PIVOT[0] + _SCALE * (cosine * x - sine * y) + _SHIFT[0],
        _PIVOT[1] + _SCALE * (sine * x + cosine * y) + _SHIFT[1],
    )


def _manual_surveyed_truth(native_point):
    """Authoritative fixture order: native -> nominal -> target calibration."""

    return _manual_target_calibration(_manual_nominal(native_point))


class _SyntheticNominalTransformer:
    """Minimal nominal transformer whose coordinates come from the formula above."""

    source_crs = _SOURCE_CRS
    target_crs = _TARGET_CRS

    def __init__(self):
        self.source = osr.SpatialReference()
        self.target = osr.SpatialReference()
        assert self.source.SetFromUserInput(self.source_crs) == 0
        assert self.target.SetFromUserInput(self.target_crs) == 0
        self.geod = Geod(ellps="WGS84")

    def point(self, point):
        return _manual_nominal(point)

    def points(self, points):
        return [self.point(point) for point in points]


def _accepted_calibration():
    nominal = _SyntheticNominalTransformer()
    native_controls = (
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 100.0),
        (100.0, 100.0),
        (45.0, 65.0),
    )
    controls = tuple(
        GroundControlPoint(
            point_id=f"SYN-{index}",
            cad_point=native_point,
            target_point=_manual_surveyed_truth(native_point),
            target_crs=_TARGET_CRS,
            role="check" if index == len(native_controls) - 1 else "train",
            source="closed-form synthetic surveyed fixture",
            accuracy_m=0.01,
            weight=1.0,
        )
        for index, native_point in enumerate(native_controls)
    )
    result = fit_calibration(
        controls,
        nominal,
        model="similarity",
        validation=ValidationSettings(
            max_check_rmse_m=1e-6,
            max_check_p95_m=1e-6,
            max_check_error_m=1e-6,
            min_check_points=1,
            affine_min_improvement_ratio=None,
            spatial_distribution_reviewed=True,
            spatial_distribution_review_source="synthetic fixture corners plus holdout",
        ),
        expected_source_crs=_SOURCE_CRS,
        expected_target_crs=_TARGET_CRS,
    )
    assert result.validation_passed is True
    return nominal, result


def _feature(key, feature_class, native_points, geometry_kind, geometry_role):
    return Feature(
        feature_key=key,
        feature_class=feature_class,
        geometry_kind=geometry_kind,
        native_points=list(native_points),
        source_entity_key=f"entity-{key}",
        source_handle=key,
        source_layer=feature_class,
        geometry_role=geometry_role,
        style=CadStyle(aci_color=3),
        attributes={"CODE": key},
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )


def test_manual_surveyed_truth_locks_projection_order_and_point_line_consistency(tmp_path):
    nominal, result = _accepted_calibration()
    delivery = DeliveryTransformer(nominal, result)
    native_point = (25.0, 35.0)
    native_line = ((-10.0, 10.0), (30.0, 40.0), (90.0, -20.0))
    expected_point = _manual_surveyed_truth(native_point)
    expected_line = tuple(_manual_surveyed_truth(point) for point in native_line)

    # This deliberately wrong order is observably different, so the expected
    # values above would catch calibration being applied before nominal CRS.
    wrong_order = _manual_nominal(_manual_target_calibration(native_point))
    assert math.dist(expected_point, wrong_order) > 1_000.0

    point = _feature("POINT-1", "PTECH", (native_point,), "Point", "SOURCE_ASSET")
    line = _feature("LINE-1", "CABLE", native_line, "LineString", "SOURCE_ROUTE")
    original_point = tuple(point.native_points)
    original_line = tuple(line.native_points)
    enrich_delivery_metrics((point, line), delivery)

    assert delivery.point(native_point) == pytest.approx(expected_point, abs=1e-7)
    for actual, expected in zip(delivery.points(native_line), expected_line):
        assert actual == pytest.approx(expected, abs=1e-7)
    assert (point.attributes["X"], point.attributes["Y"]) == pytest.approx(
        expected_point, abs=1e-7
    )
    expected_grid_length = sum(
        math.dist(start, end) for start, end in zip(expected_line, expected_line[1:])
    )
    assert line.attributes["LONGUEUR"] == pytest.approx(expected_grid_length, abs=1e-7)
    assert line.attributes["delivery_grid_length_m"] == pytest.approx(
        expected_grid_length, abs=1e-7
    )
    assert tuple(point.native_points) == original_point
    assert tuple(line.native_points) == original_line

    delivery_path = tmp_path / "manual_contract_delivery.gpkg"
    write_delivery(delivery_path, (point, line), delivery)
    dataset = ogr.Open(str(delivery_path))
    assert dataset is not None
    try:
        stored_point_feature = dataset.GetLayerByName("PTECH").GetNextFeature()
        point_geometry = stored_point_feature.GetGeometryRef()
        assert (point_geometry.GetX(), point_geometry.GetY()) == pytest.approx(
            expected_point, abs=1e-7
        )
        stored_line_feature = dataset.GetLayerByName("CABLE").GetNextFeature()
        line_geometry = stored_line_feature.GetGeometryRef()
        stored_line = tuple(
            line_geometry.GetPoint_2D(index)
            for index in range(line_geometry.GetPointCount())
        )
        for actual, expected in zip(stored_line, expected_line):
            assert actual == pytest.approx(expected, abs=1e-7)
    finally:
        dataset = None


@pytest.mark.parametrize("validation_passed", [False, None])
def test_delivery_transformer_fails_closed_without_explicit_acceptance(validation_passed):
    nominal, accepted = _accepted_calibration()
    unaccepted = replace(accepted, validation_passed=validation_passed)

    with pytest.raises(ValueError, match="failed calibration"):
        DeliveryTransformer(nominal, unaccepted)


def test_delivery_schema_excludes_gcp_target_and_residual_fields(tmp_path):
    nominal, result = _accepted_calibration()
    delivery = DeliveryTransformer(nominal, result)
    path = tmp_path / "schema_contract_delivery.gpkg"
    write_delivery(path, (), delivery)

    forbidden_gcp_fields = {
        "point_id",
        "cad_x",
        "cad_y",
        "nominal_easting",
        "nominal_northing",
        "target_easting",
        "target_northing",
        "adjusted_easting",
        "adjusted_northing",
        "observed_easting",
        "observed_northing",
        "predicted_easting",
        "predicted_northing",
        "nominal_error_m",
        "residual_e_m",
        "residual_n_m",
        "residual_dx_m",
        "residual_dy_m",
        "residual_m",
        "error_m",
        "accuracy_m",
        "reviewed_weight",
        "fitting_weight",
        "effective_weight",
        "inlier",
    }
    dataset = ogr.Open(str(path))
    assert dataset is not None
    try:
        layer_names = {
            dataset.GetLayerByIndex(index).GetName()
            for index in range(dataset.GetLayerCount())
        }
        assert layer_names == set(LAYER_ORDER)
        for layer_name in LAYER_ORDER:
            definition = dataset.GetLayerByName(layer_name).GetLayerDefn()
            fields = {
                definition.GetFieldDefn(index).GetName()
                for index in range(definition.GetFieldCount())
            }
            assert fields.isdisjoint(forbidden_gcp_fields), (
                layer_name,
                sorted(fields & forbidden_gcp_fields),
            )
    finally:
        dataset = None
