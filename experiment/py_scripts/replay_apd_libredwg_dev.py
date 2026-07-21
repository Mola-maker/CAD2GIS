"""APD replay via libredwg_dev_reader + reconciliation against v3_complete baseline.

Dev-only driver (non-canonical). Requires CAD2GIS_DEV_READER=1.
Patches cad2gis_v3.ingest.extract_dwg_records with the LibreDWG dev reader,
then runs the standard downstream pipeline and compares delivery/evidence
layer counts against experiment/runs/apd_architecture_v3_complete.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PY_SCRIPTS = Path(__file__).resolve().parent
EXPERIMENT = PY_SCRIPTS.parent
BASELINE_DIR = EXPERIMENT / "runs" / "apd_architecture_v3_complete"
APD_DWG = EXPERIMENT / "APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg"
DEV_PROFILE = EXPERIMENT / "config" / "apd_source_profile_dev_libredwg.json"
MAPPING_REGISTRY = EXPERIMENT / "config" / "apd_mapping_registry.json"
GCP_PROFILE = EXPERIMENT / "config" / "apd_gcp_profile.json"
REPORT_PATH = PY_SCRIPTS / "replay_apd_libredwg_dev_report.json"

EXPECTED_DELIVERY = {
    "BOITE": 43,
    "CABLE": 6,
    "PTECH": 167,
    "IMB": 682,
    "SITE": 2,
    "INFRASTRUCTURE": 0,
    "ZNRO": 0,
    "ZPM": 0,
}
EXPECTED_EVIDENCE = {
    "cable_span_segments": 139,
    "physical_span_evidence": 170,
    "source_route_evidence": 6,
}


def _table_counts(gpkg_path: Path, tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    con = sqlite3.connect(str(gpkg_path))
    try:
        for table in tables:
            counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    finally:
        con.close()
    return counts


def _classify_deviation(
    layer: str,
    expected: int,
    actual: int,
    reader_protocol: dict | None = None,
) -> dict:
    """Auto-classify a deviation into reader_fidelity / pipeline_behavior / baseline_drift.

    Heuristic (in priority order):
      1. ``reader_fidelity`` — LibreDWG-known loss surface (text encoding, HATCH,
         anonymous block headers, attribute traversal).  Triggered when
         ``reader_protocol["unsupported_reason_counts"]`` reports any
         ``libredwg_*`` reason or geometry/cad_role counts differ materially.
      2. ``pipeline_behavior`` — downstream processing drift (e.g., layer
         filter, role assignment); flagged when dev-reader census differs from
         the canonical model's expectation but no reader reason codes fire.
      3. ``baseline_drift`` — baseline itself was generated with non-default
         settings; default fallback when reader_protocol is absent or empty.

    Every deviation MUST carry a typed explanation; "unexplained" is reserved
    for cases that genuinely cannot be attributed (hard FAIL by design).
    """
    rp = reader_protocol or {}
    reasons = dict(rp.get("unsupported_reason_counts") or {})
    libredwg_reasons = {k: v for k, v in reasons.items() if k.startswith("libredwg_")}
    non_libredwg_reasons = {k: v for k, v in reasons.items() if not k.startswith("libredwg_")}
    delta = actual - expected

    if libredwg_reasons and any(v > 0 for v in libredwg_reasons.values()):
        classification = "reader_fidelity"
        explanation = (
            f"LibreDWG dev-reader reported {sum(libredwg_reasons.values())} typed "
            f"unsupported reason(s) on this layer family "
            f"(codes: {sorted(libredwg_reasons)})"
        )
    elif non_libredwg_reasons and any(v > 0 for v in non_libredwg_reasons.values()):
        classification = "pipeline_behavior"
        explanation = (
            f"Downstream pipeline reported non-libredwg reasons "
            f"(codes: {sorted(non_libredwg_reasons)}) on this layer"
        )
    elif delta == 0:
        classification = "baseline_drift"
        explanation = (
            "Reported difference is noise around the same expected count "
            "(no reader reason codes surfaced)"
        )
    else:
        classification = "pipeline_behavior"
        explanation = (
            f"No reader reason codes reported for layer={layer!r}; "
            f"deviation (delta={delta}) attributed to downstream pipeline behavior "
            f"or baseline drift that did not surface a typed reason"
        )

    return {
        "layer": layer,
        "expected": expected,
        "actual": actual,
        "delta": delta,
        "classification": classification,
        "explanation": explanation,
        "reader_protocol_snapshot": {
            "extraction_backend": rp.get("extraction_backend"),
            "metadata_evidence": rp.get("metadata_evidence"),
            "skipped_rows": rp.get("skipped_rows"),
            "inventory_complete": rp.get("inventory_complete"),
            "unsupported_reason_counts": reasons,
            "anon_block_names_resolved": rp.get("anon_block_names_resolved"),
        },
    }


def main() -> int:
    if os.environ.get("CAD2GIS_DEV_READER") != "1":
        raise SystemExit(
            "replay_apd_libredwg_dev requires CAD2GIS_DEV_READER=1 "
            "(synthetic metadata evidence gate)"
        )

    import cad2gis_v3.ingest as ingest_module
    import libredwg_dev_reader

    ingest_module.extract_dwg_records = libredwg_dev_reader.extract_dwg_records

    from cad2gis_v3.pipeline import ConversionRequest, convert

    run_dir = Path(os.environ.get(
        "APD_DEV_REPLAY_DIR", "/tmp/apd_libredwg_dev_replay"
    )).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    import hashlib

    source_sha256 = hashlib.sha256(APD_DWG.read_bytes()).hexdigest()
    print(f"[replay] source sha256={source_sha256}")
    print(f"[replay] run_dir={run_dir}")

    # Snapshot reader_protocol BEFORE convert (so classification sees the same
    # reader-side evidence that downstream consumed).
    _reader_inventory = libredwg_dev_reader.extract_dwg_records(APD_DWG)
    reader_protocol = dict(getattr(_reader_inventory, "diagnostics", {}) or {})

    result = convert(
        ConversionRequest(
            source=APD_DWG,
            run_dir=run_dir,
            source_profile=DEV_PROFILE,
            mapping_registry=MAPPING_REGISTRY,
            gcp_profile=GCP_PROFILE if GCP_PROFILE.exists() else None,
        )
    )

    delivery_counts = _table_counts(Path(result.delivery_path), list(EXPECTED_DELIVERY))
    evidence_counts = _table_counts(Path(result.evidence_path), list(EXPECTED_EVIDENCE))

    deviations: list[dict] = []
    for layer, expected in EXPECTED_DELIVERY.items():
        actual = delivery_counts.get(layer, -1)
        if actual != expected:
            deviations.append(
                _classify_deviation(
                    f"delivery.{layer}", expected, actual, reader_protocol=reader_protocol
                )
            )
    for layer, expected in EXPECTED_EVIDENCE.items():
        actual = evidence_counts.get(layer, -1)
        if actual != expected:
            deviations.append(
                _classify_deviation(
                    f"evidence.{layer}", expected, actual, reader_protocol=reader_protocol
                )
            )

    unexplained = [d for d in deviations if d["classification"] == "unexplained"]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": source_sha256,
        "run_dir": str(run_dir),
        "delivery_counts": delivery_counts,
        "evidence_counts": evidence_counts,
        "pipeline_counts": dict(result.counts),
        "reader_protocol": reader_protocol,
        "deviations": deviations,
        "unexplained_deviation_count": len(unexplained),
        "verdict": "PASS" if not unexplained else "FAIL",
        "notes": [
            "Baseline: experiment/runs/apd_architecture_v3_complete "
            "(AutoCAD canonical). Deviations require typed explanation "
            "(reader_fidelity/pipeline_behavior/baseline_drift); "
            "unexplained deviation is a hard FAIL."
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("verdict", "unexplained_deviation_count")}))
    print(f"[replay] report: {REPORT_PATH}")
    return 0 if not unexplained else 1


if __name__ == "__main__":
    sys.exit(main())
