"""Real-DWG compatibility checks for the user-supplied APD_test corpus."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "data" / "apd_test_manifest.json"
DEFAULT_DATASET_ROOT = ROOT.parent / "APD_test"
ALLOWED_TEST_ROLES = {
    "reader",
    "crash_resistance",
    "generic_fidelity",
    "determinism",
    "performance",
}
REQUIRED_RECORD_FIELDS = {
    "entity_key",
    "source_sha256",
    "source_file",
    "handle",
    "layout",
    "cad_role",
    "layer",
    "dwg_type_name",
    "points",
    "text",
    "native_length",
    "curve_facts",
    "aci_color",
    "linetype",
    "lineweight",
}
FULL_READER_ENV = "CAD2GIS_FULL_DWG_TESTS"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _dataset_root() -> Path:
    configured = os.environ.get("CAD2GIS_TEST_DATASET_ROOT")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else DEFAULT_DATASET_ROOT.resolve()
    )


def _case_id(case: dict[str, object]) -> str:
    return str(case["case_id"])


def _cases() -> tuple[dict[str, object], ...]:
    return tuple(_manifest()["cases"])


def _full_reader_enabled() -> bool:
    return os.environ.get(FULL_READER_ENV, "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _extract_records(source: Path):
    from cad2gis.reader.contracts import ReaderUnavailableError
    from cad2gis.reader.resolver import extract_records

    try:
        return extract_records(source)
    except ReaderUnavailableError as exc:
        pytest.skip(f"Configured DWG reader is unavailable: {exc}")


def test_apd_test_manifest_is_governed_without_accuracy_claim() -> None:
    manifest = _manifest()

    assert manifest["schema_version"] == "cad2gis-test-dataset-v1"
    assert manifest["provenance"] == "user_supplied"
    assert manifest["license_status"] == "not_documented"
    boundary = manifest["claim_boundary"]
    assert boundary["truth_status"] == "not_established"
    assert set(boundary["allowed_test_roles"]) == ALLOWED_TEST_ROLES
    assert boundary["domain_accuracy"] == "not_claimed"
    assert boundary["absolute_accuracy"] == "not_claimed"
    assert len(manifest["cases"]) == 3
    assert sum(bool(case["full_reader_default"]) for case in manifest["cases"]) == 1


@pytest.mark.parametrize("case", _cases(), ids=_case_id)
def test_apd_test_source_identity(case: dict[str, object]) -> None:
    source = _dataset_root() / str(case["source"])
    if not source.is_file():
        pytest.skip(
            "External APD_test corpus is unavailable; set "
            "CAD2GIS_TEST_DATASET_ROOT"
        )

    content = source.read_bytes()

    assert len(content) == int(case["size_bytes"])
    assert hashlib.sha256(content).hexdigest().casefold() == str(
        case["sha256"]
    ).casefold()
    assert content[:6].decode("ascii") == case["dwg_version"]


@pytest.mark.parametrize("case", _cases(), ids=_case_id)
def test_apd_test_reader_contract(case: dict[str, object]) -> None:
    source = _dataset_root() / str(case["source"])
    if not source.is_file():
        pytest.skip(
            "External APD_test corpus is unavailable; set "
            "CAD2GIS_TEST_DATASET_ROOT"
        )
    if not bool(case["full_reader_default"]) and not _full_reader_enabled():
        pytest.skip(
            f"Complex-DWG extraction is opt-in; set {FULL_READER_ENV}=1. "
            "Source identity is still checked by the default corpus suite."
        )

    inventory = _extract_records(source)
    diagnostics = dict(inventory.diagnostics)
    expected_sha = str(case["sha256"]).casefold()

    assert inventory
    assert diagnostics["inventory_complete"] is True
    assert diagnostics["skipped_rows"] == 0
    assert int(diagnostics["parsed_rows"]) == len(inventory)
    assert all(
        str(record["source_sha256"]).casefold() == expected_sha
        for record in inventory
    )
    assert all(
        not (REQUIRED_RECORD_FIELDS - set(record))
        for record in inventory
    )

    cad_records = [
        record for record in inventory
        if str(record.get("dwg_type_name", "")).upper()
        not in {"DOCUMENT_METADATA", "BLOCK_RECORD"}
    ]
    type_counts = Counter(
        str(record.get("dwg_type_name", "")).upper()
        for record in cad_records
    )
    assert cad_records
    assert sum(type_counts[name] for name in ("TEXT", "MTEXT", "ATTRIB")) > 0
    assert sum(
        type_counts[name]
        for name in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "SPLINE")
    ) > 0
    if diagnostics.get("backend") == "autocad_core_console_bulk":
        assert diagnostics["completion_rows"] == len(inventory)
