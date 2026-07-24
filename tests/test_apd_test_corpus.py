"""Governance and source-binding checks for the APD robustness corpus."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = ROOT / "APD_test"
DATASET_MANIFEST = DATASET_ROOT / "dataset_manifest.json"
LEGACY_PROFILE_SHA = "7bbadb28f01d797ff9404e34c3c04c1cffe7f70c92d20b87df50fffb10120c30"
LEGACY_READCAD_DWG_SHA = "557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557"
ALLOWED_TEST_ROLES = {
    "reader",
    "crash_resistance",
    "generic_fidelity",
    "determinism",
    "performance",
}
EXPECTED_CASES = {
    "lamteh_dayah_aceh_sf": {
        "source": "APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg",
        "sha256": "5B0872935A070CAC9B3969811799D229BB48E534085254930263D526DB9C5735",
        "size_bytes": 4_848_807,
        "contract": "cases/lamteh_dayah_aceh_sf.json",
    },
    "lamteh_dayah_aceh": {
        "source": "APD - KELURAHAN LAMTEH DAYAH ACEH.dwg",
        "sha256": "D71AD87E6C0C81811725CF6238DD883621F0D09C03815C81EEBD067EEC1F8E78",
        "size_bytes": 5_253_009,
        "contract": "cases/lamteh_dayah_aceh.json",
    },
    "kletek_rw05_sidoarjo": {
        "source": "KLETEK RW 05 SIDOARJO.dwg",
        "sha256": "756F6E5BB4FEB61FED2D86046478E0FB714434628BD0F17DB4D78FEF0B221016",
        "size_bytes": 308_753,
        "contract": "cases/kletek_rw05_sidoarjo.json",
    },
}
NOT_AVAILABLE_FIELDS = {
    "inventory",
    "reader_diagnostics",
    "crs",
    "units",
    "gcp",
    "expected_business_output",
    "machine_generated_profile",
    "reviewed_profile",
}


def _load_manifest() -> dict:
    assert DATASET_MANIFEST.is_file(), "APD_test dataset governance manifest is missing"
    return json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))


def test_dataset_manifest_declares_governance_and_claim_boundary() -> None:
    manifest = _load_manifest()
    assert manifest["schema_version"] == "cad2gis-test-dataset-v1"
    assert manifest["dataset_id"] == "apd_test_robustness_v1"
    assert manifest["provenance"] == "user_supplied"
    assert manifest["license_status"] == "not_documented"
    assert manifest["handling_status"] == "repository_test_asset"
    claim_boundary = manifest["claim_boundary"]
    assert claim_boundary["truth_status"] == "not_established"
    assert set(claim_boundary["allowed_test_roles"]) == ALLOWED_TEST_ROLES
    assert claim_boundary["domain_accuracy"] == "not_claimed"
    assert claim_boundary["absolute_accuracy"] == "not_claimed"


def test_apd_case_is_bound_to_exact_source_bytes_and_sidecar() -> None:
    manifest = _load_manifest()
    cases = {case["case_id"]: case for case in manifest["cases"]}
    assert set(cases) == set(EXPECTED_CASES)
    forbidden = {LEGACY_PROFILE_SHA, LEGACY_READCAD_DWG_SHA}

    for case_id, expected in EXPECTED_CASES.items():
        case = cases[case_id]
        assert case["source"] == expected["source"]
        assert case["sha256"] == expected["sha256"]
        assert case["size_bytes"] == expected["size_bytes"]
        assert case["dwg_version"] == "AC1032"
        assert case["contract"] == expected["contract"]
        assert case["sha256"].lower() not in forbidden

        source = DATASET_ROOT / case["source"]
        assert source.is_file()
        payload = source.read_bytes()
        assert len(payload) == case["size_bytes"]
        assert hashlib.sha256(payload).hexdigest().upper() == case["sha256"]

        contract_path = DATASET_ROOT / case["contract"]
        assert contract_path.is_file()
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        assert contract["schema_version"] == "cad2gis-test-case-v1"
        assert contract["case_id"] == case_id
        assert contract["source"] == case["source"]
        assert contract["source_sha256"] == case["sha256"]
        assert contract["size_bytes"] == case["size_bytes"]
        assert contract["dwg_version"] == case["dwg_version"]
        assert contract["truth_status"] == "not_established"
        assert set(contract["allowed_test_roles"]) == ALLOWED_TEST_ROLES
        assert contract["claim_boundary"]["domain_accuracy"] == "not_claimed"
        assert contract["claim_boundary"]["absolute_accuracy"] == "not_claimed"
        availability = contract["availability"]
        assert set(availability) == NOT_AVAILABLE_FIELDS
        assert all(value == "not_available" for value in availability.values())


def test_new_cases_do_not_reuse_legacy_hutabohu_bindings() -> None:
    manifest = _load_manifest()
    forbidden = {LEGACY_PROFILE_SHA, LEGACY_READCAD_DWG_SHA}
    assert forbidden.isdisjoint({case["sha256"].lower() for case in manifest["cases"]})
