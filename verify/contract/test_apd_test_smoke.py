"""Capability-gated smoke coverage for the repository APD DWG dataset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cad2gis.reader.libredwg import extract_dwg_records, libredwg_capability


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY_ROOT / "APD_test" / "dataset_manifest.json"
DATASET_MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
DATASET_CASES = tuple(DATASET_MANIFEST["cases"])


def _case_id(case: dict[str, object]) -> str:
    return str(case["case_id"])


@pytest.mark.parametrize("case", DATASET_CASES, ids=_case_id)
def test_apd_test_real_dwg_smoke(case: dict[str, object]) -> None:
    capability = libredwg_capability()
    if not capability.available:
        pytest.skip(
            f"LibreDWG unavailable for {case['case_id']}: {capability.detail} "
            f"Remediation: {capability.remediation}"
        )

    source = REPOSITORY_ROOT / "APD_test" / str(case["source"])
    inventory = extract_dwg_records(source)
    diagnostics = inventory.diagnostics
    expected_sha256 = str(case["sha256"]).casefold()

    assert diagnostics["extraction_backend"] == "libredwg"
    assert diagnostics["inventory_complete"] is True
    assert diagnostics["skipped_rows"] == 0
    assert inventory
    for record in inventory:
        assert str(record["source_sha256"]).casefold() == expected_sha256
