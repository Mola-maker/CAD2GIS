"""Contract tests for the LibreDWG dev-reader and ingest_dev wrapper."""

from __future__ import annotations

import dataclasses
import hashlib
import math
import os
import re
from collections import Counter
from pathlib import Path

import pytest

import cad2gis_v3.ingest as canonical_ingest
import cad2gis_v3.ingest_dev as ingest_dev_module
import libredwg_dev_reader as dev_reader_module
from cad2gis_v3.config import SourceProfile
from cad2gis_v3.ingest_dev import ingest as dev_ingest
from cad2gis_v3.model import SourceEntity

_APD_DWG = (
    Path(__file__).resolve().parent.parent
    / "APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg"
)
_DEV_PROFILE = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "apd_source_profile_dev_libredwg.json"
)
_CANONICAL_PROFILE = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "apd_source_profile.json"
)

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
    # Style sub-fields consumed directly by from_record.
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


def _load_dev_profile():
    return SourceProfile.load(_DEV_PROFILE)


def _load_canonical_profile():
    return SourceProfile.load(_CANONICAL_PROFILE)


@pytest.fixture(scope="module")
def apd_records():
    if not _APD_DWG.exists():
        pytest.skip(f"APD DWG not found: {_APD_DWG}")
    return dev_reader_module.extract_dwg_records(_APD_DWG)


def test_inventory_complete_and_no_skips(apd_records):
    diag = apd_records.diagnostics
    assert diag["extraction_backend"] == "libredwg_dev"
    assert diag["skipped_rows"] == 0
    assert diag["inventory_complete"] is True
    assert diag["returned_records"] == len(apd_records)


def test_census_matches_apd_baseline(apd_records):
    model = [r for r in apd_records if r["layout"] == "Model"]
    actual = {
        "model_entities": len(model),
        "model_inserts": sum(r["dwg_type_name"] == "INSERT" for r in model),
        "model_dimensions": sum("DIMENSION" in r["dwg_type_name"] for r in model),
    }
    expected = {"model_entities": 6940, "model_inserts": 222, "model_dimensions": 170}
    differences = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in expected
        if actual[key] != expected[key]
    }
    if differences:
        # Provide a detailed diff before failing so reports are actionable.
        type_distribution = Counter(r["dwg_type_name"] for r in model)
        raise AssertionError(
            f"APD census mismatch: {differences}; "
            f"model type distribution: {dict(sorted(type_distribution.items(), key=lambda kv: -kv[1]))}"
        )


def test_unsupported_records_use_v3_contract(apd_records):
    unsupported_reasons_seen: set[str] = set()
    for record in apd_records:
        if record.get("inventory_support_status") != "inventory_only":
            continue
        reasons = record.get("raw_properties", {}).get("unsupported_reasons")
        assert reasons, (
            f"record {record['handle']} is inventory_only but has no unsupported_reasons"
        )
        for reason in reasons:
            unsupported_reasons_seen.add(reason)
            assert (
                reason.startswith("libredwg_")
                or reason in {
                    "geometry_unavailable",
                    "geometry_unsupported_in_com_backend",
                    "geometry_unavailable_for_block_definition_record",
                    "external_reference_geometry_not_embedded",
                }
            ), f"unexpected unsupported reason: {reason!r}"
    # Basic sanity: we expect at least some typed unsupported records on APD.
    assert unsupported_reasons_seen


def test_no_windows_imports():
    source = Path(dev_reader_module.__file__).read_text(encoding="utf-8")
    for name in ("win32com", "pythoncom", "accoreconsole"):
        assert name not in source, f"dev reader must not reference {name}"


