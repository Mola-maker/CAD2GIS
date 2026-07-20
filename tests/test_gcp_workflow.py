"""Contracts for the canonical, user-facing GCP workflow adapter.

The adapter is intentionally tested with a tiny fake operator backend.  This
keeps these tests independent of GDAL/OGR while checking the boundary that
the CLI and QGIS integrations consume: lazy loading, fail-closed status,
provenance policy, and copyable operator actions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cad2gis.gcp_workflow as workflow


class _Backend:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.calls: list[tuple[str, dict[str, object]]] = []

    def prepare_capture(self, **kwargs):
        self.calls.append(("prepare_capture", kwargs))
        return {
            "capture": str(kwargs["output_path"]),
            "capture_sha256": "capture-hash",
            "candidate_count": 2,
            "publication_changed": False,
        }

    def diagnose_capture(self, **kwargs):
        self.calls.append(("diagnose_capture", kwargs))
        return {
            "report": str(kwargs["report_path"]),
            "report_sha256": "report-hash",
            "active_train_count": 3,
            "active_check_count": 2,
            "available_models": ["translation", "similarity"],
            "diagnostic_only": True,
            "publication_changed": False,
            "reference_scope": "relative_osm_reference_only",
        }

    def export_profile(self, **kwargs):
        self.calls.append(("export_profile", kwargs))
        profile_path = Path(kwargs["output_path"])
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "controls": [
                        {
                            "point_id": f"P-{index}",
                            "cad_x": index + 1,
                            "cad_y": index + 2,
                            "target_easting": index + 3,
                            "target_northing": index + 4,
                            "role": "train" if index < 3 else "check",
                            "source": (
                                "RELATIVE_OSM_REFERENCE_ONLY | snapshot"
                                if index == 5
                                else "AUTHORITATIVE_CONTROL | survey"
                            ),
                            "accuracy_m": 1,
                            "weight": 1,
                            "enabled": True,
                        }
                        for index in range(6)
                    ],
                }
            ),
            encoding="utf-8",
        )
        return {
            "profile": str(profile_path),
            "profile_sha256": "profile-hash",
            "enabled": True,
            "validation_passed": True,
            "reference_scope": "relative_osm_reference_only",
            "publication_changed": False,
        }


def test_status_missing_project_is_lazy_and_fail_closed(monkeypatch, tmp_path):
    def backend_must_not_load():
        raise AssertionError("status must not import the GDAL operator backend")

    monkeypatch.setattr(workflow, "_load_backend", backend_must_not_load)
    result = workflow.status_project(tmp_path / "does-not-exist")

    assert result["status"] == "blocked"
    assert result["absolute_accuracy_validation"] == "not_verified"
    assert result["authority"]["absolute_train_and_check_ready"] is False
    assert any("cad2gis gcp prepare" in action for action in result["next_actions"])


def test_status_malformed_manifest_is_structured(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow, "_load_backend", lambda: pytest.fail("must stay lazy"))
    (tmp_path / "run_manifest.json").write_text("{not-json", encoding="utf-8")

    result = workflow.status_project(tmp_path)

    assert result["status"] == "blocked"
    assert result["absolute_accuracy_validation"] == "not_verified"
    assert any("run manifest is not valid JSON" in item for item in result["blockers"])


def test_prepare_normalises_backend_result_and_never_claims_absolute_accuracy(
    monkeypatch, tmp_path
):
    backend = _Backend(tmp_path)
    monkeypatch.setattr(workflow, "_load_backend", lambda: backend)
    request = workflow.PrepareRequest(
        delivery_path=tmp_path / "delivery.gpkg",
        evidence_path=tmp_path / "evidence.gpkg",
        manifest_path=tmp_path / "run_manifest.json",
        output_path=tmp_path / "project" / "gcp_capture.gpkg",
        candidate_layers=("ptech", "PTECH", "site"),
    )

    result = workflow.prepare_capture(request)

    assert result["operation"] == "prepare"
    assert result["status"] == "blocked"
    assert result["absolute_accuracy_validation"] == "not_verified"
    assert result["artifacts"]["capture_sha256"] == "capture-hash"
    assert "surveyed" in result["operator_actions"][0]
    assert any("cad2gis gcp diagnose" in action for action in result["next_actions"])
    assert backend.calls[0][1]["candidate_layers"] == ("PTECH", "SITE")
    assert result["input_policy"]["adapter_generates_control_values"] is False


def test_diagnose_keeps_relative_osm_visual_reference_non_absolute(monkeypatch, tmp_path):
    backend = _Backend(tmp_path)
    monkeypatch.setattr(workflow, "_load_backend", lambda: backend)

    result = workflow.diagnose_capture(
        workflow.DiagnoseRequest(
            capture_path=tmp_path / "capture.gpkg",
            report_path=tmp_path / "diagnostic.json",
        )
    )

    assert result["status"] == "not_verified"
    assert result["absolute_accuracy_validation"] == "not_verified"
    assert workflow.RELATIVE_OSM_WARNING in result["warnings"]
    assert workflow.RELATIVE_OSM_WARNING in result["blockers"]
    assert result["authority"]["contains_relative_osm"] is True
    assert any("cad2gis gcp export" in action for action in result["next_actions"])


def test_diagnose_without_models_is_blocked_even_with_minimum_counts(
    monkeypatch, tmp_path
):
    class NoModelBackend(_Backend):
        def diagnose_capture(self, **kwargs):
            return {
                "report": str(kwargs["report_path"]),
                "active_train_count": 3,
                "active_check_count": 2,
                "available_models": [],
                "reference_scope": "surveyed_or_authoritative",
            }

    monkeypatch.setattr(workflow, "_load_backend", lambda: NoModelBackend(tmp_path))
    result = workflow.diagnose_capture(
        capture_path=tmp_path / "capture.gpkg",
        report_path=tmp_path / "diagnostic.json",
    )

    assert result["status"] == "blocked"
    assert any("No calibration model" in item for item in result["blockers"])


def test_export_marks_osm_profile_not_verified_and_returns_copyable_commands(
    monkeypatch, tmp_path
):
    backend = _Backend(tmp_path)
    monkeypatch.setattr(workflow, "_load_backend", lambda: backend)
    result = workflow.export_profile(
        capture_path=tmp_path / "capture.gpkg",
        template_profile_path=tmp_path / "template.json",
        output_path=tmp_path / "reviewed.json",
        enable=True,
        allow_relative_osm=True,
    )

    assert result["status"] == "not_verified"
    assert result["absolute_accuracy_validation"] == "not_verified"
    assert workflow.RELATIVE_OSM_WARNING in result["warnings"]
    assert result["authority"]["relative_osm_count"] == 1
    assert any("cad2gis convert" in action for action in result["next_actions"])
    convert_action = next(
        action for action in result["next_actions"] if "cad2gis convert" in action
    )
    assert '"<SOURCE.dwg>"' in convert_action
    assert '"<NEW_RUN_DIR>"' in convert_action
    assert any("cad2gis gcp status" in action for action in result["next_actions"])


def test_backend_errors_are_structured_unless_debug_requested(monkeypatch, tmp_path):
    def failing_backend():
        class Broken:
            def prepare_capture(self, **kwargs):
                raise KeyError("malformed capture field")

        return Broken()

    monkeypatch.setattr(workflow, "_load_backend", failing_backend)
    kwargs = {
        "delivery_path": tmp_path / "delivery.gpkg",
        "evidence_path": tmp_path / "evidence.gpkg",
        "manifest_path": tmp_path / "manifest.json",
        "output_path": tmp_path / "capture.gpkg",
    }
    result = workflow.prepare_capture(**kwargs)
    assert result["status"] == "blocked"
    assert result["error"]["type"] == "KeyError"
    with pytest.raises(KeyError, match="malformed capture field"):
        workflow.prepare_capture(**kwargs, raise_on_error=True)


def test_result_to_dict_is_json_safe_and_aliases_are_stable():
    result = workflow.GCPWorkflowResult(
        operation="status",
        status="blocked",
        absolute_accuracy_validation="not_verified",
        blockers=("No GCP",),
        next_actions=("cad2gis gcp prepare",),
        artifacts={"path": Path("capture.gpkg"), "values": (1, 2)},
        backend_result={"non_finite": float("nan")},
    ).to_dict()

    json.dumps(result)
    assert workflow.prepare is workflow.prepare_capture
    assert workflow.diagnose is workflow.diagnose_capture
    assert workflow.export_profile is workflow.export_reviewed_profile
    assert result["blockers"] == ["No GCP"]
    assert result["artifacts"] == {"path": "capture.gpkg", "values": [1, 2]}
    assert result["non_finite"] is None
