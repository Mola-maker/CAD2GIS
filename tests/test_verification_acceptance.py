"""Engineering gates constrain overall claims without erasing measured facts."""

import json

import pytest

from cad2gis.verify import evaluate_matrix, strongest_allowed_claim
from cad2gis.verify.claims import CLAIM_SINGLE_ABSOLUTE
from test_verification_matrix import _architecture_artifacts, _sample


def accepted_sample(tmp_path):
    sample = _sample("reviewed", "architecture-source", absolute=True)
    sample["artifacts"] = _architecture_artifacts(tmp_path)
    sample["architecture"] = {"review_isolation": {
        "status": "PASS", "evidence_ref": "synthetic-reviewed-workspace",
        "immutable_run_artifacts": True, "separate_workspace": True,
        "optimistic_revisions": True,
    }}
    sample["run_status"] = "VERIFIED"
    return sample


def evaluate(tmp_path, sample):
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(sample), encoding="utf-8")
    before = path.read_bytes()
    report = evaluate_matrix(path)
    assert path.read_bytes() == before
    return report


@pytest.mark.parametrize("name", ["reviewer_acceptance", "engineering_review"])
@pytest.mark.parametrize("passed,expected", [(None, "WATCH"), (False, "FAIL")])
def test_unverified_or_failed_gate_blocks_overall_without_erasing_spatial_facts(
    tmp_path, name, passed, expected,
):
    sample = accepted_sample(tmp_path)
    sample["validation"][name] = {
        # Even an inconsistent optimistic status cannot override null/false.
        "status": "passed", "passed": passed, "unresolved_count": 1,
    }
    report = evaluate(tmp_path, sample)
    row = report["samples"][0]
    assert all(value == "PASS" for value in row["dimensions"].values())
    assert report["status"] == expected
    assert report["acceptance"]["status"] == expected
    assert row["acceptance"]["gates"][name]["evidence"]["passed"] is passed
    assert report["claim"].startswith(CLAIM_SINGLE_ABSOLUTE)
    assert "Engineering acceptance is not established" in report["claim"]


def test_legacy_missing_gate_evidence_is_reported_without_discarding_verified_dimensions(tmp_path):
    sample = accepted_sample(tmp_path)
    del sample["validation"]
    del sample["run_status"]
    report = evaluate(tmp_path, sample)
    row = report["samples"][0]
    assert report["status"] == "WATCH"
    assert all(value == "PASS" for value in row["dimensions"].values())
    for gate in row["acceptance"]["gates"].values():
        assert gate["status"] == "WATCH"
        assert gate["evidence_provided"] is False
        assert gate["reason"] == "evidence_not_provided"
    assert report["claim"].startswith(CLAIM_SINGLE_ABSOLUTE)
    assert "gate evidence was not provided" in report["claim"]


@pytest.mark.parametrize("run_status,expected", [("CONDITIONAL", "WATCH"), ("UNSAFE", "FAIL")])
def test_pipeline_run_status_cannot_be_upgraded_by_passing_dimension_and_acceptance_gates(
    tmp_path, run_status, expected,
):
    sample = accepted_sample(tmp_path)
    sample["run_status"] = run_status
    report = evaluate(tmp_path, sample)
    assert report["status"] == expected
    assert report["samples"][0]["acceptance"]["run_status"] == run_status
    assert "Engineering acceptance is not established" in report["claim"]


def test_complete_acceptance_keeps_existing_narrow_claim(tmp_path):
    report = evaluate(tmp_path, accepted_sample(tmp_path))
    assert report["status"] == "PASS"
    assert report["acceptance"]["status"] == "PASS"
    assert report["claim"] == CLAIM_SINGLE_ABSOLUTE


def test_conflicting_top_level_gate_cannot_hide_a_failed_manifest_gate(tmp_path):
    sample = accepted_sample(tmp_path)
    sample["validation"]["engineering_review"] = {"passed": False}
    sample["engineering_review"] = {"passed": True}
    report = evaluate(tmp_path, sample)
    assert report["status"] == "FAIL"
    assert report["acceptance"]["status"] == "FAIL"
    assert "an acceptance gate or run status failed" in report["claim"]


def test_direct_claim_caller_cannot_use_an_aggregate_pass_to_hide_missing_gates():
    report = {"acceptance": {"status": "PASS"}, "samples": [{
        "evaluated": True, "input_verified": True, "input_sha256": "a" * 64,
        "dimensions": {name: "PASS" for name in (
            "geometry", "topology", "semantics", "style", "length", "nominal_crs",
        )},
        "acceptance": {"status": "PASS"},
    }]}
    claim = strongest_allowed_claim(report)
    assert claim.startswith("Single-input source-fidelity and nominal CRS transform verified")
    assert "gate evidence was not provided" in claim
