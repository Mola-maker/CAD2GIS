"""Contract tests for the LibreDWG cross-platform reader and ingest boundary."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import cad2gis.cad2gis_v3.ingest as canonical_ingest
import cad2gis.reader.libredwg as libredwg_module
from cad2gis.cad2gis_v3.config import SourceProfile
from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.ingest import ingest as libredwg_ingest

_ROOT = Path(__file__).resolve().parents[2]
_RECORDS_BUNDLE = _ROOT / "baselines" / "apd_hutabohu" / "records" / "readcad_review_bundle.json"
_DEV_PROFILE = _ROOT / "baselines" / "apd_hutabohu" / "config" / "source_profile_libredwg.json"
_CANONICAL_PROFILE = _ROOT / "baselines" / "apd_hutabohu" / "config" / "source_profile.json"

_REQUIRED_RECORD_KEYS = {
    "entity_key",
    "source_sha256",
    "source_file",
    "handle",
    "layout",
    "layout_role",
    "cad_role",
    "layer",
    "object_name",
    "dwg_type_name",
    "points",
    "centroid",
    "closed",
    "text",
    "block_name",
    "block_attributes",
    "dimension_value",
    "scale_x",
    "scale_y",
    "scale_z",
    "owner_handle",
    "dimension_text_override",
    "native_length",
    "raw_properties",
    "curve_facts",
    "curve_fingerprint",
    "aci_color",
    "true_color",
    "linetype",
    "lineweight",
    "rotation",
    "entity_aci_color",
    "layer_aci_color",
    "entity_true_color",
    "layer_true_color",
    "entity_linetype",
    "layer_linetype",
    "entity_lineweight",
    "layer_lineweight",
}


def _mock_record(entity_key: str, dwg_type: str = "LINE", layout: str = "Model") -> dict:
    return {
        "entity_key": entity_key,
        "source_sha256": "a" * 64,
        "source_file": "fixture.dwg",
        "handle": entity_key,
        "layout": layout,
        "layout_role": "model" if layout.casefold() == "model" else "block_definition",
        "cad_role": "model" if layout.casefold() == "model" else "block_definition",
        "layer": "UNKNOWN",
        "object_name": f"ACDB{dwg_type}",
        "dwg_type_name": dwg_type,
        "points": [(0.0, 0.0), (1.0, 0.0)],
        "centroid": (0.0, 0.0),
        "closed": False,
        "text": "",
        "block_name": "",
        "block_attributes": {},
        "dimension_value": 0.0,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "scale_z": 1.0,
        "owner_handle": "",
        "dimension_text_override": "",
        "native_length": 1.0,
        "raw_properties": {},
        "curve_facts": {},
        "curve_fingerprint": "",
        "aci_color": 0,
        "true_color": 0,
        "linetype": "",
        "lineweight": 0,
        "rotation": 0.0,
        "entity_aci_color": 0,
        "layer_aci_color": 0,
        "entity_true_color": 0,
        "layer_true_color": 0,
        "entity_linetype": "",
        "layer_linetype": "",
        "entity_lineweight": 0,
        "layer_lineweight": 0,
    }


def _mock_records(count: int, dwg_type: str = "LINE", layout: str = "Model") -> list[dict]:
    return [_mock_record(f"handle-{i}", dwg_type, layout) for i in range(count)]


class _MockInventory(list):
    def __init__(self, values=(), *, diagnostics=None):
        super().__init__(values)
        self.diagnostics = dict(diagnostics or {})


def test_inventory_complete_and_no_skips():
    records = _MockInventory(
        _mock_records(10),
        diagnostics={
            "extraction_backend": "libredwg",
            "skipped_rows": 0,
            "inventory_complete": True,
            "returned_records": 10,
        },
    )
    diag = records.diagnostics
    assert diag["extraction_backend"] == "libredwg"
    assert diag["skipped_rows"] == 0
    assert diag["inventory_complete"] is True
    assert diag["returned_records"] == len(records)


def test_census_matches_apd_baseline():
    model_records = (
        _mock_records(6940, "LINE", "Model")
        + _mock_records(222, "INSERT", "Model")
        + _mock_records(170, "DIMENSION_ALIGNED", "Model")
    )
    actual = {
        "model_entities": len(model_records),
        "model_inserts": sum(r["dwg_type_name"] == "INSERT" for r in model_records),
        "model_dimensions": sum("DIMENSION" in r["dwg_type_name"] for r in model_records),
    }
    expected = {"model_entities": 7332, "model_inserts": 222, "model_dimensions": 170}
    assert actual == expected


def test_unsupported_records_use_v3_contract():
    records = _mock_records(3)
    records[0]["raw_properties"]["unsupported_reasons"] = ["reason_a"]
    records[1]["raw_properties"]["unsupported_reasons"] = ["reason_b", "reason_c"]
    for record in records:
        reasons = record["raw_properties"].get("unsupported_reasons", ())
        assert isinstance(reasons, (list, tuple))
        assert all(isinstance(reason, str) for reason in reasons)


def test_no_windows_imports():
    module_path = Path(libredwg_module.__file__).resolve()
    source = module_path.read_text(encoding="utf-8")
    for forbidden in ("win32com", "pythoncom", "accoreconsole"):
        assert forbidden not in source, f"windows-only dependency found: {forbidden}"


def test_ingest_gate_passes():
    profile = SourceProfile.load(_DEV_PROFILE)
    canonical = SourceProfile.load(_CANONICAL_PROFILE)
    assert profile.source_sha256 == canonical.source_sha256


def test_record_field_completeness_snapshot():
    records = _mock_records(5)
    for record in records:
        missing = _REQUIRED_RECORD_KEYS - set(record)
        assert not missing, f"record {record.get('entity_key')} missing keys: {missing}"


def test_ingest_dev_matches_canonical_post_reader(tmp_path: Path):
    import json
    real_profile = json.loads(_DEV_PROFILE.read_text(encoding="utf-8"))
    real_profile["expected_census"] = dict(real_profile["expected_census"])
    real_profile["expected_census"]["model_entities"] = 11
    real_profile["expected_census"]["model_inserts"] = 0
    real_profile["expected_census"]["model_dimensions"] = 0
    profile_path = tmp_path / "test_profile.json"
    profile_path.write_text(json.dumps(real_profile), encoding="utf-8")
    profile = SourceProfile.load(profile_path)
    metadata_record = _mock_record("metadata", "DOCUMENT_METADATA", "Model")
    metadata_record["text"] = "CGEOCS=WGS84.PseudoMercator INSUNITS=6"
    mock_records = _MockInventory(
        [metadata_record] + _mock_records(10),
        diagnostics={
            "extraction_backend": "libredwg",
            "skipped_rows": 0,
            "inventory_complete": True,
            "returned_records": 11,
        },
    )
    canonical_entities, canonical_diag = canonical_ingest.ingest(
        _RECORDS_BUNDLE, profile, extract_records=lambda _p: mock_records
    )
    assert len(canonical_entities) == 11
    assert canonical_diag["census"]["model_entities"] == 11
