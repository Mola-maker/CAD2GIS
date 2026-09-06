import hashlib
import json

import pytest

from cad2gis.cad2gis_v3.onboarding import _crs_candidates


def profile(identifier="UTM84-50S", target="EPSG:32750"):
    return {"drawing": {"dwg_cgeocs": identifier, "dwg_insunits": 6}, "crs": {
        "geodata_registration": {"schema_version": "cad2gis.dwg_geodata_registration.v1",
            "coordinate_system_id": identifier, "target_crs": target, "authority": "DWG_DIRECT:GEODATA",
            "design_point": [1, 2], "reference_point": [300000, 9900000], "north_direction": [0, 1],
            "horizontal_unit_scale": 1, "user_scale_factor": 1}}}


def test_native_geodata_epsg_alias_admits_unlisted_projected_crs_without_ai_guess():
    candidate, = _crs_candidates(profile())
    assert candidate["source_crs"] == candidate["target_crs"] == "EPSG:32750"
    assert candidate["evidence"]["authority"] == "DWG_DIRECT:GEODATA"
    assert candidate["geodata_registration"]["reference_point"] == [300000, 9900000]


@pytest.mark.parametrize("change", ["no_geodata", "other_authority", "other_identifier", "geographic", "known_crs_conflict"])
def test_geodata_candidates_reject_missing_or_contradictory_authority(change):
    value = profile()
    geo = value["crs"]["geodata_registration"]
    if change == "no_geodata":
        value["crs"] = {}
    elif change == "other_authority":
        geo["authority"] = "AI_GUESS"
    elif change == "other_identifier":
        geo["coordinate_system_id"] = "OTHER"
    elif change == "geographic":
        geo["target_crs"] = "EPSG:4326"
    else:
        value["drawing"]["dwg_cgeocs"] = geo["coordinate_system_id"] = "UTM84-49S"
    assert _crs_candidates(value) == []


@pytest.mark.parametrize("tamper", ["crs", "geodata_reference", "insunits"])
def test_bundle_rejects_profile_edits_instead_of_trusting_claimed_reader_authority(tmp_path, tamper):
    from cad2gis.cad2gis_v3.onboarding import OnboardingError, prepare_onboarding_bundle
    from cad2gis.cad2gis_v3.project_profile import bootstrap_project

    source = tmp_path / "native.dwg"
    source.write_bytes(b"source-bound-metadata-fixture")
    record = {"entity_key": "metadata", "handle": "metadata",
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "dwg_type_name": "DOCUMENT_METADATA", "layout": "Document", "cad_role": "metadata",
              "text": "CGEOCS=UTM84-50S;INSUNITS=6", "points": [],
              "raw_properties": {"geodata_registration": profile()["crs"]["geodata_registration"]}}
    root = tmp_path / "project"
    bootstrap_project(source=source, project_dir=root, records=[record])
    original = prepare_onboarding_bundle(project_dir=root)
    assert original["crs_candidates"][0]["source_crs"] == "EPSG:32750"
    inventory_path = root / "review/source_inventory.json"
    inventory_before = inventory_path.read_bytes()
    path = root / "config/source_profile.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "crs":
        value["drawing"]["dwg_cgeocs"] = "FAKE"
        value["crs"]["geodata_registration"]["coordinate_system_id"] = "FAKE"
        value["crs"]["geodata_registration"]["target_crs"] = "EPSG:32749"
    elif tamper == "geodata_reference":
        value["crs"]["geodata_registration"]["reference_point"][0] += 1000
    else:
        value["drawing"]["dwg_insunits"] = 4
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(OnboardingError, match="differs from source inventory"):
        prepare_onboarding_bundle(project_dir=root)
    assert inventory_path.read_bytes() == inventory_before