def test_ingest_gate_passes(monkeypatch, tmp_path):
    if not _APD_DWG.exists():
        pytest.skip(f"APD DWG not found: {_APD_DWG}")

    base_profile = _load_dev_profile()
    # The dev reader enumerates 6942 model entities vs the canonical 6940.
    # To test the ingestion gate logic without being blocked by that deviation,
    # relax the expected census to the actual reader output for this test only.
    actual_records = dev_reader_module.extract_dwg_records(_APD_DWG)
    model = [r for r in actual_records if r["layout"] == "Model"]
    profile = dataclasses.replace(
        base_profile,
        expected_census={
            **base_profile.expected_census,
            "model_entities": len(model),
            "model_inserts": sum(r["dwg_type_name"] == "INSERT" for r in model),
            "model_dimensions": sum("DIMENSION" in r["dwg_type_name"] for r in model),
        },
    )

    # Forward gate: with the env var set, dev ingestion succeeds.
    monkeypatch.setenv("CAD2GIS_DEV_READER", "1")
    entities, diagnostics = dev_ingest(_APD_DWG, profile)
    assert entities
    assert diagnostics["reader_protocol"]["metadata_evidence"] in ("reader", "synthetic")

    # Reverse gate: without the env var, synthetic metadata must be rejected.
    if diagnostics["dwg_metadata"].startswith(dev_reader_module._SYNTHETIC_METADATA_MARKER):
        monkeypatch.delenv("CAD2GIS_DEV_READER", raising=False)
        with pytest.raises(RuntimeError, match="synthetic metadata evidence requires"):
            dev_ingest(_APD_DWG, profile)

    # Contradiction guard: a record claiming reader evidence while carrying the
    # synthetic marker must also raise.
    contradiction_records = dev_reader_module.extract_dwg_records(_APD_DWG)
    contradiction_records[0]["text"] = (
        "CGEOCS=WGS84.PseudoMercator;INSUNITS=6;"
        + dev_reader_module._SYNTHETIC_METADATA_MARKER
    )
    contradiction_records.diagnostics["metadata_evidence"] = "reader"

    def _fake_extract(_path):
        return contradiction_records

    monkeypatch.setattr(dev_reader_module, "extract_dwg_records", _fake_extract)
    monkeypatch.setattr(ingest_dev_module, "extract_dwg_records", _fake_extract)
    monkeypatch.setenv("CAD2GIS_DEV_READER", "1")
    with pytest.raises(RuntimeError, match="reader metadata_evidence is 'reader'"):
        dev_ingest(_APD_DWG, profile)


def test_record_field_completeness_snapshot(apd_records):
    missing_distribution: Counter = Counter()
    for record in apd_records:
        missing = _REQUIRED_RECORD_KEYS - set(record.keys())
        if missing:
            missing_distribution[tuple(sorted(missing))] += 1
    assert not missing_distribution, (
        f"Some records are missing required keys: {dict(missing_distribution)}"
    )


