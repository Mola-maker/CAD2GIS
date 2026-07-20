"""Calibration gates for reviewed non-metre source CRS contracts.

These tests deliberately exercise the boundary between CAD drawing units,
source CRS axis units, and target-grid residual metres.  They do not change
the nominal CRS operation or infer a CRS from an input drawing.
"""

from __future__ import annotations

import pytest

from cad2gis_v3.calibration import GroundControlPoint, fit_calibration
from cad2gis_v3.georef import DirectTransformer
from cad2gis_v3.units import build_unit_crs_contract


def _control(point_id: str, cad_point, target_point, *, target_crs="EPSG:9481"):
    return GroundControlPoint(
        point_id=point_id,
        cad_point=tuple(cad_point),
        target_point=tuple(target_point),
        target_crs=target_crs,
        role="train",
        source="reviewed synthetic control",
        accuracy_m=0.01,
        weight=1.0,
    )


def test_state_plane_us_foot_source_is_accepted_with_reviewed_contract():
    contract = build_unit_crs_contract(
        2,
        "EPSG:2277",
        "EPSG:9481",
        source_coordinate_scale_to_m=0.3048,
        source_coordinate_scale_reviewed=True,
    )
    transformer = DirectTransformer(
        "EPSG:2277", "EPSG:9481", unit_contract=contract,
    )
    cad_point = (100.0, 200.0)
    nominal = transformer.point(cad_point)
    result = fit_calibration(
        (_control("FT-1", cad_point, (nominal[0] + 2.5, nominal[1] - 1.25)),),
        transformer,
        model="translation",
    )

    assert result.parameters["pivot_shift_e_m"] == pytest.approx(2.5)
    assert result.parameters["pivot_shift_n_m"] == pytest.approx(-1.25)
    assert result.train_metrics.rmse_m == pytest.approx(0.0, abs=1e-10)


def test_non_metre_source_without_reviewed_unit_contract_fails_closed():
    class NoContractTransformer:
        source_crs = "EPSG:2277"
        target_crs = "EPSG:9481"

        @staticmethod
        def point(point):
            return float(point[0]), float(point[1])

    point = (100.0, 200.0)
    with pytest.raises(ValueError, match="unit_contract"):
        fit_calibration(
            (_control("NO-CONTRACT", point, point),),
            NoContractTransformer(),
            model="translation",
        )


def test_non_metre_target_is_rejected_even_with_a_nominal_contract():
    contract = build_unit_crs_contract(6, "EPSG:3857", "EPSG:2227")
    transformer = DirectTransformer(
        "EPSG:3857", "EPSG:2227", unit_contract=contract,
    )
    point = (1.0, 2.0)
    with pytest.raises(ValueError, match="metres"):
        fit_calibration(
            (_control("TARGET-FT", point, transformer.point(point)),),
            transformer,
            model="translation",
            expected_target_crs="EPSG:2227",
        )


def test_geographic_target_is_rejected_before_residual_calibration():
    class GeographicTransformer:
        source_crs = "EPSG:3857"
        target_crs = "EPSG:4326"

        @staticmethod
        def point(point):
            return float(point[0]), float(point[1])

    point = (1.0, 2.0)
    with pytest.raises(ValueError, match="projected CRS"):
        fit_calibration(
            (_control("TARGET-GEOGRAPHIC", point, point, target_crs="EPSG:4326"),),
            GeographicTransformer(),
            model="translation",
            expected_target_crs="EPSG:4326",
        )


def test_apd_metric_direct_transformer_remains_calibratable():
    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    point = (13_681_914.403, 69_386.445)
    nominal = transformer.point(point)
    result = fit_calibration(
        (_control("APD-METRIC", point, nominal),),
        transformer,
        model="translation",
    )

    assert result.selected_model == "translation"
    assert result.train_metrics.rmse_m == pytest.approx(0.0, abs=1e-10)

