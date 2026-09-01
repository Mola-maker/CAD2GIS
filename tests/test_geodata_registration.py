from __future__ import annotations

import math

from pyproj import Transformer

from cad2gis.cad2gis_v3 import onboarding
from cad2gis.cad2gis_v3.geodata import (
    GEODATA_REGISTRATION_SCHEMA,
    crs_to_local_point,
    local_to_crs_point,
)
from cad2gis.cad2gis_v3.georef import GeoDataTransformer
from cad2gis.cad2gis_v3.units import build_unit_crs_contract


def _registration() -> dict[str, object]:
    return {
        "schema_version": GEODATA_REGISTRATION_SCHEMA,
        "coordinate_system_id": "UTM84-49S",
        "target_crs": "EPSG:32749",
        "design_point": [0.0, 0.0],
        "reference_point": [685710.1999292718, 9185968.57756193],
        "horizontal_unit_scale": 1.0,
        "user_scale_factor": 1.0,
        "north_direction": [0.0, 1.0],
        "authority": "DWG_DIRECT:GEODATA",
    }


def test_geodata_similarity_registration_is_invertible() -> None:
    registration = _registration()
    native = (125.5, -72.25)
    projected = local_to_crs_point(native, registration)

    assert projected == (
        685835.6999292718,
        9185896.32756193,
    )
    restored = crs_to_local_point(projected, registration)
    assert math.dist(native, restored) < 1e-9


def test_geodata_transformer_registers_before_crs_projection() -> None:
    registration = _registration()
    contract = build_unit_crs_contract(
        6,
        "EPSG:32749",
        "EPSG:3857",
        source_coordinate_scale_to_m=1.0,
        source_coordinate_scale_reviewed=True,
    )
    transformer = GeoDataTransformer(
        "EPSG:32749",
        "EPSG:3857",
        geodata_registration=registration,
        unit_contract=contract,
    )
    expected = Transformer.from_crs(
        "EPSG:32749", "EPSG:3857", always_xy=True,
    ).transform(*registration["reference_point"])

    assert math.dist(transformer.point((0.0, 0.0)), expected) < 1e-6
    assert transformer.roundtrip_error([(0.0, 0.0), (125.5, -72.25)]) < 1e-6
    assert transformer.engine_crosscheck_error([(0.0, 0.0)]) < 1e-6
    assert transformer.lineage_model == "dwg_geodata_nominal"


def test_onboarding_candidate_carries_only_source_observed_geodata() -> None:
    registration = _registration()
    candidates = onboarding._crs_candidates({
        "drawing": {"dwg_cgeocs": "UTM84-49S", "dwg_insunits": 6},
        "crs": {"geodata_registration": registration},
    })

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["candidate_id"].endswith(":GEODATA")
    assert candidate["source_crs"] == "EPSG:32749"
    assert candidate["target_crs"] == "EPSG:32749"
    assert candidate["source_coordinate_scale_to_m"] == 1.0
    assert candidate["geodata_registration"] == registration
    assert candidate["evidence"]["authority"] == "DWG_DIRECT:GEODATA"
