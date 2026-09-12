"""Native recovery is exact-pipeline, source-bound, and relative-only."""

import copy
import hashlib
import json

import pytest
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

from cad2gis.cad2gis_v3.units import build_unit_crs_contract
from cad2gis.reverse_models import GISDataset, GISFeature, ReverseConversionError
from cad2gis.reverse_registration import load_registration
from test_visual_audit_transforms import manifest_fixture, reseal_manifest


def direct_manifest(*, scale=1.0, insunits=6):
    contract = build_unit_crs_contract(insunits, "EPSG:3857", "EPSG:3857",
        source_coordinate_scale_to_m=scale, source_coordinate_scale_reviewed=True).to_manifest_dict()
    return {"crs": {"source_crs": "EPSG:3857", "target_crs": "EPSG:3857",
        "unit_crs_contract": contract, "lineage": {"model": "nominal_direct"},
        "calibration": {"status": "not_provided"},
        "coordinate_operation": {"definition": "+proj=affine +xoff=11 +yoff=-7"}}}


def bound_input(tmp_path, manifest=None, geometry=None, *, crs="EPSG:3857", partition=None):
    manifest = copy.deepcopy(manifest if manifest is not None else direct_manifest())
    source = tmp_path / "delivery.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    artifact = {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    manifest.update(schema_version="cad2gis-run-manifest-v4", pipeline="cad2gis-reviewed-project-evidence-first-v4")
    manifest["source"] = {"path": "drawing.dwg", "sha256": "a" * 64}
    manifest["artifacts"] = {"delivery": artifact}
    if partition:
        manifest["artifacts"]["delivery"] = {"path": "main.gpkg", "sha256": "c" * 64}
        manifest["delivery_partitions"] = {partition: artifact}
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    dataset = GISDataset([GISFeature("1", "features", geometry if geometry is not None else Point(100, 200, 3.5))], {"features": crs})
    return path, source, dataset, manifest


def rewrite(path, manifest):
    path.write_text(json.dumps(manifest), encoding="utf-8")


def assert_invalid(path, source, dataset, **kwargs):
    with pytest.raises(ReverseConversionError) as caught:
        load_registration(path, source, dataset, **kwargs)
    assert caught.value.code == "REVERSE_REGISTRATION_INVALID"
    return str(caught.value)


