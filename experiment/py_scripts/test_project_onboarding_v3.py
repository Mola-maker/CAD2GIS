"""Cross-drawing onboarding contracts: facts first, review always explicit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cad2gis_v3.config import (
    MappingRegistry,
    ProjectExpectations,
    ReviewRecord,
    SourceProfile,
)
from cad2gis_v3.ingest import ingest
from cad2gis_v3.pipeline import ConversionRequest, convert
from cad2gis_v3.project_profile import (
    bootstrap_project,
    inspect_source,
    validate_project,
)


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "vendor-a.dwg"
    path.write_bytes(b"synthetic-dwg-for-contract-tests")
    return path


def _records(source: Path) -> list[dict]:
    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    common = {"source_sha256": digest, "source_file": source.name}
    return [
        {
            **common,
            "entity_key": "metadata",
            "layout": "Document",
            "cad_role": "metadata",
            "dwg_type_name": "DOCUMENT_METADATA",
            "text": "CGEOCS=Vendor.LocalGrid;INSUNITS=4",
        },
        {
            **common,
            "entity_key": "insert-1",
            "layout": "Model",
            "cad_role": "model",
            "dwg_type_name": "INSERT",
            "layer": "UNKNOWN_ASSET",
            "block_name": "VENDOR_BLOCK_42",
            "raw_properties": {
                "unsupported_reasons": ["dynamic_block_properties_unavailable"]
            },
        },
        {
            **common,
            "entity_key": "dimension-1",
            "layout": "Model",
            "cad_role": "model",
            "dwg_type_name": "DIMENSION",
            "layer": "MEASUREMENTS",
        },
    ]


def test_inspect_source_is_deterministic_inventory_not_semantic_guess(tmp_path):
    source = _source(tmp_path)
    first = inspect_source(source=source, records=_records(source))
    second = inspect_source(source=source, records=reversed(_records(source)))

    assert first["inventory_sha256"] == second["inventory_sha256"]
    assert first["counts"]["model_entities"] == 2
    assert first["block_names"] == {"VENDOR_BLOCK_42": 1}
    assert first["block_instances"][0]["block_name"] == "VENDOR_BLOCK_42"
    assert first["style_facts"]
    assert first["document_metadata"]["dwg_cgeocs_values"] == ["Vendor.LocalGrid"]
    assert first["unsupported"]["by_reason"] == {
        "dynamic_block_properties_unavailable": 1
    }
    assert "feature_counts" not in first
    assert "source_crs" not in first


def test_bootstrap_writes_only_draft_review_pack_and_validate_does_not_approve(
    tmp_path,
):
    source = _source(tmp_path)
    project = tmp_path / "project"
    result = bootstrap_project(
        source=source,
        project_dir=project,
        records=_records(source),
    )

    assert result["status"] == "draft"
    assert result["conversion_allowed"] is False
    assert not (project / "config" / "gcp_profile.json").exists()
    profile_value = json.loads(
        (project / "config" / "source_profile.json").read_text(encoding="utf-8")
    )
    registry_value = json.loads(
        (project / "config" / "mapping_registry.json").read_text(encoding="utf-8")
    )
    assert profile_value["review"] == {"status": "draft"}
    assert profile_value["drawing"]["drawing_units"] is None
    assert profile_value["crs"]["source_crs"] is None
    assert profile_value["crs"]["target_crs"] is None
    assert profile_value["expectations"]["feature_counts"] == {}
    assert registry_value["review"] == {"status": "draft"}
    assert registry_value["block_families"] == {}
    assert registry_value["coverage"]["semantics"]["policy"] == "fail"

    validation = validate_project(project_dir=project)
    assert validation["valid"] is True
    assert validation["status"] == "unreviewed"
    assert validation["conversion_allowed"] is False


def test_draft_contracts_are_loadable_for_review_but_never_for_conversion(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    project = tmp_path / "project"
    bootstrap_project(source=source, project_dir=project, records=_records(source))
    profile_path = project / "config" / "source_profile.json"
    registry_path = project / "config" / "mapping_registry.json"

    profile = SourceProfile.load(profile_path)
    with pytest.raises(ValueError, match="draft"):
        profile.require_reviewed()
    registry = MappingRegistry.load(
        registry_path, profile.source_sha256, require_reviewed=False,
    )
    assert registry.review.status == "draft"
    with pytest.raises(ValueError, match="draft"):
        MappingRegistry.load(registry_path, profile.source_sha256)

    def forbidden_ingest(*_args, **_kwargs):
        raise AssertionError("draft conversion reached the AutoCAD reader")

    monkeypatch.setattr("cad2gis_v3.pipeline.ingest", forbidden_ingest)
    with pytest.raises(ValueError, match="draft"):
        convert(ConversionRequest(
            source=source,
            run_dir=tmp_path / "run",
            source_profile=profile_path,
            mapping_registry=registry_path,
        ))


def test_validate_project_rejects_inventory_tampering(tmp_path):
    source = _source(tmp_path)
    project = tmp_path / "project"
    bootstrap_project(source=source, project_dir=project, records=_records(source))
    inventory_path = project / "review" / "source_inventory.json"
    value = json.loads(inventory_path.read_text(encoding="utf-8"))
    value["counts"]["records"] += 1
    inventory_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="inventory hash mismatch"):
        validate_project(project_dir=project)


def test_apd_legacy_profiles_still_normalize_to_dynamic_contracts():
    root = Path(__file__).resolve().parents[1]
    profile = SourceProfile.load(root / "config" / "apd_source_profile.json")
    registry = MappingRegistry.load(
        root / "config" / "apd_mapping_registry.json", profile.source_sha256,
    )

    assert profile.is_legacy is True
    assert profile.is_reviewed is True
    assert profile.expectations.feature_counts["CABLE"] == 6
    assert {item.family_id for item in profile.expectations.annotation_families} == {
        "fat", "pole_new", "pole_existing",
    }
    assert registry.semantic_coverage_policy == "fail"
    assert registry.style_coverage_policy == "fail"


def test_apd_legacy_project_pack_is_validated_without_modern_review_sidecars():
    root = Path(__file__).resolve().parents[1]

    validation = validate_project(project_dir=root)

    assert validation["valid"] is True
    assert validation["conversion_allowed"] is True
    assert validation["status"] == "reviewed_ready_legacy_compatibility"
    assert validation["compatibility_mode"] == "legacy-source-bound"
    assert validation["source_sha256"] == (
        "557e01413c394421c55709ce94b091793196bee1ec0452c46f69a72e4e815557"
    )
    assert validation["inventory_sha256"] is None
    assert validation["warnings"]


def test_original_apd_v1_profile_remains_loadable(tmp_path):
    root = Path(__file__).resolve().parents[1]
    value = json.loads(
        (root / "config" / "apd_source_profile.json").read_text(encoding="utf-8")
    )
    value["schema_version"] = "cad2gis-source-profile-v1"
    value.pop("spatial_coverage_policy")
    value["expected_census"].pop("source_route_curve_facts", None)
    path = tmp_path / "apd-v1.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    profile = SourceProfile.load(path)
    assert profile.schema_version == "cad2gis-source-profile-v1"
    assert profile.spatial_coverage_policy is None
    assert all(
        gate.dotted_path != "curve_facts_checked"
        for gate in profile.expectations.source_geometry_gates
    )
    assert profile.expectations.delivery_counts["CABLE_SEGMENT"] == 139


def test_ingest_does_not_invent_or_compare_missing_header_declarations(
    tmp_path, monkeypatch,
):
    source = _source(tmp_path)
    import hashlib

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    records = [
        {
            "entity_key": "metadata",
            "source_sha256": source_hash,
            "source_file": source.name,
            "layout": "Document",
            "layout_role": "document",
            "cad_role": "metadata",
            "dwg_type_name": "DOCUMENT_METADATA",
            "text": "READER=AUTOCAD",
        },
        {
            "entity_key": "line",
            "source_sha256": source_hash,
            "source_file": source.name,
            "layout": "Model",
            "layout_role": "model",
            "cad_role": "model",
            "dwg_type_name": "LINE",
            "layer": "UNREVIEWED",
            "points": [(0.0, 0.0), (1.0, 0.0)],
        },
    ]
    monkeypatch.setattr(
        "cad2gis_v3.ingest.extract_dwg_records", lambda _source: records,
    )
    expectations = ProjectExpectations.from_mapping({
        "source_inventory": {"model_entities": 1},
        "feature_counts": {},
        "annotation_families": {},
        "source_geometry_gates": {},
        "topology_gates": {},
        "segment_gates": {},
        "delivery_counts": {},
    }, allow_incomplete=True)
    profile = SourceProfile(
        path=tmp_path / "profile.json",
        schema_version="cad2gis-project-profile-v1",
        source_sha256=source_hash,
        dwg_cgeocs=None,
        dwg_insunits=None,
        source_crs=None,
        target_crs=None,
        drawing_units=None,
        spatial_coverage_policy=None,
        expected_census={"model_entities": 1},
        expectations=expectations,
        review=ReviewRecord(
            status="reviewed", reviewed_by="tester", reviewed_at="now",
            provenance="fixture",
        ),
    )

    entities, diagnostics = ingest(source, profile)
    assert len(entities) == 2
    assert diagnostics["drawing_units"] == {"insunits": None, "name": None}


def test_ingest_rejects_compatibility_reader_with_skipped_rows(tmp_path, monkeypatch):
    source = _source(tmp_path)
    import hashlib

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    class IncompleteInventory(list):
        diagnostics = {
            "compatibility_policy": "skip_malformed",
            "total_rows": 2,
            "parsed_rows": 1,
            "skipped_rows": 1,
            "inventory_complete": False,
        }

    records = IncompleteInventory([{
        "entity_key": "metadata",
        "source_sha256": source_hash,
        "source_file": source.name,
        "layout": "Document",
        "layout_role": "document",
        "cad_role": "metadata",
        "dwg_type_name": "DOCUMENT_METADATA",
        "text": "",
    }])
    monkeypatch.setattr(
        "cad2gis_v3.ingest.extract_dwg_records", lambda _source: records,
    )
    expectations = ProjectExpectations.from_mapping({
        "source_inventory": {}, "feature_counts": {},
        "annotation_families": {}, "source_geometry_gates": {},
        "topology_gates": {}, "segment_gates": {}, "delivery_counts": {},
    }, allow_incomplete=True)
    profile = SourceProfile(
        path=tmp_path / "profile.json",
        schema_version="cad2gis-project-profile-v1",
        source_sha256=source_hash,
        dwg_cgeocs=None,
        dwg_insunits=None,
        source_crs=None,
        target_crs=None,
        drawing_units=None,
        spatial_coverage_policy=None,
        expected_census={},
        expectations=expectations,
        review=ReviewRecord(
            status="reviewed", reviewed_by="tester", reviewed_at="now",
            provenance="fixture",
        ),
    )

    with pytest.raises(RuntimeError, match="reader inventory is incomplete"):
        ingest(source, profile)
