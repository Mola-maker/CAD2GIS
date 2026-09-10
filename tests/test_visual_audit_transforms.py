"""Independent inverse checks for actual canonical OSM/GEODATA run modes."""

import copy
import math
import json

import pytest
from pyproj import Transformer
from pyproj.enums import TransformDirection
from shapely.geometry import LineString
from shapely.ops import transform

from cad2gis.cad2gis_v3 import osm_matching
from cad2gis.cad2gis_v3.georef import DirectTransformer, GeoDataTransformer
from cad2gis.cad2gis_v3.units import build_unit_crs_contract
from cad2gis.visual_audit import UnsupportedAuditTransform, geometry_change_review_rows, inverse_operation, transform_roundtrip_metrics


def manifest_fixture(*, source="EPSG:32749", target="EPSG:3857", scale=1.0, local=False, geodata=True, registration=None):
    source = None if local else source
    contract = build_unit_crs_contract(
        4 if scale == 0.001 else 6, source, target, source_coordinate_scale_to_m=scale,
        source_coordinate_scale_reviewed=True,
        **({"local_registration_strategy": "osm_relative", "local_registration_reviewed": True} if local else {}),
    )
    if local:
        nominal = osm_matching.LocalMatchingReference(source, target, contract)
    elif geodata:
        nominal = GeoDataTransformer(source, target, unit_contract=contract, geodata_registration=registration or {
            "schema_version": "cad2gis.dwg_geodata_registration.v1", "coordinate_system_id": "UTM84-49S",
            "target_crs": source, "design_point": [11.0, -20.0],
            "reference_point": [685710.1999292718, 9185968.57756193],
            "north_direction": [0.6, 0.8], "horizontal_unit_scale": scale, "user_scale_factor": 1.0,
            "authority": "DWG_DIRECT:GEODATA",
        })
    else:
        nominal = DirectTransformer(source, target, unit_contract=contract)
    latitude = 60.0 if local else -7.359767890061975
    record = osm_matching._seal({
        "schema_version": osm_matching.SCHEMA, "source_sha256": "a" * 64, "source_path": "drawing.dwg",
        "source_crs": source, "target_crs": target, "revision": 1, "parent_match_sha256": None,
        "region_id": None, "status": "matched", "authority": "relative_osm", "absolute_accuracy_verified": False,
        "base": {"mode": "local" if local else "nominal", "source_unit_to_m": scale,
                 "reference_latitude": latitude, "nominal_operation_sha256": osm_matching._nominal_identity(nominal),
                 "scale_to_3857": scale / math.cos(math.radians(latitude)) if local else 1.0},
        "translation_3857": [12543751.0521, 8200000.0] if local else [-3.1110917070730775, 0.9136057806470526],
        "osm_snapshot": {"roads": [], "sha256": osm_matching._digest([])},
    })
    operation = nominal.operation_metadata((0, 0) if local or geodata else (685710, 9185968))
    operation["osm_matching_sha256"] = record["match_sha256"]
    manifest = {"source": {"sha256": "a" * 64}, "osm_matching": copy.deepcopy(record), "crs": {
        "source_crs": source, "target_crs": target, "unit_crs_contract": copy.deepcopy(nominal.unit_crs_manifest),
        "coordinate_operation": operation, "calibration": {"status": "not_provided"},
        "lineage": {"model": "osm_relative_translation"}, "osm_matching": record,
        "osm_matching_by_region": {"MAIN": record},
        # This real-world legacy hint must never be mistaken for the applied shift.
        "osm_anchor": {"translation_dx": 12543751.0521, "translation_dy": -821454.7449},
    }}
    return manifest, osm_matching.OSMMatchingTransformer(record, nominal)


@pytest.mark.parametrize("target,scale", [("EPSG:3857", 1.0), ("EPSG:32749", 1.0), ("EPSG:3857", 0.001)])
def test_independent_inverse_matches_canonical_geodata_then_osm_transform(target, scale):
    manifest, canonical = manifest_fixture(target=target, scale=scale)
    original = copy.deepcopy(manifest)
    inverse = inverse_operation(manifest)
    native = [(0.0, 0.0), (-263 / scale, -300 / scale), (275 / scale, 266 / scale)]
    delivered = canonical.points(native)
    for source, target_point in zip(native, delivered):
        assert inverse(*target_point) == pytest.approx(source, abs=3e-6 / scale)
        assert inverse.forward(*source) == pytest.approx(target_point, abs=3e-6)
    local_line = transform(inverse, LineString(delivered))
    assert local_line.hausdorff_distance(LineString(native)) * scale < 3e-6
    metrics = transform_roundtrip_metrics(inverse, native, delivered, scale, 1.0)
    assert metrics["passed"] is True
    assert inverse.metadata["method"] == "manifest_declared_pipeline_inverse"
    assert inverse.metadata["legacy_osm_anchor_hint_applied"] is False
    assert manifest == original


