"""Tests for the plan_domain include_orphan_blocks project-profile key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.config import SourceProfile


def _profile_payload(plan_domain=None) -> dict:
    payload = {
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
    if plan_domain is not None:
        payload["plan_domain"] = plan_domain
    return payload


def _load(tmp_path: Path, plan_domain=None) -> SourceProfile:
    path = tmp_path / "source_profile.json"
    path.write_text(
        json.dumps(_profile_payload(plan_domain)), encoding="utf-8",
    )
    return SourceProfile.load(path)


def test_plan_domain_key_defaults_to_no_recovery(tmp_path: Path) -> None:
    profile = _load(tmp_path)

    assert profile.include_orphan_blocks == ()


def test_plan_domain_empty_list_means_no_recovery(tmp_path: Path) -> None:
    profile = _load(tmp_path, {"include_orphan_blocks": []})

    assert profile.include_orphan_blocks == ()


def test_plan_domain_block_name_list_is_loaded(tmp_path: Path) -> None:
    profile = _load(
        tmp_path,
        {"include_orphan_blocks": ["sfsfsfs", " zcczczc "]},
    )

    assert profile.include_orphan_blocks == ("sfsfsfs", "zcczczc")


def test_plan_domain_star_is_loaded(tmp_path: Path) -> None:
    profile = _load(tmp_path, {"include_orphan_blocks": "*"})

    assert profile.include_orphan_blocks == ("*",)


@pytest.mark.parametrize("plan_domain", ["sfsfsfs", ["include_orphan_blocks"], 42])
def test_plan_domain_must_be_an_object(tmp_path: Path, plan_domain) -> None:
    with pytest.raises(ValueError, match="Invalid plan_domain keys"):
        _load(tmp_path, plan_domain)


def test_plan_domain_rejects_unknown_keys(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid plan_domain keys"):
        _load(
            tmp_path,
            {"include_orphan_blocks": [], "extra": True},
        )


@pytest.mark.parametrize(
    "blocks",
    ["**", 42, ["sfsfsfs", ""], ["sfsfsfs", "  "], ["sfsfsfs", 7], "ORPHAN"],
)
def test_plan_domain_rejects_invalid_block_values(tmp_path: Path, blocks) -> None:
    with pytest.raises(ValueError, match="include_orphan_blocks"):
        _load(tmp_path, {"include_orphan_blocks": blocks})
