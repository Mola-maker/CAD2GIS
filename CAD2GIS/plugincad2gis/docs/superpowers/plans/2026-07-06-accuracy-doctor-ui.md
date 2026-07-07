# Accuracy Doctor UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Accuracy Doctor correction loop with deterministic diagnostics, correction ledgers, CLI commands, QGIS review UI, and web review dashboard.

**Architecture:** The deterministic Python core owns diagnostics, patch validation, patch application, and verification. QGIS is the authoritative correction console. The web dashboard reads the same artifacts for QA, evidence review, and reporting.

**Tech Stack:** Python dataclasses/JSON, pytest, argparse CLI, PyQGIS widgets, FastAPI, vanilla HTML/CSS/JS, Leaflet.

---

## File Structure

- Create `src/cad2gis/diagnostics.py`: issue dataclasses, JSON serialization, deterministic issue detection.
- Create `src/cad2gis/corrections.py`: patch dataclasses, validation, application, ledger JSONL read/write.
- Create `src/cad2gis/doctor.py`: offline prompt-package generation and strict proposal JSON loader.
- Modify `src/cad2gis/cli.py`: add `diagnose`, `doctor-proposals`, `apply-corrections`, and implemented `verify`.
- Modify `src/cad2gis/pipeline.py`: optionally include diagnostics path in run reports after the core functions exist.
- Modify `qgis_plugin/dockwidget.py`: add Accuracy Doctor tabs and artifact loaders.
- Modify `demo/server/app.py`: expose diagnostics, proposals, corrections, and verification report endpoints.
- Modify `demo/server/live.html`, `demo/live.css`, `demo/live.js`: add browser review dashboard tabs.
- Create `tests/test_diagnostics.py`: deterministic issue detection.
- Create `tests/test_corrections.py`: patch validation/application/ledger.
- Create `tests/test_cli_doctor.py`: CLI artifact commands.

## Task 1: Deterministic Diagnostics

**Files:**
- Create: `src/cad2gis/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write failing diagnostics tests**

```python
from shapely.geometry import LineString, Point

from cad2gis.diagnostics import diagnose_collection
from cad2gis.model import Feature, FeatureCollection, SourceRef


def test_diagnose_unverified_duct_and_dangling_route():
    coll = FeatureCollection(source_file="sample.dxf")
    coll.add(Feature(Point(0, 0), "duct", {"resolved_by": "topology_propagation"},
                     SourceRef(handle="D1", layer="GXYZ", block="gc013b", entity_type="INSERT")))
    coll.add(Feature(LineString([(10, 10), (20, 10)]), "cable", {},
                     SourceRef(handle="C1", layer="COMM", entity_type="LWPOLYLINE")))

    issues = diagnose_collection(coll, per_feature={"by_class": {"duct": {"total": 1, "verified": 0}}},
                                 network={"dangling_ends": 2})

    assert [i.issue_type for i in issues] == ["unverified_duct", "dangling_route"]
    assert issues[0].source_handle == "D1"
    assert issues[1].source_handle == "C1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_diagnostics.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'cad2gis.diagnostics'`.

- [ ] **Step 3: Implement diagnostics**

Implement `Issue`, `issues_to_jsonable`, `issues_from_jsonable`, `write_diagnostics`, `read_diagnostics`, and `diagnose_collection`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_diagnostics.py -q`

Expected: PASS.

## Task 2: Correction Schema, Validation, Application, Ledger

**Files:**
- Create: `src/cad2gis/corrections.py`
- Test: `tests/test_corrections.py`

- [ ] **Step 1: Write failing correction tests**

```python
from shapely.geometry import Point

from cad2gis.corrections import CorrectionPatch, apply_patches, read_ledger, write_ledger_entry
from cad2gis.model import Feature, FeatureCollection, SourceRef


def test_apply_reviewed_label_patch_preserves_provenance(tmp_path):
    coll = FeatureCollection(source_file="sample.dxf")
    coll.add(Feature(Point(1, 2), "__unmapped__", {}, SourceRef(handle="A1", layer="GXYZ")))
    patch = CorrectionPatch(
        patch_id="p1", patch_type="apply_reviewed_label", source_handle="A1",
        after={"feature_class": "duct", "attributes": {"review_status": "accepted"}},
        evidence={"reviewed_label": "duct"}, reason="hand reviewed duct label"
    )

    out, records = apply_patches(coll, [patch])

    assert out.features[0].feature_class == "duct"
    assert out.features[0].source.handle == "A1"
    assert records[0].status == "accepted"


