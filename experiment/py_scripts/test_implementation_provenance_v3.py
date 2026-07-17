"""Regression tests for the explicit production implementation scope."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cad2gis_v3.implementation import (
    IMPLEMENTATION_SCHEMA_VERSION,
    PRODUCTION_CONVERSION_FILES,
    PRODUCTION_CONVERSION_SCOPE,
    build_implementation_provenance,
    implementation_manifest_fields,
    production_conversion_provenance,
)
from cad2gis_v3.pipeline import _implementation_digest


PY_SCRIPTS = Path(__file__).resolve().parent


def test_production_scope_is_explicit_complete_and_excludes_review_lane():
    assert PRODUCTION_CONVERSION_FILES == tuple(sorted(PRODUCTION_CONVERSION_FILES))
    assert all((PY_SCRIPTS / path).is_file() for path in PRODUCTION_CONVERSION_FILES)
    assert "cad2gis_v3/pipeline.py" in PRODUCTION_CONVERSION_FILES
    assert "cad2gis_v3/implementation.py" in PRODUCTION_CONVERSION_FILES
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
