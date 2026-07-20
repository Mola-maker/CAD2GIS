"""Focused tests for the read-only cross-CAD verification matrix."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cad2gis.verify import (
    CLAIM_CROSS_NOMINAL_CRS,
    CLAIM_INVENTORY_ONLY,
    CLAIM_SINGLE_NOMINAL_CRS,
    evaluate_matrix,
    strongest_allowed_claim,
)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _sample(sample_id: str, seed: str, *, absolute: bool = False) -> dict:
    value = {
        "sample_id": sample_id,
        "evaluated": True,
        "input_verified": True,
        "source": {
            "sha256": _sha(seed),
            "version": "2026.1",
            "vendor": "AutoCAD",
            "units": "m",
            "crs": "EPSG:3857",
        },
        "layouts": ["Model"],
        "blocks": {"count": 12, "reviewed": True},
        "curves": {"count": 0, "reviewed": True},
        "profile": {"id": f"{sample_id}-profile", "reviewed": True},
        "gold": {"available": True, "independent": True},
        "geometry": {"passed": True, "source_geometry_immutable": True},
        "topology": {"passed": True},
        "semantics": {"passed": True},
        "style": {"passed": True},
        "length": {"passed": True, "closure_passed": True},
        "nominal_crs": {"passed": True},
        "gcp": {
            "surveyed": absolute,
            "training_control_count": 4 if absolute else 0,
            "check_control_count": 4 if absolute else 0,
            "check_status": "PASS" if absolute else "NOT_VERIFIED",
            "reviewed": absolute,
        },
    }
    return value


def test_missing_surveyed_gcp_is_hard_absolute_fail(tmp_path: Path) -> None:
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps({"schema_version": "cad2gis-verification-matrix-v1", "samples": [_sample("APD", "a")] }),
        encoding="utf-8",
    )

    before = path.read_bytes()
    report = evaluate_matrix(path)

    assert report["schema_version"] == "cad2gis-verification-report-v1"
    assert report["samples"][0]["dimensions"]["absolute_accuracy"] == "FAIL"
    assert report["status"] == "FAIL"
    assert "absolute accuracy" in report["samples"][0]["reasons"][0]
    assert report["claim"] == CLAIM_SINGLE_NOMINAL_CRS
    assert path.read_bytes() == before


def test_distinct_verified_hashes_enable_cross_cad_fidelity_only(tmp_path: Path) -> None:
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "cad2gis-verification-matrix-v1",
                "samples": [_sample("cad-a", "a"), _sample("cad-b", "b")],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_matrix(path)

    assert report["summary"]["unique_input_hashes"] == 2
    assert report["summary"]["cross_cad_eligible"] is True
    assert report["claim"] == CLAIM_CROSS_NOMINAL_CRS
    assert report["dimensions"]["absolute_accuracy"]["status"] == "FAIL"


def test_duplicate_hash_does_not_become_cross_cad() -> None:
    first = _sample("copy-a", "same")
    second = _sample("copy-b", "same")
    report = {
        "samples": [
            {
                "input_verified": True,
                "evaluated": True,
                "input_sha256": first["source"]["sha256"],
                "dimensions": {key: "PASS" for key in ("geometry", "topology", "semantics", "style", "length")},
            },
            {
                "input_verified": True,
                "evaluated": True,
                "input_sha256": second["source"]["sha256"],
                "dimensions": {key: "PASS" for key in ("geometry", "topology", "semantics", "style", "length")},
            },
        ]
    }

    assert strongest_allowed_claim(report).startswith("Single-input")


def test_inventory_rows_are_kept_without_precision_claim(tmp_path: Path) -> None:
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "cad2gis-verification-matrix-v1",
                "samples": [
                    {
                        "sample_id": "AGA",
                        "status": "inventory_only",
                        "source": {"sha256": _sha("aga")},
                        "vendor": "AutoCAD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_matrix(path)

    assert report["summary"]["inventory_count"] == 1
    assert report["samples"][0]["status"] == "INVENTORY_ONLY"
    assert report["claim"] == CLAIM_INVENTORY_ONLY


def test_unsupported_schema_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "bad-schema.json"
    path.write_text(
        json.dumps({"schema_version": "cad2gis-verification-matrix-v99", "samples": []}),
        encoding="utf-8",
    )

    report = evaluate_matrix(path)

    assert report["status"] == "FAIL"
    assert report["errors"] == ["unsupported matrix schema: cad2gis-verification-matrix-v99", "matrix contains no samples"]


def test_run_level_unresolved_candidates_do_not_override_coverage_pass(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.dwg"
    source.write_bytes(b"verified-source")
    path = tmp_path / "run_manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "cad2gis-run-manifest-v4",
                "source": {
                    "path": str(source),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
                "validation": {
                    "source_geometry": {
                        "passed": True,
                        "source_geometry_immutable": True,
                    },
                    "topology": {"passed": True},
                    "measurements": {"passed": True},
                },
                "semantics": {
                    "status": "PASS",
                    "passed": True,
                    "conversion_allowed": True,
                    "counts": {"non_allowlisted": 0},
                },
                "style": {
                    "status": "PASS",
                    "passed": True,
                    "conversion_allowed": True,
                    "counts": {"non_allowlisted": 0},
                },
                "unresolved_count": 13,
                "crs": {
                    "source_crs": "EPSG:3857",
                    "target_crs": "EPSG:9481",
                    "operation": "direct source-to-target",
                    "calibration": {"status": "disabled"},
                },
            }
        ),
        encoding="utf-8",
    )

    sample = evaluate_matrix(path)["samples"][0]

    assert sample["dimensions"]["semantics"] == "PASS"
    assert sample["dimensions"]["style"] == "PASS"
    assert not any("unsupported/unmatched semantics" in reason for reason in sample["reasons"])