def test_ledger_round_trip(tmp_path):
    path = tmp_path / "ledger.jsonl"
    patch = CorrectionPatch("p1", "reject_feature", "A1", after={"feature_class": "__unmapped__"},
                            evidence={"negative": "paving"}, reason="surface restoration")
    _, records = apply_patches(FeatureCollection(), [patch])
    write_ledger_entry(path, records[0])
    assert read_ledger(path)[0].patch_id == "p1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_corrections.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'cad2gis.corrections'`.

- [ ] **Step 3: Implement corrections**

Implement supported patch types: `apply_reviewed_label`, `reclassify_feature`, `reject_feature`, and `set_attribute`. Unsupported patch types must produce rejected ledger records without mutating features.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python -m pytest tests/test_corrections.py -q`

Expected: PASS.

## Task 3: CLI Artifact Commands

**Files:**
- Modify: `src/cad2gis/cli.py`
- Create: `src/cad2gis/doctor.py`
- Test: `tests/test_cli_doctor.py`

- [ ] **Step 1: Write failing CLI tests**

```python
import json

from cad2gis.cli import main


def test_verify_command_writes_report(tmp_path):
    report = tmp_path / "verify.json"
    rc = main(["verify", "--report", str(report)])
    assert rc == 0
    assert json.loads(report.read_text())["status"] == "verified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_cli_doctor.py -q`

Expected: FAIL because `verify` returns not-yet-implemented exit code 2.

- [ ] **Step 3: Implement CLI commands**

Add:

```bash
cad2gis diagnose input.dxf --report build/diagnostics.json
cad2gis doctor-proposals build/diagnostics.json --out build/doctor_proposals.json --offline-template build/doctor_prompt.md
cad2gis apply-corrections input.dxf build/doctor_proposals.json --ledger build/corrections/DS04.jsonl --out-report build/apply_report.json
cad2gis verify --report build/verification_after_corrections.json
```

- [ ] **Step 4: Run CLI tests**

Run: `PYTHONPATH=src python -m pytest tests/test_cli_doctor.py -q`

Expected: PASS.

## Task 4: Web Review Dashboard

**Files:**
- Modify: `demo/server/app.py`
- Modify: `demo/server/live.html`
- Modify: `demo/live.css`
- Modify: `demo/live.js`

- [ ] **Step 1: Add artifact API behavior tests if a server test harness exists**

If no server tests exist, keep the change minimal and verify with `python -m py_compile demo/server/app.py`.

- [ ] **Step 2: Add read-only endpoints**

Add `/api/diagnostics`, `/api/proposals`, `/api/corrections`, and `/api/verification`.

- [ ] **Step 3: Add dashboard tabs**

Add Overview, Map, Issues, Evidence, Corrections, and Report tabs. The browser must not mutate the authoritative ledger.

- [ ] **Step 4: Verify syntax**

Run: `python -m py_compile demo/server/app.py`

Expected: PASS.

## Task 5: QGIS Accuracy Doctor Dock

**Files:**
- Modify: `qgis_plugin/dockwidget.py`

- [ ] **Step 1: Add dock tabs**

Add Convert, Issues, Evidence, Corrections, and Score tabs using QGIS-native Qt widgets.

- [ ] **Step 2: Add artifact loaders**

Load diagnostics, proposals, ledgers, and verification reports from `build/`.

- [ ] **Step 3: Add bounded correction actions**

Add Accept, Reject, Needs Review, and Zoom to Evidence buttons as UI actions. Before the correction engine is fully wired into QGIS, these actions must update the dock state only and never mutate map layers directly.

- [ ] **Step 4: Verify syntax**

Run: `python -m py_compile qgis_plugin/dockwidget.py`

Expected: PASS.

## Task 6: Final Verification

**Files:**
- Modify docs only if command names or artifact names differ from the spec.

- [ ] **Step 1: Run focused tests**

Run: `PYTHONPATH=src python -m pytest tests/test_diagnostics.py tests/test_corrections.py tests/test_cli_doctor.py -q`

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run: `PYTHONPATH=src python -m pytest tests/ -q`

Expected: PASS or existing environment-only skips.

- [ ] **Step 3: Run syntax checks**

Run: `python -m py_compile demo/server/app.py qgis_plugin/dockwidget.py`

Expected: PASS.