def test_declared_affine_recovery_and_byte_bound_metadata(tmp_path):
    path, source, dataset, _ = bound_input(tmp_path)
    result = load_registration(path, source, dataset)
    point = result.transform_geometry(dataset.features[0].geometry)
    assert tuple(point.coords)[0] == pytest.approx((89, 207, 3.5))
    assert result.forward_geometry(point).equals_exact(dataset.features[0].geometry, 1e-12)
    assert result.units == 6
    assert result.metadata["manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.metadata["source_gis_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.metadata["source_cad_sha256"] == "a" * 64
    assert result.metadata["source_cad_bytes_verified"] is False
    assert result.metadata["original_cad_restored"] is False
    assert result.metadata["absolute_accuracy_verified"] is False
    assert result.metadata["roundtrip"]["passed"] is True


def test_native_units_follow_reviewed_coordinate_scale_not_target_or_insertion_units(tmp_path):
    path, source, dataset, _ = bound_input(tmp_path, direct_manifest(scale=0.001, insunits=6))
    result = load_registration(path, source, dataset)
    point = result.transform_geometry(dataset.features[0].geometry)
    assert tuple(point.coords)[0] == pytest.approx((89000, 207000, 3.5))
    assert result.units == 4
    assert result.unit_report["metres_per_unit"] == 0.001
    assert result.unit_report["source_geometry_unit"]["insunits"] == 6


def test_nested_polygons_holes_and_mixed_dimensions_keep_z(tmp_path):
    shape = GeometryCollection([Point(20, 30, -4), LineString([(40, 50), (60, 70)]),
        Polygon([(11, 11, 2), (21, 11, 2), (21, 21, 2), (11, 21, 2)],
                [[(13, 13, 2), (13, 15, 2), (15, 15, 2), (15, 13, 2)]])])
    path, source, dataset, _ = bound_input(tmp_path, geometry=shape)
    result = load_registration(path, source, dataset)
    native = result.transform_geometry(shape)
    assert native.geoms[0].z == -4
    assert not native.geoms[1].has_z
    assert len(native.geoms[2].interiors) == 1
    assert all(coordinate[2] == 2 for coordinate in native.geoms[2].interiors[0].coords)
    assert result.forward_geometry(native).equals_exact(shape, 1e-12)


@pytest.mark.parametrize("tamper", ["source_hash", "origin_hash", "origin_path", "schema", "source_only", "contract_schema", "missing_pipeline", "unreviewed_scale", "unit_scale", "unit_code", "source_crs", "target_crs", "axis_scale", "singular", "calibration"])
def test_missing_conflicting_or_unsupported_facts_fail_closed(tmp_path, tamper):
    path, source, dataset, manifest = bound_input(tmp_path)
    crs = manifest["crs"]
    contract = crs["unit_crs_contract"]
    if tamper == "source_hash":
        source.write_text("changed GIS snapshot", encoding="utf-8")
    elif tamper == "origin_hash":
        manifest["source"]["sha256"] = "unknown"
    elif tamper == "origin_path":
        manifest["source"].pop("path")
    elif tamper == "schema":
        manifest["schema_version"] = "cad2gis-run-manifest-v99"
    elif tamper == "source_only":
        manifest["pipeline"] = "cad2gis-source-export"
    elif tamper == "contract_schema":
        contract["schema_version"] = "unknown"
    elif tamper == "missing_pipeline":
        crs["coordinate_operation"].pop("definition")
    elif tamper == "unreviewed_scale":
        contract["source_coordinate_scale_reviewed"] = False
    elif tamper == "unit_scale":
        contract["source_coordinate_scale_to_m"] = 0
    elif tamper == "unit_code":
        contract["source_geometry_unit"]["insunits"] = 4
    elif tamper == "source_crs":
        contract["source_crs"] = "EPSG:32649"
    elif tamper == "target_crs":
        contract["target_crs"] = "EPSG:32649"
    elif tamper == "axis_scale":
        contract["target_crs_axis_unit"]["metres_per_unit"] = 0.001
    elif tamper == "singular":
        crs["coordinate_operation"]["definition"] = "+proj=affine +s11=0 +s22=0"
    else:
        crs["calibration"]["status"] = "applied"
    rewrite(path, manifest)
    assert_invalid(path, source, dataset)


@pytest.mark.parametrize("crs", [None, "EPSG:4326", "EPSG:32649"])
def test_gis_crs_must_equal_delivery_crs(tmp_path, crs):
    path, source, dataset, _ = bound_input(tmp_path, crs=crs)
    assert "CRS differs" in assert_invalid(path, source, dataset)


def test_duplicate_manifest_keys_are_not_accepted(tmp_path):
    path, source, dataset, _ = bound_input(tmp_path)
    path.write_text('{"source": {}, "source": {}}', encoding="utf-8")
    assert "Duplicate" in assert_invalid(path, source, dataset)


@pytest.mark.parametrize("selection", [None, "wrong"])
def test_partition_delivery_requires_exact_explicit_selection(tmp_path, selection):
    path, source, dataset, _ = bound_input(tmp_path, partition="SECOND")
    assert_invalid(path, source, dataset, partition=selection)


def test_direct_partition_and_scoped_run_bind_explicitly(tmp_path):
    path, source, dataset, manifest = bound_input(tmp_path, partition="SECOND")
    result = load_registration(path, source, dataset, partition="SECOND")
    assert result.metadata["partition"] == "SECOND"
    manifest["artifacts"]["delivery"] = manifest["delivery_partitions"]["SECOND"]
    manifest["delivery_partition"] = {"region_id": "SECOND"}
    manifest["delivery_partitions"] = {}
    rewrite(path, manifest)
    assert_invalid(path, source, dataset)
    assert load_registration(path, source, dataset, partition="SECOND").metadata["partition"] == "SECOND"


@pytest.mark.parametrize("scale", [1.0, 0.001])
@pytest.mark.parametrize("local", [False, True])
def test_geodata_and_local_sealed_osm_match_independent_numerical_controls(tmp_path, scale, local):
    manifest, canonical = manifest_fixture(scale=scale, local=local)
    native_points = [(0, 0), (120 / scale, -90 / scale), (100 / scale, 45 / scale)]
    delivered = LineString([(*canonical.point(point), index + 0.125) for index, point in enumerate(native_points)])
    path, source, dataset, _ = bound_input(tmp_path, manifest, delivered)
    result = load_registration(path, source, dataset)
    native = result.transform_geometry(delivered)
    for expected, actual in zip(native_points, native.coords):
        assert actual[:2] == pytest.approx(expected, abs=3e-6 / scale)
    assert [c[2] for c in native.coords] == [0.125, 1.125, 2.125]
    assert result.metadata["authority"] == "relative_osm"
    assert result.metadata["absolute_accuracy_verified"] is False
    assert result.metadata["osm_relative_inverse_applied"] is True
    assert result.metadata["roundtrip"]["max_native_residual_m"] <= 1e-5
    assert result.metadata["roundtrip"]["max_target_grid_residual_m"] <= 1e-5


def test_nominal_geodata_without_osm_uses_declared_control(tmp_path):
    manifest, canonical = manifest_fixture()
    manifest.pop("osm_matching")
    crs = manifest["crs"]
    crs.pop("osm_matching")
    crs.pop("osm_matching_by_region")
    crs["coordinate_operation"].pop("osm_matching_sha256")
    crs["lineage"]["model"] = "dwg_geodata_nominal"
    point = (42, -101)
    path, source, dataset, _ = bound_input(tmp_path, manifest, Point(*canonical.nominal.point(point), 11))
    result = load_registration(path, source, dataset)
    assert tuple(result.transform_geometry(dataset.features[0].geometry).coords)[0] == pytest.approx((*point, 11), abs=3e-6)
    assert result.metadata["geodata_inverse_applied"] is True
    assert result.metadata["osm_relative_inverse_applied"] is False


@pytest.mark.parametrize("tamper", ["unsealed", "origin_binding", "geodata_schema", "region"])
def test_osm_and_geodata_binding_failures_are_rejected(tmp_path, tamper):
    manifest, canonical = manifest_fixture()
    crs = manifest["crs"]
    if tamper == "unsealed":
        crs["osm_matching"]["translation_3857"][0] += 10
    elif tamper == "origin_binding":
        crs["osm_matching"]["source_sha256"] = "b" * 64
        reseal_manifest(manifest)
    elif tamper == "geodata_schema":
        crs["unit_crs_contract"]["coordinate_registration"]["schema_version"] = "unrecognized"
    else:
        crs["osm_matching"]["region_id"] = "SECOND"
        reseal_manifest(manifest)
    path, source, dataset, _ = bound_input(tmp_path, manifest, Point(canonical.point((10, 20))))
    assert_invalid(path, source, dataset)


def test_partition_osm_uses_own_shift_and_rejects_conflicting_artifact_record(tmp_path):
    from cad2gis.cad2gis_v3 import osm_matching

    manifest, canonical = manifest_fixture()
    record = copy.deepcopy(manifest["crs"]["osm_matching"])
    record.update(region_id="SECOND", translation_3857=[40, -13])
    record = osm_matching._seal(record)
    manifest["crs"]["osm_matching_by_region"]["SECOND"] = record
    selected = osm_matching.OSMMatchingTransformer(record, canonical.nominal)
    path, source, dataset, manifest = bound_input(tmp_path, manifest, Point(selected.point((10, 20))), partition="SECOND")
    result = load_registration(path, source, dataset, partition="SECOND")
    assert tuple(result.transform_geometry(dataset.features[0].geometry).coords)[0] == pytest.approx((10, 20), abs=3e-6)
    assert result.metadata["osm_translation_3857"] == [40, -13]
    manifest["delivery_partitions"]["SECOND"]["osm_matching"] = manifest["crs"]["osm_matching"]
    rewrite(path, manifest)
    assert_invalid(path, source, dataset, partition="SECOND")


def test_failed_numerical_closure_is_rejected(tmp_path, monkeypatch):
    path, source, dataset, _ = bound_input(tmp_path)
    def broken(x, y):
        return x + 1, y
    broken.forward = lambda x, y: (x, y)
    broken.metadata = {}
    monkeypatch.setattr("cad2gis.reverse_registration.inverse_operation", lambda *args, **kwargs: broken)
    assert "roundtrip exceeds" in assert_invalid(path, source, dataset)


def test_registration_does_not_need_gdal_import(tmp_path):
    import sys

    path, source, dataset, _ = bound_input(tmp_path)
    before = {name for name in sys.modules if name == "osgeo" or name.startswith("osgeo.")}
    load_registration(path, source, dataset)
    assert {name for name in sys.modules if name == "osgeo" or name.startswith("osgeo.")} == before
