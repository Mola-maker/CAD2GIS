from __future__ import annotations

import json

from cad2gis.cli import main


def test_verify_command_writes_report(tmp_path):
    report = tmp_path / "verify.json"

    rc = main(["verify", "--report", str(report)])

    assert rc == 0
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "verified"


def test_doctor_proposals_writes_offline_prompt_and_schema(tmp_path):
    diagnostics = tmp_path / "diagnostics.json"
    proposals = tmp_path / "doctor_proposals.json"
    prompt = tmp_path / "doctor_prompt.md"
    diagnostics.write_text(json.dumps({"issues": []}), encoding="utf-8")

    rc = main([
        "doctor-proposals",
        str(diagnostics),
        "--out",
        str(proposals),
        "--offline-template",
        str(prompt),
    ])

    assert rc == 0
    assert json.loads(proposals.read_text(encoding="utf-8"))["proposals"] == []
    assert "CAD2GIS Accuracy Doctor" in prompt.read_text(encoding="utf-8")


def test_apply_corrections_persists_corrected_features(tmp_path):
    proposals = tmp_path / "proposals.json"
    ledger = tmp_path / "ledger.jsonl"
    report = tmp_path / "apply_report.json"
    corrected = tmp_path / "corrected_features.json"
    proposals.write_text(json.dumps({"proposals": []}), encoding="utf-8")

    rc = main([
        "apply-corrections",
        "samples/synthetic_comms.dxf",
        str(proposals),
        "--ledger",
        str(ledger),
        "--out-report",
        str(report),
        "--out-features",
        str(corrected),
    ])

    assert rc == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["corrected_features"] == str(corrected)
    assert json.loads(corrected.read_text(encoding="utf-8"))["features"]