@pytest.mark.parametrize("scale", [1.0, 0.001])
def test_explicit_local_osm_base_has_independent_inverse_without_guessing_source_crs(scale):
    manifest, canonical = manifest_fixture(local=True, scale=scale, target="EPSG:32649")
    inverse = inverse_operation(manifest)
    for source in [(0, 0), (100 / scale, -200 / scale)]:
        target = canonical.point(source)
        assert inverse(*target) == pytest.approx(source, abs=3e-6 / scale)
    assert inverse.metadata["method"] == "declared_local_unit_scale"


def reseal_manifest(manifest):
    record = osm_matching._seal(manifest["crs"]["osm_matching"])
    manifest["crs"]["osm_matching"] = record
    manifest["crs"]["osm_matching_by_region"] = {"MAIN": record}
    manifest["osm_matching"] = copy.deepcopy(record)
    manifest["crs"]["coordinate_operation"]["osm_matching_sha256"] = record["match_sha256"]


@pytest.mark.parametrize("tamper", ["record_hash", "source_hash", "nominal_hash", "unit_scale", "missing_pipeline", "abstained", "region", "unreviewed_scale"])
def test_osm_inverse_keeps_record_source_unit_and_scope_guards(tamper):
    manifest, _ = manifest_fixture()
    crs = manifest["crs"]
    if tamper == "record_hash":
        crs["osm_matching"]["translation_3857"][0] += 1
        manifest["osm_matching"] = copy.deepcopy(crs["osm_matching"])
    elif tamper == "source_hash":
        crs["osm_matching"]["source_sha256"] = "b" * 64
        reseal_manifest(manifest)
    elif tamper == "nominal_hash":
        crs["osm_matching"]["base"]["nominal_operation_sha256"] = "b" * 64
        reseal_manifest(manifest)
    elif tamper == "unit_scale":
        crs["osm_matching"]["base"]["scale_to_3857"] = 2.0
        reseal_manifest(manifest)
    elif tamper == "missing_pipeline":
        crs["coordinate_operation"].pop("definition")
    elif tamper == "abstained":
        crs["osm_matching"]["status"] = "abstained"
        reseal_manifest(manifest)
    elif tamper == "region":
        crs["osm_matching"]["region_id"] = "OTHER-PANEL"
        reseal_manifest(manifest)
    else:
        crs["unit_crs_contract"]["source_coordinate_scale_reviewed"] = False
    with pytest.raises(UnsupportedAuditTransform):
        inverse_operation(manifest)


def test_legacy_area_hint_alone_cannot_authorize_an_inverse():
    manifest, _ = manifest_fixture()
    crs = manifest["crs"]
    crs.pop("osm_matching")
    crs.pop("lineage")
    crs["coordinate_operation"].pop("osm_matching_sha256")
    with pytest.raises(UnsupportedAuditTransform, match="refuse inferred inversion"):
        inverse_operation(manifest)


def test_explicit_pipeline_is_used_instead_of_selecting_a_new_crs_operation():
    contract = build_unit_crs_contract(6, "EPSG:3857", "EPSG:3857", source_coordinate_scale_reviewed=True)
    manifest = {"crs": {"source_crs": "EPSG:3857", "target_crs": "EPSG:3857",
        "unit_crs_contract": contract.to_manifest_dict(),
        "lineage": {"model": "nominal_direct"},
        "coordinate_operation": {"definition": "+proj=affine +xoff=11 +yoff=-7"}}}
    inverse = inverse_operation(manifest)
    assert inverse(100, 200) == pytest.approx((89, 207))
    assert inverse.forward(89, 207) == pytest.approx((100, 200))


def test_numeric_roundtrip_failure_is_blocked_instead_of_becoming_a_visual_pass():
    def broken_inverse(x, y):
        return x + 1, y
    broken_inverse.forward = lambda x, y: (x, y)
    with pytest.raises(UnsupportedAuditTransform, match="numerical roundtrip"):
        transform_roundtrip_metrics(broken_inverse, [(0, 0)], [(1, 0)], 1.0, 1.0)


@pytest.mark.parametrize("lineage", [None, "unsupported_rubber_sheet", "nominal_direct"])
def test_unknown_missing_or_conflicting_nominal_lineage_is_blocked(lineage):
    manifest, _ = manifest_fixture()
    crs = manifest["crs"]
    crs["lineage"]["model"] = lineage
    crs.pop("osm_matching")
    crs.pop("osm_anchor")
    crs["coordinate_operation"].pop("osm_matching_sha256")
    with pytest.raises(UnsupportedAuditTransform, match="lineage"):
        inverse_operation(manifest)