def test_ingest_dev_matches_canonical_post_reader(monkeypatch, tmp_path):
    """Reader-agnostic check: given identical mock records, ingest_dev and
    canonical ingest produce the same post-reader behaviour (entities count,
    census, annotation carriers)."""
    mock_records = dev_reader_module.DWGRecordInventory(
        [
            {
                "entity_key": "meta",
                "source_sha256": "a" * 64,
                "source_file": str(tmp_path / "mock.dwg"),
                "handle": "DOCUMENT_METADATA",
                "layout": "",
                "layout_role": "",
                "cad_role": "",
                "layer": "0",
                "object_name": "DOCUMENT_METADATA",
                "dwg_type_name": "DOCUMENT_METADATA",
                "points": [],
                "centroid": (0.0, 0.0),
                "closed": False,
                "text": "CGEOCS=WGS84.PseudoMercator;INSUNITS=6",
                "block_name": "",
                "block_attributes": {},
                "dimension_value": None,
                "scale_x": 1.0,
                "scale_y": 1.0,
                "scale_z": 1.0,
                "owner_handle": "",
                "dimension_text_override": "",
                "native_length": None,
                "raw_properties": {
                    "unsupported_reasons": [],
                    "schema_version": "cad2gis-raw-properties-v1",
                },
                "curve_facts": {},
                "curve_fingerprint": "",
                "aci_color": 256,
                "true_color": "",
                "linetype": "ByLayer",
                "lineweight": -1,
                "rotation": 0.0,
                "entity_aci_color": 256,
                "layer_aci_color": 7,
                "entity_true_color": "",
                "layer_true_color": "",
                "entity_linetype": "ByLayer",
                "layer_linetype": "Continuous",
                "entity_lineweight": -1,
                "layer_lineweight": -1,
                "inventory_support_status": "full",
            },
            {
                "entity_key": "l1",
                "source_sha256": "a" * 64,
                "source_file": str(tmp_path / "mock.dwg"),
                "handle": "A1",
                "layout": "Model",
                "layout_role": "model",
                "cad_role": "model",
                "layer": "CABLE",
                "object_name": "ACDBLWPOLYLINE",
                "dwg_type_name": "LWPOLYLINE",
                "points": [(0.0, 0.0), (1.0, 1.0)],
                "centroid": (0.5, 0.5),
                "closed": False,
                "text": "",
                "block_name": "",
                "block_attributes": {},
                "dimension_value": None,
                "scale_x": 1.0,
                "scale_y": 1.0,
                "scale_z": 1.0,
                "owner_handle": "",
                "dimension_text_override": "",
                "native_length": math.sqrt(2.0),
                "raw_properties": {
                    "unsupported_reasons": [],
                    "schema_version": "cad2gis-raw-properties-v1",
                },
                "curve_facts": {},
                "curve_fingerprint": "",
                "aci_color": 7,
                "true_color": "",
                "linetype": "ByLayer",
                "lineweight": -1,
                "rotation": 0.0,
                "entity_aci_color": 7,
                "layer_aci_color": 7,
                "entity_true_color": "",
                "layer_true_color": "",
                "entity_linetype": "ByLayer",
                "layer_linetype": "Continuous",
                "entity_lineweight": -1,
                "layer_lineweight": -1,
                "inventory_support_status": "full",
            },
            {
                "entity_key": "i1",
                "source_sha256": "a" * 64,
                "source_file": str(tmp_path / "mock.dwg"),
                "handle": "B1",
                "layout": "Model",
                "layout_role": "model",
                "cad_role": "model",
                "layer": "OBJECTS",
                "object_name": "ACDBBLOCKREFERENCE",
                "dwg_type_name": "INSERT",
                "points": [(10.0, 20.0)],
                "centroid": (10.0, 20.0),
                "closed": False,
                "text": "",
                "block_name": "CABINET",
                "block_attributes": {"ID": "X1"},
                "dimension_value": None,
                "scale_x": 1.0,
                "scale_y": 1.0,
                "scale_z": 1.0,
                "owner_handle": "",
                "dimension_text_override": "",
                "native_length": None,
                "raw_properties": {
                    "unsupported_reasons": [],
                    "schema_version": "cad2gis-raw-properties-v1",
                },
                "curve_facts": {},
                "curve_fingerprint": "",
                "aci_color": 256,
                "true_color": "",
                "linetype": "ByLayer",
                "lineweight": -1,
                "rotation": 0.0,
                "entity_aci_color": 256,
                "layer_aci_color": 7,
                "entity_true_color": "",
                "layer_true_color": "",
                "entity_linetype": "ByLayer",
                "layer_linetype": "Continuous",
                "entity_lineweight": -1,
                "layer_lineweight": -1,
                "inventory_support_status": "full",
            },
        ],
        diagnostics={
            "extraction_backend": "libredwg_dev",
            "skipped_rows": 0,
            "inventory_complete": True,
            "metadata_evidence": "reader",
            "unsupported_reason_counts": {},
        },
    )

    base_profile = _load_dev_profile()
    source = tmp_path / "mock.dwg"
    source.write_bytes(b"mock")
    mock_sha256 = hashlib.sha256(b"mock").hexdigest()
    profile = dataclasses.replace(
        base_profile,
        source_sha256=mock_sha256,
        expected_census={
            "model_entities": 2,
            "model_inserts": 1,
            "model_dimensions": 0,
        },
    )

    def _fake_extract(_path):
        return mock_records

    monkeypatch.setattr(dev_reader_module, "extract_dwg_records", _fake_extract)
    monkeypatch.setattr(ingest_dev_module, "extract_dwg_records", _fake_extract)
    monkeypatch.setattr(canonical_ingest, "extract_dwg_records", _fake_extract)

    dev_entities, dev_diag = dev_ingest(source, profile)
    canonical_entities, canonical_diag = canonical_ingest.ingest(source, profile)

    assert len(dev_entities) == len(canonical_entities)
    assert dev_diag["census"] == canonical_diag["census"]
    assert (
        dev_diag["reader_inventory"]["annotation_carriers"]
        == canonical_diag["reader_inventory"]["annotation_carriers"]
    )
