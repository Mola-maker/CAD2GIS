"""Focused regressions for always-on native/nominal/delivery lineage."""

from __future__ import annotations

import hashlib
import json
import math

import pytest

from cad2gis_v3.calibration import GroundControlPoint, ValidationSettings, fit_calibration
from cad2gis_v3.georef import (
    DeliveryTransformer,
    DirectTransformer,
    feature_adjustment_records,
)
from cad2gis_v3.model import CadStyle, Feature
from cad2gis_v3.pipeline import _nominal_lineage_audit


def _feature(key, feature_class, points):
    return Feature(
        feature_key=key,
        feature_class=feature_class,
        geometry_kind="LineString" if len(points) > 1 else "Point",
        native_points=list(points),
        source_entity_key=f"entity-{key}",
        source_handle=key,
        source_layer=feature_class,
        geometry_role="SOURCE_ROUTE" if feature_class == "CABLE" else "SOURCE_ASSET",
        style=CadStyle(),
        attributes={},
    )


def _fingerprint(payload):
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


@pytest.fixture
def nominal():
    return DirectTransformer("EPSG:3857", "EPSG:9481")


def _expected_target_rotation(transformer, anchor, cad_radians):
    probe = (
        anchor[0] + math.cos(cad_radians),
        anchor[1] + math.sin(cad_radians),
    )
    target_anchor = transformer.point(anchor)
    target_probe = transformer.point(probe)
    target_ccw = math.degrees(math.atan2(
        target_probe[1] - target_anchor[1],
        target_probe[0] - target_anchor[0],
    ))
    return (-target_ccw) % 360.0


@pytest.mark.parametrize("cad_degrees", (90.0, -90.0, 23.75, -37.5))
def test_no_gcp_rotation_uses_nominal_target_grid_direction(nominal, cad_degrees):
    anchor = (13_681_914.403, 69_386.445)
    cad_radians = math.radians(cad_degrees)

    expected = _expected_target_rotation(nominal, anchor, cad_radians)

    assert nominal.qgis_rotation(anchor, cad_radians) == pytest.approx(expected)
    assert nominal.qgis_rotation(anchor, cad_radians) != pytest.approx(
        (-cad_degrees) % 360.0, abs=1e-6,
    )


@pytest.mark.parametrize("cad_degrees", (90.0, -90.0, 23.75, -37.5))
def test_disabled_identity_residual_rotation_uses_delivery_direction(nominal, cad_degrees):
    result = fit_calibration(
        (), nominal, model="disabled", expected_target_crs="EPSG:9481",
    )
    delivery = DeliveryTransformer(nominal, result)
    anchor = (13_681_914.403, 69_386.445)
    cad_radians = math.radians(cad_degrees)

    expected = _expected_target_rotation(delivery, anchor, cad_radians)

    assert delivery.qgis_rotation(anchor, cad_radians) == pytest.approx(expected)
    assert delivery.qgis_rotation(anchor, cad_radians) != pytest.approx(
        (-cad_degrees) % 360.0, abs=1e-6,
    )


def test_qgis_rotation_fails_closed_for_nonfinite_or_degenerate_projection(nominal):
    with pytest.raises(ValueError, match="must be finite"):
        nominal.qgis_rotation((math.nan, 0.0), 0.0)

    nominal.point = lambda _point: (489_621.0, 68_893.0)
    with pytest.raises(ValueError, match="degenerate"):
        nominal.qgis_rotation((13_681_914.403, 69_386.445), 0.0)


def test_direct_nominal_lineage_is_complete_and_identity(nominal):
    features = [
        _feature("P-1", "PTECH", [(13_681_914.403, 69_386.445)]),
        _feature("C-1", "CABLE", [(13_681_914.403, 69_386.445), (13_681_920.0, 69_390.0)]),
    ]

    records = feature_adjustment_records(features, nominal, nominal)

    assert len(records) == len(features)
    for record in records:
        assert record["model"] == "nominal_direct"
        assert record["adjusted_points_json"] == record["nominal_points_json"]
        assert record["adjusted_fingerprint"] == record["nominal_fingerprint"]
        assert record["mean_displacement_m"] == pytest.approx(0.0)
        assert record["max_displacement_m"] == pytest.approx(0.0)
        for space in ("native", "nominal", "adjusted"):
            payload = record[f"{space}_points_json"]
            assert record[f"{space}_fingerprint"] == _fingerprint(payload)


def test_disabled_residual_lineage_is_identity_and_explicit(nominal):
    result = fit_calibration(
        (), nominal, model="disabled", expected_target_crs="EPSG:9481",
    )
    delivery = DeliveryTransformer(nominal, result)
    feature = _feature("C-1", "CABLE", [(1.0, 2.0), (3.0, 5.0)])

    record = feature_adjustment_records([feature], nominal, delivery)[0]

    assert record["model"] == "identity_residual"
    assert record["adjusted_points_json"] == record["nominal_points_json"]
    assert record["adjusted_fingerprint"] == record["nominal_fingerprint"]
    assert record["centroid_dx_m"] == pytest.approx(0.0)
    assert record["centroid_dy_m"] == pytest.approx(0.0)
    assert record["mean_displacement_m"] == pytest.approx(0.0)
    assert record["max_displacement_m"] == pytest.approx(0.0)


def test_accepted_residual_lineage_keeps_adjusted_schema_and_shift(nominal):
    cad_point = (1.0, 2.0)
    nominal_point = nominal.point(cad_point)
    control = GroundControlPoint(
        point_id="TRAIN-1",
        cad_point=cad_point,
        target_point=(nominal_point[0] + 2.0, nominal_point[1] - 1.0),
        target_crs="EPSG:9481",
        role="train",
        source="synthetic reviewed control",
        accuracy_m=1.0,
        weight=1.0,
    )
    result = fit_calibration(
        (control,), nominal, model="translation", expected_target_crs="EPSG:9481",
        validation=ValidationSettings(
            max_check_rmse_m=None,
            max_check_p95_m=None,
            max_check_error_m=None,
            min_check_points=0,
            affine_min_improvement_ratio=None,
            spatial_distribution_reviewed=False,
            spatial_distribution_review_source="",
        ),
    )
    delivery = DeliveryTransformer(nominal, result)
    feature = _feature("P-1", "PTECH", [cad_point])

    record = feature_adjustment_records([feature], nominal, delivery)[0]

    assert record["model"] == "translation"
    assert json.loads(record["adjusted_points_json"])[0] == pytest.approx(
        [nominal_point[0] + 2.0, nominal_point[1] - 1.0], abs=1e-8,
    )
    assert record["max_displacement_m"] == pytest.approx(2.2360679775, abs=1e-8)


def test_no_profile_audit_contains_lineage_without_gcp_observations(nominal):
    feature = _feature("P-1", "PTECH", [(1.0, 2.0)])
    records = feature_adjustment_records([feature], nominal, nominal)

    audit = _nominal_lineage_audit(nominal, "a" * 64, records)

    assert audit["status"] == "not_provided"
    assert audit["observations"] == []
    assert audit["result"]["selected_model"] == "nominal_direct"
    assert audit["feature_displacements"] == records