def test_dropped_height_datum_pipeline_is_inverted_as_the_actual_xy_map():
    # Recorded ID74 -> WGS84 pipeline used by the Lamteh SF run. Formal
    # INVERSE restores the stacked height, so its XY roundtrip is approximate.
    definition = (
        "proj=pipeline step inv proj=utm zone=46 a=6378160 rf=298.247 "
        "step inv proj=longlat a=6378160 rf=298.247 step proj=push v_3 "
        "step proj=cart a=6378160 rf=298.247 step proj=helmert "
        "x=-1.977 y=-13.06 z=-9.993 rx=-0.364 ry=-0.254 rz=-0.689 "
        "s=-1.037 convention=coordinate_frame step inv proj=cart ellps=WGS84 "
        "step proj=pop v_3 step proj=webmerc lat_0=0 lon_0=0 x_0=0 y_0=0 ellps=WGS84"
    )
    contract = build_unit_crs_contract(6, "EPSG:23846", "EPSG:3857", source_coordinate_scale_reviewed=True)
    manifest = {"crs": {"source_crs": "EPSG:23846", "target_crs": "EPSG:3857",
        "unit_crs_contract": contract.to_manifest_dict(), "lineage": {"model": "nominal_direct"},
        "coordinate_operation": {"definition": definition}}}
    projection = Transformer.from_pipeline(definition)
    source = (750640.2059408867, 613213.2658479799)
    target = projection.transform(*source)
    formal_inverse = projection.transform(*target, direction=TransformDirection.INVERSE)
    assert math.dist(source, formal_inverse) > 1e-5
    inverse = inverse_operation(manifest)
    assert math.dist(source, inverse(*target)) < 1e-7
    assert transform_roundtrip_metrics(inverse, [source], [target], 1.0, 1.0)["passed"]


def test_osm_wrapper_inverts_both_actual_xy_datum_legs_for_tinggede():
    # Tinggede has a nominal no-op in ID74 / UTM 50S. Its OSM wrapper's
    # two separate ID74/WGS84 pipelines both drop height; treating them
    # as exact inverses causes ~19 micrometres of error before GEODATA.
    registration = {
        "schema_version": "cad2gis.dwg_geodata_registration.v1",
        "coordinate_system_id": "Indonesian1974.UTM-50S", "target_crs": "EPSG:23890",
        "design_point": [-1195.884827530681, 13721.094607985702],
        "reference_point": [820087.4465519745, 9896357.923879098],
        "horizontal_unit_scale": 1.0, "user_scale_factor": 1.0,
        "north_direction": [0.0, 1.0], "authority": "DWG_DIRECT:GEODATA",
    }
    manifest, canonical = manifest_fixture(source="EPSG:23890", target="EPSG:23890", registration=registration)
    record = manifest["crs"]["osm_matching"]
    record["base"]["reference_latitude"] = -0.9378392412044751
    record["translation_3857"] = [-5.980362997845717, -15.372640974426046]
    reseal_manifest(manifest)
    canonical = osm_matching.OSMMatchingTransformer(manifest["crs"]["osm_matching"], canonical.nominal)
    original = copy.deepcopy(manifest)
    native = [tuple(registration["design_point"]), (-1300.0, 13500.0), (-1000.0, 14000.0)]
    delivered = canonical.points(native)
    to_mercator = Transformer.from_crs("EPSG:23890", "EPSG:3857", always_xy=True)
    from_mercator = Transformer.from_crs("EPSG:3857", "EPSG:23890", always_xy=True)
    x, y = to_mercator.transform(*delivered[0])
    dx, dy = record["translation_3857"]
    approximate_grid = from_mercator.transform(x - dx, y - dy)
    assert math.dist(approximate_grid, registration["reference_point"]) > 1e-5
    inverse = inverse_operation(manifest)
    for point, target in zip(native, delivered):
        assert math.dist(inverse(*target), point) < 1e-7
        assert math.dist(inverse.forward(*point), target) < 1e-7
    metrics = transform_roundtrip_metrics(inverse, native, delivered, 1.0, 1.0)
    assert metrics["numeric_tolerance_m"] == 1e-5
    assert metrics["passed"] is True
    assert manifest == original


def test_recorded_relocation_is_still_listed_for_engineering_review():
    comparison = {"status": "explicit_derived_displacement", "layer": "BOITE", "source_entity_key": "asset",
                  "source_handle": "1348", "hausdorff_native_m": 3.696544493,
                  "distance_basis": "source_Point_to_delivery_Point"}
    values = [{"fid": 3, "properties": {"CODE": "KTK5.086.A04", "lineage_json": json.dumps([
        {"operation": "collocate_with_support", "support_handle": "12A8", "max_displacement_m": 3.696544492},
    ])}}]
    review = geometry_change_review_rows([comparison], {("BOITE", "asset"): values})[0]
    assert review["operations"] == "collocate_with_support"
    assert review["support_handles"] == "12A8"
    assert review["source_relative_displacement_m"] == 3.696544493
    assert review["declared_requires_review"] is None
    assert review["declared_review_status"] == "not_recorded"
    assert review["audit_requires_review"] is True
    assert review["engineering_acceptance"] == "not_established_by_lineage"
