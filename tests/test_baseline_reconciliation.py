"""Fast reconciliation checks for the committed APD evidence baseline."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3.config import SourceProfile
from cad2gis.reader.records_adapter import load_records, validate_bundle_facts

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "baselines" / "apd_hutabohu"
SOURCE_PROFILE = ROOT / "experiment" / "config" / "apd_source_profile.json"
RECORDS = BASELINE / "records" / "readcad_review_bundle.json"
DELIVERY = BASELINE / "delivery" / "apd_delivery.gpkg"
EXPECTED_DELIVERY_COUNTS = {
    "BOITE": 43,
    "CABLE": 6,
    "PTECH": 167,
    "IMB": 682,
    "SITE": 2,
}


def test_apd_records_bundle_contract() -> None:
    profile = SourceProfile.load(SOURCE_PROFILE)
    bundle_info = validate_bundle_facts(RECORDS, profile)

    assert bundle_info["schema_version"] == "cad2gis.review_bundle.v2"
    assert bundle_info["objects_count"] == 9391
    assert bundle_info["facts_count"] == 9391
    assert bundle_info["source_sha256"] == profile.source_sha256
    assert len(load_records(RECORDS)) == 9391


def test_apd_records_bundle_rejects_a_different_source_profile() -> None:
    unrelated_profile = SourceProfile.load(
        BASELINE / "config" / "source_profile.json"
    )

    with pytest.raises(ValueError, match="source SHA-256"):
        validate_bundle_facts(RECORDS, unrelated_profile)


def test_apd_delivery_layer_counts() -> None:
    with sqlite3.connect(DELIVERY) as connection:
        actual = {
            layer: connection.execute(
                f'SELECT COUNT(*) FROM "{layer}"'
            ).fetchone()[0]
            for layer in EXPECTED_DELIVERY_COUNTS
        }

    assert actual == EXPECTED_DELIVERY_COUNTS


def test_apd_bundle_payload_count_matches_contract() -> None:
    payload = json.loads(RECORDS.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "cad2gis.review_bundle.v2"
    assert len(payload["objects"]) == 9391
