from __future__ import annotations

import json

import pytest

from cad2gis import agent_mcp
from cad2gis.geometry_review import list_geometry_changes, record_geometry_decision


def _audit(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text('{"run_status": "CONDITIONAL"}', encoding="utf-8")
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "report.json").write_text(json.dumps({
        "run_dir": str(run), "source_sha256": "a" * 64,
        "explicit_geometry_changes": [
            {"layer": "ZNRO", "source_entity_key": "k1", "source_handle": "18837E",
             "operations": "repair_boundary_polygon", "status": "explicit_derived_displacement",
             "hausdorff_native_m": 97.7, "point_anchor_displacement_m": None},
            {"layer": "BOITE", "source_entity_key": "k2", "source_handle": "4D210",
             "operations": "collocate_with_support|identity", "status": "explicit_derived_displacement",
             "hausdorff_native_m": 12.18, "point_anchor_displacement_m": 12.18},
        ],
        "derived_without_direct_source": [
            {"layer": "ZNRO", "source_entity_key": "k3", "source_handle": "ZPM-COMPONENT-2",
             "operations": "conservative_znro_component", "status": "derived_without_direct_source"},
        ],
    }), encoding="utf-8")
    return run, audit


def test_changes_are_ranked_with_operation_specific_questions(tmp_path):
    _, audit = _audit(tmp_path)
    review = list_geometry_changes(audit, tmp_path / "workspace")
    assert review["change_count"] == 3
    assert review["decision_counts"]["undecided"] == 3
    first, second, derived = review["changes"]
    assert (first["source_handle"], second["source_handle"]) == ("18837E", "4D210")
    assert "region" in first["question"] and "support point" in second["question"]
    assert derived["kind"] == "derived_without_direct_source"
    assert "no complete polygon" in derived["question"]
    assert len({item["change_id"] for item in review["changes"]}) == 3


def test_latest_decision_wins_and_never_claims_accuracy(tmp_path):
    _, audit = _audit(tmp_path)
    workspace = tmp_path / "workspace"
    change = list_geometry_changes(audit, workspace)["changes"][0]["change_id"]
    first = record_geometry_decision(audit, workspace, change, "accept", reviewer="r", note="ok")
    assert first["absolute_accuracy_verified"] is False and first["receipt_sha256"]
    record_geometry_decision(audit, workspace, change, "defer", note="needs site visit")
    review = list_geometry_changes(audit, workspace)
    assert review["decision_counts"] == {"accept": 0, "defer": 1, "reject": 0, "undecided": 2}
    assert len((workspace / "geometry_decisions.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_decisions_are_bound_to_the_audited_run(tmp_path):
    run, audit = _audit(tmp_path)
    workspace = tmp_path / "workspace"
    change = list_geometry_changes(audit, workspace)["changes"][0]["change_id"]
    with pytest.raises(ValueError, match="decision must be"):
        record_geometry_decision(audit, workspace, change, "approve")
    with pytest.raises(ValueError, match="does not belong"):
        record_geometry_decision(audit, workspace, "0" * 64, "accept")
    with pytest.raises(ValueError, match="outside the immutable run"):
        record_geometry_decision(audit, run / "review", change, "accept")
    record_geometry_decision(audit, workspace, change, "accept")
    # A different run manifest is a different run: old decisions do not carry over.
    (run / "run_manifest.json").write_text('{"run_status": "VERIFIED"}', encoding="utf-8")
    review = list_geometry_changes(audit, workspace)
    assert review["decision_counts"]["undecided"] == 3
    assert change not in {item["change_id"] for item in review["changes"]}


def test_mcp_wrappers_enforce_project_roots(tmp_path, monkeypatch, tmp_path_factory):
    _, audit = _audit(tmp_path)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    review = agent_mcp.list_geometry_changes(str(audit), str(tmp_path / "workspace"))
    receipt = agent_mcp.record_geometry_decision(
        str(audit), str(tmp_path / "workspace"), review["changes"][1]["change_id"], "reject")
    assert receipt["decision"] == "reject"
    outside = tmp_path_factory.mktemp("outside")
    with pytest.raises(agent_mcp.MCPServiceError) as error:
        agent_mcp.list_geometry_changes(str(audit), str(outside / "workspace"))
    assert error.value.code == "PATH_OUTSIDE_ROOT"
    with pytest.raises(agent_mcp.MCPServiceError) as error:
        agent_mcp.record_geometry_decision(str(audit), str(tmp_path / "workspace"), "bad", "accept")
    assert error.value.code == "VALIDATION_FAILED"
