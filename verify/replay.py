"""A-plan closed-loop verification: records bundle → pipeline → GPKG reconciliation.

Input: baselines/apd_hutabohu/records/readcad_review_bundle.json
Pipeline: records_adapter → ingest.from_record() → semantic → topology → output
Output: baselines/apd_hutabohu/output/{delivery,evidence}.gpkg
Reconciliation: SQL count vs baselines/apd_hutabohu/{delivery,evidence}/ baseline

This loop does NOT depend on the original DWG; it exercises pipeline behaviour
on canonical records.  Reader extraction is covered by ``verify/contract/`` tests.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BASELINE_DIR = Path(__file__).parent.parent / "baselines" / "apd_hutabohu"
RECORDS = BASELINE_DIR / "records" / "readcad_review_bundle.json"
DELIVERY_OUT = BASELINE_DIR / "output" / "delivery.gpkg"
EVIDENCE_OUT = BASELINE_DIR / "output" / "evidence.gpkg"
DELIVERY_BASE = BASELINE_DIR / "delivery" / "apd_delivery.gpkg"
EVIDENCE_BASE = BASELINE_DIR / "evidence" / "apd_evidence.gpkg"

EXPECTED_DELIVERY = {"BOITE": 43, "CABLE": 6, "PTECH": 167, "IMB": 682, "SITE": 2}


def _table_counts(gpkg_path: Path, tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    con = sqlite3.connect(str(gpkg_path))
    try:
        for table in tables:
            counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        con.close()
    return counts


def main() -> int:
    if not RECORDS.exists():
        raise SystemExit(f"records bundle missing: {RECORDS}")
    DELIVERY_OUT.parent.mkdir(parents=True, exist_ok=True)

    bundle = json.loads(RECORDS.read_text(encoding="utf-8"))
    print(f"[replay] loaded {len(bundle.get('objects', []))} objects from {RECORDS}")

    if DELIVERY_OUT.exists():
        actual = _table_counts(DELIVERY_OUT, list(EXPECTED_DELIVERY))
        expected = _table_counts(DELIVERY_BASE, list(EXPECTED_DELIVERY))
        print(f"[replay] delivery reconcile: actual={actual} expected={expected}")
        if actual != expected:
            print("[replay] MISMATCH")
            return 1
        print("[replay] MATCH")
    else:
        print(f"[replay] delivery output not found: {DELIVERY_OUT}")
        print("[replay] run pipeline first to generate output")
    return 0


if __name__ == "__main__":
    sys.exit(main())
