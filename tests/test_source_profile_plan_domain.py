"""Validation for source-bound plan-domain recovery declarations."""

from __future__ import annotations

import json

import pytest

from cad2gis.cad2gis_v3.config import SourceProfile


def _payload() -> dict:
    return {
        "schema_version": "cad2gis-project-profile-v1",
        "project_id": "plan-domain-fixture",
        "review": {
            "status": "draft",
            "reviewed_by": "",
            "reviewed_at": "",
            "provenance": "",
        },
        "source_binding": {
            "source_sha256": "a" * 64,
            "source_size_bytes": 0,
            "inventory_sha256": "b" * 64,
        },
        "drawing": {
            "dwg_cgeocs": None,
            "dwg_insunits": None,
            "drawing_units": None,
        },
        "crs": {"source_crs": None, "target_crs": None},
        "spatial_coverage_policy": None,
        "expectations": {
            "source_inventory": {},
            "feature_counts": {},
            "annotation_families": {},
            "source_geometry_gates": {},
            "topology_gates": {},
            "segment_gates": {},
            "delivery_counts": {},
        },
    }


def _load(tmp_path, **updates) -> SourceProfile:
    payload = _payload()
    payload.update(updates)
    path = tmp_path / "source_profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return SourceProfile.load(path)


def test_plan_domain_declarations_default_empty(tmp_path):
    profile = _load(tmp_path)
    assert profile.plan_layouts == ()
    assert profile.include_orphan_blocks == ()


def test_explicit_plan_domain_declarations_are_loaded(tmp_path):
    profile = _load(
        tmp_path,
        plan_layouts=["APD - SF", "APD - SF"],
        plan_domain={"include_orphan_blocks": ["NETWORK", " NETWORK "]},
    )
    assert profile.plan_layouts == ("APD - SF",)
    assert profile.include_orphan_blocks == ("NETWORK",)


@pytest.mark.parametrize(
    "plan_domain",
    [
        {"include_orphan_blocks": "*"},
        {"include_orphan_blocks": ["NETWORK", ""]},
        {"include_orphan_blocks": [], "unexpected": True},
    ],
)
def test_unsafe_or_invalid_orphan_declarations_are_rejected(
    tmp_path, plan_domain,
):
    with pytest.raises(ValueError, match="plan_domain|orphan"):
        _load(tmp_path, plan_domain=plan_domain)
