"""Tests for plan_layouts existence validation in validate_project."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.project_profile import bootstrap_project, validate_project


def _record(
    source: Path,
    source_sha256: str,
    *,
    entity_key: str,
    dwg_type: str,
    layout: str = "Model",
    layout_role: str = "model",
    cad_role: str = "model",
    points: tuple[tuple[float, float], ...] = (),
    text: str = "",
) -> dict:
    return {
        "entity_key": entity_key,
        "source_sha256": source_sha256,
        "source_file": source.name,
        "handle": entity_key,
        "layout": layout,
        "layout_role": layout_role,
        "cad_role": cad_role,
        "layer": "FO 48C CABLE",
        "object_name": f"AcDb{dwg_type.title()}",
        "dwg_type_name": dwg_type,
        "points": points,
        "centroid": points[0] if points else (0.0, 0.0),
        "closed": False,
        "text": text,
        "block_name": "",
        "block_attributes": {},
        "raw_properties": {},
    }


def _project(tmp_path: Path) -> Path:
    source = tmp_path / "source.dwg"
    source.write_bytes(b"plan-layouts-validation-fixture")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    records = [
        _record(
            source,
            source_sha256,
            entity_key="metadata",
            dwg_type="DOCUMENT_METADATA",
            layout="Document",
            layout_role="metadata",
            cad_role="metadata",
            text="CGEOCS=UTM84-49S;INSUNITS=6",
        ),
        _record(
            source,
            source_sha256,
            entity_key="route",
            dwg_type="LINE",
            points=((100.0, 200.0), (110.0, 200.0)),
        ),
        _record(
            source,
            source_sha256,
            entity_key="paper-note",
            dwg_type="TEXT",
            layout="APD - SF",
            layout_role="layout",
            cad_role="layout",
            text="sheet note",
        ),
        _record(
            source,
            source_sha256,
            entity_key="orphan-member",
            dwg_type="LINE",
            layout="BLOCKDEF:ORPHAN",
            layout_role="block_definition",
            cad_role="block_definition",
            points=((0.0, 0.0), (1.0, 0.0)),
        ),
    ]
    root = tmp_path / "project"
    bootstrap_project(source=source, project_dir=root, records=records)
    return root


def _clean_project(tmp_path: Path) -> Path:
    source = tmp_path / "source.dwg"
    source.write_bytes(b"plan-layouts-clean-fixture")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    records = [
        _record(
            source,
            source_sha256,
            entity_key="metadata",
            dwg_type="DOCUMENT_METADATA",
            layout="Document",
            layout_role="metadata",
            cad_role="metadata",
            text="CGEOCS=UTM84-49S;INSUNITS=6",
        ),
        _record(
            source,
            source_sha256,
            entity_key="route",
            dwg_type="LINE",
            points=((100.0, 200.0), (110.0, 200.0)),
        ),
    ]
    root = tmp_path / "project"
    bootstrap_project(source=source, project_dir=root, records=records)
    return root


def _declare_plan_layouts(root: Path, layouts: list[str]) -> None:
    profile_path = root / "config" / "source_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["plan_layouts"] = layouts
    profile_path.write_text(
        json.dumps(profile, indent=2), encoding="utf-8",
    )


def test_validate_project_accepts_declared_existing_layout(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _declare_plan_layouts(root, ["APD - SF"])

    result = validate_project(project_dir=root)

    assert result["valid"] is True


def test_validate_project_rejects_declared_missing_layout(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _declare_plan_layouts(root, ["NO SUCH LAYOUT"])

    with pytest.raises(ValueError, match="plan_layouts"):
        validate_project(project_dir=root)


def test_validate_project_ignores_empty_plan_layouts(tmp_path: Path) -> None:
    root = _project(tmp_path)

    result = validate_project(project_dir=root)

    assert result["valid"] is True


def test_validate_project_warnings_surface_plan_domain_watch(tmp_path: Path) -> None:
    root = _project(tmp_path)

    warnings = validate_project(project_dir=root)["warnings"]

    assert any("orphan block definition" in warning for warning in warnings)
    assert any("Paper-space layouts" in warning for warning in warnings)


def test_validate_project_warnings_empty_without_plan_domain_issues(
    tmp_path: Path,
) -> None:
    root = _clean_project(tmp_path)

    assert validate_project(project_dir=root)["warnings"] == []
