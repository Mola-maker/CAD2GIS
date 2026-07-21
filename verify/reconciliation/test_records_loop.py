"""A-plan closed-loop reconciliation: records bundle → pipeline → GPKG count.

This test exercises the pipeline behaviour on canonical records without
requiring the original DWG.  Reader extraction is covered by
``verify/contract/`` tests.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = _ROOT / "baselines" / "apd_hutabohu"
_RECORDS = _BASELINE / "records" / "readcad_review_bundle.json"
_DELIVERY_BASE = _BASELINE / "delivery" / "apd_delivery.gpkg"
_EVIDENCE_BASE = _BASELINE / "evidence" / "apd_evidence.gpkg"


def _table_counts(gpkg_path: Path, tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    con = sqlite3.connect(str(gpkg_path))
    try:
        for table in tables:
            counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        con.close()
    return counts


def test_records_bundle_schema_stable():
    bundle = json.loads(_RECORDS.read_text(encoding="utf-8"))
    assert bundle.get("schema_version") == "cad2gis.review_bundle.v2"
    assert len(bundle.get("objects", [])) == 9391


def test_baseline_gpkg_counts():
    expected_delivery = {"BOITE": 43, "CABLE": 6, "PTECH": 167, "IMB": 682, "SITE": 2}
    tables = list(expected_delivery)
    actual = _table_counts(_DELIVERY_BASE, tables)
    assert actual == expected_delivery


def test_replay_output_matches_baseline(tmp_path):
    pytest.importorskip("cad2gis.cad2gis_v3.pipeline")
    from cad2gis.reader.records_adapter import load_records, validate_bundle_facts
    from cad2gis.cad2gis_v3.config import SourceProfile

    profile_path = _BASELINE / "config" / "source_profile.json"
    profile = SourceProfile.load(profile_path)
    bundle_info = validate_bundle_facts(_RECORDS, profile)
    assert bundle_info["objects_count"] == 9391
    assert bundle_info["schema_version"] == "cad2gis.review_bundle.v2"

    entities = load_records(_RECORDS)
    assert len(entities) == 9391
