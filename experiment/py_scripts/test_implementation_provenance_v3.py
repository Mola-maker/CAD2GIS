"""Regression tests for the explicit production implementation scope."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cad2gis_v3.implementation import (
    CONVERSION_SNAPSHOT_SCHEMA_VERSION,
    IMPLEMENTATION_SCHEMA_VERSION,
    PRODUCTION_CONVERSION_FILES,
    PRODUCTION_CONVERSION_SCOPE,
    SnapshotVerificationError,
    build_implementation_provenance,
    conversion_snapshot_manifest_fields,
    freeze_conversion_snapshot,
    implementation_manifest_fields,
    production_conversion_provenance,
    verify_conversion_snapshot,
)
from cad2gis_v3.pipeline import _implementation_digest


PY_SCRIPTS = Path(__file__).resolve().parent


def test_production_scope_is_explicit_complete_and_excludes_review_lane():
    assert PRODUCTION_CONVERSION_FILES == tuple(sorted(PRODUCTION_CONVERSION_FILES))
    assert all((PY_SCRIPTS / path).is_file() for path in PRODUCTION_CONVERSION_FILES)
    assert "cad2gis_v3/pipeline.py" in PRODUCTION_CONVERSION_FILES
    assert "cad2gis_v3/implementation.py" in PRODUCTION_CONVERSION_FILES
    assert "cad2gis_v3/gpkg_metadata.py" in PRODUCTION_CONVERSION_FILES
    assert "cad2gis_v3/spatial_coverage.py" in PRODUCTION_CONVERSION_FILES
    assert "cad2gis_v3/semantic_anchor.py" not in PRODUCTION_CONVERSION_FILES
    assert not any("curation" in path or "provider" in path for path in PRODUCTION_CONVERSION_FILES)


def test_production_provenance_is_reproducible_and_manifest_digests_match():
    first = production_conversion_provenance()
    second = production_conversion_provenance()
    fields = implementation_manifest_fields(first)

    assert first == second
    assert first["schema_version"] == IMPLEMENTATION_SCHEMA_VERSION
    assert first["scope"] == PRODUCTION_CONVERSION_SCOPE
    assert first["sha256"] == _implementation_digest()
    assert fields["implementation_sha256"] == fields["implementation"]["sha256"]
    assert len(first["files"]) == len(PRODUCTION_CONVERSION_FILES)
    assert json.loads(json.dumps(fields, sort_keys=True)) == fields


def test_out_of_scope_changes_do_not_change_digest(tmp_path):
    (tmp_path / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "curation.py").write_text("MODEL = 'before'\n", encoding="utf-8")

    before = build_implementation_provenance(
        tmp_path, scope="production-conversion", scope_version=1,
        relative_paths=("runtime.py",),
    )
    (tmp_path / "curation.py").write_text("MODEL = 'after'\n", encoding="utf-8")
    after_review_change = build_implementation_provenance(
        tmp_path, scope="production-conversion", scope_version=1,
        relative_paths=("runtime.py",),
    )
    (tmp_path / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    after_runtime_change = build_implementation_provenance(
        tmp_path, scope="production-conversion", scope_version=1,
        relative_paths=("runtime.py",),
    )

    assert after_review_change["sha256"] == before["sha256"]
    assert after_runtime_change["sha256"] != before["sha256"]


@pytest.mark.parametrize("relative_path", ("../outside.py", "/absolute.py"))
def test_scope_rejects_paths_outside_its_root(tmp_path, relative_path):
    with pytest.raises(ValueError, match="must be relative"):
        build_implementation_provenance(
            tmp_path, scope="production-conversion", scope_version=1,
            relative_paths=(relative_path,),
        )


def test_scope_fails_closed_when_a_declared_runtime_file_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="missing.py"):
        build_implementation_provenance(
            tmp_path, scope="production-conversion", scope_version=1,
            relative_paths=("missing.py",),
        )


def test_conversion_snapshot_freezes_inputs_and_verifies_before_publish(tmp_path):
    source = tmp_path / "drawing.dwg"
    profile = tmp_path / "profile.json"
    registry = tmp_path / "registry.json"
    gcp = tmp_path / "gcp.json"
    source.write_bytes(b"DWG bytes")
    profile.write_text("{\"schema_version\":\"draft\"}\n", encoding="utf-8")
    registry.write_text("{\"schema_version\":\"draft\"}\n", encoding="utf-8")
    gcp.write_text("{\"status\":\"blocked\"}\n", encoding="utf-8")
    (tmp_path / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")

    snapshot = freeze_conversion_snapshot(
        source,
        profile,
        registry,
        gcp,
        code_root=tmp_path,
        code_paths=("runtime.py",),
    )
    assert snapshot["schema_version"] == CONVERSION_SNAPSHOT_SCHEMA_VERSION
    assert snapshot["source_sha256"] == snapshot["artifacts"]["source"]["sha256"]
    manifest = conversion_snapshot_manifest_fields(snapshot)
    assert manifest["conversion_snapshot_sha256"] == snapshot["snapshot_sha256"]
    report = verify_conversion_snapshot(snapshot)
    assert report["verified"] is True
    assert report["checked"] == [
        "source", "source_profile", "mapping_registry", "gcp_profile", "implementation",
    ]

    source.write_bytes(b"changed")
    try:
        verify_conversion_snapshot(snapshot)
    except SnapshotVerificationError as exc:
        assert any("artifacts.source.sha256" in item for item in exc.mismatches)
    else:  # pragma: no cover - defensive assertion for fail-closed behavior
        raise AssertionError("changed source must abort publication")


def test_conversion_snapshot_rejects_descriptor_tampering(tmp_path):
    source = tmp_path / "drawing.dwg"
    profile = tmp_path / "profile.json"
    registry = tmp_path / "registry.json"
    source.write_bytes(b"DWG bytes")
    profile.write_text("profile", encoding="utf-8")
    registry.write_text("registry", encoding="utf-8")
    (tmp_path / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = freeze_conversion_snapshot(
        source, profile, registry, code_root=tmp_path, code_paths=("runtime.py",)
    )
    snapshot["artifacts"]["source"]["sha256"] = "0" * 64
    try:
        verify_conversion_snapshot(snapshot)
    except SnapshotVerificationError as exc:
        assert any("snapshot descriptor changed" in item for item in exc.mismatches)
    else:  # pragma: no cover
        raise AssertionError("descriptor tampering must abort publication")
