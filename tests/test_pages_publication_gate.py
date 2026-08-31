from __future__ import annotations

import json
from pathlib import Path

from scripts.build_pages import _publication_gate_passed


ROOT = Path(__file__).resolve().parents[1]
DEMO_ASSETS = ROOT / "src" / "cad2gis" / "webdemo" / "original-demo" / "assets"


def test_current_public_catalog_contains_only_truthfully_labelled_hutabohu() -> None:
    catalog = json.loads(
        (DEMO_ASSETS / "demo-catalog.json").read_text(encoding="utf-8")
    )
    assert [project["id"] for project in catalog["projects"]] == ["hutabohu"]
    project = catalog["projects"][0]
    assert project["map_precision"] == "nominal_crs_no_surveyed_gcp"
    assert "未提供实测 GCP" in project["accuracy_note"]
    assert _publication_gate_passed(project)
    assert not list(DEMO_ASSETS.glob("demo-data-*.json"))


def test_future_cases_require_authoritative_calibration_and_checkpoints() -> None:
    osm_only = {
        "id": "kletek",
        "publication_gate": {
            "calibration_status": "verified",
            "control_authority": "openstreetmap",
            "independent_checkpoint_count": 4,
            "spatial_coverage_passed": True,
            "run_status": "VERIFIED",
        },
    }
    assert not _publication_gate_passed(osm_only)

    surveyed = {
        "id": "kletek",
        "publication_gate": {
            "calibration_status": "verified",
            "control_authority": "surveyed",
            "independent_checkpoint_count": 3,
            "spatial_coverage_passed": True,
            "run_status": "VERIFIED",
        },
    }
    assert _publication_gate_passed(surveyed)

    surveyed["publication_gate"]["independent_checkpoint_count"] = 2
    assert not _publication_gate_passed(surveyed)
