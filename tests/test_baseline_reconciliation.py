"""Fast reconciliation checks for the committed APD evidence baseline."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.config import SourceProfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "baselines" / "hutabohu"
SOURCE_PROFILE = BASELINE / "config" / "source_profile.json"
SOURCE_INVENTORY = BASELINE / "review" / "source_inventory.json"
DELIVERY = BASELINE / "run" / "delivery.gpkg"

pytestmark = pytest.mark.skipif(
    not SOURCE_INVENTORY.is_file() or not DELIVERY.is_file(),
    reason=(
        "Optional Hutabohu evidence baseline is not present in this checkout; "
        "run the external-corpus robustness suite instead"
    ),
)


def _profile() -> SourceProfile:
    return SourceProfile.load(SOURCE_PROFILE)


def _inventory() -> dict:
    return json.loads(SOURCE_INVENTORY.read_text(encoding="utf-8"))


def test_source_inventory_binds_to_the_reviewed_profile() -> None:
    profile = _profile()
    inventory = _inventory()

    assert inventory["schema_version"] == "cad2gis-source-inventory-v1"
    assert inventory["source"]["sha256"] == profile.source_sha256
    assert inventory["inventory_sha256"] == profile.inventory_sha256
    assert inventory["source"]["size_bytes"] == profile.source_size_bytes
    assert inventory["counts"]["records"] == 9896


def test_source_profile_rejects_a_different_dwg_stream(tmp_path: Path) -> None:
    profile = _profile()
    other_source = tmp_path / "same-name-different-bytes.dwg"
    other_source.write_bytes(b"not the reviewed source byte stream")
    with pytest.raises(ValueError, match="Source hash mismatch"):
        profile.validate_source(other_source)


def test_apd_delivery_layer_counts() -> None:
    profile = _profile()
    expected = {
        layer: count
        for layer, count in profile.expectations.feature_counts.items()
        if count > 0 and layer != "CABLE"
    }
    with sqlite3.connect(DELIVERY) as connection:
        actual = {
            layer: connection.execute(
                f'SELECT COUNT(*) FROM "{layer}"'
            ).fetchone()[0]
            for layer in expected
        }
        cable_segment_count = connection.execute(
            'SELECT COUNT(*) FROM "CABLE"'
        ).fetchone()[0]

    assert actual == expected

    # The delivery CABLE layer is the segment-normalised view: one row per
    # immutable source segment.  The reviewed feature census still counts
    # whole CABLE routes.
    manifest = json.loads(
        (BASELINE / "run" / "run_manifest.json").read_text(encoding="utf-8")
    )
    segment_contract = manifest["validation"]["segment_delivery"]
    assert segment_contract["passed"] is True
    assert cable_segment_count == segment_contract["count"]


def test_apd_inventory_count_matches_contract() -> None:
    inventory = _inventory()

    assert inventory["counts"]["records"] == 9896
    assert inventory["counts"]["model_entities"] == 7099
    assert inventory["counts"]["unsupported_records"] == 1527
